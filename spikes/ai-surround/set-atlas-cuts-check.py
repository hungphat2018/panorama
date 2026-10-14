"""By-eye check of the set atlas at cuts (phase 3: shots that inherit a set's plate show no visible mismatch at cuts).
For up to --n shots that joined a set whose plate was painted, in a worker's disk cache, renders the 32:9 strip (the
picture in the middle, each side half its width, at half size) of three frames:
  1. the last frame of the shot before the cut, with its own set's canvas (the plate, else the T1 mosaic);
  2. the joined shot's first frame, with its set's plate seen through the shot's transform into the set's world;
  3. the joined shot's last frame, the same way (the plate must follow the camera through the shot).
The sides sample the canvas as the player does (texel centres, bilinear, over a dark ground where alpha is 0); the
picture is pasted on top and its edges marked. CPU: one short decode per frame.
Every joined shot also gets today's join check against its set's key shot (set_atlas.agreement on the two cached
mosaics, and worker_sets.edge_person on both): the cache may hold joins made under an older rule.

    python -I -X utf8 set-atlas-cuts-check.py CACHE_DIR FILM [--content DIR] [--cues FILE] [--n 10] [--out DIR]
        [--stats-only]
Writes OUT/<stamp>-set-cuts/cut-<k>-<first>.png and index.json (per cut: the shots, the set, its member transform, the
check); --stats-only prints the checks of all joined shots and writes no sheets.
"""
import argparse
import glob
import json
import os
import sys
import time

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from live import film_timeline, set_atlas, worker_sets  # noqa: E402
from live.analyse_v0 import ShotAnalysis  # noqa: E402
from live.frame_reader import FrameReader  # noqa: E402
from live.mosaic_t1 import Mosaic  # noqa: E402

DS = 2.0                                                          # source px per strip px (half size)
GROUND = 24                                                       # where a canvas has nothing (the glow's dark)


def load_analysis(cache: str, first: int, n: int):
    paths = sorted(glob.glob(os.path.join(cache, "analysis", f"{first}-{n}-*.npz")), key=os.path.getmtime)
    if not paths:
        return None
    with np.load(paths[-1]) as z:
        return ShotAnalysis.from_arrays(z)


def load_mosaic(cache: str, first: int, n: int):
    """The shot's newest cached mosaic (its real pixels in its own world), or None."""
    pngs = sorted(glob.glob(os.path.join(cache, "mosaic", f"{first}-{n}-*.png")), key=os.path.getmtime)
    if not pngs:
        return None
    with open(pngs[-1][:-4] + ".json", encoding="utf-8") as f:
        meta = json.load(f)
    rgba = cv2.cvtColor(cv2.imread(pngs[-1], cv2.IMREAD_UNCHANGED), cv2.COLOR_BGRA2RGBA)
    return Mosaic(world_x0=meta["world_x0"], world_y0=meta["world_y0"], scale=meta["scale"],
                  rgb=np.ascontiguousarray(rgba[..., :3]), coverage=rgba[..., 3] > 127, side_share=meta["side_share"])


def join_check(cache: str, st: dict, mb: dict, boxes) -> dict:
    """Today's join check of a member against its set's key shot (the cache holds no record of which member it matched):
    agreement of the two mosaics through the member's T (its world -> the key's), and characters cut by a side edge."""
    key, sp = tuple(st["key"]), tuple(mb["span"])
    out = {"edge_shot": None, "edge_key": None}
    an_k, an_s = load_analysis(cache, *key), load_analysis(cache, *sp)
    if an_k is not None and an_s is not None:
        out["edge_key"] = worker_sets.edge_person(boxes, key[0], key[1], an_k.crop)
        out["edge_shot"] = worker_sets.edge_person(boxes, sp[0], sp[1], an_s.crop)
    m_k, m_s = load_mosaic(cache, *key), load_mosaic(cache, *sp)
    out.update(set_atlas.agreement(m_k, m_s, mb["T"]) if m_k is not None and m_s is not None else {"cells": 0})
    meta, rgba = load_canvas(cache, key, 2, st["plate_rkey"]) if st.get("plate_rkey") else (None, None)
    if meta is not None and m_s is not None and an_s is not None:
        p = worker_sets.plate_band_agreement(rgba, meta, m_s, an_s.crop, np.asarray(mb["T"], np.float64))
        out["plate_differ"], out["plate_ok"] = p["differ"], p["ok"]
    out["join"] = (bool(out.get("ok")) and out.get("plate_ok", True) and not out["edge_key"] and not out["edge_shot"])
    return out


def check_text(c: dict) -> str:
    edge = [w for w, k in (("shot", "edge_shot"), ("key", "edge_key")) if c.get(k)]
    plate = f", plate bands {c['plate_differ']:.0%} differ" if "plate_differ" in c else ""
    return (f"check: {c.get('cells', 0)} cells, {c.get('differ', 1.0):.0%} differ (median {c.get('median')}){plate}"
            f"{', character at a side edge in the ' + ' and '.join(edge) if edge else ''}: "
            f"{'join' if c['join'] else 'NO JOIN'}")


