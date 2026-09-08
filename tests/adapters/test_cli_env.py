"""通用 wrapper 运行时 env 解析测试（vendor-agnostic）。

刻意**不引用任何真实厂商**（不用 QClaw / WorkBuddy 等名）：全部用合成的
假 wrapper + 假运行时配置，证明本能力按「模式」工作 —— 任意厂商的 wrapper
型 CLI 只要符合「缺 env 就秒退」这一模式，都能被第三方进程拉起。
"""

from __future__ import annotations

import json
import os
import stat
import sys

import pytest

from vermes_cli.adapters.cli_env import (
    _is_wrapper,
    required_env_vars,
    resolve_cli_env,
)

# 模拟「宿主 App 注入 env 才能跑」的 wrapper（与真实厂商 wrapper 同构）
WRAPPER_BODY = """#!/usr/bin/env bash
set -euo pipefail
# Runtime paths are injected by the host app main process via env vars.
if [ -z "${FOO_CLI_NODE_BINARY:-}" ]; then
  echo "[foo-cli] Error: FOO_CLI_NODE_BINARY is not set" >&2
  exit 1
fi
if [ ! -f "${FOO_CLI_NODE_BINARY}" ]; then
  echo "[foo-cli] Error: node binary not found" >&2
  exit 1
fi
if [ -z "${FOO_CLI_ENTRY_MJS:-}" ]; then
  echo "[foo-cli] Error: FOO_CLI_ENTRY_MJS is not set" >&2
  exit 1
fi
exec "${FOO_CLI_NODE_BINARY}" "${FOO_CLI_ENTRY_MJS}" "$@"
"""


def _write(path, content, *, executable=True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if executable:
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _make_node(path) -> None:
    """假 node：回显被执行的入口脚本路径（便于断言真实入口被正确解析）。"""
    _write(path, '#!/bin/sh\necho "node-ran:$1"\n')


def test_required_env_vars_parsed_from_wrapper(tmp_path):
    w = tmp_path / "bin" / "foo"
    _write(w, WRAPPER_BODY)
    assert _is_wrapper(w)
    assert required_env_vars(w) == ["FOO_CLI_NODE_BINARY", "FOO_CLI_ENTRY_MJS"]


def test_non_wrapper_untouched(tmp_path):
    """真实二进制（含 NUL 字节 / 无 shebang）不应被识别为 wrapper。"""
    b = tmp_path / "bin" / "realbin"
    b.parent.mkdir(parents=True, exist_ok=True)
    b.write_bytes(b"\x7fELF\x00\x00binary")
    b.chmod(b.stat().st_mode | stat.S_IXUSR)
    assert not _is_wrapper(b)
    env = {"PATH": "/usr/bin"}
    assert resolve_cli_env(str(b), env) == env


def test_resolve_from_app_runtime_json(tmp_path):
    """布局一：App 把真实路径写在就近的运行时 JSON 里（最常见）。"""
    node = tmp_path / "node"
    _make_node(node)
    entry = tmp_path / "node_modules" / "foo" / "foo.mjs"
    _write(entry, "console.log('hi')\n", executable=False)
    (tmp_path / "runtime.json").write_text(
        json.dumps({"cli": {"nodeBinary": str(node), "entryMjs": str(entry)}}),
        encoding="utf-8",
    )
    w = tmp_path / "bin" / "foo"
    _write(w, WRAPPER_BODY)

    env = resolve_cli_env(str(w), {"PATH": "/usr/bin"})
    assert env["FOO_CLI_NODE_BINARY"] == str(node)
    assert env["FOO_CLI_ENTRY_MJS"] == str(entry)


def test_resolve_by_name_shape_without_json(tmp_path):
    """布局二：没有配置文件，纯按变量名语义 + 目录形状猜。"""
    node = tmp_path / "Contents" / "Resources" / "node" / "node"
    _make_node(node)
    entry = tmp_path / "node_modules" / "foo" / "foo.mjs"
    _write(entry, "// entry\n", executable=False)
    w = tmp_path / "bin" / "foo"
    _write(w, WRAPPER_BODY)

    env = resolve_cli_env(str(w), {"PATH": "/usr/bin"})
    assert env["FOO_CLI_NODE_BINARY"] == str(node) or env["FOO_CLI_NODE_BINARY"].endswith("node")
    assert env["FOO_CLI_ENTRY_MJS"] == str(entry)


def test_resolve_from_config_dir_layout(tmp_path, monkeypatch):
    """布局三：配置放在 ``~/.config/<app>/``（另一类厂商布局）。

    同时验证「按 App 名定向」：CLI 路径里的 App 名决定去哪个配置目录找，
    因此不会误拿别家 App 的同名配置。
    """
    import vermes_cli.adapters.cli_env as cli_env

    home = tmp_path / "home"
    (home / ".config").mkdir(parents=True)
    monkeypatch.setattr(cli_env.Path, "home", staticmethod(lambda: home))

    node = tmp_path / "node"
    _make_node(node)
    entry = tmp_path / "node_modules" / "foo" / "foo.mjs"
    _write(entry, "// entry\n", executable=False)
    cfg_dir = home / ".config" / "foo"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "runtime.json").write_text(
        json.dumps({"cli": {"nodeBinary": str(node), "entryMjs": str(entry)}}),
        encoding="utf-8",
    )
    # CLI 落在 App 支持目录（路径含 App 名 foo），配置却在家目录
    w = home / "Library" / "Application Support" / "Foo" / "foo" / "config" / "bin" / "foo"
    _write(w, WRAPPER_BODY)

    env = resolve_cli_env(str(w), {"PATH": "/usr/bin"})
    assert env["FOO_CLI_NODE_BINARY"] == str(node)
    assert env["FOO_CLI_ENTRY_MJS"] == str(entry)


