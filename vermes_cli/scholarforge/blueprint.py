"""
ScholarForge Blueprint — 论文写作模块
独立注册，不影响 Vermes 原有链路

全部端点挂载在 /api/scholar 下，与 Vermes 核心路由完全隔离
"""
import asyncio
import json
import logging
import os
import time
from typing import Optional

from fastapi import HTTPException, Path, UploadFile, File
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agent.pipeline import Pipeline, PipelineConfig, Stage

logger = logging.getLogger(__name__)

# RAG retriever 缓存 (project_id → (paper_count, PaperRetriever))
_rag_cache: dict = {}

# ScholarForge 默认 system prompt — 防止模型自我介绍的闲聊式回复
_SCHOLAR_SYSTEM = (
    "你是一个专业的中文学术写作助手（ScholarForge）。"
    "请直接输出学术内容，不要自我介绍，不要说'你好'或'我是某某AI'。"
    "输出结构清晰、语言严谨的中文学术文本。"
)


# === 论文 Agent LLM 调用（完全独立 provider/model 解析，不依赖 Vermes 默认值） ===

# 各 provider 的默认模型（仅 ScholarForge 内部用，不污染 Vermes 核心）
_PROVIDER_FALLBACK_MODELS = {
    "agnes": "agnes-2.0-flash",
    "deepseek": "deepseek-v4-flash",
    "openai": "gpt-4o-mini",
    "anthropic": "claude-sonnet-4-20250514",
    "ollama": "llama3.2",
    "openrouter": "openrouter/auto",
}


def _resolve_credentials(provider: str = "", model: str = ""):
    """复用 Vermes 核心的 _resolve_model_provider，与聊天 Agent 完全同步。"""
    from vermes_cli.blueprints.chat import _resolve_model_provider
    resolved_provider, base_url, api_key, resolved_model = _resolve_model_provider(
        model or "deepseek-v4-flash",
        provider or None,
    )
    return {
        "api_key": api_key or "",
        "base_url": base_url.rstrip("/") if base_url else "",
        "model": resolved_model or "",
        "provider": resolved_provider or "",
    }


def _list_configured_providers() -> list[dict]:
    """扫描用户已配置的 providers，返回按优先级排序的列表。
    完全复用 Vermes 凭证链路 — 通过 _resolve_model_provider 逐个检查。
    """
    from vermes_cli.blueprints.chat import PROVIDERS, _resolve_model_provider

    result = []
    for prov_key in PROVIDERS:
        try:
            resolved_provider, base_url, api_key, resolved_model = _resolve_model_provider(
                "",  # 无 model，仅根据 provider 解析凭证
                prov_key,
            )
            if api_key and base_url:
                result.append({
                    "provider": prov_key,
                    "api_key": api_key,
                    "base_url": base_url.rstrip("/"),
                    "model": resolved_model or _PROVIDER_FALLBACK_MODELS.get(prov_key, ""),
                })
        except Exception as e:
            logger.debug(f"Provider discovery failed: {e}", exc_info=True)
            continue
    return result


def _pick_default_provider(providers: list[dict]) -> dict | None:
    """用户未显式选择时，取第一个可用 provider 作为默认值"""
    return providers[0] if providers else None


def _resolve_agent_providers(pid: int) -> dict[str, dict]:
    """解析所有 Agent 的 provider/model：
    1. 如果用户显式绑定了（agent_providers 表），用用户的选择
    2. 否则根据 Agent 类型自动选择最佳模型
    3. 所有 Agent 共享同一组已配置 provider 池
    """
    from . import database as db
    stored = db.get_agent_providers(pid) if pid > 0 else dict(db.DEFAULT_AGENT_PROVIDERS)
    all_providers = _list_configured_providers()

    result = {}
    for agent_name in ["topic", "literature", "outline", "writing", "refinement", "reviewer"]:
        cfg = stored.get(agent_name, {})
        provider = cfg.get("provider", "")
        model = cfg.get("model", "")
        if provider:
            # 用户显式绑定了，照用
            result[agent_name] = {"provider": provider, "model": model}
        else:
            # 自动选择
            best = _pick_default_provider(all_providers)
            if best:
                result[agent_name] = {"provider": best["provider"], "model": best["model"]}
            else:
                result[agent_name] = {"provider": "", "model": ""}
        if agent_name == "writing":
            pass
    return result


def _scan_configured_providers() -> list[dict]:
    """兼容旧接口 — 返回所有已配置 providers"""
    return _list_configured_providers()


async def _make_llm(provider_override: str = None, model_override: str = None):
    """工厂函数 — 返回绑定好特定 provider/model 的 _llm。
    每次调用按用户实际配置解析凭证，不依赖 Vermes 默认 Chat 链路。
    如果 provider 未指定，自动扫描用户已配置的 providers，用第一个有 Key 的。
    """
    provider = provider_override or ""
    model = model_override or ""

    if not provider:
        # 自动检测：直接复用 Vermes 当前聊天 provider
        creds = _resolve_credentials()
        provider = creds["provider"]
        if not model:
            model = creds["model"]
        logger.info(f"[ScholarForge] Auto-selected: {provider}/{model or 'auto'}")

    # 预解析凭证
    creds = _resolve_credentials(provider, model)

    async def _llm(prompt: str, system_prompt: str = _SCHOLAR_SYSTEM) -> str:
        import httpx

        url = creds["base_url"]
        key = creds["api_key"]
        model = creds["model"]
        provider = creds["provider"]

        if not url or not key:
            raise HTTPException(
                500,
                f"未配置 API Key。请在 Vermes 设置中为「{provider or 'deepseek'}」添加 Key，"
                "或在论文页右上面板为当前 Agent 选择已配置的模型。"
            )

        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        body = {
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 8192,
            "stream": False,
        }
        if model:
            body["model"] = model

        _LLM_TIMEOUT = float(os.environ.get('SCHOLAR_LLM_TIMEOUT', '60'))
        try:
            async with httpx.AsyncClient(timeout=_LLM_TIMEOUT) as client:
                resp = await client.post(
                    f"{url.rstrip('/')}/chat/completions",
                    json=body, headers=headers,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return data["choices"][0]["message"]["content"]
                else:
                    err = resp.text[:200]
                    logger.error(
                        f"LLM call failed ({provider}/{model}): {resp.status_code} {err}"
                    )
                    raise HTTPException(
                        502, f"{provider}/{model} 调用失败 ({resp.status_code})"
                    )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"LLM call error ({provider}/{model}): {e}")
            raise HTTPException(502, str(e))

    return _llm


# === Pydantic Models ===
class ScholarChatRequest(BaseModel):
    message: str
    agent: str = "topic"
    pipeline: bool = False
    project_id: Optional[int] = None
    section: str = ""       # 指定只写某一节（对应前端 activeSection）
    depth: int = 2          # 1=快速, 2=标准, 3=深度
    checkpoint: bool = True  # 每阶段后等待用户确认（默认开启）
    client_id: str = ""     # 前端 localStorage client UUID，用于多用户隔离
    continue_from: str = ""  # P1: 从指定阶段继续 pipeline（跳过已完成的阶段）


class LiteratureSearchRequest(BaseModel):
    query: str
    limit: int = 10


# === 内存中的项目上下文 ===
_session_contexts: dict[str, "ProjectContext"] = {}
_ctx_lock = asyncio.Lock()
_CTX_MAX = 50  # 最大缓存数，防止内存泄漏


async def _get_ctx(project_id: str = "default", client_id: str = ""):
    from vermes_cli.scholarforge.agents import ProjectContext
    from . import database as db

    # Key by client_id:project_id for multi-user isolation
    ctx_key = f"{client_id}:{project_id}" if client_id else project_id

    # 快速路径：已存在直接返回
    if ctx_key in _session_contexts:
        return _session_contexts[ctx_key]

    async with _ctx_lock:
        # double-check
        if ctx_key in _session_contexts:
            return _session_contexts[ctx_key]

        # LRU 淘汰：超过上限时删除最早的条目
        while len(_session_contexts) >= _CTX_MAX:
            oldest_key = next(iter(_session_contexts))
            del _session_contexts[oldest_key]

        ctx = ProjectContext()
        # 从数据库恢复项目状态（paper_type / title / draft / papers / outline）
        try:
            pid_int = int(project_id) if project_id and project_id != "default" else 0
            if pid_int > 0:
                proj = db.get_project(pid_int)
                if proj:
                    if proj.get("paper_type"):
                        ctx.paper_type = proj["paper_type"]
                    ctx.topic = proj.get("title", "") or ctx.topic
                    # 从 section_contents 恢复 draft
                    contents = proj.get("contents", {})
                    full_paper = contents.get("full_paper", "")
                    if full_paper:
                        ctx.draft = full_paper
                    # 从 literatures 恢复 papers
                    literatures = proj.get("literatures", [])
                    for lit in literatures:
                        ctx.add_paper(lit)
                    # 从 outlines 恢复 outline
                    outline_rows = proj.get("outline", [])
                    if outline_rows:
                        ctx.outline = {"sections": outline_rows}
        except Exception as e:
            logger.debug(f"Session context restore failed: {e}", exc_info=True)
        _session_contexts[ctx_key] = ctx
    return _session_contexts[ctx_key]


