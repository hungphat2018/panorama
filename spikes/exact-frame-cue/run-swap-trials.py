"""Run the cue-table hot-swap trials and print one summary table.

Each trial plays a 75 s test video fullscreen on the laptop panel while
swap-planner.py loads a refined cue table every 5 s through mpv's IPC pipe.
Every table changes only shots that start at least 1 s after the playhead.
The shaders also carry a MAIN-stage cue (before scaling), so the same runs
show whether cue-dependent work before the OUTPUT hook lands on the exact
frame with and without interpolation.

Usage: python run-swap-trials.py MPV_EXE OUT_DIR [--only NAME,NAME] [--rescore]
Writes OUT_DIR/summary.json; per-trial detail is in OUT_DIR/work*/runs/NAME/.
--rescore re-runs only the analysis on the captures already in OUT_DIR.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CAPACITY = 8192   # fixed table size: room for any film's shots, identical GLSL in every version
SECONDS = 75
STRIP_W = 960     # make-cue-shader.SWAP_STRIP_W: capture strip reaching the version and MAIN bars
INTERPOLATE = ["--video-sync=display-resample", "--interpolation"]
VIDEOS = {"work24": "24", "work25": "25"}
PLANNER = ["--every=5", "--horizon=1.0", f"--capacity={CAPACITY}", "--main-stripe"]
# name, test video, planner mode, seeks, extra mpv args
TRIALS = [
    ("swap-identical", "work24", "identical", "", []),
    ("swap-identical-display-resample", "work24", "identical", "", ["--video-sync=display-resample"]),
    ("swap-identical-25-interpolation", "work25", "identical", "", INTERPOLATE),
    ("swap-recompile", "work24", "recompile", "", []),
    ("swap-broken-and-same-path", "work24", "broken", "", []),
    ("swap-identical-seeks", "work24", "identical", "20:50/55:25/40:65", []),
    # The demo's sync mode, with the planner confirming every swap through vo-passes.
    ("final-swap-display-resample", "work24", "identical", "", ["--video-sync=display-resample"]),
    ("final-broken-display-resample", "work24", "broken", "", ["--video-sync=display-resample"]),
]


def run(*cmd):
    subprocess.run([str(c) for c in cmd], check=True)


def rescore(work, run_dir, seeks):
    """Re-run the analysis on a trial's saved capture, keeping what run-trial.py added to the report."""
    old = json.loads((run_dir / "report.json").read_text())
    cmd = [sys.executable, HERE / "analyze-swap-capture.py", work / "truth.json", run_dir / "capture.mkv",
           run_dir / "planner.jsonl", "--mpv-log", run_dir / "mpv.log", *(["--seeks"] if seeks else [])]
    report = json.loads(subprocess.run([str(c) for c in cmd], capture_output=True, text=True, check=True).stdout)
    report.update({k: old[k] for k in ("mpv_counters", "mode", "seeks", "mpv_args", "planner_mode", "planner_args")})
    (run_dir / "report.json").write_text(json.dumps(report, indent=1))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mpv")
    parser.add_argument("out", type=Path)
    parser.add_argument("--only", default="", help="comma-separated trial names")
    parser.add_argument("--rescore", action="store_true", help="re-analyze saved captures instead of playing")
    args = parser.parse_args()
    only = set(filter(None, args.only.split(",")))
    for name, fps in VIDEOS.items():
        work = args.out / name
        if not (work / "truth.json").exists():
            run(sys.executable, HERE / "make-test-video.py", work, "--fps", fps, "--seconds", SECONDS)
        base = [sys.executable, HERE / "make-cue-shader.py", work / "truth.json"]
        run(*base, work / "cue-swap.glsl", "--capacity", CAPACITY, "--main-stripe")
        run(*base, work / "cue-swap-naive.glsl", "--capacity", CAPACITY, "--main-stripe", "--version-define")

    summary_path = args.out / "summary.json"
    summary = {row["trial"]: row for row in json.loads(summary_path.read_text())} if summary_path.exists() else {}
    for name, work_name, mode, seeks, extra in TRIALS:
        if only and name not in only:
            continue
        work = args.out / work_name
        shader = work / ("cue-swap-naive.glsl" if mode == "recompile" else "cue-swap.glsl")
        if args.rescore:
            rescore(work, work / "runs" / name, seeks)
        else:
            run(sys.executable, HERE / "run-trial.py", args.mpv, work, name, "--mode", "pts", "--seeks", seeks,
                "--shader", shader, "--strip-w", STRIP_W, "--planner-mode", mode,
                *[f"--planner-arg={a}" for a in PLANNER], *[f"--mpv-arg={a}" for a in extra])
        r = json.loads((work / "runs" / name / "report.json").read_text())
        log, c, main = r["mpv_log"] or {}, r["mpv_counters"], r["main"] or {}
        summary[name] = {
            "trial": name, "frames_seen": f'{r["frames_seen"]}/{r["frames_total"]}',
            "swaps": sum(s["kind"] == "version" for s in r["swaps"]), "versions_shown": r["versions_shown"],
            "applied_mismatch": r["applied_mismatch"], "mid_shot_changes": r["mid_shot_changes"],
            "plan_mismatch_frames": r["plan_mismatch_frames"], "no_shader_frames": len(r["no_shader_frames"]),
            "latency_frames": r["swap_latency_frames"], "latency_ms": r["swap_latency_ms"],
            "confirm_ms": r.get("swap_confirm_ms"), "reverts": r.get("reverts"),
            "hitches": r["pacing"]["hitches"], "hitches_near_swaps": r["pacing"]["hitches_near_swaps"],
            "missing_frames": r["pacing"]["missing_frames"], "gap_ms": r["pacing"]["gap_ms"],
            "swaps_with_compile": log.get("swaps_with_compile"), "compile_ms_per_swap": log.get("compile_ms_per_swap"),
            "main_mismatch_frames": main.get("mismatch_frames"), "main_mismatch_on_cuts": main.get("mismatch_on_cut_frames"),
            "main_cuts_checked": main.get("cut_frames_checked"), "main_blended": main.get("blended"),
            "drops": c.get("frame-drop-count"), "vo_delayed": c.get("vo-delayed-frame-count"),
            "mistimed": c.get("mistimed-frame-count"), "display_fps": c.get("display-fps"),
        }
        print(json.dumps(summary[name]), flush=True)
    order = [t[0] for t in TRIALS]
    rows = sorted(summary.values(), key=lambda row: order.index(row["trial"]) if row["trial"] in order else len(order))
    summary_path.write_text(json.dumps(rows, indent=1))


if __name__ == "__main__":
    main()
