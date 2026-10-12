"""Shot-boundary metrics (research table, row "Shots"): F1 at +-N frames for cuts, transition recall and span IoU, false splits per hour,
flash false positives.

Truth comes from a shots label file: hard cuts (`kind` hard, `frame` = first frame of the new shot), transitions (a span [start, end)
over which both pictures show), and `not_cuts` (flashes and other events a detector must NOT report). A predicted boundary is a point
(hard) or a span (transition). Matching is one-to-one: every truth item takes the nearest unmatched prediction whose frames fall within
`tol` frames of the item.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from fa_eval.types import Boundary


@dataclass
class ShotTruth:
    hard: list[int]
    transitions: list[tuple[int, int, str]]                  # (start, end exclusive, kind)
    not_cuts: list[tuple[int, int, str]]                     # (start, end exclusive, kind)
    transition_info: list[dict] = field(default_factory=list)


def truth_from_labels(lab: dict) -> ShotTruth:
    hard, trans, info = [], [], []
    for b in lab.get("boundaries", []):
        if b.get("kind") == "hard" or not b.get("span"):
            hard.append(int(b["frame"]))
        else:
            s, e = b["span"]
            trans.append((int(s), int(e), b.get("kind", "dissolve")))
            info.append({"transition": b.get("transition", b.get("kind")), "duration": int(e - s)})
    nc = [(int(n["span"][0]), int(n["span"][1]), n.get("kind", "not-a-cut")) for n in lab.get("not_cuts", [])]
    return ShotTruth(sorted(hard), trans, nc, info)


def _interval(b: Boundary) -> tuple[int, int]:
    """Inclusive frame interval a predicted boundary occupies."""
    if b.span:
        return b.span[0], max(b.span[1] - 1, b.span[0])
    return b.frame, b.frame


def match(truth_iv: list[tuple[int, int]], pred_iv: list[tuple[int, int]], tol: int) -> tuple[dict[int, int], set[int]]:
    """One-to-one matching of truth intervals to predicted intervals within tol frames. Returns ({truth idx: pred idx}, unmatched pred idx)."""
    pairs = []
    for i, (ta, tb) in enumerate(truth_iv):
        for j, (pa, pb) in enumerate(pred_iv):
            gap = max(ta - pb, pa - tb, 0)                  # 0 when the intervals overlap
            if gap <= tol:
                pairs.append((gap, abs((ta + tb) - (pa + pb)), i, j))
    pairs.sort()
    used_t, used_p, out = set(), set(), {}
    for _, _, i, j in pairs:
        if i in used_t or j in used_p:
            continue
        used_t.add(i)
        used_p.add(j)
        out[i] = j
    return out, set(range(len(pred_iv))) - used_p


def _prf(tp: int, fp: int, fn: int) -> dict:
    p = tp / (tp + fp) if tp + fp else 1.0
    r = tp / (tp + fn) if tp + fn else 1.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": p, "recall": r, "f1": f}


def span_iou(a: tuple[int, int], b: tuple[int, int]) -> float:
    inter = max(0, min(a[1], b[1]) - max(a[0], b[0]))
    union = (a[1] - a[0]) + (b[1] - b[0]) - inter
    return inter / union if union > 0 else 0.0


def shot_metrics(truth: ShotTruth, pred: list[Boundary], n_frames: int, fps: float, *, tols=(0, 1, 2), main_tol: int = 1,
                 layout_kinds=("hard", "dissolve", "fade_black", "fade_white", "wipe", "slide", "unknown")) -> dict:
    """All shot metrics for one clip. Counts are returned so clips can be pooled before computing rates.

    hard_cuts_*: truth hard cuts against ALL predicted boundaries (a prediction of any kind near a cut is a find).
    transitions: truth spans found by a predicted boundary that touches the span +- tol, and the span IoU of those predictions that carry a span.
    false_splits: predicted boundaries (of a layout-change kind) matching no truth item; per hour of video.
    flash: truth not_cuts (flashes) that drew a predicted boundary within tol: must be zero.
    """
    pred = [b for b in pred if b.kind in layout_kinds]
    p_iv = [_interval(b) for b in pred]
    hard_iv = [(f, f) for f in truth.hard]
    tr_iv = [(s, e - 1) for s, e, _ in truth.transitions]
    res: dict = {"n_frames": n_frames, "hours": n_frames / fps / 3600.0, "n_pred": len(pred), "n_hard": len(truth.hard),
                 "n_transitions": len(truth.transitions), "n_flashes": len(truth.not_cuts)}
    for tol in tols:
        m, extra = match(hard_iv + tr_iv, p_iv, tol)
        n_h = len(hard_iv)
        tp_h = sum(1 for i in m if i < n_h)
        tp_t = sum(1 for i in m if i >= n_h)
        # predictions that match nothing and are not inside a flash window: false splits; those inside a flash window: flash hits
        fl_iv = [(s, e - 1) for s, e, _ in truth.not_cuts]
        fp_idx = sorted(extra)
        flash_hit = {j for j in fp_idx if any(max(a - p_iv[j][1], p_iv[j][0] - b, 0) <= tol for a, b in fl_iv)}
        res[f"tol{tol}"] = {"hard": _prf(tp_h, len(extra), n_h - tp_h), "transitions_found": tp_t, "false_splits": len(extra),
                            "false_splits_not_flash": len(extra) - len(flash_hit), "flash_false_positives": len(flash_hit)}
        res[f"tol{tol}"]["all"] = _prf(tp_h + tp_t, len(extra), len(hard_iv) + len(tr_iv) - tp_h - tp_t)
        if tol == main_tol:
            ious, found_by_type = [], {}
            for i, j in m.items():
                if i >= n_h:
                    k = i - n_h
                    kind = truth.transition_info[k]["transition"] if k < len(truth.transition_info) else truth.transitions[k][2]
                    found_by_type[kind] = found_by_type.get(kind, 0) + 1
                    if pred[j].span:
                        ious.append(span_iou((truth.transitions[k][0], truth.transitions[k][1]), pred[j].span))
            res["span_iou"] = {"n": len(ious), "mean": float(np.mean(ious)) if ious else None, "median": float(np.median(ious)) if ious else None}
            res["transitions_found_by_type"] = found_by_type
            fl_hits_per_flash = 0
            for a, b in fl_iv:
                if any(max(a - pb, pa - b, 0) <= tol for pa, pb in p_iv):
                    fl_hits_per_flash += 1
            res["flash"] = {"flashes": len(fl_iv), "with_false_boundary": fl_hits_per_flash}
            res["false_splits_per_hour"] = (len(extra) - len(flash_hit)) / res["hours"] if res["hours"] else None
            res["false_splits_total_per_hour"] = len(extra) / res["hours"] if res["hours"] else None
    return res


def pool(results: list[dict], main_tol: int = 1) -> dict:
    """Sum counts over clips (same keys) and recompute precision, recall, F1 for the main tolerance."""
    keys = f"tol{main_tol}"
    tp = sum(r[keys]["hard"]["tp"] for r in results)
    fp = sum(r[keys]["hard"]["fp"] for r in results)
    fn = sum(r[keys]["hard"]["fn"] for r in results)
    hours = sum(r["hours"] for r in results)
    out = {"hard": _prf(tp, fp, fn), "hours": hours, "false_splits": sum(r[keys]["false_splits_not_flash"] for r in results),
           "transitions": sum(r["n_transitions"] for r in results), "transitions_found": sum(r[keys]["transitions_found"] for r in results),
           "flashes": sum(r["n_flashes"] for r in results), "flash_false_positives": sum(r[keys]["flash_false_positives"] for r in results)}
    out["false_splits_per_hour"] = out["false_splits"] / hours if hours else None
    return out
