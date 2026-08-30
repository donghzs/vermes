"""P4-1 发砖审核状态机 + 端点接线测试。

分三层：
1. BrickReviewStore 状态机（核心逻辑，unit）
2. _validate_submission_ci（auto_reject 触发源，unit）
3. 端点穿 auth_middleware 集成（tmp VERMES_HOME 隔离，避免写真实 home）
"""
import os

import pytest

from vermes_cli.capabilities.brick_reviews import (
    BrickReviewError,
    BrickReviewStore,
    STATUS_APPROVED,
    STATUS_IN_REVIEW,
    STATUS_REJECTED,
    STATUS_SUBMITTED,
    get_review_store,
)


@pytest.fixture
def store():
    s = BrickReviewStore()
    s._cache = {}
    yield s
    s._cache = None


# ── 1. 状态机 ─────────────────────────────────────────────
class TestBrickReviewStateMachine:
    def test_submit_creates_submitted(self, store):
        rev = store.submit("module:foo", metadata={"version": "1.0.0"})
        assert rev.status == STATUS_SUBMITTED
        assert rev.metadata["version"] == "1.0.0"

    def test_submit_then_begin_review(self, store):
        store.submit("module:foo", {})
        rev = store.begin_review("module:foo", reviewer="alice")
        assert rev.status == STATUS_IN_REVIEW

    def test_approve_from_in_review(self, store):
        store.submit("module:foo", {})
        store.begin_review("module:foo")
        rev = store.review("module:foo", "approve", reviewer="alice", note="ok")
        assert rev.status == STATUS_APPROVED

    def test_reject_from_submitted(self, store):
        store.submit("module:foo", {})
        rev = store.review("module:foo", "reject", note="bad")
        assert rev.status == STATUS_REJECTED
        assert rev.decision_note == "bad"

    def test_auto_reject_from_submitted(self, store):
        store.submit("module:foo", {})
        rev = store.auto_reject("module:foo", "sha256 mismatch")
        assert rev.status == STATUS_REJECTED
        assert rev.auto_reject_reason == "sha256 mismatch"
        assert rev.reviewed_by == "ci"

    def test_resubmit_after_rejected(self, store):
        store.submit("module:foo", {})
        store.review("module:foo", "reject")
        rev = store.submit("module:foo", metadata={"v": "2"})
        assert rev.status == STATUS_SUBMITTED

    def test_cannot_submit_when_already_submitted(self, store):
        store.submit("module:foo", {})
        with pytest.raises(BrickReviewError):
            store.submit("module:foo", {})

    def test_review_on_missing_record_raises(self, store):
        with pytest.raises(BrickReviewError):
            store.review("module:foo", "approve")

    def test_auto_reject_on_non_submitted_raises(self, store):
        store.submit("module:foo", {})
        store.review("module:foo", "approve")
        with pytest.raises(BrickReviewError):
            store.auto_reject("module:foo", "x")

    def test_list_filter_by_status(self, store):
        store.submit("a", {})
        store.submit("b", {})
        store.review("b", "approve")
        submitted = store.list(status=STATUS_SUBMITTED)
        assert {r.brick_id for r in submitted} == {"a"}


# ── 2. CI 校验（auto_reject 触发源）───────────────────────
class TestSubmissionCIValidation:
    def test_bad_sha256_flagged(self):
        from vermes_cli.blueprints.bricks import _validate_submission_ci, SubmitBrickRequest
        p = SubmitBrickRequest(code_sha256="not-hex")
        conflicts = _validate_submission_ci(p)
        assert any("sha256" in c for c in conflicts)

    def test_valid_submission_no_conflict(self):
        from vermes_cli.blueprints.bricks import _validate_submission_ci, SubmitBrickRequest
        p = SubmitBrickRequest(version="1.0.0", vermes_min="0.0.0")
        assert _validate_submission_ci(p) == []

    def test_missing_dependency_flagged(self):
        from vermes_cli.blueprints.bricks import _validate_submission_ci, SubmitBrickRequest
        p = SubmitBrickRequest(dependencies=["nonexistent_brick_xyz"])
        conflicts = _validate_submission_ci(p)
        assert any("依赖缺失" in c for c in conflicts)


# ── 3. 端点穿 auth_middleware 集成 ────────────────────────
class TestBrickReviewEndpoints:
    def test_submit_and_review_through_middleware(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VERMES_HOME", str(tmp_path))
        get_review_store()._cache = None  # 强制从 tmp home 重读

        import vermes_cli.web_server as ws
        # 放行 auth（模拟已登录 SPA），仅验证端点接线 + 状态机串联
        monkeypatch.setattr(ws, "_has_valid_session_token", lambda req: True)
        from starlette.testclient import TestClient
        client = TestClient(ws.app)

        r = client.post(
            "/api/v1/bricks/module:foo/submit",
            json={"version": "1.0.0", "submitted_by": "dev"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "submitted"

        r2 = client.post(
            "/api/v1/bricks/module:foo/review",
            json={"decision": "approve", "reviewer": "alice", "note": "good"},
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["status"] == "approved"

        # 已 approved，不再出现在 submitted 列表
        r3 = client.get("/api/v1/bricks/reviews?status=submitted")
        assert r3.status_code == 200
        assert all(x["brick_id"] != "module:foo" for x in r3.json()["reviews"])

    def test_submit_with_ci_failure_auto_rejects(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VERMES_HOME", str(tmp_path))
        get_review_store()._cache = None

        import vermes_cli.web_server as ws
        monkeypatch.setattr(ws, "_has_valid_session_token", lambda req: True)
        from starlette.testclient import TestClient
        client = TestClient(ws.app)

        r = client.post(
            "/api/v1/bricks/module:bad/submit",
            json={"code_sha256": "zzz"},  # 非法 sha256 → CI 失败
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["auto_rejected"] is True
        assert body["status"] == "rejected"

    def test_review_requires_token_returns_401_without_auth(self, monkeypatch):
        """一致性：bricks 端点非 public，无 token 必须 401（与现有 bricks 同姿态）。"""
        import vermes_cli.web_server as ws
        monkeypatch.setattr(ws, "_has_valid_session_token", lambda req: False)
        from starlette.testclient import TestClient
        client = TestClient(ws.app)
        r = client.post("/api/v1/bricks/module:foo/review", json={"decision": "approve"})
        assert r.status_code == 401
