---
title: "Performer requirement x option matrix, refreshed with evidence (M1 phase 02, step 1)"
date: 2026-10-11
type: research
answers: "Phase 02 success criterion 1: requirement x option matrix with evidence for every cell. Now R1-R10 against options A, B, B2, C, D, E and two new ones (E2, F). Does ADR 0022's ranking (C first, B2 fallback) still hold?"
sources: "mpv d94d095 and libplacebo 1dd0226 source (both still the heads of master at 18:22 on 2026-10-11), FFmpeg n9.0.2, GStreamer 1.28.8, VLC master, OBS, Splash, MPC-BE and Kodi source; GitHub and videolan GitLab trackers; local probes (vulkaninfo, pacman -Qi, wc); project reports ENG, P0, P1, S1, Z. Over 100 API and raw-file reads, 9 page fetches, 5 searches; 6 dead ends (section 10)."
---

# Performer requirement x option matrix, refreshed

Run 2026-10-11, 18:20 to 19:00, Asia/Bangkok. Read-only on the repo: nothing installed, no code changed, no window opened. Local probes: `vulkaninfo`, `pacman -Qi` (licenses), `wc -l`, file reads.

## 0. Terms, tags, labels

Terms (plain):
- **Performer**: plays the file and applies the cue sheet on the exact frame. **Presenter**: the part that owns windows and swapchains and decides when each monitor shows a picture.
- **Refresh**: one chance for a monitor to show a new picture. **Swapchain**: the queue of back buffers a window shows from. **Present**: the call that queues one. **Flip model**: Windows' way to show a window's picture without copying it.
- **Top-row time**: when a refresh's first row is drawn on the glass, from scan-line reads. The vblank signal is not it: it marks the end of the previous refresh.
- **Rolling blend** (R8): each row of the faster screen shows the old and the new film frame mixed by that row's own time share. **Shear**: a picture offset that grows with the row number (a camera slide drawn row by row).
- **Adapter**: one graphics chip with its own driver (RTX 5070 Ti, Radeon 610M). **VO**: mpv's video output driver, the part of mpv that draws. **Render context**: libmpv's handle for drawing into a surface the host owns. **`pl_queue`**: libplacebo's frame queue; hands back the film frames around any chosen time.

Evidence tags: **[V]** primary source read this pass (file:line at a named commit, or a fetched doc or tracker page, read 2026-10-11). **[L]** measured or queried on this PC (project reports P0, P1, S1, Z, or a probe run this pass). **[D]** derived by me from [V] and [L] facts. **[S]** snippet, forum post or search summary. **[K]** prior knowledge: none left in the matrix.

Labels: `mpv:` = mpv `d94d095` (2026-10-11 02:19 UTC), `plc:` = libplacebo `1dd0226` (2026-10-10), `ff:` = FFmpeg `n9.0.2` (2026-09-17), `gst:` = GStreamer `1.28.8` (2026-10-08), `vlc:` = VLC `master` (version `4.0.0-dev`) read 2026-10-11. Trackers: `mpv#N` = GitHub issue or PR, `plc!N` = libplacebo merge request, `plc#N` = libplacebo issue (both on code.videolan.org). Reports in `plans/reports/`: `ENG` = researcher-261011-1247-performer-engine-and-picture-path, `P0` = spike-261011-1247-performer-toolchain-p0, `P1` = spike-261011-1552-performer-two-window-p1, `S1` = spike-261011-1247-one-window-per-monitor, `Z` = research-261011-1523-zero-delay-across-scan-speeds, `DES` = design-261011-1247-m1-architecture. Pages (`ms:Present`, `gst:clocks`, `epic:sync`) are in section 9.

Options: **A** stock mpv + generated shader + IPC. **B** mpv fork, multi-window inside `vo_gpu_next`. **B2** mpv core + one new VO driver that is our presenter. **C** own performer (libplacebo + FFmpeg + libass + WASAPI). **D** GStreamer + custom render element. **E** libmpv + own presenter (render API as it is on master). **E2** (new) E if the unmerged D3D11 render API lands (mpv#17764). **F** (new) VLC with its `wall` video splitter.

## 1. Answer

1. **The ranking holds: C, B2, B, D, A (baseline only), E2, E, F.** Nothing found moves an option more than one place.
   - **C got stronger.** P0 and P1 show a working thin presenter on the real stack: libplacebo's own present held 240 Hz for 60 s with 0 late and 0 skipped refreshes, and our own `Present1` did the same over 20 s [L]; the video-only, two-window build is 2.2k lines plus 0.6k of timing headers (against ENG's 2.6k estimate) [L wc].
   - **R8, R9 and R10 do not separate C from B2.** R8 needs the same libplacebo change (or the same app-side workaround) in both; R9 and R10 need our own code in both, because mpv provides neither. They do kill A, D, E, E2 and F on R8, and make B the most expensive.
   - **B2 got more valuable as the fallback.** FFmpeg's Matroska demuxer still ignores ordered chapters and linked segments (`ff:libavformat/matroskadec.c:481,678,689`; no ChapterSegmentUID in the file); GStreamer's skips the segment and previous/next UIDs too (`gst-plugins-good/gst/matroska/matroska-read-common.c:2096-2104`); mpv plays them with 640 lines of its own (`mpv:demux/demux_mkv_timeline.c`). So the per-file mpv fallback in ADR 0022 is required, not optional.
2. **One correction to ENG.** It said a search found "no open work item" for a libmpv D3D11, Vulkan or gpu-next render API. That was incomplete. mpv PR **#17764** (D3D11 texture target, by an mpv maintainer, open since 2026-04-17) and PR **#16818** (gpu-next backend, OpenGL first, draft, changes requested) are open; the Vulkan attempts were closed (#18258 closed by a maintainer 2026-07-13; #18343 closed not-planned 2026-09-17). Master still has only OpenGL and software (`mpv:include/mpv/render.h:468-470`). Both open paths run through one render context per core (`mpv:video/out/vo_libmpv.c:221`), so E and E2 still fail per-monitor geometry. Everything else in ENG held: mpv and libplacebo master have no commits since the pins, and about 25 spot-checked file:line cites are still right.
3. **New rows, one line each** (details in section 4):
   - **R8 rolling blend + shear.** Weights: libplacebo gives each frame one number (`plc:src/renderer.c:4185-4187`); a per-row weight is about 50-90 lines there, or about 100-150 lines app-side with two renders and our own blend pass. Shear: about 40-120 lines in `shaders.c`/`sampling.c`, or a custom resampler hook of about 150 lines. Same cost in C and B2; impossible in A, D, E, E2, F without a fork.
   - **R9 present timing.** C has the pieces (own `Present1` measured; 583 lines of C11 timing headers in progress); B2 can reuse them inside its VO; mpv, GStreamer and VLC present through fixed `Present(…, 0)` or `Present1(0, 0)` calls with no per-monitor refresh-time input.
   - **R10 device per adapter.** libplacebo supports one `pl_d3d11` per adapter by API; frames cross adapters through our code (about 0.6 GB/s for 4K at 24 fps through system memory [D]). Not testable on Setup A: the Radeon 610M has no outputs.
