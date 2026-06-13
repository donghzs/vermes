"""Blueprint: Studio（多模态 API 直通车 — 独立于 Agent）

完全独立于 Agent 会话系统。用户填 base_url + model + api_key + prompt，
直接调厂商 API，不经任何 Agent 逻辑。

Endpoints:
- POST /api/studio/generate         — 生成文本/图片/视频
- POST /api/studio/status/{id}      — 视频状态查询（推荐）
- GET  /api/studio/status/{id}      — 视频状态查询（兼容旧版）
"""

import json
import logging
import httpx
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional

router = APIRouter(prefix="/api/studio", tags=["studio"])


class StudioRequest(BaseModel):
    base_url: str
    model: str
    api_key: str
    mode: str = "text"  # text | image | image2image | video | image2video | multi2video | keyframes
    prompt: str
    system: Optional[str] = ""
    size: Optional[str] = "1024x1024"
    image_url: Optional[str] = ""
    image_data: Optional[str] = ""  # base64 图片数据
    # 视频参数
    num_frames: Optional[int] = 121
    frame_rate: Optional[int] = 24
    width: Optional[int] = 1152
    height: Optional[int] = 768
    image_urls: Optional[list[str]] = None  # 多图/关键帧
    video_mode: Optional[str] = ""  # "keyframes"


class StudioStatusRequest(BaseModel):
    base_url: str = ""
    api_key: str = ""


class StudioResponse(BaseModel):
    success: bool
    mode: str
    text: Optional[str] = None
    image_url: Optional[str] = None
    image_local: Optional[str] = None
    video_url: Optional[str] = None
    video_id: Optional[str] = None
    note: Optional[str] = None
    error: Optional[str] = None


# ═══════════════════════════════════════════════════════
#  工具函数
# ═══════════════════════════════════════════════════════


def _get_root(base_url: str) -> str:
    """从 base_url 提取根域名（去掉 /v1 后缀）"""
    return base_url.rstrip("/v1").rstrip("/")


def _get_client(base_url: str, api_key: str) -> httpx.Client:
    """创建 API client，base_url 为根域名，路径写完整 /v1/xxx"""
    root = _get_root(base_url)
    return httpx.Client(
        base_url=root,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        timeout=180,
        verify=False,
    )


def _save_image(url: str) -> str:
    """下载图片到桌面，返回本地路径"""
    out_dir = Path.home() / "Desktop" / "vermes-studio"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    uid = __import__("uuid").uuid4().hex[:8]
    local_path = out_dir / f"image_{ts}_{uid}.jpg"
    try:
        resp = httpx.get(url, timeout=30, verify=False)
        resp.raise_for_status()
        local_path.write_bytes(resp.content)
        return str(local_path)
    except Exception:
        return ""


def _extract_image_url(content: str) -> Optional[str]:
    """从模型返回文本中提取第一张图片 URL"""
    import re
    urls = re.findall(r"https?://[^\s\)]+\.(?:jpg|jpeg|png|gif|webp)", content)
    return urls[0] if urls else None


def _clean_b64(data: str) -> str:
    """提取纯 base64 内容（去掉 data:... 前缀）"""
    if "," in data:
        return data.split(",", 1)[1]
    return data


def _upload_image_url(client: httpx.Client, data_uri: str, model: str) -> Optional[str]:
    """将 data URL 上传到 API 获取公开 URL"""
    try:
        resp = client.post(
            "/v1/images/generations",
            json={
                "model": model,
                "prompt": "直接输出这张图片，不做任何修改",
                "image": data_uri,
                "size": "1024x1024",
                "n": 1,
            },
        )
        if resp.status_code == 200:
            return resp.json()["data"][0]["url"]
    except Exception:
        pass
    return None


def _infer_image_model(model: str) -> str:
    """从视频模型名推断图片上传用的模型名"""
    m = model.lower()
    if "video" in m or "agnes-video" in m:
        return "agnes-image-2.1-flash"
    return model


def _infer_video_model(model: str) -> str:
    """从图片模型名推断视频模型名"""
    m = model.lower()
    if "image" in m or ("agnes" in m and "video" not in m):
        return "agnes-video-v2.0"
    return model


