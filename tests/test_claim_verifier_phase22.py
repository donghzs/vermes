"""Phase 2.2 测试：claim_verifier 抽取后的纯函数覆盖 + 反向验证。

核心目标：
1. 纯函数 ``detect_operation_claims`` 脱离 agent 即可单测（抽取的直接收益）。
2. 反向验证：用「机制关闭即不拦截」的控制组，证明拦截确实由验证器产生，
   而非巧合；若验证器被掏空（detect 永远返回 []），同一输入不会被拦截。
"""

import pytest


# ---------------------------------------------------------------------------
# 1) 纯函数单测：无需拉起整个 AIAgent
# ---------------------------------------------------------------------------
def test_detect_catches_fabricated_claim_cn():
    from agent.claim_verifier import detect_operation_claims
    claims = detect_operation_claims("已安装了 numpy，可以使用了。")
    assert claims, "应检测到中文完成声称"
    assert any("安装" in c["claim"] for c in claims)


def test_detect_catches_fabricated_claim_en():
    from agent.claim_verifier import detect_operation_claims
    claims = detect_operation_claims("I have installed numpy and it's ready to use.")
    assert claims, "应检测到英文完成声称"
    assert any("installed" in c["claim"].lower() for c in claims)


def test_detect_excludes_false_positives():
    from agent.claim_verifier import detect_operation_claims
    # 这些含「配置/设置/保存设置」应是普通名词，不应误判为操作声称
    for text in [
        "请修改设置后再运行。",
        "这是配置文件，你先看一下。",
        "我已保存设置，下次生效。",
        "修改方案后我们再评审。",
    ]:
        assert detect_operation_claims(text) == [], f"不应误判为声称: {text!r}"


def test_detect_empty_text():
    from agent.claim_verifier import detect_operation_claims
    assert detect_operation_claims("") == []
    assert detect_operation_claims(None) == []


def test_detect_context_window():
    from agent.claim_verifier import detect_operation_claims
    claims = detect_operation_claims("已安装了 numpy")
    assert len(claims) == 1
    c = claims[0]
    assert c["start"] == 0
    assert c["end"] == len("已安装了")  # 实际匹配跨度
    assert "context" in c and isinstance(c["context"], str)


# ---------------------------------------------------------------------------
# 2) 集成：经 conversation_loop 薄封装走到 claim_verifier
# ---------------------------------------------------------------------------
class _StubAgent:
    _operator_claim_rejection_count = 0

    def __init__(self, enabled=True, detect_returns=None):
        self._enabled = enabled
        self._detect_returns = detect_returns  # 反向验证用：None=走真实实现

    def _operator_claim_verifier_enabled(self):
        return self._enabled

    def _detect_operation_claims(self, text):
        if self._detect_returns is not None:
            return self._detect_returns
        from agent.claim_verifier import detect_operation_claims
        return detect_operation_claims(text)


def _fabricated_no_tools():
    """构造「声称完成但本回合零工具调用」的会话。"""
    return [{"role": "user", "content": "帮我安装 numpy"}]


def _final_claim():
    return "已安装了 numpy，可以使用了。"


def test_hard_reject_fabricated_claim():
    """机制开启 + 凭空声称 → 硬拒绝（注入 retry + 替换回复）。"""
    from agent.conversation_loop import _apply_operator_claim_verifier
    agent = _StubAgent(enabled=True)
    result, msgs = _apply_operator_claim_verifier(
        agent, _fabricated_no_tools(), _final_claim(), False
    )
    assert "操作链验证器" in result
    assert any(m.get("role") == "user" and "System" in m.get("content", "") for m in msgs)
    assert agent._operator_claim_rejection_count == 1


def test_reverse_validation_disabled_does_not_reject():
    """反向验证（控制组）：机制关闭时同一输入不被拦截。

    若此用例通过而 test_hard_reject_fabricated_claim 也通过 → 证明拦截
    确实由验证器产生。若有人把验证器掏空（enabled 恒 False），
    本用例会「仍通过」但上面的拒绝用例会「变红」——缺口暴露。
    """
    from agent.conversation_loop import _apply_operator_claim_verifier
    agent = _StubAgent(enabled=False)
    result, msgs = _apply_operator_claim_verifier(
        agent, _fabricated_no_tools(), _final_claim(), False
    )
    assert "操作链验证器" not in (result or "")
    assert not any(m.get("role") == "user" and "System" in m.get("content", "") for m in msgs)


def test_reverse_validation_neutralized_detector_does_not_reject():
    """反向验证（掏空）：detect 永远返回 [] 时，声称不会被识别 → 不拦截。

    把此用例与 test_hard_reject_fabricated_claim 对照：前者要求
    detect 真实命中，后者让 detect 失效。若有人把 detect 改坏
    （例如误删 patterns / 返回空），test_hard_reject 必然变红，
    证明测试抓住了真实缺口而非空过。
    """
    from agent.conversation_loop import _apply_operator_claim_verifier
    agent = _StubAgent(enabled=True, detect_returns=[])  # 模拟损坏的检测器
    result, msgs = _apply_operator_claim_verifier(
        agent, _fabricated_no_tools(), _final_claim(), False
    )
    assert "操作链验证器" not in (result or "")
    assert agent._operator_claim_rejection_count == 0


def test_no_reject_when_tool_succeeded_phase22():
    """工具成功时不拒绝（与既有用例互补，走抽取后路径）。"""
    from agent.conversation_loop import _apply_operator_claim_verifier
    agent = _StubAgent(enabled=True)
    messages = [
        {"role": "user", "content": "写入章节"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "tc1", "function": {"name": "scholarforge_write", "arguments": "{}"}}
        ]},
        {"role": "tool", "name": "scholarforge_write", "tool_call_id": "tc1",
         "content": "已写入章节内容。"},
    ]
    final = "已帮你写入章节内容，保存成功。"
    result, msgs = _apply_operator_claim_verifier(agent, messages, final, False)
    assert "操作链验证器" not in (result or "")
