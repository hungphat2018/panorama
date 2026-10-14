---
title: "AI surround: quality baseline, built from replaceable roles"
status: in-progress
created: 2026-10-12 16:39
track: AI surround (anime outpainting)
prior: plans/handoffs/ai-surround-anime-outpainting-20261012-1615.md
synthesis: plans/reports/research-261012-1521-ai-surround-anime-outpainting-synthesis.md
---

# AI surround: quality baseline

## Outcome

The **quality baseline** for "extend the picture outside the frame": the best sides we can make today with no speed limit,
built from replaceable parts (roles), written down with numbers, videos and the user's blind-test verdict. Every later
change (faster model, new tool, fine-tune) is measured against it. A model is a tool inside a role, never the goal.

## Constraints

- Real picture never altered (ADR 0001); film content stays local (no hosted pages, no external APIs).
- Quality first; speed and VRAM are information. Big models run locally, Q8 / FP8 + offload. Licences: test all, decide later.
- One heavy GPU job at a time; no big download while a GPU job runs.
- By eye decides (ADR 0017): truth as anchor, forced choice (blind "real or generated?" test).

## Non-goals (this session)

Speed, real-time, performer integration, ADRs, per-show fine-tuning (the first lever after the baseline), production-grade
automatic reference search (hand-picked "oracle" references are allowed and labelled as such).

## Acceptance criteria

1. Every candidate is a **recipe**: a chain of role tools that writes the same layout (`runs/<recipe>/<scene>/s0.5.mp4`,
   real centre pasted) and is scored by the same harness (`hidden_sides.score`, sheets, truth-above videos).
2. Recipes on the anchor scenes cover: today's best (klein plate + fit + live, VACE-1.3B, keyframe-guided VACE), the
   motion wraps, reference-guided layout, one bigger painter and one bigger animator.
3. One blind test with all recipes; the user's results and by-eye notes recorded in the synthesis.
4. Baseline declared: best recipe per scene type with its numbers; the weakest role named; the next lever named.
5. Real-use check: the hidden-sides test cuts real frames (the model sees the middle half; the hidden quarters are the
   truth), so it measures continuation of what the animators drew, not invention beyond the true frame edge. The best
   recipes also run on full, uncut frames (16:9 extended to 32:9) and the user picks between recipes by eye (no truth:
   "which looks most like the same show filmed wider?"). Real wider truth needs an anime released in two aspect ratios.
   The two gates differ (user, 18:12): no right answer beyond the true edge (plausibility, not fidelity); the real edge is
   a composition boundary, the test cut is not; real sides sit in peripheral vision on other monitors, often behind a
   bezel (strict on flicker and brightness, lenient on detail); the test model sees a 8:9 half frame, the real one 16:9.
   The hidden-sides test stays the development gate for what both share (colour, style, scale, seam, motion); the
   baseline verdict needs the real-use check on the user's monitors, and the recipe ranking of the two is compared: if
   they agree, the cheap test is trusted for later work.

## Concept map

### Invariant

The sides read as the same shot filmed wider; nobody can tell where the real picture ends; the real picture is untouched.

### What "the same shot" means (quality dimensions)

1. **Style**: lines, flat fills, shading of this show.
2. **Ratio**: object scale and perspective continue (no 30% bigger tiles).
3. **Colour**: same grade and haze.
4. **Composition**: what the frame cuts continues outward; the model never "closes" it into a new picture.
5. **Identity** (new): a person or object cut by the frame stays the same across the seam. Seen on edge-person-still:
   VACE turned the ponytail into a bob, klein turned the green sailor collar navy.
6. **Seam**: no visible line.
7. **Motion and cadence** (new): the sides move only when and where the picture moves. Anime holds each drawing 2-3 film
   frames (74% of frame steps repeat here); sides that keep changing during a hold "boil".
8. **Consistency across cuts** (new): back in the same room, the sides show the same room. The 3 s hidden-sides scenes
   cannot measure it; needs a cut-sequence test (e.g. shots 102-106, the bathroom) after the baseline.
9. **Invention** only as much as the user allows (decided by eye).

### Where side content can come from (information sources)

