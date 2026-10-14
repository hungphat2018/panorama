---
title: "AI surround for anime: synthesis of research + first hands-on results (quality-first)"
date: 2026-10-12
type: research synthesis
inputs:
  - plans/reports/researcher-261012-1407-real-time-generative-models-and-neural-rendering.md
  - plans/reports/researcher-261012-1407-outpainting-sota-and-anime-fine-tune-bases.md
  - plans/reports/researcher-261012-1407-gpu-sharing-and-inference-runtime-on-windows.md
  - plans/reports/researcher-261010-1846-unused-canvas-area-periphery-and-outpainting.md
  - spikes/ai-surround/ (hidden-sides harness, runs on the test episode)
---

# AI surround for anime: synthesis (2026-10-12)

Tags: **[L]** measured on this laptop today · **[V]** verified source (via the research reports) · **[E]** estimate · **[eye]** by-eye look at the sheets, no viewer study.

Terms: **sides** = the generated parts of the canvas left and right of the real picture. **seam** = the line where a side meets the picture. **plate** = one generated still used for a whole shot. **shot** = film between two cuts. **drawing** = one new hand-drawn image (anime holds each for 2-3 film frames). **hidden-sides test** = take real frames, hide their outer parts, let a candidate redraw them, compare with the hidden truth.

## 1 Goal and how it is judged (changed today)

The user set the priority on 2026-10-12, 15:16-15:22: **quality first, at any cost: preserve the art style, ratio, colour and composition; speed and hardware are not filters.** This matches ADR 0001 ("the video is never distorted in any way and keeps its composition") extended to the generated sides. Under ADR 0017 the surround is a "what you see" decision: the user's by-eye verdict decides.

Quality bar for the sides, in order:
1. **Art style**: line weight, flat colour fills, shading of this show.
2. **Ratio**: proportions, object scale and perspective continue the picture's (no bigger tiles, no stretched look).
3. **Colour**: the same grade (haze, tint, brightness) as the picture.
4. **Composition**: objects cut by the frame continue outward; the model does not "close" the picture into a new one.
5. **No seam**, **motion that follows the picture**, **no flicker**, **invention only as the user allows** (decided by eye).

Speed is now information only. The end state stays "watch while it generates" (memory, 2026-10-12): the chosen pipeline must later be made to run ahead of the playhead, but that is optimisation, not selection.

## 2 Measured on the test episode [L]

`[Judas] Nama-Anaru - S01E02.mkv`: 1080p HEVC 10-bit, 23.976 fps, 34,047 frames, gag comedy, few pans.

| Fact | Value | Meaning for the sides |
|---|---|---|
| Shots | 357, median 3.3 s | per-shot work units are short |
| Held frames | 74% of frame steps repeat (~6.3 new drawings/s) | motion arrives in steps, not every frame |
| Person box touches left/right edge | 52% of frames (head 27.5%, face 6%) | the sides often must continue a body or head |
| Camera slides >= 1 s | 2.3% of frames, mostly vertical | real pixels seen during pans cover 0.1% of the sides: "keep the seen" cannot be the backbone |
| Outer 10% edge band static for the whole shot | 51% of shots, 52% of frames | a still plate can only be right for about half the shots |
| Frames in shots with large edge drift | ~26% | the other half needs sides that move |

## 3 Hands-on results [L]

Hidden-sides test, s = 0.5 (each side half the visible width: 16:9 picture on a 32:9 canvas), 8 test scenes of 3 s (title card, person at edge, head close-up, held dialogue, horizontal pan, vertical tilt, dark, warning graphic). Errors are mean absolute 0-255 differences to the hidden truth, near = next to the seam, far = outer half; **seam** = jump across the seam divided by the truth's own jump there (1.0 = as smooth as the real picture). A low error is not the goal by itself; the sheets put the truth on top as the by-eye anchor.

### 3.1 Non-AI baselines (whole clips)

| Fill | near | far | seam |
|---|---|---|---|
| black | 166 | 179 | x64.7 |
| glow (parametric) | 73 | 124 | x26.0 |
| blur-extend | 42 | 108 | x10.1 |
| mirror-blur | 43 | 109 | x9.4 (duplicates faces and text) |

### 3.2 FLUX.2-klein-4B (Apache-2.0), one still per scene, middle frame

Generated at 960x544, 4 steps (distilled), FP8 weight storage, upscaled, real centre pasted back. 4.7 s per plate, 5.1 GB peak VRAM (information only).

