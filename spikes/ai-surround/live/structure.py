"""Structure path (role: render ahead, T3): what the camera carries into the sides between drawings, as patches that
change a shot's world only when the picture changes. Phase 4 of plans/261013-0718-ai-surround-live-path.

World lattice: texel (i, j) of a shot's patch grid covers world px [i s, (i + 1) s) x [j s, (j + 1) s) (s = SCALE world
px per texel, the canvases' half resolution); world px are continuous source px of the shot's first frame (pixel u's
centre at (u + 0.5) per source px, mosaic_t1's convention). Every patch sits on this lattice, so packing never resamples.

Exit strips (persistent patches): when a new drawing's picture no longer covers texels the previous frame's picture
covered, those texels became side, and the picture's last view of them (the previous frame, warped by its map) is what
they show from this frame on, until the shot ends or the picture covers them again. A pan leaves the real pixels it
passes behind, moving with the world (X1's harness wrote the picture into its world the same way). Content is
gain-normalised per side (divided by the side's gain at the frame it was seen in, in linear light) because the player
multiplies the whole side by the gain of the frame on screen: a flash never burns into the sides. A drawing with one
edge's strip larger than MAX_STRIP_SHARE of the picture leaves no strip at all: a cut the detector missed, a map the
model got wrong (wrong for all four edges), or a whip pan whose sides need a generator, not a carry; such strips were
3 % of JJK's and 29 % of its pages' area, and filled pages of 16 MB.

Overlay patches (a frame range, shown over the rest) are for trails of characters that leave on their own; the page
format carries them, the planner does not make them yet.

Patch page (side-store schema 2): the shot's patches packed in shelves into one RGBA8 texture, plus a table (frame,
until, kind, destination rect in the page grid, source rect in the texture) and the page grid's map (set world px ->
page grid texel index). The player copies persistent patches into its accumulate texture from their frame on and draws
the overlays in their range; it composites overlay over accumulate over the base canvas.

The harness's projective world (carry_strips) is X1b's world carry with 3x3 frame maps: the X1c versions use it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import cv2
import numpy as np

SCALE = 2.0                         # world px per texel: half the film's resolution, like the canvases
PERSISTENT, OVERLAY = 1, 2
PAGE_W = 4096                       # packed texture width
MAX_GRID = 16384                    # page grid texels per axis (the player's limit)
MIN_STRIP_TEXELS = 4
MAX_STRIP_SHARE = 0.10              # one edge's strip over this share of the picture's texels is no exit: a cut, a wild map
                                    # or a whip pan (JJK: the median strip is 0.4 %; over 10 %: 3 % of strips, 29 % of area)
_LIN = ((np.arange(256) / 255.0) ** 2.4).astype(np.float32)        # code -> linear light (analyse_v0's)


def lattice_map(s: float = SCALE) -> np.ndarray:
    """world px -> lattice texel index (texel centres on integers)."""
    return np.float64([[1 / s, 0, -0.5], [0, 1 / s, -0.5], [0, 0, 1]])


def pixel_map(ds: float, ox: float = 0.0, oy: float = 0.0) -> np.ndarray:
    """pixel index of an image at ds source px per pixel whose pixel 0 starts at source (ox, oy) -> continuous source px."""
    return np.float64([[ds, 0, ox + 0.5 * ds], [0, ds, oy + 0.5 * ds], [0, 0, 1]])


@dataclass
class Patch:
    frame: int                      # film frame from which it applies
    kind: int                       # PERSISTENT or OVERLAY
    i0: int                         # lattice texel of its top left corner
    j0: int
    rgba: np.ndarray                # (h, w, 4) uint8, the film's own encoded RGB, coverage in alpha
    side: int = -1                  # 0 left, 1 top, 2 right, 3 bottom (the side it lies on), -1 unknown
    until: int = 0xFFFFFFFF         # overlay: last frame (inclusive)


def _quad(W: np.ndarray, crop: tuple, inset: float) -> np.ndarray:
    """The picture's crop (source px, inset by `inset` px) through W, in lattice texel coordinates."""
    x0, y0, x1, y1 = crop
    c = np.float64([[x0 + inset, y0 + inset], [x1 - inset, y0 + inset], [x1 - inset, y1 - inset], [x0 + inset, y1 - inset]])
    p = np.hstack([c, np.ones((4, 1))]) @ (lattice_map() @ W).T
    return p[:, :2] / p[:, 2:3]


