"""Agnes AI image generation backend.

Exposes Agnes's image models via OpenAI-compatible /v1/images/generations.
Supports text-to-image and image-to-image (img2img).

Models:
  agnes-image-2.0-flash  — img2img + multi-image composition
  agnes-image-2.1-flash  — text-to-image (newer)

API endpoint: https://apihub.agnes-ai.com/v1/images/generations
Auth: Bearer token (AGNES_API_KEY env var)
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from agent.image_gen_provider import (
    DEFAULT_ASPECT_RATIO,
    ImageGenProvider,
    error_response,
    resolve_aspect_ratio,
    save_b64_image,
    success_response,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Model catalog
# ---------------------------------------------------------------------------

MODELS: Dict[str, Dict[str, Any]] = {
    "agnes-image-2.0-flash": {
        "display": "Agnes Image 2.0 Flash",
        "speed": "~10-20s",
        "strengths": "img2img, multi-image composition, style transfer",
        "supports_img2img": True,
    },
    "agnes-image-2.1-flash": {
        "display": "Agnes Image 2.1 Flash",
        "speed": "~10-20s",
        "strengths": "Text-to-image, latest model",
        "supports_img2img": False,
    },
}

DEFAULT_MODEL = "agnes-image-2.1-flash"

SIZES = {
    "landscape": "1024x768",
    "square": "1024x1024",
    "portrait": "768x1024",
}

BASE_URL = "https://apihub.agnes-ai.com/v1"


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _load_config() -> Dict[str, Any]:
    try:
        from hermes_cli.config import load_config
        cfg = load_config()
        section = cfg.get("image_gen") if isinstance(cfg, dict) else None
        return section if isinstance(section, dict) else {}
    except Exception:
        return {}


def _resolve_model() -> str:
    env = os.environ.get("AGNES_IMAGE_MODEL")
    if env and env in MODELS:
        return env
    cfg = _load_config()
    agnes_cfg = cfg.get("agnes") if isinstance(cfg.get("agnes"), dict) else {}
    candidate = agnes_cfg.get("model") if isinstance(agnes_cfg, dict) else None
    if isinstance(candidate, str) and candidate in MODELS:
        return candidate
    top = cfg.get("model")
    if isinstance(top, str) and top in MODELS:
        return top
    return DEFAULT_MODEL


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

class AgnesImageGenProvider(ImageGenProvider):
    """Agnes AI image generation backend."""

    @property
    def name(self) -> str:
        return "agnes"

    @property
    def display_name(self) -> str:
        return "Agnes AI"

    def is_available(self) -> bool:
        return bool(os.environ.get("AGNES_API_KEY"))

    def list_models(self) -> List[Dict[str, Any]]:
        return [
            {
                "id": mid,
                "display": meta["display"],
                "speed": meta["speed"],
                "strengths": meta["strengths"],
                "price": "free (limited)",
            }
            for mid, meta in MODELS.items()
        ]

    def default_model(self) -> Optional[str]:
        return DEFAULT_MODEL

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": "Agnes AI",
            "badge": "free",
            "tag": "Text-to-image + img2img, free tier available",
            "env_vars": [
                {
                    "key": "AGNES_API_KEY",
                    "prompt": "Agnes AI API key",
                    "url": "https://platform.agnes-ai.com",
                },
            ],
        }

    def generate(
        self,
        prompt: str,
        aspect_ratio: str = DEFAULT_ASPECT_RATIO,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        prompt = (prompt or "").strip()
        aspect = resolve_aspect_ratio(aspect_ratio)

        if not prompt:
            return error_response(
                error="Prompt is required",
                error_type="invalid_argument",
                provider="agnes",
                aspect_ratio=aspect,
            )

        api_key = os.environ.get("AGNES_API_KEY")
        if not api_key:
            return error_response(
                error=(
                    "AGNES_API_KEY not set. Get a free key at "
                    "https://platform.agnes-ai.com"
                ),
                error_type="auth_required",
                provider="agnes",
                aspect_ratio=aspect,
            )

        model_id = _resolve_model()
        size = SIZES.get(aspect, SIZES["square"])

        # Build request payload
        import httpx

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        payload: Dict[str, Any] = {
            "model": model_id,
            "prompt": prompt,
            "size": size,
            "response_format": "b64_json",
        }

        # img2img support: check for image URLs in kwargs
        image_urls = kwargs.get("image_urls") or kwargs.get("images")
        if image_urls and isinstance(image_urls, list) and MODELS.get(model_id, {}).get("supports_img2img"):
            payload["extra_body"] = {
                "tags": ["img2img"],
                "image": image_urls,
            }

        try:
            resp = httpx.post(
                f"{BASE_URL}/images/generations",
                headers=headers,
                json=payload,
                timeout=60,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            return error_response(
                error=f"Agnes image generation failed: {exc}",
                error_type="api_error",
                provider="agnes",
                model=model_id,
                prompt=prompt,
                aspect_ratio=aspect,
            )

        # Parse response (OpenAI-compatible format)
        images = data.get("data", [])
        if not images:
            return error_response(
                error="Agnes returned no image data",
                error_type="empty_response",
                provider="agnes",
                model=model_id,
                prompt=prompt,
                aspect_ratio=aspect,
            )

        first = images[0]
        b64 = first.get("b64_json")
        url = first.get("url")

        if b64:
            try:
                saved_path = save_b64_image(b64, prefix=f"agnes_{model_id}")
            except Exception as exc:
                return error_response(
                    error=f"Could not save image: {exc}",
                    error_type="io_error",
                    provider="agnes",
                    model=model_id,
                    prompt=prompt,
                    aspect_ratio=aspect,
                )
            image_ref = str(saved_path)
        elif url:
            image_ref = url
        else:
            return error_response(
                error="Agnes response contained neither b64_json nor URL",
                error_type="empty_response",
                provider="agnes",
                model=model_id,
                prompt=prompt,
                aspect_ratio=aspect,
            )

        return success_response(
            image=image_ref,
            model=model_id,
            prompt=prompt,
            aspect_ratio=aspect,
            provider="agnes",
            extra={"size": size},
        )


# ---------------------------------------------------------------------------
# Plugin entry point
# ---------------------------------------------------------------------------

def register(ctx) -> None:
    ctx.register_image_gen_provider(AgnesImageGenProvider())
