"""H4.4 测试：显式用户反馈 → raw_events（监督式自学习写点 + 工具 handler）。

注意：evolution DB 首次激活会被 _seed_evolution_db 预置示例事件，因此断言
只校验「反馈行是否存在且字段正确」，不依赖精确行数。
"""

import json
import sqlite3

import pytest


def _reset_evolution_state():
    import agent.evolution_manager as em

    em._evolution_active = None
    import agent.raw_event as re

    re._LAST_EMERGENCE_OK = None


@pytest.fixture
def VERMES_home(tmp_path, monkeypatch):
    d = tmp_path / "Vermes"
    d.mkdir()
    monkeypatch.setenv("VERMES_HOME", str(d))
    _reset_evolution_state()
    yield d


def _feedback_rows(tool_name: str):
    from agent.evolution_manager import get_self_model_db

    conn = sqlite3.connect(str(get_self_model_db()))
    rows = conn.execute(
        "SELECT tool_name, success, args_preview, result_preview FROM raw_events "
        "WHERE tool_name = ?",
        (tool_name,),
    ).fetchall()
    conn.close()
    return rows


def test_thumbs_up_writes_positive_raw_event(VERMES_home):
    from agent.feedback_learning import record_user_feedback

    ok = record_user_feedback("thumbs_up", "write_file", "格式正确")
    assert ok is True
    rows = _feedback_rows("feedback_thumbs_up")
    assert len(rows) == 1
    assert rows[0][1] == 1  # 点赞 = 成功
    assert "write_file" in rows[0][2]


def test_thumbs_down_is_negative_event(VERMES_home):
    from agent.feedback_learning import record_user_feedback

    ok = record_user_feedback("thumbs_down", "read_file", "读错文件")
    assert ok is True
    rows = _feedback_rows("feedback_thumbs_down")
    assert len(rows) == 1
    assert rows[0][1] == 0  # 点踩 = 失败


def test_correction_writes_negative_event(VERMES_home):
    from agent.feedback_learning import record_user_feedback

    ok = record_user_feedback("correction", "错误结论", "正确结论是 X")
    assert ok is True
    rows = _feedback_rows("feedback_correction")
    assert len(rows) == 1
    assert rows[0][1] == 0
    assert "正确结论是 X" in rows[0][3]


def test_unknown_kind_rejected(VERMES_home):
    from agent.feedback_learning import record_user_feedback

    # 未知类型直接拒绝，不落库（未触碰 evolution，DB 可能尚未 seed）
    assert record_user_feedback("bogus", "x") is False


def test_tool_handlers_return_success_json(VERMES_home):
    from tools.feedback_tool import thumbs, submit_correction

    r1 = json.loads(thumbs("up", "read_file", "nice"))
    assert r1["success"] is True
    assert r1["feedback"] == "thumbs_up"

    r2 = json.loads(submit_correction("wrong fact", "correct fact"))
    assert r2["success"] is True
    assert r2["correction"] == "correct fact"

    # 两条工具调用都应已落库
    assert _feedback_rows("feedback_thumbs_up")
    assert _feedback_rows("feedback_correction")
