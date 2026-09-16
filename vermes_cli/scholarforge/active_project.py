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

from contextvars import ContextVar
from typing import Optional

from vermes_cli.scholarforge.database import list_projects, create_project

# ── 兜底项目「显形」机制（2026-09-16）─────────────────────────────────
# A1 的兜底设计（下面 _ensure_default_project）解决了「静默丢内容」，但留下另一半问题：
# **它替用户做了选择，却不告诉用户**。用户有多个论文项目、忘了选，工具就默默写进
# 「最近更新的那个」，输出里只出现一个 `#52` —— 用户未必把它跟「写错项目」联系起来。
#
# 这里不改 A1 的取舍（仍然保证有落库目标），只补上「显形」：
#   · 真的走第三级兜底时记一笔（ContextVar → asyncio 并发安全，与 _LLM_USAGE_ACC 同机制）；
#   · 由 `_with_usage` 这个统一包装器读出并追加到工具结果末尾（23 个调用点零改动，
#     而且**不可能有人忘记加** —— 忘记加提示的可能是被包装器兜掉的）。
# 自我限流：兜底会把该项目设为激活，故下一次调用走「激活」分支 → 提示自然只出现一次。
_project_fallback: ContextVar[Optional[tuple]] = ContextVar("sf_project_fallback", default=None)


def _note_fallback(pid: int, title: str = "") -> None:
    """记录一次「兜底选了项目」。"""
    try:
        _project_fallback.set((int(pid), title or ""))
    except Exception:
        pass


def clear_project_fallback():
    """工具调用开始时清空记录，返回 token 供 finally 恢复（嵌套安全）。"""
    return _project_fallback.set(None)


def restore_project_fallback(token) -> None:
    try:
        _project_fallback.reset(token)
    except Exception:
        pass


def take_project_fallback_note() -> str:
    """取出并清空本次调用的兜底记录；没发生兜底则返回空串。"""
    info = _project_fallback.get()
    _project_fallback.set(None)
    if not info:
        return ""
    pid, title = info
    label = f"#{pid}" + (f"「{title}」" if title else "")
    return (
        f"\n\n---\n\n"
        f"⚠️ **你这次没有指定论文项目**，为避免内容丢失，已自动落到项目 {label}。\n"
        f"- 这**不一定**是你要的项目 —— 系统是按「最近更新」挑的，不是你选的。\n"
        f"- 若不是，请先在论文页选中目标项目（或调用 `scholarforge_set_active_project`）后重跑。\n"
        f"- 已把「当前项目」记住为 {label}，**后续调用会继续写到这里**（本条提示不会重复出现）。\n"
    )

# 无激活项目时的兜底默认项目名（保证写回类工具永远有落库目标）
_DEFAULT_PROJECT_TITLE = "Vermes 默认项目"

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


def _ensure_default_project() -> int:
    """无激活项目时的兜底：选最近项目或自动建「默认项目」，保证写回永远落库。

    返回兜底项目 id；DB 异常时返回 0（由调用方降级）。
    """
    try:
        projects = list_projects()
    except Exception:
        return 0
    title = ""
    if projects:
        # list_projects 按 updated_at DESC，取最近一个作为默认落库目标
        pid = int(projects[0]["id"])
        title = str(projects[0].get("title") or "")
    else:
        try:
            new = create_project(_DEFAULT_PROJECT_TITLE)
            pid = int(new["id"])
            title = _DEFAULT_PROJECT_TITLE
        except Exception:
            return 0
    set_active_project(pid)
    # 记一笔「这是我替你挑的」，由 _with_usage 追加到工具结果里显形。
    _note_fallback(pid, title)
    return pid


def resolve_project_id(args: dict, session_id: Optional[str] = None) -> int:
    """解析写回类工具所需的 project_id。

    优先级：显式 args.project_id（>0）> 激活项目（全局/会话）> 兜底默认项目。
    A1：末尾兜底自动选/建默认项目，写回类工具不再因「双无」而静默丢内容。
    """
    raw = (args or {}).get("project_id", 0)
    try:
        pid = int(raw) if raw else 0
    except (TypeError, ValueError):
        pid = 0
    if pid > 0:
        return pid
    active = get_active_project(session_id)
    if active > 0:
        return active
    return _ensure_default_project()
