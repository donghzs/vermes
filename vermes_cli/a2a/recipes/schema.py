"""Recipe schema for the Bot 神魔堂 (请神收尾 T1).

每条 recipe 描述一个可被 Vermes 经 ACP transport 拉入的异构 agent：
探测指纹、鉴权来源、spawn 入口、能力标签。``recipes/*.yaml`` 是
「目录即 agent 食谱」——用户/社区丢一个 yaml 即接入一个新 agent。

entry_point 约定（关键）：
- 走 ACP 的 agent 大多经 Zed 维护的 npx 适配器包，而非裸 CLI。
  - Codex 本体无 --acp → entry_point: npx, args: ["@agentclientprotocol/codex-acp@1.8.0"]
  - Claude Code 本体是 ACP client 非 server → entry_point: npx, args: ["@agentclientprotocol/claude-agent-acp@0.73.0"]
  - Copilot 原生支持 → entry_point: copilot, args: ["--acp", "--stdio"]
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RecipeAuth:
    scheme: str = "apikey"
    env_var: str | None = None
    fallback_settings: str | None = None


@dataclass
class RecipeFingerprint:
    cli: list[str] = field(default_factory=list)
    config_dirs: list[str] = field(default_factory=list)
    app_bundles: list[str] = field(default_factory=list)
    mcp_config: list[str] = field(default_factory=list)


@dataclass
class RecipeConfig:
    name: str
    version: str
    description: str
    transport: str
    entry_point: str
    args: list[str] = field(default_factory=list)
    auth: RecipeAuth = field(default_factory=RecipeAuth)
    capabilities: list[str] = field(default_factory=list)
    # 能力标签的来源标记（神魔堂大升级·诚实标注 2026-09-08）：
    #   "official"  —— 用户/社区手工钉死的真值标签（如手写 6 条核心 recipe）
    #   "inferred"  —— 由描述启发式推断（registry 38 条，上游无 capabilities 字段）
    #   "unknown"   —— 未标注（generator 默认留空，不瞎编）
    # 该标记用于提示 dispatcher LLM：inferred/unknown 的标签是「推测」而非权威，
    # 分派时还应结合 description 判断，避免被雷同标签误导。
    capability_source: str = "inferred"
    fingerprint: RecipeFingerprint = field(default_factory=RecipeFingerprint)
    provider: str | None = None
    # 傻瓜式安装引导（神魔堂公开版收口 2026-09-07）：用户电脑没装对应 CLI 时，
    # 前端据此展示「怎么装」——给官网/安装命令/提示文案，一键复制跳转。
    install_hint: str | None = None

    @property
    def spawn_command(self) -> list[str]:
        """The exact argv used to spawn this agent over stdio."""
        return [self.entry_point, *self.args]

    @property
    def is_acp(self) -> bool:
        return self.transport == "acp"

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, source: str = "<recipe>") -> "RecipeConfig":
        """Build a RecipeConfig from a dict, with friendly validation errors."""
        if not isinstance(data, dict):
            raise ValueError(f"{source}: recipe must be a YAML mapping, got {type(data).__name__}")

        required = ("name", "version", "description", "transport", "entry_point")
        for field_name in required:
            if field_name not in data:
                raise ValueError(f"{source}: missing required field '{field_name}'")

        for field_name in required:
            if not isinstance(data[field_name], str) or not data[field_name].strip():
                raise ValueError(f"{source}: field '{field_name}' must be a non-empty string")

        raw_args = data.get("args") or []
        if not isinstance(raw_args, list) or not all(isinstance(a, str) for a in raw_args):
            raise ValueError(f"{source}: field 'args' must be a list of strings")

        raw_caps = data.get("capabilities") or []
        if not isinstance(raw_caps, list) or not all(isinstance(c, str) for c in raw_caps):
            raise ValueError(f"{source}: field 'capabilities' must be a list of strings")

        auth_raw = data.get("auth") or {}
        if not isinstance(auth_raw, dict):
            raise ValueError(f"{source}: field 'auth' must be a mapping")
        fp_raw = data.get("fingerprint") or {}
        if not isinstance(fp_raw, dict):
            raise ValueError(f"{source}: field 'fingerprint' must be a mapping")
        for fp_key in ("cli", "config_dirs", "app_bundles", "mcp_config"):
            if fp_key in fp_raw and (
                not isinstance(fp_raw[fp_key], list)
                or not all(isinstance(x, str) for x in fp_raw[fp_key])
            ):
                raise ValueError(f"{source}: fingerprint.{fp_key} must be a list of strings")

        return cls(
            name=data["name"].strip(),
            version=data["version"].strip(),
            description=data["description"].strip(),
            transport=data["transport"].strip(),
            entry_point=data["entry_point"].strip(),
            args=list(raw_args),
            auth=RecipeAuth(
                scheme=str(auth_raw.get("scheme", "apikey")).strip(),
                env_var=(auth_raw.get("env_var") or None),
                fallback_settings=(auth_raw.get("fallback_settings") or None),
            ),
            capabilities=list(raw_caps),
            capability_source=str(data.get("capability_source") or "inferred").strip(),
            fingerprint=RecipeFingerprint(
                cli=list(fp_raw.get("cli") or []),
                config_dirs=list(fp_raw.get("config_dirs") or []),
                app_bundles=list(fp_raw.get("app_bundles") or []),
                mcp_config=list(fp_raw.get("mcp_config") or []),
            ),
            provider=(data.get("provider") or None),
            install_hint=(data.get("install_hint") or data.get("website") or None),
        )
