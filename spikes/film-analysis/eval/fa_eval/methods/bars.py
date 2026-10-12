"""Black-bar methods of the research pick: FFmpeg cropdetect per frame, a calibrated black level, per-shot union snapped to the file's
crop vocabulary (ADR 0010 refined by 0024), and a bar-strip max-luma guard for burned-in text.

Rectangles: (x0, y0, x1, y1) half-open ints. cropdetect reports inclusive x1..x2 / y1..y2; converted here.
Policies (names used in the report):
  S0  raw per frame (hold the last rectangle when a frame gives none)
  S1  running union (cropdetect reset=0 behaviour: only ever grows)
  S2  per-shot union of the frame rectangles, snapped to the smallest vocabulary mode that contains it (the pick)
Everything here is deterministic numpy on cached per-frame data.
"""
from __future__ import annotations

import re
import subprocess
import tempfile
from collections import Counter
from pathlib import Path

import numpy as np

import fa_eval  # noqa: F401
from fa_eval.decode import THREADS
from testset_common import NVDEC_CODECS, popen, video_info

MIN_PICTURE = 32          # a detected rectangle smaller than this in either direction counts as "no picture seen" (black frame)


def esc(p) -> str:
    return str(p).replace("\\", "/").replace(":", "\\:")


def cropdetect_frames(path, limit: float, *, gpu: bool | None = None) -> np.ndarray:
    """Per-frame cropdetect (round=2, reset=1) -> int32 [N,4] half-open rectangles (x0,y0,x1,y1). Frames with no result keep (0,0,0,0)."""
    info = video_info(path)
    use_gpu = (info["codec"] in NVDEC_CODECS) if gpu is None else gpu
    fmt = "p010le" if info["pix_fmt"] and "10" in info["pix_fmt"] else "nv12"
    for g in ([True, False] if use_gpu else [False]):
        with tempfile.TemporaryDirectory() as td:
            meta = Path(td) / "m.txt"
            pre = f"hwdownload,format={fmt}," if g else ""
            hw = ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"] if g else ["-threads", THREADS]
            fs = f"[0:v]{pre}cropdetect=limit={limit:.5f}:round=2:reset=1:skip=0,metadata=print:file='{esc(meta)}'[out]"
            cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", *hw, "-i", str(path), "-an", "-sn", "-map", "0:v:0", "-fps_mode", "passthrough",
                   "-filter_complex", fs, "-map", "[out]", "-f", "null", "-"]
            r = subprocess.run(cmd, capture_output=True, creationflags=0x4000)
            if r.returncode != 0 or not meta.exists():
                if g:
                    continue
                raise RuntimeError(f"cropdetect failed: {r.stderr.decode('utf-8', 'replace')[-400:]}")
            txt = meta.read_text()
        frames = [int(x) for x in re.findall(r"(?m)^frame:(\d+)", txt)]
        keys = {k: [int(x) for x in re.findall(rf"lavfi\.cropdetect\.{k}=(-?\d+)", txt)] for k in ("x1", "x2", "y1", "y2")}
        n = max(frames) + 1 if frames else 0
        out = np.zeros((n, 4), np.int32)
        m = min(len(frames), *(len(v) for v in keys.values()))
        idx = np.array(frames[:m], dtype=np.int64)
        out[idx, 0] = keys["x1"][:m]
        out[idx, 1] = keys["y1"][:m]
        out[idx, 2] = np.array(keys["x2"][:m]) + 1
        out[idx, 3] = np.array(keys["y2"][:m]) + 1
        return out
    raise RuntimeError("unreachable")


def valid_rects(rect: np.ndarray, w: int, h: int) -> np.ndarray:
    """Frames whose rectangle looks like a picture: big enough and inside the frame."""
    return ((rect[:, 2] - rect[:, 0] >= MIN_PICTURE) & (rect[:, 3] - rect[:, 1] >= MIN_PICTURE) & (rect[:, 0] >= 0) & (rect[:, 1] >= 0)
            & (rect[:, 2] <= w) & (rect[:, 3] <= h))


def keyframe_luma(path, max_frames: int = 48) -> list[np.ndarray]:
    """Gray frames of up to max_frames keyframes, evenly spread (full resolution, for the black-level estimate)."""
    info = video_info(path)
    w, h = info["width"], info["height"]
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-threads", THREADS, "-skip_frame", "nokey", "-i", str(path), "-an", "-sn", "-map", "0:v:0",
           "-fps_mode", "passthrough", "-vf", "format=gray", "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    p = popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    frames = []
    try:
        while True:
            buf = p.stdout.read(w * h)
            if len(buf) < w * h:
                break
            frames.append(np.frombuffer(buf, np.uint8).reshape(h, w).copy())
    finally:
        p.kill()
        p.wait()
    if len(frames) > max_frames:
        frames = [frames[i] for i in np.linspace(0, len(frames) - 1, max_frames).astype(int)]
    return frames


