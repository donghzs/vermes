"""L2c recommend 单元测试：CliAnythingHubSource 解析 + CatalogIndex 聚合 + recommend 倒排/差集/排序。

沙箱可跑：mock cli-hub JSON / 手工构造 CatalogEntry，不依赖真实 cli-hub 网络。
"""

from __future__ import annotations

from vermes_cli.adapters.recommend import (
    CATALOG_INDEX,
    CatalogEntry,
    CatalogIndex,
    CliAnythingHubSource,
    InstallResult,
    Recommendation,
    install,
    recommend,
    usage_rank_hook,
)

# 真实 cli-hub list --json 的样例（截取 2 条，含 name ≠ entry_point 前缀的边界 case）
SAMPLE_RAW = [
    {
        "name": "freecad",
        "display_name": "FreeCAD",
        "version": "1.1.3",
        "description": "Parametric 3D CAD modeler",
        "requires": "FreeCAD >= 1.1",
        "homepage": "https://www.freecad.org",
        "source_url": None,
        "install_cmd": "pip install git+https://github.com/HKUDS/CLI-Anything.git#subdirectory=freecad/agent-harness",
        "entry_point": "cli-anything-freecad",
        "skill_md": "skills/cli-anything-freecad/SKILL.md",
        "category": "3d",
        "contributors": [],
        "_source": "harness",
    },
    {
        "name": "cc-switch",
        "display_name": "CC Switch",
        "version": "1.0.0",
        "description": "Manage AI coding tool configurations",
        "requires": "CC Switch installed with active database",
        "homepage": "https://github.com/HKUDS/CLI-Anything",
        "source_url": None,
        "install_cmd": "pip install git+https://github.com/HKUDS/CLI-Anything.git#subdirectory=cc-switch/agent-harness",
        "entry_point": "cli-anything-ccswitch",
        "skill_md": "skills/cli-anything-ccswitch/SKILL.md",
        "category": "devops",
        "contributors": [],
        "_source": "harness",
    },
]


def _index(*entries: CatalogEntry) -> CatalogIndex:
    idx = CatalogIndex()
    for e in entries:
        idx.add_source(_FakeSource(e))
    return idx


class _FakeSource:
    name = "fake"

    def __init__(self, entry: CatalogEntry):
        self.entry = entry

    def list_entries(self):
        return [self.entry]


def _entry(software, domain, keywords, name=None):
    return CatalogEntry(
        name=name or software,
        software=software,
        harness=f"cli-anything-{software}",
        domain=domain,
        description="",
        requires="",
        keywords=keywords,
    )


# ---------------------------------------------------------------------------
# CliAnythingHubSource 解析
# ---------------------------------------------------------------------------

def test_cli_hub_source_parse():
    entries = CliAnythingHubSource._parse(SAMPLE_RAW)
    assert len(entries) == 2
    freecad = entries[0]
    assert freecad.name == "freecad"
    assert freecad.software == "freecad"          # entry_point 去前缀
    assert freecad.harness == "cli-anything-freecad"
    assert freecad.domain == "3d"
    assert freecad.requires == "FreeCAD >= 1.1"
    assert freecad.install_cmd == "cli-hub install freecad"
    # 边界 case：name="cc-switch" 但 entry_point="cli-anything-ccswitch"
    ccswitch = entries[1]
    assert ccswitch.name == "cc-switch"
    assert ccswitch.software == "ccswitch"        # 差集 key = entry_point 去前缀，非 name
    assert ccswitch.install_cmd == "cli-hub install cc-switch"


def test_cli_hub_source_fail_open_when_binary_missing(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda *a, **kw: None)
    src = CliAnythingHubSource(cli_hub_bin="cli-hub")
    assert src.list_entries() == []


def test_cli_hub_source_fail_open_on_bad_json(monkeypatch):
    import subprocess

    monkeypatch.setattr("shutil.which", lambda *a, **kw: "/usr/bin/cli-hub")

    def fake_run(cmd, **kw):
        class _P:
            stdout = "not-json"
            returncode = 0
        return _P()

    monkeypatch.setattr(subprocess, "run", fake_run)
    src = CliAnythingHubSource(cli_hub_bin="cli-hub")
    assert src.list_entries() == []


# ---------------------------------------------------------------------------
# CatalogIndex 聚合去重
# ---------------------------------------------------------------------------

def test_catalog_index_dedup_by_harness():
    idx = CatalogIndex()
    a = _entry("freecad", "3d", ["freecad", "cad"])
    b = _entry("freecad", "3d", ["freecad", "parametric"])  # 同 harness，去重
    idx.add_source(_FakeSource(a))
    idx.add_source(_FakeSource(b))
    assert len(idx.all_entries()) == 1


# ---------------------------------------------------------------------------
# recommend：倒排 + 差集 + 排序 + rank_hook
# ---------------------------------------------------------------------------

