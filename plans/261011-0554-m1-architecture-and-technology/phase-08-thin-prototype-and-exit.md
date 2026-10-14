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
5. By eye, anchored, the picture-path checks moved here from [phase 3](./phase-03-picture-path.md): a diagonal line across the real bezel (pass: no step at the right gap value, a step seen at 5 mm off); a 0–10% near-black ramp, corrected next to uncorrected (pass: no bands); grey bars across the bezel (pass: the user can't say which side is brighter); the surround at 10 consecutive hard cuts (pass: no flicker seen).
6. Carried here when phases 1 to 4 closed (2026-10-12); the harnesses exist, each needs the screens, the user's eyes or ears, or approval:
   - Pace and seam, full screen on perf1, 24 fps at 96 and 72 Hz for 5 min each, with the tuned lag: the 72 Hz run must show the laptop even (the presenter spike, without hysteresis, alternated 9 and 11 refreshes on 57 of 455 frames). Watch the late-first-refresh check after seeks, with the render thread in the "Games" class.
   - Seam calibration with seam tuner v2 (forced choice: gap, height, lag), after brightness matching (`spikes/mixed-rate-pacing/match-brightness.py`); values go to the room model.
   - X2, the film-step test: aimed switch against the rolling blend (`--blend rolling`). The verdict decides whether the blend is used and gets its ADR (it changes what you see, ADR 0017).
   - Optional: the threshold test (is 4.2 ms visible); X3, the scan-matched laptop mode (needs approval: the laptop may go dark up to 15 s); X4, a phone photo for backlight flicker; a phone slow-motion camera check of the tuner.
   - Sound by ear: lip sync on the real speakers (the default device is a virtual enhancer that reports 0 ms latency; `--audio-offset-ms` calibrates it), and whether a 0.1 % pitch rise under the speed lock is acceptable.
   - Picture choices by eye: glow or black surround and its cut rule, fold against curve at the real seat distance, pan mode on a real camera slide.
   - Analysis during playback (with the user's OK): the worker scanning while the performer plays full screen for 10 min, 0 dropped frames. The face, head and person model calls take 35 to 156 ms per batch, longer than the 20 ms counted as short in ADR 0024; if frames drop, shrink the batches or pause the models while playing.
   - Optional: the Vulkan presenter's windowed and full-screen suite.
7. Update the roadmap status and the PRD "Current approach"; mark M1 done.

## Success Criteria

- [ ] Exact-frame and pace measurements pass.
- [ ] By-eye pan check and picture-path checks pass.
- [ ] Roadmap and PRD updated; every M1 choice has an ADR.

## Risk Assessment

- A piece fails only when combined (e.g. the color stage breaks independent flip). Signal: measurements regress against the phase spikes. Response: bisect by disabling stages; record the cause as an architecture finding.
