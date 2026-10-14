"""A judge that looks (role R6): Qwen3-VL-8B rates how convincingly each candidate's sides continue the picture.

best-of-n-plates.py showed that signals computed from pixels pick better seams but cannot tell whether the content far
from the seam is right. A vision-language model can be asked that directly. Each candidate canvas (real centre pasted,
generated sides) goes to Qwen3-VL-8B (the text encoder already downloaded for Qwen-Image-2.1, run by ComfyUI's
TextGenerate, greedy) with one question; the first number in its answer is the rating. On the hidden-sides test the
truth is known, so the Spearman correlation per scene between the rating and the true errors says whether the judge
can pick; its pick per scene is written as a fill, so the rest of the chain (seam-match, blind test) can use it.

    python -I vlm-judge.py SCENES_DIR OUT_DIR [--family klein-green-n8] [--port 8189]

Reads OUT_DIR/<family>/<scene>/seed*.png and OUT_DIR/scores-best-of-n.json (from best-of-n-plates.py). Writes
OUT_DIR/scores-vlm-judge.json and OUT_DIR/<family>-vlmpick/<scene>/{plate,raw}-s<side>.png.
"""
import argparse
import json
import os
import re
import shutil
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # python -I leaves the script's folder off sys.path
import comfy_client as cc  # noqa: E402
import hidden_sides as hs  # noqa: E402

QUESTION = ("This wide anime frame was made by extending a narrower original picture: the middle half is the original, "
            "the left quarter and the right quarter were added by a computer. Rate from 1 to 10 how convincingly the "
            "added parts continue the original as one real frame of the show: the same place, the same characters "
            "completed correctly, the same scale and perspective, the same drawing style and colours, nothing invented "
            "that does not belong. Answer with the number only.")


def judge_graph(image, max_length=8):
    return {
        "1": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen3vl_8b_bf16.safetensors", "type": "qwen_image"}},
        "2": {"class_type": "LoadImage", "inputs": {"image": image}},
        "3": {"class_type": "TextGenerate", "inputs": {"clip": ["1", 0], "prompt": QUESTION, "image": ["2", 0],
                                                       "max_length": max_length, "sampling_mode": "off"}},
        "4": {"class_type": "PreviewAny", "inputs": {"source": ["3", 0]}},
    }


def spearman(a, b):
    ra, rb = np.argsort(np.argsort(a)), np.argsort(np.argsort(b))
    if np.std(ra) == 0 or np.std(rb) == 0:
        return 0.0
    return float(np.corrcoef(ra, rb)[0, 1])


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("scenes_dir")
    ap.add_argument("out_dir")
    ap.add_argument("--family", default="klein-green-n8")
    ap.add_argument("--side", type=float, default=0.5)
    ap.add_argument("--port", type=int, default=8189)
    a = ap.parse_args(argv)
    truth_scores = json.load(open(os.path.join(a.out_dir, "scores-best-of-n.json"), encoding="utf-8"))[a.family]
    fam_dir = os.path.join(a.out_dir, a.family)

    # Every candidate is copied into ComfyUI's input folder before the server starts, so its file list includes them.
    jobs = []
    for sc in sorted(truth_scores):
        for d in truth_scores[sc]["draws"]:
            src = os.path.join(fam_dir, sc, f"seed{d['seed']}.png")
            name = f"panorama-judge-{a.family}-{sc}-seed{d['seed']}.png"
            shutil.copy(src, os.path.join(cc.INPUT_DIR, name))
            jobs.append((sc, d, name, src))

    out_path = os.path.join(a.out_dir, "scores-vlm-judge.json")
    result = json.load(open(out_path, encoding="utf-8")) if os.path.isfile(out_path) else {}
    fam = result.setdefault(a.family, {})
    server = cc.start_server(a.port, os.path.join(a.out_dir, "comfy-server-judge.log"))
    try:
        for sc, d, name, src in jobs:
            t0 = time.perf_counter()
            answer = cc.run_graph(a.port, judge_graph(name))["4"]["text"][0].strip()
            m = re.search(r"\d+(\.\d+)?", answer)
            rating = float(m.group()) if m else None
            fam.setdefault(sc, {})[str(d["seed"])] = {"rating": rating, "answer": answer[:80], "seconds": round(time.perf_counter() - t0, 1),
                                                     "err_near": d["err_near"], "err_far": d["err_far"], "seam_step": d["seam_step"]}
            print(f"{sc:18s} seed {d['seed']}: rating {rating}  ({answer[:40]!r})  truth far {d['err_far']}", flush=True)
            hs.save_json(result, out_path)
    finally:
        cc.stop_server(server)

    # Does the rating predict the truth? Higher rating should mean lower error: report Spearman(rating, -error).
    corr_all = {}
    for sc, by_seed in fam.items():
        if sc.startswith("_"):
            continue
        rows = [v for v in by_seed.values() if v["rating"] is not None]
        if len(rows) < 3:
            continue
        corr_all[sc] = {m: round(spearman([r["rating"] for r in rows], [-r[m] for r in rows]), 2)
                        for m in ("err_near", "err_far", "seam_step")}
        pick_seed = max(by_seed, key=lambda s: (by_seed[s]["rating"] or -1, -by_seed[s]["seam_step"]))
        pdir = os.path.join(a.out_dir, f"{a.family}-vlmpick", sc)
        os.makedirs(pdir, exist_ok=True)
        for fname in (f"plate-s{a.side}.png", f"raw-s{a.side}.png"):   # the plate has the real centre: its fit is identity
            shutil.copy(os.path.join(fam_dir, sc, f"seed{pick_seed}.png"), os.path.join(pdir, fname))
        print(f"{sc:18s} judge picks seed {pick_seed}  corr {corr_all[sc]}")
    fam["_corr"] = corr_all
    fam["_mean_corr"] = {m: round(float(np.mean([c[m] for c in corr_all.values()])), 2) for m in ("err_near", "err_far", "seam_step")} if corr_all else {}
    hs.save_json(result, out_path)
    print("mean Spearman(rating, -error) over scenes:", fam["_mean_corr"], "(> 0: the judge prefers better candidates)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
