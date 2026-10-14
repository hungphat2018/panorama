---
title: "Specialist student for the anime wings: architecture, start checkpoint, laptop compute"
date: 2026-10-13
author: researcher
extends: researcher-261012-1407-real-time-generative-models-and-neural-rendering.md ; researcher-261012-1407-outpainting-sota-and-anime-fine-tune-bases.md
cross-read: kongming-261013-1049-real-time-architecture-counsel.md (same hour, no web) ; memory notes of the sibling passes (training, fast recipe, perception budget)
---

> Saved by the parent session from the agent's returned text (the agent's harness forbade writing report files), 2026-10-13.


Tags: **[V]** verified this pass (API, config, source file, abstract, repo) · **[V~]** WebFetch digest of an HTML paper (numbers digest-level, not re-checked on the PDF) · **[S]** snippet / secondhand · **[R]** recalled, not verified · **[E]** my arithmetic or inference · **[L]** measured on this laptop by others.
Budget: ~75 pulls (15 page fetches, 2 searches, ~58 arXiv/HF/GitHub API or raw-source reads). No GPU, no download, no install.
Terms (once): **wing** = one generated side; **update** = one new picture, both wings. **token** = one cell of the latent grid the transformer handles; cost grows with token count. **p** = pixels per token side (p=16: 256 px/token; p=32: 1,024). **f32t8c128** = autoencoder shrinks each image side 32x, time 8x, 128 numbers per cell. **DiT** = diffusion transformer. **step/NFE** = one network pass. **prefix + KV cache** = known tokens placed before the noisy ones, their keys/values stored once, later steps only read them. **GDN/linear attention** = attention with a fixed-size running memory (cost linear in tokens, constant memory); **hybrid** = mostly such layers plus some full softmax layers. **u** = share of this GPU (u=0.5: half). **eta** = share of matmul peak actually reached.

## 1 Decision + executive summary

**Decision**
1. **Shape:** a *wings-only, prefix-fed, <=2-step, hybrid-attention DiT on a >=32x latent.* Only the wings are denoised. The real picture never becomes extra noisy tokens: it enters once per update as a one-way KV-cached prefix (64-token thumbnail + two seam bands at fine tokens + previous wings + per-shot coarse plate). 3:1 linear:softmax layers, 4 then 2 steps, mixed FP8/NVFP4, tiny anime-tuned decoder.
2. **Start:** animator student **A = `Efficient-Large-Model/SANA-Streaming` 2B** (Apache-2.0 tag, weights 2026-06-14), fine-tuned for outpaint + anime. **B = 0.3-0.9B drawing-gated wing specialist** (target for s=1/2 and 3 screens), made by pruning/distilling A; recipe anchor MobileI2V 270M. **C = DC-adapted Wan2.1-VACE-1.3B** is the fallback. **Qwen-Image-2.1 is cut by function, not by layers:** copy its prefix design (already native), drop the VLM encoder, keep the 7.1B body as painter/teacher (Turbo 8-step), prune only for plates. Do not graft temporal layers onto Qwen; do not merge painter and animator.
3. **Speed on this laptop [E, FLOP model calibrated on 4 measured points]:** A at s=1/4: u=0.73 (BF16) as published; 0.37-0.56 wings-only at 2-4 steps; x0.7 with mixed precision; at s=1/2 u>=1.2: no. B on a 144-token grid (s=1/4 f32 or s=1/2 f64), 2 steps: 0.27B u=0.25, 0.6B u=0.56, 0.9B u=0.85 at 15 updates/s. Painter today is u=42 (170 s x 0.25 plates/s); the ladder in 4.3 brings it to ~0.2-0.9.
4. **Order of work:** kongming's R1 (CPU cel-compositor demo) comes first. If the user accepts it, the student shrinks to a band refiner (B-band, u=0.1-0.35 at 3 Hz) plus the plate painter. If not, B is the drawing-rate fallback priced here. The architecture is the same either way.

**Executive bullets**
- The 2,900x gap closes through **tokens (11x), steps (25x), temporal packing (2x), params (7x), CFG (2x)**, in that order of importance (table 2.2). Params alone give 7x. Miss either tokens or steps and it does not close.
- **Cheapest correct way to give the picture:** wings-only noisy tokens + one-way prefix (thumbnail, seam bands, previous wings, plate). 1.5-1.8x fewer token-forwards than a full canvas under the same refresh policy [E]; the KV cache alone gives nothing at 1 step and 1.3-1.6x at 2-4 steps. Qwen-Image-2.1 ships this design (`causal_condition`, t=0 prefix) [V source].
- **A real base exists now.** SANA-Streaming: 2B, Apache-2.0, 4 steps, LTX2 32x32x8 latents, source video by channel concat, 24 end-to-end FPS at 1280x704 on an RTX 5090 [V~]. It is not mask- or outpaint-trained and not anime. This pulls streaming (b) from "6-12 months" to "one fine-tune plus a kernel check on Windows sm_120" [E].
- **Latent:** DC-AE-V f32 (HF 2026-09-30) or LTX2; adapting a pretrained video DiT costs ~10 H100-days at 14B [V~]. An anime-specific AE is sound but second-order: measure round-trip first, fix with decoder-only distillation (~$95-class, Turbo-VAED) before touching an encoder, because a new encoder forces re-alignment of the DiT.
- **Surgery evidence:** depth pruning -50% params with <3% metric loss (PPCL, Qwen-Image 60 layers), 2B->0.9B with KD (Taming), 270M specialist (MobileI2V). No anime-outpaint evidence for any small model exists anywhere I found.
- **Foveated/multi-resolution tokens** cut tokens ~2x (images, 26% tokens) to 3.5x (video, 38%) near parity [V~]; 244 vs 510 wing tokens at s=1/2 [E]. Band sufficiency at 50-67% masks is untested: test T2.
- **Risks:** no anime-quality evidence; MPQ kernels unreleased and GDN kernels pinned to torch 2.10/triton 3.6; tiny models are latency-bound (u up to 2.3x worse); full LTX2 VAE costs as much as the DiT; Qwen Research Licence + LTX2-derived VAE licence.

