#!/usr/bin/env python3
"""scripts/upstream_canary.py — 上游哨兵（pinned canary）。

为什么要有这个东西
------------------
Vermes 与 upstream **没有共同祖先**（`git merge-base` 为空且非 shallow），
所以取长只能是能力级搬运。搬运的隐含假设是：

    「我们搬过来的那些上游文件，在上游没有发生语义漂移。」

上游近 30 天约 1.4 万提交（当场 `rev-list --count --since=30.days` 测为准），
这个假设每周都可能失效。本脚本把它变成**每周可判定**的一件事。

设计约束（roadmap §8.4，不做自我发挥）
-------------------------------------
1. **必须 pinned**：判据是固定上游 tag（默认 `v2026.9.14`），**禁止**用 HEAD /
   分支名当基线。移动 ref 会导致「每周红的原因不可复现、无法归因」。
   季度升钉 = 单独 PR 改 `PIN_PATH` 文件并写明对比区间。
2. **只告警不阻塞**发版：红因多在上游漂移，不该卡住 Vermes 自己的发布。
   因此 CI 侧用 `continue-on-error` + 开 issue，不进 required checks。
3. 内容：`intake` + `boundary` + 契约测 + `check_coexistence.py --deep`。

退出码
------
    0  无 FAIL（可含 WARN）
    1  至少一个 FAIL
    2  用法/配置错误（pin 不可解析、仓库不存在等）

用法
----
    python3 scripts/upstream_canary.py                    # 全跑
    python3 scripts/upstream_canary.py --no-deep          # 不做端口连通性探测
    python3 scripts/upstream_canary.py --skip-tests       # 不跑契约测（快）
    python3 scripts/upstream_canary.py --strict           # WARN 也判 FAIL
    python3 scripts/upstream_canary.py --print-json       # 机器可读输出

产物
----
    reports/canary/<UTC>.md       人读报告
    reports/.canary-state.json    机器状态（CI 读取 / issue body）
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORTS_DIR = ROOT / "reports"
CANARY_DIR = REPORTS_DIR / "canary"
PIN_PATH = REPORTS_DIR / ".upstream-canary-pin.json"
STATE_PATH = REPORTS_DIR / ".canary-state.json"


def default_python() -> str:
    """契约测/子命令的默认解释器：**venv 绑定**，不跟调用方的 sys.executable。

    曾踩坑：用 `python3 scripts/upstream_canary.py` 时 sys.executable 是系统 Python
    （本机 Homebrew 3.14），而项目 venv 是 3.11——契约测可能假红/假绿。
    项目标准解释器是 `.venv/bin/python`（见 docs/HANDOFF_TO_MIMO 等），存在即优先。
    """
    venv_py = ROOT / ".venv" / "bin" / "python"
    if venv_py.exists():
        return str(venv_py)
    return sys.executable

# §8.4：默认钉 `v2026.9.14`（roadmap §8.4 原文）。季度升钉 = 改 PIN_PATH 文件。
DEFAULT_PIN_TAG = "v2026.9.14"
DEFAULT_UPSTREAM_REPO = "~/.hermes/hermes-agent"

# 移动 ref —— 这些当 pin 会被拒绝（违反 §8.4「禁止移动 ref」）。
MOVING_REFS = {"HEAD", "main", "master", "develop", "trunk", "upstream/main"}

# boundary 契约税阈值：roadmap §8.3 停止条件 hard-2「增量 ≤10」。
DEFAULT_TAX_THRESHOLD = 10

# 默认契约测（存在性在运行时校验，缺文件会 WARN 而不是静默跳过 —— 不虚标覆盖）。
DEFAULT_TESTS = [
    "tests/tools/test_distribution_mechanism.py",
    "tests/tools/test_env_passthrough.py",
    "tests/tools/test_approval_lock_commit.py",
    "tests/tools/test_profile_gate_env_isolation.py",
    "tests/tools/test_approval_unattended_allowlist.py",
    "tests/tools/test_cron_session_contextvar.py",
    "tests/tools/test_session_id_contextvar.py",
    "tests/tools/test_upstream_canary.py",
]

# --------------------------------------------------------------------------
# 工具
# --------------------------------------------------------------------------
def _sha256_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def is_moving_ref(ref: str) -> bool:
    """该 ref 是否为移动引用（分支/HEAD），pinned canary 禁止使用。"""
    if ref in MOVING_REFS:
        return True
    # remote-tracking 分支名也算移动 ref
    return bool(re.match(r"^(origin|upstream)/[A-Za-z0-9._-]+$", ref)) and "/" in ref


def load_pin(path: Path = PIN_PATH) -> dict:
    """读 pin 文件；不存在则回落到 DEFAULT_PIN_TAG（不视为错误）。"""
    if not path.exists():
        return {
            "upstream_tag": DEFAULT_PIN_TAG,
            "source": "default(no pin file)",
            "note": "首次运行；写入 reports/.upstream-canary-pin.json 固定钉点后可季度升钉",
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"[canary] pin 文件不可解析: {path} ({exc})")
    for key in ("upstream_tag",):
        if not data.get(key):
            raise SystemExit(f"[canary] pin 文件缺 {key}: {path}")
    data.setdefault("source", str(path))
    return data


def _run(cmd: list[str], cwd: Path | None = None, timeout: int = 900) -> tuple[int, str, str]:
    proc = subprocess.run(
        cmd, cwd=str(cwd or ROOT), capture_output=True, text=True, timeout=timeout
    )
    return proc.returncode, proc.stdout, proc.stderr


def _git(repo: Path, *args: str) -> tuple[int, str]:
    rc, out, err = _run(["git", *args], cwd=repo, timeout=120)
    return rc, (out or err).strip()


# --------------------------------------------------------------------------
# Step 1: pin
# --------------------------------------------------------------------------
def step_pin(pin: dict, repo: Path, strict: bool) -> dict:
    tag = pin["upstream_tag"]
    if is_moving_ref(tag):
        return {
            "name": "pin",
            "status": "FAIL",
            "detail": f"pin 是移动 ref ({tag})，违反 roadmap §8.4",
            "evidence": {"pin": tag},
        }
    if not repo.is_dir():
        return {
            "name": "pin",
            "status": "WARN" if not strict else "FAIL",
            "detail": f"上游仓不存在: {repo}（无法校验钉点，仅记录）",
            "evidence": {"pin": tag, "repo": str(repo)},
        }
    rc, out = _git(repo, "rev-parse", "--short", f"{tag}^{{commit}}")
    if rc != 0 or not out:
        return {
            "name": "pin",
            "status": "FAIL",
            "detail": f"钉点不可解析: {tag}",
            "evidence": {"pin": tag, "git": out},
        }
    rc2, describe = _git(repo, "describe", "--tags", "--abbrev=0", tag)
    return {
        "name": "pin",
        "status": "PASS",
        "detail": f"钉点 {tag} → {out}",
        "evidence": {
            "pin": tag,
            "commit": out,
            "describe": describe if rc2 == 0 else "-",
            "source": pin.get("source", "-"),
        },
    }


# --------------------------------------------------------------------------
# Step 2: intake（意图级巡检 → 待人工裁决清单）
# --------------------------------------------------------------------------
def step_intake(tag: str, repo: Path, py: str, max_commits: int, strict: bool) -> dict:
    script = ROOT / "scripts" / "upstream_watch.py"
    cmd = [
        py, str(script), "intake", "--since", tag,
        "--max", str(max_commits), "--upstream-repo", str(repo),
    ]
    rc, out, err = _run(cmd, timeout=1800)
    text = out + err
    if rc != 0 and "unused" not in text:
        return {
            "name": "intake",
            "status": "FAIL",
            "detail": f"intake 退出码 {rc}",
            "evidence": {"tail": text[-600:]},
        }
    ghsa = len(re.findall(r"GHSA-", text, re.I))
    fixsec = len(re.findall(r"^fix\(?security", text, re.M | re.I))
    counterpart = len(re.findall(r"有对应物", text))
    n_commits = 0
    m = re.search(r"(\d+)\s+commits", text)
    if m:
        n_commits = int(m.group(1))
    pending = counterpart  # 有对应物 = 需要人读的
    if pending:
        return {
            "name": "intake",
            "status": "WARN",
            "detail": f"{n_commits} commits；Vermes 有对应物 {pending} 条待人工裁决",
            "evidence": {"commits": n_commits, "ghsa": ghsa, "fixsec": fixsec, "counterpart": counterpart},
        }
    return {
        "name": "intake",
        "status": "PASS",
        "detail": f"{n_commits} commits；无待裁决对应物",
        "evidence": {"commits": n_commits, "ghsa": ghsa, "fixsec": fixsec, "counterpart": 0},
    }


# --------------------------------------------------------------------------
# Step 3: boundary（边界闸门 / 契约税）
# --------------------------------------------------------------------------
def step_boundary(py: str, since: str | None, threshold: int, max_commits: int) -> dict:
    script = ROOT / "scripts" / "upstream_watch.py"
    cmd = [py, str(script), "boundary", "--max", str(max_commits)]
    if since:
        cmd += ["--since", since]
    rc, out, err = _run(cmd, timeout=1800)
    text = out + err
    # 格式（实测）：`[boundary] commits=159 未登记税=45 已登记偏离=47`
    # 曾踩坑：按中文单词 "契约税" 去找，脚本实际输出的是 "未登记税=" → 恒解析不到
    # → 闸门结果永远读不出来。这里以脚本真实输出为准。
    m = re.search(r"未登记税\s*=\s*(\d+)", text)
    m_reg = re.search(r"已登记偏离\s*=\s*(\d+)", text)
    registered = int(m_reg.group(1)) if m_reg else None
    tax = int(m.group(1)) if m else None
    if m is None:
        status = "WARN"
        detail = "未能从输出解析契约税数值（脚本输出格式可能已变）"
    elif tax > threshold:
        status = "FAIL"
        detail = f"契约税 {tax} > 阈值 {threshold}"
    elif tax > 0:
        status = "WARN"
        detail = f"契约税 {tax}（阈值 {threshold}，在容差内但非零）"
    else:
        status = "PASS"
        detail = "契约税 0"
    return {
        "name": "boundary",
        "status": status,
        "detail": detail,
        "evidence": {"tax": tax, "registered": registered, "threshold": threshold, "rc": rc, "tail": text[-400:]},
    }


# --------------------------------------------------------------------------
# Step 4: 契约测
# --------------------------------------------------------------------------
def step_tests(py: str, tests: list[str], tmpdir: Path | None) -> dict:
    existing = [t for t in tests if (ROOT / t).exists()]
    missing = [t for t in tests if t not in existing]
    if not existing:
        return {
            "name": "contract-tests",
            "status": "FAIL",
            "detail": f"契约测全部缺失: {missing}",
            "evidence": {"missing": missing},
        }
    cmd = [
        py, "-m", "pytest", *existing,
        "-p", "no:xdist", "-o", "addopts=", "-q",
    ]
    if tmpdir:
        cmd += ["--basetemp", str(tmpdir)]
    rc, out, err = _run(cmd, timeout=2400)
    text = out + err
    m = re.search(r"(\d+) passed", text)
    passed = int(m.group(1)) if m else 0
    m2 = re.search(r"(\d+) failed", text)
    failed = int(m2.group(1)) if m2 else 0
    if rc != 0 or failed:
        status = "FAIL"
        detail = f"{passed} passed / {failed} failed"
    else:
        status = "PASS"
        detail = f"{passed} passed"
    result = {
        "name": "contract-tests",
        "status": status,
        "detail": detail,
        "evidence": {"passed": passed, "failed": failed, "files": len(existing)},
    }
    if missing:
        result["evidence"]["missing"] = missing
        if status == "PASS":
            result["status"] = "WARN"
            result["detail"] += f"（另有 {len(missing)} 个契约测文件缺失，未纳入覆盖）"
    return result


# --------------------------------------------------------------------------
# Step 5: coexistence（--deep）
# --------------------------------------------------------------------------
def step_coexistence(py: str, deep: bool) -> dict:
    script = ROOT / "scripts" / "vermes" / "check_coexistence.py"
    if not script.exists():
        return {
            "name": "coexistence",
            "status": "WARN",
            "detail": f"不存在: {script}",
            "evidence": {},
        }
    cmd = [py, str(script)]
    if deep:
        cmd.append("--deep")
    rc, out, err = _run(cmd, timeout=600)
    text = out + err

    # 汇总行优先：`结果：…（FAIL n / WARN m / 共 k 项）`。
    # 曾踩坑：直接在全文 `count("FAIL")` 会把汇总行里的字面量 "FAIL 0" 算成一次
    # 失败 → 全绿也判红（永久假红）。故必须先认汇总行，只把它当唯一判据。
    m_sum = re.search(r"FAIL\s+(\d+)\s*/\s*WARN\s+(\d+)\s*/\s*共\s*(\d+)\s*项", text)
    if m_sum:
        n_fail, n_warn = int(m_sum.group(1)), int(m_sum.group(2))
        n_items = int(m_sum.group(3))
        n_pass = max(n_items - n_fail - n_warn, 0)
    else:
        # 兜底：只数表格里的状态单元格（`│ FAIL │` / `| FAIL |`），不数正文。
        def _cells(status: str) -> int:
            return len(re.findall(r"[│|]\s*" + status + r"\s*[│|]", text))

        n_fail, n_warn, n_pass = _cells("FAIL"), _cells("WARN"), _cells("PASS")
        n_items = n_fail + n_warn + n_pass

    if n_fail:
        status, detail = "FAIL", f"{n_fail} FAIL / {n_warn} WARN / 共 {n_items} 项"
    elif n_warn:
        status, detail = "WARN", f"{n_warn} WARN / {n_pass} PASS / 共 {n_items} 项"
    else:
        status, detail = "PASS", f"{n_pass} PASS / 共 {n_items} 项"
    return {
        "name": "coexistence",
        "status": status,
        "detail": detail + ("" if deep else "（未 --deep）"),
        "evidence": {"pass": n_pass, "warn": n_warn, "fail": n_fail, "items": n_items, "deep": deep},
    }


# --------------------------------------------------------------------------
# Step 6: A/B 哨兵（roadmap §8.2 指标 5 的最短落地 — Hermes 2026-09-23）
# 语料哨兵 → 可执行断言；变差即红。未映射 = WARN（禁止静默当成 PASS）。
# --------------------------------------------------------------------------
AB_SENTINEL_TESTS: dict[str, list[str]] = {
    # cache 前缀：两轮 stable sha 必须一致
    "A08": ["tests/tools/test_s2_gold.py::test_stable_sha_stable_across_two_builds"],
    "B07": ["tests/tools/test_s2_gold.py::test_stable_sha_stable_across_two_builds"],
    # 审批：危险命令进审批而非直接执行
    "C02": [
        "tests/tools/test_approval.py::TestDetectDangerousRm::test_rm_rf_detected",
        "tests/tools/test_approval.py::TestDetectDangerousRm::test_rm_recursive_long_flag",
    ],
    # write-deny：~/.vermes/auth.json / 密钥库写入被拒（语料 C03 原文）
    "C03": [
        "tests/agent/test_file_safety_secret_stores.py",
        "tests/tools/test_file_write_safety.py::TestStaticDenyList::test_ssh_key_is_denied",
        "tests/tools/test_file_write_safety.py::TestStaticDenyList::test_etc_shadow_is_denied",
    ],
    # session 不串味（L-007/L-010）
    "C04": [
        "tests/tools/test_cron_session_contextvar.py::test_concurrent_threads_do_not_contaminate",
        "tests/tools/test_approval.py::TestSessionKeyContext::test_context_session_key_overrides_process_env",
    ],
    # cron 不被误判为交互挂起（unattended）
    "C05": ["tests/tools/test_cron_session_contextvar.py"],
    # 渠道目标 / 断线重连 — 暂无确定性单测，先显式 WARN
    "D02": [],
    "D03": [],
}


def step_ab_sentinels(py: str, tmpdir: Path | None) -> dict:
    """A/B 语料哨兵 → 可执行断言（指标 5 停止条件）。

    **按哨兵分跑**，保证红因归因正确（不连坐）。
    """
    rows = []
    failed_ids: list[str] = []
    unmapped: list[str] = []
    total_passed = total_failed = 0
    for sid in sorted(AB_SENTINEL_TESTS):
        nodes = AB_SENTINEL_TESTS[sid]
        if not nodes:
            unmapped.append(sid)
            rows.append((sid, "WARN", "未映射确定性断言"))
            continue
        missing = [n for n in nodes if not (ROOT / n.split("::")[0]).exists()]
        if missing:
            rows.append((sid, "WARN", f"测试文件缺失: {missing}"))
            unmapped.append(sid)
            continue
        label = "+".join(n.split("::")[-1] for n in nodes)
        cmd = [py, "-m", "pytest", *nodes, "-p", "no:xdist", "-o", "addopts=", "-q"]
        if tmpdir:
            cmd += ["--basetemp", str(tmpdir)]
        rc, out, err = _run(cmd, timeout=600)
        text = out + err
        m = re.search(r"(\d+) passed", text)
        passed = int(m.group(1)) if m else 0
        m2 = re.search(r"(\d+) failed", text)
        failed = int(m2.group(1)) if m2 else 0
        total_passed += passed
        total_failed += failed
        if rc != 0 or failed:
            rows.append((sid, "FAIL", f"{label} → {passed} passed / {failed} failed"))
            failed_ids.append(sid)
        else:
            rows.append((sid, "PASS", f"{label} ({passed} passed)"))

    n_fail = sum(1 for _s, st, _d in rows if st == "FAIL")
    n_warn = sum(1 for _s, st, _d in rows if st == "WARN")
    n_pass = sum(1 for _s, st, _d in rows if st == "PASS")
    if n_fail:
        status, detail = "FAIL", f"{n_fail} 哨兵红 / {n_pass} 绿 / {n_warn} 未映射"
    elif n_warn:
        status, detail = "WARN", f"{n_pass} 绿 / {n_warn} 未映射（D02/D03 等）"
    else:
        status, detail = "PASS", f"{n_pass} 哨兵全绿"
    return {
        "name": "ab-sentinels",
        "status": status,
        "detail": detail,
        "evidence": {
            "rows": [{"id": s, "status": st, "detail": d} for s, st, d in rows],
            "passed": total_passed,
            "failed": total_failed,
            "unmapped": unmapped,
            "failed_ids": failed_ids,
        },
    }


# --------------------------------------------------------------------------
# 报告
# --------------------------------------------------------------------------
def render_report(steps: list[dict], meta: dict) -> str:
    lines = [
        "# 上游哨兵 canary 报告",
        "",
        f"- 时间（UTC）: {meta['ts']}",
        f"- Vermes HEAD: `{meta['vermes_head']}`",
        f"- 上游钉点: `{meta['pin_tag']}` → `{meta['pin_commit']}`（{meta['pin_source']}）",
        f"- 上游 HEAD（仅参考，**不作判据**）: `{meta['upstream_head']}`",
        f"- 结论: **{meta['verdict']}**",
        f"- 纪律: pinned tag 固定，季度升钉走单独 PR；**只告警不阻塞发版**（roadmap §8.4）",
        "",
        "| step | status | detail |",
        "|---|---|---|",
    ]
    for s in steps:
        lines.append(f"| {s['name']} | {s['status']} | {s['detail']} |")
    lines.append("")
    lines.append("## 明细证据")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(steps, ensure_ascii=False, indent=2)[:8000])
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="上游哨兵 pinned canary（roadmap §8.4）")
    ap.add_argument("--pin", default=None, help=f"覆盖钉点 tag（默认读 {PIN_PATH.name}，再回落 {DEFAULT_PIN_TAG}）")
    ap.add_argument("--upstream-repo", default=DEFAULT_UPSTREAM_REPO, help="本地上游仓路径")
    ap.add_argument(
        "--python",
        default=default_python(),
        help="跑子命令的解释器（默认：项目 .venv/bin/python，否则 sys.executable）",
    )
    ap.add_argument("--max-intake", type=int, default=2000, help="intake 扫描上限")
    ap.add_argument("--max-boundary", type=int, default=2000, help="boundary 扫描上限")
    ap.add_argument("--boundary-since", default=None, help="boundary 基线（默认脚本内置冻结锚）")
    ap.add_argument("--tax-threshold", type=int, default=DEFAULT_TAX_THRESHOLD, help="契约税阈值")
    ap.add_argument("--test", action="append", default=None, help="追加/覆盖契约测文件（可多次）")
    ap.add_argument("--no-deep", action="store_true", help="check_coexistence 不加 --deep")
    ap.add_argument("--skip-tests", action="store_true", help="跳过契约测")
    ap.add_argument("--skip-ab", action="store_true", help="跳过 A/B 哨兵（step 6）")
    ap.add_argument("--skip-intake", action="store_true", help="跳过 intake")
    ap.add_argument("--skip-boundary", action="store_true", help="跳过 boundary")
    ap.add_argument("--strict", action="store_true", help="WARN 也判 FAIL")
    ap.add_argument("--print-json", action="store_true", help="额外输出机器可读 JSON")
    ap.add_argument("--basetemp", default=None, help="pytest --basetemp（沙箱环境可能需要）")
    args = ap.parse_args()

    repo = Path(os.path.expanduser(args.upstream_repo))
    pin = load_pin()
    if args.pin:
        pin = dict(pin, upstream_tag=args.pin, source="--pin override")

    ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d-%H%M%SZ")
    rc_v, head = _git(ROOT, "rev-parse", "--short", "HEAD")

    steps: list[dict] = [step_pin(pin, repo, args.strict)]
    if not args.skip_intake:
        steps.append(step_intake(pin["upstream_tag"], repo, args.python, args.max_intake, args.strict))
    if not args.skip_boundary:
        steps.append(step_boundary(args.python, args.boundary_since, args.tax_threshold, args.max_boundary))
    tmpdir = Path(args.basetemp) if args.basetemp else None
    if not args.skip_tests:
        tests = args.test or DEFAULT_TESTS
        steps.append(step_tests(args.python, tests, tmpdir))
    steps.append(step_coexistence(args.python, deep=not args.no_deep))
    if not args.skip_ab:
        steps.append(step_ab_sentinels(args.python, tmpdir))

    if args.strict:
        for s in steps:
            if s["status"] == "WARN":
                s["status"] = "FAIL"
                s["detail"] += "（--strict：WARN 升级为 FAIL）"

    n_fail = sum(1 for s in steps if s["status"] == "FAIL")
    n_warn = sum(1 for s in steps if s["status"] == "WARN")
    verdict = "FAIL" if n_fail else ("WARN" if n_warn else "PASS")

    pin_step = steps[0]
    meta = {
        "ts": ts,
        "vermes_head": head if rc_v == 0 else "?",
        "pin_tag": pin["upstream_tag"],
        "pin_commit": pin_step["evidence"].get("commit", "未解析"),
        "pin_source": pin.get("source", "-"),
        "upstream_head": _git(repo, "rev-parse", "--short", "HEAD")[1] if repo.is_dir() else "仓库缺失",
        "verdict": verdict,
    }

    CANARY_DIR.mkdir(parents=True, exist_ok=True)
    rpt = CANARY_DIR / f"{ts}.md"
    rpt.write_text(render_report(steps, meta), encoding="utf-8")

    state = {
        "ts": ts,
        "verdict": verdict,
        "n_fail": n_fail,
        "n_warn": n_warn,
        "steps": [{k: s[k] for k in ("name", "status", "detail")} for s in steps],
        "meta": meta,
        "report": str(rpt.relative_to(ROOT)),
    }
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[canary] 结论 {verdict}（FAIL={n_fail} WARN={n_warn}）")
    for s in steps:
        print(f"  - {s['status']:<4} {s['name']}: {s['detail']}")
    print(f"[canary] 报告: {rpt.relative_to(ROOT)}")
    if args.print_json:
        print(json.dumps(state, ensure_ascii=False, indent=2))
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
