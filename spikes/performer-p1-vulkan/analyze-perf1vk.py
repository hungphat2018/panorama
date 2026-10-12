"""Analyze perf1vk logs: pace, render time, missed refreshes, and what happened around every hitch.

perf1vk writes the same monitor-N-<mode>.json files as perf1 (so spikes/per-monitor-presenter/analyze-per-monitor-pacing.py
and spikes/performer-p1/analyze-perf1.py read them) plus, per monitor, a "vk" block with the phases of every present
loop, and one events.json with the libplacebo / FFmpeg log lines and the presenter's own notes. This script adds:

  - loop statistics: the interval between presents, and the interval between swapchain image acquires, which is the
    monitor's real pace when libplacebo's swapchain is used (it reports nothing back). A gap of k+1 refreshes between
    two acquires means k refreshes went by without a new picture: "missed refreshes (inferred)".
  - phase times of a loop (image acquire / frame pick / render / present call / waiting for the GPU)
  - hitches: every loop longer than --hitch-ms (default: the value the run was started with, 50), with the phase that
    took the time and the log lines recorded within a few milliseconds of it. This is what attributes a stall to the
    swapchain (acquire or present blocked), shader compilation (a "Spent N ms compiling shader" line), the decoder (the
    pick phase, queue_more), the GPU (render or swap phase) or the thread not running at all (no phase long, a gap
    before the loop started).
  - the same row-light seam offsets as analyze-perf1.py

Usage: python -I analyze-perf1vk.py LOG_DIR [--mode nearest|own] [--skip-seconds 1] [--hitch-ms 50]
"""
import argparse
import bisect
import importlib.util
import json
import re
import statistics
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # the pacing analyzer lives in another spike's folder: leave no cache files there

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "pacing", HERE.parent / "per-monitor-presenter" / "analyze-per-monitor-pacing.py")
pacing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pacing)
spec_t = importlib.util.spec_from_file_location("avt", HERE / "analyze-vk-timing.py")
avt = importlib.util.module_from_spec(spec_t)
spec_t.loader.exec_module(avt)

PHASES = ("acquire", "pick", "render", "gap", "present", "swap")
CONTEXT_S = 0.004  # log lines this close to a hitch are listed with it


def pct(v, q):
    v = sorted(v)
    return v[min(len(v) - 1, max(0, int(q * len(v) - 1e-9)))] if v else float("nan")


def stats(v):
    return {"n": len(v), "p50": round(pct(v, .5), 3), "p99": round(pct(v, .99), 3), "max": round(max(v), 3)} if v else {}


def cycles_of(log):
    """The loops of one monitor as dicts of QPC times: loop, acquired, picked, rendered, call, ret, done."""
    names = log["vk"]["cycle_fields"]
    return [dict(zip(names, c)) for c in log["vk"]["cycles"]]


def phase_ms(c):
    """Durations of the phases of one loop, ms."""
    return {
        "acquire": (c["acquired"] - c["loop"]) * 1e3,   # blocked in vkAcquireNextImageKHR (or the offscreen wait)
        "pick": (c["picked"] - c["acquired"]) * 1e3,    # pl_queue_update: choosing and mapping the film frame
        "render": (c["rendered"] - c["picked"]) * 1e3,  # pl_render_image_mix, CPU side
        "gap": (c["call"] - c["rendered"]) * 1e3,       # between the render call and the present call
        "present": (c["ret"] - c["call"]) * 1e3,        # the present call (libplacebo: pl_swapchain_submit_frame)
        "swap": (c["done"] - c["ret"]) * 1e3,           # waiting for the GPU to finish the frame
    }


COMPILE = re.compile(r"Spent ([0-9.]+) ms (compiling shader|creating pipeline|translating SPIR-V|re-specializing shader|generating shader LUT)")


def classify(gap_ms, dominant, events):
    """A guess at where a hitch came from, from its dominant phase and the log lines next to it. Order matters: a log line
    that explains the time wins over the phase it happened in."""
    texts = [e[2] for e in events]
    for t in texts:
        m = COMPILE.search(t)
        if m and float(m.group(1)) >= 0.3 * gap_ms:
            return "shader compilation (" + t + ")"
    if any(t.startswith("window ") for t in texts):
        return "window (a window message came with it)"
    if any("swapchain image" in t or "wapchain of size" in t or "Failed (re)creating" in t for t in texts):
        return "swapchain re-creation"
    if any(t.startswith("ffmpeg:") for t in texts) and dominant == "pick":
        return "decoder (an FFmpeg message came with it)"
    if dominant in ("acquire", "present", "previous swap"):
        return "swapchain / presentation engine (blocked in " + dominant + ", no log line explains it)"
    if dominant == "pick":
        return "decoder or frame mapping (pl_queue_update)"
    if dominant in ("render", "swap", "gap"):
        return "GPU or render passes (" + dominant + ")"
    if dominant.startswith("before loop"):
        return "render thread not scheduled (CPU or OS)"
    return "unknown"


