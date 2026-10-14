"""Set atlas (roles: analyse + paint, real pixels): shots that show the same place share one world, one canvas and one
plate (phase 3, step 2). A plate painted for a set's first shot serves the later ones, and their real pixels fill each
other's sides; a shot cut back to (dialogue, reverse shots) gets the same sides as before instead of a new invention.
X5 measured JJK's set reuse at 1.10 (plates per film-second 0.33-0.36); dialogue episodes cut back and forth more.

Registration (X5's rule, set-reuse.py, made incremental): each new shot's kept frames give SIFT keypoints (at 480 px
wide, inside the crop, outside the content model's person / head / face boxes when it has them), placed in the shot's
world px through each frame's camera offset, so one shot is one point set. It is matched against the shots analysed
within --window-s of it plus the top_k most similar by an HSV histogram elsewhere: Lowe's ratio test 0.75, one-to-one,
RANSAC similarity (cv2.estimateAffinePartial2D, 12 source px), accepted with >= 10 inliers, inliers spread over >= 0.8 %
of the picture in both shots, roll <= 35 degrees, scale 0.4-2.5 (X5's final rule; its contact sheets: weak accepts
13/14 plausible, random accepts 14/14).
Joining: the shot joins the set of its best accepted match (most inliers) that passes the join check, through that
member's transform; sets never merge (painted plates stay valid). It does not join when the set already has a plate
that covers under 95 % of the shot's canvas, or when the set's texels would be magnified more than 1.5x on its screen
(X5's plate-cover rule); then it opens its own set.
Join check (sets-v2; the caller's verify, worker_sets.py): a plate holds its members' real pixels and the painter's
continuation of them, so the shot must agree with the matched member where both saw the place (agreement(): their
mosaics in cells of 4 texels, at most 15 % of the cells differing), and neither may have a character cut by the
picture's left or right edge (the plate continues that character into the sides). Under sets-v1, cut sheets of 10 JJK
joins (set-atlas-cuts-check.py) showed another shot's character in the sides, a smoky one-frame shot's plate on a
clear shot, a fade's black plate on a lit one, and a dark side where the two shots' depths differed. A set's world is its key shot's (the first one registered); T maps a member's world px to
the set's: a similarity [A | c].
Atlas (T1 level): every member's mosaic warped into the set's world, the key shot's pixels first, then the others in the
order they joined; the extent is the union of the members' canvases, at the key shot's scale (coarser if a heap slot
would not hold it). The painter paints the atlas; the player samples it through each frame's transform composed with T
(the store's frame transform is a projective 3x3 map, schema 2, perf1-ai-sides.c), with the set as the shot's canvas id.
"""
from __future__ import annotations

import json
import math
import os
import threading
from dataclasses import dataclass, field
from typing import Optional

import cv2
import numpy as np

from .analyse_v0 import ShotAnalysis, h8
from .mosaic_t1 import Mosaic

FEATURES_VERSION = "sift480-500x4-0.02|pooled-world"             # the cached features' recipe
SET_VERSION = f"sets-v2|{FEATURES_VERSION}|ratio0.75|ransac12|in10|spread0.008|roll35|scale0.4-2.5|agree4-40-0.15"
WORK_W = 480                                                      # SIFT width (X5's working size)
REP_FRAMES = 4                                                    # kept frames used: the first, the last, two between
SIFT_N = 500                                                      # keypoints per frame (X5: 800 on ~3.7 frames a shot)
MIN_HIST = 0.35                                                   # pairs whose colours overlap less are not matched
RATIO = 0.75
RANSAC_SRC_PX = 12.0                                              # X5's 3 px at 480 wide, in source px of 1920
MIN_INLIERS = 10
MIN_SPREAD = 0.008
MAX_ANGLE = 35.0
SCALE_RANGE = (0.4, 2.5)
PLATE_COVER = 0.95
MAX_ZOOM = 1.5
AGREE_GRID = 4                                                    # texels per side of a comparison cell
AGREE_DIFF = 40.0                                                 # a cell differs: a channel's mean differs by more
AGREE_MAX_SHARE = 0.15                                            # a join: at most this share of the cells differ
AGREE_MIN_CELLS = 200                                             # ... out of at least this many shared cells
IDENTITY = np.float64([[1, 0, 0], [0, 1, 0]])                     # a shot in its own set's world


