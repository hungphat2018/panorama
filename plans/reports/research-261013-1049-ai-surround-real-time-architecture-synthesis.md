---
title: "AI surround at film speed: architecture synthesis (concept level)"
date: 2026-10-13
type: research synthesis
inputs:
  - plans/reports/kongming-261013-1049-real-time-architecture-counsel.md (saved)
  - plans/reports/researcher-261013-1049-fast-current-recipe-without-training.md
  - plans/reports/researcher-261013-1049-specialist-student-architecture-and-compute.md
  - plans/reports/researcher-261013-1049-specialist-student-training-data-and-adaptation.md
  - plans/reports/researcher-261013-1049-render-not-generate-structure-first-moving-sides.md
  - plans/reports/researcher-261013-1049-perceptual-budget-of-the-sides.md
  - plans/reports/researcher-261012-1407-real-time-generative-models-and-neural-rendering.md, synthesis 261012-1521 (8.4, 8.5)
---

# AI surround at film speed: architecture synthesis

Tags: [V] verified by a researcher this session, [L] measured locally, [E] estimate, [R] recalled. Budget numbers are [E] (+-30-50%).

## 1 Target (user, 2026-10-13)

- **High preset = today's best quality** (Qwen-Image-2.1 plate + Wan2.2-VACE-Fun-A14B keyed + wraps) with "minimal loss that seems to be no loss", running at **1x film speed on this laptop**. Low/medium presets for weaker hardware are later work.
- **Two screens first** (32:9; each side = half the picture width). **Play at once; AI sides catch up** (cheap tiers at the start, then the generator stays ahead).
- **Training (user, 13:12 → 13:20): "small training is fine, not cloud".** Only small runs on this laptop (LoRA, small models). Nothing goes to a rented GPU, so every rented rung below is out (cross-size distillation, DMD/rCM students, VDOT++-style runs). Because training stays local, the user's own episodes may be used and the film never leaves the machine.

## 2 Verdict

Per-frame video generation cannot reach 1x on this laptop, even with every public speed-up and no training. All public levers together give 150-300x; action needs 1,200-1,800x. That leaves the best case 8-12x short on action (4-6x on comedy), and 37-55x short with files downloadable today (fast-recipe report [E on V]). The gap closes only if the architecture changes shape:

- Most frames are rendered from structure, not generated.
- Generation runs only where the picture changed something the structure cannot explain.
- Every side change is tied to a picture change.

The big models leave the live path and become the painter, the teachers and the offline oracle.

## 3 Evidence carried from the reports

| Source | Finding that shapes the design |
|---|---|
| Perception (Strasburger 2011, Guenter 2012, Patney 2016, IllumiRoom, Novy 2013 [V]) | **Errors in time are the most visible; detail is the least.** A side pixel may change only when the picture changes (new drawing, camera step, cut): no boil, no swaps mid-shot, upgrades only at cuts. Bands: 1/2 resolution within 5° of the picture, 1/4 at 5-15°, 1/8 beyond, about 10% of native side pixels. Generators already run near this pixel floor, so **the savings left are in time and architecture, not resolution**. Periphery systems people liked had ≤ half the centre's pixel density; complaints were sides that stood still while the picture moved, cuts, and brightness. |
| Structure path, measured on JJK S03E12 (CPU [L]) | Holds, cuts and still-or-rigid-slide steps make up **63% of steps**, 81% with small patches. Generation is needed on **19% of steps** (~4.7 drawings/s, mean 19% of the band area): about 6% of the pixel work of "generate everything". A camera model fitted on the picture's centre explains the edge bands as well as the bands' own motion (62.7-65.4% vs 65.0%). A plate alone matches only 33-53% of frames and goes stale within 0.25-0.5 s. About 9 of 16 hard steps were effects (beams, speed lines, lightning); characters crossing the seam were about 29%. Atlases (CoDeF), EbSynth and inbetweeners do not help. |
| Fast current recipe (no training) | **Painter:** Turbo 8-step plus Turbo LoRA (official), INT8/FP8, resident models, NVFP4 and fewer tokens: 170 s → ~9 s per plate. Whether the outpaint LoRA survives Turbo is untested. **Animator:** VDOT-14B is a public 4-step VACE student (Apache, outpaint parity claimed, untested on anime), alongside lightx2v 4-step LoRAs, INT8 plus SageAttention (sm_120 wheels exist), LightVAE and half resolution: 54 → ~0.1-0.2 s/frame. Nothing streaming at 14B fits 12-16 GB. |
| Student architecture | Best shape: **wings only, picture given once as a cached prefix, ≤2 steps, hybrid linear/softmax attention, ≥32x latent.** Gap stack: tokens 11x, steps 25x, CFG 2x, params 7x, temporal packing 2x. Start points: **SANA-Streaming 2B** (Apache, 24 fps end to end at 1280x704 on an RTX 5090) or a **0.27-0.9B drawing-gated specialist** (MobileI2V-class). Qwen-Image-2.1 already uses a t=0 KV prefix: copy the design, keep the 7.1B as painter/teacher, do not merge painter and animator. |
| Student training | **Train on the truth first:** hidden-sides pairs from the user's own episodes (local, free, exact). Per-show painter LoRA on the laptop (6-22 h); plate-keyed animator LoRA. Tiny data teaches the task (Qwen outpaint LoRA used 68 pictures; a Wan-14B outpaint LoRA took 25 clips and ~1 h on an H100). Then compress (DMD/rCM/VDOT++-style, rented, public clips only: $0.3-1.4k per run). Preference loop: free truth-vs-sample pairs (RealAlign: 512 pairs ≈ 851k human pairs), with the user's forced choices as the gate. Skip TTT layers and per-shot MOTIA. |
| Kongming (Fable) | A **cel compositor, not a video generator**. Plates per set, reused across shots. Continuation cels per character appearance. **"Trails":** the picture's own past and future edge columns unfolded outward for exits and entries (real pixels, perfect identity). A small band refiner, change-gated. A degrade ladder so playback can never stall. |

