"""
真引用提供器 — DBLP + CrossRef API 获取真实 BibTeX
论文类型为期刊/会议/博士时自动替换伪引用 [n] 为真实文献
"""
import asyncio
import json
import logging
import re
import time
from typing import Optional
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from urllib.parse import quote

logger = logging.getLogger("scholarforge.citation")

# ── DBLP API ──
DBLP_SEARCH = "https://dblp.org/search/publ/api?format=json&h=10&q="

# ── CrossRef API ──
CROSSREF_SEARCH = "https://api.crossref.org/works?rows=10&query="

# ── Semantic Scholar API (free, no key needed) ──
SEMANTIC_SCHOLAR_SEARCH = "https://api.semanticscholar.org/graph/v1/paper/search?limit=10&query="
SEMANTIC_SCHOLAR_BIBTEX = "https://api.semanticscholar.org/graph/v1/paper/"


class RealCitation:
    """真实引用条目"""
    def __init__(self, title="", authors=None, year="", venue="", doi="", bibtex="", paper_id=""):
        self.title = title
        self.authors = authors or []
        self.year = year
        self.venue = venue
        self.doi = doi
        self.bibtex = bibtex
        self.paper_id = paper_id
        self.source = ""  # dblp | crossref | semantic_scholar

    def to_dict(self):
        return {
            "title": self.title, "authors": self.authors, "year": self.year,
            "venue": self.venue, "doi": self.doi, "bibtex": self.bibtex,
            "paper_id": self.paper_id, "source": self.source,
        }

    def format_bibtex(self, index: int = 1) -> str:
        """生成标准 BibTeX 条目 — cite_key 含标题首词防碰撞"""
        first_author = self.authors[0].split()[-1] if self.authors else "unknown"
        # 标题首词（忽略 a/an/the 及标点）
        title_words = [w.lower() for w in re.findall(r'[a-zA-Z]{3,}', self.title)] if self.title else []
        title_prefix = title_words[0][:8] if title_words else "paper"
        cite_key = f"{first_author.lower()}{self.year}{title_prefix}{chr(96+index)}"
        author_str = " and ".join(self.authors)
        return (
            f"@article{{{cite_key},\n"
            f"  title = {{{self.title}}},\n"
            f"  author = {{{author_str}}},\n"
            f"  year = {{{self.year}}},\n"
            f"  journal = {{{self.venue}}},\n"
            f"  doi = {{{self.doi}}}\n"
            f"}}"
        )


async def search_dblp(query: str, limit: int = 5) -> list[RealCitation]:
    """DBLP 搜索计算机科学文献"""
    results = []
    try:
        url = DBLP_SEARCH + quote(query)
        req = Request(url, headers={"User-Agent": "ScholarForge/1.0", "Accept": "application/json"})
        resp = await asyncio.to_thread(urlopen, req, timeout=10)
        data = json.loads(resp.read().decode("utf-8"))
        
        hits = data.get("result", {}).get("hits", {}).get("hit", [])
        for hit in hits[:limit]:
            info = hit.get("info", {})
            title = info.get("title", "")
            year = info.get("year", "")
            venue = info.get("venue", "")
            doi = info.get("doi", "")
            authors_list = info.get("authors", {}).get("author", [])
            if isinstance(authors_list, dict):
                authors_list = [authors_list]
            authors = [a.get("text", "") for a in authors_list if a.get("text")]
            
            paper_id = f"dblp:{info.get('key', '')}"
            if title:
                c = RealCitation(title=title, authors=authors, year=str(year) if year else "",
                                 venue=venue, doi=doi, paper_id=paper_id)
                c.source = "dblp"
                # 生成 BibTeX
                c.bibtex = c.format_bibtex(len(results) + 1)
                results.append(c)
    except (HTTPError, URLError, json.JSONDecodeError) as e:
        logger.warning(f"DBLP search failed for '{query}': {e}")
    except Exception as e:
        logger.error(f"DBLP unexpected error: {e}")
    return results


