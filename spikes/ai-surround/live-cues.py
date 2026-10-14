"""A cue sheet for the live path's runs (a planner stand-in): one shot per content-model shot at its cue-sheet start, with
the model's crop where it is not the whole frame (letterboxed shots: the bars are not picture, so the sides start at the
picture's edge), default placement, no slide. The player plays it (--cues) and the live worker reads the same file
(--cues), so both take the same pixels as picture.

    python -I -X utf8 live-cues.py CONTENT_DIR OUT.json [--no-crops]
"""
import argparse
import json
import os
import sys


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("content_dir")
    ap.add_argument("out")
    ap.add_argument("--no-crops", action="store_true", help="whole frame on every shot")
    a = ap.parse_args(argv)
    with open(os.path.join(a.content_dir, "shots.json"), encoding="utf-8") as f:
        shots = json.load(f)["shots"]
    with open(os.path.join(a.content_dir, "content.json"), encoding="utf-8") as f:
        key = json.load(f)["file"]["key"]
    with open(os.path.join(a.content_dir, "file-facts.json"), encoding="utf-8") as f:
        video = json.load(f)["video"]
    full = [0, 0, int(video["width"]), int(video["height"])]
    out, cropped = [], 0
    for s in shots:
        cue = {"start": 0.0 if not out else float(s["start_s"])}
        rect = (s.get("crop") or {}).get("rect")
        if not a.no_crops and rect and list(rect) != full:
            cue["crop"] = [int(v) for v in rect]
            cropped += 1
        out.append(cue)
    sheet = {"schema": "panorama.cues/1", "version": 1, "file_hash": key["sample_sha256"], "room_signature": "setup-a",
             "preferences_hash": "default", "shots": out}
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(sheet, f, indent=1)
    print(f"{a.out}: {len(out)} shots, {cropped} with a crop")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
