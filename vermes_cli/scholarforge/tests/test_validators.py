"""
ScholarForge 验证器测试 — validators.py
测试三个验证器的核心功能
"""
import asyncio
import math
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from vermes_cli.scholarforge.validators import (
    CitationCheck,
    StatCheck,
    DesignFlaw,
    verify_citation_authenticity,
    check_statistics_consistency,
    detect_design_flaws,
    format_citation_report,
    format_statistics_report,
    format_design_report,
    run_all_validators,
)


# ═══════════════════════════════════════════════════════════════
# 统计一致性校验器测试
# ═══════════════════════════════════════════════════════════════

class TestStatisticsConsistency:
    """统计指标一致性校验"""

    def test_eta_squared_to_d_consistent(self):
        """η² ↔ d 一致的情况"""
        # d = 2√(η²/(1-η²))
        # η²=0.14 → d = 2√(0.14/0.86) = 2*0.4037 = 0.807
        # 但论文中 d=2.12 与 η²=0.14 的关系是 d=2t/√df 形式
        # 对于 ANOVA: d = 2√(η²/(1-η²))
        # η²=0.14 → d ≈ 0.807
        checks = check_statistics_consistency({
            "eta_squared": 0.14,
            "cohens_d": 0.807,
        })
        # 应该至少有一个校验项
        assert len(checks) >= 1
        eta_d_check = [c for c in checks if "η²" in c.metric]
        assert len(eta_d_check) >= 1
        assert eta_d_check[0].consistent is True

    def test_eta_squared_to_d_inconsistent(self):
        """η² ↔ d 不一致的情况"""
        # η²=0.14 对应 d≈0.807，但报告 d=2.12
        checks = check_statistics_consistency({
            "eta_squared": 0.14,
            "cohens_d": 2.12,
        })
        eta_d_check = [c for c in checks if "η²" in c.metric]
        assert len(eta_d_check) >= 1
        # 2.12 vs 0.807 应该不一致
        assert eta_d_check[0].consistent is False

    def test_t_to_d_consistent(self):
        """t ↔ d 一致的情况"""
        # d = 2t/√df
        # t=6.75, df=58 → d = 2*6.75/√58 = 13.5/7.616 = 1.773
        checks = check_statistics_consistency({
            "t_value": 6.75,
            "df": 58,
            "cohens_d": 1.773,
        })
        t_d_check = [c for c in checks if "t ↔" in c.metric]
        assert len(t_d_check) >= 1
        assert t_d_check[0].consistent is True

    def test_f_to_eta_squared_consistent(self):
        """F ↔ η² 一致的情况"""
        # η² = F/(F + df_error)
        # F=45.67, df_error=57 → η² = 45.67/(45.67+57) = 45.67/102.67 = 0.4448
        checks = check_statistics_consistency({
            "f_value": 45.67,
            "df_error": 57,
            "eta_squared": 0.4448,
        })
        f_eta_check = [c for c in checks if "F ↔" in c.metric]
        assert len(f_eta_check) >= 1
        assert f_eta_check[0].consistent is True

    def test_f_to_eta_squared_inconsistent(self):
        """F ↔ η² 不一致的情况"""
        # F=45.67, df_error=57 → η² 应为 0.4448，但报告 0.14
        checks = check_statistics_consistency({
            "f_value": 45.67,
            "df_error": 57,
            "eta_squared": 0.14,
        })
        f_eta_check = [c for c in checks if "F ↔" in c.metric]
        assert len(f_eta_check) >= 1
        assert f_eta_check[0].consistent is False

    def test_d_from_mean_and_sd(self):
        """d = mean_diff / pooled_sd"""
        checks = check_statistics_consistency({
            "cohens_d": 1.5,
            "mean_diff": 15.0,
            "pooled_sd": 10.0,
        })
        d_ms_check = [c for c in checks if "mean_diff" in c.metric]
        assert len(d_ms_check) >= 1
        assert d_ms_check[0].consistent is True

    def test_p_value_t_statistic_inconsistency(self):
        """p值与t统计量矛盾"""
        checks = check_statistics_consistency({
            "t_value": 5.0,
            "df": 100,
            "p_value": 0.5,  # |t|=5 应该 p < 0.001
        })
        p_check = [c for c in checks if "p值" in c.metric]
        assert len(p_check) >= 1
        assert p_check[0].consistent is False

    def test_empty_stats(self):
        """空统计指标"""
        checks = check_statistics_consistency({})
        assert len(checks) == 0

    def test_single_metric(self):
        """仅一个指标（无法交叉校验）"""
        checks = check_statistics_consistency({"cohens_d": 0.5})
        # 单一指标不应该产生矛盾，但也不应该产生校验项
        assert all(c.consistent for c in checks)


