"""③ Bot Mode P1 · T4/T5 端到端路由测试（固化自 /tmp/verify_botmode_t4.py）。

覆盖：
- 全链路：建房间 → @mention → 派生 session → 时间线 → 重启还原
- 空 id 校验：room_id / name / ref_id / text 入口 400（T4 交叉审计必做）
- G4 派生 key 格式（room:{norm}:agent:{id}，完整 key 供缓存清理精确匹配）
- G2 异构缓存 key 一致（T5）：profile 的 provider/model 流入 _cache_key，
  @研究助手(deepseek) 与 @编码助手(agnes) 不会命中同一缓存 agent
- P1（T4 审计）：AIAgent 构造传入 session_id=room_key，保住房间记忆连续性；
  且该房间 key 不污染单聊 sessions 表（web 模式 agent._session_db=None +
  bot 端点不调 _persist_web_turn_to_state_db + 单聊列表排除 source='web'）
- T6 G3 已决：room_update WS 契约（topic=room:{room_id} 房间级粒度 + type=room_update
  与渠道未读分流 + event∈{room_message,room_created,member_change}，为 ⑭ Bot 实验室复用基础）
- T7：BOT_MODE_ENABLED 总开关短路在最外层——关闭时 register_to 内 bot 路由整体不注册
  （404）、处理器入口另有守卫（403「bot mode disabled」）；断言「零影响单聊」是真·零
  （不建 agent + 不广播 + 单聊路由 /api/chat/models 正常响应）。
- Phase 2：GET /api/bot/rooms/{id}/members —— 完整 @ 补全的候选端点。
  T4 只有 members POST；本 GET 净新增，与 POST 共用路径、以 methods 区分；
  候选携带 T1 预留的 name/hue/avatar_seed 供前端渲染 SVG 头像与首字；
  同受最外层开关键控（关闭时不注册，非绕过口）。

测试隔离：SessionDB 重定向到临时库；AIAgent 用 FakeAgent 替换（不触网/不触 LLM）；
_resolve_model_provider 用确定性映射；_agent_cache 每用例全新实例。

可独立运行：python tests/botmode/test_room_routes.py
"""

import sys
import tempfile
import sqlite3
from pathlib import Path

sys.path.insert(0, "/Users/dongzusheng/Projects/vermes-electron")

import vermes_state
import vermes_cli.blueprints.chat as chat_bp
import run_agent  # 用于替换 AIAgent
from vermes_cli.blueprints import agent_cache as _agent_cache_mod
import pytest


# ─────────────────────────── 测试环境装配（pytest 与 __main__ 共用） ───────────────────────────

def _install_fakes(env):
    """把 SessionDB / 缓存 / AIAgent / provider 解析重定向到隔离环境。

    env 需提供：db_path (Path)、captured (list)。
    """
    chat_bp.SessionDB = lambda: vermes_state.SessionDB(env.db_path)
    chat_bp._agent_cache = _agent_cache_mod._AgentCache()

    class _FakeAgent:
        tools = ["dummy"]

        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.session_id = kwargs.get("session_id")
            env.captured.append(self)

        def chat(self, msg, stream_callback=None):
            # 兼容流式：若传入 stream_callback，模拟逐字回调（验证 delta 广播链路）
            if stream_callback is not None:
                for ch in f"[fake-agent-reply] {msg[:20]}":
                    stream_callback(ch)
            return f"[fake-agent-reply] {msg[:20]}"

    run_agent.AIAgent = _FakeAgent

    def _fake_resolve(model, provider=None):
        if provider == "deepseek":
            return ("deepseek", "https://api.deepseek.com/v1", "k-dp", model)
        if provider == "agnes":
            return ("agnes", "https://api.anthropic.com/v1", "k-ag", model)
        return ("agnes", "https://api.anthropic.com/v1", "k-ag", model or "agnes-2.0-flash")

    chat_bp._resolve_model_provider = _fake_resolve


def _client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    chat_bp.register_to(app)
    return TestClient(app)


def _seed(db):
    db.seed_default_profiles()  # 幂等：空库才插 researcher/coder