# === SSE 事件格式化 ===
def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


# === Blueprint 注册函数 ===
def register_to(app, host_api=None):
    """向 FastAPI app 注册 ScholarForge 路由 — 全部挂载在 /api/scholar

    Args:
        app: FastAPI 应用实例
        host_api: 宿主接口（由 module_loader 注入，用于运行时模块模式）
    """
    if host_api is not None:
        import host_api as _ha
        _ha._inject(host_api)

    @app.post("/api/tools/invoke")
    async def api_invoke_tool(req: dict):
        """通用工具直接调用端点 — 走 registry.dispatch，复用已注册 handler。

        写回类工具（如 scholarforge_write）内部已含 run_quality_gate，质量闸门自然生效。
        请求体: {"name": "scholarforge_write", "args": {...}}
        响应: {"result": "<handler 返回的字符串>"}
        """
        from tools.registry import registry
        name = (req or {}).get("name")
        args = (req or {}).get("args") or {}
        if not name:
            raise HTTPException(400, "missing tool name")
        result = registry.dispatch(name, args)
        return {"result": result}

    @app.get("/api/scholar/model")
    async def current_model_info():
        """返回当前默认模型与 Provider，便于前端展示"""
        from vermes_cli.blueprints.chat import _get_chat_credentials, _resolve_model_provider
        try:
            base_url, api_key, default_model = _get_chat_credentials()
            provider, url, key, model = _resolve_model_provider(
                default_model or "deepseek-v4-flash", ""
            )
            return {
                "model": model or default_model or "deepseek-v4-flash",
                "provider": provider or "deepseek",
                "base_url": (url or base_url or "").rstrip("/"),
            }
        except Exception as e:
            return {"model": "deepseek-v4-flash", "provider": "deepseek", "base_url": "", "error": str(e)}

    @app.get("/api/scholar/tools")
    async def api_list_tool_schemas():
        """暴露 scholarforge 工具 schema，供前端 SchemaForm 自动生成参数表单。

        返回每项: {name, description, emoji, is_async, schema, requires_project_id}
        schema 即工具注册时的 JSON Schema（parameters.properties / required）。
        """
        from tools.registry import registry
        tools = []
        for entry in registry._snapshot_entries():
            if getattr(entry, "toolset", None) != "scholarforge":
                continue
            params = entry.schema.get("parameters", {}) if entry.schema else {}
            props = params.get("properties", {}) or {}
            tools.append({
                "name": entry.name,
                "description": entry.description or "",
                "emoji": getattr(entry, "emoji", "") or "",
                "is_async": bool(getattr(entry, "is_async", False)),
                "schema": entry.schema,
                "requires_project_id": "project_id" in props,
            })
        tools.sort(key=lambda t: t["name"])
        return {"tools": tools}

    @app.get("/api/scholar/usage")
    async def api_tool_usage(days: int = 30):
        """工具使用统计（用户场景验证：真实使用数据驱动优先级）。

        返回最近 N 天每个工具的调用次数/成功率/平均耗时/最近使用时间，
        数据来自 tool_usage 表（register_tools 的 _with_usage 包装器自动埋点）。
        """
        from .database import get_tool_usage_stats

        days = max(1, min(days, 365))
        stats = get_tool_usage_stats(days=days)
        return {"days": days, "stats": stats}

    # ═══════════════════════════════════════════════════════════════
    # 质量报告 — QualityView 读取/保存 section_quality 表
    # ═══════════════════════════════════════════════════════════════

    @app.get("/api/scholar/quality")
    async def api_list_quality(project_id: int = 0):
        """列出某项目的全部质量报告（按检查时间倒序）。

        数据来自 section_quality 表（写回闸门 flag 模式落库 + 前端手动全量检查落库）。
        """
        from .quality_gate import list_quality_reports

        if not project_id:
            return {"reports": []}
        return {"reports": list_quality_reports(project_id)}

    @app.post("/api/scholar/quality")
    async def api_save_quality(req: dict):
        """显式保存一份质量报告（前端手动触发 scholarforge_quality_gate 后落库）。"""
        from .quality_gate import save_quality_report

        project_id = req.get("project_id")
        section_key = req.get("section_key", "") or ""
        report = req.get("report", "")
        if not project_id:
            raise HTTPException(400, "project_id 必填")
        save_quality_report(project_id, section_key, report)
        return {"saved": True}

    # ═══════════════════════════════════════════════════════════════
    # 项目管理 — 每个论文项目独立的工作空间
    # ═══════════════════════════════════════════════════════════════

    @app.get("/api/scholar/projects")
    async def api_list_projects():
        """列出所有论文项目"""
        from . import database as db
        return {"projects": db.list_projects()}

    @app.post("/api/scholar/active-project")
    async def api_set_active_project(req: dict):
        """设置当前激活论文项目（供 agent 对话路径零样本写回使用）。

        前端面板激活/切换项目时调用，把 project_id 种入后端进程全局；
        agent 调用写回类工具时若未显式传 project_id，会自动取此激活项目。
        """
        from . import database as db
        from .active_project import set_active_project
        raw = req.get("project_id", 0)
        try:
            pid = int(raw) if raw else 0
        except (TypeError, ValueError):
            pid = 0
        if pid <= 0:
            raise HTTPException(400, "project_id 必须为正整数")
        if not db.get_project(pid):
            raise HTTPException(404, "项目不存在")
        set_active_project(pid)
        return {"active_project_id": pid}

    @app.post("/api/scholar/projects")
    async def api_create_project(req: dict):
        """创建新论文项目"""
        from . import database as db
        title = (req.get("title") or "").strip()
        if not title:
            raise HTTPException(400, "项目标题不能为空")
        proj = db.create_project(
            title=title,
            paper_type=req.get("paper_type", "本科论文"),
            target_words=int(req.get("target_words", 8000)),
        )
        return proj

    @app.get("/api/scholar/projects/{pid}")
    async def api_get_project(pid: int):
        """获取项目详情（含大纲/内容）"""
        from . import database as db
        proj = db.get_project(pid)
        if not proj:
            raise HTTPException(404, "项目不存在")
        return proj

    @app.patch("/api/scholar/projects/{pid}")
    async def api_update_project(pid: int, req: dict):
        """更新项目属性"""
        from . import database as db
        ok = db.update_project(pid, **req)
        if not ok:
            raise HTTPException(400, "无可更新字段")
        return db.get_project(pid)

    @app.delete("/api/scholar/projects/{pid}")
    async def api_delete_project(pid: int):
        """删除项目"""
        from . import database as db
        ok = db.delete_project(pid)
        if not ok:
            raise HTTPException(404, "项目不存在")
        return {"deleted": True}

    @app.get("/api/scholar/projects/{pid}/section/{section_key}")
    async def api_get_section(pid: int, section_key: str):
        """获取章节内容"""
        from . import database as db
        content = db.get_section_content(pid, section_key)
        return {"content": content, "section_key": section_key}

    @app.post("/api/scholar/projects/{pid}/section/{section_key}")
    async def api_save_section(pid: int, section_key: str, req: dict):
        """保存章节内容（写回前过质量闸门，与 agent write handler 行为一致）"""
        from . import database as db
        from .quality_gate import run_quality_gate
        content = req.get("content", "")
        # 质量闸门前移（与 commit 0fa84d3f9 的 write handler 一致）：
        # Tier1 De-AIGC+查重恒跑，mode=flag 写回成功+报告附返回+落 section_quality 表。
        # 消除「前端编辑直存绕过闸门」缺口；flag 模式不拦截，净化后 content 同用于落库。
        if pid and content:
            content, _gate_report, _blocked = run_quality_gate(
                pid, section_key, content, mode="flag", stage="write"
            )
        # P0-B 扩面：接回返回值，不再无条件 saved:True（web 端此前是静默假成功路径）。
        # 空内容是前端合法的「清空章节」操作，save_section_content 对其返回 False，
        # 但那不算写回失败，故只在 content 非空时判为真失败。
        _persist_ok = db.save_section_content(pid, section_key, content)
        if not _persist_ok and content:
            logger.error(
                "api_save_section: save_section_content failed (pid=%s, section_key=%s)",
                pid, section_key,
            )
            return {
                "saved": False,
                "section_key": section_key,
                "error": "内容未能持久化到数据库，请重试",
            }
        db.update_project(pid, last_section_key=section_key)
        return {"saved": True, "section_key": section_key}

    # ═══════════════════════════════════════════════════════════════
    # P0-1: 逐句自动补全 — Jenni AI 核心体验
    # ═══════════════════════════════════════════════════════════════

    @app.post("/api/scholar/autocomplete")
    async def api_autocomplete(req: dict):
        """逐句自动补全 — 根据当前章节上下文+RAG文献生成下一句建议

        请求体:
          project_id: int
          section_key: str
          text_before: str  — 光标前的文本
          section_title: str — 当前章节标题
        响应:
          { "suggestion": "...", "citation": [1,3] }
        """
        pid = req.get("project_id")
        section_key = req.get("section_key", "")
        text_before = req.get("text_before", "")
        section_title = req.get("section_title", "")

        if not pid or not text_before.strip():
            return {"suggestion": "", "citation": []}

        # 取最后 1200 字符作为上下文（控制 token）
        context_text = text_before[-1200:]

        # 获取项目文献
        from . import database as db
        proj = db.get_project(pid)
        if not proj:
            return {"suggestion": "", "citation": []}

        papers = db.list_literature(pid)
        if not papers:
            papers = []

        # RAG: 检索最相关的 3 篇文献（轻量级，只取标题+摘要）
        rag_context = ""
        citation_hints = []
        if papers:
            try:
                from vermes_cli.scholarforge.rag import PaperRetriever
                from vermes_cli.scholarforge.agents import PaperCard
                # 缓存 retriever（文献数不变时复用）
                cache_key = pid
                cached = _rag_cache.get(cache_key)
                if cached and cached[0] == len(papers):
                    rag = cached[1]
                else:
                    rag = PaperRetriever()
                    paper_cards = []
                    for p in papers[:20]:
                        paper_cards.append(PaperCard(
                            paper_id=p.get("paper_id", p.get("id", "")),
                            title=p.get("title", ""),
                            authors=p.get("authors", []),
                            year=p.get("year", ""),
                            venue=p.get("venue", ""),
                        abstract=p.get("abstract", ""),
                        citation_count=p.get("citation_count", 0),
                        url=p.get("url", ""),
                        source=p.get("source", ""),
                    ))
                    rag.load_papers(paper_cards)
                    _rag_cache[cache_key] = (len(papers), rag)
                results = rag.retrieve_for_writing(section_title, context_text, top_k=3)
                if results:
                    rag_context = "\n".join([
                        f"[{i+1}] {p.title} — {', '.join(p.authors[:2] if hasattr(p,'authors') and p.authors else [])} ({getattr(p,'year','')})"
                        for i, (p, _) in enumerate(results)
                    ])
                    citation_hints = [i+1 for i, _ in enumerate(results)]
            except Exception as e:
                logger.debug(f"Autocomplete RAG skipped: {e}")

        # 构建补全 prompt — 要求简洁、学术、只生成一句
        system = (
            "你是学术论文写作助手。根据当前上下文，生成1-2句续写内容。"
            "要求：学术风格、简洁、不重复已有内容、自然衔接。"
            "只输出续写的句子，不要输出已有内容。"
            "如果上下文是段落末尾，续写下一句；如果是段中，续写接续句。"
        )

        prompt = (
            f"当前章节：{section_title}\n\n"
            f"已有内容（末尾）：\n...{context_text}\n\n"
        )
        if rag_context:
            prompt += f"相关文献：\n{rag_context}\n\n"
        prompt += (
            "请生成1-2句续写。要求：\n"
            "1. 学术风格，与上下文自然衔接\n"
            "2. 如果引用文献，用 [n] 格式\n"
            "3. 只输出续写内容，不输出已有内容\n"
            "4. 不超过100字"
        )

        try:
            _llm = await _make_llm()
            suggestion = await _llm(prompt, system)
            # 清理：去掉可能的引号包裹
            suggestion = suggestion.strip().strip('"').strip("'").strip()
            # 限制长度
            if len(suggestion) > 200:
                # 截到最后一个句号
                cut = suggestion[:200].rsplit('。', 1)
                suggestion = cut[0] + '。' if len(cut) > 1 else cut[0]
            return {"suggestion": suggestion, "citation": citation_hints}
        except Exception as e:
            logger.debug(f"Autocomplete LLM failed: {e}")
            return {"suggestion": "", "citation": []}

    @app.delete("/api/scholar/projects/{pid}/section/{section_key}")
    async def api_delete_section(pid: int, section_key: str):
        """删除章节内容"""
        from . import database as db
        db.delete_section_content(pid, section_key)
        return {"ok": True}

    @app.post("/api/scholar/projects/{pid}/literature")
    async def api_add_literature(pid: int, req: dict):
        """项目内加文献"""
        from . import database as db
        lit_id = db.add_literature(pid, **req)
        return {"id": lit_id}

    @app.get("/api/scholar/projects/{pid}/literature")
    async def api_list_literature(pid: int):
        """列出项目内文献"""
        from . import database as db
        return {"literatures": db.list_literature(pid)}

    @app.delete("/api/scholar/literature/{lit_id}")
    async def api_delete_literature(lit_id: int):
        from . import database as db
        ok = db.delete_literature(lit_id)
        if not ok:
            raise HTTPException(404, "文献不存在")
        return {"deleted": True}

    # ── P1-8: BibTeX 批量导入 ──
    @app.post("/api/scholar/projects/{pid}/literature/import")
    async def api_import_bibtex(pid: int, req: dict):
        """批量导入 BibTeX 文本，解析后写入文献库"""
        from . import database as db
        import re
        bibtex_text = req.get("bibtex", "")
        if not bibtex_text.strip():
            raise HTTPException(400, "BibTeX 内容不能为空")
        
        added = 0
        skipped = 0
        entries = re.split(r'(?=@\w+\{)', bibtex_text)
        for entry in entries:
            entry = entry.strip()
            if not entry:
                continue
            m = re.match(r'@(\w+)\{(\w+)', entry)
            if not m:
                continue
            entry_type, cite_key = m.group(1).lower(), m.group(2)
            fields = {}
            for fk, pattern in [('title', r'title\s*=\s*[{"]([^}"]+)[}"]'),
                               ('author', r'author\s*=\s*[{"]([^}"]+)[}"]'),
                               ('year', r'year\s*=\s*[{"]?(\d{4})[}"]?'),
                               ('journal', r'(?:journal|booktitle)\s*=\s*[{"]([^}"]+)[}"]'),
                               ('doi', r'doi\s*=\s*[{"]?(10\.\d{4}/[^},\s]+)[}"]?'),
                               ('url', r'url\s*=\s*[{"]([^}"]+)[}"]')]:
                fm = re.search(pattern, entry, re.IGNORECASE)
                if fm:
                    fields[fk] = fm.group(1).strip().replace('{', '').replace('}', '')
            
            if not fields.get('title'):
                skipped += 1
                continue
            
            authors = [a.strip() for a in fields.get('author', '').split(' and ')] if fields.get('author') else []
            try:
                db.add_literature(pid,
                    title=fields['title'],
                    authors=authors,
                    year=int(fields['year']) if fields.get('year') else None,
                    venue=fields.get('journal', ''),
                    doi=fields.get('doi', ''),
                    url=fields.get('url', ''))
                added += 1
            except Exception as e:
                logger.debug(f"Literature import skipped: {e}")
                skipped += 1
        
        return {"added": added, "skipped": skipped, "total": added + skipped}

    @app.post("/api/scholar/projects/{pid}/literature/upload-pdf")
    async def api_upload_pdf(pid: int, file: UploadFile = File(...)):
        """上传 PDF 文件，提取文本并创建文献记录"""
        import os, tempfile, fitz
        from . import database as db

        # 验证文件类型
        if not file.filename or not file.filename.lower().endswith(".pdf"):
            raise HTTPException(400, "仅支持 PDF 文件")

        # 读取文件内容（50MB 限制）
        content = await file.read()
        if len(content) > 50 * 1024 * 1024:
            raise HTTPException(400, "文件大小超过 50MB 限制")

        # 保存到临时目录（消毒文件名防路径穿越）
        import uuid
        safe_name = f"{uuid.uuid4().hex[:8]}_{os.path.basename(file.filename)}"
        upload_dir = os.path.join(tempfile.gettempdir(), "scholarforge_uploads", str(pid))
        os.makedirs(upload_dir, exist_ok=True)
        filepath = os.path.join(upload_dir, safe_name)
        with open(filepath, "wb") as f:
            f.write(content)

        try:
            doc = fitz.open(filepath)
            # 提取元数据
            meta = doc.metadata or {}
            title = meta.get("title", "") or ""
            authors_str = meta.get("author", "") or ""
            authors = [a.strip() for a in authors_str.split(",") if a.strip()] if authors_str else []

            # 提取文本
            full_text = ""
            for page in doc:
                full_text += page.get_text()
            doc.close()

            # 如果元数据无标题，从正文启发式提取
            if not title:
                lines = [l.strip() for l in full_text.split("\n") if l.strip()]
                if lines:
                    # 取前几行中最长的作为标题
                    title = max(lines[:20], key=len) if lines[:20] else file.filename.replace(".pdf", "")

            # 启发式提取摘要
            abstract = ""
            import re
            abs_match = re.search(r'(?i)\babstract\b[:\s\n]*(.{50,500}?)(?:\n\n|\bkeywords?\b|\b1\.\s|introduction)', full_text, re.DOTALL)
            if abs_match:
                abstract = abs_match.group(1).strip()[:500]

            # 提取年份
            year = None
            year_match = re.search(r'\b(20\d{2})\b', full_text[:2000])
            if year_match:
                year = int(year_match.group(1))

            # 创建文献记录
            lit_id = db.add_literature(pid,
                title=title,
                authors=authors,
                year=year,
                venue="",
                doi="",
                url="",
                abstract=abstract)

            logger.info(f"[ScholarForge] PDF uploaded: {file.filename} → literature #{lit_id} ({len(full_text)} chars)")

            return {
                "id": lit_id,
                "title": title,
                "authors": authors,
                "year": year,
                "abstract": abstract,
                "text_length": len(full_text),
                "filename": file.filename,
            }
        except Exception as e:
            logger.error(f"PDF upload/parse failed: {e}", exc_info=True)
            raise HTTPException(500, f"PDF 解析失败: {e}")

    # ═══ 语义搜索 ═══
    @app.get("/api/scholar/projects/{pid}/literature/search")
    async def api_semantic_search_literature(pid: int, q: str = "", top_k: int = 20):
        """语义搜索项目文献 — TF-IDF 余弦相似度排序"""
        from . import database as db
        papers = db.list_literature(pid)
        if not papers:
            return {"results": []}
        if not q.strip():
            return {"results": [{"paper": p, "score": 0.0} for p in papers[:top_k]]}
        from vermes_cli.scholarforge.rag import semantic_search_literature
        results = semantic_search_literature(papers, q, top_k=top_k)
        return {"results": [{"paper": p, "score": round(s, 4)} for p, s in results]}

    # ═══ Collection/标签系统 ═══
    @app.get("/api/scholar/projects/{pid}/tags")
    async def api_list_tags(pid: int):
        """列出项目所有标签"""
        from . import database as db
        return {"tags": db.get_all_tags(pid)}

    @app.post("/api/scholar/literature/{lit_id}/tag")
    async def api_add_tag(lit_id: int, req: dict):
        """给文献添加标签"""
        from . import database as db
        tag = req.get("tag", "").strip()
        if not tag:
            raise HTTPException(400, "标签不能为空")
        ok = db.add_tag(lit_id, tag)
        return {"ok": ok, "tag": tag}

    @app.delete("/api/scholar/literature/{lit_id}/tag")
    async def api_remove_tag(lit_id: int, tag: str = ""):
        """移除文献标签"""
        from . import database as db
        if not tag:
            raise HTTPException(400, "标签不能为空")
        ok = db.remove_tag(lit_id, tag)
        return {"ok": ok}

    @app.get("/api/scholar/projects/{pid}/literature/tagged")
    async def api_list_literature_with_tags(pid: int, tag: str = ""):
        """获取文献列表（含标签），可按标签筛选"""
        from . import database as db
        lits = db.get_literature_with_tags(pid)
        if tag:
            lits = [l for l in lits if tag in l.get("tags", [])]
        return {"literatures": lits}

    @app.get("/api/scholar/projects/{pid}/messages")
    async def api_list_messages(pid: int, agent: str = None):
        """项目内 AI 对话历史"""
        from . import database as db
        return {"messages": db.list_messages(pid, agent)}

    @app.post("/api/scholar/projects/{pid}/messages")
    async def api_add_message(pid: int, req: dict):
        """保存项目内 AI 对话"""
        from . import database as db
        msg_id = db.add_message(
            pid,
            req.get("agent", "general"),
            req.get("role", "user"),
            req.get("content", ""),
        )
        return {"id": msg_id}

    @app.delete("/api/scholar/projects/{pid}/messages")
    async def api_clear_messages(pid: int, agent: str = None):
        """清空项目内对话历史"""
        from . import database as db
        n = db.clear_messages(pid, agent)
        return {"cleared": n}

    # ── P1-5: 版本快照 ──
    @app.get("/api/scholar/projects/{pid}/snapshots")
    async def api_list_snapshots(pid: int):
        """列出项目的所有快照（倒序，轻量元信息）"""
        from . import database as db
        return {"snapshots": db.list_snapshots(pid)}

    @app.post("/api/scholar/projects/{pid}/snapshots")
    async def api_create_snapshot(pid: int, req: dict):
        """创建快照，保存当前全文 + overview"""
        from . import database as db
        data = req.get("data", {})
        sid = db.create_snapshot(pid,
            label=req.get("label", ""),
            note=req.get("note", ""),
            data=data)
        return {"id": sid, "label": req.get("label", "")}

    @app.get("/api/scholar/snapshots/{sid}")
    async def api_get_snapshot(sid: int):
        """获取单个快照详情（含全文 payload）"""
        from . import database as db
        snap = db.get_snapshot(sid)
        if not snap:
            raise HTTPException(404, "快照不存在")
        return snap

    @app.delete("/api/scholar/snapshots/{sid}")
    async def api_delete_snapshot(sid: int):
        """删除快照"""
        from . import database as db
        db.delete_snapshot(sid)
        return {"deleted": True}

    @app.post("/api/scholar/snapshots/{sid}/restore")
    async def api_restore_snapshot(sid: int):
        """从快照恢复项目状态（大纲+章节内容+项目元信息）"""
        from . import database as db
        result = db.restore_snapshot(sid)
        if result.get("error"):
            raise HTTPException(400, result["error"])
        return result

    @app.get("/api/scholar/projects/{pid}/citation-verifications")
    async def api_get_citation_verifications(pid: int):
        """获取项目的引用验证结果（持久化，刷新后保留）"""
        from . import database as db
        verifications = db.get_citation_verifications(pid)
        return {"verifications": verifications}

    @app.get("/api/scholar/projects/{pid}/citation-style")
    async def get_citation_style(pid: int):
        """获取项目的引用格式"""
        from . import database as db
        proj = db.get_project(pid)
        if not proj:
            raise HTTPException(404, "项目不存在")
        style = proj.get("citation_style", "gbt7714") or "gbt7714"
        return {"style": style, "available_styles": [
            {"value": "gbt7714", "label": "GB/T 7714-2015"},
            {"value": "apa", "label": "APA 7th"},
            {"value": "mla", "label": "MLA 9th"},
            {"value": "ieee", "label": "IEEE"},
            {"value": "chicago", "label": "Chicago 17th"},
            {"value": "vancouver", "label": "Vancouver"},
        ]}

    @app.put("/api/scholar/projects/{pid}/citation-style")
    async def set_citation_style(pid: int, body: dict = None):
        """设置项目的引用格式"""
        from . import database as db
        if not body or "style" not in body:
            raise HTTPException(400, "缺少 style 字段")
        style = body["style"]
        from vermes_cli.scholarforge.citation_provider import CITATION_STYLES
        if style not in CITATION_STYLES:
            raise HTTPException(400, f"不支持的引用格式: {style}")
        db.update_project(pid, citation_style=style)
        return {"style": style}

    @app.get("/api/scholar/projects/{pid}/references")
    async def get_references(pid: int, style: str = None):
        """按指定格式获取项目的参考文献列表"""
        from . import database as db
        from vermes_cli.scholarforge.citation_provider import format_references_list, CITATION_STYLES
        proj = db.get_project(pid)
        if not proj:
            raise HTTPException(404, "项目不存在")
        # 优先用查询参数，回退到项目设置
        if not style:
            style = proj.get("citation_style", "gbt7714") or "gbt7714"
        if style not in CITATION_STYLES:
            style = "gbt7714"
        papers = proj.get("literatures", []) or []
        formatted = format_references_list(papers, style)
        from vermes_cli.scholarforge.citation_provider import format_citation as _fmt_cit
        return {
            "style": style,
            "count": len(papers),
            "formatted": formatted,
            "references": [_fmt_cit(p, style, i+1) for i, p in enumerate(papers)],
        }

    @app.get("/api/scholar/agents")
    async def list_agents():
        """列出所有论文写作 Agent"""
        from vermes_cli.scholarforge.agents import AGENTS

        return {
            "agents": [agent_cls.to_dict() for agent_cls in AGENTS.values()]
        }

    @app.get("/api/scholar/search")
    async def search_literature_get(query: str, limit: int = 10, sources: str = ""):
        """多源文献搜索（GET）"""
        from vermes_cli.scholarforge.search import search_papers

        source_list = [s.strip() for s in sources.split(",") if s.strip()] if sources else None

        results = []
        sources_used = []
        try:
            async for paper in search_papers(query, limit=limit, sources=source_list):
                results.append(paper.to_dict())
                if paper.source not in sources_used:
                    sources_used.append(paper.source)
        except Exception as e:
            logger.error(f"Search error: {e}")
            return {"results": [], "error": str(e)}

        return {"query": query, "results": results, "count": len(results), "sources": sources_used}

    @app.post("/api/scholar/search")
    async def search_literature_post(req: LiteratureSearchRequest):
        """文献搜索（POST）"""
        from vermes_cli.scholarforge.search import search_papers

        results = []
        sources_used = []
        try:
            async for paper in search_papers(req.query, limit=req.limit):
                results.append(paper.to_dict())
                if paper.source not in sources_used:
                    sources_used.append(paper.source)
        except Exception as e:
            logger.error(f"Search error: {e}")
            return {"query": req.query, "results": [], "sources": [], "error": str(e)}

        return {
            "query": req.query,
            "results": results,
            "count": len(results),
            "sources": sources_used,
        }

