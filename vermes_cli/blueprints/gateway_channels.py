"""
Gateway Channels API — 移动渠道接入管理

GET  /api/gateway/channels          — 列出所有平台 schema + 当前配置状态
GET  /api/gateway/channels/{key}    — 获取单个平台详情
PUT  /api/gateway/channels/{key}    — 保存平台凭据（写入 config.yaml + .env）
DELETE /api/gateway/channels/{key}  — 清除平台凭据
POST /api/gateway/channels/{key}/toggle — 启用/禁用平台
"""

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from vermes_cli.gateway_channels import get_all_channel_schemas, get_channel_schema, ChannelSchema

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/gateway/channels", tags=["gateway-channels"])


# ── 请求模型 ──

class SaveChannelRequest(BaseModel):
    fields: Dict[str, str]  # { field_key: value, ... }
    enabled: bool = True


# ── 辅助函数 ──

def _get_vermes_home() -> Path:
    """Get the Vermes home directory."""
    # VERMES_HOME takes precedence; fall back to the canonical ~/.vermes home.
    return Path(os.environ.get("VERMES_HOME") or os.environ.get("VERMES_HOME") or os.path.expanduser("~/.vermes"))


def _load_config_yaml() -> dict:
    """Load config.yaml as a dict."""
    import yaml
    cfg_path = _get_vermes_home() / "config.yaml"
    if not cfg_path.exists():
        return {}
    try:
        with open(cfg_path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        logger.warning("Failed to load config.yaml: %s", e)
        return {}


def _save_config_yaml(data: dict) -> None:
    """Save dict to config.yaml."""
    import yaml
    cfg_path = _get_vermes_home() / "config.yaml"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cfg_path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def _load_env() -> dict:
    """Load .env file as a dict."""
    env_path = _get_vermes_home() / ".env"
    if not env_path.exists():
        return {}
    result = {}
    try:
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, v = line.split("=", 1)
                    result[k.strip()] = v.strip()
    except Exception as e:
        logger.warning("Failed to load .env: %s", e)
    return {}


def _append_env(key: str, value: str) -> None:
    """Append or update a key in .env file."""
    env_path = _get_vermes_home() / ".env"
    env_path.parent.mkdir(parents=True, exist_ok=True)

    lines = []
    if env_path.exists():
        with open(env_path, encoding="utf-8") as f:
            lines = f.readlines()

    found = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(f"{key}="):
            lines[i] = f"{key}={value}\n"
            found = True
            break

    if not found:
        lines.append(f"{key}={value}\n")

    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(lines)


def _remove_env_key(key: str) -> None:
    """Remove a key from .env file."""
    env_path = _get_vermes_home() / ".env"
    if not env_path.exists():
        return

    lines = []
    with open(env_path, encoding="utf-8") as f:
        lines = f.readlines()

    filtered = [line for line in lines if not line.strip().startswith(f"{key}=")]

    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(filtered)


def _sync_env_from_config(config_data: dict, schema: ChannelSchema) -> None:
    """以 config.yaml 为真相源，强制同步 .env 中该平台的所有字段。

    防止 .env 残留旧值导致 Gateway 读到错误凭证。
    """
    platforms = config_data.get("platforms", {})
    plat_data = platforms.get(schema.key, {})
    extra = plat_data.get("extra", {})

    for field in schema.fields:
        if not field.env_key:
            continue

        # 从 config.yaml 读取当前值
        if field.storage == "token":
            value = plat_data.get("token", "")
        elif field.storage == "api_key":
            value = plat_data.get("api_key", "")
        else:
            value = extra.get(field.key, "")

        if value:
            _append_env(field.env_key, value)
        else:
            # config.yaml 中没有该字段，从 .env 中清除
            _remove_env_key(field.env_key)


def _mask(value: str) -> str:
    """Mask a secret value for display."""
    if not value:
        return ""
    if len(value) <= 4:
        return "●●●●"
    return value[:2] + "●" * (len(value) - 4) + value[-2:]


def _is_platform_configured(schema: ChannelSchema, config_data: dict, env_data: dict) -> bool:
    """Check if a platform has all required fields filled."""
    platforms = config_data.get("platforms", {})
    plat_data = platforms.get(schema.key, {})
    extra = plat_data.get("extra", {})
    token = plat_data.get("token", "")
    api_key = plat_data.get("api_key", "")

    for f in schema.fields:
        if not f.required:
            continue
        if f.storage == "token":
            if not token:
                return False
        elif f.storage == "api_key":
            if not api_key:
                return False
        else:  # extra
            val = extra.get(f.key) or env_data.get(f.env_key, "") if f.env_key else extra.get(f.key, "")
            if not val:
                return False
    return True


def _get_field_value(field_key: str, storage: str, schema_key: str, config_data: dict, env_data: dict, env_key: str = "") -> str:
    """Get current value of a field."""
    platforms = config_data.get("platforms", {})
    plat_data = platforms.get(schema_key, {})
    extra = plat_data.get("extra", {})

    if storage == "token":
        return plat_data.get("token", "")
    elif storage == "api_key":
        return plat_data.get("api_key", "")
    else:
        val = extra.get(field_key, "")
        if not val and env_key:
            val = env_data.get(env_key, "")
        return val


def _schema_to_dict(schema: ChannelSchema, config_data: dict, env_data: dict) -> dict:
    """Convert schema to API response dict with current values."""
    platforms = config_data.get("platforms", {})
    plat_data = platforms.get(schema.key, {})
    configured = _is_platform_configured(schema, config_data, env_data)
    enabled = plat_data.get("enabled", False)

    fields = []
    for f in schema.fields:
        raw_val = _get_field_value(f.key, f.storage, schema.key, config_data, env_data, f.env_key)
        display_val = _mask(raw_val) if (f.secret and raw_val) else raw_val
        fields.append({
            "key": f.key,
            "label": f.label,
            "placeholder": f.placeholder,
            "required": f.required,
            "secret": f.secret,
            "storage": f.storage,
            "env_key": f.env_key,
            "value": display_val,
            "has_value": bool(raw_val),
        })

    return {
        "key": schema.key,
        "label": schema.label,
        "icon": schema.icon,
        "category": schema.category,
        "fields": fields,
        "tutorial": schema.tutorial,
        "apply_url": schema.apply_url,
        "note": schema.note,
        "configured": configured,
        "enabled": enabled,
    }


# ── API 端点 ──

@router.get("")
async def list_channels() -> dict:
    """列出所有平台 schema + 当前配置状态。"""
    config_data = _load_config_yaml()
    env_data = _load_env()

    schemas = get_all_channel_schemas()
    channels = [_schema_to_dict(s, config_data, env_data) for s in schemas]

    # 按 category 分组
    grouped: Dict[str, list] = {}
    for ch in channels:
        grouped.setdefault(ch["category"], []).append(ch)

    return {
        "channels": channels,
        "grouped": grouped,
        "total": len(channels),
        "configured_count": sum(1 for ch in channels if ch["configured"]),
    }


@router.get("/{platform_key}")
async def get_channel(platform_key: str) -> dict:
    """获取单个平台详情。"""
    schema = get_channel_schema(platform_key)
    if not schema:
        raise HTTPException(status_code=404, detail=f"Unknown platform: {platform_key}")

    config_data = _load_config_yaml()
    env_data = _load_env()
    return _schema_to_dict(schema, config_data, env_data)


@router.put("/{platform_key}")
async def save_channel(platform_key: str, req: SaveChannelRequest) -> dict:
    """保存平台凭据。写入 config.yaml platforms 段 + .env。

    config.yaml 是唯一真相源：每次保存以 config.yaml 为准同步 .env，
    确保两者一致。Gateway 进程读 .env，后端 API 读 config.yaml，
    必须保证同一字段值相同。
    """
    schema = get_channel_schema(platform_key)
    if not schema:
        raise HTTPException(status_code=404, detail=f"Unknown platform: {platform_key}")

    config_data = _load_config_yaml()
    platforms = config_data.setdefault("platforms", {})
    plat_data = platforms.setdefault(platform_key, {})
    extra = plat_data.setdefault("extra", {})

    for field in schema.fields:
        value = req.fields.get(field.key, "").strip()
        if not value:
            continue

        if field.storage == "token":
            plat_data["token"] = value
        elif field.storage == "api_key":
            plat_data["api_key"] = value
        else:  # extra
            extra[field.key] = value

        # 同时写入 .env（如果定义了 env_key）
        if field.env_key:
            _append_env(field.env_key, value)

    plat_data["enabled"] = req.enabled

    _save_config_yaml(config_data)

    # ── 一致性校验：config.yaml 为真相源，强制同步 .env ──
    # 防止 .env 残留旧值导致 Gateway 读到错误凭证
    _sync_env_from_config(config_data, schema)

    # 重新加载返回最新状态
    config_data = _load_config_yaml()
    env_data = _load_env()
    result = _schema_to_dict(schema, config_data, env_data)
    return {"ok": True, "channel": result}


@router.delete("/{platform_key}")
async def clear_channel(platform_key: str) -> dict:
    """清除平台凭据。"""
    schema = get_channel_schema(platform_key)
    if not schema:
        raise HTTPException(status_code=404, detail=f"Unknown platform: {platform_key}")

    config_data = _load_config_yaml()
    platforms = config_data.get("platforms", {})

    if platform_key in platforms:
        plat_data = platforms[platform_key]
        # 清除凭据但保留平台条目
        plat_data.pop("token", None)
        plat_data.pop("api_key", None)
        plat_data.pop("extra", None)
        plat_data["enabled"] = False
        _save_config_yaml(config_data)

    # 强制同步 .env（config.yaml 中已清除，.env 也清除）
    _sync_env_from_config(config_data, schema)

    return {"ok": True, "message": f"已清除 {schema.label} 的凭据"}


@router.post("/{platform_key}/toggle")
async def toggle_channel(platform_key: str) -> dict:
    """启用/禁用平台（不修改凭据）。"""
    schema = get_channel_schema(platform_key)
    if not schema:
        raise HTTPException(status_code=404, detail=f"Unknown platform: {platform_key}")

    config_data = _load_config_yaml()
    platforms = config_data.setdefault("platforms", {})
    plat_data = platforms.setdefault(platform_key, {})
    current = plat_data.get("enabled", False)
    plat_data["enabled"] = not current

    _save_config_yaml(config_data)

    return {"ok": True, "enabled": not current}


def register_to(app):
    """Register channels routes on the FastAPI app."""
    app.include_router(router)