def hitches(log, cycles, events, hitch_ms, t_skip):
    """Every loop whose present came more than hitch_ms after the previous present, with its dominant phase."""
    out = []
    ev_times = [e[0] for e in events]
    for i in range(1, len(cycles)):
        c, p = cycles[i], cycles[i - 1]
        if c["call"] < t_skip:
            continue
        gap_ms = (c["call"] - p["call"]) * 1e3
        if gap_ms <= hitch_ms:
            continue
        ph = phase_ms(c)
        # time between the previous present's return and this loop starting (the thread not running, or the previous swap)
        ph_prev = phase_ms(p)
        before_loop = (c["loop"] - p["done"]) * 1e3
        candidates = dict(ph)
        candidates["previous swap"] = ph_prev["swap"]
        candidates["before loop (thread not running)"] = before_loop
        dominant = max(candidates, key=candidates.get)
        lo = bisect.bisect_left(ev_times, p["done"] - CONTEXT_S)
        hi = bisect.bisect_right(ev_times, c["done"] + CONTEXT_S)
        near = [e for e in events[lo:hi]]
        out.append({"t_after_start_s": round(c["call"] - log["t0"], 3), "gap_ms": round(gap_ms, 1), "dominant_phase": dominant,
                    "dominant_ms": round(candidates[dominant], 1), "cause": classify(gap_ms, dominant, near),
                    "phases_ms": {k: round(v, 2) for k, v in ph.items()}, "before_loop_ms": round(before_loop, 2),
                    "previous_swap_ms": round(ph_prev["swap"], 2),
                    "events": [[round(e[0] - log["t0"], 4), e[1], e[2]] for e in near[:8]]})
    return out


def loop_stats(log, cycles, skip_s):
    t_skip = log["t0"] + skip_s
    cs = [c for c in cycles if c["call"] >= t_skip]
    period = statistics.median(b - a for a, b in zip(log["vblanks"], log["vblanks"][1:]))
    out = {"loops": len(cs), "refresh_hz": round(1 / period, 3)}
    if len(cs) < 3:
        return out
    call_gaps = [(b["call"] - a["call"]) * 1e3 for a, b in zip(cs, cs[1:])]
    acq_gaps = [(b["acquired"] - a["acquired"]) for a, b in zip(cs, cs[1:])]
    out["present_interval_ms"] = stats(call_gaps)
    out["acquire_interval_ms"] = stats([g * 1e3 for g in acq_gaps])
    slots = [round(g / period) for g in acq_gaps]
    out["acquire_gap_refreshes"] = {str(k): slots.count(k) for k in sorted(set(slots))}
    out["missed_refreshes_inferred"] = sum(max(0, s - 1) for s in slots)
    out["loops_over_1_5_refreshes"] = sum(1 for g in acq_gaps if g > 1.5 * period)
    phases = [phase_ms(c) for c in cs]
    out["phase_ms"] = {k: stats([p[k] for p in phases]) for k in PHASES}
    out["lead_before_target_ms"] = stats(log["lead_ms"][-len(cs):]) if log.get("lead_ms") else {}
    return out


def render_stats(log, skip_s):
    t_skip = log["t0"] + skip_s
    pres = log["presents"]
    keep = [i for i, p in enumerate(pres) if p[2] >= t_skip]
    new = {i for i in keep if i and pres[i][4] != pres[i - 1][4]}
    cpu, gpu = log["render_cpu_ms"], log["render_gpu_ms"]
    return {
        "render_cpu_ms_all": stats([cpu[i] for i in keep]),
        "render_cpu_ms_new_frame": stats([cpu[i] for i in keep if i in new]),
        "render_gpu_ms_all": stats([gpu[i] for i in keep]),
        "render_gpu_ms_new_frame": stats([gpu[i] for i in keep if i in new]),
        "queue_more": log["perf1"]["queue_more"], "empty_mix": log["perf1"]["empty_mix"],
        "render_failed": log["perf1"]["render_failed"], "swap_begin_failures": log["vk"]["start_fail"],
        "distinct_swapchain_images": log["vk"]["distinct_images"],
    }


