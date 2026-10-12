"""Reading a finished (or running) content-model folder: the access layer for the planner and for evaluation scripts.

    from film_analysis.content_reader import ContentModel
    m = ContentModel.open(folder)
    m.times            frame times in seconds on the rebased timeline (float64, the performer's doubles)
    m.signal("dx")     any column of signals.bin as a numpy array (read-only view)
    m.shots            list of shot dicts (shots.json); m.shot_at(t) / m.shot_of_frame(i)
    m.crop_at(t)       the shot's crop rectangle [x0, y0, x1, y1) or None, extended by the text-in-bars amounts when asked
    m.frame_at(t)      index of the frame on screen at time t
    m.slide_runs       slide-paths.json runs; m.slide_position(t) -> (x, y) source px or None
    m.subjects_of_shot(i), m.keepclear(i)  -> faces / heads / people tracks and the 36x64 map
    m.subtitles(stream_index)  -> the track's document
All times are seconds on the rebased timeline, all positions source stored pixels (schema/common.schema.json).
"""
from __future__ import annotations

import bisect
import json
import os
from typing import Optional

import numpy as np

from . import subjects_faces as sf
from .signals_store import SignalsStore


def _load(path: str):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class ContentModel:
    def __init__(self, folder: str):
        self.folder = folder
        self.manifest = _load(os.path.join(folder, "content.json"))
        self.facts = _load(os.path.join(folder, "file-facts.json"))
        self.store = SignalsStore.open(folder)
        tb = self.facts["timeline"]["time_base"]
        pts = np.array(self.store.col("pts"))
        secs_abs = pts.astype(np.float64) * (tb[0] / tb[1])
        self.times = secs_abs - secs_abs[0]               # the rule of schema/common.schema.json, bit for bit
        self._shots_doc: Optional[dict] = None
        self._slides_doc: Optional[dict] = None
        self._subjects_doc: Optional[dict] = None
        self._starts: Optional[list] = None

    @classmethod
    def open(cls, folder: str) -> "ContentModel":
        return cls(folder)

    # -- frame table ---------------------------------------------------------------------------------------------------------
    @property
    def n_frames(self) -> int:
        return len(self.times)

    def frame_at(self, t: float) -> int:
        return max(0, int(np.searchsorted(self.times, t, side="right")) - 1)

    def signal(self, name: str) -> np.ndarray:
        return self.store.col(name)

    def scanned(self) -> np.ndarray:
        return self.store.col("scanned") > 0

    # -- shots ---------------------------------------------------------------------------------------------------------------
    @property
    def shots_doc(self) -> dict:
        if self._shots_doc is None:
            self._shots_doc = _load(os.path.join(self.folder, "shots.json"))
            self._starts = [s["first_frame"] for s in self._shots_doc["shots"]]
        return self._shots_doc

    @property
    def shots(self) -> list:
        return self.shots_doc["shots"]

    def shot_of_frame(self, i: int) -> dict:
        _ = self.shots_doc
        return self.shots[bisect.bisect_right(self._starts, i) - 1]

    def shot_at(self, t: float) -> dict:
        return self.shot_of_frame(self.frame_at(t))

    def crop_at(self, t: float, with_text: bool = True) -> Optional[list]:
        shot = self.shot_at(t)
        rect = shot["crop"]["rect"]
        if rect is None:
            return None
        if not with_text:
            return list(rect)
        ext = shot["bar_content"]["extend"]
        x0, y0, x1, y1 = rect
        return [x0 - ext["left"], y0 - ext["top"], x1 + ext["right"], y1 + ext["bottom"]]

    # -- slides --------------------------------------------------------------------------------------------------------------
    @property
    def slide_runs(self) -> list:
        if self._slides_doc is None:
            self._slides_doc = _load(os.path.join(self.folder, "slide-paths.json"))
        return self._slides_doc["runs"]

    def slide_position(self, t: float) -> Optional[tuple]:
        i = self.frame_at(t)
        for r in self.slide_runs:
            if r["first_frame"] <= i <= r["last_frame"]:
                j = i - r["first_frame"]
                return r["x"][j], r["y"][j]
        return None

    # -- subjects ------------------------------------------------------------------------------------------------------------
    def _subjects(self) -> dict:
        if self._subjects_doc is None:
            self._subjects_doc = {s["shot"]: s for s in _load(os.path.join(self.folder, "subjects.json"))["shots"]}
        return self._subjects_doc

    def subjects_of_shot(self, shot_id: int) -> dict:
        return self._subjects().get(shot_id, {"samples": 0, "tracks": [], "keepclear": None})

    def keepclear(self, shot_id: int) -> np.ndarray:
        return sf.decode_keepclear(self.subjects_of_shot(shot_id).get("keepclear"))

    # -- subtitles -----------------------------------------------------------------------------------------------------------
    def subtitles(self, stream_index: int) -> dict:
        return _load(os.path.join(self.folder, "subtitles", f"track-{stream_index}.json"))

    def subtitle_tracks(self) -> list:
        d = os.path.join(self.folder, "subtitles")
        return sorted(int(f[6:-5]) for f in os.listdir(d) if f.startswith("track-")) if os.path.isdir(d) else []
