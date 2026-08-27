"""Task Planning DSL - Structured task decomposition for Agent execution.

Provides TaskPlan and Step dataclasses for structured multi-step task execution
with real-time progress tracking and WorkBuddy-style UX.

Integration points:
- run_agent.py: Generate plan at conversation start, update step status during execution
- chat.py blueprint: Emit plan events via SSE for TaskDrawer rendering
- memory_fabric.py: Archive completed plans with lifecycle_tag="volatile"
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import List, Dict, Optional, Any, Callable

logger = logging.getLogger(__name__)


class StepStatus(str, Enum):
    """Step execution states."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AgentRole(str, Enum):
    """Predefined agent roles for step assignment."""
    PLANNER = "planner"      # Task decomposition
    CODER = "coder"          # Code writing
    REVIEWER = "reviewer"    # Code review
    TESTER = "tester"        # Testing
    DEBUGGER = "debugger"    # Debugging
    RESEARCHER = "researcher"  # Information gathering
    WRITER = "writer"        # Documentation/writing
    ANALYST = "analyst"      # Data analysis
    DEFAULT = "default"      # General purpose


@dataclass
class ToolCall:
    """Record of a tool invocation within a step."""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str = ""
    status: str = "pending"  # pending | running | done | error
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    duration: float = 0.0
    is_error: bool = False
    result_summary: str = ""  # Brief result for UI display
    
    def start(self):
        self.status = "running"
        self.started_at = time.time()
    
    def finish(self, success: bool = True, summary: str = ""):
        self.status = "done" if success else "error"
        self.is_error = not success
        self.finished_at = time.time()
        self.duration = (self.finished_at - self.started_at) if self.started_at else 0
        self.result_summary = summary