@dataclass
class ShotFeatures:
    row: int
    start_s: float
    pts: np.ndarray                     # (N, 2) float32, world px of the shot
    des: np.ndarray                     # (N, 128) uint8 (SIFT's values are 0-255; a quarter of float32's RAM)
    hist: np.ndarray                    # (128,) float32, HSV 8x4x4 outside the person boxes, sums to 1
    area: float                         # the picture's area, source px^2 (the spread test's unit)

    def arrays(self) -> dict:
        """What the disk cache keeps (SIFT descriptors are integers 0-255: stored as uint8)."""
        return {"row": self.row, "start_s": self.start_s, "pts": self.pts.astype(np.float32),
                "des": self.des, "hist": self.hist, "area": self.area}

    @classmethod
    def from_arrays(cls, z, row: int, start_s: float) -> "ShotFeatures":
        return cls(row=row, start_s=start_s, pts=np.asarray(z["pts"], np.float32), des=np.asarray(z["des"], np.uint8),
                   hist=np.asarray(z["hist"], np.float32), area=float(z["area"]))


@dataclass
class ShotSet:
    set_id: int                         # its key shot's row
    members: list = field(default_factory=list)          # rows, in the order they joined
    T: dict = field(default_factory=dict)                # row -> 2x3 similarity, member world -> set world
    boxes: dict = field(default_factory=dict)            # row -> (x0, y0, x1, y1) of its canvas in set-world px
    plate_box: Optional[tuple] = None   # the painted plate's extent in set-world px, once there is one
    plate_rkey: Optional[str] = None    # that plate's cache key
    plate_gen: int = 0                  # bumped by every set_plate: a join verified before it must verify again


def features_of(an: ShotAnalysis, person_boxes=None, start_s: float = 0.0) -> Optional[ShotFeatures]:
    """SIFT keypoints of the shot's kept frames in its world px. person_boxes(frame) -> [(x0, y0, x1, y1) source px]."""
    if not an.kept:
        return None
    sift = cv2.SIFT_create(nfeatures=SIFT_N, contrastThreshold=0.02)  # dark episodes: low-contrast keypoints too
    ds = an.decode_scale
    maps = an.maps()
    x0, y0, x1, y1 = an.crop
    pts, des = [], []
    hist = np.zeros((8, 4, 4), np.float32)
    pick = sorted(set(np.linspace(0, len(an.kept) - 1, min(REP_FRAMES, len(an.kept))).round().astype(int).tolist()))
    for rel, rgb in (an.kept[i] for i in pick):
        h, w = rgb.shape[:2]
        f = WORK_W / float(w)                                     # decode px -> work px
        small = cv2.resize(rgb, (WORK_W, max(8, int(round(h * f)))), interpolation=cv2.INTER_AREA)
        k = ds / f                                                # source px per work px
        mask = np.zeros(small.shape[:2], np.uint8)
        mask[int(math.ceil(y0 / k)) + 3:int(y1 / k) - 3, int(math.ceil(x0 / k)) + 3:int(x1 / k) - 3] = 255
        for bx0, by0, bx1, by1 in (person_boxes(an.first + rel) if person_boxes else ()):
            mask[max(0, int(by0 / k) - 4):max(0, int(by1 / k) + 5), max(0, int(bx0 / k) - 4):max(0, int(bx1 / k) + 5)] = 0
        hsv = cv2.cvtColor(small, cv2.COLOR_RGB2HSV)
        hist += cv2.calcHist([hsv], [0, 1, 2], mask if mask.any() else None, [8, 4, 4], [0, 180, 0, 256, 0, 256])
        kp, d = sift.detectAndCompute(cv2.cvtColor(small, cv2.COLOR_RGB2GRAY), mask)
        if d is None or not kp:
            continue
        p = (np.float64([q.pt for q in kp]) + 0.5) * k            # work px -> source px of this frame
        pts.append(apply_map(maps[rel], p).astype(np.float32))    # -> world px through the frame's camera map
        des.append(np.clip(np.round(d), 0, 255).astype(np.uint8))
    if not pts:
        return None
    hist = hist.ravel()
    return ShotFeatures(row=an.shot_id, start_s=start_s, pts=np.vstack(pts), des=np.vstack(des),
                        hist=hist / max(1.0, float(hist.sum())), area=float((x1 - x0) * (y1 - y0)))


