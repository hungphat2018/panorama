"""Compares pass 1's canvas from a --dump-canvas run with the float64 reference of the picture-path harness, per monitor.

  python -I compare-dump.py DUMP_DIR --room ROOM_JSON --source-even PPM [--source-odd PPM] [--limit 1.0]

The dump's JSON says what the canvas was made from: the surface (fold or curve), and for each frame in it (one, or two when the
rolling blend mixed a neighbouring frame in) the shot's crop and place, the camera slide of that refresh (the picture's
displacement at row 0 and per row), whether the monitor magnified the picture (sigmoid domain), and the blend's weights. From
that this script builds the harness's scenario for each frame, renders the references (pp_reference.render_monitor), mixes
them row by row with the blend's weights, and prints the harness's own statistics (compare-canvases.py): the largest and the
99th-percentile difference in 8-bit code values over the pixels away from edges.

ROOM_JSON is the room the dump's pixel sizes belong to (for a windowed run at --window-scale 0.5, the room with native_px
halved); frames of the film are the patterns: even film frames show --source-even, odd ones --source-odd (a film made from
two alternating pictures, which is what makes a blend visible).

Exit status 1 if the largest difference away from edges exceeds --limit (default 1.0 code value) or a domain differs.
"""
import argparse
import json
import os
import sys

import numpy as np

PP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "picture-path")
sys.path.insert(0, PP)
import pp_reference as ref  # noqa: E402
from pp_compare import compare  # noqa: E402
from pp_image import read_canvas, read_ppm, write_ppm  # noqa: E402


def cropped(path, crop, out_dir):
    """The source cut to the crop (a PPM in out_dir). The performer takes a tap beyond the crop from the crop's edge pixel, so
    the black bars a crop removes never bleed into the picture; the harness's reference clamps to the whole texture, and
    reproduces the performer exactly when it is given the cropped picture."""
    x0, y0, x1, y1 = (int(v) for v in crop)
    if [x0, y0, x1, y1] != list(crop):
        raise ValueError("a fractional crop cannot be reproduced by cutting the source")
    out = os.path.join(out_dir, "crop-%d-%d-%d-%d-%s" % (x0, y0, x1, y1, os.path.basename(path)))
    write_ppm(out, read_ppm(path)[y0:y1, x0:x1])
    return out


def scenario(meta, view, monitor, room_path, even, odd, room_scan_ms, rows, out_dir):
    """The harness scenario that reproduces one frame's view of the dumped canvas of one monitor."""
    crop = view["crop"]
    src = even if (view["ord"] % 2 == 0 or not odd) else odd
    if crop != [0.0, 0.0, 1920.0, 1080.0]:
        src = cropped(src, crop, out_dir)
    scn = {
        "room": room_path,
        "surface": {"model": meta["surface"], "curvature": meta["curvature"]},
        "source": {"file": src, "width": 1920, "height": 1080, "sar": 1.0},
        "cue": {"centre_mm": view["centre_pp_mm"], "height_mm": view["height_mm"]},
        "kernel": meta.get("kernel", "ewa_lanczossharp"),
        "domain": "auto",
    }
    du0, dv0, ddu, ddv = view["slide"]
    if any(v != 0.0 for v in (du0, dv0, ddu, ddv)):
        # the dump's displacement is du0 + ddu * row mm (right / down); the harness moves the picture by v * (t - t_ref) with
        # t = t_top + row / rows * scan_ms and v UP: so v = rate per row * rows / scan, t_top = offset at row 0 / v
        vu, vv = ddu * rows * 1000.0 / room_scan_ms, -ddv * rows * 1000.0 / room_scan_ms
        t_top = du0 * 1000.0 / vu if vu else (-dv0) * 1000.0 / vv
        if vu and vv and abs(-dv0 * 1000.0 / vv - t_top) > 1e-6 * max(1.0, abs(t_top)):
            raise ValueError("the slide's two components do not share a time origin: not expressible as one harness slide")
        scn["slide"] = {"velocity_mm_s": [vu, vv], "t_ref_ms": 0.0, "t_top_ms": {monitor: t_top}}
    return scn


def render(meta, view, name, room_path, even, odd, scan, rows, out_dir):
    setup = ref.Setup(scenario(meta, view, name, room_path, even, odd, scan, rows, out_dir))
    k = [m.id for m in setup.room.monitors].index(name)
    return ref.render_monitor(setup, k, None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dump_dir")
    ap.add_argument("--room", required=True)
    ap.add_argument("--source-even", required=True)
    ap.add_argument("--source-odd", default=None)
    ap.add_argument("--kernel", default=None)
    ap.add_argument("--limit", type=float, default=1.0)
    args = ap.parse_args()
    room_path = os.path.abspath(args.room)
    even = os.path.abspath(args.source_even)
    odd = os.path.abspath(args.source_odd) if args.source_odd else None
    names = sorted(f[:-5] for f in os.listdir(args.dump_dir) if f.endswith(".json"))
    bad = False
    for name in names:
        gpu, meta = read_canvas(os.path.join(args.dump_dir, name))
        if args.kernel:
            meta["kernel"] = args.kernel
        rows = gpu.shape[0]
        room = json.load(open(room_path, "r", encoding="utf-8"))
        scan = next(m["timing"]["scan_ms"] for m in room["monitors"] if m["id"] == name)
        want, domain = render(meta, meta["a"], name, room_path, even, odd, scan, rows, args.dump_dir)
        b = meta["blend"]
        domain_b = None
        if b["partner"]:
            want_b, domain_b = render(meta, meta["b"], name, room_path, even, odd, scan, rows, args.dump_dir)
            w = np.clip(b["wa"] + b["wb"] * (np.arange(rows) + 0.5), 0.0, 1.0)[:, None, None]
            want = (1.0 - w) * want + w * want_b          # both premultiplied by their own coverage: the mix is exact
        res = compare(gpu, want)
        res.pop("_diff")
        res.pop("_masks")
        i, fl, ed = res["interior"], res["flat"], res["edge"]
        slide = any(meta["a"]["slide"]) or (b["partner"] and any(meta["b"]["slide"]))
        domains_ok = meta["a"]["domain"] == domain and (not b["partner"] or meta["b"]["domain"] == domain_b)
        print("%-9s %s, domain %s%s, slide %s, blend %s | interior: n=%d max %.3f p99 %.3f p99.9 %.3f mean %.4f (>1: %d) | "
              "flat max %.3f | edge max %.3f | alpha err %.1e" % (
                  name, meta["surface"], domain, "/" + domain_b if domain_b else "", "yes" if slide else "no",
                  "%d (a %d b %d, crops %s / %s)" % (b["partner"], meta["a"]["ord"], meta["b"]["ord"], meta["a"]["crop"], meta["b"]["crop"])
                  if b["partner"] else "no", i.get("n", 0), i.get("max", 0), i.get("p99", 0), i.get("p99.9", 0), i.get("mean", 0),
                  i.get("over1", 0), fl.get("max", 0), ed.get("max", 0), res["alpha_interior_max_diff"]))
        if not domains_ok or i.get("max", 0) > args.limit or i.get("n", 0) == 0:
            bad = True
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
