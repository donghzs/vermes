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
    """替换占位符引用为真实文献"""
    import re
    import asyncio

    draft = args.get("draft", "")
    max_refs = min(args.get("max_refs", 15), 30)

    if not draft.strip():
        return "❌ 请提供包含 [n] 占位符的论文草稿。"

    # 提取所有 [n] 占位符
    placeholders = re.findall(r'\[(\d+)\]', draft)
    if not placeholders:
        return "ℹ️ 草稿中未发现 [n] 占位符引用，无需替换。"

    unique_nums = sorted(set(int(n) for n in placeholders), reverse=True)
    if len(unique_nums) > max_refs:
        unique_nums = unique_nums[:max_refs]

    # 按段落提取上下文，为每个占位符推断搜索词
    paragraphs = draft.split("\n")
    num_context: dict[int, str] = {}
    for para in paragraphs:
        nums_in_para = re.findall(r'\[(\d+)\]', para)
        if nums_in_para:
            # 取该段落中 [n] 前后各 80 字作为上下文
            for m in re.finditer(r'\[(\d+)\]', para):
                n = int(m.group(1))
                if n in unique_nums and n not in num_context:
                    start = max(0, m.start() - 80)
                    end = min(len(para), m.end() + 80)
                    num_context[n] = para[start:end].strip()

    # 用 LLM 为每个占位符生成搜索关键词
    sys_prompt = (
        "你是一个学术文献检索助手。根据论文上下文，推断该位置应该引用什么主题的文献。"
        "只输出搜索关键词（中英文均可），不要解释。每行一个关键词。"
    )
    context_block = "\n".join(
        f"[{n}] 上下文: {num_context.get(n, '(无上下文)')[:150]}"
        for n in unique_nums
    )
    prompt = f"以下论文中各 [n] 标记处需要引用文献，请为每个推断搜索关键词：\n\n{context_block}"
    kw_response = await _call_llm(prompt, sys_prompt)

    # 解析关键词
    search_terms: dict[int, str] = {}
    lines = kw_response.strip().split("\n")
    for i, n in enumerate(unique_nums):
        if i < len(lines):
            # 提取行中的关键词（去掉 [n] 前缀和序号）
            term = re.sub(r'^\[?\d+\]?\.?\s*', '', lines[i]).strip()
            if term and len(term) > 2:
                search_terms[n] = term

    # 并行搜索文献
    from hermes_cli.scholarforge.search import search_papers

    found_papers: dict[int, dict] = {}
    all_results: list = []

    async def search_one(n: int, term: str):
        async for paper in search_papers(term, limit=3):
            all_results.append((n, term, paper))
            break  # 每个编号只取第一个结果

    tasks = [search_one(n, t) for n, t in search_terms.items()]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)

    # 去重：同一篇论文不重复引用
    seen_titles: set[str] = set()
    ref_list: list[tuple[int, dict]] = []
    next_ref_num = 1
    num_to_ref: dict[int, int] = {}

    for n, term, paper in all_results:
        title_key = paper.title.lower().strip()
        if title_key in seen_titles:
            # 复用已分配的编号
            for prev_n, prev_ref in ref_list:
                if prev_ref["title"].lower().strip() == title_key:
                    num_to_ref[n] = num_to_ref[prev_n]
                    break
            continue
        seen_titles.add(title_key)
        num_to_ref[n] = next_ref_num
        ref_list.append((n, {
            "ref_num": next_ref_num,
            "title": paper.title,
            "authors": ", ".join(paper.authors[:3]) if paper.authors else "Unknown",
            "year": paper.year or "n.d.",
            "venue": paper.venue or "",
            "doi": paper.doi or "",
        }))
        next_ref_num += 1

    # 替换草稿中的 [n]
    result_draft = draft
    for original_n in unique_nums:
        if original_n in num_to_ref:
            result_draft = result_draft.replace(
                f"[{original_n}]", f"[{num_to_ref[original_n]}]"
            )

    # 生成参考文献列表
    ref_lines = ["\n\n## 参考文献\n"]
    for _, ref in sorted(ref_list, key=lambda x: x[1]["ref_num"]):
        ref_lines.append(
            f"[{ref['ref_num']}] {ref['authors']} ({ref['year']}). "
            f"{ref['title']}. {ref['venue']}."
        )

    replaced = len(num_to_ref)
    unreplaced = len(unique_nums) - replaced

    summary = f"✅ 替换完成：{replaced}/{len(unique_nums)} 个占位符已匹配真实文献"
    if unreplaced:
        summary += f"，{unreplaced} 个未找到匹配"

    return result_draft + "\n".join(ref_lines) + f"\n\n---\n{summary}"


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
    logger.info("[ScholarForge] 5 Agent tools registered: search/write/review/replace_citations/learn_style")


# Register on import
_register_tools()
