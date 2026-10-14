"""Routing by motion (roles R1 + R5): each side of a shot comes from the tool that fits how the picture moves next to it.

A still plate is right while the picture beside it stands still; a video fill keyed by that plate drifts away from it on
every frame but the key (held-dialogue: plate far 12.4, plate-keyed VACE-1.3B far 22.4). So, per side and per shot, the
share of still steps in the picture's band next to that seam (apply-wraps.still_steps on the visible picture only, no
truth) decides:
  share >= --still-min   the plate fill's side (e.g. qwen21-outpaint-v2-fitlive: one still, colour fit, live seam pull)
  otherwise              the video fill's side (e.g. a plate-keyed video model + live + freeze)
A shot whose two bands both hold gets the plate on both sides; one that moves on one side only gets one of each. Both
inputs are finished fills (real centre pasted), so this only picks columns; the real picture is pasted back again.

    python -I route-by-motion.py SCENES_DIR RUNS_DIR --plate FILL --video FILL --name RECIPE [--scenes a,b]
                                 [--still-min 0.9] [--side 0.5]

Writes RUNS_DIR/<name>/<scene>/s<side>.mp4 (+ -with-truth.mp4, recipe.json with each side's route) and adds the scores
(hidden_sides.score + boil) to RUNS_DIR/scores-route.json.
"""
import argparse
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)                                          # python -I leaves the script's folder off sys.path
import hidden_sides as hs  # noqa: E402

_spec = importlib.util.spec_from_file_location("apply_wraps", os.path.join(HERE, "apply-wraps.py"))
wraps = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wraps)                                   # still_steps, boil: the same rules as the wraps


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("scenes_dir")
    ap.add_argument("runs_dir")
    ap.add_argument("--plate", required=True, help="fill used where the picture's band holds still")
    ap.add_argument("--video", required=True, help="fill used where it moves")
    ap.add_argument("--name", required=True, help="recipe name of the routed fill")
    ap.add_argument("--scenes", default="", help="comma-separated subset (default: scenes both fills have)")
    ap.add_argument("--still-min", type=float, default=0.9, help="still-step share at or above which a side is still")
    ap.add_argument("--side", type=float, default=0.5)
    ap.add_argument("--still-diff", type=int, default=6)
    ap.add_argument("--still-share", type=float, default=0.002)
    a = ap.parse_args(argv)

    only = set(filter(None, a.scenes.split(",")))
    scores_path = os.path.join(a.runs_dir, "scores-route.json")
    scores = json.load(open(scores_path, encoding="utf-8")) if os.path.isfile(scores_path) else {}
    for sc in sorted(os.listdir(a.scenes_dir)):
        paths = [os.path.join(a.runs_dir, f, sc, f"s{a.side}.mp4") for f in (a.plate, a.video)]
        if (only and sc not in only) or not all(os.path.isfile(p) for p in paths):
            continue
        meta = json.load(open(os.path.join(a.scenes_dir, sc, "scene.json"), encoding="utf-8"))
        plate, video = (hs.read_frames(p) for p in paths)
        n = min(len(plate), len(video))
        parts = [hs.split(f, a.side) for f in hs.read_frames(os.path.join(a.scenes_dir, sc, "clip.mkv"), n)]
        truth, vis, side = [p[0] for p in parts], [p[1] for p in parts], parts[0][2]
        n = len(truth)
        band = max(16, side // 4)
        shares = []
        for cols in (slice(0, band), slice(-band, None)):
            still = wraps.still_steps([v[:, cols] for v in vis], a.still_diff, a.still_share)
            shares.append(sum(still) / max(1, n - 1))
        route = ["plate" if s >= a.still_min else "video" for s in shares]
        w = truth[0].shape[1]
        out = []
        for t in range(n):
            f = video[t].copy()
            if route[0] == "plate":
                f[:, :side] = plate[t][:, :side]
            if route[1] == "plate":
                f[:, w - side:] = plate[t][:, w - side:]
            f[:, side:side + vis[t].shape[1]] = vis[t]            # the real picture is never a fill's
            out.append(f)
        s_ = hs.score(truth, out, side)
        s_["boil"] = wraps.boil(out, truth, side, wraps.still_steps(vis, a.still_diff, a.still_share))
        s_["route"] = route
        s_["band_still_share"] = [round(x, 3) for x in shares]
        scores.setdefault(a.name, {}).setdefault(sc, {})[f"s{a.side}"] = s_
        odir = os.path.join(a.runs_dir, a.name, sc)
        os.makedirs(odir, exist_ok=True)
        hs.write_video(out, os.path.join(odir, f"s{a.side}.mp4"), meta["fps"])
        hs.write_stacked(truth, out, side, os.path.join(odir, f"s{a.side}-with-truth.mp4"), meta["fps"])
        hs.save_json({"recipe": [f"R1+R5:route(still>={a.still_min})", {"plate": a.plate, "video": a.video}],
                      "route": route, "band_still_share": s_["band_still_share"], "band_px": band},
                     os.path.join(odir, "recipe.json"))
        hs.save_json(scores, scores_path)
        print(f"{a.name} {sc}: route {route} (band still {s_['band_still_share']})  {s_}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
