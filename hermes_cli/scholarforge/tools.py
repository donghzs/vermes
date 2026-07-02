"""
ScholarForge Agent Tools — 让 Vermes Agent 在对话中使用论文写作能力

三件套:
  scholarforge_search  — 搜索学术文献
  scholarforge_write   — 撰写论文章节
  scholarforge_review  — 审稿/评价论文质量
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from tools.registry import registry

logger = logging.getLogger("scholarforge.tools")

# ──────────────────────────────────────────────────────────────
# Schema definitions
# ──────────────────────────────────────────────────────────────

SCHOLARFORGE_SEARCH_SCHEMA = {
    "name": "scholarforge_search",
    "description": (
        "搜索学术文献。输入中文或英文关键词，返回相关论文的标题、作者、年份、摘要。"
        "支持 arXiv、Crossref、Semantic Scholar、PubMed、OpenAlex、DOAJ、CORE 等 7 个免费学术源。"
        "适用于：论文写作前的文献调研、查找某个领域的最新研究、验证某个论断是否有文献支撑。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索关键词，中文或英文，如 '大语言模型幻觉检测' 或 'transformer attention mechanism'",
            },
            "limit": {
                "type": "integer",
                "description": "返回文献数量上限，默认 10，最大 30",
                "minimum": 1,
                "maximum": 30,
                "default": 10,
            },
        },
        "required": ["query"],
    },
}

SCHOLARFORGE_WRITE_SCHEMA = {
    "name": "scholarforge_write",
    "description": (
        "撰写学术论文内容。输入主题和章节描述，输出符合学术规范的论文章节文本（含文献引用标记 [n]）。"
        "适用于：帮助用户写论文的某一段/某一节、根据大纲扩写完整内容、生成规范的学术文本。"
        "注意：生成的引用标记 [n] 仅为编号占位，用户需用 scholarforge_search 找到真实文献后替换。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "description": "论文主题或章节标题，如 '基于对比学习的文本表示方法'",
            },
            "section_type": {
                "type": "string",
                "description": (
                    "章节类型，可选值: 'introduction'(引言), 'literature_review'(文献综述), "
                    "'method'(方法), 'experiment'(实验), 'discussion'(讨论), 'conclusion'(结论), "
                    "'abstract'(摘要)"
                ),
                "enum": [
                    "introduction", "literature_review", "method",
                    "experiment", "discussion", "conclusion", "abstract",
                ],
            },
            "context": {
                "type": "string",
                "description": "可选的已有上下文（如大纲、前几章内容、已知文献），帮助生成更连贯的内容",
            },
            "paper_type": {
                "type": "string",
                "description": (
                    "论文类型，影响写作风格和深度。可选: 本科论文/课程论文/硕士论文/博士论文/"
                    "期刊论文/会议论文/综述论文/开题报告/调研报告/实验报告/案例分析/毕业设计"
                ),
                "enum": [
                    "本科论文", "课程论文", "硕士论文", "博士论文",
                    "期刊论文", "会议论文", "综述论文", "开题报告",
                    "调研报告", "实验报告", "案例分析", "毕业设计",
                ],
                "default": "本科论文",
            },
        },
        "required": ["topic", "section_type"],
    },
}

SCHOLARFORGE_REVIEW_SCHEMA = {
    "name": "scholarforge_review",
    "description": (
        "审阅论文草稿，给出结构化评审意见。包括：创新性评价、方法论优点与缺陷、"
        "论证逻辑检查、语言表达建议、文献引用完整性检查。"
        "适用于：用户写完后自查、帮别人审稿、投稿前的最后一关。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "draft": {
                "type": "string",
                "description": "需要审阅的论文草稿全文或片段",
            },
            "focus": {
                "type": "string",
                "description": (
                    "可选，指定审阅重点。如 '只看方法论'、'重点检查引用'、'全面审阅'。"
                    "默认为全面审阅。"
                ),
            },
        },
        "required": ["draft"],
    },
}


# ──────────────────────────────────────────────────────────────
# Credential resolver (reuse blueprint pattern — zero Vemes core intrusion)
# ──────────────────────────────────────────────────────────────

_PROVIDER_FALLBACK_MODELS = {
    "agnes": "agnes-2.0-flash",
    "deepseek": "deepseek-v4-flash",
    "openai": "gpt-4o-mini",
    "anthropic": "claude-sonnet-4-20250514",
    "ollama": "llama3.2",
    "openrouter": "openrouter/auto",
}


def _resolve_credentials():
    """复用 Vermes 核心凭证链路，与聊天 Agent 完全同步。
    返回 dict(api_key, base_url, model, provider) 或 None。
    """
    from hermes_cli.blueprints.chat import PROVIDERS, _get_chat_credentials
    from hermes_constants import get_hermes_home

    base_url, api_key, default_model = _get_chat_credentials()
    if not api_key or not base_url:
        return None
    # 反查 provider：匹配 .env 中哪个 ENV_KEY 的值等于 default_key
    env_path = get_hermes_home() / ".env"
    env_lines = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    provider = None
    for prov_key, prov_def in PROVIDERS.items():
        env_key = prov_def.get("env_key", "")
        if env_key:
            for line in env_lines.splitlines():
                line = line.strip()
                if line.startswith(f"{env_key}="):
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if val and val == api_key:
                        provider = prov_key
                        break
        if provider:
            break
    return {
        "api_key": api_key,
        "base_url": base_url.rstrip("/"),
        "model": default_model,
        "provider": provider or "",
    }


async def _call_llm(prompt: str, system: str = "") -> str:
    """Call LLM with auto-detected credentials."""
    import httpx

    creds = _resolve_credentials()
    if not creds:
        return "❌ 未找到已配置的 API Key。请在 Vermes 设置中添加至少一个 Provider 的 API Key。"

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    body = {
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 4096,
        "stream": False,
    }
    if creds["model"]:
        body["model"] = creds["model"]

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            f"{creds['base_url']}/chat/completions",
            json=body,
            headers={
                "Authorization": f"Bearer {creds['api_key']}",
                "Content-Type": "application/json",
            },
        )
        if resp.status_code == 200:
            data = resp.json()
            return data["choices"][0]["message"]["content"]
        err = resp.text[:200]
        logger.error(f"LLM call failed ({creds['provider']}/{creds['model']}): {resp.status_code} {err}")
        return f"❌ LLM 调用失败 ({resp.status_code}): {err[:150]}"


# ──────────────────────────────────────────────────────────────
# Tool handlers
# ──────────────────────────────────────────────────────────────

async def _handle_scholarforge_search(args: dict, **kw: Any) -> str:
    """搜索学术文献"""
    query = args.get("query", "")
    limit = min(args.get("limit", 10), 30)

    if not query.strip():
        return "❌ 请提供搜索关键词。"

    try:
        from hermes_cli.scholarforge.search import search_papers

        papers = []
        async for paper in search_papers(query, limit=limit):
            papers.append(paper)

        if not papers:
            return f"🔍 未找到与「{query}」相关的文献。建议：试试换用英文关键词，或调整搜索词。"

        lines = [f"## 文献搜索结果: {query}"]
        lines.append(f"找到 {len(papers)} 篇文献：\n")
        for i, p in enumerate(papers, 1):
            authors = ", ".join(p.authors[:3] if p.authors else [])
            if len(p.authors) > 3:
                authors += " et al."
            lines.append(f"**[{i}] {p.title}**")
            lines.append(f"  {authors} · {p.year} · 📎 {p.citation_count} 引用 · {p.source}")
            if p.abstract:
                lines.append(f"  > {p.abstract[:200]}{'...' if len(p.abstract) > 200 else ''}")
            lines.append("")

        return "\n".join(lines)
    except Exception as e:
        logger.error(f"scholarforge_search error: {e}")
        return f"❌ 文献搜索失败: {str(e)[:200]}"


async def _handle_scholarforge_write(args: dict, **kw: Any) -> str:
    """撰写论文内容"""
    topic = args.get("topic", "")
    section_type = args.get("section_type", "introduction")
    context = args.get("context", "")
    paper_type = args.get("paper_type", "本科论文")

    section_guides = {
        "introduction": (
            "引言章节。应包含：研究背景、问题陈述、研究意义、"
            "相关工作简述、本文贡献概述。字数约 800-1500 字。"
        ),
        "literature_review": (
            "文献综述章节。应包含：领域发展脉络、关键研究成果、"
            "不同观点/方法的对比、研究空白识别。字数约 1000-2000 字。"
        ),
        "method": (
            "方法章节。应包含：方法论选择理由、具体算法/流程描述、"
            "实验设置、评估指标。使用数学符号和公式。字数约 1000-2000 字。"
        ),
        "experiment": (
            "实验章节。应包含：实验设计、数据集描述、基线方法、"
            "结果表格/分析、消融实验。字数约 1000-2000 字。"
        ),
        "discussion": (
            "讨论章节。应包含：结果解读、与已有工作的对比、"
            "局限性分析、未来工作方向。字数约 800-1500 字。"
        ),
        "conclusion": (
            "结论章节。应包含：研究总结、核心贡献重申、"
            "实践启示、未来研究方向。字数约 500-800 字。"
        ),
        "abstract": (
            "摘要。应包含：问题→方法→结果→结论 四要素，"
            "200-300 字，不含引用。"
        ),
    }

    guide = section_guides.get(section_type, "学术论文章节。")
    section_labels = {
        "introduction": "引言",
        "literature_review": "文献综述",
        "method": "方法",
        "experiment": "实验",
        "discussion": "讨论",
        "conclusion": "结论",
        "abstract": "摘要",
    }
    label = section_labels.get(section_type, section_type)

    system_prompt = (
        "你是一个专业的中文学术写作助手。请直接输出学术内容，"
        "不要自我介绍，不要输出 '好的' 或 '我来帮你写' 等开场白。"
        "使用学术规范语言，Markdown 格式。"
    )

    prompt = f"""撰写以下学术论文章节：

