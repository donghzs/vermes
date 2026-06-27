"""
ScholarForge Agent 引擎 — 论文写作 5 Agent + STORM Pipeline
完全独立于 Vermes 核心，通过 Blueprint 注册

SSE 事件格式（与前端 Writer.vue handleSSE 一致）：
  {"type":"thinking", "message":"..."}    事件日志（右侧面板）
  {"type":"searching", "message":"..."}   搜索状态
  {"type":"writing", "message":"..."}     写作状态
  {"type":"citation", "paper":{...}}      文献引用
  {"type":"content", "text":"..."}        正文内容（追加到 streamingText）
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
            warnings.append(f"[引用 {ref}] 无对应文献（文献池仅 {max_idx} 篇）")
        elif ref not in seen and idx <= max_idx:
            p = papers[idx - 1]
            seen.add(ref)
    return warnings


def _fuzzy_match_title(candidate: str, papers: list) -> int | None:
    """用模糊匹配找到最接近的文献 index（1-based），未找到返回 None"""
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
        self.papers: list[PaperCard] = []
        self.citations: list[Citation] = []
        self.outline: dict | None = None
        self.draft: str = ""
        self.stage: str = "idle"
        self.personas: list[str] = []
        self.research_conversation: list[dict] = []

    def add_paper(self, card: PaperCard):
        if not any(p.paper_id == card.paper_id for p in self.papers):
            self.papers.append(card)

    def to_context_text(self) -> str:
        parts = []
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

请用中文回答，结构清晰。"""

        response = await self.llm(prompt)
        yield {"type": "content", "text": response}
        yield {"type": "done", "message": "选题分析完成"}


class LiteratureAgent(BaseAgent):
    """文献 Agent — STORM 多视角检索 + 综述生成"""
    name = "literature"
    icon = "📚"
    label = "文献"
    description = "多视角搜索学术文献、生成论文卡片、撰写文献综述"
    prompt_hint = "输入关键词检索文献，AI 生成综述..."

    async def run(self, user_input: str) -> AsyncGenerator[dict, None]:
        # Step 1: 提取关键词
        yield {"type": "thinking", "message": "分析检索关键词..."}

        kw_prompt = f"""从以下研究主题提取 3-5 个核心检索关键词（英文），用逗号分隔：

{user_input}"""
        kw_resp = await self.llm(kw_prompt)
        keywords = [k.strip() for k in kw_resp.split(",") if k.strip()][:5]
        if not keywords:
            keywords = [user_input]

        keywords = keywords[:3]  # 限制最多 3 个关键词
        yield {"type": "searching", "message": f"检索关键词：{', '.join(keywords)}"}

        # Step 2: 多源搜索
        all_papers: dict[str, PaperResult] = {}
        for kw in keywords:
            async for paper in search_papers(kw, limit=5):
                if paper.paper_id not in all_papers:
                    all_papers[paper.paper_id] = paper
                    self.ctx.add_paper(PaperCard(
                        paper_id=paper.paper_id, title=paper.title,
                        authors=paper.authors, year=paper.year, venue=paper.venue,
                        abstract=paper.abstract, citation_count=paper.citation_count,
                        url=paper.url, source=paper.source,
                    ))
                    yield {"type": "citation", "paper": paper.to_dict()}

        yield {"type": "searching", "message": f"已收集 {len(all_papers)} 篇文献"}

        if not all_papers:
            yield {"type": "done", "message": "未检索到相关文献"}
            return

        # Step 3: 生成文献综述
        yield {"type": "writing", "message": "生成文献综述..."}

        papers_text = "\n\n".join([
            f"[{i+1}] {p.title}\n作者：{', '.join(p.authors[:3])}\n{p.year} · {p.venue}\n摘要：{p.abstract}"
            for i, p in enumerate(list(all_papers.values())[:10])
        ])

        review_prompt = f"""基于以下文献，用中文撰写一篇学术文献综述。

研究主题：{user_input}

文献：
{papers_text}

要求：
1. 按主题分类组织（至少 2-3 个主题类别）
2. 分析各研究的方法论特点
3. 指出现有研究的不足和研究空白
4. 使用学术引用格式 [1] [2] 引用文献，**只引用上方给出的文献**，不要编造
5. 每个引用必须对应真实文献，如文献[1]对应上方第1篇
6. 在末尾列出所有参考文献

请用学术规范语言，2000-3000 字。"""

        response = await self.llm(review_prompt)
        validate_warnings = _validate_citation_refs(response, list(all_papers.values()))
        if validate_warnings:
            warn_text = "\n\n---\n⚠️ **引用验证警告**：\n" + "\n".join(f"- {w}" for w in validate_warnings)
            response += warn_text
            yield {"type": "content", "text": warn_text}
        yield {"type": "content", "text": "\n\n" + response}
        yield {"type": "done", "message": f"文献综述完成 ({len(all_papers)}篇)"}


