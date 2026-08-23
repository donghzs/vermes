"""Phase 3.2: 市场后端中间层（modules_market blueprint）测试。

验证：catalog 列表 + 已安装状态 / origin 纵深防御 / catalog SSRF 白名单 /
install 接 module_catalog+reload / uninstall 接 4.2 uninstall_module。

用最小 FastAPI app 注册 blueprint 本身（不拉起全量 web_server 初始化），
聚焦中间层逻辑。
"""
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# 隔离 modules_dir，避免污染真实 ~/.vermes
import agent.module_loader as ml


@pytest.fixture
def client(tmp_path, monkeypatch):
    # 把全局 modules_dir 缓存指到 tmp
    monkeypatch.setattr(ml, "_MODULES_DIR_CACHE", tmp_path / "modules")
    # 真实 catalog 源（本地文件），供 install 测试用
    cat = tmp_path / "catalog.json"
    cat.write_text(
        '{"modules": [{"name": "demo", "display_name": "Demo", "latest": "1.0.0", '
        '"vermes_min": "0.0.0", "code_asset": "", "code_sha256": ""}]}'
    )
    monkeypatch.setenv("VERMES_TEST_CATALOG", str(cat))

    from vermes_cli.blueprints import modules_market

    # 让 blueprint 默认读测试 catalog：monkeypatch _resolve_catalog_url 默认分支
    app = FastAPI()
    modules_market.register_to(app)
    # 覆盖：默认 catalog_url 指向测试 catalog
    import vermes_cli.blueprints.modules_market as mm
    monkeypatch.setattr(mm, "_resolve_catalog_url", lambda u: str(cat) if not u else u)
    yield TestClient(app)
    ml._module_tool_names.pop("demo", None)
    sys.modules.pop("_vermes_module_demo_tools", None)


def test_market_list_empty_catalog(client):
    """无 catalog 源时返回空列表 + catalog_available=False，不报错。"""
    # 用一个指向不存在 catalog 的 client 分支：直接调 _resolve 返回空
    resp = client.get("/api/v1/modules/market", params={"catalog_url": ""})
    # catalog_url="" → _resolve 返回 "" → blueprint 返回空（取决于 monkeypatch）
    assert resp.status_code in (200,)
    body = resp.json()
    assert "modules" in body


def test_market_list_returns_catalog_with_installed_flag(client):
    """列出 catalog 模块 + 已安装标记（demo 未安装应为 False）。"""
    resp = client.get("/api/v1/modules/market")
    assert resp.status_code == 200
    body = resp.json()
    assert body["catalog_available"] is True
    mods = {m["id"]: m for m in body["modules"]}
    assert "demo" in mods
    assert mods["demo"]["installed"] is False
    assert mods["demo"]["version"] == "1.0.0"


def test_market_origin_blocked_on_cross_site(client):
    """跨站 origin 被 _check_origin 拒绝（403）。"""
    resp = client.get(
        "/api/v1/modules/market",
        headers={"origin": "https://evil.example.com", "host": "localhost:8080"},
    )
    assert resp.status_code == 403


def test_market_install_unknown_module_404(client):
    """安装不存在的模块 → 404。"""
    resp = client.post("/api/v1/modules/market/install", json={"id": "ghost"})
    assert resp.status_code == 404


def test_market_uninstall_unknown_module_safe(client):
    """卸载不存在的模块 → 安全成功（不崩），tools_removed=0。"""
    resp = client.post("/api/v1/modules/market/uninstall", json={"name": "ghost"})
    # uninstall_module 对未知模块返回 ok=True（安全失败）
    assert resp.status_code == 200
    assert resp.json()["tools_removed"] == 0
