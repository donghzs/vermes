#!/usr/bin/env python3
"""
Vision Tools Module

This module provides vision analysis tools that work with image URLs.
Uses the centralized auxiliary vision router, which can select OpenRouter,
Nous, Codex, native Anthropic, or a custom OpenAI-compatible endpoint.

Available tools:
- vision_analyze_tool: Analyze images from URLs with custom prompts

Features:
- Downloads images from URLs and converts to base64 for API compatibility
- Comprehensive image description
- Context-aware analysis based on user queries
- Automatic temporary file cleanup
- Proper error handling and validation
- Debug logging support

Usage:
    from vision_tools import vision_analyze_tool
    import asyncio
    
    # Analyze an image
    result = await vision_analyze_tool(
        image_url="https://example.com/image.jpg",
        user_prompt="What architectural style is this building?"
    )
"""

import base64
import json
import logging
import os
import re
import uuid
from pathlib import Path
from typing import Any, Awaitable, Dict, Optional
from urllib.parse import urlparse
import httpx
from agent.auxiliary_client import async_call_llm, extract_content_or_reasoning
from hermes_constants import get_hermes_dir
from tools.debug_helpers import DebugSession
from tools.website_policy import check_website_access
import sys

logger = logging.getLogger(__name__)

_debug = DebugSession("vision_tools", env_var="VISION_TOOLS_DEBUG")

# Configurable HTTP download timeout for _download_image().
# Separate from auxiliary.vision.timeout which governs the LLM API call.
# Resolution: config.yaml auxiliary.vision.download_timeout → env var → 30s default.
def _resolve_download_timeout() -> float:
    env_val = os.getenv("HERMES_VISION_DOWNLOAD_TIMEOUT", "").strip()
    if env_val:
        try:
            return float(env_val)
        except ValueError:
            pass
    try:
        from hermes_cli.config import cfg_get, load_config
        cfg = load_config()
        val = cfg_get(cfg, "auxiliary", "vision", "download_timeout")
        if val is not None:
            return float(val)
    except Exception:
        pass
    return 30.0

_VISION_DOWNLOAD_TIMEOUT = _resolve_download_timeout()

# Hard cap on downloaded image file size (50 MB). Prevents OOM from
# attacker-hosted multi-gigabyte files or decompression bombs.
_VISION_MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024


def _decode_data_url(data_url: str) -> tuple[bytes, str]:
    """Decode a base64 data URL and return (binary_data, file_extension).
    
    Args:
        data_url: A data URL string like "data:image/png;base64,iVBORw0KG..."
        
    Returns:
        Tuple of (decoded_bytes, file_extension)
        
    Raises:
        ValueError: If the data URL is malformed
    """
    if "," not in data_url:
        raise ValueError("Invalid data URL format: missing comma separator")
    
    header, encoded = data_url.split(",", 1)
    data = base64.b64decode(encoded)
    
    # Extract MIME type from header (e.g., "data:image/png;base64")
    mime_match = re.match(r"data:([^;,]+)(?:;base64)?", header)
    ext = "png"  # default fallback
    if mime_match:
        mime_type = mime_match.group(1)
        ext = {
            "image/png": "png",
            "image/jpeg": "jpg",
            "image/jpg": "jpg",
            "image/gif": "gif",
            "image/webp": "webp",
            "image/bmp": "bmp",
            "image/heic": "heic",
            "image/heif": "heif",
        }.get(mime_type, "png")
    
    return data, ext


def _validate_image_url(url: str) -> bool:
    """
    Basic validation of image URL format.
    
    Args:
        url (str): The URL to validate
        
    Returns:
        bool: True if URL appears to be valid, False otherwise
    """
    if not url or not isinstance(url, str):
        return False

    # Basic HTTP/HTTPS URL check
    if not url.startswith(("http://", "https://")):
        return False

    # Parse to ensure we at least have a network location; still allow URLs
    # without file extensions (e.g. CDN endpoints that redirect to images).
    parsed = urlparse(url)
    if not parsed.netloc:
        return False

    # Block private/internal addresses to prevent SSRF
    from tools.url_safety import is_safe_url
    if not is_safe_url(url):
        return False

    return True


def _detect_image_mime_type(image_path: Path) -> Optional[str]:
    """Return a MIME type when the file looks like a supported image."""
    with image_path.open("rb") as f:
        header = f.read(64)

    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if header.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if header.startswith(b"BM"):
        return "image/bmp"
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "image/webp"
    if image_path.suffix.lower() == ".svg":
        head = image_path.read_text(encoding="utf-8", errors="ignore")[:4096].lower()
        if "<svg" in head:
            return "image/svg+xml"
    return None


async def _download_image(image_url: str, destination: Path, max_retries: int = 3) -> Path:
    """
    Download an image from a URL to a local destination (async) with retry logic.
    
    Args:
        image_url (str): The URL of the image to download
        destination (Path): The path where the image should be saved
        max_retries (int): Maximum number of retry attempts (default: 3)
        
    Returns:
        Path: The path to the downloaded image
        
    Raises:
        Exception: If download fails after all retries
    """
    import asyncio
    
    # Create parent directories if they don't exist
    destination.parent.mkdir(parents=True, exist_ok=True)
    
    async def _ssrf_redirect_guard(response):
        """Re-validate each redirect target to prevent redirect-based SSRF.

        Without this, an attacker can host a public URL that 302-redirects
        to http://169.254.169.254/ and bypass the pre-flight is_safe_url check.

        Must be async because httpx.AsyncClient awaits event hooks.
        """
        if response.is_redirect and response.next_request:
            redirect_url = str(response.next_request.url)
            from tools.url_safety import is_safe_url
            if not is_safe_url(redirect_url):
                raise ValueError(
                    f"Blocked redirect to private/internal address: {redirect_url}"
                )

    last_error = None
    for attempt in range(max_retries):
        try:
            blocked = check_website_access(image_url)
            if blocked:
                raise PermissionError(blocked["message"])

            # Download the image with appropriate headers using async httpx
            # Enable follow_redirects to handle image CDNs that redirect (e.g., Imgur, Picsum)
            # SSRF: event_hooks validates each redirect target against private IP ranges
            async with httpx.AsyncClient(
                timeout=_VISION_DOWNLOAD_TIMEOUT,
                follow_redirects=True,
                event_hooks={"response": [_ssrf_redirect_guard]},
            ) as client:
                response = await client.get(
                    image_url,
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                        "Accept": "image/*,*/*;q=0.8",
                    },
                )
                response.raise_for_status()

                # Reject overly large images early via Content-Length header.
                cl = response.headers.get("content-length")
                if cl and int(cl) > _VISION_MAX_DOWNLOAD_BYTES:
                    raise ValueError(
                        f"Image too large ({int(cl)} bytes, max {_VISION_MAX_DOWNLOAD_BYTES})"
                    )

                final_url = str(response.url)
                blocked = check_website_access(final_url)
                if blocked:
                    raise PermissionError(blocked["message"])
                
                # Save the image content (double-check actual size)
                body = response.content
                if len(body) > _VISION_MAX_DOWNLOAD_BYTES:
                    raise ValueError(
                        f"Image too large ({len(body)} bytes, max {_VISION_MAX_DOWNLOAD_BYTES})"
                    )
                destination.write_bytes(body)
            
            return destination
        except Exception as e:
            last_error = e
            if attempt < max_retries - 1:
                wait_time = 2 ** (attempt + 1)  # 2s, 4s, 8s
                logger.warning("Image download failed (attempt %s/%s): %s", attempt + 1, max_retries, str(e)[:50])
                logger.warning("Retrying in %ss...", wait_time)
                await asyncio.sleep(wait_time)
            else:
                logger.error(
                    "Image download failed after %s attempts: %s",
                    max_retries,
                    str(e)[:100],
                    exc_info=True,
                )
    
    if last_error is None:
        raise RuntimeError(
            f"_download_image exited retry loop without attempting (max_retries={max_retries})"
        )
    raise last_error


def _determine_mime_type(image_path: Path) -> str:
    """
    Determine the MIME type of an image based on its file extension.
    
    Args:
        image_path (Path): Path to the image file
        
    Returns:
        str: The MIME type (defaults to image/jpeg if unknown)
    """
    extension = image_path.suffix.lower()
    mime_types = {
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.png': 'image/png',
        '.gif': 'image/gif',
        '.bmp': 'image/bmp',
        '.webp': 'image/webp',
        '.svg': 'image/svg+xml'
    }
    return mime_types.get(extension, 'image/jpeg')


