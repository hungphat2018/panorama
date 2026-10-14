"""The step planner's rates over a whole episode, from the live worker's cached analyses (phase 4's success criterion:
the per-step GEN rate at tau 0.10 against the research report's 4.7 drawings/s on JJK S03E12, and the full planner's).

Reads <cache>/<film key folder>/analysis/*.npz made with the current analysis version (ShotAnalysis: states, u, flags).
  research rate   new drawings whose worse left/right band has U_step > 0.10 (research report 261013-1049, section 4),
                  per film second
  planner rates   per side, the drawings the planner marks PATCH or GEN (a generator call each), and GEN alone
  states          the share of each step state per side over all frames
    python -I -X utf8 planner-stats.py [--cache out/live/cache] [--film F] [--fps 23.976] [--out FILE]
"""
import argparse
import glob
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, "spikes", "film-analysis"))
from film_analysis import file_key  # noqa: E402
from live import step_planner as sp  # noqa: E402
from live.analyse_v0 import ANALYSIS_VERSION  # noqa: E402
from live.side_cache import key_hash  # noqa: E402

FILM = "C:/Users/tamla/Downloads/Jujutsu.Kaisen.S03E12.FiNAL.MULTi.1080p.WEBRiP.x265-KAF.mkv"
SIDES = sp.SIDES


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cache", default=os.path.join(HERE, "out", "live", "cache"))
    ap.add_argument("--film", default=FILM)
    ap.add_argument("--fps", type=float, default=24000 / 1001)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    folder = os.path.join(a.cache, file_key.cache_folder_name(file_key.sample_key(a.film)), "analysis")
    files = sorted(glob.glob(os.path.join(folder, "*.npz")))
    frames = new = research = 0
    gen = np.zeros(4)
    call = np.zeros(4)
    hist = np.zeros((4, 7))
    covered = []
    for p in files:
        with np.load(p) as z:
            if "states" not in z.files or "u" not in z.files:
                continue                                          # an analysis of an earlier version
            first, n = int(z["first"]), int(z["n"])
            if key_hash(ANALYSIS_VERSION, [round(float(v), 3) for v in z["crop"]]) not in os.path.basename(p):
                continue
            st, u, nd = np.asarray(z["states"]), np.asarray(z["u"]), np.asarray(z["new_drawing"], bool)
        covered.append((first, n))
        frames += n
        new += int(nd[1:].sum())
        worse = np.maximum(u[1:, 0, 0], u[1:, 2, 0])            # left and right U_step
        research += int(((worse > 0.10) & nd[1:]).sum())
        for k in range(4):
            gen[k] += int((st[1:, k] == sp.STATES["gen"]).sum())
            call[k] += int(np.isin(st[1:, k], (sp.STATES["gen"], sp.STATES["patch"])).sum())
            hist[k] += np.bincount(st[:, k], minlength=7)[:7]
    if not frames:
        raise SystemExit(f"no analyses of version {ANALYSIS_VERSION!r} in {folder}")
    film_s = frames / a.fps
    names = {v: k for k, v in sp.STATES.items()}
    res = {"analysis_version": ANALYSIS_VERSION, "shots": len(covered), "frames": frames, "film_s": round(film_s, 1),
           "new_drawings_per_s": round(new / film_s, 2),
           "research_gen_per_s": round(research / film_s, 2), "research_reference_per_s": 4.7,
           "planner_calls_per_s": {s: round(call[k] / film_s, 2) for k, s in enumerate(SIDES)},
           "planner_gen_per_s": {s: round(gen[k] / film_s, 2) for k, s in enumerate(SIDES)},
           "states_share": {s: {names[i]: round(float(hist[k, i] / max(1, hist[k].sum())), 3) for i in range(7)}
                            for k, s in enumerate(SIDES)}}
    text = json.dumps(res, indent=1)
    print(text)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