4. **Licence hygiene found.** The MSYS2 `ffmpeg` package is GPL-3.0-or-later and the MSYS2 `lcms2` is built with the GPL-3.0 `fast_float` plugin (`-Dfastfloat=true`, shipped as a separate `liblcms2_fast_float-2.dll`). P0's LGPL FFmpeg build and its 14-DLL closure avoid both; ship only that closure.
5. **Weakest cells:** B2 (never prototyped, P7 not run); C's R1 and R9 at full screen and on Vulkan (P1 full-screen run pending); R10 anywhere (no second-adapter output here); madVR (forum only). No [K] is left.

## 2. Matrix at a glance

Legend: **yes** works as shipped. **write** we write it in code we own. **patch** needs a source change in code we do not own (carry and rebase). **part** partly. **no** blocked or not feasible. **\*** already built in thin form for C (video only, two windows, D3D11, no seek; R9 timing headers in progress). R7 row: **ok** our code's licence stays open; **part** only with an LGPL build of the host; **no** the host's GPL covers our in-process code.

| Req | A | B | B2 | C | D | E | E2 | F |
|---|---|---|---|---|---|---|---|---|
| R1 window, swapchain, present control per monitor | no | patch | write | write\* | part | part | part | part |
| R2 each refresh's time vs film frames | part | patch | write | write | no | no | no | no |
| R3 exact per-frame cue, also under blending | yes | yes | write | write | write | yes | yes | no |
| R4 refine without visible change, also at seeks | part | part | write | write | write | part | part | no |
| R5 one resample, colour last, dither, surround, subs | part | patch | write | write\* | no | no | no | no |
| R6 hw decode, audio sync, subs, seek, rate lock | yes | yes | yes | write\* | part | yes | yes | yes |
| R7 licence stays open | ok | no | no | ok | ok | part | part | ok |
| R8 per-row frame weights + per-row slide offsets | no | patch | write or patch | write or patch | no | no | no | no |
| R9 present timing per monitor (top-row time, chosen refresh) | no | patch | write | write\* | no | part | part | no |
| R10 one device per adapter, shared decode | no | patch | write | write | part | no | no | no |

Ranking (product): **1 C, 2 B2, 3 B, 4 D, 5 A (demo and baseline), 6 E2, 7 E, 8 F.**

## 3. Matrix with evidence

One table per requirement. Citation labels are in section 0; every cell has at least one.

### R1. One window and swapchain per monitor, each paced by its monitor, control over when each presents

| Opt | Verdict | Evidence | Tag |
|---|---|---|---|
| A | no | One window state per VO (`mpv:video/out/vo.h:468-474`); one `Present(sync_interval, 0)` per frame (`mpv:video/out/d3d11/context.c:292`). A window spanning outputs syncs to the output holding most of its area (`ms:Present`) and is composed on the main display's clock: follower offsets -1.7..+6.7 ms (S1 baseline; spike-261011-1034). Maintainer on a second `vo`: "I just don't think it's reasonable to implement, due to mpv's internal design" (mpv#5844, 2019-03-15; closed, the only answer found). | V, L, S |
| B | patch | Seven single-instance sites, 10.7k lines (`wc` at pin): `vo.c` 1,549; `vo_gpu_next.c` 3,012; `w32_common.c` 2,638; `d3d11/context.c` 642 + `gpu/d3d11_helpers.c` 1,277; `gpu_next/context.c` 236; `player/video.c` 1,352. Commits on them in the last 12 months: 12, 53, 20, 3, 4, 2, 8 (GitHub API). The flip swapchain description sets no `Flags` (`mpv:video/out/gpu/d3d11_helpers.c:640-700`). | V, L |
| B2 | write | VO drivers are a static array (`mpv:video/out/vo.c:70-119`) and their sources a static list (`meson.build:254-268`): no plugin API, so a one-line patch carry. A VO may own its window: `vo_sdl.c:845` calls `SDL_CreateWindow` without `w32_common`. Driver hooks `draw_frame`, `flip_page`, `get_vsync` at `vo.h:317-461`. Not prototyped (P7 not run). | V, D |
| C | write\* | `perf1` (P1): two borderless windows, one own flip-model swapchain each, wrapped with `pl_d3d11_create_swapchain(.swapchain)` (`plc:src/include/libplacebo/d3d11.h:154-161,219`), own `Present1`. P1 ran windowed only: 0 skipped frames, 0 queue stalls on both monitors; the full-screen run is pending. The scheme itself in raw DXGI at full screen: both windows in hardware overlay, even pace, 0.28 ms seam offset (S1). | L, V |
| D | part | One swapchain per sink, flip-sequential with 2 buffers (`gst:sys/d3d11/gstd3d11window_win32.cpp:1171-1211`; d3d12 `gstd3d12window-swapchain.cpp:277-278`); `adapter` property and external window handle exist (`gstd3d11videosink.cpp:301`; `gstd3d11window_win32.cpp:92,201`). But every present is `Present1(0, 0)` (`gstd3d11window.cpp:1062`; `gstd3d11window_win32.cpp:1322`; `gstd3d12window-swapchain.cpp:548`), and flip SyncInterval 0 means "cancel the remaining time of the previous frame, discard this one if a newer is queued", not "wait for a refresh" (`ms:Present`). Pacing comes from the one pipeline clock (`gst:clocks`). | V |
| E | part | The host owns window and present, but only via `MPV_RENDER_API_TYPE_OPENGL` or `_SW` (`mpv:include/mpv/render.h:468-470`; backends `vo_libmpv.c:114-117`). One render context per core (`vo_libmpv.c:221`). The render parameters (`render.h:198-424`) carry no per-call source rectangle, so monitors would share one geometry. | V |
| E2 | part | mpv#17764: the embedder renders into an app-owned `ID3D11Texture2D`, "swapchain creation and presentation stay entirely on the caller side". Open since 2026-04-17, last activity 2026-08-23, not merged. It reuses the legacy renderer (`libmpv_gpu.c` +3 lines, new `libmpv_d3d11.c` +109). Its author: "for d3d11 on Windows, window embedding may be better solution. Render api is limited." Same one-context limit. | V |
| F | part | `vlc:modules/video_splitter/wall.c` splits one video into a grid of up to 15x15 outputs, each its own vout window; options are only `wall-cols`, `wall-rows`, `wall-active` (`wall.c:67-71`). The D3D11 display module presents `Present1(0, 0)` (`vlc:modules/video_output/win32/dxgi_swapchain.cpp:360`); the vout waits on the input clock and has no vsync feedback (`vlc:src/video_output/video_output.c:1453-1534`). Master is `4.0.0-dev` (`vlc:meson.build:2`); the site offers 3.0.24. | V |

### R2. Each refresh's time relative to the film frames (pan mode)

