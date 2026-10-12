---
phase: 4
title: "Film analysis"
status: completed
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
1. (Moved to a later milestone, see Scope.) Confirm the picks with the research's spikes S1-S10 (GPU sharing under the real performer, shot bake-off, bars and text on real films, subtitles corpus, slide purity, faces at planner level, 4K HDR/Dolby Vision, packaging, pre-scan quality).
2. (Moved to a later milestone.) Build a small labelled test set with the user's help: anime (the episode at hand, OP/ED, a 4:3 pillarbox show, a scope movie, night scenes) and live action (scope film with IMAX scenes, a night-heavy film, burned-in subtitles in the bars, slow pans). Labels per shot: cut frames, active area, faces, slide yes/no.
3. (Moved to a later milestone; the playback-load part moved to [phase 8](./phase-08-thin-prototype-and-exit.md).) Spikes per field on the test set: cut accuracy; crop look-ahead (cut-px = 0, crop changes only at cuts); face detectors (anime and live); slide detection and path accuracy; throughput with hardware decode while the performer plays (0 dropped frames).
4. Done 2026-10-12: content-model schema v0 (JSON Schemas in `spikes/film-analysis/schema/`), the timeline checked against the performer's frame times, the cache key, and a thin worker that fills every field: [report](../reports/spike-261012-1103-content-model-schema-v0-and-worker.md).
5. Done 2026-10-12: [ADR 0027](../../docs/adr/0027-content-model-schema-v0-timeline-and-cache-key.md) (schema v0, timeline, cache key; what stays swappable) on top of [ADR 0024](../../docs/adr/0024-film-analysis-worker-and-content-model-shape.md). Per-field detector picks stay as ADR 0024 lists them until the later accuracy work.

## Success Criteria

- [ ] Each field has a chosen method with measured accuracy on the test set. Moved to a later milestone (user, 2026-10-12): methods are chosen and swappable, the accuracy rests on the research's measurements.
- [x] Throughput on Setup A: pre-scan within the short wait; full scan of a 2 h film in the background before the viewer reaches it at normal playback. Measured on the 24-minute episode: pre-scan 2.8 s, first scanned segment 8.4 s after launch, full run 164 s (8.7 times real time); about 14 minutes for 2 h [derived].
- [ ] Measured: analysis running during playback causes 0 dropped frames over 10 min. Moved to [phase 8](./phase-08-thin-prototype-and-exit.md): needs the user's OK and both screens. Watch the face/head/person batches (35 to 156 ms per call).
- [x] Content-model schema v0 and ADR(s) written: schema files, timeline checked against the performer (47 of 49 openable files bit for bit; real performer within 3.3e-10 s), cache key tested; [ADR 0027](../../docs/adr/0027-content-model-schema-v0-timeline-and-cache-key.md).

**Scope (user, 2026-10-12):** testing trimmed to the hard-to-change parts: schema v0, the timeline checked against the performer's own frame times, the cache key, a thin worker that fills the schema, and the playback check (S1, one 10-minute run with the user's OK). Detectors stay swappable per field (ADR 0024), so steps 2–3 (labelled test set, accuracy per field) and spikes S2–S10 move to a later milestone, on the user's own films; until then the picks rest on the research's measurements on the episode. Throughput rests on the research's numbers (about 1 min per episode, 5 min per 2 h film) plus the worker's episode run.

## Risk Assessment

- Learned detectors may need runtimes that don't yet support this GPU generation on Windows. Signal: install or kernel errors on the RTX 50 series. Response: ONNX export, another execution provider, or the classical fallback.
- Test material: no feature film is on the PC. Response: the user names 4–6 files from their library; clips are cut locally, never uploaded.
- Anime breaks live-action assumptions (held drawings, flashes, layered pans). Response: measure on anime first; per-field fallbacks.
