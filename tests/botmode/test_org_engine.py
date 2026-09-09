"""⑭ Agent Org 组织流水线状态机纯逻辑测试（2026-09-07）。

覆盖：
- 完整流程：分派→并行执行→审计通过→汇总→交付（fake runner 记录指令）
- 审计打回：意见精确到子任务 → 只重做被打回者 → 重审通过
- 审计超限：超过 MAX_AUDIT_ROUNDS → rejected（上报老板裁决）
- 无分派者：老板直派全员并行
- 无审计者：小作坊直通汇总
- 无汇总者：拼接工件
- 老板验收：通过→done；打回→回到 dispatched 带意见
- 拆解格式坏 → 均分兜底
- 模板完整性：company/court 岗位齐全

可独立运行：python tests/botmode/test_org_engine.py
"""

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/Users/dongzusheng/Projects/vermes-electron")

import pytest

from vermes_cli.botmode import org_engine as oe


# ─────────────────────────── 测试装备 ───────────────────────────

def make_task(tid="t1"):
    return {
        "id": tid, "room_id": "r1", "title": "写季度报告",
        "brief": "写一份 Q3 季度报告，覆盖营收与风险",
        "status": "dispatched", "plan": [], "artifacts": {},
        "audit_log": [], "final_output": "", "current_round": 1,
        "created_at": 0, "updated_at": 0,
    }


def make_ctx(task=None, roles=None, profiles=None, runner=None, room=None):
    task = task or make_task()
    roles = roles or [
        {"role_id": "pm", "name": "产品经理", "type": "dispatcher", "profile_id": "pm"},
        {"role_id": "e1", "name": "工程师甲", "type": "executor", "profile_id": "eng1"},
        {"role_id": "e2", "name": "工程师乙", "type": "executor", "profile_id": "eng2"},
        {"role_id": "qa", "name": "QA", "type": "auditor", "profile_id": "qa"},
        {"role_id": "d1", "name": "交付经理", "type": "aggregator", "profile_id": "deliver"},
    ]
    profiles = profiles or {
        "pm": {"id": "pm", "name": "产品经理"},
        "eng1": {"id": "eng1", "name": "工程师甲"},
        "eng2": {"id": "eng2", "name": "工程师乙"},
        "qa": {"id": "qa", "name": "QA"},
        "deliver": {"id": "deliver", "name": "交付经理"},
    }
    calls = []
    stored = {}

    def store(tid, t):
        stored[tid] = dict(t)

    async def fake_runner(profile, instruction, ctx):
        calls.append((profile["id"], instruction))
        pid = profile["id"]
        if "audit" in instruction or pid == "qa":
            # 审计岗位：默认通过（测试里可覆盖）
            return '{"verdict": "pass", "comments": {}}'
        if pid == "pm":
            return ('[\n{"assignee": "eng1", "instruction": "写营收部分", '
                    '"acceptance": "数据准确"},'
                    '\n{"assignee": "eng2", "instruction": "写风险部分", '
                    '"acceptance": "风险点齐全"}\n]')
        if pid == "deliver":
            return "【汇总】营收+风险整合完成。"
        # executor
        return f"[工件-{pid}] 完成 {instruction[:20]}"

    ctx = oe.OrgContext(
        task=task, roles=roles, profiles=profiles,
        room=room or {"title": "测试群"}, store=store,
        append_message=lambda *a, **k: None,
    )
    return ctx, calls, stored


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ─────────────────────────── 用例 ───────────────────────────

def test_full_flow_pass():
    ctx2, calls2, stored2 = make_ctx()
    task = run(oe.run_org_task(ctx2, lambda p, i, c: _runner_default(p, i, c, calls2)))
    assert task["status"] == "delivered"
    assert len(task["plan"]) == 2
    assert task["plan"][0]["assignee"] == "eng1"
    assert task["plan"][1]["assignee"] == "eng2"
    # 并行执行两个 executor 都跑了
    pids = [c[0] for c in calls2]
    assert "eng1" in pids and "eng2" in pids
    # 审计通过，汇总有 final_output
    assert task["audit_log"][-1]["verdict"] == "pass"
    assert "汇总" in task["final_output"]
    # 工件齐全
    assert task["artifacts"].get("s1") and task["artifacts"].get("s2")