async def search_crossref(query: str, limit: int = 5) -> list[RealCitation]:
    """CrossRef 搜索跨学科文献（含完整 DOI/BibTeX）"""
    results = []
    try:
        url = CROSSREF_SEARCH + quote(query)
        req = Request(url, headers={"User-Agent": "ScholarForge/1.0", "Accept": "application/json"})
        resp = await asyncio.to_thread(urlopen, req, timeout=10)
        data = json.loads(resp.read().decode("utf-8"))
        
        items = data.get("message", {}).get("items", [])
        for item in items[:limit]:
            title_list = item.get("title", [])
            title = title_list[0] if title_list else ""
            year = item.get("published-print", {}).get("date-parts", [[None]])[0][0] or \
                   item.get("created", {}).get("date-parts", [[None]])[0][0] or ""
            doi = item.get("DOI", "")
            
            # Authors
            authors_list = item.get("author", [])
            authors = [f"{a.get('given', '')} {a.get('family', '')}".strip() for a in authors_list]
            
            # Venue
            container = item.get("container-title", [])
            venue = container[0] if container else item.get("publisher", "")
            
            # ISSN
            issn_list = item.get("ISSN", [])
            issn = issn_list[0] if issn_list else ""
            
            if title:
                c = RealCitation(title=title, authors=authors, year=str(year) if year else "",
                                 venue=venue, doi=doi, paper_id=f"doi:{doi}")
                c.source = "crossref"
                # BibTeX
                first_author = authors[0].split()[-1] if authors else "unknown"
                title_words = [w.lower() for w in re.findall(r'[a-zA-Z]{3,}', title)] if title else []
                title_prefix = title_words[0][:8] if title_words else "paper"
                cite_key = f"{first_author.lower()}{year}{title_prefix}{chr(96+len(results)+1)}"
                author_str = " and ".join(authors)
                c.bibtex = (
                    f"@article{{{cite_key},\n"
                    f"  title = {{{title}}},\n"
                    f"  author = {{{author_str}}},\n"
                    f"  year = {{{year}}},\n"
                    f"  journal = {{{venue}}},\n"
                    f"  doi = {{{doi}}}\n"
                    f"}}"
                )
                results.append(c)
    except (HTTPError, URLError, json.JSONDecodeError) as e:
        logger.warning(f"CrossRef search failed for '{query}': {e}")
    except Exception as e:
        logger.error(f"CrossRef unexpected error: {e}")
    return results


async def search_semantic_scholar(query: str, limit: int = 5) -> list[RealCitation]:
    """Semantic Scholar (免费，无需 Key)"""
    results = []
    try:
        url = SEMANTIC_SCHOLAR_SEARCH + quote(query)
        req = Request(url, headers={"User-Agent": "ScholarForge/1.0"})
        resp = await asyncio.to_thread(urlopen, req, timeout=10)
        data = json.loads(resp.read().decode("utf-8"))
        
        papers = data.get("data", [])
        for p in papers[:limit]:
            paper_id = p.get("paperId", "")
            title = p.get("title", "")
            year = p.get("year", "")
            venue = p.get("venue", "") or p.get("publicationVenue", {}).get("name", "")
            doi = p.get("externalIds", {}).get("DOI", "")
            authors = [a.get("name", "") for a in p.get("authors", [])]
            
            if title:
                c = RealCitation(title=title, authors=authors, year=str(year) if year else "",
                                 venue=venue, doi=doi, paper_id=paper_id)
                c.source = "semantic_scholar"
                c.bibtex = c.format_bibtex(len(results) + 1)
                results.append(c)
    except (HTTPError, URLError, json.JSONDecodeError) as e:
        logger.warning(f"Semantic Scholar search failed for '{query}': {e}")
    except Exception as e:
        logger.error(f"Semantic Scholar unexpected error: {e}")
    return results


async def fetch_real_citations(
    title: str,
    keywords: list[str],
    paper_type: str = "本科论文",
    limit: int = 10,
) -> list[RealCitation]:
    """主入口：根据论文标题/关键词拉取真实引用
    
    Args:
        title: 论文标题/主题
        keywords: 关键词列表
        paper_type: 论文学（期刊/会议优先用交叉 API）
        limit: 最多获取几篇
        
    Returns:
        RealCitation 列表（去重后）
    """
    all_citations: dict[str, RealCitation] = {}  # paper_id → citation (去重)
    
    # 根据论文类型确定搜索策略
    is_advanced = paper_type in ("博士论文", "硕士论文", "期刊论文", "会议论文")
    
    # 构建所有搜索查询
    queries = [title[:100]] + keywords[:4]
    
    for q in queries[:3]:  # 最多 3 个查询串
        if not q.strip():
            continue
        
        # 并行搜索多个源
        tasks = [search_semantic_scholar(q, limit=5)]
        if is_advanced:
            tasks.append(search_crossref(q, limit=5))
            # DBLP 仅对 CS 相关有用
            if any(k in q.lower() for k in ("learning", "network", "algorithm", "model", "system")):
                tasks.append(search_dblp(q, limit=5))
        
        batch_results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in batch_results:
            if isinstance(result, Exception):
                logger.warning(f"Citation search sub-task failed: {result}")
                continue
            for c in result:
                key = c.paper_id or c.title[:80]
                if key not in all_citations:
                    all_citations[key] = c
        
        if len(all_citations) >= limit:
            break
        
        # 避免限流
        await asyncio.sleep(0.3)
    
    citations = list(all_citations.values())[:limit]
    logger.info(f"Fetched {len(citations)} real citations for '{title[:40]}...' (type={paper_type})")
    return citations


