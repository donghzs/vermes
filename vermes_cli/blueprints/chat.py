"""Blueprint: Chat（聊天核心路由）

Endpoints:
- POST /api/chat/completions  — Agent-powered 聊天（流式/非流式）
- GET  /api/chat/models       — 可用模型列表
"""

import asyncio
import base64 as b64mod
import difflib
import json
import logging

logger = logging.getLogger(__name__)
import os
import re
import secrets
import hashlib
import time
from pathlib import Path
from typing import Optional

import yaml
from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from vermes_cli.config import load_config, remove_env_value

_log = logging.getLogger(__name__)

from vermes_cli.blueprints.agent_cache import (
    _agent_cache,
    stop_agent_session,
    clean_agent_for_session,
)

# ── Session plan state store (for SSE reconnect snapshot) ──────────
# session_id → {"plan": dict|None, "todo_states": dict, "plan_emitted": bool}
_session_plan_store: dict[str, dict] = {}


def _persist_session_plan(session_id: str, state: dict) -> None:
    """Best-effort persist plan state to SQLite (closes cross-restart gap). Fail-open."""
    try:
        from agent.session_plan_store import save_plan_state

        save_plan_state(
            session_id,
            state.get("plan"),
            state.get("todo_states", {}),
            state.get("plan_emitted", False),
        )
    except Exception:
        pass


def _restore_session_plan(session_id: str):
    """Best-effort restore plan state from SQLite (covers process restart). Fail-open."""
    try:
        from agent.session_plan_store import load_plan_state

        return load_plan_state(session_id)
    except Exception:
        return None


def _update_session_plan(session_id: str, plan=None, todo_states=None, plan_emitted=None) -> None:
    """Partially update in-memory plan state and persist to SQLite (audit #2 DRY).

    Only the supplied fields are overwritten; the rest keep their current value.
    Fail-open: never raises.
    """
    state = _session_plan_store.setdefault(
        session_id, {"plan": None, "todo_states": {}, "plan_emitted": False}
    )
    if plan is not None:
        state["plan"] = plan
    if todo_states is not None:
        state["todo_states"] = todo_states
    if plan_emitted is not None:
        state["plan_emitted"] = plan_emitted
    _persist_session_plan(session_id, state)


def clean_session_plan_state(session_id: str) -> None:
    """Drop in-memory + persisted plan state on session delete (closes mem-leak gap, audit #1)."""
    _session_plan_store.pop(session_id, None)
    try:
        from agent.session_plan_store import delete_plan_state

        delete_plan_state(session_id)
    except Exception:
        pass


def _normalize_stream_text(text) -> str:
    """折叠空白做宽松比对（与 run_agent._normalize_interim_visible_text 同规则）。"""
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _longest_common_ratio(a: str, b: str) -> float:
    """计算两个字符串的相似度比例（0.0~1.0）。

    用于判断 streamed_text 是否已覆盖 final_response 的大部分内容。
    使用 difflib.SequenceMatcher.ratio() 做字符级相似度比对，
    对流式分块导致的细微差异（标点/空白/截断位置不同）鲁棒。

    性能：O(n*m) 最坏情况，但 difflib 有内部优化（只匹配连续块），
    实际场景下远快于理论复杂度。对超长文本截断到 5000 字符。
    """
    if not b:
        return 0.0
    if not a:
        return 0.0
    if a == b:
        return 1.0
    # 限制搜索范围，避免超长文本卡顿。
    # 当 a 远长于 b 时，取 a 的尾部（b 长度的 1.5x）来比对，
    # 因为 final_response 通常对应 streamed_text 的最后一段。
    if len(a) > len(b) * 2:
        a = a[-int(len(b) * 1.5):]
    a, b = a[:5000], b[:5000]
    import difflib
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def _compute_final_fallback_tail(final_response, streamed_text) -> "str | None":
    """计算需要补发到 SSE 队列的「尾部差分」文本；已完整呈现则返回 None。

    背景：SSE 生成器只转发流式 delta，run_conversation 的返回值从不入队。
    当最终回答未经过 stream_delta_callback 流出（非流式回退、
    fallback_prior_turn、guardrail halt、partial recovery 等路径）时，
    后端有答案前端却收到 0 个 delta → "⚠ 回复为空"。此函数检测并给出应补发文本。

    关键修复（回复重复）：原逻辑 `final_n not in streamed_n` 在「流式文本是
    final 的前缀」时会把整段 final 重发，导致单条气泡内容翻倍。现改为差分：
      - final 等于/被包含于 streamed → 已呈现，返回 None；
      - streamed 是 final 的前缀 → 仅补发 final 的尾部（缺失部分），不重复前缀；
      - 极短回答(<=7) → 宁可短重复也不漏发（防空回复）；
      - 其它无包含关系 → 整段补发（保持原行为，不丢内容）。
    """
    final_n = _normalize_stream_text(final_response)
    if not final_n or final_n == "(empty)":
        return None
    streamed_n = _normalize_stream_text(streamed_text)
    if not streamed_n:
        return final_n
    # 已完整呈现：归一化后整段流文本恰好等于 final → 不补发
    if streamed_n == final_n:
        return None
    # final 已包含在 streamed 中（final 是子串/前缀）→ 已呈现
    if final_n in streamed_n:
        return None
    # streamed 是 final 的前缀 → 仅补发缺失尾部，避免整段重复（修复回复重复）
    if final_n.startswith(streamed_n):
        _tail = final_n[len(streamed_n):]
        return _tail or None
    # 极短回答(<=7 归一化字符)：模型答案里夹短词可能恰好是流文本子串，用 in 会误判
    # 已呈现而漏发，重现空回复。除整段精确等于外一律补发（最多一条短重复）。
    if len(final_n) < 8:
        return final_n
    # 长回答且无精确包含关系：检查重叠度。如果 streamed 已经覆盖了 final
    # 的大部分内容，说明流式已经发过，差异可能是 scrubber 处理
    # 导致的细微差异——不整段补发，避免重复输出。
    #
    # 尾部匹配：当 streamed 远长于 final（典型场景：工具流式输出 + 最终简短回复），
    # 整体 overlap 会被工具内容稀释。改为只比较 streamed 尾部（长度 = final 的 1.5x）
    # 与 final，精准检测最终回复是否已流式发出。
    if len(streamed_n) > len(final_n) * 2:
        tail_len = int(len(final_n) * 1.5)
        streamed_tail = streamed_n[-tail_len:]
        tail_overlap = _longest_common_ratio(streamed_tail, final_n)
        if tail_overlap >= 0.5:
            # 尾部高重叠：最终回复已通过流式发出，不补发
            return None

    overlap = _longest_common_ratio(streamed_n, final_n)
    if overlap >= 0.5:
        # 中高重叠：内容已通过流式发出，差异可能是 scrubber/空白处理导致
        # 不整段补发，只提取差分部分（如果有）
        s = difflib.SequenceMatcher(None, streamed_n, final_n)
        tail_parts = []
        for tag, i1, i2, j1, j2 in s.get_opcodes():
            if tag in ('insert', 'replace'):
                tail_parts.append(final_n[j1:j2])
        tail = ''.join(tail_parts).strip()
        if len(tail) < len(final_n) * 0.3:
            # 差分小于 final 30%，认为是噪声，不补发
            return None
        return tail or None
    # 真正无包含关系（不同内容） → 整段补发（保持原行为，不丢内容）
    return final_n


