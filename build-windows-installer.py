"""
Vermes Windows 构建脚本 — 在 A11 上运行
流程: git pull → (可选前端构建) → PyInstaller COLLECT → Inno Setup 安装包

用法 (在 A11 PowerShell 中):
  cd C:\Projects\vermes-src
  python build-windows-installer.py
"""
import subprocess
import sys
import os
import shutil
import io

# 强制 UTF-8 输出，避免 Windows GBK 终端打印 emoji 崩溃
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# 允许跳过 npm 构建（web_dist 已 commit 进仓库时使用：VERMES_SKIP_NPM=1）
SKIP_NPM = os.environ.get("VERMES_SKIP_NPM") == "1"
import time

VERMES_DIR = os.path.dirname(os.path.abspath(__file__))
DIST_DIR = os.path.join(VERMES_DIR, "dist", "Vermes")
OUTPUT_DIR = os.path.join(VERMES_DIR, "installer-output")
ISS_FILE = os.path.join(VERMES_DIR, "packaging", "vermes-inno-setup.iss")

# Inno Setup 路径（默认安装位置）
INNO_COMPILER = r"C:\Program Files (x86)\Inno Setup 6\Compil32.exe"
INNO_COMPILER_CLI = r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"


def run(cmd, cwd=None):
    """运行命令并打印输出"""
    print(f"  > {cmd}")
    r = subprocess.run(cmd, shell=True, cwd=cwd or VERMES_DIR,
                       capture_output=True, text=True)
    if r.stdout:
        print(r.stdout[-500:])
    if r.returncode != 0:
        print(f"  ❌ FAILED (exit {r.returncode})")
        if r.stderr:
            print(r.stderr[-500:])
        return False
    return True


def step(n, msg):
    print(f"\n{'='*60}")
    print(f"  Step {n}: {msg}")
    print(f"{'='*60}")


