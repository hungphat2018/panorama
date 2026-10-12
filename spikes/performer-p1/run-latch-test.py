"""The headless test of the performer's cue latch, frame picking and scheduling (P3), end to end.

Runs, in this order, and prints one summary (exit code 0 only if everything passes):
  1. cue-selftest.exe          JSON parser, cue sheet loader and store, latch, picture layout (unit checks)
  2. test-analyze-cues.py      the analyzer on made-up logs with known answers, good and bad
  3. latch-sim.exe, functional one family per frame-rate case (23.976 fps, 25 fps, variable rate), each with
                               swap-and-seek runs (a seek -50..+150 ms around every swap, pauses) and scrub runs (5 seeks/s);
                               every log judged by analyze-cues.py: 0 wrong frames, 0 shots changed on screen, every
                               monitor starting every epoch together; at least 100 swaps and 50 seeks per family
  4. latch-sim.exe picks       the master's frame on every refresh against the exact timestamps, and the old shortcut
  5. latch-sim.exe clock       hysteresis and re-aim over hours of simulated drift, measured on the true grids
  6. negative controls         seven deliberate defects, each of which analyze-cues.py must flag with a named check
  7. perf1-p3-lgpl.exe --headless   the real program (FFmpeg seeks, pl_queue, threads, control script, named pipe) on
                               virtual displays and libplacebo's dummy GPU, every log judged by the same analyzer

Binaries come from compile.ps1 (-Program latch-test builds latch-sim.exe and cue-selftest.exe; -Program p3 the exe).
No window is opened and no graphics hardware used. The runner runs at idle priority (the programs it starts inherit that), so
it never competes with a live session; --normal-priority switches that off.

  python -I run-latch-test.py [--out results-p3/latch-test] [--quick] [--keep-logs] [--skip-clock] [--skip-exe]
"""
import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
BIN = HERE / "bin"
SIM = BIN / "latch-sim.exe"
SELFTEST = BIN / "cue-selftest.exe"

spec = importlib.util.spec_from_file_location("analyze_cues", HERE / "analyze-cues.py")
ac = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ac)

JUDAS = HERE.parent / "clock-sim" / "data" / "judas-s01e02-video-pts.txt"
HOTEL = HERE.parent / "clock-sim" / "data" / "hotel-aman-video-pts.txt"
VFR_REAL = HERE / "data" / "vfr-24-30-60-pts.txt"

# name, trace, external Hz, exact timestamps for the pick check, runs (scenario, seconds, seed)
FAMILIES = [
    ("23.976 fps (anime episode), 240+96 Hz", JUDAS, 96, "cfr:24000/1001", [("swapseek", 400, 1), ("scrub", 400, 2)]),
    ("23.976 fps (anime episode), 240+72 Hz", JUDAS, 72, "cfr:24000/1001", [("swapseek", 300, 3), ("scrub", 300, 4)]),
    ("25 fps (hotel film), 240+100 Hz", HOTEL, 100, "cfr:25/1", [("swapseek", 400, 5), ("scrub", 400, 6)]),
    ("variable rate (synthetic 24/30/60), 240+96 Hz", "synthetic-vfr", 96, None, [("swapseek", 400, 7), ("scrub", 400, 8)]),
    ("variable rate (synthetic 24/30/60), 240+72 Hz", "synthetic-vfr", 72, None, [("swapseek", 300, 9), ("scrub", 300, 10)]),
    ("variable rate (real 12 s clip 24/30/60), 240+96 Hz", VFR_REAL, 96, None, [("swapseek", 400, 11), ("scrub", 300, 12)]),
]

# fault -> (trace, extra args, seconds, the checks that must flag it)
CONTROLS = {
    "no-latch": (JUDAS, [], 150, {"C4"}),
    "lookup-by-clock": (JUDAS, [], 150, {"C2"}),
    "keep-latch": (JUDAS, [], 150, {"C6"}),
    "pl-queue-pick": (VFR_REAL, [], 150, {"C1b"}),   # the real 24/30/60 clip: 60 fps sections within the first 12 s
    "independent-start": (JUDAS, [], 150, {"C7b"}),
    "no-hold": (JUDAS, [], 150, {"C8"}),
    "no-reaim-guard": ("synthetic-flip", [], 150, {"C10"}),
}


