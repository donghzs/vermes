#!/usr/bin/env python3
"""打包 FreeCAD 引擎为 Release tar.gz（M1-6 真机发布用）。

前置：用户机器已安装 FreeCAD 1.0（freecadcmd 可跑）。本脚本不发布、不推送，
只把本机 FreeCAD 打成可分发 tarball 并印出 catalog 资产块，供 M1-6 上传 Release +
`python3 scripts/build_modules.py --push-catalog` 把真实 url/sha256/size 写进 P7 catalog。

产出的 tarball 顶层布局（经 safe_extract 解压到 ~/.vermes/engines/freecad）：
  freecadcmd            # 符号链接 → freecadcmd_app/.../freecadcmd（_find_freecadcmd 命中）
  freecadcmd_app/...    # 完整 FreeCAD 应用（macOS FreeCAD.app / Linux 安装树）

用法:
  python3 vermes-mod-freecad-engine/build_engine_asset.py [--freecadcmd /path/to/freecadcmd] [--version 0.1.0]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tarfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DIST = REPO_ROOT / "dist-modules"
MODULE_DIR = Path(__file__).resolve().parent


def _read_version() -> str:
    try:
        import re

        text = (MODULE_DIR / "module.yaml").read_text(encoding="utf-8")
        m = re.search(r"^version:\s*([0-9][\w.]*)\s*$", text, re.MULTILINE)
        if m:
            return m.group(1)
    except Exception:
        pass
    return "0.1.0"


def locate_freecadcmd(explicit: str | None) -> Path:
    """多策略定位 freecadcmd。"""
    if explicit:
        p = Path(explicit)
        if p.exists():
            return p.resolve()
        raise SystemExit(f"[build_engine_asset] 指定路径不存在: {explicit}")
    # 1) VERMES_FREECAD_ENGINE_DIR
    env = os.environ.get("VERMES_FREECAD_ENGINE_DIR")
    if env and (Path(env) / "freecadcmd").exists():
        return (Path(env) / "freecadcmd").resolve()
    # 2) PATH
    on_path = shutil.which("freecadcmd")
    if on_path:
        return Path(on_path).resolve()
    # 3) macOS 常见位置
    candidates = [
        "/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd",
        "/opt/homebrew/opt/freecad/libexec/bin/freecadcmd",
        "/usr/bin/freecadcmd",
        "/usr/lib/freecad/bin/freecadcmd",
        "/opt/freecad/bin/freecadcmd",
    ]
    for c in candidates:
        if Path(c).exists():
            return Path(c).resolve()
    raise SystemExit(
        "[build_engine_asset] 未找到 freecadcmd。请先安装 FreeCAD 1.0，"
        "或用 --freecadcmd 指定其路径。"
    )


def find_app_root(freecadcmd: Path) -> Path:
    """从 freecadcmd 反推 FreeCAD 应用根目录（含运行时依赖）。"""
    # 跟随符号链接到真实文件
    real = freecadcmd.resolve()
    p = real
    # macOS: .../FreeCAD.app/Contents/Resources/bin/freecadcmd → 根 = FreeCAD.app
    # Linux: .../freecad/libexec/bin/freecadcmd 或 .../bin/freecadcmd → 根 = 含 bin/lib 的树
    if "FreeCAD.app" in p.parts:
        idx = p.parts.index("FreeCAD.app")
        return Path(*p.parts[: idx + 1])
    # 向上找含 'bin' 且同级有 'lib' 的目录作为根
    cur = p.parent
    for _ in range(6):
        if (cur / "bin").is_dir() and (cur / "lib").is_dir():
            return cur
        if (cur / "libexec").is_dir() and (cur / "bin").is_dir():
            return cur
        if cur.parent == cur:
            break
        cur = cur.parent
    # 兜底：取 freecadcmd 的上两级（.../bin/freecadcmd → ...）
    return real.parent.parent


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="打包 FreeCAD 引擎为 Release tarball（M1-6 发布用）")
    ap.add_argument("--freecadcmd", default=None, help="freecadcmd 显式路径")
    ap.add_argument("--version", default=None, help="版本（默认读 module.yaml）")
    ap.add_argument("--output", default=None, help="输出 tar.gz 路径（默认 dist-modules/）")
    args = ap.parse_args(argv)

    version = args.version or _read_version()
    freecadcmd = locate_freecadcmd(args.freecadcmd)
    app_root = find_app_root(freecadcmd)
    print(f"📍 freecadcmd: {freecadcmd}")
    print(f"📦 应用根目录: {app_root}")

    rel = freecadcmd.resolve().relative_to(app_root.resolve())
    link_target = f"freecadcmd_app/{rel.as_posix()}"

    DIST.mkdir(parents=True, exist_ok=True)
    tar_name = args.output or str(DIST / f"vermes-mod-freecad-engine-{version}.tar.gz")
    tar_path = Path(tar_name)

    print(f"🗜️  打包 → {tar_path} ...")
    with tarfile.open(tar_path, "w:gz") as tf:
        # 1) 应用根目录 → 顶层 freecadcmd_app/
        tf.add(app_root, arcname="freecadcmd_app", recursive=True)
        # 2) 顶层符号链接 freecadcmd → freecadcmd_app/.../freecadcmd
        ti = tarfile.TarInfo(name="freecadcmd")
        ti.type = tarfile.SYMTYPE
        ti.linkname = link_target
        ti.mode = 0o755
        tf.addfile(ti)

    size = tar_path.stat().st_size
    sha = sha256_file(tar_path)
    url = f"https://github.com/donghzs/vermes-mod-freecad-engine/releases/download/v{version}/{tar_path.name}"

    print(f"✅ {tar_path.name} ({size // 1024}KB, sha256={sha})")
    snippet = {
        "id": "freecadcmd",
        "label": "FreeCAD 引擎（freecadcmd）",
        "target": "engines/freecad",
        "url": url,
        "sha256": sha,
        "size": size,
        "release_asset": tar_path.name,
        "optional": False,
    }
    print("\n📋 把以下资产块粘进 catalog.json 的 vermes-mod-freecad-engine.assets：")
    print(json.dumps(snippet, indent=2, ensure_ascii=False))
    print("\n➡️  下一步：上传本 tarball 到 GitHub Release v%s，再运行 "
          "`python3 scripts/build_modules.py --push-catalog` 把真实值写进 P7 catalog。" % version)
    return 0


if __name__ == "__main__":
    sys.exit(main())
