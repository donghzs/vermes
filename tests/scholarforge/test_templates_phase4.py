"""ScholarForge Phase 4 — 模板导入导出测试

验证预设模板列出、详情、从模板创建项目、从项目导出模板。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """临时 ScholarForge DB。"""
    db_path = str(tmp_path / "test_templates.db")
    import hermes_cli.scholarforge.database as db
    monkeypatch.setattr(db, "DB_PATH", db_path)
    db.init_db()
    yield db


class TestBuiltinTemplates:
    def test_list_builtin_templates(self):
        """列出预设模板。"""
        from hermes_cli.scholarforge.project_templates import list_builtin_templates
        templates = list_builtin_templates()
        assert len(templates) == 3
        keys = [t["key"] for t in templates]
        assert "cs_undergraduate" in keys
        assert "business_master" in keys
        assert "edu_review" in keys

    def test_get_builtin_template(self):
        """获取模板详情。"""
        from hermes_cli.scholarforge.project_templates import get_builtin_template
        t = get_builtin_template("cs_undergraduate")
        assert t is not None
        assert t["name"] == "计算机本科毕设"
        assert len(t["outline"]) == 9  # 9 chapters
        assert t["paper_type"] == "本科论文"

    def test_get_nonexistent_template(self):
        """获取不存在的模板返回 None。"""
        from hermes_cli.scholarforge.project_templates import get_builtin_template
        assert get_builtin_template("nonexistent") is None


class TestCreateFromTemplate:
    def test_create_from_cs_template(self, tmp_db):
        """从计算机本科模板创建项目。"""
        from hermes_cli.scholarforge.project_templates import (
            get_builtin_template,
            create_project_from_template,
        )
        t = get_builtin_template("cs_undergraduate")
        result = create_project_from_template(t, "基于深度学习的图像分类系统")
        assert "error" not in result
        assert result["title"] == "基于深度学习的图像分类系统"
        assert result["paper_type"] == "本科论文"
        # 大纲应有 9 章节
        outline = result.get("outline", [])
        assert len(outline) == 9

    def test_create_from_business_template(self, tmp_db):
        """从工商管理模板创建项目。"""
        from hermes_cli.scholarforge.project_templates import (
            get_builtin_template,
            create_project_from_template,
        )
        t = get_builtin_template("business_master")
        result = create_project_from_template(t, "中小企业数字化转型研究")
        assert "error" not in result
        assert result["paper_type"] == "硕士论文"
        assert len(result.get("outline", [])) == 10

    def test_create_from_edu_review_template(self, tmp_db):
        """从教育学综述模板创建项目。"""
        from hermes_cli.scholarforge.project_templates import (
            get_builtin_template,
            create_project_from_template,
        )
        t = get_builtin_template("edu_review")
        result = create_project_from_template(t, "在线教育研究综述")
        assert "error" not in result
        assert result["paper_type"] == "综述论文"
        assert len(result.get("outline", [])) == 9


class TestExportTemplate:
    def test_export_project_as_template(self, tmp_db):
        """从已有项目导出模板。"""
        proj = tmp_db.create_project(
            title="原项目",
            paper_type="本科论文",
            target_words=10000,
        )
        pid = proj["id"]

        from hermes_cli.scholarforge.project_templates import export_project_as_template
        template = export_project_as_template(pid)
        assert "error" not in template
        assert "原项目" in template["name"]
        assert template["paper_type"] == "本科论文"
        assert len(template["outline"]) > 0
        assert template.get("source_project_id") == pid

    def test_export_nonexistent_project(self, tmp_db):
        """导出不存在的项目返回错误。"""
        from hermes_cli.scholarforge.project_templates import export_project_as_template
        template = export_project_as_template(99999)
        assert "error" in template
