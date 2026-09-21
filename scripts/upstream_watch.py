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
    intake    意图级巡检：上游安全/正确性修复 → Vermes 对应物清单
              （只出清单，不自动改代码）

## 用法

    python3 scripts/upstream_watch.py watch              # 默认自上次基线
    python3 scripts/upstream_watch.py watch --since v2026.9.14 --max 400
    python3 scripts/upstream_watch.py watch --fetch      # 先 git fetch upstream
    python3 scripts/upstream_watch.py boundary              # 默认读冻结锚 FREEZE_REF
    python3 scripts/upstream_watch.py boundary --since 888bf8a344
    python3 scripts/upstream_watch.py intake               # 默认上游最新 tag → HEAD

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
MANIFEST_PATH = os.path.join(ROOT, "docs", "DISTRIBUTION_MANIFEST.md")

# 冻结锚：Vermes 侧“从这里开始算契约税”的不可移动 ref。发版 tag 可以动，这个不能动。
FREEZE_REF = "888bf8a344"

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
        "scripts/upstream_watch.py",  # 发行版雷达/闸门（Vermes 独有工具）
        "scripts/trigger-win-build.py",  # Windows 远程构建触发（Vermes 独有工具）
        "docs/vermes/",            # 外置的 Vermes 独有文档（外置迁移后进 own）
        "scripts/vermes/",         # 外置的 Vermes 独有脚本（外置迁移后进 own）
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

# ---------------------------------------------------------------------------
# 意图级巡检：上游安全/正确性修复 → Vermes 对应物映射
# ---------------------------------------------------------------------------

# 上游路径 → Vermes 对应物。值 None = 红线（只参考思路，默认不建议直接搬）。
# 值 str = Vermes 侧存在的对应路径（有则输出"有对应物"，无则输出"无"）。
UPSTREAM_VERMES_MAP: dict[str, str | None] = {
    "gateway/platforms/": None,            # 红线：gateway 平台层
    "agent/": None,                        # 红线：agent 核心
    "tools/env_passthrough.py": "tools/env_passthrough.py",
    "tools/environments/": "tools/environments/",
    "tools/approval.py": "tools/approval.py",
    "tools/skills_hub.py": "tools/skills_hub.py",
    "cron/scheduler.py": "cron/scheduler.py",
    "cron/lifecycle_guard.py": "cron/lifecycle_guard.py",
    "plugins/memory/": "plugins/memory/",
}

# 安全信号：主题/正文命中这些词 = 意图级候选（并集，不只看 fix(security)）
INTENT_SECURITY_RE = re.compile(
    r"GHSA-|CVE-|security|credential|secret|auth|approval|sandbox|injection|token|key leak|bypass",
    re.I,
)

# 纯文档/噪声，巡检直接排除
INTENT_SKIP_RE = re.compile(
    r"^(docs?|catalog|chore\(deps\)|test)[(:]|website|readme|typo|re-pin",
    re.I,
)


def _vermes_counterpart(upstream_path: str) -> tuple[str, str]:
    """返回 (vermes_path, 判定)。判定 ∈ {"有对应物", "无", "红线"}。

    目录前缀映射（如 tools/environments/）判定：把上游相对路径拼到 Vermes 根，
    检查对应文件是否存在（而非只判目录存在）。
    """
    for up_prefix, vm in UPSTREAM_VERMES_MAP.items():
        if not upstream_path.startswith(up_prefix):
            continue
        if vm is None:
            return upstream_path, "红线"
        if vm.endswith("/"):
            # 目录前缀映射：拼上游文件名
            rel = upstream_path[len(up_prefix):]
            vm_full = vm + rel
            return vm_full, ("有对应物" if os.path.exists(os.path.join(ROOT, vm_full)) else "无")
        # 文件级映射
        return vm, ("有对应物" if os.path.exists(os.path.join(ROOT, vm)) else "无")
    return upstream_path, "无"


