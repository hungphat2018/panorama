"""Wraps (role R5) around any fill: colour and motion of the sides follow the picture, whatever drew them.

  tone    per clip, per side: a per-channel gain and offset that map the side's band next to the seam onto the picture's
          band at its edge (mean and spread over the whole clip; gain clamped to 0.8-1.25). Removes a cast that covers the
          whole side (a video model that keeps the centre shows no shift there, so the centre-redraw fit cannot see it).
  live    per film frame: the seam pull of seam-match (hidden_sides.live_seam), rows of the side next to the seam pulled
          onto the picture's edge, fading with distance. What a performer shader can do.
  freeze  per side: while the picture's band next to that seam stays still, the side becomes its own per-pixel median
          over that still run, so nothing moves out there unless the picture moves next to it.
  hold    anime holds each drawing for 2-3 film frames (74% of frame steps repeat in the test episode). When the visible
          picture repeats, the sides repeat too: a generator cannot "boil" (redraw slightly differently) during a hold.
Applied in that order (colour first, so freeze and hold keep corrected frames). A frame step is "still" when fewer than
--still-share of its pixels change by more than --still-diff code values (grey, half size, 3x3 blur against codec noise).
The wraps need nothing but the fill's video and the picture, so they apply to any recipe without touching it.

Scores: the usual four (hidden_sides.score) plus boil = mean change of the sides on steps where the picture holds,
candidate minus truth (0: no more change than the real sides; > 0: the sides boil during holds).

    python -I apply-wraps.py SCENES_DIR RUNS_DIR --fills vace-1.3b-fit[,...] [--sets "freeze;tone,live,freeze"]
                             [--side 0.5]

Writes RUNS_DIR/<fill>-<wrap>[-<wrap>...]/<scene>/s<side>.mp4 (+ s<side>-with-truth.mp4, recipe.json) per set and
RUNS_DIR/scores-wraps.json (the input fill re-scored with boil, then each wrapped one).
"""
import argparse
import json
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # python -I leaves the script's folder off sys.path
import hidden_sides as hs  # noqa: E402

ORDER = ("tone", "live", "freeze", "hold")


def change_share(a, b, diff):
    """Share of pixels whose grey value moved by more than `diff` between two prepared images."""
    return float((np.abs(a.astype(np.int16) - b.astype(np.int16)) > diff).mean())


