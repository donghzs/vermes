"""
Universal OpenAI-Compatible Video Generation Backend
=====================================================

One provider for all async-task-style video APIs. Supports the two most
common patterns:

Pattern 1 — Agnes style:
  POST {base_url}/videos           → {task_id/video_id, status: "queued"}
  GET  {base_url}/videos/{id}      → {status: "completed", video_url: "..."}

Pattern 2 — Agnes recommended (newer):
  POST {base_url}/videos           → {video_id, status: "queued"}
  GET  {base_url}/agnesapi?video_id={id} → {status: "completed", video_url: "..."}

Pattern 3 — xAI style:
  POST {base_url}/videos/generations  → {id, status: "queued"}
  GET  {base_url}/videos/generations/{id} → {status: "completed", ...}

All three are abstracted into a configurable submit + poll pair.
Configure via video_gen in config.yaml:

    video_gen:
      provider: openai_compat
      base_url: https://apihub.agnes-ai.com/v1
      api_key: ${AGNES_API_KEY}
      model: agnes-video-v2.0
      # Submit endpoint (relative to base_url):
      submit_path: /videos          # default; or /videos/generations
      # Poll endpoint pattern (relative to base_url):
      poll_path: /videos/{id}       # default; or /agnesapi?video_id={id}
      # Optional:
      poll_interval: 10             # seconds between polls (default 10)
      poll_timeout: 600             # max seconds (default 600)
      default_width: 1152
      default_height: 768
      default_num_frames: 121
      default_fps: 24

No hardcoded URLs, no hardcoded models.
"""

from __future__ import annotations

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
# Config resolution
# ---------------------------------------------------------------------------

def _load_video_gen_config() -> Dict[str, Any]:
    try:
        from hermes_cli.config import load_config
        cfg = load_config()
        section = cfg.get("video_gen") if isinstance(cfg, dict) else None
        return section if isinstance(section, dict) else {}
    except Exception:
        return {}


def _resolve_base_url(cfg: Dict[str, Any]) -> Optional[str]:
    url = cfg.get("base_url") or cfg.get("openai_compat", {}).get("base_url")
    if isinstance(url, str) and url.strip():
        return url.strip().rstrip("/")
    for env_key in ("VIDEO_GEN_BASE_URL", "AGNES_API_BASE", "XAI_API_BASE"):
        val = os.environ.get(env_key)
        if val:
            return val.strip().rstrip("/")
    return None


def _resolve_api_key(cfg: Dict[str, Any]) -> Optional[str]:
    key = cfg.get("api_key") or cfg.get("openai_compat", {}).get("api_key")
    if isinstance(key, str) and key.strip():
        if key.startswith("${") and key.endswith("}"):
            return os.environ.get(key[2:-1])
        return key.strip()
    for env_key in ("VIDEO_GEN_API_KEY", "AGNES_API_KEY", "XAI_API_KEY"):
        val = os.environ.get(env_key)
        if val:
            return val
    return None


def _resolve_model(cfg: Dict[str, Any]) -> Optional[str]:
    model = cfg.get("model")
    if isinstance(model, str) and model.strip():
        return model.strip()
    return os.environ.get("VIDEO_GEN_MODEL")


def _resolve_dimensions(cfg: Dict[str, Any], aspect_ratio: str) -> tuple:
    """Return (width, height) from config or aspect ratio."""
    width = cfg.get("default_width")
    height = cfg.get("default_height")
    if width and height:
        return int(width), int(height)
    # Aspect ratio defaults
    ratio_map = {
        "16:9": (1152, 768),
        "9:16": (768, 1152),
        "1:1": (768, 768),
        "4:3": (1024, 768),
        "3:4": (768, 1024),
    }
    return ratio_map.get(aspect_ratio, (1152, 768))


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

