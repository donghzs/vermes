#!/usr/bin/env python3
"""参数化渐开线直齿轮生成器 — build123d 0.11.x
build123d 无内置齿轮 API（dir(build123d) 无 gear/involute），渐开线公式参数化生成。

实测（2026-08-25）：m2 z24 齿宽12 孔径12 → 单实体、体积 23666 mm³、
齿顶圆 52.47mm≈理论 52（da=M(Z+2)=52）✓

用法:
  python spur_gear.py --module 2.0 --teeth 24 --face-width 12 --bore 12 -o gear.step
  python spur_gear.py -m 1.5 -z 36 --pressure-angle 20 --face-width 10 --bore 6 -o g36.step
"""
import argparse
import math
from pathlib import Path

from build123d import (Cylinder, Pos, Spline, extrude, export_stl, export_step,
                       make_face)


def spur_gear(module: float, teeth: int, face_width: float,
              bore: float, pressure_angle_deg: float = 20.0, tip_steps: int = 25,
              involute_step_deg: int = 2):
    """返回 (gear_solid, metrics_dict)。"""
    PA = math.radians(pressure_angle_deg)
    d = module * teeth                    # 分度圆直径
    da = module * (teeth + 2)             # 齿顶圆直径
    # df = module * (teeth - 2.5)         # 齿根圆直径（齿根按圆弧过渡简化）
    rb = d * math.cos(PA) / 2             # 基圆半径
    ra = da / 2

    def involute(rb_, ang_deg):
        pts = []
        for t in range(0, int(ang_deg) + 1, involute_step_deg):
            rad = math.radians(t)
            pts.append((rb_ * (math.cos(rad) + rad * math.sin(rad)),
                        rb_ * (math.sin(rad) - rad * math.cos(rad))))
        return pts

    half_ang = (math.pi * module / 2) / d          # 分度圆上齿厚半角
    inv_alpha = math.tan(PA) - PA                  # 渐开线函数
    t_max = math.degrees(math.sqrt((ra / rb) ** 2 - 1)) + 2  # 齿顶处展角

    def rot(p, a):
        ca, sa = math.cos(a), math.sin(a)
        return (p[0] * ca - p[1] * sa, p[0] * sa + p[1] * ca)

    right = [rot(p, -(half_ang + inv_alpha)) for p in involute(rb, t_max)]
    left = [rot(p, (half_ang + inv_alpha)) for p in involute(rb, t_max)[::-1]]

    a1 = math.atan2(right[-1][1], right[-1][0])
    a2 = math.atan2(left[-1][1], left[-1][0])
    tip = [(ra * math.cos(a1 + (a2 - a1) * i / tip_steps),
            ra * math.sin(a1 + (a2 - a1) * i / tip_steps)) for i in range(tip_steps + 1)]

    tooth = right + tip + left
    all_pts = [rot(p, 2 * math.pi * k / teeth) for k in range(teeth) for p in tooth]

    gear = extrude(make_face(Spline(*all_pts, periodic=True)), face_width)
    if bore > 0:
        gear = gear - Pos(0, 0, -face_width) * Cylinder(bore / 2, face_width * 3)

    bbox = gear.bounding_box()
    metrics = {
        "solids": len(gear.solids()),
        "volume_mm3": round(gear.volume, 1),
        "tip_diameter_mm": round(bbox.max.Y * 2, 2),
        "face_width_mm": face_width,
    }
    return gear, metrics


def main():
    ap = argparse.ArgumentParser(description="渐开线直齿轮生成器 (build123d)")
    ap.add_argument("-m", "--module", type=float, required=True, help="模数 mm")
    ap.add_argument("-z", "--teeth", type=int, required=True, help="齿数")
    ap.add_argument("--face-width", type=float, default=10.0, help="齿宽 mm")
    ap.add_argument("--bore", type=float, default=0.0, help="中心孔径 mm (0=实心)")
    ap.add_argument("--pressure-angle", type=float, default=20.0, help="压力角 度")
    ap.add_argument("-o", "--output", default="gear.step", help="输出路径 (.step)")
    args = ap.parse_args()

    gear, metrics = spur_gear(args.module, args.teeth, args.face_width,
                              args.bore, args.pressure_angle)
    print("metrics:", metrics)
    out = Path(args.output)
    export_step(gear, str(out))
    print("STEP ->", out.resolve(), f"({out.stat().st_size} bytes)")
    stl = out.with_suffix(".stl")
    export_stl(gear, str(stl), tolerance=0.005)
    print("STL  ->", stl.resolve(), f"({stl.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
