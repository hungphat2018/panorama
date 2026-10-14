"""Scores an E1 run: per cell and monitor, how well the performer kept every film frame on time while the GPU was shared.

  python -I -X utf8 e1-analyse.py RUN_DIR [--skip-seconds 2] [--tail-seconds 0.5] [--no-csv] [--check-truth]
RUN_DIR is a run of e1-player-under-load.py (one folder per cell) or one perf1 output folder (scored as a single cell).

Inputs per cell. perf1 writes:
  monitor-N-nearest.json  presents: [present id (GetLastPresentCount after Present1), Present1 call, return (QPC s), wake-up time
                          of the refresh it was planned for, film frame ordinal]; stats: [PresentCount, PresentRefreshCount,
                          SyncRefreshCount, SyncQPCTime, polled] from GetFrameStatistics after every present, a row whenever
                          PresentCount changed; vblanks (WaitForVBlank wake-ups); render_gpu_ms / render_cpu_ms per present;
                          composition_modes (DXGI_FRAME_STATISTICS_MEDIA)
  perf1-cuelog.json       refreshes: the engine's record of every present, same order as presents (k = planned refresh, epoch,
                          ordinal, ...); starts: each frame's start refresh on every monitor (the plan)
  gpu-share.json          frame latency and GPU priority asked, return codes, read-backs (perf1-gpu-share.c)
the runner writes cell.json (cell, clock pairs QPC/wall, temperatures, exit codes), load.json (gpu-load.py) and nvsmi.csv (E2).

Per present (presents-<monitor>.csv): the refresh it reached the screen on is the PresentRefreshCount (vblank counter) of its
statistics row; a present without a row takes it from its neighbours when they leave no doubt (their counters differ exactly by
their ids: no miss between them), else it stays unknown. Per monitor, over the steady part (presents called from film start +
skip to the last present - tail):
  missed    refreshes on which no new present reached the screen (the previous one stayed up): over consecutive presents with a
            statistics row, counter difference minus id difference
  wrong     film frames shown for another number of refreshes than the engine planned (cue log starts): shown = counter of the
            next shown frame's first present minus this frame's; a frame never shown is wrong (0 refreshes); frames whose first
            present has no known counter are "uncertain" and left out. Split: with a missed refresh among its presents, at its
            change or up to 3 presents before it ("wrong_with_miss"), else "replan" only (see below)
  err       present-interval error: screen time (SyncQPCTime) difference of consecutive presents with a row minus one refresh
            period per present between them: 0 when every present stays up one refresh, + one period per missed refresh
  replan    the engine's planned refresh skipping (+2) or repeating (0) between consecutive presents: wake-up jitter against its
            0.5 ms service margin (engine_service), seen without any load; a skip and repeat straddling a frame change make two
            wrong frames without a missed refresh
  overlay   share of presents DXGI reported in independent flip; anything else is a composed window (pace not comparable)
Clocks (nvsmi.csv on the QPC clock through the runner's clock pairs, over the steady part): P-states, SM and memory clock p5 /
p50, power, throttle reasons; a missed refresh or wrong frame is "near a clock event" when a P-state change, a clock step over
15 % or a newly set throttle reason lies within 0.25 s of it.
Writes RUN_DIR/e1-summary.json and prints a table; --check-truth compares with fake-truth.json (the dry run's fake player).
"""
import argparse
import bisect
import csv
import json
import math
import statistics
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

REASONS = {0x1: "idle", 0x2: "app_clocks", 0x4: "sw_power_cap", 0x8: "hw_slowdown", 0x10: "sync_boost", 0x20: "sw_thermal",
           0x40: "hw_thermal", 0x80: "hw_power_brake", 0x100: "display_clock"}
NEAR_S, CLOCK_STEP = 0.25, 0.15


def pct(v, q):
    v = sorted(v)
    return v[min(len(v) - 1, max(0, int(math.ceil(q * len(v))) - 1))] if v else None


