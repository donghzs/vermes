"""Tests for agent.vertical_validators — ecosystem validator registration.

Run: python -m pytest tests/test_vertical_validators.py -v
"""

import json
import os
import tempfile
import pytest

from agent.self_validator import SelfValidator, VerifyResult
from agent.vertical_validators import (
    ScholarForgeWriteVerify,
    ScholarForgeCitationVerify,
    StudioImageGenVerify,
    StudioVideoGenVerify,
    register_scholarforge,
    register_studio,
    register_all,
)


@pytest.fixture
def validator():
    """Fresh validator with all ecosystem validators registered."""
    v = SelfValidator(mode="warn")
    register_all(v)
    return v


class FakeAgent:
    pass


# ---------------------------------------------------------------------------
# ScholarForgeWriteVerify tests
# ---------------------------------------------------------------------------


class TestScholarForgeWriteVerify:
    def test_good_output(self, validator):
        text = "# Introduction\n\n" + "word " * 200 + "\n\n## Method\n\n" + "data " * 100
        r = validator.verify_tool_result(
            "scholarforge_write", {}, text, FakeAgent()
        )
        assert r.ok is True
        assert "words" in r.message

    def test_short_output_warning(self, validator):
        r = validator.verify_tool_result(
            "scholarforge_write", {}, "short text", FakeAgent()
        )
        assert r.ok is True
        assert r.is_warning is True
        assert "short" in r.message

    def test_no_sections_warning(self, validator):
        text = "word " * 200  # enough words but no section headings
        r = validator.verify_tool_result(
            "scholarforge_write", {}, text, FakeAgent()
        )
        assert r.ok is True
        assert r.is_warning is True
        assert "section" in r.message

    def test_skipped_on_error(self, validator):
        r = validator.verify_tool_result(
            "scholarforge_write", {}, "error", FakeAgent(), is_error=True
        )
        assert r.ok is True
        assert "skipped" in r.message


# ---------------------------------------------------------------------------
# ScholarForgeCitationVerify tests
# ---------------------------------------------------------------------------


class TestScholarForgeCitationVerify:
    def test_clean_citations(self, validator):
        text = "Smith et al. (2024) found that the results were significant [1]."
        r = validator.verify_tool_result(
            "scholarforge_replace_citations", {}, text, FakeAgent()
        )
        assert r.ok is True
        assert "no placeholder" in r.message

    def test_remaining_placeholders(self, validator):
        text = "As noted by [TODO: find citation], the method works."
        r = validator.verify_tool_result(
            "scholarforge_replace_citations", {}, text, FakeAgent()
        )
        assert r.ok is False
        assert "placeholder" in r.message

    def test_multiple_placeholders(self, validator):
        text = "[TODO: cite1] and [PLACEHOLDER: cite2] and [CITE_xxx]"
        r = validator.verify_tool_result(
            "scholarforge_replace_citations", {}, text, FakeAgent()
        )
        assert r.ok is False
        assert "3" in r.message

    def test_skipped_on_error(self, validator):
        r = validator.verify_tool_result(
            "scholarforge_replace_citations", {}, "", FakeAgent(), is_error=True
        )
        assert r.ok is True


# ---------------------------------------------------------------------------
# StudioImageGenVerify tests
# ---------------------------------------------------------------------------


