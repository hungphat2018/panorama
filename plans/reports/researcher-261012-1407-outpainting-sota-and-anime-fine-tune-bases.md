---
title: "Outpainting SOTA (Oct 2026) and open bases for anime-only outpainting"
date: 2026-10-12
agent: researcher
extends: plans/reports/researcher-261010-1846-unused-canvas-area-periphery-and-outpainting.md
---

# Outpainting SOTA and anime fine-tune bases (2026-10-12)

Tags: **[V]** verified this pass (primary page / API / file fetched) · **[S]** search snippet or secondhand · **[R]** recalled, not verified · **[E]** my arithmetic or inference. Every GitHub / Hugging Face (HF) fact is an API read dated 2026-10-12 unless another date is given.
Budget: asked ~40 web calls; used ~60 network calls (about 25 were one-shot API probes that each read many repos). WebSearch cannot reach reddit.com (tool error), so Reddit evidence comes from the public pullpush.io archive (read-only: threads + comments). "Not found" is not "does not exist".

## 1 Executive summary

1. **Decision:** which open base(s) to fine-tune for anime outpainting, and which part of HL-OutPaint to copy. The 12 GB laptop is for inference/experiments; training needs a rented 24-80 GB GPU.
2. **Rank 1: Wan2.1-VACE (1.3B to iterate, 14B for quality), Apache-2.0 [V].** Built for masked video edits incl. outpainting, community-proven (4:3 -> 16:9 workflows exist), 1.3B fits 12 GB, 14B only via 4-bit + offload, LoRA training scripts exist (DiffSynth-Studio). The VACE lineage also owns the only real-time mask-capable adaptation found (sibling report: "Adapting VACE", 2026-02-16, LongLive-1.3B + VACE masks, 17.2 FPS at 368x640 on an RTX 5090, no training; its Scope code is CC BY-NC-SA). Weak spots: 480p/720p, 81 frames at 16 fps, colour drift on long clips, repo quiet since 2025-10-17.
3. **Rank 2: LTX-2.5 / 2.3 (22B) + Lightricks' official In-Outpainting IC-LoRA** (HF created 2026-06-17). Best tooling (official trainer config `video_outpainting_lora.yaml`, 8-step distilled, NVFP4 path on Blackwell, community runs it on 12 GB cards). **Licence trap [V]:** LoRAs/fine-tunes are "Derivatives" that must be distributed under the LTX licence; entities with >= $10M revenue need a paid licence for non-research use, and the 2026-08-11 text (LTX-2.5) adds that you may not hand a Derivative to such an entity unless it already holds one. That clashes with "free release, licence decided later". Quality/speed challenger only.
4. **Rank 3: AniSora-AnyMask** (Apache-2.0, anime-trained, Wan2.1-I2V-14B arch, spatial + temporal masks, weights 2025-10-31). The only anime-specialised mask model; outpainting never shown. Zero-shot test on rented GPU before any investment.
5. **Rank 4: FLUX.2-klein-4B (Apache-2.0) + outpaint LoRA** for per-shot background plates (an image, not a video path); anime image models (Anima, NoobAI) as low-denoise line-art repair.
6. **HL-OutPaint: still no code, weights or dataset (2026-10-12) [V].** Its strategy (global keyframes + local windows + latent swap in the first 8 of 40 steps + tiled refinement) is training-free glue; only its two LoRAs need training. Rebuild = engineering around VACE/LTX, not retraining 14B LoRAs. Its own table shows no clear win over VACE on FVD [V, prior pass].
7. **Nothing newer than HL-OutPaint (2026-05-17) found** on arXiv / HF Papers through 2026-10-12 [V]. Real movement is community tooling (LTX IC-LoRAs, Wan-VACE nodes). Correction to the prior pass: Seen-to-Scene has MIT code; OutDreamer's official code is withheld.
8. **Anime evidence gap:** no paper tests anime except one flow-based TVCG-2025 paper (closed access). Community failure modes: edge-darkening, colour drift, seams, line-weight loss, "too smooth" motion; nobody documents scene cuts.
9. **Speed:** nothing here is real time. Measured: VACE ~17 s/frame on A100 (paper) or ~3.75 s/frame on a rented 5090 (Reddit, derived [E]); LTX-2.3 three passes ~90 min on an RTX 3060 12 GB. Real time: the sibling pass (2026-10-12, real-time-generative report) found only Adapting-VACE; I add an unverified Reddit claim (Scope, 2026-03/04) of LTX-2.3 running real time on a 4090 at reduced quality, with IC-LoRA support.

## 2 Invariant + lever map

**Invariant (goal):** the real picture is never touched (ADR 0001); the generated wings (16-37% of canvas width each side, see Q8) must read as the same shot: same line weight, flat colours, palette, lighting, no invented characters, no seam, no flicker, no drift across 5-min of film or across cuts; runs locally; licence compatible with a free release.

| Lever | Options | What the evidence says |
|---|---|---|
| L1 what draws the wings | video diffusion / image diffusion per keyframe / feed-forward fill (LaMa) / real pixels from other frames | video diffusion is the only family shown on moving shots; flow-based (TVCG 2025, Seen-to-Scene) keeps real pixels but fails on big wings |
| L2 mask conditioning | native (VACE, LTX IC-LoRA) / bolt-on adapter / retrain LoRA (HL) | native exists and is Apache (VACE) or licence-encumbered (LTX) |
| L3 granularity | per frame / 5-9 s chunk / per shot / per film | community stitches 9 s chunks: colour drift + seams accumulate |
| L4 global guidance | keyframes + swap (HL) / reference image (VACE) / last-frame anchor (community) / real-pixel mosaic | HL idea targets exactly the drift problem; evidence vs plain VACE is thin |
| L5 resolution | generate low-res + upsample / tile high-res | wings are low-acuity (prior report): <=480p-tall generation is enough; tiling only if quality demands it |
| L6 training | zero-shot / LoRA / full fine-tune; self-supervised crops of real anime | official LTX trainer has this exact recipe; costs in Q8 |
| L7 licence | Apache (Wan, VACE, AniSora) / custom (LTX, Hunyuan, MiniMax) / non-commercial (FLUX.1 Fill, Anima) | decides what can be shipped as a LoRA |
| L8 where it runs | 12 GB laptop / rented GPU precompute | 12 GB = 1.3B models or 4-bit + offload of 14-22B |
| L9 repair passes | colour match, Laplacian blend, low-denoise anime-model pass, composite real frames back | each is a documented community fix, each has a failure threshold (Q6) |

