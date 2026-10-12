"""Times the worker on one film and records CPU and GPU use while it runs.

    python -I run-throughput-trial.py FILE --out RESULT_DIR --name NAME [--mode prescan|scan|seek] [--workers N] [--from SECONDS]
                                      [--segment-seconds 45] [--decode-path auto] [--keep-content]

  prescan  the quick pre-scan alone (analyze.py --prescan-only) in a fresh folder
  scan     the pre-scan and the full scan in a fresh folder
  seek     the stored pre-scan of an earlier `scan` or `prescan` trial of the same name, then the scan from --from: measures the
           time from starting the worker to the first segment around the playhead being written ("first usable result")
Samplers run beside it: nvidia-smi every 0.5 s (GPU, video-decoder and memory use) and typeperf every second (total CPU). The worker's
own accounting (CPU seconds of every process in its job object, peak memory) comes from its last output line. Run it under the GPU
timing lock when other agents share the GPU; it does not start while a game is running.
Writes RESULT_DIR/NAME-MODE.json (a small summary: kept in the repository) and, with --keep-content, leaves the content folder.
"""
import argparse
import csv
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
PY = REPO / "tools" / "venv-analysis" / "Scripts" / "python.exe"
CREATE_NO_WINDOW = 0x08000000


def games_running():
    out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True).stdout.lower()
    return [g for g in ("dd2.exe", "eldenring.exe", "cyberpunk2077.exe") if f'"{g}"' in out]


