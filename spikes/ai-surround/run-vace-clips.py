"""Video outpainting with Wan2.1-VACE-1.3B on the hidden-sides test: the sides of a whole clip, timed and scored for flicker.

VACE takes the clip with the sides painted grey plus a mask (white = generate, black = keep) and redraws the masked part
for every frame at once, so its sides can move with the picture; a per-shot plate cannot. The canvas is generated at
--width x --height (848x480 by default, the 1.3B model's training size) and upscaled; the visible centre is pasted back
untouched. Frames: the first --frames of the clip, cut to 4k + 1 (the model's rule). The text encoder (umT5-XXL, 11 GB)
runs once on the CPU and is dropped. Defaults are the model's quality settings (50 steps, CFG 5, shift 3 at 480p).
Two versions are scored: vace-1.3b as generated, and vace-1.3b-fit with one colour matrix per clip that maps the model's
own redraw of the centre onto the real centre (hidden_sides.colour_fit), applied to the sides.

With --key-plate <fill>, that fill's still plate for the clip's middle frame (colour-fitted) is handed to VACE as a fully
kept frame at that index, so the whole clip's sides grow from it (HL-OutPaint's global keyframe, done with VACE's masks).

With --refs, the scene's references (SCENES_DIR/<scene>/refs/, from pick-references.py: the cut-off character in full,
the same place from another angle) go in as VACE reference images: each is fitted onto a white canvas of the generation
size and encoded as an extra frame in front of the clip, which the model reads but does not output. Scenes without
references are skipped.

    python -I run-vace-clips.py SCENES_DIR OUT_DIR [--scenes held-dialogue,edge-person-still] [--side 0.5] [--frames 81]
                                [--steps 50] [--guidance 5.0] [--seed 7] [--key-plate klein-green] [--refs]

Writes OUT_DIR/<version>/<scene>/s<side>.mp4 (+ s<side>-with-truth.mp4, truth above candidate), OUT_DIR/scores-vace.json
and OUT_DIR/sheets/<scene>-vace[-key][-refs]-s<side>.png. Versions: vace-1.3b[-key-<fill>][-refs] and the same with -fit.
"""
import argparse
import json
import os
import sys
import time

import cv2
import numpy as np
import torch
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # python -I leaves the script's folder off sys.path
import hidden_sides as hs  # noqa: E402

MODEL = "Wan-AI/Wan2.1-VACE-1.3B-diffusers"
PROMPT = "anime, 2D hand-drawn animation, flat colours, clean line art, the scene continues naturally to the left and right"
NEGATIVE = ("photorealistic, 3D render, blurry, low quality, distorted, watermark, text, subtitles, extra characters, "
            "duplicated face, frame border, black bars")