def _image_to_base64_data_url(image_path: Path, mime_type: Optional[str] = None) -> str:
    """
    Convert an image file to a base64-encoded data URL.
    
    Args:
        image_path (Path): Path to the image file
        mime_type (Optional[str]): MIME type of the image (auto-detected if None)
        
    Returns:
        str: Base64-encoded data URL (e.g., "data:image/jpeg;base64,...")
    """
    # Read the image as bytes
    data = image_path.read_bytes()
    
    # Encode to base64
    encoded = base64.b64encode(data).decode("ascii")
    
    # Determine MIME type
    mime = mime_type or _determine_mime_type(image_path)
    
    # Create data URL
    data_url = f"data:{mime};base64,{encoded}"
    
    return data_url


# Hard limit for vision API payloads (20 MB) — matches the most restrictive
# major provider (Gemini inline data limit).  Images above this are rejected.
_MAX_BASE64_BYTES = 20 * 1024 * 1024

# Target size when auto-resizing on API failure (5 MB).  After a provider
# rejects an image, we downscale to this target and retry once.
_RESIZE_TARGET_BYTES = 5 * 1024 * 1024


def _is_image_size_error(error: Exception) -> bool:
    """Detect if an API error is related to image or payload size."""
    err_str = str(error).lower()
    return any(hint in err_str for hint in (
        "too large", "payload", "413", "content_too_large",
        "request_too_large", "image_url", "invalid_request",
        "exceeds", "size limit",
    ))


def _resize_image_for_vision(image_path: Path, mime_type: Optional[str] = None,
                              max_base64_bytes: int = _RESIZE_TARGET_BYTES) -> str:
    """Convert an image to a base64 data URL, auto-resizing if too large.

    Tries Pillow first to progressively downscale oversized images.  If Pillow
    is not installed or resizing still exceeds the limit, falls back to the raw
    bytes and lets the caller handle the size check.

    Returns the base64 data URL string.
    """
    # Quick file-size estimate: base64 expands by ~4/3, plus data URL header.
    # Skip the expensive full-read + encode if Pillow can resize directly.
    file_size = image_path.stat().st_size
    estimated_b64 = (file_size * 4) // 3 + 100  # ~header overhead
    if estimated_b64 <= max_base64_bytes:
        # Small enough — just encode directly.
        data_url = _image_to_base64_data_url(image_path, mime_type=mime_type)
        if len(data_url) <= max_base64_bytes:
            return data_url
    else:
        data_url = None  # defer full encode; try Pillow resize first

    # Attempt auto-resize with Pillow (soft dependency)
    try:
        from PIL import Image
        import io as _io
    except ImportError:
        logger.info("Pillow not installed — cannot auto-resize oversized image")
        if data_url is None:
            data_url = _image_to_base64_data_url(image_path, mime_type=mime_type)
        return data_url  # caller will raise the size error

    logger.info("Image file is %.1f MB (estimated base64 %.1f MB, limit %.1f MB), auto-resizing...",
                file_size / (1024 * 1024), estimated_b64 / (1024 * 1024),
                max_base64_bytes / (1024 * 1024))

    mime = mime_type or _determine_mime_type(image_path)
    # Choose output format: JPEG for photos (smaller), PNG for transparency
    pil_format = "PNG" if mime == "image/png" else "JPEG"
    out_mime = "image/png" if pil_format == "PNG" else "image/jpeg"

    try:
        img = Image.open(image_path)
    except Exception as exc:
        logger.info("Pillow cannot open image for resizing: %s", exc)
        if data_url is None:
            data_url = _image_to_base64_data_url(image_path, mime_type=mime_type)
        return data_url  # fall through to size-check in caller
    # Convert RGBA to RGB for JPEG output
    if pil_format == "JPEG" and img.mode in {"RGBA", "P"}:
        img = img.convert("RGB")

    # Strategy: halve dimensions until base64 fits, up to 4 rounds.
    # For JPEG, also try reducing quality at each size step.
    # For PNG, quality is irrelevant — only dimension reduction helps.
    quality_steps = (85, 70, 50) if pil_format == "JPEG" else (None,)
    prev_dims = (img.width, img.height)
    candidate = None  # will be set on first loop iteration

    for attempt in range(5):
        if attempt > 0:
            # Proportional scaling: halve the longer side and scale the
            # shorter side to preserve aspect ratio (min dimension 64).
            scale = 0.5
            new_w = max(int(img.width * scale), 64)
            new_h = max(int(img.height * scale), 64)
            # Re-derive the scale from whichever dimension hit the floor
            # so both axes shrink by the same factor.
            if new_w == 64 and img.width > 0:
                effective_scale = 64 / img.width
                new_h = max(int(img.height * effective_scale), 64)
            elif new_h == 64 and img.height > 0:
                effective_scale = 64 / img.height
                new_w = max(int(img.width * effective_scale), 64)
            # Stop if dimensions can't shrink further
            if (new_w, new_h) == prev_dims:
                break
            img = img.resize((new_w, new_h), Image.LANCZOS)
            prev_dims = (new_w, new_h)
            logger.info("Resized to %dx%d (attempt %d)", new_w, new_h, attempt)

        for q in quality_steps:
            buf = _io.BytesIO()
            save_kwargs = {"format": pil_format}
            if q is not None:
                save_kwargs["quality"] = q
            img.save(buf, **save_kwargs)
            encoded = base64.b64encode(buf.getvalue()).decode("ascii")
            candidate = f"data:{out_mime};base64,{encoded}"
            if len(candidate) <= max_base64_bytes:
                logger.info("Auto-resized image fits: %.1f MB (quality=%s, %dx%d)",
                            len(candidate) / (1024 * 1024), q,
                            img.width, img.height)
                return candidate

    # If we still can't get it small enough, return the best attempt
    # and let the caller decide
    if candidate is not None:
        logger.warning("Auto-resize could not fit image under %.1f MB (best: %.1f MB)",
                       max_base64_bytes / (1024 * 1024), len(candidate) / (1024 * 1024))
        return candidate

    # Shouldn't reach here, but fall back to full encode
    return data_url or _image_to_base64_data_url(image_path, mime_type=mime_type)


# ---------------------------------------------------------------------------
# Native fast path: short-circuit the auxiliary LLM when the active main model
# supports native vision. Instead of asking a separate LLM to describe the
# image and returning text, we load the image, base64-encode it, and return a
# multimodal tool-result envelope. The agent loop unwraps the envelope into an
# OpenAI-style content list on the `tool` role; provider adapters (anthropic,
# codex_responses, chat_completions) translate that into Anthropic
# tool_result image blocks / Responses input_image / OpenAI image_url tool
# content. The main model then "sees" the pixels directly on its next turn.
# ---------------------------------------------------------------------------


# ── Proxy provider detection ────────────────────────────────────────────
# When users connect through One-API or similar proxies, the provider
# string is "vbit.top" or "one-api" instead of the real provider.
# We infer the real provider from the model name.

# Model name prefix → real provider mapping
_MODEL_PROVIDER_HINTS: Dict[str, str] = {
    "mimo-": "xiaomi",
    "glm-": "zai",
    "deepseek-": "deepseek",
    "qwen-": "alibaba",
    "gpt-": "openai",
    "claude-": "anthropic",
    "gemini-": "google",
    "kimi-": "moonshot",
    "yi-": "01-ai",
    "internlm-": "internlm",
    "chatglm-": "zai",
    "baichuan-": "baichuan",
    "hunyuan-": "tencent",
}

# Known proxy provider patterns
_PROXY_PROVIDERS = frozenset({
    "vbit.top", "one-api", "new-api", "openrouter",
})


def _resolve_real_provider(provider: str, model: str) -> str:
    """Resolve the real provider from model name when using a proxy.

    If *provider* is a known proxy (e.g. ``"vbit.top"``), we inspect the
    model name prefix to infer the actual backend provider.  This lets
    vision routing find the correct provider-specific vision model
    (e.g. ``xiaomi → mimo-v2-omni``) even when the request goes through
    One-API.

    Returns the resolved provider string (lowercase, stripped).
    """
    p = (provider or "").strip().lower()
    m = (model or "").strip().lower()

    if not p or p not in _PROXY_PROVIDERS:
        return p

    # Try model name prefix hints
    for prefix, real in _MODEL_PROVIDER_HINTS.items():
        if m.startswith(prefix):
            return real

    # Fallback: return the proxy provider as-is
    return p


