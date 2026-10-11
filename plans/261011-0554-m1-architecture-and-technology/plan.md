---
title: "M1: settle the architecture and technology"
description: "Make every hard-to-change choice for panorama on evidence (pace, motion, picture path, film analysis, plan, room, display settings), then prove the chosen pieces in a thin prototype on Setup A."
status: in-progress
priority: P1
effort: "15d agent time + user by-eye sessions"
branch: main
tags: [architecture, research, experimental]
blockedBy: []
blocks: []
created: 2026-10-11
---

# M1: settle the architecture and technology

## Overview

Roadmap milestone M1 ([roadmap](../../docs/project-roadmap.md)). Every choice that would be hard to change later is made on evidence, building on the best available ([ADR 0019](../../docs/adr/0019-settle-architecture-and-technology-first.md)). Code written here may be thrown away.

Each phase answers one roadmap question the same way: state the invariant, map every lever, check the state of the art fresh, settle with the cheapest test (spike, measurement, anchored by-eye check), record each choice as an ADR.

## Keystone

Pace decides the performer. One window spanning the screens is paced by the main display only, and the two screens switch frames a few ms apart ([mixed-rate spike](../reports/spike-261011-1034-one-window-across-mixed-refresh-rates.md)). If the fix needs per-monitor presentation, stock mpv can't be the performer, and the picture path and cue-sheet transport follow from the new engine. So 01 → 02 → 03 and 05 run in order; 04, 06 and 07 run in parallel with them.

## Phases

| # | Phase | Status |
|---|-------|--------|
| 1 | [Pace and motion](./phase-01-pace-and-motion.md) | In progress |
| 2 | [Performer engine](./phase-02-performer-engine.md) | In progress |
| 3 | [Picture path](./phase-03-picture-path.md) | Pending |
| 4 | [Film analysis](./phase-04-film-analysis.md) | In progress |
| 5 | [Plan and cue sheet](./phase-05-plan-and-cue-sheet.md) | Pending |
| 6 | [Room model](./phase-06-room-model.md) | In progress |
| 7 | [Display settings](./phase-07-display-settings.md) | In progress |
| 8 | [Thin prototype and exit](./phase-08-thin-prototype-and-exit.md) | Pending |

```
01 Pace ──► 02 Engine ──► 03 Picture path ──┐
                     └──► 05 Plan ──────────┼──► 08 Prototype + exit
04 Film analysis ───────────────────────────┤
06 Room ──► (feeds 01, 03) ─────────────────┤
07 Display settings ────────────────────────┘
```

## Design and decisions

- [M1 architecture design](../reports/design-261011-1247-m1-architecture.md): components, contracts, decisions so far.
- ADRs from this plan: [0020](../../docs/adr/0020-motion-crosses-a-bezel-with-no-delay.md) zero delay across a bezel (user), [0021](../../docs/adr/0021-one-window-per-monitor-one-film-clock.md) one window per monitor, [0022](../../docs/adr/0022-own-performer-on-libplacebo-and-ffmpeg.md) own performer, [0023](../../docs/adr/0023-display-changes-held-by-the-player.md) display changes held by the player, [0024](../../docs/adr/0024-film-analysis-worker-and-content-model-shape.md) film analysis worker and content-model shape.
- Review: [red team](../reports/red-team-261011-1247-m1-architecture.md), verdict go with changes (folded into the ADRs, design and phases).
- Spikes: [one window per monitor](../reports/spike-261011-1247-one-window-per-monitor.md) (phase 01), [performer toolchain P0](../reports/spike-261011-1247-performer-toolchain-p0.md) (phase 02).

## Research for this plan (2026-10-11)

- [Per-monitor presentation and sync](../reports/researcher-261011-1247-per-monitor-presentation-and-sync.md) (phase 01)
- [Performer engine and picture path](../reports/researcher-261011-1247-performer-engine-and-picture-path.md) (phases 02, 03)
- [Film analysis content model](../reports/researcher-261011-1247-film-analysis-content-model.md) (phase 04)
- [Room model and display settings](../reports/researcher-261011-1247-room-model-and-display-settings.md) (phases 06, 07)
- [Zero delay across scan speeds](../reports/research-261011-1523-zero-delay-across-scan-speeds.md) (phase 01: rolling blend, scan-matched mode, blank-time correction)

## Success criteria (roadmap "Done when")

- [ ] Each M1 question answered in a report; every choice it makes is an ADR.
- [ ] Thin prototype on Setup A plays a real film from a hand-written cue sheet through the chosen pieces.
- [ ] Measured: changes land on their exact frames; every frame stays up equally long on the screens carrying the picture.
- [ ] By eye: a slow pan across the bezel shows no flicker and no stumble.

## Needs the user

- Approval before display-mode changes (custom 96/72 Hz modes) and full-screen trials on both screens.
- Anchored by-eye checks (seam offset threshold, pans, line continuity, grey bars).
- Setup B facts: run the room probe once with Setup B connected; sizes, orientation, gaps.
