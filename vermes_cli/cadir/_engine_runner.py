#!/usr/bin/env python3
"""cadir 引擎侧运行器 —— 在引擎 venv（build123d/trimesh/numpy）中执行，输出单行 JSON。

由 tools.py 经子进程调用（对齐 mfgcad 的引擎桥接模式：主 venv 不装重依赖）。

模式：
  python _engine_runner.py --verify-step <file.step> \
      [--expect-vol V] [--expect-bbox X,Y,Z] [--expect-solids N] [--tolerance T]
      几何核验（镜像自 verify_step.py 的已验证逻辑，改为 JSON 输出）。
  python _engine_runner.py --verify-stl <file.stl> [--write-clean <out.stl>]
      STL 网格核验（复用 stl_verify.py 的 read_stl/write_stl，50B/三角形解析）。

stdout 最后一行恒为 JSON：{"ok": bool, ...}；进程退出码 0=核验通过/1=不通过或出错。
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def verify_step(args: argparse.Namespace) -> int:
    from build123d import import_step  # noqa: 引擎 venv 内才可导入

    part = import_step(args.step_file)
    bb = part.bounding_box()
    size = [bb.max.X - bb.min.X, bb.max.Y - bb.min.Y, bb.max.Z - bb.min.Z]
    solids = len(part.solids())
    vol = part.volume

    checks = []
    ok = True
    if args.expect_solids is not None:
        match = solids == args.expect_solids
        ok &= match
        checks.append({"check": "solids", "expect": args.expect_solids, "got": solids, "ok": bool(match)})
    if args.expect_bbox:
        for i, (got, want) in enumerate(zip(size, args.expect_bbox)):
            dev = abs(got - want)
            match = dev <= args.tolerance
            ok &= match
            checks.append({"check": f"bbox[{i}]", "expect": want, "got": round(got, 2), "dev_mm": round(dev, 2), "ok": bool(match)})
    if args.expect_vol is not None and args.expect_vol > 0:
        dev_pct = abs(vol - args.expect_vol) / args.expect_vol * 100
        match = dev_pct <= args.tolerance
        ok &= match
        checks.append({"check": "volume", "expect": args.expect_vol, "got": round(vol, 2), "dev_pct": round(dev_pct, 3), "ok": bool(match)})

    _emit({
        "ok": bool(ok),
        "mode": "verify_step",
        "file": str(args.step_file),
        "solids": solids,
        "bbox": {
            "min": [round(bb.min.X, 2), round(bb.min.Y, 2), round(bb.min.Z, 2)],
            "max": [round(bb.max.X, 2), round(bb.max.Y, 2), round(bb.max.Z, 2)],
        },
        "size": [round(s, 2) for s in size],
        "volume_mm3": round(vol, 2),
        "checks": checks,
    })
    return 0 if ok else 1


def _is_bad_vertex(v) -> bool:
    return any(math.isnan(c) or math.isinf(c) or abs(c) > 1e6 for c in v)


def verify_stl(args: argparse.Namespace) -> int:
    # 复用同目录 stl_verify.py 的已验证解析器（50B/三角形，避免流式错位）
    sys.path.insert(0, str(_HERE))
    from stl_verify import read_stl, write_stl  # noqa: 引擎 venv（numpy）

    tris = read_stl(args.stl_file)
    total = int(tris.shape[0])
    bad = sum(1 for tri in tris if any(_is_bad_vertex(v) for v in tri))

    result = {
        "ok": bad == 0,
        "mode": "verify_stl",
        "file": str(args.stl_file),
        "triangles": total,
        "bad_faces": bad,
    }
    if args.write_clean and bad:
        good = [t for t in tris if not any(_is_bad_vertex(v) for v in t)]
        write_stl(args.write_clean, good)
        result["clean_file"] = str(args.write_clean)
        result["clean_triangles"] = len(good)
        result["dropped"] = bad
    _emit(result)
    return 0 if bad == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="cadir engine runner")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--verify-step", metavar="FILE")
    g.add_argument("--verify-stl", metavar="FILE")
    ap.add_argument("--expect-vol", type=float, default=None, help="期望体积 mm³（偏差百分比对比）")
    ap.add_argument("--expect-bbox", default=None, help="期望包围盒 X,Y,Z（mm，线性偏差对比）")
    ap.add_argument("--expect-solids", type=int, default=None, help="期望实体数（1=单实体）")
    ap.add_argument("--tolerance", type=float, default=0.5, help="允许偏差：体积%% / bbox mm")
    ap.add_argument("--write-clean", default=None, help="过滤坏面后写回干净 STL 的路径")
    args = ap.parse_args()

    if args.verify_step:
        args.step_file = args.verify_step
        args.expect_bbox = [float(x) for x in args.expect_bbox.split(",")] if args.expect_bbox else None
        return verify_step(args)
    args.stl_file = args.verify_stl
    return verify_stl(args)


if __name__ == "__main__":
    sys.exit(main())
