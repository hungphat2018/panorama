"""Build the 24 fps vs smooth A/B clip from real pan shots of a film or an episode.

For each chosen shot (start time and length) it decodes the shot at full size,
measures how far the picture slides between neighbouring frames (phase
correlation refined by ECC, at half size) and draws one smooth path through the
position of every new drawing (shape-preserving cubic, so the animators' own
speed-up and slow-down are kept, and a background that anime moves only every
2nd or 3rd frame gets a steady path instead of steps). Then, at 240 pictures
per second:
  A = the frames exactly as drawn, each held 10 refreshes (24 fps);
  B = for every refresh, the nearest frame slid to where the smooth path is at
      that moment. Exact for a pure slide; the strip a slide uncovers at the edge
      is filled from the neighbouring frame.
Each shot plays A then B, labelled; the clip loops in watch-viewing-clip.py.
Use shots where one slide explains the whole picture (find-pan-shots.py).

Usage: python make-real-pan-ab.py VIDEO OUT_MKV --shot START_S:SECONDS [--shot ...]
"""
import argparse
import math
import subprocess
from fractions import Fraction
from pathlib import Path

import cv2
import numpy as np
from scipy.interpolate import PchipInterpolator

HOLD = 10        # refreshes per film frame at 240 Hz
OUT_FPS = 240
EDGE = 4         # extra pixels taken from the neighbour next to an uncovered strip (filter support)
ECC_CRITERIA = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 60, 1e-5)


def probe(video):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height,r_frame_rate", "-of", "default=nw=1:nk=1", str(video)],
                         capture_output=True, text=True, check=True).stdout.split()
    return int(out[0]), int(out[1]), Fraction(out[2])


def decode(video, start, count, w, h):
    cmd = ["ffmpeg", "-v", "error", "-hwaccel", "d3d11va", "-ss", f"{start:.3f}", "-i", str(video), "-map", "0:v:0",
           "-frames:v", str(count), "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    n = len(raw) // (w * h * 3)
    return [np.frombuffer(raw, np.uint8, w * h * 3, i * w * h * 3).reshape(h, w, 3) for i in range(n)]


def slide(prev, cur):
    """(dx, dy) the picture moved from prev to cur, in full-size pixels."""
    a, b = (cv2.cvtColor(cv2.resize(f, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
            .astype(np.float32) for f in (prev, cur))
    # Copies: when the size is already FFT-friendly, phaseCorrelate applies the window in place.
    (dx, dy), _ = cv2.phaseCorrelate(a.copy(), b.copy(), cv2.createHanningWindow(a.shape[::-1], cv2.CV_32F))
    warp = np.float32([[1, 0, dx], [0, 1, dy]])
    mask = np.zeros(a.shape, np.uint8)
    mask[40:-40, 40:-40] = 1
    try:
        _, warp = cv2.findTransformECC(a, b, warp, cv2.MOTION_TRANSLATION, ECC_CRITERIA, mask, 5)
    except cv2.error:
        pass  # flat or held frame: keep the phase-correlation estimate
    return 2 * float(warp[0, 2]), 2 * float(warp[1, 2])


def shifted(frame, sx, sy, border):
    return cv2.warpAffine(frame, np.float32([[1, 0, sx], [0, 1, sy]]), (frame.shape[1], frame.shape[0]),
                          flags=cv2.INTER_LANCZOS4, borderMode=border)


def label(frame, text):
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.9, 2)
    cv2.rectangle(frame, (16, 16), (36 + tw, 36 + th), (0, 0, 0), -1)
    cv2.putText(frame, text, (26, 26 + th), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2, cv2.LINE_AA)


def render_shot(frames, start, enc):
    n = len(frames)
    steps = np.array([slide(frames[i], frames[i + 1]) for i in range(n - 1)])
    pos = np.vstack([[0.0, 0.0], np.cumsum(steps, axis=0)])           # where each frame's picture sits
    moved = np.hypot(steps[:, 0], steps[:, 1]) >= 0.3
    keys = np.concatenate([[0], np.nonzero(moved)[0] + 1])               # frames that show a new drawing
    curve = PchipInterpolator(keys.astype(float), pos[keys], axis=0, extrapolate=True)
    path = lambda tau: curve(tau)
    off_path = np.abs(curve(np.arange(n, dtype=float)) - pos).max(axis=0)
    speed = np.hypot(*(pos[-1] - pos[0])) / (n - 1) * 24
    print(f"  shot {start:.2f} s: {n} frames, {speed:.0f} px/s, {n - len(keys)} held frames, "
          f"held frames off the smooth path by up to {off_path.round(1).tolist()} px")
    stamp = f"{int(start // 60):02d}:{start % 60:04.1f}"
    for f in frames:                                                     # A: as drawn
        img = f.copy()
        label(img, f"A  24 fps as drawn   (shot {stamp})")
        data = img.tobytes()
        for _ in range(HOLD):
            enc.stdin.write(data)
    h, w = frames[0].shape[:2]
    way = pos[-1] - pos[0]                                               # overall pan direction
    for r in range(n * HOLD):                                            # B: smooth path
        tau = r / HOLD
        i = min(n - 1, int(round(tau)))
        target = path(tau)
        sx, sy = target - pos[i]
        img = shifted(frames[i], sx, sy, cv2.BORDER_REPLICATE)
        # The uncovered strip shows scenery that a frame further along (or behind) the path still has.
        ahead = (sx * way[0] + sy * way[1]) > 0
        candidates = range(i + 1, min(n, i + 5)) if ahead else range(i - 1, max(-1, i - 5), -1)
        j = next((k for k in candidates if ((pos[k] - target) @ way >= 0) == ahead), None)
        if j is not None and (abs(sx) >= 0.01 or abs(sy) >= 0.01):
            fill = shifted(frames[j], *(path(tau) - pos[j]), cv2.BORDER_REPLICATE)
            cx, cy = math.ceil(abs(sx)) + EDGE, math.ceil(abs(sy)) + EDGE
            if sx > 0:
                img[:, :cx] = fill[:, :cx]
            elif sx < 0:
                img[:, w - cx:] = fill[:, w - cx:]
            if sy > 0:
                img[:cy] = fill[:cy]
            elif sy < 0:
                img[h - cy:] = fill[h - cy:]
        label(img, f"B  smooth: pan made smooth   (shot {stamp})")
        enc.stdin.write(img.tobytes())


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("video", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--shot", action="append", required=True, help="START_S:SECONDS")
    args = parser.parse_args()

    w, h, fps = probe(args.video)
    enc = subprocess.Popen(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
                            "-s", f"{w}x{h}", "-r", str(OUT_FPS), "-i", "-", "-c:v", "hevc_nvenc", "-preset", "p5",
                            "-rc", "vbr", "-cq", "18", "-b:v", "0", "-g", str(2 * OUT_FPS), "-pix_fmt", "yuv420p",
                            str(args.out)], stdin=subprocess.PIPE)
    for item in args.shot:
        start, seconds = map(float, item.split(":"))
        frames = decode(args.video, start, int(round(seconds * fps)), w, h)
        render_shot(frames, start, enc)
    enc.stdin.close()
    if enc.wait() != 0:
        raise SystemExit("encoding failed")
    print(f"wrote {args.out} ({args.out.stat().st_size / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
