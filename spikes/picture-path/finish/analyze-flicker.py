"""Flicker metrics of the surround, computed from the CSVs that `finish-test surround` writes.

Run: python -I analyze-flicker.py DIR [--md]      (DIR holds <scenario>__<config>.csv files)

Each CSV is one film sequence rendered at 240 Hz refreshes through the real passes: per refresh, the mean linear
luminance of a row of the glow 24 px above the picture (top_near), the same 24 px below (bot_near), farther rows
(top_far), and of the picture's own edge row (pic_edge). "S" below is the mean of top_near and bot_near: the brightest
part of the glow, the part nearest the picture, where a change is easiest to see.

What "flicker" means here (every number is derived from S; thresholds are provisional [D] and are confirmed by eye):

  abrupt25    largest change of lightness L* within 25 ms. A change that fast is read as a pop, not a fade.
  swell100    largest change of L* within 100 ms: how fast the glow moves when the picture is not doing it.
              For both, windows that contain a SNAP cut are left out: a snap moves the glow on the same refresh as the
              picture, so it is part of the cut, not a flicker added to it. The size of those steps is reported as
              snap_step.
  flashes/s   the WCAG 2.2 general-flash test applied to S: a flash is a pair of opposite changes of relative luminance
              of 0.1 or more (darker side below 0.8); more than 3 flashes in any one second fails (SC 2.3.1).
  reversals/s the most direction reversals of L* (each at least 2 L* tall) in any 1 s window: the glow breathing.
  pic fl/s    the same flash count for the picture's own edge, for scale: the glow should never flash more than the picture.
  lag         mean and 95th percentile of |L*(S) - L*(S of the exact glow)|: what smoothing costs in agreement with the picture.
  flash seq   peak rise of L* after a 2-frame white flash, as a share of the rise the exact glow has, and the time the
              glow stays more than 1 L* above where it was.
  grain seq   RMS change of L* from one film frame to the next in a shot that does not change but has 5 % grain.
"""
import csv
import glob
import math
import os
import sys

import numpy as np

REFRESH_HZ = 240.0
LIM = {"abrupt25": 3.0, "swell100": 6.0, "flashes": 3.0, "flash_rise": 6.0, "shimmer": 0.25}


def lstar(y):
    y = np.clip(np.asarray(y, dtype=float), 0.0, None)
    return np.where(y > 216.0 / 24389.0, 116.0 * np.cbrt(y) - 16.0, y * 24389.0 / 27.0)


def load(path):
    meta = {}
    with open(path, encoding="utf-8") as f:
        first = f.readline()
        for token in first.lstrip("# ").split():
            if "=" in token:
                k, v = token.split("=", 1)
                meta[k] = v
        rows = list(csv.DictReader(f))
    cols = {k: np.array([float(r[k]) for r in rows]) for k in rows[0]}
    return meta, cols


def zigzag(values, delta):
    """Turning points of a series, ignoring reversals smaller than delta. Returns [(index, value)], where the index is
    the first refresh at which the series got within delta/10 of the extreme (a noisy plateau has no single peak)."""
    pts = []
    if len(values) == 0:
        return pts
    tol = 0.1 * delta
    lo = hi = values[0]
    lo_i = hi_i = 0
    seg = 0
    direction = 0

    def first_near(start, end, target):
        for j in range(start, end + 1):
            if abs(values[j] - target) <= tol:
                return j
        return end

    for i, v in enumerate(values):
        if v > hi:
            hi, hi_i = v, i
        if v < lo:
            lo, lo_i = v, i
        if direction >= 0 and hi - v >= delta:
            pts.append((first_near(seg, hi_i, hi), hi))
            direction = -1
            seg = hi_i
            lo, lo_i = v, i
            hi = v
        elif direction <= 0 and v - lo >= delta:
            pts.append((first_near(seg, lo_i, lo), lo))
            direction = 1
            seg = lo_i
            hi, hi_i = v, i
            lo = v
    return pts


def max_per_second(indices, n):
    """Most events (given as refresh indices) inside any window of one second."""
    if not len(indices):
        return 0
    idx = np.sort(np.asarray(indices))
    best = 0
    j = 0
    for i in range(len(idx)):
        while idx[i] - idx[j] >= REFRESH_HZ:
            j += 1
        best = max(best, i - j + 1)
    return best


