#!/usr/bin/env python3
"""打包 mfgcad/scholarforge 模块为 Release tar.gz + 生成 catalog.json。

用法:
    python scripts/build_modules.py

产物:
    dist-modules/mfgcad-<version>.tar.gz
    dist-modules/scholarforge-<version>.tar.gz
    dist-modules/catalog.json
"""
import hashlib
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist-modules"

MODULES = [
    {
        "name": "mfgcad",
        "display_name": "制造 CAD（3D 建模）",
        "source_dir": "vermes_cli/mfgcad",
        "version": "0.3.0",
        "vermes_min": "2.3.9",
        "repository": "donghzs/vermes-mod-mfgcad",
        "homepage": "https://github.com/donghzs/vermes-mod-mfgcad",
        "description": "自然语言→STEP 三维模型，参数化建模+制造链路",
        "recommended": True,
    },
    {
        "name": "scholarforge",
        "display_name": "ScholarForge 论文写作",
        "source_dir": "vermes_cli/scholarforge",
        "version": "1.0.0",
        "vermes_min": "2.3.9",
        "repository": "donghzs/vermes-mod-scholarforge",
        "homepage": "https://github.com/donghzs/vermes-mod-scholarforge",
        "description": "AI 驱动的学术论文写作全链路",
        "recommended": True,
    },
    {
        "name": "vermes-mod-freecad-engine",
        "display_name": "FreeCAD 引擎（专业精修后端）",
        "source_dir": "vermes-mod-freecad-engine",
        "version": "0.1.0",
        "vermes_min": "2.3.9",
        "repository": "donghzs/vermes-mod-freecad-engine",
        "homepage": "https://github.com/donghzs/vermes-mod-freecad-engine",
        "description": "ProToolAdapter 的 FreeCAD 后端引擎（headless freecadcmd），承载 STEP→Body / 编辑翻译 / 特征树提取",
        "asset_only": True,  # 不含 Python 代码包，仅经 P7 catalog 分发 FreeCAD tarball（M1-5）
        "recommended": False,
    },
]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_yaml_field(yaml_path: Path, field: str, default=None):
    """从 module.yaml 读取字段（简易解析，不依赖 PyYAML）。"""
    try:
        import yaml
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        return data.get(field, default)
    except Exception:
        return default


def pack_module(mod: dict) -> dict:
    """打包单个模块为 tar.gz，返回 catalog 条目。

    asset_only=True 的模块（如 vermes-mod-freecad-engine）不含 Python 代码包，
    仅经 P7 catalog 分发重资产（FreeCAD tarball）；此时跳过代码 tarball，
    仅产出含 pending 资产的 catalog 条目（url/sha256/size 由 M1-6 真机构建后回填）。
    """
    name = mod["name"]
    src = ROOT / mod["source_dir"]
    asset_only = bool(mod.get("asset_only", False))

    # module.yaml 为唯一真相源：version / provides_tools / keywords / assets
    yaml_path = src / "module.yaml"
    version = read_yaml_field(yaml_path, "version", mod.get("version")) or mod.get("version")
    provides_tools = read_yaml_field(yaml_path, "provides_tools", []) or []
    keywords = read_yaml_field(yaml_path, "keywords", []) or []
    assets_raw = read_yaml_field(yaml_path, "assets", []) or []
    if not version:
        raise SystemExit(f"[build_modules] {name}: module.yaml 缺少 version 字段")

    # 重资产块：M1-5 注册时 url/sha256/size 留空（pending），M1-6 真机构建后回填
    assets = []
    for a in assets_raw:
        if not isinstance(a, dict) or not a.get("id"):
            continue
        assets.append({
            "id": a["id"],
            "label": a.get("label", a["id"]),
            "target": a.get("target", ""),
            "release_asset": a.get("release_asset", ""),
            "url": "",
            "sha256": "",
            "size": 0,
            "optional": bool(a.get("optional", False)),
        })

    if asset_only:
        # 不打包代码；仅打印注册信息
        print(f"  🔧 {name}-{version}: 重资产模块（asset_only），跳过代码打包，"
              f"{len(assets)} 个 pending 资产待 M1-6 回填")
        code_url = ""
        sha = ""
        size = 0
    else:
        tar_name = f"{name}-{version}.tar.gz"
        tar_path = DIST / tar_name

        # 打包（tar.gz，顶层以模块名开头）
        with tarfile.open(tar_path, "w:gz") as tf:
            for item in src.rglob("*"):
                if item.is_file():
                    # 排除 __pycache__、.pyc、tests
                    if "__pycache__" in item.name or item.suffix == ".pyc":
                        continue
                    if "tests" in item.parts:
                        continue
                    arcname = f"{name}/{item.relative_to(src)}"
                    tf.add(item, arcname=arcname)

        size = tar_path.stat().st_size
        sha = sha256_file(tar_path)

        print(f"  ✅ {name}-{version}: {tar_name} ({size // 1024}KB, sha256={sha[:16]}...)")

        # GitHub Release URL 模板（实际发布后填真实 URL）
        repo = mod["repository"]
        code_url = f"https://github.com/{repo}/releases/download/v{version}/{tar_name}"

    return {
        "name": name,
        "display_name": mod["display_name"],
        "latest": version,
        "vermes_min": mod["vermes_min"],
        "code_asset": code_url,
        "code_sha256": sha,
        "size_code": size,
        "assets": assets,
        "provides_tools": provides_tools,
        "keywords": keywords,
        "repository": mod["repository"],
        "homepage": mod["homepage"],
        "description": mod["description"],
        "recommended": mod.get("recommended", False),
    }


