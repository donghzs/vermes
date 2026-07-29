"""Blueprint: Config（配置 / 环境变量 / Onboarding）

Endpoints:
- GET  /api/onboarding       — 首次配置检查
- GET  /api/config            — 读取配置
- PUT  /api/config            — 写入配置
- GET  /api/config/defaults   — 默认配置
- GET  /api/config/schema     — 配置 schema
- GET  /api/config/raw        — 原始 YAML
- PUT  /api/config/raw        — 写入原始 YAML
- GET  /api/env               — 环境变量列表
- PUT  /api/env               — 设置环境变量
- DELETE /api/env             — 删除环境变量
- POST /api/env/reveal        — 揭示环境变量值
"""

import asyncio
import logging
import time
from typing import Any, Dict

import yaml
from fastapi import HTTPException, Request
from pydantic import BaseModel

from vermes_cli.config import (
    cfg_get,
    DEFAULT_CONFIG,
    OPTIONAL_ENV_VARS,
    get_config_path,
    get_hermes_home,
    load_config,
    load_env,
    save_config,
    save_env_value,
    remove_env_value,
    redact_key,
)

_log = logging.getLogger(__name__)


# ── max_tokens 策略 ─────────────────────────────────────────────────
def _resolve_max_tokens(model: str) -> int | None:
    """返回 max_tokens 上限，优先用户配置。

    配置方式（在 config.yaml 中）：
    ```yaml
    model:
      max_tokens: 8192  # 自定义 max_tokens，优先级最高
    ```

    如果用户未配置，返回 None（不设置上限，让模型自己决定）。
    """
    # 优先级 1: 从 config.yaml 读取用户配置
    try:
        from vermes_constants import get_hermes_home as _ghh
        home = _ghh()
        cfg_path = home / "config.yaml"
        if cfg_path.exists():
            with open(cfg_path, encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            user_max_tokens = cfg.get("model", {}).get("max_tokens")
            if user_max_tokens is not None:
                try:
                    value = int(user_max_tokens)
                    if value > 0:
                        return value
                except (TypeError, ValueError):
                    pass
    except Exception:
        pass

    # 优先级 2: 不设置上限，让模型自己决定
    return None


# ── Config Schema 构建 ──────────────────────────────────────────────

# Manual overrides for fields that need select options or custom types
_SCHEMA_OVERRIDES: Dict[str, Dict[str, Any]] = {
    "model": {
        "type": "string",
        "description": "Default model (e.g. anthropic/claude-sonnet-4.6)",
        "category": "general",
    },
    "model_context_length": {
        "type": "number",
        "description": "Context window override (0 = auto-detect from model metadata)",
        "category": "general",
    },
    "terminal.backend": {
        "type": "select",
        "description": "Terminal execution backend",
        "options": ["local", "docker", "ssh", "modal", "daytona", "vercel_sandbox", "singularity"],
    },
    "terminal.vercel_runtime": {
        "type": "select",
        "description": "Vercel Sandbox runtime",
        "options": ["node24", "node22", "python3.13"],
    },
    "terminal.modal_mode": {
        "type": "select",
        "description": "Modal sandbox mode",
        "options": ["sandbox", "function"],
    },
    "tts.provider": {
        "type": "select",
        "description": "Text-to-speech provider",
        "options": ["edge", "elevenlabs", "openai", "neutts"],
    },
    "stt.provider": {
        "type": "select",
        "description": "Speech-to-text provider",
        "options": ["local", "openai"],
    },
    "display.skin": {
        "type": "select",
        "description": "CLI visual theme",
        "options": ["default", "ares", "mono", "slate"],
    },
    "dashboard.theme": {
        "type": "select",
        "description": "Web dashboard visual theme",
        "options": ["default", "midnight", "ember", "mono", "cyberpunk", "rose"],
    },
    "display.resume_display": {
        "type": "select",
        "description": "How resumed sessions display history",
        "options": ["minimal", "full", "off"],
    },
    "display.busy_input_mode": {
        "type": "select",
        "description": "Input behavior while agent is running",
        "options": ["interrupt", "queue", "steer"],
    },
    "memory.provider": {
        "type": "select",
        "description": "Memory provider plugin",
        "options": ["builtin", "honcho"],
    },
    "approvals.mode": {
        "type": "select",
        "description": "Dangerous command approval mode",
        "options": ["ask", "yolo", "deny"],
    },
    "context.engine": {
        "type": "select",
        "description": "Context management engine",
        "options": ["default", "custom"],
    },
    "human_delay.mode": {
        "type": "select",
        "description": "Simulated typing delay mode",
        "options": ["off", "typing", "fixed"],
    },
    "logging.level": {
        "type": "select",
        "description": "Log level for agent.log",
        "options": ["DEBUG", "INFO", "WARNING", "ERROR"],
    },
    "agent.service_tier": {
        "type": "select",
        "description": "API service tier (OpenAI/Anthropic)",
        "options": ["", "auto", "default", "flex"],
    },
    "delegation.reasoning_effort": {
        "type": "select",
        "description": "Reasoning effort for delegated subagents",
        "options": ["", "low", "medium", "high"],
    },
}

# Categories with fewer fields get merged into "general" to avoid tab sprawl.
_CATEGORY_MERGE: Dict[str, str] = {
    "privacy": "security",
    "context": "agent",
    "skills": "agent",
    "cron": "agent",
    "network": "agent",
    "checkpoints": "agent",
    "approvals": "security",
    "human_delay": "display",
    "dashboard": "display",
    "code_execution": "agent",
    "prompt_caching": "agent",
    "goals": "agent",
    "telegram": "discord",
}

# Display order for tabs — unlisted categories sort alphabetically after these.
_CATEGORY_ORDER = [
    "general", "agent", "terminal", "display", "delegation",
    "memory", "compression", "security", "browser", "voice",
    "tts", "stt", "logging", "discord", "auxiliary",
]


def _infer_type(value: Any) -> str:
    """Infer a UI field type from a Python value."""
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "number"
    if isinstance(value, float):
        return "number"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "object"
    return "string"


def _build_schema_from_config(
    config: Dict[str, Any],
    prefix: str = "",
) -> Dict[str, Dict[str, Any]]:
    """Walk DEFAULT_CONFIG and produce a flat dot-path → field schema dict."""
    schema: Dict[str, Dict[str, Any]] = {}
    for key, value in config.items():
        full_key = f"{prefix}.{key}" if prefix else key

        if full_key in {"_config_version",}:
            continue

        if prefix:
            category = prefix.split(".")[0]
        elif isinstance(value, dict):
            category = key
        else:
            category = "general"

        if isinstance(value, dict):
            schema.update(_build_schema_from_config(value, full_key))
        else:
            entry: Dict[str, Any] = {
                "type": _infer_type(value),
                "description": full_key.replace(".", " → ").replace("_", " ").title(),
                "category": category,
            }
            if full_key in _SCHEMA_OVERRIDES:
                entry.update(_SCHEMA_OVERRIDES[full_key])
            entry["category"] = _CATEGORY_MERGE.get(entry["category"], entry["category"])
            schema[full_key] = entry
    return schema


CONFIG_SCHEMA = _build_schema_from_config(DEFAULT_CONFIG)

# Inject virtual fields — model_context_length right after "model"
_mcl_entry = _SCHEMA_OVERRIDES["model_context_length"]
_ordered_schema: Dict[str, Dict[str, Any]] = {}
for _k, _v in CONFIG_SCHEMA.items():
    _ordered_schema[_k] = _v
    if _k == "model":
        _ordered_schema["model_context_length"] = _mcl_entry
CONFIG_SCHEMA = _ordered_schema


# ── Pydantic models ─────────────────────────────────────────────────

class ConfigUpdate(BaseModel):
    config: dict


class EnvVarUpdate(BaseModel):
    key: str
    value: str


class EnvVarDelete(BaseModel):
    key: str


class EnvVarReveal(BaseModel):
    key: str


class RawConfigUpdate(BaseModel):
    yaml_text: str


# ── Config normalize/denormalize ─────────────────────────────────────

def _normalize_config_for_web(config: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize config for the web UI.

    Hermes supports ``model`` as either a bare string or a dict.
    Normalize to the string form so the frontend schema matches.
    Also surfaces ``model_context_length`` as a top-level field.
    """
    config = dict(config)
    model_val = config.get("model")
    if isinstance(model_val, dict):
        ctx_len = model_val.get("context_length", 0)
        config["model"] = model_val.get("default", model_val.get("name", ""))
        config["model_context_length"] = ctx_len if isinstance(ctx_len, int) else 0
    else:
        config["model_context_length"] = 0
    return config


def _denormalize_config_from_web(config: Dict[str, Any]) -> Dict[str, Any]:
    """Reverse _normalize_config_for_web before saving.

    Reconstructs ``model`` as a dict by reading the current on-disk config
    to recover model subkeys that were stripped from the GET response.
    Also handles ``model_context_length``.
    """
    config = dict(config)
    config.pop("_model_meta", None)

    ctx_override = config.pop("model_context_length", 0)
    if not isinstance(ctx_override, int):
        try:
            ctx_override = int(ctx_override)
        except (TypeError, ValueError):
            ctx_override = 0

    model_val = config.get("model")
    if isinstance(model_val, str) and model_val:
        try:
            disk_config = load_config()
            disk_model = disk_config.get("model")
            if isinstance(disk_model, dict):
                disk_model["default"] = model_val
                if ctx_override > 0:
                    disk_model["context_length"] = ctx_override
                else:
                    disk_model.pop("context_length", None)
                config["model"] = disk_model
            elif ctx_override > 0:
                config["model"] = {
                    "default": model_val,
                    "context_length": ctx_override,
                }
        except Exception:
            pass
    return config


# ── Rate limiter state (for reveal endpoint) ─────────────────────────
_reveal_timestamps: list = []
_REVEAL_MAX_PER_WINDOW = 5
_REVEAL_WINDOW_SECONDS = 30


# ── Allowed env var keys for PUT ─────────────────────────────────────
_ENV_WRITE_ALLOWED_KEYS: frozenset = frozenset({
    "DEFAULT_MODEL", "DEFAULT_PROVIDER", "THEME", "LANGUAGE",
    "VBIT_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY",
    "QWEN_API_KEY", "ZHIPU_API_KEY", "MISTRAL_API_KEY",
    "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY",
    "XIAOMI_API_KEY", "DOUBAO_API_KEY", "MOONSHOT_API_KEY",
    "BAICHUAN_API_KEY", "YI_API_KEY", "SPARK_API_KEY",
    "SILICONFLOW_API_KEY", "Baidu_API_KEY", "BAIDU_API_KEY",
    "XINGHUO_API_KEY", "STEPFUN_API_KEY", "MINIMAX_API_KEY",
    "ANT_LING_API_KEY",
    "GEMINI_API_KEY", "GROQ_API_KEY", "TOGETHER_API_KEY",
    "COHERE_API_KEY",
    "CUSTOM_API_KEY",
})


def _allowed_env_keys():
    """Allowlist for PUT /api/env.

    Starts from the hardcoded provider/LLM keys and is unioned with every
    dynamically registered business-service env var, so the unified "services"
    API form (driven by the same ``register_service`` registry the schema
    endpoint uses) can persist credentials. Single source of truth = the
    registry, so the two can never drift.
    """
    keys = set(_ENV_WRITE_ALLOWED_KEYS)
    try:
        from agent.service_credentials import get_registered_services

        for _sid, _meta in get_registered_services().items():
            for _field in _meta.get("fields", []):
                _k = _field.get("key")
                if _k:
                    keys.add(_k)
    except Exception:
        pass
    return keys


# ── Route handlers ───────────────────────────────────────────────────

async def get_onboarding():
    """Check if Vermes is configured enough to start chatting."""
    from vermes_cli.config import get_env_value

    cfg = load_config()
    missing = []
    model_cfg = cfg.get("model", {})

    model_name = model_cfg.get("default", "") if isinstance(model_cfg, dict) else str(model_cfg or "")
    if not model_name:
        missing.append("model")

    provider_id = model_cfg.get("provider", "") if isinstance(model_cfg, dict) else ""
    if not provider_id:
        missing.append("provider")

    from providers import get_provider_profile
    profile = get_provider_profile(provider_id) if provider_id else None
    has_key = False
    if profile and profile.env_vars:
        has_key = any(get_env_value(ev) for ev in profile.env_vars)
    if not has_key:
        has_key = bool(get_env_value("OPENAI_API_KEY"))
    if not has_key:
        missing.append("api_key")

    return {"configured": len(missing) == 0, "missing": missing}


async def get_config():
    config = _normalize_config_for_web(load_config())
    return {k: v for k, v in config.items() if not k.startswith("_")}


async def get_defaults():
    return DEFAULT_CONFIG


async def get_cloud_models():
    """Return cloud/free/recommended provider metadata for the frontend.

    Single source of truth derived from chat.PROVIDERS — eliminates the need
    for hardcoded CLOUD_MODELS / RECOMMENDED_IDS in api.js / Settings.vue.
    """
    from vermes_cli.blueprints.chat import PROVIDERS
    cloud = [pid for pid, info in PROVIDERS.items() if info.get("cloud", False)]
    recommended = [
        {"id": pid, "free": info.get("free", False)}
        for pid, info in PROVIDERS.items() if info.get("recommended", False)
    ]
    return {"cloud_models": cloud, "recommended_providers": recommended}


async def get_schema():
    # Base schema from DEFAULT_CONFIG (main LLM + system settings).
    fields = dict(CONFIG_SCHEMA)

    # Aggregate business-service API fields into ONE "services" category so the
    # desktop frontend renders a single API section instead of enumerating every
    # API-needing plugin/tool/skill separately (owner directive: the frontend
    # "can't possibly list them all"). Plugins register their own service
    # metadata at import time; the framework stays vendor-agnostic.
    try:
        from agent.service_credentials import get_registered_services

        dynamic_categories = []
        for sid, meta in get_registered_services().items():
            label = meta.get("label", sid)
            category = meta.get("category", "services")
            if category not in dynamic_categories:
                dynamic_categories.append(category)
            for field in meta.get("fields", []):
                kind = field.get("kind", "extra")
                suffix = kind if kind in ("api_key", "base_url") else field["key"].lower()
                entry = {
                    "type": "string",
                    "description": field.get("label") or f"{label} {kind}",
                    "category": category,
                    "env_var": field.get("key"),
                }
                if field.get("secret"):
                    entry["secret"] = True
                fields[f"services.{sid}.{suffix}"] = entry
    except Exception:
        dynamic_categories = ["services"]

    categories = list(_CATEGORY_ORDER)
    for cat in dynamic_categories or ["services"]:
        if cat not in categories:
            categories.append(cat)
    return {"fields": fields, "category_order": categories}


async def get_registered_services_endpoint():
    """Expose the plugin-declared service registry for the settings UI.

    Metadata only (labels/categories/field descriptors) — never credential
    values. The frontend combines this with GET /api/env (token-protected)
    for set-status/masking, and PUT /api/env to persist values. Powers the
    grouped settings tabs (e.g. category="literature" → 文献源 tab) without
    the framework hardcoding any vendor names.
    """
    try:
        from agent.service_credentials import get_registered_services

        return {"services": get_registered_services()}
    except Exception:
        _log.exception("GET /api/registered-services failed")
        return {"services": {}}


# ── 自定义文献源（用户自建机构内部文献库，无限添加） ────────────────────────
# 元数据（字段名/认证方式/端点）持久化到 ~/.vermes/literature_custom_sources.json；
# 实际凭证仍走 PUT /api/env（命名空间 LIT_<ID>_*），复用掩码/审计/白名单。


async def list_literature_custom_sources():
    """List user-defined literature sources (metadata only — never secrets)."""
    try:
        from agent.literature_custom_store import list_custom_sources

        return {"sources": list_custom_sources()}
    except Exception:
        _log.exception("GET /api/literature-custom-sources failed")
        return {"sources": []}


async def create_literature_custom_source(body: Dict[str, Any], request: Request):
    from vermes_cli.web_server import _require_token

    _require_token(request)
    try:
        from agent.literature_custom_store import add_custom_source

        definition = add_custom_source(body or {})
        return {"ok": True, "source": definition}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        _log.exception("POST /api/literature-custom-sources failed")
        raise HTTPException(status_code=500, detail="Internal server error")


async def update_literature_custom_source(source_id: str, body: Dict[str, Any], request: Request):
    from vermes_cli.web_server import _require_token

    _require_token(request)
    try:
        from agent.literature_custom_store import update_custom_source

        definition = update_custom_source(source_id, body or {})
        if definition is None:
            raise HTTPException(status_code=404, detail=f"自定义文献源 '{source_id}' 不存在")
        return {"ok": True, "source": definition}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception:
        _log.exception("PUT /api/literature-custom-sources failed")
        raise HTTPException(status_code=500, detail="Internal server error")


async def delete_literature_custom_source(source_id: str, request: Request):
    from vermes_cli.web_server import _require_token

    _require_token(request)
    try:
        from agent.literature_custom_store import delete_custom_source

        if not delete_custom_source(source_id):
            raise HTTPException(status_code=404, detail=f"自定义文献源 '{source_id}' 不存在")
        return {"ok": True, "id": source_id}
    except HTTPException:
        raise
    except Exception:
        _log.exception("DELETE /api/literature-custom-sources failed")
        raise HTTPException(status_code=500, detail="Internal server error")


async def parse_literature_credential_source(body: Dict[str, Any], request: Request):
    """粘贴凭证块 → 自动识别并一键接入为自定义文献源。

    接收 ``{"text": "卡号：...\\n密码：...\\n网址：..."}``，解析后创建自定义文献源
    并将凭证落盘到 .env（命名空间 LIT_<ID>_*）。返回脱敏摘要，绝不回显明文密码。
    """
    from vermes_cli.web_server import _require_token

    _require_token(request)
    text = (body or {}).get("text", "")
    if not text or not text.strip():
        raise HTTPException(status_code=400, detail="请提供要识别的凭证文本（卡号/密码/网址）")
    try:
        from agent.literature_custom_store import register_source_from_credential_block

        result = register_source_from_credential_block(text)
    except Exception:
        _log.exception("POST /api/literature-custom-sources/parse failed")
        raise HTTPException(status_code=500, detail="识别失败")
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "识别失败"))
    return result


# ── 本地文献库（用户本地文件夹 / USB）──────────────────────────────────────────

async def list_literature_local_sources():
    """List the user's local literature libraries (config only, no secrets)."""
    try:
        from agent.local_library_store import list_local_libraries

        return {"sources": list_local_libraries()}
    except Exception:
        _log.exception("GET /api/literature-local-sources failed")
        return {"sources": []}


async def create_literature_local_source(body: Dict[str, Any], request: Request):
    from vermes_cli.web_server import _require_token

    _require_token(request)
    root = (body or {}).get("root", "")
    label = (body or {}).get("label", "")
    description = (body or {}).get("description", "")
    try:
        from agent.local_library_store import add_local_library

        rec = add_local_library(root, label or None, description=description or "")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        _log.exception("POST /api/literature-local-sources failed")
        raise HTTPException(status_code=500, detail="Internal server error")
    # Index off the request thread so the UI stays responsive on large folders.
    try:
        from agent.local_library_index import index_library
        from agent.local_library_store import touch_indexed

        summary = await asyncio.to_thread(index_library, rec["id"], rec["root"], False)
        touch_indexed(
            rec["id"],
            summary.get("indexed", 0) + summary.get("updated", 0),
            "indexed" if summary.get("errors", 0) == 0 else "error",
        )
        rec = {**rec, "index_summary": summary}
    except Exception as exc:  # noqa: BLE001
        _log.warning("local library index failed (deferred): %s", exc)
        rec = {**rec, "index_summary": {"error": str(exc)}}
    return {"ok": True, "source": rec}


async def delete_literature_local_source(source_id: str, request: Request):
    from vermes_cli.web_server import _require_token

    _require_token(request)
    try:
        from agent.local_library_store import delete_local_library

        if not delete_local_library(source_id):
            raise HTTPException(status_code=404, detail=f"本地文献库 '{source_id}' 不存在")
        return {"ok": True, "id": source_id}
    except HTTPException:
        raise
    except Exception:
        _log.exception("DELETE /api/literature-local-sources failed")
        raise HTTPException(status_code=500, detail="Internal server error")


async def reindex_literature_local_source(source_id: str, request: Request):
    from vermes_cli.web_server import _require_token

    _require_token(request)
    try:
        from agent.local_library_index import index_library
        from agent.local_library_store import get_local_library, touch_indexed

        lib = get_local_library(source_id)
        if lib is None:
            raise HTTPException(status_code=404, detail=f"本地文献库 '{source_id}' 不存在")
        summary = await asyncio.to_thread(index_library, source_id, lib["root"], True)
        touch_indexed(
            source_id,
            summary.get("indexed", 0) + summary.get("updated", 0),
            "indexed" if summary.get("errors", 0) == 0 else "error",
        )
        return {"ok": True, "id": source_id, "summary": summary}
    except HTTPException:
        raise
    except Exception:
        _log.exception("POST /api/literature-local-sources/{id}/index failed")
        raise HTTPException(status_code=500, detail="Internal server error")


async def update_config(body: ConfigUpdate):
    try:
        save_config(_denormalize_config_from_web(body.config))
        return {"ok": True}
    except Exception:
        _log.exception("PUT /api/config failed")
        raise HTTPException(status_code=500, detail="Internal server error")


async def get_config_raw():
    path = get_config_path()
    if not path.exists():
        return {"yaml": ""}
    return {"yaml": path.read_text(encoding="utf-8")}


async def update_config_raw(body: RawConfigUpdate):
    try:
        parsed = yaml.safe_load(body.yaml_text)
        if not isinstance(parsed, dict):
            raise HTTPException(status_code=400, detail="YAML must be a mapping")
        save_config(parsed)
        return {"ok": True}
    except yaml.YAMLError as e:
        raise HTTPException(status_code=400, detail=f"Invalid YAML: {e}")


async def get_env_vars(request: Request):
    from vermes_cli.web_server import _require_token
    _require_token(request)
    env_on_disk = load_env()
    result = {}
    for var_name, info in OPTIONAL_ENV_VARS.items():
        value = env_on_disk.get(var_name)
        result[var_name] = {
            "is_set": bool(value),
            "redacted_value": redact_key(value) if value else None,
            "description": info.get("description", ""),
            "url": info.get("url"),
            "category": info.get("category", ""),
            "is_password": info.get("password", False),
            "tools": info.get("tools", []),
            "advanced": info.get("advanced", False),
        }
    # Append dynamically registered business-service env vars so the unified
    # "services" API form can reflect set status (and mask values) for keys
    # that aren't in the static OPTIONAL_ENV_VARS table.
    try:
        from agent.service_credentials import get_registered_services

        for _sid, _meta in get_registered_services().items():
            _category = _meta.get("category", "services")
            for _field in _meta.get("fields", []):
                _env = _field.get("key")
                if not _env or _env in result:
                    continue
                _val = env_on_disk.get(_env)
                result[_env] = {
                    "is_set": bool(_val),
                    "redacted_value": redact_key(_val) if _val else None,
                    "description": _field.get("label")
                        or f"{_meta.get('label', _sid)} {_field.get('kind', '')}".strip(),
                    "url": _meta.get("url"),
                    "category": _category,
                    "is_password": bool(_field.get("secret")),
                    "tools": [],
                    "advanced": False,
                }
    except Exception:
        pass
    return result


async def set_env_var(body: EnvVarUpdate, request: Request):
    from vermes_cli.web_server import _require_token
    _require_token(request)
    if body.key not in _allowed_env_keys():
        raise HTTPException(status_code=403, detail=f"Key '{body.key}' is not allowed")
    try:
        _log.info(f"[ENV] Updated {body.key}")
        save_env_value(body.key, body.value)
        return {"ok": True, "key": body.key}
    except Exception:
        _log.exception("PUT /api/env failed")
        raise HTTPException(status_code=500, detail="Internal server error")


async def remove_env_var(body: EnvVarDelete, request: Request):
    from vermes_cli.web_server import _require_token
    _require_token(request)
    try:
        removed = remove_env_value(body.key)
        if not removed:
            raise HTTPException(status_code=404, detail=f"{body.key} not found in .env")
        return {"ok": True, "key": body.key}
    except HTTPException:
        raise
    except Exception:
        _log.exception("DELETE /api/env failed")
        raise HTTPException(status_code=500, detail="Internal server error")


async def reveal_env_var(body: EnvVarReveal, request: Request):
    """Return the real (unredacted) value of a single env var.

    Protected by session token + rate limiting + audit logging.
    """
    # Lazy import to avoid circular dependency
    from vermes_cli.web_server import _require_token
    _require_token(request)

    # --- Rate limit ---
    now = time.time()
    cutoff = now - _REVEAL_WINDOW_SECONDS
    _reveal_timestamps[:] = [t for t in _reveal_timestamps if t > cutoff]
    if len(_reveal_timestamps) >= _REVEAL_MAX_PER_WINDOW:
        raise HTTPException(status_code=429, detail="Too many reveal requests. Try again shortly.")
    _reveal_timestamps.append(now)

    # --- Reveal ---
    env_on_disk = load_env()
    value = env_on_disk.get(body.key)
    if value is None:
        raise HTTPException(status_code=404, detail=f"{body.key} not found in .env")

    _log.info("env/reveal: %s", body.key)
    return {"key": body.key, "value": value}


# ── Registration ─────────────────────────────────────────────────────

def register_to(app):
    """Register config/env/onboarding routes on the FastAPI app."""
    app.add_api_route("/api/onboarding", get_onboarding, methods=["GET"])
    app.add_api_route("/api/config", get_config, methods=["GET"])
    app.add_api_route("/api/config", update_config, methods=["PUT"])
    app.add_api_route("/api/config/defaults", get_defaults, methods=["GET"])
    app.add_api_route("/api/config/cloud-models", get_cloud_models, methods=["GET"])
    app.add_api_route("/api/config/schema", get_schema, methods=["GET"])
    app.add_api_route("/api/registered-services", get_registered_services_endpoint, methods=["GET"])
    app.add_api_route("/api/literature-custom-sources", list_literature_custom_sources, methods=["GET"])
    app.add_api_route("/api/literature-custom-sources", create_literature_custom_source, methods=["POST"])
    app.add_api_route("/api/literature-custom-sources/{source_id}", update_literature_custom_source, methods=["PUT"])
    app.add_api_route("/api/literature-custom-sources/{source_id}", delete_literature_custom_source, methods=["DELETE"])
    app.add_api_route("/api/literature-custom-sources/parse", parse_literature_credential_source, methods=["POST"])

    # --- Local literature libraries (user folders / USB) ---
    app.add_api_route("/api/literature-local-sources", list_literature_local_sources, methods=["GET"])
    app.add_api_route("/api/literature-local-sources", create_literature_local_source, methods=["POST"])
    app.add_api_route("/api/literature-local-sources/{source_id}", delete_literature_local_source, methods=["DELETE"])
    app.add_api_route("/api/literature-local-sources/{source_id}/index", reindex_literature_local_source, methods=["POST"])
    app.add_api_route("/api/config/raw", get_config_raw, methods=["GET"])
    app.add_api_route("/api/config/raw", update_config_raw, methods=["PUT"])
    app.add_api_route("/api/env", get_env_vars, methods=["GET"])
    app.add_api_route("/api/env", set_env_var, methods=["PUT"])
    app.add_api_route("/api/env", remove_env_var, methods=["DELETE"])
    app.add_api_route("/api/env/reveal", reveal_env_var, methods=["POST"])


blueprint = None  # no APIRouter; uses register_to(app) pattern
