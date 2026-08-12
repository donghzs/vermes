"""Phase 2/3 修复测试：replace_citations 闭环 / 引用样式收窄 / AI 检测诚实化 / 使用埋点。

对应审计修订路线：
- Phase 2: replace_citations claim→检索闭环（本地库优先 + 写回兑现）
- Phase 3a: 引用样式收窄为 gbt7714/apa/ieee/mla 四种主流
- Phase 3b: deaigc 改「AI 写作特征提示」，不冒充 AI 检测器
- 用户场景验证: tool_usage 埋点 + 统计查询
"""

import asyncio
import os
import tempfile

import pytest


@pytest.fixture()
def temp_db(monkeypatch):
    """隔离的 scholarforge DB。"""
    import vermes_cli.scholarforge.database as db

    tmpdir = tempfile.mkdtemp()
    monkeypatch.setattr(db, "DB_PATH", os.path.join(tmpdir, "test_scholar.db"))
    yield db


# ──────────────────────────────────────────────────────────────
# Phase 2: replace_citations 闭环
# ──────────────────────────────────────────────────────────────

class TestReplaceCitationsLoop:
    def test_schema_describes_real_loop(self):
        """schema 描述与实现一致：claim 检索式 + 本地库优先 + 写回去重。"""
        from vermes_cli.scholarforge.tools import SCHOLARFORGE_REPLACE_CITATIONS_SCHEMA as S
        desc = S["description"]
        assert "claim" in desc
        assert "本地文献库" in desc
        assert "写回" in desc
        assert "去重" in desc

    def test_local_literature_becomes_candidate(self, temp_db, monkeypatch):
        """项目本地文献应参与匹配并被选中（无需联网）。"""
        import vermes_cli.scholarforge.tools as tools_mod
        from vermes_cli.scholarforge import search as search_mod

        pid = temp_db.create_project(title="测试项目", paper_type="期刊论文")["id"]
        temp_db.add_literature(
            pid,
            title="Retrieval Augmented Generation for Knowledge Intensive Tasks",
            authors=["Lewis P", "Perez E"],
            year=2020,
            venue="NeurIPS",
            doi="10.1000/rag2020",
        )

        # 在线检索返回空（模拟离线）
        async def fake_search(keyword, limit=10):
            if False:
                yield None

        monkeypatch.setattr(search_mod, "search_papers", fake_search)

        # LLM 不可用（claim 提取回退正则，精排回退粗排）
        async def fake_llm(prompt, system=""):
            return "❌ no llm"

        monkeypatch.setattr(tools_mod, "_call_llm", fake_llm)

        async def fake_rerank(cands, ctx, kw, **kwargs):
            return [(p, 0.9) for p in cands]

        # F-25 重构后匹配走 citation_matcher.match_citations，精排改打 citation_matcher.llm_rerank
        from vermes_cli.scholarforge import citation_matcher as cm_mod
        monkeypatch.setattr(cm_mod, "llm_rerank", fake_rerank)

        draft = "Retrieval Augmented Generation (RAG) improves knowledge intensive tasks [1]。"
        result = asyncio.run(
            tools_mod._handle_scholarforge_replace_citations(
                {"draft": draft, "project_id": pid}
            )
        )
        assert "Retrieval Augmented Generation" in result
        assert "📚本地" in result  # 本地命中标记

    def test_writeback_dedup(self, temp_db, monkeypatch):
        """在线检索命中的新文献写回项目库，且按标题去重不重复入库。"""
        import vermes_cli.scholarforge.tools as tools_mod
        from vermes_cli.scholarforge import search as search_mod
        from vermes_cli.scholarforge.search import PaperResult

        pid = temp_db.create_project(title="写回测试", paper_type="期刊论文")["id"]

        paper = PaperResult(
            paper_id="x1",
            title="Attention Is All You Need",
            authors=["Vaswani A"],
            year="2017",
            venue="NeurIPS",
            doi="10.1000/attn",
            source="arxiv",
        )

        async def fake_search(keyword, limit=10):
            yield paper

        monkeypatch.setattr(search_mod, "search_papers", fake_search)

        async def fake_llm(prompt, system=""):
            return "❌ no llm"

        monkeypatch.setattr(tools_mod, "_call_llm", fake_llm)

        async def fake_rerank(cands, ctx, kw, **kwargs):
            return [(p, 0.95) for p in cands]

        # F-25 重构后匹配走 citation_matcher.match_citations，精排改打 citation_matcher.llm_rerank
        from vermes_cli.scholarforge import citation_matcher as cm_mod
        monkeypatch.setattr(cm_mod, "llm_rerank", fake_rerank)

        draft = "Transformer architecture with attention mechanism [1]。"
        result = asyncio.run(
            tools_mod._handle_scholarforge_replace_citations(
                {"draft": draft, "project_id": pid}
            )
        )
        lits = temp_db.list_literature(pid)
        assert len(lits) == 1
        assert lits[0]["title"] == "Attention Is All You Need"
        assert "已写回项目文献库" in result

        # 第二次运行：同一篇不应重复入库
        asyncio.run(
            tools_mod._handle_scholarforge_replace_citations(
                {"draft": draft, "project_id": pid}
            )
        )
        assert len(temp_db.list_literature(pid)) == 1


