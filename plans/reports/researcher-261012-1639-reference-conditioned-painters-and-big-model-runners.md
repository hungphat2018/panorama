---
title: "R3 reference-conditioned painters and R4 big-model animators: what to download, where to run (2026-10-12)"
date: 2026-10-12
agent: researcher
extends:
  - plans/reports/research-261012-1521-ai-surround-anime-outpainting-synthesis.md (sections 4-6)
  - plans/reports/researcher-261012-1407-outpainting-sota-and-anime-fine-tune-bases.md
decision: which models to download after the GPU queue ends (~17:40) and which runner to run them in
---

# R3 painters + R4 animators: models, runners, downloads (2026-10-12)

Tags: **[V]** verified this pass (API read, file or source read, or my own header probe) · **[E]** my estimate or inference · **[S]** secondhand (Reddit, search snippet, page digest) · **[L]** measured earlier on this laptop (prior reports, repo scripts). `[n]` = source id, list in section 10 (every source carries its date). All sizes are decimal GB (bytes / 1e9, as the HF page shows), read 2026-10-12. Nothing was downloaded or run (GPU busy); the only network reads of weights were safetensors headers and a few 1 MiB slices (section 5).

## 0 Plain terms (once)

- **Reference image**: an extra picture the model looks at while drawing (another frame showing the whole character). **Mask**: map of keep/redraw pixels. **Sentinel**: a flat colour (green or gray) painted where the model must draw.
- **LoRA**: small add-on file that teaches a big model one skill. **Base vs distilled**: base = ~50 steps; distilled = student that needs 4-8 steps. A LoRA trained on the base may not work on the distilled model.
- **GGUF Q8_0 / FP8 / INT8-ConvRot**: ways to store weights in ~8 bit (about half of bf16), near-lossless in principle. INT8-ConvRot is ComfyUI's new native format; GGUF needs a custom node.
- **Expert (MoE)**: Wan2.2 A14B = two 14B networks, "high-noise" (layout, motion) and "low-noise" (detail); one runs per step. **VACE module**: the extra blocks that let Wan read masks, kept frames and reference images. **IC-LoRA**: LoRA that also reads a whole extra video in context.
- **Offload**: streaming weights between RAM/disk and the 12 GB GPU. **Dynamic VRAM (aimdo)**: ComfyUI's on-demand weight paging. **Group offload (leaf level)**: diffusers' equivalent.

## 1 Summary (decision first)

### 1.1 Bottom line