Plain-English terms (once): **outpainting** = AI draws picture beyond the frame edge. **Mask** = map of pixels to keep vs invent. **LoRA** = small add-on file that teaches a big model a new skill/style without retraining it. **IC-LoRA** = LoRA that feeds a whole reference video (here the padded source) as extra input. **Distilled** = student model that needs 4-8 denoising steps instead of 30-50. **Denoising step** = one refinement pass of the generator. **NVFP4 / FP8 / GGUF Q4** = smaller number formats that shrink weights. **MoE (two experts)** = Wan2.2 swaps between two networks (early vs late steps). **PSNR/SSIM** = pixel match to ground truth (higher better). **LPIPS** = perceptual distance (lower better). **FVD** = how video-like the output set is (lower better).

## 3 Findings per question

### Q1 HL-OutPaint now

**Code status [V 2026-10-12]:** project page (koyy001.github.io) links only HF paper page, YouTube video, PDF, arXiv; no code / weights / dataset link, no "coming soon". GitHub repo search "HL-OutPaint": 0 hits. HF model search: 0. HF paper API: no linked models/spaces. arXiv comment field: "Supplementary material and video included." arXiv v1 2026-05-17, v2 05-19, v3 05-23. Licence on file: only arXiv's non-exclusive licence (paper, not code). Authors: POSTECH + Samsung Electronics Visual Display Business; SIGGRAPH Conference Papers '26. Treat as unobtainable; Samsung co-authorship suggests no release [E].

**Strategy, step by step [V: arXiv html v3, Sec 4-6, App B/C/D/G/I/J; one digest pass per chunk, numbers not re-checked against PDF].**
Backbone: Wan2.2-14B-I2V (paper's name; handling of its two experts not stated). Input = padded video + binary mask (1 = to invent) concatenated with the noise latent on the channel axis. Text prompts are used (caption source not described).

*Stage 1, Global Coarse Guidance (GCG), low resolution:*
1. Shrink padded video + mask to the backbone size (768x768 given as example).
2. Pick K keyframes evenly over the whole clip (K = max frames the model takes in one pass; training uses 13). Keyframes are encoded one by one by the VAE (frame-wise), so 13 keyframes = 13 independent images.
3. For each keyframe build a **local window**: K consecutive frames around it, stride 5 for static clips, 1 for dynamic clips.
4. Denoise the keyframe set and every window **in parallel** from noise (40 steps).
5. **Frame swap:** in the first 8 of 40 steps, after every step, each keyframe's latent is overwritten with the latent of the same frame inside its window. Plain English: the sparse "storyboard" frames lack short-range detail (a half-cropped sign); the dense window knows it, so the keyframe inherits it while the layout is still being decided. Ablation on the Long-Video set: no swap FVD 141.8; 4/40 steps 196.1; **8/40 133.2**; 16/40 211.3 (non-monotonic, so noisy). Whether the window also receives the keyframe's latent: not confirmed in my digest [unverified].
6. **Multi-scale:** insert midpoint keyframes between existing ones, re-outpaint them conditioned on the finished keyframes (the paper gives finished keyframes all-ones masks, i.e. "fully known"; this polarity is not reconciled with the Sec 5 convention "1 = outpainted" in my digest), repeat until adjacent keyframes are <= 20 frames apart. Windows are scaffolding only, never output.

*Stage 2, high resolution, guided by GCG:*
7. Rebuild the low-res video with GCG keyframes pasted in as known frames (all-ones mask, same polarity caveat); other frames stay downsampled originals; cut into **overlapping temporal tiles**, sample in parallel, **blend overlaps in latent space after every step**.
8. Upsample (bicubic) to full size, add noise to an intermediate level (SDEdit style), denoise with padded input + mask as conditions, again in **overlapping spatial-temporal tiles blended every step** (weights highest at tile centre). GCG enters twice: as condition (keyframes) and as initialisation (the upsampled result). Tile size, overlap, SDEdit start step, CFG: **not stated** [V].

*The two LoRAs:* A = builds GCG (trained on sparse frames with large random gaps, 13 keyframes + short-stride anchors); B = high-res outpainting (dense, original frame rate, normal VAE). Rank 128, lr 1e-4, velocity-prediction loss, inserted in DiT blocks.
*Training data [V App C]:* ~17,000 OpenVid-1M videos + 270 REDS videos (120 fps), all 768x768, 49 frames; masks random in position/scale/shape from top/bottom/left/right. Steps, batch, GPUs, GPU-hours: **not stated**.
*Eval [V]:* DAVIS 512^2 -> 1280x720 (90 videos), DAVIS-20 -> 1920x1080, Long-Video (20 Pexels clips, ~500 frames), Short-form (9 portrait clips); PSNR, SSIM, FVD, subject/background consistency (DINO/CLIP), LAION aesthetic score, user study n=20. Baselines: M3DDM, MOTIA, Infinite-Canvas, VACE. Runtime A100-80GB, 500 frames at 720x1280: HL 105 min, VACE 143, MOTIA 161, Infinite-Canvas 285, M3DDM 780.
*Stated limits:* not real time; extreme expansion (512^2 -> 5760^2) blurs; very long clips may outrun keyframe coverage. **Anime, cartoon, scene cuts: not mentioned.**

**What can be rebuilt from open parts [E unless tagged]:**

| Piece | Open substitute | Training cost |
|---|---|---|
| Mask-aware 14B denoiser | Wan2.1-VACE or LTX In-Outpainting IC-LoRA (already trained for outpainting) | none |
| Global-local swap + midpoint refinement | ~100s of lines in diffusers / ComfyUI; training-free | engineering only |
| Tiled latent blending (temporal + spatial) | MultiDiffusion-style blending, per-step | engineering only |
| LoRA A (sparse keyframes, large gaps) | not in VACE / IC-LoRA; test whether VACE tolerates gapped keyframes; else train | ~20-60 H100/A100-hours per 5k steps (Q8) |
| LoRA B (dense, high res) | VACE / IC-LoRA does this already | none; anime LoRA optional |
| Data (17k clips, 768^2 x 49) | anime clips (other agent) | data work |

Strategy value is unproven vs plain VACE: HL's own table shows VACE ahead on FVD (177.0 vs 202.4 DAVIS-720p; 131.7 vs 133.2 Long) and near-equal PSNR/SSIM [V, prior pass]; HL wins only its own user study (n=20). Adopt the keyframe-global part first (it targets the community's top failure, long-range colour drift); add tiling only if resolution/width demands it.

### Q2 New video outpainting work since May 2026

Search: arXiv API (abs/title "video outpainting", "outpaint*", aspect ratio, extrapolation) newest-first, HF Papers search x4, GitHub repo search. **No new video-outpainting paper after HL-OutPaint (2026-05-17) through 2026-10-12 [V; absence limited to these queries].** Adjacent only: ReCA 2026-05-26 (temporal extrapolation, ali-vilab), CameraAnything 2026-07-27 (camera/intrinsics editing), WorldWeave 2026-09-28 (persistent scene growth), "Preserving Subject-Clarity in Image Outpainting" 2026-09-05 (image). ECCV 2026 papers may not use these words: gap.

