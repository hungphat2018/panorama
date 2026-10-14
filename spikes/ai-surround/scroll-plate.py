"""Plates that move with the camera (roles R2 + R3 + R5 for camera slides): the shot's own background, stitched from the
visible picture of every frame, completed once by the painter, slid under the picture as the camera moves.

Anime pans slide one long background painting under a fixed frame. A still plate cannot follow it (moving shots: seam
x9-35 for every plate) and a video model redraws the background on every frame. Here, per shot:
  1. camera offset per frame: the most common optical-flow vector between consecutive visible centres (grey, half size),
     chained, relative to the key frame (the clip's middle frame, where the plates are drawn). Only the visible picture
     is used, never the hidden truth.
  2. mosaic (source S2, real pixels of other frames of the same shot): every frame's visible centre placed at its offset
     on a canvas in key-frame coordinates that covers the whole travel; per pixel the median of the frames that saw it,
     so characters moving against the background drop out and the background stays.
  3. plate for what no frame saw, two versions:
       scroll-<fill>         that fill's existing key-frame plate (raw-s<side>.png, colour-fitted), edge-extended
                             beyond its canvas: no new painting
       scroll-mosaic-qwen21  the mosaic on grey, completed by Qwen-Image-2.1 + outpaint LoRA v2 (comfy-plates.qwen_graph,
                             caption of the key frame's visible picture): the painter sees all the real background
     The mosaic's real pixels are kept where seen (feathered 24 px into the painted part).
  4. per frame: the completed background sampled at that frame's offset, the real centre pasted, live seam pull
     (hidden_sides.live_seam) for what the background lacks at the seam (a character crossing it).

    python -I scroll-plate.py SCENES_DIR RUNS_DIR --scenes pan-horizontal,jjk-pan [--fill qwen21-outpaint-v2] [--paint]
                              [--side 0.5] [--seed 7] [--port 8189]

--paint runs the painter (GPU; ComfyUI is started unless a server answers on --port; a stitched canvas above 2.2 MP is
painted scaled down to it, the real pixels stay full size); without it only the scroll-<fill>
version is made (CPU). Writes RUNS_DIR/<recipe>/<scene>/s<side>.mp4 (+ -with-truth.mp4, recipe.json, mosaic.png,
background.png), the offsets in recipe.json, scores (+ boil) in RUNS_DIR/scores-scroll.json and a sheet
RUNS_DIR/sheets/<scene>-scroll-s<side>.png.
"""
import argparse
import importlib.util
import json
import os
import sys
import time

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)                                          # python -I leaves the script's folder off sys.path
import comfy_client as cc  # noqa: E402
import hidden_sides as hs  # noqa: E402


