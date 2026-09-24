#!/usr/bin/env python3
"""统计 canary-schedule 判定行：是否已连续 N 次零人工全绿。

用法:
  python3 scripts/canary_streak.py --need 3
退出码 0 = 达标；1 = 未达标。
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
    green_n = sum(1 for r in rows if is_green(r))
    print(f"rows={len(rows)} green_total={green_n} trailing_streak={streak} need={args.need}")
    for r in rows[-args.need - 2 :]:
        mark = "GREEN" if is_green(r) else "RED"
        print(f"  {r.get('ts')} {mark} canary={r.get('canary_rc')} boundary={r.get('boundary_rc')} gold={r.get('gold_rc')} tax={r.get('unregistered_tax')}")
    return 0 if streak >= args.need else 1


if __name__ == "__main__":
    sys.exit(main())
