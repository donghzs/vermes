"""Lightweight Pipeline / Step primitives for multi-stage agent workflows.

Design goals (from Route B-1 of Vermes Harness Baseline Audit):
- Declarative stage list with ordered execution
- Per-stage agent invocation with context passing
- Checkpoint support (pause between stages for user confirmation)
- SSE event emission for frontend consumption
- Fail-open: errors in one stage don't crash the pipeline
- Zero dependency on existing infra (no registry changes)

Usage:
    pipeline = Pipeline(stages=[
        Stage("topic", TopicAgent, label="选题分析"),
        Stage("literature", LiteratureAgent, label="文献综述"),
        ...
    ])
    async for event in pipeline.run(ctx, make_agent_fn, **kwargs):
        yield event  # SSE-ready dict
"""
from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

_VAR_KEYWORD = inspect.Parameter.VAR_KEYWORD
_KEYWORD_KINDS = (
    inspect.Parameter.POSITIONAL_OR_KEYWORD,
    inspect.Parameter.KEYWORD_ONLY,
)


def select_supported_kwargs(run_method: Any, kwargs: dict) -> dict:
    """Return the subset of ``kwargs`` that ``run_method`` can actually accept.

    Why this exists (P0): ``extra_kwargs`` is broadcast to *every* stage, but
    real agents have fixed signatures — only ``LiteratureAgent.run`` accepts
    ``depth``, only ``WritingAgent.run`` accepts ``section``. Handing an agent
    an unsupported kwarg raises ``TypeError``, which the fail-open handler below
    converts into a stage ``error`` event — silently killing the stage while the
    pipeline reports "complete". Filtering against the real signature keeps
    broadcast convenient *and* safe.

    Fail-open: if the signature cannot be introspected, everything is passed
    through so exotic callables keep their previous behaviour.
    """
    try:
        params = inspect.signature(run_method).parameters
    except (TypeError, ValueError):
        return dict(kwargs)

    # A ``**kwargs`` catch-all means the agent accepts anything.
    if any(p.kind is _VAR_KEYWORD for p in params.values()):
        return dict(kwargs)

    accepted = {
        name for name, p in params.items()
        if p.kind in _KEYWORD_KINDS and name != "self"
    }
    return {k: v for k, v in kwargs.items() if k in accepted}


# ── Event helpers ────────────────────────────────────────────────────

def stage_event(stage: str, status: str, **extra) -> dict:
    """Build a standard SSE-ready stage event."""
    evt = {"type": "stage", "stage": stage, "pipeline": status}
    evt.update(extra)
    return evt


def checkpoint_event(stage: str, next_stage: str, message: str,
                     completed: str, remaining: list[str]) -> dict:
    """Build a checkpoint event (pause between stages)."""
    return {
        "type": "checkpoint",
        "stage": stage,
        "next": next_stage,
        "message": message,
        "completed": completed,
        "remaining": remaining,
    }


def done_event(**extra) -> dict:
    """Build a pipeline-done event."""
    evt = {"type": "done", "pipeline": "complete"}
    evt.update(extra)
    return evt


def thinking_event(message: str) -> dict:
    """Build a thinking/status event."""
    return {"type": "thinking", "message": message}


# ── Data structures ──────────────────────────────────────────────────

@dataclass
class Stage:
    """A single pipeline stage.

    Attributes:
        name: Unique stage identifier (e.g. "topic", "literature").
        agent_cls: Agent class to instantiate for this stage.
            Called as ``agent_cls(ctx, llm)`` → must expose ``async def run(**kwargs)``.
        label: Human-readable label for UI display.
        section_kwarg: If set, pass ``req.section`` as this kwarg name.
        depth_kwarg: If set, pass ``req.depth`` as this kwarg name.
        post_hooks: Optional list of ``(ctx, pid) -> None`` callbacks run after stage.
    """
    name: str
    agent_cls: Any
    label: str = ""
    section_kwarg: Optional[str] = None
    depth_kwarg: Optional[str] = None
    post_hooks: list[Callable] = field(default_factory=list)


@dataclass
class PipelineConfig:
    """Configuration for a pipeline run.

    Attributes:
        checkpoint: If True, emit checkpoint events between stages.
        continue_from: Stage name to resume from (skip earlier stages).
    """
    checkpoint: bool = False
    continue_from: str = ""


# ── Pipeline runner ──────────────────────────────────────────────────