## 4 The architecture (roles by cadence)

| Cadence | Role | Tool now → target | Cost class |
|---|---|---|---|
| Per episode, at file open, ahead of the playhead | **Analyse:** shots and hidden cuts, centre-fitted camera path (DIS flow mode + similarity), step planner (HOLD / STILL / SLIDE / GAIN / PATCH / GEN per side per drawing), set clustering, cost model, schedule | content model (exists), `scroll-plate.py` flow mode, `route-by-motion.py` → 6-state planner | CPU, minutes |
| Per set / shot | **Paint:** wide plate (foveated); reuse across shots of the same set; re-key at cuts, hidden cuts and drift past the limit | Qwen-2.1 + outpaint LoRA (170 s) → Turbo/OPD 4-step, FP8/NVFP4, ~2.5k tokens, pruned 3.5B or DC-Gen f32 latent (~1-3.5 s) | GPU, ~0.3-0.45 of the GPU |
| Per flagged drawing (~4.7/s on action, far fewer on comedy) | **Refine the zone:** draw what changed (effects, smears, a character crossing the seam) | **new small model** (≤0.3-0.9B, 1-2 steps). Inputs: warped previous side render, picture seam band, thumbnail, plate, character reference crops. Trained on hidden-sides truth | GPU, ~0.2-0.3 of the GPU |
| Per frame (inside the performer) | **Render:** warp the previous render by the camera step, sample the plate, trails, colour fit + per-step gain, live seam pull, hold/freeze, paste the real picture | exists in parts (seam-match, wraps, scroll) | ~0.3 ms |
| Never in the live path | **Teach / oracle:** A14B keyed (oracle and pseudo-truth beyond the real edge), Qwen 25-step (plate teacher) | today's recipe | offline |

**Never stall.** Degrade ladder: T0 glow → T1 scrolling mosaic → T2 plate → T3 propagated + patches → T4 refined zones. Tiers change only at cuts. Because the film is known ahead, the planner flags drawings seconds early, the generator works only on those, and bursts borrow time from calm stretches.

**Budget, action episode (u ≈ 0.6 of the GPU) [E]:** painter 0.22-0.27 plates/s (after reuse and skipping very short shots) x ≤1.5-2 s = 0.3-0.45. Refiner 4.7/s x ≤40-60 ms = 0.2-0.3. Renderer ~0. **Total ≈ 0.5-0.75: fits only if the painter reaches ~2 s and the refiner ≤60 ms.** Comedy has more slack.

## 5 Model surgery (the user's "break the model apart")

- **Qwen-Image-2.1 (painter):**
  - drop the 8B VLM encoder from the live path (cached or learned embedding; test whether the encoder must see the image);
  - Turbo 8 steps, then OPD to 4 (~2 GPU-h per DiffSynth docs);
  - depth-prune 7.1B → ~3.5B (PPCL showed -50% params at <3% loss on 60-layer Qwen-Image) and heal on hide-sides data;
  - foveated canvas (fine tokens at the seam, coarse far out);
  - retrain the outpaint LoRA at the real strip geometry and for this show.
- **Wan A14B:** leaves the live path; stays as teacher, oracle and source of pseudo-truth for "beyond the real edge".
- **Refiner:** purpose-built. Conditions enter as a prefix or extra channels, so only zone tokens are denoised. Latent ≥32x **after** an anime line round-trip test. 1-2 steps, adversarial post-training.

