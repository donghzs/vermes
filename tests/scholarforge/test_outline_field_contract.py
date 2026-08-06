"""save_outline 字段契约测试 —— 照**真实调用方的形状**写，不是照实现写。

背景（P0-B 审计发现的回归）：
    `project_context.save_outline` 委托给 `database.save_outline` 时丢了归一化层。
    两者服务两套字段契约：
      - camelCase  {id, number, title, wordCount}   ← BUILTIN_TEMPLATES / blueprint 前端 JSON
      - snake_case {section_key, section_number, word_count} ← tools.py:1600 agent 工具
    委托后 agent 路径落库的 section_key 从 `section_1` 变成 `sec_0`、word_count 从 500 变成 0，
    导致 outlines 与 section_contents 的 key 成孤儿。

    当时的 18 个测试全绿却没拦住 —— 因为它们用的是**实现方的形状** `{"id": ...}`，
    而不是**真实调用方的形状**。测试镜像了实现，于是只能证明「代码按自己的想法运行」。

本文件的纪律：每个用例的输入形状必须能在生产代码里指到出处（注释标注行号），
断言落库的**实际值**，而不是「有几行」这种弱断言。
"""
import sqlite3

import pytest


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    import vermes_cli.scholarforge.database as db
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "outline_contract.db"))
    db.init_db()
    yield db


@pytest.fixture
def pid(tmp_db):
    return tmp_db.create_project("契约测试论文", "本科论文")["id"]