| Opt | Verdict | Evidence | Tag |
|---|---|---|---|
| A | part | User shaders get `PTS`, chroma offsets and HDR fields only (`mpv:video/out/gpu/utils.c:58-91`); libplacebo adds a render counter `frame` (`plc:docs/custom-shaders.md:70`; `src/shaders/custom_mpv.c:1363-1368`). The core's `ideal_frame_vsync` is used only when interpolating (`mpv:video/out/vo_gpu_next.c:1355`) and never reaches hooks (`vo_gpu_next.c:2755-2770`). Position from the counter is off by up to one refresh (4 ms at 240 Hz). | V, D |
| B | patch | The core has the value for one display (`vo.h:231-286`; `vo.c:974-975` advances it per repeat); exposing it is about 10 lines, but per-window values need per-window `num_vsyncs` logic: `player/video.c:833` uses one `vo_get_vsync_interval`. | V, D |
| B2 | write | The same fields reach the VO for the master display only; per-monitor film time comes from our clock inside the VO. | V, D |
| C | write | `pl_queue` returns the bracketing frames with timestamps `(frame pts - target pts) / fps.estimate` (`plc:src/utils/frame_queue.c:746-799`). P1 feeds one queue per monitor from one decoder and asks each for its own refresh's film time; with mixing off the queue picks the nearest frame, so a half-frame shift gives "last frame due" and assumes constant frame duration (P1). Pan mode not yet run (P2). | V, L |
| D | no | No property carries a refresh time: `d3d11videosink` has 21 properties, `d3d12videosink` 28 (`gst:…/gstd3d11videosink.cpp:301-555`, `gstd3d12videosink.cpp:346-578`); no `WaitForVBlank`, `GetFrameStatistics` or vsync use in the four window files. | V |
| E | no | One `target_time` per frame, chosen by the core's display-sync (`mpv:include/mpv/render.h:525-541`; `BLOCK_FOR_TARGET_TIME` at `:317`). | V |
| E2 | no | Same API contract; the PR adds a texture target only. | V |
| F | no | Frames leave the vout on the input clock (`vlc:src/video_output/video_output.c:1453-1534`). | V |

### R3. Per-frame cues exact, including under frame blending

| Opt | Verdict | Evidence | Tag |
|---|---|---|---|
| A | yes | Measured: exact-frame switching by the `PTS` parameter and a table texture in the OUTPUT hook (spike-261010-2345); `PTS` auto param at `mpv:video/out/gpu/utils.c:58-91`. | L, V |
| B | yes | Same hook mechanism inside the same renderer (`mpv:video/out/vo_gpu_next.c:2676` loads the hook files; `:2755-2770` feeds `PTS`). | V, D |
| B2 | write | Our code sets crops per frame; libplacebo also parses mpv hook files (`plc:src/include/libplacebo/shaders/custom.h:335`, used by mpv at `vo_gpu_next.c:2676`), so the generated shader still runs. | V, D |
| C | write | Each frame in a mix is rendered with its own image and crop (`plc:src/renderer.c:4033`) and cached by that crop (`:3988-3995,4089`), so per-frame cues survive mixing; direct parameters or the same hook files. Not yet run (P3). | V, D |
| D | write | D3D elements have no shader hook (property lists in R2); the GL route has a user-GLSL element, `gst:subprojects/gst-plugins-base/ext/gl/gstglfiltershader.c`, OpenGL only. A cue mechanism would be our own element. | V |
| E | yes | User shaders run in the legacy renderer (`mpv:video/out/gpu/libmpv_gpu.c:88`). | D |
| E2 | yes | Same renderer (mpv#17764 changes `libmpv_gpu.c` by 3 lines and adds a D3D11 context file). | V, D |
| F | no | The wall splitter has fixed grid cells; no per-frame cue input (`vlc:wall.c`). | V |

### R4. Refinement without changing a shot on screen, also under seeks

| Opt | Verdict | Evidence | Tag |
|---|---|---|---|
| A | part | Refinement by shader swap, no visible change, measured (spike-261011-0950). The latch under seeks is designed, untested. | L |
| B | part | Same shader-swap mechanism as A (spike-261011-0950), inside the same renderer. | L, D |
| B2 | write | The render loop owns the state: one atomic pointer to an immutable cue-sheet version (DES section 4); mpv's core seeks, the latch is ours. | D |
| C | write | Same as B2, in our own loop; P3 pending. | D |
| D | write | Own element (DES section 4 rule applies). | D |
| E | part | As A (spike-261011-0950), through the legacy renderer's user shaders (`mpv:video/out/gpu/libmpv_gpu.c:88`). | L, D |
| E2 | part | As E. | L, D |
| F | no | No cue mechanism (R3). | V |

### R5. One resample per monitor with the geometry in it; colour last, then dither; surround; crisp subtitles

| Opt | Verdict | Evidence | Tag |
|---|---|---|---|
| A | part | The OUTPUT hook runs "after alpha blending, before dithering" (`plc:docs/custom-shaders.md:107`), and only RGB, LUMA, CHROMA, XYZ, NATIVE and MAIN can resize (`:230-236`): geometry at OUTPUT is a second resample, a MAIN-stage hook can make it one. | V |
| B | patch | libplacebo pipeline, but per-window crops need the rectangle logic in `vo_gpu_next.c` (3,012 lines) changed. | D |
| B2 | write | We call libplacebo per monitor. | D |
| C | write\* | Sub-pixel placement in one resample: the destination rect is rounded to whole pixels and the rounding moved into the float source rect (`plc:src/renderer.c:3311-3343`); dither to each target's depth (`:3055-3068`). No per-pixel warp resampler: `distort_params` is a 2x2 affine after scaling (`renderer.h:222-231`; `renderer.c:2815-2860`), so a smooth curve needs a custom MAIN hook. P1 used whole-pixel `target.crop` with matching float `image.crop` (pass 1 only). Cost, RTX D3D11, 1080p to 1440p `hq`: p99 1.13 ms; 4K 1.66 ms; Radeon 610M 19.3 ms (P0). Quality unmeasured (P4). | V, L |
| D | no | Scaling filters are the GPU's point, linear and anisotropic samplers (`gst:gst-libs/gst/d3d11/gstd3d11converter.cpp:88-94`; d3d12 `gstd3d12converter.cpp:48-62`); HDR10 metadata goes to the fixed-function video processor (`gstd3d11converter.cpp:136-137,370-376`), no shader tone mapping found; no libplacebo element among gst-plugins-bad `ext/` in 1.28.8 (directory listing). | V |
| E | no | The legacy `gl_video` renderer has scalers, dither and user shaders (`mpv:video/out/gpu/libmpv_gpu.c:88,190`; `video/out/gpu/video.c`, 4,537 lines), but one geometry per context: the source crop is a global option, not a render parameter. | V |
| E2 | no | Same renderer. | V |
| F | no | VLC 4 has a libplacebo vout (`vlc:modules/video_output/libplacebo/`, Vulkan and OpenGL instances) but the wall splitter gives each output a fixed grid cell. | V |

### R6. Hardware decode, audio and A/V sync, libass subtitles, seeking, rate locking (and the long tail of files)

| Opt | Verdict | Evidence | Tag |
|---|---|---|---|
| A | yes | Complete player. Ordered chapters and linked segments: `mpv:demux/demux_mkv_timeline.c` (640 lines). Display-resample: `player/video.c:707-945`. | V |
| B | yes | Same core. | V |
| B2 | yes | Same core, but D3D11VA zero-copy import must be redone: `hwdec_d3d11va.c:111-115` accepts only mpv's own `ra_d3d11` context (311-line file); P0's own import took about 70 lines. | V, L |
| C | write\* | Done: D3D11VA import (about 70 lines), hardware decoders at 55-75x real time using 0.08-0.28 core (software decode: 47-75x on 7.9-11.6 cores), first frame 0.27 s warm (P0); FFmpeg's D3D11VA context takes our device and downloads frames (`ff:libavutil/hwcontext_d3d11va.h:56-110`; `hwcontext_d3d11va.c:746-748`). Not done: audio and A/V sync, libass (mpv's glue is about 150-180 lines on a 723-line `sub/osd_libass.c`, `mpv:video/out/vo_gpu_next.c:325-360,440-560`), seek, PGS, interlace. Not available in libavformat: ordered chapters and linked segments (`ff:libavformat/matroskadec.c:481,678,689`). About 4k lines [D]. | L, V, D |
| D | part | d3d11 and d3d12 decoders, `wasapi2` and `assrender` exist in 1.28.8 (directory listings). The audio sink slaves audio to the pipeline clock (`gst:gst-libs/gst/audio/gstaudiobasesink.c:91,204`); no display-resample. Matroska demuxing skips SegmentUID, PrevUID and NextUID (`gst-plugins-good/gst/matroska/matroska-read-common.c:2096-2104`) and exposes chapters only as a TOC; `matroska-demux.c` (7,922 lines) has no ordered-chapter or linked-segment code (grep). | V |
| E | yes | The full mpv core sits behind the render context: the host creates a normal mpv handle and adds a render context to its core (`mpv:include/mpv/render.h:26-37,563-565`). | V |
| E2 | yes | Same. | V |
| F | yes | Ordered chapters and linked segments in `vlc:modules/demux/mkv/virtual_segment.cpp:47-86`; WASAPI output in `modules/audio_output/wasapi.c` and `mmdevice.c`. | V |

