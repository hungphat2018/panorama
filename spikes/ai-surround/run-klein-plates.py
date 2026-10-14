"""Per-shot plates with FLUX.2-klein-4B on the hidden-sides test: one generated still per scene, timed and scored.

Two ways of asking the same model to redraw the hidden sides of the scene's middle frame:
  klein-green    the edit pipeline with fal's outpaint LoRA (Apache-2.0): the sides are painted pure green and the prompt
                 is the LoRA's own ("Fill the green spaces according to the image"); strength 1.1 as its card says
  klein-inpaint  the native masked fill (Flux2KleinInpaintPipeline): the sides are masked, no LoRA. The pipeline also
                 feeds the input image as a reference, and the editing model copies its black sides on bright scenes

The canvas is generated at --scale of full size (0.5: 960x540 for s = 0.5) and upscaled with Lanczos; the visible centre
is then pasted back untouched, so only the sides come from the model, as the performer would draw them. The text encoder
runs once on the CPU (the prompt never changes) so the GPU holds only the transformer and the VAE.

With --refs (green only), the scene's references (SCENES_DIR/<scene>/refs/, from pick-references.py: the cut-off
character in full, the same place from another angle) follow the green canvas as extra images; FLUX.2 reads every image
as its own reference (each at most 1 MP). Scenes without references are skipped. --prompt replaces the LoRA's prompt.

    python -I run-klein-plates.py SCENES_DIR OUT_DIR [--side 0.5] [--scale 0.5] [--steps 4] [--seed 7] [--only green,inpaint]
                                  [--suffix=-full] [--refs] [--prompt "..."]

A suffix starting with "-" needs the "=" form (argparse reads "--suffix -full" as a missing value).

Writes OUT_DIR/<fill><suffix>/<scene>/plate-s<side>.png (+ raw-s<side>.png, the model's whole canvas before the centre is
pasted back), OUT_DIR/scores-klein.json (scores on the plate frame only: a still has no flicker of its own) and
OUT_DIR/sheets/<scene>-klein-s<side>.png (truth, best baseline, every klein fill on disk).
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

MODEL = "black-forest-labs/FLUX.2-klein-4B"
BASE_MODEL = "black-forest-labs/FLUX.2-klein-base-4B"   # the non-distilled model the fal LoRA was trained on (fal serves it there)
LORA = "fal/flux-2-klein-4B-outpaint-lora"
LORA_FILE = "flux-outpaint-lora.safetensors"
GREEN_PROMPT = "Fill the green spaces according to the image"
INPAINT_PROMPT = "anime background, same art style, seamless continuation of the scene"


def round16(x):
    return max(16, int(round(x / 16.0)) * 16)


def middle_frame(scene_dir):
    frames = hs.read_frames(os.path.join(scene_dir, "clip.mkv"))
    return frames[len(frames) // 2]


def encode_prompt_on_cpu(pipe, prompt):
    """(prompt embeds, negative embeds or None): encoded once on the CPU, then the text encoder is dropped, so the GPU
    never holds it and the pipeline's execution device becomes the GPU (taken from the first component that still
    exists). The base (non-distilled) model uses classifier-free guidance and needs the empty prompt's embeds too."""
    pipe.text_encoder.to("cpu")
    def enc(p):
        with torch.no_grad():
            out = pipe.encode_prompt(prompt=p, device=torch.device("cpu"), num_images_per_prompt=1)
        return (out[0] if isinstance(out, (tuple, list)) else out).to("cuda", dtype=torch.bfloat16)
    embeds = enc(prompt)
    negative = None if pipe.config.is_distilled else enc("")
    pipe.text_encoder = None
    return embeds, negative


