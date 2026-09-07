"""Load + validate agent recipes from YAML (safe_load, no code execution).

「目录即 agent 食谱」：``recipes/*.yaml`` 每个文件描述一个可经 ACP transport
拉入的异构 agent。loader 负责发现、解析、校验，并对错误给出友好信息
（指明哪个文件、哪个字段、什么错）。
"""

from __future__ import annotations

import yaml
from pathlib import Path
from typing import Any, Iterator

from .schema import RecipeConfig

# recipes 目录 = 本模块同目录（包内数据；PyInstaller 打包后仍可达）。
RECIPES_DIR = Path(__file__).resolve().parent


def _has_unsafe_tag(node: Any) -> bool:
    """Reject YAML that could execute code (defence in depth beyond safe_load)."""
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(k, str) and k.startswith("!!"):
                return True
            if _has_unsafe_tag(v):
                return True
    elif isinstance(node, list):
        for item in node:
            if _has_unsafe_tag(item):
                return True
    elif isinstance(node, str) and node.strip().startswith("!!"):
        return True
    return False


def load_recipe(path: str | Path) -> RecipeConfig:
    """Load a single recipe YAML into a RecipeConfig.

    Raises ValueError with a friendly message on missing file / bad YAML /
    unsafe tags / schema violations.
    """
    p = Path(path)
    if not p.exists():
        raise ValueError(f"{p}: recipe file not found")
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as e:
        raise ValueError(f"{p}: cannot read recipe: {e}") from e

    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ValueError(f"{p}: invalid YAML: {e}") from e

    if _has_unsafe_tag(raw):
        raise ValueError(f"{p}: unsafe YAML tag (e.g. !!python/object) is not allowed")

    return RecipeConfig.from_dict(raw, source=str(p))


def iter_recipe_files(directory: str | Path, recursive: bool = False) -> Iterator[Path]:
    """Yield recipe files in ``directory``.

    ``recursive=False`` (default) only scans the top level — this is what the
    curated recipes use and keeps ``test_load_all_recipes_finds_three`` exact.
    ``recursive=True`` also descends into subdirs (e.g. ``registry/`` auto-dumped
    from the ACP Registry) so generated starters surface alongside curated ones.
    """
    d = Path(directory)
    if not d.is_dir():
        return
    if recursive:
        for path in sorted(d.rglob("*.yaml")):
            if path.is_file():
                yield path
        for path in sorted(d.rglob("*.yml")):
            if path.is_file():
                yield path
    else:
        for path in sorted(d.glob("*.yaml")):
            if path.is_file():
                yield path
        for path in sorted(d.glob("*.yml")):
            if path.is_file():
                yield path


def load_all_recipes(directory: str | Path, recursive: bool = False) -> list[RecipeConfig]:
    """Load every recipe in ``directory`` (skips files that fail validation).

    Pass ``recursive=True`` to also include recipes in subdirectories.
    """
    recipes: list[RecipeConfig] = []
    for path in iter_recipe_files(directory, recursive=recursive):
        try:
            recipes.append(load_recipe(path))
        except ValueError as e:
            # 单条 recipe 坏不影响其他；调用方可用 load_recipe 单独拿详细错误
            continue
    return recipes


def find_recipe(
    name: str, directory: str | Path, recursive: bool = False
) -> RecipeConfig | None:
    """Find a recipe by ``name`` field (exact match).

    ``recursive=False`` (default) only scans the top level (backwards
    compatible with the curated-recipes call sites). Pass ``recursive=True``
    to also search subdirectories (e.g. ``registry/`` auto-dumped recipes) —
    required wherever the full 41-recipe surface is exposed (登堂/dispatch).
    """
    for path in iter_recipe_files(directory, recursive=recursive):
        try:
            rc = load_recipe(path)
        except ValueError:
            continue
        if rc.name == name:
            return rc
    return None
