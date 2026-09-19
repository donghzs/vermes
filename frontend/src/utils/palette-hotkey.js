/** ⑤ C5 quick-entry：⌘K 热键判定与绑定（可测纯函数）。 */
export function isPaletteToggleEvent(e) {
  return !!(e && (e.metaKey || e.ctrlKey) && e.key === 'k')
}

export function attachPaletteHotkey(target, toggle) {
  if (!target || typeof target.addEventListener !== 'function') return () => {}
  const onKeydown = (e) => {
    if (!isPaletteToggleEvent(e)) return
    e.preventDefault()
    toggle()
  }
  target.addEventListener('keydown', onKeydown)
  return () => target.removeEventListener('keydown', onKeydown)
}
