"""Phase 4.2: 插件生命周期 —— uninstall_module 闭环测试。

验证卸载对称于 reload：deregister 工具 → 清 sys.modules 缓存 → 清追踪映射
→ 删目录。不依赖全量套件，用 fake_catalog 落一个真实模块目录。
"""
import sys
import importlib
from pathlib import Path

import pytest

from agent import module_loader as ml


@pytest.fixture
def installed_module(tmp_path, monkeypatch):
    """落一个真实模块目录，并把 modules_dir 指向 tmp_path，模拟已注册状态。"""
    cat_path = tmp_path / "catalog.json"
    cat_path.write_text("{}")
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()
    mod_dir = modules_dir / "demo"
    mod_dir.mkdir()
    (mod_dir / "module.yaml").write_text(
        "name: demo\nversion: 1.0.0\ndisplay_name: Demo\n"
    )
    backend = mod_dir / "backend"
    backend.mkdir()
    (backend / "__init__.py").write_text("")
    tools_py = backend / "tools.py"
    tools_py.write_text(
        "def register_tools(host_api):\n"
        "    host_api.registry.register(name='demo_tool', toolset='demo',\n"
        "        schema={'name': 'demo_tool'}, handler=lambda args: 'ok')\n"
    )
    # 把全局 modules_dir 缓存指到 tmp，隔离真实 ~/.vermes
    monkeypatch.setattr(ml, "_MODULES_DIR_CACHE", modules_dir)
    # 真实走一遍 register_tools，让工具真正进入 registry（而非仅塞追踪映射）
    from agent.module_loader import HostAPI
    api = HostAPI()
    spec = importlib.util.spec_from_file_location(
        "_vermes_module_demo_tools", str(tools_py),
        submodule_search_locations=[str(backend)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_vermes_module_demo_tools"] = mod
    spec.loader.exec_module(mod)
    mod.register_tools(api)
    # 模拟 reload 已注册：把工具名塞进追踪映射
    ml._module_tool_names["demo"] = {"demo_tool"}
    yield mod_dir
    # 清理追踪映射，避免污染其他测试
    ml._module_tool_names.pop("demo", None)
    sys.modules.pop("_vermes_module_demo_tools", None)


def test_uninstall_removes_tools_and_dir(installed_module):
    """卸载后：工具从 registry 消失 + 模块目录被删。"""
    from tools.registry import registry

    # 前置：工具已注册
    assert registry.get_entry("demo_tool") is not None

    res = ml.uninstall_module("demo")
    assert res["ok"] is True
    assert res["state"] == ml.ModuleLifecycle.UNINSTALLED.value
    assert res["tools_removed"] == 1
    assert res["dir_deleted"] is True
    # 工具已 deregister
    assert registry.get_entry("demo_tool") is None
    # 目录已删
    assert not installed_module.exists()
    # 追踪映射已清
    assert "demo" not in ml._module_tool_names


def test_uninstall_unknown_module_is_safe(installed_module):
    """卸载不存在的模块不抛异常、不崩溃（零信任面新增）。"""
    res = ml.uninstall_module("ghost")
    assert res["ok"] is True
    assert res["state"] == ml.ModuleLifecycle.UNINSTALLED.value
    assert res["tools_removed"] == 0


def test_uninstall_clears_sys_modules_cache(installed_module):
    """卸载清除 _vermes_module_demo 的 sys.modules 缓存，避免旧代码残留。"""
    # 模拟 reload 留下的缓存条目
    sys.modules["_vermes_module_demo_tools"] = object()
    res = ml.uninstall_module("demo")
    assert res["ok"] is True
    assert "_vermes_module_demo_tools" not in sys.modules


def test_module_lifecycle_enum_values():
    """生命周期状态机四态语义清晰、可用于状态记录。"""
    assert {s.value for s in ml.ModuleLifecycle} == {
        "installed", "active", "reloading", "uninstalled"
    }
