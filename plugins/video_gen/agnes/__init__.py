"""Agnes AI video generation backend.

Exposes Agnes's video model via /v1/videos (async task pattern).
Submit task → poll for completion → return video URL.

Model: agnes-video-v2.0
  - Text-to-video
  - Supports 8n+1 frames: 81/121/161/241
  - Default: 121 frames, 24fps, 768x1152

API endpoint: https://apihub.agnes-ai.com/v1/videos
Auth: Bearer token (AGNES_API_KEY env var)
"""

from __future__ import annotations
from agent.service_credentials import get_api_key, get_service_credentials, register_service

import logging
import os
import time
from typing import Any, Dict, List, Optional

from agent.video_gen_provider import (
    VideoGenProvider,
    error_response,
    success_response,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Model catalog
# ---------------------------------------------------------------------------

MODELS: Dict[str, Dict[str, Any]] = {
    "agnes-video-v2.0": {
        "display": "Agnes Video 2.0",
        "speed": "~60-180s",
        "strengths": "Text/Image/Multi-image/Keyframe-to-video, 768x1152, 24fps, up to 10s",
        "tier": "free",
        "aspect_ratios": ("16:9", "9:16", "1:1"),
        "frame_rates": (24,),
        "frame_counts": (81, 121, 161, 241),
        "max_duration": 10,
        "min_duration": 3,
        "supports_image_to_video": True,
        "supports_multi_image": True,
        "supports_keyframes": True,
        "max_reference_images": 5,
    },
}

DEFAULT_MODEL = "agnes-video-v2.0"

BASE_URL = "https://apihub.agnes-ai.com/v1"

# Poll settings
POLL_INTERVAL = 10  # seconds
POLL_TIMEOUT = 600  # 10 minutes max


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _load_config() -> Dict[str, Any]:
    try:
        from hermes_cli.config import load_config
        cfg = load_config()
        section = cfg.get("video_gen") if isinstance(cfg, dict) else None
        return section if isinstance(section, dict) else {}
    except Exception:
        return {}


def _resolve_model() -> str:
    env = os.environ.get("AGNES_VIDEO_MODEL")
    if env and env in MODELS:
        return env
    cfg = _load_config()
    agnes_cfg = cfg.get("agnes") if isinstance(cfg.get("agnes"), dict) else {}
    candidate = agnes_cfg.get("model") if isinstance(agnes_cfg, dict) else None
    if isinstance(candidate, str) and candidate in MODELS:
        return candidate
    return DEFAULT_MODEL


def _pick_frame_count(duration: Optional[int], model_meta: Dict[str, Any]) -> int:
    """Pick the closest valid frame count (8n+1) for the requested duration."""
    fps = 24
    counts = model_meta.get("frame_counts", (121,))
    if duration is None:
        return 121  # default ~5s
    target_frames = duration * fps
    return min(counts, key=lambda c: abs(c - target_frames))


def _pick_resolution(aspect_ratio: str) -> tuple:
    """Return (width, height) for the given aspect ratio."""
    ratios = {
        "16:9": (1152, 768),
        "9:16": (768, 1152),
        "1:1": (768, 768),
    }
    return ratios.get(aspect_ratio, (1152, 768))


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

class AgnesVideoGenProvider(VideoGenProvider):
    """Agnes AI video generation backend (async task pattern)."""

    @property
    def name(self) -> str:
        return "agnes"

    @property
    def display_name(self) -> str:
        return "Agnes AI"

    def is_available(self) -> bool:
        return bool(get_api_key("agnes"))

    def list_models(self) -> List[Dict[str, Any]]:
        return [
            {
                "id": mid,
                "display": meta["display"],
                "speed": meta["speed"],
                "strengths": meta["strengths"],
                "price": meta.get("tier", "free"),
                "modalities": ["text", "image"],
            }
            for mid, meta in MODELS.items()
        ]

    def default_model(self) -> Optional[str]:
        return DEFAULT_MODEL

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": "Agnes AI",
            "badge": "free",
            "tag": "Text-to-video, free tier available",
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
            "modalities": ["text", "image"],  # Fix: actual generate() supports image input
            "aspect_ratios": ["16:9", "9:16", "1:1"],
            "resolutions": ["768p"],
            "max_duration": 10,
            "min_duration": 3,
            "supports_audio": False,
            "supports_negative_prompt": False,
            "max_reference_images": 5,  # Fix: actual generate() supports multi-image/keyframes
        }

    def generate(
        self,
        prompt: str,
        *,
        model: Optional[str] = None,
        image_url: Optional[str] = None,
        duration: Optional[int] = None,
        aspect_ratio: str = "16:9",
        resolution: str = "720p",
        negative_prompt: Optional[str] = None,
        audio: Optional[bool] = None,
        seed: Optional[int] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        import httpx

        prompt = (prompt or "").strip()
        if not prompt:
            return error_response(
                error="prompt is required",
                error_type="missing_prompt",
                provider="agnes",
            )

        api_key = get_api_key("agnes")
        if not api_key:
            return error_response(
                error=(
                    "AGNES_API_KEY not set. Get a free key at "
                    "https://platform.agnes-ai.com"
                ),
                error_type="auth_required",
                provider="agnes",
            )

        model_id = model or _resolve_model()
        meta = MODELS.get(model_id, MODELS[DEFAULT_MODEL])
        num_frames = _pick_frame_count(duration, meta)
        width, height = _pick_resolution(aspect_ratio)

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        payload: Dict[str, Any] = {
            "model": model_id,
            "prompt": prompt,
            "height": height,
            "width": width,
            "num_frames": num_frames,
            "frame_rate": 24,
        }
        if seed is not None:
            payload["seed"] = seed

        # 图像转视频：单图
        if image_url:
            payload["image"] = image_url
        # 多图/关键帧：通过 kwargs 传入
        elif kwargs.get("images"):
            extra_body = {"image": kwargs["images"]}
            if kwargs.get("mode") == "keyframes":
                extra_body["mode"] = "keyframes"
            payload["extra_body"] = extra_body

        # Submit async task
        try:
            resp = httpx.post(
                f"{BASE_URL}/videos",
                headers=headers,
                json=payload,
                timeout=30,
            )
            resp.raise_for_status()
            task_data = resp.json()
        except Exception as exc:
            return error_response(
                error=f"Agnes video task submission failed: {exc}",
                error_type="api_error",
                provider="agnes",
                model=model_id,
                prompt=prompt,
            )

        task_id = task_data.get("id")
        if not task_id:
            return error_response(
                error="Agnes did not return a task ID",
                error_type="empty_response",
                provider="agnes",
                model=model_id,
                prompt=prompt,
            )

        logger.info("[Agnes Video] Task submitted: %s", task_id)

        # Use recommended query endpoint: /agnesapi?video_id=<ID>
        # (per Agnes API docs — Legacy /v1/videos/{id} still works but is deprecated)
        video_id = task_data.get("video_id") or task_id
        poll_url = f"{BASE_URL}/agnesapi?video_id={video_id}"

        # Poll for completion
        deadline = time.time() + POLL_TIMEOUT
        while time.time() < deadline:
            # NOTE: video_generate tool is synchronous (is_async=False),
            # so time.sleep is safe here. If the tool becomes async in the
            # future, change to asyncio.sleep and make generate() async.
            time.sleep(POLL_INTERVAL)
            try:
                result_resp = httpx.get(
                    poll_url,
                    headers=headers,
                    timeout=15,
                )
                result_resp.raise_for_status()
                result = result_resp.json()
            except Exception as exc:
                logger.warning("[Agnes Video] Poll failed: %s", exc)
                continue

            status = result.get("status", "")
            if status == "completed":
                video_url = result.get("video_url") or result.get("url")
                if not video_url:
                    return error_response(
                        error="Agnes completed but returned no video URL",
                        error_type="empty_response",
                        provider="agnes",
                        model=model_id,
                        prompt=prompt,
                    )
                return success_response(
                    video=video_url,
                    model=model_id,
                    prompt=prompt,
                    modality="text",
                    aspect_ratio=aspect_ratio,
                    duration=num_frames // 24,
                    provider="agnes",
                    extra={
                        "task_id": task_id,
                        "num_frames": num_frames,
                        "resolution": f"{width}x{height}",
                    },
                )
            elif status in ("failed", "error"):
                err_msg = result.get("error", "Unknown error")
                return error_response(
                    error=f"Agnes video generation failed: {err_msg}",
                    error_type="api_error",
                    provider="agnes",
                    model=model_id,
                    prompt=prompt,
                )
            # else: still processing, continue polling

        return error_response(
            error=f"Agnes video generation timed out after {POLL_TIMEOUT}s",
            error_type="timeout",
            provider="agnes",
            model=model_id,
            prompt=prompt,
        )


# ---------------------------------------------------------------------------
# Plugin entry point
# ---------------------------------------------------------------------------

def register(ctx) -> None:
    ctx.register_video_gen_provider(AgnesVideoGenProvider())
