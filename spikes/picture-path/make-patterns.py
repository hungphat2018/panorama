"""Generates the exact test pictures (binary PPM, 8-bit sRGB codes) into patterns/. Everything is a closed-form
function of the pixel position with analytic edge coverage, so every run writes the same bytes.

  T1  geometry: 100 px grid with 2 px lines, three circles (r = 100 px at 1080p) centred on the seam line, diagonals
      at 30/45/60 degrees through the picture centre, a 2 px frame at the picture edge.
  T7  sharpness across the seam: two slanted edges (a square rotated 5 degrees), a zone plate centred on the seam (its
      local frequency reaches the source Nyquist at the rim), vertical line pairs of period 2, 3 and 4 px straddling the
      seam, and fine text.
  noise       full-entropy RGB noise: any hidden resample shows up as a changed code (identity test).
  shear       thin vertical bars: the bar position per row shows a slide's shear.
  flat-a/-b   two flat greys for the per-row blend weights.

  python -I make-patterns.py [--seam-x 960]      (writes patterns/*.ppm)
"""
import argparse
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from pp_image import write_ppm  # noqa: E402


def cover(half_width, dist):
    """Coverage of a stroke of width 2 * half_width whose centre line is `dist` away from the pixel centre."""
    return np.clip(half_width + 0.5 - np.abs(dist), 0.0, 1.0)


def paint(img, cov, colour):
    c = np.asarray(colour, dtype=float)
    img[...] = img * (1.0 - cov[..., None]) + c * cov[..., None]


def grid(w, h):
    x = np.arange(w, dtype=float) + 0.5
    y = np.arange(h, dtype=float) + 0.5
    return np.meshgrid(x, y)


def make_t1(w, h, seam_x):
    s = w / 1920.0
    xx, yy = grid(w, h)
    img = np.empty((h, w, 3))
    img[...] = 28.0
    pitch = 100.0 * s
    cy = h / 2.0
    dx = np.abs(((xx - seam_x + 0.5 * pitch) % pitch) - 0.5 * pitch)
    dy = np.abs(((yy - cy + 0.5 * pitch) % pitch) - 0.5 * pitch)
    paint(img, np.maximum(cover(1.0 * s, dx), cover(1.0 * s, dy)), (150, 150, 150))
    for ang, col in ((30, (255, 80, 80)), (45, (80, 255, 80)), (60, (110, 150, 255))):
        a = math.radians(ang)
        d = (xx - seam_x) * math.sin(a) - (yy - cy) * math.cos(a)        # distance to the line through the centre
        paint(img, cover(1.25 * s, d), col)
        d2 = (xx - seam_x) * math.sin(-a) - (yy - cy) * math.cos(-a)
        paint(img, cover(1.25 * s, d2), col)
    for k in (-1, 0, 1):
        r = np.hypot(xx - seam_x, yy - (cy + k * 270.0 * s))
        paint(img, cover(1.5 * s, r - 100.0 * s), (240, 240, 240))
    edge = np.minimum(np.minimum(xx, w - xx), np.minimum(yy, h - yy))
    paint(img, cover(1.0, edge - 1.0), (255, 255, 255))
    return np.clip(np.rint(img), 0, 255).astype(np.uint8)


def box_sdf(xx, yy, cx, cy, half, angle_deg):
    a = math.radians(angle_deg)
    c, s = math.cos(a), math.sin(a)
    px, py = xx - cx, yy - cy
    qx = np.abs(c * px + s * py) - half
    qy = np.abs(-s * px + c * py) - half
    outside = np.hypot(np.maximum(qx, 0.0), np.maximum(qy, 0.0))
    inside = np.minimum(np.maximum(qx, qy), 0.0)
    return outside + inside


def line_pairs(xx, period, x0, x1):
    """Vertical bars (bright first half of each period), integrated over the pixel width for exact coverage."""
    acc = np.zeros_like(xx)
    n = 16
    for i in range(n):
        xs = xx - 0.5 + (i + 0.5) / n
        acc += ((xs - x0) % period) < 0.5 * period
    return np.where((xx >= x0) & (xx < x1), acc / n, np.nan)


