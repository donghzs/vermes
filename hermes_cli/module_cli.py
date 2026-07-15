"""
Vermes 生态模块管理 CLI

Usage:
    python3 -m hermes_cli.module_cli install <path-or-url>
    python3 -m hermes_cli.module_cli uninstall <name>
    python3 -m hermes_cli list
    python3 -m hermes_cli info <name>
"""

import json
import os
import shutil
import sys
from pathlib import Path

# 确保项目根目录在 sys.path
_PROJECT_ROOT = Path(__file__).parent.parent.resolve()
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from agent.module_loader import get_modules_dir, discover_modules, parse_manifest

# ── Colors ──
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RESET = "\033[0m"
BOLD = "\033[1m"


def _ok(msg):
    print(f"{GREEN}✅{RESET} {msg}")

def _err(msg):
    print(f"{RED}❌{RESET} {msg}")

def _info(msg):
    print(f"{CYAN}ℹ️{RESET} {msg}")

def _warn(msg):
    print(f"{YELLOW}⚠️{RESET} {msg}")


def install(source: str):
    """安装生态模块

    Args:
        source: 模块目录路径（本地）或 Git 仓库 URL
    """
    source = os.path.expanduser(source)

    # 1. 判断来源类型
    is_git = source.startswith("http") or source.startswith("git@")
    is_local = Path(source).is_dir()

    if not is_git and not is_local:
        _err(f"模块来源无效: {source}")
        _info("支持本地目录路径或 Git URL")
        return 1

    # 2. 准备临时目录
    import tempfile
    tmpdir = tempfile.mkdtemp(prefix="vermes-module-")

    try:
        if is_git:
            _info(f"克隆模块仓库: {source}")
            import subprocess
            result = subprocess.run(
                ["git", "clone", "--depth", "1", source, tmpdir],
                capture_output=True, text=True
            )
            if result.returncode != 0:
                _err(f"Git 克隆失败: {result.stderr}")
                return 1
            module_src = tmpdir
        else:
            module_src = source

        # 3. 检查 module.yaml
        manifest_path = Path(module_src) / "module.yaml"
        if not manifest_path.exists():
            _err(f"模块清单文件不存在: {manifest_path}")
            _info("模块目录必须包含 module.yaml 文件")
            return 1

        # 4. 解析清单
        try:
            import yaml
            with open(manifest_path) as f:
                manifest = yaml.safe_load(f)
        except ImportError:
            # PyYAML 不可用时用简单的 JSON 解析
            _warn("PyYAML 未安装，尝试 JSON 格式...")
            try:
                with open(manifest_path) as f:
                    manifest = json.load(f)
            except Exception:
                _err("无法解析 module.yaml（需要 PyYAML 或 JSON 格式）")
                return 1
        except Exception as e:
            _err(f"解析 module.yaml 失败: {e}")
            return 1

        name = manifest.get("name")
        if not name:
            _err("module.yaml 缺少 name 字段")
            return 1

        version = manifest.get("version", "0.0.0")
        display_name = manifest.get("display_name", name)

        # 5. 检查是否已安装
        target_dir = get_modules_dir() / name
        if target_dir.exists():
            _warn(f"模块 {BOLD}{name}{RESET} 已安装，正在更新...")
            shutil.rmtree(target_dir)

        # 6. 创建目标目录
        get_modules_dir().mkdir(parents=True, exist_ok=True)
        target_dir.mkdir()

        # 7. 复制文件（排除 .git）
        for item in Path(module_src).iterdir():
            if item.name == ".git":
                continue
            dest = target_dir / item.name
            if item.is_dir():
                shutil.copytree(item, dest)
            else:
                shutil.copy2(item, dest)

        # 8. 验证安装
        _ok(f"模块已安装: {display_name} v{version}")
        _info(f"位置: {target_dir}")

        # 9. 检查前端是否已构建
        frontend_entry = manifest.get("frontend_entry", "")
        if frontend_entry:
            entry_path = target_dir / frontend_entry
            if entry_path.exists():
                size_kb = entry_path.stat().st_size / 1024
                _ok(f"前端已构建: {frontend_entry} ({size_kb:.0f}KB)")
            else:
                _warn(f"前端未构建: {frontend_entry}")
                _info("模块前端需要构建才能使用，请参考模块文档")

        # 10. 尝试加载验证
        try:
            loaded = parse_manifest(target_dir)
            if loaded:
                _ok(f"模块验证通过: {loaded.name}")
                _info("重启 Vermes 后生效")
            else:
                _warn("模块验证失败，请检查 module.yaml 格式")
        except Exception as e:
            _warn(f"模块验证出错: {e}")

        return 0

    finally:
        # 清理临时目录
        if is_git and Path(tmpdir).exists():
            shutil.rmtree(tmpdir, ignore_errors=True)


