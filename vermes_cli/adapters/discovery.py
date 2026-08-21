"""L2a 发现层（UNIVERSAL_OPERATION_LAYER_DESIGN.md §15.2 / §14.5）。

设计纪律（守「薄」）：
- 只做「路由 / 索引 / 两层发现」，绝不写任何垂直逻辑。
- 消费 `SoftwareAdapter.discover_tools()` 的注册结果（§15.1，不改造 L2 契约）。
- 阶段一 `route_toolset()` 纯倒排索引、无 LLM、可单测；直接消化 argmax 无门槛反模式。
- 阶段二 `select_tool()` 接 LLM `tool_choice` 细选，带最低相关阈值，不达标返 `NEEDS_CLARIFY`。

两层发现（L2 真实边界 case，见 §14 / 用户 B 桶发现）：
operation_mechanism=cli_native 的适配器不仅要发现 CLI 二进制（Layer1：cli-anything-freecad），
还要定位目标软件后端（Layer2：freecadcmd / blender）。CLI-Anything 在 macOS 写死
`Contents/MacOS/FreeCADCmd`，但本机实际是 `Contents/Resources/bin/freecadcmd`，
靠 `FREECAD_PATH` 环境变量兜底。BackendLocator 覆盖双候选路径 + 环境变量兜底。
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

# operation_mechanism 取值（与 §15.2 对齐）
CLI_NATIVE = "cli_native"
SDK_BRIDGE = "sdk_bridge"
GUI_AUTOMATION = "gui_automation"
OFFICIAL_API = "official_api"

# select_tool 最低相关阈值：低于此分直接降级 NEEDS_CLARIFY，绝不静默选噪声。
# 经验值：单条强意图词命中即应 > 0.2（见 _score_tool / _score_toolset 归一口径）。
MIN_TOOL_SCORE = 0.2

# route_toolset 命中即纳入候选的最低分（阶段一只是粗筛，阈值可更低）。
MIN_TOOLSET_SCORE = 0.05

# 跨语言护栏（user-memory 跨语言相似度恒为 0 警告）：阶段一无 LLM，纯关键词索引对
# 中文意图会失配。domain 用结构化 tag 匹配 + 极简双语别名桥接，避免中文意图在阶段一
# 直接落空；真正的跨语言细选交给阶段二 LLM tool_choice。
DOMAIN_BILINGUAL_HINTS = {
    "3d": ["3d", "三维", "建模", "模型", "cad", "零件", "倒角", "拉伸", "渲染", "网格", "mesh", "render"],
    "video": ["video", "视频", "剪辑", "特效", "动画", "animation"],
    "office": ["office", "文档", "表格", "ppt", "word", "excel", "pdf"],
    "ide": ["ide", "代码", "编辑器", "editor", "debug"],
}


@dataclass
class ToolSummary:
    """单个工具的能力摘要，供 LLM 阶段二细选（不携带垂直逻辑）。"""

    name: str
    description: str
    subcommand: list[str]
    toolset: str
    operation_mechanism: str = CLI_NATIVE
    intent_keywords: list[str] = field(default_factory=list)


@dataclass
class CapabilityIndex:
    """一个 toolset 的能力索引（倒排索引源）。由 discover_tools() 注册时 build。"""

    toolset: str
    domain: str
    operation_mechanism: str
    intent_keywords: list[str]
    tools: list[ToolSummary] = field(default_factory=list)


@dataclass
class ToolsetRef:
    """route_toolset 返回的候选 toolset 引用（含命中分数，供 UI 排序）。"""

    toolset: str
    domain: str
    operation_mechanism: str
    score: float
    matched_keywords: list[str] = field(default_factory=list)


@dataclass
class ToolChoice:
    """select_tool 的返回：要么放行某个工具，要么要求澄清。"""

    decision: str  # "allow_tool" | "needs_clarify"
    tool: Optional[ToolSummary] = None
    score: float = 0.0
    reason: str = ""


NEEDS_CLARIFY = "needs_clarify"


# ---------------------------------------------------------------------------
# 两层发现：BackendLocator
# ---------------------------------------------------------------------------

@dataclass
class BackendTarget:
    """两层发现结果：CLI 二进制（Layer1）+ 目标软件后端（Layer2）。"""

    software: str
    cli_bin: str  # Layer1：cli-anything-freecad
    bin_name: str  # Layer2 后端二进制名（freecadcmd / blender）
    cli_resolved: Optional[str]  # Layer1 解析路径
    backend_resolved: Optional[str]  # Layer2 解析路径
    env_var: str  # FREECAD_PATH / BLENDER_PATH
    env_value: Optional[str]  # 注入 subprocess 的环境变量值（后端路径兜底）


class BackendLocator:
    """定位 cli_native 适配器的两层目标：CLI 二进制 + 后端软件。

    真实边界 case（用户 B 桶发现，CLI-Anything macOS 路径 bug）：
    CLI-Anything 写死 /Applications/FreeCAD.app/Contents/MacOS/FreeCADCmd，
    本机实际是 /Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd。
    解决：环境变量兜底 → PATH → macOS 双候选 app bundle 路径。
    """

    BACKEND_BIN_NAMES = {
        "freecad": ["freecadcmd", "FreeCADCmd", "freecad", "FreeCAD"],
        "blender": ["blender", "Blender"],
    }

    MACOS_APP_PATHS = {
        "freecad": [
            "/Applications/FreeCAD.app/Contents/MacOS/FreeCADCmd",
            "/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd",
        ],
        "blender": [
            "/Applications/Blender.app/Contents/MacOS/Blender",
            "/Applications/Blender.app/Contents/MacOS/blender",
        ],
    }

    ENV_VARS = {"freecad": "FREECAD_PATH", "blender": "BLENDER_PATH"}

    def locate(self, software: str, cli_bin: Optional[str] = None) -> BackendTarget:
        # Layer 1：CLI 二进制（cli-anything-freecad）
        cli_resolved = shutil.which(cli_bin) if cli_bin else None

        # Layer 2：目标软件后端（freecadcmd / blender）
        env_var = self.ENV_VARS.get(software, f"{software.upper()}_PATH")
        # ① 环境变量兜底（用户 workaround，最高优先）
        env_value = os.environ.get(env_var)
        backend_resolved: Optional[str] = None
        if env_value and Path(env_value).exists():
            backend_resolved = env_value
        # ② PATH 查找
        if backend_resolved is None:
            for name in self.BACKEND_BIN_NAMES.get(software, [software]):
                p = shutil.which(name)
                if p:
                    backend_resolved = p
                    break
        # ③ macOS app bundle 双候选路径（覆盖 CLI-Anything 写死的单一路径）
        if backend_resolved is None:
            for cand in self.MACOS_APP_PATHS.get(software, []):
                if Path(cand).exists():
                    backend_resolved = cand
                    break

        return BackendTarget(
            software=software,
            cli_bin=cli_bin or "",
            bin_name=self.BACKEND_BIN_NAMES.get(software, [software])[0],
            cli_resolved=cli_resolved,
            backend_resolved=backend_resolved,
            env_var=env_var,
            env_value=backend_resolved,
        )


# ---------------------------------------------------------------------------
# 阶段一：route_toolset（无 LLM，纯倒排索引）
# ---------------------------------------------------------------------------

def _tokenize(text: str) -> list[str]:
    """小写 + 按非词字符切分；中文按字保留（与英文词混合）。"""
    import re

    # 英文/数字词
    toks = re.findall(r"[a-z0-9_]+", text.lower())
    # 中文连续段按字拆，避免整句当一个 token 失配
    for seg in re.findall(r"[\u4e00-\u9fff]+", text):
        toks.extend(list(seg))
    return [t for t in toks if t]


def _domain_match_score(domain: str, intent: str, intent_tokens: list[str]) -> tuple[float, list[str]]:
    """结构化 domain tag 匹配 + 极简双语别名桥接（跨语言护栏）。

    CJK 意图按字切分后无法与多字 hint（如「倒角」）做 token 相等匹配，故对 CJK hint
    用子串包含（hint in 原句）兜底，避免跨语言相似度恒为 0（user-memory 警告）。
    """
    hints = DOMAIN_BILINGUAL_HINTS.get(domain, [domain])
    matched = [h for h in hints if h in intent_tokens or h in intent]
    # domain 命中给较高权重（结构信号强于关键词）
    return (min(1.0, 0.6 + 0.1 * len(matched)), matched) if matched else (0.0, [])


def route_toolset(
    intent: str,
    index: Optional[list[CapabilityIndex]] = None,
) -> list[ToolsetRef]:
    """基于 domain 结构匹配 + intent_keywords 倒排，返回候选 toolset（可多命中，降序）。

    纯索引、无 LLM。命中分 >= MIN_TOOLSET_SCORE 才纳入候选。
    """
    from .discovery_registry import CAPABILITY_REGISTRY

    idx = index if index is not None else CAPABILITY_REGISTRY.all()
    tokens = _tokenize(intent)
    intent_lc = intent.lower()
    refs: list[ToolsetRef] = []
    for cap in idx:
        dom_score, dom_matched = _domain_match_score(cap.domain, intent_lc, tokens)
        kw_matched = [k for k in cap.intent_keywords if k.lower() in tokens]
        # 关键词命中按占比给分（归一：命中数 / max(1, 关键词总数) 的温和折让）
        kw_score = min(0.5, 0.1 * len(kw_matched)) if kw_matched else 0.0
        score = max(dom_score, kw_score)
        if score >= MIN_TOOLSET_SCORE:
            refs.append(
                ToolsetRef(
                    toolset=cap.toolset,
                    domain=cap.domain,
                    operation_mechanism=cap.operation_mechanism,
                    score=round(score, 3),
                    matched_keywords=dom_matched + kw_matched,
                )
            )
    refs.sort(key=lambda r: r.score, reverse=True)
    return refs


# ---------------------------------------------------------------------------
# 阶段二：select_tool（接 LLM tool_choice，带最低阈值降级）
# ---------------------------------------------------------------------------

def _score_tool(tool: ToolSummary, intent_tokens: list[str]) -> float:
    """工具级相关度：name + subcommand + description + intent_keywords 的混合命中占比。

    归一口径：命中词数 / max(1, 意图词数)，上限 1.0。避免长描述刷分。
    """
    hay = " ".join(
        [tool.name, " ".join(tool.subcommand), tool.description, " ".join(tool.intent_keywords)]
    ).lower()
    hits = sum(1 for t in intent_tokens if t and t in hay)
    return min(1.0, hits / max(1, len(intent_tokens)))


def select_tool(
    tools: list[ToolSummary],
    intent: str,
    ctx: Optional[dict] = None,
    llm_chooser: Optional[Callable[[list[ToolSummary], str, Optional[dict]], str]] = None,
) -> ToolChoice:
    """在候选 toolset 内细选 1 个工具。

    - llm_chooser 提供时（接 runtime_provider LLM 的 tool_choice）：返回工具名，再用
      相关度阈值把关，消化 argmax 无门槛反模式。
    - llm_chooser 返回 None = LLM 不可用（未配置/失败），降级到启发式 argmax 兜底。
    - 不提供时（沙箱/单测）：用启发式 argmax 兜底，但同样受最低阈值约束。
    - 相关度 < MIN_TOOL_SCORE → 返 NEEDS_CLARIFY（不静默选噪声）。
    """
    if not tools:
        return ToolChoice(NEEDS_CLARIFY, reason="候选工具集为空")

    tokens = _tokenize(intent)
    scores = {t.name: _score_tool(t, tokens) for t in tools}

    if llm_chooser is not None:
        chosen_name = llm_chooser(tools, intent, ctx)
        # 返回 None = LLM 不可用，降级到下方启发式 argmax（而非误判为选错）。
        if chosen_name is not None:
            chosen = next((t for t in tools if t.name == chosen_name), None)
            if chosen is None:
                return ToolChoice(NEEDS_CLARIFY, reason=f"LLM 选中的工具 {chosen_name} 不在候选集")
            score = scores[chosen_name]
            if score < MIN_TOOL_SCORE:
                return ToolChoice(
                    NEEDS_CLARIFY,
                    reason=f"LLM 选中 {chosen_name} 但相关度 {score:.2f} < 阈值 {MIN_TOOL_SCORE}",
                )
            return ToolChoice("allow_tool", tool=chosen, score=round(score, 3))

    # 启发式兜底（无 LLM）：argmax + 最低阈值
    best = max(tools, key=lambda t: scores[t.name])
    best_score = scores[best.name]
    if best_score < MIN_TOOL_SCORE:
        return ToolChoice(
            NEEDS_CLARIFY,
            reason=f"最高相关度 {best_score:.2f} < 阈值 {MIN_TOOL_SCORE}，需澄清意图",
        )
    return ToolChoice("allow_tool", tool=best, score=round(best_score, 3))
