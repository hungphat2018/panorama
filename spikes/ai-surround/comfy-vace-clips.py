"""Video outpainting with Wan2.2-VACE-Fun-A14B (Q8_0 GGUF, two experts) through ComfyUI, on the hidden-sides test.

The same test as run-vace-clips.py (VACE-1.3B in diffusers), with the 14B model run the way ComfyUI runs it on a 12 GB
card: the clip with grey sides and a mask (white = generate) go in as lossless FFV1 videos, WanVaceToVideo builds the
VACE context, the high-noise expert samples while sigma >= 0.875 and the low-noise expert finishes. Settings are the
official VACE-Fun 2.2 mask example's (VideoX-Fun examples/wan2.2_vace_fun/predict_v2v_mask.py): 50 steps, CFG 5, flow
shift 12, boundary 0.875; size 848x480 like the 1.3B run. Each clip runs as two prompts, one per expert, on a server
started with --cache-none (run_two_phase: one prompt held both experts and the text encoder, 54 GB on 32 GB of RAM).
--key-plate <fill> keeps that fill's colour-fitted plate as a
fully kept frame at the clip's middle (HL-OutPaint's global keyframe); --refs adds the scene's first reference image
(WanVaceToVideo takes one). Scoring, the -fit version and the outputs are hidden_sides.finish_video_fill's, as for 1.3B.

    python -I comfy-vace-clips.py SCENES_DIR OUT_DIR [--scenes held-dialogue,edge-person-still] [--key-plate klein-green]
                                  [--refs] [--frames 81] [--steps 50] [--port 8189]

Writes OUT_DIR/vace-fun-a14b-q8[-key-<fill>][-refs][-fit]/<scene>/s<side>.mp4 (+ -with-truth.mp4), OUT_DIR/scores-vace.json
and OUT_DIR/sheets/<scene>-vace-a14b[-key][-refs]-s<side>.png.
"""
import argparse
import copy
import json
import os
import shutil
import subprocess
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # python -I leaves the script's folder off sys.path
import comfy_client as cc  # noqa: E402
import hidden_sides as hs  # noqa: E402

HIGH = "Wan2.2-VACE-Fun-A14B-high-noise-Q8_0.gguf"
LOW = "Wan2.2-VACE-Fun-A14B-low-noise-Q8_0.gguf"
PROMPT = "anime, 2D hand-drawn animation, flat colours, clean line art, the scene continues naturally to the left and right"
NEGATIVE = ("photorealistic, 3D render, blurry, low quality, distorted, watermark, text, subtitles, extra characters, "
            "duplicated face, frame border, black bars")


def switch_step(steps, shift, boundary):
    """First step of the low-noise expert: ComfyUI's 'simple' schedule on a shifted flow, sigma = s t / (1 + (s - 1) t)."""
    t = 1.0 - np.arange(steps) / steps
    sigma = shift * t / (1 + (shift - 1) * t)
    return int((sigma >= boundary).sum())


def write_ffv1(frames, path, gray=False):
    """Frames (RGB uint8, or one channel with gray=True) to a lossless FFV1 .mkv that ComfyUI's LoadVideo reads."""
    h, w = frames[0].shape[:2]
    pix_in, pix_out = ("gray", "gray") if gray else ("rgb24", "bgr0")
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", pix_in, "-s", f"{w}x{h}",
           "-r", "16", "-i", "-", "-c:v", "ffv1", "-pix_fmt", pix_out, path]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for f in frames:
        p.stdin.write(np.ascontiguousarray(f).tobytes())
    p.stdin.close()
    if p.wait() != 0:
        raise RuntimeError(f"ffmpeg failed writing {path}")


