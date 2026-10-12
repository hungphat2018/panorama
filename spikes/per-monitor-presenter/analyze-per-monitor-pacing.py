"""Analyze per-monitor-presenter logs: pace on each monitor and the frame-change offset across the seam.

For each monitor, every present's screen time comes from DXGI frame statistics (the refresh at which
present N reached the screen). A present not seen in the statistics is placed on the refresh after it
returned (a fallback; counted separately). From the screen times:
  - refreshes per film frame on each monitor (even pace = one value)
  - when each film frame first reached each monitor, and the offset between monitors (the seam)
  - how often Windows reported the swapchain as composed vs overlay / independent flip

Two kinds of seam offset, both "second monitor minus first" (monitor-1 minus monitor-0 by output index):
  - blank-start: the screen times as logged. DXGI's SyncQPCTime and IDXGIOutput::WaitForVBlank mark the start of
    the vertical blank, just after a refresh's bottom row, so this compares the moments the screens START their
    blanks. The picture's top row comes one blank later (a blank differs per mode) and lower rows later still.
  - row light: for logs written by the row-timing presenters (fields timing, scan_grid, geometry). Each screen
    time is snapped to the monitor's top-row grid (the first top row at or after it), then moved down to the row
    at one physical height and by the panel lag, i.e. the moment that monitor lights that row:
        light = top_after(screen time) + (y - top_mm) / height_mm * scan_s + lag
    The offset is reported at the top row, the aim row and the bottom row of the height both screens share.
    Logs without those fields (older runs) still parse; they just have no row-light section.

Usage: python analyze-per-monitor-pacing.py LOG_DIR [--mode own|nearest|follow] [--skip-seconds 1]
"""
import argparse
import bisect
import json
import math
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


def describe(offsets_ms):
    """min / p5 / median / p95 / max of a list of offsets (ms), rounded like the original report."""
    offsets = sorted(offsets_ms)
    pick = lambda q: offsets[min(len(offsets) - 1, int(q * len(offsets)))]
    return {"frames": len(offsets), "min": round(offsets[0], 2), "p5": round(pick(0.05), 2),
            "median": round(pick(0.5), 2), "p95": round(pick(0.95), 2), "max": round(offsets[-1], 2)}


# ---- row light --------------------------------------------------------------------------------------

def scan_grid_of(log):
    """The top-row grid of a log: the end-of-run fit, else the film-start fit. None for logs without one."""
    for key in ("scan_grid", "scan_grid_at_start"):
        grid = log.get(key)
        if grid and grid.get("ok", True) and grid.get("period"):
            return grid
    return None


def has_rows(log):
    return scan_grid_of(log) is not None and "timing" in log and "geometry" in log


def top_after(grid, t):
    """First top row at or after t on a logged grid (scanline-clock.h scan_grid_top_after)."""
    return grid["base"] + math.ceil((t - grid["base"]) / grid["period"] - 1e-9) * grid["period"]


def top_nearest(grid, t):
    return grid["base"] + math.floor((t - grid["base"]) / grid["period"] + 0.5) * grid["period"]


def row_light(log, shown, y_mm):
    """When this monitor lights height y_mm of the picture whose refresh shows at `shown` (blank start)."""
    geo = log["geometry"]
    row_s = (y_mm - geo["top_mm"]) / geo["height_mm"] * log["timing"]["scan_s"]
    return top_after(scan_grid_of(log), shown) + row_s + geo["lag_ms"] * 1e-3


def seam_heights(logs):
    """(top, aim, bottom) in mm on the shared vertical axis: the band both screens cover and the aim row."""
    a = logs[0]
    if "shared_mm" in a:
        top, bottom = a["shared_mm"]
    else:
        spans = [(l["geometry"]["top_mm"], l["geometry"]["top_mm"] + l["geometry"]["height_mm"]) for l in logs[:2]]
        top, bottom = max(s[0] for s in spans), min(s[1] for s in spans)
    return top, a.get("aim_mm", 0.5 * (top + bottom)), bottom