def rnd(x, d=3):
    return None if x is None else round(x, d)


def read_json(p):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


# ---- one monitor -----------------------------------------------------------------------------------------------------

def screen_counters(ids, stat, period):
    """Per present: vblank counter it reached the screen on, where it came from, its screen time (QPC s)."""
    n = len(ids)
    prc, src, tq = [None] * n, [""] * n, [None] * n
    for i, pid in enumerate(ids):
        s = stat.get(pid)
        if s:
            prc[i], src[i] = s[0], "stat"
            tq[i] = s[2] - (s[1] - s[0]) * period  # SyncQPCTime is the time of SyncRefreshCount (equal to the counter in flip model)
    known = [i for i in range(n) if prc[i] is not None]
    for a, b in zip(known, known[1:]):
        if b - a > 1 and prc[b] - prc[a] == ids[b] - ids[a]:
            for i in range(a + 1, b):
                prc[i], src[i] = prc[a] + (ids[i] - ids[a]), "inferred"
                tq[i] = tq[a] + (prc[i] - prc[a]) * period
    return prc, src, tq


def score_monitor(mlog, rlog, starts, slot, skip_s, tail_s):
    pres = mlog["presents"]
    n = min(len(pres), len(rlog))
    vb = mlog.get("vblanks", [])
    period = statistics.median(b - a for a, b in zip(vb, vb[1:])) if len(vb) > 10 else 1.0 / 240.0
    out = {"name": mlog["name"], "refresh_hz": rnd(1.0 / period, 4), "presents": len(pres), "engine_records": len(rlog)}
    if n < 10:
        out["error"] = "too few presents"
        return out, [], []
    out["join_ok"] = all(abs(pres[i][3] - rlog[i][3]) < 1e-6 and pres[i][4] == rlog[i][6] for i in range(0, n, max(1, n // 200)))
    ids = [p[0] for p in pres[:n]]
    stat = {}
    for pc, prc_, sync, qpc, _polled in mlog["stats"]:
        stat.setdefault(pc, (prc_, sync, qpc))
    prc, src, tq = screen_counters(ids, stat, period)

    t_first, t_last = mlog["t0"] + skip_s, pres[n - 1][1] - tail_s
    steady = [i for i in range(n) if t_first <= pres[i][1] <= t_last]
    if len(steady) < 10:
        out["error"] = "no steady part (run too short for --skip-seconds)"
        return out, [], []
    s_lo, s_hi = steady[0], steady[-1]
    with_row = [i for i in steady if src[i] == "stat"]
    out["stats_coverage"] = rnd(len(with_row) / len(steady), 4)

    # missed refreshes and the present-interval error, over consecutive presents with a statistics row
    missed, anomalies, errs, miss_events = 0, 0, [], []
    for a, b in zip(with_row, with_row[1:]):
        extra = (prc[b] - prc[a]) - (ids[b] - ids[a])
        if extra > 0:
            missed += extra
            miss_events.append((b, extra))
        elif extra < 0:
            anomalies += 1
        errs.append(((tq[b] - tq[a]) - (ids[b] - ids[a]) * period) * 1e3)
    known_t = [tq[i] for i in steady if tq[i] is not None]
    minutes = (max(known_t) - min(known_t)) / 60.0 if len(known_t) > 1 else float("nan")

    # the plan: each frame's start refresh on this monitor; frames in the order they first reached a present
    plan = {(r[0], r[1]): r[5 + slot] for r in starts}
    first = {}
    for i in range(n):
        first.setdefault((rlog[i][5], rlog[i][6]), i)
    shown_order = sorted(first, key=first.get)
    miss_idx = [b for b, _ in miss_events]
    frames, wrong, uncertain, wrong_miss, deltas, wrong_events = 0, 0, 0, 0, Counter(), []
    frame_of = {}  # (ep, ord) -> (planned, shown, wrong)
    for f, g in zip(shown_order, shown_order[1:]):
        i, j = first[f], first[g]
        if f[0] != g[0] or i < s_lo or j > s_hi:
            continue
        if prc[i] is None or prc[j] is None:
            uncertain += 1
            continue
        for o in range(f[1], g[1]):  # f itself, then any frame between f and g that never reached the screen
            p0, p1 = plan.get((f[0], o)), plan.get((f[0], o + 1))
            if p0 is None or p1 is None:
                continue
            planned = p1 - p0
            shown = prc[j] - prc[i] if o == f[1] else 0
            frames += 1
            bad = shown != planned
            frame_of[(f[0], o)] = (planned, shown, bad)
            if bad:
                wrong += 1
                deltas[shown - planned] += 1
                # a missed refresh among its presents, at its change, or up to 3 presents before it (behind a deeper queue the
                # engine's re-plan after a late flip comes that many presents later)
                k = bisect.bisect_left(miss_idx, i - 4)
                near_miss = k < len(miss_idx) and miss_idx[k] <= j
                wrong_miss += near_miss
                wrong_events.append(tq[j] if tq[j] is not None else pres[j][1])

    ks = [rlog[i][0] for i in steady]
    skips = sum(1 for a, b in zip(ks, ks[1:]) if b - a >= 2)
    repeats = sum(1 for a, b in zip(ks, ks[1:]) if b == a)
    comp = mlog.get("composition_modes", {})
    total_comp = sum(comp.values())
    gpu = [mlog["render_gpu_ms"][i] for i in steady if i < len(mlog.get("render_gpu_ms", []))]
    out.update({
        "steady_presents": len(steady), "minutes": rnd(minutes, 4), "frames_scored": frames, "frames_uncertain": uncertain,
        "missed": missed, "missed_per_min": rnd(missed / minutes, 3) if minutes > 0 else None,
        "wrong": wrong, "wrong_per_min": rnd(wrong / minutes, 3) if minutes > 0 else None,
        "wrong_with_miss": wrong_miss, "wrong_replan_only": wrong - wrong_miss,
        "wrong_deltas": {str(k): v for k, v in sorted(deltas.items())}, "replan_skips": skips, "replan_repeats": repeats,
        "err_ms": {"p50": rnd(pct(errs, .5)), "p99": rnd(pct(errs, .99)), "p999": rnd(pct(errs, .999)),
                   "max": rnd(max(errs) if errs else None), "min": rnd(min(errs) if errs else None)},
        "stat_anomalies": anomalies,
        "overlay_share": rnd(comp.get("1", 0) / total_comp, 4) if total_comp else None,
        "composition": comp, "render_gpu_ms": {"p50": rnd(pct(gpu, .5)), "p99": rnd(pct(gpu, .99)), "p999": rnd(pct(gpu, .999)),
                                               "max": rnd(max(gpu) if gpu else None)},
        "error_text": mlog.get("error", ""),
        # perf1's own counters: the decoder behind (queue_more, empty_mix), a frame drawn other than chosen, failed renders
        "perf1_counters": {k: mlog.get("perf1", {}).get(k) for k in ("queue_more", "empty_mix", "drawn_mismatch", "render_failed")},
    })
    rows = []
    for i in range(n):
        key = (rlog[i][5], rlog[i][6])
        planned, shown, bad = frame_of.get(key, (None, None, None))
        nxt = prc[i + 1] if i + 1 < n and prc[i + 1] is not None and prc[i] is not None else None
        st = stat.get(ids[i])
        rows.append([ids[i], f"{pres[i][1]:.7f}", key[0], key[1], rlog[i][0], planned, prc[i], src[i] or "unknown",
                     st[1] if st else "", f"{st[2]:.7f}" if st else "", f"{tq[i]:.7f}" if tq[i] is not None else "",
                     (nxt - prc[i]) if nxt is not None else "", shown, "" if bad is None else int(bad),
                     int(s_lo <= i <= s_hi), mlog["render_gpu_ms"][i] if i < len(mlog.get("render_gpu_ms", [])) else "",
                     mlog["render_cpu_ms"][i] if i < len(mlog.get("render_cpu_ms", [])) else ""])
    events = [(tq[b] if tq[b] is not None else pres[b][1], "missed", x) for b, x in miss_events] + \
             [(t, "wrong", 1) for t in wrong_events]
    return out, rows, events


CSV_HEADER = ["present_id", "call_qpc", "epoch", "frame", "planned_refresh_k", "planned_refreshes_of_frame",
              "screen_refresh_counter", "counter_from", "sync_refresh_count", "sync_qpc", "screen_qpc", "refreshes_on_screen",
              "frame_shown_refreshes", "frame_wrong", "steady", "render_gpu_ms", "render_cpu_ms"]


# ---- clocks (E2) -------------------------------------------------------------------------------------------------------

def number(s):
    s = s.strip().split(" ")[0]
    try:
        return float(int(s, 16)) if s.startswith("0x") else float(s)
    except ValueError:
        return None


def read_nvsmi(path, offset):
    """Samples of nvsmi.csv with their QPC time: dicts t, pstate, sm, mem, power, power_instant, reasons, temp, util."""
    if not path.exists() or offset is None:
        return []
    with path.open(encoding="utf-8", errors="replace") as f:
        rows = list(csv.reader(f))
    if not rows:
        return []
    head = [h.strip() for h in rows[0]]

    def col(*keys):
        for i, h in enumerate(head):
            if any(h.startswith(k) for k in keys):
                return i
        return None
    c = {"t": col("timestamp"), "pstate": col("pstate"), "sm": col("clocks.current.sm", "clocks.sm"),
         "mem": col("clocks.current.memory", "clocks.mem"), "power": col("power.draw [W]", "power.draw ["),
         "power_instant": col("power.draw.instant"), "reasons": col("clocks_event_reasons.active", "clocks_throttle_reasons.active"),
         "temp": col("temperature.gpu"), "util": col("utilization.gpu")}
    out = []
    for r in rows[1:]:
        if c["t"] is None or len(r) < len(head):
            continue
        try:
            wall = datetime.strptime(r[c["t"]].strip(), "%Y/%m/%d %H:%M:%S.%f").timestamp()
        except ValueError:
            continue
        s = {"t": wall + offset, "pstate": r[c["pstate"]].strip() if c["pstate"] is not None else None}
        for k in ("sm", "mem", "power", "power_instant", "temp", "util", "reasons"):
            s[k] = number(r[c[k]]) if c[k] is not None else None
        s["reasons"] = int(s["reasons"]) if s["reasons"] is not None else 0
        out.append(s)
    return out


def clock_events(samples):
    """Times of P-state changes, SM or memory clock steps over CLOCK_STEP, and newly set throttle reasons (idle excluded)."""
    ev = []
    for a, b in zip(samples, samples[1:]):
        why = []
        if a["pstate"] != b["pstate"]:
            why.append(f"{a['pstate']}->{b['pstate']}")
        for k in ("sm", "mem"):
            if a[k] and b[k] and abs(b[k] - a[k]) > CLOCK_STEP * a[k]:
                why.append(f"{k} {a[k]:.0f}->{b[k]:.0f}")
        new = (b["reasons"] & ~a["reasons"]) & ~0x1
        if new:
            why.append("+" + "+".join(n for bit, n in REASONS.items() if new & bit))
        if why:
            ev.append((b["t"], ", ".join(why)))
    return ev


def summarize_clocks(samples):
    if not samples:
        return None
    seen = Counter()
    for s in samples:
        for bit, name in REASONS.items():
            if s["reasons"] & bit:
                seen[name] += 1
    vals = lambda k: [s[k] for s in samples if s[k] is not None]
    return {"samples": len(samples), "pstates": dict(Counter(s["pstate"] for s in samples)),
            "sm_mhz": {"p5": pct(vals("sm"), .05), "p50": pct(vals("sm"), .5)},
            "mem_mhz": {"p5": pct(vals("mem"), .05), "p50": pct(vals("mem"), .5)},
            "power_w": {"p50": pct(vals("power_instant") or vals("power"), .5), "max": max(vals("power_instant") or vals("power") or [0])},
            "temp_c": {"first": samples[0]["temp"], "max": max(vals("temp") or [0])}, "util_p50": pct(vals("util"), .5),
            "throttle_share": {k: round(v / len(samples), 3) for k, v in seen.items()}}


# ---- one cell ----------------------------------------------------------------------------------------------------------

def score_cell(d, skip_s, tail_s, write_csv):
    meta = read_json(d / "cell.json") or {"cell": d.name}
    res = {"cell": meta.get("cell", d.name), "params": {k: meta.get(k) for k in ("load", "latency", "priority", "duty")},
           "status": meta.get("status", "logs only"), "monitors": {}, "flags": []}
    cue = read_json(d / "perf1-cuelog.json")
    mpaths = sorted(d.glob("monitor-*-nearest.json"))
    if cue is None or not mpaths:
        res["flags"].append("no perf1 logs")
        return res, []
    slot_of = {m["name"]: i for i, m in enumerate(cue["monitors"])}
    if cue.get("stats", {}).get("truncated"):
        res["flags"].append("the engine's refresh log was truncated")
    pairs = meta.get("clock_pairs") or []
    offset = statistics.median(q - w for q, w in pairs) if pairs else None
    samples = read_nvsmi(d / "nvsmi.csv", offset)
    all_events, windows = [], []
    for p in mpaths:
        mlog = read_json(p)
        slot = slot_of.get(mlog["name"])
        if slot is None:
            res["flags"].append(f"{mlog['name']} not in the cue log")
            continue
        out, rows, events = score_monitor(mlog, cue["refreshes"][str(slot)], cue["starts"], slot, skip_s, tail_s)
        res["monitors"][mlog["name"]] = out
        all_events += [(t, kind, x, mlog["name"]) for t, kind, x in events]
        if rows:
            windows.append((mlog["t0"] + skip_s, float(rows[-1][1]) - tail_s))
        if write_csv and rows:
            with (d / f"presents-{mlog['name']}.csv").open("w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(CSV_HEADER)
                w.writerows(rows)
    if windows and samples:
        lo, hi = min(a for a, _ in windows), max(b for _, b in windows)
        inside = [s for s in samples if lo <= s["t"] <= hi]
        res["clocks"] = summarize_clocks(inside)
        ev = clock_events(inside)
        ev_t = [t for t, _ in ev]
        for name, out in res["monitors"].items():
            near = {"missed": 0, "wrong": 0}
            for t, kind, x, mon in all_events:
                if mon != name:
                    continue
                k = bisect.bisect_left(ev_t, t - NEAR_S)
                if k < len(ev_t) and ev_t[k] <= t + NEAR_S:
                    near[kind] += x
            out["near_clock_event"] = near
        res["clock_events"] = [{"t_from_start": round(t - lo, 3), "what": w} for t, w in ev[:200]]
    elif not samples:
        res["flags"].append("no nvsmi samples")
    res["load"] = read_json(d / "load.json")
    if res["load"]:
        res["load"].pop("windows", None)
    gs = read_json(d / "gpu-share.json")
    res["gpu_share"] = gs
    for k in ("temp_start_c", "temp_end_c", "perf1_rc", "load_rc", "played_s", "rest"):
        if k in meta:
            res[k] = meta[k]
    flag_problems(res, meta, gs)
    return res, all_events


def flag_problems(res, meta, gs):
    f = res["flags"]
    for name, m in res["monitors"].items():
        if m.get("error"):
            f.append(f"{name}: {m['error']}")
        if m.get("error_text"):
            f.append(f"{name}: perf1 error '{m['error_text']}'")
        if m.get("overlay_share") is not None and m["overlay_share"] < 0.95:
            f.append(f"{name}: overlay only {m['overlay_share']:.0%} (composed: pace not comparable)")
        if m.get("stats_coverage") is not None and m["stats_coverage"] < 0.8:
            f.append(f"{name}: statistics for {m['stats_coverage']:.0%} of presents")
        if m.get("join_ok") is False:
            f.append(f"{name}: presents and engine records do not line up")
        bad = {k: v for k, v in (m.get("perf1_counters") or {}).items() if v}
        if bad:
            f.append(f"{name}: perf1 counted {bad}")
    if gs and meta.get("latency"):
        reads = {m["reads"] for m in gs["frame_latency"]["monitors"]}
        if reads != {meta["latency"]}:
            f.append(f"frame latency asked {meta['latency']}, swapchains read {sorted(reads)}")
    if gs and meta.get("priority") == "high":
        pr = gs["gpu_priority"]
        if pr["process_class"]["reads_name"] != "HIGH" or pr["gpu_thread"]["reads"] != 7:
            f.append("GPU priority high asked, read-back differs")
    if meta.get("perf1_rc") not in (None, 0):
        f.append(f"perf1 exit code {meta['perf1_rc']}")
    if meta.get("played_s") is not None and meta.get("seconds") and meta["played_s"] < 0.9 * meta["seconds"]:
        f.append(f"played {meta['played_s']:.0f} s of {meta['seconds']} s")
    ld = res.get("load")
    if meta.get("load", "none") not in ("none", "painter") and ld and ld.get("busy_share") is not None:
        if ld["busy_share"] < 0.8 * (meta.get("duty") or 100) / 100:
            f.append(f"load busy only {ld['busy_share']:.0%}")


# ---- the run -----------------------------------------------------------------------------------------------------------

def cells_of(run):
    """The cell folders of a run, in the order the runner ran them (run.json), or the folder itself if it is one perf1 run."""
    if any(run.glob("monitor-*-nearest.json")):
        return [run]
    found = sorted(p for p in run.iterdir() if p.is_dir() and ((p / "cell.json").exists() or any(p.glob("monitor-*.json"))))
    order = {name: i for i, name in enumerate((read_json(run / "run.json") or {}).get("cells", []))}
    return sorted(found, key=lambda p: (order.get(p.name, len(order)), p.name))


def print_table(results):
    head = f"{'cell':26s} {'monitor':9s} {'Hz':>7s} {'frames':>6s} {'wrong/min':>9s} {'miss/min':>8s} {'err p99':>7s} " \
           f"{'p99.9':>7s} {'ovl':>4s} {'replan':>6s} {'load ms p50/max':>15s} {'busy':>5s} {'SM p5':>6s} {'near clk':>8s}  flags"
    print(head)
    print("-" * len(head))
    for r in results:
        ld = r.get("load") or {}
        lm = ld.get("call_ms") or {}
        load_txt = f"{lm.get('p50', 0):.2f}/{lm.get('max', 0):.2f}" if lm.get("p50") is not None else "-"
        busy = f"{ld['busy_share']:.0%}" if ld.get("busy_share") is not None else "-"
        sm = (r.get("clocks") or {}).get("sm_mhz", {}).get("p5")
        if not r["monitors"]:
            print(f"{r['cell']:26s} {'-':9s} {'':>7s} {'':>6s} {'':>9s} {'':>8s} {'':>7s} {'':>7s} {'':>4s} {'':>6s} "
                  f"{load_txt:>15s} {busy:>5s} {'-':>6s} {'':>8s}  {'; '.join(r['flags'])}")
        for i, (name, m) in enumerate(sorted(r["monitors"].items())):
            if m.get("error"):
                print(f"{r['cell']:26s} {name:9s} {m.get('refresh_hz') or 0:7.2f}  {m['error']}")
                continue
            e = m["err_ms"]
            near = m.get("near_clock_event")
            near_txt = f"{near['missed']}/{near['wrong']}" if near else "-"
            flags = "; ".join(x for x in r["flags"] if not x.startswith(tuple(n + ":" for n in r["monitors"]))
                              or x.startswith(name + ":")) if i == 0 else "; ".join(x for x in r["flags"] if x.startswith(name + ":"))
            print(f"{r['cell'] if i == 0 else '':26s} {name:9s} {m['refresh_hz']:7.2f} {m['frames_scored']:6d} "
                  f"{m['wrong_per_min']:9.2f} {m['missed_per_min']:8.2f} {e['p99']:7.3f} {e['p999']:7.3f} "
                  f"{(m['overlay_share'] or 0) * 100:3.0f}% {m['replan_skips'] + m['replan_repeats']:6d} "
                  f"{load_txt if i == 0 else '':>15s} {busy if i == 0 else '':>5s} {f'{sm:.0f}' if sm is not None and i == 0 else '':>6s} "
                  f"{near_txt:>8s}  {flags}")
    print("\nwrong/min: film frames shown for another number of refreshes than planned, per minute of steady play; miss/min: "
          "refreshes with no new present; err: present-interval error (ms; one missed refresh = +1 period); replan: engine "
          "skip/repeat of its planned refresh; near clk: missed refreshes / wrong frames within 0.25 s of a clock event")


def decision_view(results):
    """Per frame latency and priority: each load's worst monitor, so the largest kernel length that stays clean shows."""
    groups = {}
    for r in results:
        p = r["params"]
        if not p.get("load") or not r["monitors"] or any(m.get("error") for m in r["monitors"].values()):
            continue
        w = sum(m["wrong_per_min"] or 0 for m in r["monitors"].values())
        mi = sum(m["missed_per_min"] or 0 for m in r["monitors"].values())
        groups.setdefault((p.get("latency"), p.get("priority")), []).append((p["load"], w, mi))
    if not groups:
        return
    print("\nper frame latency and GPU priority: load -> wrong/min, missed/min (both monitors summed)")
    for (lat, pri), rows in sorted(groups.items(), key=lambda x: (str(x[0][0]), str(x[0][1]))):
        print(f"  latency {lat}, priority {pri}: " + ", ".join(f"{l} {w:.2f}/{m:.2f}" for l, w, m in rows))


def check_truth(results, run):
    ok = True
    for r in results:
        truth = read_json(run / r["cell"] / "fake-truth.json") if run.name != r["cell"] else read_json(run / "fake-truth.json")
        if truth is None:
            continue
        for name, t in truth["monitors"].items():
            m = r["monitors"].get(name, {})
            got = {k: m.get(k) for k in t}
            good = got == t
            ok &= good
            print(f"truth {r['cell']} {name}: {'PASS' if good else 'FAIL'} expected {t} got {got}")
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run", type=Path)
    ap.add_argument("--skip-seconds", type=float, default=2.0, help="film start to the steady part (default 2)")
    ap.add_argument("--tail-seconds", type=float, default=0.5, help="left out before the last present (default 0.5)")
    ap.add_argument("--no-csv", action="store_true", help="do not write presents-<monitor>.csv")
    ap.add_argument("--check-truth", action="store_true", help="compare with fake-truth.json (dry runs); exit 1 on a mismatch")
    a = ap.parse_args()
    results = []
    for d in cells_of(a.run):
        r, _ = score_cell(d, a.skip_seconds, a.tail_seconds, not a.no_csv)
        results.append(r)
    summary = {"schema": "panorama.e1.summary/1", "run": str(a.run), "skip_seconds": a.skip_seconds,
               "tail_seconds": a.tail_seconds, "cells": results}
    (a.run / "e1-summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print_table(results)
    decision_view(results)
    print(f"\nwrote {a.run / 'e1-summary.json'}")
    if a.check_truth:
        return 0 if check_truth(results, a.run) else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
