"""The live worker's sets (role: analyse + paint, data side): registers each analysed shot into a set (set_atlas.py), makes
the set's T1 canvas from its members' mosaics, hands the painter the set's atlas, and keeps the sets in the disk cache so a
re-watch finds the same sets and their plates.

A shot alone is its own set (id = its row) and keeps phase 2's cache keys (T1: key_hash(mkey, "t1"); T2: the painter's
plate key over its mosaic key), so caches made before sets still serve it. A set of several shots has its own keys: T1
over its members' spans, mosaic keys and transforms; T2 over that atlas key and the painter's recipe (the registry keeps
the plate's key and extent, and later members join only if that plate covers them).
"""
from __future__ import annotations

import collections
import threading
from typing import Optional

import numpy as np

from . import mosaic_t1, set_atlas, side_cache
from .analyse_v0 import ShotAnalysis
from .mosaic_t1 import Mosaic
from .worker_state import TIER_MOSAIC, TIER_PLATE, WorkerState

IDENTITY = set_atlas.IDENTITY
SEAM_BAND = 0.12                                                  # the plate check's bands: this share of the
                                                                  # picture's width inside each side edge
EDGE_SHARE = 0.02                                                 # a person box this close to the crop's left or
                                                                  # right edge: a character cut by the seam


def person_boxes_of(film):
    """frame -> [(x0, y0, x1, y1) source px] of the content model's person / head / face boxes (sampled every 12th
    frame; the nearest sample inside the shot), or None without a content model."""
    content = getattr(film, "content", None)
    if content is None or not film.spans:
        return None
    by_frame = {}
    try:
        for span in film.spans:
            for tr in content.subjects_of_shot(span.shot_id).get("tracks", []):
                for b in tr.get("boxes", []):
                    by_frame.setdefault(int(b["frame"]), []).append((b["x"], b["y"], b["x"] + b["w"], b["y"] + b["h"]))
    except Exception:                                             # no subjects.json: no masks
        return None
    frames = np.array(sorted(by_frame), np.int64)
    if not len(frames):
        return None

    def boxes(frame: int):
        span = film.span_of_frame(frame)
        i = int(np.searchsorted(frames, frame))
        best = None
        for j in (i - 1, i):
            if 0 <= j < len(frames) and (best is None or abs(frames[j] - frame) < abs(best - frame)):
                if span is None or span.first <= frames[j] < span.first + span.n:
                    best = int(frames[j])
        return by_frame.get(best, []) if best is not None and abs(best - frame) <= 12 else []
    return boxes


def edge_person(person_boxes, first: int, n: int, crop) -> bool:
    """Is a character cut by the picture's left or right edge in this shot (person_boxes(frame): the content model's
    person / head / face boxes, sampled every 12th frame)? Its plate continues that character into the sides, which no
    other shot shows: such a shot neither joins a set nor is joined."""
    if person_boxes is None:
        return False
    x0, _, x1, _ = crop
    m = EDGE_SHARE * (x1 - x0)
    for fr in list(range(first, first + n, 12)) + [first + n - 1]:
        for bx0, _, bx1, _ in person_boxes(fr):
            if bx0 <= x0 + m or bx1 >= x1 - m:
                return True
    return False


def plate_band_agreement(plate_rgba: np.ndarray, meta: dict, mine: Mosaic, crop, t) -> dict:
    """A set's painted plate against a joining shot's own pixels in bands inside its first frame's picture, along the
    left and right edges (its sides continue the picture there). The mosaics' agreement only sees where both shots
    saw the place; a plate painted around another shot's picture is the painter's invention wherever that shot saw
    nothing, and must still continue this one. t: this shot's world px -> the set's (its first frame's source px are
    its world)."""
    plate = Mosaic(world_x0=float(meta["world_x0"]), world_y0=float(meta["world_y0"]), scale=float(meta["scale"]),
                   rgb=np.ascontiguousarray(plate_rgba[..., :3]), coverage=plate_rgba[..., 3] > 0, side_share=1.0)
    x0, y0, x1, y1 = crop
    band = SEAM_BAND * (x1 - x0)
    xs = mine.world_x0 + (np.arange(mine.tex_w) + 0.5) * mine.scale
    ys = mine.world_y0 + (np.arange(mine.tex_h) + 0.5) * mine.scale
    cols = ((xs >= x0) & (xs <= x0 + band)) | ((xs >= x1 - band) & (xs <= x1))
    rows = (ys >= y0) & (ys <= y1)
    banded = Mosaic(world_x0=mine.world_x0, world_y0=mine.world_y0, scale=mine.scale, rgb=mine.rgb,
                    coverage=mine.coverage & rows[:, None] & cols[None, :], side_share=mine.side_share)
    return set_atlas.agreement(plate, banded, t)


