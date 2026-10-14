---
title: "AI surround live path: AI sides at film speed on this laptop"
description: "Build the high preset (today's best sides, no visible loss) at 1x film speed on the RTX 5070 Ti Laptop, two screens, play at once and the sides catch up; a side model redraws every drawing on top of the structure path, small local training only."
status: in-progress
priority: P1
effort: "~3 weeks agent time + user by-eye sessions + overnight local training"
branch: main
tags: [ai-surround, real-time, architecture, performer]
blockedBy: []
blocks: []
created: 2026-10-13
---

# AI surround live path

## Overview

Verdict (2026-10-13): adopt the compositor from the [synthesis](../reports/research-261013-1049-ai-surround-real-time-architecture-synthesis.md). Most side frames are rendered from structure: plates, the camera's motion, real pixels. Generation runs only on zones next to the seam that the picture changed. A14B and the 25-step Qwen stay offline as oracle and teacher. Five amendments:

1. **The painter is the budget risk, not the refiner.** It paints only the sides, and one plate serves a whole set ("set atlas").
2. **New gate E1:** the player must stay on time while the AI runs on the same GPU.
3. **The X1 true-sides harness judges every role swap.**
4. **The refiner comes from small, proven-fast models trained on the user's true sides.**
5. **No carried side is shown raw** (after X1c; the user, 2026-10-14). A side model redraws each whole side at every new drawing, and the structure path becomes its input. Carrying stays only as a speed shortcut, where the per-frame gate proves it invisible (phases 4-5).

Inputs: [handoff](../handoffs/ai-surround-film-speed-architecture-20261013-1335.md), [kongming counsel](../reports/kongming-261013-1049-real-time-architecture-counsel.md), reports `researcher-261013-1049-*`, [GPU sharing](../reports/researcher-261012-1407-gpu-sharing-and-inference-runtime-on-windows.md).

## Acceptance criteria

1. **Play at once.** JJK S03E12 (action) and Judas S01E02 (comedy) start on press. The sides start at the lowest ready tier and rise as the worker gets ahead. The player never waits for the worker.
2. **1x.** After a catch-up of at most 2 min (refined by X5), ≥ 95% of film time shows the top tier on both episodes, within the worker's GPU share.
3. **On time.** Zero film frames are shown for the wrong number of refreshes because of the worker, over both full episodes (player logs).
4. **No visible loss (user, forced choice on the two screens, uncut moments).** The live high preset is compared with today's best offline recipe: Qwen plate on still shots; Qwen plate + A14B keyed + wraps on moving shots. Pass: today's best is picked at most 12 times out of 20 per scene type (still, camera slide, action, effects). The hidden-sides score against the truth is reported as the margin.
   - **Amended by the user (2026-10-14 12:10):** "the image quality of generated doesn't have to be exactly the same as the original, as good as it make senses, and right context, less artifact possible is fine".
   - The pass is now: the sides make sense, have the right scene context, and show as few artifacts as possible. A visible seam still fails (criterion 5 and the gate).
   - The forced choice against today's best stays as a comparison, not the pass. The agent judges first (artifact audit, worst moments first); the user sees finals by name, with at most 12 blind trials.
5. **Side rules.** A side pixel changes only with a picture change (new drawing, camera step, cut). Tier upgrades happen only at cuts. No boil.
   - User's verdict on A14B keyed moving sides (2026-10-13 15:35): "the quality is insane, but it still flickering, and I can still see the seamline".
   - So the bar is A14B's look **without** its flicker and seam line. By eye on both screens: no flicker in the sides, and the seam line not visible.
   - **Per frame** (the user, 2026-10-14): every shown frame of every tested moment passes the per-frame gate at the user's ladder thresholds (seam break, drift and snap, detail step, boil). Averages are not verdicts.
6. **Local only.** No cloud and no rented GPU; small training runs on the laptop; the film never leaves the machine.
7. **Swappable roles.** Analyse, paint, redraw (the side model) and render sit behind written contracts; swapping one role's tool touches no other role.
8. **Resources.** The worker stays within ~8 GB VRAM next to the player, with no pagefile growth. A rest follows every heavy run (user rule).

