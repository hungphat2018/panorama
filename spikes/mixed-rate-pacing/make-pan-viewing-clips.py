"""Make the by-eye viewing clips for the pacing questions, from one still image.

1. pan-ab-laptop.mkv (2560x1600, 240 fps, laptop panel at 240 Hz): a slow camera
   pan alternating every 6 s between A = 24 fps as filmed (each picture held 10
   refreshes, with a film camera's motion blur) and B = smooth (a new picture
   every refresh, same blur: what making a 24 fps pan smooth would look like).
2. seam-sim-laptop.mkv (2560x1600, 240 fps, laptop panel): a 24 fps pan across a
   fake bezel; the right side switches pictures 0-4 refreshes (0-17 ms) after the
   left, 6 s per step, labelled, ending on 0 ms again.
   seam-pairs-laptop.mkv: the same, but alternating 0 ms with one offset every
   3 s (0/4, 0/4, 0/8, 0/8, 0/13, 0/13 ms), so the viewer only judges same or not.
3. pan-span.mkv (4480x1600, 24 fps, both screens via --fs-screen=all): a 24 fps
   pan across the real seam. Each screen samples the picture at its own pixel
   size (laptop 344 mm, external 476 mm wide, top edges level, --gap-mm apart,
   picture continuing behind the bezel), so it keeps one physical size and speed.
   pan-span-ab.mkv: the same pan at 240 fps, alternating every 6 s between
   A = 24 fps as filmed and B = smooth (pan mode), labelled on both screens.

The picture is the image mirrored side by side (a long pan without a jump),
blurred along the pan like a 180-degree film shutter (exposure 1/48 s).

Usage: python make-pan-viewing-clips.py IMAGE OUT_DIR [--only ab,seam,pairs,span,span-ab] [--gap-mm 20]
"""
import argparse
import math
import subprocess
from pathlib import Path

import cv2
import numpy as np

LAPTOP_W, LAPTOP_H, LAPTOP_MM = 2560, 1600, 344.0
EXT_W, EXT_H, EXT_MM, EXT_H_MM = 1920, 1080, 476.0, 267.8   # 21.5-inch 16:9 panel
REFRESH, FILM_FPS, SHUTTER = 240, 24, 1 / 48
HOLD = REFRESH // FILM_FPS                                     # refreshes per film picture
SEGMENT_S = 6
FAKE_BEZEL_PX = 150                                            # about 20 mm on the laptop panel
SECONDS = {"ab": 36, "span": 40, "span-ab": 24}
SPEED = {"ab": 320, "seam": 480, "span": 480}                  # laptop pixels per second
SEAM_SCHEDULES = {                                             # (right-side delay in refreshes per step, step seconds)
    "seam": ([0, 1, 2, 3, 4, 0], 6),
    "pairs": ([0, 1, 0, 1, 0, 2, 0, 2, 0, 3, 0, 3], 3),
}


def panorama(image_path, height, tiles=4):
    img = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if img is None:
        raise SystemExit(f"cannot read {image_path}")
    scale = height / img.shape[0]
    img = cv2.resize(img, (round(img.shape[1] * scale), height), interpolation=cv2.INTER_AREA)
    return np.ascontiguousarray(np.concatenate([img if i % 2 == 0 else img[:, ::-1] for i in range(tiles)], axis=1))


def shutter_blur(pano, speed):
    """Box blur along the pan, as long as the distance moved during one exposure."""
    half = speed * SHUTTER / 2
    k = 2 * int(math.ceil(half)) + 1
    taps = np.arange(k) - k // 2
    w = np.clip(np.minimum(taps + 0.5, half) - np.maximum(taps - 0.5, -half), 0, None)
    return cv2.filter2D(pano, -1, (w / w.sum()).astype(np.float32)[None, :], borderType=cv2.BORDER_REFLECT)


def view(pano, x, w, h):
    """w x h window of pano starting at fractional column x (cubic sub-pixel shift)."""
    xi = int(math.floor(x))
    shift = np.float32([[1, 0, -(x - xi)], [0, 1, 0]])
    return cv2.warpAffine(pano[:h, xi:xi + w + 4], shift, (w, h), flags=cv2.INTER_CUBIC,
                          borderMode=cv2.BORDER_REPLICATE)


def pan_x(t, speed, span):
    """Ping-pong pan position at time t over [0, span]."""
    u = (speed * t) % (2 * span)
    return u if u < span else 2 * span - u


def film_time(n):
    """Centre of film picture n's exposure (shutter opens at n / 24 s)."""
    return n / FILM_FPS + SHUTTER / 2


def label(frame, text, x0=0):
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 1.1, 2)
    cv2.rectangle(frame, (x0 + 20, 20), (x0 + 44 + tw, 44 + th), (0, 0, 0), -1)
    cv2.putText(frame, text, (x0 + 32, 32 + th), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (255, 255, 255), 2, cv2.LINE_AA)


def encoder(path, w, h, fps):
    return subprocess.Popen(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
                             "-s", f"{w}x{h}", "-r", str(fps), "-i", "-", "-c:v", "hevc_nvenc", "-preset", "p5",
                             "-rc", "vbr", "-cq", "19", "-b:v", "0", "-g", str(2 * fps), "-pix_fmt", "yuv420p", str(path)],
                            stdin=subprocess.PIPE)


