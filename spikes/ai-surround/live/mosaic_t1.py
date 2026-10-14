"""T1 mosaic (role: paint, real pixels): the shot's own real pixels along its camera path, in its world, ready as a canvas.

Canvas extent: everything the player may sample during the shot: the crop widened by the room's margins (need_margin_px,
  source px beyond the crop on the left, top, right, bottom) at every frame's camera position, in world px. Texel i covers
  world px [x0 + i * scale, x0 + (i + 1) * scale); scale is world (= source) px per texel, 2.0 by default (half the film's
  resolution: the painter's plates are coarser still), raised when a long pan would not fit a heap slot.
Pixels: every lit kept frame's picture (analyse_v0's voting_offsets: a fade's dark frames do not vote; its crop only,
  never the bars) warped to its world position; per texel the colour of the frame whose brightness there is the median of
  the frames that saw it (whole pixels, never channels of different frames; moving characters drop out where the camera
  saw the background more than twice).
Reliability, per region: a texel seen by 2+ frames disagrees when under AGREE_SHARE of them are within AGREE_CODES of
  the picked frame's brightness: the frames do not show the same thing there (a character or an effect that moves with
  the camera, or a 3D move the projective maps cannot follow: the X1 gallery pan's chained maps were 30-45 px off a
  direct registration at the picture's corners after 21 frames). Outside the first frame's picture, a region (Gaussian
  of REGION_SIGMA texels, outside texels only) where at least REGION_UNSURE of the multi-seen texels disagree is left
  out, seen but `unsure`: T1 leaves it to the glow (alpha 0 beyond its feather), the painter repaints it. Regions, not single texels (an edge or grain would punch holes everywhere), not
  8-texel cells (a blocky checkerboard of holes), not the whole shot (a gate at 10 % took the sides of 28 % of JJK's
  shots, the static skyline of a pan with moving smoke included). `disagree` is the shot's share, for the record.
Inside the first frame's picture (T1 never shows it: the picture covers it) the mosaic holds one real frame, not the
  vote: the representative one, nearest the vote there among the lit kept frames that see at least REPR_COVER of it
  (the vote where it does not reach). The painter continues one real frame at the seam rather than a blend of frames
  (on action shots the vote there is a ghost), and the set check and the painter's feathered keep of real pixels compare
  real pictures. Not frame 0 itself: a shot that opens in smoke and clears would have its plate continue the smoke.
T1 canvas: those pixels with coverage in alpha, feathered over 12 texels so real pixels fade into the player's glow;
  uncovered texels carry their nearest covered colour (filtering then never pulls in black). A shot whose real pixels cover
  under 1 % of its first frame's side area makes no T1 (mosaic_empty): the glow shows until a plate exists.
The painter (T2) starts from the same pixels and coverage (unfeathered), grey where nothing was seen or it was unsure.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np

from .analyse_v0 import ShotAnalysis

MOSAIC_VERSION = ("mosaic-v0.8|projective-maps|lit-vote|medoid-by-luma|agree-15-0.6-region-s12-0.25-m0.1-outside"
                  "|inside-representative-frame-0.8|feather-12|push-pull-fill")
FEATHER_TEXELS = 12
EMPTY_SHARE = 0.01
STRIP = 64
GREY = 128                                                        # the outpaint LoRA's fill colour (comfy-plates.GREY)
AGREE_CODES = 15.0                                                # brightness (codes) within which two frames agree
AGREE_SHARE = 0.6
REPR_COVER = 0.8                                                  # the representative frame sees this share of the picture
REGION_SIGMA = 12.0                                               # texels
REGION_UNSURE = 0.25
REGION_MIN_MULTI = 0.1                                            # a region needs this share of multi-seen texels to be judged
MIN_JUDGED = 2000                                                 # multi-seen texels outside the picture for `disagree`


@dataclass
class Mosaic:
    world_x0: float
    world_y0: float
    scale: float                        # world px per texel
    rgb: np.ndarray                     # (h, w, 3) uint8, what was seen (0 where nothing was)
    coverage: np.ndarray                # (h, w) bool: seen and sure
    side_share: float                   # share of the first frame's side area the real pixels cover
    unsure: Optional[np.ndarray] = None  # (h, w) bool: seen, but left out (the frames do not share one world)
    disagree: float = 0.0               # share of the multi-seen texels outside the first frame's picture that disagree

    @property
    def tex_w(self) -> int:
        return self.rgb.shape[1]

    @property
    def tex_h(self) -> int:
        return self.rgb.shape[0]

    @property
    def empty(self) -> bool:
        return self.side_share < EMPTY_SHARE

    def canvas_rgba(self) -> np.ndarray:
        """The T1 canvas: filled colour, feathered coverage in alpha."""
        dist = cv2.distanceTransform(self.coverage.astype(np.uint8), cv2.DIST_L2, 5)
        alpha = np.clip(dist / FEATHER_TEXELS, 0.0, 1.0)
        out = np.empty(self.rgb.shape[:2] + (4,), np.uint8)
        out[..., :3] = fill_holes(self.rgb, self.coverage)
        out[..., 3] = np.round(alpha * 255.0).astype(np.uint8)
        return out

    def painter_input(self) -> np.ndarray:
        """What the painter completes: the real pixels on grey."""
        out = np.full(self.rgb.shape, GREY, np.uint8)
        out[self.coverage] = self.rgb[self.coverage]
        return out


def canvas_geometry(an: ShotAnalysis, margins, scale: float, max_bytes: int) -> tuple:
    """(world_x0, world_y0, scale, tex_w, tex_h) of the shot's canvas: the crop widened by the margins, through every
    frame's map (corners and edge midpoints: a homography bends the edges outward at most there)."""
    x0, y0, x1, y1 = an.crop
    ml, mt, mr, mb = (float(v) for v in margins)
    ax0, ay0, ax1, ay1 = x0 - ml, y0 - mt, x1 + mr, y1 + mb
    pts = np.float64([[ax0, ay0], [ax1, ay0], [ax0, ay1], [ax1, ay1], [(ax0 + ax1) / 2, ay0], [(ax0 + ax1) / 2, ay1],
                      [ax0, (ay0 + ay1) / 2], [ax1, (ay0 + ay1) / 2]])
    p = np.einsum("nij,kj->nki", an.maps(), np.hstack([pts, np.ones((len(pts), 1))]))
    with np.errstate(all="ignore"):
        w = p[..., :2] / p[..., 2:3]
    # a degenerate map (the camera model holds those, but an old cache may not) must not make the canvas absurd
    lim = 4.0 * max(x1 - x0, y1 - y0)
    w = np.clip(np.nan_to_num(w, nan=0.0), np.float64([x0, y0]) - lim, np.float64([x1, y1]) + lim)
    wx0, wy0 = np.floor(float(w[..., 0].min())), np.floor(float(w[..., 1].min()))
    wx1, wy1 = float(w[..., 0].max()), float(w[..., 1].max())
    while True:
        tw, th = int(np.ceil((wx1 - wx0) / scale)), int(np.ceil((wy1 - wy0) / scale))
        if tw * th * 4 <= max_bytes and tw <= 16384 and th <= 16384:
            return float(wx0), float(wy0), float(scale), tw, th
        scale *= 1.25