【章节】{label}
【主题】{topic}
【论文类型】{paper_type}
【要求】{guide}"""
    # 注入论文类型专属要求
    from hermes_cli.scholarforge.agents import get_paper_type_prompt
    prompt += get_paper_type_prompt(paper_type)
    if context:
        prompt += f"""

【已有上下文】
{context[:2000]}"""

    prompt += """

请直接输出该章节的完整内容（Markdown 格式，{label} 用 ## 标记），
引用文献时使用 [n] 标记（n为编号占位，用户后续会替换为真实文献）。"""

    return await _call_llm(prompt, system_prompt)


async def _handle_scholarforge_review(args: dict, **kw: Any) -> str:
    """审阅论文"""
    draft = args.get("draft", "")
    focus = args.get("focus", "全面审阅")

    if not draft.strip():
        return "❌ 请提供需要审阅的论文草稿。"

    system_prompt = (
        "你是一个严格的学术审稿人（领域主席级别）。请直接输出评审意见，"
        "不要自我介绍。意见应具体、可操作、有建设性。"
    )

    focus_guide = {
        "全面审阅": "全面审阅：创新性、方法论、论证逻辑、语言表达、引用",
        "只看方法论": "重点关注方法论的正确性、是否描述清楚、是否有理论支撑",
        "重点检查引用": "重点关注引用是否恰当、是否遗漏关键文献、引用格式",
    }
    focus_text = focus_guide.get(focus, focus)

    prompt = f"""请对以下论文草稿进行结构化评审。

