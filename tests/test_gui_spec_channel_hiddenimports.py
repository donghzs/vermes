"""Defect 2 contract: vermes-gui.spec channel hiddenimports stay aligned.

``build.sh`` / release use ``vermes-backend.spec`` (already complete). The GUI
spec was missing platform channel packages; we keep it aligned so a future
gui-spec build does not ImportError on feishu/telegram/etc.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _hidden_names(spec_path: Path) -> set[str]:
    import re

    text = spec_path.read_text(encoding="utf-8")
    return set(re.findall(r"'([A-Za-z0-9_.]+)'", text))


def test_gui_spec_has_core_channel_packages():
    names = _hidden_names(ROOT / "vermes-gui.spec")
    for mod in (
        "lark_oapi",
        "telegram",
        "discord",
        "slack_bolt",
        "dingtalk_stream",
        "mautrix",
        "coincurve",
        "qrcode",
    ):
        assert mod in names, f"vermes-gui.spec hiddenimports 缺渠道包 {mod}"


def test_gui_spec_covers_lark_submodules():
    names = _hidden_names(ROOT / "vermes-gui.spec")
    for mod in (
        "lark_oapi.ws",
        "lark_oapi.event.dispatcher_handler",
        "telegram.ext",
    ):
        assert mod in names, f"vermes-gui.spec 缺子模块 {mod}"


def test_backend_spec_still_has_channels():
    names = _hidden_names(ROOT / "vermes-backend.spec")
    assert "lark_oapi" in names and "telegram" in names
