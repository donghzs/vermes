"""切片③ 项目管理 + 模板测试。"""
import json
import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def tmp_mfg_home(tmp_path, monkeypatch):
    """临时 mfgcad 目录。"""
    monkeypatch.setattr(
        "vermes_cli.mfgcad.projects._mfg_home",
        lambda: tmp_path / "mfgcad",
    )
    return tmp_path / "mfgcad"


class TestProjects:
    """项目 CRUD。"""

    def test_create_project(self, tmp_mfg_home):
        from vermes_cli.mfgcad.projects import create_project
        p = create_project("测试项目", template="injection_mold", notes="备注")
        assert p["id"] == 1
        assert p["title"] == "测试项目"
        assert p["template"] == "injection_mold"
        assert p["notes"] == "备注"
        assert p["session_ids"] == []

    def test_create_multiple_projects(self, tmp_mfg_home):
        from vermes_cli.mfgcad.projects import create_project
        p1 = create_project("项目1")
        p2 = create_project("项目2")
        assert p1["id"] == 1
        assert p2["id"] == 2

    def test_list_projects(self, tmp_mfg_home):
        from vermes_cli.mfgcad.projects import create_project, list_projects
        create_project("A")
        create_project("B")
        projects = list_projects()
        assert len(projects) == 2
        assert projects[0]["title"] == "A"
        assert projects[1]["title"] == "B"

    def test_get_project(self, tmp_mfg_home):
        from vermes_cli.mfgcad.projects import create_project, get_project
        p = create_project("测试")
        got = get_project(p["id"])
        assert got is not None
        assert got["title"] == "测试"

    def test_get_project_not_found(self, tmp_mfg_home):
        from vermes_cli.mfgcad.projects import get_project
        assert get_project(999) is None

    def test_update_project(self, tmp_mfg_home):
        from vermes_cli.mfgcad.projects import create_project, update_project
        p = create_project("原名")
        updated = update_project(p["id"], title="新名", notes="新备注")
        assert updated["title"] == "新名"
        assert updated["notes"] == "新备注"

    def test_update_project_not_found(self, tmp_mfg_home):
        from vermes_cli.mfgcad.projects import update_project
        assert update_project(999, title="x") is None

    def test_delete_project(self, tmp_mfg_home):
        from vermes_cli.mfgcad.projects import create_project, delete_project, list_projects
        p = create_project("待删除")
        assert delete_project(p["id"]) is True
        assert len(list_projects()) == 0

    def test_delete_project_not_found(self, tmp_mfg_home):
        from vermes_cli.mfgcad.projects import delete_project
        assert delete_project(999) is False

    def test_link_session(self, tmp_mfg_home):
        from vermes_cli.mfgcad.projects import create_project, link_session, get_project
        p = create_project("测试")
        assert link_session(p["id"], "sess_001") is True
        got = get_project(p["id"])
        assert "sess_001" in got["session_ids"]

    def test_link_session_idempotent(self, tmp_mfg_home):
        from vermes_cli.mfgcad.projects import create_project, link_session, get_project
        p = create_project("测试")
        link_session(p["id"], "sess_001")
        link_session(p["id"], "sess_001")
        got = get_project(p["id"])
        assert got["session_ids"].count("sess_001") == 1

    def test_unlink_session(self, tmp_mfg_home):
        from vermes_cli.mfgcad.projects import create_project, link_session, unlink_session, get_project
        p = create_project("测试")
        link_session(p["id"], "sess_001")
        assert unlink_session(p["id"], "sess_001") is True
        got = get_project(p["id"])
        assert "sess_001" not in got["session_ids"]

    def test_link_project_not_found(self, tmp_mfg_home):
        from vermes_cli.mfgcad.projects import link_session
        assert link_session(999, "sess") is False


class TestTemplates:
    """内置模板。"""

    def test_list_templates_has_5(self):
        from vermes_cli.mfgcad.projects import list_templates
        t = list_templates()
        assert len(t) == 5
        assert "injection_mold" in t
        assert "3d_print" in t
        assert "mechanical_part" in t
        assert "ecommerce_display" in t
        assert "film_prop" in t

    def test_get_template_injection_mold(self):
        from vermes_cli.mfgcad.projects import get_template
        t = get_template("injection_mold")
        assert t is not None
        assert "收缩率" in t["description"]
        assert "shrinkage_compensation" in t["default_params"]
        assert t["preset"] == "mechanical_part"

    def test_get_template_3d_print(self):
        from vermes_cli.mfgcad.projects import get_template
        t = get_template("3d_print")
        assert t is not None
        assert "FDM" in t["description"]
        assert t["preset"] == "print_part"

    def test_get_template_ecommerce(self):
        from vermes_cli.mfgcad.projects import get_template
        t = get_template("ecommerce_display")
        assert t is not None
        assert t["preset"] == "ecommerce_display"
        assert t["default_params"]["engine"] == "trellis"

    def test_get_template_not_found(self):
        from vermes_cli.mfgcad.projects import get_template
        assert get_template("nonexistent") is None

    def test_all_templates_have_suggested_request(self):
        from vermes_cli.mfgcad.projects import list_templates
        for key, t in list_templates().items():
            assert "suggested_request" in t, f"{key} missing suggested_request"


