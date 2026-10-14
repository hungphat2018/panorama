"""Real-use preview for moving shots: an uncut 3 s moment extended to a 32:9 canvas by a moving-sides recipe.

make-wide-demo.py holds one Qwen plate per side for a still shot. Moving shots need sides that move, so here the whole real
frame (16:9) is the visible picture and each 8:9 side is drawn by one of the moving recipes of the hidden-sides test:
  scroll       scroll-plate.py on the uncut clip: the camera offset per frame, the median mosaic of all frames (real
               pixels the camera shows in other frames), the Qwen-Image-2.1 + outpaint LoRA v2 painter for the rest
               (scaled to 2.2 MP), slid with the camera; live seam pull
  vace13       Wan2.1-VACE-1.3B (diffusers), one run per side; the -key variant keeps a Qwen plate of that side
               (make-wide-demo's per-side plate, middle frame) as a fully kept frame, as --key-plate does in the test
  a14b         Wan2.2-VACE-Fun-A14B Q8 (ComfyUI, comfy-vace-clips' graph and settings), one run per side; -key likewise
Per side, the video canvas is that side's 8:9 plus the adjacent half of the picture (16:9, 848x480), the geometry the
plate demo uses; the model's redraw of the real half gives one colour fit per side, the real frame is pasted back, then
the live seam pull and the freeze wrap (a side stays put while the picture's band next to it is still).

    python -I make-wide-moving-demo.py FILM OUT_DIR --at SECONDS --recipe scroll|vace13|vace13-key|a14b|a14b-key
                                       [--seed 7] [--port 8189]

Writes OUT_DIR/<stamp>-<recipe>/ with make-wide-demo's files (wide-32x9.mp4, original-16x9.mp4, preview.png,
preview-marked.png, span-<layout>[-original-only].mp4, play-<layout>.bat, play-compare-<layout>.bat) and demo.json.
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


demo = load_script("make-wide-demo")                              # grab, write_spans, write_compare, per-side plates
scroll = load_script("scroll-plate")                              # camera_offsets, build_mosaic, feather_keep, render
wraps = load_script("apply-wraps")                                # still_steps, apply_freeze
plates = demo.plates
FRAMES = 69                                                       # 4k + 1 for the video models, ~2.9 s
SIDE = 960                                                        # each side of the 32:9 canvas
GEN_W, GEN_H = 848, 480


def side_canvases(frames, name, key_side=None):
    """VACE inputs for one side: per frame the 16:9 canvas (grey side + adjacent real half) at 848x480 and its mask
    (white = generate); with key_side (the side's plate, 1080x960) the middle frame is kept whole."""
    canvases, masks = [], []
    key = len(frames) // 2
    for t, f in enumerate(frames):
        half = f[:, :SIDE] if name == "left" else f[:, SIDE:]
        big = np.full((1080, 2 * SIDE, 3), plates.GREY, np.uint8)
        gen = slice(0, SIDE) if name == "left" else slice(SIDE, 2 * SIDE)
        real = slice(SIDE, 2 * SIDE) if name == "left" else slice(0, SIDE)
        big[:, real] = half
        m = np.zeros((GEN_H, GEN_W), np.uint8)
        if key_side is not None and t == key:
            big[:, gen] = key_side
        else:
            gx = slice(0, GEN_W // 2) if name == "left" else slice(GEN_W // 2, GEN_W)
            m[:, gx] = 255
        canvases.append(cv2.resize(big, (GEN_W, GEN_H), interpolation=cv2.INTER_AREA))
        masks.append(m)
    return canvases, masks


def finish_side(model_frames, frames, name):
    """Model canvases (any size) -> that side's 1080x960 per frame, colour-fitted by the model's redraw of the real half."""
    big = [cv2.resize(m, (2 * SIDE, 1080), interpolation=cv2.INTER_LANCZOS4) for m in model_frames]
    gen = slice(0, SIDE) if name == "left" else slice(SIDE, 2 * SIDE)
    real = slice(SIDE, 2 * SIDE) if name == "left" else slice(0, SIDE)
    halves = [f[:, :SIDE] if name == "left" else f[:, SIDE:] for f in frames]
    M = hs.colour_fit(np.stack([b[:, real] for b in big[::8]]), np.stack(halves[::8]))
    return [np.clip(hs.apply_fit(b[:, gen], M), 0, 255).round().astype(np.uint8) for b in big]


def run_vace13(jobs, steps, seed):
    """{side: model frames} from Wan2.1-VACE-1.3B, one pipeline load for both sides (run-vace-clips' setup)."""
    import torch
    from PIL import Image
    rv = load_script("run-vace-clips")
    from diffusers import AutoencoderKLWan, WanVACEPipeline
    from diffusers.schedulers.scheduling_unipc_multistep import UniPCMultistepScheduler
    vae = AutoencoderKLWan.from_pretrained(rv.MODEL, subfolder="vae", torch_dtype=torch.float32)
    pipe = WanVACEPipeline.from_pretrained(rv.MODEL, vae=vae, torch_dtype=torch.bfloat16)
    pipe.scheduler = UniPCMultistepScheduler.from_config(pipe.scheduler.config, flow_shift=3.0)
    pe, ne = rv.encode_on_cpu(pipe, rv.PROMPT, rv.NEGATIVE)
    pipe.transformer.to("cuda")
    pipe.vae.to("cuda")
    pipe.vae.enable_tiling()
    out = {}
    for name, (canvases, masks) in jobs.items():
        t0 = time.perf_counter()
        res = pipe(video=[Image.fromarray(c) for c in canvases], mask=[Image.fromarray(m) for m in masks],
                   prompt_embeds=pe, negative_prompt_embeds=ne, height=GEN_H, width=GEN_W, num_frames=len(canvases),
                   num_inference_steps=steps, guidance_scale=5.0, generator=torch.Generator("cuda").manual_seed(seed),
                   output_type="np").frames[0]
        out[name] = [(np.clip(f, 0, 1) * 255).round().astype(np.uint8) for f in res]
        print(f"vace13 {name} side in {time.perf_counter() - t0:.0f} s", flush=True)
    return out


def run_a14b(jobs, steps, seed, port, log_path):
    """{side: model frames} from Wan2.2-VACE-Fun-A14B Q8 through ComfyUI (comfy-vace-clips' graph and settings)."""
    cv = load_script("comfy-vace-clips")
    s_switch = cv.switch_step(steps, 12.0, 0.875)
    names = {}
    for name, (canvases, masks) in jobs.items():
        names[name] = (f"panorama-wide-{name}-control.mkv", f"panorama-wide-{name}-mask.mkv")
        cv.write_ffv1(canvases, os.path.join(cc.INPUT_DIR, names[name][0]))
        cv.write_ffv1(masks, os.path.join(cc.INPUT_DIR, names[name][1]), gray=True)
    out = {}
    server = cc.start_server(port, log_path, cc.LOW_RAM)
    try:
        for name, (control, mask) in names.items():
            t0 = time.perf_counter()
            n = len(jobs[name][0])
            res = cv.run_two_phase(port, cv.vace_graph(control, mask, "", GEN_W, GEN_H, n, steps, s_switch, seed,
                                                       f"panorama/wide-{name}"), f"panorama/wide-{name}")
            out[name] = cc.saved_images(res, "20")
            print(f"a14b {name} side in {time.perf_counter() - t0:.0f} s", flush=True)
    finally:
        cc.stop_server(server)
    return out


def key_plates(frames, seed, port, log_path):
    """Per-side Qwen plates of the middle frame (make-wide-demo's still-shot recipe), {side: 1080x960}."""
    jobs = demo.prepare_sides(frames[len(frames) // 2], "movingkey")
    server = cc.start_server(port, log_path, cc.LOW_RAM)
    try:
        sides, _ = demo.run_sides(port, jobs, seed, "movingkey")
    finally:
        cc.stop_server(server)
    return sides


def run_scroll(frames, seed, port, log_path):
    """Wide frames from the scrolling plate on the uncut clip, plus its facts."""
    key = len(frames) // 2
    offsets, agree = scroll.camera_offsets(frames, key)
    left = int(np.ceil(max(0.0, offsets[:, 0].max())))
    right = int(np.ceil(max(0.0, -offsets[:, 0].min())))
    top = int(np.ceil(max(0.0, offsets[:, 1].max())))
    bottom = int(np.ceil(max(0.0, -offsets[:, 1].min())))
    w, h = 2 * SIDE + frames[0].shape[1], frames[0].shape[0]
    ew, eh = w + left + right, h + top + bottom
    pw, ph = -(-ew // 32) * 32, -(-eh // 32) * 32
    left += (pw - ew) // 2
    top += (ph - eh) // 2
    mosaic, count = scroll.build_mosaic(frames, offsets, SIDE, (left, top), (ph, pw))
    seen = count > 0
    canvas = np.full((ph, pw, 3), plates.GREY, np.uint8)
    canvas[seen] = mosaic[seen]
    sc = min(1.0, (scroll.MAX_PAINT_PIXELS / (ph * pw)) ** 0.5)
    small = cv2.resize(canvas, (max(32, int(pw * sc) // 32 * 32), max(32, int(ph * sc) // 32 * 32)),
                       interpolation=cv2.INTER_AREA)
    cv2.imwrite(os.path.join(cc.INPUT_DIR, "panorama-wide-scroll-canvas.png"), cv2.cvtColor(small, cv2.COLOR_RGB2BGR))
    cv2.imwrite(os.path.join(cc.INPUT_DIR, "panorama-wide-scroll-centre.png"), cv2.cvtColor(frames[key], cv2.COLOR_RGB2BGR))
    server = cc.start_server(port, log_path, cc.LOW_RAM)
    try:
        res = cc.run_graph(port, plates.qwen_graph("qwen-image-2.1-outpaint-v2.safetensors", "panorama-wide-scroll-canvas.png",
                                                   "panorama-wide-scroll-centre.png", seed, "panorama/wide-scroll"))
    finally:
        cc.stop_server(server)
    painted = cv2.resize(cc.saved_images(res, "12")[0], (pw, ph), interpolation=cv2.INTER_LANCZOS4)
    background = scroll.feather_keep(painted, mosaic, seen)
    wide = scroll.render(background, offsets, (left, top), frames, SIDE, (h, w))
    facts = {"travel_px": [round(float(x), 1) for x in offsets.max(0) - offsets.min(0)],
             "flow_agreement_median": round(float(np.median(agree[1:])), 3), "canvas": [pw, ph],
             "seen_share_of_canvas": round(float(seen.mean()), 3)}
    return wide, facts, background


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("film")
    ap.add_argument("out_dir")
    ap.add_argument("--at", type=float, required=True, help="start second of the moment")
    ap.add_argument("--recipe", required=True, choices=["scroll", "vace13", "vace13-key", "a14b", "a14b-key"])
    ap.add_argument("--steps", type=int, default=50)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--port", type=int, default=8189)
    a = ap.parse_args(argv)
    frames = demo.grab(a.film, a.at, FRAMES)
    odir = os.path.join(a.out_dir, time.strftime("%Y%m%d-%H%M%S") + "-" + a.recipe)
    os.makedirs(odir, exist_ok=True)
    log_path = os.path.join(odir, "comfy-server.log")
    info = {"start_s": a.at, "frames": len(frames), "recipe": a.recipe, "seed": a.seed}
    t0 = time.perf_counter()
    if a.recipe == "scroll":
        wide, facts, background = run_scroll(frames, a.seed, a.port, log_path)
        cv2.imwrite(os.path.join(odir, "background.png"), cv2.cvtColor(background, cv2.COLOR_RGB2BGR))
        info.update(facts)
    else:
        keys = key_plates(frames, a.seed, a.port, log_path) if a.recipe.endswith("-key") else {}
        jobs = {name: side_canvases(frames, name, keys.get(name)) for name in ("left", "right")}
        if a.recipe.startswith("vace13"):
            model = run_vace13(jobs, a.steps, a.seed)
        else:
            model = run_a14b(jobs, a.steps, a.seed, a.port, log_path)
        sides = {name: finish_side(model[name], frames, name) for name in ("left", "right")}
        wide = []
        for t, f in enumerate(frames):
            c = np.hstack([sides["left"][t], f, sides["right"][t]]).astype(np.float32)
            c = hs.live_seam(c, f, SIDE).clip(0, 255).round().astype(np.uint8)
            c[:, SIDE:SIDE + f.shape[1]] = f                          # the real picture is never the model's
            wide.append(c)
        band = SIDE // 4
        band_still = (wraps.still_steps([f[:, :band] for f in frames], 6, 0.002),
                      wraps.still_steps([f[:, -band:] for f in frames], 6, 0.002))
        wide = wraps.apply_freeze(wide, SIDE, band_still)
        for c, f in zip(wide, frames):
            c[:, SIDE:SIDE + f.shape[1]] = f
        info["band_still_share"] = [round(sum(b) / max(1, len(b) - 1), 3) for b in band_still]
    info["seconds"] = round(time.perf_counter() - t0, 1)
    hs.write_video(wide, os.path.join(odir, "wide-32x9.mp4"), 23.976)
    mid = len(wide) // 2
    cv2.imwrite(os.path.join(odir, "preview.png"),
                cv2.cvtColor(cv2.resize(wide[mid], (1920, 540), interpolation=cv2.INTER_AREA), cv2.COLOR_RGB2BGR))
    demo.write_spans(odir, wide, "")
    demo.write_compare(odir, frames, wide, [mid])
    hs.save_json(info, os.path.join(odir, "demo.json"))
    print(f"done: {odir} ({info['seconds']} s)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