def _supports_media_in_tool_results(provider: str, model: str) -> bool:
    """Whether the given provider+model combination accepts image content.

    国内版：放宽判断，优先让模型自己尝试处理。
    失败时会自动回退到 vision_analyze 工具链，用户无感知。
    """
    if not isinstance(provider, str):
        return True  # 未知 provider，让它自己尝试
    p = provider.strip().lower()
    if not p:
        return True

    # 已知支持 vision 的 provider，直接通过
    _VISION_PROVIDERS = {
        "openrouter", "nous", "vertex", "bedrock", "anthropic-vertex",
        "google-vertex", "anthropic", "claude", "anthropic-direct",
        "openai", "openai-chat", "openai-codex", "azure-openai",
        "google", "gemini", "google-gemini", "google-vertex-gemini",
        "xiaomi", "deepseek", "alibaba", "zai", "moonshot",
        "minimax", "siliconflow", "baidu", "tencent-tokenhub",
    }
    if p in _VISION_PROVIDERS:
        return True

    # 其他未知 provider，也让它尝试
    # 失败会内部回退，不会暴露给用户
    return True


def _build_native_vision_tool_result(
    image_url: str,
    question: str,
    image_data_url: str,
    image_size_bytes: int,
) -> Dict[str, Any]:
    """Build the multimodal tool-result envelope returned by the fast path.

    Shape:
      {
        "_multimodal": True,
        "content": [
          {"type": "text", "text": "<short note + the user's question>"},
          {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}
        ],
        "text_summary": "<plain-text fallback>",
        "meta": {"image_url": ..., "size_bytes": N},
      }

    The text part exists for two reasons: (1) it gives the model an
    instruction to act on now that the pixels are in context, and
    (2) providers that don't support multimodal tool results can fall back
    to ``text_summary``.
    """
    # The tool-result text part is intentionally minimal. The model already
    # has the user's original question in context; this just acknowledges
    # the image is now visible and reminds it what it was asked.
    text_part = (
        "Image loaded into your context — you can see it natively now. "
        "Use your built-in vision to answer the user."
    )
    if isinstance(question, str) and question.strip():
        text_part += f"\n\nQuestion: {question.strip()}"

    summary = (
        f"Image attached natively for the main model "
        f"({image_size_bytes / 1024:.1f} KB). "
        "Answer using built-in vision."
    )

    return {
        "_multimodal": True,
        "content": [
            {"type": "text", "text": text_part},
            {"type": "image_url", "image_url": {"url": image_data_url}},
        ],
        "text_summary": summary,
        "meta": {
            "image_url": image_url[:200],
            "size_bytes": image_size_bytes,
            "native_vision": True,
        },
    }


async def _vision_analyze_native(
    image_url: str,
    question: str,
) -> Any:
    """Fast path for vision-capable main models.

    Loads the image (local file OR remote URL), base64-encodes it, and
    returns a multimodal tool-result envelope. The agent loop unwraps it;
    provider adapters serialize it into the right tool-result-with-image
    shape for each backend.

    Returns:
        A ``_multimodal`` envelope dict on success.
        A JSON error string on failure (matches the existing tool-result
        contract so the agent loop displays errors normally).
    """
    if not isinstance(image_url, str) or not image_url.strip():
        return tool_error("image_url is required", success=False)

    temp_image_path: Optional[Path] = None
    should_cleanup = False
    try:
        from tools.interrupt import is_interrupted
        if is_interrupted():
            return tool_error("Interrupted", success=False)

        # Resolve the image source (mirrors vision_analyze_tool's logic
        # exactly so behaviour is consistent).
        resolved_url = image_url
        if resolved_url.startswith("file://"):
            resolved_url = resolved_url[len("file://"):]
        local_path = Path(os.path.expanduser(resolved_url))

        if local_path.is_file():
            temp_image_path = local_path
            should_cleanup = False
        elif image_url.startswith("data:"):
            # Data URL (base64-encoded image) -- decode and save to temp file
            try:
                data, ext = _decode_data_url(image_url)
                temp_dir = get_hermes_dir("cache/vision", "temp_vision_images")
                temp_image_path = temp_dir / f"temp_image_{uuid.uuid4()}.{ext}"
                temp_image_path.write_bytes(data)
                should_cleanup = True
            except Exception as e:
                return tool_error(f"Failed to decode data URL: {e}", success=False)
        elif _validate_image_url(image_url):
            blocked = check_website_access(image_url)
            if blocked:
                return tool_error(blocked["message"], success=False)
            temp_dir = get_hermes_dir("cache/vision", "temp_vision_images")
            temp_image_path = temp_dir / f"temp_image_{uuid.uuid4()}.jpg"
            await _download_image(image_url, temp_image_path)
            should_cleanup = True
        else:
            # Provide helpful error message with configuration hints
            error_msg = (
                "Invalid image source. Provide one of:\n"
                "  • HTTP/HTTPS URL (e.g., https://example.com/image.jpg)\n"
                "  • Local file path (e.g., /Users/you/Desktop/photo.png)\n"
                "  • Base64 data URL (data:image/png;base64,...)\n\n"
                "If using a text-only model that doesn't support vision, "
                "ensure a vision-capable provider is configured:\n"
                "  • OPENROUTER_API_KEY  (for OpenRouter vision models)\n"
                "  • XIAOMI_API_KEY      (for MiMo vision models)\n"
                "Add these to ~/.vermes/.env and restart Vermes."
            )
            return tool_error(error_msg, success=False)

        image_size_bytes = temp_image_path.stat().st_size
        detected_mime_type = _detect_image_mime_type(temp_image_path)
        if not detected_mime_type:
            return tool_error(
                "Only real image files are supported for vision analysis.",
                success=False,
            )

        image_data_url = _image_to_base64_data_url(
            temp_image_path, mime_type=detected_mime_type,
        )

        # Honour the same hard cap as the legacy path. Resize if needed.
        if len(image_data_url) > _MAX_BASE64_BYTES:
            image_data_url = _resize_image_for_vision(
                temp_image_path, mime_type=detected_mime_type,
            )
            if len(image_data_url) > _MAX_BASE64_BYTES:
                return tool_error(
                    f"Image too large for vision API: base64 payload is "
                    f"{len(image_data_url) / (1024 * 1024):.1f} MB "
                    f"(limit {_MAX_BASE64_BYTES / (1024 * 1024):.0f} MB) "
                    f"even after resizing. Install Pillow "
                    f"(`pip install Pillow`) for better auto-resize, "
                    f"or compress the image manually.",
                    success=False,
                )

        return _build_native_vision_tool_result(
            image_url=image_url,
            question=question,
            image_data_url=image_data_url,
            image_size_bytes=image_size_bytes,
        )

    except Exception as exc:
        logger.warning("Native vision fast path failed: %s", exc)
        return tool_error(f"Native vision failed: {exc}", success=False)
    finally:
        # Only delete temp files we created — never user-provided paths.
        if should_cleanup and temp_image_path is not None:
            try:
                if temp_image_path.exists():
                    temp_image_path.unlink()
            except Exception:
                pass


