"""Identity references for the painter (phase 3 + phase 4's identity crops): does a crop of a character cut by the seam,
taken from another shot, make the plate keep that character's look (jjk-one-side: the blood stain, short hair, no
ponytail; without it the painter turns the stain into a red scarf)?

Variants on one scene (the hidden-sides canvas at ~1 MP, the live painter W4A8 and recipe, seeds from --seeds):
  none          today's live recipe: the grey proxy conditioning (one image slot), no reference
  ref-grey      + the crop as a second reference latent, a proxy conditioning with two grey images (no encoder live)
  ref-read      + the crop, the encoder having read the canvas and the crop (the upper bound of a real read)
  ref-told      + the crop, two grey images, the instruction also naming the reference
  ref-mixed     + the crop, the encoder having read a tiny grey canvas and the crop (what a CPU read per crop gives)
The encoder runs once (its own service) for the conditionings, or `--from-run` reuses an earlier run's; then the painter
paints each variant at each seed (one seed decides little: the painter's guess for a cut-off character varies by seed).

    python -I -X utf8 painter-identity-test.py [--scene jjk-one-side] [--refs out/identity-test/ref-shot500-person.png]
        [--dit qwen_image_2.1_turbo8-ov2_w4a8.safetensors] [--rest 120] [--seeds 7,11,23] [--from-run RUN]
Writes out/identity-test/<stamp>-<scene>/: conds/, plates <variant>-s<seed>.png, sheet-s<seed>.png (truth, the
variants, the reference), identity.json (scores per variant and seed, and their means).
"""
import argparse
import importlib.util
import os
import shutil
import sys
import time

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import hidden_sides as hs  # noqa: E402