def _should_emit_final_fallback(final_response, streamed_text) -> bool:
    """兼容旧签名：是否需要补发兜底（bool）。尾部差分由 _compute_final_fallback_tail 给出。"""
    return _compute_final_fallback_tail(final_response, streamed_text) is not None


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
        from vermes_constants import get_hermes_home
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
    web_search: bool = False  # 联网搜索开关


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

# NOTE: `_load_toolsets_for_web` now lives in `vermes_cli.tools_config`
# (moved there so the web toolset resolver and its loader share one module
# and the former circular-import risk is gone). Imported above as needed.


def _get_chat_credentials() -> tuple[str, str, str]:
    """Return (base_url, api_key, default_model) from config.yaml + .env."""
    from vermes_constants import get_hermes_home

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
    from vermes_constants import get_hermes_home
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
    from vermes_cli.blueprints.quota import _claim_trial_token
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

def _persist_web_turn_to_state_db(session_id: str, user_message: str, final_response: str) -> None:
    """Step 2: best-effort mirror of a web/desktop turn into ``state.db``.

    Makes web/desktop conversations appear in the unified cross-channel view
    (``/api/sessions``) even when they were created in a different client than
    the one reading the list. Fail-open: a broken/missing state.db must never
    block the user from seeing their reply.

    Idempotent-by-design: ``create_session`` is INSERT OR IGNORE, and each
    HTTP request corresponds to exactly one user turn, so a single user + one
    assistant append per call is correct (no double-write within state.db —
    the only other web writer is ``save_gui_messages``, which targets
    ``~/.vermes/messages/*.json``, not state.db).
    """
    if not session_id:
        return
    try:
        from vermes_state import SessionDB
        db = SessionDB()
        db.create_session(session_id, source="web")
        if user_message:
            db.append_message(session_id, "user", user_message)
        if final_response:
            db.append_message(session_id, "assistant", final_response)
    except Exception as exc:  # pragma: no cover - best-effort
        _log.debug(f"[web→state.db] persist skipped for {session_id}: {exc}")


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
                from vermes_constants import get_hermes_home
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

    # P1-3: Initialize / restore session plan store for SSE reconnect snapshot
    if _session_id not in _session_plan_store:
        _restored = _restore_session_plan(_session_id)
        if _restored is not None:
            _session_plan_store[_session_id] = _restored
    _session_plan_store.setdefault(_session_id, {"plan": None, "todo_states": {}, "plan_emitted": False})

    # Agent cache: reuse agent instance per session for persistence
    _cache_key = f"{provider}:{model}:{_session_id}"
    agent = _agent_cache.get(_cache_key)

    # ── 联网搜索开关：ephemeral 提示，每次请求都按当前 req.web_search 重算 ──
    # 提到 if 外：保证缓存复用时开关变更即时生效（此前只在建 agent 时注入，
    # 复用后 req.web_search 改动不生效）。
    _search_prompt = ""
    if req.web_search:
        _search_prompt = (
            "【联网搜索已开启】用户希望获取最新信息。"
            "当问题涉及时事、最新数据、实时信息或你不确定的事实时，"
            "请主动使用 web_search 工具搜索互联网获取准确信息，"
            "并引用来源。对于常识性问题无需搜索。"
        )

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

            _combined_prompt = (_evo_prompt + "\n" + _search_prompt).strip() or None

            from vermes_cli.tools_config import get_effective_web_toolset_keys, _load_toolsets_for_web
            # Route web through the same governed toolset resolver used by the
            # CLI/TUI so that `hermes tools` choices and the in-app toolset
            # toggle (platform_toolsets.web) actually take effect for the
            # desktop/Web agent.  Falls back to the rich legacy default when
            # platform_toolsets.web is absent/empty (fresh installs) so a
            # zero-tool agent is never produced.
            # Guard against NameError when _cfg is undefined because an earlier
            # load_config() call raised (pre-existing latent bug, now reachable
            # via this code path) — fall back to the legacy default rather than
            # returning a 500.
            try:
                _web_toolsets = get_effective_web_toolset_keys(_cfg)
            except Exception:
                _web_toolsets = _load_toolsets_for_web()
            agent = AIAgent(
                base_url=base_url,
                api_key=api_key,
                provider=provider,
                model=model,
                max_iterations=_max_iterations,
                quiet_mode=True,
                verbose_logging=False,
                platform="web",
                enabled_toolsets=list(_web_toolsets),
                disabled_toolsets=_disabled_toolsets,
                ephemeral_system_prompt=_combined_prompt,
                reasoning_config=_reasoning_config,
            )
            # 记录进化基线提示，供缓存复用时与最新联网开关重组 ephemeral prompt。
            agent._evo_base_prompt = _evo_prompt
            _n_tools = len(getattr(agent, "tools", None) or [])
            # 空工具 agent 不会被缓存（agent_cache.put 内部拒绝），下次请求重建。
            _agent_cache.put(_cache_key, agent)
            if _n_tools == 0:
                _log.warning(
                    f"[Agent] Created agent with 0 tools for session {_session_id} "
                    f"— tool registry may be cold; this request cannot use tools, "
                    f"next request will rebuild (not cached)."
                )
            else:
                _log.info(
                    f"[Agent] Created new agent for session {_session_id} (tools={_n_tools})"
                )
    else:
        _log.info(
            f"[Agent] Reusing cached agent for session {_session_id} "
            f"(tools={len(getattr(agent, 'tools', None) or [])})"
        )

    # ── ephemeral prompt 刷新：无论新建或复用，都用当前 req.web_search 重组 ──
    # 保证同一会话中途切换联网开关即时生效（复用路径此前完全失效）。
    if agent is not None:
        _evo_base = getattr(agent, "_evo_base_prompt", "") or ""
        agent.ephemeral_system_prompt = (_evo_base + "\n" + _search_prompt).strip() or None

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

        # 始终注册桌面端审批回调 → 通过 SSE 推送审批请求到前端弹窗。
        # 终端命令 / 代码执行在 YOLO 模式下由 check_dangerous_command /
        # check_execute_code_guard 提前短路放行（approval.py:1066 / 1320），
        # 不会触发本回调；只有显式走 request_gateway_approval 的特权动作
        # （如 self_modify 自我改写）会弹窗 —— YOLO 模式下也会被自动放行
        # （approval.py:629），与 dangerous command 策略一致。
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
        from vermes_cli.blueprints.state import _active_streams

        _delta_queue: asyncio.Queue = asyncio.Queue()
        _agent_done = asyncio.Event()
        _stream_id = secrets.token_urlsafe(16)
        _cancel_event = asyncio.Event()
        _tool_ids = {}  # tool_name → tool_call_id 配对
        _active_streams[_stream_id] = _cancel_event

        # ── 在事件循环线程中捕获 loop 引用 ──────────────────────────────
        # agent callback 会在子线程中执行，asyncio.get_running_loop() 会抛 RuntimeError。
        # 旧代码的 fallback 路径直接调用 _delta_queue.put_nowait()，但 asyncio.Queue
        # 不是线程安全的，会导致队列损坏 → SSE 流卡死 → "Failed to fetch"。
        # 修复：捕获 loop 引用，callback 中用 call_soon_threadsafe 安全写入。
        _main_loop = asyncio.get_running_loop()

        def _safe_put(item):
            """从任意线程安全地向 delta_queue 写入数据。"""
            if not _main_loop.is_closed():
                _main_loop.call_soon_threadsafe(_delta_queue.put_nowait, item)

        def status_callback(event_type: str, message: str):
            """Route lifecycle/warn events from AIAgent to SSE stream."""
            event = {
                "type": "lifecycle" if event_type == "lifecycle" else "warn",
                "message": message,
            }
            _safe_put(event)

        # ── 任务规划事件检测 ─────────────────────────────────────────────
        _plan_text_buffer = []  # 累积输出，检测 plan JSON
        _plan_emitted = False   # 防重复 emit
        _prev_todo_states = {}  # P0-1: 跟踪 todo 步骤状态变化，驱动 plan_step_update
        # 流式衔接兜底：累积本轮已发出的文本 delta。
        # 若 run_conversation 返回的 final_response 未包含在已流出文本中，
        # 说明最终回答走了非流式路径（回退/恢复/halt），需补发，否则前端空回复。
        _streamed_text_parts = []

        def _detect_and_emit_plan(text: str):
            """检测 plan JSON 并通过 SSE 发送规划事件。
            使用平衡括号解析器，支持嵌套对象和转义字符。"""
            nonlocal _plan_emitted
            if _plan_emitted:
                return
            _plan_text_buffer.append(text)
            combined = "".join(_plan_text_buffer)
            # 去掉 markdown fence
            stripped = re.sub(r'^```[a-z]*\s*', '', combined, flags=re.MULTILINE)
            stripped = re.sub(r'```\s*$', '', stripped, flags=re.MULTILINE)
            # 平衡括号解析：找第一个包含 "plan" 键的 JSON 对象
            result = _find_first_plan_json(stripped)
            if result is None:
                return
            plan_data = result.get("plan", result)
            steps_out = []
            for i_s, s in enumerate(plan_data.get("steps", [])):
                steps_out.append({
                    "id": s.get("id") or f"step_{i_s+1}",
                    "title": s.get("title", f"Step {i_s+1}"),
                    "description": s.get("description", ""),
                    "status": "pending",
                    "agent_role": s.get("agent_role", "default"),
                    "order": i_s,
                    "tool_calls": [],
                })
            plan_event = {
                "type": "plan_created",
                "plan": {
                    "id": hashlib.md5(
                        (plan_data.get("title", "task") or "task").encode()).hexdigest()[:8],
                    "title": plan_data.get("title", "任务规划"),
                    "description": plan_data.get("description", ""),
                    "steps": steps_out,
                    "status": "pending",
                    "progress_percent": 0,
                    "stats": {
                        "total": len(steps_out),
                        "completed": 0,
                        "in_progress": 0,
                        "pending": len(steps_out),
                    },
                    "estimated_duration": plan_data.get("estimated_duration", 0),
                    "required_tools": plan_data.get("required_tools", []),
                }
            }
            _safe_put(plan_event)
            _log.info(f"[Plan] Detected plan: {plan_event['plan']['title']} with {len(steps_out)} steps")
            _plan_emitted = True
            # P1-3: Persist plan to session store for SSE reconnect snapshot
            _update_session_plan(_session_id, plan=plan_event["plan"], todo_states=_prev_todo_states, plan_emitted=True)
            # 立即标记第一步为进行中，传递"实时反馈的快感"
            if steps_out:
                _safe_put({
                    "type": "plan_step_update",
                    "step": {
                        "id": steps_out[0]["id"],
                        "status": "in_progress",
                        "started_at": int(time.time()),
                    }
                })
                return

        def stream_callback(delta: str):
            if delta is not None:
                _log.info(f"[Stream] DELTA: {repr(delta[:60])}")
                if delta.strip():
                    _streamed_text_parts.append(delta)
                _detect_and_emit_plan(delta)
                _safe_put(delta)
            else:
                # Turn boundary: Agent 即将开始工具调用或新回合。
                # 发送分隔事件让前端在工具输出和 Agent 回复之间做视觉分隔。
                _log.info(f"[Stream] Turn boundary (delta=None), agent still running")
                _safe_put({"type": "turn_boundary"})

        def tool_progress_handler(event_type: str, tool_name: str, preview: str, args: dict, **kwargs):
            _log.info(f"[ToolEvent] {event_type}: {tool_name}")
            # 关联当前进行中的 todo 步骤，供前端把工具调用挂到对应步骤下
            # ordinal 序位：取 in_progress 条目在列表中的 1-based 序位 + total
            # 逻辑抽到 tools.todo_tool.compute_active_step_ordinal，便于单测（真测而非重算）
            try:
                from tools.todo_tool import compute_active_step_ordinal
                step_id, step_index, step_total = compute_active_step_ordinal(
                    getattr(agent, "_todo_store", None))
            except Exception:
                step_id = step_index = step_total = None
            if event_type == "tool.started":
                _tool_id = secrets.token_urlsafe(8)
                _tool_ids[tool_name] = _tool_id
                event = {
                    "type": "tool_start",
                    "tool_call_id": _tool_id,
                    "tool_name": tool_name,
                    "arguments": args or {},
                    "step_id": step_id,
                    "step_index": step_index,
                    "step_total": step_total,
                }
            else:
                event = {
                    "type": "tool_end",
                    "tool_call_id": _tool_ids.pop(tool_name, secrets.token_urlsafe(8)),
                    "tool_name": tool_name,
                    "duration": kwargs.get("duration", 0),
                    "is_error": kwargs.get("is_error", False),
                    "result_preview": preview or "",
                    "step_id": step_id,
                    "step_index": step_index,
                    "step_total": step_total,
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
                        _safe_put(todo_event)
                        # P0-1: 同步发射 plan_step_update 使步骤 1..n 进度实时更新
                        # 逐条比对 _prev_todo_states 与新状态，只发变化的步骤
                        _new_todos = todo_data.get("todos", [])
                        for _t in _new_todos:
                            _tid = _t.get("id", "")
                            _new_status = _t.get("status", "")
                            _old_status = _prev_todo_states.get(_tid)
                            if _new_status != _old_status:
                                _step_update = {
                                    "type": "plan_step_update",
                                    "step": {
                                        "id": _tid,
                                        "status": _new_status,
                                    },
                                }
                                if _new_status == "in_progress":
                                    _step_update["step"]["started_at"] = int(time.time())
                                elif _new_status in ("completed", "cancelled"):
                                    _step_update["step"]["finished_at"] = int(time.time())
                                _safe_put(_step_update)
                                _prev_todo_states[_tid] = _new_status
                        # P1-3: Sync todo states to session store
                        _update_session_plan(_session_id, todo_states=_prev_todo_states, plan_emitted=_plan_emitted)
                        # 任务全部完成 → 发庆祝事件（additive，旧前端忽略）
                        _s = todo_data.get("summary", {})
                        if _s.get("total", 0) > 0 and _s.get("completed", 0) == _s.get("total") \
                                and _s.get("in_progress", 0) == 0:
                            _safe_put({"type": "task_complete", "summary": _s})
                    except Exception:
                        pass
            _safe_put(event)

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
            _safe_put(event)

        def evolution_event_handler(message: str, tool_name: str, is_error: bool, duration: float):
            """Route evolution events (achievements, advice) to SSE stream."""
            event = {
                "type": "evolution",
                "message": message,
                "tool_name": tool_name,
                "is_error": is_error,
                "duration": round(duration, 2),
            }
            _safe_put(event)

        def reasoning_handler(text: str):
            """Route reasoning/thinking deltas to SSE stream for visualization."""
            if text:
                _safe_put({"type": "reasoning", "content": text})

        def run_sync():
            try:
                _log.info(f"[Stream] Agent starting, model={model}, provider={provider}, stream_id={_stream_id}")
                agent.stream_delta_callback = stream_callback
                agent.tool_progress_callback = tool_progress_handler
                agent.step_callback = thinking_handler
                agent.status_callback = status_callback
                agent.evolution_event_callback = evolution_event_handler
                agent.reasoning_callback = reasoning_handler

                def plan_event_handler(event_type: str, data: dict):
                    """Route plan events (step_update, tool_call, plan_completed) to SSE."""
                    # 幂等去重：若 _detect_and_emit_plan 已发射 plan_created，跳过重复
                    if event_type == "created" and _plan_emitted:
                        _log.debug("[Plan] Duplicate plan_created via bridge, skipping")
                        return
                    _safe_put({"type": f"plan_{event_type}", **data})
                agent.plan_event_callback = plan_event_handler
                _max_tokens = getattr(req, 'max_tokens', None) or _resolve_max_tokens(model)
                agent.max_tokens = _max_tokens
                result = agent.run_conversation(
                    user_message=user_message,
                    conversation_history=conversation_history[:-1] if len(conversation_history) > 1 else None,
                    stream_callback=None,
                )
                # ── Step 2: mirror web/desktop turn into state.db (unified view) ──
                try:
                    _persist_web_turn_to_state_db(
                        _session_id,
                        user_message,
                        (result or {}).get("final_response") or "",
                    )
                except Exception as _web_db_exc:  # pragma: no cover - best-effort
                    _log.debug(f"[web→state.db] persist failed: {_web_db_exc}")
                _log.info(f"[Stream] Agent done, result keys={list(result.keys()) if result else 'None'}")
                # ── 流式衔接兜底（空回复修复）─────────────────────────
                # SSE 生成器只转发流式 delta；当最终回答未经过
                # stream_delta_callback 流出（非流式回退、prior-turn
                # fallback、guardrail halt、partial recovery 等路径），
                # run_conversation 的返回值不会到达前端 → "⚠ 回复为空"。
                # 此处检测"整轮零文本 delta 但有真实 final_response"，
                # 将其补发入队，保证后端有答案时前端必能收到。
                try:
                    _final = (result or {}).get("final_response") or ""
                    _streamed_full = "".join(_streamed_text_parts)
                    _tail = _compute_final_fallback_tail(_final, _streamed_full)
                    if _tail:
                        _final_n = _normalize_stream_text(_final)
                        _streamed_n = _normalize_stream_text(_streamed_full)
                        _overlap = _longest_common_ratio(_streamed_n, _final_n)
                        _log.warning(
                            f"[Stream] Final response never streamed "
                            f"({len(_final)} chars, emitting tail {len(_tail)} chars) "
                            f"- emitting fallback to SSE queue "
                            f"[overlap={_overlap:.2f} "
                            f"final_n={len(_final_n)} streamed_n={len(_streamed_n)} "
                            f"final_preview={repr(_final_n[:80])} "
                            f"streamed_tail_preview={repr(_streamed_n[-80:])}]"
                        )
                        _safe_put(_tail)
                except Exception as _fb_exc:
                    _log.error(f"[Stream] Final fallback emission failed: {_fb_exc}")
                return result
            except Exception as e:
                _log.error(f"[Stream] Agent error: {e}")
                raise
            finally:
                # P1-1: 预算退出收尾——agent 结束后将残留 in_progress 步骤标记为 interrupted
                for _tid, _status in _prev_todo_states.items():
                    if _status == "in_progress":
                        _safe_put({
                            "type": "plan_step_update",
                            "step": {
                                "id": _tid,
                                "status": "interrupted",
                                "finished_at": int(time.time()),
                            },
                        })
                        _prev_todo_states[_tid] = "interrupted"
                # P1-3: Sync final state to session store
                _update_session_plan(_session_id, todo_states=_prev_todo_states, plan_emitted=_plan_emitted)
                _agent_done.set()

        async def stream_generator():
            try:
                yield f'data: {json.dumps({"type": "stream_start", "stream_id": _stream_id})}\n\n'

                loop = asyncio.get_running_loop()
                # 全局共享线程池，避免泄漏
                from vermes_cli.blueprints.state import get_agent_executor
                _exec = get_agent_executor()
                try:
                    agent_task = loop.run_in_executor(_exec, run_sync)
                except RuntimeError:
                    # executor 可能被 shutdown，重建
                    from vermes_cli.blueprints.state import get_agent_executor as _rebuild
                    _exec = _rebuild()
                    agent_task = loop.run_in_executor(_exec, run_sync)

                # 全局超时保护：防止 agent 线程卡死后 SSE 流无限挂起
                _stream_start_time = time.time()
                _STREAM_MAX_DURATION = 1800  # 30 分钟上限

                while not _agent_done.is_set() or not _delta_queue.empty():
                    # 超时强制退出
                    if time.time() - _stream_start_time > _STREAM_MAX_DURATION:
                        _log.error(f"[Stream] Global timeout ({_STREAM_MAX_DURATION}s), force stopping, stream_id={_stream_id}")
                        agent._interrupt_requested = True
                        agent_task.cancel()
                        break

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
                        # 给 agent 线程 5 秒时间清理，超则强制放弃
                        try:
                            await asyncio.wait_for(asyncio.shield(agent_task), timeout=5.0)
                        except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                            _log.warning(f"[Stream] Agent did not finish within 5s after cancel, abandoning, stream_id={_stream_id}")
                except NameError:
                    pass  # agent_task not yet created
                except Exception as _cleanup_err:
                    _log.error(f"[Stream] Cleanup error: {_cleanup_err}")

            # Wait for agent result (with timeout to prevent hanging)
            try:
                _agent_result = await asyncio.wait_for(agent_task, timeout=30.0)
            except asyncio.TimeoutError:
                _log.error(f"[Stream] Agent did not complete within 30s post-stream, abandoning")
                _agent_result = {}
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
        from vermes_cli.blueprints.state import get_agent_executor
        _exec = get_agent_executor()
        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(_exec, run_sync),
                timeout=1800.0  # 30 分钟超时
            )
        except asyncio.TimeoutError:
            raise HTTPException(status_code=504, detail="Agent execution timed out (30min)")
        except RuntimeError:
            _exec = get_agent_executor()  # 重建
            result = await asyncio.wait_for(
                loop.run_in_executor(_exec, run_sync),
                timeout=1800.0
            )

        final_response = result.get("final_response", "") if result else ""
        # ── Step 2: mirror web/desktop turn into state.db (unified view) ──
        try:
            _persist_web_turn_to_state_db(_session_id, user_message, final_response)
        except Exception as _web_db_exc:  # pragma: no cover - best-effort
            _log.debug(f"[web→state.db] persist failed: {_web_db_exc}")
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