def match(a: ShotFeatures, b: ShotFeatures) -> dict:
    """The similarity mapping b's world px to a's, from one-to-one ratio-test matches and RANSAC; 'accepted' by X5's
    rule. Pooling a shot's frames in its world makes one point set per shot (one match per pair, not one per frame pair)."""
    out = {"a": a.row, "b": b.row, "inliers": 0, "accepted": False}
    if len(a.des) < 8 or len(b.des) < 8:
        return out
    m = cv2.BFMatcher(cv2.NORM_L2).knnMatch(b.des.astype(np.float32), a.des.astype(np.float32), k=2)
    good = {}
    for x in m:
        if len(x) == 2 and x[0].distance < RATIO * x[1].distance:
            if x[0].trainIdx not in good or x[0].distance < good[x[0].trainIdx].distance:
                good[x[0].trainIdx] = x[0]
    good = list(good.values())
    if len(good) < 8:
        return out
    src = b.pts[[g.queryIdx for g in good]]
    dst = a.pts[[g.trainIdx for g in good]]
    M, inl = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=RANSAC_SRC_PX,
                                         maxIters=2000, confidence=0.995)
    if M is None:
        return out
    inl = inl.ravel().astype(bool)
    ni = int(inl.sum())
    spread = [float(np.prod(np.ptp(p[inl], axis=0)) / ar) if ni > 1 else 0.0 for p, ar in ((dst, a.area), (src, b.area))]
    scale = float(np.hypot(M[0, 0], M[1, 0]))
    angle = float(np.degrees(np.arctan2(M[1, 0], M[0, 0])))
    out.update(inliers=ni, good=len(good), scale=scale, angle=angle, spread=spread, M=M.astype(np.float64))
    out["accepted"] = (ni >= MIN_INLIERS and SCALE_RANGE[0] <= scale <= SCALE_RANGE[1] and abs(angle) <= MAX_ANGLE
                       and min(spread) >= MIN_SPREAD)
    return out


def compose(t1: np.ndarray, t2: np.ndarray) -> np.ndarray:
    """t1 after t2 (2x3 affine maps)."""
    return np.hstack([t1[:, :2] @ t2[:, :2], (t1[:, :2] @ t2[:, 2] + t1[:, 2])[:, None]])


def invert(t: np.ndarray) -> np.ndarray:
    a_inv = np.linalg.inv(t[:, :2])
    return np.hstack([a_inv, (-a_inv @ t[:, 2])[:, None]])


def box_through(t: np.ndarray, box: tuple) -> tuple:
    """The axis-aligned box around a box's corners mapped by t."""
    x0, y0, x1, y1 = box
    c = np.float64([[x0, y0], [x1, y0], [x0, y1], [x1, y1]]) @ t[:, :2].T + t[:, 2]
    return float(c[:, 0].min()), float(c[:, 1].min()), float(c[:, 0].max()), float(c[:, 1].max())


def cover_share(inner: tuple, outer: tuple) -> float:
    """The share of `inner`'s area inside `outer`."""
    ix0, iy0, ix1, iy1 = inner
    ox0, oy0, ox1, oy1 = outer
    w, h = max(0.0, min(ix1, ox1) - max(ix0, ox0)), max(0.0, min(iy1, oy1) - max(iy0, oy0))
    return w * h / max(1e-6, (ix1 - ix0) * (iy1 - iy0))


