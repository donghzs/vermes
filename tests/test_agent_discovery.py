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
    assert found["agent:cfg_.claude"].kind == "config"
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


def test_remote_agent_field_contract():
    """P1 字段契约守护：远端 BrickEntry.to_dict() 字段层级（董董 2026-09-04 审计发现 T4
    前端读错层→热度/主页/仓库 v-if 永不渲染后定档）。

    契约（agent_catalog.py 顶部 docstring 同源）：
    - 顶层  *有*  agent_kind / auth_scheme / source / install_state / name / version / entry_point
    - 顶层  *无*  popularity / homepage / repository（这三个走 extra）
    - extra  *有*  popularity / repository（homepage 在远端被塞进 entry_point，见 _discover_agents:457）

    触发回归场景：
    1. 后端若把 popularity 提到顶层 → 本测试 fail（前端模板写错但未察觉的概率↑）
    2. 后端若从 extra 移除某字段 → 本测试 fail
    """
    import vermes_cli.adapters.agent_catalog as ac
    from vermes_cli.adapters.agent_catalog import AgentCatalogSource
    from vermes_cli.capabilities.registry import BrickRegistry

    # 注入一条干净的远端条目（绕过 HTTP，monkeypatch list_entries 直接返回 _items）
    src = AgentCatalogSource.__new__(AgentCatalogSource)
    src.name = "contract-test"
    src.url = "stub://contract-test"
    src._items = [
        {
            "id": "agent:openclaw", "name": "OpenClaw", "kind": "remote",
            "auth_scheme": "apikey", "description": "社区热门", "version": "2.1",
            "popularity": 1234,
            "homepage": "https://openclaw.dev",
            "repository": "https://github.com/openclaw/openclaw",
        },
    ]
    src.list_entries = lambda: [src._parse(i) for i in src._items]
    try:
        ac.AGENT_CATALOG_INDEX.add_source(src)

        reg = BrickRegistry(bricks_json="/tmp/__contract_bricks.json")
        remote_entries = [e for e in reg._discover_agents(include_remote=True) if e.source == "remote"]
        assert len(remote_entries) == 1, f"远端条目数应为 1，实际 {len(remote_entries)}"
        d = remote_entries[0].to_dict()

        # 顶层必须有的字段
        for k in ("agent_kind", "auth_scheme", "source", "install_state", "name", "version", "entry_point"):
            assert k in d, f"顶层缺字段 {k}（前端依赖）：{d!r}"

        # 顶层必须没有的字段（防后端误把字段提到顶层 + 守前端不再写错）
        for k in ("popularity", "homepage", "repository"):
            assert k not in d, (
                f"顶层不应有 {k}（董董审计 2026-09-04：T4 前端读顶层→v-if 永不渲染）；"
                f"前端应读 a.extra?.{k}。当前 d[{k!r}] = {d.get(k)!r}"
            )

        # extra 必含的字段
        assert isinstance(d.get("extra"), dict), f"extra 应为 dict：{d!r}"
        assert d["extra"].get("popularity") == 1234, f"extra.popularity 应为 1234：{d!r}"
        assert d["extra"].get("repository") == "https://github.com/openclaw/openclaw", (
            f"extra.repository 应为仓库链接：{d!r}"
        )

        # extra 不应含 homepage（董董 2026-09-04 审计发现 T4 字段错位后定档的细化契约）
        assert "homepage" not in d["extra"], (
            f"extra 不应有 homepage（应走 entry_point）；d={d!r}"
        )

        # 顺带守：远端 entry_point 被塞了 homepage（设计选择，audit 2026-09-04 待董董拍板是否清理）
        assert d["entry_point"] == "https://openclaw.dev", (
            f"远端 entry_point 当前=homepage（_discover_agents:457 设计）；d={d!r}"
        )
    finally:
        # 清理全局索引污染，防后续测试看到这条 OpenClaw
        ac.AGENT_CATALOG_INDEX._entries.pop("agent:openclaw", None)


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
