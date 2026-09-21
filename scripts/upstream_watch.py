#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""upstream_watch.py — Hermes 上游雷达 + 发行版边界闸门

## 为什么需要它（硬约束，2026-09-21 实测）

Vermes 与上游 `upstream = NousResearch/hermes-agent` 的 **git 历史完全不相交**：

    $ git merge-base main upstream/main     # → 空（且 --is-shallow-repository = false）
    $ git rev-list --count main..upstream/main   # → 39490
    $ git rev-list --count upstream/main..main   # → 1758

即：**没有共同祖先**，不是 fork 而是"代码导入 + 独立演化"。
后果：`git merge upstream/main` 不可用；`git cherry-pick` 虽可执行但补丁上下文
已 diverge，冲突率极高。所以「取长上游」在 Vermes 的正确形态不是 git 级合并，
而是 **能力级移植**：人工判断 + 单文件/单特性搬运 + 契约测试兜底。

本脚本把其中的"上游有什么新东西、哪些碰不得"从人工考古变成可复跑的清单。

## 子命令

    watch     上游雷达：基线以来的上游改动 → 按发行版分区分类 →
              输出「取长候选 + 红线告警」
    boundary  边界闸门：Vermes 自身改动是否落在「上游跟随区」
              （落在跟随区 = 契约税，需登记或外置为插件）

## 用法

    python3 scripts/upstream_watch.py watch              # 默认自上次基线
    python3 scripts/upstream_watch.py watch --since v2026.9.14 --max 400
    python3 scripts/upstream_watch.py watch --fetch      # 先 git fetch upstream
    python3 scripts/upstream_watch.py boundary --since v2.5.1

分区定义见 docs/DISTRIBUTION_MANIFEST.md（本文件内置同一份默认值，二者需同步）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORTS_DIR = os.path.join(ROOT, "reports")
BASELINE_PATH = os.path.join(REPORTS_DIR, ".upstream-baseline.json")

UPSTREAM_REF = "upstream/main"

# ---------------------------------------------------------------------------
# 发行版分区（与 docs/DISTRIBUTION_MANIFEST.md 保持一致）
#   own    发行版自有资产（红线）：上游同名改动不得直接合，只可"参考思路"
#   follow 上游跟随区：优先跟随上游；Vermes 侧在此区改动 = 契约税（需登记）
#   core   同源核心（已深度 diverge）：个案评估，需对照 diverge 度量
# ---------------------------------------------------------------------------
ZONES: dict[str, list[str]] = {
    "own": [
        "vermes_cli/",            # Vermes CLI 包（上游无此命名空间）
        "agent/memory_fabric.py",          # 记忆织物（1,377 行，Vermes 领先项）
        "agent/capability_evolver.py",     # 自进化（485 行）
        "agent/workflow_runtime.py",       # 工作流 DAG
        "agent/compression_scheduler.py",  # 上下文压缩调度
        "gateway/platforms/",     # 中文平台 17 个 / 40 文件
        "scholarforge/",          # 论文写作
        "acp_registry/",          # 神魔堂 org 集成
        "frontend/",              # 中文化前端
        "electron/",              # 打包壳
        "installer/",
        "locales/",
        "scripts/build-",         # 打包链（PyInstaller / NSIS / DMG）
    ],
    "follow": [
        "plugins/",               # 上游插件生态：Vermes 应尽量零改动
        "tools/",
        "harness/",
        "cron/",
        ".github/",
        "docs/",
        "scripts/",
    ],
    "core": [
        "agent/",
        "gateway/",
        "acp_adapter/",
        "memory/",
        "runner/",
        "cli/",
    ],
}

# 上游 commit 主题里的"高价值"信号（优先作为取长候选）
VALUE_PATTERNS = [
    (re.compile(r"^fix(security|\(security\))", re.I), "security", 3),
    (re.compile(r"^fix(\(|:)", re.I), "bugfix", 2),
    (re.compile(r"^feat(\(|:)", re.I), "feature", 1),
    (re.compile(r"^perf(\(|:)", re.I), "perf", 2),
    (re.compile(r"^test(\(|:)|^chore\(deps\)", re.I), "chore", 0),
]


