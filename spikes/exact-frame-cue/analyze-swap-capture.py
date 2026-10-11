"""Score a hot-swap trial capture against truth.json and the planner log.

On every presented image the shader paints the cue it applied (OUTPUT stage,
gray stripe) and the version of the cue table it used (black/white bars after a
fixed white-black pair; no pair = the image was drawn without the shader). With
--main-stripe it also paints the cue a MAIN-stage pass baked into the frame
before scaling. The barcode says which source frame is on screen; the planner
log says what each table version holds and when it was requested.

Reported:
- applied_mismatch: the OUTPUT cue differs from the shown version's entry for
  the frame's shot (the lookup itself went wrong).
- mid_shot_changes: in normal playback the cue changed between two captures of
  the same shot: a swap changed a shot while it was on screen.
- plan_mismatch_frames (trials without seeks): a frame's cue differs from the
  newest version committed before its shot started: a swap landed too late.
- no_shader_frames: frames shown without the shader.
- main: MAIN-stage cue against the OUTPUT cue in the same image ("blended" =
  the bars mix two frames, as interpolation does).
- swaps: frames and ms from the planner's request to the first image using
  the new version, and the ms until mpv reported drawing it (vo-passes).
- pacing: gaps over 1.5 frame durations between consecutive frames, and frames
  never seen in normal playback, overall and in the second after each swap
  (the first 12 frames after the start or a seek are reported apart).
- compiles: libplacebo shader-compile log lines after playback started, per swap.

Usage: python analyze-swap-capture.py TRUTH_JSON CAPTURE_MKV PLAN_LOG [--mpv-log LOG]
                                      [--seeks] [--report OUT_JSON]
"""
import argparse
import importlib.util
import json
import re
import sys
from bisect import bisect_right
from fractions import Fraction
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
HITCH = 1.5  # gap between consecutive frames, in frame durations, that counts as a hitch
SETTLE = 12  # frames after the start or a seek whose gaps are reported apart
LOG_LINE = re.compile(r"^\[\s*([\d.]+)\]\[(\w)\]\[([^\]]+)\] (.*)$")
COMPILE = re.compile(r"Spent ([\d.]+) ms (translating GLSL to SPIR-V|translating SPIR-V to HLSL|translating HLSL to DXBC)")


def load_sibling(name):
    """Import a hyphenated sibling script as a module."""
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_bars(frame, x0, width, pitch, bits, scale=1):
    """Integer painted as black/white bars, most significant first, or None when a bar is
    neither black nor white (no shader, or a blend of two frames)."""
    value = 0
    for b in range(bits):
        left, right = (x0 + b * pitch) * scale, (x0 + b * pitch + width) * scale
        margin = (right - left) // 4
        level = float(frame[8:-8, left + margin:right - margin, 1].mean()) / 255
        if 0.25 < level < 0.75:
            return None
        value = value * 2 + int(level >= 0.75)
    return value


def stats(values):
    values = [v for v in values if v is not None]
    if not values:
        return None
    return {"count": len(values), "min": min(values), "median": float(np.median(values)), "max": max(values)}


