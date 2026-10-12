"""Turn raw shot signals into Boundary lists (the thresholds are the 'pick'; the signals are cached unthresholded)."""
from __future__ import annotations

import numpy as np

from fa_eval.types import Boundary


def find_runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Inclusive (first, last) index pairs of consecutive True values."""
    m = np.asarray(mask, dtype=bool)
    if m.size == 0:
        return []
    d = np.diff(np.concatenate([[0], m.view(np.int8), [0]]))
    starts = np.nonzero(d == 1)[0]
    ends = np.nonzero(d == -1)[0] - 1
    return list(zip(starts.tolist(), ends.tolist()))


def tn_single(single: np.ndarray, thr: float = 0.5, long_run: int = 3) -> list[Boundary]:
    """Single-frame head > thr. A run of up to long_run-1 frames is one hard cut at (last flagged + 1); longer runs are transitions."""
    out = []
    for i, j in find_runs(single > thr):
        score = float(single[i:j + 1].max())
        if j - i + 1 >= long_run:
            out.append(Boundary(i, "dissolve", (i, j + 1), score, "tn_single"))
        else:
            out.append(Boundary(j + 1, "hard", None, score, "tn_single"))
    return out


def tn_any(single: np.ndarray, many: np.ndarray, thr_single: float = 0.5, thr_many: float = 0.4, long_run: int = 3) -> list[Boundary]:
    """Either head high: the research's 'max(heads) > 0.4' policy. Short runs are hard cuts (placed with the single head), long runs transitions."""
    flag = (single > thr_single) | (many > thr_many)
    out = []
    for i, j in find_runs(flag):
        score = float(max(single[i:j + 1].max(), many[i:j + 1].max()))
        if j - i + 1 >= long_run:
            out.append(Boundary(i, "dissolve", (i, j + 1), score, "tn_any"))
        else:
            k = int(np.argmax(single[i:j + 1])) + i
            at = k + 1 if single[k] > thr_single else j + 1
            out.append(Boundary(at, "hard", None, score, "tn_any"))
    return out


def scdet(score: np.ndarray, thr: float = 10.0) -> list[Boundary]:
    """scdet score >= thr; the first frame of a run is the first frame of the new shot."""
    return [Boundary(i, "hard", None, float(score[i:j + 1].max()), f"scdet{thr:g}") for i, j in find_runs(score >= thr)]


def from_frames(frames, source: str) -> list[Boundary]:
    return [Boundary(int(f), "hard", None, 1.0, source) for f in frames]


def merge_agree(*lists: list[Boundary], tol: int = 1, min_votes: int = 2, source: str = "vote") -> list[Boundary]:
    """Keep a boundary when at least min_votes of the lists have one within tol frames (the first list's frame is used)."""
    out = []
    base = lists[0]
    others = [np.array([b.frame for b in l]) for l in lists[1:]]
    for b in base:
        votes = 1 + sum(1 for arr in others if arr.size and np.abs(arr - b.frame).min() <= tol)
        if votes >= min_votes:
            out.append(Boundary(b.frame, b.kind, b.span, b.score, source))
    return out


def consensus_cuts(sig: dict, tol: int = 1, psd_tol: int = 2) -> list[Boundary]:
    """Hard cuts that TransNetV2 (single head > 0.5, short run) and at least one independent signal agree on:
    scdet >= 10, a PySceneDetect scene start (Content or Adaptive), or a keyframe, each within tol frames (psd_tol for PySceneDetect).

    This is the "surely a cut" set used to place bar changes in constructed clips and to auto-accept label candidates; it is not the
    pick being measured (the detectors are measured against labels that also hold what they miss).
    """
    n = int(sig["n_frames"][0])
    tn = [b for b in tn_single(sig["tn_single"], 0.5) if b.kind == "hard"]
    others = []
    if "scdet" in sig:
        others.append(("scdet", np.array([b.frame for b in scdet(sig["scdet"], 10.0)]), tol))
    for k in ("psd_content", "psd_adaptive"):
        if k in sig:
            others.append((k, np.asarray(sig[k]), psd_tol))
    if "keyframes" in sig:
        others.append(("kf", np.asarray(sig["keyframes"]), tol))
    out = []
    for b in tn:
        votes = [name for name, arr, t in others if arr.size and np.abs(arr - b.frame).min() <= t]
        if votes and 0 < b.frame < n:
            out.append(Boundary(b.frame, "hard", None, b.score, "consensus:" + "+".join(votes)))
    return out
