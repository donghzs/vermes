"""Blueprint: E2 浏览器验证闭环（生成网页 → 浏览器截图验证 → 回报）

设计原则（用户 2026-09-11 拍板）：
- **opt-in 手动触发**：仅当用户在 ArtifactPanel 点「🔍 验证」按钮时触发，
  不强制、不自动跑，避免成为累赘、也不替用户做审美判断。
- **范围 = 技术冒烟层**：白页 / 布局崩 / 缺样式 / 控制台报错 / 关键元素缺失，
  明确不评判颜色、美观、主观质量（诚实标注，不越权）。
- **环境降级**：无 Chrome → 友好中文提示；无视觉模型 → 友好提示；不阻断其他功能。
- **async 安全**：浏览器工具内部用 asyncio.run，须在独立线程跑，避免嵌套事件环。

安全：验证页以随机 token 存内存（uuid4，不可猜），verify-temp 仅按 token 回放，
无路径穿越；截图路由 basename 限定在 screenshots 目录内。
"""

import asyncio
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Dict, Optional, Tuple

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from vermes_constants import get_vermes_dir

# ── 验证临时缓存（token -> (html, expire_ts)）──
_VERIFY_TTL = 600  # 10 分钟
_verify_cache: Dict[str, Tuple[str, float]] = {}
_verify_lock = threading.Lock()

_VERIFY_PROMPT = (
    "You are doing a TECHNICAL smoke-test of a generated web page (NOT an aesthetic review). "
    "Look at the screenshot and report ONLY technical rendering problems:\n"
    "1. Is the page blank or nearly empty (white screen / no visible content)?\n"
    "2. Are there obvious layout breaks: overlapping elements, large empty gaps, "
    "unstyled raw text, broken/missing images, content overflowing the viewport?\n"
    "3. Do key interactive elements (buttons, links, inputs, forms) appear present and clickable?\n"
    "4. Is there any visible raw HTML/code, console-error-like text, or 'undefined'/'NaN' showing?\n"
    "Do NOT judge color schemes, beauty, or subjective quality.\n"
    "End your answer with exactly one line: "
    "VERDICT: PASS (no significant technical issues), "
    "VERDICT: FAIL (blocking technical issues found), or "
    "VERDICT: WARN (minor issues only)."
)


def _evict_expired() -> None:
    now = time.time()
    expired = [k for k, (_h, exp) in _verify_cache.items() if exp < now]
    for k in expired:
        _verify_cache.pop(k, None)


def _store_html(html: str) -> str:
    token = uuid.uuid4().hex
    with _verify_lock:
        _evict_expired()
        _verify_cache[token] = (html, time.time() + _VERIFY_TTL)
    return token


def _get_html(token: str) -> Optional[str]:
    with _verify_lock:
        item = _verify_cache.get(token)
        if not item:
            return None
        html, exp = item
        if exp < time.time():
            _verify_cache.pop(token, None)
            return None
        return html


def _friendly_error(err: str) -> str:
    """把浏览器工具的错误翻译成用户友好的中文降级提示。"""
    e = (err or "").lower()
    if "chrom" in e or "agent-browser install" in e or "chromium" in e or ("missing" in e and "install" in e):
        return (
            "未检测到 Chrome / Chromium，浏览器验证暂不可用。请先安装 Chrome 后重试：\n"
            "· macOS：`brew install --cask google-chrome` 或运行 `agent-browser install`\n"
            "· Windows：下载并安装 Google Chrome\n"
            "安装完成后重新点击「验证」即可。"
        )
    if "vision" in e or "视觉" in e or "视觉模型" in e:
        return (
            "当前没有可用的视觉模型，无法对截图做分析。\n"
            "请在「设置」中配置一个支持视觉的模型，或等待内置视觉能力可用后重试。"
        )
    if "blocked" in e or "ssrf" in e or "metadata" in e:
        return "验证页地址被安全策略拦截，无法打开（请向开发者反馈）。"
    return (err or "验证失败")[:500]


def _parse_verdict(analysis: str) -> str:
    """从 vision 分析里提取 PASS/FAIL/WARN 结论。"""
    a = (analysis or "").upper()
    if "VERDICT: FAIL" in a or re.search(r"\bFAIL\b", a):
        return "fail"
    if "VERDICT: WARN" in a:
        return "warn"
    if "VERDICT: PASS" in a or re.search(r"\bPASS\b", a):
        return "pass"
    # 兜底启发式（prompt 未严格遵守时）
    if re.search(r"失败|破损|空白|崩溃|未渲染|缺失|报错|错乱", analysis or ""):
        return "fail"
    if re.search(r"正常|通过|良好|无明显|渲染正常", analysis or ""):
        return "pass"
    return "warn"


