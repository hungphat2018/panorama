---
phase: 5
title: "Plan and cue sheet"
status: pending
priority: P1
effort: "1d"
dependencies: [2]
---

# Phase 5: Plan and cue sheet

## Overview

Define what the cue sheet holds and how a refined plan reaches the performer without changing a shot on screen, even when the viewer jumps around.

## Context

- [ADR 0014](../../docs/adr/0014-architecture-plan-ahead-into-a-cue-sheet-perform-live.md): the cue sheet is plain data, per shot (crop, position, size, subtitle screen, surround) and per monitor (geometry, color).
- [Refinement spike](../reports/spike-261011-0950-refining-the-cue-table-during-playback.md): stock mpv refines by swapping shader files; 1 s commit horizon; confirm via `vo-passes`.
- [Pacing research](../reports/research-261011-1000-frame-pacing-judder-and-seek-swap-concepts.md) section 5: the seek–swap race and the takeover latch; "presentation changes only at breaks".

## Invariant

Presentation changes only at a break: a cut, a seek, a pause or file start. No shot changes its look while on screen, whatever the timing of refinements and seeks.

## Levers

- Where the latch lives: in the render loop (renderer knows what is on screen) vs a planner-side gate on seeks.
- Transport: in-process call (own engine) vs files + IPC + shader swap (stock mpv).
- Versioning: monotonic table versions, commit horizon, shot keyed by its start time on the rebased timeline, boundaries halfway between frames.

Hard to change: the schema's shape and timeline convention; the break rule and where it is enforced. Cheap: field defaults, file format details.

## Implementation Steps

1. Write cue-sheet schema v0 (JSON): timeline convention (seconds from the first video frame; define edit lists, negative timestamps, MKV ordered chapters per segment); per shot: start, crop, picture placement in surface coordinates (u = distance along the surface, v = height, mm, so cues survive a fold/curve switch), monitors carrying the picture, subtitle monitor, surround settings, time treatment (slide path in source pixels, judder treatment); per film: display modes, master, aim row per seam; per monitor: geometry and color references; version. Rule: a new version never moves a shot boundary before the commit horizon.
2. Choose the latch for the chosen engine: own engine, one per-shot latch in the render loop; stock mpv, the takeover latch (needs the adversarial-seek spike E4).
3. Adversarial test: seeks from −50 to +150 ms around each refinement, scrubbing at 5 seeks/s, seeks into changed shots. Pass: 0 shots changed on screen, 0 wrong frames.
4. Hand-write the cue sheet for the phase 08 prototype.
5. Write the ADR: cue sheet and the break rule.

## Success Criteria

- [ ] Schema v0 written and validated by the prototype reading it.
- [ ] Adversarial seek test passes with the chosen latch.
- [ ] ADR written.

## Risk Assessment

- Schema churn once the planner exists. Response: version field + loader that rejects unknown versions; keep it plain data so it can be read and written by hand.
- Long single takes hold a cautious value for minutes. Response: the planner may mark masked moments (fade, flash, whip pan) as allowed change points later.
