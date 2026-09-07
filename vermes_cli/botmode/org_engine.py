"""⑭ Agent Org 组织流水线状态机（纯逻辑，2026-09-07）。

群=组织=生产单元：建群配岗位表（org_roles），老板下达任务（org_tasks），
状态机驱动 拆解→逐子任务执行+交叉审计→汇总→交付，老板验收拍板。

本模块**零路由依赖**：只定义状态机 + 每一步的执行编排；agent 怎么跑
（原生/ACP/CLI）由调用方注入 runner。方便纯逻辑单测。

岗位类型：
    dispatcher  分派者（拆解任务 → 产出 plan）
    executor    执行者（领活 → 产出工件）
    auditor     审计者（交叉验收执行者工件 → 通过 / 打回附意见）
    aggregator  汇总者（汇总通过工件 → final_output 交付老板）

执行模型（2026-09-07 董董修正）：不是“全部干完一次性终审”，而是
**逐子任务流水交叉审计** —— 每完成一部分即有另一 agent 交叉审计（像我们做
Vermes 路线：每完成一部分就让另一 agent 同步交叉审计）。多条子任务链
并行，每条链独立轮次：执行→审计→通过才落定→打回重做（≤上限）→再审。
审计者必须 ≠ 执行者（自审无意义；仅一 agent 时退化为直通）。

秘书模式（2026-09-07 16:32 董董拍板·傻瓜式懒人路径）：用户懒，只拉一个
agent 进群 = 老板秘书。用户直接提需求，秘书按需搭建组织框架（产出岗位 JSON：
选谁当执行/审计/汇总，标注各岗选用的 agent），系统自动把选中的 agent 拉进群
并落岗位表，然后跑标准流水线，最终成果交付用户。预设模板（company/court）
保留但只是手动选项——人类社会中一线执行、中高层、老板角色各不相同，落到
agent 就是采用的 LLM 各不相同：讲究 经济(executor 可用快/便宜模型)、质量
(auditor/aggregator 需强模型)、效率(人数够用即止) 三平衡。

状态机：
    dispatched → planning → executing/auditing(逐子任务链) → aggregating
      → delivered → done(老板通过)
                     → rejected(老板打回 / 子任务超审计上限上报裁决)
    收敛靠「状态 + 老板验收」，不靠轮次上限；单子任务审计打回有
    MAX_AUDIT_ROUNDS 上限（默认 2：第1轮不过→重做→第2轮再审；再不过→
    上报老板裁决），人在环上拍板。
"""

import asyncio
import json
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional

# 状态常量
ST_DISPATCHED = "dispatched"      # 已下达，待拆解
ST_PLANNING = "planning"          # 拆解中
ST_EXECUTING = "executing"        # 执行中（并行）
ST_AUDITING = "auditing"          # 审计中
ST_REWORKING = "reworking"        # 打回重做中
ST_AGGREGATING = "aggregating"    # 汇总中
ST_DELIVERED = "delivered"        # 已交付，待老板验收
ST_DONE = "done"                  # 老板验收通过
ST_REJECTED = "rejected"          # 老板打回（或审计超限上报裁决）

# 岗位类型
ROLE_DISPATCHER = "dispatcher"
ROLE_EXECUTOR = "executor"
ROLE_AUDITOR = "auditor"
ROLE_AGGREGATOR = "aggregator"
ALL_ROLE_TYPES = (ROLE_DISPATCHER, ROLE_EXECUTOR, ROLE_AUDITOR, ROLE_AGGREGATOR)
# 秘书输出岗位 JSON 允许的类型（与标准岗位一致）
SECRETARY_ROLE_TYPES = ALL_ROLE_TYPES

# 默认审计打回上限（老板裁决阈值）
MAX_AUDIT_ROUNDS = 2

# runner 类型：async (profile, instruction, ctx) -> str
AgentRunner = Callable[[Dict, str, Dict], Awaitable[str]]

# ─────────────────────────── 模板 ───────────────────────────