def summarize_samples(smi_csv: Path, cpu_csv: Path) -> dict:
    out = {}
    try:
        with open(smi_csv, newline="") as f:
            rows = list(csv.reader(f))[1:]
        gpu = [float(r[1].strip().split()[0]) for r in rows if len(r) > 3 and r[1].strip()]
        dec = [float(r[2].strip().split()[0]) for r in rows if len(r) > 3 and r[2].strip()]
        mem = [float(r[3].strip().split()[0]) for r in rows if len(r) > 3 and r[3].strip()]
        pwr = [float(r[4].strip().split()[0]) for r in rows if len(r) > 4 and r[4].strip()]
        out["gpu"] = {"samples": len(gpu), "util_mean_pct": round(statistics.mean(gpu), 1), "util_max_pct": max(gpu),
                      "decoder_util_mean_pct": round(statistics.mean(dec), 1), "decoder_util_max_pct": max(dec),
                      "memory_used_max_mib": max(mem), "memory_used_min_mib": min(mem), "power_mean_w": round(statistics.mean(pwr), 1) if pwr else None}
    except Exception as e:  # noqa: BLE001
        out["gpu_error"] = str(e)[:200]
    try:
        with open(cpu_csv, newline="") as f:
            vals = []
            for r in csv.reader(f):
                if len(r) >= 2:
                    try:
                        vals.append(float(r[1]))
                    except ValueError:
                        pass
        out["cpu_total"] = {"samples": len(vals), "mean_pct": round(statistics.mean(vals), 1), "max_pct": round(max(vals), 1)}
    except Exception as e:  # noqa: BLE001
        out["cpu_error"] = str(e)[:200]
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file")
    ap.add_argument("--out", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--mode", default="scan", choices=["prescan", "scan", "seek"])
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--from", dest="playhead", type=float, default=0.0)
    ap.add_argument("--segment-seconds", type=float, default=45.0)
    ap.add_argument("--decode-path", default="auto")
    ap.add_argument("--content-root", default="", help="where content folders live (default: RESULT_DIR/content)")
    ap.add_argument("--keep-content", action="store_true")
    ap.add_argument("--extra", default="", help="more worker arguments as one quoted string")
    ap.add_argument("--allow-busy", action="store_true")
    a = ap.parse_args()
    if games_running() and not a.allow_busy:
        print("a game is running: not starting", file=sys.stderr)
        return 76
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    root = Path(a.content_root) if a.content_root else out / "content"
    root.mkdir(parents=True, exist_ok=True)
    content = root / a.name
    if a.mode != "seek" and content.exists():
        shutil.rmtree(content)
    if a.mode == "seek":
        # a seek trial must start from the stored pre-scan only: forget the scan progress of the earlier trial
        manifest = content / "content.json"
        if not manifest.exists():
            print("seek mode needs the content folder of an earlier trial with the same name", file=sys.stderr)
            return 2
        m = json.loads(manifest.read_text())
        m["scan"]["done"], m["scan"]["state"] = [], "not-started"
        manifest.write_text(json.dumps(m))
        sig = None
    sampler_dir = out / f"{a.name}-{a.mode}-samples"
    sampler_dir.mkdir(exist_ok=True)
    smi_csv, cpu_csv = sampler_dir / "gpu.csv", sampler_dir / "cpu.csv"
    smi = subprocess.Popen(["nvidia-smi", "--query-gpu=timestamp,utilization.gpu,utilization.decoder,memory.used,power.draw", "--format=csv", "-lms", "500",
                            "-f", str(smi_csv)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
    cpu = subprocess.Popen(["typeperf", r"\Processor(_Total)\% Processor Time", "-si", "1"], stdout=open(cpu_csv, "w"), stderr=subprocess.DEVNULL,
                           creationflags=CREATE_NO_WINDOW)
    time.sleep(2.0)                                   # a little idle baseline in the samples
    cmd = [str(PY), "-I", str(HERE / "analyze.py"), a.file, "--out", str(content), "--workers", str(a.workers),
           "--segment-seconds", str(a.segment_seconds), "--decode-path", a.decode_path, "--from", str(a.playhead)]
    if a.mode == "prescan":
        cmd.append("--prescan-only")
    if a.extra:
        cmd += a.extra.split()
    t_launch = time.perf_counter()
    first_usable = None
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
    stderr_lines, stdout_lines = [], []

    import threading

    def drain(stream, lines):
        # both pipes are read while the worker runs: its closing summary line is longer than a pipe holds, so a reader that
        # waits for the exit first would leave the worker blocked in its last print
        for line in stream:
            lines.append(line.rstrip())
    threads = [threading.Thread(target=drain, args=(proc.stderr, stderr_lines), daemon=True),
               threading.Thread(target=drain, args=(proc.stdout, stdout_lines), daemon=True)]
    for th in threads:
        th.start()
    manifest_path = content / "content.json"
    try:
        while proc.poll() is None:
            if first_usable is None and a.mode in ("scan", "seek") and manifest_path.exists():
                try:
                    done = json.loads(manifest_path.read_text())["scan"]["done"]
                except (ValueError, KeyError, OSError):
                    done = []
                if done:
                    first_usable = time.perf_counter() - t_launch
            time.sleep(0.1)
    finally:
        wall = time.perf_counter() - t_launch
        time.sleep(2.0)
        smi.terminate()
        cpu.terminate()
        for th in threads:
            th.join(timeout=5)
    summary_line = next((ln for ln in reversed(stdout_lines) if ln.startswith("{")), "{}")
    worker = json.loads(summary_line) if summary_line else {}
    result = {"name": a.name, "mode": a.mode, "file": a.file, "workers": a.workers, "from_s": a.playhead, "segment_seconds": a.segment_seconds,
              "decode_path": a.decode_path, "exit": proc.returncode, "wall_s": round(wall, 2),
              "first_usable_result_s": round(first_usable, 2) if first_usable is not None else None,
              "prescan_s": worker.get("prescan_s"), "worker": {k: v for k, v in worker.items() if k != "scan"},
              "scan": {k: v for k, v in (worker.get("scan") or {}).items() if k not in ("segments",)},
              "samples": summarize_samples(smi_csv, cpu_csv), "stderr_tail": stderr_lines[-6:]}
    cpu_s = (worker.get("job") or {}).get("cpu")
    if cpu_s:
        result["cpu_seconds_total"] = round(cpu_s["user_s"] + cpu_s["kernel_s"], 1)
        result["cpu_core_equivalents_mean"] = round((cpu_s["user_s"] + cpu_s["kernel_s"]) / wall, 2)
    (out / f"{a.name}-{a.mode}.json").write_text(json.dumps(result, indent=1))
    print(json.dumps({k: result[k] for k in ("name", "mode", "exit", "wall_s", "first_usable_result_s", "cpu_seconds_total", "cpu_core_equivalents_mean") if k in result}))
    print(json.dumps(result["samples"]))
    if not a.keep_content and a.mode != "prescan":
        pass                                          # content folders are kept for the next mode (seek) and cleaned by the caller
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
