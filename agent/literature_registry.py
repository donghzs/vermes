"""
Literature Provider Registry
=============================

Central map of registered literature providers. Populated by bundled
built-ins through :func:`bootstrap_builtin_providers` (called from the
``literature_search`` tool module at import time) and by user plugins through
:meth:`PluginContext.register_literature_provider`; consumed by the
``literature_search`` tool wrapper to dispatch each call to the active
backend.

Active selection
----------------
The active provider is chosen by configuration with this precedence:

1. ``literature.search_backend`` (per-capability override).
2. ``literature.backend`` (shared fallback).
3. If exactly one registered provider is available, use it.
4. Legacy preference order — paid Chinese sources (``cnki``/``wanfang``/``vip``)
   → paid international (``wos``/``scopus``/…) → free fallbacks
   (``openalex``/``crossref``/…) — filtered by availability. Paid providers are listed first
   so a user who has supplied credentials lands on a higher-quality Chinese
   source, while a user with no credentials transparently falls through to
   the always-free OpenAlex / Crossref sources.
5. Otherwise ``None`` — the tool surfaces a helpful error pointing at the
   credential-setup UI.

The availability filter (``is_available()``) is applied at every step so an
unconfigured paid provider is never surfaced as active on a fresh install.
"""

from __future__ import annotations

import logging
import threading
from typing import Dict, List, Optional

from agent.literature_provider import LiteratureProvider

logger = logging.getLogger(__name__)


_providers: Dict[str, LiteratureProvider] = {}
_lock = threading.Lock()


def register_provider(provider: LiteratureProvider) -> None:
    """Register a literature provider.

    Re-registration (same ``name``) overwrites the previous entry and logs a
    debug message — makes hot-reload scenarios (tests, dev loops) behave
    predictably.
    """
    if not isinstance(provider, LiteratureProvider):
        raise TypeError(
            f"register_provider() expects a LiteratureProvider instance, "
            f"got {type(provider).__name__}"
        )
    name = provider.name
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Literature provider .name must be a non-empty string")
    with _lock:
        existing = _providers.get(name)
        _providers[name] = provider
    if existing is not None:
        logger.debug(
            "Literature provider '%s' re-registered (was %r)",
            name, type(existing).__name__,
        )
    else:
        logger.debug(
            "Registered literature provider '%s' (%s)",
            name, type(provider).__name__,
        )


def list_providers() -> List[LiteratureProvider]:
    """Return all registered providers, sorted by name."""
    with _lock:
        items = list(_providers.values())
    return sorted(items, key=lambda p: p.name)


def get_provider(name: str) -> Optional[LiteratureProvider]:
    """Return the provider registered under *name*, or None."""
    if not isinstance(name, str):
        return None
    with _lock:
        return _providers.get(name.strip())


def get_provider_by_ref(ref: str) -> Optional[LiteratureProvider]:
    """Resolve a provider by id (``name``) or by human ``label`` (case-insensitive).

    Lets users reference a custom source either by its stable id or by the
    display name they typed in the Settings UI.
    """
    if not isinstance(ref, str) or not ref.strip():
        return None
    direct = get_provider(ref)
    if direct is not None:
        return direct
    ref_l = ref.strip().lower()
    with _lock:
        for p in _providers.values():
            p_label = getattr(p, "label", None) or getattr(p, "display_name", None)
            if (p.name or "").lower() == ref_l or (p_label or "").lower() == ref_l:
                return p
    return None


# ---------------------------------------------------------------------------
# Active-provider resolution
# ---------------------------------------------------------------------------


def _read_config_key(*path: str) -> Optional[str]:
    """Resolve a dotted config key from ``config.yaml``. Returns None on miss."""
    try:
        from hermes_cli.config import load_config

        cfg = load_config()
        cur = cfg
        for segment in path:
            if not isinstance(cur, dict):
                return None
            cur = cur.get(segment)
        if isinstance(cur, str) and cur.strip():
            return cur.strip()
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not read config %s: %s", ".".join(path), exc)
    return None


# Legacy preference order — paid Chinese sources first so a credentialed user
# lands on the best-quality source, free sources last as the fallback for users
# who have configured nothing. Filtered by ``is_available()`` at walk time.
_LEGACY_PREFERENCE = (
    # 中文付费源（凭证具备时质量最高）
    "cnki",
    "wanfang",
    "vip",
    # 国际付费源
    "wos",
    "scopus",
    "sciencedirect",
    "ieee",
    "springer",
    "ebsco",
    # 免费兜底源（零配置可用）
    "openalex",
    "crossref",
    "semanticscholar",
    "pubmed",
    "arxiv",
    "europepmc",
    "doaj",
    "core",
)