ORG_TEMPLATES: Dict[str, Dict[str, Any]] = {
    # 公司 · 产品研发
    "company": {
        "name": "公司 · 产品研发",
        "description": "老板 → 产品经理拆解 → 工程师并行开发 → QA 审计 → 交付经理汇总",
        "roles": [
            {"role_id": "pm", "name": "产品经理", "type": ROLE_DISPATCHER,
             "description": "拆解需求为可执行子任务，分配给工程师，把控范围与优先级"},
            {"role_id": "eng1", "name": "工程师甲", "type": ROLE_EXECUTOR,
             "description": "负责核心模块开发，产出代码/文档工件"},
            {"role_id": "eng2", "name": "工程师乙", "type": ROLE_EXECUTOR,
             "description": "负责配套模块开发，产出代码/文档工件"},
            {"role_id": "qa", "name": "QA 审计", "type": ROLE_AUDITOR,
             "description": "验收工件质量：通过 / 打回附具体修改意见"},
            {"role_id": "deliver", "name": "交付经理", "type": ROLE_AGGREGATOR,
             "description": "汇总全部通过工件，整理为交付成果呈报老板"},
        ],
    },
    # 朝廷 · 奏折流水
    "court": {
        "name": "朝廷 · 奏折流水",
        "description": "皇帝 → 丞相拆解 → 六部执行 → 御史审计 → 呈报官汇总",
        "roles": [
            {"role_id": "chancellor", "name": "丞相", "type": ROLE_DISPATCHER,
             "description": "领圣旨拆解为政令，分派六部办理，统筹全局"},
            {"role_id": "ministry1", "name": "吏部", "type": ROLE_EXECUTOR,
             "description": "掌官员考核/人事相关事务"},
            {"role_id": "ministry2", "name": "户部", "type": ROLE_EXECUTOR,
             "description": "掌户籍/财政/民生相关事务"},
            {"role_id": "ministry3", "name": "兵部", "type": ROLE_EXECUTOR,
             "description": "掌军事/边防相关事务"},
            {"role_id": "censor", "name": "御史大夫", "type": ROLE_AUDITOR,
             "description": "监察百官，审计政令执行质量，劾奏不当"},
            {"role_id": "herald", "name": "呈报官", "type": ROLE_AGGREGATOR,
             "description": "汇总各部结果，拟奏折呈报圣上"},
        ],
    },
}

# 岗位在任务流程中是否需要 profile（用于缺人时的报错）
ROLE_TYPE_LABELS = {
    ROLE_DISPATCHER: "分派",
    ROLE_EXECUTOR: "执行",
    ROLE_AUDITOR: "审计",
    ROLE_AGGREGATOR: "汇总",
}

# 秘书模式：群内唯一 agent 成员即老板秘书，按需设计组织方案
SECRETARY_INSTRUCTION = (
    "[组织流水线 · 秘书设计] 你是老板的秘书，老板刚下达任务，本群还没有组织架构，"
    "由你按需搭建。\n"
    "老板任务：{brief}\n"
    "可选人员（profile_id: 名称 | 类型 | 模型 | 通路）：\n{candidates}\n"
    "请设计一个精干组织完成该任务，遵守「经济-质量-效率」三平衡：\n"
    "- 执行者(executor)：量大活多 → 用便宜/快的模型，1-2 人够用就够，经济优先\n"
    "- 审计者(auditor)：必须与执行者不同人 → 用强模型交叉审计质量\n"
    "- 汇总者(aggregator)：用强模型整合最终成果\n"
    "- 分派者(dispatcher)：复杂任务配，简单任务可不配（老板直派执行者）\n"
    "- 可选人员里没有合适角色时：profile_id 写 \"forge:角色名\"（如 forge:财务分析师），"
    "并在 description 里写清该角色专长人设，系统会自动造一个新 agent 坐这个岗位\n"
    "严格按 JSON 输出（不要多余文字）：\n"
    '{{"title": "<任务短名>", "roles": [{{"role_id": "<短id>", "name": "<岗位名>", '
    '"type": "dispatcher|executor|auditor|aggregator", '
    '"profile_id": "<从上面可选人员中选，或 forge:角色名>", '
    '"description": "<该岗位在此任务中的职责（forge 时=新人设）>"}}]}}\n'
    "每个岗位必须选一个可选人员（profile_id 来自上面列表，或 forge: 前缀造新）；人数精干不冗余；"
    "默认至少 1 执行 + 1 审计（不同人）+ 1 汇总。"
)


