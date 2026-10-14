"""Per-frame side gate (phase 4 of plans/261013-0718-ai-surround-live-path): does any shown frame break at the seam,
drift and snap, lose detail or boil? Every measure is per frame and side, and a version passes a moment only when every
frame passes. No mean is a verdict (the user, 2026-10-14: X1c's carried sides passed on means and broke on screen).

Scale: X1c's strip, 1920x540 grey (+ chroma when given): the picture at columns 480-1440, a 480 px side on each side of
it. Other sources are scaled to it first (side-gate.py reads two-screen clips, wide 3840x1080 frames or strips). Codes =
mean absolute difference, 0-255. Each measure is NaN where it does not apply (no texture, no truth, not a held step).

With a truth (tests), per frame and side:
  seam          error in the SEAM px of the side next to the seam
  inner, outer  error in the side's halves (reported, no bar of their own)
  line          error within +-LINE px of a line inside the side where its content source changes (default: the
                halves' line, near-every's design); judged like a seam
  change, truth_change, jump   the side's change from the previous frame, the truth's, and |the difference| (a pop
                when the side changes more, a stall when it changes less: a frozen side while the truth slides)
  motion_vs_truth  px: how far the side's band next to the seam moved against the truth's since the previous frame
                (same-frame displacement, its change), worst textured row tile: a break
  drift_vs_truth   px: the largest change of that displacement within the last DRIFT_WINDOW frames: a slide, or a
                stall while the truth moves (a constant offset is neither; drift judges it)
  drift, shape  px: the largest displacement of a textured tile of the side against the truth (phase correlation), and
                the weakest correlation peak among those tiles (a side that lost its form)
  detail_loss   sharpness (as in detail below) of the side's first DETAIL px over the truth's there, weakest row tile
                (same content, so content cancels), as the median over the +-DETAIL_RUN frames around each frame
                (phase 3's 1 MP plates sat at 0.85 of today's best and were seen)
  boil          on held steps (the truth's picture changes < HELD codes): the side's change beyond the truth's
seam, inner, outer and line compare pixels with the truth, so they judge carried truth (X1c-style tests); a generated
side differs from the truth everywhere and is judged by the motion measures and the truth-free ones.

Without a truth (the seam guard, real use), per frame and side. Single frames are read with row medians and against
the picture's own statistics beside the seam, because real content crosses the seam too (2026-10-14: a painting's
frame on the seam column, a dark line passing through it):
  seam_step     codes: the median over all rows of the signed step from the picture's last 2 columns to the side's
                first 2 (a brightness offset along the seam; content crosses it at random signs or over part of the
                height, so a single frame cannot tell a long vertical line lying on the seam from a break)
  seam_colour   the same on the chroma planes, when given
  seam_edges    edges crossing the seam that do not continue: 1 - correlation of the vertical detail profiles of the 4
                px either side of the seam, minus the same for two neighbouring 4 px bands inside the picture, worst
                row tile with structure (0 = continues as well as inside the picture; up to 2)
  detail        sharpness (fine over medium detail: |x - blur1| / |blur1 - blur2|) of the side's first DETAIL px over the
                picture's last DETAIL px, weakest row tile with detail (ratio; low = a soft side; content cancels out)
  seam_motion   px: motion of the band just inside the side minus the band just inside the picture, frame to frame,
                worst textured row tile (phase correlation)
  seam_drift    px: that difference summed over the last DRIFT_WINDOW frames (a side sliding off the picture)
  pop           on new drawings (the picture changes >= HELD codes): how far the side's change stands out from its own
                changes on the drawings around it (+-POP_WINDOW frames), over how far the picture's band next to the
                seam stands out from its own
  boil_free     on held steps (the picture changes < HELD codes): the side's change

Bars live in live/side-gate.json ({"max": x} or {"min": x} per measure, with their source: the user's ladder, the swap
tests, truth-pipe's normal range). A measure without a bar is reported, never judged.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

import cv2
import numpy as np

STRIP_W, STRIP_H = 1920, 540
PICTURE = (480, 1440)
SEAM = 16                 # px of the side next to the seam: where X1c's breaks sat
LINE = 16                 # +- px around a source-change line inside the side
PROFILE = 4               # seam_edges: px bands either side of the seam
PROFILE_MIN = 1.0         # codes: a row tile whose vertical detail profiles are flatter on both sides is not judged
DETAIL = 32               # px either side of the seam compared for sharpness
MOTION = 64               # px wide bands either side of the seam, for seam motion
HELD = 0.5                # codes: a step whose picture changes less is held (no new drawing)
DRIFT_WINDOW = 24         # frames (1 s): seam_drift's window, the ladder's drift shape
POP_WINDOW = 12           # frames either side whose drawings set a side's usual change
DETAIL_RUN = 12           # frames either side: detail_loss is a 1 s running median (softness is a stretch; single
                          # action frames move by +-20 % between the truth's own two copies, 1 s brings that to 0.92)
PEAK_MIN = 0.15           # phase-correlation peak below which a tile's displacement is not trusted
MOTION_TEX_MIN = 0.8      # codes of fine detail below which a band tile is too flat to follow from frame to frame
                          # (flatter tiles lock onto codec noise: the pan's own reference then showed 8 px of motion)
DRIFT_TEX_MIN = 0.25      # the same against the truth (identical content apart from the shift: the decoded pan's line
                          # art at 0.3-0.6 gives clean peaks there)
DETAIL_FLOOR = 0.5        # codes of medium detail below which a band is not judged for sharpness
CHANGE_FLOOR = 0.5        # codes: usual changes below this count as this
PROFILE_TILES, DETAIL_TILES, MOTION_TILES, DRIFT_ROWS = 4, 3, 4, 3

TRUTH_MEASURES = ("seam", "inner", "outer", "line", "change", "truth_change", "jump", "drift", "shape", "detail_loss",
                  "boil", "motion_vs_truth", "drift_vs_truth")
FREE_MEASURES = ("seam_step", "seam_colour", "seam_edges", "detail", "seam_motion", "seam_drift", "pop", "boil_free")
LOW_IS_BAD = ("detail", "shape", "detail_loss")
BARS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "side-gate.json")


@dataclass(frozen=True)
class Side:
    """A side's columns in the strip; out = -1 when it lies left of the picture, +1 right of it."""
    name: str
    x0: int
    x1: int
    out: int

    @property
    def seam(self) -> int:                                        # the column where the picture begins / ends
        return self.x1 if self.out < 0 else self.x0

    @property
    def cols(self) -> slice:
        return slice(self.x0, self.x1)

    @property
    def mid(self) -> int:                                         # the halves' line
        return (self.x0 + self.x1) // 2

    def band(self, w: int) -> slice:
        """The side's w columns next to the seam."""
        return slice(self.seam - w, self.seam) if self.out < 0 else slice(self.seam, self.seam + w)

    def pic_band(self, w: int, skip: int = 0) -> slice:
        """The picture's w columns next to the seam (after skipping `skip` of them)."""
        s = self.seam
        return slice(s + skip, s + skip + w) if self.out < 0 else slice(s - skip - w, s - skip)

    def outer_half(self) -> slice:
        h = (self.x1 - self.x0) // 2
        return slice(self.x0, self.x0 + h) if self.out < 0 else slice(self.x1 - h, self.x1)

    def into_picture(self, k: int) -> int:
        """The column k px from the seam inside the picture."""
        return self.seam + k if self.out < 0 else self.seam - k


