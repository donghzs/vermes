"""
Vermes 标准件库 — 本地 STEP 目录 + mfg_standard_part 工具。

对标 text-to-cad 的 step.parts：内置常用标准件 STEP 文件，
Agent 可直接引用组装，不用每次从零生成。

目录结构：
  ~/.vermes/mfgcad/parts/
    ├── screws/
    │   ├── M3_screw.step
    │   ├── M4_screw.step
    │   ├── M5_screw.step
    │   └── ...
    ├── nuts/
    │   ├── M3_nut.step
    │   └── ...
    ├── bearings/
    │   ├── 6800.step
    │   └── ...
    └── catalog.json  # 件号 → 文件路径 + 参数描述

首次使用时从 GitHub Release 下载标准件包（通过 P6 assets 分发）。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------

@dataclass
class StandardPart:
    """一个标准件。"""
    id: str                    # 件号，如 "M5_screw"
    name: str                  # 显示名，如 "M5 内六角螺钉"
    category: str              # 分类：screws / nuts / bearings / connectors
    file_path: str             # STEP 文件相对路径（相对 parts/ 目录）
    parameters: Dict[str, float] = field(default_factory=dict)  # 关键尺寸
    description: str = ""
    standard: str = ""         # 标准号，如 "GB/T 70.1-2008"


# ---------------------------------------------------------------------------
# 内置目录（件号清单）
# ---------------------------------------------------------------------------

BUILTIN_CATALOG: List[StandardPart] = [
    # 螺钉
    StandardPart("M3_screw", "M3 内六角螺钉", "screws", "screws/M3_screw.step",
                 {"diameter": 3.0, "length": 10.0, "head_diameter": 5.5},
                 "M3×10 内六角螺钉", "GB/T 70.1-2008"),
    StandardPart("M4_screw", "M4 内六角螺钉", "screws", "screws/M4_screw.step",
                 {"diameter": 4.0, "length": 12.0, "head_diameter": 7.0},
                 "M4×12 内六角螺钉", "GB/T 70.1-2008"),
    StandardPart("M5_screw", "M5 内六角螺钉", "screws", "screws/M5_screw.step",
                 {"diameter": 5.0, "length": 16.0, "head_diameter": 8.5},
                 "M5×16 内六角螺钉", "GB/T 70.1-2008"),
    StandardPart("M6_screw", "M6 内六角螺钉", "screws", "screws/M6_screw.step",
                 {"diameter": 6.0, "length": 20.0, "head_diameter": 10.0},
                 "M6×20 内六角螺钉", "GB/T 70.1-2008"),
    StandardPart("M8_screw", "M8 内六角螺钉", "screws", "screws/M8_screw.step",
                 {"diameter": 8.0, "length": 25.0, "head_diameter": 13.0},
                 "M8×25 内六角螺钉", "GB/T 70.1-2008"),
    # 螺母
    StandardPart("M3_nut", "M3 六角螺母", "nuts", "nuts/M3_nut.step",
                 {"diameter": 3.0, "across_flats": 5.5, "thickness": 2.4},
                 "M3 六角螺母", "GB/T 6170-2015"),
    StandardPart("M4_nut", "M4 六角螺母", "nuts", "nuts/M4_nut.step",
                 {"diameter": 4.0, "across_flats": 7.0, "thickness": 3.2},
                 "M4 六角螺母", "GB/T 6170-2015"),
    StandardPart("M5_nut", "M5 六角螺母", "nuts", "nuts/M5_nut.step",
                 {"diameter": 5.0, "across_flats": 8.0, "thickness": 4.0},
                 "M5 六角螺母", "GB/T 6170-2015"),
    StandardPart("M6_nut", "M6 六角螺母", "nuts", "nuts/M6_nut.step",
                 {"diameter": 6.0, "across_flats": 10.0, "thickness": 5.0},
                 "M6 六角螺母", "GB/T 6170-2015"),
    StandardPart("M8_nut", "M8 六角螺母", "nuts", "nuts/M8_nut.step",
                 {"diameter": 8.0, "across_flats": 13.0, "thickness": 6.5},
                 "M8 六角螺母", "GB/T 6170-2015"),
    # 轴承
    StandardPart("bearing_6800", "6800 薄壁轴承", "bearings", "bearings/6800.step",
                 {"inner_diameter": 10.0, "outer_diameter": 19.0, "width": 5.0},
                 "6800-2RS 薄壁轴承", "GB/T 276-2013"),
    StandardPart("bearing_6801", "6801 薄壁轴承", "bearings", "bearings/6801.step",
                 {"inner_diameter": 12.0, "outer_diameter": 21.0, "width": 5.0},
                 "6801-2RS 薄壁轴承", "GB/T 276-2013"),
    StandardPart("bearing_6802", "6802 薄壁轴承", "bearings", "bearings/6802.step",
                 {"inner_diameter": 15.0, "outer_diameter": 24.0, "width": 5.0},
                 "6802-2RS 薄壁轴承", "GB/T 276-2013"),
    # 垫圈
    StandardPart("M5_washer", "M5 平垫圈", "washers", "washers/M5_washer.step",
                 {"inner_diameter": 5.3, "outer_diameter": 10.0, "thickness": 1.0},
                 "M5 平垫圈", "GB/T 97.1-2002"),
    StandardPart("M6_washer", "M6 平垫圈", "washers", "washers/M6_washer.step",
                 {"inner_diameter": 6.4, "outer_diameter": 12.0, "thickness": 1.6},
                 "M6 平垫圈", "GB/T 97.1-2002"),
]


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

def _parts_dir() -> Path:
    """标准件库目录。"""
    try:
        from vermes_constants import get_vermes_home
        return get_vermes_home() / "mfgcad" / "parts"
    except Exception:
        return Path.home() / ".vermes" / "mfgcad" / "parts"


def list_parts(category: Optional[str] = None) -> List[Dict[str, Any]]:
    """列出可用标准件。"""
    parts = BUILTIN_CATALOG
    if category:
        parts = [p for p in parts if p.category == category]
    return [
        {
            "id": p.id,
            "name": p.name,
            "category": p.category,
            "standard": p.standard,
            "description": p.description,
            "parameters": p.parameters,
            "available": (_parts_dir() / p.file_path).exists(),
        }
        for p in parts
    ]


def get_part(part_id: str) -> Optional[Dict[str, Any]]:
    """获取单个标准件信息。"""
    p = next((x for x in BUILTIN_CATALOG if x.id == part_id), None)
    if p is None:
        return None
    path = _parts_dir() / p.file_path
    return {
        "id": p.id,
        "name": p.name,
        "category": p.category,
        "standard": p.standard,
        "description": p.description,
        "parameters": p.parameters,
        "file_path": str(path) if path.exists() else None,
        "available": path.exists(),
    }


def search_parts(query: str) -> List[Dict[str, Any]]:
    """搜索标准件。"""
    q = query.lower()
    results = []
    for p in BUILTIN_CATALOG:
        if q in p.id.lower() or q in p.name.lower() or q in p.standard.lower():
            results.append({
                "id": p.id,
                "name": p.name,
                "category": p.category,
                "standard": p.standard,
                "available": (_parts_dir() / p.file_path).exists(),
            })
    return results


def list_categories() -> List[str]:
    """列出所有分类。"""
    return sorted(set(p.category for p in BUILTIN_CATALOG))


__all__ = [
    "StandardPart",
    "BUILTIN_CATALOG",
    "list_parts",
    "get_part",
    "search_parts",
    "list_categories",
]
