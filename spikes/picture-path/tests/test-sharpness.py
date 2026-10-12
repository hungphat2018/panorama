"""Checks the slanted-edge analysis (pp_sharpness.py) on synthetic edges with known properties: a Gaussian-blurred edge of
sigma s seen through a 1 px box aperture has MTF = exp(-2 pi^2 s^2 f^2) * sinc(f), and an edge blurred by a Lanczos-3 kernel
overshoots by the kernel's known amount.
  python -I tests/test-sharpness.py
"""
import math
import os
import sys

import numpy as np
from scipy import special

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from pp_sharpness import slanted_edge_mtf  # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILED.append(name)


def synthetic(profile, angle_deg=5.0, w=80, h=160, n=8):
    """Edge tilted by angle_deg from vertical; profile(d) is the edge value at signed distance d; each pixel is the mean over
    an n x n grid of sub-samples (the true box aperture)."""
    img = np.zeros((h, w))
    for iy in range(n):
        for ix in range(n):
            xx, yy = np.meshgrid(np.arange(w) + (ix + 0.5) / n, np.arange(h) + (iy + 0.5) / n)
            d = (xx - 40.0 - math.tan(math.radians(angle_deg)) * (yy - 80.0)) * math.cos(math.radians(angle_deg))
            img += profile(d)
    return img / (n * n)


def gaussian(sigma):
    return lambda d: 0.1 + 0.8 * 0.5 * (1 + special.erf(d / (sigma * math.sqrt(2.0))))


for sigma in (0.5, 0.7, 1.0, 1.5):
    res = slanted_edge_mtf(synthetic(gaussian(sigma)), 5, 75, 10, 150)
    f = np.linspace(0.001, 0.5, 4000)
    mtf = np.exp(-2 * math.pi ** 2 * sigma ** 2 * f ** 2) * np.abs(np.sinc(f))
    want = float(np.interp(0.5, mtf[::-1], f[::-1]))
    check("Gaussian sigma %.1f: MTF50 %.4f vs %.4f" % (sigma, res["mtf50"], want), abs(res["mtf50"] - want) < 0.04 * want + 0.002,
          "angle %.2f deg" % res["angle_deg"])
    check("Gaussian sigma %.1f: no overshoot" % sigma, res["overshoot_pct"] < 0.5 and res["undershoot_pct"] < 0.5)


def lanczos3_edge():
    # edge response of Lanczos-3: the integral of the kernel
    t = np.linspace(-3, 3, 60001)
    k = np.sinc(t) * np.sinc(t / 3.0)
    cdf = np.cumsum(k) * (t[1] - t[0])
    cdf /= cdf[-1]
    return lambda d: 0.1 + 0.8 * np.interp(d, t, cdf, left=0.0, right=1.0)


res = slanted_edge_mtf(synthetic(lanczos3_edge()), 5, 75, 10, 150)
# Lanczos-3's step response overshoots by about 8.9 percent of the step (and undershoots about 4.2) before the pixel box
check("Lanczos-3 edge: overshoot reported", 4.0 < res["overshoot_pct"] < 12.0, "%.1f %% / undershoot %.1f %%" % (res["overshoot_pct"], res["undershoot_pct"]))
print("%d failed" % len(FAILED))
sys.exit(1 if FAILED else 0)
