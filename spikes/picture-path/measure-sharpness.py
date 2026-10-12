"""Sharpness, ringing and aliasing of the picture pass on T7, one monitor with the picture fitted to it (the "plain single
monitor" case): the laptop upscales 1.333x, the external downscales 0.741x. Runs the harness for each (structure, kernel) and
measures the slanted-edge MTF (cycles per output pixel), the overshoot of the edge, the zone plate's response in bands of source
radius (the output's Nyquist frequency is reached at r = 190 on the external; beyond it anything left is aliasing) and the
contrast of 1 px line pairs.

  python -I measure-sharpness.py [--backend d3d11] [--adapter nvidia] [--json out/sharp/results.json]
"""
import argparse
import json
import math
import os
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pp_geometry as geo  # noqa: E402
import pp_reference as ref  # noqa: E402
import pp_sharpness as sh  # noqa: E402
from pp_image import linear_to_code, read_canvas  # noqa: E402

EXE = os.path.join(HERE, "bin", "picture-path.exe")
ENV = dict(os.environ, PATH=r"C:\Users\tamla\msys64\home\tamla\p0\prefix\libplacebo-master\bin;C:\Users\tamla\msys64\ucrt64\bin;"
           + os.environ.get("PATH", ""))
BANDS = [(40, 80), (80, 120), (120, 160), (160, 190), (190, 220), (220, 252)]
LINES = [(2, 90), (3, 140), (4, 190)]            # (period px, top row of the band) in the 1080p source


def inverse_fold(setup, k, sx, sy):
    """Source position -> continuous monitor pixel: a first guess from the fold's affine map, then Newton steps on the real map
    (a few steps move it onto the cylinder when the scenario is a curve)."""
    pic, room = setup.picture, setup.room
    m = room.monitors[k]
    u = pic.u_left + (np.asarray(sx) - pic.crop[0]) / pic.cw * pic.width
    v = pic.v_top - (np.asarray(sy) - pic.crop[1]) / pic.ch * pic.height
    px = float((u - (room.u0[k] - room.u_anchor)) / m.w_mm * m.W)
    py = float(geo.local_y_to_row(v - m.centre[1], m.H, m.h_mm))
    if setup.model == "fold":
        return px, py
    for _ in range(6):
        sx0, sy0, j = geo.source_jacobian(room, k, pic, np.array([px]), np.array([py]), setup.model, setup.curvature)
        step = np.linalg.solve(j[0], np.array([sx - sx0[0], sy - sy0[0]]))
        px, py = px + step[0], py + step[1]
    return px, py


def measure(canvas, setup, k):
    room, pic = setup.room, setup.picture
    m = room.monitors[k]
    lum = sh._lum(canvas)
    out = {}
    # the two slanted edges (left edge of each dark square); the source geometry of make-patterns.py at 1080p
    edges = []
    for name, cx in (("edge-left", 480.0), ("edge-right", 1440.0)):
        ex = cx - 150.0 / math.cos(math.radians(5.0))
        px, py = inverse_fold(setup, k, ex, 540.0)
        px, py = float(px), float(py)
        if not (20 < px < m.W - 20 and 100 < py < m.H - 100):
            continue
        x0, x1 = int(px - 14), int(px + 14)
        y0, y1 = int(py - 90), int(py + 90)
        res = sh.slanted_edge_mtf(lum, x0, x1, y0, y1)
        res["name"] = name
        edges.append(res)
    if edges:
        out["edge"] = {key: float(np.mean([e[key] for e in edges])) for key in
                       ("mtf50", "mtf30", "mtf10", "mtf_at_0.25", "mtf_at_0.4", "overshoot_pct", "undershoot_pct",
                        "overshoot_code_pct", "undershoot_code_pct")}
        out["edge"]["n"] = len(edges)
    # zone plate: source position of every output pixel
    px, py = geo.monitor_grid(m)
    sx, sy = geo.map_to_source(room, k, pic, px, py, setup.model, setup.curvature)
    out["zone"] = sh.zone_response(canvas, sx, sy, (960.0, 540.0), BANDS)
    # line pairs: contrast in each band, from the 5th and 95th percentile of the output code values over the band's pixels
    lp = {}
    for period, y_top in LINES:
        sel = (sx >= 850) & (sx < 1070) & (sy >= y_top + 4) & (sy < y_top + 36) & (canvas[..., 3] > 0.999)
        if sel.sum() < 50:
            continue
        v = linear_to_code(np.clip(canvas[..., 1][sel], 0, None))
        lp["period%d" % period] = float((np.percentile(v, 95) - np.percentile(v, 5)) / 195.0)
    out["linepairs_contrast"] = lp
    return out