def uninstall(name: str):
    """卸载生态模块

    Args:
        name: 模块名称
    """
    target = get_modules_dir() / name
    if not target.exists():
        _err(f"模块未安装: {name}")
        _info(f"已安装模块: {[d.name for d in get_modules_dir().iterdir()] if get_modules_dir().exists() else '(无)'}")
        return 1

    # 解析清单获取 display_name
    display_name = name
    manifest_path = target / "module.yaml"
    if manifest_path.exists():
        try:
            import yaml
            with open(manifest_path) as f:
                m = yaml.safe_load(f)
                display_name = m.get("display_name", name)
        except Exception:
            pass

    shutil.rmtree(target)
    _ok(f"模块已卸载: {display_name}")
    _info("重启 Vermes 后生效")
    return 0


def list_installed():
    """列出已安装的生态模块"""
    modules = discover_modules()

    if not modules:
        _info("未安装任何生态模块")
        _info(f"模块安装目录: {get_modules_dir()}")
        _info("使用 'vermes module install <path>' 安装模块")
        return 0

    print(f"\n{BOLD}已安装的生态模块{RESET}")
    print(f"{'─' * 60}")
    for m in modules:
        print(f"  {CYAN}{m.name}{RESET} v{m.version}")
        print(f"    {m.display_name}")
        if m.description:
            print(f"    {m.description}")
        print(f"    路径: {get_modules_dir() / m.name}")
        print()
    return 0


def info(name: str):
    """显示模块详细信息

    Args:
        name: 模块名称
    """
    modules = discover_modules()
    m = next((x for x in modules if x.name == name), None)

    if not m:
        _err(f"模块未安装: {name}")
        return 1

    print(f"\n{BOLD}{m.display_name}{RESET} v{m.version}")
    print(f"{'─' * 40}")
    print(f"  name:           {m.name}")
    print(f"  version:        {m.version}")
    print(f"  display_name:   {m.display_name}")
    if m.description:
        print(f"  description:    {m.description}")
    if m.frontend_entry:
        print(f"  frontend_entry: {m.frontend_entry}")
    if m.frontend_route:
        print(f"  frontend_route: {m.frontend_route}")
    if m.frontend_icon:
        print(f"  frontend_icon:  {m.frontend_icon}")
    if m.frontend_menu_title:
        print(f"  menu_title:     {m.frontend_menu_title}")
    if m.permissions:
        print(f"  permissions:    {', '.join(m.permissions)}")
    print(f"  path:           {get_modules_dir() / m.name}")

    # 检查文件
    target = get_modules_dir() / name
    print(f"\n{BOLD}文件结构{RESET}")
    for item in sorted(target.rglob("*")):
        if item.is_file() and ".git" not in str(item):
            rel = item.relative_to(target)
            size = item.stat().st_size
            if size > 1024:
                print(f"  {rel}  ({size/1024:.0f}KB)")
            else:
                print(f"  {rel}  ({size}B)")
    return 0


def main():
    """CLI 入口"""
    args = sys.argv[1:]

    if not args or args[0] in ("-h", "--help", "help"):
        print(f"""
{BOLD}Vermes 生态模块管理{RESET}

{BOLD}用法:{RESET}
  python3 -m hermes_cli.module_cli <command> [args]

{BOLD}命令:{RESET}
  {CYAN}install{RESET} <path-or-url>   安装模块（本地目录或 Git URL）
  {CYAN}uninstall{RESET} <name>        卸载模块
  {CYAN}list{RESET}                   列出已安装模块
  {CYAN}info{RESET} <name>            显示模块详情

{BOLD}示例:{RESET}
  # 从本地目录安装
  python3 -m hermes_cli.module_cli install ~/Projects/vermes-scholarforge

  # 从 Git 安装
  python3 -m hermes_cli.module_cli install https://github.com/donghzs/vermes-scholarforge

  # 列出已安装
  python3 -m hermes_cli.module_cli list

  # 卸载
  python3 -m hermes_cli.module_cli uninstall scholarforge

{BOLD}模块目录:{RESET} {get_modules_dir()}
""")
        return 0

    cmd = args[0]
    rest = args[1:]

    if cmd == "install" and rest:
        return install(rest[0])
    elif cmd == "uninstall" and rest:
        return uninstall(rest[0])
    elif cmd == "list":
        return list_installed()
    elif cmd == "info" and rest:
        return info(rest[0])
    else:
        _err(f"未知命令: {cmd}")
        print("使用 --help 查看用法")
        return 1


if __name__ == "__main__":
    sys.exit(main())
