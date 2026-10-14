"""Stand-in for perf1 in the E1 runner's dry run: writes perf1's logs (monitor-N-nearest.json, perf1-cuelog.json, gpu-share.json)
for Setup A's two monitors (laptop 240 Hz, external 96 Hz master; 23.976 fps locked: 10 and 4 refreshes per frame) with known
faults, and fake-truth.json with the counts e1-analyse.py must report. The truth is counted on a simulated screen (which film
frame is up on every refresh), not with the analysis' method.

Faults per monitor, at positions drawn from --seed, all well inside the steady part:
  missed refresh   a present reaches the screen one refresh late (the one before stays up); as in perf1 the service after the
                   late flip plans one refresh further: with --frame-latency N that is the N-th present after it
  replan pair      the engine plans k + 2, then k + 2 again (an early wake-up against its service margin), nothing missed; one
                   pair lands on a frame change (two wrong frames), one inside a frame (none)
  statistics gap   GetFrameStatistics missed a present (no row), always where the neighbours leave no doubt
  start            --frame-latency N: the first refresh is presented N times (the waitable is signalled N times at once)

Takes perf1's command line (only --out, --seconds, --frame-latency, --gpu-priority matter; the rest is ignored), runs for
--seconds of wall time like the real player, then writes the logs.
  python -I -X utf8 e1-fake-player.py --out DIR --seconds 5 [--frame-latency 1] [--gpu-priority default] [--fake-seed 1]
"""
import argparse
import json
import random
import sys
import time
from pathlib import Path

FPS = 24000 / 1001
MONITORS = [  # slot = DXGI index; the external is the master (slowest)
    {"index": 0, "name": "DISPLAY1", "hz": 240.0, "hold": 10, "start_k": 2500, "counter0": 4233000},
    {"index": 1, "name": "DISPLAY2", "hz": 96.0, "hold": 4, "start_k": 1000, "counter0": 1200000},
]
MASTER = 1


