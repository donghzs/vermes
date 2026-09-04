"""⑭ Bot 实验室：本地 agent 发现 + 注册表 agent 类型 单测。

纪律（见 vermes 审计记忆）：
- pytest 必须串行：``-p no:xdist -o addopts=""``，否则 xdist scheduler 崩溃。
- 必须 ``--basetemp=/tmp/xxx``：沙箱 sitecustomize 拦截 mkdir，tmp_path 建不了默认 tmp 目录。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# 从 __file__ 上溯到 repo 根，确保 vermes_cli 可导入（直跑 __main__ 也成立）
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "fakehome"
    home.mkdir()
    # 配置目录：.claude 存在（map 到 app 类）
    (home / ".claude").mkdir()
    # MCP 配置：声明一个带 env 的 server（auth=apikey）
    mcp = home / ".vermes" / "mcp.json"
    mcp.parent.mkdir(parents=True, exist_ok=True)
    mcp.write_text(
        json.dumps({"mcpServers": {"my-server": {"command": "my-mcp", "env": {"KEY": "1"}}}}),
        encoding="utf-8",
    )
    return home


def test_scanner_cli_and_config_and_mcp(fake_home, monkeypatch):
    from vermes_cli.adapters.agent_discovery import LocalAgentScanner

    # 只让 'claude' 出现在 PATH
    monkeypatch.setattr("shutil.which", lambda b: "/usr/bin/claude" if b == "claude" else None)
    scanner = LocalAgentScanner(home=fake_home)
    found = {a.id: a for a in scanner.scan()}

    assert "agent:claude" in found
    assert found["agent:claude"].kind == "cli"
    assert "agent:cfg_.claude" in found
    assert found["agent:cfg_.claude"].kind == "app"
    assert any(a.id.startswith("agent:mcp_") for a in found.values())
    assert found["agent:mcp_my-server"].auth_scheme == "apikey"


def test_scanner_fail_open_on_which_raise(fake_home, monkeypatch):
    from vermes_cli.adapters.agent_discovery import LocalAgentScanner

    def boom(_b):
        raise RuntimeError("disk error")

    monkeypatch.setattr("shutil.which", boom)
    scanner = LocalAgentScanner(home=fake_home)
    # 即便 which 抛异常，配置目录 / MCP 仍应被发现，且不抛
    found = {a.id: a for a in scanner.scan()}
    assert "agent:cfg_.claude" in found
    assert any(a.id.startswith("agent:mcp_") for a in found.values())


def test_registry_discovers_agent_type(fake_home, monkeypatch):
    from vermes_cli.capabilities.registry import BRICK_TYPES, BrickRegistry

    assert "agent" in BRICK_TYPES

    monkeypatch.setattr("shutil.which", lambda b: "/usr/bin/claude" if b == "claude" else None)
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))

    reg = BrickRegistry(bricks_json=fake_home / "bricks.json")
    entries = reg.discover(refresh=True)
    agents = [e for e in entries if e.type == "agent"]
    assert any(e.id == "agent:claude" for e in agents)
    assert any(e.agent_kind == "cli" for e in agents)
    # agent 条目归一字段正确
    claude = next(e for e in agents if e.id == "agent:claude")
    assert claude.install_state == "installed"
    assert claude.auth_scheme == "local"


def test_discover_agents_fail_open(monkeypatch, tmp_path):
    """agent 发现异常时不影响其他 brick 类型（discover 仍返回 list，不抛）。"""
    import vermes_cli.adapters.agent_discovery as ad

    class Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("x")

        def scan(self):
            raise RuntimeError("x")

    monkeypatch.setattr(ad, "LocalAgentScanner", Boom)
    from vermes_cli.capabilities.registry import BrickRegistry

    reg = BrickRegistry(bricks_json=tmp_path / "bricks.json")
    entries = reg.discover(refresh=True)
    assert isinstance(entries, list)


def test_remote_catalog_skeleton_fail_open():
    """远端 catalog 骨架默认索引为空 → 不贡献条目；解析失败 fail-open。"""
    from vermes_cli.adapters.agent_catalog import (
        AGENT_CATALOG_INDEX, AgentCatalogEntry, AgentCatalogIndex, AgentCatalogSource,
    )
    # 默认模块级索引为空
    assert AGENT_CATALOG_INDEX.all_entries() == []
    # 一个 malformed source（构造一个会抛的 list_entries）
    src = AgentCatalogSource(url="http://127.0.0.1:0/never")  # 不可达 → fail-open
    assert src.list_entries() == []
    # 正常解析
    good = AgentCatalogEntry(id="agent:foo", name="Foo", kind="remote", popularity=42)
    idx = AgentCatalogIndex()
    idx.add_source(_StubSource([good]))
    assert idx.all_entries()[0].popularity == 42


def test_remote_catalog_parse_dirty_popularity_fail_open():
    """P2 健壮性守护：脏 popularity（"oops"/{}/12.5/None/""）不应让 _parse 整批崩溃。

    审计背景（2026-09-04 董董反馈）：原 _parse 用 int(...) 强转，一条数据脏 → ValueError 抛到
    list comprehension 外 → list_entries 整批崩 → 触发条件为将来真实 community 源上线 +
    add_source(AgentCatalogSource(url)) 后，远端返回脏 popularity。对比 recommend.py 逐条
    try 内字段 .strip()，无 int() 强转，脏条目不崩——本测试守住"逐条 fail-open 归零"。
    """
    from vermes_cli.adapters.agent_catalog import AgentCatalogSource

    src = AgentCatalogSource.__new__(AgentCatalogSource)
    src.url = "x"
    src.name = "remote-agent-catalog"

    dirty = [
        {"id": "agent:a", "name": "A", "popularity": "oops"},   # str 非数字 → ValueError → 归零
        {"id": "agent:b", "name": "B", "popularity": 42},         # int 正常
        {"id": "agent:c", "name": "C", "popularity": {}},         # dict → TypeError → 归零
        {"id": "agent:d", "name": "D", "popularity": None},       # None → 归零
        {"id": "agent:e", "name": "E", "popularity": "123"},      # 数字字符串 → 解析
        {"id": "agent:f", "name": "F", "popularity": 12.5},      # float → int() 截断
        {"id": "agent:g", "name": "G", "popularity": ""},         # 空字符串 → 归零
    ]
    # 关键断言：单条脏数据不得让整批列表推导崩溃
    entries = [src._parse(i) for i in dirty]
    assert len(entries) == 7

    p = {e.id: e.popularity for e in entries}
    assert p["agent:a"] == 0    # "oops" → ValueError 归零
    assert p["agent:b"] == 42   # int 原值
    assert p["agent:c"] == 0    # dict → TypeError 归零
    assert p["agent:d"] == 0    # None → 归零
    assert p["agent:e"] == 123  # 数字字符串解析
    assert p["agent:f"] == 12   # float 截断
    assert p["agent:g"] == 0    # 空字符串归零


class _StubSource:
    def __init__(self, entries):
        self._entries = entries

    def list_entries(self):
        return self._entries


if __name__ == "__main__":
    import os
    import pytest as _pytest

    sys.exit(_pytest.main([__file__, "-p", "no:xdist", "-o", "addopts=",
                           f"--basetemp={os.path.join('/tmp', 'vermes_agent_test')}"]))
