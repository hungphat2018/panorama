"""Judge of the performer's audio sync, from audio.csv alone (format: the header of src/perf1-audio-log.c).

  python -I audio-analyze.py audio.csv [--min-steady N] [--max-resyncs N] [--min-epochs N] [--json OUT]
  python -I audio-analyze.py --selftest

Terms. An *epoch* is the stretch of playing after one film-clock restart (a `start` row; its `epoch` row is the same
placement as best known half a second later). A *break* is a `seek` or `pause` row. *Steady state* is 2 s after an epoch
starts until the next break. The error is the film time sounding minus the film clock at that moment (+ = the sound is ahead).

Assertions (each is a named check; the verdict is PASS only if none fails):
  P1  placement   every epoch's first sample sounded within 5 ms of where the film clock wants it (the `epoch` rows)
  E0  start       no error measurement beyond 40 ms (the point where the sound would be moved) in the first second of an epoch
  E1  settle      from 1 s after an epoch starts to its end (or the next break) every error measurement is within 5 ms
  E2  steady      the 99th percentile of |error| in steady state is below 2 ms (at least --min-steady measurements, default 300)
  U1  underruns   none from 1 s after an epoch starts to the next break
  R1  resyncs     the sound is moved (a `resync` row) only within 1.5 s after an epoch starts, and at most --max-resyncs times
                  in the whole run (default: unlimited, but still only after epoch starts)
  C1  coverage    at least --min-epochs epochs played sound (default 1) and every `start` has its `epoch` row
Also reported, not judged: late starts (the head of the sound skipped because the decoder was slower than the restart),
the largest correction, the first-second error, the device clock's spread.
"""
import argparse
import csv
import json
import math
import sys
from pathlib import Path

SETTLE_S = 1.0
STEADY_S = 2.0
RESYNC_WINDOW_S = 1.5
LIMIT_MS = 5.0
HARD_MS = 40.0
P99_LIMIT_MS = 2.0


def num(x, default=float("nan")):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def load(path):
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f, restkey="more"):
            r["t"] = num(r.get("t"))
            for k in ("err_ms", "comp_ppm", "film_t", "latency_ms", "resid_ms", "n", "resyncs", "underruns"):
                r[k] = num(r.get(k))
            if r.get("more"):
                r["note"] = (r.get("note") or "") + "," + ",".join(r["more"])
            rows.append(r)
    return rows


def percentile(v, p):
    if not v:
        return float("nan")
    v = sorted(v)
    k = (len(v) - 1) * p / 100.0
    lo, hi = int(math.floor(k)), int(math.ceil(k))
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def note_value(note, key):
    for part in (note or "").split(";"):
        if part.startswith(key + "="):
            return num(part[len(key) + 1:])
    return float("nan")


