"""
agent/capability_registry.py — 能力注册表与涌现式自升级框架

Vermes 的记忆与进化能力不是硬编码决定的，而是从用户实际使用中涌现。

三层架构：
  1. 能力注册表（本文件）— 声明"系统能做什么"，每个能力有检查器、安装器、激活器
  2. 涌现决策器（capability_evolver.py）— 从 self_assessment 信号 + 簇数据涌现"需要什么"
  3. 自安装机制 — Agent 自己 pip install + 动态 import + domain_modules 热插拔

能力类型：
  - retrieval: 向量检索（当关键词检索命中率不足时涌现）
  - skill_extract: 技能自动提取（当簇模式高度重复时涌现）
  - graph_sync: 知识图谱导入导出（当多设备/多 Agent 需求涌现时）
  - 未来新增能力只需在 CAPABILITIES 注册，无需修改框架代码

设计原则：
  - 能力声明是静态的（代码定义），但启用/禁用是涌现的（数据驱动）
  - 安装是可选的（有些能力需要 pip install，有些纯 Python 已内置）
  - 激活是无侵入的（通过 domain_modules 热插拔，不改主链路）
  - 失败是安全的（装不上就用不了，不影响现有功能）
"""

from __future__ import annotations

import importlib
import logging
import subprocess
import sys
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("vermes.capability")


# ── Enums ────────────────────────────────────────────────────────────────────

class CapabilityStatus(Enum):
    """Lifecycle status of a capability."""
    NOT_INSTALLED = "not_installed"   # 依赖未安装
    INSTALLED = "installed"           # 依赖已装，未激活
    ACTIVE = "active"                 # 已激活，正在使用
    FAILED = "failed"                 # 安装/激活失败
    BUILT_IN = "built_in"             # 纯 Python，无需安装


class CapabilityType(Enum):
    """What kind of capability this is."""
    RETRIEVAL = "retrieval"           # 记忆检索增强
    SKILL = "skill"                   # 技能提取/管理
    GRAPH = "graph"                   # 知识图谱
    EVOLUTION = "evolution"           # 进化系统增强


# ── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class Capability:
    """A declarative capability definition.

    Capabilities are registered statically (code), but activated
    dynamically (data-driven). The emergence decider determines
    when to activate each capability based on user behavior signals.
    """
    name: str                                   # 唯一标识符
    type: CapabilityType
    description: str                            # 人类可读描述

    # 依赖检查：返回 (installed, detail)
    check_fn: Callable[[], Tuple[bool, str]]

    # 安装函数：返回 (success, detail)
    install_fn: Optional[Callable[[], Tuple[bool, str]]] = None

    # 激活函数：返回 (success, detail)
    activate_fn: Optional[Callable[[], Tuple[bool, str]]] = None

    # 纯 Python 无需安装
    built_in: bool = False

    # 当前状态（运行时）
    status: CapabilityStatus = CapabilityStatus.NOT_INSTALLED
    last_check: str = ""
    last_error: str = ""
    activated_at: str = ""

    # 涌现信号计数器（由 capability_evolver 更新）
    emergence_signals: int = 0


@dataclass
class CapabilityReport:
    """Snapshot of all capabilities and their statuses."""
    capabilities: List[Capability] = field(default_factory=list)

    def get_by_name(self, name: str) -> Optional[Capability]:
        for cap in self.capabilities:
            if cap.name == name:
                return cap
        return None

    def get_active(self) -> List[Capability]:
        return [c for c in self.capabilities if c.status == CapabilityStatus.ACTIVE]

    def get_pending(self) -> List[Capability]:
        """Capabilities with emergence signals but not yet active."""
        return [
            c for c in self.capabilities
            if c.status in (CapabilityStatus.NOT_INSTALLED, CapabilityStatus.INSTALLED)
            and c.emergence_signals > 0
        ]

    def summary(self) -> str:
        """One-line summary for logging."""
        parts = []
        for c in self.capabilities:
            parts.append(f"{c.name}:{c.status.value}")
        return " | ".join(parts)