def _extract_video_url(data: dict) -> str:
    """从视频状态查询响应中提取视频 URL（多种返回格式兼容）"""
    # 标准字段
    for key in ("remixed_from_video_id", "video_url", "url", "output_url", "result_url"):
        val = data.get(key)
        if val and isinstance(val, str) and val.startswith("http"):
            return val
    # 嵌套 output 对象
    output = data.get("output")
    if isinstance(output, dict):
        val = output.get("url") or output.get("video_url") or output.get("result_url") or ""
        if val and isinstance(val, str):
            return val
    # 嵌套 data 对象（Agnes 内层结构）
    inner_data = data.get("data")
    if isinstance(inner_data, dict):
        val = inner_data.get("remixed_from_video_id") or inner_data.get("url") or inner_data.get("video_url") or inner_data.get("result_url") or ""
        if val and isinstance(val, str) and val.startswith("http"):
            return val
    # 嵌套 data 数组
    data_arr = data.get("data")
    if isinstance(data_arr, list) and len(data_arr) > 0:
        first = data_arr[0]
        if isinstance(first, dict):
            val = first.get("url") or first.get("video_url") or ""
            if val and isinstance(val, str):
                return val
    return ""


# ═══════════════════════════════════════════════════════
#  文本生成
# ═══════════════════════════════════════════════════════

def _handle_text(client: httpx.Client, req: StudioRequest) -> StudioResponse:
    messages = []
    if req.system:
        messages.append({"role": "system", "content": req.system})
    messages.append({"role": "user", "content": req.prompt})

    resp = client.post(
        "/v1/chat/completions",
        json={
            "model": req.model,
            "messages": messages,
            "max_tokens": 4096,
            "temperature": 0.85,
        },
    )
    if resp.status_code != 200:
        return StudioResponse(
            success=False,
            mode="text",
            error=f"API 返回 {resp.status_code}: {resp.text[:300]}",
        )
    text = resp.json()["choices"][0]["message"]["content"].strip()
    return StudioResponse(success=True, mode="text", text=text)


# ═══════════════════════════════════════════════════════
#  文生图
# ═══════════════════════════════════════════════════════

def _handle_image(client: httpx.Client, req: StudioRequest) -> StudioResponse:
    """文生图 — 优先 /v1/images/generations，回退 chat/completions"""

    # 自动压制模板/文字效果
    prompt = req.prompt
    suppress = ", no text, no watermark, no signature, no logo, no frame, pure image, natural photography style"
    if not any(kw in prompt.lower() for kw in ["no text", "no watermark", "no logo", "no frame", "without text"]):
        prompt += suppress

    # 方式1：标准文生图
    resp = client.post(
        "/v1/images/generations",
        json={
            "model": req.model,
            "prompt": prompt,
            "size": req.size,
            "n": 1,
        },
    )
    if resp.status_code == 200:
        url = resp.json()["data"][0]["url"]
        local = _save_image(url)
        return StudioResponse(success=True, mode="image", image_url=url, image_local=local)

    # 方式2：回退 chat/completions 多模态（部分厂商图片走文本）
    resp2 = client.post(
        "/v1/chat/completions",
        json={
            "model": req.model,
            "messages": [{"role": "user", "content": f"画一张图: {prompt}"}],
            "max_tokens": 4096,
        },
    )
    if resp2.status_code == 200:
        content = resp2.json()["choices"][0]["message"]["content"]
        url = _extract_image_url(content)
        if url:
            local = _save_image(url)
            return StudioResponse(success=True, mode="image", image_url=url, image_local=local)
        return StudioResponse(
            success=True,
            mode="image",
            text=content,
            note="模型返回了文本（可能不支持图片生成）",
        )

    return StudioResponse(
        success=False,
        mode="image",
        error=f"图片生成失败 ({resp.status_code}/{resp2.status_code})",
    )


# ═══════════════════════════════════════════════════════
#  图生图（3 种 fallback 策略）
# ═══════════════════════════════════════════════════════