See Table C (Sec 4). **Corrections to the prior pass:** Seen-to-Scene has code (MIT, pushed 2026-05-27); OutDreamer repo exists but states "official release date yet to be determined" (company policy), unofficial reproduction linked; M3DDM+ code is Apache-2.0; VACE is Apache-2.0 [V] (was recalled); GlobalPaint still has no repo found. Seen-to-Scene facts [V html]: SVD-XT-1.1 (1.5B) + RAFT + ProPainter flow completion; 256x256 benchmarks; 12.1 s vs M3DDM 43.1 s vs MOTIA 373.2 s (clip length unstated), peak 8.8 GB; trained on 2x RTX A6000, 100K iterations, batch 1, 25-frame YouTube-VOS clips; DAVIS PSNR 21.95 / LPIPS 0.141 / SSIM 0.783 / FVD 218.8 vs GlobalPaint 20.91 / 0.154 / 0.762 / 227.8; no anime, no cuts. Anime-specific: **"Cartoon Animation Outpainting With Region-Guided Motion Inference"**, Wu, Meng, Li, Liu, Wen, Lee, IEEE TVCG 31(4):2086-2100, April 2025 (online 2024-03) [V Crossref + OpenAlex]: 4:3 anime -> 16:9, non-generative (region-guided flow, stitched guide frame, learned voting fusion); says textureless anime and exaggerated motion break natural-video flow methods; closed access, code not looked for.

### Q3 Open video foundation models as outpainting bases

Detail in Table A. Key facts:
- **VACE** [V]: Apache-2.0; Wan2.1-VACE-1.3B (2.15B params, ~81x480x832) and -14B (17.34B, ~81x720x1280) released 2025-05-14; repo last push 2025-10-17 (quiet, not archived). VACE-LTX-Video-0.9 exists but is RAIL-M and legacy.
- **Wan2.2** [V]: Apache-2.0; I2V-A14B / T2V-A14B = 27B total, 14B active (two experts); TI2V-5B (16x16x4 VAE, 720p 24 fps, README: runs on >=24 GB, e.g. 4090); repo pushed 2026-09-21. **Newer open Wan:** only Animate-2-14B (2026-07-14), Wan-Dancer-14B (2026-07-10), S2V (2025-08): none are mask/outpaint models. Wan 2.5 / 2.7 appear as hosted APIs only (HF search finds no weights; RunComfy lists "Wan 2.7 Edit-Video" [S]).
- **Wan-VACE / Fun** [V]: alibaba-pai Wan2.2-VACE-Fun-A14B (Apache-2.0, built on Wan2.2-T2V-A14B, 64 GB bf16 across two experts, GGUF by QuantStack), Wan2.2-Fun-A14B-InP (first/last-frame inpaint, not spatial). VideoX-Fun repo (Apache-2.0, pushed 2026-09-29) has LoRA/full training scripts. One Reddit commenter (2026-07-29) prefers Wan2.1-VACE over Wan2.2-Fun for long outpainting [S, single opinion].
- **LTX-2 family** [V]: open weights, 22B ("22b" in file names), LTX-2.3 (HF created 2026-03-04, 0.95M downloads) and **LTX-2.5** (HF created 2026-07-23, 1.73M downloads; licence text dated 2026-08-11). New in 2.5 (model card): native multishot (keeps character/style across cuts), diffusion video decoder, Gemma-4-12B text encoder, 8-step distilled model; frames must be 8n+1, width/height divisible by 32. NVFP4 runs on Blackwell via `--quantization nvfp4-prequant`; low VRAM: `--quantization fp8-cast --offload cpu`. **Outpaint:** Lightricks IC-LoRA `ltx-2.3-22b-ic-lora-in-outpainting-0.9` (gated, ltx licence); docs say the LTX-2.5 outpaint workflow reuses that 2.3 LoRA. Pipeline: source centred on a larger canvas, pad = mask; stage 1 low-res 8 steps, stage 2 refine 2 steps, CFG 1, Laplacian-pyramid blend back of original pixels. Prompts optional; describing the whole scene "may cause duplicated objects or people". Community outpaint LoRAs: oumoumad (2026-04-09; black-pixel sentinel, gamma-2.0 trick for dark scenes), VR-360 (2026-04/07). Anime claims: none by Lightricks; a "cel-character" IC-LoRA exists (live-action -> 2D cel, rank 32, 1152x672).
- **HunyuanVideo-1.5** [V]: 8.3B DiT, min 14 GB with offload, training code released; licence excludes EU, UK, South Korea and bars using outputs to improve other models; no outpaint path found. **MiniMax-H3** [V]: 33B dense, BF16 only, 4-GPU example, community licence, no inpaint/outpaint mentioned; unfit for 12 GB (a community ComfyUI sliding-window outpaint node exists, MIT, 2026-09-08 [sibling report]). **LingBot-Video** [V]: Dense-1.3B / MoE-30B-A3B, Apache-2.0, 2026-07-08, T2V/I2V only: watch.
- **LoRA tools** [V]: DiffSynth-Studio (Apache-2.0; LoRA + full scripts for Wan2.1-VACE 1.3B/14B, Wan2.2-VACE-Fun, Fun-InP, I2V-A14B, TI2V-5B, LTX-2 and LTX-2.3; LTX-2.5 integrated 2026-09-10, training support not confirmed; "CPU offload training" since 2026-05-18); musubi-tuner (Apache-2.0 per README, pushed 2026-10-10; Wan2.1/2.2 incl. Fun-Control and I2V, HunyuanVideo-1.5, FLUX.2, Qwen-Image, Z-Image; **no VACE, no LTX**; "24 GB+ for video training"); diffusion-pipe (GPL-3.0, 2026-09-28); ai-toolkit (MIT, 2026-10-10; Wan2.2 14B LoRA on 24 GB with 4-bit [S]); official **ltx-trainer** (80 GB+ recommended, 32 GB with INT8 low-VRAM config, Linux + CUDA only).
- **12 GB fit** (weights only, Table A [E]); Lightricks gives no VRAM numbers; community: LTX-2.3 outpaint + colourise + detail on RTX 3060 **12 GB** via Wan2GP, 3 passes ~90 min for a short clip [V Reddit 2026-04-28]; a GGUF workflow "tested with 6 GB" [V title, 2026-04-18]. Wan2GP (DeepBeepMeep) is the practical low-VRAM Windows front end (README: HunyuanVideo-1.5 from 6 GB) [V].

