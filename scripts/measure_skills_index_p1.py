#!/usr/bin/env python3
"""P1 技能索引降级 —— auto 模式字节收益实测（M6 后续）。

只读测量，不改 config、不改 gateway。默认在真实 `~/.vermes/skills` 上跑。

用法：
    python3 scripts/measure_skills_index_p1.py [--skills-dir DIR] [--json OUT.json]

产出：
    - full / demoted prompt 字节数与差值
    - 按类目：技能数 + 描述字节 + 是否降级
    - auto 门控在「代码目录 / 非代码目录」下的实际启用结果
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

# 仓库根（本文件 parents[1]）
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.prompt_builder import (  # noqa: E402
    _NON_CODING_SKILL_CATEGORIES,
    _demoted_categories,
    build_skills_system_prompt,
    is_coding_dir,
    resolve_compact_skill_categories,
)
from vermes_constants import get_skills_dir  # noqa: E402


def _parse_index_section(prompt: str) -> list[str]:
    m = re.search(r"<available_skills>\n(.*?)\n</available_skills>", prompt, re.S)
    if not m:
        return []
    return [ln for ln in m.group(1).splitlines() if ln.strip()]


def _category_stats_from_index(lines: list[str]) -> dict[str, dict]:
    """从渲染 index 行还原类目统计；识别 `cat [names only]: a, b` 与 `cat: desc`。"""
    stats: dict[str, dict] = {}
    current = None
    for ln in lines:
        stripped = ln.strip()
        if ln.startswith("    - "):
            if current and current in stats:
                body = stripped[2:]
                if ": " in body:
                    name, desc = body.split(": ", 1)
                else:
                    name, desc = body, ""
                stats[current]["names"].append(name)
                stats[current]["desc_bytes"] += len(desc.encode())
                stats[current]["line_bytes"] += len(ln.encode())
            continue
        if not ln.startswith("  ") or ln.startswith("    "):
            continue
        m_only = re.match(r"^(.+?) \[names only\]:\s*(.*)$", stripped)
        if m_only:
            current = m_only.group(1)
            names = [p.strip() for p in m_only.group(2).split(",") if p.strip()]
            stats[current] = {
                "names": names,
                "desc_bytes": 0,
                "names_only": True,
                "line_bytes": len(ln.encode()),
            }
            continue
        # `cat:` 或 `cat: category description`
        m_cat = re.match(r"^([^:]+):\s*(.*)$", stripped)
        if m_cat:
            current = m_cat.group(1).strip()
            stats[current] = {
                "names": [],
                "desc_bytes": 0,
                "names_only": False,
                "line_bytes": len(ln.encode()),
                "cat_header": m_cat.group(2).strip(),
            }
            continue
        current = None
    return stats


def _build_prompt(platform_hint: str, tools, compact) -> str:
    import agent.prompt_builder as pb
    pb._SKILLS_PROMPT_CACHE.clear()
    pb._LAST_COMPACT_SKILL_CATEGORIES = None
    os.environ["VERMES_PLATFORM"] = platform_hint
    return build_skills_system_prompt(available_tools=tools, compact_categories=compact)


def measure(skills_dir: Path | None = None) -> dict:
    if skills_dir is not None:
        os.environ["VERMES_HOME"] = str(skills_dir.parent)
    real_skills = Path(skills_dir) if skills_dir else Path(get_skills_dir())

    import agent.prompt_builder as pb
    pb._SKILLS_PROMPT_CACHE.clear()
    pb._LAST_COMPACT_SKILL_CATEGORIES = None
    snap = pb._skills_prompt_snapshot_path()
    if snap.exists():
        snap.unlink()

    tools_typical = {"skill_view", "skills_list", "skill_manage", "terminal", "file"}
    deny = frozenset(_NON_CODING_SKILL_CATEGORIES)

    # 当前环境（不强行改 platform）
    env_platform = os.environ.get("VERMES_PLATFORM") or ""
    full = _build_prompt(env_platform, tools_typical, None)
    demoted = _build_prompt(env_platform, tools_typical, deny)
    # macos 全量库：很多 creative 技能标 platforms:[macos,linux]，空 platform 可能被过滤
    full_mac = _build_prompt("macos", tools_typical, None)
    demoted_mac = _build_prompt("macos", tools_typical, deny)

    # auto 门控：monkeypatch load_config → auto
    code_dir = Path(tempfile.mkdtemp(prefix="p1-code-"))
    (code_dir / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    plain_dir = Path(tempfile.mkdtemp(prefix="p1-plain-"))
    import vermes_cli.config as cfg_mod
    real_load = cfg_mod.load_config
    try:
        cfg_mod.load_config = lambda *a, **k: {"agent": {"compact_skill_categories": "auto"}}
        auto_coding = resolve_compact_skill_categories(code_dir)
        auto_plain = resolve_compact_skill_categories(plain_dir)
        cfg_mod.load_config = lambda *a, **k: {"agent": {"compact_skill_categories": "off"}}
        off_coding = resolve_compact_skill_categories(code_dir)
        cfg_mod.load_config = lambda *a, **k: {"agent": {"compact_skill_categories": "flase"}}
        bad_coding = resolve_compact_skill_categories(code_dir)
    finally:
        cfg_mod.load_config = real_load
        auto_real_config = resolve_compact_skill_categories(code_dir)

    def _pack(prompt_full, prompt_dem, label):
        fl = _parse_index_section(prompt_full)
        dl = _parse_index_section(prompt_dem)
        fs = _category_stats_from_index(fl)
        ds = _category_stats_from_index(dl)
        full_b, dem_b = len(prompt_full.encode()), len(prompt_dem.encode())
        idx_f = sum(len(x.encode()) + 1 for x in fl)
        idx_d = sum(len(x.encode()) + 1 for x in dl)
        demoted_cats = sorted(c for c, s in ds.items() if s.get("names_only"))
        dem_desc_bytes = sum(fs[c]["desc_bytes"] for c in demoted_cats if c in fs)
        return {
            "label": label,
            "full_prompt_bytes": full_b,
            "demoted_prompt_bytes": dem_b,
            "prompt_savings_bytes": full_b - dem_b,
            "prompt_savings_pct": round((full_b - dem_b) / full_b * 100, 2) if full_b else 0.0,
            "index_bytes_full": idx_f,
            "index_bytes_demoted": idx_d,
            "index_savings_bytes": idx_f - idx_d,
            "index_savings_pct": round((idx_f - idx_d) / idx_f * 100, 2) if idx_f else 0.0,
            "skill_lines_full": len([x for x in fl if x.startswith("    - ")]),
            "skill_lines_demoted": len([x for x in dl if x.startswith("    - ")]),
            "names_only_categories": demoted_cats,
            "names_only_category_count": len(demoted_cats),
            "demoted_categories_desc_bytes_in_full": dem_desc_bytes,
            "categories_full": len(fs),
            "index_category_detail": {
                cat: {
                    "skill_count": len(s["names"]),
                    "desc_bytes": s["desc_bytes"],
                    "names_only": s["names_only"],
                }
                for cat, s in sorted(fs.items(), key=lambda kv: -kv[1]["desc_bytes"])
            },
        }

    env_pack = _pack(full, demoted, "current_env_platform")
    mac_pack = _pack(full_mac, demoted_mac, "platform=macos")

    cat_counts: dict[str, int] = defaultdict(int)
    if real_skills.is_dir():
        for md in real_skills.rglob("SKILL.md"):
            rel = md.relative_to(real_skills)
            parts = rel.parts
            cat = "/".join(parts[:-2]) if len(parts) > 2 else (parts[0] if parts else "general")
            cat_counts[cat.split("/", 1)[0]] += 1

    return {
        "skills_dir": str(real_skills),
        "env_platform_hint": env_platform,
        "deny_list": sorted(deny),
        "current_config_auto_enabled_in_coding_dir": auto_real_config is not None,
        "gate_simulation": {
            "auto_in_coding_dir": auto_coding is not None,
            "auto_in_plain_dir": auto_plain is not None,
            "auto_coding_deny_size": len(auto_coding) if auto_coding else 0,
            "off_in_coding_dir": off_coding,
            "unknown_value_failsafe_off": bad_coding,
        },
        "env_platform": env_pack,
        "macos_platform": mac_pack,
        "fs_skill_files_by_parent": dict(sorted(cat_counts.items(), key=lambda kv: -kv[1])),
        "recommendation_inputs": {
            "primary_savings_pct": mac_pack["prompt_savings_pct"],
            "primary_savings_bytes": mac_pack["prompt_savings_bytes"],
            "note": "以 platform=macos 为桌面主场景口径；env 空 platform 可能过滤 platforms 标签技能",
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skills-dir", type=Path, default=None)
    ap.add_argument("--json", type=Path, default=None)
    args = ap.parse_args()
    data = measure(args.skills_dir)
    print(json.dumps(data, ensure_ascii=False, indent=2))
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
