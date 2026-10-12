# 0026. Picture path: one resample per monitor, on frames libplacebo prepares

- **Status:** Accepted
- **Date:** 2026-10-12
- **Refines:** [0022](0022-own-performer-on-libplacebo-and-ffmpeg.md)

## Context

The performer draws each monitor's part of the picture on that monitor's own refresh ([ADR 0021](0021-one-window-per-monitor-one-film-clock.md)). Each monitor pixel must show the picture spot at its physical place, also at the monitor's border, because the picture continues behind the bezel ([ADR 0009](0009-bezels-placement-first-picture-continues-behind-bezel.md)). The picture should be resampled once: every resample softens it a little. In a camera slide each row is drawn for its own moment, and the rolling blend mixes two film frames per row ([ADR 0025](0025-refresh-times-come-from-the-scan-line.md) gives the row times).

The [engine research](../../plans/reports/researcher-261011-1247-performer-engine-and-picture-path.md) planned for libplacebo to draw each monitor from the source with crops ("S1"). The [pass-structure spike](../../plans/reports/spike-261012-1041-picture-path-pass-structure.md) compared that with our own single resample on frames libplacebo prepares ("S2"), against a float64 reference, on the RTX 5070 Ti and the Radeon 610M, D3D11 and Vulkan:

- S2 stays under 1 code value of the reference everywhere away from the picture's edge, in fold, curve, slide and rolling blend (worst 0.53). S1 is off by up to 46 codes at the monitor borders, because libplacebo samples only inside the crop. It can't draw the curve, and a row-by-row slide takes it 34 resamples, still 56 codes off.
- With the same filter, S1 and S2 are equally sharp. Scaling first and warping after (two resamples) halves fine-line contrast or adds ringing.
- Our resample shader is 2 to 4.5 times slower than libplacebo's. Setup A's two monitors take 1.14 ms per refresh on the RTX (D3D11); on the 610M 48.5 ms (D3D11) and 19.9 ms (Vulkan), against about 10 ms for S1.

Passes 2 to 4 come from the [finish report](../../plans/reports/spike-261012-1020-picture-path-finish-stopped.md): libplacebo dithers only the picture, not the area around it, and Windows colour management (ACM) already converts an 8-bit sRGB swapchain on the laptop panel.

## Decision

### What you see (protected by [ADR 0017](0017-agents-may-change-how-not-what-you-see.md))

- Every monitor pixel, the border rows and columns included, shows the picture spot at its physical place. A line crossing a bezel lines up on both sides.
- The picture is as sharp as libplacebo's best scaler with the same filter. It is never scaled twice.
- One colour correction covers the picture and the surround together, so no edge shows where they meet.
- Dialogue subtitles are drawn crisp at the monitor's own pixels, inside one monitor ([ADR 0013](0013-dialogue-subtitles-stay-inside-one-screen.md)).
- The rolling blend is built into the picture pass as a switch, off by default. It goes on only after the user's by-eye verdict (X2, phase 8).
- The surround's look at cuts (its rule and time constant) is open until the by-eye check in phase 8.

### How it works

- **Prepare, once per film frame and graphics adapter.** libplacebo turns the decoded frame into a linear-light picture at source resolution: hardware frame mapping, chroma, colour decoding. It does no scaling. Monitors that upscale also get a copy in libplacebo's sigmoid domain, as libplacebo does by default when it upscales. When libplacebo renders into our canvases, the black point is declared (`min_luma = PL_COLOR_HDR_BLACK`). Otherwise it assumes a 1000:1 display and lifts near-black by about 3 codes.
- **Pass 1, picture: our own single resample, per monitor and refresh.** One compute pass maps each monitor pixel to its source position and filters the prepared frame there (elliptical weighted average, libplacebo's filter shapes). Fold is an affine map per monitor; curve is a per-pixel map. The pass reads the whole prepared frame, not just the crop, so border pixels are right. Inside the same pass:
  - per-row slide offsets for pan mode: row y is drawn at the refresh's top-row time plus y line times (ADR 0025);
  - per-row frame weights for the rolling blend, from two prepared frames, with the same row times.

  Output: a linear-light FP16 canvas the size of the monitor, with picture coverage in alpha.
- **Pass 2, surround.** Fills the leftover area from the picture's edge colours, kept in GPU state across refreshes. No resample.
- **Pass 3, finish.** One colour correction per monitor in linear light over picture and surround, then encode to the monitor's transfer, then our own blue-noise dither to the panel's bit depth.
- **Pass 4, subtitles.** libass bitmaps drawn at native pixels on top, after dither (`pl_render_image` with no image and `border = PL_CLEAR_SKIP`).
- **Colour ownership.** On a monitor with Windows ACM on, Windows owns gamut: the player sends sRGB with relative tweaks only (gain, white, black lift). With ACM off, the player maps gamut with its own matrix and encodes.
- **Rejected.** libplacebo drawing each monitor with crops (S1): wrong at monitor borders without an extra margin, no curve, no per-row timing in one resample. Our resample as a hook inside libplacebo's pipeline: the same shader at a higher cost, because the source conversion reruns per monitor per refresh. Any warp after scaling (canvas-then-warp, libplacebo's `distort_params`): two resamples.

## Consequences

- Phase 2's requirements now mean:
  - R5 is our own code on libplacebo. Pass 1 is ours, passes 2 to 4 are ours with libplacebo's dither and overlay helpers, and libplacebo is not patched.
  - R8 is met inside pass 1, on the app side. The two-renders-plus-blend workaround, the custom hook and the libplacebo patch for per-row weights are all dropped.
- We own an EWA resampler, about 180 lines of shader. The harness's float64 reference renderer (`spikes/picture-path/pp_reference.py` and its geometry and resample modules) is its test oracle and is kept.
- 4:2:0 chroma is upsampled once per film frame, not once per monitor.
- Cost: fine on the RTX, about a quarter of the GPU with the laptop at 240 Hz and the external at 96 Hz. Too slow on the 610M as written. Before any screen runs on an integrated GPU (Setup B), the shader gets libplacebo's shared-memory tiling, or that screen gets a smaller filter (`ewa_hermite`: 9.3 ms against 58.4 for one 2560×1600 monitor on the 610M). Reusing the canvas when nothing moved since the last refresh is a further option.
- By-eye checks moved to [phase 8](../../plans/261011-0554-m1-architecture-and-technology/phase-08-thin-prototype-and-exit.md), on the real screens:
  - a diagonal across the bezel (no step at the right gap, a step seen at 5 mm off);
  - a 0–10 % near-black ramp, corrected next to uncorrected (no bands; this also shows whether Windows re-quantizes our dither on the ACM laptop panel);
  - grey bars across the bezel (the user can't say which side is brighter);
  - the surround at 10 consecutive hard cuts (no flicker);
  - X2, which decides the rolling blend.
- Still open: an FP16 scRGB swapchain on ACM monitors (Microsoft's recommendation, untested); HDR sources through the prepare step; the filter choice, which is cheap to change.