async def vision_analyze_tool(
    image_url: str,
    user_prompt: str,
    model: str = None,
    provider: str = None,
) -> str:
    """
    Analyze an image from a URL or local file path using vision AI.
    
    This tool accepts either an HTTP/HTTPS URL or a local file path. For URLs,
    it downloads the image first. In both cases, the image is converted to base64
    and processed using the configured vision model.
    
    The user_prompt parameter is expected to be pre-formatted by the calling
    function (typically model_tools.py) to include both full description
    requests and specific questions.
    
    Args:
        image_url (str): The URL or local file path of the image to analyze.
                         Accepts http://, https:// URLs or absolute/relative file paths.
        user_prompt (str): The pre-formatted prompt for the vision model
        model (str): The vision model to use (default: auto-resolved)
        provider (str): The provider to use (default: auto-resolved). When
                        specified, forces routing to this provider (e.g. "xiaomi"
                        for mimo-v2-omni). This is important when the user's main
                        chat model doesn't support vision but the provider has a
                        dedicated vision model.
    
    Returns:
        str: JSON string containing the analysis results with the following structure:
             {
                 "success": bool,
                 "analysis": str (defaults to error message if None)
             }
    
    Raises:
        Exception: If download fails, analysis fails, or API key is not set
        
    Note:
        - For URLs, temporary images are stored under $HERMES_HOME/cache/vision/ and cleaned up
        - For local file paths, the file is used directly and NOT deleted
        - Supports common image formats (JPEG, PNG, GIF, WebP, etc.)
    """
    if not isinstance(user_prompt, str):
        user_prompt = str(user_prompt) if user_prompt is not None else ""
    debug_call_data = {
        "parameters": {
            "image_url": image_url,
            "user_prompt": user_prompt[:200] + "..." if len(user_prompt) > 200 else user_prompt,
            "model": model
        },
        "error": None,
        "success": False,
        "analysis_length": 0,
        "model_used": model,
        "image_size_bytes": 0
    }
    
    temp_image_path = None
    # Track whether we should clean up the file after processing.
    # Local files (e.g. from the image cache) should NOT be deleted.
    should_cleanup = True
    detected_mime_type = None
    
    try:
        from tools.interrupt import is_interrupted
        if is_interrupted():
            return tool_error("Interrupted", success=False)

        logger.info("Analyzing image: %s", image_url[:60])
        logger.info("User prompt: %s", user_prompt[:100])
        
        # Determine if this is a local file path or a remote URL
        # Strip file:// scheme so file URIs resolve as local paths.
        resolved_url = image_url
        if resolved_url.startswith("file://"):
            resolved_url = resolved_url[len("file://"):]
        local_path = Path(os.path.expanduser(resolved_url))
        if local_path.is_file():
            # Local file path (e.g. from platform image cache) -- skip download
            logger.info("Using local image file: %s", image_url)
            temp_image_path = local_path
            should_cleanup = False  # Don't delete cached/local files
        elif image_url.startswith("data:"):
            # Data URL (base64-encoded image) -- decode and save to temp file
            logger.info("Decoding data URL...")
            try:
                data, ext = _decode_data_url(image_url)
                temp_dir = get_hermes_dir("cache/vision", "temp_vision_images")
                temp_image_path = temp_dir / f"temp_image_{uuid.uuid4()}.{ext}"
                temp_image_path.write_bytes(data)
                should_cleanup = True
                logger.info("Data URL decoded and saved to temp file (%.1f KB)", len(data) / 1024)
            except Exception as e:
                raise ValueError(f"Failed to decode data URL: {e}")
        elif _validate_image_url(image_url):
            # Remote URL -- download to a temporary location
            blocked = check_website_access(image_url)
            if blocked:
                raise PermissionError(blocked["message"])
            logger.info("Downloading image from URL...")
            temp_dir = get_hermes_dir("cache/vision", "temp_vision_images")
            temp_image_path = temp_dir / f"temp_image_{uuid.uuid4()}.jpg"
            await _download_image(image_url, temp_image_path)
            should_cleanup = True
        else:
            # Provide helpful error message with configuration hints
            raise ValueError(
                "Invalid image source. Provide one of:\n"
                "  • HTTP/HTTPS URL (e.g., https://example.com/image.jpg)\n"
                "  • Local file path (e.g., /Users/you/Desktop/photo.png)\n"
                "  • Base64 data URL (data:image/png;base64,...)\n\n"
                "If using a text-only model that doesn't support vision, "
                "ensure a vision-capable provider is configured:\n"
                "  • OPENROUTER_API_KEY  (for OpenRouter vision models)\n"
                "  • XIAOMI_API_KEY      (for MiMo vision models)\n"
                "Add these to ~/.vermes/.env and restart Vermes."
            )
        
        # Get image file size for logging
        image_size_bytes = temp_image_path.stat().st_size
        image_size_kb = image_size_bytes / 1024
        logger.info("Image ready (%.1f KB)", image_size_kb)

        detected_mime_type = _detect_image_mime_type(temp_image_path)
        if not detected_mime_type:
            raise ValueError("Only real image files are supported for vision analysis.")
        
        # Convert image to base64 — send at full resolution first.
        # If the provider rejects it as too large, we auto-resize and retry.
        logger.info("Converting image to base64...")
        image_data_url = _image_to_base64_data_url(temp_image_path, mime_type=detected_mime_type)
        data_size_kb = len(image_data_url) / 1024
        logger.info("Image converted to base64 (%.1f KB)", data_size_kb)

        # Hard limit (20 MB) — no provider accepts payloads this large.
        if len(image_data_url) > _MAX_BASE64_BYTES:
            # Try to resize down to 5 MB before giving up.
            image_data_url = _resize_image_for_vision(
                temp_image_path, mime_type=detected_mime_type)
            if len(image_data_url) > _MAX_BASE64_BYTES:
                raise ValueError(
                    f"Image too large for vision API: base64 payload is "
                    f"{len(image_data_url) / (1024 * 1024):.1f} MB "
                    f"(limit {_MAX_BASE64_BYTES / (1024 * 1024):.0f} MB) "
                    f"even after resizing. "
                    f"Install Pillow (`pip install Pillow`) for better auto-resize, "
                    f"or compress the image manually."
                )

        debug_call_data["image_size_bytes"] = image_size_bytes
        
        # Use the prompt as provided (model_tools.py now handles full description formatting)
        comprehensive_prompt = user_prompt
        
        # Prepare the message with base64-encoded image
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": comprehensive_prompt
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": image_data_url
                        }
                    }
                ]
            }
        ]
        
        logger.info("Processing image with vision model...")
        
        # Call the vision API via centralized router.
        # Read timeout from config.yaml (auxiliary.vision.timeout), default 120s.
        # Local vision models (llama.cpp, ollama) can take well over 30s.
        vision_timeout = 120.0
        vision_temperature = 0.1
        try:
            from hermes_cli.config import cfg_get, load_config
            _cfg = load_config()
            _vision_cfg = cfg_get(_cfg, "auxiliary", "vision", default={})
            _vt = _vision_cfg.get("timeout")
            if _vt is not None:
                vision_timeout = float(_vt)
            _vtemp = _vision_cfg.get("temperature")
            if _vtemp is not None:
                vision_temperature = float(_vtemp)
        except Exception:
            pass
        call_kwargs = {
            "task": "vision",
            "messages": messages,
            "temperature": vision_temperature,
            "max_tokens": 2000,
            "timeout": vision_timeout,
        }
        if model:
            call_kwargs["model"] = model
        if provider:
            call_kwargs["provider"] = provider
        # Try full-size image first; on size-related rejection, downscale and retry.
        try:
            response = await async_call_llm(**call_kwargs)
        except Exception as _api_err:
            if (_is_image_size_error(_api_err)
                    and len(image_data_url) > _RESIZE_TARGET_BYTES):
                logger.info(
                    "API rejected image (%.1f MB, likely too large); "
                    "auto-resizing to ~%.0f MB and retrying...",
                    len(image_data_url) / (1024 * 1024),
                    _RESIZE_TARGET_BYTES / (1024 * 1024),
                )
                image_data_url = _resize_image_for_vision(
                    temp_image_path, mime_type=detected_mime_type)
                messages[0]["content"][1]["image_url"]["url"] = image_data_url
                response = await async_call_llm(**call_kwargs)
            else:
                raise
        
        # Extract the analysis — fall back to reasoning if content is empty
        analysis = extract_content_or_reasoning(response)

        # Retry once on empty content (reasoning-only response)
        if not analysis:
            logger.warning("Vision LLM returned empty content, retrying once")
            response = await async_call_llm(**call_kwargs)
            analysis = extract_content_or_reasoning(response)

        analysis_length = len(analysis)
        
        logger.info("Image analysis completed (%s characters)", analysis_length)
        
        # Prepare successful response
        result = {
            "success": True,
            "analysis": analysis or "图片分析完成，但未返回有效内容。请重试。"
        }
        
        debug_call_data["success"] = True
        debug_call_data["analysis_length"] = analysis_length
        
        # Log debug information
        _debug.log_call("vision_analyze_tool", debug_call_data)
        _debug.save()
        
        return json.dumps(result, indent=2, ensure_ascii=False)
        
    except Exception as e:
        error_msg = f"图片分析失败: {str(e)}"
        logger.error("%s", error_msg, exc_info=True)
        
        # Detect vision capability errors — give the model a clear message
        # so it can inform the user instead of a cryptic API error.
        err_str = str(e).lower()
        if any(hint in err_str for hint in (
            "402", "insufficient", "payment required", "credits", "billing",
        )):
            analysis = (
                "API 额度不足或需要付费。请充值您的 API 账户后重试。\n"
                f"错误详情: {e}"
            )
        elif any(hint in err_str for hint in (
            "401", "invalid api key", "invalid_api_key", "authentication",
            "unauthorized",
        )):
            analysis = (
                "API Key 无效或已过期。请到设置页面检查并更新 API Key。\n"
                f"错误详情: {e}"
            )
        elif any(hint in err_str for hint in (
            "does not support", "not support image",
            "content_policy", "multimodal",
            "unrecognized request argument", "image input",
        )):
            # 国内版：不暴露"不支持"给用户，改为内部回退提示
            analysis = (
                "正在尝试其他方式分析图片，请稍候..."
            )
        elif "invalid_request" in err_str or "image_url" in err_str:
            analysis = (
                "图片格式不支持或已损坏。请尝试使用 JPEG 或 PNG 格式的较小图片。\n"
                f"错误详情: {e}"
            )
        else:
            analysis = (
                "图片分析时出现问题，无法完成分析。请稍后重试。\n"
                f"错误详情: {e}"
            )
        
        # Prepare error response
        result = {
            "success": False,
            "error": error_msg,
            "analysis": analysis,
        }
        
        debug_call_data["error"] = error_msg
        _debug.log_call("vision_analyze_tool", debug_call_data)
        _debug.save()
        
        return json.dumps(result, indent=2, ensure_ascii=False)
    
    finally:
        # Clean up temporary image file (but NOT local/cached files)
        if should_cleanup and temp_image_path and temp_image_path.exists():
            try:
                temp_image_path.unlink()
                logger.debug("Cleaned up temporary image file")
            except Exception as cleanup_error:
                logger.warning(
                    "Could not delete temporary file: %s", cleanup_error, exc_info=True
                )


