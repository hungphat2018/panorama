---
phase: 1
title: "Decision gates: X1, X5, X2, E1"
status: pending
priority: P1
effort: "0.5-1 d agent + user 8 min + GPU 2-3 h (with rests)"
dependencies: []
---

# Phase 1: Decision gates

## Overview

Four cheap tests. Each one changes a decision the build depends on, and each outcome has a pre-decided response. Nothing here proves something already expected; no other spikes before building (user rule: architecture first, test once at the end).

## Requirements

- Functional: each gate produces numbers plus a recorded decision. X1 also gets the user's forced-choice answers.
- Non-functional:
  - The GPU gates run in the queue's pause (after its step 17, ~18:00), one heavy job at a time.
  - A rest follows each run and each test cell (user rule), and GPU temperature is logged at each start.
  - Film content stays local.

## Gates

| Gate | What | Needs | Decides |
|---|---|---|---|
| X1 true-sides blind test | 44 trials, 4 JJK moments. Versions: truth, frozen, camera-carried with a refresh at 1/3/6 Hz, and the planner (perfect content only where the planner flags) | user, 8 min, `spikes/ai-surround/out/demo/20261013-133033-true-sides/play-trials.bat` | refresh cadence; whether structure carries the sides; whether the pan drift is visible |
| X5 set reuse + schedule | Sets found by registering shots against each other; real-pixel coverage from other shots; a live schedule simulation ("play at once") | CPU (helper running) | plates per film-second, hence the painter's s/plate target |
| X2 painter speed | Qwen-Image-2.1 + outpaint LoRA v2, five variants (V1-V5 below) | GPU ~1.5 h | the painter recipe; whether our own compression is needed |
| E1 player under load | perf1 full-screen on both monitors while a load runs, as a matrix (cells below) | GPU + screens, unattended (user OK) | the GPU-sharing rules for the live path |

X2 variants:
- V1: resident, FP8, no caption pass
- V2: + Turbo LoRA, 8 steps
- V3: + Fun-Acc 4-step LoRA
- V4: the best of V2/V3 at a ~1 MP canvas
- V5: text-only conditioning

E1 cells: load {none, GEMM 1/2/4/8 ms, attention 2/8 ms, painter} × frame latency {1, 3} × priority {default, high}.

## Results so far

