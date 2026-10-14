---
title: AI surround phase 4 structure path overnight
date: 2026-10-14
summary: "Built the camera model, step planner, patch path (schema 2) and X1c; screens found a libplacebo double-binding bug, camera drift on 3D moves and a 4x wrong-frame rise at 240 Hz"
---

# AI surround phase 4 structure path overnight

## What happened

Phase 4 of the AI surround live path ran overnight while another session (phase 3) owned the GPU until 02:56.
- **The X1 failure reframed it.** Every carried version was spotted, so the night built only what any replan keeps: the camera model, the planner with the cadence as a parameter, the patch path, identity refs. It also built X1c, so the user picks the cadence.
- **The screens found a libplacebo rule.** One texture bound to two descriptors in a pass is refused (`db.object != desc_bindings[j].object`). The phase-4 fill bound one 1x1 stand-in as both accumulate and overlay when a showing had no page. Each refusal logged a 25 ms backtrace, so the 240 Hz screen went to 13 wrong / 115 missed per min. One stand-in per role fixed it.
- **Wrong frames are still 4x phase 2's** (6.0 against 1.5 per min at 240 Hz), and the misses now land on film-frame changes. The patch path off gives 3.1. The rest is the heavier worker. Single 10-min runs are too noisy to split it further.
- **The disk filled at 03:56.** Uncompressed patch pages of superseded versions took 9.8 GB, and a screen run lost its logs. Version bumps orphan whole cache trees; a cleanup at worker start is still missing.
- **The camera chain drifts on 3D moves:** 30-45 px at the picture's corners after 21 frames. Mosaics tore. The fix went through three versions in one night:
  - 8-texel cells: a checkerboard;
  - a per-shot gate: dropped 28 % of shots' sides;
  - disagreement regions (σ 12, 25 %): kept the static background and dropped smoke and characters, judged by eye.
- **Identity:** DBSCAN on CCIP chained 722 of 744 tracks. Complete linkage at 0.14 is mostly clean, but CCIP cannot flag its own mixes: mean_diff was tightest on a mixed cluster.

## Lessons

- Check the player's stderr after every screen run. The validation failures were only visible there.
- Measure by eye before believing a gate threshold. Two "reasonable" versions were wrong on real shots.
- Keep version-keyed caches small and prunable; the shared disk is nearly full.
- A shared repo with a parallel session works when every handover is explicit: GPU windows, "worker code OK" before their e2e, file ownership.

> Historical work record — not durable authority. Prefer docs/specs/ADRs for current decisions.
