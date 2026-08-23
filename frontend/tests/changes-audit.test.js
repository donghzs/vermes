import { describe, it, expect, beforeEach, vi } from 'vitest'

/**
 * Changes 审计功能测试
 * 测试 addChange / clearChanges 逻辑（通过 window.__vermesChanges 接口）
 */

// 模拟 Vue ref + __vermesChanges 接口
import { ref } from 'vue'

let changes
let activeChangeId

function setupChanges() {
  changes = ref([])
  activeChangeId = ref(null)

  function addChange(change) {
    const id = `change-${Date.now()}-${changes.value.length}`
    const entry = {
      id,
      path: change.path || '',
      action: change.action || 'write',
      diff: change.diff || '',
      timestamp: change.timestamp || Date.now(),
    }
    changes.value.push(entry)
    if (changes.value.length === 1) {
      activeChangeId.value = id
    }
    return id
  }

  function clearChanges() {
    changes.value = []
    activeChangeId.value = null
  }

  return { changes, activeChangeId, addChange, clearChanges }
}

describe('Changes 审计功能', () => {
  beforeEach(() => {
    const ctx = setupChanges()
    global.__vermesChanges = ctx
  })

  it('addChange 添加变更到列表', () => {
    const { addChange, changes } = global.__vermesChanges
    addChange({ path: 'src/main.js', action: 'write', diff: '+new code' })
    expect(changes.value.length).toBe(1)
    expect(changes.value[0].path).toBe('src/main.js')
    expect(changes.value[0].action).toBe('write')
  })

  it('首个变更自动激活', () => {
    const { addChange, activeChangeId, changes } = global.__vermesChanges
    addChange({ path: 'a.py', action: 'patch', diff: '-old\n+new' })
    expect(activeChangeId.value).not.toBeNull()
    expect(activeChangeId.value).toBe(changes.value[0].id)
  })

  it('多个变更按顺序追加', () => {
    const { addChange, changes } = global.__vermesChanges
    addChange({ path: 'a.py', action: 'write' })
    addChange({ path: 'b.py', action: 'patch' })
    addChange({ path: 'c.py', action: 'write' })
    expect(changes.value.length).toBe(3)
    expect(changes.value[0].path).toBe('a.py')
    expect(changes.value[2].path).toBe('c.py')
  })

  it('clearChanges 清空列表和激活状态', () => {
    const { addChange, clearChanges, changes, activeChangeId } = global.__vermesChanges
    addChange({ path: 'x.js', action: 'write' })
    expect(changes.value.length).toBe(1)
    clearChanges()
    expect(changes.value.length).toBe(0)
    expect(activeChangeId.value).toBeNull()
  })

  it('action 标签：write → 新建/覆盖', () => {
    const { addChange, changes } = global.__vermesChanges
    addChange({ path: 'new.txt', action: 'write' })
    expect(changes.value[0].action).toBe('write')
  })

  it('action 标签：patch → 修改', () => {
    const { addChange, changes } = global.__vermesChanges
    addChange({ path: 'exist.py', action: 'patch' })
    expect(changes.value[0].action).toBe('patch')
  })

  it('diff 内容正确存储', () => {
    const { addChange, changes } = global.__vermesChanges
    const diff = '+line1\n-line2\ncontext'
    addChange({ path: 'test.py', action: 'patch', diff })
    expect(changes.value[0].diff).toBe(diff)
  })

  it('变更有唯一 id', () => {
    const { addChange, changes } = global.__vermesChanges
    addChange({ path: 'a.py', action: 'write' })
    addChange({ path: 'b.py', action: 'write' })
    expect(changes.value[0].id).not.toBe(changes.value[1].id)
  })
})
