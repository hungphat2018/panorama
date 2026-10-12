---
phase: 2
title: "Performer engine"
status: in-progress
priority: P1
effort: "2d"
dependencies: [1]
---

# Phase 2: Performer engine

## Overview

Choose what the performer is built on: stock mpv with a generated shader (today's demo path), an mpv fork, an own player on libplacebo + FFmpeg, or something else, plus the language and the license consequences. The performer plays the file and applies the cue sheet on the exact frame; it makes no decisions ([ADR 0014](../../docs/adr/0014-architecture-plan-ahead-into-a-cue-sheet-perform-live.md)).

## Context

- Proven in stock mpv: exact-frame cues via the `PTS` shader parameter and a table texture in the OUTPUT hook; table refinement by shader swap with no visible effect ([exact-frame spike](../reports/spike-261010-2345-exact-frame-cue-switching-in-stock-mpv.md), [refinement spike](../reports/spike-261011-0950-refining-the-cue-table-during-playback.md)).
- Limits found: one spanning window, paced by one display; shaders get the frame's timestamp but not the refresh time ([pacing research](../reports/research-261011-1000-frame-pacing-judder-and-seek-swap-concepts.md)).
- New: [performer engine and picture path](../reports/researcher-261011-1247-performer-engine-and-picture-path.md).

## Invariant

The performer plays any local file (hardware decode, audio in sync, subtitles, seeking), presents the way phase 01 requires on every monitor, applies every cue on its exact frame, and never stutters. Our planner, room and analysis code stay free of GPL (license choice open, ADR 0014).

## Requirements (engine must provide)

| # | Requirement | From |
|---|---|---|
| R1 | One swapchain per monitor, each paced by its monitor, with control over when each presents | phase 01 |
| R2 | Each refresh's time relative to film frames (pan mode) | ADR 0016 |
| R3 | Per-frame cues exact, including under frame blending | spikes |
| R4 | Table refinement without changing a shot on screen, also under seeks | phase 05 |
| R5 | One resample per monitor with the geometry warp in it; per-monitor color last, then dither; surround; crisp subtitles inside one monitor | phase 03 |
| R6 | Hardware decode, audio + A/V sync, libass subtitles, seeking, rate locking | basics |
| R7 | Process or library boundary that keeps our code's license open | ADR 0014 |
| R8 | Per-row frame weights in one pass (rolling blend) and per-row slide offsets inside the one resample | [zero-delay research](../reports/research-261011-1523-zero-delay-across-scan-speeds.md) §5.5, design §5 |
| R9 | Present timing per monitor: each refresh's top-row time known, and control of which refresh a present lands on | [ADR 0025](../../docs/adr/0025-refresh-times-come-from-the-scan-line.md) |
| R10 | One rendering device per graphics adapter; monitors on two adapters; decode shared | ADR 0021, Setup B |

## Options

- A. Stock mpv + generated shader + IPC.
- B. mpv fork with a multi-window video output.
- C. Own player on libplacebo + FFmpeg + libass + audio (C, C++ or Rust).
- D. GStreamer-based player with custom rendering.
- E. Hybrid (mpv for decode/audio/subtitles, our own presenter).

Hard to change: the engine, the language, the process boundary, license exposure. Cheap: decode API, scaler settings, which audio library.

## Implementation Steps

1. Done 2026-10-11: [requirement matrix](../reports/researcher-261011-1900-performer-requirement-matrix.md), R1–R10 × A, B, B2, C, D, E plus E2 (libmpv with the unmerged D3D11 render API, mpv#17764) and F (VLC `wall`), every cell cited. Ranking holds: C, then B2 as fallback. R8–R10 need our own code (or the same libplacebo patch) in both C and B2, and rule out A, D, E, E2 and F. FFmpeg 9.0.2 drops MKV ordered chapters and linked segments (`matroskadec.c`), so the per-file mpv fallback is required until our performer has its own timeline layer. Weak cells: B2 is derived only (P7 not run); C's R1/R9 wait for the full-screen P1 run; R10 can't be tested on Setup A (the Radeon 610M has no outputs).
2. Decided: [ADR 0022](../../docs/adr/0022-own-performer-on-libplacebo-and-ffmpeg.md) (own C11 performer; mpv core + own video output as fallback; stock mpv as baseline and per-file fallback).
3. **P0** (done 2026-10-11, [report](../reports/spike-261011-1247-performer-toolchain-p0.md)): MSYS2 UCRT64 under the user's profile; libplacebo master and plplay build; LGPL FFmpeg 9.0.2 built from source; `perf0` (plplay-derived, C11) plays on D3D11 (software and D3D11VA, our own `Present`: 0 late, 0 skipped) and Vulkan (jittery, unexplained hitches). Supported-file smoke test done 2026-10-11 ([file matrix](../reports/spike-261011-1955-supported-file-matrix.md), 53 cases on perf0 and stock mpv, `spikes/file-matrix/`): no library-level blocker; deinterlacing, rotation and SD colour fixed in perf0 with 81 lines behind switches. Close before the concept demo: text subtitles, software-decode fallback (D3D11VA refuses Hi10P, 4:2:2, 4:4:4, MPEG-4 ASP), a deliberate colour policy for SD and HDR-to-SDR (what you see: the user's call), raw-stream timestamps. Stays on the mpv per-file fallback until built: ordered chapters, PGS, Dolby Vision 5/8.1, IVTC, audio passthrough, HDR output.
4. **P1**: the two-window presenter on libplacebo, on both D3D11 and Vulkan, plus one run with a monitor on the other graphics adapter; same measurements as the raw spike (overlay state, refreshes per frame, seam offset).
5. **P2–P6** in the order of the engine research section 6: pan mode per refresh (row-aware), cues and seek latch, picture path quality and cost, audio sync (audio follows the master display), subtitles.
6. Keep the supported-file matrix; judge the fallback against it.

## Success Criteria

- [x] Requirement × option matrix with evidence for every cell (source line, doc, or measurement): [matrix](../reports/researcher-261011-1900-performer-requirement-matrix.md); the cells resting on derivation or windowed runs are listed there.
- [ ] ADR for the performer engine, language and boundary.
- [ ] If the engine is new: a skeleton plays the anime test episode on both monitors for 5 min with 0 dropped frames, audio in sync, and seeks work.

## Risk Assessment

- An own player is more code (A/V sync, seeking, hardware decode, subtitles). Signal: skeleton effort exceeds a few days. Response: reuse libplacebo's player demo and FFmpeg examples; keep stock mpv as the fallback for single-window layouts.
- An mpv fork is GPL by default and rebases with mpv. Response: keep our code in separate processes or files; decide license after the demo.
- Library versions move (libplacebo tags are sparse). Response: pin commits; record them in the ADR.
