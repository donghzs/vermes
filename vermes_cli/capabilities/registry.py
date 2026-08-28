"""P1 四态合一 BrickRegistry：聚合层 + overlay 持久化 + 查询层。

设计铁律（见 vermes_p1_registry_design.md §三）：registry 是**聚合层 + 持久化层 +
查询层，不是新的能力生产者**。四态各自的生产/发现机制（tools/registry 自注册、
modules_market 安装、skill_utils 发现、cli-hub 内省）全部保留复用，本模块只消费
它们的产出并归一为统一 `BrickEntry`。

四源接入点（真实代码，已核对行号，禁止凭印象改函数名）：
  - tool     : tools/registry.py  `registry`(ToolRegistry 单例) + `.get_all_tool_names()`(L877) + `.get_entry(name)`(L418)
  - module   : agent/module_catalog.py `get_catalog_modules()`(L300, 用 bundled_catalog_path L93) + `is_module_installed()`(L410)
  - skill    : agent/skill_utils.py `get_all_skills_dirs()`(L327) + `iter_skill_index_files`(L532) + `parse_frontmatter`(L88) + `extract_skill_description`(L518)
  - software : vermes_cli/adapters/bootstrap.py `discover_l2_adapters()`(L113, 已装适配器) + vermes_cli/adapters/recommend.py `CatalogIndex`/`CliAnythingHubSource`(cli-hub 目录，fail-open)
  - model    : vermes_cli/capabilities/manifest.py `generate_capability_manifest()`（P0 轴，provider 型条目）

fail-open 哲学（与 P0 一致）：任一源探测失败仅记 debug 日志并跳过，不阻断其他源。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("vermes.brick_registry")


# ---------------------------------------------------------------------------
# 统一数据模型
# ---------------------------------------------------------------------------

BRICK_TYPES = ("skill", "tool", "module", "software", "provider")


@dataclass
class BrickEntry:
    """四态归一后的单一 brick 记录。字段语义见设计文档 §二。"""

    id: str                              # 全局唯一，形如 "tool:xxx" / "module:mfgcad"
    type: str                            # skill | tool | module | software | provider
    name: str                           # 展示名
    description: str = ""
    capabilities: List[str] = field(default_factory=list)   # 归一化能力标签
    domain: Optional[str] = None
    install_state: str = "available"     # installed | available | not-installed
    source: str = "bundled"             # bundled | community | cli-anything-hub | official | github-release
    version: Optional[str] = None
    sha256: Optional[str] = None
    size_bytes: Optional[int] = None
    requires: List[str] = field(default_factory=list)
    provides_tools: List[str] = field(default_factory=list)
    entry_point: Optional[str] = None    # SKILL.md 路径 / module 目录 / cli 二进制
    installed_at: Optional[float] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# 持久化路径
# ---------------------------------------------------------------------------

def vermes_home() -> Path:
    return Path(os.environ.get("VERMES_HOME", os.path.expanduser("~/.vermes")))


class BrickRegistry:
    """四态合一注册表：聚合 + overlay 持久化 + 查询。"""

    def __init__(self, bricks_json: Optional[Path] = None):
        self._lock = threading.RLock()
        self.bricks_json = Path(bricks_json) if bricks_json else (vermes_home() / "bricks.json")
        self._overlay: Dict[str, Dict[str, Any]] = {}
        self._custom_bricks: List[BrickEntry] = []
        # discover() 快照缓存：扫 234 个 skill 目录 + discover_l2_adapters()（有 register
        # 副作用）开销不小，API 层可能频繁调用（P1-2），故按 TTL 缓存；overlay/custom
        # 变更时立即失效，保证装态不陈旧。
        self._cache: Optional[List[BrickEntry]] = None
        self._cache_ts: float = 0.0
        self.cache_ttl: float = 60.0
        self._load_overlay()

    # ---- overlay 持久化 -------------------------------------------------
    def _load_overlay(self) -> None:
        try:
            if self.bricks_json.exists():
                data = json.loads(self.bricks_json.read_text(encoding="utf-8"))
                self._overlay = data.get("overlay", {}) or {}
                self._custom_bricks = [
                    BrickEntry(**b) for b in (data.get("custom_bricks") or [])
                ]
        except Exception as exc:  # noqa: BLE001 - 坏文件不致命
            logger.warning("bricks.json load failed (reset): %s", exc)
            self._overlay, self._custom_bricks = {}, []

    def persist(self) -> None:
        """仅落盘 overlay + custom_bricks（声明是派生的，不写）。"""
        data = {
            "version": 1,
            "overlay": self._overlay,
            "custom_bricks": [b.to_dict() for b in self._custom_bricks],
            "updated_at": time.time(),
        }
        try:
            self.bricks_json.parent.mkdir(parents=True, exist_ok=True)
            self.bricks_json.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("bricks.json persist failed: %s", exc)

    def set_install_state(self, brick_id: str, state: str, enabled: bool = True,
                          custom: bool = False, user_provided_sha256: Optional[str] = None) -> None:
        """记录一次安装/卸载决策到 overlay（解决 tool/software 重启清空）。"""
        with self._lock:
            ov = self._overlay.get(brick_id, {})
            ov["install_state"] = state
            ov["enabled"] = enabled
            ov["custom"] = custom
            ov["user_provided_sha256"] = user_provided_sha256
            if state == "installed":
                ov["installed_at"] = time.time()
            self._overlay[brick_id] = ov
        self.persist()
        self.invalidate_cache()

    def add_custom_brick(self, entry: BrickEntry) -> None:
        with self._lock:
            self._custom_bricks.append(entry)
        self.persist()
        self.invalidate_cache()

    # ---- discover（四源聚合） -------------------------------------------
    def discover(self, refresh: bool = False) -> List[BrickEntry]:
        now = time.time()
        if not refresh:
            with self._lock:
                if self._cache is not None and (now - self._cache_ts) < self.cache_ttl:
                    return list(self._cache)   # 新 list，元素按只读对待

        entries: List[BrickEntry] = []
        # 顺序有讲究：discover_l2_adapters() 会把 L2 适配器工具（freecad_*/blender_* 等）
        # 注册进 TOOL_REGISTRY，故先扫 software 再扫 tools，首次调用即可拿到完整工具集
        #（否则首轮只有内置工具，要等缓存过期才补齐 L2 那批）。
        entries.extend(self._discover_software())
        entries.extend(self._discover_tools())
        entries.extend(self._discover_modules())
        entries.extend(self._discover_skills())
        # merge overlay + custom（overlay 以用户决策为准，校正 install_state/installed_at）
        with self._lock:
            for e in entries:
                ov = self._overlay.get(e.id)
                if ov:
                    if ov.get("install_state"):
                        e.install_state = ov["install_state"]
                    if ov.get("installed_at"):
                        e.installed_at = ov["installed_at"]
            entries.extend(self._custom_bricks)
            self._cache = list(entries)
            self._cache_ts = now
        return entries

    def invalidate_cache(self) -> None:
        """安装/卸载/overlay 变更后调用，丢弃快照使下次 discover() 重新聚合。"""
        with self._lock:
            self._cache = None
            self._cache_ts = 0.0

    # ---- 四源适配器（只读，fail-open） -----------------------------------
    def _discover_tools(self) -> List[BrickEntry]:
        out: List[BrickEntry] = []
        try:
            from tools.registry import registry as TOOL_REGISTRY
            for name in TOOL_REGISTRY.get_all_tool_names():
                try:
                    e = TOOL_REGISTRY.get_entry(name)
                    if e is None:
                        continue
                    desc = getattr(e, "description", "") or ""
                    req = getattr(e, "requires_env", None)
                    requires = [req] if isinstance(req, str) else list(req or [])
                    out.append(BrickEntry(
                        id=f"tool:{e.name}",
                        type="tool",
                        name=e.name,
                        description=desc,
                        install_state="installed",   # 进程内常驻
                        source="bundled",
                        requires=requires,
                        entry_point=getattr(e, "toolset", None),
                        extra={"toolset": getattr(e, "toolset", None),
                               "emoji": getattr(e, "emoji", None)},
                    ))
                except Exception as exc:  # noqa: BLE001
                    logger.debug("tool entry %s parse skipped: %s", name, exc)
                    continue
        except Exception as exc:  # noqa: BLE001
            logger.debug("discover tools failed (fail-open): %s", exc)
        return out

    def _discover_modules(self) -> List[BrickEntry]:
        out: List[BrickEntry] = []
        try:
            from agent.module_catalog import (
                bundled_catalog_path, default_catalog_path,
                get_catalog_modules, is_module_installed,
            )
            modules_dir = default_catalog_path().parent   # ~/.vermes/modules/
            modules = get_catalog_modules(str(bundled_catalog_path()))
            for m in modules:
                try:
                    installed = is_module_installed(m.name)
                    out.append(BrickEntry(
                        id=f"module:{m.name}",
                        type="module",
                        name=m.display_name or m.name,
                        description=m.description or "",
                        install_state="installed" if installed else "available",
                        source="github-release" if m.repository else "official",
                        version=m.latest or None,
                        sha256=m.code_sha256 or None,
                        size_bytes=m.size_code or None,
                        requires=[],
                        provides_tools=list(m.provides_tools or []),
                        entry_point=str(modules_dir / m.name) if installed else None,
                        extra={"keywords": list(m.keywords or []),
                               "repository": m.repository,
                               "homepage": m.homepage,
                               "recommended": m.recommended},
                    ))
                except Exception as exc:  # noqa: BLE001
                    logger.debug("module %s parse skipped: %s", m.name, exc)
                    continue
        except Exception as exc:  # noqa: BLE001
            logger.debug("discover modules failed (fail-open): %s", exc)
        return out

    def _discover_skills(self) -> List[BrickEntry]:
        out: List[BrickEntry] = []
        try:
            from agent.skill_utils import (
                extract_skill_description, get_all_skills_dirs,
                iter_skill_index_files, parse_frontmatter,
            )
            for skills_dir in get_all_skills_dirs():
                if not skills_dir.is_dir():
                    continue
                for skill_file in iter_skill_index_files(skills_dir, "SKILL.md"):
                    try:
                        raw = skill_file.read_text(encoding="utf-8")
                        fm, _ = parse_frontmatter(raw)
                        name = fm.get("name") or skill_file.parent.name
                        desc = extract_skill_description(fm)
                        # 护栏：metadata 可能是字符串（畸形 YAML）——照 skill_utils 的
                        # 处理方式降级为空，避免该 skill 被 except 吞掉、从注册表消失。
                        _meta = fm.get("metadata")
                        if not isinstance(_meta, dict):
                            _meta = {}
                        # 兼容 fork 双键名：**实测 102 个 SKILL.md 用小写 `hermes:`、
                        # 0 个用大写 `Vermes:`**（Hermes→Vermes fork 遗留），故两个键都读。
                        # 注：仓库 skill_utils.extract_skill_conditions 只读大写 Vermes，
                        # 对全部 skill 静默返回空（既有 bug，此处不修，见审计报告）。
                        vermes = _meta.get("Vermes") or _meta.get("hermes") or {}
                        if not isinstance(vermes, dict):
                            vermes = {}
                        conds = {
                            k: list(vermes.get(k, []) or [])
                            for k in ("requires_toolsets", "fallback_for_toolsets",
                                      "requires_tools", "fallback_for_tools")
                        }
                        caps = conds["requires_toolsets"] + conds["fallback_for_toolsets"]
                        caps = list(dict.fromkeys(caps))  # 去重保序
                        out.append(BrickEntry(
                            id=f"skill:{name}",
                            type="skill",
                            name=str(name),
                            description=desc or "",
                            capabilities=caps,
                            install_state="installed",   # 目录即状态
                            source="community",
                            entry_point=str(skill_file),
                            extra={"conditions": conds, "vermes_meta": vermes},
                        ))
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("skill %s parse skipped: %s", skill_file, exc)
                        continue
        except Exception as exc:  # noqa: BLE001
            logger.debug("discover skills failed (fail-open): %s", exc)
        return out

    def _discover_software(self) -> List[BrickEntry]:
        out: List[BrickEntry] = []
        installed: Dict[str, int] = {}
        try:
            from vermes_cli.adapters.bootstrap import discover_l2_adapters
            installed = discover_l2_adapters() or {}
        except Exception as exc:  # noqa: BLE001
            logger.debug("discover l2 adapters failed (fail-open): %s", exc)

        # 已装适配器（side-effect: 会重新 register 工具，与 bootstrap 启动时一致；幂等）
        installed_keys = set()
        for software, n in installed.items():
            if n < 0:
                continue  # -1 = 跳过
            installed_keys.add(software)
            out.append(BrickEntry(
                id=f"software:{software}",
                type="software",
                name=software,
                install_state="installed",
                source="cli-anything",
                provides_tools=[],  # 工具名运行时内省，目录不含
                extra={"registered_tool_count": n},
            ))

        # cli-hub 目录（fail-open：cli-hub 缺失则空 list）
        try:
            from vermes_cli.adapters.recommend import CatalogIndex, CliAnythingHubSource
            idx = CatalogIndex()
            idx.add_source(CliAnythingHubSource())
            for e in idx.all_entries():
                key = e.software
                if key in installed_keys:
                    continue
                req = e.requires or ""
                requires = [r for r in req.split() if r] if req else []
                out.append(BrickEntry(
                    id=f"software:{key}",
                    type="software",
                    name=e.name or key,
                    description=e.description or "",
                    capabilities=list(e.keywords or []),
                    domain=e.domain or None,
                    install_state="available",
                    source="cli-anything-hub",
                    requires=requires,
                    version=e.version or None,
                    entry_point=e.harness or None,
                    extra={"install_cmd": e.install_cmd,
                           "homepage": e.homepage},
                ))
        except Exception as exc:  # noqa: BLE001
            logger.debug("discover software catalog failed (fail-open): %s", exc)
        return out

    # ---- 统一能力索引（合并 brick caps + P0 模型 caps） -------------------
    def capability_index(self, refresh: bool = False) -> Dict[str, Any]:
        bricks = self.discover(refresh=refresh)
        by_type: Dict[str, int] = {}
        for b in bricks:
            by_type[b.type] = by_type.get(b.type, 0) + 1

        providers: List[Dict[str, Any]] = []
        try:
            from vermes_cli.capabilities.manifest import generate_capability_manifest
            m = generate_capability_manifest(refresh=refresh)
            for grp in ("curated", "mainstream", "pinned"):
                for c in (m.get(grp) or []):
                    caps = c.get("capabilities") or {}
                    # manifest 的 capabilities 可能是 dict（{tools:true,...}）或 list（能力键列表）
                    cap_keys = (
                        list(caps.keys()) if isinstance(caps, dict)
                        else list(caps) if isinstance(caps, list)
                        else []
                    )
                    providers.append({
                        "id": c.get("id"),
                        "type": "provider",
                        "name": c.get("display_name") or c.get("id"),
                        "source_key": c.get("source_key"),
                        "capabilities": cap_keys,
                        "models": c.get("models"),
                    })
        except Exception as exc:  # noqa: BLE001
            logger.debug("capability_index provider merge skipped: %s", exc)

        return {
            "bricks_total": len(bricks),
            "by_type": by_type,
            "bricks": [b.to_dict() for b in bricks],
            "providers": providers,
        }


# ---------------------------------------------------------------------------
# 模块级单例
# ---------------------------------------------------------------------------

_BRICK_REGISTRY: Optional[BrickRegistry] = None


def get_brick_registry() -> BrickRegistry:
    global _BRICK_REGISTRY
    if _BRICK_REGISTRY is None:
        _BRICK_REGISTRY = BrickRegistry()
    return _BRICK_REGISTRY
