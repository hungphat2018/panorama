"""Lays a run's monitor canvases out at their physical positions (millimetres, the room's fold layout with the gap) into one
sRGB preview PNG, to look at continuity across the seam. Not a measurement.

  python -I compose-wall.py --scenario scenarios/fold-t1.json --run out/checks/d3d11-nvidia-rgb8/fold-t1-s2 --out out/png/wall-fold-t1.png [--px-per-mm 2]
"""
import argparse
import os
import sys

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pp_reference as ref  # noqa: E402
from pp_image import linear_to_code, read_canvas  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--run", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--px-per-mm", type=float, default=2.0)
    args = ap.parse_args()
    setup = ref.Setup(ref.load_scenario(args.scenario))
    room = setup.room
    s = args.px_per_mm
    total_w = int(round((room.u0[-1] + room.monitors[-1].w_mm) * s))
    top = max(m.centre[1] + 0.5 * m.h_mm for m in room.monitors)
    bottom = min(m.centre[1] - 0.5 * m.h_mm for m in room.monitors)
    total_h = int(round((top - bottom) * s))
    wall = np.zeros((total_h, total_w, 3), dtype=np.uint8)
    wall[...] = (40, 0, 40)
    for k, m in enumerate(room.monitors):
        canvas, _ = read_canvas(os.path.join(args.run, m.id))
        rgb = np.clip(np.rint(linear_to_code(np.clip(canvas[..., :3], 0, None))), 0, 255).astype(np.uint8)
        img = Image.fromarray(rgb, "RGB").resize((int(round(m.w_mm * s)), int(round(m.h_mm * s))), Image.LANCZOS)
        x0 = int(round(room.u0[k] * s))
        y0 = int(round((top - (m.centre[1] + 0.5 * m.h_mm)) * s))
        wall[y0:y0 + img.height, x0:x0 + img.width] = np.asarray(img)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    Image.fromarray(wall, "RGB").save(args.out)
    print("wrote", args.out, wall.shape)


if __name__ == "__main__":
    sys.exit(main())