EDGE_SIDE = (1, 2, 3, 0)            # _quad's edges top, right, bottom, left -> side (0 left, 1 top, 2 right, 3 bottom)


def _edge_of(quad_now: np.ndarray, pts: np.ndarray) -> np.ndarray:
    """For each point (lattice texel centres) outside the picture's quad as it is now, the edge it lies farthest beyond:
    0 top, 1 right, 2 bottom, 3 left (the quad's corners run clockwise in image coordinates, so outside is a negative
    cross product)."""
    out = np.empty((len(pts), 4))
    for e in range(4):
        a, b = quad_now[e], quad_now[(e + 1) % 4]
        d = b - a
        out[:, e] = -(d[0] * (pts[:, 1] - a[1]) - d[1] * (pts[:, 0] - a[0])) / max(np.hypot(*d), 1e-9)
    return np.argmax(out, axis=1)


class ShotStructure:
    """One shot's exit strips, fed new drawing by new drawing (the analyser's frames, the camera model's maps)."""

    def __init__(self, crop: tuple, ds: float):
        self.crop, self.ds = crop, ds                               # source px crop; source px per decoded px
        self.prev = None                                            # (frame, decoded rgb, W source px)
        self.patches: list = []

    def add(self, frame: int, rgb: np.ndarray, W: np.ndarray, new_drawing: bool):
        """rgb: the whole decoded frame (ds source px per pixel); W: the frame's map, continuous source px."""
        if self.prev is not None and new_drawing and np.abs(W - self.prev[2]).max() > 1e-9:
            self._strip(frame, W)
        if self.prev is None or new_drawing or np.abs(W - self.prev[2]).max() > 1e-9:
            self.prev = (frame, rgb, W)

    def _strip(self, frame: int, W: np.ndarray):
        _, rgb_prev, W_prev = self.prev
        # one decoded pixel in from the crop's edge (mosaic_t1's rule): a crop edge inside a decoded pixel (letterboxed
        # shots) would otherwise blend the bar into the strip's outer texels
        q_prev = _quad(W_prev, self.crop, self.ds)
        q_now = _quad(W, self.crop, 0.0)
        lo = np.floor(np.minimum(q_prev.min(0), q_now.min(0))).astype(int) - 1
        hi = np.ceil(np.maximum(q_prev.max(0), q_now.max(0))).astype(int) + 2
        size = (int(hi[0] - lo[0]), int(hi[1] - lo[1]))
        if size[0] <= 0 or size[1] <= 0 or size[0] * size[1] > 64 << 20:
            return
        m_prev = np.zeros((size[1], size[0]), np.uint8)
        m_now = np.zeros_like(m_prev)
        cv2.fillConvexPoly(m_prev, np.round((q_prev - lo) * 16).astype(np.int32), 1, cv2.LINE_8, 4)
        cv2.fillConvexPoly(m_now, np.round((q_now - lo) * 16).astype(np.int32), 1, cv2.LINE_8, 4)
        strip = (m_prev > 0) & (m_now == 0)
        if strip.sum() < MIN_STRIP_TEXELS:
            return
        # one patch per edge of the picture the texels left it across: a pan leaves one thin band, a zoom or roll four
        # (one box around the whole ring would be the whole picture: a page full after a few drawings)
        ys, xs = np.nonzero(strip)
        edge = _edge_of(q_now - lo, np.stack([xs, ys], 1).astype(np.float64))   # texel centres (cv2's rasterising)
        to_tex = lattice_map() @ W_prev @ pixel_map(self.ds)      # decoded pixel index -> lattice texel index
        x0, y0, x1, y1 = self.crop
        most = MAX_STRIP_SHARE * (x1 - x0) * (y1 - y0) / SCALE ** 2
        if max(int((edge == e).sum()) for e in range(4)) > most:  # one map for all four edges: none of its strips
            return
        for e in range(4):
            sel = edge == e
            if sel.sum() < MIN_STRIP_TEXELS:
                continue
            ex, ey = xs[sel], ys[sel]
            bx0, by0, bx1, by1 = ex.min(), ey.min(), ex.max() + 1, ey.max() + 1
            mask = np.zeros((by1 - by0, bx1 - bx0), bool)
            mask[ey - by0, ex - bx0] = True
            T = np.float64([[1, 0, -(lo[0] + bx0)], [0, 1, -(lo[1] + by0)], [0, 0, 1]]) @ to_tex
            img = cv2.warpPerspective(rgb_prev, T, (int(bx1 - bx0), int(by1 - by0)), flags=cv2.INTER_LINEAR,
                                      borderMode=cv2.BORDER_REPLICATE)
            rgba = np.zeros(img.shape[:2] + (4,), np.uint8)
            rgba[..., :3] = img
            rgba[..., 3] = np.where(mask, 255, 0).astype(np.uint8)
            self.patches.append(Patch(frame=frame, kind=PERSISTENT, i0=int(lo[0] + bx0), j0=int(lo[1] + by0),
                                      rgba=rgba, side=EDGE_SIDE[e]))

    def normalise_gains(self, gains: np.ndarray, first: int):
        """Divides each persistent patch by its side's gain (linear light) at the frame its content was seen in (the frame
        before the one it applies from). gains: (n, 4) per frame of the shot, left/top/right/bottom."""
        for p in self.patches:
            if p.side < 0:
                continue
            g = float(gains[max(0, p.frame - 1 - first), p.side])
            if abs(g - 1.0) < 1e-3 or g <= 0:
                continue
            lin = _LIN[p.rgba[..., :3]] / g
            p.rgba[..., :3] = np.clip(np.round(255.0 * np.power(np.clip(lin, 0, 1), 1 / 2.4)), 0, 255).astype(np.uint8)


