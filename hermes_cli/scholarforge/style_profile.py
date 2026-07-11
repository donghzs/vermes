"""
ScholarForge 风格学习模块 — 从用户示例论文学出个人写作指纹

核心思想：
- 不是"降AI率"（事后救火），而是"仿写你的声音"（事前预防）
- 从用户上传的 2-5 篇示例论文提取风格特征，生成可注入 prompt 的风格指南
- 写新章节时自动匹配用户的词汇/句式/过渡/引用习惯

对标头部框架的"仿写"能力，而非"降重"能力。
"""
from __future__ import annotations

import re
import json
import logging
from dataclasses import dataclass, field, asdict
from collections import Counter
from typing import Optional

logger = logging.getLogger("scholarforge.style_profile")


@dataclass
class StyleProfile:
    """用户写作风格指纹"""
    # 词汇特征
    domain_terms: list[str] = field(default_factory=list)   # 高频领域术语（Top 20）
    avg_word_len: float = 0.0                                # 平均词长（中文字/英文词）
    jargon_density: float = 0.0                              # 术语密度（术语/总词数）

    # 句式特征
    avg_sentence_len: float = 0.0                           # 平均句长（字）
    sentence_cv: float = 0.0                                # 句长变异系数（节奏感）
    clause_preference: str = "mixed"                        # 长句/短句/混合

    # 段落特征
    avg_paragraph_len: float = 0.0                          # 平均段长（字）
    topic_sentence_first: float = 0.0                       # 主题句在段首比例

    # 过渡短语（从用户文本学，非硬编码）
    transition_phrases: list[str] = field(default_factory=list)

    # 引用习惯
    citation_format: str = "numbered"                       # numbered([n]) / author_year((张, 2022))
    citation_density: float = 0.0                           # 每百字引用数

    # 结构偏好
    section_order: list[str] = field(default_factory=list)  # 典型章节顺序
    heading_depth: int = 2                                  # 标题层级深度

    # 元信息
    sample_count: int = 0                                   # 学习用的样本数
    learned_from: list[str] = field(default_factory=list)   # 样本来源描述

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)

    @classmethod
    def from_json(cls, data: str) -> "StyleProfile":
        return cls(**json.loads(data))


def _split_sentences(text: str) -> list[str]:
    """按中英文标点切句"""
    parts = re.split(r'[。！？.!?\n]', text)
    return [p.strip() for p in parts if len(p.strip()) > 3]


def _split_paragraphs(text: str) -> list[str]:
    """按空行/标题分段"""
    paras = re.split(r'\n\s*\n|(?=^#{1,6}\s)', text, flags=re.MULTILINE)
    return [p.strip() for p in paras if len(p.strip()) > 10]


def _extract_domain_terms(text: str, top_n: int = 20) -> tuple[list[str], float]:
    """
    提取高频领域术语。
    策略：从去停用词后的词汇中，取 TF 最高的 n 个。
    中文按 2-4 字词组，英文按单词。
    """
    # 停用词（常见虚词/功能词）
    stopwords = set('的 了 和 与 及 在 是 有 为 以 对 等 也 都 而 或 其 之 于 把 被 让 使 由 从 这 那 该 此 我们 本研究 本文 这个 那个 一种 一个 一些 可以 通过 由于 因此 但是 然而 此外 其次 最后 首先 这种 这些 那些'.split())

    # 中文 2-4 字词组（按标点/空格分词后找连续中文字）
    cn_tokens = re.findall(r'[一-鿿]{2,4}', text)
    en_tokens = re.findall(r'[a-zA-Z][a-zA-Z\-]{2,}', text)

    all_tokens = cn_tokens + [t.lower() for t in en_tokens]
    # 过滤停用词、单字重复、以及只出现1次的碎片（避免"游戏对幼"类噪声）
    filtered = [t for t in all_tokens if t not in stopwords and len(t) >= 2]
    counter_raw = Counter(filtered)
    filtered = [t for t, c in counter_raw.items() if c >= 2]  # 至少出现2次才算术语

    if not filtered:
        return [], 0.0

    counter = Counter(filtered)
    top_terms = [w for w, _ in counter.most_common(top_n)]
    # 术语密度 = 术语出现次数 / 总词数
    jargon_density = sum(counter.values()) / max(len(all_tokens), 1)

    return top_terms, round(jargon_density, 3)


def _detect_citation_format(text: str) -> tuple[str, float]:
    """检测引用格式与密度"""
    numbered = len(re.findall(r'\[\d+\]', text))
    author_year = len(re.findall(r'[（(][^（）()]{1,30}?\d{4}[）)]', text))
    words = len(re.findall(r'[一-鿿]', text))

    if numbered >= author_year and numbered > 0:
        fmt = "numbered"
        density = numbered / max(words / 100, 1)
    elif author_year > 0:
        fmt = "author_year"
        density = author_year / max(words / 100, 1)
    else:
        fmt = "none"
        density = 0.0

    return fmt, round(density, 2)


