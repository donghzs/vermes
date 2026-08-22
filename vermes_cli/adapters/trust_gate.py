"""L2b 信任闸门（UNIVERSAL_OPERATION_LAYER_DESIGN.md §15.3 / §14.5）。

设计纪律（守「薄」）：
- 只做权限声明 + 执行前闸门，不写垂直逻辑。
- 闸门插在 SoftwareAdapter.invoke() 入口（§15.4），默认 deny-unless-declared。
- cli_native（纯 CLI 透传）= 低权信任：exec_external=true 但 network=false、
  requires_explicit_consent=false → 默认 ALLOW（不阻断现有 273 工具）。
- sdk_bridge（加载外部 SDK / 长驻进程）= 默认 requires_explicit_consent=true → ASK_USER。
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 闸门决策
ALLOW = "allow"
DENY = "deny"
ASK_USER = "ask_user"

# sandbox 取值
SANDBOX_NONE = "none"
SANDBOX_CONTAINER = "container"
SANDBOX_SECCOMP = "seccomp"


@dataclass
class PermissionSpec:
    """工具的权限声明。发现时附带；未声明 = deny-unless-declared。"""

    reads_fs: bool = False
    writes_fs: bool = False
    network: bool = False
    exec_external: bool = False
    sandbox: str = SANDBOX_NONE  # none | container | seccomp
    requires_explicit_consent: bool = False


@dataclass
class GateResult:
    """闸门判定结果。"""

    decision: str  # ALLOW | DENY | ASK_USER
    reason: str = ""
    permission: Optional[PermissionSpec] = None
    rule: str = ""  # 命中规则名（undeclared_deny / consent_required / network_no_sandbox / default_allow）


# ---------------------------------------------------------------------------
# 闸门命中率计数（fail-open 观测期数据，驱动 fail-closed 切换决策）
#
# 为什么要落盘：桌面版 app 每次重启都是新进程，纯进程内计数器一重启即清零，
# 攒不出「观测期累计基线」→ 切 fail-closed 又变成拍脑袋。因此持久化**聚合快照**
# （不是逐事件日志，避免 273 工具高频写盘与无界增长）。
# ---------------------------------------------------------------------------
_gate_stats_lock = threading.Lock()
_gate_stats: dict = {
    "total": 0,
    "decisions": {"allow": 0, "deny": 0, "ask_user": 0},
    "rules": {},  # rule -> count
}

# 每累计 N 次命中落盘一次（debounce，降低写盘频率）；进程退出时 atexit 兜底 flush。
_FLUSH_EVERY = 25
_unflushed = 0
_loaded = False

_DEFAULT_STATS_PATH = "~/.vermes/gate_stats.json"


def _stats_path() -> Path:
    """快照路径。每次读环境变量，便于测试隔离（VERMES_GATE_STATS_PATH）。"""
    return Path(
        os.path.expanduser(os.environ.get("VERMES_GATE_STATS_PATH", _DEFAULT_STATS_PATH))
    )


def _load_persisted_locked() -> None:
    """首次使用时把已存盘的累计值合并进内存计数（调用方须持锁）。"""
    global _loaded
    if _loaded:
        return
    _loaded = True  # 无论成功与否只尝试一次，避免每次命中都碰磁盘
    try:
        raw = json.loads(_stats_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return
    except Exception as exc:  # 损坏/无权限：fail-open，从零开始计
        logger.debug("gate stats load failed: %s", exc)
        return
    if not isinstance(raw, dict):
        return
    try:
        _gate_stats["total"] += int(raw.get("total", 0) or 0)
        for key, val in (raw.get("decisions") or {}).items():
            _gate_stats["decisions"][key] = (
                _gate_stats["decisions"].get(key, 0) + int(val or 0)
            )
        for key, val in (raw.get("rules") or {}).items():
            _gate_stats["rules"][key] = _gate_stats["rules"].get(key, 0) + int(val or 0)
    except Exception as exc:
        logger.debug("gate stats merge failed: %s", exc)


def _snapshot_locked() -> dict:
    return {
        "total": _gate_stats["total"],
        "decisions": dict(_gate_stats["decisions"]),
        "rules": dict(_gate_stats["rules"]),
    }


def _write_snapshot(snapshot: dict) -> None:
    """原子写快照（tmp + os.replace）。任何失败都只记 debug，绝不影响工具执行。"""
    try:
        path = _stats_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(tmp, path)
    except Exception as exc:
        logger.debug("gate stats flush failed: %s", exc)


def flush_gate_stats() -> None:
    """立即把累计值落盘（atexit / 观测期结束手动收口时调用）。"""
    global _unflushed
    with _gate_stats_lock:
        snapshot = _snapshot_locked()
        _unflushed = 0
    _write_snapshot(snapshot)


atexit.register(flush_gate_stats)


def record_gate_hit(decision: str, rule: str) -> None:
    """线程安全的闸门命中计数。decision ∈ {allow,deny,ask_user}；rule 为命中规则名。

    三个去处：① 进程内累计 ② debounce 落盘（跨重启累计）
    ③ 转发 agent.metrics → 经既有 /api/v1/metrics 暴露。
    """
    global _unflushed
    snapshot = None
    with _gate_stats_lock:
        _load_persisted_locked()
        _gate_stats["total"] += 1
        _gate_stats["decisions"][decision] = _gate_stats["decisions"].get(decision, 0) + 1
        _gate_stats["rules"][rule] = _gate_stats["rules"].get(rule, 0) + 1
        _unflushed += 1
        if _unflushed >= _FLUSH_EVERY:
            _unflushed = 0
            snapshot = _snapshot_locked()
    # 落盘放在锁外，避免持锁做 IO
    if snapshot is not None:
        _write_snapshot(snapshot)
    # 转发到既有 Prometheus 端点（/api/v1/metrics），不新建可观测机制。
    # 注意：此处 except 是 fail-open 必需，但正因为它会吞异常，
    # tests/test_registry_dispatch_gate.py 里有「转发真实递增」断言兜底，
    # 防止重演 record_count 那种「import 失败被吞 → 死指标」。
    try:
        from agent.metrics import record_gate_decision

        record_gate_decision(decision, rule)
    except Exception as exc:
        logger.debug("gate metrics forward failed: %s", exc)


def get_gate_stats() -> dict:
    """返回观测期累计命中率（含已落盘的历史累计）。

    fail-open 结束后据此决定是否切 fail-closed。
    """
    with _gate_stats_lock:
        _load_persisted_locked()
        return _snapshot_locked()


def reset_gate_stats(clear_persisted: bool = False) -> None:
    """清空计数（测试隔离 / 新观测期起点）。

    clear_persisted=True 时同时删除磁盘快照（开启全新观测期）。
    """
    global _unflushed, _loaded
    with _gate_stats_lock:
        _gate_stats["total"] = 0
        _gate_stats["decisions"] = {"allow": 0, "deny": 0, "ask_user": 0}
        _gate_stats["rules"] = {}
        _unflushed = 0
        # 归零后不再合并旧盘数据，否则 reset 立刻被历史值污染
        _loaded = True
        if clear_persisted:
            try:
                _stats_path().unlink()
            except FileNotFoundError:
                pass
            except Exception as exc:
                logger.debug("gate stats unlink failed: %s", exc)


class TrustGate:
    """执行前权限闸门。"""

    @staticmethod
    def default_for_mechanism(mechanism: str) -> PermissionSpec:
        """按 operation_mechanism 给默认信任分级（§15.3）。"""
        if mechanism == "cli_native":
            # 低权信任：可透传执行 CLI、可在工作目录读写文件，但无网络、无需显式授权
            return PermissionSpec(
                reads_fs=True,
                writes_fs=True,
                network=False,
                exec_external=True,
                sandbox=SANDBOX_NONE,
                requires_explicit_consent=False,
            )
        # sdk_bridge / gui_automation / official_api 等：默认需显式授权
        return PermissionSpec(
            reads_fs=False,
            writes_fs=False,
            network=False,
            exec_external=False,
            sandbox=SANDBOX_NONE,
            requires_explicit_consent=True,
        )

    @staticmethod
    def check(spec: Optional[PermissionSpec], ctx: Optional[dict] = None) -> GateResult:
        """执行前判定：ALLOW / DENY / ASK_USER。

        默认 deny-unless-declared：spec 为 None（未声明）直接 DENY。
        每次判定都经 record_gate_hit 累计命中率（fail-open 观测数据）。
        """
        if spec is None:
            res = GateResult(DENY, "未声明 PermissionSpec（deny-unless-declared）")
            res.rule = "undeclared_deny"
            record_gate_hit(DENY, "undeclared_deny")
            return res

        if spec.requires_explicit_consent:
            res = GateResult(ASK_USER, "requires_explicit_consent=true，需用户显式授权", spec)
            res.rule = "consent_required"
            record_gate_hit(ASK_USER, "consent_required")
            return res

        # 网络访问必须落在沙箱内，否则拒绝（防止静默外联）
        if spec.network and spec.sandbox == SANDBOX_NONE:
            res = GateResult(DENY, "network=true 但 sandbox=none，拒绝未沙箱化的外联", spec)
            res.rule = "network_no_sandbox"
            record_gate_hit(DENY, "network_no_sandbox")
            return res

        res = GateResult(ALLOW, permission=spec)
        res.rule = "default_allow"
        record_gate_hit(ALLOW, "default_allow")
        return res