| Source | Example in the test episode | Used today |
|---|---|---|
| S1 the visible picture | the tub runs past the seam | yes |
| S2 other frames of the same shot | a pan reveals the next wall | VACE sees them; real pixels cover 0.1% of sides here |
| S3 other shots of the episode | shot 163 shows the ponytail girl from the front; shots 103/105 show the bathroom from other sides; shot 159 shows the crowd and the sign from the reverse angle | **no** |
| S4 structure of the picture | tile size, perspective lines, horizon | only implicitly, inside the model |
| S5 knowledge | generic anime prior; anime-tuned model; this show learned from its own frames | generic only |

Reading: every failure seen so far is a **missing source**, not only a weak model. Identity <- S3. Far-out place <- S3 + S2.
Ratio <- S4. Composition "closing" <- S5 (generic outpaint training teaches closing).

### Roles (each replaceable behind the same contract)

| Role | Job | Today | Candidates |
|---|---|---|---|
| R1 understand | shots, held drawings, camera slides, who touches the edge | content model | - |
| R2 gather | references from S2 / S3: the cut-off character in full, the same place from another angle | oracle picks (`pick-references.py`) | crops, not whole frames (whole frames made klein copy the wide shot's layout and scale); later automatic: CCIP on head tracks -> MegaLoc -> SIFT/RoMa v2 (report B) |
| R3 lay out | one high-quality still per shot: what the sides contain | klein-4B (distilled!) + fal outpaint LoRA | klein-base-4B + the same LoRA (its real base); Qwen-Image-2.1 + ausboss outpaint LoRA v1/v2 (best open editor, pixel-registered); Anima-Base + inpaint LLLite (anime-only, user's efficiency question); runner ComfyUI 0.39.0 (`comfy-plates.py`) |
| R4 animate | sides that move with the picture, seamless, grown from R3's still | VACE-1.3B | Wan2.2-VACE-Fun-A14B Q8 first; AnimeGen + VACE-Fun module (tensor-compatible, unproven); LTX-2.5; AniSora (report A) |
| R5 match | colour fit, live seam pull, hold with the picture's cadence, freeze sides where the picture is still | fit, live | hold, freeze (new, no GPU) |
| R6 judge | scores as pre-filter, best-of-N, blind test, the user's eye | scores, sheets, blind test | best-of-N |

**Contract.** Input: the shot (clip, content facts) plus any upstream recipe's outputs and references. Output:
`runs/<recipe>/<scene>/s0.5.mp4` (+ `raw-*` model canvas, `recipe.json`: the chain, parameters, timings). Same scorer and
blind test for all, so a role's tool can be swapped without touching the others.

**Routing by motion (measured 2026-10-12, 20:15).** A plate keyed into a video model is reproduced only at the key frame;
the other frames drift (held-dialogue: Qwen plate alone far 12.4 / seam x1.18 over the whole clip; Qwen-keyed VACE-1.3B
far 22.4 / seam x2.30). So the plate owns everything that does not move and the video model only what moves (a
character crossing the seam, a camera slide). R1 already flags still edge bands (51% of shots): route still shots and
still bands to the plate, moving ones to the keyed video, with a crossfade where a band starts or stops moving. The
test set has almost no story shots with motion at the edges, so this route needs an action episode to be measured.

**Break-down variant (same contract).** Anime is drawn in layers: background painting + characters + effects + a camera
filter (haze, glow). R3/R4 can work per layer: background extended once per shot (cannot boil, moves with the camera),
characters completed from S3 references, haze put back over everything. Build only if report B finds the tools ready and
the baseline shows boiling or identity as the weakest role.

### Evidence by role (2026-10-12, s = 0.5)

| Recipe | near | far | seam | flicker | By eye |
|---|---|---|---|---|---|
| klein-green + fit + live (R3 + R5), mean of 8 scenes, whole clips | 25.1 | 27.3 | x6.8 | - | style right; ratio off; closes compositions; static |
| VACE-1.3B (R4), held-dialogue | 13.4 | 30.6 | x1.84 | +0.53 | colour and tub continue; far out a different room |
| VACE-1.3B, edge-person-still | 25.4 | 65.0 | x2.11 | -1.67 | ponytail -> bob; far out purple cliffs instead of sky |
| VACE-1.3B, edge-head-closeup | 11.6 | 63.9 | x1.84 | +0.42 | (sheet to view) |
| VACE-1.3B, pan-horizontal | 8.2 | 22.4 | x3.37 | -2.24 | (sheet to view) |

Each tool fails where its information is missing: the image model knows what but not where (scale), the video model
knows where (seam, motion) but not what far out.

## Phases

| # | Phase | GPU | Status |
|---|---|---|---|
| 1 | Collect the running queue (VACE 4 scenes done -> synthesis 8; keyframe-guided VACE; blind-test rebuild), re-run klein full-res (`--suffix=-full`: the queued run failed on argparse) | busy to ~17:20 | running |
| 2 | R5 wraps `hold` + `freeze` (`apply-wraps.py`: boil +0.43 -> -0.03), R2 oracle references for the 4 story scenes (`pick-references.py`) | none | done |
| 3 | R2 -> R3 klein `--refs` (done: helps held-dialogue, hurts close-ups; whole frames are the wrong reference form); R6 best-of-8 with no-truth judge signals (running); R5 tone + live + freeze on keyframe-guided VACE (done: held-dialogue far 27.2 -> 19.2, seam x1.64) | ~1 h | running |
| 4 | R3 round: klein-base-4B, Qwen-Image-2.1 + LoRA v1/v2, Anima + LLLite (downloads approved, may overlap GPU jobs) -> pick the best plate by eye -> keyframe-guided VACE on the edge scenes with it | ~2 h | queued |
| 5 | R4 capacity: Wan2.2-VACE-Fun-A14B Q8 (37 GB) via diffusers or ComfyUI, guided by the best R3 still, story scenes; then AnimeGen + VACE-Fun | downloads + hours (overnight) | pending |
| 6 | Layered pilot (background + character completion vs same-reference control, report B), only if identity stays the weakest role | ~1-2 d | parked |
| 7 | Blind test with all recipes -> baseline record in the synthesis | none | pending |

## Round 2: action episode, 14B, moving-shot tools (2026-10-12 night, in progress)

- **Test set +5 scenes** from `Jujutsu.Kaisen.S03E12` (action, the user's file): `out/scenes/jjk-{pan,edge-move,one-side,
  action,still-edge-person}` (`make-test-scenes.py --set moving --prefix jjk-`, picked by eye from the rule's top 5;
  content model `out/jjk/content/`, per-side edge motion `out/jjk/edge-band-change.json`).
- **Measured**: the outer 25% (the hidden side) holds for the whole shot in 12% of the action episode's frames (comedy
  episode: 43%); 60% of steps change it (comedy: 24%). Plates alone can serve little of an action episode.
- **New tools** (same contract): `scroll-plate.py` (R2 + R3 + R5: the shot's stitched background, completed by the
  painter, slid with the camera; for camera slides), `route-by-motion.py` (R1 + R5: plate side where the picture's band
  holds >= 90% of steps, video side elsewhere), `make-wide-moving-demo.py` (real-use check for moving shots).
- **GPU queue** (`out/runs/queue/queue.txt`, runner log `runner.log`): A14B keyed by Qwen (3 story scenes) -> Qwen
  plates (jjk) -> seam match -> scroll plates with painter -> VACE-1.3B plain and keyed (4 moving jjk scenes) -> A14B
  plain (3 story scenes); then A14B on the jjk moving scenes, wraps, routing, blind test, real-use demos.
- **Stopped 2026-10-13 ~00:20 (low memory).** Claude Code reaped the runner while the session was idle: the one-prompt
  A14B graph kept both experts + umT5 loaded (ComfyUI 54 GB committed on 32 GB RAM, pagefile 21 -> 40 GB, C: 51 -> 21 GB
  free). Its note: restart only on the user's request. The in-flight step (A14B keyed, 3 story scenes; held-dialogue done
  23:49) was left to finish by itself; the runner loop was stopped so nothing else starts. At 00:53 its video encoder died
  under the same memory pressure after edge-person-still was generated (finished on the CPU 2026-10-13 09:10 from
  ComfyUI's saved frames); edge-head-closeup never ran.
- **AnimeGen (2026-10-13 09:20-09:40)**: the user asked why no AnimeGen frames exist (T2V only, no mask input). Checked
  the remote GGUF header: NullpoLab's AnimeGen-T2V Q8_0 has the same 1095 backbone tensors as QuantStack's VACE-Fun Q8_0
  (names, shapes, quant types), VACE-Fun adds only the 236 VACE tensors. So `comfy-nodes/panorama-gguf-graft` (loaded via
  `custom_nodes:` in `extra_model_paths.yaml`) swaps AnimeGen's backbone into VACE-Fun at load time, no merged file;
  `comfy-vace-clips.py --animegen both|low`. The user approved deleting FLUX.2-klein-4B + its LoRAs (`hf cache rm`, 16.1
  GB freed, C: 43 -> 58 GB) and downloading AnimeGen Q8 (30.8 GB, `tools/models/comfy/wan/animegen-t2v/`). User's order:
  AnimeGen keyed on the story scenes first, then the action queue. New `queue.txt`: wait for plates + download -> AnimeGen
  smoke (17 frames, `out/smoke/`) -> Qwen demos on two action moments -> waits for `out/runs/queue/go-animegen` (by-eye
  check of the smoke) -> AnimeGen keyed (held-dialogue, edge-person-still) -> A14B keyed edge-head-closeup -> the
  action queue. Result: both experts grafted = blank white sides; low-noise only = real content but more invented clutter
  and a seam. **The user rejected AnimeGen ("far away worse than Qwen") and had it removed (10:50)**: run stopped, model
  files deleted (C: 50 GB free), graft node and `--animegen` removed from the code and the ComfyUI config.
- **Real-use verdict 2026-10-13 ~10:45 (user)**: the still plate breaks continuity on action ("one frame"); still plates
  are for still shots only. Moving versions of the same fight queued (scroll, A14B keyed per side); the queue was reordered
  so the action measurements come first. Handoff: `plans/handoffs/ai-surround-moving-sides-20261013-1040.md`.
- **Blind test rebuilt 2026-10-13 09:15** (`out/blind-test/index.html`): 21 trials, comedy episode only (plate x8, VACE-1.3B
  x4, 1.3B keyed x3, A14B keyed x2, scrolling plate x2, catch x2); the action episode has no AI candidates yet.
- **Fix, untested on the GPU yet**: `comfy-vace-clips.run_two_phase` runs one expert per prompt (latent saved between,
  `/free` after each), and every big-model ComfyUI run starts with `cc.LOW_RAM` (`--cache-none`: a model leaves RAM once
  its consumers ran). Expected peak: one expert (~19 GB) instead of 54 GB.
- **Resume** (on the user's go-ahead): `cp out/runs/queue/queue-next.txt out/runs/queue/queue.txt`, then start
  `bash spikes/ai-surround/out/runs/queue/run-queue.sh` from the repo root (23 steps, ~19 h; the A14B plain story run is
  step 7; first usable blind test after step 18).

## Risks

- Oracle references overstate what automatic search will find: labelled; R2 automation measured after the baseline.
- A model may ignore references or copy them whole into the sides (wrong composition): checked by eye on the sheets.
- A14B runner on Windows / 12 GB (diffusers GGUF vs ComfyUI): settled by report A before any download.
- A14B at Q8 with two-phase offload takes hours per clip: run as an overnight queue, three scenes first.
- `hold` / `freeze` can stop a side character that should move (the truth's jumping girl): check flicker < 0 cases by eye.

## Reports

- A: `plans/reports/researcher-261012-1639-reference-conditioned-painters-and-big-model-runners.md` (R3 pick Qwen-Image-2.1 + outpaint LoRA; klein LoRA belongs on klein-base; R4 order; ComfyUI 0.39.0 pinned + diffusers)
- B: `plans/reports/researcher-261012-1639-anime-references-layer-split-and-structure-guides.md` (layered = one pilot arm, weakest link the character join; free references: ending design sheets, shots 335/337/340)

## User decisions (2026-10-12)

- 17:36: download Qwen-Image-2.1 + LoRA, klein-base-4B and Anima (general + anime-only side by side); downloads may run while the GPU tests.
- 17:35: asked whether Qwen-Image-2.1 is anime-optimized ("we don't need realism data"): no; anime-only models are the efficiency lever for the speed phase, tested now next to the general one.
