"""Brick 发砖审核状态机（P4-1）。

开发者提交 brick → 审核流（submitted → in_review → approved / rejected）。
状态落盘 ``~/.vermes/brick_reviews.json``（类比 ``bricks.json``），轻量、可审计。

状态机：
    submitted ──begin_review──► in_review
    submitted ──review(approve)──► approved
    in_review  ──review(approve)──► approved
    submitted  ──review(reject)──► rejected   (人工)
    in_review  ──review(reject)──► rejected   (人工)
    submitted  ──auto_reject──► rejected      (CI 校验失败，不进人工队列)
    rejected   ──submit(resubmit)──► submitted (可重投)

CI 校验（auto_reject 触发）：sha256 格式 / 依赖存在性 / version 倒退，复用
module_catalog.check_module_install_conflicts。
"""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from vermes_cli.capabilities.registry import vermes_home

_log = logging.getLogger(__name__)

# 合法状态
STATUS_SUBMITTED = "submitted"
STATUS_IN_REVIEW = "in_review"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
VALID_STATUSES = {STATUS_SUBMITTED, STATUS_IN_REVIEW, STATUS_APPROVED, STATUS_REJECTED}

# 合法决策
DECISION_APPROVE = "approve"
DECISION_REJECT = "reject"
DECISION_START = "start"
VALID_DECISIONS = {DECISION_APPROVE, DECISION_REJECT, DECISION_START}


@dataclass
class BrickReview:
    brick_id: str
    status: str = STATUS_SUBMITTED
    submitted_by: Optional[str] = None
    submitted_at: Optional[float] = None
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[float] = None
    decision_note: str = ""
    # 提交时携带的 brick 元数据提案（P4-2 治理字段）
    metadata: Dict[str, Any] = field(default_factory=dict)
    # 自动拒绝原因（CI 校验失败）
    auto_reject_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _store_path() -> Path:
    return vermes_home() / "brick_reviews.json"


def load_reviews() -> Dict[str, BrickReview]:
    """读全部审核记录（按 brick_id 索引）。文件缺失/坏 → 空字典（fail-open）。"""
    p = _store_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8")) or {}
        out: Dict[str, BrickReview] = {}
        for k, v in (data.get("reviews") or {}).items():
            try:
                out[k] = BrickReview(**v)
            except Exception:  # noqa: BLE001 - 坏条目跳过，不阻断整体
                _log.warning("brick_review %s 解析跳过", k)
        return out
    except Exception as exc:  # noqa: BLE001
        _log.warning("brick_reviews.json load failed (reset): %s", exc)
        return {}


def save_reviews(reviews: Dict[str, BrickReview]) -> None:
    _store_path().parent.mkdir(parents=True, exist_ok=True)
    data = {"reviews": {k: v.to_dict() for k, v in reviews.items()}}
    _store_path().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _now() -> float:
    import time
    return time.time()


class BrickReviewStore:
    """线程安全的审核状态机（单例式按进程缓存）。"""

    def __init__(self):
        self._lock = threading.RLock()
        self._cache: Optional[Dict[str, BrickReview]] = None

    def _all(self) -> Dict[str, BrickReview]:
        if self._cache is None:
            self._cache = load_reviews()
        return self._cache

    def get(self, brick_id: str) -> Optional[BrickReview]:
        return self._all().get(brick_id)

    def list(self, status: Optional[str] = None) -> List[BrickReview]:
        items = list(self._all().values())
        if status:
            items = [i for i in items if i.status == status]
        return items

    def submit(self, brick_id: str, metadata: Dict[str, Any], submitted_by: Optional[str] = None) -> BrickReview:
        """开发者提交（可重投：rejected → submitted）。"""
        with self._lock:
            all_ = self._all()
            existing = all_.get(brick_id)
            if existing and existing.status not in (STATUS_REJECTED,):
                raise BrickReviewError(
                    f"brick {brick_id} 当前状态 {existing.status}，不可重复提交"
                )
            rev = BrickReview(
                brick_id=brick_id,
                status=STATUS_SUBMITTED,
                submitted_by=submitted_by,
                submitted_at=_now(),
                metadata=dict(metadata or {}),
            )
            all_[brick_id] = rev
            save_reviews(all_)
            return rev

    def begin_review(self, brick_id: str, reviewer: Optional[str] = None) -> BrickReview:
        """submitted → in_review。"""
        with self._lock:
            rev = self._require(brick_id, {STATUS_SUBMITTED})
            rev.status = STATUS_IN_REVIEW
            rev.reviewed_by = reviewer
            rev.reviewed_at = _now()
            save_reviews(self._all())
            return rev

    def review(self, brick_id: str, decision: str, reviewer: Optional[str] = None,
               note: str = "") -> BrickReview:
        """人工决策：in_review/submitted → approved / rejected。"""
        if decision not in (DECISION_APPROVE, DECISION_REJECT):
            raise BrickReviewError(f"非法决策: {decision}")
        with self._lock:
            rev = self._require(brick_id, {STATUS_SUBMITTED, STATUS_IN_REVIEW})
            rev.status = STATUS_APPROVED if decision == DECISION_APPROVE else STATUS_REJECTED
            rev.reviewed_by = reviewer
            rev.reviewed_at = _now()
            rev.decision_note = note
            save_reviews(self._all())
            return rev

    def auto_reject(self, brick_id: str, reason: str) -> BrickReview:
        """CI 校验失败：submitted → rejected，不进人工队列。"""
        with self._lock:
            all_ = self._all()
            rev = all_.get(brick_id)
            if rev is None:
                rev = BrickReview(brick_id=brick_id, submitted_at=_now())
            if rev.status != STATUS_SUBMITTED:
                raise BrickReviewError(
                    f"brick {brick_id} 状态 {rev.status}，非 submitted 不可 auto_reject"
                )
            rev.status = STATUS_REJECTED
            rev.auto_reject_reason = reason
            rev.reviewed_by = "ci"
            rev.reviewed_at = _now()
            rev.decision_note = f"[CI 自动拒绝] {reason}"
            all_[brick_id] = rev
            save_reviews(all_)
            return rev

    def _require(self, brick_id: str, allowed: set) -> BrickReview:
        rev = self._all().get(brick_id)
        if rev is None:
            raise BrickReviewError(f"brick {brick_id} 无审核记录，请先 submit")
        if rev.status not in allowed:
            raise BrickReviewError(
                f"brick {brick_id} 状态 {rev.status}，不允许该操作（需 {sorted(allowed)}）"
            )
        return rev


class BrickReviewError(Exception):
    """审核状态机非法操作。"""


# 模块级单例（与 BrickRegistry 同款懒加载风格）
_store = BrickReviewStore()


def get_review_store() -> BrickReviewStore:
    return _store
