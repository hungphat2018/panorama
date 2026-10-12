---
phase: 3
title: "Picture path"
status: completed
priority: P1
effort: "2d"
dependencies: [2, 6]
---

# Phase 3: Picture path

## Overview

Decide how the whole canvas is drawn inside the chosen engine: geometry for both the fold and the smooth-curve model, bezels as physical gaps, per-shot crop, position and size, one resample per monitor, per-monitor color, the surround in the leftover area, and subtitles inside one monitor.

## Context

- Geometry models and math: [bezels research](../reports/researcher-261010-1846-bezels-perception-and-geometry-model.md) sections 3.1.2–3.1.5 (fold = picture printed on the panels incl. gaps; smooth curve = ray from the seat to a cylinder; coplanar panels make them identical).
- Color: [setup and color research](../reports/researcher-261010-1846-setup-calibration-and-color-matching.md) section 3.1.4 (match to the weakest panel, linear light, last stage, dither; ACM owns gamut on the laptop panel).
- Surround: [leftover-area research](../reports/researcher-261010-1846-unused-canvas-area-periphery-and-outpainting.md) (one parametric edge-glow shader vs black).
- Per-shot work must sit in the final stage to stay exact under frame blending ([refinement spike](../reports/spike-261011-0950-refining-the-cue-table-during-playback.md)).
- New: [performer engine and picture path](../reports/researcher-261011-1247-performer-engine-and-picture-path.md) section 4.

## Invariant

- Each monitor pixel shows the picture spot that belongs at its physical place: a straight line crossing a bezel lines up on both sides, as if it ran on behind it ([ADR 0009](../../docs/adr/0009-bezels-placement-first-picture-continues-behind-bezel.md)).
- The picture is resampled once, from source to each monitor's own pixel grid; never distorted.
- Monitors match: on grey bars across a bezel, the user can't say which side is brighter.
- Per-shot changes land on the exact frame of a cut; the surround never flickers at cuts.
- Dialogue subtitles are crisp and inside one monitor ([ADR 0013](../../docs/adr/0013-dialogue-subtitles-stay-inside-one-screen.md)).

## Levers

- Where the geometry runs: per-monitor render with its own crop and transform (fold is affine per monitor), a per-pixel map at source resolution sized to the monitor (curve), or canvas-then-warp (two resamples, rejected unless forced).
- Who owns color: the player (relative correction, linear light, dither) vs Windows ACM vs MHC2 profiles.
- Subtitles: libass rendered per monitor at native pixels as an overlay vs blended into the video.
- Surround: computed from the picture's edges per monitor, smoothed over time in persistent shader storage.

Hard to change: the pass structure (per monitor vs one canvas), where per-shot values enter, color ownership. Cheap: filters, glow parameters, slider values.

## Implementation Steps

1. Read the engine research section 4; draft the pass structure per monitor.
2. Spike: test pattern T1 (grid, circles on each seam, diagonals) and T7 (sharpness across the seam) through the engine in fold and curve, from one room model; compare each monitor's output with an offline reference render (pixel difference) and count resamples.
3. Spike: per-monitor color stage + dither on a near-black ramp (8-bit panels); check banding and the grey-bar match with `spikes/mixed-rate-pacing/match-brightness.py`.
4. Spike: surround glow with temporal smoothing across hard cuts.
5. Subtitle placement inside one monitor, crisp at native pixels.
6. Write the ADR: picture path.

## Success Criteria

- [x] Fold and curve both render from the same room model; measured: one resample per monitor; offline reference differs by ≤ 1 code value away from edges. (S2: worst 0.534 code, 0 pixels over 1, on both GPUs and backends; [pass-structure report](../reports/spike-261012-1041-picture-path-pass-structure.md) section 3.)
- [ ] By eye, anchored: a diagonal line across the real bezel looks straight; pass: the user can't point to a step when the gap value is right, and sees one when it is 5 mm off. Moved to phase 8.
- [ ] Near-black ramp shows no banding after color correction (anchored: a 0–10% ramp next to the uncorrected one). Moved to phase 8.
- [ ] Surround shows no flicker at 10 consecutive hard cuts. Moved to phase 8.
- [x] ADR for the picture path: [ADR 0026](../../docs/adr/0026-picture-path-one-resample-per-monitor.md).

**Scope (user, 2026-10-12):** testing trimmed to what decides the pass structure: libplacebo drawing each monitor vs our own single resample, checked for correctness, sharpness and cost on both GPUs (step 2). Steps 3–5 (colour and dither, surround, subtitles) are tuning: their lasting choices (correction last over picture and surround, then dither, subtitles on top; who owns gamut under Windows ACM) go into the ADR from the research, and their checks (diagonal across the bezel, near-black ramp, surround at cuts) move to [phase 8](./phase-08-thin-prototype-and-exit.md), by eye on the real screens.

**Closed 2026-10-12.** Pass structure settled: libplacebo prepares each frame once, our own single resample per monitor carries the geometry and the per-row timing ([pass-structure report](../reports/spike-261012-1041-picture-path-pass-structure.md), [ADR 0026](../../docs/adr/0026-picture-path-one-resample-per-monitor.md)). Passes 2 to 4 from the [finish report](../reports/spike-261012-1020-picture-path-finish-stopped.md). Open risk: our resample shader is too slow on the Radeon 610M as written (Setup B); fine on the RTX.

## Risk Assessment

- High-quality scalers may not accept a per-pixel warp in one pass. Signal: the engine only offers affine transforms. Response: affine per monitor for fold (exact); for curve, one custom pass with a good filter, accept the small quality loss and measure it.
- ACM double-manages color on the laptop panel. Signal: oversaturation when the player also corrects gamut. Response: player applies only relative tweaks on ACM monitors.
- Dither after our color stage may be missing. Signal: banding in the ramp test. Response: dither inside our own last pass.
