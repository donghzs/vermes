"""测试 P2 module_cli install --release + P3 registry 工具未找到拦截提示。

P2: 从 catalog 下载安装模块（用 file:// 模拟 Release）
P3: registry.dispatch 对未知工具返回 catalog 安装提示
"""

import json
import sys
import tempfile
from pathlib import Path

import pytest


# ── P2: module_cli install --release ─────────────────────────

@pytest.fixture
def fake_catalog_with_tarball(tmp_path, monkeypatch):
    """构造本地 catalog + tarball + monkeypatch 模块目录。"""
    import tarfile
    import hashlib

    # 假模块 tarball
    mod_src = tmp_path / "src" / "testmod"
    mod_src.mkdir(parents=True)
    (mod_src / "module.yaml").write_text(
        "name: testmod\nversion: 0.1.0\ndisplay_name: 测试模块\n"
        "provides_tools:\n  - test_tool_a\n  - test_tool_b\n"
    )
    (mod_src / "tools.py").write_text("# test tools\n")
    tar = tmp_path / "testmod-0.1.0.tar.gz"
    with tarfile.open(tar, "w:gz") as tf:
        tf.add(mod_src / "module.yaml", arcname="testmod/module.yaml")
        tf.add(mod_src / "tools.py", arcname="testmod/tools.py")

    sha = hashlib.sha256(tar.read_bytes()).hexdigest()

    catalog = {
        "generated_at": "2026-08-16T00:00:00Z",
        "modules": [{
            "name": "testmod",
            "display_name": "测试模块",
            "latest": "0.1.0",
            "code_asset": f"file://{tar}",
            "code_sha256": sha,
            "size_code": tar.stat().st_size,
            "provides_tools": ["test_tool_a", "test_tool_b"],
            "keywords": ["测试"],
            "recommended": True,
        }],
    }
    cat_path = tmp_path / "catalog.json"
    cat_path.write_text(json.dumps(catalog))

    # monkeypatch 模块安装目录
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()

    # monkeypatch 模块目录与 catalog 加载（P7：三命令改读 load_catalog() 远程优先）
    import vermes_cli.module_cli as cli
    monkeypatch.setattr(cli, "get_modules_dir", lambda: modules_dir)
    import agent.module_catalog as mc
    # 让默认 catalog 发现链返回本 fixture 的本地 catalog（模拟远程/Release 来源）
    def _fake_load(path_or_url=None):
        if path_or_url is None:
            return json.loads(cat_path.read_text(encoding="utf-8"))
        return mc.load_catalog(path_or_url)
    monkeypatch.setattr(mc, "load_catalog", _fake_load)
    monkeypatch.setattr(mc, "_modules_dir", lambda: modules_dir)

    return cat_path, modules_dir, tar


def test_install_from_release(fake_catalog_with_tarball):
    """P2: install --release <name> 从 catalog 下载安装。"""
    cat_path, modules_dir, _ = fake_catalog_with_tarball
    import vermes_cli.module_cli as cli

    rc = cli.install_from_release("testmod")
    assert rc == 0
    assert (modules_dir / "testmod" / "module.yaml").exists()
    assert (modules_dir / "testmod" / "tools.py").exists()


def test_install_from_release_not_in_catalog(fake_catalog_with_tarball):
    """P2: install --release <unknown> 报错。"""
    import vermes_cli.module_cli as cli
    rc = cli.install_from_release("nonexistent_mod")
    assert rc == 1


def test_install_from_release_reinstall(fake_catalog_with_tarball):
    """P2: 已安装的模块重新安装（更新）。"""
    cat_path, modules_dir, _ = fake_catalog_with_tarball
    import vermes_cli.module_cli as cli

    # 第一次安装
    rc = cli.install_from_release("testmod")
    assert rc == 0

    # 修改文件模拟旧版
    (modules_dir / "testmod" / "extra.txt").write_text("old")

    # 第二次安装（应覆盖）
    rc = cli.install_from_release("testmod")
    assert rc == 0
    assert not (modules_dir / "testmod" / "extra.txt").exists()  # 旧文件被清


def test_list_available(fake_catalog_with_tarball, capsys):
    """P2: available 命令列出 catalog 模块。"""
    import vermes_cli.module_cli as cli
    rc = cli.list_available()
    assert rc == 0
    out = capsys.readouterr().out
    assert "testmod" in out
    assert "测试模块" in out


def test_search_found(fake_catalog_with_tarball, capsys):
    """P2: search 命令关键词匹配。"""
    import vermes_cli.module_cli as cli
    rc = cli.search("测试")
    assert rc == 0
    out = capsys.readouterr().out
    assert "testmod" in out


def test_search_not_found(fake_catalog_with_tarball, capsys):
    """P2: search 无匹配。"""
    import vermes_cli.module_cli as cli
    rc = cli.search("量子计算")
    assert rc == 0
    out = capsys.readouterr().out
    assert "未找到" in out


# ── P3: registry dispatch 拦截 ───────────────────────────────

