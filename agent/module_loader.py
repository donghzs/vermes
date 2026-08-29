"""
Vermes 生态模块加载器

模块目录结构:
  ~/.vermes/modules/
    scholarforge/
      module.yaml
      backend/
        __init__.py
        blueprint.py      # register_to(app, host_api)
        tools.py           # register_tools(host_api)
        ...
      frontend/
        dist/
          entry.js        # 前端入口
          assets/

module.yaml 字段:
  name: 模块唯一标识
  display_name: 菜单显示名
  version: 语义版本
  backend.entry: 后端入口 (相对路径)
  backend.tools_entry: 工具注册入口 (可选)
  frontend.entry: 前端入口 (相对路径)
  frontend.route: 前端路由
  frontend.icon: 菜单图标
  frontend.menu_title: 菜单标题
  permissions: 需要的权限列表
  compatibility.vermes_min: 最低版本要求
"""
from __future__ import annotations

import importlib
import importlib.util
import logging
import sys
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field

import yaml

logger = logging.getLogger(__name__)

# 模块安装目录 — 延迟初始化，因为 get_vermes_home() 可能在配置加载才可用
_MODULES_DIR_CACHE = None

# 模块级缓存：app 和 host_api 引用，供 reload_module_tools 使用
_app_ref: Optional[Any] = None
_host_api_ref: Optional["HostAPI"] = None

# 模块 → 工具名集合映射（注册时实测快照，reload 时精确 deregister）
# key = module_name, value = set of tool names registered by that module
_module_tool_names: Dict[str, set] = {}

# 热加载并发保护：reload_module_tools 是唯一改动 registry/sys.modules 的入口，
# 所有调用方（apply_change 显式 reload、文件 watcher 兜底 reload）必须串行。
_reload_lock = threading.Lock()

# 文件 watcher 兜底：self_modify 已显式 reload 后写入时间戳，watcher 在窗口内
# 忽略同模块变更，避免一次写入触发两次 reload（显式 + watcher）。
_last_explicit_reload: Dict[str, float] = {}
_WATCHER_DEDUP_WINDOW = 2.0

# watcher 后台线程句柄（daemon），由 start_module_watcher() 启动
_module_watcher: Optional[threading.Thread] = None
# watcher 停止信号（模块级，便于测试干净关停 + 重启覆盖）
_watcher_stop = threading.Event()

def get_modules_dir() -> Path:
    """返回模块安装目录 (~/.vermes/modules/)"""
    global _MODULES_DIR_CACHE
    if _MODULES_DIR_CACHE is None:
        from vermes_constants import get_vermes_home
        _MODULES_DIR_CACHE = get_vermes_home() / "modules"
    return _MODULES_DIR_CACHE

# 向后兼容别名
MODULES_DIR = None  # 延迟属性，通过 get_modules_dir() 访问


@dataclass
class ModuleManifest:
    name: str
    display_name: str
    version: str
    description: str = ""
    author: str = ""
    homepage: str = ""
    backend_entry: Optional[str] = None
    tools_entry: Optional[str] = None
    frontend_entry: Optional[str] = None
    frontend_route: Optional[str] = None
    frontend_icon: str = "📦"
    frontend_menu_title: str = ""
    permissions: List[str] = field(default_factory=list)
    vermes_min: str = "0.0.0"
    # 新增（P1 可插拔模块）：声明本模块提供的 Agent 工具 + 意图关键词，
    # 供 Agent 自动检测「缺失模块」用（见 agent/module_catalog.py）。
    provides_tools: List[str] = field(default_factory=list)
    keywords: List[str] = field(default_factory=list)
    repository: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)
    # 模块根目录（内置模块=bundle 路径，第三方插件=~/.vermes/modules/<name>）
    # 不再硬编码 ~/.vermes/modules，避免“双路径/三副本”陷阱。
    module_root: Optional[Path] = None
    # True=内置模块（直接从打包加载，零拷贝）；False=第三方热插件
    builtin: bool = False