### R7. Licence of the performer stays open (ADR 0014)

| Opt | Verdict | Evidence | Tag |
|---|---|---|---|
| A | ok | Separate process, so ours stays free; the shipped mpv binary is GPLv2+ (`mpv:Copyright:1-10`). | V |
| B | no | GPLv2+ by default; LGPLv2.1+ only with `-Dgpl=false`, which the file calls "a convenience" and "not a grant" (`Copyright`; `meson.options:2`). `vo_gpu_next.c` and `vo.h` carry LGPL-2.1+ headers, so copying them taints our files. | V |
| B2 | no | Same: the VO runs inside mpv. | V |
| C | ok | libplacebo LGPL-2.1+ (`plc:README.md:229-235`); FFmpeg LGPL-2.1+ unless `--enable-gpl` (`ff:LICENSE.md`), and P0's `--disable-gpl` build reports LGPL; libass ISC, shaderc Apache-2.0, SPIRV-Cross Apache-2.0, glslang BSD-3, dav1d BSD-2, libdovi MIT, HarfBuzz MIT, FreeType GPL-2+ or FTL, FriBidi LGPL-2.1+ (`pacman -Qi`, MSYS2 UCRT64, 2026-10-11). Watch: the MSYS2 `ffmpeg` package is GPL-3.0+; the MSYS2 `lcms2` PKGBUILD passes `-Dfastfloat=true` (GPL-3.0 plugin, "use only if GPL 3.0 is acceptable" in Little-CMS `meson_options.txt`), shipped as separate `liblcms2_fast_float-2.dll`; libplacebo's `icc.c` includes only `lcms2.h` and `lcms2_plugin.h`, and P0's 14-DLL LGPL closure has only `liblcms2-2.dll` (MIT core). | V, L |
| D | ok | GStreamer core `COPYING` is LGPL-2.1; the sink sources carry LGPL-2+ headers (`gst:…/gstd3d12videosink.cpp:1-17`). | V |
| E | part | libmpv carries mpv's GPL or LGPL status (as B). | V |
| E2 | part | Same. | V |
| F | ok | libVLC modules are LGPL-2.1+ (`vlc:wall.c:1-20`); the VLC repository is listed GPL-2.0 (GitHub API). | V |

### R8. Per-row frame weights in one pass (rolling blend) and per-row slide offsets inside the single resample

| Opt | Verdict | Evidence | Tag |
|---|---|---|---|
| A | no | libplacebo gives each mixed frame one weight (`plc:src/renderer.c:4185-4187`); hooks see the mixed image (OUTPUT) or one frame (MAIN). No refresh time (R2) and one window (R1) either. | V |
| B | patch | The libplacebo change of section 4 plus all of B's R1 patches. | D |
| B2 | write or patch | Host-independent: the same libplacebo change or app-side two-render blend as C (section 4). | D |
| C | write or patch | Weights: about 50-90 lines in `renderer.c`, or about 100-150 lines app-side. Shear: about 40-120 lines in `shaders.c`/`sampling.c`, or a custom hook of about 150 lines. Base cost of rerunning the whole pipeline every refresh (crop moved each refresh, `hq`, 720p, RTX D3D11): GPU passes p50 0.30-0.39 ms, p99 0.59-0.79 ms (P0). | V, L, D |
| D | no | No shader hook on the D3D path, no libplacebo element (R5). | V |
| E | no | Mixing lives inside mpv's renderer (`mpv:video/out/gpu/video.c:3372-3590`, `gl_video_interpolate_frame`), not reachable through the API. | V |
| E2 | no | Same renderer. | V |
| F | no | No per-row mix anywhere in the wall splitter or vout. | V |

### R9. Present timing per monitor: top-row time known (scan-line based), control over which refresh a present lands on

| Opt | Verdict | Evidence | Tag |
|---|---|---|---|
| A | no | `Present(sync_interval, 0)` (`mpv:video/out/d3d11/context.c:292`); vsync info comes from `IDXGISwapChain_GetFrameStatistics` (`:322-340`), which marks the blank start; no D3DKMT, scan-line or `WaitForVBlank` use in the tree (grep); one display drives timing (`player/video.c:833`). | V |
| B | patch | `d3d11/context.c:282-293` (present), `gpu/d3d11_helpers.c:640-700` (swapchain flags), `vo.c:905-1030` (`wait_until`, `render_frame`, `flip_page`, `get_vsync`) made per window, plus the timing headers. | V, D |
| B2 | write | Scheduling for all monitors lives in the VO's own threads; the VO reports the master's timeline through `vo_vsync_info` (`mpv:video/out/vo.h:288-316`); reuse C's headers and scheduler. | V, D |
| C | write\* | Own `Present1` on a libplacebo-wrapped swapchain held 240 Hz with 0 late, 0 skipped (P0). Scan-line fit residual p50 0.6 us (laptop) and 2.3 us (external) (Z section 3.1). `scanline-clock.h` (268 lines) and `row-timing.h` (315 lines, growing: 240 at 18:31) are valid C11; `perf1` was being rewired to them at 18:34 and 18:40. libplacebo has no scan-line or vblank API (grep of `src`, `demos`). | L, V |
| D | no | Fixed `Present1(0, 0)` (R1); a patch would be a scheduling API on top of the roughly 40-line `SwapChain::present()` (`gst:…/gstd3d12window-swapchain.cpp:533-571`) and the `GstBaseSink` clock. | V, D |
| E | part | The host controls when it presents, but the core picks the frame for one `target_time` (R2). | V |
| E2 | part | Same, with D3D11. | V |
| F | no | `Present1(0, 0)` and clock waits (R1). | V |