def apply_own_feedback(log):
    """For the own-swapchain backend: turn VK_EXT_present_timing results into the screen times the pacing analysis reads.

    The pacing analysis wants, per present, the moment its refresh began (DXGI's SyncQPCTime, the start of the blank) and
    snaps it to the monitor's top-row grid. "Image first pixel out" is read as: the top row of the refresh it belongs to is
    the grid line nearest to (first pixel out + half a blank), which holds whether the extension reports the top row itself
    or the start of the blank before it; the screen time handed on is the middle of that blank. Returns a landing summary
    (how far each present's real refresh was from the one it was aimed at) or None when the log has no feedback."""
    own = log.get("own")
    g = log.get("scan_grid", {})
    if not own or not own.get("timing") or not own.get("feedback") or not g.get("ok"):
        return None
    grid = avt.Grid(g["base"], g["period"])
    blank = log["timing"]["blank_s"]
    fields = own["feedback_fields"]
    scale, unit = None, None
    if own["domain"] == avt.DOMAIN_QPC:
        # a QPC-domain report counts QPC ticks or nanoseconds: take the unit that puts it next to its present call
        pf = own["present_fields"]
        call_of = {p[pf.index("id")]: p[pf.index("call")] for p in own["presents"]}
        i_id, i_raw = fields.index("id"), fields.index("first_pixel_out_raw")
        pairs = [(row[i_raw], call_of[row[i_id]]) for row in own["feedback"] if row[i_raw] and row[i_id] in call_of]
        scale, unit = avt.qpc_domain_scale(pairs, own["qpc_freq"])
    convert = avt.make_converter({"domain": own["domain"], "calibrations": own["calibrations"]}, own["qpc_freq"], scale)
    if convert is None:
        return None
    stats, landed, fpo_rel = [], {}, []
    targets = {p[0]: p[3] for p in log["presents"]}
    for row in own["feedback"]:
        fb = dict(zip(fields, row))
        if not fb["first_pixel_out_raw"]:
            continue
        fpo = convert(fb["first_pixel_out_raw"], avt.STAGE_FIRST_PIXEL_OUT if own["domain"] != avt.DOMAIN_QPC else 0, fb["polled"])
        top = grid.nearest(fpo + 0.5 * blank)
        fpo_rel.append((fpo - top) * 1e3)
        shown = top - 0.5 * blank
        stats.append([fb["id"], 0, 0, shown, fb["polled"]])
        if fb["id"] in targets:
            # `target` is the wake-up time of the refresh the present was aimed at: its blank start plus the wake-up latency
            landed[fb["id"]] = round((top - blank - targets[fb["id"]]) / grid.period)
    log["stats"] = stats
    counts = {}
    for k in landed.values():
        counts[str(k)] = counts.get(str(k), 0) + 1
    summary = {"results": len(stats), "of_presents": len(log["presents"]), "fpo_minus_top_ms": avt.describe(fpo_rel),
               "landing_vs_target_refreshes": dict(sorted(counts.items(), key=lambda kv: int(kv[0])))}
    if unit:
        summary["qpc_unit"] = unit
    return summary


def analyze(log_dir, mode="nearest", skip_s=1.0, hitch_ms=None):
    logs = [json.loads(p.read_text()) for p in sorted(log_dir.glob(f"monitor-*-{mode}.json"))]
    ev_path = log_dir / "events.json"
    events = json.loads(ev_path.read_text())["events"] if ev_path.exists() else []
    result = {"mode": mode, "monitors": [], "hitches": {}}
    landing = {}
    for log in logs:
        landing[log["name"]] = apply_own_feedback(log)
    for log in logs:
        cycles = cycles_of(log)
        threshold = hitch_ms if hitch_ms is not None else log["vk"]["hitch_ms"]
        row = pacing.summarize(log, skip_s)
        row.pop("first_shown")
        row["vk"] = {k: log["vk"][k] for k in ("backend", "headless", "present_mode", "depth", "images", "alpha", "format",
                                              "device", "layers_off")}
        row["loop"] = loop_stats(log, cycles, skip_s)
        row["landing"] = landing[log["name"]]
        row["render"] = render_stats(log, skip_s)
        result["monitors"].append(row)
        result["hitches"][log["name"]] = hitches(log, cycles, events, threshold, log["t0"] + skip_s)
    firsts = [pacing.summarize(l, skip_s)["first_shown"] for l in logs]
    result["film"] = {"fps": logs[0]["fps"], "locked": logs[0]["perf1"]["locked"], "windowed": logs[0]["perf1"]["windowed"]} if logs else {}
    if len(logs) >= 2:
        a0, b0 = firsts[0], firsts[1]
        off = sorted((b0[n] - a0[n]) * 1e3 for n in a0 if n in b0)
        if off:
            result["blank_start_offset_ms"] = {"frames": len(off), "min": round(off[0], 2), "median": round(pct(off, .5), 2),
                                               "p95": round(pct(off, .95), 2), "max": round(off[-1], 2)}
        result["row_offsets"] = pacing.row_offsets(logs, firsts)
    return result


