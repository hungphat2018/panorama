"""Analyse v0 (role: analyse): per shot, each frame's camera transform, gain and new-drawing flag, and the frames the T1
mosaic is made from. CPU only (the worker runs at below-normal priority); decoding is frame_reader.py.

Shots: the content model's spans, split where the same v0 cut detector finds a cut the model missed (a hidden cut: one
  shot's mosaic must never mix two places); without a content model, that detector alone (grey histogram correlation below
  0.5 and a mean difference above 20 codes; conservative: a missed cut joins two shots, a false one would change the sides
  mid-shot). Without a content model only shots that begin at a cut (a detected one, the film start, or the end of a shot
  already analysed) are published: after a seek the shot under way is not.
Camera (phase 4: live/camera_model.py): each frame's projective map H into the shot's world (the first frame's source
  px), a translation or a homography, re-anchored against drift, holding on flows it cannot trust. H takes continuous
  source px (pixel u's centre at u + 0.5) to world px; the side store gets it as h[8]. offsets[i] is the translation
  part, where the first frame's content near the picture's centre sits in frame i (content moved by +d): it only spaces
  the kept frames.
Plan (phase 4: live/step_planner.py): per new drawing and side, HOLD / STILL / SLIDE / GAIN / PATCH / GEN, the zone
  next to the seam, u_step and u_drift.
Structure (phase 4: live/structure.py): the exit strips (the picture's last view of what left it), a patch page's worth,
  divided by the gain of the frame each was seen in.
Gain (v0's, the store's): the frame's mean linear luma over the median over its lit kept frames, through a soft knee (1
  within +-5 %, the full ratio beyond +-16 %) so grain and small motion never pump the sides; one value for all sides.
  Lit kept frames (voting_offsets): at least VOTE_SHARE of the kept frames' 75th-percentile luma. A fade's dark frames
  neither make the mosaic nor set the reference, so the sides darken and brighten with the picture; a single flash
  stays outvoted.
Kept frames: the first, the last, one each time the camera has moved an eighth of the crop since the last travel pick, and
  periodic picks (every 12 frames, thinned as the shot grows); at most 16, at the decode size (960 px wide, the film's
  aspect).
Order: forward from where the scheduler points (after a seek: the shot after the one under way, whose showing cannot
  change any more), up to the lookahead, where decoding waits for the playhead: past it the analyser follows the playhead
  at film speed, so its CPU share follows what the playhead needs. Shots that begin behind the playhead can no longer
  change what is shown (tiers change only at cuts): while the film plays they are skipped and the analyser rejoins the
  playhead; while it is paused or no player is connected (set_fill_behind), with nothing left ahead, the earliest gap
  behind is filled (what a seek back would find).
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import cv2
import numpy as np

from . import camera_model, frame_reader, step_planner, structure
from .film_timeline import Film

# GEOMETRY_VERSION: what the camera path and the kept frames depend on (mosaics, set features and painted plates are keyed
# by it); PAGE_VERSION: the exit strips and the gain rule they are divided by (patch pages); ANALYSIS_VERSION: both plus
# the step planner's cadence (the analysis file). A new cadence, strip rule or gain rule analyses again but keeps every
# mosaic and painted plate (side_cache.py).
GEOMETRY_VERSION = "analyse-v1|hidden-cuts|keep-8th-16|decode-960x540-area|" + camera_model.VERSION
PAGE_VERSION = "strips-s3-by-edge-cap10-pack-best-of-3-shelves|gain-v1-lit-vote"
ANALYSIS_VERSION = GEOMETRY_VERSION + "|" + PAGE_VERSION + "|plan-" + step_planner.Cadence().key()
DECODE_W = 960                                                    # decode width; the height keeps the film's aspect
KEEP_TRAVEL_SHARE = 0.125
KEEP_PERIOD = 12
MAX_KEPT = 16
VOTE_SHARE = 0.5                                                  # lit kept frames: luma >= this x the kept frames' p75
FLOW_DIV = 4                                                      # the cut detector's grey size: decode size / 4
CUT_CORREL, CUT_DIFF = 0.5, 20.0
_LIN =((np.arange(256) / 255.0) ** 2.4).astype(np.float32)       # code -> linear light (BT.1886 on a true-black display)
_LUMA = np.array([0.2126, 0.7152, 0.0722], np.float32)


@dataclass
class ShotAnalysis:
    shot_id: int
    first: int
    n: int
    crop: tuple                         # source px [x0, y0, x1, y1)
    decode_scale: float                 # source px per decoded px
    from_content_model: bool
    offsets: np.ndarray                 # (n, 2) float64, source px: the translation part of H (spaces the kept frames)
    agree: np.ndarray                   # (n,) float32, the camera's agreement per frame
    luma: np.ndarray                    # (n,) float32, mean linear luma of the picture
    new_drawing: np.ndarray             # (n,) bool
    H: Optional[np.ndarray] = None      # (n, 3, 3) float64: continuous source px of frame i -> the shot's world px
    states: Optional[np.ndarray] = None  # (n, 4) uint8 step states, left/top/right/bottom (side store's step_state)
    gains4: Optional[np.ndarray] = None  # (n, 4) float32 display gains, left/top/right/bottom
    zones: Optional[np.ndarray] = None  # (n, 4, 2) float32, the zone along each seam (source px; NaN: none)
    u: Optional[np.ndarray] = None      # (n, 4, 2) float32, u_step and u_drift per side (diagnostic, phase 5)
    patches: list = field(default_factory=list)   # structure.Patch: the exit strips (persistent), film frames
    kept: list = field(default_factory=list)   # [(frame offset in the shot, rgb at the decode size)]
    kept_index: Optional[list] = None   # the kept offsets when the pixels are not at hand (loaded from the cache)

    @property
    def kept_offsets(self) -> list:
        return [k for k, _ in self.kept] if self.kept else list(self.kept_index or [])

    def maps(self) -> np.ndarray:
        """(n, 3, 3): H, or the translation offsets as maps (an analysis without H)."""
        if self.H is not None:
            return self.H
        m = np.repeat(np.eye(3)[None], self.n, axis=0)
        m[:, :2, 2] = -self.offsets
        return m

    def transforms(self) -> np.ndarray:
        """(n, 8) float32 for the side store's h: each map divided by its last element, row-major, without it."""
        return h8(self.maps())

    def voting_offsets(self) -> list:
        """The lit kept frames (frame offsets in the shot): those the mosaic is made from and the gains refer to."""
        offs = self.kept_offsets or list(range(self.n))
        lum = self.luma[offs]
        floor = VOTE_SHARE * float(np.percentile(lum, 75))
        return [o for o, v in zip(offs, lum) if v >= floor]

    def display_gains(self) -> np.ndarray:
        """(n, 4) per side: the planner's, else v0's one gain for all four sides."""
        if self.gains4 is not None:
            return self.gains4
        ref = float(np.median(self.luma[self.voting_offsets()]))
        g = step_planner.soft_knee(self.luma / ref) if ref > 1e-4 else np.ones(self.n, np.float32)
        return np.repeat(g[:, None], 4, axis=1)

    def arrays(self) -> dict:
        """What the disk cache keeps (everything but the kept frames' pixels and the patches)."""
        out = {"shot_id": self.shot_id, "first": self.first, "n": self.n, "crop": np.array(self.crop),
               "decode_scale": self.decode_scale, "from_content_model": self.from_content_model, "offsets": self.offsets,
               "agree": self.agree, "luma": self.luma, "new_drawing": self.new_drawing,
               "kept_index": np.array(self.kept_offsets, np.int32)}
        for k in ("H", "states", "gains4", "zones", "u"):
            if getattr(self, k) is not None:
                out[k] = getattr(self, k)
        return out

    @classmethod
    def from_arrays(cls, z) -> "ShotAnalysis":
        opt = {k: np.asarray(z[k]) for k in ("H", "states", "gains4", "zones", "u") if k in z}
        return cls(shot_id=int(z["shot_id"]), first=int(z["first"]), n=int(z["n"]), crop=tuple(float(v) for v in z["crop"]),
                   decode_scale=float(z["decode_scale"]), from_content_model=bool(z["from_content_model"]),
                   offsets=np.asarray(z["offsets"], np.float64), agree=np.asarray(z["agree"], np.float32),
                   luma=np.asarray(z["luma"], np.float32), new_drawing=np.asarray(z["new_drawing"], bool),
                   kept_index=[int(v) for v in z["kept_index"]], **opt)


