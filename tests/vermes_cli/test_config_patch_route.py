"""PATCH /api/config —— 补上「局部改一个开关」的写入路径。

背景：Settings.vue 的 YOLO 开关一直在发 PATCH，而路由只注册了 GET / PUT，
于是每次点击都 405 → 被 try/catch 吞成 console.error → 只写进了
localStorage，配置文件里什么都没变。用户以为关掉了 YOLO，实际没关。

PUT 又不能用：它是整份覆盖，发 `{"approvals": {...}}` 会把其余配置全抹掉。

运行：.venv/bin/python -m pytest tests/vermes_cli/test_config_patch_route.py \
        -p no:xdist -o addopts="" -q
"""

import asyncio

import pytest
import yaml

from fastapi import HTTPException

import vermes_cli.blueprints.config as bp


# ── 纯函数：深合并 ────────────────────────────────────────────────────

def test_deep_merge_only_touches_given_keys():
    base = {"approvals": {"mode": "manual", "timeout": 60}, "model": "gpt"}
    out = bp._deep_merge(base, {"approvals": {"timeout": 5}})
    assert out == {"approvals": {"mode": "manual", "timeout": 5}, "model": "gpt"}


def test_deep_merge_creates_missing_branches():
    assert bp._deep_merge({}, {"a": {"b": {"c": 1}}}) == {"a": {"b": {"c": 1}}}


def test_deep_merge_does_not_mutate_inputs():
    base = {"a": {"b": 1}}
    patch = {"a": {"c": 2}}
    bp._deep_merge(base, patch)
    assert base == {"a": {"b": 1}}
    assert patch == {"a": {"c": 2}}


def test_deep_merge_replaces_lists_wholesale():
    """列表是「换成这个」，不是「往里追加」—— 否则 allowlist 只能进不能出。"""
    out = bp._deep_merge({"command_allowlist": ["rm", "dd"]},
                         {"command_allowlist": ["ls"]})
    assert out["command_allowlist"] == ["ls"]


def test_deep_merge_scalar_over_dict_replaces():
    assert bp._deep_merge({"a": {"b": 1}}, {"a": 3}) == {"a": 3}


# ── 路由行为 ──────────────────────────────────────────────────────────

@pytest.fixture
def _cfg(tmp_path, monkeypatch):
    """把配置读写重定向到临时文件，别碰用户真实的 ~/.vermes/config.yaml。"""
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({
        "model": "deepseek-chat",
        "approvals": {"mode": "manual", "timeout": 60, "yolo_default": True},
    }, allow_unicode=True), encoding="utf-8")

    import vermes_cli.config as vc

    def _read_raw():
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    def _save(cfg):
        path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")

    monkeypatch.setattr(vc, "read_raw_config", _read_raw)
    monkeypatch.setattr(bp, "save_config", _save)
    return path, _read_raw


def _patch(body):
    return asyncio.run(bp.patch_config(body))


def test_patch_flips_one_key_and_keeps_the_rest(_cfg):
    path, read = _cfg
    assert _patch({"approvals": {"yolo_default": False}}) == {"ok": True}

    cfg = read()
    assert cfg["approvals"]["yolo_default"] is False
    assert cfg["approvals"]["mode"] == "manual"     # 兄弟键还在
    assert cfg["approvals"]["timeout"] == 60
    assert cfg["model"] == "deepseek-chat"          # 其它顶层段也还在


def test_patch_writes_tier_mode(_cfg):
    """T6 的旋钮要能从 UI 存下来，否则档位只能靠手改 yaml。"""
    path, read = _cfg
    _patch({"approvals": {"tier_mode": "conservative"}})
    assert read()["approvals"]["tier_mode"] == "conservative"
    assert read()["approvals"]["yolo_default"] is True   # 没动的不动


def test_patch_rejects_empty_body(_cfg):
    for bad in ({}, None, []):
        with pytest.raises(HTTPException) as e:
            _patch(bad)
        assert e.value.status_code == 400


def test_patch_does_not_freeze_defaults_into_user_file(_cfg):
    """合并基准是**原始文件**，不是 load_config() 的合并结果。

    否则一次 PATCH 就把当天的全套默认值固化进用户配置，之后升级改默认值
    对这个用户永远不生效 —— 那是一种很难被发现的「配置腐化」。
    """
    path, read = _cfg
    _patch({"approvals": {"tier_mode": "autonomous"}})
    cfg = read()
    assert set(cfg.keys()) == {"model", "approvals"}
    assert "goals" not in cfg and "hooks" not in cfg


def test_patch_route_is_registered():
    """真正的回归点：路由表里必须有 PATCH，不然前端照样 405。"""
    routes = []

    class _App:
        def add_api_route(self, path, fn, methods=None, **kw):
            routes.append((path, tuple(methods or ())))

    bp.register_to(_App())
    assert ("/api/config", ("PATCH",)) in routes
