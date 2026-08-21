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


# ---------------------------------------------------------------------------
# P1: 两步安装 + 装后触发 bootstrap
# ---------------------------------------------------------------------------

@dataclass
class InstallResult:
    """install() 的返回值。"""

    software: str
    adapter_installed: bool       # 第一步：adapter（cli-hub install）是否成功
    adapter_message: str = ""    # 成功/失败消息
    backend_ready: bool = False  # 第二步：本体后端是否就绪
    backend_hint: str = ""      # 本体安装指引（requires 透传）
    tools_registered: int = -1  # 装后 bootstrap 注册的工具数（-1 = 未触发）


def install(
    rec: Recommendation,
    *,
    cli_hub_bin: str = "cli-hub",
    re_scan: bool = True,
) -> InstallResult:
    """两步安装：① adapter（cli-hub install）② 检查本体就绪 + 装后重扫注册。

    - 第一步失败 → 直接返回，不尝试第二步。
    - 第二步是检查（非安装）：用 BackendLocator 检查本体是否在 PATH/环境变量上。
    - re_scan=True 时装后触发 discover_l2_adapters 重新扫描注册。
    """
    result = InstallResult(software=rec.software, adapter_installed=False, backend_hint=rec.backend_hint)

    # 第一步：安装 adapter（cli-hub install <name>）
    if not shutil.which(cli_hub_bin):
        result.adapter_message = "cli-hub 未安装，无法自动安装 adapter"
        return result
    try:
        # 从 adapter_install 提取短名："cli-hub install freecad" → "freecad"
        parts = rec.adapter_install.split()
        name = parts[-1] if parts else rec.software
        proc = subprocess.run(
            [cli_hub_bin, "install", name],
            capture_output=True, text=True, check=False, timeout=120,
        )
        if proc.returncode == 0:
            result.adapter_installed = True
            result.adapter_message = proc.stdout.strip()[:200]
        else:
            result.adapter_message = f"安装失败 (rc={proc.returncode}): {proc.stderr.strip()[:200]}"
            return result
    except Exception as exc:  # noqa: BLE001
        result.adapter_message = f"安装异常: {exc}"
        return result

    # 第二步：检查本体后端就绪
    try:
        from .discovery import BackendLocator
        locator = BackendLocator()
        target = locator.locate(rec.software)
        result.backend_ready = target.backend_resolved is not None
    except Exception:  # noqa: BLE001 — 本体检查失败不阻塞
        pass

    # 装后触发 bootstrap 重新扫描注册
    if re_scan and result.adapter_installed:
        try:
            from .bootstrap import discover_l2_adapters
            scan = discover_l2_adapters()
            result.tools_registered = scan.get(rec.software, -1)
        except Exception as exc:  # noqa: BLE001
            logger.warning("install 后 bootstrap 重扫失败: %s", exc)
            result.tools_registered = -1

    return result


# ---------------------------------------------------------------------------
# P2: 认知层 rank_hook 信号接入
# ---------------------------------------------------------------------------

def usage_rank_hook(
    entries: list[CatalogEntry],
    ctx: dict,
    *,
    weight_usage: float = 0.6,
    weight_score: float = 0.4,
) -> list[CatalogEntry]:
    """认知层 rank_hook：把 memory_fabric 的使用频率注入排序。

    排序公式：final = weight_usage * usage_rank + weight_score * keyword_score_rank
    - usage_rank：按 get_usage_counts(kind="adapter") 的 count 降序排列
    - keyword_score_rank：按 recommend() 已计算的 keyword 得分降序排列
    - 未见 usage 的适配器：usage_rank 排末尾（未知 ≠ 0 分）

    默认权重 60% usage / 40% 关键词——「已验证好用」优先于「文本匹配高」。
    """
    try:
        from agent.memory_fabric import get_usage_counts
        usage_rows = get_usage_counts(kind="adapter", limit=100)
    except Exception:  # noqa: BLE001 — 认知层不可用时降级到纯关键词
        logger.debug("usage_rank_hook: memory_fabric 不可用，降级纯关键词")
        return entries

    # 构建 software → usage count 映射
    usage_map: dict[str, int] = {}
    for row in usage_rows:
        sid = row.get("id", "")
        # pointer 格式 "usage_adapter:<software>"
        if ":" in sid:
            sid = sid.split(":")[-1]
        usage_map[sid] = row.get("count", 0)

    # 按 usage count 排名（降序，未见排末尾）
    n = len(entries)
    usage_order = sorted(entries, key=lambda e: usage_map.get(e.software, 0), reverse=True)
    usage_rank = {e.software: (n - i) for i, e in enumerate(usage_order)}

    # 关键词得分排名（降序）——从 scored dict 提取
    # 由于 rank_hook 在 recommend() 内部调用，scored 信息已丢失，
    # 用 entries 原始顺序（recommend 已按 score 降序排过）做近似
    kw_rank = {e.software: (n - i) for i, e in enumerate(entries)}

    def _final(e: CatalogEntry) -> float:
        u = usage_rank.get(e.software, 0)
        k = kw_rank.get(e.software, 0)
        max_u = max(usage_rank.values()) if usage_rank else 1
        max_k = max(kw_rank.values()) if kw_rank else 1
        return weight_usage * (u / max_u if max_u else 0) + weight_score * (k / max_k if max_k else 0)

    return sorted(entries, key=_final, reverse=True)
