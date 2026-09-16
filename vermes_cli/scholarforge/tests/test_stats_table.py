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


# ════════════════════════════════════════════════════════════════════════
# F. 🔴 续轮（2026-09-17）：粘贴 SPSS 表格时的一致性校验
#
# 背景：detect_table_rows 早就能识别 SPSS 复制出来的表，但 build_stats_report
# 只拿 parse_inline_stats 的结果去做校验 —— 后者认的是正文写法 `F(2,87)=4.12`，
# 而 SPSS 表里的数是**躺在列里的裸值**。于是粘贴表格时 stats 恒为空 →
# **一致性校验一次都没跑过**，用户却以为校验过了（虚假安全感）。
# ════════════════════════════════════════════════════════════════════════

# 真实 SPSS 中文输出的两张表（制表符分隔；末行结尾的空单元格是合法列占位）
SPSS_ANOVA = (
    "ANOVA\n\n反应时\n"
    "\t平方和\t自由度\t均方\tF\t显著性\n"
    "组之间\t1234.567\t2\t617.284\t4.123\t.019\n"
    "组内\t13023.456\t87\t149.694\t\t\n"
    "总计\t14258.023\t89\t\t\t\n"
)

SPSS_TTEST = (
    "独立样本检验\n\n"
    "\t\tF\t显著性\tt\t自由度\tSig.（双尾）\n"
    "成绩\t假定等方差\t1.234\t.271\t2.456\t78\t.016\n"
    "\t不假定等方差\t\t\t2.441\t72.345\t.017\n"
)


class TestSpssTableRowPreservation:
    """🔴 回归：行尾空单元格是**列占位**，不能当空白裁掉。

    旧实现对整段 .strip() + 每行 rstrip()，把「组内」「总计」两行结尾的 tab
    吃掉 → 列数从 6 掉到 3/4 → 被列数过滤**整行丢弃**（4 行只剩 2 行），
    而 F 的分母自由度 df_error 正来自「组内」行。
    """

    def test_anova_keeps_all_four_rows(self):
        rows = detect_table_rows(SPSS_ANOVA)
        assert len(rows) == 4, f"ANOVA 表应保留 4 行，实际 {len(rows)}：{rows}"

    def test_error_row_survives_so_df_error_is_available(self):
        """「组内」行必须活下来，否则 df_error 取不到 → F↔η² / p↔F 都算不了。"""
        from vermes_cli.scholarforge.stats_table import extract_table_stats
        stats = extract_table_stats(detect_table_rows(SPSS_ANOVA))
        assert stats.get("df_error") == 87

    def test_trailing_tab_cells_preserved(self):
        """末行 `总计\\t14258.023\\t89\\t\\t\\t` 的三个空单元格必须保留。"""
        rows = detect_table_rows(SPSS_ANOVA)
        total_row = [r for r in rows if r and r[0] == "总计"][0]
        assert len(total_row) == 6


