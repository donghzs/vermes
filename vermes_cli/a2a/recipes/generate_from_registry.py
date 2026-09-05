"""Dump the ACP Registry into Vermes agent recipes (请神收尾 B 部分 · P2 启动器).

Vermes 的「目录即 agent 食谱」约定（见 ``schema.py`` / ``loader.py``）：每个
``recipes/*.yaml`` 描述一个可经 ACP transport 拉入神魔堂的异构 agent。手写 3 条
核心（copilot / codex-acp / claude-agent-acp）已由用户钉死；本脚本把
https://cdn.agentclientprotocol.com/registry/v1/latest/registry.json 里的其余
agent 批量 dump 成 recipe，作为 P2 起步器——用户拿到的不是 39 张白纸，而是
39 份可直接 ``load_recipe`` 的半成品，再按本地环境微调（尤其是 binary 类需先
下载解包）。

设计要点（均来自对 registry 真源的核读，非猜测）：
- distribution 分三类：``npx``(21) / ``binary``(18) / ``uvx``(2)。
- npx/uvx：``entry_point`` 分别为 ``npx`` / ``uvx``，``args = [package] + args``。
- binary：registry 只给各平台 ``archive/cmd/sha256``，没有可直接 spawn 的命令，
  故 ``entry_point`` 取代表性平台（darwin-aarch64 优先）的 ``cmd``（相对路径，
  需先下载解包），并在 description 注明「需下载解包」。**但各平台节点普遍带
  ``args``（cursor/kimi 的 ``['acp']``、junie 的 ``['--acp=true']`` 等），是
  启动 ACP 模式的关键参数，必须透传，**绝不**写死空列表**。
- 同时含 binary+npx 的 agent（kilo / sigit）：优先 npx（更便携）。
- registry **无 auth 字段**（"auth" 命中只是 ``authors`` 子串误报），故 auth
  留空，由「登堂」授权弹窗按 agent 要求填。
- registry **无 capabilities**，留空（诚实，不瞎编）。
- 手写 3 条的 name（copilot / codex-acp / claude-agent-acp）会被跳过，避免覆盖
  用户定版；其余以 registry ``id`` 为 recipe name，provider = ``acp-<id>``
  （保证走 T0 泛型路由 ``acp-*`` → AcpAgentTransportBase）。
- 生成到独立子目录 ``recipes/registry/``，绝不进 ``RECIPES_DIR`` 顶层，避免与
  手写 3 条同名文件互相覆盖。

用法：
    python -m vermes_cli.a2a.recipes.generate_from_registry \
        [--registry /path/to/registry.json] [--out vermes_cli/a2a/recipes/registry]
不传 ``--registry`` 时尝试从 CDN 拉取；不传 ``--out`` 时落到包内 ``registry/``。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any, Iterable

import yaml

from .schema import RecipeConfig

REGISTRY_URL = "https://cdn.agentclientprotocol.com/registry/v1/latest/registry.json"

# 手写 3 条的核心 name——生成时跳过，绝不覆盖用户定版
CURATED_NAMES = {"copilot", "codex-acp", "claude-agent-acp"}

# 二进制 agent 的代表性平台优先级（取首个命中的 cmd 作 entry_point）
_BINARY_PLATFORM_PRIORITY = (
    "darwin-aarch64",
    "darwin-x86_64",
    "linux-x86_64",
    "linux-aarch64",
    "windows-x86_64",
    "windows-aarch64",
)


def load_registry_data(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"registry file not found: {p}")
    return json.loads(p.read_text(encoding="utf-8"))


def fetch_registry_data(url: str = REGISTRY_URL) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310 (fixed CDN)
        return json.loads(resp.read().decode("utf-8"))


def _build_spawn(agent: dict[str, Any]) -> tuple[str, list[str], str]:
    """Return (entry_point, args, dist_type) for an agent's distribution.

    npx/uvx → entry_point 为对应 runner，args = [package] + registry args；
    binary → entry_point 取代表性平台的 cmd（相对路径，需先下载解包）；
    同时含 binary+npx 的 agent 优先 npx（更便携）。
    """
    dist = agent.get("distribution") or {}
    if "npx" in dist:
        nx = dist["npx"] or {}
        pkg = (nx.get("package") or "").strip()
        extra = [str(a) for a in (nx.get("args") or [])]
        args = ([pkg] + extra) if pkg else list(extra)
        return "npx", args, "npx"
    if "uvx" in dist:
        ux = dist["uvx"] or {}
        pkg = (ux.get("package") or "").strip()
        extra = [str(a) for a in (ux.get("args") or [])]
        args = ([pkg] + extra) if pkg else list(extra)
        return "uvx", args, "uvx"
    if "binary" in dist:
        b = dist["binary"] or {}
        for plat in _BINARY_PLATFORM_PRIORITY:
            node = b.get(plat) or {}
            cmd = (node.get("cmd") or "").strip()
            if cmd:
                # ⚠️ 关键：各平台节点普遍带 args（cursor/kimi 的 ['acp']、
                # junie 的 ['--acp=true'] 等），是启动 ACP 模式的关键参数，
                # 必须透传，绝不能写死空列表（初版 P1 bug：12/16 个 binary
                # recipe 因丢 args 而 spawn 出非 ACP 模式，握手必失败）。
                return cmd, list(node.get("args") or []), "binary"
    return "", [], "unknown"


def _recipe_dict(agent: dict[str, Any], reg_version: str) -> dict[str, Any]:
    entry_point, args, dist_type = _build_spawn(agent)
    description = (agent.get("description") or "").strip()
    if dist_type == "binary":
        description = description + "（binary：需先从 release 下载解包后运行 entry_point）"
    return {
        "name": agent["id"],
        "version": str(agent.get("version") or "0.0.0"),
        "description": description,
        "transport": "acp",
        "entry_point": entry_point,
        "args": args,
        "provider": f"acp-{agent['id']}",
        "auth": {"scheme": "apikey", "env_var": None},
        "capabilities": [],
        "fingerprint": {
            "cli": [entry_point] if entry_point and dist_type != "binary" else [],
        },
    }


def _comment_header(agent: dict[str, Any], reg_version: str) -> str:
    today = _dt.date.today().isoformat()
    lines = [
        f"# Auto-generated from ACP Registry v{reg_version} ({today})",
        f"# source: {REGISTRY_URL}",
        f"# agent id: {agent['id']}",
        f"# display name: {agent.get('name', '')}",
    ]
    if agent.get("website"):
        lines.append(f"# website: {agent['website']}")
    if agent.get("repository"):
        lines.append(f"# repository: {agent['repository']}")
    if agent.get("license"):
        lines.append(f"# license: {agent['license']}")
    dist = agent.get("distribution") or {}
    lines.append(f"# distribution: {','.join(dist.keys())}")
    return "\n".join(lines)


def generate(
    registry_data: dict[str, Any],
    out_dir: str | Path,
    *,
    skip_names: Iterable[str] | None = None,
) -> list[Path]:
    """Write one YAML per registry agent into ``out_dir``.

    Returns the list of written file paths. Agents whose recipe ``name`` is in
    ``skip_names`` (default: CURATED_NAMES) are skipped to avoid clobbering
    hand-tuned recipes.
    """
    skip = set(skip_names) if skip_names is not None else set(CURATED_NAMES)
    agents = registry_data.get("agents") or []
    reg_version = str(registry_data.get("version") or "?")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    skipped = 0
    for agent in agents:
        name = agent.get("id")
        if not name:
            continue
        if name in skip:
            skipped += 1
            continue
        body = _recipe_dict(agent, reg_version)
        # 生成即校验：任何不符合 schema 的都被拦下，不会写出坏文件
        RecipeConfig.from_dict(body, source=f"<registry:{name}>")
        text = (
            _comment_header(agent, reg_version)
            + "\n"
            + yaml.safe_dump(body, sort_keys=False, allow_unicode=True)
        )
        path = out / f"{name}.yaml"
        path.write_text(text, encoding="utf-8")
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Dump ACP Registry into Vermes recipes")
    ap.add_argument("--registry", default=None, help="Path to registry.json (fetched from CDN if omitted)")
    ap.add_argument("--out", default=None, help="Output dir (default: <pkg>/recipes/registry)")
    args = ap.parse_args(argv)

    if args.registry:
        data = load_registry_data(args.registry)
    else:
        print(f"fetching {REGISTRY_URL} ...", file=sys.stderr)
        data = fetch_registry_data()

    out_dir = args.out or (Path(__file__).resolve().parent / "registry")
    written = generate(data, out_dir)
    print(
        f"registry v{data.get('version')}: wrote {len(written)} recipes -> {out_dir} "
        f"(skipped {len(CURATED_NAMES)} curated)",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
