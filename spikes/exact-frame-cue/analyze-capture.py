"""Score a screen capture from the exact-frame cue spike against truth.json.

For every captured screen update it reads the frame-index barcode (which source
frame was on screen) and the stripe's gray level (which cue the performer applied
to that frame). Every captured update of a frame must show that frame's shot level.
A cut is exact when both its last old and first new frame were captured and no
frame within 6 frames of it showed the wrong level. For cuts crossed in normal
playback it also measures when the new level appeared relative to the first frame
of the new shot (negative = early).

Usage: python analyze-capture.py TRUTH_JSON CAPTURE_MKV [--report OUT_JSON]
"""
import argparse
import json
import subprocess
from bisect import bisect_right
from collections import defaultdict
from pathlib import Path

import numpy as np

WINDOW = 6  # frames either side of a cut that count as "at the cut"


def ffprobe(path, entries):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", entries,
                          "-of", "default=nw=1:nk=1", str(path)], capture_output=True, text=True, check=True)
    return out.stdout.split()


def capture_frames(path):
    """Decode the capture with ffmpeg and yield (time, RGB frame)."""
    width, height = map(int, ffprobe(path, "stream=width,height")[:2])
    times = [float(t) for t in ffprobe(path, "frame=pts_time")]
    decoder = subprocess.Popen(["ffmpeg", "-v", "error", "-i", str(path), "-fps_mode", "passthrough",
                                "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
    size = width * height * 3
    for t in times:
        buf = decoder.stdout.read(size)
        if len(buf) < size:
            break
        yield t, np.frombuffer(buf, np.uint8).reshape(height, width, 3)
    decoder.stdout.close()
    decoder.wait()


def read_index(frame, layout):
    """Frame index from the barcode, or None when the check bits disagree."""
    scale, block = layout["screen_scale"], layout["block"]
    x0, y0 = layout["origin"]
    cy = int((y0 + block / 2) * scale)
    bits = []
    for i in range(layout["index_bits"] + layout["check_bits"]):
        cx = int((x0 + i * block + block / 2) * scale)
        bits.append(int(frame[cy - 3:cy + 4, cx - 3:cx + 4, 1].mean() > 128))
    index = int("".join(map(str, bits[:layout["index_bits"]])), 2)
    check = int("".join(map(str, bits[layout["index_bits"]:])), 2)
    return index if check == bin(index).count("1") % (1 << layout["check_bits"]) else None


def read_level(frame, layout):
    """(level, gray) of the painted stripe; level is None when the gray matches no level."""
    x0, x1 = layout["probe_screen_x"]
    gray = float(frame[8:-8, x0 + 8:x1 - 8, 1].mean()) / 255
    grays = layout["levels_gray"]
    level = int(np.argmin([abs(gray - g) for g in grays]))
    return (level if abs(gray - grays[level]) < 0.07 else None), gray


def landing_delays(order, starts, expected):
    """Delay (ms) from the first capture of a new shot to the first capture of its level."""
    delays = []
    for j in range(1, len(order)):
        _, prev_index, _ = order[j - 1]
        t_cross, index, _ = order[j]
        if not 0 < index - prev_index <= 3:
            continue  # not normal playback (seek, repeat or gap)
        for s in starts[bisect_right(starts, prev_index):bisect_right(starts, index)]:
            new = expected(s)
            early = [t for t, n, lv in reversed(order[max(0, j - 60):j]) if s - WINDOW <= n < s and lv == new]
            if early:
                delays.append(round((min(early) - t_cross) * 1000, 1))
                continue
            late = next((t for t, n, lv in order[j:j + 60] if s <= n < s + WINDOW and lv == new), None)
            if late is not None:
                delays.append(round((late - t_cross) * 1000, 1))
    return delays


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("truth", type=Path)
    parser.add_argument("capture", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    truth = json.loads(args.truth.read_text())
    layout, starts, levels = truth["layout"], truth["shot_starts"], truth["shot_levels"]

    def expected(n):
        return levels[bisect_right(starts, n) - 1]

    seen, order, gray_by_level, unreadable = defaultdict(list), [], defaultdict(list), 0
    for t, frame in capture_frames(args.capture):
        index = read_index(frame, layout)
        level, gray = read_level(frame, layout)
        if index is None or level is None or index >= truth["frames"]:
            unreadable += 1
            continue
        seen[index].append(level)
        order.append((t, index, level))
        gray_by_level[level].append(gray)

    wrong = {n: lv for n, lv in seen.items() if any(level != expected(n) for level in lv)}
    near_cut = {n for s in starts[1:] for n in range(s - WINDOW, s + WINDOW)}
    cuts = []
    for s in starts[1:]:
        if (s - 1) not in seen or s not in seen:
            continue
        nearby = [n for n in wrong if s - WINDOW <= n < s + WINDOW]
        cuts.append({"cut_frame": s, "early_frames": sum(n < s for n in nearby),
                     "late_frames": sum(n >= s for n in nearby)})
    non_exact = [c for c in cuts if c["early_frames"] or c["late_frames"]]
    delays = landing_delays(order, starts, expected)
    report = {
        "captures": len(order) + unreadable, "unreadable_captures": unreadable,
        "frames_seen": len(seen), "frames_total": truth["frames"],
        "cuts_total": len(starts) - 1, "cuts_observed": len(cuts), "cuts_exact": len(cuts) - len(non_exact),
        "wrong_frames": len(wrong),
        "wrong_frames_away_from_cuts": sorted(n for n in wrong if n not in near_cut),
        "landing_delay_ms": {"count": len(delays), "min": min(delays, default=None),
                             "median": float(np.median(delays)) if delays else None,
                             "max": max(delays, default=None), "nonzero": sum(d != 0 for d in delays)},
        "stripe_gray_by_level": {lv: [round(min(g), 3), round(max(g), 3)] for lv, g in sorted(gray_by_level.items())},
        "non_exact_cuts": non_exact[:40],
        "wrong_detail": {n: {"expected": expected(n), "shown": sorted(set(lv))} for n, lv in sorted(wrong.items())[:40]},
    }
    text = json.dumps(report, indent=1)
    if args.report:
        args.report.write_text(text)
    print(text)


if __name__ == "__main__":
    main()
