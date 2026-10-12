"""Reference resampler for the picture-path spike: elliptical weighted average (EWA) of a source image at arbitrary
source positions, float64, written from the definition (Heckbert 1989; libplacebo's polar filters are the isotropic case).

For an output pixel with source position c and Jacobian J (columns = source displacement per output pixel step) the
footprint matrix is A = J J^T. Its eigenvalues are clamped to >= 1 (a footprint never gets narrower than one source pixel,
so upscaling keeps the filter's own width and downscaling widens it), giving A'. A source texel at offset d from c has
kernel distance q = sqrt(d^T A'^-1 d) and weight K(q); the sum of weights normalises the result (as libplacebo's polar
sampler does). For J = s * identity this is exactly libplacebo's polar scaler with its kernel widened by max(1, s).
"""
import math

import numpy as np
from scipy.special import j1

JINC_ZERO3 = 3.2383154841662362076499
JINC_ZERO4 = 4.2410628637960698819573
JINC_FIRST_ZERO = 1.2196698912665045
SIGMOID_CENTER = 0.75
SIGMOID_SLOPE = 6.5


def jinc(x):
    x = np.abs(np.asarray(x, dtype=float)) * math.pi
    out = np.ones_like(x)
    m = x > 1e-8
    out[m] = 2.0 * j1(x[m]) / x[m]
    return out


def cubic_bc(x, b, c):
    """Mitchell-Netravali family, radius 2, evaluated for x >= 0 (unnormalised: the sum of weights normalises)."""
    x = np.abs(x)
    p = (12 - 9 * b - 6 * c) * x ** 3 + (-18 + 12 * b + 6 * c) * x ** 2 + (6 - 2 * b)
    q = (-b - 6 * c) * x ** 3 + (6 * b + 30 * c) * x ** 2 + (-12 * b - 48 * c) * x + (8 * b + 24 * c)
    return np.where(x < 1, p / 6.0, np.where(x < 2, q / 6.0, 0.0))


class Kernel:
    """Radial weight K(q) on q in [0, support]; a fine table with linear interpolation stands in for the Bessel calls."""

    def __init__(self, name, fn, support):
        self.name, self.support = name, support
        n = 1 << 16
        self._t = np.linspace(0.0, support, n)
        self._w = fn(self._t)

    def __call__(self, q):
        return np.interp(q, self._t, self._w, right=0.0)


def make_kernel(name):
    """Kernels by their libplacebo / mpv names. Blur and window follow libplacebo's pl_filter_sample."""
    if name in ("ewa_lanczossharp", "ewa_lanczos"):
        blur = 0.98125058372237073562493 if name == "ewa_lanczossharp" else 1.0
        r = JINC_ZERO3

        def fn(t):
            return jinc(t / blur) * jinc(t / (blur * r) * JINC_FIRST_ZERO)

        return Kernel(name, fn, r * blur)
    if name == "ewa_lanczos4sharpest":
        blur, r = 0.88451209326050047745788, JINC_ZERO4

        def fn(t):
            return jinc(t / blur) * jinc(t / (blur * r) * JINC_FIRST_ZERO)

        return Kernel(name, fn, r * blur)
    if name == "ewa_robidoux":                       # Robidoux's cubic, B = 12 / (19 + 9 sqrt 2), C = (1 - B) / 2, as an EWA
        b = 12.0 / (19.0 + 9.0 * math.sqrt(2.0))
        return Kernel(name, lambda t: cubic_bc(t, b, (1.0 - b) / 2.0), 2.0)
    if name == "ewa_catmull_rom":
        return Kernel(name, lambda t: cubic_bc(t, 0.0, 0.5), 2.0)
    if name == "ewa_mitchell":
        return Kernel(name, lambda t: cubic_bc(t, 1.0 / 3.0, 1.0 / 3.0), 2.0)
    raise ValueError("unknown kernel " + name)


def sigmoidize(x, center=SIGMOID_CENTER, slope=SIGMOID_SLOPE):
    offset = 1.0 / (1 + math.exp(slope * center))
    scale = 1.0 / (1 + math.exp(slope * (center - 1))) - offset
    sig = np.clip(x, 0.0, 1.0) * scale + offset
    return (np.log(sig / (1.0 - sig)) - math.log(offset / (1.0 - offset))) / slope


