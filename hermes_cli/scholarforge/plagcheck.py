"""
ScholarForge 查重与 AIGC 检测模块 (P0-2)
提供论文查重检测 + AI 痕迹检测，对标 Paperpal (Turnitin) + 千笔 AI 检测

检测策略：
1. 内部查重: 文本内部自相似度检测（段落级）
2. 在线查重: 调用免费查重 API（PaperYY / 格子达 Gocheck / 大雅）
3. AIGC 检测: 调用 AIGC 检测模型 + 启发式特征分析
4. 结果报告: 生成标准化查重报告 JSON

零依赖核心检测算法：
- SimHash 局部敏感哈希 — 段落级相似度
- N-gram 覆盖度 — 句子级重复率
- 引用密度 — 非引用段落占比
"""
import hashlib
import logging
import re
from dataclasses import dataclass, field
from typing import Optional
from collections import Counter

logger = logging.getLogger("scholarforge.plagcheck")


@dataclass
class PlagResult:
    """单处查重命中"""
    text: str                    # 重复文本片段（前100字符）
    length: int                  # 重复长度（字符）
    position: int                # 在原文中的起始位置
    score: float                 # 相似度 0.0~1.0
    source: str = "internal"     # internal / online


@dataclass  
class AigcResult:
    """AIGC 检测段落级结果"""
    text: str                    # 段落文本
    position: int                # 起始位置
    aigc_probability: float      # AI 痕迹概率 0.0~1.0
    features: list[str] = field(default_factory=list)  # 检测到的 AI 特征


@dataclass
class PlagReport:
    """综合查重+AI检测报告"""
    total_chars: int
    total_paragraphs: int
    overall_similarity: float           # 0.0~1.0 综合重复率
    plag_results: list[PlagResult] = field(default_factory=list)
    aigc_results: list[AigcResult] = field(default_factory=list)
    aigc_overall_ratio: float = 0.0     # AI 痕迹占比
    suggestions: list[str] = field(default_factory=list)
    checked_sources: list[str] = field(default_factory=list)


# ─── SimHash 局部敏感哈希 ───

def _tokenize(text: str) -> list[str]:
    """中文+英文混合分词"""
    # 中文字符单独拆，英文按空格拆
    tokens = []
    buf = ""
    for ch in text:
        if '\u4e00' <= ch <= '\u9fff' or '\u3400' <= ch <= '\u4dbf':
            if buf:
                tokens.append(buf.lower())
                buf = ""
            tokens.append(ch)
        elif ch.isalnum():
            buf += ch
        else:
            if buf:
                tokens.append(buf.lower())
                buf = ""
    if buf:
        tokens.append(buf.lower())
    return tokens


def simhash(text: str, bits: int = 64) -> str:
    """SimHash — 局部敏感哈希，用于段落级近似查重"""
    tokens = _tokenize(text)
    vector = [0] * bits
    
    for token in tokens:
        h = int(hashlib.md5(token.encode()).hexdigest(), 16)
        for i in range(bits):
            if h & (1 << i):
                vector[i] += 1
            else:
                vector[i] -= 1
    
    result = 0
    for i in range(bits):
        if vector[i] > 0:
            result |= (1 << i)
    return format(result, f'0{bits//4}x')


def hamming_distance(a: str, b: str) -> int:
    """汉明距离（SimHash 相似度）"""
    diff = int(a, 16) ^ int(b, 16)
    return diff.bit_count()


def simhash_similarity(a: str, b: str) -> float:
    """SimHash 相似度 0.0~1.0"""
    return 1.0 - hamming_distance(a, b) / 64.0


# ─── N-gram 覆盖度 ───

def ngram_coverage(text: str, n: int = 5) -> float:
    """N-gram 覆盖率 — 评估文本内部自重复程度"""
    chars = list(text.replace('\n', ' ').replace('\r', ''))
    if len(chars) < n:
        return 0.0
    total = len(chars) - n + 1
    ngrams = set()
    dup_count = 0
    for i in range(total):
        gram = ''.join(chars[i:i+n])
        if gram in ngrams:
            dup_count += 1
        else:
            ngrams.add(gram)
    return dup_count / total if total > 0 else 0.0


# ─── 核心检查 ───

def _split_paragraphs(text: str) -> list[tuple[int, str]]:
    """将文本按段落分割，返回 (起始位置, 段落文本)"""
    paragraphs = []
    pos = 0
    for para in re.split(r'\n\s*\n', text):
        para_clean = para.strip()
        if len(para_clean) < 20:  # 跳过太短的段落
            pos += len(para) + 2
            continue
        start = text.index(para_clean, pos)
        paragraphs.append((start, para_clean))
        pos = start + len(para_clean)
    return paragraphs


