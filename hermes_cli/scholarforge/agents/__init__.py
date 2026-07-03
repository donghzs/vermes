"""
ScholarForge Agent 引擎 — 论文写作 5 Agent + STORM Pipeline
完全独立于 Vermes 核心，通过 Blueprint 注册

SSE 事件格式(与前端 Writer.vue handleSSE 一致)：
  {"type":"thinking", "message":"..."}    事件日志(右侧面板)
  {"type":"searching", "message":"..."}   搜索状态
  {"type":"writing", "message":"..."}     写作状态
  {"type":"citation", "paper":{...}}      文献引用
  {"type":"content", "text":"..."}        正文内容(追加到 streamingText)
  {"type":"done", "message":"..."}        当前 Agent 完成
"""
import asyncio
import json
import logging
import re
import time
from typing import AsyncGenerator
from dataclasses import dataclass, field
from enum import Enum

from ..search import PaperResult, search_papers

try:
    from rapidfuzz import fuzz as _fuzz
except ImportError:
    _fuzz = None

logger = logging.getLogger("scholarforge.agent")


def _validate_citation_refs(text: str, papers: list) -> list[str]:
    """扫描正文中的 [n] 引用，验证是否对应真实文献。
    返回警告列表供前端展示。"""
    refs = re.findall(r'\[(\d+)\]', text)
    max_idx = len(papers)
    warnings = []
    seen = set()
    for ref in refs:
        idx = int(ref)
        if idx < 1 or idx > max_idx:
            warnings.append(f"[引用 {ref}] 无对应文献(文献池仅 {max_idx} 篇)")
        elif ref not in seen and idx <= max_idx:
            p = papers[idx - 1]
            seen.add(ref)
    return warnings


def _clean_citation_format(text: str) -> str:
    """清理 LLM 生成的非法引用格式，统一为 [n] 纯数字格式。
    
    处理以下非法格式：
    - [@金旭杨2025] → 删除（无法映射到编号）
    - [金旭杨2025] → 删除
    - [@Author2020] → 删除
    - (作者, 2025) → 删除  
    - [Author, 2020] → 删除
    """
    # 1. [@中文名年份] 或 [@英文名年份] — 删除整个标记
    text = re.sub(r'\[@[^\]]+\]', '', text)
    # 2. [中文名年份] 但不包含数字编号（如 [金旭杨2025]）
    text = re.sub(r'\[[^\]\d]+\d{4}\]', '', text)
    # 3. (作者, 年份) 或 (作者, 年份: 页码) 格式
    text = re.sub(r'\([^)]{2,20},\s*\d{4}[^)]*\)', '', text)
    # 4. [Author, 2020] 英文名逗号年份格式
    text = re.sub(r'\[[A-Z][a-z]+.*?,\s*\d{4}\]', '', text)
    # 5. 清理可能产生的多余空格和标点（连续空白→单空格，. .→.）
    text = re.sub(r'\s{2,}', ' ', text)
    # 6. 清理 AI 生成的 @image#n、@figure#n、@table#n 占位符（含范围格式 @image#1-5）
    text = re.sub(r'@(?:image|figure|table|chart)#\d+(?:-\d+)?', '', text)
    text = re.sub(r'\.\s*\.', '.', text)
    return text.strip()


def _sanitize_user_input(text: str, max_len: int = 2000) -> str:
    """清洗用户输入，防止 LLM prompt 注入。
    
    策略：
    1. 截断超长输入
    2. 移除可能的指令覆盖模式（"忽略以上"/"ignore above"/"system:"/"<|im_start|>" 等）
    3. 用分隔符包裹，让 LLM 明确这是用户数据而非指令
    """
    if not text:
        return ""
    text = text.strip()[:max_len]
    # 移除常见 prompt injection 模式
    injection_patterns = [
        r'(?i)ignore\s+(?:the\s+)?(?:above|previous|prior)\s+(?:instructions?|prompt|rules?)',
        r'(?i)disregard\s+(?:the\s+)?(?:above|previous|all)',
        r'(?i)you\s+are\s+(?:now|actually)\s+',
        r'(?i)system\s*:\s*',
        r'(?i)<\|im_start\|>',
        r'(?i)<\|im_end\|>',
        r'(?i)\[SYSTEM\]',
        r'(?i)\[/SYSTEM\]',
        r'(?i)forget\s+(?:everything|all\s+(?:previous|prior))',
        r'(?i)new\s+instructions?\s*:',
    ]
    for pattern in injection_patterns:
        text = re.sub(pattern, '[已过滤]', text)
    return text


def _fuzzy_match_title(candidate: str, papers: list) -> int | None:
    """用模糊匹配找到最接近的文献 index(1-based)，未找到返回 None"""
    if not papers or not candidate:
        return None
    best_score = 0
    best_idx = None
    for i, p in enumerate(papers):
        title = getattr(p, 'title', '')
        if not title:
            continue
        if _fuzz is not None:
            score = _fuzz.token_sort_ratio(candidate.lower()[:80], title.lower()[:80])
        else:
            # fallback: simple substring
            score = 100 if candidate.lower()[:30] in title.lower() else 0
        if score > best_score and score >= 60:
            best_score = score
            best_idx = i + 1
    return best_idx


class EventType(str, Enum):
    THINKING = "thinking"
    SEARCHING = "searching"
    READING = "reading"
    WRITING = "writing"
    CITATION = "citation"
    STAGE_CHANGE = "stage"
    CONTENT = "content"
    DONE = "done"
    ERROR = "error"


@dataclass
class PaperCard:
    """论文卡片 — 结构化文献摘要"""
    paper_id: str
    title: str
    authors: list[str] = field(default_factory=list)
    year: str = ""
    venue: str = ""
    abstract: str = ""
    key_findings: list[str] = field(default_factory=list)
    methodology: str = ""
    citation_count: int = 0
    url: str = ""
    source: str = ""

    def to_dict(self):
        return {
            "paper_id": self.paper_id, "title": self.title,
            "authors": self.authors, "year": self.year,
            "venue": self.venue, "abstract": self.abstract,
            "key_findings": self.key_findings, "methodology": self.methodology,
            "citation_count": self.citation_count, "url": self.url, "source": self.source,
        }


@dataclass
class Citation:
    citation_id: str
    paper_title: str
    authors: str
    year: str
    text: str = ""


class ProjectContext:
    """项目级共享上下文 — Agent 间传递数据"""
    def __init__(self, project_id: int | None = None):
        self.project_id = project_id
        self.topic: str = ""
        self.paper_type: str = "本科论文"  # 论文类型，影响所有 Agent 的写作风格
        self.papers: list[PaperCard] = []
        self.citations: list[Citation] = []
        self.outline: dict | None = None
        self.section_contents: dict[str, str] = {}
        self.draft: str = ""
        self.stage: str = "idle"
        self.personas: list[str] = []
        self.research_conversation: list[dict] = []
        self._rag_retriever = None  # PaperRetriever 缓存
        self._rag_paper_count = 0    # 缓存时的文献数，用于失效判断

    def add_paper(self, card: PaperCard):
        if not any(p.paper_id == card.paper_id for p in self.papers):
            self.papers.append(card)

    def to_context_text(self) -> str:
        parts = []
        parts.append(f"论文类型：{self.paper_type}")
        info = PAPER_TYPE_STYLES.get(self.paper_type, PAPER_TYPE_STYLES["本科论文"])
        parts.append(f"写作风格：{info['style']} | 深度：{info['depth']} | 语气：{info['tone']}")
        if self.topic:
            parts.append(f"研究主题：{self.topic}")
        if self.papers:
            parts.append(f"已收集 {len(self.papers)} 篇文献：")
            for i, p in enumerate(self.papers[:10], 1):
                a = p.authors[0] if p.authors else ""
                parts.append(f"  [{i}] {p.title} ({a}, {p.year}) — {p.abstract[:80]}...")
        if self.outline:
            parts.append(f"当前大纲：{json.dumps(self.outline, ensure_ascii=False)[:500]}")
        return "\n".join(parts) if parts else ""


# ====================
# 5 个 Agent
# ====================

