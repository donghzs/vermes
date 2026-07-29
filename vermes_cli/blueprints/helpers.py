"""
Blueprint 共享辅助函数

被 web_server.py 和各 Blueprint 共用，避免循环导入。
所有依赖 vermes_cli.web_server 的辅助函数放在这里。
"""

import asyncio
import json
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

import yaml


# ════════════════════════════════════════════════════════════════
# 1. Chat 核心辅助
# ════════════════════════════════════════════════════════════════

def _get_chat_credentials() -> Tuple[str, str, str]:
    """Return (base_url, api_key, default_model) from config.yaml + .env."""
    from vermes_cli.config import get_vermes_home

    home = get_vermes_home()
    cfg_path = home / "config.yaml"
    env_path = home / ".env"

    base_url = ""
    default_model = ""
    api_key = ""
    provider = ""

    PROVIDER_ENV_MAP = {
        "deepseek": "DEEPSEEK_API_KEY",
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "gemini": "GEMINI_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "vbit": "VBIT_API_KEY",
        "alibaba": "QWEN_API_KEY",
        "qwen": "QWEN_API_KEY",
        "zhipu": "ZHIPU_API_KEY",
        "doubao": "DOUBAO_API_KEY",
        "moonshot": "MOONSHOT_API_KEY",
        "baichuan": "BAICHUAN_API_KEY",
        "yi": "YI_API_KEY",
        "spark": "SPARK_API_KEY",
        "siliconflow": "SILICONFLOW_API_KEY",
        "mistral": "MISTRAL_API_KEY",
        "cohere": "COHERE_API_KEY",
        "custom": "CUSTOM_API_KEY",
        "xiaomi": "XIAOMI_API_KEY",
        "ant-ling": "ANT_LING_API_KEY",
        "ollama": None,
    }

    PROVIDER_BASE_URL = {
        "deepseek": "https://api.deepseek.com/v1",
        "openai": "https://api.openai.com/v1",
        "anthropic": "https://api.anthropic.com/v1",
        "gemini": "https://generativelanguage.googleapis.com/v1beta",
        "openrouter": "https://openrouter.ai/api/v1",
        "vbit": "https://vbit.top/v1",
        "alibaba": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "zhipu": "https://open.bigmodel.cn/api/paas/v4",
        "doubao": "https://ark.cn-beijing.volces.com/api/v3",
        "moonshot": "https://api.moonshot.cn/v1",
        "baichuan": "https://api.baichuan-ai.com/v1",
        "yi": "https://api.lingyiwanwu.com/v1",
        "spark": "https://spark-api-open.xf-yun.com/v1",
        "siliconflow": "https://api.siliconflow.cn/v1",
        "mistral": "https://api.mistral.ai/v1",
        "cohere": "https://api.cohere.ai/v1",
        "ollama": "http://localhost:11434/v1",
        "xiaomi": "https://api.xiaomi.com/v1",
        "ant-ling": "https://api.ant-ling.com/v1",
    }

    if cfg_path.exists():
        with open(cfg_path, encoding='utf-8') as f:
            cfg = yaml.safe_load(f) or {}
        m = cfg.get("model", {})
        base_url = m.get("base_url", "")
        default_model = m.get("default", "")
        provider = m.get("provider", "")

    env_var_name = PROVIDER_ENV_MAP.get(provider, "OPENAI_API_KEY")
    if not base_url and provider:
        base_url = PROVIDER_BASE_URL.get(provider, "")

    if env_path.exists():
        env_content = env_path.read_text()
        for line in env_content.splitlines():
            line = line.strip()
            if env_var_name and line.startswith(f"{env_var_name}="):
                api_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                break

    return base_url, api_key, default_model


def _resolve_model_provider(
    model: str, explicit_provider: Optional[str] = None
) -> Tuple[str, str, str]:
    """Resolve provider, base_url, and model name from a model string or explicit provider."""
    PROVIDER_BASE_URL = {
        "deepseek": "https://api.deepseek.com/v1",
        "openai": "https://api.openai.com/v1",
        "anthropic": "https://api.anthropic.com/v1",
        "gemini": "https://generativelanguage.googleapis.com/v1beta",
        "openrouter": "https://openrouter.ai/api/v1",
        "vbit": "https://vbit.top/v1",
        "alibaba": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "zhipu": "https://open.bigmodel.cn/api/paas/v4",
        "doubao": "https://ark.cn-beijing.volces.com/api/v3",
        "moonshot": "https://api.moonshot.cn/v1",
        "baichuan": "https://api.baichuan-ai.com/v1",
        "yi": "https://api.lingyiwanwu.com/v1",
        "spark": "https://spark-api-open.xf-yun.com/v1",
        "siliconflow": "https://api.siliconflow.cn/v1",
        "mistral": "https://api.mistral.ai/v1",
        "cohere": "https://api.cohere.ai/v1",
        "ollama": "http://localhost:11434/v1",
        "xiaomi": "https://api.xiaomi.com/v1",
        "ant-ling": "https://api.ant-ling.com/v1",
    }

    MODEL_PROVIDER_MAP = {
        "deepseek-chat": "deepseek",
        "deepseek-reasoner": "deepseek",
        "deepseek-v4-flash": "deepseek",
        "deepseek-v4": "deepseek",
        "agnes-2.0-flash": "agnes",
        "agnes-": "agnes",
        "gpt-4o": "vbit",
        "claude-opus-4": "vbit",
        "gpt-4": "openai",
        "claude-": "anthropic",
        "gemini-": "gemini",
        "openrouter/": "openrouter",
    }

    if explicit_provider:
        provider = explicit_provider
        base_url = PROVIDER_BASE_URL.get(provider, "")
        return provider, base_url, model

    # Infer from model name
    for prefix, p in MODEL_PROVIDER_MAP.items():
        if model.startswith(prefix):
            return p, PROVIDER_BASE_URL.get(p, ""), model

    return "", "", model


