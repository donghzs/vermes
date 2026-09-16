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


# ════════════════════════════════════════════════════════════════════════
# 2026-09-17 第十轮：SPSS 表型扩展（成对样本 / 卡方 / 相关矩阵 / GLM 主体间 /
# 重复测量主体内）+ 脚注·显著性标记剥离 + χ²↔p、r↔p 两个新校验。
#
# 🔴 这些表此前**全部静默返回空** —— 表格能显示、统计量一个提不出来，
#    于是"一致性校验一次都没跑过"，用户却以为校验过了。本组用例即为此而设。
# ════════════════════════════════════════════════════════════════════════

T = "\t"

# 成对样本检验：表头有**两层**（第 1 行是「成对差值」大标题，第 2 行才是列名）
SPSS_PAIRED = (
    "成对样本检验\n"
    + T + T + "成对差值" + T * 7 + "\n"
    + T + T + "平均值" + T + "标准差" + T + "标准误差平均值" + T + "下限"
    + T + "上限" + T + "t" + T + "自由度" + T + "显著性（双尾）" + "\n"
    + "配对 1" + T + "前测成绩 - 后测成绩" + T + "-3.250" + T + "4.120" + T
    + ".923" + T + "-5.337" + T + "-1.163" + T + "-3.522" + T + "15" + T + ".003"
)

# 卡方检验：χ² 单元格带脚注字母 `6.857a`；另有似然比/线性关联两行（都不是皮尔逊卡方）
SPSS_CHI = (
    "卡方检验\n"
    + T + "值" + T + "自由度" + T + "显著性（双尾）" + "\n"
    + "皮尔逊卡方" + T + "6.857a" + T + "1" + T + ".009" + "\n"
    + "似然比" + T + "7.021" + T + "1" + T + ".008" + "\n"
    + "线性关联" + T + "6.743" + T + "1" + T + ".009" + "\n"
    + "有效个案数" + T + "60" + T + T
)

# 相关矩阵：变量名在**列**；每变量占三行，变量名只写在块首行
SPSS_CORR2 = (
    "相关性\n"
    + T + T + "成绩" + T + "学习动机" + "\n"
    + "成绩" + T + "皮尔逊相关性" + T + "1" + T + ".482**" + "\n"
    + T + "Sig.（双尾）" + T + T + ".000" + "\n"
    + T + "个案数" + T + "60" + T + "60" + "\n"
    + "学习动机" + T + "皮尔逊相关性" + T + ".482**" + T + "1" + "\n"
    + T + "Sig.（双尾）" + T + ".000" + T + "\n"
    + T + "个案数" + T + "60" + T + "60"
)

SPSS_CORR3 = (
    "相关性\n"
    + T + T + "成绩" + T + "动机" + T + "焦虑" + "\n"
    + "成绩" + T + "皮尔逊相关性" + T + "1" + T + ".482**" + T + "-.310*" + "\n"
    + T + "个案数" + T + "60" + T + "60" + T + "60" + "\n"
    + "动机" + T + "皮尔逊相关性" + T + ".482**" + T + "1" + T + "-.120" + "\n"
    + T + "个案数" + T + "60" + T + "60" + T + "60" + "\n"
    + "焦虑" + T + "皮尔逊相关性" + T + "-.310*" + T + "-.120" + T + "1" + "\n"
    + T + "个案数" + T + "60" + T + "60" + T + "60"
)

# GLM 单变量「主体间效应检验」：没有「组之间」行，效应行标签是用户自己的变量名
SPSS_GLM = (
    "主体间效应检验\n"
    + "因变量:   成绩\n"
    + "源" + T + "III 类平方和" + T + "自由度" + T + "均方" + T + "F" + T + "显著性" + "\n"
    + "修正模型" + T + "245.750a" + T + "3" + T + "81.917" + T + "8.145" + T + ".000" + "\n"
    + "截距" + T + "10215.000" + T + "1" + T + "10215.000" + T + "1015.630" + T + ".000" + "\n"
    + "组别" + T + "180.500" + T + "2" + T + "90.250" + T + "8.971" + T + ".000" + "\n"
    + "误差" + T + "560.250" + T + "56" + T + "10.004" + T + T + "\n"
    + "总计" + T + "11250.000" + T + "60" + T * 3
)