async def replace_pseudo_citations(
    draft: str,
    topic: str,
    keywords: list[str],
    paper_type: str = "本科论文",
) -> tuple[str, list[RealCitation]]:
    """替换正文中的伪引用 [n] 为真实文献

    F-25 修复：改用公共匹配管线 citation_matcher.match_citations()，
    统一保证：0.3 阈值 + LLM 精排 + 去重 + 连续编号。
    旧实现仅启发式 score_relevance、无阈值、无 LLM 精排、无去重、跳号。

    支持三种占位符格式: [n] / [n-m] / [n,m,...]

    Returns:
        (替换后的正文, 新获取的真实引用列表)
    """
    from vermes_cli.scholarforge.citation_matcher import (
        match_citations, replace_citations_in_text, build_references_section,
        expand_citation as _expand,
    )

    # ── 1. 解析占位符 ──
    cite_pattern = re.compile(r'\[(\d+(?:\s*[-–,]\s*\d+)*)\]')
    raw_matches = list(cite_pattern.finditer(draft))
    if not raw_matches:
        return draft, []

    def expand_citation(raw: str) -> list[int]:
        raw = raw.strip('[]')
        nums = []
        for part in re.split(r'[,，]', raw):
            part = part.strip()
            range_m = re.match(r'(\d+)\s*[-–]\s*(\d+)', part)
            if range_m:
                a, b = int(range_m.group(1)), int(range_m.group(2))
                nums.extend(range(min(a, b), max(a, b) + 1))
            elif part.isdigit():
                nums.append(int(part))
        return nums

    all_nums: set[int] = set()
    for m in raw_matches:
        all_nums.update(expand_citation(m.group(0)))
    unique_nums = sorted(all_nums)
    max_ref = max(unique_nums) if unique_nums else 0

    # ── 2. 拉取真实文献 ──
    citations = await fetch_real_citations(topic, keywords, paper_type, limit=max(20, max_ref))
    if not citations:
        logger.info("No real citations found, keeping pseudo citations")
        return draft, []

    # ── 3. 提取每个编号的上下文 + 关键词 ──
    num_context: dict[int, str] = {}
    num_keywords: dict[int, str] = {}
    paragraphs = draft.split("\n")
    for para in paragraphs:
        for m in cite_pattern.finditer(para):
            nums = expand_citation(m.group(0))
            for n in nums:
                if n in unique_nums and n not in num_context:
                    start = max(0, m.start() - 120)
                    end = min(len(para), m.end() + 120)
                    ctx = para[start:end].strip()
                    num_context[n] = ctx
                    num_keywords[n] = _extract_keywords_local(ctx, topic)

    # ── 4. 公共匹配管线 ──
    candidates = {n: citations for n in unique_nums}  # 全池共享（match_citations 内部去重）
    result = await match_citations(
        unique_nums=unique_nums,
        candidates=candidates,
        num_context=num_context,
        num_keywords=num_keywords,
        local_papers=None,
    )

    # ── 5. 正文替换 ──
    new_draft = replace_citations_in_text(draft, result.num_to_ref)

    # ── 6. 参考文献列表 ──
    if result.ref_list:
        refs_text = build_references_section(result.ref_list)
        # 检测并替换已有参考文献节
        if re.search(r'(?i)##\s*参考文献', new_draft):
            ref_section_pattern = r'(\n\n---\n)?## 参考文献\n\n[\s\S]*$'
            new_draft = re.sub(ref_section_pattern, '\n\n---\n' + refs_text, new_draft)
        else:
            new_draft = new_draft + "\n\n---\n" + refs_text

    # 日志
    for line in result.match_log:
        logger.info(line)

    return new_draft, citations


