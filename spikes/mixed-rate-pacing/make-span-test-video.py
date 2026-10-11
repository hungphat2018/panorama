"""Make the span test video for the mixed-refresh pacing spike.

One 2240x800 video that mpv shows at exactly 2x, fullscreen across both screens
(--fs-screen=all gives a 4480x1600 window over the 2560x1600 laptop panel at
desktop x 0 and the 1920x1080 external at x 2560). Every frame carries its own
frame index as a barcode twice, at the same height: once on the laptop just left
of the seam and once on the external just right of it, so a per-screen capture
(or a camera) can tell which frame each screen showed. Two bands of white bars
move right across the seam (fast and slow) for viewing by eye.

Usage: python make-span-test-video.py OUT_DIR [--seconds 60] [--fps 24000/1001]
Writes OUT_DIR/span.mkv and OUT_DIR/span-truth.json.
"""
import argparse
import json
import subprocess
from fractions import Fraction
from pathlib import Path

import numpy as np

WIDTH, HEIGHT = 2240, 800       # shown at 2x: 4480x1600 desktop pixels
SCALE = 2
SEAM_X = 1280                   # source x of the laptop/external boundary (desktop x 2560)
VISIBLE_H_RIGHT = 540           # the 1080-row external shows source rows 0..539 only
BLOCK = 16                      # barcode block edge, source pixels
INDEX_BITS, CHECK_BITS = 12, 4  # frame index + popcount check, as in the exact-frame spike
BARCODE_Y = 16
BARCODES = {"laptop": SEAM_X - 16 - (INDEX_BITS + CHECK_BITS) * BLOCK, "external": SEAM_X + 16}
BANDS = [  # (top, bottom, source px moved per frame): 40 and 10 desktop px per frame
    (120, 260, 20),
    (300, 440, 5),
]
BAR_PERIOD, BAR_WIDTH = 64, 8
BACKGROUND, BAND_BG = 40, 20


def barcode_bits(n):
    check = bin(n).count("1") % (1 << CHECK_BITS)
    index = [(n >> (INDEX_BITS - 1 - i)) & 1 for i in range(INDEX_BITS)]
    return index + [(check >> (CHECK_BITS - 1 - i)) & 1 for i in range(CHECK_BITS)]


def draw_frame(n):
    frame = np.full((HEIGHT, WIDTH, 3), BACKGROUND, dtype=np.uint8)
    xs = np.arange(WIDTH)
    for top, bottom, step in BANDS:
        on = ((xs - n * step) % BAR_PERIOD) < BAR_WIDTH
        frame[top:bottom] = BAND_BG
        frame[top:bottom, on] = 235
    for x0 in BARCODES.values():
        for i, bit in enumerate(barcode_bits(n)):
            x = x0 + i * BLOCK
            frame[BARCODE_Y:BARCODE_Y + BLOCK, x:x + BLOCK] = 255 if bit else 0
    return frame


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--seconds", type=float, default=60)
    parser.add_argument("--fps", default="24000/1001", help="e.g. 24 or 24000/1001")
    args = parser.parse_args()

    fps = Fraction(args.fps)
    total = int(args.seconds * fps)
    assert total < (1 << INDEX_BITS), "barcode holds 12 bits of frame index"
    assert all(bottom <= VISIBLE_H_RIGHT for _, bottom, _ in BANDS), "bands must be visible on the external"
    args.out_dir.mkdir(parents=True, exist_ok=True)
    video = args.out_dir / "span.mkv"
    encode = subprocess.Popen([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{WIDTH}x{HEIGHT}", "-r", args.fps, "-i", "-",
        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-shortest",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "14", "-bf", "0", "-g", "48",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "64k", str(video),
    ], stdin=subprocess.PIPE)
    for n in range(total):
        encode.stdin.write(draw_frame(n).tobytes())
    encode.stdin.close()
    if encode.wait() != 0:
        raise SystemExit("ffmpeg encode failed")

    # Barcode block centres in desktop pixels; the logger maps them into each output.
    barcodes = {name: {"x": [(x0 + i * BLOCK + BLOCK // 2) * SCALE for i in range(INDEX_BITS + CHECK_BITS)],
                       "y": (BARCODE_Y + BLOCK // 2) * SCALE}
                for name, x0 in BARCODES.items()}
    truth = {"video": video.name, "fps": args.fps, "frames": total, "width": WIDTH, "height": HEIGHT,
             "scale": SCALE, "seam_desktop_x": SEAM_X * SCALE, "block_desktop": BLOCK * SCALE,
             "index_bits": INDEX_BITS, "check_bits": CHECK_BITS, "barcodes": barcodes,
             "bands_desktop_px_per_frame": [step * SCALE for _, _, step in BANDS]}
    (args.out_dir / "span-truth.json").write_text(json.dumps(truth, indent=1))
    print(f"{video}: {total} frames at {args.fps} fps")


if __name__ == "__main__":
    main()
