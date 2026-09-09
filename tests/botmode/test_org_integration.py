"""神魔堂集成层单测收口（S1 + S2 + S3 路由决策）。

覆盖此前 tests/ 零直接覆盖、仅由「手工 DMG 冒烟」间接验证的危险代码：

- S1 `_run_org_in_background`（chat.py:3874）：后台线程 + 独立 event loop + 独立 db 连接
  容器。断言：① fn 在后台跑完 ② 成功路径 finally 关 db ③ 异常路径写系统消息不静默吞
  ④ db_factory 被调用（独立连接）。这是神魔堂最危险代码（threading / run_until_complete /
  跨 loop），此前无任何单测。
- S2 `_org_announce_ws`（3799）/ `_org_stream_ws`（3832）：WS 实时广播。断言：① 有主 loop 时
  按契约投递（room_message / room_message_delta，agent_id 区分并行执行者，phase=delta|end）
  ② 无主 loop（fail-open）静默不广播 ③ 流式合批节流（120ms）+ done 全量 flush 并 pop key。
- S3 路由决策（bot_room_message_send 4387）：单 agent 房 → 触发 `_secretary_orchestrate`
  后台化（决策路径）。注：S3 的**全链路 e2e** 已由 test_room_routes.py::test_secretary_org_flow
  覆盖（建房→发需求→等 delivered/done→断言岗位表/自动拉人/流水线留痕）；此处补的是
  「入口路由正确选择秘书模式并触发后台」的轻量决策断言，与重 e2e 互补、不重跑流水线。

隔离方式：
- S1 直接调 `_run_org_in_background`，monkeypatch `chat_bp.threading` 为捕获+可 join 的
  真线程子类（保留真实 threading 语义，仅额外记录实例供 join），避免 daemon 线程无法等待。
- S2 用 running_loop fixture（独立线程 run_forever 真 loop）+ monkeypatch
  `_bot_broadcast_room_update` 为记录式协程；节流用 monkeypatch `chat_bp.time.monotonic`
  为可控时钟（确定性、无 sleep 抖动）。
- 不触网/不触 LLM：S3 路由测试 monkeypatch `_run_org_in_background` 为记录器，不真跑流水线。

可独立运行：python tests/botmode/test_org_integration.py
"""

import sys
import threading
import asyncio
import time
import types

sys.path.insert(0, "/Users/dongzusheng/Projects/vermes-electron")

import vermes_state
import vermes_cli.blueprints.chat as chat_bp
import run_agent
import pytest


# ─────────────────────────── 测试环境装配（照 test_room_routes 模式，自包含） ───────────────────────────

def _install_fakes(env):
    """把 SessionDB / 缓存 / AIAgent / provider 解析重定向到隔离环境。"""
    chat_bp.SessionDB = lambda: vermes_state.SessionDB(env.db_path)
    from vermes_cli.blueprints import agent_cache as _agent_cache_mod
    chat_bp._agent_cache = _agent_cache_mod._AgentCache()

    class _FakeAgent:
        tools = ["dummy"]

        def __init__(self, **kwargs):
            self.session_id = kwargs.get("session_id")
            env.captured.append(self)

        def chat(self, msg, stream_callback=None):
            if stream_callback is not None:
                for ch in f"[fake-agent-reply] {msg[:20]}":
                    stream_callback(ch)
            return f"[fake-agent-reply] {msg[:20]}"

    run_agent.AIAgent = _FakeAgent

    def _fake_resolve(model, provider=None):
        return ("agnes", "https://api.anthropic.com/v1", "k-ag", model or "agnes-2.0-flash")

    chat_bp._resolve_model_provider = _fake_resolve


def _client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    chat_bp.register_to(app)
    return TestClient(app)


@pytest.fixture
def testenv(tmp_path, monkeypatch):
    e = type("E", (), {})()
    e.db_path = tmp_path / "state.db"
    e.captured = []
    _install_fakes(e)
    yield e


