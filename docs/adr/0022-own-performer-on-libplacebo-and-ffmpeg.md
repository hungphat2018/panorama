# 0022. Own performer on libplacebo and FFmpeg

- **Status:** Accepted
- **Date:** 2026-10-11
- **Refines:** [0012](0012-concept-demo-starting-scope.md), [0014](0014-architecture-plan-ahead-into-a-cue-sheet-perform-live.md)

## Context

[ADR 0021](0021-one-window-per-monitor-one-film-clock.md) needs a performer that owns one window, one swapchain and the present call per monitor, and knows each refresh's time. The engine research read mpv and libplacebo at their 2026-10-10 master commits ([engine research](../../plans/reports/researcher-261011-1247-performer-engine-and-picture-path.md)):

- mpv's gpu-next video output is single-instance: one window, one swapchain, timing driven by one display. libmpv's render API is OpenGL or software only, on the older renderer. User shaders get the frame's timestamp, not the refresh time.
- libplacebo renders each monitor's part of the picture from the source in one resample (per-monitor source and target crops), mixes frames for any target time (`pl_queue`), and does colour management and dithering. Its gaps are workable: no D3D11 hardware-decode import, no libass glue, a fixed `Present(1,0)` on D3D11.
- GStreamer has no refresh-aware pacing and no shader hook; VLC 4 is not stable.

## Decision

- The performer is our own program: FFmpeg for reading and decoding files, libplacebo for drawing, libass for subtitles, WASAPI for sound. Our code owns the windows, the swapchains and every present.
- Written in C (C11): libplacebo, its player demo and FFmpeg's examples are all C, so reference code carries over with the least translation. C++ with clang or Rust with generated bindings would also work; neither buys enough to pay for the translation. Planner, room and analysis code may use other languages.
- Guardrails for C: sanitizers (address and undefined behaviour) in test runs, warnings as errors, each thread owning its data and talking through queues, fuzzing of file reading and cue parsing.
- It runs as its own process, talking to planner, setup and analysis through files and IPC, with the libraries dynamically linked, so the license stays open (ADR 0014).
- Fallback if the audio, subtitle and seeking work overruns: mpv's core with a new video output that is the same presenter code.
- Stock mpv stays as the baseline, as the plain stretched picture in comparisons, and as a per-file fallback for files the performer doesn't support yet.

## Consequences

- Spikes gate the concept demo on this engine: build and playback on this PC, the two-window presenter on libplacebo (on both the D3D11 and the Vulkan backend, plus a monitor on the other graphics adapter), pan mode, exact cues with the seek latch. Until they pass, stock mpv is the demo fallback.
- The real risk is the long tail of files: interlaced video, MKV ordered chapters, image subtitles (PGS), audio passthrough, monitor hot-plug. A supported-file matrix is kept from the first spike on; the fallback to mpv's core is judged against it.
- Toolchain: MSYS2 UCRT64, self-contained under the user's profile; libplacebo master builds in seconds; FFmpeg 9.0.2 rebuilt as LGPL keeps D3D11VA, D3D12VA and Vulkan decoding, and libplacebo's FFmpeg helpers work with it. The first spike played the anime test episode on D3D11 with our own `Present`: 0 late, 0 skipped frames ([P0 spike](../../plans/reports/spike-261011-1247-performer-toolchain-p0.md)). Vulkan presenting was jittery there, so the performer starts on D3D11.
- The cue sheet reaches the performer in process, so the per-shot latch (no shot changes on screen) lives in its render loop.
- More code than stock mpv: the engine research estimates about 2.6k lines for the M1 prototype and 7.6k for the full performer.
