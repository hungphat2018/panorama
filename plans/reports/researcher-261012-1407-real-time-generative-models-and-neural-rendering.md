---
title: "Real-time generative models and neural rendering: when can the AI periphery be computed on this laptop?"
date: 2026-10-12
author: researcher
extends: plans/reports/researcher-261010-1846-unused-canvas-area-periphery-and-outpainting.md
---

# Real-time generative models and neural rendering (October 2026)

Tags: **[V]** verified this pass (page / feed / repo / API fetched) · **[S]** search snippet, news, secondhand · **[R]** recalled, NOT verified · **[E]** my arithmetic or inference · **[L]** measured locally on this laptop 2026-10-12 (coordinator's PyTorch run; my nvidia-smi).
WebFetch returns a small-model digest of a page, not raw text: HTML-derived [V] numbers are digest-level (the Adapting-VACE tables were pulled twice and agreed). n/s = not stated, n/c = not checked.
Budget: 35 WebSearch/WebFetch calls (7 wasted: 4x HTTP 403 guru3d/tomshw/xenospectrum/techspot, 2x arXiv-API timeouts, 1x JS-only NVIDIA forum) + ~19 arXiv/GitHub-API script runs + local nvidia-smi/vulkaninfo. Over the ~40-call guide only if script runs are counted. No model run, download or install.

Terms (defined once):
- **shot** = unbroken run of film between cuts. **drawing** = one new hand-drawn image (anime holds a drawing for 2-3 frames). **plate** = a still background image generated for one shot. **periphery** = the leftover canvas area.
- **TFLOPS** = trillions of arithmetic operations per second. **u** = share of this GPU a job needs (u = 0.5 means half of it).
- **FP8 / NVFP4** = numbers stored in 8 / 4 bits so matrix maths runs ~2x / ~4x faster on Blackwell cards (the network must tolerate it).
- **DiT** = diffusion transformer, the big network in most 2025-26 image / video generators. **token** = a 16x16-pixel patch handled as one item; cost grows with token count.
- **motion vectors** = per-pixel arrows saying where each pixel moved since the last frame. **G-buffer** = extra images a game engine draws beside colour (depth, surface direction, material). **MFG** = DLSS multi frame generation (several made-up in-between frames per rendered one).
- **SDEdit** = start from a rough picture plus partial noise instead of pure noise, so the layout survives.

## 1 Executive summary

Decision: WHEN is the AI extension computed on Setup A (RTX 5070 Ti Laptop, 12 GB, 120 W cap, sm_120 [L])? Ranked:
1. **(c) per shot, ahead of the playhead = build now.** Test episode: 357 shots / 24 min (mean 4.0 s, median 3.3 s); camera slides >= 1 s are only 2.3% of frames [L] -> one static plate per shot, almost no warping. Few-step image models take ~1-5 s per plate here (SDXL-Lightning-class inpaint 1-2.5 s, FLUX.2 klein 4B NVFP4 ~3 s [E]) = 5-30 min GPU per 24-min episode as a pre-pass, or 0.25-0.75 of the GPU if run during playback. Parametric glow stays as fallback and for fast-changing edges.
2. **(d) offline = feasible now, as ceiling / hard-shot fallback only.** 1.3B causal video + VACE masks: ~2.2 h per 24-min episode at 1/4 scale and 24 fps (35 min if only new drawings are generated) [E]. 14B-class (HL-OutPaint-like) = ~118 GPU-days per 2-h film here, and still no code [E; GitHub search 2026-10-12 negative, V].
3. **(b) streaming video diffusion = ~6-12 months out.** Only mask-capable real-time causal model found: "Adapting VACE" (2026-02-16), 17.2 FPS at 368x640 on an RTX 5090, 14.6 GB peak [V] = ~4.3 fps here [E] vs 6.3 new drawings/s needed [L]: 1.5x short at 1/4 scale, 5.6x short at 24 fps. Closes only by stacking FP8 + 2-step student + edge-band context; none is published for VACE-style weights. No anime test anywhere. A half-day test (X3) can pull this forward if BF16 / FP8 already reaches >= 6 fps at <= 6 GB here.
4. **(a) live per film frame = no** for anything that invents pixels: generator needs 2.6-4.3 TFLOP/frame vs ~0.05-0.10 TFLOP available in "a few ms" (26-90x short) [E]. Only tiny nets fit, and they cannot invent content.
5. **DLSS 5** re-lights / re-materials pixels that already exist, fed by game-engine motion vectors; RTX 50 only; +10-22 ms per frame, two RTX 5090s in the first demo [S/E]. **Anime4K / ArtCNN** are 12k-4M-parameter local filters. Both transform known pixels; neither is evidence that missing regions can be invented in real time.
6. **Architecture hint:** make the cue-sheet "plate" a timestamped texture asset (still now, short video later) with per-shot enable + strength, so (b) can drop in without redesign.

## 2 Invariant + lever map

**Invariant:** the real picture is untouched (ADR 0001); the periphery continues the picture's edge and stays calm in time; the performer never stutters (GPU work at playback is absent or sliced short); local only, this laptop.
**Currency:** u = (work units per film-second) x (GPU-seconds per unit) = share of this GPU. Performer already ~0.23 (given). Working ceiling for added work ~0.5 [E assumption, not measured].
**Work units, real test episode [L]:** per film frame 23.976/s (41.7 ms) · per new drawing 6.3/s (159 ms; 74% of frame steps repeat) · per shot 0.25/s (4.0 s; p10 0.9 s, p90 7.9 s) · camera slide >= 1 s = 2.3% of frames.

Levers (numbers in 3.6):
- **How often:** A1 per frame · A2 per new drawing (x3.8 fewer than frames) · A3 per shot · A4 offline · A5 change-gating (refresh only when the edge band changed; unmeasured, see X1).
- **Size of one unit:** B1 canvas scale (1/4 linear = 16x fewer pixels) · B2 condition on the edge band only, not the whole picture · B3 model class (tiny CNN / 1-step image / causal video / 14B+) · B4 steps 50 -> 4 -> 2 -> 1 · B5 precision BF16 -> FP8 -> NVFP4 · B6 sparse / linear / FP4 attention · B7 tiny VAE decoder.
- **Reuse:** C1 hold or warp between generations (frame-generation idea; camera-slide track) · C2 real pixels first (same-shot mosaic incl. future frames, AI only for never-seen pixels; sibling spike `spikes/ai-surround`) · C3 static plate + parametric glow for dynamics + colour-match at the seam · C4 cache plates on disk (model-agnostic, swap model = regenerate).
- **Shortcuts:** D1 pseudo-outpaint = paste picture into a wider canvas, pre-fill sides with edge extension, run any video-to-video model at partial noise (SDEdit idea), keep only the sides: no mask-trained weights needed [E] · D2 tiny learned fill instead of a generator.
- **Where it runs:** E1 out-of-process worker (ADR 0014) · E2 short kernel slices vs 240 Hz (other agent) · E3 patch the stack: drop the T5 text encoder after caching the prompt embedding, TAEHV decoder, torch.compile / CUDA graphs [V Self-Forcing README], libplacebo hooks as host for CNN passes [V project spike] · E4 the Radeon 610M iGPU is useless for this [R].
- **Degrade path:** F1 parametric surround whenever a plate is not ready · F2 per-shot disable + strength (the fix NVIDIA shipped after the DLSS 5 backlash).

## 3 Findings

### 3.0 Local facts

| Item | Value | Tag |
|---|---|---|
| GPU | RTX 5070 Ti Laptop, compute cap 12.0 (sm_120), 12,227 MiB; power limit now 120 W (default 65, max 140); max SM clock 3090 MHz | [V local] nvidia-smi 14:24 |
| Spec | 5,888 CUDA cores, 992 AI TOPS, 672 GB/s, boost 1447-2220 MHz, 12 GB GDDR7 | [V] nvidia.com 50-series laptops page |
| Matmul | PyTorch 2.14.1+cu130, 8192^2 dense: bf16 52.8, fp16 53.2, FP8 e4m3 113.2 TFLOPS | [L] coordinator |
| Peaks implied | bf16 ~62, FP8 ~124, FP4 ~248 TFLOPS dense (FP32 accumulate); measured = 85% / 91% of them | [E] from 992 TOPS |
| Free VRAM | 10.1 GB free per coordinator; my nvidia-smi at 14:24: 5,998 MiB used = 6.2 GB free. Varies ~4 GB -> keep a resident generator <= 6 GB | [L] / [V local] |
| Episode | 357 shots, median 3.3 s, 6.3 new drawings/s, slides >= 1 s = 2.3% of frames | [L] coordinator |

### 3.1 Q1 DLSS 5 and frame generation

| Fact | Value | Tag, date |
|---|---|---|
| Announced | GTC 2026; NVIDIA post "DLSS 5, launching this Fall, introduces a real-time neural rendering model"; Huang: "GPT moment for graphics" | [V] nvidia.com news 2026-03-17 |
| Shipped | 2026-09-03 in NBA 2K27, all RTX 50 desktop + laptop GPUs + GeForce NOW; toggle "DLSS Neural Rendering", stacked on Super Resolution + Dynamic MFG | [V] nvidia.com news 2026-09-03; dev blog |
| What it does | "final neural-rendering stage": adds lighting / material detail, preserves structure, identity, artist intent; marketed "3D-Guided Neural Rendering" | [V] dev blog 2026-09-22; DLSS page |
| Inputs | frame colour + engine motion vectors. Third party: "pixel-space diffusion transformer", plus G-buffer (albedo, normals, depth, roughness, metalness) | [V] colour + MV; [S] G-buffer / DiT (jonpeddie 2026-09-02; conflicts with the dev blog, which names no depth / G-buffer) |
| Output | one frame in, one frame out, deterministic, "up to 4K", "compact, specialized model" on a single RTX 50 GPU; stability comes from motion vectors, not model memory | [V] dev blog |
| Controls | models A/B/C, Structure Intensity (fine detail), Tone Intensity (light / colour), semantic + engine masks | [V] dev blog; [S] PC Gamer (SIGGRAPH 2026) |
| Published cost | no ms, VRAM or on/off fps from NVIDIA. Only fps figure: 370 FPS at 4K Ultra + RT on an RTX 5090 (5080: 233) with DLSS Performance + 6x MFG, no off baseline | [V] dev blog, NBA 2K27 post 2026-09-03; [S] jonpeddie for 5080 |
| Unofficial cost | GTC demo used two RTX 5090s (one rendered, one ran DLSS 5). Leaked mod, characters only, 4K, 5070 Ti: 71 -> 35 fps = +14.5 ms. TechSpot (game / GPU not visible in snippet): 1440p 217 -> 67 fps = +10 ms, 4K 124 -> 33 fps = +22 ms | [S] godisageek 2026-08, TechSpot, PCGH (all via search; ms are [E]) |
| Criticism | "AI slop", "beauty filter": faces / lighting changed against artist intent; Huang: critics "completely wrong"; NVIDIA added sliders + masks | [S] Mar-Aug 2026 (Push Square, WTOP, PC Gamer) |

Plain English:
- DLSS 5 is a retoucher handed the finished photo plus a map of where each pixel moved. It may repaint the look (light, skin, material) of pixels that exist. It is not a painter who imagines the room outside the frame. Its side data (3D scene, motion vectors) comes from a game engine; a film has none.
- **Proves:** (1) a per-frame neural image transform at up to 4K ships on consumer RTX 50 GPUs in 2026, laptops included; (2) one-frame-in / one-frame-out anchored to aligned known data is stable enough to ship, because stability comes from the anchor, not from memory; (3) it is expensive: ~2.7 ms per megapixel in the TechSpot numbers (+10 ms at 1440p = 3.7 MP, +22 ms at 4K = 8.3 MP: cost scales with pixels) [E from S].
- **Does NOT prove:** inventing content where no pixels exist (the opposite problem); that it runs on video (no engine data, no public API outside game engines found [E]); anime fidelity; a cost small enough for "a few ms" next to a player. No open equivalent exists.
- Rough size guess [E, speculative]: ~2.7 ms/MP at 40-80 TFLOPS effective is ~0.1-0.3 MFLOP per pixel, i.e. a model in the tens of millions of parameters if it works on 16-px patches. A mask-capable video generator sits at ~17 MFLOP per pixel (3.7), 60-170x more.
- The art-style backlash is the warning for us: a generic generator will paint photoreal texture around anime. Copy NVIDIA's fix: strength + per-shot disable.
- **Frame generation:** NVIDIA's page [V 2026-10-12]: MFG makes up to 5 frames per rendered frame ("6X"), Dynamic MFG (DLSS 4.5) is RTX 50 only, single FG on RTX 40/50. Per-generated-frame ms: not found. NVIDIA text: 5 AI models (SR, RR, MFG) run per rendered frame "within a few ms" [S]. It warps real pixels with motion vectors and repairs small holes. Our analogue: generate on a new drawing, hold / warp in between.

### 3.2 Q2 Anime4K and successors

| Tool | What / size | Cost | Deploy | Status |
|---|---|---|---|---|
| Anime4K [V] | line-art-aware CNN shaders: Upscale S/M/L/VL/UL, Restore, Restore_Soft, Denoise, Deblur, Clamp_Highlights, Darken, Thin, AutoDownscalePre_x2/x4; v4.1 experimental SRGAN 360p -> 4K. Layer / param counts not published | no ms table. Doc says "if M takes 5 ms, L ~10 ms, VL ~20 ms"; budget 41 ms at 24 fps; Vega64 / UL numbers promised but absent | GLSL for mpv, Plex, IINA; ports Magpie, Metal, WebGPU, Unity, Rust, C++ | MIT, 21.5k stars, last push 2024-08-17: frozen but stable |
| ArtCNN [V] | C4F16 ~12k, C4F32 ~48k, R8F64 ~926k, R16F96 ~4M params; C = real-time, R = ONNX only. Variants: DS denoise+sharpen, DN denoise+soften, Chroma, JPEG420 / 444 | no ms published. Mine [E]: ~50 GFLOP (C4F16) / ~0.2 TFLOP (C4F32) per 1080p input frame -> ~2-7 ms / ~8-28 ms here (non-tensor shader, 25-40% efficiency) | mpv `vo=gpu-next` + `glsl-shader`; ONNX via vs-mlrt | MIT, 383 stars, push 2026-10-06: active |
| mpv-AnimeJaNai [S] | Real-ESRGAN Compact / UltraCompact / SuperUltraCompact ONNX through TensorRT in a custom mpv; frame-interpolation page in its docs | model cards (v1 era): RTX 3080 for UltraCompact 1080p real time, RTX 4090 for Compact | Windows only | community project |
| RTX Video (VSR + HDR) [V page, S rest] | closed AI upscaler + SDR->HDR on Tensor Cores, "offline rendering and real-time playback"; page gives 1080p->4K example only | no ms published (not found); 4 quality levels, level 4 needs xx70+ [S]; Chrome / Edge (driver 531.14, 2023), VLC [S] | NVIDIA SDK | alive (SDK page read 2026-10-12); GPUs, OS, API, anime not stated on the page |

Conclusions:
- All of them transform pixels that exist, through a small neighbourhood (each output pixel sees a few pixels around it). None inpaints or extends [V READMEs]. No real-time anime net that inpaints was found (not found != nonexistent).
- Beyond upscaling: line repair (Restore, Darken, Thin, Deblur), denoise + sharpen, chroma reconstruction, JPEG cleanup, interpolation (RIFE page in mpv-AnimeJaNai docs, title only [S]).
- **Proves:** the performer can host cheap neural passes. libplacebo hooks run mpv GLSL: spike `spikes/picture-path/src/pp-s1-hook.c` already uses `pl_hook`, and report 261011-1247 notes `pl_mpv_user_shader_parse` [V project files]. Real-time anime nets are small enough to share a GPU with a 240 Hz player.
- **Does NOT prove:** invention. Compute per pixel is ~0.05-0.2 MFLOP for the C-series vs ~17 MFLOP for a mask-capable generator [E]. They also sharpen lines, which is the opposite of what a calm periphery wants: do not sharpen the periphery.

### 3.3 Q3 Real-time / streaming video generation (2025-2026)

Terms: **diffusion** = start from noise, clean it up over several steps (one network pass per step). **Distillation** = train a fast student to copy a slow teacher in fewer steps (50 -> 4). **Causal / autoregressive (AR)** = make video chunk by chunk, each chunk seeing only past chunks (like writing the next sentence), so it can stream. **KV cache** = memory of past chunks kept so they are not recomputed. **Mask** = picture saying which pixels to generate and which to keep. **VACE** = add-on to the Wan video model that takes masks (inpaint / outpaint), depth, reference images.

| Model (date) | Params | Speed [V] | Res / GPU | Weights + licence | Known-region input? | Anime? | Flag |
|---|---|---|---|---|---|---|---|
| CausVid (2024-12-10) | Wan 1.3B-class, 4-step DMD | 9.4 FPS, single GPU (type not in abstract) | n/s | repo NOASSERTION; last push 2025-08-07 | v2v / i2v zero-shot, no mask | no | superseded |
| Self Forcing (2025-06-09, NeurIPS'25) | 1.3B | "real-time, sub-second latency, single GPU"; ~17 fps H100 [R] | README: >= 24 GB GPU, 4090 / A100 / H100 tested | Apache-2.0; push 2025-09-12 | no | no | superseded; compile + TAEHV + FP8 tips useful |
| Rolling Forcing (2025-09-29) | n/s | "16 FPS", single GPU (README) | n/s | NOASSERTION; push 2025-10-31 | no | no | stagnant |
| LongLive (2025-09-26, NVlabs) | 1.3B | 20.7 FPS H100; 24.8 FPS FP8; <= 240 s | n/s | Apache-2.0 (was CC-BY-NC-SA until 2025-11-01); push 2026-10-12 | I2V first frame only | no | active |
| LongLive-2.0 (2026-05-13) | 5B | BF16 24.8, NVFP4 4-step 29.7, NVFP4 2-step 45.7 FPS on ONE GB200, excludes VAE decode | n/s | Apache-2.0 repo; HF weights licence unchecked | I2V only, no mask | no | active |
| StreamDiffusionV2 (2025-11-10, MLSys'26) | 1.3B / 14B | 4x RTX 4090 PCIe: ~16 FPS @832x480, ~24 @512^2; 4x H100: 42.3 (1.3B, 1-step) / 39.2 (14B) @480p; first frame 0.37-0.47 s; bf16, no TensorRT | | Apache-2.0; push 2026-09-14 | v2v by noising the input; no mask | no | active |
| Krea Realtime 14B (HF card, date n/s) | 14B | 11 fps, 4-step, ONE B200; ~1 s first frame | n/s | HF tag apache-2.0 [V digest]; a curated list says non-commercial [S]: read the licence text | v2v with `strength`; no mask | no | too big |
| MirageLSD (Decart) | n/s | "<40 ms latency" [S] | | proprietary service | v2v | n/a | closed |
| Causal Forcing (2026-02-02, ICML'26) / ++ (2026-05-14) | Wan 1.3B | frame-wise 1-2 step students, first-frame latency -50%; fps not in abstract | "single 4090" [S] | Apache-2.0; push 2026-10-03 | T2V / I2V | no | recipe |
| Causal-rCM (2026-06-24) | Wan 1.3B | 1-2 steps, VBench 84.63 | | code not checked | | | recipe |
| **Adapting VACE (2026-02-16)** | LongLive 1.3B; Krea 14B | **LongLive + inpaint: 17.2 FPS** (698 ms / 12-frame chunk; baseline 22.3), 14.6 GB peak, RTX 5090, 368x640, 4-step, bf16, TAE decoder. Krea 14B + inpaint: 12.5 FPS, 45.1 GB, H100, 320x576 | | impl `daydreamlive/scope` CC BY-NC-SA 4.0 (alpha); VACE weights = Wan2.1 1.3B / 14B (licence not in paper; Apache-2.0 [R]) | **YES:** inpaint + outpaint masks, depth, extension. Reuses VACE weights with no training; +29-30% latency; mask-preservation SSIM 0.983; drifts beyond ~100 frames without re-anchoring; no VBench / user study | no | **only mask-capable real-time one found** |
| JoyAI-Video-Edit (2026-08-04) | 16B (+ external MiMo-VL) | 720p ~30 FPS B200; 840x480 @ 24 FPS RTX 5090 32 GB; 720p @ 16 FPS RTX PRO 6000 | | Apache-2.0 | instruction / reference edit; no mask or outpaint listed | demos "cartoon", "hand-drawn cel" restyle | too big |
| ABot-World-0 (2026-07-21) | n/s | 720p up to 16 FPS RTX 5090, low-bit, ~19 GiB, 1.2 s first frame | | n/c | action-conditioned world model | no | not a periphery tool |
| TurboDiffusion (2025-12-18, accelerator) | Wan2.1 1.3B / 14B, Wan2.2 I2V-A14B | 5-s 480p clip 184 s -> 1.9 s on ONE RTX 5090 (100-200x) | | Apache-2.0; push 2026-08-27 | T2V / I2V only | no | active; clip-based, not streaming |
| VDOT++ (2026-10-02), ForcingBooster (2026-10-08), LongLive-Plug (2026-09-29) | | 4-step condition-based (VACE benchmark) generators; -50% steps via early-stopping easy patches; LoRA "once-for-all" distillation | | code / weights not checked | | | watch |

Findings:
- Every real-time number is on a 24 GB+ desktop or datacentre GPU (4090, 5090, H100, B200, GB200). None on 12 GB; none on laptop silicon [V absence].
- Mask-capable real-time exists only as the Adapting-VACE hack (existing VACE weights, no training, qualitative evidence, no anime). Video-to-video models (StreamDiffusionV2, Krea, CausVid) "edit" by noising the input (SDEdit idea): no guarantee about a known region.
- DynaForcing (2026-08-18): DMD-distilled students collapse toward near-static output [V abstract]: fine for calm periphery, bad if motion is wanted.
- Anime: nothing tested. Fine-tune cost matters: LongLive's extension took 32 H100-GPU-days; Self Forcing training <2 h on 64 H100 or <16 h on 8 H100 [V READMEs]. Anime fine-tuning of a causal video model is a cloud job; LoRA on an image model is laptop-scale (BFL: klein Base is the LoRA target [V]).

### 3.4 Q4 Few-step image generation / editing and fast fill (per-shot plates)

| Model (date) | Size / VRAM | Steps, speed [V] | Inpaint / outpaint | Licence | This laptop [E] |
|---|---|---|---|---|---|
| FLUX.2 [klein] 4B (2026-01-15) | 4B; "~13 GB" (BFL blog) vs "~8 GB" (README): conflict; FP8 -40%, NVFP4 -55% VRAM | step-distilled; "<0.5 s" at 1 MP on GB200 bf16; FP8 up to 1.6x, NVFP4 up to 2.7x on RTX 5080 / 5090 @1024^2 T2I | single + multi-reference edit; **mask / inpaint / outpaint not mentioned** | 4B Apache-2.0; 9B, 9B-KV: FLUX Non-Commercial | edit plate 0.66 MP (~200 TFLOP): NVFP4 ~3 s (2-4), FP8 ~5 s (4-7), BF16 ~11 s |
| Z-Image-Turbo (2025-11-26) | 6B; "fits within 16 GB consumer VRAM" | 8 NFE; sub-second on H800 | generation only; Z-Image-Edit "to be released" in README (push 2026-02-09) | Apache-2.0 | ~10 s per MP at FP8: no |
| SANA-Sprint 1.6B (2025-03-12, ICCV'25) | 4-bit SANA < 8 GB [V README] | 1-4 steps; 0.1 s H100, **0.31 s RTX 4090** @1 MP T2I; ControlNet 0.25 s H100 | ControlNet exists; inpaint not verified | Apache-2.0 code; weights licence unchecked | ~0.9 s per MP |
| Qwen-Image-Edit-2511-Lightning (2025-12-22) | 20B base [R] | 4-step LoRA | edit by prompt | Apache-2.0 | no: 20B > 12 GB |
| SDXL-Lightning (2024-02-21) / Hyper-SD (2024-04-21) | SDXL ~2.6B UNet; ~6-8 GB [R] | 1-8 steps [V abstracts] | mature inpaint recipes [R]; many anime checkpoints [R] | OpenRAIL++ / per checkpoint [R] | ~1-2.5 s per plate (FLOPs from R) |
| SDXS-512 / 1024 (2024-03-25) | small UNet | one step; ~100 FPS (512) / ~30 FPS (1024) on "a single GPU" (type n/s), image-conditioned | conditions (ControlNet-like) | n/c | ~28-47 ms @0.26 MP |
| StreamDiffusion (2023-12-19) | SD-Turbo-class | img2img up to 91.07 fps on one RTX 4090 | img2img | n/c | ~30 ms; no temporal coherence |
| LaMa (2021-09; Apache-2.0; push 2025-02-05) / MI-GAN (ICCV'23; MIT code, weights separate licence; push 2026-09-14) | feed-forward, no diffusion; VRAM < 2 GB [R] | speeds not verified [R: tens of ms] | large-mask inpaint / mobile | | tens of ms [R]; blur and repetition on big holes [R]; LaMa stagnant |

Findings: plates are a **mature** problem (SDXL-class inpaint, 1-2.5 s); the new 2026 models (klein, Z-Image) are faster per quality but have no mask path in what I could verify, so outpaint would go through "edit with a padded canvas" prompts [E]. Plate size: one 1536x432 panorama (0.66 MP, picture in the middle) then upscale 2.5x; or two side strips of ~0.3 MP each.

### 3.5 Q5 Online video outpainting / FOV expansion

- **FlowLens** (arXiv 2211.11293, T-IV 2024) [V]: "online video inpainting" to expand a car camera's FOV; optical flow + clip-recurrent transformer; KITTI360 masks; MIT code, 35 stars, last push 2024-03-25. fps not in abstract. Driving only, legacy.
- **Real pixels first:** Seen-to-Scene (CVPR 2026 Findings) now has code (InSeokJeon/Seen_to_Scene, MIT, push 2026-05-27) [V]. Unified Long Video In/Outpainting: Apache-2.0 code, push 2026-10-05 [V]. OutDreamer: 47 stars, no licence [V]. **HL-OutPaint: no repo found** (GitHub search 2026-10-12) [V negative].
- **Open big model:** a ComfyUI node does "prompt-free sliding-window MiniMax H3 video outpainting" (MIT, 2026-09-08) [V]; MiniMax-H3 is a 33B audio+video DiT [S, SANA README]: class (d) only, > 12 GB.
- **Real-time and sparse:** DreamStereo (2604.12270) does 25 FPS at 768x1280 on one A100 by dropping > 70% of tokens (10.7x), but its holes are small [V abstract]. EditCtrl (2602.15031): cost proportional to the edited area [V]. Outpainting masks are 50-75% of the canvas, so these tricks give <= 1.3-2x here [E].
- **Games / VR:** no shipping neural widescreen or FOV expansion found (one extended search, 2026-10) [V negative]. Magpie (2608.27168, 2026-08-27) renders worlds from engine "white-box frames": not FOV expansion [V abstract]. Cloud outpainters (LTX, VEED, PixVerse, WaveSpeed) exist [S]: out by ADR 0004.
- Real-time + outpaint together = Adapting-VACE only (3.3).

### 3.6 Q6 Acceleration levers with reported numbers

| Lever | Reported | Source, date | Caveat for us |
|---|---|---|---|
| Step distillation: distribution matching (DMD, DMD2, CausVid, Self / Causal Forcing), consistency (Hyper-SD, SANA-Sprint sCM, rCM), adversarial (ADD / SD-Turbo 2023-11, SDXL-Lightning 2024-02) | 50 -> 4 steps (CausVid); frame-wise 1-2 steps (Causal Forcing++); Wan1.3B 2-step VBench 84.63; DMD2 / ADD one-step image; SANA-Sprint 0.1 s H100 | [V] abstracts 2023-11 ... 2026-06 | biggest single lever. KV-refresh pass remains: 4 steps = 5 forwards, 2 steps = 3 (x1.67) [E]. Static-output collapse risk |
| Sparse / linear attention | SLA: -95% attention FLOPs, 13.7x kernel, 2.2x end to end. TurboDiffusion bundle (SageAttention + SLA + rCM + W8A8): 184 s -> 1.9 s | [V] 2025-09; README 2026 | tuned for clip-based 480p-720p; unproven with causal + VACE |
| FP4 attention | SageAttention3: 1038 TOPS on RTX 5090 = 5x FlashAttention there | [V] 2025-05 | Blackwell only (this laptop is sm_120 [V local]); ~0.27x here [E] |
| FP8 | matmul 113.2 vs 52.8 TFLOPS bf16 here (2.14x) [L]; klein FP8 up to 1.6x; LongLive FP8 1.2x (H100) | [L] / [V] | end to end 1.2-1.5x (Amdahl: ~65% matmul) [E] |
| NVFP4 | klein up to 2.7x (-55% VRAM) on 5080 / 5090; Nunchaku ~3x vs BF16 on 5090 (FLUX.1); **LongLive-2.0 only 1.20x at 4 steps (24.8 -> 29.7), 1.84x with 2 steps (GB200)** | [V] 2026-01, 2025-02, 2026-05 | image DiT gains >> video AR gains (norms, RoPE, KV, VAE are not matmul) |
| Feature caching | TeaCache up to 4.41x (Open-Sora-Plan); Cache-DiT ~4x on 4 GPUs | [V] 2024-11; Z-Image README | skips steps; little left to skip at 2-4 steps [E]; TeaCache repo last push 2025-06 |
| Token sparsity / edit-area compute | EditCtrl "10x"; DreamStereo -70% tokens = 10.7x | [V] 2026-02, 2026-04 | masks are 50-75% here: <= 1.3-2x [E] |
| Adaptive denoising | ForcingBooster: -50% steps, same quality | [V] 2026-10-08 | smooth periphery = many easy patches [E]; no code seen |
| Low-res + cheap upscale | cost ~ canvas pixels: 1/4 linear = 16x fewer than full [E] | | periphery is low acuity [R]; upscale with ArtCNN-class pass [V deployable] but do not sharpen |
| Edge-band-only context | give the model ~1/4 of the picture beside the seam: canvas pixels x0.6-0.75 | [E] | unmeasured |
| Generate at drawing rate + hold / warp | 6.3 new drawings/s vs 23.976 frames/s = /3.8 [L]; DLSS MFG up to 5 generated frames per rendered | [L]; [V] | AR models would see "drawings" as frames; effect on their motion prior untested |
| Tiny decoder | TAEHV recommended with compile + FP8; LongLive FPS excludes VAE decode | [V] Self-Forcing, LongLive READMEs | full VAE adds ~10-20% [E] |
| Tiling (local windows over a big canvas, HL-OutPaint style) | cost x number of tiles + overlap; HL-OutPaint 12.6 s/frame on A100 (prior report) | [V] 2026-05 | avoid: at s=1/4 the whole canvas is one window |

### 3.7 Q7 Arithmetic for Setup A (assumptions; results are in section 4)

**Speed ratios, laptop vs the paper's GPU (compute-bound DiT / UNet, bf16) [E]:**
- vs RTX 5090: **/3.6** (band 3.2-4.5). Basis: [L] 52.8 TFLOPS vs ~178 achievable on a 5090 (spec 209.5 FP32-accumulate dense [R] x 0.85); GeForce core ratio 5,888 / 21,760 = 0.27, bandwidth 672 / 1,792 = 0.375 [V + R].
- vs H100: /5 (4-7). Basis: LongLive 20.7 FPS @832x480 on H100 [V] vs 22.3 FPS @368x640 on 5090 [V] = H100 ~1.6x per pixel. vs 4090: /2.8 (2.5-3.5). vs A100: /4.7 (4-6). vs GB200: /22 (15-35) [E].

**Canvas pixels** (the video model also processes the known picture): canvas = 2 x (1920s x 1080s). s=1/4: 960x270 = 259k px (periphery 480x270); s=1/2: 1.04 MP (periphery 960x540); s=1: 4.1 MP (periphery 1920x1080). Cost ~ linear in canvas pixels (short attention window), so s=1/2 is x4 and s=1 is x16, attention makes it worse. Adapting-VACE's 368x640 = 235k px ~ s=1/4 (x1.10).

**Worked example, LongLive 1.3B + VACE inpaint, BF16, 4 steps:** 17.2 fps (5090) / 3.6 / 1.10 = **4.3 fps** (3.4-5.4), T = 233 ms per frame. FLOPs check: 1.3B DiT x 2 x ~253 tokens per video frame x 5 forwards (4 steps + KV refresh) x 1.3 (VACE) = ~4.3 TFLOP per frame; 4.3 x 4.3 fps = 18.5 TFLOPS = 35% of [L] 52.8: plausible utilisation.
- FP8 x1.2-1.5, 2-step student x1.67, edge-band context x1.3, NVFP4 on top x1.2 -> T falls to ~60-120 ms.
- Offline cost: 24-min episode = 34,500 frames x 0.233 s = **2.2 h at 24 fps**; 9,070 drawings = **35 min**. s=1/2 (T ~0.93 s/frame): 8.9 h / 2.3 h. s=1 (T >= 3.7 s/frame): >= 35 h / 9 h. A 2-h film is x5.

**Budget for class (a):** "a few ms" = 3 ms x (30% of 52.8 or 113.2 TFLOPS sustained) = **48-100 GFLOP** per film frame. Generator needs 2.6 TFLOP (2-step) to 4.3 TFLOP (4-step) at s=1/4: **26-90x short.** Every lever stacked (FP8 1.5 x 2-step 1.67 x NVFP4 1.2 x band 1.3) still leaves ~10-30x. A one-step image model (SDXS class, 28-47 ms) is 10x over the few-ms budget and has no temporal coherence.

**Class (c) cost:** plates/shot: 357 per 24 min; u = 0.25 plates/s x seconds per plate. Pre-pass minutes = 357 x T: SANA-Sprint 5 min, SDXL-class 9-15, klein NVFP4 18, klein FP8 30.

## 4 Comparison tables

### 4.1 Verdict by class (Setup A)

| Class | Work rate [L] | Budget per unit | Best candidate now | GPU share u | Verdict | When |
|---|---|---|---|---|---|---|
| (a) live per film frame | 23.976 frames/s | ~3 ms = 48-100 GFLOP | tiny CNN fill (row 3) or nothing | generator: 2.2-5.6 | generator 26-90x short; tiny nets cannot invent | not in the foreseeable future on this class of laptop |
| (b) streaming ahead | 6.3 new drawings/s avg (24/s worst case) | ~80-160 ms per unit at u 0.5-1 | row 6 (LongLive + VACE) | 1.47 at BF16 -> 0.45-0.7 with FP8 + 2-step + edge band | 1.5x short at the drawing rate (BF16); 5.6x short at 24 fps | ~6-12 months at the drawing rate; ~12-24 months at 24 fps |
| (c) per shot | 0.25 plates/s (4.0 s per shot) | 2-4 s per plate | rows 12-14 | 0.22-0.75 | fits; 5-30 min pre-pass per 24-min episode | **now** |
| (d) offline | - | hours | row 6 at s=1/4-1/2 | - | fits for 1.3B-class, not for 14B | **now** (ceiling, hard shots) |

### 4.2 Candidate x class (s=1/4 canvas 259k px, or 0.66 MP plate)

u = GPU share needed at playback: (a) 24 units/s, (b) 6.3 drawings/s (24/s worst case in brackets), (c) 0.25 plates/s. ok = u <= 0.5, ~ = 0.5-1.0, no = > 1.0 or cannot fit / cannot do the job.

| # | Candidate (source, date) | Measured [V] | This laptop [E] | (a) per frame | (b) streaming | (c) per shot | (d) offline | Fits 12 GB? | Licence | Anime tested? |
|---|---|---|---|---|---|---|---|---|---|---|
| 0 | Parametric glow (ADR 0026, project; brief 2026-10-12) | picture pass ~1.1 ms / refresh pair (brief); glow pass cost not given | tiny | ok | ok | ok | ok | yes | own | n/a (fallback) |
| 1 | ArtCNN C4F16 / C4F32 (2026-10-06) | 12k / 48k params | 2-7 / 8-28 ms per 1080p frame | cannot outpaint | - | - | - | yes | MIT | made for anime; no inpaint |
| 2 | DLSS 5 (NVIDIA, 2026-09) | +10 ms @1440p, +22 ms @4K [S] | n/a: engine-only, RTX 50, closed | no access | - | - | - | n/a | proprietary | no |
| 3 | LaMa (2021-09) / MI-GAN (2023) class tiny fill | speeds unverified [R] | ~10-40 ms @256-512 px [R] | u 0.24-0.96 ~ | ok (0.06-0.25) | ok | ok | yes | Apache-2.0 / MIT (+weights?) | no; blurry big holes |
| 4 | SDXS-512 one-step (2024-03) | ~100 FPS @512^2 "single GPU" | 28-47 ms | no (0.7-1.1) | ok (0.18-0.30), flickers | ok | ok | yes | n/c | no |
| 5 | StreamDiffusion img2img (2023-12) | 91.07 fps, RTX 4090 | ~30 ms | no | ok, flickers | ok | ok | yes | n/c | no |
| 6 | **LongLive 1.3B + VACE inpaint/outpaint** (Scope impl, 2026-02-16) | 17.2 FPS @368x640, 5090, 14.6 GB | BF16 4.3 fps; FP8 5.2-6.5; FP8 + 2-step 8.6-10.8 fps | no (u 5.6 BF16; 2.2-2.8 stacked) | BF16 no (1.47); FP8 no / ~ (1.0-1.2); FP8+2-step ~ (0.6-0.7); +edge band ok (0.45-0.55). 24 fps: no | n/a | ok (2.2 h/ep; 35 min drawing-rate) | as published no (14.6 GB); FP8 + T5 offload likely [E] | LongLive Apache-2.0; Scope CC BY-NC-SA; VACE weights n/c | no |
| 7 | Krea Realtime 14B (HF card, undated; numbers: Adapting VACE 2026-02-16) | 12.5 FPS @320x576 (+VACE inpaint), H100, 45 GB | ~1.8 fps | no | no | n/a | ~ (slow) | no | apache-2.0 tag (conflict) | no |
| 8 | LongLive-2.0 5B NVFP4 2-step (2026-05) | 45.7 FPS, one GB200, no VAE | ~2-3 fps | no | no; and no mask | n/a | n/a | NVFP4 ok [E] | Apache-2.0 repo | no |
| 9 | StreamDiffusionV2 1.3B (2025-11) | ~16 FPS @480p on 4x 4090 | ~2 fps [E, shaky] | no | no; no mask | n/a | n/a | tight | Apache-2.0 | no |
| 10 | TurboDiffusion recipe on Wan 1.3B (2025-12) | 81 frames in 1.9 s, 5090, 832x480 | ~18 fps @259k; ~14 with VACE-like +30% | no (1.3-1.7) | would be ok (0.35-0.45) **if** a mask-conditioned version existed: none found | n/a | ok | yes | Apache-2.0 | no |
| 11 | JoyAI-Video-Edit 16B (2026-08) | 840x480 @24 FPS, 5090 32 GB | ~6-7 fps if it fit | no | no (does not fit; edit, not outpaint) | n/a | no | no | Apache-2.0 | cartoon restyle demo |
| 12 | FLUX.2 klein 4B edit plate (2026-01-15) | <0.5 s @1 MP GB200; NVFP4 2.7x on 5080 / 5090 | NVFP4 ~3 s, FP8 ~5 s per plate | n/a | no | NVFP4 ~ (0.75); FP8 no (1.25) as live; ok as 18-30 min pre-pass | ok | NVFP4 ok (~6 GB), FP8 ~8 GB | 4B Apache-2.0 | no; no native mask |
| 13 | SDXL-Lightning (2024-02) / Hyper-SD (2024-04) + anime checkpoint + inpaint | 1-8 steps [V abs]; speeds [R] | 1-2.5 s per plate | n/a | no | ok (0.25-0.6) | ok | yes | OpenRAIL++ / per checkpoint [R] | checkpoints exist [R] |
| 14 | SANA-Sprint 1.6B (2025-03) | 0.31 s RTX 4090 @1 MP | ~0.9 s | n/a | no | ok (0.22) | ok | yes (4-bit < 8 GB) | Apache-2.0 code | no; inpaint unverified |
| 15 | Z-Image-Turbo 6B (2025-11) | sub-second H800 | ~10 s per MP at FP8 | n/a | no | no (2.5) | ok | tight | Apache-2.0 | no; Edit unreleased |
| 16 | HL-OutPaint / VACE-14B batch (2026-05) | 12.6 s/frame, A100-80GB, 720p (prior report) | ~60 s/frame | no | no | no | no: ~118 days per 2-h film | no | no code | no |

Ranked answer by class:
- **Feasible now:** (c) rows 12-14 (+3 as a cheap seam helper); (d) row 6 at s=1/4-1/2 as ceiling.
- **~6-12 months:** (b) at the drawing rate: needs a 2-step, FP8 / NVFP4, mask-conditioned causal student (VDOT++, Causal Forcing++, LongLive-Plug show the recipes; no public VACE-style checkpoint found), edge-band context, <= 6 GB resident, and a GPU-share scheme. (b) at a full 24 fps: ~12-24 months [E].
- **Not on this class of laptop in the foreseeable future:** (a) with a generator; only rows 0-3 fit.

### 4.3 Adoption risk and architectural fit

| Option | Maturity / community [V GitHub, 2026-10-12] | Breaking-change / abandonment risk | Integration complexity, fit with ADR 0014 |
|---|---|---|---|
| SDXL-class few-step inpaint + anime checkpoint | oldest, biggest ecosystem [R] | low (frozen = stable); quality ceiling older; checkpoint licences vary | low: Python worker, plain image out; ~6-8 GB [R] |
| FLUX.2 klein 4B edit | black-forest-labs/flux2 2.7k stars, push 2026-03-12; 4B Apache-2.0 | medium: fast model churn (klein Jan 2026, 9B is non-commercial); no mask path; NVFP4 on Windows unverified | low-medium: same worker; padded-canvas edit prompt |
| SANA-Sprint | NVlabs/Sana 9.3k stars, push 2026-10-12 | low-medium; weights licence unchecked; inpaint unverified | low |
| LongLive + VACE (Scope) | LongLive 2.8k stars, push 2026-10-12 (1.0 -> 2.0 moved into sub-folders); Scope 453 stars, alpha, CC BY-NC-SA; Adapting-VACE = one paper, qualitative | high: research code, alpha, NC licence, Windows unknown | high: a second continuous producer + ring buffer of periphery frames, GPU slicing |
| TurboDiffusion recipe | 3.9k stars, push 2026-08-27; README: "checkpoints and paper are not finalized"; torch 2.8 recommended, higher "may cause OOM" | medium-high | n/a until a mask-conditioned checkpoint exists |
| ArtCNN / Anime4K (upscale / denoise only) | ArtCNN active; Anime4K 21.5k stars, frozen since 2024-08 | low | low: libplacebo hook already in the picture-path spike |

Legacy / stagnant flags: CausVid (licence NOASSERTION, last push 2025-08), Self Forcing (2025-09), Rolling Forcing (NOASSERTION, 2025-10), TeaCache (2025-06), FlowLens (2024-03), LaMa (2025-02), Anime4K (2024-08, stable not dead).

Fit with "decide ahead, perform live" (ADR 0014) [E]:
- **(c):** worker emits plate image(s) + placement (+ slide track for the 2.3% of frames); planner writes plate id + strength into the cue sheet; performer samples one extra texture per monitor and cross-fades at cuts. The performer decides nothing and pays about a texture fetch.
- **(b):** adds a second continuous producer (frames at the drawing rate): ring buffer, per-drawing upload, back-pressure and stall handling inside the performer: a new stutter risk. Only worth it once the plate interface is generalised to "timestamped texture sequence".
- **(a):** neural inference inside the render loop contradicts "performer makes no decisions" and the measured rule that large GPU jobs hurt a 240 Hz player.

Design risks of the recommended path (c) [E]:
- Seam mismatch when the edge band changes inside a shot (a character walks to the edge): blend toward the glow where the live edge colour departs from the plate's seam colour (cheap per-frame colour match). X1 measures how often.
- Very short shots (p10 = 0.9 s) and bursts: reuse the previous plate when edge statistics match, else glow. Dissolves, fades, title cards, credits: shot labels from the content model decide, glow only.
- Cold start: play at once with glow, plates arrive progressively. Running ahead of the playhead: SANA / SDXL-class 0.2-0.6x real time, klein NVFP4 ~0.75x, klein FP8 ~1.25x (cannot keep up alone).
- Style drift: generic models paint photoreal texture around anime. A LoRA on an image model is laptop-scale (BFL: klein Base is the LoRA target [V]; SDXL LoRA ecosystem [R]).

## 5 What to try first (cheap, each changes a decision)

- **X1 (no GPU, ~1 h):** from the cached content model of the test episode, per new drawing compute the share of the outer 25% band that changed, and per shot whether the edge band is static throughout; also count camera slides shorter than 1 s (the 2.3% figure covers >= 1 s only). Decides whether static plates suffice: >= 70% of shots static -> (c) alone; <= 30% -> seams will show, (c)+(b) or glow-for-dynamics hybrid needed.
- **X2 (~2 h):** plate test on 10 stills from the episode padded to 32:9: SDXL-Lightning-class inpaint with an anime checkpoint, FLUX.2 klein 4B edit (NVFP4 / FP8), SANA-Sprint if it has an inpaint path. Record s/plate, peak VRAM, by-eye seam + style check via the sibling A/B harness. Rule: <= 3 s/plate = stream ahead per shot; <= 8 s = pre-pass only; > 8 s = offline only.
- **X3 (~half day, needs installs):** LongLive 1.3B + VACE inpaint (Scope or the NVlabs repo) at canvas 960x272, BF16 vs FP8: fps and VRAM; then repeat while the performer plays 240 + 96 Hz and count dropped frames. Rule: >= 6 fps at <= 6 GB with zero drops = (b) is a 2026 option at the drawing rate; 3-6 fps = wait for 2-step / NVFP4 checkpoints; < 3 fps = park (b) >= 12 months.
- **X4 (~30 min):** sustained-clock check. Run the coordinator's matmul at bf16 and FP8 for 10 min at the 120 W cap and log clocks. The 52.8 / 113.2 TFLOPS are burst numbers; every u above scales with thermal derate over a 24-min pre-pass.
- **Watch list, no work:** HL-OutPaint code; a 2-step VACE-style checkpoint; Z-Image-Edit release; NVFP4 kernels on Windows sm_120.
- **Do not:** build a live generative path; try to reach DLSS 5 / RTX VSR from the player; chase 14B-class models.

## 6 Sources (date = publication or last-push; read date 2026-10-12)

DLSS / RTX Video
- NVIDIA GeForce news, GTC announcement, 2026-03-17 [V]: https://www.nvidia.com/en-us/geforce/news/death-stranding-2-crimson-desert-dlss-4-multi-frame-gen/
- NVIDIA GeForce news, NBA 2K27 launch, 2026-09-03 [V]: https://www.nvidia.com/en-us/geforce/news/nba-2k27-dlss-5-3d-guided-neural-rendering-geforce-game-ready-driver/
- NVIDIA developer blog, 2026-09-22 [V]: https://developer.nvidia.com/blog/whats-new-for-game-developers-dlss-5-with-3d-guided-neural-rendering-nvidia-ace-updates-and-new-rtx-kit-capabilities/
- NVIDIA DLSS page [V]: https://www.nvidia.com/en-us/geforce/technologies/dlss/ · RTX Video SDK [V]: https://developer.nvidia.com/rtx-video-sdk · 50-series laptops spec table [V]: https://www.nvidia.com/en-us/geforce/laptops/50-series/
- Jon Peddie, 2026-09-02 [S]: https://www.jonpeddie.com/news/dlss-5-moves-ai-into-rendering/ · godisageek leaked build, 2026-08 [S]: https://godisageek.com/2026/08/leaked-dlss-5-rtx-5070-ti-control-fps-drop/ · TechSpot (403, snippet only) [S]: https://techspot.com/article/3170-real-dlss-5-performance · PCGH two-5090 demo [S]: https://www.pcgameshardware.de/Deep-Learning-Super-Sampling-Software-277618/News/DLSS-5-im-ersten-Hands-On-1522073/
- Criticism, 2026-03 [S]: https://www.pushsquare.com/news/2026/03/nvidia-says-youre-all-completely-wrong-about-its-controversial-ai-upscaler-dlss-5 · https://wtop.com/national/2026/03/nvidias-dlss-5-is-the-glossy-subject-of-memes-and-backlash-from-gamers/ · controls: https://www.pcgamer.com/hardware/nvidia-finally-reveals-what-controls-devs-actually-have-for-dlss-5-and-dont-worry-folks-there-are-two-main-sliders-to-control-the-ai-filter/

Anime nets
- Anime4K [V]: https://github.com/bloc97/Anime4K · shader doc [V]: https://raw.githubusercontent.com/bloc97/Anime4K/master/md/GLSL_Instructions_Advanced.md · ArtCNN [V]: https://github.com/Artoriuz/ArtCNN · mpv-AnimeJaNai [S]: https://github.com/the-database/mpv-AnimeJaNai

Streaming video generation (arXiv id, date)
- CausVid 2412.07772 (2024-12-10) · Self Forcing 2506.08009 (2025-06-09) · LongLive 2509.22622 (2025-09-26) · Rolling Forcing 2509.25161 (2025-09-29) · StreamDiffusionV2 2511.07399 (2025-11-10) · Causal Forcing 2602.02214 (2026-02-02) · Causal Forcing++ 2605.15141 (2026-05-14) · LongLive-2.0 2605.18739 (2026-05-18) · Causal-rCM 2606.25473 (2026-06-24) · LongLive-Plug 2609.38154 (2026-09-29) · ForcingBooster 2610.12625 (2026-10-08) · DynaForcing 2608.17707 · **Adapting VACE 2602.14381 (2026-02-16)**: https://arxiv.org/html/2602.14381 · EditCtrl 2602.15031 · DreamStereo 2604.12270 · VDOT++ 2610.03221 · JoyAI-Video-Edit 2608.03974 · ABot-World-0 2607.19191 · Magpie 2608.27168 · VideoCanvas 2510.08555 · FlowLens 2211.11293 (all [V] via arXiv API abstracts)
- Repos [V, GitHub API]: guandeh17/Self-Forcing · NVlabs/LongLive (+ LongLive2.0/README: GB200 table) · thu-ml/Causal-Forcing · chenfengxu714/StreamDiffusionV2 · TencentARC/RollingForcing · tianweiy/CausVid · daydreamlive/scope · jd-opensource/JoyAI-Video-Edit · thu-ml/TurboDiffusion · thu-ml/SLA · ali-vilab/TeaCache · nunchaku-tech/nunchaku · MasterHow/FlowLens · InSeokJeon/Seen_to_Scene · zhongzero/OutDreamer · lvshq/Unified-Long-Video-In-Outpainting
- Krea Realtime card [V digest]: https://huggingface.co/krea/krea-realtime-video · Decart latency claim [S]: search result 2026-10

Image models
- FLUX.2 klein blog 2026-01-15 [V]: https://bfl.ai/blog/flux2-klein-towards-interactive-visual-intelligence · repo black-forest-labs/flux2 · Tongyi-MAI/Z-Image · NVlabs/Sana · ModelTC/Qwen-Image-Lightning · Picsart-AI-Research/MI-GAN · advimman/lama [V repos]
- arXiv abstracts [V]: SANA-Sprint 2503.09641 · SDXS 2403.16627 · StreamDiffusion 2312.12491 · SDXL-Lightning 2402.13929 · Hyper-SD 2404.13686 · ADD 2311.17042 · DMD2 2405.14867 · TurboDiffusion 2512.16093 · SLA 2509.24006 · SageAttention3 2505.11594 · TeaCache 2411.19108 · LaMa 2109.07161

Local: nvidia-smi + vulkaninfo 2026-10-12 14:24 [V local]; coordinator measurements 2026-10-12 [L]; project files `spikes/picture-path/src/pp-s1-hook.c`, `plans/reports/researcher-261011-1247-performer-engine-and-picture-path.md`, `plans/reports/researcher-261010-1846-unused-canvas-area-periphery-and-outpainting.md`.

Corrections to the 2026-10-10 pass: (1) "no code seen" is outdated for Seen-to-Scene (MIT), OutDreamer (no licence), Unified Long In/Outpainting (Apache-2.0); HL-OutPaint still has none. (2) That pass's go / no-go for an overnight precompute (<= 0.25 s/frame, consumer GPU, code available) is met at 1/4 scale by a 1.3B causal model + VACE (0.23 s/frame, [E]); quality at that scale is untested, and it is not HL-OutPaint quality. (3) "No learned real-time extension models searched": now searched; only Adapting VACE qualifies. (4) VACE licence still unverified.

## 7 Unresolved questions

1. Does any mask-conditioned causal model outpaint anime acceptably? Adapting-VACE reports only mask-preservation SSIM and qualitative figures; no anime test exists anywhere I found.
2. Will LongLive + VACE (or Scope) run on Windows, sm_120, 12 GB with FP8 / NVFP4 kernels and SageAttention? Windows support of every repo is unchecked.
3. What is inside the 13.2-14.6 GB peak VRAM (T5 encoder resident or not)? The paper does not say.
4. FLUX.2 klein 4B VRAM: "~13 GB" (blog) vs "~8 GB" (README). No mask path found; does an NVFP4 edit checkpoint run on Windows?
5. Which few-step image model has a verified outpaint / inpaint path with good seams on anime? None verified (klein no mask, Z-Image-Edit unreleased in the README snapshot, SANA inpaint unverified; SDXL-class is mature but old).
6. Weight licences: VACE, LongLive-2.0 HF, SANA-Sprint, MI-GAN, Krea (Apache tag vs "non-commercial" list).
7. DLSS 5 exact cost / model size; any non-game API (none found). Per-frame FG cost in ms: not found.
8. Edge-band change rate on real anime (X1) decides whether static plates are enough.
9. Why does free VRAM differ by ~4 GB between two readings (6.2 vs 10.1 GB)? What else holds it?
10. Do the burst matmul numbers hold for 24 min at 120 W (thermal derate, X4)?
11. The u <= 0.5 ceiling and kernel-slice rules against the 240 Hz performer: other agent's measurement.
12. Ratio uncertainty: laptop vs 5090 /3.2-4.5, vs GB200 /15-35; all (b) numbers inherit +-30% or more.

Not covered: base-model quality / licences in depth, temporal stitching, GPU sharing, Windows inference runtimes (other agents); MiniMax-H3, Wan2.2-TI2V-5B mask support, TensorRT-RTX paths; any run on this laptop.
