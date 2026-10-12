"""Analyze perf1 logs: runs the per-monitor-presenter analysis (same log format), then adds what perf1 records
on top: render time per monitor (CPU call, libplacebo GPU pass timers, split by new film frame vs repeat),
lateness of each present against its target refresh, missed refreshes, queue stalls.

Two seam offsets are reported, both second monitor minus first (monitor-1 minus monitor-0 by output index):
"blank-start" (the screen times as logged: DXGI marks the start of the vertical blank) and "row light" (each
screen time snapped to the monitor's top-row grid and moved down to the row at one physical height, plus the
panel lag) at the top, aim and bottom rows of the shared height. See analyze-per-monitor-pacing.py.

Usage: python -I analyze-perf1.py LOG_DIR [--mode nearest|own] [--skip-seconds 1]
"""
import argparse
import importlib.util
import json
import statistics
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "pacing", HERE.parent / "per-monitor-presenter" / "analyze-per-monitor-pacing.py")
pacing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pacing)


def pct(v, q):
    v = sorted(v)
    return v[min(len(v) - 1, max(0, int(q * len(v) - 1e-9)))] if v else float("nan")


def stats(v):
    return {"n": len(v), "p50": round(pct(v, .5), 3), "p99": round(pct(v, .99), 3), "max": round(max(v), 3)} if v else {}


def extras(log, skip_s):
    t_skip = log["t0"] + skip_s
    pres = log["presents"]
    cpu, gpu, lead = log["render_cpu_ms"], log["render_gpu_ms"], log["lead_ms"]
    period = statistics.median(b - a for a, b in zip(log["vblanks"], log["vblanks"][1:]))
    keep = [i for i, p in enumerate(pres) if p[2] >= t_skip]
    new = {i for i in keep if i and pres[i][4] != pres[i - 1][4]}
    out = {
        "render_cpu_ms_all": stats([cpu[i] for i in keep]),
        "render_cpu_ms_new_frame": stats([cpu[i] for i in keep if i in new]),
        "render_gpu_ms_all": stats([gpu[i] for i in keep]),
        "render_gpu_ms_new_frame": stats([gpu[i] for i in keep if i in new]),
        "lead_ms_before_target": stats([lead[i] for i in keep]),
        "present_calls_per_refresh": round(len(keep) / max(1e-9, (pres[keep[-1]][2] - pres[keep[0]][2]) / period), 3) if len(keep) > 1 else None,
        "queue_more": log["perf1"]["queue_more"], "empty_mix": log["perf1"]["empty_mix"],
        "render_failed": log["perf1"]["render_failed"],
    }
    # screen time vs target: only for presents that DXGI reported statistics for
    shown = {}
    for pc, _pr, _sr, qpc, _p in log["stats"]:
        shown.setdefault(pc, qpc)
    late, n, miss = [], 0, 0
    prev = None
    for i in keep:
        pid, _c, _r, target, _f = pres[i]
        if pid in shown:
            late.append((shown[pid] - target) * 1e3)
            n += 1
            if prev is not None and pid == prev[0] + 1:
                miss += max(0, round((shown[pid] - prev[1]) / period) - 1)
            prev = (pid, shown[pid])
    out["screen_minus_target_ms"] = stats(late) if late else {}
    if late:
        out["presents_with_stats"] = n
        out["landed_off_target_refresh"] = sum(1 for x in late if abs(x - statistics.median(late)) > 0.5 * period * 1e3)
        out["missed_refreshes_between_stat_presents"] = miss
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("log_dir", type=Path)
    ap.add_argument("--mode", default="nearest")
    ap.add_argument("--skip-seconds", type=float, default=1.0)
    a = ap.parse_args()
    logs = [json.loads(p.read_text()) for p in sorted(a.log_dir.glob(f"monitor-*-{a.mode}.json"))]
    base = []
    for log in logs:
        row = pacing.summarize(log, a.skip_seconds)
        row.pop("first_shown")
        row["perf1"] = extras(log, a.skip_seconds)
        base.append(row)
    pacing_first = [pacing.summarize(l, a.skip_seconds)["first_shown"] for l in logs]
    print(f"film {logs[0]['fps']:.4f} fps, locked={logs[0]['perf1']['locked']}, windowed={logs[0]['perf1']['windowed']}")
    for r in base:
        print(json.dumps(r))
    result = None
    if len(pacing_first) >= 2:
        a0, b0 = pacing_first[0], pacing_first[1]
        off = sorted((b0[n] - a0[n]) * 1e3 for n in a0 if n in b0)
        if off:
            print("blank-start frame-change offset ms (monitor1 - monitor0):", {"frames": len(off), "min": round(off[0], 2),
                  "median": round(pct(off, .5), 2), "p95": round(pct(off, .95), 2), "max": round(off[-1], 2)})
        result = pacing.row_offsets(logs, pacing_first)
        for line in pacing.row_offset_lines(result):
            print(line)
    (a.log_dir / "perf1-summary.json").write_text(json.dumps(base, indent=1))
    if result is not None:  # a separate file, so perf1-summary.json keeps its list-of-monitors shape
        (a.log_dir / "perf1-row-offsets.json").write_text(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