# ═══════════════════════════════════════════════════════════════
# 研究设计缺陷检测器测试
# ═══════════════════════════════════════════════════════════════

class TestDesignFlawDetection:
    """研究设计缺陷检测"""

    def test_multi_element_not_separated(self):
        """多要素未分离检测"""
        paper = """
        本研究采用准实验设计，设置实验组和对照组。
        实验组接受户外主题建构游戏干预，包含户外活动和主题建构两个要素。
        对照组进行常规教育活动。
        """
        flaws = detect_design_flaws(paper, {
            "intervention_elements": ["户外", "主题建构"],
            "n_groups": 2,
        })
        multi_element = [f for f in flaws if "多要素" in f.category]
        assert len(multi_element) >= 1
        assert multi_element[0].severity == "P0"

    def test_assessor_bias(self):
        """评估者偏差检测"""
        paper = "干预忠实度由实施教师自评完成。"
        flaws = detect_design_flaws(paper, {
            "fidelity_assessor": "self",
        })
        bias = [f for f in flaws if "评估者偏差" in f.category]
        assert len(bias) >= 1
        assert bias[0].severity == "P1"

    def test_sample_representativeness(self):
        """样本代表性不足检测"""
        paper = "本研究在一所省级示范幼儿园选取60名大班幼儿。"
        flaws = detect_design_flaws(paper, {
            "sample_source": "单一机构",
            "sample_size": 60,
        })
        sample = [f for f in flaws if "样本代表性" in f.category]
        assert len(sample) >= 1
        assert sample[0].severity == "P1"

    def test_hawthorne_effect(self):
        """霍桑效应检测"""
        paper = "实验组接受系统化干预，对照组进行常规教育活动。"
        flaws = detect_design_flaws(paper, {
            "has_control": True,
            "n_groups": 2,
        })
        hawthorne = [f for f in flaws if "霍桑" in f.category]
        assert len(hawthorne) >= 1

    def test_short_tracking_period(self):
        """追踪周期不足检测"""
        paper = "干预结束后进行追踪测试，追踪周期为4周。"
        flaws = detect_design_flaws(paper, {
            "tracking_weeks": 4,
        })
        tracking = [f for f in flaws if "追踪" in f.category]
        assert len(tracking) >= 1
        assert tracking[0].severity == "P2"

    def test_unvalidated_scale(self):
        """测量工具验证不足检测"""
        paper = "本研究使用自编量表进行测量。"
        flaws = detect_design_flaws(paper, {
            "scale_validated": False,
        })
        scale = [f for f in flaws if "测量工具" in f.category]
        assert len(scale) >= 1
        assert scale[0].severity == "P1"

    def test_non_random_assignment(self):
        """非随机分配检测"""
        paper = "本研究采用准实验设计。"
        flaws = detect_design_flaws(paper, {
            "has_random_assignment": False,
        })
        random = [f for f in flaws if "非随机" in f.category]
        assert len(random) >= 1

    def test_low_statistical_power(self):
        """统计检验力不足检测"""
        flaws = detect_design_flaws("", {
            "sample_size": 40,
            "n_groups": 2,
        })
        power = [f for f in flaws if "统计检验力" in f.category]
        assert len(power) >= 1
        assert power[0].severity == "P2"

    def test_no_flaws(self):
        """良好设计不应产生严重缺陷"""
        paper = "本研究采用真实验设计，随机分配被试至多水平实验条件。"
        flaws = detect_design_flaws(paper, {
            "has_random_assignment": True,
            "n_groups": 4,
            "intervention_elements": ["单一要素"],
            "fidelity_assessor": "independent",
            "sample_source": "多机构",
            "sample_size": 200,
            "tracking_weeks": 26,
            "scale_validated": True,
        })
        p0 = [f for f in flaws if f.severity == "P0"]
        p1 = [f for f in flaws if f.severity == "P1"]
        assert len(p0) == 0
        assert len(p1) == 0

    def test_text_inference_multi_element(self):
        """从文本自动推断多要素（通用列举模式）"""
        paper = "本研究结合了认知训练和有氧运动两种干预，实验组接受联合干预，对照组进行常规活动。"
        flaws = detect_design_flaws(paper)
        multi = [f for f in flaws if "多要素" in f.category]
        assert len(multi) >= 1