## 2 Invariant, compute model, lever map

**Invariant:** the real picture is never altered (ADR 0001); the wings continue lines, fills, colour, composition outward, never close the picture; seam invisible; side changes slaved to picture changes; local only; generator resident <=6 GB; GPU shared (u 0.5-0.75). Not required: causality (the film's future is known; chunk-bidirectional inside a shot is allowed). A's streaming form is chosen for constant memory, not liveness. A bidirectional SANA-Streaming checkpoint also exists.

**2.1 Compute model [E]** cost per update = token-forwards x (2 x P_tok + 4 x L_softmax x d x context). Token-forwards = steps x noisy + prefix once + 1 refresh pass of the clean output. Effective rate: bf16 52.8 x eta 0.35 = **18.5 TFLOPS**; mixed FP8/FP4 x1.4 = **25.9** (SANA-Streaming MPQ gave 1.30x over fused-GDN BF16, 1.59x over plain BF16 [V~]; kongming's x1.8 is optimistic). Budget at u=0.5-0.75: **9.2-13.9 TFLOP/s (bf16), 13-19.4 (mixed)**.
Calibration, 4 independent points (eta 0.30-0.40, treat as +-30%): (i) A14B animator 1,590 tok x 28 GFLOP x 50 steps x CFG / 4 frames = 1,113 TFLOP/film frame -> 60 s predicted vs 54-55 s measured [L] (attention ignored); (ii) Wan2.1-VACE-1.3B 4-step at 24 fps, s=1/4: u=5.8 -> 4.1 fps vs 4.3 fps in the previous report (from the paper's 17.2 FPS on a 5090); (iii) SANA-Streaming BF16 35.8 DiT FPS on a 5090 = 63 TFLOPS = 30% of 209.5 [V~]; (iv) kongming's plate anchor 9.6k tokens x 25 steps x 7.1B = 20 TFLOPS [S], sibling (b) 17 TFLOPS.
Caveats: (a) below ~0.5B params the GPU is latency-bound: MobileI2V 2-step 0.23 s on an A100 implies ~13 TFLOPS (4% of peak) [E from V~]; multiply u by up to 2.3 unless CUDA graphs/fusion. (b) **VAE is a separate bill:** SANA-Streaming BF16 on a 5090: VAE enc+dec 23.9 s vs DiT 26.8 s per minute (47% of end-to-end) [V~]; full LTX2 VAE at s=1/4 here ~26 ms/frame = u 0.6 at 24 fps [E]. Band-only encode + wings-only decode + tiny decoders ~0.1 [E]. (c) 5090->laptop ratio /3.6 (3.2-4.5) from the previous report. (d) Offload is not the A14B's main cost: 54 s matches pure compute.

**2.2 Gap stack, animator** (teacher 1,113 TFLOP/film frame -> target <=0.385 at u=0.5, 24 fps = **2,890x**; A at s=1/4 wings+prefix, 2 steps = 0.285 -> **3,900x** [E])

| Lever | x | Evidence |
|---|---|---|
| remove CFG | 2 | standard in every distilled student (CausVid, AAPT, SANA-Streaming cfg 1) |
| 50 -> 2 steps | 25 | Taming 20x vs 40-step CFG; AAPT 1 NFE; Causal Forcing++ frame-wise 1-2 steps [V abs] |
| tokens/latent frame 1,590 -> 144 (32x latent, s=1/4, wings only) | 11 | DC-VideoGen 6.2-14.8x end-to-end; Taming VAE 21x faster |
| prefix + refresh overhead | 0.5 | [E] 568 vs 2x144 token-forwards |
| params 14B -> 2B hybrid | 7 | SANA-Streaming 2B; DC-VideoGen 1.3B adapted beats 14B on VBench |
| frames per latent frame 4 -> 8 | 2 | LTX2/DC-AE-V t8 (cost: holds, cuts) |
| precision (not in the 3,900) | 1.3-1.6 | SANA-Streaming MPQ |

**2.3 Levers (mine):** tokens: latent f16->f32->f64, wings-only, band+thumbnail, foveated, drawing-gating. Steps: CFG off, distill, refresh pass. Model: params, hybrid attention, encoder out. Hardware: mixed precision, fused kernels/CUDA graphs, tiny decoders. Roles: plate = per-shot cached prefix; teacher/student split. Out of scope (other agents): training data/recipes, compositor/warping, GPU sharing, training-free speedups.

## 3 Findings

### 3.1 Q1 Known-region efficiency
| Pattern | How the known region enters | Reported | Use here |
|---|---|---|---|
| Mask channels + parallel context blocks (VACE, Adapting VACE) | known latent+mask as channels; 15 context blocks run on every canvas token | +29-30% latency; 17.2 FPS@368x640 5090 [V, prior report] | baseline; no saving |
| Channel-concat source (SANA-Streaming 2605.30409, 2026-05-28) | source encoded separately, concatenated on latent channels: 0 extra tokens | 1280x704 24 e2e / 58 DiT FPS (MPQ); BF16 35.8 DiT FPS [V~] | simplest; picture tokens still computed |
| One-way prefix + KV cache (EasyControl 2503.07027) | condition branch cannot see noisy tokens; K/V reused; condition fixed at 512^2 whatever the output | FLUX 1024^2: 22.4->16.3 s (1 cond), 29.9->18.3 s (2) [V~] | yes |
| OminiControl2 2503.08280 | compress condition tokens, compute them once | -90% condition overhead; 5.9x multi-condition [V abs] | yes |
| FLUX.2 klein-9B-KV (HF card; diffusers `pipeline_flux2_klein_kv.py`) | ref tokens self-attend only; post-RoPE K/V cached at step 0, steps 1-3 reuse; 4 steps | up to 2.5x multi-ref edit; ~29 GB; non-commercial [V] | pattern proof, too big |
| **Qwen-Image-2.1** (`causal_condition: true`; diffusers `transformer_qwenimage21.py`) | joint sequence [text | cond images | target]; text strictly causal, each image block bidirectional inside; prefix tokens take the t=0 modulation row so their K/V never depend on t; `use_kv_cache=True` default | "significant speedup" (no number); Turbo 8-step 2026-10-09 [V] | the template; already in your painter |
| FullDiT2 2506.04213 | dynamic condition-token selection + selective context caching | 2-3x per step [V abs] | band/thumbnail = hand-made selection |
| EditCtrl 2602.15031 (2026-02-16) | blocks run on masked tokens only; 256^2 background thumbnail as global embedder | edits 17.4 vs 76.3 PFLOPs, 4.67 vs 0.66 FPS vs VACE-1.3B; PSNR 24.16 vs 23.84; outpaint and masks >50% not evaluated [V~] | wings are 50-67% of canvas: gain shrinks |
| LiveEdit 2606.26740 | AR mask cache reuses region computation across frames | 12.66 FPS [V abs] | change-gating idea |
| RAS 2502.10389 / ToCa 2410.05317 / AViTS 2608.17995 | skip tokens/steps, training-free | 2.36x/2.51x (SD3/Lumina); 2.36x/1.93x (OpenSora/PixArt); AViTS 6.3x FLUX, ~9x FLOPs Qwen-Image-Edit [V abs] | stack last, quality risk |

- **Break-even [E]:** known/wing tokens = 1.0: cache gain 1.00x (1 step), 1.33x (2), 1.60x (4), 1.92x (25). Known = 0.25: 1.0/1.11/1.18/1.24. So the cache pays only with several steps; dropping picture tokens and shrinking the prefix gives the rest.
- **Cheapest correct:** noisy wings + one-way prefix. s=1/2, p=32: 4 steps 5,100 token-forwards (full canvas + refresh) vs 2,886; 2 steps 3,060 vs 1,866 [E]. "Correct" also means the seam bands stay at fine tokens, the real centre is pasted back, and no learned path touches picture pixels.
- **No-retrain variant:** per-side strips (wing + 1/8-1/4 picture band) as two narrow videos through the existing channel-concat net: 23-37% fewer tokens than the full canvas [E].
- Qwen-2.1's text tokens come first and are causal, so a fixed prompt's K/V is a constant. But its VLM also reads the condition image (pipeline `<image1>` template [V source]), so "cache the text embedding" is exact only if the VLM sees text alone: test T2.

### 3.2 Q2 Compact and real-time video DiTs (px per token side x film frames per latent frame)
| Model (date, id) | Params | Latent | Steps | Speed @ GPU | What bought it | Licence |
|---|---|---|---|---|---|---|
| MobileVD 2412.07583, SnapGen-V 2412.10494, AMD-Hummingbird 2503.18559 (2024-12/2025-03) | 523M UNet; 0.6B; 0.9B | UNet-era | 4 (adv.) | 523x fewer FLOPs than SVD; 5-s video in 5 s iPhone 16 PM; 31x vs VideoCrafter2 [V abs] | pruning, adversarial step distill | n/c; Hummingbird code AMD |
| LTX-Video 2501.00103 (2024-12) | 2B [R] | 32x8, patchify in VAE | n/s | 5 s 24 fps 768x512 in 2 s H100 [V abs] | 1:192 VAE | OpenRAIL-type [R] |
| AAPT 2506.09350 (2025-06) | 8B MMDiT | 8x4 | **1** | 736x416 24.8 FPS 1xH100 (0.16 s/latent frame); 1280x720 8xH100 [V~] | adversarial post-train, KV cache window 30 latent frames, input recycling; 7 d on 256 H100 | no weights found |
| Taming mobile DiT 2507.13343 (2025-07) | 2B->915M | 32x8, z=256 | 4 | 49x384x512 ~15 FPS iPhone 16 PM (668 ms/step) [V~] | VAE (21x faster decode), tri-level pruning+KD, adv. distill | n/c |
| Next-Frame Diffusion 2506.01380 | 310M | n/s | few | >30 FPS A100 [V abs] | consistency distill + speculative sampling | n/c |
| MobileI2V 2511.21475 (2025-11) | 270M, 14 linear+2 softmax | 32x8 LTX VAE + Turbo-VAED | 2 | 720p 17 f (2,760 tok) 0.23 s A100 [V~] | hybrid attention, step+decoder distill; 24 V100 x 1 week | Apache-2.0 code+weights |
| SANA-Video 2509.24695 / LongSANA | 2B | 8x4 (Wan2.1 VAE) | 4 | 16x faster than Wan2.1-1.3B; NVFP4 71->29 s @5090; LongSANA 27 FPS (GPU n/s) | block linear attention, O(1) memory | Apache-2.0 |
| **SANA-Streaming** (2026-05) | 2B: 15 GDN+5 softmax, d 2240 | 32x8 LTX2; 880 tok/latent frame | 4 | 24 e2e / 58 DiT FPS 5090 (MPQ), BF16 35.8; 5.56 GB [V~] | hybrid, fused GDN x1.22, MPQ x1.30, channel concat | Apache-2.0 tag |
| SANA-Video 2.0 2607.21553 (2026-07) | 5B/14B hybrid 3:1 | 32x8 LTX2 | 4 (DMD preview) | 5B 720p 5 s 13.06 s H100 w/ Sol-Engine [V abs] | 25% softmax optimum, attention residuals | Apache-2.0 |
| MobileWan 2607.06173 (2026-07) | 4.7B (Wan2.2-5B, 23% heads pruned) | 16x4, 390 tok/latent frame | 3 | 5 s 480x832 in 20 s, Snapdragon 8 Gen 5 NPU; human pref baseline 53% vs 22% [V~] | recurrent causal linear attention (30k+10k its), head pruning | checkpoint released |
| CineMobile 2607.03803, S2DiT 2601.12719 | pruned Wan2.1; n/s | n/s | 4; few | 40x vs teacher, <1 GB; >10 FPS iPhone [V abs] | distill-guided pruning+PTQ; 2-in-1 distillation from Wan2.2-14B | n/c |
| LingBot-Video 1.3B (2026-07-08) | 1.36B, 24 layers d 2048 | 16x... Wan2.1 VAE | n/s | n/s | none | Apache-2.0 [V] |

Design choice -> measured multiplier: VAE 4x16x16 -> 8x32x32: VAE decode 21x (Taming, A100); Wan 8x8x4 -> DC-AE-V f32t4: 6.2x (480p) to 14.8x (2160p) end-to-end (DC-VideoGen, H100); 40-50 -> 4 steps: ~20x (Taming) / 10x (MobileI2V 2-step); 4 -> 1 NFE: AAPT; pruning 2B->0.9B: -1.64 VBench with KD, -5.1 from scratch (Taming); hybrid vs all-softmax: 3.7x at minute length (SANA-Streaming), 1.7x on V100 at +0.5 FVD (MobileI2V: 4.64 vs 7.88 s); mixed FP8/FP4 + fused kernels: 1.59x (SANA-Streaming). Ranking by effect: **tokens (VAE) > steps > hybrid attention > params > precision.**

### 3.3 Q3 Latent compression
| Autoencoder | f x t, ch | ratio | PSNR [V~] | Tiny decoder |
|---|---|---|---|---|
| Wan2.1 VAE (Wan2.2-A14B, Qwen-Image v1) | f8 t4 c16 | 48 | 34.15 (Panda70M) | TAEW2.1 |
| Wan2.2 VAE | f16 t4 c48 | 64 | 33.71 (512p images; FLUX VAE f8c16 34.77) | taew2_2(+super); Turbo-VAED-Wan2.2: 1.95x, -0.7 dB, -72% FLOPs |
| Qwen-Image-2.1 VAE (HF config) | f16 t8 c64 | 96 | n/s | none; config says 3D-causal, unused for video |
| LTX VAE | f32 t8 c128 | 192 | 32.41 | taeltx2_3 (blurry, issue #20), taeltx2_3_wide |
| DC-AE-V f32 t4 c32 / c64 / c128 | | 384/192/96 | 33.07 / 35.03 / 37.37 | none |
| DC-AE-V f64 t4 c128 | | 384 | 32.79 | none |
| DC-SAE (2609.39222, 2026-09-30) image f32 | | | 29.79 on ImageNet 512 (13.5% above DC-AE) | |
DLFR-VAE 2502.11897 (training-free adaptive latent frame rate) and H3AE 2504.10567 (real-time mobile decode) [V abs].
- **Adapting a pretrained DiT to a 32x-64x latent:** DC-VideoGen: 10 H100-days for 14B, 1.3B recipe 20K patch-embed + 4K head + 20K LoRA(r256) steps on 16 H100; VBench 84.63 vs 83.38 (1.3B), 84.83 vs 83.73 (14B) [V~]. DC-Gen (ECCV'26): 31 H100-days for FLUX.1-Krea, 4K 53x; repo (426 stars, push 2026-10-01) has guides for Wan2.1-T2V-1.3B, T2V/I2V-14B, **Qwen-Image-Edit**, FLUX, Z-Image (not VACE); HF `DC-Gen-Qwen-Image-Edit-Res1K` 2026-08-22 [V].
- **Line art / anime: no measurement exists** in what I could reach. Indirect: Wan-Image VAE recovers "thin strokes" through text-data training [V~]; an SDXL anime VAE reports slight loss of fine detail [S]. PSNR losses are the only numbers: f16 costs ~1 dB vs f8; f64 costs ~4.6 dB vs f32 (natural video).
- **Is an anime AE a sound lever? Yes, in this order:** (1) round-trip 30 anime frames through off-the-shelf AEs (test T3); (2) if lines blur, **decoder-only distillation** on anime (Turbo-VAED: $95 on one RTX 4090, up to 84.5x faster, 96.9% quality; Neodragon "asymmetric decoder distillation" keeps the latent space [V]); (3) only if the *encoder* loses lines retrain it, which forces a second DiT alignment. Choose the AE before the DC-style alignment so it is paid once.

### 3.4 Q4 Model surgery
| Evidence | Result | Cost / note |
|---|---|---|
| PPCL 2511.16156 (CVPR'26), OPPO-Mente-Lab/Qwen-Image-Pruning | -50% params, <3% metric loss; Qwen-Image 60->40 layers (12-13.6B) [V] | Apache-2.0, 92 stars; 20B-era checkpoints |
| baseten Qwen-Image-2512 50-block | 10 of 60 blocks fused into neighbours, 1.2x, +0.15 dB vs cold deletion [V] | 133 steps, 2 h on one B200, 1,120 coefficients |
| TinyFusion 2412.01199 | DiT-XL depth-pruned 2x at <7% of pretraining [V abs] | NOASSERTION licence |
| Taming mobile | 2B->915M: 8 blocks, 12 heads, 25% FFN; VBench 81.45 vs 79.00 w/o KD vs 76.17 scratch [V~] | human pref still favours server 59/41 |
| SnapGen 2412.09619 | 379M from scratch with cross-architecture KD beats SDXL on GenEval/DPG; 1024^2 in 1.4 s on a phone [V abs] | general T2I |
| DC-VideoGen | 1.3B adapted 84.63 VBench > Wan2.1-14B 83.73 (T2V 720p) | VBench is crude |
| MobileI2V | 270M narrow-domain (landscape+face) VBench 0.869 vs LTX-Video 0.872 [V~] | 330K clips, 24 V100 x 1 week |
| Anima 2B (HF, 2026-01-29) | anime-only DiT (Cosmos-Predict2-2B base, Qwen3-0.6B text encoder, Qwen-Image VAE, Turbo 8-12 steps); 1.28M downloads, 2,398 likes [V] | non-commercial; no controlled benchmark vs big generalists found |
| Neodragon 2511.06055 | T5-XXL 4.76B -> DistilT5 0.2B, minimal loss; MMDiT block pruning + 2-stage distillation [V abs] | |

**Qwen-Image-2.1 by part** (HF config, diffusers source): DiT 32 single-stream layers, d=4096, SwiGLU x3, 16 px/token, ~7.0B | **keep as teacher/painter; prune only for plates** (2.1 has 32 layers, PPCL's evidence is a 60-layer model: extrapolation). Prefix/KV design, 0 params | **copy into the student.** Qwen3-VL-8B encoder (36 layers, ~16 GB) | **drop:** fixed prompt, or image-free; precedents DistilT5 0.2B, Anima Qwen3-0.6B, MobileI2V Qwen2-0.5B [V]. VAE f16 t8 c64 | optional shared latent for a painter student (teacher space). Turbo 8-step weights | use now. DC-Gen f32 conversion | optional, needs a new alignment for the 2.1 VAE.
- **Growing a video model from an image DiT:** not worth it. SANA-Video 12 days x 64 H100 = 768 H100-days; MobileI2V ~4,000 V100-hours from scratch; MobileWan linearization 30k+10k its. Qwen-Video-Edit (2608.14790) avoids new layers by tiling Wan latent frames into one virtual image (3x4 grid for 12 latent frames), LoRA r32, no cost reported, degrades past ~1024^2 grids [V~]. Start from a video DiT (A, C), use Qwen as teacher.
- **Merging painter and animator: no.** The plate carries the far-field knowledge: the 14B holds plate content, the 1.3B zero-shot invents [L, prior reports]. A small model has no capacity to also paint. Keep the plate as an explicit coarse prefix cached per shot. A single network with two step counts (2 steps refine, 8 steps paint) is fine for the small roles (kongming's refiner/cel), FlexiDiT-style (2502.20126: >40% fewer FLOPs images, 75% video, one flexible model [V abs]).

### 3.5 Q5 Side-only formulations
| Idea | Evidence | Verdict |
|---|---|---|
| Per-side strips / wings-only + band | EditCtrl (masked tokens + 256^2 thumbnail), EasyControl (fixed 512^2 condition), FullDiT2 (token selection) [V]. **No published ablation of band width for outpainting.** | likely fine; test T2 |
| Foveated mixed-resolution tokens | Foveated Diffusion 2603.23491 (2026-03): LoRA r32 on FLUX.2 klein 4B (90k images, 10k steps, 8 H100) and Wan2.1-1.3B; 26% tokens -> 2.08x (FID 12.62 vs 11.38), video 38% -> 3.5x; phase-aligned mixed-resolution RoPE; colour artifacts at the fine/coarse boundary; causal/streaming not tested [V~] | ~2x tokens; ours: fovea = seam, 244 vs 510 wing tokens at s=1/2 (fine p=32 within 128 px, coarse p=64 beyond) [E] |
| Coarse tokens early in sampling | FlexiDiT (dynamic patch size per step); Lumina-Video multi-scale Next-DiT 2502.06782; MrFlow 2607.01642: 10x training-free on FLUX.1-dev/Qwen-Image, 25x with step distillation [V abs] | for plates, not for 1-2 step updates |
| Perceptual budget of the sides | sibling pass: ~10% (2 screens) / ~6% (3 screens) of native pixels, 0.2-0.4 Mpx total with 1/2,1/4,1/8 bands [S] -> ~200-390 tokens at p=32 | matches the 144-510 token wings |
| Temporal foveation (far tokens update at shot rate, near at drawing rate) | none; [E] | unconventional, untested |

## 4 Student blueprints

**4.1 Tokens (32:9, picture centred; wing = half picture width; 3 screens doubles the wings)**
| p | s=1/2: per wing / both / canvas / seam band per side | s=1/4: per wing / both / canvas / band per side |
|---|---|---|
| 16 (Wan2.1, Qwen-2.1) | 1,020 / 2,040 / 4,080 / 510 | 255 / 510 / 1,020 / 136 |
| 32 (LTX2, DC-AE f32, Wan2.2) | 255 / 510 / 1,020 / 136 | 72 / 144 / 279 / 36 |
| 64 (DC-AE f64) | 72 / 144 / 279 / 36 | 20 / 40 / 80 / 10 |
Band = 1/4 of the picture width next to the seam. Prefix per update = 2 bands + 64 thumbnail (336 at s=1/2 p=32; 136 at s=1/4).

**4.2 Blueprints**
| | **A: SANA-Streaming-2B fine-tune** | **B: wing specialist 0.27-0.9B** | **C: DC-adapted Wan2.1-VACE-1.3B** |
|---|---|---|---|
| Start (licence) | `Efficient-Large-Model/SANA-Streaming`, DiT 4.5 GB bf16 (Apache-2.0 tag; LTX2-derived VAE 4.9 GB: licence unclear) | MobileI2V weights/recipe (Apache-2.0); or prune A; Anima-2B as anime-prior init only if non-commercial is acceptable | Wan2.1-VACE-1.3B (Apache-2.0) + DC-Gen 1.3B guide |
| Latent | LTX2 f32 t8 c128 (p=32); tiny decoder `taeltx2_3_wide` or anime-distilled | DC-AE-V f32 or f64 c128, anime-tuned decoder | DC-AE-V f32 t4 c32-128 (p=32) |
| Known picture | channel-concat as published; prefix variant needs retraining; strips variant needs none | prefix: thumbnail + bands + previous wings + plate; mixed-res RoPE | VACE branch -> prefix (VACE not in DC-Gen guides) |
| Attention / steps / precision | 15 GDN + 5 softmax, chunk 3 latent frames; 4 -> 2 steps; BF16 now, MPQ paper-only | 3:1 hybrid, 2 -> 1 step (adversarial post-training, AAPT-style); FP8/NVFP4 | softmax window 2 latent frames; 2 steps + refresh; FP8 |
| Update unit | latent frame = 8 film frames (3/s); fixed rate, ignores drawing holds | one new drawing (6.3-15/s); gated | latent frame = 4 film frames (6/s) |
| Exists | weights, V2V trainer (bidirectional + long stage), LTX2 TAE | MobileI2V code, Turbo-VAED, PPCL/TinyFusion code, One-Forcing and Causal-Forcing++ code, DC-AE-V weights | Wan-VACE weights, DC-Gen/DC-VideoGen recipe, Causal Forcing++, TAEW2.1 |
| Must build | outpaint+anime fine-tune (self-supervised crops, cloud); prefix variant; 2-step distill; MPQ; Windows/sm_120 kernels | prefix block-causal layout, 1-NFE adversarial post-training (AAPT paper only), anime decoder/AE, distillation data from the Qwen+Wan teachers | VACE->DC adaptation, causal 2-step distill, outpaint fine-tune; no TAE for DC-AE-V |

**4.3 Compute [E]** (TFLOP per update; need = updates/s; u bf16 / mixed; budget 9.2-13.9 / 13-19.4 TFLOP/s)
| Row | Tokens noisy + prefix | TFLOP/update | need | TFLOP/s | u | Verdict |
|---|---|---|---|---|---|---|
| A published full canvas, 4 steps, s=1/4 | 279 | 4.5 | 3/s | 13.4 | 0.73 / 0.52 | fits (~; DiT only) |
| A wings+prefix, 4 steps, s=1/4 | 144+136 | 3.4 | 3/s | 10.3 | 0.56 / 0.40 | fits |
| A wings+prefix, 2 steps, s=1/4 | 144+136 | 2.3 | 3/s | 6.8 | 0.37 / 0.26 | fits |
| A published, s=1/2 | 1,020 | 16.5 | 3/s | 49.5 | 2.7 / 1.9 | no |
| A wings+prefix, 2 steps, s=1/2 | 510+336 | 7.6 | 3/s | 22.7 | 1.2 / 0.88 | no (bf16), ~ (mixed) |
| B 0.27B, 2 steps, 144-token grid (s=1/4 f32 or s=1/2 f64) | 144+136 | 0.31 | 15/s | 4.7 | 0.25 / 0.18 | fits |
| B 0.6B, same | 144+136 | 0.69 | 15/s | 10.3 | 0.56 / 0.40 | fits |
| B 0.9B, same | 144+136 | 1.05 | 15/s | 15.8 | 0.85 / 0.61 | ~ |
| B 0.27B, s=1/2 f32 | 510+336 | 1.03 | 15/s | 15.5 | 0.83 / 0.60 | ~ |
| B 0.9B, s=1/2 f32 | 510+336 | 3.6 | 15/s | 54 | 2.9 / 2.1 | no |
| B-band refiner (inner half of each wing), 0.27B / 0.9B, s=1/2 f32 | 272+336 | 0.63 / 2.2 | 3/s | 1.9 / 6.5 | 0.10 / 0.35 | fits (kongming's cadence) |
| same at 15/s | | | 15/s | 9.5 / 33 | 0.51 / 1.8 | 0.27B only |
| C wings+prefix, 2 steps+refresh, s=1/4 | 144+136 | 1.46 | 6/s | 8.8 | 0.48 / 0.34 | fits |
| C wings+prefix, s=1/2 | 510+336 | 5.1 | 6/s | 30.8 | 1.7 / 1.2 | no |
| Rejected: Wan2.1-VACE-1.3B as is, 4 steps, s=1/4 | 1,020 | 17.8 | 6/s | 107 | 5.8 / 4.1 (= 4.1 fps) | no |
| Rejected: 5B class (Wan2.2-5B, MobileWan, SANA-Video 2.0), 3 steps, s=1/4 | 279 | 7.95 | 6/s | 48 | 2.6 / 1.8 | no |
Comedy (6.3 updates/s) multiplies B rows by 0.42. Max sustainable rate at u=0.5 on the 144-token grid, 2 steps: 0.27B 30-42/s, 0.6B 13.5-19/s, 0.9B 8.8-12/s. Tiny-model latency-bound risk: x1.5-2.3 on u for <=0.6B. VAE bill and 4x display upscale from s=1/4 are not in these rows. Comedy at s=1/2 f32 is feasible for B <=0.6B (u 0.57-0.78).

**4.4 Painter ladder (plates 0.25/s), ratio method from the measured 170 s [E]**
| Step | Plate time | u | Note |
|---|---|---|---|
| today: 7.1B, ~9.6k tokens, 25 steps (kongming's anchor) | 170 s [L] | 42 | ComfyUI already has `QwenImage21Cache` (default auto) [S, sibling] |
| Turbo 8-step | ~54 s | 14 | LoRA trained on base: transfer untested |
| + foveated 2.5k tokens | ~14 s | 3.5 | |
| + 4 steps | ~7 s | 1.8 | |
| + prune to 3.5B (16 layers) | ~3.5 s | 0.9 | PPCL-class, 2.1 untested; kongming gets 3.2 s |
| + DC-Gen f32 (4x fewer tokens) | ~0.9 s | 0.22 | needs alignment for the f16c64 VAE |
Prefix K/V memory at s=1/2 p=16: 4,080 tokens x 32 layers x 2 x 4096 x 2 B = 2.1 GB: halve with an FP8 cache. Plate + animator must sum to <=0.5-0.75: e.g. painter 0.22-0.9 plus A/B 0.25-0.56.

**4.5 Ranking**
1. **A** (start now): only one with a shipped real-time number on consumer Blackwell, Apache, trainer in repo. Weaknesses: 8-frame latent frames ignore drawing holds and straddle cuts; LTX2 VAE licence/size; kernels. Mitigate: align chunks to cuts, prefix/strips variant.
2. **B** (target): best fit to drawing-gated work, holds, seam fidelity; reaches s=1/2 only with f64 or foveated tokens. Most to build; heavy distillation from teachers.
3. **C** (fallback if SANA kernels fail on Windows): pieces exist but three separate adaptations, no TAE, 4x temporal packing is wrong for per-drawing work.
Rejected: 5B-class students; AAPT-8B; Wan-VACE-1.3B as is; pruning Qwen-2.1 into the animator.

**Unconventional options (all [E], untested):** (U1) plate as a per-shot cached prefix; (U2) drawing-gated variable latent-frame rate (DLFR idea) so holds cost nothing; (U3) spatio-temporal foveation; (U4) student in the teacher's latent space (Qwen-2.1 VAE f16 t8 c64) to distill latents without pixel round trips, at 4x the tokens of f32; (U5) decoder-as-upscaler: generate at s=1/4 f32, anime-distilled decoder outputs 4x; (U6) one network, two step counts (refine 2 / paint 8).

**4.6 Adoption risk and fit** (GitHub/HF reads 2026-10-13)
| Option | Maturity | Breaking-change / abandonment | Fit (ADR 0014 worker) |
|---|---|---|---|
| SANA-Streaming | weights 2026-06-14, 2 likes; parent NVlabs/Sana 9.3k stars, push 2026-10-12 | cadence high (Video 2.0, WM, Streaming in 5 months); pinned torch 2.10/triton 3.6.0/diffusers commit; MPQ unreleased | out-of-process chunk producer, ring buffer |
| DC-Gen / DC-AE-V | DC-Gen 426 stars push 2026-10-01; DC-VideoGen repo stale since 2025-10-05 ("release after legal review"); DC-AE-V weights no licence tag | medium | one-time adaptation |
| MobileI2V, Turbo-VAED | 101 / 142 stars, push 2026-05 / 2026-07, Apache-2.0 | small teams | B's recipe |
| Qwen-Image-2.1 (+Turbo) | 1.9k stars, 142k downloads | fast churn; Qwen Research Licence: non-commercial, "Improved using Qwen" for models trained on outputs [V text] | teacher/painter only; keep students on Apache teachers (Wan2.2, klein-4B) if you may distribute |
| PPCL / TinyFusion | PPCL CVPR'26 Highlight; TinyFusion 171 stars | 60-layer-era checkpoints | pruning tool |

## 5 What to try first (each changes a decision)
- **T4 (0.5 day, needs installs: ask the user):** SANA-Streaming BF16 on this laptop with 960x288 and 1920x544 canvases (grey wings): DiT FPS, VRAM, do the GDN kernels run on Windows sm_120/torch 2.14. Replaces my /3.6 and eta; kills or confirms A.
- **T3 (2 h):** round-trip 30 anime frames (lines, flat fills, 2-3 frame holds) through Wan2.1, Wan2.2(+Turbo-VAED), Qwen-2.1, LTX2(+TAE wide), DC-AE-V f32t4c64/c128/c256, f64t4c128: PSNR/LPIPS + edge F1 + line-width ratio. Decides AE and whether a decoder fine-tune is needed.
- **T2 (1-2 h, existing recipe, no training):** condition canvas cropped to band 1/8, 1/4, 1/2, full of the picture width + 256^2 thumbnail; VLM with vs without the image; blind forced choice (n>=20). Decides the prefix size and whether the encoder can be a constant.
- **T1 (0.5 day):** Turbo 8-step + the existing outpaint LoRA, `QwenImage21Cache`/`use_kv_cache` on vs off; s/plate and style. Re-anchors the painter ladder.
- **T5 (cloud 1-2 GPU-days, only after T3+T4):** LoRA outpaint fine-tune of A on self-supervised crops; judge wings by eye on 10 shots; decides A vs B.
- Do not: prune Qwen-2.1 into the animator; train an anime encoder before T3; merge painter and animator; run 5B-class students.

## 6 Sources (read 2026-10-13; date = publication or last push)
- Known-region: arXiv 2503.07027 EasyControl (2025-03-10), 2503.08280 OminiControl2 (2025-03-11), 2506.04213 FullDiT2 (2025-06-04), 2602.15031 EditCtrl (2026-02-16), 2606.26740 LiveEdit (2026-06-25), 2502.10389 RAS (CVPR'26), 2410.05317 ToCa, 2608.17995 AViTS (ECCV'26), 2607.01642 MrFlow (2026-07-02). HF black-forest-labs/FLUX.2-klein-9b-kv; diffusers `pipeline_flux2_klein_kv.py`, `transformer_qwenimage21.py`, `pipeline_qwenimage21.py` (main); Qwen/Qwen-Image-2.1 (config.json, vae/config.json, README, LICENSE 2026-09-20), github QwenLM/Qwen-Image-2.1 (push 2026-10-12).
- Compact video: 2501.00103 LTX-Video, 2412.07583 MobileVD, 2412.10494 SnapGen-V, 2503.18559 Hummingbird, 2507.13343 Taming (2025-07-17), 2506.09350 AAPT, 2506.01380 NFD, 2511.21475 MobileI2V + hustvl/MobileI2V, hustvl/Turbo-VAED, 2509.24695 SANA-Video, 2607.21553 SANA-Video 2.0, 2605.30409 SANA-Streaming + HF Efficient-Large-Model/SANA-Streaming + NVlabs/Sana docs/sana_streaming.md + longsana, 2607.06173 MobileWan, 2607.03803 CineMobile, 2601.12719 S2DiT, 2605.23458 One-Forcing, 2605.15141 Causal Forcing++, robbyant/lingbot-video-dense-1.3b, Wan-AI VACE-1.3B / TI2V-5B configs.
- Latents: 2509.25182 DC-VideoGen, 2509.25180 DC-Gen (+ dc-ai-projects/DC-Gen guides, HF dc-ai/dc-ae-v-1.0-*, DC-Gen-Qwen-Image-Edit-Res1K, DC-Gen-Wan2.1-I2V-14B), 2604.19858 Wan-Image, 2609.39222 DC-SAE, 2604.07340 TC-AE, 2504.10567 H3AE, 2502.11897 DLFR-VAE, madebyollin/taehv (MIT, push 2026-08-31).
- Surgery: 2511.16156 PPCL + OPPOer/Qwen-Image-Pruning, baseten/Qwen-Image-2512-Pruned-50blocks, 2412.01199 TinyFusion, 2412.09619 SnapGen, 2511.06055 Neodragon, 2608.14790 Qwen-Video-Edit, circlestone-labs/Anima, 2603.23491 Foveated Diffusion, 2502.20126 FlexiDiT, 2502.06782 Lumina-Video.
- Local: scratchpad `tools/student_compute.py`, `extra_calc.py`, `extra2.py`; plans/reports kongming-261013-1049 counsel and the two 261012-1407 reports.
- Not re-verified: BK-SDM, KOALA, FLUX.1-lite, Flux-Mini (older UNet/FLUX-era lineage, superseded by the verified items above).

## 7 Unresolved questions and limitations
1. Anime outpaint quality of any <=2B student at s=1/4 with 4x display upscale: no evidence anywhere found.
2. Do SANA-Streaming's GDN kernels (pinned torch 2.10/triton 3.6.0) run on Windows sm_120 with torch 2.14? MPQ recipe unreleased: my x1.4 is an assumption.
3. eta for 0.3-0.9B students on this GPU: unmeasured; MobileI2V suggests 4% of peak in its stack.
4. Band-only/thumbnail sufficiency at 50-67% masks (EditCtrl tested small masks, no outpaint).
5. Licences: LTX2-derived causal VAE inside SANA-Streaming vs its Apache tag; DC-AE-V weights carry no HF licence tag; Qwen Research Licence effect on a student trained from Qwen outputs ("Improved using Qwen" clause) needs a human read.
6. Does the outpaint LoRA transfer to Turbo? No evidence; klein LoRAs did not.
7. Latent frames of 8 film frames vs 2-3 frame holds and cuts (DC-AE-V chunk-causal, 40-frame chunks): effect untested.
8. 3 screens double the wing tokens: s must drop to ~3/8 or use f64/foveation.
9. AAPT has no public weights/code found; One-Forcing and Causal-Forcing++ cover only Wan 1.3B.
10. Foveated Diffusion untested with prefix/streaming; boundary colour artifacts.
11. Sustained clocks at 120 W over a 24-minute pre-pass: all u scale with thermal derate.
12. Calibrations ignore attention and use digest-level paper numbers: +-30% on every u.
Not covered: training data/recipes (sibling b), compositor/warping (c), GPU sharing, training-free speedups beyond the free flags, LTX-2.x 22B, HunyuanVideo.