### Q4 Image outpainting / inpainting and fast fill

Table B. Highlights:
- Licences [V tags]: FLUX.1 Fill [dev] (11.9B) non-commercial; FLUX.2 [dev] (32.2B) and [klein]-9B non-commercial; **FLUX.2 [klein]-4B (3.88B) Apache-2.0**; **Qwen-Image-Edit-2511 (20.4B) Apache-2.0**; Qwen-Image-2.1 (7.12B, 2026-09-14) "qwen-research" licence (terms not read); **Z-Image (6.15B) Apache-2.0** (Z-Image-Edit not accessible, HTTP 401); Krea-2 (12.8B) community licence.
- Outpaint adapters that exist [V]: fal `flux-2-klein-4B-outpaint-lora` (rank 16, Apache-2.0, "fill the green spaces", 2026-01-19), Ming3D / xocialize variants, Qwen-Image-2.1-Outpaint-LoRA (ausboss, 2026-09), krea2-outpaint (2026-07), DiffSynth `Template-KleinBase4B-Inpaint`.
- **Anime image models [V]:** **Anima** (CircleStone Labs + Comfy Org, 2B, Cosmos-Predict2-2B base, millions of anime images, created 2026-01-29, Base v1.0 2026-05-20, Turbo 8-12 steps, **non-commercial licence**, 1.26M downloads); NoobAI-XL (2.57B SDXL, Fair-AI-Public-License-1.0-SD); Illustrious-XL v2.0 (OpenRAIL-M on HF; paper arXiv 2409.19946); Animagine XL 4.0 (OpenRAIL++, 2025-02); Neta-Lumina (Apache-2.0, Lumina-Image-2.0 base, 2025-08). **Anime inpaint / outpaint adapters:** NoobAI inpainting ControlNets (Wenaka, Apache-2.0, 2025-03; Acly 2025-11), Anima LLLite inpaint ControlNets (hanzogak 2026-05, kawaimasa 2026-08). No anime-specific outpaint model found.
- **Fast non-diffusion fill:** LaMa (Apache-2.0; trained at 256^2, README claims it generalises to ~2k; last push 2025-02-05, quiet); MI-GAN (ICCV 2023, MIT code and MIT weights per LICENSE-WEIGHTS [V], ONNX route, pushed 2026-09-14, trained on Places2/FFHQ). No newer feed-forward fill found by GitHub search (limited query). Not tested here; both are known to produce smooth fills on large masks [R], and our wings are 50-75% of the canvas, so they fit only as a cheap blur-plus-texture baseline, not as the answer.
- Role for panorama [E]: image models draw one plate per shot (cost amortised over many frames) but give no motion; they pair with "keep the seen" propagation (other agent) and with HL's keyframe idea (keyframes = plates).

### Q5 Anime-specific video models

- **AniSora (bilibili Index-AniSora)** [V README + HF]: Apache-2.0 (repo LICENSE + HF tag). V1 = CogVideoX-5B based, IJCAI'25 (arXiv 2412.10255), "spatiotemporal mask module" for image-to-video, frame interpolation, localised image-guided animation. V2 = Wan2.1-14B. V3 / V3.1 (2025-08-27 / 09-04) arbitrary-frame inference, 3D character video, style transfer, 360p 5 s clip in 8 s. **V3.2 (2025-09-23) trained on Wan2.2, 8 steps.** 12 GB-VRAM build of V3.1 on ModelScope (2025-09-25). **AnyMask (2025-10-31):** I2V with temporal and spatial masks, loads Wan2.1-I2V-14B-480P, 8 steps, 129 frames at 1280x720 in the example, 32.8 GB checkpoint. README shows "generation controlled by spatial masks" and "frame interpolation to repair shaking"; **no outpainting claim**.
- Maintenance: HF last modified 2025-10-31; GitHub pushes since 2026-02 are credential clean-ups and a CVPR 2026 workshop page (last 2026-07-16). Community GGUF of V3.2 exists (2026-03). Treat as quiet [E].
- Other anime-trained video models found: none maintained with mask conditioning. Related: ToonComposer / AniDoc style keyframe-to-inbetween tools (not outpainting, not checked). Community uses Wan2.2 merges (DaSiWa etc.) and anime LoRAs.
- Does an anime prior help? Two Reddit comments (2026-08-24, 2026-04-26) blame the video VAE round trip and generic "smooth" motion rather than the base choice [S, n=2]; AniSora is the only base with an anime-trained prior; no head-to-head against plain Wan was found [E].

### Q6 Community practice (fan projects, workflows) and failure modes

**Workflows and cost [V Reddit via pullpush unless tagged].**
- *widescreen-it* (mitnits; Unlicense; pushed 2026-01-05): ComfyUI + **Wan2.1 VACE**, 9 s chunks, last-frame anchor + crossfade, 16 fps + RIFE; a **4-minute clip in ~4 h on a rented RTX 5090 (64 GB RAM)**, max 720p; => ~3.75 s per generated frame, ~24 h per 24-min episode [E]. Post 2025-12-24, score 120. Paper figure for VACE on A100 is 17.2 s/frame (143 min / 500 frames, 720x1280): recipe choice swings cost ~4-5x.
- *ComfyUI-Wan-VACE-Prep* (stuttlepress; MIT; 102 stars; pushed 2026-09-30): one-node outpaint prep for VACE and LTX IC-LoRA; "best results 81 frames at 16 fps"; VACE takes only 480p or 720p.
- *LTX-2.3 IC-LoRA*: "4x3 shows -> 16x9" posts (Apr-May 2026); Wan2GP "Process Full Video" plugin chunks a whole film on low VRAM; 3 passes (colourise, outpaint, detail) ~90 min on RTX 3060 12 GB. A YouTube demo "Expanding Classic Shows from 4:3 to 16:9 with AI" (WanGP + LTX 2.3) is reported as ~10 min per 20 s clip [S, search snippet; page not fetchable, GPU unknown].
- No dedicated anime widescreen fan project found. Closest: the above live-action ones, a Scope demo where DBZ is restyled (not outpainted) [S].