def prep(img):
    """Grey, half size, 3x3 blur: codec noise drops below the change threshold, a mouth flap does not."""
    g = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    g = cv2.resize(g, (g.shape[1] // 2, g.shape[0] // 2), interpolation=cv2.INTER_AREA)
    return cv2.blur(g, (3, 3))


def still_steps(images, diff, share):
    """still[t] for t >= 1: the step t-1 -> t changes fewer than `share` of the pixels (still[0] is False)."""
    p = [prep(i) for i in images]
    return [False] + [change_share(p[t - 1], p[t], diff) < share for t in range(1, len(p))]


def runs(flags):
    """Maximal [start, end] frame runs joined by still steps: frames start..end where every step start+1..end is still."""
    out, start = [], 0
    for t in range(1, len(flags) + 1):
        if t == len(flags) or not flags[t]:
            if t - 1 > start:
                out.append((start, t - 1))
            start = t
    return out


def apply_tone(frames, vis, side):
    """Per side, one gain and offset per channel for the whole clip, from the bands either side of the seam."""
    w, vis_w = frames[0].shape[1], vis[0].shape[1]
    b = max(4, side // 8)
    def fit(cand_band, pic_band):
        c = np.concatenate([x.reshape(-1, 3) for x in cand_band]).astype(np.float64)
        p = np.concatenate([x.reshape(-1, 3) for x in pic_band]).astype(np.float64)
        gain = np.clip(p.std(0) / np.maximum(c.std(0), 1e-3), 0.8, 1.25)
        return gain.astype(np.float32), (p.mean(0) - gain * c.mean(0)).astype(np.float32)
    gl, ol = fit([f[:, side - b:side] for f in frames], [v[:, :b] for v in vis])
    gr, orr = fit([f[:, side + vis_w:side + vis_w + b] for f in frames], [v[:, vis_w - b:] for v in vis])
    out = []
    for f in frames:
        g = f.astype(np.float32)
        g[:, :side] = g[:, :side] * gl + ol
        g[:, w - side:] = g[:, w - side:] * gr + orr
        out.append(np.clip(g, 0, 255).round().astype(np.uint8))
    return out


def apply_live(frames, vis, side):
    return [np.clip(hs.live_seam(f.astype(np.float32), v, side), 0, 255).round().astype(np.uint8)
            for f, v in zip(frames, vis)]


def apply_freeze(frames, side, band_still):
    """Each side, over each still run of its band, replaced by its per-pixel median over the run."""
    out = [f.copy() for f in frames]
    w = frames[0].shape[1]
    for cols, flags in ((slice(0, side), band_still[0]), (slice(w - side, w), band_still[1])):
        for a, b in runs(flags):
            med = np.median(np.stack([f[:, cols] for f in frames[a:b + 1]]), axis=0).astype(np.uint8)
            for t in range(a, b + 1):
                out[t][:, cols] = med
    return out


def apply_hold(frames, side, pic_still):
    """When the picture holds, the sides of the previous output frame are kept."""
    out = [frames[0].copy()]
    w = frames[0].shape[1]
    for t in range(1, len(frames)):
        f = frames[t].copy()
        if pic_still[t]:
            f[:, :side] = out[-1][:, :side]
            f[:, w - side:] = out[-1][:, w - side:]
        out.append(f)
    return out


def boil(cand, truth, side, pic_still):
    """Mean change of the sides on held steps, candidate minus truth (0 when the picture never holds)."""
    w = cand[0].shape[1]
    steps = [t for t in range(1, len(cand)) if pic_still[t]]
    if not steps:
        return 0.0
    def mean_change(frames):
        return float(np.mean([np.abs(np.hstack([frames[t][:, :side], frames[t][:, w - side:]]).astype(np.int16)
                                     - np.hstack([frames[t - 1][:, :side], frames[t - 1][:, w - side:]]).astype(np.int16)).mean()
                              for t in steps]))
    return round(mean_change(cand) - mean_change(truth), 2)


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("scenes_dir")
    ap.add_argument("runs_dir")
    ap.add_argument("--fills", required=True, help="comma-separated fills under RUNS_DIR (each <fill>/<scene>/s<side>.mp4)")
    ap.add_argument("--sets", default="freeze;tone,live,freeze",
                    help=f"';'-separated wrap sets, each a comma list from {','.join(ORDER)} (applied in that order)")
    ap.add_argument("--side", type=float, default=0.5)
    ap.add_argument("--still-diff", type=int, default=6, help="grey code values a pixel must move to count as changed")
    ap.add_argument("--still-share", type=float, default=0.002, help="changed-pixel share below which a step is still")
    a = ap.parse_args(argv)
    sets = [[w for w in ORDER if w in s.split(",")] for s in a.sets.split(";")]
    if any(not s for s in sets):
        raise SystemExit(f"unknown wrap in --sets {a.sets!r}; known: {', '.join(ORDER)}")

    scores_path = os.path.join(a.runs_dir, "scores-wraps.json")
    scores = json.load(open(scores_path, encoding="utf-8")) if os.path.isfile(scores_path) else {}
    for fill in a.fills.split(","):
        for sc in sorted(os.listdir(os.path.join(a.runs_dir, fill))):
            src = os.path.join(a.runs_dir, fill, sc, f"s{a.side}.mp4")
            if not os.path.isfile(src):
                continue
            meta = json.load(open(os.path.join(a.scenes_dir, sc, "scene.json"), encoding="utf-8"))
            cand = hs.read_frames(src)
            parts = [hs.split(f, a.side) for f in hs.read_frames(os.path.join(a.scenes_dir, sc, "clip.mkv"), len(cand))]
            truth, vis, side = [p[0] for p in parts], [p[1] for p in parts], parts[0][2]
            cand = cand[:len(truth)]
            band = max(16, side // 4)
            pic_still = still_steps(vis, a.still_diff, a.still_share)
            band_still = (still_steps([v[:, :band] for v in vis], a.still_diff, a.still_share),
                          still_steps([v[:, -band:] for v in vis], a.still_diff, a.still_share))
            held = sum(pic_still) / max(1, len(pic_still) - 1)
            print(f"{fill} {sc}: {len(cand)} frames, picture holds {held:.0%} of steps "
                  f"(content model: {meta.get('shot_motion', {}).get('held_share', '?')}), "
                  f"bands still {sum(band_still[0]) / max(1, len(cand) - 1):.0%} / {sum(band_still[1]) / max(1, len(cand) - 1):.0%}")
            base = hs.score(truth, cand, side)
            base["boil"] = boil(cand, truth, side, pic_still)
            scores.setdefault(fill, {}).setdefault(sc, {})[f"s{a.side}"] = base
            print(f"  {fill:40s} {base}")
            for ws in sets:
                out = cand
                for w in ws:
                    if w == "tone":
                        out = apply_tone(out, vis, side)
                    elif w == "live":
                        out = apply_live(out, vis, side)
                    elif w == "freeze":
                        out = apply_freeze(out, side, band_still)
                    else:
                        out = apply_hold(out, side, pic_still)
                for f, v in zip(out, vis):
                    f[:, side:side + v.shape[1]] = v              # the real picture is never the fill's
                name = f"{fill}-{'-'.join(ws)}"
                s_ = hs.score(truth, out, side)
                s_["boil"] = boil(out, truth, side, pic_still)
                scores.setdefault(name, {}).setdefault(sc, {})[f"s{a.side}"] = s_
                odir = os.path.join(a.runs_dir, name, sc)
                os.makedirs(odir, exist_ok=True)
                hs.write_video(out, os.path.join(odir, f"s{a.side}.mp4"), meta["fps"])
                hs.write_stacked(truth, out, side, os.path.join(odir, f"s{a.side}-with-truth.mp4"), meta["fps"])
                hs.save_json({"recipe": [fill] + [f"R5:{w}" for w in ws], "still_diff": a.still_diff,
                              "still_share": a.still_share, "band_px": band, "picture_hold_share": round(held, 3)},
                             os.path.join(odir, "recipe.json"))
                print(f"  {name:40s} {s_}")
            hs.save_json(scores, scores_path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
