"""Best-of-N still plates (role R6, judge): N seeds per scene, ranked by signals that need no truth, checked against it.

Quality first means compute may be spent on choice: draw several plates per shot and keep the best. That needs a judge
that works on any film, where the hidden sides are unknown. Two such signals per plate:
  centre_fit  how faithfully the model redrew the visible centre it was given: mean abs error of its own redraw after
              the per-shot colour fit (hidden_sides.colour_fit), area-shrunk so line art need not align. A model that
              misread the picture redraws it differently, and its sides tend to follow the misreading.
  seam_self   the jump across each seam divided by the mean jump one and two columns to either side (1.0: the seam
              column looks like any other column), from the pasted plate alone.
On the hidden-sides test the truth is known, so the Spearman rank correlation per scene between each signal and the
usual scores (err_near, err_far, seam_step) shows whether the signal picks better plates. The pick (lowest summed rank
of both signals) is written as its own fill, so the rest of the chain (seam-match, blind test) can use it.

    python -I best-of-n-plates.py SCENES_DIR OUT_DIR [--n 8] [--seed0 100] [--refs] [--prompt "..."] [--scale 0.5]
                                  [--scenes a,b] [--name klein-green-refs]

Writes OUT_DIR/<name>-best<n>/<scene>/{plate,raw}-s<side>.png, OUT_DIR/<name>-n<n>/<scene>/seed<k>.png (every draw),
OUT_DIR/scores-best-of-n.json and OUT_DIR/sheets/<scene>-<name>-n<n>-s<side>.png (truth, then the draws by judge rank).
"""
import argparse
import importlib.util
import json
import os
import sys

import cv2
import numpy as np
import torch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)                                          # python -I leaves the script's folder off sys.path
import hidden_sides as hs  # noqa: E402

_spec = importlib.util.spec_from_file_location("klein_plates", os.path.join(HERE, "run-klein-plates.py"))
kp = importlib.util.module_from_spec(_spec)                       # the plate recipe itself, reused unchanged
_spec.loader.exec_module(kp)