@pytest.fixture
def org_loop():
    """独立线程 run_forever 的真 event loop，供 run_coroutine_threadsafe 投递。"""
    loop = asyncio.new_event_loop()
    t = threading.Thread(target=lambda: (asyncio.set_event_loop(loop), loop.run_forever()), daemon=True)
    t.start()
    chat_bp._main_loop_ref = loop
    yield loop
    loop.call_soon_threadsafe(loop.stop)
    t.join(timeout=2)
    try:
        loop.close()
    except Exception:
        pass
    chat_bp._main_loop_ref = None


def _recorder():
    """返回 (记录列表, 完成 Event, 伪广播协程)。"""
    recs = []
    evt = threading.Event()

    async def _broadcast(room_id, event, message=None):
        recs.append((room_id, event, message))
        evt.set()

    return recs, evt, _broadcast


# ─────────────────────────── S1：后台线程容器契约 ───────────────────────────

def test_run_org_in_background_success_runs_fn_and_closes_db(monkeypatch):
    state = {"ran": False}
    appends = []
    closed = []

    class _FakeDB:
        def append_bot_room_message(self, *a, **k):
            appends.append(a)

        def close(self):
            closed.append(True)

    def _db_factory():
        return _FakeDB()

    async def _fn(db, room_id):
        state["ran"] = True
        db.append_bot_room_message(room_id, "agent", None, "ok")

    threads = []
    real_thread = threading.Thread

    class _Cap(real_thread):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            threads.append(self)

    monkeypatch.setattr(chat_bp, "threading", types.SimpleNamespace(Thread=_Cap, Lock=threading.Lock))
    chat_bp._run_org_in_background(_db_factory, _fn, ("r1",))
    for t in threads:
        t.join(timeout=5)

    assert state["ran"] is True, "fn 应在后台跑完"
    assert len(appends) == 1, "fn 内的落库应执行"
    assert len(closed) == 1, "finally 应关闭独立 db 连接"


def test_run_org_in_background_exception_writes_system_msg(monkeypatch):
    appends = []

    class _FakeDB:
        def append_bot_room_message(self, *a, **k):
            appends.append(a)

        def close(self):
            pass

    def _db_factory():
        return _FakeDB()

    async def _fn(db, room, room_id):
        raise RuntimeError("boom")

    threads = []
    real_thread = threading.Thread

    class _Cap(real_thread):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            threads.append(self)

    monkeypatch.setattr(chat_bp, "threading", types.SimpleNamespace(Thread=_Cap, Lock=threading.Lock))
    # 真实调用约定：args=(room, room_id, ...)，异常处理器取 args[1] 即 room_id
    chat_bp._run_org_in_background(_db_factory, _fn, ("room_dummy", "r1"))
    for t in threads:
        t.join(timeout=5)

    # 异常处理器写：[组织流水线] 后台执行异常：{e}，不静默吞
    assert any(len(c) >= 4 and c[1] == "system" and "后台执行异常" in c[3] for c in appends), \
        f"异常应写系统消息而非静默吞；appends={appends}"


# ─────────────────────────── S2：WS 实时广播契约 ───────────────────────────

def test_org_announce_ws_broadcasts_room_message(monkeypatch, org_loop):
    recs, evt, broadcast = _recorder()
    monkeypatch.setattr(chat_bp, "_bot_broadcast_room_update", broadcast)

    chat_bp._org_announce_ws("r1", "hi", author_type="agent", author_ref="x")
    assert evt.wait(timeout=2), "有主 loop 时应投递广播"
    assert recs[0] == ("r1", "room_message",
                       {"author_type": "agent", "author_ref": "x", "content": "hi"})


def test_org_announce_ws_fail_open_without_loop(monkeypatch):
    recs, evt, broadcast = _recorder()
    monkeypatch.setattr(chat_bp, "_bot_broadcast_room_update", broadcast)
    chat_bp._main_loop_ref = None  # 无主 loop → fail-open

    chat_bp._org_announce_ws("r1", "hi")
    assert recs == [], "无主 loop 时应静默返回，不广播"