def run_acp_room(env):
    """⑭ 请神群聊协作（2026-09-07 神魔堂收口）：登堂 ACP agent 拉进群后可被 @ 且
    走 ACP dispatch（_acp_agent_chat_sync），而非原生 AIAgent 路径（后者对 acp
    profile 会因 base_url="" 构建失败 → 永远 "[agent 不可用]"）。
    """
    results = []
    def check(name, cond, extra=""):
        results.append((name, cond))
        return cond

    client = _client()
    db = vermes_state.SessionDB(env.db_path)
    _seed(db)
    # 模拟一条已登堂的 ACP agent 行（register-profile 的产物：id=a2a:{provider|name}）
    db.upsert_agent_profile({
        "id": "a2a:acp-codex", "name": "codex-acp",
        "description": "", "provider": "acp-codex", "model": "acp-codex",
        "transport": "acp", "editable": 1,
    })
    db.upsert_a2a_agent({
        "profile_id": "a2a:acp-codex", "name": "codex-acp",
        "provider": "acp-codex", "model": "acp-codex", "transport": "acp",
        "recipe": "codex-acp",
    })
    db.close()

    # monkeypatch ACP dispatch 为记录式 fake（不真 spawn CLI）
    calls = []
    def _fake_acp_chat(profile, text, timeout_seconds=900.0):
        calls.append((profile.get("id"), text))
        return f"[acp-reply] {text[:20]}"
    chat_bp._acp_agent_chat_sync = _fake_acp_chat

    r = client.post("/api/bot/rooms", json={"name": "异构房"})
    room_id = r.json().get("room_id")
    check("create room ok", r.status_code == 200 and r.json().get("ok") is True, r.text[:200])

    # 拉登堂 agent + 原生 agent 同群
    r = client.post(f"/api/bot/rooms/{room_id}/members", json={"ref_id": "a2a:acp-codex"})
    check("add acp member ok", r.status_code == 200 and r.json().get("ok") is True, r.text[:200])
    r = client.post(f"/api/bot/rooms/{room_id}/members", json={"ref_id": "researcher"})
    check("add native member ok", r.status_code == 200 and r.json().get("ok") is True, r.text[:200])

    n_before = len(env.captured)
    r = client.post(f"/api/bot/rooms/{room_id}/messages", json={"text": "@codex-acp 写个冒泡排序"})
    check("send @acp message ok", r.status_code == 200 and r.json().get("ok") is True, r.text[:300])

    # ACP 派发被调用（而非原生 agent）
    check("acp dispatch invoked", len(calls) == 1 and calls[0][0] == "a2a:acp-codex", str(calls)[:200])
    check("native agent NOT built for acp member", len(env.captured) == n_before,
          f"captured={len(env.captured)}")

    tl = client.get(f"/api/bot/rooms/{room_id}/timeline").json().get("timeline", [])
    check("acp reply in timeline", any(
        t["author_type"] == "agent" and t["author_ref"] == "a2a:acp-codex" and "[acp-reply]" in t["content"]
        for t in tl), str(tl)[-400:])
    check("no agent-unavailable system msg for acp", not any(
        t["author_type"] == "system" and "acp-codex" in t.get("content", "")
        for t in tl), str(tl)[-400:])
    return results


def run_cli_room(env):
    """⑭ 本机直连群聊（2026-09-07 神魔堂公开版收口）：无 ACP recipe 的本机已装
    CLI agent（local-connect 建 transport=cli 联系人）拉进群后可被 @ 且走
    _cli_agent_chat_sync（CLI print 直连），而非原生 AIAgent 路径。
    """
    results = []
    def check(name, cond, extra=""):
        results.append((name, cond))
        return cond

    client = _client()
    db = vermes_state.SessionDB(env.db_path)
    _seed(db)
    # 模拟 local-connect 产物：transport=cli 联系人，transport_ref=CLI 类型名
    db.upsert_agent_profile({
        "id": "local:aider", "name": "Aider",
        "description": "本机 cli agent（CLI 直连）", "provider": "", "model": "",
        "transport": "cli", "transport_ref": "aider", "editable": 1,
    })
    db.close()

    calls = []
    def _fake_cli_chat(profile, text, timeout_seconds=180.0):
        calls.append((profile.get("id"), text))
        return f"[cli-reply] {text[:20]}"
    chat_bp._cli_agent_chat_sync = _fake_cli_chat

    r = client.post("/api/bot/rooms", json={"name": "直连房"})
    room_id = r.json().get("room_id")
    check("create room ok", r.status_code == 200 and r.json().get("ok") is True, r.text[:200])

    r = client.post(f"/api/bot/rooms/{room_id}/members", json={"ref_id": "local:aider"})
    check("add cli member ok", r.status_code == 200 and r.json().get("ok") is True, r.text[:200])

    n_before = len(env.captured)
    r = client.post(f"/api/bot/rooms/{room_id}/messages", json={"text": "@Aider 列出三个设计模式"})
    check("send @cli message ok", r.status_code == 200 and r.json().get("ok") is True, r.text[:300])

    check("cli dispatch invoked", len(calls) == 1 and calls[0][0] == "local:aider", str(calls)[:200])
    check("native agent NOT built for cli member", len(env.captured) == n_before,
          f"captured={len(env.captured)}")

    tl = client.get(f"/api/bot/rooms/{room_id}/timeline").json().get("timeline", [])
    check("cli reply in timeline", any(
        t["author_type"] == "agent" and t["author_ref"] == "local:aider" and "[cli-reply]" in t["content"]
        for t in tl), str(tl)[-400:])
    check("no unavailable system msg for cli", not any(
        t["author_type"] == "system" and "Aider" in t.get("content", "")
        for t in tl), str(tl)[-400:])
    return results


