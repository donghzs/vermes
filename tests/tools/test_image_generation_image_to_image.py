"""Tests for image-to-image (edit) support in the image_generate tool.

Verifies:
1. Schema exposes image_url and reference_image_urls
2. Handler forwards new params to plugin providers
3. In-tree FAL path rejects edit requests with modality_unsupported
4. Agnes provider routes img2img correctly
5. openai-codex rejects edit requests
"""

import json
import sys
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

# Ensure repo root is on path
REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))


def test_schema_exposes_edit_params():
    """Schema should include image_url and reference_image_urls."""
    from tools.image_generation_tool import IMAGE_GENERATE_SCHEMA
    props = IMAGE_GENERATE_SCHEMA["parameters"]["properties"]
    assert "image_url" in props, "Schema missing image_url"
    assert "reference_image_urls" in props, "Schema missing reference_image_urls"
    assert props["image_url"]["type"] == "string"
    assert props["reference_image_urls"]["type"] == "array"


def test_handler_forwards_image_url_to_plugin():
    """Handler should forward image_url to plugin provider."""
    from tools.image_generation_tool import _handle_image_generate

    captured_kwargs = {}

    class FakeProvider:
        name = "fake"
        def generate(self, prompt, aspect_ratio, **kwargs):
            captured_kwargs.update(kwargs)
            return {"success": True, "image": "http://example.com/img.png",
                    "model": "fake", "prompt": prompt, "aspect_ratio": aspect_ratio,
                    "provider": "fake"}

    with patch("tools.image_generation_tool._read_configured_image_provider", return_value="fake"):
        with patch("tools.image_generation_tool._read_configured_image_model", return_value=None):
            with patch("agent.image_gen_registry.get_provider", return_value=FakeProvider()):
                with patch("vermes_cli.plugins._ensure_plugins_discovered"):
                    result = _handle_image_generate({
                        "prompt": "make it blue",
                        "image_url": "http://example.com/source.png",
                    })
    data = json.loads(result)
    assert data["success"] is True
    assert captured_kwargs.get("image_url") == "http://example.com/source.png"


def test_handler_forwards_reference_images_to_plugin():
    """Handler should forward reference_image_urls to plugin provider."""
    from tools.image_generation_tool import _handle_image_generate

    captured_kwargs = {}

    class FakeProvider:
        name = "fake"
        def generate(self, prompt, aspect_ratio, **kwargs):
            captured_kwargs.update(kwargs)
            return {"success": True, "image": "http://example.com/img.png",
                    "model": "fake", "prompt": prompt, "aspect_ratio": aspect_ratio,
                    "provider": "fake"}

    refs = ["http://example.com/ref1.png", "http://example.com/ref2.png"]
    with patch("tools.image_generation_tool._read_configured_image_provider", return_value="fake"):
        with patch("tools.image_generation_tool._read_configured_image_model", return_value=None):
            with patch("agent.image_gen_registry.get_provider", return_value=FakeProvider()):
                with patch("vermes_cli.plugins._ensure_plugins_discovered"):
                    result = _handle_image_generate({
                        "prompt": "in this style",
                        "reference_image_urls": refs,
                    })
    data = json.loads(result)
    assert data["success"] is True
    assert captured_kwargs.get("reference_image_urls") == refs


def test_in_tree_fal_rejects_edit():
    """In-tree FAL path should reject image_url with modality_unsupported."""
    from tools.image_generation_tool import _handle_image_generate

    # No plugin provider configured → falls through to in-tree FAL
    with patch("tools.image_generation_tool._read_configured_image_provider", return_value=None):
        result = _handle_image_generate({
            "prompt": "edit this",
            "image_url": "http://example.com/source.png",
        })
    data = json.loads(result)
    assert data["success"] is False
    assert data["error_type"] == "modality_unsupported"
    assert "image-to-image" in data["error"]


def test_agnes_capabilities():
    """Agnes provider should declare image + reference support."""
    from plugins.image_gen.agnes import AgnesImageGenProvider
    caps = AgnesImageGenProvider().capabilities()
    assert "image" in caps["modalities"]
    assert caps["max_reference_images"] > 0
    assert "agnes-image-2.0-flash" in caps["edit_supports"]


def test_agnes_img2img_routes_payload():
    """Agnes generate() should include extra_body.image when image_url is given."""
    from plugins.image_gen.agnes import AgnesImageGenProvider
    provider = AgnesImageGenProvider()