## 6 Experiments, in order (each changes a decision)

1. **X1, true-sides real-use test (CPU, no generator).** Cut a 32:9 strip from the middle 540 rows of 1080p frames; the centre 960 px is the picture and the outer 480 px each side are the true sides, shown at 2x on the two screens. Variants: truth, frozen (control), structure-propagated with oracle zones (truth only where the planner flags), and 3/6 Hz + warp. Forced choice, ≥15/20 = visible. **Decides the architecture's premise.** Combines perception E2/E4, the structure replay and kongming's R1.
   - **Built 2026-10-13:** `spikes/ai-surround/true-sides-test.py`, output `out/demo/20261013-133033-true-sides/` (44 blind trials, 4 jjk moments).
   - **Refresh duty per moment (planner, left/right per s):** reaching 1.4/2.8, fight 8.1/14.1, edge-move 23.3/22.2 (≈ every frame), pan 0/0. The 4.7/s budget in section 4 is an episode average; effect-heavy bursts need the generator at frame rate. The run-ahead buffer and the degrade ladder carry those bursts, so they are not optional.
   - **Dry-run finding:** on the curved-wall pan, one camera shift from the picture never triggers a refresh, yet drifts at the far sides (error 15/20). The test shows whether that drift is visible; if it is, the camera model needs affine or parallax terms, or a travel-based re-key.
2. **X2, painter speed (GPU ~1 h, 1 GB download):** Turbo LoRA + outpaint LoRA v2 on the 5 jjk plates; FP8 with `--disable-dynamic-vram`; style and seam by eye, plus timing.
3. **X3, anime line round-trip through candidate latents** (Wan2.1, Qwen-2.1 local; DC-AE-V, LTX2 when disk allows). Decides the refiner's latent.
4. **X4, refiner base smoke:** SANA-Streaming on Windows/sm_120 (install + ~9.4 GB) or a MobileI2V-class model. Measured speed replaces the /3.6 scaling ratio.
5. **X5, schedule simulation (CPU):** set reuse, skippable shots, required head start.

## 7 OpenModelDB (user question)

Snapshot of the database repo `OpenModelDB/open-model-database` (last push 2026-08-14, read 2026-10-13 [V]):

- **What is there.** 671 models, 241 tagged anime or cartoon. Scale: 117 are 2x, 84 are 4x, 37 are 1x restoration. Architecture: 116 ESRGAN, 55 Compact, 18 SPAN, 7 OmniSR, a few RealPLKSR / Real-CUGAN / DAT.
- **Licences.** 111 CC-BY-NC-SA-4.0, 40 CC-BY-4.0, 28 WTFPL, 16 MIT, 16 CC-BY-NC, 6 CC0.
- **Dates.** Mostly 2021-2024; 16 from 2025 and 8 from 2026.
- **Recent permissive light ones:**
  - 2x-90s-Sonic (Compact) and -LG (RealPLKSR): MIT, 2026-08, tagged video-frame.
  - 2x-Where-On-Earth-Upscale-Small (Compact, MIT, 2025-08, video-frame).
  - 2x-DigitalPokemon-s (Compact, MIT).
  - 2x-ModernSpanimationV1 (SPAN, MIT, 2024-04).
  - 2x-MLP-StarSample-V1-0 (Compact, CC0).

All are image-to-image upscalers or restorers. Each output pixel sees a small neighbourhood, so **none can invent the sides**. Weights and architectures are open (spandrel loads them), so **no reverse engineering is needed**. They fit three roles:

- (a) **Upscale** the generated sides, made at 1/4-1/2 resolution, to display size with anime-correct lines. Real-time-class nets: Compact and SPAN.
- (b) **Repair lines** the VAE thins (1x restoration and dehalo models).
- (c) **Template for the zone refiner**: a small conv net in the Compact/SPAN family, trained locally with a GAN loss on hidden-sides crops. This is "small training, not cloud".

Still to check: speed on sm_120 (ONNX/TensorRT), and line fidelity on our sides.

## 8 Local-only training: what changes

