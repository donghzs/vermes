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
import types
import httpx
from typing import Any

from tools.registry import registry

from .active_project import (
    PROJECT_ID_MISSING_MSG,
    get_active_project,
    resolve_project_id,
    set_active_project,
)

logger = logging.getLogger("scholarforge.tools")

# ── 流式回调 ContextVar ──
# Agent 调用 ScholarForge 工具时，conversation_loop 通过 contextvars
# 传播 stream delta callback，工具 handler 内部读取以实现逐 chunk 流式输出。
import contextvars as _cv
_stream_cb_var: _cv.ContextVar = _cv.ContextVar("_scholarforge_stream_cb", default=None)

def set_stream_callback(cb):
    """设置当前上下文的流式回调（由 conversation_loop 在工具执行前调用）。"""
    _stream_cb_var.set(cb)

def get_stream_callback():
    """获取当前上下文的流式回调（工具 handler 内部调用）。"""
    return _stream_cb_var.get()

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
            "project_id": {
                "type": "integer",
                "description": "论文项目 ID。指定后工具会自动加载该项目上下文（标题/大纲/已有章节/文献），结果自动写回项目库。",
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
            "section_key": {
                "type": "string",
                "description": (
                    "章节保存键，对应 outline 中的 section id（如 'intro', 'method', 'result'）。"
                    "如果已知大纲章节的 key，传此参数可确保写入内容与大纲对齐，"
                    "read_section 和 export 也能正确读取。不传则使用 section_type 作为 key。"
                ),
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
            "project_id": {
                "type": "integer",
                "description": "论文项目 ID。指定后工具会自动加载该项目上下文（标题/大纲/已有章节/文献），结果自动写回项目库。",
            },
            "quality_gate": {
                "type": "string",
                "description": "质量护栏模式：off(仅AIGC+查重) | flag(默认，写回+报告) | block(P0缺陷拒绝写回)",
                "enum": ["off", "flag", "block"],
                "default": "flag",
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
            "project_id": {
                "type": "integer",
                "description": "论文项目 ID。指定后工具会自动加载该项目上下文（标题/大纲/已有章节/文献），结果自动写回项目库。",
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
    """获取主 LLM 凭证。优先用主链路 _get_chat_credentials，回退到直接读 config。

    统一逻辑：和 Vermes Agent 聊天用同一套凭证源，避免配置不一致。
    返回 dict(api_key, base_url, model, provider) 或 None。
    """
    # 优先：主链路凭证函数（和 Agent 聊天同一套）
    try:
        from vermes_cli.blueprints.chat import _get_chat_credentials
        base_url, api_key, default_model = _get_chat_credentials()
        if api_key and base_url:
            from vermes_constants import get_vermes_home
            import yaml as _yaml
            cfg_path = get_vermes_home() / "config.yaml"
            provider = ""
            if cfg_path.exists():
                with open(cfg_path, encoding="utf-8") as f:
                    provider = (_yaml.safe_load(f) or {}).get("model", {}).get("provider", "")
            return {
                "api_key": api_key,
                "base_url": base_url.rstrip("/"),
                "model": default_model,
                "provider": provider or "deepseek",
            }
    except Exception:
        pass

    # 回退：直接读 config.yaml + .env（PyInstaller 兼容场景）
    import yaml
    from vermes_constants import get_vermes_home

    home = get_vermes_home()
    cfg_path = home / "config.yaml"

    try:
        with open(cfg_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    except Exception:
        cfg = {}

    model_cfg = cfg.get("model", {})
    provider = model_cfg.get("provider", "").strip() or "agnes"
    default_model = model_cfg.get("default", "").strip() or "agnes-2.5-flash"
    base_url = model_cfg.get("base_url", "").strip()

    # 从 providers 配置中读取 api_key 和 base_url
    prov_cfg = cfg.get("providers", {}).get(provider, {})
    api_key = prov_cfg.get("api_key", "").strip()
    if not base_url:
        base_url = prov_cfg.get("base_url", "").strip()

    # 如果 config.yaml 中没有 api_key，从 .env 文件读取
    if not api_key:
        env_path = home / ".env"
        if env_path.exists():
            env_var_name = f"{provider.upper()}_API_KEY"
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith(f"{env_var_name}="):
                    api_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
            if not api_key:
                for line in env_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line.startswith("OPENAI_API_KEY="):
                        api_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                        break

    if not base_url:
        try:
            from vermes_cli.blueprints.chat import PROVIDERS
            base_url = (PROVIDERS.get(provider) or {}).get("base_url", "")
        except Exception:
            pass

    if not api_key or not base_url:
        return None

    return {
        "api_key": api_key,
        "base_url": base_url.rstrip("/"),
        "model": default_model,
        "provider": provider,
    }


class _LlmHttpError(Exception):
    """内部异常：区分可重试（5xx / 网络抖动）与不可重试（4xx）。"""

    def __init__(self, message: str, retryable: bool, http_code: int = 0):
        super().__init__(message)
        self.message = message
        self.retryable = retryable
        self.http_code = http_code


# ── httpx 异步客户端：按事件循环缓存 ──────────────────────────
# 服务端单 loop 复用连接池（keep-alive，摆脱 urllib 每次新建连接）；
# 测试 asyncio.run 每次新 loop 自动新建，避免 "Event loop is closed"。
_LLM_CLIENTS: dict[int, httpx.AsyncClient] = {}


def _get_llm_client() -> httpx.AsyncClient:
    loop = asyncio.get_running_loop()
    client = _LLM_CLIENTS.get(id(loop))
    if client is None or client.is_closed:
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(120.0),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
        )
        _LLM_CLIENTS[id(loop)] = client
    return client


async def _call_llm_request(url: str, body: dict, headers: dict) -> tuple[str, dict | None]:
    """真正的一次 HTTP 请求（httpx 异步，原生不阻塞事件循环）。

    返回 ``(content, usage)``：usage 是响应 JSON 的 ``usage`` 字段（token 统计字典），
    无则为 ``None``。此前 usage 被直接丢弃（G4a 黑洞）——现在上抛给 _call_llm 累加。

    抛 _LlmHttpError 携带 retryable 标志，供上层决定是否重试。
    """
    client = _get_llm_client()
    try:
        resp = await client.post(url, json=body, headers=headers)
    except (httpx.TransportError, httpx.TimeoutException, OSError) as e:
        # 网络层错误（连接失败/超时/DNS）：可重试
        raise _LlmHttpError(
            f"❌ LLM 网络错误: {type(e).__name__}: {str(e)[:200]}", retryable=True
        ) from e

    if resp.status_code >= 500:
        raise _LlmHttpError(
            f"❌ LLM 调用失败 (HTTP {resp.status_code}): {resp.text[:150]}",
            retryable=True,
            http_code=resp.status_code,
        )
    if resp.status_code >= 400:
        # 4xx 客户端错误（鉴权/参数）不可重试
        raise _LlmHttpError(
            f"❌ LLM 调用失败 (HTTP {resp.status_code}): {resp.text[:150]}",
            retryable=False,
            http_code=resp.status_code,
        )

    try:
        data = resp.json()
    except Exception:
        raise _LlmHttpError(f"❌ LLM 响应解析失败: {resp.text[:300]}", retryable=False)
    _usage = data.get("usage")  # token 统计字典，可能是 None
    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    if content:
        return content, _usage
    # 响应格式异常：不可重试（重发无意义）
    raise _LlmHttpError(
        f"❌ LLM 响应格式异常: {json.dumps(data, ensure_ascii=False)[:300]}",
        retryable=False,
    )


# 可选：分析类任务用轻模型（成本降一个数量级）。未设置则沿用默认模型。
ANALYSIS_MODEL = os.environ.get("SCHOLARFORGE_ANALYSIS_MODEL")


async def _call_llm(
    prompt: str,
    system: str = "",
    *,
    temperature: float = 0.7,
    model: str | None = None,
    json_mode: bool = False,
    max_tokens: int = 8192,
) -> str:
    """Call LLM with auto-detected credentials.

    改用 httpx 异步客户端（原生 asyncio，不再经 asyncio.to_thread 卸载到线程池，
    避免线程池上限对并发的隐性钳制）。可重试错误（5xx / 网络抖动）最多重试 3 次，
    退避 0.5s/1s；4xx 与格式异常不重试。

    新增参数（向后兼容，所有既有 await 调用方零改动，仅新增关键字参数）：
    - temperature: 写作 0.7；分析类任务（打分/查重/claim 提取/研究拆解）建议 0.2
    - model: 覆盖模型，用于简单任务走轻模型降本（分析站点传 ANALYSIS_MODEL）
    - json_mode: 设 response_format={"type":"json_object"}；需调用方同步改写
      prompt 与解析逻辑（本轮尚未接入既有工具，避免部分 provider 拒收 response_format
      且现有稳健正则解析无需变更）
    """
    creds = _resolve_credentials()
    if not creds:
        return "❌ 未找到已配置的 API Key。请在 Vermes 设置中添加至少一个 Provider 的 API Key。"

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    body = {
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    effective_model = model or creds["model"]
    if effective_model:
        body["model"] = effective_model

    url = f"{creds['base_url']}/chat/completions"
    headers = {
        "Authorization": f"Bearer {creds['api_key']}",
        "Content-Type": "application/json",
    }

    max_attempts = 3
    last_error = ""
    for attempt in range(1, max_attempts + 1):
        try:
            _content, _usage = await _call_llm_request(url, body, headers)
            # G4a：把本次调用的 token 用量累加进当前工具调用上下文（若存在）。
            # usage 为 None（无统计）时跳过；重试多次则累加而非覆盖。
            if _usage is not None:
                _accumulate_llm_usage(
                    _usage, creds["provider"], effective_model,
                    creds.get("base_url"), creds.get("api_key"),
                )
            return _content
        except _LlmHttpError as e:
            last_error = e.message
            if not e.retryable or attempt == max_attempts:
                if e.retryable:
                    logger.error(
                        f"LLM call failed after {max_attempts} attempts "
                        f"({creds['provider']}/{creds['model']}): {e.message}"
                    )
                else:
                    logger.error(
                        f"LLM call failed ({creds['provider']}/{creds['model']}): {e.message}"
                    )
                return e.message
            # 可重试：退避后再试（0.5s, 1s）
            backoff = 0.5 * attempt
            logger.warning(
                f"LLM call attempt {attempt}/{max_attempts} failed "
                f"({creds['provider']}/{creds['model']}): {e.message}; retrying in {backoff}s"
            )
            await asyncio.sleep(backoff)
        except Exception as e:  # noqa: BLE001 — 兜底，防止未预期异常泄漏
            logger.error(
                f"LLM unexpected error ({creds['provider']}/{creds['model']}): {e}",
                exc_info=True,
            )
            return f"❌ LLM 调用异常: {type(e).__name__}: {str(e)[:200]}"

    return last_error or "❌ LLM 调用失败：未知错误"


async def stream_call_llm(
    prompt: str,
    system: str = "",
    *,
    temperature: float = 0.7,
    model: str | None = None,
    max_tokens: int = 8192,
) -> "AsyncGenerator[str, None]":
    """流式调用 LLM，逐 chunk yield 文本增量。

    与 _call_llm 共享连接池与凭证解析，但 body 中 stream=True，
    逐行读取 SSE `data:` 行并提取 delta.content。
    用于 write/polish 等长文本生成场景，前端可实时渲染。
    """
    creds = _resolve_credentials()
    if not creds:
        yield "❌ 未找到已配置的 API Key。请在 Vermes 设置中添加至少一个 Provider 的 API Key。"
        return

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    body = {
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
        # F-21: 请求 stream_options 让 provider 在最后一个 chunk 返回 usage 统计，
        # 供 G4a token 记账。部分 provider 可能不支持（返回 400），
        # 调用方负责 fail-open 去掉此字段重试。
        "stream_options": {"include_usage": True},
    }
    effective_model = model or creds["model"]
    if effective_model:
        body["model"] = effective_model

    url = f"{creds['base_url']}/chat/completions"
    headers = {
        "Authorization": f"Bearer {creds['api_key']}",
        "Content-Type": "application/json",
    }

    client = _get_llm_client()
    try:
        async with client.stream("POST", url, json=body, headers=headers) as resp:
            if resp.status_code >= 400:
                error_text = await resp.aread()
                # F-21: 部分 provider 不支持 stream_options 返回 400，
                # 去掉 stream_options 重试一次（fail-open）
                if resp.status_code == 400 and "stream_options" in body:
                    logger.warning("LLM stream 400 with stream_options, retrying without")
                    body.pop("stream_options", None)
                    async with client.stream("POST", url, json=body, headers=headers) as resp2:
                        if resp2.status_code >= 400:
                            err2 = await resp2.aread()
                            logger.error(f"LLM stream retry failed (HTTP {resp2.status_code}): {err2[:200]}")
                            yield f"❌ LLM 流式调用失败 (HTTP {resp2.status_code})"
                            return
                        async for line in resp2.aiter_lines():
                            if not line.startswith("data: "):
                                continue
                            payload = line[6:]
                            if payload.strip() == "[DONE]":
                                break
                            try:
                                chunk = json.loads(payload)
                                delta = chunk.get("choices", [{}])[0].get("delta", {})
                                text = delta.get("content", "")
                                if text:
                                    yield text
                            except (json.JSONDecodeError, IndexError, KeyError):
                                continue
                    return
                logger.error(f"LLM stream failed (HTTP {resp.status_code}): {error_text[:200]}")
                yield f"❌ LLM 流式调用失败 (HTTP {resp.status_code})"
                return

            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload.strip() == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                    # F-21: 捕获最后一个 chunk 的 usage 统计（stream_options.include_usage）
                    chunk_usage = chunk.get("usage")
                    if chunk_usage:
                        try:
                            _accumulate_llm_usage(
                                chunk_usage, creds["provider"], effective_model,
                                creds.get("base_url"), creds.get("api_key"),
                            )
                        except Exception:
                            pass  # fail-open: 记账失败不影响流式输出
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    text = delta.get("content", "")
                    if text:
                        yield text
                except (json.JSONDecodeError, IndexError, KeyError):
                    continue
    except (httpx.TransportError, httpx.TimeoutException, OSError) as e:
        logger.error(f"LLM stream network error: {e}")
        yield f"❌ LLM 流式网络错误: {type(e).__name__}: {str(e)[:200]}"
    except Exception as e:  # noqa: BLE001
        logger.error(f"LLM stream unexpected error: {e}", exc_info=True)
        yield f"❌ LLM 流式异常: {type(e).__name__}: {str(e)[:200]}"


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
        from vermes_cli.scholarforge.search import search_papers

        papers = []
        async for paper in search_papers(query, limit=limit):
            papers.append(paper)

        if not papers:
            return f"🔍 未找到与「{query}」相关的文献。建议：试试换用英文关键词，或调整搜索词。"

        # 结果写回项目 DB
        project_id = resolve_project_id(args)
        if project_id and papers:
            from vermes_cli.scholarforge.project_context import save_papers
            saved = save_papers(project_id, [p.to_dict() for p in papers])

        lines = [f"## 文献搜索结果: {query}"]
        lines.append(f"找到 {len(papers)} 篇文献：\n")
        if project_id and papers:
            lines.append(f"（已自动保存 {saved} 篇到项目 #{project_id}）\n")
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
    # section_key 优先：如果用户传了 section_key（对应 outline 中的 id），用它作为保存键
    # 这样 write 保存的 key 与 outline 一致，read/export 能正确读取
    section_key = args.get("section_key", "") or section_type
    context = args.get("context", "")
    paper_type = args.get("paper_type", "本科论文")
    # 解析 project_id：显式参数优先，否则回退到激活项目
    project_id = resolve_project_id(args)
    _missing_pid = not project_id  # 缺失时不静默成功：生成内容但明确标记未保存（ok=0）

    # 注入项目上下文
    project_ctx = ""
    if project_id:
        from vermes_cli.scholarforge.project_context import format_project_context_prompt, auto_snapshot
        # Phase 2: 写操作前自动创建快照
        auto_snapshot(project_id, label=f"write_{section_type}", note="自动快照：写操作前")
        project_ctx = format_project_context_prompt(project_id)
        if project_ctx:
            # 如果用户没传 topic，从项目信息中推断
            if not topic:
                from vermes_cli.scholarforge.project_context import load_project_context
                proj = load_project_context(project_id)
                if proj:
                    topic = proj.get("title", "")
            if not paper_type or paper_type == "本科论文":
                from vermes_cli.scholarforge.project_context import load_project_context
                proj = load_project_context(project_id)
                if proj and proj.get("paper_type"):
                    paper_type = proj["paper_type"]

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
    from vermes_cli.scholarforge.agents import get_paper_type_prompt
    prompt += get_paper_type_prompt(paper_type)
    if context:
        prompt += f"""

【已有上下文】
{context[:2000]}"""
    if project_ctx:
        prompt += f"""

【项目上下文】
{project_ctx}"""

    # 注入已学习的写作风格（learn_style 落库的 style_prompt），实现自动仿写
    if project_id:
        from vermes_cli.scholarforge.project_context import get_style_prompt
        style_prompt = get_style_prompt(project_id)
        if style_prompt:
            prompt += f"""

【写作风格要求（请严格模仿）】
{style_prompt}"""

    prompt += f"""

请直接输出该章节的完整内容（Markdown 格式，{label} 用 ## 标记），
引用文献时使用 [n] 标记（n为编号占位，用户后续会替换为真实文献）。"""

    # 流式回调：优先从 kw 获取，退退从 contextvar 获取（Agent 调用链路）
    stream_cb = kw.get("stream_callback") or get_stream_callback()
    if stream_cb and callable(stream_cb):
        parts: list[str] = []
        async for chunk in stream_call_llm(prompt, system_prompt):
            stream_cb(chunk)
            parts.append(chunk)
        content = "".join(parts)
    else:
        content = await _call_llm(prompt, system_prompt)

    # ── 质量护栏前移：写回闸门 ────────────────────────
    quality_gate_mode = args.get("quality_gate", "flag")
    gate_report = ""

    if project_id and content and not content.startswith("❌"):
        from vermes_cli.scholarforge.quality_gate import run_quality_gate
        content, gate_report, blocked = run_quality_gate(
            project_id, section_type, content, mode=quality_gate_mode, stage="write",
        )
        if not blocked:
            from vermes_cli.scholarforge.project_context import save_section
            _save_ok = save_section(project_id, section_key, content)
            if not _save_ok:
                logger.error("scholarforge_write: save_section failed for project_id=%s section_key=%s", project_id, section_key)
                return f"❌ 章节写回失败：内容已生成，但数据库回读校验未通过（project_id={project_id}, section_key={section_key}）。可能原因：写入未生效、内容被截断或内容为空。请检查后重试，勿假定已保存。\n\n---\n\n{content}"
        else:
            return f"🚫 质量闸门拦截（mode=block）：检测到 P0 级严重问题，已拒绝写回。\n\n---\n\n{gate_report}\n\n---\n\n请根据报告修改后重新提交。"

    if _missing_pid:
        # project_id 缺失：内容已生成但明确标记未保存（_with_usage 会因 ❌ 前缀记 ok=0）
        warn = ("❌ 未关联 project_id：内容已生成但未写回任何项目。请调用 "
                "scholarforge_set_active_project 设置激活项目，或在调用时显式传入 project_id"
                "（如 project_id=52）。")
        if gate_report:
            return f"{warn}\n\n{content}\n\n---\n\n{gate_report}"
        return f"{warn}\n\n{content}"

    if gate_report:
        return f"{content}\n\n---\n\n{gate_report}"
    return content


async def _handle_scholarforge_review(args: dict, **kw: Any) -> str:
    """审阅论文"""
    draft = args.get("draft", "")
    project_id = resolve_project_id(args)
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

    # 注入项目上下文（此前该注入写在 prompt 定义之前，会 UnboundLocalError 崩溃且从未生效）
    if project_id:
        from vermes_cli.scholarforge.project_context import format_project_context_prompt as _fpc
        _pc = _fpc(project_id)
        if _pc:
            prompt += f"\n\n【项目上下文】\n{_pc}"

    llm_result = await _call_llm(prompt, system_prompt, temperature=0.2, model=ANALYSIS_MODEL)

    # ── De-AIGC 校准建议 ─────────────────────────────────────
    deaigc_section = ""
    try:
        from vermes_cli.scholarforge.plagcheck import check_aigc, suggest_deaigc_fixes
        aigc = check_aigc(draft)
        suggestions = suggest_deaigc_fixes(draft)
        if suggestions or aigc.get("verdict"):
            deaigc_section = "\n\n---\n## ✍️ AI 写作特征提示（启发式）\n"
            deaigc_section += f"**机械化特征指数**: {aigc.get('aigc_score', 0)*100:.0f}/100（风格度量，非 AI 生成概率）"
            if aigc.get("verdict"):
                deaigc_section += f"\n**判断**: {aigc['verdict']}"
            if aigc.get("features"):
                deaigc_section += "\n\n**检测到的机械化特征**:\n"
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
        "流程：LLM 从上下文提取被引主张(claim)生成检索式 → 优先匹配项目本地文献库 → "
        "再联网检索学术数据库 → LLM 精排选最佳匹配 → 生成参考文献列表。"
        "指定 project_id 时新文献自动写回项目文献库（按 DOI/标题去重）。"
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
            "project_id": {
                "type": "integer",
                "description": "论文项目 ID。指定后工具会自动加载该项目上下文（标题/大纲/已有章节/文献），结果自动写回项目库。",
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
            "project_id": {
                "type": "integer",
                "description": "论文项目 ID。指定后工具会自动加载该项目上下文（标题/大纲/已有章节/文献），结果自动写回项目库。",
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

    # 解析 project_id：显式参数优先，否则回退到激活项目；缺失不静默成功
    project_id = resolve_project_id(args)
    _missing_pid = not project_id  # 缺失时不静默成功：仍生成替换结果但明确标记未写回（ok=0）
    draft = args.get("draft", "")
    max_refs = min(args.get("max_refs", 15), 30)

    if not draft.strip():
        return "❌ 请提供包含 [n] 占位符的论文草稿。"

    # Phase 2: 引用替换前自动创建快照
    if project_id:
        from vermes_cli.scholarforge.project_context import auto_snapshot
        auto_snapshot(project_id, label="replace_citations_pre", note="自动快照：引用替换前")

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

    # ── 修复1: 混合关键词提取（LLM 辅助 + 正则兜底）──
    # 先用 LLM 从上下文提取 2-3 个学术搜索词（高精度），
    # 失败时回退到正则提取（低精度但零延迟）
    def extract_keywords(text: str) -> str:
        """从上下文文本提取搜索关键词（纯正则，兜底方案）"""
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

    async def extract_keywords_llm(context: str) -> str | None:
        """用 LLM 从上下文提取精准学术搜索词（2-3 个短语）"""
        try:
            prompt = (
                f"从以下论文引用上下文提取 2-3 个用于学术搜索的关键短语（英文/中文均可）。"
                f"只返回空格分隔的短语，不要解释：\n\n{context[:500]}"
            )
            result = await _call_llm(prompt, temperature=0.2, model=ANALYSIS_MODEL)
            if result and not result.startswith("❌"):
                # 取第一行，清洗掉引号和多余字符
                kw = result.strip().split("\n")[0].strip('"\'。，, ')
                if kw and len(kw) >= 3:
                    return kw[:120]
        except Exception:
            pass
        return None

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

    # ── Phase 2: claim→检索闭环 ─────────────────────────────
    # 批量用 LLM 从每个占位符上下文提取「被引主张(claim)对应的检索式」，
    # 一次 LLM 调用覆盖全部占位符（而非仅在关键词碰撞时才触发）。
    # LLM 失败时保留正则关键词兜底，流程不中断。
    if num_context:
        try:
            items = sorted(num_context.items())
            numbered_ctx = "\n".join(f"{n}. {ctx[:200]}" for n, ctx in items)
            claim_prompt = (
                "以下是论文草稿中若干引用占位符的上下文片段（编号. 片段）。\n"
                "对每个编号，先理解该处被引用支撑的核心主张(claim)，"
                "再给出一条用于学术数据库检索的查询式（2-4 个关键短语，中英文均可，"
                "优先使用领域术语与专有名词）。\n"
                "严格按 '编号: 查询式' 逐行返回，不要解释，不要多余内容。\n\n"
                f"{numbered_ctx}"
            )
            claim_result = await _call_llm(claim_prompt, temperature=0.2, model=ANALYSIS_MODEL)
            if claim_result and not claim_result.startswith("❌"):
                for line in claim_result.strip().split("\n"):
                    lm = re.match(r'^\s*\[?(\d+)\]?\s*[:：.]\s*(.+)$', line.strip())
                    if lm:
                        ln, lq = int(lm.group(1)), lm.group(2).strip().strip('"\'`')
                        if ln in num_keywords and 3 <= len(lq) <= 150:
                            num_keywords[ln] = lq
                logger.info("[ScholarForge] replace_citations: claim-based queries extracted via LLM")
        except Exception as e:
            logger.debug(f"claim extraction failed, fallback to regex keywords: {e}")

    # ── 修复2: 并行搜索 top-3 → 按相似度排序取最佳 ──
    from vermes_cli.scholarforge.search import search_papers, PaperResult

    # 存储每个编号的候选论文列表
    candidates: dict[int, list[PaperResult]] = {}

    # ── Phase 2: 项目本地文献库优先参与匹配 ──────────────
    # 用户已导入的文献（PDF 上传/手动添加）优先作为候选，与在线检索结果
    # 一起进入粗排+LLM 精排；本地命中则无需联网也能闭环。
    local_papers: list[PaperResult] = []
    if project_id:
        try:
            from vermes_cli.scholarforge.database import list_literature as _list_lit
            for lit in _list_lit(project_id)[:50]:
                local_papers.append(PaperResult(
                    paper_id=f"local_{lit['id']}",
                    title=lit.get("title") or "",
                    authors=lit.get("authors") or [],
                    year=str(lit.get("year") or ""),
                    venue=lit.get("venue") or "",
                    abstract=lit.get("abstract") or "",
                    url=lit.get("url") or "",
                    doi=lit.get("doi") or "",
                    source="local",
                ))
            if local_papers:
                logger.info(f"[ScholarForge] replace_citations: {len(local_papers)} local literatures loaded as candidates")
        except Exception as e:
            logger.debug(f"local literature load failed: {e}")

    async def search_one(n: int, keyword: str, force: bool = False):
        """搜索论文并存入 candidates。force=True 时覆盖已有候选（用于碰撞后重搜）。"""
        if not keyword:
            return
        if n in candidates and not force:
            return  # 已搜过且未要求覆盖
        papers = []
        async for paper in search_papers(keyword, limit=10):
            papers.append(paper)
            if len(papers) >= 8:
                break
        if papers:
            candidates[n] = papers

    tasks = [search_one(n, kw) for n, kw in num_keywords.items()]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)

    # ── 碰撞检测：多个占位符搜到同一篇论文时，用 LLM 增强关键词重新搜索 ──
    # 阈值 0.3 → 低于此分不选，但对同一篇低分但撞车的论文要区分
    collision_detected = False
    if len(num_keywords) >= 2:
        kw_values = [(n, kw) for n, kw in num_keywords.items() if kw]
        for i in range(len(kw_values)):
            for j in range(i + 1, len(kw_values)):
                n1, kw1 = kw_values[i]
                n2, kw2 = kw_values[j]
                # 关键词相似 → 可能碰撞
                if kw1 and kw2 and (
                    kw1.lower() == kw2.lower() or
                    max(len(kw1), len(kw2)) > 0 and sum(1 for w in kw1.split() if w.lower() in kw2.lower().split()) >= min(len(kw1.split()), len(kw2.split())) * 0.6
                ):
                    if not collision_detected:
                        logger.info(f"[ScholarForge] replace_citations: collision detected between [{n1}]({kw1[:30]}) and [{n2}]({kw2[:30]}), using LLM to refine")
                        collision_detected = True
                    # 用 LLM 重新提取更精准的关键词
                    for n in (n1, n2):
                        if n in num_context:
                            llm_kw = await extract_keywords_llm(num_context[n])
                            if llm_kw and llm_kw != num_keywords.get(n, ''):
                                num_keywords[n] = llm_kw
                                logger.info(f"  [{n}] refined: '{llm_kw[:60]}'")

    # 重新搜索被 LLM 改善过的关键词（force=True 覆盖旧候选）
    if collision_detected:
        tasks2 = [
            search_one(n, num_keywords[n], force=True)
            for n in num_keywords
            if n in num_context  # 有上下文的才重搜
        ]
        if tasks2:
            await asyncio.gather(*tasks2, return_exceptions=True)

    # 对每个编号，从候选中选最佳匹配
    # score_relevance / llm_rerank 已抽取为模块级函数（见文件上方），直接调用

    # ── F-25: 改用公共匹配管线（citation_matcher.match_citations）──
    # 旧代码将匹配逻辑内联，与 citation_provider 各自维护导致能力不对等。
    # 公共管线统一保证：0.3 阈值 + LLM 精排 + 去重 + 连续编号。
    from vermes_cli.scholarforge.citation_matcher import match_citations as _match
    _match_result = await _match(
        unique_nums=unique_nums,
        candidates=candidates,
        num_context=num_context,
        num_keywords=num_keywords,
        local_papers=local_papers,
    )
    num_to_ref = _match_result.num_to_ref
    ref_list = _match_result.ref_list
    match_log = _match_result.match_log
    failed = _match_result.failed

    # 日志输出
    for line in match_log:
        logger.info(f"[ScholarForge] {line}")

    # 替换草稿中的占位符（支持 [n] / [n-m] / [n,m,...]）
    # 修复 F-2/F-3: 旧代码用顺序 str.replace() 导致级联串号（[5]→[3] 后被 [3]→[8] 二次命中）
    # 和未匹配占位符与真引用撞号。改为单次正则回调替换，位置精确、不重扫。
    import re as _re
    def _sub_citation(_m: _re.Match) -> str:
        nums = expand_citation(_m.group(0))
        mapped = [num_to_ref.get(n) for n in nums]
        if all(r is not None for r in mapped):
            return f"[{','.join(str(r) for r in mapped)}]"
        # 未匹配的占位符显式标记，杜绝与真引用撞号
        return f"[?{_m.group(0)[1:-1]}]"
    result_draft = _re.sub(r'\[\d+(?:[-,]\d+)*\]', _sub_citation, draft)

    # ── 修复3: 替换后交叉验证 ──
    verify_report = ""
    if ref_list:
        try:
            from vermes_cli.scholarforge.citation_verifier import _fuzzy_verify
            # 构造完整 PaperResult 列表（修复 F-4: 之前每次循环只传 [_P()] 单元素，
            # 导致 _fuzzy_verify 的范围检查 ref_num > len(papers) 始终为 True，[2] 及以上全判 0/10）
            class _P:
                def __init__(self, ref):
                    self.title = ref["title"]
                    self.abstract = ""
                    self.year = ref["year"]
                    self.authors = ref["authors"].split(", ")
                    self.paper_id = f"ref_{ref['ref_num']}"
            all_papers = [_P(ref) for ref in ref_list]
            verify_results = []
            for ref in ref_list:
                result = _fuzzy_verify(ref["ref_num"], result_draft, all_papers)
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

    # ── 质量护栏：引用解析后闸门 ────────────────────────
    citation_gate_report = ""
    if ref_list:
        try:
            from vermes_cli.scholarforge.quality_gate import run_citation_gate
            citation_gate_report, _ = await run_citation_gate(ref_list, mode="flag")
        except Exception as e:
            logger.debug(f"citation gate failed: {e}")

    # ── Phase 2: 兑现 schema 承诺——新文献自动写回项目文献库 ──
    saved_count = 0
    if project_id and ref_list:
        try:
            from vermes_cli.scholarforge.database import add_literature as _add_lit, list_literature as _list_lit2
            existing = _list_lit2(project_id)
            existing_dois = {(l.get("doi") or "").lower() for l in existing if l.get("doi")}
            existing_titles = {(l.get("title") or "").lower().strip()[:80] for l in existing}
            for ref in ref_list:
                if ref.get("source") == "local":
                    continue  # 本来就在库里
                doi_key = (ref.get("doi") or "").lower()
                title_key = (ref.get("title") or "").lower().strip()[:80]
                if (doi_key and doi_key in existing_dois) or title_key in existing_titles:
                    continue
                year_val = None
                try:
                    year_val = int(str(ref.get("year", "")).strip()[:4])
                except (ValueError, TypeError):
                    pass
                _add_lit(
                    project_id,
                    title=ref["title"],
                    authors=[a.strip() for a in ref["authors"].split(",") if a.strip()],
                    year=year_val,
                    venue=ref.get("venue", ""),
                    abstract=ref.get("abstract", ""),
                    url=ref.get("url", ""),
                    doi=ref.get("doi", ""),
                )
                existing_titles.add(title_key)
                if doi_key:
                    existing_dois.add(doi_key)
                saved_count += 1
            if saved_count:
                logger.info(f"[ScholarForge] replace_citations: {saved_count} new literatures saved to project {project_id}")
        except Exception as e:
            logger.warning(f"literature write-back failed: {e}")

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
    if project_id and saved_count:
        report_lines.append(f"**📥 已写回项目文献库**: {saved_count} 篇新文献（按 DOI/标题去重）\n")

    if verify_report:
        report_lines.append(verify_report)

    if citation_gate_report:
        report_lines.append(f"\n---\n\n{citation_gate_report}")

    report_lines.append(f"\n---\n\n## 📄 处理后正文\n\n{result_draft}")
    report_lines.append("\n".join(ref_lines))

    logger.info(f"[ScholarForge] replace_citations: {replaced}/{len(unique_nums)} replaced")
    if _missing_pid:
        # project_id 缺失：引用已替换但未写回任何项目文献库（新文献未落库）。
        # 前置 ❌ 警告使 _with_usage 自动记 ok=0（不静默成功）。
        warn = ("❌ 未关联 project_id：引用已替换但未写回任何项目文献库（新文献未落库）。请调用 "
                "scholarforge_set_active_project 设置激活项目，或在调用时显式传入 project_id"
                "（如 project_id=52）。")
        return f"{warn}\n\n" + "\n".join(report_lines)
    return "\n".join(report_lines)


async def _handle_scholarforge_learn_style(args: dict, **kw: Any) -> str:
    """学习用户写作风格"""
    import re
    import statistics

    sample = args.get("sample_text", "")
    # 解析 project_id：显式参数优先，否则回退到激活项目；缺失则明确报错
    project_id = resolve_project_id(args)
    if not project_id:
        return PROJECT_ID_MISSING_MSG
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

    # ── 落库：写回 projects.style_prompt，供 write 自动仿写 ──
    # 此前 learn_style 只 return 风格提示词、从不落库，导致 write 永远读不到 →
    # schema 承诺的"后续 scholarforge_write 会自动仿写该风格"从未兑现（孤儿功能）。
    saved = False
    if project_id:
        from vermes_cli.scholarforge.project_context import save_style_profile
        saved = save_style_profile(project_id, style_prompt)

    if saved:
        persist_line = (
            f"✅ 已保存到项目 #{project_id}，后续对该项目调用 scholarforge_write "
            f"（传入相同 project_id）时会自动应用此风格。\n\n"
        )
    elif project_id:
        persist_line = (
            f"⚠️ 风格已提取，但写回项目 #{project_id} 失败（项目可能不存在），"
            f"本次风格不会被 write 自动复用。\n\n"
        )
    else:
        persist_line = (
            "💡 未指定 project_id，风格未落库。若希望 scholarforge_write 自动仿写，"
            "请在调用本工具时传入 project_id，并对同一项目写作。\n\n"
        )

    return (
        f"✅ 风格学习完成！已提取 8 维风格特征。\n\n"
        f"**风格摘要**: 句长{avg_sent_len:.0f}字、段落{'均匀' if para_cv < 0.3 else '变化'}、"
        f"术语密度{'高' if term_density > 3 else '中'}、过渡词{'多' if transition_density > 1.5 else '适中'}\n\n"
        f"{persist_line}"
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
            "project_id": {
                "type": "integer",
                "description": "论文项目 ID。指定后工具会自动加载该项目上下文（标题/大纲/已有章节/文献），结果自动写回项目库。",
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
    project_id = resolve_project_id(args)

    # 注入项目上下文
    project_ctx = ""
    if project_id:
        from vermes_cli.scholarforge.project_context import format_project_context_prompt, load_project_context, auto_snapshot
        # Phase 2: 大纲操作前自动创建快照
        auto_snapshot(project_id, label="outline_pre", note="自动快照：大纲生成前")
        project_ctx = format_project_context_prompt(project_id)
        if not topic:
            proj = load_project_context(project_id)
            if proj:
                topic = proj.get("title", "")
        if not paper_type or paper_type == "本科论文":
            proj = load_project_context(project_id)
            if proj and proj.get("paper_type"):
                paper_type = proj["paper_type"]

    if not topic.strip():
        return "❌ 请提供论文主题。"

    from vermes_cli.scholarforge.agents import get_paper_type_prompt

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
    if project_ctx:
        prompt += f"\n【项目上下文】\n{project_ctx}\n"

    prompt += """

大纲要求：
1. 章节结构完整（含摘要、引言、文献综述、方法、实验、讨论、结论、参考文献）
2. 每章列出 3-5 个要点
3. 标注每章预估字数
4. 标注每章写作难度（⭐~⭐⭐⭐）
5. 给出推荐写作顺序
"""

    outline_result = await _call_llm(prompt, system_prompt)

    # 写回项目 DB
    if project_id and outline_result and not outline_result.startswith("❌"):
        from vermes_cli.scholarforge.project_context import save_outline
        # 简单解析大纲为 sections
        sections = []
        for line in outline_result.split("\n"):
            line = line.strip()
            if line.startswith("## "):
                title = line[3:].strip()
                sections.append({"section_key": f"section_{len(sections)+1}", "title": title, "word_count": 0, "status": "pending"})
        if sections:
            _outline_ok = save_outline(project_id, sections)
            if not _outline_ok:
                logger.error("scholarforge_outline: save_outline failed for project_id=%s", project_id)
                return f"❌ 大纲写回失败：大纲已生成，但数据库回读校验未通过（project_id={project_id}）。可能原因：写入未生效、条目数或章节标识与写入值不符。请检查后重试，勿假定已保存。\n\n---\n\n{outline_result}"

    return outline_result


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
            "project_id": {
                "type": "integer",
                "description": "论文项目 ID。指定后工具会自动加载该项目上下文（标题/大纲/已有章节/文献），结果自动写回项目库。",
            },

        },
        "required": ["text"],
    },
}


async def _handle_scholarforge_polish(args: dict, **kw: Any) -> str:
    """学术润色"""
    text = args.get("text", "")
    project_id = resolve_project_id(args)
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

    if project_id:
        from vermes_cli.scholarforge.project_context import format_project_context_prompt as _fpc
        _pc = _fpc(project_id)
        if _pc:
            prompt += f"\n\n{_pc}"

    # 流式回调：润色也是长文本生成，优先从 kw 获取，退退从 contextvar
    stream_cb = kw.get("stream_callback") or get_stream_callback()
    if stream_cb and callable(stream_cb):
        parts: list[str] = []
        async for chunk in stream_call_llm(prompt, system_prompt):
            stream_cb(chunk)
            parts.append(chunk)
        polished = "".join(parts)
    else:
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
        "文档内部自相似检测（非外部库查重）。基于 SimHash + N-gram 逐段比对本文档"
        "各段落之间的重复，配合 AIGC 写作特征启发式，完全离线运行。"
        "⚠️ 本工具只在【本文档内部】查找自我重复/复制粘贴段落，"
        "不连接知网/万方/维普等任何外部比对库，因此无法检测与他人已发表文献的重复，"
        "结果不能替代知网/维普/PaperPass 等正式查重报告。"
        "返回：文档内部重复率、重复段落、AI 写作特征评分、修改建议。"
        "适用于：写作过程中自查段落复用、发现无意重复、初步评估原创度。"
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
            "project_id": {
                "type": "integer",
                "description": "论文项目 ID。指定后工具会自动加载该项目上下文（标题/大纲/已有章节/文献），结果自动写回项目库。",
            },

        },
        "required": ["text"],
    },
}


async def _handle_scholarforge_plagiarism_check(args: dict, **kw: Any) -> str:
    """查重检测"""
    text = args.get("text", "")
    project_id = resolve_project_id(args)
    title = args.get("title", "")

    if not text.strip():
        return "❌ 请提供需要查重的文本。"
    degraded = len(text) < 200

    try:
        from vermes_cli.scholarforge.plagcheck import full_plagiarism_check

        report = full_plagiarism_check(text, title=title)

        lines = ["## 📊 文档内部自相似检测报告\n"]
        lines.append("> 仅比对本文档各段落之间的重复，**未连接任何外部查重库**，不能替代知网/维普查重。\n")
        lines.append(f"**总字数**: {report.total_chars:,}")
        lines.append(f"**段落数**: {report.total_paragraphs}")
        lines.append(f"**文档内部重复率**: {report.overall_similarity:.1%}")
        lines.append(f"**AI 写作特征率**: {report.aigc_overall_ratio:.1%}")
        lines.append("")

        # 内部重复率评估
        sim = report.overall_similarity
        if sim < 0.15:
            lines.append("✅ 文档内部重复较低，段落复用少")
        elif sim < 0.30:
            lines.append("⚠️ 文档内部重复中等，建议关注下方高重复段落")
        else:
            lines.append("🔴 文档内部重复偏高，可能存在段落复制粘贴，建议改写")

        # 机械化写作特征评估（启发式提示，非 AI 检测结论）
        aigc = report.aigc_overall_ratio
        if aigc < 0.2:
            lines.append("✅ 机械化写作特征较少")
        elif aigc < 0.4:
            lines.append("⚠️ 机械化写作特征中等，建议增加个人观点和案例")
        else:
            lines.append("🔴 机械化写作特征偏多，建议使用 scholarforge_deaigc 做文风自然化")

        # 内部相似段落
        if report.plag_results:
            lines.append("\n### 文档内高相似段落\n")
            for r in report.plag_results[:5]:
                lines.append(f"- 位置 {r.position}：与本文档他段相似度 {r.score:.1%}  {r.text[:50]}...")

        # 机械化写作特征（启发式）
        if report.aigc_results:
            lines.append("\n### 机械化写作特征提示（启发式，非 AI 概率）\n")
            for r in report.aigc_results[:5]:
                feats = ", ".join(r.features[:3]) if r.features else "无"
                lines.append(f"- 位置 {r.position}：特征强度 {r.aigc_probability:.0%}  特征: {feats}")

        # 建议
        if report.suggestions:
            lines.append("\n### 修改建议\n")
            for s in report.suggestions:
                lines.append(f"- {s}")

        # 使用边界提示
        if degraded:
            lines.append(f"\n---\n⚠️ **注意**: 文本仅 {len(text)} 字（不足 200 字），内部自相似检测参考价值有限，建议扩充内容后重新检测。")
        else:
            lines.append("\n---\n💡 **提示**: 本工具只做【文档内部】自相似 + AI 写作特征检测，**不与知网/万方/维普等外部库比对**，无法发现与他人已发表文献的重复。投稿/答辩前的正式查重，请使用知网、维普、PaperPass 等官方平台。")

        return "\n".join(lines)
    except Exception as e:
        logger.error(f"plagiarism_check error: {e}", exc_info=True)
        return f"❌ 文档内部自相似检测失败: {str(e)[:200]}"


# ──────────────────────────────────────────────────────────────
# Tool: De-AIGC
# ──────────────────────────────────────────────────────────────

SCHOLARFORGE_DEAIGC_SCHEMA = {
    "name": "scholarforge_deaigc",
    "description": (
        "AI 写作特征提示 + 文风自然化改写。基于启发式规则提示文本中的「机械化写作特征」"
        "（句式模板化、过渡词堆砌、词汇单一、段落结构僵硬）并给出/执行改写。"
        "注意：特征指数是启发式风格度量，【不是】AI 生成概率，本工具不是 AI 检测器，"
        "也不保证通过知网 AIGC 检测、GPTZero 等任何检测平台。"
        "适用于：提升 AI 辅助写作后的文本自然度、消除模板化表达。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "需要自然化改写的论文文本",
            },
            "aggressive": {
                "type": "boolean",
                "description": "是否激进模式（更多改写），默认 false（保守模式，仅改写高置信度段落）",
                "default": False,
            },
            "project_id": {
                "type": "integer",
                "description": "论文项目 ID。指定后工具会自动加载该项目上下文（标题/大纲/已有章节/文献），结果自动写回项目库。",
            },

        },
        "required": ["text"],
    },
}


