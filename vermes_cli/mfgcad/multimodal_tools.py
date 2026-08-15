"""
mfgcad 多模态控制工具（P4）。

P4 — 用图片（参考图/草图/bbox 标注）辅助 3D 建模。

设计原则（对齐 P1 多后端架构）：
- 框架层做图片预处理 + 编排，重引擎按需安装
- 图片输入走文件上传 → 保存到 session 目录 → 传给引擎
- 引擎后端可插拔：MAC（不支持多模态）/ TRELLIS（支持图片条件）/ 云 API

工具列表：
- mfg_image_to_cad：参考图/草图 → 3D 模型
- mfg_bbox_to_cad：bbox 标注图 → 3D 模型
- mfg_multi_view_to_cad：多视图（正/侧/顶）→ 3D 模型
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

from tools.registry import registry


# ── 工具 Schema ──────────────────────────────────────────

MFG_IMAGE_TO_CAD_SCHEMA = {
    "name": "mfg_image_to_cad",
    "description": (
        "从参考图/草图生成 3D 模型。上传一张图片（手绘草图/产品照片/概念图），AI 生成对应的 3D 模型。\n"
        "后端：trellis（TRELLIS 2 图片条件生成，需安装引擎）/ cloud_api（云服务）/ mac（提取图片特征后走 MAC 建模）。\n"
        "输出：STEP/STL（MAC）或 GLB（TRELLIS）。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "image_path": {
                "type": "string",
                "description": "图片文件路径（jpg/png/webp）。可通过前端上传或指定本地路径。",
            },
            "description": {
                "type": "string",
                "description": "附加文字描述（可选）：补充图片无法表达的细节，如尺寸、材质等。",
            },
            "backend": {
                "type": "string",
                "enum": ["auto", "trellis", "cloud_api", "mac"],
                "default": "auto",
                "description": "后端选择：auto=自动 / trellis=本地 TRELLIS / cloud_api=云服务 / mac=提取特征走 MAC",
            },
            "session_id": {"type": "string", "description": "可选，指定会话 ID 以续作"},
        },
        "required": ["image_path"],
    },
}

MFG_BBOX_TO_CAD_SCHEMA = {
    "name": "mfg_bbox_to_cad",
    "description": (
        "从 bbox 标注图生成 3D 模型。在图片上画矩形框标注物体的边界和关键部位，AI 据此建模。\n"
        "适用于：多个零件的装配关系、空间布局、相对尺寸。\n"
        "后端同 mfg_image_to_cad。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "image_path": {"type": "string", "description": "带 bbox 标注的图片路径"},
            "bboxes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string", "description": "部位名称，如 '主体'/'把手'/'底座'"},
                        "x": {"type": "number", "description": "左上角 X 坐标（像素）"},
                        "y": {"type": "number", "description": "左上角 Y 坐标（像素）"},
                        "width": {"type": "number", "description": "宽度（像素）"},
                        "height": {"type": "number", "description": "高度（像素）"},
                    },
                },
                "description": "bbox 标注列表（可选，无则纯图片条件生成）",
            },
            "description": {"type": "string", "description": "附加文字描述"},
            "session_id": {"type": "string", "description": "可选会话 ID"},
        },
        "required": ["image_path"],
    },
}

MFG_MULTI_VIEW_SCHEMA = {
    "name": "mfg_multi_view_to_cad",
    "description": (
        "从多视图（正面/侧面/顶部）生成 3D 模型。上传 2-3 张不同角度的图片。\n"
        "适用于：已有手绘三视图的零件、产品多角度照片。\n"
        "后端同 mfg_image_to_cad。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "front_image": {"type": "string", "description": "正面视图图片路径"},
            "side_image": {"type": "string", "description": "侧面视图图片路径"},
            "top_image": {"type": "string", "description": "顶部视图图片路径（可选）"},
            "description": {"type": "string", "description": "附加文字描述"},
            "session_id": {"type": "string", "description": "可选会话 ID"},
        },
        "required": ["front_image", "side_image"],
    },
}


# ── 辅助函数 ─────────────────────────────────────────────


def _mfg_home() -> Path:
    return Path.home() / ".vermes" / "mfgcad"


def _resolve_image_backend(backend: str) -> str:
    """自动选择图片条件后端。"""
    if backend != "auto":
        return backend
    # 优先 trellis（本地）
    trellis_dir = Path.home() / ".vermes" / "engines" / "trellis"
    if trellis_dir.is_dir() and (trellis_dir / "run_trellis.py").is_file():
        try:
            import torch
            if torch.cuda.is_available() or (hasattr(torch.backends, "mps") and torch.backends.mps.is_available()):
                return "trellis"
        except ImportError:
            pass
    # 云 API
    if os.environ.get("TRELLIS_CLOUD_API_KEY"):
        return "cloud_api"
    # fallback：MAC（提取图片特征 → NL 描述 → MAC 建模）
    return "mac"


def _save_uploaded_image(image_path: str, session_id: str) -> str:
    """把图片复制到 session 目录，返回新路径。"""
    src = Path(image_path)
    if not src.is_file():
        return ""

    img_dir = _mfg_home() / "sessions" / session_id / "images" / str(int(time.time()))
    img_dir.mkdir(parents=True, exist_ok=True)
    dst = img_dir / src.name
    try:
        import shutil
        shutil.copy2(str(src), str(dst))
    except Exception:
        return ""
    return str(dst)


# provider → 视觉模型映射（仅覆盖常见 provider；未列出走 env 覆盖或通用兜底）。
# 目的：消除 P4 初版硬编码 deepseek 导致的「非 deepseek provider 多模态必 401」。
_VISION_MODEL_BY_PROVIDER = {
    "deepseek": "deepseek-vision",
    "openai": "gpt-4o",
    "azure-openai": "gpt-4o",
    "dashscope": "qwen-vl-max",
    "qwen": "qwen-vl-max",
    "siliconflow": "Qwen/Qwen2.5-VL-72B-Instruct",
    "zhipu": "glm-4v",
    "moonshot": "moonshot-v1-8k-vision",
}


# ── Handlers ─────────────────────────────────────────────


async def _handle_mfg_image_to_cad(args: dict, **kw: Any) -> str:
    """参考图/草图 → 3D 模型。"""
    image_path = (args.get("image_path") or "").strip()
    description = (args.get("description") or "").strip()
    backend_choice = (args.get("backend") or "auto").strip()
    session_id = (args.get("session_id") or f"img_{int(time.time())}").strip()
    extra_images = args.get("extra_images") or []

    if not image_path:
        return "❌ 缺少参数 image_path。"

    if not Path(image_path).is_file():
        return f"❌ 图片文件不存在：{image_path}"

    # 保存图片到 session 目录
    saved_path = _save_uploaded_image(image_path, session_id)
    if not saved_path:
        return f"❌ 图片保存失败：{image_path}"

    backend = _resolve_image_backend(backend_choice)
    output_dir = _mfg_home() / "sessions" / session_id / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    if backend == "mac":
        # MAC 不支持图片输入 → 用 LLM vision 把图片转 NL 描述 → 走 MAC 建模
        from vermes_cli.mfgcad.tools import _resolve_api_key
        key = _resolve_api_key()
        if not key:
            return "❌ MAC 后端需要 LLM API key。请配置后重试或使用 trellis/cloud_api 后端。"

        # 用 LLM vision 提取描述（多视图时把其余视图一并传入）
        img_desc = description
        if not img_desc:
            try:
                img_desc = await _llm_vision_describe(saved_path, key, extra_images)
            except Exception as e:
                return f"❌ 图片描述提取失败: {e}\n请手动提供 description 参数。"

        # 调 mfg_text_to_cad
        from vermes_cli.mfgcad.engine_backends import resolve_backend
        try:
            b = resolve_backend({"engine": "mac"})
        except RuntimeError as e:
            return f"❌ MAC 引擎未就绪: {e}"

        env = dict(os.environ)
        env["DASHSCOPE_API_KEY"] = key
        env["OPENAI_API_KEY"] = key

        try:
            result = await b.generate(
                request=f"根据参考图建模：{img_desc}",
                output_dir=str(output_dir),
                preset=None,
                env=env,
            )
        except Exception as e:
            return f"❌ 建模失败: {e}"

        files = result.files or {}
        return (
            f"✅ 从图片生成 3D 模型完成\n"
            + (f"STEP: {files.get('step', '')}\n" if files.get("step") else "")
            + (f"STL: {files.get('stl', '')}\n" if files.get("stl") else "")
            + (f"3MF: {files.get('3mf', '')}\n" if files.get("3mf") else "")
            + f"图片：{saved_path}\n"
            + (f"描述：{img_desc}\n" if img_desc else "")
            + f"后端：mac（图片→NL→MAC 建模）\n"
            + f"会话 session_id={session_id}。"
        )

    elif backend in ("trellis", "cloud_api"):
        from vermes_cli.mfgcad.engine_backends import TrellisBackend
        tb = TrellisBackend()

        if not tb.is_available():
            return (
                "❌ TRELLIS 引擎未就绪。安装方式：\n"
                "  ① 本地：~/.vermes/engines/trellis/ + torch(CUDA/MPS)\n"
                "  ② 云：设 TRELLIS_CLOUD_API_KEY 环境变量"
            )

        # TRELLIS 支持图片条件
        env = dict(os.environ)
        try:
            result = await tb.generate(
                request=description or "根据参考图生成 3D 模型",
                output_dir=str(output_dir),
                preset={"engine": "trellis"},
                env=env,
                image_path=saved_path,
            )
        except Exception as e:
            return f"❌ TRELLIS 生成失败: {e}"

        files = result.files or {}
        return (
            f"✅ 从图片生成 3D 模型完成\n"
            + (f"GLB: {files.get('glb', '')}\n" if files.get("glb") else "")
            + (f"预览图: {files.get('png', '')}\n" if files.get("png") else "")
            + f"输入图片：{saved_path}\n"
            + f"后端：{backend}\n"
            + f"会话 session_id={session_id}。"
        )

    else:
        return f"❌ 未知后端: {backend}"


async def _handle_mfg_bbox_to_cad(args: dict, **kw: Any) -> str:
    """bbox 标注图 → 3D 模型。"""
    image_path = (args.get("image_path") or "").strip()
    bboxes = args.get("bboxes") or []
    description = (args.get("description") or "").strip()
    session_id = (args.get("session_id") or f"bbox_{int(time.time())}").strip()

    if not image_path or not Path(image_path).is_file():
        return f"❌ 图片不存在：{image_path}"

    saved_path = _save_uploaded_image(image_path, session_id)

    # 把 bbox 信息（含像素坐标）拼成文字描述，真正传给模型做空间布局
    bbox_desc = description
    if bboxes:
        parts = []
        for b in bboxes:
            label = b.get("label", "未知")
            x = b.get("x"); y = b.get("y"); w = b.get("width"); h = b.get("height")
            parts.append(f"{label}(左上x={x},y={y},宽{w},高{h})")
        bbox_desc = f"标注部位及像素坐标：{', '.join(parts)}。{description}"

    # 复用 image_to_cad 逻辑
    return await _handle_mfg_image_to_cad({
        "image_path": saved_path,
        "description": bbox_desc,
        "backend": "auto",
        "session_id": session_id,
    })


async def _handle_mfg_multi_view_to_cad(args: dict, **kw: Any) -> str:
    """多视图 → 3D 模型。"""
    front = (args.get("front_image") or "").strip()
    side = (args.get("side_image") or "").strip()
    top = args.get("top_image", "").strip()
    description = (args.get("description") or "").strip()
    session_id = (args.get("session_id") or f"mv_{int(time.time())}").strip()

    if not front or not side:
        return "❌ 至少需要 front_image 和 side_image。"

    for p in [front, side, top]:
        if p and not Path(p).is_file():
            return f"❌ 图片不存在：{p}"

    # 保存所有视图
    views = {}
    for label, p in [("front", front), ("side", side), ("top", top)]:
        if p:
            views[label] = _save_uploaded_image(p, session_id)

    # 拼描述
    view_desc = f"多视图建模：正面视图、侧面视图"
    if top:
        view_desc += "、顶部视图"
    if description:
        view_desc += f"。{description}"

    # 走 image_to_cad（正面图作主参考，side/top 作为 extra_images 真透传给视觉模型）
    extra = [p for p in [views.get("side"), views.get("top")] if p]
    return await _handle_mfg_image_to_cad({
        "image_path": views["front"],
        "description": view_desc,
        "backend": "auto",
        "session_id": session_id,
        "extra_images": extra,
    })


async def _llm_vision_describe(image_path: str, api_key: str, image_paths: list[str] | None = None) -> str:
    """用 LLM vision 模型把图片转成文字描述。

    模型与 base_url 一律从统一凭证层派生（不再硬编码 deepseek），保证任意
    活跃 provider 的多模态路径都不会因 base_url/模型不匹配而 401。
    - 视觉模型：env MFGCAD_VISION_MODEL > provider 映射 > 通用 gpt-4o 兜底
    - base_url：活跃 provider 的 base_url；为空则兜底 OpenAI 兼容地址
    - image_paths：多视图时传入其余视图，一并发给视觉模型
    """
    import base64

    from vermes_cli.mfgcad.tools import _resolve_api_key_provider_base_url

    # base_url 统一从凭证层解析
    base_url = _resolve_api_key_provider_base_url()
    if not base_url:
        base_url = "https://api.openai.com/v1"  # 兜底：通用 OpenAI 兼容地址

    # 视觉模型：mfgcad 专属 model > env 覆盖 > provider 映射 > 通用兜底
    model = ""
    try:
        from vermes_cli.mfgcad.tools import _resolve_mfgcad_model
        model = _resolve_mfgcad_model()
    except Exception:
        pass
    if not model:
        model = os.environ.get("MFGCAD_VISION_MODEL")
    if not model:
        try:
            from vermes_cli.auth import (
                get_active_provider,
                resolve_api_key_provider_credentials,
            )
            pid = get_active_provider()
            if pid:
                creds = resolve_api_key_provider_credentials(pid) or {}
                model = _VISION_MODEL_BY_PROVIDER.get(creds.get("provider", pid), "gpt-4o")
        except Exception:
            model = "gpt-4o"

    # 组装多图 content（主图 + 其余视图）
    all_images = [image_path] + [p for p in (image_paths or []) if p and p != image_path]
    content: list[dict] = [{
        "type": "text",
        "text": "详细描述这张图片中的物体形状、尺寸比例、结构特征，用于 3D 建模。用简洁的技术语言。",
    }]
    for img in all_images:
        try:
            with open(img, "rb") as f:
                img_b64 = base64.b64encode(f.read()).decode()
        except Exception:
            continue
        ext = Path(img).suffix.lower()
        mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}.get(ext, "image/jpeg")
        content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{img_b64}"}})

    import httpx
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": content}],
                "max_tokens": 800,
            },
        )
        resp.raise_for_status()
        data = resp.json()

    return data["choices"][0]["message"]["content"]


# ── 工具注册 ─────────────────────────────────────────────


def register_tools(host_api=None):
    """注册 mfgcad P4 多模态控制工具。"""
    from tools.registry import registry

    for schema, handler in [
        (MFG_IMAGE_TO_CAD_SCHEMA, _handle_mfg_image_to_cad),
        (MFG_BBOX_TO_CAD_SCHEMA, _handle_mfg_bbox_to_cad),
        (MFG_MULTI_VIEW_SCHEMA, _handle_mfg_multi_view_to_cad),
    ]:
        registry.register(
            name=schema["name"],
            handler=handler,
            schema=schema,
            toolset="mfgcad",
        )
