"""The live worker's state (role: schedule, data side): which shots are analysed, which canvas versions exist and where their
pixels are, publishing into the side store, and keeping the canvases the player will need soon in the store's heap.

Shot rows: the content model's shot ids, or rows handed out as cuts are found (incremental). Canvas id = the shot's set
(set_atlas.py: shots of one place share a world and a canvas; a shot alone is its own set, id = its row). Canvas versions
grow per canvas id in the order they are made, and every member of a set sees the same versions; the player shows the
best tier, then the newest version.
Heap residency: the best version of every shot starting within the horizon ahead of the playhead (nearest first), plus
the one on screen, is kept in a heap slot, and right after it that shot's patch page (phase 4: its exit strips, a slot
of its own; within PAGE_HORIZON_S, and only for a shot that has a canvas to lay them over). A slot is reused for a nearer
need by evicting what is needed farthest behind the playhead, then the farthest ahead; never the slot the player says it
is copying. Pixels come from RAM (recently made) or the disk cache.
"""
from __future__ import annotations

import bisect
import collections
import threading
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np

from . import side_cache, side_store, structure
from .analyse_v0 import ShotAnalysis
from .film_timeline import Film

SHORT_SHOT_S = 0.8                                                # X5: shots this short get no plate of their own
PAGE_HORIZON_S = 30.0                                             # patch pages are kept in the heap this far ahead
TIER_MOSAIC, TIER_PLATE = side_store.ENUMS["tier"]["mosaic"], side_store.ENUMS["tier"]["plate"]
TIER_STRUCTURE = side_store.ENUMS["tier"]["structure"]
SHOT_FLAGS = side_store.ENUMS["shot_flags"]
FRAME_FLAGS = side_store.ENUMS["frame_flags"]
STEP = side_store.ENUMS["step_state"]


@dataclass
class CanvasVersion:
    canvas_id: int
    version: int
    tier: int
    rkey: str
    world_x0: float
    world_y0: float
    scale: float
    tex_w: int
    tex_h: int
    valid_from_s: float
    meta: dict                          # the disk cache's fields (first, n, tier, rkey, ...)
    slot: Optional[int] = None          # heap slot holding it, if any


@dataclass
class PageVersion:
    """A shot's patch page: in RAM only until it is first written into a heap slot, then loaded from the disk cache
    again when the heap needs it (a whole episode's pages would not sit in RAM)."""
    row: int
    version: int
    T3: Optional[np.ndarray]            # 3x3 shot world -> set world (None: the shot is its own set)
    rkey: str
    valid_from_s: float
    load: Callable                      # () -> structure.Page or None (the disk cache)
    page: Optional[structure.Page] = None
    slot: Optional[int] = None          # heap slot holding it, if any

    def get(self) -> Optional[structure.Page]:
        return self.page if self.page is not None else self.load()


@dataclass
class ShotInfo:
    row: int
    first: int
    n: int
    start_s: float
    end_s: float
    crop: tuple
    from_content_model: bool
    analysed: bool = False
    mosaic_empty: bool = False
    mosaic_key: Optional[str] = None
    plate_tried: bool = False           # a plate was attempted (made, failed or given up)
    set_id: int = -1                    # its canvas id (its set's); -1 until published
    versions: list = field(default_factory=list)   # its set's canvas versions (the same list for every member)
    page: Optional[PageVersion] = None  # its patch page (exit strips), if it has patches

    @property
    def short(self) -> bool:
        return self.end_s - self.start_s < SHORT_SHOT_S

    def best(self) -> Optional[CanvasVersion]:
        return max(self.versions, key=lambda v: (v.tier, v.version), default=None)


class PixelCache:
    """Recently made canvases' pixels in RAM (least recently used dropped beyond `budget` bytes)."""

    def __init__(self, budget: int):
        self.budget, self.used = budget, 0
        self.items = collections.OrderedDict()

    def put(self, key, pixels: np.ndarray):
        if key in self.items:
            self.used -= self.items.pop(key).nbytes
        self.items[key] = pixels
        self.used += pixels.nbytes
        while self.used > self.budget and len(self.items) > 1:
            _, old = self.items.popitem(last=False)
            self.used -= old.nbytes

    def get(self, key):
        px = self.items.get(key)
        if px is not None:
            self.items.move_to_end(key)
        return px