# ── Plan 解析纯函数（供 _detect_and_emit_plan 闭包与测试共用）──

def _find_first_plan_json(text: str) -> dict | None:
    """平衡括号解析器：从 text 中提取第一个包含 'plan' 键的 JSON 对象。

    支持：嵌套对象、转义字符、markdown fence 前缀/后缀已被调用方处理。
    P2-1: 加最小 schema 校验——plan 键存在且 steps 可提取。
    Returns: 解析后的 dict 或 None。
    """
    depth = 0
    start = -1
    in_str = False
    esc_next = False
    for i_c, ch in enumerate(text):
        if esc_next:
            esc_next = False
            continue
        if ch == '\\':
            esc_next = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == '{':
            if start == -1:
                start = i_c
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0 and start >= 0:
                candidate = text[start:i_c + 1]
                if '"plan"' in candidate:
                    try:
                        parsed = json.loads(candidate)
                    except Exception:
                        start = -1
                        continue
                    # P2-1: 最小 schema 校验
                    # plan 值可以是 string 或 dict；steps 必须可提取
                    plan_val = parsed.get("plan")
                    if plan_val is None:
                        start = -1
                        continue
                    if isinstance(plan_val, dict):
                        steps = plan_val.get("steps")
                    elif isinstance(plan_val, str):
                        # plan 是标题字符串，steps 在顶层
                        steps = parsed.get("steps")
                    else:
                        start = -1
                        continue
                    if not isinstance(steps, list) or len(steps) == 0:
                        start = -1
                        continue
                    return parsed
                start = -1
    return None


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
    # 空工具 agent 不会被缓存（agent_cache.put 内部拒绝）。
    _agent_cache.put(_cache_key, agent)
    _log.info(
        f"[Agent] Created API agent for session {req.session_id} "
        f"(tools={len(getattr(agent, 'tools', None) or [])})"
    )

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


