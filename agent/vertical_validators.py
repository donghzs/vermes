"""Vertical ecosystem validator registration.

This module provides a registration framework for ecosystem-specific
validators (ScholarForge, Studio, etc.) to plug into the generic
:mod:`agent.self_validator` framework.

Design principles
-----------------
1. **只注册不改** — This file only registers validators.  It does NOT
   import or modify ScholarForge/Studio source code.  Each ecosystem
   app is responsible for providing its own validator class.
2. **延迟加载** — Validators are registered lazily.  If ScholarForge
   is not installed, its registration is silently skipped.
3. **零耦合** — This module has no hard imports from any ecosystem app.
   Validators are passed in as objects implementing VerifyStrategy.

Usage
-----
In ScholarForge's init or blueprint::

    from agent.vertical_validators import register_scholarforge
    register_scholarforge(validator)

In Studio's init::

    from agent.vertical_validators import register_studio
    register_studio(validator)

Or manually::

    from agent.self_validator import get_validator
    from agent.vertical_validators import ScholarForgeWriteVerify
    get_validator().register("scholarforge_write", ScholarForgeWriteVerify())
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Mapping

from agent.self_validator import VerifyResult, get_validator

logger = logging.getLogger("vermes.vertical_validators")


# ---------------------------------------------------------------------------
# ScholarForge validators (lightweight wrappers, no heavy imports)
# ---------------------------------------------------------------------------


class ScholarForgeWriteVerify:
    """Verify ScholarForge writing tasks.

    Checks that the output has reasonable structure: non-empty,
    contains expected section markers, and meets minimum word count.
    """

    name = "scholarforge_write_verify"

    # Minimum thresholds (conservative, warn-only)
    MIN_WORDS = 100
    MIN_SECTIONS = 1

    def verify(
        self,
        tool_name: str,
        args: Mapping[str, Any],
        result: str,
        agent: Any,
        *,
        is_error: bool = False,
    ) -> VerifyResult:
        if is_error:
            return VerifyResult(
                ok=True, tool_name=tool_name, strategy_name=self.name,
                message="skipped (tool already flagged as error)", severity="info",
            )

        # ScholarForge write results are typically long text
        text = result if isinstance(result, str) else str(result)

        # Check minimum length
        word_count = len(text.split())
        if word_count < self.MIN_WORDS:
            return VerifyResult(
                ok=True, tool_name=tool_name, strategy_name=self.name,
                message=f"short output ({word_count} words, min {self.MIN_WORDS})",
                severity="warn",
                extra={"word_count": word_count},
            )

        # Check for section markers (## or # headings)
        section_count = text.count("\n## ") + text.count("\n# ")
        if section_count < self.MIN_SECTIONS:
            return VerifyResult(
                ok=True, tool_name=tool_name, strategy_name=self.name,
                message=f"no section headings found ({word_count} words)",
                severity="warn",
                extra={"word_count": word_count, "section_count": section_count},
            )

        return VerifyResult(
            ok=True, tool_name=tool_name, strategy_name=self.name,
            message=f"scholarforge write ok ({word_count} words, {section_count} sections)",
            severity="info",
            extra={"word_count": word_count, "section_count": section_count},
        )


class ScholarForgeCitationVerify:
    """Verify citation replacement tasks.

    Checks that citations are not placeholder strings and that the
    replacement count is reasonable.
    """

    name = "scholarforge_citation_verify"

    # Known placeholder patterns that should NOT remain in final output
    PLACEHOLDER_PATTERNS = ["[TODO:", "[PLACEHOLDER", "[CITE_", "[??"]

    def verify(
        self,
        tool_name: str,
        args: Mapping[str, Any],
        result: str,
        agent: Any,
        *,
        is_error: bool = False,
    ) -> VerifyResult:
        if is_error:
            return VerifyResult(
                ok=True, tool_name=tool_name, strategy_name=self.name,
                message="skipped (tool already flagged as error)", severity="info",
            )

        text = result if isinstance(result, str) else str(result)

        # Check for remaining placeholders
        remaining = sum(1 for p in self.PLACEHOLDER_PATTERNS if p in text)
        if remaining > 0:
            return VerifyResult(
                ok=False, tool_name=tool_name, strategy_name=self.name,
                message=f"{remaining} placeholder pattern(s) remaining in output",
                severity="error",
                extra={"remaining_placeholders": remaining},
            )

        return VerifyResult(
            ok=True, tool_name=tool_name, strategy_name=self.name,
            message="no placeholder patterns detected",
            severity="info",
        )


# ---------------------------------------------------------------------------
# Studio validators
# ---------------------------------------------------------------------------


class StudioImageGenVerify:
    """Verify Studio image generation results.

    Checks URL accessibility (local files) and file size.
    """

    name = "studio_image_gen_verify"

    MIN_FILE_SIZE = 1024  # 1KB
    MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB

    def verify(
        self,
        tool_name: str,
        args: Mapping[str, Any],
        result: str,
        agent: Any,
        *,
        is_error: bool = False,
    ) -> VerifyResult:
        if is_error:
            return VerifyResult(
                ok=True, tool_name=tool_name, strategy_name=self.name,
                message="skipped (tool already flagged as error)", severity="info",
            )

        from utils import safe_json_loads

        data = safe_json_loads(result)
        if not isinstance(data, dict):
            if result and result.startswith(("http://", "https://")):
                return VerifyResult(
                    ok=True, tool_name=tool_name, strategy_name=self.name,
                    message="image URL returned (remote)", severity="info",
                )
            return VerifyResult(
                ok=True, tool_name=tool_name, strategy_name=self.name,
                message="non-JSON result, skip", severity="info",
            )

        # Check for error
        if data.get("success") is False or data.get("error"):
            return VerifyResult(
                ok=False, tool_name=tool_name, strategy_name=self.name,
                message=f"image generation failed: {data.get('error', 'unknown')}",
                severity="error",
            )

        # Get URL/path
        url = data.get("url") or data.get("image_url") or data.get("output") or ""
        if not url:
            b64 = data.get("b64_json") or ""
            if b64 and len(b64) > 1000:
                return VerifyResult(
                    ok=True, tool_name=tool_name, strategy_name=self.name,
                    message=f"image b64 returned ({len(b64)} chars)", severity="info",
                )
            return VerifyResult(
                ok=False, tool_name=tool_name, strategy_name=self.name,
                message="no url or b64_json in image result", severity="error",
            )

        # Local file check
        if os.path.exists(url):
            file_size = os.path.getsize(url)
            if file_size < self.MIN_FILE_SIZE:
                return VerifyResult(
                    ok=False, tool_name=tool_name, strategy_name=self.name,
                    message=f"image file too small ({file_size} bytes)", severity="error",
                )
            if file_size > self.MAX_FILE_SIZE:
                return VerifyResult(
                    ok=True, tool_name=tool_name, strategy_name=self.name,
                    message=f"image file very large ({file_size} bytes)", severity="warn",
                )
            return VerifyResult(
                ok=True, tool_name=tool_name, strategy_name=self.name,
                message=f"image file OK ({file_size} bytes)", severity="info",
            )

        # Remote URL
        if url.startswith(("http://", "https://")):
            return VerifyResult(
                ok=True, tool_name=tool_name, strategy_name=self.name,
                message="image URL returned (remote)", severity="info",
            )

        return VerifyResult(
            ok=True, tool_name=tool_name, strategy_name=self.name,
            message="image result (unverified)", severity="info",
        )


class StudioVideoGenVerify:
    """Verify Studio video generation results.

    Checks task status and URL presence.
    """

    name = "studio_video_gen_verify"

    def verify(
        self,
        tool_name: str,
        args: Mapping[str, Any],
        result: str,
        agent: Any,
        *,
        is_error: bool = False,
    ) -> VerifyResult:
        if is_error:
            return VerifyResult(
                ok=True, tool_name=tool_name, strategy_name=self.name,
                message="skipped (tool already flagged as error)", severity="info",
            )

        from utils import safe_json_loads

        data = safe_json_loads(result)
        if not isinstance(data, dict):
            return VerifyResult(
                ok=True, tool_name=tool_name, strategy_name=self.name,
                message="non-JSON result, skip", severity="info",
            )

        # Check for error
        if data.get("success") is False or data.get("error"):
            return VerifyResult(
                ok=False, tool_name=tool_name, strategy_name=self.name,
                message=f"video generation failed: {data.get('error', 'unknown')}",
                severity="error",
            )

        # Check task status
        status = data.get("status") or data.get("task_status") or ""
        if status and status not in ("completed", "succeeded", "done", "success"):
            return VerifyResult(
                ok=True, tool_name=tool_name, strategy_name=self.name,
                message=f"video task not completed: status={status}",
                severity="warn",
                extra={"status": status},
            )

        # Check URL
        url = data.get("url") or data.get("video_url") or data.get("output") or ""
        if not url:
            return VerifyResult(
                ok=False, tool_name=tool_name, strategy_name=self.name,
                message="no video url in result", severity="error",
            )

        return VerifyResult(
            ok=True, tool_name=tool_name, strategy_name=self.name,
            message=f"video url returned: {url[:100]}",
            severity="info",
        )


# ---------------------------------------------------------------------------
# Registration functions
# ---------------------------------------------------------------------------


def register_scholarforge(validator=None) -> None:
    """Register ScholarForge validators.

    Call this from ScholarForge's init or blueprint setup::

        from agent.vertical_validators import register_scholarforge
        register_scholarforge()
    """
    if validator is None:
        validator = get_validator()

    validator.register("scholarforge_write", ScholarForgeWriteVerify())
    validator.register("scholarforge_replace_citations", ScholarForgeCitationVerify())
    logger.info("ScholarForge validators registered: write, citation")


def register_studio(validator=None) -> None:
    """Register Studio validators.

    Call this from Studio's init::

        from agent.vertical_validators import register_studio
        register_studio()
    """
    if validator is None:
        validator = get_validator()

    validator.register("studio_image_generate", StudioImageGenVerify())
    validator.register("studio_video_generate", StudioVideoGenVerify())
    logger.info("Studio validators registered: image_gen, video_gen")


def register_all(validator=None) -> None:
    """Register all ecosystem validators.

    Convenience function to register all known ecosystem validators.
    Safe to call multiple times (idempotent — overwrites previous registration).
    """
    register_scholarforge(validator)
    register_studio(validator)
    logger.info("All ecosystem validators registered")
