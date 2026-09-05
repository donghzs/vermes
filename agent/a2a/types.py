"""A2A v1.0 协议核心类型（① P0 地基）。

异构多智能体互操作的「电线协议」统一类型定义：

  - AgentHandle    —— 统一 agent 标识（vermes://<profile_id> 或 @<name>）
  - A2AEnvelope    —— 消息信封（from/to/room_id/kind/payload）
  - MessageKind    —— 信封 kind 枚举，原生承载「三股流动」语义

设计原则（见 docs/plans/2026-09-02-vermes-catchup-roadmap-final.md §5 ①）：
  1. 初版「透传」——信封只转发不改语义（无智能调度）；联邦成熟后再进阶「翻译」。
  2. kind 字段除常规 message/task/result 外，原生支持 skill/tool/knowledge
     三类交换，让「相互协作、相互教育、相互进步」成为协议一等语义。

纯 dataclass，零外部依赖，供 transports / registry / delegate 桥共用。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, Optional


# @mention 复用：@<name>（英文/数字/下划线/连字符）或 @中文名（非空白字符）。
# 与既有渠道 @mention 正则（matrix/feishu/telegram）保持同一工程语言，
# 但这里是「协议层」的 handle 解析，不绑定具体渠道 SDK。
_HANDLE_RE = re.compile(r"@([\w\u4e00-\u9fff][\w\u4e00-\u9fff.-]*)")


class MessageKind(str, Enum):
    """信封 kind 枚举。

    常规三类 + 「三股流动」三类（skill/tool/knowledge）。
    """
    MESSAGE = "message"        # 普通对话消息
    TASK = "task"              # 任务委派
    RESULT = "result"          # 任务结果回收
    SKILL = "skill"            # 技能 / 技能包引用共享
    TOOL = "tool"              # 工具 / MCP 能力借用与归属
    KNOWLEDGE = "knowledge"    # 经验 / 反模式 / @decision / @preference 跨 agent 流动


@dataclass(frozen=True)
class AgentHandle:
    """统一 agent 标识。

    两种形态：
      - `vermes://<profile_id>`（规范 URI，注册到 AgentRegistry 的主键）
      - `@<name>`（人读友好别名，经 resolver 映射）

    capabilities 为能力标签（code/search/writing/vision/...），
    随注册写入 AgentRegistry，供群聊调度按标签匹配派活。
    """

    profile_id: str
    name: str = ""
    provider: str = ""      # 异构：每 bot 接不同 LLM provider
    model: str = ""         # 异构：每 bot 接不同 model
    transport: str = "local"  # local / mcp / http / subprocess / acp / ws
    capabilities: tuple = field(default_factory=tuple)
    registered_at: float = field(default_factory=time.time)
    # ⑭ dispatch：登堂所用 recipe.name（vermes_cli/a2a/recipes/ 下的 yaml 名）。
    # 属「寻址信息」——决定怎么 spawn 这个 agent，故随 handle 一起流转，
    # dispatch 时无需二次查库。空串表示非 recipe 驱动（local 等）或历史行。
    recipe: str = ""

    def uri(self) -> str:
        return f"vermes://{self.profile_id}"

    def display(self) -> str:
        return self.name or self.profile_id

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["capabilities"] = list(self.capabilities)
        return d


@dataclass
class A2AEnvelope:
    """消息信封。

    kind 见 MessageKind；payload 为任意可 JSON 序列化对象。
    room_id 标识群聊房间（单房间/多房间路由用），DM 可为空。
    """

    from_handle: str          # 发送方 handle（vermes://... 或 @name）
    to_handle: str            # 接收方 handle
    kind: str = MessageKind.MESSAGE.value
    payload: Dict[str, Any] = field(default_factory=dict)
    room_id: str = ""
    message_id: str = ""      # 幂等去重用，空则由发送方生成
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def parse_mention_handles(text: str) -> list[str]:
    """从文本提取 @handle 列表（协议层 @mention 解析，复用于 AgentHandle resolver）。"""
    return _HANDLE_RE.findall(text or "")


def normalize_handle(raw: str) -> str:
    """把 @name 或 vermes://id 归一为规范 handle 字符串。

    规则：
      - `vermes://xxx` → 原样（已是规范 URI）
      - `@name` / `name` → 保留 name（registry resolver 负责映射到 profile_id）
    不在此处解析 name→id（依赖 AgentRegistry 路由表，见 registry.py）。
    """
    if not raw:
        return ""
    s = raw.strip()
    if s.startswith("vermes://"):
        return s
    if s.startswith("@"):
        return s
    return "@" + s