**Failure modes (sources dated; anime relevance noted).**
1. **Edge-darkening continued as dark bars** when the source has vignette / dark borders (widescreen-it post, 2025-12-24). Anime: baked-in black borders, vignettes in old DVDs.
2. **Detail-rich wings blur, mismatch palette, or hallucinate** ("instruments playing without a person") (same post). Matches "invented characters".
3. **Colour drift over time:** "a white wall can turn into a brown wall at 15 s" (comment 2026-06-08); chunk stitching gives colour drift + background inconsistency (r/StableDiffusion 2026-07-29). Fixes offered: progressive colour match, reset per segment, overlap + accepted-end conditioning, reject segments whose background moved.
4. **Visible seam at the original/generated boundary in ~30% of test clips; feathering did not help** (comment 2026-06-10); LTX docs: tune Laplacian blend dilation, raise for smooth sky/gradients; "green traces can leak through the mask" [V docs].
5. **Quality loss from chunk -> 16 fps -> RIFE -> re-encode;** keep frames as images, encode once (post comments 2025-12-25).
6. **VAE round-trip thins lines and flat regions; chained clips look washed** vs the first (comment 2026-08-24, anime short author). Our picture area is never generated, but the wings are: line weight will not match the real picture [E].
7. **Black-pixel sentinel ambiguity:** LTX outpaint LoRA marks "generate here" with pure black, so genuinely dark scenes are misread; gamma 2.0 in / 0.5 out round trip [V card]. Anime has many pure-black frames and letterboxed sources.
8. **Hue shift in Wan2.2 anime generations** (desaturated green after a few frames; "very common with Wan"); causes: single low-noise expert, no speed LoRA; colour-match nodes can over-correct high-contrast anime art (r/StableDiffusion 2026-04-06).
9. **Video models too smooth / "puppet warped"** vs 2D animation timed on ones/twos/threes (thread 2026-04-25, score 23, 29 comments); LTX team reportedly says licensed training data is the bottleneck; a commenter plans a full 2D-animation fine-tune.
10. **Low-denoise anime-model repair pass:** works at denoise 0.2-0.3, **past 0.35 "each frame invents its own linework and you get boiling"**; run in 16-frame blocks with a locked seed or flicker appears at block borders (comment 2026-08-26).
11. **Flow methods vs anime:** textureless regions and exaggerated motion break optical flow (TVCG 2025 abstract [V]).
12. **Distance decay:** quality degrades as the extension grows; RunComfy page suggests ~30% steps and warns after 2 passes [S]. Our x2-x3 width (Q8) is beyond the 30%-per-pass advice.
13. **Scene cuts: no source documents behaviour** (HL, VACE, LTX docs silent). LTX-2.5 advertises native multishot for generation, not for outpainting. Per-shot processing is the safe design [E].
14. A user judgement on the LTX 3-pass film remaster: "outpainting looks bad, existing details are lost" (comment 2026-04-29, score 3) [S opinion; pipeline included colouriser + detailer].
15. Supply chain: a commenter lists RCE-class issues in Daydream Scope's unsecured plugin API (issue #756) [S]; do not run it unaudited.

### Q7 Consumer / commercial AI fill of bars

- **TV makers: nothing found.** Samsung 2026 AI TV press (VAC, AI Upscaling Pro) describes sharpening, not bar filling; Samsung support page says upscaled 4:3 shows black side bars [S]. LG / Sony / Hisense: no hit in one query. HL-OutPaint's Samsung Visual Display co-authorship is research; no product tie found. (3 searches; weak negative.)
- **Luma Reframe (Ray 3.2 API `video_reframe`)** [V docs]: source kept "frame-for-frame", new canvas filled; ratios 9:16, 3:4, 1:1, 4:3, 16:9, 21:9 only (no 32:9); **source and output <= 10 s**; output 360p-1080p; prompt required; `source_position` places the source in the canvas; cloud, per-second price; no latency published. Cloud = film uploaded, conflicts with ADR 0004.
- **Runway "Expand Video"** (Nov 2024) [S; help page 403]: cloud, clip-length limits unknown to me. **WaveSpeedAI Video Outpainter** (cloud API) [S].
- **Adobe:** Premiere "Generative Extend" is temporal (<= 2 s at launch); forum requests for vertical-to-horizontal generative fill suggest no spatial video outpaint in Premiere [S, 2024-25 coverage; 2026 status unchecked]. **Google:** Veo 3.1 / Flow: native 9:16 generation and scene extension, no outpaint found [S]. Apple: not researched.
- None is real time; all are offline, short-clip, cloud.

### Q8 Fine-tuning recipe for anime outpainting

**Mask geometry for our canvases [E]** (picture fitted to canvas height; known width fraction f = picture aspect / canvas aspect): 16:9 on 32:9 f=0.50; 16:9 on 48:9 0.333; 4:3 on 32:9 0.375; 4:3 on 48:9 0.25; 2.39:1 on 32:9 0.672; 2.39:1 on 48:9 0.448. Wings each (1-f)/2 of canvas = 25% / 33% / 31% / 37.5% / 16% / 28%; generated area = 1-f of the canvas.

