"""Analyze vk-timing-probe logs: what does VK_EXT_present_timing report, and how does it relate to the monitor's own clocks?

Input: probe-<DISPLAY>.json files written by vk-timing-probe (one per monitor). For every experiment it prints

  - pace: presents per second against the monitor's refresh rate (a composed window on a second monitor runs at the
    main display's rate; a window with its own flip runs at its own), and the present-call intervals
  - latency: how long after vkQueuePresentKHR returned the picture's first pixel went out
  - first pixel out against the monitor: the offset to the nearest top row of the scan-line grid (D3DKMTGetScanLine fit),
    to the vblank start (top row minus the blank) and to the WaitForVBlank wake-ups. A median near 0 against one of them
    says which instant the extension reports.
  - what the waits do: when vkAcquireNextImageKHR and vkWaitForPresent return against the grid and against the report
  - targets: for a relative target of k refreshes, the gap between consecutive pictures in refreshes (honoured = k)

Times are QPC seconds. Stage times from a swapchain-local or stage-local time domain are converted with the probe's
calibrated reads (vkGetCalibratedTimestampsKHR, QPC against the domain), linearly interpolated between reads. A QPC
domain needs no calibration, but whether its raw values count QPC ticks or nanoseconds is not assumed: the unit that puts
the reports next to the present calls wins and is printed ("qpc unit").

Usage: python -I analyze-vk-timing.py LOG_DIR [--skip 0.3]
"""
import argparse
import bisect
import json
import math
import statistics
import sys
from pathlib import Path

sys.dont_write_bytecode = True

STAGE_QUEUE_END = 0x1
STAGE_DEQUEUED = 0x2
STAGE_FIRST_PIXEL_OUT = 0x4
STAGE_FIRST_PIXEL_VISIBLE = 0x8
DOMAIN_QPC = 3


def pct(values, q):
    v = sorted(values)
    return v[min(len(v) - 1, max(0, int(q * len(v) - 1e-9)))] if v else float("nan")


def describe(values, digits=3):
    """n / p5 / median / p95 / min / max of a list, rounded; {} for an empty list."""
    if not values:
        return {}
    return {"n": len(values), "min": round(min(values), digits), "p5": round(pct(values, .05), digits),
            "p50": round(pct(values, .5), digits), "p95": round(pct(values, .95), digits), "max": round(max(values), digits)}


class Grid:
    """A fitted line of top rows: row k at base + k * period."""

    def __init__(self, base, period):
        self.base, self.period = base, period

    def nearest(self, t):
        return self.base + math.floor((t - self.base) / self.period + 0.5) * self.period


def nearest_in(sorted_times, t):
    """The element of a sorted list nearest to t (None for an empty list)."""
    if not sorted_times:
        return None
    i = bisect.bisect_left(sorted_times, t)
    best = [sorted_times[j] for j in (i - 1, i) if 0 <= j < len(sorted_times)]
    return min(best, key=lambda x: abs(x - t))