class OpenAICompatVideoGenProvider(VideoGenProvider):
    """Universal OpenAI-compatible video generation backend.

    Supports async task pattern: submit → poll → return video URL.
    Configured entirely via config.yaml.
    """

    @property
    def name(self) -> str:
        return "openai_compat"

    @property
    def display_name(self) -> str:
        return "OpenAI-Compatible (Universal)"

    def is_available(self) -> bool:
        cfg = _load_video_gen_config()
        return bool(_resolve_base_url(cfg) and _resolve_api_key(cfg))

    def list_models(self) -> List[Dict[str, Any]]:
        cfg = _load_video_gen_config()
        model = _resolve_model(cfg)
        if not model:
            return []
        return [
            {
                "id": model,
                "display": model,
                "speed": "varies",
                "strengths": "User-configured model",
                "price": "varies",
                "modalities": ["text", "image"],
            }
        ]

    def default_model(self) -> Optional[str]:
        cfg = _load_video_gen_config()
        return _resolve_model(cfg)

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": "OpenAI-Compatible (Universal)",
            "badge": "universal",
            "tag": "Any async video API — configure base_url + api_key + model + endpoints",
            "env_vars": [],
        }

    def capabilities(self) -> Dict[str, Any]:
        return {
            "modalities": ["text", "image"],
            "aspect_ratios": ["16:9", "9:16", "1:1", "4:3", "3:4"],
            "resolutions": ["720p", "1080p"],
            "max_duration": 10,
            "min_duration": 3,
            "supports_audio": False,
            "supports_negative_prompt": True,
            "max_reference_images": 5,
        }

    def generate(
        self,
        prompt: str,
        *,
        model: Optional[str] = None,
        image_url: Optional[str] = None,
        reference_image_urls: Optional[List[str]] = None,
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
                provider="openai_compat",
            )

        cfg = _load_video_gen_config()
        base_url = _resolve_base_url(cfg)
        api_key = _resolve_api_key(cfg)
        resolved_model = model or _resolve_model(cfg)

        if not base_url:
            return error_response(
                error=(
                    "video_gen.base_url not configured. Set it in config.yaml:\n"
                    "  video_gen:\n"
                    "    provider: openai_compat\n"
                    "    base_url: https://apihub.agnes-ai.com/v1\n"
                    "    api_key: ${AGNES_API_KEY}\n"
                    "    model: agnes-video-v2.0"
                ),
                error_type="config_required",
                provider="openai_compat",
            )

        if not api_key:
            return error_response(
                error="No API key found. Set video_gen.api_key in config.yaml.",
                error_type="auth_required",
                provider="openai_compat",
            )

        if not resolved_model:
            return error_response(
                error="No model configured. Set video_gen.model in config.yaml.",
                error_type="config_required",
                provider="openai_compat",
            )

        # Resolve endpoint paths
        submit_path = cfg.get("submit_path", "/videos")
        poll_path_pattern = cfg.get("poll_path", "/videos/{id}")
        poll_interval = int(cfg.get("poll_interval", 10))
        poll_timeout = int(cfg.get("poll_timeout", 600))

        # Resolve video parameters
        width, height = _resolve_dimensions(cfg, aspect_ratio)
        num_frames = int(cfg.get("default_num_frames", 121))
        fps = int(cfg.get("default_fps", 24))

        # If duration is specified, pick closest valid frame count (8n+1 rule)
        if duration:
            target = duration * fps
            valid_counts = [81, 121, 161, 241]
            num_frames = min(valid_counts, key=lambda c: abs(c - target))

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        # Build submit payload
        payload: Dict[str, Any] = {
            "model": resolved_model,
            "prompt": prompt,
            "height": height,
            "width": width,
            "num_frames": num_frames,
            "frame_rate": fps,
        }

        if seed is not None:
            payload["seed"] = seed
        if negative_prompt:
            payload["negative_prompt"] = negative_prompt

        # Image-to-video: single image
        if image_url:
            payload["image"] = image_url

        # Multi-image / keyframes: via extra_body.image
        elif reference_image_urls:
            payload["extra_body"] = {
                "image": list(reference_image_urls),
            }

        # Submit task
        try:
            submit_url = f"{base_url}{submit_path}"
            resp = httpx.post(submit_url, headers=headers, json=payload, timeout=30)
            resp.raise_for_status()
            task_data = resp.json()
        except httpx.HTTPStatusError as exc:
            err_detail = ""
            try:
                err_body = exc.response.json()
                err_detail = err_body.get("error", {}).get("message", "") or str(err_body)
            except Exception:
                err_detail = exc.response.text[:500]
            return error_response(
                error=f"Submit failed ({exc.response.status_code}): {err_detail}",
                error_type="api_error",
                provider="openai_compat",
                model=resolved_model,
                prompt=prompt,
            )
        except Exception as exc:
            return error_response(
                error=f"Submit request failed: {exc}",
                error_type="api_error",
                provider="openai_compat",
                model=resolved_model,
                prompt=prompt,
            )

        # Extract task ID (support multiple response formats)
        task_id = task_data.get("video_id") or task_data.get("task_id") or task_data.get("id")
        if not task_id:
            return error_response(
                error="API did not return a task ID",
                error_type="empty_response",
                provider="openai_compat",
                model=resolved_model,
                prompt=prompt,
            )

        logger.info("[Video] Task submitted: %s (model=%s)", task_id, resolved_model)

        # Poll for completion
        poll_url = f"{base_url}{poll_path_pattern.format(id=task_id)}"
        deadline = time.time() + poll_timeout

        while time.time() < deadline:
            time.sleep(poll_interval)

            try:
                result_resp = httpx.get(poll_url, headers=headers, timeout=15)
                result_resp.raise_for_status()
                result = result_resp.json()
            except Exception as exc:
                logger.warning("[Video] Poll failed for %s: %s", task_id, exc)
                continue

            status = result.get("status", "")

            if status in ("completed", "succeeded", "success"):
                # Extract video URL from various response formats
                video_url = (
                    result.get("video_url")
                    or result.get("url")
                    or result.get("output_url")
                    or result.get("result_url")
                )
                # Check nested output object
                if not video_url and isinstance(result.get("output"), dict):
                    video_url = (
                        result["output"].get("url")
                        or result["output"].get("video_url")
                    )
                # Check nested data object
                if not video_url and isinstance(result.get("data"), dict):
                    video_url = (
                        result["data"].get("url")
                        or result["data"].get("video_url")
                    )

                if not video_url:
                    return error_response(
                        error="Task completed but no video URL in response",
                        error_type="empty_response",
                        provider="openai_compat",
                        model=resolved_model,
                        prompt=prompt,
                    )

                modality = "image" if image_url or reference_image_urls else "text"
                return success_response(
                    video=video_url,
                    model=resolved_model,
                    prompt=prompt,
                    modality=modality,
                    aspect_ratio=aspect_ratio,
                    duration=num_frames // fps,
                    provider="openai_compat",
                    extra={
                        "task_id": task_id,
                        "num_frames": num_frames,
                        "resolution": f"{width}x{height}",
                    },
                )

            elif status in ("failed", "error", "canceled"):
                err_msg = result.get("error", result.get("message", "Unknown error"))
                return error_response(
                    error=f"Video generation failed: {err_msg}",
                    error_type="api_error",
                    provider="openai_compat",
                    model=resolved_model,
                    prompt=prompt,
                )

            # Still processing — log progress if available
            progress = result.get("progress")
            if progress is not None:
                logger.info("[Video] %s progress: %s%%", task_id, progress)

        return error_response(
            error=f"Video generation timed out after {poll_timeout}s",
            error_type="timeout",
            provider="openai_compat",
            model=resolved_model,
            prompt=prompt,
        )


# ---------------------------------------------------------------------------
# Plugin entry point
# ---------------------------------------------------------------------------

def register(ctx) -> None:
    ctx.register_video_gen_provider(OpenAICompatVideoGenProvider())
