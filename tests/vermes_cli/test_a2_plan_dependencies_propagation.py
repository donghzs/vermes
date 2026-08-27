"""A2 M1a 反向验证测试：工作流 DAG 依赖边必须贯通 chat.py 实时管线。

不 mock 任何生产代码：直接调用 chat.py 的 _extract_plan_loose（宽松解析路径，
现在必须保留 dependencies）与 agent.session_plan_store 的 save/load（持久化必须
无损 round-trip 依赖边）。

反向验证：把 "丢弃 dependencies" 的旧逻辑重新套用后，这些断言必须失败——
证明测试抓的是真实缺口，而非自愈假绿。
"""

import sys
import os
import tempfile

import pytest


# 让 agent / vermes_cli 包可被 import（仓库根在 sys.path）
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _import_chat():
    sys.path.insert(0, REPO_ROOT)
    import vermes_cli.blueprints.chat as chat  # noqa: E402
    return chat


def test_loose_plan_preserves_explicit_dependencies():
    """宽松解析：步骤标题里写明 '依赖 step_x' 时，dependencies 必须被提取。

    反向验证：若 _extract_deps_from_text / _extract_plan_loose 不收集 dependencies，
    返回的 step 将没有依赖边 → 此断言失败。
    """
    chat = _import_chat()
    text = (
        "P0: 抓取网页数据\n"
        "P1: 清洗数据（依赖 P0）\n"
        "P2: 生成报告（depends on P1）\n"
    )
    result = chat._extract_plan_loose(text)
    assert result is not None, "应识别到 >=2 个步骤"
    steps = result["plan"]["steps"]
    by_id = {s["id"]: s for s in steps}
    assert "P0" in by_id and "P1" in by_id and "P2" in by_id
    # P1 依赖 P0，P2 依赖 P1
    assert "P0" in by_id["P1"]["dependencies"], "P1 应保留对 P0 的依赖边"
    assert "P1" in by_id["P2"]["dependencies"], "P2 应保留对 P1 的依赖边"
    # 无依赖的步骤 dependencies 应为空列表（而非缺失/None）
    assert by_id["P0"]["dependencies"] == []


def test_loose_plan_dependencies_default_empty_list():
    """无依赖标注时，dependencies 必须是 []（结构化、可被前端安全渲染）。"""
    chat = _import_chat()
    text = "P0: 第一步\nP1: 第二步\n"
    result = chat._extract_plan_loose(text)
    assert result is not None
    for s in result["plan"]["steps"]:
        assert s["dependencies"] == [], "缺失依赖应规范为 []"


def test_strict_plan_dependencies_roundtrip_via_session_store(tmp_path):
    """严格 JSON 路径：chat.py 构造的 steps_out 含 dependencies，且 session_plan_store
    持久化后无损还原（SSE 重连快照依赖此 round-trip）。

    这里用 chat 的严格解析逻辑复刻前端数据形状（与 chat.py:_detect_and_emit_plan
    严格分支一致），断言 dependencies 在 save/load 后仍完整。
    """
    sys.path.insert(0, REPO_ROOT)
    import agent.session_plan_store as sps  # noqa: E402

    # 复刻 chat.py 严格分支构造的 step dict（含 dependencies 字段）
    plan = {
        "id": "abc12345",
        "title": "示例工作流",
        "steps": [
            {"id": "s1", "title": "A", "status": "pending", "dependencies": []},
            {"id": "s2", "title": "B", "status": "pending", "dependencies": ["s1"]},
            {"id": "s3", "title": "C", "status": "pending", "dependencies": ["s1", "s2"]},
        ],
    }
    sps._DB_PATH = tmp_path / "session_plans.db"
    sps.save_plan_state("sess-1", plan, {"s1": "completed", "s2": "in_progress"}, True)
    loaded = sps.load_plan_state("sess-1")
    assert loaded is not None
    loaded_plan = loaded["plan"]
    by_id = {s["id"]: s for s in loaded_plan["steps"]}
    assert by_id["s2"]["dependencies"] == ["s1"]
    assert by_id["s3"]["dependencies"] == ["s1", "s2"]
    assert by_id["s1"]["dependencies"] == []
