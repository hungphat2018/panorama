"""Black bars: per-frame cropdetect rectangles -> per-shot crop snapped to the file's crop vocabulary.

Ported from the research scripts (crop_frames.py, bars_eval.py run_s2, kf_vocab.py); measured there on 23,016 frames of real
anime with exact synthetic bars: 0.00% of frames cut picture, 0.96% left bars, every crop change on a cut (raw per-frame
cropdetect: 9.81% of frames cut picture).

Rectangles are cropdetect's own x1, x2, y1, y2 (first and last non-black column / row, inclusive) in source pixels.
The content model's crop rectangles are half-open [x0, y0, x1, y1) in source pixels (x1 = x2 + 1), the form the cue sheet uses.

Method:
  * A frame's rectangle is valid when it is wider and taller than 16 px and inside the frame (a black frame gives garbage).
  * A shot's evidence is the union of its valid rectangles; `agree` is the share of valid frames within 4 px of the union.
  * The vocabulary is the file's few real modes: shot unions (those with agree >= 0.2) are clustered, a cluster keeps its
    union as the mode, and modes carrying at least 3% of the evidence frames stay. (Research: grid clusters of 8 px; here a
    leader clustering with the same 8 px tolerance, so a mode is never split across a grid line.)
  * A shot takes the SMALLEST vocabulary mode that contains its union (6 px tolerance); a union no mode contains stays as it
    is. A shot with no valid frame holds the last confident crop (ADR 0010); before any, it has none.
"""
from __future__ import annotations

import re
from typing import Optional

import numpy as np

MIN_SIDE = 16
CLUSTER_TOL = 8
AGREE_TOL = 4
SNAP_TOL = 6
MIN_AGREE = 0.2
VOCAB_SHARE = 0.03


def parse_cropdetect_metadata(text: str) -> dict:
    """The text written by metadata=print: frame number, pts (ticks of the filter's time base) and the four edges."""
    frames, pts = [], []
    for m in re.finditer(r"(?m)^frame:(\d+)\s+pts:(-?\d+)", text):
        frames.append(int(m.group(1)))
        pts.append(int(m.group(2)))

    def grab(key):
        return [int(x) for x in re.findall(rf"lavfi\.cropdetect\.{key}=(-?\d+)", text)]

    x1, x2, y1, y2 = grab("x1"), grab("x2"), grab("y1"), grab("y2")
    n = min(len(frames), len(x1), len(x2), len(y1), len(y2))
    return {"frame": np.asarray(frames[:n]), "pts": np.asarray(pts[:n], dtype=np.int64),
            "rect": np.stack([x1[:n], x2[:n], y1[:n], y2[:n]], 1).astype(np.int16) if n else np.zeros((0, 4), np.int16)}


BAR_SHAPE_TOL = 12


def valid_mask(rect: np.ndarray, width: int, height: int) -> np.ndarray:
    """A frame's rectangle counts as evidence when it is inside the frame, wider and taller than 16 px AND shaped like bars: full
    width (letterbox) or full height (pillarbox) within 12 px. A black frame with a logo, or a dark scene lit in one corner,
    gives a small rectangle that is picture content, not bars; the research's check (size only) let such rectangles into the
    vocabulary (the promo's end card became a 362x83 'mode')."""
    r = rect.astype(np.int32)
    inside = ((r[:, 1] - r[:, 0] > MIN_SIDE) & (r[:, 3] - r[:, 2] > MIN_SIDE) & (r[:, 0] >= 0) & (r[:, 2] >= 0)
              & (r[:, 1] <= width - 1) & (r[:, 3] <= height - 1))
    full_w = (r[:, 0] <= BAR_SHAPE_TOL) & (r[:, 1] >= width - 1 - BAR_SHAPE_TOL)
    full_h = (r[:, 2] <= BAR_SHAPE_TOL) & (r[:, 3] >= height - 1 - BAR_SHAPE_TOL)
    return inside & (full_w | full_h)


def union(rs: np.ndarray) -> np.ndarray:
    return np.array([rs[:, 0].min(), rs[:, 1].max(), rs[:, 2].min(), rs[:, 3].max()], dtype=np.int32)


def contains(a, b, tol: int = SNAP_TOL) -> bool:
    return a[0] <= b[0] + tol and a[1] >= b[1] - tol and a[2] <= b[2] + tol and a[3] >= b[3] - tol