def parse_secretary_plan(raw: str, candidates: Dict[str, Dict]) -> Dict:
    """解析秘书产出的组织方案 JSON → {title, roles, errors}。

    容错：坏 JSON → roles=[]（路由层回退：让秘书重试或提示用户手动配）；
    profile_id 不在候选池且非 forge: 前缀 → 该岗位剔除并记 error；
    forge: 前缀（如 forge:财务分析师）→ 保留，profile_id 原样透传，由路由层
    自动造神（用 description 做人设）。
    """
    text = _extract_json(raw)
    out: Dict[str, Any] = {"title": "", "roles": [], "errors": []}
    if not text:
        out["errors"].append("秘书未返回可解析的 JSON")
        return out
    try:
        data = json.loads(text)
    except Exception:
        out["errors"].append("秘书返回的 JSON 无法解析")
        return out
    if not isinstance(data, dict):
        out["errors"].append("秘书返回的不是 JSON 对象")
        return out
    out["title"] = str(data.get("title") or "")[:50]
    roles = data.get("roles")
    if not isinstance(roles, list):
        out["errors"].append("方案缺少 roles 列表")
        return out
    seen = set()
    for r in roles:
        if not isinstance(r, dict):
            continue
        pid = str(r.get("profile_id") or "").strip()
        rtype = str(r.get("type") or "").strip()
        rid = str(r.get("role_id") or "").strip() or f"r{len(out['roles'])+1}"
        if rtype not in SECRETARY_ROLE_TYPES:
            out["errors"].append(f"岗位 {rid} 类型非法: {rtype}")
            continue
        is_forge = pid.startswith("forge:")
        if not is_forge and pid not in candidates:
            out["errors"].append(f"岗位 {rid} 选的 {pid} 不在候选池")
            continue
        if pid in seen:
            out["errors"].append(f"{pid} 被重复安排多个岗位（一人兼多岗）")
        seen.add(pid)
        out["roles"].append({
            "role_id": rid,
            "name": str(r.get("name") or f"{rtype}-{rid}")[:30],
            "type": rtype,
            "profile_id": pid,
            "description": str(r.get("description") or "")[:200],
            "forge": is_forge,  # 路由层据此自动造神
        })
    return out


def build_candidate_list(profiles: Dict[str, Dict]) -> str:
    """候选人员清单文本（供秘书设计时挑选；profile 含 provider/model/transport）。"""
    if not profiles:
        return "(暂无其他 agent 可调用——若群内只有你，可如实说明无法组队)"
    rows = []
    for pid, p in profiles.items():
        rows.append(
            f"{pid}: {p.get('name') or pid} | "
            f"{p.get('transport') or 'native'} | "
            f"{p.get('provider') or '?'}/{(p.get('model') or '?')}"
        )
    return "\n".join(rows)


# ─────────────────────────── 流程上下文 ───────────────────────────

class OrgContext:
    """一次任务执行的可变上下文：任务读写 + 房间消息留痕回调。

    调用方（路由层）实现 store/load/append_message，本引擎只编排。
    """

    def __init__(self, *, task: Dict, roles: List[Dict],
                 profiles: Dict[str, Dict], room: Optional[Dict] = None,
                 store: Callable[[str, Dict], None],
                 append_message: Optional[Callable[[str, str, Optional[str], str], None]] = None,
                 log: Optional[Callable[[str], None]] = None):
        self.task = task
        self.roles = roles
        self.profiles = profiles          # profile_id -> profile
        self.room = room or {}
        self.store = store                # store(task_id, updated_task)
        self.append_message = append_message or (lambda *a, **k: None)
        self.log = log or (lambda s: None)

    # 便捷：按岗位类型取第一个有 profile 的岗位
    def _first_bound(self, role_type: str) -> Optional[Dict]:
        for r in self.roles:
            if r.get("type") == role_type and r.get("profile_id"):
                return r
        return None

    def role_profiles(self, role_type: str) -> List[Dict]:
        out = []
        for r in self.roles:
            if r.get("type") == role_type and r.get("profile_id"):
                p = self.profiles.get(r["profile_id"])
                if p:
                    out.append((r, p))
        return out

    def executor_profiles(self) -> List[Dict]:
        return self.role_profiles(ROLE_EXECUTOR)


