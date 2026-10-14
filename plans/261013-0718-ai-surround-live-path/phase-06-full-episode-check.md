---
phase: 6
title: "Full-episode check and verdict"
status: pending
priority: P1
effort: "1 d agent + ~1 h user by-eye"
dependencies: [2, 3, 4, 5]
---

# Phase 6: Full-episode check and verdict

## Overview

The one end-of-build test (user rule: test once at the end). Both episodes play from a cold start on the two screens with the live high preset. Logs prove 1x and on-time playback. The user's forced choice proves no visible loss. Results go into a report, and what-you-see changes are proposed as ADRs for the user's OK.

## Requirements

- Functional:
  - Cold start (empty cache) on JJK S03E12 and Judas S01E02, play at once, three seeks per episode.
  - Logs: tier per side over film time; GPU-s per film-s for the worker; catch-up time; zones-ready share; wrong-refresh frames per monitor; VRAM and RAM peaks.
- Non-functional: rests between episodes and between by-eye blocks; the film stays local.

## Architecture

- Uses only the phase 2-5 pieces and their logs. No new runtime code; a log analyser and the blind-test builders only.
- **By-eye sets:**
  1. **Uncut moments per scene type** (still, camera slide, action, effects, two each per episode). Live high preset against today's best offline recipe (Qwen plate on still shots; Qwen plate + A14B keyed + wraps on moving shots; the recovered fight demo is one of them). Forced choice: "which sides look more like the same show filmed wider and move with the picture?" 20 trials per scene type.
  2. **Hidden-sides cut** (X1 geometry). Live against truth: reported as the margin, not a gate.
  3. **The two A14B defects** (the user's 2026-10-13 verdict): on the same moments, ask "do the sides flicker?" and "can you see the seam line?". Pass: no on both, live and in free-look.

## Related code files

- Create: `spikes/ai-surround/live-episode-report.py` (log analysis + tables + plots).
- Reuse: `make-blind-test.py`, `true-sides-test.py` (trial playlists, scoring), the player logs (E1 analyser).

## Implementation steps

1. Clear the cache, then run JJK end to end with three seeks. Rest. Run Judas the same way.
2. Analyse the logs against acceptance criteria 1-3, 5 and 8.
3. Build the by-eye sets and run them with the user (forced choice, sham and positive controls as in X1).
4. Write the report: `plans/reports/spike-261013-<hhmm>-ai-surround-live-path-verdict.md` (numbers, the user's verdicts verbatim, failures and their cause).
5. Propose ADRs for what-you-see choices (e.g. the tier ladder with upgrades only at cuts, trail entries, the 120 Hz fallback if used). The user accepts or rejects each.
6. Update the synthesis and the roadmap only where behaviour or architecture changed (documentation rule).

## Success criteria

- [ ] Plan acceptance criteria 1-8 checked, each with its evidence (log number or the user's verdict).
- [ ] Failures traced to a role (analyse, paint, refine, render) or to GPU sharing, with the next lever named.
- [ ] Report written; ADR proposals presented to the user.

## Risk assessment

- **Action fails 1x while comedy passes.** Expected risk (kongming math puts action at the budget edge). Response: report the shortfall per lever (painter s/plate, reuse, refiner cadence). Then a lower tier for short action shots (by-eye gated) or a replan of the painter. No silent quality cut.
- **A by-eye result is borderline (13-14/20).** Response: 20 more trials on another day.
- **Thermal throttling over 24 min** lowers throughput. Signal: clock events in the logs. Response: account for it in the GPU share; rests between runs.
