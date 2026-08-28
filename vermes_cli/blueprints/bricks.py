"""Blueprint: Bricks（P1-2 四态合一注册表 API）

把 ``vermes_cli/capabilities/registry.py`` 的 BrickRegistry 接到 HTTP，供前端
BrickCard / ``/bricks`` 页面（P1-3）渲染统一列表；安装/卸载**委派回各源专属
安装器**（module_catalog / skills_hub / adapters.recommend），不重写安装逻辑。

端点（注册顺序有讲究：静态子路径必须先于 ``/{brick_id}`` 注册，否则被通配吃掉）：
- GET  /api/v1/bricks                       — 列表（?type=&domain=&capability=&installed_only=&q=&refresh=）
- GET  /api/v1/bricks/capabilities          — 统一能力索引（brick caps + P0 模型 caps）
- POST /api/v1/bricks/custom                — 登记自定义 brick（overlay writer，CLI 复用同一入口）
- POST /api/v1/bricks/refresh               — 强制重新发现（清缓存）
- GET  /api/v1/bricks/{brick_id}            — 单条详情
- POST /api/v1/bricks/{brick_id}/install    — 按 type 委派安装 + 装后 probe
- POST /api/v1/bricks/{brick_id}/uninstall  — 按 type 委派卸载

安全：所有端点走 ``_check_origin``（与 modules_market.py / artifacts.py 同款纵深防御，
项目惯例是按 blueprint 各自持有一份，故此处不跨模块引私有函数）。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field

from vermes_cli.capabilities.registry import (
    BRICK_TYPES, BrickEntry, get_brick_registry,
)

_log = logging.getLogger(__name__)


def _check_origin(request: Request) -> None:
    """纵深防御：校验请求来源是否为本应用（挡掉跨站调用）。"""
    origin = request.headers.get("origin", "")
    host = request.headers.get("host", "")
    if origin and host:
        parsed = urlparse(origin)
        if parsed.hostname and parsed.hostname not in ("localhost", "127.0.0.1", "0.0.0.0"):
            raise HTTPException(status_code=403, detail="跨站请求被拒绝")


def _split_id(brick_id: str):
    """``"tool:browser_back"`` → ``("tool", "browser_back")``。"""
    if ":" in brick_id:
        t, _, name = brick_id.partition(":")
        return t, name
    return "", brick_id


def _find(reg, brick_id: str) -> Optional[BrickEntry]:
    return next((e for e in reg.discover() if e.id == brick_id), None)


# ---------------------------------------------------------------------------
# GET /api/v1/bricks — 列表 + 过滤
# ---------------------------------------------------------------------------
async def list_bricks(
    request: Request,
    type: Optional[str] = None,
    domain: Optional[str] = None,
    capability: Optional[str] = None,
    installed_only: bool = False,
    q: Optional[str] = None,
    refresh: bool = False,
):
    _check_origin(request)
    reg = get_brick_registry()
    entries = reg.discover(refresh=refresh)

    out: List[Dict[str, Any]] = []
    for e in entries:
        if type and e.type != type:
            continue
        if domain and (e.domain or "") != domain:
            continue
        if capability and capability not in (e.capabilities or []):
            continue
        if installed_only and e.install_state != "installed":
            continue
        if q:
            ql = q.lower()
            hay = f"{e.id} {e.name} {e.description}".lower()
            if ql not in hay:
                continue
        out.append(e.to_dict())

    return {"ok": True, "total": len(out), "bricks": out}


# ---------------------------------------------------------------------------
# GET /api/v1/bricks/capabilities — 统一能力索引（brick + P0 模型轴）
# ---------------------------------------------------------------------------
async def bricks_capabilities(request: Request, refresh: bool = False):
    _check_origin(request)
    reg = get_brick_registry()
    idx = reg.capability_index(refresh=refresh)
    idx["ok"] = True
    return idx


# ---------------------------------------------------------------------------
# GET /api/v1/bricks/{brick_id}
# ---------------------------------------------------------------------------
async def get_brick(request: Request, brick_id: str):
    _check_origin(request)
    entry = _find(get_brick_registry(), brick_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"brick 不存在: {brick_id}")
    return {"ok": True, "brick": entry.to_dict()}


# ---------------------------------------------------------------------------
# 安装 / 卸载：按 type 委派（不重写安装逻辑）
# ---------------------------------------------------------------------------
def _delegate_install(entry: BrickEntry) -> Dict[str, Any]:
    _, raw = _split_id(entry.id)

    if entry.type == "tool":
        return {"ok": True, "message": "tool 为进程内常驻，无需安装", "skipped": True}

    if entry.type == "module":
        from agent.module_catalog import install_module_code   # 已含 sha256 + safe_extract
        path = install_module_code(raw)
        return {"ok": True, "message": f"模块已安装到 {path}", "path": str(path)}

    if entry.type == "skill":
        from vermes_cli.skills_hub import do_install
        do_install(raw, skip_confirm=True, invalidate_cache=True, name_override=raw)
        return {"ok": True, "message": f"技能已安装: {raw}"}

    if entry.type == "software":
        from vermes_cli.adapters.recommend import (
            CatalogIndex, CliAnythingHubSource, Recommendation,
            install as software_install,
        )
        idx = CatalogIndex()
        idx.add_source(CliAnythingHubSource())
        e = next((x for x in idx.all_entries() if x.software == raw), None)
        if e is None:
            return {"ok": False, "message": f"cli-hub 目录中无 {raw}（或 cli-hub 不可用）"}
        rec = Recommendation(
            software=e.software, domain=e.domain, reason="", matched_keywords=[],
            source=e.source, score=1.0, adapter_install=e.install_cmd,
            backend_hint=e.requires,
        )
        res = software_install(rec, re_scan=True)
        return {
            "ok": bool(res.adapter_installed),
            "message": res.adapter_message or (
                "adapter 已安装" if res.adapter_installed else "adapter 安装未完成"
            ),
            "adapter_installed": res.adapter_installed,
            "backend_ready": res.backend_ready,
            "backend_hint": res.backend_hint,
            "tools_registered": res.tools_registered,
        }

    return {"ok": False, "message": f"不支持安装的类型: {entry.type}"}


def _delegate_uninstall(entry: BrickEntry) -> Dict[str, Any]:
    _, raw = _split_id(entry.id)

    if entry.type == "tool":
        return {"ok": True, "message": "tool 为进程内常驻，无需卸载", "skipped": True}

    if entry.type == "module":
        from agent.module_loader import uninstall_module
        r = uninstall_module(raw)
        return {"ok": bool(r.get("ok", True)), "message": str(r)}

    if entry.type == "skill":
        from tools.skills_hub import uninstall_skill
        ok, msg = uninstall_skill(raw)
        return {"ok": bool(ok), "message": str(msg)}

    if entry.type == "software":
        # software 本体由用户系统管理（brew/apt 等），Vermes 不代管卸载。
        return {
            "ok": False,
            "message": f"software 卸载请执行 `cli-hub uninstall {raw}`；Vermes 不代管本体",
            "skipped": True,
        }

    return {"ok": False, "message": f"不支持卸载的类型: {entry.type}"}


async def install_brick(request: Request, brick_id: str):
    _check_origin(request)
    reg = get_brick_registry()
    entry = _find(reg, brick_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"brick 不存在: {brick_id}")

    try:
        res = _delegate_install(entry)
    except Exception as exc:  # noqa: BLE001 - 安装失败只透出诊断，不裸抛
        _log.warning("brick install failed: %s: %s", brick_id, exc)
        res = {"ok": False, "message": f"安装失败: {exc}"}

    # 装后 probe：清缓存重新发现，确认它真的进注册表 / 变 installed（P1-4 的「装完即用」）
    reg.invalidate_cache()
    after = _find(reg, brick_id)
    res["id"] = brick_id
    res["probe"] = {
        "in_registry": after is not None,
        "install_state": after.install_state if after else None,
        "provides_tools": len(after.provides_tools) if after else 0,
    }
    if res.get("ok"):
        # overlay 记录：解决 tool/software 重启清空（P1-1 overlay 持久化的落点）
        reg.set_install_state(brick_id, "installed")
    return res


async def uninstall_brick(request: Request, brick_id: str):
    _check_origin(request)
    reg = get_brick_registry()
    entry = _find(reg, brick_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"brick 不存在: {brick_id}")

    try:
        res = _delegate_uninstall(entry)
    except Exception as exc:  # noqa: BLE001
        _log.warning("brick uninstall failed: %s: %s", brick_id, exc)
        res = {"ok": False, "message": f"卸载失败: {exc}"}

    reg.invalidate_cache()
    res["id"] = brick_id
    if res.get("ok"):
        reg.set_install_state(brick_id, "available")
    return res


# ---------------------------------------------------------------------------
# POST /api/v1/bricks/custom — 自定义 brick（CLI `vermes bricks add` 复用同一 writer）
# ---------------------------------------------------------------------------
class CustomBrickRequest(BaseModel):
    id: str
    type: str = "tool"
    name: str = ""
    description: str = ""
    capabilities: List[str] = Field(default_factory=list)
    domain: Optional[str] = None
    install_state: str = "installed"
    source: str = "community"
    version: Optional[str] = None
    sha256: Optional[str] = None
    size_bytes: Optional[int] = None
    requires: List[str] = Field(default_factory=list)
    provides_tools: List[str] = Field(default_factory=list)
    entry_point: Optional[str] = None
    extra: Dict[str, Any] = Field(default_factory=dict)


async def add_custom_brick(request: Request, payload: CustomBrickRequest):
    _check_origin(request)
    if payload.type not in BRICK_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"type 必须是 {list(BRICK_TYPES)} 之一，收到 {payload.type!r}",
        )
    reg = get_brick_registry()
    if _find(reg, payload.id) is not None:
        raise HTTPException(status_code=409, detail=f"brick 已存在: {payload.id}")

    entry = BrickEntry(
        id=payload.id,
        type=payload.type,
        name=payload.name or payload.id,
        description=payload.description,
        capabilities=list(payload.capabilities),
        domain=payload.domain,
        install_state=payload.install_state,
        source=payload.source,
        version=payload.version,
        sha256=payload.sha256,
        size_bytes=payload.size_bytes,
        requires=list(payload.requires),
        provides_tools=list(payload.provides_tools),
        entry_point=payload.entry_point,
        extra=dict(payload.extra),
    )
    reg.add_custom_brick(entry)
    return {"ok": True, "id": entry.id, "brick": entry.to_dict()}


# ---------------------------------------------------------------------------
# POST /api/v1/bricks/refresh — 强制重新发现
# ---------------------------------------------------------------------------
async def refresh_bricks(request: Request):
    _check_origin(request)
    reg = get_brick_registry()
    reg.invalidate_cache()
    entries = reg.discover(refresh=True)
    return {"ok": True, "total": len(entries)}


# ---------------------------------------------------------------------------
# 注册
# ---------------------------------------------------------------------------
def register_to(app) -> None:
    # 顺序：静态子路径先注册，避免被 /{brick_id} 通配吃掉。
    app.add_api_route("/api/v1/bricks", list_bricks, methods=["GET"])
    app.add_api_route("/api/v1/bricks/capabilities", bricks_capabilities, methods=["GET"])
    app.add_api_route("/api/v1/bricks/custom", add_custom_brick, methods=["POST"])
    app.add_api_route("/api/v1/bricks/refresh", refresh_bricks, methods=["POST"])
    app.add_api_route("/api/v1/bricks/{brick_id}", get_brick, methods=["GET"])
    app.add_api_route("/api/v1/bricks/{brick_id}/install", install_brick, methods=["POST"])
    app.add_api_route("/api/v1/bricks/{brick_id}/uninstall", uninstall_brick, methods=["POST"])


blueprint = None  # no APIRouter; uses register_to(app) pattern
