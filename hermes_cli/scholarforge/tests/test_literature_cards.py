"""测试 ScholarForge 文献知识沉淀 (Literature Cards)

mock _call_llm + mock search_papers，验证去重/入库/矩阵生成。
"""
from __future__ import annotations

import json
import os
import tempfile
from unittest.mock import AsyncMock, patch

import pytest


SAMPLE_PAPERS = [
    {
        "title": "Deep Learning for Medical Image Analysis",
        "authors": ["Zhang, Y.", "Li, X."],
        "year": "2024",
        "venue": "Nature Methods",
        "abstract": "We propose a novel transformer-based approach for medical image segmentation...",
        "url": "https://doi.org/10.1234/dl-medical",
        "doi": "10.1234/dl-medical",
        "pdf_url": "",
        "source": "arxiv",
    },
    {
        "title": "Transformer Architectures Survey",
        "authors": ["Wang, Q."],
        "year": "2023",
        "venue": "ACL",
        "abstract": "A comprehensive survey of transformer architectures...",
        "url": "https://doi.org/10.5678/transformer-survey",
        "doi": "10.5678/transformer-survey",
        "pdf_url": "",
        "source": "crossref",
    },
]

LLM_RESPONSE = json.dumps([
    {
        "research_question": "如何用 transformer 做医学图像分割",
        "methods": "transformer + U-Net 混合架构",
        "datasets": "PSCC 2020, 自采数据",
        "findings": "在多个数据集上 SOTA",
        "limitations": "对小病灶检测能力有限",
        "key_claims": ["transformer 优于 CNN", "混合架构最优"],
        "tags": ["深度学习", "医学图像"],
    },
    {
        "research_question": "transformer 架构的发展脉络",
        "methods": "文献综述",
        "datasets": "无",
        "findings": "总结了 5 类 transformer 变体",
        "limitations": "未覆盖最新大模型",
        "key_claims": ["注意力机制是核心"],
        "tags": ["transformer", "综述"],
    },
], ensure_ascii=False)


@pytest.fixture(autouse=True)
def temp_db():
    """每个测试用临时数据库"""
    tmpdir = tempfile.mkdtemp(prefix="scholarforge_test_")
    db_path = os.path.join(tmpdir, "test.db")
    with patch("hermes_cli.scholarforge.database.DB_PATH", db_path):
        yield db_path
    import shutil
    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.mark.asyncio
async def test_save_cards_basic(temp_db):
    """端到端：mock LLM 返回抽取结果，验证入库"""
    with patch(
        "hermes_cli.scholarforge.tools._call_llm",
        AsyncMock(return_value=LLM_RESPONSE),
    ):
        from hermes_cli.scholarforge.literature_cards import save_cards

        result = await save_cards(SAMPLE_PAPERS)

    assert result["added"] == 2
    assert result["skipped"] == 0
    assert result["total"] == 2


@pytest.mark.asyncio
async def test_save_cards_dedup_by_doi(temp_db):
    """同 doi 两篇 → added=1, skipped=1"""
    with patch(
        "hermes_cli.scholarforge.tools._call_llm",
        AsyncMock(return_value=LLM_RESPONSE),
    ):
        from hermes_cli.scholarforge.literature_cards import save_cards

        # 第一次存入
        result1 = await save_cards(SAMPLE_PAPERS)
        assert result1["added"] == 2

        # 第二次：第一篇 doi 重复，第二篇 doi 重复
        result2 = await save_cards(SAMPLE_PAPERS)
        assert result2["added"] == 0
        assert result2["skipped"] == 2


@pytest.mark.asyncio
async def test_save_cards_dedup_by_title(temp_db):
    """无 doi 时按 title 去重"""
    papers_no_doi = [
        {"title": "A Paper Without DOI", "authors": [], "year": "2024", "abstract": "...", "doi": "", "source": "test"},
    ]
    with patch(
        "hermes_cli.scholarforge.tools._call_llm",
        AsyncMock(return_value=json.dumps([{"research_question": "test"}])),
    ):
        from hermes_cli.scholarforge.literature_cards import save_cards

        r1 = await save_cards(papers_no_doi)
        assert r1["added"] == 1

        r2 = await save_cards(papers_no_doi)
        assert r2["added"] == 0
        assert r2["skipped"] == 1


@pytest.mark.asyncio
async def test_save_cards_empty(temp_db):
    """空 papers 返回零"""
    from hermes_cli.scholarforge.literature_cards import save_cards

    result = await save_cards([])
    assert result["added"] == 0
    assert result["skipped"] == 0
    assert result["total"] == 0


@pytest.mark.asyncio
async def test_save_cards_llm_failure(temp_db):
    """LLM 失败时不崩，字段填'未明确'"""
    with patch(
        "hermes_cli.scholarforge.tools._call_llm",
        AsyncMock(side_effect=Exception("LLM down")),
    ):
        from hermes_cli.scholarforge.literature_cards import save_cards

        result = await save_cards(SAMPLE_PAPERS[:1])

    assert result["added"] == 1  # base 元数据照常入库

    # 验证 LLM 字段是默认值
    from hermes_cli.scholarforge.database import get_conn, init_db
    init_db()
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM literature_cards LIMIT 1").fetchone()
        assert row["research_question"] == "未明确"