async def memory_status(session_id: str = ""):
    """Return current memory injection status for the frontend.

    Shows which memory blocks are loaded in the current session's agent,
    their sizes, and a preview of content. Used by the UI memory indicator.
    """
    try:
        from vermes_cli.blueprints.agent_cache import _agent_cache

        # Find the agent for this session (or any active agent)
        agent = None
        if session_id:
            for key, a in _agent_cache._cache.items():
                if session_id in key:
                    agent = a
                    break
        if agent is None:
            # Fallback: get the most recently used agent
            if _agent_cache._cache:
                agent = next(reversed(_agent_cache._cache.values()))

        if agent is None:
            return {
                "active": False,
                "blocks": {},
                "total_chars": 0,
                "message": "No active agent session"
            }

        blocks = {}
        total_chars = 0

        # Check each memory block on the agent instance
        for attr_name, display_name in [
            ("_handoff_context", "handoff"),
            ("_evolution_context", "evolution"),
            ("_recall_context", "recall"),
        ]:
            block_text = getattr(agent, attr_name, None) or ""
            if block_text:
                # Extract content between XML tags for preview
                import re
                match = re.search(r"<([^>]+)>(.*)</\1>", block_text, re.DOTALL)
                tag = match.group(1) if match else attr_name
                content = match.group(2).strip() if match else block_text
                blocks[display_name] = {
                    "tag": tag,
                    "chars": len(block_text),
                    "preview": content[:200] + ("..." if len(content) > 200 else ""),
                }
                total_chars += len(block_text)

        # Decisions block (loaded from decision_tracker, not on agent)
        try:
            from agent.decision_tracker import format_decisions_for_prompt
            decisions_text = format_decisions_for_prompt(limit=5)
            if decisions_text:
                import re
                match = re.search(r"<([^>]+)>(.*)</\1>", decisions_text, re.DOTALL)
                blocks["decisions"] = {
                    "tag": match.group(1) if match else "decisions",
                    "chars": len(decisions_text),
                    "preview": (match.group(2).strip() if match else decisions_text)[:200] + "...",
                }
                total_chars += len(decisions_text)
        except Exception:
            pass

        # Get handoff source info if available
        handoff_info = None
        try:
            from agent.handoff_store import get_global_latest_handoff
            h = get_global_latest_handoff()
            if h:
                handoff_info = {
                    "session_id": h.get("session_id", ""),
                    "age_hours": round((time.time() - h.get("created_at", 0)) / 3600, 1),
                    "summary": (h.get("summary_text") or "")[:150],
                }
        except Exception:
            pass

        return {
            "active": True,
            "blocks": blocks,
            "total_chars": total_chars,
            "total_tokens_est": round(total_chars / 4),
            "budget_limit": 4000,
            "handoff_source": handoff_info,
            "session_id": session_id or getattr(agent, "session_id", ""),
        }
    except Exception as e:
        return {"active": False, "error": str(e)}


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


