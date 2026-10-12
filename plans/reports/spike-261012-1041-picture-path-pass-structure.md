---
title: "Picture path pass structure: libplacebo draws each monitor (S1) vs our own single resample on frames libplacebo prepares (S2)"
date: 2026-10-12
type: spike
answers: "M1 phase 3, step 2 as cut by the user on 2026-10-12: which pass structure, checked for correctness, sharpness and cost on both GPUs"
harness: spikes/picture-path/
status: "closed from the stopped harness's results plus three reruns of its own scripts (checks, benchmarks, canvas comparisons); no new code"
---

# Picture path pass structure: S1 vs S2

The harness agent was stopped on the user's scope cut before it wrote a report. This report reads what it measured, reruns its own scripts where results were stale or missing, and settles the pass structure. ADR: [0026](../../docs/adr/0026-picture-path-one-resample-per-monitor.md).

**Terms.** *Resample*: computing a monitor pixel from several source pixels at a non-integer position with a filter; every resample softens a little, so there should be one. *Fold*: the picture printed flat across the panels, gaps included; each monitor's mapping is affine. *Curve*: the picture on a cylinder seen from the seat; each pixel has its own mapping. *Rolling blend*: each row of a refresh mixes the old and new film frame by that row's own moment. *Shear*: a camera slide drawn row by row, each row at its own moment (ADR 0025). *Code*: one step of 8-bit output.

Evidence tags: [L] measured on this PC (RTX 5070 Ti Laptop and Radeon 610M, libplacebo master at 1dd0226 from `C:\Users\tamla\msys64\home\tamla\p0\prefix\libplacebo-master`), [V] source read, [D] derived. All paths below are under `spikes/picture-path/` unless they start with `plans/` or `docs/`.

## 1. Answer

**S2.** libplacebo prepares each film frame once (decode mapping, colour decoding to linear light, no scaling); our own shader does the one resample per monitor, with the geometry and the per-row timing inside it.

- **Correct where S1 isn't** [L]. S2 stays under 1 code of the float64 reference everywhere away from the picture's edge in fold, curve, shear and rolling blend, on both GPUs and both backends: worst 0.534 code (curve, external toed in 15°), at most 0.352 in the base room, 0 pixels over 1. S1 is off by up to 46 codes at the monitor borders (it samples only inside its crop), can't draw a curve, and needs 34 resamples to approximate a shear (still 56 codes off).
- **One resample** [L]: S2 counts 1 per monitor in every scenario. S1 counts 1 for plain fold, 2 for a rolling blend, 34 for a shear.
- **Same sharpness** [L]: with the same filter, S1 and S2 give identical slanted-edge MTF and line-pair contrast. In curve, S2 equals libplacebo with our shader as a hook; libplacebo-then-warp (two resamples) loses most 2 px line contrast on the laptop (0.99 to 0.40–0.51).
- **Cost is S2's weak point** [L]. Our shader is a straight loop; libplacebo's polar sampler is 2 to 4.5 times faster. On the RTX that doesn't matter (Setup A's two monitors: 1.14 ms per refresh, D3D11). On the Radeon 610M it does: 48.5 ms (D3D11) and 19.9 ms (Vulkan) per refresh for the same pair, against 10.8 and 10.1 ms for S1. The shader needs optimizing before any screen runs on the iGPU (Setup B).

## 2. What was compared

Structures, all in `src/` [V]:

| Name | What it does | Source |
|---|---|---|
| S1 | libplacebo draws each monitor: one `pl_render_image_mix` per monitor, fold expressed as `image.crop` and `target.crop` | `pp-s1.c`, `pp-s1.h` |
| S1-margin | S1 into a canvas a few pixels larger on every side, middle copied out | `pp-s1.h` |
| S1-strips | S1 in n horizontal strips, each with the slide of its middle row (shear) | `pp-s1.h` |
| S1-blend | S1 twice (frames A and B) plus our per-row blend pass | `pp-run.h` |
| S1-hook | libplacebo draws each monitor; our resample runs inside its pipeline as an RGB-stage hook sized to the monitor | `pp-s1-hook.c` |
| S1-twopass | libplacebo scales to the monitor's pixel pitch, then a second filtered pass warps to the curve | `pp-run.h` |
| S2 | libplacebo converts each frame once, 1:1, to linear BT.709 RGBA16F (plus a sigmoid copy for upscaling monitors); our compute shader resamples to each monitor with the per-pixel map, per-row slide and optional per-row blend | `pp-s2.c`, `shaders/pp-resample.glsl` (177 lines) |

