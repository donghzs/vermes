"""B-1: Pipeline / Step abstraction tests.

Tests cover:
- Stage registration and ordering
- Pipeline run with multiple stages (events emitted correctly)
- Checkpoint between stages
- continue_from (resume from specific stage)
- Stage failure fail-open (continues to next stage)
- Client disconnect detection
- Post-stage hooks
- Extra kwargs and section/depth passthrough
- Empty pipeline
- stage_labels override
"""
import asyncio
import pytest

from agent.pipeline import (
    Pipeline,
    PipelineConfig,
    Stage,
    stage_event,
    checkpoint_event,
    done_event,
    thinking_event,
)


# ── Test fixtures ────────────────────────────────────────────────────

class FakeCtx:
    """Minimal context object for testing."""
    def __init__(self, papers=None, section=None, depth=None):
        self.papers = papers or []
        self.section = section
        self.depth = depth
        self.draft = ""
        self.topic = ""


class FakeAgent:
    """Agent that yields a fixed set of events."""
    def __init__(self, ctx, llm=None):
        self.ctx = ctx
        self.llm = llm

    async def run(self, **kwargs):
        yield {"type": "thinking", "message": f"running with input={kwargs.get('user_input', '')}"}
        yield {"type": "content", "content": "some content"}


class FailingAgent:
    """Agent that raises during run."""
    async def run(self, **kwargs):
        raise RuntimeError("agent crashed")
        yield  # never reached


async def _make_agent(stage, ctx):
    """Default make_agent callback for tests."""
    return stage.agent_cls(ctx, None)


# ── Event helper tests ───────────────────────────────────────────────

class TestEventHelpers:

    def test_stage_event_start(self):
        evt = stage_event("topic", "start")
        assert evt == {"type": "stage", "stage": "topic", "pipeline": "start"}

    def test_stage_event_done_with_extra(self):
        evt = stage_event("topic", "done", papers=5)
        assert evt["type"] == "stage"
        assert evt["stage"] == "topic"
        assert evt["pipeline"] == "done"
        assert evt["papers"] == 5

    def test_checkpoint_event(self):
        evt = checkpoint_event(
            stage="topic", next_stage="literature",
            message="done?", completed="topic", remaining=["literature", "outline"],
        )
        assert evt["type"] == "checkpoint"
        assert evt["next"] == "literature"
        assert evt["remaining"] == ["literature", "outline"]

    def test_done_event(self):
        evt = done_event(papers=3)
        assert evt["type"] == "done"
        assert evt["pipeline"] == "complete"
        assert evt["papers"] == 3

    def test_thinking_event(self):
        evt = thinking_event("processing...")
        assert evt == {"type": "thinking", "message": "processing..."}


# ── Stage dataclass tests ────────────────────────────────────────────

class TestStage:

    def test_defaults(self):
        s = Stage("topic", FakeAgent)
        assert s.name == "topic"
        assert s.agent_cls is FakeAgent
        assert s.label == ""
        assert s.post_hooks == []

    def test_with_label(self):
        s = Stage("topic", FakeAgent, label="选题分析")
        assert s.label == "选题分析"


# ── Pipeline basic tests ─────────────────────────────────────────────

class TestPipelineBasics:

    def test_stage_names(self):
        p = Pipeline([
            Stage("topic", FakeAgent),
            Stage("literature", FakeAgent),
        ])
        assert p.stage_names == ["topic", "literature"]

    def test_empty_pipeline(self):
        p = Pipeline([])
        assert p.stage_names == []

    def test_get_start_index_no_continue(self):
        p = Pipeline([Stage("a", FakeAgent), Stage("b", FakeAgent)])
        assert p.get_start_index("") == 0

    def test_get_start_index_with_continue(self):
        p = Pipeline([Stage("a", FakeAgent), Stage("b", FakeAgent), Stage("c", FakeAgent)])
        assert p.get_start_index("b") == 1

    def test_get_start_index_invalid_stage(self):
        p = Pipeline([Stage("a", FakeAgent)])
        assert p.get_start_index("nonexistent") == 0


# ── Pipeline run tests ───────────────────────────────────────────────