class TestSpssTableExtraction:
    def test_anova_extracts_f_and_both_dfs(self):
        from vermes_cli.scholarforge.stats_table import extract_table_stats
        s = extract_table_stats(detect_table_rows(SPSS_ANOVA))
        assert s["f_value"] == pytest.approx(4.123)
        assert s["df_between"] == 2
        assert s["df_error"] == 87
        assert s["p_value"] == pytest.approx(0.019)

    def test_ttest_picks_equal_variance_row(self):
        """独立样本检验有两行，必须取「假定等方差」，不能取 Welch 那行。"""
        from vermes_cli.scholarforge.stats_table import extract_table_stats
        s = extract_table_stats(detect_table_rows(SPSS_TTEST))
        assert s["t_value"] == pytest.approx(2.456)   # 不是 2.441（不假定等方差行）
        assert s["df"] == 78                           # 不是 72.345
        assert s["p_value"] == pytest.approx(0.016)

    def test_levene_f_is_not_mistaken_for_the_mean_test(self):
        """🔴 表里同时有 F 和 t 时，F 是**莱文方差齐性检验**，不是均值差异检验。

        若把莱文的 F=1.234 配 t 的 df=78 去做 F↔η² / p↔F，会算出根本不存在的检验。
        故有 t 列时走 t 分支，不产出 f_value。
        """
        from vermes_cli.scholarforge.stats_table import extract_table_stats
        s = extract_table_stats(detect_table_rows(SPSS_TTEST))
        assert "f_value" not in s

    def test_rightmost_p_pairs_with_t(self):
        """t 的 p 应取最右侧的 Sig.（双尾），不是莱文那个「显著性」(.271)。"""
        from vermes_cli.scholarforge.stats_table import extract_table_stats
        s = extract_table_stats(detect_table_rows(SPSS_TTEST))
        assert s["p_value"] == pytest.approx(0.016)   # 不是 0.271

    def test_unknown_row_labels_yield_nothing(self):
        """认不出「组之间 / 组内」行标签时**宁可漏**，绝不盲取第一行。"""
        from vermes_cli.scholarforge.stats_table import extract_table_stats
        rows = [
            ["项目", "平方和", "自由度", "均方", "F", "显著性"],
            ["甲", "1.0", "1", "1.0", "3.0", ".100"],
            ["乙", "2.0", "1", "2.0", "", ""],
        ]
        assert extract_table_stats(rows) == {}

    def test_p_zero_display_becomes_upper_bound(self):
        """SPSS 的 `.000` 是三位小数舍入显示 = p < .001，不是 p = 0。"""
        from vermes_cli.scholarforge.stats_table import extract_table_stats
        tbl = (
            "\t平方和\t自由度\t均方\tF\t显著性\n"
            "组之间\t88.533\t2\t44.267\t12.407\t.000\n"
            "组内\t310.800\t87\t3.572\t\t\n"
        )
        s = extract_table_stats(detect_table_rows(tbl))
        assert s["p_op"] == "<"
        assert s["p_value"] == pytest.approx(0.001)
        assert s.get("p_zero_display") is True

    def test_descriptive_stats_row(self):
        from vermes_cli.scholarforge.stats_table import extract_table_stats
        tbl = "\t个案数\t平均值\t标准差\n前测成绩\t40\t19.85\t3.42\n"
        s = extract_table_stats(detect_table_rows(tbl))
        assert s["n"] == 40
        assert s["mean"] == pytest.approx(19.85)
        assert s["sd"] == pytest.approx(3.42)


class TestSpssEndToEndConsistency:
    """端到端：粘贴 SPSS 表 → 一致性校验真的跑起来，且真能抓错。"""

    def test_pasted_anova_now_produces_a_check(self):
        """🔴 本轮核心修复：以前粘贴表格时这一段**根本不会出现**。"""
        r = build_stats_report(SPSS_ANOVA)
        assert "统计一致性校验" in r
        assert "从表中提取到的统计量" in r

    def test_wrong_p_in_anova_table_is_caught(self):
        """把显著的 F（真值 p≈.019）写成 .919 —— 必须抓。"""
        bad = SPSS_ANOVA.replace("4.123\t.019", "4.123\t.919")
        r = build_stats_report(bad)
        assert "**矛盾**: 1" in r
        assert "p值 ↔ F统计量" in r

    def test_wrong_sig_in_ttest_table_is_caught(self):
        bad = SPSS_TTEST.replace("2.456\t78\t.016", "2.456\t78\t.916")
        r = build_stats_report(bad)
        assert "**矛盾**: 1" in r
        assert "p值 ↔ t统计量" in r

    def test_correct_tables_pass(self):
        # 注意：一致性校验器**只在矛盾时**产出条目，一致时返回空列表 →
        # 报告里是「✅ 未发现统计指标矛盾」，而不会出现「**矛盾**: 0」。
        for tbl in (SPSS_ANOVA, SPSS_TTEST):
            assert "🔴 发现矛盾" not in build_stats_report(tbl)

    def test_source_row_is_disclosed(self):
        """数字保真：必须告诉用户数字取自哪一行，便于回表核对。"""
        r = build_stats_report(SPSS_ANOVA)
        assert "组之间" in r


