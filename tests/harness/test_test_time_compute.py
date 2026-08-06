"""P1 Test-Time Compute (best-of-N) 聚焦测试。

照真实调用方形态写（conversation_loop.py:2008-2030 的非流式 seam 调用方式），
不是照实现镜像写。核心纪律（报告 §9.2 / R5）：验证器自身也要被验证——
所以本文件含负向用例（禁用的/工具步/finish!=stop 必须 N=1），且反向验证
（拷到无该模块的旧 commit 应失败）由运行脚本执行。

覆盖：
  1. DefaultJudge：排除 ❌ 前缀、全失败退化、首候选
  2. _extract_meta：标准/字典/异常响应健壮
  3. sample_best_of_n：禁用→基线、工具步→N=1、finish!=stop→N=1、
     启用且最终答案→采样 N 次并返回 winner（R4 token 日志）、调用出错 fail-open
  4. 配置 load：启用读取、缺失/损坏 fail-open 默认
"""

import pytest
from types import SimpleNamespace

from harness.test_time_compute import (
    Candidate,
    DefaultJudge,
    TTCConfig,
    _extract_meta,
    load_test_time_config,
    get_test_time_config,
    sample_best_of_n,
)


# ─── helpers ─────────────────────────────────────────────────────────────────

def _resp(content="", tool_calls=None, finish_reason="stop", tokens=0):
    msg = SimpleNamespace(content=content, tool_calls=tool_calls)
    choice = SimpleNamespace(message=msg, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice],
                           usage=SimpleNamespace(prompt_tokens=tokens, completion_tokens=0))


class _FakeAgent:
    def __init__(self, responses, raise_on=None):
        self._responses = list(responses)
        self._raise_on = set(raise_on or [])
        self._calls = []
        self._ttc_config = None  # allow get_test_time_config caching

    def _interruptible_api_call(self, api_kwargs):
        self._calls.append(dict(api_kwargs))
        idx = len(self._calls) - 1
        if idx in self._raise_on:
            raise RuntimeError(f"boom on call {idx}")
        return self._responses.pop(0)


# ─── DefaultJudge (filter, not judge) ───────────────────────────────────────

def test_default_judge_excludes_failing():
    cands = [
        Candidate(0, "❌ 写回失败", "stop", False, None),
        Candidate(1, "正常回答一", "stop", False, None),
        Candidate(2, "正常回答二", "stop", False, None),
    ]
    idx, reason = DefaultJudge().judge(cands, {})
    assert idx == 1, reason  # 第一个非失败候选


def test_default_judge_all_failing_degrades_to_baseline():
    cands = [
        Candidate(0, "❌ a", "stop", False, None),
        Candidate(1, "❌ b", "stop", False, None),
    ]
    idx, reason = DefaultJudge().judge(cands, {})
    assert idx == 0, reason  # 全失败 → 退化到基线 index 0
    assert "degraded" in reason


def test_default_judge_first_eligible_when_clean():
    cands = [
        Candidate(0, "回答零", "stop", False, None),
        Candidate(1, "回答一", "stop", False, None),
    ]
    idx, _ = DefaultJudge().judge(cands, {})
    assert idx == 0


# ─── _extract_meta 健壮性 ───────────────────────────────────────────────────

def test_extract_meta_standard():
    content, tc, fr = _extract_meta(_resp("hi", tool_calls=[1], finish_reason="tool_calls"))
    assert content == "hi"
    assert tc == [1]
    assert fr == "tool_calls"


def test_extract_meta_dict_shape():
    r = {"choices": [{"message": {"content": "x", "tool_calls": None}, "finish_reason": "stop"}]}
    content, tc, fr = _extract_meta(r)
    assert content == "x"
    assert tc is None
    assert fr == "stop"


def test_extract_meta_broken_response():
    assert _extract_meta(None) == ("", None, None)
    assert _extract_meta(object()) == ("", None, None)
    assert _extract_meta(SimpleNamespace()) == ("", None, None)


# ─── sample_best_of_n 行为 ───────────────────────────────────────────────────

def test_sample_disabled_returns_baseline():
    agent = _FakeAgent([_resp("only")])
    cfg = TTCConfig(enabled=False, n=3)
    resp, diag = sample_best_of_n(agent, {"messages": []}, cfg, DefaultJudge(), {})
    assert resp.choices[0].message.content == "only"
    assert len(agent._calls) == 1  # 未采样


