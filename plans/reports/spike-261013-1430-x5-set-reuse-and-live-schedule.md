---
title: "X5: set reuse, real-pixel coverage and the live schedule on the action episode (JJK S03E12)"
date: 2026-10-13
type: spike (CPU only)
plan: plans/261013-0718-ai-surround-live-path/phase-01-decision-gates.md
---

> Saved by the parent session from the helper agent's returned text, 2026-10-13. Scripts: `spikes/ai-surround/set-reuse.py`, `spikes/ai-surround/schedule-sim.py`; outputs in `spikes/ai-surround/out/jjk/x5-*`.

## Decision

The painter needs our own compression. To keep ≥ 95% of film time at the top tier with no head start, a plate must take about:

| GPU share u | Plates only | With a refiner at 30-60 ms per GEN drawing |
|---|---|---|
| 0.6 | 1.7-1.9 s | 0.7-1.3 s |
| 0.75 | 2.1-2.3 s | 1.1-1.8 s |

All times are full-GPU seconds. Public speed-ups (3-9 s per plate) give only 28-80% top tier. Set reuse is about 1.10 on this episode, not the 1.5 assumed.

## Method as run

1. **Scan.** One sequential CPU decode of all 40,999 frames:
   - 480x270, ffmpeg `-threads 2`, BelowNormal priority, 86 fps; aligned to the content model's `diff_before` (correlation ≥ 0.99).
   - Per shot: camera path (`scroll-plate.camera_offsets`, flow mode) and band U per step (hold / slide / slide × gain), with two camera steps (flow mode and the content model's dx/dy) at thresholds 16 (full range) and 19 (≈16 video range).
   - Representative frames: start, middle, end, plus one per ¼ width of camera travel (2,500 frames, 3.7 per shot).
2. **Sets.** SIFT outside person/head/face boxes and letterbox bars, a one-to-one ratio test, then RANSAC similarity.
   - Candidates: 25,277 pairs within ±90 s, plus 4,084 far pairs (top 8 per shot by HSV histogram).
   - The rule was tuned by eye. The strict version rejected true pairs (the wheat field at a 25° dutch angle; gallery pairs with 10-12 inliers). Final rule: ≥ 10 inliers, inlier spread ≥ 0.8%, roll ≤ 35°, scale 0.4-2.5.
   - Check sheets: weak accepts 13/14 plausible, random accepts 14/14.
   - End-credit (ED) pairs are excluded: they match only on the ED's centre window overlay.
3. **Coverage.** Masks on each shot's key-frame grid.
   - The camera path is gated: steps with flow agreement < 0.3 or > 120 px per frame count as no motion. 9.7% of steps have low agreement, and shot 584 drifted 2,367 px without the gate.
   - Donors: every member of the set, mapped into the shot through chained similarities.
4. **GEN.** A new drawing is a step with `diff_before` ≥ 0.5; the worse side's band decides.
5. **Schedule.** Earliest deadline first, all jobs known at t = 0, run to completion.
   - A late plate moves on to the next shot it serves; a late GEN job is dropped.
   - Bisection on s/plate for 95% top tier; checked by hand on a toy timeline.

**Correction to the brief.** The content model's "op" region (600-799 s, shots 209-302) is this episode's own fight footage, not an opening sequence. The ED starts at shot 653 (1616.9 s) and runs 93 s to the end. Only the ED is cacheable.

## Numbers

### Sets

| Group | Shots | Sets | Reuse |
|---|---|---|---|
| All | 679 | 615 | 1.10 |
| Story | 653 | 589 | 1.11 |
| Story, shots ≥ 0.8 s | 579 | 527 | 1.10 |
| ED | 26 | 26 | 1.0 (cache it) |

- Story set sizes: 550 single shots, 31 pairs, 5 triples, 2 sets of 6-10 shots, 1 of 13 (the wheat field). 6 far pairs accepted. The strict rule gives 1.075.
- Plate-cover reuse (an earlier plate covers ≥ 80% of a later shot's sides, magnified ≤ 1.5x): 640 plates (1.06 shots per plate), or 575 when short shots skip. At ≥ 95% cover: 652 / 586.
- Upper bound: colour-merging every pair within ±30 s gives 1.18 at similarity ≥ 0.90 and 1.40 at ≥ 0.85 (which includes false merges). That caps the painter target at ≤ 2.35 s (u = 0.6) / 2.94 s (u = 0.75). Kongming's 385-plate estimate would give 2.52 / 3.15 s.
- Recall check: 3-5 of 14 colour-similar unmatched pairs show the same place.
- Plates per film-second: 0.33-0.36.

### Real pixels covering the side area

| Source | Mean over film time | Median | Persons removed |
|---|---|---|---|
| Own camera path | 8.1% | 1.5% | 7.5% |
| Own path + set | 11.1% | 1.8% | 10.4% |

- 76% of film time has < 10% coverage.
- ≥ 50% coverage: 4.9% (own) / 7.9% (own + set) of film time. ≥ 90%: 0.3% / 2.6%. Exactly zero: 26% / 23%.
- Per shot (own + set): mean 8.8%; 43 shots ≥ 50%, 6 shots ≥ 90%.

### GEN rate

New drawings: 14.97/s.

| Camera step, threshold | tau 0.02 | tau 0.10 | tau 0.30 |
|---|---|---|---|
| Flow mode, 16 | 8.73 | 5.23 | 1.93 |
| Content-model shift, ≈16 video range | 8.40 | **4.72** | 1.63 |
| Both, 16 | 8.47 | 4.80 | 1.61 |
| Hold only, 16 | 11.2 | 7.0 | 3.3 |

- The content-model row reproduces the earlier 4.7/s, so that earlier result used this shift on video-range luma.
- Story 5.51/s, ED 0.48/s. Counting the two sides separately: 8.4 zones/s.
- Per film-second: mean 5.2, median 1, p90 17, max 24; 47% of seconds have none.
- Per shot: median 4/s, p90 17/s. 31% of story shots have none, and 24% of story time is in shots above 10/s.

## Schedule: share of film time at the top tier

Set reuse, short shots skipping, ED cached, 527 plates.

| u | Refiner | 1 s | 1.5 s | 2 s | 3 s | 5 s | 9 s |
|---|---|---|---|---|---|---|---|
| 0.60 | none | .974 | .974 | .900 | .677 | .448 | .282 |
| 0.60 | 60 ms | .847 | .680 | .572 | .448 | .296 | .226 |
| 0.75 | none | .974 | .974 | .974 | .798 | .512 | .340 |
| 0.75 | 60 ms | .969 | .841 | .698 | .542 | .381 | .257 |

- GEN zones ready in time: 0.83-0.90 at u = 0.6; 0.90-0.995 at u = 0.75.
- Time until the generator stays ahead: about 0 when feasible (only the first shot is missed). Overloaded runs never get ahead.
- Run-ahead: min/max 9.6 s / 310-915 s when feasible; about 0.1 s / 40-120 s when overloaded.
- GPU-seconds per film-second:

  | | Values |
  |---|---|
  | Plates at 1 / 1.5 / 2 / 3 / 5 / 9 s | 0.31 / 0.46 / 0.62 / 0.93 / 1.54 / 2.77 |
  | Refiner added at 30 / 60 / 120 ms | 0.16 / 0.31 / 0.63 |

- Per-shot plates (579), u = 0.6, plates only: .970 / .970 / .830 / .612 / .397 / .244.
- Ceiling: 97.4% (set) / 97.0% (per shot). The first shot (9.3 s, starting at t = 0) can never be ready, and short shots that skip their plate have none.

## Painter s/plate needed for ≥ 95% top tier

| Policy | Refiner | u = 0.6 | u = 0.75 |
|---|---|---|---|
| Per shot, no reuse | none | **1.69** | **2.11** |
| | 30 ms | 1.18 | 1.61 |
| | 60 ms | 0.70 | 1.11 |
| | 120 ms | 0.05 | 0.18 |
| Set reuse | none | **1.86** | **2.32** |
| | 30 ms | 1.30 | 1.77 |
| | 60 ms | 0.78 | 1.22 |
| | 120 ms | not reachable | 0.20 |
| Plate-cover reuse | none | 1.78 | 2.22 |
| | 60 ms | 0.73 | 1.17 |
| No short-shot skipping | none | 1.56 (shot) / 1.71 (set) | 1.96 / 2.14 |

- Caching the ED changes nothing: it comes at the end, when the generator is already ahead. An opening near the start would help (not measured).
- tau sensitivity (set reuse, 60 ms): tau 0.02 gives 0.09 / 0.53 s; tau 0.30 gives 1.46 / 1.91 s.
- Rule of thumb: s/plate ≈ (u − refiner ms × 5.5/s) × 1617 s / plates. The simulation lands within ~3% of it.
- Head start that 3 / 5 / 9 s plates would need (arithmetic; set reuse, plates only, full GPU before playback): about 10 / 28 / 63 min at u = 0.6, or 6 / 24 / 59 min at u = 0.75.

## Limitations

- Registration recall is limited (dark or flat frames, person masks, median 118 keypoints per frame); the upper bound above is ≤ 1.40 reuse.
- A reverse shot looking the other way counts as a separate set, which is right for a plate.
- Plate cost is constant; long pans would need larger canvases (246 shots travel more than ⅛ of the picture width).
- GEN jobs are treated as independent of plates.
- The content scan is assumed done ahead of time.
- The persons-removed coverage uses boxes from every 12th frame.
- Slow dissolves can chain two places into one set.
- One episode only, with no opening sequence.
- Rests taken: 2 min after the scan and before re-running registration. Everything ran at BelowNormal priority with 1-4 threads, no GPU.

## Unresolved questions

1. Is a lower top-tier share (80-90%) or a few minutes of head start acceptable for action episodes?
2. What does the refiner cost per GEN drawing, and does it need the plate?
3. Should the comedy episode get the same run (likely higher reuse)?
4. Should shots shorter than 0.8 s borrow a neighbour's plate?

## Addendum (parent, 2026-10-13 ~17:40): two-speed painting

Question: what if long-visible plates come from the big painter and the rest from a small fast painter? Same EDF simulation (`schedule-sim.py` plan and rules), per-job plate cost. Scratchpad script `two-speed-painter-sim.py`, set reuse, short shots skipping, ED cached, 527 plates.

The table shows the big painter's s/plate needed for ≥ 95% top tier, with the small painter at 0.3 s/plate. "Film time" is the share of film time those plates are on screen.

| Big painter takes | Big plates | Film time | u 0.6, refiner 30 ms | u 0.75, 30 ms | u 0.75, 60 ms | u 0.85, 30 ms | u 0.85, 60 ms |
|---|---|---|---|---|---|---|---|
| all plates (X5 above) | 527 | 97% | 1.30 | 1.77 | 1.22 | n/a | n/a |
| plates on screen ≥ 2 s | 290 | 73% | 2.16 | 3.02 | 2.02 | 3.60 | 2.59 |
| plates on screen ≥ 3 s | 170 | 56% | 3.51 | 5.05 | 3.28 | 6.07 | 4.28 |
| plates on screen ≥ 4 s | 98 | 42% | 6.07 | 8.40 | 5.57 | 9.72 | 7.31 |
| calm plates (GEN ≤ 3/s) | 238 | 51% | 2.78 | 3.83 | 2.52 | 4.50 | 3.27 |

- The small painter's own cost matters little: at 0.15 / 0.5 s it moves these numbers about ±5-10%.
- Reading: with a ~3 s threshold, the big painter may take ~3.5-6 s per plate. X2's public levers are expected to reach about 5-11 s (its estimates), so the gap shrinks from ~5x to ~1-2x.
- The price: about 44% of film time (plates on screen < 3 s, mostly fast-cut action) shows small-painter plates. That needs the user's forced-choice check on short action shots.