# ─────────────────────────── 编排 helpers ───────────────────────────

def _announce(ctx: OrgContext, content: str, author_ref: Optional[str] = None) -> None:
    """流程节点留痕（落到房间消息 = 前端看到生产流水线）。"""
    try:
        ctx.append_message(ctx.task["room_id"], "system", author_ref, content)
    except Exception:
        pass


def _set_status(ctx: OrgContext, status: str) -> None:
    ctx.task["status"] = status
    ctx.task["updated_at"] = time.time()
    try:
        ctx.store(ctx.task["id"], ctx.task)
    except Exception:
        pass


async def _run_agent(ctx: OrgContext, profile: Dict, instruction: str,
                     runner: AgentRunner) -> str:
    """跑一个 agent 岗位，产出文本（工件/意见/汇总）。"""
    try:
        return (await runner(profile, instruction, {
            "task": ctx.task, "room": ctx.room, "roles": ctx.roles,
        })) or ""
    except Exception as exc:  # fail-open：单岗位失败不拖垮整条流水线
        ctx.log(f"[Org] role run failed profile={profile.get('id')}: {exc}")
        return f"[执行失败] {exc}"


# ─────────────────────────── 主状态机 ───────────────────────────

async def run_org_task(
    ctx: OrgContext,
    runner: AgentRunner,
    *,
    max_audit_rounds: int = MAX_AUDIT_ROUNDS,
) -> Dict:
    """驱动一个任务走完整流程，返回最终 task 字典。

    流程（每步产物落 ctx.task，留痕到房间）：
      1. 拆解   dispatcher → plan [{sub_id, assignee, instruction}]
      2. 执行   executor×N 并行 → artifacts
      3. 审计   auditor → verdict pass/reject + comments（打回精确到子任务）
      4. 汇总   aggregator → final_output
      5. 交付   status=delivered，等老板验收（路由层另调 review_org_task）
    老板验收不在本函数内（人在环上），另见 review_org_task()。
    """
    task = ctx.task
    room = ctx.room
    tid = task["id"]

    # ── 1. 拆解 ──
    _set_status(ctx, ST_PLANNING)
    dispatcher_roles = ctx.role_profiles(ROLE_DISPATCHER)
    exec_roles = ctx.executor_profiles()
    _announce(ctx, f"📋 [{tid}] 任务下达：{task.get('title', '')}", None)
    _announce(ctx, f"📋 [{tid}] 拆解中（分派者：{dispatcher_roles[0][0]['name'] if dispatcher_roles else '老板直派'}）…")

    plan: List[Dict] = []
    if dispatcher_roles:
        role, prof = dispatcher_roles[0]
        exec_names = "、".join(r["name"] for r, _ in exec_roles) or "（无执行者）"
        inst = (
            f"[组织流水线 · 分派] 你是{role['name']}。老板任务：{task.get('brief', '')}\n"
            f"团队执行者：{exec_names}\n"
            f"请把任务拆解为 1-{max(1, len(exec_roles))} 个明确子任务，逐个分配给执行者。\n"
            f"严格按以下 JSON 数组格式输出（不要多余文字）：\n"
            f'[{{"assignee": "<执行者profile_id>", "instruction": "<给该执行者的明确指令>", '
            f'"acceptance": "<可验收标准>"}}]\n'
            f"每个执行者最多分到 1 个子任务；如任务单一，只产出 1 个子任务给最合适者。"
        )
        raw = await _run_agent(ctx, prof, inst, runner)
        plan = _parse_plan(raw, exec_roles)
        if not plan:
            # 拆解失败/格式坏 → 兜底：每个执行者分一个「处理老板任务」子任务
            _announce(ctx, f"⚠️ [{tid}] 分派产物无法解析，按执行者均分兜底。", None)
            for r, p in exec_roles:
                plan.append({
                    "sub_id": f"s{len(plan)+1}",
                    "assignee": p["id"],
                    "instruction": f"处理任务：{task.get('brief', '')}（你是{r['name']}，按你的职责产出）",
                    "acceptance": "产出与职责相符的完整成果",
                })
    else:
        # 无分派者 → 老板直派：全员各领全任务（并行）
        _announce(ctx, f"📋 [{tid}] 无分派岗位，老板直派全体执行者并行处理。", None)
        for r, p in exec_roles:
            plan.append({
                "sub_id": f"s{len(plan)+1}",
                "assignee": p["id"],
                "instruction": f"处理任务：{task.get('brief', '')}（你是{r['name']}，按你的职责产出）",
                "acceptance": "产出与职责相符的完整成果",
            })

    if not plan:
        _announce(ctx, f"⛔ [{tid}] 无可用执行者（群未配 executor 岗位或未绑定 agent）。", None)
        _set_status(ctx, ST_REJECTED)
        return task

    task["plan"] = plan
    _set_status(ctx, ST_EXECUTING)
    _announce(ctx, f"▶️ [{tid}] 拆解完成：{len(plan)} 个子任务逐项执行中…", None)

    # ── 2+3. 逐子任务流水：执行 → 交叉审计（董董 09-07 拍板）──
    # 不是“全部干完一次性终审”，而是每完成一部分即有另一 agent 交叉审计：
    #   executor 产出 → auditor 即时审（通过才落定）→ 不过打回重做（≤上限）
    # 多执行者并行各跑自己的链；每条链独立轮次，互不阻塞。
    artifacts: Dict[str, str] = dict(task.get("artifacts") or {})
    audit_log = list(task.get("audit_log") or [])
    auditor_roles = ctx.role_profiles(ROLE_AUDITOR)

    async def _exec_audit_chain(sub: Dict, sub_idx: int) -> None:
        """一条子任务的执行→审计链（独立轮次，打回只重做本子任务）。"""
        prof = ctx.profiles.get(sub.get("assignee") or "")
        if not prof:
            artifacts[sub["sub_id"]] = f"[无绑定 agent，子任务未执行] {sub.get('instruction','')}"
            _announce(ctx, f"⏭️ [{tid}] 子任务{sub['sub_id']} 无绑定 agent，跳过。", None)
            return
        role_name = next((r["name"] for r in ctx.roles
                          if r.get("profile_id") == sub.get("assignee")), sub.get("assignee"))
        round_no = 1
        while True:
            # 执行/重做
            _announce(ctx, f"🔧 [{tid}] {role_name} 执行子任务{sub['sub_id']}（第 {round_no} 轮）："
                       f"{sub.get('instruction','')[:50]}…", None)
            inst = _exec_instruction(task, sub)
            if round_no > 1 and isinstance(sub.get("_last_comments"), str) and sub["_last_comments"]:
                inst += (f"\n\n[上一轮审计意见（第 {round_no-1} 轮）] {sub['_last_comments']}\n"
                         f"请按意见修改后重新产出。")
            out = await _run_agent(ctx, prof, inst, runner)
            artifacts[sub["sub_id"]] = out

            # 无审计岗位 → 本链直接通过（小作坊）
            if not auditor_roles:
                return

            # 交叉审计（auditor 必须 ≠ executor，自审无意义）
            _set_status(ctx, ST_AUDITING)
            role_a, prof_a = auditor_roles[0]
            # 若唯一审计者就是执行者本人 → 退化为自审直通（无第二 agent 可交叉）
            if prof_a.get("id") == prof.get("id"):
                _announce(ctx, f"🕵️ [{tid}] {sub['sub_id']}：审计者与执行者同人，跳过交叉审计。", None)
                return
            _announce(ctx, f"🕵️ [{tid}] {role_a['name']} 交叉审计 {role_name} 的 {sub['sub_id']}（第 {round_no} 轮）…", None)
            inst_a = (
                f"[组织流水线 · 审计] 你是{role_a['name']}，交叉审计执行者{role_name}刚完成的子任务。\n"
                f"子任务指令：{sub.get('instruction','')}\n"
                f"验收标准：{sub.get('acceptance','')}\n"
                f"子任务工件：\n{out}\n"
                f"老板要求：{task.get('brief', '')}\n"
                f"严格按 JSON 输出（不要多余文字）：\n"
                f'{{"verdict": "pass" 或 "reject", "comment": "<具体修改意见，通过则空串>"}}'
            )
            raw_a = await _run_agent(ctx, prof_a, inst_a, runner)
            verdict, comment = _parse_single_audit(raw_a)
            audit_log.append({
                "sub_id": sub["sub_id"], "executor": role_name,
                "round": round_no, "auditor": role_a["name"],
                "verdict": verdict, "comment": comment,
            })
            if verdict == "pass":
                _announce(ctx, f"🟢 [{tid}] {sub['sub_id']} 第 {round_no} 轮审计通过。", None)
                return
            # 打回
            _announce(ctx, f"🔴 [{tid}] {sub['sub_id']} 第 {round_no} 轮审计打回：{comment[:80] or '（无意见）'}", None)
            if round_no >= max_audit_rounds:
                _announce(ctx, f"⛔ [{tid}] {sub['sub_id']} 超过审计上限({max_audit_rounds}轮)，上报老板裁决。", None)
                sub["_escalated"] = True
                return
            round_no += 1
            sub["_last_comments"] = comment
            _set_status(ctx, ST_REWORKING)

    await asyncio.gather(*[_exec_audit_chain(s, i) for i, s in enumerate(plan)])
    task["artifacts"] = artifacts
    task["audit_log"] = audit_log

    # 有子任务超限上报 → 整任务 rejected（老板看 audit_log 定夺）
    if any(s.get("_escalated") for s in plan):
        _announce(ctx, f"⛔ [{tid}] 存在超过审计上限的子任务，任务上报老板裁决。", None)
        _set_status(ctx, ST_REJECTED)
        return task

    _announce(ctx, f"✅ [{tid}] 全部子任务通过交叉审计。", None)


    # ── 4. 汇总 ──
    _set_status(ctx, ST_AGGREGATING)
    aggregator_roles = ctx.role_profiles(ROLE_AGGREGATOR)
    if aggregator_roles:
        role, prof = aggregator_roles[0]
        _announce(ctx, f"📦 [{tid}] 汇总中（{role['name']}）…", None)
        arts = "\n\n".join(
            f"【子任务 {s['sub_id']} · {s.get('assignee')}】{s.get('instruction','')[:60]}\n"
            f"{artifacts.get(s['sub_id']) or ''}"
            for s in plan
        )
        inst = (
            f"[组织流水线 · 汇总] 你是{role['name']}。老板任务：{task.get('brief', '')}\n"
            f"以下为全部通过审计的子任务工件：\n{arts}\n"
            f"请整合为一份完整、可直接交付给老板的最终成果（去重、衔接、补结论），"
            f"覆盖老板要求的每一个要点。"
        )
        final_out = await _run_agent(ctx, prof, inst, runner)
        task["final_output"] = final_out
    else:
        # 无汇总者 → 拼接全部工件
        _announce(ctx, f"📦 [{tid}] 无汇总岗位，直接拼接各子任务成果。", None)
        arts = "\n\n".join(
            f"【{s.get('assignee')}】{artifacts.get(s['sub_id']) or ''}" for s in plan
        )
        task["final_output"] = arts

    # ── 5. 交付 ──
    _set_status(ctx, ST_DELIVERED)
    _announce(ctx, f"📨 [{tid}] 已交付，等待老板验收。", None)
    return task


