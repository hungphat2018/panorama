"""Shot boundaries from the stored TransNetV2 scores (and, where the scan has not been, from keyframes).

TransNetV2 gives, for every frame i, p_single[i] = the probability that a cut lies between frame i and frame i+1 (the new shot
starts at i+1) and p_many[i] = the probability that frame i belongs to a transition. The research's event rule: runs of
p_single > 0.5; on the real anime episode that is 350 events (cuts), 27 of 27 sampled looked like real visual changes.

Boundaries here:
  * hard      a run of 1-2 frames above 0.5 in the single head; the new shot starts after the run's last frame
  * gradual   a run of 3 or more frames above 0.5 in the single head, or a run of 4 or more frames above 0.5 in the many-hot
              head that no single-head run touches (dissolves and fades: the many-hot head marks the whole span); the span is
              kept, the new shot starts after it
  * keyframe  provisional: an unscanned stretch is cut at its keyframes (74% of the episode's TransNetV2 cuts coincide with a
              keyframe, 84% of its keyframes are cuts) until the scan has been there
`layout_safe` is true for hard and gradual boundaries (a place where the picture may change layout unseen); the planner should
still ask for confidence, because an extra cut costs nothing but a layout change on a false cut would be visible.
"""
from __future__ import annotations

import numpy as np

CUT_THRESHOLD = 0.5
MANY_THRESHOLD = 0.5
HARD_MAX_RUN = 2
MANY_MIN_RUN = 4


def _runs(mask: np.ndarray) -> list:
    """(first, last) index pairs of consecutive True values."""
    if not mask.any():
        return []
    d = np.diff(np.concatenate([[0], mask.astype(np.int8), [0]]))
    starts = np.nonzero(d == 1)[0]
    ends = np.nonzero(d == -1)[0] - 1
    return list(zip(starts.tolist(), ends.tolist()))


def tn_boundaries(single: np.ndarray, many: np.ndarray, scanned: np.ndarray, key: np.ndarray) -> list:
    """Boundaries inside the scanned frames. Returns dicts sorted by `frame` (first frame of the new shot)."""
    n = len(single)
    s = np.where(scanned > 0, single.astype(np.float32), 0.0) > CUT_THRESHOLD
    m = np.where(scanned > 0, many.astype(np.float32), 0.0) > MANY_THRESHOLD
    out = []
    covered = np.zeros(n, bool)
    for first, last in _runs(s):
        frame = last + 1
        if frame >= n:
            continue
        kind = "hard" if (last - first + 1) <= HARD_MAX_RUN else "gradual"
        out.append({"frame": frame, "kind": kind, "source": "transnetv2", "p_single": float(single[first:last + 1].max()),
                    "p_many": float(many[first:last + 1].max()), "span": None if kind == "hard" else [first, frame],
                    "keyframe_aligned": bool(key[max(0, frame - 1):frame + 2].any())})
        covered[max(0, first - 1):min(n, last + 2)] = True
    for first, last in _runs(m):
        if last - first + 1 < MANY_MIN_RUN or covered[first:last + 1].any():
            continue
        frame = last + 1
        if frame >= n:
            continue
        out.append({"frame": frame, "kind": "gradual", "source": "transnetv2-many-hot", "p_single": float(single[first:last + 1].max()),
                    "p_many": float(many[first:last + 1].max()), "span": [first, frame],
                    "keyframe_aligned": bool(key[max(0, frame - 1):frame + 2].any())})
    out.sort(key=lambda b: b["frame"])
    return out


def keyframe_boundaries(key: np.ndarray, scanned: np.ndarray, taken: set) -> list:
    """Provisional boundaries at keyframes of frames the scan has not covered (and not already a boundary)."""
    out = []
    for f in np.nonzero(key)[0].tolist():
        if f == 0 or scanned[f] > 0 or f in taken:
            continue
        out.append({"frame": f, "kind": "keyframe", "source": "keyframe", "p_single": None, "p_many": None, "span": None,
                    "keyframe_aligned": True})
    return out
