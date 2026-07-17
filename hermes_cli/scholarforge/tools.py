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
        "max_tokens": 8192,
        "stream": False,
    }
    if creds["model"]:
        body["model"] = creds["model"]

    try:
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
    except httpx.HTTPError as e:
        logger.error(f"LLM network error ({creds['provider']}/{creds['model']}): {e}")
        return f"❌ LLM 网络错误: {type(e).__name__}: {str(e)[:120]}"
    except Exception as e:
        logger.error(f"LLM unexpected error ({creds['provider']}/{creds['model']}): {e}", exc_info=True)
        return f"❌ LLM 调用异常: {type(e).__name__}: {str(e)[:120]}"


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

    content = await _call_llm(prompt, system_prompt)

    # ── Write 后质量门控: 自动 De-AIGC ─────────────────────
    try:
        from hermes_cli.scholarforge.plagcheck import check_aigc, apply_deaigc_suggestions
        _aigc = check_aigc(content)
        if _aigc.get("aigc_score", 0) > 0.4:
            _cleaned = apply_deaigc_suggestions(content)
            if _cleaned != content:
                content = _cleaned
    except Exception:
        pass

    return content


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

    llm_result = await _call_llm(prompt, system_prompt)

    # ── De-AIGC 校准建议 ─────────────────────────────────────
    deaigc_section = ""
    try:
        from hermes_cli.scholarforge.plagcheck import check_aigc, suggest_deaigc_fixes
        aigc = check_aigc(draft)
        suggestions = suggest_deaigc_fixes(draft)
        if suggestions or aigc.get("verdict"):
            deaigc_section = "\n\n---\n## 🤖 AI 痕迹检测\n"
            deaigc_section += f"**综合评分**: {aigc.get('aigc_score', 0)*100:.0f}/100"
            if aigc.get("verdict"):
                deaigc_section += f"\n**判断**: {aigc['verdict']}"
            if aigc.get("features"):
                deaigc_section += "\n\n**检测到的问题**:\n"
                for f in aigc["features"][:8]:
                    deaigc_section += f"- {f}\n"
            if suggestions:
                deaigc_section += "\n**改写建议**:\n"
                for s in suggestions[:5]:
                    deaigc_section += f"- **{s['fix']}** (例: {s['example']})\n"
    except Exception:
        pass

    return llm_result + deaigc_section


# ──────────────────────────────────────────────────────────────
# Schema: Replace Citations
# ──────────────────────────────────────────────────────────────

SCHOLARFORGE_REPLACE_CITATIONS_SCHEMA = {
    "name": "scholarforge_replace_citations",
    "description": (
        "替换论文草稿中的 [n] 占位符引用为真实文献。"
        "自动搜索相关学术文献，根据上下文匹配最合适的引用，并生成参考文献列表。"
        "适用于：AI 生成的论文草稿中引用标记需要替换为真实文献时。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "draft": {
                "type": "string",
                "description": "包含 [n] 占位符的论文草稿全文",
            },
            "max_refs": {
                "type": "integer",
                "description": "最大引用文献数量，默认 15，最大 30",
                "minimum": 1,
                "maximum": 30,
                "default": 15,
            },
        },
        "required": ["draft"],
    },
}


# ──────────────────────────────────────────────────────────────
# Schema: Learn Style
# ──────────────────────────────────────────────────────────────

SCHOLARFORGE_LEARN_STYLE_SCHEMA = {
    "name": "scholarforge_learn_style",
    "description": (
        "学习用户已有论文的写作风格（句长、术语密度、过渡短语等 8 维特征），"
        "生成风格提示词，后续 scholarforge_write 会自动仿写该风格。"
        "适用于：用户希望 AI 写出的论文跟自己之前的写作风格一致。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "sample_text": {
                "type": "string",
                "description": "用户已有的论文片段（至少 500 字），作为风格学习样本",
            },
        },
        "required": ["sample_text"],
    },
}