# ──────────────────────────────────────────────────────────────
# Phase 3a: 引用样式收窄为 4 种
# ──────────────────────────────────────────────────────────────

class TestCitationStyles:
    def test_schema_enum_is_four_styles(self):
        from vermes_cli.scholarforge.tools import SCHOLARFORGE_FORMAT_REFS_SCHEMA as S
        enum = S["parameters"]["properties"]["style"]["enum"]
        assert set(enum) == {"gbt7714", "apa", "ieee", "mla"}

    @pytest.mark.parametrize("style,expect", [
        ("apa", "(2017)"),
        ("ieee", "[1]"),
        ("mla", '"'),
    ])
    def test_new_styles_format(self, style, expect):
        import vermes_cli.scholarforge.tools as tools_mod
        papers = '[{"title": "Attention Is All You Need", "authors": ["Ashish Vaswani"], "year": "2017", "venue": "NeurIPS", "doi": ""}]'
        result = asyncio.run(
            tools_mod._handle_scholarforge_format_refs({"papers": papers, "style": style})
        )
        assert not result.startswith("❌"), result
        assert expect in result

    def test_apa7_backward_compat(self):
        """旧枚举值 apa7 仍可用（映射到 apa）。"""
        import vermes_cli.scholarforge.tools as tools_mod
        papers = '[{"title": "T", "authors": ["A B"], "year": "2020", "venue": "V", "doi": ""}]'
        result = asyncio.run(
            tools_mod._handle_scholarforge_format_refs({"papers": papers, "style": "apa7"})
        )
        assert not result.startswith("❌")
        assert "APA 7th" in result

    def test_gbt7714_still_works(self):
        import vermes_cli.scholarforge.tools as tools_mod
        papers = '[{"title": "深度学习研究", "authors": ["张三"], "year": "2021", "venue": "计算机学报", "doi": ""}]'
        result = asyncio.run(
            tools_mod._handle_scholarforge_format_refs({"papers": papers})
        )
        assert not result.startswith("❌")
        assert "GB/T 7714" in result

    def test_unknown_style_rejected(self):
        import vermes_cli.scholarforge.tools as tools_mod
        papers = '[{"title": "T", "authors": [], "year": "", "venue": "", "doi": ""}]'
        result = asyncio.run(
            tools_mod._handle_scholarforge_format_refs({"papers": papers, "style": "chicago"})
        )
        assert result.startswith("❌")


# ──────────────────────────────────────────────────────────────
# Phase 3b: AI 检测诚实化
# ──────────────────────────────────────────────────────────────

