"""Pick test scenes from a film's content model and cut them out as short clips, for hidden-sides comparisons.

Every outpainting candidate is judged on the same scenes: the true sides of a real frame are hidden (only the centre is
given) and the candidate redraws them; the hidden part is the answer key. Scenes are picked by rule from the content model,
one per kind of hard case, so the set can be rebuilt for any film:

  wide-no-subject      longest shot with no face, head or person: scenery the sides should just continue
  edge-person-still    still camera, a person box touches the left or right picture edge (a body cut by the frame)
  edge-head-closeup    a head box touching an edge and taller than 40% of the picture (close-up cut by the frame)
  held-dialogue        a face on screen, nearly every step repeats the drawing (talking head on held drawings)
  pan-horizontal       the camera slide run with the largest sideways travel (any purity)
  tilt-vertical        the pure slide run with the largest vertical travel
  dark                 the darkest shot of at least 2 s
  action               the shot with the fastest global motion and the fewest held drawings, at least 1.5 s

--set moving picks story shots whose hidden sides move (an action episode), for the video recipes and routing by motion.
Story = region "normal" (no opening, no credits), at least --min-frames long, mean luma 24-230 (no fades or flashes). It
needs --edge-stats, edge-band-change.py's output for the same film (per shot and per side: drift of the outer 25% band, the
hidden side at s = 0.5, from the shot's middle frame, and the share of steps that change it):
  pan                  the slide run with the largest sideways travel, at least 1.5 s: the sides scroll with the camera
  edge-move            no slide run, and a hidden side moves the most (largest side drift x changed-step share): a
                       character or effect moving at the edge while the camera stands
  one-side             one hidden side holds for the whole shot (drift <= the limit) while the other moves the most:
                       the routing test (plate for the still side, video for the moving one)
  action               the fastest global motion with the fewest held drawings
  still-edge-person    no slide run, both hidden sides hold, a person box touches the left/right edge (longest): the
                       still-shot control in this show's style
Each shot is picked once (kinds in that order); the five best candidates per kind are printed, and --force kind=shot[,...]
replaces a pick after a look at them.

    python -I make-test-scenes.py FILM CONTENT_DIR OUT_DIR [--seconds 3] [--skip-frames 2]
                                  [--set moving --edge-stats FILE] [--prefix jjk-] [--force kind=shot,...] [--dry-run]

Writes OUT_DIR/<prefix><kind>/clip.mkv (x264 CRF 10, 8-bit 4:2:0, full resolution), key.png (the clip's middle frame) and
scene.json (shot, times, why picked). Needs ffmpeg on PATH (or PANORAMA_FFMPEG).
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

EDGE_MARGIN = 0.01        # a box within 1% of the picture width from the left/right edge touches it


def load(content_dir, name):
    with open(os.path.join(content_dir, name), encoding="utf-8") as f:
        return json.load(f)


def touches_edge(box, w):
    return box["x"] <= EDGE_MARGIN * w or box["x"] + box["w"] >= w - EDGE_MARGIN * w


def pick_scenes(shots, subjects, slides, w, h):
    """Return {kind: (shot, run or None, why)} using the rules in the module docstring."""
    normal = [s for s in shots if s["region"]["kind"] == "normal" and s.get("motion") and s["frames"] >= 24]
    tracks = {t["shot"]: t["tracks"] for t in subjects}
    by_id = {s["id"]: s for s in shots}
    has = lambda s, cls: any(t["class"] == cls for t in tracks.get(s["id"], []))
    edge = lambda s, cls, pred=lambda b: True: any(
        t["class"] == cls and any(touches_edge(b, w) and pred(b) for b in t["boxes"]) for t in tracks.get(s["id"], []))
    still = lambda s: s["motion"]["speed_px_s_p90"] < 5.0
    longest = lambda xs: max(xs, key=lambda s: s["frames"]) if xs else None
    out = {}

    s = longest([s for s in normal if not tracks.get(s["id"])])
    if s: out["wide-no-subject"] = (s, None, "longest shot with no face, head or person")
    s = longest([s for s in normal if still(s) and edge(s, "person")])
    if s: out["edge-person-still"] = (s, None, "longest still shot with a person box at the left/right edge")
    s = longest([s for s in normal if edge(s, "head", lambda b: b["h"] > 0.4 * h)])
    if s: out["edge-head-closeup"] = (s, None, "longest shot with a head taller than 40% of the picture at an edge")
    s = longest([s for s in normal if has(s, "face") and s["motion"]["held_share"] >= 0.95])
    if s: out["held-dialogue"] = (s, None, "longest shot with a face and >= 95% held steps")
    runs = slides["runs"]
    r = max(runs, key=lambda r: max(r["x"]) - min(r["x"]), default=None)
    if r: out["pan-horizontal"] = (by_id[r["shot"]], r, f"largest sideways slide travel ({max(r['x']) - min(r['x']):.0f} px, {r['purity']['label']})")
    pure_v = [r for r in runs if r["purity"]["label"] == "pure"]
    r = max(pure_v, key=lambda r: max(r["y"]) - min(r["y"]), default=None)
    if r: out["tilt-vertical"] = (by_id[r["shot"]], r, f"largest pure vertical slide travel ({max(r['y']) - min(r['y']):.0f} px)")
    dark = [s for s in normal if s.get("luma") and s["end_s"] - s["start_s"] >= 2.0]
    s = min(dark, key=lambda s: s["luma"]["mean"], default=None)
    if s: out["dark"] = (s, None, f"darkest shot >= 2 s (mean luma {s['luma']['mean']})")
    act = [s for s in normal if s["end_s"] - s["start_s"] >= 1.5]
    s = max(act, key=lambda s: s["motion"]["speed_px_s_p90"] * (1.0 - s["motion"]["held_share"] + 0.05), default=None)
    if s: out["action"] = (s, None, f"fastest motion, fewest holds (p90 {s['motion']['speed_px_s_p90']} px/s, held {s['motion']['held_share']})")
    return out


def pick_moving(shots, subjects, slides, edge, w, min_frames, force, top=5):
    """Return {kind: (shot, run or None, why)} for the --set moving rules (module docstring), printing the top candidates."""
    story = [s for s in shots if s["region"]["kind"] == "normal" and s.get("motion") and s["frames"] >= min_frames
             and s.get("luma") and 24 <= s["luma"]["mean"] <= 230]
    per = {e["shot"]: e for e in edge["bands"]["25%"]["per_shot"]}
    story = [s for s in story if s["id"] in per]
    tracks = {t["shot"]: t["tracks"] for t in subjects}
    runs_ = [r for r in slides["runs"] if r["seconds"] >= 1.5]
    slid = {r["shot"] for r in slides["runs"]}
    by_id = {s["id"]: s for s in shots}
    person_at_edge = lambda s: any(t["class"] == "person" and any(touches_edge(b, w) for b in t["boxes"])
                                   for t in tracks.get(s["id"], []))
    travel = lambda r: max(r["x"]) - min(r["x"])
    moving = lambda s, i: per[s["id"]]["drift_p90_lr"][i] * per[s["id"]]["changed_step_share_lr"][i]
    story_ids = {s["id"] for s in story}
    rules = [
        ("pan", [(by_id[r["shot"]], r, travel(r)) for r in runs_ if r["shot"] in story_ids],
         lambda c: f"largest sideways slide travel ({c[2]:.0f} px over {c[1]['seconds']:.1f} s, {c[1]['purity']['label']})"),
        ("edge-move", [(s, None, max(moving(s, 0), moving(s, 1))) for s in story if s["id"] not in slid],
         lambda c: f"camera still, a hidden side moves (drift p90 {per[c[0]['id']]['drift_p90_lr']}, "
                   f"changed steps {per[c[0]['id']]['changed_step_share_lr']})"),
        ("one-side", [(s, None, max(moving(s, i) for i in (0, 1) if not per[s["id"]]["plate_holds_lr"][i]))
                      for s in story if sum(per[s["id"]]["plate_holds_lr"]) == 1],
         lambda c: f"one side holds, the other moves (holds {per[c[0]['id']]['plate_holds_lr']}, "
                   f"drift p90 {per[c[0]['id']]['drift_p90_lr']}, changed steps {per[c[0]['id']]['changed_step_share_lr']})"),
        ("action", [(s, None, s["motion"]["speed_px_s_p90"] * (1.0 - s["motion"]["held_share"] + 0.05)) for s in story],
         lambda c: f"fastest motion, fewest holds (p90 {c[0]['motion']['speed_px_s_p90']} px/s, "
                   f"held {c[0]['motion']['held_share']})"),
        ("still-edge-person", [(s, None, s["frames"]) for s in story if s["id"] not in slid
                               and s["motion"]["speed_px_s_p90"] < 5.0 and all(per[s["id"]]["plate_holds_lr"])
                               and person_at_edge(s)],
         lambda c: f"longest still shot, both sides hold, a person box at the edge ({c[0]['frames']} frames)"),
    ]
    out, used = {}, set()
    for kind, cands, why in rules:
        cands = sorted(cands, key=lambda c: -c[2])
        print(f"{kind}: " + "; ".join(f"shot {c[0]['id']} ({c[0]['start_s']:.1f} s, {c[0]['frames']} fr) {c[2]:.1f}"
                                      for c in cands[:top]))
        if kind in force:
            pick = next((c for c in cands if c[0]["id"] == force[kind]), None)
            if pick is None:                                      # forced outside the rule: take the shot as it is
                s = by_id[force[kind]]
                run_ = max((r for r in runs_ if r["shot"] == s["id"]), key=travel, default=None) if kind == "pan" else None
                pick = (s, run_, 0.0)
        else:
            pick = next((c for c in cands if c[0]["id"] not in used), None)
        if pick:
            used.add(pick[0]["id"])
            out[kind] = (pick[0], pick[1], ("picked by eye from the candidates (--force); the rule's measure: " if kind in force
                                            else "") + why(pick))
    return out


def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(" ".join(cmd) + "\n" + p.stderr[-2000:])


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("film")
    ap.add_argument("content_dir")
    ap.add_argument("out_dir")
    ap.add_argument("--seconds", type=float, default=3.0)
    ap.add_argument("--skip-frames", type=int, default=2, help="frames to skip after the cut")
    ap.add_argument("--set", default="episode", choices=["episode", "moving"])
    ap.add_argument("--edge-stats", help="edge-band-change.py output for the same film (needed by --set moving)")
    ap.add_argument("--min-frames", type=int, default=49, help="--set moving: shortest story shot (49 = 2 s)")
    ap.add_argument("--prefix", default="", help="scene folder prefix, e.g. jjk- for a second film in the same OUT_DIR")
    ap.add_argument("--force", default="", help="kind=shot[,kind=shot]: take these shots instead of the rule's pick")
    ap.add_argument("--dry-run", action="store_true", help="print the picks and candidates, cut nothing")
    a = ap.parse_args(argv)

    ffmpeg = os.environ.get("PANORAMA_FFMPEG") or shutil.which("ffmpeg")
    if not ffmpeg:
        print("ffmpeg not found", file=sys.stderr)
        return 2
    shots = load(a.content_dir, "shots.json")
    facts = load(a.content_dir, "file-facts.json")
    w, h = facts["video"]["width"], facts["video"]["height"]
    fps = shots["frame_count"] / shots["shots"][-1]["end_s"]
    if a.set == "moving":
        if not a.edge_stats:
            raise SystemExit("--set moving needs --edge-stats (edge-band-change.py output)")
        with open(a.edge_stats, encoding="utf-8") as f:
            edge = json.load(f)
        force = {k: int(v) for k, v in (p.split("=") for p in a.force.split(",") if p)}
        scenes = pick_moving(shots["shots"], load(a.content_dir, "subjects.json")["shots"],
                             load(a.content_dir, "slide-paths.json"), edge, w, a.min_frames, force)
    else:
        scenes = pick_scenes(shots["shots"], load(a.content_dir, "subjects.json")["shots"],
                             load(a.content_dir, "slide-paths.json"), w, h)
    if a.dry_run:
        for kind, (shot, run_, why) in scenes.items():
            print(f"{a.prefix}{kind:18s} shot {shot['id']:4d} at {shot['start_s']:8.2f} s  {why}")
        return 0

    for kind, (shot, run_, why) in scenes.items():
        first = run_["first_frame"] if run_ else shot["first_frame"]
        last = run_["last_frame"] if run_ else shot["last_frame"]
        first = min(first + a.skip_frames, last)
        n = max(1, min(last - first + 1, int(round(a.seconds * fps))))
        # Seek by the frame's own time: frame i of the content model lies at start_s + (i - first_frame) / fps.
        t0 = shot["start_s"] + (first - shot["first_frame"] + 0.5) / fps
        d = os.path.join(a.out_dir, a.prefix + kind)
        os.makedirs(d, exist_ok=True)
        clip = os.path.join(d, "clip.mkv")
        run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{t0:.4f}", "-i", a.film, "-map", "0:v:0",
             "-frames:v", str(n), "-an", "-sn", "-c:v", "libx264", "-preset", "slow", "-crf", "10",
             "-pix_fmt", "yuv420p", clip])
        run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", clip, "-vf", f"select=eq(n\\,{n // 2})",
             "-frames:v", "1", os.path.join(d, "key.png")])
        meta = {"kind": a.prefix + kind, "why": why, "shot": shot["id"], "run": run_["id"] if run_ else None,
                "first_frame": first, "frames": n, "start_s": round(t0, 4), "fps": round(fps, 5),
                "picture": [w, h], "film": os.path.basename(a.film),
                "shot_motion": shot.get("motion"), "shot_luma": shot.get("luma")}
        with open(os.path.join(d, "scene.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=1)
        print(f"{kind:18s} shot {shot['id']:4d} frames {first}-{first + n - 1} ({n}) t={t0:8.3f}s  {why}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
