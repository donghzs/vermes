"""⑭ Bot 实验室：远端 agent 热度榜骨架。

仿 ``recommend.py`` 的 ``CatalogSource`` / ``CatalogIndex`` 范式：
- ``AgentCatalogSource``：HTTP GET 一个 catalog URL，解析 JSON list → ``AgentCatalogEntry``。
- ``AgentCatalogIndex``：多源聚合（按 id 去重）+ 全量检索。
- 模块级单例 ``AGENT_CATALOG_INDEX``：默认**空**，调用方 ``add_source`` 后才贡献条目。

fail-open 哲学（与 recommend.py 一致）：网络不可用 / 数据源缺失 / 解析失败 → 返回空 list，
绝不阻断本地 agent 发现。本模块只提供「 plumbing 骨架」——真实 community 数据源上线时，
只需把 URL 指向可用端点并 ``AGENT_CATALOG_INDEX.add_source(AgentCatalogSource(url))`` 即可。
"""
from __future__ import annotations

import json
import logging
import urllib.request
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger("vermes.agent_catalog")

# 占位 community 源（真实数据源上线前指向此处；不可达时 fail-open 为空）
AGENT_CATALOG_URL = "https://catalog.vermes.ai/agents.json"

# 超时（秒）：远端拉取不应拖垮本地发现
_AGENT_CATALOG_TIMEOUT = 10


@dataclass
class AgentCatalogEntry:
    """远端 catalog 里的一个 agent 条目（多源归一化）。"""

    id: str
    name: str
    kind: str = "remote"          # remote | cli | app | mcp
    auth_scheme: str = "none"     # none | apikey | oauth | local
    description: str = ""
    version: str = ""
    popularity: int = 0           # 社区热度（下载/星标/使用量归一）
    homepage: str = ""
    repository: str = ""


class AgentCatalogSource:
    """默认源：HTTP GET 一个 catalog JSON，解析为 ``AgentCatalogEntry`` 列表。"""

    name = "remote-agent-catalog"

    def __init__(self, url: str = AGENT_CATALOG_URL):
        self.url = url

    def list_entries(self) -> List[AgentCatalogEntry]:
        try:
            req = urllib.request.Request(
                self.url, headers={"User-Agent": "Vermes/1.0"}
            )
            with urllib.request.urlopen(req, timeout=_AGENT_CATALOG_TIMEOUT) as resp:
                raw = resp.read().decode("utf-8")
            data = json.loads(raw)
        except Exception as exc:  # noqa: BLE001 - 远端不可用 → fail-open 空 list
            logger.debug("agent catalog fetch failed (fail-open): %s", exc)
            return []

        items = data if isinstance(data, list) else (data.get("agents") or [])
        if not isinstance(items, list):
            return []
        return [self._parse(i) for i in items if isinstance(i, dict)]

    @staticmethod
    def _parse(item: dict) -> AgentCatalogEntry:
        aid = item.get("id") or item.get("name") or ""
        if ":" not in aid:
            aid = f"agent:{aid}"
        # P2 健壮性：脏 popularity（如 "oops" / {} / 12.5）不得拖垮整批解析。
        # 逐条 fail-open（对比 recommend.py 逐条 try 内字段 .strip()）——单条脏数据只归零，不崩。
        try:
            popularity = int(item.get("popularity") or 0)
        except (ValueError, TypeError):
            popularity = 0
        return AgentCatalogEntry(
            id=aid,
            name=item.get("name", aid),
            kind=item.get("kind", "remote"),
            auth_scheme=item.get("auth_scheme", "none"),
            description=item.get("description", ""),
            version=item.get("version", ""),
            popularity=popularity,
            homepage=item.get("homepage", ""),
            repository=item.get("repository", ""),
        )


class AgentCatalogIndex:
    """多源聚合 + 去重（按 id）+ 全量检索。"""

    def __init__(self):
        self._entries: dict[str, AgentCatalogEntry] = {}

    def add_source(self, source: AgentCatalogSource) -> None:
        for e in source.list_entries():
            self._entries.setdefault(e.id, e)

    def all_entries(self) -> List[AgentCatalogEntry]:
        return list(self._entries.values())


# 模块级默认索引（空；调用方 add_source 后 registry._discover_agents 才贡献远端条目）
AGENT_CATALOG_INDEX = AgentCatalogIndex()
