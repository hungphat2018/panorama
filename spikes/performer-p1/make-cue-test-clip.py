"""Makes a frame-numbered test clip for the cue latch, and the ground truth about its shots.

Every frame shows (1920x1080, 16:9):
  - its own frame number in big digits, and the shot number
  - a barcode of the frame index (4 sync blocks, 12 index bits, 4 check bits: white = 1, black = 0, 40 px blocks, centred
    at y 880..930), so a screen capture can tell which frame was on screen even when a cue crops or scales the picture
  - a white border at the very edge, and coloured outlines at 5 % (yellow), 10 % (cyan) and 15 % (magenta) inset, so a crop
    of 5 % or 10 % shows up as "the outline now sits at the picture's edge"
  - a red bar sweeping across the whole frame in 2 s, so motion crossing the bezel is visible
The background colour changes at every cut, so a shot change is visible by eye.

  python -I make-cue-test-clip.py OUT_DIR [--seconds 60] [--fps 24000/1001] [--seed 5] [--keyint 48]

Writes OUT_DIR/cue-test.mkv, OUT_DIR/shots.json (fps, frame count, shot start frames and start times halfway between the
last frame of one shot and the first of the next) and OUT_DIR/pts.txt (frame timestamps in seconds, from ffprobe).
Needs numpy and an ffmpeg with libx264 on PATH (the WinGet full build, or the MSYS2 one).
"""
import argparse
import json
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

import numpy as np

W, H = 1920, 1080
BLOCK = 40
BAR_Y = 880
PALETTE = [(40, 60, 90), (90, 50, 50), (45, 85, 55), (80, 70, 35), (70, 45, 90), (35, 80, 85), (95, 60, 30), (60, 60, 60)]
INSETS = [(0.05, (255, 220, 0)), (0.10, (0, 220, 255)), (0.15, (255, 0, 220))]

# 5x7 digit font
FONT = {
    "0": "01110 10001 10011 10101 11001 10001 01110", "1": "00100 01100 00100 00100 00100 00100 01110",
    "2": "01110 10001 00001 00010 00100 01000 11111", "3": "11110 00001 00001 01110 00001 00001 11110",
    "4": "00010 00110 01010 10010 11111 00010 00010", "5": "11111 10000 11110 00001 00001 10001 01110",
    "6": "00110 01000 10000 11110 10001 10001 01110", "7": "11111 00001 00010 00100 01000 01000 01000",
    "8": "01110 10001 10001 01110 10001 10001 01110", "9": "01110 10001 10001 01111 00001 00010 01100",
    "S": "01111 10000 10000 01110 00001 00001 11110",
}
GLYPH = {k: np.array([[int(c) for c in row] for row in v.split()], dtype=bool) for k, v in FONT.items()}


def draw_text(img, text, x, y, scale, color=(255, 255, 255), back=(0, 0, 0)):
    """Draws text with the 5x7 font; a dark box behind it keeps it readable on any background."""
    cell = 6 * scale
    w, h = len(text) * cell + scale, 8 * scale + scale
    img[y:y + h, x:x + w] = back
    for i, ch in enumerate(text):
        g = GLYPH[ch]
        big = np.kron(g, np.ones((scale, scale), dtype=bool))
        region = img[y + scale:y + scale + 7 * scale, x + scale + i * cell:x + scale + i * cell + 5 * scale]
        region[big] = color
    return w


def barcode_bits(n):
    check = bin(n).count("1") % 16
    return [1, 0, 1, 0] + [(n >> (11 - i)) & 1 for i in range(12)] + [(check >> (3 - i)) & 1 for i in range(4)]


def make_base(color):
    img = np.full((H, W, 3), color, dtype=np.uint8)
    img[:12, :] = 255
    img[-12:, :] = 255
    img[:, :12] = 255
    img[:, -12:] = 255
    for frac, c in INSETS:
        x, y = int(W * frac), int(H * frac)
        img[y:y + 6, x:W - x] = c
        img[H - y - 6:H - y, x:W - x] = c
        img[y:H - y, x:x + 6] = c
        img[y:H - y, W - x - 6:W - x] = c
    return img


def shot_starts(total, fps, rng):
    starts, pos = [], 0
    while pos < total:
        starts.append(pos)
        pos += max(12, int(rng.uniform(2.0, 6.0) * float(fps)))
    return starts


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out_dir", type=Path)
    ap.add_argument("--seconds", type=float, default=60)
    ap.add_argument("--fps", default="24000/1001")
    ap.add_argument("--seed", type=int, default=5)
    ap.add_argument("--keyint", type=int, default=48)
    a = ap.parse_args()
    fps = Fraction(a.fps)
    total = int(a.seconds * fps)
    if total >= 4096:
        sys.exit("the barcode holds 12 bits: at most 4095 frames")
    rng = np.random.default_rng(a.seed)
    starts = shot_starts(total, fps, rng)
    a.out_dir.mkdir(parents=True, exist_ok=True)
    video = a.out_dir / "cue-test.mkv"
    bases = [make_base(PALETTE[i % len(PALETTE)]) for i in range(len(starts))]

    enc = subprocess.Popen([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
        "-r", a.fps, "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "14", "-g", str(a.keyint),
        "-pix_fmt", "yuv420p", "-vf", "setsar=1", "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
        "-color_range", "tv", str(video)], stdin=subprocess.PIPE)
    shot = 0
    for n in range(total):
        while shot + 1 < len(starts) and n >= starts[shot + 1]:
            shot += 1
        img = bases[shot].copy()
        x = (n * 40) % W                                          # the sweeping bar: 40 px per frame
        img[:, x:x + 24] = (255, 40, 40)
        digits = str(n)
        draw_text(img, digits, (W - len(digits) * 6 * 28) // 2, 300, 28)
        draw_text(img, "S" + str(shot), 330, 560, 12)
        for i, bit in enumerate(barcode_bits(n)):
            xx = (W - 20 * BLOCK) // 2 + i * BLOCK
            img[BAR_Y:BAR_Y + 50, xx:xx + BLOCK] = 255 if bit else 0
        enc.stdin.write(img.tobytes())
    enc.stdin.close()
    if enc.wait():
        sys.exit("ffmpeg failed")

    pts = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts_time", "-of", "csv=p=0",
                          str(video)], capture_output=True, text=True, check=True).stdout.split()
    times = sorted(float(x.strip(",")) for x in pts if x.strip(",") not in ("", "N/A"))
    times = [t - times[0] for t in times]
    (a.out_dir / "pts.txt").write_text("\n".join(f"{t:.6f}" for t in times) + "\n")
    truth = {
        "video": video.name, "width": W, "height": H, "fps": a.fps, "frames": total,
        "shot_start_frames": starts,
        "shot_start_seconds": [0.0] + [0.5 * (times[s - 1] + times[s]) for s in starts[1:]],
        "barcode": {"block_px": BLOCK, "x0": (W - 20 * BLOCK) // 2, "y0": BAR_Y, "height": 50,
                    "bits": "1010 + 12 index bits + 4 check bits (popcount mod 16)"},
        "insets_percent": [5, 10, 15],
    }
    (a.out_dir / "shots.json").write_text(json.dumps(truth, indent=1))
    print(f"{video}: {total} frames, {len(starts)} shots, {times[-1]:.2f} s")


if __name__ == "__main__":
    main()