def area(r) -> int:
    return int((r[1] - r[0] + 1) * (r[3] - r[2] + 1))


def to_half_open(r) -> list:
    """[x_first, x_last, y_first, y_last] inclusive -> [x0, y0, x1, y1) half-open."""
    return [int(r[0]), int(r[2]), int(r[1]) + 1, int(r[3]) + 1]


def shot_evidence(rect: np.ndarray, ok: np.ndarray, a: int, b: int) -> dict:
    """Union and agreement of the valid rectangles of frames a..b-1."""
    idx = np.nonzero(ok[a:b])[0] + a
    n = b - a
    if len(idx) == 0:
        return {"union": None, "agree": 0.0, "n_valid": 0, "n_frames": n}
    u = union(rect[idx].astype(np.int32))
    agree = float(np.mean(np.abs(rect[idx].astype(np.int32) - u).max(axis=1) <= AGREE_TOL))
    return {"union": u, "agree": agree, "n_valid": int(len(idx)), "n_frames": n}


def build_vocabulary(items: list, vocab_share: float = VOCAB_SHARE, min_agree: float = MIN_AGREE) -> list:
    """items: dicts with 'union' (inclusive rect or None), 'agree', and 'weight' (frames or keyframes it stands for).
    Returns modes sorted by share, each {'rect': [x1,x2,y1,y2], 'share': float, 'weight': int}."""
    modes = []   # each: {"rect": ndarray, "weight": int, "seed": ndarray}
    for it in items:
        u = it["union"]
        if u is None or it["agree"] < min_agree:
            continue
        for m in modes:
            if np.abs(m["seed"] - u).max() <= CLUSTER_TOL:
                m["weight"] += it["weight"]
                m["rect"] = np.array([min(m["rect"][0], u[0]), max(m["rect"][1], u[1]), min(m["rect"][2], u[2]),
                                      max(m["rect"][3], u[3])], dtype=np.int32)
                break
        else:
            modes.append({"rect": np.asarray(u, dtype=np.int32).copy(), "weight": int(it["weight"]), "seed": np.asarray(u, dtype=np.int32).copy()})
    total = sum(m["weight"] for m in modes)
    if total == 0:
        return []
    out = [{"rect": [int(v) for v in m["rect"]], "share": round(m["weight"] / total, 4), "weight": int(m["weight"])}
           for m in modes if m["weight"] / total >= vocab_share]
    out.sort(key=lambda m: -m["share"])
    return out


def snap(u, vocab: list):
    """Index of the smallest mode containing the union u (within SNAP_TOL), or None."""
    best, best_area = None, None
    for i, m in enumerate(vocab):
        if contains(m["rect"], u):
            a = area(m["rect"])
            if best is None or a < best_area:
                best, best_area = i, a
    return best


def assign_shot_crops(evidence: list, vocab: list, width: int, height: int) -> list:
    """One result per shot: {'rect': half-open or None, 'vocab_id', 'conf', 'evidence'}.
    'evidence': 'normal' (at least half the frames have a valid rectangle), 'dark' (some, fewer), 'no_valid_frames' (none:
    the last confident crop is held)."""
    out = []
    last: Optional[dict] = None
    for ev in evidence:
        if ev["union"] is None or ev["n_valid"] == 0:
            if last is not None:
                out.append({"rect": last["rect"], "vocab_id": last["vocab_id"], "conf": 0.0, "evidence": "no_valid_frames",
                            "held_from_previous": True})
            else:
                out.append({"rect": None, "vocab_id": None, "conf": 0.0, "evidence": "no_valid_frames", "held_from_previous": False})
            continue
        u = ev["union"]
        vid = snap(u, vocab)
        rect_incl = vocab[vid]["rect"] if vid is not None else [int(v) for v in u]
        share_valid = ev["n_valid"] / max(1, ev["n_frames"])
        kind = "normal" if share_valid >= 0.5 else "dark"
        conf = float(ev["agree"]) * min(1.0, ev["n_valid"] / 8.0)
        res = {"rect": to_half_open(rect_incl), "vocab_id": vid, "conf": round(conf, 3), "evidence": kind, "held_from_previous": False}
        out.append(res)
        if ev["agree"] >= MIN_AGREE and kind == "normal":
            last = res
    return out