def _extract_transitions(text: str, top_n: int = 10) -> list[str]:
    """
    从用户文本学过渡短语（非硬编码）。
    策略：找段首/句首的 2-6 字连接性短语，统计高频的。
    """
    # 常见过渡词种子（用于定位，但只保留用户实际用的）
    seeds = ['因此', '然而', '此外', '同时', '另外', '首先', '其次', '最后',
             '总之', '综上', '换言之', '具体来说', '值得注意的是', '需要强调',
             '在此基础上', '与此同时', '从...来看', '就...而言', '一方面', '另一方面',
             '由此可见', '基于', '通过', '由于', '根据', '相较于', '不同于']

    found = []
    for seed in seeds:
        # 仅当种子词本身出现在文本中时才采纳（不扩字，避免噪声）
        if seed in text:
            found.append(seed)

    if not found:
        # 兜底：取段首前4字
        for para in _split_paragraphs(text)[:20]:
            clean = re.sub(r'^#{1,6}\s*', '', para).strip()
            if len(clean) >= 4:
                found.append(clean[:4])

    counter = Counter(found)
    # 过滤带标点的碎片（如"。此外，"→"此外"）
    cleaned = []
    for phrase, cnt in counter.most_common(top_n * 2):
        clean = phrase.strip('，。、；：:. ')  # 去除首尾标点
        if 2 <= len(clean) <= 6 and clean not in cleaned:
            cleaned.append(clean)
        if len(cleaned) >= top_n:
            break
    return cleaned


def _detect_section_order(text: str) -> list[str]:
    """从标题提取章节顺序"""
    headings = re.findall(r'^#{1,6}\s*(.+)$', text, flags=re.MULTILINE)
    # 清理标题中的编号，跳过 H1（文档标题通常是论文名而非章节）
    cleaned = [re.sub(r'^[\d.、，。\s]+', '', h).strip() for h in headings]
    # 只保留 H2+ 作为章节顺序（H1 通常是论文标题）
    h_levels = re.findall(r'^(#+)\s*.+$', text, flags=re.MULTILINE)
    section_pairs = list(zip(h_levels, cleaned))
    filtered_pairs = [(lvl, h) for lvl, h in section_pairs if len(lvl) >= 2 and h]
    # 去重保序
    seen = set()
    order = []
    for _, h in filtered_pairs:
        if h and h not in seen:
            seen.add(h)
            order.append(h[:10])
    return order[:10]


def extract_style(text: str, source_desc: str = "") -> StyleProfile:
    """
    从单篇示例文本提取风格特征。

    Args:
        text: 示例论文/草稿全文
        source_desc: 样本来源描述（用于记录）

    Returns:
        StyleProfile: 风格指纹
    """
    profile = StyleProfile()
    profile.learned_from = [source_desc] if source_desc else []

    # 分句/分段
    sentences = _split_sentences(text)
    paragraphs = _split_paragraphs(text)

    # 词汇
    domain_terms, jargon_density = _extract_domain_terms(text)
    profile.domain_terms = domain_terms
    profile.jargon_density = jargon_density

    # 词长估算（中英混合）
    cn_chars = len(re.findall(r'[一-鿿]', text))
    en_words = len(re.findall(r'[a-zA-Z]+', text))
    total_tokens = cn_chars + en_words
    profile.avg_word_len = round(total_tokens / max(len(re.findall(r'[一-鿿a-zA-Z]+', text)), 1), 2)

    # 句式
    if sentences:
        sent_lens = [len(s) for s in sentences]
        mean_len = sum(sent_lens) / len(sent_lens)
        var_len = sum((l - mean_len) ** 2 for l in sent_lens) / len(sent_lens)
        profile.avg_sentence_len = round(mean_len, 1)
        profile.sentence_cv = round((var_len ** 0.5) / mean_len, 2) if mean_len > 0 else 0.0
        if mean_len > 35:
            profile.clause_preference = "long"
        elif mean_len < 18:
            profile.clause_preference = "short"
        else:
            profile.clause_preference = "mixed"

    # 段落
    if paragraphs:
        para_lens = [len(p) for p in paragraphs]
        profile.avg_paragraph_len = round(sum(para_lens) / len(para_lens), 1)
        # 主题句在段首（段首句长 > 段均长）
        topic_first = 0
        for p in paragraphs:
            p_sentences = _split_sentences(p)
            if len(p_sentences) >= 2:
                first_len = len(p_sentences[0])
                avg_len = sum(len(s) for s in p_sentences) / len(p_sentences)
                if first_len >= avg_len * 0.8:
                    topic_first += 1
        profile.topic_sentence_first = round(topic_first / len(paragraphs), 2)

    # 过渡短语
    profile.transition_phrases = _extract_transitions(text)

    # 引用
    fmt, density = _detect_citation_format(text)
    profile.citation_format = fmt
    profile.citation_density = density

    # 结构
    profile.section_order = _detect_section_order(text)
    headings = re.findall(r'^(#+)\s', text, flags=re.MULTILINE)
    if headings:
        profile.heading_depth = max(len(h) for h in headings)

    profile.sample_count = 1
    return profile


