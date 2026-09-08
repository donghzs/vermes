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
    "description": """调度「神魔堂」——你的多 Agent 协作组织，用于处理需要异构 agent 分工协作的复杂任务。

⚠️ 经济性硬约束（必须遵守）：绝大多数场景你自己（单 agent）独立完成即可，**不要默认调用本工具**——多 agent 协作会额外消耗大量 token 与上下文，徒增用户经济负担。只有当用户**明确表达**需要多 agent 协作/组队/拆解派活/请多个专家一起干（例如说「叫神魔堂」「组个队」「多找几个 agent 一起」「让秘书派活」等）时，才调用本工具。用户没提多 agent 需求时，一律自己直接做。

当用户（在单聊或移动渠道）明确要求多角色协作时，用本工具把它交给秘书分身组队执行。适合：需要不同专长（研究/编码/法律/写作/分析等）分工协作、或需要拆解-执行-审计-汇总流水线的任务。

参数说明：
- action='delegate'：下达新任务。秘书会按需组队（可自动造神）、拉群、驱动流水线，跑完后把交付物摘要回给你。
- action='review'：对已交付（delivered）的任务验收或打回。approve=true 验收通过；approve=false 需给 feedback（打回意见）。
- action='status'：查某个任务/群的进度。

关于 topic：默认复用/创建「秘书群」房间并按 topic 分组，同一话题的任务会落在同一个群（可复用组织岗位）。topic 用简短中文短语概括任务主题即可。

delegate 模式会阻塞直到流水线跑完（拆解→执行→审计→汇总→交付），耗时较长（数十秒到数分钟），只在用户明确要求多 agent 协作时使用。""",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["delegate", "review", "status"],
                "description": "delegate=下达任务；review=验收/打回；status=查进度",
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
) -> str:
    """神魔堂调度入口（async，registry dispatch 经 _run_async 桥接）。"""
    action = (action or "").strip().lower()
    SessionDB, _secretary, _org_msg, _norm = _import_chat_orchestrate()
    db = SessionDB()
    try:
        db.seed_default_profiles()

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
    ),
    is_async=True,
    emoji="⛩️",
    description="调度神魔堂多 Agent 协作组织（秘书分身组队/造神/拉群/派活/交付）",
    max_result_size_chars=100_000,
)