def test_recommend_english_inverted_index():
    idx = _index(
        _entry("freecad", "3d", ["freecad", "cad", "part", "fillet"]),
        _entry("blender", "3d", ["blender", "render", "mesh"]),
        _entry("libreoffice", "office", ["libreoffice", "document", "pdf"]),
    )
    recs = recommend("3d modeling with fillet", installed=set(), index=idx)
    softwares = [r.software for r in recs]
    assert "freecad" in softwares
    assert "blender" in softwares
    assert "libreoffice" not in softwares  # office 不命中 3d 意图


def test_recommend_chinese_domain_hint():
    """中文意图「建模」经 DOMAIN_BILINGUAL_HINTS 桥接命中 3d 域。"""
    idx = _index(
        _entry("freecad", "3d", ["freecad", "cad"]),
        _entry("libreoffice", "office", ["libreoffice", "document"]),
    )
    recs = recommend("我要建模一个零件", installed=set(), index=idx)
    softwares = [r.software for r in recs]
    assert "freecad" in softwares
    assert "libreoffice" not in softwares


def test_recommend_diff_excludes_installed():
    idx = _index(
        _entry("freecad", "3d", ["freecad", "cad"]),
        _entry("blender", "3d", ["blender", "render"]),
    )
    recs = recommend("3d modeling", installed={"freecad"}, index=idx)
    softwares = [r.software for r in recs]
    assert "freecad" not in softwares      # 差集：已装不推荐
    assert "blender" in softwares


def test_recommend_rank_hook_reorders():
    idx = _index(
        _entry("freecad", "3d", ["freecad", "cad"]),
        _entry("blender", "3d", ["blender", "render"]),
    )
    # 认知层 hook：强制 blender 排第一
    def hook(entries, ctx):
        return sorted(entries, key=lambda e: 0 if e.software == "blender" else 1)

    recs = recommend("3d modeling", installed=set(), rank_hook=hook, index=idx)
    assert recs[0].software == "blender"


def test_recommend_unrelated_intent_empty():
    idx = _index(_entry("freecad", "3d", ["freecad", "cad"]))
    recs = recommend("translate this document to french", installed=set(), index=idx)
    assert recs == []


def test_recommend_backend_hint_from_requires():
    idx = _index(
        CatalogEntry(
            name="freecad", software="freecad", harness="cli-anything-freecad",
            domain="3d", description="", requires="FreeCAD >= 1.1",
            keywords=["freecad", "cad"], install_cmd="cli-hub install freecad",
        )
    )
    recs = recommend("3d cad modeling", installed=set(), index=idx)
    assert recs[0].backend_hint == "FreeCAD >= 1.1"
    assert recs[0].adapter_install == "cli-hub install freecad"


# ---------------------------------------------------------------------------
# P1: install 两步链路
# ---------------------------------------------------------------------------

def _rec(software="freecad", adapter_install="cli-hub install freecad", backend_hint="FreeCAD >= 1.1"):
    return Recommendation(
        software=software,
        domain="3d",
        reason="命中关键词：cad",
        matched_keywords=["cad"],
        source="cli-anything-hub",
        score=0.7,
        adapter_install=adapter_install,
        backend_hint=backend_hint,
    )


def test_install_adapter_success(monkeypatch):
    """第一步 cli-hub install 成功 → adapter_installed=True。"""
    import subprocess
    monkeypatch.setattr("shutil.which", lambda *a, **kw: "/usr/bin/cli-hub")

    class _OkProc:
        returncode = 0
        stdout = "Installing freecad...\n✓ Installed FreeCAD"
        stderr = ""
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _OkProc())
    monkeypatch.setattr("vermes_cli.adapters.bootstrap.discover_l2_adapters", lambda *a, **kw: {"freecad": 273})
    monkeypatch.setattr("vermes_cli.adapters.discovery.BackendLocator.locate", lambda *a, **kw: type("L", (), {"backend_resolved": None})())

    result = install(_rec(), re_scan=True)
    assert result.adapter_installed is True
    assert result.tools_registered == 273


def test_install_adapter_fail_no_cli_hub(monkeypatch):
    """cli-hub 未装 → 第一步直接失败。"""
    monkeypatch.setattr("shutil.which", lambda *a, **kw: None)
    result = install(_rec())
    assert result.adapter_installed is False
    assert "cli-hub 未安装" in result.adapter_message
    assert result.tools_registered == -1


def test_install_adapter_fail_rc_nonzero(monkeypatch):
    """cli-hub install 返回非零 → 第一步失败，不尝试第二步。"""
    import subprocess
    monkeypatch.setattr("shutil.which", lambda *a, **kw: "/usr/bin/cli-hub")

    class _FailProc:
        returncode = 1
        stdout = ""
        stderr = "Error: package not found"
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _FailProc())

    result = install(_rec())
    assert result.adapter_installed is False
    assert "安装失败" in result.adapter_message