def run_secretary_org_flow(env):
    """⑭ 秘书模式端到端（2026-09-07 16:32 董董拍板·傻瓜式懒人路径）：
    群里只有 1 个 agent（秘书）+ 群无岗位表 → 用户直接提需求 →
    秘书设计组织 JSON → 系统落岗位表 + 自动拉人 → 建任务跑流水线 →
    delivered 等老板验收 → 「验收通过」→ done。
    """
    results = []
    def check(name, cond, extra=""):
        results.append((name, cond))
        return cond

    client = _client()
    db = vermes_state.SessionDB(env.db_path)
    _seed(db)
    db.close()

    # 秘书 agent：设计 JSON / 审计 pass / 其它工件
    class SecretaryAgent:
        tools = ["dummy"]
        def __init__(self, **kwargs):
            self.session_id = kwargs.get("session_id")
        def chat(self, msg, stream_callback=None):
            if "[组织流水线 · 秘书设计]" in msg:
                return ('{"title": "写周报", "roles": ['
                        '{"role_id": "eng", "name": "研究员", "type": "executor", '
                        '"profile_id": "researcher", "description": "写正文"},'
                        '{"role_id": "qa", "name": "QA", "type": "auditor", '
                        '"profile_id": "coder", "description": "交叉审计"},'
                        '{"role_id": "agg", "name": "汇总", "type": "aggregator", '
                        '"profile_id": "researcher", "description": "整合"}]}')
            if "[组织流水线 · 审计]" in msg:
                return '{"verdict": "pass", "comment": ""}'
            if "汇总" in msg and "你是" in msg:
                return "【汇总】周报已整合完成。"
            return "[工件] 周报正文内容。"
    run_agent.AIAgent = SecretaryAgent

    r = client.post("/api/bot/rooms", json={"name": "秘书群"})
    room_id = r.json().get("room_id")
    check("create room ok", r.status_code == 200 and r.json().get("ok") is True, r.text[:200])
    r = client.post(f"/api/bot/rooms/{room_id}/members", json={"ref_id": "researcher"})
    check("add secretary member ok", r.status_code == 200 and r.json().get("ok") is True, r.text[:200])

    # 发需求 → 秘书模式应自动触发（非普通群聊）
    r = client.post(f"/api/bot/rooms/{room_id}/messages", json={"text": "帮我写一份周报"})
    check("send demand ok", r.status_code == 200 and r.json().get("ok") is True, r.text[:300])
    tl = r.json().get("timeline", [])
    joined = "\n".join(f"[{m['author_type']}|{m.get('author_ref')}] {m['content']}" for m in tl)

    # 组织岗位表落地（含自动拉入的 coder）
    db2 = vermes_state.SessionDB(env.db_path)
    roles = db2.get_org_roles(room_id)
    check("org roles persisted", len(roles) >= 2, str(roles)[:300])
    types = {r["type"] for r in roles}
    check("roles have executor+auditor", {"executor", "auditor"} <= types, str(types))
    members = {m["ref_id"] for m in db2.list_bot_room_members(room_id) if m["member_type"] == "agent"}
    check("auto-pulled coder into room", "coder" in members, str(members))
    tasks = db2.list_org_tasks(room_id)
    check("task auto-created", len(tasks) >= 1, str(tasks)[:200])
    check("task delivered awaiting boss", bool(tasks) and tasks[-1].get("status") == "delivered",
          str(tasks)[:300])
    # timeline 应有流水线留痕（执行→审计→交付）
    check("pipeline traces in timeline", "审计" in joined and "已交付" in joined, joined[-400:])

    # 老板验收 → done
    r = client.post(f"/api/bot/rooms/{room_id}/messages", json={"text": "验收通过"})
    tl2 = r.json().get("timeline", [])
    joined2 = "\n".join(m["content"] for m in tl2)
    check("accept -> done", "验收通过，任务" in joined2 and "done" in joined2, joined2[-300:])
    tasks2 = db2.list_org_tasks(room_id)
    status = tasks2[-1].get("status") if tasks2 else None
    check("final status done", status == "done", str(tasks2[-1])[:200] if tasks2 else "")
    db2.close()
    return results


