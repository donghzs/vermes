"""神魔堂大升级·诚实标注(2026-09-08) 测试。

回应 QClaw 审计的 3 个问题：
- 问题 1：registry 标签是「推断」非官方——capability_source 标记 inferred/official，
  dispatcher prompt 标注(推测) 防 LLM 过度信任雷同标签。
- 问题 3：38 条标签雷同 → prompt 同时透传 description，让 LLM 靠简介真实区分。
- 问题 2：recipe 字段名 capabilities / profile 字段名 capability_tags 口径清晰，
  测试覆盖两端取值。
"""

import sys

sys.path.insert(0, "/Users/dongzusheng/Projects/vermes-electron")

from vermes_cli.botmode import org_engine as oe
from vermes_cli.a2a.recipes.generate_from_registry import infer_capabilities


def test_exec_capability_line_marks_inferred_with_description():
    """_exec_capability_line：带标签 + 简介 → 标注(推测) 且附简介。"""
    p = {"name": "Kimi", "capability_tags": ["code", "refactor", "search"],
         "description": "Moonshot AI 的通用编码助手，中文强"}
    line = oe._exec_capability_line(p)
    assert "Kimi" in line
    assert "能力(推测)" in line, "推断标签必须标注(推测)"
    assert "code" in line and "refactor" in line
    assert "简介" in line and "Moonshot" in line, "必须透传 description 供 LLM 区分"


def test_exec_capability_line_unlabeled_is_honest():
    """无标签 → 显式『未标注』，不伪装成能力。"""
    p = {"name": "X", "capability_tags": []}
    line = oe._exec_capability_line(p)
    assert "能力：未标注" in line


def test_build_candidate_list_marks_inferred():
    """build_candidate_list（秘书拉人）：候选行标注(推测) 且带简介。"""
    profiles = {
        "a2a:devin": {"name": "Devin", "transport": "acp", "provider": "acp-devin",
                      "model": "acp-devin", "capability_tags": ["code", "refactor", "search"],
                      "description": "Devin CLI coding agent by Cognition（自主 SWE）"},
    }
    out = oe.build_candidate_list(profiles)
    assert "能力(推测)" in out, "秘书候选清单应标注推测"
    assert "Devin CLI coding agent" in out, "应透传简介"


def test_recipe_capability_source_parsed():
    """schema 解析：registry=inferred，手写=official。"""
    from vermes_cli.a2a.recipes.loader import load_recipe
    reg = load_recipe("vermes_cli/a2a/recipes/registry/devin.yaml")
    assert reg.capability_source == "inferred"
    assert "code" in reg.capabilities
    hand = load_recipe("vermes_cli/a2a/recipes/claude-agent-acp.yaml")
    assert hand.capability_source == "official"


def test_infer_capabilities_baseline_and_differentiators():
    """generator 推断：编码 agent 基线恒真，描述信号加区分项。"""
    assert infer_capabilities("devin", "Devin CLI coding agent") == ["code", "refactor", "search"]
    assert "devops" in infer_capabilities("stakpak", "Open-source DevOps agent in Rust")
    assert "edit" in infer_capabilities("cursor", "Cursor's coding agent")
    assert "reasoning" in infer_capabilities("claude-acp", "Anthropic's Claude")
    # 确定性
    assert infer_capabilities("kimi", "coding assistant") == infer_capabilities("kimi", "coding assistant")
