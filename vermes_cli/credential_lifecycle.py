"""P1-4: 凭证轮换策略 + approvals 协同

基于现有 auth.py resolve_*_runtime_credentials 链路扩展：
1. 过期检测 + 自动续期（resolve 链路已有，此处加巡检和告警）
2. 泄露轮换（手动触发，重置 auth.json 中指定 provider 的凭证）
3. TrustGate 与 approvals 协同：deny-unless-declared 严格模式 + 建议审批流兜底
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

# ── 凭证健康状态 ──────────────────────────────────────────

@dataclass
class CredentialHealth:
    provider: str
    status: str  # healthy | expiring | expired | missing | error
    expires_at: Optional[str] = None
    last_refresh: Optional[str] = None
    days_until_expiry: Optional[float] = None
    recommendation: str = ""  # 人类可读建议
    can_auto_refresh: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _load_auth_store() -> Dict[str, Any]:
    """复用 auth.py 的 auth store 加载。"""
    try:
        from vermes_cli.auth import _load_auth_store as _load
        return _load()
    except Exception as e:
        log.warning("加载 auth store 失败: %s", e)
        return {}


def _save_auth_store(store: Dict[str, Any]) -> None:
    try:
        from vermes_cli.auth import _save_auth_store as _save
        _save(store)
    except Exception as e:
        log.error("保存 auth store 失败: %s", e)


def check_credential_health(provider_id: str) -> CredentialHealth:
    """检查指定 provider 的凭证健康状态。"""
    store = _load_auth_store()
    state = store.get("providers", {}).get(provider_id)

    if not state:
        return CredentialHealth(
            provider=provider_id,
            status="missing",
            recommendation="未找到凭证，需要授权或手动配置 API Key",
            can_auto_refresh=False,
        )

    expires_at = state.get("expires_at")
    if not expires_at:
        # 无过期时间，可能是永久 API Key
        return CredentialHealth(
            provider=provider_id,
            status="healthy",
            recommendation="永久凭证（无过期时间）",
            can_auto_refresh=False,
        )

    # 解析过期时间
    try:
        from vermes_cli.auth import _parse_iso_timestamp, _is_expiring
        expires_epoch = _parse_iso_timestamp(expires_at)
        if expires_epoch is None:
            return CredentialHealth(
                provider=provider_id,
                status="healthy",
                expires_at=expires_at,
                recommendation="无法解析过期时间，假设有效",
                can_auto_refresh=False,
            )

        now = time.time()
        days_left = (expires_epoch - now) / 86400

        if _is_expiring(expires_at, skew_seconds=300):
            return CredentialHealth(
                provider=provider_id,
                status="expiring",
                expires_at=expires_at,
                days_until_expiry=round(days_left, 1),
                recommendation=f"凭证即将过期（{days_left:.1f} 天），建议立即续期",
                can_auto_refresh=bool(state.get("refresh_token")),
            )

        if expires_epoch < now:
            return CredentialHealth(
                provider=provider_id,
                status="expired",
                expires_at=expires_at,
                days_until_expiry=round(days_left, 1),
                recommendation=f"凭证已过期 {-days_left:.1f} 天，需要重新授权",
                can_auto_refresh=bool(state.get("refresh_token")),
            )

        return CredentialHealth(
            provider=provider_id,
            status="healthy",
            expires_at=expires_at,
            days_until_expiry=round(days_left, 1),
            recommendation=f"凭证有效，{days_left:.1f} 天后过期",
            can_auto_refresh=False,
        )
    except Exception as e:
        return CredentialHealth(
            provider=provider_id,
            status="error",
            recommendation=f"检查失败: {e}",
            can_auto_refresh=False,
        )


def check_all_credentials() -> List[CredentialHealth]:
    """检查所有已存储凭证的健康状态。"""
    store = _load_auth_store()
    providers = store.get("providers", {})
    if not providers:
        return []
    return [check_credential_health(pid) for pid in providers]


def refresh_credential(provider_id: str) -> Tuple[bool, str]:
    """手动触发凭证刷新。

    返回 (success, message)。
    """
    store = _load_auth_store()
    state = store.get("providers", {}).get(provider_id)
    if not state:
        return False, f"未找到 provider '{provider_id}' 的凭证"

    # 尝试调用对应的 resolve 函数
    resolve_map = {
        "qwen": "resolve_qwen_runtime_credentials",
        "gemini": "resolve_gemini_oauth_runtime_credentials",
        "spotify": "resolve_spotify_runtime_credentials",
        "codex": "resolve_codex_runtime_credentials",
        "xai": "resolve_xai_oauth_runtime_credentials",
        "nous": "resolve_nous_runtime_credentials",
    }

    func_name = resolve_map.get(provider_id)
    if not func_name:
        return False, f"provider '{provider_id}' 无自动刷新支持（永久 API Key 无需刷新）"

    try:
        from vermes_cli import auth as auth_module
        resolve_fn = getattr(auth_module, func_name, None)
        if not resolve_fn:
            return False, f"刷新函数 {func_name} 不存在"

        result = resolve_fn(provider_id, force_refresh=True)
        if result and result.get("access_token"):
            return True, f"凭证刷新成功"
        return False, "刷新返回空结果"
    except Exception as e:
        return False, f"刷新失败: {e}"


def rotate_credential(provider_id: str) -> Tuple[bool, str]:
    """泄露轮换：清除旧凭证，标记需要重新授权。

    这不会自动获取新凭证（需要用户交互），但会安全清除旧凭证。
    """
    store = _load_auth_store()
    providers = store.get("providers", {})
    if provider_id not in providers:
        return False, f"未找到 provider '{provider_id}' 的凭证"

    # 备份旧状态（用于审计）
    old_state = providers[provider_id].copy()

    # 清除敏感字段
    sensitive_keys = ["access_token", "refresh_token", "api_key", "id_token",
                      "token_type", "scope", "expires_at", "session_token"]
    for key in sensitive_keys:
        providers[provider_id].pop(key, None)

    providers[provider_id]["rotated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    providers[provider_id]["rotation_reason"] = "manual"

    _save_auth_store(store)

    log.info("凭证轮换: provider=%s, 已清除敏感字段, rotated_at=%s",
             provider_id, providers[provider_id]["rotated_at"])

    return True, f"已清除 {provider_id} 的旧凭证，请重新授权"


# ── TrustGate + approvals 协同 ─────────────────────────────

# 严格模式：deny-unless-declared（默认关，用户可开）
_TRUST_GATE_STRICT = False

def is_trust_gate_strict() -> bool:
    """返回 TrustGate 是否处于严格模式。"""
    return _TRUST_GATE_STRICT


def set_trust_gate_strict(enabled: bool) -> None:
    global _TRUST_GATE_STRICT
    _TRUST_GATE_STRICT = enabled


def suggest_approval(action: str, context: Dict[str, Any]) -> Dict[str, Any]:
    """建议审批流：低危操作给建议而非硬拒。

    返回:
        {
            "action": action,
            "risk_level": "low" | "medium" | "high",
            "suggestion": str,  # 人类可读建议
            "strict_mode": bool,  # 当前是否严格模式
            "decision": "allow" | "suggest" | "deny",  # 最终决策
        }
    """
    risk_map = {
        "read_file": ("low", "读取文件通常安全，建议允许"),
        "write_file": ("medium", "写入文件可能修改数据，建议确认路径"),
        "patch_file": ("medium", "修改文件可能影响功能，建议确认"),
        "exec_command": ("high", "执行命令可能不可逆，建议严格审查"),
        "network_request": ("low", "网络请求通常安全，建议检查目标地址"),
        "delete_file": ("high", "删除操作不可逆，建议确认"),
        "install_package": ("high", "安装包可能引入风险，建议审查来源"),
        "mcp_install": ("medium", "安装 MCP 服务器已通过安全校验，建议确认"),
    }

    risk_level, suggestion = risk_map.get(action, ("medium", "未知操作，建议人工审查"))

    if _TRUST_GATE_STRICT and risk_level in ("high", "medium"):
        decision = "deny"
    elif risk_level == "high":
        decision = "suggest"
    else:
        decision = "allow"

    return {
        "action": action,
        "risk_level": risk_level,
        "suggestion": suggestion,
        "strict_mode": _TRUST_GATE_STRICT,
        "decision": decision,
    }
