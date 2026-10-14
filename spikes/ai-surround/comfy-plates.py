"""Per-shot plates from image models run in ComfyUI (role R3), scored exactly like run-klein-plates.py.

ComfyUI (pinned portable v0.39.0 in tools/ComfyUI_windows_portable, models in tools/models/comfy via its
extra_model_paths.yaml) runs the models the way their authors publish them: Qwen-Image-2.1 with ausboss's outpaint LoRA
(ComfyUI key format), Anima-Base with kohya's inpaint ControlNet-LLLite. This script starts a local ComfyUI server
(127.0.0.1 only) unless one already answers on --port, sends one API workflow per scene, reads the picture back and treats
it like a klein plate: the model's whole canvas is saved as raw-s<side>.png, the real centre is pasted back, and the plate
is scored against the hidden truth. A server it started is stopped at the end.

Recipes (one still per scene, drawn from the clip's middle frame, at the canvas's own size; 4 grey rows top and bottom
round 1080 up to 1088 for the models' /32 and /16 grids and are cropped off again):
  qwen21-outpaint-v2, -v1  sides flat grey #808080 (the LoRA's input). Prompt: the LoRA's instruction + " Scene: " + a
                           caption of the visible picture written by the same Qwen3-VL-8B text encoder (TextGenerate,
                           greedy). 25 steps, CFG 1, euler/simple (the LoRA card). The caption is kept in captions.json.
  anima-lllite             Anima-Base v1.0 + inpaint LLLite v2 (the node zeroes the masked RGB itself and adds the mask,
                           white = fill) and the same mask on the latent: img2img with a mask, kohya's own sample
                           workflow (30 steps, CFG 4, er_sde/simple, denoise 0.8). The sides start from the non-AI
                           blur-extend fill, because img2img keeps 20% of the input there: with black sides (first run,
                           2026-10-12 19:00) the model drew dark sides with ghost copies of the picture. Anime tags + the
                           scene's caption from captions.json when a Qwen run wrote one.

    python -I comfy-plates.py SCENES_DIR OUT_DIR --recipes qwen21-outpaint-v2[,qwen21-outpaint-v1,anima-lllite]
                              [--side 0.5] [--seed 7] [--port 8189] [--scenes a,b]

Writes OUT_DIR/<recipe>/<scene>/{plate,raw}-s<side>.png, scores into OUT_DIR/scores-klein.json (the plate scores every
plate sheet and seam-match read), OUT_DIR/captions.json, OUT_DIR/sheets/<scene>-painters-s<side>.png and the server log
OUT_DIR/comfy-server.log.
"""
import argparse
import json
import os
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # python -I leaves the script's folder off sys.path
import comfy_client as cc  # noqa: E402
import hidden_sides as hs  # noqa: E402

INSTRUCTION = ("Outpaint the image: replace the solid gray areas with a seamless continuation of the scene, keeping the "
               "existing picture unchanged.")
CAPTION_ASK = ("Describe this anime frame in two or three sentences: the place, the people and what they wear, the art "
               "style, the colours and the light. Do not describe any text.")
ANIMA_POS = "masterpiece, best quality, score_7, safe, anime screenshot, the scene continues to the left and right, "
ANIMA_NEG = ("worst quality, low quality, score_1, score_2, score_3, artist name, blurry, jpeg artifacts, "
             "chromatic aberration, text, watermark, frame border")
PAD = 4                                                           # rows added top and bottom: 1080 -> 1088
GREY = 128                                                        # #808080, the outpaint LoRA's fill


