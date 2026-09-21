/** ⑤ C4/C5：⌘K 命令面清单（页面 + 动作），供 CommandPalette 与测试共用。 */

export function buildPalettePageCommands(router) {
  const go = (path) => () => router.push(path)
  return [
    { key: 'page:chat', icon: '💬', label: '对话', hint: '回到聊天', kind: '页面', action: go('/') },
    { key: 'page:shenmotang', icon: '⛩️', label: '神魔堂', hint: '多 Agent 群聊 + 请神登堂', kind: '页面', action: go('/shenmotang') },
    // MCP 指挥中心已并入 Agent 管理
    { key: 'page:agents', icon: '🤖', label: 'Agent 管理', hint: '自造神/封神榜/已装技能/工具/MCP/记忆/知识库', kind: '页面', action: go('/agents') },
    { key: 'page:settings-mcp', icon: '🔌', label: '设置 · MCP', hint: '设置页 MCP 标签', kind: '页面', action: go('/settings') },
    { key: 'page:studio', icon: '🎨', label: '创作工作室', kind: '页面', action: go('/studio') },
    { key: 'page:scholarforge', icon: '📝', label: '论文写作', kind: '页面', action: go('/scholarforge') },
    { key: 'page:3d', icon: '🏭', label: '3D 建模', kind: '页面', action: go('/3d-studio') },
    { key: 'page:workflows', icon: '🔀', label: '工作流编排', kind: '页面', action: go('/workflows') },
    { key: 'page:bricks', icon: '🧱', label: '积木市场', kind: '页面', action: go('/bricks') },
    { key: 'page:growth', icon: '🌱', label: '成长', kind: '页面', action: go('/growth') },
    { key: 'page:benchmark', icon: '📊', label: 'Benchmark 大盘', kind: '页面', action: go('/benchmark') },
    { key: 'page:settings', icon: '⚙️', label: '设置', kind: '页面', action: go('/settings') },
  ]
}

export function buildPaletteActionCommands(router, chat) {
  return [
    { key: 'act:new-chat', icon: '💬', label: '新建对话', kind: '动作', action: () => chat.createSession('新会话') },
    { key: 'act:toggle-theme', icon: '🌙', label: '切换深色/浅色主题', kind: '动作', action: () => chat.toggleTheme() },
    { key: 'act:agents', icon: '🤖', label: '打开 Agent 管理', hint: '已装技能/工具/MCP/记忆/知识库', kind: '动作', action: () => router.push('/agents') },
  ]
}

export function filterPaletteCommands(all, query, limit = 20) {
  const q = (query || '').trim().toLowerCase()
  if (!q) return all.slice(0, Math.min(12, limit))
  return all.filter(c =>
    c.label.toLowerCase().includes(q) ||
    (c.hint && c.hint.toLowerCase().includes(q)) ||
    (c.kind && c.kind.toLowerCase().includes(q))
  ).slice(0, limit)
}
