#!/usr/bin/env python3
"""校验 catalog.json 里每个 code_asset 的实际 sha256/size 是否与登记一致。

背景（2026-08-29 实测发现的存量故障）：
  build_modules.py 只负责「本地打包 → 算 sha256 → 写 catalog → 同步远端 catalog」，
  **不负责上传 Release 资产**（上传靠手动 gh release）。一旦本地重构建与已上传资产
  不同步，catalog 里的 sha256 就会与线上资产漂移 → 用户安装时 verify_sha256 失败，
  install_module_code() 直接抛错「下载/校验代码包失败」，该模块**完全装不上**。
  修复 scholarforge 时即为此情形（catalog 记 241edc…，线上资产实为 55db82…）。

本脚本把「发布后校验」补成可 CI 化的独立步骤，防回归。

用法:
    python3 scripts/verify_catalog_assets.py                     # 校验 bundled catalog
    python3 scripts/verify_catalog_assets.py --path <catalog.json>
    python3 scripts/verify_catalog_assets.py --remote            # 校验远端官方 catalog
    python3 scripts/verify_catalog_assets.py --json              # 机器可读输出

退出码: 0 = 全部一致；1 = 存在漂移/不可达；2 = catalog 无法解析
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUNDLED = ROOT / "vermes_cli" / "modules" / "catalog.json"
REMOTE_URL = (
    "https://raw.githubusercontent.com/donghzs/vermes-modules-catalog/main/catalog.json"
)
TIMEOUT = 60


def _sha256_and_size(data: bytes) -> tuple[str, int]:
    return hashlib.sha256(data).hexdigest(), len(data)


def _fetch(url: str, retries: int = 3) -> bytes:
    """下载 URL，带重试 + curl 回退。

    本机/沙箱环境常经 HTTP_PROXY 出网，urllib 的 CONNECT 隧道偶发
    ``Tunnel connection failed: 502``，而 curl/gh 走同一代理却正常。故失败时
    重试若干次后回退到 curl，避免把环境抖动误报成「资产漂移」。
    """
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "vermes-catalog-verify"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return resp.read()
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt < retries:
                time.sleep(1.5 * attempt)

    # 回退：curl（跟随重定向，URL 是 GitHub Release 的 302 跳转）
    try:
        r = subprocess.run(
            ["curl", "-sSL", "--max-time", str(TIMEOUT), url],
            capture_output=True, check=False,
        )
        if r.returncode == 0 and r.stdout:
            return r.stdout
        last_err = RuntimeError(f"curl rc={r.returncode}: {(r.stderr or b'').decode()[:200]}")
    except FileNotFoundError:
        last_err = RuntimeError("curl 不可用")
    raise last_err  # type: ignore[misc]


def load_catalog(args) -> tuple[dict, str]:
    if args.remote:
        raw = _fetch(REMOTE_URL)
        return json.loads(raw.decode("utf-8")), REMOTE_URL
    path = Path(args.path) if args.path else BUNDLED
    if not path.exists():
        raise SystemExit(f"[verify] catalog 不存在: {path}")
    return json.loads(path.read_text(encoding="utf-8")), str(path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="校验 catalog 资产的 sha256/size 一致性")
    ap.add_argument("--path", default="", help="catalog.json 路径（默认 bundled）")
    ap.add_argument("--remote", action="store_true", help="校验远端官方 catalog（优先级更高）")
    ap.add_argument("--json", action="store_true", dest="as_json", help="机器可读输出")
    args = ap.parse_args(argv)

    try:
        catalog, source = load_catalog(args)
    except json.JSONDecodeError as e:
        print(f"[verify] catalog 解析失败: {e}", file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001
        print(f"[verify] catalog 读取失败: {e}", file=sys.stderr)
        return 2

    modules = catalog.get("modules") or []
    results = []
    for m in modules:
        name = m.get("name")
        url = m.get("code_asset") or ""
        want_sha = (m.get("code_sha256") or "").lower()
        want_size = m.get("size_code")
        row = {"name": name, "url": url, "expected_sha256": want_sha,
               "expected_size": want_size, "status": "skip", "detail": ""}

        if not url:
            row["detail"] = "无 code_asset（asset_only 模块，资产待回填）"
        elif not want_sha:
            row["status"] = "unverifiable"
            row["detail"] = "catalog 未登记 sha256，无法校验"
        else:
            try:
                data = _fetch(url)
                got_sha, got_size = _sha256_and_size(data)
                row["actual_sha256"] = got_sha
                row["actual_size"] = got_size
                if got_sha != want_sha:
                    row["status"] = "MISMATCH"
                    row["detail"] = (
                        f"sha256 漂移！catalog={want_sha[:16]}… 实际={got_sha[:16]}…"
                        f"（体积 {want_size} vs {got_size}）→ 该模块将无法安装"
                    )
                elif want_size is not None and got_size != want_size:
                    row["status"] = "SIZE_DRIFT"
                    row["detail"] = f"体积不一致：catalog={want_size} 实际={got_size}"
                else:
                    row["status"] = "OK"
                    row["detail"] = f"{got_size} bytes"
            except Exception as e:  # noqa: BLE001
                row["status"] = "UNREACHABLE"
                row["detail"] = f"{type(e).__name__}: {e}"
        results.append(row)

    if args.as_json:
        print(json.dumps({"source": source, "results": results}, ensure_ascii=False, indent=2))
    else:
        print(f"catalog 来源: {source}\n")
        for r in results:
            icon = {"OK": "✅", "MISMATCH": "❌", "SIZE_DRIFT": "⚠️",
                    "UNREACHABLE": "⚠️", "unverifiable": "⚠️", "skip": "⏭"}.get(r["status"], "·")
            print(f"  {icon} {r['name']:28s} {r['status']:12s} {r['detail']}")
        bad = [r for r in results if r["status"] in ("MISMATCH", "SIZE_DRIFT", "UNREACHABLE")]
        print()
        if bad:
            print(f"❌ {len(bad)} 个模块存在资产漂移/不可达，安装会失败。")
            print("   修复：重新上传与 catalog 一致的资产，或用实际 sha256 回填 catalog 后同步远端。")
        else:
            print(f"✅ {len([r for r in results if r['status'] == 'OK'])} 个模块资产校验通过。")

    return 1 if any(r["status"] in ("MISMATCH", "SIZE_DRIFT", "UNREACHABLE") for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
