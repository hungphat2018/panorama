"""Checks a content-model folder against the JSON Schemas in schema/ and against the rules the schemas cannot say.

    tools\\venv-analysis\\Scripts\\python.exe -I spikes\\film-analysis\\validate-content.py DIR [--quiet]

Schema checks: content.json, file-facts.json, signals.json, shots.json, slide-paths.json, subjects.json, every line of
subjects-raw.jsonl, subtitles/track-*.json. Cross checks: frame counts agree everywhere; signals.bin has the size its header says;
the frame table is strictly increasing; `scanned` is 1 exactly on the intervals content.json calls done; shots tile the film
without gaps and their start_s equals the cue-sheet rule recomputed from pts (halfway between frames, to the bit); crops lie
inside the frame; slide-run arrays have one entry per frame; subject shot ids exist. Needs jsonschema (requirements-dev.txt).
Exit code 0 when everything holds.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import numpy as np  # noqa: E402
from jsonschema import Draft202012Validator  # noqa: E402
from referencing import Registry, Resource  # noqa: E402

from film_analysis.signals_store import SignalsStore  # noqa: E402

SCHEMA_DIR = os.path.join(HERE, "schema")


def load_registry():
    schemas, resources = {}, []
    for name in sorted(os.listdir(SCHEMA_DIR)):
        if name.endswith(".schema.json"):
            with open(os.path.join(SCHEMA_DIR, name), encoding="utf-8") as f:
                s = json.load(f)
            schemas[name] = s
            resources.append((s["$id"], Resource.from_contents(s)))
    return schemas, Registry().with_resources(resources)


class Report:
    def __init__(self, quiet):
        self.problems, self.checked, self.quiet = [], 0, quiet

    def check(self, ok, what):
        self.checked += 1
        if not ok:
            self.problems.append(what)
            print("FAIL", what)
        elif not self.quiet:
            print("ok  ", what)


def validate_file(rep, validator, path, label):
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    errors = sorted(validator.iter_errors(doc), key=lambda e: list(e.path))
    rep.check(not errors, f"{label} matches its schema" + ("" if not errors else f": {errors[0].message[:200]} at {list(errors[0].path)[:6]}"))
    return doc


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    folder = argv[0]
    rep = Report("--quiet" in argv)
    schemas, registry = load_registry()

    def validator_for(name):
        return Draft202012Validator(schemas[name], registry=registry)

    content = validate_file(rep, validator_for("content.schema.json"), os.path.join(folder, "content.json"), "content.json")
    facts = validate_file(rep, validator_for("file-facts.schema.json"), os.path.join(folder, "file-facts.json"), "file-facts.json")
    header = validate_file(rep, validator_for("signals-header.schema.json"), os.path.join(folder, "signals.json"), "signals.json")
    n = header["n_frames"]
    rep.check(content["scan"]["n_frames"] == n == facts["timeline"]["frames"], "frame counts agree (content.json, signals.json, file-facts.json)")

    # signals.bin
    store = SignalsStore.open(folder)
    last = header["columns"][-1]
    size = last["offset"] + n * last["width"] * np.dtype(last["dtype"]).itemsize
    rep.check(os.path.getsize(os.path.join(folder, "signals.bin")) >= size, "signals.bin is at least as long as its header says")
    pts = store.col("pts")
    rep.check(bool(np.all(np.diff(pts) > 0)), "frame table: pts strictly increasing")
    done = np.zeros(n, bool)
    for a, b in content["scan"]["done"]:
        done[a:b] = True
    rep.check(bool(np.array_equal(done, store.col("scanned") > 0)), "scanned == 1 exactly on the intervals content.json calls done")

    tb = facts["timeline"]["time_base"]
    tbf = tb[0] / tb[1]
    secs_abs = pts.astype(np.float64) * tbf
    t = secs_abs - secs_abs[0]
    rep.check(t[0] == 0.0, "frame 0 is time 0 exactly")

    # shots
    if os.path.isfile(os.path.join(folder, "shots.json")):
        shots = validate_file(rep, validator_for("shots.schema.json"), os.path.join(folder, "shots.json"), "shots.json")
        sl = shots["shots"]
        rep.check(sl[0]["first_frame"] == 0 and sl[-1]["last_frame"] == n - 1, "shots start at frame 0 and end at the last frame")
        rep.check(all(sl[i]["last_frame"] + 1 == sl[i + 1]["first_frame"] for i in range(len(sl) - 1)), "shots tile the film without gaps or overlaps")
        starts_ok = all(s["start_s"] == (0.0 if s["first_frame"] == 0 else float((t[s["first_frame"] - 1] + t[s["first_frame"]]) / 2.0)) for s in sl)
        rep.check(starts_ok, "every start_s equals the cue-sheet rule (halfway between the frames) to the bit")
        rep.check(all(sl[i]["end_s"] == sl[i + 1]["start_s"] for i in range(len(sl) - 1)), "end_s of a shot equals the next shot's start_s")
        W, H = facts["video"]["width"], facts["video"]["height"]
        bad = [s["id"] for s in sl if s["crop"]["rect"] and not (0 <= s["crop"]["rect"][0] < s["crop"]["rect"][2] <= W and 0 <= s["crop"]["rect"][1] < s["crop"]["rect"][3] <= H)]
        rep.check(not bad, f"crop rectangles lie inside the {W}x{H} frame" + ("" if not bad else f" (shots {bad[:5]})"))
        nv = len(shots["vocabulary"])
        rep.check(all(s["crop"]["vocab_id"] is None or 0 <= s["crop"]["vocab_id"] < nv for s in sl), "crop vocab ids refer to the vocabulary")
        shot_ids = {s["id"] for s in sl}
    else:
        shot_ids = set()

    if os.path.isfile(os.path.join(folder, "slide-paths.json")):
        sp = validate_file(rep, validator_for("slide-paths.schema.json"), os.path.join(folder, "slide-paths.json"), "slide-paths.json")
        ok = all(len(r["x"]) == len(r["y"]) == len(r["new_drawing"]) == len(r["conf"]) == r["last_frame"] - r["first_frame"] + 1 for r in sp["runs"])
        rep.check(ok, "slide-run arrays have one entry per frame of the run")
        rep.check(all(r["shot"] in shot_ids for r in sp["runs"]), "slide runs refer to existing shots")

    if os.path.isfile(os.path.join(folder, "subjects.json")):
        sj = validate_file(rep, validator_for("subjects.schema.json"), os.path.join(folder, "subjects.json"), "subjects.json")
        rep.check(all(s["shot"] in shot_ids for s in sj["shots"]), "subjects refer to existing shots")
    raw = os.path.join(folder, "subjects-raw.jsonl")
    if os.path.isfile(raw):
        v = validator_for("subjects-raw.schema.json")
        bad = 0
        with open(raw, encoding="utf-8") as f:
            for line in f:
                if line.strip() and list(v.iter_errors(json.loads(line))):
                    bad += 1
        rep.check(bad == 0, "every line of subjects-raw.jsonl matches its schema")

    sub_dir = os.path.join(folder, "subtitles")
    if os.path.isdir(sub_dir):
        v = validator_for("subtitles.schema.json")
        for name in sorted(os.listdir(sub_dir)):
            if name.endswith(".json"):
                validate_file(rep, v, os.path.join(sub_dir, name), f"subtitles/{name}")

    print(f"{rep.checked - len(rep.problems)} of {rep.checked} checks passed")
    return 0 if not rep.problems else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