def _resolve(configured: Optional[str], *, capability: str) -> Optional[LiteratureProvider]:
    """Resolve the active provider for a capability ("search" | "fulltext").

    Resolution rules (in order):

    1. **Explicit config wins, ignoring availability.** If ``literature.<cap>_backend``
       or ``literature.backend`` names a registered provider that supports
       *capability*, return it even if ``is_available()`` is False — the
       dispatcher will surface a precise "X credential is not set" error to
       the user instead of silently routing somewhere else.
    2. **Single-provider shortcut.** When only one registered provider supports
       *capability* AND ``is_available()`` is True, return it.
    3. **Legacy preference walk, filtered by availability.** Walk
       :data:`_LEGACY_PREFERENCE` looking for a provider whose
       ``supports_<capability>()`` is True AND ``is_available()`` is True.
    """
    with _lock:
        snapshot = dict(_providers)

    def _capable(p: LiteratureProvider) -> bool:
        if capability == "search":
            return bool(p.supports_search())
        if capability == "fulltext":
            return bool(p.supports_fulltext())
        return False

    def _is_available_safe(p: LiteratureProvider) -> bool:
        try:
            return bool(p.is_available())
        except Exception as exc:  # noqa: BLE001
            logger.debug("provider %s.is_available() raised %s", p.name, exc)
            return False

    if configured:
        provider = snapshot.get(configured)
        if provider is not None and _capable(provider):
            return provider
        if provider is None:
            logger.debug(
                "literature backend '%s' configured but not registered; falling back",
                configured,
            )
        else:
            logger.debug(
                "literature backend '%s' configured but does not support '%s'; falling back",
                configured, capability,
            )

    eligible = [
        p for p in snapshot.values()
        if _capable(p) and _is_available_safe(p)
    ]
    if len(eligible) == 1:
        return eligible[0]

    for legacy in _LEGACY_PREFERENCE:
        provider = snapshot.get(legacy)
        if provider is not None and _capable(provider) and _is_available_safe(provider):
            return provider

    return None


def get_active_search_provider() -> Optional[LiteratureProvider]:
    """Resolve the currently-active literature search provider.

    Reads ``literature.search_backend`` (preferred) or ``literature.backend``
    (shared fallback) from config.yaml; falls back per the module docstring.
    """
    explicit = _read_config_key("literature", "search_backend") or _read_config_key(
        "literature", "backend"
    )
    return _resolve(explicit, capability="search")


# ``get_active_literature_provider`` is the canonical name used by the tool
# wrapper and external callers; it aliases the search resolver.
def get_active_literature_provider() -> Optional[LiteratureProvider]:
    """Resolve the currently-active literature provider (search-capable)."""
    return get_active_search_provider()


def get_active_fulltext_provider() -> Optional[LiteratureProvider]:
    """Resolve the currently-active full-text provider, if any."""
    explicit = _read_config_key("literature", "fulltext_backend") or _read_config_key(
        "literature", "backend"
    )
    return _resolve(explicit, capability="fulltext")


def _reset_for_tests() -> None:
    """Clear the registry. **Test-only.**"""
    with _lock:
        _providers.clear()