async def self_modify_history(limit: int = 100):
    """Return the self-modification / self-evolution approval log.

    Surfaces every privileged self-change the agent proposed or the system
    auto-suggested, and how the user resolved it:
      - self_modify        : agent-proposed source edits (committed / proposed /
                             held / rejected)
      - self_modify_rollback: agent/user/system rollbacks
      - capability_activate : system-emergent capability activations
                             (activated / denied by the user)

    This is the "what did the agent try to change about itself, and what did
    I approve" panel. All rows come from raw_events.
    """
    try:
        from agent.evolution_manager import get_self_model_db
        db_path = str(get_self_model_db())
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT timestamp, tool_name, args_preview, result_preview, success
               FROM raw_events
               WHERE tool_name IN ('self_modify', 'self_modify_rollback', 'capability_activate', '__retraction__')
               ORDER BY rowid DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        conn.close()

        import re
        events = []
        for r in rows:
            tool = r["tool_name"]
            result = r["result_preview"] or ""
            args = r["args_preview"] or ""
            initiator_m = re.search(r"initiator'?\s*:\s*'(\w+)'", args)
            initiator = initiator_m.group(1) if initiator_m else "system"
            status, target, backup = _classify_self_modify(tool, result)
            events.append({
                "timestamp": r["timestamp"],
                "type": tool,
                "status": status,
                "target": target,
                "backup": backup,
                "initiator": initiator,
                "success": bool(r["success"]),
                "detail": result,
            })
        return {"events": events, "count": len(events)}
    except Exception as e:
        return {"events": [], "count": 0, "error": str(e)}