def analyze(rows, min_steady=300, max_resyncs=None, min_epochs=1):
    starts = [r for r in rows if r["kind"] == "start"]
    epochs = [r for r in rows if r["kind"] == "epoch"]
    errs = [r for r in rows if r["kind"] == "err"]
    breaks = sorted(r["t"] for r in rows if r["kind"] in ("seek", "pause"))
    resyncs = [r for r in rows if r["kind"] == "resync"]
    underruns = [r for r in rows if r["kind"] == "underrun"]
    fails, notes = [], []

    def end_of(t0):
        later = [b for b in breaks if b > t0]
        return later[0] if later else float("inf")

    # the break that ends an epoch is stamped by the engine slightly before the audio thread sees it: no tolerance needed,
    # because err rows are only written while playing and playing stops when the note is handled
    windows = [(s["t"], end_of(s["t"])) for s in starts]

    # C1
    if len(starts) < min_epochs:
        fails.append(f"C1 coverage: {len(starts)} epochs played sound, want at least {min_epochs}")
    if len(epochs) < len(starts):
        fails.append(f"C1 coverage: {len(starts)} starts but {len(epochs)} placement rows")

    # P1
    bad = [e for e in epochs if not abs(e["err_ms"]) < LIMIT_MS]
    for e in bad[:5]:
        fails.append(f"P1 placement: epoch {int(e['epoch'])} first sample {e['err_ms']:+.2f} ms off")
    if len(bad) > 5:
        fails.append(f"P1 placement: ... {len(bad)} epochs in all")

    # E1, E2
    first_second, start_bad, settled_bad, steady = [], [], [], []
    for (t0, t1) in windows:
        for r in errs:
            if r["t"] < t0 or r["t"] >= t1:
                continue
            dt = r["t"] - t0
            if dt < SETTLE_S:
                first_second.append(abs(r["err_ms"]))
                if not abs(r["err_ms"]) < HARD_MS:
                    start_bad.append((dt, r["err_ms"]))
            else:
                if not abs(r["err_ms"]) < LIMIT_MS:
                    settled_bad.append((dt, r["err_ms"]))
                if dt >= STEADY_S:
                    steady.append(abs(r["err_ms"]))
    for dt, e in start_bad[:3]:
        fails.append(f"E0 start: error {e:+.2f} ms at {dt:.2f} s after an epoch start")
    for dt, e in settled_bad[:5]:
        fails.append(f"E1 settle: error {e:+.2f} ms at {dt:.2f} s after an epoch start")
    if len(settled_bad) > 5:
        fails.append(f"E1 settle: ... {len(settled_bad)} measurements in all")
    p99 = percentile(steady, 99)
    if len(steady) < min_steady:
        fails.append(f"E2 steady: only {len(steady)} steady-state measurements, want at least {min_steady}")
    elif steady and not p99 < P99_LIMIT_MS:
        fails.append(f"E2 steady: p99 |error| {p99:.3f} ms >= {P99_LIMIT_MS} ms")

    # U1
    for u in underruns:
        for (t0, t1) in windows:
            if t0 + SETTLE_S <= u["t"] < t1:
                fails.append(f"U1 underruns: {u.get('note', '')} underrun ({int(u['n'])} frames) {u['t'] - t0:.2f} s after an epoch start")
                break

    # R1
    for r in resyncs:
        near = [t0 for (t0, t1) in windows if t0 <= r["t"] < t1 and r["t"] - t0 <= RESYNC_WINDOW_S]
        if not near:
            fails.append(f"R1 resyncs: a resync at t={r['t']:.3f} is not within {RESYNC_WINDOW_S} s after an epoch start ({r.get('note', '')})")
    if max_resyncs is not None and len(resyncs) > max_resyncs:
        fails.append(f"R1 resyncs: {len(resyncs)} resyncs, at most {max_resyncs} allowed")

    late = [note_value(s["note"], "skipped_ms") for s in starts]
    late = [x for x in late if x > 0.0]
    stats = dict(
        epochs=len(starts), breaks=len(breaks), err_samples=len(errs), steady_samples=len(steady),
        steady_p50_ms=percentile(steady, 50), steady_p99_ms=p99, steady_max_ms=max(steady) if steady else float("nan"),
        first_second_max_ms=max(first_second) if first_second else float("nan"),
        placement_max_ms=max((abs(e["err_ms"]) for e in epochs), default=float("nan")),
        resyncs=len(resyncs), underruns=len(underruns), late_starts=len(late), late_max_ms=max(late) if late else 0.0,
        comp_max_ppm=max((abs(r["comp_ppm"]) for r in errs), default=0.0),
        resid_max_ms=max((r["resid_ms"] for r in errs), default=float("nan")),
    )
    return fails, stats, notes


def report(path, fails, stats):
    lines = [f"{path}",
             f"  epochs {stats['epochs']}, breaks {stats['breaks']}, measurements {stats['err_samples']} "
             f"(steady {stats['steady_samples']})",
             f"  placement max {stats['placement_max_ms']:.3f} ms; first second max {stats['first_second_max_ms']:.3f} ms; "
             f"steady |error| p50 {stats['steady_p50_ms']:.3f} p99 {stats['steady_p99_ms']:.3f} max {stats['steady_max_ms']:.3f} ms",
             f"  resyncs {stats['resyncs']}, underruns {stats['underruns']}, late starts {stats['late_starts']} "
             f"(max {stats['late_max_ms']:.1f} ms skipped), largest correction {stats['comp_max_ppm']:.0f} ppm, "
             f"device clock spread {stats['resid_max_ms']:.4f} ms"]
    for f in fails:
        lines.append("  FAIL " + f)
    lines.append("  VERDICT: " + ("PASS" if not fails else "FAIL"))
    return "\n".join(lines)


# ---- the judge's own tests ------------------------------------------------------------------------------------------

def make_log(epochs=3, length=6.0, err=lambda dt: 0.0, place=0.01, extra=()):
    """A made-up log: `epochs` epochs `length` s long, seek rows between them, errors from err(dt) every 10 ms."""
    rows = []

    def row(kind, t, e=0.0, n=0, note="", epoch=0):
        rows.append(dict(kind=kind, t=t, epoch=epoch, film_t=0.0, err_ms=e, comp_ppm=0.0, resyncs=0, underruns=0,
                         latency_ms=30.0, resid_ms=0.005, n=n, note=note))

    t = 100.0
    for k in range(epochs):
        row("start", t, 0.0, 0, "silence_ms=10;skipped_ms=0", k)
        row("epoch", t, place, 0, "first_sound_ms=0.01", k)
        for i in range(int(length / 0.01)):
            row("err", t + i * 0.01, err(i * 0.01), 0, "", k)
        t += length
        row("seek", t, 0.0, k + 1, "", k)
        t += 0.2
    for x in extra:
        rows.append(x)
    return rows