class TestDeaigcHonesty:
    def test_schema_disclaims_detector(self):
        from vermes_cli.scholarforge.tools import SCHOLARFORGE_DEAIGC_SCHEMA as S
        desc = S["description"]
        assert "不是" in desc and "AI 检测器" in desc
        assert "不保证" in desc
        # 不再承诺"降低 AIGC 检测分数"
        assert "降低 AIGC 检测分数" not in desc

    def test_report_no_ai_probability_claim(self, monkeypatch):
        """报告用「机械化特征指数」而非「AI 评分」，并带免责声明。"""
        import vermes_cli.scholarforge.tools as tools_mod

        async def fake_llm(prompt, system=""):
            return "改写后的文本"

        monkeypatch.setattr(tools_mod, "_call_llm", fake_llm)

        # 构造高模板化文本触发处理路径
        text = "首先，本文提出了方法。其次，本文分析了问题。再次，本文总结了结论。" * 20
        result = asyncio.run(
            tools_mod._handle_scholarforge_deaigc({"text": text})
        )
        assert "AI 评分" not in result
        if "机械化特征指数" in result:
            assert "不是 AI 生成概率" in result
        else:
            # 低特征路径也必须带非检测声明
            assert "非 AI 检测结论" in result


# ──────────────────────────────────────────────────────────────
# 用户场景验证: 工具使用埋点
# ──────────────────────────────────────────────────────────────

class TestUsageTracking:
    def test_record_and_stats(self, temp_db):
        temp_db.record_tool_usage("scholarforge_write", ok=True, duration_ms=1200)
        temp_db.record_tool_usage("scholarforge_write", ok=False, duration_ms=300)
        temp_db.record_tool_usage("scholarforge_search", ok=True, duration_ms=800)

        stats = temp_db.get_tool_usage_stats(days=1)
        assert len(stats) == 2
        by_name = {s["tool_name"]: s for s in stats}
        assert by_name["scholarforge_write"]["calls"] == 2
        assert by_name["scholarforge_write"]["successes"] == 1
        assert by_name["scholarforge_search"]["calls"] == 1

    def test_with_usage_wrapper_records(self, temp_db):
        from vermes_cli.scholarforge.tools import _with_usage

        async def dummy_handler(args, **kw):
            return "✅ ok"

        wrapped = _with_usage("scholarforge_dummy", dummy_handler)
        result = asyncio.run(wrapped({}))
        assert result == "✅ ok"

        stats = temp_db.get_tool_usage_stats(days=1)
        names = [s["tool_name"] for s in stats]
        assert "scholarforge_dummy" in names

    def test_wrapper_marks_error_result(self, temp_db):
        from vermes_cli.scholarforge.tools import _with_usage

        async def failing_handler(args, **kw):
            return "❌ 出错了"

        wrapped = _with_usage("scholarforge_failing", failing_handler)
        asyncio.run(wrapped({}))
        stats = temp_db.get_tool_usage_stats(days=1)
        by_name = {s["tool_name"]: s for s in stats}
        assert by_name["scholarforge_failing"]["successes"] == 0

    def test_wrapper_never_breaks_tool(self, temp_db, monkeypatch):
        """埋点自身故障不影响工具返回。"""
        import vermes_cli.scholarforge.database as db
        from vermes_cli.scholarforge.tools import _with_usage

        def boom(*a, **kw):
            raise RuntimeError("db down")

        monkeypatch.setattr(db, "record_tool_usage", boom)

        async def dummy_handler(args, **kw):
            return "✅ still fine"

        wrapped = _with_usage("scholarforge_x", dummy_handler)
        assert asyncio.run(wrapped({})) == "✅ still fine"

    def test_all_22_tools_wrapped(self):
        """register_tools 中 22 个工具全部经过 _with_usage 包装。"""
        import inspect
        import vermes_cli.scholarforge.tools as tools_mod

        src = inspect.getsource(tools_mod.register_tools)
        assert src.count("_with_usage(") == 26
