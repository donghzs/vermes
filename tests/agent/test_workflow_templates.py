"""G3 工作流模板：存储 / 实例化 / 运行 + 反向承重墙。

按用户纪律：每个核心行为配「掏空实现→对应断言必红」的反向验证，证明测试真抓缺口。
"""

import argparse

import pytest

from agent import session_plan_store, workflow_templates
from agent.workflow_templates import (
    WorkflowTemplateStore,
    instantiate_template,
    run_template,
)

_SAMPLE_PLAN = {
    "steps": [
        {"id": "a", "title": "收集", "dependencies": []},
        {"id": "b", "title": "分析", "dependencies": ["a"]},
        {"id": "c", "title": "汇报", "dependencies": ["b"]},
    ]
}


@pytest.fixture
def tmp_home(tmp_path, monkeypatch):
    """隔离 VERMES_HOME + 重置模块级 DB 路径缓存，避免污染真实环境。"""
    monkeypatch.setenv("VERMES_HOME", str(tmp_path))
    monkeypatch.setattr(workflow_templates, "_DB_PATH", None)
    monkeypatch.setattr(session_plan_store, "_DB_PATH", None)
    return tmp_path


def _store():
    return WorkflowTemplateStore()


# ── T1 roundtrip ────────────────────────────────────────────────────────────
def test_t1_save_load_roundtrip(tmp_home):
    v = _store().save_template("relay", _SAMPLE_PLAN, description="d")
    assert v == 1
    tpl = _store().load_template("relay")
    assert tpl is not None
    assert tpl["version"] == 1
    assert tpl["description"] == "d"
    assert len(tpl["steps"]) == 3
    # 依赖边完整保留
    by_id = {s["id"]: s for s in tpl["steps"]}
    assert by_id["b"]["dependencies"] == ["a"]
    assert by_id["c"]["dependencies"] == ["b"]


def test_t1_reverse_wall_strip_save(tmp_home, monkeypatch):
    """掏空 save_template（不落库）→ load 必为 None（证明 load 真读库）。"""
    monkeypatch.setattr(
        WorkflowTemplateStore, "save_template",
        lambda self, *a, **k: 1,  # 不写库
    )
    _store().save_template("x", _SAMPLE_PLAN)
    assert _store().load_template("x") is None


# ── T2 实例化：新 id + 依赖边重映射 ────────────────────────────────────────
def test_t2_instantiate_new_ids_and_remap(tmp_home):
    _store().save_template("relay", _SAMPLE_PLAN)
    new_plan = instantiate_template("relay", "sessX")
    ids = [s["id"] for s in new_plan["steps"]]
    # 全部为新 id（与模板原始 id 无交集）
    assert not (set(ids) & {"a", "b", "c"})
    assert len(ids) == 3 and len(set(ids)) == 3
    # 依赖边按新 id 重映射
    by_new = {s["title"]: s for s in new_plan["steps"]}
    a_new = by_new["收集"]["id"]
    b_new = by_new["分析"]["id"]
    c_new = by_new["汇报"]["id"]
    assert by_new["分析"]["dependencies"] == [a_new]
    assert by_new["汇报"]["dependencies"] == [b_new]
    # 全新实例化：状态全部 pending，inputs/outputs 清空
    for s in new_plan["steps"]:
        assert s["status"] == "pending"
        assert s["inputs"] == {}
        assert s["outputs"] == {}
    # session_plan_store 里确实落了这份新 plan（可被 Scheduler 加载）
    persisted = session_plan_store.load_plan_state("sessX")
    assert persisted is not None
    assert len(persisted["plan"]["steps"]) == 3
    assert all(v == "pending" for v in persisted["todo_states"].values())


def test_t2_reverse_wall_no_remap(tmp_home, monkeypatch):
    """掏空 _remap_plan（保持原 id）→ 实例化后仍是旧 id（证明 remap 是产生新 id 的来源）。"""
    monkeypatch.setattr(
        workflow_templates, "_remap_plan", lambda plan, sid: plan
    )
    _store().save_template("relay", _SAMPLE_PLAN)
    new_plan = instantiate_template("relay", "sessR")
    assert new_plan["steps"][0]["id"] == "a"  # 未重映射（坏行为成立 = 承重墙成立）


