"""Task Planning Bridge - Integrates task_planning with run_agent.py and chat blueprint.

Provides:
- Plan generation hook in Agent.run_conversation
- SSE event emission for real-time TaskDrawer updates
- Memory fabric integration for plan archival
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional, Callable, Dict, Any

from agent.task_planning import (
    TaskPlan, Step, StepStatus, ToolCall,
    parse_plan_from_agent_output, should_create_plan,
    create_planning_prompt_for_request,
)

logger = logging.getLogger(__name__)

# Global registry for active plans per session
_active_plans: Dict[str, TaskPlan] = {}

# Callback registry for event emission
_plan_callbacks: Dict[str, Callable[[str, Dict], None]] = {}


def register_plan_callback(session_id: str, callback: Callable[[str, Dict], None]):
    """Register a callback for plan events in a session.
    
    Args:
        session_id: The chat session ID
        callback: Function(event_type: str, data: dict) -> None
    """
    _plan_callbacks[session_id] = callback
    logger.debug(f"[TaskBridge] Registered callback for session {session_id[:8]}...")


def unregister_plan_callback(session_id: str):
    """Unregister callback for a session."""
    _plan_callbacks.pop(session_id, None)
    _active_plans.pop(session_id, None)


def _emit_event(session_id: str, event_type: str, data: Dict[str, Any]):
    """Emit a plan event to the registered callback."""
    callback = _plan_callbacks.get(session_id)
    if callback:
        try:
            callback(event_type, data)
        except Exception as e:
            logger.warning(f"[TaskBridge] Callback error: {e}")


def get_or_create_plan(session_id: str, user_request: str) -> Optional[TaskPlan]:
    """Get existing plan or determine if we need one for this request."""
    # Return existing plan if available
    if session_id in _active_plans:
        return _active_plans[session_id]
    
    # Check if this request warrants a plan
    if not should_create_plan(user_request):
        logger.debug(f"[TaskBridge] Request too simple for plan: {user_request[:50]}...")
        return None
    
    # Create placeholder plan (will be populated when Agent outputs JSON)
    logger.info(f"[TaskBridge] Planning warranted for session {session_id[:8]}...")
    return None  # Will be created when Agent outputs plan JSON


def create_plan_from_agent_output(session_id: str, agent_content: str, user_request: str) -> Optional[TaskPlan]:
    """Parse Agent output and create a plan if valid JSON plan found.
    
    Called when Agent produces output that might contain a plan.
    """
    plan = parse_plan_from_agent_output(agent_content)
    if not plan:
        return None
    
    # Enrich with request context
    plan.user_request = user_request
    
    # Set up callbacks for real-time updates
    def on_step_change(step: Step):
        _emit_event(session_id, "step_update", {
            "plan_id": plan.id,
            "step": step.to_dict(),
            "progress_percent": plan.progress_percent,
            "stats": plan.stats,
        })
    
    def on_tool_call(step: Step, tool: ToolCall):
        _emit_event(session_id, "tool_call", {
            "plan_id": plan.id,
            "step_id": step.id,
            "tool": {
                "id": tool.id,
                "name": tool.name,
                "status": tool.status,
                "duration": tool.duration,
            },
        })
    
    plan._on_step_change = on_step_change
    plan._on_tool_call = on_tool_call
    
    # Register
    _active_plans[session_id] = plan
    
    # Emit plan created event
    _emit_event(session_id, "plan_created", plan.to_dict())
    
    logger.info(f"[TaskBridge] Plan created: '{plan.title}' with {len(plan.steps)} steps")
    return plan


def start_plan_execution(session_id: str) -> bool:
    """Start executing a plan."""
    plan = _active_plans.get(session_id)
    if not plan:
        return False
    
    plan.start()
    _emit_event(session_id, "plan_started", plan.to_dict())
    
    # Start first step
    step = plan.start_next_step()
    if step:
        _emit_event(session_id, "step_started", {
            "plan_id": plan.id,
            "step": step.to_dict(),
        })
    
    return True


def advance_to_next_step(session_id: str) -> bool:
    """Complete current step and advance to next."""
    plan = _active_plans.get(session_id)
    if not plan:
        return False
    
    # Complete current step
    if plan.current_step:
        plan.current_step.complete(success=True)
        _emit_event(session_id, "step_completed", {
            "plan_id": plan.id,
            "step": plan.current_step.to_dict(),
            "progress_percent": plan.progress_percent,
        })
    
    # Start next step
    next_step = plan.start_next_step()
    if next_step:
        _emit_event(session_id, "step_started", {
            "plan_id": plan.id,
            "step": next_step.to_dict(),
        })
        return True
    else:
        # Plan completed
        plan.complete(success=True)
        _emit_event(session_id, "plan_completed", plan.to_dict())
        _archive_plan_to_memory(session_id, plan)
        return False


def start_tool_call(session_id: str, tool_name: str) -> bool:
    """Record start of a tool call in current step."""
    plan = _active_plans.get(session_id)
    if not plan or not plan.current_step:
        return False
    
    tool = plan.start_tool_in_current_step(tool_name)
    _emit_event(session_id, "tool_started", {
        "plan_id": plan.id,
        "step_id": plan.current_step.id,
        "tool": {
            "id": tool.id,
            "name": tool.name,
            "status": tool.status,
        },
    })
    return True


def finish_tool_call(session_id: str, success: bool = True, summary: str = "") -> bool:
    """Record completion of current tool call."""
    plan = _active_plans.get(session_id)
    if not plan or not plan.current_step:
        return False
    
    plan.finish_tool_in_current_step(success, summary)
    if plan.current_step.tool_calls:
        tool = plan.current_step.tool_calls[-1]
        _emit_event(session_id, "tool_completed", {
            "plan_id": plan.id,
            "step_id": plan.current_step.id,
            "tool": {
                "id": tool.id,
                "name": tool.name,
                "status": tool.status,
                "duration": round(tool.duration, 1),
                "is_error": tool.is_error,
                "result_summary": tool.result_summary,
            },
        })
    return True


def fail_current_step(session_id: str, error: str):
    """Mark current step as failed."""
    plan = _active_plans.get(session_id)
    if not plan or not plan.current_step:
        return
    
    plan.current_step.status = StepStatus.FAILED
    plan.current_step.complete(success=False)
    _emit_event(session_id, "step_failed", {
        "plan_id": plan.id,
        "step": plan.current_step.to_dict(),
        "error": error,
    })


def get_active_plan(session_id: str) -> Optional[TaskPlan]:
    """Get the currently active plan for a session."""
    return _active_plans.get(session_id)


def has_active_plan(session_id: str) -> bool:
    """Check if session has an active plan."""
    return session_id in _active_plans and _active_plans[session_id].status == "running"


def _archive_plan_to_memory(session_id: str, plan: TaskPlan):
    """Archive completed plan to memory fabric."""
    try:
        from agent.memory_fabric import record
        
        # Build summary
        steps_summary = "\n".join([
            f"- {s.title} ({s.status.value}, {s.elapsed:.1f}s)"
            for s in plan.steps
        ])
        
        record({
            "source": "task_plan",
            "layer": "L3_EPISODIC",
            "type": "completed_task",
            "scope": session_id,
            "pointer": f"task://{plan.id}",
            "fts_content": f"""Task: {plan.title}