def main():
    # ── Step 1: 验证 import ──
    step(1, "验证 Python 环境和 import 链")
    if not run(f'"{sys.executable}" -c "from vermes_cli.web_server import app; print(\'IMPORT OK\')"'):
        print("\n❌ Import 失败！先修复依赖再构建。")
        print("   pip install -e .")
        sys.exit(1)

    # ── Step 2: 构建前端 ──
    step(2, "构建前端 (npm run build)")
    frontend_dir = os.path.join(VERMES_DIR, "frontend")
    if SKIP_NPM:
        print("  跳过 npm run build (VERMES_SKIP_NPM=1)")
    elif os.path.exists(os.path.join(frontend_dir, "package.json")):
        if not run("npm run build", cwd=frontend_dir):
            print("  警告: npm run build 失败，将使用仓库中已有的 web_dist")
    else:
        print("  跳过（无 frontend 目录）")

    # 同步 web_dist（仅在真正跑 npm build 时执行；
    # SKIP_NPM=1 时仓库中的 web_dist 已是权威源，不碰）
    src_dist = os.path.join(frontend_dir, "dist") if os.path.exists(frontend_dir) else None
    dst_dist = os.path.join(VERMES_DIR, "vermes_cli", "web_dist")
    if SKIP_NPM:
        print("  VERMES_SKIP_NPM=1: 跳过 web_dist 同步（使用仓库内的 web_dist）")
    elif src_dist and os.path.exists(src_dist):
        if os.path.exists(dst_dist):
            # 保留 modules/ 目录（ScholarForge 前端是独立构建、存进仓库的，
            # 不在主前端 npm run build 流程里，rmtree 会误删）
            modules_dir = os.path.join(dst_dist, "modules")
            tmp_modules = None
            if os.path.exists(modules_dir):
                import tempfile
                tmp_modules = tempfile.mkdtemp(prefix="sf_modules_")
                shutil.copytree(modules_dir, tmp_modules, dirs_exist_ok=True)
                print(f"  暂存 web_dist/modules/ 至 {tmp_modules}")
            shutil.rmtree(dst_dist)
            shutil.copytree(src_dist, dst_dist)
            if tmp_modules:
                shutil.copytree(tmp_modules, modules_dir, dirs_exist_ok=True)
                shutil.rmtree(tmp_modules, ignore_errors=True)
                print(f"  恢复 web_dist/modules/")
        else:
            shutil.copytree(src_dist, dst_dist)
        print(f"  ✅ web_dist 已同步 ({len(os.listdir(dst_dist))} files)")

    # ── Step 3: PyInstaller COLLECT ──
    step(3, "PyInstaller COLLECT 构建")
    # 清理旧构建
    for d in ["build", "dist"]:
        p = os.path.join(VERMES_DIR, d)
        if os.path.exists(p):
            shutil.rmtree(p)
            print(f"  清理 {d}/")

    if not run(f'"{sys.executable}" -m PyInstaller vermes-gui.spec --noconfirm'):
        print("\n❌ PyInstaller 构建失败！")
        sys.exit(1)

    # 验证输出
    exe_path = os.path.join(DIST_DIR, "Vermes.exe")
    if not os.path.exists(exe_path):
        print(f"\n❌ Vermes.exe 不存在: {exe_path}")
        sys.exit(1)

    exe_size = os.path.getsize(exe_path) / 1024 / 1024
    total_size = sum(
        os.path.getsize(os.path.join(dp, f))
        for dp, dn, fn in os.walk(DIST_DIR)
        for f in fn
    ) / 1024 / 1024
    print(f"  ✅ Vermes.exe: {exe_size:.1f} MB")
    print(f"  ✅ COLLECT 总大小: {total_size:.1f} MB")

    # ── Step 4: 注入 VC++ DLL ──
    step(4, "注入 VC++ 运行时 DLL")
    vc_dlls = [
        "vcruntime140.dll", "vcruntime140_1.dll",
        "msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll",
        "vcruntime140_threads.dll",
    ]
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    vc_src = os.path.join(system_root, "System32")
    injected = 0
    for dll in vc_dlls:
        src = os.path.join(vc_src, dll)
        if os.path.exists(src):
            # 放到 COLLECT 根目录
            dst1 = os.path.join(DIST_DIR, dll)
            shutil.copy2(src, dst1)
            # 放到 _internal
            dst2 = os.path.join(DIST_DIR, "_internal", dll)
            os.makedirs(os.path.dirname(dst2), exist_ok=True)
            shutil.copy2(src, dst2)
            injected += 1
        else:
            print(f"  ⚠️ {dll} 未找到（系统可能已安装 VC++ 运行时）")
    print(f"  ✅ 注入 {injected}/{len(vc_dlls)} 个 DLL（根目录 + _internal 双份）")

    # ── Step 5: Inno Setup 打包 ──
    step(5, "Inno Setup 打包安装程序")
    if not os.path.exists(INNO_COMPILER_CLI):
        print(f"  ❌ Inno Setup 未安装！")
        print(f"  下载: https://jrsoftware.org/isdl.php")
        print(f"  安装后重试。")
        # 降级为 ZIP
        print("\n  降级: 打包为 ZIP...")
        zip_path = os.path.join(VERMES_DIR, "Vermes-Windows-x64.zip")
        import zipfile
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_STORED) as zf:
            for root, dirs, files in os.walk(DIST_DIR):
                for f in files:
                    fpath = os.path.join(root, f)
                    arcname = os.path.relpath(fpath, DIST_DIR)
                    zf.write(fpath, arcname)
        zip_size = os.path.getsize(zip_path) / 1024 / 1024
        print(f"  ✅ ZIP: {zip_path} ({zip_size:.1f} MB)")
        return

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    if not run(f'"{INNO_COMPILER_CLI}" "{ISS_FILE}"'):
        print("\n❌ Inno Setup 编译失败！")
        sys.exit(1)

    # 验证输出
    installer = os.path.join(OUTPUT_DIR, "Vermes-Setup-x64.exe")
    if os.path.exists(installer):
        size = os.path.getsize(installer) / 1024 / 1024
        print(f"\n{'='*60}")
        print(f"  ✅ 构建成功！")
        print(f"  安装包: {installer}")
        print(f"  大小: {size:.1f} MB")
        print(f"{'='*60}")
    else:
        print(f"\n❌ 安装包未生成: {installer}")
        sys.exit(1)


if __name__ == "__main__":
    main()
