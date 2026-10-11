---
phase: 8
title: "Thin prototype and exit"
status: pending
priority: P1
effort: "4d"
dependencies: [1, 2, 3, 5, 6, 7]
---

# Phase 8: Thin prototype and exit

## Overview

Prove the chosen pieces together: a thin prototype on Setup A, rough quality allowed, plays a real film from a hand-written cue sheet. This is the M1 exit test from the [roadmap](../../docs/project-roadmap.md).

## Requirements

- Functional: the chosen performer (phase 02) with the picture path (03), a hand-written cue sheet (05), the Setup A room model (06) and the display manager (07). Film analysis (04) is not required: the cue sheet is written by hand.
- Non-functional: throwaway code allowed; measurements must be reproducible from a script.

## Implementation Steps

1. Assemble the pieces; play the anime test episode (and one live-action file) with a cue sheet that changes crop, position and size at chosen cuts and marks one camera slide.
2. Measure exact frames: barcode or frame-index capture harness from the earlier spikes, adapted to the chosen presentation. Pass: every cue lands on its frame, 0 wrong frames.
3. Measure pace: per-monitor flip timing logs. Pass: every frame stays up equally long on the screens carrying the picture; seam offset at the aimed row within the bound of the presentation in use (half the faster monitor's refresh on fixed rates, about zero with variable refresh), with the tuned lag applied.
4. By eye, anchored: a slow pan across the bezel, and something moving across it on its own. Pass: no flicker, no stumble, no felt delay ([ADR 0020](../../docs/adr/0020-motion-crosses-a-bezel-with-no-delay.md)).
5. Update the roadmap status and the PRD "Current approach"; mark M1 done.

## Success Criteria

- [ ] Exact-frame and pace measurements pass.
- [ ] By-eye pan check passes.
- [ ] Roadmap and PRD updated; every M1 choice has an ADR.

## Risk Assessment

- A piece fails only when combined (e.g. the color stage breaks independent flip). Signal: measurements regress against the phase spikes. Response: bisect by disabling stages; record the cause as an architecture finding.
