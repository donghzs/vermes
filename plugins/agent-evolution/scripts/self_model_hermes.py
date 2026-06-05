#!/usr/bin/env python3
"""
Agent Self-Model — 自动记录执行结果、更新自我认知、提供策略建议。

用法:
  # 记录一次执行结果
  python3 self_model.py record --task build --action "npm install" --tool terminal --success 1 --retries 0

  # 查询自我模型
  python3 self_model.py status

  # 获取当前任务的策略建议
  python3 self_model.py advise --task build

  # 从 session 数据自动分析并更新 self-model
  python3 self_model.py analyze

  # 记录反模式
  python3 self_model.py anti-pattern --pattern "不看源码就改代码" --correct "先 read_file 再 patch"
"""

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime

DB_PATH = os.path.expanduser("~/.hermes/self-model.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def record_outcome(task_type, action, tool_used, success, retries=0, root_cause=None, notes=None, session_id=None, duration=None):
    """Record a single execution outcome."""
    conn = get_db()
    conn.execute(
        """INSERT INTO outcomes (session_id, task_type, action, tool_used, success, retry_count, root_cause, duration_seconds, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (session_id, task_type, action, tool_used, success, retries, root_cause, duration, notes),
    )
    conn.commit()

    # Auto-update self-model
    _update_self_model(conn, task_type)
    conn.close()

    status = "✅" if success == 1 else "⚠️" if success == -1 else "❌"
    print(f"{status} Recorded: [{task_type}] {action} (tool={tool_used}, retries={retries})")


def _update_self_model(conn, task_type):
    """Recalculate self-model for a task type from outcomes."""
    row = conn.execute(
        """SELECT
             COUNT(*) as total,
             SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as successes,
             SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) as failures,
             AVG(retry_count) as avg_r
           FROM outcomes WHERE task_type = ?""",
        (task_type,),
    ).fetchone()

    total = row["total"]
    if total < 3:
        return  # Need at least 3 data points

    rate = row["successes"] / total if total > 0 else 0.5
    failures = row["failures"] or 0
    avg_r = row["avg_r"] or 0

    # Get common failures
    fail_rows = conn.execute(
        """SELECT root_cause, COUNT(*) as cnt
           FROM outcomes WHERE task_type = ? AND success = 0 AND root_cause IS NOT NULL
           GROUP BY root_cause ORDER BY cnt DESC LIMIT 3""",
        (task_type,),
    ).fetchall()
    common_fails = [r["root_cause"] for r in fail_rows]

    # Confidence: based on success rate and sample size
    # Bayesian-ish: starts at 0.5, moves toward observed rate
    confidence = (rate * total + 0.5 * 5) / (total + 5)

    conn.execute(
        """INSERT OR REPLACE INTO self_model
           (dimension, success_rate, total_attempts, success_count, failure_count,
            avg_retries, common_failures, confidence, last_updated)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
        (task_type, rate, total, row["successes"], failures, avg_r, json.dumps(common_fails), confidence),
    )
    conn.commit()


def get_status():
    """Show current self-model."""
    conn = get_db()

    print("=" * 50)
    print("  Agent Self-Model (自我认知)")
    print("=" * 50)

    rows = conn.execute("SELECT * FROM self_model ORDER BY total_attempts DESC").fetchall()
    if not rows:
        print("  (no data yet — start recording outcomes)")
        return

    for r in rows:
        bar_len = int(r["success_rate"] * 20)
        bar = "█" * bar_len + "░" * (20 - bar_len)
        conf = "🟢" if r["confidence"] > 0.7 else "🟡" if r["confidence"] > 0.4 else "🔴"
        print(f"\n  {r['dimension'].upper()} {conf}")
        print(f"    Success: [{bar}] {r['success_rate']:.0%}")
        print(f"    Attempts: {r['total_attempts']} (✅{r['success_count']} ❌{r['failure_count']})")
        print(f"    Avg retries: {r['avg_retries']:.1f}")
        if r["common_failures"]:
            fails = json.loads(r["common_failures"])
            print(f"    Common failures: {', '.join(fails)}")

    # Anti-patterns
    aps = conn.execute("SELECT * FROM anti_patterns ORDER BY occurrences DESC LIMIT 5").fetchall()
    if aps:
        print(f"\n{'=' * 50}")
        print("  Anti-Patterns (反模式)")
        print("=" * 50)
        for ap in aps:
            print(f"  ❌ {ap['pattern']}")
            print(f"     → {ap['correct_approach']}")

    conn.close()


