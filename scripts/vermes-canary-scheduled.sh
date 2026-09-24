#!/bin/bash
# vermes-canary-scheduled.sh — 定时 canary（零人工干预门禁）
# 由 launchd 调起；也可手动跑一次。只读上游 + 本仓，不改产品代码。
set -uo pipefail
ROOT="/Users/dongzusheng/Projects/vermes-electron"
UP="${VERMES_UPSTREAM_REPO:-/Users/dongzusheng/.hermes/hermes-agent}"
LOG_DIR="$ROOT/reports/canary-schedule"
mkdir -p "$LOG_DIR"
TS=$(date -u +%Y%m%dT%H%M%SZ)
LOG="$LOG_DIR/${TS}.log"
STATE="$LOG_DIR/verdicts.jsonl"

{
  echo "=== vermes canary scheduled $TS ==="
  echo "ROOT=$ROOT"
  echo "UP=$UP"
  cd "$ROOT" || exit 2
  echo "HEAD=$(git rev-parse --short HEAD)"
  git status -sb | head -3

  echo "--- boundary ---"
  python3 scripts/upstream_watch.py boundary 2>&1 | tail -5
  BOUND_RC=$?

  echo "--- gold ---"
  PYTHONPATH=. .venv/bin/python scripts/s2_snapshot.py --check 2>&1 | tail -5
  GOLD_RC=$?

  echo "--- canary ---"
  python3 scripts/upstream_canary.py --upstream-repo "$UP" --max-intake 500 2>&1 | tail -15
  CAN_RC=$?

  # 追加一行判定，供「连续 N 次」统计
  BOUND_TAX=$(python3 scripts/upstream_watch.py boundary 2>/dev/null | sed -n 's/.*未登记税=\([0-9]*\).*/\1/p' | tail -1)
  python3 - "$STATE" "$TS" "$BOUND_RC" "$GOLD_RC" "$CAN_RC" "${BOUND_TAX:-}" <<'PY'
import json, sys, os
path, ts, b, g, c, tax = sys.argv[1:7]
rec = {
    "ts": ts,
    "boundary_rc": int(b) if b.isdigit() else -1,
    "gold_rc": int(g) if g.isdigit() else -1,
    "canary_rc": int(c) if c.isdigit() else -1,
    "unregistered_tax": int(tax) if tax.isdigit() else -1,
}
os.makedirs(os.path.dirname(path), exist_ok=True)
with open(path, "a", encoding="utf-8") as fh:
    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
print("VERDICT_LINE", json.dumps(rec, ensure_ascii=False))
PY

  echo "=== done $TS ==="
} | tee "$LOG"

echo "log=$LOG"
