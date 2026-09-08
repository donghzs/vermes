"""ACP transport factory for the Bot 神魔堂 (请神收尾 T2).

把一条 recipe（T1 的产物）翻译成可运行的 ACP transport 实例：
``build_acp_transport(recipe)`` 读取 recipe 的 ``entry_point`` / ``args``，
构造 ``AcpAgentTransportBase``（泛型基类）或 Copilot 子类（保留弃用守卫）。

这是「目录即 agent 食谱」模式的落地层：
丢一个 yaml（T1）→ 这里变成真正能 spawn + 握手外部 agent 的 transport（T2）
→ 接入运行时注册（T3）。
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from agent.copilot_acp_client import AcpAgentTransportBase, CopilotACPClient

from .credentials import get_credential
from .recipes.schema import RecipeConfig

# provider → transport class 映射。
# - copilot-acp → CopilotACPClient（保留 gh-copilot 弃用守卫，与生产路径一致）
# - 其余任意 acp-* provider → AcpAgentTransportBase（泛型，由 entry_point/args
#   决定 spawn 谁——Codex/Claude/... 都走基类，仅 recipe 不同）
_PROVIDER_TRANSPORT_REGISTRY: dict[str, type[AcpAgentTransportBase]] = {
    "copilot-acp": CopilotACPClient,
}


def _resolve_entry_point(entry_point: str) -> str:
    """把 recipe 的 entry_point 解析成可 spawn 的路径。

    若 entry_point 已是绝对路径或 PATH 可命中则原样返回；否则遍历额外 CLI
    目录（与 agent_discovery._extra_cli_dirs 一致）兜底——WorkBuddy 内置的
    codebuddy 藏在 app.asar.unpacked/cli/bin，PATH 不含但需能 spawn。
    """
    ep = (entry_point or "").strip()
    if not ep:
        return ep
    if ep.startswith("/") or ep.startswith("~") or "/" in ep:
        return os.path.expanduser(ep)
    if shutil.which(ep):
        return ep
    # PATH 落空 → 遍历已知额外目录直查（与 discovery 的 _extra_cli_dirs 同源）
    home = Path.home()
    qclaw_root = home / "Library" / "Application Support" / "QClaw"
    extra_dirs = [
        Path("/opt/homebrew/bin"),
        Path("/usr/local/bin"),
        qclaw_root / "npm-global" / "bin",
        home / ".npm-global" / "bin",
        home / ".local" / "bin",
        home / ".cargo" / "bin",
        qclaw_root / "openclaw" / "config" / "bin",
        home / ".config" / "openclaw" / "config" / "bin",
        Path("/Applications/WorkBuddy.app/Contents/Resources/app.asar.unpacked/cli/bin"),
    ]
    for d in extra_dirs:
        cand = d / ep
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand)
    return ep


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
    # 授权 Key 持久化 · 读侧解析：优先进程环境，回退到用户凭据库
    # （~/.vermes/agent_auth.json）。凭据库 key 一律用 recipe.name —— 唯一；
    # 不用 recipe.auth.fallback_settings（那是 settings.json 路径字面量，
    # 三条手写 recipe 该值相同，会让 key 共用导致覆盖——P0 已修）。
    auth_env = recipe.auth.env_var
    auth_value = None
    if auth_env:
        auth_value = os.environ.get(auth_env)
        if not auth_value:
            auth_value = get_credential(recipe.name)
    return cls(
        acp_command=_resolve_entry_point(recipe.entry_point),
        acp_args=list(recipe.args),
        auth_env=auth_env,
        auth_value=auth_value,
        **kwargs,
    )