async def _runner_default(profile, inst, ctx, calls=None):
    pid = profile.get("id") if isinstance(profile, dict) else profile
    if calls is not None:
        calls.append((pid, inst))
    if inst.startswith("[组织流水线 · 审计]") or pid == "qa":
        return '{"verdict": "pass", "comments": {}}'
    if pid == "pm":
        return ('[{"assignee": "eng1", "instruction": "写营收部分", "acceptance": "数据准确"},'
                '{"assignee": "eng2", "instruction": "写风险部分", "acceptance": "风险点齐全"}]')
    if pid == "deliver":
        return "【汇总】整合完成。"
    return f"[工件-{pid}] 完成"


def test_audit_reject_then_pass():
    """流水交叉审计：eng1 第 1 轮被打回（意见精确到子任务）→ 只重做 eng1 →
    第 2 轮通过；eng2 链独立且不受影响。"""
    ctx, calls, stored = make_ctx()
    # 审 s1(写营收)第 1 次 reject，第 2 次 pass；s2 恒 pass
    s1_audits = {"n": 0}
    eng1_calls = []
    eng2_calls = []

    async def runner(profile, instruction, ctx):
        pid = profile["id"]
        if pid == "eng1":
            eng1_calls.append(instruction)
        if pid == "eng2":
            eng2_calls.append(instruction)
        if pid == "qa":
            if "写营收" in instruction:
                s1_audits["n"] += 1
                if s1_audits["n"] == 1:
                    return '{"verdict": "reject", "comment": "营收数据缺同比"}'
            return '{"verdict": "pass", "comment": ""}'
        if pid == "pm":
            return '[{"assignee": "eng1", "instruction": "写营收", "acceptance": "含同比"},{"assignee": "eng2", "instruction": "写风险", "acceptance": "齐全"}]'
        if pid == "deliver":
            return "【汇总】完成。"
        return f"[工件-{pid}]"

    task = run(oe.run_org_task(ctx, runner))
    assert task["status"] == "delivered"
    # 两条链各审：eng1 审了 2 轮（reject→pass），eng2 审了 1 轮（pass）
    s1_log = [e for e in task["audit_log"] if e["sub_id"] == "s1"]
    s2_log = [e for e in task["audit_log"] if e["sub_id"] == "s2"]
    assert len(s1_log) == 2
    assert s1_log[0]["verdict"] == "reject"
    assert s1_log[0]["comment"] == "营收数据缺同比"
    assert s1_log[0]["executor"] == "工程师甲"
    assert s1_log[1]["verdict"] == "pass"
    assert len(s2_log) == 1 and s2_log[0]["verdict"] == "pass"
    # 核心语义：重做只发生在被打回的 eng1（执行+重做=2 次），eng2 只执行 1 次
    assert len(eng1_calls) == 2, f"eng1 应执行+重做共2次, got {len(eng1_calls)}"
    assert len(eng2_calls) == 1, f"eng2 不应被重做, got {len(eng2_calls)}"
    # eng1 第二次(重做)指令携带上一轮审计意见
    assert "营收数据缺同比" in eng1_calls[1] or "审计意见" in eng1_calls[1]
    # eng2 指令不含审计意见（从未被打回）
    assert "审计意见" not in eng2_calls[0]


def test_audit_exceed_rounds_reports_boss():
    """单子任务连续打回超上限 → 该链上报，整任务 rejected（不无限循环）。"""
    ctx, calls, stored = make_ctx()
    count = {"n": 0}

    async def runner(profile, instruction, ctx):
        pid = profile["id"]
        if pid == "qa":
            if "写营收" in instruction:
                count["n"] += 1
                return '{"verdict": "reject", "comment": "还是不行"}'
            return '{"verdict": "pass", "comment": ""}'
        if pid == "pm":
            return '[{"assignee": "eng1", "instruction": "写营收", "acceptance": "含同比"},{"assignee": "eng2", "instruction": "写风险", "acceptance": "齐全"}]'
        if pid == "deliver":
            return "汇总"
        return "工件"

    task = run(oe.run_org_task(ctx, runner))
    assert task["status"] == "rejected"
    # eng1 链审计轮数 = 上限 2（第1轮 reject→重做→第2轮 reject→上报），不再第3轮
    assert count["n"] == oe.MAX_AUDIT_ROUNDS
    s1_log = [e for e in task["audit_log"] if e["sub_id"] == "s1"]
    assert len(s1_log) == oe.MAX_AUDIT_ROUNDS


