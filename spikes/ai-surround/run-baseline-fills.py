"""Non-AI fills on the hidden-sides test: the floor any AI candidate must beat.

  black        the sides stay black (the control)
  glow         the performer's surround today, approximated: edge colours in 32 px cells, blurred at two scales, dimmed
               with distance, smoothed over time (pass 2 of the picture path, ADR 0026)
  blur-extend  the picture's edge column carried outward, blurred more and dimmed with distance (F2 in the 2026-10-10 report)
  mirror-blur  the picture mirrored at its edge, blurred more and dimmed with distance (shows why mirroring duplicates faces)

    python -I run-baseline-fills.py SCENES_DIR OUT_DIR [--sides 0.25,0.5,1.0] [--sheet-side 0.5] [--scenes a,b]

Writes OUT_DIR/<fill>/<scene>/s<side>.mp4, OUT_DIR/scores-baselines.json (scenes run again are replaced, others kept) and
OUT_DIR/sheets/<scene>-s<side>.png (truth on top, then each fill, on the clip's middle frame).
"""
import argparse
import json
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # python -I leaves the script's folder off sys.path
import hidden_sides as hs  # noqa: E402

CELL = 32


def falloff(side, gain0=0.85, gain1=0.35):
    """Gain per side column, column 0 = next to the seam: linear in distance from gain0 to gain1."""
    return np.linspace(gain0, gain1, side, dtype=np.float32)


def place(vis, left, right):
    """Canvas = left side + visible centre + right side (left/right given with column 0 next to the seam)."""
    return np.concatenate([left[:, ::-1], vis, right], axis=1)


def fill_black(vis, side, state):
    z = np.zeros((vis.shape[0], side, 3), np.uint8)
    return place(vis, z, z)


