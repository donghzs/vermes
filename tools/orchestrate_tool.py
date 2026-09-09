#!/usr/bin/env python3
"""神魔堂调度工具（⑭ 单聊/移动渠道 → 秘书分身 → 神魔堂编排）。

把「神魔堂」从群聊孤岛，打通为**单聊主 agent 可调度的能力**：

- 用户在单聊（桌面 WebChat / 飞书 / Telegram / QQ / 微信等移动渠道）说一句
  需要异构多 agent 协作的任务 → 主 agent 调本工具 → 秘书分身组队/造神/拉群/
  派活 → 流水线（拆解→执行→交叉审计→汇总→交付）→ 交付物摘要回当前对话。

实现方式：懒加载 ``vermes_cli.blueprints.chat`` 里的既有编排函数
（``_secretary_orchestrate`` / ``_org_message_orchestrate``），**不改编排核心**，
只做「单聊 → 群聊房间」的桥接。所以：
  - 复用「秘书群」房间 + 按话题分组（默认，用户已选）；
  - 跑完把交付物摘要回当前对话；
  - 支持在单聊里验收/打回（复用 _org_message_orchestrate 的文字触发）。

为什么放 tools/ 而非直接在 chat.py 里加：
  - 工具走 ``tools/registry.register`` 自注册 → 单聊主 agent（CORE_TOOLS）
    天然拿到，无需改 toolset 装配；
  - lazy import chat.py 避免 model_tools → tools/* → chat.py → model_tools 循环。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── schema ────────────────────────────────────────────────────────────────
_SHENMOTANG_SCHEMA = {
    "name": "shenmotang",
    "description": """调度「神魔堂」——你的多 Agent 协作组织，用于处理协同、长程自动化闭环、需多角色并行分工的任务。

触发判断（关键）：
- ✅ 用本工具：协同/长程闭环任务（需多角色分工、并行执行、并行审计、汇总提交；或跨多个窗口/多个 agent 才能完成的事）。这类任务交给神魔堂反而**更省**——多 agent 共享上下文、并行干活 + 并行审计 + 汇总提交，远好于用户自己在 QClaw/WorkBuddy/Vermes 等多个窗口手动复制粘贴（那样既累也一样耗 token、时间、精力）。现实生产场景就是如此。
- ❌ 不要用本工具：简单的单步任务、问答、自己（单 agent）一步就能独立完成的事。这类任务自己直接做即可，不必要组队。

当任务确实需要多角色协作时，用本工具交给秘书分身组队执行（研究/编码/法律/写作/分析等分工 + 拆解-执行-审计-汇总流水线）。

参数说明：
- action='delegate'：下达新任务。秘书会按需组队（可自动造神）、拉群、驱动流水线，跑完后把交付物摘要回给你。
- action='review'：对已交付（delivered）的任务验收或打回。approve=true 验收通过；approve=false 需给 feedback（打回意见）。
- action='status'：查某个任务/群的进度。
- action='onboard'：接入一个外部 agent（如 Codex/Claude Code/OpenClaw 等）。你负责探测本机安装、匹配封神榜 recipe、接 ACP/CLI 通路。若缺 API key，返回 need_auth——此时**不要**在群聊里展示或索要 key，而是引导用户去设置页（前端弹窗）填写，key 永不进群聊。

关于 topic：默认复用/创建「秘书群」房间并按 topic 分组，同一话题的任务会落在同一个群（可复用组织岗位）。topic 用简短中文短语概括任务主题即可。

