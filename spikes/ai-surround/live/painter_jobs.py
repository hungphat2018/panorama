"""T2 plates in the live worker (role: schedule, GPU side): one plate at a time from the resident painter (painter.py), one
per set (worker_sets.py: shots of one place share it), earliest deadline first among the sets it can still finish before
their next member starts (X5's rule: a plate that would be late is not started; it would only help a re-watch). The
deadline is that member's start minus a lead for the player's upload; the painter's time per plate is a running estimate
(a factor 1.2 of safety). No plate starts while the player reports GPU pressure (E1: late refreshes or low present
slack): that is the throttle.

Two-speed policy (X5 addendum): the big painter's plates go first to sets on screen at least --big-plate-min-s in all
(default 3 s: 56 % of JJK's film time from 170 of 527 plates); shorter ones (shots from 0.8 s) get a plate only while no
longer one within the lookahead still needs one. Phase 5's small painter takes the short ones; until then they show their
T1 or the glow. A set is tried once per run.
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import threading
import time
from typing import Optional

from . import side_store
from .side_cache import key_hash
from .identity_encoder import IdentityEncoder
from .painter import Painter, PainterConfig
from .set_atlas import box_through
from .worker_state import TIER_PLATE, ShotInfo, WorkerState

PAINTER = side_store.ENUMS["painter_state"]
PREFETCH_CLUSTERS = 24                                            # identity crops read ahead (~25 s each on the CPU)
SAFETY = 1.2


class IdentityRefs:
    """Phase 4's identity crops: <film cache>/identity/clusters.json (re-read when it changes) ->, per set, the clusters
    cut by the seam in its member shots (most often first), the first body crop of each, else its first crop.

    Only clusters a person checked by eye are used: identity/allowed.json, {"clusters_sha1": <sha1 of clusters.json>,
    "clusters": ["c2", ...], "note": ...}. A cluster can mix characters and CCIP cannot tell (phase 4), and a mixed
    cluster hands one character's look to another. With no list, or a list made for another clusters.json (re-clustering
    renames the clusters), no reference is used."""

    def __init__(self, folder: str, limit: int = 1, log=None):
        self.folder, self.limit, self.log = folder, limit, log or (lambda m: None)
        self.path = os.path.join(folder, "clusters.json")
        self.allowed_path = os.path.join(folder, "allowed.json")
        self.mtime, self.doc = None, None
        self.allowed: set = set()
        self.fresh = False                                        # a new clusters.json or list since top_crops() was asked

    def _load(self):
        try:
            mt = (os.path.getmtime(self.path), os.path.getmtime(self.allowed_path)
                  if os.path.exists(self.allowed_path) else None)
        except OSError:
            self.doc = None
            return
        if mt != self.mtime:
            try:
                with open(self.path, "rb") as f:
                    raw = f.read()
                self.doc = json.loads(raw)
                self.mtime, self.fresh = mt, True
            except (OSError, ValueError):
                self.doc = None
                return
            self.allowed = self._allowed(hashlib.sha1(raw).hexdigest())

    def _allowed(self, sha1: str) -> set:
        try:
            with open(self.allowed_path, encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, ValueError):
            self.log("identity: no readable identity/allowed.json: no references used")
            return set()
        if doc.get("clusters_sha1") != sha1:
            self.log("identity: allowed.json was made for another clusters.json: no references used until it is redone")
            return set()
        allowed = set(doc.get("clusters", [])) & set(self.doc.get("clusters", {}))
        self.log(f"identity: references from {len(allowed)} checked clusters: {', '.join(sorted(allowed))}")
        return allowed

    def for_spans(self, spans) -> list:
        """Crop paths for the worker's shots with these (first, n) spans (at most `limit`). Entries are the content
        model's shots, which the worker may split at hidden cuts: an entry counts when its frames overlap a span."""
        self._load()
        if not self.doc:
            return []
        spans = [(int(f), int(n)) for f, n in spans]
        count = {}
        for e in self.doc.get("cut_by_seam", []):
            ef, en = int(e.get("first", -1)), int(e.get("n", 0))
            if e.get("side", "left") not in ("left", "right"):     # the sides continue only these edges
                continue
            if en > 0 and any(ef < f + n and f < ef + en for f, n in spans):
                for c in e.get("clusters", []):
                    if c in self.allowed:
                        count[c] = count.get(c, 0) + 1
        return self._crops(sorted(count, key=lambda c: -count[c])[:self.limit])

    def top_crops(self, k: int) -> list:
        """The crops of the k clusters cut by a side edge most often in the whole film (read ahead, most often first);
        [] while there is no clusters.json or it has not changed since the last call."""
        self._load()
        if not self.doc or not self.fresh:
            return []
        self.fresh = False
        count = {}
        for e in self.doc.get("cut_by_seam", []):
            if e.get("side", "left") in ("left", "right"):
                for c in e.get("clusters", []):
                    if c in self.allowed:
                        count[c] = count.get(c, 0) + 1
        return self._crops(sorted(count, key=lambda c: -count[c])[:k])

    def _crops(self, clusters) -> list:
        """Each cluster's first whole body (not cut by the frame), else its first body, else its first crop (existing
        files only): the reference should show what the seam cuts off."""
        out = []
        for c in clusters:
            crops = self.doc.get("clusters", {}).get(c, {}).get("crops", [])
            pick = next((k for k in crops if k.get("kind") == "body" and k.get("uncut")),
                        next((k for k in crops if k.get("kind") == "body"), crops[0] if crops else None))
            if pick:
                p = os.path.join(self.folder, pick["path"])
                if os.path.isfile(p):
                    out.append(p)
        return out


