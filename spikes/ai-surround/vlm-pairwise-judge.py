"""The blind test, taken by a vision-language model (role R6): which of two frames is the real one?

vlm-judge.py asked Qwen3-VL-8B for a 1-10 rating of one canvas; it answered 7 for nearly everything. Here the model gets
the question the user gets in make-blind-test.py: two versions of the same moment, one real, one whose outer quarters
were drawn by a candidate; which is real? Both canvases go in one picture, one above the other, labelled A and B; every
pair is asked twice with the order swapped, so a model that always says "A" scores 50%. Frames: the clip's middle frame
and the frames at 1/4 and 3/4. Per candidate fill: "picked as real" = how often the model took the candidate for the real
one (about 50%: it cannot tell; 0%: always spotted), the same number the user's blind test gives, so the two rankings can
be compared: if they agree, this judge can rank recipes and pick best-of-N without the user.

    python -I vlm-pairwise-judge.py SCENES_DIR RUNS_DIR --fills a,b,... [--scenes a,b] [--side 0.5] [--port 8189]

Reads RUNS_DIR/<fill>/<scene>/s<side>.mp4. Writes RUNS_DIR/scores-vlm-pairwise.json (answers per pair and order, and
picked-as-real per fill and scene) and the server log RUNS_DIR/comfy-server-judge.log.
"""
import argparse
import json
import os
import re
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # python -I leaves the script's folder off sys.path
import comfy_client as cc  # noqa: E402
import hidden_sides as hs  # noqa: E402

QUESTION = ("This picture shows two versions, A (top) and B (bottom), of the same moment of an anime. One is the real "
            "frame of the show. In the other, the left quarter and the right quarter were removed and redrawn by a "
            "computer from the middle half. Look at the outer quarters: do the place, the characters, the scale, the "
            "drawing style and the colours continue the middle exactly as the animators drew it? Which version is the "
            "real frame? Answer with one letter: A or B.")


def judge_graph(image, max_length=6):
    return {
        "1": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen3vl_8b_bf16.safetensors", "type": "qwen_image"}},
        "2": {"class_type": "LoadImage", "inputs": {"image": image}},
        "3": {"class_type": "TextGenerate", "inputs": {"clip": ["1", 0], "prompt": QUESTION, "image": ["2", 0],
                                                       "max_length": max_length, "sampling_mode": "off"}},
        "4": {"class_type": "PreviewAny", "inputs": {"source": ["3", 0]}},
    }


def pair_image(top, bottom, width=1280):
    """Two canvases one above the other, each with its letter, scaled to `width`."""
    rows = []
    for img, label in ((top, "A"), (bottom, "B")):
        h = int(round(img.shape[0] * width / img.shape[1]))
        im = cv2.resize(img, (width, h), interpolation=cv2.INTER_AREA)
        strip = np.zeros((28, width, 3), np.uint8)
        cv2.putText(strip, label, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2, cv2.LINE_AA)
        rows += [strip, im]
    return np.vstack(rows)


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("scenes_dir")
    ap.add_argument("runs_dir")
    ap.add_argument("--fills", required=True)
    ap.add_argument("--scenes", default="")
    ap.add_argument("--side", type=float, default=0.5)
    ap.add_argument("--port", type=int, default=8189)
    a = ap.parse_args(argv)
    only = set(filter(None, a.scenes.split(",")))

    # Every input picture is written before the server starts, so its file list includes them.
    jobs = []
    for sc in sorted(os.listdir(a.scenes_dir)):
        if (only and sc not in only) or not os.path.isfile(os.path.join(a.scenes_dir, sc, "clip.mkv")):
            continue
        clip = None
        for fill in a.fills.split(","):
            path = os.path.join(a.runs_dir, fill, sc, f"s{a.side}.mp4")
            if not os.path.isfile(path):
                continue
            cand = hs.read_frames(path)
            if clip is None:
                clip = [hs.split(f, a.side)[0] for f in hs.read_frames(os.path.join(a.scenes_dir, sc, "clip.mkv"))]
            n = min(len(cand), len(clip))
            for t in sorted({n // 4, n // 2, 3 * n // 4}):
                for order in ("real-first", "cand-first"):
                    top, bottom = (clip[t], cand[t]) if order == "real-first" else (cand[t], clip[t])
                    name = f"panorama-pair-{fill}-{sc}-{t}-{order}.png"
                    cv2.imwrite(os.path.join(cc.INPUT_DIR, name), cv2.cvtColor(pair_image(top, bottom), cv2.COLOR_RGB2BGR))
                    jobs.append({"fill": fill, "scene": sc, "frame": t, "order": order, "image": name})
    if not jobs:
        raise SystemExit("no candidate videos found")

    scores_path = os.path.join(a.runs_dir, "scores-vlm-pairwise.json")
    server = cc.start_server(a.port, os.path.join(a.runs_dir, "comfy-server-judge.log"))
    try:
        for j in jobs:
            t0 = time.perf_counter()
            out = cc.run_graph(a.port, judge_graph(j["image"]))
            text = out.get("4", {}).get("text", [""])[0].strip()
            m = re.search(r"\b([AB])\b", text.upper())
            j["answer"], j["text"], j["seconds"] = (m.group(1) if m else "?"), text, round(time.perf_counter() - t0, 1)
            real_letter = "A" if j["order"] == "real-first" else "B"
            j["fooled"] = None if j["answer"] == "?" else j["answer"] != real_letter
            print(f"{j['fill']:44s} {j['scene']:22s} f{j['frame']:<3d} {j['order']:10s} -> {j['answer']} "
                  f"({'fooled' if j['fooled'] else 'spotted' if j['fooled'] is False else 'no answer'})", flush=True)
    finally:
        cc.stop_server(server)

    summary = {}
    for j in jobs:
        s = summary.setdefault(j["fill"], {}).setdefault(j["scene"], {"n": 0, "fooled": 0, "first_letter": {"A": 0, "B": 0, "?": 0}})
        s["first_letter"][j["answer"]] += 1
        if j["fooled"] is not None:
            s["n"] += 1
            s["fooled"] += int(j["fooled"])
    for fill, by in summary.items():
        n = sum(v["n"] for v in by.values())
        f = sum(v["fooled"] for v in by.values())
        print(f"{fill:44s} picked as real {f}/{n} ({100 * f / max(1, n):.0f}%)")
    hs.save_json({"question": QUESTION, "jobs": jobs, "summary": summary}, scores_path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
