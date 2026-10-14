"""Disk cache of the live worker: what was made once is never made again, so re-watching and seeking back are served at
once, across worker runs. One folder per film, named by ADR 0027's film key (a renamed or moved copy finds its cache):

    <root>/<film key folder>/
        analysis/<first>-<n>-<akey>.npz     a shot's analysis (ShotAnalysis.arrays: camera path, plan, gains, kept offsets)
        patches/<first>-<n>-<pkey>.npz      its patch page (structure.Page: exit strips, compressed; "none" = no patches)
        mosaic/<first>-<n>-<mkey>.png       its mosaic: the real pixels (RGB), alpha 255 covered, 64 seen but unsure, 0 unseen
        mosaic/<first>-<n>-<mkey>.json      world_x0, world_y0, scale, side_share
        canvas/<first>-<n>-t<tier>-<rkey>.png / .json   a canvas (RGBA as in the store) and its fields
Shots are named by their frame span, not by row: rows are per run when there is no content model. Keys hash everything
that changes the result: gkey = the geometry version (camera path, kept frames, decode) and crop, for set features and
mosaics; pkey = gkey's version plus the page version (exit strips, their gain rule) and crop, for patch pages; akey = the
whole analysis version (both plus the step planner's cadence) and crop; mkey = gkey, mosaic version, margins and scale;
rkey = mkey and the tier's own recipe (the painter's for T2). A new cadence, strip rule or gain rule thus analyses again
but keeps every mosaic and painted plate. Mosaic JSON: world_x0, world_y0, scale, side_share and disagree (the share of
multi-seen texels outside the first picture whose frames disagree). Files are written under a temporary name and
renamed, so a crash never leaves half a file under a real name.
"""
from __future__ import annotations

import glob
import hashlib
import json
import math
import os
from typing import Optional

import cv2
import numpy as np

from .analyse_v0 import ANALYSIS_VERSION, GEOMETRY_VERSION, PAGE_VERSION, ShotAnalysis
from .mosaic_t1 import MOSAIC_VERSION, Mosaic
from .structure import Page

# The room's side widths are rounded up to this step, and canvases (and their cache keys) are made for the rounded widths:
# a windowed player, or a sub-pixel change in its measurement, usually finds the canvases cached for the same room (a width
# within a pixel of a step can still round either way).
MARGIN_STEP_PX = 32.0


def room_margins(values) -> tuple:
    """The room's side widths (L, T, R, B source px) rounded up to MARGIN_STEP_PX. A width of 1 px or less, or one that
    is not a number, is no side, as in the player's fill."""
    out = []
    for v in values:
        v = float(v)
        out.append(math.ceil(v / MARGIN_STEP_PX - 1e-6) * MARGIN_STEP_PX if math.isfinite(v) and v > 1.0 else 0.0)
    return tuple(out)


def tiling(on_disk: dict, first: int, end: int) -> Optional[list]:
    """(first, n) of cached shots (on_disk: first frame -> set of n) that tile [first, end) in order, as hidden cuts split
    a span; longer parts are tried first. None if none do."""
    if first == end:
        return []
    for n in sorted((n for n in on_disk.get(first, ()) if 0 < n <= end - first), reverse=True):
        rest = tiling(on_disk, first + n, end)
        if rest is not None:
            return [(first, n)] + rest
    return None