| Variant | near | far | seam (key frame) | Verdict |
|---|---|---|---|---|
| fal outpaint LoRA, green sides ("klein-green") | **21.4** | **33.0** | x5.7 | works; style right; seams show (below) |
| native masked fill ("klein-inpaint") | 63.0 | 86.5 | x16.9 | broken on bright scenes: sides come out black |

klein-inpaint cause [L, diffusers 0.41 source]: `Flux2KleinInpaintPipeline` always feeds the input image as an extra reference image; our input had black sides, and the editing model copies the reference. Not a judgement on masked fill; it needs pre-filled sides to be tested fairly.

By eye, klein-green [eye]:
- **Art style: good.** Flat fills, clean line art, the show's look; cut-off characters completed believably (the ponytail girl in edge-person-still, the boy's head in edge-head-closeup).
- **Colour: wrong but fixable.** It drops the episode's soft green haze and draws clean white; that tone jump is the visible seam.
- **Ratio: off.** Wall tiles ~30% larger than the real ones; the tub's height and angle differ from the picture's.
- **Composition: closes instead of continuing.** The warning sign and the title text are finished inside the sides instead of running outward; the graph-paper grid fades out far from the seam.
- **Invention: sometimes too much.** pan-horizontal: a large grinning face where the truth is only hair.

### 3.3 Seam correction without a generator (klein-green plate, whole 3 s clips)

`seam-match.py`: (a) **fit** = once per shot, a 3x4 colour matrix maps the model's own redraw of the centre onto the real centre, applied to the sides; (b) **live** = once per film frame, the jump between the picture's edge columns and the plate's seam columns, smoothed along the edge, added to the plate with an exponential fade (performer-shader style).

| Plate version | near | far | seam |
|---|---|---|---|
| raw | 36.5 | 28.8 | x13.8 |
| fit | 34.3 | 27.6 | x12.6 |
| live | 25.4 | 28.5 | x6.8 |
| fit + live | **25.1** | **27.3** | **x6.8** |

- Static scenes: fit + live brings the seam to x2.2-2.7 (from x3.8-6.3); on held-dialogue the fit alone takes the far error 31 -> 20 and restores the haze [eye: colour seam nearly gone; the structure mismatch stays].
- Moving scenes (action, pan, tilt): a still plate stays far off however it is corrected (seam x4-14, near 32-56). These need sides that move.
- Conclusion: **colour is solved by a cheap per-shot fit; ratio, composition and motion are not**, and they are what the user ranks.

### 3.4 Wan2.1-VACE-1.3B (Apache-2.0), video outpainting

Pending in this report version; see section 8.

## 4 Quality levers (invariant -> levers)

Invariant: the sides read as the same shot (style, ratio, colour, composition), the seam disappears, motion follows the picture, the real picture is untouched.

| Lever | Options | Evidence so far |
|---|---|---|
| Q1 model family | image per keyframe / video per window / real pixels from other frames | image: style ok, no motion [L]; video: only family shown on moving shots [V]; real pixels: 0.1% coverage here [L] |
| Q2 capacity | klein-4B -> Qwen-Image-Edit-2511 20B / FLUX.2 dev 32B (NC); VACE-1.3B -> VACE-14B / Wan2.2-VACE-Fun-A14B; AniSora-AnyMask (anime-trained 14B) | bigger video models were rejected only for speed/VRAM before today; now in scope |
| Q3 resolution | gen at 0.5 scale -> full 1080p -> tiled refinement (HL-OutPaint) | 0.5 scale upscaled 2x: lines softer than the picture's [eye] |
| Q4 steps / distillation | distilled 4 steps -> base model 50 steps + CFG | fal LoRA was trained against klein base, we ran it on the distilled model |
| Q5 conditioning | colour sentinel (green) / explicit mask / latent blending at each step | sentinel = colour drop at seam [L]; masked fill must not see black sides [L] |
| Q6 context | visible picture only / + other frames of the shot / + keyframes (HL global) / + scene caption | image model sees one 8:9 crop: ratio and composition guessed [eye] |
| Q7 colour harmonisation | per-shot colour fit, live seam pull | fit fixes grade; live halves the seam jump [L] |
| Q8 anime fine-tune | LoRA on self-supervised anime crops (hide sides of real shots, learn to redraw them) | generic outpaint data teaches "close the composition"; anime crops teach "continue it" [E]; recipe and costs in the outpainting report Q8 |
| Q9 invention control | prompt / negative prompt / LoRA scale / guidance / face-aware limits | by-eye decision pending (see 6) |
| Q10 best-of-N | generate several per shot, pick by seam / similarity / VLM judge | untested; pure compute for quality |
| Q11 temporal stability | window overlap, keyframe anchoring, colour drift control | community failure modes: drift, seams, boiling [V] |

