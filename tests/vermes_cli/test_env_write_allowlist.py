"""Regression test: PUT /api/env allowlist must accept every provider key
the Settings UI can render and the user can fill.

Root bug: ``AGNES_API_KEY`` / ``SCNET_API_KEY`` (and other provider keys that
exist in ``chat.PROVIDERS`` but were missing from the hardcoded
``_ENV_WRITE_ALLOWED_KEYS``) were rejected with 403 — "can see but can't store".
Fix: ``_allowed_env_keys()`` now unions the hardcoded set with the three
authoritative provider sources (chat.PROVIDERS env_key, model-provider
profiles env_vars, add-provider templates api_key_env) plus the dynamic
service registry.
"""

from __future__ import annotations

from vermes_cli.blueprints.config import _ENV_WRITE_ALLOWED_KEYS, _allowed_env_keys


def test_allowed_keys_is_superset_of_hardcoded():
    allowed = _allowed_env_keys()
    assert set(_ENV_WRITE_ALLOWED_KEYS) <= allowed


def test_agnes_key_allowed():
    assert "AGNES_API_KEY" in _allowed_env_keys()


def test_scnet_key_allowed():
    assert "SCNET_API_KEY" in _allowed_env_keys()


def test_every_chat_provider_env_key_allowed():
    """Every provider env_key in chat.PROVIDERS must be writable.

    This is the invariant that was violated: the frontend Settings UI's
    getEnvKey maps provider id → env var from exactly this table, so any key
    listed here must be accepted by PUT /api/env.
    """
    from vermes_cli.blueprints.chat import PROVIDERS

    allowed = _allowed_env_keys()
    for _pid, _pdef in PROVIDERS.items():
        if not isinstance(_pdef, dict):
            continue
        _k = _pdef.get("env_key")
        if _k:
            assert _k in allowed, (
                f"provider {_pid!r} env_key {_k!r} missing from allowlist"
            )


def test_every_model_provider_env_var_allowed():
    """Every model-provider profile env_var must be writable."""
    from providers import list_providers

    allowed = _allowed_env_keys()
    for _pp in list_providers():
        for _var in getattr(_pp, "env_vars", ()) or ():
            if _var:
                assert _var in allowed, (
                    f"model-provider {_pp.name!r} env_var {_var!r} missing"
                )


def test_every_provider_template_api_key_env_allowed():
    """Every add-provider template api_key_env must be writable."""
    from vermes_cli.blueprints.providers import PROVIDER_TEMPLATES

    allowed = _allowed_env_keys()
    for _tpl in PROVIDER_TEMPLATES.values():
        if isinstance(_tpl, dict):
            _k = _tpl.get("api_key_env")
            if _k:
                assert _k in allowed, (
                    f"template api_key_env {_k!r} missing from allowlist"
                )
