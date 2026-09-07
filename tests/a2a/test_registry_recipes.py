"""ACP Registry → recipe 批量 dump 测试（请神收尾 B 部分 · P2 启动器）。

校验：生成器把 registry.json 里 39 个 agent 转成可 ``load_recipe`` 的 YAML，
跳过手写 3 条核心（避免覆盖用户定版），分发类型映射正确（npx/uvx/binary），
且递归加载能把生成食谱与手写核心一起端上来。
"""

from __future__ import annotations

import json
from pathlib import Path

from vermes_cli.a2a.recipes.generate_from_registry import (
    CURATED_NAMES,
    _BINARY_PLATFORM_PRIORITY,
    generate,
)
from vermes_cli.a2a.recipes.loader import (
    load_all_recipes,
    load_recipe,
    find_recipe,
    RECIPES_DIR,
)

REGISTRY_PATH = Path("/tmp/acp_registry.json")


def _registry_data():
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def test_generate_writes_valid_recipes(tmp_path: Path) -> None:
    data = _registry_data()
    written = generate(data, tmp_path)
    # 39 个 agent - 1 个手写同名(copilot/codex-acp/claude-agent-acp 中命中 codex-acp) = 38
    assert len(written) == 38
    npx = uvx = binary = 0
    for path in written:
        rc = load_recipe(path)  # 必须能加载，否则生成器自身校验有漏
        assert rc.is_acp
        assert rc.transport == "acp"
        assert rc.spawn_command, f"{rc.name}: empty spawn_command"
        assert rc.provider == f"acp-{rc.name}", f"{rc.name}: provider 必须以 acp- 前缀（T0 泛型路由依赖）"
        assert rc.auth.env_var is None, f"{rc.name}: registry 无 auth，不应瞎填"
        # 分发类型映射
        if rc.entry_point == "npx":
            npx += 1
            assert rc.args, f"{rc.name}: npx 至少含 package"
        elif rc.entry_point == "uvx":
            uvx += 1
            assert rc.args, f"{rc.name}: uvx 至少含 package"
        else:  # binary
            binary += 1
            assert rc.entry_point.startswith("./") or "/" in rc.entry_point
            assert "需先" in rc.description, f"{rc.name}: binary 应注明需下载解包"
    # 与 registry 实测分布一致：npx 21 / uvx 2 / binary 18，但 codex-acp(npx)被跳过
    assert npx == 20, f"npx 期望 20（21-1跳过），实得 {npx}"
    assert uvx == 2
    assert binary == 16, f"binary 期望 16（18-2 同含 npx 的 kilo/sigit 走 npx），实得 {binary}"


def test_generate_skips_curated_names(tmp_path: Path) -> None:
    data = _registry_data()
    written = generate(data, tmp_path)
    written_names = {p.stem for p in written}
    # 手写 3 条核心绝不该出现在生成目录（否则会覆盖用户定版）
    assert not (written_names & CURATED_NAMES), f"生成目录误含手写核心: {written_names & CURATED_NAMES}"
    # codex-acp 既是 registry id 又是手写核心 name → 必须被跳过
    assert "codex-acp" not in written_names


def test_recursive_loader_includes_generated_and_curated() -> None:
    all_recipes = load_all_recipes(RECIPES_DIR, recursive=True)
    names = {r.name for r in all_recipes}
    # 手写 3 条核心仍在
    assert {"copilot", "codex-acp", "claude-agent-acp"} <= names
    # registry 生成的 38 条也端上来了（含 claude-acp / github-copilot-cli 等）
    assert "claude-acp" in names
    assert "github-copilot-cli" in names
    assert "amp-acp" in names
    assert "fast-agent" in names
    assert len(all_recipes) >= 41  # 3 手写 + 38 生成


def test_find_recipe_recursive_supports_ascension_and_dispatch() -> None:
    """登堂闭环回归（2026-09-07）：GET /agents/recipes 递归返回 41 条，
    register-profile（chat.py）与 ACP dispatch（transports_acp.py）的
    find_recipe 必须同样递归，否则 registry/ 生成的 recipe 登堂/dispatch 必 404。
    """
    # 覆盖三个传输分发类型：npx（claude-acp）/ binary（cursor）/ uvx（如 amp-acp）
    for name in ("claude-acp", "cursor", "junie"):
        rc = find_recipe(name, RECIPES_DIR, recursive=True)
        assert rc is not None, f"登堂/dispatch 找不到 registry/ 里的 {name!r}"
        assert rc.is_acp and rc.transport == "acp"


def test_find_recipe_registry_recipes_roundtrip() -> None:
    """登堂闭环守卫（2026-09-07）：列表能展示的（recursive）必须登堂找得到。

    若 find_recipe 不递归而 load_all_recipes 递归，GET /agents/recipes 展示的
    41 张卡片里 registry/ 38 张点「登堂」都会 404 —— 列表与登堂两个端点
    口径必须一致。
    """
    listed = load_all_recipes(RECIPES_DIR, recursive=True)
    for rc in listed:
        if not rc.is_acp:
            continue
        found = find_recipe(rc.name, RECIPES_DIR, recursive=True)
        assert found is not None, (
            f"列表可见但登堂找不到：{rc.name!r}（recursive find_recipe 失配）"
        )


