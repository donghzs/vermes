"""Blueprint: Chat（聊天核心路由）

Endpoints:
- POST /api/chat/completions  — Agent-powered 聊天（流式/非流式）
- GET  /api/chat/models       — 可用模型列表
"""

import asyncio
import base64 as b64mod
import json
import logging
import os
import secrets
import time
from typing import Optional

import yaml
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from hermes_cli.config import load_config, remove_env_value

_log = logging.getLogger(__name__)


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
    "deepseek": {"env_key": "DEEPSEEK_API_KEY", "base_url": "https://api.deepseek.com/v1"},
    "openai": {"env_key": "OPENAI_API_KEY", "base_url": "https://api.openai.com/v1"},
    "anthropic": {"env_key": "ANTHROPIC_API_KEY", "base_url": "https://api.anthropic.com/v1"},
    "gemini": {"env_key": "GEMINI_API_KEY", "base_url": "https://generativelanguage.googleapis.com/v1beta"},
    "openrouter": {"env_key": "OPENROUTER_API_KEY", "base_url": "https://openrouter.ai/api/v1"},
    "vbit": {"env_key": "VBIT_API_KEY", "base_url": "https://api.vbit.top/v1"},
    "alibaba": {"env_key": "QWEN_API_KEY", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1"},
    "qwen": {"env_key": "QWEN_API_KEY", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1"},
    "zhipu": {"env_key": "ZHIPU_API_KEY", "base_url": "https://open.bigmodel.cn/api/paas/v4"},
    "doubao": {"env_key": "DOUBAO_API_KEY", "base_url": "https://ark.cn-beijing.volces.com/api/v3"},
    "moonshot": {"env_key": "MOONSHOT_API_KEY", "base_url": "https://api.moonshot.cn/v1"},
    "baichuan": {"env_key": "BAICHUAN_API_KEY", "base_url": "https://api.baichuan-ai.com/v1"},
    "yi": {"env_key": "YI_API_KEY", "base_url": "https://api.lingyiwanwu.com/v1"},
    "spark": {"env_key": "SPARK_API_KEY", "base_url": "https://spark-api-open.xf-yun.com/v1"},
    "siliconflow": {"env_key": "SILICONFLOW_API_KEY", "base_url": "https://api.siliconflow.cn/v1"},
    "mistral": {"env_key": "MISTRAL_API_KEY", "base_url": "https://api.mistral.ai/v1"},
    "cohere": {"env_key": "COHERE_API_KEY", "base_url": "https://api.cohere.ai/v1"},
    "custom": {"env_key": "CUSTOM_API_KEY", "base_url": ""},
    "xiaomi": {"env_key": "XIAOMI_API_KEY", "base_url": "https://api.xiaomimimo.com/v1"},
    "ant-ling": {"env_key": "ANT_LING_API_KEY", "base_url": "https://api.ant-ling.com/v1"},
    "minimax": {"env_key": "MINIMAX_API_KEY", "base_url": "https://api.minimax.chat/v1"},
    "baidu": {"env_key": "BAIDU_API_KEY", "base_url": "https://qianfan.baidubce.com/v2"},
    "xinghuo": {"env_key": "XINGHUO_API_KEY", "base_url": "https://spark-api.xf-yun.com/v1"},
    "stepfun": {"env_key": "STEPFUN_API_KEY", "base_url": "https://api.stepfun.com/v1"},
    "groq": {"env_key": "GROQ_API_KEY", "base_url": "https://api.groq.com/openai/v1"},
    "together": {"env_key": "TOGETHER_API_KEY", "base_url": "https://api.together.xyz/v1"},
    "ollama": {"env_key": None, "base_url": "http://localhost:11434/v1"},
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
    }
    provider = provider.lower(); provider = provider_aliases.get(provider, provider)

    if not provider:
        cfg_path = home / "config.yaml"
        if cfg_path.exists():
            with open(cfg_path, encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            provider = cfg.get("model", {}).get("provider", "deepseek")
        else:
            provider = "deepseek"

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

    requested_model = req.model or "deepseek-chat"
    provider, base_url, api_key, model = _resolve_model_provider(requested_model, req.provider)

    if not base_url:
        raise HTTPException(status_code=500, detail=f"No base_url found for provider '{provider}'. Check config.yaml.")

    # Validate attachments
    validated_attachments, att_error = _validate_attachments(req.attachments)
    if att_error:
        print(f"[Attachment validation] {att_error}", flush=True)
    req.attachments = validated_attachments

    # Auto-claim trial token when no API key configured
    if (not api_key or api_key.startswith("unknown")) and provider != "ollama":
        if api_key and api_key.startswith("unknown"):
            api_key = ""
            remove_env_value("VBIT_API_KEY")
        env_hint = (PROVIDERS.get(provider) or {}).get("env_key", "API_KEY")
        if provider == "vbit":
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

    # Build conversation messages
    conversation_history = []
    for m in req.messages:
        content = m.content
        if isinstance(content, list):
            conversation_history.append({"role": m.role, "content": content})
        else:
            conversation_history.append({"role": m.role, "content": content})

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
                    data_url = f"data:{att.mime};base64,{att.data}"
                    parts.append({"type": "image_url", "image_url": {"url": data_url}})
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

    # All messages go through Agent mode
    use_agent_mode = True
    agent = None
    try:
        agent = AIAgent(
            base_url=base_url,
            api_key=api_key,
            provider=provider,
            model=model,
            max_iterations=1000,
            quiet_mode=True,
            verbose_logging=False,
            platform="web",
        )
        from tools.approval import enable_session_yolo, set_current_session_key
        _gui_sk = "gui-" + (getattr(agent, "session_id", "") or "default")
        set_current_session_key(_gui_sk)
        enable_session_yolo(_gui_sk)
    except ValueError as e:
        if "context window" in str(e).lower():
            print(f"[WARN] Model {model} context too small, falling back to proxy mode: {e}")
            use_agent_mode = False
        else:
            raise

    # Proxy call helper
    async def call_proxy():
        import httpx
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
            resp = await client.post(
                f"{base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": model, "messages": conversation_history, "stream": False},
            )
            return resp

    if req.stream:
        # Streaming mode
        _stream_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        async def stream_from_proxy():
            import httpx
            async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
                async with client.stream(
                    "POST",
                    f"{base_url.rstrip('/')}/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={"model": model, "messages": conversation_history, "stream": True},
                ) as resp:
                    if resp.status_code >= 400:
                        error_body = ""
                        async for chunk in resp.aiter_bytes():
                            error_body += chunk.decode(errors="replace")
                        try:
                            err_json = json.loads(error_body)
                            err_msg = err_json.get("error", {}).get("message", error_body[:200])
                        except Exception:
                            err_msg = error_body[:200]
                        _log.warning(f"[Proxy] 上游错误 {resp.status_code}: {err_msg}")
                        yield f'data: {json.dumps({"error": {"message": err_msg, "type": "one_api_error", "code": resp.status_code}})}\n\n'
                        yield "data: [DONE]\n\n"
                        return
                    async for line in resp.aiter_lines():
                        if line.startswith("data: "):
                            data_content = line[6:]
                            if data_content.strip() == "[DONE]":
                                break
                            try:
                                chunk_data = json.loads(data_content)
                                if "usage" in chunk_data and chunk_data["usage"].get("total_tokens", 0) > 0:
                                    _stream_usage["prompt_tokens"] = chunk_data["usage"].get("prompt_tokens", 0)
                                    _stream_usage["completion_tokens"] = chunk_data["usage"].get("completion_tokens", 0)
                                    _stream_usage["total_tokens"] = chunk_data["usage"]["total_tokens"]
                            except (json.JSONDecodeError, KeyError, TypeError):
                                pass
                            yield line + "\n\n"
            # Stream-end quota reporting
            wechat_openid = req.wechat_openid or os.environ.get("VERMES_WECHAT_OPENID", "")
            if provider == "vbit":
                _report_quota(wechat_openid, _stream_usage["total_tokens"], "流式")
            yield "data: [DONE]\n\n"

        if agent is None:
            return StreamingResponse(stream_from_proxy(), media_type="text/event-stream")
        else:
            # Agent mode streaming
            from hermes_cli.blueprints.state import _active_streams

            _delta_queue: asyncio.Queue = asyncio.Queue()
            _agent_done = asyncio.Event()
            _stream_id = secrets.token_urlsafe(16)
            _cancel_event = asyncio.Event()
            _active_streams[_stream_id] = _cancel_event

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
                    event = {
                        "type": "tool_start",
                        "tool_call_id": _tool_id,
                        "tool_name": tool_name,
                        "arguments": args or {},
                    }
                else:
                    event = {
                        "type": "tool_end",
                        "tool_call_id": kwargs.get("tool_id", secrets.token_urlsafe(8)),
                        "tool_name": tool_name,
                        "duration": kwargs.get("duration", 0),
                        "is_error": kwargs.get("is_error", False),
                        "result_preview": preview or "",
                    }
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

            def run_sync():
                try:
                    _log.info(f"[Stream] Agent starting, model={model}, provider={provider}, stream_id={_stream_id}")
                    agent.stream_delta_callback = stream_callback
                    agent.tool_progress_callback = tool_progress_handler
                    agent.step_callback = thinking_handler
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
                    agent_task = loop.run_in_executor(None, run_sync)

                    last_ping = time.time()

                    while not _agent_done.is_set() or not _delta_queue.empty():
                        if _cancel_event.is_set():
                            _log.info(f"[Stream] Frontend requested stop, stream_id={_stream_id}")
                            agent._interrupt_requested = True
                            agent_task.cancel()
                            break

                        if time.time() - last_ping > 15:
                            yield f'data: {json.dumps({"type": "ping"})}\n\n'
                            last_ping = time.time()

                        try:
                            delta = await asyncio.wait_for(_delta_queue.get(), timeout=1.0)
                        except asyncio.TimeoutError:
                            if agent_task.done():
                                if _delta_queue.empty():
                                    _agent_done.set()
                                    break
                                continue
                            continue

                        _log.info(f"[Stream] Queue item: type={type(delta).__name__}, size={_delta_queue.qsize()}")
                        if isinstance(delta, dict):
                            _log.info(f"[Stream] SSE dict event: type={delta.get('type')}")
                            yield f"data: {json.dumps(delta)}\n\n"
                            last_ping = time.time()
                        else:
                            chunk = {
                                "id": "vermes-agent",
                                "object": "chat.completion.chunk",
                                "created": int(time.time()),
                                "model": model,
                                "choices": [{"index": 0, "delta": {"content": delta}, "finish_reason": None}]
                            }
                            yield f"data: {json.dumps(chunk)}\n\n"
                            last_ping = time.time()
                finally:
                    _active_streams.pop(_stream_id, None)

                # Wait for agent and report quota
                try:
                    _agent_result = await agent_task
                except Exception:
                    _agent_result = {}

                _wechat_openid = req.wechat_openid or os.environ.get("VERMES_WECHAT_OPENID", "")
                if _wechat_openid and _agent_result and provider == "vbit":
                    _total = _agent_result.get("total_tokens", 0)
                    _report_quota(_wechat_openid, _total, "Agent流式")

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
        if agent is None:
            proxy_resp = await call_proxy()
            proxy_data = proxy_resp.json()
            msg = proxy_data.get("choices", [{}])[0].get("message", {})
            final_response = msg.get("content", "Proxy error")
            if "usage" in proxy_data and proxy_data["usage"]:
                _usage = {
                    "prompt_tokens": proxy_data["usage"].get("prompt_tokens", 0),
                    "completion_tokens": proxy_data["usage"].get("completion_tokens", 0),
                    "total_tokens": proxy_data["usage"].get("total_tokens", 0),
                }
        else:
            def run_sync():
                return agent.run_conversation(
                    user_message=user_message,
                    conversation_history=conversation_history[:-1] if len(conversation_history) > 1 else None,
                )

            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(None, run_sync)

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
        if provider == "vbit":
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


# ── Registration ─────────────────────────────────────────────────────

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


blueprint = None  # no APIRouter; uses register_to(app) pattern
