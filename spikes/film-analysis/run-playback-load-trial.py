"""S1: does the analysis worker disturb the performer? One trial = the performer plays a film full screen while the worker scans a
film at below-normal priority; the performer's own logs say whether any refresh was late or missed.

    python -I spikes\\film-analysis\\run-playback-load-trial.py --file FILM --out RESULTS_DIR [--seconds 600] [--refresh DISPLAY2=96]
                                                               [--conditions baseline load] [--worker-file FILM2] [--workers 3]

A TRIAL TAKES OVER THE SCREENS (the performer runs full screen and changes the external screen's refresh rate for the run; Windows
restores it, ADR 0023). RUN IT ONLY WITH THE USER'S APPROVAL AND NO GAME OPEN. Esc stops the performer's windows. Use --headless 96
to test the plumbing without any window (no graphics hardware, idle priority, results not meaningful).

Conditions, run one after the other, same film, same settings:
  baseline  the performer alone
  load      the performer plus the worker, started 20 s into the run and restarted (new scan, same priority) each time it finishes
            until the performer ends; one copy of the worker's own log, the GPU sampler (nvidia-smi every 0.5 s) and the CPU
            sampler (typeperf, total processor time, every 1 s) go into the condition's folder.
After each performer run `analyze-perf1.py` makes perf1-summary.json; this script reads per monitor: skipped frames, refreshes per
frame (the pace: laptop {10: all} and external {4: all} at 23.976 fps on 240 + 96 Hz), presents that landed off their target
refresh, missed refreshes, render failures, how early the present call is before its target (p99, max).
Pass test (S1): in the load run every monitor has skipped_frames 0, missed refreshes 0, render_failed 0, landed_off_target_refresh
no more than the baseline's + 2, a refreshes-per-frame histogram with the same keys as the baseline's, lead p99 within 0.5 ms of
the baseline's. The verdict and every number go to RESULTS_DIR\\s1-result.json.
"""
import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
PERF_DIR = REPO / "spikes" / "performer-p1"
PREFIX = Path(r"C:\Users\tamla\msys64\home\tamla\p0\prefix")
RUNTIME_DIRS = [PREFIX / "ffmpeg-lgpl" / "bin", PREFIX / "libplacebo-master" / "bin", Path(r"C:\Users\tamla\msys64\ucrt64\bin")]
GAMES = ["DD2.exe", "eldenring.exe", "cyberpunk2077.exe", "Cyberpunk2077.exe", "witcher3.exe", "RDR2.exe", "GTA5.exe", "starfield.exe"]
PY_VENV = REPO / "tools" / "venv-analysis" / "Scripts" / "python.exe"
BELOW_NORMAL = 0x4000
CREATE_NO_WINDOW = 0x08000000


def running_games() -> list:
    out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True).stdout.lower()
    return [g for g in GAMES if f'"{g.lower()}"' in out]


def gpu_busy() -> str:
    r = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu,memory.used", "--format=csv,noheader,nounits"], capture_output=True, text=True)
    try:
        util, mem = (int(x) for x in r.stdout.strip().splitlines()[0].split(","))
    except (ValueError, IndexError):
        return ""
    return f"GPU at {util}% with {mem} MiB in use" if util > 15 else ""


def perf_env() -> dict:
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join([str(PERF_DIR / "bin")] + [str(p) for p in RUNTIME_DIRS] + [env.get("PATH", "")])
    return env


def performer_cmd(exe: str, film: str, seconds: int, refresh: str, headless: int, out: Path, extra: list) -> list:
    cmd = [str(PERF_DIR / "bin" / exe), "--file", film, "--seconds", str(seconds), "--out", str(out)]
    if headless:
        cmd += ["--headless", str(headless)]
    elif refresh:
        cmd += ["--refresh", refresh]
    return cmd + extra