# ═══════════════════════════════════════════════════════════════
# 引用真实性验证器测试
# ═══════════════════════════════════════════════════════════════

class TestCitationVerification:
    """引用真实性验证"""

    @pytest.mark.asyncio
    async def test_local_check_year_anomaly(self):
        """年份异常检测（离线模式）"""
        papers = [
            {"title": "Test Paper", "authors": "John Smith", "year": "2030", "venue": "Test Journal"},
        ]
        checks = await verify_citation_authenticity(papers, enable_online=False)
        assert len(checks) == 1
        assert "年份" in checks[0].issue
        assert checks[0].confidence < 0.5

    @pytest.mark.asyncio
    async def test_local_check_missing_authors(self):
        """作者缺失检测"""
        papers = [
            {"title": "Test Paper", "authors": "", "year": "2023", "venue": "Journal"},
        ]
        checks = await verify_citation_authenticity(papers, enable_online=False)
        assert "作者缺失" in checks[0].issue

    @pytest.mark.asyncio
    async def test_local_check_short_title(self):
        """标题过短检测"""
        papers = [
            {"title": "Hi", "authors": "Author", "year": "2023", "venue": "Journal"},
        ]
        checks = await verify_citation_authenticity(papers, enable_online=False)
        assert "标题过短" in checks[0].issue

    @pytest.mark.asyncio
    async def test_doi_format_check(self):
        """DOI 格式检查"""
        papers = [
            {"title": "Valid Paper", "authors": "Author", "year": "2023", "venue": "Journal", "doi": "invalid-doi"},
        ]
        checks = await verify_citation_authenticity(papers, enable_online=False)
        assert "DOI 格式" in checks[0].issue

    @pytest.mark.asyncio
    async def test_online_verification_mocked(self):
        """在线验证（mocked）"""
        papers = [
            {"title": "Attention Is All You Need", "authors": "Vaswani et al.", "year": "2017", "venue": "NeurIPS", "doi": ""},
        ]
        # Mock Semantic Scholar response
        with patch("vermes_cli.scholarforge.validators._verify_semantic_scholar",
                   new_callable=AsyncMock, return_value={"verified": True, "confidence": 0.9, "doi": ""}):
            checks = await verify_citation_authenticity(papers, enable_online=True)
        assert len(checks) == 1
        assert checks[0].verified is True
        assert checks[0].source == "semantic_scholar"

    @pytest.mark.asyncio
    async def test_online_verification_failed(self):
        """在线验证失败"""
        papers = [
            {"title": "Fake Paper That Does Not Exist", "authors": "Nobody", "year": "2023", "venue": ""},
        ]
        with patch("vermes_cli.scholarforge.validators._verify_semantic_scholar",
                   new_callable=AsyncMock, return_value=None):
            with patch("vermes_cli.scholarforge.validators._verify_crossref_doi",
                       new_callable=AsyncMock, return_value=None):
                checks = await verify_citation_authenticity(papers, enable_online=True)
        assert len(checks) == 1
        assert checks[0].verified is False
        assert "在线验证未找到" in checks[0].issue


# ═══════════════════════════════════════════════════════════════
# 格式化报告测试
# ═══════════════════════════════════════════════════════════════