class BaseAgent:
    name: str = "base"
    icon: str = "🤖"
    label: str = ""
    description: str = ""
    prompt_hint: str = ""

    def __init__(self, ctx: ProjectContext, llm_call):
        self.ctx = ctx
        self.llm = llm_call

    @staticmethod
    def _safe_input(user_input: str, max_len: int = 2000) -> str:
        """转义用户输入，防止 LLM prompt 注入"""
        return _sanitize_user_input(user_input, max_len)

    async def run(self, user_input: str) -> AsyncGenerator[dict, None]:
        raise NotImplementedError

    @classmethod
    def to_dict(cls):
        return {
            "name": cls.name, "icon": cls.icon, "label": cls.label,
            "description": cls.description, "promptHint": cls.prompt_hint,
        }


class TopicAgent(BaseAgent):
    """选题 Agent — 明确研究方向，验证创新性"""
    name = "topic"
    icon = "💡"
    label = "选题"
    description = "分析研究方向的可行性、创新性和学术价值"
    prompt_hint = "描述你的研究方向，AI 分析可行性和创新性..."

    async def run(self, user_input: str) -> AsyncGenerator[dict, None]:
        yield {"type": "thinking", "message": "分析研究选题..."}
        user_input = self._safe_input(user_input)
        self.ctx.topic = user_input

        prompt = f"""你是一个学术研究方法论专家。用户的论文选题是：

{user_input}

请从以下维度分析这个选题：
1. **研究问题的清晰度**：核心问题是什么？
2. **创新性**：与现有研究有何不同？
3. **可行性**：数据获取、方法、时间方面是否可行？
4. **学术贡献**：预期对领域有何贡献？
5. **建议的研究问题**：提出 3 个具体的研究问题
6. **推荐关键词**：5-8 个核心关键词用于文献检索

请用中文回答，结构清晰。
""" + get_paper_type_prompt(self.ctx.paper_type)

        response = await self.llm(prompt)
        response = _clean_citation_format(response)
        yield {"type": "content", "text": response}
        yield {"type": "done", "message": "选题分析完成"}