class TestHandlers:
    """tools.py handler 集成。"""

    @pytest.mark.asyncio
    async def test_handler_create_project(self, tmp_mfg_home, monkeypatch):
        from vermes_cli.mfgcad.tools import _handle_mfg_project
        result = await _handle_mfg_project({"action": "create", "title": "测试项目"})
        assert "✅" in result
        assert "测试项目" in result

    @pytest.mark.asyncio
    async def test_handler_create_with_template(self, tmp_mfg_home, monkeypatch):
        from vermes_cli.mfgcad.tools import _handle_mfg_project
        result = await _handle_mfg_project({
            "action": "create", "title": "注塑件项目", "template": "injection_mold"
        })
        assert "✅" in result
        assert "注塑件" in result

    @pytest.mark.asyncio
    async def test_handler_create_missing_title(self, tmp_mfg_home):
        from vermes_cli.mfgcad.tools import _handle_mfg_project
        result = await _handle_mfg_project({"action": "create"})
        assert "需要 title" in result

    @pytest.mark.asyncio
    async def test_handler_list_empty(self, tmp_mfg_home):
        from vermes_cli.mfgcad.tools import _handle_mfg_project
        result = await _handle_mfg_project({"action": "list"})
        assert "暂无" in result

    @pytest.mark.asyncio
    async def test_handler_list_with_data(self, tmp_mfg_home):
        from vermes_cli.mfgcad.tools import _handle_mfg_project
        await _handle_mfg_project({"action": "create", "title": "A"})
        await _handle_mfg_project({"action": "create", "title": "B"})
        result = await _handle_mfg_project({"action": "list"})
        assert "A" in result
        assert "B" in result

    @pytest.mark.asyncio
    async def test_handler_get_not_found(self, tmp_mfg_home):
        from vermes_cli.mfgcad.tools import _handle_mfg_project
        result = await _handle_mfg_project({"action": "get", "project_id": 999})
        assert "不存在" in result

    @pytest.mark.asyncio
    async def test_handler_link_unlink(self, tmp_mfg_home):
        from vermes_cli.mfgcad.tools import _handle_mfg_project
        await _handle_mfg_project({"action": "create", "title": "测试"})
        link_result = await _handle_mfg_project({
            "action": "link", "project_id": 1, "session_id": "sess_001"
        })
        assert "✅" in link_result
        unlink_result = await _handle_mfg_project({
            "action": "unlink", "project_id": 1, "session_id": "sess_001"
        })
        assert "✅" in unlink_result

    @pytest.mark.asyncio
    async def test_handler_template_list(self):
        from vermes_cli.mfgcad.tools import _handle_mfg_template
        result = await _handle_mfg_template({"action": "list"})
        assert "injection_mold" in result
        assert "3d_print" in result
        assert "注塑件" in result

    @pytest.mark.asyncio
    async def test_handler_template_get(self):
        from vermes_cli.mfgcad.tools import _handle_mfg_template
        result = await _handle_mfg_template({"action": "get", "template": "injection_mold"})
        assert "注塑件" in result
        assert "收缩率" in result

    @pytest.mark.asyncio
    async def test_handler_template_not_found(self):
        from vermes_cli.mfgcad.tools import _handle_mfg_template
        result = await _handle_mfg_template({"action": "get", "template": "xxx"})
        assert "不存在" in result


class TestToolsetConsistency:
    """工具注册一致性。"""

    def test_mfg_project_in_toolsets(self):
        import toolsets
        assert "mfg_project" in toolsets.TOOLSETS["mfgcad"]["tools"]

    def test_mfg_template_in_toolsets(self):
        import toolsets
        assert "mfg_template" in toolsets.TOOLSETS["mfgcad"]["tools"]

    def test_toolset_count_17(self):
        import toolsets
        tools = toolsets.TOOLSETS["mfgcad"]["tools"]
        assert len(tools) == 17, f"Expected 12, got {len(tools)}: {tools}"
