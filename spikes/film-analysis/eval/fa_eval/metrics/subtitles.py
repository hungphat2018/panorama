"""Dialogue metrics (research table, row "Dialogue"): dialogue / sign / unknown accuracy and macro-F1, width error of the rendered box,
correct "crosses a seam" prediction.

Truth classes are dialogue and sign (a positioned or standalone line the file places on the picture: title, caption, credit, song).
A method may also answer "unknown"; accuracy is reported over the events it decided and coverage says how many that was. The planner
moves only dialogue and never a sign, so the costly error is a sign called dialogue.
"""
from __future__ import annotations

import numpy as np

CLASSES = ("dialogue", "sign")


def class_metrics(truth: list[str], pred: list[str]) -> dict:
    assert len(truth) == len(pred)
    n = len(truth)
    decided = [(t, p) for t, p in zip(truth, pred) if p in CLASSES]
    cm = {t: {p: 0 for p in (*CLASSES, "unknown")} for t in CLASSES}
    for t, p in zip(truth, pred):
        cm[t][p if p in CLASSES else "unknown"] += 1
    acc_decided = float(np.mean([t == p for t, p in decided])) if decided else None
    acc_all = float(np.mean([t == p for t, p in zip(truth, pred)])) if n else None      # unknown counts as wrong
    f1s = {}
    for c in CLASSES:
        tp = sum(1 for t, p in zip(truth, pred) if t == c and p == c)
        fp = sum(1 for t, p in zip(truth, pred) if t != c and p == c)
        fn = sum(1 for t, p in zip(truth, pred) if t == c and p != c)
        pr = tp / (tp + fp) if tp + fp else 0.0
        rc = tp / (tp + fn) if tp + fn else 0.0
        f1s[c] = {"precision": pr, "recall": rc, "f1": 2 * pr * rc / (pr + rc) if pr + rc else 0.0, "support": tp + fn}
    present = [c for c in CLASSES if f1s[c]["support"] > 0]
    return {"n": n, "coverage": len(decided) / n if n else None, "accuracy_decided": acc_decided, "accuracy_all": acc_all,
            "macro_f1": float(np.mean([f1s[c]["f1"] for c in present])) if present else None, "per_class": f1s, "confusion": cm,
            "signs_called_dialogue": cm["sign"]["dialogue"], "dialogue_called_sign": cm["dialogue"]["sign"]}


def width_errors(truth_rects: list, pred_rects: list, frame_w: int) -> dict:
    """Error of the predicted box width against the truth box width, as a share of the frame width, and the IoU of the two boxes."""
    err, ious = [], []
    for t, p in zip(truth_rects, pred_rects):
        if t is None or p is None:
            continue
        err.append(abs((p[2] - p[0]) - (t[2] - t[0])) / frame_w)
        ix = max(0, min(t[2], p[2]) - max(t[0], p[0]))
        iy = max(0, min(t[3], p[3]) - max(t[1], p[1]))
        inter = ix * iy
        union = (t[2] - t[0]) * (t[3] - t[1]) + (p[2] - p[0]) * (p[3] - p[1]) - inter
        ious.append(inter / union if union > 0 else 0.0)
    if not err:
        return {"n": 0}
    e = np.array(err)
    return {"n": len(err), "median": float(np.median(e)), "p95": float(np.quantile(e, 0.95)), "max": float(e.max()),
            "iou_median": float(np.median(ious)), "iou_p10": float(np.quantile(ious, 0.1))}


def seam_crossing(truth_rects: list, pred_rects: list, frame_w: int, seams=(0.4, 0.5, 0.6)) -> dict:
    """For each seam x (fraction of the frame width): does the box cross it (x0 < seam < x1)? Agreement between truth and prediction."""
    out = {}
    for s in seams:
        agree = tot = t_cross = 0
        for t, p in zip(truth_rects, pred_rects):
            if t is None or p is None:
                continue
            x = s * frame_w
            ct, cp = t[0] < x < t[2], p[0] < x < p[2]
            tot += 1
            agree += int(ct == cp)
            t_cross += int(ct)
        out[f"{s:g}"] = {"n": tot, "agree": agree, "accuracy": agree / tot if tot else None, "truth_crossing_share": t_cross / tot if tot else None}
    return out
