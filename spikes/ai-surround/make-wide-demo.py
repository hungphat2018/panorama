"""A real-use preview: a random 3 s moment of the film, uncut, extended to a 32:9 canvas for two screens side by side.

Unlike the hidden-sides test (which cuts real frames so a truth exists), this extends the whole real frame beyond its
true edges, as the product will. Route (plan "routing by motion"): a story shot whose outer bands stay still gets one
Qwen-Image-2.1 + outpaint-LoRA-v2 plate per side (the baseline's still-shot recipe), held for the shot while the real
picture moves in the middle. Each side is drawn from the adjacent half of the picture on a 1920x1080 canvas (half new,
half real), the same geometry the hidden-sides test measured, and its caption describes only that visible half, as in
the test: a whole-frame caption (first demo, 2026-10-12 21:36) made each side draw the characters it named but could not
see, so the main characters appeared two and three times. The real picture is pasted back untouched in every frame.

Display: each screen shows one 16:9 half of the 32:9 canvas, so the picture's middle sits on the bezel. Two layouts for
screens of different size (physical sizes from the monitors' EDID):
  fill     each half fills its own screen: both screens covered, the picture's halves differ in size at the bezel
  matched  both halves at the same physical size: continuous across the bezel, black borders on the larger screen
One borderless mpv window spans the desktop and plays a pre-composed video, so both halves stay in sync.

To see what is real: original-16x9.mp4 is the untouched clip, preview-marked.png draws dashed lines where the real
frame ends, and play-compare-<layout>.bat plays "original only" (the real frame with black sides, same layout) and the
extended version as a two-item playlist: > and < switch, q quits.

    python -I make-wide-demo.py FILM SHOTS_JSON OUT_DIR [--at SECONDS] [--seed N] [--port 8189]
    python -I make-wide-demo.py FILM SHOTS_JSON OUT_DIR --compare-only DEMO_DIR    (adds the comparison files, no GPU)

Writes OUT_DIR/<stamp>/{wide-32x9.mp4, original-16x9.mp4, preview.png, preview-marked.png, span-<layout>.mp4,
span-<layout>-original-only.mp4, play-<layout>.bat, play-compare-<layout>.bat, demo.json} for layouts fill and matched.
Needs the GPU (ComfyUI is started here unless a server already answers on --port).
"""
import argparse
import importlib.util
import json
import os
import random
import subprocess
import sys
import time

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)                                          # python -I leaves the script's folder off sys.path
import comfy_client as cc  # noqa: E402
import hidden_sides as hs  # noqa: E402


