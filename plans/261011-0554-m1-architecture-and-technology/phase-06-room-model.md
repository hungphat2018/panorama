---
phase: 6
title: "Room model"
status: in-progress
priority: P1
effort: "1d"
dependencies: []
---

# Phase 6: Room model

## Overview

Decide what the setup must capture, how each field is obtained on Windows, and the schema, so nothing assumes two side-by-side screens. Collect Setup B's facts.

## Context

- [ADR 0011](../../docs/adr/0011-setup-automatic-detection-and-manual-alignment.md): automatic detection plus manual alignment on screen, no phone photo. [ADR 0014](../../docs/adr/0014-architecture-plan-ahead-into-a-cue-sheet-perform-live.md): physical units, made once per setup.
- What Windows reports exactly and what it can't: [setup and color research](../reports/researcher-261010-1846-setup-calibration-and-color-matching.md) section 3.1.1 (EDID size can be 3–4% off).
- Pacing fields the model must hold: [pacing research](../reports/research-261011-1000-frame-pacing-judder-and-seek-swap-concepts.md) section 6.
- New: [room model and display settings](../reports/researcher-261011-1247-room-model-and-display-settings.md); read-only probe `spikes/room-probe/room-probe.py`.

## Invariant

The room model holds everything the geometry, color and timing need, in physical units and seat-centred, for any 2–3 monitor desk (sizes, orientations, depths, angles), and matches a saved setup to the current desk reliably.

## Levers

Windows detection (CCD, DXGI, DisplayMonitor, EDID/DisplayID), derived values (pixel pitch with sanity checks), on-screen alignment (gaps, stagger, depth, angles, seat), measurements later in the pipeline (panel latency, verified custom modes).

Hard to change: the schema shape (3D poses in mm, seat-centred), identity keys. Cheap: which alignment screens set which field.

## Implementation Steps

1. Read the room research; review the probe output for Setup A.
2. The user runs the probe once with Setup B connected (one command, changes nothing) and gives sizes, orientation and gaps.
3. Write room-model schema v0: monitors (pixels, rotation, physical size with sanity flags, pose, active-area edges), seams (gaps), seat, color per monitor, timing per monitor (modes with exact rates, VRR range and link, verified custom modes, panel lag and scan time from the seam tuner with the backlight levels they were tuned at, scan direction, [ADR 0020](../../docs/adr/0020-motion-crosses-a-bezel-with-no-delay.md)), graphics adapter per monitor, identity (`StableMonitorId`). One vertical convention: seat frame +y up, pixels y down, one conversion with a round-trip test.
4. Write the ADR: room model.

## Success Criteria

- [ ] Schema v0 covers Setup A and Setup B without special cases.
- [ ] Setup B facts recorded (probe output + user answers).
- [ ] ADR written.

## Risk Assessment

- Setup B is not connected now. Response: the probe needs no arguments and runs in seconds; everything else waits on it.
- EDID sizes are wrong on some panels. Response: sanity flags; alignment overrides.
