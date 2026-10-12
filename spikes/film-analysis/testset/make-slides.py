"""Constructed clips with exact camera-slide paths: windows moved over real still frames by known sub-pixel amounts.

Usage: python -I make-slides.py anime|sol|live

Each clip holds 13 scenarios of about 100-140 frames, joined by hard cuts (exact). A scenario moves a window across a real frame of
the film (a 2x larger picture is not needed: the window is smaller than the frame) along a known path and re-encodes the result
like a release (x265 CRF 22 10-bit for anime, x264 CRF 18 for live action). Paths use every real-world profile: constant speed,
ease-in/ease-out (smoothstep), ease-in only, whip pan, a slow creep (0.35 px per frame), a slide on twos (a new position every second
frame, as anime often draws it). Class of each scenario (truth for the purity metric):
  pure       the whole picture slides as one sheet: h-ease, v-const, diag-in, slow, whip, twos
  layered    a static plate / burned-in subtitles stay still over the sliding picture: plate, subs
  parallax   the lower 35% moves at 1.6x the speed of the rest
  subject    a cut-out patch moves over the picture on its own while the picture slides
  nonrigid   zoom in 8% / rotation by 3 degrees (not a translation)
  none       a still picture (control)
Labels: labels/<clip>/slides.json (per scenario: class, frame range of the motion, the exact path: cumulative content displacement of the
background in px, positive x = content moves right; origin = first frame of the run) and shots.json (the joins).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import testset_common as tc  # noqa: E402

FPS = "24000/1001"
CONFIG = {
    # clip: (output size, [(source clip, time s)...] base frames, encoder)
    "anime": ((1280, 720), [("anime-nama-anaru-ep2", 0.0), ("anime-nama-anaru-ep2", 120.0), ("anime-nama-anaru-ep2", 480.0), ("anime-nama-anaru-ep2", 940.0)],
              dict(codec="libx265", crf=22, preset="fast", pix_fmt="yuv420p10le")),
    "sol": ((1280, 720), [("sol-levante-w1", 0.5), ("sol-levante-w1", 4.5), ("sol-levante-w2", 0.5), ("sol-levante-w2", 5.0)],
            dict(codec="libx265", crf=22, preset="fast", pix_fmt="yuv420p10le")),
    "live": ((1920, 1080), [("meridian-4k", 60.0), ("meridian-4k", 180.0), ("meridian-4k", 5.0), ("meridian-4k", 420.0)],
             dict(codec="libx264", crf=18, preset="medium", pix_fmt="yuv420p")),
}
# scenario: (name, class, lead-in still frames, moving frames, lead-out still frames)
SCENARIOS = [
    ("h-ease", "pure", 24, 72, 24), ("v-const", "pure", 12, 60, 12), ("diag-in", "pure", 12, 60, 12), ("slow", "pure", 12, 96, 12),
    ("whip", "pure", 12, 24, 24), ("twos", "pure", 12, 60, 12), ("plate", "layered", 12, 60, 12), ("subs", "layered", 12, 60, 12),
    ("parallax", "parallax", 12, 60, 12), ("subject", "subject", 12, 60, 12), ("zoom", "nonrigid", 12, 60, 12), ("rotate", "nonrigid", 12, 60, 12),
    ("still", "none", 0, 48, 0),
]


def grab_still(clip_id: str, t: float) -> np.ndarray:
    entry = tc.clip_by_id(tc.load_manifest(), clip_id)
    path = tc.clip_path(entry)
    r = tc.run(["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", path, "-frames:v", "1", "-f", "image2pipe", "-c:v", "png", "-"])
    return cv2.imdecode(np.frombuffer(r.stdout, np.uint8), cv2.IMREAD_COLOR)


def profile(kind: str, n: int) -> np.ndarray:
    """Fraction of the total travel reached after each of n moving frames (frame 0 is 0 = the first position)."""
    u = np.arange(n) / (n - 1)
    if kind in ("h-ease", "subs", "plate"):
        return u * u * (3 - 2 * u)                      # smoothstep: ease in and out
    if kind == "diag-in":
        return u * u                                    # ease in only
    if kind == "whip":
        return u ** 3
    if kind == "twos":
        k = np.arange(n) // 2 * 2
        return k / (n - 1)                              # new position every second frame
    return u                                            # constant speed


TRAVEL = {   # total content travel in px at 1280 wide (scaled for other sizes): (dx, dy), as window offset (camera moves +)
    "h-ease": (480, 0), "v-const": (0, 240), "diag-in": (420, 210), "slow": (34, 34), "whip": (520, 0), "twos": (180, 0),
    "plate": (240, 0), "subs": (240, 0), "parallax": (200, 0), "subject": (200, 0), "zoom": (60, 0), "rotate": (60, 0), "still": (0, 0),
}


def window(S: np.ndarray, ox: float, oy: float, size: tuple[int, int], extra: np.ndarray | None = None) -> np.ndarray:
    W, H = size
    M = np.float32([[1, 0, -ox], [0, 1, -oy]])
    return cv2.warpAffine(S, M, (W, H), flags=cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_REPLICATE)


def render_scenario(S: np.ndarray, name: str, size: tuple[int, int], n_move: int, rng: np.random.Generator) -> tuple[list[np.ndarray], np.ndarray, np.ndarray]:
    """(frames, background window offsets [n,2], background content displacement [n,2]) for the moving part of a scenario."""
    W, H = size
    sc = W / 1280.0
    dx_t, dy_t = (v * sc for v in TRAVEL[name])
    margin_x, margin_y = S.shape[1] - W, S.shape[0] - H
    # start offset so the whole path stays inside the still (the camera moves towards +x / +y)
    ox0 = float(rng.uniform(0.05, 0.25) * max(margin_x - dx_t, 1))
    oy0 = float(rng.uniform(0.05, 0.25) * max(margin_y - dy_t, 1))
    prof = profile(name, n_move)
    offs = np.stack([ox0 + dx_t * prof, oy0 + dy_t * prof], axis=1)
    frames = []
    patch = None
    if name == "subject":
        px, py, pw, ph = int(S.shape[1] * 0.62), int(S.shape[0] * 0.30), int(0.16 * W), int(0.30 * H)
        patch = S[py:py + ph, px:px + pw].copy()
        mask = np.zeros((ph, pw), np.float32)
        cv2.ellipse(mask, (pw // 2, ph // 2), (pw // 2 - 2, ph // 2 - 2), 0, 0, 360, 1.0, -1)
        mask = cv2.GaussianBlur(mask, (0, 0), 3)[..., None]
    for i, (ox, oy) in enumerate(offs):
        if name == "zoom":
            z = 1.0 + 0.08 * prof[i]
            M = np.float32([[z, 0, -ox - (z - 1) * W / 2], [0, z, -oy - (z - 1) * H / 2]])
            f = cv2.warpAffine(S, M, (W, H), flags=cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_REPLICATE)
        elif name == "rotate":
            ang = 3.0 * prof[i]
            R = cv2.getRotationMatrix2D((W / 2 + ox, H / 2 + oy), ang, 1.0)
            R[0, 2] -= ox
            R[1, 2] -= oy
            f = cv2.warpAffine(S, R, (W, H), flags=cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_REPLICATE)
        else:
            f = window(S, ox, oy, size)
        if name == "parallax":
            fg = window(S, ox0 + (ox - ox0) * 1.6, oy, size)
            cut = int(0.65 * H)
            f = f.copy()
            f[cut:] = fg[cut:]
        elif name == "plate":
            f = f.copy()
            x0, y0, x1, y1 = int(0.06 * W), int(0.62 * H), int(0.36 * W), int(0.86 * H)
            cv2.rectangle(f, (x0, y0), (x1, y1), (245, 245, 245), -1)
            cv2.rectangle(f, (x0, y0), (x1, y1), (30, 30, 30), 3)
            cv2.putText(f, "TITLE PLATE", (x0 + 14, y0 + int((y1 - y0) * 0.6)), cv2.FONT_HERSHEY_DUPLEX, 0.0013 * W, (20, 20, 20), 3, cv2.LINE_AA)
        elif name == "subs":
            f = f.copy()
            for k, line in enumerate(("Burned-in subtitles stay where they are", "while the picture slides behind them")):
                y = int(H * (0.86 + 0.07 * k))
                cv2.putText(f, line, (int(0.12 * W), y), cv2.FONT_HERSHEY_SIMPLEX, 0.0012 * W, (0, 0, 0), 5, cv2.LINE_AA)
                cv2.putText(f, line, (int(0.12 * W), y), cv2.FONT_HERSHEY_SIMPLEX, 0.0012 * W, (255, 255, 255), 2, cv2.LINE_AA)
        elif name == "subject":
            f = f.copy()
            x = int(0.78 * W - i * 0.0035 * W)                 # walks left on screen while the picture slides right
            y = int(0.52 * H)
            x0, y0 = max(x, 0), y
            roi = f[y0:y0 + patch.shape[0], x0:x0 + patch.shape[1]]
            m = mask[:roi.shape[0], :roi.shape[1]]
            roi[:] = (patch[:roi.shape[0], :roi.shape[1]] * m + roi * (1 - m)).astype(np.uint8)
        frames.append(f)
    disp = -(offs - offs[0])                                    # content moves opposite to the window; positive = content moves right/down
    return frames, offs, disp


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("variant", choices=list(CONFIG))
    ap.add_argument("--seed", type=int, default=3)
    a = ap.parse_args()
    size, bases, enc = CONFIG[a.variant]
    W, H = size
    rng = np.random.default_rng(a.seed)
    stills = [grab_still(c, t) for c, t in bases]
    # stills smaller than the travel would run out of picture: use them at native size, never scaled
    cid = f"ctor-slides-{a.variant}"
    out = tc.CLIPS / f"{cid}.mkv"
    runs, boundaries, pos, nframes = [], [], 0, 0
    with tc.VideoWriter(out, W, H, FPS, in_pix_fmt="bgr24", **enc) as vw:
        for k, (name, cls, lead, nmove, tail) in enumerate(SCENARIOS):
            S = stills[k % len(stills)]
            if k > 0:
                boundaries.append({"frame": pos, "kind": "hard", "span": None, "layout_change_point": True})
            frames, offs, disp = render_scenario(S, name, size, nmove, rng)
            seq = [frames[0]] * lead + frames + [frames[-1]] * tail
            for f in seq:
                vw.write(np.ascontiguousarray(f).tobytes())
            a0, a1 = pos + lead, pos + lead + nmove
            if cls not in ("none", "nonrigid"):          # a zoom or a rotation has no single translation path
                runs.append({"frames": [a0, a1], "class": cls, "kind": name, "path_xy": [[round(float(x), 5), round(float(y), 5)] for x, y in disp],
                             "still_before": [pos, a0], "still_after": [a1, pos + len(seq)], "max_step_px": round(float(np.hypot(*np.diff(disp, axis=0).T).max()), 3)})
            else:
                runs.append({"frames": [a0, a1], "class": cls, "kind": name, "path_xy": None})
            pos += len(seq)
            nframes += len(seq)
    facts = tc.describe_clip(out)
    if facts["video"]["frames"] != pos:
        raise RuntimeError(f"wrote {pos} frames, file has {facts['video']['frames']}")
    basis = (f"constructed: windows {W}x{H} moved over real frames ({', '.join(f'{c}@{t}s' for c, t in bases)}) along known sub-pixel paths, "
             f"re-encoded {enc['codec']} CRF {enc['crf']}; 13 scenarios joined by hard cuts; path = exact content displacement per frame")
    tc.write_labels(cid, "slides", {"storage": {"w": W, "h": H}, "convention": "path_xy = cumulative content displacement in px since the first frame of the run; positive x = content moves right, positive y = down",
                                     "runs": runs}, status="exact", proposed_by="script", basis=basis, frame_count=pos, fps=FPS)
    tc.write_labels(cid, "shots", {"boundaries": boundaries, "not_cuts": [], "regions": []}, status="exact", proposed_by="script", basis=basis, frame_count=pos, fps=FPS)
    man = tc.load_manifest()
    tc.upsert_clip(man, {"id": cid, "kind": "constructed", "domain": "anime" if a.variant in ("anime", "sol") else "live", "path": tc.rel_media(out), "tests": ["slides", "shots"],
                         "recipe": {"script": "make-slides.py", "args": {"variant": a.variant, "seed": a.seed}}, "derived_from": sorted({c for c, _ in bases}), "notes": basis,
                         "labels": {"slides": {"file": f"labels/{cid}/slides.json", "status": "exact", "items": len(runs)},
                                    "shots": {"file": f"labels/{cid}/shots.json", "status": "exact", "items": len(boundaries)}}, **facts})
    tc.save_json(tc.MANIFEST, man)
    print(f"{cid}: {pos} frames, {len(runs)} scenarios, classes {sorted({r['class'] for r in runs})}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