class TestStudioImageGenVerify:
    def test_local_file_ok(self, validator):
        fd, path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        with open(path, "wb") as f:
            f.write(b"x" * 5000)
        try:
            result = json.dumps({"url": path})
            r = validator.verify_tool_result(
                "studio_image_generate", {}, result, FakeAgent()
            )
            assert r.ok is True
            assert "OK" in r.message
        finally:
            os.remove(path)

    def test_local_file_too_small(self, validator):
        fd, path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        with open(path, "wb") as f:
            f.write(b"x" * 100)  # < 1KB
        try:
            result = json.dumps({"url": path})
            r = validator.verify_tool_result(
                "studio_image_generate", {}, result, FakeAgent()
            )
            assert r.ok is False
            assert "too small" in r.message
        finally:
            os.remove(path)

    def test_remote_url(self, validator):
        result = json.dumps({"url": "https://example.com/image.png"})
        r = validator.verify_tool_result(
            "studio_image_generate", {}, result, FakeAgent()
        )
        assert r.ok is True
        assert "remote" in r.message

    def test_error_in_result(self, validator):
        result = json.dumps({"success": False, "error": "rate limited"})
        r = validator.verify_tool_result(
            "studio_image_generate", {}, result, FakeAgent()
        )
        assert r.ok is False

    def test_no_url(self, validator):
        result = json.dumps({"status": "ok"})
        r = validator.verify_tool_result(
            "studio_image_generate", {}, result, FakeAgent()
        )
        assert r.ok is False

    def test_b64_result(self, validator):
        result = json.dumps({"b64_json": "x" * 2000})
        r = validator.verify_tool_result(
            "studio_image_generate", {}, result, FakeAgent()
        )
        assert r.ok is True


# ---------------------------------------------------------------------------
# StudioVideoGenVerify tests
# ---------------------------------------------------------------------------


class TestStudioVideoGenVerify:
    def test_completed_with_url(self, validator):
        result = json.dumps({
            "status": "completed",
            "url": "https://example.com/video.mp4",
        })
        r = validator.verify_tool_result(
            "studio_video_generate", {}, result, FakeAgent()
        )
        assert r.ok is True
        assert "url" in r.message.lower()

    def test_pending_status_warning(self, validator):
        result = json.dumps({"status": "processing"})
        r = validator.verify_tool_result(
            "studio_video_generate", {}, result, FakeAgent()
        )
        assert r.ok is True
        assert r.is_warning is True
        assert "not completed" in r.message

    def test_error_result(self, validator):
        result = json.dumps({"success": False, "error": "timeout"})
        r = validator.verify_tool_result(
            "studio_video_generate", {}, result, FakeAgent()
        )
        assert r.ok is False

    def test_no_url_error(self, validator):
        result = json.dumps({"status": "completed"})
        r = validator.verify_tool_result(
            "studio_video_generate", {}, result, FakeAgent()
        )
        assert r.ok is False

    def test_non_json_result(self, validator):
        r = validator.verify_tool_result(
            "studio_video_generate", {}, "raw text", FakeAgent()
        )
        assert r.ok is True
        assert "non-JSON" in r.message


# ---------------------------------------------------------------------------
# Registration function tests
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_register_scholarforge(self):
        v = SelfValidator(mode="warn")
        register_scholarforge(v)
        # Verify strategies are registered
        assert "scholarforge_write" in v._strategies
        assert "scholarforge_replace_citations" in v._strategies
        # Verify they work
        text = "# Section\n\n" + "word " * 200
        r = v.verify_tool_result(
            "scholarforge_write", {}, text, FakeAgent()
        )
        assert r.ok is True

    def test_register_studio(self):
        v = SelfValidator(mode="warn")
        register_studio(v)
        assert "studio_image_generate" in v._strategies
        assert "studio_video_generate" in v._strategies

    def test_register_all(self):
        v = SelfValidator(mode="warn")
        register_all(v)
        assert "scholarforge_write" in v._strategies
        assert "scholarforge_replace_citations" in v._strategies
        assert "studio_image_generate" in v._strategies
        assert "studio_video_generate" in v._strategies

    def test_registration_is_idempotent(self):
        v = SelfValidator(mode="warn")
        register_all(v)
        register_all(v)  # should not crash
        assert len(v._strategies) >= 4  # at least 4 ecosystem + built-ins

    def test_registered_validator_works_end_to_end(self):
        v = SelfValidator(mode="warn")
        register_all(v)
        # Test a ScholarForge citation with placeholders
        text = "As noted by [TODO: find source], results are clear."
        r = v.verify_tool_result(
            "scholarforge_replace_citations", {}, text, FakeAgent()
        )
        assert r.ok is False
        assert "placeholder" in r.message