def test_no_dispatcher_boss_direct():
    """无分派者 → 老板直派全体执行者。"""
    roles = [
        {"role_id": "e1", "name": "工程师甲", "type": "executor", "profile_id": "eng1"},
        {"role_id": "e2", "name": "工程师乙", "type": "executor", "profile_id": "eng2"},
        {"role_id": "qa", "name": "QA", "type": "auditor", "profile_id": "qa"},
    ]
    profiles = {
        "eng1": {"id": "eng1", "name": "工程师甲"},
        "eng2": {"id": "eng2", "name": "工程师乙"},
        "qa": {"id": "qa", "name": "QA"},
    }
    ctx = oe.OrgContext(task=make_task(), roles=roles, profiles=profiles,
                        room={"title": "直派群"},
                        store=lambda *a, **k: None,
                        append_message=lambda *a, **k: None)
    calls = []

    async def runner(profile, instruction, ctx):
        calls.append(profile["id"])
        if profile["id"] == "qa":
            return '{"verdict": "pass", "comments": {}}'
        return f"工件-{profile['id']}"

    task = run(oe.run_org_task(ctx, runner))
    assert task["status"] == "delivered"
    assert len(task["plan"]) == 2  # 两个执行者各一
    assert "eng1" in calls and "eng2" in calls


def test_no_auditor_passes_direct():
    """无审计岗位 → 直接汇总（小作坊）。"""
    roles = [
        {"role_id": "e1", "name": "工程师", "type": "executor", "profile_id": "eng1"},
        {"role_id": "d1", "name": "交付", "type": "aggregator", "profile_id": "deliver"},
    ]
    profiles = {
        "eng1": {"id": "eng1", "name": "工程师"},
        "deliver": {"id": "deliver", "name": "交付"},
    }
    ctx = oe.OrgContext(task=make_task(), roles=roles, profiles=profiles,
                        room={"title": "小作坊"},
                        store=lambda *a, **k: None,
                        append_message=lambda *a, **k: None)
    calls = []

    async def runner(profile, instruction, ctx):
        calls.append(profile["id"])
        if profile["id"] == "deliver":
            return "汇总成果"
        return "工件"

    task = run(oe.run_org_task(ctx, runner))
    assert task["status"] == "delivered"
    assert task["audit_log"] == []  # 无审计
    assert task["final_output"] == "汇总成果"


def test_no_aggregator_concats():
    """无汇总者 → 拼接全部工件。"""
    roles = [
        {"role_id": "e1", "name": "工程师", "type": "executor", "profile_id": "eng1"},
    ]
    profiles = {"eng1": {"id": "eng1", "name": "工程师"}}
    ctx = oe.OrgContext(task=make_task(), roles=roles, profiles=profiles,
                        room={"title": "单人"},
                        store=lambda *a, **k: None,
                        append_message=lambda *a, **k: None)

    async def runner(profile, instruction, ctx):
        return "唯一工件"

    task = run(oe.run_org_task(ctx, runner))
    assert task["status"] == "delivered"
    assert "唯一工件" in task["final_output"]


def test_no_executor_uses_other_roles():
    """缺 executor 岗位 → 由已有岗位（分派/审计/汇总）agent 兼任执行者，不卡死。"""
    roles = [
        {"role_id": "sec", "name": "分派", "type": "dispatcher", "profile_id": "sec"},
    ]
    profiles = {"sec": {"id": "sec", "name": "秘书"}}
    ctx = oe.OrgContext(task=make_task(), roles=roles, profiles=profiles,
                        room={"title": "只拉秘书群"},
                        store=lambda *a, **k: None,
                        append_message=lambda *a, **k: None)
    calls = []

    async def runner(profile, instruction, ctx):
        calls.append(profile["id"])
        return f"工件-{profile['id']}"

    task = run(oe.run_org_task(ctx, runner))
    # 不再 rejected，而是让秘书兼任执行者跑完（无审计/汇总 → 直通拼接）
    assert task["status"] == "delivered"
    assert len(task["plan"]) == 1
    assert "sec" in calls