class TestMandatoryKeysSurviveWhitelist:
    """A model whose whitelist forgets the mandatory keys must not produce a
    request with the prompt / source images silently stripped."""

    _SIZES = {"square": "1024x1024", "landscape": "1536x1024", "portrait": "1024x1536"}

    def test_edit_keeps_prompt_and_image_urls(self, monkeypatch):
        from tools import image_generation_tool as t

        fake = {
            "size_style": "image_size_preset",
            "sizes": self._SIZES,
            "edit_supports": {"seed"},  # intentionally omits prompt + image_urls
        }
        monkeypatch.setitem(t.FAL_MODELS, "test/edit-model", fake)
        payload = t._build_fal_edit_payload(
            "test/edit-model", "make it blue", ["https://x/y.png"], "square",
        )
        assert payload["prompt"] == "make it blue"
        assert payload["image_urls"] == ["https://x/y.png"]

    def test_text_keeps_prompt(self, monkeypatch):
        from tools import image_generation_tool as t

        fake = {
            "size_style": "image_size_preset",
            "sizes": self._SIZES,
            "supports": {"seed"},  # intentionally omits prompt
        }
        monkeypatch.setitem(t.FAL_MODELS, "test/text-model", fake)
        payload = t._build_fal_payload("test/text-model", "a cat", aspect_ratio="square")
        assert payload["prompt"] == "a cat"


def test_agnes_routes_img2img_with_extra_body():
    """Agnes should route image_url edits with extra_body tags."""
    from plugins.image_gen.agnes import AgnesImageGenProvider
    provider = AgnesImageGenProvider()

    captured = {}

    class FakeResponse:
        def raise_for_status(self): pass
        def json(self): return {"data": [{"url": "http://example.com/out.png"}]}

    def fake_post(url, headers, json, timeout):
        captured["url"] = url
        captured["json"] = json
        return FakeResponse()

    with patch.dict(os.environ, {"AGNES_API_KEY": "test-key", "AGNES_IMAGE_MODEL": "agnes-image-2.0-flash"}):
        with patch("httpx.post", side_effect=fake_post):
            result = provider.generate(
                "make it anime style",
                image_url="http://example.com/src.png",
            )

    assert result["success"] is True
    assert "extra_body" in captured["json"]
    assert captured["json"]["extra_body"]["image"] == ["http://example.com/src.png"]
    assert "http://example.com/src.png" in captured["json"]["extra_body"]["image"]


def test_agnes_rejects_edit_on_text_only_model():
    """Agnes should reject image_url for models that don't support img2img."""
    from plugins.image_gen.agnes import AgnesImageGenProvider
    provider = AgnesImageGenProvider()

    with patch.dict(os.environ, {"AGNES_API_KEY": "test-key", "AGNES_IMAGE_MODEL": "agnes-image-2.1-flash"}):
        result = provider.generate(
            "edit this",
            image_url="http://example.com/src.png",
        )

    assert result["success"] is False
    assert result["error_type"] == "modality_unsupported"


def test_openai_codex_rejects_edit():
    """openai-codex should reject image_url with modality_unsupported."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "openai_codex_plugin",
        REPO / "plugins" / "image_gen" / "openai-codex" / "__init__.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    provider = mod.OpenAICodexImageGenProvider()

    result = provider.generate("edit this", image_url="http://example.com/src.png")
    assert result["success"] is False
    assert result["error_type"] == "modality_unsupported"


def test_normalize_reference_images():
    """normalize_reference_images should handle various inputs."""
    from agent.image_gen_provider import normalize_reference_images

    assert normalize_reference_images(None) == []
    assert normalize_reference_images([]) == []
    assert normalize_reference_images("http://a.com/1.png") == ["http://a.com/1.png"]
    assert normalize_reference_images(["http://a.com/1.png", None, ""]) == ["http://a.com/1.png"]
    assert len(normalize_reference_images(["a", "b", "c"], max_count=2)) == 2


if __name__ == "__main__":
    tests = [
        test_schema_exposes_edit_params,
        test_handler_forwards_image_url_to_plugin,
        test_handler_forwards_reference_images_to_plugin,
        test_in_tree_fal_rejects_edit,
        test_agnes_capabilities,
        test_agnes_img2img_routes_payload,
        test_agnes_rejects_edit_on_text_only_model,
        test_openai_codex_rejects_edit,
        test_normalize_reference_images,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            print(f"✅ {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"❌ {t.__name__}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1
    print(f"\n{passed}/{passed + failed} passed")
    if failed:
        sys.exit(1)