def grid_checks(log):
    """Sanity numbers for the snapping: how far after a screen time the next top row comes, and how far the grid
    fitted at film start has drifted from the one fitted at the end (at the last present)."""
    grid = scan_grid_of(log)
    out = {}
    syncs = sorted({s[3] for s in log["stats"] if s[3] > 0})
    if syncs:
        d = sorted((top_after(grid, s) - s) * 1e3 for s in syncs)
        out["sync_to_top_ms"] = {"median": round(d[len(d) // 2], 3), "min": round(d[0], 3), "max": round(d[-1], 3)}
        # A statistic of a monitor that really flips in step with its own refreshes sits within its blank (plus a
        # little for the interrupt); one that doesn't (a composed window, presented on the main display's clock)
        # lands anywhere in the refresh, and its row-light offsets mean nothing.
        blank_ms = log["timing"]["blank_s"] * 1e3
        out["sync_in_blank_fraction"] = round(sum(1 for x in d if 0.0 <= x <= blank_ms + 0.15) / len(d), 3)
    wakes = log["vblanks"]
    if wakes:
        d = sorted((top_after(grid, w) - w) * 1e3 for w in wakes)
        out["wake_median_to_top_ms"] = round(d[len(d) // 2], 3)
    for key in ("wake_to_top_ms", "wake_to_top_end_ms", "live_refits"):
        if log.get(key) is not None:
            out[key] = log[key]
    start, end = log.get("scan_grid_at_start"), log.get("scan_grid")
    if start and end and start.get("ok") and end.get("ok") and log["presents"]:
        t = log["presents"][-1][2]
        out["grid_drift_us"] = round((top_nearest(start, t) - top_nearest(end, t)) * 1e6, 1)
    return out


def row_offsets(logs, firsts):
    """Seam offset in light, logs[1] minus logs[0], at the top, aim and bottom rows of the shared height.

    firsts: per log, {film frame: screen time it first reached that monitor} (summarize()["first_shown"]).
    The earliest frame of each monitor is dropped: the analysis skips the first seconds, so that frame's first
    retained present is usually not its first appearance. Returns None if either log has no row-timing fields.
    """
    if len(logs) < 2 or not (has_rows(logs[0]) and has_rows(logs[1])):
        return None
    a, b = logs[0], logs[1]
    fa, fb = firsts[0], firsts[1]
    drop = {min(fa, default=None), min(fb, default=None)}
    frames = sorted(n for n in fa if n in fb and n not in drop)
    top, aim, bottom = seam_heights(logs)
    out = {"second": b["name"], "first": a["name"], "master": a.get("master"),
           "heights_mm": {"top": top, "aim": aim, "bottom": bottom}, "offset_ms": {},
           "windowed": bool(a.get("windowed") or a.get("perf1", {}).get("windowed")),
           "checks": {a["name"]: grid_checks(a), b["name"]: grid_checks(b)}}
    if "predicted_ms" in a:
        out["predicted_ms"] = a["predicted_ms"]
    if not frames:
        return out
    for label, y in (("top", top), ("aim", aim), ("bottom", bottom)):
        out["offset_ms"][label] = describe([(row_light(b, fb[n], y) - row_light(a, fa[n], y)) * 1e3 for n in frames])
    return out


def row_offset_lines(result):
    """Printable lines for a row_offsets() result."""
    if result is None:
        return ["row-light offset: no row-timing fields in these logs (run a presenter with row timing)"]
    names = f"{result['second']} - {result['first']}"
    lines = [f"row-light seam offset {names} (ms), master monitor {result['master']}; frames paired: "
             f"{next(iter(result['offset_ms'].values()), {}).get('frames', 0)}"]
    for label in ("top", "aim", "bottom"):
        mm = result["heights_mm"][label]
        stats = result["offset_ms"].get(label)
        predicted = result.get("predicted_ms", {}).get(label)
        pred = f"   predicted {predicted:+.2f}" if predicted is not None else ""
        if stats is None:
            lines.append(f"  {label:6s} row ({mm:6.1f} mm): no frame shown on both monitors{pred}")
        else:
            lines.append(f"  {label:6s} row ({mm:6.1f} mm): median {stats['median']:+.2f}  p5 {stats['p5']:+.2f}  "
                         f"p95 {stats['p95']:+.2f}  min {stats['min']:+.2f}  max {stats['max']:+.2f}{pred}")
    for name, check in result["checks"].items():
        parts = []
        if "sync_to_top_ms" in check:
            s = check["sync_to_top_ms"]
            parts.append(f"DXGI SyncQPCTime to next top row {s['median']} ms (min {s['min']}, max {s['max']})")
        if "wake_to_top_ms" in check:
            end = f" at film start, {check['wake_to_top_end_ms']} at the end" if "wake_to_top_end_ms" in check else ""
            parts.append(f"vblank wake-up to top row {check['wake_to_top_ms']} ms{end} "
                         f"(median over the run's wake-ups {check.get('wake_median_to_top_ms')})")
        elif "wake_median_to_top_ms" in check:
            parts.append(f"vblank wake-up to top row {check['wake_median_to_top_ms']} ms (median over the run's wake-ups)")
        if "grid_drift_us" in check:
            refits = f", the film followed a grid refitted {check['live_refits']} times" if "live_refits" in check else ""
            parts.append(f"start-fit grid vs end-fit grid at the last present {check['grid_drift_us']:+.1f} us{refits}")
        lines.append(f"  {name}: " + "; ".join(parts))
        if check.get("sync_in_blank_fraction", 1.0) < 0.9:
            lines.append(f"  WARNING {name}: only {check['sync_in_blank_fraction'] * 100:.0f}% of DXGI's times sit within this "
                         f"monitor's blank before a top row (a composed window?): its row-light offsets are not meaningful")
    if result["windowed"]:
        lines.append("  (windowed run: a composed window is presented on the main display's clock, so the second "
                     "monitor's pacing and these offsets only check the plumbing)")
    return lines


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
            offsets = [(b[n] - a[n]) * 1000 for n in a if n in b]
            if offsets:
                # blank-start basis: the key keeps its old name (offset_ms) for older readers of the summary
                summary["offset_ms"] = describe(offsets)
                print(f"blank-start frame-change offset {rows[1]['name']} - {rows[0]['name']} (ms): {summary['offset_ms']}")
            result = row_offsets(logs, [r["first_shown"] for r in rows])
            for line in row_offset_lines(result):
                print(line)
            if result is not None:
                summary["row_offset"] = result
        report[mode] = summary
    out = args.log_dir / "pacing-summary.json"
    out.write_text(json.dumps(report, indent=1))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