def be_polite():
    """Idle priority class for this process; the programs it starts inherit it (CreateProcess does that for an idle parent),
    so a test run never competes with a live session (a full-screen measurement, a game)."""
    import ctypes
    k32 = ctypes.windll.kernel32
    k32.SetPriorityClass(k32.GetCurrentProcess(), 0x00000040)   # IDLE_PRIORITY_CLASS


def run(cmd, **kw):
    t = time.time()
    p = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, **kw)
    return p.returncode, p.stdout, p.stderr, time.time() - t


def sim(out, scenario, trace, setup, seconds, seed, *extra, hours=None):
    cmd = [SIM, "--scenario", scenario, "--trace", trace, "--setup", setup, "--seed", seed, "--out", out]
    cmd += ["--hours", hours] if hours is not None else ["--seconds", seconds]
    cmd += list(extra)
    return run(cmd)


def analyze_dir(out, tol_ms=0.0):
    path, d = ac.load(out)
    C, counts, seam = ac.analyze(d, tol_ms * 1e-3, 3)
    text, totals, bad = ac.report(path, d, C, counts, seam)
    return C, counts, seam, totals, bad, text


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=HERE / "results-p3" / "latch-test")
    ap.add_argument("--quick", action="store_true", help="a fifth of the virtual time (smoke check, counts will fall short)")
    ap.add_argument("--keep-logs", action="store_true", help="keep every cuelog.json (about 20 MB each)")
    ap.add_argument("--skip-clock", action="store_true")
    ap.add_argument("--skip-exe", action="store_true", help="skip step 7 (the real program on virtual displays)")
    ap.add_argument("--normal-priority", action="store_true", help="do not lower the priority class of the runner")
    a = ap.parse_args()
    if not a.normal_priority:
        be_polite()
    for exe in (SIM, SELFTEST):
        if not exe.exists():
            sys.exit(f"{exe} is missing: compile it first (compile.ps1 -Target latch-test)")
    scale = 0.2 if a.quick else 1.0
    if a.out.exists():
        shutil.rmtree(a.out)
    a.out.mkdir(parents=True)
    summary = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "ok": True}
    failures = []
    t_all = time.time()

    def fail(msg):
        failures.append(msg)
        print("  !! " + msg)

    # 1. unit checks ----------------------------------------------------------------------------------------
    rc, out, err, dt = run([SELFTEST])
    last = out.strip().splitlines()[-1] if out.strip() else "(no output)"
    print(f"1. cue-selftest.exe: {last}")
    summary["selftest"] = last
    if rc:
        fail("cue-selftest failed:\n" + out)

    # 2. analyzer tests -----------------------------------------------------------------------------------------
    rc, out, err, dt = run([sys.executable, "-I", HERE / "test-analyze-cues.py"])
    last = (out.strip().splitlines() or err.strip().splitlines() or ["(no output)"])[-1]
    print(f"2. test-analyze-cues.py: {last}")
    summary["analyzer_tests"] = last
    if rc:
        fail("analyzer tests failed:\n" + out + err)

    # 3. functional families ------------------------------------------------------------------------------------
    print("3. functional runs (latch-sim + analyze-cues), per family:")
    fam_rows = []
    for name, trace, hz, truth, runs in FAMILIES:
        tot = dict(swaps=0, seeks=0, adversarial=0, pauses=0, restarts=0, refreshes=0, wrong=0, changed=0, breaks=0, clock=0, late=0, runs=0)
        verdicts = []
        seam_worst = 0.0
        for scenario, seconds, seed in runs:
            out = a.out / f"{scenario}-{Path(str(trace)).stem[:12]}-{hz}-s{seed}"
            rc, so, se, dt = sim(out, scenario, str(trace), hz, seconds * scale, seed)
            if rc:
                fail(f"{name} {scenario}: latch-sim exit {rc}: {se.strip()}")
                continue
            C, counts, seam, totals, bad, text = analyze_dir(out)
            (out / "analysis.txt").write_text(text, encoding="utf-8")
            tot["runs"] += 1
            tot["swaps"] += counts["swaps"]
            tot["seeks"] += counts["seeks"]
            tot["adversarial"] += counts["adversarial_seeks"]
            tot["pauses"] += counts["pauses"]
            tot["restarts"] += counts["restarts"]
            tot["refreshes"] += sum(counts["refreshes"].values())
            tot["wrong"] += totals["wrong frames"]
            tot["changed"] += totals["shots changed"]
            tot["breaks"] += totals["breaks"]
            tot["clock"] += totals["clock"]
            tot["late"] += C["C1late"].count
            for s in seam.values():
                seam_worst = max(seam_worst, s["max_abs"])
            verdicts.append("PASS" if not bad else "FAIL")
            if bad:
                fail(f"{name} {scenario} seed {seed}: analyzer FAIL, see {out / 'analysis.txt'}")
            if not a.keep_logs:
                (out / "cuelog.json").unlink(missing_ok=True)
        enough = a.quick or (tot["swaps"] >= 100 and tot["adversarial"] >= 50)
        if not enough:
            fail(f"{name}: only {tot['swaps']} swaps and {tot['adversarial']} adversarial seeks")
        tot.update(name=name, seam_max_ms=seam_worst, verdicts=verdicts, counts_ok=enough)
        fam_rows.append(tot)
        print(f"   {name}: {tot['swaps']} swaps, {tot['seeks']} seeks ({tot['adversarial']} within -50..+150 ms of a swap), "
              f"{tot['pauses']} pauses, {tot['restarts']} restarts, {tot['refreshes']} refreshes -> "
              f"wrong frames {tot['wrong']}, shots changed {tot['changed']}, break failures {tot['breaks']}, "
              f"clock violations {tot['clock']}, late {tot['late']}; seam max {seam_worst:.2f} ms; {'/'.join(verdicts)}")
    summary["families"] = fam_rows
    totals_all = {k: sum(r[k] for r in fam_rows) for k in ("swaps", "seeks", "adversarial", "restarts", "refreshes", "wrong", "changed", "breaks", "clock", "late")}
    summary["functional_total"] = totals_all
    print(f"   TOTAL: {totals_all['swaps']} swaps, {totals_all['seeks']} seeks, {totals_all['restarts']} restarts, "
          f"{totals_all['refreshes']} refreshes; wrong frames {totals_all['wrong']}, shots changed {totals_all['changed']}, "
          f"break failures {totals_all['breaks']}, clock violations {totals_all['clock']}")

    # 4. picks ---------------------------------------------------------------------------------------------------
    print("4. frame picks against the exact timestamps (master, every refresh of 300 s):")
    picks = []
    for name, trace, hz, truth, _ in FAMILIES:
        if "real" in name:
            continue
        row = {"name": name}
        for fault in (None, "pl-queue-pick"):
            out = a.out / f"picks-{Path(str(trace)).stem[:12]}-{hz}-{fault or 'engine'}"
            extra = (["--fault", fault] if fault else []) + (["--truth", truth] if truth else [])
            rc, so, se, dt = sim(out, "picks", str(trace), hz, 300 * scale, 1, *extra)
            if rc:
                fail(f"picks {name}: exit {rc} {se.strip()}")
                continue
            j = json.loads((out / "picks.json").read_text())
            row["shortcut" if fault else "engine"] = j
        picks.append(row)
        e, s = row.get("engine"), row.get("shortcut")
        if e and s:
            print(f"   {name}: engine wrong {e['wrong']} of {e['master_refreshes_checked']} ({100 * e['wrong_fraction']:.3f} %); "
                  f"old shortcut wrong {s['wrong']} ({100 * s['wrong_fraction']:.2f} %), worst {s['worst_late_ms']:.1f} ms late")
            if e["wrong"]:
                fail(f"picks {name}: the engine picked {e['wrong']} wrong frames")
    summary["picks"] = picks
    if not any(r.get("shortcut", {}).get("wrong", 0) > 0 for r in picks if "variable" in r["name"]):
        fail("the old shortcut should be wrong on variable-rate traces, and was not")

    # 5. clock -----------------------------------------------------------------------------------------------------
    clock_rows = []
    if not a.skip_clock:
        print("5. hysteresis and re-aim, 2 simulated hours per seed on the true grids (aim row, ms):")
        configs = [
            ("23.976 fps, 240+96 Hz, 0.5 ppm", JUDAS, 96, 0.5, None, 2.0, 8),
            ("23.976 fps, 240+72 Hz, 0.5 ppm", JUDAS, 72, 0.5, None, 2.0, 8),
            ("25 fps, 240+100 Hz, 0.5 ppm", HOTEL, 100, 0.5, None, 2.0, 4),
            ("23.976 fps, 240+96 Hz, 2 ppm", JUDAS, 96, 2.0, None, 2.0, 4),
            ("23.976 fps, 240+96 Hz, 5 ppm", JUDAS, 96, 5.0, None, 2.0, 4),
            ("variable rate, 240+96 Hz, 0.5 ppm", "synthetic-vfr", 96, 0.5, None, 2.0, 4),
            ("variable rate, 240+96 Hz, parked boundary (0 ppm)", "synthetic-vfr", 96, 0.0, 0.5, 0.1, 8),
        ]
        policies = [("nearest, no hysteresis, no re-aim (perf1 now)", ["--no-hysteresis", "--no-reaim"]),
                    ("hysteresis, moves at cuts", ["--no-reaim"]),
                    ("hysteresis + re-aim at cuts", [])]
        for cname, trace, hz, ppm, align, hours, seeds in configs:
            for pname, pargs in policies:
                runs = []
                for seed in range(1, int(seeds * (0.25 if a.quick else 1)) + 1):
                    out = a.out / "clock-tmp"
                    args = list(pargs) + ["--drift-ppm", ppm] + (["--align-frac", align] if align is not None else [])
                    rc, so, se, dt = sim(out, "clock", str(trace), hz, 0, seed, *args, hours=hours * (0.25 if a.quick else 1))
                    if rc:
                        fail(f"clock {cname} {pname}: exit {rc} {se.strip()}")
                        break
                    runs.append(json.loads((out / "clock-summary.json").read_text()))
                if not runs:
                    continue
                h = sum(r["hours"] for r in runs)
                row = {
                    "config": cname, "policy": pname, "hours": h, "seeds": len(runs),
                    "mid_shot_slips": sum(r["mid_shot_slips"] for r in runs),
                    "mid_per_hour": sum(r["mid_shot_slips"] for r in runs) / h,
                    "cut_slips_per_hour": sum(r["slips_at_cuts"] for r in runs) / h,
                    "flip_flops": sum(r["flip_flops"] for r in runs),
                    "reaims_per_hour": sum(r["reaims"] for r in runs) / h,
                    "aim_p5": sum(r["aim_ms"]["p5"] for r in runs) / len(runs),
                    "aim_p95": sum(r["aim_ms"]["p95"] for r in runs) / len(runs),
                    "aim_max": max(r["aim_ms"]["max_abs"] for r in runs),
                    "top_max": max(r["top_ms"]["max_abs"] for r in runs),
                    "bottom_max": max(r["bottom_ms"]["max_abs"] for r in runs),
                    "master_min_frame": min(r["master_min_frame_refreshes"] for r in runs),
                    "class_resyncs": sum(r["class_resyncs"] for r in runs),
                    "forced_moves": sum(r["forced_moves"] for r in runs),
                    "p_over_q": f"{runs[0]['p']}/{runs[0]['q']}",
                }
                clock_rows.append(row)
                print(f"   {cname} | {pname}: {h:.0f} h; mid-shot slips {row['mid_shot_slips']} ({row['mid_per_hour']:.2f}/h), "
                      f"at cuts {row['cut_slips_per_hour']:.2f}/h, flip-flops {row['flip_flops']}, re-aims {row['reaims_per_hour']:.2f}/h; "
                      f"aim row p5..p95 {row['aim_p5']:+.2f}..{row['aim_p95']:+.2f}, max {row['aim_max']:.2f}; "
                      f"top max {row['top_max']:.2f}, bottom max {row['bottom_max']:.2f}; shortest master frame {row['master_min_frame']}")
        shutil.rmtree(a.out / "clock-tmp", ignore_errors=True)
        summary["clock"] = clock_rows
        for r in clock_rows:
            if r["policy"].startswith("hysteresis") and r["mid_shot_slips"]:
                fail(f"clock {r['config']} | {r['policy']}: {r['mid_shot_slips']} mid-shot slips")
            if r["policy"].startswith("hysteresis") and r["master_min_frame"] < 1:
                fail(f"clock {r['config']} | {r['policy']}: a frame lasted less than one refresh")
            if r["policy"].startswith("hysteresis") and (r["class_resyncs"] or r["forced_moves"]):
                fail(f"clock {r['config']} | {r['policy']}: class table out of step ({r['class_resyncs']} resyncs, {r['forced_moves']} forced moves)")

    # 6. negative controls -----------------------------------------------------------------------------------------
    print("6. negative controls (each defect must be flagged by the analyzer):")
    ctl = {}
    for fault, (trace, extra, seconds, expect) in CONTROLS.items():
        out = a.out / f"fault-{fault}"
        rc, so, se, dt = sim(out, "swapseek", str(trace), 96, seconds * max(scale, 0.5), 21, "--fault", fault, *extra)
        if rc:
            fail(f"control {fault}: exit {rc} {se.strip()}")
            continue
        C, counts, seam, totals, bad, text = analyze_dir(out)
        flagged = {k for k, c in C.items() if c.must_be_zero and c.count}
        caught = expect <= flagged
        ctl[fault] = {"flagged": sorted(flagged), "expected": sorted(expect), "caught": caught,
                      "counts": {k: C[k].count for k in sorted(flagged)}}
        print(f"   {fault}: flagged {', '.join(f'{k} x{C[k].count}' for k in sorted(flagged)) or 'nothing'} -> "
              f"{'caught' if caught else 'NOT CAUGHT (expected ' + ','.join(sorted(expect)) + ')'}")
        if not caught:
            fail(f"negative control {fault} was not caught")
        if not a.keep_logs:
            (out / "cuelog.json").unlink(missing_ok=True)
    summary["negative_controls"] = ctl

    # 7. the real program, headless -------------------------------------------------------------------------------
    exe_rows = []
    exe = BIN / "perf1-p3-lgpl.exe"
    if a.skip_exe or not exe.exists():
        print("7. perf1-p3-lgpl.exe --headless: skipped" + ("" if a.skip_exe else f" ({exe} is not compiled)"))
    else:
        print("7. perf1-p3-lgpl.exe --headless (real FFmpeg seeks, pl_queue, threads, control script; virtual displays, dummy GPU):")
        env = dict(os.environ, PATH=";".join([str(BIN), r"C:\Users\tamla\msys64\home\tamla\p0\prefix\ffmpeg-lgpl\bin",
                                              r"C:\Users\tamla\msys64\home\tamla\p0\prefix\libplacebo-master\bin",
                                              r"C:\Users\tamla\msys64\ucrt64\bin", os.environ.get("PATH", "")]))

        def clip(name, fps, seconds, seed):
            d = HERE / "data" / name
            if not (d / "cue-test.mkv").exists():
                subprocess.run([sys.executable, "-I", str(HERE / "make-cue-test-clip.py"), str(d), "--seconds", str(seconds),
                                "--fps", fps, "--seed", str(seed)], check=True, capture_output=True)
            return d

        c24, c25 = clip("cue-test", "24000/1001", 60, 5), clip("cue-test-25", "25", 40, 8)
        plans = [
            ("23.976 fps clip, 240+96 Hz, a seek -50..+150 ms around each swap", c24, 96, "adversarial", 50, 12, 4, []),
            ("23.976 fps clip, 240+96 Hz, scrub at 5 seeks/s", c24, 96, "scrub", 50, 10, 4, []),
            ("23.976 fps clip, 240+96 Hz, pause, resume, seek while paused", c24, 96, "breaks", 50, 10, 6, []),
            ("23.976 fps clip, 240+96 Hz, 20 seeks/s", c24, 96, "stress", 40, 5, 4, []),
            ("23.976 fps clip, 240+72 Hz, speed not locked", c24, 72, "adversarial", 25, 8, 4, ["--no-lock"]),
            ("25 fps clip, 240+100 Hz", c25, 100, "adversarial", 30, 7, 2, []),
        ]
        for name, cdir, hz, kind, secs, versions, seed, extra in plans:
            sheets = cdir / "sheets"
            subprocess.run([sys.executable, "-I", str(HERE / "make-cue-sheets.py"), str(cdir), str(sheets), "--versions", str(versions),
                            "--seed", str(seed), "--script", kind, "--span", str(secs)], check=True, capture_output=True)
            out = a.out / f"exe-{kind}-{hz}"
            out.mkdir(parents=True, exist_ok=True)
            cmd = [str(exe), "--headless", str(hz), "--file", str(cdir / "cue-test.mkv"), "--cues", str(sheets / "cues-v1.json"),
                   "--control-script", str(sheets / f"script-{kind}.jsonl"), "--seconds", str(secs * (0.4 if a.quick else 1.0)),
                   "--out", str(out), *extra]
            t = time.time()
            p = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=secs + 90)
            (out / "stdout.txt").write_text(p.stdout + p.stderr)
            if p.returncode or not (out / "perf1-cuelog.json").exists():
                fail(f"exe run {name}: exit {p.returncode}: {(p.stderr or p.stdout).strip()[-300:]}")
                continue
            C, counts, seam, totals, bad, text = analyze_dir(out / "perf1-cuelog.json", tol_ms=0.5)
            (out / "analysis.txt").write_text(text, encoding="utf-8")
            row = dict(name=name, swaps=counts["swaps"], seeks=counts["seeks"], pauses=counts["pauses"], restarts=counts["restarts"],
                       refreshes=sum(counts["refreshes"].values()), wrong=totals["wrong frames"], changed=totals["shots changed"],
                       breaks=totals["breaks"], clock=totals["clock"], late=C["C1late"].count, verdict="PASS" if not bad else "FAIL",
                       seconds=round(time.time() - t, 1))
            exe_rows.append(row)
            print(f"   {name}: {row['swaps']} published sheets, {row['seeks']} seeks, {row['pauses']} pauses, {row['restarts']} restarts, "
                  f"{row['refreshes']} refreshes -> wrong frames {row['wrong']}, shots changed {row['changed']}, break failures "
                  f"{row['breaks']}, clock violations {row['clock']}, late {row['late']}; {row['verdict']}")
            if bad:
                fail(f"exe run {name}: analyzer FAIL, see {out / 'analysis.txt'}")
            if not a.keep_logs:
                (out / "perf1-cuelog.json").unlink(missing_ok=True)
        rc, so, se, dt = run([sys.executable, "-I", HERE / "test-control-pipe.py", "--out", a.out / "exe-pipe"], env=env)
        last = (so.strip().splitlines() or ["(no output)"])[-1]
        print(f"   named pipe (load, refused loads, seek, pause, resume, status, stop): {last}")
        exe_rows.append({"name": "named pipe", "result": last})
        if rc:
            fail("named-pipe test failed:\n" + so + se)
    summary["exe_runs"] = exe_rows

    summary["ok"] = not failures
    summary["failures"] = failures
    summary["seconds"] = round(time.time() - t_all, 1)
    (a.out / "latch-test-summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(("PASS" if not failures else f"FAIL ({len(failures)})") + f" in {summary['seconds']} s; summary: {a.out / 'latch-test-summary.json'}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
