---
title: Spike — exact-frame cue switching in stock mpv
date: 2026-10-10
type: spike
answers: ADR 0014 consequence "whether stock mpv can apply a change on the exact frame"
harness: spikes/exact-frame-cue/
---

# Spike: exact-frame cue switching in stock mpv

## Answer

**Yes.** Stock mpv (0.40+, `--vo=gpu-next`) fills a user-shader parameter named `PTS` with the timestamp of the frame being rendered, on every render. With the cue sheet inside the generated shader (as a texture) and looked up by `PTS`, each change lands on the exact cut frame. Measured on Setup A: every observed cut exact, 0 wrong frames, in all 8 configurations tried (seeks, display-resample, interpolation, 23.976/24/25 fps, 3000-cue table). The naive way, a script setting the change while playing, lands about one frame (41 ms) late on most cuts.

## Mechanism (from source, mpv v0.41.0)

- Shader declares `//!PARAM PTS` / `//!TYPE DYNAMIC float`. Before each `pl_render_image_mix`, `vo_gpu_next.c` calls `update_hook_opts_dynamic(p, hook, frame->current)` (line 1403), which writes `frame->current->pts` into it (line 2373). In-band: the value belongs to the frame being drawn, no IPC, no script timing.
- Added in mpv 0.40.0 (`DOCS/interface-changes.rst`: "glsl-shader-opts now has predefined parameters"). Still present on master (checked 2026-10-10).
- `PTS` is on mpv's timeline: file start rebased to 0 (`--rebase-start-time=yes` default; `demux.c` applies `ts_offset`). Cue times must be on the same timeline.
- Cue starts placed halfway between the last frame of one shot and the first of the next, so float32 rounding cannot cross a cut (2 h film: 0.5 ms resolution vs 20.8 ms margin at 24 fps).
- Table = `//!TEXTURE` `rg32f` (start, value), bound by its own name (`texelFetch(CUES, ...)`; texture blocks get no `_raw` macro), binary search.

## Setup

- mpv v0.41.0 official Windows build (x86_64-w64-mingw32, built 2025-12-21): libplacebo **v7.358.0**, FFmpeg git-2025-12-21. In `tools/mpv-v0.41.0-x86_64-w64-mingw32/`.
- `--vo=gpu-next --gpu-api=d3d11 --hwdec=d3d11va`, fullscreen on the laptop panel (2560x1600, 240.001 Hz, RTX 5070 Ti Laptop), `--ontop`.
- Test video: synthetic 1280x800 (fills the 16:10 panel at exactly 2x), barcode of its own frame index in every frame, 57 shots incl. 2-5 frame shots, silent audio track, x264 without B-frames. 120 s per trial.
- Shader paints a stripe whose gray level = the cue it applied to the frame it is drawing.
- Capture: Windows Graphics Capture of mpv's window (FFmpeg 8.1 `gfxcapture`), every present up to 240 Hz, cropped to the barcode + stripe strip. Analysis decodes frame index + applied cue from each captured image.
- Arms: **pts** = cue looked up from `PTS` inside the shader. **script** = Lua sets the shader parameter from `time-pos` while playing (out-of-band control).

## Results

| Trial | Frames seen | Cuts exact | Wrong frames | Landing delay ms (min/median/max) | Drops |
|---|---|---|---|---|---|
| pts | 2879/2880 | **56/56** | 0 | 0 / 0 / 0 | 0 |
| script | 2880/2880 | 15/56 | 38 | 0 / **41** / 46 | 0 |
| pts + seeks | 2521/2880 | **51/51** | 0 | 0 / 0 / 0 | 0 |
| script + seeks | 2520/2880 | 10/51 | 38 | 0 / 42 / 50 | 0 |
| pts + display-resample | 2879/2880 | **56/56** | 0 | 0 / 0 / 0 | 0 |
| pts + interpolation, 24 fps | 2879/2880 | 56/56 | 0 | 0 / 0 / 0 | 0 |
| pts, 3000-cue table | 2880/2880 | **56/56** | 0 | 0 / 0 / 0 | 0 |
| pts, 23.976 fps | 2877/2877 | **56/56** | 0 | 0 / 0 / 0 | 0 |
| pts, 25 fps | 2999/3000 | **57/57** | 0 | 0 / 0 / 0 | 0 |
| pts + interpolation, 25 fps | 2999/3000 | **57/57** | 0 | 0 / 0 / 0 | 0 |

