"""Run one mixed-refresh pacing trial.

Starts mpv paused and fullscreen across every screen (--fs-screen=all), starts
dda-present-log.py on each screen's barcode, lets the helper unpause, and scores
the log with analyze-span-pacing.py. With --refresh DEVICE=HZ it first switches
that display to HZ (display-refresh.py: unsaved) and always switches it back
afterwards. Takes over all screens for about SECONDS + 5 s (+ 3 s per switch).

Usage: python run-span-trial.py MPV_EXE WORK_DIR NAME [--seconds 30] [--refresh \\\\.\\DISPLAY2=96]
                                [--mpv-arg=ARG ...]
WORK_DIR holds span.mkv and span-truth.json (make-span-test-video.py).
Results go to WORK_DIR/runs/NAME/: mpv.log, present-log.json, report.json.
"""
import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
START_DELAY = 3.0   # seconds mpv waits paused, so the logger sees the first frames
LOGGER_AFTER = 1.5  # seconds after launching mpv that the logger starts


def mpv_command(args, run_dir):
    script_opts = f"spantest-start_delay={START_DELAY},spantest-play_seconds={args.seconds + 1.5}"
    return [args.mpv, "--no-config", "--pause", "--fs", "--fs-screen=all", "--ontop",
            "--vo=gpu-next", "--gpu-api=d3d11", "--hwdec=d3d11va", "--video-sync=display-resample",
            "--osc=no", "--osd-level=0", "--cursor-autohide=always", "--input-default-bindings=no",
            f"--script={HERE / 'span-test-helper.lua'}", f"--script-opts={script_opts}",
            f"--log-file={run_dir / 'mpv.log'}", *args.mpv_arg, str(args.work / "span.mkv")]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mpv")
    parser.add_argument("work", type=Path)
    parser.add_argument("name")
    parser.add_argument("--seconds", type=float, default=30)
    parser.add_argument("--refresh", action="append", default=[], help="DEVICE=HZ, switched back afterwards")
    parser.add_argument("--mpv-arg", action="append", default=[])
    args = parser.parse_args()

    run_dir = args.work / "runs" / args.name
    if run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True)
    log_path = run_dir / "present-log.json"
    refresh_tool = [sys.executable, str(HERE / "display-refresh.py")]
    switched, player, logger = [], None, None
    try:
        for item in args.refresh:
            device, hz = item.split("=")
            switched.append(device)
            subprocess.run([*refresh_tool, "set", device, hz], check=True)
        player = subprocess.Popen(mpv_command(args, run_dir))
        time.sleep(LOGGER_AFTER)
        logger = subprocess.Popen([sys.executable, str(HERE / "dda-present-log.py"), str(args.work / "span-truth.json"),
                                   str(log_path), "--seconds", str(args.seconds)])
        logger.wait(timeout=args.seconds + 60)
        print(f"mpv exit code {player.wait(timeout=args.seconds + 60)}")
    finally:
        for process in (logger, player):
            if process is not None and process.poll() is None:
                process.kill()
                process.wait()
        for device in switched:
            subprocess.run([*refresh_tool, "restore", device])

    scored = subprocess.run([sys.executable, str(HERE / "analyze-span-pacing.py"), str(log_path),
                             "--mpv-log", str(run_dir / "mpv.log"), "--report", str(run_dir / "report.json")],
                            capture_output=True, text=True)
    if scored.returncode != 0:
        raise SystemExit(f"analysis failed:\n{scored.stderr}")
    report = json.loads(scored.stdout)
    report["mpv_args"], report["refresh"] = args.mpv_arg, args.refresh
    (run_dir / "report.json").write_text(json.dumps(report, indent=1))
    print(json.dumps(report["summary"], indent=1))


if __name__ == "__main__":
    main()