def _raw_outline(db, project_id):
    """绕过 get_outline 的 camelCase 出口，直查原始列。"""
    con = sqlite3.connect(db.DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT section_key, section_number, section_title, word_count "
            "FROM outlines WHERE project_id=? ORDER BY sort_order",
            (project_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


class TestAgentToolShape:
    """输入形状出处：vermes_cli/scholarforge/tools.py:1600（scholarforge_outline handler）。"""

    def test_snake_case_keys_persist_verbatim(self, tmp_db, pid):
        from vermes_cli.scholarforge.project_context import save_outline

        sections = [
            {"section_key": "section_1", "title": "引言", "word_count": 500, "status": "pending"},
            {"section_key": "section_2", "title": "方法", "word_count": 800, "status": "pending"},
        ]
        assert save_outline(pid, sections) is True

        rows = _raw_outline(tmp_db, pid)
        assert [r["section_key"] for r in rows] == ["section_1", "section_2"], (
            "agent 工具的 section_key 必须原样落库；退化成 sec_0/sec_1 会让 "
            "outlines 与 section_contents 成孤儿"
        )
        assert [r["word_count"] for r in rows] == [500, 800]
        assert [r["section_title"] for r in rows] == ["引言", "方法"]

    def test_section_key_joins_with_section_contents(self, tmp_db, pid):
        """回归的真实后果：key 对不上时 UPDATE outlines SET word_count 永不命中。"""
        from vermes_cli.scholarforge.project_context import save_outline, save_section

        save_outline(pid, [
            {"section_key": "section_1", "title": "引言", "word_count": 500, "status": "pending"},
        ])
        content = "这是引言正文。" * 10
        assert save_section(pid, "section_1", content) is True

        rows = _raw_outline(tmp_db, pid)
        assert rows[0]["word_count"] == len(content), (
            "写正文后大纲的 word_count 应被同步更新；未更新说明 section_key 没对上"
        )


class TestTemplateAndFrontendShape:
    """输入形状出处：project_templates.py:33 BUILTIN_TEMPLATES / blueprint.py:1064,1203。"""

    def test_camel_case_keys_still_work(self, tmp_db, pid):
        from vermes_cli.scholarforge.database import save_outline

        sections = [
            {"id": "intro", "number": "1", "title": "绪论", "wordCount": 1000},
            {"id": "refs", "number": "", "title": "参考文献", "wordCount": 0},
        ]
        assert save_outline(pid, sections) is True

        rows = _raw_outline(tmp_db, pid)
        assert [r["section_key"] for r in rows] == ["intro", "refs"]
        assert [r["word_count"] for r in rows] == [1000, 0]
        assert rows[1]["section_number"] == "", "模板里的空 number 应原样保留，不被 fallback 成序号"

    def test_builtin_template_persists_intact(self, tmp_db, pid):
        """直接喂真实模板数据，避免测试里手搓的形状与生产漂移。"""
        from vermes_cli.scholarforge.database import save_outline
        from vermes_cli.scholarforge.project_templates import BUILTIN_TEMPLATES

        outline = BUILTIN_TEMPLATES["cs_undergraduate"]["outline"]
        assert save_outline(pid, outline) is True

        rows = _raw_outline(tmp_db, pid)
        assert [r["section_key"] for r in rows] == [s["id"] for s in outline]
        assert [r["word_count"] for r in rows] == [s["wordCount"] for s in outline]

    def test_string_word_count_not_false_negative(self, tmp_db, pid):
        """前端/LLM 的 JSON 可能给字符串 "500"，SQLite 会转成 int 500。

        回读若做裸 `!=` 比对会把这种合法写入误判为失败（假阴性）。
        """
        from vermes_cli.scholarforge.database import save_outline

        assert save_outline(pid, [{"id": "intro", "title": "绪论", "wordCount": "500"}]) is True
        assert _raw_outline(tmp_db, pid)[0]["word_count"] == 500


class TestReadbackCatchesItself:
    """回读必须能抓住写入未生效 —— 原来只查 COUNT(*)>0，把自己引入的回归认证成了成功。

    ⚠️ 诚实边界：回读比对的是「本次打算写的值」vs「库里的值」，两者都来自
    `_norm_outline_section`。若**归一化逻辑本身**写错，expected 会跟着一起错，
    回读比对仍然相等 —— 抓不住。这正是上面那些契约测试不可替代的原因：
    它们从**调用方视角**断言字面实际值，不依赖实现自己的 expected。
    验证器能证明「我写的落库了」，不能证明「我该写的就是这个」。
    """

    def test_readback_detects_row_count_mismatch(self, tmp_db, pid, monkeypatch):
        """条目数不符必须判失败（旧实现只要 COUNT>0 就返回 True）。"""
        import vermes_cli.scholarforge.database as db

        original_get_conn = db.get_conn
        state = {"writes": 0}

        class _PartialConn:
            """让第二条 INSERT 静默丢失，模拟部分写入。"""

            def __init__(self, conn):
                self._conn = conn

            def execute(self, sql, params=()):
                if sql.strip().startswith("INSERT INTO outlines"):
                    state["writes"] += 1
                    if state["writes"] > 1:
                        return None
                return self._conn.execute(sql, params)

            def __getattr__(self, name):
                return getattr(self._conn, name)

        import contextlib

        @contextlib.contextmanager
        def patched():
            with original_get_conn() as conn:
                yield _PartialConn(conn)

        monkeypatch.setattr(db, "get_conn", patched)
        ok = db.save_outline(pid, [
            {"section_key": "section_1", "title": "引言", "word_count": 100},
            {"section_key": "section_2", "title": "方法", "word_count": 200},
        ])
        assert ok is False, "只写进 1 条却期望 2 条，回读必须判失败"


class TestSectionContentExactMatch:
    """save_section_content 的回读要做全等比对 —— 只验非空抓不到「upsert 没生效、旧内容还在」。"""

    def test_upsert_overwrites_and_verifies(self, tmp_db, pid):
        from vermes_cli.scholarforge.database import get_section_content, save_section_content

        assert save_section_content(pid, "intro", "第一版内容") is True
        assert save_section_content(pid, "intro", "第二版内容，更长一些") is True
        assert get_section_content(pid, "intro") == "第二版内容，更长一些"

    def test_readback_detects_content_mismatch(self, tmp_db, pid, monkeypatch):
        """模拟 upsert 未生效：写入被吞，库里仍是旧内容 → 必须返回 False。"""
        import vermes_cli.scholarforge.database as db

        assert db.save_section_content(pid, "intro", "旧内容") is True

        original_get_conn = db.get_conn
        import contextlib

        class _SwallowUpsert:
            def __init__(self, conn):
                self._conn = conn

            def execute(self, sql, params=()):
                if "INSERT INTO section_contents" in sql:
                    return None  # 静默吞掉写入
                return self._conn.execute(sql, params)

            def __getattr__(self, name):
                return getattr(self._conn, name)

        @contextlib.contextmanager
        def patched():
            with original_get_conn() as conn:
                yield _SwallowUpsert(conn)

        monkeypatch.setattr(db, "get_conn", patched)
        ok = db.save_section_content(pid, "intro", "新内容应该覆盖旧的")
        # 只还原 get_conn；用 monkeypatch.undo() 会连 fixture patch 的 DB_PATH 一起撤销
        monkeypatch.setattr(db, "get_conn", original_get_conn)

        assert ok is False, "写入被吞、库里还是旧内容，全等比对必须判失败"
        assert db.get_section_content(pid, "intro") == "旧内容"


class TestTouchProjectIsNonFatal:
    """touch_project 只是元数据副作用，它失败不该否定「正文已落库」。"""

    def test_touch_project_failure_does_not_flip_result(self, tmp_db, pid, monkeypatch):
        import vermes_cli.scholarforge.database as db

        def boom(*_a, **_kw):
            raise RuntimeError("touch_project 故障注入")

        original_touch = db.touch_project
        monkeypatch.setattr(db, "touch_project", boom)
        assert db.save_section_content(pid, "intro", "正文内容") is True
        assert db.save_outline(pid, [{"section_key": "section_1", "title": "引言", "word_count": 1}]) is True
        # 只还原 touch_project；用 monkeypatch.undo() 会连 fixture patch 的 DB_PATH 一起撤销
        monkeypatch.setattr(db, "touch_project", original_touch)

        assert db.get_section_content(pid, "intro") == "正文内容"
