"""How much of the leftover canvas could real pixels fill? Seen-pixel coverage, from the content model alone.

The picture (the shot's crop, W x H source pixels) is extended by s*W on the left and on the right; s is the side share
(0.25: canvas 1.5x the picture's width, 0.5: 2x, e.g. 16:9 on 32:9, 1.0: 3x, e.g. 16:9 on 48:9). Inside a camera slide run
(slide-paths.json) frame j shows the background sheet shifted by (x_j, y_j). A side point q of frame k is "seen" when some
allowed frame j of the same run shows that sheet point inside its picture: q + (s_j - s_k) lies in [0, W) x [0, H).
Frames outside any run count as unseen: a still camera never shows more than its own picture. Purely geometric, no pixels
are decoded, so it is an upper bound for the sheet itself: characters over the sheet still need their own answer.

Windows (which frames may lend pixels): past = frames already shown; N = past plus N seconds of look-ahead; shot = the whole
run. Pure runs only by default (one rigid sheet); --all-runs adds layered and unknown runs.

    python -I seen-pixel-coverage.py CONTENT_DIR [--sides 0.25,0.5,1.0] [--windows past,2,10,shot] [--all-runs] [--out FILE]
"""
import argparse
import json
import os
import statistics
import sys

import numpy as np

GRID_X, GRID_Y = 24, 18            # sample points per side rectangle


def load(content_dir, name):
    with open(os.path.join(content_dir, name), encoding="utf-8") as f:
        return json.load(f)


def side_points(w, h, s):
    """Sample points of the left and right side rectangles, in the frame's own screen pixels."""
    xs = (np.arange(GRID_X) + 0.5) / GRID_X * s * w
    ys = (np.arange(GRID_Y) + 0.5) / GRID_Y * h
    gx, gy = np.meshgrid(xs, ys)
    left = np.stack([-gx.ravel(), gy.ravel()], axis=1)        # x in (-s*w, 0)
    right = np.stack([w + gx.ravel(), gy.ravel()], axis=1)    # x in (w, w + s*w)
    return np.concatenate([left, right])


def run_coverage(run, w, h, s, window_frames):
    """Per-frame covered share of both side rectangles for one run; window_frames None = whole run, -1 = past only."""
    shift = np.stack([np.asarray(run["x"], float), np.asarray(run["y"], float)], axis=1)
    pts = side_points(w, h, s)
    n = len(shift)
    out = np.zeros(n)
    for k in range(n):
        hi = n if window_frames is None else (k + 1 if window_frames < 0 else min(n, k + 1 + window_frames))
        d = shift[:hi] - shift[k]                                  # (m, 2) other frames' shift relative to frame k
        px = pts[:, None, 0] + d[None, :, 0]
        py = pts[:, None, 1] + d[None, :, 1]
        seen = ((px >= 0) & (px < w) & (py >= 0) & (py < h)).any(axis=1)
        out[k] = seen.mean()
    return out


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("content_dir")
    ap.add_argument("--sides", default="0.25,0.5,1.0")
    ap.add_argument("--windows", default="past,2,10,shot")
    ap.add_argument("--all-runs", action="store_true")
    ap.add_argument("--out")
    a = ap.parse_args(argv)

    shots = load(a.content_dir, "shots.json")
    slides = load(a.content_dir, "slide-paths.json")
    facts = load(a.content_dir, "file-facts.json")
    n_frames = shots["frame_count"]
    fps = n_frames / max(1e-9, shots["shots"][-1]["end_s"])
    by_id = {sh["id"]: sh for sh in shots["shots"]}

    # Shot statistics: length, held drawings, still camera.
    secs = [sh["end_s"] - sh["start_s"] for sh in shots["shots"]]
    held_w = still_frames = moving_known = 0.0
    for sh in shots["shots"]:
        m = sh.get("motion")
        if not m:
            continue
        moving_known += sh["frames"]
        held_w += m["held_share"] * sh["frames"]
        if m["speed_px_s_p90"] < 5.0:
            still_frames += sh["frames"]

    runs = [r for r in slides["runs"] if a.all_runs or r["purity"]["label"] == "pure"]
    labels = {}
    for r in slides["runs"]:
        labels[r["purity"]["label"]] = labels.get(r["purity"]["label"], 0) + (r["last_frame"] - r["first_frame"] + 1)

    sides = [float(x) for x in a.sides.split(",")]
    windows = a.windows.split(",")
    result = {
        "file": facts["file"]["name"] if "name" in facts.get("file", {}) else None,
        "frames": n_frames, "fps": round(fps, 3), "shots": len(shots["shots"]),
        "slides_state": slides["state"], "shots_state": shots["state"],
        "shot_seconds": {"median": round(statistics.median(secs), 2),
                         "p10": round(float(np.percentile(secs, 10)), 2),
                         "p90": round(float(np.percentile(secs, 90)), 2)},
        "held_share_of_steps": round(held_w / max(1, moving_known), 3),
        "new_drawings_per_s": round(fps * (1 - held_w / max(1, moving_known)), 2),
        "still_camera_share_of_frames": round(still_frames / max(1, moving_known), 3),
        "run_frames_share_by_purity": {k: round(v / n_frames, 4) for k, v in labels.items()},
        "runs_used": len(runs), "purity_filter": "all" if a.all_runs else "pure",
        "coverage_share_of_all_side_area": {}, "coverage_share_within_run_frames": {}, "runs": [],
    }
    run_frames = sum(len(r["x"]) for r in runs)
    per_run = {r["id"]: {"shot": r["shot"], "seconds": r["seconds"], "direction": r["direction"],
                         "speed_px_s": r["speed_px_s"], "purity": r["purity"]["label"],
                         "travel_px": [round(max(r["x"]) - min(r["x"]), 1), round(max(r["y"]) - min(r["y"]), 1)],
                         "coverage": {}} for r in runs}
    for s in sides:
        for wname in windows:
            wf = None if wname == "shot" else (-1 if wname == "past" else int(round(float(wname) * fps)))
            covered = 0.0
            for r in runs:
                rect = (by_id.get(r["shot"], {}).get("crop") or {}).get("rect") or [0, 0, facts["video"]["width"], facts["video"]["height"]]
                w, h = rect[2] - rect[0], rect[3] - rect[1]
                cov = run_coverage(r, w, h, s, wf)
                covered += cov.sum()
                per_run[r["id"]]["coverage"][f"s{s}-{wname}"] = round(float(cov.mean()), 3)
            key = f"s{s}-{wname}"
            result["coverage_share_of_all_side_area"][key] = round(covered / n_frames, 4)
            result["coverage_share_within_run_frames"][key] = round(covered / max(1, run_frames), 4)
    result["runs"] = [dict(id=k, **v) for k, v in per_run.items()]

    text = json.dumps(result, indent=1)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