def bootstrap_builtin_providers() -> None:
    """Register the bundled literature providers (idempotent, safe to re-call).

    Delayed-imports the provider classes to avoid an import cycle between this
    module and :mod:`agent.literature_providers` (the registry is imported by
    the providers; the providers are imported lazily here).
    """
    try:
        from agent.literature_providers import (
            ArxivProvider,
            CnkiProvider,
            CoreProvider,
            CrossrefProvider,
            DoajProvider,
            EbscoProvider,
            EuropePmcProvider,
            IeeeProvider,
            OpenAlexProvider,
            PubMedProvider,
            ScienceDirectProvider,
            ScopusProvider,
            SemanticScholarProvider,
            SpringerProvider,
            VipProvider,
            WanfangProvider,
            WosProvider,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to import bundled literature providers: %s", exc)
        return

    for cls in (
        OpenAlexProvider,
        CrossrefProvider,
        PubMedProvider,
        ArxivProvider,
        SemanticScholarProvider,
        EuropePmcProvider,
        DoajProvider,
        CoreProvider,
        CnkiProvider,
        WanfangProvider,
        VipProvider,
        WosProvider,
        ScopusProvider,
        ScienceDirectProvider,
        IeeeProvider,
        SpringerProvider,
        EbscoProvider,
    ):
        try:
            register_provider(cls())
        except Exception as exc:  # noqa: BLE001
            logger.debug("bootstrap literature provider %s failed: %s", cls.__name__, exc)


def bootstrap_custom_providers() -> None:
    """Register user-defined custom literature sources (institutions' internal
    portals). Safe to call repeatedly; re-reads the persisted definitions each
    time so edits in the Settings UI take effect without a restart.

    Kept separate from :func:`bootstrap_builtin_providers` so the built-in
    provider count stays stable for tests, while custom sources are layered on
    top at tool-dispatch time.
    """
    try:
        from agent.literature_custom_store import list_custom_sources
        from agent.literature_providers.custom import CustomHttpProvider
    except Exception as exc:  # noqa: BLE001
        logger.debug("custom literature providers unavailable: %s", exc)
        return

    for definition in list_custom_sources():
        name = definition.get("id")
        if not name:
            continue
        try:
            # 专用适配器（EmpireCMS 登录 + SSO → 动态 KNS8 镜像检索）。
            # 整族代理共用通用骨架 Kns8TempLoginProvider，仅入口差异靠配置表达。
            if _is_shutong_source(definition):
                from agent.literature_providers.shutong import ShutongProvider

                register_provider(ShutongProvider(definition))
            elif _is_wenx_source(definition):
                from agent.literature_providers.wenx import WenxProvider

                register_provider(WenxProvider(definition))
            else:
                register_provider(CustomHttpProvider(definition))
        except Exception as exc:  # noqa: BLE001
            logger.debug("bootstrap custom literature provider %s failed: %s", name, exc)


def _is_shutong_source(definition: dict) -> bool:
    """判定自定义源是否为书童 shutong（用于路由到专用适配器）。

    显式 ``provider_type == "shutong"`` 优先；否则按域名特征兜底识别，
    以兼容在 provider_type 检测落地之前就已注册的存量源。
    """
    if (definition.get("provider_type") or "").lower() == "shutong":
        return True
    haystack = " ".join(
        str(definition.get(k) or "")
        for k in ("id", "label", "base_url", "login_url", "url", "sso_url")
    ).lower()
    return "shutong" in haystack


def _is_wenx_source(definition: dict) -> bool:
    """判定自定义源是否为文献云图书馆 wenx / ccki 等同族代理。

    与 shutong 同构（EmpireCMS 登录 + SSO → KNS8 镜像），差异仅在入口形式
    （``/csNN.php`` 直接 302、且知网频道常需购买群组开通）。

    显式 ``provider_type == "wenx"`` 优先；否则按域名特征兜底识别，
    以兼容在 provider_type 检测落地之前就已注册的存量源。
    """
    if (definition.get("provider_type") or "").lower() in ("wenx", "ccki"):
        return True
    haystack = " ".join(
        str(definition.get(k) or "")
        for k in ("id", "label", "base_url", "login_url", "url", "sso_url")
    ).lower()
    return any(k in haystack for k in ("wenx", "ccki"))


# Registry of local-file providers registered by the most recent
# :func:`bootstrap_local_providers` call. Used by the search tool to fan out to
# all available local libraries in addition to the active HTTP provider.
_local_providers: List["LiteratureProvider"] = []


def bootstrap_local_providers() -> None:
    """Register the user's local literature libraries (folders / USB volumes).

    Safe to call repeatedly; re-reads ``~/.vermes/literature_local_sources.json``
    each time so adding/removing a local library takes effect without a restart.
    Only libraries whose folder is currently mounted & readable are registered.
    """
    global _local_providers
    _local_providers = []
    try:
        from agent.local_library_store import list_local_libraries
        from agent.literature_providers.local_file import LocalFileProvider
    except Exception as exc:  # noqa: BLE001
        logger.debug("local literature providers unavailable: %s", exc)
        return

    for lib in list_local_libraries():
        lid = lib.get("id")
        if not lid:
            continue
        try:
            provider = LocalFileProvider(
                {"id": lid, "root": lib.get("root", ""), "label": lib.get("label", lid)}
            )
            if provider.is_available():
                register_provider(provider)
                _local_providers.append(provider)
        except Exception as exc:  # noqa: BLE001
            logger.debug("bootstrap local literature provider %s failed: %s", lid, exc)


def iter_local_providers() -> List["LiteratureProvider"]:
    """Return local-file providers registered by the last bootstrap call."""
    return list(_local_providers)
