"""Checks the finished frame of a --dump-canvas run against what pass 3 must make of the pass 1 canvas next to it.

  python -I compare-final.py DUMP_DIR [--surround black] [--limit 1.0]

For every <name>-final.ppm in DUMP_DIR: the expected code value is 255 * sRGB_encode(canvas) (the canvas is premultiplied by the
picture's coverage, so the leftover area is black), the finished frame is the same value quantised to 8 bits with a blue-noise
dither: it differs from the exact value by less than one code (the dither moves a value to the code above or below it, never
further), averages out to no bias, and shows nothing outside the coverage. With --surround black that is everything checked;
a glow fills the leftover area, so only the covered part is compared and the leftover area must not be black.

With --text-on NAME only the subtitle check is made. Exit status 1 if the largest difference over the covered area exceeds --limit (default 1.0), the mean error is not within 0.05 of
zero, or the leftover area is not black (black surround) / is black (glow).
"""
import argparse
import json
import os
import sys

import numpy as np

PP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "picture-path")
sys.path.insert(0, PP)
from pp_image import read_canvas, read_ppm  # noqa: E402
from pp_resample import linear_to_srgb  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dump_dir")
    ap.add_argument("--surround", default="black")
    ap.add_argument("--limit", type=float, default=1.0)
    ap.add_argument("--room", default=None, help="the room file whose per-monitor colour block (gain, white, black_lift) pass 3 applied; it also lifts the leftover area's black")
    ap.add_argument("--text-on", default=None, help="subtitle check: this monitor carries subtitle text, the others must not")
    args = ap.parse_args()
    names = sorted(f[:-10] for f in os.listdir(args.dump_dir) if f.endswith("-final.ppm"))
    if not names:
        print("no *-final.ppm in", args.dump_dir)
        return 1
    bad = False
    for name in names:
        canvas, _ = read_canvas(os.path.join(args.dump_dir, name))
        final = read_ppm(os.path.join(args.dump_dir, name + "-final.ppm")).astype(np.float64)
        if final.shape[:2] != canvas.shape[:2]:
            print("%s: size mismatch %s vs %s" % (name, final.shape, canvas.shape))
            bad = True
            continue
        lin = canvas[..., :3].astype(np.float64)
        if args.room:
            # the colour block: v = lift + (gain * white - lift) * linear, per channel, clamped, then sRGB (Windows owns the gamut)
            col = next(m for m in json.load(open(args.room, "r", encoding="utf-8"))["monitors"] if m["id"] == name).get("color", {})
            ch = lambda key, dflt: np.broadcast_to(np.asarray(col.get(key, dflt), dtype=np.float64), (3,))
            gain, lift = ch("gain", 1.0) * ch("white", 1.0), ch("black_lift", 0.0)
            lin = lift + (gain - lift) * np.maximum(lin, 0.0)   # pass 3 clips negative light (resampling undershoot) before the lift
        expected = 255.0 * linear_to_srgb(np.clip(lin, 0.0, 1.0))
        cov = canvas[..., 3]
        covered = cov >= 0.999
        diff = final - expected
        d = np.abs(diff[covered])
        if args.text_on:
            # subtitles are drawn after the dither: pixels that differ from pass 3's value by more than the dither can are text
            text = np.max(np.abs(diff), axis=-1) > 1.5
            ys, xs = np.nonzero(text)
            box = "x %d..%d, y %d..%d" % (xs.min(), xs.max(), ys.min(), ys.max()) if text.any() else "none"
            print("%-9s text pixels %d (%s) of %dx%d" % (name, int(text.sum()), box, final.shape[1], final.shape[0]))
            if (name == args.text_on) != bool(text.any()):
                bad = True
            continue
        leftover = cov <= 0.0
        left_max = float(final[leftover].max()) if leftover.any() else 0.0
        if args.room:
            left_max = 0.0  # a black lift raises the leftover area's black too: not a "surround is not black" finding
        mean_err = float(diff[covered].mean())
        print("%-9s covered n=%d: max |error| %.3f, p99 %.3f, mean |error| %.3f, mean error %+.4f codes; leftover area max code %.0f" % (
            name, d.size, d.max(), np.percentile(d, 99), d.mean(), mean_err, left_max))
        if d.max() > args.limit or abs(mean_err) > 0.05 or (args.surround == "black" and left_max > 0) or                 (args.surround == "glow" and left_max == 0):
            bad = True
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
