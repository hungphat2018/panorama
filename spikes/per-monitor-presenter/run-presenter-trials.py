"""Run the per-monitor presenter in each scheduling mode, then analyze the logs.

Takes over every screen for about 25 s per mode (Esc stops a run early).

Usage: python run-presenter-trials.py OUT_DIR [--modes own,nearest,follow] [--fps 20] [--seconds 20]
                                      [--refresh DISPLAY2=96]
"""
import argparse
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXE = HERE / "bin" / "per-monitor-presenter.exe"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--modes", default="own,nearest,follow")
    parser.add_argument("--fps", default="20")
    parser.add_argument("--seconds", default="20")
    parser.add_argument("--refresh", help="DISPLAYn=HZ: switch that display for each run (restored when it ends)")
    args = parser.parse_args()
    if not EXE.exists():
        raise SystemExit(f"missing {EXE}: run compile.bat first")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    for mode in args.modes.split(","):
        print(f"--- mode {mode}", flush=True)
        refresh = ["--refresh", args.refresh] if args.refresh else []
        subprocess.run([str(EXE), "--mode", mode, "--fps", args.fps, "--seconds", args.seconds, *refresh,
                        "--out", str(args.out_dir.resolve())], check=False)
        time.sleep(2)
    subprocess.run([sys.executable, str(HERE / "analyze-per-monitor-pacing.py"), str(args.out_dir)], check=False)


if __name__ == "__main__":
    main()
