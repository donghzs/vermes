# -*- coding: utf-8 -*-
"""P4-3 校验覆盖 CI 断言：动态枚举已注册 scholarforge 工具，与 VALIDATED_TOOLS 清单交叉校验。

用户拍板 Q4：27 工具中 ≥25 个具备 ≥1 层校验即收口。

本测试不依赖记忆：直接从全局 registry 枚举真实注册名（register_tools 的唯一事实源），
再与 vermes_cli/scholarforge/validation_coverage.py 的清单交叉校验：
1. 每个已注册工具都在清单中有校验层级（不漏接）；
2. 清单里的每个工具都真实注册（防死条目漂移）；
3. 覆盖度达到必达线 ≥25。
"""
import sys

sys.path.insert(0, "/Users/dongzusheng/Projects/vermes-electron")

from tools.registry import registry
from vermes_cli.scholarforge import tools as sf_tools
from vermes_cli.scholarforge.validation_coverage import (
    VALIDATED_TOOLS,
    coverage_passing,
    short_name,
)


def _ensure_registered():
    """register_tools 内部 override=False，重复注册会抛错；已注册则跳过。"""
    if any(n.startswith("scholarforge_") for n in registry.get_all_tool_names()):
        return
    sf_tools.register_tools()


def _registered_scholarforge_short_names():
    _ensure_registered()
    return {
        short_name(n)
        for n in registry.get_all_tool_names()
        if n.startswith("scholarforge_")
    }


def test_every_registered_tool_has_validation_tier():
    registered = _registered_scholarforge_short_names()
    assert registered, "global registry 中没有任何 scholarforge 工具被注册"
    missing = sorted(registered - set(VALIDATED_TOOLS.keys()))
    assert not missing, (
        f"{len(missing)} 个已注册 scholarforge 工具在 VALIDATED_TOOLS 中缺失条目：{missing}"
    )


def test_validation_manifest_has_no_orphans():
    """清单不应包含运行时未注册的工具，防止死条目漂移。"""
    registered = _registered_scholarforge_short_names()
    orphans = sorted(set(VALIDATED_TOOLS.keys()) - registered)
    assert not orphans, (
        f"VALIDATED_TOOLS 含 {len(orphans)} 个运行时未注册的孤儿条目：{orphans}"
    )


def test_scholarforge_coverage_meets_threshold():
    """≥25 个工具具备 ≥1 层校验即收口（用户 Q4 必达线）。"""
    assert coverage_passing(25) is True
