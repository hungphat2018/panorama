"""Camera model (role: analyse): each frame's map into its shot's world, measured on the picture only (the sides have no
pixels of their own), as a projective map, so pans over curved or 3D backgrounds, zooms and rolls extrapolate to the
sides. Phase 4 of plans/261013-0718-ai-surround-live-path (X1's pan: a translation carried a curved wall wrongly).

Per new drawing (a held drawing is no motion), in grey at the flow size (FLOW_W px wide, an eighth of a 1920 source):
  guess    dense DIS flow from the previous frame; its mode, refined as the median of the vectors within 1.5 px of it
           (hidden_sides.flow_step's rule): a translation. Agreement = the share of vectors within 1.5 px of it.
  anchor   the anchor frame (a recent frame) warped by the chained guess, the leftover flow to this frame fitted by a
           translation and by a homography (MAGSAC); the homography is kept when it is tame (below) and holds the
           picture's outer bands (BAND of its width or height next to each edge: the stand-in for the sides, to which it
           must extrapolate) better than the translation by PREFER (on a flat pan its projective terms are noise that
           grows toward the sides' far edge). A pan in a 3D set, a zoom or a roll too
           slow to show in one step builds up against the anchor, so chained drift stays bounded. The anchor moves on
           when less than ANCHOR_OVERLAP of the picture still overlaps it.
  Measured on X1's pan (true sides, a curved wall): a homography fitted on the picture predicts the sides about twice
  as well as a translation (far error 2.9 against 6.9 px after 5 frames, 6.0 against 13.7 after 10, half size); a
  similarity is worse than a translation there (it fits the edges' speed-up as a zoom). No 2D map follows that wall
  for long (the whole strip's own best homography drifts 1, 2, 4.4, 7 px after 5, 10, 20, 30 frames: parallax), so the
  step planner also refreshes by camera travel.
A homography is refused when its projective terms would bend the sides (its denominator outside [1 / DENOM_MAX,
DENOM_MAX] over the picture widened by SIDE_REACH each way). The anchor's fit is taken when its model agrees with at
least MIN_AGREE of the vectors (within AGREE_TOL) and its leftover translation is under MAX_LEFTOVER flow px.
A guess whose agreement is below MIN_AGREE is not trusted (a flat fill or a flash fooled the flow: X1's credits pan,
agreement 0.07, X5's gate; or the characters' new drawing covers the picture: action on threes, where every third
frame's mode is sometimes the camera's step, sometimes a character's 40 px jump). Such a frame is arbitrated: no motion,
the flow's mode and the last trusted step (when one was taken within PREDICT_FRAMES frames: the background of such
shots moves on every frame) each warp the previous picture, and the one whose outer bands then match this picture best
wins (the mean of the lowest TRIM share of the absolute differences, so the characters do not vote; no motion wins
ties within STILL_BIAS). Constant velocity alone, or the mode alone, each lost badly on some of 24 JJK moments (camera
check: 113 against 66 px, 100 against 31). A step that moves the picture's centre more than MAX_STEP_SHARE of its
width holds.

Maps are 3x3 in the input's pixel index units plus the crop's origin (pixel centres on integers, cv2's convention):
W_t takes frame t's pixels to the shot's world (frame 0's pixels); held drawings and refused steps repeat W_{t-1}.
CameraTracker.source_map converts them to continuous source px (pixel u's centre at (u + 0.5) * scale), the side
store's convention.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import cv2
import numpy as np

FLOW_W = 240                        # flow width (an eighth of 1920, analyse_v0's)
PRESET = cv2.DISOPTICAL_FLOW_PRESET_MEDIUM
ANCHOR_EVERY = 2                    # the anchor fit runs on every n-th new drawing (the guess alone in between)
# Measured 2026-10-14 on 240 JJK pan frames (every frame a new drawing: the worst case), CPU per frame with the planner and
# the strips: 480 px medium every drawing 66.8 ms (23 fps: slower than the film), 240 px medium every 2nd 21.2 ms (68 fps,
# 0.51 CPU-s per film second); the far edge of the sides on synthetic motions: pan 0.03 / 0.17 px, zoom 1.6 / 8.0, roll
# 2.5 / 5.9. DIS fast and ultrafast at 480 px fitted false homographies on a pure pan (9.6 and 15 px).
GRID = 6                            # flow px between grid vectors
EDGE = 8                            # flow px left out at the picture's border (DIS is weak there)
AGREE_TOL = 1.5                     # flow px: agreement of a guess (hidden_sides.flow_step's)
ANCHOR_TOL = 0.5                    # flow px: a vector within this of a model is its inlier (against the anchor)
BAND = 0.25                         # outer share of the picture next to each edge: where the model must hold
PREFER = 0.02                       # the homography must hold the bands this much better than the translation
MIN_AGREE = 0.3
MAX_STEP_SHARE = 0.25
PREDICT_FRAMES = 3                  # a trusted step this recent is a candidate for a frame whose own flow is not trusted
TAME = 4.0                          # a chained map's divisor over the sides' reach stays within this factor of the centre's
TRIM = 0.6                          # share of the band's absolute differences (the lowest) that score an arbitration
STILL_BIAS = 1.03                   # a moving candidate must beat no motion by this factor
MAX_LEFTOVER = 8.0                  # flow px: an anchor fit whose leftover translation is larger is not the camera
SIDE_REACH = 0.8                    # sides reach this share of the picture's size beyond its edges (Setup A: 0.78)
DENOM_MAX = 1.25
ANCHOR_OVERLAP = 0.6
MIN_ANCHOR_OVERLAP = 0.3            # below this the anchor is not used for this frame
HELD_DIFF = 0.5                     # mean |grey difference| (codes, flow size) below which the drawing is held
MODELS = ("still", "predicted", "arbitrated", "translation", "homography")   # per frame: CameraPath.model
VERSION = (f"camera-v1|dis-medium-{FLOW_W}|grid6|mode-guess|anchor-T/H-tol0.5-every{ANCHOR_EVERY}|band0.25|prefer+0.02"
           f"|anchor0.6|reach0.8|denom1.25|gate0.3@1.5px-arbitrate-trim0.6-bias1.03|w/4|tame{TAME}")


@dataclass
class Fit:
    S: np.ndarray                   # 3x3, g0 px -> g1 px (flow px)
    model: str
    agree: float                    # inlier share over the grid
    band_inliers: float = 0.0       # inlier share in the outer bands


def apply(H: np.ndarray, pts: np.ndarray) -> np.ndarray:
    """(N, 2) points through a 3x3 map."""
    p = np.hstack([pts, np.ones((len(pts), 1))]) @ H.T
    return p[:, :2] / p[:, 2:3]


class StepFitter:
    """Fits a map to a flow field between two grey pictures of one size (the flow size)."""

    def __init__(self, w: int, h: int, preset: int = cv2.DISOPTICAL_FLOW_PRESET_MEDIUM):
        self.w, self.h = w, h
        self.dis = cv2.DISOpticalFlow_create(preset)
        ys, xs = np.mgrid[EDGE:h - EDGE:GRID, EDGE:w - EDGE:GRID]
        self.grid = np.stack([xs.ravel(), ys.ravel()], 1).astype(np.float32)
        self.gi = self.grid.astype(int)
        x, y = self.grid[:, 0], self.grid[:, 1]
        self.band = (x < BAND * w) | (x >= (1.0 - BAND) * w) | (y < BAND * h) | (y >= (1.0 - BAND) * h)
        yy, xx = np.mgrid[0:h, 0:w]
        self.band_px = (xx < BAND * w) | (xx >= (1.0 - BAND) * w) | (yy < BAND * h) | (yy >= (1.0 - BAND) * h)
        rx, ry = SIDE_REACH * w, SIDE_REACH * h
        self.reach = np.float64([[-rx, -ry], [w / 2, -ry], [w + rx, -ry], [-rx, h / 2], [w + rx, h / 2],
                                 [-rx, h + ry], [w / 2, h + ry], [w + rx, h + ry]])

    def band_error(self, g0: np.ndarray, g1: np.ndarray, S: np.ndarray) -> float:
        """How well g0 warped by S (flow px) matches g1 in the outer bands: the mean of the lowest TRIM share of the
        absolute differences over the pixels the warp covers."""
        warped = cv2.warpPerspective(g0, S, (self.w, self.h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
        ok = cv2.warpPerspective(np.ones_like(g0), S, (self.w, self.h), flags=cv2.INTER_NEAREST,
                                 borderMode=cv2.BORDER_CONSTANT) > 0
        m = ok & self.band_px
        if m.sum() < 64:
            return float("inf")
        d = np.abs(warped[m].astype(np.float32) - g1[m].astype(np.float32))
        k = max(1, int(TRIM * d.size))
        return float(np.partition(d, k - 1)[:k].mean())

    def flow(self, g0: np.ndarray, g1: np.ndarray) -> np.ndarray:
        return self.dis.calc(g0, g1, None)

    def fit(self, flow: np.ndarray, valid: Optional[np.ndarray] = None, tol: float = ANCHOR_TOL,
            homography: bool = True) -> Fit:
        """The map g0 -> g1 from a flow field: its translation (agreement: the share of vectors within AGREE_TOL), or a
        homography when it is tame and holds the outer bands at least as well (inliers within tol). valid: where g0 has
        content (a warped anchor's footprint)."""
        keep = np.ones(len(self.grid), bool) if valid is None else valid[self.gi[:, 1], self.gi[:, 0]]
        if keep.sum() < 32:
            return Fit(S=np.eye(3), model="still", agree=0.0)
        src = self.grid[keep]
        band = self.band[keep]
        if not band.any():
            band = np.ones(len(src), bool)
        v = flow[self.gi[keep, 1], self.gi[keep, 0]]
        lo, hi = np.floor(v.min(0)) - 1, np.ceil(v.max(0)) + 1
        bins = [max(1, int((hi[i] - lo[i]) / 0.5)) for i in (0, 1)]
        hist, ex, ey = np.histogram2d(v[:, 0], v[:, 1], bins=bins, range=[[lo[0], hi[0]], [lo[1], hi[1]]])
        i, j = np.unravel_index(np.argmax(hist), hist.shape)
        mode = np.array([(ex[i] + ex[i + 1]) / 2, (ey[j] + ey[j + 1]) / 2])
        near = np.abs(v - mode).max(1) <= AGREE_TOL
        t = np.median(v[near], axis=0) if near.any() else mode
        d_t = np.abs(v - t).max(1)
        fit = Fit(S=np.float64([[1, 0, t[0]], [0, 1, t[1]], [0, 0, 1]]), model="translation",
                  agree=float((d_t <= AGREE_TOL).mean()), band_inliers=float((d_t[band] <= tol).mean()))
        if homography and len(src) >= 32:
            dst = src + v
            H, _ = cv2.findHomography(src, dst, cv2.USAC_MAGSAC, tol, maxIters=500, confidence=0.99)
            if H is not None and abs(H[2, 2]) > 1e-9:
                H = H / H[2, 2]
                den = self.reach @ H[2, :2] + 1.0
                d_h = np.abs(apply(H, src) - dst).max(1)
                band_h = float((d_h[band] <= tol).mean())
                if (den.min() > 1.0 / DENOM_MAX and den.max() < DENOM_MAX and band_h >= fit.band_inliers + PREFER
                        and (d_h <= tol).mean() >= 0.8 * (d_t <= tol).mean()):
                    fit = Fit(S=H, model="homography", agree=float((d_h <= AGREE_TOL).mean()), band_inliers=band_h)
        return fit


@dataclass
class CameraPath:
    """Per frame of a shot: W (frame px -> world px), the model and its agreement."""
    W: list = field(default_factory=list)
    model: list = field(default_factory=list)
    agree: list = field(default_factory=list)


class CameraTracker:
    """One shot's camera path, frame by frame: add(grey picture, new_drawing) -> that frame's W."""

    def __init__(self, crop: tuple, flow_w: int = None, preset: int = None, anchor_every: int = None):
        x0, y0, x1, y1 = crop                                     # the picture's pixels in the input image
        self.crop = crop
        self.cw, self.ch = x1 - x0, y1 - y0
        self.fw = flow_w or FLOW_W
        self.fh = max(24, int(round(self.ch * self.fw / self.cw)))
        self.k = self.cw / float(self.fw)                         # input px per flow px
        self.fitter = StepFitter(self.fw, self.fh, PRESET if preset is None else preset)
        self.anchor_every = anchor_every or ANCHOR_EVERY
        self.since_anchor_fit = 0
        self.prev = None                                          # the latest frame's grey at the flow size
        self.anchor = None                                        # (grey at the flow size, its W)
        self.new_drawing = False                                  # whether the latest frame was a new drawing
        self.trusted = None                                       # (frame index, step) of the latest trusted guess
        self.path = CameraPath()
        # flow pixel index -> input pixel index (an area resize: flow pixel i spans input [i k, (i + 1) k)), + origin
        self.F = np.float64([[self.k, 0, x0 + 0.5 * self.k - 0.5], [0, self.k, y0 + 0.5 * self.k - 0.5], [0, 0, 1]])
        self.Finv = np.linalg.inv(self.F)
        self.centre = np.float64([[x0 + self.cw / 2, y0 + self.ch / 2]])

    def small(self, grey_picture: np.ndarray) -> np.ndarray:
        """The picture (crop only) at the flow size."""
        return cv2.resize(grey_picture, (self.fw, self.fh), interpolation=cv2.INTER_AREA)

    def flow_map(self, W: np.ndarray) -> np.ndarray:
        """W in flow px."""
        return self.Finv @ W @ self.F

    def grid_map(self, W: np.ndarray, width: int) -> np.ndarray:
        """W in the pixel grid of the picture resized (area) to `width` px wide (the step planner's grid)."""
        k = self.cw / float(width)
        G = np.float64([[k, 0, self.crop[0] + 0.5 * k - 0.5], [0, k, self.crop[1] + 0.5 * k - 0.5], [0, 0, 1]])
        return np.linalg.inv(G) @ W @ G

    @staticmethod
    def source_map(W: np.ndarray, scale: float) -> np.ndarray:
        """W (input pixel index units, `scale` source px per input px) in continuous source px."""
        P = np.float64([[scale, 0, 0.5 * scale], [0, scale, 0.5 * scale], [0, 0, 1]])
        return P @ W @ np.linalg.inv(P)

    def add(self, grey_picture: np.ndarray, new_drawing: Optional[bool] = None) -> np.ndarray:
        """The frame's W. new_drawing: the content model's flag, or None to decide here (self.new_drawing tells)."""
        g = self.small(grey_picture)
        if self.prev is None:
            self.anchor = (g, np.eye(3))
            self.new_drawing = True
            return self._push(np.eye(3), "still", 1.0, g)
        W_prev = self.path.W[-1]
        diff = float(np.abs(g.astype(np.int16) - self.prev.astype(np.int16)).mean())
        self.new_drawing = bool(new_drawing) if new_drawing is not None else diff >= HELD_DIFF
        if not self.new_drawing or diff < HELD_DIFF:
            return self._push(W_prev.copy(), "still", 1.0, g)
        guess = self.fitter.fit(self.fitter.flow(self.prev, g), homography=False)
        S = self.F @ guess.S @ self.Finv                          # previous frame px -> this frame px (input px)
        model, agree = guess.model, guess.agree
        if agree < MIN_AGREE:                                     # arbitrate: no motion, the mode, the last trusted step
            cands = [("still", np.eye(3)), ("mode", S)]
            if self.trusted is not None and len(self.path.W) - self.trusted[0] <= PREDICT_FRAMES:
                cands.append(("predicted", self.trusted[1]))
            errs = [self.fitter.band_error(self.prev, g, self.Finv @ C @ self.F) * (1.0 if n == "still" else STILL_BIAS)
                    for n, C in cands]
            name, S = cands[int(np.argmin(errs))]
            if name == "still":
                return self._push(W_prev.copy(), "still", agree, g)
            model = "predicted" if name == "predicted" else "arbitrated"
            if name == "mode":
                self.trusted = (len(self.path.W), S)
        else:
            self.trusted = (len(self.path.W), S)
        moved = apply(S, self.centre)[0] - self.centre[0]
        if np.abs(moved).max() > MAX_STEP_SHARE * self.cw:
            return self._push(W_prev.copy(), "still", agree, g)
        W = W_prev @ np.linalg.inv(S)
        ga, Wa = self.anchor
        M_guess = np.linalg.inv(W) @ Wa                           # anchor px -> this frame px (input px)
        M_flow = self.Finv @ M_guess @ self.F
        warped = cv2.warpPerspective(ga, M_flow, (self.fw, self.fh), flags=cv2.INTER_LINEAR,
                                     borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        valid = cv2.warpPerspective(np.full_like(ga, 255), M_flow, (self.fw, self.fh), flags=cv2.INTER_NEAREST,
                                    borderMode=cv2.BORDER_CONSTANT, borderValue=0) > 0
        overlap = float(valid.mean())
        self.since_anchor_fit += 1
        if overlap >= MIN_ANCHOR_OVERLAP and (self.since_anchor_fit >= self.anchor_every or overlap < ANCHOR_OVERLAP):
            self.since_anchor_fit = 0
            inner = cv2.erode(valid.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
            res = self.fitter.fit(self.fitter.flow(warped, g), valid=inner)
            leftover = apply(res.S, np.float64([[self.fw / 2, self.fh / 2]]))[0] - np.float64([self.fw / 2, self.fh / 2])
            if res.agree >= MIN_AGREE and np.abs(leftover).max() <= MAX_LEFTOVER:
                W = Wa @ np.linalg.inv(self.F @ res.S @ self.Finv @ M_guess)
                if res.model != "translation":
                    model = res.model
        if not self._tame(W):                                     # a chained map that folds or blows up the sides: hold
            return self._push(W_prev.copy(), "still", agree, g)
        if overlap < ANCHOR_OVERLAP:
            self.anchor = (g, W.copy())
        return self._push(W, model, agree, g)

    def _tame(self, W: np.ndarray) -> bool:
        """The map's divisor stays positive and within a factor TAME of its value at the picture's centre over the
        picture widened by SIDE_REACH (the steps are checked one by one, their product here)."""
        if not np.all(np.isfinite(W)):
            return False
        x0, y0 = self.crop[0], self.crop[1]
        rx, ry = SIDE_REACH * self.cw, SIDE_REACH * self.ch
        pts = np.float64([[x0 - rx, y0 - ry, 1], [x0 + self.cw + rx, y0 - ry, 1], [x0 - rx, y0 + self.ch + ry, 1],
                          [x0 + self.cw + rx, y0 + self.ch + ry, 1]])
        den = pts @ W[2]
        ref = float(np.r_[self.centre[0], 1.0] @ W[2])
        return ref > 1e-9 and den.min() > ref / TAME and den.max() < ref * TAME

    def _push(self, W: np.ndarray, model: str, agree: float, g: np.ndarray) -> np.ndarray:
        self.path.W.append(W)
        self.path.model.append(model)
        self.path.agree.append(float(agree))
        self.prev = g
        return W.copy()
