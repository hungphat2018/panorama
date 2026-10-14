"""The user's visible threshold for side breaks, measured once (phase 4 of plans/261013-0718-ai-surround-live-path).

The pass bar is "visible at normal speed on both screens" (the user, 2026-10-14). This ladder measures it in about 5
minutes, then the per-frame gate (side-gate.py) scores every ladder clip, so the bars are set in the gate's own units.
Material: the pan of X1c (JJK S03E12 at 366.9 s, 2.9 s uncut), its truth-pipe (the true sides through the same world
resampling as every X1c version). Two staircases, interleaved:
  break   the side slides off the picture over 3 frames and snaps back: the shape of the pan's frames 5-7 under
          planner-v2 (measured 2026-10-14: 1.3, 2.4, 3.5 px, diagonal 0.62 x : 0.78 y). Size = the third frame's px.
  drift   the side slides off the picture steadily over 1 s (24 frames), then snaps back in one frame: the shape of
          slow-side's frames 30-54. Size = px reached before the snap.
Sizes are strip px (the picture is 960 px wide; on the screens 1 px = 2-2.7 screen px). The side moves outward, so it
shows real content from the picture's side of it (no border fill). Shifts are made on the 2x frame with Lanczos, so the
side keeps its sharpness at sub-pixel sizes. Each trial: "1", a clip, "2", a clip, then "which had a break or a jump in
the sides?" (1 or 2): one clip is truth-pipe, the other has one event on one side. Weighted up-down (1 down after a
right answer, 3 up after a wrong one: 75 % correct; 2 down before the first wrong answer), in side-ladder.lua.
Counterbalanced: in every block of 4 trials each staircase gets one trial with the break first and one second, one on
the left and one on the right.

    python -I side-ladder.py build FILM OUT_DIR [--at 366.9083] [--trials 16] [--seed 5]
    python -I side-ladder.py score LADDER_DIR [--answers ladder-answers-<stamp>.json] [--write-bars]

score fits a 2AFC psychometric function per staircase (maximum likelihood, logistic in log2 size, guess 0.5, lapse
0.02, slope fixed), takes the size picked right 3 times in 4 (x75), renders the event at half that size on both sides
and runs the gate on it against the clean clip (bar_candidates: each measure's value at the event's frames, where the
event moved it above its own noise; bars_from: the mean over the sides it moved, the stricter staircase, and no bar
for a truth-free measure real content reaches anyway). With --write-bars the bars go into live/side-gate.json.
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
sys.path.insert(0, HERE)                                          # python -I leaves the script's folder off sys.path
import hidden_sides as hs  # noqa: E402
from live import side_gate as sg  # noqa: E402
from live import structure  # noqa: E402


def load_script(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), os.path.join(HERE, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ts = load_script("true-sides-test")                               # grab, projective path, clip and card writers
gate = load_script("side-gate")                                   # decode + score_clip

PAN_AT = 366.9083
BREAK_DIR = (0.62, 0.78)                                          # the pan break's direction (x outward, y down)
LADDERS = {
    "break": {"levels": [0.5, 0.7, 1.0, 1.4, 2.0, 2.8, 4.0, 5.7, 8.0, 11.3, 16.0], "start": 7,
              "frames": 3, "onsets": (14, 50)},
    "drift": {"levels": [2.0, 2.8, 4.0, 5.7, 8.0, 11.3, 16.0, 22.6, 32.0, 45.0, 64.0], "start": 7,
              "frames": 24, "onsets": (8, 36)},
}
STEP = {"down": 1, "up": 3, "fast_down": 2}
GUESS, LAPSE, SLOPE = 0.5, 0.02, 3.0                              # 2AFC; slope per octave of size (fixed)
KEYS_LUA = os.path.join(HERE, "side-ladder.lua")


def truth_pipe(film, at):
    """The pan's 69 strips and its truth-pipe (every region refreshed from the truth each frame, one world)."""
    strips = [f[ts.ROW0:ts.ROW0 + ts.ROWS] for f in ts.demo.grab(film, at, ts.FRAMES)]
    W = ts.projective_path(strips)[0]
    n = len(strips)
    return structure.carry_strips(strips, W, [{r: True for r in ts.REGIONS}] * n, [{} for _ in range(n)],
                                  ts.PIC_SLICE, ts.REGIONS)