def _handle_image2image(client: httpx.Client, req: StudioRequest) -> StudioResponse:
    if not req.image_data:
        return StudioResponse(success=False, mode="image2image", error="未上传参考图片")

    b64 = _clean_b64(req.image_data)
    data_uri = f"data:image/png;base64,{b64}"

    # 方式1：/v1/images/generations + image 字段（Agnes 原生）
    try:
        import json as _json

        _payload = _json.dumps({
            "model": req.model,
            "prompt": req.prompt,
            "image": data_uri,
            "size": req.size,
            "n": 1,
        })
        resp = client.post("/v1/images/generations", content=_payload)
        resp.raise_for_status()
        url = resp.json()["data"][0]["url"]
        local = _save_image(url)
        return StudioResponse(success=True, mode="image2image", image_url=url, image_local=local)
    except Exception:
        pass

    # 方式2：OpenAI 标准 /v1/images/edits
    try:
        import base64

        files = {
            "image": ("ref.png", base64.b64decode(b64), "image/png"),
            "prompt": (None, req.prompt),
            "model": (None, req.model),
            "n": (None, "1"),
            "size": (None, req.size),
        }
        resp = client.post("/v1/images/edits", files=files)
        resp.raise_for_status()
        url = resp.json()["data"][0]["url"]
        local = _save_image(url)
        return StudioResponse(success=True, mode="image2image", image_url=url, image_local=local)
    except Exception:
        pass

    # 方式3：多模态 chat/completions
    try:
        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": req.model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": req.prompt},
                            {"type": "image_url", "image_url": {"url": data_uri}},
                        ],
                    },
                ],
                "max_tokens": 4096,
            },
        )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"]
        url = _extract_image_url(text)
        if url:
            local = _save_image(url)
            return StudioResponse(success=True, mode="image2image", image_url=url, image_local=local)
        return StudioResponse(
            success=True,
            mode="image2image",
            text=text,
            note="模型返回了文本（可能不支持图生图）",
        )
    except Exception:
        pass

    return StudioResponse(
        success=False,
        mode="image2image",
        error="所有图生图方式均失败",
    )


# ═══════════════════════════════════════════════════════
#  视频 — 提交
# ═══════════════════════════════════════════════════════

def _handle_video_submit(client: httpx.Client, req: StudioRequest) -> StudioResponse:
    """提交视频生成任务 — 文生视频/图生视频/多图视频/关键帧"""

    video_model = _infer_video_model(req.model)
    image_model = _infer_image_model(req.model)

    # 收集并上传所有图片 → 确保都是公开 URL
    uploaded_urls: list[str] = []

    if req.image_url:
        uploaded_urls.append(req.image_url)

    if req.image_data:
        b64 = _clean_b64(req.image_data)
        data_uri = f"data:image/png;base64,{b64}"
        url = _upload_image_url(client, data_uri, image_model)
        if url:
            uploaded_urls.append(url)

    if req.image_urls:
        for u in req.image_urls:
            if u.startswith("data:"):
                url = _upload_image_url(client, u, image_model)
                if url:
                    u = url
            if u not in uploaded_urls:
                uploaded_urls.append(u)

    # 构建视频请求体
    payload: dict = {
        "model": video_model,
        "prompt": req.prompt,
        "num_frames": req.num_frames or 121,
        "frame_rate": req.frame_rate or 24,
        "width": req.width or 1152,
        "height": req.height or 768,
    }

    if len(uploaded_urls) == 1:
        payload["image"] = uploaded_urls[0]
    elif len(uploaded_urls) >= 2:
        extra: dict = {"image": uploaded_urls}
        if req.video_mode == "keyframes":
            extra["mode"] = "keyframes"
        payload["extra_body"] = extra

    # 提交
    resp = client.post("/v1/video/generations", json=payload)
    if resp.status_code != 200:
        return StudioResponse(
            success=False,
            mode="video",
            error=f"视频提交失败 ({resp.status_code}): {resp.text[:300]}",
        )

    data = resp.json()
    query_id = (data.get("task_id") or data.get("id") or "").strip()
    if not query_id:
        return StudioResponse(
            success=False,
            mode="video",
            error=f"未返回 task_id: {data}",
        )

    return StudioResponse(
        success=True,
        mode="video",
        video_id=query_id,
        note="已提交，正在排队...",
    )