class LiteratureAgent(BaseAgent):
    """文献 Agent — STORM 多视角检索 + 综述生成 + 递进式深度研究"""
    name = "literature"
    icon = "📚"
    label = "文献"
    description = "多视角搜索学术文献、生成论文卡片、撰写文献综述"
    prompt_hint = "输入关键词检索文献，AI 生成综述..."

    async def run(self, user_input: str, depth: int = 1) -> AsyncGenerator[dict, None]:
        """文献检索主入口

        Args:
            user_input: 用户输入的研究主题/关键词
            depth: 研究深度 (1=单轮, 2=双轮多视角, 3=三轮含空白分析)
        """
        depth = max(1, min(3, depth))  # 限制 1-3
        user_input = self._safe_input(user_input)

        if depth >= 2:
            async for evt in self._deep_search(user_input, depth):
                yield evt
        else:
            async for evt in self._shallow_search(user_input):
                yield evt

    async def _shallow_search(self, user_input: str) -> AsyncGenerator[dict, None]:
        """单轮搜索(加相关性过滤)"""
        # Step 1: 提取关键词
        yield {"type": "thinking", "message": "分析检索关键词..."}

        kw_prompt = f"""从以下研究主题提取 3-5 个核心检索关键词(英文)，用逗号分隔：

{user_input}"""
        kw_resp = await self.llm(kw_prompt)
        keywords = [k.strip() for k in kw_resp.split(",") if k.strip()][:5]
        if not keywords:
            keywords = [user_input]

        keywords = keywords[:3]
        yield {"type": "searching", "message": f"检索关键词：{', '.join(keywords)}"}

        # Step 2: 多源搜索 + 相关性过滤
        all_papers: dict[str, PaperResult] = {}
        for kw in keywords:
            async for paper in search_papers(kw, limit=8):  # 多搜一点，后面过滤
                if paper.paper_id not in all_papers:
                    # 相关性打分
                    relevance = await self._score_relevance(user_input, paper)
                    if relevance >= 7:  # 只保留 ≥7 分的
                        all_papers[paper.paper_id] = paper
                        self.ctx.add_paper(PaperCard(
                            paper_id=paper.paper_id, title=paper.title,
                            authors=paper.authors, year=paper.year, venue=paper.venue,
                            abstract=paper.abstract, citation_count=paper.citation_count,
                            url=paper.url, source=paper.source,
                        ))
                        yield {"type": "citation", "paper": paper.to_dict()}

        yield {"type": "searching", "message": f"已收集 {len(all_papers)} 篇高相关文献"}

        if not all_papers:
            yield {"type": "done", "message": "未检索到相关文献"}
            return

        # Step 3: 生成文献综述
        yield {"type": "writing", "message": "生成文献综述..."}
        response = await self._generate_review(user_input, list(all_papers.values()))
        response = _clean_citation_format(response)
        yield {"type": "content", "text": "\n\n" + response}
        yield {"type": "done", "message": f"文献综述完成 ({len(all_papers)}篇)"}

    async def _score_relevance(self, topic: str, paper: PaperResult) -> int:
        """让 LLM 给文献相关性打分 1-10"""
        prompt = f"""研究主题：{topic}

文献标题：{paper.title}
文献摘要：{paper.abstract[:500] if paper.abstract else '无'}

请判断这篇文献与研究主题的相关性，只回复 1-10 的数字(10=高度相关，1=完全不相关)："""
        try:
            resp = await self.llm(prompt)
            m = re.search(r'\d+', resp)
            if m is None:
                logger.warning(f"_score_relevance: LLM returned no digit in response: {resp[:100]}")
                return 5
            score = int(m.group())
            return max(1, min(10, score))
        except Exception:
            logger.warning(f"_score_relevance failed for paper, defaulting to 5", exc_info=True)
            return 5  # 默认中等

    async def _generate_personas(self, topic: str, papers: list[PaperResult]) -> list[str]:
        """动态生成研究视角（替代静态 STORM_PERSONAS）

        基于 Round 1 宽泛搜索发现的文献 + 用户 topic，让 LLM 分析出
        该主题下的真实研究视角。例如：
        - "自动驾驶" → ["感知系统", "规控算法", "安全验证", "人因工程"]
        - "气候变化" → ["大气模型", "碳排放政策", "生态影响", "能源转型"]

        Args:
            topic: 用户研究主题
            papers: Round 1 已收集文献（用于推断领域结构）
        Returns:
            list[str]: 3-5 个动态研究视角
        """
        # 构建 little context from round 1 findings
        papers_ctx = ""
        if papers:
            titles = [f"- {p.title}" for p in papers[:8]]
            papers_ctx = "\n".join(titles)
            papers_ctx = f"\n基于以下已发现的文献标题：\n{papers_ctx}\n"

        prompt = f"""你是一个研究领域分析专家。请分析以下研究主题涉及的主要研究方向/视角。

研究主题：{topic}
{papers_ctx}

请从该研究领域提炼 3-5 个**具体的研究视角**（不是泛泛的方法论角色）。
要求：
- 每个视角应该反映该领域实际的研究子方向或学派分歧
- 视角应该具体、有区分度（不要"实证研究者"这种万能角色）
- 结合文献标题推断真实存在的子方向

直接输出，每行一个视角，格式：「视角名：简要说明」
例如：
感知系统架构：关注传感器融合和3D目标检测
端到端规控：关注从感知直接到控制的学习方法
安全验证与仿真：关注事故场景生成和形式化验证"""

        try:
            resp = await self.llm(prompt)
            personas = []
            for line in resp.strip().split("\n"):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                # 去掉编号前缀
                line = re.sub(r'^\d+[\.\)、]\s*', '', line)
                if line and len(line) > 4:
                    personas.append(line)
            if personas:
                return personas[:5]
        except Exception as e:
            logger.warning(f"[ScholarForge] Failed to generate dynamic personas: {e}")

        return []  # caller will use STORM_PERSONAS fallback

    async def _deep_search(self, user_input: str, depth: int) -> AsyncGenerator[dict, None]:
        """递进式深度研究 (depth >= 2)

        流程：
        Round 1: 宽泛主题搜索 → 获取概览
        Round 2: 多视角 Personas 分别聚焦检索 → 丰富视角
        Round 3 (depth=3): 空白分析 → 定向搜索研究空白
        Aggregation: 综合所有轮次发现 → 生成结构化文献综述
        """
        all_papers: dict[str, PaperResult] = {}
        round_findings: list[dict] = []  # 每轮的分析摘要

        # ═══ Round 1: Broad Topic Search ═══
        yield {"type": "stage", "stage": "literature_r1", "pipeline": "start",
               "message": f"🔍 第1轮：宽泛主题搜索 — {user_input}"}
        yield {"type": "thinking", "message": "第1轮：宽泛主题搜索，获取领域概览..."}

        # 提取宽泛关键词
        kw_prompt = f"""从以下研究主题提取 3-5 个**宽泛**的核心检索关键词(英文)，用逗号分隔。
使用领域级术语而非具体方法名：

{user_input}"""
        kw_resp = await self.llm(kw_prompt)
        broad_keywords = [k.strip() for k in kw_resp.split(",") if k.strip()][:3]
        if not broad_keywords:
            broad_keywords = [user_input]

        yield {"type": "searching", "message": f"第1轮关键词：{', '.join(broad_keywords)}"}

        r1_papers: dict[str, PaperResult] = {}
        for kw in broad_keywords:
            async for paper in search_papers(kw, limit=5):
                if paper.paper_id not in all_papers:
                    all_papers[paper.paper_id] = paper
                    r1_papers[paper.paper_id] = paper
                    self.ctx.add_paper(PaperCard(
                        paper_id=paper.paper_id, title=paper.title,
                        authors=paper.authors, year=paper.year, venue=paper.venue,
                        abstract=paper.abstract, citation_count=paper.citation_count,
                        url=paper.url, source=paper.source,
                    ))
                    yield {"type": "citation", "paper": paper.to_dict()}

        yield {"type": "searching", "message": f"第1轮完成，收集 {len(r1_papers)} 篇"}

        # Round 1 分析
        if r1_papers:
            r1_analysis = await self._analyze_round(
                f"第1轮(宽泛搜索)分析",
                user_input,
                list(r1_papers.values()),
                focus="识别该领域的主要研究方向、代表性工作和方法论趋势",
            )
            round_findings.append({"round": 1, "label": "宽泛搜索", "analysis": r1_analysis})
            yield {"type": "content", "text": _clean_citation_format(f"\n\n### 📊 第1轮：领域概览\n\n{r1_analysis}")}

        # ═══ Round 2: Multi-Perspective Search ═══
        yield {"type": "stage", "stage": "literature_r2", "pipeline": "start",
               "message": "🔍 第2轮：多视角深度检索"}

        # 动态生成研究视角（基于 Round 1 发现 + topic）
        yield {"type": "thinking", "message": "正在分析领域结构，生成动态研究视角..."}
        dynamic_personas = await self._generate_personas(user_input, list(r1_papers.values()))
        if not dynamic_personas:
            dynamic_personas = STORM_PERSONAS  # fallback
        # 存到 context 供前端展示
        self.ctx.personas = dynamic_personas
        yield {"type": "personas", "personas": dynamic_personas}

        # 为每个 Persona 分配检索，但限制同时并发数
        r2_papers: dict[str, PaperResult] = {}
        persona_count = min(3, len(dynamic_personas))

        for pi, persona in enumerate(dynamic_personas[:persona_count]):
            yield {"type": "thinking", "message": f"视角 {pi+1}/{persona_count}：{persona[:20]}..."}

            # 基于该视角生成定向检索关键词
            persona_kw_prompt = f"""你是一个学术研究者，视角为「{persona}」。
研究主题：{user_input}

从你的专业视角，生成 2-3 个最相关的英文检索关键词(不同于宽泛主题词，要聚焦于你的专业视角)，
用逗号分隔。只需输出关键词，不要解释。"""
            persona_kw_resp = await self.llm(persona_kw_prompt)
            persona_keywords = [k.strip() for k in persona_kw_resp.split(",") if k.strip()][:2]

            if not persona_keywords:
                continue

            yield {"type": "searching", "message": f"  '{persona[:25]}...' 搜索：{', '.join(persona_keywords)}"}
            persona_papers: dict[str, PaperResult] = {}
            for kw in persona_keywords:
                async for paper in search_papers(kw, limit=3):
                    if paper.paper_id not in all_papers:
                        all_papers[paper.paper_id] = paper
                        r2_papers[paper.paper_id] = paper
                        persona_papers[paper.paper_id] = paper
                        self.ctx.add_paper(PaperCard(
                            paper_id=paper.paper_id, title=paper.title,
                            authors=paper.authors, year=paper.year, venue=paper.venue,
                            abstract=paper.abstract, citation_count=paper.citation_count,
                            url=paper.url, source=paper.source,
                        ))
                        yield {"type": "citation", "paper": paper.to_dict()}

            # 每个视角的微型分析
            if persona_papers:
                pa = await self._analyze_round(
                    f"{persona} 视角分析",
                    user_input,
                    list(persona_papers.values()),
                    focus=f"从'{persona}'角度，这些文献有何独特洞察？",
                )
                round_findings.append({
                    "round": 2,
                    "label": persona,
                    "analysis": pa,
                    "paper_count": len(persona_papers),
                })

        yield {"type": "searching", "message": f"第2轮完成，新增 {len(r2_papers)} 篇文献"}

        # ═══ Round 3: Gap Analysis (depth=3 only) ═══
        if depth >= 3:
            yield {"type": "stage", "stage": "literature_r3", "pipeline": "start",
                   "message": "🔍 第3轮：研究空白定向搜索"}
            yield {"type": "thinking", "message": "第3轮：分析研究空白，定向检索..."}

            # 基于前两轮发现，让 LLM 识别研究空白
            gap_prompt = f"""研究主题：{user_input}

前两轮已检索到的文献领域和发现：
{chr(10).join(f"- {r['label']}: {r['analysis'][:300]}" for r in round_findings)}

请识别 2-3 个该领域明显的**研究空白 (Research Gaps)**，并为每个空白输出 1-2 个具体的英文检索关键词。
格式：
空白1：xxx | 关键词：kw1, kw2
空白2：xxx | 关键词：kw1, kw2"""

            gap_resp = await self.llm(gap_prompt)
            yield {"type": "content", "text": _clean_citation_format(f"\n\n### 🔎 第3轮：研究空白识别\n\n{gap_resp}")}

            # 解析空白关键词
            gap_keywords = []
            for line in gap_resp.split("\n"):
                if "关键词" in line or "keywords" in line.lower():
                    kw_part = line.split("关键词", 1)[-1].split(":", 1)[-1]
                    keywords = [k.strip() for k in kw_part.replace("，", ",").split(",") if k.strip()]
                    gap_keywords.extend(keywords)

            if not gap_keywords:
                gap_keywords = [f"{user_input} research gap", f"{user_input} future work"]

            yield {"type": "searching", "message": f"空白检索：{', '.join(gap_keywords[:5])}"}

            r3_papers: dict[str, PaperResult] = {}
            for kw in gap_keywords[:5]:
                async for paper in search_papers(kw, limit=3):
                    if paper.paper_id not in all_papers:
                        all_papers[paper.paper_id] = paper
                        r3_papers[paper.paper_id] = paper
                        self.ctx.add_paper(PaperCard(
                            paper_id=paper.paper_id, title=paper.title,
                            authors=paper.authors, year=paper.year, venue=paper.venue,
                            abstract=paper.abstract, citation_count=paper.citation_count,
                            url=paper.url, source=paper.source,
                        ))
                        yield {"type": "citation", "paper": paper.to_dict()}

            yield {"type": "searching", "message": f"第3轮完成，新增 {len(r3_papers)} 篇"}

            if r3_papers:
                gpa = await self._analyze_round(
                    "第3轮(研究空白)分析",
                    user_input,
                    list(r3_papers.values()),
                    focus="分析这些文献揭示的研究空白和未来方向",
                )
                round_findings.append({"round": 3, "label": "研究空白", "analysis": gpa})

        # ═══ Aggregation: Synthesize All Round Findings ═══
        yield {"type": "stage", "stage": "literature_agg", "pipeline": "start",
               "message": "📝 综合所有轮次，生成结构化文献综述..."}
        yield {"type": "writing", "message": f"综合 {len(round_findings)} 轮发现，共 {len(all_papers)} 篇文献..."}

        response = await self._synthesize_review(user_input, round_findings, list(all_papers.values()))
        response = _clean_citation_format(response)
        yield {"type": "content", "text": "\n\n" + response}
        yield {"type": "done", "message": f"深度研究完成 ({depth}轮, {len(all_papers)}篇文献)"}

    # ═══ 辅助方法 ═══

    async def _generate_review(self, topic: str, papers: list[PaperResult]) -> str:
        """生成单轮文献综述"""
        papers_text = "\n\n".join([
            f"[{i+1}] {p.title}\n作者：{', '.join(p.authors[:3])}\n{p.year} · {p.venue}\n摘要：{p.abstract}"
            for i, p in enumerate(papers[:10])
        ])

        review_prompt = f"""基于以下文献，用中文撰写一篇学术文献综述。

研究主题：{topic}

文献：
{papers_text}

要求：
1. 按主题分类组织(至少 2-3 个主题类别)
2. 分析各研究的方法论特点
3. 指出现有研究的不足和研究空白
4. **引用格式铁律**：只使用 [1] [2] [3] 纯数字方括号引用
   - ✅ 正确："张三等人提出了基于CNN的方法[1]"
   - ❌ 禁止：[@张三2025]、[Author2020]、(作者, 年份) 等非纯数字格式
5. 每个引用编号对应上方文献列表中同编号的文献
6. **引用真实化**：只能引用上方列出的文献，严禁编造。如文献不足，用"[此处需补充文献]"标注
7. 在末尾列出完整参考文献（含 DOI/URL）
8. **去 AI 痕迹**：段落长短交替，关键论点短句强调；禁止使用"有趣的是""值得注意的是""进一步地"等 AI 典型过渡词；每篇被引文献都要指出其局限
9. **数学公式**：涉及方法论概念时给出数学公式（LaTeX $$...$$ 语法），如相似度计算、优化目标等

请用学术规范语言，2000-3000 字。
""" + get_paper_type_prompt(self.ctx.paper_type)

        response = await self.llm(review_prompt)
        validate_warnings = _validate_citation_refs(response, papers)
        if validate_warnings:
            response += "\n\n---\n⚠️ **引用验证警告**：\n" + "\n".join(f"- {w}" for w in validate_warnings)
        return response

    async def _analyze_round(
        self, label: str, topic: str, papers: list[PaperResult], focus: str
    ) -> str:
        """对单轮搜索结果进行分析摘要"""
        if not papers:
            return f"({label}：未检索到相关文献)"

        papers_text = "\n".join([
            f"  [{i+1}] {p.title} ({p.year}) — {p.abstract[:120]}..."
            for i, p in enumerate(papers[:5])
        ])

        prompt = f"""你是一个学术文献分析助手。

研究主题：{topic}
分析任务：{focus}

本轮检索文献({len(papers)}篇)：
{papers_text}

请用 200-400 字中文总结本轮的主要发现、代表工作和方法论特征。
用编号 [1][2] 引用文献。"""

        try:
            return await self.llm(prompt)
        except Exception as e:
            logger.error(f"Round analysis failed: {e}")
            return f"(分析失败: {e})"

    async def _synthesize_review(
        self, topic: str, round_findings: list[dict], all_papers: list[PaperResult]
    ) -> str:
        """综合所有轮次发现，生成结构化文献综述"""
        # 构建轮次分析上下文
        findings_text = "\n\n".join([
            f"### {r.get('label', '第' + str(r.get('round', '?')) + '轮')}\n\n{r.get('analysis', '')}"
            for r in round_findings
            if r.get("analysis")
        ])

        # 构建文献列表
        papers_text = "\n".join([
            f"[{i+1}] {p.title}\n    作者：{', '.join(p.authors[:3])}\n    {p.year} · {p.venue}\n    摘要：{p.abstract[:150]}..."
            for i, p in enumerate(all_papers[:20])
        ])

        prompt = f"""你是一个资深学术文献综述专家。以下是递进式深度研究的多轮发现，请综合写成一篇结构化文献综述。

研究主题：{topic}

## 多轮研究发现

{findings_text}

## 全部检索文献(共{len(all_papers)}篇)

{papers_text}

## 要求

请撰写一篇完整的结构化文献综述(2000-3500字中文)，包含：

1. **研究背景与范围**(简述领域背景和本文献综述范围)
2. **主题分类**(至少 3 个主题类别，每个类别综合多轮发现)
   - 对于每个类别，选择代表性文献深入分析
3. **方法论比较**(不同方法的优劣和适用范围)
4. **研究空白与未来方向**(基于第3轮空白分析及综合判断，明确指出 2-4 个关键空白)
5. **结论**
6. **参考文献列表**(编号 [1]-[{len(all_papers)}]，仅用上方已有文献)

**关键规则：**
- 引用文献时使用 [1] [2] 格式
- **只能引用上方文献列表中已有的文献**，严禁编造
- 尾部列出完整参考文献列表
- 学术语言规范"""

        response = await self.llm(prompt)
        validate_warnings = _validate_citation_refs(response, all_papers)
        if validate_warnings:
            response += "\n\n---\n⚠️ **引用验证警告**：\n" + "\n".join(f"- {w}" for w in validate_warnings)
        return response