def test_parse_plan_fallback():
    """分派者输出坏格式 → 均分兜底（每个执行者领任务）。"""
    ctx, calls, stored = make_ctx()

    async def runner(profile, instruction, ctx):
        if profile["id"] == "pm":
            return "我不会JSON，就随便说说"  # 坏格式
        if profile["id"] == "qa":
            return '{"verdict": "pass", "comments": {}}'
        if profile["id"] == "deliver":
            return "汇总"
        return f"工件-{profile['id']}"

    task = run(oe.run_org_task(ctx, runner))
    assert task["status"] == "delivered"
    assert len(task["plan"]) == 2  # 兜底两个执行者
    assert task["plan"][0]["assignee"] == "eng1"
    assert task["plan"][1]["assignee"] == "eng2"


def test_boss_approve_and_reject():
    """老板验收：通过→done；打回→dispatched 带意见重跑。"""
    ctx, calls, stored = make_ctx()
    # 先跑完整流程到 delivered
    async def runner(profile, instruction, ctx):
        if profile["id"] == "qa":
            return '{"verdict": "pass", "comments": {}}'
        if profile["id"] == "pm":
            return '[{"assignee": "eng1", "instruction": "写营收", "acceptance": "含同比"},{"assignee": "eng2", "instruction": "写风险", "acceptance": "齐全"}]'
        if profile["id"] == "deliver":
            return "【汇总】最终成果"
        return "工件"

    task = run(oe.run_org_task(ctx, runner))
    assert task["status"] == "delivered"

    # 老板通过
    task2 = oe.review_org_task(ctx, approve=True)
    assert task2["status"] == "done"

    # 新任务，老板打回
    ctx3, _, stored3 = make_ctx()
    task3 = run(oe.run_org_task(ctx3, runner))
    assert task3["status"] == "delivered"
    task4 = oe.review_org_task(ctx3, approve=False, comment="缺少图表")
    assert task4["status"] == "dispatched"
    assert "缺少图表" in task4["brief"]
    assert task4["plan"] == []
    assert task4["final_output"] == ""


def test_templates_complete():
    """模板完整性：company/court 岗位齐全、类型合法。"""
    assert set(oe.ORG_TEMPLATES.keys()) >= {"company", "court"}
    for key, tmpl in oe.ORG_TEMPLATES.items():
        roles = tmpl["roles"]
        types = {r["type"] for r in roles}
        assert oe.ROLE_DISPATCHER in types, f"{key} 缺分派者"
        assert oe.ROLE_EXECUTOR in types, f"{key} 缺执行者"
        assert oe.ROLE_AUDITOR in types, f"{key} 缺审计者"
        assert oe.ROLE_AGGREGATOR in types, f"{key} 缺汇总者"
        for r in roles:
            assert r["role_id"] and r["name"] and r["type"] in oe.ALL_ROLE_TYPES


def test_parse_secretary_plan_ok():
    """秘书返回合法组织 JSON → 解析出岗位（type/profile_id 校验）。"""
    cands = {
        "eng1": {"id": "eng1", "name": "快模型甲", "provider": "p1", "model": "m1"},
        "qa": {"id": "qa", "name": "强模型审计", "provider": "p2", "model": "m2"},
        "agg": {"id": "agg", "name": "汇总", "provider": "p3", "model": "m3"},
    }
    raw = ('{"title": "写季度报告", "roles": ['
           '{"role_id": "eng", "name": "工程师", "type": "executor", "profile_id": "eng1", "description": "写初稿"},'
           '{"role_id": "qa", "name": "QA", "type": "auditor", "profile_id": "qa", "description": "审质量"},'
           '{"role_id": "agg", "name": "汇总", "type": "aggregator", "profile_id": "agg", "description": "整合"}]}')
    out = oe.parse_secretary_plan(raw, cands)
    assert not out["errors"]
    assert len(out["roles"]) == 3
    assert out["roles"][0]["profile_id"] == "eng1"
    assert out["roles"][1]["type"] == oe.ROLE_AUDITOR


