"""⑭ 请神收尾 · 本机登录态探测（登堂降门槛）。

外部 ACP agent（claude/codex/gemini/copilot 等）登堂时，优先复用本机 CLI
的登录态（OAuth / 凭据文件 / macOS Keychain），**免配置直接登堂**；未登录
才退回「弹窗填 API Key」。这是「本机发现 → 一键登堂」降门槛的核心。

探测策略（轻量文件/凭据探测优先，CLI 命令探测兜底）：
  - claude  : ~/.claude/.credentials.json 存在 / macOS Keychain / `claude auth status`
  - codex   : ~/.codex/auth.json 存在 / `codex login status`
  - gemini  : ~/.gemini/oauth_creds.json 存在
  - copilot : GitHub CLI 登录态（~/.config/gh/hosts.yml / `gh auth status`）

设计纪律：
  - 探测**只读、无副作用**，任何失败 fail-open 返回 unknown（不阻断登堂）。
  - 探测结果归一为 ``LoginProbe``（logged_in: bool | None，None=无法判定）。
  - 不在此模块做鉴权决策——只提供事实，决策交给路由层。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger("vermes.login_probe")


@dataclass
class LoginProbe:
    """一条 agent 的登录态探测结果。"""

    logged_in: Optional[bool]  # True=已登录 / False=未登录 / None=无法判定
    method: str                # 探测方式描述（credential file / cli status / keychain）
    detail: str = ""           # 附加说明（凭据路径 / 命令输出摘要）

    @property
    def is_logged_in(self) -> bool:
        return self.logged_in is True


def _home() -> Path:
    return Path.home()


def _resolve_bin(bin_name: str) -> Optional[str]:
    """找 CLI 二进制：PATH 优先，落空遍历额外目录兜底（与 agent_discovery
    _extra_cli_dirs 同源）——WorkBuddy 内置 codebuddy / QClaw openclaw 不在 PATH。"""
    p = shutil.which(bin_name)
    if p:
        return p
    home = Path.home()
    qclaw_root = home / "Library" / "Application Support" / "QClaw"
    for d in (
        Path("/opt/homebrew/bin"),
        Path("/usr/local/bin"),
        qclaw_root / "npm-global" / "bin",
        home / ".npm-global" / "bin",
        home / ".local" / "bin",
        home / ".cargo" / "bin",
        qclaw_root / "openclaw" / "config" / "bin",
        home / ".config" / "openclaw" / "config" / "bin",
        Path("/Applications/WorkBuddy.app/Contents/Resources/app.asar.unpacked/cli/bin"),
    ):
        cand = d / bin_name
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand)
    return None


def _file_exists(path: str) -> bool:
    return (_home() / path).exists()


def _keychain_has_claude_credential() -> bool:
    """macOS Keychain 里是否存在 Claude Code 的登录凭据。"""
    if shutil.which("security") is None:
        return False
    for service in ("Claude Code-credentials", "claude-code", "com.anthropic.claude"):
        try:
            proc = subprocess.run(
                ["security", "find-generic-password", "-s", service],
                capture_output=True, text=True, timeout=5, check=False,
            )
            if proc.returncode == 0:
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def _cli_status(bin_name: str, args: list[str], not_logged_markers: tuple[str, ...]) -> Optional[bool]:
    """跑 `bin args` 判定登录态；输出含 not_logged_markers 任一 → False。

    返回 None 表示命令不可用/输出无法判定。
    """
    if shutil.which(bin_name) is None:
        return None
    try:
        proc = subprocess.run(
            [bin_name, *args], capture_output=True, text=True, timeout=15, check=False,
        )
    except Exception:  # noqa: BLE001
        return None
    out = (proc.stdout or "") + (proc.stderr or "")
    if not out.strip():
        return None
    for marker in not_logged_markers:
        if marker in out:
            return False
    # claude auth status 输出 JSON 含 loggedIn 字段
    if bin_name == "claude":
        try:
            data = json.loads(out)
            if isinstance(data, dict) and "loggedIn" in data:
                return bool(data["loggedIn"])
        except Exception:  # noqa: BLE001
            pass
    return True


# provider / recipe name 关键词 → 探测函数
def _probe_claude() -> LoginProbe:
    if _file_exists(".claude/.credentials.json"):
        return LoginProbe(True, "credential file", "~/.claude/.credentials.json")
    if _keychain_has_claude_credential():
        return LoginProbe(True, "keychain", "macOS Keychain Claude Code-credentials")
    st = _cli_status("claude", ["auth", "status"], ("loggedIn\": false", "not logged in", "Not logged in"))
    if st is False:
        return LoginProbe(False, "cli status", "claude auth status 报告未登录")
    if st is True:
        return LoginProbe(True, "cli status", "claude auth status 报告已登录")
    return LoginProbe(None, "unknown", "无法判定 Claude Code 登录态")


def _probe_codex() -> LoginProbe:
    if _file_exists(".codex/auth.json"):
        return LoginProbe(True, "credential file", "~/.codex/auth.json")
    st = _cli_status("codex", ["login", "status"], ("Not logged in", "not logged in", "未登录"))
    if st is False:
        return LoginProbe(False, "cli status", "codex login status 报告未登录")
    if st is True:
        return LoginProbe(True, "cli status", "codex login status 报告已登录")
    return LoginProbe(None, "unknown", "无法判定 Codex 登录态")


def _probe_gemini() -> LoginProbe:
    if _file_exists(".gemini/oauth_creds.json"):
        return LoginProbe(True, "credential file", "~/.gemini/oauth_creds.json")
    return LoginProbe(None, "unknown", "无法判定 Gemini 登录态")


def _probe_copilot() -> LoginProbe:
    if _file_exists(".config/gh/hosts.yml"):
        return LoginProbe(True, "credential file", "~/.config/gh/hosts.yml")
    if shutil.which("gh") is not None:
        try:
            proc = subprocess.run(
                ["gh", "auth", "status"], capture_output=True, text=True, timeout=10, check=False,
            )
            out = (proc.stdout or "") + (proc.stderr or "")
            if proc.returncode == 0:
                return LoginProbe(True, "cli status", "gh auth status 已登录")
            if "not logged" in out.lower() or "未登录" in out:
                return LoginProbe(False, "cli status", "gh auth status 未登录")
        except Exception:  # noqa: BLE001
            pass
    return LoginProbe(None, "unknown", "无法判定 GitHub Copilot 登录态")


def _probe_hermes() -> LoginProbe:
    """Hermes 鉴权 = ~/.hermes/.env 的模型/凭据配置（custom runtime credentials）。

    已初始化（.env 存在且 hermes status 报告了 model）→ 免配置登堂；
    否则引导 `hermes acp --setup`。
    """
    if _file_exists(".hermes/.env"):
        return LoginProbe(True, "credential file", "~/.hermes/.env（已配置模型/凭据）")
    if shutil.which("hermes") is not None:
        try:
            proc = subprocess.run(
                ["hermes", "status"], capture_output=True, text=True, timeout=15, check=False,
            )
            out = (proc.stdout or "") + (proc.stderr or "")
            low = out.lower()
            if "model:" in low and (".env file" in low or "✓" in out):
                return LoginProbe(True, "cli status", "hermes status 报告已配置模型")
            if "not set" in low and ".env" in low:
                return LoginProbe(False, "cli status", "hermes 未初始化模型/凭据")
        except Exception:  # noqa: BLE001
            pass
    return LoginProbe(None, "unknown", "无法判定 Hermes 初始化态")


def _probe_openclaw() -> LoginProbe:
    """OpenClaw 是 gateway 型 ACP 桥（openclaw acp），本机 Gateway 免 token。

    有 `openclaw acp` 命令且 Gateway 在跑 → 免配置登堂；
    否则引导用户先 `openclaw gateway status`/启动 Gateway。
    """
    if shutil.which("openclaw") is None:
        return LoginProbe(None, "unknown", "未检测到 openclaw CLI")
    try:
        # acp 子命令可识别 = 具备 ACP 桥能力
        proc = subprocess.run(
            ["openclaw", "acp", "--help"], capture_output=True, text=True, timeout=15, check=False,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        if "ACP" not in out and "acp" not in out.lower():
            return LoginProbe(False, "cli status", "openclaw 无 acp 子命令（非开源原版/旧版 wrapper）")
    except Exception:  # noqa: BLE001
        return LoginProbe(None, "unknown", "无法判定 OpenClaw ACP 能力")
    # 本机 Gateway 在跑（进程/端口）→ 免 token；否则需启动 Gateway
    try:
        proc = subprocess.run(
            ["openclaw", "gateway", "status"], capture_output=True, text=True, timeout=15, check=False,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        if "running" in out.lower() or "ready" in out.lower() or "connected" in out.lower():
            return LoginProbe(True, "gateway status", "openclaw Gateway 运行中，本机免 token")
    except Exception:  # noqa: BLE001
        pass
    # Gateway 状态未知，但 acp 桥存在 → 视为可登堂（远程可能需 token，交由登堂时再探测）
    return LoginProbe(True, "cli status", "openclaw acp 桥可用（Gateway 免 token 或需 --token）")


def _probe_codebuddy() -> LoginProbe:
    """CodeBuddy Code（WorkBuddy 底层）— 有 `--acp` 能力即可登堂，本机已登录免配置。

    authMethods 返回 iOA/微信/Google/企业域，但本机 print 直连成功 = 已登录态可用。
    故：有 --acp 能力 → 视为可登堂（未登录时 ACP 握手会引导，前端提示先登录）。
    """
    cb = _resolve_bin("codebuddy")
    if cb is None:
        return LoginProbe(None, "unknown", "未检测到 codebuddy CLI")
    try:
        proc = subprocess.run(
            [cb, "--version"], capture_output=True, text=True, timeout=15, check=False,
        )
        out = (proc.stdout or "").strip()
        if not out:
            return LoginProbe(None, "unknown", "无法判定 CodeBuddy 版本")
    except Exception:  # noqa: BLE001
        return LoginProbe(None, "unknown", "无法判定 CodeBuddy 能力")
    return LoginProbe(True, "cli status", f"CodeBuddy Code {out}（ACP 模式可用，已登录免配置）")


_PROBE_FUNCS = {
    "claude": _probe_claude,
    "codex": _probe_codex,
    "gemini": _probe_gemini,
    "copilot": _probe_copilot,
    "hermes": _probe_hermes,
    "openclaw": _probe_openclaw,
    "codebuddy": _probe_codebuddy,
}

# 家族 → 登录引导命令（前端「未登录」弹窗展示 + 一键打开终端预置）
LOGIN_COMMANDS = {
    "claude": "claude",
    "codex": "codex login",
    "gemini": "gemini login",
    "copilot": "gh auth login",
    "hermes": "hermes acp --setup",
    "openclaw": "openclaw gateway status",
    "codebuddy": "codebuddy",
}


def _match_family(recipe_name: str, provider: str) -> Optional[str]:
    """recipe name/provider → 探测家族（claude/codex/gemini/copilot）。"""
    blob = f"{recipe_name} {provider}".lower()
    for family in ("claude", "codex", "gemini", "copilot", "hermes", "openclaw", "codebuddy"):
        if family in blob:
            return family
    return None


def probe_login(recipe_name: str, provider: str = "") -> LoginProbe:
    """探测某 ACP agent 本机 CLI 登录态（登堂前调用）。

    无法识别家族 / 探测失败 → 返回 LoginProbe(None, ...)，由路由层按
    「未知」处理（不阻断登堂，退回现有 need_auth 逻辑）。
    """
    family = _match_family(recipe_name, provider)
    if family is None:
        return LoginProbe(None, "unknown", f"无登录态探测策略：{recipe_name}")
    try:
        return _PROBE_FUNCS[family]()
    except Exception as exc:  # noqa: BLE001 - 探测失败 fail-open
        logger.debug("login probe failed for %s: %s", family, exc)
        return LoginProbe(None, "unknown", f"探测失败：{exc}")


def login_command_for(recipe_name: str, provider: str = "") -> str:
    """返回该 agent 的终端登录引导命令（未登录时前端展示 + 一键打开终端）。"""
    family = _match_family(recipe_name, provider)
    return LOGIN_COMMANDS.get(family or "", "")