class WorkerState:
    def __init__(self, film: Film, store: side_store.SideStore, cache: side_cache.SideCache, log, ram_budget: int = 768 << 20):
        self.film, self.store, self.cache, self.log = film, store, cache, log
        self.lock = threading.RLock()                             # the store and this state, across the worker's threads
        self.shots: dict = {}                                     # row -> ShotInfo
        self.by_first: dict = {}                                  # first frame -> row
        self.pending_rows: dict = {}                              # first frame -> row handed out, shot not published yet
        self.done: list = []                                      # sorted [first, end) of analysed shots (incremental)
        self.next_row = 0
        self.version_of: dict = collections.defaultdict(int)      # canvas id -> last version handed out
        self.canvases: dict = {}                                  # canvas id (set) -> its versions (shared with members)
        self.members: dict = collections.defaultdict(set)         # canvas id (set) -> member rows
        self.pixels = PixelCache(ram_budget)
        self.slots: list = [None] * store.heap_slots              # slot -> CanvasVersion
        if film.spans:
            for s in film.spans:
                self._add_shot(s.shot_id, s.first, s.n, True)
            self.next_row = len(film.spans)

    # ---- shots -----------------------------------------------------------------------------------------------------------
    def _add_shot(self, row: int, first: int, n: int, from_cm: bool) -> ShotInfo:
        film = self.film
        info = ShotInfo(row=row, first=first, n=n, start_s=film.cut_time(first), end_s=film.cut_time(first + n),
                        crop=film.crop_at(film.cut_time(first)), from_content_model=from_cm)
        self.shots[row] = info
        self.by_first[first] = row
        self._join(info, row)
        return info

    def ensure_shot(self, row: int, first: int, n: int, from_cm: bool) -> ShotInfo:
        """The shot's row in this state (nothing written to the store until it is published)."""
        with self.lock:
            info = self.shots.get(row)
            return info if info is not None else self._add_shot(row, first, n, from_cm)

    def _join(self, info: ShotInfo, set_id: int):
        """The shot's canvas becomes its set's: it sees the set's versions (one shared list)."""
        if info.set_id == set_id:
            return
        if info.set_id >= 0:
            self.members[info.set_id].discard(info.row)
        info.set_id = set_id
        info.versions = self.canvases.setdefault(set_id, [])
        self.members[set_id].add(info.row)

    def next_use(self, canvas_id: int, playhead: float) -> float:
        """When the canvas is next on screen: the earliest start of a member not yet over; when all are behind, the
        latest end (canvases long behind the playhead go first)."""
        rows = [r for r in self.members.get(canvas_id, ()) if r in self.shots]
        ahead = [self.shots[r].start_s for r in rows if self.shots[r].end_s > playhead]
        if ahead:
            return min(ahead)
        return max((self.shots[r].end_s for r in rows), default=-1e9)

    # the analyser's plan protocol
    def is_done(self, first: int, n: int) -> bool:
        with self.lock:
            if self.film.spans:                                   # done when analysed shots tile it (hidden cuts split it)
                f, end = first, first + n
                while f < end:
                    info = self.shots.get(self.by_first.get(f, -1))
                    if info is None or not info.analysed or info.n <= 0:
                        return False
                    f += info.n
                return True
            i = bisect.bisect_right(self.done, [first, float("inf")]) - 1
            return i >= 0 and self.done[i][0] <= first < self.done[i][1]

    def next_undone(self, frame: int) -> int:
        with self.lock:
            for a, b in self.done:
                if a <= frame < b:
                    frame = b
            return frame

    def new_shot_id(self, first: int) -> int:
        """The row of the shot that starts at `first`: the one it had before (a shot analysed again, or one given up at
        a seek), else a fresh one. Rows are never handed out twice for one first frame, so they don't run out."""
        with self.lock:
            row = self.by_first.get(first, self.pending_rows.get(first))
            if row is None:
                row = self.pending_rows[first] = self.next_row
                self.next_row += 1
            return row

    def shots_from(self, t: float, horizon_s: float) -> list:
        """Known shots on screen at any time in [t, t + horizon_s], in film order."""
        with self.lock:
            out = [s for s in self.shots.values() if s.end_s > t and s.start_s <= t + horizon_s]
        return sorted(out, key=lambda s: s.first)

    # ---- publishing a shot's analysis --------------------------------------------------------------------------------------
    def publish_analysis(self, an: ShotAnalysis, set_id: Optional[int] = None, transforms: Optional[np.ndarray] = None):
        """The side plan of a shot into the store: its frames' transforms, gains, states and flags, and its shot row.
        set_id / transforms: the set the shot joined and its frames' maps into the set's world (default: its own)."""
        with self.lock:
            info = self.shots.get(an.shot_id)
            if info is None:
                info = self._add_shot(an.shot_id, an.first, an.n, an.from_content_model)
            self._join(info, an.shot_id if set_id is None else set_id)
            info.crop = tuple(an.crop)
            info.n, info.end_s = an.n, self.film.cut_time(an.first + an.n)   # shorter than its span after a hidden cut
            n = an.n
            states = an.states if an.states is not None else np.zeros((n, 4), np.uint8)
            flags = np.full(n, FRAME_FLAGS["valid"], np.uint32)
            flags[0] |= FRAME_FLAGS["cut"]
            flags[an.new_drawing] |= FRAME_FLAGS["new_drawing"]
            self.store.write_frame_plan(an.first, an.shot_id, an.transforms() if transforms is None else transforms,
                                        an.display_gains().astype(np.float32), states.astype(np.uint8), flags)
            info.analysed = True
            try:
                self._write_shot_row(info)
            except Exception:
                info.analysed = False                             # not done: its row is not in the store
                raise
            if not self.film.spans:
                bisect.insort(self.done, [an.first, an.first + n])
            self.store.worker["analysed_frames"] = int(self.store.worker["analysed_frames"]) + n

    def _write_shot_row(self, info: ShotInfo):
        flags = SHOT_FLAGS["known"]
        flags |= SHOT_FLAGS["analysed"] if info.analysed else 0
        flags |= SHOT_FLAGS["mosaic_empty"] if info.mosaic_empty else 0
        flags |= SHOT_FLAGS["short"] if info.short else 0
        flags |= SHOT_FLAGS["from_content_model"] if info.from_content_model else 0
        best = info.best()
        self.store.write_shot(info.row, flags=flags, first_frame=info.first, n_frames=info.n, canvas_id=info.set_id,
                              key_frame=info.first, crop=list(info.crop), start_s=info.start_s, end_s=info.end_s,
                              best_tier=best.tier if best else 0)

    def set_mosaic(self, row: int, mosaic_key: str, empty: bool):
        with self.lock:
            info = self.shots[row]
            info.mosaic_key, info.mosaic_empty = mosaic_key, empty
            self._write_shot_row(info)

    # ---- canvas versions ---------------------------------------------------------------------------------------------------
    def add_canvas(self, row: int, tier: int, rkey: str, world_x0: float, world_y0: float, scale: float,
                   pixels: Optional[np.ndarray], meta: dict, tex_w: int, tex_h: int) -> CanvasVersion:
        """A new canvas version of shot `row`'s set (pixels in RAM when given, else on disk)."""
        with self.lock:
            info = self.shots[row]
            cid = info.set_id
            for v in info.versions:                               # the same recipe again (a cache load): one version
                if v.tier == tier and v.rkey == rkey:
                    if pixels is not None:
                        self.pixels.put((cid, v.version), pixels)
                    return v
            self.version_of[cid] += 1
            start = min((self.shots[r].start_s for r in self.members.get(cid, ()) if r in self.shots), default=info.start_s)
            cv = CanvasVersion(canvas_id=cid, version=self.version_of[cid], tier=tier, rkey=rkey, world_x0=world_x0,
                               world_y0=world_y0, scale=scale, tex_w=tex_w, tex_h=tex_h, valid_from_s=start, meta=meta)
            info.versions.append(cv)
            if pixels is not None:
                self.pixels.put((cid, cv.version), pixels)
            # the shot row is not rewritten here: the player reads it when a showing starts, and a write under way then
            # would cost that showing its canvas (best_tier, the row's only field a canvas changes, is diagnostic)
            return cv

    def _pixels_of(self, cv: CanvasVersion) -> Optional[np.ndarray]:
        px = self.pixels.get((cv.canvas_id, cv.version))
        if px is None:
            px = self.cache.load_canvas(cv.meta)
            if px is not None:
                self.pixels.put((cv.canvas_id, cv.version), px)
        return px

    # ---- patch pages (phase 4) -------------------------------------------------------------------------------------------
    def set_page(self, row: int, load: Optional[Callable], T_set: Optional[np.ndarray], rkey: str,
                 page: Optional[structure.Page] = None):
        """The shot's patch page (load None: it has no patches): load() reads it from the disk cache; page, when given,
        is kept until its first heap write. Its grid is placed in the set's world by T_set (2x3 similarity, member world
        -> set world; None: the shot is its own set)."""
        with self.lock:
            info = self.shots[row]
            if load is None:
                info.page = None
                return
            T3 = None if T_set is None else np.vstack([np.asarray(T_set, np.float64), [0.0, 0.0, 1.0]])
            info.page = PageVersion(row=row, version=(info.page.version + 1) if info.page else 1, T3=T3, rkey=rkey,
                                    valid_from_s=info.start_s, load=load, page=page)

    # ---- heap residency ----------------------------------------------------------------------------------------------------
    def ensure_heap(self, playhead: float, horizon_s: float, uploading_slot: int, max_loads: int = 3) -> int:
        """Keeps the canvases (and patch pages) needed soon in heap slots; returns how many were loaded this call."""
        with self.lock:
            wanted = []
            for info in self.shots_from(playhead - 0.5, horizon_s):
                best = info.best()
                if best is None:
                    continue
                wanted.append(best)
                if info.page is not None and info.start_s <= playhead + PAGE_HORIZON_S:
                    wanted.append(info.page)
            wanted = wanted[:max(1, self.store.heap_slots - 2)]   # nearest first; two slots stay free for what comes
            want_ids = {id(x) for x in wanted}
            loads = 0
            for item in wanted:
                if item.slot is not None and self.slots[item.slot] is item:
                    continue
                slot = self._free_slot(playhead, want_ids, uploading_slot)
                if slot is None:
                    break
                ok = self._write_page(slot, item) if isinstance(item, PageVersion) else self._write_canvas(slot, item)
                if not ok:
                    continue
                loads += 1
                if loads >= max_loads:
                    break
            return loads

    def _take(self, slot: int, item):
        old = self.slots[slot]
        if old is not None:
            old.slot = None
        self.slots[slot], item.slot = item, slot

    def _write_canvas(self, slot: int, cv: CanvasVersion) -> bool:
        px = self._pixels_of(cv)
        if px is None:
            self.log(f"heap: canvas {cv.canvas_id} v{cv.version} has no pixels (cache miss)")
            return False
        if px.nbytes > self.store.heap_slot_bytes:                # made for bigger slots (an older store): skip it
            if not getattr(cv, "too_big_logged", False):
                self.log(f"heap: canvas {cv.canvas_id} v{cv.version} ({px.nbytes >> 20} MB) does not fit a slot")
                cv.too_big_logged = True
            return False
        self._take(slot, cv)
        self.store.begin_slot(slot)
        self.store.finish_slot(slot, px, canvas_id=cv.canvas_id, version=cv.version, tier=cv.tier,
                               world_x0=cv.world_x0, world_y0=cv.world_y0, scale=cv.scale,
                               valid_from_s=cv.valid_from_s, recipe=side_cache.recipe_u64(cv.rkey), shot_hint=cv.canvas_id)
        return True

    def _write_page(self, slot: int, pv: PageVersion) -> bool:
        info = self.shots.get(pv.row)
        page = pv.get()
        if info is None or page is None:
            return False
        try:
            self._take(slot, pv)
            self.store.begin_slot(slot)
            self.store.finish_page(slot, {"shot": pv.row, "grid_w": page.grid_w, "grid_h": page.grid_h,
                                          "first_frame": info.first, "n_frames": info.n,
                                          "grid_map": page.grid_map(pv.T3).ravel()},
                                   page.store_table(side_store.PATCH_DT), page.texture, canvas_id=info.set_id,
                                   version=pv.version, tier=TIER_STRUCTURE, valid_from_s=pv.valid_from_s,
                                   recipe=side_cache.recipe_u64(pv.rkey), shot_hint=pv.row)
            pv.page = None                                        # in the heap now; read from disk if needed again
            return True
        except ValueError as e:                                    # larger than a slot: the shot shows without its patches
            self.store.evict_slot(slot)
            self.slots[slot], pv.slot = None, None
            if not getattr(pv, "too_big_logged", False):
                self.log(f"heap: patch page of shot {pv.row}: {e}")
                pv.too_big_logged = True
            return False

    def _next_use_of(self, item, playhead: float) -> float:
        if isinstance(item, PageVersion):
            info = self.shots.get(item.row)
            if info is None:
                return -1e9
            return info.start_s if info.end_s > playhead else info.end_s
        return self.next_use(item.canvas_id, playhead)

    def _free_slot(self, playhead: float, want_ids: set, uploading_slot: int) -> Optional[int]:
        best, best_score = None, None
        for i, item in enumerate(self.slots):
            if i == uploading_slot:
                continue
            if item is None:
                return i
            if id(item) in want_ids:
                continue
            start = self._next_use_of(item, playhead)
            # behind the playhead first (the farther the better), then the farthest ahead
            score = (0, start) if start < playhead else (1, -start)
            if best_score is None or score < best_score:
                best, best_score = i, score
        return best

    def heap_summary(self) -> dict:
        with self.lock:
            used = [x for x in self.slots if x is not None]
            return {"slots_used": len(used), "pages": sum(isinstance(x, PageVersion) for x in used),
                    "tiers": collections.Counter(x.tier for x in used if isinstance(x, CanvasVersion))}
