"""What the three derived tables share: the frame times, which frames the scan has covered, and the shot boundaries.

Covered stretches (frames with scanned == 1) get their boundaries from the shot model; stretches not yet scanned are cut at
their keyframes and flagged provisional. The spine is cheap (a pass over two score columns), so every table builds on a fresh
one and any table can be made again alone.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import shots_transnet
from .frame_table import FrameTable


@dataclass
class Spine:
    n: int
    W: int
    H: int
    t: np.ndarray                  # frame times on the rebased timeline (the rule of schema/common.schema.json)
    key: np.ndarray                # bool, keyframe
    sc: np.ndarray                 # bool, frame scanned
    n_scanned: int
    state: str                     # provisional | partial | complete
    tn: list                       # shot-model boundaries
    kfb: list                      # provisional keyframe boundaries
    bounds: dict                   # first frame of a shot -> its boundary
    firsts: list                   # first frame of every shot
    lasts: list
    starts_s: list                 # cue-sheet start of every shot (halfway between frames)
    ends_s: list
    fps: float


def make_spine(store, table: FrameTable, facts: dict) -> Spine:
    n = table.n
    scanned = store.col("scanned")
    key = store.col("key").astype(bool)
    n_scanned = int((scanned > 0).sum())
    state = "provisional" if n_scanned == 0 else ("complete" if n_scanned == n else "partial")
    tn = shots_transnet.tn_boundaries(store.col("tn_single"), store.col("tn_many"), scanned, key)
    kfb = shots_transnet.keyframe_boundaries(key, scanned, {b["frame"] for b in tn})
    bounds = {}
    for b in tn + kfb:
        bounds.setdefault(b["frame"], b)
    firsts = [0] + sorted(bounds)
    lasts = [f - 1 for f in firsts[1:]] + [n - 1]
    t = table.seconds()
    starts_s = table.cue_starts(firsts)
    ends_s = starts_s[1:] + [float(t[-1]) + table.last_frame_duration_s()]
    return Spine(n, facts["video"]["width"], facts["video"]["height"], t, key, scanned > 0, n_scanned, state, tn, kfb, bounds,
                 firsts, lasts, starts_s, ends_s, float(facts["timeline"]["frame_rate"].get("fps_avg") or 24.0))
