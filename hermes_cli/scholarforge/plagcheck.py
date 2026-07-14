"""
ScholarForge 查重与 AIGC 检测模块 (P0-2)
提供论文查重检测 + AI 痕迹检测,对标 Paperpal (Turnitin) + 千笔 AI 检测

检测策略:
1. 内部查重: 文本内部自相似度检测(段落级)
2. 在线查重: 调用免费查重 API(PaperYY / 格子达 Gocheck / 大雅)
3. AIGC 检测: 调用 AIGC 检测模型 + 启发式特征分析
4. 结果报告: 生成标准化查重报告 JSON

零依赖核心检测算法:
- SimHash 局部敏感哈希 - 段落级相似度
- N-gram 覆盖度 - 句子级重复率
- 引用密度 - 非引用段落占比
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
    text: str                    # 重复文本片段(前100字符)
    length: int                  # 重复长度(字符)
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
    # 中文字符单独拆,英文按空格拆
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
    """SimHash - 局部敏感哈希,用于段落级近似查重"""
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
    """汉明距离(SimHash 相似度)"""
    diff = int(a, 16) ^ int(b, 16)
    return diff.bit_count()


def simhash_similarity(a: str, b: str) -> float:
    """SimHash 相似度 0.0~1.0"""
    return 1.0 - hamming_distance(a, b) / 64.0


# ─── N-gram 覆盖度 ───

def ngram_coverage(text: str, n: int = 5) -> float:
    """N-gram 覆盖率 - 评估文本内部自重复程度"""
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
    """将文本按段落分割,返回 (起始位置, 段落文本)"""
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
    """段落级内部查重 - SimHash 比较

    threshold: SimHash 相似度阈值,>= 此值视为重复
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
    """AIGC 痕迹检测 - 启发式特征分析 + 段落级检测

    检测维度（8维）：
    1. 句式规整度 — AI 倾向于高度一致的句式结构
    2. 连接词密度 — AI 过度使用“然而”“此外”“因此”等
    3. 段落长度均匀度 — AI 段长差异小
    4. 引用模式 — AI 常编造引用或引用密度异常
    5. N-gram 重复度 — 模板化写作导致文本自重复
    6. 四字套话密度 — AI 偏好格式化四字结构
    7. 主语回避 — “本文/本研究”过度使用
    8. 结论绝对化 — “证明了/彻底解决”等断言式表述
    """
    paras = _split_paragraphs(text)
    if not paras:
        return {"overall_ratio": 0.0, "results": [], "features": []}

    para_texts = [p[1] for p in paras]
    para_lengths = [len(p) for p in para_texts]

    # ── 1. 句式规整度 ──
    # 计算句长标准差 / 均值(CV),AI 写作文本 CV 通常 < 0.4
    sentences = re.split(r'[。!?.!?\n]', text)
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
    citation_count = len(re.findall(r'\[\d+\]|\[\d+[--]\d+\]|\[[\w\s,]+\d{4}\]', text))
    words_approx = len(re.findall(r'[\u4e00-\u9fff]', text))  # 中文字数
    citation_density = citation_count / max(words_approx / 100, 1)  # 每100字引用数

    # ── 5. N-gram 重复度 ──
    ngram_dup = ngram_coverage(text, n=5)

    # ── 6. 四字套话密度 ──
    cliche_phrases = [
        '综上所述', '由此可见', '不言而喻', '毋庸置疑', '众所周知',
        '首当其冲', '势在必行', '卓有成效', '有目共睹', '毋庸置疑',
        '事实上', '从某种意义上', '从某种程度上', '从根本上',
        '值得关注的是', '尤为重要的是', '值得强调的是',
        '毫无疑问', '可以预见', '总而言之',
    ]
    cliche_count = sum(text.count(c) for c in cliche_phrases)
    cliche_density = (cliche_count / max(words_approx, 1)) * 1000  # 每千字

    # ── 7. 主语回避密度(泛指主语过度使用)──
    first_person_generic = ['本文', '本研究', '本论文', '笔者', '本文作者', '本研究结果', '本研究发现']
    fp_count = sum(text.count(p) for p in first_person_generic)
    fp_density = fp_count / max(len(paras), 1)

    # ── 8. 结论绝对化检测 ──
    absolutism_patterns = [
        '证明了', '验证了', '充分证明了', '完全证实', '彻底解决',
        '完全一致', '显著提升', '显著改善', '显著增强', '全面提高',
        '彻底消除', '完美呈现', '完美诠释', '无可替代', '不可或缺',
    ]
    abs_count = sum(text.count(p) for p in absolutism_patterns)
    abs_density = abs_count / max(len(paras), 1)

    # ── 综合评分 ──
    # 每项 0~1,越高越像 AI 写的
    sent_score = max(0, 1 - sent_cv * 3) if sent_cv < 0.35 else 0  # CV<0.35 → AI 嫌疑
    connector_score = min(connector_density / 3.0, 1.0)   # 每段 >3 个连接词 → 嫌疑
    para_score = max(0, 1 - para_cv * 3) if para_cv < 0.4 else 0
    citation_score = 1.0 if citation_density < 0.5 and citation_count < 3 else max(0, 1 - citation_density)
    ngram_score = ngram_dup * 3  # N-gram 重复率高 → 模板化写作
    cliche_score = min(cliche_density / 5.0, 1.0)  # 每千字 >5 个四字词 → 嫌疑
    fp_score = min(fp_density / 2.0, 1.0)           # 每段 >2 个泛指主语 → 嫌疑
    abs_score = min(abs_density / 1.5, 1.0) * 0.5   # 绝对化表述 → 嫌疑(权重减半)

    overall = (sent_score * 0.15 + connector_score * 0.10 + para_score * 0.10 +
               citation_score * 0.20 + ngram_score * 0.15 +
               cliche_score * 0.10 + fp_score * 0.10 + abs_score * 0.10)
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
    if cliche_density > 3:
        detected_features.append(f"四字套话偏多 ({cliche_density:.1f}/千字)")
    if fp_density > 1.5:
        detected_features.append(f"泛指主语过度 ({fp_density:.1f}/段)")
    if abs_count > 0:
        detected_features.append(f"结论绝对化表述 ({abs_count}处)")

    # 对每段做简化版检测
    for pos, para_text in paras:
        if len(para_text) < 50:
            continue
        ps = re.split(r'[。!?.!?]', para_text)
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
            "cliche_density": round(cliche_density, 2),
            "first_person_density": round(fp_density, 2),
            "absolutism_density": round(abs_density, 2),
        },
    }