def disagree_report(cache: str):
    """The mosaics' "disagree": per shot, the share of its multi-seen texels outside the first picture whose kept frames
    disagree (the mosaic leaves out the regions where they do). Its distribution over the cache's shots, for tuning."""
    vals = []
    for p in glob.glob(os.path.join(cache, "mosaic", "*.json")):
        with open(p, encoding="utf-8") as f:
            d = json.load(f).get("disagree")
        if d is not None:
            vals.append((float(d), os.path.basename(p).split("-")[0]))
    if not vals:
        print("no mosaic records a disagreement")
        return
    v = np.array([x for x, _ in vals])
    q = np.percentile(v, [10, 25, 50, 75, 90, 95])
    print(f"mosaic disagreement over {len(v)} shots: p10 {q[0]:.3f} p25 {q[1]:.3f} median {q[2]:.3f} p75 {q[3]:.3f} "
          f"p90 {q[4]:.3f} p95 {q[5]:.3f}; above 0.05: {(v > 0.05).mean():.0%}, 0.10: {(v > 0.10).mean():.0%}, "
          f"0.20: {(v > 0.20).mean():.0%}")
    for lo, hi in ((0.05, 0.10), (0.10, 0.15), (0.15, 0.30)):
        firsts = sorted(int(f) for x, f in vals if lo < x <= hi)[:8]
        print(f"  shots with {lo:.2f} < disagree <= {hi:.2f} (first frames): {firsts}")


def load_canvas(cache: str, key, tier: int, rkey: str = "*"):
    """(meta, RGBA) of the set's newest canvas of this tier, or (None, None)."""
    metas = sorted(glob.glob(os.path.join(cache, "canvas", f"{key[0]}-{key[1]}-t{tier}-{rkey}.json")),
                   key=os.path.getmtime)
    if not metas:
        return None, None
    with open(metas[-1], encoding="utf-8") as f:
        meta = json.load(f)
    img = cv2.imread(metas[-1][:-5] + ".png", cv2.IMREAD_UNCHANGED)
    return meta, cv2.cvtColor(img, cv2.COLOR_BGRA2RGBA)


def frame_at(film, index: int) -> np.ndarray:
    """One frame at the strip's scale (source / DS), RGB."""
    w, h = int(round(film.src_w / DS)), int(round(film.src_h / DS))
    reader = FrameReader(film, index, size=(w, h), threads=2)
    try:
        for i, rgb in reader:
            if i >= index:
                return rgb
    finally:
        reader.close()
    raise SystemExit(f"frame {index} not decoded")


