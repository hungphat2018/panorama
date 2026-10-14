# Phase 4: the per-frame gate, the ladder, X1c re-scored (2026-10-14, 10:31-12:00)

Plan: [phase 4](../261013-0718-ai-surround-live-path/phase-04-structure-path.md). Asked: start phase 4 with the per-frame gate, then the ladder for the user.

## Outcome

- **Gate built:**
  - Every measure is per frame and per side; no mean is a verdict.
  - It reproduces this morning's per-frame numbers exactly (table below).
- **Ladder built and checked; the user took 5 of 32 trials.**
  - The two clips looked nearly the same to them, "but sometimes flickering on left or sometimes I see the very clearly seamline".
  - They asked Claude to judge the quality itself. The flicker and the line were the ladder's events, and the reference (truth-pipe) was clean.
- **Bars set:**
  - Carried truth: at the measurement floor.
  - Generated sides: from the user's 5 answers.
- **X1c re-scored per frame** (step 3, done):
  - The raw truth and truth-pipe pass every frame of all 6 moments.
  - Every carried version fails, except on edge-move, where its files are byte-identical to the reference.

## Gate

Files: `spikes/ai-surround/live/side_gate.py` (measures, importable by the worker), `side-gate.py` (CLI: `x1c`, `clip`, `md5`, `normal`), `live/side-gate.json` (bars with sources).

The decode follows this morning's check: the two-screen clip goes back to the 1920x540 strip, grey, area-scaled.

| This morning's number | Gate |
|---|---|
| pan, planner-v2, left seam, frame 7 | 11.4 |
| slow-side, planner-v2, right, frame 54 | change 58.8 against the truth's 1.3 (jump 57.5) |
| near-every inner line, pan | 20.3 at frame 64 |
| near-every inner line, slow-side | 23.2 at frame 36 |

### Measures

Against a truth, per frame and side:
- **Pixel error:** in the 16 px next to the seam, in the inner and outer halves, and within ±16 px of a source-change line.
- **jump:** two-sided, |side change − truth change|, so a stall fails too.
- **drift and shape:** tile displacement against the truth (px), and the weakest correlation peak.
- **motion_vs_truth and drift_vs_truth:**
  - Both use the side band's displacement against the truth in the same frame.
  - motion_vs_truth is its change from the previous frame; drift_vs_truth is its largest change within 1 s.
- **detail_loss:** the side's sharpness next to the seam over the truth's, as a 1 s running median.
- **boil:** side change on held frames, beyond the truth's.

Without a truth: seam_step, seam_colour, seam_edges, detail, seam_motion, seam_drift, pop, boil_free.

### What real content did to the no-truth measures

The first versions were fooled by real content:
- a painting's frame on the seam column (pan, frame 20);
- a dark line through the seam (pan, frame 54);
- flat tiles giving false motion.

Redesigned: the seam step uses medians over rows, detail compares sharpness, and the motion measures have texture floors.

