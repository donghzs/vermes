"""测试 ScholarForge 研究选题拆解 (research_map)

mock _call_llm 返回结构化 JSON，验证格式化和边界条件。
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest


SAMPLE_RESPONSE = json.dumps({
    "core_question": "大语言模型能否有效提升K-12学生的写作能力？",
    "sub_questions": [
        {"question": "LLM 在写作辅导中的具体角色是什么？", "aspect": "方法"},
        {"question": "不同年级学生对 LLM 辅导的接受度差异？", "aspect": "评估"},
        {"question": "LLM 辅导对写作质量的影响如何量化？", "aspect": "应用"},
    ],
    "consensus": [
        "LLM 能提供即时反馈，减轻教师批改负担",
        "提示工程对输出质量影响显著",
    ],
    "controversies": [
        {"point": "LLM 是否会抑制学生独立思考", "positions": ["会依赖", "不会，反而促进迭代"]},
    ],
    "gaps": [
        "缺乏针对 K-12 低年级的纵向研究",
        "不同学科领域的影响差异未充分比较",
    ],
    "hypotheses": [
        {
            "hypothesis": "使用 LLM 辅导的学生写作评分高于未使用组",
            "type": "差异",
            "key_variables": ["LLM使用(自变量)", "写作评分(因变量)", "年级/基础水平(控制)"],
            "testable": True,
        },
    ],
    "recommended_approaches": [
        "准实验设计：实验组/对照组前后测",
        "混合方法：量化评分 + 质性访谈",
    ],
}, ensure_ascii=False)


@pytest.mark.asyncio
async def test_research_map_basic():
    """端到端：mock LLM 返回完整 JSON，验证报告格式"""
    with patch(
        "vermes_cli.scholarforge.tools._call_llm",
        AsyncMock(return_value=SAMPLE_RESPONSE),
    ):
        from vermes_cli.scholarforge.research_map import research_map

        report = await research_map("大语言模型在教育中的应用")

    assert "研究地图" in report
    assert "核心研究问题" in report
    assert "研究问题树" in report
    assert "领域共识" in report
    assert "争议与分歧" in report
    assert "研究空白" in report
    assert "可验证假设" in report
    assert "推荐研究路径" in report
    # 检查具体内容
    assert "K-12" in report
    assert "差异" in report  # hypothesis type


@pytest.mark.asyncio
async def test_research_map_with_context():
    """带补充上下文"""
    with patch(
        "vermes_cli.scholarforge.tools._call_llm",
        AsyncMock(return_value=SAMPLE_RESPONSE),
    ) as mock_llm:
        from vermes_cli.scholarforge.research_map import research_map

        report = await research_map("LLM教育应用", context="已有文献: Smith 2020")

    assert "研究地图" in report
    # 验证 context 被传入 prompt
    call_args = mock_llm.call_args
    assert "Smith 2020" in call_args[0][0]  # prompt 含 context


@pytest.mark.asyncio
async def test_research_map_empty_topic():
    """空方向应返回错误"""
    from vermes_cli.scholarforge.research_map import research_map

    report = await research_map("")
    assert "❌" in report
    assert "研究方向" in report


@pytest.mark.asyncio
async def test_research_map_llm_failure():
    """LLM 调用失败时优雅返回错误"""
    with patch(
        "vermes_cli.scholarforge.tools._call_llm",
        AsyncMock(side_effect=Exception("LLM unavailable")),
    ):
        from vermes_cli.scholarforge.research_map import research_map

        report = await research_map("某个方向")
        assert "❌" in report
        assert "研究选题拆解失败" in report


@pytest.mark.asyncio
async def test_research_map_json_in_codeblock():
    """LLM 返回 ```json 包裹的 JSON 也能解析"""
    wrapped = f"```json\n{SAMPLE_RESPONSE}\n```"
    with patch(
        "vermes_cli.scholarforge.tools._call_llm",
        AsyncMock(return_value=wrapped),
    ):
        from vermes_cli.scholarforge.research_map import research_map

        report = await research_map("测试方向")
        assert "研究地图" in report
        assert "核心研究问题" in report


@pytest.mark.asyncio
async def test_research_map_partial_json():
    """LLM 返回部分字段缺失的 JSON 也能格式化（不崩）"""
    partial = json.dumps({"core_question": "测试问题"}, ensure_ascii=False)
    with patch(
        "vermes_cli.scholarforge.tools._call_llm",
        AsyncMock(return_value=partial),
    ):
        from vermes_cli.scholarforge.research_map import research_map

        report = await research_map("测试")
        assert "研究地图" in report
        assert "测试问题" in report
        # 缺失的 section 不会出现但也不崩
        assert "可验证假设" not in report


@pytest.mark.asyncio
async def test_research_map_invalid_json():
    """LLM 返回非 JSON 时返回提示"""
    with patch(
        "vermes_cli.scholarforge.tools._call_llm",
        AsyncMock(return_value="这不是JSON，只是普通文本"),
    ):
        from vermes_cli.scholarforge.research_map import research_map

        report = await research_map("测试")
        assert "未能从 LLM 响应中解析" in report


@pytest.mark.asyncio
async def test_handle_research_map_via_tools():
    """通过 tools.py handler 调用"""
    with patch(
        "vermes_cli.scholarforge.tools._call_llm",
        AsyncMock(return_value=SAMPLE_RESPONSE),
    ):
        from vermes_cli.scholarforge.tools import _handle_scholarforge_research_map

        result = await _handle_scholarforge_research_map({"topic": "LLM教育"})
        assert "研究地图" in result


@pytest.mark.asyncio
async def test_handle_research_map_empty_topic():
    """handler 空主题返回错误"""
    from vermes_cli.scholarforge.tools import _handle_scholarforge_research_map

    result = await _handle_scholarforge_research_map({"topic": ""})
    assert "❌" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