| Was | Now |
|---|---|
| Painter 4-step via OPD on a rented GPU | Use official few-step weights first (Qwen-Image-2.1-Turbo 8-step, PAI Fun-Acc 4-step LoRA). OPD on the laptop only if they fail with the outpaint LoRA. |
| Prune Qwen 7.1B → 3.5B and heal | Healing with full weights does not fit 12 GB; LoRA healing is unproven. Keep it as the last painter lever, after plate reuse and fewer tokens. |
| Refiner student distilled from the 14B (rented) | Small refiner trained **locally on truth** (hidden-sides pairs from the user's episodes). Candidates: a ≤0.3-0.9B DiT, or a Compact/SPAN-class conv net with a GAN loss (section 7c). Smoke-test step time first. |
| Preference loop | Unchanged and local: free truth-vs-sample pairs plus the user's forced choices. |

## 9 Open questions

1. Licence: the Qwen Research licence ("Improved using Qwen") follows any student trained from Qwen outputs; an Apache painter path (klein-4B, Wan) may be needed for release.
2. ~~Does the outpaint LoRA survive Turbo or the 4-step LoRA? (X2.)~~ It survives Turbo at 8 and 6 steps, merged and quantized to W4A8, with the 25-step plate's hidden-sides scores (section 10). The user's swap test is pending.
3. Do effects at the seam need a procedural path rather than a learned one?
4. Can a local-only refiner reach A14B-keyed quality on effects and characters crossing the seam? No evidence yet; X1 bounds what a perfect one would give.

## 10 Update after phase 3 (2026-10-14) [L]

Report: [implementation-261014-0245-painter-at-speed-phase3](implementation-261014-0245-painter-at-speed-phase3.md).

- **Painter (section 4's "Paint" row).** The tool is now Qwen-Image-2.1 with the Turbo and outpaint v2 LoRAs merged, quantized to W4A8 and resident in-process.
  - It uses INT8 attention and 6 steps (the Turbo schedule sub-sampled), on a ~1 MP canvas.
  - It paints a plate in 5.8 s (from 170 s in today's recipe, 14 s in X2), with the 25-step plate's hidden-sides scores: near 10.26 / far 26.09 / seam median 1.16, against 10.35 / 27.10 / 1.31.
  - Section 4's ~2 s per plate is not reached without training. Two-speed painting (X5) moves the big painter's target to 5.0-6.1 s at T = 3 s.
- **The 8B encoder is out of the live path (section 5).** The plate does not need the encoder to see the canvas. One conditioning, read once from an all-grey canvas, scores like a per-canvas read. What matters is the image slot in the conditioning, which X2's text-only variant lacked.
- **GPU sharing.** The player's misses come from the painter's queue depth (hundreds of kernels queued ahead), not from kernel length or VRAM.
  - The painter now waits for the GPU after each attention kernel, MLP and VAE convolution, and splits attention into kernels of at most 2048 queries.
  - With that, E1's painter cell is at the no-load level: 2.0 / 1.0 wrong / missed per min at 240 Hz against 1.0 / 2.6, and 0 / 0 at 96 Hz.
- **u measured.** With the player running at 240 + 96 Hz, the painter keeps 0.75-0.79 of its alone speed in every E1 cell, throttled or not. This is within section 4's assumed 0.6-0.85.
- **Set atlas.** Reuse in the live worker is about 1.06-1.08 on JJK, X5's 1.10 measured live. Sharing a plate can show another shot's character, a flash or a fade in the sides, so joins now also need appearance agreement and no character cut at a side edge (sets-v2). Reuse buys under 10 % of painting.
- **Still open:**
  - Mosaic seams on 3D or curved backgrounds (staircase fragments in plates).
  - The small painter for short shots (phase 5's refiner in paint mode).
  - Sides-only retraining and 4-step distillation, if the budget needs them.

## 11 Update after X1c (2026-10-14) [L]

Evidence: [phase 4's X1c verdict](../261013-0718-ai-surround-live-path/phase-04-structure-path.md#x1c-verdict-and-replan-2026-10-14) (the user's by-eye review plus a per-frame check of every version).

- **Section 2's shape fails by eye for moving sides.**
  - Carried sides break at the seam within 1-3 frames on real camera moves.
  - On a slow move the camera drifted the side ~51 px, while a frozen side stays within 20 px.
  - Each refresh pops by the whole error. No cadence fixes it, even with the true pixels as the painter.
- **New shape (the user, 2026-10-14).** A side model redraws each whole side at every new drawing (~15/s on action). It starts from the carried side and the picture, and is trained on the true sides.
  - The structure (camera map, planner, exit strips, plates) becomes its input.
  - Section 4's "Refine the zone" and "Render" rows merge into "Redraw the sides" per drawing. The player shows each drawing's sides without warping them.
  - Carrying stays only as a speed shortcut, where the per-frame gate proves it invisible.
- **The bar** is per frame, at the user's normal-speed threshold (measured once with a ladder). It is never an average. Detail at the seam counts: phase 3's swap tests found that half the fine detail reads as a join line.
- **Budget.** The old refiner's slot (4.7/s × 30 ms) at 15 updates/s comes to ~9-10 ms per update. That is the learned-carry class (phase 5's candidate D). Section 3's student report prices the inventing path: B at 0.27-0.6B, u 0.25-0.56 [E].
- Plan: [phases 4-5](../261013-0718-ai-surround-live-path/plan.md), amendment 5.