def fill_glow(vis, side, state):
    h, w = vis.shape[:2]
    f = vis.astype(np.float32)
    cells = cv2.resize(f, (max(1, w // CELL), max(1, h // CELL)), interpolation=cv2.INTER_AREA)
    edges = np.stack([cells[:, 0], cells[:, -1]])                        # (2, rows, 3): left and right edge cells
    prev = state.get("glow")
    edges = edges if prev is None else prev + 0.2 * (edges - prev)       # temporal smoothing, as the performer does
    state["glow"] = edges
    out = []
    g = falloff(side)
    for e in edges:
        col = cv2.resize(e[:, None, :], (1, h), interpolation=cv2.INTER_LINEAR)[:, 0]
        fine = cv2.GaussianBlur(col[:, None, :], (1, 0), sigmaX=0.1, sigmaY=2 * CELL)[:, 0]
        coarse = cv2.GaussianBlur(col[:, None, :], (1, 0), sigmaX=0.1, sigmaY=8 * CELL)[:, 0]
        t = np.linspace(0.0, 1.0, side, dtype=np.float32)[None, :, None]
        band = fine[:, None, :] * (1 - t) + coarse[:, None, :] * t
        out.append(np.clip(band * g[None, :, None] * 0.75, 0, 255).astype(np.uint8))
    return place(vis, out[0], out[1])


def _spread(strip, side, levels=(2, 6, 16, 40, 96)):
    """strip: (h, side, 3) float, column 0 next to the seam. Blur that grows with distance, by blending blur levels."""
    h, w = strip.shape[:2]
    small = cv2.resize(strip, (max(1, w // 4), max(1, h // 4)), interpolation=cv2.INTER_AREA)
    blurred = []
    for s in levels:                      # wide blurs at quarter resolution: same look, a fraction of the cost
        if s < 8:
            blurred.append(cv2.GaussianBlur(strip, (0, 0), sigmaX=s, sigmaY=s, borderType=cv2.BORDER_REFLECT))
        else:
            b = cv2.GaussianBlur(small, (0, 0), sigmaX=s / 4, sigmaY=s / 4, borderType=cv2.BORDER_REFLECT)
            blurred.append(cv2.resize(b, (w, h), interpolation=cv2.INTER_LINEAR))
    pos = np.linspace(0.0, len(levels) - 1.0, side, dtype=np.float32)
    i0 = np.floor(pos).astype(int)
    i1 = np.minimum(i0 + 1, len(levels) - 1)
    t = (pos - i0)[None, :, None]
    out = np.empty_like(strip)
    for c in range(side):
        out[:, c] = blurred[i0[c]][:, c] * (1 - t[0, c]) + blurred[i1[c]][:, c] * t[0, c]
    return out


def fill_blur_extend(vis, side, state):
    f = vis.astype(np.float32)
    g = falloff(side)
    sides = []
    for edge in (f[:, 0:1], f[:, -1:]):
        strip = np.repeat(edge, side, axis=1)
        sides.append(np.clip(_spread(strip, side) * g[None, :, None], 0, 255).astype(np.uint8))
    return place(vis, sides[0], sides[1])


def fill_mirror_blur(vis, side, state):
    f = vis.astype(np.float32)
    w = f.shape[1]
    g = falloff(side)
    idx = np.minimum(np.arange(side), w - 1)
    left = f[:, idx]                      # column d of the left side mirrors visible column d
    right = f[:, w - 1 - idx]
    return place(vis, *[np.clip(_spread(s, side) * g[None, :, None], 0, 255).astype(np.uint8) for s in (left, right)])


FILLS = {"black": fill_black, "glow": fill_glow, "blur-extend": fill_blur_extend, "mirror-blur": fill_mirror_blur}


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("scenes_dir")
    ap.add_argument("out_dir")
    ap.add_argument("--sides", default="0.25,0.5,1.0")
    ap.add_argument("--sheet-side", type=float, default=0.5)
    ap.add_argument("--scenes", default="", help="comma-separated subset (default: every scene)")
    a = ap.parse_args(argv)
    sides = [float(x) for x in a.sides.split(",")]
    os.makedirs(os.path.join(a.out_dir, "sheets"), exist_ok=True)
    scores_path = os.path.join(a.out_dir, "scores-baselines.json")
    scores = json.load(open(scores_path, encoding="utf-8")) if os.path.isfile(scores_path) else {}
    only = set(filter(None, a.scenes.split(",")))
    for scene in sorted(os.listdir(a.scenes_dir)):
        sdir = os.path.join(a.scenes_dir, scene)
        if not os.path.isfile(os.path.join(sdir, "clip.mkv")) or (only and scene not in only):
            continue
        meta = json.load(open(os.path.join(sdir, "scene.json"), encoding="utf-8"))
        frames = hs.read_frames(os.path.join(sdir, "clip.mkv"))
        for s in sides:
            parts = [hs.split(f, s) for f in frames]
            truth = [p[0] for p in parts]
            side = parts[0][2]
            sheet_rows, sheet_labels = [hs.mark_seams(truth[len(truth) // 2], side)], [f"{scene}  s={s}  TRUTH"]
            for name, fn in FILLS.items():
                state = {}
                cand = [fn(p[1], side, state) for p in parts]
                sc = hs.score(truth, cand, side)
                scores.setdefault(name, {}).setdefault(scene, {})[f"s{s}"] = sc
                odir = os.path.join(a.out_dir, name, scene)
                os.makedirs(odir, exist_ok=True)
                hs.write_video(cand, os.path.join(odir, f"s{s}.mp4"), meta["fps"])
                if s == a.sheet_side:
                    sheet_rows.append(hs.mark_seams(cand[len(cand) // 2], side))
                    sheet_labels.append(f"{name}  near {sc['err_near']}  far {sc['err_far']}  seam x{sc['seam_step']}  flicker {sc['flicker']:+}")
            if s == a.sheet_side:
                hs.compose_sheet(sheet_rows, sheet_labels, os.path.join(a.out_dir, "sheets", f"{scene}-s{s}.png"), width=1280)
            print(scene, s, {n: scores[n][scene][f"s{s}"] for n in FILLS})
    hs.save_json(scores, scores_path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
