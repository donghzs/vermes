#!/usr/bin/env python3
"""Phase 0-B：技能使用度量（只读，决策 gate）。

产出四个数字（写入 reports/skill-usage-measurement_*.md）：
1. 加载率   — 有 skill_view 调用的会话 ÷ 总会话数
2. 用到率   — skill_view 后，同会话后续消息中出现该技能名的占比（代理指标）
3. 重复加载 — 同会话同名技能多次 skill_view 的分布
4. Top8 重尾 — .usage.json use_count 分布 / Top8 占比 / Gini

数据源：
- ~/.vermes/skills/.usage.json
- ~/.vermes/state.db（sessions / messages，只读）

用法：
  PYTHONPATH=. python scripts/measure_skill_usage.py \
    [--json reports/skill-usage-measurement-raw.json] \
    [--md reports/skill-usage-measurement_20260920.md]
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

try:
    from vermes_constants import get_vermes_home
except Exception:  # pragma: no cover
    def get_vermes_home() -> Path:
        return Path.home() / ".vermes"


def _gini(values: list[int]) -> float:
    """Gini coefficient of a non-negative list (0=equal, 1=concentrated)."""
    vals = sorted(v for v in values if v >= 0)
    n = len(vals)
    if n == 0 or sum(vals) == 0:
        return 0.0
    cum = 0.0
    for i, v in enumerate(vals, 1):
        cum += i * v
    return (2 * cum) / (n * sum(vals)) - (n + 1) / n


def measure_usage_json(home: Path) -> dict:
    path = home / "skills" / ".usage.json"
    out: dict = {
        "usage_json_path": str(path),
        "usage_records": 0,
        "runtime_skills": 0,
        "coverage_pct": 0.0,
        "use_counts": {},
        "top8": [],
        "top8_share_pct": 0.0,
        "gini": 0.0,
        "view_vs_use": {},
    }
    if not path.exists():
        return out
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        out["error"] = str(e)
        return out
    if not isinstance(data, dict):
        return out
    out["usage_records"] = len(data)
    use_counts: dict[str, int] = {}
    view_counts: dict[str, int] = {}
    for name, rec in data.items():
        if isinstance(rec, dict):
            use_counts[name] = int(rec.get("use_count") or 0)
            view_counts[name] = int(rec.get("view_count") or 0)
        elif isinstance(rec, int):
            use_counts[name] = rec
    n_skills = 0
    skills_root = home / "skills"
    if skills_root.is_dir():
        n_skills = sum(1 for _ in skills_root.rglob("SKILL.md"))
    out["runtime_skills"] = n_skills
    if n_skills:
        out["coverage_pct"] = round(100 * len(data) / n_skills, 2)
    ranked = sorted(use_counts.items(), key=lambda kv: kv[1], reverse=True)
    out["use_counts"] = dict(ranked)
    top8 = ranked[:8]
    out["top8"] = [{"skill": k, "use_count": v} for k, v in top8]
    total = sum(use_counts.values()) or 0
    out["top8_share_pct"] = round(100 * sum(v for _, v in top8) / total, 2) if total else 0.0
    out["gini"] = round(_gini(list(use_counts.values())), 4)
    out["view_vs_use"] = {
        "skills_with_views": sum(1 for v in view_counts.values() if v > 0),
        "skills_with_uses": sum(1 for v in use_counts.values() if v > 0),
        "total_views": sum(view_counts.values()),
        "total_uses": total,
    }
    return out


def _parse_tool_calls(raw) -> list[dict]:
    if not raw:
        return []
    calls = raw
    if isinstance(raw, str):
        try:
            calls = json.loads(raw)
        except Exception:
            return []
    if not isinstance(calls, list):
        return []
    out = []
    for c in calls:
        if isinstance(c, dict):
            out.append(c)
    return out


def _skill_from_call(call: dict) -> str | None:
    func = call.get("function") or {}
    name = func.get("name")
    if name != "skill_view":
        return None
    args = func.get("arguments")
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except Exception:
            return None
    if not isinstance(args, dict):
        return None
    skill = args.get("name")
    return skill.strip() if isinstance(skill, str) and skill.strip() else None


def measure_state_db(home: Path, days: int | None = None) -> dict:
    db = home / "state.db"
    out: dict = {
        "state_db_path": str(db),
        "state_db_bytes": db.stat().st_size if db.exists() else 0,
        "total_sessions": 0,
        "sessions_with_skill_view": 0,
        "load_rate_pct": 0.0,
        "skill_view_events": 0,
        "unique_skills_loaded": 0,
        "repeat_load": {
            "sessions_with_any_repeat": 0,
            "repeat_event_count": 0,
            "max_repeats_in_session": 0,
            "top_repeat_skills": [],
        },
        "used_after_load_pct": None,
        "used_after_load_detail": {
            "load_events": 0,
            "load_events_with_followup_mention": 0,
        },
        "error": None,
    }
    if not db.exists() or db.stat().st_size == 0:
        out["error"] = "state.db missing or empty"
        return out
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        cutoff = None
        if days is not None:
            cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
        if cutoff is not None:
            total = con.execute(
                "SELECT COUNT(*) AS n FROM sessions WHERE started_at >= ?", (cutoff,)
            ).fetchone()["n"]
        else:
            total = con.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()["n"]
        out["total_sessions"] = total

        if cutoff is not None:
            rows = con.execute(
                """
                SELECT m.session_id AS sid, m.tool_calls AS tc, m.timestamp AS ts
                FROM messages m
                JOIN sessions s ON s.id = m.session_id
                WHERE s.started_at >= ?
                  AND m.role = 'assistant' AND m.tool_calls IS NOT NULL
                ORDER BY m.session_id, m.timestamp, m.id
                """,
                (cutoff,),
            ).fetchall()
        else:
            rows = con.execute(
                """
                SELECT m.session_id AS sid, m.tool_calls AS tc, m.timestamp AS ts
                FROM messages m
                WHERE m.role = 'assistant' AND m.tool_calls IS NOT NULL
                ORDER BY m.session_id, m.timestamp, m.id
                """
            ).fetchall()

        # Collect skill_view events per session in order
        session_views: dict[str, list[tuple[str, float | None]]] = defaultdict(list)
        # Also index later assistant content / tool_calls per session for "used after load"
        session_later_text: dict[str, list[tuple[float | None, str]]] = defaultdict(list)

        # First pass: views
        view_sids = set()
        all_skills = Counter()
        for r in rows:
            for call in _parse_tool_calls(r["tc"]):
                skill = _skill_from_call(call)
                if skill:
                    session_views[r["sid"]].append((skill, r["ts"]))
                    view_sids.add(r["sid"])
                    all_skills[skill] += 1

        out["sessions_with_skill_view"] = len(view_sids)
        out["load_rate_pct"] = round(100 * len(view_sids) / total, 2) if total else 0.0
        out["skill_view_events"] = sum(all_skills.values())
        out["unique_skills_loaded"] = len(all_skills)

        # Repeat loads
        repeat_sessions = 0
        repeat_events = 0
        max_rep = 0
        skill_repeats = Counter()
        for sid, events in session_views.items():
            c = Counter(s for s, _ in events)
            reps = {k: v for k, v in c.items() if v > 1}
            if reps:
                repeat_sessions += 1
                repeat_events += sum(v - 1 for v in reps.values())
                max_rep = max(max_rep, max(reps.values()))
                for k, v in reps.items():
                    skill_repeats[k] += v - 1
        out["repeat_load"] = {
            "sessions_with_any_repeat": repeat_sessions,
            "repeat_event_count": repeat_events,
            "max_repeats_in_session": max_rep,
            "top_repeat_skills": [
                {"skill": k, "extra_loads": v}
                for k, v in skill_repeats.most_common(8)
            ],
        }

        # Used-after-load: for each skill_view, look at later messages in same session
        if view_sids:
            if cutoff is not None:
                all_msgs = con.execute(
                    """
                    SELECT m.session_id AS sid, m.role AS role, m.content AS content,
                           m.tool_calls AS tc, m.timestamp AS ts, m.id AS id
                    FROM messages m
                    JOIN sessions s ON s.id = m.session_id
                    WHERE s.started_at >= ? AND m.session_id IN ({})
                    ORDER BY m.session_id, m.timestamp, m.id
                    """.format(",".join("?" * len(view_sids))),
                    (cutoff, *sorted(view_sids)),
                ).fetchall()
            else:
                all_msgs = con.execute(
                    """
                    SELECT m.session_id AS sid, m.role AS role, m.content AS content,
                           m.tool_calls AS tc, m.timestamp AS ts, m.id AS id
                    FROM messages m
                    WHERE m.session_id IN ({})
                    ORDER BY m.session_id, m.timestamp, m.id
                    """.format(",".join("?" * len(view_sids))),
                    sorted(view_sids),
                ).fetchall()

            by_sid: dict[str, list] = defaultdict(list)
            for r in all_msgs:
                by_sid[r["sid"]].append(r)

            load_events = 0
            used_events = 0
            for sid, views in session_views.items():
                msgs = by_sid.get(sid, [])
                # reconstruct ordered (skill, ts, idx) — use list position
                view_positions = []  # (skill, msg_index)
                vi = 0
                for idx, m in enumerate(msgs):
                    for call in _parse_tool_calls(m["tc"]):
                        skill = _skill_from_call(call)
                        if skill:
                            view_positions.append((skill, idx))
                for skill, pos in view_positions:
                    load_events += 1
                    # Skip tool-role rows that are the skill_view response itself
                    # (they always contain the skill name → tautological hit).
                    later = []
                    skip_tool_result = True
                    for lm in msgs[pos + 1 :]:
                        role = lm["role"]
                        if role == "tool" and skip_tool_result:
                            # drop the tool result(s) for this view call
                            continue
                        skip_tool_result = False
                        later.append(lm)
                    hit = False
                    for lm in later:
                        content = (lm["content"] or "") if isinstance(lm["content"], str) else str(lm["content"] or "")
                        if skill and skill in content:
                            hit = True
                            break
                        for call in _parse_tool_calls(lm["tc"]):
                            if skill and skill in json.dumps(call, ensure_ascii=False):
                                hit = True
                                break
                        if hit:
                            break
                    if hit:
                        used_events += 1

            out["used_after_load_detail"] = {
                "load_events": load_events,
                "load_events_with_followup_mention": used_events,
            }
            out["used_after_load_pct"] = (
                round(100 * used_events / load_events, 2) if load_events else None
            )

        con.close()
    except Exception as e:
        out["error"] = str(e)
    return out


def render_md(payload: dict) -> str:
    u = payload.get("usage_json") or {}
    s = payload.get("state_db") or {}
    lines = [
        "# 技能使用度量 · Phase 0-B · 2026-09-20",
        "",
        "> **性质**：只读度量，Phase 1（L2 prefetch）立项 gate。",
        "> **脚本**：`scripts/measure_skill_usage.py`",
        f"> **生成时间**：{payload.get('generated_at')}",
        "",
        "## 1. 四个硬指标",
        "",
        "| 指标 | 数值 | 口径 |",
        "|---|---:|---|",
        f"| **加载率** | **{s.get('load_rate_pct', '—')}%** | 有 `skill_view` 的会话 / 总会话（{s.get('sessions_with_skill_view', '—')}/{s.get('total_sessions', '—')}） |",
        f"| **用到率（代理）** | **{s.get('used_after_load_pct', '—')}%** | skill_view **之后**（跳过该次 tool 回包）同会话后续 assistant/tool 文本出现该技能名（{s.get('used_after_load_detail', {}).get('load_events_with_followup_mention', '—')}/{s.get('used_after_load_detail', {}).get('load_events', '—')} 次加载） |",
        f"| **重复加载** | 会话 {s.get('repeat_load', {}).get('sessions_with_any_repeat', '—')} 次出现重复；多余加载 {s.get('repeat_load', {}).get('repeat_event_count', '—')}；单会话最多 {s.get('repeat_load', {}).get('max_repeats_in_session', '—')} | 同会话同名 skill_view >1 |",
        f"| **Top8 重尾** | **{u.get('top8_share_pct', '—')}%**（Gini={u.get('gini', '—')}） | `.usage.json` use_count Top8 / 总量 |",
        "",
        "## 2. usage.json 库存",
        "",
        "| 项 | 值 |",
        "|---|---|",
        f"| 记录数 | {u.get('usage_records')} |",
        f"| 运行时技能数 | {u.get('runtime_skills')} |",
        f"| 覆盖率 | {u.get('coverage_pct')}% |",
        f"| 有 view 的技能 | {u.get('view_vs_use', {}).get('skills_with_views')} |",
        f"| 有 use 的技能 | {u.get('view_vs_use', {}).get('skills_with_uses')} |",
        f"| 总 view / 总 use | {u.get('view_vs_use', {}).get('total_views')} / {u.get('view_vs_use', {}).get('total_uses')} |",
        "",
        "### Top8 use_count",
        "",
        "| # | 技能 | use_count |",
        "|---:|---|---:|",
    ]
    for i, row in enumerate(u.get("top8") or [], 1):
        lines.append(f"| {i} | {row.get('skill')} | {row.get('use_count')} |")
    lines += [
        "",
        "### 重复加载 Top",
        "",
        "| 技能 | 额外加载次数 |",
        "|---|---:|",
    ]
    for row in s.get("repeat_load", {}).get("top_repeat_skills") or []:
        lines.append(f"| {row.get('skill')} | {row.get('extra_loads')} |")
    lines += [
        "",
        "## 3. 会话侧",
        "",
        "| 项 | 值 |",
        "|---|---|",
        f"| state.db | `{s.get('state_db_path')}` ({s.get('state_db_bytes')} bytes) |",
        f"| skill_view 事件总数 | {s.get('skill_view_events')} |",
        f"| 唯一被加载技能数 | {s.get('unique_skills_loaded')} |",
        f"| error | {s.get('error')} |",
        "",
        "## 4. Gate 解读（供拍板）",
        "",
        "请结合上表判断：",
        "",
        "- 若 **加载率低** 且 **用到率高**：技能少而准，L2 路由收益有限 → 可缓做 Phase 1。",
        "- 若 **加载率高** 但 **用到率低** / **重复加载高**：选不准或乱猜 → 支持 Phase 1 prefetch。",
        "- 若 **Top8 极重** + 覆盖率低：优先技能库治理/裁剪，路由次之。",
        "",
        "## 5. 指针",
        "",
        "- 执行方案：`reports/qclaw/skill-management-执行方案_20260920.md`",
        "- 决策简报：`reports/skill-routing-decision-brief_20260920.md`",
        "",
        "— scripts/measure_skill_usage.py · 只读 · 未改生产数据",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure skill usage (read-only)")
    parser.add_argument("--home", default=None, help="VERMES home (default: get_vermes_home())")
    parser.add_argument("--days", type=int, default=None, help="Only sessions started in last N days")
    parser.add_argument("--json", default="reports/skill-usage-measurement-raw.json")
    parser.add_argument("--md", default="reports/skill-usage-measurement_20260920.md")
    args = parser.parse_args(argv)

    home = Path(args.home) if args.home else get_vermes_home()
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "home": str(home),
        "usage_json": measure_usage_json(home),
        "state_db": measure_state_db(home, days=args.days),
    }
    json_path = Path(args.json)
    md_path = Path(args.md)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_md(payload), encoding="utf-8")

    s = payload["state_db"]
    u = payload["usage_json"]
    print("=== Skill usage measurement ===")
    print(f"home: {home}")
    print(f"load_rate: {s.get('load_rate_pct')}% ({s.get('sessions_with_skill_view')}/{s.get('total_sessions')} sessions)")
    print(f"used_after_load: {s.get('used_after_load_pct')}%")
    print(f"repeat: {s.get('repeat_load')}")
    print(f"top8_share: {u.get('top8_share_pct')}%  gini={u.get('gini')}  coverage={u.get('coverage_pct')}%")
    print(f"wrote: {json_path}")
    print(f"wrote: {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