def test_optional_profile_env_from_entry_script(tmp_path):
    """第二层：入口脚本里的**可选**env（profile / 配置目录 / 状态目录）。

    这类变量有默认值，wrapper 不会报错，但会静默走错 profile —— 表现为
    「能起来，却连不上宿主 App 自己的网关」。必须一并补齐。
    """
    node = tmp_path / "node"
    _make_node(node)
    entry = tmp_path / "node_modules" / "foo" / "foo.mjs"
    _write(
        entry,
        'const cfg = process.env.FOO_CLI_CONFIG_PATH ?? "~/.foo/default.json";\n'
        'const dir = process.env.FOO_CLI_STATE_DIR ?? "~/.foo";\n'
        'console.log(cfg, dir);\n',
        executable=False,
    )
    cfg_file = tmp_path / "profile.json"
    _write(cfg_file, "{}", executable=False)
    (tmp_path / "runtime.json").write_text(
        json.dumps({"configPath": str(cfg_file), "stateDir": str(tmp_path)}),
        encoding="utf-8",
    )
    w = tmp_path / "bin" / "foo"
    _write(w, WRAPPER_BODY)

    env = resolve_cli_env(str(w), {"PATH": "/usr/bin"})
    assert env["FOO_CLI_CONFIG_PATH"] == str(cfg_file)   # 文件（非可执行）
    assert env["FOO_CLI_STATE_DIR"] == str(tmp_path)     # 目录


def test_existing_env_never_overwritten(tmp_path):
    """宿主/用户显式设置的 env 优先级最高，绝不覆盖。"""
    node = tmp_path / "node"
    _make_node(node)
    entry = tmp_path / "node_modules" / "foo" / "foo.mjs"
    _write(entry, "// entry\n", executable=False)
    w = tmp_path / "bin" / "foo"
    _write(w, WRAPPER_BODY)

    env = resolve_cli_env(str(w), {"FOO_CLI_NODE_BINARY": "/custom/node"})
    assert env["FOO_CLI_NODE_BINARY"] == "/custom/node"


def test_unresolvable_is_fail_safe(tmp_path):
    """解不出来：不抛异常、不塞空值，保持原样（仅告警）。

    用一个语义上无从猜测的变量名（非 node / 非 entry 形状，配置文件里也没有），
    确保解析链真的走到「放弃」分支。
    """
    w = tmp_path / "bin" / "foo"
    _write(
        w,
        '#!/usr/bin/env bash\n'
        'if [ -z "${FOO_CLI_SECRET_TOKEN:-}" ]; then echo "missing" >&2; exit 1; fi\n'
        'echo ok\n',
    )
    env = {"PATH": "/usr/bin"}
    out = resolve_cli_env(str(w), env)
    assert out == env
    assert "FOO_CLI_SECRET_TOKEN" not in out


def test_wrapper_actually_runs_after_resolution(tmp_path):
    """端到端：补齐前秒退 exit 1；补齐后 wrapper 真能 exec 起来。"""
    import subprocess

    node = tmp_path / "node"
    _make_node(node)
    entry = tmp_path / "node_modules" / "foo" / "foo.mjs"
    _write(entry, "console.log('hello from foo')\n", executable=False)
    (tmp_path / "runtime.json").write_text(
        json.dumps({"cli": {"nodeBinary": str(node), "entryMjs": str(entry)}}),
        encoding="utf-8",
    )
    w = tmp_path / "bin" / "foo"
    _write(w, WRAPPER_BODY)

    bare = subprocess.run([str(w)], capture_output=True, text=True, check=False)
    assert bare.returncode == 1
    assert "is not set" in (bare.stderr or "")

    fixed = subprocess.run(
        [str(w)], capture_output=True, text=True, check=False,
        env=resolve_cli_env(str(w), {"PATH": os.environ.get("PATH", "")}),
    )
    assert fixed.returncode == 0, fixed.stderr
    # 假 node 回显它拿到的入口脚本 → 证明 FOO_CLI_ENTRY_MJS 解析正确
    assert f"node-ran:{entry}" in (fixed.stdout or "")
