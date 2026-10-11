"""Make the clip for the anchored seam-offset threshold test (M1 phase 01, step 2).

Question: from how many milliseconds apart does it show when the two sides of a bezel
switch film pictures at different moments? Each trial plays the same 3 s twice on the
240 Hz laptop panel: once with both sides of a fake 20 mm bezel switching together
(the reference) and once with the right side X ms later, in random order. The viewer
says which of the two split at the bezel. Guessing gets about 5 of 10 right; seeing it
gets 9 or 10.

Contents, both 24 fps as filmed, with a film camera's motion blur (1/48 s exposure):
  card  a textured card crossing the bezel over a still picture: motion that pan mode
        does not smooth, such as a character or an object moving on its own
  pan   the whole picture panning across the bezel: a camera slide played as filmed
Offsets: 1 and 2 refreshes of the 240 Hz panel (4.2 and 8.3 ms).

Writes OUT_DIR/seam-threshold-trials.mkv (2560x1600, 240 fps, all trials in one file so
no file switch can cause a hitch) and OUT_DIR/seam-threshold-trials.json (timing and the
key). run-seam-threshold-test.py plays it and scores the answers.

Usage: python -s make-seam-threshold-trials.py IMAGE OUT_DIR [--trials-per-condition 10]
                                               [--conditions card:1,card:2,pan:1,pan:2] [--seed N]
"""
import argparse
import json
import math
import random
import subprocess
from pathlib import Path

import cv2
import numpy as np

W, H = 2560, 1600                    # laptop panel, native pixels
REFRESH, FILM_FPS, SHUTTER = 240, 24, 1 / 48
HOLD = REFRESH // FILM_FPS           # refreshes per film picture
BEZEL_PX = 150                       # about 20 mm on the 344 mm wide laptop panel
LEFT_END, RIGHT_START = (W - BEZEL_PX) // 2, (W + BEZEL_PX) // 2
SEGMENTS = (("cue1", 0.5), ("interval1", 3.0), ("cue2", 0.6), ("interval2", 3.0), ("ask", 0.4))
CARD_W, CARD_H, CARD_SPEED = 300, 460, 720   # pixels, pixels, pixels per second
PAN_SPEED = 480
GRAY = 90
DEFAULT_CONDITIONS = "card:1,card:2,pan:1,pan:2"


def frames(seconds):
    return round(seconds * REFRESH)


def film_time(n):
    """Centre of film picture n's exposure (shutter opens at n / 24 s)."""
    return n / FILM_FPS + SHUTTER / 2


def box_blur_x(img, length):
    """Box blur along x, as long as the distance moved during one exposure."""
    half = length / 2
    k = 2 * int(math.ceil(half)) + 1
    taps = np.arange(k) - k // 2
    w = np.clip(np.minimum(taps + 0.5, half) - np.maximum(taps - 0.5, -half), 0, None)
    return cv2.filter2D(img, -1, (w / w.sum()).astype(np.float32)[None, :], borderType=cv2.BORDER_CONSTANT)


