"""Faces and attention metrics (research table, row "Faces"): per-frame recall at IoU 0.5, keep-clear coverage, per-shot recall,
false keep-clear area, and the planner-level bezel-hit rate.

Boxes are (x, y, w, h) in source storage pixels. Truth classes: face, head, person, text. A planner keeps faces and heads off the
bezel strip (ADR 0009); persons and text are kept clear where possible but do not count as "known faces" for the bezel-hit rate.

Planner-level stand-in layout (state it wherever the numbers are quoted): the picture spans two screens and a bezel strip of
`bezel_w` of the picture width (default 2%, the middle of ADR 0009's 1-3%) sits at picture-relative x = `centre`. The naive layout
puts the strip at the picture centre (0.5), "the picture centred on the gap". The stand-in planner may slide the picture so the strip
lands anywhere in 0.5 +- `max_shift` (default 0.15: Setup A's two screens are about 41% and 57% of the combined width) and must keep
the picture's centre off the strip.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


def iou(a, b) -> float:
    ax0, ay0, aw, ah = a
    bx0, by0, bw, bh = b
    ix = max(0.0, min(ax0 + aw, bx0 + bw) - max(ax0, bx0))
    iy = max(0.0, min(ay0 + ah, by0 + bh) - max(ay0, by0))
    inter = ix * iy
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def _raster(boxes, w: int, h: int, scale: float = 0.125) -> np.ndarray:
    gw, gh = max(int(round(w * scale)), 1), max(int(round(h * scale)), 1)
    m = np.zeros((gh, gw), bool)
    for x, y, bw, bh in boxes:
        x0, y0 = int(np.floor(x * scale)), int(np.floor(y * scale))
        x1, y1 = int(np.ceil((x + bw) * scale)), int(np.ceil((y + bh) * scale))
        m[max(y0, 0):max(min(y1, gh), 0), max(x0, 0):max(min(x1, gw), 0)] = True
    return m


def covered_fraction(truth_box, pred_boxes, w: int, h: int) -> float:
    t = _raster([truth_box], w, h)
    if t.sum() == 0:
        return 1.0
    p = _raster(pred_boxes, w, h)
    return float((t & p).sum() / t.sum())


@dataclass
class FrameBoxes:
    truth: list[dict]           # {"cls", "x", "y", "w", "h"}
    pred: list[dict]            # {"cls", "x", "y", "w", "h", "score"}


def subject_metrics(frames: dict[int, FrameBoxes], w: int, h: int, *, cover_frac: float = 0.7, iou_thr: float = 0.5,
                    keep_classes=("face", "head", "person", "text")) -> dict:
    """Counts over labelled frames. strict = IoU >= iou_thr with a predicted box of the same class; covered = at least cover_frac of the
    truth box lies inside the union of ALL predicted keep-clear boxes."""
    cls_all = ("face", "head", "person", "text")
    tot = {c: 0 for c in cls_all}
    strict = {c: 0 for c in cls_all}
    covered = {c: 0 for c in cls_all}
    frames_with_truth = 0
    false_boxes = 0
    pred_boxes = 0
    false_area = []
    for fr in frames.values():
        tb = [(t["cls"], (t["x"], t["y"], t["w"], t["h"])) for t in fr.truth]
        pb = [(p["cls"], (p["x"], p["y"], p["w"], p["h"])) for p in fr.pred if p["cls"] in keep_classes]
        if tb:
            frames_with_truth += 1
        for c, box in tb:
            tot[c] += 1
            if any(pc == c and iou(box, pbx) >= iou_thr for pc, pbx in pb):
                strict[c] += 1
            if covered_fraction(box, [pbx for _, pbx in pb], w, h) >= cover_frac:
                covered[c] += 1
        pred_boxes += len(pb)
        for _, pbx in pb:
            if not any(iou(pbx, box) > 0.1 or covered_fraction(pbx, [box], w, h) > 0.5 for _, box in tb):
                false_boxes += 1
        pm = _raster([b for _, b in pb], w, h)
        tm = _raster([b for _, b in tb], w, h)
        false_area.append(float((pm & ~tm).sum() / pm.size))
    return {"frames": len(frames), "frames_with_truth": frames_with_truth, "truth": tot, "strict": strict, "covered": covered,
            "recall_strict": {c: (strict[c] / tot[c] if tot[c] else None) for c in cls_all},
            "recall_covered": {c: (covered[c] / tot[c] if tot[c] else None) for c in cls_all},
            "pred_boxes": pred_boxes, "false_boxes": false_boxes, "false_keepclear_area_mean": float(np.mean(false_area)) if false_area else 0.0}


def shot_recall(frames: dict[int, FrameBoxes], shot_of_frame, w: int, h: int, *, cls=("face",), cover_frac: float = 0.7) -> dict:
    """Per shot: has a truth box of the given classes in any labelled frame; found when some such box is matched (IoU >= 0.5 with a
    same-class prediction) or covered in the same frame."""
    shots: dict[int, dict] = {}
    for f, fr in frames.items():
        s = shot_of_frame(f)
        d = shots.setdefault(s, {"has": False, "found": False})
        tb = [t for t in fr.truth if t["cls"] in cls]
        if not tb:
            continue
        d["has"] = True
        pb = [(p["x"], p["y"], p["w"], p["h"]) for p in fr.pred]
        for t in tb:
            box = (t["x"], t["y"], t["w"], t["h"])
            same = [(p["x"], p["y"], p["w"], p["h"]) for p in fr.pred if p["cls"] == t["cls"]]
            if any(iou(box, q) >= 0.5 for q in same) or covered_fraction(box, pb, w, h) >= cover_frac:
                d["found"] = True
    has = [d for d in shots.values() if d["has"]]
    return {"shots": len(shots), "shots_with_truth": len(has), "shots_found": sum(1 for d in has if d["found"]),
            "recall": (sum(1 for d in has if d["found"]) / len(has)) if has else None}


# ------------------------------------------------------------------------------------------------ planner level
@dataclass(frozen=True)
class Layout:
    bezel_w: float = 0.02        # fraction of the picture width hidden behind the bezel
    centre: float = 0.5          # naive: the strip at the picture centre
    max_shift: float = 0.15      # the planner may slide the strip within centre +- max_shift
    centre_margin: float = 0.005  # the picture's centre must stay this far outside the strip
    keep_margin: float = 0.004   # clearance asked around a keep-clear box


def strip(c: float, lay: Layout) -> tuple[float, float]:
    return c - lay.bezel_w / 2, c + lay.bezel_w / 2


def _overlap_w(strip_x: tuple[float, float], boxes_rel: list[tuple[float, float]], margin: float) -> float:
    return sum(max(0.0, min(strip_x[1], b1 + margin) - max(strip_x[0], b0 - margin)) for b0, b1 in boxes_rel)


def plan_bezel(keep_rel: list[tuple[float, float]], lay: Layout) -> float:
    """Strip centre chosen for a shot: every candidate within +-max_shift (steps of 0.0025) whose strip keeps the picture centre outside it;
    least overlap with the keep-clear x-ranges first, then the smallest move from the naive centre."""
    best, best_key = lay.centre, None
    steps = int(round(lay.max_shift / 0.0025))
    for k in range(-steps, steps + 1):
        c = lay.centre + k * 0.0025
        s0, s1 = strip(c, lay)
        if s0 - lay.centre_margin <= 0.5 <= s1 + lay.centre_margin:
            continue
        key = (round(_overlap_w((s0, s1), keep_rel, lay.keep_margin), 6), abs(c - lay.centre), c)
        if best_key is None or key < best_key:
            best, best_key = c, key
    return best


def to_rel(box, pic_x0: float, pic_w: float) -> tuple[float, float]:
    x, _, w, _ = box
    return (x - pic_x0) / pic_w, (x + w - pic_x0) / pic_w


def bezel_hit_rate(shots: list[dict], lay: Layout, pic_x0: float, pic_w: float, *, mode: str, known=("face", "head")) -> dict:
    """shots: [{"truth": [boxes], "pred": [boxes]}] each box a dict with cls,x,y,w,h (union over the shot's labelled frames).

    mode: "naive" (strip at the centre, no planner), "centre-rule" (shift just enough to keep the centre off the strip, blind to content),
    "detected" (planner uses predicted face/head/person/text boxes), "oracle" (planner uses the truth boxes). A shot is a hit when a truth
    face or head box overlaps the strip. Only shots with at least one truth face or head count."""
    hits = total = 0
    shifts = []
    for sh in shots:
        tf = [to_rel((b["x"], b["y"], b["w"], b["h"]), pic_x0, pic_w) for b in sh["truth"] if b["cls"] in known]
        if not tf:
            continue
        total += 1
        if mode == "naive":
            c = lay.centre
        elif mode == "centre-rule":
            c = plan_bezel([], lay)
        elif mode == "detected":
            c = plan_bezel([to_rel((b["x"], b["y"], b["w"], b["h"]), pic_x0, pic_w) for b in sh["pred"] if b["cls"] in ("face", "head", "person", "text")], lay)
        elif mode == "oracle":
            c = plan_bezel([to_rel((b["x"], b["y"], b["w"], b["h"]), pic_x0, pic_w) for b in sh["truth"] if b["cls"] in ("face", "head", "person", "text")], lay)
        else:
            raise ValueError(mode)
        s0, s1 = strip(c, lay)
        if any(min(s1, b1) - max(s0, b0) > 0 for b0, b1 in tf):
            hits += 1
        shifts.append(abs(c - lay.centre))
    return {"shots": total, "hits": hits, "hit_rate": hits / total if total else None, "mean_shift": float(np.mean(shifts)) if shifts else 0.0}