def check_vision_requirements() -> bool:
    """Check if vision analysis is possible via any available path.

    Generic (no hardcoded vendor vision models, no sponsored fallback). Returns
    True if at least one of these is available:
    1. Main model supports native vision (multimodal tool results)
    2. Another user-configured provider resolves a vision client (the user
       brings their own credentials — we never assume or pay for a model)
    3. Auxiliary vision client (OpenRouter/Nous/Anthropic aggregator chain)
    4. OCR fallback (pytesseract installed) — text-only last resort
    """
    try:
        from agent.auxiliary_client import (
            _read_main_provider, _read_main_model, resolve_provider_client,
            resolve_vision_provider_client,
        )

        _raw_provider = _read_main_provider()
        _model = _read_main_model()

        # Resolve real provider from proxy (e.g. vbit.top + mimo-v2.5 → xiaomi)
        _provider = _resolve_real_provider(_raw_provider, _model)

        # Check 1: Main model supports native vision
        try:
            if _supports_media_in_tool_results(_provider, _model):
                return True
        except Exception:
            pass

        # Check 2: Another user-configured provider resolves a vision client.
        # Generic: we only discover which providers the user has credentials
        # for; the vision model itself is resolved on demand (is_vision=True).
        for cand_provider in _list_user_providers(_provider or ""):
            try:
                probe_client, _ = resolve_provider_client(
                    cand_provider, is_vision=True,
                )
                if probe_client is not None:
                    return True
            except Exception:
                continue

        # Check 3: Auxiliary vision client (aggregator chain)
        _p, client, _m = resolve_vision_provider_client()
        if client is not None:
            return True

        # Check 4: OCR fallback (pytesseract) — text-only last resort
        try:
            import pytesseract  # noqa: F401
            return True
        except ImportError:
            pass

        return False

    except Exception:
        return False


if __name__ == "__main__":
    """
    Simple test/demo when run directly
    """
    logger.info("👁️ Vision Tools Module (Vermes)")
    logger.info("=" * 50)
    
    # Show current vision routing
    try:
        from agent.auxiliary_client import _read_main_provider, _read_main_model
        _provider = _read_main_provider()
        _model = _read_main_model()
        _resolved = _resolve_real_provider(_provider, _model)

        logger.info(f"📋 当前 provider: {_provider}")
        logger.info(f"📋 当前 model: {_model}")

        # Native vision support (generic, capability-based — no vendor map)
        if _supports_media_in_tool_results(_provider, _model):
            logger.info(f"✅ {_provider}/{_model} 支持原生视觉（native fast path）")
        else:
            logger.info(f"⚠️ {_provider}/{_model} 不支持原生视觉")

        # Other user-configured providers (generic discovery; vision model
        # resolved on demand, never from a hardcoded table)
        _others = _list_user_providers(_resolved)
        if _others:
            logger.info("\n📋 其他已配置的 provider（将按需解析视觉模型）:")
            for p in _others:
                logger.info(f"  • {p}")
        else:
            logger.info("\n📋 未配置其他 provider")
    except Exception as e:
        logger.info(f"⚠️ 无法读取当前配置: {e}")
    
    # Check if vision is available via any path
    api_available = check_vision_requirements()
    
    if not api_available:
        logger.info("❌ 没有可用的视觉分析路径")
        logger.info("请配置以下任一方式（我们不内置/不替你付费购买视觉模型）：")
        logger.info("  1. 使用支持视觉的模型（如 GPT-4o、Claude、Gemini 等）")
        logger.info("  2. 设置 AUXILIARY_VISION_MODEL / AUXILIARY_VISION_PROVIDER")
        logger.info("     指向你偏好的视觉模型")
        logger.info("  3. 配置辅助视觉后端（OpenRouter、Nous、Anthropic 等，需自备 key）")
        logger.info("  注：最后的文字级退化手段是纯 OCR（pytesseract，CPU-only），"
                    "只能抽文字、不是真识图")
        sys.exit(1)
    else:
        logger.info("✅ 视觉分析可用")
    
    logger.info("\n🛠️ Vision tools ready!")
    
    # Show debug mode status
    if _debug.active:
        logger.info(f"🐛 Debug mode ENABLED - Session ID: {_debug.session_id}")
        logger.info(f"   Debug logs will be saved to: ./logs/vision_tools_debug_{_debug.session_id}.json")
    else:
        logger.info("🐛 Debug mode disabled (set VISION_TOOLS_DEBUG=true to enable)")
    
    logger.info("\nBasic usage:")
    logger.info("  from vision_tools import vision_analyze_tool")
    logger.info("  import asyncio")
    logger.info("")
    logger.info("  async def main():")
    logger.info("      result = await vision_analyze_tool(")
    logger.info("          image_url='https://example.com/image.jpg',")
    logger.info("          user_prompt='What do you see in this image?'")
    logger.info("      )")
    logger.info("      logger.info(result)")
    logger.info("  asyncio.run(main())")
    
    logger.info("\nExample prompts:")
    logger.info("  - 'What architectural style is this building?'")
    logger.info("  - 'Describe the emotions and mood in this image'")
    logger.info("  - 'What text can you read in this image?'")
    logger.info("  - 'Identify any safety hazards visible'")
    logger.info("  - 'What products or brands are shown?'")
    
    logger.info("\nDebug mode:")
    logger.info("  # Enable debug logging")
    logger.info("  export VISION_TOOLS_DEBUG=true")
    logger.info("  # Debug logs capture all vision analysis calls and results")
    logger.info("  # Logs saved to: ./logs/vision_tools_debug_UUID.json")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
from tools.registry import registry, tool_error
from harness.recoverable import recoverable_tool

VISION_ANALYZE_SCHEMA = {
    "name": "vision_analyze",
    "description": (
        "Load an image into the conversation so you can see it. Accepts a "
        "URL, local file path, or data URL. When your active model has "
        "native vision, the image is attached to your context directly "
        "and you read the pixels yourself on the next turn — call this "
        "any time the user references an image (filepath in their message, "
        "URL in tool output, screenshot from the browser, etc.). For "
        "non-vision models, falls back to an auxiliary vision model that "
        "returns a text description."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "image_url": {
                "type": "string",
                "description": "Image URL (http/https), local file path, or data: URL to load."
            },
            "question": {
                "type": "string",
                "description": "Your specific question or request about the image. Optional context the model uses on the next turn after seeing the image."
            }
        },
        "required": ["image_url", "question"]
    }
}


# ── OCR fallback ────────────────────────────────────────────────────────
def _ocr_extract_text(image_source: str) -> str:
    """Extract text from an image using Pillow + basic OCR.

    Tries pytesseract first (if installed), falls back to a simple
    Pillow-based approach for images with clear text.

    Returns extracted text or empty string on failure.
    """
    import tempfile
    from pathlib import Path

    # Resolve image to local path
    try:
        if image_source.startswith(("http://", "https://")):
            temp_dir = get_hermes_dir("cache/vision", "temp_vision_images")
            temp_path = temp_dir / f"ocr_temp_{uuid.uuid4()}.jpg"
            # Use synchronous download
            import urllib.request
            urllib.request.urlretrieve(image_source, str(temp_path))
            should_cleanup = True
        elif image_source.startswith("data:"):
            # Decode data URL
            _, encoded = image_source.split(",", 1)
            data = base64.b64decode(encoded)
            temp_dir = get_hermes_dir("cache/vision", "temp_vision_images")
            temp_path = temp_dir / f"ocr_temp_{uuid.uuid4()}.jpg"
            temp_path.write_bytes(data)
            should_cleanup = True
        else:
            temp_path = Path(image_source).expanduser()
            if not temp_path.exists():
                return ""
            should_cleanup = False
    except Exception:
        return ""

    text = ""

    # Try pytesseract first
    try:
        import pytesseract
        from PIL import Image
        img = Image.open(temp_path)
        text = pytesseract.image_to_string(img, lang="chi_sim+eng")
    except ImportError:
        pass
    except Exception as e:
        logger.debug("pytesseract OCR failed: %s", e)

    # Fallback: no real OCR available, return empty
    # (Pillow alone can't do OCR, but we tried)
    finally:
        if should_cleanup and temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass

    return text.strip()