def mpv_log_summary(path):
    """Shader compiles after unpause attributed to the swap before them, plus errors and counters."""
    events, unpause = [], None
    for line in path.read_text(errors="replace").splitlines():
        m = LOG_LINE.match(line)
        if m:
            events.append((float(m.group(1)), m.group(2), m.group(3), m.group(4)))
    unpause = next((t for t, _, _, text in events if text.startswith("cue-unpause")), None)
    swaps = [(t, text) for t, _, _, text in events if text.startswith("cue-swap")]
    per_swap, startup_ms = [{"log_time": t, "swap": text, "compile_ms": 0.0, "compile_steps": 0} for t, text in swaps], 0.0
    for t, _, _, text in events:
        m = COMPILE.search(text)
        if not m:
            continue
        if unpause is None or t < unpause:
            startup_ms += float(m.group(1))
            continue
        owner = next((s for s in reversed(per_swap) if s["log_time"] <= t), None)
        if owner is not None:
            owner["compile_ms"] = round(owner["compile_ms"] + float(m.group(1)), 3)
            owner["compile_steps"] += 1
    playing = [s for s in per_swap if unpause is not None and s["log_time"] >= unpause]
    return {
        "startup_compile_ms": round(startup_ms, 1),
        "swaps_logged": len(playing),
        "swaps_with_compile": sum(s["compile_steps"] > 0 for s in playing),
        "compile_ms_per_swap": [s["compile_ms"] for s in playing],
        "errors_after_start": [f"{t:.3f} [{prefix}] {text}" for t, level, prefix, text in events
                               if level == "e" and unpause is not None and t >= unpause][:20],
        "counter_changes": [text for _, _, _, text in events if text.startswith("cue-counter")][:40],
        "final_counters": dict(re.findall(r"counter (\S+)=(\S*)", "\n".join(text for *_, text in events))),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("truth", type=Path)
    parser.add_argument("capture", type=Path)
    parser.add_argument("plan_log", type=Path)
    parser.add_argument("--mpv-log", type=Path)
    parser.add_argument("--seeks", action="store_true", help="trial had seeks: skip the plan check")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    capture = load_sibling("analyze-capture")
    shaders = load_sibling("make-cue-shader")
    truth = json.loads(args.truth.read_text())
    layout, shot_starts = truth["layout"], truth["shot_starts"]
    entries = [json.loads(line) for line in args.plan_log.read_text().splitlines() if line.strip()]
    plan = entries[0]
    tables = {0: plan["levels"]}            # version -> level per shot
    applied = []                            # versions mpv should have kept (not broken)
    for e in entries:
        if e["type"] == "version":
            tables[e["k"]] = e["levels"]
            if not e["broken"]:
                applied.append(e)
        elif e["type"] == "same-path":
            tables[e["k"]] = None           # must never show: mpv keeps the table it parsed
    known = sorted(tables)

    def shot(n):
        return bisect_right(shot_starts, n) - 1

    def unwrap(version_mod, last):
        candidates = [k for k in known if k % 16 == version_mod]
        return min(candidates, key=lambda k: (abs(k - last), -k)) if candidates else None

    rows, unreadable, last_version = [], 0, 0   # rows: (time, frame, cue, version, main cue)
    for t, frame in capture.capture_frames(args.capture):
        n = capture.read_index(frame, layout)
        if n is None or n >= truth["frames"]:
            unreadable += 1
            continue
        marker = read_bars(frame, **shaders.VERSION_BARS)
        if marker is None or marker >> 4 != shaders.VERSION_SYNC:
            rows.append((t, n, None, None, None))
            continue
        version = unwrap(marker & 15, last_version)
        last_version = last_version if version is None else version
        main_level = (read_bars(frame, **shaders.MAIN_BARS, scale=layout["screen_scale"])
                      if plan["main_stripe"] else None)
        rows.append((t, n, capture.read_level(frame, layout)[0], version, main_level))

    shown = [r for r in rows if r[3] is not None]
    applied_mismatch = [{"frame": n, "version": v, "shown": lv,
                         "table": None if tables.get(v) is None else tables[v][shot(n)]}
                        for _, n, lv, v, _ in shown if tables.get(v) is None or lv != tables[v][shot(n)]]

    mid_shot, prev = [], None
    for _, n, lv, v, _ in shown:
        if prev and 0 <= n - prev[0] <= 1 and shot(n) == shot(prev[0]) and lv != prev[1]:
            mid_shot.append({"frame": n, "from": prev[1], "to": lv, "versions": [prev[2], v]})
        prev = (n, lv, v)

    plan_mismatch = None
    if not args.seeks:
        commits = [(e["commit"], e["k"]) for e in applied]

        def planned(n):
            s = shot(n)
            k = max((k for c, k in commits if c <= plan["cue_starts"][s]), default=0)
            return tables[k][s]
        plan_mismatch = sorted({n for _, n, lv, _, _ in shown if lv != planned(n)})

    main = None
    if plan["main_stripe"]:
        cut_frames = set(shot_starts[1:])
        clean = [(n, lv, m) for _, n, lv, _, m in shown if m is not None]
        wrong = sorted({n for n, lv, m in clean if m != lv})
        main = {"captures": len(shown), "blended": len(shown) - len(clean), "mismatch_captures": sum(m != lv for _, lv, m in clean),
                "mismatch_frames": len(wrong), "mismatch_on_cut_frames": len(set(wrong) & cut_frames),
                "cut_frames_checked": len({n for n, _, _ in clean} & cut_frames), "mismatch_examples": wrong[:20]}

    frame_s = 1 / float(Fraction(truth["fps"]))
    pts = [p - truth["start_time"] for p in truth["frame_pts"]]
    first_capture = {}
    for t, n, *_ in rows:
        first_capture.setdefault(n, t)
    first_version = {}
    for t, n, _, v, _ in shown:
        first_version.setdefault(v, (t, n))
    swaps = []
    for e in entries:
        if e["type"] not in ("version", "same-path"):
            continue
        request = bisect_right(pts, e["request_pos"]) - 1
        seen = first_version.get(e["k"])
        swap = {"k": e["k"], "kind": "broken" if e.get("broken") else e["type"], "request_frame": request,
                "ack_ms": e.get("ack_ms"), "confirm_ms": e.get("confirm_ms", e.get("drawn_ms")),
                "first_frame": seen[1] if seen else None}
        if seen and request in first_capture:
            swap["latency_frames"] = seen[1] - request
            swap["latency_ms"] = round((seen[0] - first_capture[request]) * 1000, 1)
        swaps.append(swap)

    # Frame-to-frame gaps in normal playback. The first SETTLE frames after the start or a seek
    # are kept apart: the paused start and seek landings are not swap effects.
    gaps, settling, missing, cur, cur_t, since_jump = [], [], [], None, None, 0
    for t, n, *_ in rows:
        if n == cur:
            continue
        if cur is not None and n == cur + 1:
            (gaps if since_jump >= SETTLE else settling).append((n, t - cur_t))
            since_jump += 1
        elif cur is not None and cur + 1 < n <= cur + 5:
            missing.extend(range(cur + 1, n))
            since_jump += 1
        else:
            since_jump = 0
        cur, cur_t = n, t
    hitches = [(n, round(dt * 1000, 1)) for n, dt in gaps if dt > HITCH * frame_s]
    windows = [range(s["request_frame"] + 1, s["request_frame"] + 1 + round(1 / frame_s)) for s in swaps]
    near = lambda n: any(n in w for w in windows)

    report = {
        "captures": len(rows) + unreadable, "unreadable_barcode": unreadable,
        "frames_seen": len({n for _, n, *_ in rows}), "frames_total": truth["frames"],
        "no_shader_frames": sorted({n for _, n, _, v, _ in rows if v is None}),
        "applied_mismatch": len(applied_mismatch), "applied_mismatch_examples": applied_mismatch[:20],
        "mid_shot_changes": len(mid_shot), "mid_shot_examples": mid_shot[:20],
        "plan_mismatch_frames": None if plan_mismatch is None else len(plan_mismatch),
        "plan_mismatch_examples": None if plan_mismatch is None else plan_mismatch[:20],
        "versions_shown": sorted(v for v in first_version),
        "swaps": swaps,
        "swap_latency_frames": stats([s.get("latency_frames") for s in swaps if s["kind"] == "version"]),
        "swap_latency_ms": stats([s.get("latency_ms") for s in swaps if s["kind"] == "version"]),
        "swap_confirm_ms": stats([s["confirm_ms"] for s in swaps if s["kind"] == "version"]),
        "reverts": [{k: e[k] for k in ("k", "by", "detect_ms") if k in e} for e in entries if e["type"] == "revert"],
        "pacing": {"frame_ms": round(frame_s * 1000, 3), "gaps": len(gaps),
                   "gap_ms": stats([round(dt * 1000, 1) for _, dt in gaps]),
                   "hitches": len(hitches), "hitches_near_swaps": sum(near(n) for n, _ in hitches),
                   "hitch_examples": hitches[:20],
                   "settling_hitches": [(n, round(dt * 1000, 1)) for n, dt in settling if dt > HITCH * frame_s],
                   "missing_frames": len(missing),
                   "missing_near_swaps": sum(near(n) for n in missing), "missing_examples": missing[:20]},
        "main": main,
        "mpv_log": mpv_log_summary(args.mpv_log) if args.mpv_log else None,
    }
    text = json.dumps(report, indent=1)
    if args.report:
        args.report.write_text(text)
    print(text)


if __name__ == "__main__":
    main()
