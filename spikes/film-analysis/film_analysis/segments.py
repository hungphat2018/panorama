"""Segments: the units of the full scan, cut on keyframes so a scan can start at any seek.

The scan covers frames [0, n). Parts already done (the manifest's `done` intervals) are left out. What remains is cut into
segments that begin and end on keyframes (a segment is decoded alone: ffmpeg seeks to its first keyframe). Scan order follows
the playhead: the segment that holds it first, then forward to the end of the film, then the part before the playhead, nearest
the start first. The first segments from the playhead are short and grow (10 s, 20 s, 40 s, then the target length, 45 s by
default) so the first usable result arrives early; later segments are longer to keep the per-segment costs (two ffmpeg starts,
about 150 frames of context for the shot model) small.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

SHOT_CONTEXT_BEFORE = 77      # frames before a segment the small stream decodes: 75 for the shot model windows + 2 for the scdet memory
SHOT_CONTEXT_AFTER = 75


def merge_intervals(iv: list) -> list:
    out = []
    for a, b in sorted(iv):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def subtract(whole: tuple, done: list) -> list:
    """whole minus the done intervals, as [a, b] pairs."""
    a, b = whole
    out, pos = [], a
    for da, db in merge_intervals([list(x) for x in done]):
        if db <= pos:
            continue
        if da >= b:
            break
        if da > pos:
            out.append([pos, min(da, b)])
        pos = max(pos, db)
    if pos < b:
        out.append([pos, b])
    return out


def _split_interval(a: int, b: int, key_idx: np.ndarray, secs: np.ndarray, lengths: list, target_s: float) -> list:
    """Cut [a, b) at keyframes. lengths: the first segments' durations in seconds; then target_s."""
    segs, pos, i = [], a, 0
    while pos < b:
        want = lengths[i] if i < len(lengths) else target_s
        i += 1
        limit = secs[pos] + want
        # first keyframe whose time is at least `limit`, strictly after pos, not beyond b
        j = int(np.searchsorted(secs[key_idx], limit, side="left"))
        cands = key_idx[j:]
        cands = cands[(cands > pos) & (cands < b)]
        end = int(cands[0]) if len(cands) else b
        segs.append((pos, end))
        pos = end
    return segs


def plan(secs: np.ndarray, key: np.ndarray, done: list, playhead_frame: int, target_s: float = 45.0,
         ramp_s: tuple = (10.0, 20.0, 40.0)) -> list:
    """Segments still to scan, in scan order: list of (start_frame, end_frame)."""
    n = len(secs)
    key_idx = np.nonzero(key)[0]
    key_idx = key_idx[key_idx > 0]                                  # frame 0 always starts a segment
    remaining = subtract((0, n), done)
    # the keyframe at or before the playhead starts the first segment
    kidx = np.concatenate([[0], key_idx])
    play_start = int(kidx[np.searchsorted(kidx, playhead_frame, side="right") - 1])
    forward, backward = [], []
    for a, b in remaining:
        if b <= play_start:
            backward.append((a, b))
        elif a >= play_start:
            forward.append((a, b))
        else:
            backward.append((a, play_start))
            forward.append((play_start, b))
    segs_forward, first = [], True
    for a, b in sorted(forward):
        lengths = list(ramp_s) if first and a == play_start else []
        first = False
        segs_forward += _split_interval(a, b, key_idx, secs, lengths, target_s)
    segs_backward = []
    for a, b in sorted(backward):
        segs_backward += _split_interval(a, b, key_idx, secs, [], target_s)
    return segs_forward + segs_backward


def decode_range(key: np.ndarray, a: int, b: int, n: int) -> tuple:
    """Frames the small stream decodes for a segment [a, b): from the last keyframe at or before a - 77 (or frame 0) to b + 75."""
    want = max(0, a - SHOT_CONTEXT_BEFORE)
    key_idx = np.nonzero(key[: want + 1])[0]
    d0 = int(key_idx[-1]) if len(key_idx) else 0
    d1 = min(n, b + SHOT_CONTEXT_AFTER)
    return d0, d1