### R10. One rendering device per graphics adapter; monitors on two adapters; shared decode

| Opt | Verdict | Evidence | Tag |
|---|---|---|---|
| A | no | One device: `--d3d11-adapter` is a single string (`mpv:video/out/d3d11/context.c:69-70`), one `ra_ctx` and `pl_gpu`, hwdec bound to it (`hwdec_d3d11va.c:111-115`). | V |
| B | patch | A second `ra_ctx`, `pl_gpu`, swapchain and hwdec in `gpu_next/context.c` (236 lines) and the state of `vo_gpu_next.c` (3,012 lines); 2 and 53 commits a year on those files. | V, D |
| B2 | write | The VO owns its devices; mpv's decoder gives hardware frames only on mpv's own device (`hwdec_d3d11va.c:111-115`), so the VO needs its own hwdec glue or software frames; cross-adapter hand-off is ours. | V, D |
| C | write | `pl_d3d11_create` takes `.device`, `.adapter` or `.adapter_luid` (`plc:src/include/libplacebo/d3d11.h:47-56`), so one per adapter. The D3D11 `pl_gpu` is not thread-safe (`thread_safe` is set only in `src/vulkan/gpu.c:527` and `src/opengl/gpu.c:183`): one render thread per adapter. The D3D11 backend has no shared-handle import or export (no `PL_HANDLE_*` in `src/d3d11/`), so frames cross adapters in our code: FFmpeg's D3D11VA takes our device and downloads frames (`ff:libavutil/hwcontext_d3d11va.h:56`; `.c:746-748`); 4K P010 at 24 fps is about 0.6 GB/s through system memory [D]; or decode on both (hardware decode costs 0.08-0.28 core, P0). iGPU render cost, 1440p: `fast` 1.4 ms, `default` 8.6 ms, `hq` 19.3 ms (P0). Untested: the Radeon 610M has no outputs here. | V, L, D |
| D | part | Both sinks have an `adapter` property (`gst:…/gstd3d11videosink.cpp:301`; `gstd3d12videosink.cpp:346`); a `tee` shares one decode; `d3d11upload` and `d3d11download` elements exist. Each branch still has the R1, R2 and R5 gaps. | V |
| E | no | One render context per core on the host's one GL context (`vo_libmpv.c:221`). | V |
| E2 | no | `MPV_RENDER_PARAM_D3D11_INIT_PARAMS` carries one device (mpv#17764 text). | V |
| F | no | The wall splitter has no adapter or screen option (`vlc:wall.c:67-71`). | V |

### Meta rows: effort, upkeep, adoption risk

| | A | B | B2 | C | D | E / E2 | F |
|---|---|---|---|---|---|---|---|
| Effort | done | weeks plus rebase; 10.7k coupled lines (R1) | about 3k lines plus mpv build [D] | thin version 2.8k lines built (perf1 2,205 + headers 583, wc 18:55); full about 7.6k [D, ENG] | high: libplacebo element plus sink patches [D] | E blocked on master; E2 needs mpv#17764 merged | n/a |
| Upstream churn on files touched, 12 months to 2026-10-11 | none | `vo_gpu_next.c` 53, `w32_common.c` 20, `vo.c` 12, `video.c` 8, `vo.h` 6 | `vo.c` 12 (registration only), `meson.build` | libplacebo only if patched: `renderer.c` 30, `sampling.c` 4, `shaders.c` 2, `frame_queue.c` 6, `d3d11/swapchain.c` 6 | n/a | mpv#17764 is one commit | n/a |
| Adoption signals | mpv: last tag 0.41.0 on 2025-12-21 (294 days), 250 commits in 90 days, 205 by one committer (GitHub API; committer, not author) | | | libplacebo: last tag 7.360.1 on 2026-03-13 (212 days), master is API 375 against tag 360; 221 commits in 12 months by Kacper Michajlow 118, Niklas Haas 76, llyyr 22; 30 open merge requests; FFmpeg 9.0.2 on 2026-09-18 and 8.1.3 on 2026-09-21; libass 0.17.5 on 2026-06-24 | GStreamer 1.28.8 on 2026-10-08, 1.29.2 dev | | VLC: stable 3.0.24 (tag 2026-09-17), master `4.0.0-dev` |
| Constraint | mpv master needs libplacebo >= 7.360.1 (`mpv:meson.build:30`) | | | libplacebo tag 7.360.1 has float timestamps; master (API 375) has double (P0) | | | |

## 4. What it takes: R8, R9, R10 per option

Patch locations are at the pins; sizes are my estimates from reading the code [D], not prototypes.

| Opt | R8 rolling blend + shear | R9 present timing | R10 device per adapter |
|---|---|---|---|
| A | Not possible without a fork: no refresh time, one window. | Not possible: fixed `Present(…, 0)`, one display clock. | Not possible: one device. |
| B | Section below, plus about 30 lines in `vo_gpu_next.c` to pass row-time parameters. | About 600-1,000 lines across `d3d11/context.c`, `gpu/d3d11_helpers.c`, `vo.c`, `video.c` made per-window, plus the timing headers. | At least 1,000 lines: second `ra_ctx`/`pl_gpu`/hwdec across `gpu_next/context.c` and `vo_gpu_next.c`. |
| B2 | Same libplacebo work as C. | VO threads call `Present1`; port the scheduler from `perf1` (1,346-line `perf1-main.c`, scheduling not yet separated) plus 583 lines of headers: about 400-800 lines. | Own devices in the VO, own hwdec glue (about 70 lines as in P0) tied into `mp_hwdec_devices`, cross-adapter copy: about 300-500 lines. |
| C | Section below. | Remaining: wire the headers into the scheduler (in progress), full-screen validation: about +100-200 lines. If D3D11 cannot hold the offset, wrap our own `VkSwapchainKHR` with `pl_vulkan_wrap` and `VK_EXT_present_timing` (about 400-600 lines); libplacebo's only present is `vkQueuePresentKHR` at `plc:src/vulkan/swapchain.c:1004`, with no present id, wait or timing. | Adapter enumeration, one `pl_d3d11` and render thread per adapter, frame hand-off by download and upload: about 300-500 lines. |
| D | A libplacebo-based element and sink patches: not worth it. | Not worth it. | Exists in part (R10 table), but the branches fail R1, R2, R5. |
| E / E2 | A new per-call source rectangle in the render API and a change to mpv's renderer (`gl_video_interpolate_frame`): an mpv fork. | We present, but the core picks one frame. | Not possible: one context. |
| F | Not worth it. | Patch `dxgi_swapchain.cpp:360` and the vout clock path. | Not worth it. |

**R8 weights (libplacebo patch).** `plc:src/renderer.c:3913-3935` computes `weight = (end - rts) / vsync_duration` from each frame's visible interval; `:4105-4106` stores it; `:4185-4187` multiplies the cached frame texture by that one number (`SH_FLOAT_DYN(weight)`). Change: pass each frame's start and end plus a row-time function (top-row offset, line time, refresh period, scan direction) as dynamic constants, and compute `clamp(min(end, t_row + T) - max(start, t_row), 0, T) / T` in GLSL from the row; keep the CPU culling at `:3931-3966` conservative. `pl_queue` needs no change: `oversample()` already returns the bracketing pair (`frame_queue.c:746-799`) and at 24 fps the window (scan 3.7 ms + refresh 4.2 ms on the laptop, Z section 5.1) never reaches a third frame. About 50-90 lines plus header fields and a test. `renderer.c` had 30 commits in 12 months, so expect rebases.

**R8 weights (app side, no libplacebo change).** Two `pl_render_image` calls (frame k and k+1, each with its own crop, which `renderer.c:3988-3995,4033` already assumes) into linear FP16 canvases, then pass 3 blends with per-row weights: about 100-150 lines. One extra picture pass on the 2-3 refreshes around each film-frame change where any row blends.

**R8 shear (libplacebo patch).** The sampling position is a vertex attribute built from a rectangle (`plc:src/shaders.c:513-583`, `sh_bind` and `sh_attr_vec2("tex_coord", …)`), so a row-linear offset is one extra corner term carried in `struct pl_sample_src` and applied in `setup_src` (`sampling.c:~100-190`). The polar compute path sizes its shared-memory tile from `bw / rx` and `bh / ry` (`sampling.c:~660-690`) and would need `+shear x bh` columns, or fall back to the fragment path. About 40-120 lines. Low churn: `shaders.c` 2 and `sampling.c` 4 commits in 12 months.

**R8 shear (app side).** A custom MAIN-stage hook with its own resampler, sized to the monitor so the stock scaler sees 1:1 (`plc:docs/custom-shaders.md:230-236`): about 150 lines of GLSL. Or `distort_params` today: a second resample, bilinear or bicubic, applied once on the mixed image (`renderer.c:2815-2860`; cleared from the per-frame hash at `:3804`).

## 5. Option C: what P0 and P1 measured

| Req | What was measured | Result | Report |
|---|---|---|---|
| R1, R9 | libplacebo's own `Present(1, 0)`, 60 s, windowed 1280x800 on the 240 Hz laptop, software and D3D11VA decode | 14,399 refreshes: 0 late, 0 skipped; longest loop interval 5.2 ms; 1,437 of 1,439 film frames shown for exactly 10 refreshes | P0 |
| R1, R9 | Our own waitable flip-model swapchain, own `Present1`, same setup | 0 late, 0 skipped over 20 s; loop interval p99 5.0 / 4.5 ms (software / D3D11VA), max 5.5 ms; two 15 s repeats had 2 and 3 late intervals (max 6.7 and 10.1 ms): "a little more jitter than libplacebo's own present, no lost refresh" | P0 |
| R1, R2 | `perf1`: two borderless half-size windows, D3D11VA, one `pl_queue` per monitor, composed mode | 0 skipped frames, 0 queue stalls; 25 fps film shows 9-10 refreshes on 240 Hz and 4 on 100 Hz; render CPU p50 0.027 ms, p99 0.28 ms; GPU passes p50 0.076 ms, p99 0.34 ms; lead before target 3.8-4.2 ms. Artefacts of windowed mode: the 100 Hz window is presented at 240 Hz (2.4 `Present1` per refresh), so its offsets mean little; DISPLAY1 reached overlay in about 90% of polls. Full-screen run pending. | P1 |
| R5 cost | One 1080p frame to 1440p, `hq` (EWA, deband), every cache bypassed | RTX D3D11 p50 0.82 / p99 1.13 ms; Vulkan 0.99 / 1.43; 4K 1.36 / 1.66 (D3D11); Radeon 610M 19.0 / 19.3 ms (`default` 8.4 / 8.6, `fast` 1.35 / 1.43) | P0 |
| R8 base cost | Crop moved 1/1000 px every refresh so everything reruns, `hq`, 720p | GPU passes p50 0.30-0.39 ms, p99 0.59-0.79 ms; GPU 3D engine 13-14% against 5-6%; 0 late | P0 |
| R6 | Decode only | Hardware 55-75x real time on 0.08-0.28 core; software 47-75x on 7.9-11.6 cores; D3D11VA import about 70 lines plus `SetMultithreadProtected`; first frame 0.27 s warm (0.41-0.46 cold) | P0 |
| R7 | LGPL FFmpeg 9.0.2 built from signed source with `--disable-gpl` | Reports LGPL-2.1+; keeps `d3d11va`, `d3d12va`, `dxva2`, `vulkan`; player closure 14 non-Windows DLLs, 42 MiB against 100 DLLs, 149 MiB with the GPL package | P0 |
| Toolchain | MSYS2 UCRT64 portable under the user profile; libplacebo master `1dd0226` builds in 9.5 s; libplacebo's FFmpeg helpers compile and work against FFmpeg 9.0.2 (libavutil 61) | Answers ENG open questions 1 and 2; MSYS2's libplacebo 7.360.1 also works with two version guards | P0 |
| Vulkan | libplacebo's Vulkan swapchain | Bursty at default depth (loop interval 0.1-10 ms, frames shown 9-12 refreshes); depth 1 gives 0 skipped but p99 6.6 ms; hitches of 300-720 ms in 2 of 12 runs; degraded later in the day; D3D11 unaffected. Start on D3D11. | P0 |
| Size | Source lines at 18:55 | `perf0` 2,418; `perf1` 2,205 (+ `scanline-clock.h` 268, `row-timing.h` 315): the M1 exit prototype is about 7% over ENG's 2.6k | wc |
| Not measured | R3, R4 (P3), pan mode (P2), picture quality (P4), audio (P5), subtitles (P6), seek, ordered chapters, second adapter, full-screen overlay with the wrapped swapchain | open | P0, P1 |

## 6. What changed since the engine research

1. **libmpv render API:** master unchanged (GL and software). New in the trackers: mpv#17764 (D3D11, legacy renderer, open) and mpv#16818 (gpu-next, OpenGL, draft). Vulkan: mpv#18258 closed 2026-07-13 ("Not really salvageable", authorship), the maintainer asked for vo_gpu_next to be ported to `ra` primitives first; mpv#18343 closed not-planned 2026-09-17.
2. **Masters did not move.** mpv `d94d095` and libplacebo `1dd0226` are still the heads on GitHub and on code.videolan.org (libplacebo's canonical repository; GitHub is a mirror) at 18:22.
3. **libplacebo open merge requests that matter** (30 open): !834 Vulkan swapchain fence sync (a possible lever for P0's Vulkan jitter; untested); !892 generic compositor API (several inputs onto one output, +611 lines; not several outputs); !826 adapter selection by type (we pass `adapter_luid` ourselves); !876 draft motion-estimation interpolation. Nothing on present timing, tearing or per-row mixing; issue plc#143 "Presentation feedback / video timing" has been open since 2021.
4. **mpv master now needs libplacebo >= 7.360.1** (`mpv:meson.build:30`); ENG quoted 6.338.2 from the 0.41 notes.
5. **FFmpeg:** 9.0.2 (2026-09-18) is current, 8.1.3 (2026-09-21), no 9.1 release (`n9.1-dev` on master). D3D11VA and D3D12VA hwcontext headers present. Ordered chapters still unsupported.
6. **GStreamer 1.28.8** (2026-10-08, development 1.29.2): sinks read; they present with `Present1(0, 0)`, expose no timing, no shader hook; scaling is hardware sampler filters only.
7. **VLC:** master is `4.0.0-dev`; the website offers 3.0.24; the `wall` and `clone` splitters exist in both 3.0.x and master; D3D11 presents `Present1(0, 0)`.
8. **Licence hygiene** (section 1, point 4).
9. **P0 and P1 results** replace ENG's spike targets with numbers (section 5).
10. **Churn and bus-factor numbers** are new (meta table).
11. **A sibling spike is running:** `spikes/file-matrix` (51 cases including `mkv-ordered-chapters-internal.mkv` and `mkv-ordered-chapters-linked`, at case 7 when checked at 18:54) will turn the ordered-chapter finding from source-read [V] into measured [L]; `spikes/clock-sim` has a `rolling-blend.py`. Neither was used here.

## 7. Other players and libraries that present per monitor

Searched: GitHub repository searches (video wall, multi-display player, synchronized playback, projection mapping), code searches in the named projects, five web searches (video walls, madVR, OpenVideoWalls, nDisplay, Vulkan present timing). **Found no open-source player or library that gives each monitor its own refresh-aware presentation from one film clock.** Closest, with the reason each is out:

| Project | What it does | Licence | Per-monitor refresh-aware pacing? | Out because | Evidence |
|---|---|---|---|---|---|
| mpv display-resample | Locks speed so the display rate is a multiple of the film rate | GPL or LGPL | One display only | It is options A-E2 | V `player/video.c:833` |
| VLC 3.0.24 / 4.0-dev `wall` and `clone` splitters | One vout window per grid cell | LGPL-2.1+ module, GPL-2.0 repo | No: clock wait, `Present1(0, 0)` | 4.0 unreleased; not refresh-aware (option F) | V |
| Kodi | Vblank reference clock thread for the display in use | GPL-2.0+ | One display | One display, GPL. Learn: a vblank reference clock | V `xbmc/windowing/windows/VideoSyncD3D.cpp:92` (`IDXGIOutput::WaitForVBlank`) |
| MPC-BE | D3D9Ex presenter that estimates refresh rate and scan lines per second from raster status | GPL-3.0 | One display | One display, D3D9, GPL. Learn: precedent for a scan-line clock (R9) | V `src/filters/renderer/VideoRenderers/DX9AllocatorPresenter.cpp:295-349,1017` (`GetRasterStatus`) |
| OBS Studio projectors | N display windows rendered from one graphics thread at the OBS frame rate | GPL-2.0 | No | Not refresh-aware, GPL | V `libobs/obs-video.c:94,1126,1139`; `libobs-d3d11/d3d11-subsystem.cpp:2415` `Present(interval, 0)` |
| Splash (video mapping) | OpenGL, up to 8 outputs on 2 GPUs, single computer | GPL-3.0 | No: assumes equal refresh rates; window 0 waits for vsync, the others draw to the front buffer; NV swap groups on Quadro | GPL, OpenGL, equal-rate assumption; last release 0.10.8 (2024-03-05). Learn: one-clock software workaround | V `src/graphics/api/opengl/window_gfx_impl.cpp:115-160` |
| GStreamer D3D sinks | N sinks on one clock | LGPL | No | Option D | V |
| Unreal nDisplay | Cluster rendering | Engine source under Epic's licence | Hardware: "requires NVIDIA Quadro graphics cards, plus Sync II cards, plus a genlock generator"; software Ethernet policy "tearing can still be possible among the displays because of Windows DWM"; refresh-rate mixing not mentioned | Reference only | V `epic:sync` (page fetched; quotes from the fetch digest) |
| madVR | Closed video renderer | Closed | One display (refresh follows the monitor the window opened on; frame drops when moved across refresh rates) | Closed; reference only | S doom9 forum posts via search |
| Commercial video-wall servers (WATCHOUT, disguise) | Hardware genlock or framelock | Closed | Never mix rates | Reference only (presentation research Q5) | S |
| OpenVideoWalls (ACM MMAsia 2024) | Raspberry Pi per display, server timestamps | Paper; no code found | Heterogeneous displays, network clocks | Not Windows, no code; paper page unreachable (connection refused) | S |
| N mpv processes + IPC | The workaround in mpv#5844 | GPL | No: "not quite in sync since it's not controlled by the same processes" | No shared film clock; mpv#1272 (synchronized playback) is still open | S |
| Chromium, Firefox, wlroots and KWin | Not examined. Windows exposes one composition clock to apps (`DwmGetCompositionTimingInfo` is main-display only; measured in the presentation research). | | | Not embeddable as a player | not checked |

One patent flagged for later: Intel **US12342034B2** "Multi-display video synchronization" (priority 2021-12-20, granted 2025-06-24, listed active, expiry 2043 by Google's estimate): a secondary system adjusts its VSYNC period from a primary's VSYNC timestamps (two 60 Hz displays in the example). Our scheme keeps one PC and does not adjust periods, but the claims were not read (only the first 100,000 characters of the page, which ended before the claims). The Cisco patent US11195452B1 from Z section 5.4 is a separate item.

## 8. Ranking check

| Rank | Option | Why it stays |
|---|---|---|
| 1 | C | Wins R7, upkeep (no mpv build, no GPL, no rebase of 53 commits a year) and has a measured presenter. Loses R6: about 4k lines of glue and the ordered-chapter class. |
| 2 | B2 | Free R6 (audio, A/V sync, subtitles, seek, ordered chapters); ties C on R8-R10 because mpv gives neither R9 nor R10 and R8 is a libplacebo change; costs GPL, a patch carry on `vo.c` (12 commits a year) and mpv's build. |
| 3 | B | B2 plus a rebase of `vo_gpu_next.c`, `w32_common.c` and friends. |
| 4 | D | Fails R2, R5, R8, R9; closing them means a libplacebo element plus sink patches, i.e. C inside GStreamer. |
| 5 | A | Working baseline and the plain-stretched comparison; no per-monitor presentation. |
| 6-8 | E2, E, F | E and E2: one context, one geometry, one frame time per core. F: unreleased, not refresh-aware. |

Triggers that would reorder: (1) P5 plus P6 cost more than 3 weeks (ADR 0022's gate) puts B2 first; (2) P1 full-screen fails for engine reasons opens P7; (3) mpv#17764 and #16818 merged and extended with a per-call source rectangle would re-rate E2 (not on today's evidence); (4) a high share of ordered-chapter files in the user's library (file-matrix) raises the weight of R6 for B2.

## 9. Sources

All fetched or run 2026-10-11 unless a date is given.

Pinned source (read-only clones in a sibling session's scratchpad, depth 1; heads re-checked with `gh api repos/…/compare/<pin>...master` = identical at 18:22):
- mpv `d94d095`: https://github.com/mpv-player/mpv
- libplacebo `1dd0226`: https://code.videolan.org/videolan/libplacebo (canonical; head confirmed through its API) and https://github.com/haasn/libplacebo (mirror)
- FFmpeg `n9.0.2` raw files: https://raw.githubusercontent.com/FFmpeg/FFmpeg/n9.0.2/ (`libavformat/matroskadec.c`, `matroska.h`, `libavutil/hwcontext_d3d11va.[ch]`, `hwcontext_d3d12va.[ch]`, `Changelog`, `LICENSE.md`)
- GStreamer `1.28.8` raw files: https://raw.githubusercontent.com/GStreamer/gstreamer/1.28.8/subprojects/ (`gst-plugins-bad/sys/d3d11`, `sys/d3d12`, `gst-libs/gst/d3d11`, `gst-libs/gst/d3d12`, `gst-plugins-good/gst/matroska/matroska-demux.c`, `gst-plugins-base/gst-libs/gst/audio/gstaudiobasesink.c`, `gstreamer/COPYING`)
- VLC master raw files: https://raw.githubusercontent.com/videolan/vlc/master/ (`meson.build`, `modules/video_splitter/wall.c`, `modules/video_output/win32/dxgi_swapchain.cpp`, `src/video_output/video_output.c`, `modules/demux/mkv/virtual_segment.cpp`)
- Others: OBS `libobs/obs-video.c`, `libobs-d3d11/d3d11-subsystem.cpp`; Splash `src/graphics/api/opengl/window_gfx_impl.cpp`, `src/graphics/rendering_context.cpp`; MPC-BE `src/filters/renderer/VideoRenderers/DX9AllocatorPresenter.cpp`; Kodi `xbmc/windowing/windows/VideoSyncD3D.cpp`; Little-CMS `meson_options.txt`, `plugins/fast_float/src/fast_float_internal.h`; MSYS2 https://raw.githubusercontent.com/msys2/MINGW-packages/master/mingw-w64-lcms2/PKGBUILD

Trackers:
- mpv: https://github.com/mpv-player/mpv/pull/17764, /pull/16818, /pull/18258, /issues/18343, /issues/5844, /issues/1272
- libplacebo (GitLab): https://code.videolan.org/videolan/libplacebo/-/merge_requests/834, /892, /826, /876 and /-/issues/143, /324 (issue comments need a login: not read)
- Releases: https://ffmpeg.org/download.html, https://gstreamer.freedesktop.org/releases/, https://www.videolan.org/vlc/, `gh api repos/{mpv-player/mpv,haasn/libplacebo,libass/libass}/releases`, `…/tags`

Pages: `ms:Present` https://learn.microsoft.com/en-us/windows/win32/api/dxgi/nf-dxgi-idxgiswapchain-present (updated 2024-02-22); `gst:clocks` https://gstreamer.freedesktop.org/documentation/application-development/advanced/clocks.html; `epic:sync` https://dev.epicgames.com/documentation/unreal-engine/synchronization-in-ndisplay-in-unreal-engine; Splash README https://gitlab.com/sat-mtl/tools/splash/splash/-/raw/develop/README.md; Intel patent https://patents.google.com/patent/US12342034B2/en; OpenVideoWalls and madVR through web search snippets only.

Local: `vulkaninfo` (18:39: RTX 5070 Ti Laptop, driver 617.42, Vulkan 1.4.351, lists `VK_EXT_present_timing` rev 3, `VK_KHR_present_id`, `present_wait`, `swapchain_maintenance1`; Radeon 610M driver 2.0.353 lists none of them); `pacman -Qi` licences; `wc -l`; project files under `spikes/` and `plans/reports/` (P0, P1, S1, Z, ENG, DES, ADR 0022, phase-02 plan).

## 10. Unresolved questions and limits

Unresolved:
1. Does the libplacebo-wrapped swapchain reach hardware overlay at full screen on both monitors, and does the offset hold with `row-timing.h`? (P1 full-screen run, controller.)
2. R8: libplacebo patch (carried, about 50-90 + 40-120 lines) or the app-side workaround (about 100-150 + 150 lines)? Decide at P2 and P4 once the by-eye test of the rolling blend (Z X2) says the feature stays.
3. R10: needs Setup B or another machine with a monitor on the second adapter. Copy through system memory or decode twice?
4. Does `VK_EXT_present_timing` work in a borderless window on Windows with driver 617.42? Advertised, never exercised; libplacebo's Vulkan swapchain would have to be replaced for it.
5. Does libplacebo MR !834 (presentation-fence sync) change P0's Vulkan jitter?
6. How many of the user's files use ordered chapters or linked segments? Sizes the C-versus-B2 long tail; the `file-matrix` run will show how perf0 and mpv behave.
7. Ship lcms2 as an MIT-only build, or drop ICC support in the first release?
8. Freedom to operate: read the claims of Intel US12342034B2 (and Cisco US11195452B1 for the rolling blend).
9. Will mpv#17764 or #16818 move? Check once more at the M1 exit; no action before.

Limits:
- B2 has never been built (P7 not run): every B2 cell is [D] on top of [V] interface reads.
- Sizes and line counts of patches are estimates from reading code, not prototypes.
- C cells for R1 and R9 rest on windowed P1 runs, raw-DXGI full-screen S1 runs and P0's single-window runs; none yet covers the libplacebo-wrapped swapchain at full screen.
- GStreamer and VLC were read, not run. VLC's R6 claim rests on file presence and function names, not a playback test.
- madVR, nDisplay and OpenVideoWalls rest on forum posts, vendor docs or snippets.
- Browser engines were not examined.
- Not re-checked because no matrix cell depends on them: the audio library choice in ENG Q4 (WASAPI direct versus cubeb, miniaudio, SDL3: their clock and licence claims there stay [K] and [S]) and the Rust crate status in ENG Q3.
- Commit counts are by committer, not author: they measure who merges.
- `perf1-main.c`, `row-timing.h` and the `spikes/file-matrix` and `spikes/clock-sim` folders were being edited by another agent during this pass (P1's report is from 15:52); no cell relies on code that was changing, except where marked "in progress".
- Dead ends: GitLab notes API (401, issue comments in plc#143 and plc#324 unreadable); gitlab.com API for Splash (non-JSON, used the GitHub mirror and README); par.nsf.gov (connection refused); first multi-word `gh search issues` queries (empty, switched to the REST search endpoint); wrong VLC module paths (404); the Google Patents page for US12342034B2 ended before its claims.