def fill_holes(rgb: np.ndarray, coverage: np.ndarray) -> np.ndarray:
    """Push-pull: uncovered texels take the colour of the nearest covered ones (coarser levels reach farther)."""
    if coverage.all() or not coverage.any():
        return rgb.copy()
    levels = []
    c = coverage.astype(np.float32)
    x = rgb.astype(np.float32) * c[..., None]
    while (c <= 1e-6).any() and min(c.shape) > 2:
        levels.append((x, c))
        size = (max(1, x.shape[1] // 2), max(1, x.shape[0] // 2))
        x = cv2.resize(x, size, interpolation=cv2.INTER_AREA)
        c = cv2.resize(c, size, interpolation=cv2.INTER_AREA)
    mean = x.reshape(-1, 3).sum(0) / max(float(c.sum()), 1e-6)    # for what even the coarsest level did not reach
    out = np.where(c[..., None] > 1e-6, x / np.maximum(c, 1e-6)[..., None], mean)
    for x_l, c_l in reversed(levels):
        up = cv2.resize(out, (x_l.shape[1], x_l.shape[0]), interpolation=cv2.INTER_LINEAR)
        out = np.where(c_l[..., None] > 1e-6, x_l / np.maximum(c_l, 1e-6)[..., None], up)
    return np.clip(np.round(out), 0, 255).astype(np.uint8)


def build(an: ShotAnalysis, margins, scale: float = 2.0, max_bytes: int = 16 << 20) -> Mosaic:
    """The shot's mosaic from its lit kept frames (an.kept must hold their pixels)."""
    voters = set(an.voting_offsets())
    kept = [(rel, rgb) for rel, rgb in an.kept if rel in voters]
    if not kept:
        raise ValueError(f"shot {an.shot_id}: no kept frames to make a mosaic from")
    wx0, wy0, scale, tw, th = canvas_geometry(an, margins, scale, max_bytes)
    ds = an.decode_scale
    x0, y0, x1, y1 = an.crop
    h_dec, w_dec = kept[0][1].shape[:2]
    valid = np.zeros((h_dec, w_dec), np.uint8)                     # the picture inside the crop, 1 px in from its edge
    cx0, cy0 = int(np.ceil(x0 / ds)) + 1, int(np.ceil(y0 / ds)) + 1
    cx1, cy1 = int(np.floor(x1 / ds)) - 1, int(np.floor(y1 / ds)) - 1
    valid[cy0:cy1, cx0:cx1] = 255
    maps = an.maps()
    # decoded pixel centre u -> source (u + 0.5) ds -> world through the frame's map -> texel centre (w - x0) / scale - 0.5
    to_src = np.float64([[ds, 0, 0.5 * ds], [0, ds, 0.5 * ds], [0, 0, 1]])
    to_tex = np.float64([[1 / scale, 0, -wx0 / scale - 0.5], [0, 1 / scale, -wy0 / scale - 0.5], [0, 0, 1]])
    warps = [(to_tex @ maps[rel] @ to_src, rgb) for rel, rgb in kept]
    k = len(warps)
    out = np.zeros((th, tw, 3), np.uint8)
    cover = np.zeros((th, tw), bool)
    multi = np.zeros((th, tw), bool)                              # seen by 2+ frames
    split = np.zeros((th, tw), bool)                              # ... that disagree
    # the first frame's picture in texels (the side share's reference, and where the representative frame goes)
    ix0, iy0 = int(np.floor((x0 - wx0) / scale)), int(np.floor((y0 - wy0) / scale))
    ix1, iy1 = int(np.ceil((x1 - wx0) / scale)), int(np.ceil((y1 - wy0) / scale))
    inside = np.zeros_like(cover)
    inside[max(0, iy0):max(0, iy1), max(0, ix0):max(0, ix1)] = True
    dev_sum, dev_n = np.zeros(k), np.zeros(k)                     # each frame's |brightness - the vote's| inside
    luma_w = np.array([0.299, 0.587, 0.114], np.float32)
    for s0 in range(0, th, STRIP):
        s1 = min(th, s0 + STRIP)
        rgbs = np.zeros((k, s1 - s0, tw, 3), np.uint8)
        key = np.full((k, s1 - s0, tw), np.inf, np.float32)      # each frame's brightness there; inf: not seen
        shift = np.float64([[1, 0, 0], [0, 1, -s0], [0, 0, 1]])
        for j, (m, rgb) in enumerate(warps):
            m2 = shift @ m
            img = cv2.warpPerspective(rgb, m2, (tw, s1 - s0), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT,
                                      borderValue=0)
            ok = cv2.warpPerspective(valid, m2, (tw, s1 - s0), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT,
                                     borderValue=0) >= 255
            rgbs[j] = img
            key[j][ok] = (img.astype(np.float32) @ luma_w)[ok]
        count = np.isfinite(key).sum(axis=0)                      # frames that saw each texel
        order = np.argsort(key, axis=0)                           # unseen (inf) last
        pick = np.take_along_axis(order, (np.maximum(count - 1, 0) // 2)[None], axis=0)[0]   # the median-bright frame
        med = np.take_along_axis(rgbs, pick[None, :, :, None].repeat(3, axis=3), axis=0)[0]
        seen = count > 0
        picked = np.take_along_axis(key, pick[None], axis=0)      # the picked frame's brightness
        with np.errstate(invalid="ignore"):                       # inf - inf where nothing saw the texel
            dev = np.abs(key - picked)
            agree = (dev <= AGREE_CODES).sum(axis=0)
        multi[s0:s1] = count >= 2
        split[s0:s1] = (count >= 2) & (agree < AGREE_SHARE * count)
        out[s0:s1][seen] = med[seen]
        cover[s0:s1] = seen
        ins = inside[s0:s1]
        if ins.any():
            seen_j = np.isfinite(key[:, ins])                     # (k, texels inside this strip)
            dev_sum += np.where(seen_j, dev[:, ins], 0.0).sum(axis=1)
            dev_n += seen_j.sum(axis=1)
    # inside the first picture: the representative frame (nearest the vote there among those that see most of it)
    n_in = int((inside & cover).sum())
    fits = [j for j in range(k) if n_in and dev_n[j] >= REPR_COVER * n_in]
    if fits:
        j = min(fits, key=lambda i: dev_sum[i] / dev_n[i])
        m_r, rgb_r = warps[j]
        img = cv2.warpPerspective(rgb_r, m_r, (tw, th), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
        own = (cv2.warpPerspective(valid, m_r, (tw, th), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT) >= 255)
        sel = own & inside
        out[sel] = img[sel]
    judged = int((multi & ~inside).sum())
    disagree = float((split & ~inside).sum()) / judged if judged >= MIN_JUDGED else 0.0
    # outside texels only: a character moving inside the picture must not take the real pixels next to the seam
    out_f = (~inside).astype(np.float32)
    split_r = cv2.GaussianBlur((split & ~inside).astype(np.float32), (0, 0), REGION_SIGMA)
    multi_r = cv2.GaussianBlur((multi & ~inside).astype(np.float32), (0, 0), REGION_SIGMA)
    area_r = cv2.GaussianBlur(out_f, (0, 0), REGION_SIGMA)       # the outside's own weight there (near the seam < 1)
    doubt = (cover & ~inside & (multi_r >= REGION_MIN_MULTI * area_r)
             & (split_r >= REGION_UNSURE * np.maximum(multi_r, 1e-6)))
    cover = cover & ~doubt
    side_area = max(1, int((~inside).sum()))
    share = float((cover & ~inside).sum()) / side_area
    return Mosaic(world_x0=wx0, world_y0=wy0, scale=scale, rgb=out, coverage=cover, side_share=share, unsure=doubt,
                  disagree=disagree)