# ── Cross-provider vision fallback ─────────────────────────────────────────

# Generic, vendor-agnostic provider discovery for the vision fallback chain.
#
# We deliberately keep NO per-vendor "vision model" table and NO built-in /
# sponsored fallback model. Users configure their own preferred vision model
# (e.g. via AUXILIARY_VISION_MODEL / AUXILIARY_VISION_PROVIDER, or by choosing
# a multimodal main model). When scanning other user-configured providers for
# vision, we resolve each one on demand via resolve_provider_client(is_vision=True)
# — the actual model name then comes from the provider's own default, never from
# a hardcoded map here.

# .env key → provider id. Generic mapping used ONLY to discover which providers
# the user has credentials for; it never assigns vision models.
_ENV_KEY_TO_PROVIDER = {
    "ANTHROPIC_API_KEY": "anthropic", "OPENAI_API_KEY": "openai",
    "GEMINI_API_KEY": "gemini", "OPENROUTER_API_KEY": "openrouter",
    "DEEPSEEK_API_KEY": "deepseek", "QWEN_API_KEY": "alibaba",
    "ZHIPU_API_KEY": "zai", "XIAOMI_API_KEY": "xiaomi",
    "MOONSHOT_API_KEY": "moonshot", "MINIMAX_API_KEY": "minimax",
    "NVIDIA_API_KEY": "nvidia", "SILICONFLOW_API_KEY": "siliconflow",
    "STEPFUN_API_KEY": "stepfun", "TENCENT_API_KEY": "tencent-tokenhub",
    "BAIDU_API_KEY": "baidu", "VBIT_API_KEY": "vbit",
    "CUSTOM_API_KEY": "custom", "ONEAPI_KEY": "oneapi",
}


def _list_user_providers(main_provider: str) -> list[str]:
    """Return provider ids the user has configured credentials for (.env key
    present, or listed under config.yaml ``providers``), excluding the current
    main provider.

    Generic by design: this only discovers *which* providers exist, never
    which vision model to use for them. The vision model is resolved later,
    per provider, via ``resolve_provider_client(is_vision=True)``.
    """
    try:
        from hermes_cli.config import load_env, load_config
        env = load_env()
        cfg = load_config()
    except Exception:
        return []

    user_providers: set[str] = set()
    for env_key, provider in _ENV_KEY_TO_PROVIDER.items():
        if env.get(env_key):
            user_providers.add(provider)

    cfg_providers = cfg.get("providers", {})
    if isinstance(cfg_providers, dict):
        for p in cfg_providers:
            if isinstance(cfg_providers[p], dict):
                user_providers.add(p)

    user_providers.discard(main_provider)
    user_providers.discard("auto")
    user_providers.discard("")
    return sorted(user_providers)


async def _try_other_provider_vision(
    image_url: str,
    full_prompt: str,
    main_provider: str,
) -> Optional[str]:
    """Try vision analysis via another user-configured provider.

    Generic: discovers providers the user has credentials for (via
    _list_user_providers) and resolves each one on demand with
    resolve_provider_client(is_vision=True) — no hardcoded per-vendor vision
    model. Falls through to Step 3 (auxiliary chain) if nothing works.

    Returns the JSON result string on success, or None if nothing worked.
    """
    main_norm = (main_provider or "").strip().lower()
    logger.info(
        "vision_analyze Step 2.5: scanning user-configured providers "
        "(main=%s)", main_norm,
    )

    for cand_provider in _list_user_providers(main_norm):
        try:
            from agent.auxiliary_client import resolve_provider_client

            probe_client, probe_model = resolve_provider_client(
                cand_provider, is_vision=True,
            )
            if probe_client is None:
                continue

            logger.info(
                "  -> trying %s (%s)", cand_provider,
                probe_model or "default",
            )
            result_str = await vision_analyze_tool(
                image_url, full_prompt,
                model=probe_model,
                provider=cand_provider,
            )
            result = json.loads(result_str)
            if result.get("success"):
                logger.info("  -> %s succeeded", cand_provider)
                return result_str
            logger.info("  -> %s returned success=false", cand_provider)
        except Exception as exc:
            logger.debug("  -> %s failed: %s", cand_provider, exc)

    logger.info("Step 2.5: no user-configured provider with vision found")
    return None


@recoverable_tool(
    tool_name="vision_analyze",
    missing_hint=(
        "视觉分析需要配置一个支持视觉（多模态）的模型 / provider（我们不内置、"
        "也不替你付费购买视觉模型）。可：选择多模态主模型，或设置 "
        "AUXILIARY_VISION_MODEL / AUXILIARY_VISION_PROVIDER，或配置 OpenRouter / "
        "Nous / Anthropic 等辅助视觉后端（需自备 key）。纯 OCR 仅能抽文字。"
    ),
    returns="json",
)
async def _handle_vision_analyze(args: Dict[str, Any], **kw: Any) -> str:
    image_url = args.get("image_url", "")
    question = args.get("question", "")

    # ── Vision analysis workflow (universal "try-first" strategy) ────────
    #
    # Design principle: DON'T gate on capability checks — just try.
    # Capability metadata is often wrong, incomplete, or doesn't account
    # for proxy providers (One-API, vbit.top, etc.).  The only reliable
    # test is to actually call the API.
    #
    # Priority order (generic — NO hardcoded vendor vision models, NO sponsored
    # fallback model; the user configures their own vision model):
    #   1. Try current model directly (provider-aware, including proxy)
    #   2. Try other user-configured providers (resolved on demand via
    #      resolve_provider_client(is_vision=True) — the user brings their own
    #      credentials, we never assume or pay for a model)
    #   3. Try auxiliary LLM chain (OpenRouter → Nous → Anthropic)
    #   4. Try OCR fallback (local Pillow text extraction — text-only last resort)
    #
    # Each step catches errors and falls through to the next.
    # This handles ALL scenarios: direct providers, proxies (One-API,
    # vbit.top, new-api), custom model names, unknown providers, etc.

    full_prompt = (
        "请完整描述并解释这张图片的所有内容，然后回答以下问题：\n\n"
        f"{question}"
    )

    # ── Step 1: Try current model directly ───────────────────────────────
    # 直接尝试，不做预判（不依赖任何厂商→视觉模型写死映射）。失败自动回退。
    _provider = ""
    _model = ""
    try:
        from agent.auxiliary_client import (
            _read_main_provider, _read_main_model,
        )

        _raw_provider = _read_main_provider()
        _model = _read_main_model()
        _provider = _resolve_real_provider(_raw_provider, _model)

        logger.info(
            "vision_analyze Step 1: trying current model "
            "(raw_provider=%s, resolved=%s, model=%s)",
            _raw_provider, _provider, _model,
        )

        # 直接尝试当前模型，不检查是否"支持"
        try:
            logger.info("  -> trying current model via API call")
            result_str = await vision_analyze_tool(
                image_url, full_prompt, model=_model, provider=_provider,
            )
            result = json.loads(result_str)
            if result.get("success"):
                logger.info("  -> current model succeeded")
                return result_str
            logger.info("  -> current model returned success=false, trying next")
        except Exception as exc:
            logger.info("  -> current model failed: %s", exc)

    except Exception as exc:
        logger.debug("Step 1 setup failed: %s", exc)

    # ── Step 2: Try other user-configured providers ───────────────────────
    # Generic: discover providers the user has credentials for and resolve
    # each on demand (resolve_provider_client(is_vision=True)). No hardcoded
    # per-vendor vision model, no sponsored fallback — the user brings their
    # own keys and picks their own vision model.
    try:
        _other_result = await _try_other_provider_vision(
            image_url, full_prompt, _provider,
        )
        if _other_result is not None:
            return _other_result
    except Exception as exc:
        logger.info("Step 2 failed: %s", exc)

    # ── Step 3: Auxiliary LLM chain (OpenRouter -> Nous -> Anthropic) ──────
    try:
        logger.info("vision_analyze Step 3: trying auxiliary LLM chain")
        aux_model = os.getenv("AUXILIARY_VISION_MODEL", "").strip() or None
        result_str = await vision_analyze_tool(image_url, full_prompt, aux_model)
        result = json.loads(result_str)
        if result.get("success"):
            logger.info("  -> auxiliary LLM succeeded")
            return result_str
        logger.info("  -> auxiliary LLM returned success=false")
    except Exception as exc:
        logger.info("Step 3 failed: %s", exc)

    # ── Step 4: OCR fallback (local Pillow text extraction) ─────────────────
    try:
        logger.info("vision_analyze Step 4: trying OCR fallback")
        ocr_text = _ocr_extract_text(image_url)
        if ocr_text and len(ocr_text.strip()) > 10:
            logger.info("  -> OCR extracted %d chars, sending to model", len(ocr_text))
            # Use the current model to analyze OCR text (no image needed)
            from agent.auxiliary_client import resolve_provider_client
            current_model = _read_main_model()
            provider_name, client, model_name = resolve_provider_client(
                model=current_model, async_mode=False,
            )
            if client:
                ocr_prompt = (
                    f"以下是图片中提取的文字内容：\n\n{ocr_text}\n\n"
                    f"用户问题：{question}\n\n"
                    "请根据以上文字内容回答用户的问题。如果文字内容不足以回答，"
                    "请说明需要什么额外信息。"
                )
                messages = [{"role": "user", "content": ocr_prompt}]
                response = client(messages, model=model_name, max_tokens=2000)
                content = extract_content_or_reasoning(response)
                if content:
                    logger.info("  -> OCR + model analysis succeeded (text-only fallback)")
                    # B3: when we fall all the way to OCR it means NO real vision
                    # model is configured. Do NOT return a silent "success" that
                    # could be mistaken for true image understanding — make the
                    # degraded path explicit and actionable.
                    return json.dumps(
                        {
                            "success": True,
                            "ocr_only": True,
                            "analysis": (
                                "⚠️ 未配置视觉（多模态）模型，已退化为纯 OCR 文字提取"
                                "（非真识图，仅能读取图中文字）。\n"
                                "要真正理解图片语义，请配置以下任一方式"
                                "（我们不内置、也不替你付费购买视觉模型）：\n"
                                "  • 选择多模态主模型（如 GPT-4o / Claude / Gemini 等）；或\n"
                                "  • 设置 AUXILIARY_VISION_MODEL / AUXILIARY_VISION_PROVIDER"
                                " 指向你偏好的视觉模型；或\n"
                                "  • 配置辅助视觉后端（OpenRouter / Nous / Anthropic 等，需自备 key）。\n\n"
                                f"── 以下仅基于图中文字作答 ──\n{content}"
                            ),
                        },
                        indent=2, ensure_ascii=False,
                    )
            logger.info("  -> OCR + model analysis failed")
        else:
            logger.info("  -> OCR extracted insufficient text (%d chars)", len(ocr_text or ""))
    except Exception as exc:
        logger.info("Step 4 (OCR) failed: %s", exc)

    # ── All steps failed ─────────────────────────────────────────────────
    logger.error("vision_analyze: all steps failed")
    return json.dumps({
        "success": False,
        "error": "未配置可用的视觉模型",
        "analysis": (
            "当前账号未配置支持视觉（多模态）的模型，无法分析图片。\n"
            "请配置一个支持视觉的模型 / provider：\n"
            "  • 选择一个多模态主模型（如 GPT-4o、Claude、Gemini 等）；或\n"
            "  • 设置 AUXILIARY_VISION_MODEL / AUXILIARY_VISION_PROVIDER 指向你\n"
            "    偏好的视觉模型；或\n"
            "  • 配置辅助视觉后端（OpenRouter / Nous / Anthropic 等，需自备 key）。\n"
            "我们不内置、也不替你付费购买视觉模型。\n"
            "最后说明：纯 OCR（pytesseract，CPU-only、较快）可作为文字级退化手段，\n"
            "但只能抽取图中文字、不是真正的识图。"
        ),
    }, indent=2, ensure_ascii=False)