class TestReportFormatting:
    """报告格式化测试"""

    def test_citation_report_with_issues(self):
        """引用报告（有问题）"""
        checks = [
            CitationCheck(ref_num=1, title="Real Paper", authors="Author", year="2023",
                          verified=True, confidence=0.9, source="crossref", doi="10.1234/test"),
            CitationCheck(ref_num=2, title="Fake Paper", authors="Nobody", year="2030",
                          verified=False, confidence=0.1, source="local_check",
                          issue="年份 2030 在当前年份之后"),
        ]
        report = format_citation_report(checks)
        assert "文献引用真实性验证报告" in report
        assert "存疑" in report
        assert "已验证" in report
        assert "Fake Paper" in report

    def test_citation_report_all_verified(self):
        """引用报告（全部验证通过）"""
        checks = [
            CitationCheck(ref_num=1, title="Paper A", authors="Author A", year="2023",
                          verified=True, confidence=0.95, source="crossref"),
        ]
        report = format_citation_report(checks)
        assert "已验证" in report
        assert "存疑" not in report or "0 篇" in report

    def test_statistics_report_consistent(self):
        """统计报告（一致）"""
        checks = [
            StatCheck(metric="η² ↔ Cohen's d", value_reported="d = 0.8",
                      value_expected="d = 0.807", consistent=True,
                      explanation="一致"),
        ]
        report = format_statistics_report(checks)
        assert "统计一致性校验" in report
        assert "通过校验" in report

    def test_statistics_report_inconsistent(self):
        """统计报告（矛盾）"""
        checks = [
            StatCheck(metric="η² ↔ Cohen's d", value_reported="d = 2.12",
                      value_expected="d = 0.807", consistent=False,
                      explanation="偏差 162%"),
        ]
        report = format_statistics_report(checks)
        assert "矛盾" in report
        assert "η²" in report

    def test_statistics_report_empty(self):
        """统计报告（空）"""
        report = format_statistics_report([])
        assert "未发现统计指标矛盾" in report

    def test_design_report_with_flaws(self):
        """设计报告（有缺陷）"""
        flaws = [
            DesignFlaw(severity="P0", category="多要素未分离",
                       description="测试描述", evidence="测试证据", suggestion="测试建议"),
        ]
        report = format_design_report(flaws)
        assert "研究设计缺陷检测" in report
        assert "P0 严重缺陷" in report
        assert "多要素未分离" in report

    def test_design_report_no_flaws(self):
        """设计报告（无缺陷）"""
        report = format_design_report([])
        assert "未发现明显设计缺陷" in report


# ═══════════════════════════════════════════════════════════════
# 综合入口测试
# ═══════════════════════════════════════════════════════════════

class TestRunAllValidators:
    """综合验证器入口"""

    @pytest.mark.asyncio
    async def test_run_all_with_no_data(self):
        """无数据时跳过"""
        report = await run_all_validators()
        assert "未提供验证数据" in report

    @pytest.mark.asyncio
    async def test_run_all_with_stats_only(self):
        """仅统计指标"""
        report = await run_all_validators(
            stats={"eta_squared": 0.14, "cohens_d": 0.807},
        )
        assert "统计一致性校验" in report

    @pytest.mark.asyncio
    async def test_run_all_with_text_only(self):
        """仅论文文本"""
        paper = "本研究在一所幼儿园选取60名幼儿，采用准实验设计。"
        report = await run_all_validators(paper_text=paper)
        assert "研究设计缺陷检测" in report

    @pytest.mark.asyncio
    async def test_run_all_combined(self):
        """综合验证"""
        with patch("vermes_cli.scholarforge.validators._verify_semantic_scholar",
                   new_callable=AsyncMock, return_value=None):
            with patch("vermes_cli.scholarforge.validators._verify_crossref_doi",
                       new_callable=AsyncMock, return_value=None):
                report = await run_all_validators(
                    papers=[{"title": "Test", "authors": "A", "year": "2023", "venue": "J"}],
                    stats={"eta_squared": 0.14, "cohens_d": 2.12},
                    paper_text="一所幼儿园60名幼儿准实验设计",
                )
        assert "文献引用真实性验证报告" in report
        assert "统计一致性校验" in report
        assert "研究设计缺陷检测" in report


