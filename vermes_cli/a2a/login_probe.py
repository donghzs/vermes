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


_PROBE_FUNCS = {
    "claude": _probe_claude,
    "codex": _probe_codex,
    "gemini": _probe_gemini,
    "copilot": _probe_copilot,
}


def _match_family(recipe_name: str, provider: str) -> Optional[str]:
    """recipe name/provider → 探测家族（claude/codex/gemini/copilot）。"""
    blob = f"{recipe_name} {provider}".lower()
    for family in ("claude", "codex", "gemini", "copilot"):
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
