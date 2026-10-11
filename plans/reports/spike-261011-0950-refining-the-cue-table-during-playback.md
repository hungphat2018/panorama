---
title: Spike — refining the cue table during playback, and the performer's other weak spots
date: 2026-10-11
type: spike
answers: ADR 0014 consequence "Refining the cue sheet while playing means reloading the shader, which is untested"; open questions 1–2 of the previous spike
harness: spikes/exact-frame-cue/ (swap-planner.py, run-swap-trials.py, analyze-swap-capture.py, measure-swap-memory.py)
results: spikes/exact-frame-cue/results-2026-10-11/
---

# Spike: refining the cue table during playback

Terms: a *shot* is one unbroken piece of footage; a *cut* is where one shot ends and the next starts (a close-up of a face cuts to a wide street view). The *cue table* is the per-shot list of settings inside the generated shader. *Interpolation* is mpv blending neighbouring frames for smoother motion on screens whose refresh rate is not a whole multiple of the film's frame rate.

## Answer

**Yes.** The planner can replace the cue table while the film plays, with no visible effect, if every new table:

1. keeps the shader code byte-identical (a fixed-size table; only the texture data changes), so mpv compiles nothing;
2. is a new file (mpv ignores a rewritten file under the same name);
3. changes only shots that start at least 1 s after the playhead, so the moment the swap lands never matters.