## 5 Candidate pipelines, quality-first

- **A. Keyframe-global + local-window video (HL-OutPaint idea on open parts).** Per shot: outpaint one or more keyframes with the best image model at full resolution (layout, ratio, composition), then a mask-capable video model (VACE-14B or Wan2.2-VACE-Fun-A14B) fills the sides for the whole shot conditioned on those keyframes, then colour fit and seam checks; anime LoRA once the base is chosen. Matches what the user liked in HL-OutPaint; no HL code exists, but its parts are training-free glue around VACE plus two LoRAs [V]. Runs ahead of the playhead later; cost is a speed problem.
- **B. Per-shot still plate + live seam pull.** What was measured today: right style and colour, wrong on moving edges (~half the shots), ratio and composition guessed. Stays as preview / fallback while A's results arrive.
- **C. Hybrid by shot.** The content model's edge-band drift (51% static shots) routes static shots to B-quality plates and moving-edge shots to A. Only worth it if A is clearly better on moving shots and B is indistinguishable on static ones (by eye).

Recommendation: **target A** for quality; keep B as the cold-start / seek fallback (with parametric glow before any plate is ready). The cue-sheet "plate" should be a timestamped texture asset (still now, video later) with per-shot enable + strength (real-time report), so A drops in without redesign.

## 6 Decisions for the user (by eye and scope)

1. **By-eye forced choice per scene, truth on top**: sheets in `spikes/ai-surround/out/runs/sheets/` (`*-klein-s0.5.png`, `*-klein-green-seam-s0.5.png`, `*-vace-s0.5.png`) and videos in `out/runs/seam-videos/`, `out/runs/vace-1.3b*/<scene>/s0.5-with-truth.mp4`.
2. **Invention level**: is completing a cut-off character "as if filmed wider" acceptable (edge-person-still: ponytail girl), and is inventing new parts (pan-horizontal: a face where the truth is hair) not?
3. **Where the 14B-class models run** (VACE-14B, Wan2.2-VACE-Fun-A14B, AniSora-AnyMask, AnimeGen): answered, locally, quantized + offloaded (6.1); needs disk (6.3).
4. **LTX-2.5 + official In-Outpainting IC-LoRA** (LoRAs are "Derivatives" with a transfer clause [V]): answered, test it, licence later.
5. **Non-commercial models** (FLUX.2 dev 32B, klein-9B, Anima anime model): answered, test them, licence later.
6. **Disk**: free or add ~45-85 GB before the 14B round (6.3).
7. **More anime**: one gag-comedy episode is not enough; an action episode and a 2.39:1 anime film (the only real "wider" ground truth) would make the ratio and composition checks fair.

### 6.1 User answers (2026-10-12, ~16:00)

- Big models: **run locally, quantized + offloaded, least quality loss possible** (not a rented GPU).
- Licences: **test all, decide later** (LTX and non-commercial models are in the quality tests; Apache-2.0 stays marked as the safe choice).
- Asked: "is AniSora the best model yet?" Answer in 6.2.

### 6.2 Is AniSora the best anime model? (checked 2026-10-12 against GitHub / Hugging Face APIs [V])

- **AniSora (bilibili Index-AniSora)**: newest weights V3.2 (Wan2.2 base, 2025-09) and AnyMask (Wan2.1-I2V-14B, spatial + temporal masks, 2025-10-31); HF last modified 2025-10-31; 2026 repo commits are credential clean-ups and a workshop page (last 2026-07-16). AnyMask is the only anime-trained model with masks, but outpainting is never shown.
- **Newer, and missed by this morning's pass: AnimeGen T2V / I2V** (AIdeaLab, Japan, built with the METI/NEDO GENIAC programme), created 2026-06-17, Apache-2.0, diffusers, fine-tuned from Wan2.2-A14B "to improve anime-style motion, character rendering, color composition, and visual consistency". No mask input. Community GGUF builds exist (NullpoLab, 2026-07-18).
- **Combination worth testing first**: AnimeGen-T2V and Wan2.2-VACE-Fun-A14B (VACE mask control for Wan2.2, Apache-2.0) are both fine-tuned from **Wan2.2-T2V-A14B**, so VACE-Fun's mask blocks should attach to AnimeGen's backbone without training (Kijai publishes the VACE-Fun blocks as separate modules). Anime prior + native outpainting; compatibility unproven.
- No benchmark compares any of these on anime outpainting: "best" is settled by the hidden-sides test and the user's eye. Candidate set for the 14B round: AnimeGen + VACE-Fun blocks, plain Wan2.2-VACE-Fun-A14B, AniSora-AnyMask, LTX-2.5 + In-Outpainting IC-LoRA.