def _classify_self_modify(tool: str, result: str):
    """Map a raw_event result string to (status, target_path, backup_path)."""
    if tool == "__retraction__":
        # result = "retracted: capability:cap_name" or "retracted: insight:insight_name"
        body = result.replace("retracted: ", "", 1).strip()
        # 剥掉 type 前缀，仅保留名称用于展示（capability:foo → foo）
        if ":" in body:
            body = body.split(":", 1)[1]
        return "retracted", body, ""
    if tool == "self_modify_rollback":
        if result.startswith("denied: "):
            return "denied", result[8:].strip(), ""
        return "rolled_back", result.replace("rolled back: ", "", 1).strip(), ""
    if tool == "capability_activate":
        if result.startswith("activated: "):
            return "activated", result[11:].strip(), ""
        if result.startswith("denied: "):
            return "denied", result[8:].strip(), ""
        return "unknown", result.strip(), ""
    # self_modify
    if result.startswith("committed: "):
        body = result[11:].strip()
        backup = ""
        if " || backup: " in body:
            body, backup = body.split(" || backup: ", 1)
        return "committed", body, backup
    if "proposed: awaiting user approval" in result:
        return "proposed", "", ""
    if "pending_confirmation" in result:
        return "held", "", ""
    if result.startswith("rejected: "):
        return "rejected", "", ""
    return "unknown", "", ""


