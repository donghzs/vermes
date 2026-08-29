#!/usr/bin/env python3
"""
二进制 STL 正确解析 + 质量核验脚本（2026-08-17 树叶实测定型）

为什么必须有这个脚本：
  STL 每三角形 = normal 12B + verts 36B + attribute 2B = 50 字节。
  流式 f.read(12)+f.read(36) 只读 48B → 从第 2 个三角形起累积错位 2B →
  产生大量"幽灵垃圾面"（±3.3e38/NaN 假象）→ 曾据此误删 64% 好面（4422→1570，纹理丢失）。
  本脚本按 50B/三角形 struct 偏移解析，是判质的唯一可靠方式。

用法：
  python3 stl_verify.py <file.stl> [--write-clean <out.stl>]
  --write-clean: 过滤坏面（NaN/Inf/极端坐标>1e6）后写回干净二进制 STL
"""
import sys
import struct
import numpy as np


def read_stl(path):
    """正确解析：整文件读入内存，按 50B/三角形偏移取 verts。"""
    with open(path, 'rb') as f:
        raw = f.read()
    n = struct.unpack_from('<I', raw, 80)[0]
    tris = np.zeros((n, 3, 3))
    for i in range(n):
        off = 84 + i * 50
        verts = struct.unpack_from('<9f', raw, off + 12)
        tris[i] = np.array(verts).reshape(3, 3)
    return tris


def write_stl(path, tris):
    """写回二进制 STL（含法向量，右手定则）。"""
    with open(path, 'wb') as f:
        f.write(b'\0' * 80)
        f.write(struct.pack('<I', len(tris)))
        for tri in tris:
            v0, v1, v2 = tri
            normal = np.cross(v1 - v0, v2 - v0)
            norm = np.linalg.norm(normal)
            normal = normal / norm if norm > 1e-12 else np.array([0.0, 0.0, 1.0])
            f.write(struct.pack('<3f', *normal))
            f.write(struct.pack('<9f', *tri.flatten()))
            f.write(struct.pack('<H', 0))


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    src = sys.argv[1]
    tris = read_stl(src)
    print(f"三角形总数: {len(tris)}")

    bad_nan = ~np.all(np.isfinite(tris), axis=(1, 2))
    bad_ext = np.abs(tris).max(axis=(1, 2)) > 1e6
    bad = bad_nan | bad_ext
    print(f"NaN/Inf 面: {bad_nan.sum()} | 极端坐标面(>1e6): {bad_ext.sum()} | 合计坏面: {bad.sum()} ({bad.sum()/len(tris)*100:.2f}%)")

    zs = tris[:, :, 2].reshape(-1)
    print(f"坐标范围: x[{tris[:,:,0].min():.2f},{tris[:,:,0].max():.2f}] "
          f"y[{tris[:,:,1].min():.2f},{tris[:,:,1].max():.2f}] "
          f"z[{zs.min():.2f},{zs.max():.2f}]")
    print(f"z 分位数: P50={np.percentile(zs,50):.2f} P90={np.percentile(zs,90):.2f} "
          f"P95={np.percentile(zs,95):.2f} P99={np.percentile(zs,99):.2f} max={zs.max():.2f}")

    verdict = "✅ 文件干净" if bad.sum() == 0 else f"❌ {bad.sum()} 个坏面（渲染/切片前需过滤）"
    print(verdict)

    if "--write-clean" in sys.argv:
        out = sys.argv[sys.argv.index("--write-clean") + 1]
        good = tris[~bad]
        write_stl(out, good)
        # 复验
        tris2 = read_stl(out)
        bad2 = (~np.all(np.isfinite(tris2), axis=(1, 2))) | (np.abs(tris2).max(axis=(1, 2)) > 1e6)
        print(f"已写回: {out} ({len(good)} 面), 复验坏面: {bad2.sum()}")
    return 0 if bad.sum() == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
