"""The side store, worker end (and a reader for tests and diagnostics): one memory-mapped file whose layout is
side-store.layout.json, the contract the player's C reader mirrors.

    store = SideStore.create(path, n_frames=..., max_shots=..., heap_slots=24, heap_slot_bytes=16 << 20, ...)
    store = SideStore.open(path)                    # attach to an existing store (worker restart, tests)
    store.layout / store.worker / store.player      # numpy structured views of the header blocks
    store.frames / store.shots / store.canvases     # numpy structured arrays over the tables
    store.slot_pixels(i)                            # writable memoryview of heap slot i

Consistency (the layout's CONSISTENCY rule): records that change after they are first published are written under a
sequence counter, odd while the fields change. write_frame_plan / write_shot / begin_slot + finish_slot do that here:
a write first sets the counter's low bit (so a counter a crashed writer left odd stays usable), then adds 1 at the end.
read_player and read_record copy a record and retry until the counter was even and unchanged. The writes here are
ordinary stores in program order (numpy copies, no non-temporal stores), which x86-64 (the only target) makes visible to
other cores in that order: what the player's reader relies on.
"""
from __future__ import annotations

import ctypes
import json
import mmap
import os
import secrets
from typing import Optional

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
LAYOUT_PATH = os.path.join(HERE, "side-store.layout.json")
with open(LAYOUT_PATH, encoding="utf-8") as _f:
    LAYOUT = json.load(_f)

SCHEMA_MAJOR = int(LAYOUT["schema"].rsplit("/", 1)[1])
SCHEMA_MINOR = 0
MAGIC = b"PNRSIDE2"
PAGE_MAGIC = b"PNRPAGE1"
HEADER_BYTES = LAYOUT["header_bytes"]
PAGE = 4096
ENUMS = {name: dict(values) for name, values in LAYOUT["enums"].items()}


def _dtype(spec: dict) -> np.dtype:
    """numpy structured dtype of one block or record of the layout (explicit offsets and item size)."""
    names, formats, offsets = [], [], []
    for f in spec["fields"]:
        names.append(f["name"])
        formats.append((f["type"], tuple(f["shape"])) if "shape" in f else f["type"])
        offsets.append(f["offset"])
    dt = np.dtype({"names": names, "formats": formats, "offsets": offsets, "itemsize": spec["bytes"]})
    return dt


BLOCK_DT = {name: _dtype(spec) for name, spec in LAYOUT["blocks"].items()}
RECORD_DT = {name: _dtype(spec) for name, spec in LAYOUT["records"].items()}
BLOCK_OFFSET = {name: spec["offset"] for name, spec in LAYOUT["blocks"].items()}
FRAME_DT, SHOT_DT, CANVAS_DT = RECORD_DT["frame"], RECORD_DT["shot"], RECORD_DT["canvas"]
PAGE_DT, PATCH_DT = RECORD_DT["patch_page"], RECORD_DT["patch"]


