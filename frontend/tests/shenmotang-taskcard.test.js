/**
 * T4 任务卡：@派活 → kanban 任务 + 消息携带 task_id + 行内卡/进度。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import {
  parseDispatch,
  parseTaskIds,
  formatTaskMark,
  prepareDispatchSend,
  taskStatusMeta,
  resolveAssignee,
  TASK_STATUS_META,
} from '../src/utils/taskDispatch'
import ShenmotangTaskCard from '../src/components/ShenmotangTaskCard.vue'

describe('T4 消息↔任务绑定', () => {
  it('parseDispatch 识别派活并抽标题', () => {
    expect(parseDispatch('你好')).toBeNull()
    const d = parseDispatch('@Codex 派活 修 CI 超时')
    expect(d).toBeTruthy()
    expect(d.title).toBe('修 CI 超时')
    const d2 = parseDispatch('派活')
    expect(d2.title).toBeTruthy()
  })

  it('消息携带 task_id 可解析 + 标记格式', () => {
    const mark = formatTaskMark(42)
    expect(mark).toBe('【任务 #42】')
    expect(parseTaskIds(`干得不错\n${mark}`)).toEqual(['42'])
    expect(parseTaskIds('没有任务')).toEqual([])
    // 不把普通 # 当成任务号时至少能抓到【任务 #n】
    expect(parseTaskIds('【任务 #7】和【任务 #8】')).toEqual(['7', '8'])
  })

  it('真枚举状态标签（无 dispatched/planning）', () => {
    expect(taskStatusMeta('running').label).toBe('执行中')
    expect(taskStatusMeta('done').label).toBe('完成')
    expect(taskStatusMeta('triage').label).toBe('分诊')
    expect(taskStatusMeta('archived').label).toBe('归档')
    // 覆盖 9 态
    for (const s of ['triage', 'todo', 'scheduled', 'ready', 'running', 'blocked', 'review', 'done', 'archived']) {
      expect(taskStatusMeta(s).label).toBeTruthy()
    }
    expect(Object.keys(TASK_STATUS_META).length).toBe(9)
    expect(TASK_STATUS_META.dispatched).toBeUndefined()
    expect(TASK_STATUS_META.planning).toBeUndefined()
  })

  it('resolveAssignee：@名优先，缺省 fallback（1:1 用 contact.id，不靠成员列表兜底）', () => {
    const contacts = [{ id: 'p1', name: 'Codex' }, { id: 'p2', name: 'Kimi' }]
    expect(resolveAssignee(contacts, 'Codex', 'p2')).toBe('p1')
    expect(resolveAssignee(contacts, '@Kimi', '')).toBe('p2')
    expect(resolveAssignee(contacts, '', 'p1')).toBe('p1')
    expect(resolveAssignee(contacts, '不存在', 'p1')).toBe('p1')
  })

  it('prepareDispatchSend：派活建任务并附标记；非派活原样', async () => {
    const calls = []
    globalThis.fetch = vi.fn(async (url, opts) => {
      calls.push({ url: String(url), body: opts && opts.body ? JSON.parse(opts.body) : {} })
      return {
        ok: true,
        json: async () => ({ task: { id: 99, title: '修 CI', status: 'ready', assignee: 'p1' } }),
      }
    })
    const hit = await prepareDispatchSend('派活 修 CI', {
      contacts: [{ id: 'p1', name: 'Codex' }],
      assigneeId: 'p1',
    })
    expect(hit.taskId).toBe(99)
    expect(hit.text).toContain('【任务 #99】')
    expect(calls[0].url).toContain('/api/plugins/kanban/tasks')
    expect(calls[0].body.assignee).toBe('p1')

    const miss = await prepareDispatchSend('普通聊天', { assigneeId: 'p1' })
    expect(miss.task).toBeNull()
    expect(miss.text).toBe('普通聊天')
  })
})

describe('T4 行内任务卡', () => {
  beforeEach(() => {
    globalThis.fetch = vi.fn(async (url) => {
      const u = String(url)
      if (u.includes('/tasks/')) {
        return {
          ok: true,
          json: async () => ({
            task: {
              id: 99,
              title: '修 CI 超时',
              status: 'running',
              assignee: 'Codex',
            },
            events: [
              { id: 1, kind: 'created', payload: '{"note":"派活"}', created_at: '2026-09-27T10:00:00Z' },
              { id: 2, kind: 'status', payload: '{"status":"running"}', created_at: '2026-09-27T10:01:00Z' },
            ],
          }),
        }
      }
      return { ok: true, json: async () => ({}) }
    })
  })

  it('渲染标题/真枚举状态/assignee，展开看进度事件', async () => {
    const wrapper = mount(ShenmotangTaskCard, {
      props: { taskId: 99 },
    })
    await new Promise(r => setTimeout(r, 30))
    const text = wrapper.text()
    expect(text).toContain('任务卡 · 修 CI 超时')
    expect(text).toContain('执行中')
    expect(text).toContain('Codex')
    expect(text).toContain('#99')
    // 未展开时没有事件行
    expect(text).not.toContain('created')
    const btn = wrapper.find('button')
    await btn.trigger('click')
    await new Promise(r => setTimeout(r, 10))
    expect(wrapper.text()).toContain('created')
    expect(wrapper.text()).toContain('status')
  })

  it('ShenmotangPeerDm 接线派活与任务卡（源码断言）', async () => {
    const fs = await import('node:fs')
    const path = await import('node:path')
    const src = fs.readFileSync(path.resolve('src/components/ShenmotangPeerDm.vue'), 'utf8')
    expect(src).toContain('ShenmotangTaskCard')
    expect(src).toContain('prepareDispatchSend')
    expect(src).toContain('parseTaskIds')
  })
})
