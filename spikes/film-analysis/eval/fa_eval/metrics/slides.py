"""Camera-slide metrics (research table, row "Slides"): purity precision and recall, per-frame position error against an exact path,
false-smooth rate on layered shots.

A truth run is a frame range [a, b) with a class: pure (the whole picture slides as one sheet: smoothing it is right), layered (static plate,
subtitles or other overlay on a sliding background), parallax (layers at different speeds), subject (something moves on its own over the
slide), nonrigid (zoom, rotation, handheld). Only pure runs may be smoothed. Positions are source px: positive x moves the content right.
"""
from __future__ import annotations

import numpy as np

PURE = "pure"


def _overlap(a, b) -> int:
    return max(0, min(a[1], b[1]) - max(a[0], b[0]))


def run_overlap_share(run, mask: np.ndarray) -> float:
    a, b = run
    return float(mask[a:b].mean()) if b > a else 0.0


def purity_metrics(truth_runs: list[dict], pred_runs: list[dict], n: int, *, min_cover: float = 0.5) -> dict:
    """truth_runs: [{"frames": [a, b], "class": ...}]; pred_runs: [{"frames": (a, b), "pure": bool}].

    A truth run counts as predicted pure when predicted-pure runs cover at least min_cover of its frames. A predicted pure run is judged by
    the truth run it overlaps most: correct if that run is pure, wrong if it is another class, unlabelled if it overlaps no truth run.
    """
    pure_mask = np.zeros(n, bool)
    for r in pred_runs:
        if r.get("pure"):
            a, b = r["frames"]
            pure_mask[a:b] = True
    t_pure = [t for t in truth_runs if t["class"] == PURE]
    t_other = [t for t in truth_runs if t["class"] != PURE and t["class"] != "none"]
    found = sum(1 for t in t_pure if run_overlap_share(tuple(t["frames"]), pure_mask) >= min_cover)
    false_smooth = [t for t in t_other if run_overlap_share(tuple(t["frames"]), pure_mask) >= min_cover]
    correct = wrong = unlabelled = 0
    by_class: dict[str, int] = {}
    for r in pred_runs:
        if not r.get("pure"):
            continue
        best, best_o = None, 0
        for t in truth_runs:
            o = _overlap(tuple(r["frames"]), tuple(t["frames"]))
            if o > best_o:
                best, best_o = t, o
        if best is None:
            unlabelled += 1
        elif best["class"] == PURE:
            correct += 1
        else:
            wrong += 1
            by_class[best["class"]] = by_class.get(best["class"], 0) + 1
    judged = correct + wrong
    return {"truth_pure": len(t_pure), "truth_other": len(t_other), "pure_found": found,
            "recall": found / len(t_pure) if t_pure else None,
            "pred_pure": correct + wrong + unlabelled, "pred_pure_correct": correct, "pred_pure_wrong": wrong, "pred_pure_unlabelled": unlabelled,
            "precision": correct / judged if judged else None,
            "false_smooth": len(false_smooth), "false_smooth_rate": len(false_smooth) / len(t_other) if t_other else None,
            "wrong_by_class": by_class}


def path_error(true_xy: np.ndarray, pred_step_xy: np.ndarray, frames: tuple[int, int], *, ramp_speed_px: float = 1.0) -> dict:
    """Per-frame position error over a run. true_xy: [N,2] cumulative content displacement (px) of the exact path; pred_step_xy: [N,2]
    predicted displacement of frame i against frame i-1 (row 0 ignored). Both paths are zeroed at the run's first frame.
    Reported over the frames of the run: median / p95 / max Euclidean error of the position, the same for the per-frame step, and the
    position error on ramp frames (steps slower than ramp_speed_px) separately."""
    a, b = frames
    t = true_xy[a:b] - true_xy[a]
    p = np.cumsum(np.vstack([[0.0, 0.0], pred_step_xy[a + 1:b]]), axis=0)
    err = np.hypot(*(p - t).T)
    step_t = np.diff(t, axis=0)
    step_p = pred_step_xy[a + 1:b]
    serr = np.hypot(*(step_p - step_t).T) if len(step_t) else np.zeros(0)
    speed = np.hypot(*step_t.T) if len(step_t) else np.zeros(0)
    ramp = speed < ramp_speed_px
    out = {"frames": b - a, "pos_median": float(np.median(err)), "pos_p95": float(np.quantile(err, 0.95)), "pos_max": float(err.max()),
           "step_median": float(np.median(serr)) if len(serr) else 0.0, "step_p95": float(np.quantile(serr, 0.95)) if len(serr) else 0.0,
           "step_max": float(serr.max()) if len(serr) else 0.0, "ramp_frames": int(ramp.sum())}
    if ramp.any():
        out["step_median_ramp"] = float(np.median(serr[ramp]))
        out["step_median_fast"] = float(np.median(serr[~ramp])) if (~ramp).any() else None
    return out