def cmd_intake(args: argparse.Namespace) -> int:
    """意图级巡检：上游安全/正确性修复 → Vermes 有对应物/红线/无 清单。

    只出清单，不自动改代码。人月更：跑脚本 → 读清单 → 采纳则改代码+契约测试
    + TAKEALONG §7c → 拒绝也记一行。
    """
    up_repo = os.path.expanduser(args.upstream_repo)
    if not os.path.isdir(os.path.join(up_repo, ".git")):
        print(f"[error] 上游仓库不存在: {up_repo}", file=sys.stderr)
        return 2

    head = git_at(up_repo, "rev-parse", "--short", "HEAD").strip()
    since = args.since or git_at(up_repo, "describe", "--tags", "--abbrev=0", "HEAD").strip()
    if not since:
        print("[error] 无法确定上游基线，请用 --since 指定", file=sys.stderr)
        return 2

    print(f"[intake] 上游 {since} → {head}，上限 {args.max} commits")
    commits = collect_commits_from(up_repo, f"{since}..HEAD", args.max)
    if not commits:
        print("[intake] 无提交")
        return 0

    # 逐条判定
    rows: list[dict] = []
    n_ghsa = n_fixsec = n_security_semantic = n_counterpart = 0
    for c in commits:
        subject = c["subject"]
        if INTENT_SKIP_RE.search(subject):
            continue
        # 信号分类
        is_ghsa = bool(re.search(r"GHSA-", subject, re.I))
        is_fixsec = bool(re.search(r"^fix\(?security", subject, re.I))
        is_security_semantic = bool(INTENT_SECURITY_RE.search(subject))
        if not (is_ghsa or is_fixsec or is_security_semantic):
            continue
        n_ghsa += is_ghsa
        n_fixsec += is_fixsec
        if is_security_semantic:
            n_security_semantic += 1
        # 对应物判定（取该 commit 首个有映射的路径）
        vm_path, verdict = "", "无"
        for p in c["paths"]:
            vm_path, verdict = _vermes_counterpart(p)
            if verdict != "无":
                break
        if verdict == "有对应物":
            n_counterpart += 1
        rows.append({
            "hash": c["hash"],
            "date": c["date"],
            "subject": subject,
            "signal": ("GHSA" if is_ghsa else ("fix(security)" if is_fixsec else "安全语义")),
            "paths": c["paths"],
            "vermes": vm_path,
            "verdict": verdict,
        })

    # 排序：GHSA > fix(security) > 安全语义；同档按日期倒序
    rank = {"GHSA": 0, "fix(security)": 1, "安全语义": 2}
    rows.sort(key=lambda r: (rank[r["signal"]], r["date"]), reverse=False)

    date_tag = datetime.now().strftime("%Y%m%d")
    out_path = os.path.join(REPORTS_DIR, f"upstream-intent-{date_tag}.md")
    lines: list[str] = [
        f"# 上游意图级巡检 · 安全/正确性修复候选（{datetime.now():%Y-%m-%d}）\n",
        f"> 上游 `{since}` → `{head}`，扫描 {len(commits)} commits，命中候选 {len(rows)} 条。",
        "> 只出清单不自动改代码；人月更，采纳→改代码+契约测试+TAKEALONG §7c，拒绝也记一行。\n",
        "## 0. 校准摘要\n",
        "| 口径 | 数量 |",
        "|---|---|",
        f"| 显式 GHSA | {n_ghsa} |",
        f"| fix(security) 标签 | {n_fixsec} |",
        f"| 安全语义候选 | {n_security_semantic} |",
        f"| 有 Vermes 对应物 | {n_counterpart} |",
        "",
        "## 1. 候选清单\n",
        "| 信号 | hash | 日期 | 主题 | Vermes 对应物 | 建议 |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        sug = {"有对应物": "移植/评估", "红线": "红线只读", "无": "拒绝/无需"}[r["verdict"]]
        lines.append(
            f"| {r['signal']} | `{r['hash']}` | {r['date']} | {r['subject'][:70]} | "
            f"{r['vermes'] or '—'} | {sug} |"
        )
    lines.append("")
    lines.append("## 2. 处置纪律\n")
    lines.append("1. 采纳：改代码 + 契约测试 + TAKEALONG_LEDGER §7c 登记（含来源 commit/落点/验收/人时）。")
    lines.append("2. 拒绝：也在 §7c 记一行（防重复考古）。")
    lines.append("3. 红线：只输出思路参考，默认不直接搬。")

    os.makedirs(REPORTS_DIR, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"[intake] 候选 {len(rows)} 条（GHSA {n_ghsa} / fixsec {n_fixsec} / 安全语义 {n_security_semantic} / 有对应物 {n_counterpart}）")
    print(f"[intake] 报告 → {out_path}")
    return 0


def git(*args: str) -> str:
    """跑 git 命令；失败返回空串（本脚本是只读雷达，不因单点失败中断）。"""
    return git_at(ROOT, *args)


def git_at(repo: str, *args: str) -> str:
    """在指定仓库跑 git 命令；失败返回空串。"""
    try:
        out = subprocess.run(
            ["git", *args], cwd=repo, capture_output=True, text=True, check=True
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


def parse_diversion_ledger() -> tuple[set[str], set[str]]:
    """从 DISTRIBUTION_MANIFEST.md 解析 DIVERSION_LEDGER，返回 (exact_files, dir_prefixes)。

    账本用固定 HTML 注释包裹（<!--DIVERSION_LEDGER:START--> 至 END），
    每行是 markdown 表格行，第二列是登记路径（逗号分隔可列多个）。

    语义（精确匹配，禁止放水）：
    - 文件条目（带扩展名，如 tools/env_passthrough.py）→ 进 exact_files，仅精确匹配该文件
    - 目录条目（以 / 结尾，如 docs/vermes/）→ 进 dir_prefixes，才豁免其子路径
    - 文件条目**不**升格为父目录免税（防整个 tools/ docs/ 被放水）
    """
    exact_files: set[str] = set()
    dir_prefixes: set[str] = set()
    if not os.path.exists(MANIFEST_PATH):
        return exact_files, dir_prefixes
    text = open(MANIFEST_PATH, encoding="utf-8").read()
    m = re.search(r"<!--DIVERSION_LEDGER:START-->\s*(.*?)<!--DIVERSION_LEDGER:END-->", text, re.S)
    if not m:
        return exact_files, dir_prefixes
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 2 or cells[0] in ("id", "---", "---"):
            continue
        cell = cells[1]
        # 逗号/顿号分隔多个路径；剥反引号与括号注释（全角+半角）
        raw_paths = [p for p in re.split(r"[,、]", cell) if p.strip()]
        for raw in raw_paths:
            path = raw.strip().strip("`").strip()
            # 去掉括号注释，如 `tools/env_passthrough.py`（+ ...）或 (deprecated)
            path = re.split(r"[（(]", path)[0].strip()
            if not path:
                continue
            if path.endswith("/"):
                # 显式目录条目 → 前缀豁免
                dir_prefixes.add(path)
            elif os.path.basename(path) and "." in os.path.basename(path):
                # 文件条目 → 精确匹配
                exact_files.add(path)
            else:
                # 无扩展名且无斜杠结尾 → 按目录前缀处理（如 "docs/vermes"）
                dir_prefixes.add(path.rstrip("/") + "/")
    return exact_files, dir_prefixes


def is_registered_diversion(path: str, ledger: tuple[set[str], set[str]]) -> bool:
    """判断一条 follow 区路径是否已被 DIVERSION_LEDGER 登记（算作有意偏离，不税）。

    ledger = (exact_files, dir_prefixes)。精确文件匹配，目录前缀才豁免子路径。
    """
    exact_files, dir_prefixes = ledger
    if path in exact_files:
        return True
    for p in dir_prefixes:
        if path.startswith(p):
            return True
    return False


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
    return collect_commits_from(ROOT, ref_range, max_commits)


def collect_commits_from(repo: str, ref_range: str, max_commits: int) -> list[dict]:
    """同上，但可在指定仓库（如上游 ~/.hermes/hermes-agent）跑。"""
    sep = "\x1f"
    fmt = f"%H{sep}%ad{sep}%s"
    raw = git_at(
        repo,
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
    """边界闸门：Vermes 自身改动落在「上游跟随区」= 契约税（未登记才算）。"""
    # 冻结锚：默认读 FREEZE_REF（不可移动），不跟发版 tag（tag 会前移导致窗口塌缩）
    since = args.since or FREEZE_REF
    commits = collect_commits(f"{since}..main", args.max)
    if not commits:
        print(f"[boundary] {since}..main 无提交")
        return 0

    ledger = parse_diversion_ledger()
    unregistered_tax: list[tuple[dict, str]] = []
    registered_diversion: list[tuple[dict, str]] = []
    zone_counter: Counter[str] = Counter()
    for c in commits:
        for p in c["paths"]:
            z = classify(p)
            zone_counter[z] += 1
            if z == "follow":
                if is_registered_diversion(p, ledger):
                    registered_diversion.append((c, p))
                else:
                    unregistered_tax.append((c, p))

    date_tag = datetime.now().strftime("%Y%m%d")
    out_path = os.path.join(REPORTS_DIR, f"dist-boundary-{date_tag}.md")
    lines = [
        f"# 发行版边界闸门（{datetime.now():%Y-%m-%d}）\n",
        f"> 区间 `{since}..main`（Vermes 侧 {len(commits)} commits）。",
        f"> 冻结锚 `{FREEZE_REF}`（非发版 tag）。",
        "> 判据：改动落在**上游跟随区**（`plugins/ tools/ harness/ cron/ .github/ docs/ scripts/`）",
        "> 且 **两账都未登记** = 契约税。已登记（DIVERSION_LEDGER）= 有意偏离，单列不税。\n",
        "## 1. 分区分布\n",
        "| 分区 | 文件改动数 | 判定 |",
        "|---|---|---|",
    ]
    verdict = {
        "own": "✅ 发行版自有，正常",
        "follow": "⚠️ 契约税（未登记）",
        "core": "🔍 核心 diverge，个案评估",
        "other": "—",
    }
    for z, n in zone_counter.most_common():
        lines.append(f"| `{z}` | {n} | {verdict.get(z, '')} |")
    lines.append("")

    lines.append("## 2. 未登记契约税明细（follow 区改动 && 两账未登记）\n")
    if not unregistered_tax:
        lines.append("_无。当前 Vermes 在跟随区零未登记改动 —— 边界干净。_")
    else:
        lines.append("| hash | 主题 | 跟随区路径 |")
        lines.append("|---|---|---|")
        for c, p in unregistered_tax[:60]:
            lines.append(f"| `{c['hash']}` | {c['subject'][:60]} | `{p}` |")
        if len(unregistered_tax) > 60:
            lines.append(f"\n_（仅列前 60 条，共 {len(unregistered_tax)} 条）_")
    lines.append("")

    lines.append("## 3. 已登记偏离（DIVERSION_LEDGER，不算税）\n")
    if not registered_diversion:
        lines.append("_无。_")
    else:
        lines.append("| hash | 主题 | 路径 |")
        lines.append("|---|---|---|")
        for c, p in registered_diversion[:60]:
            lines.append(f"| `{c['hash']}` | {c['subject'][:60]} | `{p}` |")
    lines.append("")

    lines.append("## 4. 闸门结论\n")
    if len(unregistered_tax) == 0:
        lines.append("**PASS** —— 零未登记契约税。已登记偏离单列，不阻碍跟随。")
    else:
        lines.append(
            f"**WARN/FAIL** —— {len(unregistered_tax)} 处未登记契约税。"
            "逐条登记到 DISTRIBUTION_MANIFEST.md §7b（DIVERSION_LEDGER）或 §7c（TAKEALONG_LEDGER），"
            "或外置为插件。"
        )

    os.makedirs(REPORTS_DIR, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")

    print(f"[boundary] commits={len(commits)} 未登记税={len(unregistered_tax)} 已登记偏离={len(registered_diversion)}")
    print(f"[boundary] 报告 → {out_path}")
    return 0 if len(unregistered_tax) == 0 else 1


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
    b.add_argument("--since", default=None, help=f"基线 ref（默认冻结锚 {FREEZE_REF}，不跟发版 tag）")
    b.add_argument("--max", type=int, default=2000)
    b.set_defaults(func=cmd_boundary)

    i = sub.add_parser("intake", help="意图级巡检：上游安全/正确性修复 → Vermes 对应物清单")
    i.add_argument("--since", default=None, help="上游基线 ref（默认上游最新 tag）")
    i.add_argument("--max", type=int, default=2000)
    i.add_argument("--upstream-repo", default="~/.hermes/hermes-agent", help="上游仓库路径（默认 ~/.hermes/hermes-agent）")
    i.set_defaults(func=cmd_intake)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
