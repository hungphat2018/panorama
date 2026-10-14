"""The per-frame side gate on clips (phase 4 of plans/261013-0718-ai-surround-live-path; measures in live/side_gate.py).

Reads every version back to the gate's scale (X1c's 1920x540 strip, grey, area-scaled) from the two-screen clips
(4480x1600, make-wide-demo's 'fill' layout), wide 3840x1080 frames or strips, measures every frame and side, and judges
each measure against its bar in live/side-gate.json (measures without a bar are reported, never judged). Before any
test reaches the user: every version passes on every frame, and no two trial clips are the same file.

    python -I side-gate.py x1c TEST_DIR [--moments pan,slow-side] [--versions planner-v2,near-every] [--ref truth-pipe]
    python -I side-gate.py clip VERSION.mp4 [--truth TRUTH.mp4] [--name NAME]
    python -I side-gate.py md5 TEST_DIR            identical clips and trials whose two clips are the same file
    python -I side-gate.py normal TEST_DIR [--metrics X1C_METRICS.json --film FILM]
                                                   the truth-free measures' normal ranges on truth-pipe

Writes OUT (default TEST_DIR/gate-<stamp>, or next to the clip): gate.json (per version and moment: each measure's worst
frame per side, failing frames per bar, clip md5s), <moment>-<version>.csv (every frame and side), and for the worst
frames crops (truth above version, the seam marked) and x-t slices across each seam (time runs down).
"""
import argparse
import collections
import csv
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)                                          # python -I leaves the script's folder off sys.path
import hidden_sides as hs  # noqa: E402
from live import side_gate as sg  # noqa: E402