class TestPipelineRun:

    @pytest.mark.asyncio
    async def test_basic_run_two_stages(self):
        p = Pipeline([
            Stage("topic", FakeAgent),
            Stage("literature", FakeAgent),
        ])
        ctx = FakeCtx()
        events = []
        async for evt in p.run(ctx, PipelineConfig(), _make_agent, user_input="test"):
            events.append(evt)

        # Should have: stage start, 2 agent events, stage done, stage start, 2 agent events, stage done, done
        stage_starts = [e for e in events if e.get("type") == "stage" and e.get("pipeline") == "start"]
        stage_dones = [e for e in events if e.get("type") == "stage" and e.get("pipeline") == "done"]
        dones = [e for e in events if e.get("type") == "done"]

        assert len(stage_starts) == 2
        assert len(stage_dones) == 2
        assert len(dones) == 1
        assert stage_starts[0]["stage"] == "topic"
        assert stage_starts[1]["stage"] == "literature"

    @pytest.mark.asyncio
    async def test_checkpoint_between_stages(self):
        p = Pipeline([
            Stage("topic", FakeAgent, label="选题分析"),
            Stage("literature", FakeAgent, label="文献综述"),
        ])
        ctx = FakeCtx()
        config = PipelineConfig(checkpoint=True)
        events = []
        async for evt in p.run(ctx, config, _make_agent):
            events.append(evt)

        checkpoints = [e for e in events if e.get("type") == "checkpoint"]
        assert len(checkpoints) == 1  # not after last stage
        assert checkpoints[0]["stage"] == "topic"
        assert checkpoints[0]["next"] == "literature"
        assert "选题分析" in checkpoints[0]["message"]

    @pytest.mark.asyncio
    async def test_no_checkpoint_after_last_stage(self):
        p = Pipeline([Stage("only", FakeAgent)])
        ctx = FakeCtx()
        config = PipelineConfig(checkpoint=True)
        events = []
        async for evt in p.run(ctx, config, _make_agent):
            events.append(evt)
        checkpoints = [e for e in events if e.get("type") == "checkpoint"]
        assert len(checkpoints) == 0

    @pytest.mark.asyncio
    async def test_continue_from_stage(self):
        p = Pipeline([
            Stage("topic", FakeAgent),
            Stage("literature", FakeAgent),
            Stage("outline", FakeAgent),
        ])
        ctx = FakeCtx()
        config = PipelineConfig(continue_from="literature")
        events = []
        async for evt in p.run(ctx, config, _make_agent):
            events.append(evt)

        thinking = [e for e in events if e.get("type") == "thinking"]
        assert any("继续" in t["message"] for t in thinking)

        stage_starts = [e for e in events if e.get("type") == "stage" and e.get("pipeline") == "start"]
        assert len(stage_starts) == 2  # literature + outline, topic skipped
        assert stage_starts[0]["stage"] == "literature"

    @pytest.mark.asyncio
    async def test_stage_failure_fail_open(self):
        p = Pipeline([
            Stage("topic", FailingAgent),
            Stage("literature", FakeAgent),
        ])
        ctx = FakeCtx()
        events = []
        async for evt in p.run(ctx, PipelineConfig(), _make_agent):
            events.append(evt)

        # First stage should have error event, second should still run
        errors = [e for e in events if e.get("pipeline") == "error"]
        assert len(errors) == 1
        assert errors[0]["stage"] == "topic"

        stage_starts = [e for e in events if e.get("type") == "stage" and e.get("pipeline") == "start"]
        assert len(stage_starts) == 2  # both stages ran

    @pytest.mark.asyncio
    async def test_client_disconnect_stops_pipeline(self):
        p = Pipeline([
            Stage("topic", FakeAgent),
            Stage("literature", FakeAgent),
        ])
        ctx = FakeCtx()

        disconnected = False
        async def check_disconnect():
            return disconnected

        events = []
        async for evt in p.run(
            ctx, PipelineConfig(), _make_agent,
            is_disconnected=check_disconnect,
        ):
            events.append(evt)
            # After first stage, mark as disconnected
            if evt.get("pipeline") == "done" and evt.get("stage") == "topic":
                disconnected = True

        stage_starts = [e for e in events if e.get("type") == "stage" and e.get("pipeline") == "start"]
        assert len(stage_starts) == 1  # only topic, literature skipped

    @pytest.mark.asyncio
    async def test_post_stage_hooks(self):
        hook_calls = []
        def hook(ctx, stage_name):
            hook_calls.append(stage_name)

        p = Pipeline([
            Stage("topic", FakeAgent, post_hooks=[hook]),
            Stage("literature", FakeAgent, post_hooks=[hook]),
        ])
        ctx = FakeCtx()
        events = []
        async for evt in p.run(ctx, PipelineConfig(), _make_agent):
            events.append(evt)

        assert hook_calls == ["topic", "literature"]

    @pytest.mark.asyncio
    async def test_post_stage_hook_failure_doesnt_crash(self):
        def bad_hook(ctx, stage_name):
            raise RuntimeError("hook failed")

        p = Pipeline([
            Stage("topic", FakeAgent, post_hooks=[bad_hook]),
            Stage("literature", FakeAgent),
        ])
        ctx = FakeCtx()
        events = []
        async for evt in p.run(ctx, PipelineConfig(), _make_agent):
            events.append(evt)

        # Pipeline should still complete
        dones = [e for e in events if e.get("type") == "done"]
        assert len(dones) == 1

    @pytest.mark.asyncio
    async def test_extra_kwargs_passed_to_agent(self):
        received_kwargs = {}

        class KwargCaptureAgent:
            def __init__(self, ctx=None, llm=None):
                pass
            async def run(self, **kwargs):
                received_kwargs.update(kwargs)
                yield {"type": "content", "content": "ok"}

        p = Pipeline([Stage("topic", KwargCaptureAgent)])
        ctx = FakeCtx()
        events = []
        async for evt in p.run(
            ctx, PipelineConfig(), _make_agent,
            user_input="hello",
            extra_kwargs={"custom_param": 42},
        ):
            events.append(evt)

        assert received_kwargs["user_input"] == "hello"
        assert received_kwargs["custom_param"] == 42

    @pytest.mark.asyncio
    async def test_section_kwarg_passed_through(self):
        received_kwargs = {}

        class CaptureAgent:
            def __init__(self, ctx=None, llm=None):
                pass
            async def run(self, **kwargs):
                received_kwargs.update(kwargs)
                yield {"type": "content", "content": "ok"}

        p = Pipeline([
            Stage("writing", CaptureAgent, section_kwarg="section"),
        ])
        ctx = FakeCtx(section="introduction")
        events = []
        async for evt in p.run(ctx, PipelineConfig(), _make_agent):
            events.append(evt)

        assert received_kwargs["section"] == "introduction"

    @pytest.mark.asyncio
    async def test_depth_kwarg_passed_through(self):
        received_kwargs = {}

        class CaptureAgent:
            def __init__(self, ctx=None, llm=None):
                pass
            async def run(self, **kwargs):
                received_kwargs.update(kwargs)
                yield {"type": "content", "content": "ok"}

        p = Pipeline([
            Stage("literature", CaptureAgent, depth_kwarg="depth"),
        ])
        ctx = FakeCtx(depth=5)
        events = []
        async for evt in p.run(ctx, PipelineConfig(), _make_agent):
            events.append(evt)

        assert received_kwargs["depth"] == 5

    @pytest.mark.asyncio
    async def test_papers_count_in_done_event(self):
        p = Pipeline([Stage("topic", FakeAgent)])
        ctx = FakeCtx(papers=[{"id": 1}, {"id": 2}, {"id": 3}])
        events = []
        async for evt in p.run(ctx, PipelineConfig(), _make_agent):
            events.append(evt)

        done = [e for e in events if e.get("type") == "done"]
        assert done[0]["papers"] == 3

    @pytest.mark.asyncio
    async def test_stage_labels_override(self):
        p = Pipeline([
            Stage("topic", FakeAgent),
            Stage("literature", FakeAgent),
        ])
        ctx = FakeCtx()
        config = PipelineConfig(checkpoint=True)
        events = []
        async for evt in p.run(
            ctx, config, _make_agent,
            stage_labels={"topic": "T1", "literature": "L2"},
        ):
            events.append(evt)

        checkpoints = [e for e in events if e.get("type") == "checkpoint"]
        assert "T1" in checkpoints[0]["message"]
        assert "L2" in checkpoints[0]["message"]

    @pytest.mark.asyncio
    async def test_empty_pipeline_yields_done(self):
        p = Pipeline([])
        ctx = FakeCtx()
        events = []
        async for evt in p.run(ctx, PipelineConfig(), _make_agent):
            events.append(evt)
        dones = [e for e in events if e.get("type") == "done"]
        assert len(dones) == 1
