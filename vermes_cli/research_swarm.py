"""AutoResearch 研究组预设（路线图 J4）：学术检索/写作流水线 → Kanban 蜂群。

Why this module exists
----------------------
仓内其实早已备齐研究所需的全部"零件"，本模块只是把它们按研究阶段拼起来：

* ``tools/literature_search_tool.py:40`` → 工具 ``literature_search``
  （跨 provider 的文献检索）。
* ``vermes_cli/scholarforge/`` → 27 个学术工具，见 ``module.yaml`` 的
  ``provides_tools``（search / outline / write / review / quality_gate /
  verify_citations / literature_matrix / research_map ...）。
* ``vermes_cli/kanban_swarm.py`` → ``create_swarm`` 蜂群原语：
  root(共享黑板) → 并行专家 → verifier → synthesizer，四张卡即可调度。

缺的只有"研究阶段 ↔ 专家角色"的映射，即本预设层。

设计约束（纯增量，不破现有链路）
--------------------------------
1. **纯编排**：只调用 ``kanban_swarm.create_swarm``，绝不 import 或改动
   scholarforge / literature_search 的内部实现，任一组件升级都不牵连本文件。
2. **工具名写进 body，不写进 skills**：``kanban_db.create_task`` 会显式拒绝
   把工具集名当 skill 传入（见 kanban_db.py 的 ``toolset_typos`` 分支），
   而 ``scholarforge_*`` / ``literature_search`` 是 *tools* 不是 skill
   bundle。因此工具清单渲染进任务正文，作为专家的执行指引。
3. **不改 create_swarm 的内置 skills**：root 固定 ``kanban-orchestrator``、
   verifier 固定 ``requesting-code-review``、synthesizer 固定
   ``avoid-ai-writing``（kanban_swarm.py:127 / :193 / :212），本模块沿用，
   只自定义并行专家。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from vermes_cli import kanban_swarm as ks

__all__ = [
    "ResearchRole",
    "DEFAULT_RESEARCH_ROLES",
    "VERIFIER_ROLE",
    "SYNTHESIZER_ROLE",
    "create_research_swarm",
    "list_research_roles",
]

# 写在 root 卡 metadata 里的类型标记，便于仪表盘/审计按 kind 过滤蜂群。
RESEARCH_SWARM_KIND = "research_swarm_v1"


@dataclass(frozen=True)
class ResearchRole:
    """研究组中的一个专家角色。

    并行 worker 用 ``DEFAULT_RESEARCH_ROLES``；verifier / synthesizer 各用
    一个固定角色。``tools`` 只是**正文里的调用指引**，不会被当成 skill 传入。
    """

    key: str
    title: str
    objective: str
    tools: tuple[str, ...] = ()
    priority: int = 0

    def render_body(self) -> str:
        """渲染任务正文（create_swarm 会自动追加蜂群协议段）。"""
        parts = [self.objective.strip(), ""]
        if self.tools:
            parts.append("建议调用的工具（按需选用，不要机械地全跑一遍）：")
            parts.extend(f"- `{tool}`" for tool in self.tools)
            parts.append("")
        parts.append(
            "产出要求：把可机读的结论写进完成 metadata；"
            "跨专家的发现用结构化评论写到 root 黑板。"
        )
        return "\n".join(parts)

    def as_worker_spec(
        self,
        profile: str,
        max_runtime_seconds: Optional[int] = None,
    ) -> ks.SwarmWorkerSpec:
        """转成 ``SwarmWorkerSpec``。skills 留空——工具指引已在 body 里。"""
        return ks.SwarmWorkerSpec(
            profile=profile,
            title=self.title,
            body=self.render_body(),
            skills=[],
            priority=self.priority,
            max_runtime_seconds=max_runtime_seconds,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "tools": list(self.tools),
            "priority": self.priority,
        }


# ---------------------------------------------------------------------------
# 预设角色
# ---------------------------------------------------------------------------

DEFAULT_RESEARCH_ROLES: tuple[ResearchRole, ...] = (
    ResearchRole(
        key="retrieval",
        title="文献检索：建立候选文献池",
        objective=(
            "围绕研究问题做系统性中英文文献检索，给出候选文献池"
            "（含来源、年份、核心结论、与问题的相关性）。"
            "不要下最终结论，只负责把池子建全、建准，并剔除明显不相关的噪声。"
        ),
        tools=(
            "literature_search",
            "scholarforge_search",
            "scholarforge_save_literature_cards",
        ),
    ),
    ResearchRole(
        key="mapping",
        title="研究地图：画出该问题的研究版图",
        objective=(
            "把候选文献按主题 / 方法 / 结论分歧归类，输出该问题的研究版图："
            "主流路径有哪些、争议点在哪、哪些区域尚属空白。"
            "输出用结构化分类，便于 synthesizer 直接引用。"
        ),
        tools=("scholarforge_research_map", "scholarforge_citation_graph"),
    ),
    ResearchRole(
        key="matrix",
        title="文献矩阵：核心文献逐篇精读",
        objective=(
            "对核心文献做逐篇精读，产出文献矩阵："
            "研究对象 / 方法 / 样本 / 结论 / 局限 / 可信度。"
            "明确标注哪些结论可直接引用、哪些需要存疑、哪些存在冲突。"
        ),
        tools=("scholarforge_literature_matrix", "scholarforge_read_section"),
    ),
    ResearchRole(
        key="critique",
        title="方法学审查：找出该领域的常见硬伤",
        objective=(
            "审查该领域主流研究的方法学缺陷：样本偏差、变量混淆、统计误用、"
            "p-hacking 风险、可重复性问题。给出写作时必须规避的坑清单。"
        ),
        tools=("scholarforge_detect_design_flaws", "scholarforge_review_claims"),
    ),
)

VERIFIER_ROLE = ResearchRole(
    key="verifier",
    title="核验：引用与主张一致性",
    objective=(
        "逐条核验各专家产出：引用是否真实存在、主张是否被原文支持、"
        "是否存在过度概括或断章取义。证据充分才放行"
        '（完成 metadata 写 {"gate": "pass"}），否则精确指出缺什么。'
    ),
    tools=(
        "scholarforge_verify_citations",
        "scholarforge_review_claims",
        "scholarforge_check_stats",
    ),
)

SYNTHESIZER_ROLE = ResearchRole(
    key="synthesizer",
    title="综合：综述 / 章节写作",
    objective=(
        "把已核验的证据综合成最终稿件：结构沿用研究地图，每个论断挂引用，"
        "明确写出分歧与空白。不要引入任何未经核验的新事实。"
    ),
    tools=(
        "scholarforge_outline",
        "scholarforge_write",
        "scholarforge_format_refs",
        "scholarforge_quality_gate",
    ),
)


# ---------------------------------------------------------------------------
# 组装
# ---------------------------------------------------------------------------

def create_research_swarm(
    conn: sqlite3.Connection,
    *,
    goal: str,
    profile: str,
    verifier_profile: Optional[str] = None,
    synthesizer_profile: Optional[str] = None,
    roles: Optional[Iterable[ResearchRole]] = None,
    tenant: Optional[str] = None,
    created_by: str = "research-swarm-orchestrator",
    root_title: Optional[str] = None,
    priority: int = 0,
    idempotency_key: Optional[str] = None,
    workspace_kind: str = "scratch",
    workspace_path: Optional[str] = None,
    max_runtime_seconds: Optional[int] = None,
) -> ks.SwarmCreated:
    """Create a research-group swarm: parallel specialists → verify → synthesize.

    Thin wrapper over :func:`kanban_swarm.create_swarm`. The returned graph is
    immediately dispatchable, exactly like ``vermes kanban swarm`` output.

    Args:
        goal: The research question. Becomes the swarm root's goal and is
            echoed into every worker's body by ``create_swarm``.
        profile: Profile assigned to the parallel specialist workers.
        verifier_profile / synthesizer_profile: profiles for the gating and
            synthesis cards. Both default to ``profile``.
        roles: Override the parallel worker roles. Defaults to
            :data:`DEFAULT_RESEARCH_ROLES`.

    Returns:
        :class:`~vermes_cli.kanban_swarm.SwarmCreated` with
        ``root_id`` / ``worker_ids`` / ``verifier_id`` / ``synthesizer_id``.
    """
    selected = tuple(roles) if roles is not None else DEFAULT_RESEARCH_ROLES
    if not selected:
        raise ValueError("at least one research role is required")
    if not (goal or "").strip():
        raise ValueError("goal is required")
    if not (profile or "").strip():
        raise ValueError("profile is required")

    workers = [
        role.as_worker_spec(profile, max_runtime_seconds) for role in selected
    ]
    first_line = goal.strip().splitlines()[0]

    return ks.create_swarm(
        conn,
        goal=goal,
        workers=workers,
        verifier_assignee=(verifier_profile or profile),
        synthesizer_assignee=(synthesizer_profile or profile),
        root_title=root_title or f"研究组：{first_line[:60]}",
        verifier_title=VERIFIER_ROLE.title,
        synthesizer_title=SYNTHESIZER_ROLE.title,
        tenant=tenant,
        created_by=created_by,
        workspace_kind=workspace_kind,
        workspace_path=workspace_path,
        priority=priority,
        idempotency_key=idempotency_key,
    )


def list_research_roles() -> list[dict[str, Any]]:
    """Return the preset roles (for CLI ``--list-roles`` / dashboards)."""
    return [role.as_dict() for role in DEFAULT_RESEARCH_ROLES]