# ── Capability Checkers ──────────────────────────────────────────────────────

def _check_chromadb() -> Tuple[bool, str]:
    """Check if chromadb is importable."""
    try:
        importlib.import_module("chromadb")
        return True, "chromadb available"
    except ImportError:
        return False, "chromadb not installed"


def _check_skill_extract() -> Tuple[bool, str]:
    """Skill extraction is pure Python, always available."""
    return True, "built-in (pure Python)"


def _check_graph_sync() -> Tuple[bool, str]:
    """Graph sync is pure Python, always available."""
    return True, "built-in (pure Python)"


# ── Capability Installers ───────────────────────────────────────────────────

def _install_chromadb() -> Tuple[bool, str]:
    """Install chromadb via pip."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "chromadb", "--quiet"],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode == 0:
            return True, "chromadb installed"
        return False, f"pip failed: {result.stderr[:200]}"
    except subprocess.TimeoutExpired:
        return False, "pip install timed out (120s)"
    except Exception as e:
        return False, f"install error: {e}"


# ── Capability Activators ───────────────────────────────────────────────────

def _activate_vector_retrieval() -> Tuple[bool, str]:
    """Activate vector retrieval by ensuring Chroma collection exists.

    Creates a persistent Chroma collection at ~/.vermes/chroma/
    that hybrid_retriever can use for semantic search.
    """
    try:
        import os
        import chromadb
        from pathlib import Path

        chroma_path = Path(
            os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))
        ) / "chroma"
        chroma_path.mkdir(parents=True, exist_ok=True)

        client = chromadb.PersistentClient(path=str(chroma_path))
        # get_or_create so we don't fail if it exists
        collection = client.get_or_create_collection(
            name="vermes_memory",
            metadata={"description": "Vermes emergent memory store"}
        )
        return True, f"chroma collection ready ({collection.count()} docs)"
    except Exception as e:
        return False, f"activate error: {e}"


def _activate_skill_extract() -> Tuple[bool, str]:
    """Activate skill extraction by initializing the skill store."""
    try:
        from agent.skill_extractor import ensure_skill_tables
        import os
        from pathlib import Path

        db_path = Path(
            os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))
        ) / "evolution" / "self-model.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)

        import sqlite3
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        ensure_skill_tables(conn)
        conn.close()
        return True, "skill tables ready"
    except Exception as e:
        return False, f"activate error: {e}"


def _activate_graph_sync() -> Tuple[bool, str]:
    """Activate graph sync by ensuring export tables exist."""
    try:
        from agent.graph_sync import ensure_graph_tables
        import os
        from pathlib import Path

        db_path = Path(
            os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))
        ) / "evolution" / "self-model.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)

        import sqlite3
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        ensure_graph_tables(conn)
        conn.close()
        return True, "graph tables ready"
    except Exception as e:
        return False, f"activate error: {e}"


# ── Registry ────────────────────────────────────────────────────────────────

# 全局能力注册表
_CAPABILITIES: List[Capability] = [
    Capability(
        name="vector_retrieval",
        type=CapabilityType.RETRIEVAL,
        description="向量语义检索 — 当关键词检索命中率不足时涌现",
        check_fn=_check_chromadb,
        install_fn=_install_chromadb,
        activate_fn=_activate_vector_retrieval,
        built_in=False,
    ),
    Capability(
        name="skill_extraction",
        type=CapabilityType.SKILL,
        description="技能自动提取 — 当簇模式高度重复时涌现",
        check_fn=_check_skill_extract,
        activate_fn=_activate_skill_extract,
        built_in=True,
    ),
    Capability(
        name="graph_sync",
        type=CapabilityType.GRAPH,
        description="知识图谱导入导出 — 当多设备/多 Agent 需求涌现时",
        check_fn=_check_graph_sync,
        activate_fn=_activate_graph_sync,
        built_in=True,
    ),
]


def get_capabilities() -> List[Capability]:
    """Return all registered capabilities."""
    return _CAPABILITIES


def get_capability(name: str) -> Optional[Capability]:
    """Get a capability by name."""
    for cap in _CAPABILITIES:
        if cap.name == name:
            return cap
    return None


def check_all_capabilities() -> CapabilityReport:
    """Check the status of all capabilities.

    Runs check_fn for each capability and updates status.
    Does NOT install or activate anything — pure observation.
    """
    from datetime import datetime

    report = CapabilityReport()
    for cap in _CAPABILITIES:
        try:
            installed, detail = cap.check_fn()
            cap.last_check = datetime.now().isoformat()
            if cap.built_in:
                cap.status = CapabilityStatus.BUILT_IN
            elif installed:
                cap.status = CapabilityStatus.INSTALLED
            else:
                cap.status = CapabilityStatus.NOT_INSTALLED
            cap.last_error = "" if installed else detail
        except Exception as e:
            cap.status = CapabilityStatus.FAILED
            cap.last_error = str(e)
            cap.last_check = datetime.now().isoformat()

        report.capabilities.append(cap)

    logger.info("Capability check: %s", report.summary())
    return report


def install_capability(name: str) -> Tuple[bool, str]:
    """Install a capability's dependencies.

    Called by the emergence decider when signals indicate the capability
    is needed. Safe to call multiple times.
    """
    cap = get_capability(name)
    if not cap:
        return False, f"unknown capability: {name}"

    if cap.built_in:
        cap.status = CapabilityStatus.BUILT_IN
        return True, "built-in, no install needed"

    if cap.status in (CapabilityStatus.INSTALLED, CapabilityStatus.ACTIVE):
        return True, "already installed"

    if not cap.install_fn:
        return False, "no installer defined"

    success, detail = cap.install_fn()
    if success:
        cap.status = CapabilityStatus.INSTALLED
        logger.info("Capability '%s' installed: %s", name, detail)
    else:
        cap.status = CapabilityStatus.FAILED
        cap.last_error = detail
        logger.warning("Capability '%s' install failed: %s", name, detail)

    return success, detail


def activate_capability(name: str) -> Tuple[bool, str]:
    """Activate a capability.

    Called by the emergence decider after installation.
    Runs the activate_fn to set up collections/tables/etc.
    """
    cap = get_capability(name)
    if not cap:
        return False, f"unknown capability: {name}"

    if cap.status == CapabilityStatus.ACTIVE:
        return True, "already active"

    # If not installed, try installing first
    if cap.status == CapabilityStatus.NOT_INSTALLED:
        ok, detail = install_capability(name)
        if not ok:
            return False, f"install failed: {detail}"

    if not cap.activate_fn:
        cap.status = CapabilityStatus.ACTIVE
        return True, "no activation needed"

    success, detail = cap.activate_fn()
    if success:
        cap.status = CapabilityStatus.ACTIVE
        from datetime import datetime
        cap.activated_at = datetime.now().isoformat()
        logger.info("Capability '%s' activated: %s", name, detail)
    else:
        cap.status = CapabilityStatus.FAILED
        cap.last_error = detail
        logger.warning("Capability '%s' activate failed: %s", name, detail)

    return success, detail


def get_capability_report_prompt() -> str:
    """Generate a <capability_status> block for system prompt.

    Lets the Agent know what capabilities are available and their status,
    so it can self-assess and potentially request upgrades.
    """
    report = check_all_capabilities()
    if not report.capabilities:
        return ""

    lines = ["<capability_status>"]
    for cap in report.capabilities:
        icon = {
            CapabilityStatus.ACTIVE: "✅",
            CapabilityStatus.INSTALLED: "📦",
            CapabilityStatus.NOT_INSTALLED: "⬜",
            CapabilityStatus.FAILED: "❌",
            CapabilityStatus.BUILT_IN: "🔧",
        }.get(cap.status, "?")
        lines.append(f"  {icon} {cap.name}: {cap.status.value}")
        if cap.emergence_signals > 0:
            lines.append(f"     emergence signals: {cap.emergence_signals}")
    lines.append("</capability_status>")

    return "\n".join(lines)
