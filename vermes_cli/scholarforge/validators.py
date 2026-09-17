"""
ScholarForge 质量验证器 — 论文生成后的质量门控

三个高优先级验证器：
1. CitationAuthenticityVerifier — 文献引用真实性验证（DOI/CrossRef/知网 API 校验）
2. StatisticsConsistencyChecker — 统计指标内部一致性校验（η²↔d↔t↔F 值换算）
3. ResearchDesignDetector — 研究设计缺陷检测（多要素未分离/评估者偏差等）

使用方式：
    from vermes_cli.scholarforge.validators import (
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
    suspicious = sum(1 for c in checks if not c.verified and c.source != "api_unavailable" and c.confidence <= 0.3)

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
            if not c.verified and c.confidence <= 0.3:
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


# ═══════════════════════════════════════════════════════════════
# 2.1 精确 p 值（纯 Python，不依赖 scipy）
# ═══════════════════════════════════════════════════════════════
# 🔴 为什么不用 scipy：`vermes-backend.spec` 的 excludes 里**明确排除了 scipy**
#    （为控制包体积），打进 DMG 的后端里 import scipy 会直接 ModuleNotFoundError。
#    故这里用 Numerical Recipes 的连分式算法实现正则化不完全 Beta 函数。
#    精度实测：与 scipy.stats 在 df=1/10/39/100/200、p 从 6e-1 到 2.5e-6 全量比对，
#    相对误差 ~1e-15（机器精度），对「跨阈值判定」这个用途绰绰有余。

def _betacf(a: float, b: float, x: float, itmax: int = 200,
            eps: float = 3e-16, fpmin: float = 1e-300) -> float:
    """不完全 Beta 函数的连分式展开（Lentz 算法，Numerical Recipes 6.4）"""
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < fpmin:
        d = fpmin
    d = 1.0 / d
    h = d
    for m in range(1, itmax + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        dele = d * c
        h *= dele
        if abs(dele - 1.0) < eps:
            break
    return h


def _betai(a: float, b: float, x: float) -> float:
    """正则化不完全 Beta 函数 I_x(a, b)"""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    front = math.exp(lbeta + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def t_p_two_tailed(t: float, df: float) -> float:
    """t 检验双尾 p 值：p = I_{df/(df+t²)}(df/2, 1/2)。非法输入返回 NaN。"""
    try:
        df = float(df)
        t = abs(float(t))
    except (TypeError, ValueError):
        return float("nan")
    if df <= 0:
        return float("nan")
    if t == 0.0:
        return 1.0
    return _betai(df / 2.0, 0.5, df / (df + t * t))


def f_p_right_tail(f: float, df_between: float, df_error: float) -> float:
    """F 检验右尾 p 值：p = I_{df2/(df2+df1·F)}(df2/2, df1/2)。非法输入返回 NaN。"""
    try:
        df1 = float(df_between)
        df2 = float(df_error)
        f = float(f)
    except (TypeError, ValueError):
        return float("nan")
    if df1 <= 0 or df2 <= 0 or f < 0:
        return float("nan")
    if f == 0.0:
        return 1.0
    return _betai(df2 / 2.0, df1 / 2.0, df2 / (df2 + df1 * f))


def _gammq(a: float, x: float) -> float:
    """正则化**上**不完全 Gamma 函数 Q(a, x) = 1 − P(a, x)（纯 Python）。

    🔴 scipy 被 `vermes-backend.spec` 列进 EXCLUDES，打包后的后端 `import scipy`
    必抛 `ModuleNotFoundError` —— 与 `_betai` 同样必须自己实现。
    走 Numerical Recipes 的两段式（与 `_betai` 同构，精度 ~1e-15）：
      · x < a+1：级数求 P(a,x) 再取补（收敛快）
      · x ≥ a+1：修正 Lentz 连分式直接求 Q（级数此时收敛慢且会抵消丢失精度）
    """
    if x < 0.0 or a <= 0.0:
        return float("nan")
    if x == 0.0:
        return 1.0
    if x < a + 1.0:
        ap, term, total = a, 1.0 / a, 1.0 / a
        for _ in range(1000):
            ap += 1.0
            term *= x / ap
            total += term
            if abs(term) < abs(total) * 1e-16:
                break
        return 1.0 - total * math.exp(-x + a * math.log(x) - math.lgamma(a))
    tiny = 1e-300
    b = x + 1.0 - a
    c = 1.0 / tiny
    d = 1.0 / b
    h = d
    for i in range(1, 1000):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < tiny:
            d = tiny
        c = b + an / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-16:
            break
    return math.exp(-x + a * math.log(x) - math.lgamma(a)) * h


def chi2_p_right_tail(chi2: float, df: float) -> float:
    """χ² 检验右尾 p 值：p = Q(df/2, χ²/2)。非法输入返回 NaN。

    卡方在教育学的**类别变量**分析里是主力（性别×是否留守、生源地×入学准备水平…），
    此前本模块只能校 t / F，卡方表粘进来是"能看不能校"。
    """
    try:
        a = float(df) / 2.0
        x = float(chi2) / 2.0
    except (TypeError, ValueError):
        return float("nan")
    if a <= 0 or x < 0:
        return float("nan")
    if x == 0.0:
        return 1.0
    return _gammq(a, x)


def r_p_two_tailed(r: float, n: float) -> float:
    """Pearson 相关 r 的双尾 p 值：t = r·√((n−2)/(1−r²))，df = n−2。非法输入 NaN。"""
    try:
        nn = float(n)
        rr = float(r)
    except (TypeError, ValueError):
        return float("nan")
    if nn <= 2.0 or abs(rr) >= 1.0:
        return float("nan")
    if rr == 0.0:
        return 1.0
    return t_p_two_tailed(rr * math.sqrt((nn - 2.0) / (1.0 - rr * rr)), nn - 2.0)


def z_p_two_tailed(z: float) -> float:
    """标准正态双尾 p = erfc(|z|/√2)。非法输入返回 NaN。

    🔴 `math.erfc` 是**标准库**，不需要 scipy（scipy 已被 spec excludes）——
    这也是非参数检验里唯一能精确算的一类：Mann-Whitney U / Wilcoxon 的
    「渐近显著性」本身就是 SPSS 用正态近似算出来的，与这里的口径**完全一致**。

    注意：SPSS 的小样本会另给「精确显著性」（Exact），那是**精确分布**而非正态近似，
    两者本就不同 —— 故本函数只用来校「渐近显著性」，不校精确 p（见调用处的闸门）。
    """
    try:
        zz = abs(float(z))
    except (TypeError, ValueError):
        return float("nan")
    if zz != zz:  # NaN
        return float("nan")
    return math.erfc(zz / math.sqrt(2.0))


def _p_verdict(expected_p: float, reported_p: float) -> str:
    """判断「精确 p 值」与「论文报告的 p 值」是否构成可断言的矛盾。

    返回 "" 表示不断言（未校验）；否则返回矛盾方向（"过大" / "过小"）。

    🔴 判定纪律（宁可放过、不可误伤），两条规则各自都有必须如此的理由：
      · 「报告 p 过大」：expected < 0.05（真值显著）且 reported > 0.05（报告不显著）
        且 reported ≥ expected×5 才断言。
        - `reported > 0.05` 这道闸是为了避开「p < .05」「p < .001」这类**上界写法**
          ——它们被解析成 0.05 / 0.001，天然大于真值，属合法报告而非错误。
        - 🔴 2026-09-17 放宽：原门槛是 `expected < 0.001`，**放过了整整一类最严重的
          抄错** —— 真值 p=.019（显著）却报告 p=.919（不显著），即「把显著写成不显著」，
          旧规则不判（因为 .019 不小于 .001）。而 `expected < 0.001` 对避上界并**无必要**
          （上界污染由 `reported > 0.05` 单独挡住），故放宽到 α 阈值本身。
        - ×5 余量：避开临界抖动（.049 vs .051），也兼容常见的多重比较校正
          （Bonferroni 通常抬高 3–5 倍）。判定文本里会显式提示校正值属正常。
      · 「报告 p 过小」：只有 expected > 0.05、reported < 0.05 且相差 10 倍以上才断言。
        上界写法只会把 reported 抬高，不会压低，故这个方向不受上界污染；
        留 10 倍余量是为了兼容**单尾 p**（与双尾恰好差 2 倍，是合法差异）。
    """
    if expected_p != expected_p or reported_p != reported_p:  # NaN 不参与判定
        return ""
    if not (0.0 < reported_p <= 1.0):  # p=0（"< .001" 被解析没了）或越界值无法判定
        return ""
    if expected_p < 0.05 and reported_p > 0.05 and reported_p >= expected_p * 5:
        return "过大"
    if expected_p > 0.05 and reported_p < 0.05 and reported_p * 10 < expected_p:
        return "过小"
    return ""


def check_statistics_consistency(
    stats: dict[str, Any],
) -> list[StatCheck]:
    """校验统计指标的内部一致性

    支持校验的指标对：
    1. η² ↔ Cohen's d:  d = 2√(η²/(1-η²))
       🔴 **仅适用于两组比较**。df_between ≥ 2 时 η² 是整体模型效应量，
       同一个 η² 可对应多组间各不相同的两两 d，无唯一换算值 → 输出「未校验」而非判矛盾。
    2. t ↔ d:  **按检验类型取候选式**（任一吻合即通过）：
         独立样本（严格式）      d = 2t/√(df+2)
         独立样本（教科书近似）  d = 2t/√df
         配对样本（d_z）         d = t/√(df+1)
       可用 t_test_type 指定类型以缩小候选集。详见校验 2 的 2026-09-16 修正说明。
    3. F ↔ η²:  η² = F·df_between/(F·df_between + df_error)  (单因素 ANOVA；
       df_between 缺省时只能算出 df1=1 的理论**下界**，见校验 3 的四分支说明)
    4. d ↔ mean_diff / pooled_sd:  d = mean_diff / pooled_sd
    5. d ↔ r:  r = d/√(d² + A)，A = (n1+n2)(n1+n2-2)/(n1·n2)；
       等样本量的大样本极限 A→4，即教科书常见的 r = d/√(d²+4)。
       要求 r 是**二分组与连续变量的点二列相关**；配对 d_z 或两个连续变量的
       Pearson r 都不适用，故解释文本里会显式提示这一前提。
    6. p值 ↔ t / F:  **精确计算**双尾（右尾）p 值，不再用 |t|>3.29 之类的粗启发式，
       且小样本（df ≤ 30）同样覆盖。判定纪律见 `_p_verdict` 的注释 ——
       只在跨过显著性阈值且数量级差 10 倍以上时断言，以兼容单尾 p 与「p < .001」上界写法。
    7. p值 ↔ χ²:  右尾 p = Q(df/2, χ²/2)（纯 Python 上不完全 Gamma，见 `_gammq`）。
       2026-09-17 新增：卡方表此前"能看不能校"。
    8. p值 ↔ r:   t = r·√((n−2)/(1−r²))，df = n−2，再走 t 双尾（见 `r_p_two_tailed`）。
       要求提供样本量 n，否则无法换算（缺 n 就不校验，不猜）。
    9. p值 ↔ Z:   标准正态双尾 p = erfc(|Z|/√2)（math.erfc，stdlib）。
       2026-09-17 新增。口径 = SPSS 的「渐近显著性」；小样本的「精确显著性」
       是精确分布、与正态近似本就不同，故只校前者。

    Args:
        stats: 统计指标字典，可含:
            - eta_squared: float (η²)
            - cohens_d: float (Cohen's d)
            - t_value: float (t 统计量)
            - df: int (自由度)
            - t_test_type: str ("paired" 配对样本 / "independent" 独立样本；缺省则三种候选式都试)
            - f_value: float (F 统计量)
            - df_between: int (组间自由度，单因素 ANOVA = 组数-1；缺省时 F↔η² 按两组比较估算)
            - df_error: int (误差自由度)
            - p_value: float (p 值)
            - r_value: float (相关系数 r，应为二分组的点二列相关)
            - n_group1: int (组1样本量)
            - n_group2: int (组2样本量)
            - mean_diff: float (均值差)
            - pooled_sd: float (合并标准差)
            - chi_square: float (χ²；卡方检验 / 克鲁斯卡尔-沃利斯 H / 巴特利特球形度检验)
            - n: int (样本量；供 r↔p 换算使用)
            - z_value: float (Z；Mann-Whitney U / Wilcoxon 的正态近似)
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
    # 组间自由度（单因素 ANOVA 中 = 组数-1）。2026-09-16 新增：缺它时 F↔η² 换算
    # 只能在「两组比较」前提下成立，三组及以上会误判（详见校验 3 的说明）。
    df_between = stats.get("df_between")
    p = stats.get("p_value")
    r = stats.get("r_value")
    t_test_type = (stats.get("t_test_type") or "").strip().lower()
    n1 = stats.get("n_group1")
    n2 = stats.get("n_group2")
    mean_diff = stats.get("mean_diff")
    pooled_sd = stats.get("pooled_sd")
    chi2 = stats.get("chi_square")
    n = stats.get("n")

    # ── 校验 1: η² ↔ Cohen's d ──
    if eta_sq is not None and d is not None:
        if not (isinstance(eta_sq, (int, float)) and 0.0 <= float(eta_sq) < 1.0):
            # 兜底：η² 越界会让 √(η²/(1-η²)) 抛 domain error / 出 NaN，先拦住
            checks.append(StatCheck(
                metric="η² 取值范围",
                value_reported=f"η² = {eta_sq}",
                value_expected="0 ≤ η² < 1",
                consistent=False,
                explanation=f"η² 必须落在 [0, 1) 区间，报告值 {eta_sq} 不合法，无法换算（请核对是否把百分比 14 当成了 0.14）。",
            ))
        elif df_between is not None and int(df_between) >= 2:
            # 🔴 2026-09-16 新增「适用条件闸门」：d = 2√(η²/(1-η²)) 是 η² = d²/(d²+4)
            #    的逆运算，而该恒等式**只在两组比较时成立**。三组及以上时 η² 描述整体模型，
            #    同一个 η² 可以对应多组之间各不相同的两两 d，硬算必然误判。
            #    处理沿用校验 3 的纪律：说「未校验」，既不猜「一致」放过错误，也不报「矛盾」误伤正确报告。
            checks.append(StatCheck(
                metric="η² ↔ Cohen's d（未校验）",
                value_reported=f"d = {d}",
                value_expected="—（多组 ANOVA 无唯一对应 d）",
                consistent=True,
                explanation=(
                    f"组间自由度 df_between={int(df_between)}（≥2，即三组及以上）时，η² 是**整体模型**的效应量，"
                    f"而 Cohen's d 描述的是**两两**组间差异 —— 同一个 η² 可以对应多组之间各不相同的 d，"
                    f"不存在唯一换算值，故不作判定。若要比对，请给出**两两比较**的 d 及其对应的两组 η²，"
                    f"或改用 η² 直接报告效应量。"
                ),
            ))
        else:
            # d = 2√(η²/(1-η²))
            d_expected = 2 * math.sqrt(float(eta_sq) / (1 - float(eta_sq)))
            tolerance = 0.15  # 允许 15% 误差
            # 只比量级：η² 恒正，对应的是 |d|；d 的符号取决于哪组减哪组
            ratio = abs(abs(d) - d_expected) / max(abs(d_expected), 0.001)
            consistent = ratio < tolerance
            _hint = (
                ""
                if df_between is not None
                else "若该 η² 来自三组及以上 ANOVA，此换算不适用（请补 df_between 以启用多组判定）。"
            )
            checks.append(StatCheck(
                metric="η² ↔ Cohen's d",
                value_reported=f"d = {d}",
                value_expected=f"d = {d_expected:.3f} (from η²={eta_sq})",
                consistent=consistent,
                explanation=(
                    f"根据 η²={eta_sq} 换算 d 应为 {d_expected:.3f}，"
                    f"论文报告 d={d}，{'一致' if consistent else f'偏差 {ratio:.0%}，超出 {tolerance:.0%} 容忍范围'}。"
                    f"{_hint}"
                ),
            ))

    # ── 校验 2: t ↔ d ──
    # 🔴 2026-09-16 修正：原实现只认 d = 2t/√df —— 这仅是**独立样本**的教科书近似式，
    #    于是两类真实且正确的论文报告被误判成「矛盾」：
    #    ① 配对样本：正确换算是 d_z = t/√(df+1)（df = 配对对数-1）。
    #       实测 t=6.92, df=39：正确的 d_z = 1.094，旧式给 2.216，**偏差 51%** → 误报。
    #    ② 独立样本小 df：严格式是 d = 2t/√(df+2)，旧式 2t/√df 系统性偏高，
    #       df=4 时偏高 18.4%（已超 15% 容差）、df=10 偏高 8.7% → 小样本误报。
    #    修法：改为「候选集」——三个式子任一吻合即判一致；可用 t_test_type 指定类型缩小候选集。
    if t is not None and df is not None and d is not None and float(df) > 0:
        tolerance = 0.15
        # 三元组 (简称, 公式, 值)。简称必须**互不相同** —— 早期版本用「独立样本」统一称呼
        # 严格式与近似式，导致解释文本里出现「独立样本 → 2.161；独立样本 → 2.216」这种
        # 分不清谁是谁的输出，读者根本无法据此判断该用哪个口径。
        candidates: list[tuple[str, str, float]] = []
        if t_test_type in ("paired", "配对", "related", "within", "repeated"):
            candidates.append(("配对样本 d_z", "t/√(df+1)", float(t) / math.sqrt(float(df) + 1)))
        elif t_test_type in ("independent", "独立", "unrelated", "between"):
            candidates.append(("独立样本·严格式", "2t/√(df+2)", 2 * float(t) / math.sqrt(float(df) + 2)))
            candidates.append(("独立样本·教科书近似", "2t/√df", 2 * float(t) / math.sqrt(float(df))))
        else:
            # 未指定 → 三种都试（口径最宽，宁可放过）
            candidates.append(("独立样本·严格式", "2t/√(df+2)", 2 * float(t) / math.sqrt(float(df) + 2)))
            candidates.append(("独立样本·教科书近似", "2t/√df", 2 * float(t) / math.sqrt(float(df))))
            candidates.append(("配对样本 d_z", "t/√(df+1)", float(t) / math.sqrt(float(df) + 1)))

        # 只比量级：d 的符号取决于哪组减哪组，正负号不构成矛盾（否则误报率极高）
        d_abs = abs(float(d))
        best_name, best_formula, best_val, best_ratio = "", "", 0.0, None
        for name, formula, val in candidates:
            ratio = abs(d_abs - abs(val)) / max(abs(val), 0.001)
            if best_ratio is None or ratio < best_ratio:
                best_name, best_formula, best_val, best_ratio = name, formula, val, ratio
        consistent = best_ratio is not None and best_ratio < tolerance
        all_forms = "；".join(f"{n} {f} → {v:.3f}" for n, f, v in candidates)
        if consistent:
            detail = (
                f"一致（已指定 t_test_type={t_test_type!r}）"
                if t_test_type
                else f"一致（未指定检验类型，三种候选式取最贴近者：{all_forms}）"
            )
        else:
            detail = (
                f"偏差 {best_ratio:.0%} —— 候选式均不吻合（{all_forms}）。"
                f"t↔d 的换算式随检验类型而变：配对样本 d_z = t/√(df+1)，"
                f"独立样本 d = 2t/√df（严格为 2t/√(df+2)）。"
                f"若三者都对不上，通常是 d 与 t 并非来自同一次检验（例如 d 来自两两比较、t 来自整体模型）。"
            )
        checks.append(StatCheck(
            metric="t ↔ Cohen's d",
            value_reported=f"d = {d}",
            value_expected=f"d = {best_val:.3f} (from t={t}, df={df}，{best_name} {best_formula})",
            consistent=consistent,
            explanation=(
                f"根据 t={t}, df={df} 按「{best_name} {best_formula}」换算 d 应为 {best_val:.3f}，"
                f"论文报告 d={d}，{detail}"
            ),
        ))

    # ── 校验 3: F ↔ η² (单因素 ANOVA) ──
    if f is not None and df_error is not None and eta_sq is not None:
        # 🔴 2026-09-16 修正：原实现为 η² = F/(F+df_error)，**漏了组间自由度**。
        #   推导：η² = SS_b/(SS_b+SS_e)，而 F = (SS_b/df1)/(SS_e/df2)
        #         → SS_b = F·MS_e·df1, SS_e = MS_e·df2
        #         → η² = F·df1 / (F·df1 + df2)
        #   两个式子**仅在 df1=1（两组比较）时等价**；三组及以上用旧式会系统性低估 η²，
        #   实测 F=8.0/df1=2/df2=87：旧式给 0.0842，真值 0.1553 —— 于是把**正确报告**
        #   判成「矛盾」（误报）。此前的测试数据恰好都是 F(1,58) 这类 df1=1，故未暴露。
        assumed_two_group = df_between is None
        k = 1 if assumed_two_group else max(1, int(df_between))
        eta_sq_expected = (f * k) / (f * k + df_error)
        # 容差改「相对为主 + 绝对下限」：η² 是 0~0.2 量级的量，原先固定 0.05 绝对容差
        # 分辨力过差（0.087 与 0.045 相差近一倍仍判「一致」）。
        tolerance = max(0.01, abs(eta_sq_expected) * 0.20)
        diff = abs(eta_sq - eta_sq_expected)
        consistent = diff < tolerance

        if not assumed_two_group:
            # ① 传了组间自由度 → 公式与期望值都确定，严格判定。
            checks.append(StatCheck(
                metric="F ↔ η²",
                value_reported=f"η² = {eta_sq}",
                value_expected=f"η² = {eta_sq_expected:.4f} (from F={f}, df1={k}, df_error={df_error})",
                consistent=consistent,
                explanation=(
                    f"根据 F={f}, df1={k}, df_error={df_error} 换算 η² 应为 {eta_sq_expected:.4f}，"
                    f"论文报告 η²={eta_sq}，"
                    f"{'一致' if consistent else f'偏差 {diff:.4f}，超出容差 {tolerance:.4f}'}"
                ),
            ))
        elif consistent:
            # ② 未传 df_between，但报告值与 df1=1 的下界吻合 → 两组场景，正常通过。
            checks.append(StatCheck(
                metric="F ↔ η²",
                value_reported=f"η² = {eta_sq}",
                value_expected=f"η² = {eta_sq_expected:.4f} (from F={f}, df1=1(假定), df_error={df_error})",
                consistent=True,
                explanation=(
                    f"按两组比较（df1=1）换算 η² 应为 {eta_sq_expected:.4f}，"
                    f"论文报告 η²={eta_sq}，一致"
                ),
            ))
        elif eta_sq < eta_sq_expected:
            # ③ **低于下界 → 必然矛盾，可安全断言**：η² 关于 df1 单调递增而 df1≥1，
            #    故 df1=1 的换算值已是理论最小值，报告值比它还小就不可能成立。
            checks.append(StatCheck(
                metric="F ↔ η²",
                value_reported=f"η² = {eta_sq}",
                value_expected=f"η² ≥ {eta_sq_expected:.4f}（df1=1 时的理论下界）",
                consistent=False,
                explanation=(
                    f"η² 不可能低于 {eta_sq_expected:.4f}：换算关系 η²=F·df1/(F·df1+df2) 关于 df1 "
                    f"单调递增，而组间自由度 df1≥1，故该值已是下界。报告 η²={eta_sq} 偏小，请核对。"
                ),
            ))
        else:
            # ④ 高于下界 → 总存在某个 df1>1 使其成立，**无法判定**。
            #    这里明确说「未校验」而不是猜「一致」（放过错误）或报「矛盾」（误伤正确报告）。
            checks.append(StatCheck(
                metric="F ↔ η²（未校验）",
                value_reported=f"η² = {eta_sq}",
                value_expected=f"η² ≥ {eta_sq_expected:.4f}（df1=1 时的下界）",
                consistent=True,
                explanation=(
                    f"未提供组间自由度 df_between，无法确定 η² 的期望值 —— η² 随组数增大而增大，"
                    f"df1=1 时下界为 {eta_sq_expected:.4f}，而报告值高于它，说明可能是多组比较，"
                    f"但不能据此判定对错。请补充 df_between（单因素 ANOVA = 组数-1）后重新校验。"
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

    # ── 校验 4b: d ↔ r（点二列相关）──
    # 2026-09-16 新增：docstring 里早就承诺了「4. d ↔ r: r = d/√(d²+4)」，
    #   但**代码里从来没有实现过**（全文搜 r_value / pearson 均无校验逻辑）——
    #   属于「文档承诺未兑现」，用户按说明传了 r 却看不到任何校验项。
    #   实现上比教科书式更严一层：r = d/√(d²+A)，A = (n1+n2)(n1+n2-2)/(n1·n2)。
    #   等样本量的大样本极限 A→4，即回归到教科书式 r = d/√(d²+4)。
    if r is not None and d is not None:
        A = 4.0
        form = "等样本量近似式（A=4）"
        if n1 and n2 and float(n1) > 0 and float(n2) > 0:
            n_total = float(n1) + float(n2)
            A = n_total * (n_total - 2) / (float(n1) * float(n2))
            form = f"精确式 A={A:.3f}（n1={n1}, n2={n2}）"
        if A > 0:
            r_expected = abs(float(d)) / math.sqrt(float(d) ** 2 + A)
            ratio = abs(abs(float(r)) - r_expected) / max(r_expected, 0.001)
            consistent = ratio < 0.15
            checks.append(StatCheck(
                metric="d ↔ r",
                value_reported=f"r = {r}",
                value_expected=f"r = {r_expected:.3f} (from d={d}，{form})",
                consistent=consistent,
                explanation=(
                    f"根据 d={d} 按{form}换算 r 应为 {r_expected:.3f}（只比量级，符号取决于分组编码），"
                    f"论文报告 r={r}，{'一致' if consistent else f'偏差 {ratio:.0%}，超出 15% 容忍范围'}。"
                    f"⚠️ 适用前提：该换算要求 r 是**二分组与连续变量的点二列相关**；"
                    f"若 r 是两个连续变量的 Pearson 相关，或 d 来自配对样本（d_z），二者不能直接互推。"
                ),
            ))

    # ── 校验 5: p值 ↔ 统计量 ──
    # 🔴 2026-09-16 修正：原来只有两条粗启发式（|t|>3.29 应 p<.001 / |t|<1.0 应 p>.05），
    #    且**只在 df>30 时生效** —— 小样本（df≤30）完全不校验，而小样本恰恰是心理学/教育学
    #    论文里最常见、也最容易抄错 p 的场景。现改为精确计算：
    #      · t：双尾 p = I_{df/(df+t²)}(df/2, 1/2)
    #      · F：右尾 p = I_{df2/(df2+df1·F)}(df2/2, df1/2)
    #    实现在 `_betai`（纯 Python，与 scipy 相对误差 ~1e-15），不引入 scipy 依赖。
    #    判定纪律（宁可放过、不可误伤）见 `_p_verdict` 注释。
    if p is not None and t is not None and df is not None:
        expected_p = t_p_two_tailed(t, df)
        verdict = _p_verdict(expected_p, float(p))
        if verdict:
            checks.append(StatCheck(
                metric="p值 ↔ t统计量",
                value_reported=f"p = {p}",
                value_expected=f"p = {expected_p:.3g} (精确双尾, |t|={abs(float(t))}, df={df})",
                consistent=False,
                explanation=(
                    f"由 t={t}, df={df} 精确计算双尾 p = {expected_p:.3g}，论文报告 p={p}，"
                    f"报告值明显{'过大' if verdict == '过大' else '过小'}。"
                    + ("（若该 p 是「p < .001」这类上界写法、或**多重比较校正后**"
                       "（Bonferroni 等）的校正值，则属正常，请按校正后理解。）"
                       if verdict == "过大" else
                       "（若该 p 为单尾值，与双尾恰好差 2 倍，不会触发本判定。）")
                ),
            ))

    if p is not None and f is not None and df_between is not None and df_error is not None:
        expected_p = f_p_right_tail(f, df_between, df_error)
        verdict = _p_verdict(expected_p, float(p))
        if verdict:
            checks.append(StatCheck(
                metric="p值 ↔ F统计量",
                value_reported=f"p = {p}",
                value_expected=(
                    f"p = {expected_p:.3g} (精确右尾, F={f}, df1={df_between}, df2={df_error})"
                ),
                consistent=False,
                explanation=(
                    f"由 F={f}, df1={df_between}, df2={df_error} 精确计算右尾 p = {expected_p:.3g}，"
                    f"论文报告 p={p}，报告值明显{'过大' if verdict == '过大' else '过小'}。"
                    + ("（若该 p 是「p < .001」这类上界写法、或**多重比较校正后**"
                       "（Bonferroni 等）的校正值，则属正常，请按校正后理解。）"
                       if verdict == "过大" else
                       "（F 检验只有右尾，不存在单尾/双尾差异；若确为合法报告请核对 F 值与自由度。）")
                ),
            ))

    # ── 校验 7: p值 ↔ χ² ──
    if p is not None and chi2 is not None and df is not None:
        expected_p = chi2_p_right_tail(chi2, df)
        verdict = _p_verdict(expected_p, float(p))
        if verdict:
            checks.append(StatCheck(
                metric="p值 ↔ χ²统计量",
                value_reported=f"p = {p}",
                value_expected=f"p = {expected_p:.3g} (精确右尾, χ²={chi2}, df={df})",
                consistent=False,
                explanation=(
                    f"由 χ²={chi2}, df={df} 精确计算右尾 p = {expected_p:.3g}，"
                    f"论文报告 p={p}，报告值明显{'过大' if verdict == '过大' else '过小'}。"
                    "（卡方只有右尾，不存在单尾/双尾差异；若确为合法报告请核对 χ² 值与自由度，"
                    "并确认报的是「皮尔逊卡方」而非似然比或线性关联。）"
                ),
            ))

    # ── 校验 8: p值 ↔ r ──
    if p is not None and r is not None and n is not None:
        expected_p = r_p_two_tailed(r, n)
        verdict = _p_verdict(expected_p, float(p))
        if verdict:
            try:
                t_from_r = float(r) * math.sqrt((float(n) - 2.0) / (1.0 - float(r) ** 2))
            except (TypeError, ValueError, ZeroDivisionError):
                t_from_r = float("nan")
            checks.append(StatCheck(
                metric="p值 ↔ 相关系数 r",
                value_reported=f"p = {p}",
                value_expected=f"p = {expected_p:.3g} (精确双尾, r={r}, n={n})",
                consistent=False,
                explanation=(
                    f"由 r={r}, n={n} 换算 t={t_from_r:.3f}、df={float(n) - 2.0:g}，"
                    f"精确双尾 p = {expected_p:.3g}，论文报告 p={p}，"
                    f"报告值明显{'过大' if verdict == '过大' else '过小'}。"
                    "（⚠️ 该换算要求 r 是两连续变量的 Pearson 相关；若为 Spearman ρ 或"
                    "点二列相关，p 的换算式不同，请按实际检验类型理解。）"
                ),
            ))

    # ── 校验 9: p值 ↔ Z（非参数检验的正态近似）──
    z = stats.get("z_value")
    if p is not None and z is not None:
        expected_p = z_p_two_tailed(z)
        verdict = _p_verdict(expected_p, float(p))
        if verdict:
            checks.append(StatCheck(
                metric="p值 ↔ Z统计量",
                value_reported=f"p = {p}",
                value_expected=f"p = {expected_p:.3g} (精确双尾, |Z|={abs(float(z))})",
                consistent=False,
                explanation=(
                    f"由 Z={z} 按标准正态精确计算双尾 p = {expected_p:.3g}，"
                    f"论文报告 p={p}，报告值明显{'过大' if verdict == '过大' else '过小'}。"
                    "（⚠️ 该口径对应 SPSS 的「渐近显著性」；若论文报的是小样本的"
                    "「精确显著性」，那是精确分布而非正态近似，两者本就不同，请按实际口径理解。）"
                ),
            ))

    # ── 校验 10: R² ↔ R 与 调整后 R² ≤ R²（回归「模型摘要」）──
    # 🔴 这两条是**确定性代数关系**（不是统计推断），故用**严格数值容差**，
    #    不走 `_p_verdict` 那套"宁可放过不可误伤"的统计判据 ——
    #    R² 必须等于 R 的平方，抄错就是抄错，没有"口径不同"的余地。
    rsq = stats.get("r_squared")
    if r is not None and rsq is not None:
        try:
            expected_rsq = float(r) ** 2
            reported_rsq = float(rsq)
        except (TypeError, ValueError):
            expected_rsq = reported_rsq = None
        # 容差 0.002：SPSS 通常显示 3 位小数（R=.685 → R²=.469225 → 显示 .469），
        # 舍入误差 ≤ 0.0005；留 4 倍余量仍能抓住"抄成 .496"这类真实错误。
        if expected_rsq is not None and abs(reported_rsq - expected_rsq) > 0.002:
            checks.append(StatCheck(
                metric="R² ↔ 相关系数 R",
                value_reported=f"R² = {reported_rsq}",
                value_expected=f"R² = {expected_rsq:.4g}（即 R 的平方，R={r}）",
                consistent=False,
                explanation=(
                    f"R² 与 R 是确定性关系：R² 恒等于 R 的平方。由 R={r} 得 "
                    f"R²={expected_rsq:.4g}，表中 R²={reported_rsq}，二者不符 —— "
                    "通常是抄串行，或把「调整后 R²」误当成 R² 抄进了论文。"
                ),
            ))
    adj = stats.get("adj_r_squared")
    if rsq is not None and adj is not None:
        try:
            if float(adj) > float(rsq) + 1e-9:
                checks.append(StatCheck(
                    metric="调整后 R² ≤ R²",
                    value_reported=f"调整后 R² = {adj}",
                    value_expected=f"≤ R² = {rsq}",
                    consistent=False,
                    explanation=(
                        "调整后 R² 是对自变量个数的惩罚，**恒不大于** R²"
                        "（自变量数 ≥1 且样本量大于参数数时）。"
                        f"表中调整后 R²={adj} > R²={rsq}，二者应有其一抄错。"
                    ),
                ))
        except (TypeError, ValueError):
            pass

    # ── 校验 11: Exp(B) ↔ B（逻辑回归的优势比）──
    # Exp(B) = e^B 是**确定性代数关系**，故用严格容差（与校验 10 同理，
    # 不走 `_p_verdict` 那套统计判据）。用**相对**容差 1%：Exp(B) 量级跨度大
    # （B=3 时 Exp(B)≈20），固定绝对容差会在大值处失效。
    b_coef = stats.get("b_value")
    exp_b = stats.get("exp_b")
    if b_coef is not None and exp_b is not None:
        try:
            expected_eb = math.exp(float(b_coef))
            reported_eb = float(exp_b)
        except (TypeError, ValueError, OverflowError):
            expected_eb = reported_eb = None
        if (expected_eb is not None and reported_eb > 0
                and abs(reported_eb - expected_eb) > 0.01 * abs(expected_eb)):
            checks.append(StatCheck(
                metric="Exp(B) ↔ 系数 B",
                value_reported=f"Exp(B) = {reported_eb}",
                value_expected=f"Exp(B) = {expected_eb:.4g}（= e^B，B={b_coef}）",
                consistent=False,
                explanation=(
                    f"Exp(B) 与 B 是确定性关系：Exp(B) = e^B。由 B={b_coef} 得 "
                    f"Exp(B)={expected_eb:.4g}，表中 Exp(B)={reported_eb}，二者不符 —— "
                    "通常是抄串行，或把 95% 置信区间的边界误当成了 Exp(B)。"
                ),
            ))

    # ── 附带：效应量大小分类（只在已有矛盾时顺带给出，放在所有校验之后）──
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
        # 从文本中通用推断干预要素（不再硬编码教育/心理学关键词）
        # 策略：查找「包含/结合/整合 A 和 B」型列举
        import re as _re
        _element_patterns = [
            # 中文「结合A和B」型列举，要素限2-8字
            r'(?:包含|结合|整合|融合|采用)[了]?(?:了)?([\u4e00-\u9fff]{2,6}?)\s*(?:和|与|及)\s*([\u4e00-\u9fff]{2,6}?)(?:[，。；的]|两种|三种|等)',
            # 英文 "combined A and B"
            r'(?:combined|integrating|incorporating)\s+(\w+(?:\s+\w+)?)\s+and\s+(\w+(?:\s+\w+)?)',
        ]
        for pat in _element_patterns:
            matches = _re.findall(pat, paper_text[:5000])
            if matches:
                elements = []
                for m in matches[:2]:
                    if isinstance(m, tuple):
                        elements.extend([p.strip() for p in m])
                    else:
                        elements.append(m.strip())
                break
        # 退退：查找「A+B」型联合干预
        if not elements:
            combined = _re.findall(r'([\u4e00-\u9fff]{2,6})\s*\+\s*([\u4e00-\u9fff]{2,6})', paper_text[:5000])
            if combined:
                elements = [combined[0][0], combined[0][1]]

    n_groups = design_info.get("n_groups", 0)
    if not n_groups:
        # 从文本推断组数
        if "实验组" in paper_text and "对照组" in paper_text:
            n_groups = 2

    if len(elements) >= 2 and n_groups <= 2:
        # 检查是否设置了多水平对比组（通用关键词）
        has_multi_level = any(kw in paper_text for kw in
                             ["多水平", "对比组", "不同强度", "不同剂量", "不同条件",
                              "multi-level", "dose-response", "different intensity"])
        if not has_multi_level:
            flaws.append(DesignFlaw(
                severity="P0",
                category="多要素未分离",
                description=(
                    f"研究包含 {len(elements)} 个干预要素（{'/'.join(elements)}），"
                    f"但仅设置了 {n_groups} 组（实验组+对照组），无法分离各要素的独立贡献"
                ),
                evidence="未设置不同干预强度的对比组",
                suggestion=(
                    "建议设置多水平实验条件（不同干预强度/单要素组），"
                    "以精确分离各要素的贡献。如无法增加组数，应在局限性中明确说明"
                ),
            ))

    # ── 检测 2: 评估者偏差 ──
    assessor = design_info.get("fidelity_assessor", "")
    if not assessor:
        # 从文本中通用推断（不再硬编码教育场景）
        if "忠实度" in paper_text or "忠实性" in paper_text or "fidelity" in text_lower:
            if any(kw in paper_text for kw in ["自评", "自我评估", "实施者评估", "self-eval", "self-report"]):
                assessor = "self"
            elif any(kw in paper_text for kw in ["独立观察", "第三方评估", "independent observer", "third-party"]):
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
        # 从文本中通用推断样本来源（不再硬编码幼儿园/学校）
        if any(kw in paper_text for kw in ["一所", "单个", "一家", "某院", "single institution", "one hospital", "one school"]):
            sample_source = "单一机构"
        # 通用提取样本量：数字 + 量词 + 被试词
        size_match = re.search(r'(\d+)\s*(名|个|位|例|名患者|名受试者).*(?:幼儿|儿童|学生|被试|患者|受试者|subject|patient|participant|sample)', paper_text, re.IGNORECASE)
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
    claims: list[str] | None = None,
) -> str:
    """运行所有验证器，返回综合报告

    Args:
        papers: 文献列表（用于引用验证 + Tier3 论断-摘要支持校验）
        stats: 统计指标字典（用于统计一致性校验）
        paper_text: 论文全文（用于设计缺陷检测）
        design_info: 设计信息（用于设计缺陷检测）
        enable_online_citation: 是否启用在线引用验证
        claims: 显式论断列表（Tier3 深度验证用）；为 None 且有 paper_text 时自动抽取
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

    # ── Tier3 深度验证：论断是否被所引论文摘要支持 ──
    if papers:
        if claims is None and paper_text:
            try:
                from vermes_cli.scholarforge.scoring import extract_key_claims

                claims = await extract_key_claims(paper_text, max_claims=5)
            except Exception as e:
                logger.warning("deep_verify claim extraction failed: %s", e)
                claims = []
        if claims:
            try:
                from vermes_cli.scholarforge.deep_verify import (
                    deep_verify_claims,
                    format_deep_verify_report,
                )

                dv_results = await deep_verify_claims(claims, papers)
                report = format_deep_verify_report(dv_results)
                if report:
                    sections.append(report)
            except Exception as e:
                logger.warning("deep_verify failed: %s", e)

    if not sections:
        return "ℹ️ 未提供验证数据，跳过验证"

    return "\n\n---\n\n".join(sections)