def apply_map(m: np.ndarray, pts: np.ndarray) -> np.ndarray:
    """(N, 2) points through a 3x3 map."""
    p = np.hstack([pts, np.ones((len(pts), 1))]) @ m.T
    return p[:, :2] / p[:, 2:3]


def frame_transforms(an: ShotAnalysis, t: np.ndarray) -> np.ndarray:
    """The shot's per-frame transforms (source px -> set world px) as the store wants them: (n, 8) float32, the set's
    similarity t (2x3, member world -> set world) after each frame's projective map."""
    T = np.vstack([np.asarray(t, np.float64), [0.0, 0.0, 1.0]])
    return h8(np.einsum("ij,njk->nik", T, an.maps()))


class SetRegistry:
    """The film's sets, built shot by shot as the analyser finishes them; thread-safe. Persisted (to_json / from_json) in
    the disk cache, so a later run keeps the sets its plates were painted for."""

    def __init__(self, window_s: float = 90.0, top_k: int = 8):
        self.window_s, self.top_k = window_s, top_k
        self.feats: dict = {}                                     # row -> ShotFeatures (this run's, in RAM)
        self.set_of: dict = {}                                    # row -> set id
        self.sets: dict = {}                                      # set id -> ShotSet
        self.lock = threading.Lock()

    def candidates(self, f: ShotFeatures) -> list:
        """Shots within the window, then the top_k most similar in colour elsewhere; none whose colours overlap less
        than MIN_HIST (histogram intersection: the same place keeps most of its palette)."""
        sim = {g.row: float(np.minimum(f.hist, g.hist).sum()) for g in self.feats.values() if g.row != f.row}
        near = [g for g in self.feats.values() if g.row in sim and abs(g.start_s - f.start_s) <= self.window_s]
        far = sorted((g for g in self.feats.values() if g.row in sim and abs(g.start_s - f.start_s) > self.window_s),
                     key=lambda g: -sim[g.row])[:self.top_k]
        return [g for g in near + far if sim[g.row] >= MIN_HIST]

    def register(self, f: ShotFeatures, box: tuple, verify=None) -> tuple:
        """(set id, T member world -> set world, the accepted match or None) for a newly analysed shot whose canvas is
        `box` in its own world px. A shot without features (dark, flat) opens its own set (open_alone). Accepted matches
        are tried by inliers; verify(match) -> bool, when given, is the join check (run outside the lock)."""
        with self.lock:
            cands = self.candidates(f)
        acc = [r for r in (match(g, f) for g in cands) if r["accepted"]]   # outside the lock (CPU seconds)
        with self.lock:
            self.feats[f.row] = f
        for best in sorted(acc, key=lambda r: -r["inliers"]):
            gen = self._plate_gen(best)
            if self._joinable(f.row, best, box) is None or (verify is not None and not verify(best)):
                continue
            with self.lock:                                       # again: a plate may have been painted meanwhile
                j = self._joinable(f.row, best, box, locked=True)
                if j is not None and j[0].plate_gen == gen:     # verify judged the set as it still is
                    s, t, sbox = j
                    s.members.append(f.row)
                    s.T[f.row], s.boxes[f.row] = t, sbox
                    self.set_of[f.row] = s.set_id
                    return s.set_id, t, best
        with self.lock:
            return self._open(f.row, box), IDENTITY.copy(), None

    def _plate_gen(self, best: dict) -> int:
        with self.lock:
            s = self.sets.get(self.set_of.get(best["a"], -1))
            return s.plate_gen if s is not None else -1

    def _joinable(self, row: int, best: dict, box: tuple, locked: bool = False):
        """(set, T, the canvas box in set-world px) when the shot may join best's set by the geometry rules, else None."""
        if not locked:
            with self.lock:
                return self._joinable(row, best, box, locked=True)
        sid = self.set_of.get(best["a"])
        s = self.sets.get(sid) if sid is not None else None
        if s is None or row in self.set_of:
            return None
        t = compose(s.T[best["a"]], best["M"])                     # f's world -> a's world -> set world
        zoom = 1.0 / float(np.hypot(t[0, 0], t[1, 0]))              # set texels per screen texel of f
        sbox = box_through(t, box)
        if zoom <= MAX_ZOOM and (s.plate_box is None or cover_share(sbox, s.plate_box) >= PLATE_COVER):
            return s, t, sbox
        return None

    def _open(self, row: int, box: tuple) -> int:
        s = ShotSet(set_id=row, members=[row], T={row: IDENTITY.copy()}, boxes={row: tuple(box)})
        self.sets[row] = s
        self.set_of[row] = row
        return row

    def open_alone(self, row: int, box: tuple) -> int:
        """A shot with no features: its own set (the caller passes its row)."""
        with self.lock:
            return self._open(row, box)

    def set_plate(self, set_id: int, box: tuple, rkey: str):
        with self.lock:
            if set_id in self.sets:
                s = self.sets[set_id]
                s.plate_box, s.plate_rkey, s.plate_gen = tuple(box), rkey, s.plate_gen + 1

    def plate_of(self, set_id: int) -> tuple:
        """(plate box, plate cache key) of the set, or (None, None)."""
        with self.lock:
            s = self.sets.get(set_id)
            return (s.plate_box, s.plate_rkey) if s is not None else (None, None)

    def members(self, set_id: int) -> list:
        with self.lock:
            s = self.sets.get(set_id)
            return [] if s is None else [(r, s.T[r].copy()) for r in s.members]

    def set_id_of(self, row: int) -> Optional[int]:
        with self.lock:
            return self.set_of.get(row)

    def to_json(self, span_of) -> dict:
        """span_of(row) -> (first, n): shots are kept by their frame span, which, unlike rows, is the same in every run."""
        with self.lock:
            return {"version": SET_VERSION, "sets": [
                {"key": list(span_of(s.set_id)),
                 "members": [{"span": list(span_of(r)), "T": s.T[r].round(6).tolist(), "box": list(s.boxes[r])}
                             for r in s.members],
                 "plate_box": list(s.plate_box) if s.plate_box else None, "plate_rkey": s.plate_rkey}
                for s in self.sets.values()]}

    def load_json(self, d: dict, row_of) -> int:
        """row_of(first, n) -> this run's row, or None for a shot not known (yet); a set loads only when all its
        members are known. Returns how many sets were loaded."""
        if d.get("version") != SET_VERSION:
            return 0
        n_sets = 0
        with self.lock:
            for e in d["sets"]:
                rows = [row_of(*m["span"]) for m in e["members"]]
                key = row_of(*e["key"])
                if key is None or any(r is None for r in rows) or any(r in self.set_of for r in rows):
                    continue
                s = ShotSet(set_id=key, plate_box=tuple(e["plate_box"]) if e.get("plate_box") else None,
                            plate_rkey=e.get("plate_rkey"))
                for r, m in zip(rows, e["members"]):
                    s.members.append(r)
                    s.T[r], s.boxes[r] = np.float64(m["T"]), tuple(m["box"])
                    self.set_of[r] = key
                self.sets[key] = s
                n_sets += 1
        return n_sets

    def add_features(self, f: ShotFeatures):
        """A shot from an earlier run (its features from the cache): matchable again, its set kept."""
        with self.lock:
            self.feats[f.row] = f


