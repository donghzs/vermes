#!/usr/bin/env python3
"""Verify a STEP file's geometry: solid count, bounding box, volume vs expectation.

Usage (from mac_poc dir, with .venv activated):
    python verify_step.py <file.step>
    python verify_step.py <file.step> --expect-vol 76708.2 --tolerance 0.5
    python verify_step.py <file.step> --expect-bbox 60 60 130

This is the independent arbiter against pipeline QA false-positives:
QA may report FAIL for geometrically-valid artifacts (stale temp_missed
residue, ghost FILLET_FAILED) — this script re-derives the truth from
the actual STEP geometry.

Exit code 0 = all checks passed, 1 = any check failed.
"""
import argparse
import sys


def main() -> int:
    ap = argparse.ArgumentParser(description="Verify STEP geometry")
    ap.add_argument("step_file", help="path to .step file")
    ap.add_argument("--expect-vol", type=float, default=None,
                    help="expected volume in mm3")
    ap.add_argument("--expect-bbox", nargs=3, type=float, default=None,
                    help="expected X Y Z extents in mm")
    ap.add_argument("--expect-solids", type=int, default=None,
                    help="expected number of solids (1 = single body)")
    ap.add_argument("--tolerance", type=float, default=0.5,
                    help="allowed deviation: % for volume, mm for bbox")
    args = ap.parse_args()

    from build123d import import_step
    part = import_step(args.step_file)
    bb = part.bounding_box()
    size = (bb.max.X - bb.min.X, bb.max.Y - bb.min.Y, bb.max.Z - bb.min.Z)
    solids = len(part.solids())
    vol = part.volume

    print(f"file   : {args.step_file}")
    print(f"solids : {solids}")
    print(f"bbox   : x {bb.min.X:.2f}..{bb.max.X:.2f}  y {bb.min.Y:.2f}..{bb.max.Y:.2f}  z {bb.min.Z:.2f}..{bb.max.Z:.2f}")
    print(f"size   : {size[0]:.2f} x {size[1]:.2f} x {size[2]:.2f}")
    print(f"volume : {vol:.2f}")

    ok = True
    if args.expect_solids is not None:
        match = solids == args.expect_solids
        ok &= match
        print(f"expect solids={args.expect_solids}: got {solids} {'OK' if match else 'MISMATCH'}")

    if args.expect_bbox is not None:
        for i, (got, want) in enumerate(zip(size, args.expect_bbox)):
            dev = abs(got - want)
            match = dev <= args.tolerance
            ok &= match
            print(f"expect dim[{i}]={want}: got {got:.2f} (dev {dev:.2f}mm) {'OK' if match else 'MISMATCH'}")

    if args.expect_vol is not None:
        dev_pct = abs(vol - args.expect_vol) / args.expect_vol * 100
        match = dev_pct <= args.tolerance
        ok &= match
        print(f"expect vol={args.expect_vol}: got {vol:.2f} (dev {dev_pct:.3f}%) {'OK' if match else 'MISMATCH'}")

    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
