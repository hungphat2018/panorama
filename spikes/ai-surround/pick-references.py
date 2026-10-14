"""References for a scene (role R2, "gather"): frames from other shots of the episode that show what the hidden sides miss.

The picks below are hand-made ("oracle") from contact sheets of the shots around each anchor scene: they stand in for an
automatic search (same character, same place) and are labelled as such, so a baseline built on them states that it
assumes good retrieval. Each reference is the middle frame of its shot at full size.

    python -I pick-references.py FILM SHOTS_JSON SCENES_DIR

Writes SCENES_DIR/<scene>/refs/shot<id>.png and SCENES_DIR/<scene>/refs/refs.json.
"""
import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # python -I leaves the script's folder off sys.path
import hidden_sides as hs  # noqa: E402

# scene -> [(shot, what it shows that the scene's hidden sides need)]; graphic scenes (opening/ending credits, title card,
# eyecatch) have no useful neighbours and keep only their own frames.
PICKS = {
    "held-dialogue": [(103, "place: the same bathroom from the shower side, tiles and tub"),
                      (105, "place: the same tub and wall from the other end")],
    "edge-person-still": [(163, "character: the ponytail girl cut by the left edge, seen from the front"),
                          (159, "place: the crowd, the sign and the green-haired girl from the reverse angle")],
    "edge-head-closeup": [(260, "character: the boy cut by the left edge, seen from behind in the same room"),
                          (276, "place: the student council room, wide")],
    "dark": [(192, "place: the same night corridor and stairs"),
             (196, "place: the night corridor with figures, wider")],
}


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("film")
    ap.add_argument("shots_json")
    ap.add_argument("scenes_dir")
    a = ap.parse_args(argv)
    shots = json.load(open(a.shots_json, encoding="utf-8"))["shots"]
    for scene, picks in PICKS.items():
        rdir = os.path.join(a.scenes_dir, scene, "refs")
        os.makedirs(rdir, exist_ok=True)
        entries = []
        for sid, why in picks:
            s = shots[sid]
            t = (s["start_s"] + s["end_s"]) / 2
            path = os.path.join(rdir, f"shot{sid}.png")
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{t:.3f}", "-i", a.film,
                            "-frames:v", "1", path], check=True)
            entries.append({"file": os.path.basename(path), "shot": sid, "time_s": round(t, 3), "why": why})
            print(f"{scene:18s} shot {sid:3d} at {t:7.2f} s  {why}")
        hs.save_json({"picked_by": "hand (oracle stand-in for automatic search)", "refs": entries},
                     os.path.join(rdir, "refs.json"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