def estimate_black_level(frames: list[np.ndarray], default: float = 16.0) -> dict:
    """Bar luma of the file: median of the outermost 6 rows / columns of keyframes whose edge strip is dark and flat. Falls back to `default`
    (16 for limited range) when no frame has a flat dark edge (no bars, or bars hidden by a bright picture)."""
    cands = []
    for g in frames:
        h, w = g.shape
        for strip in (g[:6], g[-6:], g[:, :6], g[:, -6:]):
            m = float(np.median(strip))
            if m < 40 and float(strip.std()) < 4.0:
                cands.append(m)
    if len(cands) >= 4:
        level = float(np.median(cands))
        return {"level": level, "from_frames": len(cands), "calibrated": True}
    return {"level": default, "from_frames": len(cands), "calibrated": False}


def limit_for(level: float, margin: float = 3.0) -> float:
    """cropdetect limit (0..1) = the bar level + margin codes, kept in a sane range."""
    return float(np.clip((level + margin) / 255.0, 0.03, 0.12))


# ------------------------------------------------------------------------------------------------ policies
def full_rect(w: int, h: int) -> np.ndarray:
    return np.array([0, 0, w, h], np.int32)


def policy_s0(rect: np.ndarray, ok: np.ndarray, w: int, h: int) -> np.ndarray:
    """Raw per frame; frames without a valid rectangle keep the previous one (the full frame before the first valid one)."""
    D = np.zeros_like(rect)
    cur = full_rect(w, h)
    for i in range(len(rect)):
        if ok[i]:
            cur = rect[i]
        D[i] = cur
    return D


def policy_s1(rect: np.ndarray, ok: np.ndarray, w: int, h: int) -> np.ndarray:
    """Running union: the rectangle only ever grows (cropdetect with reset=0)."""
    D = np.zeros_like(rect)
    cur = None
    for i in range(len(rect)):
        if ok[i]:
            cur = rect[i].copy() if cur is None else np.array([min(cur[0], rect[i, 0]), min(cur[1], rect[i, 1]), max(cur[2], rect[i, 2]), max(cur[3], rect[i, 3])], np.int32)
        D[i] = full_rect(w, h) if cur is None else cur
    return D


def _union(rs: np.ndarray) -> np.ndarray:
    return np.array([rs[:, 0].min(), rs[:, 1].min(), rs[:, 2].max(), rs[:, 3].max()], np.int32)


def _contains(a: np.ndarray, b: np.ndarray, tol: int) -> bool:
    return a[0] <= b[0] + tol and a[1] <= b[1] + tol and a[2] >= b[2] - tol and a[3] >= b[3] - tol


def _area(r: np.ndarray) -> int:
    return int((r[2] - r[0]) * (r[3] - r[1]))


def shot_unions(rect: np.ndarray, ok: np.ndarray, starts: np.ndarray, n: int, min_agree: float = 0.2) -> list[dict]:
    """Per shot [a, b): union of its valid frame rectangles, and the share of frames within 4 px of that union."""
    edges = np.concatenate([np.asarray(starts, dtype=np.int64), [n]])
    out = []
    for a, b in zip(edges[:-1], edges[1:]):
        idx = np.arange(a, b)[ok[a:b]]
        if len(idx) == 0:
            out.append({"a": int(a), "b": int(b), "union": None, "agree": 0.0})
            continue
        u = _union(rect[idx])
        agree = float(np.mean([np.abs(rect[j] - u).max() <= 4 for j in idx]))
        out.append({"a": int(a), "b": int(b), "union": u, "agree": agree})
    return out