def _run_verify(preview_url: str) -> dict:
    """同步执行浏览器验证（在线程池中运行，规避 asyncio 嵌套事件环）。"""
    try:
        from tools.browser_tool import browser_navigate, browser_vision
    except Exception as e:  # 浏览器工具本身不可用时
        return {"ok": False, "stage": "import", "error": f"浏览器工具不可用：{e}"}

    # 1) 打开验证页
    try:
        nav_raw = browser_navigate(preview_url, task_id="e2_verify")
        nav = json.loads(nav_raw) if isinstance(nav_raw, str) else nav_raw
    except Exception as e:
        return {"ok": False, "stage": "navigate", "error": f"打开验证页失败：{e}"}
    if not nav.get("success"):
        return {"ok": False, "stage": "navigate", "error": _friendly_error(nav.get("error"))}

    # 2) 截图 + 视觉分析（技术冒烟）
    try:
        vis_raw = browser_vision(_VERIFY_PROMPT, annotate=False, task_id="e2_verify")
        vis = json.loads(vis_raw) if isinstance(vis_raw, str) else vis_raw
    except Exception as e:
        return {"ok": False, "stage": "vision", "error": f"截图分析失败：{e}"}
    if not vis.get("success"):
        sp = vis.get("screenshot_path")
        return {
            "ok": False,
            "stage": "vision",
            "error": _friendly_error(vis.get("error")),
            "screenshot": os.path.basename(sp) if sp else None,
        }

    analysis = vis.get("analysis", "") or ""
    sp = vis.get("screenshot_path")
    return {
        "ok": True,
        "verdict": _parse_verdict(analysis),
        "analysis": analysis,
        "screenshot": os.path.basename(sp) if sp else None,
        "preview_url": preview_url,
    }


def register_to(app):
    @app.post("/api/v1/verify-html")
    async def verify_html(request: Request):
        """接收 html 内容，生成预览 URL，在线程池里跑浏览器验证，返回结构化回报。"""
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "error": "请求体无效"}, status_code=400)
        html = (body.get("html") or "") if isinstance(body, dict) else ""
        if not html.strip():
            return JSONResponse({"ok": False, "error": "HTML 内容为空"}, status_code=400)
        if len(html) > 8 * 1024 * 1024:
            return JSONResponse({"ok": False, "error": "HTML 过大（>8MB），无法验证"}, status_code=413)

        token = _store_html(html)
        base = str(request.base_url).rstrip("/")
        preview_url = f"{base}/api/v1/verify-temp/{token}"

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
        result = await loop.run_in_executor(None, _run_verify, preview_url)
        return JSONResponse(result)

    @app.get("/api/v1/verify-temp/{token}")
    async def verify_temp(token: str):
        """回放验证页 HTML（随机 token，CSP 放宽以让页面完整渲染供截图）。"""
        html = _get_html(token)
        if html is None:
            return HTMLResponse(
                "<h1>验证页已过期</h1><p>请回到 ArtifactPanel 重新点击「🔍 验证」。</p>",
                status_code=404,
            )
        return HTMLResponse(
            html,
            headers={
                # 验证页需完整渲染（脚本/样式/图片）才能被截图，故 CSP 放宽为自包含沙箱。
                "Content-Security-Policy": (
                    "default-src 'self' 'unsafe-inline' 'unsafe-eval' data: blob:; "
                    "img-src * data: blob:; media-src * data: blob:; "
                    "font-src * data:; style-src * 'unsafe-inline'; "
                    "script-src * 'unsafe-inline' 'unsafe-eval'"
                ),
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get("/api/v1/verify-screenshot/{name}")
    async def verify_screenshot(name: str):
        """校验截图（basename 限定在 screenshots 目录内，防穿越）。"""
        name = os.path.basename(name)
        if not name or ".." in name or "/" in name or "\\" in name:
            return JSONResponse({"error": "非法文件名"}, status_code=400)
        shots_dir = get_vermes_dir("cache/screenshots", "browser_screenshots")
        path = Path(shots_dir) / name
        if not path.exists() or not path.is_file():
            return JSONResponse({"error": "截图不存在"}, status_code=404)
        try:
            data = path.read_bytes()
        except Exception as e:
            return JSONResponse({"error": f"读取截图失败：{e}"}, status_code=500)
        return Response(
            data,
            media_type="image/png",
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )
