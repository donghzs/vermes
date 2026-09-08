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

# CLI 二进制额外探测目录（不依赖 PATH）。GUI 双击启动的 app 后端进程继承
# launchd 精简 PATH（/usr/bin:/bin:...），导致 npm-global/homebrew/QClaw 等
# 目录里的 agent CLI 用 shutil.which 找不到 → 本机发现不全。这里列出 macOS
# 常见 CLI 落脚点，`_scan_cli` 会先 which、再逐个目录直查，双保险。
def _extra_cli_dirs(home: Path) -> List[Path]:
    """返回 CLI 额外探测目录（存在即纳入），供 PATH 缺失时兜底。"""
    qclaw_root = home / "Library" / "Application Support" / "QClaw"
    candidates = [
        Path("/opt/homebrew/bin"),            # Apple Silicon Homebrew
        Path("/usr/local/bin"),               # Intel Homebrew / 通用
        qclaw_root / "npm-global" / "bin",     # QClaw npm 全局（claude/codex）
        home / ".npm-global" / "bin",
        home / ".local" / "bin",
        home / ".cargo" / "bin",
        qclaw_root / "openclaw" / "config" / "bin",  # QClaw/openclaw CLI
        home / ".config" / "openclaw" / "config" / "bin",
        home / "Library" / "Python" / "3.9" / "bin",
        # WorkBuddy 内置 CodeBuddy CLI（app.asar.unpacked/cli/bin）
        Path("/Applications/WorkBuddy.app/Contents/Resources/app.asar.unpacked/cli/bin"),
    ]
    out: List[Path] = []
    for d in candidates:
        if d.is_dir() and d not in out:
            out.append(d)
    return out

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
    # 同生态 agent（Hermes 上游 / Vermes 自身），有 print 直连能力
    "hermes": ("Hermes Agent", ["--version"]),
    "vermes": ("Vermes", ["--help"]),
    # WorkBuddy 内置的腾讯 CodeBuddy Code（app.asar.unpacked/cli/bin/codebuddy）
    "codebuddy": ("CodeBuddy Code", ["--version"]),
}

# 已知 agent 配置目录（home 相对）：存在即视为已装（home-dir based agent）
_KNOWN_AGENT_CONFIG_DIRS: Dict[str, Tuple[str, str]] = {
    ".claude": ("Claude", "config"),
    ".config/codex": ("Codex", "config"),
    ".config/openclaw": ("OpenClaw", "config"),
    ".qclaw": ("QClaw", "config"),
    ".config/aider": ("Aider", "config"),
    ".gemini": ("Gemini CLI", "config"),
    ".goose": ("Goose", "config"),
    ".codebuddy": ("CodeBuddy Code", "config"),
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
        extra_dirs = _extra_cli_dirs(self.home)
        for bin_name, (disp, ver_args) in _KNOWN_CLI_AGENTS.items():
            try:
                path = shutil.which(bin_name)
            except Exception:  # noqa: BLE001 - which 探测失败 → 跳过该命令，不阻断其他源
                path = None
            # PATH 精简（GUI 启动）时 which 可能落空 → 遍历额外目录直查兜底
            if not path:
                for d in extra_dirs:
                    cand = d / bin_name
                    if cand.exists() and os.access(cand, os.X_OK):
                        path = str(cand)
                        break
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
                version=self._probe_version(path, ver_args),
                entry_point=path,
            )

    @staticmethod
    def _probe_version(bin_name: str, ver_args: List[str]) -> str:
        try:
            # 版本探测同样要经过 wrapper 运行时补齐：否则 wrapper 型 CLI
            # 会以「缺 env → exit 1」被误判为「已安装但版本未知」。
            from vermes_cli.adapters.cli_env import resolve_cli_env

            env = resolve_cli_env(bin_name, extra_dirs=_extra_cli_dirs(Path.home()))
            proc = subprocess.run(
                [bin_name, *ver_args],
                capture_output=True, text=True, timeout=10, check=False,
                env=env,
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