def _extract_keywords_local(text: str, fallback: str = "") -> str:
    """从上下文提取搜索关键词（本地版，保留原 extract_keywords 逻辑）。"""
    proper_nouns = re.findall(r'(?<![A-Za-z0-9])[A-Z][A-Za-z0-9]{2,}(?![A-Za-z0-9])', text)
    stop_proper = {'The', 'This', 'That', 'These', 'Those', 'Such', 'However',
                   'Moreover', 'Furthermore', 'Therefore', 'Also', 'While',
                   'When', 'Where', 'What', 'Which', 'Based', 'Using',
                   'Given', 'Since', 'From', 'With', 'Both', 'Each',
                   'First', 'Second', 'Third', 'Finally', 'In', 'For',
                   'And', 'But', 'Not', 'Are', 'Was', 'Were', 'Has',
                   'Have', 'Can', 'May', 'Will', 'Been', 'Some', 'More',
                   'Most', 'Other', 'All', 'One', 'Two', 'Three'}
    proper_nouns = [w for w in proper_nouns if w not in stop_proper]

    stop_en = {'the', 'and', 'for', 'are', 'but', 'not', 'this', 'that', 'with',
               'from', 'have', 'has', 'was', 'were', 'will', 'can', 'may',
               'also', 'such', 'than', 'then', 'these', 'those', 'which',
               'their', 'there', 'what', 'when', 'where', 'who', 'whom',
               'been', 'being', 'into', 'about', 'after', 'before',
               'between', 'through', 'during', 'above', 'below', 'over',
               'under', 'again', 'more', 'most', 'other', 'some'}
    en_words = re.findall(r'[A-Za-z]{3,30}', text)
    en_words = [w for w in en_words if w.lower() not in stop_en]

    cn_words = re.findall(r'[\u4e00-\u9fa5]{2,4}', text)
    stop_cn = {'的研究', '本文', '本研', '研究', '方法', '结果', '结论',
               '实验', '分析', '通过', '基于', '采用', '提出', '实现',
               '一个', '可以', '这个', '那个', '因此', '所以', '然而',
               '此外', '同时', '另外', '首先', '其次', '最后'}
    cn_words = [w for w in cn_words if w not in stop_cn]

    seen = set()
    all_words = []
    for w in proper_nouns + en_words[:5] + cn_words[:3]:
        wl = w.lower()
        if wl not in seen:
            seen.add(wl)
            all_words.append(w)
    if not all_words:
        return fallback[:60].strip()
    return ' '.join(all_words[:6])



def format_authors_gbt(authors: list[str]) -> str:
    """GB/T 7714: 作者1, 作者2, 作者3, 等"""
    if not authors:
        return "[Anon]"
    names = []
    for a in authors[:3]:
        parts = a.strip().split()
        if len(parts) >= 2:
            names.append(f"{parts[-1]} {parts[0][0] if parts[0] else ''}{''.join(p[0] for p in parts[1:-1])}")
        else:
            names.append(a)
    if len(authors) > 3:
        names.append("等")
    return ", ".join(names)


def format_authors_apa(authors: list[str]) -> str:
    """APA 7th: Last, F. M., & Last, F. M."""
    if not authors:
        return "Anonymous"
    formatted = []
    for a in authors[:20]:
        parts = a.strip().split()
        if len(parts) >= 2:
            last = parts[-1]
            initials = ". ".join(p[0] for p in parts[:-1] if p)
            formatted.append(f"{last}, {initials}." if initials else f"{last}.")
        else:
            formatted.append(a)
    if len(formatted) == 1:
        return formatted[0]
    if len(formatted) == 2:
        return f"{formatted[0]}, & {formatted[1]}"
    return ", ".join(formatted[:-1]) + f", & {formatted[-1]}"


def format_authors_mla(authors: list[str]) -> str:
    """MLA 9th: Last, First M., et al."""
    if not authors:
        return "Anonymous"
    parts = authors[0].strip().split()
    if len(parts) >= 2:
        first = " ".join(parts[:-1])
        last = parts[-1]
        first_author = f"{last}, {first}"
    else:
        first_author = authors[0]
    if len(authors) > 3:
        return f"{first_author}, et al."
    others = []
    for a in authors[1:3]:
        p = a.strip().split()
        others.append(f"{p[0]} {p[-1]}" if len(p) >= 2 else a)
    if others:
        return f"{first_author}, " + ", ".join(others) + "."
    return f"{first_author}."


def format_authors_ieee(authors: list[str]) -> str:
    """IEEE: F. M. Last, F. M. Last, and F. M. Last"""
    if not authors:
        return "Anonymous"
    formatted = []
    for a in authors[:6]:
        parts = a.strip().split()
        if len(parts) >= 2:
            last = parts[-1]
            initials = " ".join(f"{p[0]}." for p in parts[:-1] if p)
            formatted.append(f"{initials} {last}" if initials else last)
        else:
            formatted.append(a)
    if len(authors) > 6:
        formatted.append("et al.")
    if len(formatted) <= 2:
        return " and ".join(formatted)
    return ", ".join(formatted[:-1]) + f", and {formatted[-1]}"


