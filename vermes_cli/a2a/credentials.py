"""Credential store for the Bot 神魔堂 (请神收尾 · 授权 Key 持久化).

授权 Key 持久化到用户目录 ``~/.vermes/agent_auth.json``（0600，非 git 跟踪），
而**不**写进 recipe YAML——后者落在 git 跟踪的包目录
（``vermes_cli/a2a/recipes/``），明文密钥有被误提交的风险。

recipe 通过 ``auth.fallback_settings``（缺省回退到 ``recipe.name``）作为引用键，
在 spawn 时从本凭据库回注到 ``auth.env_var`` 指定的环境变量。

设计取舍（董董拍板）：优先「傻瓜式请神」——用户填一次 key，重启后仍可登堂。
安全收紧（凭据库权限、统一写端点鉴权）归 ⑨ 隐私硬化。
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any

try:
    from vermes_constants import get_vermes_home
except Exception:  # pragma: no cover - 隔离测试环境兜底
    def get_vermes_home() -> Path:  # type: ignore[misc]
        return Path(os.environ.get("VERMES_HOME", os.path.expanduser("~/.vermes")))


CRED_STORE_PATH = get_vermes_home() / "agent_auth.json"


def load_credentials() -> dict[str, str]:
    """Read the credential store; return {} if missing or unreadable."""
    if not CRED_STORE_PATH.exists():
        return {}
    try:
        with open(CRED_STORE_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return {}
    if isinstance(data, dict):
        return {str(k): str(v) for k, v in data.items()}
    return {}


def save_credential(key: str, value: str) -> None:
    """Persist a single credential, atomically, with 0600 perms."""
    if not key or value is None:
        return
    creds = load_credentials()
    creds[str(key)] = str(value)
    CRED_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CRED_STORE_PATH.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(creds, fh, indent=2, sort_keys=True)
    os.chmod(tmp, 0o600)
    os.replace(tmp, CRED_STORE_PATH)
    # 覆盖既有文件后再次确保权限（os.replace 继承 tmp 的 0600，双保险）
    try:
        os.chmod(CRED_STORE_PATH, 0o600)
    except OSError:
        pass


def get_credential(key: str) -> str | None:
    if not key:
        return None
    return load_credentials().get(str(key))


def delete_credential(key: str) -> bool:
    """Remove a credential. Returns True if it existed and was removed."""
    if not key:
        return False
    creds = load_credentials()
    if str(key) not in creds:
        return False
    del creds[str(key)]
    CRED_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CRED_STORE_PATH.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(creds, fh, indent=2, sort_keys=True)
    os.chmod(tmp, 0o600)
    os.replace(tmp, CRED_STORE_PATH)
    try:
        os.chmod(CRED_STORE_PATH, 0o600)
    except OSError:
        pass
    return True


def get_credential_path() -> Path:
    """暴露给测试：当前凭据库路径。生产代码不要直接读这条路径做旁路。"""
    return CRED_STORE_PATH
