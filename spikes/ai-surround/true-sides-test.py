"""True-sides real-use test: how often must the sides change, and can the camera carry them between changes?

No generator. A 32:9 strip is cut from the middle 540 rows of real 1080p frames: its centre 960 px is the "picture" and
the outer 480 px each side are TRUE sides (real pixels of the same frame). Shown at 2x over the two screens
(make-wide-demo's 'fill' layout) the geometry is the product's (16:9 picture, each side half its width), so any
difference between versions is timing, never invention. The picture is always the real one; the sides are:
  truth       the true sides every frame
  frozen      the true sides of frame 0, held (positive control: the still plate that "didn't maintain the continuity")
  warp-<k>hz  refreshed from the truth k times a second; in between carried by the camera: the last known sides and
              the picture's own pixels leaving it (trails) slid by the camera step measured on the picture only
              (scroll-plate's flow mode), one resampling from a world canvas (no blur build-up)
  planner     the structure path with a perfect zone painter: a side is refreshed from the truth only when the picture's
              band next to it changed in a way no hold, camera slide or brightness gain explains (more than TAU of the
              band's pixels by more than DIFF levels); otherwise carried by the camera (+ that gain). Reads the picture
              only, never the hidden truth, to decide.

Writes OUT_DIR/<stamp>-true-sides/:
  play-trials.bat + trials.m3u   blind forced choice: per trial "1", one version, "2", the other, then the question
                                 "which had the odd sides?"; press 1 or 2 (trial-answer-keys.lua saves answers-<stamp>.txt)
  key.json                       the answers (do not open before answering)
  free-look/<moment>/            the same clips with real names, play-free-look-<moment>.bat (> and < switch)
  metrics.json, sheets/          per version: error vs truth near/far, side change per frame vs the picture's, refreshes/s

    python -I true-sides-test.py FILM OUT_DIR [--at 1314.3963,1226.0583,366.9083,1292.7913] [--seed 11] [--same-resampling]
    python -I true-sides-test.py --score TEST_DIR "1,2,2,1,..."

X1b (--same-resampling, 2026-10-14): X1 scored 40/40 visible, with every carried version spotted, even 6 Hz on the calmest
moment (2-3 codes from the truth). Its carried frames were resampled through the world and its refresh frames were
raw truth, so the sides' sharpness pulsed at the refresh rate, and nothing controlled for the resampling itself. X1b
reads every version's sides back from the world each frame and compares them with truth-pipe (the truth refreshed
every frame through the same world); the "pipe" level (raw truth against truth-pipe) shows whether the resampling
alone is visible. Output folder <stamp>-true-sides-b.

X1c (--x1c, 2026-10-14, phase 4; replaces X1b's trials, keeps its world rule and its pipe control): the cadence question.
X1's planner refreshed only at U > 0.10 per step, had no trigger for change that builds up, and slid a translation.
Every version goes through one projective world (live/structure.carry_strips) on the camera model's path
(live/camera_model) and is compared with truth-pipe:
  planner-v2  the step planner (live/step_planner: PATCH at U_step > 0.02, GEN at > 0.10, the drift trigger, the
              near-seam rule): a flagged side is redrawn from the truth whole (a perfect generator), the rest carried
  near-every  the inner half of each side redrawn from the truth at every new drawing (per-drawing generation next to
              the seam), the outer half as planner-v2
plus frozen, pipe and sham. Output folder <stamp>-true-sides-c; metrics also carry the motion correlation (side change
per frame against the picture's) and boil. --x1c-metrics N measures the same versions, plus the camera check
(translation against projective carry, no refresh after frame 0), on N more moments without building clips.

Since phase 4's replan (2026-10-14): every build ends with the per-frame gate on every version (side-gate.py, into
<test>/gate/; --no-gate skips it), so no test reaches the user unchecked; each level puts the reference first in half
its trials (the user leans to the second clip); a trial whose two clips are the same file is left out.
"""
import argparse
import collections
import importlib.util
import json
import math
import os
import random
import shutil
import subprocess
import sys
import time

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)                                          # python -I leaves the script's folder off sys.path
import hidden_sides as hs  # noqa: E402
from live import camera_model as cm  # noqa: E402
from live import step_planner as sp  # noqa: E402
from live import structure  # noqa: E402


