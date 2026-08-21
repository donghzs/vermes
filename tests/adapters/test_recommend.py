"""L2c recommend 单元测试：CliAnythingHubSource 解析 + CatalogIndex 聚合 + recommend 倒排/差集/排序。

沙箱可跑：mock cli-hub JSON / 手工构造 CatalogEntry，不依赖真实 cli-hub 网络。
"""

from __future__ import annotations

from vermes_cli.adapters.recommend import (
    CATALOG_INDEX,
    CatalogEntry,
    CatalogIndex,
    CliAnythingHubSource,
    recommend,
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
