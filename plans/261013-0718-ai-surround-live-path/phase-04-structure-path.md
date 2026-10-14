---
phase: 4
title: "Structure path, then the per-frame bar and the side model's inputs"
status: in-progress
priority: P1
effort: "built in 2-3 d; replanned work ~1-1.5 d agent + one screen session (the ladder became 5 trials: the user asked Claude to judge)"
dependencies: [1, 2]
---

# Phase 4: Structure path, then the per-frame bar and the side model's inputs

## X1c verdict and replan (2026-10-14)

**What the user saw.** The user stopped the blind test after 8 of 48 trials (`answers-20261014-093034.txt`) and judged the free-look clips instead:
1. pan, frame 7, left seam: the waterfall painting breaks at the seam.
2. slow-side, frames 53-54, right side: the hair and outline tear into a zigzag, then the side jumps in one frame.
3. pan, right side: the side moves in small jumps while the picture slides smoothly.
4. near-every: clean at the picture's edge, but it tears at the halfway line inside the side.
5. edge-move: the three versions are the same file, so those trials had no answer.

**The process failures the user named:**
- The phase's central claim (structure moves the sides on 63-81 % of steps) was shown only by averages, never by eye.
- Pass numbers were means over all frames.
- near-every could not pass the way it was built.
- The test reached the user unchecked: identical files, unwatched clips, 48 hard choices.
- The motion bar (r ≥ 0.7) was set aside, not replaced.
- Wrong frames at 240 Hz are still 6.0/min.

**Per-frame check.** Method: every frame of the six moments, decoded from the free-look clips back to the 1920x540 strip (grey, area-scaled from the screen layout), compared with truth-pipe. Units: codes = mean absolute difference, 0-255.
- **pan, planner-v2, left seam** (the 16 px beside it):
  - The side was redrawn about every other frame, yet one carried frame is already ~4 codes off.
  - Three carried frames in a row (frames 5-7) reach 11.4: the break the user saw.
  - Right side, frames 62-68: the carried side changes up to 2.4x as much per frame as the truth (11-14.5 codes against 6). These are the small jumps.
- **slow-side, planner-v2, right side.** The truth drifts slowly, ~0.3 px a frame (21 px over the clip). The carried side:
  - swung 14-26 px one way, jumped 33 px the other way at frame 36, and was ~51 px off at frame 51;
  - lost its shape (phase-correlation peak 0.46-0.6).

  A frozen side is only 6-20 px off over the same frames, so the camera did worse than holding still. The planner reads only the picture, so it could not see this. Frame 54's redraw changes the side by 58.8 codes against the truth's 1.3: that is the jump. No character enters here: the tear is the hair at the seam, broken by the drift.
- **Worst frames.**
  - planner-v2's seam reaches 8.3-12.5 on the five moments where it differs from the truth.
  - near-every's seam stays ≤ 0.8 everywhere, but its inner line (±16 px) reaches 7.2-23.2.
- **edge-move.** The planner flagged every frame (24/s per side), so planner-v2 and near-every equal truth-pipe byte for byte.
- **The "perfect generator" is not neutral.** A redraw from the truth ignores what the side was showing, so each one shows the carry's whole error at once: the pops and the inner tear. A model that starts from the carried side could hide a tear, but not a side moving the wrong way.

**Conclusion:** no cadence makes shown carried sides pass. At the sides' reach the camera can't be trusted on real footage (a curved wall, a slow move). A planner that reads only the picture can't see its errors.

**The same morning, phase 3's swap tests** found that sides with about half the film's fine detail "read as a join line" next to the sharp picture. So the bar covers detail at the seam too, not only position and motion.

**User decisions (2026-10-14, late morning):**
1. **Redraw every drawing.** On every new drawing, a side model re-makes each whole side. It starts from the carried side and the picture, and is trained on the true sides. The structure path becomes its inputs. No raw carried frame is shown, except as a speed shortcut where a seam check proves it invisible. See [phase 5](./phase-05-zone-refiner.md).
2. **Pass bar: visible at normal speed** on both screens. A ladder of about 5 minutes measures the user's threshold once. After that, every test is gated frame by frame on it.
3. **The user's checks:** a version reaches the user only after it passes the gate. It is shown by name next to the truth, with the worst moments first. A blind test is used only to confirm a final candidate, at most 12 trials.
4. That session replanned only; nothing ran.

## Overview (replanned)

