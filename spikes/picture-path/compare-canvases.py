"""Compares a harness run with the float64 reference render, per monitor, in 8-bit code values away from edges.

  python -I compare-canvases.py --gpu out/fold-t1-s2 --ref ref/fold-t1 [--monitor laptop] [--json out.json] [--png out/png]

Prints one line per monitor and monitor-wise statistics; --png also writes a preview of the GPU canvas and a difference
image (8x amplified, codes). Exit status 1 if the interior maximum exceeds --limit (default 1.0 code value).
"""
import argparse
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from pp_compare import compare  # noqa: E402
from pp_image import read_canvas, write_preview_png  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gpu", required=True)
    ap.add_argument("--ref", required=True)
    ap.add_argument("--monitor", default=None)
    ap.add_argument("--json", default=None)
    ap.add_argument("--png", default=None)
    ap.add_argument("--limit", type=float, default=1.0)
    args = ap.parse_args()
    names = [args.monitor] if args.monitor else sorted(f[:-5] for f in os.listdir(args.gpu) if f.endswith(".json") and f != "run.json")
    report, bad = {}, False
    for name in names:
        gpu, gmeta = read_canvas(os.path.join(args.gpu, name))
        ref, rmeta = read_canvas(os.path.join(args.ref, name))
        if gpu.shape != ref.shape:
            print("%s: size mismatch %s vs %s" % (name, gpu.shape, ref.shape))
            bad = True
            continue
        res = compare(gpu, ref)
        diff = res.pop("_diff")
        res.pop("_masks")
        report[name] = res
        i, co, fl, ed = res["interior"], res["core"], res["flat"], res["edge"]
        print("%-9s interior: n=%d max %.3f p99 %.3f p99.9 %.3f mean %.4f (>1: %d) | core max %.3f | flat max %.3f | edge max %.3f | "
              "covered max %.3f | alpha err %.2e, outside %.1e | gpu range %.3f..%.3f | domain gpu=%s ref=%s" % (
                  name, i.get("n", 0), i.get("max", 0), i.get("p99", 0), i.get("p99.9", 0), i.get("mean", 0), i.get("over1", 0),
                  co.get("max", 0), fl.get("max", 0), ed.get("max", 0), res["covered"].get("max", 0), res["alpha_interior_max_diff"],
                  res["alpha_outside_max"], res["gpu_range"][0], res["gpu_range"][1], gmeta.get("domain"), rmeta.get("domain")))
        if i.get("max", 0) > args.limit:
            bad = True
        if args.png:
            write_preview_png(os.path.join(args.png, name + "-gpu.png"), gpu)
            from PIL import Image
            img = np.clip(np.rint(diff * 8.0), 0, 255).astype(np.uint8)
            os.makedirs(args.png, exist_ok=True)
            Image.fromarray(img, "L").save(os.path.join(args.png, name + "-diff-x8.png"))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=1)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