def encode_on_cpu(pipe, prompt, negative):
    pipe.text_encoder.to("cpu")
    with torch.no_grad():
        pe, ne = pipe.encode_prompt(prompt=prompt, negative_prompt=negative, do_classifier_free_guidance=True,
                                    max_sequence_length=512, device=torch.device("cpu"))   # 512: the pipeline's own call and Wan's training pad to it
    pipe.text_encoder = None                                   # never on the GPU; the execution device becomes CUDA
    return pe.to("cuda", torch.bfloat16), ne.to("cuda", torch.bfloat16)


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("scenes_dir")
    ap.add_argument("out_dir")
    ap.add_argument("--scenes", default="held-dialogue,edge-person-still,edge-head-closeup,pan-horizontal")
    ap.add_argument("--side", type=float, default=0.5)
    ap.add_argument("--frames", type=int, default=81)
    ap.add_argument("--width", type=int, default=848)
    ap.add_argument("--height", type=int, default=480)
    ap.add_argument("--steps", type=int, default=50)
    ap.add_argument("--guidance", type=float, default=5.0)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--key-plate", default="",
                    help="fill whose plate (OUT_DIR/<fill>/<scene>/raw-s<side>.png, drawn from the clip's middle frame) is "
                         "colour-fitted and given to VACE as a fully kept key frame there: HL-OutPaint's global keyframe "
                         "idea through VACE's own masks, no training")
    ap.add_argument("--refs", action="store_true", help="add the scene's reference images; skip scenes without")
    a = ap.parse_args(argv)
    base = "vace-1.3b" + (f"-key-{a.key_plate}" if a.key_plate else "") + ("-refs" if a.refs else "")

    from diffusers import AutoencoderKLWan, WanVACEPipeline
    from diffusers.schedulers.scheduling_unipc_multistep import UniPCMultistepScheduler
    t_load = time.perf_counter()
    vae = AutoencoderKLWan.from_pretrained(MODEL, subfolder="vae", torch_dtype=torch.float32)
    pipe = WanVACEPipeline.from_pretrained(MODEL, vae=vae, torch_dtype=torch.bfloat16)
    pipe.scheduler = UniPCMultistepScheduler.from_config(pipe.scheduler.config, flow_shift=3.0)
    pe, ne = encode_on_cpu(pipe, PROMPT, NEGATIVE)
    pipe.transformer.to("cuda")
    pipe.vae.to("cuda")
    pipe.vae.enable_tiling()
    print(f"loaded + prompt in {time.perf_counter() - t_load:.1f} s")

    scores_path = os.path.join(a.out_dir, "scores-vace.json")
    scores = json.load(open(scores_path, encoding="utf-8")) if os.path.isfile(scores_path) else {}
    os.makedirs(os.path.join(a.out_dir, "sheets"), exist_ok=True)
    for sc in a.scenes.split(","):
        sdir = os.path.join(a.scenes_dir, sc)
        meta = json.load(open(os.path.join(sdir, "scene.json"), encoding="utf-8"))
        refs = None
        if a.refs:
            ref_json = os.path.join(sdir, "refs", "refs.json")
            if not os.path.isfile(ref_json):
                print(f"skip {sc}: no references")
                continue
            refs = [Image.open(os.path.join(sdir, "refs", e["file"])).convert("RGB")
                    for e in json.load(open(ref_json, encoding="utf-8"))["refs"]]
        frames = hs.read_frames(os.path.join(sdir, "clip.mkv"))
        key = len(frames) // 2                                    # the frame run-klein-plates drew its plates from
        frames = frames[:a.frames]
        n = len(frames) - (len(frames) - 1) % 4                   # 4k + 1 frames
        frames = frames[:n]
        parts = [hs.split(f, a.side) for f in frames]
        truth = [p[0] for p in parts]
        side = parts[0][2]
        h, w = truth[0].shape[:2]
        vis_w = parts[0][1].shape[1]
        small_vis = int(round(vis_w * a.width / w / 2)) * 2
        small_side = (a.width - small_vis) // 2
        key_canvas = None
        if a.key_plate:
            if key >= n:
                raise SystemExit(f"{sc}: key frame {key} is outside the {n} generated frames")
            raw = cv2.cvtColor(cv2.imread(os.path.join(a.out_dir, a.key_plate, sc, f"raw-s{a.side}.png")), cv2.COLOR_BGR2RGB)
            plate = cv2.resize(raw, (w, h), interpolation=cv2.INTER_LANCZOS4)
            M = hs.colour_fit(plate[:, side:side + vis_w], parts[key][1])
            plate = np.clip(hs.apply_fit(plate, M), 0, 255).round().astype(np.uint8)
            plate[:, side:side + vis_w] = parts[key][1]
            key_canvas = cv2.resize(plate, (a.width, a.height), interpolation=cv2.INTER_AREA)
        video, masks = [], []
        for i, (_, vis, _) in enumerate(parts):
            if key_canvas is not None and i == key:
                video.append(Image.fromarray(key_canvas))
                masks.append(Image.fromarray(np.zeros((a.height, a.width), np.uint8)))   # the whole key frame is kept
                continue
            canvas = np.full((a.height, a.width, 3), 128, np.uint8)
            canvas[:, small_side:small_side + small_vis] = cv2.resize(vis, (small_vis, a.height), interpolation=cv2.INTER_AREA)
            m = np.full((a.height, a.width), 255, np.uint8)
            m[:, small_side:small_side + small_vis] = 0
            video.append(Image.fromarray(canvas))
            masks.append(Image.fromarray(m))
        torch.cuda.synchronize()
        torch.cuda.reset_peak_memory_stats()
        t0 = time.perf_counter()
        out = pipe(video=video, mask=masks, reference_images=refs, prompt_embeds=pe, negative_prompt_embeds=ne,
                   height=a.height, width=a.width, num_frames=n, num_inference_steps=a.steps, guidance_scale=a.guidance,
                   generator=torch.Generator("cuda").manual_seed(a.seed), output_type="np").frames[0]
        torch.cuda.synchronize()
        secs = time.perf_counter() - t0
        peak = torch.cuda.max_memory_allocated() / 2**30
        # The model's whole canvas per frame: its own redraw of the centre gives the colour fit of the -fit version, then
        # the real centre is pasted back over both versions (hidden_sides.finish_video_fill).
        model = [(np.clip(f, 0, 1) * 255).round().astype(np.uint8) for f in out]
        info = {"seconds": round(secs, 1), "seconds_per_frame": round(secs / n, 2), "frames": n,
                "peak_vram_gb": round(peak, 2), "gen_size": [a.width, a.height], "steps": a.steps}
        sheet = f"{sc}-vace{'-key' if a.key_plate else ''}{'-refs' if a.refs else ''}-s{a.side}.png"
        hs.finish_video_fill(model, parts, base, a.out_dir, sc, meta["fps"], a.side, info, scores, sheet)
        hs.save_json(scores, scores_path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