def merge_profiles(profiles: list[StyleProfile]) -> StyleProfile:
    """
    合并多篇示例的风格（取均值/高频）。
    用于用户上传多篇论文时聚合。
    """
    if not profiles:
        return StyleProfile()
    if len(profiles) == 1:
        return profiles[0]

    merged = StyleProfile()
    n = len(profiles)

    # 数值取均值
    merged.avg_word_len = round(sum(p.avg_word_len for p in profiles) / n, 2)
    merged.jargon_density = round(sum(p.jargon_density for p in profiles) / n, 3)
    merged.avg_sentence_len = round(sum(p.avg_sentence_len for p in profiles) / n, 1)
    merged.sentence_cv = round(sum(p.sentence_cv for p in profiles) / n, 2)
    merged.avg_paragraph_len = round(sum(p.avg_paragraph_len for p in profiles) / n, 1)
    merged.topic_sentence_first = round(sum(p.topic_sentence_first for p in profiles) / n, 2)
    merged.citation_density = round(sum(p.citation_density for p in profiles) / n, 2)
    merged.heading_depth = max(p.heading_depth for p in profiles)

    # 分类取众数
    from collections import Counter
    fmt_counter = Counter(p.citation_format for p in profiles)
    merged.citation_format = fmt_counter.most_common(1)[0][0]
    clause_counter = Counter(p.clause_preference for p in profiles)
    merged.clause_preference = clause_counter.most_common(1)[0][0]

    # 词汇：合并高频术语（去重，按出现频次）
    term_counter: Counter = Counter()
    for p in profiles:
        term_counter.update(p.domain_terms)
    merged.domain_terms = [t for t, _ in term_counter.most_common(20)]

    # 过渡短语：合并高频
    trans_counter: Counter = Counter()
    for p in profiles:
        trans_counter.update(p.transition_phrases)
    merged.transition_phrases = [t for t, _ in trans_counter.most_common(10)]

    # 结构：取最长常见的章节顺序
    merged.section_order = max((p.section_order for p in profiles), key=len, default=[])

    merged.sample_count = n
    merged.learned_from = [s for p in profiles for s in p.learned_from]
    return merged


def generate_style_prompt(profile: StyleProfile) -> str:
    """
    将风格指纹转为可注入 write prompt 的风格指南。

    设计：具体、可操作、少抽象。
    用用户的真实过渡短语和术语，而非泛泛的"学术风格"。
    """
    if profile.sample_count == 0:
        return ""

    lines = ["【用户写作风格参考】（从你的示例论文学习，请尽量匹配）"]

    # 词汇
    if profile.domain_terms:
        lines.append(f"  • 常用术语: {', '.join(profile.domain_terms[:10])}")
    if profile.jargon_density > 0:
        level = "高" if profile.jargon_density > 0.15 else ("中" if profile.jargon_density > 0.08 else "低")
        lines.append(f"  • 术语密度: {level}（每句约使用领域专有词汇）")

    # 句式
    if profile.avg_sentence_len > 0:
        lines.append(f"  • 句长: 平均{profile.avg_sentence_len:.0f}字，节奏{'多样' if profile.sentence_cv > 0.4 else '均匀'}")
    if profile.clause_preference != "mixed":
        lines.append(f"  • 偏好{'长句（复合结构）' if profile.clause_preference == 'long' else '短句（简洁明快）'}")

    # 段落
    if profile.avg_paragraph_len > 0:
        lines.append(f"  • 段落: 平均{profile.avg_paragraph_len:.0f}字，{'主题句在段首' if profile.topic_sentence_first > 0.6 else '自由展开'}")

    # 过渡
    if profile.transition_phrases:
        lines.append(f"  • 习惯过渡词: {', '.join(profile.transition_phrases[:6])}")

    # 引用
    if profile.citation_format == "numbered":
        lines.append(f"  • 引用格式: 编号式 [n]（密度约{profile.citation_density:.1f}/百字）")
    elif profile.citation_format == "author_year":
        lines.append(f"  • 引用格式: 作者-年份式 (作者, 年份)")
    else:
        lines.append(f"  • 引用格式: 自由（建议保持一致性）")

    # 结构
    if profile.section_order:
        lines.append(f"  • 典型章节顺序: {' → '.join(profile.section_order[:6])}")

    lines.append("\n请在写作时刻意匹配以上风格，使其读起来像用户本人的学术表达。")
    return "\n".join(lines)