Measured on Setup A in 8 trials: 91 swaps, 0 wrong frames, 0 shots changed while on screen, 0 dropped or late frames from good swaps; 0 compiles in the 77 swaps with identical code. A swap is drawn 0–2 frames after the request (same frame in the demo's sync mode). mpv reports back which table it drew, 8–15 ms after the request in the demo's sync mode.

This removes the trade-off between a fast start and steady crops: play at once on the cautious cue sheet from the quick pre-scan, swap in refined tables as the background scan finishes (ADR 0014).

Weak spots found and how the design now handles them:

- **Interpolation delays per-shot work done before the final shader stage**: the first frame of a new shot showed the previous shot's cue on 22 of 33 cuts. Work in the final stage (OUTPUT hook) stayed exact in every trial, with or without interpolation. → All per-shot work goes in the OUTPUT hook, inside the monitor mapping the demo shader already does there; no extra resampling.
- **A broken table file plays on silently with no shader.** → Each swap is confirmed (mpv reports the drawn table version); an error or no confirmation reverts. Still one unmapped frame per failure, so files are written atomically and read back before the swap.
- **mpv keeps every table version until playback ends** (~98 bytes RAM + ~65 bytes GPU memory per table entry, per swap). → Size the table to the film, batch refinements into a few dozen swaps per film.

## How it works

1. **Fixed-size table per film** (shots plus headroom). Unused entries start at 1e30, the last entry holds the version. The GLSL is the same in every version, so libplacebo reuses the compiled pass: it keys compiled passes by a hash of the GLSL (`dispatch.c`), and the table is texture data, not code.
2. **New file per version**, written to a temporary name, renamed, read back. mpv parses a shader file once per path and never re-reads it (`vo_gpu_next.c` `load_hook`).
3. **Commit horizon**: version k may change only shots starting at or after playhead + 1 s (playhead read over IPC just before). Swaps land within 2 frames, so 1 s is ample.
4. **Swap** via mpv's JSON IPC: `set_property glsl-shaders [path]`. Any option change resets mpv's render state (`update_render_options` → `want_reset`); measured harmless (no hitch in any trial).
5. **Confirm**: the OUTPUT pass description ends with "(cue table vN)". The description is not part of the GLSL (no recompile, verified), and mpv's `vo-passes` property reports the description of every pass it drew. An mpv error or no confirmation within 1 s → swap back to the last confirmed file (already parsed, so instant).
6. **Batch** refinements: every swap costs memory (below).

## Architecture variants tried

| Variant | Result |
|---|---|
| Fixed-size table, same code, new file per version (**chosen**) | 0 compiles in 77 swaps, 0 wrong frames |
| Naive generator: version compiled into the code | Compiles on every swap, ~19 ms (two passes, ~3 + ~16 ms). No stutter here only because this test shader is tiny and 24 fps leaves 41.7 ms per frame |
| Rewrite the same file, load the same path | Ignored: mpv keeps the table it parsed first (seen in the capture and via `vo-passes`) |
| Stream the next cues as shader parameters (`glsl-shader-opts`) instead of swapping | Not built. From source: every parameter change causes the same render reset as a swap, needs a script that follows the playhead, and a seek outruns the streamed window. No gain |
| Scan the whole film before playing | No longer needed (avoids ~5 min wait for a 2 h film) |
| Per-shot work before the final stage (MAIN) | Exact without interpolation (0 late in 212 cuts); with interpolation 22 of 33 cuts one frame late |
| Per-shot work in the final stage (OUTPUT) (**chosen**) | Exact in all trials, including interpolation |

## Results

Laptop panel 2560x1600 @ 240 Hz, fullscreen, mpv 0.41.0 (libplacebo 7.358), `--vo=gpu-next --gpu-api=d3d11 --hwdec=d3d11va`. Synthetic 75 s video, 33 shots, frame index barcoded in every frame. A planner stand-in swaps a new table every 5 s; each version visibly changes all shots after its commit time. The shader paints, on every presented image: the cue it applied (OUTPUT stage), the table version, and the cue a MAIN-stage pass baked into the frame. Windows Graphics Capture records every present.

| Trial (24 fps unless noted) | Swaps | Wrong frames | Changed on screen | Compiles | Landing (frames) | Hitches | Drops / late | MAIN-stage cue late at cuts |
|---|---|---|---|---|---|---|---|---|
| default sync | 14 | 0 | 0 | 0 | 1–2 | 0 | 0 / 0 | 0 of 32 |
| display-resample | 14 | 0 | 0 | 0 | 0–1 | 0 | 0 / 0 | 0 of 32 |
| 25 fps + interpolation | 14 | 0 | 0 | 0 | 0–1 | 0 | 0 / 0 | **22 of 33** |
| naive recompile (control) | 14 | 0 | 0 | **14** | 1 | 0 | 0 / 0 | 0 of 32 |
| broken file every 3rd swap + same-path rewrite | 6 + 3 broken | 0 | 0 | 1 (no-shader pipeline) | 0–1 | 0 | 0 / 0 | 0 of 32 |
| seeks during swaps (20→50, 55→25, 40→65 s) | 9 | 0 | 0 | 0 | 1 | 0 | 0 / 0 | 0 of 20 |
| **final**: display-resample + confirmation | 14 | 0 | 0 | 0 | 0 | 0 | 0 / 0 | 0 of 32 |
| **final**: broken files + confirmation, display-resample | 6 + 3 broken | 0 | 0 | 1 (27 ms) | 0 | 1 | 0 / 4 | 0 of 32 |

- *Wrong frame*: the image's OUTPUT cue differs from what its table version (read from the same image) holds for that frame's shot.
- *Changed on screen*: in normal playback, the cue changed between two images of the same shot.
- Plan check, trials without seeks: every shot showed the newest version committed before it started. 0 mismatches.
- Confirmation (final trials): 7.9–14.7 ms after the request. Windowed dry run with default sync: 53–77 ms.
- Broken files: 1 frame drawn with no shader per failure (3 of 3, both sync modes). Caught through mpv's error log 17–52 ms after the swap. In display-resample each failure also delayed one frame, and the first one stretched a frame to 71 ms (mpv compiled its no-shader pipeline, 27 ms). The 4th late frame came 12 s after the last swap, with a routine audio-drift correction: unrelated.
- "Mistimed" frames (display-sync trials): 2 in the first 0.13 s after unpause in every such trial, plus 1 at the first broken file in the final broken-file trial. None at a good swap.
- Interpolation trial: the 22 late cuts span 120 captured images, so the wrong cue stayed for the frame's whole display time; log confirms interpolation was active.

**Memory** (small window, a swap every 0.2 s, ~345 swaps per run):

| Run | RAM per swap | GPU memory per swap |
|---|---|---|
| 8192-entry table (136 KB file) | 850 KB | 533 KB |
| 1024-entry table (20 KB file) | 161 KB | 67 KB |
| Control: alternate two files already parsed | ~5 KB (noise) | ~0 |

Never freed until playback ends: mpv keeps every parsed shader. The render reset itself does not leak (control). Roughly 63 KB plus 98 bytes per table entry in RAM, and 65 bytes per entry on the GPU, which is 8 times the table's own 8 bytes per entry. Budget: a film with 2000 shots uses a 2048-entry table, about 260 KB + 135 KB per swap; 30 swaps ≈ 8 MB + 4 MB.

## Why interpolation breaks per-shot work before the final stage

With interpolation, libplacebo renders each incoming frame into a cache before blending and reuses it while the frame is on screen; the cache key ignores shader parameter values (`renderer.c`, `pl_render_image_mix`). mpv sets `PTS` once per render, to the frame currently due. The first frame of a new shot enters the cache while the previous frame is still due, so a pass before OUTPUT bakes the previous shot's cue into it. The OUTPUT hook runs after blending, once per presented image, and stays exact.

The demo shader already maps the picture onto the monitors in the OUTPUT hook ([rendering research](researcher-261010-1846-windows-rendering-and-smooth-multi-monitor-playback.md), shader skeleton). Per-shot crop, position and size fold into that same mapping, so moving them there adds no resampling step. Only remaining effect under interpolation: on the one refresh at a cut where mpv blends the old and new frame, both get the same shot's mapping. That refresh is already a blend across the cut.

## Correction to the previous spike

The seek list fired out of order: the helper fired every pending seek whose time had passed, so "20→70, 80→35, 50→100" ran as 20→70, at once 70→100, then 100→35 (2521 frames seen). Fixed: seeks fire in list order, one at a time. Re-run (120 s, 24 fps): segments 0–20, 70–80, 35–50, 100–120 s, 1561 frames seen, 33 of 33 cuts exact, 0 wrong frames. The previous conclusion stands.

## Rules for the demo performer (replace the previous list)

1. Per-shot values only through the `PTS` lookup inside the shader, never through options set while playing.
2. Tables in textures, not GLSL const arrays (D3D11 X4505).
3. **All per-shot work in the OUTPUT hook**, inside the monitor mapping. Exact with and without interpolation.
4. Fixed-size table per film; every refinement is a new file with byte-identical code.
5. A refined table changes only shots starting at least 1 s after the playhead read just before.
6. Write atomically, read back, swap over IPC, confirm the drawn version via `vo-passes`; revert on an mpv error or no confirmation within 1 s. Check the first table the same way at startup.
7. Batch refinements: a few dozen swaps per film at most.
8. Cue times on mpv's rebased timeline, boundaries halfway between frames.

## Reproduce

- `python spikes/exact-frame-cue/run-swap-trials.py tools/mpv-v0.41.0-x86_64-w64-mingw32/mpv.exe OUT_DIR [--only NAME,...] [--rescore]`: fullscreen on the laptop panel, about 1.5 min per trial.
- `python spikes/exact-frame-cue/measure-swap-memory.py MPV_EXE OUT_DIR/work24 MEM_DIR [--capacity N] [--mode identical|reload]`: small window, about 75 s (WORK_DIR must hold a `cue-swap.glsl` built with the same `--capacity`).
- Needs Python 3 + numpy and FFmpeg 8.1 on PATH.

## Unresolved questions

1. **Setup A has no even cadence for films in one spanning window.** Refresh rates offered: laptop 60/240 Hz, external 50/59/60/75/100 Hz; the only common rate is 60 Hz, and 24 fps at 60 Hz is uneven (3:2). Smooth playback there needs interpolation (now safe for per-shot work), or each monitor at its own rate. Belongs to spikes S3/S4 and a by-eye A/B in the demo.
2. **Seek during a swap**: if the user seeks past the commit point between the planner reading the playhead and the swap landing (~10–100 ms), the shot after the seek could change once on screen. Not seen (9 swaps, 3 seeks). Possible guard: the planner watches mpv's seek events and re-plans.
3. GPU memory per swap is 8 times the table data; a 2D texture layout might cut it. Untested.
4. Rolling mpv build and libplacebo 7.360: still not run.
5. One window across monitors, and Setup B: still open (S2/S3).
