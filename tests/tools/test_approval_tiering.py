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
    # 批准是有记忆的（TTL / session / permanent grant）——那正是生产要的行为，
    # 但测试之间必须互不影响，否则前一条用例的批准会让后一条"没弹窗"。
    ap.clear_privileged_grants()
    with ap._lock:
        ap._session_approved.pop("sk", None)
        _stale = {k for k in ap._permanent_approved
                  if k.startswith(ap._PRIVILEGED_GRANT_PREFIX)}
        ap._permanent_approved.difference_update(_stale)
    yield
    ap.clear_privileged_grants()


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


# ── 「非必要不弹」：批准是有记忆的 ────────────────────────────────────
# T1 要求源码级改写必须人工确认，但不等于每次都问。批准一次即授予一个作用域
# 通行证：默认 TTL 30 分钟，选"本次会话"到会话结束，选"始终"则永久。

def _src(path="/repo/agent/foo.py", **kw):
    d = {"target_path": path, "pattern_key": "self_modify"}
    d.update(kw)
    return d


def test_approval_is_remembered_within_ttl(monkeypatch):
    """一次任务里连续改 3 个文件 → 只弹 1 次。这是重复弹窗的最大来源。"""
    _stub_cfg(monkeypatch)
    monkeypatch.setattr(ap, "is_session_yolo_enabled", lambda sk: True)
    calls = []
    _stub_gateway(monkeypatch, calls)

    for p in ("/repo/a.py", "/repo/b.py", "/repo/c.py"):
        assert ap.approve_privileged_action("sk", _src(p)) is True
    assert len(calls) == 1


def test_ttl_zero_disables_reuse(monkeypatch):
    """0 = 每次都弹。这个方向是收紧，所以按写的值原样尊重（不套 P1 的 >0 护栏）。"""
    _stub_cfg(monkeypatch, privileged_grant_ttl_minutes=0)
    calls = []
    _stub_gateway(monkeypatch, calls)

    ap.approve_privileged_action("sk", _src("/repo/a.py"))
    ap.approve_privileged_action("sk", _src("/repo/b.py"))
    assert len(calls) == 2


def test_expired_grant_asks_again(monkeypatch):
    _stub_cfg(monkeypatch, privileged_grant_ttl_minutes=30)
    calls = []
    _stub_gateway(monkeypatch, calls)

    ap.approve_privileged_action("sk", _src("/repo/a.py"))
    assert len(calls) == 1
    # 把通行证的到期时间拨到过去
    with ap._lock:
        for k in ap._privileged_grants:
            ap._privileged_grants[k] = 0.0
    ap.approve_privileged_action("sk", _src("/repo/b.py"))
    assert len(calls) == 2


def test_session_choice_outlives_ttl(monkeypatch):
    """选「本次会话都允许」→ 即便 TTL 被设成 0 也不再问。"""
    calls = []
    _stub_gateway(monkeypatch, calls, choice="session")
    _stub_cfg(monkeypatch, privileged_grant_ttl_minutes=0)

    ap.approve_privileged_action("sk", _src("/repo/a.py"))
    assert len(calls) == 1
    ap.approve_privileged_action("sk", _src("/repo/b.py"))
    assert len(calls) == 1
    assert "privileged:self_modify" in ap._session_approved.get("sk", set())


def test_always_choice_persists(monkeypatch):
    saved = []
    monkeypatch.setattr(ap, "save_permanent_allowlist", lambda pats: saved.append(set(pats)))
    calls = []
    _stub_gateway(monkeypatch, calls, choice="always")
    _stub_cfg(monkeypatch, privileged_grant_ttl_minutes=0)

    ap.approve_privileged_action("sk", _src("/repo/a.py"))
    assert len(calls) == 1
    assert "privileged:self_modify" in ap._permanent_approved
    assert saved and "privileged:self_modify" in saved[0]
    # 永久授权跨会话生效
    ap.clear_session("sk")
    ap.approve_privileged_action("other-session", _src("/repo/b.py"))
    assert len(calls) == 1


def test_grant_does_not_leak_across_action_categories(monkeypatch):
    """批准「改源码」不等于批准「回滚/删文件」——两类风险各自授权。"""
    _stub_cfg(monkeypatch)
    calls = []
    _stub_gateway(monkeypatch, calls)

    ap.approve_privileged_action("sk", _src("/repo/a.py"))
    assert len(calls) == 1
    ap.approve_privileged_action(
        "sk", {"target_path": "/repo/a.py", "pattern_key": "self_modify_rollback"})
    assert len(calls) == 2


def test_denial_grants_nothing(monkeypatch):
    _stub_cfg(monkeypatch)
    calls = []
    _stub_gateway(monkeypatch, calls, choice="deny")

    assert ap.approve_privileged_action("sk", _src("/repo/a.py")) is False
    assert ap.approve_privileged_action("sk", _src("/repo/b.py")) is False
    assert len(calls) == 2


def test_timeout_grants_nothing(monkeypatch):
    _stub_cfg(monkeypatch)
    calls = []

    def _timeout(session_key, approval_data, *, surface="gateway"):
        calls.append(approval_data)
        return {"resolved": False, "choice": None}
    monkeypatch.setattr(ap, "request_gateway_approval", _timeout)

    assert ap.approve_privileged_action("sk", _src("/repo/a.py")) is False
    assert ap.approve_privileged_action("sk", _src("/repo/b.py")) is False
    assert len(calls) == 2


