"""
Vermes 模块目录子系统（远程 catalog + Release 分发）

这是「可插拔 / 按需下载」架构的 P1 数据层：负责
  1. 读取远程/本地 catalog.json（模块清单：版本、代码包 URL、sha256、大小、提供的工具、关键词）
  2. 构建「工具名 → 模块名」「关键词 → 模块」索引（Agent 自动检测缺失用）
  3. 下载代码包 → sha256 供应链校验 → 安全解压到 ~/.vermes/modules/<name>/
  4. 提供 ensure_module_ready()（与 engine_setup.ensure_mac_ready 同构），安装后热重载工具

设计原则（fail-open）：
  - catalog 缺失/损坏 → 返回空目录，宿主照常以内置模块运行，绝不崩溃、绝不阻断启动。
  - 本文件只依赖标准库（json / hashlib / tarfile / urllib）+ 已有的 reload_module_tools（懒加载）。
  - 不触碰 _BUILTIN_MODULES，不动 tool_executor 拦截（那是 P3 的工作）。

依赖关系（分期）：
  P1（本文件）= catalog 格式 + 下载/校验/解压原语 + ensure_module_ready 函数。
  P2 = module_cli install --release 接线；P3 = tool_executor 拦截；P4 = 前端商店；P5 = 内置降级；P6 = 重资产。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import tarfile
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------

@dataclass
class AssetSpec:
    """重资产（引擎/权重/标准件库），与代码包分离，按引擎分桶懒下载。"""
    id: str
    label: str = ""
    url: str = ""                 # Release 资产完整 URL
    release_asset: str = ""       # Release 内文件名（url 缺省时回退拼装）
    sha256: str = ""
    size: int = 0
    target: str = ""              # 落点（~/.vermes/engines/mac 或模块内 assets/...）
    optional: bool = False


@dataclass
class CatalogModule:
    """catalog.json 里的一个模块条目（已解析）。"""
    name: str
    display_name: str
    latest: str
    vermes_min: str = "0.0.0"
    code_asset: str = ""          # 代码包（小，~2MB）完整 URL
    code_sha256: str = ""
    size_code: int = 0
    assets: List[AssetSpec] = field(default_factory=list)
    provides_tools: List[str] = field(default_factory=list)
    keywords: List[str] = field(default_factory=list)
    repository: str = ""
    homepage: str = ""
    description: str = ""
    recommended: bool = False


# ---------------------------------------------------------------------------
# catalog 加载（fail-open）
# ---------------------------------------------------------------------------

def default_catalog_url() -> str:
    """官方 catalog 仓的 raw URL（可由 CI 在每次模块发版时重新生成）。"""
    return "https://raw.githubusercontent.com/donghzs/vermes-modules-catalog/main/catalog.json"


def default_catalog_path() -> Path:
    """本地缓存的 catalog 路径（~/.vermes/modules/catalog.json）。"""
    return _modules_dir() / "catalog.json"


def load_catalog(path_or_url: Optional[str] = None) -> Dict[str, Any]:
    """读取 catalog.json（本地文件或远程 URL）。

    fail-open：任何错误都返回 {"modules": [], "generated_at": None}，不抛异常。
    """
    if path_or_url is None:
        # 优先本地缓存，其次官方 URL；都失败则返回空。
        local = default_catalog_path()
        if local.exists():
            try:
                return _parse_catalog_file(local)
            except Exception as e:  # noqa: BLE001
                logger.warning("load_catalog: local catalog parse failed: %s", e)
        path_or_url = default_catalog_url()

    try:
        if _is_url(path_or_url):
            data = _fetch_json(path_or_url)
        else:
            data = _parse_catalog_file(Path(path_or_url))
    except Exception as e:  # noqa: BLE001
        logger.warning("load_catalog: failed to load %s: %s", path_or_url, e)
        return {"modules": [], "generated_at": None}

    modules = data.get("modules") if isinstance(data, dict) else None
    if not isinstance(modules, list):
        logger.warning("load_catalog: malformed catalog (no 'modules' list)")
        return {"modules": [], "generated_at": None}
    return data


def _is_url(s: str) -> bool:
    return s.startswith("http://") or s.startswith("https://") or s.startswith("file://")


def _parse_catalog_file(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _fetch_json(url: str) -> Dict[str, Any]:
    """下载远程 catalog 并（可选地）缓存到本地。"""
    req = urllib.request.Request(url, headers={"User-Agent": "vermes-module-catalog/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
        raw = resp.read().decode("utf-8")
    data = json.loads(raw)
    # 缓存到本地，下次离线/网络失败可用
    try:
        cache = default_catalog_path()
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(raw, encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        logger.debug("load_catalog: cache write skipped: %s", e)
    return data


# ---------------------------------------------------------------------------
# 解析 + 索引
# ---------------------------------------------------------------------------

def catalog_modules(catalog: Dict[str, Any]) -> List[CatalogModule]:
    """把原始 catalog dict 解析成 CatalogModule 列表。"""
    out: List[CatalogModule] = []
    for entry in catalog.get("modules", []) or []:
        if not isinstance(entry, dict) or not entry.get("name"):
            continue
        assets = [
            AssetSpec(
                id=a.get("id", ""),
                label=a.get("label", ""),
                url=a.get("url", ""),
                release_asset=a.get("release_asset", ""),
                sha256=a.get("sha256", ""),
                size=int(a.get("size", 0) or 0),
                target=a.get("target", ""),
                optional=bool(a.get("optional", False)),
            )
            for a in (entry.get("assets", []) or [])
            if isinstance(a, dict)
        ]
        out.append(CatalogModule(
            name=entry["name"],
            display_name=entry.get("display_name", entry["name"]),
            latest=str(entry.get("latest", "")),
            vermes_min=entry.get("vermes_min", "0.0.0"),
            code_asset=entry.get("code_asset", ""),
            code_sha256=entry.get("code_sha256", ""),
            size_code=int(entry.get("size_code", 0) or 0),
            assets=assets,
            provides_tools=list(entry.get("provides_tools", []) or []),
            keywords=list(entry.get("keywords", []) or []),
            repository=entry.get("repository", ""),
            homepage=entry.get("homepage", ""),
            description=entry.get("description", ""),
            recommended=bool(entry.get("recommended", False)),
        ))
    return out


def build_tool_index(modules: List[CatalogModule]) -> Dict[str, str]:
    """工具名 → 模块名 反查表（Agent 工具分发拦截用）。"""
    idx: Dict[str, str] = {}
    for m in modules:
        for t in m.provides_tools:
            idx[t] = m.name
    return idx


def build_keyword_index(modules: List[CatalogModule]) -> Dict[str, List[str]]:
    """关键词 → 模块名列表（对话意图预检粗匹配用）。"""
    idx: Dict[str, List[str]] = {}
    for m in modules:
        for kw in m.keywords:
            idx.setdefault(kw.lower(), []).append(m.name)
    return idx


def find_module_for_tool(tool_name: str, modules: List[CatalogModule]) -> Optional[CatalogModule]:
    """给定工具名，返回提供它的模块（无则 None）。"""
    for m in modules:
        if tool_name in m.provides_tools:
            return m
    return None


def match_modules_by_keywords(text: str, modules: List[CatalogModule]) -> List[Tuple[CatalogModule, int]]:
    """按用户自然语言粗匹配模块（返回 (模块, 命中关键词数)，按命中数降序）。"""
    if not text:
        return []
    lowered = text.lower()
    scored: List[Tuple[CatalogModule, int]] = []
    for m in modules:
        hits = sum(1 for kw in m.keywords if kw and kw.lower() in lowered)
        if hits:
            scored.append((m, hits))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


def get_catalog_modules(path_or_url: Optional[str] = None) -> List[CatalogModule]:
    """便捷：加载默认/指定 catalog 并解析为模块列表。"""
    return catalog_modules(load_catalog(path_or_url))


# ---------------------------------------------------------------------------
# 供应链校验 + 安全下载/解压
# ---------------------------------------------------------------------------

def verify_sha256(path: Path, expected: str) -> bool:
    """校验文件 sha256（expected 为空则跳过）。"""
    if not expected:
        return True
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest().lower() == expected.lower()


def download_file(url: str, dest: Path, sha256: Optional[str] = None,
                 progress: Optional[Any] = None, timeout: int = 60) -> bool:
    """下载文件到 dest，可选 sha256 校验。

    返回 True 表示下载且（若要求）校验通过；False 表示失败（dest 已清理）。
    file:// 也支持（便于测试用本地 tarball 充当 release）。
    """
    dest = Path(dest)
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "vermes-module-catalog/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp, open(tmp, "wb") as out:  # noqa: S310
            total = resp.length if hasattr(resp, "length") else None
            done = 0
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                if progress and total:
                    try:
                        progress(f"下载中 {done * 100 // total}%")
                    except Exception:  # noqa: BLE001
                        pass
    except Exception as e:  # noqa: BLE001
        logger.error("download_file failed: %s", e)
        tmp.unlink(missing_ok=True)
        return False

    if sha256 and not verify_sha256(tmp, sha256):
        logger.error("download_file: sha256 mismatch for %s", url)
        tmp.unlink(missing_ok=True)
        return False

    tmp.replace(dest)
    return True


def safe_extract(tar_path: Path, dest_dir: Path) -> None:
    """把 tar(.gz) 安全解压到 dest_dir，阻止路径穿越 / 越界符号链接。

    约定：release 包内条目以模块名作顶层前缀（如 mfgcad/tools.py），
    解压后自然落到 dest_dir/<name>/。本函数不假设前缀，只保证不越界。
    """
    dest_dir = Path(dest_dir).resolve()
    dest_dir.mkdir(parents=True, exist_ok=True)

    with tarfile.open(tar_path, "r:*") as tf:
        for member in tf.getmembers():
            # 拒绝绝对路径与 '..' 穿越
            name = member.name
            if name.startswith("/") or name.startswith("\\") or ".." in name.split("/") or ".." in name.split("\\"):
                raise ValueError(f"拒绝解压越界条目: {name!r}")
            target = (dest_dir / name).resolve()
            # 防止通过符号/硬链接逃逸（仅对实际有链接目标的条目检查）
            if (member.issym or member.islnk) and member.linkname:
                link = member.linkname
                if ".." in link.split("/") or ".." in link.split("\\") or link.startswith("/"):
                    raise ValueError(f"拒绝解压越界链接: {name!r} -> {link!r}")
                if not str((dest_dir / link).resolve()).startswith(str(dest_dir) + os.sep):
                    raise ValueError(f"拒绝解压越界链接: {name!r} -> {link!r}")
            if not str(target).startswith(str(dest_dir) + os.sep) and target != dest_dir:
                raise ValueError(f"拒绝解压越界条目: {name!r}")
        tf.extractall(dest_dir)  # 已在上面逐条校验


# ---------------------------------------------------------------------------
# 安装 + 就绪检查
# ---------------------------------------------------------------------------

def _modules_dir() -> Path:
    """返回 ~/.vermes/modules/（懒加载，避免配置未就绪时导入失败）。"""
    try:
        from agent.module_loader import get_modules_dir
        return get_modules_dir()
    except Exception:  # noqa: BLE001
        from vermes_constants import get_vermes_home
        return get_vermes_home() / "modules"


def _vermes_home() -> Path:
    """返回 ~/.vermes/（懒加载）。"""
    try:
        from vermes_constants import get_vermes_home
        return get_vermes_home()
    except Exception:  # noqa: BLE001
        return Path.home() / ".vermes"


def is_module_installed(name: str, modules_dir: Optional[Path] = None) -> bool:
    """模块是否已安装到 ~/.vermes/modules/<name>/（存在 module.yaml 即视为已装）。"""
    d = Path(modules_dir) if modules_dir else _modules_dir()
    return (d / name / "module.yaml").exists()


def install_module_code(name: str, modules: Optional[List[CatalogModule]] = None,
                         modules_dir: Optional[Path] = None,
                         progress: Optional[Any] = None) -> Path:
    """下载代码包 → sha256 校验 → 安全解压到 modules_dir/<name>/。

    返回模块根目录 Path。失败抛异常（调用方负责兜底文案）。
    """
    if modules is None:
        modules = get_catalog_modules()
    mod = next((m for m in modules if m.name == name), None)
    if mod is None:
        raise ModuleNotFoundError(f"catalog 中不存在模块 {name!r}")
    if not mod.code_asset:
        raise ValueError(f"模块 {name!r} 没有 code_asset（未发布 Release？）")

    d = Path(modules_dir) if modules_dir else _modules_dir()
    d.mkdir(parents=True, exist_ok=True)

    tmp = d / f".{name}.code.download.tmp"
    if not download_file(mod.code_asset, tmp, sha256=mod.code_sha256 or None, progress=progress):
        raise RuntimeError(f"下载/校验代码包失败: {mod.code_asset}")

    try:
        safe_extract(tmp, d)
    finally:
        tmp.unlink(missing_ok=True)

    root = d / name
    if not (root / "module.yaml").exists():
        raise RuntimeError(f"解压后未找到 {name}/module.yaml，打包格式可能非前缀布局")
    return root


async def ensure_module_ready(
    name: str,
    *,
    catalog_path_or_url: Optional[str] = None,
    auto_install: bool = True,
    progress: Optional[Any] = None,
) -> Tuple[bool, str]:
    """确保模块就绪（已装则直接放行；否则按需安装并热重载）。

    与 engine_setup.ensure_mac_ready 同构。返回 (ok, message)。
    """
    modules_dir = _modules_dir()
    if is_module_installed(name, modules_dir):
        return True, ""

    if not auto_install:
        return False, (
            f"⚙️ 模块 {name} 未安装，请到「模块商店」安装 "
            f"或调用 module_install({name})"
        )

    if progress:
        try:
            progress(f"⚙️ 首次安装「{name}」模块中，请稍候…（仅此一次）")
        except Exception:  # noqa: BLE001
            pass

    try:
        modules = get_catalog_modules(catalog_path_or_url)
        install_module_code(name, modules=modules, modules_dir=modules_dir, progress=progress)
    except Exception as e:  # noqa: BLE001
        return False, f"安装模块 {name} 失败：{e}"

    # 安装成功后热重载工具（复用现有 reload_module_tools）
    try:
        from agent.module_loader import reload_module_tools
        res = reload_module_tools(name)
        if not res.get("ok"):
            return False, f"模块 {name} 已下载但工具加载失败：{res.get('error')}"
    except Exception as e:  # noqa: BLE001
        return False, f"模块 {name} 已下载但热重载失败：{e}"

    return True, ""


def ensure_module_ready_sync(
    name: str,
    *,
    catalog_path_or_url: Optional[str] = None,
    auto_install: bool = True,
    progress: Optional[Any] = None,
) -> Tuple[bool, str]:
    """同步包装（CLI / 非 async 上下文用）。"""
    return asyncio.run(ensure_module_ready(
        name, catalog_path_or_url=catalog_path_or_url,
        auto_install=auto_install, progress=progress,
    ))


# ---------------------------------------------------------------------------
# P6: 重资产管理（引擎 / 权重 / 标准件库）
# ---------------------------------------------------------------------------

def install_module_asset(
    name: str,
    asset_id: str,
    *,
    modules: Optional[List[CatalogModule]] = None,
    progress: Optional[Any] = None,
) -> Path:
    """下载并安装模块的一个重资产（引擎 venv / 模型权重 / 标准件库）。

    与 install_module_code 同构：download → sha256 校验 → 解压到 target 目录。
    返回资产落点 Path。失败抛异常。
    """
    if modules is None:
        modules = get_catalog_modules()
    mod = next((m for m in modules if m.name == name), None)
    if mod is None:
        raise ModuleNotFoundError(f"catalog 中不存在模块 {name!r}")

    asset = next((a for a in mod.assets if a.id == asset_id), None)
    if asset is None:
        raise ValueError(f"模块 {name!r} 没有资产 {asset_id!r}")

    if not asset.url:
        raise ValueError(f"资产 {asset_id!r} 没有 url")

    # target 落点：绝对路径直接用；相对路径基于 ~/.vermes/
    if asset.target:
        target = Path(asset.target)
        if not target.is_absolute():
            target = _vermes_home() / asset.target
    else:
        target = _vermes_home() / "engines" / asset_id

    target.mkdir(parents=True, exist_ok=True)

    # 下载到临时文件
    tmp = target / f".{asset_id}.download.tmp"
    if not download_file(asset.url, tmp, sha256=asset.sha256 or None, progress=progress):
        raise RuntimeError(f"下载/校验资产失败: {asset.url}")

    try:
        # tar.gz 解压；其他格式（如 zip / 二进制）直接移动
        if asset.url.endswith(('.tar.gz', '.tgz')):
            safe_extract(tmp, target)
        else:
            dest = target / Path(asset.url).name
            tmp.rename(dest)
    finally:
        tmp.unlink(missing_ok=True)

    # 写 marker 供 _is_asset_ready 快速检测
    if asset.sha256:
        try:
            import json as _json
            (target / ".asset_ready").write_text(
                _json.dumps({"sha256": asset.sha256, "id": asset.id, "name": name})
            )
        except Exception:  # noqa: BLE001
            pass

    logger.info("资产 %s/%s 已安装到 %s", name, asset_id, target)
    return target


def list_module_assets(
    name: str,
    *,
    modules: Optional[List[CatalogModule]] = None,
) -> List[Dict[str, Any]]:
    """列出模块的所有重资产及其就绪状态。"""
    if modules is None:
        modules = get_catalog_modules()
    mod = next((m for m in modules if m.name == name), None)
    if mod is None or not mod.assets:
        return []

    result = []
    for a in mod.assets:
        target = _resolve_asset_target(a)
        ready = _is_asset_ready(a, target)
        result.append({
            "id": a.id,
            "label": a.label,
            "url": a.url,
            "sha256": a.sha256[:16] + "..." if a.sha256 else "",
            "size": a.size,
            "size_mb": round(a.size / 1048576, 1) if a.size else 0,
            "target": str(target),
            "ready": ready,
            "optional": a.optional,
        })
    return result


def ensure_assets_ready(
    name: str,
    *,
    auto_install: bool = True,
    progress: Optional[Any] = None,
) -> Tuple[bool, str]:
    """确保模块的所有必选重资产已就绪。缺失则自动下载安装。

    与 ensure_module_ready 同构。返回 (ok, message)。
    """
    modules = get_catalog_modules()
    mod = next((m for m in modules if m.name == name), None)
    if mod is None or not mod.assets:
        return True, ""  # 无资产或无 catalog = 无需检查

    missing = []
    for a in mod.assets:
        if a.optional:
            continue
        target = _resolve_asset_target(a)
        if not _is_asset_ready(a, target):
            missing.append(a)

    if not missing:
        return True, ""

    if not auto_install:
        labels = ", ".join(a.label or a.id for a in missing)
        return False, (
            f"⚙️ 模块 {name} 缺少重资产：{labels}。"
            f"请到「模块商店」安装或调用 install_module_asset('{name}', '<asset_id>')。"
        )

    for a in missing:
        if progress:
            try:
                progress(f"⚙️ 下载「{a.label or a.id}」中，请稍候…（{a.size // 1048576 if a.size else '?'}MB）")
            except Exception:  # noqa: BLE001
                pass
        try:
            install_module_asset(name, a.id, modules=modules, progress=progress)
        except Exception as e:  # noqa: BLE001
            return False, f"安装资产 {a.id} 失败：{e}"

    return True, ""


def _resolve_asset_target(asset: AssetSpec) -> Path:
    """计算资产落点路径。"""
    if asset.target:
        target = Path(asset.target)
        if not target.is_absolute():
            target = _vermes_home() / asset.target
    else:
        target = _vermes_home() / "engines" / asset.id
    return target


def _is_asset_ready(asset: AssetSpec, target: Path) -> bool:
    """检测资产是否已就绪。

    策略：target 目录存在且非空，且（有 sha256 时）包含一个 marker 文件记录已校验的 sha。
    """
    if not target.exists() or not target.is_dir():
        return False
    # 检查是否有内容
    children = list(target.iterdir())
    if not children:
        return False
    # 检查 marker（install_module_asset 成功后写入）
    marker = target / ".asset_ready"
    if marker.exists():
        try:
            import json as _json
            data = _json.loads(marker.read_text())
            if data.get("sha256") == asset.sha256:
                return True
        except Exception:  # noqa: BLE001
            pass
    # 无 marker 但目录有内容：如果资产无 sha256 要求，认为就绪
    return not asset.sha256
