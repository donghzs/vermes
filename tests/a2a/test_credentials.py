"""授权 Key 持久化 · 凭据库单元测试（⑭ ⑭-hardening 闭环）。

覆盖核心场景：
- save → get 跨「进程重启模拟」读回（隔离 HOME 目录）
- 同 key 二次 save 覆盖
- delete 真的删
- 0600 文件权限（POSIX）
- 非 ACP 写侧（register 端点）写后，凭据库可读回（与 chat.py 写侧联动验证）

不在这里测 spawn 注入（那是 transport 层职责），仅测凭据库自身契约。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from vermes_cli.a2a import credentials
from vermes_cli.a2a.credentials import (
    delete_credential,
    get_credential,
    get_credential_path,
    save_credential,
)


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """把 VERMES_HOME 隔离到 tmp_path，模拟「Vermes 重启」时新进程读不到 in-memory 状态。

    credentials 模块的 get_vermes_home() 优先级：env(VERMES_HOME) > ~/.vermes/。
    这里通过 monkeypatch env 把 VERMES_HOME 重定向到隔离目录，使
    get_credential 写入 / 读取 / 重新加载模块时都指向隔离目录。
    """
    fake_home = tmp_path / "fake_vermes_home"
    fake_home.mkdir()
    monkeypatch.setenv("VERMES_HOME", str(fake_home))
    # 重新加载以让 get_vermes_home() 解析到新 VERMES_HOME（避免模块顶层 CRED_STORE_PATH 缓存）
    import importlib
    importlib.reload(credentials)
    yield fake_home


def test_save_then_get_roundtrip(isolated_home):
    save_credential("codex-cli", "sk-test-abc-123")
    assert get_credential("codex-cli") == "sk-test-abc-123"


def test_get_missing_returns_none(isolated_home):
    assert get_credential("never-saved") is None


def test_save_overwrites(isolated_home):
    save_credential("k", "old")
    save_credential("k", "new")
    assert get_credential("k") == "new"


def test_delete_removes(isolated_home):
    save_credential("k", "v")
    assert delete_credential("k") is True
    assert get_credential("k") is None
    # 二次删 → False（无副作用）
    assert delete_credential("k") is False


def test_credentials_file_is_0600(isolated_home):
    """非 git 跟踪 + 文件权限收紧：仅当前用户可读写，杜绝同机其他账户读取密钥。"""
    save_credential("k", "v")
    path = get_credential_path()
    assert path.exists()
    mode = path.stat().st_mode & 0o777
    assert mode == 0o600, f"expected 0600, got {oct(mode)}"


def test_credentials_file_not_tracked_by_git(isolated_home, tmp_path):
    """凭据库路径必须落在 ~/.vermes/ 下，不在 git 跟踪的 vermes-electron/ 仓库内。

    验证方法：把 ~/.vermes/agent_auth.json 路径与 vermes-electron/ 仓库真源比
    对，若两者重合即为 P0 错误（密钥会被误提交）。
    """
    save_credential("k", "v")
    cred_path = Path(get_credential_path()).resolve()
    # 仓库真源（通过 git 顶层目录反推）
    repo_root = Path(__file__).resolve()
    for _ in range(10):
        if (repo_root / ".git").exists():
            break
        repo_root = repo_root.parent
    if (repo_root / ".git").exists():
        assert not str(cred_path).startswith(str(repo_root)), (
            f"凭据库 {cred_path} 落在 git 跟踪的 {repo_root} 内，"
            "密钥会被误提交！"
        )


def test_register_endpoint_write_survives_simulated_restart(isolated_home, monkeypatch):
    """端到端模拟：register 端点写侧（save_credential）→ 「重启」（隔离 HOME）
    → build_acp_transport 读侧（get_credential）→ spawn env 拿到 key。

    这条是用户最关心的承诺：「填一次、跨重启生效」。

    注意：registry dump 的 binary recipe 大多 env_var=null（registry 真源无
    auth 字段），所以这里用**手写** codex-acp（env_var=OPENAI_API_KEY）。
    """
    from vermes_cli.a2a.recipes.loader import RECIPES_DIR, find_recipe
    from vermes_cli.a2a.transport import build_acp_transport

    # 1) register 端点写侧：模拟用户在前端授权框填 key
    recipe = find_recipe("codex-acp", RECIPES_DIR)
    assert recipe is not None, "codex-acp 手写 recipe 必须在 RECIPES_DIR"
    assert recipe.auth.env_var == "OPENAI_API_KEY"
    save_credential(
        recipe.name,  # 凭据库 key 约定：recipe.name（不用 fallback_settings，见 P0）
        "sk-codex-persisted",
    )

    # 2) 模拟 Vermes 重启：env 不再有 key（仅磁盘有）
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    # 重新加载以让 get_vermes_home() 解析到新 HOME
    import importlib
    importlib.reload(credentials)

    # 3) 读侧：build_acp_transport 构造 transport 时应回注持久化 key
    # 重新加载 transport 以让其 import 的 credentials 是 reload 后的版本
    from vermes_cli.a2a import credentials as cred_reloaded
    from vermes_cli.a2a import transport as transport_mod
    importlib.reload(transport_mod)

    transport = transport_mod.build_acp_transport(recipe)
    assert transport._auth_env == "OPENAI_API_KEY"
    assert transport._auth_value == "sk-codex-persisted"

    # 4) spawn 时 _apply_auth_env 把 key 写进子进程 env
    env = transport._apply_auth_env({"PATH": "/usr/bin"})
    assert env["OPENAI_API_KEY"] == "sk-codex-persisted"
    # PATH 等已有变量不被覆盖
    assert env["PATH"] == "/usr/bin"


def test_persisted_value_used_when_env_missing(isolated_home, monkeypatch):
    """环境无 key 但凭据库有 → spawn env 应含凭据库的 key。

    registry dump 的 binary recipe（cursor/kimi 等）env_var=null 走不进
    _apply_auth_env 路径；这里用**手写** claude-agent-acp
    （env_var=ANTHROPIC_API_KEY）。
    """
    from vermes_cli.a2a.recipes.loader import RECIPES_DIR, find_recipe
    from vermes_cli.a2a import credentials as cred_mod
    from vermes_cli.a2a import transport as transport_mod
    import importlib

    recipe = find_recipe("claude-agent-acp", RECIPES_DIR)
    assert recipe is not None
    # 与 transport 读侧同 key 约定：recipe.name（P0 防线）
    cred_key = recipe.name
    save_credential(cred_key, "sk-claude-persisted")
    monkeypatch.delenv(recipe.auth.env_var, raising=False)
    importlib.reload(cred_mod)
    importlib.reload(transport_mod)
    transport = transport_mod.build_acp_transport(recipe)
    assert transport._auth_value == "sk-claude-persisted"


def test_env_wins_over_persisted(isolated_home, monkeypatch):
    """进程环境已设置 key 时，spawn env 不应被凭据库覆盖（保留用户临时覆盖）。"""
    from vermes_cli.a2a.recipes.loader import RECIPES_DIR, find_recipe
    from vermes_cli.a2a import credentials as cred_mod
    from vermes_cli.a2a import transport as transport_mod
    import importlib

    recipe = find_recipe("codex-acp", RECIPES_DIR)
    assert recipe is not None
    # 凭据库有旧值
    save_credential(recipe.name, "sk-from-store")
    # env 有新值（用户临时覆盖）
    env_var = recipe.auth.env_var
    assert env_var  # 必须有 env_var 才能覆盖
    monkeypatch.setenv(env_var, "sk-from-env-new")

    importlib.reload(cred_mod)
    importlib.reload(transport_mod)
    transport = transport_mod.build_acp_transport(recipe)
    assert transport._auth_value == "sk-from-env-new"


def test_multi_recipe_no_key_collision(isolated_home, monkeypatch):
    """🔴 P0 防线：三条手写 recipe（codex-acp / claude-agent-acp / copilot）的
    fallback_settings 都是 '~/.vermes/settings.json'——若凭据库 key 用
    fallback_settings，三条会冲突覆盖、最后一个污染前面所有 agent 的 env_var，
    导致重启后 Anthropic key 注入给 OpenAI 端点（错配泄露）。

    修复：凭据库 key 一律用 recipe.name（唯一）。本测试锁定三条同时写时
    各自 key 互不覆盖、跨重启读回各自值。
    """
    from vermes_cli.a2a.recipes.loader import RECIPES_DIR, find_recipe
    from vermes_cli.a2a import credentials as cred_mod
    from vermes_cli.a2a import transport as transport_mod
    import importlib

    recipes = [find_recipe(n, RECIPES_DIR) for n in ("codex-acp", "claude-agent-acp", "copilot")]
    for r in recipes:
        assert r is not None, f"recipe {r.name if r else '?'} 必须在 RECIPES_DIR"
        assert r.auth.env_var, f"{r.name} 必须有 env_var 才有意义"

    # 三条手写 recipe 的 fallback_settings 字面量完全相同 → 这是触发 P0 的前提
    fallback_values = {r.auth.fallback_settings for r in recipes}
    assert len(fallback_values) == 1, (
        f"P0 防线前提失效：三条 recipe fallback_settings 不再相同，"
        "请重写本测试覆盖新场景。当前值："
        + ", ".join(f"{r.name}={r.auth.fallback_settings}" for r in recipes)
    )

    # 三条同时写各自不同的 key
    test_keys = {
        "codex-acp": "sk-codex-AAA",
        "claude-agent-acp": "sk-claude-BBB",
        "copilot": "sk-copilot-DDD",
    }
    for r in recipes:
        save_credential(r.name, test_keys[r.name])

    # 模拟 Vermes 重启：env 清空，凭据库是唯一真源
    for r in recipes:
        monkeypatch.delenv(r.auth.env_var, raising=False)
    importlib.reload(cred_mod)
    importlib.reload(transport_mod)

    # 各自读回正确的 key（这是修复前会失败、修复后必须通过的断言）
    for r in recipes:
        transport = transport_mod.build_acp_transport(r)
        assert transport._auth_value == test_keys[r.name], (
            f"{r.name}: expected {test_keys[r.name]!r}, got {transport._auth_value!r}。"
            "若此值是别的 recipe 的 key，说明凭据库 key 仍用 fallback_settings，P0 未修！"
        )

    # 最终一致性检查：凭据库里三个槽位都存各自的 key，没有合并
    final_path = get_credential_path()
    import json
    with open(final_path, "r", encoding="utf-8") as fh:
        stored = json.load(fh)
    assert stored == test_keys, (
        f"凭据库内容 {stored} 应等于 {test_keys}；"
        "若有键合并或缺失，凭据库 key 命名约定错了。"
    )


def test_persisted_key_lookup_uses_recipe_name_not_fallback_settings(
    isolated_home, monkeypatch
):
    """直接锁定 transport 的凭据库 key 约定：必须用 recipe.name，不用 fallback_settings。

    把 fallback_settings 槽位和 recipe.name 槽位都写入不同值，
    build_acp_transport 应只命中 recipe.name 槽位。
    """
    from vermes_cli.a2a.recipes.loader import RECIPES_DIR, find_recipe
    from vermes_cli.a2a import credentials as cred_mod
    from vermes_cli.a2a import transport as transport_mod
    import importlib

    recipe = find_recipe("codex-acp", RECIPES_DIR)
    assert recipe is not None
    # 两个槽位都写
    save_credential(recipe.name, "sk-from-name-slot")
    save_credential(recipe.auth.fallback_settings, "sk-from-fallback-slot")

    monkeypatch.delenv(recipe.auth.env_var, raising=False)
    importlib.reload(cred_mod)
    importlib.reload(transport_mod)

    transport = transport_mod.build_acp_transport(recipe)
    assert transport._auth_value == "sk-from-name-slot", (
        "build_acp_transport 必须用 recipe.name 查凭据库，"
        "否则 P0（fallback_settings 冲突）会复发。当前拿到 "
        f"{transport._auth_value!r}"
    )