# ── P1-1: SSE 速率限制 ──

    _stream_rate_limiter: dict[str, float] = {}  # client_id → last_yield_ts

    async def _sse_rate_limit(ctx_id: str, interval_ms: int = 30):
        """确保同一 ctx 的两次 yield 之间至少间隔 interval_ms"""
        now = time.monotonic()
        last = _stream_rate_limiter.get(ctx_id, 0)
        gap = now - last
        if gap < interval_ms / 1000:
            await asyncio.sleep((interval_ms / 1000) - gap)
            now = time.monotonic()
        _stream_rate_limiter[ctx_id] = now

    async def _sse_rl(data: dict, ctx_id: str) -> str:
        """带速率限制的 SSE 格式化"""
        await _sse_rate_limit(ctx_id)
        return _sse(data)

    @app.post("/api/scholar/stream")
    async def scholar_stream(req: ScholarChatRequest):
        """论文写作 SSE 流式接口 — 每个 Agent 独立模型，支持 STORM 全链路"""
        from . import database as db

        if not req.message.strip():
            raise HTTPException(400, "消息不能为空")

        ctx = await _get_ctx(str(req.project_id or "default"), client_id=req.client_id or "")
        pid = int(req.project_id or 0)

        # 加载项目信息（target_words 等）
        if pid > 0:
            try:
                _proj = db.get_project(pid)
                if _proj:
                    ctx.target_words = int(_proj.get("target_words", 8000))
                    ctx.paper_type = _proj.get("paper_type", ctx.paper_type)
            except Exception as e:
                logger.debug(f"Context restore from DB failed: {e}", exc_info=True)

        # 加载项目 Agent-Provider 绑定（自动智能分配模型）
        agent_cfg = _resolve_agent_providers(pid)

        client_id = req.client_id or ""

        async def generate():
            _client_connected = True
            try:
                if req.pipeline:
                    use_checkpoint = req.checkpoint and req.pipeline

                    # ── 构建 ScholarForge pipeline ──
                    from vermes_cli.scholarforge.agents import AGENTS as _AGENTS

                    _STAGE_LABELS = {
                        "topic": "选题分析", "literature": "文献综述",
                        "outline": "论文大纲", "writing": "章节撰写",
                        "refinement": "润色检查", "reviewer": "审稿",
                    }

                    def _outline_hook(ctx, stage_name):
                        if stage_name == "outline" and pid > 0:
                            try:
                                outline_data = ctx.outline.get("sections", []) if isinstance(ctx.outline, dict) else []
                                if outline_data:
                                    if not db.save_outline(pid, outline_data):
                                        logger.error(
                                            "_outline_hook: save_outline failed (pid=%s, %d sections)",
                                            pid, len(outline_data),
                                        )
                            except Exception as e:
                                logger.debug(f"Failed to save outline after pipeline: {e}")

                    def _writing_hook(ctx, stage_name):
                        if stage_name == "writing" and pid > 0:
                            try:
                                db.save_section_content(pid, "full_paper", ctx.draft or "")
                            except Exception as e:
                                logger.debug(f"Failed to save full paper after pipeline: {e}")

                    _pipeline = Pipeline(stages=[
                        Stage("topic", _AGENTS["topic"], label=_STAGE_LABELS["topic"]),
                        Stage("literature", _AGENTS["literature"], label=_STAGE_LABELS["literature"], depth_kwarg="depth"),
                        Stage("outline", _AGENTS["outline"], label=_STAGE_LABELS["outline"], post_hooks=[_outline_hook]),
                        Stage("writing", _AGENTS["writing"], label=_STAGE_LABELS["writing"], section_kwarg="section", post_hooks=[_writing_hook]),
                        Stage("refinement", _AGENTS["refinement"], label=_STAGE_LABELS["refinement"]),
                        Stage("reviewer", _AGENTS["reviewer"], label=_STAGE_LABELS["reviewer"]),
                    ])

                    # P1: 加载已有项目上下文（continue_from 时恢复）
                    if req.continue_from and req.continue_from in _pipeline.stage_names and pid > 0:
                        try:
                            p = db.get_project(pid)
                            if p:
                                ctx.topic = p.get("title", "")
                                ctx.paper_type = p.get("paper_type", "")
                                ctx.target_words = int(p.get("target_words", 8000))
                            outline_data = db.get_outline(pid)
                            if outline_data:
                                ctx.outline = {"sections": outline_data}
                            from vermes_cli.scholarforge.agents import PaperCard
                            lit_rows = db.list_literature(pid)
                            for lr in lit_rows:
                                ctx.add_paper(PaperCard(
                                    paper_id=str(lr.get("paper_id", lr.get("id", ""))),
                                    title=lr.get("title", ""),
                                    authors=lr.get("authors", []) if isinstance(lr.get("authors"), list) else [],
                                    year=str(lr.get("year", "")),
                                    venue=lr.get("venue", ""),
                                    abstract=lr.get("abstract", ""),
                                    url=lr.get("url", ""),
                                    source=lr.get("source", ""),
                                ))
                            sections = db.get_all_sections(pid)
                            if sections:
                                draft_parts = []
                                for sk, content in sections.items():
                                    ctx.section_contents[sk] = content
                                    draft_parts.append(content)
                                if draft_parts:
                                    ctx.draft = "\n\n".join(draft_parts)
                        except Exception as e:
                            logging.warning(f"Failed to restore context for continue_from: {e}")

                    # ── make_agent callback ──
                    async def _make_stage_agent(stage, ctx):
                        cfg = agent_cfg.get(stage.name, {})
                        agent_llm = await _make_llm(cfg.get("provider"), cfg.get("model"))
                        return stage.agent_cls(ctx, agent_llm)

                    # ── is_disconnected callback ──
                    async def _check_disconnect():
                        return await request.is_disconnected()

                    # ── 执行 pipeline ──
                    _config = PipelineConfig(
                        checkpoint=use_checkpoint,
                        continue_from=req.continue_from,
                    )
                    _extra_kwargs = {}
                    if req.section:
                        _extra_kwargs["section"] = req.section
                    if req.depth:
                        _extra_kwargs["depth"] = req.depth

                    async for _evt in _pipeline.run(
                        ctx, _config, _make_stage_agent,
                        user_input=req.message,
                        extra_kwargs=_extra_kwargs,
                        is_disconnected=_check_disconnect,
                        stage_labels=_STAGE_LABELS,
                    ):
                        yield await _sse_rl(_evt, client_id)

                    # Pipeline 完成后自动尝试替换伪引用为真实文献
                    citations_replaced = False
                    if ctx.draft and ctx.topic:
                        yield await _sse_rl({"type": "thinking", "message": "🔍 检索真实文献替换伪引用..."}, client_id)
                        try:
                            from vermes_cli.scholarforge.citation_provider import replace_pseudo_citations
                            new_draft, real_citations = await replace_pseudo_citations(
                                ctx.draft, ctx.topic,
                                keywords=ctx.papers[:5] if ctx.papers else [],
                                paper_type=ctx.paper_type)
                            if real_citations:
                                ctx.draft = new_draft
                                citations_replaced = True
                                yield await _sse_rl({"type": "citation",
                                             "message": f"已替换为 {len(real_citations)} 篇真实文献",
                                             "count": len(real_citations)}, client_id)
                        except Exception as e:
                            logger.warning(f"Real citation replacement failed: {e}")

                    yield await _sse_rl({"type": "done", "pipeline": "complete",
                                 "papers": len(ctx.papers),
                                 "citations_replaced": citations_replaced}, client_id)
                else:
                    from vermes_cli.scholarforge.agents import AGENTS
                    agent_cls = AGENTS.get(req.agent)
                    if not agent_cls:
                        agent_cls = AGENTS["topic"]

                    agent_name = req.agent or "topic"
                    cfg = agent_cfg.get(agent_name, {})
                    agent_llm = await _make_llm(cfg.get("provider"), cfg.get("model"))
                    agent = agent_cls(ctx, agent_llm)
                    kwargs = {"user_input": req.message}
                    if req.section and agent_name in ("writing",):
                        kwargs["section"] = req.section
                    if req.depth and agent_name == "literature":
                        kwargs["depth"] = req.depth
                    # 保存用户消息到DB
                    if pid > 0:
                        try:
                            db.add_message(pid, "user", req.message, agent_name)
                        except Exception as e:
                            logger.debug(f"Failed to save user message to DB: {e}")
                    _agent_content_parts = []
                    async for evt in agent.run(**kwargs):
                        yield await _sse_rl(evt, client_id)
                        # 收集Agent回复内容
                        if evt.get("type") == "content" and evt.get("text"):
                            _agent_content_parts.append(evt["text"])
                        # 非pipeline模式：大纲Agent完成后保存到DB
                        if evt.get("type") == "outline" and agent_name == "outline" and pid > 0:
                            try:
                                outline_sections = evt.get("sections", [])
                                if outline_sections:
                                    if not db.save_outline(pid, outline_sections):
                                        logger.error(
                                            "outline event: save_outline failed (pid=%s, %d sections)",
                                            pid, len(outline_sections),
                                        )
                            except Exception as e:
                                logger.warning(f"Failed to save outline to DB: {e}")
                    # 保存Agent回复消息到DB
                    if pid > 0 and _agent_content_parts:
                        try:
                            db.add_message(pid, "assistant", "".join(_agent_content_parts), agent_name)
                        except Exception as e:
                            logger.debug(f"Failed to save agent reply to DB: {e}")

            except asyncio.CancelledError:
                logger.info(f"[ScholarForge] Stream cancelled by client (ctx_id={ctx.ctx_id})")
                return
            except HTTPException as e:
                yield _sse({"type": "error", "message": str(e.detail)})
            except Exception as e:
                logger.error(f"Scholar stream error: {e}", exc_info=True)
                yield _sse({"type": "error", "message": str(e)})
            finally:
                _stream_rate_limiter.pop(client_id, None)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            }
        )

    # ── 段落级内联编辑 (Phase 1.1 — 学 Jenni AI) ──
    @app.post("/api/scholar/inline-edit")
    async def inline_edit(req: dict):
        """选中段落 → 改写/扩写/缩写/润色/翻译，不走 SSE，直接返回编辑后文本"""
        text = req.get("text", "").strip()
        action = req.get("action", "polish")  # polish/rewrite/expand/shorten/translate-en
        if not text:
            raise HTTPException(400, "text 不能为空")
        if len(text) > 8000:
            raise HTTPException(400, "文本过长（最多8000字符）")

        prompts = {
            "polish": f"你是中文学术写作助手。请润色以下段落，提升学术表达和流畅度，保持原意不变。直接输出润色后的段落，不要加任何解释：\n\n{text}",
            "rewrite": f"你是中文学术写作助手。请改写以下段落，用不同的表达方式重述相同内容，保持原意不变。直接输出改写后的段落，不要加任何解释：\n\n{text}",
            "expand": f"你是中文学术写作助手。请对以下段落进行扩展论述，增加论据、例证或数据支撑，使其更充实。直接输出扩展后的段落，不要加任何解释：\n\n{text}",
            "shorten": f"你是中文学术写作助手。请精简以下段落，保留核心论点和关键证据，去除冗余表述。直接输出精简后的段落，不要加任何解释：\n\n{text}",
            "translate-en": f"你是学术翻译助手。请将以下中文学术段落翻译成英文，保持学术严谨性。直接输出英文翻译，不要加任何解释：\n\n{text}",
        }

        prompt = prompts.get(action)
        if not prompt:
            raise HTTPException(400, f"未知操作: {action}，支持 polish/rewrite/expand/shorten/translate-en")

        llm = await _make_llm()
        try:
            result = await llm(prompt)
            return {"text": result.strip()}
        except Exception as e:
            raise HTTPException(502, f"内联编辑失败: {e}")

    @app.get("/api/scholar/sources")
    async def list_sources():
        """列出可用文献搜索源"""
        from vermes_cli.scholarforge.search import get_available_sources, get_paid_source_configs

        free_sources = get_available_sources()
        paid_sources = await get_paid_source_configs()
        return {
            "free_sources": free_sources,
            "paid_sources": paid_sources,
        }

    @app.get("/api/scholar/sources/connectivity")
    async def sources_connectivity():
        """检查各免费搜索源的可达性"""
        from vermes_cli.scholarforge.search import get_configured_sources
        return await get_configured_sources()

    @app.get("/api/scholar/sources/paid")
    async def paid_sources_list():
        """返回付费源配置列表"""
        from vermes_cli.scholarforge.search import get_paid_source_configs
        configs = await get_paid_source_configs()
        return {"sources": configs}

    @app.post("/api/scholar/sources/activate")
    async def activate_source_endpoint(req: dict):
        """激活付费文献源（用户提供 API Key）
        CNKI 需要额外提供 gateway_url
        """
        source_name = req.get("source")
        api_key = req.get("api_key", "")
        gateway_url = req.get("gateway_url", "")
        username = req.get("username", "")
        password = req.get("password", "")
        if not source_name or not (api_key or (username and password)):
            raise HTTPException(400, "source 与 (api_key 或 username+password) 必填")

        from vermes_cli.scholarforge.search import activate_paid_source

        ok = await activate_paid_source(source_name, api_key, gateway_url, username, password)
        if not ok:
            return {"status": "error", "message": f"来源 '{source_name}' 激活失败"}
        return {"status": "ok", "source": source_name}

    # ═══════════════════════════════════════════════════════════════
    # Agent-Provider 绑定 — 每个 Agent 独立选择厂商和模型
    # ═══════════════════════════════════════════════════════════════

    @app.get("/api/scholar/providers")
    async def list_available_providers():
        """列出 Vermes 已配置的所有厂商（供 Agent 模型分配使用）— 复用聊天链路凭证"""
        from vermes_cli.blueprints.chat import PROVIDERS
        from vermes_constants import get_vermes_home
        available = []
        env_path = get_vermes_home() / ".env"
        env_lines = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
        for key, info in PROVIDERS.items():
            env_key_name = info.get("env_key", "")
            has_key = False
            if env_key_name:
                for line in env_lines.splitlines():
                    line = line.strip()
                    if line.startswith(f"{env_key_name}="):
                        val = line.split("=", 1)[1].strip().strip('"').strip("'")
                        if val:
                            has_key = True
                        break
            available.append({
                "key": key,
                "name": key,
                "label": key,
                "recommended": info.get("recommended", False),
                "has_key": has_key,
            })
        return {"providers": available}

    @app.get("/api/scholar/projects/{pid}/agent-providers")
    async def api_get_agent_providers(pid: int):
        """获取项目各 Agent 的模型分配"""
        from . import database as db
        cfg = db.get_agent_providers(pid)
        agents = []
        for name in db.SCHOLAR_AGENTS:
            ap = cfg.get(name, {})
            agents.append({
                "agent": name,
                "provider": ap.get("provider", ""),
                "model": ap.get("model", ""),
            })
        return {"agents": agents}

    @app.post("/api/scholar/projects/{pid}/agent-providers")
    async def api_set_agent_provider(pid: int, req: dict):
        """为项目某个 Agent 设置 provider/model"""
        from . import database as db
        agent = req.get("agent")
        if agent not in db.SCHOLAR_AGENTS:
            raise HTTPException(400, f"无效的 Agent: {agent}")
        db.set_agent_provider(pid, agent, req.get("provider", ""), req.get("model", ""))
        return {"ok": True}

    @app.get("/api/scholar/projects/{pid}/export")
    async def export_paper(
        pid: int,
        project_id: str = None,
        format: str = "markdown",
        title: str = "未命名论文",
        client_id: str = "",
        template: str = "ieee",
    ):
        """导出论文为 Markdown/BibTeX/LaTeX/Word/PDF
        
        内容来源优先级：数据库 section_contents > 内存 ctx.draft
        这样手动编辑和 STORM 生成的内容都能正确导出。
        """
        from . import database as db
        ctx = await _get_ctx(str(pid), client_id=client_id)
        content = ctx.draft or ""
        papers = ctx.papers or []
        abstract = ctx.abstract if hasattr(ctx, "abstract") else ""

        # 从数据库补全/覆盖内容（手动编辑的内容在 section_contents 表）
        try:
            if pid > 0:
                proj = db.get_project(pid)
                if proj:
                    # 用项目标题作为默认导出标题
                    if not title or title == "未命名论文":
                        title = proj.get("title") or title
                    db_contents = proj.get("contents", {})
                    if db_contents:
                        # 数据库有编辑内容，按章节顺序拼接
                        outline = proj.get("outline") or []
                        if outline:
                            sections = []
                            for sec in outline:
                                sec_id = sec.get("section_key") or sec.get("id", "")
                                sec_title = sec.get("section_title") or sec.get("title", "")
                                sec_num = sec.get("section_number") or sec.get("number", "")
                                body = db_contents.get(sec_id, "")
                                if body.strip():
                                    heading = f"## {sec_num} {sec_title}" if sec_num else f"## {sec_title}"
                                    sections.append(f"{heading}\n\n{body}")
                            if sections:
                                content = "\n\n".join(sections)
                        else:
                            # 无大纲，直接拼接所有内容
                            content = "\n\n".join(v for v in db_contents.values() if v and v.strip())
                    # 文献
                    db_papers = proj.get("literatures") or []
                    if db_papers and not papers:
                        papers = [
                            type('P', (), {
                                'title': l.get('title',''), 'authors': l.get('authors',[]),
                                'year': l.get('year',''), 'venue': l.get('venue',''),
                                'to_dict': lambda s, l=l: {
                                    'title': l.get('title',''), 'authors': l.get('authors',[]),
                                    'year': l.get('year',''), 'venue': l.get('venue',''),
                                }
                            })() for l in db_papers
                        ]
        except Exception as e:
            logger.warning(f"[ScholarForge export] DB fallback failed: {e}")

        # ── PDF 二进制 ──
        if format == "pdf":
            from fastapi.responses import Response
            from vermes_cli.scholarforge.export.full import export_pdf
            try:
                from urllib.parse import quote
                pdf_bytes = export_pdf(title, content, papers, abstract=abstract)
                # 检测是否为 HTML fallback（非 PDF 二进制）
                is_html_fallback = pdf_bytes[:5] in (b'<!DOC', b'<html', b'<!doc')
                if is_html_fallback:
                    return Response(
                        content=pdf_bytes,
                        media_type="text/html; charset=utf-8",
                    )
                filename_ascii = "paper.pdf"
                filename_star = quote(f"{title}.pdf", safe="")
                return Response(
                    content=pdf_bytes,
                    media_type="application/pdf",
                    headers={
                        "Content-Disposition": (
                            f"attachment; "
                            f'filename="{filename_ascii}"; '
                            f"filename*=UTF-8''{filename_star}"
                        ),
                    },
                )
            except Exception as e:
                logger.error(f"PDF export failed: {e}", exc_info=True)
                raise HTTPException(500, f"PDF 生成失败: {e}")

        # ── Word 二进制 ──
        if format == "word" or format == "docx":
            from fastapi.responses import Response
            from vermes_cli.scholarforge.export.full import export_docx
            try:
                from urllib.parse import quote
                docx_bytes = export_docx(title, content, papers, abstract=abstract)
                filename_ascii = "paper.docx"
                filename_star = quote(f"{title}.docx", safe="")
                return Response(
                    content=docx_bytes,
                    media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    headers={
                        "Content-Disposition": (
                            f"attachment; "
                            f'filename="{filename_ascii}"; '
                            f"filename*=UTF-8''{filename_star}"
                        ),
                    },
                )
            except Exception as e:
                logger.error(f"Word export failed: {e}", exc_info=True)
                raise HTTPException(500, f"Word 生成失败: {e}")

        # ── 纯文本格式 ──
        if format == "bibtex":
            from vermes_cli.scholarforge.export import format_export_bibtex
            return {
                "format": "bibtex",
                "content": format_export_bibtex(papers),
                "count": len(papers),
            }
        if format == "references" or format == "bib":
            from vermes_cli.scholarforge.export import extract_references, format_export_bibtex
            refs = extract_references(content)
            all_papers = list(papers) + [
                type('Paper', (), {'title': r.title, 'authors': r.authors, 'year': r.year, 'venue': r.venue, 'doi': getattr(r, 'doi', ''), 'url': ''})()
                for r in refs
            ]
            return {
                "format": format,
                "references_yaml": [{'title': r.title, 'authors': r.authors, 'year': r.year, 'venue': r.venue} for r in refs],
                "bibtex": format_export_bibtex(all_papers),
                "count": len(all_papers),
            }
        if format == "latex":
            from vermes_cli.scholarforge.export.latex import format_export_latex
            try:
                latex = format_export_latex(title, content, papers, template=template)
                return {"format": "latex", "template": template, "content": latex}
            except ValueError as e:
                raise HTTPException(400, str(e))
        else:
            from vermes_cli.scholarforge.export import format_export_markdown
            return {
                "format": "markdown",
                "content": format_export_markdown(title, content, papers),
            }

    @app.get("/api/scholar/projects/{pid}/score")
    async def score_project(pid: int):
        """论文评分 — 原创性/逻辑性/引用完整性 三维度 AI 评估"""
        from vermes_cli.scholarforge.scoring import score_paper
        from . import database as db
        proj = db.get_project(pid)
        if not proj:
            raise HTTPException(404, "项目不存在")
        content = "\n\n".join(proj.get("contents", {}).values()) or ""
        if not content.strip():
            raise HTTPException(400, "论文内容为空，请先写作再评分")
        papers = [
            type('P', (), {'title': l.get('title',''), 'authors': l.get('authors',[]), 'year': l.get('year','')})()
            for l in (proj.get("literatures", []) or [])
        ]
        llm_factory = await _make_llm()
        result = await score_paper(content, papers, _make_llm=lambda: llm_factory, topic=proj.get("title", ""))
        return result

    @app.post("/api/scholar/projects/{pid}/consensus")
    async def consensus_score(pid: int, req: dict):
        """共识度评分 — 给定论断，评估多篇文献的支持/反对/中立比例"""
        from vermes_cli.scholarforge.scoring import score_consensus, extract_key_claims
        from . import database as db
        proj = db.get_project(pid)
        if not proj:
            raise HTTPException(404, "项目不存在")
        claim = req.get("claim", "").strip()
        # 如果没有指定 claim，自动提取关键论断
        claims = []
        if not claim:
            content = "\n\n".join(proj.get("contents", {}).values()) or ""
            if content.strip():
                llm_factory = await _make_llm()
                claims = await extract_key_claims(content, llm=lambda: llm_factory)
        else:
            claims = [claim]
        if not claims:
            raise HTTPException(400, "未提供论断且无法从论文中提取")
        papers = [
            type('P', (), {'title': l.get('title',''), 'authors': l.get('authors',[]),
                           'year': l.get('year',''), 'abstract': l.get('abstract','')})()
            for l in (proj.get("literatures", []) or [])
        ]
        llm_factory = await _make_llm()
        results = []
        for c in claims[:5]:
            r = await score_consensus(c, papers, llm=llm_factory)
            results.append(r)
        return {"results": results}

    @app.get("/api/scholar/projects/{pid}/claims")
    async def extract_claims(pid: int):
        """提取论文关键论断"""
        from vermes_cli.scholarforge.scoring import extract_key_claims
        from . import database as db
        proj = db.get_project(pid)
        if not proj:
            raise HTTPException(404, "项目不存在")
        content = "\n\n".join(proj.get("contents", {}).values()) or ""
        if not content.strip():
            raise HTTPException(400, "论文内容为空")
        llm_factory = await _make_llm()
        claims = await extract_key_claims(content, llm=llm_factory)
        return {"claims": claims}

    # ═══════════════════════════════════════════════════════════════
    # P0-2: 查重 + AIGC 检测
    # ═══════════════════════════════════════════════════════════════

    @app.post("/api/scholar/projects/{pid}/plagcheck")
    async def api_plagiarism_check(pid: int, req: dict = None):
        """全量查重 + AIGC 检测，对标 Paperpal(Turnitin) + 千笔AI
        
        POST body:
            content: str — 直接传入的文本（优先）
            若未传 content，则从项目章节内容拼接
        
        返回:
            overall_similarity: 综合重复率 0~1
            aigc_overall_ratio: AI痕迹占比 0~1
            plag_results: 重复段落列表
            aigc_results: AI痕迹段落列表
            suggestions: 改进建议
        """
        from vermes_cli.scholarforge.plagcheck import full_plagiarism_check
        from . import database as db

        content = (req or {}).get("content", "").strip()
        if not content:
            proj = db.get_project(pid)
            if not proj:
                raise HTTPException(404, "项目不存在")
            content = "\n\n".join(proj.get("contents", {}).values()) or ""
        if not content.strip():
            raise HTTPException(400, "论文内容为空")

        try:
            report = full_plagiarism_check(content, title=(req or {}).get("title", ""))
            # 序列化 dataclass → dict
            return {
                "total_chars": report.total_chars,
                "total_paragraphs": report.total_paragraphs,
                "overall_similarity": report.overall_similarity,
                "aigc_overall_ratio": report.aigc_overall_ratio,
                "plag_results": [
                    {"text": r.text, "length": r.length, "score": r.score, "source": r.source}
                    for r in report.plag_results[:10]
                ],
                "aigc_results": [
                    {"text": r.text, "aigc_probability": r.aigc_probability, "features": r.features}
                    for r in report.aigc_results[:10]
                ],
                "suggestions": report.suggestions,
            }
        except Exception as e:
            logger.error(f"Plagcheck failed: {e}", exc_info=True)
            raise HTTPException(500, f"查重检测失败: {e}")

    # ═══════════════════════════════════════════════════════════════
    # De-AIGC 一键降重
    # ═══════════════════════════════════════════════════════════════

    @app.post("/api/scholar/projects/{pid}/deaigc-rewrite")
    async def api_deaigc_rewrite(pid: int, req: dict = None):
        """一键 De-AIGC 自动改写（规则化，零 LLM 调用）

        四字套话 → 自然表达，绝对化 → 软化，连接词精简
        返回改写后文本 + AI率变化统计
        """
        from vermes_cli.scholarforge.plagcheck import check_aigc, apply_deaigc_suggestions

        content = (req or {}).get("content", "").strip()
        if not content:
            from . import database as db
            proj = db.get_project(pid)
            if not proj:
                raise HTTPException(404, "项目不存在")
            content = "\n\n".join(proj.get("contents", {}).values()) or ""
        if not content.strip():
            raise HTTPException(400, "论文内容为空")

        before = check_aigc(content)
        rewritten = apply_deaigc_suggestions(content)
        after = check_aigc(rewritten)

        return {
            "original": content,
            "rewritten": rewritten,
            "stats": {
                "aigc_before": round(before["overall_ratio"], 4),
                "aigc_after": round(after["overall_ratio"], 4),
                "aigc_reduction": round(before["overall_ratio"] - after["overall_ratio"], 4),
                "aigc_reduction_pct": round(
                    (before["overall_ratio"] - after["overall_ratio"]) /
                    max(before["overall_ratio"], 0.001) * 100, 1
                ),
            },
        }

    # ═══════════════════════════════════════════════════════════════
    # P0-3: 逐段/逐节迭代修改
    # ═══════════════════════════════════════════════════════════════

    @app.post("/api/scholar/projects/{pid}/rewrite-section")
    async def api_rewrite_section(pid: int, req: dict):
        """逐段修改 — 对标 Jenni AI / 千笔无限改稿
        
        支持多种修改模式：
            section_key: 目标章节key（必填）
            instruction: 用户修改指令（选填，如"增加数据支撑""更口语化"）
            mode: polish / expand / shorten / restructure / add_data / academic / plain
                  默认 polish（润色）
            返回 SSE 流式响应
        """
        import re
        from . import database as db
        proj = db.get_project(pid)
        if not proj:
            raise HTTPException(404, "项目不存在")

        section_key = req.get("section_key", "")
        instruction = req.get("instruction", "")
        mode = req.get("mode", "polish")
        
        # 从项目章节内容中取当前章节
        contents = proj.get("contents", {})
        original_text = contents.get(section_key, "")
        if not original_text.strip():
            # fallback: 从 draft 字符串中按 ## 匹配
            draft = proj.get("draft", "")
            # 尝试匹配 ## 标题
            sections = re.split(r'\n(?=## )', draft)
            for s in sections:
                if s.strip().startswith(f"## "):
                    body = s.split("\n", 1)[1] if "\n" in s else ""
                    if body.strip():
                        original_text = body.strip()
                        break
        if not original_text.strip():
            raise HTTPException(400, f"章节 {section_key} 内容为空")

        # 修改模式 → prompt 映射
        mode_prompts = {
            "polish": "进行学术润色，提升表达严谨性和流畅度，保持原意和长度不变",
            "expand": "进行扩展论述，增加论据、数据或案例支撑，使内容更充实。可适当增加篇幅",
            "shorten": "进行精简，删除冗余表述和陈词，保留核心论点和关键证据。压缩至原长度的 60-70%",
            "restructure": "重组段落结构和逻辑顺序，使论证更清晰有力。可以调整论点顺序、合并或拆分段落",
            "add_data": "为论述补充具体数据、统计数字或实验结果。如果无法提供真实数据，标注 [需补充数据]",
            "academic": "将文本改写为更正式、更学术化的表达，增加专业术语使用，提升学术档次",
            "plain": "将文本改写为更通俗易懂的表达，降低阅读门槛，适合本科课程论文",
        }

        mode_instruction = mode_prompts.get(mode, mode_prompts["polish"])
        extra = f"\n\n额外要求：{instruction}" if instruction else ""

        override_prompt = f"""你是中文学术编辑。对以下论文段落{mode_instruction}。{extra}

【重要规则】
- 直接输出修改后的段落，不要加任何解释和前缀
- 保持 Markdown 格式
- 保留原有的引用标记 [n]
- 不要编造新的文献引用

【原文】
{original_text}

【修改后】"""

        async def generate():
            client_id = req.get("client_id", "")
            yield await _sse_rl({"type": "thinking", "message": f"正在修改章节 ({mode})..."}, client_id)
            try:
                llm = await _make_llm()
                result = await llm(override_prompt)
                result = result.strip()
                for noise in ["修改后的段落如下", "以下是修改后的", "改写如下", "输出如下"]:
                    if result.startswith(noise):
                        result = result[len(noise):].strip(":： \n")
                        break
                yield await _sse_rl({"type": "rewrite_done", "section_key": section_key,
                            "text": result, "mode": mode,
                            "original_length": len(original_text),
                            "new_length": len(result)}, client_id)
            except asyncio.CancelledError:
                logger.info(f"[ScholarForge] Rewrite stream cancelled by client (section={section_key})")
                return
            except Exception as e:
                yield _sse({"type": "error", "message": str(e)})
            finally:
                _stream_rate_limiter.pop(client_id, None)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )
