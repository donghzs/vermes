"""Memory-Aware Executor — 让记忆在任务执行时主动参与，不再只在 turn-1 被动注入。

两层能力：
1. pre_task_recall: 每轮 turn 前从 L1-L4 主动召回与当前用户消息相关的记忆，
   注入 <task_memory> 块（~300 token 预算），让模型每轮都能看到相关经验。
2. post_task_reflect: 任务完成后从助手回复中提取决策/偏好，自动写入 L1 记忆。

设计原则：
- fail-open：召回失败不阻断对话
- 增量：turn-1 全量召回（~300 token），后续轮增量（~150 token，只追最新相关）
- 不可压缩保护：写入的 preference/decision 由 _infer_lifecycle_tag 自动标记
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Token 预算 ──────────────────────────────────────────────
_TASK_MEMORY_BUDGET_CHARS = 1200   # ~300 tokens
_TASK_MEMORY_INCREMENTAL_CHARS = 600  # ~150 tokens (turn > 1)
_PER_SECTION_MAX = 3               # 每层最多召回条数

# ── 召回去重（同 session 内不重复召回同一条记忆）──
_recently_recalled_ids: set = set()
_last_session_id: Optional[str] = None


def pre_task_recall(
    user_message: str,
    *,
    turn: int = 1,
    session_id: str = "",
    scope: str = "",
) -> str:
    """在每轮 turn 前从 L1-L4 主动召回与用户消息相关的记忆。

    Args:
        user_message: 当前用户消息
        turn: 当前轮次（turn=1 全量，turn>1 增量）
        session_id: 当前会话 ID（用于去重）
        scope: 当前渠道（用于 scope-weighted recall）

    Returns:
        <task_memory> 块文本，空字符串表示无相关记忆。
    """
    global _recently_recalled_ids, _last_session_id

    # Session 切换 → 重置去重
    if session_id and session_id != _last_session_id:
        _recently_recalled_ids = set()
        _last_session_id = session_id

    if not user_message or not user_message.strip():
        return ""

    budget = _TASK_MEMORY_BUDGET_CHARS if turn == 1 else _TASK_MEMORY_INCREMENTAL_CHARS

    try:
        from agent.memory_fabric import recall as _mf_recall
    except ImportError:
        logger.debug("memory_aware_executor: memory_fabric unavailable")
        return ""

    blocks: List[str] = []
    total_chars = 0

    # ── L1 decision/preference：硬约束，必须召回 ──
    try:
        decisions = _mf_recall(
            user_message,
            tag_filter=["decision", "preference"],
            limit=_PER_SECTION_MAX,
            scope=scope or None,
        )
        # 去重
        decisions = [d for d in decisions if d.get("id") not in _recently_recalled_ids]
        if decisions:
            section = "## 你的决策与偏好\n"
            for d in decisions:
                line = f"  • {d.get('content', '')[:120]}\n"
                if total_chars + len(section) + len(line) > budget:
                    break
                section += line
                _recently_recalled_ids.add(d.get("id"))
            if len(section) > len("## 你的决策与偏好\n"):
                blocks.append(section)
                total_chars += len(section)
    except Exception as e:
        logger.debug("memory_aware_executor: L1 recall failed: %s", e)

    # ── L2 procedural：相关技能习惯（turn=1 才召回，省 token）──
    if turn == 1:
        try:
            skills = _mf_recall(
                user_message,
                layer="procedural",
                limit=_PER_SECTION_MAX,
                scope=scope or None,
            )
            skills = [s for s in skills if s.get("id") not in _recently_recalled_ids]
            if skills:
                section = "## 相关技能与习惯\n"
                for s in skills:
                    line = f"  • {s.get('content', '')[:120]}\n"
                    if total_chars + len(section) + len(line) > budget:
                        break
                    section += line
                    _recently_recalled_ids.add(s.get("id"))
                if len(section) > len("## 相关技能与习惯\n"):
                    blocks.append(section)
                    total_chars += len(section)
        except Exception as e:
            logger.debug("memory_aware_executor: L2 recall failed: %s", e)

    # ── L3 episodic：相似经历 ──
    try:
        episodes = _mf_recall(
            user_message,
            layer="episodic",
            limit=_PER_SECTION_MAX,
            scope=scope or None,
        )
        episodes = [e for e in episodes if e.get("id") not in _recently_recalled_ids]
        if episodes:
            section = "## 上次做类似任务\n"
            for e in episodes:
                line = f"  • {e.get('content', '')[:120]}\n"
                if total_chars + len(section) + len(line) > budget:
                    break
                section += line
                _recently_recalled_ids.add(e.get("id"))
            if len(section) > len("## 上次做类似任务\n"):
                blocks.append(section)
                total_chars += len(section)
    except Exception as e:
        logger.debug("memory_aware_executor: L3 recall failed: %s", e)

    if not blocks:
        return ""

    result = "<task_memory>\n" + "\n".join(blocks) + "</task_memory>"

    # 硬截断保预算
    if len(result) > budget + 50:  # +50 for tags
        result = result[:budget] + "\n</task_memory>"

    logger.info(
        "memory_aware_executor: pre_task_recall turn=%d, %d blocks, %d chars, session=%s",
        turn, len(blocks), len(result), session_id[:8] if session_id else "?",
    )
    return result


# ── 偏好/决策提取触发词（复用 _preference_keywords + 决策关键词）──
_DECISION_KEYWORDS = ("决定", "确定", "选定", "方案确定", "chose", "decided", "we'll use", "let's go with")


def post_task_reflect(
    user_message: str,
    assistant_text: str,
    *,
    session_id: str = "",
    scope: str = "",
) -> Dict[str, int]:
    """任务完成后从助手回复中提取决策/偏好，自动写入 L1 记忆。

    不自己判断偏好/决策——依赖 _infer_lifecycle_tag 的启发式统一判断，
    避免词表分裂（参考 _preference_keywords.py 设计文档）。

    Returns:
        {"extracted": n} 写入条数
    """
    if not assistant_text or not assistant_text.strip():
        return {"extracted": 0}

    extracted = 0

    try:
        from agent.memory_fabric import record as _mf_record
    except ImportError:
        logger.debug("memory_aware_executor: memory_fabric.record unavailable")
        return {"extracted": 0}

    # 快速预检：文本是否包含任何偏好或决策触发词
    # （没触发词就直接跳过，避免 _split_sentences 无谓开销）
    try:
        from agent._preference_keywords import ZH_PREFERENCE_TRIGGERS, EN_PREFERENCE_TRIGGERS
        _all_triggers = set(ZH_PREFERENCE_TRIGGERS) | set(EN_PREFERENCE_TRIGGERS) | set(_DECISION_KEYWORDS)
    except ImportError:
        _all_triggers = set(_DECISION_KEYWORDS)

    content_lower = assistant_text.lower()
    if not any(kw in content_lower for kw in _all_triggers):
        return {"extracted": 0}

    # 提取包含触发词的句子（最多 3 句）
    sentences = _split_sentences(assistant_text)
    hit_sentences = [
        s for s in sentences
        if any(kw in s.lower() for kw in _all_triggers)
    ]

    for s in hit_sentences[:3]:
        try:
            _mf_record({
                "source": "task_reflection",
                "layer": "note",
                "type": "preference_or_decision_extracted",
                "scope": scope,
                "pointer": f"session:{session_id or 'unknown'}",
                "fts_content": s[:500],
                # lifecycle_tag 不显式指定——由 _infer_lifecycle_tag
                # 根据内容中的触发词自动判断 preference/decision
            })
            extracted += 1
        except Exception as e:
            logger.debug("memory_aware_executor: record failed: %s", e)

    # P1: 通过 change_ledger 发 growth_moment 通知（前端 SSE 实时感知）
    if extracted:
        try:
            from agent.change_ledger import record_change
            for s in hit_sentences[:extracted]:
                _tag = "偏好" if any(kw in s for kw in ("喜欢", "偏好", "习惯", "更", "默认", "倾向", "prefer", "like", "always", "never")) else "决策"
                record_change(
                    kind="memory_learned",
                    tier="L0",  # L0 = 自动已读，不打扰
                    title=f"🌱 我学到了你的{_tag}",
                    summary=s[:80],
                )
        except Exception as e:
            logger.debug("memory_aware_executor: change_ledger failed: %s", e)

    if extracted:
        logger.info(
            "memory_aware_executor: post_task_reflect wrote %d memories, session=%s",
            extracted, session_id[:8] if session_id else "?",
        )
        # P2: 懒写日记（提取到新记忆后尝试写当日日记，同日只写一次）
        try:
            write_daily_diary()
        except Exception as e:
            logger.debug("memory_aware_executor: write_daily_diary failed: %s", e)

    return {"extracted": extracted}


def _split_sentences(text: str) -> List[str]:
    """简单分句：按中文句号/问号/感叹号 + 英文句号/问号/感叹号分割。"""
    import re
    parts = re.split(r'[。！？.!?]+', text)
    return [p.strip() for p in parts if p.strip() and len(p.strip()) > 5]


def reset_session_state():
    """重置会话级去重状态（测试用 / session 切换时调用）。"""
    global _recently_recalled_ids, _last_session_id
    _recently_recalled_ids = set()
    _last_session_id = None


# ── P2: 零 LLM 日记写入（纯聚合下游消费层）─────────────────────────
_DIARY_DIR: Optional[Path] = None  # lazy cache
_DIARY_WRITTEN_TODAY: Optional[str] = None  # "YYYY-MM-DD" 或 None，防同日重复写


def _diary_dir() -> Path:
    global _DIARY_DIR
    if _DIARY_DIR is None:
        _DIARY_DIR = Path.home() / ".vermes" / "diary"
        _DIARY_DIR.mkdir(parents=True, exist_ok=True)
    return _DIARY_DIR


def write_daily_diary() -> Optional[str]:
    """聚合今日记忆管理层结果 → 人读日记文件。零 LLM，纯聚合。

    聚合数据源（六路）：
    1. 今日 post_task_reflect 新写 preference/decision
    2. change_ledger memory_learned 事件
    3. memory_reflection 新标 flags
    4. memory_reflection 已解决 flags
    5. auto_resolve 降级统计
    6. curator 最近运行摘要（如有）

    Returns:
        日记文件路径，或 None（无内容可写 / 写入失败）
    """
    from datetime import date

    global _DIARY_WRITTEN_TODAY

    today = date.today().isoformat()

    # 防同日重复写（内容追加模式，只写一次）
    if _DIARY_WRITTEN_TODAY == today:
        return None

    sections: List[str] = [f"# {today} 成长日记"]

    # ── 1. post_task_reflect 新写 preference/decision ──
    try:
        from agent.memory_fabric import list_memories as _lm
        prefs = _lm(source="task_reflection", lifecycle_tag="preference", limit=10)
        decs = _lm(source="task_reflection", lifecycle_tag="decision", limit=10)
        learned_items: List[str] = []
        if isinstance(prefs, dict) and prefs.get("memories"):
            for m in prefs["memories"]:
                learned_items.append(f"  • 💬 偏好: {(m.get('fts_content') or m.get('content_preview') or '')[:100]}")
        if isinstance(decs, dict) and decs.get("memories"):
            for m in decs["memories"]:
                learned_items.append(f"  • 📌 决策: {(m.get('fts_content') or m.get('content_preview') or '')[:100]}")
        if learned_items:
            sections.append("## 🌱 今天我学到的")
            sections.extend(learned_items)
    except Exception as e:
        logger.debug("write_daily_diary: task_reflection read failed: %s", e)

    # ── 2. change_ledger memory_learned 事件 ──
    try:
        from agent.change_ledger import list_changes
        changes = list_changes(kind="memory_learned", limit=10)
        if changes:
            items = [f"  • {c.get('title', '')}" for c in changes[:10]]
            # 去重（可能和上面 preference/decision 重叠）
            items = [it for it in items if it not in sections]
            if items:
                sections.append("## 📡 感知到的变化")
                sections.extend(items)
    except Exception as e:
        logger.debug("write_daily_diary: change_ledger read failed: %s", e)

    # ── 3-4. memory_reflection flags ──
    try:
        from agent.memory_reflection import get_open_flags, get_resolved_flags, count_resolved_flags
        open_f = get_open_flags(limit=10)
        resolved_count = count_resolved_flags()
        flag_items: List[str] = []
        if open_f:
            by_type: Dict[str, int] = {}
            for f in open_f:
                ft = f.get("flag_type", "unknown")
                by_type[ft] = by_type.get(ft, 0) + 1
            flag_items.append(f"  • 标记 {len(open_f)} 条: " + ", ".join(f"{k}×{v}" for k, v in by_type.items()))
        if resolved_count:
            flag_items.append(f"  • 已解决 {resolved_count} 条")
        if flag_items:
            sections.append("## 🏷️ 记忆质量检查")
            sections.extend(flag_items)
    except Exception as e:
        logger.debug("write_daily_diary: reflection flags read failed: %s", e)

    # ── 5. auto_resolve 降级 ──
    try:
        from agent.memory_reflection import auto_resolve_eligible_flags
        # 不实际执行 auto_resolve（那由 reflection 自己跑），只读取统计
        # 改为从 resolved_flags 筛 resolution='auto_demote' 的
        from agent.memory_reflection import get_resolved_flags as _grf
        resolved = _grf(limit=20)
        demotes = [r for r in resolved if r.get("resolution") == "auto_demote"]
        if demotes:
            sections.append("## 🔧 自动降级")
            sections.append(f"  • 降级 {len(demotes)} 条为 ephemeral（技能来源重复/过时）")
    except Exception as e:
        logger.debug("write_daily_diary: auto_resolve read failed: %s", e)

    # ── 6. curator 最近运行 ──
    try:
        from agent.curator import load_state as _curator_state
        state = _curator_state()
        last_run = state.get("last_run")
        if last_run:
            sections.append("## 🗄️ 记忆生命周期")
            sections.append(f"  • 上次整理: {last_run}")
    except Exception as e:
        logger.debug("write_daily_diary: curator state read failed: %s", e)

    # 只有标题无内容 → 不写文件
    if len(sections) <= 1:
        return None

    content = "\n".join(sections) + "\n"
    diary_path = _diary_dir() / f"{today}.md"

    try:
        diary_path.write_text(content, encoding="utf-8")
        _DIARY_WRITTEN_TODAY = today
        logger.info("write_daily_diary: wrote %s (%d bytes)", diary_path, len(content))
        return str(diary_path)
    except Exception as e:
        logger.debug("write_daily_diary: file write failed: %s", e)
        return None
