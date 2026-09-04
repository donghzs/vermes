"""ACP transport factory for the Bot 神魔堂 (请神收尾 T2).

把一条 recipe（T1 的产物）翻译成可运行的 ACP transport 实例：
``build_acp_transport(recipe)`` 读取 recipe 的 ``entry_point`` / ``args``，
构造 ``AcpAgentTransportBase``（泛型基类）或 Copilot 子类（保留弃用守卫）。

这是「目录即 agent 食谱」模式的落地层：
丢一个 yaml（T1）→ 这里变成真正能 spawn + 握手外部 agent 的 transport（T2）
→ 接入运行时注册（T3）。
"""

from __future__ import annotations

from typing import Any

from agent.copilot_acp_client import AcpAgentTransportBase, CopilotACPClient

from .recipes.schema import RecipeConfig

# provider → transport class 映射。
# - copilot-acp → CopilotACPClient（保留 gh-copilot 弃用守卫，与生产路径一致）
# - 其余任意 acp-* provider → AcpAgentTransportBase（泛型，由 entry_point/args
#   决定 spawn 谁——Codex/Claude/... 都走基类，仅 recipe 不同）
_PROVIDER_TRANSPORT_REGISTRY: dict[str, type[AcpAgentTransportBase]] = {
    "copilot-acp": CopilotACPClient,
}


def build_acp_transport(recipe: RecipeConfig, **kwargs: Any) -> AcpAgentTransportBase:
    """Build a runnable ACP transport from a recipe.

    The recipe's ``entry_point`` / ``args`` become the spawned argv
    (``[entry_point, *args]``), exactly as ``AcpAgentTransportBase._run_prompt``
    expects. Copilot keeps its deprecation guard via ``CopilotACPClient``; every
    other ACP-compatible agent uses the generic base class — differentiated only
    by the recipe's ``entry_point`` / ``args`` (e.g. Zed npx adapters).

    Raises ValueError if the recipe is not an ACP transport.
    """
    if not recipe.is_acp:
        raise ValueError(
            f"recipe '{recipe.name}' transport is '{recipe.transport}', not 'acp'; "
            f"build_acp_transport only handles ACP transports."
        )

    cls = _PROVIDER_TRANSPORT_REGISTRY.get(
        recipe.provider or "", AcpAgentTransportBase
    )
    return cls(
        acp_command=recipe.entry_point,
        acp_args=list(recipe.args),
        **kwargs,
    )