def qpc_domain_scale(pairs, qpc_freq):
    """Seconds per raw unit of a report in the QPC time domain: (scale, "ticks" | "ns" | "unknown").

    The raw value may count QPC ticks (1 / qpc_freq s each) or nanoseconds; which one the extension uses is something the
    probe is there to find out, not to assume. `pairs` holds (raw report, QPC seconds of the present call it belongs to):
    a stage time is within a few frames of its present call, so the unit that gives the smaller median distance wins (the
    wrong one is off by orders of magnitude)."""
    best = None
    for name, scale in (("ticks", 1.0 / qpc_freq), ("ns", 1e-9)):
        dist = sorted(abs(raw * scale - call) for raw, call in pairs)
        if dist and (best is None or dist[len(dist) // 2] < best[0]):
            best = (dist[len(dist) // 2], name, scale)
    if best is None:
        return 1.0 / qpc_freq, "unknown"
    return best[2], best[1]


def make_converter(exp, qpc_freq, qpc_scale=None):
    """raw stage time -> QPC seconds, for one experiment. None when nothing can convert.
    qpc_scale: seconds per raw unit when the domain is QPC (default: QPC ticks)."""
    domain = exp.get("domain")
    if domain == DOMAIN_QPC:
        scale = qpc_scale if qpc_scale else 1.0 / qpc_freq
        return lambda raw, stage, polled: raw * scale
    cals = exp.get("calibrations") or []
    if not cals:
        return None
    by_stage = {}
    for qpc_s, local, dev, stage in cals:
        by_stage.setdefault(stage, []).append((qpc_s, local))
    for pts in by_stage.values():
        pts.sort()

    def convert(raw, stage, polled):
        # a stage-local domain has one calibration series per stage; a swapchain-local one has a single series (stage 0)
        pts = by_stage.get(stage) or by_stage.get(0) or next(iter(by_stage.values()))
        if len(pts) == 1:
            q0, l0 = pts[0]
            return q0 + (raw - l0) * 1e-9
        locals_ = [p[1] for p in pts]
        lo = max(0, min(bisect.bisect_left(locals_, raw) - 1, len(pts) - 2))
        (qa, la), (qb, lb) = pts[lo], pts[lo + 1]
        if lb == la:
            return qa + (raw - la) * 1e-9
        return qa + (raw - la) * (qb - qa) / (lb - la)

    return convert


def clock_ratio(exp):
    """Rate of the swapchain-local clock against QPC over the run (1.0 = same rate, ticks are nanoseconds)."""
    cals = exp.get("calibrations") or []
    if not cals:
        return None
    same = [c for c in cals if c[3] == cals[0][3]]
    if len(same) < 2:
        return None
    (qa, la, _, _), (qb, lb, _, _) = same[0], same[-1]
    if qb <= qa:
        return None
    return ((lb - la) * 1e-9) / (qb - qa)


def analyze_experiment(log, exp, grid, vblanks, skip_s):
    period = grid.period if grid else None
    out = {"name": exp["name"], "status": exp.get("status", ""), "api": exp["api"], "timing": exp["timing"],
           "target_refreshes": exp["target_refreshes"], "target": exp["target"], "nearest": exp["nearest"]}
    fields = exp["present_fields"]
    all_rows = [dict(zip(fields, r)) for r in exp["presents"]]
    t_skip = exp["t_start"] + skip_s
    rows = [r for r in all_rows if r["call"] >= t_skip]
    if len(rows) < 3:
        out["note"] = "too few presents"
        return out
    calls = [r["call"] for r in rows]
    rets = [r["ret"] for r in rows]
    span = calls[-1] - calls[0]
    refresh_hz = log["timing"]["refresh_hz"]
    out["presents"] = len(rows)
    out["presents_per_s"] = round((len(rows) - 1) / span, 2)
    out["refresh_hz"] = round(refresh_hz, 3)
    out["presents_per_refresh"] = round((len(rows) - 1) / span / refresh_hz, 3)
    out["present_interval_ms"] = describe([(b - a) * 1e3 for a, b in zip(calls, calls[1:])])
    out["present_call_ms"] = describe([(r["ret"] - r["call"]) * 1e3 for r in rows])
    out["acquire_ms"] = describe([(r["acq_ret"] - r["acq_call"]) * 1e3 for r in rows])
    waits = [r for r in rows if r["wait_call"] > 0]
    if waits:
        out["wait_ms"] = describe([(r["wait_ret"] - r["wait_call"]) * 1e3 for r in waits])
        out["wait_results"] = {}
        for r in waits:
            out["wait_results"][str(r["wait_result"])] = out["wait_results"].get(str(r["wait_result"]), 0) + 1
    out["present_results"] = {}
    for r in rows:
        out["present_results"][str(r["result"])] = out["present_results"].get(str(r["result"]), 0) + 1

    if grid:
        # where do the waits and acquires return, against the top-row grid (offset = time since the nearest top row)
        out["acquire_return_vs_top_ms"] = describe([(r["acq_ret"] - grid.nearest(r["acq_ret"])) * 1e3 for r in rows])
        if waits:
            out["wait_return_vs_top_ms"] = describe([(r["wait_ret"] - grid.nearest(r["wait_ret"])) * 1e3 for r in waits])
        out["present_call_vs_top_ms"] = describe([(r["call"] - grid.nearest(r["call"])) * 1e3 for r in rows])

    # feedback
    scale = None
    if exp["timing"] and exp.get("domain") == DOMAIN_QPC:
        call_of = {r["id"]: r["call"] for r in all_rows}
        pairs = [(raw, call_of[f[0]]) for f in exp["feedback"] if f[0] in call_of for _, raw in f[6] if raw]
        scale, out["qpc_unit"] = qpc_domain_scale(pairs, log["qpc_freq"])
    convert = make_converter(exp, log["qpc_freq"], scale)
    fb = {}
    if exp["timing"] and convert:
        for pid, polled, target_raw, domain, domain_id, complete, stages in exp["feedback"]:
            if pid in fb:
                continue
            st = {}
            for stage, raw in stages:
                if raw:
                    st[stage] = convert(raw, stage if domain != DOMAIN_QPC else 0, polled)
            fb[pid] = {"stages": st, "polled": polled, "complete": complete, "target_raw": target_raw}
    out["feedback_results"] = len(fb)
    if exp["timing"]:
        out["feedback_complete"] = sum(1 for v in fb.values() if v["complete"])
        out["refresh_duration_ms"] = round(exp["refresh_duration_ns"] / 1e6, 5)
        out["refresh_interval_ms"] = (None if exp["refresh_interval_ns"] in (0, 2 ** 64 - 1) else round(exp["refresh_interval_ns"] / 1e6, 5))
        out["refresh_interval_raw"] = exp["refresh_interval_ns"]
        ratio = clock_ratio(exp)
        out["domain_clock_vs_qpc"] = None if ratio is None else round(ratio, 7)
    by_id = {r["id"]: r for r in rows}
    fpo_pairs = []  # (id, first pixel out, row)
    for pid, v in sorted(fb.items()):
        if pid in by_id and STAGE_FIRST_PIXEL_OUT in v["stages"]:
            fpo_pairs.append((pid, v["stages"][STAGE_FIRST_PIXEL_OUT], by_id[pid]))
    if fpo_pairs and grid:
        blank_ms = log["timing"]["blank_s"] * 1e3
        out["blank_ms"] = round(blank_ms, 3)
        out["fpo_minus_top_ms"] = describe([(t - grid.nearest(t)) * 1e3 for _, t, _ in fpo_pairs])
        out["fpo_minus_vblank_start_ms"] = describe([(t - (grid.nearest(t) - log["timing"]["blank_s"])) * 1e3 for _, t, _ in fpo_pairs])
        wake = nearest_in(vblanks, fpo_pairs[0][1])
        if wake is not None:
            out["fpo_minus_wakeup_ms"] = describe([(t - nearest_in(vblanks, t)) * 1e3 for _, t, _ in fpo_pairs])
        out["fpo_minus_call_ms"] = describe([(t - r["call"]) * 1e3 for _, t, r in fpo_pairs])
        out["fpo_minus_ret_ms"] = describe([(t - r["ret"]) * 1e3 for _, t, r in fpo_pairs])
        med = out["fpo_minus_top_ms"]["p50"]
        spread = out["fpo_minus_top_ms"]["p95"] - out["fpo_minus_top_ms"]["p5"]
        if spread < 0.25 and abs(med) < 0.15:
            out["fpo_reading"] = "equals the top row"
        elif spread < 0.25 and abs(med + blank_ms) < 0.15:
            out["fpo_reading"] = "equals the vblank start (the top row comes one blank later)"
        else:
            out["fpo_reading"] = f"neither: {med:+.2f} ms from the top row, spread {spread:.2f} ms"
        # refreshes between consecutive pictures, ids consecutive only
        gaps = []
        for (ia, ta, _), (ib, tb, _) in zip(fpo_pairs, fpo_pairs[1:]):
            if ib == ia + 1 and period:
                gaps.append((tb - ta) / period)
        if gaps:
            out["fpo_gap_refreshes"] = describe(gaps, 3)
            out["fpo_gap_counts"] = {}
            for g in gaps:
                k = str(round(g))
                out["fpo_gap_counts"][k] = out["fpo_gap_counts"].get(k, 0) + 1
        # queue-end stage
        qe = [(pid, v["stages"][STAGE_QUEUE_END]) for pid, v in fb.items() if pid in by_id and STAGE_QUEUE_END in v["stages"]]
        if qe:
            out["queue_end_minus_call_ms"] = describe([(t - by_id[pid]["call"]) * 1e3 for pid, t in qe])
            qe_map = dict(qe)
            out["fpo_minus_queue_end_ms"] = describe([(t - qe_map[pid]) * 1e3 for pid, t, _ in fpo_pairs if pid in qe_map])
        # the wait of present N against the report of present N
        pairs = []
        for r in waits:
            # the wait recorded in the row of present N+1 was for present N (lag 1)
            wid = r["id"] - exp["wait_lag"]
            if wid in {p for p, _, _ in fpo_pairs}:
                t = next(t for p, t, _ in fpo_pairs if p == wid)
                pairs.append((r["wait_ret"] - t) * 1e3)
        if pairs:
            out["wait_return_minus_fpo_ms"] = describe(pairs)
        # relative target: gap between consecutive pictures in refreshes
        if exp["target"] == 1 and exp["target_refreshes"] > 0 and gaps:
            # relative target = the least time the previous picture stays up: whole refreshes, rounded up, or to the
            # nearest refresh cycle (a .5 target may go either way)
            want = exp["target_refreshes"]
            if exp["nearest"]:
                allowed = {math.floor(want + 0.5), math.ceil(want - 0.5)}
            else:
                allowed = {math.ceil(want - 1e-9)}
            median = pct(gaps, .5)
            out["target_check"] = {"requested_refreshes": want, "median_gap": round(median, 3),
                                   "honoured": any(abs(median - a) < 0.2 for a in allowed)}
    elif exp["timing"]:
        out["note"] = "no usable first-pixel-out reports"
    return out


def analyze(log, skip_s=0.3):
    g = log.get("scan_grid", {})
    grid = Grid(g["base"], g["period"]) if g.get("ok") and g.get("period") else None
    vblanks = sorted(log.get("vblanks", []))
    return {"monitor": log["monitor"]["name"], "windowed": log["monitor"]["windowed"], "device": log["device"],
            "config": log["config"], "surface": log["surface"], "time_domains": log["time_domains"],
            "scan_grid": g, "timing": log["timing"],
            "experiments": [analyze_experiment(log, e, grid, vblanks, skip_s) for e in log["experiments"]]}


def lines(result):
    out = []
    s = result["surface"]
    out.append(f"== {result['monitor']} on {result['device']} ({'windowed' if result['windowed'] else 'full-size'}), "
               f"{result['timing']['refresh_hz']:.3f} Hz signal, blank {result['timing']['blank_s'] * 1e3:.3f} ms; "
               f"config {result['config']['alpha']} {result['config']['format']}, {result['config']['images']} images, "
               f"loader layers off: {result['config']['layers_off']}")
    out.append(f"   surface: present timing supported {s['present_timing_supported']}, absolute {s['absolute_supported']}, "
               f"relative {s['relative_supported']}, stage queries 0x{s['stage_queries']:x}, id2 {s['present_id2_supported']}, "
               f"wait2 {s['present_wait2_supported']}; time domains: "
               + ", ".join(f"{d['name']}({d['id']})" for d in result["time_domains"]))
    g = result["scan_grid"]
    out.append(f"   top-row grid: {'ok' if g.get('ok') else 'MISSING'}, residual p50 {g.get('resid_p50_us', 0):.2f} us, "
               f"{g.get('samples', 0)} samples")
    for e in result["experiments"]:
        out.append(f"-- {e['name']}" + (f"  [{e['status']}]" if e.get("status") else ""))
        if "note" in e and "presents" not in e:
            out.append(f"   {e['note']}")
            continue
        out.append(f"   {e['presents']} presents, {e['presents_per_s']}/s = {e['presents_per_refresh']} per refresh of the {e['refresh_hz']} Hz monitor; "
                   f"present interval {fmt(e['present_interval_ms'])} ms; vkQueuePresentKHR call {fmt(e['present_call_ms'])} ms; "
                   f"acquire {fmt(e['acquire_ms'])} ms; results {e['present_results']}")
        if "wait_ms" in e:
            out.append(f"   wait for the previous present: {fmt(e['wait_ms'])} ms, results {e['wait_results']}")
        if "acquire_return_vs_top_ms" in e:
            line = f"   returns against the nearest top row (ms): acquire {fmt(e['acquire_return_vs_top_ms'])}"
            if "wait_return_vs_top_ms" in e:
                line += f"; wait {fmt(e['wait_return_vs_top_ms'])}"
            line += f"; present call {fmt(e['present_call_vs_top_ms'])}"
            out.append(line)
        if e["timing"]:
            out.append(f"   timing: {e['feedback_results']} results ({e.get('feedback_complete', 0)} complete); refresh duration "
                       f"{e.get('refresh_duration_ms')} ms, interval {e.get('refresh_interval_ms')} (raw {e.get('refresh_interval_raw')}); "
                       f"domain clock / QPC rate {e.get('domain_clock_vs_qpc')}"
                       + (f"; qpc unit {e['qpc_unit']}" if "qpc_unit" in e else ""))
        if "fpo_minus_top_ms" in e:
            out.append(f"   first pixel out minus nearest top row (ms): {fmt(e['fpo_minus_top_ms'])}  -> {e['fpo_reading']}")
            out.append(f"   first pixel out minus vblank start (top row - blank {e['blank_ms']} ms): {fmt(e['fpo_minus_vblank_start_ms'])}; "
                       f"minus WaitForVBlank wake-up: {fmt(e.get('fpo_minus_wakeup_ms', {}))}")
            out.append(f"   first pixel out minus present call: {fmt(e['fpo_minus_call_ms'])} ms; minus present return: {fmt(e['fpo_minus_ret_ms'])} ms")
            if "queue_end_minus_call_ms" in e:
                out.append(f"   queue-operations-end minus present call: {fmt(e['queue_end_minus_call_ms'])} ms; first pixel out minus queue end: "
                           f"{fmt(e.get('fpo_minus_queue_end_ms', {}))} ms")
            if "fpo_gap_refreshes" in e:
                out.append(f"   refreshes between consecutive pictures: {fmt(e['fpo_gap_refreshes'])}, counts {e['fpo_gap_counts']}")
            if "wait_return_minus_fpo_ms" in e:
                out.append(f"   wait for present N returns minus N's first pixel out: {fmt(e['wait_return_minus_fpo_ms'])} ms")
            if "target_check" in e:
                t = e["target_check"]
                out.append(f"   relative target {t['requested_refreshes']} refreshes: median gap {t['median_gap']} -> "
                           f"{'honoured' if t['honoured'] else 'NOT honoured'}")
        elif "note" in e:
            out.append(f"   {e['note']}")
    return out


def fmt(d):
    if not d:
        return "-"
    return f"p50 {d['p50']:+.3f} (p5 {d['p5']:+.3f}, p95 {d['p95']:+.3f}, min {d['min']:+.3f}, max {d['max']:+.3f}; n {d['n']})"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("log_dir", type=Path)
    ap.add_argument("--skip", type=float, default=0.3, help="ignore the first seconds of each experiment")
    a = ap.parse_args()
    results = []
    for p in sorted(a.log_dir.glob("probe-*.json")):
        res = analyze(json.loads(p.read_text()), a.skip)
        results.append(res)
        for line in lines(res):
            print(line)
    (a.log_dir / "probe-summary.json").write_text(json.dumps(results, indent=1))
    print(f"wrote {a.log_dir / 'probe-summary.json'}")


if __name__ == "__main__":
    main()
