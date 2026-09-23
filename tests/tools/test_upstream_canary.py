"""contract tests for scripts/upstream_canary.py（T2 pinned canary 机制）。

钉住三条容易「用着用着就松掉」的约束：
1. canary 的判据必须是**不可移动的 ref**（禁 HEAD/main/master/remote-tracking 分支）；
2. pin 唯一真源 = reports/.upstream-canary-pin.json，且 workflow 里**不得**写死 tag；
3. 只告警不阻塞：CI lane 必须 continue-on-error，且不含 || true 式的静默吞错。
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"


def _canary() -> Path:
    p = SCRIPTS / "upstream_canary.py"
    assert p.exists(), f"缺少 {p}"
    return p


def _load_canary():
    """按模块方式加载脚本本身（脚本在 __main__ 守卫下无导入副作用）。"""
    spec = importlib.util.spec_from_file_location("upstream_canary", _canary())
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_moving_refs_are_rejected():
    """HEAD / 分支名 / remote-tracking 必须被判为移动 ref。"""
    is_moving_ref = _load_canary().is_moving_ref
    for ref in ("HEAD", "main", "master", "upstream/main", "origin/develop"):
        assert is_moving_ref(ref), f"{ref} 应当被判为移动 ref"
    for ref in ("v2026.9.14", "v2.5.1", "a" * 40):
        assert not is_moving_ref(ref), f"{ref} 不应当被判为移动 ref"


def test_default_pin_matches_pin_file():
    """脚本回落默认 tag 必须与 pin 文件一致（避免两份真相）。"""
    pin_path = ROOT / "reports" / ".upstream-canary-pin.json"
    if not pin_path.exists():
        return
    tag = json.loads(pin_path.read_text(encoding="utf-8"))["upstream_tag"]
    assert _load_canary().DEFAULT_PIN_TAG == tag, (
        f"DEFAULT_PIN_TAG({_load_canary().DEFAULT_PIN_TAG}) != pin 文件({tag})"
    )


def test_pin_file_is_pinned_and_wellformed():
    """pin 文件存在则必须钉住 tag + 有下一次复核日期（季度升钉可审计）。"""
    pin_path = ROOT / "reports" / ".upstream-canary-pin.json"
    if not pin_path.exists():
        return  # 首次运行允许不存在，脚本会回落到 DEFAULT_PIN_TAG
    data = json.loads(pin_path.read_text(encoding="utf-8"))
    assert data.get("schema") == "vermes.canary-pin/v1", "pin schema 版本不符"
    tag = data.get("upstream_tag")
    assert tag and not re.match(r"^(HEAD|main|master)$", tag), f"pin 不得是移动 ref: {tag}"
    for key in ("upstream_commit", "pinned_at", "next_review", "bump_policy"):
        assert data.get(key), f"pin 文件缺字段 {key}"


def test_workflow_does_not_hardcode_pin_tag():
    """§8.4 纪律：钉点唯一真源是 pin json，CI 不得另写一份 tag。"""
    wf = ROOT / ".github" / "workflows" / "upstream-canary.yml"
    assert wf.exists(), f"缺少 {wf}"
    text = wf.read_text(encoding="utf-8")
    body = text.split("Prepare upstream checkout")[1] if "Prepare upstream checkout" in text else text
    assert not re.search(r"v20\d\d\.\d+\.\d+", body), (
        "workflow 里写死了 tag —— 钉点唯一真源必须是 reports/.upstream-canary-pin.json"
    )


def test_canary_is_alert_only():
    """只告警不阻塞：必须有 continue-on-error，且不得用 `|| true` 静默吞错。"""
    wf = ROOT / ".github" / "workflows" / "upstream-canary.yml"
    text = wf.read_text(encoding="utf-8")
    assert "continue-on-error: true" in text, "canary lane 必须 continue-on-error（告警语义）"
    assert "|| true" not in text, "不得用 `|| true` 静默吞错（等价于假绿）"
    assert "upstream-canary" in text
