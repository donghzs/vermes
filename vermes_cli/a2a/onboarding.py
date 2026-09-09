"""傻瓜式外部 agent 接入服务层（Sprint A · 秘书代劳接入）。

把 ``register-profile`` / ``local-connect`` 两个 HTTP handler 里重复的
「探测 + 登堂」编排逻辑抽成单一服务函数 :func:`onboard_agent`，供：

  1. 秘书工具 ``tools/orchestrate_tool.py`` 的 ``action=onboard`` 调用（Sprint B）
  2. 新 HTTP 端点 ``/api/agents/onboard`` 调用（Sprint D）

旧端点 ``register-profile`` / ``local-connect`` **保留不动**，后续择机收敛。

决策树（探测顺序，命中即走）：
  1. recipe 名精确匹配（用户说官方 recipe 名，如 ``codex-acp``）
  2. 本机扫描 name/id 模糊匹配（用户说 agent 通用名，如 ``Codex``）
     2a. fingerprint 反向命中 recipe → ACP 登堂
     2b. 无 recipe 但 ``kind==cli`` 且 bin 名在白名单 → CLI 直连
     2c. 都无 → error（引导到封神榜 / 装 CLI）
  3. 完全找不到 → not_found

安全：``need_auth`` 永不携带明文 key；调用方（秘书工具/端点）负责引导用户
去前端弹窗填 key，key 不进群聊。
"""

from __future__ import annotations

import os
import time
from typing import Any, Optional

from vermes_cli.a2a.credentials import save_credential
from vermes_cli.a2a.login_probe import login_command_for, probe_login
from vermes_cli.a2a.recipes.loader import (
    RECIPES_DIR,
    find_recipe,
    find_recipe_for_discovery,
)
from vermes_cli.a2a.transport import build_acp_transport
from vermes_cli.adapters.agent_discovery import LocalAgentScanner


def _cli_type_of(entry_point: str) -> str:
    """从 discovery 的 entry_point 取 CLI bin 名（``/usr/local/bin/aider`` → ``aider``）。"""
    if not entry_point:
        return ""
    first = entry_point.strip().split()[0]
    return first.rsplit("/", 1)[-1]


def _onboard_via_acp(db, recipe, auth_value: str) -> dict:
    """命中 ACP recipe → 登堂（复用 register-profile 的编排逻辑）。"""
    auth_env = recipe.auth.env_var
    # 鉴权读取：需要 env 但没给且环境未设置 → 探测本机登录态。
    if auth_env and not auth_value and not os.environ.get(auth_env):
        _lp = probe_login(recipe.name, recipe.provider or "")
        if not _lp.is_logged_in:
            return {
                "ok": True,
                "status": "need_auth",
                "agent": recipe.name,
                "provider": recipe.provider,
                "auth_env": auth_env,
                "spawn_command": recipe.spawn_command,
                "login_probe": {"logged_in": _lp.logged_in, "detail": _lp.detail},
                "login_command": login_command_for(recipe.name, recipe.provider or ""),
            }

    # 用户授权的 key → 注入当次进程环境 + 持久化到凭据库（跨重启生效）
    persisted = True
    persist_error: Optional[str] = None
    if auth_env and auth_value:
        os.environ[auth_env] = auth_value
        try:
            save_credential(recipe.name, auth_value)
        except OSError as e:
            persisted = False
            persist_error = f"credential store write failed: {e}"

    # 构造 transport（验证 recipe 可实例化）
    try:
        transport = build_acp_transport(recipe)
    except Exception as e:
        return {
            "ok": False,
            "status": "error",
            "agent": recipe.name,
            "error": f"build transport failed: {e}",
        }

    profile_id = f"a2a:{recipe.provider or recipe.name}"
    now = time.time()
    profile = {
        "id": profile_id,
        "name": recipe.name,
        "description": recipe.description,
        "provider": recipe.provider or "",
        "model": recipe.provider or "",
        "transport": "acp",
        "transport_ref": " ".join(recipe.spawn_command),
        "capability_tags": list(recipe.capabilities),
        "capability_source": getattr(recipe, "capability_source", "unknown") or "unknown",
        "system_prompt": "",
        "toolsets": [],
        "skill_set": "",
        "created_at": now,
    }
    try:
        db.upsert_agent_profile(profile)
        db.upsert_a2a_agent(
            {
                "profile_id": profile_id,
                "name": recipe.name,
                "provider": recipe.provider or "",
                "model": recipe.provider or "",
                "transport": "acp",
                "capabilities": list(recipe.capabilities),
                "registered_at": now,
                "last_heartbeat": now,
                "recipe": recipe.name,
            }
        )
    except Exception as e:
        return {"ok": False, "status": "error", "agent": recipe.name, "error": str(e)}

    healthy, detail = _health_check(transport)
    return {
        "ok": True,
        "status": "success" if healthy else "fail",
        "agent": recipe.name,
        "profile_id": profile_id,
        "transport": "acp",
        "via": "acp",
        "spawn_command": recipe.spawn_command,
        "capabilities": list(recipe.capabilities),
        "health": {"healthy": healthy, "detail": detail},
        "auth": {"env_var": auth_env, "persisted": persisted, "persist_error": persist_error},
    }


