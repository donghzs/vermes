"""
ScholarForge Blueprint — 论文写作模块
独立注册，不影响 Vermes 原有链路

全部端点挂载在 /api/scholar 下，与 Vermes 核心路由完全隔离
"""
import asyncio
import json
import logging
from typing import Optional

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)


# === 复用 Vermes LLM 基础设施（零侵入） ===
async def _llm(prompt: str) -> str:
    """通过 Vermes 现有 LLM 基础设施调用模型"""
    from hermes_cli.blueprints.chat import _get_chat_credentials, _resolve_model_provider
    import httpx

    base_url, api_key, default_model = _get_chat_credentials()

    if not api_key:
        raise HTTPException(500, "未配置 LLM 提供商，请在设置页添加 API Key")

    provider, resolved_url, resolved_key, model = _resolve_model_provider(
        default_model or "gpt-4o", ""
    )
    url = resolved_url or base_url
    key = resolved_key or api_key

    if not key:
        raise HTTPException(500, "未配置 API Key")

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }

    body = {
        "model": model or "gpt-4o",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
        "max_tokens": 8192,
        "stream": False,
    }

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{url.rstrip('/')}/chat/completions",
                json=body, headers=headers
            )
            if resp.status_code == 200:
                data = resp.json()
                return data["choices"][0]["message"]["content"]
            else:
                err = resp.text[:200]
                logger.error(f"LLM call failed: {resp.status_code} {err}")
                raise HTTPException(502, f"LLM 调用失败 ({resp.status_code})")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"LLM call error: {e}")
        raise HTTPException(502, str(e))


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
        """论文写作 SSE 流式接口 — 单个 Agent 或 STORM 全链路"""

        if not req.message.strip():
            raise HTTPException(400, "消息不能为空")

        ctx = _get_ctx(str(req.project_id or "default"))

        async def generate():
            try:
                if req.pipeline:
                    # STORM 全链路: Literature → Outline → Writing
                    pipeline_stages = ["literature", "outline", "writing"]

                    for stage in pipeline_stages:
                        yield _sse({"type": "stage", "stage": stage, "pipeline": "start"})

                        from hermes_cli.scholarforge.agents import AGENTS
                        agent_cls = AGENTS.get(stage)
                        if not agent_cls:
                            continue

                        agent = agent_cls(ctx, _llm)
                        async for evt in agent.run(req.message):
                            yield _sse(evt)

                        yield _sse({"type": "stage", "stage": stage, "pipeline": "done",
                                     "papers": len(ctx.papers)})

                    yield _sse({"type": "done", "pipeline": "complete",
                                 "papers": len(ctx.papers)})
                else:
                    # 单个 Agent
                    from hermes_cli.scholarforge.agents import AGENTS
                    agent_cls = AGENTS.get(req.agent)
                    if not agent_cls:
                        agent_cls = AGENTS["topic"]

                    agent = agent_cls(ctx, _llm)
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
        from hermes_cli.scholarforge.search import get_available_sources

        return {"sources": get_available_sources()}
