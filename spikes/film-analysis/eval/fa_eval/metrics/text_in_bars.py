"""Text-in-bars metrics (research table, row "Text in bars"): recall of text frames, false alarms, text pixels cropped, per-file flags.

A truth text event is a frame range and a box (x0, y0, x1, y1), half-open. Only the part of a box that lies in a bar (outside the
picture rectangle T of that frame) matters: text drawn on the picture is not a bar problem and a guard must not fire for it alone.
"""
from __future__ import annotations

import numpy as np


def bar_part(box, T) -> tuple[int, int, int, int] | None:
    """Parts of `box` that lie in the bars of rectangle T, as the union bounding box of the up-to-four slabs (usually one). None when none."""
    x0, y0, x1, y1 = box
    tx0, ty0, tx1, ty1 = T
    slabs = []
    if y0 < ty0:
        slabs.append((x0, y0, x1, min(y1, ty0)))
    if y1 > ty1:
        slabs.append((x0, max(y0, ty1), x1, y1))
    if x0 < tx0:
        slabs.append((x0, y0, min(x1, tx0), y1))
    if x1 > tx1:
        slabs.append((max(x0, tx1), y0, x1, y1))
    slabs = [s for s in slabs if s[2] > s[0] and s[3] > s[1]]
    if not slabs:
        return None
    return (min(s[0] for s in slabs), min(s[1] for s in slabs), max(s[2] for s in slabs), max(s[3] for s in slabs))


def per_frame_bar_text(events: list[dict], T: np.ndarray) -> list[list[tuple[int, int, int, int]]]:
    """For every frame the list of bar-part boxes of the text visible in it."""
    n = len(T)
    out: list[list] = [[] for _ in range(n)]
    for e in events:
        a, b = e["frames"]
        for i in range(max(a, 0), min(b, n)):
            p = bar_part(e["box"], tuple(int(v) for v in T[i]))
            if p is not None:
                out[i].append(p)
    return out


def text_metrics(events: list[dict], T: np.ndarray, D: np.ndarray, flagged: np.ndarray | None, *, tol_px: int = 2) -> dict:
    """D: final crop rectangles; flagged: per-frame guard output (True = the guard sees something bright in a bar), or None.

    cropped_px: for each frame with bar text, how many rows/columns of the text's bar part are outside D (worst side); frames_cropped counts
    frames where that is >= tol_px. A frame counts as text_ok when nothing of the bar text is cut.
    """
    n = len(T)
    parts = per_frame_bar_text(events, T)
    has = np.array([len(p) > 0 for p in parts], dtype=bool)
    cut = np.zeros(n, np.int32)
    for i, ps in enumerate(parts):
        for (x0, y0, x1, y1) in ps:
            c = max(D[i, 0] - x0, D[i, 1] - y0, x1 - D[i, 2], y1 - D[i, 3], 0)
            cut[i] = max(cut[i], c)
    res = {"n_frames": n, "text_frames": int(has.sum()), "text_frames_share": float(has.mean()) if n else 0.0,
           "frames_cropped": int(((cut >= tol_px) & has).sum()),
           "text_kept_share": float(1.0 - ((cut >= tol_px) & has).sum() / max(has.sum(), 1)),
           "cut_px_median": float(np.median(cut[has])) if has.any() else 0.0, "cut_px_p95": float(np.quantile(cut[has], 0.95)) if has.any() else 0.0,
           "cut_px_max": int(cut.max()) if n else 0}
    if flagged is not None:
        fl = np.asarray(flagged, dtype=bool)
        tp = int((fl & has).sum())
        fp = int((fl & ~has).sum())
        res.update({"guard_recall": tp / max(int(has.sum()), 1), "guard_tp": tp, "guard_fn": int((~fl & has).sum()),
                    "guard_false_alarms": fp, "guard_false_alarm_share": fp / max(int((~has).sum()), 1), "free_frames": int((~has).sum())})
    return res


def file_flags(events: list[dict], T: np.ndarray, min_share: float = 0.02) -> dict:
    """Which bars hold text in at least min_share of the frames: {"top", "bottom", "left", "right"} booleans (truth for the per-file flag)."""
    n = len(T)
    counts = {"top": 0, "bottom": 0, "left": 0, "right": 0}
    for e in events:
        a, b = e["frames"]
        x0, y0, x1, y1 = e["box"]
        for i in range(max(a, 0), min(b, n)):
            tx0, ty0, tx1, ty1 = (int(v) for v in T[i])
            if y0 < ty0:
                counts["top"] += 1
            if y1 > ty1:
                counts["bottom"] += 1
            if x0 < tx0:
                counts["left"] += 1
            if x1 > tx1:
                counts["right"] += 1
    return {k: v / max(n, 1) >= min_share for k, v in counts.items()}
