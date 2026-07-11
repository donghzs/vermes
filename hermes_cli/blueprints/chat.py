"""Blueprint: Chat（聊天核心路由）

Endpoints:
- POST /api/chat/completions  — Agent-powered 聊天（流式/非流式）
- GET  /api/chat/models       — 可用模型列表
"""

import asyncio
import base64 as b64mod
import json
import logging

logger = logging.getLogger(__name__)
import os
import secrets
import time
from pathlib import Path
from typing import Optional

import yaml
from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from hermes_cli.config import load_config, remove_env_value

_log = logging.getLogger(__name__)

from hermes_cli.blueprints.agent_cache import (
    _agent_cache,
    stop_agent_session,
    clean_agent_for_session,
)


# ── Attachment constants ─────────────────────────────────────────────

_ALLOWED_MIME_TYPES: frozenset = frozenset({
    "image/png", "image/jpeg", "image/jpg", "image/gif", "image/webp", "image/bmp",
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "text/plain", "text/markdown", "text/x-markdown", "text/csv",
    "application/json", "application/x-yaml", "text/yaml",
    "text/html", "text/css", "text/javascript", "application/javascript",
    "text/x-python", "application/x-python",
    "application/zip", "application/x-zip-compressed",
    "application/x-tar", "application/gzip",
})
_MAX_ATTACHMENT_SIZE: int = 50 * 1024 * 1024  # 50 MB total per request
_MAX_SINGLE_ATTACHMENT_SIZE: int = 20 * 1024 * 1024  # 20 MB per file


# ── File text extraction ─────────────────────────────────────────────

def _extract_file_text(name: str, mime: str, b64_data: str) -> str | None:
    """Extract readable text from an uploaded file.

    Returns extracted text, or None if the file should be treated as binary.
    Results are truncated to ~50 KB to avoid context bloat.
    """
    try:
        raw = b64mod.b64decode(b64_data)
    except Exception:
        return None

    # PDF
    if mime == "application/pdf" or name.lower().endswith(".pdf"):
        try:
            import fitz  # pymupdf
            doc = fitz.open(stream=raw, filetype="pdf")
            parts = []
            for page in doc:
                parts.append(page.get_text())
            doc.close()
            text = "\n".join(parts)
            return text[:50000] + ("\n... (truncated)" if len(text) > 50000 else "")
        except Exception as exc:
            return f"[PDF extraction failed: {exc}]"

    # DOCX
    if mime in ("application/vnd.openxmlformats-officedocument.wordprocessingml.document",) or name.lower().endswith(".docx"):
        try:
            import docx
            from io import BytesIO
            doc = docx.Document(BytesIO(raw))
            paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
            text = "\n".join(paragraphs)
            return text[:50000] + ("\n... (truncated)" if len(text) > 50000 else "")
        except Exception as exc:
            return f"[DOCX extraction failed: {exc}]"

    # XLSX
    if mime == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" or name.lower().endswith(".xlsx"):
        try:
            import openpyxl
            from io import BytesIO
            wb = openpyxl.load_workbook(BytesIO(raw), data_only=True)
            rows = []
            for sheet in wb.worksheets[:1]:
                for row in sheet.iter_rows(values_only=True):
                    rows.append("\t".join(str(c) if c is not None else "" for c in row))
            text = "\n".join(rows)
            return text[:50000] + ("\n... (truncated)" if len(text) > 50000 else "")
        except Exception:
            pass

    # Plain text / code
    if mime.startswith("text/") or mime in ("application/json", "application/javascript", "application/x-yaml"):
        try:
            text = raw.decode("utf-8", errors="replace")
            return text[:50000] + ("\n... (truncated)" if len(text) > 50000 else "")
        except Exception:
            pass

    return None


# ── max_tokens 策略 ─────────────────────────────────────────────────

def _resolve_max_tokens(model: str) -> int | None:
    """返回 max_tokens 上限，优先用户配置。未配置则返回 None。"""
    try:
        from hermes_constants import get_hermes_home
        home = get_hermes_home()
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
    return None


# ── Vision helpers ──────────────────────────────────────────────────

# Providers/models known to support vision (image_url in messages).
# Aggressive strategy: unknown models default to True (try first, fallback on error).
_VISION_KNOWN_GOOD = {
    # OpenAI family
    "gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-4-vision-preview",
    "gpt-5", "gpt-5.4-mini", "gpt-5.4-nano",
    # Google
    "gemini-2", "gemini-2.5", "gemini-3", "gemini-pro",
    # Anthropic
    "claude-3", "claude-3.5", "claude-4", "claude-sonnet", "claude-opus",
    # Chinese providers
    "qwen-vl", "qwen2-vl", "qwen-vl-max",
    "deepseek",  # DeepSeek V3+ supports vision via OpenAI-compatible API
    "step-1v", "step-2",
    "yi-vision",
    "hunyuan",  # Tencent
    # Agnes
    "agnes",
    # Xiaomi
    "mimo",
}
_VISION_KNOWN_BAD = {
    # Models that definitively do NOT support vision
    "gpt-3.5-turbo", "text-davinci", "code-", "codex",
    "llama-2", "llama-3.0", "mistral-7b", "mixtral-8x7b",
}


def _model_supports_vision(model: str) -> bool:
    """Check if a model likely supports vision (image_url).

    Aggressive strategy: default True for unknown models.
    Known-good prefixes are whitelisted; known-bad are blacklisted.
    Runtime errors from the API will naturally fallback (caller's responsibility).
    """
    model_lower = model.lower()
    for prefix in _VISION_KNOWN_BAD:
        if model_lower.startswith(prefix):
            return False
    for prefix in _VISION_KNOWN_GOOD:
        if model_lower.startswith(prefix):
            return True
    # Unknown model → aggressive: assume it supports vision
    return True


def _decode_data_url(data_url: str) -> tuple[str, bytes] | None:
    """Parse a data URL (data:mime;base64,...) into (mime, raw_bytes).

    Returns (mime_type, decoded_bytes) or None if parsing fails.
    """
    try:
        if not data_url.startswith("data:"):
            return None
        header, b64_part = data_url.split(",", 1)
        # header = "data:image/png;base64"
        mime = header.split(";")[0].split(":", 1)[1] if ":" in header else "application/octet-stream"
        raw = b64mod.b64decode(b64_part)
        return mime, raw
    except Exception:
        return None


def _strip_markdown_images(text: str) -> str:
    """Remove markdown image embeds (base64 data URLs) from text.

    Replaces ![alt](data:...) with [图片] to save tokens.
    The actual image data is already in attachments.
    """
    import re
    return re.sub(r'!\[.*?\]\(data:image[^)]+\)', '[图片]', text)


# ── @file reference expansion ───────────────────────────────────────

import re as _re_module

# Match @path/to/file.ext (not email @addresses)
# Must be preceded by start-of-string, whitespace, or newline
# Path chars: alphanumeric, /, -, _, ., +
_FILE_REF_PATTERN = _re_module.compile(r'(?:^|(?<=\s))@([\w./\-+]+(?:\.[\w]+)+)')

# Max file size to inline (50KB text)
_MAX_FILE_INLINE = 50 * 1024
# Max total expansion across all references in one message
_MAX_TOTAL_EXPANSION = 200 * 1024