delegate 模式会阻塞直到流水线跑完（拆解→执行→审计→汇总→交付），耗时较长（数十秒到数分钟），适合协同/长程闭环任务。""",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["delegate", "review", "status", "onboard"],
                "description": "delegate=下达任务；review=验收/打回；status=查进度；onboard=接入外部 agent",
            },
            "agent_name": {
                "type": "string",
                "description": "onboard 时必填：要接入的 agent 名（官方 recipe 名/通用名/本机 discovery id 均可，如 Codex/Claude Code/OpenClaw）",
            },
            "auth_value": {
                "type": "string",
                "description": "onboard 时可选：用户已授权的 API key。缺 key 时返回 need_auth，引导用户去设置页填（key 永不进群聊）",
            },
            "task": {
                "type": "string",
                "description": "delegate 时必填：要交给组织协作完成的任务描述（老板指令，自包含，含背景与期望产出）",
            },
            "topic": {
                "type": "string",
                "description": "delegate 时建议填：任务主题短语（如「竞品调研」「法务尽调」「论文写作」），用于把同话题任务归到同一组织群、复用岗位",
            },
            "task_id": {
                "type": "string",
                "description": "review/status 时必填：任务 id（delegate 返回里会给）",
            },
            "approve": {
                "type": "boolean",
                "description": "review 时必填：true=验收通过，false=打回（需配合 feedback）",
            },
            "feedback": {
                "type": "string",
                "description": "review 且 approve=false 时：打回意见",
            },
        },
        "required": ["action"],
    },
}


# ── 内部桥接 helper ──────────────────────────────────────────────────────
def _import_chat_orchestrate():
    """懒加载 chat.py 的编排函数（避免模块级循环 import）。

    返回 (db_factory, _secretary_orchestrate, _org_message_orchestrate, norm) 四元组。
    """
    from vermes_state import SessionDB
    from vermes_cli.botmode.core import RoomIdNormalizer
    from vermes_cli.blueprints.chat import (
        _org_message_orchestrate,
        _secretary_orchestrate,
    )

    def _norm(raw_room_id: str) -> str:
        return RoomIdNormalizer.normalize("desktop", raw_room_id)

    return SessionDB, _secretary_orchestrate, _org_message_orchestrate, _norm


async def _onboard_agent(agent_name: str, auth_value: str) -> str:
    """action=onboard：懒加载服务层，把探测结果转成给秘书看的自然语言回报。

    安全：need_auth 时**不返回明文 key**，只回引导语，让用户去设置页填。
    """
    if not agent_name or not agent_name.strip():
        return json.dumps({"ok": False, "error": "onboard 需要 agent_name"}, ensure_ascii=False)
    try:
        from vermes_cli.a2a.onboarding import onboard_agent

        r = onboard_agent(agent_name.strip(), auth_value or "")
    except Exception as e:  # noqa: BLE001 - 接入失败不阻断主流程，回错误文案
        logger.exception("shenmotang onboard error")
        return json.dumps(
            {"ok": False, "status": "error", "error": f"接入探测失败: {e}"}, ensure_ascii=False
        )

    st = r.get("status")
    if st == "success":
        return json.dumps({
            "ok": True,
            "status": "success",
            "agent": r.get("agent"),
            "profile_id": r.get("profile_id"),
            "transport": r.get("transport"),
            "health": r.get("health"),
            "message": f"已接入 {r.get('agent')}（profile_id={r.get('profile_id')}，通路={r.get('transport')}）。"
                       f"在群里 @它 就能拉进组织干活。",
        }, ensure_ascii=False)
    if st == "need_auth":
        return json.dumps({
            "ok": True,
            "status": "need_auth",
            "agent": r.get("agent"),
            "auth_env": r.get("auth_env"),
            "message": f"接入 {r.get('agent')} 需要 API key（环境变量 {r.get('auth_env')}）。"
                       f"请引导用户去设置页的密钥弹窗填写（key 不要发在群聊里）。"
                       f"登录命令参考: {r.get('login_command')}",
        }, ensure_ascii=False)
    if st == "not_found":
        return json.dumps({
            "ok": False,
            "status": "not_found",
            "error": r.get("error"),
            "message": f"本机没发现 {agent_name}，封神榜也无同名 recipe。"
                       f"可提醒用户：1) 确认已安装其 CLI；2) 或去封神榜用官方 recipe 登堂。",
        }, ensure_ascii=False)
    # error / fail
    return json.dumps({
        "ok": False,
        "status": st or "error",
        "error": r.get("error"),
        "message": r.get("error") or f"接入 {agent_name} 失败",
    }, ensure_ascii=False)


def _resolve_secretary(db) -> Optional[Dict[str, Any]]:
    """找秘书分身 profile（is_avatar=1 优先，回退 name=='秘书'）。"""
    try:
        for p in db.list_agent_profiles():
            if p.get("is_avatar"):
                return p
    except Exception:
        pass
    try:
        for p in db.list_agent_profiles():
            if p.get("id") == "secretary" or p.get("name") == "秘书":
                return p
    except Exception:
        pass
    return None


def _get_or_create_secretary_room(db, topic: str) -> str:
    """复用「秘书群」房间；按话题分组。返回 room_id。

    策略（用户拍板）：默认一个「秘书群」承载秘书编排，话题作为任务标题/前缀
    区分，不无限建群。若秘书群不存在则创建。
    """
    room_id = "secretary-room"
    room = db.get_bot_room(room_id)
    if room is None:
        db.create_bot_room(room_id, "秘书群", channel="desktop")
    # 保证秘书在群里（幂等）
    sec = _resolve_secretary(db)
    if sec:
        existing = {
            m["ref_id"] for m in db.list_bot_room_members(room_id)
            if m["member_type"] == "agent"
        }
        if sec["id"] not in existing:
            db.add_bot_room_member(room_id, "agent", sec["id"])
    return room_id


def _room_status_summary(db, room_id: str, task_id: Optional[str] = None) -> str:
    """生成房间/任务进度摘要，回给主 agent。"""
    lines = []
    if task_id:
        t = db.get_org_task(task_id)
        if not t:
            return f"任务 {task_id} 未找到。"
        st = t.get("status")
        lines.append(f"任务「{t.get('title') or t['id']}」状态：{st}")
        if st == "delivered":
            lines.append("已交付，待验收（可说「验收通过」或「打回: 意见」）。")
            fo = (t.get("final_output") or "").strip()
            if fo:
                lines.append(f"交付物摘要：{fo[:800]}")
        elif st == "done":
            lines.append("已验收通过（完结）。")
            fo = (t.get("final_output") or "").strip()
            if fo:
                lines.append(f"成果摘要：{fo[:800]}")
        elif st == "rejected":
            lines.append("已被打回/上报。")
        else:
            lines.append("进行中。")
        return "\n".join(lines)

    # 无 task_id → 列房间任务
    try:
        tasks = db.list_org_tasks(room_id) or []
    except Exception:
        tasks = []
    if not tasks:
        return "神魔堂暂无任务。"
    lines.append(f"神魔堂（{room_id}）共 {len(tasks)} 个任务：")
    for t in tasks[:10]:
        lines.append(f"- {t['id']}「{t.get('title') or ''}」状态 {t.get('status')}")
    return "\n".join(lines)


def _truncate(s: str, n: int = 2000) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[:n] + "\n…(截断)"


# ── handler ───────────────────────────────────────────────────────────────
async def shenmotang_tool(
    action: str,
    task: str = "",
    topic: str = "",
    task_id: str = "",
    approve: bool = True,
    feedback: str = "",
    agent_name: str = "",
    auth_value: str = "",
) -> str:
    """神魔堂调度入口（async，registry dispatch 经 _run_async 桥接）。"""
    action = (action or "").strip().lower()
    SessionDB, _secretary, _org_msg, _norm = _import_chat_orchestrate()
    db = SessionDB()
    try:
        db.seed_default_profiles()

        if action == "onboard":
            return await _onboard_agent(agent_name, auth_value)

        if action == "status":
            if task_id:
                return json.dumps(
                    {"ok": True, "summary": _room_status_summary(db, "", task_id)},
                    ensure_ascii=False,
                )
            room_id = _get_or_create_secretary_room(db, topic)
            return json.dumps(
                {"ok": True, "summary": _room_status_summary(db, room_id)},
                ensure_ascii=False,
            )

        if action == "review":
            if not task_id:
                return json.dumps({"ok": False, "error": "review 需要 task_id"}, ensure_ascii=False)
            t = db.get_org_task(task_id)
            if not t:
                return json.dumps({"ok": False, "error": f"任务 {task_id} 未找到"}, ensure_ascii=False)
            room_id = t.get("room_id") or "secretary-room"
            norm = _norm(room_id)
            room = db.get_bot_room(room_id) or {}
            if approve:
                text = "验收通过"
            else:
                text = "打回：" + (feedback.strip() or "请重做")
            # 复用组织模式消息处理（含验收/打回 + 结论回写）
            await _org_msg(db, room, room_id, norm, text, db.get_org_roles(room_id))
            return json.dumps(
                {"ok": True, "summary": _room_status_summary(db, room_id, task_id)},
                ensure_ascii=False,
            )

        # action == "delegate"
        if not task or not task.strip():
            return json.dumps({"ok": False, "error": "delegate 需要 task"}, ensure_ascii=False)
        task = task.strip()
        topic = (topic or "").strip()

        room_id = _get_or_create_secretary_room(db, topic)
        norm = _norm(room_id)
        room = db.get_bot_room(room_id) or {}

        # 秘书分身必须存在（否则无法编排）
        secretary = _resolve_secretary(db)
        if not secretary:
            return json.dumps(
                {"ok": False, "error": "没有秘书分身（secretary profile），无法调度神魔堂。"},
                ensure_ascii=False,
            )

        # 组织模式 vs 秘书模式分流（与 bot_room_message_send 一致）：
        #   - 群已有岗位表 → 组织模式（老板直接下指令）
        #   - 群无岗位表 → 秘书模式（秘书搭组织）
        try:
            roles = db.get_org_roles(room_id)
        except Exception:
            roles = []

        # 话题前缀：让任务标题带上话题，便于在群里区分
        brief = f"[{topic}] {task}" if topic else task

        if roles:
            await _org_msg(db, room, room_id, norm, brief, roles)
        else:
            await _secretary(db, room, room_id, norm, brief, secretary)

        # 跑完后取最新任务，回交付物摘要
        try:
            tasks = db.list_org_tasks(room_id) or []
        except Exception:
            tasks = []
        latest = tasks[0] if tasks else None
        if latest:
            detail = db.get_org_task(latest["id"])
            summary = _room_status_summary(db, room_id, latest["id"])
            return json.dumps(
                {
                    "ok": True,
                    "room_id": room_id,
                    "task_id": latest["id"],
                    "status": detail.get("status") if detail else latest.get("status"),
                    "summary": summary,
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {"ok": True, "room_id": room_id, "summary": "神魔堂已处理，但未查到任务记录。"},
            ensure_ascii=False,
        )
    except Exception as e:
        logger.exception("shenmotang_tool dispatch error")
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)
    finally:
        try:
            db.close()
        except Exception:
            pass


# ── 注册 ──────────────────────────────────────────────────────────────────
from tools.registry import registry

registry.register(
    name="shenmotang",
    toolset="shenmotang",
    schema=_SHENMOTANG_SCHEMA,
    handler=lambda args, **kw: shenmotang_tool(
        action=args.get("action", ""),
        task=args.get("task", ""),
        topic=args.get("topic", ""),
        task_id=args.get("task_id", ""),
        approve=bool(args.get("approve", True)),
        feedback=args.get("feedback", ""),
        agent_name=args.get("agent_name", ""),
        auth_value=args.get("auth_value", ""),
    ),
    is_async=True,
    emoji="⛩️",
    description="调度神魔堂多 Agent 协作组织（秘书分身组队/造神/拉群/派活/交付）",
    max_result_size_chars=100_000,
)
