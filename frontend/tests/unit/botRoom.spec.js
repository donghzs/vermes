import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

// 隔离网络依赖：store 仅在 actions 内用 api，getters / 流式 action 均为纯函数，
// mock 后端服务避免 import 期副作用与任何真实请求。
vi.mock('@/services/api', () => ({
  default: {
    listBotRooms: vi.fn(),
    createBotRoom: vi.fn(),
    updateBotRoom: vi.fn(),
    deleteBotRoom: vi.fn(),
    addBotRoomMember: vi.fn(),
    removeBotRoomMember: vi.fn(),
    listBotRoomMembers: vi.fn(),
    getBotRoomTimeline: vi.fn(),
    sendBotRoomMessage: vi.fn(),
  },
}))

import { useBotRoomStore } from '@/stores/botRoom'

// 后端 ROOM_MENTION_RE（botmode/core.py，对齐 ① A2A _HANDLE_RE）遇空白截断，
// 故含空格 name 退化为 ref_id 作为 insert。
function refIdHash(ref) {
  let h = 0
  for (const ch of String(ref || '')) h = (h * 31 + ch.charCodeAt(0)) % 360
  return h
}

describe('useBotRoomStore · currentRoom getter', () => {
  let store
  beforeEach(() => {
    setActivePinia(createPinia())
    store = useBotRoomStore()
  })

  it('returns the room matching currentRoomId', () => {
    store.rooms = [{ id: 'r1', title: 'A' }, { id: 'r2', title: 'B' }]
    store.currentRoomId = 'r2'
    expect(store.currentRoom).toEqual({ id: 'r2', title: 'B' })
  })

  it('returns null when currentRoomId unset', () => {
    store.currentRoomId = null
    expect(store.currentRoom).toBeNull()
  })
})

describe('useBotRoomStore · mentionCandidates getter', () => {
  let store
  beforeEach(() => {
    setActivePinia(createPinia())
    store = useBotRoomStore()
  })

  const member = (over) => ({
    ref_id: 'a1',
    name: '研究员',
    member_type: 'agent',
    hue: 210,
    ...over,
  })

  it('includes agent + secretary, excludes human', () => {
    store.members = [
      member({ ref_id: 'a1', member_type: 'agent' }),
      member({ ref_id: 'sec', member_type: 'secretary', name: '秘书' }),
      member({ ref_id: 'h1', member_type: 'human', name: '小明' }),
    ]
    expect(store.mentionCandidates.map((c) => c.ref_id)).toEqual(['a1', 'sec'])
  })

  // 钉死 Sprint A ②：秘书必须进 @ 补全候选（与 chat.py 群含 secretary 即触发闭环）
  it('secretary is a valid @ mention candidate (Sprint A ② regression)', () => {
    store.members = [member({ ref_id: 'sec', member_type: 'secretary', name: '秘书' })]
    expect(store.mentionCandidates).toHaveLength(1)
    expect(store.mentionCandidates[0].member_type).toBe('secretary')
    expect(store.mentionCandidates[0].name).toBe('秘书')
  })

  it('prefers role_name for display name and insert', () => {
    store.members = [member({ ref_id: 'a1', name: '研究员', role_name: '产品经理' })]
    const c = store.mentionCandidates[0]
    expect(c.name).toBe('产品经理')
    expect(c.insert).toBe('产品经理')
  })

  it('degrades insert to ref_id when name contains whitespace', () => {
    store.members = [member({ ref_id: 'a1', name: 'Zhang San' })]
    const c = store.mentionCandidates[0]
    expect(c.name).toBe('Zhang San')
    expect(c.insert).toBe('a1')
  })

  it('uses profile hue when > 0, else hashes ref_id deterministically', () => {
    store.members = [
      member({ ref_id: 'a1', hue: 210 }),
      member({ ref_id: 'a2', hue: 0 }),
    ]
    const cands = store.mentionCandidates
    expect(cands[0].hue).toBe(210)
    expect(cands[1].hue).toBe(refIdHash('a2'))
  })

  it('initial is first char of display name', () => {
    store.members = [member({ ref_id: 'a1', name: '研究员' })]
    expect(store.mentionCandidates[0].initial).toBe('研')
  })
})

describe('useBotRoomStore · memberByRef getter', () => {
  let store
  beforeEach(() => {
    setActivePinia(createPinia())
    store = useBotRoomStore()
  })

  it('is keyed by ref_id with visual fields (role_name preferred, consistent with mentionCandidates)', () => {
    store.members = [
      { ref_id: 'a1', name: '研究员', member_type: 'agent', hue: 210, role_name: '产品经理' },
    ]
    expect(store.memberByRef.a1).toMatchObject({
      name: '产品经理', // 岗位名优先（与 mentionCandidates 一致）
      hue: 210,
      member_type: 'agent',
      initial: '产',
      role_name: '产品经理',
    })
  })
})

describe('useBotRoomStore · onRoomUpdate streaming lifecycle', () => {
  let store
  beforeEach(() => {
    setActivePinia(createPinia())
    store = useBotRoomStore()
    store.currentRoomId = 'r1'
  })

  const delta = (phase, extra = {}) => ({
    type: 'room_update',
    topic: 'room:r1',
    event: 'room_message_delta',
    message: { agent_id: 'a1', phase, ...extra },
  })

  it('start initializes a streaming bubble', async () => {
    await store.onRoomUpdate(delta('start'))
    expect(store.streaming.a1).toEqual({ text: '', active: true })
  })

  it('delta accumulates text across chunks', async () => {
    await store.onRoomUpdate(delta('start'))
    await store.onRoomUpdate(delta('delta', { delta: '你好' }))
    await store.onRoomUpdate(delta('delta', { delta: '世界' }))
    expect(store.streaming.a1.text).toBe('你好世界')
    expect(store.streaming.a1.active).toBe(true)
  })

  it('end removes the streaming bubble (prevents dangling "生成中")', async () => {
    await store.onRoomUpdate(delta('start'))
    await store.onRoomUpdate(delta('end'))
    expect(store.streaming.a1).toBeUndefined()
  })

  it('ignores delta events for non-current room', async () => {
    await store.onRoomUpdate({
      type: 'room_update',
      topic: 'room:r2',
      event: 'room_message_delta',
      message: { agent_id: 'a1', phase: 'start' },
    })
    expect(store.streaming.a1).toBeUndefined()
  })
})