### 6.3 What local 14B tests need (Q8 = 8-bit GGUF, near-lossless)

| Item | Size | Source |
|---|---|---|
| AnimeGen-T2V, two experts (high / low noise) | 2 x 15.4 GB | NullpoLab/AnimeGen-T2V-GGUF Q8_0 |
| Wan2.2-VACE-Fun-A14B, two experts | 2 x 18.7 GB | QuantStack/Wan2.2-VACE-Fun-A14B-GGUF Q8_0 |
| VACE-Fun blocks only (for the AnimeGen combination) | ~2 x 6.6 GB bf16 [E] | Kijai/WanVideo_comfy `Fun/VACE/` |
| umT5 text encoder + Wan VAE | already here (VACE-1.3B) | - |

- **Disk: 15 GB free.** The AnimeGen combination needs ~44 GB, plain VACE-Fun ~37 GB, both ~82 GB. Space must be freed or added first.
- **RAM: 32 GB.** Two Q8 experts (31-37 GB) do not fit together: run the high-noise expert's steps, unload, then the low-noise expert's steps (two-phase sampling); blocks stream to the 12 GB GPU.
- Speed (information only): hours per 3 s clip at full steps.

## 7 The four research reports in one paragraph each

- **Real-time generative models (2026-10-12)**: per-shot plates are "build now" on this laptop; streaming video outpainting ("Adapting VACE", 17.2 fps at 368x640 on an RTX 5090) is ~6-12 months out here; live per-frame generation is 26-90x over budget; DLSS 5 and Anime4K only transform existing pixels. Under quality-first this ranks speed classes, not quality.
- **Outpainting SOTA + anime bases (2026-10-12)**: rank 1 Wan2.1-VACE (1.3B to iterate, 14B for quality, Apache-2.0); rank 2 LTX-2.5/2.3 + official IC-LoRA (licence trap); rank 3 AniSora-AnyMask (anime-trained, outpainting unproven); rank 4 FLUX.2-klein-4B + fal LoRA for plates. HL-OutPaint: still no code; its strategy is reproducible glue. Community failure modes: colour drift, seams in ~30% of clips, line-weight loss, "too smooth" motion, black-sentinel confusion. First LoRA: tens of dollars on 80 GB, not trainable beyond 1.3B here.
- **GPU sharing + runtime on Windows (2026-10-12)**: the generator should run ahead of time in a separate worker with a disk cache; tiny CNNs can live inside the performer's passes; a cross-process real-time ring has no hard protection on GeForce. Runtime: PyTorch in the worker; ORT C API + TensorRT-RTX EP for C-side inference. Matters after the quality pick.
- **Unused canvas area (2026-10-10)**: parametric edge-continuous glow for the concept demo; generative outpainting was then "not usable" for speed and code reasons. Still right for the demo; superseded as the long-term direction by today's quality-first track.

## 8 VACE-1.3B results (added 2026-10-12, 17:10)

Plain VACE-1.3B [L]: 50 steps, CFG 5, 848x480 upscaled, real centre pasted, whole clips (69 frames; pan 25). The `-fit`
version changes little: the colour already continues.

| Scene | near | far | seam | flicker | s / frame |
|---|---|---|---|---|---|
| held-dialogue | 13.35 | 30.6 | **x1.84** | +0.53 | 21.1 |
| edge-person-still | 25.4 | 65.0 | x2.11 | -1.67 | 20.8 |
| edge-head-closeup | 11.6 | 63.9 | **x1.84** | +0.42 | 20.6 |
| pan-horizontal (ending-credits graphic) | 8.2 | 22.4 | x3.37 | -2.24 | 13.2 |