def load_script(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), os.path.join(HERE, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


plates = load_script("comfy-plates")                              # qwen_graph, PAD, GREY: the painter recipe unchanged
wraps = load_script("apply-wraps")                                # still_steps, boil
FEATHER = 24                                                      # px of blend from seen (real) into painted pixels
MAX_PAINT_PIXELS = 2.2e6                                          # larger stitched canvases are painted scaled down


def camera_offsets(vis, key):
    """Per frame (dx, dy): where the key frame's content sits in that frame (content moved by +d), from the visible
    centres only. Dense optical flow (DIS) between consecutive frames at half size; the camera step is the most common
    flow vector (the background wins by area, not by contrast: phase correlation locked onto a character the camera
    follows). Chained. Returns (offsets, agreement), agreement = share of pixels within 1.5 px of that step."""
    g = [cv2.resize(cv2.cvtColor(v, cv2.COLOR_RGB2GRAY), (v.shape[1] // 2, v.shape[0] // 2), interpolation=cv2.INTER_AREA)
         for v in vis]
    dis = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM)
    acc, agree = [np.zeros(2)], [1.0]
    for t in range(1, len(g)):
        step, agreement = hs.flow_step(dis, g[t - 1], g[t])
        acc.append(acc[-1] + 2.0 * step)
        agree.append(agreement)
    acc = np.array(acc)
    return acc - acc[key], agree


def build_mosaic(vis, offsets, side, ext, size):
    """Median of every frame's visible centre placed at its offset on the extended key-frame canvas.
    size = (h, w) of the extended canvas; ext = (left, top) margins. Returns (mosaic uint8, seen-count)."""
    hh, ww = size
    vh, vw = vis[0].shape[:2]
    mosaic = np.zeros((hh, ww, 3), np.uint8)
    count = np.zeros((hh, ww), np.int32)
    # Key coordinates of frame t's visible centre: x in [side - dx, side + vw - dx), shifted by the left margin.
    mats = [np.float32([[1, 0, ext[0] + side - d[0]], [0, 1, ext[1] - d[1]]]) for d in offsets]
    strip = 48                                                    # rows at a time: the median stack stays small
    for y0 in range(0, hh, strip):
        y1 = min(hh, y0 + strip)
        stack = np.full((len(vis), y1 - y0, ww, 3), np.nan, np.float32)
        for t, (v, m) in enumerate(zip(vis, mats)):
            m2 = m.copy()
            m2[1, 2] -= y0
            img = cv2.warpAffine(v.astype(np.float32), m2, (ww, y1 - y0), flags=cv2.INTER_LINEAR,
                                 borderMode=cv2.BORDER_CONSTANT, borderValue=(np.nan, np.nan, np.nan))
            # Valid where the whole bilinear footprint lies inside the frame (no blend with the NaN border).
            ok = cv2.warpAffine(np.ones((vh, vw), np.float32), m2, (ww, y1 - y0), flags=cv2.INTER_LINEAR,
                                borderMode=cv2.BORDER_CONSTANT, borderValue=0) > 0.999
            img[~ok] = np.nan
            stack[t] = img
        with np.errstate(all="ignore"):
            med = np.nanmedian(stack, axis=0)
        n = np.isfinite(stack[..., 0]).sum(axis=0)
        mosaic[y0:y1] = np.nan_to_num(med).clip(0, 255).round().astype(np.uint8)
        count[y0:y1] = n
    return mosaic, count


def feather_keep(painted, mosaic, seen):
    """Real mosaic pixels where seen, painted ones elsewhere, blended over FEATHER px inside the seen area's border."""
    dist = cv2.distanceTransform(seen.astype(np.uint8), cv2.DIST_L2, 5)
    w = np.clip(dist / FEATHER, 0, 1)[..., None]
    return (w * mosaic.astype(np.float32) + (1 - w) * painted.astype(np.float32)).clip(0, 255).round().astype(np.uint8)


def render(background, offsets, ext, vis, side, size):
    """Each frame: the background sampled at that frame's offset (canvas size `size` = (h, w)), the real centre pasted,
    live seam pull."""
    h, w = size
    out = []
    for d, v in zip(offsets, vis):
        m = np.float32([[1, 0, ext[0] - d[0]], [0, 1, ext[1] - d[1]]])
        f = cv2.warpAffine(background, m, (w, h), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                           borderMode=cv2.BORDER_REPLICATE).astype(np.float32)
        f = hs.live_seam(f, v, side)
        f = f.clip(0, 255).round().astype(np.uint8)
        f[:, side:side + v.shape[1]] = v                          # the real picture is never the fill's
        out.append(f)
    return out


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("scenes_dir")
    ap.add_argument("runs_dir")
    ap.add_argument("--scenes", required=True)
    ap.add_argument("--fill", default="qwen21-outpaint-v2", help="existing key-frame plate for the scroll-<fill> version")
    ap.add_argument("--paint", action="store_true", help="also complete the mosaic with the painter (GPU)")
    ap.add_argument("--side", type=float, default=0.5)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--port", type=int, default=8189)
    a = ap.parse_args(argv)

    scores_path = os.path.join(a.runs_dir, "scores-scroll.json")
    scores = json.load(open(scores_path, encoding="utf-8")) if os.path.isfile(scores_path) else {}
    jobs = []
    for sc in a.scenes.split(","):
        sdir = os.path.join(a.scenes_dir, sc)
        meta = json.load(open(os.path.join(sdir, "scene.json"), encoding="utf-8"))
        frames = hs.read_frames(os.path.join(sdir, "clip.mkv"))
        key = len(frames) // 2                                    # the frame the plates were drawn from
        parts = [hs.split(f, a.side) for f in frames]
        truth, vis, side = [p[0] for p in parts], [p[1] for p in parts], parts[0][2]
        h, w = truth[0].shape[:2]
        offsets, resp = camera_offsets(vis, key)
        # Margins of the key-frame canvas that the travel needs (content moving by +d needs key content at x - d).
        left = int(np.ceil(max(0.0, offsets[:, 0].max())))
        right = int(np.ceil(max(0.0, -offsets[:, 0].min())))
        top = int(np.ceil(max(0.0, offsets[:, 1].max())))
        bottom = int(np.ceil(max(0.0, -offsets[:, 1].min())))
        # Round the extended canvas up to the painter's /32 grid, the extra split evenly.
        ew, eh = w + left + right, h + top + bottom
        pw, ph = -(-ew // 32) * 32, -(-eh // 32) * 32
        left += (pw - ew) // 2
        top += (ph - eh) // 2
        ext = (left, top)
        mosaic, count = build_mosaic(vis, offsets, side, ext, (ph, pw))
        seen = count > 0
        travel = offsets.max(0) - offsets.min(0)
        print(f"{sc}: travel x {travel[0]:.0f} y {travel[1]:.0f} px, weakest flow agreement {min(resp[1:]):.2f}, median {np.median(resp[1:]):.2f}, "
              f"canvas {pw}x{ph}, sides seen {seen[top:top + h, left:left + w][:, np.r_[0:side, w - side:w]].mean():.0%}",
              flush=True)
        jobs.append(dict(sc=sc, meta=meta, truth=truth, vis=vis, side=side, key=key, offsets=offsets, resp=resp,
                         ext=ext, size=(ph, pw), mosaic=mosaic, seen=seen))

    # Version 1: the existing key-frame plate under the mosaic (no new painting).
    for j in jobs:
        raw_path = os.path.join(a.runs_dir, a.fill, j["sc"], f"raw-s{a.side}.png")
        if not os.path.isfile(raw_path):
            print(f"skip scroll-{a.fill} {j['sc']}: no {raw_path}")
            continue
        h, w = j["truth"][0].shape[:2]
        side, vis_k = j["side"], j["vis"][j["key"]]
        plate = cv2.resize(cv2.cvtColor(cv2.imread(raw_path), cv2.COLOR_BGR2RGB), (w, h), interpolation=cv2.INTER_LANCZOS4)
        M = hs.colour_fit(plate[:, side:side + vis_k.shape[1]], vis_k)
        plate = np.clip(hs.apply_fit(plate, M), 0, 255).round().astype(np.uint8)
        (left, top), (ph, pw) = j["ext"], j["size"]
        big = cv2.copyMakeBorder(plate, top, ph - h - top, left, pw - w - left, cv2.BORDER_REPLICATE)
        j["bg_existing"] = feather_keep(big, j["mosaic"], j["seen"])

    # Version 2: the painter completes the mosaic.
    if a.paint:
        prepared = []
        for j in jobs:
            canvas = np.full(j["size"] + (3,), plates.GREY, np.uint8)
            canvas[j["seen"]] = j["mosaic"][j["seen"]]
            ph, pw = j["size"]
            scale = min(1.0, (MAX_PAINT_PIXELS / (ph * pw)) ** 0.5)  # the painter's plates are ~2 MP (1920x1088)
            if scale < 1.0:                                       # painted smaller; real pixels go back at full size
                size = (max(32, int(pw * scale) // 32 * 32), max(32, int(ph * scale) // 32 * 32))
                canvas = cv2.resize(canvas, size, interpolation=cv2.INTER_AREA)
            names = (f"panorama-scroll-{j['sc']}-canvas.png", f"panorama-scroll-{j['sc']}-centre.png")
            cv2.imwrite(os.path.join(cc.INPUT_DIR, names[0]), cv2.cvtColor(canvas, cv2.COLOR_RGB2BGR))
            cv2.imwrite(os.path.join(cc.INPUT_DIR, names[1]), cv2.cvtColor(j["vis"][j["key"]], cv2.COLOR_RGB2BGR))
            prepared.append((j, names))
        server = cc.start_server(a.port, os.path.join(a.runs_dir, "comfy-server.log"), cc.LOW_RAM)
        try:
            for j, (canvas_name, centre_name) in prepared:
                t0 = time.perf_counter()
                out = cc.run_graph(a.port, plates.qwen_graph("qwen-image-2.1-outpaint-v2.safetensors", canvas_name,
                                                             centre_name, a.seed, f"panorama/scroll-{j['sc']}"))
                painted = cc.saved_images(out, "12")[0]
                if painted.shape[:2] != j["size"]:
                    painted = cv2.resize(painted, (j["size"][1], j["size"][0]), interpolation=cv2.INTER_LANCZOS4)
                j["paint_seconds"] = round(time.perf_counter() - t0, 1)
                j["caption"] = out.get("13", {}).get("text", [""])[0].strip()
                j["bg_painted"] = feather_keep(painted, j["mosaic"], j["seen"])
                j["painted_raw"] = painted
                print(f"painted {j['sc']} in {j['paint_seconds']} s", flush=True)
        finally:
            cc.stop_server(server)

    for j in jobs:
        sc, side, truth, vis = j["sc"], j["side"], j["truth"], j["vis"]
        h, w = truth[0].shape[:2]
        pic_still = wraps.still_steps(vis, 6, 0.002)
        rows, labels = [hs.mark_seams(truth[j["key"]], side)], [f"{sc}  s={a.side}  TRUTH (frame {j['key']})"]
        for name, bg_key in ((f"scroll-{a.fill}", "bg_existing"), ("scroll-mosaic-qwen21", "bg_painted")):
            if bg_key not in j:
                continue
            out = render(j[bg_key], j["offsets"], j["ext"], vis, side, (h, w))
            s_ = hs.score(truth, out, side)
            s_["boil"] = wraps.boil(out, truth, side, pic_still)
            scores.setdefault(name, {}).setdefault(sc, {})[f"s{a.side}"] = s_
            odir = os.path.join(a.runs_dir, name, sc)
            os.makedirs(odir, exist_ok=True)
            hs.write_video(out, os.path.join(odir, f"s{a.side}.mp4"), j["meta"]["fps"])
            hs.write_stacked(truth, out, side, os.path.join(odir, f"s{a.side}-with-truth.mp4"), j["meta"]["fps"])
            cv2.imwrite(os.path.join(odir, "mosaic.png"), cv2.cvtColor(j["mosaic"], cv2.COLOR_RGB2BGR))
            cv2.imwrite(os.path.join(odir, "background.png"), cv2.cvtColor(j[bg_key], cv2.COLOR_RGB2BGR))
            hs.save_json({"recipe": ["R2:mosaic of the shot's visible centres (median)",
                                     f"R3:{a.fill} key plate" if bg_key == "bg_existing" else "R3:qwen21-outpaint-v2 on the mosaic",
                                     "R5:scroll with the camera", "R5:live"],
                          "key_frame": j["key"], "ext_left_top": j["ext"], "canvas": [j["size"][1], j["size"][0]],
                          "offsets": [[round(float(x), 2), round(float(y), 2)] for x, y in j["offsets"]],
                          "flow_agreement": [round(r, 3) for r in j["resp"]],
                          "paint_seconds": j.get("paint_seconds"), "caption": j.get("caption")},
                         os.path.join(odir, "recipe.json"))
            rows.append(hs.mark_seams(out[j["key"]], side))
            labels.append(f"{name}  near {s_['err_near']}  far {s_['err_far']}  seam x{s_['seam_step']}  "
                          f"flicker {s_['flicker']:+}  boil {s_['boil']:+}")
            print(f"{name:34s} {sc:18s} {s_}", flush=True)
        hs.save_json(scores, scores_path)
        if len(rows) > 1:
            hs.compose_sheet(rows, labels, os.path.join(a.runs_dir, "sheets", f"{sc}-scroll-s{a.side}.png"), width=1280)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
