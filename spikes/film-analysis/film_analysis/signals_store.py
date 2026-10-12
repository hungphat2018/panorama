"""Raw per-frame signals (layer L0): one flat binary file of columns plus a small JSON header.

signals.bin holds every column back to back, each `n_frames` values long (rows of `width` values for vector columns),
little-endian, no padding between values, each column starting on an 8-byte boundary. signals.json lists the columns
(name, dtype, width, byte offset, group, unit, meaning) and the analyzer version of each group. The file is created at its
final size when the frame table is known, so a reader may map it while the scan is still filling it in; the `scanned`
column says which frames hold real values (1) and which are still zero (0).

Row i is frame i of the frame table (presentation order). A pair signal (motion) at row i describes the step from frame
i-1 to frame i; row 0 holds zeros.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Optional

import numpy as np

SCHEMA = "panorama.content.signals/0"
BIN_NAME = "signals.bin"
HEADER_NAME = "signals.json"


@dataclass(frozen=True)
class Column:
    name: str
    dtype: str            # numpy dtype string, little-endian
    group: str
    unit: str
    description: str
    width: int = 1        # values per frame


# The column list is part of the schema; adding a column is a version bump of its group.
def column_specs(with_dovi: bool) -> list:
    c = Column
    cols = [
        c("pts", "<i8", "frame-table", "ticks", "presentation timestamp of the frame in stream time-base ticks"),
        c("key", "u1", "frame-table", "flag", "1 when the packet that carries this frame is a keyframe"),
        c("scanned", "u1", "scan", "flag", "1 once the full scan has filled this frame's signals"),
        c("tn_single", "<f2", "shots", "probability", "TransNetV2 single-frame head: a cut lies between this frame and the next"),
        c("tn_many", "<f2", "shots", "probability", "TransNetV2 many-hot head: this frame is inside a transition"),
        c("scd", "<f2", "motion", "0-100", "FFmpeg scdet score of the step from the previous frame (480x270 luma)"),
        c("luma_mean", "u1", "motion", "code value", "mean luma of the 480x270 frame, 8-bit code value of the stream (video range stays 16-235; HDR not converted)"),
        c("luma_p95", "u1", "motion", "code value", "95th percentile of luma, same scale"),
        c("luma_std", "u1", "motion", "code value", "standard deviation of luma, same scale, clipped to 255"),
        c("pc_peak", "u1", "motion", "response*255", "phase-correlation peak response of the step from the previous frame"),
        c("dx", "<f4", "motion", "source px", "global shift of the picture content from the previous frame, x (stored pixels, right positive)"),
        c("dy", "<f4", "motion", "source px", "global shift from the previous frame, y (stored pixels, down positive)"),
        c("diff_before", "<f2", "motion", "8-bit levels", "mean absolute luma difference to the previous frame before aligning (core of the 480x270 frame)"),
        c("diff_after", "<f2", "motion", "8-bit levels", "mean absolute difference after shifting by (dx, dy)"),
        c("explained", "u1", "motion", "fraction*255", "share of textured pixels the single shift explains"),
        c("static", "u1", "motion", "fraction*255", "share of textured pixels that did not move although the shift is at least 1 px"),
        c("other", "u1", "motion", "fraction*255", "share of textured pixels explained neither by the shift nor by standing still"),
        c("crop_rect", "<i2", "crop", "source px", "cropdetect rectangle of this frame: x_first, x_last, y_first, y_last (inclusive); zeros when none", 4),
        c("bars_valid", "u1", "crop", "flag", "1 when crop_rect and the four edge_* values of this frame were measured (every scanned frame, and the keyframes of the pre-scan)"),
        c("edge_top", "<u2", "bars-text", "source px", "rows from the top edge to the first bright row (bright = black level + margin); equals the scan depth when none"),
        c("edge_bottom", "<u2", "bars-text", "source px", "rows from the bottom edge to the first bright row"),
        c("edge_left", "<u2", "bars-text", "source px", "columns from the left edge to the first bright column"),
        c("edge_right", "<u2", "bars-text", "source px", "columns from the right edge to the first bright column"),
    ]
    if with_dovi:
        cols.append(c("dv_scene_refresh", "u1", "dolby-vision", "flag", "scene_refresh_flag of the frame's Dolby Vision RPU"))
    return cols


def _aligned(n: int, to: int = 8) -> int:
    return (n + to - 1) // to * to


def layout(columns: list, n_frames: int) -> tuple:
    offsets, pos = {}, 0
    for col in columns:
        offsets[col.name] = pos
        pos = _aligned(pos + n_frames * col.width * np.dtype(col.dtype).itemsize)
    return offsets, pos


class SignalsStore:
    """The memory-mapped columns of one content model."""

    def __init__(self, folder: str, header: dict, mode: str):
        self.folder = folder
        self.header = header
        self.n_frames = int(header["n_frames"])
        self._cols = {c["name"]: c for c in header["columns"]}
        self._map = np.memmap(os.path.join(folder, BIN_NAME), dtype=np.uint8, mode=mode)
        self.mode = mode

    # -- creation / opening -------------------------------------------------------------------------------------------
    @classmethod
    def create(cls, folder: str, n_frames: int, columns: list, group_versions: dict) -> "SignalsStore":
        offsets, size = layout(columns, n_frames)
        header = {
            "schema": SCHEMA, "n_frames": n_frames, "byte_order": "little", "file": BIN_NAME,
            "columns": [{"name": c.name, "dtype": c.dtype, "width": c.width, "offset": offsets[c.name], "group": c.group,
                         "unit": c.unit, "description": c.description} for c in columns],
            "groups": {g: {"version": v} for g, v in group_versions.items()},
        }
        with open(os.path.join(folder, BIN_NAME), "wb") as f:
            f.truncate(size)
        _write_json_atomic(os.path.join(folder, HEADER_NAME), header)
        return cls(folder, header, "r+")

    @classmethod
    def open(cls, folder: str, writable: bool = False) -> "SignalsStore":
        with open(os.path.join(folder, HEADER_NAME), encoding="utf-8") as f:
            header = json.load(f)
        return cls(folder, header, "r+" if writable else "r")

    def compatible_with(self, columns: list, group_versions: dict, n_frames: int) -> bool:
        """True when this file has exactly these columns, these group versions and this frame count."""
        if self.n_frames != n_frames:
            return False
        have = [(c["name"], c["dtype"], c["width"]) for c in self.header["columns"]]
        want = [(c.name, np.dtype(c.dtype).str, c.width) for c in columns]
        have = [(n, np.dtype(d).str, w) for n, d, w in have]
        if have != want:
            return False
        return all(self.header["groups"].get(g, {}).get("version") == v for g, v in group_versions.items())

    # -- column access ------------------------------------------------------------------------------------------------
    def col(self, name: str) -> np.ndarray:
        """A view of one column: shape (n_frames,) or (n_frames, width)."""
        c = self._cols[name]
        dt = np.dtype(c["dtype"])
        count = self.n_frames * c["width"]
        arr = self._map[c["offset"]: c["offset"] + count * dt.itemsize].view(dt)
        return arr.reshape(self.n_frames, c["width"]) if c["width"] > 1 else arr

    def has(self, name: str) -> bool:
        return name in self._cols

    def names(self) -> list:
        return list(self._cols)

    def write(self, a: int, b: int, values: dict) -> None:
        """Stores `values[name]` (length b-a) into rows a..b-1."""
        for name, arr in values.items():
            if name in self._cols:
                self.col(name)[a:b] = arr

    def flush(self) -> None:
        if self.mode != "r":
            self._map.flush()

    def close(self) -> None:
        self.flush()
        del self._map


def _write_json_atomic(path: str, obj) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1)
    os.replace(tmp, path)


def write_json_atomic(path: str, obj, indent: Optional[int] = 1) -> None:
    """Writes JSON next to the target and renames it over: a reader never sees half a file."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=indent, ensure_ascii=False)
    os.replace(tmp, path)