def test_parse_secretary_plan_rejects_unknown_or_dup():
    """秘书选了候选池外的人 / 一人多岗 → 剔除并记 error（fail-safe）。"""
    cands = {"eng1": {"id": "eng1"}, "qa": {"id": "qa"}}
    raw = ('{"title": "t", "roles": ['
           '{"role_id": "a", "name": "A", "type": "executor", "profile_id": "ghost"},'
           '{"role_id": "b", "name": "B", "type": "auditor", "profile_id": "eng1"},'
           '{"role_id": "c", "name": "C", "type": "aggregator", "profile_id": "eng1"}]}')
    out = oe.parse_secretary_plan(raw, cands)
    assert len(out["errors"]) >= 2  # ghost 不在池 + eng1 重复
    assert all(r["profile_id"] != "ghost" for r in out["roles"])
    # ghost 被剔除；eng1 一人兼 auditor+aggregator 两岗（小组织允许）
    assert len(out["roles"]) == 2


def test_parse_secretary_plan_bad_json_fail_safe():
    """秘书返回垃圾 → 空 roles + error（路由层提示重试/手动配）。"""
    out = oe.parse_secretary_plan("好的老板我马上办", {"eng1": {"id": "eng1"}})
    assert out["roles"] == []
    assert out["errors"]


def test_parse_secretary_plan_forge_prefix():
    """秘书用 forge:角色名 造神 → 保留岗位并标记 forge=True（不剔除非候选池）。"""
    cands = {"eng1": {"id": "eng1", "name": "甲"}}
    raw = ('{"title": "做财务报表", "roles": ['
           '{"role_id": "eng", "name": "研究员", "type": "executor", "profile_id": "eng1", "description": "写正文"},'
           '{"role_id": "fin", "name": "财务分析师", "type": "auditor", "profile_id": "forge:财务分析师", "description": "审计报表数据准确性"}]}')
    out = oe.parse_secretary_plan(raw, cands)
    assert not out["errors"]
    assert len(out["roles"]) == 2
    fin = out["roles"][1]
    assert fin["profile_id"] == "forge:财务分析师"
    assert fin["forge"] is True
    # 非 forge 岗位 forge 标志应为 False
    assert out["roles"][0]["forge"] is False


def test_secretary_instruction_mentions_forge():
    """秘书指令需提示：候选池没人时可 forge: 前缀造神。"""
    inst = oe.SECRETARY_INSTRUCTION
    assert "forge:" in inst


def test_secretary_instruction_mentions_balance():
    """秘书指令必须包含三平衡选人要求 + 候选清单占位。"""
    inst = oe.SECRETARY_INSTRUCTION
    assert "经济-质量-效率" in inst or "经济" in inst
    assert "auditor" in inst and "executor" in inst
    assert "{candidates}" in inst and "{brief}" in inst
    # 候选清单格式化可用
    cl = oe.build_candidate_list({"eng1": {"id": "eng1", "name": "甲", "transport": "native", "provider": "p", "model": "m"}})
    assert "eng1" in cl and "甲" in cl


# ───────────────── token 级流式（P0 体验 step ②）─────────────────

def test_run_agent_streams_tokens_and_closes():
    """native 执行者：增量回调带执行者标识，且收尾必发 done=True。

    断言意义：done 缺失会让前端「正在生成」气泡永久悬挂，故正常/异常两条路径都覆盖。
    """
    events = []

    def _stream(delta, ref=None, done=False):
        events.append((delta, ref, done))

    async def _streaming_runner(profile, instruction, ctx):
        cb = (ctx or {}).get("stream_callback")
        assert cb is not None, "runner 必须收到 stream_callback"
        for piece in ("营收", "部分", "完成"):
            cb(piece)
        return "[工件-eng1] 完成"

    ctx = oe.OrgContext(
        task=make_task(), roles=[], profiles={"eng1": {"id": "eng1"}},
        room={"title": "t"}, store=lambda tid, t: None,
        append_message=lambda *a, **k: None,
        stream=_stream,
    )
    out = run(oe._run_agent(ctx, {"id": "eng1"}, "写营收", _streaming_runner))
    assert out == "[工件-eng1] 完成"
    # 增量按序透传，且带执行者标识（并行多执行者靠 ref 区分）
    deltas = [(d, r) for d, r, done in events if not done and d]
    assert deltas == [("营收", "eng1"), ("部分", "eng1"), ("完成", "eng1")]
    # 收尾 done 恰好一次
    dones = [e for e in events if e[2] is True]
    assert len(dones) == 1 and dones[0][1] == "eng1"


