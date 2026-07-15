"""
Vermes 生态模块加载器

模块目录结构:
  ~/.hermes/modules/
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
from pathlib import Path
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field

import yaml

logger = logging.getLogger(__name__)

# 模块安装目录 — 延迟初始化，因为 get_hermes_home() 可能在配置加载后才可用
_MODULES_DIR_CACHE = None

def get_modules_dir() -> Path:
    """返回模块安装目录 (~/.hermes/modules/)"""
    global _MODULES_DIR_CACHE
    if _MODULES_DIR_CACHE is None:
        from hermes_constants import get_hermes_home
        _MODULES_DIR_CACHE = get_hermes_home() / "modules"
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
    raw: Dict[str, Any] = field(default_factory=dict)


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
        raw=data,
    )


class HostAPI:
    """宿主接口 — 向模块暴露 Vermes 核心能力"""

    def __init__(self):
        # 延迟导入避免循环
        from hermes_cli.blueprints.chat import (
            PROVIDERS,
            _get_chat_credentials,
            _resolve_model_provider,
        )
        from hermes_constants import get_hermes_home
        from tools.registry import registry

        self.PROVIDERS = PROVIDERS
        self.get_chat_credentials = _get_chat_credentials
        self.resolve_model_provider = _resolve_model_provider
        self.get_hermes_home = get_hermes_home
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


def discover_modules() -> List[ModuleManifest]:
    """扫描模块目录，发现所有已安装模块"""
    modules_dir = get_modules_dir()
    if not modules_dir.exists():
        return []

    manifests = []
    for entry in sorted(modules_dir.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name.startswith("."):
            continue
        manifest = parse_manifest(entry)
        if manifest:
            manifests.append(manifest)
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
        from hermes_constants import get_hermes_home
        mod = load_module_pyd(
            get_hermes_home() / "modules" / manifest.name,
            manifest,
        )
        if mod is None:
            continue

        # 注册后端路由
        if hasattr(mod, "register_to"):
            try:
                mod.register_to(app, host_api=host_api)
                logger.info("Module %s: backend routes registered", manifest.name)
            except Exception as e:
                logger.error("Module %s: register_to failed: %s", manifest.name, e)

        # 注册 Agent 工具
        if manifest.tools_entry and hasattr(mod, "register_tools"):
            try:
                mod.register_tools(host_api)
                logger.info("Module %s: agent tools registered", manifest.name)
            except Exception as e:
                logger.error("Module %s: register_tools failed: %s", manifest.name, e)
        elif manifest.tools_entry:
            # tools_entry 是单独文件
            tools_path = (get_hermes_home() / "modules" / manifest.name / manifest.tools_entry)
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
                    logger.info("Module %s: agent tools registered (separate file)", manifest.name)

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
        """返回模块前端静态文件"""
        from hermes_constants import get_hermes_home
        base = get_hermes_home() / "modules" / module_name / "frontend" / "dist"
        full = base / file_path
        if not full.exists():
            return JSONResponse({"error": "not found"}, status_code=404)
        return full.read_bytes()
