---
title: "Performer picture path built into perf1: prepare, one resample, surround, colour, subtitles"
date: 2026-10-12
type: implementation report
phases: "M1 phase 2 (R2, R3, R5, R8, R9) and phase 3"
code: spikes/performer-p1/ (src/perf1-picture-*.c, perf1-room.c, perf1-subs-load.c, perf1-finish-*.c)
---

# Performer picture path (phase 2 steps P2/P4/P6 and phase 3)

Status: DONE_WITH_CONCERNS. Everything compiles clean with `-Wall -Wextra`, the unit and offline-reference checks pass, and the real program runs windowed on both screens, fold and curve, with hardware decode, seeks and cue changes. Open: nothing was run full screen (rule), so cost and pacing at full size are unmeasured; the by-eye checks are phase 8.

## 1. What was built

Per monitor, per refresh (`picture_draw`, called from `perf1-render.c`; the file went from 476 to 432 lines):

| Stage | Where | Notes |
|---|---|---|
| Prepare | `perf1-picture-prepare.c` | libplacebo maps the frame (D3D11VA or uploaded), colour decode to linear BT.709 RGBA16F at source size, no scaling, **black point declared** (`min_luma = HDR_BLACK`). Cached by frame timestamp (5 slots, textures reused), so a 240 Hz screen converts a frame once, not ten times. Sigmoid copy made on demand for monitors that magnify. |
| Pass 1 | `perf1-picture-pass1.c`, `-shader.c`, `-map.c`, `-plan.c`, `-timing.c`, `perf1-room.c` | Port of pp-s2: one EWA resample per monitor pixel. Surface in perf1's coordinates (u right, v down, mm). **Fold** (affine per monitor, gaps as physical gaps) and **curve** (ray from the seat to a cylinder) from one room file. Crop/place from the cue. Per-row slide. Optional rolling blend. |
| Pass 2 | `perf1-finish-surround*.c` (ported) | Glow from the picture's edges, GPU state across refreshes. Per-shot `surround` object in the cue (mode, gain, falloff, time constant, cut rule). |
| Pass 3 | `perf1-finish-color*.c` (ported) | One correction in linear light over picture and surround, encode (sRGB or power), our blue-noise dither to the swapchain's depth; fused with pass 2 into one pass over the canvas. Colour block per monitor in the room file. |
| Pass 4 | `perf1-finish-subs.c` (ported), `perf1-subs-load.c` | libass at native pixels after the dither, on the monitor the cue names (`subtitle_monitor`), else `--sub-monitor`, else the monitor showing most of the picture. Subtitle packets are read up front through a separate `AVFormatContext` (text tracks of the film, attached fonts, or `--sub FILE`); `perf1-decode.c` is untouched. Time = the timestamp of the frame on screen. |