class Card:
    """A textured card with a light border, blurred along its motion, composited at sub-pixel x."""

    def __init__(self, image):
        background = cv2.resize(image, (W, H), interpolation=cv2.INTER_AREA)
        self.background = (background * 0.7).astype(np.float32)
        crop = image[1000:1000 + 690, 1700:1700 + 450][:, ::-1]   # forest and lake: real texture
        card = cv2.resize(crop, (CARD_W, CARD_H), interpolation=cv2.INTER_AREA).astype(np.float32)
        card = np.minimum(card * 1.15 + 15, 255)
        cv2.rectangle(card, (0, 0), (CARD_W - 1, CARD_H - 1), (235, 235, 235), 6)
        pad = 16
        rgb = np.zeros((CARD_H, CARD_W + 2 * pad, 3), np.float32)
        alpha = np.zeros((CARD_H, CARD_W + 2 * pad), np.float32)
        rgb[:, pad:pad + CARD_W] = card
        alpha[:, pad:pad + CARD_W] = 1.0
        blur = CARD_SPEED * SHUTTER
        self.rgb, self.alpha, self.pad = box_blur_x(rgb, blur), box_blur_x(alpha, blur), pad

    def picture(self, n, direction):
        """Film picture n: card centre crosses the bezel centre half way through the interval."""
        interval = dict(SEGMENTS)["interval1"]
        centre = W / 2 + direction * CARD_SPEED * (film_time(n) - interval / 2)
        x_left = centre - CARD_W / 2 - self.pad
        xi = int(math.floor(x_left))
        shift = np.float32([[1, 0, x_left - xi], [0, 1, 0]])
        sw = self.alpha.shape[1] + 1
        rgb = cv2.warpAffine(self.rgb, shift, (sw, CARD_H), flags=cv2.INTER_CUBIC, borderValue=0)
        alpha = cv2.warpAffine(self.alpha, shift, (sw, CARD_H), flags=cv2.INTER_CUBIC, borderValue=0)
        out = self.background.copy()
        y0 = (H - CARD_H) // 2
        x0, x1 = max(xi, 0), min(xi + sw, W)
        if x1 > x0:
            a = np.clip(alpha[:, x0 - xi:x1 - xi], 0, 1)[..., None]
            region = out[y0:y0 + CARD_H, x0:x1]
            region[:] = region * (1 - a) + rgb[:, x0 - xi:x1 - xi] * a
        return np.clip(out, 0, 255).astype(np.uint8)


class Pan:
    """The picture mirrored side by side, blurred along the pan, viewed at sub-pixel x."""

    def __init__(self, image, tiles=4):
        img = cv2.resize(image, (W, H), interpolation=cv2.INTER_AREA)
        pano = np.concatenate([img if i % 2 == 0 else img[:, ::-1] for i in range(tiles)], axis=1)
        self.pano = np.ascontiguousarray(box_blur_x(pano, PAN_SPEED * SHUTTER))
        self.span = self.pano.shape[1] - W - 8

    def picture(self, n, direction, start_x):
        x = start_x + direction * PAN_SPEED * film_time(n)
        xi = int(math.floor(x))
        shift = np.float32([[1, 0, -(x - xi)], [0, 1, 0]])
        return cv2.warpAffine(self.pano[:, xi:xi + W + 4], shift, (W, H), flags=cv2.INTER_CUBIC,
                              borderMode=cv2.BORDER_REPLICATE)


def text(frame, label, centre, scale, thickness=3):
    (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, scale, thickness)
    org = (int(centre[0] - tw / 2), int(centre[1] + th / 2))
    cv2.putText(frame, label, org, cv2.FONT_HERSHEY_SIMPLEX, scale, (230, 230, 230), thickness, cv2.LINE_AA)


def cue_screen(label):
    frame = np.full((H, W, 3), GRAY, np.uint8)
    frame[:, LEFT_END:RIGHT_START] = 0
    for cx in (LEFT_END / 2, (RIGHT_START + W) / 2):
        text(frame, label, (cx, H / 2), 9.0, 18)
    return frame


def corner_label(frame, label):
    cv2.rectangle(frame, (24, 24), (110, 130), (0, 0, 0), -1)
    text(frame, label, (67, 77), 2.6, 5)


def encoder(path):
    return subprocess.Popen(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo",
                             "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", str(REFRESH), "-i", "-",
                             "-c:v", "hevc_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "19", "-b:v", "0",
                             "-g", "120", "-pix_fmt", "yuv420p", str(path)], stdin=subprocess.PIPE)


