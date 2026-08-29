#!/usr/bin/env python3
"""把本地 catalog.json 同步到远程官方 catalog 仓（GitHub Contents API，经 gh CLI）。

P7：模块发版即时触达所有 app 版本——只要 push 到 vermes-modules-catalog/main，
任何版本的 Vermes 启动时 / 打开商店时都会远程优先拉到最新 catalog，无需重发 DMG。

经 gh CLI 调用（已登录 donghzs 且具 repo 权限），无需管理 git 凭证：
  - 读取本地 catalog.json（必须是合法、含非空 modules 列表的 JSON）
  - 查远程是否已存在 catalog.json 拿到 sha
  - PUT contents API：存在则带 sha 更新，不存在则创建

用法:
  python3 scripts/sync_remote_catalog.py \
      --source vermes_cli/modules/catalog.json \
      --repo donghzs/vermes-modules-catalog \
      --branch main \
      --message "release mfgcad 0.3.0 + scholarforge 1.0.0"

  python3 scripts/sync_remote_catalog.py --dry-run   # 只检查不推送
"""
from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

DEFAULT_SOURCE = Path(__file__).resolve().parent.parent / "vermes_cli" / "modules" / "catalog.json"
DEFAULT_REPO = "donghzs/vermes-modules-catalog"
DEFAULT_BRANCH = "main"
CATALOG_PATH = "catalog.json"


def _run(cmd, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def gh_available() -> bool:
    """gh CLI 是否已安装且可用。"""
    return _run(["gh", "--version"], check=False).returncode == 0


def remote_sha(repo: str, path: str, branch: str, retries: int = 3) -> Optional[str]:
    """查询远程 catalog.json 的当前 sha；文件不存在(404)返回 None。

    保留旧签名（返回 Optional[str]）以兼容既有调用方，但**失败时不再静默**：
    查询出错（网络抖动/权限/限流）会直接抛 SystemExit 中止，而不是当作
    「文件不存在」——否则后续 PUT 不带 sha，GitHub 只会回一个极具误导性的
    ``"sha" wasn't supplied. (HTTP 422)``（2026-08-29 实测踩过：代理抖动导致
    remote_sha 静默返回 None，同步失败却看不出真因）。
    """
    last_err = ""
    for attempt in range(1, retries + 1):
        r = _run(
            ["gh", "api", f"repos/{repo}/contents/{path}?ref={branch}"],
            check=False,
        )
        if r.returncode == 0:
            try:
                return json.loads(r.stdout).get("sha")
            except Exception as e:  # noqa: BLE001
                raise SystemExit(f"[error] 远程 catalog 响应解析失败: {e}")
        last_err = (r.stderr or r.stdout or "").strip()
        # 404 = 文件确实不存在，是合法的“新建”场景，不重试
        if "404" in last_err or "Not Found" in last_err:
            return None
        if attempt < retries:
            time.sleep(1.5 * attempt)
    raise SystemExit(
        f"[error] 无法查询远程 catalog 当前 sha（已重试 {retries} 次），"
        f"中止推送以免覆盖异常。gh 输出: {last_err[:300]}"
    )


def sync_catalog(
    source: Path,
    repo: str,
    branch: str,
    message: str,
    dry_run: bool = False,
) -> bool:
    """把本地 catalog 推到远程仓。成功返回 True。"""
    source = Path(source)
    if not source.exists():
        print(f"[error] 本地 catalog 不存在: {source}", file=sys.stderr)
        return False
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        print(f"[error] 本地 catalog 不是合法 JSON: {e}", file=sys.stderr)
        return False
    if not isinstance(data.get("modules"), list) or not data["modules"]:
        print("[error] catalog 缺少非空 modules 列表，拒绝推送（避免发布空目录）", file=sys.stderr)
        return False

    if not gh_available():
        print("[error] 未找到 gh CLI，无法推送（需 GitHub CLI 且已登录 donghzs）", file=sys.stderr)
        return False

    content_b64 = base64.b64encode(source.read_bytes()).decode("ascii")

    if dry_run:
        sha = remote_sha(repo, CATALOG_PATH, branch)
        print(f"[dry-run] 将推送 {source} -> {repo}@{branch}:{CATALOG_PATH} (现有 sha={sha})")
        print(f"[dry-run] message: {message}")
        return True

    sha = remote_sha(repo, CATALOG_PATH, branch)
    cmd = [
        "gh", "api", "-X", "PUT",
        f"repos/{repo}/contents/{CATALOG_PATH}",
        "-f", f"message={message}",
        "-f", f"content={content_b64}",
        "-f", f"branch={branch}",
    ]
    if sha:
        cmd += ["-f", f"sha={sha}"]
    r = _run(cmd, check=False)
    if r.returncode != 0:
        print(f"[error] 推送失败:\n{r.stderr}", file=sys.stderr)
        return False
    print(f"[ok] 已同步 catalog -> https://github.com/{repo}/blob/{branch}/{CATALOG_PATH}")
    return True


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="同步 catalog.json 到远程官方 catalog 仓（P7）")
    p.add_argument("--source", type=Path, default=DEFAULT_SOURCE, help="本地 catalog.json 路径")
    p.add_argument("--repo", default=DEFAULT_REPO, help="目标 GitHub 仓 owner/name")
    p.add_argument("--branch", default=DEFAULT_BRANCH, help="目标分支")
    p.add_argument("--message", default="sync catalog", help="commit message")
    p.add_argument("--dry-run", action="store_true", help="只检查不推送")
    args = p.parse_args(argv)
    ok = sync_catalog(args.source, args.repo, args.branch, args.message, args.dry_run)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