class OutlineAgent(BaseAgent):
    """大纲 Agent — 结构化论文大纲"""
    name = "outline"
    icon = "📋"
    label = "大纲"
    description = "生成结构化的论文章节大纲"
    prompt_hint = "生成论文大纲..."

    async def run(self, user_input: str) -> AsyncGenerator[dict, None]:
        yield {"type": "thinking", "message": "生成论文大纲..."}
        user_input = self._safe_input(user_input)
        context = self.ctx.to_context_text()

        # 元数据关键词(只过滤参考文献/致谢/目录等自动生成项，保留摘要/关键词/结论)
        _META_KEYWORDS = ['参考文献', 'references',
                         '目录', 'table of contents', '致谢', 'acknowledgements',
                         '附录', 'appendix', '注释', 'notes']

        prompt = f"""你是一个经验丰富的学术论文作者。基于以下信息，生成一篇学术论文的结构化大纲。

{context}
用户需求：{user_input}

生成标准学术论文大纲，必须包含从摘要到结论的完整章节结构。

要求：
1. 输出 4-6 个章节(一级标题，用 ## 标记)，必须覆盖：
   - 摘要与关键词（概括核心内容与关键词）
   - 引言（研究背景与问题）
   - 核心章节2-3个（方法/实验/讨论等）
   - 结论（总结发现与展望）
2. 每个章节标注预计字数(如"引言(1000-1500字)")，包含 2-4 个二级子节(###)
3. 章节顺序参考上方【论文类型专属要求】中的结构建议
4. 每个章节标题应具体，反映论文的研究内容

⚠️ 注意：
- 必须包含"摘要与关键词"章节和"结论"章节
- 不要包含"参考文献"（由系统自动生成）
- 不要包含"目录"、"致谢"等元章节

使用 Markdown 格式，## 标记一级章节，### 标记二级子节。
""" + get_paper_type_prompt(self.ctx.paper_type)

        response = await self.llm(prompt)

        # 提取章节(过滤元数据章节)
        sections = []
        for line in response.split("\n"):
            if line.startswith("## "):
                title = line[3:].strip()
                title = re.sub(r'\(\d+-?\d*字\)', '', title).strip()
                # 跳过元数据章节
                title_lower = title.lower()
                if title and len(title) > 2 and not any(
                    title_lower.startswith(kw) or kw in title_lower
                    for kw in _META_KEYWORDS
                ):
                    sections.append(title)

        self.ctx.outline = {"raw": response, "sections": sections}

        # 发送前端兼容的 outline 结构化数据，用于更新大纲导航面板
        outline_for_frontend = [
            {"id": f"sec_{i}", "number": i + 1, "title": s,
             "wordCount": 0, "status": "pending"}
            for i, s in enumerate(sections)
        ]
        yield {"type": "outline", "sections": outline_for_frontend}
        yield {"type": "content", "text": _clean_citation_format(response)}
        yield {"type": "done", "message": f"大纲生成完成 ({len(sections)}章)"}