def make_t7(w, h, seam_x):
    s = w / 1920.0
    xx, yy = grid(w, h)
    img = np.empty((h, w, 3))
    img[...] = 128.0
    cy = h / 2.0
    for cx in (seam_x - 480.0 * s, seam_x + 480.0 * s):
        block = np.maximum(np.abs(xx - cx), np.abs(yy - cy)) - 220.0 * s
        paint(img, np.clip(0.5 - block, 0.0, 1.0), (215, 215, 215))
        sq = box_sdf(xx, yy, cx, cy, 150.0 * s, 5.0)
        paint(img, np.clip(0.5 - sq, 0.0, 1.0), (40, 40, 40))
    r = np.hypot(xx - seam_x, yy - cy)
    r_max = 256.0 * s
    k = 0.5 / r_max                                    # local frequency k * r cycles per pixel: 0.5 at the rim
    zp = 128.0 + 110.0 * np.cos(math.pi * k * r * r)
    rim = np.clip(r_max - r + 0.5, 0.0, 1.0)
    img[...] = img * (1.0 - rim[..., None]) + zp[..., None] * rim[..., None]
    for i, period in enumerate((2.0, 3.0, 4.0)):
        y0 = 90.0 * s + i * 50.0 * s
        band = (yy >= y0) & (yy < y0 + 40.0 * s)
        for x0, x1 in ((seam_x - 120.0 * s, seam_x + 120.0 * s),):
            f = line_pairs(xx, period, x0, x1)
            m = band & ~np.isnan(f)
            v = 30.0 + 195.0 * np.where(m, f, 0.0)
            img[m] = v[m][:, None]
    from PIL import Image, ImageDraw, ImageFont
    band_h = int(60 * s)
    txt = Image.new("L", (int(900 * s), band_h), 215)
    d = ImageDraw.Draw(txt)
    font = ImageFont.load_default()
    d.text((8, 8), "SHARPNESS ACROSS THE SEAM 0123456789 the quick brown fox", fill=20, font=font)
    d.text((8, 28), "ABCDEFGHIJKLMNOPQRSTUVWXYZ abcdefghijklmnopqrstuvwxyz", fill=20, font=font)
    tx0, ty0 = int(seam_x - 450 * s), int(h - 150 * s)
    a = np.asarray(txt, dtype=float)
    img[ty0:ty0 + a.shape[0], tx0:tx0 + a.shape[1]] = a[..., None]
    return np.clip(np.rint(img), 0, 255).astype(np.uint8)


def make_noise(w, h, seed):
    rng = np.random.default_rng(seed)
    return rng.integers(0, 256, size=(h, w, 3), dtype=np.uint8)


def make_shear(w, h):
    xx, yy = grid(w, h)
    img = np.zeros((h, w, 3))
    d = np.abs(((xx - 100.0 + 60.0) % 120.0) - 60.0)           # bars every 120 px, centred at 100 + 120 i
    paint(img, cover(1.5 * w / 1920.0, d), (255, 255, 255))
    return np.clip(np.rint(img), 0, 255).astype(np.uint8)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(HERE, "patterns"))
    ap.add_argument("--seam-x", type=float, default=None, help="seam position in source pixels (default: picture centre)")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    for w, h in ((1920, 1080), (3840, 2160)):
        seam = args.seam_x * w / 1920.0 if args.seam_x is not None else w / 2.0
        write_ppm(os.path.join(args.out, "t1-%dx%d.ppm" % (w, h)), make_t1(w, h, seam))
        write_ppm(os.path.join(args.out, "t7-%dx%d.ppm" % (w, h)), make_t7(w, h, seam))
    write_ppm(os.path.join(args.out, "noise-2560x1600.ppm"), make_noise(2560, 1600, 1))
    write_ppm(os.path.join(args.out, "noise-1920x1080.ppm"), make_noise(1920, 1080, 2))
    write_ppm(os.path.join(args.out, "shear-1920x1080.ppm"), make_shear(1920, 1080))
    for name, code in (("flat-a", 40), ("flat-b", 220)):
        write_ppm(os.path.join(args.out, name + "-1920x1080.ppm"), np.full((1080, 1920, 3), code, dtype=np.uint8))
    print("wrote", args.out)


if __name__ == "__main__":
    sys.exit(main())