def centre_fit_error(raw, vis, side, size):
    """Mean abs error (0-255) of the model's own redraw of the visible centre after the colour fit, area-shrunk 4x."""
    big = cv2.resize(raw, size, interpolation=cv2.INTER_LANCZOS4)
    model_centre = big[:, side:side + vis.shape[1]]
    fitted = np.clip(hs.apply_fit(model_centre, hs.colour_fit(model_centre, vis)), 0, 255)
    h, w = vis.shape[:2]
    small = (max(8, w // 4), max(8, h // 4))
    a = cv2.resize(fitted.astype(np.float32), small, interpolation=cv2.INTER_AREA)
    b = cv2.resize(vis.astype(np.float32), small, interpolation=cv2.INTER_AREA)
    return float(np.abs(a - b).mean())


def seam_self(plate, side):
    """Jump across both seams divided by the mean jump of the two columns on either side of each (needs no truth)."""
    x = plate.astype(np.float32)
    w = x.shape[1]
    def col_jump(c):
        return float(np.abs(x[:, c + 1] - x[:, c]).mean())
    ratios = []
    for seam in (side - 1, w - side - 1):                         # the jump between column seam and seam + 1
        around = [col_jump(c) for c in (seam - 2, seam - 1, seam + 1, seam + 2)]
        ratios.append(col_jump(seam) / max(1e-3, float(np.mean(around))))
    return float(np.mean(ratios))


def spearman(a, b):
    ra, rb = np.argsort(np.argsort(a)), np.argsort(np.argsort(b))
    if np.std(ra) == 0 or np.std(rb) == 0:
        return 0.0
    return float(np.corrcoef(ra, rb)[0, 1])


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("scenes_dir")
    ap.add_argument("out_dir")
    ap.add_argument("--n", type=int, default=8)
    ap.add_argument("--seed0", type=int, default=100)
    ap.add_argument("--side", type=float, default=0.5)
    ap.add_argument("--scale", type=float, default=0.5)
    ap.add_argument("--steps", type=int, default=4)
    ap.add_argument("--refs", action="store_true", help="add the scene's reference images; skip scenes without")
    ap.add_argument("--prompt", default="", help="replaces the green LoRA's prompt")
    ap.add_argument("--scenes", default="", help="comma-separated subset (default: every scene)")
    ap.add_argument("--name", default="", help="family name (default klein-green[-refs])")
    a = ap.parse_args(argv)
    name = a.name or ("klein-green-refs" if a.refs else "klein-green")

    scenes = [d for d in sorted(os.listdir(a.scenes_dir)) if os.path.isfile(os.path.join(a.scenes_dir, d, "clip.mkv"))]
    if a.scenes:
        scenes = [sc for sc in scenes if sc in a.scenes.split(",")]
    refs = {sc: kp.load_refs(os.path.join(a.scenes_dir, sc)) if a.refs else [] for sc in scenes}
    if a.refs:
        scenes = [sc for sc in scenes if refs[sc]]
    pipe = kp.load("green")
    embeds = kp.encode_prompt_on_cpu(pipe, a.prompt or kp.GREEN_PROMPT)

    scores_path = os.path.join(a.out_dir, "scores-best-of-n.json")
    scores = json.load(open(scores_path, encoding="utf-8")) if os.path.isfile(scores_path) else {}
    family, best_name = f"{name}-n{a.n}", f"{name}-best{a.n}"
    os.makedirs(os.path.join(a.out_dir, "sheets"), exist_ok=True)
    corr_all = {}
    for sc in scenes:
        frame = kp.middle_frame(os.path.join(a.scenes_dir, sc))
        draws = []
        for k in range(a.n):
            seed = a.seed0 + k
            truth, big, raw, side, secs, peak = kp.run_one(pipe, "green", embeds, frame, a.side, a.scale, a.steps, seed,
                                                           refs[sc])
            vis = truth[:, side:truth.shape[1] - side]
            d = hs.score([truth], [big], side)
            d.update({"seed": seed, "seconds": round(secs, 2), "peak_vram_gb": round(peak, 2),
                      "centre_fit": round(centre_fit_error(raw, vis, side, (truth.shape[1], truth.shape[0])), 2),
                      "seam_self": round(seam_self(big, side), 2)})
            draws.append((d, big, raw))
            odir = os.path.join(a.out_dir, family, sc)
            os.makedirs(odir, exist_ok=True)
            cv2.imwrite(os.path.join(odir, f"seed{seed}.png"), cv2.cvtColor(big, cv2.COLOR_RGB2BGR))
            print(f"{sc:18s} seed {seed}: {d}")
        rows = [d for d, _, _ in draws]
        corr = {sig: {m: round(spearman([r[sig] for r in rows], [r[m] for r in rows]), 2)
                      for m in ("err_near", "err_far", "seam_step")} for sig in ("centre_fit", "seam_self")}
        corr_all[sc] = corr
        rank = np.argsort(np.argsort([r["centre_fit"] for r in rows])) + np.argsort(np.argsort([r["seam_self"] for r in rows]))
        order = list(np.argsort(rank, kind="stable"))
        pick, pick_big, pick_raw = draws[order[0]]
        bdir = os.path.join(a.out_dir, best_name, sc)
        os.makedirs(bdir, exist_ok=True)
        cv2.imwrite(os.path.join(bdir, f"plate-s{a.side}.png"), cv2.cvtColor(pick_big, cv2.COLOR_RGB2BGR))
        cv2.imwrite(os.path.join(bdir, f"raw-s{a.side}.png"), cv2.cvtColor(pick_raw, cv2.COLOR_RGB2BGR))
        mean = {m: round(float(np.mean([r[m] for r in rows])), 2) for m in ("err_near", "err_far", "seam_step")}
        scores.setdefault(family, {})[sc] = {"draws": rows, "corr": corr, "mean": mean}
        scores.setdefault(best_name, {}).setdefault(sc, {})[f"s{a.side}"] = dict(pick, picked_from=a.n)
        truth, _, side = hs.split(frame, a.side)
        sheet_rows, labels = [hs.mark_seams(truth, side)], [f"{sc}  s={a.side}  TRUTH   (draws below in judge order)"]
        for i in order:
            d, big, _ = draws[i]
            sheet_rows.append(hs.mark_seams(big, side))
            labels.append(f"seed {d['seed']}  judge: centre_fit {d['centre_fit']}  seam_self {d['seam_self']}   "
                          f"truth: near {d['err_near']}  far {d['err_far']}  seam x{d['seam_step']}")
        hs.compose_sheet(sheet_rows, labels, os.path.join(a.out_dir, "sheets", f"{sc}-{family}-s{a.side}.png"), width=960)
        print(f"{sc:18s} pick seed {pick['seed']}  mean of draws {mean}  corr {corr}")
        hs.save_json(scores, scores_path)
    for sig in ("centre_fit", "seam_self"):
        print(sig, {m: round(float(np.mean([corr_all[sc][sig][m] for sc in corr_all])), 2)
                    for m in ("err_near", "err_far", "seam_step")}, "(mean Spearman over scenes; > 0 = the signal predicts it)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
