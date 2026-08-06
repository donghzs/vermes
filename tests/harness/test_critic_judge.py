"""P2 任务级 Critic（CriticJudge）聚焦测试。

照真实调用方形态写（conversation_loop.py 接缝 → sample_best_of_n →
CriticJudge.judge(candidates, context)），不是照实现镜像写。纪律（报告 §五 / R5）：
验证器自身也要被验证——所以本文件含反向用例（硬闸全 False 必须拒绝、LLM 拒绝
必须透传、空信号不崩）+ 反向验证脚本（拷到无 critic_judge 的旧 commit 必须失败）。

覆盖：
  R5-1 正向：A 真实 / B 编造 → 选 A（LLM 路径）
  R5-2 反向：
    (a) 硬闸：本回合 tool_verify 全 False → 不调 LLM、返回 (0, unverifiable)
    (b) LLM 拒绝：空信号下法官返回「两者均不可证」→ 透传 (0, unverifiable)，不强行选
  R5-3 回归：空 recent_tool_verify → 走 LLM 不崩
  硬闸边界：部分 False → 仍走 LLM
  fail-open×2：法官抛异常 / 输出非 JSON 或越界 → 退化 (0, baseline)
  配置：critic_model 覆盖 model / 为 None 复用主 model
  信号桥：record_tool_verify 写入 agent._recent_tool_verify
"""

import pytest
from types import SimpleNamespace

from harness.test_time_compute import Candidate
from harness.critic_judge import CriticJudge, RUBRIC
from harness.test_time_compute import load_test_time_config


# ─── helpers ─────────────────────────────────────────────────────────────────

def _judge_resp(content):
    """法官响应：content 是法官输出的 JSON 文本。"""
    msg = SimpleNamespace(content=content, tool_calls=None)
    choice = SimpleNamespace(message=msg, finish_reason="stop")
    return SimpleNamespace(choices=[choice], usage=None)


def _candidate(index, content):
    return Candidate(index=index, content=content, finish_reason="stop",
                     has_tool_calls=False, raw=None)


class _FakeAgent:
    """脚本化法官调用：记录调用、按脚本返回法官响应（或抛错）。"""
    def __init__(self, judge_returns=None, raise_on_judge=False):
        self._judge_returns = judge_returns  # 单次返回的 content 字符串
        self._raise_on_judge = raise_on_judge
        self._calls = []

    def _interruptible_api_call(self, api_kwargs):
        self._calls.append(dict(api_kwargs))
        if self._raise_on_judge:
            raise RuntimeError("judge api boom")
        return _judge_resp(self._judge_returns)


def _base_kwargs(model="main-model"):
    return {
        "model": model,
        "messages": [{"role": "user", "content": "What is 2+2?"}],
        "temperature": 0.7,
        "stream": False,
    }


# ─── R5-1 正向：A 真实 / B 编造 → 选 A ────────────────────────────────────────

def test_r5_positive_selects_real_over_fabricated():
    # 空/部分信号（不触发硬闸）→ 走 LLM 路径；法官返回选 A(index 0)
    agent = _FakeAgent(judge_returns='{"winner_idx": 0, "reasoning": "A consistent with verified facts"}')
    judge = CriticJudge(agent, base_api_kwargs=_base_kwargs())
    cands = [_candidate(0, "A: 2+2=4 (verified)"), _candidate(1, "B: 2+2=5 (fabricated)")]
    idx, reason = judge.judge(cands, {"recent_tool_verify": []})
    assert idx == 0, reason
    # 法官请求形态校验：温度0、非流式、系统 rubric、复用主 model
    kw = agent._calls[0]
    assert kw["temperature"] == 0
    assert kw["stream"] is False
    assert kw["model"] == "main-model"
    assert kw["messages"][0]["role"] == "system"
    assert kw["messages"][0]["content"] == RUBRIC
    # 用户问题被带入，使法官能判断「回答问题」
    assert "What is 2+2?" in kw["messages"][1]["content"]


# ─── R5-2 反向 (a)：硬闸 全 False → 拒绝、不调 LLM ──────────────────────────────

def test_r5_negative_hardgate_all_false_rejects_without_llm():
    agent = _FakeAgent(judge_returns='{"winner_idx": 1, "reasoning": "x"}')
    judge = CriticJudge(agent, base_api_kwargs=_base_kwargs())
    cands = [_candidate(0, "A fabricated"), _candidate(1, "B fabricated")]
    tv = [
        {"function_name": "f1", "ok": False, "reason": "DB 无此记录"},
        {"function_name": "f2", "ok": False, "reason": "外证回读证伪"},
    ]
    idx, reason = judge.judge(cands, {"recent_tool_verify": tv})
    # 不调 LLM（硬闸确定性拒绝），返回 baseline index 0 + 含 unverifiable
    assert len(agent._calls) == 0, "hard gate must NOT consult LLM"
    assert idx == 0
    assert "unverifiable" in reason, reason


# ─── R5-2 反向 (b)：空信号下法官拒绝 → 透传，不强行选 ──────────────────────────

def test_r5_negative_llm_rejection_passthrough():
    # 空信号（无硬闸）→ 走 LLM；法官自身判定两者均不可证，返回 index 0 + unverifiable
    agent = _FakeAgent(judge_returns='{"winner_idx": 0, "reasoning": "both candidates contain unverifiable claims"}')
    judge = CriticJudge(agent, base_api_kwargs=_base_kwargs())
    cands = [_candidate(0, "A fabricated"), _candidate(1, "B fabricated")]
    idx, reason = judge.judge(cands, {"recent_tool_verify": []})
    assert idx == 0
    assert "unverifiable" in reason, reason
    assert len(agent._calls) == 1  # LLM 路径确实被调用