def suggest_deaigc_fixes(text: str) -> list[dict]:
    """De-AIGC 校准建议 — 检测问题后给出可操作的改写方案

    六类改写策略，每条含: type / issue / fix / example
    """
    aigc = check_aigc(text)
    features = aigc.get("features", [])
    metrics = aigc.get("metrics", {})
    suggestions = []

    # 1. 四字套话
    if any("四字套话" in f for f in features) or metrics.get("cliche_density", 0) > 3:
        suggestions.append({
            "type": "cliche",
            "issue": f"四字套话密度 {metrics.get('cliche_density', 0):.1f}/千字，高于学术写作常态",
            "fix": "将格式化四字结构替换为具体、有信息量的表述",
            "example": "“综上所述”→“基于上述三个实验，我们得出”；“由此可见”→“这一结果说明”",
        })

    # 2. 连接词过度
    if any("连接词" in f for f in features) or metrics.get("connector_density", 0) > 2:
        suggestions.append({
            "type": "connector",
            "issue": f"连接词密度 {metrics.get('connector_density', 0):.1f}/段，高于人类写作均值",
            "fix": "删除冗余连接词，保留逻辑必要的少数几个",
            "example": "“首先...其次...最后”→ 直接陈述；“此外”→ 用分号或句号分隔",
        })

    # 3. 主语回避
    if any("泛指主语" in f for f in features) or metrics.get("first_person_density", 0) > 1.5:
        suggestions.append({
            "type": "subject",
            "issue": f"泛指主语（本文/本研究）密度 {metrics.get('first_person_density', 0):.1f}/段",
            "fix": "明确动作主体，或省略不必要的主语",
            "example": "“本文发现”→“数据显示”或“实验观察到”；“本研究证明”→“结果支持”",
        })

    # 4. 绝对化表述
    if any("绝对化" in f for f in features) or metrics.get("absolutism_density", 0) > 0.5:
        suggestions.append({
            "type": "absolutism",
            "issue": "结论章节出现绝对化表述（如“证明”“彻底”“完全”）",
            "fix": "改为条件/概率/程度表述，保留学术严谨性",
            "example": "“证明了户外游戏有效”→“结果表明户外游戏对...有正向影响”",
        })

    # 5. 句长均匀
    if metrics.get("sentence_cv", 1) < 0.35:
        suggestions.append({
            "type": "sentence_length",
            "issue": f"句长变异系数 CV={metrics.get('sentence_cv', 0):.2f}，句式过于规整",
            "fix": "长短句交替，增强节奏感",
            "example": "在长句之间插入短句；将并列长句拆分为主从结构",
        })

    # 6. 引用不足
    if any("引用不足" in f for f in features) or metrics.get("citation_density", 0) < 0.5:
        suggestions.append({
            "type": "citation",
            "issue": f"引用密度 {metrics.get('citation_density', 0):.1f}/百字，低于学术规范",
            "fix": "在关键论断后补充支撑文献（使用 [n] 占位符，后续替换）",
            "example": "“建构游戏能促进合作”→“建构游戏能促进合作[3]”",
        })

    return suggestions