def selftest():
    failures = []

    def expect(name, rows, want_check, **kw):
        fails, _, _ = analyze(rows, **kw)
        want = (want_check,) if isinstance(want_check, str) else (want_check or ())
        hit = all(any(f.startswith(w) for f in fails) for w in want) if want else not fails
        other = [f for f in fails if want and not f.startswith(want)]
        ok = hit and not other
        print(("ok   " if ok else "FAIL ") + name + ("" if ok else f"  -> {fails}"))
        if not ok:
            failures.append(name)

    expect("a clean log passes", make_log(), None)
    expect("a placement 8 ms off is caught", make_log(place=8.0), "P1")
    expect("a placement -6 ms off is caught", make_log(place=-6.0), "P1")
    expect("an error that never settles is caught", make_log(err=lambda dt: 7.0 if dt > 0.5 else 0.0), ("E1", "E2"))
    expect("a wild error right after an epoch start is caught", make_log(err=lambda dt: 900.0 if dt < 0.05 else 0.0), "E0")
    expect("an error that settles in 0.9 s is fine", make_log(err=lambda dt: 7.0 if dt < 0.9 else 0.0, ), None)
    expect("a steady 2.5 ms error is caught", make_log(err=lambda dt: 2.5 if dt > 1.0 else 0.0), "E2")
    expect("a 1.5 ms error is fine", make_log(err=lambda dt: 1.5 if dt > 1.0 else 0.0), None)
    expect("an underrun in steady state is caught", make_log(extra=[dict(kind="underrun", t=103.0, epoch=0, n=480, note="device", err_ms=0.0,
                                                                         comp_ppm=0, film_t=0, resyncs=0, underruns=1, latency_ms=0, resid_ms=0)]), "U1")
    expect("an underrun in the first 0.5 s is tolerated", make_log(extra=[dict(kind="underrun", t=100.5, epoch=0, n=480, note="device", err_ms=0.0,
                                                                              comp_ppm=0, film_t=0, resyncs=0, underruns=1, latency_ms=0, resid_ms=0)]), None)
    expect("a resync in steady playback is caught", make_log(extra=[dict(kind="resync", t=104.0, epoch=0, n=0, note="skip_ms=50", err_ms=50.0,
                                                                         comp_ppm=0, film_t=0, resyncs=1, underruns=0, latency_ms=0, resid_ms=0)]), "R1")
    expect("a resync just after an epoch start is allowed", make_log(extra=[dict(kind="resync", t=100.5, epoch=0, n=0, note="skip_ms=50", err_ms=50.0,
                                                                                 comp_ppm=0, film_t=0, resyncs=1, underruns=0, latency_ms=0, resid_ms=0)]), None)
    expect("too many resyncs are caught", make_log(extra=[dict(kind="resync", t=100.5, epoch=0, n=0, note="", err_ms=50.0, comp_ppm=0, film_t=0,
                                                              resyncs=1, underruns=0, latency_ms=0, resid_ms=0)]), "R1", max_resyncs=0)
    expect("no sound at all is caught", [r for r in make_log() if r["kind"] not in ("start", "epoch", "err")], "C1", min_steady=0)
    expect("a start without its placement row is caught", [r for r in make_log() if r["kind"] != "epoch"], "C1")
    expect("short epochs need no steady state when none is asked for", make_log(epochs=10, length=0.8), None, min_steady=0)
    expect("... but ask for one and the lack is caught", make_log(epochs=10, length=0.8), "E2", min_steady=300)
    print(f"audio-analyze selftest: {'PASS' if not failures else 'FAIL (' + str(len(failures)) + ')'}")
    return 1 if failures else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("log", nargs="?", help="audio.csv")
    ap.add_argument("--min-steady", type=int, default=300, help="steady-state measurements the run must have (0 for scrub runs)")
    ap.add_argument("--max-resyncs", type=int, default=None)
    ap.add_argument("--min-epochs", type=int, default=1)
    ap.add_argument("--json", help="write the statistics here")
    ap.add_argument("--selftest", action="store_true", help="run the judge on made-up logs with known answers")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.log:
        ap.error("audio.csv is required")
    rows = load(a.log)
    fails, stats, _ = analyze(rows, a.min_steady, a.max_resyncs, a.min_epochs)
    print(report(a.log, fails, stats))
    if a.json:
        Path(a.json).write_text(json.dumps(dict(stats=stats, fails=fails, ok=not fails), indent=1), encoding="utf-8")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