def format_authors_chicago(authors: list[str]) -> str:
    """Chicago 17th: Last, First M., First M. Last, and First M. Last"""
    if not authors:
        return "Anonymous"
    formatted = []
    for a in authors[:10]:
        parts = a.strip().split()
        if len(parts) >= 2:
            first = " ".join(parts[:-1])
            last = parts[-1]
            formatted.append(f"{last}, {first}")
        else:
            formatted.append(a)
    if len(authors) > 10:
        formatted.append("et al.")
    if len(formatted) <= 3:
        return ", ".join(formatted)
    return ", ".join(formatted[:-1]) + f", and {formatted[-1]}"


def format_authors_vancouver(authors: list[str]) -> str:
    """Vancouver: Last FM, Last FM, Last FM, et al."""
    if not authors:
        return "Anonymous"
    formatted = []
    for a in authors[:6]:
        parts = a.strip().split()
        if len(parts) >= 2:
            last = parts[-1]
            initials = "".join(p[0] for p in parts[:-1] if p)
            formatted.append(f"{last} {initials}")
        else:
            formatted.append(a)
    if len(authors) > 6:
        formatted.append("et al")
    return ", ".join(formatted)


def format_citation(paper, style: str = "gbt7714", index: int = 0) -> str:
    """按指定格式格式化单条参考文献
    
    Args:
        paper: PaperCard/RealCitation/dict — 需有 title/authors/year/venue/doi
        style: gbt7714 | apa | mla | ieee | chicago | vancouver
        index: IEEE/Vancouver 用编号 [1]
    Returns:
        格式化的参考文献字符串
    """
    # 统一取值
    if isinstance(paper, dict):
        title = paper.get("title", "")
        authors = paper.get("authors", []) or []
        year = paper.get("year", "")
        venue = paper.get("venue", "")
        doi = paper.get("doi", "")
    else:
        title = getattr(paper, "title", "")
        authors = getattr(paper, "authors", []) or []
        year = getattr(paper, "year", "")
        venue = getattr(paper, "venue", "")
        doi = getattr(paper, "doi", "")

    year_str = str(year) if year else "n.d."
    doi_url = f" https://doi.org/{doi}" if doi else ""

    if style == "gbt7714":
        # GB/T 7714-2015: 作者. 标题[J]. 期刊, 年.
        author_str = format_authors_gbt(authors)
        return f"{author_str}. {title}[J]. {venue}, {year_str}.{doi_url}"

    elif style == "apa":
        # APA 7th: Author, A. A., & Author, B. B. (Year). Title. Journal.
        author_str = format_authors_apa(authors)
        return f"{author_str} ({year_str}). {title}. {venue}.{doi_url}"

    elif style == "mla":
        # MLA 9th: Last, First M. "Title." Journal, Year.
        author_str = format_authors_mla(authors)
        return f"{author_str} \"{title}.\" {venue}, {year_str}."

    elif style == "ieee":
        # IEEE: [1] F. M. Last, \"Title,\" Journal, Year.
        prefix = f"[{index}] " if index else ""
        author_str = format_authors_ieee(authors)
        return f"{prefix}{author_str}, \"{title},\" {venue}, {year_str}.{doi_url}"

    elif style == "chicago":
        # Chicago 17th: Last, First M. \"Title.\" Journal (Year).
        author_str = format_authors_chicago(authors)
        return f"{author_str}. \"{title}.\" {venue} ({year_str}).{doi_url}"

    elif style == "vancouver":
        # Vancouver: Last FM. Title. Journal. Year.
        prefix = f"{index}. " if index else ""
        author_str = format_authors_vancouver(authors)
        return f"{prefix}{author_str}. {title}. {venue}. {year_str}.{doi_url}"

    else:
        # 未知格式回退到 GB/T 7714
        author_str = format_authors_gbt(authors)
        return f"{author_str}. {title}[J]. {venue}, {year_str}.{doi_url}"


def format_references_list(papers: list, style: str = "gbt7714") -> str:
    """格式化完整参考文献列表"""
    lines = []
    for i, p in enumerate(papers, 1):
        lines.append(format_citation(p, style, index=i))
    return "\n".join(lines)


# 简易测试
async def _test():
    results = await fetch_real_citations(
        title="Large Language Models for Code Review",
        keywords=["code review", "LLM", "automated"],
        paper_type="会议论文",
    )
    for r in results:
        print(f"[{r.source}] {r.title[:60]}... ({r.year}) - {r.venue}")
        print(f"  BibTeX: {r.bibtex[:80]}...")
        print()


if __name__ == "__main__":
    asyncio.run(_test())