审阅重点：{focus_text}

评审要求：
1. **总体印象**：一句话概括论文亮点和主要问题
2. **创新性**：与已有工作相比新颖性如何
3. **方法论**：方法是否正确、描述是否清晰
4. **论证逻辑**：论点-论据-结论链条是否严密
5. **语言表达**：学术规范、可读性
6. **引用完整性**：关键文献是否遗漏、引用是否恰当
7. **致命问题**：如有任何无法修正的致命缺陷，明确指出
8. **修改建议**：按优先级列出 3-5 条具体修改建议

论文草稿：
{draft[:8000]}"""

    return await _call_llm(prompt, system_prompt)


# ──────────────────────────────────────────────────────────────
# Registration — 模块导入时自动注册
# ──────────────────────────────────────────────────────────────

def _register_tools():
    """Register all ScholarForge tools in the global registry."""
    registry.register(
        name="scholarforge_search",
        toolset="scholarforge",
        schema=SCHOLARFORGE_SEARCH_SCHEMA,
        handler=_handle_scholarforge_search,
        is_async=True,
        emoji="📚",
        description="搜索学术文献（arXiv/Crossref/Semantic Scholar/PubMed 等 7 个免费源）",
    )
    registry.register(
        name="scholarforge_write",
        toolset="scholarforge",
        schema=SCHOLARFORGE_WRITE_SCHEMA,
        handler=_handle_scholarforge_write,
        is_async=True,
        emoji="✍️",
        description="撰写学术论文内容（引言/文献综述/方法/实验/讨论/结论）",
    )
    registry.register(
        name="scholarforge_review",
        toolset="scholarforge",
        schema=SCHOLARFORGE_REVIEW_SCHEMA,
        handler=_handle_scholarforge_review,
        is_async=True,
        emoji="🔍",
        description="审阅论文草稿，给出结构化评审意见",
    )
    logger.info("[ScholarForge] 3 Agent tools registered: search/write/review")


# Register on import
_register_tools()