def advise(task_type):
    """Give strategy advice for a task type."""
    conn = get_db()

    model = conn.execute("SELECT * FROM self_model WHERE dimension = ?", (task_type,)).fetchone()
    aps = conn.execute(
        """SELECT * FROM anti_patterns WHERE root_cause = ? OR pattern LIKE ? ORDER BY occurrences DESC LIMIT 3""",
        (task_type, f"%{task_type}%"),
    ).fetchall()

    if not model:
        print(f"No data for '{task_type}'. Proceeding with default approach.")
        conn.close()
        return

    print(f"Self-model for '{task_type}':")
    print(f"  Confidence: {model['confidence']:.0%}")
    print(f"  Historical success rate: {model['success_rate']:.0%}")

    if model["confidence"] < 0.5:
        print("  ⚠️ LOW CONFIDENCE — Recommend:")
        print("    1. Break task into smaller steps")
        print("    2. Verify each step before proceeding")
        print("    3. Ask for confirmation on critical changes")
    elif model["confidence"] < 0.7:
        print("  🟡 MEDIUM CONFIDENCE — Recommend:")
        print("    1. Read source code first")
        print("    2. Verify environment before executing")
    else:
        print("  🟢 HIGH CONFIDENCE — Can proceed efficiently")

    if model["common_failures"]:
        fails = json.loads(model["common_failures"])
        print(f"  Watch out for: {', '.join(fails)}")

    if aps:
        print("  Anti-patterns to avoid:")
        for ap in aps:
            print(f"    ❌ {ap['pattern']}")

    conn.close()


def add_anti_pattern(pattern, correct_approach, root_cause=None):
    """Record an anti-pattern."""
    conn = get_db()

    # Check if similar pattern exists
    existing = conn.execute(
        "SELECT id, occurrences FROM anti_patterns WHERE pattern LIKE ?",
        (f"%{pattern[:30]}%",),
    ).fetchone()

    if existing:
        conn.execute(
            "UPDATE anti_patterns SET occurrences = occurrences + 1, last_seen = datetime('now') WHERE id = ?",
            (existing["id"],),
        )
        print(f"🔄 Anti-pattern updated (occurrence #{existing['occurrences'] + 1}): {pattern}")
    else:
        conn.execute(
            "INSERT INTO anti_patterns (pattern, correct_approach, root_cause) VALUES (?, ?, ?)",
            (pattern, correct_approach, root_cause),
        )
        print(f"❌ New anti-pattern recorded: {pattern}")

    conn.commit()
    conn.close()


