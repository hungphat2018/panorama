"""GPU cost measurements of the picture pass: S1 (libplacebo per monitor) against S2 (prepared frame + our resample), static and
per-refresh, at several target sizes, on one adapter. GPU times come from timer queries around each pass (libplacebo's own
and our passes'). Run only while holding the shared lock folder, as the other agents use the same GPU:

  python -I run-benchmarks.py --adapter nvidia --backend d3d11 [--set main|extra|vulkan] [--lock-wait-min 20]

The lock is a folder created with mkdir (atomic), polled every 30 s for up to --lock-wait-min minutes, and removed at the end
(also on errors). Before the first run it checks for known games and for other GPU load; if busy it waits and says so.
"""
import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
EXE = os.path.join(HERE, "bin", "picture-path.exe")
ENV = dict(os.environ, PATH=r"C:\Users\tamla\msys64\home\tamla\p0\prefix\libplacebo-master\bin;C:\Users\tamla\msys64\ucrt64\bin;"
           + os.environ.get("PATH", ""))
LOCK = (r"C:\Users\tamla\AppData\Local\Temp\claude\C--Users-tamla-Downloads-Telegram-Desktop-panorama"
        r"\2069b31a-b850-4507-8935-25f6ab90abf8\scratchpad\gpu-timing.lock")
GAMES = ("DD2.exe",)

FOLD_BENCH = ["bench-2560x1600-src1080-up1.33", "bench-1920x1080-src1080-down0.8", "bench-3840x2160-src1080-up2.0",
              "bench-3840x2160-src2160-down0.9", "bench-2560x1600-src2160-down0.67"]


def sh(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, shell=isinstance(cmd, str)).stdout


def busy_reason():
    tasks = sh("tasklist")
    for g in GAMES:
        if g.lower() in tasks.lower():
            return "game running: " + g
    out = sh(["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"]).strip().splitlines()
    try:
        util = max(int(x) for x in out if x.strip().isdigit())
    except ValueError:
        util = 0
    if util > 15:
        return "GPU busy: %d %%" % util
    return None


def acquire(wait_min):
    t0 = time.time()
    waited = False
    while True:
        reason = busy_reason()
        if reason is None:
            try:
                os.mkdir(LOCK)
                if waited:
                    print("lock acquired after %.1f min of waiting" % ((time.time() - t0) / 60.0))
                return True
            except FileExistsError:
                reason = "lock folder exists (another agent is measuring)"
        waited = True
        if time.time() - t0 > wait_min * 60:
            print("gave up waiting for the GPU after %d min: %s" % (wait_min, reason))
            return False
        print("waiting 30 s: %s" % reason, flush=True)
        time.sleep(30)


def release():
    try:
        os.rmdir(LOCK)
    except OSError:
        pass


def gpu_state():
    return sh(["nvidia-smi", "--query-gpu=clocks.gr,clocks.max.gr,power.draw,utilization.gpu,temperature.gpu", "--format=csv,noheader"]).strip()