def key_hash(*parts) -> str:
    return hashlib.sha1(json.dumps(parts, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]


def recipe_u64(key: str) -> int:
    """The store's 64-bit recipe field for a cache key."""
    return int(key[:16], 16) & 0x7FFFFFFFFFFFFFFF


def _write_png(path: str, img: np.ndarray):
    tmp = path + ".tmp.png"
    bgr = cv2.cvtColor(img, cv2.COLOR_RGBA2BGRA if img.shape[2] == 4 else cv2.COLOR_RGB2BGR)
    if not cv2.imwrite(tmp, bgr, [cv2.IMWRITE_PNG_COMPRESSION, 1]):
        raise OSError(f"cannot write {tmp}")
    os.replace(tmp, path)


def _read_png(path: str) -> Optional[np.ndarray]:
    img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if img is None:
        return None
    return cv2.cvtColor(img, cv2.COLOR_BGRA2RGBA if img.shape[2] == 4 else cv2.COLOR_BGR2RGB)


def _write_json(path: str, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1)
    os.replace(tmp, path)


class SideCache:
    def __init__(self, root: str, film_key_folder: str):
        self.dir = os.path.join(root, film_key_folder)
        for sub in ("analysis", "mosaic", "canvas"):
            os.makedirs(os.path.join(self.dir, sub), exist_ok=True)

    # ---- analysis ------------------------------------------------------------------------------------------------------
    @staticmethod
    def analysis_key(crop) -> str:
        return key_hash(ANALYSIS_VERSION, [round(float(v), 3) for v in crop])

    @staticmethod
    def geometry_key(crop) -> str:
        return key_hash(GEOMETRY_VERSION, [round(float(v), 3) for v in crop])

    @staticmethod
    def page_key(crop) -> str:
        return key_hash(GEOMETRY_VERSION, PAGE_VERSION, [round(float(v), 3) for v in crop])

    def _analysis_path(self, first: int, n: int, crop) -> str:
        return os.path.join(self.dir, "analysis", f"{first}-{n}-{self.analysis_key(crop)}.npz")

    def save_analysis(self, an: ShotAnalysis):
        path = self._analysis_path(an.first, an.n, an.crop)
        tmp = path + ".tmp.npz"
        np.savez(tmp, **an.arrays())
        os.replace(tmp, path)

    def load_analysis(self, first: int, n: int, crop) -> Optional[ShotAnalysis]:
        path = self._analysis_path(first, n, crop)
        if not os.path.isfile(path):
            return None
        try:
            with np.load(path) as z:
                return ShotAnalysis.from_arrays(z)
        except Exception:                                         # damaged (a truncated or empty file): not cached
            return None

    def _page_path(self, first: int, n: int, crop) -> str:
        return os.path.join(self.dir, "patches", f"{first}-{n}-{self.page_key(crop)}.npz")

    def page_state(self, first: int, n: int, crop) -> Optional[bool]:
        """None: nothing cached for the shot; False: cached as having no patches; True: a page is cached (not loaded)."""
        path = self._page_path(first, n, crop)
        if not os.path.isfile(path):
            return None
        try:
            with np.load(path) as z:
                return "none" not in z.files
        except Exception:
            return None

    def save_page(self, first: int, n: int, crop, page: Optional[Page]):
        """A shot's patch page (None: the shot has no patches; saved too, so a warm start knows)."""
        path = self._page_path(first, n, crop)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp.npz"
        np.savez_compressed(tmp, **(page.arrays() if page is not None else {"none": np.zeros(0)}))   # mostly alpha 0
        os.replace(tmp, path)

    def load_page(self, first: int, n: int, crop) -> tuple:
        """(found, page or None): found is False when nothing (or a damaged file) is cached for the shot."""
        path = self._page_path(first, n, crop)
        if not os.path.isfile(path):
            return False, None
        try:
            with np.load(path) as z:
                return True, (None if "none" in z.files else Page.from_arrays(z))
        except Exception:
            return False, None

    def cached_spans(self, crop_at=None) -> list:
        """(first, n) of every analysed shot on disk. With crop_at (first frame -> crop): only those load_analysis finds,
        made with the current analysis version and that crop."""
        out = []
        for p in glob.glob(os.path.join(self.dir, "analysis", "*.npz")):
            parts = os.path.basename(p)[:-4].split("-")
            if len(parts) != 3 or not (parts[0].isdigit() and parts[1].isdigit()):
                continue
            first, n = int(parts[0]), int(parts[1])
            if crop_at is None or parts[2] == self.analysis_key(crop_at(first)):
                out.append((first, n))
        return sorted(set(out))

    # ---- mosaics -------------------------------------------------------------------------------------------------------
    @classmethod
    def mosaic_key(cls, crop, margins, scale: float) -> str:
        return key_hash(cls.geometry_key(crop), MOSAIC_VERSION, [round(float(v), 1) for v in margins], round(scale, 4))

    def _mosaic_base(self, first: int, n: int, mkey: str) -> str:
        return os.path.join(self.dir, "mosaic", f"{first}-{n}-{mkey}")

    def save_mosaic(self, first: int, n: int, mkey: str, m: Mosaic):
        """Alpha: 255 covered, 64 seen but unsure (mosaic_t1's reliability rule), 0 never seen."""
        base = self._mosaic_base(first, n, mkey)
        unsure = m.unsure if m.unsure is not None else np.zeros_like(m.coverage)
        rgba = np.dstack([m.rgb, np.where(m.coverage, 255, np.where(unsure, 64, 0)).astype(np.uint8)])
        _write_png(base + ".png", rgba)
        _write_json(base + ".json", {"world_x0": m.world_x0, "world_y0": m.world_y0, "scale": m.scale,
                                     "side_share": m.side_share, "disagree": m.disagree})

    def mosaic_meta(self, first: int, n: int, mkey: str) -> Optional[dict]:
        """A cached mosaic's fields without its pixels (world_x0, world_y0, scale, side_share), or None."""
        base = self._mosaic_base(first, n, mkey)
        if not (os.path.isfile(base + ".png") and os.path.isfile(base + ".json")):
            return None
        try:
            with open(base + ".json", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def load_mosaic(self, first: int, n: int, mkey: str) -> Optional[Mosaic]:
        base = self._mosaic_base(first, n, mkey)
        if not (os.path.isfile(base + ".png") and os.path.isfile(base + ".json")):
            return None
        rgba = _read_png(base + ".png")
        if rgba is None or rgba.shape[2] != 4:
            return None
        with open(base + ".json", encoding="utf-8") as f:
            meta = json.load(f)
        a = rgba[..., 3]
        return Mosaic(world_x0=meta["world_x0"], world_y0=meta["world_y0"], scale=meta["scale"],
                      rgb=np.ascontiguousarray(rgba[..., :3]), coverage=a > 127, side_share=meta["side_share"],
                      unsure=(a > 0) & (a <= 127), disagree=float(meta.get("disagree", 0.0)))

    # ---- set registration (set_atlas.py) ----------------------------------------------------------------------------------
    def _features_path(self, first: int, n: int, crop, version: str) -> str:
        return os.path.join(self.dir, "features", f"{first}-{n}-{key_hash(self.geometry_key(crop), version)}.npz")

    def save_features(self, first: int, n: int, crop, version: str, arrays: dict):
        path = self._features_path(first, n, crop, version)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp.npz"
        np.savez(tmp, **arrays)
        os.replace(tmp, path)

    def load_features(self, first: int, n: int, crop, version: str) -> Optional[dict]:
        path = self._features_path(first, n, crop, version)
        if not os.path.isfile(path):
            return None
        try:
            with np.load(path) as z:
                return {k: z[k] for k in z.files}
        except Exception:                                         # damaged: registered again from a fresh analysis
            return None

    @property
    def sets_path(self) -> str:
        return os.path.join(self.dir, "sets.json")

    # ---- canvases ------------------------------------------------------------------------------------------------------
    def _canvas_base(self, first: int, n: int, tier: int, rkey: str) -> str:
        return os.path.join(self.dir, "canvas", f"{first}-{n}-t{tier}-{rkey}")

    def save_canvas(self, meta: dict, rgba: np.ndarray):
        """meta: first, n, tier, rkey, world_x0, world_y0, scale (and anything else worth keeping)."""
        base = self._canvas_base(meta["first"], meta["n"], meta["tier"], meta["rkey"])
        _write_png(base + ".png", rgba)
        _write_json(base + ".json", meta)

    def canvas_meta(self, first: int, n: int, tier: int, rkey: str) -> Optional[dict]:
        """The fields of a cached canvas, or None when it was never made."""
        base = self._canvas_base(first, n, tier, rkey)
        if not (os.path.isfile(base + ".json") and os.path.isfile(base + ".png")):
            return None
        try:
            with open(base + ".json", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def load_canvas(self, meta: dict) -> Optional[np.ndarray]:
        img = _read_png(self._canvas_base(meta["first"], meta["n"], meta["tier"], meta["rkey"]) + ".png")
        return img if img is not None and img.ndim == 3 and img.shape[2] == 4 else None

    def canvas_metas(self) -> list:
        out = []
        for p in glob.glob(os.path.join(self.dir, "canvas", "*.json")):
            try:
                with open(p, encoding="utf-8") as f:
                    out.append(json.load(f))
            except (OSError, ValueError):
                pass
        return out
