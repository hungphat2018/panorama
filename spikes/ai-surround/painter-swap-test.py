"""Swap test (phase 3): on the two screens, can the user tell today's best still plate from the live painter's? The
reference is qwen21-outpaint-v2 (Qwen-Image-2.1 + outpaint LoRA v2, 25 steps, bf16 with the caption pass, ~2 MP,
comfy-plates.py); the candidate is a painter-bench plate set (the resident live recipe). Both are the hidden-sides
composite at s = 0.5 (the real centre pasted between the painted sides), without colour fit or seam pull, which the
product adds to both alike.

Display: the middle 540 rows of each 1920x1080 plate, a 32:9 strip whose centre 960x540 is a 16:9 picture with a side
half its width on each hand (the product's geometry), at 2x over both screens like X1 (make-wide-demo's 'fill').
Per trial: "1", one plate (held 5 s), "2", the other, then "Which one's sides look more like the film's own?" -> press
1 or 2 (trial-answer-keys.lua saves answers-<stamp>.txt). Pass (plan, phase 3): the reference is picked at most 12 of
20 times. Sham trials (the same plate twice) check for an answer bias.

    python -I -X utf8 painter-swap-test.py CANDIDATE_PLATES OUT_DIR [--reference out/runs/qwen21-outpaint-v2]
        [--trials 20] [--shams 2] [--seed 5] [--name live-w4a8]
    python -I -X utf8 painter-swap-test.py --score TEST_DIR "1,2,..."

CANDIDATE_PLATES: a painter-bench folder of <scene>.png (plates/<dit>/<read>/) or a runs/<recipe> folder of
<scene>/plate-s0.5.png; or NAME=FOLDER,NAME=FOLDER: one session interleaving several candidates, each against the
reference, --trials each, scored per candidate. Every candidate's order is counterbalanced. Writes
OUT_DIR/<stamp>-swap-<name>/: play-trials.bat, trials.m3u, prompts/, plates/ (neutral names), key.json (do not open
before answering), after/<scene>.png (truth, reference, candidates) for after the test.
"""
import argparse
import importlib.util
import json
import math
import os
import random
import sys
import time

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import hidden_sides as hs  # noqa: E402


