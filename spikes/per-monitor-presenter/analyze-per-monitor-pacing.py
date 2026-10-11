"""Analyze per-monitor-presenter logs: pace on each monitor and the frame-change offset across the seam.

For each monitor, every present's screen time comes from DXGI frame statistics (the refresh at which
present N reached the screen). A present not seen in the statistics is placed on the refresh after it
returned (a fallback; counted separately). From the screen times:
  - refreshes per film frame on each monitor (even pace = one value)
  - when each film frame first reached each monitor, and the offset between monitors (the seam)
  - how often Windows reported the swapchain as composed vs overlay / independent flip

Usage: python analyze-per-monitor-pacing.py LOG_DIR [--mode own|nearest|follow] [--skip-seconds 1]
"""
import argparse
import bisect
import json
import statistics
from collections import Counter
from pathlib import Path

MODES = {0: "composed", 1: "overlay (independent flip / MPO)", 2: "none", 3: "composition failure"}


def screen_times(log, skip_until):
    """Map present id -> refresh time it reached the screen, plus how many came from the fallback."""
    vblanks = log["vblanks"]
    shown = {}
    for present_count, _present_refresh, _sync_refresh, sync_qpc, _polled in log["stats"]:
        shown.setdefault(present_count, sync_qpc)
    times, fallback = {}, 0
    for pid, call, ret, target, frame in log["presents"]:
        if ret < skip_until:
            continue
        if pid in shown:
            times[pid] = (shown[pid], frame)
        else:
            i = bisect.bisect_right(vblanks, ret)
            if i < len(vblanks):
                times[pid] = (vblanks[i], frame)
                fallback += 1
    return times, fallback


def frame_changes(times):
    """First screen time of each film frame, and refreshes per frame from consecutive changes."""
    first = {}
    for t, frame in sorted(times.values()):
        first.setdefault(frame, t)
    return first


def summarize(log, skip_s):
    skip_until = log["t0"] + skip_s
    period = statistics.median(b - a for a, b in zip(log["vblanks"], log["vblanks"][1:]))
    times, fallback = screen_times(log, skip_until)
    first = frame_changes(times)
    frames = sorted(first)
    per_frame = Counter(round((first[b] - first[a]) / period) for a, b in zip(frames, frames[1:]) if b == a + 1)
    skipped = sum(1 for a, b in zip(frames, frames[1:]) if b != a + 1)
    return {"name": log["name"], "refresh_hz": round(1 / period, 4), "presents": len(times),
            "fallback_times": fallback, "refreshes_per_frame": dict(sorted(per_frame.items())),
            "skipped_frames": skipped, "first_shown": first,
            "composition": {MODES.get(int(k), k): v for k, v in log["composition_modes"].items()},
            "error": log.get("error", "")}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("log_dir", type=Path)
    parser.add_argument("--mode", default=None, help="which mode's logs to read (default: all found)")
    parser.add_argument("--skip-seconds", type=float, default=1.0, help="ignore the first seconds after film start")
    args = parser.parse_args()
    modes = [args.mode] if args.mode else sorted({p.stem.split("-", 2)[2] for p in args.log_dir.glob("monitor-*-*.json")})
    report = {}
    for mode in modes:
        logs = [json.loads(p.read_text()) for p in sorted(args.log_dir.glob(f"monitor-*-{mode}.json"))]
        if not logs:
            continue
        rows = [summarize(log, args.skip_seconds) for log in logs]
        print(f"\n== mode {mode}, film {logs[0]['fps']:.3f} fps, master monitor {logs[0]['master']}")
        for row in rows:
            print(f"{row['name']}: {row['refresh_hz']} Hz, {row['presents']} presents "
                  f"({row['fallback_times']} timed by fallback), refreshes per frame {row['refreshes_per_frame']}, "
                  f"frames skipped {row['skipped_frames']}, composition {row['composition']}"
                  + (f", ERROR {row['error']}" if row["error"] else ""))
        summary = {"monitors": [{k: v for k, v in r.items() if k != "first_shown"} for r in rows]}
        if len(rows) >= 2:
            a, b = rows[0]["first_shown"], rows[1]["first_shown"]
            offsets = sorted((b[n] - a[n]) * 1000 for n in a if n in b)
            if offsets:
                pick = lambda q: offsets[min(len(offsets) - 1, int(q * len(offsets)))]
                summary["offset_ms"] = {"frames": len(offsets), "min": round(offsets[0], 2), "p5": round(pick(0.05), 2),
                                        "median": round(pick(0.5), 2), "p95": round(pick(0.95), 2),
                                        "max": round(offsets[-1], 2)}
                print(f"frame-change offset {rows[1]['name']} - {rows[0]['name']} (ms): {summary['offset_ms']}")
        report[mode] = summary
    out = args.log_dir / "pacing-summary.json"
    out.write_text(json.dumps(report, indent=1))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