# ─────────────────────────── 老板验收 ───────────────────────────

def review_org_task(ctx: OrgContext, *, approve: bool,
                    comment: str = "", max_audit_rounds: int = MAX_AUDIT_ROUNDS) -> Dict:
    """老板验收：approve=True → done；False → 打回重跑（从拆解/执行开始）。

    comment 会注入下一轮上下文。返回更新后 task。
    """
    task = ctx.task
    if approve:
        _set_status(ctx, ST_DONE)
        _announce(ctx, f"✅ [{task['id']}] 老板验收通过，任务完成。")
        return task
    # 打回：重置审计轮次继续（保留已积累工件/意见，plan 保留）
    task["current_round"] = 1
    if comment:
        task["brief"] = (task.get("brief") or "") + f"\n[老板打回意见] {comment}"
        _announce(ctx, f"↩️ [{task['id']}] 老板打回：{comment}", None)
    else:
        _announce(ctx, f"↩️ [{task['id']}] 老板打回（无意见），重跑流程。", None)
    task["plan"] = []
    task["final_output"] = ""
    _set_status(ctx, ST_DISPATCHED)
    return task


# ─────────────────────────── 解析 helpers ───────────────────────────

def _parse_plan(raw: str, exec_roles: List) -> List[Dict]:
    """从分派者输出解析 plan。容忍 ```json 围栏与前后杂文。"""
    text = _extract_json(raw)
    if not text:
        return []
    try:
        data = json.loads(text)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    valid_ids = {p["id"] for _, p in exec_roles}
    plan = []
    for i, item in enumerate(data[: len(exec_roles)]):  # 不超执行者数
        if not isinstance(item, dict):
            continue
        assignee = str(item.get("assignee") or "")
        if assignee not in valid_ids:
            # 尽量宽松匹配：assignee 可能是 role_id 或名字 → 跳过（兜底会补）
            continue
        plan.append({
            "sub_id": f"s{i+1}",
            "assignee": assignee,
            "instruction": str(item.get("instruction") or ""),
            "acceptance": str(item.get("acceptance") or ""),
        })
    return plan


