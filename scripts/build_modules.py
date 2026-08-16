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
        "version": "0.2.0",
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
    """打包单个模块为 tar.gz，返回 catalog 条目。"""
    name = mod["name"]
    version = mod["version"]
    src = ROOT / mod["source_dir"]

    # 读取 module.yaml 的 provides_tools 和 keywords
    yaml_path = src / "module.yaml"
    provides_tools = read_yaml_field(yaml_path, "provides_tools", []) or []
    keywords = read_yaml_field(yaml_path, "keywords", []) or []

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
        "assets": [],  # 重资产（引擎/权重）后续 P6 填
        "provides_tools": provides_tools,
        "keywords": keywords,
        "repository": repo,
        "homepage": mod["homepage"],
        "description": mod["description"],
        "recommended": mod.get("recommended", False),
    }


def main():
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
    print(f"   宿主缓存: {modules_dir / 'catalog.json'}")


if __name__ == "__main__":
    main()