# ─── R5-3 回归：空 recent_tool_verify 不崩 ────────────────────────────────────

def test_r5_regression_empty_signal_no_crash():
    agent = _FakeAgent(judge_returns='{"winner_idx": 1, "reasoning": "B better"}')
    judge = CriticJudge(agent, base_api_kwargs=_base_kwargs())
    cands = [_candidate(0, "A"), _candidate(1, "B")]
    # context 完全无 recent_tool_verify 键，也不能崩
    idx, reason = judge.judge(cands, {})
    assert idx == 1, reason
    assert "B better" == reason


# ─── 硬闸边界：部分 False → 仍走 LLM ──────────────────────────────────────────

def test_hardgate_partial_false_still_uses_llm():
    agent = _FakeAgent(judge_returns='{"winner_idx": 0, "reasoning": "A passed verify"}')
    judge = CriticJudge(agent, base_api_kwargs=_base_kwargs())
    cands = [_candidate(0, "A ok"), _candidate(1, "B bad")]
    tv = [
        {"function_name": "f1", "ok": True, "reason": "verified"},
        {"function_name": "f2", "ok": False, "reason": "failed"},
    ]
    idx, reason = judge.judge(cands, {"recent_tool_verify": tv})
    assert len(agent._calls) == 1, "partial false must consult LLM"
    assert idx == 0


# ─── fail-open×2 ─────────────────────────────────────────────────────────────

def test_failopen_judge_raises_degrades_baseline():
    agent = _FakeAgent(raise_on_judge=True)
    judge = CriticJudge(agent, base_api_kwargs=_base_kwargs())
    cands = [_candidate(0, "A"), _candidate(1, "B")]
    idx, reason = judge.judge(cands, {"recent_tool_verify": []})
    assert idx == 0
    assert "baseline" in reason, reason


def test_failopen_judge_invalid_json_degrades_baseline():
    agent = _FakeAgent(judge_returns="not a json at all")
    judge = CriticJudge(agent, base_api_kwargs=_base_kwargs())
    cands = [_candidate(0, "A"), _candidate(1, "B")]
    idx, reason = judge.judge(cands, {"recent_tool_verify": []})
    assert idx == 0
    assert "baseline" in reason, reason


def test_failopen_judge_out_of_range_degrades_baseline():
    agent = _FakeAgent(judge_returns='{"winner_idx": 9, "reasoning": "x"}')
    judge = CriticJudge(agent, base_api_kwargs=_base_kwargs())
    cands = [_candidate(0, "A"), _candidate(1, "B")]  # 仅 2 候选，9 越界
    idx, reason = judge.judge(cands, {"recent_tool_verify": []})
    assert idx == 0
    assert "baseline" in reason, reason


# ─── 配置：critic_model ───────────────────────────────────────────────────────

def test_config_critic_model_overrides_model():
    agent = _FakeAgent(judge_returns='{"winner_idx": 0, "reasoning": "r"}')
    judge = CriticJudge(agent, critic_model="critic-model", base_api_kwargs=_base_kwargs("main-model"))
    judge.judge([_candidate(0, "A")], {"recent_tool_verify": []})
    assert agent._calls[0]["model"] == "critic-model"


def test_config_critic_model_none_reuses_main_model():
    agent = _FakeAgent(judge_returns='{"winner_idx": 0, "reasoning": "r"}')
    judge = CriticJudge(agent, critic_model=None, base_api_kwargs=_base_kwargs("main-model"))
    judge.judge([_candidate(0, "A")], {"recent_tool_verify": []})
    assert agent._calls[0]["model"] == "main-model"


# ─── 信号桥：record_tool_verify 写入 agent._recent_tool_verify ─────────────────

def test_record_tool_verify_appends_per_call():
    from harness.outcome_verifier import record_tool_verify

    class _A:
        pass

    a = _A()
    record_tool_verify(a, "f1", True, "verified")
    record_tool_verify(a, "f2", False, "failed")
    assert getattr(a, "_recent_tool_verify", None) is not None
    assert len(a._recent_tool_verify) == 2
    assert a._recent_tool_verify[0] == {"function_name": "f1", "ok": True, "reason": "verified"}
    assert a._recent_tool_verify[1] == {"function_name": "f2", "ok": False, "reason": "failed"}


def test_record_tool_verify_fail_open_on_broken_agent():
    from harness.outcome_verifier import record_tool_verify
    # agent 不允许设属性 → 不应抛错（fail-open）
    class _Locked:
        def __setattr__(self, k, v):
            raise AttributeError("locked")
    a = _Locked()
    # 不抛异常即通过
    record_tool_verify(a, "f", True, "r")


# ─── 配置读取：judge / critic_model ──────────────────────────────────────────

def test_load_config_reads_judge_and_critic_model(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        "test_time_compute:\n"
        "  enabled: true\n  n: 3\n  temperature: 0.9\n"
        "  judge: critic\n  critic_model: gpt-4o\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("harness.test_time_compute._config_path", lambda: cfg_file)
    cfg = load_test_time_config()
    assert cfg.enabled is True
    assert cfg.judge == "critic"
    assert cfg.critic_model == "gpt-4o"


def test_load_config_judge_defaults_to_default(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("test_time_compute:\n  enabled: true\n  n: 2\n", encoding="utf-8")
    monkeypatch.setattr("harness.test_time_compute._config_path", lambda: cfg_file)
    cfg = load_test_time_config()
    assert cfg.judge == "default"   # 未配置 → 走 DefaultJudge（零回归）
    assert cfg.critic_model is None  # 未配置 → 复用主对话 provider