@dataclass
class Step:
    """A single step in a task plan."""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    title: str = ""
    description: str = ""
    status: StepStatus = StepStatus.PENDING
    agent_role: AgentRole = AgentRole.DEFAULT
    
    # Execution tracking
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    elapsed: float = 0.0
    
    # Dependencies and ordering
    dependencies: List[str] = field(default_factory=list)
    order: int = 0

    # Hierarchy: parent step id for sub-task tree rendering
    parent_id: Optional[str] = None

    # Tool calls within this step
    tool_calls: List[ToolCall] = field(default_factory=list)
    current_tool: Optional[ToolCall] = None
    
    # Memory integration
    key_decisions: List[str] = field(default_factory=list)
    lifecycle_tag: str = "volatile"  # Route E integration
    
    def start(self):
        """Mark step as in progress."""
        self.status = StepStatus.IN_PROGRESS
        self.started_at = time.time()
        logger.debug(f"[TaskPlan] Step '{self.title}' started")
    
    def complete(self, success: bool = True):
        """Mark step as completed or failed."""
        self.finished_at = time.time()
        self.elapsed = (self.finished_at - self.started_at) if self.started_at else 0
        self.status = StepStatus.COMPLETED if success else StepStatus.FAILED
        logger.debug(f"[TaskPlan] Step '{self.title}' {self.status.value} in {self.elapsed:.1f}s")
    
    def start_tool(self, name: str) -> ToolCall:
        """Start a new tool call within this step."""
        tool = ToolCall(name=name)
        tool.start()
        self.current_tool = tool
        self.tool_calls.append(tool)
        return tool
    
    def finish_tool(self, success: bool = True, summary: str = ""):
        """Finish current tool call."""
        if self.current_tool:
            self.current_tool.finish(success, summary)
            self.current_tool = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict for JSON transport."""
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "status": self.status.value,
            "agent_role": self.agent_role.value,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "elapsed": self.elapsed,
            "dependencies": self.dependencies,
            "order": self.order,
            "parent_id": self.parent_id,
            "tool_calls": [
                {
                    "id": tc.id,
                    "name": tc.name,
                    "status": tc.status,
                    "duration": round(tc.duration, 1),
                    "is_error": tc.is_error,
                    "result_summary": tc.result_summary,
                }
                for tc in self.tool_calls
            ],
            "current_tool": {
                "id": self.current_tool.id,
                "name": self.current_tool.name,
                "status": self.current_tool.status,
            } if self.current_tool else None,
            "key_decisions": self.key_decisions,
        }


@dataclass
class TaskPlan:
    """A complete task plan with multiple steps."""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    title: str = ""
    description: str = ""
    user_request: str = ""
    
    # Steps
    steps: List[Step] = field(default_factory=list)
    
    # Execution tracking
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    total_elapsed: float = 0.0
    
    # Estimation
    estimated_duration: int = 0  # seconds
    estimated_steps: int = 0
    
    # Status
    status: str = "pending"  # pending | running | completed | failed
    current_step_index: int = 0
    
    # Required capabilities
    required_tools: List[str] = field(default_factory=list)
    required_skills: List[str] = field(default_factory=list)
    
    # Callbacks for real-time updates
    _on_step_change: Optional[Callable[[Step], None]] = field(default=None, repr=False)
    _on_tool_call: Optional[Callable[[Step, ToolCall], None]] = field(default=None, repr=False)
    
    def __post_init__(self):
        """Assign order indices after creation."""
        for i, step in enumerate(self.steps):
            step.order = i
    
    @property
    def current_step(self) -> Optional[Step]:
        """Get currently executing step."""
        if 0 <= self.current_step_index < len(self.steps):
            return self.steps[self.current_step_index]
        return None
    
    @property
    def progress_percent(self) -> int:
        """Calculate completion percentage."""
        if not self.steps:
            return 0
        completed = sum(1 for s in self.steps if s.status == StepStatus.COMPLETED)
        return int((completed / len(self.steps)) * 100)
    
    @property
    def stats(self) -> Dict[str, int]:
        """Get step statistics."""
        return {
            "total": len(self.steps),
            "pending": sum(1 for s in self.steps if s.status == StepStatus.PENDING),
            "in_progress": sum(1 for s in self.steps if s.status == StepStatus.IN_PROGRESS),
            "completed": sum(1 for s in self.steps if s.status == StepStatus.COMPLETED),
            "failed": sum(1 for s in self.steps if s.status == StepStatus.FAILED),
        }
    
    def start(self):
        """Start executing the plan."""
        self.status = "running"
        self.started_at = time.time()
        self.current_step_index = 0
        logger.info(f"[TaskPlan] Started: '{self.title}' with {len(self.steps)} steps")
    
    def start_next_step(self) -> Optional[Step]:
        """Move to and start the next step."""
        # Complete current step if any
        if self.current_step and self.current_step.status == StepStatus.IN_PROGRESS:
            self.current_step.complete(success=True)
            if self._on_step_change:
                self._on_step_change(self.current_step)
        
        # Find next pending step (respecting dependencies)
        for i, step in enumerate(self.steps):
            if step.status == StepStatus.PENDING:
                # Check dependencies
                deps_satisfied = all(
                    self._get_step_by_id(dep_id).status == StepStatus.COMPLETED
                    for dep_id in step.dependencies
                ) if step.dependencies else True
                
                if deps_satisfied:
                    self.current_step_index = i
                    step.start()
                    if self._on_step_change:
                        self._on_step_change(step)
                    return step
        
        # No more steps
        self.complete()
        return None
    
    def _get_step_by_id(self, step_id: str) -> Optional[Step]:
        """Find step by ID."""
        for step in self.steps:
            if step.id == step_id:
                return step
        return None
    
    def complete(self, success: bool = True):
        """Mark plan as completed."""
        self.finished_at = time.time()
        self.total_elapsed = (self.finished_at - self.started_at) if self.started_at else 0
        self.status = "completed" if success else "failed"
        logger.info(f"[TaskPlan] Completed: '{self.title}' in {self.total_elapsed:.1f}s")
    
    def start_tool_in_current_step(self, name: str) -> ToolCall:
        """Start a tool call in the current step."""
        if not self.current_step:
            raise RuntimeError("No active step")
        tool = self.current_step.start_tool(name)
        if self._on_tool_call:
            self._on_tool_call(self.current_step, tool)
        return tool
    
    def finish_tool_in_current_step(self, success: bool = True, summary: str = ""):
        """Finish current tool call."""
        if self.current_step:
            self.current_step.finish_tool(success, summary)
            if self._on_tool_call:
                self._on_tool_call(self.current_step, self.current_step.tool_calls[-1])
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict for JSON transport."""
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "user_request": self.user_request,
            "status": self.status,
            "progress_percent": self.progress_percent,
            "stats": self.stats,
            "current_step_index": self.current_step_index,
            "current_step": self.current_step.to_dict() if self.current_step else None,
            "steps": [s.to_dict() for s in self.steps],
            "total_elapsed": round(self.total_elapsed, 1),
            "estimated_duration": self.estimated_duration,
            "required_tools": self.required_tools,
            "required_skills": self.required_skills,
        }
    
    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict(), ensure_ascii=False)


# ── Plan Generation from Agent Output ───────────────────────────────────────

