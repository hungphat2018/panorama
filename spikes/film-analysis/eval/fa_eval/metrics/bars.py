"""Black-bar metrics (research table, row "Bars"): frames that cut real picture (must be 0), residual bar pixels, crop changes per hour and
the share that land on cuts, time to the first correct crop, stability inside ambiguous (black picture) frames.

Rectangles are (x0, y0, x1, y1) half-open in source storage pixels, one per frame: T = truth, D = the crop the method would apply.
"""
from __future__ import annotations

import numpy as np

CUT_TOL_PX = 2          # a crop that is 1 px short of the picture can come from rounding to even sizes; 2 px or more is a cut


def rects_from_segments(segments: list[dict], n: int, default: tuple[int, int, int, int] | None = None) -> np.ndarray:
    out = np.zeros((n, 4), np.int32)
    if default is not None:
        out[:] = default
    covered = np.zeros(n, bool)
    for s in segments:
        a, b = s["frames"]
        out[a:b] = s["rect"]
        covered[a:b] = True
    if default is None and not covered.all():
        raise ValueError("segments do not cover every frame")
    return out


def mask_from_ranges(ranges: list[list[int]], n: int) -> np.ndarray:
    m = np.zeros(n, bool)
    for a, b in ranges or []:
        m[a:b] = True
    return m


def cut_px(T: np.ndarray, D: np.ndarray) -> np.ndarray:
    """Per frame, how many pixels of real picture the crop removes (worst side)."""
    return np.max([np.maximum(0, D[:, 0] - T[:, 0]), np.maximum(0, D[:, 1] - T[:, 1]),
                   np.maximum(0, T[:, 2] - D[:, 2]), np.maximum(0, T[:, 3] - D[:, 3])], axis=0)


def bar_px(T: np.ndarray, D: np.ndarray) -> np.ndarray:
    """Per frame, how many pixels of bar the crop leaves (worst side)."""
    return np.max([np.maximum(0, T[:, 0] - D[:, 0]), np.maximum(0, T[:, 1] - D[:, 1]),
                   np.maximum(0, D[:, 2] - T[:, 2]), np.maximum(0, D[:, 3] - T[:, 3])], axis=0)


def crop_changes(D: np.ndarray) -> np.ndarray:
    """Frame indices i where D[i] != D[i-1]."""
    if len(D) < 2:
        return np.zeros(0, np.int64)
    return np.nonzero(np.any(D[1:] != D[:-1], axis=1))[0] + 1


def bars_metrics(T: np.ndarray, D: np.ndarray, fps: float, *, cuts=(), ambiguous: np.ndarray | None = None, cut_slack: int = 1) -> dict:
    """Metrics for one clip. `cuts` are the frames where a crop change is allowed (real cuts, first frame of the new shot);
    `ambiguous` marks frames whose picture area is black (the truth cannot be seen there)."""
    n = len(T)
    amb = np.zeros(n, bool) if ambiguous is None else ambiguous
    cp, bp = cut_px(T, D), bar_px(T, D)
    ch = crop_changes(D)
    cuts = np.asarray(sorted(cuts), dtype=np.int64)
    on_cut = int(sum(1 for c in ch if cuts.size and np.abs(cuts - c).min() <= cut_slack))
    clear = ~amb
    ok = (cp < CUT_TOL_PX) & (bp < CUT_TOL_PX)
    first_ok = None
    # first frame from which the crop is right (no cut, no bars) and stays right for 24 frames
    good = ok & clear
    for i in range(n - 24):
        if good[i] and good[i:i + 24].all():
            first_ok = i
            break
    area_t = (T[:, 2] - T[:, 0]) * (T[:, 3] - T[:, 1])
    ix = np.maximum(0, np.minimum(T[:, 2], D[:, 2]) - np.maximum(T[:, 0], D[:, 0]))
    iy = np.maximum(0, np.minimum(T[:, 3], D[:, 3]) - np.maximum(T[:, 1], D[:, 1]))
    area_d = (D[:, 2] - D[:, 0]) * (D[:, 3] - D[:, 1])
    residual_area = (area_d - ix * iy) / np.maximum(area_t, 1)
    return {
        "n_frames": n, "hours": n / fps / 3600.0, "ambiguous_frames": int(amb.sum()),
        "cut_frames": int((cp >= CUT_TOL_PX).sum()), "cut_frames_share": float((cp >= CUT_TOL_PX).mean()),
        "cut_frames_ge1": int((cp >= 1).sum()), "cut_frames_clear": int(((cp >= CUT_TOL_PX) & clear).sum()),
        "cut_frames_ambiguous": int(((cp >= CUT_TOL_PX) & amb).sum()), "cut_px_worst": int(cp.max()) if n else 0,
        "bar_frames_share": float((bp >= CUT_TOL_PX).mean()), "bar_px_mean": float(bp.mean()), "bar_px_p95": float(np.quantile(bp, 0.95)) if n else 0.0,
        "residual_area_mean": float(residual_area.mean()),
        "crop_changes": int(len(ch)), "crop_changes_per_hour": float(len(ch) / (n / fps / 3600.0)) if n else 0.0,
        "crop_changes_on_cuts": on_cut, "crop_changes_on_cuts_share": (on_cut / len(ch)) if len(ch) else 1.0,
        "crop_changes_in_ambiguous": int(sum(1 for c in ch if amb[c])),
        "first_correct_frame": first_ok,
    }


def pool(results: list[dict]) -> dict:
    n = sum(r["n_frames"] for r in results)
    hours = sum(r["hours"] for r in results)
    cutf = sum(r["cut_frames"] for r in results)
    ch = sum(r["crop_changes"] for r in results)
    onc = sum(r["crop_changes_on_cuts"] for r in results)
    return {"n_frames": n, "cut_frames": cutf, "cut_frames_share": cutf / n if n else 0.0,
            "bar_frames_share": sum(r["bar_frames_share"] * r["n_frames"] for r in results) / n if n else 0.0,
            "cut_px_worst": max((r["cut_px_worst"] for r in results), default=0), "crop_changes": ch,
            "crop_changes_per_hour": ch / hours if hours else 0.0, "crop_changes_on_cuts_share": onc / ch if ch else 1.0}