def agreement(a: Mosaic, b: Mosaic, m_b_to_a) -> dict:
    """Do shot b's real pixels agree with shot a's where both saw the place? b's mosaic goes through m (b's world px ->
    a's) onto a's texels; both are averaged in cells of AGREE_GRID texels lying inside both coverages (small
    misregistration and line art then do not count), and a cell differs when a channel's means differ by more than
    AGREE_DIFF codes. ok: enough shared cells, few of them differing."""
    to_world = np.float64([[a.scale, 0, a.world_x0 + 0.5 * a.scale], [0, a.scale, a.world_y0 + 0.5 * a.scale]])
    to_b = np.float64([[1 / b.scale, 0, -b.world_x0 / b.scale - 0.5], [0, 1 / b.scale, -b.world_y0 / b.scale - 0.5]])
    mp = compose(to_b, compose(invert(np.asarray(m_b_to_a, np.float64)), to_world))   # a texel -> b texel
    size = (a.tex_w, a.tex_h)
    img = cv2.warpAffine(b.rgb, mp, size, flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                         borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    cov = cv2.warpAffine(b.coverage.astype(np.uint8) * 255, mp, size, flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                         borderMode=cv2.BORDER_CONSTANT, borderValue=0) >= 255
    g = AGREE_GRID
    h, w = (a.tex_h // g) * g, (a.tex_w // g) * g
    if h == 0 or w == 0:
        return {"cells": 0, "differ": 1.0, "median": None, "ok": False}
    full = (cov & a.coverage)[:h, :w].reshape(h // g, g, w // g, g).all(axis=(1, 3))
    n = int(full.sum())
    if not n:
        return {"cells": 0, "differ": 1.0, "median": None, "ok": False}
    ca = a.rgb[:h, :w].reshape(h // g, g, w // g, g, 3).mean(axis=(1, 3))[full]
    cb = img[:h, :w].reshape(h // g, g, w // g, g, 3).mean(axis=(1, 3))[full]
    d = np.abs(ca - cb).max(axis=1)
    differ = float((d > AGREE_DIFF).mean())
    return {"cells": n, "differ": round(differ, 3), "median": round(float(np.median(d)), 1),
            "ok": n >= AGREE_MIN_CELLS and differ <= AGREE_MAX_SHARE}


def atlas(members: list, scale: float, max_bytes: int) -> Optional[Mosaic]:
    """The set's real pixels: members = [(Mosaic in the member's world, T member world -> set world)], key first. Each
    texel takes the first member (in that order) that saw it; the extent is the union of the members' canvases."""
    if not members:
        return None
    boxes = [box_through(t, (m.world_x0, m.world_y0, m.world_x0 + m.tex_w * m.scale, m.world_y0 + m.tex_h * m.scale))
             for m, t in members]
    wx0, wy0 = math.floor(min(b[0] for b in boxes)), math.floor(min(b[1] for b in boxes))
    wx1, wy1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
    while True:
        tw, th = int(math.ceil((wx1 - wx0) / scale)), int(math.ceil((wy1 - wy0) / scale))
        if tw * th * 4 <= max_bytes and tw <= 16384 and th <= 16384:
            break
        scale *= 1.25
    rgb = np.zeros((th, tw, 3), np.uint8)
    cover = np.zeros((th, tw), bool)
    share_num = share_den = 0.0
    for m, t in members:
        # atlas texel -> set world -> member world -> member texel
        to_world = np.float64([[scale, 0, wx0 + 0.5 * scale], [0, scale, wy0 + 0.5 * scale]])
        to_member = np.float64([[1 / m.scale, 0, -m.world_x0 / m.scale - 0.5], [0, 1 / m.scale, -m.world_y0 / m.scale - 0.5]])
        mp = compose(to_member, compose(invert(t), to_world))
        img = cv2.warpAffine(m.rgb, mp, (tw, th), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                             borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        ok = cv2.warpAffine(m.coverage.astype(np.uint8) * 255, mp, (tw, th), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                            borderMode=cv2.BORDER_CONSTANT, borderValue=0) >= 255
        new = ok & ~cover
        rgb[new] = img[new]
        cover |= ok
        share_num += m.side_share * m.tex_w * m.tex_h
        share_den += m.tex_w * m.tex_h
    return Mosaic(world_x0=float(wx0), world_y0=float(wy0), scale=float(scale), rgb=rgb, coverage=cover,
                  side_share=share_num / max(1.0, share_den))


def save_registry(reg: SetRegistry, path: str, span_of):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(reg.to_json(span_of), f)
    os.replace(tmp, path)


def load_registry(reg: SetRegistry, path: str, row_of) -> int:
    try:
        with open(path, encoding="utf-8") as f:
            return reg.load_json(json.load(f), row_of)
    except (OSError, ValueError, KeyError, TypeError):
        return 0