def plan_trials(conditions, per_condition, rng, pan_span):
    trials = []
    for content, refreshes in conditions:
        # Balanced: the late side sits in clip 1 for half the trials, so a habit of answering "2" can't score.
        positions = [1 + i % 2 for i in range(per_condition)]
        rng.shuffle(positions)
        for position in positions:
            trial = {"content": content, "offset_refreshes": refreshes,
                     "offset_ms": round(refreshes * 1000 / REFRESH, 1),
                     "offset_interval": position, "direction": rng.choice((-1, 1))}
            if content == "pan":
                travel = PAN_SPEED * (dict(SEGMENTS)["interval1"] + 0.5)
                lo, hi = (0, pan_span - travel) if trial["direction"] > 0 else (travel, pan_span)
                trial["start_x"] = round(rng.uniform(lo, hi), 1)
            trials.append(trial)
    rng.shuffle(trials)
    return trials


def write_interval(enc, source, trial, delay, label):
    """One interval: left of the bezel shows picture r // HOLD, right shows it `delay` refreshes later."""
    cache = {}

    def picture(n):
        if n not in cache:
            args = (n, trial["direction"]) + ((trial["start_x"],) if trial["content"] == "pan" else ())
            cache[n] = source.picture(*args)
            for old in [m for m in cache if m < n - 2]:
                del cache[old]
        return cache[n]

    for r in range(frames(dict(SEGMENTS)["interval1"])):
        frame = np.zeros((H, W, 3), np.uint8)
        frame[:, :LEFT_END] = picture(r // HOLD)[:, :LEFT_END]
        frame[:, RIGHT_START:] = picture(max(0, r - delay) // HOLD)[:, RIGHT_START:]
        corner_label(frame, label)
        enc.stdin.write(frame.tobytes())


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("image", type=Path)
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--trials-per-condition", type=int, default=10)
    parser.add_argument("--conditions", default=DEFAULT_CONDITIONS, help="content:refreshes, comma separated")
    parser.add_argument("--seed", type=int, default=None, help="random seed (default: from the clock)")
    args = parser.parse_args()

    image = cv2.imread(str(args.image), cv2.IMREAD_COLOR)
    if image is None:
        raise SystemExit(f"cannot read {args.image}")
    conditions = [(c.split(":")[0], int(c.split(":")[1])) for c in args.conditions.split(",")]
    seed = args.seed if args.seed is not None else random.SystemRandom().randrange(1 << 30)
    sources = {"card": Card(image), "pan": Pan(image)}
    trials = plan_trials(conditions, args.trials_per_condition, random.Random(seed), sources["pan"].span)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    clip, key = args.out_dir / "seam-threshold-trials.mkv", args.out_dir / "seam-threshold-trials.json"
    cues = {name: cue_screen(name) for name in ("1", "2", "?")}
    enc, t = encoder(clip), 0
    for i, trial in enumerate(trials, 1):
        trial["index"], trial["times"] = i, {}
        for name, seconds in SEGMENTS:
            trial["times"][name] = round(t / REFRESH, 6)
            if name.startswith("interval"):
                which = int(name[-1])
                delay = trial["offset_refreshes"] if which == trial["offset_interval"] else 0
                write_interval(enc, sources[trial["content"]], trial, delay, str(which))
            else:
                frame = cues[{"cue1": "1", "cue2": "2", "ask": "?"}[name]]
                for _ in range(frames(seconds)):
                    enc.stdin.write(frame.tobytes())
            t += frames(seconds)
        trial["times"]["end"] = round(t / REFRESH, 6)
        print(f"trial {i}/{len(trials)}: {trial['content']} +{trial['offset_ms']} ms in interval "
              f"{trial['offset_interval']}", flush=True)
    enc.stdin.close()
    if enc.wait() != 0:
        raise SystemExit(f"encoding {clip} failed")
    key.write_text(json.dumps({"refresh_hz": REFRESH, "film_fps": FILM_FPS, "bezel_px": BEZEL_PX, "seed": seed,
                               "segments": dict(SEGMENTS), "trials": trials}, indent=1))
    print(f"wrote {clip} ({clip.stat().st_size / 1e6:.0f} MB) and {key}")


if __name__ == "__main__":
    main()
