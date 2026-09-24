#!/usr/bin/env python3
"""统计 canary-schedule 判定行：是否已连续 N 次零人工全绿。

用法:
  python3 scripts/canary_streak.py --need 3
退出码 0 = 达标；1 = 未达标。

**重要（2026-09-24 订正）**：`trailing_streak` 只数「行数」，**不判时间间隔**。
同一静止窗口内连打 N 次证明的是「门禁可重复通过」，不是「机制随时间自持」。
停止条件② 要求**跨自然日的 launchd 自动跑** —— 判读时看 `ts` 是否落在不同日期。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "reports" / "canary-schedule" / "verdicts.jsonl"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--need", type=int, default=3)
    ap.add_argument("--state", default=str(STATE))
    args = ap.parse_args()

    path = Path(args.state)
    if not path.exists():
        print(f"NO_DATA {path}")
        return 1
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if not rows:
        print("NO_ROWS")
        return 1

    def is_green(r: dict) -> bool:
        return (
            r.get("canary_rc") == 0
            and r.get("boundary_rc") == 0
            and r.get("gold_rc") == 0
            and r.get("unregistered_tax") == 0
        )

    streak = 0
    for r in reversed(rows):
        if is_green(r):
            streak += 1
        else:
            break
    # 时间维：跨几个自然日（停止条件② 的真判据）
    green_days = []
    for r in rows:
        if is_green(r):
            ts = str(r.get("ts", ""))
            day = ts[:8] if len(ts) >= 8 else ts
            if day not in green_days:
                green_days.append(day)
    trailing_days = 0
    seen = None
    for r in reversed(rows):
        if not is_green(r):
            break
        ts = str(r.get("ts", ""))
        day = ts[:8] if len(ts) >= 8 else ts
        if seen is None or day != seen:
            trailing_days += 1
            seen = day
    green_n = sum(1 for r in rows if is_green(r))
    print(
        f"rows={len(rows)} green_total={green_n} trailing_streak={streak} "
        f"trailing_days={trailing_days} distinct_green_days={len(green_days)} need={args.need}"
    )
    print(
        "NOTE: trailing_streak 不判时间间隔；停止条件② 看 trailing_days（跨自然日自动跑）"
    )
    for r in rows[-args.need - 2 :]:
        mark = "GREEN" if is_green(r) else "RED"
        print(f"  {r.get('ts')} {mark} canary={r.get('canary_rc')} boundary={r.get('boundary_rc')} gold={r.get('gold_rc')} tax={r.get('unregistered_tax')}")
    return 0 if (streak >= args.need and trailing_days >= args.need) else 1


if __name__ == "__main__":
    sys.exit(main())