def strip_sides(width: int = STRIP_W, picture: tuple = PICTURE) -> tuple:
    return Side("left", 0, picture[0], -1), Side("right", picture[1], width, +1)


SIDES = strip_sides()


def _row_tiles(h: int, n: int) -> list:
    edges = np.linspace(0, h, n + 1).round().astype(int)
    return [slice(a, b) for a, b in zip(edges[:-1], edges[1:])]


_WINDOWS: dict = {}


def _phase(a: np.ndarray, b: np.ndarray) -> tuple:
    """(dx, dy, peak): how far b's content sits from a's (phase correlation, Hann window)."""
    key = a.shape
    if key not in _WINDOWS:
        _WINDOWS[key] = cv2.createHanningWindow((a.shape[1], a.shape[0]), cv2.CV_32F)
    (dx, dy), peak = cv2.phaseCorrelate(np.ascontiguousarray(a), np.ascontiguousarray(b), _WINDOWS[key])
    return dx, dy, peak


def seam_step(img: np.ndarray, side: Side) -> float:
    """Codes: the median over all rows of the signed step from the picture's 2 columns next to the seam to the side's 2
    (an offset along the seam). Content edges cross at random signs, or along part of the height; an offset holds
    along most of it."""
    a = img[:, side.band(2)].mean(axis=1)
    b = img[:, side.pic_band(2)].mean(axis=1)
    return abs(float(np.median(a - b)))


