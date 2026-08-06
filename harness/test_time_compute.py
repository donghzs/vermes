"""Test-Time Compute Scaling (best-of-N) — sampling framework + Judge protocol.

Design (decided with the team, see report §9.8 / §9.9):

This module deliberately contains **no text-quality judge logic**. It provides
only two things:

1. A **sampling framework**: clone ``api_kwargs``, override ``temperature``,
   fire N independent non-streaming calls, collect candidates.
2. An **OutcomeJudge protocol** — ``judge(candidates, context) -> (winner_idx,
   reasoning)`` — with a conservative default implementation that is a *filter*,
   not a judge.

Why a filter and not a "smart" judge? Because a temp heuristic like "pick the
longest" or "pick the one without ❌" would be **self-certifying** — exactly the
"silent false success" trap P0 spent two steps breaking. The real text-quality
judge is the P2 task-level Critic; when it lands, it swaps in as a new
``OutcomeJudge`` implementation and P1 needs **zero** code changes.

Safety scope (the "scholarforge" / conversation-loop contract):
- best-of-N runs **only on the non-streaming seam** for a **final-answer step**
  (``finish_reason == "stop"`` AND no ``tool_calls``). A tool-call step is never
  sampled — executing N tool plans would mean N× DB writes / API side effects.
- When ``enabled`` is False or ``n <= 1``, behavior is byte-for-byte identical to
  the baseline single call (zero-cost degrade).
- Every failure path (config error, call error, judge error) degrades to the
  first candidate — fail-open, never blocks the agent loop.

Token accounting (R4, v1): we only *observe* — sum prompt+completion tokens
across the N calls and log them. No auto-throttling yet; throttle after we have
data.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, List, Optional, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# ─── Config ───────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TTCConfig:
    enabled: bool = False
    n: int = 1
    temperature: float = 0.7
    judge: str = "default"          # "default" = DefaultJudge (filter); "critic" = CriticJudge (P2 LLM)
    critic_model: Optional[str] = None  # P2: 指定更强/不同法官模型；None = 复用主对话 provider


def _config_path():
    """Locate config.yaml, fail-open to ~/.vermes/config.yaml."""
    try:
        from vermes_constants import get_config_path
        return get_config_path()
    except Exception:
        from pathlib import Path
        return Path.home() / ".vermes" / "config.yaml"


def load_test_time_config() -> TTCConfig:
    """Read ``test_time_compute:`` from config.yaml. Any error → defaults.

    Defaults: enabled=False, n=1 → zero-cost, opt-in only.
    """
    try:
        import yaml  # fail-open if pyyaml missing
        path = _config_path()
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        ttc = raw.get("test_time_compute") or {}
        if not isinstance(ttc, dict):
            ttc = {}
        return TTCConfig(
            enabled=bool(ttc.get("enabled", False)),
            n=int(ttc.get("n", 1)) if ttc.get("n") is not None else 1,
            temperature=float(ttc.get("temperature", 0.7))
            if ttc.get("temperature") is not None else 0.7,
            judge=str(ttc.get("judge", "default")) if ttc.get("judge") is not None else "default",
            critic_model=str(ttc["critic_model"]) if ttc.get("critic_model") else None,
        )
    except Exception as e:  # noqa: BLE001 - fail-open to defaults
        logger.debug("test_time_compute config load failed, using defaults: %s", e)
        return TTCConfig()


def get_test_time_config(agent: Any) -> TTCConfig:
    """Cached per-agent config read (config is observe-only, no live reload in v1)."""
    cached = getattr(agent, "_ttc_config", None)
    if cached is not None:
        return cached
    cfg = load_test_time_config()
    try:
        agent._ttc_config = cfg
    except Exception:
        pass
    return cfg


# ─── Candidate + Judge protocol ──────────────────────────────────────────────

@dataclass
class Candidate:
    index: int
    content: str
    finish_reason: Optional[str]
    has_tool_calls: bool
    raw: Any  # the original response object (consumed identically downstream)
    tool_verify: List[dict] = field(default_factory=list)  # P0-A Verifier signal (input, not judge)


@runtime_checkable
class OutcomeJudge(Protocol):
    def judge(self, candidates: List[Candidate], context: dict) -> tuple[int, str]:
        """Return (winner_idx, reasoning). Must be fail-open: never raise."""
        ...


class DefaultJudge:
    """Conservative *filter*, not a judge. No text-quality pretense.

    - Exclude candidates whose content starts with the P0-B failure marker ``❌``
      (a tool-handler that failed to persist or verify).
    - Of the remaining, return the first.
    - If ALL candidates failed, degrade to index 0 (the baseline N=1 behavior) —
      we never let a "no winner" situation block or corrupt the agent loop.

    The P0-A Verifier result is carried in ``candidate.tool_verify`` / context
    ``recent_tool_verify`` as an *input signal*, but this filter deliberately does
    NOT consume it — wiring the verifier signal into selection is P2's job.
    """

    def judge(self, candidates: List[Candidate], context: dict) -> tuple[int, str]:
        if not candidates:
            return 0, "no candidates; degraded to baseline"
        eligible = [c for c in candidates if not c.content.startswith("❌")]
        if not eligible:
            return 0, "all candidates failed ❌ filter; degraded to baseline (N=1 behavior)"
        winner = eligible[0]
        return winner.index, (
            f"selected candidate {winner.index}: first non-failing "
            f"({len(eligible)}/{len(candidates)} eligible)"
        )


# ─── Response introspection (defensive) ─────────────────────────────────────

def _extract_meta(response: Any) -> tuple[str, Optional[list], Optional[str]]:
    """Pull (content, tool_calls, finish_reason) from a provider response.

    Handles both attribute-access (SimpleNamespace / pydantic) and dict shapes,
    and tolerates missing fields — returns (``, None, None) on any surprise so the
    caller degrades safely.
    """
    try:
        choices = getattr(response, "choices", None)
        if choices is None and isinstance(response, dict):
            choices = response.get("choices")
        if not choices:
            return "", None, None
        choice = choices[0]
        if isinstance(choice, dict):
            finish_reason = choice.get("finish_reason")
            msg = choice.get("message") or {}
            content = msg.get("content") or "" if isinstance(msg, dict) else ""
            tool_calls = msg.get("tool_calls") if isinstance(msg, dict) else None
        else:
            finish_reason = getattr(choice, "finish_reason", None)
            msg = getattr(choice, "message", None)
            content = getattr(msg, "content", None) or "" if msg is not None else ""
            tool_calls = getattr(msg, "tool_calls", None) if msg is not None else None
        if content is None:
            content = ""
        return content, tool_calls, finish_reason
    except Exception:  # noqa: BLE001 - never let introspection break the loop
        return "", None, None


def _clone_override_temp(api_kwargs: dict, temperature: float) -> dict:
    """Shallow-clone kwargs and force a sampling temperature + non-streaming."""
    kwargs = dict(api_kwargs)
    if temperature is not None:
        kwargs["temperature"] = temperature
    kwargs["stream"] = False
    return kwargs


def _usage_tokens(response: Any) -> int:
    try:
        usage = getattr(response, "usage", None)
        if usage is None and isinstance(response, dict):
            usage = response.get("usage")
        if not usage:
            return 0
        if isinstance(usage, dict):
            return int(usage.get("prompt_tokens", 0) or 0) + int(usage.get("completion_tokens", 0) or 0)
        return int(getattr(usage, "prompt_tokens", 0) or 0) + int(getattr(usage, "completion_tokens", 0) or 0)
    except Exception:  # noqa: BLE001
        return 0


# ─── Core sampler ────────────────────────────────────────────────────────────

def sample_best_of_n(
    agent: Any,
    api_kwargs: dict,
    cfg: TTCConfig,
    judge: OutcomeJudge,
    context: Optional[dict] = None,
) -> tuple[Any, dict]:
    """Run best-of-N on a single non-streaming model call.

    Returns ``(response, diagnostics)``. ``response`` is always a real response
    object from ``agent._interruptible_api_call`` (or the first candidate), so the
    downstream conversation loop consumes it identically to the baseline.

    Degrades to the baseline single call on any of:
      - cfg disabled / n <= 1
      - first response is a tool-call step (finish_reason != "stop" or has tool_calls)
      - any per-candidate call error
      - judge error
    """
    context = context or {}
    diagnostics = {"enabled": cfg.enabled, "n": cfg.n, "sampled": 0, "tokens": 0,
                   "winner_idx": 0, "reasoning": "baseline (N=1)"}

    # 1) baseline call — always made, identical to the unmodified loop
    try:
        first = agent._interruptible_api_call(api_kwargs)
    except Exception:  # noqa: BLE001 - caller's outer retry/fallback handles this
        raise

    # 2) gate: disabled, or N<=1, or not a final-answer step → baseline
    if not cfg.enabled or cfg.n <= 1:
        return first, diagnostics
    content, tool_calls, finish_reason = _extract_meta(first)
    if tool_calls or finish_reason != "stop":
        # R3: tool-call step (or unknown finish) is never sampled
        return first, diagnostics

    n = max(1, int(cfg.n))
    candidates: List[Candidate] = [
        Candidate(index=0, content=content, finish_reason=finish_reason,
                  has_tool_calls=bool(tool_calls), raw=first,
                  tool_verify=context.get("recent_tool_verify") or [])
    ]
    total_tokens = _usage_tokens(first)
    call_errors = 0
    for i in range(1, n):
        try:
            r = agent._interruptible_api_call(_clone_override_temp(api_kwargs, cfg.temperature))
            c, tc, fr = _extract_meta(r)
            candidates.append(Candidate(index=i, content=c, finish_reason=fr,
                                         has_tool_calls=bool(tc), raw=r,
                                         tool_verify=context.get("recent_tool_verify") or []))
            total_tokens += _usage_tokens(r)
        except Exception as e:  # noqa: BLE001 - fail-open: skip this candidate
            call_errors += 1
            logger.warning("test_time_compute candidate %d failed, skipped: %s", i, e)
            continue

    # 3) judge — fail-open: any error → baseline (candidate 0)
    try:
        winner_idx, reasoning = judge.judge(candidates, context)
    except Exception as e:  # noqa: BLE001
        logger.warning("test_time_compute judge failed, degraded to baseline: %s", e)
        return first, {**diagnostics, "sampled": len(candidates),
                       "tokens": total_tokens, "winner_idx": 0,
                       "reasoning": f"judge error → baseline: {e}"}

    winner = candidates[winner_idx] if 0 <= winner_idx < len(candidates) else candidates[0]
    diagnostics.update(sampled=len(candidates), tokens=total_tokens,
                       winner_idx=winner.index, reasoning=reasoning)
    if cfg.enabled:
        logger.info(
            "test_time_compute: n=%d sampled=%d winner=%d tokens=%d (%d call errors); %s",
            n, len(candidates), winner.index, total_tokens, call_errors, reasoning,
        )
    return winner.raw, diagnostics