async def _handle_scholarforge_deaigc(args: dict, **kw: Any) -> str:
    """去 AI 痕迹"""
    text = args.get("text", "")
    project_id = resolve_project_id(args)
    aggressive = args.get("aggressive", False)

    if not text.strip():
        return "❌ 请提供需要处理的文本。"

    try:
        from vermes_cli.scholarforge.plagcheck import check_aigc, apply_deaigc_suggestions, suggest_deaigc_fixes

        # 1. 检测 AI 痕迹
        aigc = check_aigc(text)
        before_score = aigc.get("aigc_score", 0)

        if before_score < 0.1:
            return "✅ 机械化写作特征很少，无需处理。（注：此为启发式风格度量，非 AI 检测结论）"

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
            if project_id:
                from vermes_cli.scholarforge.project_context import format_project_context_prompt as _fpc
                _pc = _fpc(project_id)
                if _pc:
                    prompt += f"\n\n{_pc}"
            llm_result = await _call_llm(prompt, system_prompt)
            if not llm_result.startswith("❌"):
                cleaned = llm_result

        # 5. 复检
        aigc_after = check_aigc(cleaned)
        after_score = aigc_after.get("aigc_score", 0)

        lines = ["## ✍️ 文风自然化报告\n"]
        lines.append("> 「机械化特征指数」为启发式风格度量（句式模板/过渡词/词汇多样性/段落结构），**不是 AI 生成概率**，不代表任何 AIGC 检测平台的结论。\n")
        lines.append(f"**处理前机械化特征指数**: {before_score:.0%}")
        lines.append(f"**处理后机械化特征指数**: {after_score:.0%}")
        lines.append("")

        if suggestions:
            lines.append("### 检测到的机械化特征\n")
            for s in suggestions[:8]:
                lines.append(f"- **{s.get('fix', '')}**: {s.get('example', '')}")

        if aigc_after.get("features"):
            lines.append("\n### 剩余特征提示\n")
            for f in aigc_after["features"][:5]:
                lines.append(f"- {f}")

        lines.append(f"\n---\n\n## 📄 处理后正文\n\n{cleaned}")

        return "\n".join(lines)
    except Exception as e:
        logger.error(f"deaigc error: {e}", exc_info=True)
        return f"❌ 文风自然化失败: {str(e)[:200]}"


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
            "project_id": {
                "type": "integer",
                "description": "论文项目 ID。指定后工具会自动加载该项目上下文（标题/大纲/已有章节/文献），结果自动写回项目库。",
            },

        },
        "required": ["content"],
    },
}


