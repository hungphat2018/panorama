"""The black point: a film of flat bands at the darkest sRGB codes must reach pass 1's canvas with the codes it was made from.

  python -I check-ramp.py make OUT_PPM          write the test picture (1920x1080 bands of codes 0, 1, 2, 3, 4, 6, 8, 10, 16, 32, 64, 128, 255)
  python -I check-ramp.py check DUMP_DIR        compare the dumped canvases with the bands

libplacebo's colour mapping adapts a source's black level to the target's: with no black point declared it infers a 1000:1
display and moves near-black by about 3 codes. The picture path declares true black for the prepared frames (see
perf1-picture-prepare.h), so the canvas holds sRGB-decode(code) for every band: the check encodes each plateau of the canvas back to
8-bit codes and requires it within 0.3 code of the band's code (rounding of the half-float canvas is far below that).
"""
import os
import sys

import numpy as np

PP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "picture-path")
sys.path.insert(0, PP)
from pp_image import read_canvas, write_ppm  # noqa: E402
from pp_resample import linear_to_srgb  # noqa: E402

CODES = [0, 1, 2, 3, 4, 6, 8, 10, 16, 32, 64, 128, 255]


def make(path):
    img = np.zeros((1080, 1920, 3), np.uint8)
    edges = np.linspace(0, 1920, len(CODES) + 1).astype(int)
    for code, a, b in zip(CODES, edges[:-1], edges[1:]):
        img[:, a:b] = code
    write_ppm(path, img)


def check(dump_dir):
    bad = False
    for name in sorted(f[:-5] for f in os.listdir(dump_dir) if f.endswith(".json")):
        canvas, _ = read_canvas(os.path.join(dump_dir, name))
        row = canvas[canvas.shape[0] // 2]
        covered = row[row[:, 3] >= 0.999][:, :3]
        code = 255.0 * linear_to_srgb(np.clip(covered.astype(np.float64), 0.0, 1.0))
        # plateaus: runs of nearly equal values at least 8 pixels long (the bands are 100+ pixels wide on the screens)
        found = []
        start = 0
        for i in range(1, len(code) + 1):
            if i == len(code) or np.max(np.abs(code[i] - code[start])) > 0.2:
                if i - start >= 8:
                    found.append(np.median(code[start:i], axis=0))
                start = i
        found = np.array(found)
        # plateaus come in the bands' order: the first is the nearest code, the rest follow it
        seen, worst = [], 0.0
        if len(found):
            first = int(np.argmin(np.abs(np.array(CODES) - found[0, 0])))
            for k, plateau in enumerate(found):
                if first + k >= len(CODES):
                    worst = 99.0
                    break
                seen.append(CODES[first + k])
                worst = max(worst, float(np.abs(plateau - CODES[first + k]).max()))
        print("%-9s bands found %s; worst difference %.3f code" % (name, seen, worst))
        if len(seen) < 5 or worst > 0.3:
            bad = True
    return 1 if bad else 0


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "make":
        make(sys.argv[2])
    elif len(sys.argv) == 3 and sys.argv[1] == "check":
        sys.exit(check(sys.argv[2]))
    else:
        print(__doc__)
        sys.exit(2)
