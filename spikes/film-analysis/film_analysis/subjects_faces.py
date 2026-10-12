"""Faces, heads and people: raw detections per sampled frame -> per-shot tracks and the keep-clear map.

Detectors (research section 2.4, run on every sampled frame, every `sample_stride`-th frame of the file):
  anime face v1.4_s, anime head v2.0_s_yv11, anime person v1.3_s (deepghs, ONNX, GPU, batches of at most 8) and YuNet (OpenCV,
  CPU). Both families run on every frame; there is no anime / live-action switch, the planner reads `class`, `model` and `score`.
Positions are source stored pixels (x, y of the top-left corner, width, height).

Raw layer: subjects-raw.jsonl, one line per sampled frame:
  {"frame": 20004, "boxes": [["face", "anime-face-v1.4s", x, y, w, h, score], ...]}
Derived layer: subjects.json, per shot: the tracks (boxes of one class linked over the shot's samples by overlap) and a 64x36
keep-clear map (per cell the highest detection score covering it, over every sample of the shot, as one hex digit 0-f).
"""
from __future__ import annotations

import bisect
import json
import os
from typing import Optional

import numpy as np

CLASSES = ("face", "head", "person", "text")           # `text` is reserved: no text detector runs in v0
MODEL_CLASS = {"anime-face-v1.4s": "face", "anime-head-v2.0s-yv11": "head", "anime-person-v1.3s": "person", "yunet-2023mar": "face"}
GRID_W, GRID_H = 64, 36
TRACK_IOU = 0.3
DEDUPE_IOU = 0.6
RAW_NAME = "subjects-raw.jsonl"


def iou(a, b) -> float:
    ax1, ay1, bx1, by1 = a[0] + a[2], a[1] + a[3], b[0] + b[2], b[1] + b[3]
    iw = min(ax1, bx1) - max(a[0], b[0])
    ih = min(ay1, by1) - max(a[1], b[1])
    if iw <= 0 or ih <= 0:
        return 0.0
    inter = iw * ih
    return inter / (a[2] * a[3] + b[2] * b[3] - inter)


def append_raw(folder: str, records: list) -> None:
    """records: [(frame, [(class, model, x, y, w, h, score), ...]), ...] appended as JSON lines."""
    with open(os.path.join(folder, RAW_NAME), "a", encoding="utf-8") as f:
        for frame, boxes in records:
            f.write(json.dumps({"frame": int(frame), "boxes": [[c, m, round(x, 1), round(y, 1), round(w, 1), round(h, 1), round(s, 3)]
                                                                for c, m, x, y, w, h, s in boxes]}) + "\n")


def load_raw(folder: str) -> dict:
    """{frame: [box lists]} of every sampled frame stored so far (a later line for the same frame wins)."""
    path = os.path.join(folder, RAW_NAME)
    out = {}
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    r = json.loads(line)
                    out[r["frame"]] = r["boxes"]
    return out


def dedupe(boxes: list) -> list:
    """Within one frame and class, boxes from different models on the same thing collapse to the best-scoring one, which keeps
    the list of models that agreed. boxes: [class, model, x, y, w, h, score]."""
    out = []
    for cls in CLASSES:
        group = sorted((b for b in boxes if b[0] == cls), key=lambda b: -b[6])
        kept = []
        for b in group:
            for k in kept:
                if iou(b[2:6], k["box"][2:6]) >= DEDUPE_IOU:
                    if b[1] not in k["models"]:
                        k["models"].append(b[1])
                    break
            else:
                kept.append({"box": b, "models": [b[1]]})
        out += kept
    return out


def build_shot_subjects(samples: dict, first: int, last: int, width: int, height: int, sorted_frames: Optional[list] = None) -> dict:
    """samples: {frame: boxes} of every sampled frame; the shot takes those in [first, last]. sorted_frames: the sorted keys
    (pass it when calling for many shots)."""
    keys = sorted_frames if sorted_frames is not None else sorted(samples)
    frames = keys[bisect.bisect_left(keys, first): bisect.bisect_right(keys, last)]
    tracks: list = []          # each: {"class", "boxes": [...]}
    keep = np.zeros((GRID_H, GRID_W), np.uint8)
    for f in frames:
        det = dedupe(samples[f])
        used = set()
        for d in sorted(det, key=lambda d: -d["box"][6]):
            b = d["box"]
            best, best_i = None, TRACK_IOU
            for ti, t in enumerate(tracks):
                if t["class"] != b[0] or ti in used:
                    continue
                v = iou(b[2:6], t["boxes"][-1]["box"][2:6])
                if v >= best_i:
                    best, best_i = ti, v
            entry = {"frame": f, "x": b[2], "y": b[3], "w": b[4], "h": b[5], "score": b[6], "models": d["models"]}
            if best is None:
                tracks.append({"class": b[0], "boxes": [{"box": b, **entry}]})
                used.add(len(tracks) - 1)
            else:
                tracks[best]["boxes"].append({"box": b, **entry})
                used.add(best)
            _mark(keep, b[2], b[3], b[4], b[5], b[6], width, height)
    out_tracks = []
    for t in tracks:
        boxes = [{k: v for k, v in bx.items() if k != "box"} for bx in t["boxes"]]
        out_tracks.append({"class": t["class"], "first_frame": boxes[0]["frame"], "last_frame": boxes[-1]["frame"],
                           "mean_score": round(float(np.mean([bx["score"] for bx in boxes])), 3),
                           "models": sorted({m for bx in boxes for m in bx["models"]}), "boxes": boxes})
    return {"samples": len(frames), "tracks": out_tracks, "keepclear": _keep_rows(keep) if frames else None}


def _mark(keep: np.ndarray, x: float, y: float, w: float, h: float, score: float, width: int, height: int) -> None:
    c0 = max(0, int(np.floor(x / width * GRID_W)))
    c1 = min(GRID_W, int(np.ceil((x + w) / width * GRID_W)))
    r0 = max(0, int(np.floor(y / height * GRID_H)))
    r1 = min(GRID_H, int(np.ceil((y + h) / height * GRID_H)))
    if c1 > c0 and r1 > r0:
        v = max(1, min(15, int(round(score * 15))))
        keep[r0:r1, c0:c1] = np.maximum(keep[r0:r1, c0:c1], v)


def _keep_rows(keep: np.ndarray) -> list:
    return ["".join("0123456789abcdef"[int(v)] for v in row) for row in keep]


def decode_keepclear(rows: Optional[list]) -> np.ndarray:
    """The 36 strings back to a uint8 [36, 64] array of 0..15."""
    if not rows:
        return np.zeros((GRID_H, GRID_W), np.uint8)
    return np.array([[int(ch, 16) for ch in row] for row in rows], np.uint8)
