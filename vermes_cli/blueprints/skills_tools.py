"""Blueprint: Skills & Tools（技能与工具集管理）

Vermes skills and toolsets endpoints.
"""

import json
import logging
from pathlib import Path
from typing import List, Optional

from fastapi import HTTPException, Request
from pydantic import BaseModel

_log = logging.getLogger(__name__)


# ── models ─────────────────────────────────────────────────────

class SkillToggle(BaseModel):
    name: str
    enabled: bool


class ToolsetToggle(BaseModel):
    enabled: bool


class SkillInstallRequest(BaseModel):
    identifier: str
    name: Optional[str] = None
    source: Optional[str] = None
    force: bool = False


class UsageRecord(BaseModel):
    kind: str                       # "expert" | "skill"
    id: str                         # stable capability id
    title: Optional[str] = None    # human label (for readability)
    scope: Optional[str] = ""      # user/session scope ("" = global)


# ── route handlers ─────────────────────────────────────────────

async def get_skills():
    from tools.skills_tool import _find_all_skills
    from vermes_cli.skills_config import get_disabled_skills, is_recommended_skill
    from vermes_cli.config import load_config

    config = load_config()
    disabled = get_disabled_skills(config)
    skills = _find_all_skills(skip_disabled=True)
    for s in skills:
        s["enabled"] = s["name"] not in disabled
        s["recommended"] = is_recommended_skill(s["name"])
    return skills


async def get_recommended():
    """开箱即用推荐技能（白名单内、且当前已安装/启用）。供引导页展示。"""
    from tools.skills_tool import _find_all_skills
    from vermes_cli.skills_config import (
        get_disabled_skills,
        get_recommended_skills,
        is_recommended_skill,
    )
    from vermes_cli.config import load_config

    config = load_config()
    disabled = get_disabled_skills(config)
    installed = {s["name"]: s for s in _find_all_skills(skip_disabled=True)}
    out = []
    for name in get_recommended_skills():
        if name in installed:
            s = dict(installed[name])
            s["enabled"] = name not in disabled
            s["recommended"] = True
            out.append(s)
    return out


async def toggle_skill(body: SkillToggle):
    from vermes_cli.skills_config import get_disabled_skills, save_disabled_skills
    from vermes_cli.config import load_config

    config = load_config()
    disabled = get_disabled_skills(config)
    if body.enabled:
        disabled.discard(body.name)
    else:
        disabled.add(body.name)
    save_disabled_skills(config, disabled)
    return {"ok": True, "name": body.name, "enabled": body.enabled}


async def get_toolsets():
    from vermes_cli.tools_config import (
        _get_effective_configurable_toolsets,
        get_effective_web_toolset_keys,
        _toolset_has_keys,
    )
    from toolsets import resolve_toolset
    from vermes_cli.config import load_config

    config = load_config()
    # Reflect what the web/desktop agent actually runs (platform_toolsets.web),
    # not the CLI config — the app is the web platform.
    enabled_toolsets = get_effective_web_toolset_keys(config)
    result = []
    for name, label, desc in _get_effective_configurable_toolsets():
        try:
            tools = sorted(set(resolve_toolset(name)))
        except Exception:
            tools = []
        is_enabled = name in enabled_toolsets
        result.append({
            "name": name, "label": label, "description": desc,
            "enabled": is_enabled,
            "available": is_enabled,
            "configured": _toolset_has_keys(name, config),
            "tools": tools,
        })
    return result