ENTRY_FIELDS = ("frame", "until", "kind", "side", "i0", "j0", "w", "h", "sx", "sy")


@dataclass
class Page:
    """A shot's patches packed for the side store (schema 2) and the disk cache: entries[k] = (frame, until, kind, side,
    i0, j0, w, h, sx, sy): lattice rect (i0, j0, w, h), source corner (sx, sy) in the packed texture."""
    grid_i0: int                    # lattice texel of the page grid's top left corner
    grid_j0: int
    grid_w: int
    grid_h: int
    texture: np.ndarray             # (h, w, 4) uint8
    entries: np.ndarray = field(default_factory=lambda: np.zeros((0, len(ENTRY_FIELDS)), np.int64))

    def grid_map(self, T_set: Optional[np.ndarray] = None) -> np.ndarray:
        """set world px -> page grid texel coordinate, texel i spanning [i, i + 1) (the store's convention; T_set: 3x3
        shot world -> set world, None: its own set)."""
        G = np.float64([[1 / SCALE, 0, -self.grid_i0], [0, 1 / SCALE, -self.grid_j0], [0, 0, 1]])
        return G if T_set is None else G @ np.linalg.inv(T_set)

    def store_table(self, patch_dt: np.dtype) -> np.ndarray:
        """The side store's patch table (patch_dt: side_store.PATCH_DT): destinations relative to the page grid."""
        e = self.entries
        t = np.zeros(len(e), patch_dt)
        t["frame"], t["until"], t["kind"] = e[:, 0], e[:, 1], e[:, 2]
        t["side"] = np.where(e[:, 3] < 0, 255, e[:, 3])
        t["dst_x"], t["dst_y"] = e[:, 4] - self.grid_i0, e[:, 5] - self.grid_j0
        t["w"], t["h"], t["src_x"], t["src_y"] = e[:, 6], e[:, 7], e[:, 8], e[:, 9]
        return t

    def arrays(self) -> dict:
        return {"grid": np.int64([self.grid_i0, self.grid_j0, self.grid_w, self.grid_h]), "texture": self.texture,
                "entries": self.entries}

    @classmethod
    def from_arrays(cls, z) -> "Page":
        g = [int(v) for v in z["grid"]]
        return cls(grid_i0=g[0], grid_j0=g[1], grid_w=g[2], grid_h=g[3], texture=np.asarray(z["texture"], np.uint8),
                   entries=np.asarray(z["entries"], np.int64))


