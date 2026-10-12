"""Replays the research's exact-ground-truth tests of black bars and text in the bars against a content folder made by the worker.

  bars   python -I regression-bars-and-text.py bars  CONTENT_DIR bars_gt.npz
         The research's test video (first 23,016 frames of the anime episode with exact bars painted in: W1 scope 138 px top and
         bottom, W2 none, W3 4:3 pillarbox 240 px each side, W4 scope / none alternating per shot) scored the per-shot crops of
         the worker: frames that cut real picture (>= 2 px), frames with bars left (>= 2 px), crop changes and the share of them
         on shot boundaries. Research numbers for the same method (per-shot union + vocabulary snap): 0.00% cut, 0.96% bars left,
         64 crop changes all on cuts; raw per-frame cropdetect: 9.81% cut.
  text   python -I regression-bars-and-text.py text  CONTENT_DIR bbox_hs_72.npz
         The research's burned-in French subtitles (libass truth boxes at 24 fps) in the scope window: recall and false alarms of
         the worker's bar-edge guard on frames where text reaches more than 8 px into a bar; research (max over the strip at
         full size): recall 99.2%, false alarms 1.9% of text-free frames.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import numpy as np  # noqa: E402

from film_analysis.signals_store import SignalsStore  # noqa: E402

W, H = 1920, 1080
FULL = np.array([0, W - 1, 0, H - 1])
N1 = 5754


def per_frame_crop(shots: list, n: int) -> np.ndarray:
    D = np.tile(FULL, (n, 1)).astype(np.int32)
    for s in shots:
        r = s["crop"]["rect"]
        if r is not None:
            D[s["first_frame"]:s["last_frame"] + 1] = (r[0], r[2] - 1, r[1], r[3] - 1)
    return D


def score(D, T, edges, name):
    cutr = np.maximum(0, D[:, 2] - T[:, 2]) + np.maximum(0, T[:, 3] - D[:, 3])
    cutc = np.maximum(0, D[:, 0] - T[:, 0]) + np.maximum(0, T[:, 1] - D[:, 1])
    cut = np.maximum(cutr, cutc)
    barr = np.maximum(0, T[:, 2] - D[:, 2]) + np.maximum(0, D[:, 3] - T[:, 3])
    barc = np.maximum(0, T[:, 0] - D[:, 0]) + np.maximum(0, D[:, 1] - T[:, 1])
    bar = np.maximum(barr, barc)
    chg = np.nonzero(np.any(np.diff(D, axis=0) != 0, axis=1))[0] + 1
    on_cut = sum(1 for c in chg if np.abs(edges - c).min() <= 1)
    print(f"--- {name}: crop changes {len(chg)} ({on_cut} on shot boundaries)")
    out = {"crop_changes": int(len(chg)), "on_boundaries": int(on_cut), "windows": {}}
    for wn, a, b in (("W1 scope", 0, N1), ("W2 none", N1, 2 * N1), ("W3 pillar", 2 * N1, 3 * N1), ("W4 switching", 3 * N1, 4 * N1), ("ALL", 0, 4 * N1)):
        row = {"cut_ge2px": round(float((cut[a:b] >= 2).mean()), 5), "worst_cut_px": int(cut[a:b].max()),
               "bars_left_ge2px": round(float((bar[a:b] >= 2).mean()), 5), "worst_bar_px": int(bar[a:b].max())}
        out["windows"][wn] = row
        print(f"    {wn:13s} cut>=2px {row['cut_ge2px']:7.2%} (worst {row['worst_cut_px']:4d}px) | bars left>=2px {row['bars_left_ge2px']:7.2%} (worst {row['worst_bar_px']:4d}px)")
    return out


def bars(content_dir, gt_path):
    shots = json.load(open(os.path.join(content_dir, "shots.json")))["shots"]
    T = np.load(gt_path)["rect"].astype(np.int32)
    n = min(len(T), shots[-1]["last_frame"] + 1)
    D = per_frame_crop(shots, n)[:n]
    T = T[:n]
    edges = np.array([s["first_frame"] for s in shots])
    res = {"shots": len(shots), "per_shot_crop": score(D, T, edges, "per-shot union + vocabulary snap (worker)")}
    store = SignalsStore.open(content_dir)
    raw = store.col("crop_rect")[:n].astype(np.int32)
    ok = (raw[:, 1] - raw[:, 0] > 16) & (raw[:, 3] - raw[:, 2] > 16)
    cur, S0 = FULL.copy(), np.zeros((n, 4), np.int32)
    for t in range(n):
        if ok[t]:
            cur = raw[t]
        S0[t] = cur
    res["raw_per_frame"] = score(S0, T, edges, "raw per-frame cropdetect (hold on black), for comparison")
    voc = json.load(open(os.path.join(content_dir, "file-facts.json")))["crop_vocabulary"]
    print("vocabulary found:", [(m["rect"], m["share"]) for m in voc["modes"]], "(truth: scope [0,138,1920,942], none, pillar [240,0,1680,1080])")
    res["vocabulary"] = voc["modes"]
    return res


def text(content_dir, bbox_path):
    FPS = 24000 / 1001
    TOP_B, BOT_Y0 = 138, 942
    bb = np.load(bbox_path)["bbox"]
    store = SignalsStore.open(content_dir)
    et, eb = store.col("edge_top").astype(np.int32), store.col("edge_bottom").astype(np.int32)
    n = min(len(et), 5757)
    have = {int(round(r[0] * 24)): r for r in bb}
    tb = np.full((n, 4), -1)
    for i in range(n):
        r = have.get(int(round(i / FPS * 24)))
        if r is not None:
            tb[i] = r[1:5]
    has_text = tb[:, 0] >= 0
    pos = (has_text & (tb[:, 3] > BOT_Y0 + 8)) | (has_text & (tb[:, 2] < TOP_B - 8))
    pred = (eb[:n] < TOP_B - 8) | (et[:n] < TOP_B - 8)
    tp, fp, fn = int((pred & pos).sum()), int((pred & ~pos).sum()), int((~pred & pos).sum())
    res = {"frames": int(n), "frames_with_text": int(has_text.sum()), "text_in_bar_frames": int(pos.sum()), "recall": round(tp / max(1, pos.sum()), 4),
           "false_alarms": fp, "text_free_frames": int((~pos).sum()), "false_alarm_rate": round(fp / max(1, (~pos).sum()), 4), "missed": fn}
    print(json.dumps(res))
    # the shot-level result the planner sees: how far the crop is extended
    shots = json.load(open(os.path.join(content_dir, "shots.json")))["shots"]
    ext = [(s["bar_content"]["extend"]["top"], s["bar_content"]["extend"]["bottom"]) for s in shots if s["first_frame"] < n]
    res["shots_with_bottom_text"] = int(sum(1 for t, b in ext if b > 0))
    res["max_extension_px"] = {"top": max(t for t, _ in ext), "bottom": max(b for _, b in ext)}
    print("shots needing an extension:", res["shots_with_bottom_text"], "of", len(ext), "max extension", res["max_extension_px"])
    return res


def main(argv):
    if len(argv) != 3 or argv[0] not in ("bars", "text"):
        print(__doc__)
        return 2
    res = (bars if argv[0] == "bars" else text)(argv[1], argv[2])
    out = os.path.join(argv[1], f"regression-{argv[0]}.json")
    json.dump(res, open(out, "w"), indent=1)
    print("wrote", out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