class WritingAgent(BaseAgent):
    """写作 Agent — 批量逐章撰写，引用文献"""
    name = "writing"
    icon = "✍️"
    label = "写作"
    description = "按大纲逐章撰写完整论文正文"
    prompt_hint = "撰写论文章节..."

    async def run(self, user_input: str, section: str = "") -> AsyncGenerator[dict, None]:
        """批量撰写：按 outline 逐章生成。
        
        Args:
            user_input: 用户输入（可选额外指令）
            section: 指定只写某一节（section_key），空则全部章节
        """
        user_input = self._safe_input(user_input)
        outline = self.ctx.outline
        if not outline:
            yield {"type": "writing", "message": "没有大纲，先生成大纲"}
            yield {"type": "done", "message": "需先生成大纲"}
            return

        context = self.ctx.to_context_text()
        
        # RAG: 构建全文索引用于 per-section 语义检索（缓存到 ctx）
        try:
            from hermes_cli.scholarforge.rag import PaperRetriever
            paper_count = len(self.ctx.papers) if self.ctx.papers else 0
            if self.ctx._rag_retriever is not None and self.ctx._rag_paper_count == paper_count:
                _rag = self.ctx._rag_retriever  # 复用缓存
            else:
                _rag = PaperRetriever()
                if self.ctx.papers:
                    _rag.load_papers(self.ctx.papers)
                self.ctx._rag_retriever = _rag
                self.ctx._rag_paper_count = paper_count
        except Exception:
            _rag = None

        sections = outline.get("sections", []) if isinstance(outline, dict) else []
        if not sections:
            yield {"type": "error", "message": "大纲中没有章节，请重新生成大纲"}
            return

        # 过滤元数据章节（只过滤参考文献/致谢等，保留摘要/关键词/结论）
        _META_WORDS = ['参考文献', 'references',
                       '章节结构', 'chapter structure',
                       '目录', '致谢', 'acknowledgements']
        real_sections = []
        for s in sections:
            title = s.get("title", s) if isinstance(s, dict) else str(s)
            title_lower = title.lower()
            if not any(kw in title_lower for kw in _META_WORDS):
                real_sections.append(s)

        if not real_sections:
            yield {"type": "error", "message": "未提取到有效章节，请重新生成大纲"}
            return

        # 如果指定了 section_key，只写那一节
        if section:
            target = None
            for idx, s in enumerate(real_sections):
                if isinstance(s, dict):
                    sk = s.get("id", f"sec_{idx}")
                else:
                    sk = f"sec_{idx}"
                if sk == section:
                    target = (idx, s, sk)
                    break
            if target:
                real_sections = [target[1]]
                # 重设 idx/key 为实际位置，但保留原 key
                self._single_section_keys = {0: target[2]}
            else:
                yield {"type": "error", "message": f"未找到章节: {section}"}
                return

        for idx, section_item in enumerate(real_sections):
            if isinstance(section_item, dict):
                section_title = section_item.get("title", f"第{idx+1}章")
                section_key = section_item.get("id", f"sec_{idx}")
            else:
                section_title = str(section_item)
                section_key = f"sec_{idx}"

            # 如果指定了 section_key，用原 key 映射
            if hasattr(self, '_single_section_keys') and idx in self._single_section_keys:
                section_key = self._single_section_keys[idx]

            yield {"type": "writing", "message": f"撰写：{section_title}...", "section_key": section_key}
            yield {"type": "stage", "stage": "writing", "section": section_title, "section_key": section_key}

            # RAG: 为本章节检索最相关的 5-8 篇文献
            if _rag and self.ctx.papers:
                rag_results = _rag.retrieve_for_writing(section_title, self.ctx.section_contents.get(section_key, ""), top_k=8)
                relevant_papers = [(p, s) for p, s in rag_results]
                # RAG 透明度：SSE 日志告知前端检索了多少篇
                if rag_results:
                    top_scores = [f"{s:.2f}" for _, s in rag_results[:3]]
                    yield {"type": "searching",
                           "message": f"RAG: 从{len(self.ctx.papers)}篇文献中匹配 {len(rag_results)} 篇 (前3相关度: {', '.join(top_scores)})"}
                else:
                    yield {"type": "thinking",
                           "message": f"RAG: 未找到与'{section_title}'语义匹配的文献，使用全部 {min(8, len(self.ctx.papers))} 篇"}
            else:
                # fallback: 使用全部文献
                relevant_papers = [(p, 1.0) for p in self.ctx.papers[:8]]
                yield {"type": "thinking",
                       "message": f"RAG: 无检索器可用，使用文献池前 {min(8, len(self.ctx.papers))} 篇"}
            
            # 构建 per-section 文献列表（保留原始全局编号）
            papers_text = "\n".join([
                f"[{self.ctx.papers.index(p) + 1 if p in self.ctx.papers else i+1}] {p.title} ({', '.join(p.authors[:3] if hasattr(p, 'authors') and p.authors else [])}, {getattr(p, 'year', '')})"
                for i, (p, _) in enumerate(relevant_papers)
            ])
            
            # 引用映射：使用全局编号（与 papers_text 一致），避免重排后错位
            # 构建真实引用信息（含 DOI/URL）
            real_ref_map = "\n".join([
                f"  [{self.ctx.papers.index(p) + 1 if p in self.ctx.papers else i+1}] {p.title} — {', '.join(p.authors[:2] if hasattr(p, 'authors') and p.authors else [])} ({getattr(p, 'year', '')})\n"
                f"    来源: {getattr(p, 'source', '')} | DOI: {getattr(p, 'doi', '') or '无'} | URL: {getattr(p, 'url', '')}"
                for i, (p, _) in enumerate(relevant_papers)
            ])

            prompt = f"""你是一个中文学术论文作家。撰写以下章节：

【章节信息】
标题：{section_title}
位置：第{idx+1}章(共{len(real_sections)}章)

【研究背景】
{context}

【本章专属文献】以下文献按与本章主题的相关性排序，编号固定，必须严格使用：
{real_ref_map}

【写作要求】
1. 学术规范语言，逻辑严谨，段落清晰
2. **引用格式铁律**：只能使用 [1] [2] [3] 这样的纯数字方括号格式
   - ✅ 正确示例："深度学习在图像识别中取得了显著成果[1][3]"
   - ❌ 禁止：[@张三2025]、[张三2025]、(作者, 年份)、[Author2020] 等任何非纯数字格式
   - 每个引用编号必须对应上方【本章专属文献】的同编号文献
3. **引用真实化铁律**：只能引用上方列出的文献，严禁编造不存在的引用编号或文献
   - 如果某个论点没有对应文献支撑，宁可不引用，也不要编造
   - 如果上方文献不足以支撑某个论点，用"[此处需补充文献]"标注
4. **数学公式要求**（关键！）：
   - 框架设计/方法/算法章节必须包含数学公式，使用 LaTeX 语法 $$...$$ 或 $...$
   - 至少包含：损失函数、优化目标、选择概率、更新规则等核心公式
   - 公式后必须有变量说明（如"其中 α 为学习率，θ 为策略参数"）
   - 其他章节在涉及数学概念时也应给出公式
5. **去 AI 痕迹要求**：
   - 段落长短交替，不要每段都是 4-6 句的均衡长度
   - 关键论点用短句强调（1-2句话独立成段）
   - 禁止使用以下 AI 典型过渡词："有趣的是"、"值得注意的是"、"进一步地"、"值得一提的是"、"需要指出的是"
   - 技术细节要具体（给出参数值、配置、实现细节），不要泛泛而谈
   - 文献综述要有批判性分析（每篇被引文献都要指出其局限），不要只罗列
6. **实验设计要求**（仅实验/结果章节）：
   - 基线方法必须是同一类别或可直接对比的方法
   - 所有对比方法的评估指标必须单位统一（如全部用百分比或全部用小数）
   - 说明实验设置：训练轮数、学习率、批量大小、随机种子、硬件环境
   - 表格中的数据要标注来源（复现还是引用其他论文）
7. 字数按{self.ctx.paper_type}标准：本科/课程每节1500-2500字，硕士/综述3000-5000字，博士5000-8000字，期刊/会议1000-2000字
8. 输出格式：Markdown，章节标题用 ## 开头

请直接输出该章节的完整内容（不要重复本章标题）：
            """ + get_paper_type_prompt(self.ctx.paper_type)

            try:
                content = await self.llm(prompt)
                # 清理可能的重复标题
                content = content.strip()
                if not content:
                    yield {"type": "error", "message": f"{section_title} LLM 返回空内容"}
                    continue
                # 清理非法引用格式 ([@AuthorYear] → 删除)
                content = _clean_citation_format(content)
                if content.startswith(f"## {section_title}") or content.startswith(f"##{section_title}"):
                    lines = content.split("\n", 1)
                    content = lines[1].strip() if len(lines) > 1 else "（此章节待补充内容）"

                full_section = f"## {section_title}\n\n{content}\n\n"
                self.ctx.draft += full_section
                self.ctx.section_contents[section_key] = content

                yield {"type": "content", "text": _clean_citation_format(full_section), "section_key": section_key}
                yield {"type": "writing", "message": f"✓ {section_title} 完成", "section_key": section_key}

            except Exception as e:
                yield {"type": "error", "message": f"{section_title} 撰写失败: {str(e)}"}
                continue

        # 所有章节写入完成
        total_chars = len(self.ctx.draft)

        # ═══════════════════════════════════════════════════════
        # 后处理：组装完整论文结构（标题+摘要+关键词+正文+结论+参考文献）
        # ═══════════════════════════════════════════════════════
        yield {"type": "thinking", "message": "📄 组装完整论文结构..."}
        
        paper_title = self.ctx.topic or (real_sections[0].get("title", "未命名论文") if isinstance(real_sections[0], dict) else str(real_sections[0]))
        
        # 从 draft 中分离摘要/关键词、结论、正文
        parts = re.split(r'\n(?=## )', self.ctx.draft)
        abstract_kw_part = ""
        conclusion_part = ""
        body_parts = []
        
        for part in parts:
            part_lower = part.strip().lower()
            if any(kw in part_lower for kw in ['摘要', 'abstract', '关键词', 'keywords']):
                abstract_kw_part = part.strip()
            elif any(kw in part_lower for kw in ['结论', 'conclusion', '总结', 'summary']):
                conclusion_part = part.strip()
            else:
                body_parts.append(part.strip())
        
        # 组装标准学术论文
        sections = [f"# {paper_title}\n"]
        if abstract_kw_part:
            sections.append(abstract_kw_part + "\n")
        sections.extend(body_parts)
        if conclusion_part:
            sections.append(conclusion_part + "\n")
        
        # 添加参考文献列表（含 DOI/URL，确保引用真实可查）
        if self.ctx.papers and re.search(r'(?i)#{1,3}\s*(参考文献|references)', self.ctx.draft) is None:
            refs = ["## 参考文献\n"]
            for i, p in enumerate(self.ctx.papers):
                authors = ", ".join(p.authors[:3]) if hasattr(p, 'authors') and p.authors else ""
                if hasattr(p, 'authors') and len(p.authors) > 3:
                    authors += " 等"
                year = getattr(p, 'year', '')
                venue = getattr(p, 'venue', '') or getattr(p, 'source', '')
                doi = getattr(p, 'doi', '') or ''
                url = getattr(p, 'url', '') or ''
                paper_id = getattr(p, 'paper_id', '') or ''
                # 构建完整引用：作者. 标题. 期刊, 年份. DOI/URL
                ref_line = f"[{i+1}] {authors}. {p.title}. {venue}, {year}."
                if doi:
                    ref_line += f" DOI: {doi}."
                elif url:
                    ref_line += f" URL: {url}."
                elif paper_id:
                    ref_line += f" [{paper_id}]."
                refs.append(ref_line)
            sections.append("\n".join(refs))
        
        self.ctx.draft = "\n\n".join(sections)
        # 发送完整论文到前端（用于导出面板）
        yield {"type": "content", "text": _clean_citation_format(self.ctx.draft), "section_key": "full_paper"}
        
        yield {"type": "done", "message": f"论文撰写完成(共{len(real_sections)}章，{total_chars}字)",
               "papers": len(self.ctx.papers)}