async def toggle_toolset(name: str, body: ToolsetToggle):
    """Enable/disable a toolset for the web/desktop platform.

    Persists to ``platform_toolsets.web``. The web chat backend re-reads
    config per request, so the change takes effect on the next conversation
    without a restart.

    Crucially we toggle against the user's *explicit* ``.web`` list, not the
    governed resolver output: the resolver drops the ``Vermes-cli`` composite
    (a platform default carrying terminal/skills/vision) and re-expands it
    lazily. Saving the resolver's output would strip ``Vermes-cli`` and silently
    amputate the agent's core tools. Toggling the raw list preserves it.
    """
    from vermes_cli.tools_config import (
        get_effective_web_toolset_keys,
        _save_platform_tools,
        _get_effective_configurable_toolsets,
    )
    from vermes_cli.config import load_config
    from fastapi import HTTPException

    # Validate the toolset name *before* mutating config. Only real,
    # UI-toggleable toolsets (built-in + plugin) may be toggled — this stops
    # garbage keys (typos, injected names) from being persisted into
    # platform_toolsets.web, which previously would silently pollute config.
    valid_keys = {ts_key for ts_key, _, _ in _get_effective_configurable_toolsets()}
    if name not in valid_keys:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown or non-toggleable toolset: {name!r}",
        )

    config = load_config()
    raw_web = (config.get("platform_toolsets") or {}).get("web")
    if raw_web:
        base = set(raw_web)
    else:
        # No explicit .web yet (fresh install) — start from the governed
        # default set, then flip the requested toolset.
        base = set(get_effective_web_toolset_keys(config))
    if body.enabled:
        base.add(name)
    else:
        base.discard(name)
    _save_platform_tools(config, "web", base)
    return {"ok": True, "name": name, "enabled": body.enabled}


# ── market handlers ────────────────────────────────────────────

async def detect_migration_sources():
    """Detect which other-agent footprints exist on this machine."""
    home = Path.home()
    sources = []
    if (home / ".hermes").is_dir():
        sources.append("hermes")
    if (home / ".openclaw").is_dir() or (home / ".clawdbot").is_dir() or (home / ".moltbot").is_dir():
        sources.append("openclaw")
    return {"sources": sources}


async def migration_preview(source: str = "hermes"):
    """Preview migration from Hermes/OpenClaw without executing."""
    if source == "hermes":
        from vermes_cli.hermes_migrate import migrate_from_hermes
        result = migrate_from_hermes(dry_run=True)
        return {
            "summary": result.summary(),
            "items": [{"kind": it.kind, "source": it.source, "destination": it.destination, "status": it.status, "reason": it.reason} for it in result.items[:30]],
            "total": len(result.items),
            "dry_run": True,
        }
    elif source == "openclaw":
        from importlib.util import spec_from_file_location, module_from_spec
        import sys
        script_paths = [
            Path.home() / ".vermes" / "skills" / "migration" / "openclaw-migration" / "scripts" / "openclaw_to_vermes.py",
            Path(__file__).parent.parent.parent / "optional-skills" / "migration" / "openclaw-migration" / "scripts" / "openclaw_to_vermes.py",
        ]
        script = next((p for p in script_paths if p.exists()), None)
        if not script:
            return {"error": "OpenClaw migration script not found. Install via: vermes claw migrate --dry-run"}
        spec = spec_from_file_location("openclaw_to_vermes", str(script))
        mod = module_from_spec(spec)
        sys.modules["openclaw_to_vermes"] = mod
        spec.loader.exec_module(mod)
        # Migrator 需要必参 source_root/target_root/execute 等
        oc_home = Path.home() / ".openclaw"
        if not oc_home.exists():
            # 尝试其他目录名
            for alt in [".clawdbot", ".moltbot"]:
                alt_home = Path.home() / alt
                if alt_home.exists():
                    oc_home = alt_home
                    break
        if not oc_home.exists():
            return {"error": "OpenClaw directory not found (~/.openclaw / ~/.clawdbot / ~/.moltbot)"}
        vermes_home = Path.home() / ".vermes"
        migrator = mod.Migrator(
            source_root=oc_home,
            target_root=vermes_home,
            execute=False,
            workspace_target=None,
            overwrite=False,
            migrate_secrets=False,
            output_dir=None,
        )
        # 先执行 dry-run 迁移生成 items，再 build_report
        migrator.migrate()
        report = migrator.build_report()
        return {
            "summary": report.get("summary", {}),
            "items": report.get("items", [])[:30],
            "total": len(report.get("items", [])),
            "dry_run": True,
        }
    else:
        return {"error": f"Unknown source: {source}"}


