"""Runs the correctness checks: harness render -> float64 reference (cached) -> comparison, for a list of checks, and prints a
table. Functional runs only: no timing, no GPU lock needed.

  python -I run-checks.py [--set fold|curve|rooms|identity|shear|blend|all] [--backend d3d11|vulkan] [--adapter nvidia|amd]
                          [--structures s1,s2] [--src-fmt rgb8] [--only NAME[,NAME]] [--skip-ref]

Each check is (scenario, structures that can render it). Results go to out/checks/<backend>-<adapter>-<src-fmt>/<scenario>-<structure>/
and a summary to out/checks/<backend>-<adapter>-<src-fmt>/summary.json.
"""
import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
EXE = os.path.join(HERE, "bin", "picture-path.exe")
ENV = dict(os.environ, PATH=r"C:\Users\tamla\msys64\home\tamla\p0\prefix\libplacebo-master\bin;C:\Users\tamla\msys64\ucrt64\bin;"
           + os.environ.get("PATH", ""))
PY = sys.executable

SETS = {
    "fold": [("fold-t1", ["s1", "s2"]), ("fold-t7", ["s1", "s2"])],
    "curve": [("curve-t1", ["s2"]), ("curve-t7", ["s2"]), ("curveflat-t1", ["s2"])],
    "rooms": [("curve-toe15-t1", ["s2"]), ("curve-toe30-t1", ["s2"]), ("curve-laptop-nearer-t1", ["s2"]),
              ("fold-toe30-t1", ["s1", "s2"]), ("fold-laptop-nearer-t1", ["s1", "s2"])],
    "identity": [("identity-laptop", ["s1", "s2"]), ("identity-external", ["s1", "s2"])],
    "shear": [("shear-fold", ["s2", "s1-strips"])],
    "blend": [("blend-fold", ["s2", "s1-blend"]), ("blend-flat", ["s2", "s1-blend"])],
}


def run(cmd, **kw):
    t0 = time.time()
    p = subprocess.run(cmd, env=ENV, capture_output=True, text=True, **kw)
    return p, time.time() - t0


def ensure_reference(scn, skip):
    path = os.path.join("scenarios", scn + ".json")
    ref_dir = os.path.join("ref", scn)
    first = None
    with open(path, "r", encoding="utf-8") as f:
        room = json.load(f)["room"]
    newest = max(os.path.getmtime(path), os.path.getmtime(os.path.join(HERE, room)), os.path.getmtime(os.path.join(HERE, "pp_resample.py")),
                 os.path.getmtime(os.path.join(HERE, "pp_geometry.py")), os.path.getmtime(os.path.join(HERE, "pp_reference.py")))
    names = [m["id"] for m in json.load(open(os.path.join(HERE, room), "r", encoding="utf-8"))["monitors"]]
    fresh = all(os.path.exists(os.path.join(HERE, ref_dir, n + ".f32")) and os.path.getmtime(os.path.join(HERE, ref_dir, n + ".f32")) > newest
                for n in names)
    if fresh or skip:
        return ref_dir, 0.0
    p, dt = run([PY, "-I", "reference-render.py", "--scenario", path, "--out", ref_dir], cwd=HERE)
    if p.returncode != 0:
        raise RuntimeError("reference failed for %s: %s" % (scn, p.stderr[-800:]))
    return ref_dir, dt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default="fold")
    ap.add_argument("--backend", default="d3d11")
    ap.add_argument("--adapter", default="nvidia")
    ap.add_argument("--structures", default=None)
    ap.add_argument("--src-fmt", default="rgb8")
    ap.add_argument("--only", default=None)
    ap.add_argument("--skip-ref", action="store_true")
    ap.add_argument("--kernel", default=None)
    args = ap.parse_args()
    names = list(SETS) if args.set == "all" else args.set.split(",")
    only = set(args.only.split(",")) if args.only else None
    want = set(args.structures.split(",")) if args.structures else None
    tag = "%s-%s-%s" % (args.backend, args.adapter, args.src_fmt)
    out_root = os.path.join("out", "checks", tag)
    summary = {}
    sys.path.insert(0, HERE)
    from pp_compare import compare
    from pp_image import read_canvas
    for setname in names:
        for scn, structures in SETS[setname]:
            if only and scn not in only:
                continue
            ref_dir, rdt = ensure_reference(scn, args.skip_ref)
            for st in structures:
                if want and st not in want:
                    continue
                out = os.path.join(out_root, "%s-%s" % (scn, st))
                cmd = [EXE, "render", "--scenario", os.path.join("scenarios", scn + ".json"), "--structure", st, "--backend", args.backend,
                       "--adapter", args.adapter, "--src-fmt", args.src_fmt, "--out", out]
                if st == "s1-strips":
                    cmd += ["--strips", "34"]
                if args.kernel:
                    cmd += ["--kernel", args.kernel]
                p, dt = run(cmd, cwd=HERE)
                if p.returncode != 0:
                    print("%-26s %-10s RENDER FAILED: %s" % (scn, st, (p.stderr or p.stdout)[-300:].strip()))
                    summary["%s/%s" % (scn, st)] = {"error": "render failed"}
                    continue
                rep = {}
                with open(os.path.join(HERE, out, "run.json"), "r", encoding="utf-8") as f:
                    run_json = json.load(f)
                for mon in run_json["monitors"]:
                    gpu, _ = read_canvas(os.path.join(HERE, out, mon["id"]))
                    ref, _ = read_canvas(os.path.join(HERE, ref_dir, mon["id"]))
                    res = compare(gpu, ref)
                    res.pop("_diff")
                    res.pop("_masks")
                    res["domain"] = mon["domain"]
                    res["resample_passes"] = mon["resample_passes"]
                    res["passes"] = [p_["desc"] for p_ in mon["passes"]]
                    rep[mon["id"]] = res
                    i, co = res["interior"], res["core"]
                    print("%-26s %-10s %-9s dom %-7s resamples %d | interior max %.3f p99 %.3f (>1: %d) | core max %.3f | flat %.3f edge %.3f | alpha %.1e" % (
                        scn, st, mon["id"], mon["domain"], mon["resample_passes"], i.get("max", 0), i.get("p99", 0), i.get("over1", 0),
                        co.get("max", 0), res["flat"].get("max", 0), res["edge"].get("max", 0), res["alpha_interior_max_diff"]))
                summary["%s/%s" % (scn, st)] = rep
    os.makedirs(os.path.join(HERE, out_root), exist_ok=True)
    with open(os.path.join(HERE, out_root, "summary-%s.json" % args.set.replace(",", "+")), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