def load(kind, fp8_storage=True, base=False):
    from diffusers import Flux2KleinInpaintPipeline, Flux2KleinPipeline
    cls = Flux2KleinPipeline if kind == "green" else Flux2KleinInpaintPipeline
    pipe = cls.from_pretrained(BASE_MODEL if base else MODEL, torch_dtype=torch.bfloat16)
    if kind == "green":
        pipe.load_lora_weights(LORA, weight_name=LORA_FILE, adapter_name="outpaint")
        pipe.fuse_lora(adapter_names=["outpaint"], lora_scale=1.1)   # fold it in at the card's strength, so FP8 storage holds the merged weights
        pipe.unload_lora_weights()
    if fp8_storage:
        # Weights stored in FP8 (3.9 GB instead of 7.8), each layer computed in bf16: fits beside the desktop's VRAM use.
        pipe.transformer.enable_layerwise_casting(storage_dtype=torch.float8_e4m3fn, compute_dtype=torch.bfloat16)
    pipe.transformer.to("cuda")
    pipe.vae.to("cuda")
    return pipe


def load_refs(scene_dir):
    """The scene's reference images (PIL, RGB) in refs.json order, or [] when it has none."""
    path = os.path.join(scene_dir, "refs", "refs.json")
    if not os.path.isfile(path):
        return []
    entries = json.load(open(path, encoding="utf-8"))["refs"]
    return [Image.open(os.path.join(scene_dir, "refs", e["file"])).convert("RGB") for e in entries]