## Non-goals

Setup B, 3 screens, 48:9; low/medium presets; cloud distillation; licence and release decisions; macOS; M2 demo polish; audio-driven side cues; per-shot test-time training.

## Where things stand (2026-10-13, night)

- **Done:** the architecture verdict and this plan.
- **Gates done:**
  - X5 ([report](../reports/spike-261013-1430-x5-set-reuse-and-live-schedule.md)): set reuse 1.10, so two-speed painting; big-painter target ~3.5-6 s per plate.
  - X2 ([report](../reports/spike-261013-1855-x2-painter-speed.md)): Turbo 8-step INT8 at ~1 MP takes 14 s per plate at the 25-step look; text-only fails.
  - E1 ([report](../reports/spike-261013-1950-e1-player-under-ai-load.md)): short kernels are fine; the real painter causes ~20 wrong frames a minute, ~40 with its VRAM capped (phase 2 re-test: likely weight streaming). The player's own misses fall mostly on a 2-second system stall, not clock changes (phase 2).
- Slot runbook and the gate scripts: [runbook](../reports/spike-261013-1530-speed-gates-slot-runbook.md).
- **Open in phase 1:**
  - X1, the user's 8-min blind test (`spikes/ai-surround/out/demo/20261013-133033-true-sides/play-trials.bat`), with an idle GPU. Score it with `true-sides-test.py --score`.
  - The user's by-eye verdict on X2's sheets (`spikes/ai-surround/out/runs/sheets/<scene>-painter-speed-s0.5.png`).
- **User verdicts recorded:** A14B keyed is "insane" quality but flickers and shows a seam line (criterion 5). A14B and the plate invent identity on cut-off characters (blood stain turned scarf, short hair turned ponytail), so identity references are required (phases 3-5).
- **Phase 2 done (2026-10-13 night):** the worker, the side store and the player's AI fill run end to end on both screens ([report](../reports/implementation-261013-2012-live-skeleton-phase2.md)).
  - Full screen for 10 min, with 3 seeks, a re-watched minute and the worker killed and restarted: no wrong frame comes from the worker or the AI sides.
  - E1's painter cell with the GPU rules got worse (40 wrong frames a minute): ComfyUI's painter cannot share the GPU with the player, whatever its VRAM cap. Phase 3's painter must fit its VRAM budget without streaming weights.
  - The player's own misses fall on a 2-second system stall, with or without AI. Finding its source is M1's.
  - [ADR 0028](../../docs/adr/0028-ai-sides-worker-feeds-the-player-through-a-side-store.md) waits for the user's OK on what the viewer sees.
- **Next:** phase 1's open items (X1, the X2 sheets), then phases 3 and 4.

## Where things stand (2026-10-14, early morning)

- **Phase 3 built and measured** ([report](../reports/implementation-261014-0245-painter-at-speed-phase3.md)):
  - The resident painter takes **5.2 s a plate**: W4A8 merged Turbo + outpaint v2, 5 steps, INT8 attention, no encoder in its process. Its hidden-sides scores match the 25-step plate.
  - E1 is at the no-load level: 2.0 / 1.0 wrong / missed per min at 240 Hz (no load 1.0 / 2.6), 0 / 0 at 96 Hz.
  - With the player running, the painter keeps 0.75-0.79 of its speed (u).
- **Identity references** help (4 seeds: far error -7 to -9 %, short hair kept).
  - The live path is built and verified headless: a CPU encoder reads each crop once (~24 s) and reads the frequent ones ahead, behind `--identity-refs`. 7 of 13 plates got a crop.
  - It stays off by default: two of phase 4's top clusters mix characters, so a whitelist of clean clusters is the safe start.
