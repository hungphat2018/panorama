"""Measure how much memory mpv keeps per cue-table swap.

mpv's gpu-next keeps every shader file it has parsed (and its texture) until the
video output closes, keyed by path, and each swap needs a new path. This plays
the test video in a small window (no fullscreen, no capture) while
swap-planner.py swaps every --every seconds, samples mpv's private bytes every
second, reads mpv's GPU dedicated memory (Windows "GPU Process Memory" counter)
before the first and after the last swap, and fits bytes per swap.
--mode reload is the control: it alternates between two files mpv has already
parsed, so mpv rebuilds its render state on every swap but parses nothing new.

Usage: python measure-swap-memory.py MPV_EXE WORK_DIR OUT_DIR [--every S] [--capacity N]
                                     [--mode identical|reload]
WORK_DIR holds test.mkv, truth.json and cue-swap.glsl (built with the same --capacity).
Writes OUT_DIR/memory.json.
"""
import argparse
import ctypes
import json
import shutil
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent


class MemoryCounters(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
        (name, ctypes.c_size_t) for name in ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                                             "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
                                             "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage",
                                             "PrivateUsage")]


def private_bytes(pid):
    """Private bytes of a process (GetProcessMemoryInfo), or None once it has exited."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    handle = kernel32.OpenProcess(0x1000 | 0x0010, False, pid)  # QUERY_LIMITED_INFORMATION | VM_READ
    if not handle:
        return None
    try:
        counters = MemoryCounters(cb=ctypes.sizeof(MemoryCounters))
        ok = kernel32.K32GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)
        return counters.PrivateUsage if ok else None
    finally:
        kernel32.CloseHandle(handle)


def gpu_dedicated_bytes(pid):
    """Sum of the process's dedicated GPU memory over all adapters (Windows performance counter)."""
    query = (f"(Get-Counter '\\GPU Process Memory(pid_{pid}_*)\\Dedicated Usage' -ErrorAction SilentlyContinue)"
             ".CounterSamples | Measure-Object -Property CookedValue -Sum | ForEach-Object Sum")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", query], capture_output=True, text=True)
    try:
        return int(float(out.stdout.strip()))
    except ValueError:
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mpv")
    parser.add_argument("work", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--every", type=float, default=0.2)
    parser.add_argument("--capacity", type=int, default=8192)
    parser.add_argument("--mode", choices=["identical", "reload"], default="identical")
    args = parser.parse_args()

    if args.out.exists():
        shutil.rmtree(args.out)
    (args.out / "shaders").mkdir(parents=True)
    shutil.copy(args.work / "cue-swap.glsl", args.out / "shaders" / "cue-v000.glsl")
    pipe, plan_log = r"\\.\pipe\panorama-cue-memory", args.out / "planner.jsonl"
    player = subprocess.Popen([args.mpv, "--no-config", "--vo=gpu-next", "--gpu-api=d3d11", "--hwdec=d3d11va",
                               "--geometry=640x400+40+40", "--border=no", "--mute=yes", "--osc=no", "--osd-level=0",
                               f"--gpu-shader-cache-dir={args.out / 'shader-cache'}",
                               f"--glsl-shader={args.out / 'shaders' / 'cue-v000.glsl'}", "--glsl-shader-opts=use_pts=1",
                               f"--script={HERE / 'cue-test-helper.lua'}", f"--input-ipc-server={pipe}",
                               f"--log-file={args.out / 'mpv.log'}", str(args.work / "test.mkv")])
    planner = subprocess.Popen([sys.executable, "-B", str(HERE / "swap-planner.py"), pipe, str(args.work / "truth.json"),
                                str(args.out / "shaders"), str(plan_log), "--mode", args.mode, "--every",
                                str(args.every), "--horizon", "1.0", "--capacity", str(args.capacity), "--main-stripe"])
    start, samples, gpu = time.perf_counter(), [], {}
    time.sleep(2.5)
    gpu["before"] = (time.perf_counter(), gpu_dedicated_bytes(player.pid))
    while player.poll() is None:
        value = private_bytes(player.pid)
        if value is not None:
            samples.append((time.perf_counter(), value))
        if player.poll() is None and time.perf_counter() - start > 70 and "after" not in gpu:
            gpu["after"] = (time.perf_counter(), gpu_dedicated_bytes(player.pid))
        time.sleep(1.0)
    planner.wait(timeout=30)

    swaps = [json.loads(line)["wall"] for line in plan_log.read_text().splitlines()
             if json.loads(line)["type"] == "version"]
    counts = [sum(w <= t for w in swaps) for t, _ in samples]
    slope, intercept = np.polyfit(counts, [v for _, v in samples], 1) if len(set(counts)) > 1 else (None, None)
    gpu_swaps = sum(gpu["before"][0] < w <= gpu["after"][0] for w in swaps) if "after" in gpu else 0
    gpu_values = [gpu.get(k, (0, None))[1] for k in ("before", "after")]
    result = {
        "mode": args.mode, "swaps": len(swaps), "every_s": args.every, "capacity": args.capacity,
        "shader_file_bytes": (args.out / "shaders" / "cue-v000.glsl").stat().st_size,
        "private_bytes_per_swap": None if slope is None else round(slope),
        "private_mb_first_last": [round(samples[0][1] / 2**20, 1), round(samples[-1][1] / 2**20, 1)],
        "gpu_dedicated_mb_before_after": [None if v is None else round(v / 2**20, 1) for v in gpu_values],
        "gpu_bytes_per_swap": round((gpu_values[1] - gpu_values[0]) / gpu_swaps)
                              if gpu_swaps and None not in gpu_values else None,
        "samples": [(round(t - start, 2), c, v) for (t, v), c in zip(samples, counts)],
    }
    (args.out / "memory.json").write_text(json.dumps(result, indent=1))
    print(json.dumps({k: v for k, v in result.items() if k != "samples"}, indent=1))


if __name__ == "__main__":
    main()