def test_sample_n_le1_returns_baseline():
    agent = _FakeAgent([_resp("only")])
    cfg = TTCConfig(enabled=True, n=1)
    resp, diag = sample_best_of_n(agent, {"messages": []}, cfg, DefaultJudge(), {})
    assert resp.choices[0].message.content == "only"
    assert len(agent._calls) == 1


def test_sample_tool_call_step_returns_baseline():
    # 首响应是工具步 → 不得采样（副作用爆炸约束 R3）
    agent = _FakeAgent([_resp("let me call", tool_calls=[{}], finish_reason="tool_calls")])
    cfg = TTCConfig(enabled=True, n=3)
    resp, diag = sample_best_of_n(agent, {"messages": []}, cfg, DefaultJudge(), {})
    assert resp.choices[0].message.tool_calls == [{}]
    assert len(agent._calls) == 1  # N=1，未采样


def test_sample_finish_reason_not_stop_returns_baseline():
    agent = _FakeAgent([_resp("truncated", finish_reason="length")])
    cfg = TTCConfig(enabled=True, n=3)
    resp, diag = sample_best_of_n(agent, {"messages": []}, cfg, DefaultJudge(), {})
    assert len(agent._calls) == 1


def test_sample_enabled_final_samples_n_and_returns_winner():
    # 三个最终答案候选（都无 ❌）→ DefaultJudge 选第一个 (= baseline)
    r0, r1, r2 = _resp("a0"), _resp("a1"), _resp("a2")
    agent = _FakeAgent([r0, r1, r2])
    cfg = TTCConfig(enabled=True, n=3, temperature=0.9)
    resp, diag = sample_best_of_n(agent, {"messages": []}, cfg, DefaultJudge(), {})
    assert len(agent._calls) == 3  # 基线 + 2 次采样
    # 候选 1/2 用覆写后的 temperature + 非流式
    assert agent._calls[1]["temperature"] == 0.9
    assert agent._calls[1]["stream"] is False
    assert agent._calls[2]["temperature"] == 0.9
    # DefaultJudge 返回首个非失败候选 = index 0 = 基线
    assert resp is r0
    assert diag["sampled"] == 3
    assert diag["winner_idx"] == 0


def test_sample_call_error_skips_candidate_fail_open():
    r0, r2 = _resp("a0"), _resp("a2")
    agent = _FakeAgent([r0, r2], raise_on={1})  # 第 2 次（候选 1）抛错
    cfg = TTCConfig(enabled=True, n=3)
    resp, diag = sample_best_of_n(agent, {"messages": []}, cfg, DefaultJudge(), {})
    assert len(agent._calls) == 3  # 一次失败仍计为尝试
    assert resp is r0  # 退化到候选 0


def test_sample_judge_error_degrades_to_baseline():
    class _BoomJudge:
        def judge(self, candidates, context):
            raise RuntimeError("judge broke")

    r0, r1 = _resp("a0"), _resp("a1")
    agent = _FakeAgent([r0, r1])
    cfg = TTCConfig(enabled=True, n=2)
    resp, diag = sample_best_of_n(agent, {"messages": []}, cfg, _BoomJudge(), {})
    assert resp is r0  # judge 出错 → 基线


# ─── 配置 load ──────────────────────────────────────────────────────────────

def test_load_config_enabled_read(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        "test_time_compute:\n  enabled: true\n  n: 3\n  temperature: 0.9\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("harness.test_time_compute._config_path", lambda: cfg_file)
    cfg = load_test_time_config()
    assert cfg.enabled is True
    assert cfg.n == 3
    assert cfg.temperature == 0.9


def test_load_config_missing_fail_open(tmp_path, monkeypatch):
    monkeypatch.setattr("harness.test_time_compute._config_path", lambda: tmp_path / "nope.yaml")
    cfg = load_test_time_config()
    assert cfg.enabled is False
    assert cfg.n == 1  # 零成本退化


def test_load_config_malformed_fail_open(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("::: not valid yaml :::\n  - [", encoding="utf-8")
    monkeypatch.setattr("harness.test_time_compute._config_path", lambda: cfg_file)
    cfg = load_test_time_config()
    assert cfg.enabled is False
    assert cfg.n == 1


def test_get_test_time_config_caches_on_agent():
    agent = _FakeAgent([_resp()])
    c1 = get_test_time_config(agent)
    c2 = get_test_time_config(agent)
    assert c1 is c2
    assert agent._ttc_config is c1
