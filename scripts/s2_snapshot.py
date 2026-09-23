#!/usr/bin/env python3
"""scripts/s2_snapshot.py — S2 注入等价 gold 快照（roadmap S2.0 / 工单 §4.3）。

为什么存在
----------
S2 要把静态块迁到注册 API，验收是「注入文本逐字等价」。`should_inject()`
依赖 model / platform / toolset / config_flag，裸文本比对会产大量假阳性。
本脚本把「场景矩阵 → 三段 prompt（stable/context/volatile）→ sha256」固化下来，
**动注入代码之前**用当前版本生成 `reports/s2/gold/` 作为门闩；之后每次改动
重生成并与 gold 逐字比对（stable 硬门槛）。

约束（工单 §4，不做自我发挥）
-----------------------------
1. 入口必须是 `build_system_prompt_parts(agent)`（真 AIAgent，mock OpenAI/工具），
   **不另写 agent stub**（与 tests/run_agent 同一套起手式）。
2. gold 必须可入库、可 `git diff`：日期等易变行归一为占位符（显式白名单，
   不许含糊「忽略差异」）。VERMES_HOME 隔离到临时目录，不读用户真实记忆。
3. 场景矩阵与 `reports/ab-corpus/v1/corpus.yaml` 的 fixed_context 同一套维度。

用法
----
    python3 scripts/s2_snapshot.py --write-gold     # 生成/覆盖 reports/s2/gold/
    python3 scripts/s2_snapshot.py --check          # 重生成并与 gold 比对（S2.1 门）
    python3 scripts/s2_snapshot.py --out DIR        # 写到别处（对比用）

退出码：0 通过 / 1 比对失败或写失败 / 2 用法错误
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Optional
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
GOLD_DIR = ROOT / "reports" / "s2" / "gold"

# ── 显式易变行白名单（工单 §4.3：不许含糊忽略）────────────────────────
# 生成时归一，比对时也按同一套归一 —— 两边用同一个函数，避免「生成宽、比对严」。
_VOLATILE_NORMALIZERS: list[tuple[re.Pattern, str]] = [
    # 日期行（vermes_time.now → strftime('%A, %B %d, %Y')）—— 跨日会飘
    (re.compile(r"^Conversation started: .+$", re.M), "Conversation started: {{DATE}}"),
    # Session ID（仅 pass_session_id 时出现）
    (re.compile(r"^Session ID: .+$", re.M), "Session ID: {{SESSION_ID}}"),
]


def normalize_volatile(text: str) -> str:
    for pat, repl in _VOLATILE_NORMALIZERS:
        text = pat.sub(repl, text)
    return text


# ── 场景矩阵（pairwise，与 A/B 语料 fixed_context 同维）──────────────────
# model 4 × platform 4 × toolset 3 全组合 48；下表 16 条覆盖全部两两组合。
# 改这张表 = 改 gold 指纹，必须同步升 reports/s2/ 版本目录。
MODELS = ("qwen-max", "gpt-4o", "claude-sonnet", "local-qwen", "gemini-2.5-pro")
PLATFORMS = ("cli", "gateway", "feishu", "telegram")
TOOLSETS: dict[str, tuple[str, ...]] = {
    "minimal": ("web_search",),
    "standard": (
        "web_search", "memory", "read_file", "write_file", "terminal", "skill_manage",
    ),
    "full": (
        "web_search", "memory", "read_file", "write_file", "terminal", "skill_manage",
        "session_search", "image_generate", "kanban_show", "computer_use",
        "scholarforge_write", "scholarforge_search",
    ),
}

# (id, model, platform, toolset, system_message|None)
# system_message 非空 → context 段非空（补 QClaw/Hermes 指出的 context 零覆盖盲区）。
SCENARIOS: list[tuple[str, str, str, str, Optional[str]]] = [
    ("S01", "qwen-max", "cli", "minimal", None),
    ("S02", "qwen-max", "gateway", "standard", None),
    ("S03", "qwen-max", "feishu", "full", None),
    ("S04", "qwen-max", "telegram", "full", None),
    ("S05", "gpt-4o", "cli", "standard", None),
    ("S06", "gpt-4o", "gateway", "full", None),
    ("S07", "gpt-4o", "feishu", "minimal", None),
    ("S08", "gpt-4o", "telegram", "standard", None),
    ("S09", "claude-sonnet", "cli", "full", None),
    ("S10", "claude-sonnet", "gateway", "minimal", None),
    # S11：context 层探针 —— 唯一带 system_message 的场景，钉住 context 段非空
    ("S11", "claude-sonnet", "feishu", "standard",
     "S2 context-layer probe: caller-supplied system_message must land in context."),
    ("S12", "claude-sonnet", "telegram", "minimal", None),
    ("S13", "local-qwen", "cli", "full", None),
    ("S14", "local-qwen", "gateway", "standard", None),
    ("S15", "local-qwen", "feishu", "minimal", None),
    ("S16", "local-qwen", "telegram", "full", None),
    # S17：model 维探针 —— gemini 命中 google_model（patterns: gemini/gemma），
    # 与 qwen-max/claude-sonnet/local-qwen 的 stable 必须不同，否则 model 分支零覆盖。
    ("S17", "gemini-2.5-pro", "gateway", "standard", None),
]

# 覆盖口径（QClaw/Hermes 2026-09-23，勿误读）：
# - S01–S16 是 pairwise 主矩阵（对齐 A/B 语料 fixed_context 四模型）；S17 是 model 维探针。
# - `model_affinity` 只在 gpt/gemini/grok 等模式命中时才分流（openai_model/google_model）。
#   qwen-max / claude-sonnet / local-qwen **不命中任何 model processor** → 三者 stable 全同
#   （S02≡S14、S04≡S16、S09≡S13）。唯一 stable 指纹以 manifest.unique_stable_fingerprints 为准，
#   **不得**写成「N 条独立护栏」。
# - S11 用 system_message 把 context 段撑非空；迁块进 context 层前以此为护栏。
# - 其余场景 context 仍空（skip_context_files + 无 layer:context 的 YAML）。


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _make_tool_defs(*names: str) -> list:
    return [
        {
            "type": "function",
            "function": {
                "name": n,
                "description": n,
                "parameters": {"type": "object", "properties": {}},
            },
        }
        for n in names
    ]


def build_one(
    sid: str,
    model: str,
    platform: str,
    toolset: str,
    system_message: Optional[str] = None,
) -> dict[str, str]:
    """真 AIAgent + mock 网络/工具，产出三段 prompt（volatile 已归一）。

    ``system_message`` 非空 → 落 context 段（S11 context 层探针）。
    """
    os.environ["VERMES_SESSION_ID"] = f"s2-gold-{sid.lower()}"
    from run_agent import AIAgent
    from agent.system_prompt import build_system_prompt_parts

    tools = TOOLSETS[toolset]
    with (
        patch("run_agent.get_tool_definitions", return_value=_make_tool_defs(*tools)),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
    ):
        agent = AIAgent(
            api_key="s2-gold-key-0000000000",
            base_url="https://openrouter.ai/api/v1",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
            model=model,
            platform=platform,
        )
        parts = build_system_prompt_parts(agent, system_message=system_message)
    return {
        "stable": parts.get("stable", ""),
        "context": parts.get("context", ""),
        "volatile": normalize_volatile(parts.get("volatile", "")),
    }


def snapshot_all(home: Path) -> dict:
    """在隔离 VERMES_HOME 下跑全部场景，返回 manifest 结构。"""
    os.environ["VERMES_HOME"] = str(home)
    # 清掉可能从父进程渗进来的会话态（工单 §4.3：不读用户真实记忆）
    for key in (
        "VERMES_CRON_SESSION",
        "VERMES_EXEC_ASK",
        "VERMES_INTERACTIVE",
        "VERMES_GATEWAY_SESSION",
    ):
        os.environ.pop(key, None)

    scenarios = []
    for sc in SCENARIOS:
        sid, model, platform, toolset = sc[:4]
        system_message = sc[4] if len(sc) > 4 else None
        parts = build_one(sid, model, platform, toolset, system_message=system_message)
        scenarios.append(
            {
                "id": sid,
                "model": model,
                "platform": platform,
                "toolset": toolset,
                "tools": list(TOOLSETS[toolset]),
                "system_message": system_message,
                "sha256": {k: _sha256(v) for k, v in parts.items()},
                "bytes": {k: len(v.encode("utf-8")) for k, v in parts.items()},
                "parts": parts,
            }
        )
    unique_stable = len({s["sha256"]["stable"] for s in scenarios})
    unique_context = len({s["sha256"]["context"] for s in scenarios})
    return {
        "schema": "vermes.s2-gold/v1",
        "version": "gold",
        "scenario_count": len(scenarios),
        # 覆盖口径（勿把 scenario_count 当独立护栏数）：
        "unique_stable_fingerprints": unique_stable,
        "unique_context_fingerprints": unique_context,
        "coverage_note": (
            "S01-S16 为 pairwise 主矩阵；S17 为 model 维探针（gemini 命中 google_model）；"
            "S11 带 system_message 使 context 非空。"
            "qwen-max/claude-sonnet/local-qwen 不命中 model_affinity 时 stable 全同。"
            "见工单 §9c。"
        ),
        "dimensions": {
            "model": list(MODELS),
            "platform": list(PLATFORMS),
            "toolset": list(TOOLSETS),
        },
        "volatile_normalizers": [p.pattern for p, _ in _VOLATILE_NORMALIZERS],
        "scenarios": scenarios,
    }


def write_tree(snap: dict, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_scenarios = []
    for sc in snap["scenarios"]:
        sid = sc["id"]
        for tier in ("stable", "context", "volatile"):
            (out_dir / f"{sid}.{tier}.txt").write_text(sc["parts"][tier], encoding="utf-8")
        manifest_scenarios.append({k: v for k, v in sc.items() if k != "parts"})
    manifest = {k: v for k, v in snap.items() if k != "scenarios"}
    manifest["scenarios"] = manifest_scenarios
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def load_gold(out_dir: Path) -> dict:
    """从目录读回 gold（manifest + 三段文本），结构与 snapshot_all 对齐。"""
    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    scenarios = []
    for sc in manifest["scenarios"]:
        parts = {
            tier: (out_dir / f"{sc['id']}.{tier}.txt").read_text(encoding="utf-8")
            for tier in ("stable", "context", "volatile")
        }
        scenarios.append({**sc, "parts": parts})
    return {**manifest, "scenarios": scenarios}


def compare(new: dict, gold: dict) -> list[str]:
    """逐场景三段比对。stable/context 逐字节；volatile 已归一后也逐字节。

    返回差异行列表；空 = 全等。
    """
    diffs: list[str] = []
    gold_by_id = {s["id"]: s for s in gold["scenarios"]}
    for sc in new["scenarios"]:
        g = gold_by_id.get(sc["id"])
        if g is None:
            diffs.append(f"{sc['id']}: gold 中不存在该场景")
            continue
        for tier in ("stable", "context", "volatile"):
            a, b = sc["parts"][tier], g["parts"][tier]
            if a == b:
                continue
            if tier == "stable":
                diffs.append(f"{sc['id']}.stable: 逐字不等（硬门槛） sha new={_sha256(a)[:12]} gold={_sha256(b)[:12]}")
            else:
                # 找到第一处差异位置，便于排障
                i = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
                diffs.append(
                    f"{sc['id']}.{tier}: 不等 @byte {i} "
                    f"new={_sha256(a)[:12]} gold={_sha256(b)[:12]} "
                    f"new_len={len(a)} gold_len={len(b)}"
                )
        for tier in ("stable", "context", "volatile"):
            if _sha256(sc["parts"][tier]) != sc["sha256"].get(tier) and tier in sc.get("sha256", {}):
                pass  # manifest sha 以文件为准，比对用文件内容
    return diffs


def main() -> int:
    ap = argparse.ArgumentParser(description="S2 注入等价 gold 快照")
    ap.add_argument("--write-gold", action="store_true", help="生成 reports/s2/gold/")
    ap.add_argument("--check", action="store_true", help="重生成并与 gold 比对")
    ap.add_argument("--out", default=None, help="输出目录（默认 reports/s2/gold）")
    args = ap.parse_args()
    if not (args.write_gold or args.check):
        print("用法：--write-gold 或 --check", file=sys.stderr)
        return 2

    out_dir = Path(args.out) if args.out else GOLD_DIR
    with tempfile.TemporaryDirectory(prefix="s2-gold-home-") as td:
        snap = snapshot_all(Path(td))

    if args.write_gold:
        write_tree(snap, out_dir)
        print(f"[s2] gold → {out_dir}（{snap['scenario_count']} 场景 × 3 段）")
        return 0

    # --check
    if not (out_dir / "manifest.json").exists():
        print(f"[s2] 缺 gold：{out_dir}/manifest.json —— 先跑 --write-gold", file=sys.stderr)
        return 1
    gold = load_gold(out_dir)
    diffs = compare(snap, gold)
    if diffs:
        print(f"[s2] 比对失败 {len(diffs)} 处：")
        for d in diffs:
            print(f"  - {d}")
        return 1
    print(f"[s2] 比对通过：{snap['scenario_count']} 场景 × 3 段与 gold 逐字相同")
    return 0


if __name__ == "__main__":
    sys.exit(main())
