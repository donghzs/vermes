"""P0-1 能力画像 + P0-2 任务级 LLM 覆盖（D1/D2 拍板项）测试。

- P0-1：分派 LLM 的指令必须携带各执行者能力画像（capability_tags），
  让 LLM 按能力匹配最合适者（Vermes 中介层，语义决策留 LLM）。
- P0-2：任务 model_override 仅对 native/CLI 通路生效；ACP agent 模型自持，忽略。
"""

import asyncio
import sys

sys.path.insert(0, "/Users/dongzusheng/Projects/vermes-electron")

from vermes_cli.botmode import org_engine as oe

ROLES = [
    {"role_id": "pm", "name": "产品经理", "type": "dispatcher", "profile_id": "pm"},
    {"role_id": "e1", "name": "工程师甲", "type": "executor", "profile_id": "eng1"},
    {"role_id": "e2", "name": "工程师乙", "type": "executor", "profile_id": "eng2"},
    {"role_id": "qa", "name": "QA", "type": "auditor", "profile_id": "qa"},
    {"role_id": "d1", "name": "交付经理", "type": "aggregator", "profile_id": "deliver"},
]
PROFILES = {
    "pm": {"id": "pm", "name": "产品经理"},
    # eng1: CLI 通路，带能力标签 + 自有模型
    "eng1": {"id": "eng1", "name": "工程师甲", "transport": "cli",
             "model": "gpt-4o", "capability_tags": ["code", "refactor", "search"]},
    # eng2: ACP 通路，自带模型（覆盖应被忽略）
    "eng2": {"id": "eng2", "name": "工程师乙", "transport": "acp",
             "model": "claude-opus", "capability_tags": ["writing", "research"]},
    "qa": {"id": "qa", "name": "QA", "transport": "cli", "model": "gpt-4o-mini"},
    "deliver": {"id": "deliver", "name": "交付经理", "transport": "cli", "model": "gpt-4o"},
}


def make_task(extra=None):
    t = {"id": "t1", "room_id": "r1", "title": "任务", "brief": "写代码并出文档",
         "status": "dispatched", "plan": [], "artifacts": {}, "audit_log": [],
         "final_output": "", "current_round": 1, "created_at": 0, "updated_at": 0}
    if extra:
        t.update(extra)
    return t


def make_ctx(task, runner):
    return oe.OrgContext(
        task=task, roles=ROLES, profiles=PROFILES,
        room={"title": "测试群"}, store=lambda tid, t: None,
        append_message=lambda *a, **k: None,
    ), runner


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_p0_1_dispatcher_prompt_carries_capability_profile():
    """P0-1：分派指令必须含『能力画像』与各执行者 capability_tags。"""
    calls = []

    async def runner(profile, instruction, _ctx):
        calls.append((profile["id"], instruction, profile.get("model")))
        pid = profile["id"]
        if pid == "qa":
            return '{"verdict": "pass", "comment": ""}'
        if pid == "pm":
            return ('[{"assignee": "eng1", "instruction": "写代码", "acceptance": "可运行"},'
                    '{"assignee": "eng2", "instruction": "写文档", "acceptance": "通顺"}]')
        if pid == "deliver":
            return "【汇总】完成。"
        return f"[工件-{pid}]"

    ctx, runner = make_ctx(make_task(), runner)
    run(oe.run_org_task(ctx, runner))

    pm_calls = [c for c in calls if c[0] == "pm"]
    assert pm_calls, "dispatcher 未被调用"
    pm_inst = pm_calls[0][1]
    assert "能力画像" in pm_inst, "分派指令缺少能力画像段"
    assert "code" in pm_inst and "refactor" in pm_inst, "eng1 能力标签未透传"
    assert "writing" in pm_inst and "research" in pm_inst, "eng2 能力标签未透传"


def test_p0_2_model_override_native_cli_applied():
    """P0-2：model_override 对 CLI executor 生效。"""
    calls = []

    async def runner(profile, instruction, _ctx):
        calls.append((profile["id"], instruction, profile.get("model")))
        pid = profile["id"]
        if pid == "qa":
            return '{"verdict": "pass", "comment": ""}'
        if pid == "pm":
            return ('[{"assignee": "eng1", "instruction": "写代码", "acceptance": "可运行"},'
                    '{"assignee": "eng2", "instruction": "写文档", "acceptance": "通顺"}]')
        if pid == "deliver":
            return "【汇总】完成。"
        return f"[工件-{pid}]"

    ctx, runner = make_ctx(make_task({"model_override": "qwen-plus"}), runner)
    run(oe.run_org_task(ctx, runner))

    eng1 = [c for c in calls if c[0] == "eng1"]
    assert eng1, "eng1 未执行"
    assert eng1[0][2] == "qwen-plus", "CLI 通路未应用 model_override"


def test_p0_2_model_override_acp_ignored():
    """P0-2：model_override 对 ACP executor 忽略（模型由该 agent 自持）。"""
    calls = []

    async def runner(profile, instruction, _ctx):
        calls.append((profile["id"], instruction, profile.get("model")))
        pid = profile["id"]
        if pid == "qa":
            return '{"verdict": "pass", "comment": ""}'
        if pid == "pm":
            # 把文档子任务派给 ACP 的 eng2
            return ('[{"assignee": "eng1", "instruction": "写代码", "acceptance": "可运行"},'
                    '{"assignee": "eng2", "instruction": "写文档", "acceptance": "通顺"}]')
        if pid == "deliver":
            return "【汇总】完成。"
        return f"[工件-{pid}]"

    ctx, runner = make_ctx(make_task({"model_override": "qwen-plus"}), runner)
    run(oe.run_org_task(ctx, runner))

    eng2 = [c for c in calls if c[0] == "eng2"]
    assert eng2, "eng2(ACP) 未执行"
    assert eng2[0][2] == "claude-opus", "ACP 通路被错误覆盖模型（应保留自持模型）"


def test_p0_2_no_override_keeps_profile_model():
    """P0-2：无 model_override 时，native/CLI 沿用 profile 自有模型。"""
    calls = []

    async def runner(profile, instruction, _ctx):
        calls.append((profile["id"], instruction, profile.get("model")))
        pid = profile["id"]
        if pid == "qa":
            return '{"verdict": "pass", "comment": ""}'
        if pid == "pm":
            return ('[{"assignee": "eng1", "instruction": "写代码", "acceptance": "可运行"},'
                    '{"assignee": "eng2", "instruction": "写文档", "acceptance": "通顺"}]')
        if pid == "deliver":
            return "【汇总】完成。"
        return f"[工件-{pid}]"

    ctx, runner = make_ctx(make_task(), runner)
    run(oe.run_org_task(ctx, runner))

    eng1 = [c for c in calls if c[0] == "eng1"]
    assert eng1[0][2] == "gpt-4o", "无覆盖时应保留 profile 自有模型"