def vace_graph(control, mask, ref, w, h, n, steps, s_switch, seed, prefix):
    g = {
        "1": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": HIGH}},
        "2": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": LOW}},
        "3": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["1", 0], "shift": 12.0}},
        "4": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["2", 0], "shift": 12.0}},
        "5": {"class_type": "CLIPLoader", "inputs": {"clip_name": "umt5_xxl_fp16.safetensors", "type": "wan"}},
        "6": {"class_type": "VAELoader", "inputs": {"vae_name": "wan_2.1_vae.safetensors"}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["5", 0], "text": PROMPT}},
        "8": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["5", 0], "text": NEGATIVE}},
        "9": {"class_type": "LoadVideo", "inputs": {"file": control}},
        "10": {"class_type": "GetVideoComponents", "inputs": {"video": ["9", 0]}},
        "11": {"class_type": "LoadVideo", "inputs": {"file": mask}},
        "12": {"class_type": "GetVideoComponents", "inputs": {"video": ["11", 0]}},
        "13": {"class_type": "ImageToMask", "inputs": {"image": ["12", 0], "channel": "red"}},
        "15": {"class_type": "WanVaceToVideo", "inputs": {"positive": ["7", 0], "negative": ["8", 0], "vae": ["6", 0],
                                                          "width": w, "height": h, "length": n, "batch_size": 1,
                                                          "strength": 1.0, "control_video": ["10", 0], "control_masks": ["13", 0]}},
        "16": {"class_type": "KSamplerAdvanced", "inputs": {"model": ["3", 0], "add_noise": "enable", "noise_seed": seed,
                                                            "steps": steps, "cfg": 5.0, "sampler_name": "euler", "scheduler": "simple",
                                                            "positive": ["15", 0], "negative": ["15", 1], "latent_image": ["15", 2],
                                                            "start_at_step": 0, "end_at_step": s_switch,
                                                            "return_with_leftover_noise": "enable"}},
        "17": {"class_type": "KSamplerAdvanced", "inputs": {"model": ["4", 0], "add_noise": "disable", "noise_seed": seed,
                                                            "steps": steps, "cfg": 5.0, "sampler_name": "euler", "scheduler": "simple",
                                                            "positive": ["15", 0], "negative": ["15", 1], "latent_image": ["16", 0],
                                                            "start_at_step": s_switch, "end_at_step": 10000,
                                                            "return_with_leftover_noise": "disable"}},
        "18": {"class_type": "TrimVideoLatent", "inputs": {"samples": ["17", 0], "trim_amount": ["15", 3]}},
        "19": {"class_type": "VAEDecode", "inputs": {"samples": ["18", 0], "vae": ["6", 0]}},
        "20": {"class_type": "SaveImage", "inputs": {"images": ["19", 0], "filename_prefix": prefix}},
    }
    if ref:
        g["14"] = {"class_type": "LoadImage", "inputs": {"image": ref}}
        g["15"]["inputs"]["reference_image"] = ["14", 0]
    return g


SHARED = {"5", "6", "7", "8", "9", "10", "11", "12", "13", "14", "15"}      # text, VAE, inputs, VACE context
HIGH_NODES = SHARED | {"1", "3", "16"}
LOW_NODES = SHARED | {"2", "4", "17", "18", "19", "20"}


