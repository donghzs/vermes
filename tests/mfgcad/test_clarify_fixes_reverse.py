"""clarify loader 修复的反向验证测试（#400，R5）。

必须能在 pre-fix 代码（commit 117caa4cd）上失败：旧 `_load_presets` 在用户把
preset 写成 list 形态时会抛 AttributeError，导致全部 clarify 失效。

注意：旧代码没有 `_merge_presets` 这个函数，因此本文件在 pre-fix 下会因导入失败
（ImportError）而整文件报错 —— 这本身即证明修复必要。
"""

from pathlib import Path

import pytest

import sys as _sys

_sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vermes_cli.mfgcad.clarify import _load_presets, _merge_presets  # noqa: E402


class TestClarifyPresetMerge:
    def test_merge_list_format(self):
        out = _merge_presets(
            {},
            {"presets": [{"name": "a", "label": "A"}, {"name": "b", "label": "B"}]},
        )
        assert out == {"a": {"name": "a", "label": "A"}, "b": {"name": "b", "label": "B"}}

    def test_merge_dict_format(self):
        out = _merge_presets({}, {"presets": {"a": {"label": "A"}}})
        assert out == {"a": {"label": "A"}}

    def test_merge_bad_item_no_crash(self):
        # list 里缺 name/key 的项应被静默跳过，不抛异常
        out = _merge_presets({}, {"presets": [{"label": "no-name"}]})
        assert out == {}

    def test_load_presets_list_format_on_disk(self, tmp_path, monkeypatch):
        # 内置 preset 文件写成 list 形态 + 用户目录不存在 → 仍能加载
        pfile = tmp_path / "presets.yaml"
        pfile.write_text(
            "presets:\n  - name: mech\n    label: 机械\n  - name: print\n    label: 打印\n",
            encoding="utf-8",
        )
        monkeypatch.setattr("vermes_cli.mfgcad.clarify._HERE", tmp_path)
        monkeypatch.setattr("vermes_cli.mfgcad.clarify._PRESETS_CACHE", None)
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "nohome")
        presets = _load_presets()
        assert "mech" in presets and "print" in presets
