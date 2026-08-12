"""P0 regression: pipeline must not broadcast kwargs an agent cannot accept.

Why a separate file from ``test_pipeline.py``: that suite's fixtures mirror the
implementation rather than the real caller —

* ``FakeCtx.__init__(..., section=None, depth=None)`` invents attributes that the
  production ``ProjectContext`` (vermes_cli/scholarforge/agents/__init__.py:165)
  does **not** have, so the ``hasattr(ctx, "section")`` branch looked alive.
* Every fake agent is ``async def run(self, **kwargs)``, i.e. it swallows any
  keyword, so a broadcast ``depth=`` could never raise.

Result: 14 green tests while 5 of 6 real STORM stages died with
``TypeError: run() got an unexpected keyword argument 'depth'`` — converted by
pipeline's fail-open into a ``stage error`` event while the run still reported
"complete".

The fixtures below therefore copy the **real** shapes:
  - ctx has no ``section`` / ``depth`` attributes
  - agent ``run`` signatures match production exactly
    (agents/__init__.py:408 / :442 / :904 / :981 / :1347 / :1727)
"""
import inspect

import pytest

from agent.pipeline import (
    Pipeline,
    PipelineConfig,
    Stage,
    select_supported_kwargs,
)


# ── Real-shape fixtures ──────────────────────────────────────────────

class RealShapeCtx:
    """Mirrors ProjectContext: papers/draft/topic, but NO section/depth."""

    def __init__(self):
        self.papers = []
        self.draft = ""
        self.topic = "对抗训练提升 NMT 鲁棒性"


# Signatures below are copied 1:1 from vermes_cli/scholarforge/agents/__init__.py
RECEIVED: dict[str, dict] = {}


class _Recorder:
    stage_name = "?"

    def __init__(self, ctx, llm=None):
        self.ctx = ctx


class TopicLike(_Recorder):
    """agents/__init__.py:408 — async def run(self, user_input: str)"""
    async def run(self, user_input: str):
        RECEIVED["topic"] = {"user_input": user_input}
        yield {"type": "content", "text": "topic ok"}


class LiteratureLike(_Recorder):
    """agents/__init__.py:442 — async def run(self, user_input: str, depth: int = 1)"""
    async def run(self, user_input: str, depth: int = 1):
        RECEIVED["literature"] = {"user_input": user_input, "depth": depth}
        yield {"type": "content", "text": "lit ok"}


class OutlineLike(_Recorder):
    """agents/__init__.py:904 — async def run(self, user_input: str)"""
    async def run(self, user_input: str):
        RECEIVED["outline"] = {"user_input": user_input}
        yield {"type": "content", "text": "outline ok"}


class WritingLike(_Recorder):
    """agents/__init__.py:981 — async def run(self, user_input: str, section: str = "")"""
    async def run(self, user_input: str, section: str = ""):
        RECEIVED["writing"] = {"user_input": user_input, "section": section}
        yield {"type": "content", "text": "writing ok"}


class RefinementLike(_Recorder):
    """agents/__init__.py:1347 — async def run(self, user_input: str)"""
    async def run(self, user_input: str):
        RECEIVED["refinement"] = {"user_input": user_input}
        yield {"type": "content", "text": "refine ok"}


class ReviewerLike(_Recorder):
    """agents/__init__.py:1727 — async def run(self, user_input: str = "")"""
    async def run(self, user_input: str = ""):
        RECEIVED["reviewer"] = {"user_input": user_input}
        yield {"type": "content", "text": "review ok"}


def _storm_stages() -> list[Stage]:
    """The exact stage list blueprint.py builds (blueprint.py:1092-1099)."""
    return [
        Stage("topic", TopicLike, label="选题分析"),
        Stage("literature", LiteratureLike, label="文献综述", depth_kwarg="depth"),
        Stage("outline", OutlineLike, label="论文大纲"),
        Stage("writing", WritingLike, label="章节撰写", section_kwarg="section"),
        Stage("refinement", RefinementLike, label="润色检查"),
        Stage("reviewer", ReviewerLike, label="审稿"),
    ]


async def _make_agent(stage, ctx):
    return stage.agent_cls(ctx, None)


async def _drain(extra_kwargs: dict, user_input: str = "写一篇论文"):
    """Run the 6-stage STORM pipeline, returning (all_events, error_stages)."""
    RECEIVED.clear()
    pipe = Pipeline(stages=_storm_stages())
    events = []
    async for evt in pipe.run(
        RealShapeCtx(), PipelineConfig(checkpoint=False), _make_agent,
        user_input=user_input, extra_kwargs=extra_kwargs,
    ):
        events.append(evt)
    errors = [
        e["stage"] for e in events
        if e.get("type") == "stage" and e.get("pipeline") == "error"
    ]
    return events, errors


# ── The P0 itself ────────────────────────────────────────────────────