def _align(n: int, a: int) -> int:
    return -(-n // a) * a


def qpc() -> int:
    """QueryPerformanceCounter: the clock both processes stamp the store with (the player's own clock)."""
    v = ctypes.c_int64()
    ctypes.windll.kernel32.QueryPerformanceCounter(ctypes.byref(v))
    return v.value


def qpc_freq() -> int:
    v = ctypes.c_int64()
    ctypes.windll.kernel32.QueryPerformanceFrequency(ctypes.byref(v))
    return v.value


def plan_layout(n_frames: int, max_shots: int, heap_slots: int, heap_slot_bytes: int) -> dict:
    """Offsets and the file size for these table sizes (tables page-aligned, heap slots page-sized multiples)."""
    heap_slot_bytes = _align(heap_slot_bytes, PAGE)
    off_frames = HEADER_BYTES
    off_shots = _align(off_frames + n_frames * FRAME_DT.itemsize, PAGE)
    off_canvases = _align(off_shots + max_shots * SHOT_DT.itemsize, PAGE)
    off_heap = _align(off_canvases + heap_slots * CANVAS_DT.itemsize, PAGE)
    return {"max_frames": n_frames, "max_shots": max_shots, "heap_slots": heap_slots, "heap_slot_bytes": heap_slot_bytes,
            "off_frames": off_frames, "off_shots": off_shots, "off_canvases": off_canvases, "off_heap": off_heap,
            "file_bytes": off_heap + heap_slots * heap_slot_bytes}


class SideStore:
    def __init__(self, path: str, fh, mm: mmap.mmap):
        self.path, self._fh, self._mm = path, fh, mm
        self.layout = np.frombuffer(mm, BLOCK_DT["layout"], 1, BLOCK_OFFSET["layout"])[0]
        self.worker = np.frombuffer(mm, BLOCK_DT["worker"], 1, BLOCK_OFFSET["worker"])[0]
        self.player = np.frombuffer(mm, BLOCK_DT["player"], 1, BLOCK_OFFSET["player"])[0]
        self._bind_tables()

    def _bind_tables(self):
        lay = self.layout
        self.frames = np.frombuffer(self._mm, FRAME_DT, int(lay["max_frames"]), int(lay["off_frames"]))
        self.shots = np.frombuffer(self._mm, SHOT_DT, int(lay["max_shots"]), int(lay["off_shots"]))
        self.canvases = np.frombuffer(self._mm, CANVAS_DT, int(lay["heap_slots"]), int(lay["off_canvases"]))
        self.heap_slots = int(lay["heap_slots"])
        self.heap_slot_bytes = int(lay["heap_slot_bytes"])
        self._off_heap = int(lay["off_heap"])

    # ---- life cycle ----------------------------------------------------------------------------------------------

    @classmethod
    def create(cls, path: str, n_frames: int, max_shots: int, heap_slots: int, heap_slot_bytes: int,
               film_bytes: int, src_w: int, src_h: int, film_name: str) -> "SideStore":
        """A new store file (an old one at path is replaced): zero-filled, layout written, a new store_uid. The worker
        block stays in state 'starting' until the caller sets it running."""
        plan = plan_layout(n_frames, max_shots, heap_slots, heap_slot_bytes)
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        fh = open(path, "w+b")
        chunk = bytes(16 << 20)                                   # real zeros on disk: no zero-fill stall on a later page
        left = plan["file_bytes"]
        while left > 0:
            fh.write(chunk[:min(left, len(chunk))])
            left -= len(chunk)
        fh.flush()
        mm = mmap.mmap(fh.fileno(), plan["file_bytes"], access=mmap.ACCESS_WRITE)
        lay = np.frombuffer(mm, BLOCK_DT["layout"], 1, BLOCK_OFFSET["layout"])[0]
        lay["magic"] = MAGIC
        lay["schema_major"], lay["schema_minor"] = SCHEMA_MAJOR, SCHEMA_MINOR
        lay["header_bytes"] = HEADER_BYTES
        lay["frame_rec_bytes"], lay["shot_rec_bytes"] = FRAME_DT.itemsize, SHOT_DT.itemsize
        lay["canvas_rec_bytes"] = CANVAS_DT.itemsize
        for k in ("file_bytes", "max_frames", "max_shots", "heap_slots", "heap_slot_bytes", "off_frames", "off_shots",
                  "off_canvases", "off_heap"):
            lay[k] = plan[k]
        lay["store_uid"] = secrets.randbits(63) | 1
        store = cls(path, fh, mm)
        w = store.worker
        w["pid"], w["state"], w["qpc_freq"] = os.getpid(), ENUMS["worker_state"]["starting"], qpc_freq()
        w["film_bytes"], w["src_w"], w["src_h"] = film_bytes, src_w, src_h
        w["film_name"] = os.path.basename(film_name).encode("utf-8")[:127]
        w["epoch_seen"] = -1
        store.player["uploading_slot"] = -1                       # no player yet: nothing is being copied
        store.frames["shot"] = -1
        return store

    @classmethod
    def open(cls, path: str) -> "SideStore":
        """Attach to an existing store; raises ValueError when it is not a side store of this schema."""
        fh = open(path, "r+b")
        size = os.fstat(fh.fileno()).st_size
        if size < HEADER_BYTES:
            fh.close()
            raise ValueError(f"{path}: {size} bytes, not a side store")
        mm = mmap.mmap(fh.fileno(), size, access=mmap.ACCESS_WRITE)
        lay = np.frombuffer(mm, BLOCK_DT["layout"], 1, 0)[0]
        ok = bytes(lay["magic"]) == MAGIC and int(lay["schema_major"]) == SCHEMA_MAJOR and int(lay["file_bytes"]) == size
        del lay                                                   # a live view of the map would keep mm.close() from closing it
        if not ok:
            mm.close()
            fh.close()
            raise ValueError(f"{path}: not a {LAYOUT['schema']} store (or a different size)")
        return cls(path, fh, mm)

    def fits(self, n_frames: int, max_shots: int) -> bool:
        return int(self.layout["max_frames"]) >= n_frames and int(self.layout["max_shots"]) >= max_shots

    def reset(self, film_bytes: int, src_w: int, src_h: int, film_name: str):
        """Empties an attached store for a new worker run (a player may still have it mapped, so the file cannot be
        re-created): tables unpublished, plans cleared, every slot evicted, then a new store_uid, which tells the player to
        drop everything it took from the old contents."""
        w = self.worker
        w["state"] = ENUMS["worker_state"]["starting"]
        w["frames_published"] = w["shots_published"] = w["analysed_frames"] = 0
        self.frames["seq"] |= 1
        self.frames["shot"] = -1
        self.frames["flags"] = 0
        self.frames["seq"] += 1
        for slot in range(self.heap_slots):
            self.evict_slot(slot)
        self.shots["seq"] |= 1
        self.shots["flags"] = 0
        self.shots["seq"] += 1
        for k in ("plates_done", "plates_skipped", "cache_hits", "throttle_events", "painter_state"):
            w[k] = 0
        w["pid"], w["qpc_freq"], w["epoch_seen"] = os.getpid(), qpc_freq(), -1
        w["film_bytes"], w["src_w"], w["src_h"] = film_bytes, src_w, src_h
        w["film_name"] = os.path.basename(film_name).encode("utf-8")[:127]
        self.layout["store_uid"] = secrets.randbits(63) | 1

    def close(self):
        # numpy views keep the mmap's buffer exported: drop them before closing
        self.layout = self.worker = self.player = self.frames = self.shots = self.canvases = None
        try:
            self._mm.close()
        except BufferError:
            pass                                                  # a caller still holds a view; the OS unmaps at exit
        self._fh.close()

    # ---- worker block --------------------------------------------------------------------------------------------

    def beat(self, status: Optional[str] = None):
        w = self.worker
        w["beat"] = int(w["beat"]) + 1
        w["qpc"] = qpc()
        if status is not None:
            w["status"] = status.encode("ascii", "replace")[:31]

    def set_state(self, name: str):
        self.worker["state"] = ENUMS["worker_state"][name]

    # ---- frame table ---------------------------------------------------------------------------------------------

    def publish_frame_times(self, times: np.ndarray):
        """Writes every frame's pts (film seconds), then publishes the rows: the times never change afterwards."""
        n = len(times)
        if n > len(self.frames):
            raise ValueError(f"{n} frames do not fit the store's {len(self.frames)} rows")
        self.frames["pts"][:n] = times
        self.worker["frames_published"] = n

    def write_frame_plan(self, i0: int, shot: int, h: np.ndarray, gain: np.ndarray, state: np.ndarray, flags: np.ndarray):
        """The side plan of frames [i0, i0 + len(h)): one shot row, per-frame projective transforms (n, 8: a 3x3 map
        divided by its last element, row-major, without it), gains (n, 4), step states (n, 4) and flags (n,). Each row
        under its own sequence counter."""
        rows = self.frames[i0:i0 + len(h)]
        rows["seq"] |= 1                                          # odd: readers drop what they copy now
        rows["shot"] = shot
        rows["h"] = h
        rows["gain"] = gain
        rows["state"] = state
        rows["flags"] = flags
        rows["seq"] += 1

    def clear_frame_plan(self, i0: int, n: int):
        rows = self.frames[i0:i0 + n]
        rows["seq"] |= 1
        rows["shot"] = -1
        rows["flags"] = 0
        rows["seq"] += 1

    # ---- shot table ----------------------------------------------------------------------------------------------

    def write_shot(self, i: int, **fields):
        """Sets fields of shot row i under its sequence counter, and publishes the row."""
        rec = self.shots[i]
        rec["seq"] = int(rec["seq"]) | 1
        for k, v in fields.items():
            rec[k] = v
        rec["seq"] += 1
        if i >= int(self.worker["shots_published"]):
            self.worker["shots_published"] = i + 1

    # ---- canvas slots ----------------------------------------------------------------------------------------------

    def slot_pixels(self, slot: int) -> memoryview:
        off = self._off_heap + slot * self.heap_slot_bytes
        return memoryview(self._mm)[off:off + self.heap_slot_bytes]

    def begin_slot(self, slot: int):
        """Takes a slot for new pixels: odd gen, state free. Readers that copied it before see the gen change."""
        rec = self.canvases[slot]
        if int(rec["gen"]) % 2 == 0:
            rec["gen"] += 1
        rec["state"] = ENUMS["canvas_state"]["free"]

    def finish_slot(self, slot: int, pixels: np.ndarray, **fields):
        """Copies an (h, w, 4) uint8 image into the slot and publishes it complete with the given record fields
        (canvas_id, version, tier, world_x0, world_y0, scale, valid_from_s, recipe, shot_hint)."""
        h, w = pixels.shape[:2]
        row_bytes = w * 4
        if h * row_bytes > self.heap_slot_bytes:
            raise ValueError(f"{w}x{h} canvas needs {h * row_bytes} bytes, a slot has {self.heap_slot_bytes}")
        rec = self.canvases[slot]
        if int(rec["gen"]) % 2 == 0:                              # begin_slot was not called: do it now
            self.begin_slot(slot)
        dst = np.frombuffer(self.slot_pixels(slot), np.uint8, h * row_bytes).reshape(h, w, 4)
        dst[...] = pixels
        rec["format"] = ENUMS["pixel_format"]["rgba8_film"]
        rec["tex_w"], rec["tex_h"], rec["row_bytes"] = w, h, row_bytes
        rec["bytes"] = h * row_bytes
        rec["kind"], rec["shot"], rec["page_header_bytes"], rec["n_patches"] = ENUMS["canvas_kind"]["canvas"], -1, 0, 0
        for k, v in fields.items():
            rec[k] = v
        rec["made_qpc"] = qpc()
        rec["state"] = ENUMS["canvas_state"]["complete"]
        rec["gen"] += 1                                           # even: complete and stable

    def finish_page(self, slot: int, header: dict, table: np.ndarray, texels: np.ndarray, **fields):
        """Writes a shot's patch page into a slot (the patch_page record from `header`, the patch table, the packed
        RGBA8 texels from a page-aligned offset) and publishes it complete with the canvas-record fields given
        (canvas_id, version, tier, valid_from_s, recipe, shot_hint)."""
        n = len(table)
        hb = _align(PAGE_DT.itemsize + n * PATCH_DT.itemsize, PAGE)
        th, tw = texels.shape[:2]
        row_bytes = tw * 4
        if hb + th * row_bytes > self.heap_slot_bytes:
            raise ValueError(f"patch page of {n} patches, {tw}x{th} texels needs {hb + th * row_bytes} bytes, a slot has "
                             f"{self.heap_slot_bytes}")
        rec = self.canvases[slot]
        if int(rec["gen"]) % 2 == 0:
            self.begin_slot(slot)
        buf = self.slot_pixels(slot)
        page = np.frombuffer(buf, PAGE_DT, 1, 0)
        page[:] = np.zeros(1, PAGE_DT)
        page["magic"] = PAGE_MAGIC
        page["n_patches"] = n
        for k, v in header.items():
            page[k] = v
        np.frombuffer(buf, PATCH_DT, n, PAGE_DT.itemsize)[...] = table
        np.frombuffer(buf, np.uint8, th * row_bytes, hb).reshape(th, tw, 4)[...] = texels
        rec["format"] = ENUMS["pixel_format"]["rgba8_film"]
        rec["tex_w"], rec["tex_h"], rec["row_bytes"], rec["bytes"] = tw, th, row_bytes, th * row_bytes
        rec["kind"], rec["shot"] = ENUMS["canvas_kind"]["patch_page"], int(header["shot"])
        rec["page_header_bytes"], rec["n_patches"] = hb, n
        rec["world_x0"] = rec["world_y0"] = 0.0
        rec["scale"] = 1.0
        for k, v in fields.items():
            rec[k] = v
        rec["made_qpc"] = qpc()
        rec["state"] = ENUMS["canvas_state"]["complete"]
        rec["gen"] += 1

    def evict_slot(self, slot: int):
        rec = self.canvases[slot]
        if int(rec["gen"]) % 2 == 0:
            rec["gen"] += 1
        rec["state"] = ENUMS["canvas_state"]["evicted"]
        rec["gen"] += 1

    # ---- readers (tests, diagnostics, the worker reading the player block) -----------------------------------------

    def read_player(self, tries: int = 100) -> Optional[dict]:
        """A consistent copy of the player block as a dict, or None when the player is writing it right now."""
        return read_record(self.player, tries)


def read_record(rec, tries: int = 100) -> Optional[dict]:
    """Copy of a structured record under its sequence counter ('seq' or 'gen'), or None after `tries` torn copies."""
    key = "seq" if "seq" in rec.dtype.names else "gen"
    for _ in range(tries):
        s0 = int(rec[key])
        if s0 % 2:
            continue
        copy = rec.copy()
        if int(rec[key]) == s0:
            return {name: (copy[name].tolist() if hasattr(copy[name], "tolist") else copy[name]) for name in copy.dtype.names}
    return None