def unsigmoidize(y, center=SIGMOID_CENTER, slope=SIGMOID_SLOPE):
    offset = 1.0 / (1 + math.exp(slope * center))
    scale = 1.0 / (1 + math.exp(slope * (center - 1))) - offset
    bias = math.log(offset / (1.0 - offset))
    y = np.clip(y, 0.0, 1.0)
    return (1.0 / (1.0 + np.exp(-(y * slope + bias))) - 1.0 / (1.0 + math.exp(-bias))) / scale


def srgb_to_linear(c):
    c = np.asarray(c, dtype=float)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(x):
    x = np.clip(np.asarray(x, dtype=float), 0.0, 1.0)
    return np.where(x <= 0.0031308, x * 12.92, 1.055 * np.power(x, 1 / 2.4) - 0.055)


def footprint(j):
    """A' (clamped footprint matrix) of J[..., 2, 2] as (a00, a01, a11) and the matching inverse (w00, w01, w11)."""
    a = j[..., 0, 0] ** 2 + j[..., 0, 1] ** 2
    b = j[..., 0, 0] * j[..., 1, 0] + j[..., 0, 1] * j[..., 1, 1]
    c = j[..., 1, 0] ** 2 + j[..., 1, 1] ** 2
    m = 0.5 * (a + c)
    dlt = np.sqrt((0.5 * (a - c)) ** 2 + b * b)
    l1 = np.maximum(m + dlt, 1.0)
    l2 = np.maximum(m - dlt, 1.0)
    safe = np.where(dlt > 1e-12, dlt, 1.0)
    cos2 = np.where(dlt > 1e-12, 0.5 * (a - c) / safe, 1.0)
    sin2 = np.where(dlt > 1e-12, b / safe, 0.0)
    mm, dd = 0.5 * (l1 + l2), 0.5 * (l1 - l2)
    a00, a01, a11 = mm + dd * cos2, dd * sin2, mm - dd * cos2
    det = l1 * l2
    return (a00, a01, a11), (a11 / det, -a01 / det, a00 / det)


def ewa(src, sx, sy, j, kernel, chunk=150000):
    """src (H, W, C) in the filter domain; sx, sy, j for N output pixels (flat). Returns (N, C). Texel (tx, ty) has its
    centre at (tx + 0.5, ty + 0.5); positions outside the image clamp to the nearest texel (texture CLAMP addressing).
    A pixel whose map is the identity (|J - I| < 1e-3 and the position within 1e-3 of a texel centre) copies that texel:
    a jinc-windowed jinc does not interpolate, so filtering at 1:1 would blur; libplacebo skips its scaler there too."""
    h, w, ch = src.shape
    n = sx.size
    out = np.empty((n, ch))
    for s0 in range(0, n, chunk):
        s1 = min(n, s0 + chunk)
        x, y, jj = sx[s0:s1], sy[s0:s1], j[s0:s1]
        (a00, _, a11), (w00, w01, w11) = footprint(jj)
        hx = kernel.support * np.sqrt(a00)
        hy = kernel.support * np.sqrt(a11)
        rx, ry = int(math.ceil(hx.max())) + 1, int(math.ceil(hy.max())) + 1
        bx, by = np.floor(x).astype(np.int64), np.floor(y).astype(np.int64)
        acc = np.zeros((x.size, ch))
        wsum = np.zeros(x.size)
        for dy in range(-ry, ry + 2):
            ty = by + dy
            ddy = ty + 0.5 - y
            tyc = np.clip(ty, 0, h - 1)
            for dx in range(-rx, rx + 2):
                tx = bx + dx
                ddx = tx + 0.5 - x
                q2 = w00 * ddx * ddx + 2.0 * w01 * ddx * ddy + w11 * ddy * ddy
                q = np.sqrt(np.maximum(q2, 0.0))
                wgt = np.where(q < kernel.support, kernel(q), 0.0)
                txc = np.clip(tx, 0, w - 1)
                acc += wgt[:, None] * src[tyc, txc]
                wsum += wgt
        res = acc / wsum[:, None]
        ident = ((np.abs(jj[:, 0, 0] - 1) < 1e-3) & (np.abs(jj[:, 1, 1] - 1) < 1e-3) & (np.abs(jj[:, 0, 1]) < 1e-3)
                 & (np.abs(jj[:, 1, 0]) < 1e-3) & (np.abs(x - np.floor(x) - 0.5) < 1e-3) & (np.abs(y - np.floor(y) - 0.5) < 1e-3))
        if ident.any():
            res[ident] = src[np.clip(by[ident], 0, h - 1), np.clip(bx[ident], 0, w - 1)]
        out[s0:s1] = res
    return out
