"""审批分层 T1：源码级改写不再被 YOLO 豁免（config 级仍豁免）。

背景：桌面端会话默认开 YOLO（chat.py 的 `yolo_default`），而
`approve_privileged_action` 见 YOLO 就 `return True` —— 结果最高风险的
self_modify 源码改写零弹窗静默放行，中等风险的能力激活反而每次必弹。
本测试锁住修复后的分层语义。

运行：.venv/bin/python -m pytest tests/tools/test_approval_tiering.py -p no:xdist -o addopts="" -q
"""

import pytest

from tools import approval as ap


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("VERMES_YOLO_MODE", raising=False)
    monkeypatch.setattr(ap, "is_session_yolo_enabled", lambda sk: False)
    yield


def _stub_gateway(monkeypatch, calls, choice="approve"):
    def _fake(session_key, approval_data, *, surface="gateway"):
        calls.append(approval_data)
        return {"resolved": True, "choice": choice}
    monkeypatch.setattr(ap, "request_gateway_approval", _fake)


def _stub_cfg(monkeypatch, **kw):
    base = {"mode": "manual"}
    base.update(kw)
    monkeypatch.setattr(ap, "_get_approval_config", lambda: base)


def _stub_config_path(monkeypatch, path):
    import vermes_cli.config as vc
    monkeypatch.setattr(vc, "get_config_path", lambda: path)


# ── is_config_level_target ────────────────────────────────────────────

def test_config_level_detection(monkeypatch, tmp_path):
    cfg = tmp_path / "config.yaml"
    _stub_config_path(monkeypatch, str(cfg))

    assert ap.is_config_level_target(str(cfg)) is True
    assert ap.is_config_level_target("/anywhere/settings.yml") is True
    assert ap.is_config_level_target("/anywhere/thing.json") is True
    # 源码级
    assert ap.is_config_level_target("/repo/agent/memory_reflection.py") is False
    assert ap.is_config_level_target("/repo/scripts/run.sh") is False
    # 空路径按源码级（最保守）
    assert ap.is_config_level_target("") is False


# ── YOLO 下的分层行为 ────────────────────────────────────────────────

def test_config_target_still_yolo_exempt(monkeypatch, tmp_path):
    """config 级改动是可逆的（.bak + 面板撤回）→ YOLO 仍然直接放行。"""
    cfg = tmp_path / "config.yaml"
    _stub_config_path(monkeypatch, str(cfg))
    _stub_cfg(monkeypatch)
    monkeypatch.setattr(ap, "is_session_yolo_enabled", lambda sk: True)
    calls = []
    _stub_gateway(monkeypatch, calls)

    ok = ap.approve_privileged_action("sk", {"target_path": str(cfg)})
    assert ok is True
    assert calls == []          # 没弹窗


def test_source_target_not_yolo_exempt(monkeypatch):
    """源码级改写即便开着 YOLO 也必须走人工确认。"""
    _stub_cfg(monkeypatch)
    monkeypatch.setattr(ap, "is_session_yolo_enabled", lambda sk: True)
    calls = []
    _stub_gateway(monkeypatch, calls)

    ok = ap.approve_privileged_action(
        "sk", {"target_path": "/repo/agent/foo.py", "description": "改点东西"})
    assert ok is True
    assert len(calls) == 1                      # 真的弹了
    assert calls[0]["tier"] == "L2"
    assert calls[0]["yolo_exempt"] is False
    assert "源码级改写" in calls[0]["description"]
    assert "改点东西" in calls[0]["description"]   # 原描述保留


def test_source_target_denied_under_yolo(monkeypatch):
    """弹窗被拒 → 不放行（而不是因为 YOLO 就过）。"""
    _stub_cfg(monkeypatch)
    monkeypatch.setattr(ap, "is_session_yolo_enabled", lambda sk: True)
    calls = []
    _stub_gateway(monkeypatch, calls, choice="deny")

    assert ap.approve_privileged_action("sk", {"target_path": "/repo/a.py"}) is False


def test_env_yolo_also_blocked(monkeypatch):
    _stub_cfg(monkeypatch)
    monkeypatch.setenv("VERMES_YOLO_MODE", "1")
    calls = []
    _stub_gateway(monkeypatch, calls)

    ap.approve_privileged_action("sk", {"target_path": "/repo/a.py"})
    assert len(calls) == 1


def test_mode_off_also_blocked_for_source(monkeypatch):
    """approvals.mode=off 同样不豁免源码改写（唯一开关是下面那个键）。"""
    _stub_cfg(monkeypatch, mode="off")
    calls = []
    _stub_gateway(monkeypatch, calls)

    ap.approve_privileged_action("sk", {"target_path": "/repo/a.py"})
    assert len(calls) == 1


def test_opt_out_key_restores_bypass(monkeypatch):
    """显式关掉 source_modify_always_confirm → 回到旧的 YOLO 全过语义。"""
    _stub_cfg(monkeypatch, source_modify_always_confirm=False)
    monkeypatch.setattr(ap, "is_session_yolo_enabled", lambda sk: True)
    calls = []
    _stub_gateway(monkeypatch, calls)

    assert ap.approve_privileged_action("sk", {"target_path": "/repo/a.py"}) is True
    assert calls == []


def test_config_read_failure_fails_safe(monkeypatch):
    """读配置炸了 → 按"要确认"处理，绝不静默放行源码改写。"""
    def _boom():
        raise RuntimeError("config unreadable")
    monkeypatch.setattr(ap, "_get_approval_config", _boom)
    monkeypatch.setattr(ap, "is_session_yolo_enabled", lambda sk: True)
    monkeypatch.setattr(ap, "_get_approval_mode", lambda: "manual")
    calls = []
    _stub_gateway(monkeypatch, calls)

    ap.approve_privileged_action("sk", {"target_path": "/repo/a.py"})
    assert len(calls) == 1


# ── 非 YOLO 路径不受影响 ─────────────────────────────────────────────

def test_non_yolo_path_unchanged(monkeypatch, tmp_path):
    cfg = tmp_path / "config.yaml"
    _stub_config_path(monkeypatch, str(cfg))
    _stub_cfg(monkeypatch)
    calls = []
    _stub_gateway(monkeypatch, calls)

    ok = ap.approve_privileged_action("sk", {"target_path": str(cfg)})
    assert ok is True
    assert len(calls) == 1
    assert "tier" not in calls[0]   # 未被 L2 加注


def test_no_session_key_fails_closed(monkeypatch):
    """无 gateway 会话 → deny（fail-closed），源码改写不会因无人可问而自动过。"""
    _stub_cfg(monkeypatch)
    monkeypatch.setattr(ap, "is_session_yolo_enabled", lambda sk: True)
    assert ap.approve_privileged_action("", {"target_path": "/repo/a.py"}) is False
