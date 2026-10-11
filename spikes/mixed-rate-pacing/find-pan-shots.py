"""Find camera pans (the whole picture sliding) in a video, for the 24 fps vs smooth A/B.

Decodes the video small and grey and, for every pair of neighbouring frames,
measures the shift between them (phase correlation) and how much of the change
that one shift explains (the difference left after shifting). A pan is a run
where the picture keeps sliding one way and the shift explains the change. Anime
often moves a background only every 2nd or 3rd frame, so up to two held frames
inside a run are allowed. A cut ends a run.

Usage: python find-pan-shots.py VIDEO OUT_DIR [--width 480] [--min-seconds 2.5]
Writes OUT_DIR/pairs.npz (per-pair measurements; reused when present) and
OUT_DIR/pans.json (every run found, best first), and prints the best ones.
"""
import argparse
import json
import math
import subprocess
from fractions import Fraction
from pathlib import Path

import cv2
import numpy as np

MARGIN = 24        # pixels ignored at the border (shifted-in content)
CUT_DIFF = 20.0    # mean grey difference above which a pair the shift cannot explain is a cut
HELD_DIFF = 0.8    # below this the two frames are the same drawing (compression noise only)
MIN_SHIFT = 0.2    # pixels per frame at --width (480 px: about 0.8 px at 1080p)
MIN_PEAK = 0.15    # phase-correlation peak strength
LEFT_ABS, LEFT_REL = 1.0, 0.35  # difference left after shifting: absolute, or relative to before
MAX_HELD = 2


def grey_frames(video, w, h):
    cmd = ["ffmpeg", "-v", "error", "-hwaccel", "d3d11va", "-i", str(video), "-map", "0:v:0",
           "-vf", f"scale={w}:{h}:flags=area,format=gray", "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    while True:
        buf = proc.stdout.read(w * h)
        if len(buf) < w * h:
            break
        yield np.frombuffer(buf, np.uint8).reshape(h, w).astype(np.float32)
    proc.wait()


def measure_pairs(video, w, h):
    """Columns: dx, dy, peak, diff before shift, diff after shift, frame contrast."""
    win = cv2.createHanningWindow((w, h), cv2.CV_32F)
    core = (slice(MARGIN, -MARGIN), slice(MARGIN, -MARGIN))
    rows, prev = [], None
    for n, cur in enumerate(grey_frames(video, w, h)):
        if prev is not None:
            # Copies: when the size is already FFT-friendly, phaseCorrelate applies the window in place.
            (dx, dy), peak = cv2.phaseCorrelate(prev.copy(), cur.copy(), win)
            moved = cv2.warpAffine(prev, np.float32([[1, 0, dx], [0, 1, dy]]), (w, h), flags=cv2.INTER_LINEAR,
                                   borderMode=cv2.BORDER_REPLICATE)
            rows.append((dx, dy, peak, np.abs(cur - prev)[core].mean(), np.abs(cur - moved)[core].mean(),
                         cur[core].std()))
        prev = cur
        if n % 2400 == 0:
            print(f"  frame {n}", flush=True)
    return np.array(rows, np.float64)


def find_runs(pairs, fps, scale, min_frames):
    """Runs of one-way sliding; returns dicts with frame range and summary numbers."""
    runs, cur, held = [], None, 0
    for i, (dx, dy, peak, before, after, contrast) in enumerate(pairs):
        slides = math.hypot(dx, dy) >= MIN_SHIFT and peak >= MIN_PEAK and after <= max(LEFT_ABS, LEFT_REL * before)
        is_held = before < HELD_DIFF
        if cur is not None:
            if slides and dx * cur["dx"] + dy * cur["dy"] > 0:
                cur.update(end=i + 1, dx=cur["dx"] + dx, dy=cur["dy"] + dy, moving=cur["moving"] + 1)
                cur["left"].append(after)
                held = 0
                continue
            if is_held and held < MAX_HELD:
                held += 1
                continue
            runs.append(cur)
            cur = None
        if slides and not (before > CUT_DIFF and after > 0.6 * before):
            cur, held = {"start": i, "end": i + 1, "dx": dx, "dy": dy, "moving": 1, "left": [after]}, 0
    if cur is not None:
        runs.append(cur)
    out = []
    for r in runs:
        frames = r["end"] - r["start"]
        if frames < min_frames:
            continue
        seconds = frames / fps
        dist = math.hypot(r["dx"], r["dy"]) * scale
        contrast = float(pairs[r["start"]:r["end"], 5].mean())
        purity = float(np.median(r["left"]))
        out.append({"start_frame": r["start"], "end_frame": r["end"], "start_s": round(r["start"] / fps, 3),
                    "seconds": round(seconds, 2), "speed_px_s_1080p": round(dist / seconds, 1),
                    "direction_deg": round(math.degrees(math.atan2(r["dy"], r["dx"])), 1),
                    "moving_share": round(r["moving"] / frames, 2), "left_after_shift": round(purity, 3),
                    "contrast": round(contrast, 1),
                    "score": round(seconds * contrast / (1 + 4 * purity), 1)})
    return sorted(out, key=lambda r: -r["score"])


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("video", type=Path)
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--width", type=int, default=480)
    parser.add_argument("--min-seconds", type=float, default=2.5)
    args = parser.parse_args()

    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=width,height,r_frame_rate", "-of", "default=nw=1:nk=1", str(args.video)],
                           capture_output=True, text=True, check=True).stdout.split()
    src_w, src_h, fps = int(probe[0]), int(probe[1]), float(Fraction(probe[2]))
    w = args.width
    h = round(src_h * w / src_w / 2) * 2
    args.out_dir.mkdir(parents=True, exist_ok=True)
    cache = args.out_dir / "pairs.npz"
    if cache.exists():
        pairs = np.load(cache)["pairs"]
    else:
        pairs = measure_pairs(args.video, w, h)
        np.savez_compressed(cache, pairs=pairs)
    runs = find_runs(pairs, fps, src_w / w, int(args.min_seconds * fps))
    (args.out_dir / "pans.json").write_text(json.dumps({"video": str(args.video), "fps": fps, "pairs": len(pairs),
                                                        "runs": runs}, indent=1))
    print(f"{len(pairs)} frame pairs, {len(runs)} pans of {args.min_seconds}+ s")
    for r in runs[:15]:
        m, s = divmod(r["start_s"], 60)
        print(f"  {int(m):02d}:{s:05.2f}  {r['seconds']:5.2f} s  {r['speed_px_s_1080p']:6.1f} px/s  "
              f"dir {r['direction_deg']:6.1f}  moving {r['moving_share']:.2f}  left {r['left_after_shift']:.2f}  "
              f"contrast {r['contrast']:5.1f}  score {r['score']}")


if __name__ == "__main__":
    main()
