"""Seam match for per-shot plates: can a cheap correction, with no generator, hide the colour jump where a plate meets the picture?

A per-shot plate is one still drawn from the shot's middle frame; the picture beside it keeps moving, and the model repaints
colours a little (it drops a haze, warms a tint), so a line shows at the seam. Four versions of the same plate are scored over
the whole clip of each test scene:
  raw        the plate as generated, the real centre pasted back
  fit        once per shot, as the worker could do it: a 3x4 colour matrix maps the model's own redraw of the centre onto the
             real centre (both shrunk 8x, so line art need not line up) and is applied to the sides
  live       once per film frame, as a performer shader could do it: the jump between the picture's edge columns and the
             plate's seam columns, smoothed along the edge, is added to the plate and fades out with distance from the seam
  fit+live   both
Only the sides change; the real picture is pasted back untouched.

    python -I seam-match.py SCENES_DIR RUNS_DIR [--fills klein-green,klein-inpaint] [--side 0.5] [--band 4] [--sigma 24]
                            [--falloff 0.25]

Reads RUNS_DIR/<fill>/<scene>/raw-s<side>.png (from run-klein-plates.py). Writes RUNS_DIR/scores-seam.json,
RUNS_DIR/sheets/<scene>-<fill>-seam-s<side>.png (middle frame: truth, raw, fit, fit+live),
RUNS_DIR/seam-videos/<scene>-<fill>-s<side>.mp4 (truth above fit+live, whole clip, for the by-eye flicker check) and
RUNS_DIR/<fill>-fitlive/<scene>/s<side>.mp4 (fit+live alone, for the blind test).
"""
import argparse
import json
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # python -I leaves the script's folder off sys.path
import hidden_sides as hs  # noqa: E402

VARIANTS = ("raw", "fit", "live", "fit+live")
live_seam = hs.live_seam                                         # shared with apply-wraps.py (video fills)


def compose(plate, vis, side):
    out = np.clip(plate, 0, 255).round().astype(np.uint8)
    out[:, side:side + vis.shape[1]] = vis                      # the real picture is never the model's
    return out


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("scenes_dir")
    ap.add_argument("runs_dir")
    ap.add_argument("--fills", default="klein-green,klein-inpaint")
    ap.add_argument("--side", type=float, default=0.5)
    ap.add_argument("--band", type=int, default=4)
    ap.add_argument("--sigma", type=float, default=24.0)
    ap.add_argument("--falloff", type=float, default=0.25)
    a = ap.parse_args(argv)

    scenes = [d for d in sorted(os.listdir(a.scenes_dir)) if os.path.isfile(os.path.join(a.scenes_dir, d, "clip.mkv"))]
    os.makedirs(os.path.join(a.runs_dir, "sheets"), exist_ok=True)
    os.makedirs(os.path.join(a.runs_dir, "seam-videos"), exist_ok=True)
    scores_path = os.path.join(a.runs_dir, "scores-seam.json")
    # Earlier runs' fills stay in the file; a fill run again is replaced scene by scene.
    scores = json.load(open(scores_path, encoding="utf-8"))["scores"] if os.path.isfile(scores_path) else {}
    run_fills = a.fills.split(",")
    for sc in scenes:
        sdir = os.path.join(a.scenes_dir, sc)
        fps = json.load(open(os.path.join(sdir, "scene.json"), encoding="utf-8"))["fps"]
        frames = hs.read_frames(os.path.join(sdir, "clip.mkv"))
        parts = [hs.split(f, a.side) for f in frames]
        truth = [p[0] for p in parts]
        side = parts[0][2]
        key = len(frames) // 2                                   # the frame the plate was drawn from
        h, w = truth[0].shape[:2]
        for fill in a.fills.split(","):
            raw_path = os.path.join(a.runs_dir, fill, sc, f"raw-s{a.side}.png")
            if not os.path.isfile(raw_path):
                print(f"skip {fill}/{sc}: no {raw_path}")
                continue
            raw = cv2.cvtColor(cv2.imread(raw_path), cv2.COLOR_BGR2RGB)
            plate = cv2.resize(raw, (w, h), interpolation=cv2.INTER_LANCZOS4).astype(np.float32)
            vis_key = parts[key][1]
            M = hs.colour_fit(plate[:, side:side + vis_key.shape[1]], vis_key)
            fitted = hs.apply_fit(plate, M)
            cands = {v: [] for v in VARIANTS}
            for _, vis, _ in parts:
                cands["raw"].append(compose(plate, vis, side))
                cands["fit"].append(compose(fitted, vis, side))
                cands["live"].append(compose(live_seam(plate, vis, side, a.band, a.sigma, a.falloff), vis, side))
                cands["fit+live"].append(compose(live_seam(fitted, vis, side, a.band, a.sigma, a.falloff), vis, side))
            by = {v: hs.score(truth, c, side) for v, c in cands.items()}
            scores.setdefault(fill, {})[sc] = by
            print(f"{fill:14s} {sc:18s} " + "  ".join(f"{v} {s['err_near']}/{s['err_far']}/x{s['seam_step']}/{s['flicker']:+}"
                                                    for v, s in by.items()))
            rows = [hs.mark_seams(truth[key], side)] + [hs.mark_seams(cands[v][key], side) for v in ("raw", "fit", "fit+live")]
            labels = [f"{sc}  s={a.side}  TRUTH (frame {key})"] + [
                f"{fill} {v}  near {by[v]['err_near']}  far {by[v]['err_far']}  seam x{by[v]['seam_step']}  "
                f"flicker {by[v]['flicker']:+}" for v in ("raw", "fit", "fit+live")]
            hs.compose_sheet(rows, labels, os.path.join(a.runs_dir, "sheets", f"{sc}-{fill}-seam-s{a.side}.png"), width=1280)
            hs.write_stacked(truth, cands["fit+live"], side, os.path.join(a.runs_dir, "seam-videos", f"{sc}-{fill}-s{a.side}.mp4"),
                             fps)
            # The corrected plate alone, unmarked, for the blind real-or-generated test (make-blind-test.py).
            odir = os.path.join(a.runs_dir, f"{fill}-fitlive", sc)
            os.makedirs(odir, exist_ok=True)
            hs.write_video(cands["fit+live"], os.path.join(odir, f"s{a.side}.mp4"), fps)
    hs.save_json({"params": {"side": a.side, "band": a.band, "sigma": a.sigma, "falloff": a.falloff}, "scores": scores},
                 scores_path)

    print("\nmean over scenes  (err_near / err_far / seam_step / flicker)")
    for fill, by_sc in scores.items():
        if fill not in run_fills:
            continue
        for v in VARIANTS:
            vals = [s[v] for s in by_sc.values()]
            m = {k: round(float(np.mean([x[k] for x in vals])), 2) for k in ("err_near", "err_far", "seam_step", "flicker")}
            print(f"  {fill:14s} {v:9s} {m['err_near']:6.1f} {m['err_far']:6.1f}  x{m['seam_step']:<5} {m['flicker']:+.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