This phase now builds what every later test stands on:
- **The per-frame gate**: seam break, drift and snap, detail step and boil, per frame and side, with or without a truth.
- **The ladder**: the user's visible threshold, measured once.
- **A seam guard** for the tiers still shown carried during catch-up (T1, T2).
- **The structure path as the side model's inputs**, behind a written contract. T3 is retired as a display tier.
- **Wrong frames at 240 Hz** back to phase 2's level.

## Requirements

Functional:
- **Gate** (`spikes/ai-surround/side-gate.py`). It takes any version as frames or a clip, in the X1c strip geometry or from the live player's side dumps, plus the truth when there is one. It never gives a mean as a verdict.
  - With a truth, per frame and side:
    - the error in the 16 px beside the seam;
    - the error in the inner and outer halves;
    - the error within ±16 px of every line where the side's content source changes;
    - the side's change against the truth's (jump);
    - tile displacement against the truth (drift);
    - change on held steps (boil).
  - Without a truth (runtime and real use; normal ranges from truth-pipe on X1c's 6 + 24 moments):
    - **seam step:** the colour step across the seam against the picture's own column steps, and edges that cross the seam but don't continue;
    - **detail step:** fine detail in the side's first 32 px against the picture's last 32 px;
    - **seam motion:** tile motion just inside the picture against just inside the side;
    - **pop:** the side's change at a drawing against the picture's change next to the seam;
    - **boil:** any side change while the picture holds.
  - Output:
    - pass/fail per frame against the thresholds;
    - the worst frames as crops (truth above version) and x-t slices across the seam;
    - an md5 of every clip, so identical trials are caught;
    - a table per version and moment.
- **Ladder.** The user takes it once, ~5 min, on both screens at normal speed. Each trial is a forced choice: "which clip had a break or a jump in the sides?". Two staircases on the pan's truth-pipe:
  - (a) **seam break:** the side shifted by d px against the picture for 3 frames, the shape of pan frames 5-7;
  - (b) **drift and snap:** the side drifts p px over 1 s, then snaps back in one frame, the shape of slow-side frames 30-54.
  - The gate scores every ladder clip, so the thresholds are set in its own units. A frame fails at half the size the user picks correctly 3 times in 4.
  - The detail step's threshold comes from phase 3's swap tests: the 1 MP canvas was seen; the full-size result is pending.
- **Seam guard.** Before the worker publishes a shot's T1 mosaic or T2 plate, it renders that shot's sides at the gate's scale and runs the truth-free gate on every frame. A shot that fails shows T0 glow, and tiers still change only at cuts. This changes what the viewer sees, so it needs the user's OK (ADR 0017).
- **T3 off as a display tier.** The player never shows the carried canvas plus patches as a tier. The patch path stays in code, off by default, until phase 5 chooses its transport.
- **Inputs contract** for the side model, per new drawing and both sides, versioned next to the store layout (`live/side-model.inputs.json`):
  - the picture: seam bands at full size, plus a thumbnail;
  - the side model's previous output, warped by the camera map;
  - the shot's plate or mosaic, warped to this drawing;
  - exit strips (the picture's last view of what left it);
  - zone hints: where the picture's change near the seam is unexplained;
  - identity crops of characters the seam cuts;
  - the shot's coming drawings (the film is known ahead).
- **The analyser's lead is capped.** It now runs up to 600 s ahead at full speed; its CPU share should follow the playhead's need.

Non-functional:
- The gate checks a 2.9 s moment in seconds on the CPU; each clip is decoded once.
- Rests between screen runs (user rule).

## What exists (built 2026-10-14 night), and its new role

| Part | Code | New role |
|---|---|---|
| Camera model (projective, re-anchored) | `live/camera_model.py` | The warp guess for the side model's previous output; the T1/T2 warps |
| Step planner | `live/step_planner.py` | New-drawing flags (when to update), zone hints, the scheduler's cost model |
| Exit strips, patch pages | `live/structure.py` | Strips become inputs; pages are off |
| Side store schema 2, player T3 compositing | `live/side_store.py`, `src/perf1-ai-sides-patches.c` | Frame maps for T1/T2; T3 compositing off |
| Identity clusters and crops | `identity-refs.py` | Inputs (painter and side model) |
| X1c harness | `true-sides-test.py --x1c`, `--x1c-metrics N` | The gate's material; the bake-off's slots in phase 5 |

Details: [implementation report](../reports/implementation-261014-0240-phase4-structure-path.md).

## Related code files

- Create:
  - `spikes/ai-surround/side-gate.py` (the gate)
  - `spikes/ai-surround/side-ladder.py` (ladder clips and staircase; answers through `trial-answer-keys.lua`)
  - `spikes/ai-surround/live/side-model.inputs.json` (the inputs contract)
- Modify:
  - `live-worker.py`, `live/worker_state.py`: the seam guard and the analyser's lead.
  - The player: T3 off by default (today `--ai-no-patches`).
  - `true-sides-test.py`: the gate on every build, and no trial whose two clips are identical.
- Reuse: `make-wide-demo.py` (screen layout), `true-sides-test.py`'s clip writer, `trial-answer-keys.lua`.

## Implementation steps

1. **Gate.** Build the truth-based measures first. They must reproduce this session's numbers: pan frame 7 seam 11.4; slow-side frame 54 change 58.8; near-every's inner line 20.3 (pan) and 23.2 (slow-side). Then add the truth-free measures, with normal ranges from truth-pipe on the 30 X1c moments.
2. **Ladder.** Build the clips and the staircase; the user takes it (~5 min). Write the thresholds into the gate's config.
3. **Re-score X1c per frame** at the thresholds: one pass/fail table per version and moment. This closes the old criteria below.
4. **Worker and player:** add the seam guard, turn T3 off by default, cap the analyser's lead.
5. **One screen session** (idle GPU, rests): A/B runs for the wrong frames at 240 Hz with T3 off and the lead capped. Target: phase 2's 1.5/min.
6. Write the inputs contract.
7. Code review, report, plan sync.

## Progress (2026-10-14, 10:31-12:00)

Details: [report](../reports/implementation-261014-1200-phase4-gate-ladder-x1c-rescore.md).
- **Gate built:**
  - `live/side_gate.py`, `side-gate.py`, bars in `live/side-gate.json`.
  - It reproduces the X1c numbers exactly: pan frame 7 seam 11.4; slow-side frame 54 change 58.8 against the truth's 1.3; near-every's line 20.3 and 23.2.
- **No-truth measures can't separate a break from real action on single frames.** Real content reaches 61 px of seam motion, pop 16 and boil 73 codes on truth-pipe over 30 moments, so those have no bar. Tests judge against the truth.
- **The ladder:**
  - The user took 5 of 32 trials, then asked Claude to judge the quality itself (11:35). The two clips looked alike, "but sometimes flickering on left or sometimes I see the very clearly seamline": those were the ladder's own events, and the reference was clean.
  - Evidence: breaks seen at 5.7 and 8 px and missed at 2.8 px; drifts seen at 11.3 and 22.6 px.
- **The user's ladder bar is replaced:**
  - **Carried truth:** bars at the measurement floor, 1.25 × the largest difference between the truth's own two copies.
  - **Generated sides (`--generated`):** half the sizes the user saw.
  - **Detail:** from the swap tests. 1 MP plates at 0.85 of today's best were seen; the bar is 0.90 on a 1 s median.
- **X1c re-scored per frame:** the truth and truth-pipe pass every frame of all 6 moments; frozen, planner-v2 and near-every fail on 23-68 of 68 frames per side, except edge-move's byte-identical copies.
- **Phase 5's request (11:49), done:** two-sided jump, motion and drift against the truth (same-frame displacement, its change), and drift for generated sides.
- **T3 retired:**
  - The player and the worker default to off (`--ai-patches`, `--patches` opt in). The player binary is not compiled yet.
  - `true-sides-test.py` gates before launchers, drops identical-file trials and keeps the balance.
  - The inputs contract is written.
- **Code review:** H1, H2, M1-M3, L6-L8 fixed; M4 partly; M5 tried and reverted (it failed the truth's flat walls).

## Success criteria

- [x] The gate reproduces this session's per-frame numbers on X1c; normal ranges are set from truth-pipe.
- [x] ~~The ladder is taken and its thresholds recorded~~. Replaced (2026-10-14, the user's request) by measurement-floor bars for carried truth and bars from the user's 5 ladder answers for generated sides; the detail step comes from the swap tests.
- [x] X1c is re-scored per frame: a table per version and moment.
- [ ] The seam guard runs in the worker. Shots that fail show glow, and the share of film time moved to glow is logged for the user's OK. The no-truth measures alone can't do it (above); the next options are rigid-frame judging or the planner's U.
- [ ] T3 is off by default (done in source; the binary needs compiling); the inputs contract is written and versioned (done).
- [ ] Wrong frames at 240 Hz are at phase 2's level (1.5/min), or A/B runs name the remaining cause.

Closed by the replan (kept for the record):
- [x] The camera model is bounded on synthetic moves (pan 0.17, zoom 8.0, roll 5.9 px at 240 px). On X1c's moments its far error is lower than translation-only on four and 0.1 higher on two (reaching 16.8 against 16.7, edge-move 33.2 against 33.1). The old report's "lower or equal on all six" was wrong by that margin.
- [x] The planner's GEN rate is within ±20 % of the research's (4.41/s against 4.7); the full planner makes 7.3 generator calls per side per film second.
- [x] X1c built and judged: no carried version passes; the cadence question is closed.
- [x] "T3 alone tracks the picture at r ≥ 0.7" is replaced by the gate's per-frame jump and drift measures. The r bar was miscalibrated: the truth's own r on these moments is 0.50.
- [x] Patches reached the screen (2901 applied, 0 failed). T3 is now retired.
- [x] Identity clusters and crops for JJK: 176 clusters, checked by eye.
- [ ] The by-eye verdict on trail entries moves to phase 5: entries are the side model's first-appearance case.

## Risk assessment

- **A truth-free measure misses a break the user sees, or flags one they don't.** Its normal range is calibrated on truth-pipe and its threshold on the ladder. Every break the user reports becomes a regression case. Where a measure can't separate the two, the truth-based gate decides in tests, and the seam guard leans to glow.
- **The seam guard moves many shots to glow,** such as 3D moves. The share is logged, and the side model takes those shots once the worker has caught up. The number goes to the user with the ADR.
- **One ladder sitting is too coarse.** If a staircase ends near a boundary, repeat it on another day.
- **Wrong frames stay high with T3 off.** Then the cause lies in the worker's load: pace the analyser further. Phase 5's transport choice (the GPU ring) is the next lever.

## History: progress (2026-10-14, night)

- 01:15: started (panorama-cd) after phase 3's shared-file handover. The user is away overnight and authorized autonomous work; the GPU and the screens stay with phase 3 until it frees them.
- Amended below before building, because X1 failed (next section).
- 02:10, built and checked without screens:
  - Camera model:
    - synthetic error at the sides' far edge: pan 0.03 px, zoom 1.6, roll 2.5;
    - on X1's pan, projective carry with no refresh: far 13.1 against translation's 19.9;
    - untrusted flows are arbitrated in the picture's bands.
  - Step planner, with a drift trigger and a travel trigger. Gains measure lighting at fixed content (a steady pan stays at 1.000).
  - Exit strips and patch pages; side-store schema 2 (both ends).
  - Player: `bin/perf1-p3-ai4.exe`, which renders the homography and composites the patches. It compiles with 0 warnings.
  - Self-tests:
    - store 251/251, including a patch page read back by the C reader;
    - analysis 15/15: camera error 0.16 px over a 675 px pan; strip texels 0.33 codes from the true world;
    - sets 7/7; cache 23/23.
  - X1c harness (`true-sides-test.py --x1c`, `--x1c-metrics N`). On 24 moments:
    - planner-v2: error near 1.87, far 2.28, at 9.2 refreshes/s per side;
    - near-every: near 0.46, far 2.25.
- 02:37, speed and the worker end to end:
  - **The analyser was slower than the film.** At 480 px with a DIS anchor fit on every drawing it ran at 23 fps (1.6 CPU-s per film-s).
    - Now: camera flow at 240 px, the anchor fit every second drawing, the planner still at 480 px (the research's scale).
    - That gives 22.5 ms per frame (0.54 CPU-s per film-s) and 61 fps on a worst-case pan where every frame is a new drawing. Pans stay exact; zoom and roll errors at the sides' far edge are 6-8 px.
    - In the worker, with decoding, mosaics and sets, analysis runs at about 1.85x film speed (v0: 3.5x).
  - **Headless worker run** (stand-in player, no painter, 420 s, `out/live/smoke/20261014-022933-phase4-headless`):
    - 322 shots analysed; worker and player rc 0;
    - 196 showings: 91 at T3 (canvas + patch page, 187 s of film), 4 at T1, 101 glow (shots with no real side pixels, as in phase 2 without the painter).
  - **Planner over those 591 s of JJK** (`planner-stats.py`):
    - new drawings 15.13/s (research 14.97);
    - the research's GEN definition gives 4.41/s against 4.7 (within ±20 %);
    - per side: HOLD 34.6 % of steps, STILL + SLIDE + GAIN 34-35 %, PATCH 13.5 %, GEN 17 %;
    - that is 7.3 generator calls per side per film second.
  - **Fixed:** `SideStore.open` crashed on a store of the old schema (a numpy view kept the map open). It now refuses it and the worker makes a new store.
- 02:40-04:30, review fixes and the screens (details in [the implementation report](../reports/implementation-261014-0240-phase4-structure-path.md)):
  - **Review fixes:** all of the first review's findings H1-H4, M1-M5 and L1-L7. Gains are back to v0's; strips are split per edge; pages load lazily; patches apply forward only; three texture pairs are made at warm-up; canvases are copied before pages; rejected pages are skipped; cache keys are split.
  - **Screens:** T3 shows 364 of 600 film s in a 10-min full-screen run, with 2901 patches applied and 0 failed.
    - A player bug (one stand-in texture bound to two descriptors) made libplacebo refuse passes: 332 failures, each with a 25 ms backtrace. Fixed.
    - Wrong frames at 240 Hz are 6.0/min against phase 2's 1.5; with the patch path off, 3.1. The cause is shared between the worker's load and the per-patch draws. Fixes need a screen session.
  - **Camera drift on 3D moves** (X1's gallery pan): 30-45 px at the corners after 21 frames.
    - Mosaic v0.5 keeps only the first picture when the frames disagree (more than 10 % of texels).
    - Keyframe ECC refinement was measured (20-25 % better on smooth moves, refused on fast ones) and not adopted.
  - **Fades** (phase 3's finding): only lit kept frames vote and set the gain reference.
  - **Disk full at 03:56:** stale pages took 9.8 GB. They were cleaned; pages are now compressed and strips capped at 25 % of the picture.
- 04:30-05:10, the CPU deliverables:
  - **X1c** rebuilt with the shipping camera: `spikes/ai-surround/out/demo/20261014-043348-true-sides-c`, 48 trials. Its 24-moment metrics: planner-v2 near 1.87, far 2.37, at 9.45 refreshes/s per side.
  - **Identity clusters** written (176, complete linkage at 0.14). A DBSCAN run had chained 722 tracks into one cluster and was discarded.
  - **Mosaic v0.7:**
    - regions where the frames disagree are left out (σ 12, 25 %);
    - inside the first picture, frame 0 itself.
    - A per-shot gate would have dropped 28 % of shots' sides.
  - **Keyframe ECC refinement:** measured, not adopted.

## History: amendments after X1 (2026-10-14, before building)

**X1 (scored 00:05):** every carried version was spotted every time: planner 10/10; warp 6/3/1 Hz 8/8 each. The pre-decided response says: if the planner is visible, stop and replan phases 4-5.

Reading of the failure (hits per moment, X1b's numbers in `out/demo/20261014-001741-true-sides-b`):
- **No PATCH class.** X1's planner refreshed only when U > 0.10 per step. The research rule also has a 0.02-0.10 class: structure plus a local patch, which is a generator call. That class is 15.6 % of steps, and X1 carried it stale.
- **No trigger for change that builds up.** The pan got 0 refreshes in 2.9 s. The reaching arm got 1.4-2.8 a second.
- **Translation-only camera.** The pan's curved wall skews at the outer columns.
- **Carried sides step.** Between refreshes they change less per frame than the truth, then jump at each refresh (reaching: 6 Hz 2.61, planner 2.07, truth 2.90).

So the open question was the cadence, not yet the compositor; the user would decide it from X1c. The phase built only what any replan keeps:
1. **Camera model, projective.** A homography per frame (8 DOF), picked per step by how well it holds in the picture's outer bands, and re-anchored against drift.
2. **Step planner** with the cadence as a parameter, a drift trigger, per-side gains, and zones for phase 5.
3. **Patch path** (worker → store → player): changes to the sides within a shot.
   - It serves this phase's exit strips, trails, and phase 5's refined zones.
   - Phase 2's architecture had patches ("upload new patches for coming frames into the shot's world canvas"), but phase 2 built only whole canvases.
4. **Identity references:** CCIP clusters and crops for the painter (phase 3) and the refiner (phase 5).
5. **X1c**, which replaces X1b and keeps its same-resampling rule and pipe control (agreed with phase 3's session):
   - **planner-v2:** τ 0.02, the drift trigger and the homography.
   - **near-every:** the inner half of each side is refreshed on every new drawing.
   - The user takes it once. Its verdict picks the cadence or confirms the replan.

**Trails:**
- Camera-driven exits come from exit strips: the picture's last view of what leaves it.
- Character-driven exits use overlay patches, and are built only after the core.
- Entries stay off until the user's by-eye check.
