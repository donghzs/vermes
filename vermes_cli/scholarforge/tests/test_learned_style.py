"""learn_style → write 自动仿写：这条链此前**零测试**。

背景（2026-09-18 实证）：
  · 记忆里长期挂着一条「learn_style 承诺未兑现（孤儿功能）」。实测发现它
    **早在 2026-07-29（`9304923c5c`）就补上了落库 + 注入** —— 那条记忆是过期的。
  · 但真问题有两个，本轮一并锁住：
    ① **schema 与报错文案都承诺「至少 500 字」，代码却只在 < 100 时拒绝**
       —— 126 字就能拿到一份「✅ 已提取 8 维风格特征」并被**自动套用到
       后续所有写作**。2~3 句话算出的句长/段落/过渡词密度基本是噪声，
       **却看起来像一份可靠的风格档案**。
    ② **这条链一个测试都没有** —— 现有 3 处测试都只是把 `get_style_prompt`
       patch 成空串来隔离别的用例，从没验证过「落库 → 注入」真通。
"""

import asyncio
import os
import tempfile
import unittest
from unittest.mock import patch

from vermes_cli.scholarforge import database, project_context
from vermes_cli.scholarforge.tools import (
    _handle_scholarforge_learn_style,
    _handle_scholarforge_write,
)

_BASE = (
    "本研究旨在探讨大学生学习动机与学业成就之间的关系，并进一步分析二者在不同"
    "年级上的差异表现。本研究采用问卷调查法，对某市三所高校的在校大学生进行分层"
    "随机抽样调查，共发放问卷四百份，回收有效问卷三百六十二份，有效回收率为九成。"
)

_MIN_STYLE_CHARS = 500      # schema 与报错文案共同承诺的门槛
_HARD_REJECT_CHARS = 100    # 低于此值直接拒绝