def flashes_per_second(y, delta=0.1):
    pts = zigzag(list(y), delta)
    # a transition is the change between successive turning points; count it at the later point
    trans = [pts[i + 1][0] for i in range(len(pts) - 1) if abs(pts[i + 1][1] - pts[i][1]) >= delta]
    return max_per_second(trans, len(y)) / 2.0


def reversals_per_second(L, prominence=2.0):
    pts = zigzag(list(L), prominence)
    return max_per_second([p[0] for p in pts[1:-1]], len(L)), max(len(pts) - 2, 0)


def window_change(L, w, excluded):
    """Largest |L[k+w] - L[k]| over windows that contain no refresh in `excluded` (a boolean array)."""
    if len(L) <= w:
        return 0.0
    d = np.abs(L[w:] - L[:-w])
    bad = np.zeros(len(d), dtype=bool)
    ex = np.nonzero(excluded)[0]
    for k in ex:
        lo = max(k - w, 0)
        hi = min(k, len(d) - 1)
        bad[lo:hi + 1] = True
    d = d[~bad]
    return float(d.max()) if len(d) else 0.0


def analyze(path, ref_cols):
    meta, c = load(path)
    S = 0.5 * (c["top_near"] + c["bot_near"])
    L = lstar(S)
    rule = int(meta["rule"])
    snap_cut = (c["cut"] > 0.5) & (rule == 0)
    out = {"name": meta["config"], "scenario": meta["scenario"]}
    out["abrupt25"] = window_change(L, 6, snap_cut)
    out["swell100"] = window_change(L, 24, snap_cut)
    steps = [abs(L[k] - L[k - 1]) for k in np.nonzero(snap_cut)[0] if k > 0]
    out["snap_step"] = max(steps) if steps else 0.0
    out["flashes"] = flashes_per_second(S)
    out["pic_flashes"] = flashes_per_second(c["pic_edge"])
    out["rev_max"], out["rev_total"] = reversals_per_second(L)
    if ref_cols is not None and len(ref_cols["top_near"]) == len(S):
        Sref = 0.5 * (ref_cols["top_near"] + ref_cols["bot_near"])
        Lref = lstar(Sref)
        e = np.abs(L - Lref)
        out["lag_mean"], out["lag_p95"] = float(e.mean()), float(np.percentile(e, 95))
        out["ref_rev_total"] = reversals_per_second(Lref)[1]
    fa, fn = int(meta["flash_frame"]), int(meta["flash_frames"])
    if fn > 0 and ref_cols is not None:
        t0, t1 = fa / 24.0, (fa + fn) / 24.0
        k0, k1 = int(t0 * REFRESH_HZ), int(t1 * REFRESH_HZ)
        base = float(np.mean(L[max(k0 - 24, 0):k0]))
        Lref = lstar(0.5 * (ref_cols["top_near"] + ref_cols["bot_near"]))
        amp = float(Lref[k0:k1 + 1].max() - base)
        rise = float(L[k0:min(k1 + 240, len(L))].max() - base)
        out["flash_amp"], out["flash_rise"], out["flash_resp"] = amp, rise, rise / amp if amp > 0 else 0.0
        after = np.nonzero(np.abs(L[k1:] - base) > 3.0)[0]
        out["afterglow"] = float((after.max() + 1) / REFRESH_HZ) if len(after) else 0.0
    if meta["scenario"].startswith("strobe"):
        steady = S[int(0.5 * REFRESH_HZ):]
        out["strobe_pp"] = float(steady.max() - steady.min())
        out["strobe_pic_pp"] = float(c["pic_edge"][int(0.5 * REFRESH_HZ):].max() - c["pic_edge"][int(0.5 * REFRESH_HZ):].min())
    if meta["scenario"] == "grain":
        k_frame_end = np.nonzero(np.diff(c["frame"]) > 0)[0]
        Lf = L[k_frame_end][2:]
        d = np.diff(Lf)
        out["shimmer"] = float(np.sqrt(np.mean(d * d)))
    return out


def verdict(o):
    fails = []
    if o["abrupt25"] > LIM["abrupt25"]:
        fails.append("abrupt")
    if o["swell100"] > LIM["swell100"]:
        fails.append("swell")
    if o["flashes"] > LIM["flashes"]:
        fails.append("WCAG")
    if "flash_rise" in o and o["flash_rise"] > LIM["flash_rise"]:
        fails.append("flash response")
    if "shimmer" in o and o["shimmer"] > LIM["shimmer"]:
        fails.append("grain shimmer")
    return fails