async def migration_execute(source: str = "hermes"):
    """Execute migration from Hermes/OpenClaw."""
    if source == "hermes":
        from vermes_cli.hermes_migrate import migrate_from_hermes
        result = migrate_from_hermes(dry_run=False)
        return {
            "summary": result.summary(),
            "items": [{"kind": it.kind, "source": it.source, "destination": it.destination, "status": it.status, "reason": it.reason} for it in result.items[:30]],
            "total": len(result.items),
            "dry_run": False,
        }
    elif source == "openclaw":
        from importlib.util import spec_from_file_location, module_from_spec
        import sys
        script_paths = [
            Path.home() / ".vermes" / "skills" / "migration" / "openclaw-migration" / "scripts" / "openclaw_to_vermes.py",
            Path(__file__).parent.parent.parent / "optional-skills" / "migration" / "openclaw-migration" / "scripts" / "openclaw_to_vermes.py",
        ]
        script = next((p for p in script_paths if p.exists()), None)
        if not script:
            return {"error": "OpenClaw migration script not found"}
        spec = spec_from_file_location("openclaw_to_vermes", str(script))
        mod = module_from_spec(spec)
        sys.modules["openclaw_to_vermes"] = mod
        spec.loader.exec_module(mod)
        oc_home = Path.home() / ".openclaw"
        if not oc_home.exists():
            for alt in [".clawdbot", ".moltbot"]:
                alt_home = Path.home() / alt
                if alt_home.exists():
                    oc_home = alt_home
                    break
        if not oc_home.exists():
            return {"error": "OpenClaw directory not found"}
        vermes_home = Path.home() / ".vermes"
        migrator = mod.Migrator(
            source_root=oc_home,
            target_root=vermes_home,
            execute=True,
            workspace_target=None,
            overwrite=False,
            migrate_secrets=False,
            output_dir=None,
        )
        migrator.migrate()
        report = migrator.build_report()
        return {
            "summary": report.get("summary", {}),
            "items": report.get("items", [])[:30],
            "total": len(report.get("items", [])),
            "dry_run": False,
        }
    else:
        return {"error": f"Unknown source: {source}"}


async def get_experts():
    """Curated expert catalog (qclaw-style) merged with live skill status."""
    from tools.skills_tool import _find_all_skills
    from vermes_cli.skills_config import get_disabled_skills
    from vermes_cli.config import load_config

    catalog_path = Path(__file__).parent.parent / "experts_catalog.json"
    catalog = []
    catalog_error = None
    try:
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        _log.warning("Experts catalog missing — experts_catalog.json not bundled in build")
        catalog_error = "catalog_missing"
    except Exception as exc:
        _log.warning("Failed to load experts catalog: %s", exc)
        catalog_error = "load_failed"

    config = load_config()
    disabled = set(get_disabled_skills(config))
    try:
        installed_skills = _find_all_skills(skip_disabled=True)
    except Exception:
        installed_skills = []
    installed_names = {s.get("name") for s in installed_skills}

    out = []
    for e in catalog:
        skill_names = e.get("skills", []) or []
        skills_status = []
        for sn in skill_names:
            is_installed = sn in installed_names
            skills_status.append({
                "name": sn,
                "installed": is_installed,
                "enabled": is_installed and sn not in disabled,
            })
        ready = all(s["installed"] for s in skills_status) if skills_status else True
        out.append({**e, "skills_status": skills_status, "ready": ready})
    return {"experts": out, "error": catalog_error}


class _CaptureConsole:
    """Rich-console stand-in that records printed text (no TTY needed)."""

    def __init__(self):
        self.lines: List[str] = []

    def print(self, *args, **kwargs):
        self.lines.append(" ".join(str(a) for a in args))

    def status(self, *args, **kwargs):
        class _Ctx:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        return _Ctx()

    def rule(self, *args, **kwargs):
        pass