def run_collab(env):
    """⑭ 群聊真协作（2026-09-07 董董拍板：非并行单聊）四层验证：

    1. roster/时间线注入：agent 收到的 prompt 含群名 + 成员名单 + 最近记录；
    2. @ 接力：agent 回复中 @ 其他成员 → 自动派发给被点名者（带上下文）；
    3. 防死循环：互 @ 不无限循环（MAX_COLLAB_ROUNDS / spoke 去重）；
    4. 全链路时间线呈现协作链（多人发言）。
    用记录式 fake 替换原生 agent.chat 与 acp/cli dispatch，不触网。
    """
    results = []
    def check(name, cond, extra=""):
        results.append((name, cond))
        return cond

    client = _client()
    db = vermes_state.SessionDB(env.db_path)
    _seed(db)
    # 再造一个「分析师」原生 profile（描述标明角色，供 roster 断言）
    db.upsert_agent_profile({
        "id": "analyst", "name": "分析师",
        "description": "数据分析/洞察专家", "provider": "deepseek",
        "model": "deepseek-chat", "is_default": 0, "transport": "native",
        "editable": 1,
    })
    db.close()

    # 记录式 fake：原生 agent 记录收到的 msg；可编程返回（默认带 @ 接力或纯文本）
    messages = []
    class _Recorder:
        tools = ["dummy"]
        def __init__(self, **kw):
            self.kwargs = kw
            self.session_id = kw.get("session_id")
            env.captured.append(self)
        def chat(self, msg, stream_callback=None):
            messages.append((self.session_id, msg))
            # 研究助手 → 回复点名分析师；分析师 → 纯文本（终结接力）
            sid = self.session_id or ""
            if "researcher" in sid:
                return "我负责调研。@分析师 请补充数据分析部分。"
            return "收到，数据分析如下：... 任务闭环。"
    run_agent.AIAgent = _Recorder

    r = client.post("/api/bot/rooms", json={"name": "协作房"})
    room_id = r.json().get("room_id")
    check("create room ok", r.status_code == 200 and r.json().get("ok") is True, r.text[:200])
    for pid in ("researcher", "coder", "analyst"):
        r = client.post(f"/api/bot/rooms/{room_id}/members", json={"ref_id": pid})
        check(f"add {pid} ok", r.status_code == 200 and r.json().get("ok") is True, r.text[:200])

    r = client.post(f"/api/bot/rooms/{room_id}/messages", json={"text": "@研究助手 调研一下市场"})
    check("send ok", r.status_code == 200 and r.json().get("ok") is True, r.text[:300])

    # 1. roster/上下文注入：研究助手收到的 prompt 应含群名+自己是研究助手+成员名单
    rese_msgs = [m for s, m in messages if "researcher" in s]
    check("researcher invoked", len(rese_msgs) == 1, f"msgs={len(rese_msgs)}")
    if rese_msgs:
        body = rese_msgs[0]
        check("roster: 群名", "协作房" in body, body[:150])
        check("roster: 自己身份", "你是 @研究助手" in body, body[:300])
        check("roster: 成员名单含分析师", "@分析师" in body, body[:400])
        check("任务注入", "调研一下市场" in body, body[-150:])
    # 2. @ 接力：研究助手回复 @分析师 → 分析师被自动派发
    ana_msgs = [m for s, m in messages if "analyst" in s]
    check("analyst relayed", len(ana_msgs) == 1, f"msgs={len(ana_msgs)}")
    if ana_msgs:
        check("relay carries context", "接力" in ana_msgs[0] or "@" in ana_msgs[0], ana_msgs[0][:200])
        check("relay cites researcher reply", "我负责调研" in ana_msgs[0], ana_msgs[0][:200])
    # coder 未被点名不应发言
    coder_msgs = [m for s, m in messages if "coder" in s]
    check("coder not invoked (no fan-out to all)", len(coder_msgs) == 0, f"msgs={len(coder_msgs)}")
    # 3. 时间线呈现协作链
    tl = client.get(f"/api/bot/rooms/{room_id}/timeline").json().get("timeline", [])
    agents_spoke = {t["author_ref"] for t in tl if t["author_type"] == "agent"}
    check("timeline has both speakers", {"researcher", "analyst"} <= agents_spoke, str(agents_spoke))
    return results


def run_collab_loop_guard(env):
    """防死循环：两个 agent 互相 @ 对方 → 不无限循环，轮次有上限。"""
    results = []
    def check(name, cond, extra=""):
        results.append((name, cond))
        return cond

    client = _client()
    db = vermes_state.SessionDB(env.db_path)
    _seed(db)
    db.upsert_agent_profile({
        "id": "a1", "name": "A甲", "description": "", "provider": "deepseek",
        "model": "deepseek-chat", "is_default": 0, "transport": "native", "editable": 1,
    })
    db.upsert_agent_profile({
        "id": "a2", "name": "B乙", "description": "", "provider": "deepseek",
        "model": "deepseek-chat", "is_default": 0, "transport": "native", "editable": 1,
    })
    db.close()

    call_count = {"n": 0}
    class _LoopAgent:
        tools = ["dummy"]
        def __init__(self, **kw):
            self.session_id = kw.get("session_id")
        def chat(self, msg, stream_callback=None):
            call_count["n"] += 1
            sid = self.session_id or ""
            # 双方永远互 @：考验防死循环
            if "a1" in sid:
                return "@B乙 你继续"
            return "@A甲 你继续"
    run_agent.AIAgent = _LoopAgent

    r = client.post("/api/bot/rooms", json={"name": "死循环房"})
    room_id = r.json().get("room_id")
    for pid in ("a1", "a2"):
        client.post(f"/api/bot/rooms/{room_id}/members", json={"ref_id": pid})
    r = client.post(f"/api/bot/rooms/{room_id}/messages", json={"text": "开始"})
    check("send ok", r.status_code == 200 and r.json().get("ok") is True, r.text[:200])
    # fan-out 首轮 2 次 + 接力有上限：绝不能无限（此处硬上限 3*2=6 次内）
    check("bounded calls", call_count["n"] <= 8, f"calls={call_count['n']}")
    check("at least started", call_count["n"] >= 2, f"calls={call_count['n']}")
    return results


# ─────────────────────────── 核心断言逻辑（pytest 与 __main__ 共用） ───────────────────────────