class SetKeeper:
    def __init__(self, film, state: WorkerState, cache: side_cache.SideCache, log, scale: float, slot_bytes: int,
                 enabled: bool = True):
        self.film, self.state, self.cache, self.log = film, state, cache, log
        self.scale, self.slot_bytes, self.enabled = scale, slot_bytes, enabled
        self.reg = set_atlas.SetRegistry()
        self.person_boxes = person_boxes_of(film) if enabled else None
        self.atlases = collections.OrderedDict()                 # set id -> (atlas key, Mosaic): the last few in RAM
        self.lock = threading.Lock()
        self.stats = {"joined": 0, "alone": 0, "no_features": 0, "edge_person": 0, "check_failed": 0}
        self.painting: set = set()                                # sets whose plate the painter is making now

    # ---- identity helpers (rows are per run; spans are not) ---------------------------------------------------------------
    def span_of(self, row: int) -> tuple:
        info = self.state.shots[row]
        return info.first, info.n

    def row_of(self, first: int, n: int) -> Optional[int]:
        row = self.state.by_first.get(first)
        return row if row is not None and self.state.shots[row].n == n else None

    def save(self):
        if self.enabled:
            with self.state.lock:
                set_atlas.save_registry(self.reg, self.cache.sets_path, self.span_of)

    def load(self) -> int:
        """Sets from earlier runs whose members are all known now (the warm start's shots)."""
        if not self.enabled:
            return 0
        with self.state.lock:
            return set_atlas.load_registry(self.reg, self.cache.sets_path, self.row_of)

    # ---- registration ------------------------------------------------------------------------------------------------------
    def edge_person(self, first: int, n: int, crop) -> bool:
        return edge_person(self.person_boxes, first, n, crop)

    def register(self, an: ShotAnalysis, margins) -> tuple:
        """(set id, the frames' transforms into the set's world or None for its own world) of a freshly analysed shot
        (its kept frames still at hand). Its features go to the cache for later runs."""
        wx0, wy0, sc, tw, th = mosaic_t1.canvas_geometry(an, margins, self.scale, self.slot_bytes)
        box = (wx0, wy0, wx0 + tw * sc, wy0 + th * sc)
        if not self.enabled:
            return an.shot_id, None
        known = self.reg.set_id_of(an.shot_id)
        if known is not None:                                     # a set from an earlier run (re-analysed shot)
            t = dict(self.reg.members(known))[an.shot_id]
            return known, None if np.allclose(t, IDENTITY) else set_atlas.frame_transforms(an, t)
        if self.edge_person(an.first, an.n, an.crop):
            self.stats["edge_person"] += 1
            return self.reg.open_alone(an.shot_id, box), None
        start_s = self.film.cut_time(an.first)
        f = set_atlas.features_of(an, self.person_boxes, start_s)
        if f is None:
            self.stats["no_features"] += 1
            return self.reg.open_alone(an.shot_id, box), None
        self.cache.save_features(an.first, an.n, an.crop, set_atlas.FEATURES_VERSION, f.arrays())
        sid, t, best = self.reg.register(f, box, self._join_check(an, margins))
        if sid == an.shot_id:
            self.stats["alone"] += 1
            return sid, None
        self.stats["joined"] += 1
        self.log(f"sets: shot {an.shot_id} joins set {sid} ({best['inliers']} inliers with shot {best['a']}, scale "
                 f"{best['scale']:.2f}, roll {best['angle']:.1f})",
                 event={"event": "set_join", "shot": an.shot_id, "set": sid, "with": best["a"], "inliers": best["inliers"],
                        "scale": round(best["scale"], 3), "angle": round(best["angle"], 2)})
        return sid, set_atlas.frame_transforms(an, t)

    def _join_check(self, an: ShotAnalysis, margins):
        """The registry's verify for this shot: the matched member has no character cut by a side edge, their real
        pixels agree where both saw the place (set_atlas.agreement; this shot's mosaic is made once, from its kept
        frames, when a match first needs it), and a plate the set already has agrees with this shot's picture along
        its side edges (plate_band_agreement). What cannot be judged (a mosaic or plate not on disk yet) does not join."""
        mine = []

        def verify(best: dict) -> bool:
            try:
                return check(best)
            except Exception as e:                                # a failed check is a refused join, never a crash
                self.log(f"sets: shot {an.shot_id} against shot {best['a']}: check failed ({e!r}): no join")
                return False

        def check(best: dict) -> bool:
            if self.reg.set_id_of(best["a"]) in self.painting:     # its plate will not have seen this shot
                return False
            info = self.state.shots.get(best["a"])
            if info is None or info.mosaic_key is None or self.edge_person(info.first, info.n, info.crop):
                return False
            theirs = self.cache.load_mosaic(info.first, info.n, info.mosaic_key)
            if theirs is None:
                return False
            if not mine:
                mine.append(mosaic_t1.build(an, margins, self.scale, self.slot_bytes))
            r = set_atlas.agreement(theirs, mine[0], best["M"])
            if r["ok"]:
                plate = self._plate_check(best, an, mine[0])
                if plate is not None:
                    r = dict(r, plate_cells=plate["cells"], plate_differ=plate["differ"], ok=plate["ok"])
            if not r["ok"]:
                self.stats["check_failed"] += 1
            pl = f", plate {r['plate_differ']:.0%} differ" if "plate_differ" in r else ""
            self.log(f"sets: shot {an.shot_id} against shot {best['a']}: {r['cells']} shared cells, "
                     f"{r['differ']:.0%} differ{pl}: {'join' if r['ok'] else 'no join'}",
                     event={"event": "set_check", "shot": an.shot_id, "with": best["a"], **r})
            return r["ok"]
        return verify

    def _plate_check(self, best: dict, an: ShotAnalysis, mine: Mosaic) -> Optional[dict]:
        """plate_band_agreement against the set's painted plate; None while the set has no plate (the painter will
        then see this shot's pixels too); a plate in the registry but not on disk: {'ok': False, ...}."""
        sid = self.reg.set_id_of(best["a"])
        _, rkey = self.reg.plate_of(sid) if sid is not None else (None, None)
        if rkey is None:
            return None
        key = self.state.shots.get(sid)
        meta = self.cache.canvas_meta(key.first, key.n, TIER_PLATE, rkey) if key is not None else None
        rgba = self.cache.load_canvas(meta) if meta is not None else None
        t_a = dict(self.reg.members(sid)).get(best["a"])
        if rgba is None or t_a is None:
            return {"cells": 0, "differ": 1.0, "ok": False}
        return plate_band_agreement(rgba, meta, mine, an.crop, set_atlas.compose(t_a, best["M"]))

    def add_cached_features(self, row: int):
        """A cached shot's features (an earlier run's), so new shots can join its set."""
        if not self.enabled:
            return
        info = self.state.shots[row]
        z = self.cache.load_features(info.first, info.n, info.crop, set_atlas.FEATURES_VERSION)
        if z is not None:
            self.reg.add_features(set_atlas.ShotFeatures.from_arrays(z, row, info.start_s))

    def adopt_alone(self, row: int, box: tuple):
        """A cached shot that no stored set holds: its own set again."""
        if self.enabled and self.reg.set_id_of(row) is None:
            self.reg.open_alone(row, box)

    # ---- canvases ----------------------------------------------------------------------------------------------------------
    def members(self, set_id: int) -> list:
        """[(row, T)] of the set; a shot that is no registered set's member is its own set."""
        m = self.reg.members(set_id) if self.enabled else []
        return m or [(set_id, IDENTITY)]

    def alone(self, set_id: int) -> bool:
        m = self.members(set_id)
        return len(m) == 1 and np.allclose(m[0][1], IDENTITY)

    def atlas_key(self, set_id: int) -> Optional[str]:
        """The T1 key of the set's atlas as it stands (None while a member has no mosaic yet)."""
        parts = []
        for row, t in self.members(set_id):
            info = self.state.shots.get(row)
            if info is None or info.mosaic_key is None:
                return None
            parts.append((info.first, info.n, info.mosaic_key, np.round(t, 4).tolist()))
        if len(parts) == 1 and np.allclose(self.members(set_id)[0][1], IDENTITY):
            return side_cache.key_hash(parts[0][2], "t1")         # phase 2's key: old caches still serve
        return side_cache.key_hash("t1-set", set_atlas.SET_VERSION, sorted(parts))

    def atlas(self, set_id: int, mosaic_of) -> tuple:
        """(atlas key, the set's real pixels as one Mosaic in its world, side share) or (None, None, 0).
        mosaic_of(row) -> the member's Mosaic (from RAM or the cache)."""
        key = self.atlas_key(set_id)
        if key is None:
            return None, None, 0.0
        with self.lock:
            hit = self.atlases.get(set_id)
            if hit is not None and hit[0] == key:
                self.atlases.move_to_end(set_id)
                return hit
        mos, share = [], 0.0
        for row, t in self.members(set_id):
            m = mosaic_of(row)
            if m is None:
                return None, None, 0.0
            mos.append((m, t))
            share = max(share, m.side_share)
        a = mos[0][0] if len(mos) == 1 and np.allclose(mos[0][1], IDENTITY) else set_atlas.atlas(mos, self.scale,
                                                                                                 self.slot_bytes)
        out = (key, a, share)
        with self.lock:
            self.atlases[set_id] = out
            while len(self.atlases) > 8:
                self.atlases.popitem(last=False)
        return out

    def side_share(self, set_id: int, mosaic_meta_of) -> float:
        """The largest share of side area a member's own real pixels cover (from the mosaics' small JSON files)."""
        share = 0.0
        for row, _ in self.members(set_id):
            mm = mosaic_meta_of(row)
            if mm is not None:
                share = max(share, float(mm["side_share"]))
        return share

    def make_t1(self, set_id: int, mosaic_of, mosaic_meta_of) -> Optional[str]:
        """The set's T1 canvas from its members' real pixels (cached when made before: then only JSON is read); its key,
        or None when no member saw any side pixel (the glow shows until a plate exists)."""
        key = self.atlas_key(set_id)
        if key is None or self.side_share(set_id, mosaic_meta_of) < mosaic_t1.EMPTY_SHARE:
            return None
        info = self.state.shots[set_id]
        meta = self.cache.canvas_meta(info.first, info.n, TIER_MOSAIC, key)
        if meta is not None:
            self.state.add_canvas(set_id, TIER_MOSAIC, key, float(meta["world_x0"]), float(meta["world_y0"]),
                                  float(meta["scale"]), None, meta, int(meta["tex_w"]), int(meta["tex_h"]))
            return key
        key, a, share = self.atlas(set_id, mosaic_of)
        if a is None:
            return None
        rgba = a.canvas_rgba()
        meta = {"first": info.first, "n": info.n, "tier": TIER_MOSAIC, "rkey": key, "world_x0": a.world_x0,
                "world_y0": a.world_y0, "scale": a.scale, "tex_w": a.tex_w, "tex_h": a.tex_h, "side_share": round(share, 4),
                "members": [list(self.span_of(r)) for r, _ in self.members(set_id) if r in self.state.shots]}
        self.cache.save_canvas(meta, rgba)
        self.state.add_canvas(set_id, TIER_MOSAIC, key, a.world_x0, a.world_y0, a.scale, rgba, meta, a.tex_w, a.tex_h)
        return key

    def plate_key(self, set_id: int, recipe: str) -> Optional[str]:
        """The key of the set's plate to look for: the registry's (the plate last painted for it, with or without an
        identity reference, a shot alone too); else, for a shot alone, phase 2's."""
        _, rkey = self.reg.plate_of(set_id) if self.enabled else (None, None)
        if rkey is not None:
            return rkey
        return self.base_plate_key(set_id, recipe) if self.alone(set_id) else None

    def base_plate_key(self, set_id: int, recipe: str) -> Optional[str]:
        """Phase 2's plate key of a shot alone: its mosaic and the painter's recipe."""
        info = self.state.shots[set_id]
        return side_cache.key_hash(info.mosaic_key, recipe) if info.mosaic_key else None

    def begin_paint(self, set_id: int):
        self.painting.add(set_id)

    def end_paint(self, set_id: int):
        self.painting.discard(set_id)

    def adopt_plate(self, set_id: int, meta: dict, rkey: str):
        """A cached plate found at the warm start: the registry learns it, so later joins are judged against it."""
        if self.enabled:
            x0, y0, s = float(meta["world_x0"]), float(meta["world_y0"]), float(meta["scale"])
            self.reg.set_plate(set_id, (x0, y0, x0 + int(meta["tex_w"]) * s, y0 + int(meta["tex_h"]) * s), rkey)

    def plate_painted(self, set_id: int, a: Mosaic, rkey: str):
        """A plate was painted on atlas `a` (a shot alone too): later members join only if it covers them and agrees
        with them along their side edges."""
        if self.enabled:
            self.reg.set_plate(set_id, (a.world_x0, a.world_y0, a.world_x0 + a.tex_w * a.scale,
                                        a.world_y0 + a.tex_h * a.scale), rkey)
            self.save()