def load_script(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), os.path.join(HERE, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


demo = load_script("make-wide-demo")                              # grab, compose, SCREENS, MPV
scroll = load_script("scroll-plate")                              # camera_offsets (flow mode)

FPS = 23.976
FRAMES = 69                                                       # 2.9 s: the test scenes' length, checked uncut
ROW0, ROWS = 270, 540                                             # middle rows of the 1080p frame: a 32:9 strip
PIC0, PIC_W, SIDE_W = 480, 960, 480                               # centre 16:9 = picture, 480 px true side each way
BAND = 240                                                        # picture columns next to a seam the planner reads
DIFF, TAU, GAIN_MIN = 16, 0.10, 0.02                              # structure-path thresholds (research report 261013)
VERSIONS = ["truth", "frozen", "warp-1hz", "warp-3hz", "warp-6hz", "planner"]
TRIALS = {"frozen": 6, "warp-1hz": 8, "warp-3hz": 8, "warp-6hz": 8, "planner": 10, "sham": 4}
# X1b (--same-resampling): every version against truth-pipe (the truth through the same world resampling), and "pipe"
# = the raw truth against truth-pipe: is the resampling alone visible? (X1 scored 40/40 with no such control.)
VERSIONS_B = ["truth", "truth-pipe", "frozen", "warp-1hz", "warp-3hz", "warp-6hz", "planner"]
TRIALS_B = {"frozen": 4, "warp-1hz": 6, "warp-3hz": 8, "warp-6hz": 8, "planner": 10, "pipe": 4, "sham": 4}
MOMENTS = {1314.3963: "fight", 1226.0583: "reaching", 366.9083: "pan", 1292.7913: "edge-move"}   # jjk test scenes
NAMES = {**MOMENTS, 1019.56: "slow-side", 369.828: "pan-patches"}
SIDES = ("left", "right")
# X1c (--x1c): planner-v2 and near-every against truth-pipe, every version through one projective world
VERSIONS_C = ["truth", "truth-pipe", "frozen", "planner-v2", "near-every"]
TRIALS_C = {"planner-v2": 18, "near-every": 18, "frozen": 4, "pipe": 4, "sham": 4}   # 18: 3 per moment over X1c's 6
# X1c's moments: X1's four, plus two of the 24 measured where the planner carries the sides and still errs (shot 419: a
# slow move whose left side the band never explains, far error 12.6; shot 133: a 137 px/s pan with patches)
MOMENTS_C = "1314.3963,1226.0583,366.9083,1292.7913,1019.56,369.828"
HALF = SIDE_W // 2
REGIONS = {"left-outer": slice(0, HALF), "left-inner": slice(HALF, SIDE_W),
           "right-inner": slice(PIC0 + PIC_W, PIC0 + PIC_W + HALF),
           "right-outer": slice(PIC0 + PIC_W + HALF, PIC0 + PIC_W + SIDE_W)}
SIDE_REGIONS = {"left": ("left-inner", "left-outer"), "right": ("right-inner", "right-outer")}   # inner first
PIC_SLICE = slice(PIC0, PIC0 + PIC_W)


def moment_name(at):
    """The test scene's name when `at` is one of MOMENTS (within 0.05 s), else t<seconds>."""
    near = [name for t, name in NAMES.items() if abs(t - at) < 0.05]
    return near[0] if near else f"t{at:g}"


def side_cols(name):
    return slice(0, SIDE_W) if name == "left" else slice(PIC0 + PIC_W, PIC0 + PIC_W + SIDE_W)


def luma(img):
    return cv2.cvtColor(np.ascontiguousarray(img), cv2.COLOR_RGB2GRAY).astype(np.float32)


def shift(img, d, interp=cv2.INTER_LINEAR, border=cv2.BORDER_REPLICATE):
    m = np.float32([[1, 0, d[0]], [0, 1, d[1]]])
    return cv2.warpAffine(img, m, (img.shape[1], img.shape[0]), flags=interp, borderMode=border)


def unexplained(prev_pic, cur_pic, d):
    """Per side: (U, gain). U = share of the band's pixels (picture columns next to that seam) changing by more than
    DIFF under the best of hold, camera slide by d, slide + brightness gain; pixels entering the picture from outside
    are not counted. gain = the slide hypothesis' brightness factor when it is the best one, else 1."""
    p0, p1 = luma(prev_pic), luma(cur_pic)
    slid = shift(p0, d)
    valid = shift(np.ones_like(p0), d, cv2.INTER_NEAREST, cv2.BORDER_CONSTANT) > 0.5
    out = []
    for name in SIDES:
        cols = slice(0, BAND) if name == "left" else slice(PIC_W - BAND, PIC_W)
        v = valid[:, cols]
        a, b, s = p1[:, cols][v], p0[:, cols][v], slid[:, cols][v]
        if a.size == 0:
            out.append((1.0, 1.0))
            continue
        g = float(a.mean() / max(1.0, s.mean()))
        cands = [(float((np.abs(a - b) > DIFF).mean()), 1.0), (float((np.abs(a - s) > DIFF).mean()), 1.0),
                 (float((np.abs(a - g * s) > DIFF).mean()), g)]
        u, gain = min(cands, key=lambda c: c[0])
        out.append((u, gain if abs(gain - 1.0) > GAIN_MIN else 1.0))
    return out


def carried(strips, offs, refresh, gains, through_world=False):
    """Sides carried by the camera between refreshes. A world canvas in frame-0 coordinates holds the last known
    content: the refreshed sides and every real picture, so pixels leaving the picture become the side's (trails).
    Content the camera brings in from beyond everything known (the outer edge) is taken from the truth as it enters:
    a perfect painter ahead of the camera (the plate's job in the product), so only the timing of updates is tested.
    Each frame samples the world once at its camera offset (no blur build-up).
    through_world (X1b): every frame's sides are read back from the world after that frame's updates went in, so
    refreshed and carried frames get the same resampling (in X1 a refresh showed raw truth and carried frames a
    resampled copy: the sides' sharpness pulsed at the refresh rate); brightness steps scale the sides at display time
    instead of being written back (no blur build-up at a gain step)."""
    if through_world:
        return _carried_through_world(strips, offs, refresh, gains)
    pad = int(math.ceil(float(np.abs(offs).max()))) + 4
    world = cv2.copyMakeBorder(strips[0].astype(np.float32), pad, pad, pad, pad, cv2.BORDER_REPLICATE)
    known = np.zeros(world.shape[:2], np.float32)
    known[pad:pad + strips[0].shape[0], pad:pad + strips[0].shape[1]] = 1.0
    h, w = world.shape[:2]
    rows, width = strips[0].shape[:2]

    def put(img, o, mask):
        # nearest-neighbour mask: a 1-px strip at a fractional offset is still stored (a linear mask never reaches
        # 1.0 there, the strip stayed unknown and came back as stripes); replicate, not black, at the image border
        m = np.float32([[1, 0, pad - o[0]], [0, 1, pad - o[1]]])
        keep = cv2.warpAffine(mask.astype(np.uint8), m, (w, h), flags=cv2.INTER_NEAREST,
                              borderMode=cv2.BORDER_CONSTANT) > 0
        world[keep] = cv2.warpAffine(img, m, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)[keep]
        known[keep] = 1.0

    def get(arr, o, interp):
        m = np.float32([[1, 0, o[0] - pad], [0, 1, o[1] - pad]])
        return cv2.warpAffine(arr, m, (width, rows), flags=interp, borderMode=cv2.BORDER_REPLICATE)

    def cols_mask(cols):
        mask = np.zeros((rows, width), bool)
        mask[:, cols] = True
        return mask

    pic = slice(PIC0, PIC0 + PIC_W)
    out = []
    for t, s in enumerate(strips):
        sf = s.astype(np.float32)
        if t == 0:
            canvas = sf.copy()
        else:
            canvas = get(world, offs[t], cv2.INTER_LINEAR)
            fresh = get(known, offs[t], cv2.INTER_NEAREST) < 0.5      # never-seen content the camera brought in
            fresh = cv2.dilate(fresh.astype(np.uint8), np.ones((1, 3), np.uint8)) > 0   # no 1-px gaps between strips
            fresh[:, pic] = False
            if fresh.any():
                canvas[fresh] = sf[fresh]
                put(sf, offs[t], fresh)
        canvas[:, pic] = sf[:, pic]
        put(canvas, offs[t], cols_mask(pic))                      # the real picture always becomes known world
        for i, name in enumerate(SIDES):
            cols = side_cols(name)
            if t > 0 and refresh[t][i]:
                canvas[:, cols] = sf[:, cols]
            elif t > 0 and gains[t][i] != 1.0:
                canvas[:, cols] *= gains[t][i]
            else:
                continue
            put(canvas, offs[t], cols_mask(cols))                 # refreshed or re-lit sides become the new world
        out.append(np.clip(canvas, 0, 255).round().astype(np.uint8))
    return out


def _carried_through_world(strips, offs, refresh, gains):
    """carried(through_world=True): every update (the picture's trails, content brought in at the outer edge, a side's
    refresh) is written into the world first, then the frame's sides are read back from it, refreshed or not."""
    pad = int(math.ceil(float(np.abs(offs).max()))) + 4
    world = cv2.copyMakeBorder(strips[0].astype(np.float32), pad, pad, pad, pad, cv2.BORDER_REPLICATE)
    known = np.zeros(world.shape[:2], np.float32)
    known[pad:pad + strips[0].shape[0], pad:pad + strips[0].shape[1]] = 1.0
    h, w = world.shape[:2]
    rows, width = strips[0].shape[:2]

    def put(img, o, mask):
        m = np.float32([[1, 0, pad - o[0]], [0, 1, pad - o[1]]])
        keep = cv2.warpAffine(mask.astype(np.uint8), m, (w, h), flags=cv2.INTER_NEAREST,
                              borderMode=cv2.BORDER_CONSTANT) > 0
        world[keep] = cv2.warpAffine(img, m, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)[keep]
        known[keep] = 1.0

    def get(arr, o, interp):
        m = np.float32([[1, 0, o[0] - pad], [0, 1, o[1] - pad]])
        return cv2.warpAffine(arr, m, (width, rows), flags=interp, borderMode=cv2.BORDER_REPLICATE)

    def cols_mask(cols):
        mask = np.zeros((rows, width), bool)
        mask[:, cols] = True
        return mask

    pic = slice(PIC0, PIC0 + PIC_W)
    cum = {name: 1.0 for name in SIDES}                           # brightness since the side's last refresh
    out = []
    for t, s in enumerate(strips):
        sf = s.astype(np.float32)
        if t > 0:
            fresh = get(known, offs[t], cv2.INTER_NEAREST) < 0.5
            fresh = cv2.dilate(fresh.astype(np.uint8), np.ones((1, 3), np.uint8)) > 0
            fresh[:, pic] = False
            if fresh.any():
                put(sf, offs[t], fresh)
        put(sf, offs[t], cols_mask(pic))
        for i, name in enumerate(SIDES):
            if t > 0 and refresh[t][i]:
                put(sf, offs[t], cols_mask(side_cols(name)))
                cum[name] = 1.0
            elif t > 0 and gains[t][i] != 1.0:
                cum[name] *= gains[t][i]
        canvas = get(world, offs[t], cv2.INTER_LINEAR)
        canvas[:, pic] = sf[:, pic]                               # the picture itself is never resampled
        for name in SIDES:
            if cum[name] != 1.0:
                canvas[:, side_cols(name)] *= cum[name]
        out.append(np.clip(canvas, 0, 255).round().astype(np.uint8))
    return out


def versions(strips, same_resampling=False):
    """{version: strips}, plus facts: camera travel, flow agreement, planner refreshes per second per side.
    same_resampling (X1b): every carried version through the world each frame, plus truth-pipe (the truth refreshed
    every frame through the same world: the reference the others are compared with)."""
    pics = [s[:, PIC0:PIC0 + PIC_W] for s in strips]
    offs, agree = scroll.camera_offsets(pics, 0)
    n = len(strips)
    steps = [np.zeros(2)] + [offs[t] - offs[t - 1] for t in range(1, n)]
    plan = [[(0.0, 1.0), (0.0, 1.0)]] + [unexplained(pics[t - 1], pics[t], steps[t]) for t in range(1, n)]
    out = {"truth": [s.copy() for s in strips]}
    if same_resampling:
        out["truth-pipe"] = carried(strips, offs, [(True, True)] * n, [(1.0, 1.0)] * n, through_world=True)
    frozen = []
    for s in strips:
        f = s.copy()
        for name in SIDES:
            f[:, side_cols(name)] = strips[0][:, side_cols(name)]
        frozen.append(f)
    out["frozen"] = frozen
    ones = [(1.0, 1.0)] * n
    for hz in (1, 3, 6):
        period = max(1, int(round(FPS / hz)))
        refresh = [(t % period == 0, t % period == 0) for t in range(n)]
        out[f"warp-{hz}hz"] = carried(strips, offs, refresh, ones, through_world=same_resampling)
    refresh = [tuple(u > TAU for u, _ in p) for p in plan]
    gains = [tuple(g for _, g in p) for p in plan]
    out["planner"] = carried(strips, offs, refresh, gains, through_world=same_resampling)
    facts = {"travel_px": [round(float(x), 1) for x in offs.max(0) - offs.min(0)],
             "flow_agreement_median": round(float(np.median(agree[1:])), 3),
             "planner_refresh_per_s": [round(sum(r[i] for r in refresh[1:]) / (n - 1) * FPS, 2) for i in (0, 1)],
             "planner_gain_steps": [sum(g[i] != 1.0 for g in gains[1:]) for i in (0, 1)]}
    return out, facts


def metrics(frames, truth):
    """Error of the sides vs the truth (near = inner half next to the seam, far = outer half), mean change of the sides
    per frame (and the truth's), mean change of the picture per frame: the motion numbers."""
    def sides(f):
        return np.hstack([f[:, side_cols("left")], f[:, side_cols("right")]]).astype(np.int16)

    near = np.hstack([np.arange(SIDE_W // 2, SIDE_W), np.arange(SIDE_W, SIDE_W + SIDE_W // 2)])
    far = np.hstack([np.arange(0, SIDE_W // 2), np.arange(SIDE_W + SIDE_W // 2, 2 * SIDE_W)])
    err = [np.abs(sides(f) - sides(g)) for f, g in zip(frames, truth)]
    change = [np.abs(sides(frames[t]) - sides(frames[t - 1])).mean() for t in range(1, len(frames))]
    tchange = [np.abs(sides(truth[t]) - sides(truth[t - 1])).mean() for t in range(1, len(truth))]
    pic = [np.abs(truth[t][:, PIC0:PIC0 + PIC_W].astype(np.int16) - truth[t - 1][:, PIC0:PIC0 + PIC_W]).mean()
           for t in range(1, len(truth))]
    return {"err_near": round(float(np.mean([e[:, near].mean() for e in err])), 2),
            "err_far": round(float(np.mean([e[:, far].mean() for e in err])), 2),
            "side_change": round(float(np.mean(change)), 2), "truth_side_change": round(float(np.mean(tchange)), 2),
            "picture_change": round(float(np.mean(pic)), 2)}


# ---- X1c: the cadence question ---------------------------------------------------------------------------------------

def projective_path(strips, cadence=None):
    """The camera model's path on the picture (W[t]: strip px of frame t -> frame 0's strip px), each frame's new-drawing
    flag, the step planner's steps on the picture's bands, and the camera models used."""
    tracker = cm.CameraTracker((PIC0, 0, PIC0 + PIC_W, ROWS))
    psize = (sp.PLAN_W, int(round(ROWS * sp.PLAN_W / PIC_W)))
    planner = sp.StepPlanner(*psize, SIDES, cadence or sp.Cadence())
    W, new, steps = [], [], []
    for s in strips:
        g = cv2.cvtColor(np.ascontiguousarray(s[:, PIC_SLICE]), cv2.COLOR_RGB2GRAY)
        Wt = tracker.add(g)
        W.append(Wt)
        new.append(tracker.new_drawing)
        steps.append(planner.add(cv2.resize(g, psize, interpolation=cv2.INTER_AREA), tracker.grid_map(Wt, sp.PLAN_W),
                                 tracker.new_drawing))
    return W, new, steps, tracker.path.model


def translation_path(strips):
    """X1's camera: scroll-plate's flow mode, chained (a translation per frame), as 3x3 maps."""
    offs, _ = scroll.camera_offsets([s[:, PIC_SLICE] for s in strips], 0)
    return [np.float64([[1, 0, -o[0]], [0, 1, -o[1]], [0, 0, 1]]) for o in offs]


def versions_c(strips, cadence=None):
    """({version: strips}, facts, the projective path) of X1c: refreshes per second per side, the planner's states, the
    camera models used."""
    n = len(strips)
    W, new, steps, models = projective_path(strips, cadence)
    flagged = [{s: st[s].state in (sp.STATES["patch"], sp.STATES["gen"]) for s in SIDES} for st in steps]
    none = [{} for _ in range(n)]
    out = {"truth": [s.copy() for s in strips],
           "truth-pipe": structure.carry_strips(strips, W, [{r: True for r in REGIONS}] * n, none, PIC_SLICE, REGIONS)}
    frozen = []
    for s in strips:
        f = s.copy()
        for name in SIDES:
            f[:, side_cols(name)] = strips[0][:, side_cols(name)]
        frozen.append(f)
    out["frozen"] = frozen
    for version in ("planner-v2", "near-every"):
        refresh, gains = [], []
        for t in range(n):
            r, g = {}, {}
            for side, (inner, outer) in SIDE_REGIONS.items():
                r[inner] = flagged[t][side] or (version == "near-every" and new[t])
                r[outer] = flagged[t][side]
                gd = steps[t][side].gain                          # the band's brightness since the side's last refresh
                g[outer] = [(REGIONS[outer], gd)]
                g[inner] = [] if version == "near-every" else [(REGIONS[inner], gd)]
            refresh.append(r)
            gains.append(g)
        out[version] = structure.carry_strips(strips, W, refresh, gains, PIC_SLICE, REGIONS)
    names = {v: k for k, v in sp.STATES.items()}
    facts = {"refresh_per_s": {"planner-v2": [round(sum(f[s] for f in flagged[1:]) / (n - 1) * FPS, 2) for s in SIDES],
                               "near-every inner": round(sum(new[1:]) / (n - 1) * FPS, 2)},
             "states": {s: dict(collections.Counter(names[st[s].state] for st in steps[1:])) for s in SIDES},
             "camera_models": dict(collections.Counter(models[1:]))}
    return out, facts, W


def camera_check(strips, W_proj=None):
    """The camera alone: the frame-0 sides carried with no refresh (what comes in at the outer edge from the truth), by
    X1's translation and by the projective camera model; error against the truth over the clip and its last third."""
    n = len(strips)
    none = [{} for _ in range(n)]
    res = {}
    for name, W in (("translation", translation_path(strips)),
                    ("projective", W_proj if W_proj is not None else projective_path(strips)[0])):
        frames = structure.carry_strips(strips, W, none, none, PIC_SLICE, {s: side_cols(s) for s in SIDES})
        m, late = metrics_c(frames, strips), metrics(frames[2 * n // 3:], strips[2 * n // 3:])
        res[name] = {"err_near": m["err_near"], "err_far": m["err_far"], "late_near": late["err_near"],
                     "late_far": late["err_far"], "r_motion": m["r_motion"], "boil": m["boil"],
                     "boil_codes": m["boil_codes"]}                                          # T3 alone (no refresh)
    return res


BOIL_FLOOR = 0.25   # codes: the truth's change on held steps is floored here, so a still scene's ratio stays finite


def metrics_c(frames, truth):
    """metrics() plus the motion correlation (the sides' change per frame against the picture's, Pearson; kongming's
    bar r >= 0.7, the truth's own r alongside) and boil: the sides' mean change on steps where the picture holds, over
    the truth's (the phase's bar: <= 1.5), and the same as a difference in codes (boil_codes)."""
    m = metrics(frames, truth)

    def sides(f):
        return np.hstack([f[:, side_cols("left")], f[:, side_cols("right")]]).astype(np.int16)

    sc = np.array([np.abs(sides(frames[t]) - sides(frames[t - 1])).mean() for t in range(1, len(frames))])
    tc = np.array([np.abs(sides(truth[t]) - sides(truth[t - 1])).mean() for t in range(1, len(truth))])
    pc = np.array([np.abs(truth[t][:, PIC_SLICE].astype(np.int16) - truth[t - 1][:, PIC_SLICE]).mean()
                   for t in range(1, len(truth))])

    def r(a, b):
        return round(float(np.corrcoef(a, b)[0, 1]), 3) if a.std() > 1e-6 and b.std() > 1e-6 else 0.0

    held = pc < 0.5
    m["r_motion"], m["r_motion_truth"] = r(sc, pc), r(tc, pc)
    s_held, t_held = (float(sc[held].mean()), float(tc[held].mean())) if held.any() else (0.0, 0.0)
    m["boil"] = round(s_held / max(t_held, BOIL_FLOOR), 2) if held.any() else 1.0
    m["boil_codes"] = round(s_held - t_held, 2)
    m["held_steps"] = int(held.sum())                             # boil is 1.0 by definition when this is 0
    return m


def to_wide(strip):
    return cv2.resize(strip, (2 * strip.shape[1], 2 * strip.shape[0]), interpolation=cv2.INTER_LANCZOS4)


def write_span_clip(wides, path, log):
    """Wide 3840x1080 frames composed for the two screens ('fill' layout), streamed into ffmpeg (no 1.5 GB lists)."""
    screens = demo.SCREENS
    desk_w, desk_h = screens[1]["x"] + screens[1]["w"], max(s["h"] for s in screens)
    ffmpeg = os.environ.get("PANORAMA_FFMPEG") or shutil.which("ffmpeg")
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s",
           f"{desk_w}x{desk_h}", "-r", f"{FPS:.5f}", "-i", "-", "-c:v", "libx264", "-preset", "fast", "-crf", "15",
           "-pix_fmt", "yuv420p", path]
    with open(log, "ab") as err:
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=err)
        for f in wides:
            canvas = np.zeros((desk_h, desk_w, 3), np.uint8)
            for half, sc in ((f[:, :1920], screens[0]), (f[:, 1920:], screens[1])):
                canvas[:sc["h"], sc["x"]:sc["x"] + sc["w"]] = demo.compose(half, sc, "fill", 0)
            p.stdin.write(canvas.tobytes())
        p.stdin.close()
        if p.wait() != 0:
            raise RuntimeError(f"ffmpeg failed writing {path} (see {log})")


def card(lines, big=False):
    """A black 3840x1080 card with the text centred on each screen's half."""
    img = np.zeros((1080, 3840, 3), np.uint8)
    scale, thick = (9.0, 18) if big else (1.6, 3)
    for x0 in (0, 1920):
        y = 540 - (len(lines) - 1) * (60 if not big else 0)
        for text in lines:
            (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, thick)
            cv2.putText(img, text, (x0 + (1920 - tw) // 2, y + th // 2), cv2.FONT_HERSHEY_SIMPLEX, scale,
                        (235, 235, 235), thick, cv2.LINE_AA)
            y += 120
    return img


def write_card_png(img, path):
    screens = demo.SCREENS
    desk_w, desk_h = screens[1]["x"] + screens[1]["w"], max(s["h"] for s in screens)
    canvas = np.zeros((desk_h, desk_w, 3), np.uint8)
    for half, sc in ((img[:, :1920], screens[0]), (img[:, 1920:], screens[1])):
        canvas[:sc["h"], sc["x"]:sc["x"] + sc["w"]] = demo.compose(half, sc, "fill", 0)
    cv2.imwrite(path, cv2.cvtColor(canvas, cv2.COLOR_RGB2BGR))


def sheet(vers, path, names=VERSIONS):
    """Rows = versions, columns = frames 0, 24, 48, 71 (wide strips, small): the look across the clip, not one frame."""
    cols = [0, len(vers["truth"]) // 3, 2 * len(vers["truth"]) // 3, len(vers["truth"]) - 1]
    rows = []
    for v in names:
        cells = [cv2.resize(vers[v][t], (720, 202), interpolation=cv2.INTER_AREA) for t in cols]
        row = np.hstack(cells)
        cv2.putText(row, v, (8, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 0), 4, cv2.LINE_AA)
        cv2.putText(row, v, (8, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 0), 2, cv2.LINE_AA)
        rows.append(row)
    cv2.imwrite(path, cv2.cvtColor(np.vstack(rows), cv2.COLOR_RGB2BGR))


def build(a):
    stamp = time.strftime("%Y%m%d-%H%M%S")
    x1c = a.x1c
    odir = os.path.join(a.out_dir, f"{stamp}-true-sides{'-c' if x1c else '-b' if a.same_resampling else ''}")
    for sub in ("clips", "prompts", "sheets", "free-look"):
        os.makedirs(os.path.join(odir, sub), exist_ok=True)
    log = os.path.join(odir, "ffmpeg.log")
    rng = random.Random(a.seed)
    moments = [float(x) for x in a.at.split(",")]
    versions_ = VERSIONS_C if x1c else VERSIONS_B if a.same_resampling else VERSIONS
    trials_ = TRIALS_C if x1c else TRIALS_B if a.same_resampling else TRIALS
    ids, report = {}, {}
    for at in moments:
        name = moment_name(at)
        t0 = time.perf_counter()
        frames = demo.grab(a.film, at, FRAMES)
        strips = [f[ROW0:ROW0 + ROWS] for f in frames]
        cut = demo.find_cut(frames, 1, len(frames))
        g = [cv2.resize(luma(f), (480, 270), interpolation=cv2.INTER_AREA) for f in frames]
        biggest = max(float(np.abs(g[t] - g[t - 1]).mean()) for t in range(1, len(g)))
        if x1c:
            vers, facts, W = versions_c(strips)
            facts["camera_check"] = camera_check(strips, W)
        else:
            vers, facts = versions(strips, a.same_resampling)
        facts["biggest_step_luma"] = round(biggest, 1)
        facts["biggest_step_at"] = cut
        measure = metrics_c if x1c else metrics
        report[name] = {"start_s": at, "facts": facts, "versions": {v: measure(vers[v], vers["truth"]) for v in versions_}}
        sheet(vers, os.path.join(odir, "sheets", f"{name}.png"), versions_)
        fl = os.path.join(odir, "free-look", name)
        os.makedirs(fl, exist_ok=True)
        for v in versions_:
            cid = "".join(rng.choice("0123456789abcdef") for _ in range(8))
            ids[(name, v)] = cid
            path = os.path.join(odir, "clips", f"{cid}.mp4")
            write_span_clip((to_wide(s) for s in vers[v]), path, log)
            try:
                os.link(path, os.path.join(fl, f"{v}.mp4"))           # same file under its real name, no extra disk
            except OSError:
                shutil.copy2(path, os.path.join(fl, f"{v}.mp4"))
        print(f"{name}: {time.perf_counter() - t0:.0f} s, facts {facts}", flush=True)
    names = [moment_name(at) for at in moments]
    if not a.no_gate:                                             # no test reaches the user unchecked (2026-10-14):
        gate = load_script("side-gate")                           # the gate runs before any launcher exists
        gate.x1c(argparse.Namespace(test_dir=odir, moments=None, versions=None,
                                    ref="truth-pipe" if (a.same_resampling or x1c) else "truth",
                                    out=os.path.join(odir, "gate"), bars=gate.sg.BARS_PATH, generated=False,
                                    no_colour=False, no_pictures=False))
    # blind forced choice: truth against one version per trial, moments rotated, trials shuffled. A trial whose two
    # clips are the same file is not a trial (X1c's edge-move: the planner flagged every frame) and is left out; then
    # each level puts the reference first in exactly half of the trials it kept (the user leans to the second clip).
    sums = {key: hs_md5(os.path.join(odir, "clips", f"{cid}.mp4")) for key, cid in ids.items()}
    trials, dropped = [], collections.Counter()
    for level, count in trials_.items():
        ref = "truth-pipe" if (a.same_resampling or x1c) and level != "pipe" else "truth"
        other = ref if level == "sham" else ("truth-pipe" if level == "pipe" else level)
        kept = []
        for k in range(count):
            m = names[k % len(names)]
            if level != "sham" and sums[(m, ref)] == sums[(m, other)]:
                dropped[(m, level)] += 1
                continue
            kept.append(m)
        if not kept:
            print(f"level {level}: every trial left out (its clips are the reference's own files)", flush=True)
        firsts = [k < len(kept) // 2 for k in range(len(kept))]
        if len(kept) % 2:
            firsts[-1] = rng.random() < 0.5
        rng.shuffle(firsts)
        for m, first in zip(kept, firsts):
            pair = [ref, other] if first else [other, ref]
            trials.append({"moment": m, "level": level, "pair": pair, "odd": None if level == "sham" else (2 if first else 1)})
    for (m, level), c in dropped.items():
        print(f"left out {c} {level} trial(s) on {m}: its clip is the same file as the reference's", flush=True)
    rng.shuffle(trials)
    for t in (1, 2):
        lbl = card([str(t)], big=True)
        write_span_clip([lbl] * 12, os.path.join(odir, f"label-{t}.mp4"), log)
    write_card_png(card(["Watch the middle picture as you normally would.", "Each trial: 1, then 2.",
                         "Then: which had the odd sides?"]), os.path.join(odir, "prompts", "start.png"))
    lines = ["#EXTM3U", "prompts/start.png"]
    for k, tr in enumerate(trials, 1):
        tr["trial"] = k
        write_card_png(card([f"Trial {k} of {len(trials)}", "Which had the odd sides?  1 or 2"]),
                       os.path.join(odir, "prompts", f"trial-{k:02d}.png"))
        lines += ["label-1.mp4", f"clips/{ids[(tr['moment'], tr['pair'][0])]}.mp4",
                  "label-2.mp4", f"clips/{ids[(tr['moment'], tr['pair'][1])]}.mp4", f"prompts/trial-{k:02d}.png"]
    write_card_png(card(["End of the test", "Answers saved in answers-<time>.txt"]), os.path.join(odir, "prompts", "end.png"))
    lines.append("prompts/end.png")
    with open(os.path.join(odir, "trials.m3u"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    args = "--fs --fs-screen=all --video-unscaled=yes --hwdec=no --osc=no --really-quiet"
    with open(os.path.join(odir, "play-trials.bat"), "w", encoding="utf-8", newline="") as bat:
        bat.write('@echo off\r\nrem Blind test: per trial "1", a clip, "2", a clip, then the question: press 1 or 2. Space pauses, q quits.\r\n'
                  f'start "" "{demo.MPV}" {args} --image-display-duration=4 --script="{KEYS_LUA}" '
                  '--playlist="%~dp0trials.m3u"\r\n')
    for name in names:
        items = " ".join(f'"%~dp0free-look\\{name}\\{v}.mp4"' for v in versions_)
        with open(os.path.join(odir, f"play-free-look-{name}.bat"), "w", encoding="utf-8", newline="") as bat:
            bat.write(f'@echo off\r\nrem After the test: {name}, all versions by name. > and < switch, q quits.\r\n'
                      f'start "" "{demo.MPV}" {args} --loop-file=inf --loop-playlist=inf '
                      f'--osd-playing-msg="${{filename}}" --osd-duration=3000 {items}\r\n')
    hs.save_json({"trials": trials, "clip_ids": {f"{m}/{v}": c for (m, v), c in ids.items()}},
                 os.path.join(odir, "key.json"))
    hs.save_json({"thresholds": {"DIFF": DIFF, "TAU": TAU, "GAIN_MIN": GAIN_MIN, "BAND": BAND}, "moments": report},
                 os.path.join(odir, "metrics.json"))
    print(f"done: {odir} ({len(trials)} trials)", flush=True)


def hs_md5(path):
    import hashlib
    h = hashlib.md5()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


KEYS_LUA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trial-answer-keys.lua")


def score(test_dir, answers):
    """Hits per level against key.json; one-sided binomial p (chance 0.5) and a plain verdict."""
    key = json.load(open(os.path.join(test_dir, "key.json"), encoding="utf-8"))["trials"]
    ans = [int(x) for x in answers.replace(" ", "").split(",") if x]
    if len(ans) != len(key):
        raise SystemExit(f"{len(ans)} answers for {len(key)} trials")
    levels = {}
    for tr, a in zip(sorted(key, key=lambda t: t["trial"]), ans):
        lv = levels.setdefault(tr["level"], {"n": 0, "hits": 0, "picked_1": 0})
        lv["n"] += 1
        lv["picked_1"] += a == 1
        lv["hits"] += tr["odd"] is not None and a == tr["odd"]
    for name, lv in levels.items():
        if name == "sham":
            lv["verdict"] = f"bias check: picked 1 in {lv['picked_1']} of {lv['n']}"
            continue
        n, k = lv["n"], lv["hits"]
        p = sum(math.comb(n, i) for i in range(k, n + 1)) / 2 ** n
        lv["p_chance"] = round(p, 3)
        lv["verdict"] = "visible" if p <= 0.06 else ("not reliably visible" if k <= 0.6 * n else "unclear: more trials")
    hs.save_json(levels, os.path.join(test_dir, "result.json"))
    for name, lv in levels.items():
        print(f"{name:10s} {lv['hits'] if name != 'sham' else '-'}/{lv['n']}  {lv['verdict']}")


def x1c_metrics(a):
    """X1c's versions and the camera check on N more moments: story shots long enough for a clip, the picture full width
    over the strip's rows, a third each slow / medium / fast by the content model's median camera speed. No clips."""
    shots = json.load(open(os.path.join(a.content, "shots.json"), encoding="utf-8"))["shots"]
    ok = [s for s in shots if s["frames"] >= FRAMES + 6 and 100 <= s["start_s"] and s["end_s"] <= 1600
          and s["crop"]["rect"][0] <= 0 and s["crop"]["rect"][2] >= 1920
          and s["crop"]["rect"][1] <= ROW0 and s["crop"]["rect"][3] >= ROW0 + ROWS]
    def speed(s):
        return float(s["motion"]["speed_px_s_p50"])

    rng = random.Random(a.seed)
    picks = []
    for lo, hi in ((0, 20), (20, 150), (150, 1e9)):
        stratum = [s for s in ok if lo <= speed(s) < hi]
        rng.shuffle(stratum)
        picks += stratum[:max(1, a.x1c_metrics // 3)]
    rows = {}
    for s in sorted(picks, key=lambda s: s["start_s"]):
        t0 = time.perf_counter()
        at = (s["start_s"] + s["end_s"]) / 2 - FRAMES / FPS / 2
        strips = [f[ROW0:ROW0 + ROWS] for f in demo.grab(a.film, at, FRAMES)]
        vers, facts, W = versions_c(strips)
        facts["camera_check"] = camera_check(strips, W)
        rows[f"shot{s['id']}"] = {"start_s": round(at, 3), "speed_px_s_p50": speed(s), "facts": facts,
                                  "versions": {v: metrics_c(vers[v], vers["truth"]) for v in VERSIONS_C}}
        v = rows[f"shot{s['id']}"]["versions"]
        print(f"shot{s['id']:4d} {speed(s):7.1f} px/s  refresh/s {facts['refresh_per_s']}  "
              f"planner-v2 far {v['planner-v2']['err_far']:5.2f}  near-every far {v['near-every']['err_far']:5.2f}  "
              f"camera far T {facts['camera_check']['translation']['err_far']:5.2f} "
              f"P {facts['camera_check']['projective']['err_far']:5.2f}  {time.perf_counter() - t0:.0f} s", flush=True)
        del vers, strips
    if not rows:
        print("no shot qualifies for the metrics (story shots long enough, picture full width over the strip)")
        return
    summary = {}
    for ver in VERSIONS_C:
        ms = [r["versions"][ver] for r in rows.values()]
        summary[ver] = {k: round(float(np.mean([m[k] for m in ms])), 3)
                        for k in ("err_near", "err_far", "side_change", "r_motion", "r_motion_truth", "boil",
                                  "boil_codes")}
    for name in ("translation", "projective"):
        cc = [r["facts"]["camera_check"][name] for r in rows.values()]
        summary[f"camera {name}"] = {k: round(float(np.mean([c[k] for c in cc])), 3) for k in cc[0]}
    summary["planner-v2 refresh/s"] = round(float(np.mean([np.mean(r["facts"]["refresh_per_s"]["planner-v2"])
                                                           for r in rows.values()])), 2)
    summary["near-every inner refresh/s"] = round(float(np.mean([r["facts"]["refresh_per_s"]["near-every inner"]
                                                                 for r in rows.values()])), 2)
    os.makedirs(a.out_dir, exist_ok=True)
    path = os.path.join(a.out_dir, time.strftime("%Y%m%d-%H%M%S") + "-x1c-metrics.json")
    hs.save_json({"cadence": sp.Cadence().key(), "camera": cm.VERSION, "summary": summary, "moments": rows}, path)
    print(json.dumps(summary, indent=1))
    print(f"wrote {path}")


def below_normal_priority():
    """CPU work at below-normal priority (Windows), so it never competes with a player or a GPU job's feeding thread."""
    try:
        import ctypes
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)
    except (AttributeError, OSError):
        pass


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("film", nargs="?")
    ap.add_argument("out_dir", nargs="?")
    ap.add_argument("--at", default=None, help="film times of the moments (default: X1's four; X1c's six with --x1c)")
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--score", nargs=2, metavar=("TEST_DIR", "ANSWERS"))
    ap.add_argument("--same-resampling", action="store_true",
                    help="X1b: every version's sides through the same world resampling, compared with truth-pipe")
    ap.add_argument("--x1c", action="store_true", help="X1c: planner-v2 and near-every against truth-pipe (phase 4)")
    ap.add_argument("--x1c-metrics", type=int, metavar="N", help="X1c's versions measured on N more moments, no clips")
    ap.add_argument("--content", default=os.path.join(HERE, "out", "jjk", "content"), help="content model (--x1c-metrics)")
    ap.add_argument("--threads", type=int, default=4, help="OpenCV threads")
    ap.add_argument("--no-gate", action="store_true", help="skip the per-frame gate after building (side-gate.py)")
    a = ap.parse_args(argv)
    if a.at is None:
        a.at = MOMENTS_C if a.x1c else ",".join(repr(t) for t in MOMENTS)
    cv2.setNumThreads(a.threads)
    if a.score:
        score(*a.score)
    else:
        if not (a.film and a.out_dir):
            ap.error("FILM and OUT_DIR are needed to build the test")
        below_normal_priority()
        if a.x1c_metrics:
            x1c_metrics(a)
        else:
            build(a)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
