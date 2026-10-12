"""Summarize a PresentMon CSV for one process: how did each swapchain reach the screen?

PresentMon (Intel, MIT licence, signed) reads the Windows present events through ETW. It needs administrator rights
OR membership of the "Performance Log Users" group; the user of this PC is in that group, so it runs unelevated.
Its "PresentMode" column is the answer to "did this window reach independent flip?":
  Hardware: Independent Flip      the picture goes straight to the display, the compositor is not involved
  Hardware Composed: Independent Flip / Hardware: Legacy Flip   overlay-plane or legacy hardware paths
  Composed: Flip                  the desktop compositor (DWM) draws the window into its own frame
  Composed: Copy with GPU GDI ... copy paths

Usage: python -I summarize-presentmon.py presentmon.csv
"""
import csv
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.dont_write_bytecode = True


def num(row, key):
    try:
        return float(row[key])
    except (KeyError, ValueError):
        return None


def pct(v, q):
    v = sorted(v)
    return v[min(len(v) - 1, max(0, int(q * len(v) - 1e-9)))] if v else float("nan")


def summarize(path):
    groups = defaultdict(list)
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            key = (row.get("Application", "?"), row.get("SwapChainAddress", "?"), row.get("VidPnSourceId", ""), row.get("LayerIndex", ""))
            groups[key].append(row)
    out = []
    for (app, chain, src, layer), rows in sorted(groups.items()):
        modes = Counter(r.get("PresentMode", "?") for r in rows)
        runtimes = Counter(r.get("PresentRuntime", "?") for r in rows)
        sync = Counter(r.get("SyncInterval", "?") for r in rows)
        tearing = Counter(r.get("AllowsTearing", "?") for r in rows)
        line = {"application": app, "swapchain": chain, "vidpn_source": src, "layer": layer, "presents": len(rows),
                "present_mode": dict(modes), "runtime": dict(runtimes), "sync_interval": dict(sync), "allows_tearing": dict(tearing)}
        for key, label in (("FrameTime", "frame_time_ms"), ("DisplayedTime", "displayed_time_ms"),
                           ("DisplayLatency", "display_latency_ms"), ("CPUStartTime", None)):
            vals = [num(r, key) for r in rows]
            vals = [v for v in vals if v is not None and v == v]
            if label and vals:
                line[label] = {"p50": round(pct(vals, .5), 3), "p99": round(pct(vals, .99), 3), "max": round(max(vals), 3)}
        out.append(line)
    return out


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    path = Path(sys.argv[1])
    if not path.exists():
        print(f"{path}: no PresentMon output (the trace did not match the process, or PresentMon could not start a session)")
        return 1
    lines = summarize(path)
    if not lines:
        print(f"{path}: PresentMon wrote no presents for the process (matched by name; unelevated, short-lived or unnamed processes may show as <unknown>)")
        return 1
    for line in lines:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