def _parse_audit(raw: str, plan: List[Dict]) -> tuple:
    """解析审计输出 → (verdict, comments dict)。坏格式默认 pass 并记录。"""
    text = _extract_json(raw)
    if not text:
        return "pass", {}
    try:
        data = json.loads(text)
    except Exception:
        return "pass", {}
    if not isinstance(data, dict):
        return "pass", {}
    verdict = str(data.get("verdict") or "pass").lower()
    comments = data.get("comments") or {}
    if not isinstance(comments, dict):
        comments = {}
    # 只保留 plan 内子任务的意见
    valid = {s["sub_id"] for s in plan}
    comments = {k: str(v) for k, v in comments.items() if k in valid}
    if verdict not in ("pass", "reject"):
        verdict = "pass"
    return verdict, comments


def _parse_single_audit(raw: str) -> tuple:
    """解析单子任务交叉审计输出 → (verdict, comment)。

    兼容 {"verdict": "pass|reject", "comment": "..."}；坏格式默认 pass（fail-open，
    不因解析失败卡死流水线）。
    """
    text = _extract_json(raw)
    if not text:
        return "pass", ""
    try:
        data = json.loads(text)
    except Exception:
        return "pass", ""
    if not isinstance(data, dict):
        return "pass", ""
    verdict = str(data.get("verdict") or "pass").lower()
    comment = str(data.get("comment") or data.get("comments") or "")
    # 兼容旧 comments dict 形式 → 取第一条
    if isinstance(data.get("comments"), dict):
        vals = [v for v in data["comments"].values() if v]
        comment = vals[0] if vals else ""
    if verdict not in ("pass", "reject"):
        verdict = "pass"
    return verdict, comment


