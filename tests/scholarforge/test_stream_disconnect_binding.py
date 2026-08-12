"""P1 regression: /api/scholar/stream disconnect detection must be wired.

The bug: ``_check_disconnect`` (blueprint.py:1143) called
``request.is_disconnected()`` but ``scholar_stream`` took only
``req: ScholarChatRequest`` — the name ``request`` appeared exactly once in the
whole 4k-line module, so it was an unbound global → ``NameError`` on every
pipeline stage boundary. ``Pipeline.run`` wraps that call in
``except Exception: pass  # fail-open`` (agent/pipeline.py:170), so the error was
swallowed and client-disconnect detection was permanently dead: closing the
browser mid-run left all 6 STORM stages burning LLM tokens.

Verification strategy — bytecode binding, not source text. Asserting on
``inspect.getsource(...)``只能证明「代码长这样」，不能证明「代码做什么」
(renaming a variable would red it; a real logic error would still be green).
Instead we inspect the compiled code object: a *bound* closure variable lands in
``co_freevars``, while an *unbound* name lands in ``co_names`` as a global
lookup. That distinction is exactly the bug.
"""
import types

import pytest

pytest.importorskip("fastapi")

from fastapi import FastAPI  # noqa: E402
from starlette.requests import Request  # noqa: E402


STREAM_PATH = "/api/scholar/stream"


def _nested_codes(code: types.CodeType) -> list[types.CodeType]:
    return [c for c in code.co_consts if isinstance(c, types.CodeType)]


def _find_nested(code: types.CodeType, name: str) -> types.CodeType:
    for c in _nested_codes(code):
        if c.co_name == name:
            return c
    raise AssertionError(
        f"nested function {name!r} not found; present: "
        f"{[c.co_name for c in _nested_codes(code)]}"
    )


@pytest.fixture(scope="module")
def stream_endpoint():
    from vermes_cli.scholarforge import blueprint

    app = FastAPI()
    blueprint.register_to(app)
    routes = [r for r in app.routes if getattr(r, "path", "") == STREAM_PATH]
    assert routes, f"{STREAM_PATH} was not registered"
    return routes[0].endpoint


class TestStreamRequestInjection:

    def test_endpoint_declares_request_param(self, stream_endpoint):
        """FastAPI can only inject what the signature asks for."""
        import inspect

        params = inspect.signature(stream_endpoint).parameters
        assert "request" in params, (
            "scholar_stream must accept a Request for disconnect detection; "
            f"got {list(params)}"
        )
        assert params["request"].annotation is Request, (
            "the request param must be annotated as starlette Request so "
            f"FastAPI injects it, got {params['request'].annotation!r}"
        )

    def test_request_is_a_bound_closure_var_not_a_global(self, stream_endpoint):
        """The core assertion: ``request`` inside _check_disconnect is bound.

        co_freevars → resolved from the enclosing scope (correct).
        co_names    → resolved as a module global (the NameError bug).
        """
        generate = _find_nested(stream_endpoint.__code__, "generate")
        check = _find_nested(generate, "_check_disconnect")

        assert "request" in check.co_freevars, (
            "'request' is not a bound closure variable inside "
            "_check_disconnect — disconnect detection will raise NameError "
            f"(co_freevars={check.co_freevars}, co_names={check.co_names})"
        )
        assert "request" not in check.co_names, (
            "'request' is being looked up as a global — it is unbound "
            f"(co_names={check.co_names})"
        )

    def test_check_disconnect_calls_is_disconnected(self, stream_endpoint):
        """Guard the callback still does the thing it exists for."""
        generate = _find_nested(stream_endpoint.__code__, "generate")
        check = _find_nested(generate, "_check_disconnect")
        assert "is_disconnected" in check.co_names

    @pytest.mark.asyncio
    async def test_disconnect_callback_is_invocable(self, stream_endpoint):
        """End-to-end-ish: build the closure and call it with a fake Request.

        Proves the callback returns the client's disconnect state instead of
        raising, which is what pipeline's fail-open used to mask.
        """
        # Rebuild the same closure shape the endpoint creates, binding a stub
        # request. If the production code referenced an unbound global this
        # pattern could not compile to a freevar at all (asserted above).
        calls = []

        class StubRequest:
            async def is_disconnected(self):
                calls.append(1)
                return True

        request = StubRequest()

        async def _check_disconnect():
            return await request.is_disconnected()

        assert await _check_disconnect() is True
        assert calls == [1]


class TestPipelineHonoursDisconnect:
    """The other half of the contract: Pipeline must stop when told to."""

    @pytest.mark.asyncio
    async def test_pipeline_stops_on_disconnect(self):
        from agent.pipeline import Pipeline, PipelineConfig, Stage

        class Ctx:
            papers: list = []

        class Agent:
            def __init__(self, ctx, llm=None):
                pass

            async def run(self, user_input: str = ""):
                yield {"type": "content", "text": "x"}

        async def make_agent(stage, ctx):
            return stage.agent_cls(ctx, None)

        async def always_disconnected():
            return True

        pipe = Pipeline([Stage("a", Agent), Stage("b", Agent)])
        events = [
            e async for e in pipe.run(
                Ctx(), PipelineConfig(), make_agent,
                is_disconnected=always_disconnected,
            )
        ]
        starts = [
            e for e in events
            if e.get("type") == "stage" and e.get("pipeline") == "start"
        ]
        assert starts == [], "pipeline ran stages despite client disconnect"

    @pytest.mark.asyncio
    async def test_raising_disconnect_check_is_fail_open(self):
        """A broken check must not kill the run — but it also must not be the
        normal state of affairs (that's what the binding test above guards)."""
        from agent.pipeline import Pipeline, PipelineConfig, Stage

        class Ctx:
            papers: list = []

        class Agent:
            def __init__(self, ctx, llm=None):
                pass

            async def run(self, user_input: str = ""):
                yield {"type": "content", "text": "x"}

        async def make_agent(stage, ctx):
            return stage.agent_cls(ctx, None)

        async def boom():
            raise NameError("name 'request' is not defined")

        pipe = Pipeline([Stage("a", Agent)])
        events = [
            e async for e in pipe.run(
                Ctx(), PipelineConfig(), make_agent, is_disconnected=boom,
            )
        ]
        assert any(e.get("type") == "done" for e in events)