SHELF_RATIO = 0.5                   # a patch under this share of its shelf's height starts a new shelf


def _shelves(fit: list, limit: int, ratio: float) -> tuple:
    """((patch, x, y) placements, texture height, texture width): shelves of at most `limit` texels, tallest first, a
    new shelf also when the next patch is under `ratio` of the shelf's height."""
    placed, x, y, shelf, width = [], 0, 0, 0, 0
    for p in sorted(fit, key=lambda q: -q.rgba.shape[0]):
        h, w = p.rgba.shape[:2]
        if x and (x + w > limit or h < ratio * shelf):
            x, y, shelf = 0, y + shelf, 0
        placed.append((p, x, y))
        x += w
        width = max(width, x)
        shelf = max(shelf, h)
    return placed, y + shelf, width


def _layout(fit: list, page_w: int) -> tuple:
    """The smallest of three shelf layouts (placements, height, width): shelves about as wide as the square root of the
    patches' area with height classes (SHELF_RATIO: a pan's tall left and right strips and its wide top and bottom ones
    then share no shelf, and the widest shelf does not pad the rest), the same at the page's whole width, and plain
    tallest-first at the page's width (the best when one class fills the page). None fits every page best."""
    area = sum(p.rgba.shape[0] * p.rgba.shape[1] for p in fit)
    narrow = min(page_w, max(max(p.rgba.shape[1] for p in fit), int(np.ceil(np.sqrt(1.3 * area)))))
    tries = [_shelves(fit, narrow, SHELF_RATIO), _shelves(fit, page_w, SHELF_RATIO), _shelves(fit, page_w, 0.0)]
    return min(tries, key=lambda t: t[1] * t[2])


