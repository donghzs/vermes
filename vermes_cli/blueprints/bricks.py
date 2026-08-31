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
from vermes_cli.capabilities.brick_reviews import (
    BrickReviewError, get_review_store,
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
# J3 强调：标记 brick 为优先呈现（纯 overlay 元数据，低风险，不弹审批）
# ---------------------------------------------------------------------------
async def set_brick_emphasis(request: Request, brick_id: str):
    """POST /api/v1/bricks/{brick_id}/emphasis — 标记/取消「强调」。

    body: {"emphasized": true|false}（缺省 true）。复用 BrickRegistry.set_emphasis，
    走 _check_origin 纵深防御。强调是用户显式意图的元数据标记，不触发审批弹窗。
    """
    _check_origin(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    on = bool(body.get("emphasized", True))
    reg = get_brick_registry()
    reg.set_emphasis(brick_id, on)
    return {"ok": True, "id": brick_id, "emphasized": on}


# ---------------------------------------------------------------------------
# 安装 / 卸载：按 type 委派（不重写安装逻辑）
# ---------------------------------------------------------------------------
def _delegate_install(entry: BrickEntry) -> Dict[str, Any]:
    _, raw = _split_id(entry.id)

    if entry.type == "tool":
        return {"ok": True, "message": "tool 为进程内常驻，无需安装", "skipped": True}

    if entry.type == "module":
        from agent.module_catalog import install_module_code   # 已含 sha256 + safe_extract
        # P1-4：装完必须热重载，否则代码包落盘了但工具没进 registry —— 不算「装完即用」。
        # 与 modules_market.install 同一入口（install_module_code → reload_module_tools）。
        from agent.module_loader import reload_module_tools
        path = install_module_code(raw)
        reload = reload_module_tools(raw)
        return {
            "ok": bool(reload.get("ok", False)),
            "message": f"模块已安装到 {path}"
                       + ("，工具已热重载" if reload.get("ok") else "，但热重载未成功"),
            "path": str(path),
            "reload": reload,
        }

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


# ---------------------------------------------------------------------------
# P1-4 装后探测：确认「对话中真的可用」，而不只是「出现在注册表里」
# ---------------------------------------------------------------------------
def _probe_brick(entry: BrickEntry) -> Dict[str, Any]:
    """按类型做真实可用性探测。

    - tool：进程内常驻，恒可用（注册校验仅作信息展示）。
    - module：`provides_tools` 里有多少真的注册进 `tools/registry`。
    - skill：SKILL.md 是否出现在某个 skills 目录下。
    - software：adapter 是否注册了工具；本体未就绪时给 `backend_hint` 指引
      （两步安装的第二步不由 Vermes 代管，只能引导）。
    """
    _, raw = _split_id(entry.id)
    declared = list(entry.provides_tools or [])
    registered: List[str] = []
    backend_ready: Optional[bool] = None
    backend_hint = ""

    try:
        from tools.registry import registry as TOOL_REGISTRY

        if entry.type == "tool":
            registered = [raw] if TOOL_REGISTRY.get_entry(raw) else []

        elif entry.type == "module":
            # 代码包装完 + 热重载后，工具才会在 registry 里；逐个核对而非只看目录存在。
            registered = [t for t in declared if TOOL_REGISTRY.get_entry(t)]
            if not registered and declared:
                # 自愈：模块已安装但当前进程尚未加载（如刚启动还没走到 register_modules）
                # 时，直接观测会得到假阴性。主动热重载一次再判——与 tools/registry.py:478
                # 「工具未注册则懒重载」的安全网思路一致。
                try:
                    from agent.module_catalog import is_module_installed
                    from agent.module_loader import reload_module_tools
                    if is_module_installed(raw):
                        reload_module_tools(raw)
                        registered = [t for t in declared if TOOL_REGISTRY.get_entry(t)]
                        if not registered:
                            backend_hint = (
                                f"模块已安装，但工具未能在当前进程加载；"
                                f"请重启应用或执行 reload_module_tools('{raw}')"
                            )
                except Exception as exc:  # noqa: BLE001
                    _log.debug("module lazy reload during probe failed: %s", exc)

        elif entry.type == "skill":
            from agent.skill_utils import get_all_skills_dirs
            hit = any((d / raw / "SKILL.md").exists()
                      for d in get_all_skills_dirs() if d.is_dir())
            registered = [raw] if hit else []

        elif entry.type == "software":
            from vermes_cli.adapters.bootstrap import discover_l2_adapters
            n = (discover_l2_adapters() or {}).get(raw, 0)
            registered = [raw] if n > 0 else []
            backend_ready = n > 0
            if n <= 0:
                req = "、".join(entry.requires or [])
                backend_hint = (
                    f"adapter 已装，但本体未就绪：请先安装 {raw}"
                    + (f"（依赖：{req}）" if req else "")
                )
    except Exception as exc:  # noqa: BLE001 - 探测失败降级为「未确认可用」，不阻断
        _log.debug("probe failed for %s: %s", entry.id, exc)

    available = True if entry.type == "tool" else bool(registered)
    return {
        "in_registry": True,
        "install_state": entry.install_state,
        "provides_tools": len(declared),
        "tools_registered": len(registered),
        "tools_registered_names": registered[:10],
        "available": available,
        "backend_ready": backend_ready,
        "backend_hint": backend_hint,
    }


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

    # 装后 probe（P1-4）：清缓存重新发现，再按类型做真实可用性探测。
    reg.invalidate_cache()
    after = _find(reg, brick_id)
    res["id"] = brick_id
    res["probe"] = _probe_brick(after) if after is not None else {
        "in_registry": False,
        "install_state": None,
        "provides_tools": 0,
        "tools_registered": 0,
        "tools_registered_names": [],
        "available": False,
        "backend_ready": None,
        "backend_hint": "",
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
# P4-1 发砖审核：submit / review（状态机见 brick_reviews.py）
# ---------------------------------------------------------------------------
class SubmitBrickRequest(BaseModel):
    """开发者发砖提交时携带的元数据提案（对齐 P4-2 治理字段）。"""
    display_name: Optional[str] = None
    description: Optional[str] = None
    version: Optional[str] = None
    vermes_min: Optional[str] = None
    code_asset: Optional[str] = None
    code_sha256: Optional[str] = None
    dependencies: List[str] = Field(default_factory=list)
    keywords: List[str] = Field(default_factory=list)
    repository: Optional[str] = None
    homepage: Optional[str] = None
    submitted_by: Optional[str] = None


class ReviewBrickRequest(BaseModel):
    decision: str                      # approve | reject | start
    reviewer: Optional[str] = None
    note: str = ""


def _validate_submission_ci(payload: SubmitBrickRequest) -> List[str]:
    """提交时 CI 校验（auto_reject 触发源）。返回冲突列表；空=通过。

    复用 module_catalog.check_module_install_conflicts，对开发者的元数据提案
    跑依赖存在性 / vermes_min / 版本倒退三检；另加 sha256 格式校验。
    """
    from agent.module_catalog import (
        CatalogModule, catalog_modules, load_catalog,
        check_module_install_conflicts,
    )
    from vermes_cli import __version__ as _vermes_version

    conflicts: List[str] = []
    # sha256 格式（提供时必须 64 位 hex）
    if payload.code_sha256:
        import re
        if not re.fullmatch(r"[0-9a-fA-F]{64}", payload.code_sha256 or ""):
            conflicts.append("code_sha256 格式非法（须 64 位 hex）")
    # 依赖 / vermes_min / 版本：复用装前冲突检测（开发者元数据作为拟发布 CatalogModule）
    try:
        mods = catalog_modules(load_catalog(None))
        synthetic = CatalogModule(
            name="__submit_ci__",
            display_name=payload.display_name or "__submit_ci__",
            latest=payload.version or "0.0.0",
            vermes_min=payload.vermes_min or "0.0.0",
            dependencies=list(payload.dependencies or []),
        )
        conflicts.extend(
            check_module_install_conflicts(synthetic, mods, _vermes_version)
        )
    except Exception as exc:  # noqa: BLE001 - 校验异常不致命，交人工
        _log.warning("submit CI 校验跳过（catalog 不可用）: %s", exc)
    return conflicts


async def submit_brick(request: Request, brick_id: str, payload: SubmitBrickRequest):
    """开发者提交 brick 进入审核流（submitted）。

    CI 校验失败直接 auto_reject（不进人工队列）；否则落 submitted 状态。
    """
    _check_origin(request)
    store = get_review_store()
    meta = payload.model_dump(exclude={"submitted_by"}, exclude_none=True)
    try:
        rev = store.submit(brick_id, metadata=meta, submitted_by=payload.submitted_by)
    except BrickReviewError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    # CI 校验 → auto_reject
    ci_conflicts = _validate_submission_ci(payload)
    if ci_conflicts:
        store.auto_reject(brick_id, "；".join(ci_conflicts))
        rev = store.get(brick_id)
        return {
            "ok": True,
            "brick_id": brick_id,
            "status": rev.status,
            "auto_rejected": True,
            "ci_conflicts": ci_conflicts,
        }
    return {"ok": True, "brick_id": brick_id, "status": rev.status, "auto_rejected": False}


async def review_brick(request: Request, brick_id: str, payload: ReviewBrickRequest):
    """人工审核决策：start(→in_review) / approve / reject。"""
    _check_origin(request)
    if payload.decision not in ("approve", "reject", "start"):
        raise HTTPException(status_code=400, detail=f"非法决策: {payload.decision}")
    store = get_review_store()
    try:
        if payload.decision == "start":
            rev = store.begin_review(brick_id, reviewer=payload.reviewer)
        else:
            rev = store.review(
                brick_id, decision=payload.decision,
                reviewer=payload.reviewer, note=payload.note,
            )
    except BrickReviewError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"ok": True, "brick_id": brick_id, "status": rev.status, "review": rev.to_dict()}


async def get_review_status(request: Request, brick_id: str):
    """查询某 brick 的审核状态（前端待审核列表 / 详情用）。"""
    _check_origin(request)
    rev = get_review_store().get(brick_id)
    if rev is None:
        raise HTTPException(status_code=404, detail=f"无审核记录: {brick_id}")
    return {"brick_id": brick_id, "status": rev.status, "review": rev.to_dict()}


async def list_brick_reviews(request: Request, status: Optional[str] = None):
    """审核记录列表（前端「待审核」tab：?status=submitted）。"""
    _check_origin(request)
    items = get_review_store().list(status=status)
    return {
        "reviews": [r.to_dict() for r in items],
        "total": len(items),
        "status_filter": status,
    }


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
    # P4-1 发砖审核：reviews 是静态子路径，必须在 /{brick_id} 之前注册
    app.add_api_route("/api/v1/bricks/reviews", list_brick_reviews, methods=["GET"])
    app.add_api_route("/api/v1/bricks/{brick_id}", get_brick, methods=["GET"])
    app.add_api_route("/api/v1/bricks/{brick_id}/install", install_brick, methods=["POST"])
    app.add_api_route("/api/v1/bricks/{brick_id}/uninstall", uninstall_brick, methods=["POST"])
    app.add_api_route("/api/v1/bricks/{brick_id}/emphasis", set_brick_emphasis, methods=["POST"])
    # P4-1 发砖审核（/ {brick_id}/{action} 与 install/uninstall 同级，不冲突）
    app.add_api_route("/api/v1/bricks/{brick_id}/submit", submit_brick, methods=["POST"])
    app.add_api_route("/api/v1/bricks/{brick_id}/review", review_brick, methods=["POST"])
    app.add_api_route("/api/v1/bricks/{brick_id}/review", get_review_status, methods=["GET"])


blueprint = None  # no APIRouter; uses register_to(app) pattern