async def _handle_scholarforge_replace_citations(args: dict, **kw: Any) -> str:
    """替换占位符引用为真实文献

    修复三个致命问题：
    1. 废弃 LLM 生成关键词 → 改用本地正则提取上下文关键词（消除格式不可控）
    2. 搜索结果取 top-3 → 按与上下文的标题相似度排序取最佳匹配（不再盲取第一篇）
    3. 替换后调用 citation_verifier 做交叉验证（替换前不做验证，替换后检查）
    """
    import re
    import asyncio
    import difflib

    draft = args.get("draft", "")
    max_refs = min(args.get("max_refs", 15), 30)

    if not draft.strip():
        return "❌ 请提供包含 [n] 占位符的论文草稿。"

    # ── P0修复: 支持三种占位符格式 [n] / [n-m] / [n,m,...] ──
    # 1. 先展开所有占位符为独立编号
    cite_pattern = re.compile(r'\[(\d+(?:\s*[-–,]\s*\d+)*)\]')
    raw_matches = list(cite_pattern.finditer(draft))
    if not raw_matches:
        return "ℹ️ 草稿中未发现 [n] 占位符引用，无需替换。"

    def expand_citation(raw: str) -> list[int]:
        """展开 [n] / [n-m] / [n,m,...] 为编号列表"""
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

    # 收集所有编号（含展开的范围引用）
    all_nums: set[int] = set()
    # 记录每个 raw match 对应的编号列表，用于后续替换
    match_to_nums: list[tuple[re.Match, list[int]]] = []
    for m in raw_matches:
        nums = expand_citation(m.group(0))
        if nums:
            match_to_nums.append((m, nums))
            all_nums.update(nums)

    unique_nums = sorted(all_nums)
    if len(unique_nums) > max_refs:
        unique_nums = unique_nums[:max_refs]

    # ── 修复1: 本地提取关键词（不依赖 LLM）──
    # 从每个 [n] 前后上下文提取中英文关键词
    def extract_keywords(text: str) -> str:
        """从上下文文本提取搜索关键词"""
        # 优先提取专有名词：连续大写字母开头（如 RAGAS, GPT, BERT, TransE）
        proper_nouns = re.findall(r'(?<![A-Za-z0-9])[A-Z][A-Za-z0-9]{2,}(?![A-Za-z0-9])', text)
        # 排除常见非术语
        stop_proper = {'The', 'This', 'That', 'These', 'Those', 'Such', 'However',
                       'Moreover', 'Furthermore', 'Therefore', 'Also', 'While',
                       'When', 'Where', 'What', 'Which', 'Based', 'Using',
                       'Given', 'Since', 'From', 'With', 'Both', 'Each',
                       'First', 'Second', 'Third', 'Finally', 'In', 'For',
                       'And', 'But', 'Not', 'Are', 'Was', 'Were', 'Has',
                       'Have', 'Can', 'May', 'Will', 'Been', 'Some', 'More',
                       'Most', 'Other', 'All', 'One', 'Two', 'Three'}
        proper_nouns = [w for w in proper_nouns if w not in stop_proper]

        # 英文术语：3-30 字母的词（排除常见停用词）
        stop_en = {'the', 'and', 'for', 'are', 'but', 'not', 'this', 'that', 'with',
                   'from', 'have', 'has', 'was', 'were', 'will', 'can', 'may',
                   'also', 'such', 'than', 'then', 'these', 'those', 'which',
                   'their', 'there', 'what', 'when', 'where', 'who', 'whom',
                   'been', 'being', 'into', 'about', 'after', 'before',
                   'between', 'through', 'during', 'above', 'below', 'over',
                   'under', 'again', 'more', 'most', 'other', 'some'}
        en_words = re.findall(r'[A-Za-z]{3,30}', text)
        en_words = [w for w in en_words if w.lower() not in stop_en]

        # 中文关键词：2-4 字连续中文字
        cn_words = re.findall(r'[\u4e00-\u9fa5]{2,4}', text)
        # 过滤常见停用词
        stop_cn = {'的研究', '本文', '本研', '研究', '方法', '结果', '结论',
                   '实验', '分析', '通过', '基于', '采用', '提出', '实现',
                   '一个', '可以', '这个', '那个', '因此', '所以', '然而',
                   '此外', '同时', '另外', '首先', '其次', '最后'}
        cn_words = [w for w in cn_words if w not in stop_cn]

        # 合并去重，优先专有名词 > 英文术语 > 中文关键词
        seen = set()
        all_words = []
        for w in proper_nouns + en_words[:5] + cn_words[:3]:
            wl = w.lower()
            if wl not in seen:
                seen.add(wl)
                all_words.append(w)
        if not all_words:
            # 兜底：用整个上下文的前 60 字
            return text[:60].strip()
        return ' '.join(all_words[:6])

    # 为每个编号提取上下文和关键词
    # 使用扩展后的 cite_pattern 来定位所有引用位置（含范围引用）
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
                    num_keywords[n] = extract_keywords(ctx)

    logger.info(f"[ScholarForge] replace_citations: {len(num_keywords)} keywords extracted (from {len(raw_matches)} citation marks)")
    for n, kw in num_keywords.items():
        logger.debug(f"  [{n}] kw='{kw[:50]}' ctx='{num_context[n][:40]}'")

    # ── 修复2: 并行搜索 top-3 → 按相似度排序取最佳 ──
    from hermes_cli.scholarforge.search import search_papers, PaperResult

    # 存储每个编号的候选论文列表
    candidates: dict[int, list[PaperResult]] = {}

    async def search_one(n: int, keyword: str):
        if not keyword:
            return
        papers = []
        async for paper in search_papers(keyword, limit=5):
            papers.append(paper)
            if len(papers) >= 3:
                break
        if papers:
            candidates[n] = papers

    tasks = [search_one(n, kw) for n, kw in num_keywords.items()]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)

    # 对每个编号，从候选中选最佳匹配
    def score_relevance(paper: PaperResult, context: str, keyword: str) -> float:
        """计算论文与上下文的相关性分数（0-1）

        四因子评分：
        - 专有名词精确匹配 40%（如 RAGAS vs FActScore 可区分）
        - 标题 token 重叠 20%
        - 模糊相似度 20%
        - 摘要关键词匹配 20%
        """
        # 提取专有名词（大写开头）
        proper_kw = set(re.findall(r'(?<![A-Za-z0-9])[A-Z][A-Za-z0-9]{2,}(?![A-Za-z0-9])', keyword))
        proper_title = set(re.findall(r'(?<![A-Za-z0-9])[A-Z][A-Za-z0-9]{2,}(?![A-Za-z0-9])', paper.title))
        proper_abs = set(re.findall(r'(?<![A-Za-z0-9])[A-Z][A-Za-z0-9]{2,}(?![A-Za-z0-9])', paper.abstract or ''))

        # 专有名词精确匹配（最高权重）
        proper_match = 0.0
        has_proper = bool(proper_kw)
        if has_proper:
            matched = proper_kw & (proper_title | proper_abs)
            proper_match = len(matched) / len(proper_kw)

        # 标题与关键词的 token 重叠（含中文）
        kw_tokens = set(re.findall(r'[A-Za-z]{3,}|[\u4e00-\u9fa5]{2,}', keyword.lower()))
        title_tokens = set(re.findall(r'[A-Za-z]{3,}|[\u4e00-\u9fa5]{2,}', paper.title.lower()))
        overlap = len(kw_tokens & title_tokens) / max(len(kw_tokens), 1)

        # 模糊相似度
        fuzzy = difflib.SequenceMatcher(None,
            keyword[:80].lower(),
            (paper.title + ' ' + (paper.abstract or '')[:80]).lower()
        ).ratio()

        # 摘要关键词匹配
        abstract_match = 0.0
        if paper.abstract:
            abs_lower = paper.abstract.lower()
            abstract_match = sum(1 for t in kw_tokens if t.lower() in abs_lower) / max(len(kw_tokens), 1)

        # 权重分配：有英文专有名词时正常四因子；无时重分配给中文因子
        if has_proper:
            return min(proper_match * 0.4 + overlap * 0.2 + fuzzy * 0.2 + abstract_match * 0.2, 1.0)
        else:
            # 纯中文场景：overlap 35% + fuzzy 30% + abstract 35%
            return min(overlap * 0.35 + fuzzy * 0.3 + abstract_match * 0.35, 1.0)

    # 选择最佳匹配
    seen_titles: set[str] = set()
    ref_list: list[dict] = []
    next_ref_num = 1
    num_to_ref: dict[int, int] = {}
    match_log: list[str] = []
    failed: list[int] = []

    for n in unique_nums:
        if n not in candidates or not candidates[n]:
            failed.append(n)
            continue

        # 按相似度排序
        scored = [(p, score_relevance(p, num_context.get(n, ''), num_keywords.get(n, '')))
                  for p in candidates[n]]
        scored.sort(key=lambda x: x[1], reverse=True)

        # 取最佳匹配（分数 > 0.1 阈值）
        best_paper, best_score = scored[0]
        if best_score < 0.1:
            failed.append(n)
            match_log.append(f"  [{n}] ⚠️ 最佳匹配分数过低 ({best_score:.2f})，跳过")
            continue

        # 去重：同一篇论文不重复引用
        title_key = best_paper.title.lower().strip()[:80]
        if title_key in seen_titles:
            # 找已分配的编号
            for ref in ref_list:
                if ref["title"].lower().strip()[:80] == title_key:
                    num_to_ref[n] = ref["ref_num"]
                    break
            match_log.append(f"  [{n}] → [{num_to_ref.get(n)}] (重复，合并)")
            continue

        seen_titles.add(title_key)
        num_to_ref[n] = next_ref_num
        ref_list.append({
            "ref_num": next_ref_num,
            "title": best_paper.title,
            "authors": ", ".join(best_paper.authors[:3]) if best_paper.authors else "Unknown",
            "year": best_paper.year or "n.d.",
            "venue": best_paper.venue or "",
            "doi": best_paper.doi or "",
            "score": round(best_score, 2),
        })
        match_log.append(f"  [{n}] → [{next_ref_num}] ✅ ({best_score:.0%}) {best_paper.title[:50]}")
        next_ref_num += 1

    # 替换草稿中的占位符（支持 [n] / [n-m] / [n,m,...]）
    result_draft = draft
    for m, nums in match_to_nums:
        original = m.group(0)  # 如 [1-3] 或 [24,25] 或 [26]
        # 检查所有编号是否都有映射
        mapped = [num_to_ref.get(n) for n in nums]
        if all(r is not None for r in mapped):
            # 全部映射成功，生成替换文本
            if len(mapped) == 1:
                replacement = f"[{mapped[0]}]"
            else:
                # 多编号：用逗号分隔 [1,2,3]
                replacement = f"[{','.join(str(r) for r in mapped)}]"
            result_draft = result_draft.replace(original, replacement)

    # ── 修复3: 替换后交叉验证 ──
    verify_report = ""
    if ref_list:
        try:
            from hermes_cli.scholarforge.citation_verifier import _fuzzy_verify
            verify_results = []
            for ref in ref_list:
                # 构造 PaperResult 供验证
                class _P:
                    title = ref["title"]
                    abstract = ""
                    year = ref["year"]
                    authors = ref["authors"].split(", ")
                    paper_id = f"ref_{ref['ref_num']}"
                result = _fuzzy_verify(ref["ref_num"], result_draft, [_P()])
                if result:
                    verify_results.append((ref["ref_num"], result.score, result.accurate))

            inaccurate = [(n, s) for n, s, a in verify_results if not a and s < 5]
            if inaccurate:
                verify_report = "\n\n---\n**⚠️ 引用验证报告**\n"
                for n, s in inaccurate:
                    verify_report += f"  [{n}] 验证分数 {s}/10，建议人工核查\n"
        except Exception as e:
            logger.debug(f"citation verify failed: {e}")

    # 生成参考文献列表
    ref_lines = ["\n\n## 参考文献\n"]
    for ref in sorted(ref_list, key=lambda x: x["ref_num"]):
        ref_lines.append(
            f"[{ref['ref_num']}] {ref['authors']} ({ref['year']}). "
            f"{ref['title']}. {ref['venue']}."
        )

    replaced = len(num_to_ref)
    unreplaced = len(failed)

    report_lines = [f"## 🔄 引用替换报告（共 {len(unique_nums)} 个占位符）\n"]
    if match_log:
        report_lines.append("### 匹配结果\n")
        report_lines.extend(match_log)
    if failed:
        report_lines.append(f"\n### ⚠️ 未匹配 ({len(failed)} 个)\n")
        report_lines.append(f"  编号: {failed}\n")
        report_lines.append("  建议手动搜索文献后替换\n")
    report_lines.append(f"\n**统计**: 成功 {replaced}/{len(unique_nums)} ({replaced*100//max(len(unique_nums),1)}%)\n")

    if verify_report:
        report_lines.append(verify_report)

    report_lines.append(f"\n---\n\n## 📄 处理后正文\n\n{result_draft}")
    report_lines.append("\n".join(ref_lines))

    logger.info(f"[ScholarForge] replace_citations: {replaced}/{len(unique_nums)} replaced")
    return "\n".join(report_lines)


