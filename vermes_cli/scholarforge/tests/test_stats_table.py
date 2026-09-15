"""stats_table（统计结果 → 三线表）与 F↔η² 校验修正的回归测试。

2026-09-16 新增。两条主线：
  A. 解析内核：行内统计量 / SPSS 制表符表格 / GFM 表格契约 / 无匹配降级。
  B. 🔴 F↔η² 四分支回归：修好前，三组 ANOVA 的**正确** η² 会被判「矛盾」（误报）。
     本文件用 df1≥2 的用例把这条锁住 —— 既有的 test_validators 全是 df1=1，正是
     bug 长期未暴露的原因。
"""

from __future__ import annotations

import pytest

from vermes_cli.scholarforge.stats_table import (
    build_stats_report,
    detect_table_rows,
    parse_inline_stats,
    to_gfm_table,
)
from vermes_cli.scholarforge.validators import check_statistics_consistency


# ── A. 行内统计量解析 ────────────────────────────────────────────────────

class TestParseInlineStats:
    def test_t_test_with_df(self):
        s = parse_inline_stats("实验组后测显著高于对照组，t(58)=2.34, p=.023, d=0.61")
        assert s["t_value"] == pytest.approx(2.34)
        assert s["df"] == 58
        assert s["p_value"] == pytest.approx(0.023)
        assert s["cohens_d"] == pytest.approx(0.61)

    def test_anova_takes_both_df(self):
        """F(2,87) 必须同时取出组间与误差自由度 —— 组间自由度是 F↔η² 换算的关键。"""
        s = parse_inline_stats("三组间差异显著，F(2,87)=4.12, p=.019, η²=.087")
        assert s["f_value"] == pytest.approx(4.12)
        assert s["df_between"] == 2
        assert s["df_error"] == 87
        assert s["eta_squared"] == pytest.approx(0.087)

    def test_correlation_with_inequality_p(self):
        """p<.01 是上界不是点值：数值取上界，且必须保留原始算子。"""
        s = parse_inline_stats("两者呈显著正相关，r=.56, p<.01, n=60")
        assert s["r_value"] == pytest.approx(0.56)
        assert s["p_value"] == pytest.approx(0.01)
        assert s["p_op"] == "<"
        assert s["n"] == 60

    def test_fullwidth_characters(self):
        """中文输入法下的全角 = （ ） ＜ 必须也能解析。"""
        s = parse_inline_stats("t（58）＝2.34，p＜0.01")
        assert s["t_value"] == pytest.approx(2.34)
        assert s["df"] == 58
        assert s["p_op"] == "<"

    def test_case_sensitive_m_vs_m(self):
        """`M =` 与 `SD =` 是学术固定写法；不能让 `d =` 误匹配 `df =`。"""
        s = parse_inline_stats("M = 3.45, SD = 0.71, df = 58")
        assert s["mean"] == pytest.approx(3.45)
        assert s["sd"] == pytest.approx(0.71)
        assert "cohens_d" not in s  # df=58 不得被当成 Cohen's d

    def test_chinese_stat_words(self):
        s = parse_inline_stats("均值 3.45，标准差 0.71")
        assert s["mean"] == pytest.approx(3.45)
        assert s["sd"] == pytest.approx(0.71)

    def test_no_match_returns_empty(self):
        assert parse_inline_stats("今天天气不错，我们去吃饭吧") == {}


# ── B. 表格形态识别 ──────────────────────────────────────────────────────

class TestDetectTableRows:
    def test_spss_table_with_leading_empty_header_cell(self):
        """🔴 回归：SPSS 表头行首常有一个空单元格（行标签列占位）。

        表头 `\\t均值\\t标准差\\tt\\tdf\\tSig.` = 6 列，数据行也是 6 列。
        若把空串过滤掉，表头变 5 列 → 列数不一致 → 整表被判为非表格。
        """
        text = (
            "成对样本检验\n"
            "\t均值\t标准差\tt\tdf\tSig.(双尾)\n"
            "前测-后测\t-.74\t.83\t-6.92\t39\t.000\n"
            "后测-追踪\t.61\t.79\t5.11\t39\t.000"
        )
        rows = detect_table_rows(text)
        assert len(rows) == 3
        assert [len(r) for r in rows] == [6, 6, 6]

    def test_prose_is_not_a_table(self):
        """普通段落不能被误判成表格。"""
        assert detect_table_rows("这是一段普通的话，里面没有表格。") == []

    def test_single_row_is_not_a_table(self):
        assert detect_table_rows("均值    标准差") == []

    def test_two_space_separated_table(self):
        rows = detect_table_rows("组别  均值  标准差\n实验组  4.12  0.55\n对照组  3.51  0.58")
        assert len(rows) == 3
        assert rows[1] == ["实验组", "4.12", "0.55"]


# ── C. GFM 表格契约（对齐 export/full.py::_add_markdown_table）──────────

