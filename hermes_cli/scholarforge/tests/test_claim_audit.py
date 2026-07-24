"""测试 ScholarForge 主张-证据审查流水线 (claim_audit)

mock 模式照抄 test_tools.py：patch `_call_llm` 为 AsyncMock，
enable_online=False 避免联网，不依赖真实 LLM。
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest


# ──────────────────────────────────────────────────────────────
# 样本数据
# ──────────────────────────────────────────────────────────────

SAMPLE_PAPER = """
本研究探讨 X 方法对 Y 指标的影响。实验采用单因素被试间设计，
实验组 30 人，对照组 30 人。结果显示 X 方法显著提升 Y 指标，
F(1,58)=4.2, p=0.04, η²=0.12 [1]。这一发现与 Smith (2020) 的结论一致 [2]。
然而部分被试反映评估过程存在主观偏差，可能影响效度。
"""

SAMPLE_CLAIMS = json.dumps([
    {
        "claim": "X 方法显著提升 Y 指标",
        "type": "statistical",
        "section": "结果",
        "evidence_quote": "F(1,58)=4.2, p=0.04, η²=0.12",
        "citations": [1],
        "stats": {
            "eta_squared": 0.12,
            "f_value": 4.2,
            "df": 1,
            "df_error": 58,
            "p_value": 0.04,
        },
    },
    {
        "claim": "这一发现与 Smith (2020) 的结论一致",
        "type": "empirical",
        "section": "讨论",
        "evidence_quote": "与 Smith (2020) 的结论一致",
        "citations": [2],
        "stats": {},
    },
])

SAMPLE_REFERENCES = [
    {
        "title": "Effects of X on Y",
        "authors": "Zhang et al.",
        "year": "2021",
        "venue": "Journal of Example",
        "doi": "10.1234/example.2021.001",
    },
    {
        "title": "A Study on Y",
        "authors": "Smith",
        "year": "2020",
        "venue": "Journal of Samples",
        "doi": "",
    },
]


# ──────────────────────────────────────────────────────────────
# 测试用例
# ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_review_pipeline_basic():
    """端到端流水线：mock LLM 返回 2 条 Claim，验证返回审查报告"""
    with patch(
        "hermes_cli.scholarforge.tools._call_llm",
        AsyncMock(return_value=SAMPLE_CLAIMS),
    ):
        from hermes_cli.scholarforge.claim_audit import review_claims

        report = await review_claims(
            SAMPLE_PAPER,
            references=SAMPLE_REFERENCES,
            enable_online=False,
        )

    assert "主张-证据审查" in report, f"报告标题缺失: {report[:200]}"
    assert ("需复核" in report) or ("支撑充分" in report), (
        f"应有结论列: {report[:300]}"
    )
    assert "共抽取 **2** 条核心主张" in report, (
        f"应显示抽取2条claim: {report[:200]}"
    )


@pytest.mark.asyncio
async def test_review_pipeline_empty_claims():
    """LLM 返回空数组时应返回提示信息"""
    with patch(
        "hermes_cli.scholarforge.tools._call_llm",
        AsyncMock(return_value="[]"),
    ):
        from hermes_cli.scholarforge.claim_audit import review_claims

        report = await review_claims("短文本，无核心主张", enable_online=False)

    assert "未能从论文中抽取到可审查的核心主张" in report, (
        f"空 claim 应返回提示: {report}"
    )


@pytest.mark.asyncio
async def test_review_pipeline_no_references():
    """不提供 references 时跳过引用核查，不报错"""
    with patch(
        "hermes_cli.scholarforge.tools._call_llm",
        AsyncMock(return_value=SAMPLE_CLAIMS),
    ):
        from hermes_cli.scholarforge.claim_audit import review_claims

        report = await review_claims(
            SAMPLE_PAPER,
            references=None,
            enable_online=False,
        )

    assert "主张-证据审查" in report
    # 无 references 时 claim 里的 citations 会显示"引用未提供文献列表"
    assert "未提供文献列表" in report, (
        f"应提示未提供文献列表: {report[:300]}"
    )


@pytest.mark.asyncio
async def test_review_pipeline_no_network_when_disabled():
    """enable_online=False 时不触发网络调用"""
    with patch(
        "hermes_cli.scholarforge.tools._call_llm",
        AsyncMock(return_value=SAMPLE_CLAIMS),
    ) as mock_llm, patch(
        "hermes_cli.scholarforge.validators.verify_citation_authenticity",
        AsyncMock(return_value=[]),
    ) as mock_verify:
        from hermes_cli.scholarforge.claim_audit import review_claims

        await review_claims(
            SAMPLE_PAPER,
            references=SAMPLE_REFERENCES,
            enable_online=False,
        )

    # LLM 应只调一次（抽 claim）
    assert mock_llm.call_count == 1, f"LLM 应调1次, 实际 {mock_llm.call_count}"
    # verify_citation_authenticity 应以 enable_online=False 调用
    args, kwargs = mock_verify.call_args
    assert kwargs.get("enable_online") is False or (
        len(args) >= 2 and args[1] is False
    ), f"应传 enable_online=False, 实际 args={args} kwargs={kwargs}"


@pytest.mark.asyncio
async def test_review_pipeline_llm_failure_graceful():
    """LLM 调用失败时返回空 claim 提示（不抛异常）"""
    with patch(
        "hermes_cli.scholarforge.tools._call_llm",
        AsyncMock(side_effect=Exception("LLM service unavailable")),
    ):
        from hermes_cli.scholarforge.claim_audit import review_claims

        report = await review_claims(SAMPLE_PAPER, enable_online=False)

    assert "未能从论文中抽取到可审查的核心主张" in report, (
        f"LLM 失败应优雅返回提示: {report}"
    )


@pytest.mark.asyncio
async def test_handle_review_claims_via_tools():
    """通过 tools.py handler 调用，验证薄壳正常工作"""
    with patch(
        "hermes_cli.scholarforge.tools._call_llm",
        AsyncMock(return_value=SAMPLE_CLAIMS),
    ):
        from hermes_cli.scholarforge.tools import (
            _handle_scholarforge_review_claims,
        )

        result = await _handle_scholarforge_review_claims(
            {
                "paper_text": SAMPLE_PAPER,
                "references": json.dumps(SAMPLE_REFERENCES),
                "enable_online": False,
            }
        )

    assert "主张-证据审查" in result, f"handler 应返回审查报告: {result[:200]}"


@pytest.mark.asyncio
async def test_handle_review_claims_empty_paper():
    """空论文文本应返回错误提示"""
    from hermes_cli.scholarforge.tools import _handle_scholarforge_review_claims

    result = await _handle_scholarforge_review_claims({"paper_text": ""})
    assert "❌" in result
    assert "论文文本" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
