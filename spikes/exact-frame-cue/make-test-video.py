"""Make the synthetic test video and its ground truth for the exact-frame cue spike.

Every frame shows a barcode of its own frame index, so a screen capture can tell
which source frame was on screen. The video is cut into shots of random length;
shot k carries cue level k % 4, and the performer under test must show that level
from the shot's first frame on. The layout (barcode position, the stripe the
shader paints) is written into truth.json so the shader generator and the
analysis read the same numbers.

Usage: python make-test-video.py OUT_DIR [--seconds 120] [--fps 24] [--seed 7]
Writes OUT_DIR/test.mkv and OUT_DIR/truth.json.
"""
import argparse
import json
import subprocess
from fractions import Fraction
from pathlib import Path

import numpy as np

WIDTH, HEIGHT = 1280, 800      # 16:10, fills the 2560x1600 panel with no bars
BLOCK = 16                     # barcode block edge, source pixels
INDEX_BITS, CHECK_BITS = 12, 4  # frame index + popcount check
ORIGIN = (16, 16)              # barcode top-left, source pixels
SCREEN_SCALE = 2               # 1280x800 shown fullscreen on 2560x1600
PROBE_SCREEN_X = (576, 640)    # full-height stripe the shader paints, screen pixels
LEVELS_GRAY = [0.2, 0.4, 0.6, 0.8]
BACKGROUNDS = [40, 70, 100, 130]  # per-shot background so cuts are visible by eye


def shot_starts(total_frames, rng):
    """Mostly 0.5-4 s shots, with some 2-6 frame shots to stress the switch."""
    starts, pos = [], 0
    while pos < total_frames:
        starts.append(pos)
        pos += int(rng.integers(2, 7)) if rng.random() < 0.15 else int(rng.integers(12, 97))
    return starts


def barcode_bits(n):
    check = bin(n).count("1") % (1 << CHECK_BITS)
    index = [(n >> (INDEX_BITS - 1 - i)) & 1 for i in range(INDEX_BITS)]
    return index + [(check >> (CHECK_BITS - 1 - i)) & 1 for i in range(CHECK_BITS)]


def draw_frame(n, background):
    frame = np.full((HEIGHT, WIDTH, 3), background, dtype=np.uint8)
    x0, y0 = ORIGIN
    for i, bit in enumerate(barcode_bits(n)):
        x = x0 + i * BLOCK
        frame[y0:y0 + BLOCK, x:x + BLOCK] = 255 if bit else 0
    return frame


def probe(args):
    out = subprocess.run(["ffprobe", "-v", "error", *args], capture_output=True, text=True, check=True)
    return out.stdout.split()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--seconds", type=float, default=120)
    parser.add_argument("--fps", default="24", help="e.g. 24 or 24000/1001")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    fps = Fraction(args.fps)
    total = int(args.seconds * fps)
    assert total < (1 << INDEX_BITS), "barcode holds 12 bits of frame index"
    rng = np.random.default_rng(args.seed)
    starts = shot_starts(total, rng)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    video = args.out_dir / "test.mkv"

    encode = subprocess.Popen([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{WIDTH}x{HEIGHT}", "-r", args.fps, "-i", "-",
        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-shortest",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "12", "-bf", "0", "-g", "48",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "64k", str(video),
    ], stdin=subprocess.PIPE)
    shot = 0
    for n in range(total):
        while shot + 1 < len(starts) and starts[shot + 1] <= n:
            shot += 1
        encode.stdin.write(draw_frame(n, BACKGROUNDS[shot % 4]).tobytes())
    encode.stdin.close()
    if encode.wait() != 0:
        raise SystemExit("ffmpeg encode failed")

    frame_pts = [float(t) for t in probe(["-select_streams", "v:0", "-show_entries", "frame=pts_time",
                                          "-of", "default=nw=1:nk=1", str(video)])]
    start_time = float((probe(["-show_entries", "format=start_time", "-of", "default=nw=1:nk=1", str(video)]) or ["0"])[0])
    truth = {
        "video": video.name, "fps": args.fps, "frames": total, "width": WIDTH, "height": HEIGHT,
        "layout": {"block": BLOCK, "index_bits": INDEX_BITS, "check_bits": CHECK_BITS, "origin": list(ORIGIN),
                   "screen_scale": SCREEN_SCALE, "probe_screen_x": list(PROBE_SCREEN_X), "levels_gray": LEVELS_GRAY},
        "start_time": start_time, "frame_pts": frame_pts,
        "shot_starts": starts, "shot_levels": [k % len(LEVELS_GRAY) for k in range(len(starts))],
    }
    (args.out_dir / "truth.json").write_text(json.dumps(truth))
    print(f"{video}: {total} frames, {len(starts)} shots, {len(frame_pts)} frame timestamps, start {start_time}")


if __name__ == "__main__":
    main()