# 重复测量「主体内效应检验」：同一效应有 4 行（球形度成立 + 3 种校正）
SPSS_RM = (
    "主体内效应检验\n"
    + "度量:   成绩\n"
    + "源" + T + T + "III 类平方和" + T + "自由度" + T + "均方" + T + "F" + T + "显著性" + "\n"
    + "时间" + T + "采用的球形度" + T + "45.625" + T + "2" + T + "22.813" + T + "8.145" + T + ".001" + "\n"
    + T + "格林豪斯-盖斯勒" + T + "45.625" + T + "1.542" + T + "29.588" + T + "8.145" + T + ".002" + "\n"
    + T + "辛-费德特" + T + "45.625" + T + "1.698" + T + "26.868" + T + "8.145" + T + ".001" + "\n"
    + T + "下限" + T + "45.625" + T + "1.000" + T + "45.625" + T + "8.145" + T + ".007" + "\n"
    + "误差(时间)" + T + "采用的球形度" + T + "145.750" + T + "58" + T + "2.513" + T + T + "\n"
    + T + "格林豪斯-盖斯勒" + T + "145.750" + T + "44.718" + T + "3.259" + T + T + "\n"
    + T + "下限" + T + "145.750" + T + "29.000" + T + "5.026" + T + T
)


def _extract(text: str) -> dict:
    from vermes_cli.scholarforge.stats_table import (
        detect_table_rows, extract_table_stats,
    )
    return extract_table_stats(detect_table_rows(text))


class TestSpssPairedSamples:
    """成对样本检验：表头在**第 2 行**，旧实现固定取 rows[0] → 整表提取为空。"""

    def test_two_row_header_is_located(self):
        s = _extract(SPSS_PAIRED)
        assert s["t_value"] == pytest.approx(-3.522)
        assert s["df"] == 15
        assert s["p_value"] == pytest.approx(0.003)

    def test_header_rows_are_not_mistaken_for_data(self):
        """不可能取到「平均值 / 标准差」这些表头词当数值。"""
        s = _extract(SPSS_PAIRED)
        assert s["_source_row"] == "配对 1（前测成绩 - 后测成绩）"

    def test_paired_p_value_is_checkable(self):
        """端到端：t=-3.522, df=15 的精确双尾 p 与报告的 .003 一致 → 无矛盾。"""
        payload = {k: v for k, v in _extract(SPSS_PAIRED).items()
                   if k in ("t_value", "df", "p_value")}
        assert check_statistics_consistency(payload) == []


class TestSpssChiSquare:
    """卡方检验：χ² 单元格带脚注字母；只有「皮尔逊卡方」行是标准 χ²。"""

    def test_pearson_row_is_picked(self):
        s = _extract(SPSS_CHI)
        assert s["chi_square"] == pytest.approx(6.857)
        assert s["df"] == 1
        assert s["p_value"] == pytest.approx(0.009)

    def test_not_likelihood_ratio_or_linear_by_linear(self):
        """似然比 7.021 / 线性关联 6.743 是**别的检验**，取它们就答非所问。"""
        s = _extract(SPSS_CHI)
        assert s["chi_square"] != pytest.approx(7.021)
        assert s["chi_square"] != pytest.approx(6.743)

    def test_footnote_letter_is_stripped(self):
        """`6.857a` 若解析失败，整张表一个数字都提不出来（实测如此）。"""
        from vermes_cli.scholarforge.stats_table import _f
        assert _f("6.857a") == pytest.approx(6.857)
        assert _f(".482**") == pytest.approx(0.482)
        assert _f("-3.250") == pytest.approx(-3.250)

    def test_footnote_is_not_leaked_into_the_table(self):
        """三线表里出现 `6.857a` 会被当成笔误 —— 装饰要剥，数字要保。"""
        s = _extract(SPSS_CHI)
        assert s["_raw"]["chi_square"] == "6.857"

    def test_valid_count_row_is_not_treated_as_n(self):
        """「有效个案数 60」不是本行的样本量；卡方表不该产出 n。"""
        assert "n" not in _extract(SPSS_CHI)


class TestSpssCorrelationMatrix:
    """相关矩阵：变量名在列；块内三行，变量名只写在块首行。"""

    def test_first_pair_is_extracted(self):
        s = _extract(SPSS_CORR2)
        assert s["r_value"] == pytest.approx(0.482)
        assert s["n"] == 60
        assert s["p_value"] == pytest.approx(0.001)
        assert s["p_zero_display"] is True

    def test_pair_is_named(self):
        assert _extract(SPSS_CORR2)["_source_row"] == "成绩 × 学习动机"

    def test_diagonal_is_not_taken_as_r(self):
        """对角线恒为 1，取它当 r 会得到 r=1 的荒谬结果。"""
        assert abs(_extract(SPSS_CORR2)["r_value"]) != pytest.approx(1.0)

    def test_sig_row_comes_from_the_same_block(self):
        """学习动机块的 Sig. 不能喂给成绩块 —— 块必须按行序继承。"""
        s = _extract(SPSS_CORR2)
        assert s["_source_row"].startswith("成绩")
        assert s["p_zero_display"] is True  # 成绩×学习动机那一格是 .000

    def test_multi_pair_matrix_is_disclosed(self):
        """3 变量矩阵有 3 对相关，只校第 1 对 —— 必须显式告知，不能假装全校了。"""
        s = _extract(SPSS_CORR3)
        assert s["_corr_pairs"] == 2
        assert s["r_value"] == pytest.approx(0.482)

    def test_multi_pair_note_appears_in_report(self):
        r = build_stats_report(SPSS_CORR3)
        assert "本表共 2 对相关" in r