- **4 steps** (4.4 s a plate) keeps the scores too, and would meet the no-reuse target. It waits for its own swap test.
- **Set atlas.** Sharing plates showed other shots' characters, flashes and fades in the sides. A strict join check now leaves about 1 % of shots sharing, so budget the painter for no reuse: about 4.7 s a plate at T = 3 s.
- **Phase 4 built** ([report](../reports/implementation-261014-0240-phase4-structure-path.md)):
  - What exists: the projective camera model, the step planner with the cadence as a parameter, exit strips, patch pages (side-store schema 2) and the player's T3 compositing.
  - T3 showed 364 of 600 film s on the screens, with 2901 patches applied.
  - Mosaics leave out regions whose frames disagree. JJK's identity clusters are made (identity stays off by default).
  - Open:
    - **Wrong frames at 240 Hz:** 6.0/min against phase 2's 1.5 (3.1 with the patch path off). Needs a screen session.
    - **ADR 0028's schema-2 amendment** waits for the user's OK.
- **User checks waiting:**
  - The swap test, 5-step live plate against the 25-step plate: `spikes/ai-surround/out/demo/20261014-044008-swap-live-w4a8-ckint8-turbo5/play-trials.bat`. Then the 4-step test: `.../20261014-045845-swap-live-w4a8-ckint8-turbo4/play-trials.bat`.
  - The 0.74 MP canvas, by eye.
  - Phase 4's X1c, ~9 min: `spikes/ai-surround/out/demo/20261014-043348-true-sides-c/play-trials.bat`. It picks the cadence or confirms the replan of phases 4-5.

## Where things stand (2026-10-14, morning)

