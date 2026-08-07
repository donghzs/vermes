#!/usr/bin/env python3
"""Vermes 评测门禁 —— 跑 golden-set，与基线比对，输出结论。

这是 P4「评测闭环」的第四块拼图（前三块：verified 信号聚合 / 持久化 /
EvolutionPanel 接地率卡片）。它回答的问题是：**这次改动有没有把已经能干成的事干砸。**

用法：
    python scripts/eval_gate.py                     # 跑 + 与基线比对（warn-only，永远 exit 0）
    python scripts/eval_gate.py --strict            # 退步则 exit 1（CI 阻断态，需显式开启）
    python scripts/eval_gate.py --update-baseline   # 用本次结果覆盖基线
    python scripts/eval_gate.py --json              # 机器可读输出
    python scripts/eval_gate.py --only case_a case_b

设计取舍：

- **默认 warn-only。** 新门禁一上来就阻断 CI，只会训练出「习惯性 --no-verify」。
  先让它跑起来、积累几周信号、确认无假阳性，再由人决定何时打开 --strict。
- **只看退步，不看绝对值。** 门禁比较的是「本次 vs 基线」，不是「本次 vs 100%」。
  基线里有已知失败的 case 是合法状态，它们不该每次都把 CI 染红；
  但**从通过变成失败**必须报警 —— 这才是回归。
- **生产接地率是旁证不是门禁项。** get_verified_rate() 读的是真实运行库的
  ``__verified__`` 信号，跟离线 golden-set 相互独立：一个测「代码还能不能干活」，
  一个测「线上真实调用有多少拿到了外证」。这里只打印，不参与判定。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tests.eval.golden.harness import DEFAULT_MANIFEST, RunReport, run_manifest  # noqa: E402

DEFAULT_BASELINE = _REPO_ROOT / "tests" / "eval" / "baseline.scholarforge.json"
DEFAULT_RUNS_DIR = Path(os.path.expanduser("~/.vermes/eval_runs"))


# ──────────────────────────────────────────────────────────────
# 基线
# ──────────────────────────────────────────────────────────────

def load_baseline(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:  # noqa: BLE001
        print(f"⚠️  基线读取失败（按无基线处理）：{e}", file=sys.stderr)
        return None


def build_baseline(report: RunReport) -> Dict[str, Any]:
    return {
        "manifest": Path(report.manifest).name,
        "recorded_at": int(time.time()),
        "task_success_rate": round(report.task_success_rate, 4),
        "total": report.total,
        "passed": report.passed,
        # 逐 case 记录，才能分辨「总分没变但换了一批失败 case」这种伪稳定
        "cases": {c.id: c.passed for c in report.cases},
    }


def compare(report: RunReport, baseline: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """返回比对结论。regressions 非空即视为退步。"""
    cur = {c.id: c.passed for c in report.cases}
    if baseline is None:
        return {
            "has_baseline": False,
            "regressions": [],
            "fixes": [],
            "new_cases": sorted(cur),
            "missing_cases": [],
            "rate_delta": 0.0,
        }

    base_cases: Dict[str, bool] = baseline.get("cases", {}) or {}
    regressions = sorted(cid for cid, ok in cur.items() if base_cases.get(cid) and not ok)
    fixes = sorted(cid for cid, ok in cur.items() if ok and cid in base_cases and not base_cases[cid])
    new_cases = sorted(cid for cid in cur if cid not in base_cases)
    missing = sorted(cid for cid in base_cases if cid not in cur)
    # 基线里通过、现在整条 case 消失了 —— 等同于回归（删测试不能算修好）
    regressions += [f"{cid} (case removed)" for cid in missing if base_cases.get(cid)]

    return {
        "has_baseline": True,
        "regressions": regressions,
        "fixes": fixes,
        "new_cases": new_cases,
        "missing_cases": missing,
        "rate_delta": round(
            report.task_success_rate - float(baseline.get("task_success_rate", 0.0)), 4
        ),
    }


# ──────────────────────────────────────────────────────────────
# 旁证：生产接地率
# ──────────────────────────────────────────────────────────────

def read_verified_rate() -> Optional[float]:
    """读运行时 __verified__ 信号占比。任何异常都返回 None（旁证不该拖垮门禁）。"""
    try:
        from agent.evolution_manager import get_verified_rate

        return float(get_verified_rate())
    except Exception:  # noqa: BLE001
        return None


# ──────────────────────────────────────────────────────────────
# 输出
# ──────────────────────────────────────────────────────────────

def write_run_record(report: RunReport, diff: Dict[str, Any], runs_dir: Path) -> Optional[Path]:
    try:
        runs_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        out = runs_dir / f"{Path(report.manifest).stem}-{stamp}.json"
        payload = report.to_dict()
        payload["comparison"] = diff
        with open(out, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return out
    except Exception as e:  # noqa: BLE001
        print(f"⚠️  评测记录写入失败（不影响门禁结论）：{e}", file=sys.stderr)
        return None


def print_human(report: RunReport, diff: Dict[str, Any], verified: Optional[float],
                strict: bool, record: Optional[Path]) -> None:
    print("=" * 66)
    print(f"Vermes 评测门禁 · {Path(report.manifest).name}")
    print("=" * 66)
    print(f"通过 {report.passed}/{report.total}  "
          f"任务成功率 {report.task_success_rate:.1%}  用时 {report.duration_ms}ms")

    for c in report.cases:
        mark = "✅" if c.passed else "❌"
        print(f"  {mark} {c.id}  [{c.tool}]")
        if not c.passed:
            for line in c.failures:
                print(f"       └─ {line}")

    print("-" * 66)
    if not diff["has_baseline"]:
        print("ℹ️  未找到基线。用 --update-baseline 记录当前结果作为首个基线。")
    else:
        print(f"与基线相比：成功率 {diff['rate_delta']:+.1%}")
        if diff["fixes"]:
            print(f"  🟢 新修好：{', '.join(diff['fixes'])}")
        if diff["new_cases"]:
            print(f"  🆕 新增 case：{', '.join(diff['new_cases'])}")
        if diff["regressions"]:
            print(f"  🔴 退步：{', '.join(diff['regressions'])}")
        else:
            print("  🟢 无退步")

    if verified is not None:
        print(f"生产接地率（运行时 __verified__ 信号）：{verified:.1f}%  · 旁证，不参与门禁判定")
    else:
        print("生产接地率：不可用（无运行库或演进系统未激活）· 旁证，不参与门禁判定")

    if record:
        print(f"评测记录：{record}")

    print("-" * 66)
    if diff["regressions"]:
        if strict:
            print("❌ 门禁不通过（--strict）：检测到回归。")
        else:
            print("⚠️  检测到回归，但当前为 warn-only 模式，不阻断。加 --strict 可阻断。")
    else:
        print("✅ 门禁通过。")


# ──────────────────────────────────────────────────────────────
# main
# ──────────────────────────────────────────────────────────────

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Vermes golden-set 评测门禁")
    ap.add_argument("--manifest", default=str(DEFAULT_MANIFEST), help="golden-set manifest 路径")
    ap.add_argument("--baseline", default=str(DEFAULT_BASELINE), help="基线文件路径")
    ap.add_argument("--only", nargs="*", default=None, help="只跑指定 case id")
    ap.add_argument("--strict", action="store_true", help="检测到回归时 exit 1（默认 warn-only）")
    ap.add_argument("--update-baseline", action="store_true", help="用本次结果覆盖基线")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--no-record", action="store_true", help="不写评测记录文件")
    ap.add_argument("--runs-dir", default=str(DEFAULT_RUNS_DIR), help="评测记录目录")
    args = ap.parse_args(argv)

    report = run_manifest(args.manifest, only=args.only)
    baseline_path = Path(args.baseline)
    baseline = load_baseline(baseline_path)
    diff = compare(report, baseline)
    verified = read_verified_rate()

    record = None
    if not args.no_record:
        record = write_run_record(report, diff, Path(args.runs_dir))

    if args.update_baseline:
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        with open(baseline_path, "w", encoding="utf-8") as f:
            json.dump(build_baseline(report), f, ensure_ascii=False, indent=2)
            f.write("\n")
        print(f"📌 基线已更新：{baseline_path}")

    if args.json:
        payload = report.to_dict()
        payload["comparison"] = diff
        payload["verified_rate"] = verified
        payload["strict"] = args.strict
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print_human(report, diff, verified, args.strict, record)

    if diff["regressions"] and args.strict:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