def run_e2e(env):
    """全链路 + 空 id 校验 + G4 + G2 钩子 + P1 session_id/不污染。返回 (passed, failed, messages)。"""
    results = []
    def check(name, cond, extra=""):
        results.append((name, cond))
        return cond

    client = _client()
    db = vermes_state.SessionDB(env.db_path)
    _seed(db)
    db.close()

    r = client.post("/api/bot/rooms", json={"name": "测试房"})
    check("create room ok", r.status_code == 200 and r.json().get("ok") is True, r.text[:200])
    room_id = r.json().get("room_id")
    check("room_id auto-generated", bool(room_id), r.text[:200])

    r = client.post("/api/bot/rooms", json={"name": ""})
    check("empty name -> 400", r.status_code == 400, r.text[:200])

    r = client.post("/api/bot/rooms", json={"id": "r2", "name": ""})
    check("empty name -> 400 (with id)", r.status_code == 400, r.text[:200])

    r = client.post(f"/api/bot/rooms/{room_id}/members", json={"ref_id": ""})
    check("empty ref_id -> 400", r.status_code == 400, r.text[:200])

    r = client.get("/api/bot/rooms")
    rooms = r.json().get("rooms", [])
    check("list rooms has new room", any(x["id"] == room_id for x in rooms), str(rooms)[:200])

    # ⚙️ 微信式：新群默认空群（不自动全员入房），手动拉 researcher 进群
    r = client.post(f"/api/bot/rooms/{room_id}/members", json={"ref_id": "researcher"})
    check("add member ok", r.status_code == 200 and r.json().get("ok") is True, r.text[:200])

    r = client.post(f"/api/bot/rooms/{room_id}/messages", json={"text": "@研究助手 你好"})
    check("send message ok", r.status_code == 200 and r.json().get("ok") is True, r.text[:300])

    r = client.get(f"/api/bot/rooms/{room_id}/timeline")
    tl = r.json().get("timeline", [])
    check("timeline non-empty", len(tl) >= 1, str(tl)[:400])
    check("timeline has user msg", any(t["author_type"] == "user" for t in tl))
    check("timeline has agent reply", any(t["author_type"] == "agent" for t in tl), str(tl)[:400])

    sess_ids = [t.get("turn_session_id") for t in tl]
    check("G4 session_key format", any((s or "").startswith(f"room:{room_id}:agent:") for s in sess_ids), str(sess_ids)[:200])
    check("mention resolved researcher", any(s == f"room:{room_id}:agent:researcher" for s in sess_ids), str(sess_ids)[:200])

    db2 = vermes_state.SessionDB(env.db_path)
    tl2 = db2.get_bot_room_timeline(room_id)
    db2.close()
    check("restart restores timeline", len(tl2) >= 1 and any(t["author_type"] == "user" for t in tl2), f"len={len(tl2)}")

    conn = sqlite3.connect(str(env.db_path))
    bad_ap = conn.execute("SELECT COUNT(*) FROM agent_profiles WHERE id=''").fetchone()[0]
    bad_mb = conn.execute("SELECT COUNT(*) FROM bot_room_members WHERE ref_id=''").fetchone()[0]
    bad_rm = conn.execute("SELECT COUNT(*) FROM bot_rooms WHERE id=''").fetchone()[0]
    conn.close()
    check("no empty-id pollution", bad_ap == 0 and bad_mb == 0 and bad_rm == 0, f"ap={bad_ap},mb={bad_mb},rm={bad_rm}")

    # ⚙️ 微信式：新建群默认空群（0 成员），不自动全员入房
    r = client.post("/api/bot/rooms", json={"name": "房3"})
    r3_id = r.json().get("room_id")
    dbx = vermes_state.SessionDB(env.db_path)
    members = dbx._conn.execute("SELECT ref_id FROM bot_room_members WHERE room_id=?", (r3_id,)).fetchall()
    dbx.close()
    check("new room starts empty (no auto-join)", len(members) == 0, str(members)[:200])

    # 群公告/群任务往返 + 踢人
    r = client.patch(f"/api/bot/rooms/{room_id}", json={"announcement": "本周冲刺", "tasks": "完成P0"})
    check("patch room announcement/tasks ok", r.status_code == 200 and r.json().get("ok") is True, r.text[:200])
    dbx = vermes_state.SessionDB(env.db_path)
    rm = dbx.get_bot_room(room_id)
    dbx.close()
    check("announcement persisted", rm and rm["announcement"] == "本周冲刺", str(rm)[:200])
    check("tasks persisted", rm and rm["tasks"] == "完成P0", str(rm)[:200])
    r = client.delete(f"/api/bot/rooms/{room_id}/members/researcher")
    check("remove member ok", r.status_code == 200 and r.json().get("ok") is True, r.text[:200])
    dbx = vermes_state.SessionDB(env.db_path)
    after = [m["ref_id"] for m in dbx.list_bot_room_members(room_id)]
    dbx.close()
    check("member removed", "researcher" not in after, str(after)[:200])

    keys = [f"{a.kwargs['provider']}:{a.kwargs['model']}:{a.session_id}" for a in env.captured]
    check("agent cached under derived key (G2)", any(chat_bp._agent_cache.get(k) is not None for k in keys), str(keys)[:200])

    # ── T5/P1：session_id 绑定房间派生 key + 不污染单聊 sessions 表 ──
    check("AIAgent session_id == room key (P1)",
          any(a.session_id == f"room:{room_id}:agent:researcher" for a in env.captured),
          [a.session_id for a in env.captured])

    db3 = vermes_state.SessionDB(env.db_path)
    room_key = f"room:{room_id}:agent:researcher"
    pol_all = db3.list_sessions_rich(limit=100000, offset=0)
    pol_web_excl = db3.list_sessions_rich(limit=100000, offset=0, exclude_sources=["web"])
    db3.close()
    check("room key not in sessions list (no pollution)",
          not any(s["id"] == room_key for s in pol_all) and not any(s["id"] == room_key for s in pol_web_excl),
          [s["id"] for s in pol_all][:20])
    conn = sqlite3.connect(str(env.db_path))
    n = conn.execute("SELECT COUNT(*) FROM sessions WHERE id=?", (room_key,)).fetchone()[0]
    conn.close()
    check("sessions table has no room key (no pollution)", n == 0, f"污染：{room_key}")

    return results