def _sample(n: int) -> str:
    """构造**精确** n 字的样本（门槛测试必须精确到字，不能靠目测）。"""
    return (_BASE * (n // len(_BASE) + 1))[:n]


class _TmpDb(unittest.TestCase):
    """把 scholarforge.db 指到临时目录 —— 绝不碰用户真实库。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._patcher = patch.object(database, "DB_PATH",
                                     os.path.join(self._tmp, "test.db"))
        self._patcher.start()
        database.init_db()

    def tearDown(self):
        self._patcher.stop()


# ───────────────────────── ① 样本长度门槛 ─────────────────────────
class TestSampleLengthGate(unittest.TestCase):
    """schema 承诺「至少 500 字」，代码必须守住它 —— 否则就是承诺未兑现。"""

    @staticmethod
    def _learn(n: int) -> str:
        with patch("vermes_cli.scholarforge.tools.resolve_project_id", return_value=1), \
             patch("vermes_cli.scholarforge.tools.get_active_project", return_value=1):
            return asyncio.run(_handle_scholarforge_learn_style(
                {"sample_text": _sample(n), "project_id": 1}))

    def test_under_hard_limit_is_rejected(self):
        for n in (50, 99):
            self.assertTrue(self._learn(n).startswith("❌"), f"{n} 字应被拒绝")

    def test_between_limits_warns_instead_of_silently_passing(self):
        """🔴 100~499 字：旧代码一声不吭地给出"✅ 已提取 8 维风格特征"。
        不阻断（短样本也有用），但**必须显形**可靠性质疑。
        """
        for n in (100, 300, 499):
            r = self._learn(n)
            self.assertFalse(r.startswith("❌"), f"{n} 字不该被拒绝")
            self.assertIn("⚠️", r, f"{n} 字应给出警告")

    def test_at_or_above_threshold_no_warning(self):
        """达标就不该再挂警告（否则每张表都警告就变噪音，用户会习惯性忽略）。"""
        for n in (_MIN_STYLE_CHARS, _MIN_STYLE_CHARS + 20):
            self.assertNotIn("⚠️", self._learn(n), f"{n} 字不该警告")

    def test_warning_names_threshold_actual_length_and_auto_apply(self):
        """警告不能只说"样本偏短" —— 必须说清：门槛是多少、你给了多少、
        以及**这个风格会被自动套用到后续所有写作**（这才是真正的风险点）。
        """
        r = self._learn(300)
        self.assertIn("500 字门槛", r)
        self.assertIn("300", r)          # 报出实际字数，用户才知道差多少
        self.assertIn("自动套用", r)

    def test_short_sample_still_returns_the_style_prompt(self):
        """警告是**附加**信息，不能因此吞掉用户要的风格提示词。"""
        self.assertIn("# 写作风格指令", self._learn(300))

    def test_threshold_matches_schema_claim(self):
        """元守卫：代码里的门槛必须等于 schema/文案对外承诺的 500 字。
        改了门槛却漏改 schema（或反之）会立刻挂在这里。
        """
        # 🔴 用模块自身的 `__file__` 定位，**不能用相对路径** —— 否则换个 cwd 跑就挂
        from vermes_cli.scholarforge import tools as _tools
        src = open(_tools.__file__, encoding="utf-8").read()
        self.assertIn("至少 500 字", src)          # 报错文案
        self.assertIn("（至少 500 字）", src)       # schema description
        self.assertIn("if n_chars < 500:", src)    # 代码判断


# ───────────────────────── ② 落库 → 注入 全链 ─────────────────────────
class TestStylePersistence(_TmpDb):

    def test_save_then_read_round_trip(self):
        pid = database.create_project("风格测试")["id"]
        style = "句长偏短，少用术语，多用第一人称"
        self.assertTrue(project_context.save_style_profile(pid, style))
        self.assertEqual(project_context.get_style_prompt(pid), style)

    def test_project_without_style_returns_empty(self):
        pid = database.create_project("无风格项目")["id"]
        self.assertEqual(project_context.get_style_prompt(pid), "")

    def test_learn_style_persists_and_reports_it(self):
        """schema 承诺「结果自动写回项目库」—— 必须真的写回，并告知用户。"""
        pid = database.create_project("风格测试2")["id"]
        with patch("vermes_cli.scholarforge.tools.resolve_project_id", return_value=pid), \
             patch("vermes_cli.scholarforge.tools.get_active_project", return_value=pid):
            r = asyncio.run(_handle_scholarforge_learn_style(
                {"sample_text": _sample(600), "project_id": pid}))
        self.assertIn("已保存", r)
        self.assertIn("# 写作风格指令", project_context.get_style_prompt(pid))


class TestWriteAppliesLearnedStyle(unittest.TestCase):
    """write 必须真把已学风格塞进 prompt —— 这是「自动仿写」的字面含义。"""

    @staticmethod
    def _write(style: str) -> str:
        captured = {}

        def fake_llm(prompt, *a, **k):
            captured["prompt"] = prompt
            return "# 引言\n正文段落。"

        with patch("vermes_cli.scholarforge.tools.resolve_project_id", return_value=7), \
             patch("vermes_cli.scholarforge.tools._call_llm", side_effect=fake_llm), \
             patch("vermes_cli.scholarforge.project_context.auto_snapshot"), \
             patch("vermes_cli.scholarforge.project_context.format_project_context_prompt",
                   return_value="【项目】上下文"), \
             patch("vermes_cli.scholarforge.project_context.load_project_context",
                   return_value={"title": "T", "paper_type": "本科论文"}), \
             patch("vermes_cli.scholarforge.project_context.get_style_prompt",
                   return_value=style), \
             patch("vermes_cli.scholarforge.project_context.save_section"), \
             patch("vermes_cli.scholarforge.quality_gate.run_quality_gate",
                   return_value=("# 引言\n正文段落。", "", False)):
            asyncio.run(_handle_scholarforge_write(
                {"topic": "t", "section_type": "abstract", "project_id": 7}))
        return captured.get("prompt", "")

    def test_learned_style_is_injected_into_prompt(self):
        p = self._write("句长偏短，少用术语")
        self.assertIn("写作风格要求", p)
        self.assertIn("句长偏短，少用术语", p)

    def test_no_style_means_no_style_block(self):
        """没有学过风格时不该挂一个空的「写作风格要求」段（噪音）。"""
        self.assertNotIn("写作风格要求", self._write(""))


if __name__ == "__main__":
    unittest.main()