async def market_search(q: str = "", source: str = "all", limit: int = 24):
    """Search the skills hub (official / clawhub / github / lobehub / ...).

    unified_search is synchronous (ThreadPoolExecutor internally) but runs
    ~30s worst-case.  Must offload to thread to avoid blocking the event loop
    which would hang ALL concurrent API requests (积木市场卡死后端根因).
    """
    import asyncio

    def _sync_search():
        from tools.skills_hub import (
            GitHubAuth, create_source_router, unified_search, _skill_meta_to_dict,
        )
        try:
            auth = GitHubAuth()
            sources = create_source_router(auth)
            results = unified_search(
                q or "", sources,
                source_filter=source or "all",
                limit=max(1, min(int(limit), 100)),
            )
        except Exception as exc:
            _log.warning("Skills market search failed: %s", exc)
            return []
        return [_skill_meta_to_dict(r) for r in results]

    try:
        items = await asyncio.to_thread(_sync_search)
    except Exception as exc:
        _log.warning("Skills market search thread failed: %s", exc)
        return {"items": [], "total": 0, "error": str(exc)}
    return {"items": items, "total": len(items), "query": q, "source": source}


async def market_trending():
    """Get trending skill repositories from GitHub (sorted by stars)."""
    import asyncio

    def _sync_trending():
        from tools.skills_hub import search_trending_skills, _skill_meta_to_dict
        try:
            results = search_trending_skills(limit=20)
        except Exception as exc:
            _log.warning("Skills market trending failed: %s", exc)
            return []
        return [_skill_meta_to_dict(r) for r in results]

    try:
        items = await asyncio.to_thread(_sync_trending)
    except Exception as exc:
        _log.warning("Skills market trending thread failed: %s", exc)
        return {"items": [], "total": 0, "error": str(exc)}
    return {"items": items, "total": len(items), "trending": True}


async def github_trending(since: str = "daily", language: str = "", limit: int = 25):
    """GET /api/github/trending — GitHub 热门榜（日/周/月）。

    用 GitHub Search API 模拟 trending：按 created/pushed 时间窗口 + stars 排序。
    since: daily(24h) / weekly(7d) / monthly(30d)
    language: 可选语言过滤（python/javascript/go/rust/...）
    """
    import httpx
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    if since == "weekly":
        since_date = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    elif since == "monthly":
        since_date = (now - timedelta(days=30)).strftime("%Y-%m-%d")
    else:  # daily
        since_date = (now - timedelta(days=1)).strftime("%Y-%m-%d")

    q_parts = [f"stars:>50", f"pushed:>={since_date}"]
    if language:
        q_parts.append(f"language:{language}")
    q = " ".join(q_parts)

    try:
        resp = httpx.get(
            "https://api.github.com/search/repositories",
            params={"q": q, "sort": "stars", "order": "desc", "per_page": min(max(limit, 1), 50)},
            headers={"Accept": "application/vnd.github+json", "User-Agent": "Vermes/2.4"},
            timeout=15.0,
        )
        if resp.status_code != 200:
            return {"items": [], "total": 0, "since": since, "error": f"GitHub API {resp.status_code}"}
        data = resp.json()
    except Exception as exc:
        _log.warning("GitHub trending failed: %s", exc)
        return {"items": [], "total": 0, "since": since, "error": str(exc)}

    items = []
    for repo in data.get("items", []):
        items.append({
            "name": repo.get("name", ""),
            "full_name": repo.get("full_name", ""),
            "description": repo.get("description") or "",
            "stars": repo.get("stargazers_count", 0),
            "language": repo.get("language") or "",
            "url": repo.get("html_url", ""),
            "topics": repo.get("topics", []),
            "forks": repo.get("forks_count", 0),
            "open_issues": repo.get("open_issues_count", 0),
            "owner": repo.get("owner", {}).get("login", ""),
            "owner_avatar": repo.get("owner", {}).get("avatar_url", ""),
            "created_at": repo.get("created_at", ""),
            "pushed_at": repo.get("pushed_at", ""),
        })
    return {"items": items, "total": len(items), "since": since, "language": language}