class PainterJobs(threading.Thread):
    def __init__(self, state: WorkerState, sets, mosaic_of, painter: Painter, log, lead_s: float = 1.5,
                 est_s: float = 8.0, big_min_s: float = 3.0, lookahead_s: float = 600.0):
        super().__init__(daemon=True, name="paint")
        self.state, self.sets, self.mosaic_of, self.painter, self.log = state, sets, mosaic_of, painter, log
        self.lead_s, self.est_s, self.big_min_s, self.lookahead_s = lead_s, est_s, big_min_s, lookahead_s
        self.recipe = painter.cfg.recipe
        self.plates_made = 0
        self.tried: set = set()                                   # set ids tried this run
        self._lock = threading.Lock()
        self._view = (0.0, 1.0, False)                            # playhead, speed, pressure
        self._stop = threading.Event()
        self.throttled = False
        self.throttle_events = 0
        self.current: Optional[int] = None
        self.failed = 0
        self.stage_s = {}                                         # the service's last stage timings
        folder = os.path.join(state.cache.dir, "identity")
        self.identity = IdentityRefs(folder, log=log)             # one crop a plate: one conditioning each
        self.encoder = None
        if painter.cfg.identity_refs:
            self.encoder = IdentityEncoder(folder, painter.cfg.te, painter.cfg.identity_port, log)
            self.encoder.start()
        self.with_identity = 0
        self.not_before: dict = {}                               # set id -> monotonic time: retry after (atlas not ready)

    def set_view(self, playhead: float, speed: float, pressure: bool):
        with self._lock:
            self._view = (playhead, speed, pressure)

    def stop(self):
        self._stop.set()
        if self.encoder is not None:
            self.encoder.stop()

    def _set_state(self, name: str):
        self.state.store.worker["painter_state"] = PAINTER[name]

    def _paint_once(self, sid: int, atlas, crop_t, identity):
        """One plate from the service; a timed-out service is stopped (the loop starts it again before the next)."""
        try:
            return self.painter.paint(atlas, crop_t, identity)
        except Exception as e:
            if _is_timeout(e):
                self.log(f"paint: set {sid}: the painter service timed out; stopping it")
                self.painter.stop()
            raise

    def pick(self) -> Optional[ShotInfo]:
        """The shot whose set gets the next plate (its earliest member still to come), or None."""
        with self._lock:
            playhead, speed, pressure = self._view
        if pressure:
            if not self.throttled:
                self.throttled, self.throttle_events = True, self.throttle_events + 1
                self._set_state("throttled")
            return None
        if self.throttled:
            self.throttled = False
            self._set_state("idle")
        need = self.est_s * SAFETY
        rate = speed if speed > 0.05 else 1.0                     # paused: plan as if it plays on
        short, seen = None, set()
        with self.state.lock:
            shots = sorted(self.state.shots.values(), key=lambda s: s.first)
            on_screen = {}                                        # set id -> its members' film time
            for s in shots:
                on_screen[s.set_id] = on_screen.get(s.set_id, 0.0) + (s.end_s - s.start_s)
            now = time.monotonic()
            for info in shots:
                sid = info.set_id
                if sid in seen or info.end_s <= playhead or self.not_before.get(sid, 0.0) > now:
                    continue
                if (not info.analysed or info.mosaic_key is None or info.short or sid in self.tried or
                        any(v.tier >= TIER_PLATE for v in info.versions)):
                    continue
                seen.add(sid)                                     # its earliest member still to come decides
                ahead = (info.start_s - self.lead_s - playhead) / rate
                if ahead < need or ahead > self.lookahead_s:
                    continue
                if on_screen.get(sid, 0.0) >= self.big_min_s:
                    return info                                   # the earliest feasible long one
                if short is None:
                    short = info                                  # the earliest feasible short one, if no long one waits
        return short

    def run(self):
        self._set_state("loading")
        try:
            self.painter.start()
        except Exception as e:
            self.log(f"paint: the painter did not start: {e!r}; no plates this run")
            self._set_state("off")
            return
        self._set_state("idle")
        while not self._stop.is_set():
            info = self.pick()
            if info is None:
                time.sleep(0.2)
                continue
            if not self.painter.alive():
                self.log("paint: the painter service is gone; starting it again")
                try:
                    self.painter.stop()
                    self.painter.start()
                except Exception as e:
                    self.log(f"paint: restart failed ({e!r}); no more plates this run")
                    self._set_state("off")
                    return
            try:
                self._paint(info)
            except Exception as e:                                # one bad plate must not end painting for the run
                self.failed += 1
                self.log(f"paint: set {info.set_id} failed: {e!r}")
                self._set_state("idle")
        self._set_state("off")

    def _paint(self, info: ShotInfo):
        sid = info.set_id
        self.sets.begin_paint(sid)                                # no join until its plate is registered
        try:
            self._paint_set(info)
        finally:
            self.sets.end_paint(sid)

    def _paint_set(self, info: ShotInfo):
        st = self.state
        sid = info.set_id
        with st.lock:
            self.tried.add(sid)
            info.plate_tried = True
            self.current = sid
        try:
            akey, atlas, _ = self.sets.atlas(sid, self.mosaic_of)
            if atlas is None:                                     # a new member's mosaic is still being made
                with st.lock:
                    self.tried.discard(sid)
                self.not_before[sid] = time.monotonic() + 2.0
                return
            # the colour fit's region: this shot's first frame's picture in the set's texels
            t = dict(self.sets.members(sid)).get(info.row)
            x0, y0, x1, y1 = info.crop if t is None else box_through(t, info.crop)
            crop_t = ((x0 - atlas.world_x0) / atlas.scale, (y0 - atlas.world_y0) / atlas.scale,
                      (x1 - atlas.world_x0) / atlas.scale, (y1 - atlas.world_y0) / atlas.scale)
            identity = None
            if self.encoder is not None:
                try:
                    self.encoder.prefetch(self.identity.top_crops(PREFETCH_CLUSTERS))
                    with st.lock:
                        spans = [(st.shots[r].first, st.shots[r].n) for r, _ in self.sets.members(sid) if r in st.shots]
                    crops = self.identity.for_spans(spans)
                    identity = self.encoder.ready(crops[0]) if crops else None   # not read yet: painted without it
                except Exception as e:                            # a bad clusters.json costs the reference only
                    self.log(f"paint: set {sid}: identity lookup failed ({e!r}); painted without")
                    identity = None
            self._set_state("painting")
            try:
                rgba, seconds, stages = self._paint_once(sid, atlas, crop_t, identity)
            except Exception as e:
                if identity is None:
                    self.failed += 1
                    self.log(f"paint: set {sid} failed: {e!r}")
                    return
                self.log(f"paint: set {sid} failed with its identity reference ({e!r}); again without")
                identity = None
                try:
                    rgba, seconds, stages = self._paint_once(sid, atlas, crop_t, None)
                except Exception as e2:
                    self.failed += 1
                    self.log(f"paint: set {sid} failed: {e2!r}")
                    return
        finally:
            self.current = None
            self._set_state("idle")
        self.est_s = seconds if not self.plates_made else 0.7 * self.est_s + 0.3 * seconds
        self.plates_made += 1
        self.stage_s = stages
        alone = self.sets.alone(sid)
        rkey = self.sets.base_plate_key(sid, self.recipe) if alone else key_hash(akey, self.recipe)
        if identity:                                              # a plate painted with a reference is another plate
            rkey = key_hash(rkey, os.path.basename(identity[1]))
            self.with_identity += 1
        key_shot = st.shots[sid]
        meta = {"first": key_shot.first, "n": key_shot.n, "tier": TIER_PLATE, "rkey": rkey, "world_x0": atlas.world_x0,
                "world_y0": atlas.world_y0, "scale": atlas.scale, "tex_w": atlas.tex_w, "tex_h": atlas.tex_h,
                "paint_seconds": round(seconds, 2), "recipe": self.recipe, "atlas_key": akey,
                "identity": os.path.basename(identity[1]) if identity else None}
        st.cache.save_canvas(meta, rgba)
        st.add_canvas(sid, TIER_PLATE, rkey, atlas.world_x0, atlas.world_y0, atlas.scale, rgba, meta, atlas.tex_w,
                      atlas.tex_h)
        self.sets.plate_painted(sid, atlas, rkey)                 # a shot alone too: later joins are judged on it
        w = st.store.worker
        w["plates_done"] = int(w["plates_done"]) + 1
        w["painter_s_per_plate"] = self.est_s
        n_members = len(self.sets.members(sid))
        self.log(f"paint: set {sid} ({n_members} shot{'s' if n_members > 1 else ''}, first at {info.start_s:.1f} s) plate "
                 f"in {seconds:.1f} s {stages}, estimate {self.est_s:.1f} s"
                 f"{', identity ' + meta['identity'] if meta['identity'] else ''}",
                 event={"event": "plate", "set": sid, "shot": info.row, "members": n_members, "start_s": info.start_s,
                        "seconds": round(seconds, 2), "stages": stages, "identity": meta["identity"]})


def _is_timeout(e: Exception) -> bool:
    return isinstance(e, (TimeoutError, socket.timeout)) or isinstance(getattr(e, "reason", None),
                                                                      (TimeoutError, socket.timeout))


def painter_for(run_dir: str, cfg: PainterConfig, log) -> Painter:
    return Painter(cfg, os.path.join(run_dir, "painter-service.log"), log)