def load_script(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), os.path.join(HERE, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


plates = load_script("comfy-plates")                              # the painter recipe (qwen_graph), reused unchanged
wraps = load_script("apply-wraps")                                # still_steps: the routing test

FRAMES = 72                                                       # 3 s at 23.976 fps
# Desktop of this machine (physical px, from Windows): laptop 2560x1600 at (0,0), 34 cm wide; external 1920x1080 at
# (2560,0), 48 cm wide. The canvas is composed for that layout.
SCREENS = [{"x": 0, "w": 2560, "h": 1600, "cm": 34.0}, {"x": 2560, "w": 1920, "h": 1080, "cm": 48.0}]


def grab(film, start_s, n):
    """n frames (RGB) from start_s, decoded by ffmpeg at the film's own size."""
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{start_s:.3f}", "-i", film, "-frames:v", str(n),
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    frames = np.frombuffer(raw, np.uint8).reshape(-1, 1080, 1920, 3)
    return [f.copy() for f in frames]


def pick_still_moment(film, shots, rng):
    """A random story shot (not opening/ending credits) of >= 3.2 s whose outer bands hold still >= 85% of steps."""
    story = [s for s in shots if s["end_s"] - s["start_s"] >= 3.2 and 100 < s["start_s"] and s["end_s"] < 1320]
    rng.shuffle(story)
    for s in story[:40]:
        start = (s["start_s"] + s["end_s"]) / 2 - FRAMES / 23.976 / 2
        frames = grab(film, start, FRAMES)
        bands = [wraps.still_steps([f[:, :240] for f in frames], 6, 0.002),
                 wraps.still_steps([f[:, -240:] for f in frames], 6, 0.002)]
        share = [sum(b) / (len(b) - 1) for b in bands]
        if min(share) >= 0.85:
            return s, start, frames, share
    raise SystemExit("no still story shot found among 40 tries")


def still_share(frames):
    """Share of still steps in the outer 240 px bands, left and right (the routing test)."""
    bands = [wraps.still_steps([f[:, :240] for f in frames], 6, 0.002),
             wraps.still_steps([f[:, -240:] for f in frames], 6, 0.002)]
    return [round(sum(b) / max(1, len(b) - 1), 3) for b in bands]


def find_cut(frames, lo, hi):
    """Index of the first frame after the biggest picture change between frames lo..hi (a hard cut)."""
    g = [cv2.resize(cv2.cvtColor(f, cv2.COLOR_RGB2GRAY), (480, 270), interpolation=cv2.INTER_AREA).astype(np.int16)
         for f in frames]
    diffs = [float(np.abs(g[t] - g[t - 1]).mean()) for t in range(lo, hi)]
    return lo + int(np.argmax(diffs))


def compose(half, screen, mode, scale_cm):
    """One 16:9 half placed on its screen: 'fill' fits the screen, 'matched' gives it scale_cm physical width."""
    w = screen["w"] if mode == "fill" else int(round(screen["w"] * scale_cm / screen["cm"]))
    h = int(round(w * 9 / 16))
    if h > screen["h"]:
        h = screen["h"]
        w = int(round(h * 16 / 9))
    img = cv2.resize(half, (w, h), interpolation=cv2.INTER_LANCZOS4 if w > half.shape[1] else cv2.INTER_AREA)
    tile = np.zeros((screen["h"], screen["w"], 3), np.uint8)
    y0 = (screen["h"] - h) // 2
    x0 = screen["w"] - w if screen["x"] == 0 else 0                # each half hugs the bezel
    tile[y0:y0 + h, x0:x0 + w] = img
    return tile


MPV = os.path.join(cc.REPO, "tools", "mpv-v0.41.0-x86_64-w64-mingw32", "mpv.exe")
# A plain mpv window is shrunk to one screen; full screen over all screens covers the whole desktop at (0,0), and
# --video-unscaled maps the pre-composed video onto it pixel for pixel. %~dp0 = the .bat's own folder.
MPV_ARGS = "--fs --fs-screen=all --video-unscaled=yes --loop-file=inf --hwdec=no --osc=no --really-quiet"


def write_spans(odir, wide, tag):
    """span-<layout><tag>.mp4 for both layouts: each 16:9 half of the 32:9 canvas on its own screen."""
    scale_cm = min(s["cm"] for s in SCREENS)                       # matched: the smaller screen's width
    desk_w, desk_h = SCREENS[1]["x"] + SCREENS[1]["w"], max(s["h"] for s in SCREENS)
    for mode in ("fill", "matched"):
        span = []
        for f in wide:
            canvas = np.zeros((desk_h, desk_w, 3), np.uint8)
            for half, screen in ((f[:, :1920], SCREENS[0]), (f[:, 1920:], SCREENS[1])):
                canvas[:screen["h"], screen["x"]:screen["x"] + screen["w"]] = compose(half, screen, mode, scale_cm)
            span.append(canvas)
        hs.write_video(span, os.path.join(odir, f"span-{mode}{tag}.mp4"), 23.976)


def write_launchers(odir):
    for mode in ("fill", "matched"):
        with open(os.path.join(odir, f"play-{mode}.bat"), "w", encoding="utf-8", newline="") as bat:
            bat.write(f'@echo off\r\nrem The extended clip over both screens, {mode} layout. Press q to quit.\r\n'
                      f'start "" "{MPV}" {MPV_ARGS} "%~dp0span-{mode}.mp4"\r\n')
        with open(os.path.join(odir, f"play-compare-{mode}.bat"), "w", encoding="utf-8", newline="") as bat:
            bat.write(f'@echo off\r\nrem Original only, then extended ({mode} layout). Press > or < to switch, q to quit.\r\n'
                      f'start "" "{MPV}" {MPV_ARGS} --osd-playing-msg="${{filename}}" --osd-duration=3000 '
                      f'--loop-playlist=inf "%~dp0span-{mode}-original-only.mp4" "%~dp0span-{mode}.mp4"\r\n')


def write_compare(odir, frames, wide, picks=None):
    """What is real: the untouched clip, the extended frame(s) with the real frame's edges dashed (one row per shot,
    `picks` = frame indices), and the real frame alone with black sides in both screen layouts."""
    hs.write_video(frames, os.path.join(odir, "original-16x9.mp4"), 23.976)
    rows = []
    for t in picks or [len(wide) // 2]:
        marked = wide[t].copy()
        for x in (960, 2879):
            for y in range(0, 1080, 24):
                marked[y:y + 12, x - 1:x + 2] = (255, 255, 0)
        marked = cv2.resize(marked, (1920, 540), interpolation=cv2.INTER_AREA)
        for x, text in ((200, "AI"), (860, "ORIGINAL FRAME"), (1640, "AI")):
            cv2.putText(marked, text, (x, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 0), 4, cv2.LINE_AA)
            cv2.putText(marked, text, (x, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 0), 2, cv2.LINE_AA)
        rows.append(marked)
    cv2.imwrite(os.path.join(odir, "preview-marked.png"), cv2.cvtColor(np.vstack(rows), cv2.COLOR_RGB2BGR))
    black = np.zeros((1080, 960, 3), np.uint8)
    write_spans(odir, [np.hstack([black, f, black]) for f in frames], "-original-only")
    write_launchers(odir)


def prepare_sides(frame, tag):
    """Input files for one shot's two side plates: each side's canvas is the adjacent half of the picture plus a grey
    half (1920x1080, +4 grey rows for /32), and its caption source is that visible half only."""
    pad, jobs = plates.PAD, {}
    for name, keep in (("left", frame[:, :960]), ("right", frame[:, 960:])):
        canvas = np.full((1080 + 2 * pad, 1920, 3), plates.GREY, np.uint8)
        canvas[pad:pad + 1080, slice(960, 1920) if name == "left" else slice(0, 960)] = keep
        fname, half = f"panorama-demo-{tag}-{name}.png", f"panorama-demo-{tag}-{name}-half.png"
        cv2.imwrite(os.path.join(cc.INPUT_DIR, fname), cv2.cvtColor(canvas, cv2.COLOR_RGB2BGR))
        cv2.imwrite(os.path.join(cc.INPUT_DIR, half), cv2.cvtColor(keep, cv2.COLOR_RGB2BGR))
        jobs[name] = (fname, half)
    return jobs


def run_sides(port, jobs, seed, tag):
    """(sides, captions) for one shot: the Qwen-Image-2.1 + outpaint LoRA v2 plate of each side, cropped to the side."""
    sides, captions = {}, {}
    for name, (fname, half) in jobs.items():
        t0 = time.perf_counter()
        out = cc.run_graph(port, plates.qwen_graph("qwen-image-2.1-outpaint-v2.safetensors", fname, half, seed,
                                                   f"panorama/demo-{tag}-{name}"))
        raw = cc.saved_images(out, "12")[0][plates.PAD:plates.PAD + 1080]
        sides[name] = raw[:, :960] if name == "left" else raw[:, 960:]
        captions[name] = out.get("13", {}).get("text", [""])[0].strip()
        print(f"{tag} {name} side in {time.perf_counter() - t0:.0f} s", flush=True)
    return sides, captions


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("film")
    ap.add_argument("shots_json")
    ap.add_argument("out_dir")
    ap.add_argument("--at", type=float, default=None, help="start second instead of a random still story shot")
    ap.add_argument("--cut-at", type=float, default=None,
                    help="second of a cut (e.g. a scene change): 1.5 s either side, one pair of side plates per shot, "
                         "the sides switch on the exact cut frame")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--port", type=int, default=8189)
    ap.add_argument("--compare-only", default="", help="existing demo folder: add the comparison files, no generation")
    a = ap.parse_args(argv)
    if a.compare_only:
        demo = json.load(open(os.path.join(a.compare_only, "demo.json"), encoding="utf-8"))
        frames = grab(a.film, demo["start_s"], FRAMES)
        wide = hs.read_frames(os.path.join(a.compare_only, "wide-32x9.mp4"))[:len(frames)]
        for w_, f in zip(wide, frames):
            w_[:, 960:2880] = f                                   # the real picture exactly, not its re-encode
        write_compare(a.compare_only, frames, wide)
        print(f"comparison files added to {a.compare_only}")
        return 0
    seed = a.seed if a.seed is not None else int(time.time())
    shots = json.load(open(a.shots_json, encoding="utf-8"))["shots"]
    if a.cut_at is not None:                                       # 1.5 s either side of the cut, a segment per shot
        start = a.cut_at - FRAMES / 23.976 / 2
        frames = grab(a.film, start, FRAMES)
        cut = find_cut(frames, FRAMES // 2 - 12, FRAMES // 2 + 12)
        segments = [(0, cut), (cut, len(frames))]
        info = {"cut_at_s": a.cut_at, "cut_frame_in_clip": cut, "still_band_share": [still_share(frames[:cut]),
                                                                                    still_share(frames[cut:])]}
    else:
        if a.at is None:
            shot, start, frames, share = pick_still_moment(a.film, shots, random.Random(seed))
        else:
            start, frames, shot, share = a.at, grab(a.film, a.at, FRAMES), None, None
        segments = [(0, len(frames))]
        info = {"shot": shot["id"] if shot else None, "still_band_share": share}
    odir = os.path.join(a.out_dir, time.strftime("%Y%m%d-%H%M%S"))
    os.makedirs(odir, exist_ok=True)
    picks = [(s0 + s1) // 2 for s0, s1 in segments]                 # each shot's plate is drawn from its middle frame
    print(f"moment {start:.2f} s, segments {segments}, {info}", flush=True)

    # Every input is written before the server starts, so ComfyUI's file lists include them.
    jobs = [prepare_sides(frames[t], f"shot{i + 1}") for i, t in enumerate(picks)]
    server = cc.start_server(a.port, os.path.join(odir, "comfy-server.log"), cc.LOW_RAM)
    try:
        results = [run_sides(a.port, j, seed, f"shot{i + 1}") for i, j in enumerate(jobs)]
    finally:
        cc.stop_server(server)

    wide = []                                                      # the real picture, untouched, in the middle
    for (s0, s1), (sides, _) in zip(segments, results):
        wide += [np.hstack([sides["left"], f, sides["right"]]) for f in frames[s0:s1]]
    hs.write_video(wide, os.path.join(odir, "wide-32x9.mp4"), 23.976)
    cv2.imwrite(os.path.join(odir, "preview.png"), cv2.cvtColor(np.vstack([cv2.resize(wide[t], (1920, 540),
                interpolation=cv2.INTER_AREA) for t in picks]), cv2.COLOR_RGB2BGR))
    write_spans(odir, wide, "")
    write_compare(odir, frames, wide, picks)
    info.update({"start_s": round(start, 3), "seed": seed, "segments": segments,
                 "captions": [captions for _, captions in results],
                 "recipe": "qwen21-outpaint-v2 per side and shot, 1920x1080 half-new canvas, caption of the visible half"})
    hs.save_json(info, os.path.join(odir, "demo.json"))
    print(f"done: {odir}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
