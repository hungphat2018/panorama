"""The audio tests in one command (nothing is shown on a screen, no display mode is changed).

  python -I audio-run-tests.py [--exe perf1-audio-lgpl.exe] [--out results-audio/run] [--quick] [--wasapi [--wasapi-seconds 8]]

Steps (the exit code is 0 only if all pass):
  1. audio-selftest.exe on a ramp clip (NUT: exact timestamps; Matroska: millisecond timestamps): seeks to the sample, the
     converter's claimed film time against what its samples hold, speed lock, soft correction, silence, skip, end of file;
     the device clock, the write log and the correction loop on made-up numbers
  2. audio-analyze.py --selftest: the judge on made-up logs with known answers
  3. the program, --headless (virtual displays, dummy GPU) with --audio-null (a stand-in sound device on the host clock),
     each run judged from its audio.csv by audio-analyze.py:
       plain playback 60 s at 96 Hz (23.976 fps, speed lock +0.1 %); at 72 Hz; unlocked; a device clock 300 ppm fast and
       500 ppm slow; a device that holds frames 40 ms before they sound; the adversarial script (a seek 50 ms before or up to 150 ms after every cue swap); pause, resume, seek
       while paused; a scrub at 5 seeks per second; 20 seeks per second; the 12 s variable-rate clip; a 25 fps clip with 5.1 FLAC sound on a 100 Hz external
  4. with --wasapi: one headless run on the real default sound device, at volume 0.2 (it can be heard on the PC's speakers)

Clips come from audio-make-test-media.py (made here if missing); cue sheets and scripts from make-cue-sheets.py.
"""
import argparse
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
BIN = HERE / "bin"
DLLS = [r"C:\Users\tamla\msys64\home\tamla\p0\prefix\ffmpeg-lgpl\bin", r"C:\Users\tamla\msys64\home\tamla\p0\prefix\libplacebo-master\bin",
        r"C:\Users\tamla\msys64\ucrt64\bin"]

spec = importlib.util.spec_from_file_location("audio_analyze", HERE / "audio-analyze.py")
aa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(aa)

# name, clip, external Hz, script kind (None: plain), seconds, extra program arguments, analyzer arguments
SCENARIOS = [
    ("plain 60 s, 23.976 fps on 96 Hz (speed lock +0.1 %)", "audio-test-24", 96, None, 62, [], dict(min_steady=1000, max_resyncs=0)),
    ("plain, 23.976 fps on 72 Hz (speed lock +0.1 %)", "audio-test-24", 72, None, 30, [], dict(min_steady=1000, max_resyncs=0)),
    ("plain, speed not locked (--no-lock)", "audio-test-24", 96, None, 25, ["--no-lock"], dict(min_steady=1000, max_resyncs=0)),
    ("sound device clock 300 ppm fast", "audio-test-24", 96, None, 30, ["--audio-null-drift-ppm", "300"], dict(min_steady=1000, max_resyncs=0)),
    ("sound device clock 500 ppm slow", "audio-test-24", 96, None, 30, ["--audio-null-drift-ppm", "-500"], dict(min_steady=1000, max_resyncs=0)),
    ("seeks around cue swaps (adversarial), 96 Hz", "audio-test-24", 96, "adversarial", 50, [], dict(min_steady=300)),
    ("device with a 40 ms driver delay, seeks around cue swaps", "audio-test-24", 96, "adversarial", 40, ["--audio-null-latency-ms", "40"], dict(min_steady=300, max_resyncs=0)),
    ("device with a 40 ms driver delay, scrub", "audio-test-24", 96, "scrub", 40, ["--audio-null-latency-ms", "40"], dict(min_steady=0, max_resyncs=0)),
    ("pause, resume, seek while paused (breaks), 96 Hz", "audio-test-24", 96, "breaks", 50, [], dict(min_steady=300)),
    ("scrub at 5 seeks per second, 96 Hz", "audio-test-24", 96, "scrub", 50, [], dict(min_steady=0)),
    ("20 seeks per second (stress), 96 Hz", "audio-test-24", 96, "stress", 30, [], dict(min_steady=0)),
    ("variable frame rate clip (24, 30, 60 fps sections), 96 Hz", "audio-test-vfr", 96, None, 11, [], dict(min_steady=300, max_resyncs=0)),
    ("25 fps clip, 5.1 FLAC, 100 Hz, adversarial", "audio-test-25", 100, "adversarial", 36, [], dict(min_steady=300)),
]


def env():
    return dict(os.environ, PATH=";".join([str(BIN)] + DLLS + [os.environ.get("PATH", "")]))