def pack(patches: list, max_bytes: int, page_w: int = PAGE_W) -> Optional[Page]:
    """Packs patches into one texture of at most max_bytes whose page grid stays within MAX_GRID: the earliest that fit
    (the latest are dropped; a patch wider than the page is skipped), in shelves (_layout; in film order they had
    filled 5 % of their pages and a third of the pages ran out), with the table in film order (the player applies
    patches in table order)."""
    fit, area = [], 0
    for p in patches:                                             # film order: the earliest patches are kept
        h, w = p.rgba.shape[:2]
        if w > page_w or w > 0xFFFF or h > 0xFFFF:
            continue
        if 4 * (area + w * h) > max_bytes:                        # a lower bound of the texture this patch needs
            break
        fit.append(p)
        area += w * h
    while fit:
        i0, j0 = min(p.i0 for p in fit), min(p.j0 for p in fit)
        i1, j1 = max(p.i0 + p.rgba.shape[1] for p in fit), max(p.j0 + p.rgba.shape[0] for p in fit)
        if i1 - i0 <= MAX_GRID and j1 - j0 <= MAX_GRID:          # the player's limit (ai_store_read_page)
            placed, th, tw = _layout(fit, page_w)
            if th * tw * 4 <= max_bytes:
                break
        fit = fit[:max(0, len(fit) - max(1, len(fit) // 8))]      # over: give up the latest eighth and pack again
    if not fit:
        return None
    tex = np.zeros((th, tw, 4), np.uint8)
    at = {}
    for p, px, py in placed:
        h, w = p.rgba.shape[:2]
        tex[py:py + h, px:px + w] = p.rgba
        at[id(p)] = (px, py)
    rows = [(p.frame, p.until, p.kind, p.side, p.i0, p.j0, p.rgba.shape[1], p.rgba.shape[0]) + at[id(p)] for p in fit]
    return Page(grid_i0=i0, grid_j0=j0, grid_w=i1 - i0, grid_h=j1 - j0, texture=tex, entries=np.int64(rows))


def replay(page: Page, frame: int) -> np.ndarray:
    """The page grid's accumulate layer after every persistent patch up to `frame`, in table order (what the player
    holds then): straight alpha, alpha 0 keeps what is there. For tests."""
    acc = np.zeros((page.grid_h, page.grid_w, 4), np.uint8)
    for f, _, kind, _, i0, j0, w, h, sx, sy in page.entries:
        if kind != PERSISTENT or f > frame:
            continue
        dst = acc[j0 - page.grid_j0:j0 - page.grid_j0 + h, i0 - page.grid_i0:i0 - page.grid_i0 + w]
        src = page.texture[sy:sy + h, sx:sx + w]
        dst[...] = np.where(src[..., 3:4] > 0, src, dst)
    return acc


# ---- the true-sides harness (X1c): a whole strip carried through a projective world, refreshed where told ----------

def carry_strips(strips: list, W: list, refresh: list, gains: list, pic: slice, side_cols: dict, pad: int = 4) -> list:
    """X1b's world carry (true-sides-test._carried_through_world) with 3x3 frame maps. strips: full-res frames of the
    32:9 strip (the picture at columns `pic`, the true sides beside it); W[t]: strip px of frame t -> world px (frame 0's
    strip px, pixel index units); refresh[t]: {side: True / False / (h,) bool rows}: that side's columns (or rows of
    them) taken from the truth at t; gains[t]: {side: [(columns, gain)]} display gains since the last refresh. Every
    update (what the camera brings in from beyond everything known, from the truth: a perfect painter ahead of the
    camera; the real picture; refreshes) goes into the world first, then the sides are read back from it, so refreshed
    and carried frames get the same resampling."""
    h, w = strips[0].shape[:2]
    corners = np.float64([[0, 0], [w, 0], [0, h], [w, h]])
    pts = np.vstack([np.hstack([corners, np.ones((4, 1))]) @ Wt.T for Wt in W])
    pts = pts[:, :2] / pts[:, 2:3]
    x0, y0 = math.floor(pts[:, 0].min()) - pad, math.floor(pts[:, 1].min()) - pad
    x1, y1 = math.ceil(pts[:, 0].max()) + pad, math.ceil(pts[:, 1].max()) + pad
    gw, gh = x1 - x0, y1 - y0
    if gw * gh > 120e6:
        raise ValueError(f"world of {gw}x{gh} px: the camera path is not plausible")
    G = np.float64([[1, 0, -x0], [0, 1, -y0], [0, 0, 1]])
    world = np.zeros((gh, gw, 3), np.float32)
    known = np.zeros((gh, gw), np.uint8)

    def put(img, t, mask):
        M = G @ W[t]
        keep = cv2.warpPerspective(mask.astype(np.uint8), M, (gw, gh), flags=cv2.INTER_NEAREST,
                                   borderMode=cv2.BORDER_CONSTANT) > 0
        world[keep] = cv2.warpPerspective(img, M, (gw, gh), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)[keep]
        known[keep] = 1

    def get(arr, t, interp):
        return cv2.warpPerspective(arr, G @ W[t], (w, h), flags=interp | cv2.WARP_INVERSE_MAP,
                                   borderMode=cv2.BORDER_REPLICATE)

    def cols(c):
        m = np.zeros((h, w), bool)
        m[:, c] = True
        return m

    out = []
    for t, s in enumerate(strips):
        sf = s.astype(np.float32)
        if t > 0:
            fresh = get(known, t, cv2.INTER_NEAREST) == 0
            fresh = cv2.dilate(fresh.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
            fresh[:, pic] = False
            if fresh.any():
                put(sf, t, fresh)
        put(sf, t, cols(pic))
        for name, c in side_cols.items():
            r = True if t == 0 else refresh[t].get(name, False)
            if r is True:
                put(sf, t, cols(c))
            elif r is not False and np.any(r):
                m = np.zeros((h, w), bool)
                m[np.asarray(r, bool), c] = True
                put(sf, t, m)
        canvas = get(world, t, cv2.INTER_LINEAR)
        canvas[:, pic] = sf[:, pic]
        for name in side_cols:
            for c, g in gains[t].get(name, ()):
                if g != 1.0:
                    canvas[:, c] *= g
        out.append(np.clip(canvas, 0, 255).round().astype(np.uint8))
    return out