class OutlineAgent(BaseAgent):
    """大纲 Agent — 结构化论文大纲"""
    name = "outline"
    icon = "📋"
    label = "大纲"
    description = "生成结构化的论文章节大纲"
    prompt_hint = "生成论文大纲..."

    async def run(self, user_input: str) -> AsyncGenerator[dict, None]:
        yield {"type": "thinking", "message": "生成论文大纲..."}

        context = self.ctx.to_context_text()

        prompt = f"""你是一个经验丰富的学术论文作者。基于以下信息，生成一篇学术论文的结构化大纲。

{context}
用户需求：{user_input}

生成标准学术论文大纲，包含：
1. **标题**（中英文）
2. **摘要**（200-300 字中文）
3. **关键词**（5-8 个）
4. **章节结构**：
   - 每个一级章节标注预计字数（如"引言（1000-1500字）"）
   - 每个一级章节包含 2-4 个二级子节
5. **参考文献**（列出已有文献的引用）

使用 Markdown 格式，## 标记一级章节，### 标记二级子节。"""

        response = await self.llm(prompt)

        # 提取章节
        sections = []
        for line in response.split("\n"):
            if line.startswith("## "):
                title = line[3:].strip()
                title = re.sub(r'\(\d+-?\d*字\)', '', title).strip()
                if title and len(title) > 2:
                    sections.append(title)

        self.ctx.outline = {"raw": response, "sections": sections}
        yield {"type": "content", "text": response}
        yield {"type": "done", "message": f"大纲生成完成 ({len(sections)}章)"}


class WritingAgent(BaseAgent):
    """写作 Agent — 逐节撰写，引用文献"""
    name = "writing"
    icon = "✍️"
    label = "写作"
    description = "按章节撰写论文正文，引用文献"
    prompt_hint = "撰写论文章节..."

    async def run(self, user_input: str) -> AsyncGenerator[dict, None]:
        yield {"type": "writing", "message": "开始撰写..."}

        context = self.ctx.to_context_text()

        prompt = f"""你是一个中文学术论文作家。基于已有的研究背景撰写论文章节。

{context}

用户需求：{user_input}

要求：
1. 使用学术规范语言，逻辑严谨
2. 每条观点尽可能引用文献 [1] [2]
3. 段落之间过渡自然
4. 避免口语化和主观表述
5. 中文片段长度合理（100-300 字为一段）

请完成以上写作任务。"""

        response = await self.llm(prompt)
        ref_warnings = _validate_citation_refs(response, self.ctx.papers)
        if ref_warnings:
            yield {"type": "warning", "message": "\n".join(ref_warnings)}
        yield {"type": "content", "text": response}
        yield {"type": "done", "message": "写作完成"}


class RefinementAgent(BaseAgent):
    """润色 Agent — 学术语言规范化，去 AI 味"""
    name = "refinement"
    icon = "✨"
    label = "润色"
    description = "审校修改，提升学术语言质量，去除 AI 写作痕迹"
    prompt_hint = "粘贴需要润色的内容..."

    async def run(self, user_input: str) -> AsyncGenerator[dict, None]:
        yield {"type": "thinking", "message": "审校润色中..."}

        prompt = f"""你是中文学术审校专家。请逐段审校以下论文内容，重点关注：

1. **学术规范**：术语准确、引用恰当
2. **语言质量**：去除口语化、提升学术性
3. **逻辑结构**：段落逻辑清晰、过渡自然
4. **去 AI 味**：避免"首先其次最后"等机械表述，避免"值得注意的是"等 AI 常用句式
5. **原创性**：检查是否存在泛泛而谈的空话

对每一段给出"审校建议"和"修改后版本"。在末尾给出总体评价（1-5 分）。

---
{user_input}"""

        response = await self.llm(prompt)
        yield {"type": "content", "text": response}
        yield {"type": "done", "message": "润色完成"}


# 多视角 Personas（用于 LiteratureAgent 的 STORM 风格检索）
STORM_PERSONAS = [
    "实证研究者（关注实验设计、数据分析方法）",
    "理论建构者（关注概念框架、理论模型）",
    "应用导向研究者（关注实践应用、政策建议）",
    "批评者（关注研究局限、方法论缺陷）",
    "跨学科研究者（关注跨领域连接和创新方法）",
]

# Agent 注册表
AGENTS: dict[str, type[BaseAgent]] = {
    "topic": TopicAgent,
    "literature": LiteratureAgent,
    "outline": OutlineAgent,
    "writing": WritingAgent,
    "refinement": RefinementAgent,
}
