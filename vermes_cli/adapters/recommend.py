"""L2c 推荐层（薄插槽）：多源 catalog 抽象 + intent→catalog 最短路径映射。

设计纪律（L2C_RECOMMENDATION_LAYER_DESIGN.md v1.1）：
- 只做「多源 catalog + 倒排映射 + 差集 + 推荐」，不做推荐算法、不做「最合适」判断。
- 排序信号通过 rank_hook 由厚认知层（memory_fabric / 自进化 / usage）注入；
  默认 rank = 关键词命中数（朴素）。
- 直接透传 cli-hub 原生 category（35 分类）做倒排，不自己映射；双语桥接复用
  discovery 的 DOMAIN_BILINGUAL_HINTS（已有 4 域），不扩展 35 域（后置）。
- 差集 key = entry_point 去 "cli-anything-" 前缀（与 bootstrap.discover_l2_adapters
  返回的 software 对齐），而非 cli-hub 的 name 短名——两者对 16 个非标准命名
  harness（如 cc-switch → cli-anything-ccswitch）不一致。
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Callable, Optional, Protocol

from .discovery import DOMAIN_BILINGUAL_HINTS, MIN_TOOLSET_SCORE, _domain_match_score, _tokenize

logger = logging.getLogger(__name__)

_PREFIX = "cli-anything-"


@dataclass
class CatalogEntry:
    """catalog 里的一个软件适配器条目（多源归一化）。"""

    name: str            # cli-hub 短名（install 用），如 "freecad" / "cc-switch"
    software: str        # 差集 key：entry_point 去 "cli-anything-" 前缀（与 bootstrap 对齐）
    harness: str         # entry_point 原始命令名，如 "cli-anything-freecad"
    domain: str          # category 原始值（透传，35 分类）
    description: str
    requires: str        # 本体依赖声明（两步安装的第二步来源）
    keywords: list[str] = field(default_factory=list)  # 倒排用（name/software/domain/description 分词）
    source: str = "cli-anything-hub"
    install_cmd: str = ""   # "cli-hub install <name>"（短名，cli-hub 封装 pip install）
    version: str = ""
    homepage: str = ""


@dataclass
class Recommendation:
    """一次推荐结果。"""

    software: str
    domain: str
    reason: str             # "命中关键词：建模/倒角"
    matched_keywords: list[str]
    source: str
    score: float
    adapter_install: str    # 第一步：装 adapter（秒级）
    backend_hint: str       # 第二步：本体依赖（requires 透传）
    already_installed: bool = False


class CatalogSource(Protocol):
    name: str

    def list_entries(self) -> list[CatalogEntry]: ...


class CliAnythingHubSource:
    """默认源：包 `cli-hub list --json` 的输出（裸 list，101 entries，13 字段）。"""

    name = "cli-anything-hub"

    def __init__(self, cli_hub_bin: str = "cli-hub"):
        self.cli_hub_bin = cli_hub_bin

    def list_entries(self) -> list[CatalogEntry]:
        return self._parse(self._run_json(["list", "--json"]))

    def search_entries(self, term: str) -> list[CatalogEntry]:
        return self._parse(self._run_json(["search", term, "--json"]))

    def _run_json(self, args: list[str]) -> list[dict]:
        """跑 cli-hub 子命令并解析 JSON；不可用/失败 → 空 list（fail-open）。"""
        if not shutil.which(self.cli_hub_bin):
            return []
        try:
            proc = subprocess.run(
                [self.cli_hub_bin, *args],
                capture_output=True, text=True, check=False, timeout=30,
            )
            data = json.loads(proc.stdout or "[]")
            return data if isinstance(data, list) else []
        except Exception as exc:  # noqa: BLE001 - catalog 不可用时 fail-open
            logger.warning("cli-hub %s failed (fail-open): %s", args, exc)
            return []

    @staticmethod
    def _parse(raw: list[dict]) -> list[CatalogEntry]:
        entries: list[CatalogEntry] = []
        for item in raw:
            name = (item.get("name") or "").strip()
            ep = (item.get("entry_point") or "").strip()
            if not name and not ep:
                continue
            # 差集 key：entry_point 去前缀；非标准命名（无前缀）用 entry_point 本身。
            software = ep[len(_PREFIX):] if ep.startswith(_PREFIX) else (ep or name)
            domain = (item.get("category") or "").strip()
            desc = (item.get("description") or "").strip()
            entries.append(CatalogEntry(
                name=name,
                software=software,
                harness=ep,
                domain=domain,
                description=desc,
                requires=(item.get("requires") or "").strip(),
                keywords=_derive_keywords(name, software, domain, desc),
                install_cmd=f"cli-hub install {name}" if name else "",
                version=(item.get("version") or "").strip(),
                homepage=(item.get("homepage") or "").strip(),
            ))
        return entries


def _derive_keywords(*parts: str) -> list[str]:
    """从 name/software/domain/description 分词派生意图关键词（去停用词、去重）。"""
    stop = {
        "the", "a", "an", "to", "of", "and", "or", "in", "on", "for", "with",
        "via", "through", "using", "manage", "management", "native", "agent",
        "cli", "anything", "create", "inspect", "export", "import", "list",
    }
    seen: list[str] = []
    for part in parts:
        for t in re.findall(r"[a-z0-9_-]+", part.lower()):
            if t in stop or len(t) < 2 or t in seen:
                continue
            seen.append(t)
    return seen


class CatalogIndex:
    """多源聚合 + 去重（按 harness）+ 全量检索。"""

    def __init__(self):
        self._entries: dict[str, CatalogEntry] = {}

    def add_source(self, source: CatalogSource) -> None:
        for e in source.list_entries():
            self._entries.setdefault(e.harness or e.name, e)

    def all_entries(self) -> list[CatalogEntry]:
        return list(self._entries.values())


def recommend(
    intent: str,
    installed: Optional[set[str]] = None,
    rank_hook: Optional[Callable[[list[CatalogEntry], dict], list[CatalogEntry]]] = None,
    index: Optional[CatalogIndex] = None,
) -> list[Recommendation]:
    """intent → catalog 最短路径映射：倒排 + 差集 + 排序。

    - installed：已装适配器的 software 集合（来自 bootstrap.discover_l2_adapters 的 key）。
    - rank_hook：厚认知层注入的排序钩子（P2），默认按关键词得分降序。
    - 差集：已装（software in installed）不再推荐。
    """
    idx = index if index is not None else CATALOG_INDEX
    tokens = _tokenize(intent)
    intent_lc = intent.lower()
    installed = installed or set()

    scored: dict[str, tuple[CatalogEntry, float, list[str]]] = {}
    for e in idx.all_entries():
        if e.software in installed:
            continue  # 差集：已装不推荐
        dom_score, dom_matched = _domain_match_score(e.domain, intent_lc, tokens)
        kw_matched = [k for k in e.keywords if k in tokens]
        matched = list(dict.fromkeys(dom_matched + kw_matched))
        if not matched:
            continue
        score = max(dom_score, min(0.5, 0.1 * len(kw_matched)))
        if score < MIN_TOOLSET_SCORE:
            continue
        scored[e.harness or e.name] = (e, score, matched)

    entries = [t[0] for t in scored.values()]
    if rank_hook is not None:
        entries = rank_hook(entries, {"intent": intent, "installed": installed})
    else:
        entries.sort(key=lambda e: scored[e.harness or e.name][1], reverse=True)

    out: list[Recommendation] = []
    for e in entries:
        _, score, matched = scored[e.harness or e.name]
        out.append(Recommendation(
            software=e.software,
            domain=e.domain,
            reason=f"命中关键词：{'/'.join(matched[:5])}" if matched else "",
            matched_keywords=matched,
            source=e.source,
            score=round(score, 3),
            adapter_install=e.install_cmd,
            backend_hint=e.requires,
        ))
    return out


# 模块级默认索引（空；调用方 add_source 后 recommend）
CATALOG_INDEX = CatalogIndex()