def test_new_session_asks_again(monkeypatch):
    """TTL 通行证是会话内的：clear_session 之后新会话重新确认。"""
    _stub_cfg(monkeypatch)
    calls = []
    _stub_gateway(monkeypatch, calls)

    ap.approve_privileged_action("sk", _src("/repo/a.py"))
    assert len(calls) == 1
    ap.clear_session("sk")
    ap.approve_privileged_action("sk", _src("/repo/b.py"))
    assert len(calls) == 2


def test_dialog_offers_remember_scopes(monkeypatch):
    """弹窗要把「本次会话 / 始终」交给前端，默认推荐「本次会话」。"""
    _stub_cfg(monkeypatch)
    calls = []
    _stub_gateway(monkeypatch, calls)

    ap.approve_privileged_action("sk", _src("/repo/a.py"))
    assert calls[0]["scope_options"] == ["once", "session", "always"]
    assert calls[0]["default_choice"] == "session"
    assert calls[0]["scope_key"] == "privileged:self_modify"


def test_caller_approval_data_not_mutated(monkeypatch):
    """加注 tier / scope_options 不能污染调用方自己的 dict。"""
    _stub_cfg(monkeypatch)
    monkeypatch.setattr(ap, "is_session_yolo_enabled", lambda sk: True)
    _stub_gateway(monkeypatch, [])

    original = _src("/repo/a.py", description="d")
    snapshot = dict(original)
    ap.approve_privileged_action("sk", original)
    assert original == snapshot


def test_grant_ttl_read_failure_is_fail_safe(monkeypatch):
    """读不到配置 → 不发通行证，下次照常弹（宁可多问一次，不可少拦一次）。

    TTL 在*授予*时读配置决定，不在命中时读——所以 fail-safe 表现为
    「批准了但没记住」，而不是「记住了却不敢用」。
    """
    calls = []
    _stub_gateway(monkeypatch, calls)

    def _boom():
        raise RuntimeError("config unreadable")
    monkeypatch.setattr(ap, "_get_approval_config", _boom)
    monkeypatch.setattr(ap, "_get_approval_mode", lambda: "manual")

    ap.approve_privileged_action("sk", _src("/repo/a.py"))
    assert len(calls) == 1
    assert ap._privileged_grants == {}          # 没发出通行证
    ap.approve_privileged_action("sk", _src("/repo/b.py"))
    assert len(calls) == 2


# ── T2：能力激活分级（非文件类特权动作不再被误判成源码改写）─────────

def _cap(**kw):
    """能力激活的 approval_data：有类别、没有 target_path。"""
    d = {"type": "capability_activate",
         "category": "capability_activate",
         "pattern_key": "capability_activate"}
    d.update(kw)
    return d


def test_capability_activate_is_yolo_exempt(monkeypatch):
    """开着 YOLO 时，能力激活不该被「源码改写不豁免」规则误伤。

    T1 那条规则的对象是*改文件*。能力激活没有 target_path，若照
    `is_config_level_target("") is False` 直接推成源码级，就会在 YOLO
    下照弹不误 —— 而 YOLO 本来就已经代表「我信你执行命令」，pip install
    正是命令。
    """
    _stub_cfg(monkeypatch)
    monkeypatch.setattr(ap, "is_session_yolo_enabled", lambda sk: True)
    calls = []
    _stub_gateway(monkeypatch, calls)

    assert ap.approve_privileged_action("sk", _cap()) is True
    assert calls == []                          # 没弹


def test_unlabelled_action_still_prompts_under_yolo(monkeypatch):
    """没写类别 = 按 self_modify 处理 —— 保留原来的 fail-safe。

    这条锁住上面那个豁免不会被写成「只要没 target_path 就放行」：
    缺省类别仍落回 self_modify 家族，照弹。
    """
    _stub_cfg(monkeypatch)
    monkeypatch.setattr(ap, "is_session_yolo_enabled", lambda sk: True)
    calls = []
    _stub_gateway(monkeypatch, calls)

    ap.approve_privileged_action("sk", {"description": "来路不明的特权动作"})
    assert len(calls) == 1
    assert calls[0]["tier"] == "L2"


def test_capability_activate_still_prompts_without_yolo(monkeypatch):
    """非 YOLO 下 L2 能力激活照常要人确认（豁免只针对 YOLO）。"""
    _stub_cfg(monkeypatch)
    calls = []
    _stub_gateway(monkeypatch, calls)

    assert ap.approve_privileged_action("sk", _cap()) is True
    assert len(calls) == 1


def test_capability_approval_is_remembered(monkeypatch):
    """批准一次 → 同类别后续激活不再弹（T1b 通行证覆盖 T2）。

    修复前每个能力、每个涌现周期都要弹一次，这正是「安全的老在问」。
    """
    _stub_cfg(monkeypatch)
    calls = []
    _stub_gateway(monkeypatch, calls)

    assert ap.approve_privileged_action("sk", _cap(capability="a")) is True
    assert ap.approve_privileged_action("sk", _cap(capability="b")) is True
    assert len(calls) == 1


def test_capability_grant_does_not_leak_into_source_modify(monkeypatch):
    """批准能力激活 ≠ 批准改源码 —— 授权按类别隔离。"""
    _stub_cfg(monkeypatch)
    calls = []
    _stub_gateway(monkeypatch, calls)

    ap.approve_privileged_action("sk", _cap())
    assert len(calls) == 1
    ap.approve_privileged_action("sk", _src("/repo/agent/foo.py"))
    assert len(calls) == 2                      # 又弹了一次，没被串用