def apply_deaigc_suggestions(text: str, suggestions: list[dict] = None) -> str:
    """规则化自动改写（无 LLM 依赖）

    目前支持：四字套话替换 + 绝对化软化 + 连接词精简
    """
    if suggestions is None:
        suggestions = suggest_deaigc_fixes(text)

    result = text

    # 四字套话替换表
    cliche_map = {
        "综上所述": "基于以上分析",
        "由此可见": "这一结果说明",
        "不言而喻": "值得注意",
        "众所周知": "已有研究表明",
        "毋庸置疑": "数据支持",
        "首当其冲": "最为关键",
        "势在必行": "十分必要",
        "有目共睹": "已被观察到",
        "事实上": "具体来看",
        "从某种意义上说": "在一定程度上",
        "从某种程度上说": "部分而言",
        "从根本上说": "本质上",
        "有鉴于此": "因此",
        "总而言之": "整体而言",
        "概括而言": "简言之",
        "一言以蔽之": "核心结论是",
        "值得关注的是": "值得注意的是",
        "尤为重要的是": "关键的是",
        "值得强调的是": "需要指出",
        "可以预见": "预期",
        "毫无疑问": "证据显示",
    }

    # 绝对化软化表
    absolutism_map = {
        "证明了": "表明",
        "验证了": "支持",
        "充分证明了": "为...提供了证据",
        "完全证实": "证实了主要假设",
        "彻底解决": "显著改善",
        "完全一致": "高度一致",
        "显著提升": "提升",
        "显著改善": "改善",
        "显著增强": "增强",
        "全面提高": "提高",
        "彻底消除": "大幅减少",
        "完美呈现": "较好呈现",
        "完美诠释": "较好诠释",
        "无可替代": "重要",
        "不可或缺": "有价值",
    }

    for cn, natural in cliche_map.items():
        result = result.replace(cn, natural)

    for abs_word, soft_word in absolutism_map.items():
        result = result.replace(abs_word, soft_word)

    # 连接词精简（删除句首多余的“首先/其次/最后”序列标记）
    result = re.sub(r'^[（(]?(首先|其次|再次|最后|第一|第二|第三)[）)]?[，。:：]?\s*', '', result, flags=re.MULTILINE)

    return result


def full_plagiarism_check(text: str, title: str = "") -> PlagReport:
    """全量查重 + AIGC 检测,返回标准化报告

    检测维度:
    1. SimHash 内部查重 - 检测段落间相似度
    2. N-gram 重复率 - 基于 5-gram 滑动窗口
    3. AIGC 启发式检测 - 基于句式模式、过渡词密度

    注:在线查重(PaperYY/知网)需用户自行前往官网提交,
    本模块提供的是本地离线检测,用于写作过程中自查自纠。
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
        suggestions.append(f"⚠️ 综合重复率 {overall_sim:.0%} 偏高,建议:重新表述高重复段落 / 增加原创分析")
    if overall_sim > 0.15:
        suggestions.append(f"当前内部相似度 {overall_sim:.0%},本科论文建议控制在 30% 以下")
    if aigc["overall_ratio"] > 0.4:
        suggestions.append(f"🤖 AIGC 痕迹偏高 ({aigc['overall_ratio']:.0%}),建议:增加个人观点 / 案例 / 数据")
    if aigc["overall_ratio"] > 0.2:
        suggestions.append("提示:适度的 AI 辅助写作可接受,但需确保核心论述为个人原创")
    for f in aigc.get("features", [])[:3]:
        suggestions.append(f"检测到: {f}")
    if len(text) < 3000:
        suggestions.append("文本较短,查重率参考价值有限")

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


# ─── 在线查重(提示用户自行前往官网) ───

ONLINE_PLAG_SERVICES = {
    "paperyy": {"name": "PaperYY", "url": "https://www.paperyy.com/", "free_times": "每天1次免费"},
    "dachagao": {"name": "大雅查重", "url": "https://www.dayainfo.com/", "free_times": "首次免费"},
    "cnki_check": {"name": "知网查重", "url": "https://check.cnki.net/", "free_times": "收费服务"},
}

def get_online_plag_services() -> list[dict]:
    """返回可用的在线查重服务列表(用户需自行前往官网提交)"""
    return [{"id": k, **v} for k, v in ONLINE_PLAG_SERVICES.items()]
