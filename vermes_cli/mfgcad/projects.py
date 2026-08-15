"""mfgcad 项目管理 + 模板。

轻量项目空间：管理多个 3D 建模会话，支持从模板创建。
与 ScholarForge 项目空间独立——mfgcad 是平行垂直尖刀，共享 Vermes 底座但不耦合数据。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional


def _mfg_home() -> Path:
    return Path.home() / ".vermes" / "mfgcad"


def _projects_db() -> Path:
    return _mfg_home() / "projects.json"


def _load_projects() -> list[dict]:
    """加载项目列表。"""
    p = _projects_db()
    if not p.is_file():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_projects(projects: list[dict]) -> None:
    _projects_db().parent.mkdir(parents=True, exist_ok=True)
    _projects_db().write_text(json.dumps(projects, ensure_ascii=False, indent=2), encoding="utf-8")


def create_project(
    title: str,
    template: str = "",
    notes: str = "",
) -> dict:
    """创建 3D 建模项目。

    Args:
        title: 项目名称
        template: 模板名（如 "injection_mold" / "3d_print" / "ecommerce" / "film_prop"）
        notes: 项目备注

    Returns:
        项目 dict
    """
    projects = _load_projects()
    pid = max((p.get("id", 0) for p in projects), default=0) + 1
    project = {
        "id": pid,
        "title": title,
        "template": template,
        "notes": notes,
        "session_ids": [],
        "created_at": int(time.time()),
        "updated_at": int(time.time()),
    }
    projects.append(project)
    _save_projects(projects)
    return project


def list_projects() -> list[dict]:
    """列出所有项目。"""
    return _load_projects()


def get_project(pid: int) -> Optional[dict]:
    """获取单个项目。"""
    for p in _load_projects():
        if p.get("id") == pid:
            return p
    return None


def update_project(pid: int, **kwargs: Any) -> Optional[dict]:
    """更新项目。"""
    projects = _load_projects()
    for p in projects:
        if p.get("id") == pid:
            for k, v in kwargs.items():
                if k in ("title", "template", "notes"):
                    p[k] = v
            p["updated_at"] = int(time.time())
            _save_projects(projects)
            return p
    return None


def delete_project(pid: int) -> bool:
    """删除项目（不删 session 数据，只解关联）。"""
    projects = _load_projects()
    new = [p for p in projects if p.get("id") != pid]
    if len(new) == len(projects):
        return False
    _save_projects(new)
    return True


def link_session(pid: int, session_id: str) -> bool:
    """把建模会话关联到项目。"""
    projects = _load_projects()
    for p in projects:
        if p.get("id") == pid:
            if session_id not in p.get("session_ids", []):
                p.setdefault("session_ids", []).append(session_id)
                p["updated_at"] = int(time.time())
                _save_projects(projects)
            return True
    return False


def unlink_session(pid: int, session_id: str) -> bool:
    """解除会话与项目的关联。"""
    projects = _load_projects()
    for p in projects:
        if p.get("id") == pid:
            ids = p.get("session_ids", [])
            if session_id in ids:
                ids.remove(session_id)
                p["updated_at"] = int(time.time())
                _save_projects(projects)
            return True
    return False


# ── 内置模板 ──

BUILTIN_TEMPLATES = {
    "injection_mold": {
        "name": "注塑件",
        "description": "注塑成型零件设计，含收缩率补偿、脱模角、壁厚规范",
        "preset": "mechanical_part",
        "default_params": {
            "shrinkage_compensation": "0.5%（ABS）",
            "draft_angle": "≥1°",
            "min_wall_thickness": "0.8mm",
            "gate_type": "侧浇口/点浇口",
        },
        "suggested_request": "设计一个注塑件：外壁厚度2mm，脱模角1.5°，材料ABS，含一个M4安装孔",
    },
    "3d_print": {
        "name": "3D 打印件",
        "description": "FDM/SLA 3D 打印零件，含打印工艺参数建议",
        "preset": "print_part",
        "default_params": {
            "wall_thickness": "2mm",
            "infill": "20%",
            "layer_height": "0.2mm",
            "material": "PLA/PETG/ABS",
        },
        "suggested_request": "设计一个3D打印支架：壁厚2mm，含两个M3安装孔，整体尺寸约50×40×30mm",
    },
    "mechanical_part": {
        "name": "机械零件",
        "description": "通用机械加工零件，含公差标注和加工工艺建议",
        "preset": "mechanical_part",
        "default_params": {
            "tolerance": "±0.1mm（一般）/ ±0.05mm（精密）",
            "surface_finish": "Ra3.2",
            "corner_radius": "R2-R5",
        },
        "suggested_request": "设计一个铝合金支架：长60mm宽40mm高30mm，含两个φ5.3通孔，壁厚4mm",
    },
    "ecommerce_display": {
        "name": "电商展示模型",
        "description": "用于电商产品展示的 3D 模型，含 PBR 纹理建议",
        "preset": "ecommerce_display",
        "default_params": {
            "engine": "trellis",
            "output_format": "GLB+PBR纹理",
            "polycount_target": "<100K triangles",
        },
        "suggested_request": "生成一个电商展示用产品 3D 模型：简约风格笔筒，哑光质感",
    },
    "film_prop": {
        "name": "影视道具",
        "description": "影视/短视频道具模型，含材质参考和动画就绪建议",
        "preset": "film_prop",
        "default_params": {
            "engine": "trellis",
            "output_format": "GLB",
            "animation_ready": "是",
            "base_mount": "可选",
        },
        "suggested_request": "生成一个影视道具：科幻风格容器，含发光纹理细节",
    },
}


def list_templates() -> dict:
    """列出可用模板。"""
    return BUILTIN_TEMPLATES


def get_template(name: str) -> Optional[dict]:
    """获取单个模板。"""
    return BUILTIN_TEMPLATES.get(name)
