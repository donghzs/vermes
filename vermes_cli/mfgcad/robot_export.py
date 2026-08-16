"""
Vermes 机器人模型导出 — URDF/SRDF/SDF。

对标 text-to-cad 的机器人 Skill：
  - mfg_export_urdf: 从多零件组装体导出 URDF（机器人描述格式）
  - mfg_export_sdf: 导出 SDF（Gazebo 仿真用）

URDF (Unified Robot Description Format) 是 ROS 标准机器人描述格式，
SDF (Simulation Description Format) 是 Gazebo 仿真器格式。

当前实现：
  - 从 mfgcad session 的多个 STEP 零件 + 组装关系生成 URDF XML
  - 每个零件作为一个 link，组装关系中的配合面成为 joint
  - SRDF (Semantic Robot Description Format) 附带基础语义信息
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------

@dataclass
class RobotLink:
    """URDF link。"""
    name: str
    mass: float = 0.1  # kg
    origin_xyz: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    origin_rpy: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    geometry_file: str = ""  # mesh 文件路径（相对 URDF 目录）
    visual_color: List[float] = field(default_factory=lambda: [0.8, 0.8, 0.8, 1.0])


@dataclass
class RobotJoint:
    """URDF joint。"""
    name: str
    joint_type: str  # revolute / continuous / fixed / prismatic
    parent: str
    child: str
    origin_xyz: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    origin_rpy: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    axis: List[float] = field(default_factory=lambda: [0.0, 0.0, 1.0])
    limit_lower: float = -3.14
    limit_upper: float = 3.14
    limit_effort: float = 10.0
    limit_velocity: float = 1.0


# ---------------------------------------------------------------------------
# URDF 导出
# ---------------------------------------------------------------------------

def export_urdf(
    links: List[RobotLink],
    joints: List[RobotJoint],
    robot_name: str = "vermes_robot",
    output_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """生成 URDF XML 文件。

    Args:
        links: link 列表
        joints: joint 列表
        robot_name: 机器人名称
        output_path: 输出路径（缺省 ~/.vermes/mfgcad/urdf/<robot_name>.urdf）

    Returns:
        {"ok": bool, "urdf_path": str, "link_count": int, "joint_count": int}
    """
    if not links:
        return {"ok": False, "error": "至少需要一个 link"}

    if output_path is None:
        output_path = Path.home() / ".vermes" / "mfgcad" / "urdf" / f"{robot_name}.urdf"
    else:
        output_path = Path(output_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 构建 XML
    robot = ET.Element("robot", name=robot_name)

    for link in links:
        link_el = ET.SubElement(robot, "link", name=link.name)

        # visual
        visual = ET.SubElement(link_el, "visual")
        origin = ET.SubElement(visual, "origin",
                               xyz=" ".join(str(v) for v in link.origin_xyz),
                               rpy=" ".join(str(v) for v in link.origin_rpy))
        geometry = ET.SubElement(visual, "geometry")
        if link.geometry_file:
            ET.SubElement(geometry, "mesh", filename=link.geometry_file)
        else:
            ET.SubElement(geometry, "box", size="0.1 0.1 0.1")
        material = ET.SubElement(visual, "material", name=f"{link.name}_material")
        ET.SubElement(material, "color",
                      rgba=" ".join(str(v) for v in link.visual_color))

        # collision (same as visual)
        collision = ET.SubElement(link_el, "collision")
        ET.SubElement(collision, "origin",
                      xyz=" ".join(str(v) for v in link.origin_xyz),
                      rpy=" ".join(str(v) for v in link.origin_rpy))
        col_geom = ET.SubElement(collision, "geometry")
        if link.geometry_file:
            ET.SubElement(col_geom, "mesh", filename=link.geometry_file)
        else:
            ET.SubElement(col_geom, "box", size="0.1 0.1 0.1")

        # inertial
        inertial = ET.SubElement(link_el, "inertial")
        ET.SubElement(inertial, "mass", value=str(link.mass))
        ET.SubElement(inertial, "origin", xyz="0 0 0", rpy="0 0 0")
        ET.SubElement(inertial, "inertia",
                      ixx="0.001", ixy="0", ixz="0",
                      iyy="0.001", iyz="0", izz="0.001")

    for joint in joints:
        joint_el = ET.SubElement(robot, "joint",
                                  name=joint.name, type=joint.joint_type)
        ET.SubElement(joint_el, "parent", link=joint.parent)
        ET.SubElement(joint_el, "child", link=joint.child)
        ET.SubElement(joint_el, "origin",
                      xyz=" ".join(str(v) for v in joint.origin_xyz),
                      rpy=" ".join(str(v) for v in joint.origin_rpy))
        if joint.joint_type in ("revolute", "continuous", "prismatic"):
            ET.SubElement(joint_el, "axis",
                          xyz=" ".join(str(v) for v in joint.axis))
        if joint.joint_type in ("revolute", "prismatic"):
            ET.SubElement(joint_el, "limit",
                          lower=str(joint.limit_lower),
                          upper=str(joint.limit_upper),
                          effort=str(joint.limit_effort),
                          velocity=str(joint.limit_velocity))

    # 写入文件
    tree = ET.ElementTree(robot)
    ET.indent(tree, space="  ", level=0)
    tree.write(output_path, encoding="utf-8", xml_declaration=True)

    return {
        "ok": True,
        "urdf_path": str(output_path),
        "link_count": len(links),
        "joint_count": len(joints),
    }


# ---------------------------------------------------------------------------
# SRDF 导出
# ---------------------------------------------------------------------------

def export_srdf(
    links: List[RobotLink],
    joints: List[RobotJoint],
    robot_name: str = "vermes_robot",
    output_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """生成 SRDF XML 文件（基础语义信息）。

    Returns:
        {"ok": bool, "srdf_path": str}
    """
    if output_path is None:
        output_path = Path.home() / ".vermes" / "mfgcad" / "urdf" / f"{robot_name}.srdf"
    else:
        output_path = Path(output_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    robot = ET.Element("robot", name=robot_name)

    # 基础 link groups
    for link in links:
        group = ET.SubElement(robot, "group", name=f"{link.name}_group")
        ET.SubElement(group, "link", name=link.name)

    # 基础 joint groups
    for joint in joints:
        group = ET.SubElement(robot, "group", name=f"{joint.name}_group")
        ET.SubElement(group, "joint", name=joint.name)

    # 默认碰撞忽略（相邻 link 不检测自碰撞）
    for joint in joints:
        ET.SubElement(robot, "disable_collisions",
                      link1=joint.parent, link2=joint.child, reason="Adjacent")

    tree = ET.ElementTree(robot)
    ET.indent(tree, space="  ", level=0)
    tree.write(output_path, encoding="utf-8", xml_declaration=True)

    return {
        "ok": True,
        "srdf_path": str(output_path),
    }


# ---------------------------------------------------------------------------
# SDF 导出（Gazebo 格式，简化版）
# ---------------------------------------------------------------------------

def export_sdf(
    links: List[RobotLink],
    joints: List[RobotJoint],
    robot_name: str = "vermes_robot",
    output_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """生成 SDF XML 文件（Gazebo 仿真格式，简化版）。

    Returns:
        {"ok": bool, "sdf_path": str}
    """
    if output_path is None:
        output_path = Path.home() / ".vermes" / "mfgcad" / "urdf" / f"{robot_name}.sdf"
    else:
        output_path = Path(output_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    sdf = ET.Element("sdf", version="1.6")
    model = ET.SubElement(sdf, "model", name=robot_name)

    for link in links:
        link_el = ET.SubElement(model, "link", name=link.name)
        pose = ET.SubElement(link_el, "pose",
                             frame="")
        pose.text = " ".join(str(v) for v in link.origin_xyz) + " 0 0 0"

        inertial = ET.SubElement(link_el, "inertial")
        ET.SubElement(inertial, "mass", value=str(link.mass))

        visual = ET.SubElement(link_el, "visual", name=f"{link.name}_visual")
        geom = ET.SubElement(visual, "geometry")
        if link.geometry_file:
            ET.SubElement(geom, "mesh", uri=link.geometry_file)
        else:
            ET.SubElement(geom, "box", size="0.1 0.1 0.1")

    for joint in joints:
        joint_el = ET.SubElement(model, "joint",
                                  name=joint.name, type=joint.joint_type)
        ET.SubElement(joint_el, "parent", link=joint.parent)
        ET.SubElement(joint_el, "child", link=joint.child)
        ET.SubElement(joint_el, "axis",
                      xyz=" ".join(str(v) for v in joint.axis))

    tree = ET.ElementTree(sdf)
    ET.indent(tree, space="  ", level=0)
    tree.write(output_path, encoding="utf-8", xml_declaration=True)

    return {
        "ok": True,
        "sdf_path": str(output_path),
    }


__all__ = [
    "RobotLink",
    "RobotJoint",
    "export_urdf",
    "export_srdf",
    "export_sdf",
]
