"""Be a good neighbour on the shared GPU before a GPU run: no game running, no timing lock held by another agent, GPU not busy.

The timing lock is the folder gpu-timing.lock in the shared session scratchpad (mkdir is atomic). Accuracy runs do not hold it;
they only wait while someone else does. take_timing_lock()/release_timing_lock() are for runs whose seconds are reported.
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path

SCRATCH = Path(r"C:\Users\tamla\AppData\Local\Temp\claude\C--Users-tamla-Downloads-Telegram-Desktop-panorama\2069b31a-b850-4507-8935-25f6ab90abf8\scratchpad")
LOCK = SCRATCH / "gpu-timing.lock"
GAMES = ("DD2.exe",)          # the user may start a game; add executables here


def _tasklist() -> str:
    try:
        return subprocess.run(["tasklist"], capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return ""


def gpu_util() -> int:
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=30)
        return int(r.stdout.strip().splitlines()[0])
    except Exception:
        return 0


def game_running() -> str | None:
    t = _tasklist().lower()
    for g in GAMES:
        if g.lower() in t:
            return g
    return None


def gpu_busy_reason(busy_pct: int = 85) -> str | None:
    """Why the GPU should be left alone right now, or None when it is free."""
    g = game_running()
    if g:
        return f"{g} is running"
    if LOCK.exists():
        return "gpu-timing.lock is held"
    if gpu_util() >= busy_pct:
        return f"GPU {gpu_util()}% busy"
    return None


def want_gpu(log=print) -> bool:
    """True when GPU work is fine; otherwise log why and return False so the caller uses CPU-only paths (software decode, CPU ONNX)."""
    why = gpu_busy_reason()
    if why:
        log(f"GPU left alone ({why}): this run uses software decode and the CPU provider")
        return False
    return True


def wait_for_gpu(log=print, *, max_wait_s: int = 1200, busy_pct: int = 85) -> float:
    """Wait (polling every 30 s, up to max_wait_s) while a game runs, a timing lock is held or the GPU is above busy_pct. Returns seconds waited."""
    t0 = time.time()
    while True:
        why = None
        g = game_running()
        if g:
            why = f"{g} is running"
        elif LOCK.exists():
            why = "gpu-timing.lock is held"
        elif gpu_util() >= busy_pct:
            why = f"GPU {gpu_util()}% busy"
        if why is None:
            return time.time() - t0
        if time.time() - t0 > max_wait_s:
            log(f"waited {max_wait_s}s ({why}); going on anyway with a short run")
            return time.time() - t0
        log(f"GPU not free ({why}); waiting 30 s")
        time.sleep(30)


def take_timing_lock(log=print, *, max_wait_s: int = 1200) -> float:
    """mkdir the lock folder; poll every 30 s up to max_wait_s. Returns seconds waited. Raises if never obtained."""
    t0 = time.time()
    while True:
        try:
            LOCK.mkdir()
            return time.time() - t0
        except FileExistsError:
            if time.time() - t0 > max_wait_s:
                raise TimeoutError(f"timing lock {LOCK} still held after {max_wait_s} s")
            log("timing lock held by someone else; waiting 30 s")
            time.sleep(30)


def release_timing_lock() -> None:
    try:
        LOCK.rmdir()
    except OSError:
        pass
