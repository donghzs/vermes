"""⑭ Bot 实验室：本机 agent 发现（CLI 二进制 + 配置目录 + macOS app bundle + MCP 配置）。

设计纪律（仿 registry.py fail-open 哲学）：
- 四类信号源各自独立探测，任一失败仅记 debug 日志并跳过，不阻断其他源。
- 产出归一为 ``AgentDiscovery``；由 ``registry.py:_discover_agents`` 转 BrickEntry(type="agent")。
- 不安装、不启动 agent，只做「发现 + 元数据」，生命周期由用户/外部系统管理。

信号源：
  - cli       : shutil.which 探测已知 agent CLI（claude/codex/openclaw/qclaw/gemini/aider/goose...）
  - config    : home 下已知 agent 配置目录存在即视为已装（home-dir based）
  - app       : /Applications 下已知 *.app bundle
  - mcp       : 已知 MCP 配置文件里声明的 server（mcp-kind agent）
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("vermes.agent_discovery")

# 已知 agent CLI：命令名 → (展示名, 版本探测参数)
_KNOWN_CLI_AGENTS: Dict[str, Tuple[str, List[str]]] = {
    "claude": ("Claude Code", ["--version"]),
    "claude-code": ("Claude Code", ["--version"]),
    "codex": ("OpenAI Codex", ["--version"]),
    "openclaw": ("OpenClaw", ["--version"]),
    "qclaw": ("QClaw", ["--version"]),
    "gemini": ("Gemini CLI", ["--version"]),
    "aider": ("Aider", ["--version"]),
    "goose": ("Goose", ["--version"]),
    "cursor": ("Cursor", ["--version"]),
    "copilot": ("GitHub Copilot CLI", ["--version"]),
}

# 已知 agent 配置目录（home 相对）：存在即视为已装（home-dir based agent）
_KNOWN_AGENT_CONFIG_DIRS: Dict[str, Tuple[str, str]] = {
    ".claude": ("Claude", "app"),
    ".config/codex": ("Codex", "cli"),
    ".config/openclaw": ("OpenClaw", "app"),
    ".qclaw": ("QClaw", "app"),
    ".config/aider": ("Aider", "cli"),
    ".gemini": ("Gemini CLI", "cli"),
    ".goose": ("Goose", "cli"),
}

# macOS app bundle 候选（/Applications 下）
_KNOWN_APP_BUNDLES: List[str] = ["Claude.app", "Cursor.app", "Windsurf.app", "OpenClaw.app"]


@dataclass
class AgentDiscovery:
    """本机发现的一条 agent 记录（归一化）。"""

    id: str
    name: str
    kind: str                      # cli | app | mcp | config
    auth_scheme: str              # none | local | apikey | oauth
    version: str = ""
    description: str = ""
    source: str = "local"         # local | remote
    entry_point: str = ""
    homepage: str = ""
    popularity: int = 0
    extra: Dict[str, object] = field(default_factory=dict)


class LocalAgentScanner:
    """扫本机四类信号源，归一为 ``AgentDiscovery`` 列表（fail-open）。"""

    def __init__(self, home: Optional[Path] = None):
        self.home = Path(home) if home else Path(os.path.expanduser("~"))

    def scan(self) -> List[AgentDiscovery]:
        found: Dict[str, AgentDiscovery] = {}
        self._scan_cli(found)
        self._scan_config_dirs(found)
        self._scan_app_bundles(found)
        self._scan_mcp_configs(found)
        return list(found.values())

    # ---- CLI 二进制 ----------------------------------------------------
    def _scan_cli(self, found: Dict[str, AgentDiscovery]) -> None:
        for bin_name, (disp, ver_args) in _KNOWN_CLI_AGENTS.items():
            try:
                path = shutil.which(bin_name)
            except Exception:  # noqa: BLE001 - which 探测失败 → 跳过该命令，不阻断其他源
                continue
            if not path:
                continue
            key = f"agent:{bin_name}"
            if key in found:
                continue
            found[key] = AgentDiscovery(
                id=key,
                name=disp,
                kind="cli",
                auth_scheme="local",
                version=self._probe_version(bin_name, ver_args),
                entry_point=bin_name,
            )

    @staticmethod
    def _probe_version(bin_name: str, ver_args: List[str]) -> str:
        try:
            proc = subprocess.run(
                [bin_name, *ver_args],
                capture_output=True, text=True, timeout=10, check=False,
            )
            out = (proc.stdout or proc.stderr).strip().splitlines()
            return out[0][:60] if out else ""
        except Exception:  # noqa: BLE001 - 版本探测失败不致命
            return ""

    # ---- 配置目录 ------------------------------------------------------
    def _scan_config_dirs(self, found: Dict[str, AgentDiscovery]) -> None:
        for rel, (disp, kind) in _KNOWN_AGENT_CONFIG_DIRS.items():
            p = self.home / rel
            if not p.exists():
                continue
            key = f"agent:cfg_{rel.replace('/', '_')}"
            if key in found:
                continue
            found[key] = AgentDiscovery(
                id=key,
                name=disp,
                kind=kind,
                auth_scheme="local",
                entry_point=str(p),
            )

    # ---- macOS app bundle ---------------------------------------------
    def _scan_app_bundles(self, found: Dict[str, AgentDiscovery]) -> None:
        apps = Path("/Applications")
        if not apps.is_dir():
            return
        for name in _KNOWN_APP_BUNDLES:
            bundle = apps / name
            if not bundle.exists():
                continue
            key = f"agent:app_{name.lower().replace('.app', '')}"
            if key in found:
                continue
            found[key] = AgentDiscovery(
                id=key,
                name=name.replace(".app", ""),
                kind="app",
                auth_scheme="local",
                entry_point=str(bundle),
            )

    # ---- MCP 配置 ------------------------------------------------------
    def _scan_mcp_configs(self, found: Dict[str, AgentDiscovery]) -> None:
        # 相对 self.home 解析（与生产默认 expanduser('~') 等价；测试中可指向 fake_home）
        candidates = [
            self.home / ".vermes" / "mcp.json",
            self.home / ".cursor" / "mcp.json",
            self.home / ".claude" / "mcp.json",
        ]
        for p in candidates:
            if not p.exists():
                continue
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                logger.debug("mcp config 解析跳过 %s: %s", p, exc)
                continue
            servers = data.get("mcpServers") or data.get("servers") or {}
            if not isinstance(servers, dict):
                continue
            for sname, scfg in servers.items():
                key = f"agent:mcp_{sname}"
                if key in found:
                    continue
                if not isinstance(scfg, dict):
                    continue
                cmd = scfg.get("command") or ""
                auth = "apikey" if scfg.get("env") else "none"
                found[key] = AgentDiscovery(
                    id=key,
                    name=f"MCP: {sname}",
                    kind="mcp",
                    auth_scheme=auth,
                    entry_point=cmd,
                    extra={"mcp_config": str(p)},
                )