def load_script(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), os.path.join(HERE, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


demo = load_script("make-wide-demo")                              # SCREENS: the two-screen layout of every clip


def span_filter():
    """ffmpeg filter that maps a two-screen clip back to the strip: each screen's 16:9 half (placed as compose('fill')
    places it) area-scaled to 960x540, side by side."""
    parts = []
    for i, sc in enumerate(demo.SCREENS):
        w = sc["w"]
        h = int(round(w * 9 / 16))
        if h > sc["h"]:
            h = sc["h"]
            w = int(round(h * 16 / 9))
        x = sc["x"] + (sc["w"] - w if sc["x"] == 0 else 0)
        y = (sc["h"] - h) // 2
        parts.append(f"[0:v]crop={w}:{h}:{x}:{y},scale={sg.STRIP_W // 2}:{sg.STRIP_H}:flags=area[h{i}]")
    return ";".join(parts) + ";[h0][h1]hstack=inputs=2"


def ffprobe():
    """ffprobe: on PATH, next to ffmpeg, or in WinGet's links folder (some shells here lack it on PATH)."""
    found = shutil.which("ffprobe")
    if found:
        return found
    for folder in (os.path.dirname(shutil.which("ffmpeg") or ""),
                   os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Links")):
        for name in ("ffprobe.exe", "ffprobe"):
            if folder and os.path.isfile(os.path.join(folder, name)):
                return os.path.join(folder, name)
    raise SystemExit("ffprobe not found (PATH, next to ffmpeg, or %LOCALAPPDATA%\\Microsoft\\WinGet\\Links)")


def probe_size(path):
    out = subprocess.run([ffprobe(), "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                          "-of", "csv=p=0", path], capture_output=True, text=True, check=True).stdout
    w, h = (int(x) for x in out.strip().split(",")[:2])
    return w, h


def _filter(path):
    w, h = probe_size(path)
    desk_w = demo.SCREENS[1]["x"] + demo.SCREENS[1]["w"]
    if (w, h) == (desk_w, max(s["h"] for s in demo.SCREENS)):
        return ["-filter_complex", span_filter()]
    if (w, h) == (sg.STRIP_W, sg.STRIP_H):
        return []
    if abs(w / h - sg.STRIP_W / sg.STRIP_H) < 0.01:              # wide 3840x1080 or any 32:9 size
        return ["-vf", f"scale={sg.STRIP_W}:{sg.STRIP_H}:flags=area"]
    raise SystemExit(f"{path}: {w}x{h} is neither a two-screen clip nor a 32:9 strip")


_DECODED: collections.OrderedDict = collections.OrderedDict()     # the last few whole-clip decodes (a reference is
                                                                  # read once per moment, not once per version)


def decode(path, pix_fmt="gray", frames=None):
    """The clip at the gate's scale: (N, 540, 1920) for gray, (N, 540, 1920, 3) for rgb24, (N, 3, 540, 1920) for
    yuv444p. frames: only these frame indices (decoded in order, the rest dropped)."""
    if frames is None:
        st = os.stat(path)
        key = (os.path.abspath(path), st.st_mtime_ns, st.st_size, pix_fmt)
        if key not in _DECODED:
            _DECODED[key] = _decode(path, pix_fmt, None)
            while len(_DECODED) > 4:
                _DECODED.popitem(last=False)
        return _DECODED[key]
    return _decode(path, pix_fmt, frames)


def _decode(path, pix_fmt, frames):
    vf = _filter(path)
    if frames is not None:
        sel = "select='" + "+".join(f"eq(n\\,{t})" for t in sorted(frames)) + "'"
        if not vf:
            vf = ["-vf", sel]
        elif vf[0] == "-vf":
            vf = ["-vf", sel + "," + vf[1]]
        else:
            vf = ["-filter_complex", _select_span(sel)]
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", path, *vf, "-fps_mode", "passthrough", "-f", "rawvideo",
           "-pix_fmt", pix_fmt, "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    if pix_fmt == "gray":
        return np.frombuffer(raw, np.uint8).reshape(-1, sg.STRIP_H, sg.STRIP_W)
    if pix_fmt == "rgb24":
        return np.frombuffer(raw, np.uint8).reshape(-1, sg.STRIP_H, sg.STRIP_W, 3)
    return np.frombuffer(raw, np.uint8).reshape(-1, 3, sg.STRIP_H, sg.STRIP_W)


def _select_span(sel):
    """span_filter() with a frame selection first (split, since both halves read the selected frames)."""
    f = span_filter().replace("[0:v]crop", "[c]crop")
    first, rest = f.split(";", 1)
    return f"[0:v]{sel},split=2[a][b];" + first.replace("[c]", "[a]") + ";" + rest.replace("[c]", "[b]", 1)


def chroma_of(path):
    """(N, 540, 1920, 2): U and V at the gate's scale."""
    y = decode(path, "yuv444p")
    return np.ascontiguousarray(np.moveaxis(y[:, 1:], 1, -1))


def md5(path, chunk=1 << 20):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(chunk), b""):
            h.update(b)
    return h.hexdigest()


# ---- pictures of the worst frames -------------------------------------------------------------------------------------

CROP_SIDE, CROP_PIC = 192, 128                                    # px of the side and of the picture beside a seam


def _label(img, text):
    cv2.putText(img, text, (6, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 4, cv2.LINE_AA)
    cv2.putText(img, text, (6, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 0), 1, cv2.LINE_AA)


def hjoin(imgs, gap, value=255):
    """RGB images side by side with `gap` px of `value` between them."""
    sep = np.full((imgs[0].shape[0], gap, 3), value, np.uint8)
    out = [imgs[0]]
    for img in imgs[1:]:
        out += [sep, img]
    return np.hstack(out)


def _seam_cols(side):
    if side.out < 0:
        return slice(side.seam - CROP_SIDE, side.seam + CROP_PIC), CROP_SIDE
    return slice(side.seam - CROP_PIC, side.seam + CROP_SIDE), CROP_PIC


def crops(ver_path, truth_path, side, picks, out_png, title):
    """Truth above version at frames `picks`, the columns around the side's seam (marked red), in colour."""
    cols, at = _seam_cols(side)
    ver = decode(ver_path, "rgb24", picks)
    ref = decode(truth_path, "rgb24", picks) if truth_path else None
    tiles = []
    for k, t in enumerate(sorted(picks)):
        pair = []
        for name, clip in (("truth", ref), (title, ver)):
            if clip is None:
                continue
            c = np.ascontiguousarray(clip[k][:, cols])
            cv2.line(c, (at, 0), (at, 14), (255, 0, 0), 2)
            cv2.line(c, (at, c.shape[0] - 15), (at, c.shape[0] - 1), (255, 0, 0), 2)
            _label(c, f"{name} f{t}")
            pair.append(c)
        tiles.append(np.vstack(pair))
    cv2.imwrite(out_png, cv2.cvtColor(hjoin(tiles, 6), cv2.COLOR_RGB2BGR))


def xt_slice(ver, truth, side, out_png, title, reach=96, rep=4, x=None):
    """x-t slices across the seam (or across column x, e.g. a source-change line inside the side) at the 3 rows with
    the most horizontal detail (truth's, or the version's): one row per frame, repeated `rep` times, time running down;
    truth left of version, the column marked."""
    src = truth if truth is not None else ver
    x = side.seam if x is None else x
    cols = slice(x - reach, x + reach)
    gx = np.abs(np.diff(np.asarray(src[:, :, cols], np.float32), axis=2)).mean(axis=(0, 2))
    gx = np.convolve(gx, np.ones(9) / 9, mode="same")
    rows = []
    for y in np.argsort(gx)[::-1]:
        if all(abs(int(y) - r) > 40 for r in rows):
            rows.append(int(y))
        if len(rows) == 3:
            break
    panels = []
    for y in sorted(rows):
        pair = []
        for name, clip in (("truth", truth), (title, ver)):
            if clip is None:
                continue
            img = np.repeat(np.asarray(clip[:, y, cols], np.uint8), rep, axis=0)
            img = cv2.cvtColor(cv2.resize(img, (img.shape[1] * 2, img.shape[0]), interpolation=cv2.INTER_NEAREST),
                               cv2.COLOR_GRAY2RGB)
            img[:, 2 * reach - 1:2 * reach + 1] = (255, 0, 0)
            band = np.zeros((28, img.shape[1], 3), np.uint8)
            _label(band, f"{name} row {y}")
            pair.append(np.vstack([band, img]))
        panels.append(hjoin(pair, 4))
    cv2.imwrite(out_png, cv2.cvtColor(hjoin(panels, 12, 128), cv2.COLOR_RGB2BGR))


# ---- scoring ----------------------------------------------------------------------------------------------------------

def worst_frames(values, bars, side_i, keys, n_bar=3):
    """The frames to show: up to n_bar frames furthest past (or nearest to) a bar, plus each key measure's worst
    frame (a measure without a bar is still shown at its worst)."""
    n = next(iter(values.values())).shape[0]
    b = np.zeros(n)
    for m, bar in bars.items():
        if m not in values:
            continue
        v = values[m][:, side_i]
        with np.errstate(invalid="ignore", divide="ignore"):
            r = v / bar["max"] if "max" in bar else bar["min"] / np.maximum(v, 1e-6)
        b = np.fmax(b, np.nan_to_num(r, nan=0.0))
    picks = [int(t) for t in np.argsort(b)[::-1][:n_bar] if b[t] > 0.5]
    for m in keys:
        if m not in values or np.all(np.isnan(values[m][:, side_i])):
            continue
        col = values[m][:, side_i]
        picks.append(int(np.nanargmin(col) if m in sg.LOW_IS_BAD else np.nanargmax(col)))
    return sorted(set(picks))


def measure_clip(ver_path, truth_path=None, colour=True, lines=None):
    """(values, version frames, truth frames) of one clip at the gate's scale."""
    ver = decode(ver_path)
    ref = decode(truth_path) if truth_path else None
    if ref is not None and len(ref) != len(ver):
        n = min(len(ref), len(ver))
        print(f"  {os.path.basename(ver_path)}: {len(ver)} frames against the truth's {len(ref)}; judged on the first {n}")
        ver, ref = ver[:n], ref[:n]
    uv = chroma_of(ver_path)[:len(ver)] if colour else None
    return sg.measure(ver, ref, uv, lines), ver, ref


# The bars a verdict needs: without one of them the gate cannot say "pass" (a missing bar is not a passed one)
REQUIRED = {"carried": ("seam", "line", "jump", "drift", "detail_loss", "boil", "motion_vs_truth", "drift_vs_truth"),
            "generated": ("jump", "drift", "detail_loss", "boil", "motion_vs_truth", "drift_vs_truth"),
            "free": ("seam_step", "seam_colour", "detail")}


def score_clip(ver_path, truth_path, name, odir, bars, colour=True, pictures=True, lines=None, label="version",
               generated=False):
    """Measures, verdicts and pictures of one version (against a truth when given): (its gate.json entry, values)."""
    t0 = time.perf_counter()
    values, ver, ref = measure_clip(ver_path, truth_path, colour, lines)
    fl = sg.fails(values, bars)
    entry = {"clip": os.path.relpath(ver_path, odir), "md5": md5(ver_path), "frames": len(ver),
             "worst": sg.worst(values), "bars": {m: bars[m] for m in fl}, "failing_frames": {}, "pictures": []}
    for i, s in enumerate(sg.SIDES):
        bad = sorted({int(t) for m in fl for t in np.flatnonzero(fl[m][:, i])})
        entry["failing_frames"][s.name] = {"all": bad, **{m: [int(t) for t in np.flatnonzero(fl[m][:, i])] for m in fl
                                                          if fl[m][:, i].any()}}
    mode = "free" if ref is None else "generated" if generated else "carried"
    missing = [m for m in REQUIRED[mode] if m not in bars or (m == "seam_colour" and not colour)]
    failed = any(f["all"] for f in entry["failing_frames"].values())
    entry["verdict"] = "fail" if failed else f"incomplete: no bar for {', '.join(missing)}" if missing else "pass"
    with open(os.path.join(odir, f"{name}.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        keys = list(values)
        w.writerow(["frame", "side", *keys, "fails"])
        for t in range(len(ver)):
            for i, s in enumerate(sg.SIDES):
                w.writerow([t, s.name, *("" if np.isnan(values[k][t, i]) else f"{values[k][t, i]:.3f}" for k in keys),
                            "+".join(m for m in fl if fl[m][t, i])])
    if pictures:
        keys = ("seam", "line", "jump", "drift") if ref is not None else ("seam_step", "seam_motion", "pop")
        for i, s in enumerate(sg.SIDES):
            picks = worst_frames(values, bars, i, keys)
            if not picks:
                continue
            png = os.path.join(odir, f"{name}-{s.name}-worst.png")
            crops(ver_path, truth_path, s, picks, png, label)
            xt = os.path.join(odir, f"{name}-{s.name}-xt.png")
            xt_slice(ver, ref, s, xt, label)
            entry["pictures"] += [os.path.basename(png), os.path.basename(xt)]
            if "line" in fl and fl["line"][:, i].any():                # a tear inside the side: across that line too
                xl = os.path.join(odir, f"{name}-{s.name}-line-xt.png")
                xt_slice(ver, ref, s, xl, label, x=(lines or {}).get(s.name, [s.mid])[0])
                entry["pictures"].append(os.path.basename(xl))
            entry.setdefault("worst_frames", {})[s.name] = sorted(picks)
    entry["seconds"] = round(time.perf_counter() - t0, 1)
    return entry, values


def table(results, measures):
    """One line per version and moment: each measure's worst value per side as left/right (frame)."""
    lines = []
    for key, e in results.items():
        cells = []
        for m in measures:
            w = e["worst"].get(m)
            if not w:
                continue
            l, r = w["left"], w["right"]
            fmt = lambda x: "-" if x[0] is None else f"{x[0]:.1f}@{x[1]}"   # noqa: E731
            cells.append(f"{m} {fmt(l)}/{fmt(r)}")
        n_fail = {s: len(f["all"]) for s, f in e["failing_frames"].items()}
        lines.append(f"{key:28s} {e['verdict']:11s} fails L{n_fail.get('left', 0)} R{n_fail.get('right', 0)} | "
                     + "  ".join(cells))
    return "\n".join(lines)


def identical(paths):
    """Groups of distinct files with the same bytes (hard links of one file, as free-look/ and clips/ are, count once)."""
    by, seen = collections.defaultdict(list), set()
    for p in sorted(paths):
        st = os.stat(p)
        if (st.st_dev, st.st_ino) in seen and st.st_ino:
            continue
        seen.add((st.st_dev, st.st_ino))
        by[md5(p)].append(p)
    return [sorted(g) for g in by.values() if len(g) > 1]


def x1c(a):
    test = os.path.abspath(a.test_dir)
    odir = a.out or os.path.join(test, f"gate-{time.strftime('%Y%m%d-%H%M%S')}")
    os.makedirs(odir, exist_ok=True)
    bars = sg.load_bars(a.bars, generated=a.generated)
    fl_dir = os.path.join(test, "free-look")
    moments = a.moments.split(",") if a.moments else sorted(os.listdir(fl_dir))
    results = {}
    for m in moments:
        ref = os.path.join(fl_dir, m, f"{a.ref}.mp4")
        names = a.versions.split(",") if a.versions else sorted(
            os.path.splitext(f)[0] for f in os.listdir(os.path.join(fl_dir, m)) if f.endswith(".mp4"))
        for v in names:
            path = os.path.join(fl_dir, m, f"{v}.mp4")
            if not os.path.exists(path):
                continue
            key = f"{m}-{v}"
            truth = None if v == a.ref else ref
            results[key], _ = score_clip(path, truth, key, odir, bars, colour=not a.no_colour,
                                         pictures=not a.no_pictures, label=v, generated=a.generated)
            print(f"  {key}: {results[key]['verdict']} ({results[key]['seconds']} s)", flush=True)
    clips = [os.path.join(fl_dir, m, f) for m in moments for f in os.listdir(os.path.join(fl_dir, m)) if f.endswith(".mp4")]
    same = identical(clips)
    report = {"test": test, "reference": a.ref, "bars": bars, "bars_file": a.bars, "identical_clips": same,
              "versions": results}
    hs.save_json(report, os.path.join(odir, "gate.json"))
    measures = sg.TRUTH_MEASURES[:4] + ("jump", "drift") + sg.FREE_MEASURES
    print(table(results, measures))
    for g in same:
        print("IDENTICAL:", ", ".join(os.path.relpath(p, test) for p in g))
    print(f"wrote {odir}")


def clip(a):
    odir = a.out or os.path.join(os.path.dirname(os.path.abspath(a.clip)), f"gate-{time.strftime('%Y%m%d-%H%M%S')}")
    os.makedirs(odir, exist_ok=True)
    bars = sg.load_bars(a.bars, generated=a.generated)
    name = a.name or os.path.splitext(os.path.basename(a.clip))[0]
    e, _ = score_clip(a.clip, a.truth, name, odir, bars, colour=not a.no_colour, pictures=not a.no_pictures,
                      generated=a.generated)
    hs.save_json({"bars": bars, "versions": {name: e}}, os.path.join(odir, "gate.json"))
    print(table({name: e}, sg.TRUTH_MEASURES + sg.FREE_MEASURES))
    print(f"wrote {odir}")


def md5_check(a):
    """Identical clips in a blind test, and trials whose two clips are the same file (key.json)."""
    test = os.path.abspath(a.test_dir)
    clips = []
    for root, _, files in os.walk(test):
        clips += [os.path.join(root, f) for f in files if f.endswith(".mp4") and not f.startswith("label-")]
    groups = identical(clips)
    for g in groups:
        print("IDENTICAL:", ", ".join(os.path.relpath(p, test) for p in g))
    key = os.path.join(test, "key.json")
    if os.path.exists(key):
        k = json.load(open(key, encoding="utf-8"))
        ids = k.get("clip_ids", {})
        sums = {cid: md5(os.path.join(test, "clips", f"{cid}.mp4")) for cid in set(ids.values())
                if os.path.exists(os.path.join(test, "clips", f"{cid}.mp4"))}
        pair_sums = [[sums.get(ids.get(f"{t['moment']}/{v}")) for v in t["pair"]] for t in k["trials"]]
        bad = [t for t, (s0, s1) in zip(k["trials"], pair_sums) if t.get("odd") is not None and s0 is not None
               and s0 == s1]
        for t in bad:
            print(f"trial {t['trial']} ({t['moment']}, {t['level']}): both clips are the same file")
        print(f"{len(bad)} of {len(k['trials'])} trials compare a file with itself")
    if not groups:
        print("no identical clips")


def normal(a):
    """Normal ranges of the truth-free measures on truth-pipe, the same way the gate reads test clips: X1c's moments from
    their clips, more moments (an x1c-metrics JSON) rebuilt from the film as clips. Per measure over every frame and
    side: the spread (percentiles) and where its extreme sits."""
    ts = load_script("true-sides-test")
    from live import structure
    odir = a.out or os.path.join(os.path.abspath(a.test_dir), "gate-normal")
    os.makedirs(os.path.join(odir, "clips"), exist_ok=True)
    fl_dir = os.path.join(os.path.abspath(a.test_dir), "free-look")
    moments = [(m, os.path.join(fl_dir, m, "truth-pipe.mp4")) for m in sorted(os.listdir(fl_dir))]
    extra = json.load(open(a.metrics, encoding="utf-8"))["moments"] if a.metrics else {}
    for name, info in sorted(extra.items(), key=lambda kv: kv[1]["start_s"]):
        path = os.path.join(odir, "clips", f"{name}-truth-pipe.mp4")
        yield_to(a.yield_to)
        if not os.path.exists(path):
            t0 = time.perf_counter()
            strips = [f[ts.ROW0:ts.ROW0 + ts.ROWS] for f in ts.demo.grab(a.film, info["start_s"], ts.FRAMES)]
            W = ts.projective_path(strips)[0]
            n = len(strips)
            tp = structure.carry_strips(strips, W, [{r: True for r in ts.REGIONS}] * n, [{} for _ in range(n)],
                                        ts.PIC_SLICE, ts.REGIONS)
            ts.write_span_clip((ts.to_wide(s) for s in tp), path, os.path.join(odir, "ffmpeg.log"))
            print(f"  {name}: truth-pipe clip {time.perf_counter() - t0:.0f} s", flush=True)
        moments.append((name, path))
    per = {}
    for name, path in moments:
        yield_to(a.yield_to)
        values, _, _ = measure_clip(path, None, colour=not a.no_colour)
        per[name] = {m: values[m] for m in sg.FREE_MEASURES}
        print(f"  {name}: measured", flush=True)
    summary = {}
    for m in sg.FREE_MEASURES:
        cells = [(v[m][t, i], name, t, sg.SIDES[i].name) for name, v in per.items()
                 for t in range(v[m].shape[0]) for i in range(v[m].shape[1]) if not np.isnan(v[m][t, i])]
        if not cells:
            continue
        xs = np.float64([c[0] for c in cells])
        ext = min(cells) if m in sg.LOW_IS_BAD else max(cells)
        summary[m] = {"cells": len(xs), **{f"p{q:g}": round(float(np.percentile(xs, q)), 3)
                                           for q in (0.1, 1, 5, 50, 95, 99, 99.9)},
                      "min": round(float(xs.min()), 3), "max": round(float(xs.max()), 3),
                      "extreme_at": f"{ext[1]} f{ext[2]} {ext[3]}"}
        per_moment = {name: round(float(np.nanmin(v[m]) if m in sg.LOW_IS_BAD else np.nanmax(v[m])), 3)
                      for name, v in per.items() if not np.all(np.isnan(v[m]))}
        summary[m]["worst_per_moment"] = per_moment
    hs.save_json({"moments": [n for n, _ in moments], "normal": summary}, os.path.join(odir, "normal.json"))
    for m, s in summary.items():
        print(f"{m:12s} p1 {s['p1']:7.3f}  p50 {s['p50']:7.3f}  p99 {s['p99']:7.3f}  min {s['min']:7.3f}  "
              f"max {s['max']:7.3f}  extreme {s['extreme_at']}")
    print(f"wrote {odir}")


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("x1c", help="every version of an X1c-style test against its reference")
    p.add_argument("test_dir")
    p.add_argument("--moments")
    p.add_argument("--versions")
    p.add_argument("--ref", default="truth-pipe")
    q = sub.add_parser("clip", help="one clip, with or without a truth")
    q.add_argument("clip")
    q.add_argument("--truth")
    q.add_argument("--name")
    r = sub.add_parser("md5", help="identical clips and self-compared trials")
    r.add_argument("test_dir")
    nr = sub.add_parser("normal", help="normal ranges of the truth-free measures on truth-pipe")
    nr.add_argument("test_dir", help="an X1c test folder (its free-look truth-pipe clips)")
    nr.add_argument("--metrics", help="an x1c-metrics JSON: more moments, rebuilt from the film")
    nr.add_argument("--film")
    nr.add_argument("--yield-to", help="wait before each moment while a process of this image name runs (mpv.exe)")
    for s in (p, q, nr):
        s.add_argument("--out")
        s.add_argument("--bars", default=sg.BARS_PATH)
        s.add_argument("--generated", action="store_true", help="generated sides: skip the bars that ask for the truth's own pixels")
        s.add_argument("--no-colour", action="store_true", help="skip the chroma decode (seam_colour stays empty)")
        s.add_argument("--no-pictures", action="store_true")
    a = ap.parse_args(argv)
    cv2.setNumThreads(4)
    below_normal_priority()
    {"x1c": x1c, "clip": clip, "md5": md5_check, "normal": normal}[a.cmd](a)
    return 0


def yield_to(image):
    """Wait while a process of this image name runs (e.g. mpv.exe during the user's ladder: its playback must not
    stutter)."""
    if not image:
        return
    said = False
    while image.lower() in subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {image}"], capture_output=True,
                                          text=True).stdout.lower():
        if not said:
            print(f"  waiting while {image} runs", flush=True)
            said = True
        time.sleep(5)


def below_normal_priority():
    """CPU work at below-normal priority (Windows); ffmpeg children inherit it."""
    try:
        import ctypes
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)
    except (AttributeError, OSError):
        pass


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
