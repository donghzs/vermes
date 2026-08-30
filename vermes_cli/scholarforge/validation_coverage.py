"""ScholarForge 工具校验覆盖清单（P4-3）。

每个已注册工具映射其校验层级（Tier）：
- T1：输入 / 状态校验（参数非空、类型、project 存在性、空状态处理）
- T2：结构 / 内容校验（quality_gate 写回钩子、引用一致性、格式校验）
- T3：深度语义校验（专用 validator：引文真实性 / 统计一致性 / 设计缺陷 /
      全文查重 / AIGC 检测 / 论点审查 / 综合质量闸门）

覆盖度口径（用户拍板 Q4）：27 工具中 ≥25 个具备 ≥1 层校验即收口。

本清单由 P4-3 家底审计（逐 handler 实读）沉淀，非凭记忆。任何工具接线变化须同步更新此处。
"""
from __future__ import annotations

from typing import Dict, List

# 工具短名 → 校验层级（1/2/3）。键与 register_tools 注册的 scholarforge_<name> 对应。
VALIDATED_TOOLS: Dict[str, List[int]] = {
    # ── 深度校验（T3）──
    "write": [1, 2],                  # L756 run_quality_gate + 写回 DB 往返 verify_fn(L3769)
    "review": [1, 3],                 # L810 输入 + L855 check_aigc
    "replace_citations": [1, 2, 3],    # L960 输入 + L1242 _fuzzy_verify + L1280 run_citation_gate→verify_citation_authenticity
    "plagiarism_check": [1, 3],        # L1728 输入 + L1735 full_plagiarism_check
    "deaigc": [1, 3],                  # L1836 输入 + L1843 check_aigc
    "score": [1, 3],                   # L1938 输入 + L1944 score_paper
    "verify_citations": [1, 3],        # L2267 输入 + L2272 verify_citation_authenticity
    "check_stats": [1, 3],             # L2882 输入 + L2887 check_statistics_consistency
    "detect_design_flaws": [1, 3],      # L2946 输入 + L2964 detect_design_flaws(+llm)
    "review_claims": [1, 3],           # L2329 输入 + L2340 review_claims
    "quality_gate": [1, 3],            # L3132 输入 + L3136 run_full_quality_gate→run_all_validators
    # ── 状态 / 存在性校验（T1）──
    "set_active_project": [1],         # L3660 int 解析 + L3667 get_project 存在性
    "read_section": [1],               # L3615 PROJECT_ID_MISSING + L3630 空段落
    "list_projects": [1],              # L3576 空项目状态感知响应（非静默空列表）
    "literature_matrix": [1],          # P4-3 补 L2571 T1：topic/tag 至少提供一个
    # ── 纯输入 / 转换 / 抓取（T1，深度校验预期之外，列为已校准）──
    "search": [1],                     # L567 查询非空
    "learn_style": [1],                # L1367 项目守卫 + L1370 样本≥100
    "outline": [1],                    # L1530 输入 + L3806 写回 verify_fn
    "polish": [1],                     # L1633 文本非空
    "export": [1],                     # L2068 标题/内容非空 + L2161 fmt 枚举
    "format_refs": [1],                # L3021 输入 + L3027 JSON 解析 + L3063 style 枚举
    "research_map": [1],               # L2387 topic 非空
    "save_literature_cards": [1],      # L2447 JSON 解析 + L2454 输入
    "manage_snapshots": [1],           # L2609+ 动作路由 + 参数守卫
    "apply_template": [1],             # L2755+ 动作路由 + L2786 模板存在性
    "citation_graph": [1],             # L3470 paper_id 非空 + L3476 类型强转
    "run_pipeline": [1],               # L3208 message 非空（深层校验经下游子工具）
}


def get_validation_tiers(tool_short_name: str) -> List[int]:
    """返回工具短名的校验层级列表（无记录返空列表）。"""
    return list(VALIDATED_TOOLS.get(tool_short_name, []))


def coverage_passing(threshold: int = 25) -> bool:
    """≥ threshold 个工具具备 ≥1 层校验即收口。"""
    validated = sum(1 for t in VALIDATED_TOOLS.values() if t)
    return validated >= threshold


# 注册时工具全名前缀
_TOOL_PREFIX = "scholarforge_"


def short_name(registered_name: str) -> str:
    return registered_name[len(_TOOL_PREFIX):] if registered_name.startswith(_TOOL_PREFIX) else registered_name