# ═══════════════════════════════════════════════════════════════
# 2026-09-16 统计校验「适用条件」回归测试
# ═══════════════════════════════════════════════════════════════
# 背景：旧实现把每种换算都当成「唯一公式」，于是把大量**正确**的论文报告判成「矛盾」。
# 本组测试逐条锁死这些误报场景 —— 任何一条挂掉，都意味着误报回归。

from vermes_cli.scholarforge.validators import (  # noqa: E402
    t_p_two_tailed,
    f_p_right_tail,
)


def _pick(checks, keyword):
    """取出 metric 含关键词的校验项（应唯一）"""
    hit = [c for c in checks if keyword in c.metric]
    assert len(hit) == 1, f"期望恰好 1 条含「{keyword}」的校验项，实得 {len(hit)}: {[c.metric for c in checks]}"
    return hit[0]


class TestTDToCohensD:
    """校验 2：t ↔ d 的检验类型分支"""

    def test_paired_sample_not_false_positive(self):
        """🔴 核心回归：配对样本 d_z = t/√(df+1) 必须判一致

        旧实现只有 d = 2t/√df：
          t=6.92, df=39 → 旧式给 2*6.92/√39 = 2.216
          正确的配对 d_z = 6.92/√40 = 1.094
          相对偏差 51%（容差 15%）→ 旧实现会把**完全正确**的报告判成矛盾。
        """
        checks = check_statistics_consistency({
            "t_value": 6.92, "df": 39, "cohens_d": 1.094,
        })
        assert _pick(checks, "t ↔").consistent is True

    def test_paired_sample_explicit_type(self):
        """显式声明 t_test_type=paired → 只按配对式判定"""
        checks = check_statistics_consistency({
            "t_value": 6.92, "df": 39, "cohens_d": 1.094, "t_test_type": "paired",
        })
        assert _pick(checks, "t ↔").consistent is True

    def test_paired_type_rejects_independent_value(self):
        """声明 paired 却给了独立样本口径的 d → 应判矛盾（候选集缩窄后才抓得到）"""
        checks = check_statistics_consistency({
            "t_value": 6.92, "df": 39, "cohens_d": 2.216, "t_test_type": "paired",
        })
        assert _pick(checks, "t ↔").consistent is False

    def test_independent_small_df_strict_form(self):
        """🔴 核心回归：独立样本小 df 的严格式 d = 2t/√(df+2) 必须判一致

        df=4 时旧式 2t/√df 比严格式 2t/√(df+2) 高 18.4%，超过 15% 容差 → 误报。
        """
        t, df = 2.776, 4  # df=4 的双尾 .05 临界值
        d_strict = 2 * t / math.sqrt(df + 2)
        checks = check_statistics_consistency({"t_value": t, "df": df, "cohens_d": d_strict})
        assert _pick(checks, "t ↔").consistent is True

    def test_independent_textbook_approx_still_passes(self):
        """教科书近似式 2t/√df 仍应通过（兼容既有文献口径，不做无谓误伤）"""
        t, df = 6.75, 58
        checks = check_statistics_consistency({
            "t_value": t, "df": df, "cohens_d": 2 * t / math.sqrt(df),
        })
        assert _pick(checks, "t ↔").consistent is True

    def test_genuinely_wrong_d_still_caught(self):
        """三个候选式都对不上时，仍必须报矛盾（不能因为放宽就漏判）"""
        checks = check_statistics_consistency({
            "t_value": 6.92, "df": 39, "cohens_d": 0.30,
        })
        c = _pick(checks, "t ↔")
        assert c.consistent is False
        assert "候选式均不吻合" in c.explanation

    def test_sign_convention_not_flagged(self):
        """d 取负号（组别顺序相反）不构成矛盾"""
        checks = check_statistics_consistency({
            "t_value": 6.75, "df": 58, "cohens_d": -1.773,
        })
        assert _pick(checks, "t ↔").consistent is True


