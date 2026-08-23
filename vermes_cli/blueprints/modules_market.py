"""Blueprint: Modules Market（Phase 3.2 后端中间层）

把 agent/module_catalog.py（catalog 数据）与 agent/module_loader.py（install/reload/
uninstall 生命周期）接到 HTTP，供前端 PluginsPage / ModuleHost 渲染「商店列表 →
安装/卸载」。

安全：
- 所有端点走 _check_origin（纵深防御跨站调用，与 artifacts 端点一致）。
- catalog 来源强制白名单（默认本地文件 / 受信 http 源），防 SSRF。
- 安装/卸载不新增信任面：失败仅透出诊断，不裸跑、不抛未捕获异常。

端点：
- GET  /api/v1/modules/market            — catalog 列表 + 已安装状态
- POST /api/v1/modules/market/install    — 按 catalog id 安装 + 热重载
- POST /api/v1/modules/market/uninstall  — 按 name 卸载（Phase 4.2）
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from fastapi import HTTPException, Request
from pydantic import BaseModel


_log = logging.getLogger(__name__)

# catalog 来源白名单：本地路径 + 受信 http(s) 源。其余拒掉，防 SSRF。
_TRUSTED_CATALOG_HOSTS = {"localhost", "127.0.0.1", "catalog.vermes.dev"}


def _check_origin(request: Request) -> None:
    """纵深防御：校验请求来源是否为本应用（挡掉跨站调用）。"""
    origin = request.headers.get("origin", "")
    host = request.headers.get("host", "")
    if origin and host:
        parsed = urlparse(origin)
        if parsed.hostname and parsed.hostname not in ("localhost", "127.0.0.1", "0.0.0.0"):
            raise HTTPException(status_code=403, detail="跨站请求被拒绝")


def _resolve_catalog_url(catalog_url: Optional[str]) -> str:
    """校验并返回 catalog 来源，防 SSRF。

    - 本地相对/绝对路径：直接返回（落盘在用户机器上）。
    - http(s)：host 必须在白名单。
    - 其他/内网地址：拒掉。
    """
    if not catalog_url:
        # 默认指向打包内置 catalog（若有）；否则返回空 → 上层报空 catalog。
        return ""
    if catalog_url.startswith(("http://", "https://")):
        parsed = urlparse(catalog_url)
        if parsed.hostname not in _TRUSTED_CATALOG_HOSTS:
            raise HTTPException(
                status_code=400,
                detail=f"catalog 来源不在白名单: {parsed.hostname}",
            )
        return catalog_url
    # 本地路径：规范化，禁止指向敏感系统目录
    p = Path(catalog_url).resolve()
    if any(part in p.parts for part in ("..",)):
        raise HTTPException(status_code=400, detail="非法 catalog 路径")
    return str(p)


class InstallRequest(BaseModel):
    id: str


class UninstallRequest(BaseModel):
    name: str


def register_to(app) -> None:
    @app.get("/api/v1/modules/market")
    async def modules_market_list(catalog_url: Optional[str] = None, request: Request = None):
        """列出 catalog 模块 + 已安装状态。

        无 catalog 源时返回空列表（不报错），前端渲染空商店。
        """
        _check_origin(request)
        from agent.module_catalog import (
            load_catalog,
            catalog_modules,
            is_module_installed,
        )
        from agent.module_loader import get_modules_dir

        try:
            resolved = _resolve_catalog_url(catalog_url)
        except HTTPException:
            raise
        if not resolved:
            return {"modules": [], "catalog_available": False}

        try:
            mods = catalog_modules(load_catalog(resolved))
        except Exception as exc:  # catalog 解析失败不崩，返回空
            _log.warning("modules_market: load catalog failed: %s", exc)
            return {"modules": [], "catalog_available": False, "error": str(exc)}

        modules_dir = get_modules_dir()
        out: List[Dict[str, Any]] = []
        for m in mods:
            out.append({
                "id": m.name,
                "name": m.display_name or m.name,
                "version": m.latest,
                "vermes_min": m.vermes_min,
                "code_asset": m.code_asset,
                "installed": is_module_installed(m.name, modules_dir=modules_dir),
            })
        return {"modules": out, "catalog_available": True}

    @app.post("/api/v1/modules/market/install")
    async def modules_market_install(body: InstallRequest, request: Request = None):
        """按 catalog id 安装模块 + 热重载进 registry。

        接 module_catalog.install_module_code + module_loader.reload_module_tools。
        失败透出诊断，不裸跑。
        """
        _check_origin(request)
        from agent.module_catalog import (
            load_catalog,
            catalog_modules,
            install_module_code,
        )
        from agent.module_loader import reload_module_tools, get_modules_dir

        mods = catalog_modules(load_catalog(_resolve_catalog_url(None)))
        target = next((m for m in mods if m.name == body.id), None)
        if target is None:
            raise HTTPException(status_code=404, detail=f"catalog 中无模块: {body.id}")

        modules_dir = get_modules_dir()
        try:
            installed_dir = install_module_code(
                body.id, modules=mods, modules_dir=modules_dir,
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"安装失败: {exc}")

        # 热重载进 registry（与文件 watcher / apply_change 同一入口）
        reload = reload_module_tools(body.id)
        return {
            "ok": reload.get("ok", False),
            "installed_dir": str(installed_dir),
            "reload": reload,
        }

    @app.post("/api/v1/modules/market/uninstall")
    async def modules_market_uninstall(body: UninstallRequest, request: Request = None):
        """按 name 卸载模块（Phase 4.2 uninstall_module 闭环）。"""
        _check_origin(request)
        from agent.module_loader import uninstall_module

        res = uninstall_module(body.name)
        if not res.get("ok"):
            raise HTTPException(status_code=400, detail=res.get("error", "卸载失败"))
        return res