class TestSpssGlmBetweenSubjects:
    """GLM 单变量「主体间效应检验」：没有「组之间」行，效应名是用户变量名。"""

    def test_effect_row_is_picked(self):
        s = _extract(SPSS_GLM)
        assert s["f_value"] == pytest.approx(8.971)
        assert s["df_between"] == 2
        assert s["df_error"] == 56

    def test_not_model_intercept_or_total(self):
        """修正模型 8.145（整体）/ 截距 1015.630 都不是「组别」这个效应的 F。"""
        s = _extract(SPSS_GLM)
        assert s["f_value"] != pytest.approx(8.145)
        assert s["f_value"] != pytest.approx(1015.630)
        assert s["_source_row"] == "组别"

    def test_unknown_effect_name_without_error_row_yields_nothing(self):
        """🔴 宁可漏：连误差行都认不出来的表，不给"没验过的数"。"""
        from vermes_cli.scholarforge.stats_table import extract_table_stats
        rows = [
            ["项目", "平方和", "自由度", "均方", "F", "显著性"],
            ["甲", "1.0", "1", "1.0", "3.0", ".100"],
            ["乙", "2.0", "1", "2.0", "", ""],
        ]
        assert extract_table_stats(rows) == {}

    def test_multi_effect_table_is_disclosed(self):
        """两因素 ANOVA 有多个效应行，只校第一个，其余要点名告知。"""
        txt = (
            "主体间效应检验\n"
            + "源" + T + "III 类平方和" + T + "自由度" + T + "均方" + T + "F" + T + "显著性" + "\n"
            + "教法" + T + "120.000" + T + "1" + T + "120.000" + T + "6.500" + T + ".013" + "\n"
            + "性别" + T + "60.500" + T + "1" + T + "60.500" + T + "3.280" + T + ".075" + "\n"
            + "误差" + T + "1000.000" + T + "56" + T + "17.857" + T + T + "\n"
        )
        s = _extract(txt)
        assert s["_source_row"] == "教法"
        assert s["_extra_effects"] == ["性别"]
        assert "性别" in build_stats_report(txt)


class TestSpssRepeatedMeasures:
    """重复测量「主体内效应检验」：同一效应 4 行，只取「采用的球形度」。"""

    def test_sphericity_assumed_row_is_picked(self):
        s = _extract(SPSS_RM)
        assert s["f_value"] == pytest.approx(8.145)
        assert s["df_between"] == 2          # 不是 1.542（格林豪斯-盖斯勒）
        assert s["df_error"] == 58           # 来自「误差(时间)」

    def test_corrections_are_skipped(self):
        """三种校正行的 df 被 ε 修正过，混用会让 p↔F 对不上。"""
        s = _extract(SPSS_RM)
        assert s["df_between"] != pytest.approx(1.542)
        assert s["df_between"] != pytest.approx(1.000)

    def test_correction_only_paste_yields_nothing(self):
        """🔴 只粘到校正行（球形度不成立时论文确实会报 G-G）：宁可漏，不猜是哪一种。

        三种校正的 df 被 ε 修正过（1.542 / 1.698 / 1.000），而我们无从判断论文
        报的是哪一种 —— 当成未校正的 df 去校 p↔F，会得到一个既非未校正、
        也非用户实际所报的结果。故只认「采用的球形度」，没有就返回空。

        ⚠️ 这条用例是本类里**唯一**真正压住 `_ROW_SPHERE_CORR` 的：
        SPSS 总把「采用的球形度」排在最前，完整表**轮不到**校正行，
        只靠完整表验证会误以为守住了（实测 M3 变异即因此漏网）。
        """
        txt = (
            "主体内效应检验\n"
            + "源" + T + T + "III 类平方和" + T + "自由度" + T + "均方" + T + "F" + T + "显著性" + "\n"
            + "时间" + T + "格林豪斯-盖斯勒" + T + "45.625" + T + "1.542" + T + "29.588"
            + T + "8.145" + T + ".002" + "\n"
            + "误差(时间)" + T + "采用的球形度" + T + "145.750" + T + "58" + T + "2.513" + T + T
        )
        assert _extract(txt) == {}

    def test_error_row_is_distinguished_by_main_label(self):
        """效应行与误差行的**子标签都是**「采用的球形度」，只能靠主标签区分。"""
        s = _extract(SPSS_RM)
        assert s["_source_row"] == "时间（采用的球形度）"