Description: {plan.description}
Request: {plan.user_request}
Duration: {plan.total_elapsed:.1f}s
Steps:
{steps_summary}
""",
            "lifecycle_tag": "volatile",
        })
        
        logger.info(f"[TaskBridge] Plan archived to memory: {plan.title}")
    except Exception as e:
        logger.warning(f"[TaskBridge] Failed to archive plan: {e}")


def inject_planning_context(messages: list, user_request: str) -> list:
    """Inject planning system prompt into message list if warranted."""
    if not should_create_plan(user_request):
        return messages
    
    from agent.task_planning import PLANNING_SYSTEM_PROMPT_ADDITION
    
    # Find system message or prepend one
    modified = False
    for msg in messages:
        if msg.get("role") == "system":
            if "structured plan" not in msg.get("content", ""):
                msg["content"] = msg.get("content", "") + "\n\n" + PLANNING_SYSTEM_PROMPT_ADDITION
            modified = True
            break
    
    if not modified:
        messages.insert(0, {
            "role": "system",
            "content": PLANNING_SYSTEM_PROMPT_ADDITION,
        })
    
    return messages


# ── FastAPI/SSE Integration ─────────────────────────────────────────────────

async def emit_plan_event_sse(session_id: str, event_type: str, data: Dict):
    """Helper for emitting plan events via SSE from chat blueprint.
    
    Usage in chat.py:
        from agent.task_planning_bridge import emit_plan_event_sse, register_plan_callback
        
        # Register at session start
        register_plan_callback(session_id, lambda et, ed: asyncio.create_task(
            emit_plan_event_sse(session_id, et, ed)
        ))
    """
    # This is a placeholder - actual SSE emission happens in chat blueprint
    # The callback mechanism connects planning events to SSE stream
    pass
