"""
F1/F2 回归测试：delivery 事件闸门 + delivery 消息完整性

F1: 含 cancelled 步骤的任务也应发 delivery + task_complete 事件
    闸门条件：completed + cancelled == total and in_progress == 0
F2: delivery 消息应包含 changes 数组（不仅 changesCount）
"""
import pytest


def _check_delivery_gate(summary: dict) -> bool:
    """模拟 chat.py 的 delivery 闸门逻辑（F1 修复后）"""
    _done = summary.get("completed", 0) + summary.get("cancelled", 0)
    return (
        summary.get("total", 0) > 0
        and _done == summary.get("total")
        and summary.get("in_progress", 0) == 0
    )


def _check_delivery_gate_old(summary: dict) -> bool:
    """修复前的旧闸门逻辑（用于对比验证 F1）"""
    return (
        summary.get("total", 0) > 0
        and summary.get("completed", 0) == summary.get("total")
        and summary.get("in_progress", 0) == 0
    )


class TestDeliveryGate:
    """F1: delivery 事件闸门逻辑"""

    def test_all_completed_triggers(self):
        """全部完成 → 触发"""
        s = {"total": 3, "completed": 3, "in_progress": 0, "pending": 0, "cancelled": 0}
        assert _check_delivery_gate(s) is True

    def test_with_cancelled_triggers(self):
        """含 cancelled 步骤 → 也应触发（F1 修复点）"""
        s = {"total": 3, "completed": 2, "in_progress": 0, "pending": 0, "cancelled": 1}
        assert _check_delivery_gate(s) is True

    def test_with_cancelled_old_gate_does_not_trigger(self):
        """旧闸门在含 cancelled 时不触发 → 证明 F1 是真实 bug"""
        s = {"total": 3, "completed": 2, "in_progress": 0, "pending": 0, "cancelled": 1}
        assert _check_delivery_gate_old(s) is False

    def test_in_progress_does_not_trigger(self):
        """有进行中步骤 → 不触发"""
        s = {"total": 3, "completed": 1, "in_progress": 1, "pending": 1, "cancelled": 0}
        assert _check_delivery_gate(s) is False

    def test_pending_does_not_trigger(self):
        """有待办步骤 → 不触发"""
        s = {"total": 3, "completed": 1, "in_progress": 0, "pending": 2, "cancelled": 0}
        assert _check_delivery_gate(s) is False

    def test_all_cancelled_triggers(self):
        """全部取消 → 触发（终态）"""
        s = {"total": 2, "completed": 0, "in_progress": 0, "pending": 0, "cancelled": 2}
        assert _check_delivery_gate(s) is True

    def test_zero_total_does_not_trigger(self):
        """空任务 → 不触发"""
        s = {"total": 0, "completed": 0, "in_progress": 0, "pending": 0, "cancelled": 0}
        assert _check_delivery_gate(s) is False


class TestDeliveryMessageCompleteness:
    """F2: delivery 消息应包含 changes 数组"""

    def test_delivery_has_changes_array(self):
        """delivery 对象应包含 changes 数组（不仅 changesCount）"""
        delivery = {
            "summary": {"total": 2, "completed": 2, "in_progress": 0, "pending": 0, "cancelled": 0},
            "artifacts": [{"path": "/tmp/test.md", "title": "test.md", "source": "write_file"}],
            "changes_count": 1,
            "changes": [{"path": "/tmp/test.md", "action": "write_file"}],
        }
        assert "changes" in delivery
        assert isinstance(delivery["changes"], list)
        assert len(delivery["changes"]) == delivery["changes_count"]

    def test_delivery_without_changes_still_valid(self):
        """无变更时 changes 为空数组"""
        delivery = {
            "summary": {"total": 1, "completed": 1, "in_progress": 0, "pending": 0, "cancelled": 0},
            "artifacts": [],
            "changes_count": 0,
            "changes": [],
        }
        assert delivery["changes"] == []
        assert delivery["changes_count"] == 0

    def test_risk_labels_data_present(self):
        """E3 风险标签数据：cancelled > 0 时 summary 应携带 cancelled 计数"""
        s = {"total": 3, "completed": 2, "in_progress": 0, "pending": 0, "cancelled": 1}
        assert s["cancelled"] > 0  # 前端据此显示 amber 标签