def _ncc(a: np.ndarray, b: np.ndarray) -> float:
    a, b = a - a.mean(), b - b.mean()
    d = float(np.sqrt((a * a).sum() * (b * b).sum()))
    return float((a * b).sum()) / d if d > 1e-6 else 0.0


def seam_edges(vy: np.ndarray, side: Side) -> float:
    """How much worse the vertical detail continues across the seam than inside the picture: (1 - ncc of the PROFILE px
    profiles either side of the seam) - (1 - ncc of two neighbouring PROFILE px bands inside the picture), worst row
    tile with structure. vy: the frame's vertical detail (x - vertical blur)."""
    ps = vy[:, side.band(PROFILE)].mean(axis=1)
    p0 = vy[:, side.pic_band(PROFILE)].mean(axis=1)
    p1 = vy[:, side.pic_band(PROFILE, skip=PROFILE)].mean(axis=1)
    worst = float("nan")
    for rows in _row_tiles(vy.shape[0], PROFILE_TILES):
        if max(ps[rows].std(), p0[rows].std()) < PROFILE_MIN:
            continue
        d = _ncc(p0[rows], p1[rows]) - _ncc(ps[rows], p0[rows])
        worst = d if not worst >= d else worst
    return worst


def detail_ratio(hp1: np.ndarray, hp2: np.ndarray, side: Side) -> float:
    """Sharpness (fine over medium detail) of the side's first DETAIL px over the picture's last DETAIL px, weakest
    row tile where both have medium detail; NaN when none has. A side band with no medium detail is not judged: in one
    frame without a truth a flat wall next to a detailed picture (real content: X1c's pan) and a blurred side look the
    same; detail_loss, against the truth, catches the blur."""
    worst = float("nan")
    for rows in _row_tiles(hp1.shape[0], DETAIL_TILES):
        m_side, m_pic = float(hp2[rows, side.band(DETAIL)].mean()), float(hp2[rows, side.pic_band(DETAIL)].mean())
        if min(m_side, m_pic) < DETAIL_FLOOR:
            continue
        r = (float(hp1[rows, side.band(DETAIL)].mean()) / m_side) / (float(hp1[rows, side.pic_band(DETAIL)].mean()) / m_pic)
        worst = r if not worst <= r else worst
    return worst


def detail_loss(hp1: np.ndarray, hp2: np.ndarray, hp1_t: np.ndarray, hp2_t: np.ndarray, side: Side) -> float:
    """Sharpness of the version's first DETAIL px of the side over the truth's there (same content, so content cancels),
    weakest row tile where the truth has medium detail; NaN when it has none."""
    worst, cols = float("nan"), side.band(DETAIL)
    for rows in _row_tiles(hp1.shape[0], DETAIL_TILES):
        m_t, m_v = float(hp2_t[rows, cols].mean()), float(hp2[rows, cols].mean())
        if m_t < DETAIL_FLOOR:
            continue
        r = (float(hp1[rows, cols].mean()) / max(m_v, 1e-3)) / (float(hp1_t[rows, cols].mean()) / m_t)
        worst = r if not worst <= r else worst
    return worst


