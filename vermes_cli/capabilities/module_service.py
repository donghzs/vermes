"""P3-2 顶层能力感知 invoke（L2 之上的能力分发层）。

``invoke(cap, payload, session_id)`` 是统一能力调用入口：
  · 解析 cap → 工具名（复用 L2a 路由 ``route_toolset`` + ``select_tool``，不重建路由）
  · ``model_capable`` 单 if：当前模型 provider 的 capability tag 是否满足 cap 维度
  · ``tier`` 单维决策：local 直调 ``tools.registry.dispatch``；remote 降级 seam
  · 执行：复用 ``tools.registry.dispatch``（单一真相源，含信任闸门 / 未知工具提示）

设计纪律：不重新实现路由，不新建机制，纯组合既有底座。
"""
from __future__ import annotations

import json
import queue
from typing import Any, Dict, List, Optional

from tools.registry import registry as tool_registry
from vermes_cli.adapters import discovery
from vermes_cli.adapters.discovery_registry import CAPABILITY_REGISTRY
from vermes_cli.capabilities import manifest as cap_manifest
from vermes_cli import runtime_provider


# --- cap → 必需 capability 维度 ---
# P3-4 D7 起优先读 domains/*.yaml（见 vermes_cli/capabilities/domains.py），
# 实现「加行业不改码」；yaml 缺失时回退本硬编码兜底（fail-open）。
# 键 = 工具名（cap 标识）；值 = 该 cap 要求当前模型具备的 capability tag 集合。
_CAP_REQUIRED_DIMS_FALLBACK: Dict[str, set] = {
    "cadir_build": {"tools"},
    "cadir_compile": {"tools"},
    "cadir_verify_step": {"tools"},
    "cadir_verify_stl": {"tools"},
}


def _required_dims(tool_name: str) -> set:
    """返回某 cap（工具名）所需的模型能力维度集合（优先 yaml，回退硬编码）。"""
    from vermes_cli.capabilities.domains import load_domain_cap_dims

    dims = load_domain_cap_dims().get(tool_name)
    if dims:
        return set(dims)
    return _CAP_REQUIRED_DIMS_FALLBACK.get(tool_name, set())


def _resolve_tool(cap: str) -> Optional[str]:
    """cap → 工具名（复用 route_toolset + select_tool，按 score 遍历候选 toolset）。"""
    refs = discovery.route_toolset(cap)
    if not refs:
        return None
    for ref in refs:
        idx = CAPABILITY_REGISTRY.get(ref.toolset)
        if idx is None or not idx.tools:
            continue
        choice = discovery.select_tool(idx.tools, cap)
        if choice.decision == "allow_tool" and choice.tool is not None:
            return choice.tool.name
    return None


def model_capable(tool_name: str, provider: Optional[str] = None) -> Dict[str, Any]:
    """单 if 校验：当前模型 provider 是否满足 cap 维度要求。

    返回 ``{ok, required, missing, provider, note?}``。
    fail-open：provider 未知（不在能力索引 / ``"auto"``）时 ``ok=True``，无法判定则不拦截。
    """
    required = sorted(_required_dims(tool_name))
    if not required:
        return {"ok": True, "required": [], "missing": [], "provider": provider or ""}
    pid = (provider or runtime_provider.resolve_requested_provider() or "").strip().lower()
    idx = cap_manifest.build_provider_capability_index()
    if pid in ("", "auto") or pid not in idx:
        return {
            "ok": True,
            "required": required,
            "missing": [],
            "provider": pid,
            "note": "unknown_provider_fail_open",
        }
    caps = set(idx.get(pid, []))
    missing = [c for c in required if c not in caps]
    return {"ok": not missing, "required": required, "missing": missing, "provider": pid}


def get_capable(cap: str, provider: Optional[str] = None) -> Dict[str, Any]:
    """查询当前模型是否满足某 cap 的维度要求（供前端灰显，P3-3）。

    复用 ``model_capable``；先 cap → 工具名解析（灰显按 cap 维度判定）。
    返回 ``{cap, ok, satisfied, missing_dims, required_dims, provider, reason}``。
    fail-open：cap 无匹配工具（brick 未装）时 ``satisfied=True``（无法判定则不灰显）。
    """
    tool_name = _resolve_tool(cap)
    if not tool_name:
        return {
            "cap": cap,
            "ok": True,
            "satisfied": True,
            "missing_dims": [],
            "required_dims": [],
            "provider": provider or "",
            "reason": "no_tool_for_cap_fail_open",
        }
    chk = model_capable(tool_name, provider)
    return {
        "cap": cap,
        "ok": chk["ok"],
        "satisfied": chk["ok"],
        "missing_dims": chk["missing"],
        "required_dims": chk["required"],
        "provider": chk["provider"],
        "reason": chk.get("note") or ("ok" if chk["ok"] else "missing_dims"),
    }


def invoke(
    cap: str,
    payload: Optional[Dict[str, Any]] = None,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """统一能力调用入口。

    ``payload``: ``{"args": {...}, "tier": "local"|"remote", "provider": <可选>}``
    返回结构化 dict（便于前端 / 测试消费），不抛出。
    """
    payload = payload or {}
    args = payload.get("args") or {}
    tier = payload.get("tier", "local")

    tool_name = _resolve_tool(cap)
    if not tool_name:
        return {
            "error": "no_tool_for_cap",
            "cap": cap,
            "hint": "无匹配工具，可能缺少对应可插拔模块（brick 未安装 / 未注册）",
        }

    # model_capable 单 if（D5）：不满足则仅提示、不执行
    cap_check = model_capable(tool_name, payload.get("provider"))
    if not cap_check["ok"]:
        return {
            "capability_check": "not_satisfied",
            "cap": cap,
            "tool": tool_name,
            "required": cap_check["required"],
            "missing": cap_check["missing"],
            "provider": cap_check["provider"],
        }

    # tier 单维决策（D4）：remote 降级 seam，当前无远端服务，仅留结构
    if tier == "remote":
        return {
            "tier": "remote",
            "degraded": True,
            "cap": cap,
            "tool": tool_name,
            "note": "remote backend unavailable; local-light degrade seam (no execution)",
        }

    # local：复用 registry.dispatch 单一真相源（含信任闸门 / 未知工具提示）
    result_json = tool_registry.dispatch(tool_name, args, session_id=session_id)
    try:
        result = json.loads(result_json) if isinstance(result_json, str) else result_json
    except Exception:
        result = {"raw": result_json}
    return {"cap": cap, "tool": tool_name, "result": result}


# --- vermes-model-change 广播（D5 通知前端；P3-3 消费）---
# 轻量 in-process pub-sub：前端 SSE 订阅，broadcast_model_change 推送事件。
_model_change_subscribers: "set[queue.Queue]" = set()


def subscribe_model_change() -> "queue.Queue":
    """前端 SSE 订阅入口：返回队列，broadcast_model_change 向其推送事件。"""
    q: "queue.Queue" = queue.Queue()
    _model_change_subscribers.add(q)
    return q


def unsubscribe_model_change(q: "queue.Queue") -> None:
    """前端断开 SSE 时移除订阅。"""
    _model_change_subscribers.discard(q)


def broadcast_model_change(model: str, provider: Optional[str] = None) -> None:
    """推送模型切换事件（vermes-model-change）。"""
    event = {"event": "vermes-model-change", "model": model, "provider": provider}
    for q in list(_model_change_subscribers):
        try:
            q.put(event)
        except Exception:
            _model_change_subscribers.discard(q)