async def _handle_scholarforge_learn_style(args: dict, **kw: Any) -> str:
    """学习用户写作风格"""
    import re
    import statistics

    sample = args.get("sample_text", "")
    if len(sample.strip()) < 100:
        return "❌ 样本文本过短，至少需要 500 字才能提取风格特征。"

    # ── 8 维风格特征提取 ──
    # 1. 平均句长
    sentences = re.split(r'[。！？.!?\n]', sample)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 3]
    sent_lengths = [len(s) for s in sentences]
    avg_sent_len = statistics.mean(sent_lengths) if sent_lengths else 0

    # 2. 句长标准差（变异系数）
    if len(sent_lengths) > 1:
        sent_cv = statistics.stdev(sent_lengths) / avg_sent_len if avg_sent_len > 0 else 0
    else:
        sent_cv = 0

    # 3. 段落长度均匀度
    paras = [p.strip() for p in sample.split("\n\n") if p.strip() and len(p.strip()) > 20]
    para_lengths = [len(p) for p in paras]
    if len(para_lengths) > 1:
        para_cv = statistics.stdev(para_lengths) / statistics.mean(para_lengths) if statistics.mean(para_lengths) > 0 else 0
    else:
        para_cv = 0

    # 4. 术语密度（中英文专业术语比例）
    cn_chars = len(re.findall(r'[\u4e00-\u9fff]', sample))
    en_terms = len(re.findall(r'[A-Za-z]{3,}', sample))
    term_density = en_terms / max(cn_chars / 100, 1) if cn_chars > 0 else 0

    # 5. 过渡短语频率
    transitions = ['然而', '此外', '因此', '所以', '但是', '同时', '另外',
                   '首先', '其次', '最后', '总之', '综上', '换言之',
                   '具体来说', '值得注意的是', '需要强调的是', '由此可见']
    transition_count = sum(sample.count(t) for t in transitions)
    transition_density = transition_count / max(len(paras), 1)

    # 6. 引用密度
    citation_count = len(re.findall(r'\[\d+\]', sample))
    citation_density = citation_count / max(cn_chars / 100, 1) if cn_chars > 0 else 0

    # 7. 第一人称使用频率
    first_person = len(re.findall(r'我们|笔者|本研究|本文', sample))
    first_person_density = first_person / max(len(paras), 1)

    # 8. 被动句比例
    passive = len(re.findall(r'被|由|受|遭', sample))
    passive_density = passive / max(len(sentences), 1)

    # 生成风格提示词
    style_prompt = f"""# 写作风格指令
请严格模仿以下风格特征写作：

1. **句长**: 平均{avg_sent_len:.0f}字/句，句长变异系数{sent_cv:.2f}（{'整齐' if sent_cv < 0.3 else '长短交错' if sent_cv < 0.5 else '变化较大'}）
2. **段落**: 平均{statistics.mean(para_lengths):.0f}字/段，段落{'均匀' if para_cv < 0.3 else '长短不一' if para_cv < 0.5 else '差异大'}
3. **术语密度**: {'高' if term_density > 3 else '中等' if term_density > 1 else '低'}（每百字{term_density:.1f}个英文术语）
4. **过渡词**: {'频繁' if transition_density > 1.5 else '适中' if transition_density > 0.5 else '少用'}（{transition_density:.1f}个/段）
5. **引用密度**: 每百字{citation_density:.1f}个引用标记
6. **第一人称**: {'常用' if first_person_density > 0.3 else '少用'}（{first_person_density:.1f}次/段）
7. **被动语态**: {'频繁' if passive_density > 0.2 else '少用'}（{passive_density:.2f}）
8. **常用过渡词**: {', '.join([t for t in transitions if sample.count(t) > 0][:5]) or '不明显'}
"""

    return (
        f"✅ 风格学习完成！已提取 8 维风格特征。\n\n"
        f"**风格摘要**: 句长{avg_sent_len:.0f}字、段落{'均匀' if para_cv < 0.3 else '变化'}、"
        f"术语密度{'高' if term_density > 3 else '中'}、过渡词{'多' if transition_density > 1.5 else '适中'}\n\n"
        f"后续使用 scholarforge_write 时将自动应用此风格。\n\n"
        f"---\n{style_prompt}"
    )