def run_two_phase(port, graph, prefix):
    """vace_graph run one expert at a time, so only one 18.7 GB expert is in RAM. As one prompt, both experts plus the
    umT5 text encoder stayed loaded: 54 GB committed on this 32 GB laptop, the pagefile grew to 40 GB and the system ran
    critically low on memory (2026-10-13 00:20). Phase 1: the high-noise expert's steps, the latent saved with its
    leftover noise (SaveLatent keeps float32); everything freed; phase 2: the low-noise expert continues from that latent
    and decodes. Same nodes, seed, steps and switch as the one-prompt graph. Returns phase 2's outputs."""
    p1 = {k: copy.deepcopy(v) for k, v in graph.items() if k in HIGH_NODES}
    p1["21"] = {"class_type": "SaveLatent", "inputs": {"samples": ["16", 0], "filename_prefix": f"{prefix}-high"}}
    saved = cc.run_graph(port, p1)["21"]["latents"][0]
    name = f"{os.path.basename(prefix)}-high.latent"
    shutil.copyfile(os.path.join(cc.OUTPUT_DIR, saved.get("subfolder", ""), saved["filename"]), os.path.join(cc.INPUT_DIR, name))
    cc.free(port)
    p2 = {k: copy.deepcopy(v) for k, v in graph.items() if k in LOW_NODES}
    p2["22"] = {"class_type": "LoadLatent", "inputs": {"latent": name}}
    p2["17"]["inputs"]["latent_image"] = ["22", 0]
    out = cc.run_graph(port, p2)
    cc.free(port)
    return out


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("scenes_dir")
    ap.add_argument("out_dir")
    ap.add_argument("--scenes", default="held-dialogue,edge-person-still,edge-head-closeup")
    ap.add_argument("--side", type=float, default=0.5)
    ap.add_argument("--frames", type=int, default=81)
    ap.add_argument("--width", type=int, default=848)
    ap.add_argument("--height", type=int, default=480)
    ap.add_argument("--steps", type=int, default=50)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--key-plate", default="", help="fill whose plate (OUT_DIR/<fill>/<scene>/raw-s<side>.png) is kept as the middle frame")
    ap.add_argument("--refs", action="store_true", help="add the scene's first reference image; skip scenes without")
    ap.add_argument("--port", type=int, default=8189)
    a = ap.parse_args(argv)
    base = "vace-fun-a14b-q8" + (f"-key-{a.key_plate}" if a.key_plate else "") + ("-refs" if a.refs else "")
    s_switch = switch_step(a.steps, 12.0, 0.875)

    # Every input file is written before the server starts, so its file lists include them.
    jobs = []
    for sc in a.scenes.split(","):
        sdir = os.path.join(a.scenes_dir, sc)
        meta = json.load(open(os.path.join(sdir, "scene.json"), encoding="utf-8"))
        ref_name = ""
        if a.refs:
            ref_json = os.path.join(sdir, "refs", "refs.json")
            if not os.path.isfile(ref_json):
                print(f"skip {sc}: no references")
                continue
            first = json.load(open(ref_json, encoding="utf-8"))["refs"][0]["file"]
            ref_name = f"panorama-vace-{sc}-ref.png"
            img = cv2.imread(os.path.join(sdir, "refs", first))
            cv2.imwrite(os.path.join(cc.INPUT_DIR, ref_name), cv2.resize(img, (a.width, a.height), interpolation=cv2.INTER_AREA))
        frames = hs.read_frames(os.path.join(sdir, "clip.mkv"))
        key = len(frames) // 2                                    # the frame the plates were drawn from
        frames = frames[:a.frames]
        n = len(frames) - (len(frames) - 1) % 4                   # 4k + 1 frames
        parts = [hs.split(f, a.side) for f in frames[:n]]
        side, vis_w = parts[0][2], parts[0][1].shape[1]
        h, w = parts[0][0].shape[:2]
        small_vis = int(round(vis_w * a.width / w / 2)) * 2
        small_side = (a.width - small_vis) // 2
        if a.key_plate and key >= n:
            raise SystemExit(f"{sc}: key frame {key} is outside the {n} generated frames")
        canvases, masks = [], []
        for i, (_, vis, _) in enumerate(parts):
            if a.key_plate and i == key:
                raw = cv2.cvtColor(cv2.imread(os.path.join(a.out_dir, a.key_plate, sc, f"raw-s{a.side}.png")), cv2.COLOR_BGR2RGB)
                plate = cv2.resize(raw, (w, h), interpolation=cv2.INTER_LANCZOS4)
                M = hs.colour_fit(plate[:, side:side + vis_w], vis)
                plate = np.clip(hs.apply_fit(plate, M), 0, 255).round().astype(np.uint8)
                plate[:, side:side + vis_w] = vis
                canvases.append(cv2.resize(plate, (a.width, a.height), interpolation=cv2.INTER_AREA))
                masks.append(np.zeros((a.height, a.width), np.uint8))    # the whole key frame is kept
                continue
            canvas = np.full((a.height, a.width, 3), 128, np.uint8)
            canvas[:, small_side:small_side + small_vis] = cv2.resize(vis, (small_vis, a.height), interpolation=cv2.INTER_AREA)
            m = np.full((a.height, a.width), 255, np.uint8)
            m[:, small_side:small_side + small_vis] = 0
            canvases.append(canvas)
            masks.append(m)
        control, mask = f"panorama-vace-{sc}-control.mkv", f"panorama-vace-{sc}-mask.mkv"
        write_ffv1(canvases, os.path.join(cc.INPUT_DIR, control))
        write_ffv1(masks, os.path.join(cc.INPUT_DIR, mask), gray=True)
        jobs.append((sc, meta, parts, n, control, mask, ref_name))

    scores_path = os.path.join(a.out_dir, "scores-vace.json")
    scores = json.load(open(scores_path, encoding="utf-8")) if os.path.isfile(scores_path) else {}
    server = cc.start_server(a.port, os.path.join(a.out_dir, "comfy-server-vace.log"), cc.LOW_RAM)
    try:
        for sc, meta, parts, n, control, mask, ref_name in jobs:
            graph = vace_graph(control, mask, ref_name, a.width, a.height, n, a.steps, s_switch, a.seed, f"panorama/{base}-{sc}")
            t0 = time.perf_counter()
            outputs = run_two_phase(a.port, graph, f"panorama/{base}-{sc}")
            secs = time.perf_counter() - t0
            model = cc.saved_images(outputs, "20")
            if len(model) != n:
                raise RuntimeError(f"{sc}: {len(model)} frames back, {n} expected")
            info = {"seconds": round(secs, 1), "seconds_per_frame": round(secs / n, 2), "frames": n, "steps": a.steps,
                    "switch_step": s_switch, "gen_size": [a.width, a.height], "runner": "comfyui-0.39.0 + ComfyUI-GGUF"}
            sheet = f"{sc}-vace-a14b{'-key' if a.key_plate else ''}{'-refs' if a.refs else ''}-s{a.side}.png"
            hs.finish_video_fill(model, parts, base, a.out_dir, sc, meta["fps"], a.side, info, scores, sheet)
            hs.save_json(scores, scores_path)
            print(f"{base} {sc}: {secs:.0f} s ({secs / n:.1f} s/frame)", flush=True)
    finally:
        cc.stop_server(server)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