class TestNumberFidelityInExtractedTable:
    """学术数据保真：`.83` 不得被改写成 `0.83`。"""

    def test_leading_dot_preserved_through_extraction(self):
        r = build_stats_report("组别\tM\nA\t.83\nB\t.74")
        assert ".83" in r
        assert "0.83" not in r

    def test_anova_raw_cells_preserved(self):
        r = build_stats_report(SPSS_ANOVA)
        assert "4.123" in r and ".019" in r


class TestDerivedEtaSquared:
    """η² 由 F + 自由度推算 —— 但**绝不**参与一致性校验。"""

    def test_eta_is_derived(self):
        r = build_stats_report(SPSS_ANOVA)
        # F=4.123, df1=2, df2=87 → η² = 8.246/95.246 ≈ 0.087
        assert "0.087" in r
        assert "推算" in r

    def test_excluded_from_consistency(self):
        """🔴 推算值若喂回校验 = 自己验自己，恒真通过 → 校验被架空。

        这里塞一个**离谱**的 η²=0.999 并标为推算值：必须**不产出** η² 校验项。
        """
        from vermes_cli.scholarforge.stats_table import consistency_block
        stats = {"f_value": 4.123, "df_between": 2, "df_error": 87,
                 "eta_squared": 0.999, "_derived": ["eta_squared"]}
        assert "η²" not in consistency_block(stats)

    def test_positive_control_same_wrong_eta_would_be_caught(self):
        """正对照：同样的 η²=0.999 若**不是**推算值，必须被抓。
        否则上一条就是空断言（因为校验器根本不检查 η²）。"""
        checks = check_statistics_consistency(
            {"f_value": 4.123, "df_between": 2, "df_error": 87, "eta_squared": 0.999}
        )
        assert any(not c.consistent for c in checks), "正对照失效：错误的 η² 竟未被抓"


class TestPVerdictThreshold:
    """2026-09-17 放宽 `_p_verdict` 的「过大」门槛。

    旧门槛 `expected < 0.001` 放过了整整一类最严重的抄错：
    真值显著（如 .019）却报告成不显著（如 .919）。而 `expected < 0.001`
    对避「上界写法」并**无必要** —— 上界污染由 `reported > 0.05` 单独挡住。
    """

    def _p_checks(self, expected_t=None, df=None, reported=None, f=None, d1=None, d2=None):
        if f is not None:
            payload = {"f_value": f, "df_between": d1, "df_error": d2, "p_value": reported}
        else:
            payload = {"t_value": expected_t, "df": df, "p_value": reported}
        return [c for c in check_statistics_consistency(payload)
                if "p值" in c.metric and not c.consistent]

    def test_significant_but_reported_nonsignificant_is_caught(self):
        """🔴 新增覆盖：真值 p≈.019，报告 .919（旧规则放过，因为 .019 不小于 .001）"""
        assert self._p_checks(f=4.123, d1=2, d2=87, reported=0.919)

    def test_borderline_flip_not_flagged(self):
        """临界抖动（.0498 vs .051，差 1.02 倍）不得误伤 —— ×5 余量的作用。"""
        from vermes_cli.scholarforge.validators import t_p_two_tailed
        t = 2.23  # df=10 时双尾 p = 0.04984（刚过 α，临界值 t_crit = 2.228）
        assert t_p_two_tailed(t, 10) < 0.05
        assert self._p_checks(expected_t=t, df=10, reported=0.051) == []

    def test_bonferroni_like_3x_not_flagged(self):
        """多重比较校正通常抬高 3–5 倍，属合法差异，不得误伤。"""
        assert self._p_checks(f=4.123, d1=2, d2=87, reported=0.019 * 3) == []

    def test_still_catches_extreme_case(self):
        """旧实现唯一覆盖的极端场景不能丢（t=5,df=100 精确 p=2.45e-6，报 0.5）"""
        assert self._p_checks(expected_t=5.0, df=100, reported=0.5)