def run(cmd, timeout):
    t = time.time()
    p = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, env=env(), timeout=timeout)
    return p.returncode, p.stdout + p.stderr, time.time() - t


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--exe", default="perf1-audio-lgpl.exe")
    ap.add_argument("--out", default=str(HERE / "results-audio" / "run"))
    ap.add_argument("--quick", action="store_true", help="shorter runs (a third of the time); the steady-state minimums scale down")
    ap.add_argument("--only", help="run only the scenarios whose name contains this text")
    ap.add_argument("--wasapi", action="store_true", help="also one run on the real default sound device (audible, volume 0.2)")
    ap.add_argument("--wasapi-seconds", type=float, default=8.0)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    failures, rows = [], []

    # 0. media
    subprocess.run([sys.executable, "-I", str(HERE / "audio-make-test-media.py")], check=True)

    # 1. the exe's own checks
    st = BIN / "audio-selftest.exe"
    if st.exists():
        for clip, tol in (("ramp.nut", 25), ("ramp.mkv", 700)):
            rc, text, dt = run([st, HERE / "data" / "audio-test-ramp" / clip, tol], 120)
            lines = [l for l in text.splitlines() if l.startswith("audio-selftest:") or l.startswith("FAIL")]
            last = lines[-1] if lines else "(no output)"
            print(f"1. audio-selftest {clip}: {last}")
            if rc:
                failures.append(f"audio-selftest {clip}:\n{text[-800:]}")
    else:
        failures.append(f"{st} is missing: compile.ps1 -Program audio-test")

    # 2. the judge's tests
    rc, text, dt = run([sys.executable, "-I", HERE / "audio-analyze.py", "--selftest"], 60)
    print("2. audio-analyze --selftest:", text.strip().splitlines()[-1])
    if rc:
        failures.append("audio-analyze --selftest:\n" + text)

    # 3. the program with the stand-in device
    exe = BIN / a.exe
    if not exe.exists():
        failures.append(f"{exe} is missing: compile.ps1 -Output {a.exe}")
    else:
        print(f"3. {a.exe} --headless --audio-null:")
        scale = 1 / 3 if a.quick else 1.0
        for name, clip, hz, kind, secs, extra, jargs in SCENARIOS:
            if a.only and a.only not in name:
                continue
            cdir = HERE / "data" / clip
            secs = max(8.0, secs * scale)
            cmd = [exe, "--headless", hz, "--file", cdir / "cue-test.mkv", "--audio-null", "--seconds", f"{secs:.1f}"]
            slug = name.split(",")[0].replace(" ", "-").replace("(", "").replace(")", "")[:40]
            run_dir = out / f"{clip}-{hz}-{kind or 'plain'}-{len(rows)}"
            run_dir.mkdir(parents=True, exist_ok=True)
            if kind:
                sheets = cdir / "sheets"
                subprocess.run([sys.executable, "-I", str(HERE / "make-cue-sheets.py"), str(cdir), str(sheets), "--versions", "8", "--seed", "4",
                                "--script", kind, "--span", str(int(secs))], check=True, capture_output=True)
                cmd += ["--cues", sheets / "cues-v1.json", "--control-script", sheets / f"script-{kind}.jsonl"]
            cmd += extra + ["--out", run_dir]
            rc, text, dt = run(cmd, secs + 90)
            (run_dir / "stdout.txt").write_text(text, encoding="utf-8")
            log = run_dir / "audio.csv"
            if rc or not log.exists():
                failures.append(f"{name}: exit {rc}, {'no audio.csv' if not log.exists() else ''}\n{text[-600:]}")
                print(f"   {name}: exit {rc}")
                continue
            jargs = dict(jargs)
            if a.quick and jargs.get("min_steady"):
                jargs["min_steady"] = max(100, int(jargs["min_steady"] * scale))
            fails, stats, _ = aa.analyze(aa.load(log), **jargs)
            print("   " + name + f" ({dt:.0f} s)")
            print("   " + aa.report(log, fails, stats).replace("\n", "\n   "))
            rows.append(dict(name=name, **stats, ok=not fails, fails=fails))
            if fails:
                failures.append(f"{name}: " + "; ".join(fails[:3]))

    # 4. the real device
    if a.wasapi and exe.exists():
        print(f"4. {a.exe} --headless on the default sound device (volume 0.2, {a.wasapi_seconds:.0f} s):")
        cdir = HERE / "data" / "audio-test-24"
        run_dir = out / "wasapi"
        run_dir.mkdir(parents=True, exist_ok=True)
        cmd = [exe, "--headless", 96, "--file", cdir / "cue-test.mkv", "--audio-volume", "0.2", "--seconds", a.wasapi_seconds, "--out", run_dir]
        rc, text, dt = run(cmd, a.wasapi_seconds + 90)
        (run_dir / "stdout.txt").write_text(text, encoding="utf-8")
        log = run_dir / "audio.csv"
        if rc or not log.exists():
            failures.append(f"wasapi run: exit {rc}\n{text[-600:]}")
            print(f"   exit {rc}: {text[-300:]}")
        else:
            fails, stats, _ = aa.analyze(aa.load(log), min_steady=max(50, int((a.wasapi_seconds - 5) * 80)), max_resyncs=0)
            print("   " + aa.report(log, fails, stats).replace("\n", "\n   "))
            rows.append(dict(name="default sound device (WASAPI)", **stats, ok=not fails, fails=fails))
            if fails:
                failures.append("wasapi run: " + "; ".join(fails[:3]))

    (out / "audio-summary.json").write_text(json.dumps(dict(runs=rows, failures=failures), indent=1), encoding="utf-8")
    print(("PASS" if not failures else f"FAIL ({len(failures)})") + f"; summary: {out / 'audio-summary.json'}")
    for f in failures:
        print("  -", f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
