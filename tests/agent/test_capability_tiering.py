"""T2：能力激活的分级判定。

修复前 `capability_activate` 无差别每次弹窗——不管它是「建两张本地空表」
还是「往解释器里 pip install 一个包」。这两件事的可逆性和爆炸半径差了一个
数量级，用同一个等级处理的结果就是用户被问烦了，然后对所有弹窗都点「同意」。

运行：.venv/bin/python -m pytest tests/agent/test_capability_tiering.py -p no:xdist -o addopts="" -q
"""

import pytest

from agent import capability_registry as cr
from agent.capability_registry import (
    Capability,
    CapabilityStatus,
    CapabilityType,
    classify_activation_tier,
)


def _stub_registry(monkeypatch, *caps):
    monkeypatch.setattr(cr, "_CAPABILITIES", list(caps))


def _cap(name, *, built_in, check_ok=True, status=CapabilityStatus.NOT_INSTALLED,
         check_raises=False):
    def _check():
        if check_raises:
            raise RuntimeError("checker exploded")
        return (check_ok, "stub")
    return Capability(
        name=name,
        type=CapabilityType.RETRIEVAL,
        description="stub",
        check_fn=_check,
        built_in=built_in,
        status=status,
    )


# ── 真实注册表：锁住当前三个能力的实际归档 ───────────────────────────

def test_builtin_capabilities_are_l1():
    """纯 Python 内置能力 = L1：只建本地表，且状态根本没持久化。"""
    for name in ("skill_extraction", "graph_sync"):
        t = classify_activation_tier(name)
        assert t["tier"] == "L1", name
        assert t["needs_install"] is False


def test_pip_install_capability_is_l2(monkeypatch):
    """需要 pip install 的能力 = L2：改的是解释器环境，重启不会复位。"""
    _stub_registry(monkeypatch, _cap("vector_retrieval", built_in=False, check_ok=False))
    t = classify_activation_tier("vector_retrieval")
    assert t["tier"] == "L2"
    assert t["needs_install"] is True
    assert "pip install" in t["reason"]


# ── 判定逻辑 ─────────────────────────────────────────────────────────

def test_already_installed_dependency_drops_to_l1(monkeypatch):
    """依赖已经装好了 → 激活只是初始化本地存储，不该再拦。

    这条是「问的次数会随时间下降」的关键：第一次装 chromadb 要确认，
    之后每次激活就不该再问了。
    """
    _stub_registry(monkeypatch, _cap("vector_retrieval", built_in=False, check_ok=True))
    t = classify_activation_tier("vector_retrieval")
    assert t["tier"] == "L1"
    assert t["needs_install"] is False


def test_checker_is_consulted_not_cached_status(monkeypatch):
    """判定问 check_fn，而不是信可能过期的 cached status。

    包可能被别的东西装上了；拿旧 status 判定会白弹一次窗。
    """
    _stub_registry(monkeypatch, _cap(
        "vector_retrieval", built_in=False, check_ok=True,
        status=CapabilityStatus.NOT_INSTALLED,   # 陈旧
    ))
    assert classify_activation_tier("vector_retrieval")["tier"] == "L1"


def test_checker_failure_falls_back_to_status(monkeypatch):
    """check_fn 抛异常 → 退回 cached status，而不是整个判定崩掉。"""
    _stub_registry(monkeypatch, _cap(
        "x", built_in=False, check_raises=True,
        status=CapabilityStatus.INSTALLED,
    ))
    assert classify_activation_tier("x")["tier"] == "L1"

    _stub_registry(monkeypatch, _cap(
        "y", built_in=False, check_raises=True,
        status=CapabilityStatus.NOT_INSTALLED,
    ))
    assert classify_activation_tier("y")["tier"] == "L2"


def test_unknown_capability_is_fail_closed(monkeypatch):
    """不认识的名字 → L2。不认识正是最不该自作主张的时候。"""
    _stub_registry(monkeypatch)
    t = classify_activation_tier("whatever")
    assert t["tier"] == "L2"
    assert "未知能力" in t["reason"]


def test_every_result_is_a_known_tier():
    """判定只会输出 L1/L2，不会漏出 None 或别的字符串。"""
    for name in ("skill_extraction", "graph_sync", "vector_retrieval", "nope"):
        assert classify_activation_tier(name)["tier"] in ("L1", "L2")
