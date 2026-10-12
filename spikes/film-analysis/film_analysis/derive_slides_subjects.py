"""The slide-paths and subjects tables, derived from the signals and the raw detections in seconds.

Both name the shot they belong to by id and nothing in the shots table points back at them, so either can be made again alone
(manifest.stale_tables decides which).
"""
from __future__ import annotations

import math

import numpy as np

from . import slides_motion as sm, subjects_faces as sf, versions
from .derive_spine import Spine


def _f32(a: np.ndarray) -> np.ndarray:
    return a.astype(np.float32)


def build_slide_paths(sp: Spine, store) -> dict:
    n, W, H, t, sc, firsts, lasts = sp.n, sp.W, sp.H, sp.t, sp.sc, sp.firsts, sp.lasts
    sx, sy = W / sm.GRID_W, H / sm.GRID_H
    dx, dy = _f32(store.col("dx")), _f32(store.col("dy"))
    before, after = _f32(store.col("diff_before")), _f32(store.col("diff_after"))
    peak = store.col("pc_peak").astype(np.float32) / 255.0
    expl, stat, othr = (store.col(c).astype(np.float32) / 255.0 for c in ("explained", "static", "other"))

    # runs never cross a cut: the pair of frames across a boundary is in no run
    cut_frames = set(firsts[1:])
    dx_m, dy_m = np.where(sc, dx, 0.0), np.where(sc, dy, 0.0)
    runs = sm.find_runs(dx_m, dy_m, peak, np.where(sc, before, 0.0), expl, cut_frames, sp.fps, 1.0, sx, sy) if sp.n_scanned else []
    shot_of_frame = np.zeros(n, np.int32)
    for i, (a, b) in enumerate(zip(firsts, lasts)):
        shot_of_frame[a:b + 1] = i
    conf_pairs = sm.pair_confidence(before, after)
    run_docs = []
    for rid, r in enumerate(runs):
        f0, f1 = r["first"], r["last"]
        sl = slice(f0, f1 + 1)
        moving = np.hypot(dx[sl] / sx, dy[sl] / sy) >= sm.MIN_SHIFT_GRID
        if not moving.any():
            continue
        e_med = float(np.median(expl[sl][moving]))
        s_med = float(np.median(stat[sl][moving]))
        o_med = float(np.median(othr[sl][moving]))
        total = math.hypot(float(dx[sl].sum()), float(dy[sl].sum()))
        dur = t[f1] - t[f0 - 1]
        xs = np.concatenate([[0.0], np.cumsum(dx[sl])])
        ys = np.concatenate([[0.0], np.cumsum(dy[sl])])
        new_drawing = np.concatenate([[True], before[sl] >= sm.HELD_BELOW])
        conf = np.concatenate([[1.0], conf_pairs[sl]])
        direction = ("vertical" if abs(ys[-1]) > 2 * abs(xs[-1]) else "horizontal" if abs(xs[-1]) > 2 * abs(ys[-1]) else "diagonal")
        run_docs.append({
            "id": rid, "shot": int(shot_of_frame[f0]), "first_frame": f0 - 1, "last_frame": f1,
            "start_s": float(t[f0 - 1]), "end_s": float(t[f1]), "seconds": round(float(dur), 3), "direction": direction,
            "speed_px_s": round(total / dur, 2) if dur > 0 else 0.0, "moving_share": round(float(moving.mean()), 3),
            "purity": {"label": sm.classify_purity(e_med, s_med, o_med), "explained": round(e_med, 3), "static": round(s_med, 4),
                       "other": round(o_med, 4)},
            "gate_ok_share": round(float((conf[1:] > 0).mean()), 3),
            "x": [round(float(v), 3) for v in xs], "y": [round(float(v), 3) for v in ys],
            "new_drawing": [bool(v) for v in new_drawing], "conf": [round(float(v), 3) for v in conf]})
    return {"schema": "panorama.content.slide-paths/0", "version": versions.SLIDE_PATHS, "state": sp.state, "frame_count": n,
            "coverage": {"scanned_frames": sp.n_scanned},
            "params": {"min_run_seconds": 1.0, "min_shift_grid_px": sm.MIN_SHIFT_GRID, "held_below": sm.HELD_BELOW,
                       "gate": f"after <= max({sm.GATE_ABS}, {sm.GATE_REL} * before)",
                       "purity": {"pure": [sm.PURE_MIN_EXPLAINED, sm.PURE_MAX_STATIC, sm.PURE_MAX_OTHER],
                                  "layered": [sm.LAYERED_MAX_EXPLAINED, sm.LAYERED_MIN_STATIC, sm.LAYERED_MIN_OTHER]},
                       "position_unit": "source px, cumulative shift of the picture content since the run's first frame (right and down positive)"},
            "runs": run_docs}


def build_subjects(sp: Spine, raw_subjects: dict) -> dict:
    sample_frames = sorted(raw_subjects)
    docs = []
    for i, (a, b) in enumerate(zip(sp.firsts, sp.lasts)):
        sub = sf.build_shot_subjects(raw_subjects, a, b, sp.W, sp.H, sample_frames)
        docs.append({"shot": i, "samples": sub["samples"], "tracks": sub["tracks"], "keepclear": sub["keepclear"]})
    return {"schema": "panorama.content.subjects/0", "version": versions.SUBJECTS, "state": sp.state, "frame_count": sp.n,
            "grid": [sf.GRID_W, sf.GRID_H], "classes": list(sf.CLASSES), "models": sorted(sf.MODEL_CLASS),
            "sampled_frames": len(raw_subjects), "shots": docs}
