"""The timeline rule and the frame table.

THE RULE (also written into schema/frame-table.schema.json and file-facts.json):
  * The frame table has one row per frame the performer presents, in presentation order: the frames its decoder path
    returns (perf1-decode.c, handle_frame), identified by `pts`, the frame's presentation timestamp in ticks of the
    stream time base tb = tb_num / tb_den. A frame whose pts does not exceed the previous accepted one is refused by the
    performer, so the table is strictly increasing.
  * Time zero is the first frame: ts0 = pts[0].
  * Seconds on the rebased timeline, exactly as the performer computes them in IEEE double:
        t[i] = pts[i] * (tb_num / tb_den)  -  pts[0] * (tb_num / tb_den)
    (tb_num / tb_den is one double division, like av_q2d). t[0] is exactly 0.
  * A cue-sheet shot that begins with frame a > 0 starts at (t[a-1] + t[a]) / 2, halfway between the frames; the first
    shot starts at 0.
  * A frame lasts until the next one; the last frame lasts as long as its packet says, else as long as the median step.

The table is built from the container's packet list (fast: no decoding): packets flagged to be discarded (MP4 edit
lists) and packets without a timestamp are dropped, the rest are sorted by pts, and a repeated pts keeps the first
packet. `verify-frame-table.py` compares the result with the real decoder path on the edge-case files; during the scan
every segment's decoded frame times are compared with the table again.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from typing import Optional

import numpy as np

# frame rates the table is matched against when it names a nominal rate
_NOMINAL_RATES = [Fraction(n, d) for n, d in (
    (24000, 1001), (24, 1), (25, 1), (30000, 1001), (30, 1), (48000, 1001), (48, 1), (50, 1), (60000, 1001), (60, 1),
    (120000, 1001), (120, 1), (12000, 1001), (12, 1), (15, 1), (10, 1), (20, 1), (100, 1), (72, 1), (96, 1))]


@dataclass
class FrameTable:
    tb_num: int
    tb_den: int
    pts: np.ndarray                      # int64 ticks, presentation order, strictly increasing
    key: np.ndarray                      # bool, the packet carrying this frame is a keyframe
    last_duration_ticks: Optional[int]   # packet duration of the last frame, if the container says
    source: str = "packets"
    stats: dict = field(default_factory=dict)

    @property
    def n(self) -> int:
        return int(self.pts.shape[0])

    @property
    def tb(self) -> float:
        return self.tb_num / self.tb_den       # one double division, like av_q2d

    def seconds_abs(self) -> np.ndarray:
        return self.pts.astype(np.float64) * self.tb

    def seconds(self) -> np.ndarray:
        """Rebased time of every frame (the rule above); bit-identical to the performer's doubles."""
        abs_s = self.seconds_abs()
        return abs_s - abs_s[0]

    def origin_seconds_abs(self) -> float:
        return float(self.pts[0]) * self.tb

    def last_frame_duration_s(self) -> float:
        if self.last_duration_ticks and self.last_duration_ticks > 0:
            return self.last_duration_ticks * self.tb
        if self.n >= 2:
            return float(np.median(np.diff(self.pts))) * self.tb
        return 0.0

    def duration_s(self) -> float:
        """From the first frame's time to the end of the last frame."""
        return float(self.seconds()[-1]) + self.last_frame_duration_s()

    def cue_starts(self, first_frames: list) -> list:
        """Cue-sheet `start` for shots whose first frame indices are given (0 must come first)."""
        t = self.seconds()
        return [0.0 if a <= 0 else float((t[a - 1] + t[a]) / 2.0) for a in first_frames]

    def frame_at(self, seconds: float) -> int:
        """Index of the frame on screen at a rebased time: the last frame with t <= seconds (0 before the first)."""
        t = self.seconds()
        return max(0, int(np.searchsorted(t, seconds, side="right")) - 1)


def table_from_packets(packets: list, tb_num: int, tb_den: int, fallback_step_ticks: Optional[int] = None) -> FrameTable:
    """packets: (pts, dts, duration, flags) in demux order, from media_probe.video_packets.
    fallback_step_ticks: the frame step for streams with no timestamps at all (see _synthetic_table)."""
    n_total = len(packets)
    rows = []                     # (pts, order, key, duration)
    dropped_discard = dropped_no_pts = 0
    for order, (pts, _dts, dur, flags) in enumerate(packets):
        if "D" in flags:
            dropped_discard += 1
            continue
        if pts is None:
            dropped_no_pts += 1
            continue
        rows.append((pts, order, "K" in flags, dur))
    if not rows and dropped_no_pts == n_total and n_total > 0:
        return _synthetic_table(packets, tb_num, tb_den, fallback_step_ticks)
    rows.sort(key=lambda r: (r[0], r[1]))
    pts_list, key_list, dur_list = [], [], []
    dropped_dup = 0
    for pts, _order, key, dur in rows:
        if pts_list and pts <= pts_list[-1]:
            dropped_dup += 1                      # same or earlier pts as the previous accepted frame: refused
            continue
        pts_list.append(pts)
        key_list.append(key)
        dur_list.append(dur)
    if not pts_list:
        raise ValueError("no usable video packets (every packet lacks a timestamp or is discarded)")
    stats = {"packets": n_total, "dropped_discard_flag": dropped_discard, "dropped_no_pts": dropped_no_pts,
             "dropped_repeated_pts": dropped_dup, "frames": len(pts_list)}
    return FrameTable(tb_num, tb_den, np.asarray(pts_list, dtype=np.int64), np.asarray(key_list, dtype=bool),
                      dur_list[-1], "packets", stats)


