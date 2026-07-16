"""
Universal OpenAI-Compatible Image Generation Backend
=====================================================

One provider to rule them all. Any image API that follows the OpenAI
``POST /v1/images/generations`` format works here — just configure:

    image_gen:
      provider: openai_compat
      base_url: https://apihub.agnes-ai.com/v1     # any OpenAI-compatible endpoint
      api_key: ${AGNES_API_KEY}                     # or hardcode / env var
      model: agnes-image-2.1-flash                  # any model the endpoint supports
      # optional:
      size: 2K                                       # "1K"|"2K"|"3K"|"4K" or "1024x1024"
      ratio: "16:9"                                  # aspect ratio hint
      response_format: url                           # "url"|"b64_json"

Supported workflows (all via the same endpoint):
  - Text-to-image:  prompt only
  - Image-to-image: prompt + image_url (passed via extra_body.image)
  - Multi-image:    prompt + reference_image_urls (passed via extra_body.image array)

No hardcoded URLs, no hardcoded models, no hardcoded providers.
The user picks the backend; we just speak the protocol.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from agent.image_gen_provider import (
    DEFAULT_ASPECT_RATIO,
    ImageGenProvider,
    error_response,
    normalize_reference_images,
    resolve_aspect_ratio,
    save_b64_image,
    save_url_image,
    success_response,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config resolution
# ---------------------------------------------------------------------------

def _load_image_gen_config() -> Dict[str, Any]:
    """Load the image_gen section from config.yaml."""
    try:
        from hermes_cli.config import load_config
        cfg = load_config()
        section = cfg.get("image_gen") if isinstance(cfg, dict) else None
        return section if isinstance(section, dict) else {}
    except Exception:
        return {}


def _resolve_base_url(cfg: Dict[str, Any]) -> Optional[str]:
    """Resolve base_url from config or env."""
    url = cfg.get("base_url") or cfg.get("openai_compat", {}).get("base_url")
    if isinstance(url, str) and url.strip():
        return url.strip().rstrip("/")
    # Fallback: check common env vars
    for env_key in ("IMAGE_GEN_BASE_URL", "OPENAI_BASE_URL", "AGNES_API_BASE"):
        val = os.environ.get(env_key)
        if val:
            return val.strip().rstrip("/")
    return None


def _resolve_api_key(cfg: Dict[str, Any]) -> Optional[str]:
    """Resolve API key from config or env."""
    key = cfg.get("api_key") or cfg.get("openai_compat", {}).get("api_key")
    if isinstance(key, str) and key.strip():
        # Support ${ENV_VAR} syntax
        if key.startswith("${") and key.endswith("}"):
            env_name = key[2:-1]
            return os.environ.get(env_name)
        return key.strip()
    # Fallback: check common env vars
    for env_key in ("IMAGE_GEN_API_KEY", "OPENAI_API_KEY", "AGNES_API_KEY", "XAI_API_KEY"):
        val = os.environ.get(env_key)
        if val:
            return val
    return None


def _resolve_model(cfg: Dict[str, Any]) -> Optional[str]:
    """Resolve model from config or env."""
    model = cfg.get("model")
    if isinstance(model, str) and model.strip():
        return model.strip()
    return os.environ.get("IMAGE_GEN_MODEL")


def _resolve_size(cfg: Dict[str, Any], aspect: str) -> str:
    """Resolve size parameter — supports both tier format and pixel format."""
    # Explicit config wins
    size = cfg.get("size")
    if isinstance(size, str) and size.strip():
        return size.strip()
    # Aspect ratio → default pixel sizes (for older APIs)
    defaults = {
        "landscape": "1024x768",
        "square": "1024x1024",
        "portrait": "768x1024",
    }
    return defaults.get(aspect, "1024x1024")


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

class OpenAICompatImageGenProvider(ImageGenProvider):
    """Universal OpenAI-compatible image generation backend.

    Works with any provider that implements POST /v1/images/generations.
    Configured entirely via config.yaml — no hardcoded URLs or models.
    """

    @property
    def name(self) -> str:
        return "openai_compat"

    @property
    def display_name(self) -> str:
        return "OpenAI-Compatible (Universal)"

    def is_available(self) -> bool:
        cfg = _load_image_gen_config()
        return bool(_resolve_base_url(cfg) and _resolve_api_key(cfg))

    def list_models(self) -> List[Dict[str, Any]]:
        """Return user-configured model as a single-entry catalog."""
        cfg = _load_image_gen_config()
        model = _resolve_model(cfg)
        if not model:
            return []
        return [
            {
                "id": model,
                "display": model,
                "speed": "varies",
                "strengths": "User-configured OpenAI-compatible model",
                "price": "varies",
            }
        ]

    def default_model(self) -> Optional[str]:
        cfg = _load_image_gen_config()
        return _resolve_model(cfg)

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": "OpenAI-Compatible (Universal)",
            "badge": "universal",
            "tag": "Any provider with POST /v1/images/generations — configure base_url + api_key + model",
            "env_vars": [],
        }

    def capabilities(self) -> Dict[str, Any]:
        # Universal: support text + image + reference (the API handles routing)
        return {
            "modalities": ["text", "image", "reference"],
            "max_reference_images": 8,
            "edit_supports": None,  # all models — let the API reject if unsupported
        }

    def generate(
        self,
        prompt: str,
        aspect_ratio: str = DEFAULT_ASPECT_RATIO,
        *,
        image_url: Optional[str] = None,
        reference_image_urls: Optional[list] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        prompt = (prompt or "").strip()
        aspect = resolve_aspect_ratio(aspect_ratio)

        if not prompt:
            return error_response(
                error="Prompt is required",
                error_type="invalid_argument",
                provider="openai_compat",
                aspect_ratio=aspect,
            )

        cfg = _load_image_gen_config()
        base_url = _resolve_base_url(cfg)
        api_key = _resolve_api_key(cfg)
        model = _resolve_model(cfg) or kwargs.get("model")

        if not base_url:
            return error_response(
                error=(
                    "image_gen.base_url not configured. Set it in config.yaml:\n"
                    "  image_gen:\n"
                    "    provider: openai_compat\n"
                    "    base_url: https://apihub.agnes-ai.com/v1\n"
                    "    api_key: ${AGNES_API_KEY}\n"
                    "    model: agnes-image-2.1-flash"
                ),
                error_type="config_required",
                provider="openai_compat",
                aspect_ratio=aspect,
            )

        if not api_key:
            return error_response(
                error="No API key found. Set image_gen.api_key in config.yaml or API_KEY env var.",
                error_type="auth_required",
                provider="openai_compat",
                aspect_ratio=aspect,
            )

        if not model:
            return error_response(
                error="No model configured. Set image_gen.model in config.yaml.",
                error_type="config_required",
                provider="openai_compat",
                aspect_ratio=aspect,
            )

        size = _resolve_size(cfg, aspect)
        response_format = cfg.get("response_format", "url")

        # Build request payload — standard OpenAI format
        payload: Dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "size": size,
            "response_format": response_format,
        }

        # Optional: ratio for tier-based size APIs (Agnes 2.1, etc.)
        ratio = cfg.get("ratio")
        if isinstance(ratio, str) and ratio.strip():
            payload["ratio"] = ratio.strip()

        # Image-to-image / multi-image: use extra_body.image (per Agnes API docs,
        # which follows OpenAI extension pattern)
        all_images = []
        if image_url:
            all_images.append(image_url)
        refs = normalize_reference_images(reference_image_urls, max_count=8)
        all_images.extend(refs)

        if all_images:
            payload["extra_body"] = {
                "image": all_images,
            }

        # Make the request
        import httpx

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        try:
            resp = httpx.post(
                f"{base_url}/images/generations",
                headers=headers,
                json=payload,
                timeout=120,
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            err_detail = ""
            try:
                err_body = exc.response.json()
                err_detail = err_body.get("error", {}).get("message", "") or str(err_body)
            except Exception:
                err_detail = exc.response.text[:500]
            return error_response(
                error=f"API returned {exc.response.status_code}: {err_detail}",
                error_type="api_error",
                provider="openai_compat",
                model=model,
                prompt=prompt,
                aspect_ratio=aspect,
            )
        except Exception as exc:
            return error_response(
                error=f"Request failed: {exc}",
                error_type="api_error",
                provider="openai_compat",
                model=model,
                prompt=prompt,
                aspect_ratio=aspect,
            )

        # Parse response (OpenAI-compatible format)
        images = data.get("data", [])
        if not images:
            return error_response(
                error="API returned no image data",
                error_type="empty_response",
                provider="openai_compat",
                model=model,
                prompt=prompt,
                aspect_ratio=aspect,
            )

        first = images[0]
        b64 = first.get("b64_json")
        url = first.get("url")

        if b64:
            try:
                saved_path = save_b64_image(b64, prefix=f"img_{model.replace('.', '_')}")
                image_ref = str(saved_path)
            except Exception as exc:
                logger.warning("Failed to save b64 image: %s — returning raw b64", exc)
                image_ref = f"data:image/png;base64,{b64}"
        elif url:
            # Try to cache the URL locally (some providers return ephemeral URLs)
            try:
                saved_path = save_url_image(url, prefix=f"img_{model.replace('.', '_')}")
                image_ref = str(saved_path)
            except Exception:
                # If caching fails, return the URL directly
                image_ref = url
        else:
            return error_response(
                error="Response contained neither b64_json nor URL",
                error_type="empty_response",
                provider="openai_compat",
                model=model,
                prompt=prompt,
                aspect_ratio=aspect,
            )

        return success_response(
            image=image_ref,
            model=model,
            prompt=prompt,
            aspect_ratio=aspect,
            provider="openai_compat",
            extra={"size": size, "base_url": base_url},
        )


# ---------------------------------------------------------------------------
# Plugin entry point
# ---------------------------------------------------------------------------

def register(ctx) -> None:
    ctx.register_image_gen_provider(OpenAICompatImageGenProvider())