By eye [eye]:
- **Seam: the best of anything measured.** Hair, ear, glasses, tub and floor run straight through.
- **Identity breaks past the seam.** The ponytail becomes a bob (edge-person-still). The boy seen from behind turns into a face in profile (edge-head-closeup).
- **Far out it invents another place and big occluders**:
  - a purple cliff (edge-person-still) and a large blue-purple hair mass (edge-head-closeup), where the truth is plain sky or wall;
  - another room on held-dialogue (arched red door).
- **pan-horizontal**: continues the red band and the grid paper; hair strands and collar stripes smear on the right.
- **Reading.** The video model owns the seam and the motion. It lacks the information about what lies far out and who the cut-off person is, which is the job of references and a keyframe (plan `plans/261012-1639-ai-surround-quality-baseline/plan.md`).

### 8.1 Motion wraps around any fill (no GPU) [L]

`apply-wraps.py`:
- **hold**: the sides repeat while the picture repeats.
- **freeze**: each side becomes its per-pixel median while the picture's band next to that seam is still.

boil = mean change of the sides on held steps, candidate minus truth.

| vace-1.3b-fit | boil before | boil after hold+freeze | seam before -> after |
|---|---|---|---|
| held-dialogue | +0.43 | -0.03 | x1.82 -> x1.76 |
| edge-head-closeup | +0.39 | -0.01 | x1.89 -> x1.76 |
| edge-person-still | -0.73 | -1.23 | x2.05 -> x2.07 |
| pan-horizontal (no holds) | 0.0 | 0.0 | unchanged |

- Freeze removes the boiling the video model adds while the real picture stands still, at no cost. Near and far errors are unchanged.
- Hold alone does less: its picture-wide detector is stricter than the content model's (66% vs 99.7% held steps on held-dialogue).
- Applies to any recipe (plate, video, layered).

### 8.2 Does the episode hold what the sides miss? [L, eye]

Contact sheets of the 8 shots before and after each anchor scene (`pick-references.py` keeps the hand-picked ones as `out/scenes/<kind>/refs/`):
- **Story scenes: yes.**
  - The ponytail girl from the front (shot 163); the crowd and the sign from the reverse angle (159).
  - The bathroom from both ends (103, 105).
  - The boy from behind in the same room, and the room wide (260, 276).
  - The night corridor (192, 196).
- **Graphic scenes: no.**
- **Test-set bias**: 4 of the 8 anchors are graphics: opening credits (action), ending credits (pan-horizontal), title card (wide-no-subject), eyecatch (tilt-vertical). The baseline verdict is weighted to the 4 story scenes; graphics are reported apart. Story shots with real sideways camera moves are almost absent in this episode (shot 83, 1 s): an action episode is still needed.

### 8.3 Queue note

The queued full-resolution klein run failed before loading anything: argparse reads `--suffix -full` as a missing value. The flag must be written `--suffix=-full`; it was re-queued with the reference runs.

## 8.4 Quality baseline round 1: roles, not models (added 2026-10-12, 18:00)

Frame: plan `plans/261012-1639-ai-surround-quality-baseline/plan.md`. The surround is built from replaceable roles: understand, gather references, lay out (still per shot), animate, match, judge. Each tool is scored by the same harness.