async def tencent_opensource(q: str = "", limit: int = 25):
    """GET /api/trending/tencent — 腾讯开源热门项目。

    搜索 GitHub orgs: Tencent / TencentCloud / TarsCloud / Tencentyun 等
    按 stars 排序，支持关键词过滤。
    """
    import httpx

    orgs = ["Tencent", "TencentCloud", "TarsCloud", "TencentBlueKing"]
    # GitHub Search API 不支持 OR 连接多个 org: 限定符，逐个查询合并取 Top N
    per_org = max(limit // len(orgs) + 2, 5)
    all_items = []
    for org in orgs:
        q = f"org:{org}" + (f" {q}" if q else "")
        try:
            resp = httpx.get(
                "https://api.github.com/search/repositories",
                params={"q": q, "sort": "stars", "order": "desc", "per_page": min(per_org, 30)},
                headers={"Accept": "application/vnd.github+json", "User-Agent": "Vermes/2.4"},
                timeout=12.0,
            )
            if resp.status_code == 200:
                for repo in resp.json().get("items", []):
                    all_items.append({
                        "name": repo.get("name", ""),
                        "full_name": repo.get("full_name", ""),
                        "description": repo.get("description") or "",
                        "stars": repo.get("stargazers_count", 0),
                        "language": repo.get("language") or "",
                        "url": repo.get("html_url", ""),
                        "topics": repo.get("topics", []),
                        "forks": repo.get("forks_count", 0),
                        "owner": repo.get("owner", {}).get("login", ""),
                        "owner_avatar": repo.get("owner", {}).get("avatar_url", ""),
                        "created_at": repo.get("created_at", ""),
                        "pushed_at": repo.get("pushed_at", ""),
                    })
            else:
                _log.warning("Tencent opensource: org %s returned %s", org, resp.status_code)
        except Exception as exc:
            _log.warning("Tencent opensource: org %s failed: %s", org, exc)
            continue  # 单个 org 失败不中断

    # 去重 + 按 stars 排序 + 截取 limit
    seen = set()
    deduped = []
    for it in all_items:
        if it["full_name"] not in seen:
            seen.add(it["full_name"])
            deduped.append(it)
    deduped.sort(key=lambda x: x["stars"], reverse=True)
    items = deduped[:limit]
    return {"items": items, "total": len(items)}


async def market_install(body: SkillInstallRequest):
    """Install a skill non-interactively (quarantine + scan + install)."""
    from vermes_cli.skills_hub import do_install
    from tools.skills_hub import HubLockFile
    cap = _CaptureConsole()
    installed_name = (body.name or "").strip()
    try:
        do_install(
            body.identifier,
            category="",
            force=body.force,
            console=cap,
            skip_confirm=True,
            name_override=installed_name,
        )
    except Exception as exc:
        return {"ok": False, "name": installed_name, "message": str(exc)}
    lock = HubLockFile()
    resolved = installed_name or body.identifier.split("/")[-1]
    if lock.get_installed(resolved):
        return {"ok": True, "name": resolved, "message": "已安装"}
    return {"ok": False, "name": resolved, "message": "\n".join(cap.lines) or "安装失败"}


async def market_uninstall(name: str):
    """Uninstall a hub-installed skill."""
    from tools.skills_hub import uninstall_skill
    try:
        ok, msg = uninstall_skill(name)
        return {"ok": ok, "name": name, "message": msg}
    except Exception as exc:
        return {"ok": False, "name": name, "message": str(exc)}


async def skill_audit(name: str):
    """GET /api/skills/audit/{name} — 返回某技能的供应链安全审计历史。

    P0-3：供前端技能详情「安全审计」区消费。数据源是 ~/.vermes/.hub/audit.log
    （结构化 jsonl，含 scan verdict/findings 摘要/sha256）。Fail-open：无记录返回空列表。
    """
    from tools.skills_hub import get_audit_entries, HubLockFile
    try:
        entries = get_audit_entries(skill_name=name, limit=20)
        # 附带当前安装记录的 scan_verdict + skill_hash（来自 HubLockFile，最新快照）
        lock = HubLockFile()
        installed = lock.get_installed(name) or {}
        return {
            "name": name,
            "entries": entries,
            "installed": {
                "scan_verdict": installed.get("scan_verdict", ""),
                "skill_hash": installed.get("skill_hash", ""),
                "source": installed.get("source", ""),
                "trust_level": installed.get("trust_level", ""),
                "install_path": installed.get("install_path", ""),
            } if installed else None,
        }
    except Exception as exc:
        return {"name": name, "entries": [], "installed": None, "error": str(exc)}


# ── usage telemetry (越用越懂用户) ──────────────────────────────

async def record_usage(body: UsageRecord):
    """Record one capability-usage event into the unified memory base."""
    from agent.memory_fabric import record_usage as _record
    try:
        _record(body.kind, body.id, body.title or "", body.scope or "")
        return {"ok": True, "kind": body.kind, "id": body.id}
    except Exception as exc:
        _log.warning("record_usage failed: %s", exc)
        return {"ok": False, "kind": body.kind, "id": body.id, "message": str(exc)}


async def get_usage_recommend(kind: Optional[str] = None, limit: int = 4):
    """Top-used capabilities (experts/skills) for "你可能想用"."""
    from agent.memory_fabric import get_usage_counts
    try:
        items = get_usage_counts(kind, "", max(1, min(int(limit), 20)))
    except Exception as exc:
        _log.warning("usage recommend failed: %s", exc)
        items = []
    return {"items": items, "kind": kind, "limit": limit}


# ── registration ───────────────────────────────────────────────

class CreateSkillRequest(BaseModel):
    name: str
    content: str
    category: str = ""


async def create_skill_endpoint(body: CreateSkillRequest, request: Request = None):
    """POST /api/skills/create — 用户自定义技能创建。

    调 skill_manager_tool._create_skill 做安全扫描 + 写盘。
    """
    from tools.skill_manager_tool import _create_skill
    result = _create_skill(body.name, body.content, body.category or None)
    if result.get("success"):
        return {"ok": True, "message": result["message"], "path": result.get("path", "")}
    return {"ok": False, "message": result.get("error", "创建失败")}


def register_to(app):
    """Register skills & tools routes on the FastAPI app."""
    app.add_api_route(
        "/api/skills", get_skills, methods=["GET"], name="get_skills"
    )
    app.add_api_route(
        "/api/skills/recommended", get_recommended, methods=["GET"], name="get_recommended"
    )
    app.add_api_route(
        "/api/skills/toggle", toggle_skill, methods=["PUT"], name="toggle_skill"
    )
    app.add_api_route(
        "/api/tools/toolsets", get_toolsets, methods=["GET"], name="get_toolsets"
    )
    app.add_api_route(
        "/api/tools/toolsets/{name}", toggle_toolset, methods=["PUT"], name="toggle_toolset"
    )
    app.add_api_route(
        "/api/skills/market", market_search, methods=["GET"], name="market_search"
    )
    app.add_api_route(
        "/api/skills/market/trending", market_trending, methods=["GET"], name="market_trending"
    )
    app.add_api_route(
        "/api/github/trending", github_trending, methods=["GET"], name="github_trending"
    )
    app.add_api_route(
        "/api/trending/tencent", tencent_opensource, methods=["GET"], name="tencent_opensource"
    )
    app.add_api_route(
        "/api/experts", get_experts, methods=["GET"], name="get_experts"
    )
    app.add_api_route(
        "/api/migration/sources", detect_migration_sources, methods=["GET"], name="detect_migration_sources"
    )
    app.add_api_route(
        "/api/migration/preview", migration_preview, methods=["POST"], name="migration_preview"
    )
    app.add_api_route(
        "/api/migration/execute", migration_execute, methods=["POST"], name="migration_execute"
    )

    app.add_api_route(
        "/api/skills/install", market_install, methods=["POST"], name="market_install"
    )
    app.add_api_route(
        "/api/skills/{name}", market_uninstall, methods=["DELETE"], name="market_uninstall"
    )
    app.add_api_route(
        "/api/skills/audit/{name}", skill_audit, methods=["GET"], name="skill_audit"
    )
    app.add_api_route(
        "/api/usage", record_usage, methods=["POST"], name="record_usage"
    )
    app.add_api_route(
        "/api/usage/recommend", get_usage_recommend, methods=["GET"], name="get_usage_recommend"
    )
    app.add_api_route(
        "/api/skills/create", create_skill_endpoint, methods=["POST"], name="create_skill"
    )


blueprint = None  # no APIRouter; uses register_to(app) pattern
