"""⑮ 腿 B — 通用发射桥 record_generic_handoff 测试。

验证任意长程任务（非 scholarforge 域）可通过字符串 task_key 发射
handoff 状态，进入 continuity_facade 第 7 源跨会话注入。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


@pytest.fixture
def tmp_memory_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_handoff_generic.db")
    db_path_obj = type("Path", (), {"__str__": lambda s: db_path})()

    import agent.project_handoff as ph
    monkeypatch.setattr(ph, "_db_path", lambda: db_path_obj)
    import agent.memory_fabric as mf
    monkeypatch.setattr(mf, "index_db_path", lambda: db_path_obj)
    yield ph


class TestStableTaskId:
    def test_stable_across_calls(self):
        from agent.project_handoff import _stable_task_id
        assert _stable_task_id("refactor-auth-flow") == _stable_task_id("refactor-auth-flow")

    def test_distinct_keys_distinct_ids(self):
        from agent.project_handoff import _stable_task_id
        assert _stable_task_id("task-a") != _stable_task_id("task-b")

    def test_fits_sqlite_integer(self):
        from agent.project_handoff import _stable_task_id
        pid = _stable_task_id("some-long-task-key-here")
        assert pid < (1 << 52)
        assert pid >= 0


class TestRecordGeneric:
    def test_record_and_readback(self, tmp_memory_db):
        ph = tmp_memory_db
        ok = ph.record_generic_handoff(
            task_key="refactor-auth-flow",
            title="重构认证流程",
            status="writing",
            progress="第 2 阶段 / 共 4 阶段",
            last_section="oauth-callback",
            extra={"conclusion": "改用 PKCE 流程", "pending": ["补单测"]},
        )
        assert ok is True

        handoffs = ph.get_active_handoffs()
        assert len(handoffs) == 1
        h = handoffs[0]
        assert h["domain"] == "generic"
        assert h["title"] == "重构认证流程"
        assert h["extra"]["conclusion"] == "改用 PKCE 流程"

    def test_upsert_same_key(self, tmp_memory_db):
        ph = tmp_memory_db
        ph.record_generic_handoff(task_key="task-x", title="旧标题", progress="1/3")
        ph.record_generic_handoff(task_key="task-x", title="新标题", progress="3/3")

        handoffs = ph.get_active_handoffs()
        assert len(handoffs) == 1
        assert handoffs[0]["title"] == "新标题"
        assert handoffs[0]["progress"] == "3/3"

    def test_distinct_keys_coexist(self, tmp_memory_db):
        ph = tmp_memory_db
        ph.record_generic_handoff(task_key="task-a", title="A")
        ph.record_generic_handoff(task_key="task-b", title="B")
        assert len(ph.get_active_handoffs()) == 2

    def test_empty_key_rejected(self, tmp_memory_db):
        ph = tmp_memory_db
        assert ph.record_generic_handoff(task_key="", title="空") is False
        assert ph.record_generic_handoff(task_key="   ", title="空白") is False
        assert ph.get_active_handoffs() == []

    def test_format_prompt_includes_generic(self, tmp_memory_db):
        ph = tmp_memory_db
        ph.record_generic_handoff(task_key="docs-roadmap", title="文档路线图", progress="2/4")
        prompt = ph.format_handoffs_prompt()
        assert "任务" in prompt  # generic → "任务" 标签
        assert "文档路线图" in prompt

    def test_done_excluded(self, tmp_memory_db):
        ph = tmp_memory_db
        ph.record_generic_handoff(task_key="t1", title="进行中", status="writing")
        ph.record_generic_handoff(task_key="t2", title="已完成", status="done")
        handoffs = ph.get_active_handoffs()
        assert len(handoffs) == 1
        assert handoffs[0]["title"] == "进行中"


class TestEmitFromSession:
    def test_no_decision_no_emit(self, tmp_memory_db):
        """纯问答（无决策/待办）→ 不发射。"""
        ph = tmp_memory_db
        messages = [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好！有什么可以帮你？"},
        ]
        ok = ph.emit_generic_handoff_from_session(messages, "sess-chat")
        assert ok is False
        assert ph.get_active_handoffs() == []

    def test_with_decisions_emits(self, tmp_memory_db):
        """有结论的会话 → 发射到 generic 域。"""
        ph = tmp_memory_db
        messages = [
            {"role": "user", "content": "帮我重构认证流程"},
            {"role": "assistant", "content": "结论是：改用 PKCE 流程，放弃 implicit 模式。"},
        ]
        ok = ph.emit_generic_handoff_from_session(messages, "sess-auth")
        assert ok is True
        handoffs = ph.get_active_handoffs()
        assert len(handoffs) == 1
        h = handoffs[0]
        assert h["domain"] == "generic"
        assert "重构认证流程" in h["title"]

    def test_same_session_upserts(self, tmp_memory_db):
        """同一 session 多次发射 UPSERT，不无限堆积。"""
        ph = tmp_memory_db
        msgs = [
            {"role": "user", "content": "任务 A"},
            {"role": "assistant", "content": "方案是完成第一步，再验证第二步。"},
        ]
        ph.emit_generic_handoff_from_session(msgs, "sess-x")
        ph.emit_generic_handoff_from_session(msgs, "sess-x")
        handoffs = ph.get_active_handoffs()
        assert len(handoffs) == 1

    def test_empty_messages(self, tmp_memory_db):
        ph = tmp_memory_db
        assert ph.emit_generic_handoff_from_session([], "s") is False
        assert ph.emit_generic_handoff_from_session(None, "s") is False

    def test_pending_tasks_emit(self, tmp_memory_db):
        """只有待办、无结论也发射。"""
        ph = tmp_memory_db
        messages = [
            {"role": "user", "content": "写周报"},
            {"role": "assistant", "content": "下一步：补齐数据。"},
        ]
        ok = ph.emit_generic_handoff_from_session(messages, "sess-report")
        assert ok is True
        handoffs = ph.get_active_handoffs()
        assert len(handoffs) == 1
        assert handoffs[0]["extra"]["pending_tasks"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