def frame_rate_facts(table: FrameTable, avg_frame_rate: Optional[Fraction], r_frame_rate: Optional[Fraction]) -> dict:
    """Frame rate and whether it varies, from the table itself (the container's own claim is reported beside it)."""
    out = {"container_avg_frame_rate": _frac_pair(avg_frame_rate), "container_r_frame_rate": _frac_pair(r_frame_rate)}
    if table.n < 2:
        out.update({"fps_avg": float(avg_frame_rate) if avg_frame_rate else None, "nominal_rate": _frac_pair(
            r_frame_rate or avg_frame_rate), "vfr": False, "vfr_share": 0.0, "step_histogram": []})
        return out
    d = np.diff(table.pts)
    tb = table.tb
    span = float(table.pts[-1] - table.pts[0]) * tb
    fps_avg = (table.n - 1) / span if span > 0 else None
    med = float(np.median(d)) * tb
    # the average over the file is far more precise than any one step (1 ms ticks make 41/42 ms steps for 23.976 fps)
    # (within 1%: a few dropped frames move the average a little; the grid test below decides whether the rate is constant)
    nominal = _match_nominal(fps_avg, 0.01) if fps_avg else None
    if nominal is None and med > 0:
        nominal = _match_nominal(1.0 / med, 0.005)
    if nominal:
        # constant frame rate = every frame lies near its place on one regular grid. Timestamps rounded to a coarser clock than
        # the time base (milliseconds in a 1/16000 MP4) wander by up to a millisecond around the grid and still count as constant;
        # a dropped frame or a 30 / 60 fps stretch moves everything after it off the grid.
        period = float(1 / nominal)
        t = (table.pts - table.pts[0]).astype(np.float64) * tb
        resid = t - np.arange(table.n) * period
        resid -= np.median(resid)
        tol = max(0.0015, 2.0 * tb)
        share = float((np.abs(resid) > tol).mean())
    else:
        period = med
        deviating = np.abs(d.astype(np.float64) * tb - period) > 1.5 * tb + 1e-9
        share = float(deviating.mean())
    vals, counts = np.unique(d, return_counts=True)
    order = np.argsort(-counts)[:6]
    hist = [{"step_ticks": int(vals[i]), "step_ms": round(float(vals[i]) * tb * 1000, 4), "count": int(counts[i])}
            for i in order]
    out.update({"fps_avg": fps_avg, "nominal_rate": _frac_pair(nominal), "vfr": bool(share > 0.005),
                "vfr_share": round(share, 5), "step_histogram": hist,
                "step_ms_min": round(float(d.min()) * tb * 1000, 4), "step_ms_max": round(float(d.max()) * tb * 1000, 4)})
    return out


def _match_nominal(fps: float, rel_tol: float) -> Optional[Fraction]:
    """The standard rate within rel_tol of fps, else None."""
    if not fps or fps <= 0:
        return None
    best = min(_NOMINAL_RATES, key=lambda r: abs(float(r) - fps) / fps)
    return best if abs(float(best) - fps) / fps <= rel_tol else None


def _frac_pair(f: Optional[Fraction]):
    return [f.numerator, f.denominator] if f is not None else None


def _synthetic_table(packets: list, tb_num: int, tb_den: int, fallback_step_ticks: Optional[int]) -> FrameTable:
    """Raw elementary streams (.264 / .hevc) carry no timestamps: the performer's decoder path gives every frame the same
    (undefined) time and keeps only the first. For such files the table is made up: frame i at i * step in decode order
    (display order equals decode order only without B-frames), step = the packets' own duration, else the nominal frame
    step. Flagged source="synthetic": a planner can still use it, but the performer will show one frame until it learns to
    count frames at the stream's rate (the P0b matrix gap G7)."""
    durs = [p[2] for p in packets if p[2]]
    step = max(set(durs), key=durs.count) if durs else fallback_step_ticks
    if not step or step <= 0:
        raise ValueError("no timestamps in the stream and no frame step to make them up from")
    n = len(packets)
    pts = np.arange(n, dtype=np.int64) * int(step)
    key = np.asarray(["K" in p[3] for p in packets], dtype=bool)
    stats = {"packets": n, "dropped_discard_flag": 0, "dropped_no_pts": n, "dropped_repeated_pts": 0, "frames": n,
             "synthetic_step_ticks": int(step)}
    return FrameTable(tb_num, tb_den, pts, key, int(step), "synthetic", stats)