def test_run_agent_stream_closes_on_failure():
    """执行异常：仍必须补发 done=True（否则前端流式气泡悬挂）。"""
    events = []

    async def _boom(profile, instruction, ctx):
        cb = (ctx or {}).get("stream_callback")
        if cb:
            cb("半截")
        raise RuntimeError("模型炸了")

    ctx = oe.OrgContext(
        task=make_task(), roles=[], profiles={"eng1": {"id": "eng1"}},
        room={"title": "t"}, store=lambda tid, t: None,
        append_message=lambda *a, **k: None,
        stream=lambda delta, ref=None, done=False: events.append((delta, ref, done)),
    )
    out = run(oe._run_agent(ctx, {"id": "eng1"}, "写", _boom))
    assert out.startswith("[执行失败]")
    assert any(e[2] is True for e in events), "异常路径也必须收尾 done"


def test_run_agent_without_stream_is_noop():
    """未注入 stream（兼容旧构造点）：不报错，runner 收到 None 回调。fail-open。"""
    seen = {}

    async def _runner(profile, instruction, ctx):
        seen["cb"] = (ctx or {}).get("stream_callback")
        return "ok"

    ctx = oe.OrgContext(
        task=make_task(), roles=[], profiles={"eng1": {"id": "eng1"}},
        room={"title": "t"}, store=lambda tid, t: None,
        append_message=lambda *a, **k: None,
    )  # 故意不传 stream
    assert run(oe._run_agent(ctx, {"id": "eng1"}, "写", _runner)) == "ok"
    assert seen["cb"] is None


# ─────────────────────────── LLM 输出解析层（2026-09-09 补覆盖）───────────────────────────
# 背景：run_org_task 的全部决策都建立在这几个解析函数上，但它们此前 0 直接覆盖。
# 这些函数的共同契约是 **fail-open**（坏输入不抛异常、默认 pass），故测试重点在
# 「坏输入不炸 + 好输入不失真 + 越界数据被过滤」，而非只测 happy path。

class TestExtractJson:
    """_extract_json：从 LLM 输出中抠出 JSON（容忍围栏/杂文）。"""

    def test_fenced_json_block(self):
        raw = '```json\n{"verdict": "pass"}\n```'
        assert oe._extract_json(raw) == '{"verdict": "pass"}'

    def test_surrounded_by_prose(self):
        # 前后杂文：LLM 最爱说"好的，结果如下：... 希望对您有帮助"
        raw = '好的，结果如下：\n{"verdict": "reject", "comment": "改A"}\n希望对您有帮助'
        assert oe._extract_json(raw) == '{"verdict": "reject", "comment": "改A"}'

    def test_array_form(self):
        raw = '说明文字 [{"a": 1}, {"a": 2}] 结束'
        assert oe._extract_json(raw) == '[{"a": 1}, {"a": 2}]'

    def test_no_json_returns_empty(self):
        assert oe._extract_json("这里没有任何 json") == ""

    def test_empty_input_returns_empty(self):
        assert oe._extract_json("") == ""
        assert oe._extract_json(None) == ""


class TestParseAudit:
    """_parse_audit：整批审计 → (verdict, comments)，comments 必须落在 plan 内。"""

    def test_filters_comments_outside_plan(self):
        # plan 只有 s1/s2 → s9 的意见必须被丢弃（否则会拿去改不存在的子任务）
        plan = [{"sub_id": "s1"}, {"sub_id": "s2"}]
        raw = '{"verdict": "reject", "comments": {"s1": "数据不准", "s9": "越界意见"}}'
        verdict, comments = oe._parse_audit(raw, plan)
        assert verdict == "reject"
        assert comments == {"s1": "数据不准"}

    def test_bad_json_fails_open_to_pass(self):
        plan = [{"sub_id": "s1"}]
        assert oe._parse_audit("这不是 json", plan) == ("pass", {})

    def test_unknown_verdict_normalized_to_pass(self):
        plan = [{"sub_id": "s1"}]
        verdict, _ = oe._parse_audit('{"verdict": "maybe"}', plan)
        assert verdict == "pass"

    def test_comments_not_dict_becomes_empty(self):
        plan = [{"sub_id": "s1"}]
        verdict, comments = oe._parse_audit('{"verdict": "reject", "comments": ["a"]}', plan)
        assert verdict == "reject"
        assert comments == {}