def parse_manifest(module_dir: Path) -> Optional[ModuleManifest]:
    """解析 module.yaml"""
    yaml_path = module_dir / "module.yaml"
    if not yaml_path.exists():
        return None
    try:
        data = yaml.safe_load(yaml_path.read_text())
    except Exception as e:
        logger.error("Failed to parse %s: %s", yaml_path, e)
        return None

    backend = data.get("backend", {}) or {}
    frontend = data.get("frontend", {}) or {}
    compat = data.get("compatibility", {}) or {}

    return ModuleManifest(
        name=data["name"],
        display_name=data.get("display_name", data["name"]),
        version=data.get("version", "0.0.0"),
        description=data.get("description", ""),
        author=data.get("author", ""),
        homepage=data.get("homepage", ""),
        backend_entry=backend.get("entry"),
        tools_entry=backend.get("tools_entry"),
        frontend_entry=frontend.get("entry"),
        frontend_route=frontend.get("route"),
        frontend_icon=frontend.get("icon", "📦"),
        frontend_menu_title=frontend.get("menu_title", ""),
        permissions=data.get("permissions", []),
        vermes_min=compat.get("vermes_min", "0.0.0"),
        # P1 可插拔模块：声明工具 + 意图关键词 + 仓库地址
        provides_tools=data.get("provides_tools", []) or [],
        keywords=data.get("keywords", []) or [],
        repository=data.get("repository", ""),
        raw=data,
        module_root=module_dir,
        builtin=False,
    )


class HostAPI:
    """宿主接口 — 向模块暴露 Vermes 核心能力"""

    def __init__(self):
        # 延迟导入避免循环
        from vermes_cli.blueprints.chat import (
            PROVIDERS,
            _get_chat_credentials,
            _resolve_model_provider,
        )
        from vermes_constants import get_vermes_home
        from tools.registry import registry

        self.PROVIDERS = PROVIDERS
        self.get_chat_credentials = _get_chat_credentials
        self.resolve_model_provider = _resolve_model_provider
        self.get_vermes_home = get_vermes_home
        self.registry = registry

    def resolve_model_provider(self, *args, **kwargs):
        return self.resolve_model_provider(*args, **kwargs)


def load_module_pyd(module_dir: Path, manifest: ModuleManifest) -> Optional[Any]:
    """加载模块后端 Python 代码"""
    if not manifest.backend_entry:
        return None

    entry_path = module_dir / manifest.backend_entry  # e.g. backend/blueprint.py
    if not entry_path.exists():
        logger.error("Module %s: backend entry not found: %s", manifest.name, entry_path)
        return None

    # 模块名
    mod_name = f"_vermes_module_{manifest.name}"

    # 将模块目录加入 sys.path，使 from host_api import xxx 和 from .xxx 能工作
    backend_dir = entry_path.parent  # backend/
    backend_parent = backend_dir.parent  # module root dir

    # 把 backend/ 目录加入 sys.path，让 from host_api import xxx 可用
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

    # 把 backend/ 目录的父目录加入 sys.path，让 from backend.xxx 可用
    # 同时把 backend/ 本身作为 package 加载
    spec = importlib.util.spec_from_file_location(
        mod_name,
        entry_path,
        submodule_search_locations=[str(backend_dir)],
    )
    if spec is None or spec.loader is None:
        logger.error("Module %s: cannot create spec for %s", manifest.name, entry_path)
        return None

    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception as e:
        logger.error("Module %s: failed to exec: %s", manifest.name, e)
        import traceback
        traceback.print_exc()
        del sys.modules[mod_name]
        return None

    logger.info("Module %s v%s loaded from %s", manifest.name, manifest.version, entry_path)
    return mod


