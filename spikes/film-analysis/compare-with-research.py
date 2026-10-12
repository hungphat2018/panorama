"""Compares a content folder of the anime episode with the numbers the film-analysis research measured on the same file.

    python -I compare-with-research.py CONTENT_DIR RESEARCH_DIR

RESEARCH_DIR holds the research's own result files for the episode (bounds_anime_tn.npy: its 350 TransNetV2 cuts;
tn_anime_full_sw.npz / tn_anime_gpuarea.npz: TransNetV2 scores by the official CPU resize and by NVDEC resize; scd_anime.npz: FFmpeg
scdet scores; pairs_anime.npz: the slide-pair table; runs_anime.json: its 21 sliding runs; crop_anime.npz: per-frame cropdetect).
Prints and saves compare-with-research.json in CONTENT_DIR. What each block checks:
  shots   the number of cuts and how many coincide with the research's (same frame, and within one frame)
  scores  the shot-model scores against the research's two decode paths; our scdet-like score against FFmpeg's scdet
  slides  the per-pair shifts against the research's table (non-held pairs), the sliding runs of at least 1 s against its 21, and the
          three pure slides it found at 566.5 s, 584.1 s and 852.3 s
  crops   the raw per-frame cropdetect rectangles against the research's (its black threshold was FFmpeg's default 0.094)
  subtitles  events of the first ASS track and how many libass calls explicit (the research: 480 events, 0 explicit)
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import numpy as np  # noqa: E402

from film_analysis import shots_transnet as st  # noqa: E402
from film_analysis.content_reader import ContentModel  # noqa: E402


def match(a, b, tol):
    b = np.asarray(b)
    return int(sum(1 for x in a if len(b) and np.abs(b - x).min() <= tol))


def main(argv):
    if len(argv) != 2:
        print(__doc__)
        return 2
    m = ContentModel.open(argv[0])
    R = argv[1]
    out = {}
    n = m.n_frames
    print(f"{n} frames, scanned {int(m.scanned().sum())}")

    # ---- shots
    single, many = m.signal("tn_single"), m.signal("tn_many")
    ev = st.tn_boundaries(single, many, m.signal("scanned"), m.signal("key").astype(bool))
    mine = np.array([e["frame"] for e in ev if e["source"] == "transnetv2"])
    theirs = np.load(os.path.join(R, "bounds_anime_tn.npy"))
    out["shots"] = {"ours_single_head_events": int(len(mine)), "research_events": int(len(theirs)),
                    "ours_found_in_research_exact": match(mine, theirs, 0), "ours_found_in_research_pm1": match(mine, theirs, 1),
                    "research_found_in_ours_exact": match(theirs, mine, 0), "research_found_in_ours_pm1": match(theirs, mine, 1),
                    "hard": sum(1 for e in ev if e["kind"] == "hard" and e["source"] == "transnetv2"),
                    "gradual_single_head": sum(1 for e in ev if e["kind"] == "gradual" and e["source"] == "transnetv2"),
                    "gradual_many_only": sum(1 for e in ev if e["source"] == "transnetv2-many-hot")}
    print("shots:", json.dumps(out["shots"]))

    # ---- scores
    sc = {}
    for name in ("tn_anime_full_sw.npz", "tn_anime_gpuarea.npz"):
        p = np.load(os.path.join(R, name))["probs"][:n].astype(np.float32)
        d = np.abs(single.astype(np.float32)[:len(p)] - p)
        sc[name] = {"max_abs_diff": round(float(d.max()), 4), "mean_abs_diff": round(float(d.mean()), 6),
                    "cuts_agree_at_0.5": int(((single[:len(p)] > 0.5) == (p > 0.5)).sum()), "frames": int(len(p)),
                    "disagreeing_frames": int(((single[:len(p)] > 0.5) != (p > 0.5)).sum())}
    scd_r = np.load(os.path.join(R, "scd_anime.npz"))["score"][:n]
    scd_m = m.signal("scd").astype(np.float32)[:len(scd_r)]
    sc["scdet"] = {"max_abs_diff": round(float(np.abs(scd_m - scd_r).max()), 3), "mean_abs_diff": round(float(np.abs(scd_m - scd_r).mean()), 4),
                   "frames_flagged_ours_ge10": int((scd_m >= 10).sum()), "frames_flagged_ffmpeg_ge10": int((scd_r >= 10).sum()),
                   "flag_agreement": round(float(((scd_m >= 10) == (scd_r >= 10)).mean()), 5)}
    out["scores"] = sc
    print("scores:", json.dumps(sc))

    # ---- slides
    pairs = np.load(os.path.join(R, "pairs_anime.npz"))["pairs"]            # dx dy peak before after static explained other luma contrast, pair (i, i+1)
    dxm, dym = m.signal("dx")[1:len(pairs) + 1].astype(np.float64), m.signal("dy")[1:len(pairs) + 1].astype(np.float64)
    before_m = m.signal("diff_before")[1:len(pairs) + 1].astype(np.float64)
    moving = (pairs[:, 3] >= 0.8) & (before_m >= 0.8)
    ddx, ddy = np.abs(dxm - pairs[:, 0])[moving], np.abs(dym - pairs[:, 1])[moving]
    out["slides"] = {"moving_pairs_compared": int(moving.sum()),
                     "shift_diff_px_median": round(float(np.median(np.hypot(ddx, ddy))), 4), "shift_diff_px_p95": round(float(np.percentile(np.hypot(ddx, ddy), 95)), 3),
                     "shift_diff_px_max": round(float(np.hypot(ddx, ddy).max()), 2),
                     "held_pairs_ours": int((before_m < 0.8).sum()), "held_pairs_research": int((pairs[:, 3] < 0.8).sum())}
    runs_r = json.load(open(os.path.join(R, "runs_anime.json")))
    runs_m = m.slide_runs
    out["slides"]["runs_ours_ge1s"] = len(runs_m)
    out["slides"]["runs_research_ge1s"] = len(runs_r)
    rows = []
    for r in runs_r:
        t0, t1 = r["t0"], r["t0"] + r["sec"]
        best = max(runs_m, key=lambda x: max(0.0, min(x["end_s"], t1) - max(x["start_s"], t0)), default=None)
        ov = 0.0 if best is None else max(0.0, min(best["end_s"], t1) - max(best["start_s"], t0))
        rows.append({"research_t0": r["t0"], "research_sec": r["sec"], "research_explained": r["explained"], "research_static": r["static"],
                     "research_other": r["other"], "ours_found": ov > 0.5 * r["sec"],
                     "ours_t0": round(best["start_s"], 2) if best is not None and ov > 0 else None,
                     "ours_label": best["purity"]["label"] if best is not None and ov > 0 else None,
                     "ours_explained": best["purity"]["explained"] if best is not None and ov > 0 else None})
    out["slides"]["research_runs"] = rows
    out["slides"]["research_runs_found"] = sum(1 for r in rows if r["ours_found"])
    pure_times = [566.5, 584.1, 852.3]
    out["slides"]["research_pure_slides"] = [
        {"t": t, "ours": next((x["purity"]["label"] for x in runs_m if x["start_s"] - 0.5 <= t <= x["end_s"] + 0.5), None)} for t in pure_times]
    out["slides"]["ours_labels"] = {k: sum(1 for x in runs_m if x["purity"]["label"] == k) for k in ("pure", "layered", "unknown")}
    print("slides:", json.dumps({k: v for k, v in out["slides"].items() if k != "research_runs"}))

    # ---- crops
    c_r = np.load(os.path.join(R, "crop_anime.npz"))["rect"][:n]
    c_m = m.signal("crop_rect")[:len(c_r)].astype(np.int64)
    full = np.array([0, 1919, 0, 1079])
    out["crops"] = {"frames": int(len(c_r)), "identical_rectangles": round(float((c_m == c_r).all(axis=1).mean()), 4),
                    "ours_not_full_frame": round(float((c_m != full).any(axis=1).mean()), 4), "research_not_full_frame": round(float((c_r != full).any(axis=1).mean()), 4),
                    "note": "the research's per-frame run used the default black threshold 0.094; the worker uses the file's calibrated one"}
    print("crops:", json.dumps(out["crops"]))

    # ---- subtitles
    tracks = {}
    for idx in m.subtitle_tracks():
        d = m.subtitles(idx)
        tracks[idx] = {"codec": d["track"]["codec"], "language": d["track"]["language"], "events": d["counts"]["events"],
                       "explicit": sum(1 for e in d["events"] if e["features"].get("explicit")), "classes": {k: v for k, v in d["counts"].items() if k in ("dialogue", "sign", "unknown")}}
    out["subtitles"] = tracks
    print("subtitles:", json.dumps(tracks))
    with open(os.path.join(argv[0], "compare-with-research.json"), "w") as f:
        json.dump(out, f, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