def lines(result):
    out = []
    f = result["film"]
    if f:
        out.append(f"film {f['fps']:.4f} fps, locked={f['locked']}, windowed={f['windowed']}")
    for m in result["monitors"]:
        vk = m["vk"]
        out.append(f"{m['name']}: {m['refresh_hz']} Hz, backend {vk['backend']}{' (headless)' if vk['headless'] else ''}, present mode "
                   f"{vk['present_mode']}, depth {vk['depth']}, {vk['format']} {vk['alpha']}, layers off: {vk['layers_off']}; device {vk['device']}")
        lp = m["loop"]
        if "present_interval_ms" in lp:
            out.append(f"  loops {lp['loops']}: present interval {lp['present_interval_ms']}; acquire interval {lp['acquire_interval_ms']}")
            out.append(f"  acquire gaps in refreshes {lp['acquire_gap_refreshes']}; missed refreshes (inferred) {lp['missed_refreshes_inferred']}; "
                       f"loops over 1.5 refreshes {lp['loops_over_1_5_refreshes']}")
            out.append("  phases (ms): " + "; ".join(f"{k} p50 {v['p50']} p99 {v['p99']} max {v['max']}" for k, v in lp["phase_ms"].items()))
        rd = m["render"]
        out.append(f"  render cpu {rd['render_cpu_ms_all']}, new frame {rd['render_cpu_ms_new_frame']}; gpu {rd['render_gpu_ms_all']}; "
                   f"queue_more {rd['queue_more']}, empty_mix {rd['empty_mix']}, render_failed {rd['render_failed']}, "
                   f"swap_begin failures {rd['swap_begin_failures']}, distinct swapchain images {rd['distinct_swapchain_images']}")
        out.append(f"  refreshes per frame {m['refreshes_per_frame']}, skipped frames {m['skipped_frames']}, "
                   f"{m['fallback_times']} of {m['presents']} screen times inferred (no per-present report)")
        if m.get("landing"):
            ld = m["landing"]
            out.append(f"  present timing: {ld['results']} of {ld['of_presents']} presents reported; first pixel out minus the nearest top row "
                       f"(ms) {ld['fpo_minus_top_ms']}; refreshes between the refresh aimed at and the one reached {ld['landing_vs_target_refreshes']}"
                       + (f"; qpc unit {ld['qpc_unit']}" if "qpc_unit" in ld else ""))
        hs = result["hitches"][m["name"]]
        out.append(f"  hitches (> threshold): {len(hs)}")
        for h in hs[:12]:
            out.append(f"    at +{h['t_after_start_s']} s: {h['gap_ms']} ms between presents, mostly '{h['dominant_phase']}' "
                       f"({h['dominant_ms']} ms), likely cause: {h['cause']}; phases {h['phases_ms']}, before loop {h['before_loop_ms']} ms, previous swap {h['previous_swap_ms']} ms")
            for e in h["events"]:
                out.append(f"      event +{e[0]} s L{e[1]}: {e[2][:140]}")
    if "blank_start_offset_ms" in result:
        out.append(f"blank-start frame-change offset ms (monitor1 - monitor0): {result['blank_start_offset_ms']}")
    for line in pacing.row_offset_lines(result.get("row_offsets")):
        out.append(line)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("log_dir", type=Path)
    ap.add_argument("--mode", default="nearest")
    ap.add_argument("--skip-seconds", type=float, default=1.0)
    ap.add_argument("--hitch-ms", type=float, default=None)
    a = ap.parse_args()
    result = analyze(a.log_dir, a.mode, a.skip_seconds, a.hitch_ms)
    for line in lines(result):
        print(line)
    (a.log_dir / "perf1vk-summary.json").write_text(json.dumps(result, indent=1, default=str))
    print(f"wrote {a.log_dir / 'perf1vk-summary.json'}")


if __name__ == "__main__":
    main()
