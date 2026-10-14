"""E1: does the performer keep every frame on time while an AI model runs on the same GPU? Unattended matrix runner.

Cells = load x frame latency x presenter GPU priority, named LOAD-latN-PRIORITY (e.g. gemm2-lat3-high):
  load      none | gemm1 gemm2 gemm4 gemm8 (gpu-load.py gemm --ms D) | attn2 attn8 (gpu-load.py attn --ms D) |
            painter (--painter-cmd: any command, e.g. a resident diffusion loop; it gets E1_STOP_FILE and E1_CELL_DIR)
  latency   lat1 | lat3           perf1 --frame-latency 1 | 3   (render-ahead depth)
  priority  default | high        perf1 --gpu-priority default | high
--cells takes comma-separated names or shell patterns ("none-*,gemm*-lat3-*"); painter cells need --painter-cmd.

Before the first cell: perf1 --probe (the monitors; --refresh must name the external), a screen-saver check, and a busy-GPU
check (over 30 % utilisation means another job is running: the run refuses to start unless --allow-busy-gpu).

Per cell, in this order:
  1. rest: --rest seconds (default 180, also before the first cell: the slot starts after another GPU job), then, with
     --thermal-tolerance C (default 5; 0 = off), wait until the GPU is at most C degrees warmer than at the first cell's start,
     polling every 10 s, at most --thermal-wait-max seconds (default 600). The GPU temperature is logged at every cell start.
  2. nvidia-smi logs timestamp, P-state, SM / memory clock, power (1 s average and instant), throttle reasons, temperature and
     utilisation every 100 ms (E2) into nvsmi.csv
  3. the load starts; once it prints LOAD STEADY (calibrated and looping; a painter: its --painter-ready line, or after
     --painter-warmup seconds) the runner waits --pre-wait seconds (default 10)
  4. perf1 (bin/perf1-p3-e1.exe) plays the E1 clip (out/e1/clip, made by e1-make-clip.py) full screen on both monitors for
     --seconds (default 120): --frame-latency, --gpu-priority, --cues, --room, --refresh (default DISPLAY2=96), --no-audio,
     --no-subs, plus any --perf1-arg
  5. the load stops (its stop file, then the process tree is killed), the logger stops; everything is in OUT/<stamp>/<cell>/:
     perf1's monitor-N-nearest.json, perf1-cuelog.json, gpu-share.json, perf1-stdout.txt / -stderr.txt; load-stdout.txt,
     load.json; nvsmi.csv; cell.json (the commands, clock pairs QPC/wall for lining up nvsmi.csv, temperatures, exit codes, rest)
At the end e1-analyse.py scores the run (it also runs on its own: python -I -X utf8 e1-analyse.py OUT/<stamp>).

Stopping: Ctrl+C ends the cell under way (load, player and logger are killed; Windows restores the display mode perf1 held) and
the run, then scores the cells done. Every child runs in a job object that is closed when this process ends, so nothing keeps
running on the GPU or the screens even if the runner itself is killed. Two cells in a row in which perf1 did not play abort the
run (displays, focus or a missing file: look at that cell's perf1-stderr.txt). While it runs the runner keeps the displays and
the PC awake (SetThreadExecutionState); a screen saver is not stopped by that, and Esc anywhere stops perf1 (it reads the key
globally).

--dry-run prints the plan, then runs every selected cell with short timings and no rest: the player is e1-fake-player.py
(perf1's logs with known faults), the load is gpu-load.py on the CPU at 20 % duty, a painter without --painter-cmd is a sleeper;
nvidia-smi is queried for real (read only). e1-analyse.py --check-truth must then find exactly the injected faults.

  python -I -X utf8 e1-player-under-load.py --dry-run
  python -I -X utf8 e1-player-under-load.py --cells none-lat1-default,gemm2-lat3-high --seconds 20 --rest 30   (smoke)
  python -I -X utf8 e1-player-under-load.py --painter-cmd "C:/.../python.exe -u C:/.../painter-loop.py"            (all cells)
"""
import argparse
import ctypes
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