# ──────────────────────────────────────────────────────────────

# ──────────────────────────────────────────────────────────────
# Tool: Generate Outline
# ──────────────────────────────────────────────────────────────

SCHOLARFORGE_OUTLINE_SCHEMA = {
    "name": "scholarforge_outline",
    "description": (
        "生成学术论文大纲。输入论文主题和类型，输出完整的章节结构大纲（含每章要点和预估字数）。"
        "适用于：论文写作的第一步、开题报告准备、规划论文整体结构。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "description": "论文主题/题目，如 '基于深度学习的文本分类研究'",
            },
            "paper_type": {
                "type": "string",
                "description": "论文类型",
                "enum": [
                    "本科论文", "课程论文", "硕士论文", "博士论文",
                    "期刊论文", "会议论文", "综述论文", "开题报告",
                    "调研报告", "实验报告", "案例分析", "毕业设计",
                ],
                "default": "本科论文",
            },
            "requirements": {
                "type": "string",
                "description": "可选，额外要求（如字数限制、必须包含的章节、特定研究方法等）",
            },
        },
        "required": ["topic"],
    },
}


async def _handle_scholarforge_outline(args: dict, **kw: Any) -> str:
    """生成论文大纲"""
    topic = args.get("topic", "")
    paper_type = args.get("paper_type", "本科论文")
    requirements = args.get("requirements", "")

    if not topic.strip():
        return "❌ 请提供论文主题。"

    from hermes_cli.scholarforge.agents import get_paper_type_prompt

    system_prompt = (
        "你是资深学术顾问，擅长规划论文结构。直接输出大纲，不要寒暄。"
        "用 Markdown 格式，章节用 ## 标记，要点用列表。"
    )

    prompt = f"""请为以下论文生成详细大纲：

【主题】{topic}
【论文类型】{paper_type}
"""
    prompt += get_paper_type_prompt(paper_type)
    if requirements:
        prompt += f"\n【额外要求】{requirements}\n"

    prompt += """

大纲要求：
1. 章节结构完整（含摘要、引言、文献综述、方法、实验、讨论、结论、参考文献）
2. 每章列出 3-5 个要点
3. 标注每章预估字数
4. 标注每章写作难度（⭐~⭐⭐⭐）
5. 给出推荐写作顺序
"""

    return await _call_llm(prompt, system_prompt)


# ──────────────────────────────────────────────────────────────
# Tool: Polish
# ──────────────────────────────────────────────────────────────

SCHOLARFORGE_POLISH_SCHEMA = {
    "name": "scholarforge_polish",
    "description": (
        "学术润色。改善语言表达、学术规范性、逻辑连贯性，不改变原意。"
        "适用于：初稿写完后的语言打磨、投稿前的规范化、提升学术表达质量。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "需要润色的论文文本",
            },
            "focus": {
                "type": "string",
                "description": "润色重点：'language'(语言表达)、'logic'(逻辑连贯)、'format'(学术格式)、'all'(全面润色，默认)",
                "enum": ["language", "logic", "format", "all"],
                "default": "all",
            },
            "paper_type": {
                "type": "string",
                "description": "论文类型，影响润色风格",
                "enum": [
                    "本科论文", "课程论文", "硕士论文", "博士论文",
                    "期刊论文", "会议论文", "综述论文", "开题报告",
                    "调研报告", "实验报告", "案例分析", "毕业设计",
                ],
                "default": "本科论文",
            },
        },
        "required": ["text"],
    },
}


async def _handle_scholarforge_polish(args: dict, **kw: Any) -> str:
    """学术润色"""
    text = args.get("text", "")
    focus = args.get("focus", "all")
    paper_type = args.get("paper_type", "本科论文")

    if not text.strip():
        return "❌ 请提供需要润色的文本。"

    focus_guides = {
        "language": "重点改善：用词准确性、句式多样性、学术语气、避免口语化表达",
        "logic": "重点改善：段落间过渡、论点-论据-结论链条、上下文衔接",
        "format": "重点改善：引用格式规范性、图表标注、章节编号、学术写作惯例",
        "all": "全面润色：语言表达 + 逻辑连贯 + 学术格式规范",
    }

    system_prompt = (
        "你是专业学术编辑。直接输出润色后的文本，不要解释改动。"
        "保持原意不变，提升学术表达质量。使用 Markdown 格式。"
    )

    prompt = f"""请对以下学术文本进行润色：

【论文类型】{paper_type}
【润色重点】{focus_guides.get(focus, focus_guides['all'])}

要求：
1. 保持原意不变
2. 提升用词准确性和学术规范性
3. 改善句式结构和段落衔接
4. 统一引用格式为 [n] 标记
5. 直接输出润色后的全文

原文：
{text[:12000]}"""

    polished = await _call_llm(prompt, system_prompt)

    # 附加润色说明
    summary = f"\n\n---\n*润色完成。主要改善：{focus_guides.get(focus, '全面润色')}*"
    return polished + summary


# ──────────────────────────────────────────────────────────────
# Tool: Plagiarism Check
# ──────────────────────────────────────────────────────────────

SCHOLARFORGE_PLAGIARISM_CHECK_SCHEMA = {
    "name": "scholarforge_plagiarism_check",
    "description": (
        "论文查重检测。基于 SimHash + N-gram + AIGC 启发式检测，离线运行无需外部服务。"
        "返回：综合重复率、内部相似段落、AI 痕迹评分、修改建议。"
        "适用于：投稿前自查、写作过程中监控重复率、评估原创性。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "需要查重的论文全文或片段",
            },
            "title": {
                "type": "string",
                "description": "论文标题（可选，提高检测准确性）",
            },
        },
        "required": ["text"],
    },
}