- **X5 done (2026-10-13 ~15:00):** [report](../reports/spike-261013-1430-x5-set-reuse-and-live-schedule.md).
  - Set reuse on JJK is 1.10 (at most 1.40), not 1.5.
  - Real pixels cover ~11% of the side area.
  - Need: 0.33-0.36 plates per film-second; GEN 4.72/s (the earlier figure reproduced).
  - Painter s/plate for ≥ 95% top tier with no head start: 1.7-1.9 s (u = 0.6) or 2.1-2.3 s (u = 0.75) with plates alone; 0.7-1.8 s with a 30-60 ms refiner.
  - Public 3-9 s gives 28-80% top tier. The episode has no opening sequence (the content model's "op" region is story); the ED starts at 1617 s.
  - **Response applied:** the target is below any public recipe, so phase 3 becomes two-speed painting plus compression of the big painter. The refiner target tightens to ≤ 30 ms per GEN drawing, and phase 2 adds levers that raise u.

- **X2 done (2026-10-13 ~18:53):** [report](../reports/spike-261013-1855-x2-painter-speed.md).
  - Turbo 8-step LoRA + outpaint LoRA v2 on the official INT8 DiT and encoder, ComfyUI dynamic VRAM, ~1 MP canvas: **14 s per plate**, against 170-200 s.
  - Hidden-sides scores equal the 25-step plate's (9.8 / 29.5 / x1.02 against 9.7 / 31.5 / x1.11).
  - Text-only conditioning is broken (grey sides), so the VLM stays: it costs ~7 s of the 14.
  - Legacy resident mode trips the RAM guard; dynamic mode is safe and faster.
  - **Response applied:** the outpaint LoRA survives Turbo, so phase 3 starts from this recipe. The remaining ~2.5-4x to the two-speed target comes from a smaller image for the VLM, 4 steps (local OPD) and sides-only targets.
  - The user's by-eye verdict on the 5 sheets is pending.

- **E1 done (2026-10-13 ~19:51):** [report](../reports/spike-261013-1950-e1-player-under-ai-load.md).
  - Short GPU kernels (2-12 ms) are harmless at latency 1 (1-3 wrong frames a minute).
  - The real painter gives ~23 wrong frames a minute at 240 Hz (18 at 96 Hz), not tied to clock events. Likely cause: VRAM pressure (~11.5 GB held) and weight streaming.
  - The player alone has ~23 wrong frames a minute at 240 Hz, tied to GPU clock changes. Phase 2 found these fall mostly on a 2-second system stall instead ([report](../reports/implementation-261013-2012-live-skeleton-phase2.md)).
  - Latency 3 is unusable until the engine plans for queue depth.
  - **Response applied (phase 2 rules):**
    - the player locks clocks (prefer maximum performance; M1 track)
    - the painter's VRAM is capped and encoder reads are batched
    - the player reserves its VRAM; no kernel chunking needed
    - re-test the painter cell before the phase 2 smoke. Done 2026-10-13 ~22:20: with the cap it got worse (~40 wrong frames a minute at both rates, likely weight streaming), so phase 3's painter must fit its VRAM budget without streaming
- **X1 taken (the user, 2026-10-13 21:58), scored 2026-10-14 00:05:** every carried version was spotted every time (planner 10/10, warp 6 / 3 / 1 Hz 8/8 each, frozen 6/6; sham picked "1" in 2 of 4, so no answer bias). Even 6 Hz on the calmest moment, 2-3 codes from the truth, was spotted (per moment 9-11 of 10-12).
  - **A possible confound, probably small.** A carried frame showed sides resampled through the world canvas, a refresh frame the raw truth, and no control showed whether resampling alone is visible.
  - **X1b built** (`true-sides-test.py --same-resampling`, `out/demo/20261014-001741-true-sides-b/`): every version's sides are read back from the world every frame and compared with truth-pipe (the truth through the same world), plus a "pipe" level (raw truth against truth-pipe).
  - Its numbers make the confound unlikely: truth-pipe is 0.2-0.5 codes from the truth, while carried sides change less per frame than the truth's (e.g. 6 Hz 2.61 and planner 2.07 against 2.90 on "reaching") and jump at each refresh. So X1's result probably stands; X1b confirms it with the control before the pre-decided response (the planner version visible: stop and replan phases 4-5).
- Still open: the X1b test (the user, 8 min, idle GPU), plus the user's by-eye verdict on X2's sheets (superseded by phase 3's swap test of the live recipe).

## Pre-decided responses

- **X1**
  - Planner version not reliably visible (≤ 12/20) and 6 Hz + warp visible: refresh at the drawing rate on flagged drawings (as designed).
  - Some fixed rate (3 or 6 Hz + warp) not reliably visible on all moments: cap the refiner's cadence at that rate. This is a large budget saving.
  - Planner version visible: the compositor premise fails at this cadence. Stop and replan (for example, per-drawing generation for the near band).
  - Pan drift visible: phase 4 adds a travel-based re-key or a richer camera model.
  - Check first: the frozen control must come out visible, and the sham trials show answer bias.
- **X5**
  - Target ≥ X2's best s/plate: phase 3 uses X2's recipe and needs no compression of its own.
  - Target below it: phase 3 adds sides-only LoRA retraining and local 4-step distillation.
  - Still below: give very short action shots a lower tier (by-eye check), or replan.
- **X2**
  - The outpaint LoRA survives Turbo/Acc: use it.
  - It doesn't: phase 3 self-distils the merged painter on the laptop.
  - V5 holds the look: the 8B text/vision encoder leaves the live path.
- **E1**
  - 0 wrong-refresh frames with the painter load at some setting: those settings become the rules (frame latency N, priority, a cap on the worker's kernel length).
  - Fails at every setting: ask the user about 120 Hz on the laptop panel while AI sides are on (plan question 1). Further out, a D3D12/Vulkan presenter with CUDA-in-graphics (big, later).

## Related code files

- Exists: `spikes/ai-surround/true-sides-test.py` (X1 build and `--score`).
- Being created by the helpers (2026-10-13):
  - `spikes/ai-surround/set-reuse-and-schedule.py` (X5)
  - `spikes/ai-surround/painter-speed.py` (X2)
  - `spikes/ai-surround/gpu-load.py`, `e1-player-under-load.py`, `e1-analyse.py` (E1)
  - perf1 flags `--frame-latency`, `--gpu-priority` (new file plus small hooks)
- Queue: `spikes/ai-surround/out/runs/queue/queue.txt`. Line 22 waits for `go-after-speed-gates`; touch that file after the gates to resume.

## Implementation steps

1. The user takes X1 when free. Score it: `tools/venv-outpaint/Scripts/python.exe -I -X utf8 spikes/ai-surround/true-sides-test.py --score spikes/ai-surround/out/demo/20261013-133033-true-sides "<answers>"`. Do not open `key.json` before the answers arrive.
2. X5 finishes on the CPU. Review its contact sheets (accepted and rejected registrations) before trusting the reuse factor.
3. When the queue reaches line 22 (after step 17 + rests):
   - Run X2 (smoke variant first, then V1-V5, with rests).
   - Run E1: a 20-s smoke cell first, then the matrix, with rests between cells and the painter load taken from X2's best recipe.
4. Touch `go-after-speed-gates`, so the queue resumes its demos.
5. Write `plans/reports/spike-261013-<hhmm>-ai-surround-speed-gates.md`: numbers, decisions, and the responses applied. Add a short section to the synthesis.
6. Set the parameters in phases 2-5 (cadence, painter target and recipe, GPU-sharing rules).

## Success criteria

- [ ] The X1 verdict is recorded, with controls checked (frozen visible, sham not biased).
- [x] X5: the reuse factor, the coverage distribution, and the painter s/plate needed for ≥ 95% top tier at u = 0.6 and 0.75 (report linked above).
- [x] X2: warm s/plate, VRAM, and zero "lora key not loaded" warnings per variant (report linked above).
- [ ] X2: the user's by-eye pick against the 25-step plate.
- [x] E1: wrong-refresh frames per minute per cell, p99.9 present error, clock events; the chosen settings (report linked above).
- [ ] The responses above are applied to phases 2-5.

## Risk assessment

- **X1 has a single viewer and one session.** If the result is borderline (13-14/20), run 20 more trials of that level on another day.
- **E1's thermal state differs between cells.** Rests, plus a temperature check at each cell start.
- **The queue overruns step 14's estimate.** The gates move with the pause; nothing else waits on them except phases 2-5.
- **X5's registration accepts false matches.** Mitigated by the contact-sheet review. The coverage number is an upper bound because characters count as covered.