def start_samplers(folder: Path) -> list:
    procs = []
    smi = folder / "gpu-nvidia-smi.csv"
    procs.append(subprocess.Popen(["nvidia-smi", "--query-gpu=timestamp,utilization.gpu,utilization.decoder,memory.used,power.draw,clocks.gr",
                                   "--format=csv", "-lms", "500", "-f", str(smi)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                  creationflags=CREATE_NO_WINDOW))
    cpu = open(folder / "cpu-typeperf.csv", "w")
    procs.append(subprocess.Popen(["typeperf", r"\Processor(_Total)\% Processor Time", "-si", "1"], stdout=cpu, stderr=subprocess.DEVNULL,
                                  creationflags=CREATE_NO_WINDOW))
    return procs


def stop(procs: list) -> None:
    for p in procs:
        if p.poll() is None:
            p.terminate()
    for p in procs:
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()


def run_condition(name: str, a, folder: Path) -> dict:
    folder.mkdir(parents=True, exist_ok=True)
    perf_out = folder / "performer"
    perf_out.mkdir(exist_ok=True)
    samplers = start_samplers(folder)
    t0 = time.time()
    perf = subprocess.Popen(performer_cmd(a.performer_exe, a.file, a.seconds, a.refresh, a.headless, perf_out, shlex.split(a.performer_args)), cwd=str(PERF_DIR),
                            env=perf_env(), stdout=open(folder / "performer-stdout.txt", "w"), stderr=subprocess.STDOUT)
    worker_runs = []
    try:
        if name == "load":
            time.sleep(20)
            run = 0
            while perf.poll() is None:
                run += 1
                content = folder / f"worker-content-{run}"
                log = open(folder / f"worker-{run}.log", "w")
                w = subprocess.Popen([str(PY_VENV), "-I", str(HERE / "analyze.py"), a.worker_file or a.file, "--out", str(content), "--force",
                                      "--workers", str(a.workers), "--no-subtitles", *shlex.split(a.worker_args)], stdout=log, stderr=subprocess.STDOUT,
                                     creationflags=BELOW_NORMAL | CREATE_NO_WINDOW)
                started = time.time()
                while w.poll() is None and perf.poll() is None:
                    time.sleep(1)
                if w.poll() is None:
                    w.terminate()
                    try:
                        w.wait(timeout=20)
                    except subprocess.TimeoutExpired:
                        w.kill()
                worker_runs.append({"run": run, "seconds": round(time.time() - started, 1), "exit": w.poll(), "finished": w.poll() == 0})
        perf.wait()
    finally:
        if perf.poll() is None:
            perf.terminate()
        stop(samplers)
    wall = time.time() - t0
    an = subprocess.run([sys.executable, "-I", str(PERF_DIR / "analyze-perf1.py"), str(perf_out)], capture_output=True, text=True)
    (folder / "analysis.txt").write_text(an.stdout + an.stderr, encoding="utf-8")
    summary_path = perf_out / "perf1-summary.json"
    res = {"condition": name, "wall_s": round(wall, 1), "performer_exit": perf.returncode, "worker_runs": worker_runs}
    if summary_path.exists():
        res["monitors"] = [reduce_monitor(m) for m in json.loads(summary_path.read_text())]
    else:
        res["error"] = "no perf1-summary.json (headless runs write only the cue log): " + (an.stderr or an.stdout)[-200:]
    cue = perf_out / "perf1-cuelog.json"
    if cue.exists():       # the decisions log: wrong frames, late first refreshes, seam offsets (analyze-cues.py judges it)
        ac = subprocess.run([sys.executable, "-I", str(PERF_DIR / "analyze-cues.py"), str(cue)], capture_output=True, text=True)
        (folder / "analysis-cues.txt").write_text(ac.stdout + ac.stderr, encoding="utf-8")
        lines = [ln for ln in ac.stdout.splitlines() if ln.strip()]
        res["cue_log_verdict"] = next((ln for ln in lines if ln.startswith("VERDICT")), lines[-1] if lines else ac.stderr[-200:])
    return res


def reduce_monitor(m: dict) -> dict:
    p = m.get("perf1", {})
    return {"name": m["name"], "refresh_hz": m["refresh_hz"], "presents": m["presents"], "skipped_frames": m.get("skipped_frames"),
            "refreshes_per_frame": m.get("refreshes_per_frame"), "composition": m.get("composition"),
            "landed_off_target_refresh": p.get("landed_off_target_refresh"),
            "missed_refreshes": p.get("missed_refreshes_between_stat_presents"), "render_failed": p.get("render_failed"),
            "queue_more": p.get("queue_more"), "lead_ms_p99": (p.get("lead_ms_before_target") or {}).get("p99"),
            "lead_ms_max": (p.get("lead_ms_before_target") or {}).get("max"),
            "render_gpu_ms_p99": (p.get("render_gpu_ms_all") or {}).get("p99")}


def verdict(baseline: dict, load: dict) -> dict:
    checks = []
    for b, l in zip(baseline.get("monitors", []), load.get("monitors", [])):
        n = b["name"]
        checks.append((f"{n}: skipped frames 0", l["skipped_frames"] == 0))
        checks.append((f"{n}: missed refreshes 0", (l["missed_refreshes"] or 0) == 0))
        checks.append((f"{n}: render failures 0", (l["render_failed"] or 0) == 0))
        checks.append((f"{n}: off-target presents <= baseline + 2", (l["landed_off_target_refresh"] or 0) <= (b["landed_off_target_refresh"] or 0) + 2))
        checks.append((f"{n}: same refreshes-per-frame keys as baseline", set((l["refreshes_per_frame"] or {})) == set((b["refreshes_per_frame"] or {}))))
        if l["lead_ms_p99"] is not None and b["lead_ms_p99"] is not None:
            checks.append((f"{n}: lead p99 within 0.5 ms of baseline", abs(l["lead_ms_p99"] - b["lead_ms_p99"]) <= 0.5))
    if "cue_log_verdict" in load:
        checks.append(("cue log judged PASS by analyze-cues.py in the load run", "PASS" in load["cue_log_verdict"]))
    return {"checks": [{"check": c, "pass": bool(ok)} for c, ok in checks], "pass": bool(checks) and all(ok for _, ok in checks)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", required=True, help="the film the performer plays")
    ap.add_argument("--worker-file", default="", help="the film the worker scans (default: the same film)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--seconds", type=int, default=600)
    ap.add_argument("--refresh", default="DISPLAY2=96", help="performer --refresh (empty: leave the modes alone)")
    ap.add_argument("--headless", type=int, default=0, help="96|72|100: plumbing test with virtual displays, no window")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--conditions", nargs="+", default=["baseline", "load"], choices=["baseline", "load"])
    ap.add_argument("--worker-args", default="", help='more arguments for the worker as one quoted string, e.g. "--decode-path software --models-on cpu" (plumbing tests)')
    ap.add_argument("--performer-exe", default="perf1-p3-lgpl.exe")
    ap.add_argument("--performer-args", default="", help='more arguments for the performer as one quoted string, e.g. "--cues sheet.json"')
    ap.add_argument("--allow-busy", action="store_true", help="run although a game or a busy GPU is detected (do not)")
    a = ap.parse_args()
    if not a.allow_busy:
        games, busy = running_games(), gpu_busy()
        if games or busy:
            print("not starting:", ", ".join(games) if games else "", busy, file=sys.stderr)
            return 2
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    results = {}
    for name in a.conditions:
        print(f"[s1] condition {name}: {a.seconds} s", flush=True)
        results[name] = run_condition(name, a, out / name)
        print(json.dumps(results[name].get("monitors", results[name]), indent=1), flush=True)
    summary = {"film": a.file, "worker_film": a.worker_file or a.file, "seconds": a.seconds, "refresh": a.refresh, "headless": a.headless,
               "workers": a.workers, "results": results}
    if "baseline" in results and "load" in results:
        summary["verdict"] = verdict(results["baseline"], results["load"])
        print("S1 verdict:", "PASS" if summary["verdict"]["pass"] else "FAIL", json.dumps(summary["verdict"]["checks"], indent=1))
    (out / "s1-result.json").write_text(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
