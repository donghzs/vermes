"""机器人模型导出测试。"""
import pytest
from pathlib import Path
from vermes_cli.mfgcad.robot_export import (
    RobotLink, RobotJoint,
    export_urdf, export_srdf, export_sdf,
)


@pytest.fixture
def simple_robot():
    links = [
        RobotLink(name="base", mass=1.0, geometry_file="base.stl"),
        RobotLink(name="arm1", mass=0.5, geometry_file="arm1.stl",
                  origin_xyz=[0, 0, 0.1]),
    ]
    joints = [
        RobotJoint(name="joint1", joint_type="revolute",
                   parent="base", child="arm1",
                   axis=[0, 0, 1]),
    ]
    return links, joints


def test_export_urdf_basic(tmp_path, simple_robot):
    links, joints = simple_robot
    result = export_urdf(links, joints, "test_robot", tmp_path / "test.urdf")
    assert result["ok"] is True
    assert result["link_count"] == 2
    assert result["joint_count"] == 1

    # 验证 XML 内容
    from xml.etree import ElementTree as ET
    tree = ET.parse(result["urdf_path"])
    root = tree.getroot()
    assert root.tag == "robot"
    assert root.get("name") == "test_robot"
    assert len(root.findall("link")) == 2
    assert len(root.findall("joint")) == 1


def test_export_urdf_no_links():
    result = export_urdf([], [])
    assert result["ok"] is False
    assert "至少需要一个 link" in result["error"]


def test_export_urdf_default_path(simple_robot):
    links, joints = simple_robot
    result = export_urdf(links, joints, "test_default")
    assert result["ok"] is True
    assert ".vermes" in result["urdf_path"]


def test_export_srdf_basic(tmp_path, simple_robot):
    links, joints = simple_robot
    result = export_srdf(links, joints, "test_robot", tmp_path / "test.srdf")
    assert result["ok"] is True

    from xml.etree import ElementTree as ET
    tree = ET.parse(result["srdf_path"])
    root = tree.getroot()
    assert root.tag == "robot"
    # 应有 disable_collisions 条目
    disables = root.findall("disable_collisions")
    assert len(disables) == 1
    assert disables[0].get("link1") == "base"
    assert disables[0].get("link2") == "arm1"


def test_export_sdf_basic(tmp_path, simple_robot):
    links, joints = simple_robot
    result = export_sdf(links, joints, "test_robot", tmp_path / "test.sdf")
    assert result["ok"] is True

    from xml.etree import ElementTree as ET
    tree = ET.parse(result["sdf_path"])
    root = tree.getroot()
    assert root.tag == "sdf"
    model = root.find("model")
    assert model is not None
    assert model.get("name") == "test_robot"
    assert len(model.findall("link")) == 2


def test_export_urdf_fixed_joint(tmp_path):
    links = [
        RobotLink(name="base"),
        RobotLink(name="top", origin_xyz=[0, 0, 0.5]),
    ]
    joints = [
        RobotJoint(name="fix", joint_type="fixed",
                   parent="base", child="top"),
    ]
    result = export_urdf(links, joints, "fixed_robot", tmp_path / "fixed.urdf")
    assert result["ok"] is True

    from xml.etree import ElementTree as ET
    tree = ET.parse(result["urdf_path"])
    root = tree.getroot()
    joint = root.find("joint")
    assert joint.get("type") == "fixed"
    # fixed joint 不应有 axis
    assert joint.find("axis") is None