def h8(maps: np.ndarray) -> np.ndarray:
    """(n, 3, 3) maps -> (n, 8) float32: each divided by its last element, row-major, without it (the side store's h)."""
    m = np.asarray(maps, np.float64)
    m = m / m[:, 2:3, 2:3]
    return m.reshape(-1, 9)[:, :8].astype(np.float32)


def decode_size(src_w: int, src_h: int) -> tuple:
    """(width, height) the analyser decodes at: 960 wide, the film's aspect, an even height."""
    return DECODE_W, max(2, int(round(src_h * DECODE_W / src_w / 2.0)) * 2)


def _grey_small(rgb: np.ndarray, crop_dec: tuple) -> np.ndarray:
    """The crop in grey at the flow size (the decode size / FLOW_DIV)."""
    x0, y0, x1, y1 = crop_dec
    g = cv2.cvtColor(np.ascontiguousarray(rgb[y0:y1, x0:x1]), cv2.COLOR_RGB2GRAY)
    return cv2.resize(g, (max(24, (x1 - x0) // FLOW_DIV), max(24, (y1 - y0) // FLOW_DIV)), interpolation=cv2.INTER_AREA)


def _mean_luma(rgb: np.ndarray, crop_dec: tuple) -> float:
    x0, y0, x1, y1 = crop_dec
    small = cv2.resize(np.ascontiguousarray(rgb[y0:y1, x0:x1]), (max(8, (x1 - x0) // 8), max(8, (y1 - y0) // 8)),
                       interpolation=cv2.INTER_AREA)
    return float((_LIN[small] @ _LUMA).mean())


class ShotTracker:
    """One shot's analysis, fed frame by frame in order: the camera path (camera_model), the plan (step_planner), the
    exit strips (structure) and the kept frames."""

    def __init__(self, shot_id: int, first: int, crop: tuple, decode_scale: float, from_content_model: bool):
        self.shot_id, self.first, self.crop, self.ds = shot_id, first, crop, decode_scale
        self.from_cm = from_content_model
        x0, y0, x1, y1 = crop
        self.crop_dec = (int(round(x0 / decode_scale)), int(round(y0 / decode_scale)),
                         int(round(x1 / decode_scale)), int(round(y1 / decode_scale)))
        self.cam = camera_model.CameraTracker(self.crop_dec)         # maps in decoded px of the whole frame
        cw, ch = self.crop_dec[2] - self.crop_dec[0], self.crop_dec[3] - self.crop_dec[1]
        self.plan_size = (step_planner.PLAN_W, max(24, int(round(ch * step_planner.PLAN_W / cw))))
        self.plan = step_planner.StepPlanner(*self.plan_size, step_planner.SIDES)
        self.strips = structure.ShotStructure(crop, decode_scale)
        self.H, self.offsets, self.agree, self.luma, self.new_drawing = [], [], [], [], []
        self.travel_keep, self.periodic_keep = {}, {}
        self.period = KEEP_PERIOD
        self.keep_anchor = np.zeros(2)                              # offset of the last travel pick
        self.last = None
        self.extent = np.array([x1 - x0, y1 - y0])
        self.centre = np.float64([[(x0 + x1) / 2, (y0 + y1) / 2]])

    @property
    def n(self) -> int:
        return len(self.H)

    def add(self, rgb: np.ndarray, new_drawing: Optional[bool] = None):
        rel = self.n
        x0, y0, x1, y1 = self.crop_dec
        grey = cv2.cvtColor(np.ascontiguousarray(rgb[y0:y1, x0:x1]), cv2.COLOR_RGB2GRAY)
        W = self.cam.add(grey, new_drawing)
        H = camera_model.CameraTracker.source_map(W, self.ds)       # continuous source px -> world px
        nd = self.cam.new_drawing
        self.plan.add(cv2.resize(grey, self.plan_size, interpolation=cv2.INTER_AREA),
                      self.cam.grid_map(W, self.plan_size[0]), nd)
        self.strips.add(self.first + rel, rgb, H, nd)
        d = (self.centre - camera_model.apply(H, self.centre))[0]    # content near the centre moved by +d
        self.H.append(H)
        self.offsets.append(d)
        self.agree.append(self.cam.path.agree[-1])
        self.luma.append(_mean_luma(rgb, self.crop_dec))
        self.new_drawing.append(bool(new_drawing) if new_drawing is not None else nd)
        moved = np.abs(d - self.keep_anchor) / self.extent
        if rel == 0 or moved.max() >= KEEP_TRAVEL_SHARE:
            self.travel_keep[rel] = rgb
            self.keep_anchor = d.copy()
        elif rel % self.period == 0:
            self.periodic_keep[rel] = rgb
            if len(self.periodic_keep) > MAX_KEPT // 2:            # thin: keep every other pick, pick half as often
                self.periodic_keep = {k: v for k, v in self.periodic_keep.items() if k % (2 * self.period) == 0}
                self.period *= 2
        self.last = (rel, rgb)

    def finish(self) -> ShotAnalysis:
        picks = dict(self.periodic_keep)
        picks.update(self.travel_keep)
        if self.last is not None:
            picks[self.last[0]] = self.last[1]
        keys = sorted(picks)
        if len(keys) > MAX_KEPT:                                    # first and last stay, the rest thinned evenly
            inner = keys[1:-1]
            pick = np.linspace(0, len(inner) - 1, MAX_KEPT - 2).round().astype(int)
            keys = [keys[0]] + [inner[i] for i in sorted(set(pick))] + [keys[-1]]
        zones, u = self._zones()
        an = ShotAnalysis(shot_id=self.shot_id, first=self.first, n=self.n, crop=self.crop, decode_scale=self.ds,
                          from_content_model=self.from_cm, offsets=np.array(self.offsets, np.float64),
                          agree=np.array(self.agree, np.float32), luma=np.array(self.luma, np.float32),
                          new_drawing=np.array(self.new_drawing, bool), H=np.array(self.H, np.float64),
                          states=self.plan.states(), zones=zones, u=u, patches=self.strips.patches,
                          kept=[(k, picks[k]) for k in keys], kept_index=list(keys))   # (kept_offsets after kept.clear())
        self.strips.normalise_gains(an.display_gains(), self.first)  # the store's gains (v0's), at each strip's frame
        return an

    def _zones(self) -> tuple:
        """(zones (n, 4, 2) source px along each seam, NaN where none; u (n, 4, 2): u_step, u_drift) from the plan."""
        zones = np.full((self.n, 4, 2), np.nan, np.float32)
        u = np.zeros((self.n, 4, 2), np.float32)
        for t, step in enumerate(self.plan.steps):
            for side, v in step.items():
                i = step_planner.SIDES.index(side)
                u[t, i] = (v.u_step, v.u_drift)
                if v.zone:                                        # planner px edges -> decoded px -> source px
                    origin = self.crop_dec[1] if side in ("left", "right") else self.crop_dec[0]
                    k = (self.crop_dec[2] - self.crop_dec[0]) / float(self.plan_size[0])
                    zones[t, i] = [(a * k + origin) * self.ds for a in v.zone]
        return zones, u


def is_cut(g_prev: np.ndarray, g: np.ndarray) -> bool:
    """v0 cut detector for films without a content model."""
    if g_prev.shape != g.shape:
        return True
    h0 = cv2.calcHist([g_prev], [0], None, [32], [0, 256])
    h1 = cv2.calcHist([g], [0], None, [32], [0, 256])
    correl = cv2.compareHist(h0, h1, cv2.HISTCMP_CORREL)
    diff = float(np.abs(g.astype(np.int16) - g_prev.astype(np.int16)).mean())
    return correl < CUT_CORREL and diff > CUT_DIFF


class Analyser(threading.Thread):
    """Decodes forward from where the scheduler points it and hands every finished shot to on_shot. Stops at the
    lookahead (film time ahead of the playhead), skips what plan.is_done says is ready, restarts on goto(). Shots that
    begin behind the playhead are skipped while the film plays; otherwise, with nothing left ahead, it fills the earliest
    gap behind the playhead.

    plan: is_done(first, n) -> bool; next_undone(frame) -> the first frame at or after it no shot covers (incremental
    mode); new_shot_id(first) -> a fresh shot row (incremental mode)."""

    def __init__(self, film: Film, plan, on_shot: Callable[[ShotAnalysis], None], log: Callable[[str], None],
                 threads: int = 4, lookahead_s: float = 120.0):
        super().__init__(daemon=True, name="analyse")
        self.film, self.plan, self.on_shot, self.log = film, plan, on_shot, log
        self.threads, self.lookahead_s = threads, lookahead_s
        self.size = decode_size(film.src_w, film.src_h)
        self.ds = film.src_w / self.size[0]                       # source px per decoded px (both axes)
        self._lock = threading.Lock()
        self._goto: Optional[int] = None
        self._playhead = 0.0
        self._fill_behind = True                                  # shots behind the playhead: only while no film plays
        self._halt = threading.Event()
        self.frames_decoded = 0
        self.busy = False

    # -- control (any thread) ---------------------------------------------------------------------------------------------
    def set_playhead(self, t: float):
        with self._lock:
            self._playhead = t

    def set_fill_behind(self, ok: bool):
        """Whether shots behind the playhead may be analysed: not while the film plays (they cannot change what is shown,
        and the player's timing feels the CPU they take); yes while it is paused or no player is connected."""
        with self._lock:
            self._fill_behind = ok

    def _stale(self, frame: int) -> bool:
        """A shot beginning at `frame` lies behind the playhead while the film plays: skip it, rejoin the playhead."""
        with self._lock:
            return not self._fill_behind and float(self.film.times[frame]) < self._playhead

    def goto(self, frame: int):
        """Start again from the shot after the one under way at `frame` (a seek, or the film start)."""
        with self._lock:
            self._goto = frame

    def stop(self):
        self._halt.set()

    def _take_goto(self) -> Optional[int]:
        with self._lock:
            g, self._goto = self._goto, None
            return g

    def _pending_goto(self) -> bool:
        with self._lock:
            return self._goto is not None

    # -- the decode loop --------------------------------------------------------------------------------------------------
    def _start_frame(self, frame: int, skip_under_way: bool) -> Optional[int]:
        """Where to decode from at or after `frame`: the first frame of the first shot not yet done (content model; a shot
        that began before `frame` is skipped when skip_under_way: its showing cannot change), or the first frame no shot
        covers (incremental). None at the film's end."""
        film = self.film
        if frame >= film.n_frames:
            return None
        if film.spans:
            span = film.span_of_frame(frame)
            if span.first < frame:
                frame = span.first + span.n if skip_under_way else span.first
            while frame < film.n_frames:
                span = film.span_of_frame(frame)
                if not self.plan.is_done(span.first, span.n):
                    return span.first
                frame = span.first + span.n
            return None
        f = self.plan.next_undone(frame)
        return f if f < film.n_frames else None

    def run(self):
        film, nxt, skip = self.film, 0, True
        while not self._halt.is_set():
            g = self._take_goto()
            if g is not None:
                nxt, skip = g, True
            with self._lock:
                playhead, fill_behind = self._playhead, self._fill_behind
            start = self._start_frame(nxt, skip)
            if start is not None and self._stale(start):              # behind the playhead while it plays: rejoin it
                nxt, skip = film.frame_at(playhead) + 1, True
                start = self._start_frame(nxt, skip)
            if start is None or film.times[start] > playhead + self.lookahead_s:
                start = self._start_frame(0, False) if fill_behind else None   # nothing ahead: the earliest gap
                if start is None or film.times[start] > playhead + self.lookahead_s:
                    self.busy = False
                    time.sleep(0.25)
                    continue
            self.busy = True
            # incremental mode: a start right after an analysed shot (or the film's first frame) is a cut
            at_cut = start == 0 or (not film.spans and self.plan.is_done(start - 1, 1))
            try:
                nxt, skip = self._decode_from(start, at_cut), False
            except Exception as e:                                 # a decode failure must not kill the worker
                self.log(f"analyse: decoding from frame {start} failed: {e!r}")
                nxt, skip = start + max(1, int(film.fps)), False
                time.sleep(1.0)
        self.busy = False

    def _wait_for_playhead(self, t: float) -> bool:
        """Waits (the decoder waits with it) until film time t is within the lookahead; False on a goto or stop."""
        while not self._halt.is_set() and not self._pending_goto():
            with self._lock:
                if t <= self._playhead + self.lookahead_s:
                    return True
            self.busy = False
            time.sleep(0.25)
        return False

    def _decode_from(self, start: int, at_cut: bool) -> int:
        """Decodes forward from `start` until a goto, a shot already done, or the film's end, waiting at the lookahead.
        Returns where to continue."""
        film = self.film
        reader = frame_reader.FrameReader(film, start, self.size, self.threads)
        full = (0, 0, self.size[0], self.size[1])
        tracker: Optional[ShotTracker] = None
        span_end = -1
        g_prev = None
        cm = film.content
        diff_before = np.asarray(cm.signal("diff_before")) if cm is not None else None
        try:
            for idx, rgb in reader:
                self.frames_decoded += 1
                if self._halt.is_set() or self._pending_goto() or not self._wait_for_playhead(float(film.times[idx])):
                    return idx
                self.busy = True
                if film.spans:
                    if idx >= span_end:
                        if tracker is not None:
                            self.on_shot(tracker.finish())
                            tracker = None
                        span = film.span_of_frame(idx)
                        span_end = span.first + span.n
                        if idx != span.first or self.plan.is_done(span.first, span.n):
                            return span_end                          # mid-shot or ready: skip it (a new reader seeks)
                        if self._stale(idx):
                            return idx                               # run() rejoins the playhead
                        tracker = ShotTracker(span.shot_id, span.first, film.crop_at(film.cut_time(span.first)), self.ds, True)
                    g = _grey_small(rgb, full)
                    if tracker.n and g_prev is not None and is_cut(g_prev, g):   # a cut the content model missed
                        self.on_shot(tracker.finish())
                        tracker = ShotTracker(self.plan.new_shot_id(idx), idx, film.crop_at(film.cut_time(idx)), self.ds,
                                              False)
                    g_prev = g
                    nd = bool(diff_before[idx] >= 0.5) if diff_before is not None else None
                    tracker.add(rgb, nd)
                    if idx == span_end - 1:
                        self.on_shot(tracker.finish())
                        tracker = None
                else:
                    g = _grey_small(rgb, full)
                    cut = (idx == start and at_cut) or (g_prev is not None and is_cut(g_prev, g))
                    g_prev = g
                    if cut:
                        if tracker is not None:
                            self.on_shot(tracker.finish())
                        tracker = None
                        if self.plan.is_done(idx, 1):
                            return self.plan.next_undone(idx)        # already analysed from here: skip ahead
                        if self._stale(idx):
                            return idx                               # run() rejoins the playhead
                        tracker = ShotTracker(self.plan.new_shot_id(idx), idx, film.crop_at(film.cut_time(idx)), self.ds,
                                              False)
                    if tracker is not None:                        # frames before the first cut after a seek are skipped
                        tracker.add(rgb)
            if tracker is not None:                                # the film's end closes the last shot
                self.on_shot(tracker.finish())
            return film.n_frames
        finally:
            reader.close()