- **Both swap tests fail (user, 09:16-09:26).** The 25-step plate was picked 14 of 20 times against the 5-step live plate, and 16 of 20 against 4 steps (pass: at most 12; shams 1 of 2 each). The user's cues: what was painted, and the join line.
- **The cause is the ~1 MP canvas, not the step count** (details in [phase 3](./phase-03-painter-at-speed.md#progress-2026-10-14-morning)):
  - The live sides hold about half the fine detail of the film's and the 25-step plate's, at 4, 5 and 6 steps alike.
  - At 1 MP the painter also paints simpler things: broken "!" signs, a robe without its fold lines, a hazy creature. Next to the sharp picture, this reads as a join line.
  - The hidden-sides scores did not see it.
- **Full-size check:** today's live recipe on the 25-step plate's own canvas (~2 MP) is close to the 25-step plate by measure and by the agent's eye. It takes 11.0 s a plate at 5 steps; 4 steps is glitchier there.
- **Its swap test fails too (user, 10:23): 17 of 20** (8/10 with the 25-step plate first, 9/10 second). The canvas size is not what the user sees: it is what is painted.
  - The live painter's guesses are murkier wherever the scene leaves room to guess (dark: a smudge for the girl's head, where the 25-step plate draws her).
  - **Suspects:** (1) the 25-step plate's prompt describes the scene (a caption of the visible picture: place, people, clothes, style, colours, light), read with the real canvas; the live painter has one generic prompt read with a grey canvas. (2) 5 Turbo steps against 25 full ones.
  - Neither the agent's eye nor the hidden-sides scores predicted any of the three swap tests: the user's eye is the only judge.
- **Content diagnosis**, judged by the agent at the user's request; the user's impression: "pretty close, the other with a little more artifacts". Details in phase 3.
  - **The scene caption closes most of the gap:** 7 of 13 to the 25-step plate (decisive trials 6 of 8). Action gets the film's frame and purple background.
  - What remains are small few-step artifacts. 8 steps without the caption is further off (9 of 13).
  - **Caption + 8 steps** removes most of them (better in 3 of 6 problem scenes, worse in 1). It takes 15.6 s a plate. It is the best fast candidate; its acceptance test is `spikes/ai-surround/out/demo/20261014-113141-swap-live-2mp-caption-turbo8/play-trials.bat`.
  - Making captions live is new work: one per scene, from the 8B VLM.
- **Painting only the sides without training is rejected.** 7.1-7.4 s a plate, but garbled sides: copies of the picture's text, invented long hair, and seam lines without a band. It needs the trained sides-only LoRA (phase 3, step 4).
- **The time budget is the open question.** The look the user accepts costs 11-16 s a plate (full size, 5-8 steps), against the 4.7 s target at T = 3 s.
- **At ~1 MP the caption still fixes the content in 5.22 s** (5 steps), but the sides stay soft (detail 1.8, join ratio 0.53).
- **Next (user's choice, 12:10):** a fast anime upscaler on the 1 MP + caption plates, judged by the agent under the relaxed bar (criterion 4 amendment). It is continued by another session from `plans/handoffs/ai-surround-painter-upscaler-1mp-caption-20261014-1212.md`.
- Dropped: the 0.74 MP canvas (softer still) and the 6-step swap test (the same softness).
- **Identity references: the timing check fails** (`out/e1/20261014-103611`).
  - The worker uses only the clusters in `identity/allowed.json`. JJK's list: c2, c16, c28 (c1 and c8 look mixed).
  - With the painter and the CPU encoder on every core: 156 wrong / 137 missed per min at 240 Hz (no load at 10:31: 0 / 0.5; 96 Hz stays at 0.5 / 0). References stay off.
  - The encoder now runs at idle priority, with 8 threads on the upper half of the logical CPUs (user's choice). Re-measured (`out/e1/20261014-111439`): 7.15 / 8.68 per min at 240 Hz. That is 20× better, still above no load. References stay off; the remaining options are reading only while paused or stopped, or ahead of playback.
- X1c: the user stopped after 8 of 48 trials and judged the free-look clips instead; no rerun. See the next section.

## Where things stand (2026-10-14, late morning): X1c verdict, phases 4-5 replanned

- **X1c judged by eye** (the user, after 8 blind trials, on the free-look clips):
  - a seam break on the pan, and small jumps on its right side;
  - a torn, then jumping side on slow-side;
  - near-every's tear inside the side;
  - edge-move's three identical files.
- **Per-frame check** ([phase 4](./phase-04-structure-path.md#x1c-verdict-and-replan-2026-10-14)):
  - At the pan's seam, one carried frame is ~4 codes off; three in a row reach 11.4.
  - On slow-side, the camera slid the side ~51 px off, while a frozen side stays within 20 px.
  - No carried version passes per frame, so the cadence question is closed: no cadence works.
- **The user's decisions:**
  1. Redraw every drawing (amendment 5).
  2. The pass bar is "visible at normal speed", measured once with a ~5-min ladder. Every test is gated frame by frame on it.
  3. Tests reach the user only after the gate, labelled next to the truth with the worst moments first. A blind test only confirms a final candidate, in at most 12 trials.
- **Replanned:**
  - [Phase 4](./phase-04-structure-path.md) now builds the per-frame gate, the ladder, a seam guard for T1/T2 and the side model's inputs contract. It retires T3 as a display tier and fixes the wrong frames at 240 Hz.
  - [Phase 5](./phase-05-zone-refiner.md) is the side model (T4): a bake-off of a learned carry (D), a wing specialist (B) and SANA-Streaming (A), trained locally on the true sides.
- **Process rules (the user):** no averages as verdicts; a bar that proves wrong is replaced, not dropped; no candidate that can't pass by construction; no test reaches the user unchecked.
- **ADR 0028's schema-2 amendment** is on hold: phase 5's transport (schema 3) changes it again.
- **Next:** phase 4's gate and ladder (CPU only; the ladder needs ~5 min of the user's time), then phase 5's step 1.

## Where things stand (2026-10-14, midday): the gate is the judge

- **The per-frame gate is built and judges every test** ([report](../reports/implementation-261014-1200-phase4-gate-ladder-x1c-rescore.md)). It reproduces X1c's per-frame numbers exactly.
- **The ladder became 5 trials.** The user asked the agent to judge the quality itself (11:35): the clips looked alike apart from "flickering on left" and "the very clearly seamline", which were the ladder's events.
  - Evidence kept: breaks seen at 5.7 px and missed at 2.8 px; drifts seen at 11.3 px.
- **Bars:**
  - **Carried truth:** at the measurement floor, far stricter than the user's eyes. A bar is relaxed only with evidence.
  - **Generated sides:** half the sizes the user saw.
  - **Detail:** from the swap tests. 1 MP softness, 0.85 of today's best, was seen; the bar is 0.90 on a 1 s median.
  - **Sharpness was not the 2 MP plate's cue:** it matched today's best (0.99), as phase 3's content diagnosis also found.
- **X1c re-scored per frame:** only the truth and truth-pipe pass. Every carried version fails, except edge-move's byte-identical copies.
- **Limits of judging without the truth:** on single frames it can't tell a break from real action (61 px of seam motion on real content). The seam guard needs another approach.
- **Phase 5 has started in another session.** It judges its side models with `side-gate.py --generated`. At its request, the gate gained a two-sided jump and motion and drift against the truth.
- **Next (phase 4):**
  - compile the player with T3 off;
  - the seam guard, the analyser's lead cap, then the 240 Hz screen session.

## Phases

| # | Phase | Status |
|---|-------|--------|
| 1 | [Decision gates: X1, X5, X2, E1](./phase-01-decision-gates.md) | Pending |
| 2 | [Live skeleton: worker, side store, player hook, tiers T0-T2](./phase-02-live-skeleton.md) | Done |
| 3 | [Painter at speed: two-speed painting, compression](./phase-03-painter-at-speed.md) | Built; the swap test (user) and the two-speed choice (needs phase 5) remain |
| 4 | [Structure path, then the per-frame bar and the side model's inputs](./phase-04-structure-path.md) | Gate, bars, X1c re-score and inputs contract done (2026-10-14 midday); T3 off in source; seam guard, lead cap, compile and the 240 Hz screen session remain |
| 5 | [Side model: redraw every drawing (T4)](./phase-05-zone-refiner.md) | Replanned 2026-10-14; starts after phase 4's gate and ladder |
| 6 | [Full-episode check and verdict](./phase-06-full-episode-check.md) | Pending |

## Dependencies

- Phase 1 sets the parameters for the rest:
  - E1 decides the GPU-sharing rules (phase 2).
  - X5 + X2 decide the painter's target and recipe (phase 3).
  - X1 and X1c (done): no cadence works for shown carried sides, so the side model redraws every drawing (phases 4-5).
- Phases 3 and 4 can run in parallel after phase 2. Phase 5 needs phase 4's gate (with the user's thresholds) and its inputs contract.
- Phase 3's small painter is phase 5's model in paint mode. Until it exists, short shots use the lower tiers.
- X5 (done): set reuse is only 1.10 on action, so one painter would need ~1.3-1.8 s per plate. With two-speed painting (the big painter only for plates on screen ≥ ~3 s) its target is ~3.5-6 s ([X5 report + addendum](../reports/spike-261013-1430-x5-set-reuse-and-live-schedule.md)).
- The player (`spikes/performer-p1`) belongs to the M1 track, which has uncommitted audio work there. AI changes are additive: new files plus small hooks.
- The GPU runs one heavy job at a time. The other session's queue pauses after its step 17 for the phase-1 GPU gates (user, 14:10).

## Unresolved questions

1. If 240 Hz cannot coexist with the worker even with mitigations, does the user accept the laptop panel at 120 Hz while AI sides are on? Ask only if E1 fails; it changes what the user sees.
2. Licence endgame for Qwen-derived parts (research licence). The user's call, later.
3. **The final blind test's size.** Criterion 4 and phase 6 use 20 blind trials per scene type, 80 in all. The user's answer on 2026-10-14 says a blind test only confirms a final candidate, in at most 12 trials, after a labelled review. Options:
   - one 12-trial confirmation (pass: today's best picked at most 7 of 12);
   - keep 20 per scene type for the final verdict.

   The user's call.
4. **Glow in place of a T1/T2 shot that breaks at the seam** (phase 4's seam guard). This changes what the viewer sees during catch-up, so it needs the user's OK (ADR 0017), together with the share of film time it moves to glow.