def analyze_sessions():
    """Analyze recent session data to extract outcomes."""
    state_db = os.path.expanduser("~/.hermes/state.db")
    if not os.path.exists(state_db):
        print("No state.db found")
        return

    self_conn = get_db()
    state_conn = sqlite3.connect(state_db)
    state_conn.row_factory = sqlite3.Row

    # Get recent sessions
    sessions = state_conn.execute(
        """SELECT id, title, source, started_at, message_count, tool_call_count
           FROM sessions ORDER BY started_at DESC LIMIT 20"""
    ).fetchall()

    print(f"Analyzing {len(sessions)} recent sessions...")

    for s in sessions:
        sid = s["id"]
        title = s["title"] or ""

        # Classify task type from title
        task_type = "general"
        title_lower = title.lower()
        if any(w in title_lower for w in ["build", "构建", "编译", "安装", "发布", "deploy"]):
            task_type = "build"
        elif any(w in title_lower for w in ["debug", "debug", "修复", "fix", "bug", "error", "报错"]):
            task_type = "debug"
        elif any(w in title_lower for w in ["研究", "research", "分析", "analyze"]):
            task_type = "research"
        elif any(w in title_lower for w in ["config", "配置", "setup", "设置"]):
            task_type = "config"
        elif any(w in title_lower for w in ["创意", "设计", "design", "ui", "前端"]):
            task_type = "creative"

        # Check tool calls in this session
        tools = state_conn.execute(
            """SELECT role, content FROM messages WHERE session_id = ? ORDER BY id""",
            (sid,),
        ).fetchall()

        tool_errors = 0
        tool_successes = 0
        for t in tools:
            if t["role"] == "tool" and t["content"]:
                content = t["content"] if isinstance(t["content"], str) else str(t["content"])
                if "error" in content.lower() or "failed" in content.lower() or "exit_code" in content:
                    # Check if it's actually an error (exit code != 0)
                    if '"exit_code": 0' not in content and '"exit_code"' in content:
                        tool_errors += 1
                    else:
                        tool_successes += 1
                elif "exit_code" in content:
                    tool_successes += 1

        total_tools = tool_errors + tool_successes
        if total_tools > 0:
            success = 1 if tool_errors == 0 else (-1 if tool_errors < tool_successes else 0)
            self_conn.execute(
                """INSERT INTO outcomes (session_id, task_type, action, tool_used, success, retry_count, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (sid, task_type, title[:100], "session", success, tool_errors, f"tools={total_tools}"),
            )

    self_conn.commit()

    # Update all self-models
    for task_type in ["build", "debug", "research", "config", "creative", "general"]:
        _update_model_for_type(self_conn, task_type)

    self_conn.close()
    state_conn.close()
    print("Analysis complete. Run 'python3 self_model.py status' to see results.")


def _update_model_for_type(conn, task_type):
    """Update self-model for a specific task type from session outcomes."""
    row = conn.execute(
        """SELECT COUNT(*) as total,
                  SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as successes,
                  SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) as failures,
                  AVG(retry_count) as avg_r
           FROM outcomes WHERE task_type = ?""",
        (task_type,),
    ).fetchone()

    if row["total"] < 2:
        return

    rate = row["successes"] / row["total"]
    confidence = (rate * row["total"] + 0.5 * 5) / (row["total"] + 5)

    conn.execute(
        """INSERT OR REPLACE INTO self_model
           (dimension, success_rate, total_attempts, success_count, failure_count,
            avg_retries, confidence, last_updated)
           VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
        (task_type, rate, row["total"], row["successes"], row["failures"], row["avg_r"], confidence),
    )
    conn.commit()


def main():
    parser = argparse.ArgumentParser(description="Agent Self-Model")
    sub = parser.add_subparsers(dest="cmd")

    # record
    p_rec = sub.add_parser("record", help="Record an outcome")
    p_rec.add_argument("--task", required=True, help="Task type")
    p_rec.add_argument("--action", required=True, help="What was attempted")
    p_rec.add_argument("--tool", default="unknown", help="Tool used")
    p_rec.add_argument("--success", type=int, required=True, help="1=ok, 0=fail, -1=partial")
    p_rec.add_argument("--retries", type=int, default=0)
    p_rec.add_argument("--cause", help="Root cause of failure")
    p_rec.add_argument("--notes", help="Additional notes")
    p_rec.add_argument("--session", help="Session ID")

    # status
    sub.add_parser("status", help="Show self-model")

    # advise
    p_adv = sub.add_parser("advise", help="Get strategy advice")
    p_adv.add_argument("--task", required=True, help="Task type")

    # anti-pattern
    p_ap = sub.add_parser("anti-pattern", help="Record anti-pattern")
    p_ap.add_argument("--pattern", required=True, help="What NOT to do")
    p_ap.add_argument("--correct", required=True, help="What TO do instead")
    p_ap.add_argument("--cause", help="Root cause category")

    # analyze
    sub.add_parser("analyze", help="Analyze sessions and update self-model")

    args = parser.parse_args()

    if args.cmd == "record":
        record_outcome(args.task, args.action, args.tool, args.success, args.retries, args.cause, args.notes, args.session)
    elif args.cmd == "status":
        get_status()
    elif args.cmd == "advise":
        advise(args.task)
    elif args.cmd == "anti-pattern":
        add_anti_pattern(args.pattern, args.correct, args.cause)
    elif args.cmd == "analyze":
        analyze_sessions()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
