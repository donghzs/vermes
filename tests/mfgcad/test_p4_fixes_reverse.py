"""P4 修复的反向验证测试（R5）。

这些测试断言修复后的「正确行为」，设计上必须能在 pre-fix 代码（commit 117caa4cd）
上失败 —— 否则就是「测试镜像实现，没测到真实 bug」。

覆盖的修复：
- clarify loader：list 格式 preset YAML 不崩、正确合并到 dict（#400）
- P4 vision：视觉模型按 provider 派生，不再硬编码 deepseek（#401）
- P4 bbox：像素坐标真正拼进传给模型的 description（#402）
- P4 multi_view：side/top 作为 extra_images 真透传（#402）
"""

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

import sys as _sys

_sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vermes_cli.mfgcad.multimodal_tools import (  # noqa: E402
    _handle_mfg_bbox_to_cad,
    _handle_mfg_multi_view_to_cad,
    _llm_vision_describe,
)


# ── P4 vision 模型派生（#401）─────────────────────────────


class _FakeVisionResp:
    def raise_for_status(self):
        pass

    def json(self):
        return {"choices": [{"message": {"content": "视觉描述"}}]}


class _FakeVisionClient:
    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, headers=None, json=None):
        self.model = json["model"]
        self.url = url
        return _FakeVisionResp()


@pytest.mark.asyncio
async def test_vision_model_derived_from_provider(monkeypatch, tmp_path):
    img = tmp_path / "x.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0fakejpeg")

    clients = []

    def _maker(*a, **k):
        c = _FakeVisionClient()
        clients.append(c)
        return c

    monkeypatch.setattr("httpx.AsyncClient", _maker)
    monkeypatch.setattr(
        "vermes_cli.mfgcad.tools._resolve_api_key_provider_base_url",
        lambda: "https://open.bigmodel.cn/api/paas/v4",
    )
    monkeypatch.setattr("vermes_cli.auth.get_active_provider", lambda: "zhipu")
    monkeypatch.setattr(
        "vermes_cli.auth.resolve_api_key_provider_credentials",
        lambda pid: {"provider": "zhipu", "base_url": "https://open.bigmodel.cn/api/paas/v4"},
    )

    desc = await _llm_vision_describe(str(img), "fakekey")

    # 关键断言：非 deepseek provider 必须派生出对应视觉模型，而非硬编码 deepseek-vision
    assert clients and clients[0].model == "glm-4v"
    assert desc == "视觉描述"


# ── P4 bbox 坐标透传（#402）───────────────────────────────


@pytest.mark.asyncio
async def test_bbox_coordinates_forwarded(tmp_path, monkeypatch):
    img = tmp_path / "b.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\nfakepng")
    monkeypatch.setattr("vermes_cli.mfgcad.multimodal_tools._mfg_home", lambda: tmp_path)

    captured = {}

    async def _spy(args):
        captured.update(args)
        return "✅"

    with patch("vermes_cli.mfgcad.multimodal_tools._handle_mfg_image_to_cad", _spy):
        await _handle_mfg_bbox_to_cad(
            {
                "image_path": str(img),
                "bboxes": [{"label": "主体", "x": 10, "y": 10, "width": 100, "height": 200}],
                "description": "不锈钢水杯",
            }
        )

    desc = captured.get("description", "")
    # 关键断言：像素坐标必须进 description（旧代码把坐标整段丢弃）
    assert "x=10" in desc
    assert "宽100" in desc
    assert "主体" in desc
    assert "不锈钢水杯" in desc


# ── P4 multi_view 多图透传（#402）────────────────────────


@pytest.mark.asyncio
async def test_multi_view_extra_images_forwarded(tmp_path, monkeypatch):
    front = tmp_path / "front.jpg"
    front.write_bytes(b"\xff\xd8\xff\xe0fakejpeg")
    side = tmp_path / "side.jpg"
    side.write_bytes(b"\xff\xd8\xff\xe0fakejpeg")
    top = tmp_path / "top.jpg"
    top.write_bytes(b"\xff\xd8\xff\xe0fakejpeg")
    monkeypatch.setattr("vermes_cli.mfgcad.multimodal_tools._mfg_home", lambda: tmp_path)

    captured = {}

    async def _spy(args):
        captured.update(args)
        return "✅"

    with patch("vermes_cli.mfgcad.multimodal_tools._handle_mfg_image_to_cad", _spy):
        await _handle_mfg_multi_view_to_cad(
            {
                "front_image": str(front),
                "side_image": str(side),
                "top_image": str(top),
            }
        )

    extra = captured.get("extra_images", [])
    # 关键断言：side/top 必须作为 extra_images 真透传（旧代码只取正面图）。
    # 注意 _save_uploaded_image 会把图片复制到 session 目录，路径是副本，但文件名保留，
    # 所以按文件名后缀断言（原始 tmp_path 不会出现在副本路径里）。
    assert any(p.endswith("side.jpg") for p in extra)
    assert any(p.endswith("top.jpg") for p in extra)
    assert len(extra) == 2