def run_g2(env):
    """T5 G2：异构 profile 的 provider/model 流入 _cache_key 与 AIAgent 构造。返回 results。"""
    import asyncio
    results = []
    def check(name, cond, extra=""):
        results.append((name, cond))
        return cond

    db = vermes_state.SessionDB(env.db_path)
    _seed(db)
    db.upsert_agent_profile({
        "id": "pa", "name": "A助手", "provider": "deepseek", "model": "deepseek-chat",
        "is_default": 0, "transport": "native", "toolsets": [], "skill_set": "", "system_prompt": "",
    })
    db.upsert_agent_profile({
        "id": "pb", "name": "B助手", "provider": "agnes", "model": "agnes-2.0-flash",
        "is_default": 0, "transport": "native", "toolsets": [], "skill_set": "", "system_prompt": "",
    })
    prof_a = db.get_agent_profile("pa")
    prof_b = db.get_agent_profile("pb")
    db.close()

    loop = asyncio.new_event_loop()
    key_a, key_b = "room:rX:agent:pa", "room:rX:agent:pb"
    agent_a = loop.run_until_complete(chat_bp._bot_build_agent(key_a, prof_a))
    agent_b = loop.run_until_complete(chat_bp._bot_build_agent(key_b, prof_b))
    loop.close()

    # 复算 _bot_build_agent 内部使用的完整缓存键（provider:model:session_key）
    pa_prov, _, _, pa_model = chat_bp._resolve_room_agent_identity(prof_a)
    pb_prov, _, _, pb_model = chat_bp._resolve_room_agent_identity(prof_b)
    cache_key_a = f"{pa_prov}:{pa_model}:{key_a}"
    cache_key_b = f"{pb_prov}:{pb_model}:{key_b}"

    check("both agents built", agent_a is not None and agent_b is not None, f"a={agent_a},b={agent_b}")
    check("cache keys differ by provider/model (G2)", cache_key_a != cache_key_b,
          f"{cache_key_a} == {cache_key_b}")
    cached_a = chat_bp._agent_cache.get(cache_key_a)
    cached_b = chat_bp._agent_cache.get(cache_key_b)
    check("both cached", cached_a is not None and cached_b is not None,
          f"a={cache_key_a}->{cached_a}, b={cache_key_b}->{cached_b}")
    check("heterogeneous NOT share cache (G2)", cached_a is not cached_b, "误共享同一缓存 agent")

    check("agent_a provider=deepseek", agent_a.kwargs["provider"] == "deepseek")
    check("agent_a model=deepseek-chat", agent_a.kwargs["model"] == "deepseek-chat")
    check("agent_b provider=agnes", agent_b.kwargs["provider"] == "agnes")
    check("agent_b model=agnes-2.0-flash", agent_b.kwargs["model"] == "agnes-2.0-flash")
    check("agent_a session_id == key_a (P1)", agent_a.session_id == key_a)
    check("agent_b session_id == key_b (P1)", agent_b.session_id == key_b)
    return results


def run_ws(env):
    """T6 G3 已决：room_update WS 契约（topic 粒度 + event 判别 + type 分流）。

    捕获 _bot_broadcast_room_update 经 ⑪ WS（_channel_sync_broadcast）发出的 envelope，
    断言与「room:{room_id} 粒度 + type=room_update + event∈{room_message,room_created,member_change}」
    锁定契约一致。录制器须为 async（广播函数被 route 以 await 调用）。
    """
    import vermes_cli.web_server as _ws_mod
    orig = _ws_mod._channel_sync_broadcast

    async def _rec(payload):
        env.broadcasts.append(payload)

    _ws_mod._channel_sync_broadcast = _rec
    results = []
    def check(name, cond, extra=""):
        results.append((name, cond))
        return cond
    try:
        client = _client()
        db = vermes_state.SessionDB(env.db_path)
        _seed(db)
        db.close()

        # ── room_created ──
        env.broadcasts.clear()
        r = client.post("/api/bot/rooms", json={"id": "rw1", "name": "WS房"})
        check("create room ok (ws)", r.status_code == 200 and r.json().get("ok") is True, r.text[:200])
        created = [b for b in env.broadcasts if b.get("event") == "room_created"]
        check("room_created emitted", len(created) == 1, str(env.broadcasts)[:300])
        if created:
            c = created[0]
            check("envelope type=room_update", c.get("type") == "room_update", str(c))
            check("topic=room:rw1 (房间级粒度)", c.get("topic") == "room:rw1", str(c))
            check("room_created carries name", c.get("name") == "WS房", str(c))
            check("room_created carries room_id", c.get("room_id") == "rw1", str(c))

        # ── room_message ──
        env.broadcasts.clear()
        r = client.post("/api/bot/rooms/rw1/messages", json={"text": "@研究助手 你好"})
        check("send message ok (ws)", r.status_code == 200 and r.json().get("ok") is True, r.text[:200])
        rmsgs = [b for b in env.broadcasts if b.get("event") == "room_message"]
        check("room_message emitted (>=1: user + agent)", len(rmsgs) >= 1, str(env.broadcasts)[:400])
        if rmsgs:
            m0 = rmsgs[0]
            check("room_message topic=room:rw1", m0.get("topic") == "room:rw1", str(m0))
            check("room_message carries message dict", isinstance(m0.get("message"), dict), str(m0))
            check("message has author_type", bool(m0["message"].get("author_type")), str(m0))

        # ── member_change ──
        env.broadcasts.clear()
        r = client.post("/api/bot/rooms/rw1/members", json={"ref_id": "researcher"})
        check("add member ok (ws)", r.status_code == 200 and r.json().get("ok") is True, r.text[:200])
        mc = [b for b in env.broadcasts if b.get("event") == "member_change"]
        check("member_change emitted", len(mc) == 1, str(env.broadcasts)[:300])
        if mc:
            check("member_change topic=room:rw1", mc[0].get("topic") == "room:rw1", str(mc[0]))
            check("member_change carries ref_id", mc[0].get("ref_id") == "researcher", str(mc[0]))
    finally:
        _ws_mod._channel_sync_broadcast = orig
    return results


