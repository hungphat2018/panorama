"""Renders a scenario's canvases with the float64 reference (see pp_reference.py) and writes them as float32.

  python -I reference-render.py --scenario scenarios/fold-t1.json --out ref/fold-t1 [--monitor laptop] [--rows 400:420]

Writes <out>/<monitor id>.f32 + .json (RGBA, premultiplied, linear light, row 0 = top).
"""
import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pp_reference as ref  # noqa: E402
from pp_image import write_canvas_f32  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--monitor", default=None, help="monitor id (default all)")
    ap.add_argument("--rows", default=None, help="r0:r1, render only these rows")
    args = ap.parse_args()
    setup = ref.Setup(ref.load_scenario(args.scenario))
    rows = tuple(int(v) for v in args.rows.split(":")) if args.rows else None
    for k, m in enumerate(setup.room.monitors):
        if args.monitor and m.id != args.monitor:
            continue
        t0 = time.time()
        canvas, domain = ref.render_monitor(setup, k, rows)
        meta = {"monitor": m.id, "producer": "reference-render.py", "domain": domain, "kernel": setup.kernel.name,
                "model": setup.model, "curvature": setup.curvature, "rows": list(rows) if rows else None}
        write_canvas_f32(os.path.join(args.out, m.id), canvas, meta)
        print("%s: %dx%d, domain %s, %.1f s" % (m.id, m.W, m.H, domain, time.time() - t0))


if __name__ == "__main__":
    sys.exit(main())