def configs(args):
    nv = args.adapter == "nvidia"
    it = {"iters": 400, "warmup": 150} if nv else {"iters": 100, "warmup": 30}
    out = []

    def add(name, scn, st, mode="refresh", phase="refresh", fmt="rgb8", kernel=None, pace=0, **extra):
        out.append(dict(name=name, scenario=scn, structure=st, mode=mode, phase=phase, fmt=fmt, kernel=kernel, pace=pace, **it, **extra))

    if args.set == "cost":
        # The scoped matrix: Setup A's two monitor sizes (the real fold layout, and each size alone with the picture fitted),
        # source as RGB and as 4:2:0, S1 static and per refresh, S2 per refresh and its once-per-frame preparation.
        for scn in ("fold-t1", "bench-2560x1600-src1080-up1.33", "bench-1920x1080-src1080-down0.8"):
            add(scn + "-s1-static", scn, "s1", mode="static")
            add(scn + "-s1-refresh", scn, "s1")
            add(scn + "-s2-refresh", scn, "s2")
            add(scn + "-s2-prepare", scn, "s2", phase="prepare")
        for scn in ("fold-t1", "bench-2560x1600-src1080-up1.33"):
            add(scn + "-yuv420p8-s1-static", scn, "s1", mode="static", fmt="yuv420p8")
            add(scn + "-yuv420p8-s1-refresh", scn, "s1", fmt="yuv420p8")
            add(scn + "-yuv420p8-s2-refresh", scn, "s2", fmt="yuv420p8")
            add(scn + "-yuv420p8-s2-prepare", scn, "s2", phase="prepare", fmt="yuv420p8")
        add("bench-2560x1600-curve-src1080-s2-refresh", "bench-2560x1600-curve-src1080", "s2")
        for kern in ("ewa_lanczos4sharpest", "ewa_robidoux", "ewa_hermite"):
            add("bench-2560x1600-src1080-up1.33-s2-refresh-" + kern, "bench-2560x1600-src1080-up1.33", "s2", kernel=kern)
        for hz in (240,):
            add("bench-2560x1600-src1080-up1.33-s2-refresh-paced%d" % hz, "bench-2560x1600-src1080-up1.33", "s2", pace=hz)
            add("bench-2560x1600-src1080-up1.33-s1-refresh-paced%d" % hz, "bench-2560x1600-src1080-up1.33", "s1", pace=hz)
    if args.set in ("main", "all"):
        for scn in FOLD_BENCH + ["fold-t1"]:
            tag = scn
            add(tag + "-s1-static", scn, "s1", mode="static")
            add(tag + "-s1-refresh", scn, "s1")
            add(tag + "-s2-refresh", scn, "s2")
            add(tag + "-s2-prepare", scn, "s2", phase="prepare")
            add(tag + "-s1hook-refresh", scn, "s1-hook")
        add("bench-2560x1600-curve-src1080-s2-refresh", "bench-2560x1600-curve-src1080", "s2")
        add("bench-2560x1600-curve-src1080-s1hook-refresh", "bench-2560x1600-curve-src1080", "s1-hook")
        add("bench-2560x1600-curve-src1080-s1twopass-refresh", "bench-2560x1600-curve-src1080", "s1-twopass")
    if args.set in ("extra", "all"):
        for fmt in ("yuv420p8", "yuv420p10"):
            for scn in ("bench-2560x1600-src1080-up1.33", "bench-3840x2160-src2160-down0.9"):
                add("%s-%s-s1-static" % (scn, fmt), scn, "s1", mode="static", fmt=fmt)
                add("%s-%s-s1-refresh" % (scn, fmt), scn, "s1", fmt=fmt)
                add("%s-%s-s2-refresh" % (scn, fmt), scn, "s2", fmt=fmt)
                add("%s-%s-s2-prepare" % (scn, fmt), scn, "s2", phase="prepare", fmt=fmt)
        for kern in ("ewa_lanczos4sharpest", "ewa_robidoux", "ewa_hermite", "ewa_lanczos"):
            add("bench-2560x1600-src1080-up1.33-s2-refresh-" + kern, "bench-2560x1600-src1080-up1.33", "s2", kernel=kern)
            add("bench-1920x1080-src1080-down0.8-s2-refresh-" + kern, "bench-1920x1080-src1080-down0.8", "s2", kernel=kern)
        # a refresh budget view: the same loop paced at 240 Hz and 120 Hz (the GPU idles between refreshes)
        for hz in (240, 120):
            add("bench-2560x1600-src1080-up1.33-s2-refresh-paced%d" % hz, "bench-2560x1600-src1080-up1.33", "s2", pace=hz)
            add("bench-2560x1600-src1080-up1.33-s1-refresh-paced%d" % hz, "bench-2560x1600-src1080-up1.33", "s1", pace=hz)
    if args.set in ("setupa", "all"):
        for st, mode in (("s1", "static"), ("s1", "refresh"), ("s2", "refresh"), ("s1-hook", "refresh")):
            add("setupa-fold-t1-%s-%s" % (st, mode), "fold-t1", st, mode=mode)
        add("setupa-curve-t1-s2-refresh", "curve-t1", "s2")
        add("setupa-curve-t1-s1hook-refresh", "curve-t1", "s1-hook")
    return out


def run(cfg, args, out_dir):
    json_path = os.path.join(out_dir, cfg["name"] + ".json")
    cmd = [EXE, "bench", "--scenario", os.path.join("scenarios", cfg["scenario"] + ".json"), "--structure", cfg["structure"],
           "--backend", args.backend, "--adapter", args.adapter, "--src-fmt", cfg["fmt"], "--iters", str(cfg["iters"]),
           "--warmup", str(cfg["warmup"]), "--json", json_path, "--label", cfg["name"], "--mode", cfg["mode"], "--phase", cfg["phase"]]
    if cfg["kernel"]:
        cmd += ["--kernel", cfg["kernel"]]
    if cfg["pace"]:
        cmd += ["--pace-hz", str(cfg["pace"])]
    t0 = time.time()
    p = subprocess.run(cmd, cwd=HERE, env=ENV, capture_output=True, text=True)
    if p.returncode != 0:
        return None, (p.stderr or p.stdout)[-300:]
    return json.load(open(json_path, "r", encoding="utf-8")), "%.1f s" % (time.time() - t0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--adapter", default="nvidia")
    ap.add_argument("--backend", default="d3d11")
    ap.add_argument("--set", default="main")
    ap.add_argument("--lock-wait-min", type=int, default=20)
    ap.add_argument("--only", default=None, help="substring filter on the config name")
    args = ap.parse_args()
    cfgs = [c for c in configs(args) if not args.only or args.only in c["name"]]
    out_dir = os.path.join(HERE, "out", "bench", "%s-%s" % (args.backend, args.adapter))
    os.makedirs(out_dir, exist_ok=True)
    print("%d configs for %s / %s" % (len(cfgs), args.backend, args.adapter))
    if not acquire(args.lock_wait_min):
        return 3
    try:
        print("lock held; GPU before:", gpu_state())
        t_start = time.time()
        for c in cfgs:
            res, note = run(c, args, out_dir)
            if res is None:
                print("%-62s FAILED %s" % (c["name"], note.replace("\n", " ")))
                continue
            g, cpu = res["gpu_ms"], res["cpu_ms"]
            print("%-62s gpu p50 %7.3f p99 %7.3f ms | cpu p50 %6.3f | wall p50 %6.3f | %s" % (c["name"], g["p50"], g["p99"], cpu["p50"],
                  res["wall_ms"]["p50"], note), flush=True)
        print("GPU after:", gpu_state(), "| held the lock for %.1f min" % ((time.time() - t_start) / 60.0))
    finally:
        release()
    return 0


if __name__ == "__main__":
    sys.exit(main())
