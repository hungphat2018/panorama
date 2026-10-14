---
title: "X2: painter speed with public levers (Qwen-Image-2.1 + outpaint LoRA v2)"
date: 2026-10-13
type: spike (GPU)
plan: plans/261013-0718-ai-surround-live-path/phase-01-decision-gates.md
---

## Decision

The public few-step + 8-bit recipe works with the outpaint LoRA:
- **14 s per plate at ~1 MP**, against 170-200 s today (about 12x).
- Hidden-sides scores the same as the 25-step plate.
- The two-speed target for the big painter (3.5-6 s, X5 addendum) still needs ~2.5-4x more. Candidates: a smaller image for the VLM, 4 steps (local distillation), sides-only targets (local LoRA retraining).
- Text-only conditioning breaks the outpainting, so the VLM must keep reading the canvas.

## Setup

- **Software:** ComfyUI 0.39, dynamic VRAM mode (`--enable-dynamic-vram`), `--cache-classic`.
- **Models:**
  - official Comfy-Org INT8-ConvRot files: DiT `qwen_image_2.1_int8_convrot` (7.26 GB), encoder `qwen3vl_8b_int8_convrot` (9.35 GB)
  - Turbo LoRA (Comfy-Org extraction, rank 178) at 1.0, plus the ausboss outpaint LoRA v2 at 1.0
- **Sampling:** 8 ManualSigmas (the Turbo card's list), euler, CFG 1, fixed prompt (no caption pass), seed 7.
- **Scenes and rests:** the 5 JJK key frames; 180 s rest before each variant, 20 s between plates.
- **Script:** `spikes/ai-surround/painter-speed.py`, with new `--dit-file` / `--te-file` options. Log `out/runs/painter-speed/x2-main-int8dyn.log`. Sheets `out/runs/sheets/<scene>-painter-speed-s0.5.png`.

## Results (5 JJK scenes)

| Variant | Warm s/plate | s/step | Near / far / seam | 25-step plate (bf16 + caption, ~181 s) |
|---|---|---|---|---|
| Turbo 8, ~2 MP | 34.1 | 1.88 | 9.8 / 28.8 / x1.20 | 9.7 / 31.5 / x1.11 |
| **Turbo 8, ~1 MP** | **14.0** | 0.62 | **9.8 / 29.5 / x1.02** | same |
| Turbo 8, text-only, ~2 MP | 22.0 | 1.87 | 72.0 / 69.9 / x33.6 (broken) | |
| Turbo 8, text-only, ~1 MP | 7.7 | 0.78 | 72.0 / 70.0 / x31.4 (broken) | |

Other facts:
- VRAM peaked at 11.5-12.0 GB with nothing else on the GPU. LoRA warnings were 0.
- RAM in dynamic mode: 15-20 GB free, commit headroom 18-24 GB.
- In text-only mode the sides come back as the grey canvas: the painter does not see the outpaint task through the reference latent alone.
- Per plate at ~1 MP: the sampler takes ~5 s (8 x 0.62). Text-only shows ~1.5 s of overhead without the VLM image read, so the VLM reading the canvas costs about 7 s of the 14.
- By eye (agent, jjk-one-side sheet): the ~1 MP plate continues the sky, the monster and the arm like the 25-step plate. Every version keeps the plate's identity guess (the blood stain turned into a red scarf).
- The user has not judged it yet.

**Memory findings** (load-bearing for the live worker):
- Legacy resident mode (`--disable-dynamic-vram`) keeps full RAM copies of the DiT and the encoder. It tripped the 4 GB commit guard three times: with bf16 files, with fp8-cast encoder and with INT8 files. At its first plate it reserves ~30 GB of commit in 0.2 s.
- It was also slower: 3.33 s per step at 2 MP with INT8 files, against 1.88 in dynamic mode. The DiT and encoder swap in 12 GB on every plate.
- **Dynamic VRAM mode was both safe and faster here,** the opposite of the prep's expectation from issue #16551.

## Not run

V1 (25 steps fast), the Turbo LoRA at 0.7 and the full Turbo checkpoint. V3 (PAI 4-step) does not load natively in ComfyUI 0.39 (keys lack `.weight`; it has a per-step head bank). The comedy scenes were not run.

## Next levers for the big painter (phase 3), from 14 s toward 3.5-6 s

| Lever | Plate time |
|---|---|
| Smaller image for the VLM: the TextEncodeQwenImage21 `resolution` input, or the W4A8 encoder | maybe 14 → ~8-10 s |
| 4 steps: local OPD on the merged LoRA (DiffSynth docs: 50 → 8 → 4) | sampler 5 → ~2.5 s |
| Sides-only targets: outpaint LoRA retrained locally on hidden-sides pairs | about half the sampler tokens |
| NVFP4: last, unverified on sm_120 and Windows | |

## Unresolved

1. The user's by-eye verdict on the ~1 MP Turbo plates against the 25-step plates (5 sheets).
2. How small can the VLM's input image get before the plate loses the look?
3. Do the ~1 MP plates hold up on the comedy scenes and in full-screen real use, compared with the 2 MP plates?
