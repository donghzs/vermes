"""L2 启动注册（bootstrap）单元测试：扫描 PATH 上的 cli-anything-* + 失败容错。

沙箱可跑：不依赖真实 CLI-Anything 安装；用 tmp_path 伪造可执行文件 + mock
SoftwareAdapter 验证 discover/register 全链路与单适配器失败隔离。
"""

from __future__ import annotations

import pytest

from vermes_cli.adapters import bootstrap as boot
from vermes_cli.adapters.discovery import CLI_NATIVE


def test_iter_cli_anything_bins_filters_executables(tmp_path):
    """只返回可执行的 cli-anything-* 绝对路径，忽略无关/不可执行文件。"""
    (tmp_path / "cli-anything-freecad").write_text("#!/bin/sh\n")
    (tmp_path / "cli-anything-blender").write_text("#!/bin/sh\n")
    (tmp_path / "cli-anything-not-exec").write_text("#!/bin/sh\n")  # 无 x 权限
    (tmp_path / "foo.sh").write_text("#!/bin/sh\n")
    for name in ("cli-anything-freecad", "cli-anything-blender", "foo.sh"):
        (tmp_path / name).chmod(0o755)
    # cli-anything-not-exec 保持 0644（不可执行）

    bins = boot._iter_cli_anything_bins([str(tmp_path)])
    assert bins == [
        str(tmp_path / "cli-anything-blender"),
        str(tmp_path / "cli-anything-freecad"),
    ]


def test_derive_spec_known_domain():
    """freecad → domain=3d（已知映射），backend=software，cli_bin 为绝对路径。"""
    spec = boot._derive_spec("/usr/local/bin/cli-anything-freecad")
    assert spec.domain == "3d"
    assert spec.software == "freecad"
    assert spec.backend == "freecad"
    assert spec.cli_bin == "/usr/local/bin/cli-anything-freecad"
    assert spec.operation_mechanism == CLI_NATIVE


def test_derive_spec_unknown_domain_fallback():
    """未知软件 fallback 到 software 名本身（fail-open，工具仍注册）。"""
    spec = boot._derive_spec("/usr/local/bin/cli-anything-excel")
    assert spec.domain == "excel"
    assert spec.software == "excel"
    assert spec.backend == "excel"


def test_derive_spec_empty_software_raises():
    """cli-anything-（空 software）→ ValueError。"""
    with pytest.raises(ValueError):
        boot._derive_spec("/usr/local/bin/cli-anything-")


def test_discover_l2_adapters_success_and_isolated_failure(monkeypatch):
    """成功适配器正常注册；单个适配器 discover 失败仅记 -1，不阻塞其他。"""
    monkeypatch.setattr(
        boot,
        "_iter_cli_anything_bins",
        lambda paths=None: [
            "/opt/bin/cli-anything-freecad",
            "/opt/bin/cli-anything-blender",
            "/opt/bin/cli-anything-broken",
        ],
    )

    register_calls = []

    def fake_adapter_factory(spec):
        class _A:
            toolset = f"{spec.software}_adapter"

            def discover_tools(self):
                if spec.software == "broken":
                    raise RuntimeError("boom")
                return [object()] * (3 if spec.software == "freecad" else 5)

            def register(self, tools):
                register_calls.append((spec.software, len(tools)))
                return len(tools)

        return _A()

    monkeypatch.setattr(boot, "SoftwareAdapter", fake_adapter_factory)

    result = boot.discover_l2_adapters()
    assert result == {"freecad": 3, "blender": 5, "broken": -1}
    # broken 的 discover 抛异常，register 从未被调；freecad/blender 正常注册
    assert register_calls == [("freecad", 3), ("blender", 5)]
