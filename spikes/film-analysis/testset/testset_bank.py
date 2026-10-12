"""Shot bank for the constructed clips: stretches of real clips that contain no detector event, and how a picture sits on the canvas.

An excerpt picked from a safe stretch has no cut inside it (no detector fired within `margin` frames of any frame of the
excerpt), so the only boundaries in a constructed clip are the ones the generator makes. Signals come from
tools/testset/runs/signals/<clip>.npz (eval/run-signals.py).
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import numpy as np

import testset_common as tc

_EVAL = str(Path(__file__).resolve().parent.parent / "eval")
if _EVAL not in sys.path:
    sys.path.insert(0, _EVAL)
from fa_eval import cache  # noqa: E402  (cached signals live in the evaluation package)

CANVAS_W, CANVAS_H = 1920, 1080


def canvas_fit(info: dict) -> tuple[str, tuple[int, int, int, int]]:
    """(ffmpeg filter, picture rectangle x0,y0,x1,y1 on the 1920x1080 canvas) that fits the picture without changing its shape.

    Wider than 16:9 -> fit the width and pad top and bottom (a scope film in a 16:9 frame); narrower -> fit the height, pad left and
    right; 16:9 -> plain scale. Pad offsets are even so 4:2:0 chroma stays aligned and the rectangle is exact.
    """
    w, h = info["width"], info["height"]
    if w * 9 == h * 16:
        return ("scale=1920:1080:flags=lanczos" if (w, h) != (1920, 1080) else "null"), (0, 0, CANVAS_W, CANVAS_H)
    if w * 9 > h * 16:
        nh = int(round(CANVAS_W * h / w / 2)) * 2
        y0 = ((CANVAS_H - nh) // 2) // 2 * 2
        return f"scale=1920:{nh}:flags=lanczos,pad=1920:1080:0:{y0}:black", (0, y0, CANVAS_W, y0 + nh)
    nw = int(round(CANVAS_H * w / h / 2)) * 2
    x0 = ((CANVAS_W - nw) // 2) // 2 * 2
    return f"scale={nw}:1080:flags=lanczos,pad=1920:1080:{x0}:0:black", (x0, 0, x0 + nw, CANVAS_H)


@dataclass(frozen=True)
class Excerpt:
    clip: str
    start_frame: int          # index in the source clip's frame table
    frames: int               # output frames wanted (after any frame-rate change)
    t_start: float            # seconds on the source's rebased timeline
    src_frames: int           # how many source frames the excerpt spans


def unsafe_mask(clip_id: str, *, tn_single=0.02, tn_many=0.10, scdet=6.0) -> np.ndarray:
    sig = cache.load_npz("signals", clip_id)
    if "tn_single" not in sig or "scdet" not in sig:
        raise RuntimeError(f"no signals for {clip_id}: run eval/run-signals.py first")
    n = int(sig["n_frames"][0])
    bad = np.zeros(n, bool)
    bad |= sig["tn_single"][:n] > tn_single
    bad |= sig["tn_many"][:n] > tn_many
    sc = sig["scdet"]
    bad[: len(sc)] |= sc[:n] >= scdet
    # keyframes are not events by themselves; first and last frames are unsafe for lack of context
    bad[:3] = True
    bad[-3:] = True
    return bad


def safe_ranges(clip_id: str, min_len: int, margin: int = 12) -> list[tuple[int, int]]:
    """[a, b) stretches of at least min_len frames with no event within `margin` frames of any frame inside."""
    bad = unsafe_mask(clip_id)
    k = np.ones(2 * margin + 1)
    near = np.convolve(bad.astype(float), k, mode="same") > 0
    out, i, n = [], 0, len(near)
    while i < n:
        if not near[i]:
            j = i
            while j < n and not near[j]:
                j += 1
            if j - i >= min_len:
                out.append((i, j))
            i = j
        else:
            i += 1
    return out


def pick_excerpts(rng: np.random.Generator, clip_ids: list[str], n: int, frames: int | list[int], out_fps: float,
                  *, margin: int = 12, gap: int = 48) -> list[Excerpt]:
    """n non-overlapping excerpts spread over the clips, cycling through them so every clip contributes about the same.

    frames may be one length or one per excerpt (output frames). A source frame rate different from out_fps changes how many
    source frames an excerpt spans (src_frames = frames * src_fps / out_fps).
    """
    lens = [frames] * n if isinstance(frames, int) else list(frames)
    info = {c: tc.video_info(tc_clip_file(c)) for c in clip_ids}
    ranges: dict[str, list[tuple[int, int]]] = {}
    used: dict[str, list[tuple[int, int]]] = {c: [] for c in clip_ids}
    out: list[Excerpt] = []
    order = list(clip_ids)
    k = 0
    attempts = 0
    while len(out) < n and attempts < n * 60:
        attempts += 1
        c = order[k % len(order)]
        k += 1
        src_fps = float(Fraction(info[c]["fps"]))
        need = int(np.ceil(lens[len(out)] * src_fps / out_fps)) + 2
        if c not in ranges:
            ranges[c] = safe_ranges(c, need + 2 * gap, margin)
        cand = [(a, b) for a, b in ranges[c] if b - a >= need + 2 * gap]
        if not cand:
            continue
        a, b = cand[rng.integers(len(cand))]
        s = int(rng.integers(a + gap, b - need - gap + 1))
        if any(not (s + need + gap <= u0 or s >= u1 + gap) for u0, u1 in used[c]):
            continue
        used[c].append((s, s + need))
        t0 = float(tc.frame_seconds(tc_clip_file(c), [s])[0])
        out.append(Excerpt(c, s, lens[len(out)], t0, need))
    if len(out) < n:
        raise RuntimeError(f"only {len(out)} of {n} excerpts found in {clip_ids}")
    return out


def tc_clip_file(clip_id: str):
    return tc.clip_path(tc.clip_by_id(tc.load_manifest(), clip_id))


def clip_info(clip_id: str) -> dict:
    return tc.clip_by_id(tc.load_manifest(), clip_id)["video"]