def test_org_stream_ws_buffers_until_interval_then_flushes(monkeypatch, org_loop):
    recs, evt, broadcast = _recorder()
    monkeypatch.setattr(chat_bp, "_bot_broadcast_room_update", broadcast)
    clock = {"t": 0.0}
    monkeypatch.setattr(chat_bp, "time", types.SimpleNamespace(monotonic=lambda: clock["t"]))
    chat_bp._org_stream_buf.clear()

    # t=0：now-last=0 < 0.12 → 缓冲不广播
    chat_bp._org_stream_ws("r1", "a1", "x")
    assert recs == []
    # t=0.05：仍 < 0.12 → 缓冲
    clock["t"] = 0.05
    chat_bp._org_stream_ws("r1", "a1", "y")
    assert recs == []
    # t=0.2：>= 0.12 → 合批 flush "xyz"
    clock["t"] = 0.2
    chat_bp._org_stream_ws("r1", "a1", "z")
    assert evt.wait(timeout=2)
    assert recs[-1][1] == "room_message_delta"
    assert recs[-1][2] == {"agent_id": "a1", "phase": "delta", "delta": "xyz"}


def test_org_stream_ws_done_flushes_full_and_pops_key(monkeypatch, org_loop):
    recs, evt, broadcast = _recorder()
    monkeypatch.setattr(chat_bp, "_bot_broadcast_room_update", broadcast)
    clock = {"t": 1.0}
    monkeypatch.setattr(chat_bp, "time", types.SimpleNamespace(monotonic=lambda: clock["t"]))
    chat_bp._org_stream_buf.clear()

    chat_bp._org_stream_ws("r1", "a1", "final", done=True)
    assert evt.wait(timeout=2)
    assert recs[-1][1] == "room_message_delta"
    assert recs[-1][2] == {"agent_id": "a1", "phase": "end", "delta": "final"}
    assert ("r1", "a1") not in chat_bp._org_stream_buf, "done 应 pop 缓冲 key"


def test_org_stream_ws_fail_open_without_loop(monkeypatch):
    recs, evt, broadcast = _recorder()
    monkeypatch.setattr(chat_bp, "_bot_broadcast_room_update", broadcast)
    chat_bp._main_loop_ref = None
    chat_bp._org_stream_buf.clear()

    chat_bp._org_stream_ws("r1", "a1", "x", done=True)
    assert recs == [], "无主 loop 时应静默返回，不广播"


# ─────────────────────────── S3：入口路由决策（秘书模式选择） ───────────────────────────

def test_message_send_routes_secretary_mode_to_background(monkeypatch, testenv):
    """单 agent 房（无岗位表）→ 应触发 `_secretary_orchestrate` 后台化，且 HTTP 秒回。"""
    triggered = []

    def _fake_bg(db_factory, fn, args, kwargs=None):
        triggered.append((fn, args))

    monkeypatch.setattr(chat_bp, "_run_org_in_background", _fake_bg)
    chat_bp._bot_mode_enabled = lambda: True
    client = _client()

    r = client.post("/api/bot/rooms", json={"name": "秘书路由房"})
    room_id = r.json().get("room_id")
    assert r.status_code == 200 and room_id, r.text[:200]
    r = client.post(f"/api/bot/rooms/{room_id}/members", json={"ref_id": "researcher"})
    assert r.status_code == 200 and r.json().get("ok") is True, r.text[:200]

    r = client.post(f"/api/bot/rooms/{room_id}/messages", json={"text": "写一份周报"})
    assert r.status_code == 200 and r.json().get("ok") is True, r.text[:300]

    assert any(fn is chat_bp._secretary_orchestrate for fn, _ in triggered), \
        f"单 agent 房应路由到秘书模式后台 org；triggered={[f.__name__ for f,_ in triggered]}"


if __name__ == "__main__":
    import pytest as _pytest
    _pytest.main([__file__, "-p", "no:xdist", "-o", "addopts="])