def _expand_file_references(text: str) -> str:
    """Expand @file references in user message text.

    Scans for @path/to/file.ext patterns and inlines file contents
    as fenced code blocks. Skips files that don't exist or are too large.

    Examples:
      @src/main.py → ```python\n<file contents>\n```
      @README.md → ```markdown\n<file contents>\n```
    """
    if '@' not in text:
        return text

    cwd = os.getcwd()
    total_expanded = 0
   
    def _replace_match(match):
        nonlocal total_expanded
        rel_path = match.group(1)
       
        # Skip if looks like an email or version (e.g. @user, @2.0.0)
        if '/' not in rel_path and '.' not in rel_path:
            return match.group(0)
       
        file_path = os.path.join(cwd, rel_path)
       
        # Security: prevent path traversal outside cwd
        try:
            real_path = os.path.realpath(file_path)
            if not real_path.startswith(os.path.realpath(cwd)):
                return match.group(0)
        except Exception:
            return match.group(0)
       
        if not os.path.isfile(real_path):
            return match.group(0)
       
        try:
            file_size = os.path.getsize(real_path)
            if file_size > _MAX_FILE_INLINE:
                return f'@{rel_path} [文件过大: {file_size // 1024}KB, 超过50KB限制]'
            if total_expanded + file_size > _MAX_TOTAL_EXPANSION:
                return f'@{rel_path} [总引用超过200KB限制]'
           
            with open(real_path, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
           
            total_expanded += file_size
           
            # Detect language from extension
            ext = os.path.splitext(rel_path)[1].lstrip('.')
            lang_map = {
                'py': 'python', 'js': 'javascript', 'ts': 'typescript',
                'jsx': 'jsx', 'tsx': 'tsx', 'vue': 'vue', 'go': 'go',
                'rs': 'rust', 'java': 'java', 'kt': 'kotlin',
                'c': 'c', 'cpp': 'cpp', 'h': 'c', 'hpp': 'cpp',
                'cs': 'csharp', 'rb': 'ruby', 'php': 'php',
                'swift': 'swift', 'sh': 'bash', 'bash': 'bash',
                'yml': 'yaml', 'yaml': 'yaml', 'json': 'json',
                'html': 'html', 'css': 'css', 'scss': 'scss',
                'sql': 'sql', 'md': 'markdown', 'xml': 'xml',
            }
            lang = lang_map.get(ext, '')
           
            return f'`{rel_path}`:\n```{lang}\n{content}\n```'
        except Exception:
            return match.group(0)
   
    return _FILE_REF_PATTERN.sub(_replace_match, text)


# ── Pydantic models ─────────────────────────────────────────────────

class ChatMessage(BaseModel):
    role: str
    content: str | list  # str for text, list for multimodal (OpenAI format)


class AttachmentData(BaseModel):
    name: str
    type: str  # "image" or "file"
    data: str  # base64 encoded
    mime: str = ""
    size: int = 0


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    model: str | None = None
    provider: str | None = None
    stream: bool = True
    attachments: list[AttachmentData] | None = None
    wechat_openid: str | None = None
    session_id: str | None = None  # For agent caching
    reasoning_effort: str | None = None  # none / low / medium / high


# ── Attachment validation ────────────────────────────────────────────

def _validate_attachments(attachments: list[AttachmentData] | None) -> tuple[list[AttachmentData], str | None]:
    """Validate attachment MIME types and sizes."""
    if not attachments:
        return [], None

    total_size = 0
    filtered = []
    errors = []

    for att in attachments:
        if att.size > _MAX_SINGLE_ATTACHMENT_SIZE:
            errors.append(f"{att.name}: 单文件超过 20MB 限制")
            continue

        total_size += att.size

        mime = (att.mime or "application/octet-stream").lower()
        if mime not in _ALLOWED_MIME_TYPES:
            if not mime.startswith("image/"):
                errors.append(f"{att.name}: 不支持的文件类型 ({mime})")
                continue

        filtered.append(att)

    if total_size > _MAX_ATTACHMENT_SIZE:
        errors.append(f"附件总大小 {total_size / 1024 / 1024:.1f}MB 超过 50MB 限制")

    if errors:
        return filtered, "; ".join(errors)
    return filtered, None


# ── Credential resolution ────────────────────────────────────────────

def _load_toolsets_for_web() -> list[str] | None:
    """Load enabled toolsets for web UI agent."""
    import os
    # Check environment variable first
    env_toolsets = os.environ.get("HERMES_TUI_TOOLSETS", "")
    if env_toolsets:
        return [t.strip() for t in env_toolsets.split(",") if t.strip()]
    # Fall back to config — platform_toolsets.web > toolsets > default
    try:
        from hermes_constants import get_hermes_home
        import yaml
        cfg_path = os.path.join(get_hermes_home(), "config.yaml")
        if os.path.exists(cfg_path):
            with open(cfg_path) as f:
                cfg = yaml.safe_load(f) or {}
            # platform-specific toolsets take priority
            platform_ts = cfg.get("platform_toolsets", {})
            if isinstance(platform_ts, dict) and "web" in platform_ts:
                return platform_ts["web"]
            # fallback: Web UI always gets a rich toolset. If the user has
            # configured a custom toolsets list that's more substantial than
            # the bare hermes-cli default, honour it; otherwise use Web defaults.
            toolsets = cfg.get("toolsets")
            if toolsets:
                ts_list = toolsets if isinstance(toolsets, list) else [toolsets]
                if len(ts_list) == 1 and ts_list[0] == "hermes-cli":
                    return ["file", "code_execution", "browser", "web", "memory", "todo", "image_gen", "session_search", "scholarforge", "hermes-cli"]
                return ts_list
    except Exception:
        pass
    return ["file", "code_execution", "browser", "web", "memory", "todo", "image_gen", "session_search", "scholarforge", "hermes-cli"]


def _get_chat_credentials() -> tuple[str, str, str]:
    """Return (base_url, api_key, default_model) from config.yaml + .env."""
    from hermes_constants import get_hermes_home

    home = get_hermes_home()
    cfg_path = home / "config.yaml"
    env_path = home / ".env"

    base_url = ""
    default_model = ""
    api_key = ""
    provider = ""

    if cfg_path.exists():
        with open(cfg_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        m = cfg.get("model", {})
        base_url = m.get("base_url", "")
        default_model = m.get("default", "")
        provider = m.get("provider", "")

    prov_def = PROVIDERS.get(provider) or {}
    env_var_name = prov_def.get("env_key") or "OPENAI_API_KEY"
    if not base_url and provider:
        base_url = prov_def.get("base_url", "")

    if env_path.exists():
        env_content = env_path.read_text(encoding="utf-8")
        for line in env_content.splitlines():
            line = line.strip()
            if env_var_name and line.startswith(f"{env_var_name}="):
                api_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                break

    return base_url, api_key, default_model


# ── Provider routing maps ────────────────────────────────────────────

MODEL_PROVIDER_MAP = {
    "deepseek-chat": "deepseek",
    "deepseek-reasoner": "deepseek",
    "deepseek-v4-flash": "deepseek",
    "agnes-": "agnes",
    "openrouter/": "openrouter",
    "qwen2.5:": "ollama",
    "qwen3.6:": "ollama",
    "llama3:": "ollama",
    "mistral:": "ollama",
    "gemma:": "ollama",
    "gpt-4o": "vbit",
    "claude-opus-4": "vbit",
    "gpt-4": "openai",
    "gpt-3.5": "openai",
    "claude-": "anthropic",
}

# Unified provider registry (single source of truth)
PROVIDERS = {
    "deepseek": {"env_key": "DEEPSEEK_API_KEY", "base_url": "https://api.deepseek.com/v1", "cloud": True, "free": False, "recommended": True},
    "openai": {"env_key": "OPENAI_API_KEY", "base_url": "https://api.openai.com/v1", "cloud": True, "free": False, "recommended": False},
    "anthropic": {"env_key": "ANTHROPIC_API_KEY", "base_url": "https://api.anthropic.com/v1", "cloud": True, "free": False, "recommended": False},
    "gemini": {"env_key": "GEMINI_API_KEY", "base_url": "https://generativelanguage.googleapis.com/v1beta", "cloud": True, "free": False, "recommended": False},
    "openrouter": {"env_key": "OPENROUTER_API_KEY", "base_url": "https://openrouter.ai/api/v1", "cloud": True, "free": False, "recommended": False},
    "vbit": {"env_key": "VBIT_API_KEY", "base_url": "https://api.vbit.top/v1", "cloud": True, "free": True, "recommended": True},
    "alibaba": {"env_key": "QWEN_API_KEY", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "cloud": True, "free": False, "recommended": False},
    "qwen": {"env_key": "QWEN_API_KEY", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "cloud": True, "free": False, "recommended": False},
    "zhipu": {"env_key": "ZHIPU_API_KEY", "base_url": "https://open.bigmodel.cn/api/paas/v4", "cloud": True, "free": False, "recommended": False},
    "doubao": {"env_key": "DOUBAO_API_KEY", "base_url": "https://ark.cn-beijing.volces.com/api/v3", "cloud": True, "free": False, "recommended": False},
    "moonshot": {"env_key": "MOONSHOT_API_KEY", "base_url": "https://api.moonshot.cn/v1", "cloud": True, "free": False, "recommended": False},
    "baichuan": {"env_key": "BAICHUAN_API_KEY", "base_url": "https://api.baichuan-ai.com/v1", "cloud": True, "free": False, "recommended": False},
    "yi": {"env_key": "YI_API_KEY", "base_url": "https://api.lingyiwanwu.com/v1", "cloud": True, "free": False, "recommended": False},
    "spark": {"env_key": "SPARK_API_KEY", "base_url": "https://spark-api-open.xf-yun.com/v1", "cloud": True, "free": False, "recommended": False},
    "siliconflow": {"env_key": "SILICONFLOW_API_KEY", "base_url": "https://api.siliconflow.cn/v1", "cloud": True, "free": False, "recommended": False},
    "mistral": {"env_key": "MISTRAL_API_KEY", "base_url": "https://api.mistral.ai/v1", "cloud": True, "free": False, "recommended": False},
    "cohere": {"env_key": "COHERE_API_KEY", "base_url": "https://api.cohere.ai/v1", "cloud": True, "free": False, "recommended": False},
    "custom": {"env_key": "CUSTOM_API_KEY", "base_url": "", "cloud": False, "free": False, "recommended": False},
    "xiaomi": {"env_key": "XIAOMI_API_KEY", "base_url": "https://api.xiaomimimo.com/v1", "cloud": True, "free": False, "recommended": True},
    "ant-ling": {"env_key": "ANT_LING_API_KEY", "base_url": "https://api.ant-ling.com/v1", "cloud": True, "free": False, "recommended": False},
    "minimax": {"env_key": "MINIMAX_API_KEY", "base_url": "https://api.minimax.chat/v1", "cloud": True, "free": False, "recommended": False},
    "baidu": {"env_key": "BAIDU_API_KEY", "base_url": "https://qianfan.baidubce.com/v2", "cloud": True, "free": False, "recommended": False},
    "xinghuo": {"env_key": "XINGHUO_API_KEY", "base_url": "https://spark-api.xf-yun.com/v1", "cloud": True, "free": False, "recommended": False},
    "stepfun": {"env_key": "STEPFUN_API_KEY", "base_url": "https://api.stepfun.com/v1", "cloud": True, "free": False, "recommended": False},
    "groq": {"env_key": "GROQ_API_KEY", "base_url": "https://api.groq.com/openai/v1", "cloud": True, "free": False, "recommended": False},
    "together": {"env_key": "TOGETHER_API_KEY", "base_url": "https://api.together.xyz/v1", "cloud": True, "free": False, "recommended": False},
    "agnes": {"env_key": "AGNES_API_KEY", "base_url": "https://apihub.agnes-ai.com/v1", "cloud": True, "free": True, "recommended": True},
    "ollama": {"env_key": None, "base_url": "http://localhost:11434/v1", "cloud": False, "free": True, "recommended": True},
}


def _resolve_model_provider(model: str, explicit_provider: str | None = None) -> tuple:
    """Resolve model name to (provider, base_url, api_key, actual_model)."""
    from hermes_constants import get_hermes_home
    home = get_hermes_home()
    env_path = home / ".env"

    provider = explicit_provider or ""

    if not provider:
        for prefix, prov in MODEL_PROVIDER_MAP.items():
            if model.startswith(prefix):
                provider = prov
                break

    # Normalize common provider aliases
    provider_aliases = {
        "通义千问": "qwen", "vbit.top": "vbit", "Ollama (本地)": "ollama",
        "OpenRouter": "openrouter", "DeepSeek": "deepseek", "小米 MiMo": "xiaomi", "小米 mimo": "xiaomi",
        "自定义提供商": "custom", "蚂蚁百灵": "ant-ling", "MiniMax": "minimax",
        "百度文心": "baidu", "讯飞星火": "xinghuo", "阶跃星辰": "stepfun",
        "零一万物": "yi", "百川智能": "baichuan", "Groq (极速推理)": "groq",
        "Together AI": "together", "Anthropic Claude": "anthropic", "Google Gemini": "gemini",
        "Agnes AI": "agnes",
    }
    provider = provider.lower(); provider = provider_aliases.get(provider, provider)

    if not provider:
        cfg_path = home / "config.yaml"
        if cfg_path.exists():
            with open(cfg_path, encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            provider = cfg.get("model", {}).get("provider", "agnes")
        else:
            provider = "agnes"

    # Get base_url
    base_url = ""
    cfg_path = home / "config.yaml"
    cfg = {}
    if cfg_path.exists():
        with open(cfg_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        base_url = cfg.get("providers", {}).get(provider, {}).get("base_url", "")
    if not base_url:
        base_url = (PROVIDERS.get(provider) or {}).get("base_url", "")

    # Get api_key
    api_key = ""
    env_var = (PROVIDERS.get(provider) or {}).get("env_key")
    if env_var and env_path.exists():
        env_content = env_path.read_text(encoding="utf-8")
        for line in env_content.splitlines():
            line = line.strip()
            if line.startswith(f"{env_var}="):
                api_key = line.split("=", 1)[1].strip().strip('"').strip("'")

    if not api_key:
        api_key = cfg.get("providers", {}).get(provider, {}).get("api_key", "")

    if not api_key and env_path.exists():
        custom_env_key = f"{provider.upper().replace('-', '_')}_API_KEY"
        env_content = env_path.read_text(encoding="utf-8")
        for line in env_content.splitlines():
            line = line.strip()
            if line.startswith(f"{custom_env_key}="):
                api_key = line.split("=", 1)[1].strip().strip('"').strip("'")

    if provider == "ollama" and not api_key:
        api_key = "ollama"

    # Strip provider prefix from model name
    actual_model = model
    if provider == "openrouter" and model.startswith("openrouter/"):
        actual_model = model[len("openrouter/"):]
    elif provider == "vbit" and model.startswith("vbit/"):
        actual_model = model[len("vbit/"):]
    elif "/" in model and provider not in ("ollama", "openrouter", "vbit"):
        actual_model = model.split("/", 1)[1]

    return provider, base_url, api_key, actual_model


# ── Trial token wrapper ──────────────────────────────────────────────

async def claim_trial_token_wrapper(wechat_openid: str) -> dict:
    """Internal: Claim trial token — delegates to quota blueprint."""
    from hermes_cli.blueprints.quota import _claim_trial_token
    return await _claim_trial_token(wechat_openid)


def _report_quota(wechat_openid: str, total_tokens: int, mode: str = ""):
    """Report token usage to vbit backend for quota deduction."""
    if not wechat_openid or total_tokens <= 0:
        return
    secret = os.environ.get("VERMES_INTERNAL_SECRET", "")
    if not secret:
        _log.warning("[Quota] VERMES_INTERNAL_SECRET 未设置，跳过积分上报")
        return
    try:
        import httpx
        points = max(1, total_tokens // 1000)
        httpx.post(
            "https://api.vbit.top/api/quota/spend",
            json={"wechat_openid": wechat_openid, "quota_consumed": points * 720},
            headers={"X-Vermes-Secret": secret},
            timeout=5, verify=True,
        )
        _log.info(f"[Quota] {mode}上报: {points}积分 ({total_tokens} tokens)")
    except Exception as e:
        _log.warning(f"[Quota] {mode}上报失败: {e}")


# ── Route handlers ───────────────────────────────────────────────────

async def chat_completions(req: ChatRequest):
    """Agent-powered chat: uses AIAgent with tool calling capabilities."""
    from run_agent import AIAgent

    requested_model = req.model or "agnes-2.0-flash"
    provider, base_url, api_key, model = _resolve_model_provider(requested_model, req.provider)

    if not base_url:
        raise HTTPException(status_code=500, detail=f"No base_url found for provider '{provider}'. Check config.yaml.")

    # Validate attachments
    validated_attachments, att_error = _validate_attachments(req.attachments)
    if att_error:
        logger.info(f"[Attachment validation] {att_error}")
    req.attachments = validated_attachments

    # Auto-claim trial token when no API key configured
    if (not api_key or api_key.startswith("unknown")) and provider != "ollama":
        if api_key and api_key.startswith("unknown"):
            api_key = ""
            remove_env_value("VBIT_API_KEY")
        env_hint = (PROVIDERS.get(provider) or {}).get("env_key", "API_KEY")
        if provider in ("vbit", "agnes"):
            try:
                from hermes_constants import get_hermes_home
                env_path = get_hermes_home() / ".env"
                if env_path.exists():
                    for line in env_path.read_text(encoding="utf-8").splitlines():
                        line = line.strip()
                        if line.startswith("VBIT_API_KEY=") or line.startswith("VBIT_API_KEY ="):
                            existing = line.split("=", 1)[1].strip().strip('"').strip("'")
                            if existing:
                                api_key = existing
                                break
                if not api_key:
                    wechat_openid = req.wechat_openid or os.environ.get("VERMES_WECHAT_OPENID", "")
                    if wechat_openid:
                        claim_result = await claim_trial_token_wrapper(wechat_openid)
                        if claim_result.get("success") and claim_result.get("token"):
                            token = claim_result["token"]
                            env_content = ""
                            if env_path.exists():
                                env_content = env_path.read_text(encoding="utf-8")
                            lines = env_content.splitlines()
                            found = False
                            new_lines = []
                            for line in lines:
                                if line.startswith("VBIT_API_KEY=") or line.startswith("VBIT_API_KEY ="):
                                    new_lines.append(f"VBIT_API_KEY={token}")
                                    found = True
                                else:
                                    new_lines.append(line)
                            if not found:
                                new_lines.append(f"VBIT_API_KEY={token}")
                            env_path.write_text("\n".join(new_lines) + "\n")
                            api_key = token
                            # agnes provider 用 vbit 免费通道时，切到 vbit base_url
                            if provider == "agnes":
                                base_url = PROVIDERS["vbit"]["base_url"]
                            _log.info(f"[Claim] 微信用户自动领取 token 成功，已写入 .env")
                        else:
                            err_msg = claim_result.get("error", "领取失败")
                            raise HTTPException(status_code=402, detail=f"免费体验Token领取失败: {err_msg}. 请重新微信扫码登录或配置自己的API Key。")
                    else:
                        raise HTTPException(status_code=402, detail="请先微信扫码登录后再使用免费体验，或在设置页配置自己的API Key。")
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(status_code=503, detail=f"自动领取Token失败: {str(e)}. 请在设置页配置API Key。")
        else:
            raise HTTPException(status_code=500, detail=f"No API key found for provider '{provider}'. Set {env_hint} in .env 或在设置页添加Key。")

    # Build conversation messages — strip inline base64 images from all messages
    # (actual image data is in attachments, markdown embeds waste tokens)
    # Also expand @file references in user messages (e.g. @src/main.py)
    conversation_history = []
    for m in req.messages:
        content = m.content
        if isinstance(content, str):
            content = _strip_markdown_images(content)
            if m.role == "user":
                content = _expand_file_references(content)
        conversation_history.append({"role": m.role, "content": content})

    # ── Vision capability check ──
    supports_vision = _model_supports_vision(model or "")

    # Process attachments: inject into the last user message
    if req.attachments:
        last_user_idx = -1
        for i, m in enumerate(conversation_history):
            if m["role"] == "user":
                last_user_idx = i
        if last_user_idx >= 0:
            parts = []
            existing_content = conversation_history[last_user_idx]["content"]
            if isinstance(existing_content, str) and existing_content:
                parts.append({"type": "text", "text": existing_content})
            for att in req.attachments:
                if att.type == "image":
                    if supports_vision:
                        data_url = f"data:{att.mime};base64,{att.data}"
                        parts.append({"type": "image_url", "image_url": {"url": data_url}})
                    else:
                        # Model doesn't support vision — add text note instead
                        parts.append({"type": "text", "text": f"[用户发送了图片 {att.name}，但当前模型不支持图片理解。请切换到支持视觉的模型（如 GPT-4o、Gemini、DeepSeek）以查看图片。]"})
                else:
                    extracted = _extract_file_text(att.name, att.mime, att.data)
                    if extracted:
                        parts.append({"type": "text", "text": f"\U0001f4ce {att.name}:\n```\n{extracted}\n```"})
                    else:
                        parts.append({"type": "text", "text": f"\U0001f4ce {att.name}: (binary file, {att.size} bytes)"})
            if parts:
                conversation_history[last_user_idx]["content"] = parts

    # Extract the last user message
    user_message = ""
    if req.attachments and conversation_history:
        last_content = conversation_history[-1].get("content", "")
        if isinstance(last_content, list):
            user_message = last_content
    if not isinstance(user_message, list):
        for m in reversed(req.messages):
            if m.role == "user":
                content = m.content
                if isinstance(content, list):
                    text_parts = [p.get("text", "") for p in content if p.get("type") == "text"]
                    user_message = "\n".join(text_parts) or "[image/document attached]"
                else:
                    user_message = content
                break

    # All messages go through Agent mode — no proxy fallback
    agent = None

    # Extract session ID for agent caching
    _session_id = req.session_id or "default"

    # Agent cache: reuse agent instance per session for persistence
    _cache_key = f"{provider}:{model}:{_session_id}"
    agent = _agent_cache.get(_cache_key)

    if agent is None:
        # ── 跨会话涌现：注入进化上下文 + 行为引导（提炼至 evolution_manager）──
            _evo_prompt = ""
            try:
                from agent.evolution_manager import build_evolution_prompt
                _evo_prompt = build_evolution_prompt() or ""
            except Exception:
                pass
            # ── 推理配置：请求参数优先，其次 config ──────────
            _reasoning_config = None
            _effort = req.reasoning_effort
            if not _effort:
                try:
                    _cfg = load_config()
                    _effort = _cfg.get("agent", {}).get("reasoning_effort", "")
                except Exception:
                    pass
            if _effort:
                _reasoning_config = {"effort": _effort}

            # ── 读取 config 中的 max_iterations/disabled_toolsets ──
            _max_iterations = 1000
            _disabled_toolsets = None
            try:
                _cfg = _cfg or load_config()
                _agent_cfg = _cfg.get("agent", {}) or {}
                _max_turns = _agent_cfg.get("max_turns") or _agent_cfg.get("max_iterations")
                if _max_turns and isinstance(_max_turns, (int, float)) and int(_max_turns) > 0:
                    _max_iterations = int(_max_turns)
                _disabled = _agent_cfg.get("disabled_toolsets") or []
                if _disabled and isinstance(_disabled, list):
                    _disabled_toolsets = _disabled
            except Exception:
                pass

            agent = AIAgent(
                base_url=base_url,
                api_key=api_key,
                provider=provider,
                model=model,
                max_iterations=_max_iterations,
                quiet_mode=True,
                verbose_logging=False,
                platform="web",
                enabled_toolsets=_load_toolsets_for_web(),
                disabled_toolsets=_disabled_toolsets,
                ephemeral_system_prompt=_evo_prompt or None,
                reasoning_config=_reasoning_config,
            )
            _agent_cache.put(_cache_key, agent)
            _log.info(f"[Agent] Created new agent for session {_session_id}")
    else:
        _log.info(f"[Agent] Reusing cached agent for session {_session_id}")

    if agent:
        from tools.approval import enable_session_yolo, set_current_session_key, register_gateway_notify, unregister_gateway_notify
        _gui_sk = "gui-" + (getattr(agent, "session_id", "") or "default")
        set_current_session_key(_gui_sk)

        # ── 审批模式：默认 YOLO（保持现有体验），可在 Settings 关闭 ──
        _yolo_enabled = True
        try:
            _cfg = load_config()
            _yolo_enabled = _cfg.get("approvals", {}).get("yolo_default", True)
        except Exception:
            pass

        if _yolo_enabled:
            enable_session_yolo(_gui_sk)
        else:
            # 注册 gateway 审批回调 → 通过 SSE 推送审批请求到前端
            async def _notify_approval(approval_data: dict):
                try:
                    await _delta_queue.put({
                        "type": "approval_request",
                        "data": approval_data,
                    })
                except Exception:
                    pass
            def _sync_notify(approval_data: dict):
                import asyncio as _aio
                try:
                    _loop = asyncio.get_event_loop()
                    if _loop.is_running():
                        _loop.call_soon_threadsafe(
                            _loop.create_task, _notify_approval(approval_data)
                        )
                    else:
                        _loop.run_until_complete(_notify_approval(approval_data))
                except Exception:
                    pass
            register_gateway_notify(_gui_sk, _sync_notify)


    if req.stream:
        # Streaming mode
        _stream_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        # Agent mode streaming
        from hermes_cli.blueprints.state import _active_streams

        _delta_queue: asyncio.Queue = asyncio.Queue()
        _agent_done = asyncio.Event()
        _stream_id = secrets.token_urlsafe(16)
        _cancel_event = asyncio.Event()
        _tool_ids = {}  # tool_name → tool_call_id 配对
        _active_streams[_stream_id] = _cancel_event

        def status_callback(event_type: str, message: str):
            """Route lifecycle/warn events from AIAgent to SSE stream."""
            event = {
                "type": "lifecycle" if event_type == "lifecycle" else "warn",
                "message": message,
            }
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop and loop.is_running():
                loop.call_soon_threadsafe(_delta_queue.put_nowait, event)
            else:
                _delta_queue.put_nowait(event)

        def stream_callback(delta: str):
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if delta is not None:
                _log.info(f"[Stream] DELTA: {repr(delta[:60])}")
                if loop and loop.is_running():
                    loop.call_soon_threadsafe(_delta_queue.put_nowait, delta)
                else:
                    _delta_queue.put_nowait(delta)
            else:
                _log.info(f"[Stream] Turn boundary (delta=None), agent still running")

        def tool_progress_handler(event_type: str, tool_name: str, preview: str, args: dict, **kwargs):
            _log.info(f"[ToolEvent] {event_type}: {tool_name}")
            if event_type == "tool.started":
                _tool_id = secrets.token_urlsafe(8)
                _tool_ids[tool_name] = _tool_id
                event = {
                    "type": "tool_start",
                    "tool_call_id": _tool_id,
                    "tool_name": tool_name,
                    "arguments": args or {},
                }
            else:
                event = {
                    "type": "tool_end",
                    "tool_call_id": _tool_ids.pop(tool_name, secrets.token_urlsafe(8)),
                    "tool_name": tool_name,
                    "duration": kwargs.get("duration", 0),
                    "is_error": kwargs.get("is_error", False),
                    "result_preview": preview or "",
                }
                if tool_name == "todo" and preview:
                    try:
                        import json as _json
                        todo_data = _json.loads(preview)
                        todo_event = {
                            "type": "todo_update",
                            "todos": todo_data.get("todos", []),
                            "summary": todo_data.get("summary", {}),
                        }
                        try:
                            _loop = asyncio.get_running_loop()
                        except RuntimeError:
                            _loop = None
                        if _loop and _loop.is_running():
                            _loop.call_soon_threadsafe(_delta_queue.put_nowait, todo_event)
                        else:
                            _delta_queue.put_nowait(todo_event)
                    except Exception:
                        pass
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop and loop.is_running():
                loop.call_soon_threadsafe(_delta_queue.put_nowait, event)
            else:
                _delta_queue.put_nowait(event)

        def thinking_handler(iteration: int, prev_tools: list):
            _log.info(f"[ThinkEvent] iteration={iteration}, prev_tools={[t.get('name') for t in (prev_tools or [])]}")
            tool_names = [t.get("name", "?") for t in (prev_tools or [])]
            msg = f"🤔 推理第 {iteration} 步"
            if tool_names:
                msg += f" — 已用: {', '.join(tool_names)}"
            event = {
                "type": "thinking",
                "iteration": iteration,
                "message": msg
            }
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop and loop.is_running():
                loop.call_soon_threadsafe(_delta_queue.put_nowait, event)
            else:
                _delta_queue.put_nowait(event)

        def evolution_event_handler(message: str, tool_name: str, is_error: bool, duration: float):
            """Route evolution events (achievements, advice) to SSE stream."""
            event = {
                "type": "evolution",
                "message": message,
                "tool_name": tool_name,
                "is_error": is_error,
                "duration": round(duration, 2),
            }
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop and loop.is_running():
                loop.call_soon_threadsafe(_delta_queue.put_nowait, event)
            else:
                _delta_queue.put_nowait(event)

        def run_sync():
            try:
                _log.info(f"[Stream] Agent starting, model={model}, provider={provider}, stream_id={_stream_id}")
                agent.stream_delta_callback = stream_callback
                agent.tool_progress_callback = tool_progress_handler
                agent.step_callback = thinking_handler
                agent.status_callback = status_callback
                agent.evolution_event_callback = evolution_event_handler
                _max_tokens = getattr(req, 'max_tokens', None) or _resolve_max_tokens(model)
                agent.max_tokens = _max_tokens
                result = agent.run_conversation(
                    user_message=user_message,
                    conversation_history=conversation_history[:-1] if len(conversation_history) > 1 else None,
                    stream_callback=None,
                )
                _log.info(f"[Stream] Agent done, result keys={list(result.keys()) if result else 'None'}")
                return result
            except Exception as e:
                _log.error(f"[Stream] Agent error: {e}")
                raise
            finally:
                _agent_done.set()

        async def stream_generator():
            try:
                yield f'data: {json.dumps({"type": "stream_start", "stream_id": _stream_id})}\n\n'

                loop = asyncio.get_running_loop()
                # 使用独立的 executor，不依赖系统默认 executor
                # 系统默认 executor 可能被前一个失败的 agent 调用 shutdown 了
                import concurrent.futures
                _agent_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
                try:
                    agent_task = loop.run_in_executor(_agent_executor, run_sync)
                except RuntimeError:
                    # 如果 executor 也被 shutdown，重建一个
                    import concurrent.futures as _cf
                    _agent_executor = _cf.ThreadPoolExecutor(max_workers=1)
                    agent_task = loop.run_in_executor(_agent_executor, run_sync)

                while not _agent_done.is_set() or not _delta_queue.empty():
                    if _cancel_event.is_set():
                        _log.info(f"[Stream] Frontend requested stop, stream_id={_stream_id}")
                        agent._interrupt_requested = True
                        agent_task.cancel()
                        break

                    try:
                        delta = await asyncio.wait_for(_delta_queue.get(), timeout=0.5)
                    except asyncio.TimeoutError:
                        if agent_task.done():
                            exc = agent_task.exception()
                            if exc:
                                _log.error(f"[Stream] Agent error: {exc}")
                                yield f'data: {json.dumps({"error": {"message": str(exc), "type": "agent_error", "code": 500}})}\n\n'
                                yield "data: [DONE]\n\n"
                                return
                            if _delta_queue.empty():
                                _agent_done.set()
                                break
                        continue

                    if isinstance(delta, dict):
                        yield f"data: {json.dumps(delta)}\n\n"
                    else:
                        chunk = {
                            "id": "vermes-agent",
                            "object": "chat.completion.chunk",
                            "created": int(time.time()),
                            "model": model,
                            "choices": [{"index": 0, "delta": {"content": delta}, "finish_reason": None}]
                        }
                        yield f"data: {json.dumps(chunk)}\n\n"
            finally:
                _active_streams.pop(_stream_id, None)
                # Cancel agent if client disconnected mid-stream
                try:
                    if agent_task and not agent_task.done():
                        _log.info(f"[Stream] Client disconnected, cancelling agent, stream_id={_stream_id}")
                        agent._interrupt_requested = True
                        agent_task.cancel()
                except NameError:
                    pass  # agent_task not yet created

            # Wait for agent (v2.0.4 style — direct await, no timeout wrapper)
            try:
                _agent_result = await agent_task
            except Exception:
                _agent_result = {}

            # Report quota
            _wechat_openid = req.wechat_openid or os.environ.get("VERMES_WECHAT_OPENID", "")
            if _wechat_openid and _agent_result and provider in ("vbit", "agnes"):
                _total = _agent_result.get("total_tokens", 0)
                _report_quota(_wechat_openid, _total, "Agent流式")

            # Send [DONE]
            final_chunk = {
                "id": "vermes-agent",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]
            }
            yield f"data: {json.dumps(final_chunk)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(stream_generator(), media_type="text/event-stream")
    else:
        # Non-streaming mode
        _usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        def run_sync():
            return agent.run_conversation(
                user_message=user_message,
                conversation_history=conversation_history[:-1] if len(conversation_history) > 1 else None,
            )

        loop = asyncio.get_running_loop()
        import concurrent.futures as _cf
        _agent_executor = _cf.ThreadPoolExecutor(max_workers=1)
        try:
            result = await loop.run_in_executor(_agent_executor, run_sync)
        except RuntimeError:
            _agent_executor = _cf.ThreadPoolExecutor(max_workers=1)
            result = await loop.run_in_executor(_agent_executor, run_sync)

        final_response = result.get("final_response", "") if result else ""
        if not final_response and result and result.get("error"):
            final_response = f"Error: {result['error']}"
        _input_chars = sum(len(str(m.get("content", ""))) for m in conversation_history)
        _output_chars = len(final_response or "")
        _usage = {
            "prompt_tokens": max(1, _input_chars // 3),
            "completion_tokens": max(1, _output_chars // 3),
            "total_tokens": max(1, (_input_chars + _output_chars) // 3),
        }

        # Non-streaming quota reporting
        wechat_openid = req.wechat_openid or os.environ.get("VERMES_WECHAT_OPENID", "")
        if provider in ("vbit", "agnes"):
            _report_quota(wechat_openid, _usage["total_tokens"], "非流式")

        return {
            "id": "vermes-agent",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": final_response},
                "finish_reason": "stop"
            }],
            "usage": _usage
        }


async def chat_models():
    """Return available models from the configured LLM provider."""
    import httpx

    base_url, api_key, _ = _get_chat_credentials()
    if not base_url or not api_key:
        return {"data": []}

    url = f"{base_url.rstrip('/')}/models"
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                return resp.json()
    except Exception:
        pass
    return {"data": []}
class AgentRunRequest(BaseModel):
    task: str
    session_id: str = "api-default"
    model: str | None = None
    provider: str | None = None


async def agent_run(req: AgentRunRequest):
    """Run a task through the agent and return the result.

    Lightweight REST API for external systems (curl, cron, scripts, webhooks).
    Uses the same _agent_cache as chat, so evolution system works automatically.
    """
    from run_agent import AIAgent

    # Resolve model/provider
    requested_model = req.model or "agnes-2.0-flash"
    resolved_provider, base_url, api_key, resolved_model = _resolve_model_provider(requested_model, req.provider)
    if not base_url:
        return {"ok": False, "error": "No base_url found"}

    # ── 读取 config 同步 CLI 配置 ──────────────────────────────
    _cache_key = f"{resolved_provider}:{resolved_model}:{req.session_id}"
    _max_iterations = 1000
    _disabled_toolsets = None
    _reasoning_config = None
    try:
        _cfg = load_config()
        _agent_cfg = _cfg.get("agent", {}) or {}
        _max_turns = _agent_cfg.get("max_turns") or _agent_cfg.get("max_iterations")
        if _max_turns and isinstance(_max_turns, (int, float)) and int(_max_turns) > 0:
            _max_iterations = int(_max_turns)
        _disabled = _agent_cfg.get("disabled_toolsets") or []
        if _disabled and isinstance(_disabled, list):
            _disabled_toolsets = _disabled
        _effort = _agent_cfg.get("reasoning_effort", "")
        if _effort:
            _reasoning_config = {"effort": _effort}
    except Exception:
        pass

    # Inject evolution context
    _evo_prompt = ""
    try:
        from agent.evolution_manager import build_evolution_prompt
        _evo_prompt = build_evolution_prompt() or ""
    except Exception:
        pass

    agent = AIAgent(
                base_url=base_url,
                api_key=api_key,
                provider=resolved_provider,
                model=resolved_model,
                max_iterations=_max_iterations,
                quiet_mode=True,
                verbose_logging=False,
                platform="api",
                session_id=req.session_id,
                disabled_toolsets=_disabled_toolsets,
                ephemeral_system_prompt=_evo_prompt or None,
                reasoning_config=_reasoning_config,
            )
    _agent_cache.put(_cache_key, agent)
    _log.info(f"[Agent] Created API agent for session {req.session_id}")

    # Apply max_tokens from config
    try:
        _api_max_tokens = _resolve_max_tokens(resolved_model)
        if _api_max_tokens:
            agent.max_tokens = _api_max_tokens
    except Exception:
        pass

    # Run the task with timeout
    try:
        loop = asyncio.get_event_loop()
        result = await asyncio.wait_for(
            loop.run_in_executor(None, agent.run_conversation, req.task),
            timeout=120.0
        )
        return {
            "ok": True,
            "session_id": req.session_id,
            "response": result.get("final_response", ""),
        }
    except asyncio.TimeoutError:
        return {"ok": False, "error": "Task timed out (120s)"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

# ── Registration ─────────────────────────────────────────────────────


async def cache_metrics():
    """Return agent cache performance metrics."""
    return _agent_cache.get_metrics()


async def evolution_status():
    """Return evolution system status for the frontend."""
    try:
        from agent.evolution_manager import get_evolution_status, get_current_emotional_state

        status = get_evolution_status()
        if isinstance(status, dict):
            status["current_emotion"] = get_current_emotional_state()
        return status
    except Exception as e:
        return {"active": False, "error": str(e)}


async def evolution_achievements(limit: int = 10):
    """Return unlocked achievements for the frontend."""
    try:
        from agent.evolution_manager import get_evolution_status
        status = get_evolution_status()
        achievement_keys = status.get("achievements", []) if isinstance(status, dict) else []
        # Map achievement keys to display names
        achievement_map = {
            "10_records": {"id": "10_records", "name": "第一步", "description": "10 次工具调用记录"},
            "50_records": {"id": "50_records", "name": "初露锋芒", "description": "50 次工具调用记录"},
            "100_records": {"id": "100_records", "name": "百次积累", "description": "100 次工具调用记录"},
            "high_accuracy": {"id": "high_accuracy", "name": "精准执行", "description": f"成功率 {status.get('success_rate', 0):.0f}%"},
            "anti_pattern_learner": {"id": "anti_pattern_learner", "name": "善于学习", "description": "识别 3 个反模式"},
            "anti_pattern_master": {"id": "anti_pattern_master", "name": "经验丰富", "description": "识别 10 个反模式"},
            "first_error": {"id": "first_error", "name": "失败是成功之母", "description": "首次遇到错误"},
        }
        result = [achievement_map.get(k, {"id": k, "name": k, "description": k}) for k in achievement_keys]
        return result[:limit]
    except Exception as e:
        return []


async def delegate_status(task_id: str):
    """Return status of a background delegate task."""
    try:
        from tools.delegate_tool import get_background_task_status, list_background_tasks
        if task_id == "all":
            return {"tasks": list_background_tasks()}
        result = get_background_task_status(task_id)
        if result is None:
            return {"error": f"Task {task_id} not found", "status": "not_found"}
        return result
    except Exception as e:
        return {"error": str(e), "status": "error"}


async def evolution_dag(limit: int = 50):
    """Return DAG graph data: nodes and edges for visualization."""
    try:
        from agent.evolution_manager import get_self_model_db, _get_conn as _get_evo_conn
        evo_db = get_self_model_db()
        conn = _get_evo_conn(str(evo_db))
        c = conn.cursor()
        # Get relation edges grouped by type
        c.execute("""
            SELECT source_type, target_type, rel_type, COUNT(*) as cnt
            FROM relations
            GROUP BY source_type, target_type, rel_type
            ORDER BY cnt DESC
            LIMIT ?
        """, (limit,))
        edges = [
            {"source_type": r[0], "target_type": r[1], "rel_type": r[2], "count": r[3]}
            for r in c.fetchall()
        ]
        # Get top queried documents (RAG cross-DB)
        c.execute("""
            SELECT target_id, COUNT(*) as cnt
            FROM relations
            WHERE target_type='document' AND rel_type='queried'
            GROUP BY target_id
            ORDER BY cnt DESC
            LIMIT 10
        """)
        top_docs = [{"doc_id": r[0], "query_count": r[1]} for r in c.fetchall()]
        # Get top anti-patterns
        c.execute("""
            SELECT ap.id, ap.pattern, ap.frequency, ap.domain
            FROM anti_patterns ap
            ORDER BY ap.frequency DESC
            LIMIT 10
        """)
        anti_patterns = [
            {"id": r[0], "pattern": r[1], "frequency": r[2], "domain": r[3]}
            for r in c.fetchall()
        ]
        # Total counts
        c.execute("SELECT COUNT(*) FROM outcomes")
        total_outcomes = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM relations")
        total_edges = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM anti_patterns")
        total_ap = c.fetchone()[0]
        conn.close()
        return {
            "edges": edges,
            "top_documents": top_docs,
            "anti_patterns": anti_patterns,
            "totals": {
                "outcomes": total_outcomes,
                "edges": total_edges,
                "anti_patterns": total_ap,
            },
        }
    except Exception as e:
        return {"error": str(e), "edges": [], "top_documents": [], "anti_patterns": []}


async def rag_list_documents():
    """List all indexed RAG documents."""
    try:
        from agent.rag_provider import RAGProvider
        provider = RAGProvider()
        provider.initialize(session_id="api")
        docs = provider.list_documents()
        return {"documents": docs, "count": len(docs)}
    except Exception as e:
        return {"error": str(e), "documents": []}


async def rag_get_chunks(doc_id: int):
    """Get document chunks for preview."""
    try:
        from agent.rag_provider import RAGProvider
        provider = RAGProvider()
        provider.initialize(session_id="api")
        chunks = provider.get_document_chunks(doc_id)
        return {"chunks": chunks, "count": len(chunks)}
    except Exception as e:
        return {"error": str(e), "chunks": []}


async def rag_stats():
    """Return per-document usage statistics from Evolution DAG."""
    try:
        from agent.rag_provider import RAGProvider
        provider = RAGProvider()
        provider.initialize(session_id="api")
        stats = provider.get_document_stats()
        return {"documents": stats, "total": len(stats)}
    except Exception as e:
        return {"error": str(e), "documents": []}


async def rag_search(req: Request):
    """Search the knowledge base."""
    try:
        body = await req.json()
        query = body.get("query", "").strip()
        limit = min(max(body.get("limit", 5), 1), 20)
        if not query:
            return {"error": "query is required", "results": []}
        from agent.rag_provider import RAGProvider
        provider = RAGProvider()
        provider.initialize(session_id="api")
        results = provider.search(query, limit=limit)
        return {"results": results, "count": len(results), "query": query}
    except Exception as e:
        return {"error": str(e), "results": []}


async def rag_ingest(req: Request):
    """Ingest a file into the RAG knowledge base.
    
    Supports two modes:
    1. file_path: server-side file path
    2. filename + content: base64-encoded file content (for uploads)
    """
    try:
        body = await req.json()
        from agent.rag_provider import RAGProvider
        provider = RAGProvider()
        provider.initialize(session_id="api")
        
        # Mode 1: direct file path
        file_path = body.get("file_path", "")
        if file_path:
            result = provider.ingest_file(file_path)
            return result
        
        # Mode 2: filename + base64 content (upload)
        filename = body.get("filename", "")
        content_b64 = body.get("content", "")
        if filename and content_b64:
            import base64 as b64mod
            try:
                raw = b64mod.b64decode(content_b64)
            except Exception:
                return {"error": "Invalid base64 content"}
            ext = Path(filename).suffix.lower()
            binary_exts = {'.pdf', '.docx', '.xlsx', '.pptx'}
            if ext in binary_exts:
                # Binary document — extract text first
                from agent.rag_provider import _extract_text_from_bytes
                text = _extract_text_from_bytes(raw, ext)
                if not text.strip():
                    return {"error": f"无法从 {ext} 文件中提取文本，可能为扫描件或空文档"}
            else:
                # Plain text — try common encodings
                text = None
                for enc in ("utf-8", "gbk", "latin-1"):
                    try:
                        text = raw.decode(enc)
                        break
                    except Exception:
                        continue
                if text is None:
                    text = raw.decode("utf-8", errors="replace")
            result = provider.ingest_content(filename, text, body.get("file_type", ext))
            return result
        
        return {"error": "file_path or filename+content is required"}
    except Exception as e:
        return {"error": str(e)}


async def rag_delete(doc_id: int):
    """Delete a document from the RAG knowledge base."""
    try:
        from agent.rag_provider import RAGProvider
        provider = RAGProvider()
        provider.initialize(session_id="api")
        deleted = provider.delete_document(doc_id)
        return {"deleted": deleted, "doc_id": doc_id}
    except Exception as e:
        return {"error": str(e), "deleted": False}


async def mcp_list_servers():
    """List all configured MCP servers."""
    try:
        from hermes_cli.mcp_config import _get_mcp_servers
        servers = _get_mcp_servers()
        # Strip sensitive env values for listing
        safe = {}
        for name, cfg in servers.items():
            safe_cfg = dict(cfg)
            if "env" in safe_cfg and isinstance(safe_cfg["env"], dict):
                safe_cfg["env"] = {k: "***" if v else "" for k, v in safe_cfg["env"].items()}
            safe[name] = safe_cfg
        return {"servers": safe, "count": len(safe)}
    except Exception as e:
        return {"error": str(e), "servers": []}


async def mcp_add_server(request: Request):
    """Add or update an MCP server configuration."""
    try:
        body = await request.json()
        name = body.get("name", "").strip()
        if not name:
            return {"error": "name is required"}
        from hermes_cli.mcp_config import _save_mcp_server
        server_config = {"command": body.get("command", ""), "args": body.get("args", [])}
        if body.get("env"):
            server_config["env"] = body.get("env")
        _save_mcp_server(name, server_config)
        return {"saved": True, "name": name}
    except Exception as e:
        return {"error": str(e), "saved": False}


async def mcp_remove_server(name: str):
    """Remove an MCP server from configuration."""
    try:
        from hermes_cli.mcp_config import _remove_mcp_server
        removed = _remove_mcp_server(name)
        return {"removed": removed, "name": name}
    except Exception as e:
        return {"error": str(e), "removed": False}


async def mcp_test_server(request: Request):
    """Test connection to an MCP server."""
    try:
        body = await request.json()
        name = body.get("name", "").strip()
        if not name:
            return {"error": "name is required"}
        from hermes_cli.mcp_config import _get_mcp_servers, _probe_single_server
        servers = _get_mcp_servers()
        if name not in servers:
            return {"error": f"Server '{name}' not found"}
        ok, msg, tools = _probe_single_server(name, servers[name])
        return {"ok": ok, "message": msg, "tools": tools or []}
    except Exception as e:
        return {"error": str(e), "ok": False}


async def approve_command(request: Request):
    """Handle tool approval/deny from frontend.

    Body: { session_key, choice: "once"|"session"|"always"|"deny" }
    """
    try:
        body = await request.json()
        session_key = body.get("session_key", "")
        choice = body.get("choice", "deny")
        if not session_key:
            return {"ok": False, "error": "session_key required"}
        from tools.approval import resolve_gateway_approval
        resolved = resolve_gateway_approval(session_key, choice, resolve_all=False)
        return {"ok": True, "resolved": resolved}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def register_to(app):
    """Register chat routes on the FastAPI app."""
    app.add_api_route(
        "/api/chat/completions",
        chat_completions,
        methods=["POST"],
        name="chat_completions",
    )
    app.add_api_route(
        "/api/chat/models",
        chat_models,
        methods=["GET"],
        name="chat_models",
    )
    app.add_api_route(
        "/api/agent/run",
        agent_run,
        methods=["POST"],
        name="agent_run",
    )
    app.add_api_route(
        "/api/evolution/status",
        evolution_status,
        methods=["GET"],
        name="evolution_status",
    )
    app.add_api_route(
        "/api/evolution/achievements",
        evolution_achievements,
        methods=["GET"],
        name="evolution_achievements",
    )
    app.add_api_route(
        "/api/evolution/dag",
        evolution_dag,
        methods=["GET"],
        name="evolution_dag",
    )
    app.add_api_route(
        "/api/delegate/status/{task_id}",
        delegate_status,
        methods=["GET"],
        name="delegate_status",
    )
    app.add_api_route(
        "/api/rag/documents",
        rag_list_documents,
        methods=["GET"],
        name="rag_list_documents",
    )
    app.add_api_route(
        "/api/rag/ingest",
        rag_ingest,
        methods=["POST"],
        name="rag_ingest",
    )
    app.add_api_route(
        "/api/rag/delete/{doc_id}",
        rag_delete,
        methods=["DELETE"],
        name="rag_delete",
    )
    app.add_api_route(
        "/api/rag/chunks/{doc_id}",
        rag_get_chunks,
        methods=["GET"],
        name="rag_get_chunks",
    )
    app.add_api_route(
        "/api/rag/search",
        rag_search,
        methods=["POST"],
        name="rag_search",
    )
    app.add_api_route(
        "/api/rag/stats",
        rag_stats,
        methods=["GET"],
        name="rag_stats",
    )
    app.add_api_route(
        "/api/cache/metrics",
        cache_metrics,
        methods=["GET"],
        name="cache_metrics",
    )
    app.add_api_route(
        "/api/mcp/servers",
        mcp_list_servers,
        methods=["GET"],
        name="mcp_list_servers",
    )
    app.add_api_route(
        "/api/mcp/servers",
        mcp_add_server,
        methods=["POST"],
        name="mcp_add_server",
    )
    app.add_api_route(
        "/api/mcp/servers/{name}",
        mcp_remove_server,
        methods=["DELETE"],
        name="mcp_remove_server",
    )
    app.add_api_route(
        "/api/mcp/test",
        mcp_test_server,
        methods=["POST"],
        name="mcp_test_server",
    )
    app.add_api_route(
        "/api/approve",
        approve_command,
        methods=["POST"],
        name="approve_command",
    )

    # Pre-create default agent at startup for persistence
    @app.on_event("startup")
    def _pre_create_agent():
        """Start default agent at Gateway launch - stays alive for all sessions."""
        try:
            cfg = load_config()
            default_model = cfg.get("model", {}).get("default", "")
            default_provider = cfg.get("model", {}).get("provider", "")
            if default_model:
                _log.info(f"[Agent] Pre-creating default agent: {default_provider}/{default_model}")
                # Agent will be created on first request via _agent_cache
                # This just ensures config is valid
        except Exception as e:
            _log.warning(f"[Agent] Pre-create check failed: {e}")


blueprint = None  # no APIRouter; uses register_to(app) pattern
