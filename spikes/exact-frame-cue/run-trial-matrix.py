"""Run the exact-frame cue trial matrix and print one summary table.

Builds the 24, 23.976 and 25 fps test videos and cue shaders (plus a film-sized
3000-cue table), then runs the trials with run-trial.py. Each trial plays
fullscreen on the laptop panel for up to 2 minutes.

At 24 fps on a 240 Hz panel the frame/refresh ratio is a whole number, so mpv
switches interpolation off by itself; the 25 fps trials (ratio 9.6) are the ones
where interpolation really blends frames.

Usage: python run-trial-matrix.py MPV_EXE OUT_DIR [--only NAME,NAME]
Writes OUT_DIR/summary.json; per-trial detail is in OUT_DIR/work*/runs/NAME/.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SEEKS = "20:70/80:35/50:100"
BIG_TABLE = 3000
INTERPOLATE = ["--video-sync=display-resample", "--interpolation"]
VIDEOS = {"work24": "24", "work23976": "24000/1001", "work25": "25"}
# name, test video, how the cue arrives, seeks, shader file, extra mpv args
TRIALS = [
    ("pts", "work24", "pts", "", "cue.glsl", []),
    ("script", "work24", "script", "", "cue.glsl", []),
    ("pts-seeks", "work24", "pts", SEEKS, "cue.glsl", []),
    ("script-seeks", "work24", "script", SEEKS, "cue.glsl", []),
    ("pts-display-resample", "work24", "pts", "", "cue.glsl", ["--video-sync=display-resample"]),
    ("pts-interpolation", "work24", "pts", "", "cue.glsl", INTERPOLATE),
    ("pts-3000-cues", "work24", "pts", "", "cue-3000.glsl", []),
    ("pts-23.976", "work23976", "pts", "", "cue.glsl", []),
    ("pts-25", "work25", "pts", "", "cue.glsl", []),
    ("pts-25-interpolation", "work25", "pts", "", "cue.glsl", INTERPOLATE),
]


def run(*cmd):
    subprocess.run([str(c) for c in cmd], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mpv")
    parser.add_argument("out", type=Path)
    parser.add_argument("--only", default="", help="comma-separated trial names")
    args = parser.parse_args()
    mpv, out = args.mpv, args.out
    only = set(filter(None, args.only.split(",")))
    for name, fps in VIDEOS.items():
        work = out / name
        if not (work / "truth.json").exists():
            run(sys.executable, HERE / "make-test-video.py", work, "--fps", fps)
        run(sys.executable, HERE / "make-cue-shader.py", work / "truth.json", work / "cue.glsl",
            "--cues-out", work / "cues.txt")
    real_cues = len(json.loads((out / "work24" / "truth.json").read_text())["shot_starts"])
    run(sys.executable, HERE / "make-cue-shader.py", out / "work24" / "truth.json", out / "work24" / "cue-3000.glsl",
        "--pad-cues", BIG_TABLE - real_cues)

    summary_path = out / "summary.json"
    summary = {row["trial"]: row for row in json.loads(summary_path.read_text())} if summary_path.exists() else {}
    for name, work_name, mode, seeks, shader, extra in TRIALS:
        if only and name not in only:
            continue
        work = out / work_name
        run(sys.executable, HERE / "run-trial.py", mpv, work, name, "--mode", mode, "--seeks", seeks,
            "--shader", work / shader, *[f"--mpv-arg={a}" for a in extra])
        r = json.loads((work / "runs" / name / "report.json").read_text())
        c = r["mpv_counters"]
        summary[name] = {
            "trial": name, "frames_seen": f'{r["frames_seen"]}/{r["frames_total"]}',
            "cuts_exact": f'{r["cuts_exact"]}/{r["cuts_observed"]}', "wrong_frames": r["wrong_frames"],
            "wrong_away_from_cuts": len(r["wrong_frames_away_from_cuts"]),
            "delay_ms_min_med_max": [r["landing_delay_ms"][k] for k in ("min", "median", "max")],
            "unreadable": r["unreadable_captures"], "drops": c.get("frame-drop-count"),
            "vo_delayed": c.get("vo-delayed-frame-count"), "display_fps": c.get("display-fps"),
        }
        print(json.dumps(summary[name]), flush=True)
    order = [t[0] for t in TRIALS]
    rows = sorted(summary.values(), key=lambda row: order.index(row["trial"]) if row["trial"] in order else len(order))
    summary_path.write_text(json.dumps(rows, indent=1))


if __name__ == "__main__":
    main()
