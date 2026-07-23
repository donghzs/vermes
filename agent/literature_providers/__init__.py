"""Bundled literature providers.

Each provider self-declares its credential metadata (via ``register_service``)
at import time and is registered into the literature registry by
:func:`agent.literature_registry.bootstrap_builtin_providers` (called from the
``literature_search`` tool module). User-authored providers plug in through
``PluginContext.register_literature_provider``.
"""

from agent.literature_providers.cnki import CnkiProvider
from agent.literature_providers.crossref import CrossrefProvider
from agent.literature_providers.openalex import OpenAlexProvider
from agent.literature_providers.wanfang import WanfangProvider

__all__ = [
    "OpenAlexProvider",
    "CrossrefProvider",
    "CnkiProvider",
    "WanfangProvider",
]