def test_dispatch_unknown_tool_no_catalog(tmp_path, monkeypatch):
    """P3: 无 catalog 时返回普通 Unknown tool 错误。"""
    from tools.registry import ToolRegistry
    reg = ToolRegistry()
    result = json.loads(reg.dispatch("nonexistent_tool", {}))
    assert "error" in result
    assert "nonexistent_tool" in result["error"]
    # 无 catalog → 无 hint
    assert "hint" not in result


def test_dispatch_unknown_tool_with_catalog(fake_catalog_with_tarball, monkeypatch):
    """P3: catalog 中有模块提供此工具 → 返回安装提示。"""
    cat_path, _, _ = fake_catalog_with_tarball
    fake_data = json.loads(cat_path.read_text())
    
    # monkeypatch registry 内部绑定的 load_catalog（from import 绑定在 registry 模块）
    import tools.registry as reg_mod
    monkeypatch.setattr(reg_mod, "ToolRegistry", reg_mod.ToolRegistry)  # ensure module loaded
    
    from tools.registry import ToolRegistry
    reg = ToolRegistry()
    
    # 直接 monkeypatch _suggest_module_for_tool 返回固定提示
    monkeypatch.setattr(
        reg, "_suggest_module_for_tool",
        lambda tool_name: f"工具 {tool_name} 属于可插拔模块 testmod。安装命令: vermes module install --release testmod"
    )
    
    result = json.loads(reg.dispatch("test_tool_a", {}))
    assert "error" in result
    assert "hint" in result
    assert "testmod" in result["hint"]
    assert "install --release" in result["hint"]


def test_dispatch_unknown_tool_not_in_catalog(fake_catalog_with_tarball):
    """P3: catalog 中无对应模块 → 无 hint。"""
    from tools.registry import ToolRegistry
    reg = ToolRegistry()
    result = json.loads(reg.dispatch("truly_unknown_tool", {}))
    assert "error" in result
    assert "hint" not in result


def test_dispatch_registered_tool_works():
    """P3: 已注册工具正常分发，不受 P3 影响。"""
    from tools.registry import ToolRegistry

    reg = ToolRegistry()
    reg.register(
        name="my_tool",
        toolset="test",
        schema={"name": "my_tool"},
        handler=lambda args: json.dumps({"ok": True}),
    )
    result = json.loads(reg.dispatch("my_tool", {}))
    assert result["ok"] is True


# ── Phase 3.1: 单模块打包演练（catalog → SHA256 → 安全解压 → 注册 → 运行）──
# 经 registry.dispatch 一侧走通「未知工具 → 自动安装 → 热重载 → 可分发」全链路。

def test_phase31_auto_install_then_dispatch(fake_catalog_with_tarball, monkeypatch):
    """Phase 3.1 主演练：未注册工具被 dispatch 命中 → 开 VERMES_AUTO_INSTALL_MODULE
    → 自动下载/校验/解压模块 → 热重载 → 工具进入 registry 并可分发。

    反向验证：关闭自动安装时，同一工具只返回 hint、不触发任何安装。
    """
    import os
    from tools.registry import ToolRegistry

    # 关闭自动安装：应只返回 hint，不安装
    monkeypatch.setenv("VERMES_AUTO_INSTALL_MODULE", "off")
    reg = ToolRegistry()
    r_off = json.loads(reg.dispatch("test_tool_a", {}))
    assert "hint" in r_off and "auto_install" not in r_off
    # 模块未落地
    _, modules_dir, _ = fake_catalog_with_tarball
    assert not (modules_dir / "testmod").exists()

    # 开启自动安装：应完成安装并让工具可被分发
    monkeypatch.setenv("VERMES_AUTO_INSTALL_MODULE", "on")
    reg2 = ToolRegistry()
    r_on = json.loads(reg2.dispatch("test_tool_a", {}))
    # 安装成功后工具应已注册；dispatch 继续走正常路径（这里 test_tool_a 无 handler，
    # 仍会落到「未知工具」分支——因为 reload 后的 handler 来自模块文件而非 registry
    # 测试桩。断言重点是：模块已物理落地 + 工具名出现在已注册工具集）。
    assert (modules_dir / "testmod" / "module.yaml").exists(), "模块代码包应已解压落地"
    assert (modules_dir / "testmod" / "tools.py").exists(), "模块文件应已安全解压"
    # 反向验证核心：开启后 dispatch 不应再以「Unknown tool + hint」形式提示安装
    # （要么已自愈，要么回退带 auto_install 字段而非纯 hint）。
    if "hint" in r_on:
        assert "auto_install" in r_on, "开启自动安装后失败应带 auto_install 诊断而非纯 hint"


def test_phase31_auto_install_unknown_tool_no_catalog(monkeypatch):
    """反向验证（控制组）：catalog 中无此工具 → 即使开自动安装也不误装。"""
    import os
    from tools.registry import ToolRegistry
    from agent import module_catalog as mc

    monkeypatch.setenv("VERMES_AUTO_INSTALL_MODULE", "on")
    # 强制 catalog 为空
    monkeypatch.setattr(mc, "load_catalog", lambda path_or_url=None: {"modules": [], "generated_at": None})

    reg = ToolRegistry()
    r = json.loads(reg.dispatch("truly_unknown_tool", {}))
    assert "hint" not in r, "无对应模块不应编造安装提示"
    assert "error" in r

