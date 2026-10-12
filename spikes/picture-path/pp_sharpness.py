"""Sharpness, ringing and aliasing measurements on a rendered T7 canvas (linear light, premultiplied RGBA).

slanted_edge_mtf   ISO 12233 style: edge position per row, line fit, 4x oversampled edge spread function, line spread,
                   Hann window, FFT. Frequencies are in cycles per OUTPUT pixel. Also the overshoot and undershoot of the edge
                   (percent of the step, in linear light) and the same in 8-bit code values.
zone_response      RMS of the output in annuli of the zone plate, as a fraction of the source's RMS (in code values): a
                   perfect reconstruction keeps 1 below the output's Nyquist frequency; beyond it, anything left is aliasing.
"""
import math

import numpy as np

from pp_image import linear_to_code


def _lum(canvas):
    a = np.maximum(canvas[..., 3], 1e-6)
    return (0.2126 * canvas[..., 0] + 0.7152 * canvas[..., 1] + 0.0722 * canvas[..., 2]) / a


def slanted_edge_mtf(lum, x0, x1, y0, y1, oversample=4):
    """lum: 2D linear luminance; the ROI [y0:y1, x0:x1] holds one near-vertical edge. Returns a dict."""
    roi = lum[y0:y1, x0:x1].astype(np.float64)
    h, w = roi.shape
    left, right = np.median(roi[:, : max(3, w // 8)]), np.median(roi[:, -max(3, w // 8):])
    lo, hi = min(left, right), max(left, right)
    mid = 0.5 * (lo + hi)
    rising = right > left
    xs = np.arange(w) + 0.5
    pos = np.empty(h)
    for r in range(h):
        row = roi[r] if rising else roi[r][::-1]
        idx = np.nonzero((row[:-1] < mid) & (row[1:] >= mid))[0]
        if len(idx) == 0:
            pos[r] = np.nan
            continue
        i = idx[len(idx) // 2]
        t = (mid - row[i]) / (row[i + 1] - row[i])
        p = (i + 0.5) + t                     # between pixel centres i and i + 1
        pos[r] = p if rising else (w - p)
    ok = ~np.isnan(pos)
    rows = np.arange(h)[ok] + 0.5
    m, b = np.polyfit(rows, pos[ok], 1)
    yy, xx = np.meshgrid(np.arange(h) + 0.5, xs, indexing="ij")
    d = xx - (m * yy + b)                      # signed distance (pixels, along x) from the fitted edge
    bins = np.round(d * oversample).astype(int)
    lim = int(min(abs(bins.min()), abs(bins.max())) * 0.9)
    lim = min(lim, 12 * oversample)
    esf = np.zeros(2 * lim + 1)
    for k in range(-lim, lim + 1):
        sel = bins == k
        esf[k + lim] = roi[sel].mean() if sel.any() else np.nan
    nans = np.isnan(esf)
    if nans.any():
        esf[nans] = np.interp(np.nonzero(nans)[0], np.nonzero(~nans)[0], esf[~nans])
    step = (esf[-max(4, oversample):].mean() - esf[: max(4, oversample)].mean())
    plateau_lo, plateau_hi = esf[: max(4, oversample)].mean(), esf[-max(4, oversample):].mean()
    top, bottom = max(plateau_lo, plateau_hi), min(plateau_lo, plateau_hi)
    over = (esf.max() - top) / (top - bottom) * 100.0
    under = (bottom - esf.min()) / (top - bottom) * 100.0
    lsf = np.diff(esf)
    lsf *= np.hanning(len(lsf))
    n = 1 << 12
    spec = np.abs(np.fft.rfft(lsf, n))
    spec /= spec[0]
    freq = np.fft.rfftfreq(n, d=1.0 / oversample)          # cycles per output pixel
    res = {"angle_deg": math.degrees(math.atan(m)), "step": float(abs(step))}

    def at(f):
        return float(np.interp(f, freq, spec))

    def crossing(level):
        i = np.nonzero(spec < level)[0]
        if len(i) == 0:
            return float("nan")
        i = i[0]
        f0, f1, s0, s1 = freq[i - 1], freq[i], spec[i - 1], spec[i]
        return float(f0 + (s0 - level) / (s0 - s1) * (f1 - f0))

    res.update({"mtf50": crossing(0.5), "mtf30": crossing(0.3), "mtf10": crossing(0.1), "mtf_at_0.25": at(0.25), "mtf_at_0.4": at(0.4),
                "overshoot_pct": float(over), "undershoot_pct": float(under)})
    # the same overshoot in 8-bit code values (what the eye sees), relative to the code step
    c = linear_to_code(np.clip(esf, 0, None))
    ct, cb = max(c[0], c[-1]), min(c[0], c[-1])
    res["overshoot_code_pct"] = float((c.max() - ct) / (ct - cb) * 100.0)
    res["undershoot_code_pct"] = float((cb - c.min()) / (ct - cb) * 100.0)
    return res


def zone_response(canvas, sx, sy, centre, bands, src_amplitude_codes=110.0):
    """canvas (H, W, 4) linear; sx, sy: source position of every output pixel; centre (x, y) of the zone plate in source pixels;
    bands: list of (r_a, r_b) source radii. Returns {band: RMS(output codes) / RMS(source codes)}."""
    codes = linear_to_code(np.clip(canvas[..., 1] / np.maximum(canvas[..., 3], 1e-6), 0, None))
    r = np.hypot(sx - centre[0], sy - centre[1])
    valid = canvas[..., 3] > 0.999
    out = {}
    src_rms = src_amplitude_codes / math.sqrt(2.0)
    for ra, rb in bands:
        m = valid & (r >= ra) & (r < rb)
        if not m.any():
            continue
        v = codes[m]
        out["%d-%d" % (ra, rb)] = float(np.std(v) / src_rms)
    return out
