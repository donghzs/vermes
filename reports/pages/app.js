/* Vermes × Hermes 血缘调研页 — 轻交互 */
(function () {
  // 表格行 hover 高亮（无需框架）
  document.querySelectorAll('tbody tr').forEach(function (row) {
    row.addEventListener('mouseenter', function () {
      row.style.background = '#faf9f7';
    });
    row.addEventListener('mouseleave', function () {
      row.style.background = '';
    });
  });

  // 键盘：按 g 顶部，按 ? 打印帮助（无控制台噪音）
  document.addEventListener('keydown', function (e) {
    if (e.key === 'g' && !e.metaKey && !e.ctrlKey && !e.altKey) {
      var tag = (e.target && e.target.tagName) || '';
      if (tag === 'INPUT' || tag === 'TEXTAREA') return;
      window.scrollTo({ top: 0, behavior: 'smooth' });
    }
  });
})();