- Seeks: 20 s → 70 s, 80 s → 35 s, 50 s → 100 s (`absolute+exact`); cuts right after each jump were exact too.
- Correction (2026-10-11): the helper fired every pending seek whose time had passed, so the list actually ran 20 → 70 → 100 → 35 s. Re-run with seeks in list order: 33/33 cuts exact, 0 wrong frames ([follow-up spike](spike-261011-0950-refining-the-cue-table-during-playback.md)).
- Interpolation at 24 fps on 240 Hz is a no-op: mpv disables it at a whole-number refresh/fps ratio. The 25 fps trial is the real test: log confirms "re-enabling interpolation".
- The measurement can see errors: the script arm's 38 wrong frames and one-frame lateness were caught by the same pipeline. Each captured image holds barcode and stripe together, so the frame-to-cue pairing is exact by construction.
- Per-trial JSON: `spikes/exact-frame-cue/results-2026-10-10/`.

## Rules for the demo performer

Superseded on 2026-10-11 by the rules in the [follow-up spike](spike-261011-0950-refining-the-cue-table-during-playback.md).

1. **Per-shot values only via the `PTS` lookup inside the shader.** Never via `glsl-shader-opts` or properties while playing: one frame late (table above). Changing `glsl-shader-opts` at runtime also replaces the whole list and resets every other shader parameter.
2. **Tables go in textures, not GLSL const arrays.** On D3D11, 3000 const entries failed with `X4505: Sum of temp registers and indexable temp registers exceeds limit of 4096`.
3. **Fail loud on shader errors.** When a pass fails to compile, libplacebo drops it and the video plays on without it; only the mpv log shows the error. The performer must check for libplacebo errors at startup.
4. **Cue-dependent work in the OUTPUT hook, or interpolation off.** With interpolation, libplacebo renders each frame into a cache before mixing, while `PTS` names the current frame (`renderer.c`, `pl_render_image_mix`). A cue-dependent pass before OUTPUT (e.g. a MAIN-stage warp) could bake the wrong cue into an incoming frame. Only the OUTPUT hook was tested.
5. **Cue times on mpv's rebased timeline, boundaries halfway between frames.**

## Other findings

- **Desktop Duplication cannot see mpv's fullscreen output.** It captured pure black: the flip-model swapchain is promoted to a hardware overlay plane (mpv logs `overlay: yes`), outside the composed desktop. Any capture-based tooling (the A/B harness) needs Windows Graphics Capture of the window.
- **mpv on Windows ignores `--screen-name` / `--fs-screen-name`.** Use `--screen=N` / `--fs-screen=N` (Windows monitor enumeration order; here 0 = laptop panel, 1 = external).
- **Version gap:** the official 0.41.0 build bundles libplacebo 7.358.0, not the 7.360 that ADR 0012 names. The rolling build (rebuilt 2026-10-10, commit 57c745b1a) should carry a newer one. Pick when building the demo.

## Reproduce

`python spikes/exact-frame-cue/run-trial-matrix.py tools/mpv-v0.41.0-x86_64-w64-mingw32/mpv.exe OUT_DIR [--only NAME,...]`. Needs Python 3 + numpy and FFmpeg 8.1 on PATH. Takes over the laptop panel, about 2 minutes per trial.

## Unresolved questions

1. **Refining the cue sheet during playback.** ADR 0014 lets the background scan refine the part not yet shown, but the table lives in the shader, so refining means reloading it. The first compile took about 160 ms here, so a naive reload may drop a frame. A fixed-size table (same GLSL, new texture data) might hit libplacebo's shader cache. Untested. Alternative for the demo: finish the scan before playing. *Answered 2026-10-11: works with a fixed-size table and a new file per version, no compile and no stutter ([follow-up spike](spike-261011-0950-refining-the-cue-table-during-playback.md)).*
2. Cue-dependent passes before the OUTPUT hook under interpolation: untested (rule 4). *Answered 2026-10-11: one frame late at 22 of 33 cuts; per-shot work goes in the OUTPUT hook ([follow-up spike](spike-261011-0950-refining-the-cue-table-during-playback.md)).*
3. One window spanning both monitors: all panels get the same cue per frame by construction; presentation skew across monitors is the research's S2/S3, not tested here.
4. Rolling mpv build not run; master source unchanged on this path.
5. Variable-frame-rate files, timestamp resets and MKV ordered chapters: untested.