def main(argv=None):
    from argparse import ArgumentParser

    ap = ArgumentParser(description="打包可插拔模块为 Release + 生成 catalog.json（P7）")
    ap.add_argument("--push-catalog", action="store_true",
                    help="打包后同步 catalog.json 到远程官方 catalog 仓（即时触达所有 app 版本）")
    ap.add_argument("--repo", default="donghzs/vermes-modules-catalog", help="远程 catalog 仓 owner/name")
    ap.add_argument("--branch", default="main", help="远程 catalog 仓分支")
    ap.add_argument("--message", default="", help="远程同步 commit message（默认自动生成）")
    args = ap.parse_args(argv)

    DIST.mkdir(parents=True, exist_ok=True)

    print("📦 打包可插拔模块...\n")
    entries = []
    for mod in MODULES:
        entry = pack_module(mod)
        entries.append(entry)

    # 生成 catalog.json
    catalog = {
        "generated_at": subprocess.check_output(
            ["date", "-u", "+%Y-%m-%dT%H:%M:%SZ"]
        ).decode().strip(),
        "modules": entries,
    }
    catalog_path = DIST / "catalog.json"
    catalog_path.write_text(json.dumps(catalog, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n📋 catalog.json → {catalog_path} ({len(entries)} 模块)")
    print(f"   本地预览: file://{catalog_path}")

    # 也复制到主仓 vermes_cli/modules/catalog.json 供宿主缓存
    modules_dir = ROOT / "vermes_cli" / "modules"
    modules_dir.mkdir(exist_ok=True)
    (modules_dir / "catalog.json").write_text(
        json.dumps(catalog, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"   宿主缓存（bundled fallback）: {modules_dir / 'catalog.json'}")

    # P7: 远程 catalog 同步（默认关闭，避免误推送；显式 --push-catalog 才推）
    if getattr(args, "push_catalog", False):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from sync_remote_catalog import sync_catalog
        msg = getattr(args, "message", "") or "release modules: " + ", ".join(
            f"{e['name']} {e['latest']}" for e in entries
        )
        ok = sync_catalog(
            source=modules_dir / "catalog.json",
            repo=getattr(args, "repo", "donghzs/vermes-modules-catalog"),
            branch=getattr(args, "branch", "main"),
            message=msg,
        )
        if not ok:
            print("[error] 远程同步失败，本地 catalog 已生成但未推送", file=sys.stderr)
            return 1
        print("\n✅ 远程 catalog 已更新，所有已发布的 Vermes app 版本将即时拉取新模块清单。")
    else:
        print("\nℹ️  本地 catalog 已生成（未推送远程）。如需即时触达所有 app 版本，运行：")
        print(f"   python3 scripts/sync_remote_catalog.py --source {modules_dir / 'catalog.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
