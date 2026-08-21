"""L2 API 路由：/api/adapters/recommend + /api/adapters/install。

暴露 recommend() 和 install() 给前端 Agent 管理面板的「发现软件」子 tab。
遵循 register_to(app) 模式（与 skills_tools.py 一致）。
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


async def adapters_recommend(intent: str = "", limit: int = 12):
    """GET /api/adapters/recommend?intent=...&limit=12

    返回推荐的 CLI-Anything 适配器列表（差集：已装不推荐）。
    """
    from .recommend import CatalogIndex, CliAnythingHubSource, recommend, CATALOG_INDEX
    from .bootstrap import discover_l2_adapters

    # 确保索引至少加载一次
    if not CATALOG_INDEX._entries:
        try:
            CATALOG_INDEX.add_source(CliAnythingHubSource())
        except Exception as exc:  # noqa: BLE001
            logger.warning("adapters_recommend: catalog 加载失败: %s", exc)
            return JSONResponse({"recommendations": [], "error": f"catalog 加载失败: {exc}"}, status_code=500)

    # 获取已装适配器（差集用）
    try:
        installed_map = discover_l2_adapters()
        installed = set(installed_map.keys())
    except Exception:  # noqa: BLE001
        installed = set()

    intent_text = intent.strip() or "常用工具"
    recs = recommend(intent=intent_text, installed=installed, index=CATALOG_INDEX)

    result = []
    for r in recs[:limit]:
        result.append({
            "software": r.software,
            "domain": r.domain,
            "reason": r.reason,
            "matched_keywords": r.matched_keywords,
            "source": r.source,
            "score": r.score,
            "adapter_install": r.adapter_install,
            "backend_hint": r.backend_hint,
        })

    return {"recommendations": result, "total": len(recs), "installed": sorted(installed)}


async def adapters_install(software: str, adapter_install: str = "", backend_hint: str = ""):
    """POST /api/adapters/install

    两步安装适配器：① cli-hub install adapter ② 检查本体就绪 + 重扫注册。
    """
    from .recommend import Recommendation, install as do_install

    rec = Recommendation(
        software=software,
        domain="",
        reason="用户手动安装",
        matched_keywords=[],
        source="user-action",
        score=1.0,
        adapter_install=adapter_install or f"cli-hub install {software}",
        backend_hint=backend_hint,
    )

    try:
        result = do_install(rec)
        return {
            "ok": result.adapter_installed,
            "software": result.software,
            "adapter_installed": result.adapter_installed,
            "adapter_message": result.adapter_message,
            "backend_ready": result.backend_ready,
            "backend_hint": result.backend_hint,
            "tools_registered": result.tools_registered,
        }
    except Exception as exc:  # noqa: BLE001
        logger.error("adapters_install failed: %s", exc)
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


async def adapters_installed():
    """GET /api/adapters/installed

    返回已安装的 L2 适配器及其注册工具数。
    """
    from .bootstrap import discover_l2_adapters

    try:
        result = discover_l2_adapters()
        items = []
        for software, count in sorted(result.items()):
            items.append({"software": software, "tools_registered": count})
        return {"adapters": items}
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"adapters": [], "error": str(exc)}, status_code=500)


def register_to(app: FastAPI) -> None:
    """注册 /api/adapters/* 路由到 FastAPI app。"""
    app.add_api_route(
        "/api/adapters/recommend", adapters_recommend, methods=["GET"],
        name="adapters_recommend",
    )
    app.add_api_route(
        "/api/adapters/install", adapters_install, methods=["POST"],
        name="adapters_install",
    )
    app.add_api_route(
        "/api/adapters/installed", adapters_installed, methods=["GET"],
        name="adapters_installed",
    )