async def _handle_scholarforge_score(args: dict, **kw: Any) -> str:
    """论文评分"""
    content = args.get("content", "")
    project_id = resolve_project_id(args)
    topic = args.get("topic", "")

    if not content.strip():
        return "❌ 请提供论文内容。"
    if len(content) < 500:
        return "⚠️ 文本过短（<500字），评分参考价值有限。"

    try:
        from vermes_cli.scholarforge.scoring import score_paper

        # 提取引用的文献列表（从 [n] 标记）
        import re
        ref_nums = set(int(n) for n in re.findall(r'\[(\d+)\]', content))
        # 构造简易 papers 列表（使用带属性的轻量对象，便于 score_paper 读取 title/authors/year）
        class _PaperRef:
            def __init__(self, title):
                self.title = title
                self.authors = []
                self.year = ""
        papers = [_PaperRef(f"文献 [{n}]") for n in sorted(ref_nums)]

        # 接入真实 LLM 评分工厂：
        # 此前误传 _make_llm=None → score_paper 永远走 _fallback_score 启发式假评分（原创性恒 5.0）。
        # 与 blueprint.py:1501 的 scholar_stream 评分调用保持一致，传入 _call_llm 工厂。
        # 评分是分析任务，用低温度 + 分析模型降低随机性。
        _ANALYSIS_MODEL = ANALYSIS_MODEL
        async def _analysis_llm(prompt, system="", **kw):
            return await _call_llm(prompt, system, temperature=0.2, model=_ANALYSIS_MODEL, **kw)
        result = await score_paper(content, papers, _make_llm=lambda: _analysis_llm, topic=topic)

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
        "导出论文为 Word/PDF/LaTeX/Markdown/BibTeX/Zotero(CSL JSON) 格式。"
        "适用于：论文定稿后导出到本地，在 WPS/Word/LaTeX 编辑器中进一步编辑，"
        "或导出 CSL JSON 直接导入 Zotero 文献库。"
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
                "description": "导出格式：docx/pdf/latex/markdown/bibtex/zotero（Zotero 用 CSL JSON，可直接导入文献库）",
                "enum": ["docx", "pdf", "latex", "markdown", "bibtex", "zotero"],
                "default": "docx",
            },
            "abstract": {
                "type": "string",
                "description": "摘要（可选）",
            },
            "project_id": {
                "type": "integer",
                "description": "论文项目 ID。指定后工具会自动加载该项目上下文（标题/大纲/已有章节/文献），结果自动写回项目库。",
            },

        },
        "required": ["title", "content", "format"],
    },
}