def band_motion(prev: np.ndarray, cur: np.ndarray, hp_prev: np.ndarray, hp_cur: np.ndarray, side: Side) -> tuple:
    """Per row tile: the side band's motion minus the picture band's (dx, dy), for tiles where both bands are textured
    and both correlations hold. Returns (worst |difference|, mean difference vector) or (NaN, (0, 0))."""
    diffs = []
    for rows in _row_tiles(cur.shape[0], MOTION_TILES):
        m = []
        for cols in (side.band(MOTION), side.pic_band(MOTION)):
            if min(hp_prev[rows, cols].mean(), hp_cur[rows, cols].mean()) < MOTION_TEX_MIN:
                break
            dx, dy, peak = _phase(prev[rows, cols], cur[rows, cols])
            if peak < PEAK_MIN:
                break
            m.append((dx, dy))
        if len(m) == 2:
            diffs.append((m[0][0] - m[1][0], m[0][1] - m[1][1]))
    if not diffs:
        return float("nan"), (0.0, 0.0)
    d = np.float64(diffs)
    return float(np.hypot(d[:, 0], d[:, 1]).max()), tuple(d.mean(axis=0))


def band_displacement(v: np.ndarray, r: np.ndarray, hp_r: np.ndarray, side: Side) -> list:
    """Per row tile of the side's band next to the seam (seam_motion's band): where the version's content sits against
    the truth's in the same frame, (dx, dy) px, or None where the truth's tile is flat or the correlation does not hold
    (a tile the version invented has no peak and gives no evidence). Same-frame correlation of near-identical content
    is exact to ~0.2 px even on fast action, where two separate frame-to-frame motions of the version and the truth
    lock onto different peaks (35 px apart on X1c's fight between the truth's own two copies)."""
    cols, out = side.band(MOTION), []
    for rows in _row_tiles(v.shape[0], MOTION_TILES):
        if hp_r[rows, cols].mean() < DRIFT_TEX_MIN:
            out.append(None)
            continue
        dx, dy, peak = _phase(r[rows, cols], v[rows, cols])
        out.append((dx, dy) if peak >= PEAK_MIN else None)
    return out


def displacement_changes(disp: list, window: int) -> tuple:
    """From per-frame band displacements (one side): per frame, the largest change of a tile's displacement from the
    previous frame (motion_vs_truth: a break) and the largest change of the mean displacement within the last
    `window` frames (drift_vs_truth: a slide, or a stall while the truth moves)."""
    n = len(disp)
    motion, drift = np.full(n, np.nan), np.full(n, np.nan)
    means = [np.mean([d for d in tiles if d is not None], axis=0) if any(d is not None for d in tiles) else None
             for tiles in disp]
    for t in range(1, n):
        steps = [np.hypot(a[0] - b[0], a[1] - b[1]) for a, b in zip(disp[t], disp[t - 1]) if a is not None and b is not None]
        if steps:
            motion[t] = float(max(steps))
        if means[t] is not None:
            past = [np.hypot(*(means[t] - means[u])) for u in range(max(0, t - window), t) if means[u] is not None]
            if past:
                drift[t] = float(max(past))
    return motion, drift