@pytest.mark.asyncio
async def test_save_cards_from_query(temp_db):
    """mock search_papers → 沉淀"""
    from hermes_cli.scholarforge.search import PaperResult

    fake_papers = [
        PaperResult(paper_id="test:1", title="Query Paper", authors=["Author"], year="2024",
                    venue="Test", abstract="Abstract...", doi="10.9999/query", source="test"),
    ]

    async def mock_search(*args, **kwargs):
        for p in fake_papers:
            yield p

    with patch("hermes_cli.scholarforge.search.search_papers", mock_search):
        with patch(
            "hermes_cli.scholarforge.tools._call_llm",
            AsyncMock(return_value=json.dumps([{"research_question": "test query"}])),
        ):
            from hermes_cli.scholarforge.literature_cards import save_cards_from_query

            result = await save_cards_from_query("deep learning", limit=5)

    assert result["added"] == 1


def test_literature_matrix_basic(temp_db):
    """矩阵视图：存 2 卡 → 查回矩阵"""
    import time
    from hermes_cli.scholarforge.database import get_conn, init_db

    init_db()
    now = int(time.time())
    with get_conn() as conn:
        for i, p in enumerate(SAMPLE_PAPERS):
            conn.execute("""
                INSERT INTO literature_cards
                (title, authors, year, venue, doi, url, source, abstract,
                 research_question, methods, datasets, findings, limitations,
                 key_claims, tags, added_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                p["title"], json.dumps(p["authors"]), p["year"], p["venue"],
                p["doi"], p["url"], p["source"], p["abstract"],
                "测试问题", "测试方法", "测试数据", "测试发现", "测试局限",
                json.dumps(["主张1"]), json.dumps(["标签1"]), now,
            ))

    from hermes_cli.scholarforge.literature_cards import literature_matrix

    matrix = literature_matrix()

    assert "文献综述矩阵" in matrix
    assert "Deep Learning for Medical Image Analysis" in matrix
    assert "Transformer Architectures Survey" in matrix
    assert "测试问题" in matrix
    assert "测试方法" in matrix
    assert "测试发现" in matrix
    assert "测试局限" in matrix


def test_literature_matrix_by_tag(temp_db):
    """按标签过滤"""
    import time
    from hermes_cli.scholarforge.database import get_conn, init_db

    init_db()
    now = int(time.time())
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO literature_cards
            (title, authors, year, venue, doi, source, abstract,
             research_question, methods, datasets, findings, limitations,
             key_claims, tags, added_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "Tagged Paper", "[]", "2024", "Test", "10.1/tag", "test", "...",
            "RQ", "M", "D", "F", "L", "[]", '["LLM", "教育"]', now,
        ))
        conn.execute("""
            INSERT INTO literature_cards
            (title, authors, year, venue, doi, source, abstract,
             research_question, methods, datasets, findings, limitations,
             key_claims, tags, added_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "Other Paper", "[]", "2024", "Test", "10.2/other", "test", "...",
            "RQ2", "M2", "D2", "F2", "L2", "[]", '["硬件"]', now,
        ))

    from hermes_cli.scholarforge.literature_cards import literature_matrix

    matrix = literature_matrix(tag="LLM")
    assert "Tagged Paper" in matrix
    assert "Other Paper" not in matrix


def test_literature_matrix_empty(temp_db):
    """空库返回提示"""
    from hermes_cli.scholarforge.literature_cards import literature_matrix

    matrix = literature_matrix()
    assert "文献卡片库为空" in matrix


def test_literature_matrix_with_topic_rerank(temp_db):
    """有 topic 时用 TF-IDF 重排（不崩）"""
    import time
    from hermes_cli.scholarforge.database import get_conn, init_db

    init_db()
    now = int(time.time())
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO literature_cards
            (title, authors, year, venue, doi, source, abstract,
             research_question, methods, datasets, findings, limitations,
             key_claims, tags, added_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "Medical AI Paper", "[]", "2024", "Test", "10.1/med", "test",
            "Medical AI abstract about diagnosis and treatment",
            "RQ", "M", "D", "F", "L", "[]", '["medical"]', now,
        ))

    from hermes_cli.scholarforge.literature_cards import literature_matrix

    matrix = literature_matrix(topic="medical diagnosis")
    assert "Medical AI Paper" in matrix


@pytest.mark.asyncio
async def test_handle_save_cards_via_tools(temp_db):
    """通过 handler 调用"""
    with patch(
        "hermes_cli.scholarforge.tools._call_llm",
        AsyncMock(return_value=LLM_RESPONSE),
    ):
        from hermes_cli.scholarforge.tools import _handle_scholarforge_save_cards

        result = await _handle_scholarforge_save_cards({
            "papers": json.dumps(SAMPLE_PAPERS),
        })
        assert "文献卡片沉淀完成" in result
        assert "新增: 2" in result


@pytest.mark.asyncio
async def test_handle_matrix_via_tools(temp_db):
    """矩阵 handler"""
    from hermes_cli.scholarforge.tools import _handle_scholarforge_literature_matrix

    result = await _handle_scholarforge_literature_matrix({})
    assert "文献卡片库为空" in result


@pytest.mark.asyncio
async def test_handle_save_cards_no_args(temp_db):
    """无参数返回错误"""
    from hermes_cli.scholarforge.tools import _handle_scholarforge_save_cards

    result = await _handle_scholarforge_save_cards({})
    assert "❌" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