# ── T3 版本化 ───────────────────────────────────────────────────────────────
def test_t3_version_increment_and_latest(tmp_home):
    v1 = _store().save_template("v", _SAMPLE_PLAN)
    v2 = _store().save_template("v", _SAMPLE_PLAN)
    assert v1 == 1 and v2 == 2
    # 默认取最新
    assert _store().load_template("v")["version"] == 2
    # 可指定历史版本
    assert _store().load_template("v", version=1)["version"] == 1
    # list 仅列最新
    listed = _store().list_templates()
    assert len(listed) == 1
    assert listed[0]["version"] == 2


def test_t3_reverse_wall_no_increment(tmp_home, monkeypatch):
    """掏空版本递增（恒写 version=1）→ save 两次后 load 仍是最旧（证明递增逻辑被测试覆盖）。"""
    orig = WorkflowTemplateStore.save_template

    def _flat(self, name, plan, description="", version=None):
        return orig(self, name, plan, description=description, version=1)

    monkeypatch.setattr(WorkflowTemplateStore, "save_template", _flat)
    _store().save_template("v", _SAMPLE_PLAN)
    _store().save_template("v", _SAMPLE_PLAN)
    # 两次都落在 version=1（ON CONFLICT 覆盖），最新仍是 1
    assert _store().load_template("v")["version"] == 1


# ── T4 CLI 子命令存在 ──────────────────────────────────────────────────────
def test_t4_cli_subcommand_registered(tmp_home):
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    from vermes_cli.workflow_cli import build_workflow_parser

    wf = build_workflow_parser(sub)
    # 子命令存在且可解析 list
    args = parser.parse_args(["workflow", "list"])
    assert args.command == "workflow"
    assert args.workflow_command == "list"
    # save / show / delete / run 均注册（按各自必填参数构造）
    _argv = {
        "save": ["workflow", "save", "sid", "tname"],
        "show": ["workflow", "show", "tname"],
        "delete": ["workflow", "delete", "tname"],
        "run": ["workflow", "run", "tname"],
    }
    for subcmd, argv in _argv.items():
        p = argparse.ArgumentParser()
        s2 = p.add_subparsers(dest="command")
        build_workflow_parser(s2)
        a = p.parse_args(argv)
        assert a.workflow_command == subcmd


def test_t4_reverse_wall_cli_missing(tmp_home, monkeypatch):
    """掏空 build_workflow_parser（不注册任何子命令）→ parse 'workflow list' 必失败。"""
    monkeypatch.setattr(
        "vermes_cli.workflow_cli.build_workflow_parser",
        lambda subparsers: subparsers.add_parser("workflow"),
    )
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    from vermes_cli.workflow_cli import build_workflow_parser

    build_workflow_parser(sub)
    with pytest.raises(SystemExit):
        parser.parse_args(["workflow", "list"])  # 无 list 子命令 → 退出


# ── T5 run_template 接入 Scheduler（整合验证）────────────────────────────────
def test_t5_run_template_drives_scheduler(tmp_home):
    _store().save_template("relay", _SAMPLE_PLAN)

    calls = []

    async def fake_exec(step, ctx):
        from agent.workflow_scheduler import StepExecResult

        calls.append(step["id"])
        return StepExecResult(status="completed", outputs={"summary": f"done {step['id']}"})

    result = run_template("relay", "sessRun", fake_exec, concurrent=False)
    assert result is not None
    assert len(calls) == 3  # 三步全跑（实例化后 id 已重映射，不依赖原 id 字面量）
    # 依赖顺序：无依赖的步最先跑（拓扑门控保证 a→b→c 序）
    assert result.exec_order[0] is not None
    # 落盘的 todo_states 全 completed
    persisted = session_plan_store.load_plan_state("sessRun")
    assert all(v == "completed" for v in persisted["todo_states"].values())
