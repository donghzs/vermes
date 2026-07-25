"""
ScholarForge 质量验证器 — 论文生成后的质量门控

三个高优先级验证器：
1. CitationAuthenticityVerifier — 文献引用真实性验证（DOI/CrossRef/知网 API 校验）
2. StatisticsConsistencyChecker — 统计指标内部一致性校验（η²↔d↔t↔F 值换算）
3. ResearchDesignDetector — 研究设计缺陷检测（多要素未分离/评估者偏差等）

使用方式：
    from hermes_cli.scholarforge.validators import (
        verify_citation_authenticity,
        check_statistics_consistency,
        detect_design_flaws,
        run_all_validators,
    )
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("scholarforge.validators")


# ═══════════════════════════════════════════════════════════════
# 1. 文献引用真实性验证器
# ═══════════════════════════════════════════════════════════════

@dataclass
class CitationCheck:
    """单条文献验证结果"""
    ref_num: int
    title: str
    authors: str
    year: str
    verified: bool  # 是否验证为真实存在
    confidence: float  # 0-1
    source: str  # 验证来源: "crossref" | "semantic_scholar" | "local_check" | "failed"
    doi: str = ""
    issue: str = ""  # 问题描述


async def verify_citation_authenticity(
    papers: list[dict],
    enable_online: bool = True,
) -> list[CitationCheck]:
    """验证文献列表中每条文献是否真实存在

    三层验证策略：
    1. 在线验证：CrossRef API（DOI 校验）+ Semantic Scholar API（标题搜索）
    2. 本地启发式：检测年代分布异常、格式异常、作者名异常
    3. 综合判定：在线命中 → verified=True；仅本地异常 → 标记 issue

    Args:
        papers: 文献列表，每条含 title/authors/year/venue/doi
        enable_online: 是否启用在线验证（默认 True），关闭时仅做本地启发式
    Returns:
        CitationCheck 列表
    """
    results: list[CitationCheck] = []

    for i, p in enumerate(papers, 1):
        title = p.get("title", "").strip()
        authors = p.get("authors", "").strip() if isinstance(p.get("authors"), str) else ", ".join(p.get("authors", []))
        year = str(p.get("year", "")).strip()
        doi = p.get("doi", "").strip()
        venue = p.get("venue", "").strip()

        check = CitationCheck(
            ref_num=i, title=title, authors=authors, year=year,
            verified=False, confidence=0.0, source="local_check",
        )

        # ── 本地启发式检查 ──
        issues = []

        # 1. 年代分布异常：年份在当前年份之后或与论文写作时间完全同步
        current_year = 2026
        try:
            y = int(year)
            if y > current_year:
                issues.append(f"年份 {y} 在当前年份之后")
            elif y == current_year:
                issues.append(f"年份 {y} 与当前年份完全同步，需在线验证")
        except (ValueError, TypeError):
            issues.append("年份格式异常或缺失")

        # 2. 标题长度异常（过短或过长）
        if len(title) < 5:
            issues.append("标题过短")
        elif len(title) > 300:
            issues.append("标题过长")

        # 3. 作者名异常
        if not authors or authors == "Unknown":
            issues.append("作者缺失")
        elif len(authors) < 3:
            issues.append("作者名过短")

        # 4. 期刊/出版物异常
        if not venue:
            issues.append("期刊/出版物缺失")

        # 5. DOI 格式检查
        if doi and not re.match(r'^10\.\d{4,}/', doi):
            issues.append("DOI 格式不规范")

        check.issue = "; ".join(issues)

        # ── 在线验证 ──
        api_errors: list[str] = []
        crossref_result = None
        s2_result = None
        any_api_ok = False  # 至少一次 API 非 error 响应

        if enable_online and title:
            # 0) 先查用户本地已备文献库——用户手里有这篇 PDF/条目即最强存在证据
            local_result = await _verify_via_local_library(title, authors, year)
            if local_result and local_result.get("verified") and not local_result.get("error"):
                check.verified = True
                check.confidence = local_result.get("confidence", 0.95)
                check.source = "local_library"
                check.issue = ""
                logger.info(f"[CitationVerify] [{i}] verified via local library: {title[:50]}")
                results.append(check)
                continue
            # 1) 再查用户配置的付费/中文文献源（CNKI/Wanfang/...），
            #    中文文献在其覆盖远优于 Crossref/SemanticScholar。
            prov_result = await _verify_via_configured_provider(title, authors, year)
            if prov_result and prov_result.get("verified") and not prov_result.get("error"):
                check.verified = True
                check.confidence = prov_result.get("confidence", 0.85)
                check.source = prov_result.get("source", "configured_provider")
                check.issue = ""
                logger.info(f"[CitationVerify] [{i}] verified via configured provider '{check.source}': {title[:50]}")
                results.append(check)
                continue
            # 再尝试 CrossRef（如果有 DOI）
            if doi:
                crossref_result = await _verify_crossref_doi(doi)
                if crossref_result and not crossref_result.get("error"):
                    check.verified = True
                    check.confidence = 0.95
                    check.source = "crossref"
                    check.doi = doi
                    if check.issue:
                        check.issue = ""
                    logger.info(f"[CitationVerify] [{i}] DOI verified via CrossRef: {doi}")
                    results.append(check)
                    continue
                elif crossref_result and crossref_result.get("error"):
                    api_errors.append(f"CrossRef: {crossref_result['reason']}")
                else:
                    any_api_ok = True  # CrossRef 正常响应但无匹配

            # 再尝试 Semantic Scholar（标题搜索）
            s2_result = await _verify_semantic_scholar(title, authors, year)
            if s2_result and not s2_result.get("error"):
                check.verified = True
                check.confidence = s2_result.get("confidence", 0.8)
                check.source = "semantic_scholar"
                check.doi = s2_result.get("doi", "")
                if check.issue:
                    check.issue = ""
                logger.info(f"[CitationVerify] [{i}] Title verified via Semantic Scholar: {title[:50]}")
                results.append(check)
                continue
            elif s2_result and s2_result.get("error"):
                api_errors.append(f"SemanticScholar: {s2_result['reason']}")
            else:
                any_api_ok = True  # S2 正常响应但无匹配

            # ── 区分：API 不可用 vs 文献不存在 ──
            if api_errors and not any_api_ok:
                # 所有 API 调用均报错，未执行任何有效验证
                check.source = "api_unavailable"
                check.confidence = 0.0
                check.issue = f"在线验证服务不可用（{'；'.join(api_errors)}）"
                logger.warning(f"[CitationVerify] [{i}] All APIs unavailable: {'; '.join(api_errors)}")
            else:
                # 至少一次 API 成功调用但未匹配 → 文献可能不存在
                prefix = f"({'；'.join(api_errors)}；)" if api_errors else ""
                if check.issue:
                    check.confidence = 0.2
                    check.issue = f"{prefix}在线验证未找到匹配文献; {check.issue}"
                else:
                    check.confidence = 0.3
                    check.issue = f"{prefix}在线验证未找到匹配文献，可能不存在或为虚构"
        else:
            # 仅本地检查
            if not check.issue:
                check.confidence = 0.5
                check.issue = "仅本地检查，未在线验证"
            else:
                check.confidence = 0.15

        results.append(check)

    return results


async def _verify_via_configured_provider(
    title: str, authors: str, year: str
) -> dict | None:
    """用用户配置的付费/中文文献源查证文献是否真实存在。

    优先于 Crossref/SemanticScholar——中文文献（如幼儿合作能力、户外建构游戏）
    在 CNKI/Wanfang 覆盖最好。当用户仅配置了国际免费源（openalex/crossref/
    semanticscholar 等）时返回 ``None``，交给下方专用逻辑，避免重复查询。

    Returns:
        ``{"verified": True, "confidence": float, "source": "<provider>"}``
        ``None`` — 未配置合适源 / 检索无匹配
        ``{"error": True, "reason": "..."}`` — 源调用异常
    """
    try:
        from agent.literature_registry import get_active_search_provider
    except Exception as exc:
        logger.debug(f"文献源注册表不可用，跳过配置源查证: {exc}")
        return None

    provider = get_active_search_provider()
    if provider is None:
        return None

    name = (getattr(provider, "name", "") or "").lower()
    # 国际免费源由下方 Crossref/S2 逻辑覆盖，这里只接管「用户配的付费/中文源」
    if name in (
        "openalex",
        "crossref",
        "semanticscholar",
        "pubmed",
        "arxiv",
        "europepmc",
        "doaj",
        "core",
    ):
        return None

    try:
        resp = provider.search(title, limit=3)
    except Exception as exc:
        logger.warning(f"文献源 {name} 查证失败: {exc}")
        return {"error": True, "reason": str(exc)[:100]}

    if not resp or not resp.get("success"):
        return None

    hits = (resp.get("data") or {}).get("papers", [])
    if not hits:
        return None

    import difflib

    best = 0.0
    for h in hits:
        ht = h.get("title", "") or ""
        if ht:
            best = max(best, difflib.SequenceMatcher(None, title.lower(), ht.lower()).ratio())
    if best >= 0.6:
        return {
            "verified": True,
            "confidence": min(0.95, 0.7 + best * 0.25),
            "source": name,
        }
    return None


async def _verify_via_local_library(title: str, authors: str, year: str) -> dict | None:
    """用用户本地已备文献库核实文献真实性（最高信任信号）。

    用户本地文件夹/USB 里真有这篇 PDF 或 BibTeX/RIS 条目，等于「用户亲手收藏
    过这篇文献」——比任何在线源都更强的存在性证据。命中即 verified=True。

    Returns:
        ``{"verified": True, "confidence": float, "source": "local_library",
           "hit_title": str, "hit_path": str}``
        ``None`` — 本地无匹配（无本地库 / 未索引 / 确实没有这篇）
    """
    try:
        from agent.local_library_index import verify_local
    except Exception as exc:  # noqa: BLE001
        logger.debug("本地文献索引不可用，跳过本地核实: %s", exc)
        return None

    try:
        result = verify_local(title, authors, year)
    except Exception as exc:  # noqa: BLE001
        logger.debug("本地文献核实异常: %s", exc)
        return None

    if result and result.get("verified"):
        return result
    return None


async def _verify_crossref_doi(doi: str) -> dict | None:
    """通过 CrossRef API 验证 DOI 是否真实存在
    
    Returns:
        {"verified": True, ...} — 验证成功
        None — 文献不存在（API 返回但无匹配）
        {"error": True, "reason": "..."} — API 不可用（网络/超时/限流等）
    """
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"https://api.crossref.org/works/{doi}",
                headers={"User-Agent": "ScholarForge/1.0 (mailto:scholarforge@vermes.ai)"},
            )
        if resp.status_code == 200:
            data = resp.json()
            return {"verified": True, "data": data.get("message", {})}
        elif resp.status_code == 404:
            return None  # DOI 不存在
        else:
            logger.warning(f"CrossRef API unexpected status {resp.status_code} for {doi}")
            return {"error": True, "reason": f"HTTP {resp.status_code}"}
    except Exception as e:
        logger.warning(f"CrossRef API error for {doi}: {e}")
        return {"error": True, "reason": str(e)[:100]}


async def _verify_semantic_scholar(title: str, authors: str, year: str) -> dict | None:
    """通过 Semantic Scholar API 搜索标题验证文献是否存在
    
    Returns:
        {"verified": True, ...} — 验证成功
        None — 文献不存在（搜索返回但无匹配）
        {"error": True, "reason": "..."} — API 不可用（网络/超时/限流等）
    """
    import httpx
    try:
        # 搜索标题
        query = title[:200]
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://api.semanticscholar.org/graph/v1/paper/search",
                params={
                    "query": query,
                    "limit": 3,
                    "fields": "title,authors,year,externalIds",
                },
            )
        if resp.status_code == 200:
            data = resp.json()
            papers = data.get("data", [])
            if not papers:
                return None  # 搜索无结果

            # 模糊匹配标题
            import difflib
            for p in papers:
                p_title = p.get("title", "")
                similarity = difflib.SequenceMatcher(None, title.lower(), p_title.lower()).ratio()
                if similarity > 0.7:
                    # 检查年份是否匹配
                    p_year = str(p.get("year", ""))
                    year_match = not year or not p_year or year == p_year
                    confidence = similarity * (0.9 if year_match else 0.6)
                    return {
                        "verified": True,
                        "confidence": min(confidence, 0.95),
                        "doi": p.get("externalIds", {}).get("DOI", ""),
                    }
            return None  # 有结果但不匹配
        elif resp.status_code == 429:
            logger.warning(f"Semantic Scholar rate limited for '{title[:30]}'")
            return {"error": True, "reason": "rate_limited"}
        else:
            logger.warning(f"Semantic Scholar API unexpected status {resp.status_code}")
            return {"error": True, "reason": f"HTTP {resp.status_code}"}
    except Exception as e:
        logger.warning(f"Semantic Scholar API error for '{title[:30]}': {e}")
        return {"error": True, "reason": str(e)[:100]}


def format_citation_report(checks: list[CitationCheck]) -> str:
    """格式化引用验证报告"""
    total = len(checks)
    verified = sum(1 for c in checks if c.verified)
    api_unavailable = sum(1 for c in checks if c.source == "api_unavailable")
    suspicious = sum(1 for c in checks if not c.verified and c.source != "api_unavailable" and c.confidence < 0.3)

    lines = [f"## 🔍 文献引用真实性验证报告\n"]
    lines.append(f"**总计**: {total} 篇文献")
    lines.append(f"**已验证**: {verified} 篇 ({verified*100//max(total,1)}%)")
    if api_unavailable:
        lines.append(f"**⚠️ 验证服务不可用**: {api_unavailable} 篇 (API 连接失败，未完成在线验证)")
    lines.append(f"**存疑**: {suspicious} 篇\n")

    if api_unavailable > 0:
        lines.append("### ⚠️ 验证服务不可用（建议稍后重试或手动验证）\n")
        for c in checks:
            if c.source == "api_unavailable":
                lines.append(f"- **[{c.ref_num}]** {c.title[:60]}...")
                lines.append(f"  - 作者: {c.authors[:40]}")
                lines.append(f"  - 原因: {c.issue}")
                lines.append("")

    if suspicious > 0:
        lines.append("### ⚠️ 存疑文献\n")
        for c in checks:
            if not c.verified and c.confidence < 0.3:
                lines.append(f"- **[{c.ref_num}]** {c.title[:60]}...")
                lines.append(f"  - 作者: {c.authors[:40]}")
                lines.append(f"  - 年份: {c.year}")
                lines.append(f"  - 问题: {c.issue}")
                lines.append("")

    if verified > 0:
        lines.append("### ✅ 已验证文献\n")
        for c in checks:
            if c.verified:
                lines.append(f"- **[{c.ref_num}]** ✅ {c.title[:50]}... ({c.source}, {c.confidence:.0%})")

    # 建议
    if suspicious > 0:
        lines.append(f"\n### 💡 建议\n")
        lines.append(f"- {suspicious} 篇文献未能在线验证，建议手动在知网/Google Scholar 搜索确认")
        lines.append("- 使用 `scholarforge_replace_citations` 工具自动重新搜索并替换存疑引用（会根据上下文重新匹配真实文献）")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# 2. 统计指标一致性校验器
# ═══════════════════════════════════════════════════════════════

@dataclass
class StatCheck:
    """统计指标校验结果"""
    metric: str  # 指标名称
    value_reported: str  # 论文中报告的值
    value_expected: str  # 根据其他指标计算的期望值
    consistent: bool  # 是否一致
    explanation: str  # 不一致时的解释


def check_statistics_consistency(
    stats: dict[str, Any],
) -> list[StatCheck]:
    """校验统计指标的内部一致性

    支持校验的指标对：
    1. η² ↔ Cohen's d:  d = 2√(η²/(1-η²))
    2. t ↔ d:  d = 2t/√(df)  (独立样本 t 检验)
    3. F ↔ η²:  η² = F/(F + df_error)  (单因素 ANOVA)
    4. d ↔ r:  r = d/√(d² + 4)  (二分组)
    5. p值 ↔ 统计量:  根据统计量和自由度估算 p 值范围

    Args:
        stats: 统计指标字典，可含:
            - eta_squared: float (η²)
            - cohens_d: float (Cohen's d)
            - t_value: float (t 统计量)
            - df: int (自由度)
            - f_value: float (F 统计量)
            - df_error: int (误差自由度)
            - p_value: float (p 值)
            - n_group1: int (组1样本量)
            - n_group2: int (组2样本量)
            - mean_diff: float (均值差)
            - pooled_sd: float (合并标准差)
    Returns:
        StatCheck 列表，空列表表示无矛盾
    """
    checks: list[StatCheck] = []

    eta_sq = stats.get("eta_squared")
    d = stats.get("cohens_d")
    t = stats.get("t_value")
    df = stats.get("df")
    f = stats.get("f_value")
    df_error = stats.get("df_error")
    p = stats.get("p_value")
    n1 = stats.get("n_group1")
    n2 = stats.get("n_group2")
    mean_diff = stats.get("mean_diff")
    pooled_sd = stats.get("pooled_sd")

    # ── 校验 1: η² ↔ Cohen's d ──
    if eta_sq is not None and d is not None:
        # d = 2√(η²/(1-η²))
        d_expected = 2 * math.sqrt(eta_sq / (1 - eta_sq))
        tolerance = 0.15  # 允许 15% 误差
        ratio = abs(d - d_expected) / max(abs(d_expected), 0.001)
        consistent = ratio < tolerance
        checks.append(StatCheck(
            metric="η² ↔ Cohen's d",
            value_reported=f"d = {d}",
            value_expected=f"d = {d_expected:.3f} (from η²={eta_sq})",
            consistent=consistent,
            explanation=(
                f"根据 η²={eta_sq} 换算 d 应为 {d_expected:.3f}，"
                f"论文报告 d={d}，{'一致' if consistent else f'偏差 {ratio:.0%}，超出 {tolerance:.0%} 容忍范围'}"
            ),
        ))

    # ── 校验 2: t ↔ d (独立样本) ──
    if t is not None and df is not None and d is not None:
        # d = 2t/√df
        d_from_t = 2 * t / math.sqrt(df)
        tolerance = 0.15
        ratio = abs(d - d_from_t) / max(abs(d_from_t), 0.001)
        consistent = ratio < tolerance
        checks.append(StatCheck(
            metric="t ↔ Cohen's d",
            value_reported=f"d = {d}",
            value_expected=f"d = {d_from_t:.3f} (from t={t}, df={df})",
            consistent=consistent,
            explanation=(
                f"根据 t={t}, df={df} 换算 d 应为 {d_from_t:.3f}，"
                f"论文报告 d={d}，{'一致' if consistent else f'偏差 {ratio:.0%}'}"
            ),
        ))

    # ── 校验 3: F ↔ η² (单因素 ANOVA) ──
    if f is not None and df_error is not None and eta_sq is not None:
        # η² = F / (F + df_error)
        eta_sq_expected = f / (f + df_error)
        tolerance = 0.05  # η² 允许 0.05 绝对误差
        diff = abs(eta_sq - eta_sq_expected)
        consistent = diff < tolerance
        checks.append(StatCheck(
            metric="F ↔ η²",
            value_reported=f"η² = {eta_sq}",
            value_expected=f"η² = {eta_sq_expected:.4f} (from F={f}, df_error={df_error})",
            consistent=consistent,
            explanation=(
                f"根据 F={f}, df_error={df_error} 换算 η² 应为 {eta_sq_expected:.4f}，"
                f"论文报告 η²={eta_sq}，{'一致' if consistent else f'偏差 {diff:.4f}'}"
            ),
        ))

    # ── 校验 4: d ↔ mean_diff / pooled_sd ──
    if d is not None and mean_diff is not None and pooled_sd is not None:
        d_expected = mean_diff / pooled_sd
        tolerance = 0.15
        ratio = abs(d - d_expected) / max(abs(d_expected), 0.001)
        consistent = ratio < tolerance
        checks.append(StatCheck(
            metric="d ↔ mean_diff / pooled_sd",
            value_reported=f"d = {d}",
            value_expected=f"d = {d_expected:.3f} (from mean_diff={mean_diff}, pooled_sd={pooled_sd})",
            consistent=consistent,
            explanation=(
                f"根据均值差={mean_diff} 和合并标准差={pooled_sd} 计算得 d={d_expected:.3f}，"
                f"论文报告 d={d}，{'一致' if consistent else f'偏差 {ratio:.0%}'}"
            ),
        ))

    # ── 校验 5: p值合理性 ──
    if p is not None and t is not None and df is not None:
        # 对于大 df，|t| > 1.96 对应 p < 0.05（双尾）
        if df > 30:
            if abs(t) > 3.29 and p > 0.001:
                checks.append(StatCheck(
                    metric="p值 ↔ t统计量",
                    value_reported=f"p = {p}",
                    value_expected=f"p < 0.001 (|t|={abs(t)} > 3.29, df={df})",
                    consistent=False,
                    explanation=f"|t|={abs(t)} 远大于 3.29 但 p={p}，p 值可能过大",
                ))
            elif abs(t) < 1.0 and p < 0.05:
                checks.append(StatCheck(
                    metric="p值 ↔ t统计量",
                    value_reported=f"p = {p}",
                    value_expected=f"p > 0.05 (|t|={abs(t)} < 1.0, df={df})",
                    consistent=False,
                    explanation=f"|t|={abs(t)} 小于 1.0 但 p={p} < 0.05，p 值可能过小",
                ))

    # ── 校验 6: 效应量大小分类 ──
    if d is not None:
        size = "小" if abs(d) < 0.2 else "中" if abs(d) < 0.8 else "大" if abs(d) < 1.3 else "极大"
        # 只在有其他指标矛盾时才报告
        if any(not c.consistent for c in checks):
            checks.append(StatCheck(
                metric="效应量大小",
                value_reported=f"d = {d} ({size}效应)",
                value_expected="—",
                consistent=True,
                explanation=f"Cohen's d={d} 属于{size}效应范围",
            ))

    return checks


def format_statistics_report(checks: list[StatCheck]) -> str:
    """格式化统计一致性报告"""
    if not checks:
        return "## 📊 统计一致性校验\n\n✅ 未发现统计指标矛盾"

    issues = [c for c in checks if not c.consistent]
    lines = [f"## 📊 统计一致性校验\n"]
    lines.append(f"**校验项**: {len(checks)}")
    lines.append(f"**一致**: {len(checks) - len(issues)}")
    lines.append(f"**矛盾**: {len(issues)}\n")

    if issues:
        lines.append("### 🔴 发现矛盾\n")
        for c in issues:
            lines.append(f"- **{c.metric}**")
            lines.append(f"  - 报告值: {c.value_reported}")
            lines.append(f"  - 期望值: {c.value_expected}")
            lines.append(f"  - 说明: {c.explanation}")
            lines.append("")

    consistent = [c for c in checks if c.consistent]
    if consistent:
        lines.append("### ✅ 通过校验\n")
        for c in consistent:
            lines.append(f"- **{c.metric}**: {c.explanation}")

    if issues:
        lines.append(f"\n### 💡 建议\n")
        lines.append("- 重新计算统计指标，确保 η²、d、t、F 值之间的换算关系正确")
        lines.append("- 报告所有原始数据（均值、标准差、样本量），便于读者验证")
        lines.append("- 使用统计软件（SPSS/R/JASP）重新运行分析，确认结果")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# 3. 研究设计缺陷检测器
# ═══════════════════════════════════════════════════════════════

@dataclass
class DesignFlaw:
    """研究设计缺陷"""
    severity: str  # "P0" | "P1" | "P2"
    category: str  # 缺陷类别
    description: str  # 问题描述
    evidence: str  # 证据（论文中的原文片段）
    suggestion: str  # 修复建议


def detect_design_flaws(
    paper_text: str,
    design_info: dict | None = None,
) -> list[DesignFlaw]:
    """检测研究设计中的常见缺陷

    基于论文文本和可选的结构化设计信息，检测以下类别：
    1. 多要素未分离：单因素设计无法分离多个自变量的独立贡献
    2. 评估者偏差：实施者自评忠实度/效果
    3. 样本代表性：单一来源/小样本/便利抽样
    4. 霍桑效应替代解释：对照组未接受等量关注
    5. 追踪周期不足：效果持续性验证时间过短
    6. 测量工具验证不足：自编量表缺少关键心理测量学指标
    7. 混淆变量未控制：未控制可能影响结果的额外变量
    8. 选择偏差：实验组和对照组非随机分配

    Args:
        paper_text: 论文全文（用于文本模式匹配）
        design_info: 可选的结构化设计信息，可含:
            - design_type: "准实验" | "真实验" | "观察" 等
            - n_groups: 组数
            - group_labels: 组标签列表
            - has_control: bool
            - has_random_assignment: bool
            - intervention_elements: list[str] (干预要素)
            - fidelity_assessor: "self" | "independent" | "mixed"
            - tracking_weeks: int (追踪周期)
            - scale_validated: bool (量表是否经过完整验证)
            - sample_source: str (样本来源)
            - sample_size: int
    Returns:
        DesignFlaw 列表
    """
    flaws: list[DesignFlaw] = []
    design_info = design_info or {}
    text_lower = paper_text.lower()

    # ── 检测 1: 多要素未分离 ──
    elements = design_info.get("intervention_elements", [])
    if not elements:
        # 从文本中推断
        if "户外" in paper_text and "建构" in paper_text and "主题" in paper_text:
            elements = ["户外", "主题建构"]
        elif "户外" in paper_text and "游戏" in paper_text:
            elements = ["户外", "游戏"]

    n_groups = design_info.get("n_groups", 0)
    if not n_groups:
        # 从文本推断组数
        if "实验组" in paper_text and "对照组" in paper_text:
            n_groups = 2

    if len(elements) >= 2 and n_groups <= 2:
        # 检查是否设置了多水平对比组
        has_multi_level = any(kw in paper_text for kw in
                             ["多水平", "对比组", "不同强度", "自由建构组", "室内主题建构组"])
        if not has_multi_level:
            flaws.append(DesignFlaw(
                severity="P0",
                category="多要素未分离",
                description=(
                    f"研究包含 {len(elements)} 个干预要素（{'/'.join(elements)}），"
                    f"但仅设置了 {n_groups} 组（实验组+对照组），无法分离各要素的独立贡献"
                ),
                evidence="未设置不同干预强度的对比组（如自由建构组、室内主题建构组）",
                suggestion=(
                    "建议设置多水平实验条件（如：户外主题建构组 vs 室内主题建构组 vs 户外自由建构组），"
                    "以精确分离各要素的贡献。如无法增加组数，应在局限性中明确说明"
                ),
            ))

    # ── 检测 2: 评估者偏差 ──
    assessor = design_info.get("fidelity_assessor", "")
    if not assessor:
        # 从文本推断
        if "忠实度" in paper_text or "忠实性" in paper_text:
            if any(kw in paper_text for kw in ["教师自评", "实施教师", "带班教师评估"]):
                assessor = "self"
            elif "独立观察" in paper_text or "第三方评估" in paper_text:
                assessor = "independent"

    if assessor == "self":
        flaws.append(DesignFlaw(
            severity="P1",
            category="评估者偏差",
            description="忠实度/干预效果由实施教师自评，存在评估者偏差（社会赞许性偏差）",
            evidence="忠实度评估由实施教师自行完成",
            suggestion="建议引入独立观察者进行忠实度评估，或计算评估者间一致性（ICC/Kendall's W）",
        ))

    # ── 检测 3: 样本代表性 ──
    sample_source = design_info.get("sample_source", "")
    sample_size = design_info.get("sample_size", 0)
    if not sample_source:
        # 从文本推断
        if "一所" in paper_text and ("幼儿园" in paper_text or "学校" in paper_text):
            sample_source = "单一机构"
            # 提取样本量
            size_match = re.search(r'(\d+)\s*(名|个|位).*(?:幼儿|儿童|学生|被试)', paper_text)
            if size_match and not sample_size:
                sample_size = int(size_match.group(1))

    if sample_source == "单一机构" or (sample_size and sample_size < 100):
        flaws.append(DesignFlaw(
            severity="P1",
            category="样本代表性不足",
            description=(
                f"样本来自{'单一机构' if sample_source == '单一机构' else '小样本'}"
                f"{'，样本量 ' + str(sample_size) if sample_size else ''}，"
                f"结果可推广性受限"
            ),
            evidence=f"样本来源: {sample_source or '未明确'}，样本量: {sample_size or '未明确'}",
            suggestion="建议在局限性中说明，并在未来研究中扩大样本来源（多机构/多地区）",
        ))

    # ── 检测 4: 霍桑效应 ──
    has_control = design_info.get("has_control", True)
    if has_control and n_groups == 2:
        # 检查对照组是否接受等量关注
        equal_attention = any(kw in paper_text for kw in
                              ["等量关注", "安慰剂", "活性对照", "等量干预", "替代干预"])
        if not equal_attention:
            # 进一步检查：如果对照组只做"常规活动"，可能存在霍桑效应
            if ("常规" in paper_text and "对照" in paper_text and
                    "等量" not in paper_text and "活性" not in paper_text):
                flaws.append(DesignFlaw(
                    severity="P2",
                    category="霍桑效应替代解释",
                    description="对照组仅进行常规活动，实验组接受特别干预，可能存在霍桑效应",
                    evidence="对照组进行常规教育活动，实验组接受系统化干预",
                    suggestion="建议在讨论中排除霍桑效应替代解释，或设置活性对照组（接受等量关注的非目标干预）",
                ))

    # ── 检测 5: 追踪周期不足 ──
    tracking_weeks = design_info.get("tracking_weeks", 0)
    if not tracking_weeks:
        track_match = re.search(r'(?:追踪|跟踪)\s*(\d+)\s*周', paper_text)
        if track_match:
            tracking_weeks = int(track_match.group(1))

    if 0 < tracking_weeks <= 4:
        flaws.append(DesignFlaw(
            severity="P2",
            category="追踪周期不足",
            description=f"追踪周期仅 {tracking_weeks} 周，难以充分验证干预效果的长期稳定性",
            evidence=f"追踪测试在干预结束后第 {tracking_weeks} 周进行",
            suggestion="建议将追踪周期延长至 3-6 个月甚至 1 年，以充分考察效果持续性",
        ))

    # ── 检测 6: 测量工具验证不足 ──
    scale_validated = design_info.get("scale_validated")
    if scale_validated is False:
        flaws.append(DesignFlaw(
            severity="P1",
            category="测量工具验证不足",
            description="自编量表缺少关键心理测量学指标",
            evidence="缺少各维度 Cronbach's α、CFA 拟合指标、评分者一致性、内容效度等",
            suggestion="补充报告各维度信度、验证性因素分析拟合指标（CFI/TLI/RMSEA/SRMR）、评分者间一致性（ICC）",
        ))
    elif scale_validated is None:
        # 从文本推断
        if "自编" in paper_text and "量表" in paper_text:
            missing = []
            if "维度" in paper_text and "Cronbach" not in paper_text and "α" not in paper_text:
                missing.append("各维度 Cronbach's α")
            if "CFA" not in paper_text and "验证性因素" not in paper_text:
                missing.append("CFA 拟合指标")
            if "ICC" not in paper_text and "评分者" not in paper_text and "一致性" not in paper_text:
                missing.append("评分者间一致性")
            if "内容效度" not in paper_text and "CVI" not in paper_text:
                missing.append("内容效度")

            if missing:
                flaws.append(DesignFlaw(
                    severity="P1",
                    category="测量工具验证不足",
                    description=f"自编量表缺少以下心理测量学指标: {'、'.join(missing)}",
                    evidence=f"缺失指标: {', '.join(missing)}",
                    suggestion=f"补充报告: {'；'.join(missing)}",
                ))

    # ── 检测 7: 非随机分配 ──
    has_random = design_info.get("has_random_assignment")
    if has_random is None:
        if "准实验" in paper_text:
            has_random = False
    if has_random is False:
        flaws.append(DesignFlaw(
            severity="P2",
            category="非随机分配",
            description="准实验设计，被试非随机分配到各组，可能存在选择偏差",
            evidence="采用准实验设计（非随机分配）",
            suggestion="建议在前测中检验两组同质性，或在分析中使用协方差分析控制前测差异",
        ))

    # ── 检测 8: 统计检验力不足 ──
    if sample_size and sample_size > 0:
        # G*Power 经验值：中等效应量 d=0.5, α=0.05, power=0.80 需约 64 人/组
        per_group = sample_size // max(n_groups, 1)
        if per_group < 30:
            flaws.append(DesignFlaw(
                severity="P2",
                category="统计检验力不足",
                description=f"每组样本量 {per_group} 人，统计检验力可能不足（建议每组 ≥ 30）",
                evidence=f"总样本 {sample_size}，{n_groups} 组，每组约 {per_group} 人",
                suggestion="进行事后统计检验力分析（G*Power），报告实际 power 值",
            ))

    return flaws


async def detect_design_flaws_llm(
    paper_text: str,
    design_info: dict | None = None,
    call_llm=None,
) -> list[DesignFlaw]:
    """学科无关的研究设计缺陷检测（LLM 语义分析）。

    同步的 detect_design_flaws 硬编码了教育/心理学关键词（"户外/建构/幼儿园/教师自评"
    等），传入其他学科（医学、工程、经济、计算机…）时几乎恒返回空结果——"名不副实"。
    本函数改用 LLM 做语义级设计审查，覆盖任意学科，作为启发式的兜底与补充。

    Args:
        paper_text: 论文全文
        design_info: 可选结构化设计信息（会作为提示附加给 LLM）
        call_llm: async(prompt, system) -> str 的 LLM 调用函数（由调用方注入，
                  避免 validators ← tools 循环导入）。为 None 时直接返回空列表。
    Returns:
        DesignFlaw 列表（LLM 无法解析时 fail-open 返回空）
    """
    if not paper_text or not paper_text.strip() or call_llm is None:
        return []

    import json as _json

    design_hint = ""
    if design_info:
        try:
            design_hint = "\n【已知设计信息】\n" + _json.dumps(design_info, ensure_ascii=False)
        except Exception:
            design_hint = ""

    system = (
        "你是严谨的科研方法学审稿人，精通各学科（自然科学/工程/医学/社会科学/人文）的"
        "研究设计。请只输出 JSON，不要任何解释性文字。"
    )
    prompt = f"""请审查以下论文的研究设计，找出方法学缺陷。适用于任意学科，不要假设是教育学。

关注但不限于：变量混淆/未分离、缺对照或对照不当、非随机分配导致的选择偏差、
样本代表性与样本量、测量工具的信效度、评估者/实验者偏差、追踪或随访周期、
统计检验力、可重复性、伦理与数据可得性等。

请按严重程度分级：P0（致命，结论不可信）、P1（重要，需补充数据或讨论）、P2（建议优化）。

严格输出如下 JSON（flaws 可为空数组）：
{{"flaws": [{{"severity": "P0|P1|P2", "category": "缺陷类别", "description": "问题描述", "evidence": "论文中的依据或缺失点", "suggestion": "改进建议"}}]}}
{design_hint}

论文全文：
{paper_text[:10000]}"""

    try:
        raw = await call_llm(prompt, system)
    except Exception as e:
        logger.warning("detect_design_flaws_llm call failed: %s", e)
        return []

    if not raw or raw.startswith("❌"):
        return []

    # 容错解析：剥离 ```json 围栏、截取首个 { 到末个 }
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    lb, rb = text.find("{"), text.rfind("}")
    if lb >= 0 and rb > lb:
        text = text[lb:rb + 1]

    flaws: list[DesignFlaw] = []
    try:
        data = _json.loads(text)
        for item in data.get("flaws", []):
            sev = str(item.get("severity", "P2")).upper()
            if sev not in ("P0", "P1", "P2"):
                sev = "P2"
            flaws.append(DesignFlaw(
                severity=sev,
                category=str(item.get("category", "设计问题"))[:80],
                description=str(item.get("description", ""))[:500],
                evidence=str(item.get("evidence", ""))[:500],
                suggestion=str(item.get("suggestion", ""))[:500],
            ))
    except Exception as e:
        logger.warning("detect_design_flaws_llm parse failed: %s | raw=%s", e, raw[:200])
        return []

    return flaws


def _dedup_flaws(flaws: list[DesignFlaw]) -> list[DesignFlaw]:
    """按 (category, description 前 40 字) 去重，合并启发式与 LLM 双路结果。"""
    seen: set[tuple[str, str]] = set()
    out: list[DesignFlaw] = []
    for f in flaws:
        key = (f.category.strip(), f.description.strip()[:40])
        if key in seen:
            continue
        seen.add(key)
        out.append(f)
    return out


def format_design_report(flaws: list[DesignFlaw]) -> str:
    """格式化研究设计缺陷报告"""
    if not flaws:
        return "## 🔬 研究设计缺陷检测\n\n✅ 未发现明显设计缺陷"

    p0 = [f for f in flaws if f.severity == "P0"]
    p1 = [f for f in flaws if f.severity == "P1"]
    p2 = [f for f in flaws if f.severity == "P2"]

    lines = [f"## 🔬 研究设计缺陷检测\n"]
    lines.append(f"**总计**: {len(flaws)} 个缺陷")
    lines.append(f"**P0 严重**: {len(p0)} | **P1 重要**: {len(p1)} | **P2 建议**: {len(p2)}\n")

    for severity, label in [("P0", "🔴 P0 严重缺陷"), ("P1", "🟡 P1 重要问题"), ("P2", "🟢 P2 优化建议")]:
        items = [f for f in flaws if f.severity == severity]
        if items:
            lines.append(f"### {label}\n")
            for f in items:
                lines.append(f"**{f.category}**")
                lines.append(f"- 严重度: {f.severity}")
                lines.append(f"- 描述: {f.description}")
                lines.append(f"- 证据: {f.evidence}")
                lines.append(f"- 建议: {f.suggestion}")
                lines.append("")

    lines.append("### 💡 总体建议\n")
    lines.append("- P0 缺陷应在论文局限性中明确讨论，并尽可能通过设计改进来缓解")
    lines.append("- P1 缺陷应在论文中补充相关数据或讨论")
    lines.append("- P2 缺陷建议在未来研究中改进")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# 统一入口：运行所有验证器
# ═══════════════════════════════════════════════════════════════

async def run_all_validators(
    papers: list[dict] | None = None,
    stats: dict | None = None,
    paper_text: str = "",
    design_info: dict | None = None,
    enable_online_citation: bool = True,
) -> str:
    """运行所有验证器，返回综合报告

    Args:
        papers: 文献列表（用于引用验证）
        stats: 统计指标字典（用于统计一致性校验）
        paper_text: 论文全文（用于设计缺陷检测）
        design_info: 设计信息（用于设计缺陷检测）
        enable_online_citation: 是否启用在线引用验证
    Returns:
        综合验证报告（Markdown）
    """
    sections = []

    if papers:
        checks = await verify_citation_authenticity(papers, enable_online=enable_online_citation)
        sections.append(format_citation_report(checks))

    if stats:
        stat_checks = check_statistics_consistency(stats)
        sections.append(format_statistics_report(stat_checks))

    if paper_text:
        flaws = detect_design_flaws(paper_text, design_info)
        sections.append(format_design_report(flaws))

    if not sections:
        return "ℹ️ 未提供验证数据，跳过验证"

    return "\n\n---\n\n".join(sections)