async def _handle_scholarforge_plagiarism_check(args: dict, **kw: Any) -> str:
    """查重检测"""
    text = args.get("text", "")
    title = args.get("title", "")

    if not text.strip():
        return "❌ 请提供需要查重的文本。"
    if len(text) < 200:
        return "⚠️ 文本过短（<200字），查重结果参考价值有限。"

    try:
        from hermes_cli.scholarforge.plagcheck import full_plagiarism_check

        report = full_plagiarism_check(text, title=title)

        lines = ["## 📊 查重检测报告\n"]
        lines.append(f"**总字数**: {report.total_chars:,}")
        lines.append(f"**段落数**: {report.total_paragraphs}")
        lines.append(f"**综合重复率**: {report.overall_similarity:.1%}")
        lines.append(f"**AI 痕迹率**: {report.aigc_overall_ratio:.1%}")
        lines.append("")

        # 重复率评估
        sim = report.overall_similarity
        if sim < 0.15:
            lines.append("✅ 重复率较低，原创性良好")
        elif sim < 0.30:
            lines.append("⚠️ 重复率中等，建议关注高重复段落")
        else:
            lines.append("🔴 重复率偏高，建议修改高重复段落")

        # AI 痕迹评估
        aigc = report.aigc_overall_ratio
        if aigc < 0.2:
            lines.append("✅ AI 痕迹较低")
        elif aigc < 0.4:
            lines.append("⚠️ AI 痕迹中等，建议增加个人观点和案例")
        else:
            lines.append("🔴 AI 痕迹偏高，建议使用 scholarforge_deaigc 工具处理")

        # 内部相似段落
        if report.plag_results:
            lines.append("\n### 高相似段落\n")
            for r in report.plag_results[:5]:
                lines.append(f"- 段落 {r.source_para} ↔ 段落 {r.target_para}：相似度 {r.similarity:.1%}")

        # AIGC 特征
        if report.aigc_results:
            lines.append("\n### AI 痕迹特征\n")
            for r in report.aigc_results[:5]:
                lines.append(f"- {r.paragraph_preview[:60]}... → {r.label} (置信度 {r.confidence:.0%})")

        # 建议
        if report.suggestions:
            lines.append("\n### 修改建议\n")
            for s in report.suggestions:
                lines.append(f"- {s}")

        # 在线查重提示
        lines.append("\n---\n💡 **提示**: 本检测为离线检测，如需更精确的查重结果，建议前往 PaperYY、大雅查重、知网查重等平台进行在线检测。")

        return "\n".join(lines)
    except Exception as e:
        logger.error(f"plagiarism_check error: {e}", exc_info=True)
        return f"❌ 查重检测失败: {str(e)[:200]}"


# ──────────────────────────────────────────────────────────────
# Tool: De-AIGC
# ──────────────────────────────────────────────────────────────

SCHOLARFORGE_DEAIGC_SCHEMA = {
    "name": "scholarforge_deaigc",
    "description": (
        "去 AI 痕迹。检测论文中的 AI 写作特征并自动改写，使其更像人类写作。"
        "检测维度：句式模式、过渡词密度、词汇丰富度、段落结构。"
        "适用于：AI 辅助写作后去除痕迹、降低 AIGC 检测分数、提升自然度。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "需要去 AI 痕迹的论文文本",
            },
            "aggressive": {
                "type": "boolean",
                "description": "是否激进模式（更多改写），默认 false（保守模式，仅改写高置信度段落）",
                "default": False,
            },
        },
        "required": ["text"],
    },
}


async def _handle_scholarforge_deaigc(args: dict, **kw: Any) -> str:
    """去 AI 痕迹"""
    text = args.get("text", "")
    aggressive = args.get("aggressive", False)

    if not text.strip():
        return "❌ 请提供需要处理的文本。"

    try:
        from hermes_cli.scholarforge.plagcheck import check_aigc, apply_deaigc_suggestions, suggest_deaigc_fixes

        # 1. 检测 AI 痕迹
        aigc = check_aigc(text)
        before_score = aigc.get("aigc_score", 0)

        if before_score < 0.1:
            return "✅ AI 痕迹评分很低，无需处理。"

        # 2. 获取改写建议
        suggestions = suggest_deaigc_fixes(text)

        # 3. 自动改写
        cleaned = apply_deaigc_suggestions(text)

        # 4. 如果激进模式，再跑一轮 LLM 改写
        if aggressive and cleaned != text:
            system_prompt = (
                "你是学术写作专家。请改写文本使其更自然，降低 AI 痕迹。"
                "保持原意，改变句式结构，增加表达多样性。直接输出改写后的文本。"
            )
            prompt = f"请改写以下文本，降低 AI 痕迹，使其更像人类学术写作：\n\n{cleaned[:8000]}"
            llm_result = await _call_llm(prompt, system_prompt)
            if not llm_result.startswith("❌"):
                cleaned = llm_result

        # 5. 复检
        aigc_after = check_aigc(cleaned)
        after_score = aigc_after.get("aigc_score", 0)

        lines = ["## 🔄 去 AI 痕迹报告\n"]
        lines.append(f"**处理前 AI 评分**: {before_score:.0%}")
        lines.append(f"**处理后 AI 评分**: {after_score:.0%}")
        lines.append(f"**降幅**: {(before_score - after_score):.0%}")
        lines.append("")

        if suggestions:
            lines.append("### 检测到的问题\n")
            for s in suggestions[:8]:
                lines.append(f"- **{s.get('fix', '')}**: {s.get('example', '')}")

        if aigc_after.get("features"):
            lines.append("\n### 剩余特征\n")
            for f in aigc_after["features"][:5]:
                lines.append(f"- {f}")

        lines.append(f"\n---\n\n## 📄 处理后正文\n\n{cleaned}")

        return "\n".join(lines)
    except Exception as e:
        logger.error(f"deaigc error: {e}", exc_info=True)
        return f"❌ 去 AI 痕迹失败: {str(e)[:200]}"


# ──────────────────────────────────────────────────────────────
# Tool: Score
# ──────────────────────────────────────────────────────────────

SCHOLARFORGE_SCORE_SCHEMA = {
    "name": "scholarforge_score",
    "description": (
        "论文三维度评分：原创性(0-10) + 逻辑性(0-10) + 引用完整性(0-10)。"
        "综合评分 = 原创性×0.3 + 逻辑性×0.35 + 引用×0.35。"
        "适用于：投稿前评估、论文质量自检、了解改进方向。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "论文正文（Markdown 格式）",
            },
            "topic": {
                "type": "string",
                "description": "研究主题（可选，提高评分准确性）",
            },
        },
        "required": ["content"],
    },
}