class TestEtaSquaredToD:
    """校验 1：η² ↔ d 的多组闸门与取值域"""

    def test_multi_group_skips_check(self):
        """🔴 核心回归：三组及以上（df_between=2）时不得硬判，必须标「未校验」

        多组 ANOVA 的整体 η² 没有唯一对应的两两 d，硬用 d=2√(η²/(1-η²)) 会误伤。
        """
        checks = check_statistics_consistency({
            "eta_squared": 0.1553, "cohens_d": 0.62, "df_between": 2,
        })
        c = _pick(checks, "η²")
        assert "未校验" in c.metric
        assert c.consistent is True  # 未校验 ≠ 矛盾
        assert "整体" in c.explanation

    def test_two_group_still_judged(self):
        """两组比较（df_between=1）照旧严格判定"""
        checks = check_statistics_consistency({
            "eta_squared": 0.14, "cohens_d": 2.12, "df_between": 1,
        })
        assert _pick(checks, "η²").consistent is False

    def test_out_of_range_eta_squared(self):
        """η² 越界（如把 14% 写成 14）不得抛异常，且要提示"""
        checks = check_statistics_consistency({"eta_squared": 14, "cohens_d": 0.8})
        c = _pick(checks, "η²")
        assert c.consistent is False
        assert "取值范围" in c.metric

    def test_negative_d_uses_magnitude(self):
        """η² 恒正，比对 |d| 而非 d"""
        checks = check_statistics_consistency({"eta_squared": 0.14, "cohens_d": -0.807})
        assert _pick(checks, "η²").consistent is True


class TestDToR:
    """校验 4b：d ↔ r（docstring 承诺过、但代码从未实现）"""

    def test_equal_n_approx(self):
        """未给样本量 → 用教科书近似式 r = d/√(d²+4)"""
        d = 0.8
        checks = check_statistics_consistency({
            "cohens_d": d, "r_value": d / math.sqrt(d * d + 4),
        })
        c = _pick(checks, "d ↔ r")
        assert c.consistent is True
        assert "近似式" in c.value_expected

    def test_exact_form_with_group_sizes(self):
        """给 n1/n2 → 用精确式 A=(n1+n2)(n1+n2-2)/(n1·n2)，与 t 路径自洽

        交叉验证：d=1.0, n1=n2=10 → t = d/√(1/10+1/10) = 2.236, df=18
        → r = t/√(t²+df) = 0.4662，精确式 A=20·18/100=3.6 → 1/√4.6 = 0.4662 ✓
        """
        d, n1, n2 = 1.0, 10, 10
        t = d / math.sqrt(1 / n1 + 1 / n2)
        r_via_t = t / math.sqrt(t * t + (n1 + n2 - 2))
        checks = check_statistics_consistency({
            "cohens_d": d, "r_value": r_via_t, "n_group1": n1, "n_group2": n2,
        })
        c = _pick(checks, "d ↔ r")
        assert c.consistent is True
        assert "精确式" in c.value_expected

    def test_approx_vs_exact_differ_at_small_n(self):
        """小样本下近似式与精确式确有差距（证明精确式不是摆设）"""
        d, n1, n2 = 1.0, 5, 5
        r_approx = d / math.sqrt(d * d + 4)
        r_exact = d / math.sqrt(d * d + 10 * 8 / 25)
        assert abs(r_approx - r_exact) / r_exact > 0.05

    def test_mismatch_flagged(self):
        """明显对不上仍要抓"""
        checks = check_statistics_consistency({"cohens_d": 1.5, "r_value": 0.1})
        assert _pick(checks, "d ↔ r").consistent is False

    def test_applicability_caveat_present(self):
        """解释文本必须带上「点二列相关」的适用前提，防止误用于 Pearson r"""
        checks = check_statistics_consistency({"cohens_d": 0.8, "r_value": 0.371})
        assert "点二列" in _pick(checks, "d ↔ r").explanation