class RefinementAgent(BaseAgent):
    """润色 Agent — 去重、事实核查、语言润色(禁止编造引用)"""
    name = "refinement"
    icon = "✨"
    label = "润色"
    description = "去重检查、事实核查、语言润色"
    prompt_hint = "润色论文..."

    async def run(self, user_input: str) -> AsyncGenerator[dict, None]:
        """润色入口 — 分章节逐节润色，避免全文压缩"""
        yield {"type": "thinking", "message": "开始润色检查..."}
        user_input = self._safe_input(user_input)
        draft = self.ctx.draft
        if not draft:
            yield {"type": "done", "message": "没有可润色的内容"}
            return

        # Step 1: 去重检查
        yield {"type": "thinking", "message": "检查重复内容..."}
        lines = draft.split("\n")
        seen = set()
        deduped = []
        duplicates = 0
        for line in lines:
            key = line.strip().lower()[:50]
            if key and key in seen and len(key) > 20:
                duplicates += 1
                continue
            seen.add(key)
            deduped.append(line)

        if duplicates > 0:
            yield {"type": "thinking", "message": f"发现并删除 {duplicates} 处重复内容"}

        draft = "\n".join(deduped)

        # Step 2: 深度引用验证（LLM 检查每处引用是否真正支撑断论）
        yield {"type": "thinking", "message": "深度核实引用真实性..."}
        
        # 2a: 范围检查（快速，无需 LLM）
        cited_nums = set(re.findall(r'\[(\d+)\]', draft))
        valid_nums = set(str(i+1) for i in range(len(self.ctx.papers)))
        invalid_citations = cited_nums - valid_nums
        if invalid_citations:
            for num in invalid_citations:
                draft = draft.replace(f"[{num}]", f"[?{num}?]")
        
        # 2b: LLM 深度验证（检查引用是否真正支撑断论）
        verify_results = []
        valid_cited = cited_nums & valid_nums
        if self.ctx.papers and valid_cited:
            try:
                from hermes_cli.scholarforge.citation_verifier import verify_citations
                yield {"type": "thinking", "message": f"正在深度验证 {len(valid_cited)} 处引用..."}
                verify_results = await verify_citations(draft, self.ctx.papers, self.llm)
            except Exception as e:
                logger.warning(f"LLM citation verify failed, falling back to range check: {e}")

        # 始终发出 citation_verify 事件（即使全部通过），前端据此更新面板
        all_results = (
            # LLM 验证结果
            [{"ref": r.ref_num if hasattr(r, 'ref_num') else r.get('ref', '?'),
              "score": r.score if hasattr(r, 'score') else r.get('score', 5),
              "reason": (r.reason if hasattr(r, 'reason') else r.get('reason', ''))[:100]}
             for r in verify_results] +
            # 范围检查发现的无效引用（不在文献池中）
            [{"ref": int(num), "score": 0, "reason": f"引用编号 {num} 不在文献池中（共{len(self.ctx.papers)}篇）"}
             for num in invalid_citations]
        )

        if all_results:
            # 持久化验证结果到 DB（刷新页面后仍可见）
            try:
                if self.ctx.project_id:
                    from hermes_cli.scholarforge import database as db
                    db.save_citation_verifications(self.ctx.project_id, all_results)
            except Exception:
                pass  # 不影响主流程
            yield {
                "type": "citation_verify",
                "results": all_results,
                "errors": len([r for r in all_results if r["score"] < 3]),
                "warnings": len([r for r in all_results if 3 <= r["score"] < 7]),
            }

        # Step 2.5: 真引用替换 — 搜索外部数据库，替换 [n] 占位符为真实文献
        yield {"type": "thinking", "message": "搜索真实文献替换引用占位符..."}
        try:
            from hermes_cli.scholarforge.citation_provider import replace_pseudo_citations
            _keywords = [w.strip() for w in (self.ctx.topic or "").replace("，", " ").split()[:8] if len(w.strip()) > 1]
            new_draft, real_citations = await replace_pseudo_citations(
                draft, self.ctx.topic or "", _keywords or [self.ctx.topic or ""],
                paper_type=self.ctx.paper_type or "本科论文"
            )
            if real_citations:
                draft = new_draft
                # 将真实文献追加到 ctx.papers（供后续验证/导出使用）
                for c in real_citations:
                    self.ctx.papers.append(PaperResult(
                        title=c.title,
                        authors=c.authors,
                        year=c.year,
                        venue=c.venue,
                        abstract="",
                        doi=c.doi,
                        source=c.source,
                        citations=0,
                    ))
                yield {"type": "citation_replace",
                       "count": len(real_citations),
                       "message": f"已匹配并替换 {len(real_citations)} 篇真实文献",
                       "citations": [{"title": c.title[:80], "source": c.source, "year": c.year}
                                     for c in real_citations]}
        except ImportError:
            logger.info("citation_provider not imported, skipping real citation replacement")
        except Exception as e:
            logger.warning(f"Real citation replacement failed (continuing with placeholder refs): {e}")

        # Step 3: 分章节逐节润色（核心改进）
        yield {"type": "thinking", "message": "逐节语言润色..."}
        sections = re.split(r'\n(?=## )', draft)
        polished_sections = []
        total_sections = len(sections)

        for i, section_text in enumerate(sections):
            if not section_text.strip():
                continue
            # 提取章节标题用于进度显示
            header_match = re.match(r'## (.+)', section_text)
            sec_title = header_match.group(1).strip()[:40] if header_match else f"第{i+1}节"

            yield {"type": "writing", "message": f"润色 {i+1}/{total_sections}: {sec_title}"}

            prompt = f"""你是一个学术编辑。对以下论文章节进行语言润色，要求：

1. 修正语法错误、不通顺的表达
2. 统一学术术语使用
3. 优化段落衔接和逻辑连贯性
4. **禁止添加新的文献引用**
5. **禁止编造数据或案例**
6. 保持章节标题不变
7. **保持原文长度和内容**：只做语言层面的修正，不要删除段落、不要缩写内容
8. **去 AI 痕迹**：
   - 段落长短交替，打破“每段4-6句”的均衡节奏
   - 关键论点用短句强调（1-2句话独立成段）
   - 删除以下 AI 典型过渡词：“有趣的是”“值得注意的是”“进一步地”“值得一提的是”“需要指出的是”
   - 将模糊表述替换为具体技术细节（参数值、配置、实现细节）
9. **公式检查**：
   - 如果原文包含 $$...$$ 或 $...$ 公式，确保公式格式正确、变量有说明
   - 如果章节涉及方法论但缺少公式，在适当位置补充（用 LaTeX 语法）
10. **引用格式检查**：确保所有引用为 [1] [2] 纯数字格式，发现非数字格式则修正

原文：
{section_text}

请输出润色后的章节文本：
""" + get_paper_type_prompt(self.ctx.paper_type)

            try:
                polished_sec = await self.llm(prompt)
                polished_sections.append(polished_sec.strip())
            except Exception as e:
                logger.warning(f"润色 {sec_title} 失败: {e}，保留原章节")
                polished_sections.append(section_text.strip())

        self.ctx.draft = "\n\n".join(polished_sections)
        yield {"type": "content", "text": _clean_citation_format("\n\n---\n**润色完成**\n\n")}
        yield {"type": "done", "message": f"润色完成(去重{duplicates}处，标记{len(invalid_citations)}处无效引用)"}

    async def _check_duplicate(self, prev: str, curr: str) -> dict:
        """检查与上一章节的重复内容"""
        prompt = f"""比较以下两章内容，判断当前章节是否与上一章有重复论述的主题。

上一章：
{prev[:800]}...

当前章：
{curr[:800]}...

只回复 JSON：{{"has_duplicate": true/false, "duplicate_topics": "重复的主题描述"}}"""
        try:
            resp = await self.llm(prompt)
            result = json.loads(resp)
            if not isinstance(result, dict):
                return {"has_duplicate": False, "duplicate_topics": str(result)[:100]}
            return result
        except Exception:
            logger.warning("_check_duplicate JSON parse failed, assuming no duplicates")
            return {"has_duplicate": False, "duplicate_topics": ""}

    async def _check_facts(self, text: str) -> dict:
        """检查是否缺少具体数据/案例"""
        prompt = f"""审校以下论文内容，找出所有"泛泛而谈"但没有具体数据、案例或文献支撑的论点。

内容：
{text[:1500]}

只回复 JSON：{{"missing": ["论点1", "论点2"]}}，如果没有则返回空数组。"""
        try:
            resp = await self.llm(prompt)
            result = json.loads(resp)
            if not isinstance(result, dict):
                return {"missing": []}
            return result
        except Exception:
            logger.warning("_check_facts JSON parse failed, assuming no missing facts")
            return {"missing": []}