class TestParseSingleAudit:
    """_parse_single_audit：单子任务交叉审计 → (verdict, comment)。"""

    def test_plain_comment_field(self):
        assert oe._parse_single_audit('{"verdict": "reject", "comment": "改A"}') == ("reject", "改A")

    def test_legacy_comments_dict_takes_first(self):
        # 旧格式用 comments dict → 取第一条非空值
        verdict, comment = oe._parse_single_audit(
            '{"verdict": "reject", "comments": {"s1": "第一条", "s2": "第二条"}}')
        assert verdict == "reject"
        assert comment == "第一条"

    def test_bad_json_fails_open(self):
        assert oe._parse_single_audit("坏输出") == ("pass", "")


class TestFmtComments:
    """_fmt_comments：审计意见 → 人类可读串（含 80 字截断）。"""

    def test_empty_returns_placeholder(self):
        assert oe._fmt_comments({}) == "（无具体意见，全组重做）"

    def test_joins_with_semicolon(self):
        out = oe._fmt_comments({"s1": "数据不准", "s2": "缺风险"})
        assert out == "s1: 数据不准; s2: 缺风险"

    def test_truncates_long_comment_to_80(self):
        out = oe._fmt_comments({"s1": "长" * 200})
        # "s1: " (4) + 80 字截断
        assert out == "s1: " + "长" * 80

    def test_drops_empty_values(self):
        # 空意见不该渲染成 "s1: " 这种噪声
        assert oe._fmt_comments({"s1": "", "s2": "有意见"}) == "s2: 有意见"


class TestExecInstruction:
    """_exec_instruction：子任务执行指令拼装。"""

    def test_contains_all_four_fields(self):
        task = {"title": "季度报告", "brief": "老板原始指令"}
        sub = {"instruction": "写营收", "acceptance": "数据准确"}
        out = oe._exec_instruction(task, sub)
        assert "季度报告" in out and "写营收" in out
        assert "数据准确" in out and "老板原始指令" in out

    def test_missing_fields_do_not_crash(self):
        # 缺字段走 .get 默认值，不应 KeyError
        out = oe._exec_instruction({}, {})
        assert "请直接产出" in out


class TestBuildCandidateList:
    """build_candidate_list：候选清单 + 能力诚实标注三态（P2-b 修复点）。"""

    def test_empty_profiles_returns_hint(self):
        out = oe.build_candidate_list({})
        assert "暂无其他 agent" in out

    def test_official_capability_not_marked_inferred(self):
        # 官方手写的能力是真值，不得标"(推测)"
        out = oe.build_candidate_list({
            "a1": {"id": "a1", "name": "甲", "capability_tags": ["code"],
                   "capability_source": "official"},
        })
        assert "能力：code" in out
        assert "(推测)" not in out

    def test_inferred_capability_marked(self):
        out = oe.build_candidate_list({
            "a1": {"id": "a1", "name": "甲", "capability_tags": ["code"],
                   "capability_source": "inferred"},
        })
        assert "能力(推测)：code" in out

    def test_unknown_source_defaults_to_inferred_mark(self):
        # 未标注 source 时按"不是官方"处理 → 标推测（保守、不冒充真值）
        out = oe.build_candidate_list({
            "a1": {"id": "a1", "name": "甲", "capability_tags": ["code"]},
        })
        assert "能力(推测)：code" in out

    def test_no_capability_shows_unlabeled(self):
        out = oe.build_candidate_list({"a1": {"id": "a1", "name": "甲"}})
        assert "能力：未标注" in out

    def test_description_truncated_to_60(self):
        out = oe.build_candidate_list({
            "a1": {"id": "a1", "name": "甲", "description": "介" * 100},
        })
        assert "介" * 60 in out
        assert "介" * 61 not in out

    def test_row_shape_pid_name_transport_model(self):
        out = oe.build_candidate_list({
            "a1": {"id": "a1", "name": "甲", "transport": "acp",
                   "provider": "openai", "model": "gpt-5"},
        })
        assert out == "a1: 甲 | acp | openai/gpt-5 | 能力：未标注"


if __name__ == "__main__":
    # 简易独立运行入口
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
            passed += 1
        except Exception as exc:
            print(f"FAIL {fn.__name__}: {exc}")
    print(f"\n{passed}/{len(fns)} passed")