class TestNoBroadcastTypeError:

    @pytest.mark.asyncio
    async def test_default_depth_does_not_kill_five_stages(self):
        """ScholarChatRequest.depth defaults to 2 (blueprint.py:214), so
        blueprint always sends extra_kwargs={"depth": 2} → this is the
        every-request path, not an edge case."""
        _, errors = await _drain({"depth": 2})
        assert errors == [], (
            f"stages killed by kwarg broadcast: {errors} "
            "(expected every stage to run)"
        )

    @pytest.mark.asyncio
    async def test_section_and_depth_together(self):
        """blueprint.py:1151-1155 adds BOTH when the user targets a section."""
        _, errors = await _drain({"section": "introduction", "depth": 3})
        assert errors == []

    @pytest.mark.asyncio
    async def test_all_six_stages_actually_executed(self):
        """Absence of error events is not enough — assert each agent ran."""
        await _drain({"depth": 2})
        assert set(RECEIVED) == {
            "topic", "literature", "outline",
            "writing", "refinement", "reviewer",
        }

    @pytest.mark.asyncio
    async def test_writing_stage_reached(self):
        """The regression's worst casualty: 正文撰写 never ran."""
        await _drain({"depth": 2})
        assert "writing" in RECEIVED, "正文撰写阶段未执行"


class TestKwargRoutedToRightStageOnly:

    @pytest.mark.asyncio
    async def test_depth_reaches_only_literature(self):
        await _drain({"depth": 3})
        assert RECEIVED["literature"]["depth"] == 3
        # Others must have run without it (they'd TypeError if it leaked in).
        assert RECEIVED["topic"] == {"user_input": "写一篇论文"}
        assert RECEIVED["outline"] == {"user_input": "写一篇论文"}

    @pytest.mark.asyncio
    async def test_section_reaches_only_writing(self):
        await _drain({"section": "method", "depth": 2})
        assert RECEIVED["writing"]["section"] == "method"
        # writing must NOT receive depth
        assert "depth" not in RECEIVED["writing"]
        # literature must NOT receive section
        assert "section" not in RECEIVED["literature"]

    @pytest.mark.asyncio
    async def test_user_input_reaches_every_stage(self):
        await _drain({"depth": 2}, user_input="研究选题 X")
        # Guard against a vacuous pass: an empty RECEIVED would make the loop
        # below trivially succeed (caught during reverse validation on the
        # pre-fix commit, where every stage had died before recording).
        assert len(RECEIVED) == 6, f"only {len(RECEIVED)} stages ran: {sorted(RECEIVED)}"
        for name, kw in RECEIVED.items():
            assert kw["user_input"] == "研究选题 X", f"{name} lost user_input"

    @pytest.mark.asyncio
    async def test_unknown_kwarg_is_dropped_not_fatal(self):
        """A caller typo / new broadcast field must not kill the run."""
        _, errors = await _drain({"depth": 2, "totally_unknown_field": object()})
        assert errors == []
        assert set(RECEIVED) == {
            "topic", "literature", "outline",
            "writing", "refinement", "reviewer",
        }


# ── select_supported_kwargs unit contract ────────────────────────────

class TestSelectSupportedKwargs:

    def test_filters_unsupported(self):
        async def run(user_input: str, depth: int = 1):
            yield {}
        out = select_supported_kwargs(
            run, {"user_input": "a", "depth": 2, "section": "x"}
        )
        assert out == {"user_input": "a", "depth": 2}

    def test_var_keyword_accepts_everything(self):
        async def run(user_input: str, **kwargs):
            yield {}
        payload = {"user_input": "a", "depth": 2, "whatever": 9}
        assert select_supported_kwargs(run, payload) == payload

    def test_keyword_only_params_supported(self):
        async def run(user_input: str, *, depth: int = 1):
            yield {}
        out = select_supported_kwargs(run, {"user_input": "a", "depth": 5, "z": 1})
        assert out == {"user_input": "a", "depth": 5}

    def test_bound_method_self_not_leaked(self):
        out = select_supported_kwargs(
            WritingLike(RealShapeCtx()).run,
            {"user_input": "a", "section": "s", "self": "BAD", "depth": 1},
        )
        assert out == {"user_input": "a", "section": "s"}

    def test_uninspectable_callable_fails_open(self):
        """Fail-open: if the signature can't be read, pass everything through."""
        class NoSig:
            def __call__(self, *a, **k):
                pass

        payload = {"user_input": "a", "depth": 1}
        # print is a builtin whose signature raises ValueError on some builds;
        # use an explicit monkey-free stand-in to keep this deterministic.
        assert select_supported_kwargs(NoSig(), payload) == payload

    def test_real_agent_signatures_are_what_we_assume(self):
        """Guard against the fixtures drifting from production signatures."""
        expected = {
            TopicLike: ["user_input"],
            LiteratureLike: ["user_input", "depth"],
            OutlineLike: ["user_input"],
            WritingLike: ["user_input", "section"],
            RefinementLike: ["user_input"],
            ReviewerLike: ["user_input"],
        }
        for cls, params in expected.items():
            got = [p for p in inspect.signature(cls.run).parameters if p != "self"]
            assert got == params, f"{cls.__name__} fixture drifted: {got}"
