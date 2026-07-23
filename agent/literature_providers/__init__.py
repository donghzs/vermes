"""Bundled literature providers.

Each provider self-declares its credential metadata (via ``register_service``)
at import time and is registered into the literature registry by
:func:`agent.literature_registry.bootstrap_builtin_providers` (called from the
``literature_search`` tool module). User-authored providers plug in through
``PluginContext.register_literature_provider``.
"""

from agent.literature_providers.arxiv import ArxivProvider
from agent.literature_providers.cnki import CnkiProvider
from agent.literature_providers.core import CoreProvider
from agent.literature_providers.crossref import CrossrefProvider
from agent.literature_providers.doaj import DoajProvider
from agent.literature_providers.ebsco import EbscoProvider
from agent.literature_providers.europepmc import EuropePmcProvider
from agent.literature_providers.ieee import IeeeProvider
from agent.literature_providers.openalex import OpenAlexProvider
from agent.literature_providers.pubmed import PubMedProvider
from agent.literature_providers.sciencedirect import ScienceDirectProvider
from agent.literature_providers.scopus import ScopusProvider
from agent.literature_providers.semanticscholar import SemanticScholarProvider
from agent.literature_providers.springer import SpringerProvider
from agent.literature_providers.vip import VipProvider
from agent.literature_providers.wanfang import WanfangProvider
from agent.literature_providers.wos import WosProvider

__all__ = [
    # 免费源
    "OpenAlexProvider",
    "CrossrefProvider",
    "PubMedProvider",
    "ArxivProvider",
    "SemanticScholarProvider",
    "EuropePmcProvider",
    "DoajProvider",
    "CoreProvider",
    # 中文付费源
    "CnkiProvider",
    "WanfangProvider",
    "VipProvider",
    # 国际付费源
    "WosProvider",
    "ScopusProvider",
    "ScienceDirectProvider",
    "IeeeProvider",
    "SpringerProvider",
    "EbscoProvider",
]
