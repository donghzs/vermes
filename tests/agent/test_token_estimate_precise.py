"""
#2 边界修复测试 — token 估算精确化（tiktoken，fail-open 回退 char/4）

验证：
1. 返回 int 且 > 0
2. tiktoken 可用时走精确路径（结果 ≠ 粗略 char/4）
3. tiktoken 不可用时 fail-open 回退到原有 char/4 估算
"""
import pytest

from agent.model_metadata import estimate_request_tokens_rough


def test_returns_int():
    v = estimate_request_tokens_rough(
        [{"role": "user", "content": "hi"}],
        system_prompt="sys",
        tools=[{"name": "t"}],
    )
    assert isinstance(v, int) and v > 0


def test_precise_path_active(monkeypatch):
    import agent.model_metadata as mm

    enc = mm._get_tiktoken_encoder()
    if enc is None:
        pytest.skip("tiktoken unavailable in this env")

    text = "hello world " * 20  # 240 chars
    msgs = [{"role": "user", "content": text}]
    precise = mm.estimate_request_tokens_rough(msgs)
    crude = (len(text) + 3) // 4  # 60
    assert precise > 0
    assert precise != crude  # confirms tokenizer path is active


def test_fail_open_without_tiktoken(monkeypatch):
    import agent.model_metadata as mm
    from agent.model_metadata import estimate_messages_tokens_rough

    monkeypatch.setattr(mm, "_get_tiktoken_encoder", lambda: None)
    msgs = [{"role": "user", "content": "a" * 400}]
    val = mm.estimate_request_tokens_rough(msgs)  # no system, no tools
    # fail-open must equal the original char/4 rough path (no tiktoken)
    assert val == estimate_messages_tokens_rough(msgs)
    assert val > 0