def offsets(ladder, size, onset):
    """{frame: (dx, dy) strip px, x outward} of one event."""
    if ladder == "break":
        return {onset + k: (BREAK_DIR[0] * size * (k + 1) / 3, BREAK_DIR[1] * size * (k + 1) / 3) for k in range(3)}
    n = LADDERS["drift"]["frames"]
    return {onset + k: (size * (k + 1) / n, 0.0) for k in range(n)}         # back to 0 at onset + n: the snap


def shifted_wide(wide, side, d):
    """The 2x frame with one side's content slid by d (strip px; x outward), the picture and the other side untouched."""
    sgn = -1.0 if side == "left" else 1.0
    m = np.float32([[1, 0, 2 * sgn * d[0]], [0, 1, 2 * d[1]]])
    moved = cv2.warpAffine(wide, m, (wide.shape[1], wide.shape[0]), flags=cv2.INTER_LANCZOS4,
                           borderMode=cv2.BORDER_REPLICATE)
    out = wide.copy()
    cols = slice(0, 2 * ts.SIDE_W) if side == "left" else slice(2 * (ts.PIC0 + ts.PIC_W), wide.shape[1])
    out[:, cols] = moved[:, cols]
    return out


def event_clip(wides, ladder, size, side, onset, path, log):
    offs = offsets(ladder, size, onset)
    frames = (shifted_wide(w, side, offs[t]) if t in offs else w for t, w in enumerate(wides))
    ts.write_span_clip(frames, path, log)
    return offs


