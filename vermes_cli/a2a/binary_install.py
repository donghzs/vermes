"""S5 — ACP binary recipe 下载解包。

registry 里 ``distribution: binary`` 的 agent（cursor/kimi/junie 等）没有
可直接 spawn 的全局命令：recipe 的 ``entry_point`` 是相对路径（``./kimi``），
必须先把 release 归档下载解包到本地目录，再以该目录为 cwd spawn。

数据源：
  1. recipe 自带 ``binaries`` 字段（未来 generate_from_registry 可 dump）
  2. ACP Registry CDN（``…/registry/v1/latest/registry.json``）按 id 取
     platform → archive/cmd/sha256

安装目录：``~/.vermes/acp-bin/<recipe-name>/``（entry_point 相对其解析）。
sha256 不匹配 → 拒绝落盘可执行权限，返回 error（不假成功）。
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import stat
import tarfile
import zipfile
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

REGISTRY_URL = "https://cdn.agentclientprotocol.com/registry/v1/latest/registry.json"

_PLATFORM_PRIORITY = (
    "darwin-aarch64",
    "darwin-x86_64",
    "linux-x86_64",
    "linux-aarch64",
    "windows-x86_64",
    "windows-aarch64",
)


def acp_bin_root() -> Path:
    home = Path(os.environ.get("VERMES_HOME") or os.path.expanduser("~/.vermes"))
    return home / "acp-bin"


def _current_platform_key() -> str:
    import platform
    import sys
    machine = (platform.machine() or "").lower()
    if machine in ("arm64", "aarch64"):
        arch = "aarch64"
    else:
        arch = "x86_64"
    if sys.platform == "darwin":
        return f"darwin-{arch}"
    if sys.platform.startswith("win"):
        return f"windows-{arch}"
    return f"linux-{arch}"


def _load_recipe_raw(name: str) -> Optional[dict[str, Any]]:
    try:
        from vermes_cli.a2a.recipes.loader import find_recipe
        from vermes_cli.a2a.recipes.loader import RECIPES_DIR, iter_recipe_files, load_recipe
        recipe = find_recipe(name, RECIPES_DIR, recursive=True)
        if recipe is None:
            return None
        src = getattr(recipe, "source", "") or ""
        if src and Path(src).exists():
            import yaml
            data = yaml.safe_load(Path(src).read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        # RecipeConfig 可能不带 source：按 name 扫 yaml 读 raw
        for path in iter_recipe_files(RECIPES_DIR, recursive=True):
            try:
                import yaml
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(data, dict) and (data.get("name") or "").strip() == name:
                return data
        # 最后兜底：从 dataclass 字段构造最小 raw
        try:
            from dataclasses import asdict
            return asdict(recipe)
        except Exception:
            return None
    except Exception as exc:
        logger.debug("binary recipe load failed %s: %s", name, exc)
        return None


def _fetch_registry_platforms(agent_id: str) -> Optional[dict[str, Any]]:
    """从 ACP Registry CDN 取该 agent 的 platforms 节点。"""
    try:
        import json
        import urllib.request
        with urllib.request.urlopen(REGISTRY_URL, timeout=30) as resp:  # noqa: S310
            data = json.loads(resp.read().decode("utf-8"))
        agents = data.get("agents") or data.get("data") or []
        if isinstance(agents, dict):
            agents = list(agents.values())
        for a in agents:
            if not isinstance(a, dict):
                continue
            aid = (a.get("id") or a.get("name") or "").strip()
            if aid == agent_id or aid.replace("_", "-") == agent_id:
                return a.get("platforms") or a.get("binary") or None
    except Exception as exc:
        logger.warning("registry fetch failed for %s: %s", agent_id, exc)
    return None


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 256), b""):
            h.update(chunk)
    return h.hexdigest()


def _extract_archive(archive: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    name = archive.name.lower()
    if name.endswith(".zip"):
        with zipfile.ZipFile(archive, "r") as zf:
            zf.extractall(dest)
    elif name.endswith((".tar.gz", ".tgz", ".tar")):
        mode = "r:gz" if name.endswith((".tar.gz", ".tgz")) else "r"
        with tarfile.open(archive, mode) as tf:
            tf.extractall(dest)
    else:
        # 裸二进制：直接拷贝
        shutil.copy2(archive, dest / archive.name)


def _chmod_exec(path: Path) -> None:
    try:
        mode = path.stat().st_mode
        path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except Exception:
        pass


def _resolve_entry_path(dest: Path, entry_point: str) -> Optional[Path]:
    if not entry_point:
        return None
    ep = entry_point[2:] if entry_point.startswith("./") else entry_point
    candidate = dest / ep
    if candidate.exists():
        return candidate
    # 解包后常见：嵌套一层目录
    for p in dest.rglob(Path(ep).name):
        if p.is_file():
            return p
    return None


def is_binary_recipe(raw: dict[str, Any]) -> bool:
    desc = (raw.get("description") or "")
    ep = (raw.get("entry_point") or "")
    return ep.startswith("./") or "binary" in desc.lower() or bool(raw.get("binaries"))


def list_binary_recipe_names() -> list[str]:
    """扫描 registry 目录中 distribution=binary 的 recipe 名。"""
    from vermes_cli.a2a.recipes.loader import RECIPES_DIR, find_recipe
    names: list[str] = []
    reg = Path(RECIPES_DIR) / "registry"
    if not reg.is_dir():
        return names
    for yml in sorted(reg.glob("*.yaml")):
        try:
            import yaml
            raw = yaml.safe_load(yml.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        if is_binary_recipe(raw) and (raw.get("name") or "").strip():
            names.append(raw["name"].strip())
    return names


def ensure_binary_recipe_installed(
    name: str,
    *,
    platform_key: str = "",
    download_url: str = "",
    sha256: str = "",
    force: bool = False,
) -> dict[str, Any]:
    """确保 binary recipe 的本地可执行文件已就绪。

    Returns:
        ok=True + path/entry_point；失败 ok=False + error（不假装已安装）
    """
    raw = _load_recipe_raw(name)
    if raw is None:
        return {"ok": False, "error": f"recipe not found: {name!r}"}
    if not is_binary_recipe(raw) and not download_url:
        return {
            "ok": False,
            "error": f"recipe {name!r} is not binary distribution; use npx/uvx path",
        }

    dest = acp_bin_root() / name
    entry_point = (raw.get("entry_point") or "").strip()
    existing = _resolve_entry_path(dest, entry_point) if dest.exists() else None
    if existing and not force:
        _chmod_exec(existing)
        return {
            "ok": True,
            "already_installed": True,
            "path": str(existing),
            "entry_point": entry_point,
            "dest": str(dest),
        }

    # 解析下载源
    url = download_url
    plat = platform_key or _current_platform_key()
    expect_sha = sha256
    if not url:
        binaries = raw.get("binaries") or {}
        node = binaries.get(plat) or {}
        url = (node.get("url") or node.get("archive") or "").strip()
        expect_sha = expect_sha or (node.get("sha256") or "")
        if not url:
            platforms = _fetch_registry_platforms(raw.get("name") or name) or {}
            node = platforms.get(plat) or {}
            if not node and isinstance(platforms, dict):
                # 键可能是列表形式
                for k in _PLATFORM_PRIORITY:
                    if k in platforms:
                        plat = k
                        node = platforms[k]
                        break
            url = (node.get("url") or node.get("archive") or "").strip()
            expect_sha = expect_sha or (node.get("sha256") or "")
    if not url:
        return {
            "ok": False,
            "error": (
                f"no download url for binary recipe {name!r} (platform={plat}); "
                f"check install_hint={raw.get('install_hint')!r} or add binaries.{plat}.url"
            ),
            "platform": plat,
        }

    dest.mkdir(parents=True, exist_ok=True)
    archive_path = dest / ("_download" + Path(url).name[-20:])
    try:
        import urllib.request
        logger.info("downloading binary recipe %s from %s", name, url)
        urllib.request.urlretrieve(url, archive_path)  # noqa: S310
    except Exception as exc:
        return {"ok": False, "error": f"download failed: {exc}", "url": url}

    if expect_sha:
        actual = _sha256_file(archive_path)
        if actual.lower() != str(expect_sha).lower():
            try:
                archive_path.unlink()
            except Exception:
                pass
            return {
                "ok": False,
                "error": f"sha256 mismatch for {name}: expected {expect_sha}, got {actual}",
            }

    try:
        _extract_archive(archive_path, dest)
    except Exception as exc:
        return {"ok": False, "error": f"unpack failed: {exc}", "url": url}

    try:
        archive_path.unlink()
    except Exception:
        pass

    resolved = _resolve_entry_path(dest, entry_point)
    if resolved is None:
        return {
            "ok": False,
            "error": (
                f"unpacked archive but entry_point {entry_point!r} not found under {dest}; "
                "adjust recipe.entry_point to the real binary path"
            ),
            "dest": str(dest),
        }
    _chmod_exec(resolved)
    return {
        "ok": True,
        "already_installed": False,
        "path": str(resolved),
        "entry_point": entry_point,
        "dest": str(dest),
        "url": url,
        "platform": plat,
    }
