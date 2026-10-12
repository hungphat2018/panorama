"""Frame-level composition on 8-bit yuv420p frames (Y, U, V planes): blends, fades, wipes, slides, circles, flashes, solid bars.

A frame is a tuple (Y, U, V) of uint8 arrays with U and V at half size. Blending planes of a gamma-coded YUV picture is the same
linear mix FFmpeg's xfade does; limited-range black is (16, 128, 128) and white (235, 128, 128). Pure functions, no files.
"""
from __future__ import annotations

import numpy as np

Frame = tuple  # (Y, U, V)

BLACK = (16, 128, 128)
WHITE = (235, 128, 128)


def unpack(buf: bytes, w: int, h: int) -> Frame:
    a = np.frombuffer(buf, np.uint8)
    y = a[: w * h].reshape(h, w)
    u = a[w * h: w * h + (w // 2) * (h // 2)].reshape(h // 2, w // 2)
    v = a[w * h + (w // 2) * (h // 2):].reshape(h // 2, w // 2)
    return y, u, v


def pack(f: Frame) -> bytes:
    return f[0].tobytes() + f[1].tobytes() + f[2].tobytes()


def solid(w: int, h: int, color=BLACK) -> Frame:
    return (np.full((h, w), color[0], np.uint8), np.full((h // 2, w // 2), color[1], np.uint8), np.full((h // 2, w // 2), color[2], np.uint8))


def mix(a: Frame, b: Frame, ay, ac=None) -> Frame:
    """out = a*(1-alpha) + b*alpha. ay: scalar or (H,W) float alpha for luma; ac: same for chroma (default: ay or its 2x2 subsample)."""
    if ac is None:
        ac = ay[::2, ::2] if isinstance(ay, np.ndarray) else ay
    out = []
    for pa, pb, al in ((a[0], b[0], ay), (a[1], b[1], ac), (a[2], b[2], ac)):
        v = pa.astype(np.float32) * (1.0 - al) + pb.astype(np.float32) * al
        out.append(np.clip(v + 0.5, 0, 255).astype(np.uint8))
    return tuple(out)


def tint(a: Frame, color, alpha: float) -> Frame:
    h, w = a[0].shape
    return mix(a, solid(w, h, color), alpha)


# ------------------------------------------------------------------------------------------------ transitions
# t in (0, 1) is the progress of the transition at this frame: (j + 1) / (d + 1) for the j-th of d mixed frames.

def _grid(h: int, w: int):
    yy, xx = np.mgrid[0:h, 0:w]
    return yy.astype(np.float32), xx.astype(np.float32)


def transition_frame(kind: str, a: Frame, b: Frame, t: float) -> Frame:
    """One frame of a transition from picture a to picture b at progress t."""
    h, w = a[0].shape
    if kind == "fade":
        return mix(a, b, t)
    if kind == "fadeblack":
        k = solid(w, h, BLACK)
        return mix(a, k, 2 * t) if t < 0.5 else mix(k, b, 2 * t - 1)
    if kind == "fadewhite":
        k = solid(w, h, WHITE)
        return mix(a, k, 2 * t) if t < 0.5 else mix(k, b, 2 * t - 1)
    if kind == "fadegrays":
        # to mid-gray luma and neutral chroma, then to b
        k = solid(w, h, (128, 128, 128))
        return mix(a, k, 2 * t) if t < 0.5 else mix(k, b, 2 * t - 1)
    yy, xx = _grid(h, w)
    if kind == "wipeleft":       # b enters from the right edge
        m = (xx >= w * (1 - t)).astype(np.float32)
    elif kind == "wiperight":
        m = (xx < w * t).astype(np.float32)
    elif kind == "wipeup":
        m = (yy >= h * (1 - t)).astype(np.float32)
    elif kind == "wipedown":
        m = (yy < h * t).astype(np.float32)
    elif kind in ("circleopen", "circleclose"):
        r_max = float(np.hypot(w / 2, h / 2))
        d = np.hypot(xx - w / 2, yy - h / 2)
        m = (d < t * r_max).astype(np.float32) if kind == "circleopen" else (d > (1 - t) * r_max).astype(np.float32)
    elif kind in ("slideleft", "slideright"):
        s = int(round(t * w / 2)) * 2
        out = []
        for pa, pb in zip(a, b):
            pw = pa.shape[1]
            ss = s * pw // w
            o = np.empty_like(pa)
            if kind == "slideleft":      # b pushes a out to the left
                o[:, : pw - ss] = pa[:, ss:]
                o[:, pw - ss:] = pb[:, :ss]
            else:
                o[:, ss:] = pa[:, : pw - ss]
                o[:, :ss] = pb[:, pw - ss:]
            out.append(o)
        return tuple(out)
    else:
        raise ValueError(kind)
    return mix(a, b, m)


TRANSITION_KIND = {   # label kind for each xfade-like name
    "fade": "dissolve", "fadeblack": "fade_black", "fadewhite": "fade_white", "fadegrays": "dissolve",
    "wipeleft": "wipe", "wiperight": "wipe", "wipeup": "wipe", "wipedown": "wipe", "circleopen": "wipe", "circleclose": "wipe",
    "slideleft": "slide", "slideright": "slide",
}


# ------------------------------------------------------------------------------------------------ flashes
def flash_frames(kind: str, frames: list[Frame], rng: np.random.Generator) -> list[Frame]:
    """Replace consecutive frames by a flash of the given kind. Returns the new frames (same count) and does not change the inputs.

    kinds: white1, white2, white3 (full-frame white), bright2 (55% white), warm2 (orange tint 60%), ramp4 (25/60/60/25% white),
    local3 (a bright blob over about a third of the picture, like an explosion), strobe (white, 2 normal, white; needs 4 frames).
    """
    h, w = frames[0][0].shape
    out = list(frames)
    if kind.startswith("white"):
        n = int(kind[5:])
        return [solid(w, h, WHITE) for _ in range(n)]
    if kind == "bright2":
        return [tint(f, WHITE, 0.55) for f in frames[:2]]
    if kind == "warm2":
        return [tint(f, (150, 90, 175), 0.6) for f in frames[:2]]
    if kind == "ramp4":
        return [tint(f, WHITE, a) for f, a in zip(frames[:4], (0.25, 0.6, 0.6, 0.25))]
    if kind == "local3":
        cx, cy = rng.uniform(0.25, 0.75) * w, rng.uniform(0.25, 0.75) * h
        yy, xx = _grid(h, w)
        r = 0.32 * min(w, h)
        d = np.hypot(xx - cx, yy - cy)
        blob = np.clip(1.0 - d / r, 0, 1) ** 0.6 * 0.95
        return [mix(f, solid(w, h, WHITE), blob * s) for f, s in zip(frames[:3], (0.7, 1.0, 0.7))]
    if kind == "strobe":
        return [solid(w, h, WHITE), frames[1], frames[2], solid(w, h, WHITE)]
    raise ValueError(kind)


FLASH_KINDS = {"white1": 1, "white2": 2, "white3": 3, "bright2": 2, "warm2": 2, "ramp4": 4, "local3": 3, "strobe": 4}


# ------------------------------------------------------------------------------------------------ bars
def paint_rect_outside(f: Frame, rect: tuple[int, int, int, int], color=BLACK, noise: np.ndarray | None = None) -> Frame:
    """Paint everything outside rect = (x0, y0, x1, y1) with `color` (all four numbers even). noise: optional luma noise
    pattern (H,W) int8 added to the painted area only (grain in the bars)."""
    y, u, v = (p.copy() for p in f)
    h, w = y.shape
    x0, y0, x1, y1 = rect
    assert all(r % 2 == 0 for r in rect), rect
    mask = np.ones((h, w), bool)
    mask[y0:y1, x0:x1] = False
    y[mask] = color[0]
    if noise is not None:
        y[mask] = np.clip(y[mask].astype(np.int16) + noise[mask], 0, 255).astype(np.uint8)
    cm = mask[::2, ::2]
    u[cm] = color[1]
    v[cm] = color[2]
    return y, u, v
