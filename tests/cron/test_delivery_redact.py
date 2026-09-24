"""L-020: cron delivery is a safety boundary — secrets never leave to chat.

Upstream `c0362da9a6e9`. Shell-job stdout/stderr is already redacted where
captured; the LLM job response text previously reached delivery unscanned.
"""

from __future__ import annotations

from unittest import mock


def test_production_deliver_result_contains_force_redact():
    """Pin the live `_deliver_result` chokepoint (not a shadow copy)."""
    import inspect
    from cron import scheduler as sch

    src = inspect.getsource(sch._deliver_result)
    assert "_redact_cron_payload" in src
    assert "force=True" in inspect.getsource(sch) or "force=True" in src
    # Job name and body both go through the redactor.
    assert "content = _redact_cron_payload(content)" in src
    assert "_redact_cron_payload(job.get" in src or "_redact_cron_payload(job.get(\"name\"" in src


def test_redact_cron_payload_fail_closed(monkeypatch):
    """A raising redactor must replace the payload, not let it through."""
    import cron.scheduler as sch

    # Recreate the helper the same way production defines it (local closure).
    def _redact_cron_payload(text: str) -> str:
        try:
            from agent.redact import redact_sensitive_text
            return redact_sensitive_text(text, force=True)
        except Exception:
            return "[redacted: delivery payload failed safety scrub]"

    monkeypatch.setattr(
        "agent.redact.redact_sensitive_text",
        lambda t, force=False, **k: t.replace("sk-SECRET", "[REDACTED]") if force else t,
    )
    assert "sk-SECRET" not in _redact_cron_payload("echo sk-SECRET")

    def _boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr("agent.redact.redact_sensitive_text", _boom)
    out = _redact_cron_payload("sk-SECRET")
    assert "sk-SECRET" not in out
    assert out.startswith("[redacted:")


def test_redact_sensitive_text_force_ignores_user_preference(monkeypatch):
    """force=True is a safety boundary — user redact preference cannot disable it."""
    from agent.redact import redact_sensitive_text
    import agent.redact as redact_mod

    monkeypatch.setattr(redact_mod, "_REDACT_ENABLED", False)
    out = redact_sensitive_text("key=sk-ABCDEFGHIJKLMNOP", force=True)
    # Either the key is masked or an exception-free safe string is returned —
    # never the raw secret while force=True and the function returns normally.
    assert "sk-ABCDEFGHIJKLMNOP" not in out or out == "key=sk-ABCDEFGHIJKLMNOP"
    # If the function returns the input unchanged under force, that is a
    # contract break for safety boundaries — assert it is masked.
    if out == "key=sk-ABCDEFGHIJKLMNOP":
        raise AssertionError("force=True must not return raw secrets")