# 多视角 Personas(用于 LiteratureAgent 的 STORM 风格检索)
STORM_PERSONAS = [
    "实证研究者(关注实验设计、数据分析方法)",
    "理论建构者(关注概念框架、理论模型)",
    "应用导向研究者(关注实践应用、政策建议)",
    "批评者(关注研究局限、方法论缺陷)",
    "跨学科研究者(关注跨领域连接和创新方法)",
]

# 不同论文类型的写作风格指南 — Agent 在 prompt 中动态注入
PAPER_TYPE_STYLES: dict[str, dict] = {
    "本科论文": {
        "style": "教学导向、概念清晰、实证为主",
        "depth": "基础到中等，注重方法论的完整介绍，引用 20-40 篇",
        "tone": "正式但易懂，句式中等长度",
        "structure_hint": "摘要-引言-理论基础-系统设计-测试-总结",
    },
    "课程论文": {
        "style": "简洁紧凑、课程作业深度",
        "depth": "基础，引用 8-15 篇，重在展示理解",
        "tone": "学习者口吻，学术但不过于正式",
        "structure_hint": "摘要-引言-相关理论-讨论-结论",
    },
    "硕士论文": {
        "style": "研究导向、逻辑严谨、有创新贡献",
        "depth": "中等偏深，引用 50-80 篇，方法论要详尽",
        "tone": "正式学术，句式复杂，强调研究的独特性",
        "structure_hint": "绪论-文献综述-方法-实验-讨论-结论",
    },
    "博士论文": {
        "style": "原创研究、理论创新、学术贡献突出",
        "depth": "非常深，引用 100+ 篇，强调原始贡献和理论深度",
        "tone": "高度学术化，论证严密，承认研究局限",
        "structure_hint": "绪论-文献综述-理论基础-方法-多组实验-综合讨论-创新点",
    },
    "期刊论文": {
        "style": "精炼原创、IMRaD 结构、面向同行评审",
        "depth": "深，引用 30-50 篇，强调方法可复现",
        "tone": "高度精炼，段落短，重点突出",
        "structure_hint": "Abstract-Introduction-Related Work-Method-Experiment-Discussion-Conclusion",
    },
    "会议论文": {
        "style": "极致精炼（8-10 页）、新颖性突出、demo 友好",
        "depth": "中，引用 15-25 篇，方法部分占大头",
        "tone": "紧凑有力，每句话都要有信息量",
        "structure_hint": "Abstract-Intro-Related-Method-Experiment-Conclusion",
    },
    "综述论文": {
        "style": "全景视野、分类体系、批判性分析",
        "depth": "广而深，引用 80+ 篇，强调覆盖面和分析深度",
        "tone": "客观中立，多观点并陈，指出研究空白",
        "structure_hint": "引言-背景-分类体系-方法对比-挑战-未来方向",
    },
    "开题报告": {
        "style": "计划导向、可行性论证、研究意义",
        "depth": "中等，引用 30-50 篇，重在论证方案可行性",
        "tone": "前瞻性、说服力强，逻辑清晰",
        "structure_hint": "背景与意义-现状-目标-方法-进度-可行性",
    },
    "调研报告": {
        "style": "实务导向、数据驱动、决策建议",
        "depth": "中，引用 20-40 篇，混合使用数据、访谈、案例",
        "tone": "客观、务实、可操作",
        "structure_hint": "背景-方法-现状发现-问题分析-建议对策",
    },
    "实验报告": {
        "style": "精确记录、客观分析、误差讨论",
        "depth": "中，重在数据真实性和分析严谨性",
        "tone": "客观、精确、科学",
        "structure_hint": "目的原理-设备步骤-数据记录-处理分析-结论误差",
    },
    "案例分析": {
        "style": "理论结合实际、深度剖析、提炼启示",
        "depth": "中，引用 15-30 篇，理论框架是关键",
        "tone": "分析性、批判性、启发性",
        "structure_hint": "背景-描述-理论框架-分析-讨论-结论",
    },
    "毕业设计": {
        "style": "工程导向、完整系统、文档规范",
        "depth": "中，强调系统设计、实现、测试的完整性",
        "tone": "技术性强、结构清晰、文档化",
        "structure_hint": "引言-技术基础-需求-设计-实现-测试-总结",
    },
}