HERE = Path(__file__).resolve().parent
PERF1_DIR = HERE.parent / "performer-p1"
BIN = PERF1_DIR / "bin"
CLIP_DIR = HERE / "out" / "e1" / "clip"
ROOM = HERE.parent / "picture-path" / "room" / "setup-a-default.json"
PY = [sys.executable, "-I", "-X", "utf8"]
LOADS = {"none": None, "gemm1": ("gemm", 1), "gemm2": ("gemm", 2), "gemm4": ("gemm", 4), "gemm8": ("gemm", 8),
         "attn2": ("attn", 2), "attn8": ("attn", 8), "painter": "painter"}
NVSMI_FIELDS = ("timestamp,pstate,clocks.sm,clocks.mem,power.draw,power.draw.instant,clocks_throttle_reasons.active,"
                "temperature.gpu,utilization.gpu")
STEADY = "LOAD STEADY"


def say(msg):
    print(time.strftime("%H:%M:%S ") + msg, flush=True)


# ---- Windows: a job object for every child, keep the PC awake ------------------------------------------------------------

class Job:
    """A job object with KILL_ON_JOB_CLOSE that holds every child: when this process ends, however it ends, its handle closes and
    every process in the job dies. Children are started suspended, put in the job, then resumed, so whatever they start (a
    venv's python.exe starts the real interpreter, cmd /c starts a painter) is in the job from its first instruction. Joining
    the job with this process instead does not work: the venv launcher runs this interpreter in its own job, whose
    SILENT_BREAKAWAY_OK lets this interpreter's children leave every job."""

    def __init__(self):
        class BASIC(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

        class EXTENDED(ctypes.Structure):
            _fields_ = [("Basic", BASIC), ("Io", ctypes.c_ulonglong * 6), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.ntdll = ctypes.WinDLL("ntdll")
        self.ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
        self.k32, self.handle = k32, k32.CreateJobObjectW(None, None)
        info = EXTENDED()
        info.Basic.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not (self.handle and k32.SetInformationJobObject(self.handle, 9, ctypes.byref(info), ctypes.sizeof(info))):
            raise SystemExit(f"cannot set up the job object (error {ctypes.get_last_error()})")

    def popen(self, cmd, **kw):
        proc = subprocess.Popen(cmd, creationflags=0x4, **kw)  # CREATE_SUSPENDED
        h = wintypes.HANDLE(int(proc._handle))
        if not self.k32.AssignProcessToJobObject(self.handle, h):
            say(f"warning: pid {proc.pid} is not in the job (error {ctypes.get_last_error()}): it may outlive a killed runner")
        self.ntdll.NtResumeProcess(h)
        return proc


def keep_awake(on):
    """Displays and system stay on while the matrix runs (a video player's request; it does not stop a screen saver)."""
    ES_CONTINUOUS, ES_SYSTEM_REQUIRED, ES_DISPLAY_REQUIRED = 0x80000000, 0x1, 0x2
    flags = ES_CONTINUOUS | (ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED if on else 0)
    ctypes.windll.kernel32.SetThreadExecutionState(ctypes.c_uint32(flags))


def screen_saver_note():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Desktop") as k:
            active = winreg.QueryValueEx(k, "ScreenSaveActive")[0]
            try:
                exe = winreg.QueryValueEx(k, "SCRNSAVE.EXE")[0]
            except OSError:
                exe = ""
        if str(active) == "1" and exe:
            return f"a screen saver is set ({exe}): it can start during the run and cover the screens; switch it off"
    except OSError:
        pass
    return None


def kill_tree(proc):
    if proc and proc.poll() is None:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()


def clock_pair():
    return [time.perf_counter(), time.time()]  # perf_counter is QueryPerformanceCounter: the clock perf1 stamps its logs with


def gpu_temp():
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=temperature.gpu", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=20).stdout
        return int(out.split()[0])
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        return None


def gpu_busy():
    """Median GPU utilisation (%) and memory used (MiB) over 2 s: another heavy job shows as tens of percent (the desktop alone
    is a few). On WDDM nvidia-smi's process list names every desktop process, so it cannot tell a job apart."""
    util, mem = [], []
    for _ in range(5):
        try:
            out = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu,memory.used", "--format=csv,noheader,nounits"],
                                 capture_output=True, text=True, timeout=20).stdout.split(",")
            util.append(float(out[0]))
            mem.append(float(out[1]))
        except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
            pass
        time.sleep(0.5)
    return (sorted(util)[len(util) // 2], sorted(mem)[len(mem) // 2]) if util else (None, None)


def sleep_s(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        time.sleep(min(1.0, end - time.monotonic()))


# ---- the plan -------------------------------------------------------------------------------------------------------------

def all_cells():
    return [{"name": f"{load}-lat{lat}-{pri}", "load": load, "latency": lat, "priority": pri}
            for load in LOADS for lat in (1, 3) for pri in ("default", "high")]


def select_cells(pattern, painter_cmd, dry):
    cells = all_cells()
    if pattern:
        pats = [p.strip() for p in pattern.split(",") if p.strip()]
        unknown = [p for p in pats if not any(fnmatch.fnmatchcase(c["name"], p) for c in cells)]
        if unknown:
            raise SystemExit(f"no cell matches {unknown}; cells are LOAD-latN-PRIORITY, e.g. gemm2-lat3-high")
        cells = [c for c in cells if any(fnmatch.fnmatchcase(c["name"], p) for p in pats)]
    if not painter_cmd and not dry:
        dropped = [c["name"] for c in cells if c["load"] == "painter"]
        if dropped and pattern and all("painter" in p for p in pattern.split(",")):
            raise SystemExit("painter cells need --painter-cmd")
        if dropped:
            say(f"no --painter-cmd: leaving out {len(dropped)} painter cells")
        cells = [c for c in cells if c["load"] != "painter"]
    return cells


def perf1_env():
    """perf1's DLL PATH, as bin/run.cmd sets it for its own process (one place defines it)."""
    text = (BIN / "run.cmd").read_text(encoding="utf-8", errors="replace")
    m = re.search(r'set "PATH=([^"]*)"', text)
    env = dict(os.environ)
    if m:
        env["PATH"] = m.group(1).replace("%~dp0", str(BIN) + "\\").replace("%PATH%", os.environ.get("PATH", ""))
    return env


def probe(a):
    """perf1 --probe (outputs only: no window, no device, no display change): the monitors and their names."""
    exe = BIN / a.perf1
    if not exe.exists():
        return None, f"{exe} is missing (compile: pwsh -File spikes/performer-p1/compile.ps1 -Output {a.perf1})"
    out = subprocess.run([str(exe), "--probe"], capture_output=True, text=True, env=perf1_env(), timeout=60).stdout
    mons = [dict(zip(("index", "name", "w", "h", "hz"), (int(m[0]), m[1], int(m[2]), int(m[3]), int(m[4]))))
            for m in re.findall(r"output (\d+) (\S+): (\d+)x(\d+) at \([^)]*\), (\d+) Hz", out)]
    return mons, out.strip()


def estimate_s(cells, a):
    t = 0.0
    for c in cells:
        t += a.rest + a.seconds + (2 if a.dry_run else 25)  # perf1 start (display switch 3 s, settle, warm-up) and stop
        if c["load"] == "painter":
            t += (2 if a.dry_run else a.painter_warmup / 2) + a.pre_wait + 10
        elif c["load"] != "none":
            t += (5 if a.dry_run else 25) + a.pre_wait  # torch, CUDA, calibration
    return t


# ---- one cell -------------------------------------------------------------------------------------------------------------

def load_command(c, a, d):
    spec = LOADS[c["load"]]
    if spec == "painter":
        if a.painter_cmd and not a.dry_run:  # a dry run never starts the real painter
            return a.painter_cmd
        return PY + ["-c", f"import time; print('{STEADY} painter stand-in', flush=True); time.sleep(3600)"]
    mode, ms = spec
    cmd = PY + [str(HERE / "gpu-load.py"), mode, "--ms", str(ms), "--duty", str(a.duty), "--stop-file", str(d / "load.stop"),
                "--out-json", str(d / "load.json"), "--seconds", str(a.pre_wait + a.seconds + 240)]
    return cmd + (["--device", "cpu"] if a.dry_run else [])


def perf1_command(c, a, d, seed):
    args = ["--file", str(a.clip), "--seconds", str(a.seconds), "--out", str(d), "--frame-latency", str(c["latency"]),
            "--gpu-priority", c["priority"], "--no-audio", "--no-subs"]
    for flag, value in (("--cues", a.cues), ("--room", a.room), ("--refresh", a.refresh)):
        if value and str(value).lower() != "none":
            args += [flag, str(value)]
    args += a.perf1_arg
    if a.dry_run:
        return PY + [str(HERE / "e1-fake-player.py")] + args + ["--fake-seed", str(seed)]
    return [str(BIN / a.perf1)] + args


def wait_ready(proc, out_path, pattern, timeout_s, need_marker):
    """True once the load's output shows the pattern (or, for a painter without one, after timeout_s); False if it died."""
    end = time.monotonic() + timeout_s
    rx = re.compile(pattern)
    while time.monotonic() < end:
        if proc.poll() is not None:
            return False
        try:
            if rx.search(out_path.read_text(encoding="utf-8", errors="replace")):
                return True
        except OSError:
            pass
        time.sleep(0.5)
    return not need_marker


def rest_before(a, state, meta):
    rest = {"rest_s": a.rest, "temp_before_rest_c": gpu_temp()}
    if a.rest > 0:
        say(f"  rest {a.rest:.0f} s (GPU {rest['temp_before_rest_c']} C)")
        sleep_s(a.rest)
    t = gpu_temp()
    rest["temp_after_rest_c"] = t
    waited = 0.0
    if a.thermal_tolerance > 0 and state["first_temp"] is not None and t is not None:
        target = state["first_temp"] + a.thermal_tolerance
        rest["target_c"] = target
        while t is not None and t > target and waited < a.thermal_wait_max:
            if waited == 0:
                say(f"  GPU {t} C, waiting for <= {target} C (first cell started at {state['first_temp']} C), "
                    f"at most {a.thermal_wait_max:.0f} s")
            sleep_s(10)
            waited += 10
            t = gpu_temp()
        if t is not None and t > target:
            say(f"  still {t} C after {waited:.0f} s: going on")
    rest["thermal_wait_s"] = waited
    meta["rest"] = rest


def run_cell(c, a, run_dir, job, state, seed):
    d = run_dir / c["name"]
    d.mkdir(parents=True, exist_ok=True)
    meta = dict(c, duty=a.duty, seconds=a.seconds, pre_wait=a.pre_wait, dry_run=a.dry_run, clock_pairs=[], status="started",
                started=time.strftime("%Y-%m-%d %H:%M:%S"))
    write_meta = lambda: (d / "cell.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    nv = load = player = None
    files = []
    try:
        rest_before(a, state, meta)
        meta["temp_start_c"] = gpu_temp()
        if state["first_temp"] is None:
            state["first_temp"] = meta["temp_start_c"]
        meta["clock_pairs"].append(clock_pair())
        meta["nvsmi_logging"] = not a.no_nvsmi
        if not a.no_nvsmi:                                  # --no-nvsmi: no driver queries while the player plays
            f_nv = open(d / "nvsmi.csv", "wb")
            files.append(f_nv)
            nv = job.popen(["nvidia-smi", f"--query-gpu={NVSMI_FIELDS}", "--format=csv", "-lms", "100"], stdout=f_nv,
                           stderr=subprocess.STDOUT)
        if c["load"] != "none":
            cmd = load_command(c, a, d)
            meta["load_cmd"] = cmd
            f_ld = open(d / "load-stdout.txt", "wb")
            files.append(f_ld)
            env = dict(os.environ, E1_STOP_FILE=str(d / "load.stop"), E1_CELL_DIR=str(d), PYTHONUNBUFFERED="1")
            load = job.popen(cmd, stdout=f_ld, stderr=subprocess.STDOUT, env=env, cwd=str(HERE))
            painter = c["load"] == "painter"
            ready = wait_ready(load, d / "load-stdout.txt", a.painter_ready if painter else STEADY,
                               a.painter_warmup if painter else 240, need_marker=not painter)
            meta["load_ready_s"] = round(time.perf_counter() - meta["clock_pairs"][0][0], 1)
            if not ready:
                meta["status"] = "load did not start (see load-stdout.txt)"
                say(f"  {meta['status']}")
                return meta
            say(f"  load steady after {meta['load_ready_s']} s; {a.pre_wait:.0f} s more before the player")
            sleep_s(a.pre_wait)
        cmd = perf1_command(c, a, d, seed)
        meta["perf1_cmd"] = cmd
        meta["clock_pairs"].append(clock_pair())
        with open(d / "perf1-stdout.txt", "wb") as so, open(d / "perf1-stderr.txt", "wb") as se:
            player = job.popen(cmd, stdout=so, stderr=se, env=perf1_env(), cwd=str(PERF1_DIR))
            end = time.monotonic() + a.seconds + 120
            while player.poll() is None and time.monotonic() < end:
                time.sleep(0.5)
            if player.poll() is None:
                say("  perf1 did not stop in time: killed")
                kill_tree(player)
        meta["perf1_rc"] = player.returncode
        meta["clock_pairs"].append(clock_pair())
        logs = sorted(d.glob("monitor-*-nearest.json"))
        if logs:
            m = json.loads(logs[0].read_text(encoding="utf-8"))
            meta["played_s"] = round(m["presents"][-1][1] - m["t0"], 2) if m.get("presents") else 0.0
        played = meta.get("played_s", 0) >= 0.9 * a.seconds
        # a full playback with a non-zero exit (a monitor error string at the end) is still scored; only no playback aborts
        meta["status"] = ("ok" if player.returncode == 0 else f"played, perf1 rc {player.returncode}") if played else "perf1 failed"
        return meta
    except KeyboardInterrupt:
        meta["status"] = "interrupted"
        raise
    finally:
        kill_tree(player)
        if load is not None:
            (d / "load.stop").touch()  # gpu-load.py ends within 50 ms and writes load.json; a painter may watch E1_STOP_FILE
            try:
                load.wait(10 if c["load"] == "painter" else 20)
            except subprocess.TimeoutExpired:
                pass
            kill_tree(load)  # whatever is left, a painter's own children included
            meta["load_rc"] = load.returncode
        time.sleep(0.5)
        if nv is not None:
            nv.terminate()
            try:
                nv.wait(10)
            except subprocess.TimeoutExpired:          # nvidia-smi can outlive terminate(); its CSV is already flushed
                kill_tree(nv)
        for f in files:
            f.close()
        meta["temp_end_c"] = gpu_temp()
        meta["clock_pairs"].append(clock_pair())
        meta["ended"] = time.strftime("%Y-%m-%d %H:%M:%S")
        write_meta()


# ---- main -----------------------------------------------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cells", default=None, help="comma-separated cell names or patterns (default: all)")
    ap.add_argument("--seconds", type=float, default=None, help="perf1 playback per cell (default 120; dry run 5)")
    ap.add_argument("--pre-wait", type=float, default=None, help="load running before perf1 starts (default 10; dry run 1)")
    ap.add_argument("--rest", type=float, default=None, help="rest before every cell, s (default 180; dry run 0)")
    ap.add_argument("--thermal-tolerance", type=float, default=None,
                    help="wait until the GPU is at most this many C above the first cell's start (default 5; 0 off; dry run 0)")
    ap.add_argument("--thermal-wait-max", type=float, default=600.0, help="cap of that wait, s (default 600)")
    ap.add_argument("--duty", type=float, default=100.0, help="gpu-load.py --duty for the gemm / attn cells (default 100)")
    ap.add_argument("--painter-cmd", default=None, help="command line of the painter load (run as is, no shell)")
    ap.add_argument("--painter-ready", default=STEADY, help=f"regex in the painter's output that means it is running (default '{STEADY}')")
    ap.add_argument("--painter-warmup", type=float, default=300.0, help="max wait for that line, s; then it counts as running")
    ap.add_argument("--clip", type=Path, default=CLIP_DIR / "jjk-pans-130s.mkv")
    ap.add_argument("--cues", default=str(CLIP_DIR / "cues.json"), help="cue sheet, or none")
    ap.add_argument("--room", default=str(ROOM), help="room file, or none")
    ap.add_argument("--refresh", default="DISPLAY2=96", help="perf1 --refresh for every cell, or none (keep the current rate)")
    ap.add_argument("--perf1", default="perf1-p3-e1.exe", help="the program in spikes/performer-p1/bin")
    ap.add_argument("--perf1-arg", action="append", default=[], help="one more perf1 argument (repeat the option for more)")
    ap.add_argument("--out", type=Path, default=HERE / "out" / "e1")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-nvsmi", action="store_true",
                    help="no nvidia-smi clock logging during playback (tests whether its driver queries cause stalls)")
    ap.add_argument("--no-analyse", action="store_true")
    ap.add_argument("--allow-busy-gpu", action="store_true", help="start although the GPU is over 30 %% busy before the first cell")
    a = ap.parse_args()
    dry = a.dry_run
    a.seconds = a.seconds if a.seconds is not None else (5.0 if dry else 120.0)
    a.pre_wait = a.pre_wait if a.pre_wait is not None else (1.0 if dry else 10.0)
    a.rest = a.rest if a.rest is not None else (0.0 if dry else 180.0)
    a.thermal_tolerance = a.thermal_tolerance if a.thermal_tolerance is not None else (0.0 if dry else 5.0)
    if dry:
        a.duty = 20.0  # the stand-in load runs on the CPU: keep it light
    cells = select_cells(a.cells, a.painter_cmd, dry)
    if not cells:
        raise SystemExit("no cells selected")
    for p in [a.clip] + [Path(x) for x in (a.cues, a.room) if x and x.lower() != "none"]:
        if not p.exists():
            raise SystemExit(f"{p} is missing" + (" (make it: python -I -X utf8 e1-make-clip.py)" if CLIP_DIR in p.parents else ""))
    if shutil.which("nvidia-smi") is None:
        raise SystemExit("nvidia-smi not on PATH")
    clip_meta = a.clip.with_name("clip.json")  # e1-make-clip.py's record of the clip in that folder (and of its cues.json)
    meta_c = json.loads(clip_meta.read_text(encoding="utf-8")) if clip_meta.exists() else {}
    if meta_c and Path(meta_c.get("clip", "")).name != a.clip.name:
        say(f"WARNING: {clip_meta} describes {Path(meta_c.get('clip', '')).name}, not {a.clip.name}: check that --cues fits the clip")
    elif meta_c.get("seconds") and a.seconds > meta_c["seconds"] - 3:  # perf1 stops at the film's end
        raise SystemExit(f"--seconds {a.seconds:g} is longer than the clip allows ({meta_c['seconds']:.0f} s): make a longer one, "
                         f"e.g. e1-make-clip.py --seconds {a.seconds + 10:.0f} --out out/e1/clip-{a.seconds + 10:.0f}s, "
                         f"and pass its --clip and --cues")

    stamp = time.strftime("%Y%m%d-%H%M%S") + ("-dry" if dry else "")
    run_dir = a.out / stamp
    run_dir.mkdir(parents=True, exist_ok=True)
    mons, probe_text = probe(a)
    say(f"E1 run {run_dir}{' (DRY RUN: fake player, CPU load)' if dry else ''}")
    notes = []
    if mons is None:
        notes.append(probe_text)
    else:
        say("perf1 --probe: " + "; ".join(f"{m['name']} {m['w']}x{m['h']} {m['hz']} Hz" for m in mons))
        dev = a.refresh.split("=")[0].replace("\\\\.\\", "") if a.refresh.lower() != "none" else None
        if len(mons) != 2:
            notes.append(f"{len(mons)} monitors found, E1 expects the laptop and one external")
        if dev and dev not in [m["name"] for m in mons]:
            notes.append(f"--refresh names {dev}, which is not an output now: pass --refresh with the external's name")
        elif dev and [m for m in mons if m["name"] == dev and (m["w"], m["h"]) == (2560, 1600)]:
            notes.append(f"--refresh names {dev}, which is the laptop panel")
    saver = screen_saver_note()
    if saver:
        notes.append(saver)
    util, mem = gpu_busy()
    busy = util is not None and util > 30
    if busy:
        notes.append(f"the GPU is busy before the first cell ({util:.0f} % used, {mem:.0f} MiB): another job would load every cell"
                     + ("" if dry or a.allow_busy_gpu else "; refusing (wait for it to end, or --allow-busy-gpu)"))
    for n in notes:
        say("WARNING: " + n)
    if (mons is None or (busy and not a.allow_busy_gpu)) and not dry:
        raise SystemExit(1)
    est = estimate_s(cells, a)
    say(f"{len(cells)} cells, about {est / 60:.0f} min (+ thermal waits up to {a.thermal_wait_max:.0f} s each): "
        + ", ".join(c["name"] for c in cells))
    say(f"per cell: rest {a.rest:.0f} s, thermal tolerance {a.thermal_tolerance:g} C, load {a.pre_wait:.0f} s before perf1, "
        f"perf1 {a.seconds:.0f} s on {a.clip.name}, refresh {a.refresh}, duty {a.duty:g} %")
    if any(c["load"] == "painter" for c in cells):
        say(f"painter: {a.painter_cmd if a.painter_cmd else '(none)'}" + (" (dry run: a sleeper stands in)" if dry else ""))
    run_meta = {"schema": "panorama.e1.run/1", "args": {k: str(v) for k, v in vars(a).items()}, "cells": [c["name"] for c in cells],
                "probe": probe_text, "warnings": notes, "started": time.strftime("%Y-%m-%d %H:%M:%S"), "status": "running"}
    (run_dir / "run.json").write_text(json.dumps(run_meta, indent=1), encoding="utf-8")

    job = Job()
    if not dry:
        keep_awake(True)
    state = {"first_temp": None}
    failed_in_row, rc = 0, 0
    try:
        for i, c in enumerate(cells):
            say(f"cell {i + 1}/{len(cells)} {c['name']}")
            meta = run_cell(c, a, run_dir, job, state, seed=i + 1)
            say(f"  {meta['status']}: perf1 rc {meta.get('perf1_rc')}, played {meta.get('played_s')} s, "
                f"GPU {meta.get('temp_start_c')} -> {meta.get('temp_end_c')} C")
            failed_in_row = failed_in_row + 1 if meta["status"] == "perf1 failed" else 0
            if failed_in_row >= 2:
                say("two cells in a row without playback: stopping the run (see their perf1-stderr.txt)")
                run_meta["status"] = "aborted"
                rc = 1
                break
        else:
            run_meta["status"] = "done"
    except KeyboardInterrupt:
        say("interrupted: the cell under way was stopped")
        run_meta["status"] = "interrupted"
        rc = 130
    finally:
        if not dry:
            keep_awake(False)
        run_meta["ended"] = time.strftime("%Y-%m-%d %H:%M:%S")
        (run_dir / "run.json").write_text(json.dumps(run_meta, indent=1), encoding="utf-8")
    if not a.no_analyse:
        res = subprocess.run(PY + [str(HERE / "e1-analyse.py"), str(run_dir)] + (["--check-truth"] if dry else []))
        if dry and res.returncode != 0:
            rc = rc or 1
    say(f"run {run_meta['status']}: {run_dir}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