def run_harness(scn, structure, kernel, backend, adapter, tag, kernel2=None):
    out = os.path.join("out", "sharp", tag)
    cmd = [EXE, "render", "--scenario", os.path.join("scenarios", scn + ".json"), "--structure", structure, "--backend", backend,
           "--adapter", adapter, "--kernel", kernel, "--out", out]
    if kernel2:
        cmd += ["--kernel2", kernel2]
    p = subprocess.run(cmd, cwd=HERE, env=ENV, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError("render failed: %s" % (p.stderr or p.stdout)[-400:])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default="d3d11")
    ap.add_argument("--adapter", default="nvidia")
    ap.add_argument("--json", default=os.path.join("out", "sharp", "results.json"))
    ap.add_argument("--curve", action="store_true", help="measure the cylinder scenarios (S2, S1-hook, S1-twopass) instead")
    args = ap.parse_args()
    combos = []
    if args.curve:
        for mon in ("laptop", "external"):
            for st, kern, k2 in (("s2", "ewa_lanczossharp", None), ("s1-hook", "ewa_lanczossharp", None),
                                 ("s1-twopass", "ewa_lanczossharp", "ewa_catmull_rom"), ("s1-twopass", "ewa_lanczossharp", "ewa_mitchell"),
                                 ("s1-twopass", "ewa_lanczossharp", "ewa_lanczossharp"), ("s2", "ewa_catmull_rom", None)):
                combos.append((mon, "plain-curve-%s-t7" % mon, st, kern, k2))
    for mon in ("laptop", "external") if not args.curve else ():
        for suffix, st, kern in (("", "s1", "ewa_lanczossharp"), ("", "s2", "ewa_lanczossharp"), ("", "s2", "ewa_lanczos"),
                                 ("", "s1", "ewa_lanczos"), ("", "s2", "ewa_lanczos4sharpest"), ("", "s1", "ewa_lanczos4sharpest"),
                                 ("", "s2", "ewa_robidoux"), ("", "s1", "ewa_robidoux"), ("", "s2", "ewa_mitchell"),
                                 ("", "s2", "ewa_hermite"), ("-ar", "s1", "ewa_lanczossharp"), ("-ar", "s2", "ewa_lanczossharp"),
                                 ("", "s1", "bicubic"), ("", "s1", "lanczos"), ("", "s1", "catmull_rom"), ("", "s1", "hermite")):
            combos.append((mon, "plain-%s-t7%s" % (mon, suffix), st, kern, None))
    results = {}
    for mon, scn, st, kern, k2 in combos:
        tag = "%s-%s-%s%s" % (scn, st, kern, ("-then-" + k2) if k2 else "")
        try:
            out = run_harness(scn, st, kern, args.backend, args.adapter, tag, k2)
        except RuntimeError as e:
            print("%-30s %-3s %-22s skipped: %s" % (scn, st, kern, str(e)[:80].replace("\n", " ")))
            continue
        setup = ref.Setup(ref.load_scenario(os.path.join(HERE, "scenarios", scn + ".json")))
        canvas, _ = read_canvas(os.path.join(HERE, out, mon))
        res = measure(canvas, setup, 0)
        results[tag] = {"scenario": scn, "structure": st, "kernel": kern, "kernel2": k2, **res}
        e = res.get("edge", {})
        z = res["zone"]
        print("%-8s %-10s %-22s %-18s MTF50 %.3f MTF30 %.3f @0.25 %.3f | over %.1f%% under %.1f%% (codes %.1f/%.1f) | zone %s | lines %s" % (
            mon, st, kern, k2 or "", e.get("mtf50", 0), e.get("mtf30", 0), e.get("mtf_at_0.25", 0), e.get("overshoot_pct", 0), e.get("undershoot_pct", 0),
            e.get("overshoot_code_pct", 0), e.get("undershoot_code_pct", 0), " ".join("%s:%.2f" % (b, v) for b, v in z.items()),
            " ".join("%s:%.2f" % (b, v) for b, v in res["linepairs_contrast"].items())))
    os.makedirs(os.path.dirname(os.path.join(HERE, args.json)), exist_ok=True)
    with open(os.path.join(HERE, args.json), "w", encoding="utf-8") as f:
        json.dump(results, f, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
