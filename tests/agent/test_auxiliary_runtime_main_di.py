"""C2: explicit dependency injection replaces module-level runtime-main globals.

Locks the behavior introduced in agent/auxiliary_client.py:
  * the five loose ``_RUNTIME_MAIN_*`` module globals are consolidated into a
    single typed ``RuntimeMainOverride`` holder;
  * ``get_runtime_main()`` is the single read point;
  * ``set_runtime_main`` / ``clear_runtime_main`` keep their signatures
    (backward compatible with conversation_loop and existing tests);
  * ``resolve_provider_client(runtime_main=...)`` accepts an explicit
    ``RuntimeMainOverride`` and injects it (DI) ahead of the process-local
    global and config.yaml.
"""
import agent.auxiliary_client as aux


def test_unset_runtime_main_is_none():
    aux.clear_runtime_main()
    assert aux.get_runtime_main() is None


def test_set_runtime_main_stores_typed_override():
    aux.set_runtime_main(
        "OpenRouter",
        "anthropic/claude-opus-4.6",
        base_url="https://api.openrouter.ai/api/v1",
        api_key="sk-test",
        api_mode="chat_completions",
    )
    o = aux.get_runtime_main()
    assert isinstance(o, aux.RuntimeMainOverride)
    assert o.provider == "openrouter"  # lowercased
    assert o.model == "anthropic/claude-opus-4.6"
    assert o.base_url == "https://api.openrouter.ai/api/v1"
    assert o.api_key == "sk-test"
    assert o.api_mode == "chat_completions"
    # auth_mode defaults empty
    assert o.auth_mode == ""


def test_clear_runtime_main_resets_to_none():
    aux.set_runtime_main("openai", "gpt-4o")
    aux.clear_runtime_main()
    assert aux.get_runtime_main() is None


def test_runtime_main_to_dict_omits_empty_fields():
    o = aux.RuntimeMainOverride(provider="openrouter", model="gpt-4o")
    d = aux._runtime_main_to_dict(o)
    # only the two set fields survive; empty strings are dropped
    assert d == {"provider": "openrouter", "model": "gpt-4o"}
    # and the dict shape is consumable by the normalizer
    assert aux._normalize_main_runtime(d)["provider"] == "openrouter"


def test_read_main_model_provider_use_override():
    aux.set_runtime_main("alibaba", "qwen-max")
    try:
        assert aux._read_main_provider() == "alibaba"
        assert aux._read_main_model() == "qwen-max"
    finally:
        aux.clear_runtime_main()


def test_resolve_provider_client_injects_runtime_main_kwarg(monkeypatch):
    """An explicit ``runtime_main`` is handed to ``_resolve_auto`` as the
    main_runtime dict — i.e. the DI path is wired ahead of the global."""
    captured = {}

    def fake_resolve_auto(main_runtime=None):
        captured["main_runtime"] = main_runtime
        return (None, None)

    monkeypatch.setattr(aux, "_resolve_auto", fake_resolve_auto)
    aux.clear_runtime_main()  # ensure the global is NOT the source here
    override = aux.RuntimeMainOverride(
        provider="nous", model="my-model", api_key="sk-nous"
    )
    aux.resolve_provider_client(provider="auto", runtime_main=override)
    assert captured["main_runtime"] == {
        "provider": "nous",
        "model": "my-model",
        "api_key": "sk-nous",
    }
    aux.clear_runtime_main()


def test_resolve_provider_client_global_fallback_when_no_kwarg(monkeypatch):
    """Without the kwarg, no dict is injected; the process-local global is the
    consulted default source (read inside ``_resolve_auto`` via
    ``get_runtime_main()``). Backward compatible with conversation_loop."""
    captured = {}

    def fake_resolve_auto(main_runtime=None):
        captured["main_runtime"] = main_runtime
        return (None, None)

    monkeypatch.setattr(aux, "_resolve_auto", fake_resolve_auto)
    aux.set_runtime_main("deepseek", "deepseek-chat", api_key="sk-ds")
    try:
        aux.resolve_provider_client(provider="auto")  # no runtime_main kwarg
        # no explicit dict injected when the kwarg is absent
        assert captured["main_runtime"] is None
        # the live global is the consulted default
        assert aux.get_runtime_main().provider == "deepseek"
    finally:
        aux.clear_runtime_main()
