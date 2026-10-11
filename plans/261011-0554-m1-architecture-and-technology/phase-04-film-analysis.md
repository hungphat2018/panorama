---
phase: 4
title: "Film analysis"
status: in-progress
priority: P1
effort: "3d"
dependencies: []
---

# Phase 4: Film analysis

## Overview

Decide what computes each field of the content model reliably in anime and live action, the analysis stack (language, runtime, decode path, process boundary), and the content-model schema. The content model is made once per file from the file alone, cached, and knows nothing about the monitors ([ADR 0014](../../docs/adr/0014-architecture-plan-ahead-into-a-cue-sheet-perform-live.md)).

## Context

- Black bars and shots: [black-bar research](../reports/researcher-261010-1846-black-bar-detection-fast-start-steady-crops.md) (FFmpeg 8.1 cropdetect/scdet measured; look-ahead design; crop vocabulary; hold the last confident crop, [ADR 0010](../../docs/adr/0010-crop-changes-hold-last-confident-crop.md)).
- Slides: phase correlation + ECC measured each frame's slide to ~0.1 px on the real anime episode ([mixed-rate spike](../reports/spike-261011-1034-one-window-across-mixed-refresh-rates.md); `spikes/mixed-rate-pacing/find-pan-shots.py`, `make-real-pan-ab.py`).
- New: [film analysis content model](../reports/researcher-261011-1247-film-analysis-content-model.md).

Terms: a *shot* is one unbroken piece of footage; a *cut* is where one shot ends and the next begins (a close-up of a face cuts to a wide street view). *Black bars* are black strips stored in the video around the real picture. A *camera slide* is a pan or tilt that moves the whole picture together.

## Invariant

From the file alone: shots and cuts, black bars per shot (also when the aspect ratio changes mid-film), text in the bars, faces and attention per shot, dialogue lines, camera slides with every frame's position, and per-shot motion and brightness. Reliable enough that the planner never crops real picture, never parks a known face on a bezel, and never moves the picture inside a shot. The quick pre-scan fits a short wait; the background scan never causes a dropped frame.

## Levers

Signal processing, learned models, metadata (Dolby Vision active area, chapters, subtitle styles and `\pos` tags), the whole file being available ahead of time (look-ahead, per-shot decisions), and the planner's tolerance for detector errors (a missed cut fails safe for crops).

Hard to change: content-model schema and timeline convention, analysis process boundary and runtime, cache key. Cheap: individual detectors (swappable per field).

## Implementation Steps

0. Done 2026-10-11: [research](../reports/researcher-261011-1247-film-analysis-content-model.md) with local measurements; stack and content-model shape decided in [ADR 0024](../../docs/adr/0024-film-analysis-worker-and-content-model-shape.md). Working picks: TransNetV2 for shots (OmniShotCut as second opinion), per-shot crop union snapped to the vocabulary plus a bar-strip text guard, deepghs anime face/head/person and YuNet for live action, libass boxes and timing/alignment features for dialogue vs signs, phase correlation + ECC at 480x270 for slides with purity gating.
1. Confirm the picks with the research's spikes S1-S10 (GPU sharing under the real performer, shot bake-off, bars and text on real films, subtitles corpus, slide purity, faces at planner level, 4K HDR/Dolby Vision, packaging, pre-scan quality).
2. Build a small labelled test set with the user's help: anime (the episode at hand, OP/ED, a 4:3 pillarbox show, a scope movie, night scenes) and live action (scope film with IMAX scenes, a night-heavy film, burned-in subtitles in the bars, slow pans). Labels per shot: cut frames, active area, faces, slide yes/no.
3. Spikes per field on the test set: cut accuracy; crop look-ahead (cut-px = 0, crop changes only at cuts); face detectors (anime and live); slide detection and path accuracy; throughput with hardware decode while the performer plays (0 dropped frames).
4. Write content-model schema v0 (JSON, per shot and per frame) and the cache key.
5. Write the ADR(s): analysis stack and per-field detectors.

## Success Criteria

- [ ] Each field has a chosen method with measured accuracy on the test set.
- [ ] Throughput on Setup A: pre-scan within the short wait; full scan of a 2 h film in the background before the viewer reaches it at normal playback.
- [ ] Measured: analysis running during playback causes 0 dropped frames over 10 min.
- [ ] Content-model schema v0 and ADR(s) written.

## Risk Assessment

- Learned detectors may need runtimes that don't yet support this GPU generation on Windows. Signal: install or kernel errors on the RTX 50 series. Response: ONNX export, another execution provider, or the classical fallback.
- Test material: no feature film is on the PC. Response: the user names 4–6 files from their library; clips are cut locally, never uploaded.
- Anime breaks live-action assumptions (held drawings, flashes, layered pans). Response: measure on anime first; per-field fallbacks.