def run_off(env):
    """T7：BOT_MODE_ENABLED 关闭 → 完全不加载 bot 代码路径（路由不注册 + 不建 agent + 不广播），
    且单聊路由不受影响。返回 results。

    关闭时 register_to 内 bot 路由整体不注册 → /api/bot/* 404；处理器入口另有守卫返回 403。
    双重验证「零影响单聊」是真·零（不只是不触发广播，而是 bot 代码路径根本不存在）。
    """
    results = []
    def check(name, cond, extra=""):
        results.append((name, cond))
        return cond

    orig_flag = chat_bp._bot_mode_enabled
    orig_agent = run_agent.AIAgent
    calls = []
    class _ForbiddenAgent:
        def __init__(self, *a, **k):
            calls.append(1)
    chat_bp._bot_mode_enabled = lambda: False
    run_agent.AIAgent = _ForbiddenAgent
    env.broadcasts.clear()
    try:
        # 关闭态重建 app：register_to 内 bot 路由不注册
        client = _client()
        r = client.post("/api/bot/rooms", json={"id": "rx", "name": "x"})
        check("bot route disabled when flag off (403/404)", r.status_code in (403, 404), str(r.status_code))
        # Phase 2 新增的 GET members 同受最外层开关键控（不能成为绕过口）
        r = client.get("/api/bot/rooms/rx/members")
        check("GET members disabled when flag off (403/404)", r.status_code in (403, 404), str(r.status_code))
        # 单聊路由独立于 bot 开关：始终注册、正常响应（此处返回 {"data":[]}）
        r2 = client.get("/api/chat/models")
        check("single-chat route unaffected (not bot-disabled)",
              r2.status_code != 404 and "bot mode disabled" not in (r2.text or ""),
              str(r2.status_code))
        check("no agent built when flag off", len(calls) == 0, f"calls={len(calls)}")
        check("no broadcast when flag off", len(env.broadcasts) == 0, f"bcasts={len(env.broadcasts)}")
    finally:
        chat_bp._bot_mode_enabled = orig_flag
        run_agent.AIAgent = orig_agent
    return results


def run_members(env):
    """Phase 2：GET /api/bot/rooms/{id}/members —— 完整 @ 补全的候选端点。

    断言候选携带 UI 展示字段（name / hue / avatar_seed）。这三列是 T1 数据模型
    为头像预留的（seed: researcher→hue=210、coder→hue=140），Phase 2 只是读取、
    不新增列。hue 为 0/缺失时前端按 ref_id 哈希兜底，故此处断言的是「后端确实
    把预留列透出」，前端兜底逻辑不在此覆盖。
    """
    results = []
    def check(name, cond, extra=""):
        results.append((name, cond))
        return cond

    client = _client()
    db = vermes_state.SessionDB(env.db_path)
    _seed(db)
    db.create_bot_room("rm", "成员房")
    for p in db.list_agent_profiles():
        db.add_bot_room_member("rm", "agent", p["id"])
    db.close()

    r = client.get("/api/bot/rooms/rm/members")
    check("GET members 200 + ok", r.status_code == 200 and r.json().get("ok") is True, r.text[:200])
    members = r.json().get("members", [])
    refs = {m["ref_id"] for m in members}
    check("候选含 researcher/coder", refs >= {"researcher", "coder"}, str(refs)[:300])

    by_ref = {m["ref_id"]: m for m in members}
    rs = by_ref.get("researcher", {})
    cd = by_ref.get("coder", {})
    check("候选带 name（中文别名）", rs.get("name") == "研究助手", str(rs)[:200])
    check("候选带 hue（头像色相 210）", rs.get("hue") == 210, str(rs)[:200])
    check("候选带 avatar_seed", cd.get("avatar_seed") == "coder", str(cd)[:200])
    check("候选 member_type=agent", all(m.get("member_type") == "agent" for m in members), str(members)[:300])

    # 空 room_id 校验（与其余 bot handler 同纪律）：%20 → strip 后为空 → 400
    r = client.get("/api/bot/rooms/%20/members")
    check("空 room_id -> 400", r.status_code == 400, r.text[:200])

    # 无 profile 的成员（如外部 human）不应丢条目：ref_id 兜底、不 JOIN 掉
    db = vermes_state.SessionDB(env.db_path)
    db.add_bot_room_member("rm", "human", "u-outsider")
    db.close()
    r = client.get("/api/bot/rooms/rm/members")
    members2 = r.json().get("members", [])
    hit = [m for m in members2 if m["ref_id"] == "u-outsider"]
    check("无 profile 成员不丢失（LEFT JOIN 兜底）", len(hit) == 1, str(members2)[:300])
    check("无 profile 成员 name 退化为 ref_id", hit and hit[0].get("name") == "u-outsider", str(hit)[:200])
    return results