def test_install_backend_check(monkeypatch):
    """第二步 BackendLocator 检查本体就绪状态。"""
    import subprocess
    monkeypatch.setattr("shutil.which", lambda *a, **kw: "/usr/bin/cli-hub")

    class _OkProc:
        returncode = 0
        stdout = "✓ Installed"
        stderr = ""
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _OkProc())
    # 本体已就绪
    monkeypatch.setattr("vermes_cli.adapters.discovery.BackendLocator.locate", lambda self, s: type("L", (), {"backend_resolved": "/usr/bin/freecadcmd"})())
    monkeypatch.setattr("vermes_cli.adapters.bootstrap.discover_l2_adapters", lambda *a, **kw: {"freecad": 273})

    result = install(_rec())
    assert result.adapter_installed is True
    assert result.backend_ready is True
    assert result.tools_registered == 273


def test_install_re_scan_disabled(monkeypatch):
    """re_scan=False → 不触发 bootstrap 重扫。"""
    import subprocess
    monkeypatch.setattr("shutil.which", lambda *a, **kw: "/usr/bin/cli-hub")

    class _OkProc:
        returncode = 0
        stdout = "✓ Installed"
        stderr = ""
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _OkProc())
    monkeypatch.setattr("vermes_cli.adapters.discovery.BackendLocator.locate", lambda self, s: type("L", (), {"backend_resolved": None})())

    result = install(_rec(), re_scan=False)
    assert result.adapter_installed is True
    assert result.tools_registered == -1  # 未触发重扫


# ---------------------------------------------------------------------------
# P2: usage_rank_hook 认知层信号
# ---------------------------------------------------------------------------

def test_usage_rank_hook_boosts_high_usage(monkeypatch):
    """使用频率高的 software 排名提升（即使关键词得分较低）。"""
    entries = [
        _entry("blender", "3d", ["blender", "render"]),    # 高 usage
        _entry("freecad", "3d", ["freecad", "cad"]),        # 低 usage
    ]
    # 模拟 memory_fabric：freecad 100 次、blender 5 次
    fake_usage = [
        {"kind": "adapter", "id": "freecad", "count": 100, "last_used": "2026-08-20"},
        {"kind": "adapter", "id": "blender", "count": 5, "last_used": "2026-08-01"},
    ]
    monkeypatch.setattr("agent.memory_fabric.get_usage_counts", lambda *a, **kw: fake_usage)

    result = usage_rank_hook(entries, {"intent": "3d modeling"})
    # freecad usage 远高于 blender → 应排第一
    assert result[0].software == "freecad"


def test_usage_rank_hook_no_usage_data(monkeypatch):
    """memory_fabric 无数据时降级到纯关键词排序（原序不变）。"""
    entries = [
        _entry("freecad", "3d", ["freecad", "cad"]),
        _entry("blender", "3d", ["blender", "render"]),
    ]
    monkeypatch.setattr("agent.memory_fabric.get_usage_counts", lambda *a, **kw: [])
    result = usage_rank_hook(entries, {"intent": "3d modeling"})
    assert [e.software for e in result] == ["freecad", "blender"]  # 原序不变


def test_usage_rank_hook_import_fails(monkeypatch):
    """memory_fabric 导入失败时降级（不抛异常）。"""
    entries = [_entry("freecad", "3d", ["freecad"])]
    # 让 import 失败
    import sys
    orig = sys.modules.get("agent.memory_fabric")
    sys.modules["agent.memory_fabric"] = None  # 触发 ImportError
    try:
        result = usage_rank_hook(entries, {"intent": "3d"})
        assert result == entries  # 降级原序
    finally:
        if orig is not None:
            sys.modules["agent.memory_fabric"] = orig
        else:
            sys.modules.pop("agent.memory_fabric", None)


def test_recommend_with_usage_rank_hook(monkeypatch):
    """recommend() 接入 usage_rank_hook 后端到端验证。"""
    idx = _index(
        _entry("freecad", "3d", ["freecad", "cad", "fillet"]),
        _entry("blender", "3d", ["blender", "render", "mesh"]),
    )
    # blender 关键词得分更高（3 命中 vs 3 命中，但 blender 有 render 更贴）
    # 但 usage 数据：freecad 调用 50 次，blender 2 次
    fake_usage = [
        {"kind": "adapter", "id": "freecad", "count": 50, "last_used": "2026-08-20"},
        {"kind": "adapter", "id": "blender", "count": 2, "last_used": "2026-08-01"},
    ]
    monkeypatch.setattr("agent.memory_fabric.get_usage_counts", lambda *a, **kw: fake_usage)

    recs = recommend(
        "3d modeling render",
        installed=set(),
        rank_hook=usage_rank_hook,
        index=idx,
    )
    assert len(recs) >= 2
    # freecad 因高 usage 应排在前面（60% 权重）
    assert recs[0].software == "freecad"