# ═══════════════════════════════════════════════════════
#  视频 — 状态查询
# ═══════════════════════════════════════════════════════

def _do_video_status(task_id: str, base_url: str, api_key: str) -> StudioResponse:
    """查询视频生成状态（核心实现，供 GET/POST 两入口共用）"""
    if not base_url or not api_key:
        return StudioResponse(success=False, mode="video", error="缺少 base_url 或 api_key")
    try:
        root = _get_root(base_url)
        headers = {"Authorization": f"Bearer {api_key}"}
        with httpx.Client(verify=False, timeout=30, http2=False) as client:
            resp = client.get(f"{root}/v1/video/generations/{task_id}", headers=headers)
        if resp.status_code != 200:
            return StudioResponse(
                success=False,
                mode="video",
                error=f"查询失败 ({resp.status_code})",
            )
        data = resp.json()
        # Agnes 返回结构: {"code":"success","data":{...}} 或顶层直接有 status
        inner = data.get("data") or data
        status = inner.get("status", "").lower()

        if status in ("completed", "success"):
            # 优先找 GCS 直链（inner.data.remixed_from_video_id）
            inner_data_field = inner.get("data")
            video_url = ""
            if isinstance(inner_data_field, dict):
                video_url = inner_data_field.get("remixed_from_video_id") or ""
                if video_url and not video_url.startswith("http"):
                    video_url = ""
            if not video_url:
                video_url = _extract_video_url(inner)
            if not video_url:
                video_url = _extract_video_url(data)
            return StudioResponse(
                success=True,
                mode="video",
                video_url=video_url,
                video_id=task_id,
            )
        elif status in ("failed", "error"):
            err_msg = inner.get("error") or inner.get("fail_reason") or str(inner)
            return StudioResponse(
                success=False,
                mode="video",
                video_id=task_id,
                error=f"视频生成失败: {err_msg}",
            )
        else:
            note = f"processing: {status}"
            progress = inner.get("progress")
            if progress is not None:
                note += f" ({progress}%)"
            return StudioResponse(
                success=False,
                mode="video",
                note=note,
                video_id=task_id,
            )
    except Exception as e:
        return StudioResponse(success=False, mode="video", error=str(e))


# ═══════════════════════════════════════════════════════
#  路由端点
# ═══════════════════════════════════════════════════════


@router.post("/generate", response_model=StudioResponse)
def generate(req: StudioRequest):
    """通用多模态生成端点"""
    try:
        client = _get_client(req.base_url, req.api_key)

        if req.mode == "text":
            return _handle_text(client, req)
        elif req.mode == "image":
            return _handle_image(client, req)
        elif req.mode == "image2image":
            return _handle_image2image(client, req)
        elif req.mode in ("video", "image2video", "multi2video", "keyframes"):
            return _handle_video_submit(client, req)
        else:
            return StudioResponse(
                success=False,
                mode=req.mode,
                error=f"未知模式: {req.mode}",
            )
    except Exception as e:
        logger.error("[Studio] generate error: %s", e)
        return StudioResponse(success=False, mode=req.mode, error=str(e))


@router.post("/status/{task_id}")
def video_status(task_id: str, req: Optional[StudioStatusRequest] = None):
    """查询视频生成状态（推荐：API Key 在 body 中，不暴露到 URL 日志）"""
    base_url = req.base_url if req else ""
    api_key = req.api_key if req else ""
    return _do_video_status(task_id, base_url, api_key)


@router.get("/status/{task_id}")
def video_status_get(task_id: str, base_url: str = "", api_key: str = ""):
    """查询视频生成状态（兼容旧版 GET，api_key 在 URL 参数中 — 建议改用 POST）"""
    return _do_video_status(task_id, base_url, api_key)


# ═══════════════════════════════════════════════════════
#  注册
# ═══════════════════════════════════════════════════════


def register_to(app):
    """注册 Studio 路由到 FastAPI app"""
    app.include_router(router)
    logger.info("[Studio] Blueprint registered at /api/studio")
