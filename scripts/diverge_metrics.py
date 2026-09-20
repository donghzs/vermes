#!/usr/bin/env python3
"""Vermes vs 上游 Hermes 分歧度量（P0-A · A4）。

来源：~/.hermes/skills/hermes-vermes-architecture/scripts/diverge_metrics.py
（Hermes skill 指针真源；本文件为**仓库内可复现副本**，路径默认指向本机上游检出。）

用法：
    python3 scripts/diverge_metrics.py [upstream_root] [vermes_root]
    默认：upstream = ~/.hermes/hermes-agent，vermes = 本仓库

产出三组基线（验收 A4）：
    1) 核心工具差集（toolsets.py *_CORE_TOOLS）
    2) 同源文件行集合 Jaccard
    3) 静默失败信号（树内计数 + 近 90 天提交补丁新增计数）

口径纪律（E3）：工具面只用 toolsets.py 核心列表，禁止用 tool_guardrails 注册名。
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_UP = Path.home() / ".hermes" / "hermes-agent"

CORE_VARS = ("_HERMES_CORE_TOOLS", "_vermes_CORE_TOOLS", "_VERMES_CORE_TOOLS", "_CORE_TOOLS")
SAME_ORIGIN = (
    "toolsets.py",
    "model_tools.py",
    "utils.py",
    "agent/context_compressor.py",
)
SKIP = ("/node_modules/", "/__pycache__/", "/.git/", "/venv/", "/.venv/", "/site-packages/", "/dist/")

SILENT_PATTERNS = (
    re.compile(r"\|\|\s*true\b"),
    re.compile(r"except\s*:\s*pass\b"),
    re.compile(r"except\s+Exception\s*(?:as\s+\w+\s*)?:\s*pass\b"),
    re.compile(r"except\s+Exception\s*(?:as\s+\w+\s*)?:\s*continue\b"),
)


def core_tools(root: Path) -> list[str] | None:
    path = root / "toolsets.py"
    if not path.is_file():
        return None
    text = path.read_text(errors="replace")
    for var in CORE_VARS:
        m = re.search(rf"{var}\s*=\s*\[(.*?)\]", text, re.S)
        if m:
            return re.findall(r'"([^"]+)"', m.group(1))
    m = re.search(r"([A-Z_]*CORE_TOOLS)\s*=\s*\[(.*?)\]", text, re.S)
    return re.findall(r'"([^"]+)"', m.group(2)) if m else None


def line_set(path: Path) -> set[str]:
    return {ln.strip() for ln in path.read_text(errors="replace").splitlines() if ln.strip()}


def jaccard(a: set[str], b: set[str]) -> float | None:
    return len(a & b) / len(a | b) if a and b else None


def walk_files(root: Path, suffixes: tuple[str, ...] = (".py",), cap: int = 300000):
    stack = [root]
    n = 0
    while stack and n < cap:
        cur = stack.pop()
        try:
            entries = list(cur.iterdir())
        except OSError:
            continue
        for p in entries:
            s = str(p)
            if any(k in s for k in SKIP):
                continue
            if p.is_dir():
                stack.append(p)
            elif p.suffix in suffixes:
                n += 1
                yield p


def walk_count(root: Path, name_glob: str, cap: int = 200000) -> int:
    hits = 0
    stack = [root]
    while stack and hits < cap:
        cur = stack.pop()
        try:
            entries = list(cur.iterdir())
        except OSError:
            continue
        for p in entries:
            s = str(p)
            if any(k in s for k in SKIP):
                continue
            if p.is_dir():
                stack.append(p)
            elif p.match(name_glob):
                hits += 1
    return hits


def count_silent_in_tree(root: Path) -> dict[str, int]:
    counts = {p.pattern: 0 for p in SILENT_PATTERNS}
    files_hit = 0
    for path in walk_files(root):
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        hit = False
        for pat in SILENT_PATTERNS:
            found = pat.findall(text)
            if found:
                counts[pat.pattern] += len(found)
                hit = True
        if hit:
            files_hit += 1
    counts["_files_hit"] = files_hit
    return counts


def count_silent_in_recent_commits(repo: Path, days: int = 90, max_commits: int = 400) -> dict[str, int]:
    """统计近 N 天提交补丁中**新增**的静默失败模式（只数 + 行）。"""
    try:
        raw = subprocess.check_output(
            [
                "git", "-C", str(repo), "log",
                f"--since={days}.days", f"--max-count={max_commits}",
                "-p", "--unified=0", "--no-color",
            ],
            text=True, errors="replace",
            stderr=subprocess.DEVNULL,
        )
    except Exception as exc:
        return {"error": 1, "detail": str(exc)[:80], "adds": 0, "commits_touched": 0}

    adds = 0
    commits_touched: set[str] = set()
    current_commit = None
    for line in raw.splitlines():
        if line.startswith("commit "):
            current_commit = line.split(" ", 1)[1].strip()
            continue
        if line.startswith("+") and not line.startswith("+++"):
            body = line[1:]
            if any(p.search(body) for p in SILENT_PATTERNS):
                adds += 1
                if current_commit:
                    commits_touched.add(current_commit)
    return {"adds": adds, "commits_touched": len(commits_touched), "days": days, "scanned_commits_cap": max_commits}


def main() -> int:
    up = Path(sys.argv[1]).expanduser() if len(sys.argv) > 1 else DEFAULT_UP
    vm = Path(sys.argv[2]).expanduser() if len(sys.argv) > 2 else REPO_ROOT
    for label, root in (("上游", up), ("Vermes", vm)):
        if not root.is_dir():
            print(f"✗ {label} 根目录不存在: {root}")
            return 2

    print(f"upstream={up}")
    print(f"vermes={vm}")
    print("=" * 72)
    print("1) 核心工具面（口径：toolsets.py 的 *_CORE_TOOLS）")
    uh, vh = core_tools(up), core_tools(vm)
    if not uh or not vh:
        print("   ✗ 解析失败：确认两侧都有 toolsets.py")
    else:
        print(f"   上游 {len(uh)} 个 / Vermes {len(vh)} 个")
        only_u, only_v = sorted(set(uh) - set(vh)), sorted(set(vh) - set(uh))
        print(f"   仅上游有 ({len(only_u)}): {only_u}")
        print(f"   仅 Vermes 有 ({len(only_v)}): {only_v}")
        for prefix in ("browser", "kanban", "memory", "skill"):
            a = [t for t in uh if t.startswith(prefix)]
            b = [t for t in vh if t.startswith(prefix)]
            if a or b:
                print(f"   {prefix}_*: 上游 {len(a)} / Vermes {len(b)}")

    print("=" * 72)
    print("2) 同源文件行集合 Jaccard（≤0.15 = 深度分歧）")
    for rel in SAME_ORIGIN:
        a, b = up / rel, vm / rel
        if not (a.is_file() and b.is_file()):
            # 上游可能把 toolsets 放在 agent/ 下
            alt = Path("agent") / rel
            if (up / alt).is_file() or (vm / alt).is_file():
                a, b = up / alt, vm / alt
                rel = str(alt)
            if not (a.is_file() and b.is_file()):
                print(f"   {rel}: 缺一侧，跳过")
                continue
        j = jaccard(line_set(a), line_set(b))
        na = len(a.read_text(errors="replace").splitlines())
        nb = len(b.read_text(errors="replace").splitlines())
        print(f"   {rel}: 上游 {na} 行 / Vermes {nb} 行 | Jaccard {j:.2f}")

    print("=" * 72)
    print("3) 工程资产")
    for root_label, root in (("上游", up), ("Vermes", vm)):
        tests = walk_count(root / "tests", "test_*.py") if (root / "tests").is_dir() else 0
        wf_dir = root / ".github" / "workflows"
        wf = len([p for p in wf_dir.iterdir() if p.suffix in (".yml", ".yaml")]) if wf_dir.is_dir() else 0
        agents = walk_count(root, "AGENTS.md")
        print(f"   {root_label}: tests {tests} / workflows {wf} / AGENTS.md {agents}")

    print("=" * 72)
    print("4) 静默失败信号（A4 补充口径；模式: || true / except: pass / except Exception: pass|continue）")
    for root_label, root in (("上游", up), ("Vermes", vm)):
        tree = count_silent_in_tree(root / "agent" if (root / "agent").is_dir() else root)
        # agent/ + gateway/ + tools/ + vermes_cli/ 更贴近运行时
        runtime_hits = 0
        for sub in ("agent", "gateway", "tools", "vermes_cli", "cron"):
            d = root / sub
            if d.is_dir():
                t = count_silent_in_tree(d)
                runtime_hits += sum(v for k, v in t.items() if k != "_files_hit")
        print(f"   {root_label} agent-tree 模式命中≈{sum(v for k,v in tree.items() if k!='_files_hit')} files={tree.get('_files_hit')}")
        print(f"   {root_label} runtime(agent/gateway/tools/vermes_cli/cron) 模式命中≈{runtime_hits}")
    silent_commits = count_silent_in_recent_commits(vm, days=90)
    print(f"   Vermes 近 90 天补丁新增静默模式行数={silent_commits.get('adds')} "
          f"涉及提交≈{silent_commits.get('commits_touched')} detail={silent_commits}")
    print("=" * 72)
    print("提示：报数时写明口径；AGENTS.md 个数是制度层代理指标，")
    print("      真正的差距要看是否写了不可违反的 invariants。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
