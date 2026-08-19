"""M1-5：vermes-mod-freecad-engine 已注册到 P7 bundled catalog，资产契约与 M1-4 对齐。

不联网、不依赖 FreeCAD：只校验 catalog.json 的结构契约——
模块名 / 资产 id / 资产 target 必须能让 engine_setup.ensure_freecad_ready 的
_default_freecad_installer（install_module_asset）正确解析到 ~/.vermes/engines/freecad/freecadcmd。
"""
from __future__ import annotations

from pathlib import Path

import json

CATALOG = Path(__file__).resolve().parents[2] / "vermes_cli" / "modules" / "catalog.json"

EXPECTED_MODULE = "vermes-mod-freecad-engine"
EXPECTED_ASSET_ID = "freecadcmd"
EXPECTED_TARGET = "engines/freecad"


def _load():
    return json.loads(CATALOG.read_text(encoding="utf-8"))


def test_catalog_file_present():
    assert CATALOG.exists()


def test_freecad_engine_module_registered():
    mods = {m["name"]: m for m in _load()["modules"]}
    assert EXPECTED_MODULE in mods


def test_freecad_engine_asset_contract():
    mod = next(m for m in _load()["modules"] if m["name"] == EXPECTED_MODULE)
    assets = {a["id"]: a for a in mod.get("assets", [])}
    assert EXPECTED_ASSET_ID in assets
    asset = assets[EXPECTED_ASSET_ID]
    # target 相对 ~/.vermes/ → ~/.vermes/engines/freecad，与 get_freecad_engine_dir 默认一致
    assert asset.get("target") == EXPECTED_TARGET
    # 资产尚未发布时为 pending（url/sha256 空，M1-6 构建后填充）；契约字段必须齐
    assert "release_asset" in asset
    assert asset["id"] == EXPECTED_ASSET_ID


def test_freecad_engine_is_asset_only_module():
    mod = next(m for m in _load()["modules"] if m["name"] == EXPECTED_MODULE)
    # 重资产模块：无 Python 代码包、不提供工具
    assert mod.get("provides_tools") == []
    assert mod.get("code_asset", "") == ""