1. **R3: download Qwen-Image-2.1 + the ausboss Outpaint LoRA first.** Best open image-edit model on an independent arena (Elo 1074 vs klein-9B 1013, Edit-2511 1020, FLUX.2 dev 1000, klein-4B 950, klein-base-4B 871) [8]; up to 10 reference images [1][2]; its outpaint LoRA is built to keep the picture pixel-registered (kept-area PSNR 34 dB vs 16-26 dB without the LoRA, author's tests on 8-12 photos) [4], which targets your "scale 30% too big" failure. Licence is research/non-commercial [1]. **Use the Comfy-Org native files, not GGUF**: Qwen-2.1 GGUF is broken in ComfyUI-GGUF on ComfyUI 0.39.0 (open issues, 2026-10-07) [7].
2. **R3 cheap answer to unresolved question 4 of the synthesis (klein base + fal LoRA): the fal LoRA is a base-model LoRA and was run on the distilled klein-4B.** fal serves it on `klein/4b/base` [13]; the klein-9B outpaint LoRA author says distilled models "do not produce good results in most cases", use the base [14]. Download the klein-base-4B transformer (7.75 GB, Apache-2.0) and re-run before judging klein.
3. **R3 mask paths** (none validated on anime frames): Qwen-2.1 Fun-ControlNet-Union inpaint branch (true mask, 7.55 GB) [6]; LanPaint sampler for ComfyUI incl. Qwen-2.1 masked edit with a 2nd reference, FLUX.2 dev, klein, Wan2.2 video outpaint [24]; diffusers `Flux2KleinInpaintPipeline` (mask + `image_reference`) [17].
4. **R4: run plain Wan2.2-VACE-Fun-A14B first** (QuantStack Q8_0, 2 x 18.66 GB) [29]. **AnimeGen + VACE-Fun module is structurally drop-in** (1095 backbone tensors identical in name and shape to the Fun-VACE backbone, +236 module tensors) [V own probe, 5.1]; the Fun-VACE backbone is the unmodified official Wan2.2-T2V (0.17% relative difference = bf16 rounding), AnimeGen drifted 2-6% from it [V own probe, 5.2]; **no community report of AnimeGen + VACE exists** [V-neg]. Build one merged file per expert and run it in any runner.
5. **Runner: ComfyUI portable v0.39.x (Python 3.13, torch cu130), pinned, driven by its HTTP API, as the model-zoo runner** (Qwen-2.1, klein, Wan VACE-Fun, LTX-2.5). **Keep the existing diffusers 0.41 venv for the harness paths** (klein plates, VACE clips with refs/key plate, merged AnimeGen): it needs `pip install gguf`, and **leaf-level group offload, not `enable_sequential_cpu_offload()`** (broken with GGUF in 0.41) [18]. Kijai's wrapper only as a fallback (no push since 2026-05-24) [34]. AniSora AnyMask: repo code only [39].

6. **Caution from your own concurrent runs (5.3):** adding 2 reference images to klein-4B-distilled + fal LoRA raised the hidden-sides error (4 scenes: near 18.4 -> 25.9, far 20.8 -> 48.0). References are not a free win with a single-image outpaint LoRA; test each stack with 0, 1, then 2-3 refs.

### 1.2 R3 painter: ranked

| Rank | Model | Why | Adoption risk | Files + sizes | Runner |
|---|---|---|---|---|---|
| 1 | **Qwen-Image-2.1** (7B DiT + Qwen3-VL-8B encoder) + **ausboss Outpaint LoRA v2** | arena #1 open edit model [8]; 10 refs, output follows image_1, refs may differ in size/aspect [1][5]; LoRA: gray-pad canvas, keeps picture registered [4] | released 2026-09-20, 3 weeks old [1]; research-only licence [1]; vanilla T2I called "muddy" on anime detail [27]; LoRA trained on photos, canvas as sole input [4]; GGUF path broken in ComfyUI-GGUF [7] | DiT bf16 14.23 + TE bf16 17.53 + VAE 0.68 + LoRA v2 0.16 = **32.6 GB**. INT8-ConvRot pair (DiT 7.26 + TE 9.35 = 16.6 GB) is the ComfyUI template default [5] | ComfyUI native; diffusers `QwenImage21Pipeline` second |
| 2 | **FLUX.2 klein BASE**: 4B + fal LoRA now; 9B-base next | fixes the distilled/base mismatch [13][14]; 4B is Apache-2.0; 9B has real-mask path and LanPaint support [17][24] | arena: 4B 950, 4B-base 871, 9B 1013, 9B-base 976 (low) [8]; 9B is non-commercial; no first-party 9B outpaint LoRA (train one: ~1000 pairs, rank 16, 600 steps worked for ERP) [14] | 4B-base transformer **7.75 GB** (+ LoRA 0.08; TE/VAE already cached [E]); 9B-base Q8_0 9.98 + TE fp8 8.67 + VAE 0.34 = **19.0 GB** | diffusers (existing `run-klein-plates.py`) and ComfyUI templates |
| 3 | **Qwen-Image-Edit-2511** (20B, Apache-2.0) | only strong Apache edit model; arena 1020 [8][20] | no outpaint LoRA found; EditPlus pipeline has no mask [17]; superseded by 2.1 | INT8-ConvRot 20.50 + TE fp8 9.39 + VAE 0.25 = **30.1 GB** | ComfyUI |
| 4 | **FLUX.2 dev** (32B) | quality ceiling on paper, but arena 1000 is below klein-9B 1013 [8] | Q8_0 35.0 GB > 32 GB RAM; slow; non-commercial | Q8_0 35.00 + TE fp8 18.04 + VAE 0.34 = **53.4 GB** | ComfyUI, last |
| wild | Anima-Base v1.0 + LLLite inpaint v2 (anime-native 2B, real mask, ref by split-screen hack) | the only anime-trained painter with a mask path [25] | non-commercial; refs only by hack; 2B | 4.18 + 1.19 + 0.25 + 0.07 = **5.7 GB** | ComfyUI |
| wild | Krea-2 Turbo + Registered Outpaint / AnyPaint LoRA | source placed by RoPE coordinates = scale-aware by design [23] | one source only, conditioning at 384 px max edge, 12.8B, community licence, photo bias [23] | not in download list (test only if scale stays wrong) | diffusers helper code |
| skip | HunyuanImage-3.0-Instruct | arena 1068 (2nd open) [8] but 83B params, 168.7 GB bf16, 3 refs, EU/UK/KR excluded [21]; 3.5 is API-only [22][8] | no local fit | - | - |

Also seen, unproven: JoyAI-Image-Edit-Plus (16.3B, Apache-2.0, multi-image, 2026-06-22) and Boogu-Image-0.1-Edit (10.3B, Apache-2.0, 2026-06-16) [26]; not on the arena page [8].

### 1.3 R4 animator: run order

| Order | Candidate | refs + mask + kept key frame | Runner (with evidence) | Files + sizes | Main blocker |
|---|---|---|---|---|---|
| 1 | **Wan2.2-VACE-Fun-A14B** (Apache-2.0) | all three; multi refs only in diffusers (separate frames, white-padded) [17]; ComfyUI native takes 1 ref [35]; Kijai tiles refs into one frame [34] | ComfyUI native (Comfy-Org fp8_scaled repack) [30] and diffusers `WanVACEPipeline` (two experts + `boundary_ratio`, PR merged 2025-09-15) [19] | QuantStack Q8_0 2 x 18.66 = **37.3 GB**; or fp8_scaled 2 x 17.35 = 34.7 GB; + umT5 fp16 11.37 + VAE 0.25 (diffusers reuses cached umT5/VAE [E]) | diffusers: GGUF + sequential offload broken, two experts > 32 GB RAM [18][E] |
| 2 | **AnimeGen-T2V + Fun-VACE module** (merge once) | refs/mask/key frame come from the module; AnimeGen alone has none (T2V) [32] | merged file loads in ComfyUI native (model type detected from `vace_patch_embedding`) [V md.py], Kijai wrapper (VACE included), diffusers | aidealab bf16 2 x 28.58 + Kijai modules 2 x 6.10 (or fp8 2 x 3.05) = 69.4 GB in, ~37.4 GB Q8 out [E][31][32] | AnimeGen drift 2-6% from the backbone the module was trained on [5.2]; no community report |
| 3 | **LTX-2.5 + In-Outpainting IC-LoRA** (LTX licence) | mask = pad (green) [38]; optional start image; **refs undocumented** (Ingredients IC-LoRA = one sheet, 768x448x121, no stacking info) [38] | ComfyUI native: official ComfyUI-layout files + outpaint template [38][47]; diffusers `LTX2InContextPipeline` exists [17]; Wan2GP fallback [40] | INT8-ConvRot 21.50 + Gemma-4 TE INT8 15.37 + VAEs 1.84 + IC-LoRA 1.31 = **40.0 GB** | gated repos; do not use GGUF in ComfyUI-GGUF [7] |
| 4 | **AniSora AnyMask** (Apache-2.0) | mask yes (video + `_mask.mp4`); key frame via temporal mask; **no refs** [39] | repo code only [39] | 32.79 + T5 11.36 + VAE 0.51 = 44.7 GB (+ ~5 GB base-dir files [E]) | `flash-attn`, `transformers==4.46.1`, `numpy<2` pins; no quantised build [39] |

### 1.4 Runner decision

| Runner | Use for | Evidence for | Against |
|---|---|---|---|
| **ComfyUI portable v0.39.x, pinned** (py 3.13, torch cu130; release 2026-10-05) | R3: Qwen-2.1, klein, LanPaint. R4: Wan2.2-VACE-Fun, LTX-2.5 | official templates for Qwen-2.1 edit, klein x5, Wan VACE outpaint/ref2v, LTX-2.5, LTX-2.3 outpaint LoRA [47]; 4070 SUPER 12 GB + 32 GB RAM Windows user runs Wan2.2 GGUF [37]; README: portable = py3.13 + cu130 [45] | fast-moving core already broke GGUF ops (#487/#488) [7]; dynamic-VRAM bugs (section 6); 1 ref per `WanVaceToVideo` [35]; no Fun-VACE template [47] |
| **diffusers 0.41 venv** (`tools/venv-outpaint`) | what the harness already does (klein plates, `run-vace-clips.py` with `--refs`, `--key-plate`); merged AnimeGen; LTX2 IC-LoRA | all needed pipelines are in 0.41 [17]; refs as separate frames [17] | `gguf` not installed [L]; sequential offload + GGUF broken [18]; two experts > RAM [E]; INT8-ConvRot / NVFP4 are ComfyUI-only formats [E] |
| Kijai WanVideoWrapper | block-swap control only | VACE nodes, block swap, GGUF [34] | last push 2026-05-24, 1,283 open issues [34]; refs tiled into one frame; GGUF base needs a GGUF module [34]; reference dominance [34] |
| Wan2GP | LTX-2.5 fallback on 12 GB | pushed 2026-10-12, Windows 1-click, headless `--process`, applies Ingredients when refs given [40] | own model handling, hard to pin |
| repo code | AniSora AnyMask only | only runner [39] | dependency pins |

Pins to record: ComfyUI v0.39.0, comfy-aimdo 0.5.5, comfy-kitchen 0.2.37 [22][36]; ComfyUI-GGUF master (last push 2026-01-12) only for Wan/FLUX GGUF [37]; LanPaint master 2026-09-28 [24]. Driver here 617.42 [L].

## 2 Download list, in order (disk free 138 GB [L]; the whole list is ~290 GB, so stage and prune)

| Step | What (repo / file) | GB | Cum GB | Notes |
|---|---|---|---|---|
| 0 | `ComfyUI_windows_portable_nvidia.7z` (v0.39.0 release, 2.0 GB archive) + LanPaint + KJNodes; `pip install gguf` in `tools/venv-outpaint` | ~10 [E] | 10 | no GPU needed; pin versions [22][45] |
| 1a | `Comfy-Org/Qwen-Image-2.1`: `diffusion_models/qwen_image_2.1_bf16.safetensors` 14.23, `text_encoders/qwen3vl_8b_bf16.safetensors` 17.53, `vae/qwen_image_2.1_vae_bf16.safetensors` 0.68 | 32.4 | 42 | bf16 = reference for judging 8-bit loss; licence click-through not needed [3] |
| 1b | `ausboss/Qwen-Image-2.1-Outpaint-LoRA`: `qwen-image-2.1-outpaint-v2.safetensors` 0.16 (+ v1 0.16) | 0.3 | 42 | rank 32, ComfyUI key format [4] |
| 1c | `black-forest-labs/FLUX.2-klein-base-4B`: `transformer/diffusion_pytorch_model.safetensors` | 7.75 | 50 | Apache; reuse cached TE + VAE [E]; fal LoRA already cached [L] |
| 2a | klein-base-9B: `unsloth/FLUX.2-klein-base-9B-GGUF/flux-2-klein-base-9b-Q8_0.gguf` 9.98; `Comfy-Org/vae-text-encorder-for-flux-klein-9b`: `text_encoders/qwen_3_8b_fp8mixed.safetensors` 8.67 (bf16 16.38), `vae/flux2-vae.safetensors` 0.34 | 19.0 | 69 | non-commercial; for `Flux2KleinInpaintPipeline` / LanPaint / own LoRA |
| 2b | `alibaba-pai/Qwen-Image-2.1-Fun-Controlnet-Union`: `Qwen-Image-2.1-Fun-Controlnet-Union.safetensors` 7.55 | 7.55 | 77 | true-mask inpaint branch [6] |
| 2c | Anima wildcard: `circlestone-labs/Anima` `split_files/diffusion_models/anima-base-v1.0.safetensors` 4.18 + `text_encoders/qwen_3_06b_base.safetensors` 1.19 + `vae/qwen_image_vae.safetensors` 0.25; `kohya-ss/Anima-LLLite/anima-lllite-inpainting-v2.safetensors` 0.07 | 5.7 | 83 | non-commercial [25] |
| 3 | **Run the R3 hidden-sides test, then delete the losers** (expect to free 20-40 GB) | - | ~55 | keep the winner stack + klein-base-4B |
| 4 | R4 base: `QuantStack/Wan2.2-VACE-Fun-A14B-GGUF`: `HighNoise/...-high-noise-Q8_0.gguf` 18.66 + `LowNoise/...-low-noise-Q8_0.gguf` 18.66; `Comfy-Org/Wan_2.2_ComfyUI_Repackaged`: `split_files/text_encoders/umt5_xxl_fp16.safetensors` 11.37, `split_files/vae/wan_2.1_vae.safetensors` 0.25 | 49.0 (37.3 if diffusers only) | ~104 | alt native format: `wan2.2_fun_vace_{high,low}_noise_14B_fp8_scaled.safetensors` 17.35 each [30] |
| 5 | AnimeGen build: `aidealab/AnimeGen-T2V`: `high_noise.safetensors` 28.58, `low_noise.safetensors` 28.58; `Kijai/WanVideo_comfy`: `Fun/VACE/Wan2_2_Fun_VACE_module_A14B_HIGH_bf16.safetensors` 6.10, `..._LOW_bf16.safetensors` 6.10 (fp8 alt in `Kijai/WanVideo_comfy_fp8_scaled/VACE/` 3.05 each) | 69.4 in, ~37.4 out [E] | needs ~53 free | **one expert at a time** (28.6 + 6.1 + ~18.7 = ~53 GB peak [E]); delete inputs after each expert. After step 4 only ~34 GB are free, so first delete every R3 stack except the winner (or park the bf16 sources on another drive) |
| 6 | LTX-2.5: `Lightricks/LTX-2.5`: `diffusion_models/ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors` 21.50, `text_encoders/gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors` 15.37, `vae/ltx-2.5-video-vae-bf16.safetensors` 1.47, `vae/ltx-2.5-audio-vae-bf16.safetensors` 0.37; `Lightricks/LTX-2.3-22b-IC-LoRA-In-Outpainting/ltx-2.3-22b-ic-lora-in-outpainting-0.9.safetensors` 1.31 | 40.0 | - | repos gated (accept LTX licence); NVFP4 alt 18.72 [38] |
| 7 | AniSora: `IndexTeam/Index-anisora/anymask/`: `diffusion_pytorch_model.safetensors` 32.79, `models_t5_umt5-xxl-enc-bf16.pth` 11.36, `Wan2.1_VAE.pth` 0.51 + Wan2.1-I2V-14B-480P CLIP/tokenizer files only | ~50 | - | last; own venv; we must quantise it |

Optional between 4 and 5, cheapest anime-prior check without the module: `NullpoLab/AnimeGen-T2V-GGUF` `T2V_high_noise_Q8_0.gguf` + `T2V_low_noise_Q8_0.gguf` 15.41 each = 30.8 GB, run through LanPaint's Wan2.2 T2V video outpaint workflow [24][32] (works on any Wan2.2-T2V weights [E]).

## 3 R3 per-candidate notes

### 3.1 The six questions

| Model | (1) References: how many, how passed, works with mask/LoRA? | (2) Masked outpaint: native / LoRA / input format | (3) Anime evidence | (4) Licence |
|---|---|---|---|---|
| **Qwen-Image-2.1** | up to 10 [1][2]; pipeline `image=[...]`, prompt names them `<image1>..`; each ref goes to the Qwen3-VL encoder and into VAE latent tokens [17]; ComfyUI `TextEncodeQwenImage21` image_1.., output follows image_1 [5]. With LoRA: card shows image_1 only; extra refs untested [4][E] | no pixel-mask pipeline in diffusers [17]. (a) LoRA v1/v2: pad with flat gray `#808080`, canvas = image_1, prompt "Outpaint the image: replace the solid gray areas with a seamless continuation of the scene, keeping the existing picture unchanged.", 25 steps, CFG 1, paste original back with 32 px feather; **do not use Set Latent Noise Mask** (visible rectangle) [4]. (b) Fun-ControlNet-Union: white = redraw, 129-ch control (latents 64 + mask 1 + masked-image latents 64) [6]. (c) LanPaint masked edit with 2nd reference [24] | arena Elo 1074, n=6,108 [8] (general edit, not anime). Anime LoRA FUSAL (50k records incl. character-sheet reference-to-image, 2026-10-09) [x1]. Reddit: "best edit model" [27]; vanilla T2I "muddy details... particularly anime" (2026-09-26) [27] | Qwen Research License 2026-09-20: research/evaluation only; "Built with Qwen" if outputs train a distributed model [1] |
| **FLUX.2 klein** (4B, 4B-base, 9B, 9B-base, 9B-KV) | multi-reference in every variant [10]; `Flux2KleinPipeline(image=[...])`: no cap in code, each ref resized to <=1 MP [17]; `Flux2KleinKVPipeline` caches ref K/V (card: up to 2.5x faster, 9B) [x2]; HF blog states 10 for dev, klein cap not stated [11][V-neg] | `Flux2KleinInpaintPipeline(image, mask_image, image_reference, strength)`: white = repaint; it also feeds `image` as a reference (our black-sides failure [L]) and crops a ref batch to the first ref's size [17]. LoRAs: fal 4B (rank 16, pure green `#00FF00` border 5-25%, prompt "Fill the green spaces according to the image", scale ~1.1, served on base-4B) [13]; mirrors Ming3D, xocialize; 9B: nomadoor ERP-360 (base-9B, green canvas, 1-3 reference patches, rank 16, AdamW8bit, lr 3e-5, ~1000 pairs, ~600 steps) [14]; `24aittl/klein-9b-outpaint` merged, 589-byte card [x3]; LanPaint klein example [24] | arena: 9B 1013 (n=7,503), 9B-base 976, 4B 950, 4B-base 871 [8]; ours: style right, scale +30%, closes composition, recolours [L]; community: anime LoRAs for 9B (2026-09-11) [27], manga colourisation-by-reference LoRA on base-9B (2026-06-10) [x4] | 4B and 4B-base Apache-2.0; 9B family and dev: FLUX Non-Commercial (v2.1 text read in the klein-9B-KV copy, dev's own file not read): commercial/production use of the model barred; Outputs are not Derivatives and may be used commercially, 2(d); content-filter duty 2(e) [12] |
| **Qwen-Image-Edit-2511** | multi-image via `QwenImageEditPlusPipeline` [17][20] | EditPlus has no mask; `QwenImageEditInpaintPipeline` = single image + mask, built for original Edit (2511 fit unverified) [17]; no outpaint LoRA on HF (name searches outpaint/uncrop/extend/expand/zoom-out) [V-neg]; LanPaint Edit-2511 example [24] | arena 1020 (n=4,850) [8]; one Reddit user: klein better for inpaint/outpaint (n=1) [27] | Apache-2.0 [20] |
| **FLUX.2 dev** | up to 10 [11]; `Flux2Pipeline` [17] | no native inpaint pipeline; no outpaint LoRA found [V-neg]; LanPaint dev example [24] | arena 1000 (anchor, n=9,258), below klein-9B [8]; none anime-specific | FLUX.2-dev Non-Commercial [10][12] |
| **HunyuanImage-3.0-Instruct** | multi-image fusion up to 3 [21] | none documented | arena 1068 [8]; none anime-specific | Tencent: not valid in EU/UK/South Korea; outputs may not improve other models [21] |

### 3.2 Files, diffusers 0.41, ComfyUI

| Model | Files (repo / file, GB) | diffusers 0.41 | ComfyUI |
|---|---|---|---|
| Qwen-Image-2.1 | `Comfy-Org/Qwen-Image-2.1`: `diffusion_models/qwen_image_2.1_bf16.safetensors` 14.23, `..._int8_convrot.safetensors` 7.26; `text_encoders/qwen3vl_8b_bf16.safetensors` 17.53, `..._int8_convrot.safetensors` 9.35; `vae/qwen_image_2.1_vae_bf16.safetensors` 0.68; `model_patches/qwen_image_2.1_fun_controlnet_union_{bf16,int8_convrot}` [3]. GGUF exists (`unsloth/Qwen-Image-2.1-GGUF/qwen-image-2.1-Q8_0.gguf` 7.64) but see right | `QwenImage21Pipeline`, `QwenImage21Transformer2DModel` in the single-file list (PR #14836) [16][17]; GGUF key compatibility and ComfyUI-format LoRA conversion for 2.1 **not verified** [E] | native since day 0, template `image_qwen_image_2_1_image_edit` [2][5]; **ComfyUI-GGUF fails on 2.1** (issue #487 "mat1 and mat2 shapes cannot be multiplied", #488 `input_act` TypeError, 2026-10-07; fix PRs #483/#485 open) [7] |
| klein | 4B-base: `black-forest-labs/FLUX.2-klein-base-4B/transformer/diffusion_pytorch_model.safetensors` 7.75; `unsloth/FLUX.2-klein-base-4B-GGUF/flux-2-klein-base-4b-Q8_0.gguf` 4.30; `black-forest-labs/FLUX.2-klein-base-4b-fp8/flux-2-klein-base-4b-fp8.safetensors` 4.09; LoRA `fal/flux-2-klein-4B-outpaint-lora/flux-outpaint-lora.safetensors` 0.08. 9B: GGUF Q8_0 9.98 (base and distilled), `black-forest-labs/FLUX.2-klein-9b-fp8` 9.43 (gated), `...-9b-kv-fp8` 9.82 (open), bf16 transformer 18.16. TE `Comfy-Org/vae-text-encorder-for-flux-klein-{4b,9b}`: `qwen_3_4b.safetensors` 8.05, `qwen_3_8b_fp8mixed.safetensors` 8.67 (bf16 16.38); VAE 0.34 [15] | `Flux2KleinPipeline`, `Flux2KleinKVPipeline`, `Flux2KleinInpaintPipeline`; `Flux2Transformer2DModel` single-file + GGUF (Q8_0 dequant present; needs `gguf`) [17] | templates `image_flux2_klein_image_edit_{4b,9b}_{base,distilled}`, `image_flux2_klein_9b_kv_image_edit` [47]; ComfyUI-GGUF `flux` arch OK (klein bug #411 closed) [7] |
| Edit-2511 | `unsloth/Qwen-Image-Edit-2511-GGUF/qwen-image-edit-2511-Q8_0.gguf` 21.76; `Comfy-Org/Qwen-Image-Edit_ComfyUI/split_files/diffusion_models/qwen_image_edit_2511_{int8_convrot 20.50, fp8mixed 20.53, bf16 40.86}`; TE `Comfy-Org/Qwen-Image_ComfyUI/split_files/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors` 9.39 (bf16 16.58); VAE `qwen_image_vae.safetensors` 0.25 [20] | `QwenImageEditPlusPipeline`, `QwenImageEditInpaintPipeline` [17] | native |
| FLUX.2 dev | `city96/FLUX.2-dev-gguf/flux2-dev-Q8_0.gguf` 35.00 (same in `unsloth/FLUX.2-dev-GGUF`); `Comfy-Org/flux2-dev/split_files/diffusion_models/flux2_dev_fp8mixed.safetensors` 35.46; TE `split_files/text_encoders/mistral_3_small_flux2_fp8.safetensors` 18.04 (bf16 35.59); `black-forest-labs/FLUX.2-dev-NVFP4/flux2-dev-nvfp4.safetensors` 21.04 [15] | `Flux2Pipeline`; HF blog: group offload runs in 8 GB VRAM + 32 GB RAM [11] | native; NVFP4 loads with BFL's files (ComfyUI maintainer, 2026-01-14) [36] |

Streaming note [E]: Q8 files larger than RAM (FLUX.2 dev 35 GB, Wan Q8 37 GB for two experts) are read from SSD per step; plan minutes per image, not seconds.

### 3.3 Why this order (trade-offs)

- **Scale / registration** is the failure that matters most. Plain edit models reframe or rescale a padded canvas (Qwen-2.1 without LoRA: kept-area PSNR 23.6 dB, "a fisheye kitchen shrank inside its frame") [4]; the LoRA drives it to 33.6 dB, seam colour step -0.6 [4]. Krea-2 solves it architecturally (RoPE placement) but sees the source at 384 px [23]. Nobody published an anime test of either.
- **Distilled vs base** is a free experiment (7.75 GB): fal and nomadoor both trained on the base [13][14]. Your own run shows references hurt on the distilled model with this LoRA (5.3); expect the same risk for any single-image outpaint LoRA, so test each candidate with 0 refs first, then 1, then 2-3.
- **Anime detail**: vanilla Qwen-2.1 T2I is reported soft on anime illustration [27]; edit/outpaint with the source in context may differ [E]. An anime LoRA stack exists (FUSAL) but was trained on the Base checkpoint and may not combine with the outpaint LoRA [x1][E].
- **FLUX.2 dev is not a quality upgrade on this evidence**: arena 1000 vs klein-9B 1013 [8].

## 4 R4 per-candidate notes

### 4.0 Runner matrix (Windows, 12 GB VRAM + 32 GB RAM, Q8-class; evidence tag per cell)

| Candidate | diffusers 0.41 | ComfyUI native (+ GGUF node) | Kijai wrapper | Repo code |
|---|---|---|---|---|
| Wan2.2-VACE-Fun-A14B | `WanVACEPipeline` (transformer + transformer_2 + boundary_ratio, refs, masks) and GGUF single-file for `WanVACETransformer3DModel` exist [V 17][19]; sequential offload + GGUF broken, group offload untested on this model [18][E] | `WanVaceToVideo` (1 ref) + Comfy-Org fp8_scaled repack [V 35][30], or QuantStack GGUF through city96 node (Wan arch whitelisted) [V 7]; no Fun-VACE template [47] | works: VACE included in GGUF, block swap incl. VACE blocks, refs tiled [V 34]; stale since 2026-05-24 | VideoX-Fun (Apache-2.0, push 2026-09-29): `GPU_memory_mode` model_cpu_offload / qfloat8 / sequential_cpu_offload; tested env is Windows 10, torch 2.2, CUDA 11.8/12.1, RTX 3060 12G (pre-Blackwell, needs a torch upgrade) [V 46] |
| AnimeGen + Fun-VACE module | merge first, then `WanVACETransformer3DModel` from the merged state dict [E] | merged file via `UNETLoader`: class chosen from keys incl. `vace_patch_embedding` [V md.py]; cannot attach a separate module [V nmm.py] | separate module only with a non-GGUF base, GGUF base needs a GGUF module (ValueError) [V 34] | VideoX-Fun with a custom transformer path [E] |
| LTX-2.5 + In-Outpainting | `LTX2InContextPipeline` + `LTX2ReferenceCondition` (two-stage) [V 17]; outpaint padding / blend is ours [E] | official ComfyUI-layout INT8-ConvRot / NVFP4 files + LTX-2.3 outpaint template [V 38][47] | not applicable | Lightricks repo (not read this pass) |
| AniSora AnyMask | no (custom `WanI2V_any_mask1_spa` class) [V 39] | no node; the I2V mask channel could host it, unbuilt [E] | no | **yes, the only runner** [V 39] |

Adoption risk, R4 [E from the cited dates]: Wan2.2-VACE-Fun 13 months old, VideoX-Fun active, community split on quality [42]; AnimeGen 4 months, small startup, 1.4k T2V downloads, unanswered provenance question [32]; LTX-2.5 very active (new IC-LoRAs weekly in September; 1.7M downloads) but licence-encumbered [38]; AniSora quiet since 2025-10 [39]. Runners: ComfyUI breaks custom ops with core releases (GGUF, v0.39) [7]; diffusers ships a minor every ~6 weeks (0.38 2026-05-01, 0.39 07-03, 0.40 08-20, 0.41 10-06) [16]: pin both.

### 4.1 Wan2.2-VACE-Fun-A14B (alibaba-pai, Apache-2.0, 2025-09-10)

- **(1) Inputs [V][17].** `video` (gray 0.5 where unknown), `mask` (1 = generate), `reference_images` (each fitted to the canvas and centred on white, one extra latent frame in front of the clip), `conditioning_scale`, `guidance_scale` + `guidance_scale_2` (per expert), `boundary_ratio` (PR default 0.875 [19]). A kept key frame = one frame with mask 0 (already implemented as `--key-plate` in `run-vace-clips.py` [L]). Wan2.2-T2V reference settings: shift 12.0, 40 steps, boundary 0.875, CFG 3.0 low-noise / 4.0 high-noise [43].
- **Refs per runner.** diffusers: list of separate frames. ComfyUI native `WanVaceToVideo`: `reference_image[:1]`, but VACE conditionings are stackable (one node per ref; not equivalent to VACE's joint context) [35][E]. Kijai: all refs tiled side by side into one frame [34]. **Refs must be small on white padding or they dominate the mask edit** (Kijai, 2025-09-14) [34]; a bf16 module with a quantised base ignored the ref and start frame, the fp8_scaled module worked (2025-09-23) [34].
- **Architecture match.** Config of `alibaba-pai/.../high_noise_model/config.json`: dim 5120, 40 layers, `vace_layers [0,5,...,35]`, `vace_in_dim 96` = diffusers' Wan2.1-VACE-14B config, so single-file auto-detection `wan-vace-14B` applies [28][17][V own compare].
- **Community quality (mixed) [S].** "Works great for outpainting" (2026-02-11) vs "worse than 2.1 for inpaint and outpaint" (2026-07-29); one user: 2.2 better colour consistency, 2.1 better scene consistency; for outpainting a reference image helps colour stability when the scene is stable, not with strong camera motion; some prefer Wan2.2 low-noise + extracted 2.1 module; "high-noise model not needed for VACE and can recolour b/w video" [42].
- **Blockers.** diffusers: see section 6. ComfyUI: only Wan2.1 VACE templates exist; Fun-VACE runs by swapping the model files (first-party repack [30]) [E].

### 4.2 AnimeGen-T2V + Fun-VACE modules

- **(1)** AnimeGen-T2V (aidealab, Apache-2.0, fine-tune of Wan2.2-T2V-A14B, 2026-06-17): text-to-video only; README uses the 4-step Lightning LoRA, 8 steps, shift 3.0, 16 fps, "RTX 4090 or higher recommended" [32]. Masks, refs and key frames only through the VACE module.
- **(2) Runners.** (a) **Merged file (recommended)**: concatenate the two safetensors' raw tensors (disjoint key sets, section 5.1), then quantise (city96 GGUF tools, as lym00 did for Wan2.2 T2V + Wan2.1 VACE scopes [41]; or `convert_to_quant --int8 --convrot --wan --exclude-layers "vace_blocks\.\d+\.(before|after)_proj"`, which keeps the 9 zero-initialised projection tensors unquantised because Q-noise can silence VACE [x5]). (b) Kijai wrapper with separate module: node `WanVideoVACEModelSelect`; error text: use the module with "matching T2V base model"; **with a GGUF base the module must also be GGUF** (so NullpoLab GGUF + Kijai safetensors module raises ValueError); "VACE only works properly with T2V models" [34]. (c) Native ComfyUI cannot attach a standalone module: the model class is chosen from the loaded keys; merge nodes only patch existing keys [V md.py][V nmm.py]; Kijai: possible only with non-GGUF models (image-only reply) [34]. (d) No-VACE alternative: LanPaint Wan2.2 T2V video outpaint, 880x480, 40 frames, 2 thinking steps, ~40 GB VRAM listed, <=40 frames advised [24][E for AnimeGen drop-in].
- **(3) Attach evidence.** Structural [V own probe]; values in section 5.2; precedents: "aniwan" anime fine-tune + VACE module in Kijai's wrapper (2025-09-02) [34], lym00 VACE-into-Wan2.2-T2V GGUF (2025-07-28) [41], "Wan2.2 T2V low-noise + extracted 2.1 module gives the best inpaint" [42]. **None for AnimeGen**: HF discussions (T2V #1 on training-data provenance, I2V #1 "Unable to generate"), GitHub issue search, Reddit archive [V-neg]. Reddit verdict on AnimeGen itself: "not sure it beats base Wan with LoRAs" (2026-07-19) [27].
- **(4) Files**: section 2 step 5. HIGH module pairs with the high-noise expert, LOW with low-noise [E]. Variant worth one run: official Fun-VACE high-noise expert + AnimeGen-low + LOW module (anime prior where detail is drawn, less drift where layout is set) [E].
- Licence note: HF discussion #1 asks about AnimeGen training-data provenance and Ghibli/KyoAni/Eva look-alike outputs; no maintainer answer yet (2026-07-15) [32].

### 4.3 LTX-2.5 + In-Outpainting IC-LoRA

- **(1)** Docs: source centred on a larger canvas, pad = mask; prompt optional; stage 1 "optionally conditions on a start image"; nothing on extra references or on stacking IC-LoRAs; "In-Outpainting IC-LoRA is reused from LTX-2.3" [38]. Ingredients IC-LoRA (2026-09-10): one composite reference sheet as looped video, trained at 768x448, 121 frames, first-frame conditioning 0.0 [38]. Community Multiple-Subject-Reference IC-LoRA (2026-08-14) [S].
- **(2) Runners.** ComfyUI native (official files in ComfyUI layout; LTX-2.3 outpaint template) [38][47]; diffusers `LTX2InContextPipeline` + `LTX2ReferenceCondition` (stage 1 IC-LoRA, latent upsample, distilled-LoRA refine) [17]; padding colour and Laplacian blend-back would be ours [E]; Wan2GP [40].
- **Quality hints [S].** "LTX outpaint works very well provided there is no animation in the outpaint area" (2026-07-23) [42]; seams: blend dilation is "the most impactful setting" [38].
- **Blockers.** gated repos; GGUF Q8_0 exists (`vantagewithai/LTX-2.5-GGUF/distilled/ltx-2.5-22b-distilled-transformer-Q8_0.gguf` 23.60) but ComfyUI-GGUF custom ops are out of sync with core [7]; NVFP4 checkpoints without `_quantization_metadata` load unquantised and crash (#15511) [36].

### 4.4 AniSora AnyMask

- **(1)** Input = source video + `<name>_mask.mp4` (spatial and temporal mask) + one image; Wan2.1-I2V-14B architecture with CLIP-vision, so **no reference images** [39]. Example: 1280x720, 129 frames, 8 steps, guide scale 1, single-GPU command given; outpainting never shown [39].
- **(2)** Repo code only; no GGUF, no ComfyUI port (only a V3.2 GGUF guide; "Support to COMFYUI" #13 open since 2025-05-19) [39][V-neg]. Last HF update 2025-10-31; GitHub pushes since are credential clean-ups [39].
- **Blockers.** requirements: `flash-attn`, `transformers==4.46.1`, `numpy<2` -> separate venv; flash-attn on Windows unlikely, SDPA fallback if the fork keeps upstream `attention.py` [E]; 32.79 GB bf16 needs our own quantisation for 12 GB [E].

## 5 Own probes (no model downloads; HTTP Range reads, 2026-10-12)

### 5.1 Header diff

| File | Tensors | Params |
|---|---|---|
| `alibaba-pai/Wan2.2-VACE-Fun-A14B/high_noise_model/diffusion_pytorch_model.safetensors` | 1331 | 17.34B |
| `aidealab/AnimeGen-T2V/high_noise.safetensors` | 1095 | 14.29B |
| `Kijai/WanVideo_comfy/Fun/VACE/Wan2_2_Fun_VACE_module_A14B_HIGH_bf16.safetensors` | 236 (`vace_blocks.0-7` + `vace_patch_embedding`) | 3.05B |

AnimeGen high-noise vs Fun-VACE high-noise: 0 tensors only in AnimeGen, 236 only in Fun-VACE (exactly the module), **0 shape mismatches** [33].

### 5.2 Weight drift (relative L2 = ||a-b|| / ||a|| on 1 MiB slices of 6 big matrices per expert; a = official Wan-AI/Wan2.2-T2V-A14B)

| Expert | official vs Fun-VACE backbone | official vs AnimeGen |
|---|---|---|
| high-noise | 0.0016-0.0017 | 0.032-0.061 |
| low-noise | 0.0017 | 0.021-0.039 |

Small tensors (norms, modulation, biases; 14 tensors, high-noise): official vs Fun-VACE 0.1-0.35% of max (bf16 rounding); official vs AnimeGen 0.4-18% [33]. Reading: the module was trained on a frozen official backbone; AnimeGen is a real full fine-tune (not a light merge), so the module sees a shifted backbone. Community fine-tunes with modules usually still work (4.2), but expect some loss of mask/ref adherence [E]. Sample, not a full diff.

### 5.3 Concurrent local runs found while finalising (not mine; `spikes/ai-surround/out/runs/`, files stamped 17:24-17:31) [L]

Hidden-sides means (near / far error 0-255, seam factor; lower is better), klein-4B **distilled** + fal green LoRA, same 4 scenes (dark, edge-head-closeup, edge-person-still, held-dialogue):

| Variant | near | far | seam |
|---|---|---|---|
| green canvas only | 18.4 | 20.8 | x4.7 |
| + 2 reference images after the canvas | 25.9 | 48.0 | x9.0 |
| + 2 refs and a reference-aware prompt | 27.3 | 44.1 | x9.5 |

References made the numbers worse on 3 of 4 scenes (edge-head-closeup 15.5 / 24.1 -> 46.4 / 78.3; dark far 6.1 -> 49.0) [L scores-klein.json]. Numbers are not the goal (the by-eye sheets decide), but it says a single-image LoRA on the distilled model does not take references cleanly: the reason to prefer the base model, a LoRA trained with references (nomadoor trained with 1-3 reference patches [14]) or a true-mask path for reference use. VACE-1.3B with the klein-green key plate as a kept frame is mixed on the two scenes finished (held-dialogue far 30.6 -> 27.3 but near 13.3 -> 17.1 and seam x1.84 -> x2.36; pan-horizontal far 22.4 -> 34.3) [L scores-vace.json]. The VACE `--refs` run had no scores yet.

## 6 Runner blockers: Windows, Blackwell sm_120, 12 GB VRAM + 32 GB RAM

- **diffusers 0.41:** `enable_sequential_cpu_offload()` + any GGUF raises `KeyError: None`; `enable_model_cpu_offload()` moves the whole transformer (Q8 14B = 18.7 GB) and OOMs; **leaf-level group offload works** (tested: FLUX.1-schnell Q4_K_S, RTX 5070 Laptop 8 GB, 0.77 GiB peak, 3.0-3.7 s with `use_stream=True`); issue #14691 and fix PR #14698 open [18]. Not tested on `WanVACETransformer3DModel` [E]. `gguf` missing in the venv [L]. Two Q8 experts (37.3 GB) exceed 32 GB RAM: swap experts inside `callback_on_step_end` (the loop reads `self.transformer` / `self.transformer_2` every step) [17][E]. Qwen-2.1 mentions a `flex` attention backend; needs Triton on Windows [17][E].
- **ComfyUI dynamic VRAM (aimdo):** on RTX 5070 Ti `fp8_e4m3fn_fast` loses native FP8 matmul under dynamic VRAM (1.07 vs 1.72 it/s Flux; still reproducible on v0.39.0, 2026-10-10); workaround `--disable-dynamic-vram` [36]. Intermittent `cuMemSetAccess ... device not ready` -> `--disable-cuda-malloc` [36]. Windows standby list fills with the model file and thrashes the SSD until reboot; RAMMap "Empty Standby List" fixes it (2026-07-10) [36]; aimdo opens files with `FILE_FLAG_SEQUENTIAL_SCAN` [44]. Cumulative RAM growth with Wan2.2 GGUF on a 5070 (closed 2026-01-21) [36].
- **GGUF in ComfyUI:** city96 node stale (push 2026-01-12), "Is GGUF over?" thread; ComfyUI team: use native formats with dynamic VRAM [37]; dynamic-VRAM GGUF is open PR #427 (one user reports Wan2.2 GGUF fine on a 4070 SUPER 12 GB / 32 GB RAM Windows box, quant unstated; another found Wan2.2 Q4_K_M slower than the non-dynamic path) [37]. Wan arch is whitelisted [V loader.py].
- **SageAttention on sm_120:** `sageattn_qk_int8_pv_fp16_cuda` aborts, `..._fp16_triton` raises `cudaErrorIllegalAddress`; only KJNodes `auto` runs; FP8-PV path produced noise above ~160k tokens on an RTX PRO 6000 (the reporter later saw it clean on ComfyUI 0.31.1; cause not pinned down) [36]. A 32:9 canvas at 1920x540 and 81 frames is ~86k tokens (W/16 x H/16 x 21) [E]. A commenter on a black-image Qwen outpaint thread says sage must be off for Qwen models (Reddit 2026-01-09): leave sage off first [27][E].
- **Heavy-GPU hangs:** `DPC_WATCHDOG_VIOLATION` reboot in `nvlddmkm` with MiniMax H3 on Blackwell + Windows portable (driver 610.88; also 5090), fixed by lighter settings [36]; not seen with Wan or LTX, but expect long single kernels to matter on a laptop [E].
- **NVFP4:** works with BFL's official FLUX.2-dev NVFP4 files, not with arbitrary NVFP4 Wan checkpoints (2026-01-14) [36]. NVFP4 is 4-bit, not the near-lossless class you asked for.
- **Torch/driver:** ComfyUI requires cu130-class PyTorch on NVIDIA 20-series and up; portable = py3.13 + cu130 [45]; here torch 2.14.1+cu130, driver 617.42 [L].
- **Wan on wide canvases:** VACE-Fun trained at 512/768/1024 buckets [28]; 3.5:1 canvases are outside any published test [E].

## 7 Known failure modes

- **R3:** edit models reframe or rescale a padded canvas, or return the gray untouched (Qwen-2.1 without LoRA: 9.1% gray left) [4]; second subject on large missing regions (Krea registered outpaint) [23]; Set Latent Noise Mask draws a visible rectangle on Qwen-2.1 [4]; LanPaint mask boundary glow (issue #80) [24]; distilled model + base-trained LoRA degrades [14]; `Flux2KleinInpaintPipeline` copies a black-sided input (our test [L]); doubled person without the Qwen LoRA [4]; vanilla Qwen-2.1 anime detail soft [27].
- **R4:** reference dominates the mask edit unless small on white padding [34]; quantised base + bf16 module ignores refs [34]; VACE colour shift (VACE issue #44, noted by lym00) [41]; 2.2 vs 2.1 VACE trade-off colour vs scene consistency [42]; LTX outpaint struggles when the outpaint area contains action [42]; LanPaint video degrades beyond ~40 frames [24]; GGUF base needs GGUF module in Kijai [34].

## 8 Limits of this research

- Nothing was run: all quality statements are second-hand; the only own measurements are header and slice reads (5). No anime outpaint benchmark exists for any model here.
- The arena [8] measures general human preference for edits, not anime or outpainting; page undated; two WebFetch digests agree; "open weights" is inferred from HF links.
- Reddit comes from the pullpush archive (keyword luck); Civitai not queried; YouTube unreadable.
- diffusers GGUF loading for Qwen-2.1, FLUX.2 and Wan-VACE was read in source, not executed. The 14B-class behaviours (group offload with GGUF, mid-run expert swap, RAM fit) are [E].
- INT8-ConvRot vs Q8_0 vs bf16 fidelity: no independent measurement found (one forum claim that INT8 has higher perplexity than Q8_0 [37]).
- LTX licence text not re-read this pass (see prior report); Qwen and FLUX clauses were read [1][12].

## 9 Unresolved questions

1. Does the ausboss LoRA keep the picture registered when image_2..N references are added? (untested; the incumbent klein-4B + fal LoRA got worse with 2 refs, 5.3)
2. Does klein-base-4B + fal LoRA remove the +30% scale error, or is that a property of klein?
3. Does the AnimeGen + Fun-VACE module keep mask/ref adherence given 2-6% drift; HIGH only, LOW only, or both?
4. diffusers: does leaf-level group offload + GGUF work on `WanVACETransformer3DModel` (18.7 GB per expert) and is the mid-run expert swap clean?
5. INT8-ConvRot vs Q8_0 vs bf16 on anime line art (Qwen-2.1 TE also reads the references).
6. Qwen-2.1 "muddy anime detail": does it appear in edit/outpaint with the source in context?
7. Do stacked `WanVaceToVideo` nodes approximate VACE's joint multi-reference context?
8. LTX-2.5: can Ingredients and In-Outpainting IC-LoRAs be combined; does the 2.3 outpaint LoRA match 2.5 quality?
9. AniSora AnyMask: spatial-mask semantics for "keep centre, draw sides"; any SDPA fallback in the fork?
10. LanPaint seams on Qwen-2.1 (ausboss warns against latent masks) vs LoRA stitch.
11. Licences to decide later: Qwen Research (non-commercial, "Built with Qwen"), FLUX NC v2.1, LTX derivative clause, Tencent territory limits, AnimeGen data provenance (unanswered).
12. Behaviour of 14B video models on a 32:9 canvas.

## 10 Sources (read 2026-10-12 unless a date is given)

R3
- [1] Qwen-Image-2.1 card + LICENSE (repo 2026-09-14, licence 2026-09-20): https://huggingface.co/Qwen/Qwen-Image-2.1
- [2] QwenLM/Qwen-Image-2.1 README (news 2026-09-20, 2026-10-09): https://github.com/QwenLM/Qwen-Image-2.1
- [3] Comfy-Org repack (2026-09-15): https://huggingface.co/Comfy-Org/Qwen-Image-2.1
- [4] ausboss Outpaint LoRA (2026-09-26): https://huggingface.co/ausboss/Qwen-Image-2.1-Outpaint-LoRA
- [5] ComfyUI template (main): https://github.com/Comfy-Org/workflow_templates/blob/main/templates/image_qwen_image_2_1_image_edit.json
- [6] Fun-ControlNet-Union (2026-09-23): https://huggingface.co/alibaba-pai/Qwen-Image-2.1-Fun-Controlnet-Union
- [7] ComfyUI-GGUF issues #487, #488 (2026-10-07), PRs #483, #485, #427, #463 (2026-06-23), loader.py: https://github.com/city96/ComfyUI-GGUF
- [8] Artificial Analysis image-editing arena (undated; read twice): https://artificialanalysis.ai/image/leaderboard/editing
- [10] BFL flux2 README (push 2026-03-12): https://github.com/black-forest-labs/flux2
- [11] HF blog FLUX.2 (2025-11-25): https://huggingface.co/blog/flux-2
- [12] FLUX Non-Commercial License v2.1 (copy, 2026-03-09): https://huggingface.co/black-forest-labs/FLUX.2-klein-9b-kv-fp8/blob/main/LICENSE
- [13] fal klein-4B outpaint LoRA (2026-01-19): https://huggingface.co/fal/flux-2-klein-4B-outpaint-lora (mirror xocialize 2026-07-14 states scale ~1.1)
- [14] nomadoor klein-9B ERP LoRA + TRAINING_NOTES (2026-03-01, mod 2026-06-21): https://huggingface.co/nomadoor/flux-2-klein-9B-360-erp-outpaint-lora
- [15] HF API file lists: unsloth/FLUX.2-klein-{base-4B,base-9B,9B}-GGUF, city96 + unsloth FLUX.2-dev GGUF, black-forest-labs/FLUX.2-klein-*-fp8, FLUX.2-dev-NVFP4, Comfy-Org/flux2-dev, Comfy-Org/vae-text-encorder-for-flux-klein-{4b,9b}
- [16] diffusers v0.41.0 release (2026-10-06): https://github.com/huggingface/diffusers/releases/tag/v0.41.0
- [17] local diffusers 0.41.0 source, `tools/venv-outpaint/Lib/site-packages/diffusers` (pipelines flux2, qwenimage, qwenimage21, wan/pipeline_wan_vace.py, ltx2/pipeline_ltx2_ic_lora.py, loaders/single_file_model.py, quantizers/gguf)
- [18] diffusers issue #14691 (2026-09-02) + PR #14698: https://github.com/huggingface/diffusers/issues/14691
- [20] Qwen-Image-Edit-2511 (2025-12-17), unsloth GGUF, Comfy-Org repacks: https://huggingface.co/Qwen/Qwen-Image-Edit-2511
- [21] HunyuanImage-3.0-Instruct card + LICENSE (mod 2026-02-03): https://huggingface.co/tencent/HunyuanImage-3.0-Instruct
- [22] ComfyUI releases v0.32.0 (2026-08-11), v0.38.0 (2026-09-29), v0.39.0 (2026-10-05): https://github.com/Comfy-Org/ComfyUI/releases
- [23] Krea-2 registered outpaint (2026-07-14), AnyPaint (2026-08-03): https://huggingface.co/yijunwang2/krea2-outpaint
- [24] LanPaint README (push 2026-09-28): https://github.com/scraed/LanPaint
- [25] Anima + LLLite (2026-01-29 / 2026-04-27): https://huggingface.co/kohya-ss/Anima-LLLite; Reddit 1totumo (2026-05-27)
- [26] JoyAI-Image-Edit-Plus (2026-06-22), Boogu-Image-0.1-Edit (2026-06-16): https://huggingface.co/jdopensource/JoyAI-Image-Edit-Plus-Diffusers
- [27] Reddit (pullpush archive): r/StableDiffusion 1ww2s4w (2026-10-02/03), 1wnl1yf (2026-09-22..10-03), 1wpyieu (2026-09-25); r/comfyui 1q849u7 (2026-01-09), 1wdhhjj (2026-09-11); 1rrkt3c (2026-03-12); 1v099rv AnimeGen (2026-07-18..21)
- [x1] FUSAL anime LoRA (2026-10-09): https://huggingface.co/MicksHF/Qwen-Image-2.1-Anime-Fusal
- [x2] klein-9B-KV card (2026-03-09): https://huggingface.co/black-forest-labs/FLUX.2-klein-9b-kv-fp8
- [x3] https://huggingface.co/24aittl/klein-9b-outpaint (2026-06-29)
- [x4] https://huggingface.co/thedeoxen/FLUX.2-klein-9B-manga-colorization-by-reference-LORA (2026-06-10)

R4 and runners
- [19] diffusers PR #12324 (merged 2025-09-15): https://github.com/huggingface/diffusers/pull/12324; issue #11878 (2025-07)
- [28] alibaba-pai Wan2.2-VACE-Fun-A14B (2025-09-10): https://huggingface.co/alibaba-pai/Wan2.2-VACE-Fun-A14B
- [29] QuantStack GGUF (2025-09-14): https://huggingface.co/QuantStack/Wan2.2-VACE-Fun-A14B-GGUF
- [30] Comfy-Org Wan_2.2_ComfyUI_Repackaged: https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged
- [31] Kijai modules: https://huggingface.co/Kijai/WanVideo_comfy ; https://huggingface.co/Kijai/WanVideo_comfy_fp8_scaled
- [32] AnimeGen-T2V (2026-06-17; discussion 2026-07-15), NullpoLab GGUF (2026-07-18), easygoing0114 INT8: https://huggingface.co/aidealab/AnimeGen-T2V
- [33] own probe: safetensors header and 1 MiB slice range reads, 2026-10-12 (scripts in the session scratchpad)
- [34] Kijai/ComfyUI-WanVideoWrapper main (push 2026-05-24) nodes_model_loading.py, nodes.py; issues #1220 (2025-09-12), #1291 (2025-09-23), #1163 (2025-09-02): https://github.com/kijai/ComfyUI-WanVideoWrapper
- [35] ComfyUI `comfy_extras/nodes_wan.py` (master) WanVaceToVideo; PR #8240 (2025-05-22)
- [36] ComfyUI issues #14876 (2026-07-10), #11775 (2026-01-10), #16551 (2026-09-25..10-10), #15263 (2026-08-03), #11864 (2026-01-14), #15489 (2026-08-11), #12943 (2026-03-14), #15511 (2026-08-11): https://github.com/Comfy-Org/ComfyUI/issues
- [37] ComfyUI-GGUF status (push 2026-01-12), #427 (2026-03-05), #463 (2026-06-23)
- [38] Lightricks/LTX-2.5 (2026-07-23), IC-LoRA In-Outpainting (2026-06-17), Ingredients (2026-09-10), docs: https://docs.ltx.io/open-source-model/advanced-workflows/in-outpainting
- [39] AniSora: https://github.com/bilibili/Index-anisora (push 2026-07-16, `anisora_anymask/`), https://huggingface.co/IndexTeam/Index-anisora (mod 2025-10-31)
- [40] Wan2GP (push 2026-10-12): https://github.com/deepbeepmeep/Wan2GP
- [41] lym00 Wan2.2_T2V_A14B_VACE-test (2025-07-28): https://huggingface.co/lym00/Wan2.2_T2V_A14B_VACE-test
- [42] Reddit: 1tt771p (2026-05-31..07-30), 1s6997m (2026-03-28..08-11), 1r1hbrv (2026-02-11), 1v4207d (2026-07-23)
- [43] Wan2.2 `wan_t2v_A14B.py`: https://github.com/Wan-Video/Wan2.2
- [44] comfy-aimdo README, issue #70 (2026-06-29): https://github.com/Comfy-Org/comfy-aimdo
- [45] ComfyUI README: https://github.com/Comfy-Org/ComfyUI
- [46] VideoX-Fun README (push 2026-09-29): https://github.com/aigc-apps/VideoX-Fun
- [47] Comfy-Org/workflow_templates tree (main): https://github.com/Comfy-Org/workflow_templates
- [x5] FartingBackwards INT8-ConvRot VACE-Fun repack (2026-07-25): https://huggingface.co/FartingBackwards/wan2.2-fun-vace-14B-int8-convrot
- [V md.py] ComfyUI `comfy/model_detection.py` line 752; [V nmm.py] `comfy_extras/nodes_model_merging.py`; [V loader.py] ComfyUI-GGUF `loader.py` line 12.