class TestGfmTable:
    def test_header_and_separator_first(self):
        md = to_gfm_table([["组别", "M"], ["实验组", "4.12"]], caption="表 3-1")
        lines = md.splitlines()
        assert lines[0] == "**表 3-1**"
        assert lines[2] == "| 组别 | M |"
        assert lines[3] == "| --- | --- |"
        assert lines[4] == "| 实验组 | 4.12 |"

    def test_pipe_is_escaped(self):
        """单元格里的 | 必须转义，否则会切碎表格结构。"""
        md = to_gfm_table([["a", "b"], ["x|y", "z"]])
        assert r"x\|y" in md

    def test_ragged_rows_are_padded(self):
        md = to_gfm_table([["a", "b", "c"], ["1"]])
        assert md.splitlines()[-1] == "| 1 |  |  |"


# ── D. 端到端报告 ────────────────────────────────────────────────────────

class TestBuildStatsReport:
    def test_table_input_produces_gfm(self):
        r = build_stats_report("组别\tM\tSD\n实验组\t4.12\t0.55\n对照组\t3.51\t0.58")
        assert "| 组别 | M | SD |" in r
        assert "4.12" in r

    def test_inline_input_produces_summary_table(self):
        r = build_stats_report("t(58)=2.34, p=.023, d=0.61")
        assert "| 统计量 | 值 |" in r
        assert "t 值" in r

    def test_unparseable_gives_guidance_not_silence(self):
        """🔴 解析不出来时必须给出「怎么给数据」的指引，而不是返回空串。"""
        r = build_stats_report("你好啊")
        assert "未识别到统计量或表格" in r
        assert "SPSS" in r

    def test_empty_input(self):
        assert "未识别到统计量或表格" in build_stats_report("")

    def test_mentions_three_line_table_conversion(self):
        r = build_stats_report("t(58)=2.34, p=.023")
        assert "三线表" in r

    def test_numbers_are_not_reformatted(self):
        """学术数据保真：`.83` 不得被写成 `0.83`。"""
        r = build_stats_report("组别\tM\nA\t.83\nB\t.74")
        assert ".83" in r and "0.83" not in r


# ── E. 🔴 F↔η² 四分支回归（本文件的核心价值）────────────────────────────

def _eta_check(stats: dict):
    checks = [c for c in check_statistics_consistency(stats) if "η²" in c.metric]
    assert checks, "应产出 F↔η² 检查项"
    return checks[0]


class TestEtaSquaredBranches:
    """修正前：η² = F/(F+df_error) 漏了组间自由度。

    F=8.0, df1=2, df2=87 时真值 η² = F·df1/(F·df1+df2) = 0.1553，
    而旧式给 0.0842 —— 差 0.0711 > 旧容差 0.05 → **把正确报告判成矛盾**。
    """

    F, DF1, DF2 = 8.0, 2, 87

    @property
    def true_eta(self) -> float:
        return round(self.F * self.DF1 / (self.F * self.DF1 + self.DF2), 4)  # 0.1553

    def test_branch1_three_group_correct_value_is_consistent(self):
        """① 传了 df_between 且报告值正确 → 一致。

        🔴 这条就是旧实现的失败点：修好前会被判「矛盾」。
        """
        c = _eta_check({"f_value": self.F, "df_between": self.DF1,
                        "df_error": self.DF2, "eta_squared": self.true_eta})
        assert c.consistent is True, f"正确值被误判：{c.explanation}"

    def test_branch1_three_group_wrong_value_is_inconsistent(self):
        """① 传了 df_between 且报告值确实错 → 矛盾（不能因为修误报就漏报）。"""
        c = _eta_check({"f_value": self.F, "df_between": self.DF1,
                        "df_error": self.DF2, "eta_squared": 0.05})
        assert c.consistent is False

    def test_branch2_two_group_correct_value(self):
        """② 未传 df_between，值与下界吻合 → 一致（两组场景的行为不能变）。"""
        c = _eta_check({"f_value": 4.12, "df_error": 58, "eta_squared": 0.0663})
        assert c.consistent is True

    def test_branch3_below_lower_bound_is_inconsistent(self):
        """③ 低于 df1=1 的理论下界 → 必然矛盾（η² 关于 df1 单调递增，df1≥1）。"""
        c = _eta_check({"f_value": 4.12, "df_error": 58, "eta_squared": 0.02})
        assert c.consistent is False
        assert "下界" in c.value_expected

    def test_branch4_above_lower_bound_is_not_asserted(self):
        """④ 未传 df_between 且高于下界 → 明确标注「未校验」，不猜对错。

        三组场景（真值 0.1553 > 下界 0.0842）必须落在这里，而不是被判矛盾。
        """
        c = _eta_check({"f_value": self.F, "df_error": self.DF2,
                        "eta_squared": self.true_eta})
        assert c.consistent is True
        assert "未校验" in c.metric
        assert "df_between" in c.explanation

    def test_df_between_is_parsed_and_fed_end_to_end(self):
        """端到端接力：文本里的 F(2,87) 解析出 df1=2 并送进校验器，走分支①。"""
        report = build_stats_report("三组间差异显著，F(2,87)=4.12, p=.019, η²=.087")
        assert "df1=2" in report          # 证明用了组间自由度，不是假定 df1=1
        assert "**矛盾**: 0" in report

    def test_end_to_end_catches_wrong_eta(self):
        report = build_stats_report("三组间差异显著，F(2,87)=4.12, p=.019, η²=.20")
        assert "**矛盾**: 1" in report