def run_one(pipe, kind, embeds, frame, s, scale, steps, seed, refs=(), guidance=4.0):
    """embeds = (prompt embeds, negative embeds or None) from encode_prompt_on_cpu; guidance applies to the base model."""
    embeds, negative = embeds
    truth, vis, side = hs.split(frame, s)
    h, w = truth.shape[:2]
    gh, gw = round16(h * scale), round16(w * scale)
    small_vis_w = int(round(vis.shape[1] * gw / w))
    small_side = (gw - small_vis_w) // 2
    vis_small = cv2.resize(vis, (small_vis_w, gh), interpolation=cv2.INTER_AREA)
    canvas = np.zeros((gh, gw, 3), np.uint8)
    canvas[:, small_side:small_side + small_vis_w] = vis_small
    gen = torch.Generator("cuda").manual_seed(seed)
    torch.cuda.synchronize()
    torch.cuda.reset_peak_memory_stats()
    t0 = time.perf_counter()
    if kind == "green":
        canvas[:, :small_side] = (0, 255, 0)
        canvas[:, small_side + small_vis_w:] = (0, 255, 0)
        out = pipe(image=[Image.fromarray(canvas)] + list(refs), prompt_embeds=embeds, negative_prompt_embeds=negative,
                   guidance_scale=guidance, height=gh, width=gw, num_inference_steps=steps, generator=gen).images[0]
    else:
        mask = np.zeros((gh, gw), np.uint8)
        mask[:, :small_side] = 255
        mask[:, small_side + small_vis_w:] = 255
        out = pipe(image=Image.fromarray(canvas), mask_image=Image.fromarray(mask), prompt_embeds=embeds,
                   negative_prompt_embeds=negative, guidance_scale=guidance, height=gh, width=gw, strength=1.0,
                   num_inference_steps=steps, generator=gen).images[0]
    torch.cuda.synchronize()
    secs = time.perf_counter() - t0
    peak = torch.cuda.max_memory_allocated() / 2**30
    raw = np.asarray(out.convert("RGB"))
    big = cv2.resize(raw, (w, h), interpolation=cv2.INTER_LANCZOS4)
    big[:, side:side + vis.shape[1]] = vis                    # the real picture is never the model's
    return truth, big, raw, side, secs, peak


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("scenes_dir")
    ap.add_argument("out_dir")
    ap.add_argument("--side", type=float, default=0.5)
    ap.add_argument("--scale", type=float, default=0.5)
    ap.add_argument("--steps", type=int, default=None, help="default 4 (distilled) or 50 (--base)")
    ap.add_argument("--base", action="store_true",
                    help="klein-base-4B (non-distilled, the fal LoRA's own base): CFG --guidance, fills named klein-base-*")
    ap.add_argument("--guidance", type=float, default=4.0, help="classifier-free guidance, --base only")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--only", default="green,inpaint")
    ap.add_argument("--suffix", default="", help="added to each fill's name (e.g. =-full), so variants sit side by side")
    ap.add_argument("--refs", action="store_true", help="green only: add the scene's reference images; skip scenes without")
    ap.add_argument("--prompt", default="", help="replaces the green LoRA's prompt")
    a = ap.parse_args(argv)
    if a.refs and a.only != "green":
        raise SystemExit("--refs works with --only green (the inpaint pipeline takes one image)")
    a.steps = a.steps or (50 if a.base else 4)

    scenes = [d for d in sorted(os.listdir(a.scenes_dir)) if os.path.isfile(os.path.join(a.scenes_dir, d, "clip.mkv"))]
    refs = {sc: load_refs(os.path.join(a.scenes_dir, sc)) if a.refs else [] for sc in scenes}
    if a.refs:
        scenes = [sc for sc in scenes if refs[sc]]
    frames = {sc: middle_frame(os.path.join(a.scenes_dir, sc)) for sc in scenes}
    scores_path = os.path.join(a.out_dir, "scores-klein.json")
    scores = json.load(open(scores_path, encoding="utf-8")) if os.path.isfile(scores_path) else {}
    os.makedirs(os.path.join(a.out_dir, "sheets"), exist_ok=True)
    for kind in a.only.split(","):
        name = f"klein-{'base-' if a.base else ''}{kind}{a.suffix}"
        t_load = time.perf_counter()
        pipe = load(kind, base=a.base)
        embeds = encode_prompt_on_cpu(pipe, (a.prompt or GREEN_PROMPT) if kind == "green" else INPAINT_PROMPT)
        print(f"{name}: loaded + prompt in {time.perf_counter() - t_load:.1f} s")
        for i, sc in enumerate(scenes):
            truth, big, raw, side, secs, peak = run_one(pipe, kind, embeds, frames[sc], a.side, a.scale, a.steps, a.seed,
                                                        refs[sc], a.guidance)
            sc_scores = hs.score([truth], [big], side)
            sc_scores.update({"seconds": round(secs, 2), "peak_vram_gb": round(peak, 2), "first_call": i == 0,
                              "refs": len(refs[sc]), "prompt": a.prompt or None, "steps": a.steps,
                              "guidance": a.guidance if a.base else None})
            scores.setdefault(name, {}).setdefault(sc, {})[f"s{a.side}"] = sc_scores
            odir = os.path.join(a.out_dir, name, sc)
            os.makedirs(odir, exist_ok=True)
            cv2.imwrite(os.path.join(odir, f"plate-s{a.side}.png"), cv2.cvtColor(big, cv2.COLOR_RGB2BGR))
            # The model's whole canvas at generation size, its own redraw of the centre included: seam colour matching reads it.
            cv2.imwrite(os.path.join(odir, f"raw-s{a.side}.png"), cv2.cvtColor(raw, cv2.COLOR_RGB2BGR))
            print(f"  {sc:18s} {secs:6.2f} s  peak {peak:5.2f} GB  {sc_scores}")
        del pipe
        torch.cuda.empty_cache()
    hs.save_json(scores, scores_path)

    # Sheets: truth, best non-AI fill (blur-extend, from run-baseline-fills), then every klein fill on disk, this run's
    # and earlier ones (scores-klein.json names them), so variants run separately still sit side by side.
    for sc in scenes:
        truth, _, side = hs.split(frames[sc], a.side)
        rows, labels = [hs.mark_seams(truth, side)], [f"{sc}  s={a.side}  TRUTH"]
        base = os.path.join(a.out_dir, "blur-extend", sc, f"s{a.side}.mp4")
        if os.path.isfile(base):
            bf = hs.read_frames(base)
            rows.append(hs.mark_seams(bf[len(bf) // 2], side))
            labels.append("blur-extend (best non-AI)")
        for name in sorted(scores):
            plate = os.path.join(a.out_dir, name, sc, f"plate-s{a.side}.png")
            s_ = scores[name].get(sc, {}).get(f"s{a.side}")
            if s_ and os.path.isfile(plate):
                rows.append(hs.mark_seams(cv2.cvtColor(cv2.imread(plate), cv2.COLOR_BGR2RGB), side))
                labels.append(f"{name}  {s_['seconds']} s  near {s_['err_near']}  far {s_['err_far']}  seam x{s_['seam_step']}")
        hs.compose_sheet(rows, labels, os.path.join(a.out_dir, "sheets", f"{sc}-klein-s{a.side}.png"), width=1280)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