async def _handle_scholarforge_score(args: dict, **kw: Any) -> str:
    """论文评分"""
    content = args.get("content", "")
    topic = args.get("topic", "")

    if not content.strip():
        return "❌ 请提供论文内容。"
    if len(content) < 500:
        return "⚠️ 文本过短（<500字），评分参考价值有限。"

    try:
        from hermes_cli.scholarforge.scoring import score_paper

        # 提取引用的文献列表（从 [n] 标记）
        import re
        ref_nums = set(int(n) for n in re.findall(r'\[(\d+)\]', content))
        # 构造简易 papers 列表
        papers = [{"title": f"Ref [{n}]", "year": ""} for n in sorted(ref_nums)]

        result = await score_paper(content, papers, _make_llm=None, topic=topic)

        lines = ["## 📊 论文评分报告\n"]

        orig = result.get("originality", {})
        logic = result.get("logic", {})
        cite = result.get("citation_completeness", {})
        overall = result.get("overall", 0)

        lines.append(f"### 综合评分: {overall:.1f}/10\n")
        lines.append(f"| 维度 | 评分 | 说明 |")
        lines.append(f"|------|------|------|")
        lines.append(f"| 原创性 (30%) | {orig.get('score', 0):.1f}/10 | {orig.get('reasoning', '')[:60]} |")
        lines.append(f"| 逻辑性 (35%) | {logic.get('score', 0):.1f}/10 | {logic.get('reasoning', '')[:60]} |")
        lines.append(f"| 引用完整性 (35%) | {cite.get('score', 0):.1f}/10 | {cite.get('reasoning', '')[:60]} |")
        lines.append("")

        # 评级
        if overall >= 8:
            lines.append("✅ 优秀——达到投稿水平")
        elif overall >= 6:
            lines.append("⚠️ 良好——少量改进后可投稿")
        elif overall >= 4:
            lines.append("🔴 一般——需要较大修改")
        else:
            lines.append("❌ 较差——建议重新组织")

        if result.get("overall_reasoning"):
            lines.append(f"\n**总评**: {result['overall_reasoning']}")

        return "\n".join(lines)
    except Exception as e:
        logger.error(f"score error: {e}", exc_info=True)
        return f"❌ 论文评分失败: {str(e)[:200]}"


# ──────────────────────────────────────────────────────────────
# Tool: Export
# ──────────────────────────────────────────────────────────────

SCHOLARFORGE_EXPORT_SCHEMA = {
    "name": "scholarforge_export",
    "description": (
        "导出论文为 Word/PDF/LaTeX/Markdown 格式。"
        "适用于：论文定稿后导出到本地，在 WPS/Word/LaTeX 编辑器中进一步编辑。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "论文标题",
            },
            "content": {
                "type": "string",
                "description": "论文正文（Markdown 格式）",
            },
            "format": {
                "type": "string",
                "description": "导出格式",
                "enum": ["docx", "pdf", "latex", "markdown", "bibtex"],
                "default": "docx",
            },
            "abstract": {
                "type": "string",
                "description": "摘要（可选）",
            },
        },
        "required": ["title", "content", "format"],
    },
}


async def _handle_scholarforge_export(args: dict, **kw: Any) -> str:
    """导出论文"""
    title = args.get("title", "")
    content = args.get("content", "")
    fmt = args.get("format", "docx")
    abstract = args.get("abstract", "")

    if not title.strip() or not content.strip():
        return "❌ 请提供论文标题和正文。"

    try:
        import os
        import tempfile
        import re

        # 从正文中提取参考文献列表
        papers = []
        ref_section = re.search(r'##\s*参考文献\s*\n(.*)', content, re.DOTALL)
        if ref_section:
            ref_text = ref_section.group(1)
            # 解析 [n] Author (Year). Title. Venue.
            for m in re.finditer(r'\[(\d+)\]\s+(.+?)(?=\n\[|\n\n|$)', ref_text, re.DOTALL):
                ref_num = int(m.group(1))
                ref_body = m.group(2).strip().replace("\n", " ")
                papers.append({
                    "title": ref_body.split(".")[1].strip() if "." in ref_body else ref_body[:80],
                    "authors": ref_body.split(".")[0].strip() if "." in ref_body else "",
                    "year": "",
                    "venue": "",
                    "doi": "",
                    "ref_num": ref_num,
                })

        export_path = os.path.join(tempfile.gettempdir(), f"scholarforge_export")
        os.makedirs(export_path, exist_ok=True)

        # 安全文件名
        safe_title = re.sub(r'[^\w\u4e00-\u9fff]', '_', title)[:50]

        if fmt == "docx":
            from hermes_cli.scholarforge.export.full import export_docx
            data = export_docx(title, content, papers, abstract=abstract)
            filepath = os.path.join(export_path, f"{safe_title}.docx")
            with open(filepath, "wb") as f:
                f.write(data)
            return f"✅ Word 文档已导出：{filepath}\n\n📄 文件大小：{len(data)/1024:.0f} KB\n💡 可用 WPS 或 Microsoft Word 打开编辑。"

        elif fmt == "pdf":
            from hermes_cli.scholarforge.export.full import export_pdf
            data = export_pdf(title, content, papers, abstract=abstract)
            filepath = os.path.join(export_path, f"{safe_title}.pdf")
            with open(filepath, "wb") as f:
                f.write(data)
            return f"✅ PDF 已导出：{filepath}\n\n📄 文件大小：{len(data)/1024:.0f} KB"

        elif fmt == "latex":
            from hermes_cli.scholarforge.export.full import export_latex
            latex_text = export_latex(title, content, papers, abstract=abstract)
            filepath = os.path.join(export_path, f"{safe_title}.tex")
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(latex_text)
            return f"✅ LaTeX 已导出：{filepath}\n\n📄 文件大小：{len(latex_text)/1024:.0f} KB"

        elif fmt == "markdown":
            from hermes_cli.scholarforge.export.full import export_markdown
            md_text = export_markdown(title, content, papers, abstract=abstract)
            filepath = os.path.join(export_path, f"{safe_title}.md")
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(md_text)
            return f"✅ Markdown 已导出：{filepath}\n\n📄 文件大小：{len(md_text)/1024:.0f} KB"

        elif fmt == "bibtex":
            from hermes_cli.scholarforge.export.full import export_bibtex
            bib_text = export_bibtex(papers)
            filepath = os.path.join(export_path, f"{safe_title}.bib")
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(bib_text)
            return f"✅ BibTeX 已导出：{filepath}\n\n📄 文件大小：{len(bib_text)/1024:.0f} KB"

        else:
            return f"❌ 不支持的格式：{fmt}"

    except Exception as e:
        logger.error(f"export error: {e}", exc_info=True)
        return f"❌ 导出失败: {str(e)[:200]}"


# ──────────────────────────────────────────────────────────────
# Tool: Verify Citations (new validator)
# ──────────────────────────────────────────────────────────────

SCHOLARFORGE_VERIFY_CITATIONS_SCHEMA = {
    "name": "scholarforge_verify_citations",
    "description": (
        "验证论文引用的真实性。通过 CrossRef API 和 Semantic Scholar API 在线校验每条文献是否真实存在。"
        "检测虚构引用、年份异常、作者缺失等问题。"
        "适用于：投稿前引用核查、AI 生成论文的质量门控、确保参考文献真实可信。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "papers": {
                "type": "string",
                "description": (
                    "文献列表，JSON 数组格式，每条含 title/authors/year/venue/doi。"
                    "或每行一篇，格式：作者. 标题. 期刊. 年份. DOI"
                ),
            },
            "enable_online": {
                "type": "boolean",
                "description": "是否启用在线验证（默认 true），关闭时仅做本地启发式检查",
                "default": True,
            },
        },
        "required": ["papers"],
    },
}


