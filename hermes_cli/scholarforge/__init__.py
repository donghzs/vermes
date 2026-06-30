"""ScholarForge — 论文写作 Agent 模块
完全独立于 Vermes 核心，通过 Blueprint 注册。

Phase 3 (2026-06-29):  +3 Agent Tools (search/write/review) 供 Vermes Agent 在对话中直接使用。
"""

# 导入 tools 模块，触发全局 registry 注册（3 个 Agent 工具）
from hermes_cli.scholarforge import tools  # noqa: F401 — side-effect: registers scholarforge_search/write/review
