"""Score a mixed-refresh pacing log from dda-present-log.py.

Two clocks per screen, both in QPC time:
- composition: when Windows produced a new desktop image for the screen, and
  which video frame it held (Desktop Duplication);
- refresh: when the screen started a new refresh (WaitForVBlank).
A refresh shows the newest image composed before it (minus a latch margin, swept
over 0-2 ms to show how sensitive the result is). From that the analysis gets,
per screen, how many refreshes each video frame stayed on screen, and across
screens, how much later each frame reached the external than the laptop. Panel
processing delay after the refresh starts is not included (that needs a camera).

Usage: python analyze-span-pacing.py PRESENT_LOG_JSON [--mpv-log LOG] [--report OUT_JSON]
"""
import argparse
import json
import re
from collections import Counter
from pathlib import Path

import numpy as np

LATCH_MS = (0.0, 1.0, 2.0)


def stats(values):
    a = np.asarray(values, float)
    if a.size == 0:
        return None
    pick = lambda q: round(float(np.percentile(a, q)), 3)
    return {"n": int(a.size), "min": round(float(a.min()), 3), "p5": pick(5), "median": pick(50), "p95": pick(95),
            "max": round(float(a.max()), 3), "mean": round(float(a.mean()), 3), "std": round(float(a.std()), 3)}


def grid(times):
    """Fitted period (ms) and off-grid residuals of a sequence of tick times."""
    times = np.asarray(times, float)
    if times.size < 3:
        return None, np.zeros(0)
    nominal = float(np.median(np.diff(times)))
    k = np.concatenate([[0.0], np.cumsum(np.round(np.diff(times) / nominal))])
    slope, intercept = np.polyfit(k, times, 1)
    return float(slope), times - (intercept + slope * k)


def composition(out, t0, freq):
    """Sorted (time ms, frame index) of every readable desktop update, plus first-appearance holds."""
    ev = sorted(e for e in out["events"] if e[3] >= 0)
    times = np.array([(e[0] - t0) * 1000 / freq for e in ev])
    index = np.array([e[3] for e in ev])
    first, merged_first = {}, set()
    for (present, _acq, merged, n), t in zip(ev, times):
        if n not in first:
            first[n] = t
            if merged > 1:
                merged_first.add(n)  # true first appearance may be earlier than logged
    period, resid = grid(times)
    holds = [first[n + 1] - first[n] for n in sorted(first)
             if n + 1 in first and not ({n, n + 1} & merged_first)]
    return times, index, {
        "updates": len(out["events"]), "frames_seen": len(first), "merged_updates": sum(e[2] > 1 for e in out["events"]),
        "period_ms": round(period, 4) if period else None, "off_grid_ms": stats(np.abs(resid)),
        "first_hold_ms": stats(holds),
        "first_hold_periods": dict(sorted(Counter(int(round(h / period)) for h in holds).items())) if period else None,
    }


def shown_per_refresh(vblanks, comp_t, comp_i, latch):
    """(refresh time, frame index) for every refresh after the first composed image."""
    pos = np.searchsorted(comp_t, vblanks - latch, side="right") - 1
    ok = pos >= 0
    return vblanks[ok], comp_i[pos[ok]]


def on_screen(vb_t, shown):
    """Refresh counts per video frame (run lengths, partial first/last runs dropped) and first refresh times."""
    runs, first = [], {}
    start = 0
    for i in range(1, len(shown) + 1):
        if i == len(shown) or shown[i] != shown[start]:
            runs.append((int(shown[start]), i - start))
            first.setdefault(int(shown[start]), float(vb_t[start]))
            start = i
    inner = runs[1:-1]
    skipped = sum(max(0, b - a - 1) for (a, _), (b, _) in zip(runs, runs[1:]))
    backwards = sum(b < a for (a, _), (b, _) in zip(runs, runs[1:]))
    return inner, first, skipped, backwards


def mpv_counters(path):
    if not path or not path.exists():
        return {}
    return dict(re.findall(r"counter (\S+)=(.*)", path.read_text(errors="replace")))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("log", type=Path)
    parser.add_argument("--mpv-log", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    log = json.loads(args.log.read_text())
    freq = log["qpc_freq"]
    outputs = {o["barcode"]: o for o in log["outputs"] if o.get("events")}
    if not outputs:
        raise SystemExit("no screen updates were logged")
    t0 = min(o["events"][0][0] for o in outputs.values())
    screens, firsts = {}, {latch: {} for latch in LATCH_MS}
    for name, out in outputs.items():
        comp_t, comp_i, comp = composition(out, t0, freq)
        vb = (np.array(out.get("vblanks", []), float) - t0) * 1000 / freq
        period, resid = grid(vb)
        screen = {"refresh_hz_nominal": round(out["refresh"][0] / out["refresh"][1], 4),
                  "refresh_hz_measured": round(1000 / period, 4) if period else None,
                  "refresh_off_grid_ms": stats(np.abs(resid)), "vblank_failures": out.get("vblank_failures"),
                  "composition": comp, "on_screen": {}}
        for latch in LATCH_MS:
            vb_t, shown = shown_per_refresh(vb, comp_t, comp_i, latch)
            runs, first, skipped, backwards = on_screen(vb_t, shown)
            firsts[latch][name] = first
            counts = [c for _, c in runs]
            screen["on_screen"][f"latch_{latch:g}ms"] = {
                "refreshes_per_frame": dict(sorted(Counter(counts).items())),
                "first_36": counts[:36], "frames_skipped": skipped, "backwards_steps": backwards}
        screens[name] = screen
    offsets = {}
    for latch, first in firsts.items():
        if "laptop" in first and "external" in first:
            common = sorted(set(first["laptop"]) & set(first["external"]))
            offsets[f"latch_{latch:g}ms"] = stats([first["external"][n] - first["laptop"][n] for n in common])
    comp_offset = None
    if {"laptop", "external"} <= outputs.keys():
        fl = {}
        for name in ("laptop", "external"):
            ct, ci, _ = composition(outputs[name], t0, freq)
            fl[name] = {}
            for t, n in zip(ct, ci):
                fl[name].setdefault(int(n), t)
        common = sorted(set(fl["laptop"]) & set(fl["external"]))
        comp_offset = stats([fl["external"][n] - fl["laptop"][n] for n in common])
    counters = mpv_counters(args.mpv_log)
    summary = {
        "screens": {name: {"refresh_hz_measured": s["refresh_hz_measured"],
                           "composition_hz": round(1000 / s["composition"]["period_ms"], 3)
                           if s["composition"]["period_ms"] else None,
                           "refreshes_per_frame_latch_1ms": s["on_screen"]["latch_1ms"]["refreshes_per_frame"],
                           "first_36_latch_1ms": s["on_screen"]["latch_1ms"]["first_36"],
                           "frames_skipped": s["on_screen"]["latch_1ms"]["frames_skipped"]}
                    for name, s in screens.items()},
        "composition_offset_external_minus_laptop_ms": comp_offset,
        "display_offset_external_minus_laptop_ms_latch_1ms": offsets.get("latch_1ms"),
        "mpv": {k: counters.get(k) for k in ("display-fps", "estimated-display-fps", "display-names", "frame-drop-count",
                                             "vo-delayed-frame-count", "mistimed-frame-count", "vsync-jitter",
                                             "video-speed-correction", "interpolation")},
    }
    text = json.dumps({"summary": summary, "screens": screens, "display_offsets": offsets,
                       "mpv_counters": counters}, indent=1)
    if args.report:
        args.report.write_text(text)
    print(text)


if __name__ == "__main__":
    main()
