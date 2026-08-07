"""P4 verified 信号：聚合逻辑 + 持久化（反向验证 R5 解药）。

- ``compute_verified``：纯函数，覆盖 vr / ov / file_landed 各组合。
- ``record_verification``：写 ``__verified__`` raw_event，``success`` 列即 verdict。
- 反向验证：本测试依赖 ``compute_verified`` / ``record_verification`` 的存在；
  拷到这两个符号加入前必 ``ImportError`` → 证测试验真功能，而非 mock 出来的绿。
"""
import sqlite3
import tempfile

import pytest

from harness.outcome_verifier import compute_verified
from agent import raw_event as raw_event_mod
from agent.raw_event import record_verification


# ─── compute_verified 纯函数 ────────────────────────────────────────────────
class TestComputeVerified:
    def test_both_ok_no_file(self):
        assert compute_verified(vr_ok=True, ov_ok=True, file_landed=None) is True

    def test_vr_fail(self):
        assert compute_verified(vr_ok=False, ov_ok=True, file_landed=None) is False

    def test_ov_fail(self):
        assert compute_verified(vr_ok=True, ov_ok=False, file_landed=None) is False

    def test_file_not_landed(self):
        # 写类工具结果未落盘 → 即便 vr/ov 都 ok 也应判未验证
        assert compute_verified(vr_ok=True, ov_ok=True, file_landed=False) is False

    def test_file_landed_true(self):
        assert compute_verified(vr_ok=True, ov_ok=True, file_landed=True) is True

    def test_file_landed_none_does_not_force_fail(self):
        # file_landed=None（非写类/判定失败）不应把已验证的翻成 False
        assert compute_verified(vr_ok=True, ov_ok=True, file_landed=None) is True


# ─── record_verification 持久化 ─────────────────────────────────────────────
@pytest.fixture
def temp_self_model_db(monkeypatch):
    """把 self-model.db 指向临时文件并建表，隔离真实 DB。"""
    path = tempfile.mktemp(suffix=".db")
    conn = sqlite3.connect(path)
    raw_event_mod.ensure_raw_events_table(conn)
    conn.close()
    monkeypatch.setattr("agent.evolution_manager.get_self_model_db", lambda: path)
    yield path


class _FakeAgent:
    session_id = "sess-verified-test"
    turn_counter = 7


class TestRecordVerification:
    def test_writes_verified_event_success(self, temp_self_model_db):
        rid = record_verification("write_file", True, "self_validator=True p0a=True", _FakeAgent())
        assert rid is not None
        conn = sqlite3.connect(temp_self_model_db)
        row = conn.execute(
            "SELECT tool_name, success, result_preview FROM raw_events WHERE id=?", (rid,)
        ).fetchone()
        conn.close()
        assert row[0] == "__verified__"
        assert row[1] == 1  # success 列 = verified
        assert "self_validator=True" in row[2]

    def test_writes_verified_event_failure(self, temp_self_model_db):
        rid = record_verification("write_file", False, "p0a=False", _FakeAgent())
        assert rid is not None
        conn = sqlite3.connect(temp_self_model_db)
        row = conn.execute(
            "SELECT tool_name, success FROM raw_events WHERE id=?", (rid,)
        ).fetchone()
        conn.close()
        assert row[0] == "__verified__"
        assert row[1] == 0  # 未验证 → success=0

    def test_fail_open_when_raw_event_raises(self, temp_self_model_db, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("db down")

        monkeypatch.setattr(raw_event_mod, "record_raw_event", _boom)
        # fail-open：底层写失败只返回 None，绝不抛
        assert record_verification("read_file", True, "x", _FakeAgent()) is None