**Self-supervised pairs [E, mirrors the official LTX recipe and HL's random masks]:** take a real anime shot clip (no cuts, bars cropped), keep a centred width fraction f of every frame as known, ask the model to redraw the rest, loss only on the wings. Draw f from {0.25-0.75} so all ratios are covered, sometimes off-centre (picture straddles monitors). **Catch:** a 16:9 frame cropped to f=0.5 is an 8:9 portrait crop, not a 16:9 picture on a 32:9 canvas, so the task is "extend x2 width" but framing statistics differ; no native 32:9 anime exists. Mitigate with 2.39:1 anime films (crop to 1.2:1 and 0.8:1 for f=0.5 / 0.33) as the held-out test set.
**Mask in the model:** LTX official trainer: `spatial_crop` condition = a pixel rectangle fed clean (timestep 0, excluded from loss), model generates the rest; default LoRA rank 32, lr 1e-4, **2000 steps, batch 1**, attention-only targets [V config]. HL: rank 128, lr 1e-4, mask channel-concatenated [V]. VACE / Fun: pass `vace_video` + `vace_video_mask` [V DiffSynth]. Do not use black as the sentinel (failure 7); use an explicit mask.
**Sizes reported:** HL ~17k clips (768^2 x 49) [V]; Seen-to-Scene 100K clips, 100K iterations on 2x A6000 [V]; style LoRAs on Wan2.2-14B: 20-40 clips, 3-5k steps, rank 16-32 [S Spheron]; an anime-character LoRA on 82 videos [S Reddit]. A domain LoRA on an already outpaint-trained base (VACE / IC-LoRA) should need far fewer than HL's 17k clips: hundreds to a few thousand shots [E, unproven].

**First-LoRA cost [E, arithmetic from model sizes; real step times must be measured].** Per sample = ~3.5x forward FLOPs (LoRA + gradient checkpointing); H100 40% / A100 40% / 4090 35% utilisation.

| Base, clip | s/sample H100 / A100 / 4090 | 2k steps | 5k steps | Notes |
|---|---|---|---|---|
| VACE-1.3B, 480x832x49 | 1.4 / 4.6 / 9.9 | 0.8 / 2.5 / 5.5 h | 2 / 6 / 14 h | fits 24 GB easily; laptop figure below |
| VACE-14B, 480x832x49 | 9 / 29 / 63 | 5 / 16 / 35 h | 13 / 41 / 88 h | 24 GB needs fp8 + block swap (slower) |
| Wan2.2-14B-I2V (HL-like), 768^2 x 49 | 14 / 44 / 95 | 8 / 25 / 53 h | 19 / 61 / 133 h | HL used rank 128; two LoRAs => double |
| LTX-2.5 22B IC-LoRA | no data | no data | no data | official: 80 GB+; 32 GB with INT8; ~1.6x Wan-14B compute per token [E] |

*This laptop:* sibling pass measured bf16 matmul 52.8 TFLOPS and FP8 113.2 TFLOPS on the RTX 5070 Ti Laptop (120 W cap), free VRAM only ~6-10 GB [L, coordinator run quoted in the real-time report]. At 35% utilisation that is ~31 s/sample for VACE-1.3B at 480x832x49: 2k steps ~17 h, 5k ~43 h [E]; fine for a smoke test, not for iteration; 14B+ is not trainable here.

Cross-checks [S]: Spheron (undated, rates "09 Jul 2026"): Wan2.2-14B video LoRA 3-5k steps = 20-26 h on H100 PCIe ($2.01/h), 24-30 h on A100-80G ($1.69/h), 48-72 h on a 4090 with fp8 + block swap ($0.58/h), i.e. ~$30-50 per run; fal.ai Wan2.2 trainer $0.004 per step (T2V-A14B). So **a first LoRA is tens of dollars on 80 GB or 2-3 days on 24 GB**; the real cost is data preparation, evaluation and repeated runs (5-10 runs likely [E]). 12 GB laptop: VACE-1.3B only, and only to smoke-test.

**How papers evaluate [V]:** PSNR / SSIM / LPIPS vs the true hidden pixels; FVD; subject/background consistency (DINO/CLIP) and LAION aesthetic (HL); user studies (HL n=20); benchmarks DAVIS, YouTube-VOS, Pexels long clips; mask ratios 0.125-0.66. **Not measured by anyone:** seam visibility, colour drift over minutes, line-art fidelity, cut behaviour, anime. Proposed protocol for us [E]: held-out 2.39:1 anime crops at f = 0.5 / 0.33; LPIPS + PSNR on wings only; seam score (gradient jump across the boundary vs inside); boundary Lab colour shift at t=0 and t=60 s; line-width ratio wing vs picture (edge-map stroke width); flicker (warp error between frames); blind forced-choice against the parametric blur of ADR 0008.

## 4 Comparison tables

Weights GB are [E] = params x bytes (bf16 2, fp8 1, Q4 ~0.61, NVFP4 ~0.56); activations, VAE and text encoder (umt5-xxl 11 GB bf16; Gemma-4-12B ~24 GB bf16) come on top. "12 GB" = fits RTX 5070 Ti Laptop for inference (the sibling run saw only 6-10 GB free while other jobs ran [L], so "fits" is optimistic).

**Table A, video bases** (rank = Sec 1 order; dates = last HF modify / repo push)

| Base | Params | Licence | Maint. | Weights bf16 / fp8 / Q4 or NVFP4 | 12 GB | Outpaint path | Train tools | Anime | Speed evidence |
|---|---|---|---|---|---|---|---|---|---|
| 1a Wan2.1-VACE-1.3B | 2.15B [V] | Apache-2.0 [V] | HF 2025-05-19; repo 2025-10-17 | 4.3 / 2.1 / 1.3 | yes, 480p | native mask + video; community workflows [V] | DiffSynth LoRA/full [V]; not musubi | none specific | not measured; DiffSynth: 1.3B inference min 8 GB [V] |
| 1b Wan2.1-VACE-14B | 17.34B [V] | Apache-2.0 [V] | same | 34.7 / 17.3 / 10.5 | Q4 + offload only | same, best quality per Reddit | DiffSynth [V] | none specific | 17.2 s/frame A100 720p [V paper]; ~3.75 s/frame 5090 recipe [E] |
| 1c Wan2.2-VACE-Fun-A14B | 64 GB bf16, two experts [V] | Apache-2.0 [V] | HF 2025-12-11; VideoX-Fun 2026-09-29 | 32 per expert | expert swap + Q4 (~9.7/expert) | VACE scheme on Wan2.2 [V] | DiffSynth, VideoX-Fun [V] | none specific | n/a |
| 1d Wan2.2-I2V-A14B (HL base) | 27B total / 14B active [V] | Apache-2.0 [V] | repo 2026-09-21 | 54 total | offload only | needs HL-style mask LoRAs (not released) | musubi, DiffSynth, diffusion-pipe | none | 12.6 s/frame (HL, A100) [V] |
| 2 LTX-2.5 / 2.3 22B | ~22B [V name] | LTX-2.x Community: free <$10M revenue; derivatives same licence; 2026-08-11 text adds no transfer to >=$10M entities without paid licence [V] | HF 2026-07-23 / repo 2026-10-02; 1.73M dl | 44 / 22 / 12.4 (NVFP4) | NVFP4 or fp8-cast + CPU offload; Gemma-4-12B offloaded | official In-Outpainting IC-LoRA 0.9 (2026-06-17) + trainer config [V] | official ltx-trainer (80 GB), DiffSynth (2.3 [V], 2.5 unconfirmed) | no claim; cel IC-LoRA exists | 3060-12GB: 90 min / 3 passes [V]; 4090 real-time at reduced quality [S] |
| 3 AniSora V3.2 / AnyMask | 14B-class (Wan2.1/2.2) | Apache-2.0 [V] | HF 2025-10-31; repo 2026-07-16 | ~28-33 | V3.1 12 GB build exists [V]; AnyMask needs offload/GGUF (none found) | spatial + temporal masks, I2V; outpaint untested | AniSora V1 train code; V2/V3: not checked | **anime-trained** [V] | "5 s 360p in 8 s" (V3) [V] |
| HunyuanVideo-1.5 | 8.3B [V] | Tencent: excludes EU/UK/S.Korea [V] | repo 2026-04-10 | 16.6 / 8.3 / 5 | 14 GB with offload [V] | none found | own train.py, musubi [V] | none | n/a |
| MiniMax-H3 | 33B dense [V] | MiniMax H3 community licence [V] | 2026-07/08 | 66 / 33 / 19 | no | none (card) | DiffSynth adapter, musubi | none | 4-GPU example |
| Wan2.2-TI2V-5B | 5B | Apache-2.0 [V] | 2025-08 | 10 / 5 / 3 | offload; README >=24 GB | no mask path (I2V only) | musubi/DiffSynth | none | n/a |
| LingBot-Video Dense-1.3B | 1.36B [V] | Apache-2.0 [V] | 2026-07-08, 222 dl | 2.7 / 1.4 / 0.8 | yes | none | DiffSynth [V] | none | n/a (new, unproven) |

**Table B, image bases and fillers**

| Model | Params | Licence | Date | 12 GB (bf16 / Q4) | Outpaint / inpaint | Anime |
|---|---|---|---|---|---|---|
| FLUX.2 [klein]-4B | 3.88B | Apache-2.0 | 2026-01-14 | 7.8 / 2.4 GB yes | fal outpaint LoRA (rank 16, Apache) [V]; DiffSynth inpaint template | generic |
| FLUX.2 [klein]-9B / [dev] | 9.1B / 32.2B | non-commercial | 2026-01-14 / 2025-11-22 | 18 / 64 GB no | LoRAs exist | generic |
| FLUX.1 Fill [dev] | 11.9B | non-commercial | 2024-11 (HF 2025-06) | 24 / 7 GB Q4 | native fill | generic |
| Qwen-Image-Edit-2511 | 20.4B | Apache-2.0 | 2025-12-17 | 41 / 12.4 GB Q4 (edge) | edit / outpaint prompts | generic |
| Qwen-Image-2.1 | 7.1B | qwen-research | 2026-09-14 | 14 / 4 GB | outpaint LoRA (ausboss) | generic |
| Z-Image (+Turbo) | 6.15B | Apache-2.0 | 2025-11 / 2026-01 | 12 / 3.7 GB | Fun-ControlNet-Union; Edit not accessible | generic |
| Krea-2 | 12.8B | krea-2 community | 2026-06-18 | 26 / 8 GB | outpaint LoRA | generic |
| Anima (+Turbo) | 2B | **non-commercial** | 2026-01-29 | 4 GB yes | LLLite inpaint ControlNets (community) | **anime-trained** |
| NoobAI-XL / Illustrious-XL 2.0 / Animagine XL 4.0 | 2.57B (SDXL) | Fair-AI-PL / OpenRAIL-M / OpenRAIL++ | 2024-25 | 5 GB yes | NoobAI inpaint ControlNets (Apache) | **anime-trained** |
| Neta-Lumina | Lumina-2.0 (2.6B [R]) | Apache-2.0 | 2025-08 | yes | none found | anime |
| LaMa | small CNN (tens of M [R]) | Apache-2.0 | push 2025-02 | trivial | feed-forward fill, 256^2 train | none |
| MI-GAN | small CNN (mobile-class [R]) | MIT code + MIT weights [V] | push 2026-09-14 | trivial | feed-forward, mobile | none |

**Table C, outpainting methods**

| Method | Date / venue | Idea | Numbers [V] | Code / licence (2026-10-12) | Anime | Maint. |
|---|---|---|---|---|---|---|
| HL-OutPaint | 2026-05-17, SIGGRAPH '26 | keyframe GCG + swap + tiled refine on Wan2.2-14B-I2V, 2 LoRAs r128 | 12.6 s/frame A100; FVD 202.4 vs VACE 177.0 (DAVIS-720p) | none | no | watch |
| Seen-to-Scene | 2026-04-16, CVPR'26 Findings | flow propagation + SVD latent | 12.1 s vs MOTIA 373 s (256^2); DAVIS FVD 218.8 | MIT code, 9 stars, push 2026-05-27 (SVD licence applies) | no | new, small |
| OutDreamer | TIP 2026 (arXiv 2025-06-27) | DiT, cross-clip refiner | DAVIS PSNR 20.30, FVD 268.9 | official release TBD; unofficial repro | no | unclear |
| GlobalPaint | 2026-01-10 | key-frame outpaint + interpolation | DAVIS FVD 227.8 | none found | no | unobtainable |
| M3DDM+ | 2026-01-16, IWAIT | fixes mask train/test gap | n/a | Apache-2.0, 5 stars, push 2026-04-06 | no | tiny |
| Follow-Your-Canvas / IN2OUT | 2024-09 / 2025-08 | window merge / inpaint fine-tune | FYC DAVIS FVD 242.8 | no licence / NOASSERTION; pushes 2025-08 | no | legacy |
| TVCG-2025 cartoon outpaint | 2025-04 (online 2024-03) | region-guided flow, voting fusion | n/a (closed) | not looked for | **yes, 4:3 anime -> 16:9** | academic |
| Unified long inpaint + outpaint (arXiv 2511.03272, v2 2026-09-08) | 2025-11-05 | LoRA on pretrained T2V, overlapping windows, high-order co-denoising | 81-800 frames; beats Wan2.1 variants + VACE "on several metrics" | Apache-2.0 code, push 2026-10-05 (found by sibling pass; I did not open it) | no | active |
| Adapting VACE (sibling report) | 2026-02-16 | VACE masks on causal LongLive-1.3B, no training | 17.2 FPS @368x640 RTX 5090, 14.6 GB; drifts after ~100 frames | Scope impl CC BY-NC-SA, alpha | no | alpha |
| VACE (as outpainter) | 2025-05 | all-in-one editor | 143 min / 500 fr A100 | Apache-2.0 | none | quiet |
| LTX In-Outpainting IC-LoRA | 2026-06-17 | IC-LoRA two-stage + Laplacian blend | 8+2 steps, CFG 1 | LTX licence, gated | none | active |

## 5 What to try first on this laptop (decision-changing only)

1. **E1, zero-shot bake-off (about 1 day).** 10 anime shots x 5 s at 480p; make known/unknown by cropping real frames to f=0.5 and 0.333. Run (a) VACE-1.3B bf16, (b) VACE-14B GGUF Q4/Q5, (c) LTX-2.3/2.5 distilled + In-Outpainting IC-LoRA (NVFP4, or fp8-cast + CPU offload; Wan2GP or ComfyUI). Log s/frame and peak VRAM; score wings with the Q8 protocol; blind forced-choice against the ADR 0008 blur. *Decides:* rank 1 vs 2, whether fine-tuning is needed at all, and whether any base is even near a precompute budget.
2. **E2, plate path (about 2 h).** 20 anime keyframes at ~1 MP with FLUX.2-klein-4B + fal outpaint LoRA; then Anima / NoobAI at denoise 0.2-0.3 as line-weight repair. *Decides:* whether the image-plate branch deserves a design.
3. **E3, local training smoke test (about 1 h).** 20-step VACE-1.3B LoRA with DiffSynth CPU-offload training at 480x832x49: s/step and peak VRAM. *Decides:* local vs rented training and the [E] estimates in Q8.
4. **E4, dark-frame check (30 min).** LTX outpaint on anime night scenes and on frames with real black borders, with and without the gamma 2.0 trick. *Decides:* never use black as the sentinel in our own training format.
5. **E5, only if E1 is "good, not great" (rented 80 GB, 1-2 h, a few dollars).** AniSora-AnyMask zero-shot on the same 10 shots. *Decides:* rank 3 stays or is dropped.

Skip: HL-OutPaint reproduction, 14B LoRA training, Scope, until E1 says the base is worth it.

## 6 Sources (fetch/API date 2026-10-12 unless noted)

- Sibling reports read for cross-checks: plans/reports/researcher-261012-1407-real-time-generative-models-and-neural-rendering.md (Adapting VACE, 2511.03272 code, laptop TFLOPS); prior pass plans/reports/researcher-261010-1846-unused-canvas-area-periphery-and-outpainting.md.
- HL-OutPaint: page https://koyy001.github.io/Publications/hl-outpaint · https://arxiv.org/abs/2605.17543 (v1 2026-05-17, v3 2026-05-23) · https://arxiv.org/html/2605.17543v3 · https://huggingface.co/api/papers/2605.17543 · prior pass report (2026-10-10).
- Seen-to-Scene https://arxiv.org/html/2604.14648 (2026-04-16), https://github.com/InSeokJeon/Seen_to_Scene · OutDreamer https://github.com/zhongzero/OutDreamer · M3DDM+ https://github.com/tamaki-lab/M3DDM-Plus · TVCG paper DOI 10.1109/TVCG.2024.3379125 (Crossref, OpenAlex).
- LTX: https://github.com/Lightricks/LTX-2 (LICENSE, LICENSE-2 dated 2026-01-05, LICENSE-2_x dated 2026-08-11, packages/ltx-trainer/configs/video_outpainting_lora.yaml, docs/training-modes.md) · https://huggingface.co/Lightricks/LTX-2.3-22b-IC-LoRA-In-Outpainting (created 2026-06-17) · https://huggingface.co/Lightricks/LTX-2.5 · https://docs.ltx.io/open-source-model/advanced-workflows/in-outpainting · https://huggingface.co/oumoumad/LTX-2.3-22b-IC-LoRA-Outpaint (2026-04-09).
- Wan / VACE / Fun: https://github.com/Wan-Video/Wan2.2 · https://github.com/ali-vilab/VACE · https://huggingface.co/alibaba-pai/Wan2.2-VACE-Fun-A14B · https://github.com/aigc-apps/VideoX-Fun · HF cards Wan-AI/Wan2.2-I2V-A14B, Wan2.2-TI2V-5B, Wan2.1-VACE-1.3B/14B.
- Trainers: https://github.com/modelscope/DiffSynth-Studio (README, docs Wan.md) · https://github.com/kohya-ss/musubi-tuner · https://github.com/tdrussell/diffusion-pipe · https://github.com/ostris/ai-toolkit.
- AniSora https://github.com/bilibili/Index-anisora · https://huggingface.co/IndexTeam/Index-anisora · arXiv 2412.10255.
- Other models: HunyuanVideo-1.5 repo + LICENSE · https://huggingface.co/MiniMaxAI/MiniMax-H3 · Robbyant/lingbot-video-* · HF cards for FLUX.2-klein, FLUX.1-Fill-dev, Qwen-Image-Edit-2511, Qwen-Image-2.1, Z-Image, Krea-2, Anima (circlestone-labs), NoobAI-XL, Illustrious-XL, Animagine-XL-4.0, Neta-Lumina · LaMa, MI-GAN repos (LICENSE-WEIGHTS).
- Community (pullpush.io archive of Reddit): 1pup4tw widescreen-it (2025-12-24); 1vaa4am long-video tools (2026-07-29); 1sygmfa LTX remaster RTX 3060 (2026-04-28); 1t7l9ce Hound of the Baskervilles (2026-05-08); 1seeem7 + 1sneaok Wan-VACE-Prep (2026-04); 1sot3w6 LTX low-VRAM (2026-04-18); 1svrzzt 2D-animation fine-tune thread (2026-04-25); 1sdzt08 Wan2.2 anime hue shift (2026-04-06); 1vwll68 anime I2V workflows (2026-08-23); 1s5i1vc + 1ss8z7r Scope real-time LTX (2026-03-27 / 04-22). URLs: reddit.com/r/<sub>/comments/<id>/.
- Costs: https://www.spheron.network/blog/fine-tune-flux2-wan-lora-cost-gpu-cloud-2026/ (undated, rates 09 Jul 2026) · fal.ai Wan2.2 trainer page (search snippet).
- Commercial: https://docs.agents.lumalabs.ai/guides/videos/reframing · Runway help article (HTTP 403, snippet only) · Samsung 2026 AI TV press pages (search) · Adobe Generative Extend coverage (2024).

## 7 Unresolved questions

1. HL-OutPaint: does the swap copy window -> keyframe only, or both ways? Tile size, SDEdit start step, CFG, GPU-hours: absent from the text I read; PDF not re-checked.
2. Does plain VACE tolerate gapped sparse keyframes, or is HL's LoRA A essential? (E1 variant.)
3. LTX trainer: step time and cost for the outpainting recipe; LTX-2.5 vs 2.3 quality of the 0.9 In-Outpainting LoRA (docs say reused).
4. Legal reading of LTX "Derivative" for a freely hosted LoRA (3.5 transfer clause): not legal advice; needs a human read before choosing it.
5. Any anime ground truth for x2-x3 width: 2.39:1 anime films are the only candidate; how many titles / how clean?
6. Wan2.2-VACE-Fun vs Wan2.1-VACE quality for outpainting: only one opinion found.
7. Qwen-Image-2.1, Krea-2, MiniMax-H3 licence texts not read (tags only).
8. Real-time LTX claim (Scope, 4090): resolution, fps, quality unknown; hand to the streaming-diffusion agent.
9. Whether TVCG-2025 cartoon outpainting has code or data.
10. Consumer-feature search was thin (Samsung 3 queries, LG/Sony 1, Apple 0, Runway page 403).
11. Reddit coverage is the pullpush index plus keyword luck; no anime-specific outpaint thread found does not mean none exists. YouTube showcases were not readable.
12. Speed of VACE-14B GGUF / LTX NVFP4 on the RTX 5070 Ti Laptop: unmeasured (E1).
13. Unboxed (CVPR 2025): still not found. 2511.03272 code and the Adapting-VACE numbers come from the sibling report, not re-opened by me.
14. MI-GAN weights are MIT per LICENSE-WEIGHTS [V]; the sibling report listed them as unchecked (resolved here).