def test_find_recipe_recursive_covers_full_registry_surface() -> None:
    # 闭环回归（2026-09-07）：GET /agents/recipes 暴露的全部 41 个名字，
    # 登堂端点 find_recipe(..., recursive=True) 必须全能命中——
    # 否则前端封神榜卡片点了必 404（列表可见 ≠ 可登堂）。
    listed = load_all_recipes(RECIPES_DIR, recursive=True)
    listed_names = {r.name for r in listed if r.is_acp}
    assert len(listed_names) >= 41
    missing = [
        n for n in sorted(listed_names)
        if find_recipe(n, RECIPES_DIR, recursive=True) is None
    ]
    assert not missing, f"登堂找不到这些 recipe（列表可见但不可登堂）: {missing}"


def test_curated_codex_pinned_version_intact() -> None:
    # 生成器绝不许覆盖用户钉死的 Zed 适配器版本 @1.8.0
    rc = load_recipe(RECIPES_DIR / "codex-acp.yaml")
    assert rc.spawn_command == ["npx", "@agentclientprotocol/codex-acp@1.8.0"]


def test_claude_acp_registry_recipe_uses_newer_zed_adapter() -> None:
    # 生成的 claude-acp 用 registry 当前版本（与手写 claude-agent-acp @0.73.0 区分）
    rc = load_recipe(RECIPES_DIR / "registry" / "claude-acp.yaml")
    assert rc.entry_point == "npx"
    assert rc.args and rc.args[0].startswith("@agentclientprotocol/claude-agent-acp@")


def test_binary_recipes_preserve_registry_args(tmp_path: Path) -> None:
    """P1 回归守卫：binary 类 recipe 必须透传 registry 真源的 args。

    初版 _build_spawn 的 binary 分支写死 ``return cmd, [], "binary"``，导致
    12/16 个 binary recipe 丢了启动 ACP 模式的关键参数（cursor/kimi 的 ['acp']、
    junie 的 ['--acp=true'] 等），spawn 出来不是 ACP 模式、握手必失败。本条
    逐条比对生成 recipe 的 args 与 registry 真源，杜绝再次静默丢参。
    """
    data = _registry_data()
    written = generate(data, tmp_path)
    written_by_name = {p.stem: p for p in written}
    reg_agents = {a["id"]: a for a in data["agents"]}
    for name, path in written_by_name.items():
        rc = load_recipe(path)
        if not rc.entry_point.startswith("./"):  # 仅校验 binary 类
            continue
        agent = reg_agents[name]
        b = (agent.get("distribution") or {}).get("binary") or {}
        expected_args: list[str] = []
        for plat in _BINARY_PLATFORM_PRIORITY:
            node = b.get(plat) or {}
            if (node.get("cmd") or "").strip():
                expected_args = list(node.get("args") or [])
                break
        assert rc.args == expected_args, (
            f"{name}: binary args 丢失/错配！生成={rc.args} 真源={expected_args}"
        )


def test_cursor_and_junie_binary_args() -> None:
    """针对审计点名的代表 agent 做高信号断言：丢 args = 登堂必失败。"""
    cur = load_recipe(RECIPES_DIR / "registry" / "cursor.yaml")
    assert cur.entry_point.startswith("./")
    assert cur.args == ["acp"], f"cursor 必须带 ['acp'] 才能进 ACP 模式，实得 {cur.args}"
    jun = load_recipe(RECIPES_DIR / "registry" / "junie.yaml")
    assert jun.args == ["--acp=true"], f"junie 必须带 ['--acp=true']，实得 {jun.args}"


def test_find_recipe_recursive_covers_display_surface() -> None:
    """回归守卫（2026-09-07）：列表展示面(41)与登堂可命中面必须一致。

    GET /agents/recipes 用 recursive=True 展示全部 recipe；若 register-profile / dispatch
    的 find_recipe 不递归，则前端 38/41 卡片点「登堂」会 404。此测试锁住：凡是能被列表
    展示的 registry recipe，递归 find_recipe 都必须命中。
    """
    displayed = load_all_recipes(RECIPES_DIR, recursive=True)
    for rc in displayed:
        found = find_recipe(rc.name, RECIPES_DIR, recursive=True)
        assert found is not None, f"展示面含 {rc.name!r} 但递归 find_recipe 找不到（登堂必 404）"


def test_find_recipe_recursive_supports_registry_ascension() -> None:
    """登堂闭环回归（2026-09-07）：前端封神榜 41 张卡片里 38 张来自 registry/，
    点登堂走 POST /api/agents/register-profile → find_recipe(recipe_name, RECIPES_DIR)
    → 若不递归会 404。register-profile 与 dispatch 均已改 recursive=True，
    此处锁住「registry/ 里任意 recipe 名都能被递归 find_recipe 命中」。
    """
    all_recipes = load_all_recipes(RECIPES_DIR, recursive=True)
    acp_recipes = [r for r in all_recipes if r.is_acp]
    # 与 GET /agents/recipes 返回面一致：41 条全部可登堂（顶层 3 + registry 38）
    assert len(acp_recipes) >= 41
    # 每条在列表里出现的 recipe，登堂时 find_recipe(recursive=True) 必须能命中
    for r in acp_recipes:
        found = find_recipe(r.name, RECIPES_DIR, recursive=True)
        assert found is not None, f"登堂会 404：列表展示 {r.name!r} 但 find_recipe 找不到"
        assert found.name == r.name