registry.register(
    name="vision_analyze",
    toolset="vision",
    schema=VISION_ANALYZE_SCHEMA,
    handler=_handle_vision_analyze,
    check_fn=check_vision_requirements,
    is_async=True,
    emoji="👁️",
)


# ---------------------------------------------------------------------------
# Video Analysis Tool
# ---------------------------------------------------------------------------

# Extension → MIME. avi/mkv fall back to mp4.
_VIDEO_MIME_TYPES = {
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/mov",
    ".avi": "video/mp4",
    ".mkv": "video/mp4",
    ".mpeg": "video/mpeg",
    ".mpg": "video/mpeg",
}

_MAX_VIDEO_BASE64_BYTES = 50 * 1024 * 1024  # 50 MB hard cap
_VIDEO_SIZE_WARN_BYTES = 20 * 1024 * 1024


def _detect_video_mime_type(video_path: Path) -> Optional[str]:
    """Return a video MIME type based on file extension, or None if unsupported."""
    ext = video_path.suffix.lower()
    return _VIDEO_MIME_TYPES.get(ext)


def _video_to_base64_data_url(video_path: Path, mime_type: Optional[str] = None) -> str:
    """Convert a video file to a base64-encoded data URL."""
    data = video_path.read_bytes()
    encoded = base64.b64encode(data).decode("ascii")
    mime = mime_type or _VIDEO_MIME_TYPES.get(video_path.suffix.lower(), "video/mp4")
    return f"data:{mime};base64,{encoded}"


async def _download_video(video_url: str, destination: Path, max_retries: int = 3) -> Path:
    """Download video from URL with SSRF protection and retry."""
    import asyncio

    destination.parent.mkdir(parents=True, exist_ok=True)

    async def _ssrf_redirect_guard(response):
        if response.is_redirect and response.next_request:
            redirect_url = str(response.next_request.url)
            from tools.url_safety import is_safe_url
            if not is_safe_url(redirect_url):
                raise ValueError(
                    f"Blocked redirect to private/internal address: {redirect_url}"
                )

    last_error = None
    for attempt in range(max_retries):
        try:
            blocked = check_website_access(video_url)
            if blocked:
                raise PermissionError(blocked["message"])

            async with httpx.AsyncClient(
                timeout=60.0,
                follow_redirects=True,
                event_hooks={"response": [_ssrf_redirect_guard]},
            ) as client:
                response = await client.get(
                    video_url,
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                        "Accept": "video/*,*/*;q=0.8",
                    },
                )
                response.raise_for_status()

                cl = response.headers.get("content-length")
                if cl and int(cl) > _MAX_VIDEO_BASE64_BYTES:
                    raise ValueError(
                        f"Video too large ({int(cl)} bytes, max {_MAX_VIDEO_BASE64_BYTES})"
                    )

                final_url = str(response.url)
                blocked = check_website_access(final_url)
                if blocked:
                    raise PermissionError(blocked["message"])

                body = response.content
                if len(body) > _MAX_VIDEO_BASE64_BYTES:
                    raise ValueError(
                        f"Video too large ({len(body)} bytes, max {_MAX_VIDEO_BASE64_BYTES})"
                    )
                destination.write_bytes(body)

            return destination
        except Exception as e:
            last_error = e
            if attempt < max_retries - 1:
                wait_time = 2 ** (attempt + 1)
                logger.warning("Video download failed (attempt %s/%s): %s", attempt + 1, max_retries, str(e)[:50])
                await asyncio.sleep(wait_time)
            else:
                logger.error(
                    "Video download failed after %s attempts: %s",
                    max_retries, str(e)[:100], exc_info=True,
                )

    if last_error is None:
        raise RuntimeError(
            f"_download_video exited retry loop without attempting (max_retries={max_retries})"
        )
    raise last_error