async def _handle_scholarforge_export(args: dict, **kw: Any) -> str:
    """导出论文（导出成功后标记项目完成）。"""
    title = args.get("title", "")
    # 软解析：优先显式 project_id，否则回退激活项目；导出正文由 content 提供，project_id 仅用于收尾标记
    project_id = resolve_project_id(args)
    content = args.get("content", "")
    fmt = args.get("format", "docx")
    abstract = args.get("abstract", "")

    # content 为空时自动从 DB 组装已写回的章节
    if not content.strip() and project_id:
        from vermes_cli.scholarforge.database import get_all_sections, get_outline
        sections = get_all_sections(project_id)
        outline = get_outline(project_id)
        parts = []
        for sec in outline:
            key = sec.get("id", "")
            sec_title = sec.get("title", key)
            sec_content = sections.get(key, "")
            if sec_content.strip():
                parts.append(f"## {sec_title}\n\n{sec_content}")
        if parts:
            content = "\n\n".join(parts)
            logger.info(f"export: auto-assembled {len(parts)} sections from DB (project_id={project_id})")

    if not title.strip() or not content.strip():
        return "❌ 请提供论文标题和正文，或先使用 write 工具写入章节内容后重试。"

    _export_result = ""
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
            from vermes_cli.scholarforge.export.full import export_docx
            data = export_docx(title, content, papers, abstract=abstract)
            filepath = os.path.join(export_path, f"{safe_title}.docx")
            with open(filepath, "wb") as f:
                f.write(data)
            _export_result = f"✅ Word 文档已导出：{filepath}\n\n📄 文件大小：{len(data)/1024:.0f} KB\n💡 可用 WPS 或 Microsoft Word 打开编辑。"

        elif fmt == "pdf":
            from vermes_cli.scholarforge.export.full import export_pdf
            data = export_pdf(title, content, papers, abstract=abstract)
            filepath = os.path.join(export_path, f"{safe_title}.pdf")
            with open(filepath, "wb") as f:
                f.write(data)
            _export_result = f"✅ PDF 已导出：{filepath}\n\n📄 文件大小：{len(data)/1024:.0f} KB"

        elif fmt == "latex":
            from vermes_cli.scholarforge.export.full import export_latex
            latex_text = export_latex(title, content, papers, abstract=abstract)
            filepath = os.path.join(export_path, f"{safe_title}.tex")
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(latex_text)
            _export_result = f"✅ LaTeX 已导出：{filepath}\n\n📄 文件大小：{len(latex_text)/1024:.0f} KB"

        elif fmt == "markdown":
            from vermes_cli.scholarforge.export.full import export_markdown
            md_text = export_markdown(title, content, papers, abstract=abstract)
            filepath = os.path.join(export_path, f"{safe_title}.md")
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(md_text)
            _export_result = f"✅ Markdown 已导出：{filepath}\n\n📄 文件大小：{len(md_text)/1024:.0f} KB"

        elif fmt == "bibtex":
            from vermes_cli.scholarforge.export.full import export_bibtex
            bib_text = export_bibtex(papers)
            filepath = os.path.join(export_path, f"{safe_title}.bib")
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(bib_text)
            _export_result = f"✅ BibTeX 已导出：{filepath}\n\n📄 文件大小：{len(bib_text)/1024:.0f} KB"

        elif fmt == "zotero":
            # CSL JSON 直接从参考文献区解析（不读 SQLite），质量优于弱 parser 的 papers
            csl_papers = []
            if ref_section:
                from vermes_cli.scholarforge.export import parse_references_csl

                csl_papers = parse_references_csl(ref_section.group(1))
            from vermes_cli.scholarforge.export.full import export_csl_json

            csl_text = export_csl_json(csl_papers)
            filepath = os.path.join(export_path, f"{safe_title}.json")
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(csl_text)
            _export_result = (
                f"✅ Zotero CSL JSON 已导出：{filepath}\n\n"
                f"📚 收录文献：{len(csl_papers)} 篇\n"
                f"💡 可直接在 Zotero「文件 → 导入」该 JSON 入库"
            )

        else:
            return f"❌ 不支持的格式：{fmt}"

    except Exception as e:
        logger.error(f"export error: {e}", exc_info=True)
        return f"❌ 导出失败: {str(e)[:200]}"

    # 导出成功 → 标记项目完成（fail-open）
    if _export_result and project_id:
        try:
            from vermes_cli.scholarforge.project_context import mark_project_done
            mark_project_done(project_id)
        except Exception:
            pass
    return _export_result


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
            "project_id": {
                "type": "integer",
                "description": "论文项目 ID。指定后工具会自动加载该项目上下文（标题/大纲/已有章节/文献），结果自动写回项目库。",
            },

        },
        "required": ["papers"],
    },
}


def _parse_papers(raw: Any) -> list[dict]:
    """解析文献列表 — 接受 list / JSON 字符串 / 纯文本，返回统一的 dict 列表。

    verify_citations 和 review_claims 两个 handler 共用。
    """
    if isinstance(raw, list):
        return raw
    if not raw or (isinstance(raw, str) and not raw.strip()):
        return []

    # 尝试 JSON
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass

    # 按行解析
    import re
    papers = []
    for line in raw.strip().split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
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
    return papers


async def _handle_scholarforge_verify_citations(args: dict, **kw: Any) -> str:
    """验证引用真实性"""
    papers_raw = args.get("papers", "")
    project_id = resolve_project_id(args)
    enable_online = args.get("enable_online", True)
    papers = _parse_papers(papers_raw)

    if not papers:
        return "❌ 未能解析文献列表，请提供 JSON 数组或每行一篇的格式。"

    try:
        from vermes_cli.scholarforge.validators import verify_citation_authenticity, format_citation_report
        checks = await verify_citation_authenticity(papers, enable_online=enable_online)
        return format_citation_report(checks)
    except Exception as e:
        logger.error(f"verify_citations error: {e}", exc_info=True)
        return f"❌ 引用验证失败: {str(e)[:200]}"


# ──────────────────────────────────────────────────────────────
# Tool: Review Claims (主张-证据审查流水线)
# ──────────────────────────────────────────────────────────────

SCHOLARFORGE_REVIEW_CLAIMS_SCHEMA = {
    "name": "scholarforge_review_claims",
    "description": (
        "主张-证据审查流水线：从论文中抽取核心主张(Claim)，逐条检查引用真实性、"
        "统计一致性、研究设计缺陷，生成结构化审查报告。"
        "适用于：投稿前自检、审稿辅助、AI 生成论文的质量门控。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "paper_text": {
                "type": "string",
                "description": "论文全文或主要章节文本",
            },
            "references": {
                "type": "string",
                "description": (
                    "文献列表，JSON 数组格式，每条含 title/authors/year/venue/doi。"
                    "或每行一篇，格式：作者. 标题. 期刊. 年份. DOI。"
                    "不提供则跳过引用核查。"
                ),
            },
            "design_info": {
                "type": "object",
                "description": "可选的结构化设计信息，如 {\"design\":\"between-subjects\",\"groups\":2}",
            },
            "enable_online": {
                "type": "boolean",
                "description": "是否启用在线引用验证（默认 true），关闭时仅做本地启发式检查",
                "default": True,
            },
            "project_id": {
                "type": "integer",
                "description": "论文项目 ID。指定后工具会自动加载该项目上下文（标题/大纲/已有章节/文献），结果自动写回项目库。",
            },

        },
        "required": ["paper_text"],
    },
}


async def _handle_scholarforge_review_claims(args: dict, **kw: Any) -> str:
    """主张-证据审查流水线"""
    paper_text = args.get("paper_text", "")
    project_id = resolve_project_id(args)
    if not paper_text.strip():
        return "❌ 请提供论文文本。"

    references_raw = args.get("references", "")
    references = _parse_papers(references_raw) if references_raw else None

    design_info = args.get("design_info")
    enable_online = args.get("enable_online", True)

    try:
        from vermes_cli.scholarforge.claim_audit import review_claims
        return await review_claims(
            paper_text,
            references=references,
            design_info=design_info,
            enable_online=enable_online,
        )
    except Exception as e:
        logger.error(f"review_claims error: {e}", exc_info=True)
        return f"❌ 主张-证据审查失败: {str(e)[:200]}"


# ──────────────────────────────────────────────────────────────
# Tool: Research Map (研究选题拆解)
# ──────────────────────────────────────────────────────────────

SCHOLARFORGE_RESEARCH_MAP_SCHEMA = {
    "name": "scholarforge_research_map",
    "description": (
        "研究选题拆解：将模糊研究方向拆成研究问题树、共识/分歧/空白、可验证假设。"
        "适用于：开题前选题分析、确定研究gap、生成可测试假设。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "description": "研究方向或大题目，如 '大语言模型在教育中的应用'",
            },
            "context": {
                "type": "string",
                "description": "可选补充上下文（已有文献、方法、限制条件等）",
            },
            "project_id": {
                "type": "integer",
                "description": "论文项目 ID。指定后工具会自动加载该项目上下文（标题/大纲/已有章节/文献），结果自动写回项目库。",
            },

        },
        "required": ["topic"],
    },
}


async def _handle_scholarforge_research_map(args: dict, **kw: Any) -> str:
    """研究选题拆解"""
    topic = args.get("topic", "")
    project_id = resolve_project_id(args)
    if not topic.strip():
        return "❌ 请提供研究方向。"

    context = args.get("context", "")

    try:
        from vermes_cli.scholarforge.research_map import research_map
        return await research_map(topic, context=context)
    except Exception as e:
        logger.error(f"research_map error: {e}", exc_info=True)
        return f"❌ 研究选题拆解失败: {str(e)[:200]}"


# ──────────────────────────────────────────────────────────────
# Tool: Save Literature Cards (文献知识沉淀)
# ──────────────────────────────────────────────────────────────

SCHOLARFORGE_SAVE_CARDS_SCHEMA = {
    "name": "scholarforge_save_literature_cards",
    "description": (
        "文献知识沉淀：把搜索结果存为结构化卡片（LLM 抽取研究问题/方法/数据/发现/局限/主张/标签），跨会话累积。"
        "适用于：文献调研后沉淀、积累个人文献库、为综述写作储备素材。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索关键词，触发检索后自动沉淀。与 papers 二选一。",
            },
            "papers": {
                "type": "string",
                "description": "已有的文献 JSON 数组（每条含 title/authors/year/abstract/doi 等），与 query 二选一。",
            },
            "limit": {
                "type": "integer",
                "description": "最大沉淀数量，默认 10",
            },
            "project_id": {
                "type": "integer",
                "description": "论文项目 ID。指定后工具会自动加载该项目上下文（标题/大纲/已有章节/文献），结果自动写回项目库。",
            },

        },
    },
}


async def _handle_scholarforge_save_cards(args: dict, **kw: Any) -> str:
    """文献知识沉淀"""
    query = args.get("query", "")
    project_id = resolve_project_id(args)
    papers_json = args.get("papers", "")
    limit = args.get("limit", 10)

    try:
        from vermes_cli.scholarforge.literature_cards import save_cards, save_cards_from_query

        if papers_json.strip():
            import json as _json
            try:
                papers = _json.loads(papers_json)
            except _json.JSONDecodeError:
                return "❌ papers 参数不是合法的 JSON 数组。"
            result = await save_cards(papers)
        elif query.strip():
            result = await save_cards_from_query(query, limit=limit)
        else:
            return "❌ 请提供 query 或 papers 参数。"

        return (
            f"✅ 文献卡片沉淀完成\n"
            f"  新增: {result['added']} 篇\n"
            f"  跳过(重复/无效): {result['skipped']} 篇\n"
            f"  总计: {result['total']} 篇"
        )
    except Exception as e:
        logger.error(f"save_cards error: {e}", exc_info=True)
        return f"❌ 文献沉淀失败: {str(e)[:200]}"


# ──────────────────────────────────────────────────────────────
# Tool: Literature Matrix (综述矩阵)
# ──────────────────────────────────────────────────────────────

SCHOLARFORGE_MATRIX_SCHEMA = {
    "name": "scholarforge_literature_matrix",
    "description": (
        "从已沉淀的文献卡片生成综述矩阵：按研究问题/方法/数据/发现/局限分列展示，并提示潜在研究空白。"
        "适用于：写文献综述时梳理已有文献、发现研究 gap。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "description": "可选，用 TF-IDF 对摘要做语义排序",
            },
            "tag": {
                "type": "string",
                "description": "可选，按标签过滤",
            },
            "limit": {
                "type": "integer",
                "description": "最多返回条数，默认 30",
            },
            "project_id": {
                "type": "integer",
                "description": "论文项目 ID。指定后工具会自动加载该项目上下文（标题/大纲/已有章节/文献），结果自动写回项目库。",
            },

        },
    },
}


SCHOLARFORGE_MANAGE_SNAPSHOTS_SCHEMA = {
    "name": "scholarforge_manage_snapshots",
    "description": (
        "管理论文项目版本快照：创建快照、列出快照、恢复快照、删除快照。"
        "适用于：写论文前创建安全回滚点、恢复到之前版本、查看历史版本。"
        "action: create=创建快照, list=列出快照, restore=恢复快照, get=查看快照详情, delete=删除快照。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "操作: create/list/restore/get/delete",
            },
            "project_id": {
                "type": "integer",
                "description": "论文项目 ID（create/list 必填）",
            },
            "snapshot_id": {
                "type": "integer",
                "description": "快照 ID（restore/get/delete 必填）",
            },
            "label": {
                "type": "string",
                "description": "快照标签（create 可选，如'大纲定稿'）",
            },
            "note": {
                "type": "string",
                "description": "快照备注（create 可选）",
            },
        },
        "required": ["action"],
    },
}


SCHOLARFORGE_APPLY_TEMPLATE_SCHEMA = {
    "name": "scholarforge_apply_template",
    "description": (
        "论文模板管理：列出预设模板、获取模板详情、从模板创建项目、从已有项目导出模板。"
        "适用于：快速开始新论文、标准化论文结构、导师模板复用。"
        "action: list=列出预设模板, get=获取模板详情, create=从模板创建项目, export=从项目导出模板。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "操作: list/get/create/export",
            },
            "template_key": {
                "type": "string",
                "description": "模板标识（list/get/create 使用，如 cs_undergraduate）",
            },
            "title": {
                "type": "string",
                "description": "新项目标题（create 必填）",
            },
            "project_id": {
                "type": "integer",
                "description": "已有项目 ID（export 使用）",
            },
        },
        "required": ["action"],
    },
}


async def _handle_scholarforge_literature_matrix(args: dict, **kw: Any) -> str:
    """综述矩阵"""
    topic = args.get("topic", "")
    project_id = resolve_project_id(args)
    tag = args.get("tag", "")
    limit = args.get("limit", 30)

    try:
        from vermes_cli.scholarforge.literature_cards import literature_matrix
        return literature_matrix(topic=topic, tag=tag, limit=limit)
    except Exception as e:
        logger.error(f"literature_matrix error: {e}", exc_info=True)
        return f"❌ 生成综述矩阵失败: {str(e)[:200]}"


# ──────────────────────────────────────────────────────────────────────────
# Tool: Manage Snapshots (Phase 2 — 版本快照)
# ──────────────────────────────────────────────────────────────────────────

async def _handle_scholarforge_manage_snapshots(args: dict, **kw: Any) -> str:
    """管理论文项目版本快照。"""
    action = args.get("action", "list")
    # 软解析：优先显式 project_id，否则回退激活项目；restore 等动作不依赖 project_id
    project_id = resolve_project_id(args)
    snapshot_id = args.get("snapshot_id", 0)
    label = args.get("label", "")
    note = args.get("note", "")

    try:
        from vermes_cli.scholarforge.project_context import (
            auto_snapshot,
            restore_snapshot,
            list_snapshots,
            get_snapshot_detail,
            delete_snapshot,
        )

        if action == "create":
            if not project_id:
                return "❌ 创建快照需要 project_id"
            sid = auto_snapshot(project_id, label=label, note=note)
            if sid:
                return f"✅ 快照已创建\n\n快照 ID: {sid}\n标签: {label or '(无)'}\n备注: {note or '(无)'}\n\n可在未来使用 action=restore + snapshot_id={sid} 恢复到此版本。"
            return f"❌ 创建快照失败（项目 {project_id} 可能不存在）"

        elif action == "list":
            if not project_id:
                return "❌ 列出快照需要 project_id"
            snaps = list_snapshots(project_id)
            if not snaps:
                return f"项目 {project_id} 暂无快照。\n\n使用 action=create 创建快照。"
            lines = [f"## 📸 项目 {project_id} 的快照列表\n"]
            for s in snaps:
                import time as _t
                ts = _t.strftime("%Y-%m-%d %H:%M", _t.localtime(s["created_at"]))
                size_kb = (s.get("size", 0) or 0) / 1024
                sid_val = s["id"]
                label_val = s["label"] or "(无标签)"
                note_val = s.get("note", "")
                lines.append(f"  #{sid_val} [{ts}] {label_val} ({size_kb:.1f}KB)")
                if note_val:
                    lines.append(f"    备注: {note_val}")
            lines.append(f"\n共 {len(snaps)} 个快照。")
            return "\n".join(lines)

        elif action == "restore":
            if not snapshot_id:
                return "❌ 恢复快照需要 snapshot_id"
            result = restore_snapshot(snapshot_id)
            err = result.get("error")
            if err:
                return f"❌ 恢复失败: {err}"
            pid_r = result.get("project_id")
            sid_r = result.get("snapshot_id")
            label_r = result.get("label")
            outline_n = result.get("outline_sections", 0)
            content_n = result.get("content_sections", 0)
            return (
                f"✅ 快照已恢复\n\n"
                f"项目 ID: {pid_r}\n"
                f"快照 ID: {sid_r}\n"
                f"标签: {label_r}\n"
                f"大纲节数: {outline_n}\n"
                f"章节数: {content_n}\n\n"
                f"⚠️ 当前项目状态已被覆盖。如需撤销，可使用原快照 ID 重新恢复。"
            )

        elif action == "get":
            if not snapshot_id:
                return "❌ 查看快照需要 snapshot_id"
            snap = get_snapshot_detail(snapshot_id)
            err = snap.get("error")
            if err:
                return f"❌ {err}"
            import time as _t
            ts = _t.strftime("%Y-%m-%d %H:%M", _t.localtime(snap.get("created_at", 0)))
            snap_id = snap["id"]
            snap_pid = snap.get("project_id", "?")
            snap_label = snap.get("label", "")
            snap_note = snap.get("note", "")
            lines = [
                f"## 📸 快照 #{snap_id}\n",
                f"项目 ID: {snap_pid}",
                f"标签: {snap_label}",
                f"备注: {snap_note}",
                f"创建时间: {ts}",
            ]
            payload = snap.get("payload", {})
            if payload:
                p_title = payload.get("title", "")
                p_type = payload.get("paper_type", "")
                lines.append(f"\n项目标题: {p_title}")
                lines.append(f"论文类型: {p_type}")
                outline = payload.get("outline", [])
                if outline:
                    lines.append("\n大纲:")
                    for s in outline:
                        s_num = s.get("section_number", "")
                        s_title = s.get("section_title", "")
                        lines.append(f"  {s_num} {s_title}")
                contents = payload.get("contents", {})
                if contents:
                    lines.append(f"\n章节内容: {len(contents)} 段")
                    for key, content in contents.items():
                        preview = (content or "")[:100]
                        cl = len(content or "")
                        lines.append(f"  {key} ({cl} 字): {preview}...")
            return "\n".join(lines)

        elif action == "delete":
            if not snapshot_id:
                return "❌ 删除快照需要 snapshot_id"
            ok = delete_snapshot(snapshot_id)
            if ok:
                return f"✅ 快照 {snapshot_id} 已删除。"
            return f"❌ 删除失败"

        else:
            return f"❌ 未知操作: {action}\n\n支持: create/list/restore/get/delete"

    except Exception as e:
        logger.error(f"manage_snapshots error: {e}", exc_info=True)
        return f"❌ 快照操作失败: {str(e)[:200]}"


# ────────────────────────────────────────────────────────────────────
# Tool: Apply Template (Phase 4 — 模板导入)
# ────────────────────────────────────────────────────────────────────

async def _handle_scholarforge_apply_template(args: dict, **kw: Any) -> str:
    """论文模板管理。"""
    action = args.get("action", "list")
    template_key = args.get("template_key", "")
    title = args.get("title", "")
    # 软解析：优先显式 project_id，否则回退激活项目；list/get/create 动作不依赖它
    project_id = resolve_project_id(args)

    try:
        from vermes_cli.scholarforge.project_templates import (
            list_builtin_templates,
            get_builtin_template,
            export_project_as_template,
            create_project_from_template,
        )

        if action == "list":
            templates = list_builtin_templates()
            if not templates:
                return "暂无预设模板。"
            lines = ["## 📋 预设论文模板\n"]
            for t in templates:
                tk = t["key"]
                tn = t["name"]
                tpt = t["paper_type"]
                ts = t["sections"]
                tw = t["target_words"]
                lines.append(
                    f"  `{tk}` — {tn}"
                    f"（{tpt}，{ts} 章节，目标 {tw} 字）"
                )
            lines.append("\n使用 action=create + template_key 创建项目。")
            return "\n".join(lines)

        elif action == "get":
            if not template_key:
                return "❌ 需要 template_key 参数"
            t = get_builtin_template(template_key)
            if not t:
                return f"❌ 模板 {template_key} 不存在"
            t_name = t["name"]
            t_type = t["paper_type"]
            t_words = t["target_words"]
            t_style = t["citation_style"]
            t_outline = t["outline"]
            n_out = len(t_outline)
            lines = [
                f"## 📋 模板: {t_name}\n",
                f"论文类型: {t_type}",
                f"目标字数: {t_words}",
                f"引用格式: {t_style}",
                f"\n大纲 ({n_out} 章节):",
            ]
            for s in t_outline:
                wc = s.get("wordCount", 0)
                wc_str = f" ({wc} 字)" if wc else ""
                s_num = s.get("number", "")
                s_title = s.get("title", "")
                lines.append(f"  {s_num} {s_title}{wc_str}")
            return "\n".join(lines)

        elif action == "create":
            if not template_key:
                return "❌ 需要 template_key 参数"
            if not title:
                return "❌ 需要 title 参数（新项目标题）"
            t = get_builtin_template(template_key)
            if not t:
                return f"❌ 模板 {template_key} 不存在"
            result = create_project_from_template(t, title)
            err = result.get("error")
            if err:
                return f"❌ 创建失败: {err}"
            pid = result.get("id", "?")
            n_sections = len(result.get("outline", []))
            t_name = t["name"]
            t_type = t["paper_type"]
            t_words = t["target_words"]
            return (
                f"✅ 项目已创建\n\n"
                f"项目 ID: {pid}\n"
                f"标题: {title}\n"
                f"模板: {t_name}\n"
                f"大纲: {n_sections} 章节\n"
                f"论文类型: {t_type}\n"
                f"目标字数: {t_words} 字\n"
            )

        elif action == "export":
            if not project_id:
                return "❌ 需要 project_id 参数"
            template = export_project_as_template(project_id)
            err = template.get("error")
            if err:
                return f"❌ 导出失败: {err}"
            tp_name = template["name"]
            tp_type = template["paper_type"]
            tp_words = template["target_words"]
            tp_outline = template["outline"]
            n_tp = len(tp_outline)
            lines = [
                f"## 📋 模板已导出\n",
                f"名称: {tp_name}",
                f"论文类型: {tp_type}",
                f"目标字数: {tp_words}",
                f"大纲: {n_tp} 章节",
            ]
            for s in tp_outline:
                s_num = s.get("number", "")
                s_title = s.get("title", "")
                lines.append(f"  {s_num} {s_title}")
            lines.append("\n可使用此模板创建新项目。")
            return "\n".join(lines)

        else:
            return f"❌ 未知操作: {action}\n\n支持: list/get/create/export"

    except Exception as e:
        logger.error(f"apply_template error: {e}", exc_info=True)
        return f"❌ 模板操作失败: {str(e)[:200]}"


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
            "project_id": {
                "type": "integer",
                "description": "论文项目 ID。指定后工具会自动加载该项目上下文（标题/大纲/已有章节/文献），结果自动写回项目库。",
            },

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
        from vermes_cli.scholarforge.validators import check_statistics_consistency, format_statistics_report
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
            "project_id": {
                "type": "integer",
                "description": "论文项目 ID。指定后工具会自动加载该项目上下文（标题/大纲/已有章节/文献），结果自动写回项目库。",
            },

        },
        "required": ["paper_text"],
    },
}


async def _handle_scholarforge_detect_design_flaws(args: dict, **kw: Any) -> str:
    """研究设计缺陷检测"""
    paper_text = args.get("paper_text", "")
    project_id = resolve_project_id(args)

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
        from vermes_cli.scholarforge.validators import (
            detect_design_flaws, detect_design_flaws_llm, format_design_report, _dedup_flaws,
        )
        # 双路检测：① 同步启发式（教育/心理学关键词，快、零成本、确定性）
        #          ② LLM 语义分析（学科无关，覆盖医学/工程/经济/CS 等任意领域）
        heuristic = detect_design_flaws(paper_text, design_info)
        llm_flaws = await detect_design_flaws_llm(paper_text, design_info, call_llm=_call_llm)
        # 合并去重：启发式在前（确定性优先），LLM 补充其未覆盖的学科通用缺陷
        merged = _dedup_flaws(heuristic + llm_flaws)
        report = format_design_report(merged)
        if llm_flaws:
            report += "\n\n> ℹ️ 本报告已融合关键词启发式与 LLM 语义分析（学科无关），LLM 结果供参考请人工复核。"
        return report
    except Exception as e:
        logger.error(f"detect_design_flaws error: {e}", exc_info=True)
        return f"❌ 设计缺陷检测失败: {str(e)[:200]}"


# ──────────────────────────────────────────────────────────────
# Tool: Format References
# ──────────────────────────────────────────────────────────────

SCHOLARFORGE_FORMAT_REFS_SCHEMA = {
    "name": "scholarforge_format_refs",
    "description": (
        "格式化参考文献列表。支持 4 种主流格式：GB/T 7714-2015（国标）、APA 7th、"
        "IEEE、MLA 9th，覆盖国内学位论文与主流期刊投稿场景。"
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
                "description": "引用格式：gbt7714=国标 GB/T 7714-2015，apa=APA 7th，ieee=IEEE，mla=MLA 9th",
                "enum": ["gbt7714", "apa", "ieee", "mla"],
                "default": "gbt7714",
            },
            "project_id": {
                "type": "integer",
                "description": "论文项目 ID。指定后工具会自动加载该项目上下文（标题/大纲/已有章节/文献），结果自动写回项目库。",
            },

        },
        "required": ["papers"],
    },
}


async def _handle_scholarforge_format_refs(args: dict, **kw: Any) -> str:
    """格式化参考文献"""
    import json as json_mod
    import re

    project_id = resolve_project_id(args)
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

    # 向后兼容：旧枚举值 apa7 → apa
    if style == "apa7":
        style = "apa"

    STYLE_LABELS = {
        "gbt7714": "GB/T 7714-2015",
        "apa": "APA 7th",
        "ieee": "IEEE",
        "mla": "MLA 9th",
    }
    if style not in STYLE_LABELS:
        return f"❌ 不支持的格式：{style}。支持：gbt7714 / apa / ieee / mla"

    try:
        if style == "gbt7714":
            # GB/T 7714 保留 quality.py 的成熟实现（含中文规则）
            from vermes_cli.scholarforge.quality import format_all_references_gbt7714
            result = format_all_references_gbt7714(papers)
        else:
            # APA/IEEE/MLA 统一走 citation_provider 的样式引擎
            from vermes_cli.scholarforge.citation_provider import format_citation
            lines = []
            for i, p in enumerate(papers, 1):
                # 作者字段兼容字符串输入（按行解析路径产出 str）
                if isinstance(p.get("authors"), str):
                    p = {**p, "authors": [a.strip() for a in re.split(r'[,;，；]', p["authors"]) if a.strip()]}
                lines.append(format_citation(p, style=style, index=i))
            result = "\n\n".join(lines)

        return f"## 📚 参考文献列表（{STYLE_LABELS[style]} 格式）\n\n{result}"
    except Exception as e:
        logger.error(f"format_refs error: {e}", exc_info=True)
        return f"❌ 格式化失败: {str(e)[:200]}"


# ──────────────────────────────────────────────────────────────
# 质量护栏显式工具
# ──────────────────────────────────────────────────────────────

SCHOLARFORGE_QUALITY_GATE_SCHEMA = {
    "name": "scholarforge_quality_gate",
    "description": (
        "显式全量质量检查。对已写回的章/全文运行引用真实性+统计一致性+设计缺陷检测。"
        "适用于：投稿前最终质检、章写完后深度检查、用户主动要求质量审查。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "project_id": {
                "type": "integer",
                "description": "论文项目 ID。",
            },
            "section_key": {
                "type": "string",
                "description": "指定章/节（如 'introduction'），不传则检查全项目。",
            },
            "stats": {
                "type": "object",
                "description": "统计指标字典（如 {eta_squared: 0.05, cohens_d: 0.3}），用于统计一致性校验。",
            },
            "paper_text": {
                "type": "string",
                "description": "论文全文（不传则从项目 DB 读取）。",
            },
            "design_info": {
                "type": "object",
                "description": "结构化设计信息（如 {design: 'between-subjects', control_group: true}）。",
            },
        },
        "required": ["project_id"],
    },
}


async def _handle_scholarforge_quality_gate(args: dict, **kw: Any) -> str:
    """显式全量质量检查"""
    from vermes_cli.scholarforge.quality_gate import run_full_quality_gate

    project_id = resolve_project_id(args)
    if not project_id:
        return "❌ 请提供 project_id。"

    try:
        report = await run_full_quality_gate(
            project_id=project_id,
            section_key=args.get("section_key"),
            papers=None,  # 从 DB 读
            stats=args.get("stats"),
            paper_text=args.get("paper_text", ""),
            design_info=args.get("design_info"),
        )
        return report or "✅ 未发现质量问题。"
    except Exception as e:
        logger.error(f"quality_gate error: {e}", exc_info=True)
        return f"❌ 质量检查失败: {str(e)[:200]}"


