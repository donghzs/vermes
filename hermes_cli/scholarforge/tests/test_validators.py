"""
ScholarForge 验证器测试 — validators.py
测试三个验证器的核心功能
"""
import asyncio
import math
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from hermes_cli.scholarforge.validators import (
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
        """从文本自动推断多要素"""
        paper = "实验组接受户外主题建构游戏干预，对照组进行常规活动。"
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
        with patch("hermes_cli.scholarforge.validators._verify_semantic_scholar",
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
        with patch("hermes_cli.scholarforge.validators._verify_semantic_scholar",
                   new_callable=AsyncMock, return_value=None):
            with patch("hermes_cli.scholarforge.validators._verify_crossref_doi",
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
        with patch("hermes_cli.scholarforge.validators._verify_semantic_scholar",
                   new_callable=AsyncMock, return_value=None):
            with patch("hermes_cli.scholarforge.validators._verify_crossref_doi",
                       new_callable=AsyncMock, return_value=None):
                report = await run_all_validators(
                    papers=[{"title": "Test", "authors": "A", "year": "2023", "venue": "J"}],
                    stats={"eta_squared": 0.14, "cohens_d": 2.12},
                    paper_text="一所幼儿园60名幼儿准实验设计",
                )
        assert "文献引用真实性验证报告" in report
        assert "统计一致性校验" in report
        assert "研究设计缺陷检测" in report
