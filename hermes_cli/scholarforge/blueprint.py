"""
ScholarForge Blueprint — 论文写作模块
独立注册，不影响 Vermes 原有链路

全部端点挂载在 /api/scholar 下，与 Vermes 核心路由完全隔离
"""
import asyncio
import json
import logging
import os
from typing import Optional

from fastapi import HTTPException, Path
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)


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


def _resolve_credentials(provider: str, model: str = ""):
    """解析指定 provider/model 的 api_key + base_url。
    优先级：config.yaml providers.<provider>.api_key > .env <ENV_KEY>
    零侵入 Vermes 核心链路 — 不调用 _get_chat_credentials。
    """
    import yaml, os
    from hermes_cli.blueprints.chat import PROVIDERS

    home = os.path.expanduser("~/.vermes")
    cfg_path = os.path.join(home, "config.yaml")
    env_path = os.path.join(home, ".env")

    # 1) 读 config.yaml
    cfg = {}
    if os.path.exists(cfg_path):
        with open(cfg_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    cfg_providers = cfg.get("providers", {})

    # 2) 读 .env
    env_vars = {}
    if os.path.exists(env_path):
        for line in open(env_path).read().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env_vars[k.strip()] = v.strip().strip('"').strip("'")

    # 3) 目标 provider 配置
    prov_def = PROVIDERS.get(provider) or {}
    cfg_entry = cfg_providers.get(provider, {})

    # api_key: config.yaml 优先 > .env <ENV_KEY>
    env_key_name = prov_def.get("env_key", "")
    api_key = (
        cfg_entry.get("api_key", "") or
        (env_vars.get(env_key_name) if env_key_name else "") or
        ""
    )

    # base_url: 目标 provider > PROVIDERS registry 默认
    base_url = (
        cfg_entry.get("base_url", "") or
        prov_def.get("base_url", "") or
        ""
    )

    if not model:
        model = cfg_entry.get("model") or ""

    return {
        "api_key": api_key,
        "base_url": base_url.rstrip("/") if base_url else "",
        "model": model,
        "provider": provider,
    }


def _scan_configured_providers() -> list[dict]:
    """扫描用户已配置的 providers，返回有 Key 的列表"""
    import yaml, os
    from hermes_cli.blueprints.chat import PROVIDERS

    home = os.path.expanduser("~/.vermes")
    cfg = {}
    cfg_path = os.path.join(home, "config.yaml")
    if os.path.exists(cfg_path):
        with open(cfg_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    env_vars = {}
    env_path = os.path.join(home, ".env")
    if os.path.exists(env_path):
        for line in open(env_path).read().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env_vars[k.strip()] = v.strip().strip('"').strip("'")
    cfg_providers = cfg.get("providers", {})
    cfg_model_provider = cfg.get("model", {}).get("provider", "")

    result = []
    # 优先用 config.yaml 中 model.provider 指定的
    for prov_key in [cfg_model_provider] + [k for k in PROVIDERS if k != cfg_model_provider]:
        if not prov_key:
            continue
        prov_def = PROVIDERS.get(prov_key, {})
        cfg_entry = cfg_providers.get(prov_key, {})
        env_key_name = prov_def.get("env_key", "")
        api_key = (
            cfg_entry.get("api_key", "") or
            (env_vars.get(env_key_name) if env_key_name else "") or
            ""
        )
        base_url = cfg_entry.get("base_url", "") or prov_def.get("base_url", "")
        model = cfg_entry.get("model", "") or _PROVIDER_FALLBACK_MODELS.get(prov_key, "")
        if api_key and base_url:
            result.append({
                "provider": prov_key,
                "api_key": api_key,
                "base_url": base_url.rstrip("/"),
                "model": model,
            })
    return result


async def _make_llm(provider_override: str = None, model_override: str = None):
    """工厂函数 — 返回绑定好特定 provider/model 的 _llm。
    每次调用按用户实际配置解析凭证，不依赖 Vermes 默认 Chat 链路。
    如果 provider 未指定，自动扫描用户已配置的 providers，用第一个有 Key 的。
    """
    provider = provider_override or ""
    model = model_override or ""

    if not provider:
        # 自动检测：扫描用户已配置的 provider，用第一个有 Key 的
        from hermes_cli.blueprints.chat import PROVIDERS
        import yaml, os
        home = os.path.expanduser("~/.vermes")
        cfg = {}
        cfg_path = os.path.join(home, "config.yaml")
        if os.path.exists(cfg_path):
            with open(cfg_path, encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
        env_vars = {}
        env_path = os.path.join(home, ".env")
        if os.path.exists(env_path):
            for line in open(env_path).read().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env_vars[k.strip()] = v.strip().strip('"').strip("'")
        cfg_providers = cfg.get("providers", {})
        # 优先用 config.yaml 中 model.provider 指定的
        cfg_model_provider = cfg.get("model", {}).get("provider", "")
        for prov_key in [cfg_model_provider] + [k for k in PROVIDERS if k != cfg_model_provider]:
            if not prov_key:
                continue
            prov_def = PROVIDERS.get(prov_key, {})
            cfg_entry = cfg_providers.get(prov_key, {})
            env_key_name = prov_def.get("env_key", "")
            has_key = bool(
                cfg_entry.get("api_key") or
                (env_vars.get(env_key_name) if env_key_name else False)
            )
            if has_key:
                provider = prov_key
                if not model:
                    model = cfg_entry.get("model") or _PROVIDER_FALLBACK_MODELS.get(prov_key, "")
                logger.info(f"[ScholarForge] Auto-selected provider: {provider} (model: {model or 'auto'}) (has API Key)")
                break

    # 预解析凭证
    creds = _resolve_credentials(provider, model)

    async def _llm(prompt: str) -> str:
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

        body = {
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7,
            "max_tokens": 8192,
            "stream": False,
        }
        if model:
            body["model"] = model

        try:
            async with httpx.AsyncClient(timeout=120) as client:
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


class LiteratureSearchRequest(BaseModel):
    query: str
    limit: int = 10


# === 内存中的项目上下文 ===
_session_contexts: dict[str, "ProjectContext"] = {}


def _get_ctx(project_id: str = "default"):
    from hermes_cli.scholarforge.agents import ProjectContext

    if project_id not in _session_contexts:
        _session_contexts[project_id] = ProjectContext()
    return _session_contexts[project_id]


# === SSE 事件格式化 ===
def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


# === Blueprint 注册函数 ===
def register_to(app):
    """向 FastAPI app 注册 ScholarForge 路由 — 全部挂载在 /api/scholar"""

    @app.get("/api/scholar/model")
    async def current_model_info():
        """返回当前默认模型与 Provider，便于前端展示"""
        from hermes_cli.blueprints.chat import _get_chat_credentials, _resolve_model_provider
        try:
            base_url, api_key, default_model = _get_chat_credentials()
            provider, url, key, model = _resolve_model_provider(
                default_model or "deepseek-v4-flash", ""
            )
            # 读取 config.yaml 中的 provider 默认模型
            cfg_model = None
            cfg_provider = None
            try:
                import yaml
                with open(os.path.expanduser("~/.vermes/config.yaml")) as f:
                    cfg = yaml.safe_load(f) or {}
                md = cfg.get("model", {}) or {}
                cfg_model = md.get("default")
                cfg_provider = md.get("provider")
            except Exception:
                pass
            return {
                "model": cfg_model or model or default_model or "deepseek-v4-flash",
                "provider": cfg_provider or provider or "deepseek",
                "base_url": (url or base_url or "").rstrip("/"),
            }
        except Exception as e:
            return {"model": "deepseek-v4-flash", "provider": "deepseek", "base_url": "", "error": str(e)}

    # ═══════════════════════════════════════════════════════════════
    # 项目管理 — 每个论文项目独立的工作空间
    # ═══════════════════════════════════════════════════════════════

    @app.get("/api/scholar/projects")
    async def api_list_projects():
        """列出所有论文项目"""
        from . import database as db
        return {"projects": db.list_projects()}

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

    @app.post("/api/scholar/projects/{pid}/section/{section_key}")
    async def api_save_section(pid: int, section_key: str, req: dict):
        """保存章节内容"""
        from . import database as db
        content = req.get("content", "")
        db.save_section_content(pid, section_key, content)
        return {"ok": True, "word_count": len(content)}

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

    @app.get("/api/scholar/agents")
    async def list_agents():
        """列出所有论文写作 Agent"""
        from hermes_cli.scholarforge.agents import AGENTS

        return {
            "agents": [agent_cls.to_dict() for agent_cls in AGENTS.values()]
        }

    @app.get("/api/scholar/search")
    async def search_literature_get(query: str, limit: int = 10):
        """多源文献搜索（GET）"""
        from hermes_cli.scholarforge.search import search_papers

        results = []
        try:
            async for paper in search_papers(query, limit=limit):
                results.append(paper.to_dict())
        except Exception as e:
            logger.error(f"Search error: {e}")
            return {"results": [], "error": str(e)}

        return {"query": query, "results": results, "count": len(results)}

    @app.post("/api/scholar/search")
    async def search_literature_post(req: LiteratureSearchRequest):
        """文献搜索（POST）"""
        from hermes_cli.scholarforge.search import search_papers

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

    @app.post("/api/scholar/stream")
    async def scholar_stream(req: ScholarChatRequest):
        """论文写作 SSE 流式接口 — 每个 Agent 独立模型，支持 STORM 全链路"""

        if not req.message.strip():
            raise HTTPException(400, "消息不能为空")

        ctx = _get_ctx(str(req.project_id or "default"))
        pid = int(req.project_id or 0)

        # 加载项目 Agent-Provider 绑定
        from . import database as db
        agent_cfg = db.get_agent_providers(pid) if pid > 0 else db.DEFAULT_AGENT_PROVIDERS

        async def generate():
            try:
                # STORM 引擎模式 — 走 Stanford STORM 全链路
                if req.pipeline and req.agent == "storm":
                    from hermes_cli.scholarforge.storm_adapter import StormAdapter

                    # 解析凭证
                    cfg = agent_cfg.get("literature", {}) or agent_cfg.get("writing", {})
                    provider = cfg.get("provider", "")
                    model = cfg.get("model", "")
                    creds = _resolve_credentials(provider, model)

                    if not creds["api_key"]:
                        # 自动检测已配置的 provider
                        creds_list = _scan_configured_providers()
                        if creds_list:
                            creds = creds_list[0]
                            provider = creds["provider"]
                            model = creds["model"]

                    if not creds["api_key"]:
                        yield _sse({
                            "type": "error",
                            "message": "未配置 API Key。请在 Vermes 设置中添加 Key，或在论文页为 Agent 选择已配置的模型。"
                        })
                        return

                    adapter = StormAdapter(
                        provider=creds["provider"],
                        model=creds["model"] or _PROVIDER_FALLBACK_MODELS.get(creds["provider"], "gpt-4o-mini"),
                        api_key=creds["api_key"],
                        base_url=creds["base_url"],
                    )
                    async for evt in adapter.run(req.message):
                        yield _sse(evt)
                    return

                if req.pipeline:
                    pipeline_stages = ["literature", "outline", "writing"]

                    for stage in pipeline_stages:
                        yield _sse({"type": "stage", "stage": stage, "pipeline": "start"})

                        from hermes_cli.scholarforge.agents import AGENTS
                        agent_cls = AGENTS.get(stage)
                        if not agent_cls:
                            continue

                        cfg = agent_cfg.get(stage, {})
                        agent_llm = await _make_llm(cfg.get("provider"), cfg.get("model"))
                        agent = agent_cls(ctx, agent_llm)
                        async for evt in agent.run(req.message):
                            yield _sse(evt)

                        yield _sse({"type": "stage", "stage": stage, "pipeline": "done",
                                     "papers": len(ctx.papers)})

                    yield _sse({"type": "done", "pipeline": "complete",
                                 "papers": len(ctx.papers)})
                else:
                    from hermes_cli.scholarforge.agents import AGENTS
                    agent_cls = AGENTS.get(req.agent)
                    if not agent_cls:
                        agent_cls = AGENTS["topic"]

                    agent_name = req.agent or "topic"
                    cfg = agent_cfg.get(agent_name, {})
                    agent_llm = await _make_llm(cfg.get("provider"), cfg.get("model"))
                    agent = agent_cls(ctx, agent_llm)
                    async for evt in agent.run(req.message):
                        yield _sse(evt)

            except HTTPException as e:
                yield _sse({"type": "error", "message": str(e.detail)})
            except Exception as e:
                logger.error(f"Scholar stream error: {e}", exc_info=True)
                yield _sse({"type": "error", "message": str(e)})

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            }
        )

    @app.get("/api/scholar/sources")
    async def list_sources():
        """列出可用文献搜索源"""
        from hermes_cli.scholarforge.search import get_available_sources, get_paid_source_configs

        free_sources = get_available_sources()
        paid_sources = await get_paid_source_configs()
        return {
            "free_sources": free_sources,
            "paid_sources": paid_sources,
        }

    @app.post("/api/scholar/sources/activate")
    async def activate_paid_source(req: dict):
        """激活付费文献源（用户提供 API Key）"""
        source_name = req.get("source")
        api_key = req.get("api_key")
        if not source_name or not api_key:
            raise HTTPException(400, "source 和 api_key 必填")

        from hermes_cli.scholarforge.search import activate_paid_source

        ok = await activate_paid_source(source_name, api_key)
        if not ok:
            return {"status": "error", "message": f"来源 '{source_name}' 激活失败（不支持或验证不通过）"}
        return {"status": "ok", "source": source_name}

    # ═══════════════════════════════════════════════════════════════
    # Agent-Provider 绑定 — 每个 Agent 独立选择厂商和模型
    # ═══════════════════════════════════════════════════════════════

    @app.get("/api/scholar/providers")
    async def list_available_providers():
        """列出 Vermes 已配置的所有厂商（供 Agent 模型分配使用）"""
        from hermes_cli.blueprints.chat import PROVIDERS
        import yaml, os
        available = []
        home = os.path.expanduser("~/.vermes")
        cfg_path = os.path.join(home, "config.yaml")
        env_path = os.path.join(home, ".env")
        cfg = {}
        if os.path.exists(cfg_path):
            with open(cfg_path, encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
        cfg_providers = cfg.get("providers", {})
        # 读取 .env 中的 Key
        env_vars = {}
        if os.path.exists(env_path):
            for line in open(env_path).read().splitlines():
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    env_vars[k.strip()] = v.strip().strip('"').strip("'")
        for key, info in PROVIDERS.items():
            cfg_has = cfg_providers.get(key, {})
            env_key_name = info.get("env_key", "")
            has_key = bool(
                (cfg_has and cfg_has.get("api_key")) or
                (env_key_name and env_vars.get(env_key_name))
            )
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

    @app.get("/api/scholar/export")
    async def export_paper(
        project_id: str = "default",
        format: str = "markdown",
        title: str = "未命名论文",
    ):
        """导出论文为 Markdown 或 BibTeX"""
        ctx = _get_ctx(project_id)
        content = ctx.draft or ""
        papers = ctx.papers or []

        if format == "bibtex":
            from hermes_cli.scholarforge.export import format_export_bibtex
            return {
                "format": "bibtex",
                "content": format_export_bibtex(papers),
                "count": len(papers),
            }
        if format == "references" or format == "bib":
            from hermes_cli.scholarforge.export import extract_references, format_export_bibtex
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
        else:
            from hermes_cli.scholarforge.export import format_export_markdown
            return {
                "format": "markdown",
                "content": format_export_markdown(title, content, papers),
            }