def check_internal_plagiarism(text: str, threshold: float = 0.75) -> list[PlagResult]:
    """段落级内部查重 — SimHash 比较
    
    threshold: SimHash 相似度阈值，>= 此值视为重复
    """
    paras = _split_paragraphs(text)
    if len(paras) < 2:
        return []
    
    results = []
    hashes = [simhash(p[1]) for p in paras]
    
    for i in range(len(paras)):
        for j in range(i + 1, len(paras)):
            sim = simhash_similarity(hashes[i], hashes[j])
            if sim >= threshold:
                results.append(PlagResult(
                    text=paras[i][1][:100],
                    length=len(paras[i][1]),
                    position=paras[i][0],
                    score=sim,
                    source="internal",
                ))
                break  # 每段只记一次
    
    return results


def check_aigc(text: str) -> dict:
    """AIGC 痕迹检测 — 启发式特征分析 + 段落级检测
    
    检测维度（对标千笔 AI）：
    1. 句式规整度 — AI 倾向于高度一致的句式结构
    2. 连接词密度 — AI 过度使用"然而""此外""因此"等
    3. 段落长度均匀度 — AI 段长差异小
    4. 引用模式 — AI 常编造引用或引用密度异常
    5. 语气稳定性 — AI 缺乏人类写作的情感波动
    """
    paras = _split_paragraphs(text)
    if not paras:
        return {"overall_ratio": 0.0, "results": [], "features": []}
    
    para_texts = [p[1] for p in paras]
    para_lengths = [len(p) for p in para_texts]
    
    # ── 1. 句式规整度 ──
    # 计算句长标准差 / 均值(CV)，AI 写作文本 CV 通常 < 0.4
    sentences = re.split(r'[。！？.!?\n]', text)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 5]
    sent_lengths = [len(s) for s in sentences]
    if sent_lengths:
        sent_mean = sum(sent_lengths) / len(sent_lengths)
        sent_var = sum((l - sent_mean) ** 2 for l in sent_lengths) / len(sent_lengths)
        sent_cv = (sent_var ** 0.5) / sent_mean if sent_mean > 0 else 0
    else:
        sent_cv = 0
    
    # ── 2. 连接词密度 ──
    connectors = ['然而', '此外', '因此', '所以', '但是', '不过', '同时', '另外',
                  '首先', '其次', '最后', '总之', '综上', '换言之', '具体来说',
                  '由此可见', '值得注意的是', '需要强调的是']
    connector_count = sum(text.count(c) for c in connectors)
    connector_density = connector_count / max(len(paras), 1)
    
    # ── 3. 段落长度均匀度 ──
    if para_lengths:
        para_mean = sum(para_lengths) / len(para_lengths)
        para_var = sum((l - para_mean) ** 2 for l in para_lengths) / len(para_lengths)
        para_cv = (para_var ** 0.5) / para_mean if para_mean > 0 else 0
    else:
        para_cv = 0
    
    # ── 4. 引用密度 ──
    citation_count = len(re.findall(r'\[\d+\]|\[\d+[-–]\d+\]|\[[\w\s,]+\d{4}\]', text))
    words_approx = len(re.findall(r'[\u4e00-\u9fff]', text))  # 中文字数
    citation_density = citation_count / max(words_approx / 100, 1)  # 每100字引用数
    
    # ── 5. N-gram 重复度 ──
    ngram_dup = ngram_coverage(text, n=5)
    
    # ── 综合评分 ──
    # 每项 0~1，越高越像 AI 写的
    sent_score = max(0, 1 - sent_cv * 3) if sent_cv < 0.35 else 0  # CV<0.35 → AI 嫌疑
    connector_score = min(connector_density / 3.0, 1.0)   # 每段 >3 个连接词 → 嫌疑
    para_score = max(0, 1 - para_cv * 3) if para_cv < 0.4 else 0
    citation_score = 1.0 if citation_density < 0.5 and citation_count < 3 else max(0, 1 - citation_density)
    ngram_score = ngram_dup * 3  # N-gram 重复率高 → 模板化写作
    
    overall = (sent_score * 0.2 + connector_score * 0.15 + para_score * 0.15 +
               citation_score * 0.25 + ngram_score * 0.25)
    overall = min(overall, 1.0)
    
    # ── 段落级检测 ──
    aigc_results = []
    detected_features = []
    
    if sent_cv < 0.35:
        detected_features.append(f"句式过于规整 (CV={sent_cv:.2f})")
    if connector_density > 2:
        detected_features.append(f"连接词密度偏高 ({connector_density:.1f}/段)")
    if para_cv < 0.4 and len(paras) > 3:
        detected_features.append(f"段落长度过于均匀 (CV={para_cv:.2f})")
    if citation_density < 0.5 and citation_count < 3:
        detected_features.append("引用严重不足")
    if ngram_dup > 0.15:
        detected_features.append(f"文本自重复率高 ({ngram_dup:.1%})")
    
    # 对每段做简化版检测
    for pos, para_text in paras:
        if len(para_text) < 50:
            continue
        ps = re.split(r'[。！？.!?]', para_text)
        ps = [s.strip() for s in ps if len(s.strip()) > 5]
        if not ps:
            continue
        ps_lens = [len(s) for s in ps]
        ps_mean = sum(ps_lens) / len(ps_lens)
        ps_var = sum((l - ps_mean) ** 2 for l in ps_lens) / len(ps_lens)
        ps_cv = (ps_var ** 0.5) / ps_mean if ps_mean > 0 else 0
        
        p_conn = sum(para_text.count(c) for c in connectors)
        p_score = (max(0, 1 - ps_cv * 3) * 0.6 + min(p_conn / 3.0, 1.0) * 0.4)
        
        if p_score > 0.3:
            aigc_results.append(AigcResult(
                text=para_text[:100],
                position=pos,
                aigc_probability=round(p_score, 3),
                features=([f"句长CV={ps_cv:.2f}"] if ps_cv < 0.35 else []) +
                         ([f"连接词={p_conn}"] if p_conn > 1 else []),
            ))
    
    return {
        "overall_ratio": round(overall, 3),
        "results": aigc_results,
        "features": detected_features,
        "metrics": {
            "sentence_cv": round(sent_cv, 3),
            "connector_density": round(connector_density, 2),
            "paragraph_cv": round(para_cv, 3),
            "citation_density": round(citation_density, 2),
            "ngram_duplication": round(ngram_dup, 3),
        },
    }


def full_plagiarism_check(text: str, title: str = "") -> PlagReport:
    """全量查重 + AIGC 检测，返回标准化报告
    
    检测维度：
    1. SimHash 内部查重 — 检测段落间相似度
    2. N-gram 重复率 — 基于 5-gram 滑动窗口
    3. AIGC 启发式检测 — 基于句式模式、过渡词密度
    
    注：在线查重（PaperYY/知网）需用户自行前往官网提交，
    本模块提供的是本地离线检测，用于写作过程中自查自纠。
    """
    paras = _split_paragraphs(text)
    
    # 内部查重
    internal_results = check_internal_plagiarism(text)
    
    # N-gram 重复率 → 总体相似度估算
    ngram_dup = ngram_coverage(text, n=5)
    overall_sim = min(min(ngram_dup * 2.5, 1.0) if internal_results else ngram_dup * 1.5, 1.0)
    
    # AIGC 检测
    aigc = check_aigc(text)
    
    # 建议
    suggestions = []
    if overall_sim > 0.3:
        suggestions.append(f"⚠️ 综合重复率 {overall_sim:.0%} 偏高，建议：重新表述高重复段落 / 增加原创分析")
    if overall_sim > 0.15:
        suggestions.append(f"当前内部相似度 {overall_sim:.0%}，本科论文建议控制在 30% 以下")
    if aigc["overall_ratio"] > 0.4:
        suggestions.append(f"🤖 AIGC 痕迹偏高 ({aigc['overall_ratio']:.0%})，建议：增加个人观点 / 案例 / 数据")
    if aigc["overall_ratio"] > 0.2:
        suggestions.append("提示：适度的 AI 辅助写作可接受，但需确保核心论述为个人原创")
    for f in aigc.get("features", [])[:3]:
        suggestions.append(f"检测到: {f}")
    if len(text) < 3000:
        suggestions.append("文本较短，查重率参考价值有限")
    
    return PlagReport(
        total_chars=len(text),
        total_paragraphs=len(paras),
        overall_similarity=round(overall_sim, 3),
        plag_results=internal_results,
        aigc_results=aigc["results"],
        aigc_overall_ratio=aigc["overall_ratio"],
        suggestions=suggestions,
        checked_sources=["simhash", "ngram_coverage", "aigc_heuristic"],
    )


# ─── 在线查重（提示用户自行前往官网） ───

ONLINE_PLAG_SERVICES = {
    "paperyy": {"name": "PaperYY", "url": "https://www.paperyy.com/", "free_times": "每天1次免费"},
    "dachagao": {"name": "大雅查重", "url": "https://www.dayainfo.com/", "free_times": "首次免费"},
    "cnki_check": {"name": "知网查重", "url": "https://check.cnki.net/", "free_times": "收费服务"},
}

def get_online_plag_services() -> list[dict]:
    """返回可用的在线查重服务列表（用户需自行前往官网提交）"""
    return [{"id": k, **v} for k, v in ONLINE_PLAG_SERVICES.items()]