def _extract_json(raw: str) -> str:
    """从 LLM 输出提取 JSON 文本（容忍 ```json 围栏、首尾杂文）。"""
    if not raw:
        return ""
    s = raw.strip()
    # 去掉 ```json ... ``` 围栏
    if s.startswith("```"):
        lines = s.splitlines()
        if lines and lines[0].strip().lstrip("#").strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        s = "\n".join(lines).strip()
    # 定位第一个 { 或 [ 到最后匹配
    try:
        if s.startswith("["):
            end = s.rindex("]")
            return s[: end + 1]
        start = s.index("{")
        end = s.rindex("}")
        return s[start : end + 1]
    except ValueError:
        return ""


def _fmt_comments(comments: Dict) -> str:
    if not comments:
        return "（无具体意见，全组重做）"
    return "; ".join(f"{k}: {v[:80]}" for k, v in comments.items() if v)


def _exec_instruction(task: Dict, sub: Dict) -> str:
    return (
        f"[组织流水线 · 执行] 你在任务「{task.get('title','')}」中负责子任务：\n"
        f"{sub.get('instruction','')}\n"
        f"验收标准：{sub.get('acceptance','')}\n"
        f"任务背景（老板原始指令）：{task.get('brief','')}\n"
        f"请直接产出你的完整成果（正文输出，不要 JSON）。"
    )
