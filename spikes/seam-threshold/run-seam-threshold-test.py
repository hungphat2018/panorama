"""Run the anchored seam-offset threshold test on the laptop panel and score it.

Plays seam-threshold-trials.mkv full screen on the laptop panel (screen 0, 240 Hz).
Each trial shows clip 1 and clip 2; in one of them the right side of the black bar
switches pictures a few ms late. The viewer presses 1 or 2: which one split at the bar?
R replays a trial, Q quits early. About 8 minutes for 40 trials.

Scoring per condition (content, offset), from the forced choice:
  visible      the viewer beats guessing clearly: one-sided binomial p <= 0.025
               (9 or 10 of 10, 15 or more of 20)
  not visible  at most 60% right (6 of 10, 12 of 20): no better than guessing
  unclear      in between: run 10 more trials of that condition
                 (make-seam-threshold-trials.py --conditions content:refreshes)
Trials with dropped, late or mistimed frames during the clips are flagged; rerun them
if there are any.

Usage: python run-seam-threshold-test.py [--clips DIR] [--mpv MPV_EXE]
       python run-seam-threshold-test.py --score ANSWERS_JSONL
"""
import argparse
import json
import math
import subprocess
import time
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_MPV = HERE.parents[1] / "tools" / "mpv-v0.41.0-x86_64-w64-mingw32" / "mpv.exe"
DEFAULT_CLIPS = HERE / "trial-clips"


def binomial_tail(k, n):
    """P(at least k right of n by guessing)."""
    return sum(math.comb(n, i) for i in range(k, n + 1)) / 2 ** n


def verdict(correct, n):
    if binomial_tail(correct, n) <= 0.025:
        return "visible"
    if correct <= 0.6 * n:
        return "not visible"
    return "unclear: run 10 more"


def score(answers_path):
    rows = [json.loads(line) for line in answers_path.read_text().splitlines() if line.strip()]
    groups = defaultdict(list)
    for row in rows:
        groups[(row["content"], row["offset_ms"])].append(row)
    print(f"\n{len(rows)} answers in {answers_path}")
    print(f"{'content':8} {'offset':>8} {'right':>7} {'p':>7}  verdict")
    summary = []
    for (content, offset), group in sorted(groups.items()):
        n, right = len(group), sum(1 for r in group if r["correct"])
        p = binomial_tail(right, n)
        summary.append({"content": content, "offset_ms": offset, "trials": n, "right": right,
                        "p_guessing": round(p, 4), "verdict": verdict(right, n)})
        print(f"{content:8} {offset:>6} ms {right:>3}/{n:<3} {p:>7.3f}  {verdict(right, n)}")
    glitchy = [r["trial"] for r in rows if any(v for v in r.get("glitches", {}).values())]
    rates = sorted({round(r.get("display_fps", 0), 2) for r in rows})
    print(f"display rate seen by mpv: {rates} Hz; trials with dropped/late/mistimed frames: {glitchy or 'none'}")
    out = answers_path.with_suffix(".score.json")
    out.write_text(json.dumps({"conditions": summary, "glitchy_trials": glitchy, "display_fps": rates}, indent=1))
    print(f"wrote {out}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--clips", type=Path, default=DEFAULT_CLIPS)
    parser.add_argument("--mpv", type=Path, default=DEFAULT_MPV)
    parser.add_argument("--score", type=Path, help="only score an existing answers file")
    args = parser.parse_args()
    if args.score:
        score(args.score)
        return

    clip, key = args.clips / "seam-threshold-trials.mkv", args.clips / "seam-threshold-trials.json"
    if not clip.exists() or not key.exists():
        raise SystemExit(f"missing {clip} or {key}: run make-seam-threshold-trials.py first")
    results = HERE / "results"
    results.mkdir(exist_ok=True)
    answers = results / time.strftime("answers-%Y%m%d-%H%M%S.jsonl")
    answers.touch()
    subprocess.run([str(args.mpv), "--no-config", "--fs", "--screen=0", "--fs-screen=0", "--vo=gpu-next",
                    "--gpu-api=d3d11", "--hwdec=d3d11va", "--video-sync=display-resample", "--keep-open=yes",
                    "--no-input-default-bindings", "--osc=no", "--osd-level=1", "--osd-font-size=34",
                    "--cursor-autohide=always", "--no-audio",
                    f"--script={HERE / 'seam-threshold-answers.lua'}",
                    f"--script-opts=seam_threshold-trials={key},seam_threshold-answers={answers}", str(clip)])
    score(answers)


if __name__ == "__main__":
    main()