class TestExactPValue:
    """精确 p 值计算 + 校验 5 的判定纪律"""

    def test_matches_known_critical_values(self):
        """对照统计学教材的标准临界值（双尾 0.05 / 0.01）"""
        for t, df, expected in [
            (12.706, 1, 0.05), (4.303, 2, 0.05), (2.228, 10, 0.05),
            (63.657, 1, 0.01), (9.925, 2, 0.01), (3.169, 10, 0.01),
        ]:
            got = t_p_two_tailed(t, df)
            assert abs(got - expected) / expected < 0.001, f"t={t}, df={df}: {got} vs {expected}"

    def test_matches_three_decimal_tail(self):
        """极端尾概率：t=5, df=100 双尾 p = 2.4501734135e-6（与 scipy 相对误差 ~1e-15）

        注意：这里必须写满有效数字 —— 早期版本把参考值截断成 2.45017341e-06（9 位），
        导致「实现是对的、参考值不准」的假失败（相对差 1.4e-9 却要求 < 1e-9）。
        """
        assert abs(t_p_two_tailed(5.0, 100) - 2.450173413503806e-06) / 2.450173413503806e-06 < 1e-10

    def test_f_right_tail(self):
        """F 右尾：F(2,87)=4.12 → p ≈ 0.01952"""
        assert abs(f_p_right_tail(4.12, 2, 87) - 0.0195183978) / 0.0195183978 < 1e-6

    def test_invalid_inputs_return_nan(self):
        """非法输入返回 NaN 而不是抛异常（打包环境里崩溃代价太高）"""
        assert math.isnan(t_p_two_tailed(1.0, 0))
        assert math.isnan(f_p_right_tail(-1.0, 2, 87))
        assert t_p_two_tailed(0.0, 10) == 1.0

    def test_small_df_p_still_checked(self):
        """🔴 核心回归：小样本（df≤30）也必须校验 p —— 旧实现只在 df>30 时生效"""
        checks = check_statistics_consistency({
            "t_value": 0.5, "df": 12, "p_value": 0.001,
        })
        c = _pick(checks, "p值")
        assert c.consistent is False
        assert "过小" in c.explanation

    def test_upper_bound_notation_not_flagged(self):
        """「p < .001」被解析成 0.001 时不该被判「过大」（上界写法的合法场景）"""
        checks = check_statistics_consistency({
            "t_value": 6.92, "df": 39, "p_value": 0.001,  # 精确 p = 2.76e-8
        })
        p_checks = [c for c in checks if "p值" in c.metric and not c.consistent]
        assert p_checks == []

    def test_p_less_than_05_bound_not_flagged(self):
        """真正显著却只报「p < .05」是合法报告，不得误伤"""
        checks = check_statistics_consistency({
            "t_value": 6.92, "df": 39, "p_value": 0.05,
        })
        assert [c for c in checks if "p值" in c.metric and not c.consistent] == []

    def test_one_tailed_not_flagged(self):
        """单尾 p（双尾的一半，差 2 倍）属合法差异，不得误伤"""
        two_tailed = t_p_two_tailed(2.5, 20)
        checks = check_statistics_consistency({
            "t_value": 2.5, "df": 20, "p_value": two_tailed / 2,
        })
        assert [c for c in checks if "p值" in c.metric and not c.consistent] == []

    def test_reported_p_too_large(self):
        """t 极大却报 p=0.5 → 必须抓（这是旧实现唯一覆盖的场景，不能丢）"""
        checks = check_statistics_consistency({
            "t_value": 5.0, "df": 100, "p_value": 0.5,
        })
        c = _pick(checks, "p值")
        assert c.consistent is False
        assert "过大" in c.explanation

    def test_f_p_contradiction(self):
        """F 检验的 p 同样校验：F(2,87)=8.0 的精确 p=6.47e-4，报 0.5 必抓"""
        checks = check_statistics_consistency({
            "f_value": 8.0, "df_between": 2, "df_error": 87, "p_value": 0.5,
        })
        c = _pick(checks, "p值")
        assert c.consistent is False
        assert "F统计量" in c.metric

    def test_consistent_p_produces_no_check(self):
        """p 值吻合时不产生校验项（避免报告噪音）"""
        checks = check_statistics_consistency({
            "t_value": 2.228, "df": 10, "p_value": 0.05,
        })
        assert [c for c in checks if "p值" in c.metric] == []