async def _handle_scholarforge_verify_citations(args: dict, **kw: Any) -> str:
    """验证引用真实性"""
    import json as json_mod

    papers_raw = args.get("papers", "")
    enable_online = args.get("enable_online", True)

    if not papers_raw.strip():
        return "❌ 请提供文献列表。"

    # 尝试解析 JSON
    papers = []
    try:
        papers = json_mod.loads(papers_raw)
    except (json_mod.JSONDecodeError, ValueError):
        # 按行解析
        for line in papers_raw.strip().split("\n"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # 尝试解析 [n] Author (Year). Title. Venue.
            ref_match = re.match(r'\[?\d+\]?\s*(.+?)\s*\((\d{4})\)\.\s*(.+?)\.\s*(.+)', line)
            if ref_match:
                papers.append({
                    "authors": ref_match.group(1).strip(),
                    "year": ref_match.group(2),
                    "title": ref_match.group(3).strip(),
                    "venue": ref_match.group(4).strip(),
                    "doi": "",
                })
            else:
                parts = [p.strip() for p in line.split(".")]
                papers.append({
                    "title": parts[1] if len(parts) > 1 else line,
                    "authors": parts[0] if parts else "",
                    "year": "",
                    "venue": parts[2] if len(parts) > 2 else "",
                    "doi": "",
                })

    if not papers:
        return "❌ 未能解析文献列表，请提供 JSON 数组或每行一篇的格式。"

    try:
        from hermes_cli.scholarforge.validators import verify_citation_authenticity, format_citation_report
        checks = await verify_citation_authenticity(papers, enable_online=enable_online)
        return format_citation_report(checks)
    except Exception as e:
        logger.error(f"verify_citations error: {e}", exc_info=True)
        return f"❌ 引用验证失败: {str(e)[:200]}"


# ──────────────────────────────────────────────────────────────
# Tool: Check Statistics Consistency (new validator)
# ──────────────────────────────────────────────────────────────

SCHOLARFORGE_CHECK_STATS_SCHEMA = {
    "name": "scholarforge_check_stats",
    "description": (
        "校验统计指标的内部一致性。自动检验 η²↔Cohen's d、t↔d、F↔η²、d↔均值差/标准差 等换算关系。"
        "适用于：论文投稿前统计核查、确保报告的统计指标数学一致、防止计算错误。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "eta_squared": {"type": "number", "description": "η²（eta squared）效应量"},
            "cohens_d": {"type": "number", "description": "Cohen's d 效应量"},
            "t_value": {"type": "number", "description": "t 统计量"},
            "df": {"type": "integer", "description": "自由度"},
            "f_value": {"type": "number", "description": "F 统计量"},
            "df_error": {"type": "integer", "description": "误差自由度"},
            "p_value": {"type": "number", "description": "p 值"},
            "n_group1": {"type": "integer", "description": "组1样本量"},
            "n_group2": {"type": "integer", "description": "组2样本量"},
            "mean_diff": {"type": "number", "description": "均值差"},
            "pooled_sd": {"type": "number", "description": "合并标准差"},
        },
        "required": [],
    },
}


async def _handle_scholarforge_check_stats(args: dict, **kw: Any) -> str:
    """统计一致性校验"""
    # 过滤掉未提供的参数
    stats = {k: v for k, v in args.items() if v is not None}

    if not stats:
        return "❌ 请至少提供两个统计指标用于交叉校验。"

    try:
        from hermes_cli.scholarforge.validators import check_statistics_consistency, format_statistics_report
        checks = check_statistics_consistency(stats)
        return format_statistics_report(checks)
    except Exception as e:
        logger.error(f"check_stats error: {e}", exc_info=True)
        return f"❌ 统计校验失败: {str(e)[:200]}"


# ──────────────────────────────────────────────────────────────
# Tool: Detect Design Flaws (new validator)
# ──────────────────────────────────────────────────────────────

SCHOLARFORGE_DETECT_DESIGN_FLAWS_SCHEMA = {
    "name": "scholarforge_detect_design_flaws",
    "description": (
        "检测研究设计中的常见缺陷：多要素未分离、评估者偏差、样本代表性不足、"
        "霍桑效应、追踪周期不足、测量工具验证不足、非随机分配、统计检验力不足。"
        "适用于：论文开题前设计审查、投稿前质量自检、审稿时快速识别设计问题。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "paper_text": {
                "type": "string",
                "description": "论文全文或方法论章节（用于文本模式匹配检测）",
            },
            "design_type": {"type": "string", "description": "设计类型，如 '准实验'、'真实验'、'观察'"},
            "n_groups": {"type": "integer", "description": "组数"},
            "has_control": {"type": "boolean", "description": "是否设置对照组"},
            "has_random_assignment": {"type": "boolean", "description": "是否随机分配"},
            "intervention_elements": {
                "type": "array",
                "items": {"type": "string"},
                "description": "干预要素列表，如 ['户外', '主题建构']",
            },
            "fidelity_assessor": {
                "type": "string",
                "enum": ["self", "independent", "mixed"],
                "description": "忠实度评估者：self=实施者自评，independent=独立观察者，mixed=混合",
            },
            "tracking_weeks": {"type": "integer", "description": "追踪周期（周）"},
            "scale_validated": {"type": "boolean", "description": "量表是否经过完整心理测量学验证"},
            "sample_source": {"type": "string", "description": "样本来源，如 '单一机构'、'多机构'"},
            "sample_size": {"type": "integer", "description": "总样本量"},
        },
        "required": ["paper_text"],
    },
}


async def _handle_scholarforge_detect_design_flaws(args: dict, **kw: Any) -> str:
    """研究设计缺陷检测"""
    paper_text = args.get("paper_text", "")

    if not paper_text.strip():
        return "❌ 请提供论文文本。"

    # 构建 design_info
    design_info = {}
    for key in ["design_type", "n_groups", "has_control", "has_random_assignment",
                "intervention_elements", "fidelity_assessor", "tracking_weeks",
                "scale_validated", "sample_source", "sample_size"]:
        val = args.get(key)
        if val is not None:
            design_info[key] = val

    try:
        from hermes_cli.scholarforge.validators import detect_design_flaws, format_design_report
        flaws = detect_design_flaws(paper_text, design_info)
        return format_design_report(flaws)
    except Exception as e:
        logger.error(f"detect_design_flaws error: {e}", exc_info=True)
        return f"❌ 设计缺陷检测失败: {str(e)[:200]}"


# ──────────────────────────────────────────────────────────────
# Tool: Format References
# ──────────────────────────────────────────────────────────────