async def video_analyze_tool(
    video_url: str,
    user_prompt: str,
    model: str = None,
) -> str:
    """Analyze a video via multimodal LLM. Returns JSON {success, analysis}."""
    if not isinstance(user_prompt, str):
        user_prompt = str(user_prompt) if user_prompt is not None else ""
    debug_call_data = {
        "parameters": {
            "video_url": video_url,
            "user_prompt": user_prompt[:200] + "..." if len(user_prompt) > 200 else user_prompt,
            "model": model,
        },
        "error": None,
        "success": False,
        "analysis_length": 0,
        "model_used": model,
        "video_size_bytes": 0,
    }

    temp_video_path = None
    should_cleanup = True

    try:
        from tools.interrupt import is_interrupted
        if is_interrupted():
            return tool_error("Interrupted", success=False)

        logger.info("Analyzing video: %s", video_url[:60])
        logger.info("User prompt: %s", user_prompt[:100])

        # Resolve local path vs remote URL
        resolved_url = video_url
        if resolved_url.startswith("file://"):
            resolved_url = resolved_url[len("file://"):]
        local_path = Path(os.path.expanduser(resolved_url))

        if local_path.is_file():
            logger.info("Using local video file: %s", video_url)
            temp_video_path = local_path
            should_cleanup = False
        elif video_url.startswith("data:"):
            # Data URL (base64-encoded video) -- decode and save to temp file
            logger.info("Decoding video data URL...")
            try:
                data, ext = _decode_data_url(video_url)
                # Video files typically use mp4, but respect the declared type
                video_ext = ext if ext in ("mp4", "webm", "mov", "mkv") else "mp4"
                temp_dir = get_hermes_dir("cache/video", "temp_video_files")
                temp_video_path = temp_dir / f"temp_video_{uuid.uuid4()}.{video_ext}"
                temp_video_path.write_bytes(data)
                should_cleanup = True
                logger.info("Video data URL decoded and saved (%.1f KB)", len(data) / 1024)
            except Exception as e:
                raise ValueError(f"Failed to decode video data URL: {e}")
        elif _validate_image_url(video_url):
            blocked = check_website_access(video_url)
            if blocked:
                raise PermissionError(blocked["message"])
            temp_dir = get_hermes_dir("cache/video", "temp_video_files")
            temp_video_path = temp_dir / f"temp_video_{uuid.uuid4()}.mp4"
            await _download_video(video_url, temp_video_path)
            should_cleanup = True
        else:
            raise ValueError(
                "Invalid video source. Provide an HTTP/HTTPS URL, a valid local file path, or a data URL."
            )

        video_size_bytes = temp_video_path.stat().st_size
        video_size_mb = video_size_bytes / (1024 * 1024)
        logger.info("Video ready (%.1f MB)", video_size_mb)

        detected_mime = _detect_video_mime_type(temp_video_path)
        if not detected_mime:
            raise ValueError(
                f"Unsupported video format: '{temp_video_path.suffix}'. "
                f"Supported: {', '.join(sorted(_VIDEO_MIME_TYPES.keys()))}"
            )

        if video_size_bytes > _VIDEO_SIZE_WARN_BYTES:
            logger.warning("Video is %.1f MB — may be slow or rejected", video_size_mb)

        video_data_url = _video_to_base64_data_url(temp_video_path, mime_type=detected_mime)
        data_size_mb = len(video_data_url) / (1024 * 1024)

        if len(video_data_url) > _MAX_VIDEO_BASE64_BYTES:
            raise ValueError(
                f"Video too large for API: base64 payload is {data_size_mb:.1f} MB "
                f"(limit {_MAX_VIDEO_BASE64_BYTES / (1024 * 1024):.0f} MB). "
                f"Compress or trim the video and retry."
            )

        debug_call_data["video_size_bytes"] = video_size_bytes

        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": user_prompt,
                    },
                    {
                        "type": "video_url",
                        "video_url": {
                            "url": video_data_url,
                        },
                    },
                ],
            }
        ]

        vision_timeout = 180.0
        vision_temperature = 0.1
        try:
            from hermes_cli.config import cfg_get, load_config
            _cfg = load_config()
            _vision_cfg = cfg_get(_cfg, "auxiliary", "vision", default={})
            _vt = _vision_cfg.get("timeout")
            if _vt is not None:
                vision_timeout = max(float(_vt), 180.0)
            _vtemp = _vision_cfg.get("temperature")
            if _vtemp is not None:
                vision_temperature = float(_vtemp)
        except Exception:
            pass

        call_kwargs = {
            "task": "vision",
            "messages": messages,
            "temperature": vision_temperature,
            "max_tokens": 4000,
            "timeout": vision_timeout,
        }
        if model:
            call_kwargs["model"] = model

        response = await async_call_llm(**call_kwargs)
        analysis = extract_content_or_reasoning(response)

        if not analysis:
            logger.warning("Empty video response, retrying once")
            response = await async_call_llm(**call_kwargs)
            analysis = extract_content_or_reasoning(response)

        analysis_length = len(analysis) if analysis else 0
        logger.info("Video analysis completed (%s characters)", analysis_length)

        result = {
            "success": True,
            "analysis": analysis or "There was a problem with the request and the video could not be analyzed.",
        }

        debug_call_data["success"] = True
        debug_call_data["analysis_length"] = analysis_length
        _debug.log_call("video_analyze_tool", debug_call_data)
        _debug.save()

        return json.dumps(result, indent=2, ensure_ascii=False)

    except Exception as e:
        error_msg = f"Error analyzing video: {str(e)}"
        logger.error("%s", error_msg, exc_info=True)

        err_str = str(e).lower()
        if any(hint in err_str for hint in (
            "402", "insufficient", "payment required", "credits", "billing",
        )):
            analysis = (
                "Insufficient credits or payment required. Please top up your "
                f"API provider account and try again. Error: {e}"
            )
        elif any(hint in err_str for hint in (
            "does not support", "not support video",
            "content_policy", "multimodal",
            "unrecognized request argument", "video input",
            "video_url",
        )):
            analysis = (
                f"The model does not support video analysis or the request was "
                f"rejected. Ensure you're using a video-capable model "
                f"(e.g. google/gemini-2.5-flash). Error: {e}"
            )
        elif any(hint in err_str for hint in (
            "too large", "payload", "413", "content_too_large",
            "request_too_large", "exceeds", "size limit",
        )):
            analysis = (
                "The video is too large for the API. Try compressing or trimming "
                f"the video (max ~50 MB). Error: {e}"
            )
        else:
            analysis = (
                "There was a problem with the request and the video could not "
                f"be analyzed. Error: {e}"
            )

        result = {
            "success": False,
            "error": error_msg,
            "analysis": analysis,
        }

        debug_call_data["error"] = error_msg
        _debug.log_call("video_analyze_tool", debug_call_data)
        _debug.save()

        return json.dumps(result, indent=2, ensure_ascii=False)

    finally:
        if should_cleanup and temp_video_path and temp_video_path.exists():
            try:
                temp_video_path.unlink()
                logger.debug("Cleaned up temporary video file")
            except Exception as cleanup_error:
                logger.warning(
                    "Could not delete temporary file: %s", cleanup_error, exc_info=True
                )


VIDEO_ANALYZE_SCHEMA = {
    "name": "video_analyze",
    "description": (
        "Analyze a video from a URL or local file path using a multimodal AI model. "
        "Sends the video to a video-capable model (e.g. Gemini) for understanding. "
        "Use this for video files — for images, use vision_analyze instead. "
        "Supports mp4, webm, mov, avi, mkv, mpeg formats. "
        "Note: large videos (>20 MB) may be slow; max ~50 MB."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "video_url": {
                "type": "string",
                "description": "Video URL (http/https) or local file path to analyze.",
            },
            "question": {
                "type": "string",
                "description": "Your specific question about the video. The AI will describe what happens in the video and answer your question.",
            },
        },
        "required": ["video_url", "question"],
    },
}


@recoverable_tool(
    tool_name="video_analyze",
    missing_hint=(
        "视频分析需要配置 AUXILIARY_VIDEO_MODEL 或 AUXILIARY_VISION_MODEL。"
        "可设置 OPENROUTER_API_KEY / XIAOMI_API_KEY 等视觉 provider。"
    ),
    returns="json",
)
def _handle_video_analyze(args: Dict[str, Any], **kw: Any) -> Awaitable[str]:
    video_url = args.get("video_url", "")
    question = args.get("question", "")
    full_prompt = (
        "Fully describe and explain everything happening in this video, "
        "including visual content, motion, audio cues, text overlays, and scene "
        f"transitions. Then answer the following question:\n\n{question}"
    )
    model = os.getenv("AUXILIARY_VIDEO_MODEL", "").strip() or os.getenv("AUXILIARY_VISION_MODEL", "").strip() or None
    return video_analyze_tool(video_url, full_prompt, model)


registry.register(
    name="video_analyze",
    toolset="video",
    schema=VIDEO_ANALYZE_SCHEMA,
    handler=_handle_video_analyze,
    check_fn=check_vision_requirements,
    is_async=True,
    emoji="🎬",
)


def check_vision_fallback_config() -> dict[str, bool]:
    """Check if vision fallback providers are configured.
    
    Returns a dict with provider names as keys and whether they're configured.
    Logs warnings for missing providers.
    """
    providers = {
        "OPENROUTER_API_KEY": bool(os.getenv("OPENROUTER_API_KEY", "").strip()),
        "XIAOMI_API_KEY": bool(os.getenv("XIAOMI_API_KEY", "").strip()),
        "ANTHROPIC_API_KEY": bool(os.getenv("ANTHROPIC_API_KEY", "").strip()),
        "NOUS_API_KEY": bool(os.getenv("NOUS_API_KEY", "").strip()),
    }
    
    configured = [k for k, v in providers.items() if v]
    missing = [k for k, v in providers.items() if not v]
    
    if configured:
        logger.info("Vision fallback providers configured: %s", ", ".join(configured))
    if missing:
        logger.debug("Vision fallback providers not configured: %s", ", ".join(missing))
    
    return providers