def strip(rgb: np.ndarray, an: ShotAnalysis, rel: int, T, meta: dict, canvas: np.ndarray) -> np.ndarray:
    """The 32:9 strip of the shot's frame `rel`: the canvas through member world -> set world (T) -> texels around the
    picture (the crop), the decoded picture pasted in the middle, its edges marked."""
    x0, y0, x1, y1 = an.crop
    pw, ph = (x1 - x0) / DS, (y1 - y0) / DS
    out_w, out_h = int(round(2 * pw)), int(round(ph))
    side = pw / 2
    # strip px centre (u + 0.5) -> source px (x0 + (u + 0.5 - side) DS) -> member world (the frame's map) -> set world
    # (T) -> texel centre (w - world_x0) / scale - 0.5
    to_src = np.float64([[DS, 0, x0 + (0.5 - side) * DS], [0, DS, y0 + 0.5 * DS], [0, 0, 1]])
    s = float(meta["scale"])
    to_tex = np.float64([[1 / s, 0, -float(meta["world_x0"]) / s - 0.5], [0, 1 / s, -float(meta["world_y0"]) / s - 0.5],
                         [0, 0, 1]])
    T3 = np.vstack([np.asarray(T, np.float64), [0.0, 0.0, 1.0]])
    m = to_tex @ T3 @ an.maps()[rel] @ to_src
    rgba = cv2.warpPerspective(canvas, m, (out_w, out_h), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                               borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
    a = rgba[..., 3:4].astype(np.float32) / 255.0
    out = np.round(rgba[..., :3].astype(np.float32) * a + GROUND * (1.0 - a)).astype(np.uint8)
    px0, py0 = int(round(x0 / DS)), int(round(y0 / DS))
    pic = rgb[py0:py0 + out_h, px0:px0 + int(round(pw))]
    sx = int(round(side))
    out[:pic.shape[0], sx:sx + pic.shape[1]] = pic
    for x in (sx - 1, sx + pic.shape[1]):
        out[::6, x] = (255, 255, 0)                               # a dotted seam mark, every 6th row
    return out


def labelled(img: np.ndarray, text: str) -> np.ndarray:
    bar = np.zeros((26, img.shape[1], 3), np.uint8)
    cv2.putText(bar, text, (8, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
    return np.vstack([bar, img])


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cache", help="the film's folder in the worker's disk cache (holds sets.json)")
    ap.add_argument("film")
    ap.add_argument("--content", default=os.path.join(HERE, "out", "jjk", "content"))
    ap.add_argument("--cues", default=os.path.join(HERE, "out", "live", "jjk-cues.json"))
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--out", default=os.path.join(HERE, "out", "live", "set-cuts"))
    ap.add_argument("--stats-only", action="store_true", help="print every joined shot's check; no sheets")
    a = ap.parse_args(argv)
    with open(os.path.join(a.cache, "sets.json"), encoding="utf-8") as f:
        sets = json.load(f)["sets"]
    span_set = {}                                                 # (first, n) -> (set, member)
    for st in sets:
        for mb in st["members"]:
            span_set[tuple(mb["span"])] = (st, mb)
    spans = sorted(span_set)
    joined = []
    for k, sp in enumerate(spans):
        st, mb = span_set[sp]
        if sp == tuple(st["key"]) or not st.get("plate_rkey") or k == 0:
            continue
        joined.append((spans[k - 1], sp))
    print(f"{len(sets)} sets, {len(spans)} shots, {len(joined)} joined shots with a painted plate")
    film = film_timeline.open_film(a.film, a.content, a.cues)
    boxes = worker_sets.person_boxes_of(film)
    checks = {}
    for sp in spans:
        st, mb = span_set[sp]
        if sp != tuple(st["key"]):
            checks[sp] = join_check(a.cache, st, mb, boxes)
            print(f"  shot {sp} in set {tuple(st['key'])}: {check_text(checks[sp])}")
    if checks:
        n_join = sum(c["join"] for c in checks.values())
        print(f"today's check keeps {n_join} of {len(checks)} joins")
    disagree_report(a.cache)
    if a.stats_only:
        return 0
    run = os.path.join(a.out, f"{time.strftime('%Y%m%d-%H%M%S')}-set-cuts")
    os.makedirs(run, exist_ok=True)
    pick = joined if len(joined) <= a.n else [joined[int(round(i * (len(joined) - 1) / (a.n - 1)))] for i in range(a.n)]
    index = []
    for k, (prev, sp) in enumerate(pick, 1):
        st, mb = span_set[sp]
        pst, pmb = span_set[prev]
        rows = []
        # 1: the shot before the cut, last frame, its own set's canvas
        an_p = load_analysis(a.cache, *prev)
        meta_p, can_p = load_canvas(a.cache, pst["key"], 2, pst["plate_rkey"]) if pst.get("plate_rkey") else (None, None)
        tier_p = 2
        if meta_p is None:
            meta_p, can_p = load_canvas(a.cache, pst["key"], 1)
            tier_p = 1
        if an_p is not None and meta_p is not None:
            rgb = frame_at(film, prev[0] + prev[1] - 1)
            rows.append(labelled(strip(rgb, an_p, prev[1] - 1, pmb["T"], meta_p, can_p),
                                 f"before the cut: shot {prev[0]}+{prev[1]} last frame, its set {tuple(pst['key'])} "
                                 f"T{tier_p}"))
        # 2-3: the joined shot, first and last frame, the set's plate
        an = load_analysis(a.cache, *sp)
        meta, can = load_canvas(a.cache, st["key"], 2, st["plate_rkey"])
        if an is None or meta is None:
            print(f"cut {k}: shot {sp} has no analysis or plate on disk, skipped")
            continue
        T = np.asarray(mb["T"], np.float64)
        scale = float(np.hypot(T[0, 0], T[1, 0]))
        for rel, what in ((0, "first"), (sp[1] - 1, "last")):
            rgb = frame_at(film, sp[0] + rel)
            rows.append(labelled(strip(rgb, an, rel, mb["T"], meta, can),
                                 f"joined shot {sp[0]}+{sp[1]} {what} frame: set {tuple(st['key'])}'s plate (painted "
                                 f"from shot {st['key'][0]}), member scale {scale:.3f}"))
        rows.append(labelled(np.zeros((0, rows[-1].shape[1], 3), np.uint8), check_text(checks[sp])))
        w = max(r.shape[1] for r in rows)
        sheet = np.vstack([np.pad(r, ((0, 4), (0, w - r.shape[1]), (0, 0))) for r in rows])
        path = os.path.join(run, f"cut-{k:02d}-{sp[0]}.png")
        cv2.imwrite(path, cv2.cvtColor(sheet, cv2.COLOR_RGB2BGR))
        index.append({"cut": k, "before": prev, "shot": sp, "set": st["key"], "T": mb["T"], "scale": round(scale, 4),
                      "members": len(st["members"]), "sheet": os.path.basename(path), "check": checks[sp]})
        print(f"cut {k}: {prev} -> {sp} (set {st['key']}, {len(st['members'])} members, scale {scale:.3f}) {path}",
              flush=True)
    with open(os.path.join(run, "index.json"), "w", encoding="utf-8") as f:
        json.dump({"cache": a.cache, "film": a.film, "joined": len(joined), "cuts": index}, f, indent=1)
    print(f"done: {run}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