Reference: a float64 Python renderer of the same geometry and filters (`pp_geometry.py`, `pp_resample.py`, `pp_reference.py`); its geometry tests pass, 25 checks, 0 failed (`tests/test-geometry.py`, run today) [L]. Room: `room/setup-a-default.json` (coplanar, gap 20 mm, seat 700 mm; a placeholder until the phase 6 room model). Curve radius 700 mm (`scenarios/curve-t1.json`). Shear: a 300 mm/s slide (`scenarios/shear-fold.json`). Patterns T1 (grid, circles on the seam, diagonals) and T7 (slanted edges, zone plate, line pairs) from `make-patterns.py`.

Metric (`pp_compare.py`) [V]: difference in 8-bit sRGB codes (float, before rounding) between the harness canvas and the reference. *Interior* = full picture coverage and at least 6 px from the picture's own edge; a monitor's border counts as interior, because the picture continues behind the bezel (ADR 0009). *Core* = interior also 6 px away from the monitor's border.

## 3. Correctness against the reference

Current results: `out/checks/<backend>-<adapter>-rgb8/summary-fold+curve+identity+shear+blend.json` (09:55–10:00), and today's rerun on RTX D3D11 with the current binary and shader, `out/checks/d3d11-nvidia-rgb8/summary-curve+fold+identity+shear+blend.json` (10:41, adds fold-t7 and curve-t7; every shared row identical to the 09:55 run). The shader files changed after the 09:55–10:00 matrix (`src/shaders/*.glsl` 10:04 and 10:09), which is why the rerun was needed [L].

Interior max / p99 / pixels over 1 code, laptop (2560×1600) and external (1920×1080), RTX D3D11:

| Scenario | Structure | Resamples | Laptop | External | Core max (lap / ext) |
|---|---|---|---|---|---|
| fold-t1 | S1 | 1 | 10.29 / 0.122 / 228 | 46.01 / 0.065 / 86 | 0.209 / 0.685 |
| fold-t1 | S2 | 1 | 0.271 / 0.133 / 0 | 0.320 / 0.057 / 0 | same |
| fold-t7 | S1 | 1 | 16.78 / 0.134 / 1591 | 40.24 / 0.117 / 454 | 0.238 / 0.564 |
| fold-t7 | S2 | 1 | 0.287 / 0.185 / 0 | 0.193 / 0.117 / 0 | same |
| curve-t1 | S2 | 1 | 0.265 / 0.132 / 0 | 0.263 / 0.056 / 0 | same |
| curve-t7 | S2 | 1 | 0.279 / 0.185 / 0 | 0.177 / 0.117 / 0 | same |
| shear-fold | S2 | 1 | 0.162 / 0.075 / 0 | 0.203 / 0.040 / 0 | same |
| shear-fold | S1-strips (34) | 34 | 17.07 / 6.51 / 58,502 | 56.25 / 15.42 / 27,113 | same |
| blend-fold | S2 | 1 | 0.253 / 0.166 / 0 | 0.311 / 0.083 / 0 | same |
| blend-fold | S1-blend | 2 | 16.38 / 0.149 / 1424 | 45.77 / 0.128 / 274 | 0.296 / 0.739 |
| blend-flat | S2 | 1 | 0.178 / 0.176 / 0 | 0.124 / 0.116 / 0 | same |
| identity | S1 | 0 | 0.080 | 0.080 | |
| identity | S2 | 1 | 0.080 | 0.118 | |