# ── LLM token 用量累加器（G4a：闭环 ScholarForge token 黑洞）──
# _call_llm 内部把每次 LLM 调用的 usage 累加进当前工具调用上下文；
# _with_usage 在 finally 汇总归一化后落库。用 ContextVar 隔离并发工具调用，
# 默认 None 时 _call_llm 直接跳过（无感知、零开销），不影响未包裹的调用。
_LLM_USAGE_ACC: _cv.ContextVar = _cv.ContextVar("_scholarforge_llm_usage_acc", default=None)


def _accumulate_llm_usage(usage_dict, provider, model, base_url, api_key):
    """把一次 LLM 调用的 usage 追加到当前工具调用上下文（若存在）。"""
    _acc = _LLM_USAGE_ACC.get()
    if _acc is None:
        return
    _acc.append((usage_dict, provider, model, base_url, api_key))


def _dict_to_ns(d):
    """递归把 dict 转成 SimpleNamespace，使 normalize_usage 的 getattr 能读到字段。"""
    if isinstance(d, dict):
        return types.SimpleNamespace(**{k: _dict_to_ns(v) for k, v in d.items()})
    return d


def _summarize_llm_usage(entries):
    """汇总多次 LLM 调用的 token 与估算成本。

    复用主链路 agent.usage_pricing 的 normalize_usage + estimate_usage_cost，
    不重写任何计价逻辑。返回 {input_tokens, output_tokens, estimated_cost_usd, model}。
    任何单条解析/计价失败都跳过该条（fail-open），绝不阻断工具。
    """
    _in = _out = 0
    _cost = 0.0
    _model = None
    try:
        from agent.usage_pricing import normalize_usage, estimate_usage_cost
    except Exception:
        return {"input_tokens": 0, "output_tokens": 0, "estimated_cost_usd": 0.0, "model": None}
    for (_usage, _provider, _model_e, _base_url, _api_key) in entries:
        if not _model:
            _model = _model_e
        try:
            _ns = _dict_to_ns(_usage) if isinstance(_usage, dict) else _usage
            _cu = normalize_usage(_ns, provider=_provider, api_mode=None)
            _in += int(_cu.input_tokens or 0)
            _out += int(_cu.output_tokens or 0)
            _cr = estimate_usage_cost(
                _model_e or "", _cu, provider=_provider, base_url=_base_url, api_key=_api_key
            )
            if _cr and _cr.amount_usd is not None:
                _cost += float(_cr.amount_usd)
        except Exception:
            continue
    return {
        "input_tokens": _in,
        "output_tokens": _out,
        "estimated_cost_usd": _cost,
        "model": _model,
    }


def _with_usage(name: str, handler):
    """工具使用埋点包装器（用户场景验证）。

    记录每次调用的工具名/成败/耗时/token 到 scholarforge 自有 DB 的 tool_usage 表，
    用真实使用数据驱动后续优化优先级。token 来自 _call_llm 经 _LLM_USAGE_ACC 累加的
    usage 字段，复用主链路 normalize_usage + estimate_usage_cost 归一化计价（G4a）。
    埋点失败静默，绝不影响工具本身。成败判定：handler 抛异常或返回以 ❌ 开头的字符串视为失败。
    """
    import functools
    import time as _time

    @functools.wraps(handler)
    async def wrapped(args: dict, **kw):
        _t0 = _time.monotonic()
        _acc_token = _LLM_USAGE_ACC.set([])
        ok = True
        try:
            try:
                result = await handler(args, **kw)
                ok = not (isinstance(result, str) and result.lstrip().startswith("❌"))
                return result
            except Exception:
                ok = False
                raise
        finally:
            try:
                _entries = _LLM_USAGE_ACC.get()
                _LLM_USAGE_ACC.reset(_acc_token)
                if _entries:
                    _sum = _summarize_llm_usage(_entries)
                    from vermes_cli.scholarforge.database import record_tool_usage
                    record_tool_usage(
                        name, ok=ok,
                        duration_ms=int((_time.monotonic() - _t0) * 1000),
                        input_tokens=_sum["input_tokens"],
                        output_tokens=_sum["output_tokens"],
                        estimated_cost_usd=_sum["estimated_cost_usd"],
                        model=_sum["model"],
                    )
                else:
                    from vermes_cli.scholarforge.database import record_tool_usage
                    record_tool_usage(
                        name, ok=ok,
                        duration_ms=int((_time.monotonic() - _t0) * 1000),
                    )
            except Exception:
                try:
                    _LLM_USAGE_ACC.reset(_acc_token)
                except Exception:
                    pass

    return wrapped


# ──────────────────────────────────────────────────────────────
# Tool: Citation Graph (一跳引用图谱)
# ──────────────────────────────────────────────────────────────

SCHOLARFORGE_CITATION_GRAPH_SCHEMA = {
    "name": "scholarforge_citation_graph",
    "description": (
        "构建论文的一跳引用图谱：输入一篇论文的 DOI 或 Semantic Scholar 论文 ID，"
        "返回它「被哪些论文引用」(citations)、「引用了哪些论文」(references)、"
        "以及「Semantic Scholar 推荐的相似论文」(recommendations)。"
        "适用于：开题找相关工作、梳理某领域的引文脉络、发现高影响力论文、"
        "为文献综述补充候选文献。结果带本地缓存，避免触发 S2 限流。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "paper_id": {
                "type": "string",
                "description": "论文标识：DOI（如 10.1145/3292500.3330701）或 Semantic Scholar 论文 ID（40 位 hex）",
            },
            "kinds": {
                "type": "array",
                "description": "要获取的边类型，默认全部三类。可选子集：citations / references / recommendations",
                "items": {
                    "type": "string",
                    "enum": ["citations", "references", "recommendations"],
                },
                "default": ["citations", "references", "recommendations"],
            },
            "limit": {
                "type": "integer",
                "description": "每类边最多返回条数，默认 50，最大 100",
                "minimum": 1,
                "maximum": 100,
                "default": 50,
            },
            "use_cache": {
                "type": "boolean",
                "description": "是否使用本地缓存（默认 true，30 天内同论文直接命中缓存，避开 S2 限流）",
                "default": True,
            },
            "project_id": {
                "type": "integer",
                "description": "论文项目 ID（可选）。仅用于上下文关联，不改变写回行为。",
            },
        },
        "required": ["paper_id"],
    },
}


async def _handle_scholarforge_citation_graph(args: dict, **kw: Any) -> str:
    """构建一跳引用图谱（复用 S2 学术图谱 + 本地缓存）"""
    paper_id = args.get("paper_id", "")
    if not paper_id or not str(paper_id).strip():
        return "❌ 请提供论文标识（DOI 或 Semantic Scholar 论文 ID）。"

    kinds = args.get("kinds") or ["citations", "references", "recommendations"]
    if isinstance(kinds, str):
        kinds = [kinds]
    limit = min(int(args.get("limit", 50) or 50), 100)
    use_cache = args.get("use_cache", True)
    if isinstance(use_cache, str):
        use_cache = use_cache.lower() != "false"

    try:
        from vermes_cli.scholarforge.citation_graph import build_citation_graph

        result = build_citation_graph(
            paper_id, kinds=list(kinds), limit=limit, use_cache=use_cache
        )
    except Exception as e:
        logger.error(f"citation_graph error: {e}", exc_info=True)
        return f"❌ 引用图谱构建失败: {str(e)[:200]}"

    if not result.get("success"):
        err = result.get("error", "未知错误")
        e2 = result.get("errors") or {}
        if e2:
            err += "（" + "; ".join(f"{k}: {v}" for k, v in e2.items()) + "）"
        return f"❌ 引用图谱获取失败: {err}"

    data = result["data"]
    counts = result["counts"]
    cache_tag = "（命中本地缓存）" if result.get("cache_hit") else ""
    lines = [f"## 论文引用图谱: {paper_id}{cache_tag}", ""]
    lines.append(
        f"边统计：被引 **{counts['citations']}** · 引证 **{counts['references']}** · "
        f"推荐 **{counts['recommendations']}** · 去重节点 **{result.get('node_count')}**"
    )
    lines.append("")

    def _fmt_nodes(nodes, label):
        nodes = nodes or []
        lines.append(f"### {label}（{len(nodes)}）")
        if not nodes:
            lines.append("（无）")
        for i, n in enumerate(nodes[:limit], 1):
            authors = ", ".join((n.get("authors") or [])[:3])
            if len(n.get("authors") or []) > 3:
                authors += " et al."
            cite = n.get("citationCount", 0)
            doi = n.get("doi", "")
            suffix = f" · 📎 {cite} 引用" + (f" · DOI:{doi}" if doi else "")
            lines.append(f"**[{i}] {n.get('title', '')}**")
            lines.append(f"  {authors} · {n.get('year', '')}{suffix}")
        lines.append("")

    _fmt_nodes(data.get("citations"), "被以下论文引用 (citations)")
    _fmt_nodes(data.get("references"), "引用了以下论文 (references)")
    _fmt_nodes(data.get("recommendations"), "Semantic Scholar 推荐 (recommendations)")
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────
# Schema + Tool: 项目发现与激活（修复「agent 不知道 project_id」根因）
# ──────────────────────────────────────────────────────────────

SCHOLARFORGE_LIST_PROJECTS_SCHEMA = {
    "name": "scholarforge_list_projects",
    "description": (
        "列出当前所有论文项目，并标出哪一个被设为「激活项目」。\n"
        "激活项目是此后写回类工具（scholarforge_write / replace_citations / learn_style / "
        "manage_snapshots / apply_template / export）默认作用的对象——当调用未显式传 project_id 时生效。\n"
        "当不知道该操作哪个项目、或想切换项目时调用本工具。"
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}


SCHOLARFORGE_SET_ACTIVE_PROJECT_SCHEMA = {
    "name": "scholarforge_set_active_project",
    "description": (
        "设置当前「激活论文项目」。设置后，所有写回类工具若未显式传 project_id，"
        "将默认作用于此项目（解决 agent 对话路径拿不到 project_id 导致写回不落库的问题）。\n"
        "用于切换正在撰写的论文。可用 scholarforge_list_projects 查看可选 id。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "project_id": {
                "type": "integer",
                "description": "要设为激活项目的论文项目 id（正整数，可用 scholarforge_list_projects 查看）",
            },
        },
        "required": ["project_id"],
    },
}


async def _handle_scholarforge_list_projects(args: dict, **kw: Any) -> str:
    """列出论文项目并标出激活项目。"""
    from vermes_cli.scholarforge.database import list_projects

    active = get_active_project()
    projects = list_projects()
    if not projects:
        return "📭 当前没有任何论文项目。请先在面板新建，或调用对应创建工具。"

    lines = ["## 📚 论文项目列表", ""]
    for p in projects:
        mark = " ➡️ [激活]" if p.get("id") == active else ""
        lines.append(
            f"- #{p.get('id')} 《{p.get('title')}》"
            f"（{p.get('paper_type', '')}，{p.get('section_count', 0)} 章 / "
            f"{p.get('total_words', 0)} 字 / {p.get('literature_count', 0)} 文献）{mark}"
        )
    if active:
        lines.append(
            "\n当前激活项目为 "
            f"#{active}。写回类工具会自动作用于它；切换请调用 scholarforge_set_active_project。"
        )
    else:
        lines.append(
            "\n尚未设置激活项目。写回类工具将要求显式 project_id，"
            "或请先调用 scholarforge_set_active_project。"
        )
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────
# Tool: Read Section (读取章节内容)
# ──────────────────────────────────────────────────────────────

async def _handle_scholarforge_read_section(args: dict, **kw: Any) -> str:
    """读取论文项目中已写入的章节内容。"""
    project_id = resolve_project_id(args)
    if not project_id:
        return PROJECT_ID_MISSING_MSG

    section_key = args.get("section_key", "").strip()

    from vermes_cli.scholarforge.database import (
        get_all_sections,
        get_outline,
        get_section_content,
    )

    if section_key:
        # 读取单个章节
        content = get_section_content(project_id, section_key)
        if not content.strip():
            return f"📄 章节 `{section_key}` 尚未写入内容。"
        return f"## {section_key}\n\n{content}"

    # 读取全部章节概览
    sections = get_all_sections(project_id)
    outline = get_outline(project_id)
    lines = [f"📋 项目 #{project_id} 章节概览：\n"]
    total_words = 0
    written = 0
    for sec in outline:
        key = sec.get("id", "")
        title = sec.get("title", key)
        status = sec.get("status", "pending")
        content = sections.get(key, "")
        wc = len(content)
        total_words += wc
        if wc > 0:
            written += 1
        lines.append(f"  - **{title}** (`{key}`) — {wc} 字，状态: {status}")
    lines.append(f"\n📊 总计：{written}/{len(outline)} 章已写入，{total_words} 字")
    if total_words == 0:
        lines.append("⚠️ 所有章节均未写入内容。请先使用 `scholarforge_write` 写入章节。")
    return "\n".join(lines)


async def _handle_scholarforge_set_active_project(args: dict, **kw: Any) -> str:
    """设置激活论文项目。"""
    raw = args.get("project_id", 0)
    try:
        pid = int(raw) if raw else 0
    except (TypeError, ValueError):
        pid = 0
    if pid <= 0:
        return "❌ 请提供有效的 project_id（正整数）。可用 scholarforge_list_projects 查看项目列表。"
    from vermes_cli.scholarforge.database import get_project

    if not get_project(pid):
        return f"❌ 项目 #{pid} 不存在。可用 scholarforge_list_projects 查看有效项目。"
    set_active_project(pid)
    return (
        f"✅ 已将项目 #{pid} 设为激活项目。后续写回类工具默认作用于它；"
        "切换请再次调用 scholarforge_set_active_project。"
    )


