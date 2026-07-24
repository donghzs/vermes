"""Writing Pipeline（scholarforge_writing_pipeline）编排测试。

策略：mock 掉 LLM 调用与文献检索网络调用，验证 6 个阶段被依次串联、
失败/边界分支正确，且不抛异常。golden 模式对齐仓库既有 test_tools.py。
"""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch


class TestWritingPipeline(unittest.TestCase):

    def _run(self, args: dict):
        async def _go():
            with patch(
                "hermes_cli.scholarforge.tools._call_llm",
                AsyncMock(return_value="MOCK_STAGE_OUTPUT"),
            ), patch(
                "hermes_cli.scholarforge.literature_cards.save_cards_from_query",
                AsyncMock(return_value={"added": 1, "skipped": 0, "total": 1}),
            ):
                from hermes_cli.scholarforge.tools import (
                    _handle_scholarforge_writing_pipeline,
                )
                return await _handle_scholarforge_writing_pipeline(args)
        return asyncio.run(_go())

    def test_pipeline_runs_all_six_stages(self):
        result = self._run({"topic": "大语言模型在教育中的应用"})
        # 标题
        self.assertIn("写作流水线", result)
        # 6 个阶段标题齐全
        self.assertIn("研究地图", result)
        self.assertIn("大纲", result)
        self.assertIn("初稿", result)
        self.assertIn("主张-证据审查", result)
        self.assertIn("文献卡片沉淀", result)
        self.assertIn("综述矩阵", result)
        # 检查点清单
        self.assertIn("检查点", result)

    def test_pipeline_empty_topic_returns_error(self):
        result = self._run({"topic": ""})
        self.assertIn("❌", result)

    def test_pipeline_skip_cards_omits_card_stages(self):
        result = self._run({"topic": "X", "skip_cards": True})
        self.assertIn("写作流水线", result)
        # skip_cards=True 时应跳过文献沉淀与矩阵阶段
        self.assertNotIn("文献卡片沉淀", result)
        self.assertNotIn("综述矩阵", result)
        # 但前 4 阶段仍在
        self.assertIn("研究地图", result)
        self.assertIn("大纲", result)
        self.assertIn("初稿", result)
        self.assertIn("主张-证据审查", result)

    def test_pipeline_custom_sections(self):
        result = self._run({"topic": "Y", "skip_cards": True,
                             "sections": ["abstract", "conclusion"]})
        # 仅写 abstract + conclusion 两章：初稿里应出现这两个 section 标记
        self.assertIn("abstract", result)
        self.assertIn("conclusion", result)


if __name__ == "__main__":
    unittest.main()