def schedule(trials, rng):
    """Blocks of 4: each staircase once with the event first and once second, once left and once right; shuffled."""
    out = []
    for _ in range(trials // 2):
        block = []
        for ladder in LADDERS:
            sides = ["left", "right"]
            rng.shuffle(sides)
            block += [{"ladder": ladder, "side": sides[0], "event_first": True},
                      {"ladder": ladder, "side": sides[1], "event_first": False}]
        rng.shuffle(block)
        out += block
    return out


def longest_run(seq):
    best = cur = 1
    for a, b in zip(seq, seq[1:]):
        cur = cur + 1 if a == b else 1
        best = max(best, cur)
    return best


def build(a):
    odir = os.path.join(a.out_dir, f"{time.strftime('%Y%m%d-%H%M%S')}-side-ladder")
    for sub in ("clips", "prompts"):
        os.makedirs(os.path.join(odir, sub), exist_ok=True)
    log = os.path.join(odir, "ffmpeg.log")
    rng = random.Random(a.seed)
    t0 = time.perf_counter()
    wides = [ts.to_wide(s) for s in truth_pipe(a.film, a.at)]
    print(f"truth-pipe of the pan: {len(wides)} frames, {time.perf_counter() - t0:.0f} s", flush=True)
    ts.write_span_clip(wides, os.path.join(odir, "clips", "clean.mp4"), log)
    plan = {"film": a.film, "at": a.at, "made": time.strftime("%Y-%m-%d %H:%M"), "step": STEP, "clean": "clips/clean.mp4",
            "labels": ["label-1.mp4", "label-2.mp4"], "ladders": {}}
    for name, spec in LADDERS.items():
        entry = {"levels": spec["levels"], "start": spec["start"], "clips": {"left": [], "right": []},
                 "onsets": {"left": [], "right": []}}
        for i, size in enumerate(spec["levels"]):
            for side in ("left", "right"):
                onset = rng.randint(*spec["onsets"])
                rel = f"clips/{name}-{side}-{i:02d}.mp4"
                event_clip(wides, name, size, side, onset, os.path.join(odir, rel), log)
                entry["clips"][side].append(rel)
                entry["onsets"][side].append(onset)
            print(f"  {name} {size:g} px: {time.perf_counter() - t0:.0f} s", flush=True)
        plan["ladders"][name] = entry
    for k in (1, 2):
        ts.write_span_clip([ts.card([str(k)], big=True)] * 12, os.path.join(odir, f"label-{k}.mp4"), log)
    for k in range(200):                                            # no run longer than 3 (order, side, staircase)
        sched = schedule(a.trials + a.trials % 2, random.Random(a.seed * 1000 + k))
        if max(longest_run([s[f] for s in sched]) for f in ("ladder", "event_first", "side")) <= 3:
            break
    plan["schedule"] = sched
    n = len(sched)
    cards = {
        "start": ["Side ladder: about 5 minutes.", "Each trial: clip 1, then clip 2.",
                  "One of them has a break or a jump in the sides, left or right.",
                  "Press 1 or 2. When unsure, guess.", "First two examples.  Press space to begin."],
        "example-break": ["Example 1: a break at the seam", "(left side, made large)"],
        "example-drift": ["Example 2: a slow slide, then a jump back", "(right side, made large)"],
        "go": [f"Now the test: {n} trials.", "Watch the middle picture as you normally would.",
               "After each pair: 1 or 2."],
        "end": ["End of the ladder", "Answers saved in ladder-answers-<time>.json"],
    }
    for key, lines in cards.items():
        ts.write_card_png(ts.card(lines), os.path.join(odir, "prompts", f"{key}.png"))
    for k in range(1, n + 1):
        ts.write_card_png(ts.card([f"Trial {k} of {n}", "Which had a break or a jump in the sides?  1 or 2"]),
                          os.path.join(odir, "prompts", f"trial-{k:02d}.png"))
    ex = {"break": ("left", len(LADDERS["break"]["levels"]) - 2), "drift": ("right", len(LADDERS["drift"]["levels"]) - 3)}
    plan["examples"] = ["prompts/example-break.png", plan["ladders"]["break"]["clips"][ex["break"][0]][ex["break"][1]],
                        "prompts/example-drift.png", plan["ladders"]["drift"]["clips"][ex["drift"][0]][ex["drift"][1]],
                        "prompts/go.png"]
    hs.save_json(plan, os.path.join(odir, "ladder.json"))
    with open(os.path.join(odir, "ladder.m3u"), "w", encoding="utf-8", newline="\n") as f:
        f.write("#EXTM3U\nprompts/start.png\n")
    args = "--fs --fs-screen=all --video-unscaled=yes --hwdec=no --osc=no --really-quiet --keep-open=yes"
    with open(os.path.join(odir, "play-ladder.bat"), "w", encoding="utf-8", newline="") as bat:
        bat.write("@echo off\r\nrem The side ladder: two examples, then the trials. 1 or 2 answers; q quits (answers so far "
                  "are kept).\r\n"
                  f'start "" "{ts.demo.MPV}" {args} --image-display-duration=4 --script="{KEYS_LUA}" '
                  '--playlist="%~dp0ladder.m3u"\r\n')
    print(f"schedule: {n} trials, longest run of one staircase {longest_run([s['ladder'] for s in sched])}, of event "
          f"first {longest_run([s['event_first'] for s in sched])}, of one side {longest_run([s['side'] for s in sched])}")
    print(f"done: {odir} ({time.perf_counter() - t0:.0f} s)")


# ---- scoring ----------------------------------------------------------------------------------------------------------

def p_right(size, alpha, slope=SLOPE):
    return GUESS + (1 - GUESS - LAPSE) / (1 + math.exp(-slope * (math.log2(size) - math.log2(alpha))))


def fit(trials):
    """(x75, (lo, hi) 95 % likelihood interval, log-likelihood curve) of a staircase's trials [(size, right)]."""
    grid = np.exp2(np.linspace(-3, 8, 1101))                       # 0.125 .. 256 px
    ll = np.array([sum(math.log(p_right(s, a)) if r else math.log(1 - p_right(s, a)) for s, r in trials)
                   for a in grid])
    best = int(np.argmax(ll))
    inside = grid[ll >= ll[best] - 1.92]
    # x75: where p_right = 0.75 for the best alpha
    target = (0.75 - GUESS) / (1 - GUESS - LAPSE)
    shift = math.log(target / (1 - target)) / SLOPE
    x75 = float(grid[best] * 2 ** shift)
    return x75, (float(inside.min() * 2 ** shift), float(inside.max() * 2 ** shift))


def reversals(sizes_right):
    """Sizes at the staircase's reversals (direction changes)."""
    out, last = [], None
    for k in range(1, len(sizes_right)):
        d = sizes_right[k][0] - sizes_right[k - 1][0]
        if d == 0:
            continue
        dirn = d > 0
        if last is not None and dirn != last:
            out.append(sizes_right[k - 1][0])
        last = dirn
    return out


def score(a):
    ldir = os.path.abspath(a.ladder_dir)
    plan = json.load(open(os.path.join(ldir, "ladder.json"), encoding="utf-8"))
    answers = a.answers or max((f for f in os.listdir(ldir) if f.startswith("ladder-answers-")), default=None)
    if not answers:
        raise SystemExit("no ladder-answers-*.json in the ladder folder")
    log = json.load(open(os.path.join(ldir, answers), encoding="utf-8"))
    res = {"answers": answers, "ladders": {}}
    for name in plan["ladders"]:
        tr = [t for t in log["trials"] if t["ladder"] == name]
        pairs = [(float(t["size"]), bool(t["right"])) for t in tr]
        if not pairs:
            continue
        x75, ci = fit(pairs)
        revs = reversals(pairs)
        by_pos = {"event first": [sum(t["right"] for t in tr if t["event_first"]), sum(t["event_first"] for t in tr)],
                  "event second": [sum(t["right"] for t in tr if not t["event_first"]),
                                   sum(not t["event_first"] for t in tr)]}
        res["ladders"][name] = {"trials": len(pairs), "right": sum(r for _, r in pairs), "x75_px": round(x75, 2),
                                "x75_interval_px": [round(ci[0], 2), round(ci[1], 2)],
                                "reversals_px": revs, "reversal_mean_px": round(float(np.mean(revs[-4:])), 2) if revs else None,
                                "by_position": by_pos, "bar_size_px": round(x75 / 2, 2),
                                "sizes": [s for s, _ in pairs], "rights": [int(r) for _, r in pairs],
                                "edge_warning": ci[1] >= plan["ladders"][name]["levels"][-1] or ci[0] <= plan["ladders"][name]["levels"][0]}
        print(f"{name}: x75 {x75:.2f} px (95 % {ci[0]:.2f}-{ci[1]:.2f}), right {res['ladders'][name]['right']}/{len(pairs)}, "
              f"reversals {revs}; by position {by_pos}")
    res["bars"] = bar_candidates(ldir, plan, res, a)
    cfg = json.load(open(sg.BARS_PATH, encoding="utf-8")) if os.path.exists(sg.BARS_PATH) else {}
    res["proposed_bars"] = bars_from(res, ldir, cfg.get("normal", {}).get("extreme", {}))
    hs.save_json(res, os.path.join(ldir, "ladder-result.json"))
    for m, b in res["proposed_bars"].items():
        print(f"  {m:12s} {b}")
    if a.write_bars:
        write_bars(res, ldir)


MARGIN = {"seam": 0.5, "line": 0.5, "jump": 0.5, "drift": 0.3, "seam_motion": 0.3, "seam_drift": 0.5, "pop": 0.5}


def bar_candidates(ldir, plan, res, a):
    """Render each staircase's event at half its x75 on both sides (onset mid-clip) and run the gate against the clean
    clip. Per measure and side: its largest value over the event's frames (and the frame after: the snap), the same
    measure's largest value off the event (frames more than 2 away: codec and measure noise on identical content),
    and whether the event moved it (value > max(2 x noise, noise + MARGIN)). The pan's two sides differ (the left
    seam band is a flat wall at the onset; outward on the right runs with the pan, so its snap is a one-frame pause),
    so a side the event did not move gives no candidate."""
    gdir = os.path.join(ldir, "half-size")
    os.makedirs(gdir, exist_ok=True)
    wides = None
    clean = os.path.join(ldir, plan["clean"])
    cand = {}
    for name, r in res["ladders"].items():
        size = r["bar_size_px"]
        onset = sum(LADDERS[name]["onsets"]) // 2
        frames_hit = sorted(set(offsets(name, size, onset)) | {onset + LADDERS[name]["frames"]})
        for side in ("left", "right"):
            path = os.path.join(gdir, f"{name}-{side}-{size:g}px.mp4")
            if not os.path.exists(path):
                if wides is None:
                    wides = [ts.to_wide(s) for s in truth_pipe(plan["film"], plan["at"])]
                event_clip(wides, name, size, side, onset, path, os.path.join(gdir, "ffmpeg.log"))
            _, values = gate.score_clip(path, clean, f"{name}-{side}", gdir, {}, label=f"{name} {size:g}px")
            i = [s.name for s in sg.SIDES].index(side)
            n = len(values["seam"])
            hit = [t for t in frames_hit if t < n]
            off = [t for t in range(1, n) if t < hit[0] - 2 or t > hit[-1] + 2]
            vals = {}
            for m in sg.TRUTH_MEASURES + sg.FREE_MEASURES:
                if m in sg.LOW_IS_BAD:                            # a slid side does not soften: not the ladder's
                    continue
                xs, ys = values[m][hit, i], values[m][off, i]
                if np.all(np.isnan(xs)):
                    continue
                v = float(np.nanmax(xs))
                noise = 0.0 if np.all(np.isnan(ys)) else max(0.0, float(np.nanmax(ys)))
                vals[m] = {"value": round(v, 3), "noise": round(noise, 3),
                           "moved": bool(v > max(2 * noise, noise + MARGIN.get(m, 0.5)))}
            cand.setdefault(name, {})[side] = vals
    return cand


LADDER_MEASURES = ("seam", "line", "jump", "drift", "seam_motion", "seam_drift", "pop")   # what a slid side moves


def bars_from(res, ldir, normal):
    """{measure: bar} for the measures the ladder moves. Per staircase: the mean over the sides the half-size event
    moved (the trials alternated sides, and codes measures depend on the content there); then the smaller over the
    staircases (the stricter). A measure the event moved nowhere gets {unset}. A truth-free one whose bar is not above
    real content's own extreme (truth-pipe's normal range) cannot separate a break from real content: {unset}, and the
    truth-based gate decides in tests (the phase's risk rule). The other bars (detail, boil, seam step...) are not the
    ladder's and stay as live/side-gate.json has them."""
    bars, src = {}, os.path.basename(ldir)
    for m in LADDER_MEASURES:
        per = []
        for name, sides in res["bars"].items():
            moved = [(v[m]["value"], side) for side, v in sides.items() if m in v and v[m]["moved"]]
            if moved:
                per.append((float(np.mean([x for x, _ in moved])), f"{name} ({', '.join(s for _, s in moved)})"))
        if not per:
            bars[m] = {"unset": "the half-size events did not move it above its noise on either side"}
            continue
        val, where = min(per)
        val = round(val, 3)
        extreme = normal.get(m)
        if m in sg.FREE_MEASURES and extreme is not None and val <= extreme:
            bars[m] = {"unset": f"cannot separate: the ladder's half-size event gives {val} ({where}), real content "
                                f"reaches {extreme} (truth-pipe's normal range)"}
            continue
        bars[m] = {"max": val, "source": f"side ladder {src}: half of x75, {where}"}
    return bars


def write_bars(res, ldir):
    """live/side-gate.json: the ladder's bars and its record; every other bar is kept."""
    path = sg.BARS_PATH
    cfg = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {"bars": {}}
    cfg["bars"].update(bars_from(res, ldir, cfg.get("normal", {}).get("extreme", {})))
    cfg["ladder"] = {k: {kk: v[kk] for kk in ("x75_px", "x75_interval_px", "bar_size_px", "trials", "right")}
                     for k, v in res["ladders"].items()}
    cfg["ladder"]["folder"] = os.path.relpath(ldir, os.path.dirname(path))
    hs.save_json(cfg, path)
    print(f"bars written to {path}")


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("film")
    b.add_argument("out_dir")
    b.add_argument("--at", type=float, default=PAN_AT)
    b.add_argument("--trials", type=int, default=16, help="trials per staircase (even)")
    b.add_argument("--seed", type=int, default=5)
    s = sub.add_parser("score")
    s.add_argument("ladder_dir")
    s.add_argument("--answers")
    s.add_argument("--write-bars", action="store_true")
    a = ap.parse_args(argv)
    cv2.setNumThreads(4)
    ts.below_normal_priority()
    build(a) if a.cmd == "build" else score(a)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