def _get_bundle_root() -> Optional[Path]:
    """返回打包根目录（含 vermes_cli/），源码/打包两种安装均适用。

    - PyInstaller 打包：sys._MEIPASS
    - 源码运行：本文件上两级（agent/ 的父目录 = 仓库根）
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    # agent/module_loader.py -> agent/ -> 仓库根
    return Path(__file__).resolve().parent.parent


# 内置模块注册表：名称 -> 打包内后端包子目录（相对 bundle 根）。
# 这些是自家功能，代码已编译进桁面应用，直接从 bundle 加载，零拷贝。
_BUILTIN_MODULES: Dict[str, str] = {
    "scholarforge": "vermes_cli/scholarforge",
    "mfgcad": "vermes_cli/mfgcad",
    "cadir": "vermes_cli/cadir",
}


def is_builtin_module(name: str) -> bool:
    """判断模块名是否为内置模块（P5 降级时用：内置模块不从远程下载）。"""
    return name in _BUILTIN_MODULES


def _synth_scholarforge_manifest(pkg_dir: Path) -> ModuleManifest:
    """为内置 ScholarForge 合成 manifest（无需 module.yaml 文件）。

    前端 entry 指向打包内 web_dist 下的模块前端（由 serve_module_frontend 处理）。
    若前端目录不存在，则 frontend_entry=None，避免 ModuleHost 加载 404。
    """
    # 检测前端目录是否存在（打包时打入 web_dist/modules/scholarforge/）
    frontend_dir = pkg_dir / "frontend"
    has_frontend = frontend_dir.is_dir()

    return ModuleManifest(
        name="scholarforge",
        display_name="ScholarForge 论文写作",
        version="1.0.0",
        description="AI 全链路学术写作——文献搜索、STORM 写作、查重检测、论文评分、Word 导出",
        author="Vermes Team",
        homepage="https://vbit.top/vermes",
        backend_entry="blueprint.py",
        tools_entry="tools.py",
        frontend_entry="entry.js" if has_frontend else None,
        frontend_route="/scholarforge" if has_frontend else None,
        frontend_icon="📝",
        frontend_menu_title="论文" if has_frontend else "",
        permissions=["llm_call", "file_read", "file_write", "web_search"],
        vermes_min="2.1.0",
        raw={},
        module_root=pkg_dir,
        builtin=True,
    )


def discover_builtin_modules() -> List[ModuleManifest]:
    """发现打包内置模块（ScholarForge 等自家功能）。

    从 bundle 直接加载，不拷贝、不走 ~/.vermes/modules/。
    消除“代码都编译进桁了却要手动热加载”的设计债。
    """
    bundle = _get_bundle_root()
    if bundle is None:
        return []
    manifests: List[ModuleManifest] = []
    for name, rel in _BUILTIN_MODULES.items():
        pkg_dir = bundle / rel
        if not pkg_dir.is_dir():
            logger.debug("Builtin module %s: package dir not found at %s", name, pkg_dir)
            continue
        if name == "scholarforge":
            manifests.append(_synth_scholarforge_manifest(pkg_dir))
        elif name == "mfgcad":
            manifests.append(_synth_mfgcad_manifest(pkg_dir))
        elif name == "cadir":
            manifests.append(_synth_cadir_manifest(pkg_dir))
    return manifests


def _synth_mfgcad_manifest(pkg_dir: Path) -> ModuleManifest:
    """为内置 mfgcad（制造业 text-to-CAD）合成 manifest（无需 module.yaml）。

    mfgcad 是无前端的 Agent 工具模块：后端入口即 tools.py（register_tools 在其中），
    无 FastAPI blueprint、无前端路由。引擎（MAC）走独立 venv 子进程桥接，
    不在此加载。
    """
    return ModuleManifest(
        name="mfgcad",
        display_name="制造 CAD",
        version="0.1.0",
        description="制造业 text-to-CAD：自然语言需求直接生成 STEP 三维模型（双引擎校验）",
        author="Vermes Team",
        homepage="https://vbit.top/vermes",
        backend_entry="tools.py",
        tools_entry="tools.py",
        frontend_entry=None,
        frontend_route=None,
        frontend_icon="🏭",
        frontend_menu_title="",
        permissions=["llm_call", "file_read", "file_write"],
        vermes_min="2.1.0",
        raw={},
        module_root=pkg_dir,
        builtin=True,
    )


def _synth_cadir_manifest(pkg_dir: Path) -> ModuleManifest:
    """为内置 cadir（CAD-IR 契约建模）合成 manifest（无需 module.yaml）。

    与 mfgcad 同类：无前端的 Agent 工具模块，后端入口即 tools.py
    （register_tools 在其中），无 FastAPI blueprint、无前端路由。
    3D 几何核验/构建（build123d/trimesh/numpy）走独立引擎 venv 子进程桥接，
    不在宿主进程加载。契约编译器（cad_ir_contract.py）为纯 Python，进程内加载。
    """
    return ModuleManifest(
        name="cadir",
        display_name="CAD-IR 契约建模",
        version="0.1.0",
        description="CAD-IR 契约建模工具集（cad.ir.v1）：LLM 生成 JSON 契约→确定性编译→build123d→STEP，附 STEP/STL 几何独立核验",
        author="Vermes Team",
        homepage="https://github.com/donghzs/vermes-mod-cadir",
        backend_entry="tools.py",
        tools_entry="tools.py",
        frontend_entry=None,
        frontend_route=None,
        frontend_icon="📐",
        frontend_menu_title="",
        permissions=["subprocess", "filesystem_write", "file_read"],
        provides_tools=[
            "cadir_compile",
            "cadir_build",
            "cadir_verify_step",
            "cadir_verify_stl",
        ],
        keywords=["CAD", "契约", "IR", "cad.ir.v1"],
        repository="donghzs/vermes-mod-cadir",
        vermes_min="2.3.9",
        raw={},
        module_root=pkg_dir,
        builtin=True,
    )


def discover_modules() -> List[ModuleManifest]:
    """发现所有模块：内置模块（bundle）+ 第三方插件（~/.vermes/modules/）。

    内置模块优先；同名第三方插件会被内置版覆盖（避免旧拷贝遮蔽新代码）。
    """
    manifests: List[ModuleManifest] = list(discover_builtin_modules())
    _seen = {m.name for m in manifests}

    modules_dir = get_modules_dir()
    if modules_dir.exists():
        for entry in sorted(modules_dir.iterdir()):
            if not entry.is_dir():
                continue
            if entry.name.startswith("."):
                continue
            if entry.name in _seen:
                logger.info(
                    "Skipping third-party module %s: shadowed by builtin", entry.name
                )
                continue
            manifest = parse_manifest(entry)
            if manifest:
                manifests.append(manifest)
                _seen.add(manifest.name)
            else:
                logger.warning("Skipping %s: no valid module.yaml", entry)

    return manifests


def register_modules(app, host_api: HostAPI):
    """注册所有已安装模块的后端路由和工具"""
    manifests = discover_modules()
    if not manifests:
        logger.info("No modules installed")
        return []

    registered = []
    for manifest in manifests:
        # module_root 已在 discover 阶段确定：
        #   内置模块 -> bundle 内 vermes_cli/<name>
        #   第三方插件 -> ~/.vermes/modules/<name>
        # 不再硬编码 ~/.vermes/modules，彻底消除双路径/三副本陷阱。
        _mod_dir = manifest.module_root
        if _mod_dir is None:
            from vermes_constants import get_vermes_home
            _mod_dir = get_vermes_home() / "modules" / manifest.name
        logger.info(
            "Module %s: loading from %s (%s)",
            manifest.name, _mod_dir,
            "builtin/bundle" if manifest.builtin else "third-party",
        )
        mod = load_module_pyd(_mod_dir, manifest)
        if mod is None:
            continue

        # 注册后端路由
        if hasattr(mod, "register_to"):
            try:
                mod.register_to(app, host_api=host_api)
                logger.info("Module %s: backend routes registered", manifest.name)
            except Exception as e:
                logger.error("Module %s: register_to failed: %s", manifest.name, e)

        # 注册 Agent 工具（注册前后快照，记录该模块注册了哪些工具名）
        _before = set(registry_snapshot_names())
        if manifest.tools_entry and hasattr(mod, "register_tools"):
            try:
                mod.register_tools(host_api)
                _after = set(registry_snapshot_names())
                _module_tool_names[manifest.name] = _after - _before
                logger.info("Module %s: agent tools registered (%d tools)",
                            manifest.name, len(_module_tool_names.get(manifest.name, set())))
            except Exception as e:
                logger.error("Module %s: register_tools failed: %s", manifest.name, e)
        elif manifest.tools_entry:
            # tools_entry 是单独文件（相对 module_root 解析）
            tools_path = _mod_dir / manifest.tools_entry
            if tools_path.exists():
                tools_mod_name = f"_vermes_module_{manifest.name}_tools"
                spec2 = importlib.util.spec_from_file_location(
                    tools_mod_name,
                    tools_path,
                    submodule_search_locations=[str(tools_path.parent)],
                )
                tools_mod = importlib.util.module_from_spec(spec2)
                sys.modules[tools_mod_name] = tools_mod
                spec2.loader.exec_module(tools_mod)
                if hasattr(tools_mod, "register_tools"):
                    tools_mod.register_tools(host_api)
                    _after = set(registry_snapshot_names())
                    _module_tool_names[manifest.name] = _after - _before
                    logger.info("Module %s: agent tools registered (%d tools, separate file)",
                                manifest.name, len(_module_tool_names.get(manifest.name, set())))

        registered.append(manifest)

    return registered


# FastAPI 端点：返回已安装模块列表（供前端读取）
def register_module_api(app, host_api: HostAPI):
    """注册 /api/modules/* 端点"""
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    @app.get("/api/modules")
    async def list_modules():
        """返回已安装模块列表"""
        manifests = discover_modules()
        return {
            "modules": [
                {
                    "name": m.name,
                    "display_name": m.display_name,
                    "version": m.version,
                    "description": m.description,
                    "frontend_entry": m.frontend_entry,
                    "frontend_route": m.frontend_route,
                    "frontend_icon": m.frontend_icon,
                    "frontend_menu_title": m.frontend_menu_title,
                    "permissions": m.permissions,
                }
                for m in manifests
            ]
        }

    @app.get("/api/modules/{module_name}/frontend/{file_path:path}")
    async def serve_module_frontend(module_name: str, file_path: str):
        """返回模块前端静态文件。

        内置模块：从 bundle 内 vermes_cli/web_dist/modules/<name>/ 提供（构建时打入）。
        第三方插件：从 ~/.vermes/modules/<name>/frontend/dist/ 提供（热加载）。
        """
        from fastapi.responses import Response
        import mimetypes

        # 解析该模块的前端根目录
        base = None
        if module_name in _BUILTIN_MODULES:
            bundle = _get_bundle_root()
            if bundle is not None:
                # 构建时 ScholarForge 前端打入 web_dist/modules/<name>/
                _cand = bundle / "vermes_cli" / "web_dist" / "modules" / module_name
                if _cand.is_dir():
                    base = _cand
        if base is None:
            # 第三方插件（或内置模块回退到热加载目录）
            base = get_modules_dir() / module_name / "frontend" / "dist"

        full = base / file_path
        try:
            full = full.resolve()
            base = base.resolve()
            # 路径穿越防护：规范化后必须仍位于 base 之下。
            # 尾斜杠防止 “base 是另一目录前缀” 的误判（如 /a/b/scholar vs /a/b/scholarX）。
            _base_prefix = str(base).rstrip("/") + "/"
            if not str(full).startswith(_base_prefix):
                return JSONResponse({"error": "forbidden"}, status_code=403)
        except Exception:
            # 规范化失败（符号链接异常 / 权限问题等）一律拒绝，不跳过校验
            return JSONResponse({"error": "forbidden"}, status_code=403)
        if not full.exists() or not full.is_file():
            return JSONResponse({"error": "not found"}, status_code=404)
        _mime, _ = mimetypes.guess_type(str(full))
        return Response(content=full.read_bytes(), media_type=_mime or "application/octet-stream")


# ---------------------------------------------------------------------------
# Hot reload support — Phase 0
# ---------------------------------------------------------------------------

def _set_app_ref(app, host_api: "HostAPI") -> None:
    """启动时由 web_server.py 调用，缓存 app 和 host_api 引用。"""
    global _app_ref, _host_api_ref
    _app_ref = app
    _host_api_ref = host_api


def _get_host_api() -> "HostAPI":
    """获取启动时缓存的 HostAPI 实例。"""
    global _host_api_ref
    if _host_api_ref is None:
        _host_api_ref = HostAPI()
    return _host_api_ref


def registry_snapshot_names() -> list:
    """返回当前 registry 中所有工具名的快照（用于注册前后 diff）。"""
    try:
        from tools.registry import registry
        return [e.name for e in registry._snapshot_entries()]
    except Exception:
        return []


def is_module_hot_path(target_path: str) -> bool:
    """判断 target 是否在 ~/.vermes/modules/ 热路径下。"""
    try:
        modules_dir = get_modules_dir().resolve()
        Path(target_path).resolve().relative_to(modules_dir)
        return True
    except (ValueError, OSError):
        return False


def extract_module_name(target_path: str) -> str:
    """从 ~/.vermes/modules/<name>/... 路径提取模块名。"""
    try:
        parts = Path(target_path).resolve().parts
        for i, part in enumerate(parts):
            if part == "modules" and i + 1 < len(parts):
                return parts[i + 1]
    except (OSError, ValueError):
        pass
    return ""


def reload_module_tools(name: str) -> dict:
    """运行态重新加载单个模块的工具（deregister 旧 → load 新 → register 新）。

    流程：
    1. 从 _module_tool_names 取该模块注册的旧工具名集合
    2. 逐个 deregister 旧工具
    3. 清除 sys.modules 中的旧模块缓存
    4. 重新 parse_manifest + load + register_tools

    返回 {"ok": bool, "state": str, "error": str|None, "tools_loaded": int}
    """
    with _reload_lock:
        from tools.registry import registry

        mod_dir = get_modules_dir() / name
        if not mod_dir.exists():
            return {"ok": False, "state": "not_found", "error": f"module {name} not found", "tools_loaded": 0}

        manifest = parse_manifest(mod_dir)
        if not manifest:
            return {"ok": False, "state": "parse_failed", "error": "invalid module.yaml", "tools_loaded": 0}

        # Step 1: deregister old tools (using recorded names, not toolset guessing)
        old_names = _module_tool_names.pop(name, set())
        deregistered = 0
        for tool_name in old_names:
            registry.deregister(tool_name)
            deregistered += 1
        if deregistered:
            logger.info("Hot reload %s: deregistered %d old tools", name, deregistered)

        # Step 2: clear sys.modules cache for this module
        cleared = 0
        for key in list(sys.modules.keys()):
            if key.startswith(f"_vermes_module_{name}"):
                del sys.modules[key]
                cleared += 1
        if cleared:
            logger.debug("Hot reload %s: cleared %d sys.modules entries", name, cleared)

        # Step 2b: invalidate importlib caches and clear __pycache__ for this module
        # SourceFileLoader may use stale .pyc files otherwise
        importlib.invalidate_caches()
        for pycache in (mod_dir / "backend").rglob("__pycache__"):
            for pyc in pycache.iterdir():
                if name in pyc.name or "tools" in pyc.name or "blueprint" in pyc.name:
                    try:
                        pyc.unlink()
                    except OSError:
                        pass

        # Step 3: re-load and register tools
        host_api = _get_host_api()
        before = set(registry_snapshot_names())

        # Try loading via tools_entry (separate file) or backend_entry
        tools_path = mod_dir / manifest.tools_entry if manifest.tools_entry else None
        loaded = False

        if tools_path and tools_path.exists():
            tools_mod_name = f"_vermes_module_{name}_tools"
            spec = importlib.util.spec_from_file_location(
                tools_mod_name,
                str(tools_path),
                submodule_search_locations=[str(tools_path.parent)],
            )
            if spec and spec.loader:
                tools_mod = importlib.util.module_from_spec(spec)
                sys.modules[tools_mod_name] = tools_mod
                try:
                    spec.loader.exec_module(tools_mod)
                    if hasattr(tools_mod, "register_tools"):
                        tools_mod.register_tools(host_api)
                        loaded = True
                    else:
                        return {"ok": False, "state": "no_register_tools",
                                "error": "tools.py has no register_tools function", "tools_loaded": 0}
                except Exception as e:
                    del sys.modules[tools_mod_name]
                    logger.error("Hot reload %s: exec failed: %s", name, e)
                    return {"ok": False, "state": "exec_failed", "error": str(e), "tools_loaded": 0}
        else:
            # Fall back to backend_entry (module with register_tools on main module object)
            mod = load_module_pyd(mod_dir, manifest)
            if mod and hasattr(mod, "register_tools"):
                try:
                    mod.register_tools(host_api)
                    loaded = True
                except Exception as e:
                    return {"ok": False, "state": "register_failed", "error": str(e), "tools_loaded": 0}

        if not loaded:
            return {"ok": False, "state": "no_tools", "error": "no tools_entry or register_tools found", "tools_loaded": 0}

        after = set(registry_snapshot_names())
        new_names = after - before
        _module_tool_names[name] = new_names

        logger.info("Hot reload %s: registered %d new tools", name, len(new_names))
        return {"ok": True, "state": "reloaded", "error": None, "tools_loaded": len(new_names)}


# ── Phase 4.2: 插件生命周期 —— 卸载（install/active/reload 已有，补 uninstall） ──

from enum import Enum


class ModuleLifecycle(str, Enum):
    """插件生命周期状态机（Phase 4.2）。

    - INSTALLED: 已落地 module 目录（install_module 完成）
    - ACTIVE:    工具已注册进 registry（reload_module_tools 成功）
    - RELOADING: 热重载进行中（_reload_lock 持有）
    - UNINSTALLED: 已 deregister + 清缓存 + 删目录（uninstall_module 完成）
    """
    INSTALLED = "installed"
    ACTIVE = "active"
    RELOADING = "reloading"
    UNINSTALLED = "uninstalled"


def uninstall_module(name: str) -> dict:
    """卸载插件：deregister 工具 → 清 sys.modules 缓存 → 清追踪映射 → 删目录。

    与 reload_module_tools 对称：reload 是「原地替换」，uninstall 是「彻底移除」。
    失败不抛异常、不新增信任面，仅透出诊断（删除目录失败可能是权限问题，
    但不应让调用方崩溃）。返回 {"ok", "state", "error", "tools_removed"}。
    """
    with _reload_lock:
        from tools.registry import registry

        removed = 0
        old_names = _module_tool_names.pop(name, set())
        for tool_name in old_names:
            try:
                registry.deregister(tool_name)
                removed += 1
            except Exception as exc:  # 防御：单个 deregister 失败不阻断其余
                logger.warning("uninstall %s: deregister %s failed: %s", name, tool_name, exc)
        if removed:
            logger.info("uninstall %s: deregistered %d tools", name, removed)

        # 清 sys.modules 缓存（与 reload 同策略）
        for key in list(sys.modules.keys()):
            if key.startswith(f"_vermes_module_{name}"):
                del sys.modules[key]
        importlib.invalidate_caches()

        # 删目录（卸载的物理动作）
        mod_dir = get_modules_dir() / name
        deleted = False
        if mod_dir.exists():
            try:
                import shutil
                shutil.rmtree(mod_dir)
                deleted = True
                logger.info("uninstall %s: removed module dir %s", name, mod_dir)
            except Exception as exc:
                logger.error("uninstall %s: rmtree failed: %s", name, exc)
                return {
                    "ok": False, "state": ModuleLifecycle.UNINSTALLED.value,
                    "error": f"deregistered {removed} tools but rmtree failed: {exc}",
                    "tools_removed": removed,
                }

        return {
            "ok": True,
            "state": ModuleLifecycle.UNINSTALLED.value,
            "error": None,
            "tools_removed": removed,
            "dir_deleted": deleted,
        }


def mark_explicit_reload(name: str) -> None:
    """记录一次由 self_modify/apply_change 显式触发的 reload 时间戳。

    文件 watcher 在窗口内看到同模块变更时会忽略，避免一次写入被
    reload 两次（显式 + watcher）。patch/write_file 等绕过 self_modify
    的写入不会调用本函数，因此 watcher 会作为安全网兜底触发 reload。
    """
    _last_explicit_reload[name] = time.time()


def start_module_watcher(poll_interval: float = 0.5, debounce: float = 0.3) -> None:
    """启动后台轮询，监听 ~/.vermes/modules/ 下 .py/.yaml 变更并热重载对应模块。

    这是「热路径文件被绕过 self_modify 直接写入」（如 patch/write_file 工具）
    时的安全网：那些写入落盘但不调用 reload_module_tools，没有 watcher 就只能
    等重启才生效。watcher 与 apply_change 的显式 reload 通过 _reload_lock 串行、
    通过 _last_explicit_reload 去重，互不打架。

    采用 dependency-free 轮询（不引 watchdog），避免冻结包新增第三方依赖。
    """
    global _module_watcher
    if _module_watcher is not None and _module_watcher.is_alive():
        return

    _watcher_stop.clear()
    mtimes: Dict[str, float] = {}          # 文件路径 -> 上次已知 mtime
    pending: Dict[str, float] = {}         # 模块名 -> 首次变更时间（用于 debounce）

    def _poll() -> None:
        while not _watcher_stop.is_set():
            try:
                modules_dir = get_modules_dir()
                if modules_dir.exists():
                    for p in modules_dir.rglob("*"):
                        if not p.is_file():
                            continue
                        if p.suffix not in (".py", ".yaml", ".yml"):
                            continue
                        try:
                            mt = p.stat().st_mtime
                        except OSError:
                            continue
                        key = str(p)
                        prev = mtimes.get(key)
                        if prev is None:
                            # 首次见到：只记录，不触发（避免启动即误 reload）
                            mtimes[key] = mt
                            continue
                        if mt != prev:
                            mtimes[key] = mt
                            mod = extract_module_name(key)
                            if mod:
                                pending[mod] = time.time()
                    # 处理达到 debounce 阈值的待 reload 模块
                    now = time.time()
                    for mod in list(pending.keys()):
                        if now - pending[mod] < debounce:
                            continue
                        del pending[mod]
                        # 去重：显式 reload 刚做过则跳过
                        if now - _last_explicit_reload.get(mod, 0.0) < _WATCHER_DEDUP_WINDOW:
                            continue
                        try:
                            res = reload_module_tools(mod)
                        except Exception as e:  # 单次失败不应拖垮轮询线程
                            logger.error("[ModuleWatcher] reload %s error: %s", mod, e)
                            continue
                        if res.get("ok"):
                            logger.info("[ModuleWatcher] hot-reloaded %s (tools=%d)", mod, res.get("tools_loaded"))
                        else:
                            logger.warning("[ModuleWatcher] reload %s skipped: %s", mod, res.get("error"))
            except Exception as e:
                logger.debug("[ModuleWatcher] poll error: %s", e)
            _watcher_stop.wait(poll_interval)

    t = threading.Thread(target=_poll, name="module-watcher", daemon=True)
    t.start()
    _module_watcher = t
    logger.info("[ModuleWatcher] started, watching %s (poll=%ss debounce=%ss)",
                get_modules_dir(), poll_interval, debounce)


def stop_module_watcher() -> None:
    """停止后台文件 watcher（主要用于测试与干净关停）。"""
    global _module_watcher
    _watcher_stop.set()
    if _module_watcher is not None:
        _module_watcher.join(timeout=2.0)
        _module_watcher = None