def tile_drift(ver: np.ndarray, truth: np.ndarray, hp_truth: np.ndarray, side: Side) -> tuple:
    """(largest displacement px, weakest peak) of the side's textured tiles (halves x DRIFT_ROWS) against the truth."""
    worst, weakest = float("nan"), float("nan")
    for cols in (side.band((side.x1 - side.x0) // 2), side.outer_half()):
        for rows in _row_tiles(ver.shape[0], DRIFT_ROWS):
            if hp_truth[rows, cols].mean() < DRIFT_TEX_MIN:
                continue
            dx, dy, peak = _phase(truth[rows, cols], ver[rows, cols])
            weakest = peak if not weakest <= peak else weakest
            if peak >= PEAK_MIN:
                d = float(np.hypot(dx, dy))
                worst = d if not worst >= d else worst
    return worst, weakest


def pops(side_change: np.ndarray, band_change: np.ndarray, drawing: np.ndarray) -> np.ndarray:
    """Per frame and side, on new drawings (the picture changed >= HELD): the side's change over its median change on
    the other drawings within +-POP_WINDOW frames, over the same for the picture's band next to the seam (at least 1)."""
    out = np.full(side_change.shape, np.nan)
    n = side_change.shape[0]
    drawing = np.asarray(drawing, bool) & ~np.isnan(side_change[:, 0])
    for i in range(side_change.shape[1]):
        for t in np.flatnonzero(drawing):
            win = [u for u in range(max(1, t - POP_WINDOW), min(n, t + POP_WINDOW + 1)) if u != t and drawing[u]]
            if len(win) < 3:
                continue
            zs = side_change[t, i] / max(float(np.median(side_change[win, i])), CHANGE_FLOOR)
            zp = band_change[t, i] / max(float(np.median(band_change[win, i])), CHANGE_FLOOR)
            out[t, i] = zs / max(1.0, zp)
    return out


def measure(frames, truth=None, chroma=None, lines=None, sides=SIDES) -> dict:
    """Every measure per frame (axis 0) and side (axis 1, in `sides` order). frames, truth: sequences of HxW grey
    images (uint8 or float) at the gate's scale; chroma: optional sequence of HxWx2 (U, V); lines: {side name: [x, ...]}
    source-change lines inside the sides (default: the halves' lines)."""
    n, k = len(frames), len(sides)
    out = {m: np.full((n, k), np.nan) for m in (TRUTH_MEASURES if truth is not None else ()) + FREE_MEASURES}
    lines = lines if lines is not None else {s.name: [s.mid] for s in sides}
    side_change, band_change, pic_change = np.full((n, k), np.nan), np.full((n, k), np.nan), np.full(n, np.nan)
    pic = slice(sides[0].seam, sides[-1].seam)
    acc = [[] for _ in sides]                                     # per side: the seam-motion difference per frame
    disp = [[] for _ in sides]                                    # per side: band displacements against the truth
    prev = prev_hp = prev_t = None
    for t in range(n):
        v = np.asarray(frames[t], np.float32)
        b1 = cv2.GaussianBlur(v, (0, 0), 1.0)
        hp, hp2 = np.abs(v - b1), np.abs(b1 - cv2.GaussianBlur(v, (0, 0), 2.0))
        vy = v - cv2.GaussianBlur(v, (1, 0), sigmaX=0.1, sigmaY=2.0)
        r = np.asarray(truth[t], np.float32) if truth is not None else None
        if r is not None:
            b1_r = cv2.GaussianBlur(r, (0, 0), 1.0)
            hp_r, hp2_r = np.abs(r - b1_r), np.abs(b1_r - cv2.GaussianBlur(r, (0, 0), 2.0))
        # held: the whole picture repeats (anime holds whole drawings; a flat band next to a seam can stay the same
        # while the camera moves). The truth's picture when there is one.
        src, src_prev = (r, prev_t) if r is not None else (v, prev)
        pic_change[t] = float(np.abs(src[:, pic] - src_prev[:, pic]).mean()) if t else np.nan
        held = t > 0 and pic_change[t] < HELD
        for i, s in enumerate(sides):
            band = s.pic_band(240)
            out["seam_step"][t, i] = seam_step(v, s)
            if chroma is not None:
                c = np.asarray(chroma[t], np.float32)
                out["seam_colour"][t, i] = max(seam_step(c[..., 0], s), seam_step(c[..., 1], s))
            out["seam_edges"][t, i] = seam_edges(vy, s)
            out["detail"][t, i] = detail_ratio(hp, hp2, s)
            if t:
                change = float(np.abs(v[:, s.cols] - prev[:, s.cols]).mean())
                side_change[t, i] = change
                band_change[t, i] = float(np.abs(v[:, band] - prev[:, band]).mean())
                worst, mean_d = band_motion(prev, v, prev_hp, hp, s)
                out["seam_motion"][t, i] = worst
                acc[i].append(mean_d)
                window = np.float64(acc[i][-DRIFT_WINDOW:]).sum(axis=0)
                out["seam_drift"][t, i] = float(np.hypot(*window))
                if held:
                    out["boil_free"][t, i] = change
            if r is None:
                continue
            e = np.abs(v - r)
            out["seam"][t, i] = float(e[:, s.band(SEAM)].mean())
            out["inner"][t, i] = float(e[:, s.band((s.x1 - s.x0) // 2)].mean())
            out["outer"][t, i] = float(e[:, s.outer_half()].mean())
            out["line"][t, i] = max(float(e[:, x - LINE:x + LINE].mean()) for x in lines[s.name]) \
                if lines.get(s.name) else np.nan
            out["drift"][t, i], out["shape"][t, i] = tile_drift(v, r, hp_r, s)
            out["detail_loss"][t, i] = detail_loss(hp, hp2, hp_r, hp2_r, s)
            if t:
                out["change"][t, i] = change
                out["truth_change"][t, i] = float(np.abs(r[:, s.cols] - prev_t[:, s.cols]).mean())
                # two-sided: a side that changes more than the truth pops, one that changes less stalls (a frozen
                # side while the truth slides)
                out["jump"][t, i] = abs(change - out["truth_change"][t, i])
                if held:
                    out["boil"][t, i] = max(0.0, change - out["truth_change"][t, i])
            disp[i].append(band_displacement(v, r, hp_r, s))
        prev, prev_hp, prev_t = v, hp, r
    out["pop"] = pops(side_change, band_change, pic_change >= HELD)
    if truth is not None:
        out["detail_loss"] = running_median(out["detail_loss"], DETAIL_RUN)
        for i in range(k):
            out["motion_vs_truth"][:, i], out["drift_vs_truth"][:, i] = displacement_changes(disp[i], DRIFT_WINDOW)
    return out


def running_median(v: np.ndarray, w: int) -> np.ndarray:
    """Per frame and side, the median over the frames within +-w (NaN where none has a value)."""
    out = np.full(v.shape, np.nan)
    for t in range(v.shape[0]):
        win = v[max(0, t - w):t + w + 1]
        for i in range(v.shape[1]):
            if not np.all(np.isnan(win[:, i])):
                out[t, i] = float(np.nanmedian(win[:, i]))
    return out


# ---- bars and verdicts ------------------------------------------------------------------------------------------------

def load_bars(path: str = BARS_PATH, generated: bool = False) -> dict:
    """{measure: {"max" or "min": value, ...}} for the measures that have a bar. generated: the version's sides are
    generated, not carried truth, so the bars that ask for the truth's own pixels ("applies": "carried") are left out
    (a generator never copies the truth pixel for pixel; judging it so would fail it by construction), and a bar's
    "generated_max" / "generated_min" (set from the user's ladder answers rather than the measurement floor) replaces
    its value."""
    if not os.path.exists(path):
        return {}
    cfg = json.load(open(path, encoding="utf-8"))
    out = {}
    for m, b in cfg.get("bars", {}).items():
        if not b or not ("max" in b or "min" in b) or (generated and b.get("applies") == "carried"):
            continue
        b = dict(b)
        if generated:
            for k in ("max", "min"):
                if f"generated_{k}" in b:
                    b[k] = b[f"generated_{k}"]
        out[m] = b
    return out


def fails(values: dict, bars: dict) -> dict:
    """{measure: (frames x sides) bool}: where a measure crosses its bar (NaN never fails)."""
    out = {}
    for m, b in bars.items():
        if m not in values:
            continue
        v = values[m]
        with np.errstate(invalid="ignore"):
            out[m] = (v > b["max"]) if "max" in b else (v < b["min"])
    return out


def worst(values: dict, sides=SIDES) -> dict:
    """{measure: {side: [value, frame]}}: each measure's worst frame per side (lowest where low is bad)."""
    res = {}
    for m, v in values.items():
        res[m] = {}
        for i, s in enumerate(sides):
            col = v[:, i]
            if np.all(np.isnan(col)):
                res[m][s.name] = [None, None]
                continue
            t = int(np.nanargmin(col) if m in LOW_IS_BAD else np.nanargmax(col))
            res[m][s.name] = [round(float(col[t]), 3), t]
    return res