def simulate(mon, frames, latency, t0, rng, skip_s, tail_s, seconds):
    """Presents of one monitor ([planned k, screen counter]), their call times, the presents with a statistics row, and the
    truth counted on the simulated screen."""
    period, hold, k0 = 1.0 / mon["hz"], mon["hold"], mon["start_k"]
    k_last = k0 + hold * frames - 1
    wake = lambda k: t0 + (k - k0) * period          # the engine's grid: refresh k0 wakes at film start
    start_of = lambda f: k0 + hold * f
    # faults on frames well inside the steady part, two frames apart at least
    f_lo, f_hi = int((skip_s + 0.4) * FPS) + 1, int((seconds - tail_s - 0.6) * FPS) - 1
    picked = rng.sample(range(f_lo, f_hi, 2), 5)
    miss_at = {start_of(picked[0]), start_of(picked[1]) + hold // 2, start_of(picked[2]) + hold - 1}  # first, middle, last refresh
    replan_at = {start_of(picked[3]), start_of(picked[4]) + hold // 2}  # a pair onto a frame change, one inside a frame

    pres, c, k = [], mon["counter0"] + k0 - 1, k0
    for _ in range(latency):                          # the first refresh presented `latency` times
        c += 1
        pres.append([k, c])
    pending_skip, repeat_next, late_idx = set(), False, []
    while k < k_last:
        idx = len(pres)
        if repeat_next:
            nk, repeat_next = k, False                # the second of a replan pair: the same refresh again
        else:
            nk = k + 1 + (idx in pending_skip)        # after a late flip the service plans one refresh further
            if nk + 1 in replan_at:                   # an early wake-up: one refresh further, then the same again
                nk, repeat_next = nk + 1, True
                replan_at.discard(nk)
        late = nk in miss_at
        miss_at.discard(nk)
        c += 1 + late
        pres.append([nk, c])
        if late:
            late_idx.append(idx)
            pending_skip.add(idx + latency)           # the service at the delayed wake-up: `latency` presents later
        k = nk
    # the frame each present shows (largest start <= k), the screen, the truth
    frame_of = lambda kk: min(frames - 1, max(0, (kk - k0) // hold))
    calls = [wake(p[0]) - period + 1e-4 + 1e-7 * i for i, p in enumerate(pres)]
    on_screen = {}                                    # counter -> frame
    for a, b in zip(pres, pres[1:] + [[None, pres[-1][1] + 1]]):
        for cc in range(a[1], b[1]):
            on_screen[cc] = frame_of(a[0])
    lo_t, hi_t = t0 + skip_s, calls[-1] - tail_s
    steady = [i for i in range(len(pres)) if lo_t <= calls[i] <= hi_t]
    # statistics gaps: three presents inside the steady part, at least three presents away from any late one
    far = [i for i in steady[5:-5] if all(abs(i - m) >= 3 for m in late_idx)]
    gaps = set(rng.sample(far, 3))
    rows = [i for i in range(len(pres)) if i not in gaps]
    row_set = set(rows)
    st_rows = [i for i in steady if i in row_set]
    c_lo, c_hi = pres[st_rows[0]][1], pres[st_rows[-1]][1]
    shown_counters = {p[1] for p in pres}
    missed = sum(1 for c in range(c_lo, c_hi + 1) if c not in shown_counters)
    first_i = {}
    for i, p in enumerate(pres):
        first_i.setdefault(frame_of(p[0]), i)
    order = sorted(first_i, key=first_i.get)
    wrong = scored = 0
    for f, g in zip(order, order[1:]):
        if first_i[f] < steady[0] or first_i[g] > steady[-1]:
            continue
        scored += 1
        shown = sum(1 for c, fr in on_screen.items() if fr == f)
        wrong += shown != hold
    truth = {"missed": missed, "wrong": wrong, "frames_scored": scored}
    return pres, calls, rows, truth, wake, period


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--seconds", type=float, default=5.0)
    ap.add_argument("--frame-latency", type=int, default=1)
    ap.add_argument("--gpu-priority", default="default")
    ap.add_argument("--fake-seed", type=int, default=1)
    ap.add_argument("--fake-skip-seconds", type=float, default=2.0, help="the analysis' --skip-seconds")
    ap.add_argument("--fake-tail-seconds", type=float, default=0.5, help="the analysis' --tail-seconds")
    a, _ignored = ap.parse_known_args()
    if a.seconds < a.fake_skip_seconds + a.fake_tail_seconds + 2.0:
        raise SystemExit("fake player: --seconds too short for a steady part with faults in it")
    t_start = time.perf_counter()
    print(f"perf1 stand-in: {a.seconds} s, frame latency {a.frame_latency}, priority {a.gpu_priority}", flush=True)
    time.sleep(a.seconds)
    rng = random.Random(a.fake_seed)
    t0 = t_start + 1.5
    frames = int(a.seconds * FPS) + 1
    a.out.mkdir(parents=True, exist_ok=True)
    refreshes, truth = {}, {}
    for slot, mon in enumerate(MONITORS):
        pres, calls, rows, tr, wake, period = simulate(mon, frames, a.frame_latency, t0, rng, a.fake_skip_seconds,
                                                       a.fake_tail_seconds, a.seconds)
        truth[mon["name"]] = tr
        hold, k0 = mon["hold"], mon["start_k"]
        frame_of = lambda kk: min(frames - 1, max(0, (kk - k0) // hold))
        first_id = 2
        presents = [[first_id + i, calls[i], calls[i] + 2e-4, wake(p[0]), frame_of(p[0])] for i, p in enumerate(pres)]
        screen_t = lambda c: t0 + (c - mon["counter0"] - k0) * period - 1.5e-4
        stats = [[first_id + i, pres[i][1], pres[i][1], screen_t(pres[i][1]), calls[min(len(calls) - 1, i + a.frame_latency)] + 2e-4]
                 for i in rows]
        k_first, k_last = pres[0][0] - 400, pres[-1][0] + 5
        log = {"monitor": mon["index"], "name": mon["name"], "rect": [0, 0, 0, 0], "width_mm": 0, "left_mm": 0, "mode": "nearest",
               "fps": FPS, "t0": t0, "master": MASTER, "error": "", "composition_modes": {"1": len(presents)},
               "vblanks": [wake(k) - 1e-4 for k in range(k_first, k_last)], "presents": presents, "stats": stats,
               "render_cpu_ms": [0.05] * len(presents), "render_gpu_ms": [0.3] * len(presents),
               "lead_ms": [period * 1e3] * len(presents), "perf1": {"fake": True, "hold": MONITORS[MASTER]["hold"]}}
        (a.out / f"monitor-{mon['index']}-nearest.json").write_text(json.dumps(log), encoding="utf-8")
        refreshes[str(slot)] = [[p[0], MONITORS[MASTER]["start_k"] + frame_of(p[0]) * MONITORS[MASTER]["hold"], calls[i] - 5e-5,
                                 wake(p[0]), frame_of(p[0]) / FPS, 0, frame_of(p[0]), 1, 0, 0.0, frame_of(p[0]), 0, 0, 0, 0]
                                for i, p in enumerate(pres)]
    starts = [[0, f, MONITORS[1]["start_k"] + 4 * f, int(f == 0), 0, MONITORS[0]["start_k"] + 10 * f, MONITORS[1]["start_k"] + 4 * f]
              for f in range(frames + 1)]
    cue = {"schema": "panorama.perf1.cuelog/1", "n_monitors": 2, "master": MASTER,
           "monitors": [{"index": m["index"], "name": m["name"], "rate_hz": m["hz"]} for m in MONITORS],
           "epochs": [{"ep": 0, "J0": MONITORS[1]["start_k"], "start_k": [MONITORS[0]["start_k"], MONITORS[1]["start_k"]]}],
           "start_fields": ["ep", "ord", "j", "cut", "shift", "s0", "s1"], "starts": starts,
           "refresh_fields": ["k", "j", "svc", "target", "pts", "ep", "ord", "vid", "sidx", "sstart", "drawn", "ach", "lid", "flags",
                              "dec"], "refreshes": refreshes, "stats": {"fake": True}}
    (a.out / "perf1-cuelog.json").write_text(json.dumps(cue), encoding="utf-8")
    high = a.gpu_priority == "high"
    share = {"schema": "panorama.perf1.gpu-share/1",
             "frame_latency": {"asked": a.frame_latency, "buffers": max(3, a.frame_latency + 1),
                               "monitors": [{"name": m["name"], "set_hr": "0x00000000", "reads": a.frame_latency} for m in MONITORS]},
             "gpu_priority": {"asked": a.gpu_priority, "process_class": {"set": high, "set_status": "0x00000000",
                                                                          "reads": 4 if high else 2, "reads_name": "HIGH" if high else "NORMAL",
                                                                          "read_status": "0x00000000"},
                              "gpu_thread": {"set": high, "set_value": 7, "set_hr": "0x00000000", "reads": 7 if high else 0,
                                             "read_ok": True}}}
    (a.out / "gpu-share.json").write_text(json.dumps(share), encoding="utf-8")
    (a.out / "fake-truth.json").write_text(json.dumps({"monitors": truth, "seed": a.fake_seed}, indent=1), encoding="utf-8")
    print(f"perf1 stand-in: wrote logs to {a.out}; truth {truth}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
