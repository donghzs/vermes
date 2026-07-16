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
    normalize_reference_images,
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

# Size maps for different model versions
# 2.0 Flash uses pixel format; 2.1 Flash supports tier format (1K/2K/3K/4K) + ratio
SIZES_PIXEL = {
    "landscape": "1024x768",
    "square": "1024x1024",
    "portrait": "768x1024",
}
SIZES_TIER = {
    "landscape": "2K",
    "square": "2K",
    "portrait": "2K",
}
RATIOS = {
    "landscape": "16:9",
    "square": "1:1",
    "portrait": "9:16",
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

    def capabilities(self) -> Dict[str, Any]:
        return {
            "modalities": ["text", "image"],
            "max_reference_images": 4,
            "edit_supports": ["agnes-image-2.0-flash"],
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
        meta = MODELS.get(model_id, {})
        # Use tier format for 2.1 Flash, pixel format for 2.0 Flash
        if model_id == "agnes-image-2.1-flash":
            size = SIZES_TIER.get(aspect, "2K")
        else:
            size = SIZES_PIXEL.get(aspect, SIZES_PIXEL["square"])

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
            "response_format": "url",
        }
        # 2.1 Flash supports ratio parameter
        if model_id == "agnes-image-2.1-flash":
            payload["ratio"] = RATIOS.get(aspect, "1:1")

        # img2img support: use image_url or reference_image_urls
        all_images = []
        if image_url:
            all_images.append(image_url)
        refs = normalize_reference_images(reference_image_urls, max_count=4)
        all_images.extend(refs)
        
        if all_images and MODELS.get(model_id, {}).get("supports_img2img"):
            # Per Agnes API docs: use extra_body.image, do NOT pass tags
            payload["extra_body"] = {
                "image": all_images,
            }
        elif all_images and not MODELS.get(model_id, {}).get("supports_img2img"):
            return error_response(
                error=(
                    f"Model '{model_id}' does not support image-to-image / editing. "
                    f"Use 'agnes-image-2.0-flash' for img2img, or provide a text-only prompt."
                ),
                error_type="modality_unsupported",
                provider="agnes",
                model=model_id,
                prompt=prompt,
                aspect_ratio=aspect,
            )

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