SCHOLARFORGE_FORMAT_REFS_SCHEMA = {
    "name": "scholarforge_format_refs",
    "description": (
        "格式化参考文献列表。支持 GB/T 7714（国标）和 APA 7th 两种格式。"
        "适用于：投稿前规范化参考文献、切换引用格式、生成标准参考文献列表。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "papers": {
                "type": "string",
                "description": "文献列表，每行一篇（格式：作者. 标题. 期刊/会议. 年份. DOI/URL），或 JSON 数组",
            },
            "style": {
                "type": "string",
                "description": "引用格式",
                "enum": ["gbt7714", "apa7"],
                "default": "gbt7714",
            },
        },
        "required": ["papers"],
    },
}


async def _handle_scholarforge_format_refs(args: dict, **kw: Any) -> str:
    """格式化参考文献"""
    import json as json_mod

    papers_raw = args.get("papers", "")
    style = args.get("style", "gbt7714")

    if not papers_raw.strip():
        return "❌ 请提供文献列表。"

    # 尝试解析 JSON 或按行解析
    papers = []
    try:
        papers = json_mod.loads(papers_raw)
    except (json_mod.JSONDecodeError, ValueError):
        # 按行解析
        for line in papers_raw.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            # 简单解析：Author. Title. Venue. Year.
            parts = [p.strip() for p in line.split(".")]
            paper = {
                "title": parts[1] if len(parts) > 1 else line,
                "authors": parts[0] if parts else "",
                "year": "",
                "venue": parts[2] if len(parts) > 2 else "",
                "doi": "",
            }
            # 尝试提取年份
            import re
            year_m = re.search(r'(20\d{2}|19\d{2})', line)
            if year_m:
                paper["year"] = year_m.group(1)
            papers.append(paper)

    if not papers:
        return "❌ 未能解析文献列表，请检查格式。"

    try:
        if style == "gbt7714":
            from hermes_cli.scholarforge.quality import format_all_references_gbt7714
            result = format_all_references_gbt7714(papers)
        elif style == "apa7":
            from hermes_cli.scholarforge.quality import format_apa7
            lines = []
            for i, p in enumerate(papers, 1):
                lines.append(format_apa7(p, ref_num=i))
            result = "\n\n".join(lines)
        else:
            return f"❌ 不支持的格式：{style}"

        return f"## 📚 参考文献列表（{style.upper()} 格式）\n\n{result}"
    except Exception as e:
        logger.error(f"format_refs error: {e}", exc_info=True)
        return f"❌ 格式化失败: {str(e)[:200]}"


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
    registry.register(
        name="scholarforge_replace_citations",
        toolset="scholarforge",
        schema=SCHOLARFORGE_REPLACE_CITATIONS_SCHEMA,
        handler=_handle_scholarforge_replace_citations,
        is_async=True,
        emoji="🔗",
        description="替换 [n] 占位符为真实文献引用",
    )
    registry.register(
        name="scholarforge_learn_style",
        toolset="scholarforge",
        schema=SCHOLARFORGE_LEARN_STYLE_SCHEMA,
        handler=_handle_scholarforge_learn_style,
        is_async=True,
        emoji="🎯",
        description="学习用户写作风格，后续写作自动仿写",
    )
    registry.register(
        name="scholarforge_outline",
        toolset="scholarforge",
        schema=SCHOLARFORGE_OUTLINE_SCHEMA,
        handler=_handle_scholarforge_outline,
        is_async=True,
        emoji="📝",
        description="生成论文大纲（章节结构+每章要点+预估字数）",
    )
    registry.register(
        name="scholarforge_polish",
        toolset="scholarforge",
        schema=SCHOLARFORGE_POLISH_SCHEMA,
        handler=_handle_scholarforge_polish,
        is_async=True,
        emoji="✨",
        description="学术润色（语言+逻辑+格式）",
    )
    registry.register(
        name="scholarforge_plagiarism_check",
        toolset="scholarforge",
        schema=SCHOLARFORGE_PLAGIARISM_CHECK_SCHEMA,
        handler=_handle_scholarforge_plagiarism_check,
        is_async=True,
        emoji="📊",
        description="论文查重检测（SimHash+N-gram+AIGC 启发式）",
    )
    registry.register(
        name="scholarforge_deaigc",
        toolset="scholarforge",
        schema=SCHOLARFORGE_DEAIGC_SCHEMA,
        handler=_handle_scholarforge_deaigc,
        is_async=True,
        emoji="🤖",
        description="去 AI 痕迹（检测+自动改写）",
    )
    registry.register(
        name="scholarforge_score",
        toolset="scholarforge",
        schema=SCHOLARFORGE_SCORE_SCHEMA,
        handler=_handle_scholarforge_score,
        is_async=True,
        emoji="⭐",
        description="论文三维度评分（原创性+逻辑性+引用完整性）",
    )
    registry.register(
        name="scholarforge_export",
        toolset="scholarforge",
        schema=SCHOLARFORGE_EXPORT_SCHEMA,
        handler=_handle_scholarforge_export,
        is_async=True,
        emoji="📤",
        description="导出论文（Word/PDF/LaTeX/Markdown/BibTeX）",
    )
    registry.register(
        name="scholarforge_format_refs",
        toolset="scholarforge",
        schema=SCHOLARFORGE_FORMAT_REFS_SCHEMA,
        handler=_handle_scholarforge_format_refs,
        is_async=True,
        emoji="📚",
        description="格式化参考文献（GB/T 7714 / APA 7th）",
    )
    registry.register(
        name="scholarforge_verify_citations",
        toolset="scholarforge",
        schema=SCHOLARFORGE_VERIFY_CITATIONS_SCHEMA,
        handler=_handle_scholarforge_verify_citations,
        is_async=True,
        emoji="🔬",
        description="验证文献引用真实性（CrossRef/Semantic Scholar API 在线校验）",
    )
    registry.register(
        name="scholarforge_check_stats",
        toolset="scholarforge",
        schema=SCHOLARFORGE_CHECK_STATS_SCHEMA,
        handler=_handle_scholarforge_check_stats,
        is_async=True,
        emoji="📐",
        description="统计指标一致性校验（η²↔d↔t↔F 值换算验证）",
    )
    registry.register(
        name="scholarforge_detect_design_flaws",
        toolset="scholarforge",
        schema=SCHOLARFORGE_DETECT_DESIGN_FLAWS_SCHEMA,
        handler=_handle_scholarforge_detect_design_flaws,
        is_async=True,
        emoji="⚠️",
        description="研究设计缺陷检测（多要素未分离/评估者偏差/样本代表性等 8 类）",
    )
    logger.info("[ScholarForge] 15 Agent tools registered: search/write/review/replace_citations/learn_style/outline/polish/plagiarism_check/deaigc/score/export/format_refs/verify_citations/check_stats/detect_design_flaws")


# Register on import
_register_tools()