def git(*args: str) -> str:
    """跑 git 命令；失败返回空串（本脚本是只读雷达，不因单点失败中断）。"""
    try:
        out = subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
        )
        return out.stdout
    except subprocess.CalledProcessError as exc:
        print(f"[warn] git {' '.join(args)} 失败: {exc.stderr.strip()[:200]}", file=sys.stderr)
        return ""


def classify(path: str) -> str:
    """把一条路径映射到发行版分区。own 优先（更具体的前缀在前）。"""
    for name in ("own", "follow", "core"):
        for prefix in ZONES[name]:
            if path.startswith(prefix):
                return name
    return "other"


def value_of(subject: str) -> tuple[str, int]:
    for pat, label, score in VALUE_PATTERNS:
        if pat.search(subject):
            return label, score
    return "other", 0


def load_baseline() -> str | None:
    if os.path.exists(BASELINE_PATH):
        try:
            with open(BASELINE_PATH, encoding="utf-8") as fh:
                return json.load(fh).get("upstream_head")
        except Exception:
            return None
    return None


def save_baseline(head: str, since: str, n_commits: int) -> None:
    os.makedirs(REPORTS_DIR, exist_ok=True)
    payload = {
        "upstream_head": head,
        "since": since,
        "commits_scanned": n_commits,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    with open(BASELINE_PATH, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


def collect_commits(ref_range: str, max_commits: int) -> list[dict]:
    """解析 `git log --name-only`，返回 commit 列表（含改动路径）。"""
    sep = "\x1f"
    fmt = f"%H{sep}%ad{sep}%s"
    raw = git(
        "log", ref_range, "--name-only", f"--pretty=format:{fmt}",
        "--date=short", f"--max-count={max_commits}",
    )
    if not raw.strip():
        return []
    commits: list[dict] = []
    cur: dict | None = None
    for line in raw.splitlines():
        if sep in line and line.count(sep) >= 2:
            h, date, subject = line.split(sep, 2)
            cur = {"hash": h[:12], "date": date, "subject": subject, "paths": []}
            commits.append(cur)
        elif line.strip() and cur is not None:
            cur["paths"].append(line.strip())
    return commits


def cmd_watch(args: argparse.Namespace) -> int:
    if args.fetch:
        print("[watch] git fetch upstream --tags --prune ...")
        git("fetch", "upstream", "--tags", "--prune")

    head = git("rev-parse", "--short", UPSTREAM_REF).strip()
    if not head:
        print(f"[error] 无法解析 {UPSTREAM_REF}；先 `git fetch upstream`", file=sys.stderr)
        return 2

    since = args.since or load_baseline() or git("describe", "--tags", "--abbrev=0", UPSTREAM_REF).strip()
    if not since:
        print("[error] 无法确定基线，请用 --since 指定（如 v2026.9.14）", file=sys.stderr)
        return 2

    print(f"[watch] 基线 {since} → {UPSTREAM_REF} ({head})，上限 {args.max} commits")
    commits = collect_commits(f"{since}..{UPSTREAM_REF}", args.max)
    if not commits:
        print("[watch] 无新增提交（或区间为空）")
        return 0

    zone_counter: Counter[str] = Counter()
    value_counter: Counter[str] = Counter()
    path_counter: Counter[str] = Counter()
    candidates: list[tuple[int, dict]] = []
    redline_hits: list[tuple[dict, str]] = []

    for c in commits:
        zones = {classify(p) for p in c["paths"]}
        label, score = value_of(c["subject"])
        for z in zones:
            zone_counter[z] += 1
        value_counter[label] += 1
        for p in c["paths"]:
            path_counter[p] += 1
            # 上游改动落在"发行版自有（红线）"同名路径 → 直接搬运会覆盖自有资产
            if classify(p) == "own":
                redline_hits.append((c, p))
        # 候选评分：价值 - 红线惩罚（碰 own 区的 commit 基本不可直接搬）
        penalty = 5 if "own" in zones else 0
        candidates.append((score - penalty, c))

    candidates.sort(key=lambda x: (-x[0], x[1]["date"]))

    date_tag = datetime.now().strftime("%Y%m%d")
    out_path = os.path.join(REPORTS_DIR, f"upstream-watch-{date_tag}.md")
    lines: list[str] = []
    lines.append(f"# 上游雷达 · Hermes Agent（{datetime.now():%Y-%m-%d}）\n")
    lines.append("> 由 `scripts/upstream_watch.py watch` 生成。")
    lines.append("> **硬约束**：Vermes 与上游无共同祖先（`git merge-base` 为空，非 shallow），")
    lines.append("> `git merge` 不可用；本清单用于**能力级取长**，不是可自动合入的补丁队列。\n")
    lines.append("## 1. 概览\n")
    lines.append("| 项 | 值 |")
    lines.append("|---|---|")
    lines.append(f"| 基线 | `{since}` |")
    lines.append(f"| 上游 HEAD | `{head}` |")
    lines.append(f"| 扫描 commit 数 | {len(commits)}（上限 {args.max}） |")
    lines.append(f"| 涉及文件数 | {len(path_counter)} |")
    lines.append(f"| 红线命中（碰发行版自有资产） | **{len(redline_hits)}** |")
    lines.append("")
    lines.append("## 2. 分区分布\n")
    lines.append("| 分区 | commit 数 | 含义 |")
    lines.append("|---|---|---|")
    meaning = {
        "own": "发行版自有（红线）— 只参考思路，禁止直接搬运",
        "follow": "上游跟随区 — 优先跟随，Vermes 侧改动=契约税",
        "core": "同源核心（已 diverge）— 个案评估",
        "other": "其他（根文件/配置/构建）",
    }
    for z, n in zone_counter.most_common():
        lines.append(f"| `{z}` | {n} | {meaning.get(z, '')} |")
    lines.append("")
    lines.append("## 3. 价值分布\n")
    lines.append("| 类型 | commit 数 |")
    lines.append("|---|---|")
    for v, n in value_counter.most_common():
        lines.append(f"| {v} | {n} |")
    lines.append("")
    lines.append("## 4. 取长候选 Top 20（未触碰红线 / 价值优先）\n")
    shown = [(s, c) for s, c in candidates if s > 0][:20]
    if not shown:
        lines.append("_（无：候选全部触碰红线，或区间内无 fix/feat/perf）_")
    else:
        lines.append("| 分 | 日期 | hash | 主题 | 改动文件 |")
        lines.append("|---|---|---|---|---|")
        for s, c in shown:
            lines.append(
                f"| {s} | {c['date']} | `{c['hash']}` | {c['subject'][:70]} | {len(c['paths'])} |"
            )
    lines.append("")
    lines.append("## 5. 红线告警（上游改动落在发行版自有资产同名路径）\n")
    if not redline_hits:
        lines.append("_无。_")
    else:
        lines.append("| hash | 主题 | 命中红线路径 |")
        lines.append("|---|---|---|")
        for c, p in redline_hits[:40]:
            lines.append(f"| `{c['hash']}` | {c['subject'][:60]} | `{p}` |")
        if len(redline_hits) > 40:
            lines.append(f"\n_（仅列前 40 条，共 {len(redline_hits)} 条）_")
    lines.append("")
    lines.append("## 6. 高频改动文件 Top 30（漂移热点）\n")
    lines.append("| 文件 | 提交数 | 分区 |")
    lines.append("|---|---|---|")
    for p, n in path_counter.most_common(30):
        lines.append(f"| `{p}` | {n} | {classify(p)} |")
    lines.append("")
    lines.append("---\n")
    lines.append("## 处置纪律（见 docs/DISTRIBUTION_MANIFEST.md）\n")
    lines.append("1. 候选先过**红线闸门**：碰 `own` 区 = 禁止直接搬运，只参考实现思路")
    lines.append("2. 单个取长 = 一个 commit + 登记 ledger（价值 / 冲突面 / 验收 / 回退）")
    lines.append("3. 取长后跑 `python3 scripts/check_coexistence.py --deep` 与相关 pytest")
    lines.append("4. 本报告的基线写入 `reports/.upstream-baseline.json`，下次只看增量")

    os.makedirs(REPORTS_DIR, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")

    if not args.dry_run:
        save_baseline(head, since, len(commits))

    print(f"[watch] commits={len(commits)} files={len(path_counter)} redline={len(redline_hits)}")
    print(f"[watch] 报告 → {out_path}")
    if redline_hits:
        print(f"[warn] {len(redline_hits)} 处上游改动落在发行版自有资产路径，需人工判定")
    return 0


def cmd_boundary(args: argparse.Namespace) -> int:
    """边界闸门：Vermes 自身改动落在「上游跟随区」= 契约税。"""
    since = args.since or "v2.5.1"
    commits = collect_commits(f"{since}..main", args.max)
    if not commits:
        print(f"[boundary] {since}..main 无提交")
        return 0

    tax: list[tuple[dict, str]] = []
    zone_counter: Counter[str] = Counter()
    for c in commits:
        for p in c["paths"]:
            z = classify(p)
            zone_counter[z] += 1
            if z == "follow":
                tax.append((c, p))

    date_tag = datetime.now().strftime("%Y%m%d")
    out_path = os.path.join(REPORTS_DIR, f"dist-boundary-{date_tag}.md")
    lines = [
        f"# 发行版边界闸门（{datetime.now():%Y-%m-%d}）\n",
        f"> 区间 `{since}..main`（Vermes 侧 {len(commits)} commits）。",
        "> 判据：改动落在**上游跟随区**（`plugins/ tools/ harness/ cron/ .github/ docs/ scripts/`）"
        "= 契约税 —— 下次跟随上游时会冲突。要么登记（有意偏离），要么外置为插件。\n",
        "## 1. 分区分布\n",
        "| 分区 | 文件改动数 | 判定 |",
        "|---|---|---|",
    ]
    verdict = {
        "own": "✅ 发行版自有，正常",
        "follow": "⚠️ 契约税（需登记或外置）",
        "core": "🔍 核心 diverge，个案评估",
        "other": "—",
    }
    for z, n in zone_counter.most_common():
        lines.append(f"| `{z}` | {n} | {verdict.get(z, '')} |")
    lines.append("")
    lines.append("## 2. 契约税明细（跟随区改动）\n")
    if not tax:
        lines.append("_无。当前 Vermes 在跟随区零改动 —— 边界干净。_")
    else:
        lines.append("| hash | 主题 | 跟随区路径 |")
        lines.append("|---|---|---|")
        for c, p in tax[:60]:
            lines.append(f"| `{c['hash']}` | {c['subject'][:60]} | `{p}` |")
        if len(tax) > 60:
            lines.append(f"\n_（仅列前 60 条，共 {len(tax)} 条）_")
    lines.append("")
    lines.append("## 3. 闸门结论\n")
    if len(tax) == 0:
        lines.append("**PASS** —— 跟随区零改动，跟随上游无结构性摩擦。")
    elif len(tax) <= 10:
        lines.append(f"**WARN** —— {len(tax)} 处契约税，逐条登记到 DISTRIBUTION_MANIFEST ledger 即可。")
    else:
        lines.append(
            f"**FAIL** —— {len(tax)} 处契约税，需优先外置为插件，否则跟随上游的成本将持续累积。"
        )

    os.makedirs(REPORTS_DIR, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")

    print(f"[boundary] commits={len(commits)} 契约税={len(tax)}")
    print(f"[boundary] 报告 → {out_path}")
    return 0 if len(tax) == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Hermes 上游雷达 + 发行版边界闸门")
    sub = ap.add_subparsers(dest="cmd", required=True)

    w = sub.add_parser("watch", help="上游雷达：取长候选 + 红线告警")
    w.add_argument("--since", default=None, help="基线 ref（默认读基线文件，再退化为上游最新 tag）")
    w.add_argument("--max", type=int, default=2000, help="扫描 commit 上限")
    w.add_argument("--fetch", action="store_true", help="先 git fetch upstream")
    w.add_argument("--dry-run", action="store_true", help="不推进基线")
    w.set_defaults(func=cmd_watch)

    b = sub.add_parser("boundary", help="边界闸门：Vermes 跟随区契约税")
    b.add_argument("--since", default=None, help="基线 ref（默认 v2.5.1）")
    b.add_argument("--max", type=int, default=2000)
    b.set_defaults(func=cmd_boundary)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
