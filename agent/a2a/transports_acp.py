"""A2A ACP transport —— 神魔堂「登堂」agent 的真实 dispatch 通路（⑭）。

把 A2AEnvelope 投递为一次**真实** ACP 调用：

    envelope.to_handle → AgentRegistry.resolve() → AgentHandle.recipe
    → find_recipe() → build_acp_transport() → chat.completions.create()
    → 返回文本

与 ``transports_subprocess.py`` 的分工：
    subprocess = codex_app_server 桥接桩（初版透传，未接线）
    acp        = ⑭ 请神收尾的 recipe 驱动通路（本文件，已接线）

命名对齐：register 端点写 ``transport="acp"``，故 adapter 名同为 ``"acp"``，
``get_a2a_transport("acp")`` 直接命中，无需映射。

recipe 来源：a2a_agents.recipe 列（T1 新增），随 AgentHandle 一起流转。
历史行（未接线前注册的）recipe 为 NULL → 明确报错而非静默透传，
避免「以为 dispatch 了其实没 spawn」这类假成功。
"""

from __future__ import annotations

from typing import Any, Optional

from agent.a2a.transports import AgentTransport, register_a2a_transport
from agent.a2a.types import A2AEnvelope, MessageKind

# 可 dispatch 的信封类型（其余类型 ACP 通路无对应语义 → 透传）
_DISPATCHABLE = (MessageKind.MESSAGE.value, MessageKind.TASK.value)


class AcpTransport(AgentTransport):
    name = "acp"
    capabilities = ("acp", "code", "agent")

    def send(self, envelope: A2AEnvelope) -> Any:
        """把信封投递给一个已登堂的 ACP agent（真实 spawn + 对话）。

        返回结构与 LocalTransport 对齐：
        成功 ``{"transport": "acp", "result": <文本>}``；
        失败 ``{"transport": "acp", "error": <原因>}``（不抛，协议层不阻断）。
        """
        if envelope.kind not in _DISPATCHABLE:
            # 非消息/任务类信封：ACP 通路无对应语义，透传（与 LocalTransport 一致）
            return {
                "transport": self.name,
                "kind": envelope.kind,
                "payload": envelope.payload,
            }

        payload = envelope.payload or {}
        text = payload.get("goal") or payload.get("text") or payload.get("message") or ""
        if not text:
            return {"transport": self.name, "error": "empty message text in payload"}

        # 延迟 import：vermes_cli 侧依赖 agent 侧，模块级 import 会成环
        try:
            from agent.a2a.registry import AgentRegistry
            from vermes_cli.a2a.recipes.loader import RECIPES_DIR, find_recipe
            from vermes_cli.a2a.transport import build_acp_transport
        except ImportError as exc:  # pragma: no cover - 依赖缺失属部署问题
            return {"transport": self.name, "error": f"a2a dispatch deps unavailable: {exc}"}

        # 1) 寻址：@name / vermes://id → AgentHandle（离线/未知 → None）
        handle = AgentRegistry().resolve(envelope.to_handle)
        if handle is None:
            return {
                "transport": self.name,
                "error": f"agent not resolvable (offline or unknown): {envelope.to_handle!r}",
            }

        # 2) recipe：决定怎么 spawn 这个 agent。缺失即报错，不静默透传。
        if not handle.recipe:
            return {
                "transport": self.name,
                "error": (
                    f"agent {handle.name or handle.profile_id!r} has no recipe on record; "
                    "re-register via /api/agents/register-profile to enable dispatch"
                ),
            }

        recipe = find_recipe(handle.recipe, RECIPES_DIR, recursive=True)
        if recipe is None:
            return {
                "transport": self.name,
                "error": f"recipe not found: {handle.recipe!r}",
            }

        # 3) spawn + 对话（transport 内部走 _run_prompt：initialize → session/new
        #    → session/prompt → 回收 chunk）
        try:
            transport = build_acp_transport(recipe)
            response = transport.chat.completions.create(
                messages=[{"role": "user", "content": text}],
                model=handle.model or recipe.provider or "acp-agent",
            )
        except Exception as exc:  # spawn/握手/对话任一失败都不阻断协议层
            return {"transport": self.name, "error": f"acp call failed: {exc}"}

        # 4) 归一化：OpenAI 风格响应 → 文本
        try:
            content = response.choices[0].message.content
        except Exception as exc:
            return {"transport": self.name, "error": f"malformed acp response: {exc}"}

        return {"transport": self.name, "result": content}

    async def asend(self, envelope: A2AEnvelope) -> Any:
        """ACP 调用是阻塞 stdio 往返，异步入口直接复用同步实现。

        （未跑事件循环 offload —— dispatch 调用方本身已在 worker 线程里；
        若将来进入 asyncio 主循环，此处应改 run_in_executor。）
        """
        return self.send(envelope)


register_a2a_transport("acp", AcpTransport)