def _onboard_via_cli(db, found, cli_type: str) -> dict:
    """本机发现、无 recipe、但 CLI 在白名单 → CLI 直连。"""
    from vermes_cli.blueprints.chat import _CLI_PRINT_ARGS

    now = time.time()
    profile_id = f"local:{cli_type}"
    profile = {
        "id": profile_id,
        "name": found.name,
        "description": found.description or f"本机 {found.kind} agent（CLI 直连）",
        "provider": "",
        "model": "",
        "transport": "cli",
        "transport_ref": cli_type,
        "capability_tags": [],
        "system_prompt": "",
        "toolsets": [],
        "skill_set": "",
        "created_at": now,
    }
    try:
        db.upsert_agent_profile(profile)
    except Exception as e:
        return {"ok": False, "status": "error", "agent": found.name, "error": str(e)}
    return {
        "ok": True,
        "status": "success",
        "agent": found.name,
        "profile_id": profile_id,
        "transport": "cli",
        "via": "cli",
        "cli": cli_type,
        "health": {"healthy": True, "detail": f"{cli_type} 在 PATH，可被 @ 直连调用"},
    }


def _health_check(transport, timeout_seconds: float = 30.0) -> "tuple[bool, str]":
    """ACP 健康检查（与 chat.py ``_acp_health_check`` 同源逻辑，独立实现避免循环 import）。

    两级：PATH 可达性快检 → 真 ``initialize`` 握手。失败不阻断注册。
    """
    import shutil

    binary = getattr(transport, "_acp_command", None)
    if not binary:
        return False, "transport has no acp_command"
    resolved = shutil.which(binary)
    if not resolved:
        return False, f"ACP command not found on PATH: {binary!r}"
    handshake = getattr(transport, "_handshake", None)
    if handshake is None:
        return True, f"resolved {binary} -> {resolved} (handshake unavailable)"
    ok, detail = handshake(timeout_seconds=timeout_seconds)
    if ok:
        return True, f"{detail} ({binary} -> {resolved})"
    return False, f"handshake failed: {detail}"


def onboard_agent(name: str, auth_value: str = "", *, db=None) -> dict:
    """傻瓜式接入外部 agent。

    参数：
      name       —— 用户说的 agent 名（官方 recipe 名 / 通用名 / 本机 discovery id 均可）
      auth_value —— 用户授权的 key（可选；缺 key 时返回 need_auth，引导走前端弹窗）
      db         —— 可注入的 SessionDB（测试用）；默认自建并 finally close

    返回结构化 dict，``status`` ∈ {success, fail, need_auth, error, not_found}。
    """
    from vermes_state import SessionDB

    name = (name or "").strip()
    if not name:
        return {"ok": False, "status": "error", "error": "agent 名不能为空"}

    own_db = db is None
    if own_db:
        db = SessionDB()
    try:
        return _onboard(db, name, auth_value)
    finally:
        if own_db:
            try:
                db.close()
            except Exception:
                pass


def _onboard(db, name: str, auth_value: str) -> dict:
    """决策树主体（见模块 docstring）。"""
    # 步骤 1：recipe 名精确匹配
    recipe = None
    try:
        recipe = find_recipe(name, RECIPES_DIR, recursive=True)
    except Exception:
        recipe = None

    # 步骤 2：本机扫描定位（name / id 模糊匹配）
    found = None
    if recipe is None:
        try:
            _nl = name.lower()
            for a in LocalAgentScanner().scan():
                if a.id.lower() == _nl or a.name.lower() == _nl:
                    found = a
                    break
        except Exception:
            found = None

    # 步骤 2a：本机发现 → fingerprint 反向匹配 recipe
    if recipe is None and found is not None:
        try:
            recipe = find_recipe_for_discovery(
                discovery_id=found.id,
                name=found.name or "",
                entry_point=found.entry_point or "",
                config_dir=found.entry_point or "",
                directory=RECIPES_DIR,
                recursive=True,
            )
        except Exception:
            recipe = None

    # 分支 A：命中 recipe → ACP 登堂
    if recipe is not None:
        return _onboard_via_acp(db, recipe, auth_value)

    # 分支 B：本机发现但无 recipe → 看 CLI 白名单
    if found is not None:
        if found.kind != "cli":
            return {
                "ok": False,
                "status": "error",
                "agent": found.name,
                "error": f"{found.name} 无 ACP recipe 且无 CLI 直连通路，暂无法本地接入；"
                         f"可到封神榜找同名 agent 用官方 recipe 登堂",
            }
        cli_type = _cli_type_of(found.entry_point)
        if not cli_type:
            return {
                "ok": False,
                "status": "error",
                "agent": found.name,
                "error": f"{found.name} 无可用 CLI 入口，无法本地接入",
            }
        from vermes_cli.blueprints.chat import _CLI_PRINT_ARGS

        if cli_type not in _CLI_PRINT_ARGS:
            return {
                "ok": False,
                "status": "error",
                "agent": found.name,
                "error": f"本机直连暂不支持 {cli_type!r}（映射表缺该 CLI 类型）；"
                         f"可到封神榜找同名 agent 用官方 recipe 登堂",
            }
        return _onboard_via_cli(db, found, cli_type)

    # 分支 C：完全找不到
    return {
        "ok": False,
        "status": "not_found",
        "error": f"本机未发现 {name!r}，且封神榜无同名 recipe。"
                 f"请确认已安装其 CLI，或到封神榜用官方 recipe 登堂。",
    }