def qwen_graph(lora, canvas, centre, seed, prefix):
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "qwen_image_2.1_bf16.safetensors", "weight_dtype": "default"}},
        "2": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["1", 0], "lora_name": lora, "strength_model": 1.0}},
        "3": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen3vl_8b_bf16.safetensors", "type": "qwen_image"}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": "qwen_image_2.1_vae_bf16.safetensors"}},
        "5": {"class_type": "LoadImage", "inputs": {"image": canvas}},
        "6": {"class_type": "LoadImage", "inputs": {"image": centre}},
        "7": {"class_type": "TextGenerate", "inputs": {"clip": ["3", 0], "prompt": CAPTION_ASK, "image": ["6", 0],
                                                        "max_length": 160, "sampling_mode": "off"}},
        "8": {"class_type": "StringConcatenate", "inputs": {"string_a": INSTRUCTION, "string_b": ["7", 0], "delimiter": " Scene: "}},
        "9": {"class_type": "TextEncodeQwenImage21", "inputs": {"clip": ["3", 0], "prompt": ["8", 0], "negative_prompt": "",
                                                                "vae": ["4", 0], "resolution": 0, "images.image_1": ["5", 0]}},
        "10": {"class_type": "KSampler", "inputs": {"model": ["2", 0], "positive": ["9", 0], "negative": ["9", 1],
                                                    "latent_image": ["9", 2], "seed": seed, "steps": 25, "cfg": 1.0,
                                                    "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
        "11": {"class_type": "VAEDecode", "inputs": {"samples": ["10", 0], "vae": ["4", 0]}},
        "12": {"class_type": "SaveImage", "inputs": {"images": ["11", 0], "filename_prefix": prefix}},
        "13": {"class_type": "PreviewAny", "inputs": {"source": ["7", 0]}},
    }


def anima_graph(canvas, mask, prompt, seed, prefix):
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "anima-base-v1.0.safetensors", "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen_3_06b_base.safetensors", "type": "stable_diffusion"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "qwen_image_vae.safetensors"}},
        "4": {"class_type": "LoadImage", "inputs": {"image": canvas}},
        "5": {"class_type": "LoadImageMask", "inputs": {"image": mask, "channel": "red"}},
        "6": {"class_type": "AnimaLLLiteApply_sdscripts", "inputs": {"model": ["1", 0], "lllite_name": "anima-lllite-inpainting-v2.safetensors",
                                                                     "image": ["4", 0], "strength": 1.0, "start_percent": 0.0,
                                                                     "end_percent": 1.0, "preserve_wrapper": True, "mask": ["5", 0]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": prompt}},
        "8": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": ANIMA_NEG}},
        "9": {"class_type": "VAEEncode", "inputs": {"pixels": ["4", 0], "vae": ["3", 0]}},
        "10": {"class_type": "SetLatentNoiseMask", "inputs": {"samples": ["9", 0], "mask": ["5", 0]}},
        "11": {"class_type": "KSampler", "inputs": {"model": ["6", 0], "positive": ["7", 0], "negative": ["8", 0],
                                                    "latent_image": ["10", 0], "seed": seed, "steps": 30, "cfg": 4.0,
                                                    "sampler_name": "er_sde", "scheduler": "simple", "denoise": 0.8}},
        "12": {"class_type": "VAEDecode", "inputs": {"samples": ["11", 0], "vae": ["3", 0]}},
        "13": {"class_type": "SaveImage", "inputs": {"images": ["12", 0], "filename_prefix": prefix}},
    }


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("scenes_dir")
    ap.add_argument("out_dir")
    ap.add_argument("--recipes", default="qwen21-outpaint-v2")
    ap.add_argument("--side", type=float, default=0.5)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--port", type=int, default=8189)
    ap.add_argument("--scenes", default="", help="comma-separated subset (default: every scene)")
    a = ap.parse_args(argv)
    loras = {"qwen21-outpaint-v2": "qwen-image-2.1-outpaint-v2.safetensors", "qwen21-outpaint-v1": "qwen-image-2.1-outpaint.safetensors"}
    recipes = a.recipes.split(",")
    unknown = [r for r in recipes if r not in loras and r != "anima-lllite"]
    if unknown:
        raise SystemExit(f"unknown recipes {unknown}")

    scenes = [d for d in sorted(os.listdir(a.scenes_dir)) if os.path.isfile(os.path.join(a.scenes_dir, d, "clip.mkv"))]
    if a.scenes:
        scenes = [sc for sc in scenes if sc in a.scenes.split(",")]
    in_dir = cc.INPUT_DIR
    scores_path = os.path.join(a.out_dir, "scores-klein.json")
    scores = json.load(open(scores_path, encoding="utf-8")) if os.path.isfile(scores_path) else {}
    captions_path = os.path.join(a.out_dir, "captions.json")
    captions = json.load(open(captions_path, encoding="utf-8")) if os.path.isfile(captions_path) else {}

    # Inputs per scene: the grey canvas (Qwen), the blur-prefilled canvas + white-sides mask (Anima), the visible centre
    # (caption).
    prepared = {}
    for sc in scenes:
        frames = hs.read_frames(os.path.join(a.scenes_dir, sc, "clip.mkv"))
        mid = len(frames) // 2
        truth, vis, side = hs.split(frames[mid], a.side)
        h, w = truth.shape[:2]
        grey = np.full((h + 2 * PAD, w, 3), GREY, np.uint8)
        grey[PAD:PAD + h, side:side + vis.shape[1]] = vis
        blur_path = os.path.join(a.out_dir, "blur-extend", sc, f"s{a.side}.mp4")
        if os.path.isfile(blur_path):
            fill = hs.read_frames(blur_path, mid + 1)[mid]
        else:                                                     # no baseline on disk: stretch the edge columns
            fill = cv2.copyMakeBorder(vis, 0, 0, side, w - side - vis.shape[1], cv2.BORDER_REPLICATE)
        fill = fill.copy()
        fill[:, side:side + vis.shape[1]] = vis
        prefill = cv2.copyMakeBorder(fill, PAD, PAD, 0, 0, cv2.BORDER_REPLICATE)
        mask = np.full(grey.shape[:2], 255, np.uint8)
        mask[PAD:PAD + h, side:side + vis.shape[1]] = 0
        names = {k: f"panorama-{sc}-{k}.png" for k in ("grey", "prefill", "mask", "centre")}
        for k, img in (("grey", grey), ("prefill", prefill), ("centre", vis)):
            cv2.imwrite(os.path.join(in_dir, names[k]), cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
        cv2.imwrite(os.path.join(in_dir, names["mask"]), mask)
        prepared[sc] = (truth, vis, side, names)

    server = cc.start_server(a.port, os.path.join(a.out_dir, "comfy-server.log"), cc.LOW_RAM)
    try:
        for recipe in recipes:
            for i, sc in enumerate(scenes):
                truth, vis, side, names = prepared[sc]
                prefix = f"panorama/{recipe}-{sc}"
                if recipe in loras:
                    graph = qwen_graph(loras[recipe], names["grey"], names["centre"], a.seed, prefix)
                    save_node = "12"
                else:
                    prompt = ANIMA_POS + captions.get(sc, "")
                    graph = anima_graph(names["prefill"], names["mask"], prompt, a.seed, prefix)
                    save_node = "13"
                t0 = time.perf_counter()
                outputs = cc.run_graph(a.port, graph)
                secs = time.perf_counter() - t0
                if recipe in loras and "13" in outputs:
                    captions[sc] = outputs["13"]["text"][0].strip()
                raw = cc.saved_images(outputs, save_node)[0][PAD:PAD + truth.shape[0]]
                big = cv2.resize(raw, (truth.shape[1], truth.shape[0]), interpolation=cv2.INTER_LANCZOS4)
                big[:, side:side + vis.shape[1]] = vis            # the real picture is never the model's
                sc_scores = hs.score([truth], [big], side)
                sc_scores.update({"seconds": round(secs, 2), "first_call": i == 0, "seed": a.seed, "runner": "comfyui-0.39.0"})
                scores.setdefault(recipe, {}).setdefault(sc, {})[f"s{a.side}"] = sc_scores
                odir = os.path.join(a.out_dir, recipe, sc)
                os.makedirs(odir, exist_ok=True)
                cv2.imwrite(os.path.join(odir, f"plate-s{a.side}.png"), cv2.cvtColor(big, cv2.COLOR_RGB2BGR))
                cv2.imwrite(os.path.join(odir, f"raw-s{a.side}.png"), cv2.cvtColor(raw, cv2.COLOR_RGB2BGR))
                print(f"{recipe:20s} {sc:18s} {secs:7.1f} s  {sc_scores}", flush=True)
                hs.save_json(scores, scores_path)
                hs.save_json(captions, captions_path)
    finally:
        cc.stop_server(server)

    # Sheets: truth, the klein plates for comparison, then every recipe of this run.
    for sc in scenes:
        truth, _, side, _ = prepared[sc]
        rows, labels = [hs.mark_seams(truth, side)], [f"{sc}  s={a.side}  TRUTH"]
        for name in ["klein-green", "klein-base-green"] + recipes:
            plate = os.path.join(a.out_dir, name, sc, f"plate-s{a.side}.png")
            s_ = scores.get(name, {}).get(sc, {}).get(f"s{a.side}")
            if s_ and os.path.isfile(plate):
                rows.append(hs.mark_seams(cv2.cvtColor(cv2.imread(plate), cv2.COLOR_BGR2RGB), side))
                labels.append(f"{name}  {s_['seconds']} s  near {s_['err_near']}  far {s_['err_far']}  seam x{s_['seam_step']}")
        hs.compose_sheet(rows, labels, os.path.join(a.out_dir, "sheets", f"{sc}-painters-s{a.side}.png"), width=1280)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