def finish(enc, path):
    enc.stdin.close()
    if enc.wait() != 0:
        raise SystemExit(f"encoding {path} failed")
    print(f"wrote {path} ({path.stat().st_size / 1e6:.0f} MB)")


def make_ab(image, out):
    pano = shutter_blur(panorama(image, LAPTOP_H), SPEED["ab"])
    span = pano.shape[1] - LAPTOP_W - 8
    enc, key, frame = encoder(out, LAPTOP_W, LAPTOP_H, REFRESH), None, None
    for r in range(SECONDS["ab"] * REFRESH):
        film = (r // (SEGMENT_S * REFRESH)) % 2 == 0
        new_key = ("A", r // HOLD) if film else ("B", r)
        if new_key != key:
            t = film_time(r // HOLD) if film else r / REFRESH + SHUTTER / 2
            frame = view(pano, pan_x(t, SPEED["ab"], span), LAPTOP_W, LAPTOP_H)
            label(frame, "A  24 fps as filmed" if film else "B  smooth: a new picture every refresh")
            key = new_key
        enc.stdin.write(frame.tobytes())
    finish(enc, out)


def make_seam(image, out, schedule):
    pano = shutter_blur(panorama(image, LAPTOP_H), SPEED["seam"])
    span = pano.shape[1] - LAPTOP_W - 8
    steps, step_s = SEAM_SCHEDULES[schedule]
    left_end, right_start = (LAPTOP_W - FAKE_BEZEL_PX) // 2, (LAPTOP_W + FAKE_BEZEL_PX) // 2
    cache, enc = {}, encoder(out, LAPTOP_W, LAPTOP_H, REFRESH)

    def picture(n):
        if n not in cache:
            cache[n] = view(pano, pan_x(film_time(n), SPEED["seam"], span), LAPTOP_W, LAPTOP_H)
            for old in [m for m in cache if m < n - 2]:
                del cache[old]
        return cache[n]

    for r in range(len(steps) * step_s * REFRESH):
        k = steps[r // (step_s * REFRESH)]
        frame = np.zeros((LAPTOP_H, LAPTOP_W, 3), np.uint8)
        frame[:, :left_end] = picture(r // HOLD)[:, :left_end]
        frame[:, right_start:] = picture(max(0, r - k) // HOLD)[:, right_start:]
        label(frame, f"right side {k * 1000 / REFRESH:.0f} ms later")
        enc.stdin.write(frame.tobytes())
    finish(enc, out)


def make_span(image, out, gap_mm, ab=False):
    pitch_l, pitch_e = LAPTOP_MM / LAPTOP_W, EXT_MM / EXT_W
    pano_l = shutter_blur(panorama(image, math.ceil(EXT_H_MM / pitch_l) + 8), SPEED["span"])
    scale_e = pitch_l / pitch_e  # external pixels per laptop pixel
    pano_e = cv2.resize(pano_l, (round(pano_l.shape[1] * scale_e), round(pano_l.shape[0] * scale_e)),
                        interpolation=cv2.INTER_AREA)
    offset_e = (LAPTOP_MM + gap_mm) / pitch_e  # where the external's left edge sits, in its own pixels
    span = min(pano_l.shape[1] - LAPTOP_W, (pano_e.shape[1] - EXT_W - offset_e) / scale_e) - 8
    fps = REFRESH if ab else FILM_FPS
    enc, key, frame = encoder(out, LAPTOP_W + EXT_W, LAPTOP_H, fps), None, None
    for r in range(SECONDS["span-ab" if ab else "span"] * fps):
        film = not ab or (r // (SEGMENT_S * REFRESH)) % 2 == 0
        n = r // HOLD if ab else r                      # film picture shown at this output frame
        new_key = ("A", n) if film else ("B", r)
        if new_key != key:
            x = pan_x(film_time(n) if film else r / REFRESH + SHUTTER / 2, SPEED["span"], span)
            frame = np.zeros((LAPTOP_H, LAPTOP_W + EXT_W, 3), np.uint8)
            frame[:, :LAPTOP_W] = view(pano_l, x, LAPTOP_W, LAPTOP_H)
            frame[:EXT_H, LAPTOP_W:] = view(pano_e, x * scale_e + offset_e, EXT_W, EXT_H)
            if ab:
                text = "A  24 fps as filmed" if film else "B  smooth (pan mode)"
                label(frame, text)
                label(frame, text, LAPTOP_W)
            key = new_key
        enc.stdin.write(frame.tobytes())
    finish(enc, out)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("image", type=Path)
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--only", default="ab,seam,pairs,span")
    parser.add_argument("--gap-mm", type=float, default=20.0, help="bezel gap between the two screens' pictures")
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    wanted = set(args.only.split(","))
    if "ab" in wanted:
        make_ab(args.image, args.out_dir / "pan-ab-laptop.mkv")
    if "seam" in wanted:
        make_seam(args.image, args.out_dir / "seam-sim-laptop.mkv", "seam")
    if "pairs" in wanted:
        make_seam(args.image, args.out_dir / "seam-pairs-laptop.mkv", "pairs")
    if "span" in wanted:
        make_span(args.image, args.out_dir / "pan-span.mkv", args.gap_mm)
    if "span-ab" in wanted:
        make_span(args.image, args.out_dir / "pan-span-ab.mkv", args.gap_mm, ab=True)


if __name__ == "__main__":
    main()
