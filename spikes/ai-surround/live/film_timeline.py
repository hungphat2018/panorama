"""The film as the live worker sees it: identity, frame table, shots and crops, on the player's own timeline.

Frame table: the content model's (spikes/film-analysis, ADR 0027) when its folder is given, else made the same way from
the container's packet list (no decoding, a few seconds): either way frame i's time is the player's frame time bit for bit.
Shots: the content model's spans; without one there are none here and the analyser finds cuts itself (analyse_v0.py).
Crops: the cue sheet's, when the worker is given the same sheet the player plays; else the whole frame (the player's
default). The crop decides which source pixels are picture; everything outside it is side.
"""
from __future__ import annotations

import bisect
import json
import os
import sys
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(REPO, "spikes", "film-analysis"))
from film_analysis import file_key, frame_table, media_probe  # noqa: E402
from film_analysis.content_reader import ContentModel  # noqa: E402


@dataclass
class ShotSpan:
    shot_id: int
    first: int                          # first frame
    n: int                              # frames
    from_content_model: bool


@dataclass
class Film:
    path: str
    key: dict                           # film_analysis.file_key.sample_key
    key_folder: str                     # its cache folder name
    size: int
    src_w: int
    src_h: int
    stream_index: int
    container_start_s: float            # format start_time: ffmpeg's -ss is relative to it
    tb_num: int
    tb_den: int
    pts: np.ndarray                     # int64 ticks
    keyframe: np.ndarray                # bool
    times: np.ndarray                   # float64 seconds, rebased: the player's frame times
    origin_abs_s: float                 # pts[0] * tb
    last_frame_s: float                 # how long the last frame lasts
    matrix: str                         # ffmpeg in_color_matrix
    in_range: str                       # ffmpeg in_range
    fps: float
    content: Optional[ContentModel] = None
    spans: Optional[list] = None        # ShotSpan per content-model shot, None without a content model
    cue_starts: list = field(default_factory=list)
    cue_crops: list = field(default_factory=list)

    @property
    def n_frames(self) -> int:
        return len(self.times)

    @property
    def duration_s(self) -> float:
        return float(self.times[-1]) + self.last_frame_s

    def frame_at(self, t: float) -> int:
        """The frame on screen at film time t (the last one whose time is at most t)."""
        return max(0, int(np.searchsorted(self.times, t + 1e-6, side="right")) - 1)

    def cut_time(self, first: int) -> float:
        """Cue-sheet start of a shot beginning at frame `first`: halfway between the frames (0 for frame 0)."""
        if first <= 0:
            return 0.0
        if first >= self.n_frames:
            return self.duration_s
        return float((self.times[first - 1] + self.times[first]) / 2.0)

    def crop_at(self, t: float) -> tuple:
        """The picture's crop in source px [x0, y0, x1, y1) for the shot the cue sheet has at film time t."""
        if self.cue_starts:
            i = bisect.bisect_right(self.cue_starts, t + 1e-9) - 1
            if i >= 0 and self.cue_crops[i] is not None:
                return tuple(float(v) for v in self.cue_crops[i])
        return (0.0, 0.0, float(self.src_w), float(self.src_h))

    def span_of_frame(self, i: int) -> Optional[ShotSpan]:
        if not self.spans:
            return None
        if len(getattr(self, "_firsts", ())) != len(self.spans):
            self._firsts = [s.first for s in self.spans]
        return self.spans[bisect.bisect_right(self._firsts, i) - 1]


def _colour_options(stream: dict, height: int) -> tuple:
    """(in_color_matrix, in_range) for ffmpeg's scale: the file's tags, else what the player's libplacebo assumes for an
    untagged file (BT.709 from 720 rows up, BT.601 below; limited range)."""
    space = (stream.get("color_space") or "").lower()
    matrix = {"bt709": "bt709", "bt470bg": "bt601", "smpte170m": "bt601", "bt2020nc": "bt2020", "bt2020c": "bt2020"}.get(space)
    if matrix is None:
        matrix = "bt709" if height >= 720 else "bt601"
    rng = "pc" if (stream.get("color_range") or "").lower() in ("pc", "jpeg") else "tv"
    return matrix, rng


def _read_cues(path: str) -> tuple:
    with open(path, encoding="utf-8") as f:
        sheet = json.load(f)
    starts, crops = [], []
    for s in sheet.get("shots", []):
        starts.append(float(s["start"]))
        crops.append(s.get("crop"))
    return starts, crops


def open_film(path: str, content_dir: Optional[str] = None, cues_path: Optional[str] = None) -> Film:
    info = media_probe.probe_container(path)
    st = media_probe.pick_video_stream(info)
    tb = st["time_base"].split("/")
    tb_num, tb_den = int(tb[0]), int(tb[1])
    w, h = int(st["width"]), int(st["height"])
    start = float(info.get("format", {}).get("start_time") or 0.0)
    matrix, rng = _colour_options(st, h)
    key = file_key.sample_key(path)
    content = None
    if content_dir:
        content = ContentModel.open(content_dir)
        mkey = content.manifest["file"]["key"]
        if not file_key.same_file(mkey, key):
            raise SystemExit(f"{content_dir} is the content model of another file ({mkey['sample_sha256'][:12]}..., "
                             f"{mkey['size_bytes']} bytes); this film is {key['sample_sha256'][:12]}..., {key['size_bytes']} bytes")
        pts = np.asarray(content.signal("pts"), dtype=np.int64)
        keyframe = np.asarray(content.signal("key")) > 0
        tl = content.facts["timeline"]
        last_s = float(tl.get("last_frame_duration_s") or 0.0)
        fps = float(tl.get("frame_rate", {}).get("fps_avg") or 24.0)
    else:
        table = frame_table.table_from_packets(media_probe.video_packets(path, int(st["index"])), tb_num, tb_den)
        pts, keyframe, last_s = table.pts, table.key, table.last_frame_duration_s()
        fps = (len(pts) - 1) / max(1e-9, float(pts[-1] - pts[0]) * tb_num / tb_den) if len(pts) > 1 else 24.0
    tbf = tb_num / tb_den                                           # one double division, as the player
    abs_s = pts.astype(np.float64) * tbf
    film = Film(path=path, key=key, key_folder=file_key.cache_folder_name(key), size=os.path.getsize(path), src_w=w, src_h=h,
                stream_index=int(st["index"]), container_start_s=start, tb_num=tb_num, tb_den=tb_den, pts=pts,
                keyframe=keyframe, times=abs_s - abs_s[0], origin_abs_s=float(abs_s[0]), last_frame_s=last_s, matrix=matrix,
                in_range=rng, fps=fps, content=content)
    if content is not None:
        film.spans = [ShotSpan(int(s["id"]), int(s["first_frame"]), int(s["frames"]), True) for s in content.shots]
    if cues_path:
        film.cue_starts, film.cue_crops = _read_cues(cues_path)
    return film