# ─────────────────────────── pytest 入口 ───────────────────────────

@pytest.fixture
def env(tmp_path, monkeypatch):
    e = type("E", (), {})()
    e.db_path = tmp_path / "state.db"
    e.captured = []
    e.broadcasts = []
    _install_fakes(e)
    # monkeypatch 仅用于会话级清理语义；重定向已在 _install_fakes 完成
    yield e


def test_e2e_full_flow_and_empty_id(env):
    results = run_e2e(env)
    failed = [n for n, c in results if not c]
    assert not failed, f"FAILED: {failed}\n" + "\n".join(f"  {'PASS' if c else 'FAIL'} {n}" for n, c in results)


def test_g2_heterogeneous_cache_keys(env):
    results = run_g2(env)
    failed = [n for n, c in results if not c]
    assert not failed, f"FAILED: {failed}\n" + "\n".join(f"  {'PASS' if c else 'FAIL'} {n}" for n, c in results)


def test_ws_room_update_contract(env):
    results = run_ws(env)
    failed = [n for n, c in results if not c]
    assert not failed, f"FAILED: {failed}\n" + "\n".join(f"  {'PASS' if c else 'FAIL'} {n}" for n, c in results)


def test_bot_mode_disabled_short_circuits(env):
    results = run_off(env)
    failed = [n for n, c in results if not c]
    assert not failed, f"FAILED: {failed}\n" + "\n".join(f"  {'PASS' if c else 'FAIL'} {n}" for n, c in results)


def test_room_members_endpoint(env):
    results = run_members(env)
    failed = [n for n, c in results if not c]
    assert not failed, f"FAILED: {failed}\n" + "\n".join(f"  {'PASS' if c else 'FAIL'} {n}" for n, c in results)


def test_acp_agent_room_dispatch(env):
    results = run_acp_room(env)
    failed = [n for n, c in results if not c]
    assert not failed, f"FAILED: {failed}\n" + "\n".join(f"  {'PASS' if c else 'FAIL'} {n}" for n, c in results)


def test_cli_agent_room_dispatch(env):
    results = run_cli_room(env)
    failed = [n for n, c in results if not c]
    assert not failed, f"FAILED: {failed}\n" + "\n".join(f"  {'PASS' if c else 'FAIL'} {n}" for n, c in results)

def test_secretary_org_flow(env):
    results = run_secretary_org_flow(env)
    failed = [n for n, c in results if not c]
    assert not failed, f"FAILED: {failed}\n" + "\n".join(f"  {'PASS' if c else 'FAIL'} {n}" for n, c in results)

def test_collab_room_relay(env):
    results = run_collab(env)
    failed = [n for n, c in results if not c]
    assert not failed, f"FAILED: {failed}\n" + "\n".join(f"  {'PASS' if c else 'FAIL'} {n}" for n, c in results)


def test_collab_room_loop_guard(env):
    results = run_collab_loop_guard(env)
    failed = [n for n, c in results if not c]
    assert not failed, f"FAILED: {failed}\n" + "\n".join(f"  {'PASS' if c else 'FAIL'} {n}" for n, c in results)



# ─────────────────────────── 独立运行入口 ───────────────────────────

if __name__ == "__main__":
    tmp = tempfile.mkdtemp(prefix="botmode_t5_")
    e = type("E", (), {})()
    e.db_path = Path(tmp) / "state.db"
    e.captured = []
    e.broadcasts = []
    _install_fakes(e)

    all_results = []
    all_results += run_e2e(e)
    all_results += run_g2(e)
    all_results += run_ws(e)
    all_results += run_off(e)
    all_results += run_members(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_acp_room(e)
    all_results += run_collab(e)
    all_results += run_collab_loop_guard(e)
    all_results += run_cli_room(e)
    all_results += run_cli_room(e)
    all_results += run_cli_room(e)
    all_results += run_secretary_org_flow(e)
    all_results += run_secretary_org_flow(e)

    print("\n=== SUMMARY ===")
    fails = [n for n, c in all_results if not c]
    for n, c in all_results:
        print(("PASS" if c else "FAIL"), "-", n)
    print(f"\n{len(all_results) - len(fails)}/{len(all_results)} passed" +
          (f" | FAILED: {fails}" if fails else " | ALL PASS"))
    raise SystemExit(1 if fails else 0)
