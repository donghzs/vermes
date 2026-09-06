"""④ Cron monitor-mode — 回归守卫（2026-09-06 补）

背景：核心逻辑由 commit ``794a774616`` 落地（hash 短路 + 记忆加载），但**零测试
覆盖**——改动了 ``cron/scheduler.py`` 的作业执行路径却无守护。本文件补齐守护，
防两类回退：

1. **行为回退**：target 解析 / hash 计算 / 持久化 的语义被改坏；
2. **零回归回退**：``monitor_mode`` 缺省（False）时行为被改成与现状不一致
   ——spec 明确要求「缺省 False 时与现状完全一致」。

零回归契约（L``_ZERO_REGRESSION_CONTRACT``）以源码契约断言形式守护：
生产代码内联在 ``_run_job()`` 里难以单测（需拉起完整作业），故以「源码必须含
该表达式」的契约断言守住，被人改回硬编码即红灯。
"""

from __future__ import annotations

import inspect
from pathlib import Path
from unittest import mock

import pytest

from cron import scheduler as sched


# ── 零回归契约：monitor_mode 缺省 False 时必须与现状一致 ──────────────
# 表达式来源：cron/scheduler.py:1743 `skip_memory=not bool(job.get("monitor_mode"))`
_ZERO_REGRESSION_CONTRACT = "skip_memory=not bool(job.get"


# ── _compute_monitor_hash ────────────────────────────────────────────


def test_hash_is_deterministic_and_16_hex():
    """同输入恒等输出；输出为 16 位 hex（spec §3 口径）。"""
    h1 = sched._compute_monitor_hash("snapshot-A")
    h2 = sched._compute_monitor_hash("snapshot-A")
    assert h1 == h2, "hash 必须确定性（否则短路判断失效）"
    assert len(h1) == 16, f"spec 约定 16 位摘要，实得 {len(h1)}"
    int(h1, 16)  # 必须是合法 hex，非 hex 会抛 ValueError


def test_hash_strips_leading_trailing_whitespace():
    """normalize 阶段 strip —— 目标内容仅空白差异不应触发误报。"""
    assert sched._compute_monitor_hash("  same  ") == sched._compute_monitor_hash(
        "same"
    )


def test_hash_differs_on_content_change():
    """内容变化 → hash 变化（短路的前提：内容真变才跑 LLM）。"""
    assert sched._compute_monitor_hash("v1") != sched._compute_monitor_hash("v2")


def test_hash_handles_empty_and_none():
    """空值/None 不得抛异常（best-effort 语义）。"""
    assert sched._compute_monitor_hash("") == sched._compute_monitor_hash(None)


# ── _resolve_monitor_target ──────────────────────────────────────────


def test_target_falls_back_to_prompt_when_absent():
    """无 monitor_target → 回退 prompt（spec：目标缺失不应静默禁用作业）。"""
    job = {"prompt": "检查首页是否变化"}
    assert sched._resolve_monitor_target(job) == "检查首页是否变化"


def test_target_bare_string_is_text():
    assert sched._resolve_monitor_target({"monitor_target": "字面快照"}) == "字面快照"


def test_target_dict_text():
    job = {"monitor_target": {"type": "text", "value": "hello"}}
    assert sched._resolve_monitor_target(job) == "hello"


def test_target_dict_file_reads_content(tmp_path):
    f = tmp_path / "watch.txt"
    f.write_text("file-snapshot-42", encoding="utf-8")
    job = {"monitor_target": {"type": "file", "value": str(f)}}
    out = sched._resolve_monitor_target(job)
    assert "file-snapshot-42" in out


def test_target_dict_missing_value_falls_back_to_prompt():
    """dict 但无 value → 回退 prompt（不静默禁用）。"""
    job = {"prompt": "P", "monitor_target": {"type": "url"}}
    assert sched._resolve_monitor_target(job) == "P"


def test_target_unknown_type_best_effort_stringify():
    """未知 type 不得抛异常 —— 降级为 str()，由 hash 短路兜底。"""
    job = {"monitor_target": {"type": "telepathy", "value": "x"}}
    assert sched._resolve_monitor_target(job) == "x"


def test_target_url_is_fetched_with_size_cap():
    """url 类型走 urlopen，响应体截断到 256 KiB 防内存爆炸。"""
    payload = b"HTTP-BODY"
    fake_resp = mock.MagicMock()
    fake_resp.read.return_value = payload
    fake_resp.__enter__.return_value = fake_resp
    fake_resp.__exit__.return_value = False

    with mock.patch(
        "urllib.request.urlopen", return_value=fake_resp
    ) as m_open:
        out = sched._resolve_monitor_target(
            {"monitor_target": {"type": "url", "value": "https://example.com"}}
        )
    assert "HTTP-BODY" in out
    assert m_open.call_args.kwargs.get("timeout") == 20, "必须有超时防挂死"
    fake_resp.read.assert_called_once_with(256 * 1024)