- **Across GPUs and backends** [L]: RTX Vulkan, 610M D3D11 and 610M Vulkan give the same S1 numbers and S2 numbers within 0.04 code (largest S2 interior max: 0.333, fold-t1 external, 610M Vulkan) (`tmp/matrix.log` and the four summaries).
- **Rooms** [L]: toe-in 15° and 30° and "laptop nearer" stay clean in S2 (interior max ≤ 0.534, 0 over 1; `out/checks/d3d11-nvidia-rgb8/summary-curve+rooms.json`). curveflat-t1 (the curve's flat limit) 0.271 / 0.352, 0 over 1 (same file).
- **Where S1 fails** [L, V]: only near the monitor borders (core is clean). libplacebo keeps only the crop in its intermediate textures, so at a border where the picture continues behind the bezel the filter footprint is clamped (`pp-s1.h` header). S1-margin removes it: fold-t1 interior max 0.209 / 0.697, 0 over 1 (`compare-canvases.py` on `out/fold-t1-s1m`, run today). The margin costs extra pixels per monitor; not benchmarked.
- **S1-hook is correct too** [L]: fold-t1 0.381 / 0.586, curve-t1 0.401 / 0.779, 0 over 1 (`compare-canvases.py` on `out/hook-fold-t1`, `out/hook-curve-t1`, run today). It is S2's shader inside libplacebo, so it shares S2's correctness and adds cost (section 5).
- **Shear in S1** [L]: strips are a staircase of a smooth shear, so the error is large everywhere, not just at edges.
- **Rolling blend in S1** [L]: correct in the core; the border error is S1's crop clamp again, doubled by two renders.

**Superseded results** [L, D]. `summary-curve.json` (09:48) and the curve rows of `summary-rooms.json` (09:50) show S2 curve errors up to 28 codes. The reference files didn't change (`ref/curve-t1/*.f32` 09:43); the GPU side did: `src/pp-gpu.c` and `check-gpu-map.py` were edited at 09:50, `out/map-curve-t1` (the GPU map dump) is 09:51, and every curve run from 09:52 on is clean, today's rerun included. `summary-identity.json`, `summary-shear.json` and `summary-blend.json` are earlier copies of rows in the 09:55 summary; `tmp/checks-shear.log` shows S1-strips on the external with 0 resamples, an earlier bug, fixed in the 09:55 run (34).

## 4. Sharpness (T7)

Each monitor alone, picture fitted to it: the laptop upscales 1.333×, the external downscales 0.741× (`measure-sharpness.py` header). MTF in cycles per output pixel. Results `out/sharp/results.json` (10:08) and `out/sharp/results-curve.json` (10:13), RTX D3D11 [L].

| Case | MTF50 | Overshoot | 2 px line pairs | 3 px |
|---|---|---|---|---|
| Laptop, S1 `ewa_lanczossharp` | 0.403 | 7.3 % | 0.94 | 0.87 |
| Laptop, S2 `ewa_lanczossharp` | 0.403 | 7.3 % | 0.94 | 0.87 |
| External, S1 `ewa_lanczossharp` | 0.468 | 5.8 % | 0.06 | 0.79 |
| External, S2 `ewa_lanczossharp` | 0.468 | 5.8 % | 0.06 | 0.79 |
| Laptop curve, S2 | 0.461 | 7.7 % | 0.99 | 1.08 |
| Laptop curve, S1-hook | 0.461 | 7.6 % | 0.99 | 1.08 |
| Laptop curve, S1-twopass (then `ewa_catmull_rom` / `ewa_mitchell` / `ewa_lanczossharp`) | 0.474 / 0.410 / 0.424 | 18.1 / 8.7 / 9.9 % | 0.50 / 0.40 / 0.51 | 1.11 / 1.05 / 1.10 |
| External curve, S2 | 0.465 | 6.0 % | 0.06 | 0.80 |
| External curve, S1-twopass (same three) | 0.420 / 0.371 / 0.399 | 8.2 / 3.9 / 7.1 % | 0.09 / 0.06 / 0.06 | 1.22 / 0.90 / 0.79 |

- S1 and S2 match to the third decimal for every filter both offer (`ewa_lanczos`, `ewa_lanczos4sharpest`, `ewa_robidoux`, anti-ringing on) [L]. The structure doesn't cost sharpness; the filter is a cheap, later choice.
- Two resamples (S1-twopass) either soften (2 px contrast halves on the laptop) or ring (overshoot up to 18 %) [L].
- Not measured: sharpness across the seam itself (each monitor was measured alone; seam continuity is the T1 correctness check above).

## 5. Cost

`run-benchmarks.py` (offscreen, no window, GPU lock held) run today 10:33–10:39 for the four GPU/backend pairs; the stopped agent's own run never got the lock (`tmp/bench-nvidia-d3d11.log`) and `out/bench/` was empty. Results `out/bench/<backend>-<adapter>/*.json`, tables from `summarize-bench.py` [L]. "Refresh" = the inputs move a little every refresh (as in pan mode), so nothing is reused; "static" = S1 with nothing moving, libplacebo's frame cache hit; "prepare" = S2's once-per-film-frame step. GPU ms per refresh, p50 (p99), both Setup A monitors (fold-t1, 1080p RGB source):

| | S1 static | S1 refresh | S2 refresh | S2 prepare | S1-hook refresh |
|---|---|---|---|---|---|
| RTX, D3D11 | 0.027 | 0.387 (0.402) | 1.140 (1.562) | 0.041 | 1.358 (1.600) |
| RTX, Vulkan | 0.036 | 0.433 (0.442) | 1.390 (1.580) | 0.061 | not run |
| 610M, D3D11 | 0.657 | 10.79 (10.97) | 48.49 (48.59) | 0.966 | 52.63 (53.17) |
| 610M, Vulkan | 0.828 | 10.07 (10.30) | 19.87 (20.37) | 1.136 | not run |

- Split per monitor, S2 refresh: RTX D3D11 laptop 0.803, external 0.331 ms; 610M D3D11 laptop 34.17, external 14.32 ms; 610M Vulkan laptop 13.95, external 5.91 ms [L].
- Curve costs about the same as fold: Setup A curve-t1 S2 1.189 ms (RTX D3D11), 50.85 ms (610M D3D11) [L].
- 4:2:0 source: S1 refresh rises (RTX D3D11 0.593 ms, 610M D3D11 16.47 ms), because each monitor redoes chroma; S2 refresh doesn't change (1.134 / 48.51 ms), the chroma work sits in prepare once per frame [L].
- Filter size drives S2's cost: one 2560×1600 monitor on the 610M D3D11, S2: `ewa_lanczos4sharpest` 77.6, `ewa_lanczossharp` 58.4, `ewa_robidoux` 24.9, `ewa_hermite` 9.3 ms; S1 `ewa_lanczossharp` 11.9 ms [L].
- Budget [D]: RTX D3D11, laptop at 240 Hz plus external at 96 Hz, S2 every refresh: 0.803 × 240 + 0.331 × 96 ≈ 225 ms of GPU per second, about 23 % of the RTX; S1 the same way about 8 %. Fine on Setup A. On the 610M the laptop refresh alone (34 ms D3D11, 14 ms Vulkan) misses even 60 Hz.
- Why [V, D]: libplacebo's polar sampler runs as a compute shader that loads a tile of source pixels into shared memory once per work group (research report section 4 R8, `sampling.c` ~660–690); our shader fetches every tap from the texture and looks up the kernel table per tap (`shaders/pp-resample.glsl`, `pp_ewa`). So S2's cost is our shader's, not the structure's. Two cheaper paths are open: the same shared-memory tiling, and reusing the canvas when nothing changed since the last refresh (no slide, no blend, same frame: what S1's frame cache does). Neither is built.
- S1-hook costs more than S2 on both GPUs, because the source conversion and our linearize pass rerun per monitor per refresh (`pp-s1-hook.h` header) [L, V].

## 6. Not measured or not done

- Cost of S1-margin, of the rolling blend (S2 with two frames) and of passes 2 to 4. S1-hook on Vulkan.
- An optimized S2 shader (the 610M numbers are for the straight loop).
- 4:2:0 and 10-bit correctness: outputs exist (`out/yuv-*`, `out/yuvc-*`), but the reference is RGB, so no comparison was made.
- HDR sources through the prepare step (tone mapping into the prepared frame).
- Sharpness across the seam on real screens, and every by-eye check (phase 8).
- The real room: all scenarios use the placeholder Setup A room.

## 7. For the ADR

Per film frame, libplacebo prepares the source once per adapter: decode mapping, chroma, colour decoding to linear light, no scaling, black point declared. Per monitor and refresh: pass 1 is our own single resample from the prepared frame to the monitor's grid, fold as an affine map and curve as a per-pixel map, sampling the whole frame so the monitor's border pixels are right, with per-row slide offsets (pan mode) and per-row frame weights (rolling blend) from ADR 0025 row times; passes 2 to 4 as in the [finish report](spike-261012-1020-picture-path-finish-stopped.md) section 4. Rejected: S1 (border error, no curve, no per-row timing in one resample), S1-hook (S2's shader at higher cost), any warp after scaling (two resamples). Risk: S2's shader cost on the iGPU.

## 8. Unresolved questions

1. Does a shared-memory version of our resampler bring the 610M within a refresh (Setup B), or does the iGPU screen need a smaller filter? Decides before Setup B, not before the phase 8 prototype on Setup A.
2. Reusing the canvas when nothing moved: worth building, or is every refresh rendered anyway once pan mode and the rolling blend are on?
3. Does the prepare step handle HDR sources (tone mapping into linear BT.709 or a wider space)? Untested.
4. Why is the 610M 2.4 times slower on D3D11 than on Vulkan for our shader (48.5 vs 19.9 ms), while S1 is equal? Not investigated.
