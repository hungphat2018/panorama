"""Run one exact-frame cue trial: capture mpv's window while it plays the test video.

Starts mpv fullscreen and paused with the cue shader, attaches a Windows Graphics
Capture (FFmpeg gfxcapture) to mpv's window, cropped to the top-left strip that
holds the barcode and the painted stripe, lets the helper script unpause, stops
the capture when mpv exits, then scores it with analyze-capture.py.

With --planner-mode, swap-planner.py refines the cue table while mpv plays: it
writes new shader versions next to cue-v000.glsl (a copy of --shader) in the run
directory and loads them through mpv's JSON IPC pipe. The capture is then scored
with analyze-swap-capture.py.

Desktop Duplication (ddagrab) is not used: it captured black, because the
fullscreen flip-model swapchain is promoted to a hardware overlay plane that is
not part of the composed desktop Desktop Duplication copies. Each run gets its
own empty shader cache, so no run depends on what an earlier one compiled.

Usage: python run-trial.py MPV_EXE WORK_DIR NAME --mode pts|script [--seeks LIST]
                           [--shader GLSL] [--strip-w PX] [--mpv-arg=ARG ...]
                           [--planner-mode identical|recompile|broken [--planner-arg=ARG ...]]
WORK_DIR holds test.mkv, truth.json, cue.glsl and cues.txt (from the make-*.py
scripts). LIST is "at:to/at:to" in seconds. Results go to WORK_DIR/runs/NAME/.
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
# The 2560x1600 240 Hz laptop panel is mpv screen 0 (Windows monitor enumeration
# order; mpv's Windows backend ignores --screen-name).
PANEL_SCREEN = 0
PANEL_W, PANEL_H = 2560, 1600
STRIP_W, STRIP_H = 672, 96   # top-left strip with the barcode and the painted stripe
START_DELAY = 2.5            # seconds mpv stays paused so the capture can attach first


def mpv_command(args, work, run_dir, shader, pipe):
    use_pts = "1" if args.mode == "pts" else "0"
    script_opts = [f"cuetest-seeks={args.seeks}", f"cuetest-start_delay={START_DELAY}"]
    if args.mode == "script":
        script_opts.append(f"cuetest-cues={work / 'cues.txt'}")
    ipc = [f"--input-ipc-server={pipe}"] if pipe else []
    return [args.mpv, "--no-config", "--pause", "--fs", f"--screen={PANEL_SCREEN}", f"--fs-screen={PANEL_SCREEN}",
            "--ontop", "--vo=gpu-next", "--gpu-api=d3d11", "--hwdec=d3d11va",
            f"--gpu-shader-cache-dir={run_dir / 'shader-cache'}",
            f"--glsl-shader={shader}", f"--glsl-shader-opts=use_pts={use_pts}",
            f"--script={HERE / 'cue-test-helper.lua'}", "--script-opts=" + ",".join(script_opts),
            "--osc=no", "--osd-level=0", "--cursor-autohide=always", "--input-default-bindings=no",
            f"--log-file={run_dir / 'mpv.log'}", *ipc, *args.mpv_arg, str(work / "test.mkv")]


def stop(process, quit_key=None):
    """Stop a child we started: polite quit first, then kill."""
    if process is None:
        return
    try:
        process.communicate(quit_key, timeout=30)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mpv")
    parser.add_argument("work", type=Path)
    parser.add_argument("name")
    parser.add_argument("--mode", choices=["pts", "script"], required=True)
    parser.add_argument("--seeks", default="")
    parser.add_argument("--shader", type=Path)
    parser.add_argument("--strip-w", type=int, default=STRIP_W)
    parser.add_argument("--mpv-arg", action="append", default=[])
    parser.add_argument("--planner-mode", choices=["identical", "recompile", "broken"])
    parser.add_argument("--planner-arg", action="append", default=[])
    args = parser.parse_args()

    run_dir = args.work / "runs" / args.name
    if run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True)
    shader, pipe, plan_log = args.shader or args.work / "cue.glsl", None, run_dir / "planner.jsonl"
    if args.planner_mode:
        (run_dir / "shaders").mkdir()
        shader = Path(shutil.copy(shader, run_dir / "shaders" / "cue-v000.glsl"))
        pipe = rf"\\.\pipe\panorama-cue-{args.name}"
    capture_path = run_dir / "capture.mkv"
    capture_filter = (f"gfxcapture=window_class=mpv:max_framerate=240:capture_cursor=0:"
                      f"crop_right={PANEL_W - args.strip_w}:crop_bottom={PANEL_H - STRIP_H}")
    player = subprocess.Popen(mpv_command(args, args.work, run_dir, shader, pipe))
    capture = planner = None
    with open(run_dir / "capture.log", "w") as capture_log:
        try:
            if pipe:
                planner = subprocess.Popen([sys.executable, str(HERE / "swap-planner.py"), pipe,
                                            str(args.work / "truth.json"), str(run_dir / "shaders"), str(plan_log),
                                            "--mode", args.planner_mode, *args.planner_arg],
                                           stdout=capture_log, stderr=subprocess.STDOUT)
            time.sleep(1.2)  # window exists and is fullscreen, playback still paused
            capture = subprocess.Popen(["ffmpeg", "-hide_banner", "-loglevel", "warning", "-y", "-f", "lavfi",
                                        "-i", capture_filter, "-vf", "hwdownload,format=bgra", "-fps_mode",
                                        "passthrough", "-c:v", "ffv1", str(capture_path)],
                                       stdin=subprocess.PIPE, stderr=capture_log)
            print(f"mpv exit code {player.wait(timeout=600)}")
        finally:
            if player.poll() is None:
                player.kill()
                player.wait()
            time.sleep(0.3)
            stop(capture, b"q")
            if planner is not None:
                stop(planner)

    if args.planner_mode:
        analysis = [str(HERE / "analyze-swap-capture.py"), str(args.work / "truth.json"), str(capture_path),
                    str(plan_log), "--mpv-log", str(run_dir / "mpv.log")] + (["--seeks"] if args.seeks else [])
    else:
        analysis = [str(HERE / "analyze-capture.py"), str(args.work / "truth.json"), str(capture_path)]
    scored = subprocess.run([sys.executable, *analysis, "--report", str(run_dir / "report.json")],
                            capture_output=True, text=True)
    if scored.returncode != 0:
        raise SystemExit(f"analysis failed:\n{scored.stderr}")
    report = json.loads(scored.stdout)
    report["mpv_counters"] = dict(re.findall(r"counter (\S+)=(\S*)", (run_dir / "mpv.log").read_text(errors="replace")))
    report["mode"], report["seeks"], report["mpv_args"] = args.mode, args.seeks, args.mpv_arg
    report["planner_mode"], report["planner_args"] = args.planner_mode, args.planner_arg
    (run_dir / "report.json").write_text(json.dumps(report, indent=1))
    if args.planner_mode:
        keys = ["planner_mode", "frames_seen", "no_shader_frames", "applied_mismatch", "mid_shot_changes",
                "plan_mismatch_frames", "versions_shown", "swap_latency_frames", "swap_latency_ms", "pacing",
                "main", "mpv_counters"]
    else:
        keys = ["mode", "frames_seen", "cuts_observed", "cuts_exact", "wrong_frames", "landing_delay_ms",
                "unreadable_captures", "mpv_counters"]
    print(json.dumps({k: report[k] for k in keys}, indent=1))


if __name__ == "__main__":
    main()