def vocabulary(shots: list[dict], grid: int = 8, share: float = 0.03, min_agree: float = 0.2) -> list[np.ndarray]:
    """The file's few real crop modes: shot unions grouped on an 8 px grid, frame-weighted, keeping groups with >= share of the frames."""
    c: Counter = Counter()
    rep: dict = {}
    for s in shots:
        u = s["union"]
        if u is not None and s["agree"] >= min_agree:
            k = tuple((u // grid) * grid)
            c[k] += s["b"] - s["a"]
            rep[k] = u.copy() if k not in rep else np.array([min(rep[k][0], u[0]), min(rep[k][1], u[1]), max(rep[k][2], u[2]), max(rep[k][3], u[3])], np.int32)
    tot = sum(c.values())
    return [rep[k] for k, v in c.most_common() if tot and v / tot >= share]


def policy_s2(rect: np.ndarray, ok: np.ndarray, starts: np.ndarray, w: int, h: int, *, min_agree: float = 0.2, snap_tol: int = 6) -> tuple[np.ndarray, list, np.ndarray]:
    """Per-shot union snapped to the smallest vocabulary mode containing it; a shot with no valid frame holds the previous crop.
    Returns (D per frame, vocabulary, per-shot crops)."""
    n = len(rect)
    shots = shot_unions(rect, ok, starts, n, min_agree)
    vocab = vocabulary(shots, min_agree=min_agree)
    D = np.zeros_like(rect)
    prev = full_rect(w, h)
    crops = []
    for s in shots:
        u = s["union"]
        if u is None or s["agree"] < min_agree:
            cur = prev.copy()
        else:
            cands = [v for v in vocab if _contains(v, u, snap_tol)]
            cur = min(cands, key=_area).copy() if cands else u
        D[s["a"]:s["b"]] = cur
        crops.append(cur)
        prev = cur
    return D, vocab, np.array(crops)


# ------------------------------------------------------------------------------------------------ text guard
def luma_profiles(path, *, h: int | None = None, w: int | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Per frame, the maximum luma of every row [N,H] and every column [N,W] (8-bit scale), from full-resolution gray frames."""
    info = video_info(path)
    w, h = info["width"], info["height"]
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-threads", THREADS, "-i", str(path), "-an", "-sn", "-map", "0:v:0", "-fps_mode", "passthrough",
           "-vf", "format=gray", "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    p = popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    rows, cols = [], []
    try:
        while True:
            buf = p.stdout.read(w * h * 16)
            if not buf:
                break
            k = len(buf) // (w * h)
            if k == 0:
                break
            a = np.frombuffer(buf, np.uint8, k * w * h).reshape(k, h, w)
            rows.append(a.max(axis=2))
            cols.append(a.max(axis=1))
    finally:
        p.kill()
        p.wait()
    return np.concatenate(rows, 0), np.concatenate(cols, 0)


def guard_frames(rowmax: np.ndarray, colmax: np.ndarray, crop: np.ndarray, level: float, *, thr: int = 40, persist: int = 3, margin: int = 2) -> tuple[np.ndarray, np.ndarray]:
    """For every frame the text found in the bar strips outside that frame's crop.

    A strip pixel row/column is bright when its maximum luma is at least level + thr. The rows right next to the picture (margin) are skipped
    (codec ringing). Bright strips must last `persist` frames in a row. Returns (flagged [N] bool, need [N,4] int32 rectangle that must stay:
    the crop grown to contain every bright strip row/column; equal to the crop when nothing is flagged)."""
    n, h = rowmax.shape
    w = colmax.shape[1]
    lim = level + thr
    raw = np.zeros(n, bool)
    need = crop.copy()
    for i in range(n):
        x0, y0, x1, y1 = crop[i]
        grow = [x0, y0, x1, y1]
        hit = False
        top = np.nonzero(rowmax[i, :max(y0 - margin, 0)] >= lim)[0]
        if top.size:
            grow[1] = int(top.min())
            hit = True
        bot = np.nonzero(rowmax[i, min(y1 + margin, h):] >= lim)[0]
        if bot.size:
            grow[3] = int(y1 + margin + bot.max() + 1)
            hit = True
        lef = np.nonzero(colmax[i, :max(x0 - margin, 0)] >= lim)[0]
        if lef.size:
            grow[0] = int(lef.min())
            hit = True
        rig = np.nonzero(colmax[i, min(x1 + margin, w):] >= lim)[0]
        if rig.size:
            grow[2] = int(x1 + margin + rig.max() + 1)
            hit = True
        raw[i] = hit
        need[i] = grow
    # persistence: keep a flag only inside runs of at least `persist` flagged frames
    flagged = np.zeros(n, bool)
    i = 0
    while i < n:
        if raw[i]:
            j = i
            while j + 1 < n and raw[j + 1]:
                j += 1
            if j - i + 1 >= persist:
                flagged[i:j + 1] = True
            i = j + 1
        else:
            i += 1
    need[~flagged] = crop[~flagged]
    return flagged, need


def apply_guard_per_shot(crop: np.ndarray, need: np.ndarray, flagged: np.ndarray, starts: np.ndarray) -> np.ndarray:
    """Crop changes only at cuts: inside a shot the crop grows to the union of everything the guard needed there."""
    n = len(crop)
    D = crop.copy()
    edges = np.concatenate([np.asarray(starts, dtype=np.int64), [n]])
    for a, b in zip(edges[:-1], edges[1:]):
        f = flagged[a:b]
        if f.any():
            u = _union(need[a:b][f])
            base = crop[a]
            D[a:b] = np.array([min(base[0], u[0]), min(base[1], u[1]), max(base[2], u[2]), max(base[3], u[3])], np.int32)
    return D