# ── _get / _store_monitor_hash（DB 往返 + best-effort 容错）──────────


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """把 SessionDB 重定向到临时库——绝不污染用户真实状态库。"""
    from vermes_state import SessionDB

    db_path = tmp_path / "state.db"
    monkeypatch.setattr("vermes_state.SessionDB", lambda: SessionDB(db_path))
    yield db_path


def test_monitor_hash_roundtrip(isolated_db):
    """存 → 读 一致（短路判断依赖这条）。"""
    sched._store_monitor_hash("job-A", "deadbeefdeadbeef")
    assert sched._get_monitor_hash("job-A") == "deadbeefdeadbeef"


def test_monitor_hash_upsert_overwrites(isolated_db):
    """同 job 二次写入覆盖（ON CONFLICT DO UPDATE），不是追加。"""
    sched._store_monitor_hash("job-A", "hash-v1")
    sched._store_monitor_hash("job-A", "hash-v2")
    assert sched._get_monitor_hash("job-A") == "hash-v2"


def test_monitor_hash_isolated_per_job(isolated_db):
    """不同 job 的 hash 互不影响（否则 A 作业的状态会短路掉 B 作业）。"""
    sched._store_monitor_hash("job-A", "aaa")
    sched._store_monitor_hash("job-B", "bbb")
    assert sched._get_monitor_hash("job-A") == "aaa"
    assert sched._get_monitor_hash("job-B") == "bbb"


def test_monitor_hash_unknown_job_returns_none(isolated_db):
    """未存过的 job → None（首次运行必须跑 LLM，不能误判为「无变化」）。"""
    assert sched._get_monitor_hash("never-seen") is None


def test_monitor_hash_read_failure_is_non_fatal(monkeypatch):
    """DB 读失败 → 返回 None 而非抛出（best-effort：宁可多跑一次 LLM）。"""

    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr("vermes_state.SessionDB", _boom)
    assert sched._get_monitor_hash("job-A") is None


def test_monitor_hash_write_failure_is_non_fatal(monkeypatch):
    """DB 写失败 → 静默吞掉（best-effort：不能因存状态失败而中断作业）。"""

    def _boom():
        raise RuntimeError("disk full")

    monkeypatch.setattr("vermes_state.SessionDB", _boom)
    sched._store_monitor_hash("job-A", "hash")  # 不得抛出


# ── notepad（job 级小笔记）──────────────────────────────────────────


def test_notepad_roundtrip_and_overwrite(isolated_db):
    from vermes_state import SessionDB

    # fixture 已把 vermes_state.SessionDB patch 为「指向临时库」的 0 参工厂
    db = SessionDB()
    try:
        db.set_notepad("job-A", "last_alert", "2026-09-06")
        assert db.get_notepad("job-A", "last_alert") == "2026-09-06"
        db.set_notepad("job-A", "last_alert", "2026-09-07")
        assert db.get_notepad("job-A", "last_alert") == "2026-09-07"
    finally:
        db.close()


def test_notepad_scoped_by_job(isolated_db):
    """笔记按 (job_id, key) 复合主键隔离——A 作业的笔记不泄漏到 B。"""
    from vermes_state import SessionDB

    # fixture 已把 vermes_state.SessionDB patch 为「指向临时库」的 0 参工厂
    db = SessionDB()
    try:
        db.set_notepad("job-A", "k", "A-value")
        db.set_notepad("job-B", "k", "B-value")
        assert db.get_notepad("job-A", "k") == "A-value"
        assert db.get_notepad("job-B", "k") == "B-value"
    finally:
        db.close()


def test_notepad_missing_key_returns_none(isolated_db):
    from vermes_state import SessionDB

    # fixture 已把 vermes_state.SessionDB patch 为「指向临时库」的 0 参工厂
    db = SessionDB()
    try:
        assert db.get_notepad("job-A", "nope") is None
    finally:
        db.close()


# ── 零回归契约守卫 ───────────────────────────────────────────────────


def test_zero_regression_contract_skip_memory_is_conditional():
    """防回退：monitor_mode 缺省 False → skip_memory 必须仍为 True。

    spec 硬性要求「零回归」：非监控作业的行为不得因本特性改变。该逻辑内联
    在 ``_run_job()`` 中难以单测，故以源码契约断言守住——若被人改回硬编码
    ``skip_memory=True``（会静默禁用监控作业记忆加载）或无条件 False（会让
    所有 cron 作业都加载记忆、改变既有行为），此处立即红灯。
    """
    src = inspect.getsource(sched)
    assert (
        _ZERO_REGRESSION_CONTRACT in src
    ), "skip_memory 必须是 monitor_mode 的条件表达式，禁止改回硬编码"


def test_monitor_helpers_are_all_present():
    """防回退：三个 helper 缺一即监控链路断裂（名字改动需同步本测试）。"""
    for name in (
        "_resolve_monitor_target",
        "_compute_monitor_hash",
        "_get_monitor_hash",
        "_store_monitor_hash",
    ):
        assert hasattr(sched, name), f"缺失 helper: {name}"
