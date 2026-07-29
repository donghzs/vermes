"""测试操作链验证器四盲区修复。"""
import pytest
import sys
import os

os.environ["VERMES_HOME"] = os.path.expanduser("~/.Vermes")


class FakeAgent:
    """Minimal agent stub for testing."""
    _operator_claim_rejection_count = 0

    def _operator_claim_verifier_enabled(self):
        return True

    @staticmethod
    def _detect_operation_claims(text):
        # 使用真实实现检测
        from run_agent import AIAgent
        return AIAgent._detect_operation_claims(text)


def test_blind_spot_3_first_turn_trigger():
    """盲区3：首轮也触发（不需要 _tool_history > 0）。"""
    from agent.conversation_loop import _apply_operator_claim_verifier
    agent = FakeAgent()
    agent._operator_claim_rejection_count = 0
    # 首轮：只有 user 消息，无工具历史
    messages = [{"role": "user", "content": "帮我安装 numpy"}]
    final = "已安装了 numpy，可以使用了。"
    result, msgs = _apply_operator_claim_verifier(agent, messages, final, False)
    assert "操作链验证器" in result  # 被拒绝


def test_blind_spot_2_failed_tool_results():
    """盲区2：工具调了但失败，仍声称成功 → 拒绝。"""
    from agent.conversation_loop import _apply_operator_claim_verifier
    agent = FakeAgent()
    agent._operator_claim_rejection_count = 0
    messages = [
        {"role": "user", "content": "写入章节"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "tc1", "function": {"name": "scholarforge_write", "arguments": "{}"}}]},
        {"role": "tool", "name": "scholarforge_write", "tool_call_id": "tc1", "content": "❌ 无法确定 project_id：写回操作必须关联一个论文项目。"},
    ]
    final = "已帮你写入章节内容，保存成功。"
    result, msgs = _apply_operator_claim_verifier(agent, messages, final, False)
    assert "操作链验证器" in result  # 被拒绝


def test_no_reject_when_tool_succeeded():
    """工具成功时不拒绝。"""
    from agent.conversation_loop import _apply_operator_claim_verifier
    agent = FakeAgent()
    agent._operator_claim_rejection_count = 0
    messages = [
        {"role": "user", "content": "写入章节"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "tc1", "function": {"name": "scholarforge_write", "arguments": "{}"}}]},
        {"role": "tool", "name": "scholarforge_write", "tool_call_id": "tc1", "content": "✅ 章节已写入，共 2000 字。"},
    ]
    final = "已帮你写入章节内容，保存成功。"
    result, msgs = _apply_operator_claim_verifier(agent, messages, final, False)
    assert "操作链验证器" not in result  # 不拒绝


def test_blind_spot_4_english_claims():
    """盲区4：英文完成声称也能检测。"""
    from run_agent import AIAgent
    claims = AIAgent._detect_operation_claims("I have installed numpy and it's ready to use.")
    assert len(claims) > 0

    claims = AIAgent._detect_operation_claims("Successfully deployed the application.")
    assert len(claims) > 0

    claims = AIAgent._detect_operation_claims("All done! The build is complete.")
    assert len(claims) > 0


def test_no_false_positive_normal_chat():
    """正常对话不误报。"""
    from run_agent import AIAgent
    claims = AIAgent._detect_operation_claims("你好，我是 AI 助手，有什么可以帮你的？")
    assert len(claims) == 0

    claims = AIAgent._detect_operation_claims("Sure, I can help you with that. Let me think about it.")
    assert len(claims) == 0


def test_reset_counter_on_success():
    """工具成功后重置计数器。"""
    from agent.conversation_loop import _apply_operator_claim_verifier
    agent = FakeAgent()
    agent._operator_claim_rejection_count = 3  # 之前被拒多次
    messages = [
        {"role": "user", "content": "写入章节"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "tc1", "function": {"name": "scholarforge_write", "arguments": "{}"}}]},
        {"role": "tool", "name": "scholarforge_write", "tool_call_id": "tc1", "content": "✅ 章节已写入。"},
    ]
    final = "已帮你写入章节内容。"
    _apply_operator_claim_verifier(agent, messages, final, False)
    assert agent._operator_claim_rejection_count == 0  # 重置