# ════════════════════════════════════════════════════════════════
# 2. 配额 / 微信登录 辅助
# ════════════════════════════════════════════════════════════════

async def _claim_trial_token(wechat_openid: str) -> dict:
    """Call vbit backend to claim trial token for WeChat user."""
    import httpx

    try:
        resp = httpx.get(
            f"https://vbit.top/api/claim?device_id={wechat_openid}",
            timeout=10,
        )
        data = resp.json()
        if data.get("success"):
            return {"success": True, "token": data.get("token", "")}
        return {"success": False, "error": data.get("error", "领取失败")}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _check_quota(wechat_openid: str) -> dict:
    """Check quota for WeChat user via vbit backend."""
    import httpx

    try:
        resp = httpx.get(
            "https://vbit.top/api/quota/check",
            headers={"X-WeChat-Openid": wechat_openid},
            timeout=10,
        )
        return resp.json()
    except Exception:
        return {"success": False}


async def _spend_quota(wechat_openid: str, tokens_used: int) -> dict:
    """Report token usage to vbit backend for quota deduction."""
    import httpx

    try:
        resp = httpx.post(
            "https://vbit.top/api/quota/spend",
            json={"wechat_openid": wechat_openid, "tokens_used": tokens_used},
            timeout=10,
        )
        return resp.json()
    except Exception:
        return {"success": False}


# ════════════════════════════════════════════════════════════════
# 3. Session 辅助
# ════════════════════════════════════════════════════════════════

# ════════════════════════════════════════════════════════════════
# 5. Profile 辅助（供 cron_jobs 等蓝图使用）
# ════════════════════════════════════════════════════════════════

def _profile_attr(info, name: str, default: Any = None) -> Any:
    try:
        return getattr(info, name)
    except Exception:
        return default


def _profile_to_dict(info) -> Dict[str, Any]:
    return {
        "name": _profile_attr(info, "name", ""),
        "path": str(_profile_attr(info, "path", "")),
        "is_default": bool(_profile_attr(info, "is_default", False)),
        "model": _profile_attr(info, "model"),
        "provider": _profile_attr(info, "provider"),
        "has_env": bool(_profile_attr(info, "has_env", False)),
        "skill_count": int(_profile_attr(info, "skill_count", 0) or 0),
    }


def _fallback_profile_dicts(profiles_mod) -> List[Dict[str, Any]]:
    def _safe(callable_, default):
        try:
            return callable_()
        except Exception:
            return default

    profiles: List[Dict[str, Any]] = []
    default_home = profiles_mod._get_default_vermes_home()
    if default_home.is_dir():
        model, provider = _safe(lambda: profiles_mod._read_config_model(default_home), (None, None))
        profiles.append({
            "name": "default",
            "path": str(default_home),
            "is_default": True,
            "model": model,
            "provider": provider,
            "has_env": (default_home / ".env").exists(),
            "skill_count": _safe(lambda: profiles_mod._count_skills(default_home), 0),
        })

    profiles_root = profiles_mod._get_profiles_root()
    if profiles_root.is_dir():
        for entry in sorted(profiles_root.iterdir()):
            if not entry.is_dir() or not profiles_mod._PROFILE_ID_RE.match(entry.name):
                continue
            model, provider = _safe(lambda entry=entry: profiles_mod._read_config_model(entry), (None, None))
            profiles.append({
                "name": entry.name,
                "path": str(entry),
                "is_default": False,
                "model": model,
                "provider": provider,
                "has_env": (entry / ".env").exists(),
                "skill_count": _safe(lambda entry=entry: profiles_mod._count_skills(entry), 0),
            })

    return profiles


# ════════════════════════════════════════════════════════════════
# 6. Session 辅助
# ════════════════════════════════════════════════════════════════

def _session_latest_descendant(session_id: str):
    """Resolve a session id to the newest child leaf session.

    /model may create child sessions. Dashboard refresh should continue the
    newest child instead of reopening the old parent.
    """
    from vermes_state import SessionDB

    def row_get(row, key, index):
        if isinstance(row, dict):
            return row.get(key)
        try:
            return row[key]
        except Exception:
            try:
                return row[index]
            except Exception:
                return None

    db = SessionDB()
    try:
        sid = db.resolve_session_id(session_id)
        if not sid or not db.get_session(sid):
            return None, []

        conn = (
            getattr(db, "conn", None)
            or getattr(db, "_conn", None)
            or getattr(db, "connection", None)
            or getattr(db, "_connection", None)
        )

        rows = []
        if conn is not None:
            raw_rows = conn.execute(
                "SELECT id, parent_session_id, started_at FROM sessions"
            ).fetchall()
            for row in raw_rows:
                rows.append({
                    "id": row_get(row, "id", 0),
                    "parent_session_id": row_get(row, "parent_session_id", 1),
                    "started_at": row_get(row, "started_at", 2),
                })
        else:
            rows = db.list_sessions_rich(limit=10000, offset=0)

        children = {}
        for row in rows:
            rid = row.get("id")
            parent = row.get("parent_session_id")
            if rid and parent:
                children.setdefault(parent, []).append(row)

        def started(row):
            try:
                return float(row.get("started_at") or 0)
            except Exception:
                return 0.0

        current = sid
        path = [sid]
        seen = {sid}

        while children.get(current):
            candidates = [r for r in children[current] if r.get("id") not in seen]
            if not candidates:
                break
            candidates.sort(key=started, reverse=True)
            current = candidates[0]["id"]
            path.append(current)
            seen.add(current)

        return current, path
    finally:
        db.close()