def parse_plan_from_agent_output(content: str) -> Optional[TaskPlan]:
    """Parse a TaskPlan from Agent's structured output.
    
    Expected format in Agent response:
    ```json
    {
      "plan": {
        "title": "Implement feature X",
        "description": "...",
        "steps": [
          {"title": "Analyze requirements", "agent_role": "analyst"},
          {"title": "Write code", "agent_role": "coder", "dependencies": ["step_1_id"]},
          {"title": "Add tests", "agent_role": "tester", "dependencies": ["step_2_id"]}
        ],
        "estimated_duration": 300
      }
    }
    ```
    """
    try:
        # Extract JSON from markdown code block or raw JSON
        json_str = content
        if "```json" in content:
            json_str = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            json_str = content.split("```")[1].split("```")[0].strip()
        
        data = json.loads(json_str)
        plan_data = data.get("plan", data)  # Support both wrapped and unwrapped
        
        steps = []
        for i, step_data in enumerate(plan_data.get("steps", [])):
            step = Step(
                id=step_data.get("id") or f"step_{i+1}",
                title=step_data.get("title", f"Step {i+1}"),
                description=step_data.get("description", ""),
                agent_role=AgentRole(step_data.get("agent_role", "default")),
                dependencies=step_data.get("dependencies", []),
                parent_id=step_data.get("parent_id"),
                order=i,
            )
            steps.append(step)
        
        return TaskPlan(
            title=plan_data.get("title", "Untitled Task"),
            description=plan_data.get("description", ""),
            steps=steps,
            estimated_duration=plan_data.get("estimated_duration", 0),
            estimated_steps=len(steps),
            required_tools=plan_data.get("required_tools", []),
            required_skills=plan_data.get("required_skills", []),
        )
    except Exception as e:
        logger.warning(f"[TaskPlan] Failed to parse plan from output: {e}")
        return None


def should_create_plan(user_request: str) -> bool:
    """Heuristic: determine if a request warrants a structured plan."""
    # Multi-step indicators
    multi_step_keywords = [
        "帮我", "帮我做", "帮我写", "帮我改", "帮我分析",
        "实现", "开发", "创建", "搭建", "配置",
        "调研", "研究", "对比", "评估",
        "优化", "重构", "修复", "调试",
        "写一份", "生成一个", "做一个",
    ]
    
    # Complexity indicators
    complexity_markers = [
        "和", "然后", "接着", "再", "之后", "最后",
        "第一步", "第二步", "首先", "其次", "最终",
        "多个", "几个", "系列", "全套",
    ]
    
    request_lower = user_request.lower()
    has_multi_step = any(kw in request_lower for kw in multi_step_keywords)
    has_complexity = any(m in request_lower for m in complexity_markers)
    
    # Also check for explicit planning request
    explicit_plan = any(x in request_lower for x in ["分步骤", "规划", "计划", "拆解"])
    
    return explicit_plan or (has_multi_step and (has_complexity or len(user_request) > 50))


# ── System Prompt Addition ──────────────────────────────────────────────────

PLANNING_SYSTEM_PROMPT_ADDITION = """
When the user request involves multiple steps or complex tasks, you MUST create a structured plan first.

Before executing, output your plan in this exact JSON format:

```json
{
  "plan": {
    "title": "Brief task title",
    "description": "What this task will accomplish",
    "steps": [
      {
        "id": "step_1",
        "title": "Step description",
        "description": "Detailed explanation",
        "agent_role": "coder|tester|reviewer|researcher|analyst|writer|debugger|default"
      },
      {
        "id": "step_2",
        "title": "Sub-task of step_1",
        "description": "Break a large step into child steps for a clearer tree",
        "agent_role": "coder",
        "parent_id": "step_1"
      }
    ],
    "estimated_duration": 300,
    "required_tools": ["file", "browser", "code_execution"]
  }
}
```

Rules:
1. ALWAYS output the plan JSON before starting execution
2. Steps should be sequential and logical
3. Use appropriate agent_role for each step
4. Mark dependencies if a step relies on previous output
5. For large/complex steps, split into child steps and set "parent_id" to the parent step's "id" to form a task tree (the UI renders it as a collapsible hierarchy)
6. After outputting the plan, wait for user confirmation or proceed with step 1

Available agent roles:
- coder: Write or modify code
- tester: Write and run tests
- reviewer: Review code quality
- researcher: Gather information
- analyst: Analyze data or requirements
- writer: Write documentation
- debugger: Debug issues
- default: General purpose
"""


# ── Integration Helpers ─────────────────────────────────────────────────────

def create_planning_prompt_for_request(user_request: str) -> str:
    """Generate a prompt that encourages the Agent to create a plan."""
    return f"""The user has made a request that may involve multiple steps:

"{user_request}"

Please analyze this request and:
1. If it involves multiple steps or complexity, output a structured plan in the required JSON format
2. If it's simple enough for a single response, proceed directly

Remember: When in doubt, create a plan. Users appreciate seeing the roadmap before execution begins.
"""
