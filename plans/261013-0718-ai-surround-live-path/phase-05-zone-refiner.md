---
phase: 5
title: "Side model: redraw every drawing (T4)"
status: pending
priority: P1
effort: "6-10 d agent + 3-6 overnight local trainings with rests + the user's by-eye sessions"
dependencies: [3, 4]
---

# Phase 5: Side model, redraw every drawing (T4)

## Why this phase changed (2026-10-14)

Before X1c, this phase was a zone refiner: it drew only the zones the step planner flagged, and the camera carried the sides in between. X1c showed that carried sides break within 1-3 frames on real camera moves and can drift worse than a frozen side. Each redraw then pops by the whole error ([phase 4's verdict](./phase-04-structure-path.md#x1c-verdict-and-replan-2026-10-14)).

The user chose (2026-10-14):
- On every new drawing, a model re-makes each whole side. It starts from the carried side and the picture, and is trained on the true sides.
- No raw carried frame is shown, except as a speed shortcut where the gate proves it invisible.

The 10-13 research had already priced this drawing-rate path as its fallback ("B: wing specialist", [student architecture report](../reports/researcher-261013-1049-specialist-student-architecture-and-compute.md)).

Two findings shape the model:
- **Detail must match at the seam.** Phase 3's swap tests (2026-10-14) showed that sides with about half the film's fine detail "read as a join line". Next to the seam, the output keeps the picture's detail. A coarser scale is allowed only further out, and only where the gate's detail step passes.
- **The film is known ahead.** An update may read the shot's coming drawings, not only the past ones (chunk-bidirectional inside a shot).

## Overview

One update is one new drawing, with both sides batched. JJK has 15.13 new drawings a second (measured over 591 s of action); comedy has fewer. The side model takes [phase 4's inputs contract](./phase-04-structure-path.md#requirements) and outputs both whole sides for that drawing. The player shows them as they are until the next drawing.

Phase 3's painter keeps painting plates. A plate is the world's long-term memory and the start of each shot. Upgrades still happen only at cuts.

## Requirements

Functional:
- **Input:** phase 4's inputs contract.
- **Output:** both sides of the drawing in RGB, colour-matched. Near the seam they keep the picture's detail; further out, the scale the bake-off proves invisible.
- **Shot start:** the first drawing starts from the plate (T2) or the mosaic (T1). When neither is ready, "paint mode" runs: the same weights, more work. This is phase 3's small painter for short shots.
- **First appearance:** characters and effects that cross from the picture into a side. Trail entries (things walking in from outside) stay off until the user's by-eye verdict.
- **T4 per shot:** a shot shows T4 only when its updates are in the store for its whole length when its showing starts. There is no fallback mid-shot; a shot that isn't ready keeps its lower tier.
- **Speed shortcut (only after the model passes):** on a drawing where the carried side passes the gate's truth-free checks with margin, skip the model and show the carry. The shortcut is turned on only if every skipped drawing on the held-out moments also passes the truth-based gate. The skipped share is logged per shot.

Non-functional:
- **The bar:** every frame of every held-out moment passes the gate at the user's thresholds. truth-pipe's own numbers are the floor.
- **Budget:** the side model's GPU-seconds per film second, plus the painter's, must fit the worker's share (u ≈ 0.75, measured next to the player).
  - The old refiner's slot (4.7/s × 30 ms ≈ 0.14) at 15.13 updates/s comes to ~9-10 ms per update for both sides.
  - Quality first: in the bake-off, timing is reported, not used to drop a candidate. Phase 6 judges 1x.
- **VRAM:** painter, side model and buffers fit within the worker's ~8 GB, with no weight streaming (E1).
- **Training:** local only, small runs, rests between runs (`--rest`). The film never leaves the machine.
- Zero wrong frames caused by the worker.

## Architecture

**Candidates** (a bake-off; each is trained on hidden-sides sequences; concrete checkpoints are verified current at step 1, with licences noted):

| | What | Why | Known risk |
|---|---|---|---|
| **D. Learned carry** | A small convolutional net that predicts per-pixel motion for the previous side and the plate, a blend mask and a residual, then gets adversarial post-training (the real-time frame-interpolation class) | ms-class; copies sharp pixels at full detail; learns from the truth what moves with what (the slow-side failure) | Invents little: effects and characters that enter |
| **B. Wing specialist** | 0.27-0.6B, ≤ 2 steps; only the sides are generated; the picture enters once as a prefix (seam bands, thumbnail, previous sides, plate) | Priced to fit 15 updates/s at u 0.25-0.56 on a 144-token grid [E]; it can invent | The most to build; local training only; detail at the seam needs fine tokens there |
| **A. SANA-Streaming 2B, LoRA** | An Apache-2.0 streaming video model; the source video enters by channel concat | The only shipped real-time number on consumer Blackwell (24 fps at 1280x704 on an RTX 5090) | 8-frame latent frames against holds and cuts; Windows sm_120 kernels; LoRA on 12 GB; detail |
| **D + zones** | D on every drawing; a generator (B-band class) only on drawings with flagged zones | D's speed and detail, with invention where it's needed | The zone must agree with D's next update |

**Data.** Hidden-sides sequences in X1's geometry: the sides are half the picture's width, at the film's own size and full height. They cover whole shots of JJK S03E12 and Judas S01E02. Inputs are made the way the runtime makes them:
- the camera's carry of the model's previous output;
- a plate stand-in (the painter's plate where it is cached);
- exit strips and zone hints.

Held out: X1c's 6 moments, the shots of the 24 metric moments, and a set of Judas moments.

**Training:**
1. Teacher forcing: the previous side is the truth, jittered.
2. Rollout over 8-24 drawings on the model's own outputs, so it learns to correct its own drift.
3. Adversarial post-training for lines.

Losses: per-pixel and perceptual against the truth; seam (the first 8-32 px, detail included); temporal (the side's change per drawing matches the truth's: no pops, no boil). Validation is the gate.

**Judge:**
- The gate on the held-out moments, with truth-pipe as the ceiling.
- For the real-use look beyond the true edge, where no truth exists: the gate's truth-free measures, against today's best offline recipe on the same moments.
- The user's labelled review: version next to the truth, worst moments first.
- A blind confirmation for the final candidate, at most 12 trials.

**Runtime.** The winner is exported (TensorRT-RTX, or torch.compile with CUDA graphs; verified current at step 1) and resident in the worker. Updates are scheduled earliest-deadline-first ahead of the playhead, with kernels short enough for E1.

**Transport.** One image per side per drawing: side store schema 3, a per-shot stream of drawing frames.
- At full size this is ~100 MB/s on action, far above phase 2's "a few MB per second".
- Two options, settled by reasoning plus at most one screen spike:
  - compressed frames (BC-class, uploaded as they are) in the CPU store;
  - the GPU ring (CUDA↔D3D11), phase 2's named upgrade path.
- The player shows each drawing's sides without warping; the seam pull stays.
- This changes ADR 0028 again: its schema-2 amendment waits for this choice.

**Tiers:** T4 is the per-drawing sides (top). T3 is retired. T0-T2 are unchanged, behind phase 4's seam guard.

## Related code files

- Create:
  - `spikes/ai-surround/make-side-sequences.py` (training sequences from local episodes)
  - `spikes/ai-surround/train-side-model.py` (`--rest`)
  - `spikes/ai-surround/side-model-bakeoff.py` (candidates in the X1c slots, plus timing; `--rest`)
  - `spikes/ai-surround/live/side_model.py` (the resident runtime)
  - Player: `src/perf1-ai-sides-drawings.c` (the per-drawing stream)
- Modify:
  - `true-sides-test.py`: a `--fill-from` option puts a candidate's sides into the harness.
  - `live-worker.py`, `live/worker_state.py`: scheduling the updates.
  - `live/side_store.py`, `live/side-store.layout.json`: schema 3.
  - The player's fill: T4.
- Reuse: `hidden_sides.py`, `live/camera_model.py`, `live/step_planner.py`, `live/structure.py`, phase 4's gate.

## Implementation steps

1. **Concrete candidates** for D, B and A, each verified current (state-of-the-art check, licences). A gets this phase's only spike: does it run on this laptop (Windows sm_120 kernels), and at what update rate and VRAM on a side-sized canvas?
2. **Sequence builder** for JJK and Judas, with the holdouts. Check the disk budget first (C: is nearly full).
3. **Train D** (hours, with rests) and gate it on the held-out moments. Where it fails, record what failed: seam, drift or invention.
4. **Only where D fails on invention:** train B (or A's LoRA, if step 1 passed) for the whole side, or for zones (D + zones). Rests.
5. **Bake-off verdict:** the gate, timing next to the player, and the user's labelled review of the best one or two.
6. **Integration:** worker updates, schema 3 transport, player T4, the per-shot T4 rule, logs. Then the speed shortcut, behind its own gate.
7. **JJK and Judas in the player**, with rests. Run the gate on the player's dumped sides at the X1c moments.
8. **The user:** a labelled review on both screens, then the final blind confirmation (at most 12 trials).

## Success criteria

- [ ] Every frame of every held-out moment passes the gate at the user's thresholds: seam break, drift and snap, detail step, boil.
- [ ] Cross-show: trained on JJK and gated on Judas (and the reverse), or a per-show adaptation closes the gap.
- [ ] Timing next to the player is recorded, and the painter's and the side model's GPU-seconds per film second on JJK action are compared with u. Zero wrong frames come from the worker.
- [ ] VRAM fits the worker's budget, with no pagefile growth.
- [ ] The user's labelled review finds no break. In the blind confirmation, the final candidate is told from the truth at most 7 times in 12.
- [ ] The training recipe and the data builder can be reproduced from local files only.

## Risk assessment

- **No locally trained model passes at 15 updates/s.** Responses, in order:
  - D + zones;
  - full detail only in the seam band, coarser further out (gated);
  - updates that read coming drawings in chunks;
  - a longer head start;
  - then back to the user with the numbers.
- **Drift over long shots,** because each update builds on its own output. Responses: rollout training, the plate as an anchor, and the gate's drift measure on long shots.
- **Blur.** Responses: copy-based heads (D), adversarial post-training, the plate's sharp pixels, and the gate's detail step.
- **Flicker.** Responses: a temporal loss, updates only on new drawings, and the gate's boil measure.
- **Teacher forcing differs from runtime.** Response: a rollout fine-tune on the runtime's real inputs (the model's own previous output, the live camera map).
- **Hidden sides differ from real use.** Real sides lie beyond the film's frame, twice as far out. Response: real-use moments are judged with the truth-free gate and the user's labelled review against today's best.
- **The transport costs wrong frames.** Responses: upload each drawing off the present path; move to the GPU ring if needed.
- **The painter leaves too little GPU.** Response: the painter's plates become anchors for long shots only, and paint mode covers the rest; the split is reported.
- **Disk.** Responses: training sequences are compressed, and stale caches are pruned first.
- **Rollback:** T0-T2 behind the seam guard stay the shipped behaviour until T4 passes; the player's T4 path stays behind its own switch.