class TestChiSquareAndRConsistency:
    """2026-09-17 新增的两个校验：χ²↔p（纯 Python 上不完全 Gamma）与 r↔p。"""

    @staticmethod
    def _issues(**kw) -> list:
        return [c for c in check_statistics_consistency(kw) if not c.consistent]

    # ── 精度自证：对照统计学标准临界值 ──
    def test_chi2_p_matches_standard_critical_values(self):
        from vermes_cli.scholarforge.validators import chi2_p_right_tail
        assert chi2_p_right_tail(3.841459, 1) == pytest.approx(0.05, abs=1e-6)
        assert chi2_p_right_tail(6.634897, 1) == pytest.approx(0.01, abs=1e-6)
        assert chi2_p_right_tail(5.991465, 2) == pytest.approx(0.05, abs=1e-6)
        assert chi2_p_right_tail(7.814728, 3) == pytest.approx(0.05, abs=1e-6)
        assert chi2_p_right_tail(11.070498, 5) == pytest.approx(0.05, abs=1e-6)

    def test_r_p_equals_t_conversion(self):
        """r↔p 必须严格等于 t = r√((n−2)/(1−r²)) 的 t 双尾 p。"""
        from vermes_cli.scholarforge.validators import r_p_two_tailed, t_p_two_tailed
        r, n = 0.482, 60
        t = r * ((n - 2) / (1 - r * r)) ** 0.5
        assert r_p_two_tailed(r, n) == pytest.approx(t_p_two_tailed(t, n - 2), rel=1e-12)

    # ── 正向对照：确认校验真的会触发，不是空转 ──
    def test_chi2_catches_significant_reported_as_not(self):
        """χ²=6.857,df=1 精确 p=.0088（显著）却报 .919（不显著）→ 必须判矛盾。"""
        issues = self._issues(chi_square=6.857, df=1, p_value=0.919)
        assert issues and "χ²" in issues[0].metric

    def test_r_catches_significant_reported_as_not(self):
        issues = self._issues(r_value=0.482, n=60, p_value=0.919)
        assert issues and "r" in issues[0].metric

    # ── 反向对照：正确报告不得误伤 ──
    def test_chi2_correct_report_not_flagged(self):
        assert self._issues(chi_square=6.857, df=1, p_value=0.009) == []

    def test_r_correct_report_not_flagged(self):
        assert self._issues(r_value=0.482, n=60, p_value=0.0001) == []

    def test_r_upper_bound_not_flagged(self):
        """「p < .001」是上界写法，不得当成抄错。"""
        assert self._issues(r_value=0.482, n=60, p_value=0.001) == []

    # ── 边界：缺参数 / 非法输入不得抛异常，也不得乱判 ──
    def test_r_without_n_is_not_checked(self):
        """没有 n 就换算不出 t —— 不校验，不猜。"""
        assert self._issues(r_value=0.482, p_value=0.919) == []

    def test_chi2_without_df_is_not_checked(self):
        assert self._issues(chi_square=6.857, p_value=0.919) == []

    def test_invalid_inputs_return_nan(self):
        from vermes_cli.scholarforge.validators import chi2_p_right_tail, r_p_two_tailed
        assert chi2_p_right_tail(1.0, 0) != chi2_p_right_tail(1.0, 0)   # NaN
        assert chi2_p_right_tail(-1.0, 2) != chi2_p_right_tail(-1.0, 2)
        assert r_p_two_tailed(1.5, 60) != r_p_two_tailed(1.5, 60)       # |r|≥1
        assert r_p_two_tailed(0.5, 2) != r_p_two_tailed(0.5, 2)         # n≤2


class TestNewTableTypesEndToEnd:
    """端到端：粘表 → 出表 + 校验真的跑了（不是空转）。"""

    def test_chi_square_report_runs_consistency(self):
        r = build_stats_report(SPSS_CHI)
        assert "6.857" in r
        assert "统计一致性校验" in r

    def test_paired_samples_report_runs_consistency(self):
        r = build_stats_report(SPSS_PAIRED)
        assert "-3.522" in r
        assert "统计一致性校验" in r

    def test_glm_report_runs_consistency_and_derives_eta(self):
        r = build_stats_report(SPSS_GLM)
        assert "8.971" in r
        assert "η²" in r          # F+df 推算出的效应量
        assert "统计一致性校验" in r
