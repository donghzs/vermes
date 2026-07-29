"""Blueprint: Analytics（用量与模型分析）

Token/cost analytics endpoints for the dashboard.
"""

import time
import logging
from typing import Optional

from fastapi import HTTPException

_log = logging.getLogger(__name__)


# ── route handlers ─────────────────────────────────────────────

async def get_usage_analytics(days: int = 30):
    from vermes_state import SessionDB
    from agent.insights import InsightsEngine

    db = SessionDB()
    try:
        cutoff = time.time() - (days * 86400)
        cur = db._conn.execute("""
            SELECT date(started_at, 'unixepoch') as day,
                   SUM(input_tokens) as input_tokens,
                   SUM(output_tokens) as output_tokens,
                   SUM(cache_read_tokens) as cache_read_tokens,
                   SUM(reasoning_tokens) as reasoning_tokens,
                   COALESCE(SUM(estimated_cost_usd), 0) as estimated_cost,
                   COALESCE(SUM(actual_cost_usd), 0) as actual_cost,
                   COUNT(*) as sessions,
                   SUM(COALESCE(api_call_count, 0)) as api_calls
            FROM sessions WHERE started_at > ?
            GROUP BY day ORDER BY day
        """, (cutoff,))
        daily = [dict(r) for r in cur.fetchall()]

        cur2 = db._conn.execute("""
            SELECT model,
                   SUM(input_tokens) as input_tokens,
                   SUM(output_tokens) as output_tokens,
                   COALESCE(SUM(estimated_cost_usd), 0) as estimated_cost,
                   COUNT(*) as sessions,
                   SUM(COALESCE(api_call_count, 0)) as api_calls
            FROM sessions WHERE started_at > ? AND model IS NOT NULL
            GROUP BY model ORDER BY SUM(input_tokens) + SUM(output_tokens) DESC
        """, (cutoff,))
        by_model = [dict(r) for r in cur2.fetchall()]

        cur3 = db._conn.execute("""
            SELECT SUM(input_tokens) as total_input,
                   SUM(output_tokens) as total_output,
                   SUM(cache_read_tokens) as total_cache_read,
                   SUM(reasoning_tokens) as total_reasoning,
                   COALESCE(SUM(estimated_cost_usd), 0) as total_estimated_cost,
                   COALESCE(SUM(actual_cost_usd), 0) as total_actual_cost,
                   COUNT(*) as total_sessions,
                   SUM(COALESCE(api_call_count, 0)) as total_api_calls
            FROM sessions WHERE started_at > ?
        """, (cutoff,))
        totals = dict(cur3.fetchone())
        insights_report = InsightsEngine(db).generate(days=days)
        skills = insights_report.get("skills", {
            "summary": {
                "total_skill_loads": 0,
                "total_skill_edits": 0,
                "total_skill_actions": 0,
                "distinct_skills_used": 0,
            },
            "top_skills": [],
        })

        return {
            "daily": daily,
            "by_model": by_model,
            "totals": totals,
            "period_days": days,
            "skills": skills,
        }
    finally:
        db.close()


async def get_models_analytics(days: int = 30):
    """Rich per-model analytics for the Models dashboard page.

    Returns token/cost/session breakdown per model plus capability metadata
    from models.dev (context window, vision, tools, reasoning, etc.).
    """
    from vermes_state import SessionDB

    db = SessionDB()
    try:
        cutoff = time.time() - (days * 86400)

        cur = db._conn.execute("""
            SELECT model,
                   billing_provider,
                   SUM(input_tokens) as input_tokens,
                   SUM(output_tokens) as output_tokens,
                   SUM(cache_read_tokens) as cache_read_tokens,
                   SUM(reasoning_tokens) as reasoning_tokens,
                   COALESCE(SUM(estimated_cost_usd), 0) as estimated_cost,
                   COALESCE(SUM(actual_cost_usd), 0) as actual_cost,
                   COUNT(*) as sessions,
                   SUM(COALESCE(api_call_count, 0)) as api_calls,
                   SUM(tool_call_count) as tool_calls,
                   MAX(started_at) as last_used_at,
                   AVG(input_tokens + output_tokens) as avg_tokens_per_session
            FROM sessions WHERE started_at > ? AND model IS NOT NULL AND model != ''
            GROUP BY model, billing_provider
            ORDER BY SUM(input_tokens) + SUM(output_tokens) DESC
        """, (cutoff,))
        rows = [dict(r) for r in cur.fetchall()]

        models = []
        for row in rows:
            provider = row.get("billing_provider") or ""
            model_name = row["model"]
            caps = {}
            try:
                from agent.models_dev import get_model_capabilities
                mc = get_model_capabilities(provider=provider, model=model_name)
                if mc is not None:
                    caps = {
                        "supports_tools": mc.supports_tools,
                        "supports_vision": mc.supports_vision,
                        "supports_reasoning": mc.supports_reasoning,
                        "context_window": mc.context_window,
                        "max_output_tokens": mc.max_output_tokens,
                        "model_family": mc.model_family,
                    }
            except Exception:
                pass

            models.append({
                "model": model_name,
                "provider": provider,
                "input_tokens": row["input_tokens"],
                "output_tokens": row["output_tokens"],
                "cache_read_tokens": row["cache_read_tokens"],
                "reasoning_tokens": row["reasoning_tokens"],
                "estimated_cost": row["estimated_cost"],
                "actual_cost": row["actual_cost"],
                "sessions": row["sessions"],
                "api_calls": row["api_calls"],
                "tool_calls": row["tool_calls"],
                "last_used_at": row["last_used_at"],
                "avg_tokens_per_session": row["avg_tokens_per_session"],
                "capabilities": caps,
            })

        totals_cur = db._conn.execute("""
            SELECT COUNT(DISTINCT model) as distinct_models,
                   SUM(input_tokens) as total_input,
                   SUM(output_tokens) as total_output,
                   SUM(cache_read_tokens) as total_cache_read,
                   SUM(reasoning_tokens) as total_reasoning,
                   COALESCE(SUM(estimated_cost_usd), 0) as total_estimated_cost,
                   COALESCE(SUM(actual_cost_usd), 0) as total_actual_cost,
                   COUNT(*) as total_sessions,
                   SUM(COALESCE(api_call_count, 0)) as total_api_calls
            FROM sessions WHERE started_at > ? AND model IS NOT NULL AND model != ''
        """, (cutoff,))
        totals = dict(totals_cur.fetchone())

        return {
            "models": models,
            "totals": totals,
            "period_days": days,
        }
    finally:
        db.close()


# ── registration ───────────────────────────────────────────────

def register_to(app):
    """Register analytics routes on the FastAPI app."""
    app.add_api_route(
        "/api/analytics/usage", get_usage_analytics, methods=["GET"], name="get_usage_analytics"
    )
    app.add_api_route(
        "/api/analytics/models", get_models_analytics, methods=["GET"], name="get_models_analytics"
    )


blueprint = None  # no APIRouter; uses register_to(app) pattern
