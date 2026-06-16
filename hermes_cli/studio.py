#!/usr/bin/env python3
"""
Vermes Studio — 多模态 API 直通车

一条命令，文本/图片/视频全搞定。支持任意兼容 OpenAI API 格式的多模态模型。
无需 Agent，无需模板，填入 API Key 直接用。

Usage:
    vermes studio                          # 交互式引导
    vermes studio --provider deepseek --key sk-xxx "写一个短视频剧本"
    vermes studio image "一只猫在吃火锅" --provider agnes
    vermes studio video "日落海滩，海浪拍岸" --provider xiaomi

支持的 provider:
    agnes       Agnes AI (apihub.agnes-ai.com) — 文本/图片/视频 ✅
    deepseek    DeepSeek — 文本 ✅
    alibaba     阿里通义千问 — 文本/图片 ✅
    xiaomi      小米 MiMo — 文本/图片/视频 ✅
    openai      OpenAI — 文本/图片 ✅
    custom      自定义 — 填你自己的 base_url
"""

import os
import sys
import json
import time
import argparse
import httpx
from pathlib import Path
from datetime import datetime

# ── Provider 配置 ────────────────────────────────────────────────

PROVIDERS = {
    "agnes": {
        "name": "Agnes AI",
        "base_url": "https://apihub.agnes-ai.com/v1",
        "models": {
            "text": "agnes-2.0-flash",
            "image": "agnes-image-2.1-flash",
            "video": "agnes-video-v2.0",
        },
        "env_key": "AGNES_API_KEY",
    },
    "deepseek": {
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "models": {
            "text": "deepseek-chat",
        },
        "env_key": "DEEPSEEK_API_KEY",
    },
    "alibaba": {
        "name": "阿里通义千问",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models": {
            "text": "qwen-max",
            "image": "qwen-vl-max",
        },
        "env_key": "QWEN_API_KEY",
    },
    "xiaomi": {
        "name": "小米 MiMo",
        "base_url": "https://api.xiaomimimo.com/v1",
        "models": {
            "text": "mimo-v2.5-pro",
            "image": "mimo-v2.5-pro",
            "video": "mimo-v2.5-pro",
        },
        "env_key": "XIAOMI_API_KEY",
    },
    "openai": {
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "models": {
            "text": "gpt-4o",
            "image": "dall-e-3",
        },
        "env_key": "OPENAI_API_KEY",
    },
}

# ── API 调用 ────────────────────────────────────────────────────

def _get_client(provider: str, api_key: str = None, base_url: str = None) -> tuple:
    """返回 (httpx.Client, provider_config, api_key)"""
    prov = PROVIDERS.get(provider)
    if not provider:
        print(f"❌ 未知 provider: {provider}，可用: {', '.join(PROVIDERS.keys())}")
        sys.exit(1)

    if not api_key:
        api_key = os.environ.get(prov["env_key"], "")
    if not api_key and prov["env_key"] in os.environ:
        api_key = os.environ[prov["env_key"]]

    if not api_key:
        print(f"❌ 未找到 {provider} 的 API Key")
        print(f"   设置环境变量 {prov['env_key']} 或传入 --key")
        sys.exit(1)

    base = base_url or prov["base_url"]
    client = httpx.Client(
        base_url=base,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        timeout=180,
        verify=False,
    )
    return client, prov, api_key


def _call_text(client: httpx.Client, model: str, prompt: str, system: str = "") -> str:
    """文本生成"""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    resp = client.post("/chat/completions", json={
        "model": model,
        "messages": messages,
        "max_tokens": 4096,
        "temperature": 0.85,
    })
    if resp.status_code != 200:
        return f"[错误 {resp.status_code}] {resp.text[:300]}"
    return resp.json()["choices"][0]["message"]["content"].strip()


def _call_image(client: httpx.Client, model: str, prompt: str, size: str = "1024x1024") -> dict:
    """图片生成，返回 {url, local_path}"""
    # 尝试标准 OpenAI 格式 /images/generations
    resp = client.post("/images/generations", json={
        "model": model,
        "prompt": prompt,
        "size": size,
        "n": 1,
    })
    if resp.status_code == 200:
        data = resp.json()
        url = data["data"][0]["url"]
    else:
        # 某些 provider 用 chat/completions 多模态生成图片
        resp2 = client.post("/chat/completions", json={
            "model": model,
            "messages": [{"role": "user", "content": f"画一张图: {prompt}"}],
            "max_tokens": 4096,
        })
        if resp2.status_code != 200:
            return {"error": f"图片生成失败 ({resp.status_code}/{resp2.status_code})"}
        # 有些模型返回 markdown 图片 URL
        import re
        content = resp2.json()["choices"][0]["message"]["content"]
        urls = re.findall(r"https?://[^\s\)]+\.(?:jpg|jpeg|png|gif|webp)", content)
        if urls:
            url = urls[0]
        else:
            return {"text_output": content, "note": "模型返回了文本而非图片"}

    # 下载到本地
    out_dir = Path.home() / "Desktop" / "vermes-studio"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    uid = __import__("uuid").uuid4().hex[:8]
    local_path = out_dir / f"image_{ts}_{uid}.jpg"

    try:
        img_resp = httpx.get(url, timeout=30, verify=False)
        img_resp.raise_for_status()
        local_path.write_bytes(img_resp.content)
        return {"url": url, "local_path": str(local_path)}
    except Exception:
        return {"url": url, "local_path": "", "note": "图片 URL 有效但下载失败"}


def _call_video(client: httpx.Client, model: str, prompt: str, image_url: str = "") -> dict:
    """视频生成（文生视频/图生视频）"""
    payload = {
        "model": model,
        "prompt": prompt,
        "duration": 5,
    }
    if image_url:
        payload["images"] = [image_url]

    resp = client.post("/video/generations", json=payload)
    if resp.status_code != 200:
        return {"error": f"视频提交失败 ({resp.status_code}): {resp.text[:200]}"}

    data = resp.json()
    video_id = data.get("video_id") or data.get("id")
    if not video_id:
        return {"error": f"未返回 video_id: {data}"}

    print(f"  ⏳ 视频生成中 (ID: {video_id[:16]}...)")

    # 轮询
    max_attempts = 60  # 最多等 5 分钟
    for i in range(max_attempts):
        time.sleep(5)
        status_resp = client.get(f"/videos/{video_id}")
        if status_resp.status_code != 200:
            continue
        status_data = status_resp.json()
        s = status_data.get("status", "")
        progress = status_data.get("progress", 0)

        if s == "completed":
            video_url = status_data.get("remixed_from_video_id", "")
            if video_url:
                print(f"  ✅ 视频完成!")
                return {"video_url": video_url, "video_id": video_id}
        elif s in ("failed", "error"):
            return {"error": f"视频生成失败: {status_data}"}
        elif i % 6 == 0:
            # 每 30 秒打印一次进度
            print(f"  ⏳ 进度: {progress}% ({i*5}s)")

    return {"error": f"视频生成超时 ({max_attempts * 5}秒)"}


# ── 命令入口 ────────────────────────────────────────────────────

def cmd_studio(args):
    """Main entry point for `vermes studio`"""
    mode = args.mode
    prompt = args.prompt or ""  # image/video 用 prompt
    if hasattr(args, 'text'):
        prompt = prompt or args.text or ""  # text 子命令用 text
    provider = args.provider or "agnes"
    api_key = args.key or ""
    base_url = args.base_url or ""
    model_override = args.model or ""
    size = getattr(args, 'size', "1024x1024")
    output = getattr(args, 'output', "")
    system = getattr(args, 'system', "")

    client, prov, _ = _get_client(provider, api_key, base_url)

    # 确定模型
    if not model_override:
        if mode == "text":
            model = prov["models"].get("text", "")
        elif mode == "image":
            model = prov["models"].get("image", prov["models"].get("text", ""))
        elif mode == "video":
            model = prov["models"].get("video", prov["models"].get("image", prov["models"].get("text", "")))
        else:
            model = prov["models"].get("text", "")
    else:
        model = model_override

    if not model:
        print(f"❌ {provider} 不支持 {mode} 模式")
        sys.exit(1)

    if not prompt:
        prompt = input("📝 输入提示词: ").strip()
        if not prompt:
            print("❌ 提示词不能为空")
            sys.exit(1)

    print(f"\n{'='*50}")
    print(f"  Provider: {prov['name']} ({provider})")
    print(f"  模型: {model}")
    print(f"  模式: {mode}")
    print(f"  提示词: {prompt[:80]}{'...' if len(prompt) > 80 else ''}")
    print(f"{'='*50}\n")

    if mode == "text":
        print(f"  📝 正在生成文本...")
        result = _call_text(client, model, prompt, system)
        print(f"\n{result}")

        if output:
            Path(output).write_text(result, encoding="utf-8")
            print(f"\n✅ 已保存到 {output}")

    elif mode == "image":
        print(f"  🎨 正在生成图片...")
        result = _call_image(client, model, prompt, size)
        if "error" in result:
            print(f"  ❌ {result['error']}")
        elif "text_output" in result:
            print(f"\n{result['text_output']}")
        else:
            print(f"  ✅ 图片已生成!")
            print(f"  本地: {result.get('local_path', '')}")
            print(f"  URL: {result.get('url', '')}")

    elif mode == "video":
        image_url = args.image_url or ""
        if not image_url and args.image_file:
            # 上传图片到临时
            print("  📤 上传图片...")
            image_url = args.image_file
        print(f"  🎬 正在生成视频{' (图生视频)' if image_url else ' (文生视频)'}...")
        result = _call_video(client, model, prompt, image_url)
        if "error" in result:
            print(f"  ❌ {result['error']}")
        else:
            print(f"  ✅ 视频已生成!")
            print(f"  URL: {result.get('video_url', '')}")
            print(f"  ID: {result.get('video_id', '')}")

    else:
        print(f"❌ 未知模式: {mode}，可用: text, image, video")
        sys.exit(1)


# ── CLI 解析 ────────────────────────────────────────────────────

def studio_parser(subparsers):
    """注册 vermes studio 子命令"""
    p = subparsers.add_parser("studio", help="多模态内容创作 — 文本/图片/视频直通 API")
    sub = p.add_subparsers(dest="mode", required=True)

    # text 子命令
    text_p = sub.add_parser("text", help="文本生成")
    text_p.add_argument("prompt", nargs="?", help="提示词")
    text_p.add_argument("--provider", "-p", help=f"Provider ({', '.join(PROVIDERS.keys())})")
    text_p.add_argument("--key", "-k", help="API Key")
    text_p.add_argument("--base-url", "-u", help="Base URL (custom provider)")
    text_p.add_argument("--model", "-m", help="模型名覆盖")
    text_p.add_argument("--system", "-s", help="系统提示词")
    text_p.add_argument("--output", "-o", help="保存到文件")

    # image 子命令
    img_p = sub.add_parser("image", help="图片生成")
    img_p.add_argument("prompt", nargs="?", help="图片描述")
    img_p.add_argument("--provider", "-p", help=f"Provider ({', '.join(PROVIDERS.keys())})")
    img_p.add_argument("--key", "-k", help="API Key")
    img_p.add_argument("--base-url", "-u", help="Base URL")
    img_p.add_argument("--model", "-m", help="模型名覆盖")
    img_p.add_argument("--size", "-s", default="1024x1024", help="图片尺寸")
    img_p.add_argument("--output", "-o", help="保存路径")

    # video 子命令
    vid_p = sub.add_parser("video", help="视频生成")
    vid_p.add_argument("prompt", nargs="?", help="视频描述")
    vid_p.add_argument("--provider", "-p", help=f"Provider ({', '.join(PROVIDERS.keys())})")
    vid_p.add_argument("--key", "-k", help="API Key")
    vid_p.add_argument("--base-url", "-u", help="Base URL")
    vid_p.add_argument("--model", "-m", help="模型名覆盖")
    vid_p.add_argument("--image-url", help="图生视频：输入图片 URL")
    vid_p.add_argument("--image-file", help="图生视频：本地图片路径")
    vid_p.add_argument("--output", "-o", help="保存路径")

    p.set_defaults(func=cmd_studio)


# 如果直接运行
if __name__ == "__main__":
    # 简易解析
    mode = sys.argv[1] if len(sys.argv) > 1 else "text"
    prompt = sys.argv[2] if len(sys.argv) > 2 else ""
    provider = "agnes"
    key = ""
    for i, arg in enumerate(sys.argv):
        if arg == "--provider" and i+1 < len(sys.argv):
            provider = sys.argv[i+1]
        if arg == "--key" and i+1 < len(sys.argv):
            key = sys.argv[i+1]

    client, prov, _ = _get_client(provider, key)
    model = prov["models"].get(mode, prov["models"].get("text", ""))

    if mode == "image":
        result = _call_image(client, model, prompt)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif mode == "video":
        result = _call_video(client, model, prompt)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        result = _call_text(client, model, prompt)
        print(result)
