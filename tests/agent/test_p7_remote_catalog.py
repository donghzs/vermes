"""P7: 远程优先 catalog 发现链（远程官方 → bundled → 用户缓存 → 空）。

验证 load_catalog()（不传路径）走「远程优先」发现链；显式传路径直接加载、不走发现链。
用 file:// 模拟远程 Release；用不可达 https URL 模拟远程失败触发降级。
"""
import json
from pathlib import Path

import pytest

import agent.module_catalog as mc


@pytest.fixture
def reset_cache():
    """每个测试清掉进程内 TTL 缓存，避免串扰。"""
    mc._catalog_cache = None
    mc._catalog_cache_ts = 0.0
    yield
    mc._catalog_cache = None
    mc._catalog_cache_ts = 0.0


def test_remote_priority_returns_remote(tmp_path, monkeypatch, reset_cache):
    """远程可达时，load_catalog() 返回远程内容（file:// 模拟 Release）。"""
    remote = tmp_path / "remote_catalog.json"
    remote.write_text(json.dumps({"generated_at": "x", "modules": [{"name": "remote_mod"}]}))
    monkeypatch.setattr(mc, "default_catalog_url", lambda: remote.as_uri())
    monkeypatch.setattr(mc, "default_catalog_path", lambda: tmp_path / "cache.json")
    data = mc.load_catalog()
    assert any(m["name"] == "remote_mod" for m in data["modules"])


def test_fallback_to_bundled_when_remote_fails(tmp_path, monkeypatch, reset_cache):
    """远程失败 → 回退 bundled catalog。"""
    bundled = tmp_path / "bundled.json"
    bundled.write_text(json.dumps({"modules": [{"name": "bundled_mod"}]}))
    monkeypatch.setattr(mc, "default_catalog_url", lambda: "https://127.0.0.1:9/nope")
    monkeypatch.setattr(mc, "bundled_catalog_path", lambda: bundled)
    monkeypatch.setattr(mc, "default_catalog_path", lambda: tmp_path / "missing_cache.json")
    data = mc.load_catalog()
    assert any(m["name"] == "bundled_mod" for m in data["modules"])


def test_fallback_to_user_cache(tmp_path, monkeypatch, reset_cache):
    """远程 + bundled 均失败 → 回退用户缓存。"""
    cache = tmp_path / "user_cache.json"
    cache.write_text(json.dumps({"modules": [{"name": "cache_mod"}]}))
    monkeypatch.setattr(mc, "default_catalog_url", lambda: "https://127.0.0.1:9/nope")
    monkeypatch.setattr(mc, "bundled_catalog_path", lambda: tmp_path / "missing_bundled.json")
    monkeypatch.setattr(mc, "default_catalog_path", lambda: cache)
    data = mc.load_catalog()
    assert any(m["name"] == "cache_mod" for m in data["modules"])


def test_all_sources_fail_returns_empty(tmp_path, monkeypatch, reset_cache):
    """全失败 → 空目录（fail-open，绝不抛异常）。"""
    monkeypatch.setattr(mc, "default_catalog_url", lambda: "https://127.0.0.1:9/nope")
    monkeypatch.setattr(mc, "bundled_catalog_path", lambda: tmp_path / "missing_bundled.json")
    monkeypatch.setattr(mc, "default_catalog_path", lambda: tmp_path / "missing_cache.json")
    data = mc.load_catalog()
    assert data == {"modules": [], "generated_at": None}


def test_explicit_path_bypasses_discovery(tmp_path, monkeypatch, reset_cache):
    """显式传路径时不走发现链（测试 / 本地覆盖用），直接加载该来源。"""
    cat = tmp_path / "explicit.json"
    cat.write_text(json.dumps({"modules": [{"name": "explicit_mod"}]}))
    # 即使远程/bundled 都坏，显式路径仍直接加载
    monkeypatch.setattr(mc, "default_catalog_url", lambda: "https://127.0.0.1:9/nope")
    monkeypatch.setattr(mc, "bundled_catalog_path", lambda: tmp_path / "missing_bundled.json")
    data = mc.load_catalog(str(cat))
    assert any(m["name"] == "explicit_mod" for m in data["modules"])


def test_ttl_cache_avoids_refetch(tmp_path, monkeypatch, reset_cache):
    """进程内 TTL 缓存命中时直接返回，不再二次拉取（用计数器验证）。"""
    remote = tmp_path / "remote_catalog.json"
    remote.write_text(json.dumps({"modules": [{"name": "remote_mod"}]}))
    fetches = {"n": 0}

    def _fake_url():
        fetches["n"] += 1
        return remote.as_uri()

    monkeypatch.setattr(mc, "default_catalog_url", _fake_url)
    monkeypatch.setattr(mc, "default_catalog_path", lambda: tmp_path / "cache.json")

    assert any(m["name"] == "remote_mod" for m in mc.load_catalog()["modules"])
    # 第二次调用应在 TTL 内命中缓存，default_catalog_url 不再被调用
    assert any(m["name"] == "remote_mod" for m in mc.load_catalog()["modules"])
    assert fetches["n"] == 1