def load_script(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), os.path.join(HERE, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ts = load_script("true-sides-test")                               # card, write_card_png, write_span_clip, KEYS_LUA
demo = ts.demo
ROW0, ROWS = 270, 540
HOLD_S = 5
QUESTION = "Which one's sides look more like the film's own?"


def plate_path(folder, scene):
    for p in (os.path.join(folder, f"{scene}.png"), os.path.join(folder, scene, "plate-s0.5.png")):
        if os.path.isfile(p):
            return p
    return None


def composed(plate_rgb):
    """The plate's 32:9 strip at 2x, composed for the two screens (the desktop canvas X1 wrote)."""
    strip = plate_rgb[ROW0:ROW0 + ROWS]
    wide = cv2.resize(strip, (2 * strip.shape[1], 2 * strip.shape[0]), interpolation=cv2.INTER_LANCZOS4)
    screens = demo.SCREENS
    desk_w, desk_h = screens[1]["x"] + screens[1]["w"], max(s["h"] for s in screens)
    canvas = np.zeros((desk_h, desk_w, 3), np.uint8)
    for half, sc in ((wide[:, :1920], screens[0]), (wide[:, 1920:], screens[1])):
        canvas[:sc["h"], sc["x"]:sc["x"] + sc["w"]] = demo.compose(half, sc, "fill", 0)
    return canvas


def parse_candidates(spec):
    """CANDIDATE_PLATES: one folder (named "candidate"), or NAME=FOLDER,NAME=FOLDER for one session that interleaves
    several candidates, each against the reference."""
    out = {}
    for item in spec.split(","):
        name, folder = item.split("=", 1) if "=" in item else ("candidate", item)
        if name in out or name == "reference":
            raise SystemExit(f"candidate name {name!r} is taken")
        out[name] = folder
    return out


def counterbalanced(order, rng, odd_first):
    """Per trial, True where the reference is shown first: in half the trials (the odd one out: odd_first), and a scene
    shown twice gets both orders (2026-10-14: the user picked the second plate in 29 of 44 answers, so an uneven order
    would tilt the score)."""
    singles = [sc for sc in dict.fromkeys(order) if order.count(sc) == 1]
    firsts = [True, False] * (len(singles) // 2) + [odd_first] * (len(singles) % 2)
    rng.shuffle(firsts)
    first_of, out = dict(zip(singles, firsts)), []
    for sc in order:
        if sc not in first_of:                                    # a scene's first showing: either order; its second: the other
            first_of[sc] = rng.random() < 0.5
            out.append(first_of[sc])
        else:
            out.append(first_of[sc] if sc in singles else not first_of[sc])
    return out


def build(a):
    reference = a.reference or os.path.join(HERE, "out", "runs", "qwen21-outpaint-v2")
    cands = parse_candidates(a.candidate)
    scenes = sorted(d for d in os.listdir(os.path.join(HERE, "out", "scenes"))
                    if plate_path(reference, d) and all(plate_path(f, d) for f in cands.values()))
    if not scenes:
        raise SystemExit("no scene has every plate")
    rng = random.Random(a.seed)
    odir = os.path.join(a.out_dir, f"{time.strftime('%Y%m%d-%H%M%S')}-swap-{a.name}")
    for sub in ("plates", "prompts"):
        os.makedirs(os.path.join(odir, sub), exist_ok=True)
    ids = {}
    for sc in scenes:
        for who, folder in [("reference", reference)] + list(cands.items()):
            rgb = cv2.cvtColor(cv2.imread(plate_path(folder, sc)), cv2.COLOR_BGR2RGB)
            if rgb.shape[:2] != (1080, 1920):
                rgb = cv2.resize(rgb, (1920, 1080), interpolation=cv2.INTER_AREA)
            cid = "".join(rng.choice("0123456789abcdef") for _ in range(8))
            ids[(sc, who)] = cid
            cv2.imwrite(os.path.join(odir, "plates", f"{cid}.png"), cv2.cvtColor(composed(rgb), cv2.COLOR_RGB2BGR))
        # after the test: truth / reference / each candidate, labelled
        truth = cv2.imread(os.path.join(HERE, "out", "scenes", sc, "key.png"))
        rows = []
        for label, img in [("truth", truth), ("reference 25-step", cv2.imread(plate_path(reference, sc)))] + [
                (f"candidate {a.name}" if name == "candidate" else name, cv2.imread(plate_path(f, sc)))
                for name, f in cands.items()]:
            if img is None:
                continue
            img = cv2.resize(img, (1920, 1080), interpolation=cv2.INTER_AREA)[ROW0:ROW0 + ROWS]
            cv2.putText(img, label, (10, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 255, 255), 3, cv2.LINE_AA)
            rows.append(img)
        os.makedirs(os.path.join(odir, "after"), exist_ok=True)
        cv2.imwrite(os.path.join(odir, "after", f"{sc}.png"), np.vstack(rows))
    trials = []
    for ci, name in enumerate(cands):                             # --trials per candidate
        order = scenes + [scenes[i % len(scenes)] for i in rng.sample(range(len(scenes)), min(len(scenes), max(0, a.trials - len(scenes))))]
        order = order[:a.trials]
        for sc, first_ref in zip(order, counterbalanced(order, rng, odd_first=ci % 2 == 0)):
            pair = ["reference", name] if first_ref else [name, "reference"]
            trials.append({"scene": sc, "level": "swap", "candidate": name, "pair": pair,
                           "reference_is": 1 if first_ref else 2})
    for k in range(a.shams):
        sc = rng.choice(scenes)
        who = rng.choice(["reference"] + list(cands))
        trials.append({"scene": sc, "level": "sham", "pair": [who, who], "reference_is": None})
    rng.shuffle(trials)
    log = os.path.join(odir, "ffmpeg.log")
    for t in (1, 2):
        ts.write_span_clip([ts.card([str(t)], big=True)] * 12, os.path.join(odir, f"label-{t}.mp4"), log)
    ts.write_card_png(ts.card(["Look at the sides as you would while watching.", "Each trial: 1, then 2.", QUESTION]),
                      os.path.join(odir, "prompts", "start.png"))
    lines = ["#EXTM3U", "prompts/start.png"]
    for k, tr in enumerate(trials, 1):
        tr["trial"] = k
        ts.write_card_png(ts.card([f"Trial {k} of {len(trials)}", QUESTION, "1 or 2"]),
                          os.path.join(odir, "prompts", f"trial-{k:02d}.png"))
        lines += ["label-1.mp4", f"plates/{ids[(tr['scene'], tr['pair'][0])]}.png",
                  "label-2.mp4", f"plates/{ids[(tr['scene'], tr['pair'][1])]}.png", f"prompts/trial-{k:02d}.png"]
    ts.write_card_png(ts.card(["End of the test", "Answers saved in answers-<time>.txt"]),
                      os.path.join(odir, "prompts", "end.png"))
    lines.append("prompts/end.png")
    with open(os.path.join(odir, "trials.m3u"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    args = "--fs --fs-screen=all --video-unscaled=yes --hwdec=no --osc=no --really-quiet"
    with open(os.path.join(odir, "play-trials.bat"), "w", encoding="utf-8", newline="") as bat:
        bat.write('@echo off\r\nrem Swap test: per trial "1", a plate, "2", a plate, then the question: press 1 or 2. Space '
                  'pauses, q quits.\r\n'
                  f'start "" "{demo.MPV}" {args} --image-display-duration={HOLD_S} --script="{ts.KEYS_LUA}" '
                  '--playlist="%~dp0trials.m3u"\r\n')
    hs.save_json({"reference": reference, "candidate": a.candidate, "candidates": cands, "name": a.name,
                  "question": QUESTION, "trials": trials, "ids": {f"{s}/{w}": c for (s, w), c in ids.items()}},
                 os.path.join(odir, "key.json"))
    print(f"done: {odir} ({len(trials)} trials, {len(scenes)} scenes, candidates {', '.join(cands)})")
    return 0


def verdict_of(picked_ref, n):
    """(one-sided binomial p at chance 0.5, verdict). Pass (plan, phase 3): the reference picked at most 12 in 20."""
    p = sum(math.comb(n, i) for i in range(picked_ref, n + 1)) / 2 ** n
    return p, ("pass: the reference is not reliably preferred" if picked_ref <= 12 * n / 20 else
               "fail: the reference is preferred" if p <= 0.06 else "unclear: more trials")


def score(test_dir, answers):
    key = json.load(open(os.path.join(test_dir, "key.json"), encoding="utf-8"))["trials"]
    ans = [int(x) for x in answers.replace(" ", "").split(",") if x]
    if len(ans) != len(key):
        raise SystemExit(f"{len(ans)} answers for {len(key)} trials")
    pairs = list(zip(sorted(key, key=lambda t: t["trial"]), ans))
    shams = [a for tr, a in pairs if tr["level"] == "sham"]
    out = {"sham_picked_1": sum(a == 1 for a in shams), "shams": len(shams), "by_candidate": {}}
    for name in dict.fromkeys(tr.get("candidate", "candidate") for tr, _ in pairs if tr["level"] == "swap"):
        swap = [(tr, a) for tr, a in pairs if tr["level"] == "swap" and tr.get("candidate", "candidate") == name]
        picked_ref, n = sum(a == tr["reference_is"] for tr, a in swap), len(swap)
        p, verdict = verdict_of(picked_ref, n)
        c = {"reference_picked": picked_ref, "trials": n, "p_chance": round(p, 3), "verdict": verdict,
             # the user leans to the second plate: the reference's picks when it was shown first / second
             "reference_picked_when_first": f"{sum(a == 1 for tr, a in swap if tr['reference_is'] == 1)}/"
                                            f"{sum(tr['reference_is'] == 1 for tr, _ in swap)}",
             "reference_picked_when_second": f"{sum(a == 2 for tr, a in swap if tr['reference_is'] == 2)}/"
                                             f"{sum(tr['reference_is'] == 2 for tr, _ in swap)}",
             "by_scene": {}}
        for tr, a in swap:
            d = c["by_scene"].setdefault(tr["scene"], {"reference": 0, "candidate": 0})
            d["reference" if a == tr["reference_is"] else "candidate"] += 1
        out["by_candidate"][name] = c
    if len(out["by_candidate"]) == 1:                             # one candidate: its numbers at the top, as before
        out.update(next(iter(out["by_candidate"].values())))
    hs.save_json(out, os.path.join(test_dir, "result.json"))
    for name, c in out["by_candidate"].items():
        print(f"{name}: reference picked {c['reference_picked']}/{c['trials']} (p {c['p_chance']:.3f}): {c['verdict']}; "
              f"when shown first {c['reference_picked_when_first']}, second {c['reference_picked_when_second']}")
        for sc, d in sorted(c["by_scene"].items()):
            print(f"  {sc:24s} reference {d['reference']}  candidate {d['candidate']}")
    print(f"shams: 1 picked {out['sham_picked_1']} of {len(shams)}")
    return 0


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("candidate", nargs="?")
    ap.add_argument("out_dir", nargs="?")
    ap.add_argument("--reference", default="")
    ap.add_argument("--trials", type=int, default=20)
    ap.add_argument("--shams", type=int, default=2)
    ap.add_argument("--seed", type=int, default=5)
    ap.add_argument("--name", default="live")
    ap.add_argument("--score", nargs=2, metavar=("TEST_DIR", "ANSWERS"))
    a = ap.parse_args(argv)
    if a.score:
        return score(*a.score)
    if not (a.candidate and a.out_dir):
        ap.error("CANDIDATE_PLATES and OUT_DIR are needed to build the test")
    return build(a)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
