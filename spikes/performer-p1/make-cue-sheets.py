"""A planner stand-in: writes cue sheet versions (panorama.cues/1) and control scripts for a test clip.

Reads the clip's shots.json (make-cue-test-clip.py) or any file with {"width", "height", "shot_start_seconds"}.

  python -I make-cue-sheets.py CLIP_DIR OUT_DIR [--versions 6] [--seed 3] [--script KIND] [--span 40]

Versions: v1 is the plain picture (no crop, default placement) on every shot; every later version picks, per shot, a crop of 0 /
5 / 10 % inset and a placement (the default, or shifted left or right by up to 25 mm, or a few mm smaller), differently for
each version, so a version change is visible on screen exactly where it takes effect. Files: OUT_DIR/cues-v1.json ...

Scripts (--script, written to OUT_DIR/script-KIND.jsonl, lines in perf1-control.h's format):
  swaps      a new version every 2-4 s of wall time
  adversarial  like swaps, plus a seek 50 ms before or up to 150 ms after every swap, to a point near a shot boundary
  scrub      bursts of 5 seeks per second between swaps
  breaks     swaps with pauses and resumes (and a seek while paused)
  stress     a seek every 50 ms to a random time for --span seconds, with three swaps in between
"""
import argparse
import json
import random
from fractions import Fraction
from pathlib import Path


def build_versions(truth, n_versions, rng):
    w, h = truth["width"], truth["height"]
    starts = truth["shot_start_seconds"]
    versions = []
    for v in range(1, n_versions + 1):
        shots = []
        for i, s in enumerate(starts):
            shot = {"start": s if i else 0.0}
            if v > 1:
                inset = rng.choice([0.0, 0.05, 0.10])
                if inset:
                    x, y = round(w * inset), round(h * inset)
                    shot["crop"] = [x, y, w - x, h - y]
                shift = rng.choice([0.0, 0.0, -25.0, -12.0, 12.0, 25.0])
                size = rng.choice([215.0, 215.0, 205.0, 195.0])
                if shift or size != 215.0:
                    shot["placement"] = {"center_mm": [354.0 + shift, 107.5], "height_mm": size}
            shots.append(shot)
        versions.append({"schema": "panorama.cues/1", "version": v, "file_hash": "test-clip", "room_signature": "setup-a",
                         "preferences_hash": "default", "shots": shots})
    return versions


def script_lines(kind, truth, n_versions, rng, span):
    lines = []
    duration = float(truth["frames"] / Fraction(truth["fps"]))
    starts = truth["shot_start_seconds"]

    def near_boundary():
        return max(0.0, min(duration - 3.0, rng.choice(starts[1:]) + rng.uniform(-0.1, 0.1)))

    if kind == "stress":
        n = int(span / 0.05)
        swaps_at = {n // 4: 2, n // 2: 3, 3 * n // 4: 4}   # versions 2, 3, 4 a quarter, half and three quarters of the way
        for i in range(n):
            lines.append({"after": 0.05, "cmd": "seek", "t": round(rng.uniform(0, duration - 3), 3)})
            v = swaps_at.get(i)
            if v and v <= n_versions:
                lines.append({"after": 0.0, "cmd": "load", "file": f"cues-v{v}.json"})
        lines.append({"after": 0.5, "cmd": "status"})
        return lines
    t_total = 0.0
    swap = 2
    while swap <= n_versions and t_total < span:
        gap = rng.uniform(2.0, 4.0)
        t_total += gap
        if kind == "adversarial":
            delta = rng.uniform(-0.050, 0.150)
            if delta < 0:
                lines.append({"after": round(max(0.0, gap + delta), 3), "cmd": "seek", "t": round(near_boundary(), 3)})
                lines.append({"after": round(-delta, 3), "cmd": "load", "file": f"cues-v{swap}.json"})
            else:
                lines.append({"after": round(gap, 3), "cmd": "load", "file": f"cues-v{swap}.json"})
                lines.append({"after": round(delta, 3), "cmd": "seek", "t": round(near_boundary(), 3)})
        elif kind == "scrub":
            lines.append({"after": round(gap, 3), "cmd": "load", "file": f"cues-v{swap}.json"})
            for _ in range(rng.randint(8, 14)):
                lines.append({"after": round(0.2 + rng.uniform(-0.02, 0.02), 3), "cmd": "seek", "t": round(rng.uniform(0, duration - 3), 3)})
        elif kind == "breaks":
            lines.append({"after": round(gap, 3), "cmd": "load", "file": f"cues-v{swap}.json"})
            lines.append({"after": round(rng.uniform(0.0, 0.15), 3), "cmd": "pause"})
            if rng.random() < 0.5:
                lines.append({"after": round(rng.uniform(0.2, 0.6), 3), "cmd": "seek", "t": round(near_boundary(), 3)})
            lines.append({"after": round(rng.uniform(0.4, 1.2), 3), "cmd": "resume"})
        else:  # swaps
            lines.append({"after": round(gap, 3), "cmd": "load", "file": f"cues-v{swap}.json"})
        swap += 1
    lines.append({"after": 1.0, "cmd": "status"})
    return lines


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("clip_dir", type=Path)
    ap.add_argument("out_dir", type=Path)
    ap.add_argument("--versions", type=int, default=6)
    ap.add_argument("--seed", type=int, default=3)
    ap.add_argument("--script", choices=["swaps", "adversarial", "scrub", "breaks", "stress"])
    ap.add_argument("--span", type=float, default=40.0, help="seconds of wall time the script should cover")
    a = ap.parse_args()
    truth = json.loads((a.clip_dir / "shots.json").read_text())
    rng = random.Random(a.seed)
    a.out_dir.mkdir(parents=True, exist_ok=True)
    for v in build_versions(truth, a.versions, rng):
        (a.out_dir / f"cues-v{v['version']}.json").write_text(json.dumps(v, indent=1))
    print(f"{a.versions} cue sheets in {a.out_dir}")
    if a.script:
        lines = script_lines(a.script, truth, a.versions, rng, a.span)
        # the load lines name files relative to the working directory of the run: write them next to the script
        for ln in lines:
            if "file" in ln:
                ln["file"] = str(a.out_dir / ln["file"]).replace("\\", "/")
        (a.out_dir / f"script-{a.script}.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n")
        print(f"script-{a.script}.jsonl: {len(lines)} lines")


if __name__ == "__main__":
    main()