def vstr(o):
    if o["name"].startswith("ref"):
        return "reference"
    v = verdict(o)
    return "ok" if not v else "FAIL " + ",".join(v)


def table(rows, cols, md):
    widths = [max(len(str(r[i])) for r in [cols] + rows) for i in range(len(cols))]
    sep = " | " if md else "  "
    lines = []
    lines.append(sep.join(str(cols[i]).ljust(widths[i]) for i in range(len(cols))))
    if md:
        lines.append(sep.join("-" * widths[i] for i in range(len(cols))))
    for r in rows:
        lines.append(sep.join(str(r[i]).ljust(widths[i]) for i in range(len(cols))))
    return "\n".join(("| " + l + " |") if md else l for l in lines)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    md = "--md" in sys.argv
    if not args:
        print(__doc__)
        return 2
    folder = args[0]
    by_scn = {}
    for p in sorted(glob.glob(os.path.join(folder, "*__*.csv"))):
        scn = os.path.basename(p).split("__")[0]
        by_scn.setdefault(scn, []).append(p)
    for scn, paths in by_scn.items():
        ref = None
        for p in paths:
            if os.path.basename(p) == f"{scn}__ref.csv":
                ref = load(p)[1]
        print(f"\n### {scn}\n")
        results = [analyze(p, ref) for p in paths]
        if scn == "ten-cuts":
            cols = ["config", "snap step", "abrupt25", "swell100", "flashes/s", "pic fl/s", "reversals/s", "lag mean", "lag p95", "verdict"]
            rows = []
            for o in results:
                rows.append([o["name"], f"{o['snap_step']:.1f}", f"{o['abrupt25']:.1f}", f"{o['swell100']:.1f}", f"{o['flashes']:.1f}",
                             f"{o['pic_flashes']:.1f}", o["rev_max"], "-" if o["name"] == "black" else f"{o.get('lag_mean', 0):.1f}",
                             "-" if o["name"] == "black" else f"{o.get('lag_p95', 0):.1f}", vstr(o)])
        elif scn == "flash":
            cols = ["config", "flash amp (L*)", "glow rise (L*)", "response", "afterglow s (>3 L*)", "abrupt25", "swell100", "flashes/s", "verdict"]
            rows = []
            for o in results:
                rows.append([o["name"], f"{o.get('flash_amp', 0):.1f}", f"{o.get('flash_rise', 0):.1f}", f"{100 * o.get('flash_resp', 0):.0f} %",
                             f"{o.get('afterglow', 0):.2f}", f"{o['abrupt25']:.1f}", f"{o['swell100']:.1f}", f"{o['flashes']:.1f}",
                             vstr(o)])
        elif scn.startswith("strobe"):
            cols = ["config", "glow swing (rel. lum.)", "picture swing", "flashes/s", "picture fl/s", "verdict"]
            rows = [[o["name"], f"{o['strobe_pp']:.3f}", f"{o['strobe_pic_pp']:.3f}", f"{o['flashes']:.1f}", f"{o['pic_flashes']:.1f}",
                     "reference" if o["name"] == "ref" else ("ok" if o["flashes"] <= LIM["flashes"] else "FAIL WCAG")] for o in results]
        elif scn == "grain":
            cols = ["config", "shimmer rms (L*)", "verdict"]
            rows = [[o["name"], f"{o.get('shimmer', 0):.3f}", vstr(o)] for o in results]
        else:
            cols = ["config", "lag mean", "lag p95", "abrupt25", "swell100"]
            rows = [[o["name"], f"{o.get('lag_mean', 0):.1f}", f"{o.get('lag_p95', 0):.1f}", f"{o['abrupt25']:.1f}", f"{o['swell100']:.1f}"] for o in results]
        print(table(rows, cols, md))
    print(f"\nthresholds: abrupt25 <= {LIM['abrupt25']} L*, swell100 <= {LIM['swell100']} L*, flashes <= {LIM['flashes']}/s (WCAG 2.2 SC 2.3.1), "
          f"glow rise after a 2-frame flash <= {LIM['flash_rise']} L*, grain shimmer <= {LIM['shimmer']} L*")
    return 0


if __name__ == "__main__":
    sys.exit(main())