def register_tools(host_api=None):
    """Register all ScholarForge tools in the global registry.

    Called by module_loader after host_api injection.
    Not called on import to avoid premature registration.
    """

    # P0-A: Outcome Verifier — 外证回读验证工具写回是否真在库
    def _verify_scholarforge_write(
        function_name: str, function_args: dict,
        function_result: str, is_error: bool,
    ) -> tuple[bool, str]:
        """外证回读：查 section_contents 表确认内容真在库且非空。

        R1: 不信任 is_error —— handler 返回 ❌ 字符串而非抛错。
        R3: 走外证回读（SELECT），不自证工具返回串。
        """
        try:
            from vermes_cli.scholarforge.database import get_conn, init_db
            init_db()
            project_id = function_args.get("project_id")
            section_key = function_args.get("section_key")
            if not project_id or not section_key:
                return (True, "no project_id/section_key — skip verification")
            with get_conn() as conn:
                row = conn.execute(
                    "SELECT content FROM section_contents WHERE project_id=? AND section_key=?",
                    (project_id, section_key),
                ).fetchone()
            if row is None:
                return (False, f"section_contents row not found (pid={project_id}, key={section_key})")
            if not row["content"]:
                return (False, f"section_contents content is empty (pid={project_id}, key={section_key})")
            return (True, "")
        except Exception as e:
            # R4: fail-open
            return (True, f"verifier error: {e}")

    def _verify_scholarforge_outline(
        function_name: str, function_args: dict,
        function_result: str, is_error: bool,
    ) -> tuple[bool, str]:
        """外证回读：查 outlines 表确认条目真在库。"""
        try:
            from vermes_cli.scholarforge.database import get_conn, init_db
            init_db()
            project_id = function_args.get("project_id")
            if not project_id:
                return (True, "no project_id — skip verification")
            with get_conn() as conn:
                count = conn.execute(
                    "SELECT COUNT(*) FROM outlines WHERE project_id=?",
                    (project_id,),
                ).fetchone()
            if count is None or count[0] == 0:
                return (False, f"outlines: 0 rows after outline (pid={project_id})")
            return (True, f"outlines: {count[0]} rows confirmed")
        except Exception as e:
            return (True, f"verifier error: {e}")

    registry.register(
        name="scholarforge_search",
        toolset="scholarforge",
        schema=SCHOLARFORGE_SEARCH_SCHEMA,
        handler=_with_usage("scholarforge_search", _handle_scholarforge_search),
        is_async=True,
        emoji="📚",
        description="搜索学术文献（arXiv/Crossref/Semantic Scholar/PubMed 等 7 个免费源）",
    )
    registry.register(
        name="scholarforge_write",
        toolset="scholarforge",
        schema=SCHOLARFORGE_WRITE_SCHEMA,
        handler=_with_usage("scholarforge_write", _handle_scholarforge_write),
        is_async=True,
        emoji="✍️",
        description="撰写学术论文内容（引言/文献综述/方法/实验/讨论/结论）",
        verify_fn=_verify_scholarforge_write,
    )
    registry.register(
        name="scholarforge_review",
        toolset="scholarforge",
        schema=SCHOLARFORGE_REVIEW_SCHEMA,
        handler=_with_usage("scholarforge_review", _handle_scholarforge_review),
        is_async=True,
        emoji="🔍",
        description="审阅论文草稿，给出结构化评审意见",
    )
    registry.register(
        name="scholarforge_replace_citations",
        toolset="scholarforge",
        schema=SCHOLARFORGE_REPLACE_CITATIONS_SCHEMA,
        handler=_with_usage("scholarforge_replace_citations", _handle_scholarforge_replace_citations),
        is_async=True,
        emoji="🔗",
        description="替换 [n] 占位符为真实文献引用",
    )
    registry.register(
        name="scholarforge_learn_style",
        toolset="scholarforge",
        schema=SCHOLARFORGE_LEARN_STYLE_SCHEMA,
        handler=_with_usage("scholarforge_learn_style", _handle_scholarforge_learn_style),
        is_async=True,
        emoji="🎯",
        description="学习用户写作风格，后续写作自动仿写",
    )
    registry.register(
        name="scholarforge_outline",
        toolset="scholarforge",
        schema=SCHOLARFORGE_OUTLINE_SCHEMA,
        handler=_with_usage("scholarforge_outline", _handle_scholarforge_outline),
        is_async=True,
        emoji="📝",
        description="生成论文大纲（章节结构+每章要点+预估字数）",
        verify_fn=_verify_scholarforge_outline,
    )
    registry.register(
        name="scholarforge_polish",
        toolset="scholarforge",
        schema=SCHOLARFORGE_POLISH_SCHEMA,
        handler=_with_usage("scholarforge_polish", _handle_scholarforge_polish),
        is_async=True,
        emoji="✨",
        description="学术润色（语言+逻辑+格式）",
    )
    registry.register(
        name="scholarforge_plagiarism_check",
        toolset="scholarforge",
        schema=SCHOLARFORGE_PLAGIARISM_CHECK_SCHEMA,
        handler=_with_usage("scholarforge_plagiarism_check", _handle_scholarforge_plagiarism_check),
        is_async=True,
        emoji="📊",
        description="论文查重检测（SimHash+N-gram+AIGC 启发式）",
    )
    registry.register(
        name="scholarforge_deaigc",
        toolset="scholarforge",
        schema=SCHOLARFORGE_DEAIGC_SCHEMA,
        handler=_with_usage("scholarforge_deaigc", _handle_scholarforge_deaigc),
        is_async=True,
        emoji="🤖",
        description="文风自然化（机械化特征提示+改写，非 AI 检测器）",
    )
    registry.register(
        name="scholarforge_score",
        toolset="scholarforge",
        schema=SCHOLARFORGE_SCORE_SCHEMA,
        handler=_with_usage("scholarforge_score", _handle_scholarforge_score),
        is_async=True,
        emoji="⭐",
        description="论文三维度评分（原创性+逻辑性+引用完整性）",
    )
    registry.register(
        name="scholarforge_export",
        toolset="scholarforge",
        schema=SCHOLARFORGE_EXPORT_SCHEMA,
        handler=_with_usage("scholarforge_export", _handle_scholarforge_export),
        is_async=True,
        emoji="📤",
        description="导出论文（Word/PDF/LaTeX/Markdown/BibTeX）",
    )
    registry.register(
        name="scholarforge_format_refs",
        toolset="scholarforge",
        schema=SCHOLARFORGE_FORMAT_REFS_SCHEMA,
        handler=_with_usage("scholarforge_format_refs", _handle_scholarforge_format_refs),
        is_async=True,
        emoji="📚",
        description="格式化参考文献（GB/T 7714 / APA 7th）",
    )
    registry.register(
        name="scholarforge_verify_citations",
        toolset="scholarforge",
        schema=SCHOLARFORGE_VERIFY_CITATIONS_SCHEMA,
        handler=_with_usage("scholarforge_verify_citations", _handle_scholarforge_verify_citations),
        is_async=True,
        emoji="🔬",
        description="验证文献引用真实性（CrossRef/Semantic Scholar API 在线校验）",
    )
    registry.register(
        name="scholarforge_check_stats",
        toolset="scholarforge",
        schema=SCHOLARFORGE_CHECK_STATS_SCHEMA,
        handler=_with_usage("scholarforge_check_stats", _handle_scholarforge_check_stats),
        is_async=True,
        emoji="📐",
        description="统计指标一致性校验（η²↔d↔t↔F 值换算验证）",
    )
    registry.register(
        name="scholarforge_detect_design_flaws",
        toolset="scholarforge",
        schema=SCHOLARFORGE_DETECT_DESIGN_FLAWS_SCHEMA,
        handler=_with_usage("scholarforge_detect_design_flaws", _handle_scholarforge_detect_design_flaws),
        is_async=True,
        emoji="⚠️",
        description="研究设计缺陷检测（多要素未分离/评估者偏差/样本代表性等 8 类）",
    )
    registry.register(
        name="scholarforge_review_claims",
        toolset="scholarforge",
        schema=SCHOLARFORGE_REVIEW_CLAIMS_SCHEMA,
        handler=_with_usage("scholarforge_review_claims", _handle_scholarforge_review_claims),
        is_async=True,
        emoji="⚖️",
        description="主张-证据审查流水线（抽取 Claim → 逐条检查引用/统计/设计 → 结构化报告）",
    )
    registry.register(
        name="scholarforge_research_map",
        toolset="scholarforge",
        schema=SCHOLARFORGE_RESEARCH_MAP_SCHEMA,
        handler=_with_usage("scholarforge_research_map", _handle_scholarforge_research_map),
        is_async=True,
        emoji="🗺️",
        description="研究选题拆解（方向→问题树+共识/分歧/空白+可验证假设）",
    )
    registry.register(
        name="scholarforge_save_literature_cards",
        toolset="scholarforge",
        schema=SCHOLARFORGE_SAVE_CARDS_SCHEMA,
        handler=_with_usage("scholarforge_save_literature_cards", _handle_scholarforge_save_cards),
        is_async=True,
        emoji="📇",
        description="文献知识沉淀（search→结构化卡片+LLM 抽取 7 字段+跨会话累积）",
    )
    registry.register(
        name="scholarforge_literature_matrix",
        toolset="scholarforge",
        schema=SCHOLARFORGE_MATRIX_SCHEMA,
        handler=_with_usage("scholarforge_literature_matrix", _handle_scholarforge_literature_matrix),
        is_async=True,
        emoji="📊",
        description="综述矩阵（已沉淀卡片→按方法/数据/发现分列+gap 提示）",
    )
    registry.register(
        name="scholarforge_manage_snapshots",
        toolset="scholarforge",
        schema=SCHOLARFORGE_MANAGE_SNAPSHOTS_SCHEMA,
        handler=_with_usage("scholarforge_manage_snapshots", _handle_scholarforge_manage_snapshots),
        is_async=True,
        emoji="📸",
        description="版本快照管理（创建/列出/恢复/查看/删除）",
    )
    registry.register(
        name="scholarforge_apply_template",
        toolset="scholarforge",
        schema=SCHOLARFORGE_APPLY_TEMPLATE_SCHEMA,
        handler=_with_usage("scholarforge_apply_template", _handle_scholarforge_apply_template),
        is_async=True,
        emoji="📋",
        description="论文模板管理（预设/导出/创建）",
    )
    registry.register(
        name="scholarforge_quality_gate",
        toolset="scholarforge",
        schema=SCHOLARFORGE_QUALITY_GATE_SCHEMA,
        handler=_with_usage("scholarforge_quality_gate", _handle_scholarforge_quality_gate),
        is_async=True,
        emoji="🛡️",
        description="显式全量质量检查（引用真实性+统计一致性+设计缺陷，可选在线验证）",
    )
    registry.register(
        name="scholarforge_citation_graph",
        toolset="scholarforge",
        schema=SCHOLARFORGE_CITATION_GRAPH_SCHEMA,
        handler=_with_usage("scholarforge_citation_graph", _handle_scholarforge_citation_graph),
        is_async=True,
        emoji="🕸️",
        description="构建论文一跳引用图谱（被引/引证/推荐），复用 S2 学术图谱 + 本地缓存避限流",
    )
    registry.register(
        name="scholarforge_list_projects",
        toolset="scholarforge",
        schema=SCHOLARFORGE_LIST_PROJECTS_SCHEMA,
        handler=_with_usage("scholarforge_list_projects", _handle_scholarforge_list_projects),
        is_async=True,
        emoji="🗂️",
        description="列出所有论文项目并标出当前激活项目",
    )
    registry.register(
        name="scholarforge_set_active_project",
        toolset="scholarforge",
        schema=SCHOLARFORGE_SET_ACTIVE_PROJECT_SCHEMA,
        handler=_with_usage("scholarforge_set_active_project", _handle_scholarforge_set_active_project),
        is_async=True,
        emoji="🎯",
        description="设置当前激活论文项目（写回类工具默认作用对象）",
    )

    # ── read_section ──
    registry.register(
        name="scholarforge_read_section",
        toolset="scholarforge",
        schema={
            "name": "scholarforge_read_section",
            "description": (
                "读取论文项目中已写入的章节内容。可指定 section_key 读单个章节，"
                "或不指定读取全部章节概览（标题+字数+状态）。"
                "用于 export 前确认已写回的内容。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "integer", "description": "论文项目 ID"},
                    "section_key": {
                        "type": "string",
                        "description": "章节标识（如 intro/method/result）。留空则返回全部章节概览。",
                    },
                },
            },
        },
        handler=_with_usage("scholarforge_read_section", _handle_scholarforge_read_section),
        is_async=True,
        emoji="📖",
        description="读取论文章节内容（单章或全部概览）",
    )
    logger.info("[ScholarForge] 26 Agent tools registered: search/write/review/replace_citations/learn_style/outline/polish/plagiarism_check/deaigc/score/export/format_refs/verify_citations/check_stats/detect_design_flaws/review_claims/research_map/save_literature_cards/literature_matrix/manage_snapshots/apply_template/quality_gate/citation_graph/list_projects/set_active_project/read_section")
