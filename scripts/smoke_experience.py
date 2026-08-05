#!/usr/bin/env python3
"""可感知体验冒烟清单 —— 对着**运行中的后端**跑，抓用户真的会撞上的回退。

为什么需要它
------------
单元测试和 eval 抓不到"体验回退"。真实教训（2026-08-05）：冻结包里 edge_tts
被误判未安装，于是在聊天请求路径上同步跑 pip install，阻塞 asyncio 事件循环，
/health 在看门狗 2s 窗口内失联，Electron 主进程把后端 SIGTERM 掉，前端表现为
"Failed to fetch" + "重连中 (n/2)"。整套测试全绿，因为没有任何一条测试
"发一条真消息，同时确认 /health 还在答"。

这个脚本就是补那一刀：**只测用户能直接感知的东西**，并且用真流量测。

用法
----
    python scripts/smoke_experience.py                     # 默认 127.0.0.1:9119
    python scripts/smoke_experience.py --base http://127.0.0.1:9129
    python scripts/smoke_experience.py --skip-chat         # 不花 token，只跑只读项

退出码 0 = 全过；1 = 有 FAIL。WARN 不阻断，但要看一眼。
建议作为任何改动落地前的 P0 门槛。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

# 看门狗的判死阈值。/health 探测延迟一旦逼近这个数，后端就有被误杀的风险，
# 所以这里把它当成硬红线，而不是"慢一点而已"。见 electron/main.js
# BACKEND_PROBE_TIMEOUT_MS。
WATCHDOG_PROBE_TIMEOUT_MS = 5000

AGENT_LOG = Path.home() / ".vermes" / "logs" / "agent.log"

# 桌面 token 护栏：多数 /api/* 端点要求这个头，否则 401。后端把它落在这个文件里。
SESSION_TOKEN_FILE = Path.home() / ".vermes" / ".session_token"
SESSION_HEADER = "X-Vermes-Session-Token"


def _session_token() -> str:
    try:
        return SESSION_TOKEN_FILE.read_text().strip()
    except Exception:
        return ""

# 请求路径上绝不该出现的日志特征。每一条都对应一个真实发生过的故障。
FORBIDDEN_LOG_PATTERNS = {
    "Lazy-installing": "请求路径上触发了懒安装（会同步阻塞事件循环）",
    "收到 SIGTERM": "后端被外部杀掉（看门狗误杀）",
    "SIGTERM": "后端被外部杀掉（看门狗误杀）",
}

_results: list[tuple[str, str, str, str]] = []  # (code, status, name, detail)


def record(code: str, status: str, name: str, detail: str = "") -> None:
    _results.append((code, status, name, detail))
    icon = {"PASS": "✅", "FAIL": "❌", "WARN": "⚠️ ", "SKIP": "－"}[status]
    print(f"  {icon} {code}  {name}" + (f"\n         └ {detail}" if detail else ""))


def _req(base: str, path: str, *, method: str = "GET", body: dict | None = None,
         timeout: float = 15.0):
    """裸 urllib，刻意不用 requests：这个脚本要能在任何环境跑，包括冻结包。"""
    url = base.rstrip("/") + path
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    tok = _session_token()
    if tok:
        headers[SESSION_HEADER] = tok
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    # 桌面环境常有 HTTP_PROXY 指向本地抓包端口，会把 127.0.0.1 的请求也劫走。
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    t0 = time.monotonic()
    try:
        with opener.open(req, timeout=timeout) as r:
            payload = r.read()
            return r.status, payload, (time.monotonic() - t0) * 1000
    except urllib.error.HTTPError as e:
        return e.code, e.read(), (time.monotonic() - t0) * 1000
    except Exception as e:
        return 0, str(e).encode(), (time.monotonic() - t0) * 1000


def _json(payload: bytes):
    try:
        return json.loads(payload)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 只读项：后端起来之后，用户打开界面第一眼会看到的东西
# ---------------------------------------------------------------------------

def check_health(base: str) -> bool:
    code, payload, ms = _req(base, "/health", timeout=10)
    d = _json(payload) or {}
    if code != 200:
        record("C01", "FAIL", "后端存活 /health", f"http={code}")
        return False
    if ms > WATCHDOG_PROBE_TIMEOUT_MS:
        record("C01", "FAIL", "后端存活 /health",
               f"延迟 {ms:.0f}ms 超过看门狗阈值 {WATCHDOG_PROBE_TIMEOUT_MS}ms")
        return False
    integ = (d.get("integrity") or {}).get("state_db")
    extra = f"{ms:.0f}ms, v{d.get('version')}, state_db={integ}"
    if integ not in (None, "ok"):
        record("C01", "WARN", "后端存活 /health", extra)
    else:
        record("C01", "PASS", "后端存活 /health", extra)
    return True


def check_models(base: str) -> None:
    """models.dev 不可达时前端全挂过一次，这里守住"至少能列出模型"。"""
    code, payload, ms = _req(base, "/api/chat/models", timeout=20)
    d = _json(payload)
    if code != 200:
        record("C02", "FAIL", "模型列表可用", f"http={code}")
        return
    n = len(d) if isinstance(d, list) else len(d.get("models", d.get("data", []) or []))
    if n == 0:
        record("C02", "FAIL", "模型列表可用", "返回 0 个模型 → 前端选不了模型")
    else:
        record("C02", "PASS", "模型列表可用", f"{n} 个, {ms:.0f}ms")


def check_sessions(base: str) -> None:
    code, payload, ms = _req(base, "/api/sessions?limit=5")
    if code != 200:
        record("C03", "FAIL", "历史会话可读", f"http={code}")
        return
    d = _json(payload)
    n = len(d) if isinstance(d, list) else len((d or {}).get("sessions", []))
    record("C03", "PASS", "历史会话可读", f"{n} 条, {ms:.0f}ms")


def check_memory(base: str) -> None:
    code, payload, ms = _req(base, "/api/memory/status")
    if code != 200:
        record("C04", "FAIL", "记忆系统在线", f"http={code}")
        return
    d = _json(payload) or {}
    record("C04", "PASS", "记忆系统在线", f"{json.dumps(d, ensure_ascii=False)[:110]}")


def check_flags(base: str) -> None:
    """flag 误报刷屏是可感知体验的头号噪音源，超过阈值就该有人看。"""
    code, payload, ms = _req(base, "/api/flags")
    if code != 200:
        record("C05", "FAIL", "反思 flag 未刷屏", f"http={code}")
        return
    d = _json(payload)
    items = d if isinstance(d, list) else (d or {}).get("flags", [])
    n = len(items)
    if n > 20:
        record("C05", "WARN", "反思 flag 未刷屏", f"{n} 条待处理 flag，用户会被弹窗淹没")
    else:
        record("C05", "PASS", "反思 flag 未刷屏", f"{n} 条 open")


def check_tools(base: str) -> None:
    code, payload, ms = _req(base, "/api/tools/toolsets")
    if code != 200:
        record("C06", "FAIL", "工具集可枚举", f"http={code}")
        return
    d = _json(payload)
    n = len(d) if isinstance(d, (list, dict)) else 0
    if n == 0:
        record("C06", "FAIL", "工具集可枚举", "0 个工具集 → agent 无工具可用")
    else:
        record("C06", "PASS", "工具集可枚举", f"{n} 组")


def check_skills(base: str) -> None:
    code, payload, _ = _req(base, "/api/skills")
    if code != 200:
        record("C07", "WARN", "技能列表可读", f"http={code}")
        return
    d = _json(payload)
    n = len(d) if isinstance(d, list) else len((d or {}).get("skills", []))
    record("C07", "PASS", "技能列表可读", f"{n} 个")


def check_usage(base: str) -> None:
    code, payload, _ = _req(base, "/api/analytics/usage")
    if code != 200:
        record("C08", "WARN", "用量/成本可见", f"http={code}")
        return
    record("C08", "PASS", "用量/成本可见", f"{(payload or b'')[:90].decode('utf-8', 'replace')}")


# ---------------------------------------------------------------------------
# 核心项：发真消息，同时盯着 /health —— 直接复现"重连中"那类故障
# ---------------------------------------------------------------------------

class HealthProbe(threading.Thread):
    """聊天期间持续探 /health。事件循环一旦被同步调用堵住，这里立刻看得见。"""

    def __init__(self, base: str, interval: float = 0.25):
        super().__init__(daemon=True)
        self.base, self.interval = base, interval
        self.stop_flag = threading.Event()
        self.max_ms = 0.0
        self.samples = 0
        self.failures = 0

    def run(self) -> None:
        while not self.stop_flag.is_set():
            code, _, ms = _req(self.base, "/health",
                               timeout=WATCHDOG_PROBE_TIMEOUT_MS / 1000)
            self.samples += 1
            self.max_ms = max(self.max_ms, ms)
            if code != 200:
                self.failures += 1
            self.stop_flag.wait(self.interval)


def check_chat_under_load(base: str, prompt: str) -> None:
    log_offset = AGENT_LOG.stat().st_size if AGENT_LOG.exists() else 0

    probe = HealthProbe(base)
    probe.start()

    url = base.rstrip("/") + "/api/chat/completions"
    body = json.dumps({
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
    }).encode()
    chat_headers = {"Content-Type": "application/json"}
    if _session_token():
        chat_headers[SESSION_HEADER] = _session_token()
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers=chat_headers)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    t0 = time.monotonic()
    status, chunks, saw_done, err = 0, [], False, None
    try:
        with opener.open(req, timeout=120) as r:
            status = r.status
            for raw in r:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    saw_done = True
                    break
                chunks.append(data)
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
    elapsed = time.monotonic() - t0

    probe.stop_flag.set()
    probe.join(timeout=3)

    # ---- C09 真实聊天 ----
    if status != 200 or err:
        record("C09", "FAIL", "真实聊天可用（SSE 流式）",
               f"http={status} err={err} 耗时{elapsed:.1f}s")
    elif not saw_done:
        record("C09", "FAIL", "真实聊天可用（SSE 流式）",
               f"流未正常收尾（无 [DONE]），收到 {len(chunks)} 个事件")
    else:
        record("C09", "PASS", "真实聊天可用（SSE 流式）",
               f"{len(chunks)} 个事件, {elapsed:.1f}s")

    # ---- C10 事件循环未被阻塞（本轮 bug 的直接回归测试）----
    if probe.failures:
        record("C10", "FAIL", "聊天期间 /health 持续可答",
               f"{probe.failures}/{probe.samples} 次探测失败 → 看门狗会杀后端")
    elif probe.max_ms > WATCHDOG_PROBE_TIMEOUT_MS * 0.6:
        record("C10", "WARN", "聊天期间 /health 持续可答",
               f"最大延迟 {probe.max_ms:.0f}ms 已逼近看门狗阈值 "
               f"{WATCHDOG_PROBE_TIMEOUT_MS}ms")
    else:
        record("C10", "PASS", "聊天期间 /health 持续可答",
               f"{probe.samples} 次探测, 最大 {probe.max_ms:.0f}ms, 0 失败")

    # ---- C11 日志窗口内无禁忌事件 ----
    hits: list[str] = []
    if AGENT_LOG.exists():
        with AGENT_LOG.open("rb") as f:
            f.seek(log_offset)
            window = f.read().decode("utf-8", "replace")
        for pat, why in FORBIDDEN_LOG_PATTERNS.items():
            if pat in window:
                hits.append(f"{pat} → {why}")
    if hits:
        record("C11", "FAIL", "本次请求未触发阻塞/误杀", "; ".join(dict.fromkeys(hits)))
    else:
        record("C11", "PASS", "本次请求未触发阻塞/误杀",
               "窗口内无 Lazy-installing / SIGTERM")


def main() -> int:
    ap = argparse.ArgumentParser(description="Vermes 可感知体验冒烟清单")
    ap.add_argument("--base", default=os.environ.get("VERMES_BASE",
                                                     "http://127.0.0.1:9119"))
    ap.add_argument("--skip-chat", action="store_true",
                    help="跳过真实聊天（不消耗 token，但会漏掉最关键的 C09-C11）")
    ap.add_argument("--prompt", default="只回复两个字：畅通")
    args = ap.parse_args()

    print(f"\n可感知体验冒烟清单  target={args.base}\n" + "─" * 66)
    print("【只读链路】")
    if not check_health(args.base):
        print("\n后端不可达，后续检查无意义。")
        return 1
    check_models(args.base)
    check_sessions(args.base)
    check_memory(args.base)
    check_flags(args.base)
    check_tools(args.base)
    check_skills(args.base)
    check_usage(args.base)

    print("\n【真流量链路】")
    if args.skip_chat:
        for c, n in (("C09", "真实聊天可用（SSE 流式）"),
                     ("C10", "聊天期间 /health 持续可答"),
                     ("C11", "本次请求未触发阻塞/误杀")):
            record(c, "SKIP", n, "--skip-chat")
    else:
        check_chat_under_load(args.base, args.prompt)

    n_fail = sum(1 for _, s, _, _ in _results if s == "FAIL")
    n_warn = sum(1 for _, s, _, _ in _results if s == "WARN")
    n_pass = sum(1 for _, s, _, _ in _results if s == "PASS")
    print("─" * 66)
    print(f"PASS {n_pass}   WARN {n_warn}   FAIL {n_fail}")
    if n_fail:
        print("\n有 FAIL —— 用户可感知的链路存在回退，不要发版。")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
