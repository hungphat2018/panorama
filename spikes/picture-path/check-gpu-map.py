"""Compares the pixel -> source map the S2 shader computes on the GPU (float32; harness run with --debug-map) with the float64
geometry of pp_geometry.py: the position (source pixels) and the Jacobian diagonal.

  bin/picture-path.exe render --scenario scenarios/curve-t1.json --structure s2 --debug-map --out out/map-curve-t1
  python -I check-gpu-map.py --scenario scenarios/curve-t1.json --gpu out/map-curve-t1
"""
import argparse
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pp_geometry as geo  # noqa: E402
import pp_reference as ref  # noqa: E402
from pp_image import read_canvas  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--gpu", required=True)
    args = ap.parse_args()
    setup = ref.Setup(ref.load_scenario(args.scenario))
    worst = 0.0
    for k, m in enumerate(setup.room.monitors):
        g, _ = read_canvas(os.path.join(args.gpu, m.id))
        px, py = geo.monitor_grid(m)
        sx, sy, j = geo.source_jacobian(setup.room, k, setup.picture, px, py, setup.model, setup.curvature)
        dx, dy = np.abs(g[..., 0] - sx), np.abs(g[..., 1] - sy)
        dj = np.abs(g[..., 2] - j[..., 0, 0]), np.abs(g[..., 3] - j[..., 1, 1])
        i = np.unravel_index(np.argmax(np.hypot(dx, dy)), dx.shape)
        print("%-9s position error (source px): max %.2e p99 %.2e mean %.2e at (row %d, col %d, src %.1f, %.1f) | Jacobian j00 max %.2e j11 max %.2e" % (
            m.id, max(dx.max(), dy.max()), np.percentile(np.hypot(dx, dy), 99), np.hypot(dx, dy).mean(), i[0], i[1], sx[i], sy[i], dj[0].max(), dj[1].max()))
        worst = max(worst, float(max(dx.max(), dy.max())))
        # where along the monitor does the error live
        col_err = np.hypot(dx, dy).mean(axis=0)
        print("          mean error by 8 column bands:", " ".join("%.1e" % col_err[b:b + m.W // 8].mean() for b in range(0, m.W - m.W // 8 + 1, m.W // 8)))
    return 0 if worst < 1e-3 else 1


if __name__ == "__main__":
    sys.exit(main())
