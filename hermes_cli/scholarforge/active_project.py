"""ScholarForge 激活项目（当前论文项目）存储与解析。

写回类工具需要明确的 project_id，但 agent 对话路径往往不传 project_id，
导致此前「写回成功却零落库」的幻影进度 bug。引入「激活项目」概念：

- 前端面板激活/切换项目时 POST /api/scholar/active-project 种入（进程全局）；
- agent 也可显式调用 scholarforge_set_active_project 工具切换；
- 写回类工具解析 project_id = 显式 args.project_id（优先）或 激活项目；
  二者皆无则视为缺失，由调用方决定报错还是降级。

桌面端单用户单进程，激活项目以进程全局存储即可；会话级字典预留扩展。
"""
from __future__ import annotations

from typing import Optional

# 进程全局：当前激活的论文项目 id（由前端激活或工具设置）
_active_pid: int = 0

# 预留：按会话隔离（桌面单用户暂未启用）
_active_by_session: dict[str, int] = {}

# 缺失 project_id 时的统一报错（以 ❌ 开头，工具埋点会自动记 ok=0）
PROJECT_ID_MISSING_MSG = (
    "❌ 无法确定 project_id：写回操作必须关联一个论文项目。请二选一：\n"
    "1) 调用 scholarforge_set_active_project 选择当前项目（如 project_id=52）；\n"
    "2) 在调用时显式传入 project_id 参数。"
)


def set_active_project(project_id: int, session_id: Optional[str] = None) -> int:
    """设置激活项目。返回生效的 project_id（非法值清零）。"""
    global _active_pid
    try:
        pid = int(project_id) if project_id else 0
    except (TypeError, ValueError):
        pid = 0
    _active_pid = pid
    if session_id:
        _active_by_session[session_id] = pid
    return pid


def get_active_project(session_id: Optional[str] = None) -> int:
    """返回当前激活项目 id：会话级优先，否则全局；无则 0。"""
    if session_id:
        return _active_by_session.get(session_id, _active_pid)
    return _active_pid


def resolve_project_id(args: dict, session_id: Optional[str] = None) -> int:
    """解析写回类工具所需的 project_id。

    优先级：显式 args.project_id（>0）> 激活项目（全局/会话）> 0（缺失）。
    """
    raw = (args or {}).get("project_id", 0)
    try:
        pid = int(raw) if raw else 0
    except (TypeError, ValueError):
        pid = 0
    if pid > 0:
        return pid
    return get_active_project(session_id)
