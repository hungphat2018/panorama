---
title: "Kongming counsel: real-time anime sides on this laptop: the architecture to bet on, the compute math, model surgery"
date: 2026-10-13 ~11:30 (Asia/Bangkok)
author: kongming (Claude Fable 5.1), advisory only; no code, no GPU job, no download
inputs: caller brief (measurements 2026-10-12/13); read today: researcher-261012-1407-real-time-generative-models..., research-261012-1521-ai-surround-...-synthesis, researcher-261012-1639-anime-references-layer-split..., researcher-261012-1407-outpainting-sota..., spikes/ai-surround/{scroll-plate,route-by-motion,edge-band-change,comfy-plates}.py
---

Tags: **[V]** verified today in a repo file or report · **[L]** measured locally (caller / reports) · **[R]** recalled, not verified · **[E]** my arithmetic or inference. Web left to the SOTA session.
Terms once: **plate** = one wide still painted for a shot. **drawing** = one new hand-drawn image (anime holds it 2-3 frames). **edge band** = the strip of a side next to the seam (here: 25% of the picture's width). **token** = one 16x16-px patch a diffusion transformer (DiT) processes; cost grows with tokens. **NFE** = one network pass (steps x 2 if CFG is on). **compositor** = shader that assembles textures per frame (no network). **foveated** = sharp near the picture, coarser far out, like the eye.

## TL;DR

Bet on a **cel compositor, not a video generator**: paint a wide background plate once per *set* (reused across shots), continue cut-off characters once per *appearance* and move that continuation rigidly with the visible body, fill the rest with real pixels unfolded from the picture's own past/future edge, and let a small refiner touch up the edge band at <= 1-3 Hz. Generation happens per set and per appearance; everything per frame is rendering. Compute: ~25-40 TFLOP per film-second of generation on action episodes, inside the ~22 TFLOP/s budget only with a ~2-3.5B anime-only painter at 4 steps FP8 [E]; comedy fits with today's Qwen 7.1B at 4 steps. The obvious "distilled causal VACE student" is token-bound 8-20x over budget, throws away the known future, and fights anime's held drawings; keep it as an offline teacher only.

## 0 Reframe

Decision: not "which model is fast enough" but **what is the minimal set of generated pixels per film-second, and what cheap mechanism carries them to every frame**. Requirements: user's quality order (style, ratio, colour, composition, identity, no seam, motion follows the picture, consistency across cuts), real picture untouched (ADR 0001), local only, 12 GB, GPU shared with the 240 Hz performer, no stall. Non-goals: per-frame novelty, smooth 24-fps motion in the sides (anime itself holds), matching the far side pixel-exactly. Hidden fact in the brief: the user's verdict on the still plate was a **motion-sync** complaint ("one frame in the action scene"), not a detail complaint. The architecture must make the sides change exactly when and where the picture changes.

## 1 The bet: roles, cadence, generated vs propagated vs rendered

| Cadence | Role | Generated / propagated / rendered | Exists? |
|---|---|---|---|
| per episode (pre-pass, CPU+NVDEC, minutes) | shots, held drawings, camera offset per frame, person/head tracks, identity clusters (CCIP), background clusters (same set), OP/ED detection -> cue sheet | analysis | shots/holds/tracks/offsets exist [V content model; scroll-plate.py:59-80]; CCIP + BG clustering planned [V layer report A1/A2] |
| per set (~0.3-0.7 x shots) | **plate**: median mosaic of the shot(s)' own background + painter for unseen area, foveated res | generated once, cached by set | scroll-plate.py [V]; reuse across shots: new |
| per character appearance at an edge | **continuation cel**: the cut-off part of the character, identity-conditioned (CCIP reference, ED design sheets [V layer report §0.6]) | generated once per appearance | the Qwen plate already completes cut-off characters at the key frame [L synthesis 3.2] |
| per drawing (6-15/s) | **cel motion**: similarity transform of the visible body (flow mode inside the person box) applied to its continuation; **trails**: picture's past/future edge columns shifted outward for exits/entries (sec 6, U5) | propagated (CPU/shader) | new, cheap |
| per key drawing (<= 1-3/s, change-gated) | **band refiner**: 2-step touch-up of the warped edge band (fix articulation, contact, new objects) | generated, small | new model (sec 3.2) |
| per frame (24/s, stepping on the picture's frame clock) | compositor: plate sampled at camera offset, cels placed, trails, seam pull, per-shot colour fit, per-frame global flash wash, freeze while the picture's band holds | rendered | seam pull / fit / freeze exist [V synthesis 3.3] |

Rules that make it hold together:
- **Degrade ladder, always renderable**: T0 freeze+fit+seam pull -> T1 scrolling mosaic -> T2 painted plate -> T3 cels+trails -> T4 refined band. The compositor never waits; a missing tier falls to the one below. "Never stall" is then a property of the design, not of the model's speed.
- **Tier upgrades only at cuts.** Peripheral vision detects pops best; a plate arriving mid-shot must not swap in. So plates must be ready before their shot: the run-ahead covers one plate's latency (~2-5 s), not a deficit.
- **Side changes are slaved to picture changes**: a side pixel changes only on a drawing step, a camera step or a cut. No gliding at 240 Hz under a 24-fps picture, no per-frame texture shimmer (the harness's `boil` [V apply-wraps]).
- **Everything resident in VRAM** (<= 6 GB generator [V real-time report 3.0]): offloading over PCIe is what makes the A14B 55 s/frame; this architecture must never offload.

## 2 Compute math [E unless tagged]

Planning numbers. Sanity check first: today's plate = 7.1B x 2 FLOP x ~9,600 tokens (~2.4 MP canvas, comfy-plates PAD/GREY recipe, 25 steps, CFG 1 [V comfy-plates.py:14,67]) = ~136 TFLOP/NFE x 25 = 3.4 PFLOP in 170 s [L] -> **~20 TFLOPS bf16 effective** (38% of the 52.8 measured peak). FP8 x1.8 -> ~36 TFLOPS at full GPU; at a 0.6 share -> **~22 TFLOPS planning budget = 22 TFLOP per film-second**, i.e. ~32,000 TFLOP per 24-min episode. Matches the caller's 15-40 bracket.

Demand per unit:
- Compositor: a few shader passes over 2 x <= 1920x1080 at 24 fps: < 2 ms/frame, ~0 TFLOP. Trails, cel warps, mosaic: CPU/shader, ~0.
- Plate, 32:9 canvas at 1080p, foveated (inner quarter of each side at half res, outer at quarter res, upscaled line-aware): ~2,500 tokens (vs 9,600 today). Per NFE: 7.1B -> 35 TFLOP; 3.5B -> 18; 2B -> 10. At 4 NFE: **7.1B 6.5 s · 3.5B 3.2 s · 2B 1.8 s**; with an encoder-decoder split (context tokens encoded once, sec 3.1): 3.5B ~2.0 s. 48:9 adds ~30% (all extra area is far = quarter res). At today's 8-step Turbo [V layer report B2: Qwen-Image-2.1 Turbo 8-step, 2026-10-09] double these: 7.1B = 13 s.
- Plate budget: comedy 357 shots, ~200 plates after set reuse 1.5x and skipping shots < 0.8 s (~15%) -> 1440 s x 0.6 share / 200 = **4.3 s per plate**. Action 679 shots -> ~385 plates -> **2.2 s per plate**. Dead time (OP+ED+eyecatch ~3 min/episode, identical every episode -> precomputed once) adds 25-40% plate budget.
- Continuation cel: one 8-NFE call on a ~1,000-token band with references: 2B -> 32 TFLOP -> 1.5 s; ~0.3-0.5 per second of action film -> 0.5-0.7 s/s. Heavy; amortise: most appearances persist across shot-reverse-shot (same character, same side, same scene) -> reuse like plates (~2x) -> **0.3 s/s**.
- Band refiner: <= 1B, 2 NFE, ~600 tokens (band + conditions as extra channels, not tokens): ~2.4 TFLOP -> 0.11 s; at 1-3 Hz change-gated -> **0.1-0.3 s/s**.

Totals (fraction of the 0.6 GPU share): comedy with 7.1B/4-step plates: plates ~1.0, cels+refiner ~0.1 -> **1.1x: fits only with reuse >= 1.5x; margin thin**; with a 3.5B painter 0.6x, comfortable. Action with 3.5B split or 2B painter: plates 0.8-0.9, cels 0.3, refiner 0.2 -> **~1.3x over on average** unless set reuse reaches 2x+ (plausible in fights: one arena BG [E]) and short-shot skipping holds; with the 2B painter at 1.2 s/plate and 2x reuse -> **~0.9x**: at budget, no margin. 48:9 action: ~1.2-1.5x over today. Conclusion: comedy is real-time with today's painter plus 4-step distillation and foveation; action needs the anime-only 2-3.5B painter AND set reuse AND the degrade ladder carrying the peaks. Report the math, not a promise.

Memory: Qwen DiT 7.1 GB FP8 (3.6 GB NVFP4) + no text encoder (cached embedding, sec 3.1) + VAE 0.3 + refiner 1-2 + activations ~1 -> **~6-10 GB FP8**; the 3.5B/2B painter brings it under the 6 GB resident target.

## 3 Restructure and retrain

### 3.1 Qwen-Image-2.1 surgery (plate painter)
1. **Amputate the text encoder** (Qwen3-VL-8B, 17.5 GB bf16 [V layer report]): run it once on one fixed prompt per show, cache the embedding, delete it. Zero training; frees half the VRAM. Then fine-tune with the constant embedding so the DiT learns to ignore text: for outpainting the image is the condition.
2. **Encoder-decoder split**: the picture (context) tokens never change across denoising steps, only the side (target) tokens do. In an MMDiT both pass through every layer every step, and AdaLN modulation keys the context on the timestep, so plain KV caching is not exact. Fix at training time: context tokens get a fixed t=0 modulation and are encoded once by the first N blocks; target tokens run the remaining blocks per step with attention over [cached context, target]. Initialised from Qwen's own blocks; trained on the hide-sides task. Per-step cost ~halves for 32:9 (sec 2).
3. **Depth-prune 7.1B -> ~3.5B**: score each block by the plate loss delta when removed (hide-sides data), drop the least useful half of the middle blocks, heal with LoRA then full fine-tune. DiT depth pruning to ~50% with recovery is established for class-conditional DiTs (TinyFusion-class [R]); for a narrow task (anime outpaint) the recovery should be easier than for general T2I [E].
4. **4-step (then 2-step) distillation on the anime-outpaint distribution only** (DMD2 / consistency / flow-map, student = the LoRA-merged painter). Narrow distributions distil better than general T2I [R]. Check first whether the official Turbo 8-step or a community Lightning LoRA survives the outpaint LoRA merge (R2, sec 5); if not, self-distil.
5. **Train at the real geometry**: tall 32:9/48:9 strips with foveated latent resolution, grey fill as today (comfy-plates GREY [V]), plus the mosaic's real pixels as kept context.
6. **NVFP4 weights** via SVDQuant/Nunchaku-class kernels (Qwen-Image was supported in 2025 [R]; 2.1 unverified): ~2x over FP8 on Blackwell, mostly for VRAM; the SOTA session should confirm.

### 3.2 Purpose-built band refiner (the only new model)
Task: given the picture's inner 25% band, the plate behind, the *warped previous* edge band (rigid cel motion + camera), and 1-3 identity references, output the edge band for this drawing. Conditions enter as extra input channels (inpainting-UNet style), so tokens = the band only (~600 at half res). <= 1B parameters, 2 NFE from a low-noise start (it fixes a warp, it does not invent a scene), FP8. Initialise from the pruned Qwen blocks (style prior, same VAE as the plate = latent-space compositing possible) or from an anime SDXL UNet (NoobAI/Illustrious, OpenRAIL, 2.57B [V outpainting report Table B]) if pruning stalls. A "first appearance" mode at 8 NFE from pure noise produces the continuation cel with the same weights.

### 3.3 Data and objective (all local, no upload)
- **Band task, fully self-supervised, unlimited**: from any local anime, hide the outer 25% of every frame of a clip; per drawing, target = the real hidden band; inputs = visible picture band, GT previous band warped by the measured cel motion (teacher forcing), plate = median mosaic of the clip's hidden area, references = CCIP-matched crops of the same character from other frames. No teacher model needed. One 24-min episode gives ~14k drawings [E]; 50 episodes ~700k.
- **Plate task**: (a) hide-sides on real frames (narrow sides: real GT), (b) **pan mosaics** (scroll-plate.py builds real backgrounds wider than 16:9 [V]: real wide GT, background only), (c) 2.39:1 anime films cropped to 16:9 (real sides, 14% each), (d) wide pseudo-GT from the current Qwen+LoRA painter only for the far regime with no GT. Mixed, with (a)-(c) weighted up: generic outpaint data teaches "close the composition", anime hide-sides teaches "continue" [E, matches synthesis Q8].
- **Loss**: flow-matching plus a line-art term (AniLines/Anime2Sketch edges [V layer report C1]) weighted near the seam, and a seam-continuity term on the first 8 px. Rank loss against the user's forced-choice sheets later.
- **Train on the laptop**: LoRA on a <= 3.5B FP8 model at 600-2,500 tokens, batch 1-4, gradient checkpointing: feasible in 12 GB [E]; full fine-tune of 7.1B is not. Run between viewing sessions; a per-show LoRA is small and local.

### 3.4 What to do with Wan
Nothing in the real-time path. Keep Wan2.2-VACE-Fun-A14B as the **offline oracle**: it holds plate content while moving with the picture [L]; use it (a) to produce wide-regime pseudo-GT bands for a few hundred of the show's own shots as background jobs, (b) as the quality ceiling in the harness. Wan2.1-VACE-1.3B as a refiner is too heavy even at 1 step on a 1-latent-frame band (~4.4 TFLOP -> 0.2 s; 60% of the share at 3 Hz [E]) and its VAE's 4x temporal packing is wrong for per-drawing work.

## 4 Critique of the obvious plan ("small causal VACE student, few steps, FP8")

1. **Token-bound.** A 1.3B causal video DiT at 848x480, 4 steps, no CFG: ~74 TFLOP linear + ~150 TFLOP attention per NFE (28.6k tokens) -> ~300 TFLOP per film-second: **8-20x over budget** before any side-only or low-res trick, and 3D attention is quadratic in tokens [E]. Per-frame generation of anything is the wrong cadence for a medium that draws 6-15 times a second.
2. **Causal is a constraint we do not have.** The film's future is known; a streaming model is engineered for a live camera. We can use the whole shot (mosaic), the whole episode (identity, sets) and the future edge (entries). A causal student discards the single biggest advantage of the product.
3. **Drift without an anchor.** Autoregressive video drifts in identity and colour (measured: ponytail -> bob at 1.3B [L]; Adapting-VACE drifts beyond ~100 frames [V real-time report 3.3]). Smaller students drift faster. A per-shot plate + per-appearance cel + references removes drift structurally.
4. **Fights the medium.** Video diffusion trained on smooth footage paints floaty in-betweens where anime holds, and boils texture per frame; the periphery sees flicker better than detail (sec 6, U4). The compositor produces holds and zero boil by construction.
5. **Few-step distillation weakens mask fidelity.** The 1.3B's "best seams" was at 50 steps; distilled students follow kept regions less exactly, so the seam, the one thing judged at the fovea's edge, gets worse.
6. **Cuts.** Action shots average ~2.1 s [L]; a streaming model's warm-up/context window is a large share of every shot and consistency across cuts is a hard reset per shot. Per-set plates give cross-cut consistency for free.
What it misses: the problem decomposes along anime's production layers (background painting, character cels, effects, camera), and almost all side pixels are background under a 2D camera transform.

## 5 Three riskiest assumptions and the cheapest test for each

| # | Assumption | Cheapest experiment | Flip condition |
|---|---|---|---|
| R1 | "Plate + rigidly moved continuation cel + trails + touch-ups" reads as *moving with the picture* to the user on two screens; the still-plate complaint was motion sync, not per-drawing detail. | **No new model, CPU.** 3 JJK action shots: scrolling plate (exists) + the Qwen plate's completed character cut out (ToonOut/SAM box) and moved per frame by the person box's flow mode (similarity fit) + exit/entry trails. Two-screen forced choice vs still plate and vs the A14B clip where one exists. ~1 day. | User still says "one frame": the hidden part needs per-drawing redraw -> refiner at drawing rate, budget 3x tighter, 48:9 action infeasible today. |
| R2 | The painter can reach <= 2.2 s (action) / <= 4.5 s (comedy) per plate at the style bar via 4 steps + FP8 + foveation (+ pruning). | **Measure, not build.** Qwen-Image-2.1 Turbo 8-step (exists [V]; weights may need a download: next spike) at FP8 with the outpaint LoRA on 10 plates at the foveated canvas; time + eye. The 170-s point validates the linear cost model within ~15% [E], so 4-step and 3.5B points extrapolate. | Turbo+LoRA breaks style (LoRA trained on base): self-distil on the merged model, ~1-2 GPU-days [E]; or FP8 costs visible line quality. |
| R3 | Set reuse >= 1.5x, skippable shots >= 15%, and <= 60 s head start cover action variance. | **Pure arithmetic on existing data.** Cluster each shot's character-blanked median mosaic (pHash 32x32, then MegaLoc only on ties [V layer report A2]) on both episodes; count unique BGs. Simulate the schedule: plate cost by cluster, band cost from edge-band-change stats; plot the run-ahead buffer. Hours, CPU. | Reuse < 1.2x or skip share < 10%: the 2B painter is mandatory before any action demo; comedy still fits. |

Fourth, not mine to own but gating: generator kernels interleave with the 240 Hz performer without late frames (spike S1 in the content-model report [V]).

## 6 Unconventional levers the obvious framing hides

- **U1 Known future.** (a) Pans: the side at frame 1 is real content from frame N (scroll-plate does this [V]); (b) **look-ahead identity**: a character cut off now is often fully visible seconds later or in the next shot: reference from the future; (c) exact plate extent from the known camera path; (d) schedule plates in the OP/ED/recap dead time (~3 min/episode, identical every episode: precompute once); (e) upgrade tiers only at cuts, which the cut itself masks.
- **U2 Show reuse.** Plate cache keyed by background across the whole series (second episode cheaper and more consistent); the ED's full-body design sheets (shots 335-340 of the test episode [V layer report]) are canonical identity references; a per-show LoRA trained in the background between sessions ("the model learns the show as you watch").
- **U3 Production layers.** Compose BG / cel / FX / camera instead of generating a video. Holds are correct. Full-frame flashes = a per-frame global colour/luminance wash on the sides (never flash harder than the picture: photosensitivity, periphery report line 120 [V]); shake = the measured camera step; speed lines = procedural from edge orientation; only beams that physically cross the seam reach the refiner.
- **U4 Peripheral perception.** Acuity falls steeply with eccentricity; motion, flicker and onset sensitivity persist [R textbook; same basis as periphery report line 74 [V]]. Consequences: foveated resolution (outer plate at quarter res + line-aware upscale is cheap and probably invisible when not stared at); zero boil is worth more than detail; every side change should coincide with a picture change so the brain attributes it to the picture. Trade-off: the user does stare at the sides to judge; keep the inner band at the picture's line quality.
- **U5 Trails: unfold the edge in time.** The picture's edge columns over the last/next ~0.5 s are the content that belongs further out in the side right now. A character exiting right: the right side shows their pre-exit drawings shifted outward by their measured velocity (walk cycle intact, identity perfect, zero invention). A character entering left in 0.5 s: the left side already shows them walking in, from the *future* edge columns. Needs a matte over the plate and velocity decay for things that stop just outside. Covers the most common edge event (person box at an edge in 52% of frames [L]) for free. Invents timing, not appearance: a by-eye decision for the user.
- **U6 Multiplane parallax** only where the content model sees two flow populations in a shot: split the plate into 2 depth layers from the mosaic's flow; else a 2D transform is geometrically exact for anime pans/zooms.
- **U7 Audio as an off-screen cue** (speculative, later): panned off-screen SFX -> a small wash/motion on that side.

## 7 Alternatives and trade-offs

- **A. The obvious causal student** (sec 4): token-bound, future-blind, drift-prone; keep as offline oracle/teacher only.
- **B. Pure non-generative compositor** (plate + cels + trails, no refiner): cheapest, zero boil, identity by construction; fails on hidden articulation, never-seen objects entering from outside, and FX crossing the seam. Might already satisfy the periphery: it is R1, so it comes first anyway.
- **C. Full-canvas per-drawing image generation** (no plate/band split, one 2B few-step painter): 2B x 2 x 4,700 tokens x 4 = 75 TFLOP per drawing -> 3.4 s each: ~50x over in action, ~10x in comedy [E]. Simplest pipeline, impossible arithmetic. The split is forced by the math, not by taste.
- **Quality-first note**: the compositor is also the quality-correct architecture for anime (holds, no shimmer, identity via references, cross-cut consistency via sets), not a speed compromise. Where it is weaker: hidden articulation and novel FX; that is exactly where the refiner's budget goes.

## 8 Work checklist (ordered, cheap first)

1. R3 arithmetic (CPU): BG clustering + schedule simulation on both episodes; decide painter size from the result.
2. R1 demo (CPU): scrolling plate + cel warp + trails on 3 JJK shots; two-screen forced choice; record the verdict verbatim.
3. R2 measurement (GPU, when the queue is free): Turbo 8-step FP8 foveated plates x10, timed, judged.
4. Pre-pass as a product component: cue sheet with shots, holds, camera offsets, tracks, CCIP identity clusters, BG clusters, OP/ED.
5. Compositor in the performer: plate + band textures, per-frame transform, cels, trails, seam pull, fit, flash wash, freeze, upgrade-at-cut rule, side steps on the picture's frame clock.
6. Scheduler: plates ahead by set, cels by appearance, refiner change-gated; degrade ladder; GPU-share guard (S1).
7. Training: band dataset builder (3.3) -> 4-step distillation of the merged painter -> depth prune 7.1B -> 3.5B and heal -> band refiner <= 1B at 2 steps -> encoder-decoder split -> NVFP4.
8. Later: per-show background LoRA, multiplane, audio cues.

## 9 Success metrics

- User, two screens, action shots: >= 70% forced-choice preference over the still plate; verdict "moves with the picture"; identity unchanged across a shot.
- Harness: side change-per-frame correlates with picture change-per-frame (r >= 0.7), boil <= 1.5x the truth's, seam <= x3 on moving shots, near/far error <= today's Qwen plate on static shots.
- Throughput: episode GPU-seconds <= 0.6 x runtime; run-ahead buffer never below one shot after a <= 60 s start on JJK; zero late performer frames (S1 criterion); generator resident <= 6 GB.

## 10 Assumptions (in place of questions)

- Picture 1080p; 32:9 is the primary target, 48:9 secondary. **High.**
- Generator share 0.6, FP8 effective ~22 TFLOPS (anchored on the 170-s plate and the 113 TFLOPS matmul). **Medium**; R2 re-anchors it.
- The outpaint LoRA transfers to Turbo/4-step weights. **Low**; R2 tests it; fallback is self-distillation.
- Set reuse >= 1.5x on action. **Medium**; R3 measures it.
- Research-licence Qwen is acceptable for personal local use; derived students inherit the question if ever distributed. **Medium** (user: "licences later"); distribution would flip the painter to an Apache base (klein-4B/Z-Image/Wan-derived) as teacher.
- Downloads are allowed once the GPU queue is free (user memory). **Medium.**
- Peripheral-vision facts are textbook [R]; DiT depth pruning to ~50% with healing [R]. **Medium.**

## Unresolved questions (for the user, with defaults)

1. Is a softer (quarter-res, line-upscaled) far side acceptable when stared at? Default: yes for the outer half; flip if the forced choice says "blurry".
2. Trails show a character before they enter the picture: invention of timing, not appearance. Default: allow exits, gate entries behind a by-eye check.
3. 48:9 priority vs 32:9: default 32:9 first; 48:9 inherits everything at ~1.3x plate cost.
4. Licence endgame (personal tool vs distributed product) decides the painter's base model before the pruning investment.
