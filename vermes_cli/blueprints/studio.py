"""Blueprint: Studio（多模态 API 直通车 — 独立于 Agent）

完全独立于 Agent 会话系统。用户填 base_url + model + api_key + prompt，
直接调厂商 API，不经任何 Agent 逻辑。

Endpoints:
- GET  /api/studio/providers       — 动态返回可用 provider 列表（从 config.yaml + 插件注册表）
- POST /api/studio/providers       — 新增 provider 配置（写入 config.yaml）
- DELETE /api/studio/providers/{name} — 删除 provider 配置
- POST /api/studio/models          — 拉取厂商实时模型列表（调用 /v1/models）
- POST /api/studio/generate         — 生成文本/图片/视频
- POST /api/studio/status/{id}      — 视频状态查询（推荐）
- GET  /api/studio/status/{id}      — 视频状态查询（兼容旧版）
"""

import json
import logging
import os
import httpx
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional, Any, Dict, List

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

    # 方式1：/v1/images/generations + extra_body.image 数组（OpenAI 兼容格式，per Agnes API 文档）
    try:
        import json as _json

        _payload = _json.dumps({
            "model": req.model,
            "prompt": req.prompt,
            "extra_body": {
                "image": [data_uri],
            },
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


# ═══════════════════════════════════════════════════════
#  动态 Provider / Model 端点
# ═══════════════════════════════════════════════════════

# 内置预设 — 作为 fallback，当 config.yaml 没有配置时使用
_BUILTIN_PRESETS = [
    {
        "name": "agnes",
        "label": "Agnes AI",
        "icon": "🧠",
        "baseUrl": "https://apihub.agnes-ai.com/v1",
        "text": "agnes-2.0-flash",
        "image": "agnes-image-2.1-flash",
        "video": "agnes-video-v2.0",
        "keyEnv": "AGNES_API_KEY",
    },
    {
        "name": "deepseek",
        "label": "DeepSeek",
        "icon": "🔍",
        "baseUrl": "https://api.deepseek.com",
        "text": "deepseek-chat",
        "image": "",
        "video": "",
        "keyEnv": "DEEPSEEK_API_KEY",
    },
    {
        "name": "xiaomi",
        "label": "小米 MiMo",
        "icon": "📱",
        "baseUrl": "https://api.xiaomimimo.com/v1",
        "text": "mimo-v2.5-pro",
        "image": "mimo-v2.5-pro",
        "video": "mimo-v2.5-pro",
        "keyEnv": "XIAOMI_API_KEY",
    },
    {
        "name": "openai",
        "label": "OpenAI",
        "icon": "⚡",
        "baseUrl": "https://api.openai.com/v1",
        "text": "gpt-4o",
        "image": "dall-e-3",
        "video": "",
        "keyEnv": "OPENAI_API_KEY",
    },
    {
        "name": "alibaba",
        "label": "阿里通义",
        "icon": "☁️",
        "baseUrl": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "text": "qwen-max",
        "image": "qwen-vl-max",
        "video": "",
        "keyEnv": "DASHSCOPE_API_KEY",
    },
]


def _load_studio_config() -> Dict[str, Any]:
    """从 config.yaml 读取 studio 段。"""
    try:
        from vermes_cli.config import load_config
        cfg = load_config()
        section = cfg.get("studio") if isinstance(cfg, dict) else None
        return section if isinstance(section, dict) else {}
    except Exception:
        return {}


def _resolve_env_value(raw: str) -> str:
    """支持 ${ENV_VAR} 语法。"""
    if isinstance(raw, str) and raw.startswith("${") and raw.endswith("}"):
        return os.environ.get(raw[2:-1], "")
    return raw or ""


@router.get("/providers")
def list_providers():
    """返回可用 provider 列表。

    合并三个来源：
    1. config.yaml 中 studio.providers（用户自定义）
    2. config.yaml 中 image_gen / video_gen 配置（Agent 工具配置也可用于 Studio）
    3. 内置预设（_BUILTIN_PRESETS）作为 fallback

    去重逻辑：同 baseUrl 的以 config.yaml 为优先。
    """
    result: List[Dict[str, Any]] = []
    seen_urls = set()

    # 1. 从 config.yaml studio.providers 读取
    studio_cfg = _load_studio_config()
    custom_providers = studio_cfg.get("providers")
    if isinstance(custom_providers, list):
        for p in custom_providers:
            if not isinstance(p, dict):
                continue
            url = p.get("baseUrl", p.get("base_url", ""))
            if url and url not in seen_urls:
                seen_urls.add(url)
                result.append({
                    "name": p.get("name", "custom"),
                    "label": p.get("label", p.get("name", "自定义")),
                    "icon": p.get("icon", "🔧"),
                    "baseUrl": url,
                    "text": p.get("text", p.get("model", "")),
                    "image": p.get("image", ""),
                    "video": p.get("video", ""),
                    "keyEnv": p.get("keyEnv", ""),
                })

    # 2. 从 image_gen / video_gen 配置读取（Agent 工具配置共享）
    try:
        from vermes_cli.config import load_config
        cfg = load_config()
        for section_key in ("image_gen", "video_gen"):
            section = cfg.get(section_key) if isinstance(cfg, dict) else None
            if not isinstance(section, dict):
                continue
            base_url = section.get("base_url", "")
            if base_url and base_url not in seen_urls:
                seen_urls.add(base_url)
                model = section.get("model", "")
                result.append({
                    "name": f"{section_key}_config",
                    "label": f"{section_key} 配置",
                    "icon": "⚙️",
                    "baseUrl": base_url,
                    "text": model,
                    "image": model if section_key == "image_gen" else "",
                    "video": model if section_key == "video_gen" else "",
                    "keyEnv": "",
                })
    except Exception:
        pass

    # 3. 内置预设 fallback
    for p in _BUILTIN_PRESETS:
        if p["baseUrl"] not in seen_urls:
            seen_urls.add(p["baseUrl"])
            result.append(dict(p))

    # 总是添加「自定义」选项
    result.append({
        "name": "custom",
        "label": "自定义",
        "icon": "🔧",
        "baseUrl": "",
        "text": "",
        "image": "",
        "video": "",
        "keyEnv": "",
    })

    return {"providers": result}


class StudioProviderRequest(BaseModel):
    """新增/更新 provider 配置。"""
    name: str
    label: str = ""
    icon: str = "🔧"
    baseUrl: str
    text: str = ""
    image: str = ""
    video: str = ""
    apiKey: str = ""  # 明文 key，写入 .env 而非 config.yaml


@router.post("/providers")
def save_provider(req: StudioProviderRequest):
    """新增或更新 provider 配置，写入 config.yaml。

    - provider 配置写入 config.yaml 的 studio.providers 列表
    - apiKey 写入 .env 文件（不暴露在 config.yaml 中）
    - 同名 provider 自动覆盖
    """
    try:
        from vermes_cli.config import load_config, save_config
        from vermes_cli.config import get_env_path, ensure_hermes_home

        cfg = load_config() or {}

        # 初始化 studio 段
        if "studio" not in cfg or not isinstance(cfg.get("studio"), dict):
            cfg["studio"] = {}
        if not isinstance(cfg["studio"].get("providers"), list):
            cfg["studio"]["providers"] = []

        providers = cfg["studio"]["providers"]

        # 同名覆盖
        existing_idx = None
        for i, p in enumerate(providers):
            if p.get("name") == req.name:
                existing_idx = i
                break

        provider_entry = {
            "name": req.name,
            "label": req.label or req.name,
            "icon": req.icon or "🔧",
            "baseUrl": req.baseUrl,
            "text": req.text,
            "image": req.image,
            "video": req.video,
        }

        # apiKey 写入 .env，config.yaml 只存引用
        if req.apiKey:
            env_key = f"STUDIO_{req.name.upper()}_API_KEY"
            provider_entry["keyEnv"] = env_key
            # 写入 .env
            ensure_hermes_home()
            env_path = get_env_path()
            _upsert_env_var(env_path, env_key, req.apiKey)

        if existing_idx is not None:
            providers[existing_idx] = provider_entry
        else:
            providers.append(provider_entry)

        cfg["studio"]["providers"] = providers
        save_config(cfg)

        logger.info("[Studio] Provider '%s' saved to config.yaml", req.name)
        return {"success": True, "provider": provider_entry}

    except Exception as exc:
        logger.error("[Studio] save_provider error: %s", exc)
        return {"success": False, "error": str(exc)}


@router.delete("/providers/{provider_name}")
def delete_provider(provider_name: str):
    """从 config.yaml 中删除指定 provider。"""
    try:
        from vermes_cli.config import load_config, save_config

        cfg = load_config() or {}
        studio = cfg.get("studio")
        if not isinstance(studio, dict):
            return {"success": False, "error": "studio 配置不存在"}

        providers = studio.get("providers")
        if not isinstance(providers, list):
            return {"success": False, "error": "providers 列表不存在"}

        original_len = len(providers)
        cfg["studio"]["providers"] = [
            p for p in providers if p.get("name") != provider_name
        ]

        if len(cfg["studio"]["providers"]) == original_len:
            return {"success": False, "error": f"未找到 provider '{provider_name}'"}

        save_config(cfg)
        logger.info("[Studio] Provider '%s' deleted", provider_name)
        return {"success": True, "deleted": provider_name}

    except Exception as exc:
        logger.error("[Studio] delete_provider error: %s", exc)
        return {"success": False, "error": str(exc)}


def _upsert_env_var(env_path: Path, key: str, value: str):
    """在 .env 文件中新增或更新一个环境变量。"""
    from pathlib import Path as P

    path = P(env_path)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{key}={value}\n", encoding="utf-8")
        return

    lines = path.read_text(encoding="utf-8").splitlines()
    found = False
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(f"{key}=") or stripped.startswith(f"export {key}="):
            new_lines.append(f"{key}={value}")
            found = True
        else:
            new_lines.append(line)

    if not found:
        new_lines.append(f"{key}={value}")

    path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


class StudioModelsRequest(BaseModel):
    base_url: str
    api_key: str


@router.post("/models")
def list_models(req: StudioModelsRequest):
    """调用厂商 /v1/models 端点拉取实时模型列表。

    让用户看到厂商当前可用的所有模型，不再依赖硬编码模型名。
    厂商更新模型后自动可见。
    """
    if not req.base_url or not req.api_key:
        return {"success": False, "models": [], "error": "base_url 和 api_key 不能为空"}

    root = req.base_url.rstrip("/")
    # 确保 /v1 前缀
    if not root.endswith("/v1"):
        root = root.rstrip("/") + ("" if root.endswith("/v1") else "/v1")

    try:
        resp = httpx.get(
            f"{root}/models",
            headers={"Authorization": f"Bearer {req.api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        # OpenAI 格式: {data: [{id: "model-name", ...}, ...]}
        raw_models = data.get("data", [])
        if not isinstance(raw_models, list):
            raw_models = data.get("models", [])
        if not isinstance(raw_models, list):
            raw_models = []

        models = []
        for m in raw_models:
            if isinstance(m, dict):
                mid = m.get("id") or m.get("name", "")
                if mid:
                    models.append({
                        "id": mid,
                        "display": m.get("display_name", mid),
                        "owned_by": m.get("owned_by", ""),
                    })
            elif isinstance(m, str):
                models.append({"id": m, "display": m, "owned_by": ""})

        # 按字母排序
        models.sort(key=lambda x: x["id"])

        return {"success": True, "models": models, "count": len(models)}

    except httpx.HTTPStatusError as exc:
        err_msg = ""
        try:
            err_body = exc.response.json()
            err_msg = err_body.get("error", {}).get("message", "") or str(err_body)
        except Exception:
            err_msg = exc.response.text[:300]
        return {"success": False, "models": [], "error": f"HTTP {exc.response.status_code}: {err_msg}"}
    except Exception as exc:
        return {"success": False, "models": [], "error": str(exc)}


# ═══════════════════════════════════════════════════════
#  生成 / 状态查询
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