async def self_modify_rollback(request: Request):
    """User-initiated one-click rollback of an applied self-modification.

    Body: { "target_path": str, "backup_path": str|null }

    The user's click on the Evolution panel IS the confirmation, so no
    Gateway approval gate is applied (initiator="user"). This mirrors the
    dangerous-command policy where the user's explicit action is already
    in-the-loop. Autonomous (agent/system) rollbacks are gated separately
    inside EmergentChangePipeline.rollback_change.
    """
    try:
        body = await request.json()
        target_path = body.get("target_path", "")
        backup_path = body.get("backup_path", "") or None
        if not target_path:
            return {"ok": False, "error": "target_path required"}
        from agent.emergent_change import get_pipeline
        rolled = get_pipeline().rollback_change(
            target_path, backup_path, initiator="user",
        )
        return {"ok": True, "rolled_back": bool(rolled), "target_path": target_path}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def retract_evolution_item(request: Request):
    """Logically retract a capability or insight.

    Body: { "target_type": "capability"|"insight", "target_name": str, "reason": str? }

    Logical retraction records a __retraction__ raw_event that the emergence
    cycle uses to suppress future suggestions. The original event is NOT
    deleted — this preserves the audit trail and the negative-feedback signal.
    """
    try:
        body = await request.json()
        target_type = body.get("target_type", "")
        target_name = body.get("target_name", "")
        reason = body.get("reason", "")
        if not target_type or not target_name:
            return {"ok": False, "error": "target_type and target_name required"}
        from agent.raw_event import record_retraction
        rowid = record_retraction(
            target_type=target_type,
            target_name=target_name,
            reason=reason,
        )
        return {
            "ok": True,
            "retracted": True,
            "target_type": target_type,
            "target_name": target_name,
            "event_id": rowid,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def emergence_status():
    """Return emergence system status: richness, clusters, capabilities, health."""
    try:
        from agent.evolution_manager import get_self_model_db
        db_path = str(get_self_model_db())

        # Richness
        try:
            from agent.memory_recall import compute_richness
            richness = compute_richness(db_path)
            richness_data = {
                "score": round(richness.score, 3),
                "tier": richness.tier,
                "raw_event_density": round(richness.raw_event_density, 3),
                "cluster_density": round(richness.cluster_density, 3),
                "session_density": round(richness.session_density, 3),
                "handoff_density": round(richness.handoff_density, 3),
            }
        except Exception:
            richness_data = None

        # Cluster stats
        try:
            import sqlite3 as _sql
            conn = _sql.connect(db_path)
            conn.row_factory = _sql.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=3000")
            c = conn.cursor()
            c.execute("SELECT status, COUNT(*) as cnt FROM clusters GROUP BY status")
            cluster_stats = {r[0]: r[1] for r in c.fetchall()}
            c.execute("SELECT COUNT(*) FROM raw_events")
            total_events = c.fetchone()[0]
            conn.close()
        except Exception:
            cluster_stats = {}
            total_events = 0

        # Capability registry
        try:
            from agent.capability_registry import get_capability_report
            caps = get_capability_report()
        except Exception:
            caps = {"installed": [], "active": []}

        # Health check
        try:
            from agent.raw_event import _LAST_EMERGENCE_OK, _EMERGENCE_STALE_THRESHOLD
            from datetime import datetime as _dt
            if _LAST_EMERGENCE_OK is not None:
                stale_hours = (_dt.now() - _LAST_EMERGENCE_OK).total_seconds() / 3600
                health = {
                    "last_emergence_ok": _LAST_EMERGENCE_OK.isoformat(),
                    "stale_hours": round(stale_hours, 1),
                    "healthy": (_dt.now() - _LAST_EMERGENCE_OK) < _EMERGENCE_STALE_THRESHOLD,
                }
            else:
                health = {"last_emergence_ok": None, "stale_hours": None, "healthy": None, "reason": "cold_start"}
        except Exception:
            health = {"error": "unavailable"}

        # Domain modules
        try:
            from agent.domain_modules import list_all_modules
            _mods = list_all_modules(db_path)
            domain_mods = [
                {"id": m.id, "name": m.name, "description": m.description,
                 "event_count": m.event_count, "is_active": m.is_active}
                for m in _mods
            ]
        except Exception:
            domain_mods = []

        # Cross-session continuity
        try:
            from agent.cross_session_continuity import CrossSessionContinuity
            _cs = CrossSessionContinuity(db_path)
            _last_snap = _cs.load_last_snapshot()
            continuity = {
                "last_snapshot": _last_snap.timestamp if _last_snap else None,
                "last_session_id": _last_snap.session_id if _last_snap else None,
                "has_snapshot": _last_snap is not None,
            }
        except Exception:
            continuity = {"has_snapshot": False}

        return {
            "richness": richness_data,
            "clusters": cluster_stats,
            "total_events": total_events,
            "capabilities": caps,
            "health": health,
            "domain_modules": domain_mods,
            "continuity": continuity,
        }
    except Exception as e:
        return {"error": str(e)}


async def emergence_skills():
    """Return pending skills for user confirmation + active skills list."""
    try:
        from agent.evolution_manager import get_self_model_db
        db_path = str(get_self_model_db())

        from agent.skill_extractor import SkillExtractor
        extractor = SkillExtractor(db_path)
        pending = extractor.list_skills(status="pending")
        active = extractor.list_skills(status="active")
        rejected = extractor.list_skills(status="rejected")

        def _fmt(s):
            return {
                "id": s.id,
                "name": s.name,
                "description": s.description,
                "cluster_id": s.cluster_id,
                "status": s.status,
                "created_at": s.created_at,
                "confirmed_at": s.confirmed_at,
            }

        return {
            "pending": [_fmt(s) for s in pending],
            "active": [_fmt(s) for s in active],
            "rejected": [_fmt(s) for s in rejected],
        }
    except Exception as e:
        return {"error": str(e), "pending": [], "active": [], "rejected": []}


async def emergence_confirm_skill(skill_id: int, action: str = "confirm"):
    """User confirms or rejects a pending skill."""
    try:
        from agent.evolution_manager import get_self_model_db
        db_path = str(get_self_model_db())

        from agent.skill_extractor import SkillExtractor
        extractor = SkillExtractor(db_path)

        if action == "confirm":
            ok = extractor.confirm_skill(skill_id)
            return {"ok": ok, "action": "confirmed", "skill_id": skill_id}
        elif action == "reject":
            ok = extractor.reject_skill(skill_id)
            return {"ok": ok, "action": "rejected", "skill_id": skill_id}
        else:
            return {"error": f"Unknown action: {action}"}
    except Exception as e:
        return {"error": str(e)}


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
        from vermes_cli.mcp_config import _get_mcp_servers
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
        from vermes_cli.mcp_config import _save_mcp_server
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
        from vermes_cli.mcp_config import _remove_mcp_server
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
        from vermes_cli.mcp_config import _get_mcp_servers, _probe_single_server
        servers = _get_mcp_servers()
        if name not in servers:
            return {"error": f"Server '{name}' not found"}
        ok, msg, tools = _probe_single_server(name, servers[name])
        return {"ok": ok, "message": msg, "tools": tools or []}
    except Exception as e:
        return {"error": str(e), "ok": False}


async def mcp_set_enabled(name: str, request: Request):
    """Enable or disable an MCP server (persists the ``enabled`` flag in config)."""
    try:
        from vermes_cli.mcp_config import _get_mcp_servers, _save_mcp_server
        body = await request.json()
        enabled = body.get("enabled", True)
        if not name:
            return {"error": "name is required", "ok": False}
        servers = _get_mcp_servers()
        if name not in servers:
            return {"error": f"Server '{name}' not found", "ok": False}
        cfg = dict(servers[name])
        cfg["enabled"] = bool(enabled)
        _save_mcp_server(name, cfg)
        return {"ok": True, "name": name, "enabled": bool(enabled)}
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


async def resolve_flag_endpoint(request: Request):
    """Resolve a memory flag from frontend/CLI.

    Body: { flag_id: int, resolution: "demote"|"merge"|"false_positive" }
    """
    try:
        body = await request.json()
        flag_id = body.get("flag_id")
        resolution = body.get("resolution", "")
        if not isinstance(flag_id, int):
            try:
                flag_id = int(flag_id)
            except (TypeError, ValueError):
                return {"ok": False, "error": "flag_id must be an integer"}
        if resolution not in ("demote", "merge", "false_positive"):
            return {"ok": False, "error": "resolution must be demote|merge|false_positive"}
        from agent.memory_reflection import resolve_flag
        ok = resolve_flag(flag_id, resolution)
        return {"ok": ok, "flag_id": flag_id, "resolution": resolution}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def list_flags_endpoint(request: Request):
    """List open memory flags for the frontend panel."""
    try:
        from agent.memory_reflection import get_open_flags
        return {"ok": True, "flags": get_open_flags()}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def plan_snapshot(session_id: str):
    """P1-3: Return current plan + todo state for SSE reconnect recovery.

    Restores from SQLite if not in memory (covers process restart / cross-process
    takeover — closes the cross-restart recovery gap).
    """
    state = _session_plan_store.get(session_id)
    if state is None:
        state = _restore_session_plan(session_id)
    if state is None:
        return {"ok": True, "session_id": session_id, "plan": None, "todo_states": {}, "plan_emitted": False}
    return {
        "ok": True,
        "session_id": session_id,
        "plan": state.get("plan"),
        "todo_states": state.get("todo_states", {}),
        "plan_emitted": state.get("plan_emitted", False),
    }


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
        "/api/evolution/self_modify_history",
        self_modify_history,
        methods=["GET"],
        name="self_modify_history",
    )
    app.add_api_route(
        "/api/evolution/self_modify_rollback",
        self_modify_rollback,
        methods=["POST"],
        name="self_modify_rollback",
    )
    app.add_api_route(
        "/api/evolution/retract",
        retract_evolution_item,
        methods=["POST"],
        name="retract_evolution_item",
    )
    app.add_api_route(
        "/api/emergence/status",
        emergence_status,
        methods=["GET"],
        name="emergence_status",
    )
    app.add_api_route(
        "/api/emergence/skills",
        emergence_skills,
        methods=["GET"],
        name="emergence_skills",
    )
    app.add_api_route(
        "/api/emergence/skill/{skill_id}/{action}",
        emergence_confirm_skill,
        methods=["POST"],
        name="emergence_confirm_skill",
    )
    app.add_api_route(
        "/api/delegate/status/{task_id}",
        delegate_status,
        methods=["GET"],
        name="delegate_status",
    )
    app.add_api_route(
        "/api/memory/status",
        memory_status,
        methods=["GET"],
        name="memory_status",
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
        "/api/mcp/servers/{name}/enabled",
        mcp_set_enabled,
        methods=["POST"],
        name="mcp_set_enabled",
    )
    app.add_api_route(
        "/api/approve",
        approve_command,
        methods=["POST"],
        name="approve_command",
    )
    app.add_api_route(
        "/api/resolve_flag",
        resolve_flag_endpoint,
        methods=["POST"],
        name="resolve_flag",
    )
    app.add_api_route(
        "/api/flags",
        list_flags_endpoint,
        methods=["GET"],
        name="list_flags",
    )
    app.add_api_route(
        "/api/session/{session_id}/plan_snapshot",
        plan_snapshot,
        methods=["GET"],
        name="plan_snapshot",
    )

    # Pre-create default agent at startup for persistence
    @app.on_event("startup")
    def _pre_create_agent():
        """Start default agent at Gateway launch - stays alive for all sessions."""
        try:
            # Bootstrap literature providers so they appear in Settings UI
            # immediately (not only when ScholarForge search is first used)
            from agent.literature_registry import bootstrap_builtin_providers
            bootstrap_builtin_providers()
            _log.info("[Agent] Literature providers bootstrapped")
        except Exception as e:
            _log.warning(f"[Agent] Literature bootstrap failed: {e}")
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