class Pipeline:
    """Ordered sequence of Stages with SSE event emission.

    The pipeline is a thin orchestrator: it manages stage iteration,
    event emission, and checkpoint logic, but does NOT own the context
    object or agent instantiation. Those are delegated to caller-provided
    callbacks, keeping the pipeline generic.
    """

    def __init__(self, stages: list[Stage]):
        self.stages = stages
        self._stage_names = [s.name for s in stages]

    @property
    def stage_names(self) -> list[str]:
        return list(self._stage_names)

    def get_start_index(self, continue_from: str) -> int:
        """Return the start index for a resume-from-stage run."""
        if continue_from and continue_from in self._stage_names:
            return self._stage_names.index(continue_from)
        return 0

    async def run(
        self,
        ctx: Any,
        config: PipelineConfig,
        make_agent: Callable[[Stage, Any], Awaitable[Any]],
        user_input: str = "",
        extra_kwargs: Optional[dict] = None,
        is_disconnected: Optional[Callable[[], Awaitable[bool]]] = None,
        stage_labels: Optional[dict[str, str]] = None,
    ) -> AsyncIterator[dict]:
        """Execute the pipeline, yielding SSE-ready event dicts.

        Args:
            ctx: Shared context object passed to each agent.
            config: Pipeline run configuration.
            make_agent: Async callable ``(stage, ctx) -> agent_instance``.
                The returned agent must have ``async def run(**kwargs)``.
            user_input: The original user message.
            extra_kwargs: Additional kwargs passed to every stage's run().
            is_disconnected: Optional async callable to check if client disconnected.
            stage_labels: Optional override for stage labels (name → label).

        Yields:
            SSE-ready dict events: stage start/done, checkpoint, thinking, done.
        """
        extra_kwargs = extra_kwargs or {}
        start_idx = self.get_start_index(config.continue_from)

        if config.continue_from and start_idx > 0:
            yield thinking_event(
                f"📍 从 {self._stage_names[start_idx]} 阶段继续..."
            )

        for stage_idx, stage in enumerate(self.stages):
            if stage_idx < start_idx:
                continue

            # Client disconnect check
            if is_disconnected:
                try:
                    if await is_disconnected():
                        logger.info(
                            "Pipeline: client disconnected at stage=%s",
                            stage.name,
                        )
                        return
                except Exception:
                    pass  # fail-open: don't crash on disconnect check

            # Stage start
            yield stage_event(stage.name, "start")

            # ── Route D metrics ──
            _stage_error = False

            # Instantiate and run agent
            try:
                agent = await make_agent(stage, ctx)
                kwargs = {"user_input": user_input, **extra_kwargs}
                # Declarative routing: an explicit value in extra_kwargs wins;
                # otherwise fall back to the context object (legacy path — note
                # ScholarForge's ProjectContext carries neither attribute, so
                # this fallback is a no-op there by design).
                if stage.section_kwarg and stage.section_kwarg not in kwargs:
                    _sec = getattr(ctx, "section", None)
                    if _sec is not None:
                        kwargs[stage.section_kwarg] = _sec
                if stage.depth_kwarg and stage.depth_kwarg not in kwargs:
                    _dep = getattr(ctx, "depth", None)
                    if _dep is not None:
                        kwargs[stage.depth_kwarg] = _dep

                # P0: never hand an agent a kwarg its run() cannot accept —
                # a TypeError here would be swallowed into a stage error below.
                safe_kwargs = select_supported_kwargs(agent.run, kwargs)
                if len(safe_kwargs) != len(kwargs):
                    logger.debug(
                        "Pipeline: stage=%s dropped unsupported kwargs %s",
                        stage.name,
                        sorted(set(kwargs) - set(safe_kwargs)),
                    )

                async for evt in agent.run(**safe_kwargs):
                    yield evt
            except Exception as exc:
                _stage_error = True
                logger.warning(
                    "Pipeline: stage=%s failed: %s", stage.name, exc
                )
                yield stage_event(
                    stage.name, "error", message=str(exc)
                )
                # fail-open: continue to next stage

            try:
                from agent.metrics import record_pipeline_stage
                record_pipeline_stage(stage.name, error=_stage_error)
            except Exception:
                pass  # metrics are best-effort

            # Stage done
            papers_count = len(getattr(ctx, "papers", []))
            yield stage_event(stage.name, "done", papers=papers_count)

            # Post-stage hooks
            for hook in stage.post_hooks:
                try:
                    hook(ctx, stage.name)
                except Exception as exc:
                    logger.debug(
                        "Pipeline: post-hook for stage=%s failed: %s",
                        stage.name, exc,
                    )

            # Checkpoint between stages (not after last)
            if config.checkpoint and stage_idx < len(self.stages) - 1:
                next_stage = self.stages[stage_idx + 1]
                labels = stage_labels or {}
                yield checkpoint_event(
                    stage=stage.name,
                    next_stage=next_stage.name,
                    message=(
                        f"{labels.get(stage.name, stage.label or stage.name)}"
                        f"完成，是否继续{labels.get(next_stage.name, next_stage.label or next_stage.name)}？"
                    ),
                    completed=stage.name,
                    remaining=self._stage_names[stage_idx + 1:],
                )

        # Pipeline complete
        yield done_event(
            papers=len(getattr(ctx, "papers", [])),
        )


__all__ = [
    "Pipeline",
    "PipelineConfig",
    "Stage",
    "select_supported_kwargs",
    "stage_event",
    "checkpoint_event",
    "done_event",
    "thinking_event",
]