Normal ranges on truth-pipe over 30 moments (X1c's 6 plus the 24 of `x1c-metrics`, rebuilt as clips; `gate-normal/normal.json`):

| Measure | Real content reaches | Bar |
|---|---|---|
| seam_step | 7.5 codes (p99.9 2.5) | 7.5 |
| seam_colour | 3.5 | 3.5 |
| detail | 0.669 (lowest) | min 0.669 |
| seam_edges | 1.26 | none |
| seam_motion | 61 px (p99 26) | none: action moves differently either side |
| seam_drift | 100 px | none |
| pop | 16.4 | none |
| boil_free | 73 codes (true sides change while the picture holds) | none |

So without a truth, one frame cannot tell a break from real action. Tests use the truth-based measures. The seam guard needs another approach (open).

### Detail, from phase 3's swap tests

detail_loss of each live plate against the 25-step plate:

| Plate | Median | Lowest | User |
|---|---|---|---|
| 1 MP | 0.85 | 0.61 | seen, 14/20 and 16/20 |
| 2 MP | 0.99 | 0.83 | 17/20: what was painted, not softness |

- Per frame, the truth's own two copies differ by up to 0.78 on action.
- A 1 s running median brings that floor to 0.923. Bar: 0.90.

## Ladder

`out/demo/20261014-105347-side-ladder` (`side-ladder.py`, `side-ladder.lua`).

**Design:** X1c's pan, two interleaved weighted up-down staircases.
- **break:** a 3-frame ramp, then a snap back: the measured shape of the pan's frames 5-7 (1.3, 2.4, 3.5 px).
- **drift:** a slide over 1 s, then a snap back.
- **Shifts:** made on the 2x frame with Lanczos. The side moves outward, so it shows real content and needs no border fill.
- **Order:** counterbalanced, with no run longer than 3.

**Checked before it reached the user:**
- 45 clips, none identical.
- Each event's measured shift matched the intended one (e.g. 2.0 px: wanted (1.24, 1.56), measured (1.26, 1.51)), with ≤ 0.32 codes elsewhere.
- A headless mpv run drove the script over IPC; crops and x-t slices were checked by eye.

**The sitting:** 5 answers, then stopped.
- break 5.7 px and 8 px: picked right; 2.8 px: wrong.
- drift 11.3 px and 22.6 px: picked right.

**Claude's check of what the user saw:**
- truth-pipe against the raw truth: side sharpness 0.95-1.13, frame-to-frame swing median 0.008. No flicker of its own.
- The "flicker on the left" and the "clear seam line" were the events. For example, the 11.3 px drift reaches 11.5 codes at the inner line and a 5.4-code jump at the snap.

## Bars (`live/side-gate.json`)

Policy since the user's request at 11:35:
- **Carried truth (X1c-style):** bars at the measurement floor, 25 % above the largest difference between the truth's own two copies (raw and through the world) over X1c's 6 moments.
- **Generated sides (`--generated`):** half the user's estimated 75 % sizes. Break x75 ≈ 4 px (seen 5.7, missed 2.8). Drift x75 ≤ 11.3 px.

| Bar | Carried | Generated |
|---|---|---|
| seam / line / inner / outer (codes) | 2.1 / 1.6 / 1.55 / 1.45 | not judged (a generator does not copy pixels) |
| drift (px) | 0.3 | 2.0 |
| jump (codes) | 0.55 | 0.71 |
| motion_vs_truth (px) | 0.6 | 1.9 |
| drift_vs_truth (px) | 0.6 | 5.6 |
| detail_loss (1 s) | min 0.9 | min 0.9 |
| boil (codes) | 1.0 | 1.0 |

A verdict says "incomplete" while a required bar is missing.

## X1c re-scored per frame

`out/demo/20261014-043348-true-sides-c/gate-20261014-x1c-rescore/` holds `gate.json`, a CSV per version, crops of the worst frames and x-t slices (across the seam, and across the inner line where it fails).

Frames failing, left/right, of 69 per side:

| Moment | truth | truth-pipe | frozen | planner-v2 | near-every |
|---|---|---|---|---|---|
| fight | 0/0 | 0/0 | 68/68 | 43/49 | 41/27 |
| reaching | 0/0 | 0/0 | 68/68 | 65/55 | 23/43 |
| pan | 0/0 | 0/0 | 68/68 | 68/68 | 62/67 |
| edge-move | 0/0 | 0/0 | 68/68 | 0/0 (same file as truth-pipe) | 0/0 (same file) |
| slow-side | 0/0 | 0/0 | 68/68 | 66/68 | 41/68 |
| pan-patches | 0/0 | 0/0 | 68/68 | 68/68 | 67/67 |

With `--generated`, slow-side frozen fails 68/68 on both sides. That was the phase 5 session's repro: under the first generated bars, its right side had passed.

## Other phase 4 steps

- **T3 retired as a display tier:**
  - Player: `perf1-ai-sides.c` defaults to no patches; `--ai-patches` opts in, and `--ai-no-patches` is still accepted. Not compiled yet: another session's painter bench holds the machine.
  - Worker: `live-worker.py --patches` opts in. Without it, no page is packed, cached or published. With it, a shot cached by a run without it is analysed again.
- **`true-sides-test.py`:**
  - The gate runs before any launcher exists.
  - Trials whose two clips are the same file are left out.
  - The order is counterbalanced over the trials kept.
- **Inputs contract:** `live/side-model.inputs.json` (`panorama.side-model-inputs/1`).

## Review (code-reviewer, `plans/reports/code-reviewer-261014-1125-phase4-gate-ladder-review.md`)

**Fixed:**
- H1: "pass" with missing bars;
- H2: `--patches` on a cache made without pages;
- M1: hard links counted as identical clips;
- M2: balance broken after drops;
- M3: the gate ran after the launchers;
- M4: the reference decoded once per version (now once per moment);
- L6: missing ids compared as the same file;
- L7: misleading "moved" flags on low-is-bad measures;
- L8: the 0.87 / 0.85 sharpness mismatch.

M5 (a fully blurred side skipping the no-truth detail check) was tried and reverted: the change made flat walls in the truth fail. detail_loss catches blur against the truth.

**Left:**
- Each clip is still decoded twice: grey, kept for exact reproduction, plus YUV for colour.
- Half-size and normal-range clips are cached by name.
- Exit strips are still computed with T3 off; they are phase 5's inputs.

## Next (phase 4)

1. Compile `bin/perf1-p3-ai5.exe` and run `live-store-selftest.py`, when no timing job runs.
2. Seam guard. Single-frame no-truth measures cannot separate on action, so candidates are:
   - judging only frames where the picture's bands move rigidly;
   - the step planner's own unexplained-change measures (U).
3. Cap the analyser's lead.
4. Screen session: wrong frames at 240 Hz, with T3 off and the lead capped.

## Unresolved questions

1. Carried bars sit at the floor, far below what the user saw (2.8 px breaks were missed; the drift bar is 0.3 px). Keep that strictness, or relax it toward the evidence bars once phase 5 has candidates? Recommendation: keep it until a candidate fails only below the evidence bars, then show the user those frames.
2. The ladder covered 5 of 32 trials. Every generated bar rests on those answers. A second, shorter sitting would narrow the break estimate (2.8-5.7 px); only if the user wants to.
3. The seam guard's approach (above).