def load_script(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), os.path.join(HERE, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bench = load_script("painter-bench")
ps = bench.ps
TOLD = (" The other images show characters of this scene who are cut by the picture's edge: continue them with the "
        "same hair, clothes and marks.")


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--scene", default="jjk-one-side")
    ap.add_argument("--refs", default=os.path.join(HERE, "out", "identity-test", "ref-shot500-person.png"))
    ap.add_argument("--dit", default="qwen_image_2.1_turbo8-ov2_w4a8.safetensors")
    ap.add_argument("--te", default="qwen3vl_8b_int8_convrot.safetensors")
    ap.add_argument("--rest", type=float, default=120.0)
    ap.add_argument("--port", type=int, default=8193)
    ap.add_argument("--variants", default="", help="comma-separated subset of none,ref-grey,ref-read,ref-told,ref-mixed")
    ap.add_argument("--seeds", default="7", help="comma-separated painter seeds")
    ap.add_argument("--attention", default="ck-int8", help="painter-service.py --attention (the live recipe's)")
    ap.add_argument("--schedule", default="turbo6", help="painter_core.SCHEDULES (the live recipe's)")
    ap.add_argument("--attn-chunk", type=int, default=2048, help="painter-service.py --attn-chunk (the live recipe's)")
    ap.add_argument("--from-run", default="", help="reuse this earlier run's conds/ (same scene and refs): no encoder")
    a = ap.parse_args(argv)
    refs = [r for r in a.refs.split(",") if r]
    seeds = [int(s) for s in a.seeds.split(",") if s]
    run = os.path.join(HERE, "out", "identity-test", f"{time.strftime('%Y%m%d-%H%M%S')}-{a.scene}")
    os.makedirs(os.path.join(run, "conds"), exist_ok=True)
    prep = bench.prepare([a.scene], os.path.join(HERE, "out", "scenes"), run)[a.scene]
    h, w = prep["canvas"].shape[:2]
    grey = os.path.join(run, "grey-canvas.png")
    bench.write_png(grey, np.full((h, w, 3), bench.plates_mod.GREY, np.uint8))
    grey_refs = []
    for i, r in enumerate(refs):
        im = cv2.imread(r)
        p = os.path.join(run, f"grey-ref{i + 1}.png")
        cv2.imwrite(p, np.full(im.shape, bench.plates_mod.GREY, np.uint8))
        grey_refs.append(p)
    instr = bench.plates_mod.INSTRUCTION
    small_grey = os.path.join(run, "grey-small.png")             # the canvas read does not matter (phase 3, Q1): tiny
    bench.write_png(small_grey, np.full((64, 128, 3), bench.plates_mod.GREY, np.uint8))
    conds = {"none": (instr, [grey]), "ref-grey": (instr, [grey] + grey_refs),
             "ref-read": (instr, [prep["canvas_path"]] + refs), "ref-told": (instr + TOLD, [grey] + grey_refs),
             "ref-mixed": (instr, [small_grey] + refs)}
    if a.variants:
        conds = {k: v for k, v in conds.items() if k in a.variants.split(",")}
    gpu = ps.GpuMonitor(os.path.join(run, "gpu.csv"))
    gpu.start()
    log = []
    try:
        if a.from_run:
            for name in conds:
                shutil.copy2(os.path.join(a.from_run, "conds", f"{name}.pt"), os.path.join(run, "conds", f"{name}.pt"))
            print(f"conds from {a.from_run}", flush=True)
        else:
            ps.rest(a.rest, "before the encoder", gpu, lambda e: log.append(e))
            svc = bench.ServiceProc(a.port, ["--te", a.te], os.path.join(run, "service-encode.log"))
            try:
                for name, (prompt, images) in conds.items():
                    r = svc.call("/encode", {"prompt": prompt, "images": images,
                                             "out": os.path.join(run, "conds", f"{name}.pt")})
                    print(f"encode {name}: {r}", flush=True)
            finally:
                svc.stop()
        ps.rest(a.rest, "before the painter", gpu, lambda e: log.append(e))
        svc = bench.ServiceProc(a.port, ["--dit", a.dit, "--kv-cache", "gpu-int8", "--vram-cap-gb", "10",
                                         "--attention", a.attention, "--attn-chunk", str(a.attn_chunk)],
                                os.path.join(run, "service-paint.log"))
        rows = {}
        try:
            for seed in seeds:
                for name in conds:
                    raw = os.path.join(run, f"{name}-s{seed}-raw.png")
                    res = svc.call("/paint", {"canvas": prep["canvas_path"], "seed": seed, "out": raw,
                                              "schedule": a.schedule,
                                              "cond": os.path.join(run, "conds", f"{name}.pt"),
                                              "extra_refs": [] if name == "none" else refs})
                    img = bench.read_png(raw)[prep["pad"]:prep["pad"] + prep["ph"]]
                    big = cv2.resize(img, (prep["truth"].shape[1], prep["truth"].shape[0]),
                                     interpolation=cv2.INTER_LANCZOS4)
                    big[:, prep["side"]:prep["side"] + prep["vis"].shape[1]] = prep["vis"]
                    bench.write_png(os.path.join(run, f"{name}-s{seed}.png"), big)
                    r = rows[(name, seed)] = dict(hs.score([prep["truth"]], [big], prep["side"]), seconds=res["seconds"])
                    print(f"paint {name} seed {seed}: {res['seconds']['total']:.2f} s near {r['err_near']} far "
                          f"{r['err_far']} seam x{r['seam_step']}", flush=True)
                    time.sleep(10)
        finally:
            svc.stop()
    finally:
        gpu.stop()
    for seed in seeds:
        write_sheet(run, prep, refs, conds, rows, seed)
    means = {n: {k: round(float(np.mean([rows[(n, s)][k] for s in seeds])), 2) for k in ("err_near", "err_far", "seam_step")}
             for n in conds}
    for n, m in means.items():
        print(f"mean over {len(seeds)} seeds  {n:10s} near {m['err_near']} far {m['err_far']} seam x{m['seam_step']}")
    hs.save_json({"scene": a.scene, "refs": refs, "seeds": seeds, "means": means,
                  "rows": {f"{n}/s{s}": r for (n, s), r in rows.items()}, "conds": {k: v[1] for k, v in conds.items()}},
                 os.path.join(run, "identity.json"))
    print(f"sheets {os.path.join(run, 'sheet-s*.png')}")
    return 0


def write_sheet(run, prep, refs, conds, rows, seed):
    """Truth, each variant's plate at this seed (seams marked, scores in the label), then the first reference."""
    tiles = [hs.mark_seams(prep["truth"], prep["side"])] + [
        hs.mark_seams(bench.read_png(os.path.join(run, f"{n}-s{seed}.png")), prep["side"]) for n in conds]
    labels = ["TRUTH (hidden sides)"] + [
        f"{n} seed {seed}  near {rows[(n, seed)]['err_near']} far {rows[(n, seed)]['err_far']} "
        f"seam x{rows[(n, seed)]['seam_step']}" for n in conds]
    cells = []
    for img, label in zip(tiles, labels):
        im = cv2.resize(img, (960, 540), interpolation=cv2.INTER_AREA)
        strip = np.zeros((22, 960, 3), np.uint8)
        cv2.putText(strip, label, (6, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA)
        cells.append(np.vstack([strip, im]))
    ref = bench.read_png(refs[0])
    ref = cv2.resize(ref, (int(round(ref.shape[1] * 540 / ref.shape[0])), 540))
    pad = np.zeros((562, 960, 3), np.uint8)
    pad[22:, :ref.shape[1]] = ref[:, :960]
    cells.append(pad)
    if len(cells) % 2:
        cells.append(np.zeros_like(cells[0]))
    sheet = np.vstack([np.hstack([cells[i], np.zeros((562, 4, 3), np.uint8), cells[i + 1]]) for i in range(0, len(cells), 2)])
    bench.write_png(os.path.join(run, f"sheet-s{seed}.png"), sheet)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
