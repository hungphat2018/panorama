"""Pre-scan quality (spike S10): after the keyframe scan and the first minutes of full analysis, how often is the cue sheet already right?

For one file: the crop rectangle the pre-scan would apply to each frame of the first `window_s` seconds (D), the truth (T) and the shots.
Per shot: right = the crop is within `tol` px of the truth on every side for every frame of the shot (nothing cut, no bars left);
safe = never cuts picture. Also the share of frames right and the first frame from which everything stays right.
"""
from __future__ import annotations

import numpy as np

from fa_eval.metrics.bars import bar_px, cut_px


def prescan_metrics(T: np.ndarray, D: np.ndarray, shot_starts, fps: float, *, window_s: float = 180.0, tol: int = 2, ambiguous: np.ndarray | None = None) -> dict:
    n = min(len(T), int(window_s * fps))
    T, D = T[:n], D[:n]
    cp, bp = cut_px(T, D), bar_px(T, D)
    right = (cp < tol) & (bp < tol)
    safe = cp < tol
    amb = np.zeros(n, bool) if ambiguous is None else ambiguous[:n]
    starts = [int(s) for s in shot_starts if s < n]
    if not starts or starts[0] != 0:
        starts = [0] + starts
    ends = starts[1:] + [n]
    shots_right = shots_safe = 0
    for a, b in zip(starts, ends):
        seg = slice(a, b)
        ok = right[seg] | amb[seg]
        sf = safe[seg] | amb[seg]
        shots_right += int(ok.all())
        shots_safe += int(sf.all())
    stays = np.flip(np.cumprod(np.flip(right | amb)).astype(bool))      # True from i on if every later frame is right
    first = int(np.argmax(stays)) if stays.any() else None
    return {"frames": n, "shots": len(starts), "shots_right": shots_right, "shots_right_share": shots_right / len(starts),
            "shots_safe": shots_safe, "shots_safe_share": shots_safe / len(starts), "frames_right_share": float(right.mean()),
            "frames_cutting": int((cp >= tol).sum()), "first_right_frame_that_stays": first,
            "first_right_seconds": (first / fps) if first is not None else None}