Cue sheet v0 gained an optional per-shot **`slide`**: `[[t, du_mm, dv_mm], ...]`, at least two points, `t` strictly increasing, linear between, held outside; old sheets stay valid and keep their fingerprints (the slide is not in the fingerprint, `analyze-cues.py` is unchanged). Each row is drawn at the path position for the middle of its own light time (row light = top-row time + row × line time + panel lag, then + half a refresh, the seam tuner's rule), via the engine's film clock.

**Rolling blend** (`--blend rolling`, off by default): a follower refresh mixes the engine's frame with the neighbouring one by the share of the refresh after the master's row switch, `w(y) = clamp((a(y) + P - e(y)) / P, 0, 1)`, two numbers per refresh (`wa`, `wb`). Each frame is seen through **its own shot's** crop, place, slide and domain, so a blend across a cut is exact. The neighbouring frame is not in the monitor's queue (ZOH keeps one frame), so it comes from the prepared-frame cache; if it is gone the refresh is drawn unmixed and counted (`blend_missed`).

New options: `--room FILE`, `--surface fold|curve`, `--blend off|rolling`, `--surround glow|black`, `--kernel NAME`, `--chroma-filter NAME`, `--sub FILE`, `--sub-stream N`, `--sub-monitor N`, `--no-subs`, `--dump-canvas DIR [--dump-at S] [--dump-cut]` (read-back for the offline comparison). `--surround` defaults to **glow**: change it to black if the current look must stay (user decision, ADR 0008/0017).

Room file: `panorama.room/1` as in the harness plus an optional `surface` and a per-monitor `color` block (`acm`, `gain`, `white`, `black_lift`, `primaries`, `transfer`, `power`), neutral by default. Format and the placement conventions are in the header of `src/perf1-room.h`. Monitors are matched to the room left to right and their pixel sizes must match.

Shared files touched, with small Edits only: `compile-perf1.sh` (PICTURE sources, libass, a `picture-test` program), `perf1-args.c` (2 lines), `perf1-app.h` (4 prototypes), `perf1-main.c` (2 lines: `picture_load_room()` before `assign_geometry`, `picture_top_mm()` for the monitors' tops). No engine, decode, control or audio file touched. The per-pass cost is in a new `monitor-N-passes.json` (and the console), written by `display_destroy`; `perf1-log.c` is not mine, so the existing `render_gpu_ms` column now carries the sum of the passes and the split is in the new file.

## 2. How to run

```powershell
# from C:\Users\tamla\Downloads\Telegram Desktop\panorama\spikes\performer-p1
pwsh -File .\compile.ps1 -Output perf1-pic-lgpl.exe        # the program (own output name: the audio work writes the default one)
pwsh -File .\compile.ps1 -Program picture-test              # bin\picture-selftest.exe
pwsh -File .\compile.ps1 -Program latch-test                # bin\cue-selftest.exe, latch-sim.exe
.\bin\run.cmd perf1-pic-lgpl.exe --windowed --window-scale 0.5 --file FILM --room ..\picture-path\room\setup-a-default.json --cues SHEET --seconds 20
```
Full screen: drop `--windowed`, add `--refresh DISPLAY2=96` as before; `--blend rolling`, `--surface curve`, `--surround black` as wanted. Headless runs also work with `--room`/`--blend` (CPU side only).

## 3. Verified

Automated, nothing full screen, no display-mode change, small windows for a few seconds each.

| Check | Result |
|---|---|
| Compile | `-Wall -Wextra`, 0 warnings (full program, `picture-test`, `latch-test`) |
| `bin\picture-selftest.exe` (new): room file and its errors, colour block, fold and curve map (flat curve = coplanar fold to 1e-9 mm; arc length on a cylinder), Jacobian, upscale rule, slide per row, film-time map, blend weights, blend choice, surround look and rule | 92 checks, 0 failed |
| `bin\cue-selftest.exe` with the new slide cases (parse, rejects, interpolation, hold, NaN, fingerprint unchanged) | 183 checks (was 155), 0 failed |
| `run-latch-test.py --skip-exe` (steps 1 to 6: units, judge, six simulations, clock rules, seven negative controls) | PASS in 89.5 s |
| `run-latch-test.py` step 7 (real program headless, my exe) | verdict FAIL on **C7/C7b only** (late first refresh at a restart, the known gap of the P3 report section 6), 0 wrong frames, 0 shots changed, 0 clock violations; the machine was loaded (the audio work's runs were going, headless runs at idle priority). A/B on the same script, 2 rounds each: plan code on C7 4/3, plan code off C7 8/8. So not caused by this work; needs a quiet machine. |
| Headless run with room, blend and a slide sheet (cue judge: PASS) | 288 of 1442 laptop refreshes blended (2 per frame change: the top and bottom rows straddle different refreshes), slide on exactly the slide shot's refreshes |
| Windowed 30 s, cue-test.mkv, D3D11VA, 8 sheet versions, 7 seeks, glow, room | VERDICT PASS (`analyze-cues.py`), 6300 presents per monitor, `drawn!=chosen 0` |
| Real film (HEVC episode, D3D11VA), windowed, seek to 131.5 s, glow, embedded subtitle track (513 events, 3 fonts, read in 0.09 s) and ASS stream 3 (480 events) | plays, subtitle crisp on the monitor with most picture; picture runs across the bezel gap, glow in the leftover area |

**Offline reference** (`run-picture-checks.py`, 10 cases at `--window-scale 0.5`, 1280x800 + 960x540; pass 1's canvas read back and compared with the harness's float64 reference using its own `pp_compare`, tolerance 1 code, away from edges; the finished frame compared with pass 3's exact value). Transcript: `spikes/performer-p1/results-pic/final-checks.txt`; media regenerate with `make-picture-test-media.py`.

| Case | Pass 1 interior max / p99 (code values), laptop; external |
|---|---|
| fold (t1 pattern) | 0.269 / 0.055; 0.111 / 0.049 |
| curve (seat 700 mm) | 0.463 / 0.057; 0.162 / 0.053 |
| crop (laptop magnifies: sigmoid domain, external shrinks) | 0.264 / 0.136; 0.243 / 0.062 |
| slide (300 mm/s across, 60 mm/s down, per row) | 0.095 / 0.055; 0.133 / 0.057 |
| blend, two alternating pictures (a laptop refresh mixing frames 12 and 13) | 0.227 / 0.117 |
| **blend across a cut** (frames 23 and 24, crops full and quarter, domains linear and sigmoid, each through its own shot) | 0.264 / 0.128 |

Every case: 0 pixels over 1 code, alpha error under 5e-4. Finished frame: error under 1 code (the dither's bound), mean error within 0.001 code of zero, leftover area exactly black with `--surround black`. Also: a near-black ramp (codes 0, 1, 2, 3, 4, 6, 8, 10, 16 ... 255) reaches the canvas within 0.02 code (the black point declaration works, no 3-code shift); a per-monitor colour block (gain 0.8 x white [1, 0.95, 0.9], black lift 0.01, and another monitor's) matches the exact maths; subtitles from the film's SRT track appear on the named monitor only (3184 text pixels, bottom centre) and on none other; glow fills the leftover area.

GPU cost per refresh as libplacebo's timers report it (windowed half-size, RTX 5070 Ti, D3D11; laptop 1280x800, external 960x540; p50 / p99 ms): resample 0.25 / 0.41 and 0.21 / 0.53; surround (glow) 0.12 / 0.15 both; finish 0.017 / 0.022 and 0.011 / 0.013; prepare 0.03 / 0.28 (counted on the monitor that misses the cache, usually the external); total per refresh about 0.4 ms. Full-size numbers (4x the pixels on the laptop) are not measured; expect about 1 ms for the laptop's resample, to be measured in phase 8. The AMD 610M was not run (no outputs).

## 4. Findings and decisions worth knowing

1. **One `pl_dispatch` per monitor, not one shared.** With a single dispatch shared by both monitors, the first refresh after a shot change on the second monitor drew with a mix of old and new uniforms (wrong crop for one refresh, reproducibly; an extra GPU read-back hid it). libplacebo keeps the last value written per pass uniform and skips unchanged ones; two monitors alternating on one pass with different values broke that. Fixed by a dispatch per monitor (`pic_pass1_job.dp`, `fp_env.dp`), verified by the cut-blend case. This matters for the rest of the performer: anything with uniforms that differ between monitors must not share a dispatch.
2. **Crop edge.** Taps beyond the crop take the crop's edge pixel (the harness's shader read the texture beyond it). The black bars a crop removes can no longer bleed into the picture. The comparison script reproduces this by cutting the source to the crop before the reference render; a fractional crop cannot be compared that way (not asserted).
3. **Blend partner frame** comes from the prepared-frame cache, not the queue (a ZOH queue holds only the frame on screen). The next frame is there because the master services its refresh a whole master refresh before its light; counted if missed (0 in all runs).
4. **Slide point times** are "film seconds at which the picture is at that position"; for a position measured on frame n use the middle of its hold (timestamp + half the duration). Documented in the cue-sheet header; confirm with the planner (phase 5).
5. **Film clock for the slide** uses the engine's current re-aim shift. A re-aim happens at a cut, up to two master refreshes before the old shot's last frame leaves, so those frames get a slide position off by one master refresh (a few mm at 300 mm/s, only at a shot's end). Not corrected.
6. Colour on a monitor with `acm: false` and primaries maps BT.709 to the panel (matrix checked in the unit test, not on a GPU); with `acm: true` (default) it is plain sRGB with the relative tweaks. Video transfer functions are libplacebo's defaults (BT.709 tags decode as BT.1886, then sRGB-encode): the colour policy for SD/HDR remains the user's call.
7. libass uses the system fonts (DirectWrite) plus the film's attachments; the finish harness had used none, which cannot render text.

## 5. Left for phase 8 (by eye, real screens)

- The default surround (glow, gain 0.8, falloff 250 px, time constant 0.5 s, snap at cuts) is the harness's untuned default; compare with `--surround black`, then choose the cut rule and constants.
- Rolling blend vs aimed switch (X2) on the real bezel: `--blend rolling`; it changes what is seen.
- Pan mode on a real slide: needs a planner or a hand-written `slide` path from a measured slide.
- Near-black ramp on the laptop panel (does Windows or the GPU re-quantise our dither under ACM), swapchain format (rgb10a2 default vs bgra8; FP16 scRGB untested).
- Full-screen cost and pacing (present mode, `lead_ms`), D3D11 on both adapters, and a first-subtitle/first-sigmoid shader compile hitch (not warmed up).
- Fold vs curve by eye with the real seat distance in a room file.

## 6. Unresolved questions

1. Surround default: glow (as built, ADR 0008's design) or black (what perf1 showed before)?
2. Slide path time convention (middle of the frame's hold) and whether the planner emits paths per frame or per segment.
3. Should the slide use the re-aim shift of the frame's own start (small correction, finding 5)?
4. `perf1-log.c` is outside my ownership: want the pass costs as a column there instead of `monitor-N-passes.json`?
5. Room placement convention (u from the leftmost monitor's left edge, v down from the master's top) is still the v0 choice; `room_top_mm` derives the follower's top from the room's poses instead of `--height-offset-mm`.
6. `perf1-room.c` is 330 lines, `perf1-finish-subs.c` (port) 401: split now or leave until the prototype settles?

Status: DONE_WITH_CONCERNS
Summary: Prepare, our single resample (fold, curve, crop, per-row slide, optional rolling blend with per-shot views), surround, colour with dither and libass subtitles are built into perf1 and verified against the harness reference (0.46 code max, 10 cases) and the real program windowed with hardware decode, seeks and cue changes.
Concerns/Blockers: Nothing run full screen (cost and pacing at full size unmeasured); headless step 7 of the latch test fails only on the known late-first-refresh gap under load (A/B shows it is independent of this work); the `perf1-log.c` column was not edited for ownership.