**Lay out + animate + match** (klein-green plate as VACE-1.3B's kept key frame, then tone + live + freeze) [L]. held-dialogue, whole clip:

| Recipe | near | far | seam | boil |
|---|---|---|---|---|
| VACE-1.3B plain | 13.2 | 30.3 | x1.82 | +0.43 |
| keyframe-guided | 17.1 | 27.2 | x2.20 | +0.25 |
| keyframe-guided + tone + live + freeze | **12.5** | **19.2** | **x1.64** | **-0.03** |

- By eye: the same bathroom; shelves and bottles run through the seam; tiles at the right size. Remaining problems: a second tub where the truth has a ledge and dark floor, and a pink-grey cast before tone.
- pan-horizontal: a still key frame on a moving graphic got worse (far 22 -> 34, flicker +2.3). A pan needs a key that moves with the camera.
- **tone** (per-side gain/offset from the bands either side of the seam) fixes a cast carried in from a plate. It hurts when different content legitimately meets at the seam: plain VACE edge scenes, far 65 -> 78 and 64 -> 67. Use it only in recipes with a plate cast.

**Gather references** (whole frames from neighbouring shots, klein-4B multi-reference) [L, eye]:
- held-dialogue (references framed like the scene): far 31.2 -> 23.0 with the explaining prompt.
- edge-head-closeup and dark: far 24 -> 78-82 and 6 -> 47. The model copies the wide reference's room, at the wide shot's scale, into a close-up, and adds a character.
- Lesson: hand references as crops (character) or as same-framing shots only.

**Judge** (best of 8 seeds, picked by signals that need no truth) [L]:
- `seam_self` (seam jump vs neighbouring columns) predicts the true seam (mean Spearman 0.61) and near error (0.30). The pick beat the mean seam on 6 of 8 scenes (edge-person-still x3.34 -> x2.45).
- `centre_fit` (the model's redraw of the given centre) predicts almost nothing.
- Neither predicts far content: that needs a judge that understands the picture.
- **Qwen3-VL-8B as a rating judge** (`vlm-judge.py`, 1-10 "how convincingly do the added parts continue the original", greedy) [L]: it does not discriminate.
  - It answers 7 for almost every draw and 1 for every title-card draw; on action it wrote sentences instead of a number.
  - The Spearman values it printed come from how tied ratings were ranked: ignore them.
  - Next judge designs: pairwise "which of A/B is one real frame", questions about specific failures (complete person, seam, extra characters), or a bigger VLM.
  - Until then the user's blind test is the only judge of far content.

**Lay out: the painter round (one still per scene, real centre pasted, key frame; 2026-10-12 19:40)** [L]:

| Painter (runner) | near | far | seam | Licence | s / plate |
|---|---|---|---|---|---|
| klein-4B distilled + fal LoRA (diffusers) | 21.4 | 33.0 | x5.7 | Apache-2.0 | 4.6 |
| klein-base-4B + fal LoRA (its real base, 50 steps, CFG 4) | 18.8 | 26.7 | x4.2 | Apache-2.0 | 108 |
| **Qwen-Image-2.1 + ausboss outpaint LoRA v2** (ComfyUI, 25 steps, CFG 1, Qwen3-VL caption) | **10.8** | **24.4** | **x1.44** | Qwen Research (non-commercial) | 170 |
| Qwen-Image-2.1 + LoRA v1 | 12.3 | 24.8 | x1.49 | same | 170 |
| Anima-Base + inpaint LLLite v2 (anime-only, kohya's settings, blur-prefilled) | far worse: action 81, held-dialogue 101, edge-person-still 143 far | | | non-commercial | 80 |

Means over the 8 scenes. Qwen v2 by eye:
- action: closes the sign inside the frame with a triangle on each side, as the truth does (klein made an endless band).
- edge-head-closeup: the boy seen from behind with one ear (klein-base drew two) and a gradient with no seam.
- edge-person-still: the ponytail girl's hair, green collar and skirt all right (klein turned the collar navy; klein-base invented a second girl).
- held-dialogue: far 12.4, the lowest of any recipe so far.

The Qwen3-VL caption can be wrong ("pixel-art" for the warning card) without hurting.

Whole clips (one plate held for 3 s):
- Still story shots: Qwen v2 keeps the seam at x1.0-1.4, and colour fit + live seam pull add nothing.
- Moving shots (action, pan, tilt): every plate fails (seam x9-35). Those need the video model.

Anima (the user's anime-only efficiency candidate):
- With black sides, its inpaint add-on drew dark sides with ghost copies of the picture.
- With kohya's own workflow (img2img, denoise 0.8) on blur-prefilled sides, it draws plausible anime structure (held-dialogue: tub edge and dark floor closer to the truth than VACE's second tub). But it darkens the sides heavily and invents large characters on edge-person-still.
- Reading: it lacks outpaint training, not anime knowledge. A fair anime-only test needs an outpaint adapter for it, for example trained on the show's own frames.

**Research decisions (reports A and B, 2026-10-12):**
- Lay out:
  - Qwen-Image-2.1 + ausboss outpaint LoRA, the best open editor, keeps the picture pixel-registered.
  - klein-base-4B: the fal LoRA's real base; we had run it on the distilled model.
  - Anima-Base + inpaint LLLite, anime-only (the user's efficiency question).
- Animate: Wan2.2-VACE-Fun-A14B Q8 first, then AnimeGen + VACE-Fun module. They are tensor-compatible, untested together.
- Runner: ComfyUI 0.39.0 pinned + diffusers.
- Layered background/character pilot: parked unless identity stays the weakest role. Free character sheets in the ending: shots 335, 337, 340.

## 8.5 Round 2: action episode, 14B animator, moving-shot tools (night of 2026-10-12, in progress)

**Action test material** [L]. The user's `Jujutsu.Kaisen.S03E12` (1080p HEVC 10-bit, 40,999 frames, 679 shots; opening
mid-episode at 600 s, credits from 1700 s, both labelled by the content model). How often the hidden side (outer 25% of
the picture) stays as it was at the shot's middle frame:

| Episode | shots where it holds | frames in such shots | steps that change it |
|---|---|---|---|
| Judas S01E02 (gag comedy) | 43% | 43% | 24% |
| JJK S03E12 (action) | 14% | 12% | 60% |

Reading: a still plate can serve only about one frame in eight of an action episode; video (or sides that move some
other way) carries the rest. Many long "shots" of the fights are runs of fast cuts the shot detector misses (smear
frames), so the 5 new scenes were picked by eye from each rule's top 5 and checked for hidden cuts:

| Scene | Shot | What moves at the sides |
|---|---|---|
| jjk-pan | 133 | camera tracks a walking man past scroll paintings on a curved wall (~310 px in 3 s) |
| jjk-edge-move | 523 | camera still; laughing close-up, fur collar and lightning at both edges |
| jjk-one-side | 503 | left side (sky, monster hair) holds; right side (his shoulder, arm) moves |
| jjk-action | 534 | fight, camera slides ~630 px; lightning; a second fighter on the right |
| jjk-still-edge-person | 59 | nothing: still control in this show's style (boy from behind, pole) |

**The painter on the action episode** (Qwen-Image-2.1 + outpaint LoRA v2, key frame only, 2026-10-13 09:47) [L, eye]:

| Scene | near | far | seam | By eye |
|---|---|---|---|---|
| jjk-still-edge-person | 3.1 | 17.3 | x1.35 | wall and tendrils continue; his arm reaches out, the pole he grips (all in the hidden side) is missing |
| jjk-pan | 4.9 | 20.9 | x1.08 | the curved gallery continues with more scrolls; the left one a little large and tilted |
| jjk-one-side | 8.7 | 24.8 | x1.15 | (sheet) |
| jjk-action | 13.7 | 41.8 | x0.96 | fur-cuffed fist, lightning, the second fighter's cuff: this show's look |
| jjk-edge-move | 18.1 | 52.6 | x1.01 | fur collar and lightning glow on both sides; the real ones sit elsewhere |

The painter keeps this show's style (heavier line work, effects, darker grade) as well as the comedy's. A plate is still
one picture: over the moving clips it cannot follow (whole-clip scores in the queue). Runner note: with `--cache-none`
each plate reloads both models; 5 plates took 17 min (3.4 min each) with 15 GB of RAM free (last night 1.2 GB).

**Real-use check, first verdict (user, by eye, 2026-10-13 ~10:45)**: the two-screen demo of the still-plate recipe on
an uncut fight moment (`out/demo/20261013-100301`, shot 534) "didn't maintain the continuity, it's one frame in the action
scene": the sides change 0.00 per frame while the picture changes 13.9 (fist, camera slide, lightning); a leftover of the
fist from the plate's frame sits at the left seam at frame 0. Settled: the still plate is for still shots only; moving
shots need sides that move. The same moment is queued with moving sides (A14B keyed by Qwen per side; scrolling plate).

**14B animator, keyed by the Qwen plate** (Wan2.2-VACE-Fun-A14B Q8, ComfyUI, 50 steps, 62 min per 3 s clip) [L]:

| Whole clip, near / far / seam | held-dialogue | edge-person-still |
|---|---|---|
| Qwen plate, fit + live | 8.8 / 12.1 / x1.0 | 15.2 / 24.4 / x1.62 |
| VACE-1.3B keyed + fit + live + freeze | 12.7 / 22.3 / x1.68 | 16.7 / 32.3 / x1.87 |
| **A14B keyed + fit + live + freeze** | **9.1 / 11.7 / x1.50** (boil -0.03) | 16.7 / 27.0 / x1.91 |

The 14B keeps the key plate's far content across the clip (1.3B drifts); its raw colours shift (held-dialogue near
15.7) and the per-clip fit removes that. By eye: held-dialogue is the same bathroom, shelves and bottles through the
seam, tiles at the right size (the free-standing tub on the left comes from the plate itself); edge-person-still keeps
the ponytail girl whole (hair, green collar, sleeve, skirt), adds grey crowd shapes behind her where the truth is plain
sky, and leaves out the green-haired girl and the sign, which lie entirely in the hidden right side (nothing visible
tells any model they are there). edge-head-closeup was not run: the queue stopped (below).

Run note: the overnight run stopped twice. Claude Code reaped the queue runner at ~00:20 for low memory (one prompt
held both experts + umT5, 54 GB committed on 32 GB RAM), and at 00:53 the client's video encoder (ffmpeg) died at start
under the same pressure after edge-person-still's 69 frames were generated; those frames were finished on the CPU from
ComfyUI's output folder. Fix in code, not yet run on the GPU: one expert per prompt (`run_two_phase`) and `--cache-none`.

**AnimeGen (anime fine-tune of the same 14B) with VACE-Fun's mask blocks grafted in** (2026-10-13) [L]. AnimeGen-T2V
takes text only; its 1095 backbone tensors match VACE-Fun's by name, shape and quant type, so `panorama-gguf-graft`
swaps them in at load time (log: "1095 tensors from T2V_high_noise_Q8_0.gguf, 236 kept"). Smoke test, both experts
grafted, held-dialogue, 17 frames, no key: **the sides come out almost blank white** (near 40.6, far 67.2, seam x2.63;
VACE-1.3B plain: 13.3 / 30.2 / x1.85); only a faint trace of the girl's shoulder continues at the seam. Reading: the
mask blocks were trained on the official backbone and do not steer AnimeGen's (2-6% weight drift), so nothing is drawn
in the masked part. **AnimeGen in the low-noise expert only** (layout from the official high-noise expert, `--animegen
low`), same smoke: real content (near 13.0, far 18.4, seam x2.62; fit 12.3 / 21.1 / x2.17). By eye: clean anime line art,
haze and flat colours kept; shelves continue with more bottles and jars, bath stools on the floor, a towel knot and a
window on the right: more invented clutter than the truth, and a visible seam line on the right. **Rejected and removed
(user, 2026-10-13 10:50)**: "AnimeGen is far away worse than Qwen" (Qwen plate on the same scene: 8.8 / 12.1 / x1.0, no
invented clutter). The keyed comparison run was stopped after 15 min; the model files (30.8 GB), the graft loader node and
the `--animegen` option were deleted. The numbers above stay as the record. An anime-tuned animator would need its mask
blocks trained for it (a training job), not a graft.

**Sides that move with the camera** (`scroll-plate.py`, no video model) [L]. The camera step per frame is the most common
optical-flow vector of the visible picture (phase correlation locked onto the walking man the camera follows); a median
mosaic of every frame's visible picture gives the shot's real background; the plate fills what no frame saw; the result
slides with the camera. Eyecatch tilt (1040 px vertical): seam x4.58 -> **x1.56**, near 56.5 -> 31.1 against the still
plate (fit + live), with the existing plate edge-stretched (the painter-completed version is queued). The credits
"pan" has no rigid motion (flow agreement 0.07): routing must send only confident slides here.

**Routing by motion** (`route-by-motion.py`): a side gets the plate when the picture's band next to it holds >= 90% of
steps, else the video fill. Story scenes: held-dialogue and edge-head-closeup route to the plate on both sides;
edge-person-still to plate left / keyed VACE-1.3B right (band holds 52%), which scores worse there than the plate alone
(far 29.4 vs 24.4). Moving scenes pending.

## 9 Unresolved questions

1. VACE-1.3B quality on these scenes (section 8), and whether 14B-class models fix ratio and composition (needs the rented GPU, decision 3).
2. Does feeding the image model more context (whole picture at full resolution, several frames, a scene caption) fix ratio and composition without fine-tuning?
3. Is "ratio" in the user's list about object proportions and scale (as read here), the canvas aspect, or both?
4. klein base (non-distilled, 50 steps) with the fal LoRA: better than distilled? Needs ~8 GB more disk.
5. Fair test of masked fill: pre-filled sides, or a pipeline variant that drops the reference copy.
6. Licences: Wan2.1-VACE-1.3B, FLUX.2-klein-4B and the fal LoRA read `license: apache-2.0` in their cached model cards [L] (the real-time report had VACE as unverified; settled). VACE-14B, Wan2.2-VACE-Fun, LTX and the NC models wait on the user (decisions 4-5).
