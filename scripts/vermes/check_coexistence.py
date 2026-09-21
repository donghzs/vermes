#!/usr/bin/env python3
"""并存不变量验收脚本 — 同一台 Mac 上多个引擎安装是否真隔离。

目的：把「Hermes 官方 / Vermes / 其他引擎能在本机并存」从"感觉"变成"每次可验"。
任何一次改造（尤其是发行版化：引擎层跟随上游）跑一遍即可知道并存有没有被弄坏。

只读：ps / lsof / launchctl / 路径探测 / 本机自身端口 TCP 连通性（--deep）。
绝不发消息、不写文件、不 kill 任何进程。

用法：
    python3 scripts/check_coexistence.py             # 默认：不变量检查
    python3 scripts/check_coexistence.py --deep      # 额外做网关端口连通性探测
    python3 scripts/check_coexistence.py --json      # 机读输出（CI 用）
    python3 scripts/check_coexistence.py --quiet     # 只输出 FAIL/WARN

退出码：0 = 无 FAIL；1 = 存在 FAIL（并存已被破坏）

不变量清单见 docs/SPEC_coexistence_invariants_20260920.md
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
from pathlib import Path

HOME = Path.home()

# ---------------------------------------------------------------- 安装登记表
# markers: 进程 args 中出现任一即认为属于该安装（大小写不敏感）
# config_root: 配置根（不同安装必须不同）
# runtime_roots: 运行时会写入的目录（跨安装句柄探测用）
INSTALLS: list[dict] = [
    {
        "key": "hermes",
        "label": "Hermes 官方",
        "markers": [".hermes/", "Hermes.app", "hermes-agent", "ai.hermes"],
        "config_root": HOME / ".hermes",
        "code_roots": [HOME / ".hermes" / "hermes-agent"],
        "launcher": "hermes",
        "gateway_hint": "launchd label ai.hermes.gateway",
    },
    {
        "key": "vermes",
        "label": "Vermes",
        "markers": [".vermes/", "/Applications/Vermes.app", "Projects/vermes-electron/", "/backend/vermes"],
        "config_root": HOME / ".vermes",
        "code_roots": [
            HOME / "Projects" / "vermes-electron",
            Path("/Applications/Vermes.app/Contents/Resources/backend"),
        ],
        "launcher": "vermes",
        "gateway_hint": "dashboard :9119 / gateway :9120",
    },
    {
        "key": "qclaw",
        "label": "QClaw",
        "markers": [".qclaw/", "QClaw.app", "/QClaw/python/"],
        "config_root": HOME / ".qclaw",
        "code_roots": [Path("/Applications/QClaw.app")],
        "launcher": "qclaw",
        "gateway_hint": "Electron app",
    },
    {
        "key": "workbuddy",
        "label": "WorkBuddy",
        "markers": [".workbuddy/", "WorkBuddy.app"],
        "config_root": HOME / ".workbuddy",
        "code_roots": [Path("/Applications/WorkBuddy.app")],
        "launcher": "workbuddy",
        "gateway_hint": "Electron app",
    },
    {
        "key": "mimo",
        "label": "Xiaomi MiMo",
        "markers": ["Xiaomi MiMo.app", "Xiaomi MiMo"],
        "config_root": HOME / ".mimo",
        "code_roots": [Path("/Applications/Xiaomi MiMo.app")],
        "launcher": "mimo",
        "gateway_hint": "Electron app",
    },
]

# 进程 args 里出现这些就不算「引擎后端」（Electron 渲染/GPU 助手、crashpad、构建工具等）
NOISE = re.compile(
    r"Helper|crashpad|GPU|Renderer|\.vite|esbuild|lsp/bin|mcp_crawl4ai|node_modules|--type=",
    re.IGNORECASE,
)

results: list[dict] = []


def add(check: str, status: str, msg: str, evidence: list[str] | None = None) -> None:
    """status: PASS / WARN / FAIL / SKIP"""
    results.append({"check": check, "status": status, "msg": msg, "evidence": evidence or []})


def run(cmd: list[str], timeout: int = 20) -> str:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""


# ------------------------------------------------------------------ 探测层
def processes() -> list[dict]:
    out = run(["ps", "-axo", "pid=,ppid=,args="])
    exe_out = run(["ps", "-axo", "pid=,comm="])
    exe_by_pid: dict[int, str] = {}
    for line in exe_out.splitlines():
        m = re.match(r"\s*(\d+)\s+(.*)", line)
        if m:
            exe_by_pid[int(m.group(1))] = m.group(2).strip()
    procs = []
    for line in out.splitlines():
        m = re.match(r"\s*(\d+)\s+(\d+)\s+(.*)", line)
        if not m:
            continue
        pid, ppid, args = int(m.group(1)), int(m.group(2)), m.group(3).strip()
        procs.append({"pid": pid, "ppid": ppid, "args": args, "exe": exe_by_pid.get(pid, "")})
    return procs


def classify(proc: dict) -> list[str]:
    """返回该进程归属的安装 key 列表（可能为空或冲突多个）。"""
    low = proc["args"].lower()
    return [i["key"] for i in INSTALLS if any(mk.lower() in low for mk in i["markers"])]


def install_of(key: str) -> dict:
    return next(i for i in INSTALLS if i["key"] == key)


def listening_ports() -> list[dict]:
    out = run(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN"])
    rows = []
    for line in out.splitlines()[1:]:
        f = line.split()
        if len(f) < 9:
            continue
        name = f[-2] if f[-1].startswith("(") else f[-1]
        m = re.search(r":(\d+)$", name)
        if not m:
            continue
        rows.append({"command": f[0], "pid": int(f[1]), "port": int(m.group(1)), "name": name})
    return rows


def open_paths(pids: list[int], timeout: int = 25) -> list[str]:
    if not pids:
        return []
    out = run(["lsof", "-p", ",".join(str(p) for p in pids), "-Fn"], timeout=timeout)
    return [ln[1:] for ln in out.splitlines() if ln.startswith("n") and ln[1:].startswith("/")]


def launchd_labels() -> list[dict]:
    out = run(["launchctl", "list"])
    rows = []
    for line in out.splitlines()[1:]:
        f = line.split("\t")
        if len(f) == 3:
            rows.append({"pid": f[0].strip(), "status": f[1].strip(), "label": f[2].strip()})
    return rows


# ------------------------------------------------------------------ 检查层
def _first_script(rest: str) -> str | None:
    """在 args 余下部分里找第一个真实存在的绝对路径文件（跳过 --flag 与不存在的路径）。"""
    for tok in rest.split():
        if tok.startswith("/") and "--" not in tok:
            try:
                if Path(tok.rstrip(",")).is_file():
                    return tok
            except OSError:
                continue
    return None


def split_cross_host(proc: dict) -> tuple[str | None, str | None, str | None, str | None]:
    """用 ps 的 comm（可执行文件全路径，含空格也完整）+ args 余下部分判定跨安装托管。

    返回 (exe, exe_owner, script_owner, script)。
    exe_owner 与 script_owner 都存在且不同 = 跨安装托管（A 的解释器在跑 B 的脚本）。
    """
    exe = proc.get("exe") or None
    if exe and not exe.startswith("/"):
        exe = None
    rest = proc["args"]
    if exe and exe in rest:
        rest = rest.split(exe, 1)[1]
    script = _first_script(rest)
    exe_owner = classify({"pid": proc["pid"], "ppid": 0, "args": exe}) if exe else None
    script_owner = classify({"pid": proc["pid"], "ppid": 0, "args": script}) if script else None
    return exe, (exe_owner[0] if exe_owner else None), (script_owner[0] if script_owner else None), script


def check_process_ownership(procs: list[dict]) -> dict[str, dict]:
    """按安装分组运行中进程；跨安装托管与真混淆分开报。"""
    grouped: dict[str, dict] = {i["key"]: {"pids": [], "procs": []} for i in INSTALLS}
    ambiguous, hosted = [], []
    for p in procs:
        keys = classify(p)
        if not keys:
            continue
        exe, exe_owner, script_owner, script = split_cross_host(p)
        if exe_owner and script_owner and exe_owner != script_owner:
            hosted.append(
                f"pid {p['pid']}: {install_of(exe_owner)['label']} 的解释器 "
                f"({exe}) 在跑 {install_of(script_owner)['label']} 的脚本 ({script})"
            )
        elif len(keys) > 1:
            ambiguous.append(f"pid {p['pid']} 同时命中 {keys}: {p['args'][:120]}")
        for k in keys:
            grouped[k]["pids"].append(p["pid"])
            group = "backend" if not NOISE.search(p["args"]) else "helper"
            grouped[k]["procs"].append({**p, "kind": group})

    live = [i["key"] for i in INSTALLS if grouped[i["key"]]["pids"]]
    lines = []
    for i in INSTALLS:
        g = grouped[i["key"]]
        if not g["pids"]:
            continue
        backends = [p for p in g["procs"] if p["kind"] == "backend"]
        lines.append(f"{i['label']}: {len(g['pids'])} 进程（后端 {len(backends)}）")
    status = "FAIL" if ambiguous else ("WARN" if hosted else "PASS")
    msg = f"运行中安装 {len(live)} 个：{', '.join(live) or '无'}"
    if ambiguous:
        msg += f"；{len(ambiguous)} 个进程归属混乱"
    if hosted:
        msg += f"；{len(hosted)} 处跨安装托管（见 ★ 项）"
    add("1 进程归属可判定", status, msg, lines or None)
    if ambiguous:
        add("★ 进程归属无混淆", "FAIL", f"{len(ambiguous)} 个进程同时命中多个安装", ambiguous)
    add("★ 跨安装二进制托管", "WARN" if hosted else "PASS",
        "无进程使用其他安装的解释器/二进制" if not hosted
        else f"{len(hosted)} 处：某安装的进程依赖另一安装的解释器（宿主升级/删除会连带故障）",
        hosted)
    return grouped


def check_config_roots() -> None:
    roots = [(i["key"], i["label"], i["config_root"]) for i in INSTALLS if i["config_root"].exists()]
    dupes = [f"{k} 与 {l} 共用 {r}" for k, l, r in roots for k2, l2, r2 in roots
             if k < k2 and r.resolve() == r2.resolve()]
    nested = [f"{l} 的配置根 {r} 位于 {o['label']} 之下" for _, l, r in roots for o in INSTALLS
              if o["config_root"] != r and o["config_root"] in r.parents]
    ev = [f"{l}: {r}" for _, l, r in roots]
    if dupes or nested:
        add("2 配置根隔离", "FAIL", "配置根存在共用或嵌套", ev + dupes + nested)
    else:
        add("2 配置根隔离", "PASS", f"{len(roots)} 个配置根互不共用、互不嵌套", ev)


def check_state_dbs() -> None:
    """每个安装的 .db 不得出现在另一个安装的配置根下。"""
    all_dbs: list[tuple[str, Path]] = []
    for i in INSTALLS:
        if not i["config_root"].exists():
            continue
        for db in sorted(i["config_root"].glob("*.db")):
            all_dbs.append((i["key"], db))
    cross = []
    for key, db in all_dbs:
        for other in INSTALLS:
            if other["key"] != key and other["config_root"] in db.parents:
                cross.append(f"{db} 属 {key} 却位于 {other['label']} 配置根")
    by_install = {}
    for k, db in all_dbs:
        by_install.setdefault(k, []).append(db.name)
    ev = [f"{install_of(k)['label']}: {len(v)} 个 db（{', '.join(v[:4])}{'…' if len(v) > 4 else ''}）"
          for k, v in by_install.items()]
    add("3 状态库隔离", "FAIL" if cross else "PASS",
        "各安装的 db 均在自己的配置根内" if not cross else "存在跨安装 db", ev + cross)


def check_ports(port_rows: list[dict], grouped: dict[str, dict]) -> None:
    """端口两两不重叠；并列出每个端口归属哪个安装。"""
    def owner(pid: int) -> str:
        for i in INSTALLS:
            if pid in grouped[i["key"]]["pids"]:
                return i["label"]
        return f"外部({next((p['args'][:40] for p in procs_cache if p['pid'] == pid), '?')})"

    seen: dict[int, str] = {}
    dup = []
    lines = []
    for r in sorted(port_rows, key=lambda x: x["port"]):
        who = owner(r["pid"])
        lines.append(f":{r['port']} ← {who}")
        if r["port"] in seen and seen[r["port"]] != who:
            dup.append(f":{r['port']} 被 {seen[r['port']]} 与 {who} 同时占用")
        seen[r["port"]] = who
    add("4 端口隔离", "FAIL" if dup else "PASS",
        f"{len(port_rows)} 个监听端口，归属可判定且无冲突" if not dup else "端口冲突", lines + dup)


def check_launchd(labels: list[dict]) -> None:
    ours = [l for l in labels if re.search(r"hermes|vermes|qclaw|workbuddy|mimo", l["label"], re.I)]
    by_prefix: dict[str, list[str]] = {}
    for l in ours:
        prefix = l["label"].split(".")[0] if "." in l["label"] else l["label"]
        by_prefix.setdefault(prefix, []).append(l["label"])
    clash = []
    # Hermes 官方 label 命名空间（ai.hermes.*）不得被其他安装占用
    hermes_ns = [l["label"] for l in ours if l["label"].startswith("ai.hermes")]
    vermes_ns = [l["label"] for l in ours if "vermes" in l["label"].lower()]
    overlap = set(hermes_ns) & set(vermes_ns)
    if overlap:
        clash.append(f"命名空间重叠: {sorted(overlap)}")
    ev = [f"{l['label']} (pid={l['pid'] or '未运行'}, last_exit={l['status']})" for l in ours]
    add("5 launchd 命名空间隔离", "FAIL" if clash else "PASS",
        f"{len(ours)} 个相关服务，命名空间不重叠", ev + clash)


def check_interpreters(grouped: dict[str, dict]) -> list[str]:
    """每个引擎进程的解释器/二进制应属于它自己的安装树；跨安装使用记 WARN。"""
    cross = []
    ev = []
    for i in INSTALLS:
        backends = [p for p in grouped[i["key"]]["procs"] if p["kind"] == "backend"]
        for p in backends:
            exe = p.get("exe") or (p["args"].split()[0] if p["args"] else "")
            if not exe.startswith("/"):
                continue
            owner = classify({"pid": p["pid"], "ppid": 0, "args": exe})
            ev.append(f"{i['label']} pid {p['pid']}: {exe}")
            if owner and i["key"] not in owner:
                cross.append(f"{i['label']} 的进程用的是 {owner} 的二进制: {exe} (pid {p['pid']})")
    add("6 解释器/二进制隔离", "WARN" if cross else "PASS",
        "各引擎用自己的解释器与二进制" if not cross else f"{len(cross)} 处跨安装使用", ev + cross)
    return cross


def check_launchers() -> None:
    import shutil
    ev, bad = [], []
    for i in INSTALLS:
        path = shutil.which(i["launcher"])
        if not path:
            ev.append(f"{i['label']}: 未找到 {i['launcher']} 入口（可能未提供 CLI）")
            continue
        ev.append(f"{i['label']}: {i['launcher']} → {path}")
    # Hermes 与 Vermes 的 CLI 入口不得指向同一个文件
    h, v = shutil.which("hermes"), shutil.which("vermes")
    if h and v and Path(h).resolve() == Path(v).resolve():
        bad.append(f"hermes 与 vermes 入口指向同一文件: {h}")
    add("7 CLI 入口隔离", "FAIL" if bad else "PASS",
        "各安装的 CLI 入口独立" if not bad else "入口冲突", ev + bad)


def check_skills_roots() -> None:
    rows, nested = [], []
    for i in INSTALLS:
        sk = i["config_root"] / "skills"
        if not sk.exists():
            continue
        n = sum(1 for _ in sk.rglob("SKILL.md"))
        rows.append(f"{i['label']}: {sk}（{n} 个 SKILL.md）")
        for o in INSTALLS:
            if o["key"] != i["key"] and o["config_root"] in sk.parents:
                nested.append(f"{sk} 位于 {o['label']} 之下")
    add("8 技能根隔离", "FAIL" if nested else "PASS",
        f"{len(rows)} 个技能根互不嵌套", rows + nested)


def check_code_roots() -> None:
    """代码根必须互不包含：尤其 Vermes 的引擎不得落在 ~/.hermes 内。"""
    h = HOME / ".hermes" / "hermes-agent"
    v_repo = HOME / "Projects" / "vermes-electron"
    v_app = Path("/Applications/Vermes.app/Contents/Resources/backend")
    bad, ev = [], []
    for name, p in (("Hermes 引擎", h), ("Vermes 源码", v_repo), ("Vermes 装机后端", v_app)):
        if p.exists():
            ev.append(f"{name}: {p}")
        else:
            ev.append(f"{name}: {p}（不存在）")
    if v_app.exists() and str(v_app).startswith(str(h)):
        bad.append("Vermes 装机后端位于 Hermes 引擎树内")
    if v_repo.exists() and str(v_repo).startswith(str(h)):
        bad.append("Vermes 源码位于 Hermes 引擎树内")
    if v_app.exists() and v_repo.exists() and str(v_repo) in str(v_app):
        bad.append("Vermes 装机后端指向源码树（装机遇源码改动而变）")
    add("9 代码根隔离", "FAIL" if bad else "PASS",
        "三方代码根互相独立" if not bad else "代码根交叉", ev + bad)


def check_cross_handles(grouped: dict[str, dict]) -> None:
    """核心检查：A 安装的进程不得持有 B 安装目录下的文件句柄。"""
    cross, ev = [], []
    for i in INSTALLS:
        if not i["config_root"].exists():
            continue
        backends = [p["pid"] for p in grouped[i["key"]]["procs"] if p["kind"] == "backend"]
        # Electron 主进程也查，但限制数量避免 lsof 过慢
        helpers = [p["pid"] for p in grouped[i["key"]]["procs"] if p["kind"] == "helper"][:2]
        pids = backends + helpers
        if not pids:
            continue
        paths = open_paths(pids)
        ev.append(f"{i['label']}: 检查 {len(pids)} 进程 / {len(paths)} 个文件句柄")
        for other in INSTALLS:
            if other["key"] == i["key"]:
                continue
            needle = str(other["config_root"])
            hits = [p for p in paths if p.startswith(needle)]
            for hit in hits[:5]:
                cross.append(f"{i['label']} 持有 {other['label']} 的文件: {hit}")
    add("★ 跨安装文件句柄", "FAIL" if cross else "PASS",
        "无跨安装文件句柄（真隔离）" if not cross else f"{len(cross)} 处跨安装写入/读取", ev + cross)


def check_deep(grouped: dict[str, dict], port_rows: list[dict]) -> None:
    """可选：对本机自身网关端口做一次 TCP 连通性探测（不发送任何业务数据）。"""
    targets = {"Hermes gateway": None, "Vermes dashboard": 9119, "Vermes gateway": 9120}
    ev, bad = [], []
    for label, port in targets.items():
        if port is None:
            ev.append(f"{label}: 端口由 launchd 管理，跳过（见不变量 5）")
            continue
        if not any(r["port"] == port for r in port_rows):
            ev.append(f"{label}: :{port} 未监听")
            bad.append(f"{label} :{port} 未监听")
            continue
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=3):
                ev.append(f"{label}: :{port} 可连接 ✓")
        except OSError as exc:
            ev.append(f"{label}: :{port} 连接失败 {exc}")
            bad.append(f"{label} :{port} 不可连接")
    add("★ 端口连通性（--deep）", "WARN" if bad else "PASS",
        "本机网关端口均可连接" if not bad else f"{len(bad)} 个端口异常", ev)


# ------------------------------------------------------------------ 主流程
procs_cache: list[dict] = []


def main() -> int:
    ap = argparse.ArgumentParser(description="并存不变量验收")
    ap.add_argument("--deep", action="store_true", help="额外做网关端口连通性探测")
    ap.add_argument("--json", action="store_true", help="机读输出")
    ap.add_argument("--quiet", action="store_true", help="只输出 FAIL/WARN")
    args = ap.parse_args()

    global procs_cache
    procs_cache = processes()
    port_rows = listening_ports()
    grouped = check_process_ownership(procs_cache)
    check_config_roots()
    check_state_dbs()
    check_ports(port_rows, grouped)
    check_launchd(launchd_labels())
    check_interpreters(grouped)
    check_launchers()
    check_skills_roots()
    check_code_roots()
    check_cross_handles(grouped)
    if args.deep:
        check_deep(grouped, port_rows)

    n_fail = sum(1 for r in results if r["status"] == "FAIL")
    n_warn = sum(1 for r in results if r["status"] == "WARN")

    if args.json:
        print(json.dumps({
            "summary": {"fail": n_fail, "warn": n_warn, "total": len(results)},
            "checks": results,
        }, ensure_ascii=False, indent=2))
        return 1 if n_fail else 0

    icon = {"PASS": "✅", "WARN": "⚠️ ", "FAIL": "❌", "SKIP": "➖"}
    print("=" * 78)
    print("并存不变量验收 — 只读探测，未改动任何进程/文件")
    print("=" * 78)
    for r in results:
        if args.quiet and r["status"] == "PASS":
            continue
        print(f"{icon.get(r['status'], '?')} [{r['check']}] {r['msg']}")
        for e in r["evidence"]:
            print(f"      · {e}")
    print("-" * 78)
    verdict = "❌ 并存已被破坏" if n_fail else ("⚠️  通过但有告警" if n_warn else "✅ 并存完好")
    print(f"结果：{verdict}（FAIL {n_fail} / WARN {n_warn} / 共 {len(results)} 项）")
    if not args.deep:
        print("提示：加 --deep 可顺带探测本机网关端口连通性；真实消息往返需人工（见 SPEC 附录）。")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
