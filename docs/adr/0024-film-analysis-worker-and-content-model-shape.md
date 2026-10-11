# 0024. Film analysis: a separate worker, raw signals and derived tables

- **Status:** Accepted
- **Date:** 2026-10-11
- **Refines:** [0010](0010-crop-changes-hold-last-confident-crop.md), [0014](0014-architecture-plan-ahead-into-a-cue-sheet-perform-live.md)

## Context

The content model (ADR 0014) is made from the file alone and must never slow playback. The [film analysis research](../../plans/reports/researcher-261011-1247-film-analysis-content-model.md) measured on Setup A with the real anime episode:

- Hardware decode to small frames runs at about 2,600 fps without using the GPU's shader cores; the shot model (TransNetV2) at about 5,800 fps; the whole analysis takes about 1 minute per 24-minute episode and about 5 minutes per 2-hour film (derived), with a first usable result after about 5 seconds.
- Short GPU jobs (up to 8 items at a time) and hardware decode left a 240 Hz player with no dropped or late frames; larger GPU jobs did not.
- On real anime with exact synthetic bars, deciding each shot's crop from all its frames and snapping it to the file's few real crop shapes cut no picture in 23,016 frames, and every crop change fell on a cut; frame-by-frame detection cut picture in 9.8% of frames.

## Decision

- **Process:** analysis runs in its own worker process at below-normal priority, never inside the performer. It talks through files and a small control channel (where the playhead is, seeks). If it crashes, the planner keeps the cautious cue sheet.
- **Stack:** a Python worker; FFmpeg with hardware decode as a subprocess (never assuming CUDA, with a software fallback); ONNX Runtime with the CUDA provider for models (not DirectML, which Microsoft keeps only on sustained engineering); OpenCV for motion. Models on the hot path are ONNX files.
- **Time and space:** everything is keyed by each frame's own timestamp (with a frame table), never by frame numbers alone; positions are in the source's stored pixels, independent of any crop.
- **Two layers:** raw per-frame signals (about 40 bytes per frame, about 7 MB per film) and derived per-shot tables built from them. A policy change re-derives tables in seconds; only a model change re-decodes. Each table carries its own analyzer version; work is split into keyframe-aligned segments so a scan can start at any seek.
- **File identity:** a hash of the size and three 1 MiB samples at once, the full hash later.
- **Crops (ADR 0010):** per shot, the union of the shot's evidence snapped to the file's crop vocabulary, plus a guard on the bar strips for burned-in text; the "hold the last confident crop" rule stays for shots with no usable evidence.

## Consequences

- Detectors stay swappable behind fixed outputs: shots (cut or transition, with span and both scores), crops, text in bars, subjects (face, head, person, text boxes and a keep-clear map), subtitle events with a dialogue/sign class, slide paths as per-frame positions with confidence, motion and brightness.
- Which detectors ship is settled by the labelled-test spikes in the research (shot bake-off, real scope and IMAX films, faces at planner level, subtitles corpus, slides purity), not here. Licences need checking per model (some detector architectures are AGPL, some face models non-commercial).
- Dialogue lines are wide (median 41% of the frame) and nearly all centred lines would cross a seam at the middle, so ADR 0013 applies to almost every line; how lines are fitted into one screen is a what-you-see question for the user.
- GPU sharing must be re-checked with the real performer.
