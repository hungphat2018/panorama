"""Drives a running headless perf1 through its named pipe and checks the replies and the effect on the run.

  python -I test-control-pipe.py [--exe bin/perf1-p3-lgpl.exe] [--clip data/cue-test] [--out results-p3/pipe-test]

Starts perf1 with --headless 96 and --pipe, then sends: status; a good load; the same version again (refused); a missing file;
a crop outside the video; broken JSON; an unknown command; a seek, and waits until the film is there; pause (and that the
playhead then stands still); resume; stop. The run must then end by itself and the cue log must pass analyze-cues.py.
No window and no graphics hardware are used. Prints one line per step and "N checks, M failed" last.
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
PIPE = r"\\.\pipe\perf1-pipe-test"
checks = failed = 0


def check(cond, what):
    global checks, failed
    checks += 1
    if not cond:
        failed += 1
    print(("ok   " if cond else "FAIL ") + what)


class Pipe:
    def __init__(self, path, wait_s=20.0):
        t = time.time()
        while True:
            try:
                self.f = open(path, "r+b", buffering=0)
                return
            except OSError:
                if time.time() - t > wait_s:
                    raise
                time.sleep(0.1)

    def ask(self, msg):
        data = msg if isinstance(msg, bytes) else json.dumps(msg).encode()
        self.f.write(data)
        return json.loads(self.f.read(65536))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--exe", type=Path, default=HERE / "bin" / "perf1-p3-lgpl.exe")
    ap.add_argument("--clip", type=Path, default=HERE / "data" / "cue-test")
    ap.add_argument("--out", type=Path, default=HERE / "results-p3" / "pipe-test")
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    sheets = a.clip / "sheets"
    if not (sheets / "cues-v2.json").exists():
        subprocess.run([sys.executable, "-I", str(HERE / "make-cue-sheets.py"), str(a.clip), str(sheets), "--versions", "4"], check=True)
    bad = a.out / "crop-outside.json"
    bad.write_text(json.dumps({"schema": "panorama.cues/1", "version": 99, "shots": [{"start": 0, "crop": [0, 0, 5000, 5000]}]}))

    path_dll = ";".join([str(HERE / "bin"), r"C:\Users\tamla\msys64\home\tamla\p0\prefix\ffmpeg-lgpl\bin",
                         r"C:\Users\tamla\msys64\home\tamla\p0\prefix\libplacebo-master\bin", r"C:\Users\tamla\msys64\ucrt64\bin",
                         os.environ.get("PATH", "")])
    env = dict(os.environ, PATH=path_dll)
    cmd = [str(a.exe), "--headless", "96", "--file", str(a.clip / "cue-test.mkv"), "--cues", str(sheets / "cues-v1.json"),
           "--pipe", "perf1-pipe-test", "--seconds", "60", "--out", str(a.out)]
    proc = subprocess.Popen(cmd, env=env, stdout=open(a.out / "stdout.txt", "w"), stderr=subprocess.STDOUT)
    try:
        p = Pipe(PIPE)
        time.sleep(3.0)                                  # the film starts about 2 s after launch
        r = p.ask({"cmd": "status"})
        check(r.get("ok") and r["state"] == "playing" and r["version"] == 1, f"status while playing: {r}")
        r = p.ask({"cmd": "load", "file": str(sheets / "cues-v2.json").replace("\\", "/")})
        check(r.get("ok") and r["version"] == 2, f"load version 2: {r}")
        r = p.ask({"cmd": "load", "file": str(sheets / "cues-v2.json").replace("\\", "/")})
        check(not r.get("ok") and "does not exceed" in r.get("error", ""), f"the same version again is refused: {r}")
        r = p.ask({"cmd": "load", "file": "C:/no/such/sheet.json"})
        check(not r.get("ok") and "cannot open" in r.get("error", ""), f"a missing file is refused: {r}")
        r = p.ask({"cmd": "load", "file": str(bad).replace("\\", "/")})
        check(not r.get("ok") and "outside" in r.get("error", ""), f"a crop outside the video is refused: {r}")
        r = p.ask(b"{not json")
        check(not r.get("ok") and "line 1" in r.get("error", ""), f"broken JSON is refused: {r}")
        r = p.ask({"cmd": "dance"})
        check(not r.get("ok"), f"an unknown command is refused: {r}")
        r = p.ask({"cmd": "seek", "t": -3})
        check(not r.get("ok"), f"a negative seek time is refused: {r}")
        r = p.ask({"cmd": "seek", "t": 31.5})
        check(r.get("ok"), f"seek to 31.5 s: {r}")
        deadline = time.time() + 5
        st = {}
        while time.time() < deadline:
            st = p.ask({"cmd": "status"})
            if st["state"] == "playing" and st["playhead"] >= 31.5:
                break
            time.sleep(0.05)
        check(st["state"] == "playing" and 31.4 <= st["playhead"] < 33.5 and st["seeks"] == 1, f"the film is at the sought time: {st}")
        r = p.ask({"cmd": "pause"})
        time.sleep(0.5)
        st1 = p.ask({"cmd": "status"})
        time.sleep(0.6)
        st2 = p.ask({"cmd": "status"})
        check(r.get("ok") and st1["state"] == "paused" and st1["playhead"] == st2["playhead"], f"paused and standing still: {st1['playhead']} {st2['playhead']}")
        r = p.ask({"cmd": "resume"})
        time.sleep(0.8)
        st = p.ask({"cmd": "status"})
        check(r.get("ok") and st["state"] == "playing" and st["playhead"] >= st1["playhead"] - 0.05, f"resumed: {st}")
        r = p.ask({"cmd": "stop"})
        check(r.get("ok"), f"stop: {r}")
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
        check(proc.returncode == 0, f"perf1 ended by itself with exit code {proc.returncode}")
    finally:
        if proc.poll() is None:
            proc.kill()
    log = a.out / "perf1-cuelog.json"
    check(log.exists(), "the cue log was written")
    if log.exists():
        spec = importlib.util.spec_from_file_location("analyze_cues", HERE / "analyze-cues.py")
        ac = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ac)
        _, d = ac.load(log)
        C, counts, seam = ac.analyze(d, 0.5e-3, 3)
        _, totals, bad_count = ac.report(log, d, C, counts, seam)
        check(bad_count == 0, f"the cue log passes the analyzer ({counts['swaps']} swaps, {counts['seeks']} seeks, {counts['pauses']} pauses, "
                             f"{counts['restarts']} restarts)")
        check(counts["swaps"] == 2 and counts["seeks"] == 1 and counts["pauses"] == 1 and counts["resumes"] == 1,
              f"the log shows exactly what was commanded: {counts['swaps']} published versions (the sheet given with --cues and "
              f"version 2), {counts['seeks']} seek, {counts['pauses']} pause, {counts['resumes']} resume")
    print(f"{checks} checks, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
