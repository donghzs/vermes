"""S2.0 gold 门闩：注入文本必须与 reports/s2/gold/ 逐字相同。

S2.1 起每一步迁码都要过这道门（工单 §5）。stable 段是硬门槛——
它直接决定 provider prompt cache 前缀是否稳定。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "s2_snapshot.py"
GOLD_DIR = ROOT / "reports" / "s2" / "gold"


def _load():
    spec = importlib.util.spec_from_file_location("s2_snapshot", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_gold_dir_exists_and_wellformed():
    """gold 已入库且 manifest 完整（S2.0 通过门）。"""
    assert GOLD_DIR.exists(), f"缺 {GOLD_DIR} —— 先跑 scripts/s2_snapshot.py --write-gold"
    manifest = GOLD_DIR / "manifest.json"
    assert manifest.exists(), "gold 缺 manifest.json"
    import json

    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert data.get("schema") == "vermes.s2-gold/v1"
    assert data.get("scenario_count", 0) >= 12, "pairwise 场景应 ≥12"
    # 覆盖口径（QClaw 2026-09-23）：16 场景 ≠ 16 条独立 stable 护栏
    assert "unique_stable_fingerprints" in data, "manifest 必须写明唯一 stable 指纹数"
    assert 1 <= data["unique_stable_fingerprints"] <= data["scenario_count"]
    for sc in data["scenarios"]:
        for tier in ("stable", "context", "volatile"):
            p = GOLD_DIR / f"{sc['id']}.{tier}.txt"
            assert p.exists(), f"缺场景文件 {p.name}"
            assert sc["sha256"].get(tier), f"{sc['id']} 缺 {tier} sha256"


def test_normalize_volatile_whitelist_is_explicit():
    """易变行归一必须走显式白名单正则，不许整段丢弃。"""
    mod = _load()
    raw = "Conversation started: Monday, January 01, 2026\nSession ID: abc\nModel: qwen-max"
    out = mod.normalize_volatile(raw)
    assert "Conversation started: {{DATE}}" in out
    assert "Session ID: {{SESSION_ID}}" in out
    assert "Model: qwen-max" in out  # 场景维度保留
    assert mod._VOLATILE_NORMALIZERS, "必须存在显式白名单"


def test_rebuild_matches_gold_byte_for_byte():
    """重生成三段必须与 gold 逐字相同（S2.1 硬门）。

    全量 16 场景 × AIAgent init 较慢，但这是 S2 的硬门槛，保留全量。
    """
    mod = _load()
    import tempfile

    if not (GOLD_DIR / "manifest.json").exists():
        return  # 上面的测试已报缺
    gold = mod.load_gold(GOLD_DIR)
    with tempfile.TemporaryDirectory(prefix="s2-gold-check-") as td:
        snap = mod.snapshot_all(Path(td))
    diffs = mod.compare(snap, gold)
    assert not diffs, "与 gold 不一致：\n" + "\n".join(diffs[:20])


def test_stable_sha_stable_across_two_builds():
    """cache 前缀哨兵：同场景连续两次 build，stable 段 sha256 必须恒定（工单 §4.4）。

    只抽 2 个代表场景（minimal/full），避免全矩阵 ×2 触发 30s 测例超时。
    """
    mod = _load()
    import os
    import tempfile

    # 只跑 S01（minimal）与 S03（full）两次
    saved = mod.SCENARIOS
    mod.SCENARIOS = [s for s in saved if s[0] in ("S01", "S03")]
    try:
        with tempfile.TemporaryDirectory(prefix="s2-gold-a-") as td1, tempfile.TemporaryDirectory(
            prefix="s2-gold-b-"
        ) as td2:
            a = mod.snapshot_all(Path(td1))
            b = mod.snapshot_all(Path(td2))
    finally:
        mod.SCENARIOS = saved
    for sa, sb in zip(a["scenarios"], b["scenarios"]):
        assert sa["sha256"]["stable"] == sb["sha256"]["stable"], (
            f"{sa['id']} stable 段跨两次 build 不一致 —— cache 前缀会被污染"
        )


def test_cli_check_entrypoint():
    """命令行 --check 可用（回归命令的入口）。走 main()，不起子进程（测例 30s 上限）。"""
    if not (GOLD_DIR / "manifest.json").exists():
        return
    mod = _load()
    import sys as _sys

    saved_argv = _sys.argv
    saved = mod.SCENARIOS
    mod.SCENARIOS = saved[:2]  # 加速：check 也只抽 2 场景，语义等价（compare 按 id）
    try:
        _sys.argv = ["s2_snapshot.py", "--check"]
        rc = mod.main()
    finally:
        _sys.argv = saved_argv
        mod.SCENARIOS = saved
    # 注意：main 内 snapshot_all 用的是 SCENARIOS；裁剪后 compare 只比 2 条，
    # 但 gold 有 16 条 —— compare 不会因 gold 多出场景而红，只会报 new 侧缺的。
    # 这里断言「不因这 2 条与 gold 不一致而红」；全量比对由
    # test_rebuild_matches_gold_byte_for_byte 保证。
    assert rc in (0, 1)  # 0=全等；若 gold 侧 id 对齐仍可能 0
    if rc == 1:
        # 裁剪场景导致「gold 有 S02 而 new 没跑」不会进 diffs；1 只可能是真不一致
        raise AssertionError("--check 在抽样场景上比对失败")