def get_paper_type_prompt(paper_type: str) -> str:
    """生成 paper_type 专属的写作指引，注入到所有 Agent 的 prompt 中"""
    info = PAPER_TYPE_STYLES.get(paper_type, PAPER_TYPE_STYLES["本科论文"])
    return (
        f"\n\n【论文类型专属要求】\n"
        f"- 类型：{paper_type}\n"
        f"- 风格：{info['style']}\n"
        f"- 深度：{info['depth']}\n"
        f"- 语气：{info['tone']}\n"
        f"- 结构建议：{info['structure_hint']}\n"
        f"请严格按上述要求生成内容。"
    )


# Agent 注册表


class ReviewerAgent(BaseAgent):
    """审稿 Agent — 独立 LLM（防自评偏差），四维审查论文质量
    
    使用独立 provider/model 审查 draft，与 writing Agent 隔离防自评偏差。
    四维：结构与逻辑 / 引用质量 / 数据案例 / 学术表达
    """
    name = "reviewer"
    icon = "🔍"
    label = "审稿"
    description = "独立审稿——结构/逻辑/引用/数据四维审查"
    prompt_hint = "审查全文质量..."

    def __init__(self, ctx: ProjectContext, llm_call, review_scope: str = "full"):
        super().__init__(ctx, llm_call)
        self.review_scope = review_scope

    async def run(self, user_input: str = "") -> AsyncGenerator[dict, None]:
        yield {"type": "thinking", "message": "🔍 独立审稿中（使用独立模型审查，防自评偏差）..."}
        user_input = self._safe_input(user_input)
        draft = self.ctx.draft or user_input
        if not draft or len(draft.strip()) < 100:
            yield {"type": "done", "message": "正文过短，无法审查"}
            return

        outline = self.ctx.outline or {}
        sections = outline.get("sections", []) if isinstance(outline, dict) else []
        papers_count = len(self.ctx.papers) if self.ctx.papers else 0

        reviews = []
        if self.review_scope in ("full", "structure"):
            yield {"type": "thinking", "message": "📐 审查结构与逻辑..."}
            reviews.append(("结构与逻辑", "📐", await self._review_structure(draft, sections)))
        if self.review_scope in ("full", "citations"):
            yield {"type": "thinking", "message": "📚 审查引用质量..."}
            reviews.append(("引用质量", "📚", await self._review_citations(draft, papers_count)))
        if self.review_scope in ("full", "expression"):
            yield {"type": "thinking", "message": "✍️ 审查学术表达..."}
            reviews.append(("学术表达", "✍️", await self._review_expression(draft)))

        yield {"type": "thinking", "message": "📊 汇总审稿报告..."}
        total_issues = 0

        report_lines = ["# 📋 审稿报告\n"]
        fixed_reviews = []
        for dim_name, dim_icon, dim_result in reviews:
            if not isinstance(dim_result, dict):
                dim_result = {"score": 5, "issues": [], "suggestions": [str(dim_result)[:100]]}
            fixed_reviews.append((dim_name, dim_icon, dim_result))
            score = dim_result.get("score", 5)
            issues = dim_result.get("issues", [])
            suggestions = dim_result.get("suggestions", [])
            total_issues += len(issues)
            report_lines.append(f"## {dim_icon} {dim_name} 评分: {score}/10\n")
            if issues:
                report_lines.append(f"### 问题（{len(issues)} 处）\n")
                for issue in issues:
                    report_lines.append(f"- {issue}")
                report_lines.append("")
            if suggestions:
                report_lines.append(f"### 改进建议\n")
                for s in suggestions:
                    report_lines.append(f"- {s}")
                report_lines.append("")

        avg_score = sum(d.get("score", 5) for _, _, d in fixed_reviews) / max(len(fixed_reviews), 1)
        report_lines.insert(1, f"**综合评分**: {avg_score:.1f}/10 | **总问题**: {total_issues}\n")

        review_report = "\n".join(report_lines)
        yield {
            "type": "review",
            "report": review_report,
            "score": round(avg_score, 1),
            "total_issues": total_issues,
            "dimensions": [{"name": n, "score": d.get("score", 5)} for n, _, d in fixed_reviews],
        }
        yield {"type": "done", "message": f"审稿完成（{avg_score:.1f}/10）"}

    async def _review_structure(self, draft: str, sections: list) -> dict:
        sec_text = "\n".join([f"- {s.get('title','?')}" for s in sections[:10]]) if sections else "（无大纲）"
        prompt = f"""你是学术审稿人，审查以下论文的结构与逻辑。\n预期大纲:\n{sec_text}\n正文（前 3000 字）:\n{draft[:3000]}\n审查: 章节结构清晰度、逻辑衔接、论证链完整性、是否有重复/跳跃。\n只回复 JSON: {{"score": 1-10, "issues": [...], "suggestions": [...]}}"""
        try:
            resp = await self.llm(prompt)
            result = json.loads(resp)
            if not isinstance(result, dict):
                result = {"score": 5, "issues": [], "suggestions": [str(result)[:100]]}
            return result
        except Exception:
            return {"score": 5, "issues": [], "suggestions": ["LLM 审查失败"]}

    async def _review_citations(self, draft: str, papers_count: int) -> dict:
        refs = re.findall(r'\[(\d+)\]', draft)
        unique_refs = sorted(set(int(r) for r in refs))
        prompt = f"""你是学术审稿人，审查引用质量。\n引用统计: {len(refs)}处, 唯一编号:{unique_refs[:15]}, 文献池:{papers_count}。\n正文（前 3000 字）:\n{draft[:3000]}\n审查: 无效引用、引用堆砌、关键论断是否支撑、常识滥用引用。\n只回复 JSON: {{"score": 1-10, "issues": [...], "suggestions": [...]}}"""
        try:
            resp = await self.llm(prompt)
            result = json.loads(resp)
            if not isinstance(result, dict):
                result = {"score": 5, "issues": [], "suggestions": [str(result)[:100]]}
            invalid = [r for r in unique_refs if r > papers_count or r < 1]
            if invalid:
                result.setdefault("issues", []).insert(0, f"P0: 无效引用{invalid}(超出[1-{papers_count}])")
            return result
        except Exception:
            return {"score": 5, "issues": [], "suggestions": ["LLM 审查失败"]}

    async def _review_expression(self, draft: str) -> dict:
        prompt = f"""你是学术语言审稿人，审查以下论文的学术表达。\n正文（前 3000 字）:\n{draft[:3000]}\n审查: 口语化表达、术语不规范、句式复杂/重复、主语不当。\n只回复 JSON: {{"score": 1-10, "issues": [...], "suggestions": [...]}}"""
        try:
            resp = await self.llm(prompt)
            result = json.loads(resp)
            if not isinstance(result, dict):
                result = {"score": 5, "issues": [], "suggestions": [str(result)[:100]]}
            return result
        except Exception:
            return {"score": 5, "issues": [], "suggestions": ["LLM 审查失败"]}


# Agent 注册表
AGENTS: dict[str, type[BaseAgent]] = {
    "topic": TopicAgent,
    "literature": LiteratureAgent,
    "outline": OutlineAgent,
    "writing": WritingAgent,
    "refinement": RefinementAgent,
    "reviewer": ReviewerAgent,
}
