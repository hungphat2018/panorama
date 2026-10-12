"""Layer L1: the per-shot tables derived from the raw per-frame signals (L0) in seconds, without decoding.

Inputs are the signal columns, the frame table, the file facts and the raw subject detections. Everything here is policy:
thresholds, the snapping tolerance, purity limits can change and the tables are simply re-derived.

Covered stretches (frames with scanned == 1) get their boundaries from the shot model; stretches not yet scanned are cut at
their keyframes and flagged provisional, with crops from the keyframes' own rectangles. So shots.json always covers the whole
film, and a planner can start from the pre-scan alone.

Three tables come out of the signals: shots (the spine: boundaries, crops, text in the bars, brightness, motion), slide-paths and
subjects (derive_slides_subjects.py). slide-paths and subjects refer to shots by id (a run or a track names its shot) and the shots
table refers to neither, so any of the three can be made again alone: `want` names the ones to build. The shot boundaries they all
need are computed every time (derive_spine.py).
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from . import bars_crop, shots_transnet, slides_motion as sm
from . import versions
from .derive_slides_subjects import build_slide_paths, build_subjects
from .derive_spine import Spine, make_spine
from .frame_table import FrameTable

BAR_MIN_PX = 16               # a side counts as a bar when the crop leaves at least this many px
TEXT_MARGIN_PX = 8            # text must start this far inside the bar (picture edge noise is not text)
TEXT_EXTRA_PX = 4             # safety added to the needed extension
TEXT_MIN_RUN = 3              # frames in a row

ALL_TABLES = frozenset({"shots", "slide-paths", "subjects"})


def derive(store, table: FrameTable, facts: dict, raw_subjects: Optional[dict], scan_done: list,
           want: Optional[set] = None) -> dict:
    """Returns {'shots': doc, 'slide_paths': doc, 'subjects': doc, 'facts_update': {...}}. An entry is None when it was not asked
    for in `want` (names of tables, default all three) or, for subjects, when there are no raw detections yet."""
    want = ALL_TABLES if want is None else want
    sp = make_spine(store, table, facts)
    out = {"shots": None, "slide_paths": None, "subjects": None, "facts_update": {}}
    if "shots" in want:
        out["shots"], out["facts_update"] = build_shots(sp, store, table, facts, scan_done)
    if "slide-paths" in want:
        out["slide_paths"] = build_slide_paths(sp, store)
    if "subjects" in want and raw_subjects is not None:
        out["subjects"] = build_subjects(sp, raw_subjects)
    return out


def build_shots(sp: Spine, store, table: FrameTable, facts: dict, scan_done: list) -> tuple:
    """The shots document and the parts of file-facts that come from the scan (crop vocabulary, text in the bars)."""
    n, W, H, key, sc, n_scanned = sp.n, sp.W, sp.H, sp.key, sp.sc, sp.n_scanned
    firsts, lasts, starts_s, ends_s, bounds = sp.firsts, sp.lasts, sp.starts_s, sp.ends_s, sp.bounds
    rect = store.col("crop_rect")
    bars_valid = store.col("bars_valid").astype(bool)
    edges = {s: store.col("edge_" + s) for s in ("top", "bottom", "left", "right")}
    dx, dy = store.col("dx").astype(np.float32), store.col("dy").astype(np.float32)
    before = store.col("diff_before").astype(np.float32)
    luma_mean, luma_p95 = store.col("luma_mean").astype(np.float32), store.col("luma_p95").astype(np.float32)

    # ---- per-shot crop evidence -> vocabulary -> crops ----------------------------------------------------------------------
    ok = bars_crop.valid_mask(rect.astype(np.int16), W, H) & bars_valid
    evidence = [bars_crop.shot_evidence(rect, ok, a, b + 1) for a, b in zip(firsts, lasts)]
    for ev, a, b in zip(evidence, firsts, lasts):
        ev["weight"] = b - a + 1
    vocab_modes = bars_crop.build_vocabulary(evidence)
    crops = bars_crop.assign_shot_crops(evidence, vocab_modes, W, H)
    vocabulary = [{"id": i, "rect": bars_crop.to_half_open(m["rect"]), "share": m["share"],
                   "source": "scan" if n_scanned else "keyframes"} for i, m in enumerate(vocab_modes)]

    chapters = [c for c in facts.get("chapters", []) if c["kind_guess"] != "normal"]
    shots = []
    text_frames_total = {s: 0 for s in edges}
    bar_frames_total = {s: 0 for s in edges}
    ext_max = {s: 0 for s in edges}
    for i, (a, b) in enumerate(zip(firsts, lasts)):
        bd = bounds.get(a)
        provisional = not bool(sc[a:b + 1].all())
        crop = dict(crops[i])
        ev = evidence[i]
        if provisional and ev["n_valid"] > 0 and not sc[a:b + 1].any():
            crop["evidence"] = "keyframe"          # only the keyframe's own rectangle: cautious
        # text in the bars: how far the crop must reach into each bar to keep what is written there
        extend = {"top": 0, "bottom": 0, "left": 0, "right": 0}
        text_frames = {"top": 0, "bottom": 0, "left": 0, "right": 0}
        if crop["rect"] is not None and sc[a:b + 1].any():
            x0, y0, x1, y1 = crop["rect"]
            thick = {"top": y0, "bottom": H - y1, "left": x0, "right": W - x1}
            for s, tk in thick.items():
                if tk < BAR_MIN_PX:
                    continue
                seg_edges = edges[s][a:b + 1].astype(np.int32)[sc[a:b + 1]]
                bar_frames_total[s] += len(seg_edges)
                pos = seg_edges < tk - TEXT_MARGIN_PX
                pos = _runs_at_least(pos, TEXT_MIN_RUN if len(pos) >= TEXT_MIN_RUN else 1)
                if pos.any():
                    text_frames[s] = int(pos.sum())
                    text_frames_total[s] += int(pos.sum())
                    extend[s] = int(min(tk, tk - int(seg_edges[pos].min()) + TEXT_EXTRA_PX))
                    ext_max[s] = max(ext_max[s], extend[s])
        # luma and motion over the scanned frames of the shot
        scs = sc[a:b + 1]
        shot_motion = None
        luma = None
        if scs.any():
            idx = np.arange(a, b + 1)[scs]
            luma = {"mean": round(float(luma_mean[idx].mean()), 1), "p95_max": int(luma_p95[idx].max()), "p95_mean": round(float(luma_p95[idx].mean()), 1)}
            pair_idx = idx[idx > a]          # pairs inside the shot (the pair crossing the cut belongs to the boundary)
            if len(pair_idx):
                moving = before[pair_idx] >= sm.HELD_BELOW
                speed = np.hypot(dx[pair_idx], dy[pair_idx]) * sp.fps
                mv = speed[moving]
                shot_motion = {"held_share": round(float(1.0 - moving.mean()), 3),
                               "speed_px_s_p50": round(float(np.percentile(mv, 50)), 2) if len(mv) else 0.0,
                               "speed_px_s_p90": round(float(np.percentile(mv, 90)), 2) if len(mv) else 0.0,
                               "speed_picture_heights_s_p90": round(float(np.percentile(mv, 90)) / H, 4) if len(mv) else 0.0}
        region = next(({"kind": c["kind_guess"], "source": "chapter"} for c in chapters if c["start_s"] <= starts_s[i] < c["end_s"]),
                      {"kind": "normal", "source": "none"})
        shots.append({
            "id": i, "first_frame": a, "last_frame": b, "frames": b - a + 1, "start_s": starts_s[i], "end_s": ends_s[i],
            "boundary_in": ({"kind": "start", "source": "file-start", "p_single": None, "p_many": None, "span": None, "keyframe_aligned": bool(key[0])}
                            if bd is None else {k: bd[k] for k in ("kind", "source", "p_single", "p_many", "span", "keyframe_aligned")}),
            "layout_safe": bool(bd is not None and bd["kind"] in ("hard", "gradual")),
            "provisional": provisional, "region": region,
            "crop": {"rect": crop["rect"], "vocab_id": crop["vocab_id"], "confidence": crop["conf"], "evidence": crop["evidence"],
                     "held_from_previous": crop["held_from_previous"], "valid_frames": ev["n_valid"],
                     "agree": round(float(ev["agree"]), 3)},
            "bar_content": {"extend": extend, "text_frames": text_frames},
            "luma": luma, "motion": shot_motion,
        })

    n_hard = sum(1 for b in sp.tn if b["kind"] == "hard")
    doc = {"schema": "panorama.content.shots/0", "version": versions.SHOTS_TABLE, "state": sp.state, "frame_count": n,
           "time_base": [table.tb_num, table.tb_den],
           "coverage": {"scanned_frames": n_scanned, "scanned_intervals": scan_done},
           "counts": {"shots": len(shots), "transnet_hard": n_hard, "transnet_gradual": len(sp.tn) - n_hard, "keyframe_provisional": len(sp.kfb)},
           "params": {"cut_threshold": shots_transnet.CUT_THRESHOLD, "many_threshold": shots_transnet.MANY_THRESHOLD,
                      "crop_cluster_tol_px": bars_crop.CLUSTER_TOL, "snap_tol_px": bars_crop.SNAP_TOL, "vocab_share": bars_crop.VOCAB_SHARE,
                      "text_margin_px": TEXT_MARGIN_PX, "text_min_run_frames": TEXT_MIN_RUN},
           "vocabulary": vocabulary, "shots": shots}
    text_summary = {s: {"bar_frames": bar_frames_total[s], "frames_with_text": text_frames_total[s],
                        "share": round(text_frames_total[s] / bar_frames_total[s], 4) if bar_frames_total[s] else None,
                        "max_extension_px": ext_max[s]} for s in edges}
    facts_update = {"crop_vocabulary": {"source": "scan" if n_scanned else "keyframes", "modes": vocabulary},
                    "text_in_bars": {"source": "scan" if n_scanned else "none", "per_side": text_summary}}
    return doc, facts_update


def _runs_at_least(mask: np.ndarray, min_len: int) -> np.ndarray:
    """mask with every run of True shorter than min_len cleared."""
    out = np.zeros_like(mask)
    i, m = 0, len(mask)
    while i < m:
        if mask[i]:
            j = i
            while j + 1 < m and mask[j + 1]:
                j += 1
            if j - i + 1 >= min_len:
                out[i:j + 1] = True
            i = j + 1
        else:
            i += 1
    return out
