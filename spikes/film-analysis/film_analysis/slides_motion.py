"""Camera slides, held drawings, motion and brightness: the per-pair measurement and the run / path derivation.

Ported from the research (slide_pairs.py, slide_runs.py): phase correlation then ECC translation at 480x270 (0.039 px median
error on known shifts after an x265 re-encode, 6 ms per pair), plus layer diagnostics that tell a pure slide from a layered one
(a character over a sliding background, a title plate over a push).

What differs from the scripts, on purpose:
  * the measurement runs on a region of interest: the part of the picture that is picture in every crop mode of the file
    (the whole frame when the file has no bars), so static black bars cannot pull the phase peak to zero;
  * a held pair (the same drawing twice: mean difference below 0.8 levels) skips the correlation and the diagnostics and
    stores a zero shift: the scripts' decisions never used those numbers for held pairs, and 73% of the episode's pairs are
    held, so the pass costs about a third;
  * shifts are stored in source pixels (the 480x270 grid scaled by width/480 and height/270).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np

GRID_W, GRID_H = 480, 270
MARGIN = 24                  # px of the 480x270 grid left out of the diagnostics (inside the region of interest)
HELD_BELOW = 0.8             # mean absolute difference (8-bit levels) under which two frames show the same drawing
MIN_SHIFT_GRID = 0.2         # grid px per pair: 0.8 px at 1920 wide, the research's MIN_SHIFT
MIN_PEAK = 0.1
MIN_EXPLAINED = 0.35
MAX_HELD_IN_RUN = 2
GATE_ABS, GATE_REL = 1.0, 0.35   # a pair is trusted when after <= max(1.0, 0.35 * before)
# purity labels (v0 heuristics read off the 12 inspected runs of the research; to be tuned on the labelled slide set)
PURE_MIN_EXPLAINED, PURE_MAX_STATIC, PURE_MAX_OTHER = 0.85, 0.02, 0.08
LAYERED_MAX_EXPLAINED, LAYERED_MIN_STATIC, LAYERED_MIN_OTHER = 0.70, 0.10, 0.15
_CRIT = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 60, 1e-5)


@dataclass(frozen=True)
class Roi:
    """Region of interest in 480x270 grid coordinates, half-open."""
    x0: int
    y0: int
    x1: int
    y1: int

    @property
    def usable(self) -> bool:
        return (self.x1 - self.x0) >= 96 and (self.y1 - self.y0) >= 64


def roi_from_modes(modes: list, width: int, height: int) -> Roi:
    """Intersection of the picture rectangles of every crop mode (modes: half-open [x0,y0,x1,y1] in source px), in grid
    coordinates; the whole grid when there are none."""
    if not modes:
        return Roi(0, 0, GRID_W, GRID_H)
    x0 = max(m[0] for m in modes)
    y0 = max(m[1] for m in modes)
    x1 = min(m[2] for m in modes)
    y1 = min(m[3] for m in modes)
    sx, sy = GRID_W / width, GRID_H / height
    # round inward so no bar pixel is inside
    return Roi(int(math.ceil(x0 * sx)), int(math.ceil(y0 * sy)), int(math.floor(x1 * sx)), int(math.floor(y1 * sy)))


class PairMeter:
    """Measures the step between two consecutive 480x270 float32 luma frames. One instance per process."""

    def __init__(self, roi: Roi, width: int, height: int):
        self.roi = roi
        self.sx, self.sy = width / GRID_W, height / GRID_H
        w, h = roi.x1 - roi.x0, roi.y1 - roi.y0
        self.w, self.h = w, h
        self.enabled = roi.usable
        if not self.enabled:
            return
        self.win = cv2.createHanningWindow((w, h), cv2.CV_32F)
        m = min(MARGIN, w // 4, h // 4)
        self.core = (slice(m, h - m), slice(m, w - m))
        self.mask = np.zeros((h, w), np.uint8)
        self.mask[m:h - m, m:w - m] = 1

    def measure(self, prev: np.ndarray, cur: np.ndarray) -> dict:
        """prev, cur: full 480x270 float32 luma. Returns shifts in source px and the diagnostics (see signals_store columns)."""
        zero = {"dx": 0.0, "dy": 0.0, "peak": 0.0, "before": 0.0, "after": 0.0, "static": 0.0, "explained": 0.0, "other": 0.0}
        if not self.enabled:
            return zero
        r = self.roi
        a = np.ascontiguousarray(prev[r.y0:r.y1, r.x0:r.x1])
        b = np.ascontiguousarray(cur[r.y0:r.y1, r.x0:r.x1])
        d0 = np.abs(b - a)[self.core]
        before = float(d0.mean())
        if before < HELD_BELOW:
            return {**zero, "peak": 1.0, "before": before, "after": before, "explained": 1.0}
        # phaseCorrelate multiplies the Hann window into its inputs in place: give it copies (the research scripts did too)
        (dx, dy), peak = cv2.phaseCorrelate(a.copy(), b.copy(), self.win)
        warp = np.float32([[1, 0, dx], [0, 1, dy]])
        if math.hypot(dx, dy) >= MIN_SHIFT_GRID:
            try:
                _, warp = cv2.findTransformECC(a, b, warp, cv2.MOTION_TRANSLATION, _CRIT, self.mask, 5)
            except cv2.error:
                pass          # ECC did not converge: keep the phase-correlation estimate
        dx, dy = float(warp[0, 2]), float(warp[1, 2])
        moved = cv2.warpAffine(a, np.float32([[1, 0, dx], [0, 1, dy]]), (self.w, self.h), flags=cv2.INTER_LINEAR,
                               borderMode=cv2.BORDER_REPLICATE)
        d1 = np.abs(b - moved)[self.core]
        gx = cv2.Sobel(a, cv2.CV_32F, 1, 0, ksize=3)[self.core]
        gy = cv2.Sobel(a, cv2.CV_32F, 0, 1, ksize=3)[self.core]
        tex = np.hypot(gx, gy) > 24.0
        nt = max(int(tex.sum()), 1)
        big = math.hypot(dx * self.sx, dy * self.sy) >= 1.0
        static = float(((d0 < 6) & (d1 > 12) & tex).sum() / nt) if big else 0.0
        explained = float(((d1 < 8) & tex).sum() / nt)
        other = float(((d0 > 12) & (d1 > 12) & tex).sum() / nt)
        return {"dx": dx * self.sx, "dy": dy * self.sy, "peak": float(peak), "before": before, "after": float(d1.mean()),
                "static": static, "explained": explained, "other": other}


# --- scdet-like score ---------------------------------------------------------------------------------------------------

def scdet_scores(g8_frames: list, prev_g8: Optional[np.ndarray], prev_mafd: float) -> tuple:
    """FFmpeg's scdet formula on 8-bit 480x270 luma: mafd = mean|d| * 100 / 256, score = clip(min(mafd, |mafd - previous
    mafd|), 0, 100). Returns (scores, last_g8, last_mafd)."""
    out = []
    prev = prev_g8
    for g in g8_frames:
        if prev is None:
            out.append(0.0)
            prev_mafd = 0.0
        else:
            mafd = float(np.abs(g.astype(np.int16) - prev.astype(np.int16)).mean()) * 100.0 / 256.0
            out.append(min(max(min(mafd, abs(mafd - prev_mafd)), 0.0), 100.0))
            prev_mafd = mafd
        prev = g
    return out, prev, prev_mafd


# --- runs and paths (derived layer) ---------------------------------------------------------------------------------------

def find_runs(dx, dy, peak, before, explained, cut_frames: set, fps: float, min_seconds: float, sx: float, sy: float) -> list:
    """Sliding runs. Arrays are indexed by frame (pair i-1 -> i at row i; row 0 unused). cut_frames: indices of frames that start
    a shot (the pair crossing a cut is never part of a run). Returns dicts {first, last, moving} with first/last frame rows of
    the run's pairs (a run of m pairs covers frames first-1 .. last)."""
    n = len(dx)
    min_frames = int(min_seconds * fps)
    runs, cur, held = [], None, 0
    for i in range(1, n):
        shift_grid = math.hypot(dx[i] / sx, dy[i] / sy)
        crossing = i in cut_frames
        slides = shift_grid >= MIN_SHIFT_GRID and peak[i] >= MIN_PEAK and explained[i] >= MIN_EXPLAINED and not crossing
        is_held = before[i] < HELD_BELOW and not crossing
        if cur is not None:
            if slides and dx[i] * cur["dx"] + dy[i] * cur["dy"] > 0:
                cur["last"] = i
                cur["dx"] += dx[i]
                cur["dy"] += dy[i]
                cur["moving"] += 1
                held = 0
                continue
            if is_held and held < MAX_HELD_IN_RUN:
                held += 1
                continue
            runs.append(cur)
            cur = None
        if slides:
            cur, held = {"first": i, "last": i, "dx": float(dx[i]), "dy": float(dy[i]), "moving": 1}, 0
    if cur is not None:
        runs.append(cur)
    return [r for r in runs if (r["last"] - r["first"] + 1) >= min_frames]


def classify_purity(explained: float, static: float, other: float) -> str:
    if explained >= PURE_MIN_EXPLAINED and static <= PURE_MAX_STATIC and other <= PURE_MAX_OTHER:
        return "pure"
    if explained < LAYERED_MAX_EXPLAINED or static > LAYERED_MIN_STATIC or other > LAYERED_MIN_OTHER:
        return "layered"
    return "unknown"


def pair_confidence(before: np.ndarray, after: np.ndarray) -> np.ndarray:
    """Per-pair trust 0..1: 0 when the gate fails (after > max(1.0, 0.35 * before)), else how much the shift removed."""
    gate = after <= np.maximum(GATE_ABS, GATE_REL * before)
    rel = 1.0 - after / np.maximum(before, 1.0)
    return np.where(gate, np.clip(rel, 0.0, 1.0), 0.0)
