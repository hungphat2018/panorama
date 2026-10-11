---
title: "Performer engine and picture path (M1 phases 02 and 03)"
date: 2026-10-11
type: research
answers: "M1 phase 02 (which engine runs the performer) and phase 03 (how the picture path runs inside it)"
sources: "Source read: mpv d94d095 and libplacebo 1dd0226 (both 2026-10-10, shared clones), spike reports, ADRs 0002/0006/0012/0014/0016/0017/0019. Web: 33 network calls = 31 fetch/search + 2 byte-range reads of one zip tail; 6 were dead ends (4 x 404, 1 bot-blocked, 1 range 501). Local probes on this PC (read-only): vulkaninfo, tool inventory, ffmpeg -version."
---

# Performer engine and picture path

Terms (plain): the **performer** plays the file and applies the cue sheet on the exact frame, deciding nothing. A **presenter** is the part that owns the windows and swapchains and decides when each monitor shows a picture. A **refresh** is one chance for a monitor to show a new picture.

Evidence tags: **[V]** verified this pass (fetched primary page, or source file:line read at the pinned commit); **[S]** search snippet only; **[K]** prior knowledge, not re-verified; **[L]** local probe on this PC; **[D]** derivation. Source paths: `mpv:` = mpv d94d095, `plc:` = libplacebo 1dd0226.

## 1. Answer

1. **Product engine: build our own performer (option C).** A C11 program on libplacebo (renders) + FFmpeg (decode) + libass (subtitles) + WASAPI (audio), in which *our* code owns one window, one swapchain and the `Present` call per monitor. Ranked first of six. Total about 7.6k lines of C [D]; the first thin version (video only, two windows, no seek) is about 2.6k lines and is exactly the M1 exit prototype.
2. **Demo engine: the same one, behind gates.** Build the M1 prototype on it; if spikes P0 to P3 pass (section 6), the M2 demo is built on it (audio, subtitles, seek added in M2). Until then **stock mpv (A) stays** as the working baseline, the fallback, and the "plain stretched picture" comparison arm. Stock mpv cannot give per-monitor presentation (measured in the mixed-rate spike, and by construction in the source) and can only approximate per-refresh motion.
3. **Why not mpv as the host.** (a) libmpv's render API is OpenGL or software only, on the legacy renderer, unchanged on master [V]. (b) The gpu-next VO is single-window, single swapchain, single vsync interval at seven coupled sites, about 10k lines [V]; its D3D11 swapchain has no tearing or waitable flags [V]. (c) Shaders see only `PTS` plus a render counter `frame`, never the refresh time [V]. A multi-window fork is GPL (or LGPL via `-Dgpl=false`) and must be rebased on every release.
4. **Fallback if the own-player glue (audio, sync, subtitles, seek) overruns: B2** = mpv core (decode, audio, sync, subs, seek, IPC) plus one new VO driver that is our presenter. The presenter module is the same code in C and B2, so building it first loses nothing. Gate (suggested budget [D]): if audio plus subtitles plus seek cost more than 3 weeks of spike time, switch.
5. **libplacebo covers the picture path except six gaps** [V]: no per-pixel warp resampler (only affine distort, bilinear/bicubic); no present-timing or tearing control in the D3D11 swapchain (`Present(1,0)` is hard-coded); no libass glue; no D3D11VA import in its libav helpers (mpv's ~150-line import must be re-derived); D3D11 `pl_gpu` is not thread-safe (Vulkan is); the border blur is not dithered. All six are small and have workarounds (sections 3 and 4).
6. **Rejected:** E (libmpv hybrid: blocked by the render API), D (GStreamer: not refresh-aware, no shader hook, would need a libplacebo element anyway), VLC (4.0 not stable as of July 2026 [S]), N mpv instances (no frame-exact sync). B (fork vo_gpu_next in place) ranks third: possible, but the most invasive.
7. **Freshness flags.** FFmpeg **9.0** is current (tag commit 2026-08-03; 9.0.2 on 2026-09-18; 8.1.3 on 2026-09-21), so "8.x" is one major behind [V]. mpv's last tag 0.41.0 is 9.7 months old; libplacebo's last tag 7.360.1 is 7 months old while master is at API 375 [V]. Rust: no maintained libplacebo binding (`libplacebo-sys` last 2019); `rsmpeg` stops at FFmpeg 8.0 [V].
8. **Language: C (C11) for the performer; Rust stays fine for planner, room and analysis.** libplacebo's API is built on C99 compound-literal macros that Rust cannot call and MSVC C++ rejects. **Build route:** MSYS2 UCRT64 (libplacebo 7.360.1-2 is packaged today) or clang-cl; MSVC `cl` is untested upstream. **License:** dynamic-linking LGPL libs keeps the performer license free (R7); copying mpv code into our files makes those files LGPL, so re-derive instead of paste.

**Decision rule:** start P0 now (1 to 2 days). The by-eye seam threshold the user still owes (`seam-pairs-laptop.mkv`) decides only whether the spanning-window demo (A) is *acceptable* for non-pan motion; it does not change the product direction, because Setup B (three screens, a 60 Hz UltraFine) needs per-monitor presentation anyway.

## 2. Requirement-by-engine matrix

Options: **A** stock mpv + generated shader + IPC. **B** mpv fork, multi-window inside `vo_gpu_next`. **B2** (added) mpv core + one new VO driver that is our presenter. **C** own performer. **D** GStreamer + custom render element. **E** libmpv + own presenter.

| Req | A | B | B2 | C | D | E |
|---|---|---|---|---|---|---|
| R1 per-monitor window, swapchain, present control | **no** [V spike; mpv single window] | yes, invasive (7 sites) [V] | yes [D] | **yes** [V: own Present, `pl_d3d11_wrap`] | partial: N sinks on one clock, not refresh-aware [K] | **no**: render API GL/SW only [V] |
| R2 per-refresh render with refresh time | **spike**: `frame` counter exists [V], true offset not exposed [V], redraws inflate count [V] | yes: core has `ideal_frame_vsync` [V] | yes | **yes**: `pl_queue` timestamps [V] | no [K] | no |
| R3 exact per-frame cue | **yes** (spiked) [V] | yes | yes | yes: same hook shader or direct params [V] | only inside own element | n/a |
| R4 refine + latch on seek | refine **yes** (spiked); seek latch **spike** (designed, untested) | yes [D] | yes [D] | yes: render loop owns state, atomic version pointer [D] | own element | n/a |
| R5 one resample per monitor, color last, dither, glow, subs, EWA, tone map | **partial**: canvas pass, 2nd resample unless MAIN-stage; dither after OUTPUT ok [V] | yes [V] | yes | **yes**; curve warp needs custom EWA hook (gap) [V] | no: D3D elements lack EWA, tone map [K] | legacy GL renderer [V] |
| R6 hw decode, audio, A/V sync, subs, seek, 23.976 lock | **yes** | yes | yes (hwdec import must be redone in the VO [V]) | **build** (~4k lines; reference code in tree) [D] | mostly (no display-resample) [K] | mpv parts yes, but no GPU frames out [V] |
| R7 license of performer | process boundary: free; mpv is GPL [V] | GPL (or LGPL `-Dgpl=false`) [V] | same | **free** (LGPL libs, dynamic) [V/K] | LGPL core [K] | libmpv GPL/LGPL |
| Effort | done | weeks + rebase | ~3k lines + mpv build infra | ~7.6k lines (2.6k for M1) | high | blocked |
| Upkeep | low | high | medium (patch carry) | ours | medium | n/a |
| Adoption risk | low | high (mpv internals churn, GPL) | medium | medium (libplacebo has one main maintainer; sparse tags) | medium | blocked |

**Ranking (product):** 1 **C**, 2 **B2**, 3 B, 4 D, 5 A (demo and baseline only), 6 E.
**Trade-off in one line:** C costs about 4k extra lines of well-understood glue (A/V sync, audio, seek, subtitles) and buys license freedom, no rebase tax, a cross-vendor multi-adapter option, and a clean macOS story; B2 saves that glue but pins us to mpv's build, release cadence and GPL.

## 3. Findings per question

### Q1. mpv master (d94d095, 2026-10-10)

**Release.** Latest tag v0.41.0, 2025-12-21 [V GitHub API]; a rolling "git-release" pre-release exists; master moves daily. 0.41 made gpu-next the default and added "vd_lavc: prefer Vulkan hwdec when available" [V `mpv:RELEASE_NOTES`]. Min FFmpeg 6.1, libplacebo 6.338.2.

**libmpv render API: OpenGL or software only.** [V]
- `mpv:include/mpv/render.h:468-470` defines only `MPV_RENDER_API_TYPE_OPENGL` and `_SW`.
- `mpv:video/out/vo_libmpv.c:114-117` lists backends `render_backend_gpu`, `render_backend_sw`.
- `mpv:video/out/gpu/libmpv_gpu.c:8-13` offers only the GL context; line 88 `gl_video_init` = the legacy `--vo=gpu` renderer, not libplacebo; line 190 `gl_video_render_frame`.
- `mpv:DOCS/client-api-changes.rst`: newest entry API 2.5 (mpv 0.40.0); no D3D11/Vulkan/gpu-next in `render.h`. A web search found no open work item [S].
- The new `--d3d11-output-mode=composition` (0.41) lets a *VO* render into a window-less swapchain the host reads via the `display-swapchain` property [V `mpv:video/out/d3d11/context.c:540`, `DOCS/man/options.rst:6419`]. That is one swapchain for the whole canvas, so it does not give per-monitor timing. Not a lever.

**VO interface** (`mpv:video/out/vo.h`). One `struct vo` with a union holding **one** window state (lines 468-474). Driver hooks: `draw_frame` 408, `flip_page` 413, `get_vsync` 422; `vo_frame` carries `num_vsyncs`, `ideal_frame_vsync`, `display_synced` (231-286).

**Does gpu-next render once per vsync in display-sync mode?** Yes in steady state, with exceptions [V].
- `mpv:video/out/vo.c:966-1018`: each repeat of a frame is one `draw_frame` → `wait_until` → `flip_page` → `get_vsync`; lines 969-976 advance `vsync_offset` and `ideal_frame_vsync` per repeat; `num_vsyncs` counts down (979-980).
- Extras that break a pure counter: `do_redraw` (1071-1105: OSD, expose, pause) calls `draw_frame` again; a dropped frame skips it (990-993); gpu-next returns early if the swapchain refuses a frame (`vo_gpu_next.c:1570-1584`).
- OUTPUT hooks run on every call, redraws included: `draw_frame` → `pl_render_image_mix` → `pass_output_target` → `pass_hook(PL_HOOK_OUTPUT)` (`plc:src/renderer.c:2965`). Redraws only switch frame mixing off (`vo_gpu_next.c:1360-1361`).

**User-shader auto params: `PTS` and HDR fields only.** [V `mpv:video/out/gpu/utils.c:58-91`] The list is `PTS`, `chroma_offset_x/y`, `min_luma`, `max_luma`, `max_cll`, `max_fall`, `scene_*`, `max_pq_y`, `avg_pq_y`. No vsync time, no display fps. libplacebo adds shader globals `frame` (execution counter), `random`, `input_size`, `target_size`, `tex_offset` [V `plc:docs/custom-shaders.md:70`, `plc:src/shaders/custom_mpv.c:1363-1368`].

**Pan mode (R2) in stock mpv.**
- The true in-between offset exists in the core (`ideal_frame_vsync`, `mpv:player/video.c:921-922`) but `update_hook_opts_dynamic` passes only the frame image (`vo_gpu_next.c:1745-1747`, `2755-2770`). Without `--interpolation`, `pts_offset` is 0 (1355).
- A storage buffer holding "`frame` at last `PTS` change" gives refresh index k, so position ≈ PTS + k × vsync. Error up to one refresh from the unknown sub-refresh phase (`prev_error`) [D]: 4 ms at 240 Hz, about 10% of a frame step. Needs the storage write-then-read order across passes verified (open since the pacing research, question 7).
- Exact needs a ~10-line mpv patch exposing the offset as an auto param (GPL fork).

**How invasive is a multi-window VO in a fork?** Single-instance sites [V]:

| Site | Where | Single-instance thing |
|---|---|---|
| window state | `vo.h:468-474`; `w32_common.c:86-192` | one HWND, one HMONITOR, one `display_fps` |
| refresh source | `w32_common.c:670-696` | `MonitorFromWindow(..., PRIMARY)`, one rate |
| GPU context | `gpu_next/context.c:109-181` | one `ra_ctx`, one `pl_gpu`, one `pl_swapchain` |
| VO state | `vo_gpu_next.c:118-181, 2516-2565` | one swapchain, queue, renderer; hwdec bound to that `ra_ctx` |
| D3D11 swapchain | `d3d11_helpers.c:638-700`; `d3d11/context.c:282-293` | FLIP_DISCARD with no Flags (no tearing, no waitable); `Present(sync_interval, 0)` |
| VO loop | `vo.c:918-1018` | one draw, one wait, one flip, one vsync feedback |
| display-sync | `player/video.c:833-923` | one `vo_get_vsync_interval` decides `num_vsyncs` for everything |

About 10k lines of tightly coupled code (vo.c 1.5k, vo_gpu_next 3.0k, w32_common 2.6k, d3d11 ctx+helpers 1.9k, video.c 1.4k) [L wc]. mpv overall is ~220k lines (player 26k, audio 21k, demux 20k, sub 8k, video 86k) [L wc]. A fork touching all seven sites then chasing master is the most expensive option. **B2** avoids touching them: a new `vo_driver` (vo.h:317-461) that creates its own windows, plus a registration line in `vo.c` and `meson.build` [K]. Catches: no VO plugin API (patch carry), and `hwdec_d3d11va.c:111` requires mpv's own `ra_d3d11` context, so hwdec import must be rebuilt (~300 lines).

### Q2. libplacebo master (1dd0226, 2026-10-10)

**Release.** Latest tag v7.360.1, 2026-03-13; previous v7.360.0 (2026-02-11), v7.351.0 (2025-05-21), v7.349.0 (2024-07-02) [V GitHub API]. Master is at API 375 (`plc:meson.build:15`) = 15 unreleased API steps. One of them (API 375) turns `pl_source_frame` and `pl_frame_mix` timestamps from float to double "to avoid drift" (commit message and API note) [V], so a tagged 7.360.1 queue has float32 PTS (0.5 ms resolution at 2 h) [D]. Pin a master commit once P0 passes. License LGPL-2.1+ (relicensing possible on request) [V `plc:README.md`]. MSYS2 ships 7.360.1-2 for ucrt64, clang64, mingw64 (updated 2026-10-11) [V].

**`pl_queue` (frame queue and mixing for an arbitrary target time)** [V `plc:src/include/libplacebo/utils/frame_queue.h`].
- `pl_queue_update(.pts = t)` returns a `pl_frame_mix`; `timestamps[i] = (frame pts - t) / frame_duration_estimate` (`plc:src/utils/frame_queue.c:780, 914`), `vsync_duration = vps/fps` (791, 925). That is exactly "each refresh's time relative to film frames" (R2).
- Constraints: PTS must be monotonic (jumps need `pl_queue_reset`); thread-safe; `interpolation_threshold` silently switches to hold semantics when display and film rates are within tolerance; `drift_compensation` shifts all later PTS (mpv sets it to 0 because its core owns sync, `vo_gpu_next.c:1578, 1662`).
- Consequence: one queue per monitor (each has its own monotonic time), all fed the same decoded frames; share the mapped GPU frame between queues via refcount.
- The renderer says that for variable-refresh displays you are better off skipping the mixer and calling `pl_render_image` at the right PTS (`plc:renderer.h:803-806`).

**Several swapchains on one `pl_gpu`.**
- D3D11: `pl_d3d11_create_swapchain(d3d11, params)` can be called once per HWND [V `d3d11.h:154-220`]. `pl_d3d11_swapchain_unwrap` returns the `IDXGISwapChain` (225). `max_frame_latency` is device-wide except for waitable swapchains. **D3D11 `pl_gpu` is not thread-safe** (`thread_safe` is set only in Vulkan, `plc:src/vulkan/gpu.c:527`, and GL with make_current, `opengl/gpu.c:183`; none in `d3d11/*.c`). `pl_renderer` is unsafe too (`renderer.h:37`), so one renderer per monitor.
- Vulkan: `pl_vulkan_create_swapchain(vk, {surface, present_mode, swapchain_depth})` per surface [V `vulkan.h:325-381`]; `pl_gpu` is thread-safe.
- Both let us bypass the swapchain wrapper and render into images we own: `pl_d3d11_wrap` (`d3d11.h:261`) and `pl_vulkan_wrap` (`vulkan.h:479-532`).

**What the swapchain API exposes about present timing: nothing** [V `plc:swapchain.h:33-167`]. Only latency, resize, colorspace hint, start/submit/swap. The header warns that `swap_buffers` duration is "not an authoritative source of vsync timing".
- D3D11: `submit_frame` only releases the backbuffer; **`Present(1, 0)` is hard-coded in `swap_buffers`** (`plc:src/d3d11/swapchain.c:162-185`). Creation `flags` pass through (ALLOW_TEARING possible) but Present flags do not. Workaround: call `start_frame`/`submit_frame`, skip `swap_buffers`, and call `Present` ourselves on the unwrapped swapchain [D, trivially compatible with lines 121-185].
- Vulkan: `vkQueuePresentKHR` inside the swapchain (`vulkan/swapchain.c:1019`); no `present_id`, `present_wait`, or `VK_EXT_present_timing` anywhere [V grep]. This PC's NVIDIA driver advertises `VK_EXT_present_timing` rev 3, `present_id`, `present_wait`, `present_wait2` (AMD 610M does not) [L]; the extension schedules a present at a target time and reports refresh duration and interval per swapchain [V docs.vulkan.org proposal]. Whether it works in a borderless window on Windows is untested.

**Custom shader hooks.**
- Stages RGB, LUMA, CHROMA, ALPHA, XYZ, CHROMA_SCALED, NATIVE, MAIN, LINEAR, SIGMOID, PREKERNEL, POSTKERNEL, SCALED, PREOUTPUT, OUTPUT (`docs/custom-shaders.md:83-107`). OUTPUT is "after alpha blending, before dithering".
- `//!WIDTH/HEIGHT` resize works only at RGB, LUMA, CHROMA, XYZ, NATIVE, MAIN (docs:233); OUTPUT cannot change size.
- `//!BUFFER ... STORAGE` and `//!TEXTURE ... STORAGE` persist across invocations (docs:392, 429). `PARAM ... DYNAMIC` values are plain memory the host rewrites each frame (how mpv sets `PTS`, `vo_gpu_next.c:2755-2770`).
- libplacebo parses the *same* mpv hook files (`pl_mpv_user_shader_parse`, used at `vo_gpu_next.c:2676`), so the existing shader generator output runs unchanged in an own player.

**Warp plus scale in ONE pass with a good filter: not built in** [V]. `pl_render_params.distort_params` is a 2x2 affine applied *after* scaling ("cannot ... change the aspect ratio", `renderer.h:224-231`; `renderer.c:2815-2861` runs `pl_shader_distort`, a second resample, bilinear or bicubic only). `pl_shader_sample_polar` and `_ortho2` take a source rect plus output size: uniform scale only (`sampling.h:153-201`). What *is* built in and exact:
- **Fold model**: per-monitor `image.crop` (source rect, float) and `target.crop`; libplacebo rounds the destination rect to whole pixels and moves the rounding into the source rect, so sub-pixel placement is carried exactly in one resample (`plc:src/renderer.c:3311-3343`). EWA via `upscaler = ewa_lanczossharp` and friends.
- **Smooth curve**: needs our own GLSL (per-pixel map, local-Jacobian EWA or Lanczos) as a MAIN-stage hook sized to the monitor (so the stock scaler sees 1:1), or a bicubic second pass after a normal scale (documented fallback, two resamples).

**Per-target color and dither** [V].
- Color: `pl_frame.icc` (ICC via lcms2), `pl_frame.lut` and `pl_render_params.lut` (`PL_LUT_NORMALIZED` = linear light), `color_adjustment`, `target.color` (primaries, transfer, HDR metadata) (`renderer.h:246-253, 583-591`). Target LUT type NATIVE is applied after encoding (`renderer.c:2954`).
- Dither: to `target.repr.bits.color_depth` per render call, blue noise or error diffusion (`renderer.c:3057-3069`), so each monitor can dither to its own depth.
- Order inside `pass_output_target` (`renderer.c:2746-3145`): blurred-border source → distort → PRE_OUTPUT → alpha/background → encode → **OUTPUT hook (2965)** → border fill (2971) → picture draw with dither (3058-3069) → overlays (3134-3143). Consequences: the border fill (`PL_CLEAR_BLUR`) is **not dithered** and not seen by hooks (`renderer.c:2645-2708`), and subtitles are blended after dither, crisp at target resolution.

**Overlays and libass.** `pl_overlay` (monochrome A8 parts with colour, or BGRA) in source or destination coordinates (`renderer.h:474-526`). libplacebo has **no libass integration** (grep of src, demos, docs: none). mpv's glue is ~130 lines (`vo_gpu_next.c:325-360, 462-558`) on top of `sub/osd_libass.c` (723 lines) and a bitmap packer (195 lines) [L wc].

**`demos/plplay.c`** [V]. 768 lines, license CC0/public domain (header). Decode thread with libav, `pl_queue_push_block`, render loop on wall-clock time, `pl_swapchain_*`. "Not a serious video player": **no audio, no A/V sync**, loops forever. Hardware decode only for formats libplacebo's libav helpers map: `AV_PIX_FMT_DRM_PRIME`, `VAAPI`, `VULKAN` (`plc:utils/libav_internal.h:645-650, 1429-1440`). **D3D11VA frames are not supported by libplacebo itself**; mpv does its own import (copy the decoder array slice, then `pl_d3d11_wrap`; `hwdec_d3d11va.c:246-286`, `vo_gpu_next.c:744-757`; ~150 lines of logic). FFmpeg's own `ffplay` has used a libplacebo Vulkan renderer since 7.0 (`fftools/ffplay_renderer.c`, ~590 lines, LGPL-2.1+, FIFO present, Vulkan device shared with FFmpeg, no `pl_queue`, no libass) [V Changelog 7.0, fetched file]. Together plplay and ffplay_renderer are the reference code for the thin prototype.

### Q3. Alternatives (checked 2026-10-11)

| Candidate | Fresh facts | Verdict |
|---|---|---|
| **GStreamer** | 1.28.0 released 2026-01-27; 1.28.8 on 2026-10-08 [V release notes]. D3D12 elements active (overlay compositor, interlace, fisheye dewarp, UV remap, `d3d12swapchainsink`); Vulkan AV1/VP9 decode new in 1.28; `clocksync` gained rate/resync [V]. No shader-hook change or libass mention in the notes [V]. N sinks on a `tee` share the pipeline clock; sinks present against that clock, not against each monitor's refresh [K]. | Reject. We would still write a libplacebo element (caps negotiation, device sharing) and lose display-resample. |
| **VLC 4 / libVLC** | 4.0 not stable as of July 2026: beta only on Apple, Windows nightlies, no release date [S]. | Legacy/not ready. Reject. |
| **FFmpeg** | **9.0** tag commit 2026-08-03; 9.0.2 2026-09-18; 8.1 (2026-03-16) now 8.1.3 (2026-09-21); 7.1.5 [V]. Relevant: ffplay on libplacebo (7.0), D3D12VA decode H.264/HEVC/VP9/AV1/MPEG-2/VC1 (7.0), Vulkan hwaccels incl. VP9 (8.0) and APV (9.0), `vf_scale_d3d11` (8.0), swscale Vulkan (8.1) [V Changelog]. `vf_libplacebo` exists (this PC's 8.1 has `--enable-libplacebo`, `--enable-d3d11va`, `--enable-vulkan`) [L]. | Use libav* for decode. `vf_libplacebo` is for offline reference renders (P4), not realtime. |
| **Rust** | `ffmpeg-next` 9.0.0 (2026-08-05), `ffmpeg-sys-next` 9.0.1 (2026-10-09); `rusty_ffmpeg` 0.17 (FFmpeg 8.1); `rsmpeg` 0.18.0 (FFmpeg 8.0, 2025-08, no 9 feature); `libplacebo-sys` 0.1.0 (2019); `wasapi` 0.25.0 (2026-10-01); `cpal` 0.18.2 [V crates.io]. | Good for FFmpeg and WASAPI; **nothing for libplacebo**. |
| **Splash** (sat-mtl) | GPLv3, OpenGL only, GLFW, FFmpeg, "up to eight outputs on two graphic cards", single computer; README says nothing about swap lock or sync [V]. | Learn only: one process, one GL window per output works. Cannot reuse code (GPL, GL). |
| Others | VLC 3 "wall" filter, OBS multi-projector, nDisplay-style swap groups (pro GPUs) [K, not verified]. | No reusable consumer-GPU frame-lock found. |

### Q4. A/V sync and audio on Windows

**What mpv does** [V]:
- Audio is the clock: `playing_audio_pts = last_out_pts - audio_speed * ao_get_delay()` (`mpv:player/audio.c:624-630`); video timing follows it (`player/video.c:602-640`).
- `display-resample`: `speed_factor_v = find_best_speed` (`video.c:695-725`) so display rate = integer × film rate within `sync-max-video-change`; frames get `num_vsyncs = lrint((frame_duration + error)/vsync)` with error carry (846-854); drop or repeat vsyncs if |A/V diff| ≥ 20 ms and ≥ 1 vsync (869-876); audio speed follows a low-pass drift controller, 1 s filter, 10 s recovery, slew limited (739-802).
- It follows **one** display (`vo_get_vsync_interval`, line 833).
- That core is ~240 lines (`handle_display_sync_frame` 805-939, `find_best_speed`, drift) and is portable as an idea. Per monitor, only the *leader* monitor runs it; followers derive film time from the leader.

**Minimal own implementation, ranked:**
1. **WASAPI shared mode, event-driven, direct** (C/C++ COM, or `wasapi` crate): `IAudioClient` with `AUTOCONVERTPCM`, `IAudioClock::GetPosition` + QPC for the audible position, `swr_convert` plus `swr_set_compensation` for drift. ~400-600 lines [D]. mpv's equivalent is 1.9k lines (`ao_wasapi.c` 546, `_utils.c` 1138, `_changenotify.c` 251 [L wc]) mostly for exclusive mode, format negotiation and hot-plug.
2. **cubeb** (Mozilla, C++): advertises "latency compensated audio clock reporting for A/V sync"; Firefox's WASAPI path computes position as stream clock minus latency, with known rounding and non-monotonic-clock bugs [S]. License believed ISC [K].
3. **miniaudio** (single header) and **SDL3 audio**: easiest, but their clock and latency reporting was not verified this pass [K, unverified]. Callback-only.

**Pitfalls (all [K] unless noted):** Bluetooth and USB DAC latency not fully reported by WASAPI, so keep a user `audio-delay`; device hot-plug (`AUDCLNT_E_DEVICE_INVALIDATED`); engine resampling hides sample-rate mismatch but shifts positions; HDMI audio clock differs from the display clock (hence slave audio to display by resampling, never drop video frames across a bezel); per-monitor panel latency differs by tens of ms (calibrate per monitor; lip-sync to the monitor carrying faces); after seek flush and pre-roll both streams before start; first compile of a D3D11 shader cost ~160 ms in the spikes, so pre-warm and cache (`pl_cache`).

### Q5. Languages and build on this PC

**Facts** [L unless noted]: VS 2022 Build Tools, MSVC 14.44 (`cl` only inside a developer prompt); **no** clang or clang-cl, meson, pkg-config, MSYS2, or Vulkan SDK; MinGW at `C:\MinGW` is MinGW.org GCC 6.3.0, 32-bit (useless); CMake, Ninja, Python 3.11 + pip, Rust 1.93.1. Vulkan: NVIDIA RTX 5070 Ti Laptop 1.4.351 (driver 617.42), AMD 610M 1.4.315. Vulkan Video decode extensions: NVIDIA H.264, H.265, AV1, VP9; AMD H.264, H.265, AV1.

**libplacebo on Windows** [V]:
- Needs meson ≥ 1.3, C11, C++20 (`meson.build:1-9`). Vulkan-Headers is a vendored git submodule, used when `3rdparty/Vulkan-Headers/include` exists (`src/vulkan/meson.build:8-27`), and code generation uses vendored jinja plus Python; **no Vulkan SDK required**, but a GLSL→SPIR-V compiler is (shaderc or glslang, `src/meson.build:176-181`), and the **D3D11 backend also needs SPIRV-Cross ≥ 0.29** (`src/d3d11/meson.build:5-9`).
- Upstream CI builds Windows only via **MinGW-w64 cross** (`.gitlab-ci.yml:47-79`). `_MSC_VER` appears nowhere in `src/`; meson mentions lld-link. So **MSVC `cl` is untested**; clang-cl or MinGW are the safe routes [D].
- No vcpkg port (GitHub API 404) [V]. MSYS2 packages exist [V]; they also carry FFmpeg, libass, shaderc, spirv-cross, lcms2 [K].
- GNU-isms are modest: 14 `__builtin_`, 19 `typeof`, 9 `__attribute__` [L grep]; libplacebo's libav helpers are gated `>=` up to libavutil 60.16 (FFmpeg 8.x) and untested against FFmpeg 9 headers (libavutil 61) [V]; check in P0.

**FFmpeg dev packages** [V zip central directory read by byte range]: BtbN `ffmpeg-n9.0-latest-win64-lgpl-shared-9.0.zip` (77 MB, rebuilt daily; n8.1 and master variants too) contains `include/` (incl. `hwcontext_d3d11va.h`, `hwcontext_d3d12va.h`, `hwcontext_vulkan.h`), `lib/*.lib` MSVC import libs, and `bin/*.dll` (avcodec-63, avformat-63, avutil-61, swscale-10, swresample-7). The "lgpl" variant lacks GPL-only libraries, "most prominently libx264 and libx265" [V README]. Which hwaccels the LGPL build enables is not confirmed: run `-hwaccels` in P0 [K]. gyan.dev also offers shared dev builds [K]. This PC's installed 8.1 is a static GPL CLI build, not usable for linking.

**Language ranking for the performer:**

| Rank | Language | Why |
|---|---|---|
| 1 | **C11** (build with MSYS2 gcc or clang-cl; C++ allowed for COM files) | libplacebo, plplay, ffplay_renderer, mpv are all C with compound-literal macros; agents can adapt reference code with little translation; COM works via COBJMACROS |
| 2 | Rust | `windows` crate is excellent for DXGI/WASAPI and `ffmpeg-next` 9 is current, but libplacebo needs hand-made bindgen (no maintained crate, bindgen needs libclang which is not installed), every call is `unsafe`, and the struct-by-macro API must be rewritten |
| 3 | C++ (MSVC) | compound-literal macros are invalid in MSVC C++; fill structs by hand; fine otherwise |

Keep planner, room model and analysis in Rust or Python; they talk to the performer through files (ADR 0014).

### Q6. Licensing

| Component | License | Consequence for us |
|---|---|---|
| mpv | GPLv2+ default; LGPLv2.1+ with `-Dgpl=false` ("a convenience, not a license grant"); new contributions LGPL (`mpv:Copyright`, `meson.options:2`) [V] | Fork or VO patch: the performer is GPL/LGPL. Planner et al. stay free as separate processes. `vo_gpu_next.c`, `video.c`, `osd_libass.c` carry LGPL-2.1+ headers [V], so copying them taints our files at LGPL |
| libplacebo | LGPL-2.1+ (permissive relicense possible on request) [V] | Link dynamically (DLL) |
| FFmpeg | LGPL-2.1+ by default; GPL with `--enable-gpl` (x264, x265, ...) [V BtbN README] | Use the lgpl-shared build |
| libass | ISC [K]; HarfBuzz, FreeType (FTL), FriBidi (LGPL) [K]. libass 0.17.5 released 2026-06-24 [V] | Permissive; FriBidi LGPL dynamic |
| shaderc, glslang, SPIRV-Cross, lcms2 | Apache-2 / BSD / MIT [K] | Build/runtime deps of libplacebo |
| plplay.c | CC0 [V]; `ffplay_renderer.c` LGPL-2.1+ [V] | plplay freely adaptable; ffplay file LGPL-bound |
| Splash | GPLv3 [V] | Do not copy |
| GStreamer core | LGPL-2.1+ [K] | n/a |
| cubeb ISC, miniaudio public domain/MIT-0, SDL3 zlib | [K] | n/a |

What each engine forces: A, nothing on our code (mpv is a separate process, but shipping mpv.exe means GPL compliance for that binary). B and B2, GPL or LGPL on everything inside the mpv process. C, nothing beyond LGPL replaceability notices if the DLLs stay dynamic and FFmpeg is the LGPL build.

## 4. Picture path design inside option C

**Invariant.** One master film clock; each monitor *pulls* the frame for its next refresh by film time (never pushed at decode rate). Per monitor: `t_m = film_time_at(predicted_present_time_m)`.

**Per monitor, per refresh** (monitor m with its own `pl_renderer`, `pl_queue`, swapchain or wrapped backbuffer):

```
latch:  shot = cue_sheet(version_ptr).lookup(PTS of the frame being drawn)   // snapshot per shot (R3, R4)
pass 1  PICTURE   pl_render_image_mix(rr_m, mix(t_m), canvas_m, params_m)
                  image.crop  = cue crop (source rect, float; pan mode adds the sub-pixel slide here)
                  target.crop = where the picture lands on this monitor (fold: intersect with this monitor)
                  one EWA resample source -> monitor pixels; target = FP16 canvas whose target.color is the
                  monitor's primaries with a linear transfer; border = SKIP; tone map and ICC happen here
                  curve mode only: MAIN-stage hook sized to the monitor does the per-pixel warp + EWA
pass 2  SURROUND  edge colours of canvas_m -> per-monitor EMA state (cut = snap or fast blend, from cue)
                  -> fill the leftover area of canvas_m (1:1 pixels, no resample)
pass 3  FINISH    per-monitor correction in linear light (3x3 + curves or 3D LUT from the room model)
                  -> encode (monitor transfer) -> dither to the monitor's bit depth -> backbuffer
pass 4  SUBS      libass bitmaps -> pl_overlay on the backbuffer after dither (crisp, native pixels);
                  pl_render_image with a NULL image draws overlays only (renderer.c:3589-3649)
present           our Present call (D3D11) or our vkQueuePresent (Vulkan), timing per monitor
```

| Concern | Where it runs | Why |
|---|---|---|
| Crop, position, size | pass 1 (`image.crop`, `target.crop`) | exact on the frame by construction: the cue is looked up for the frame being drawn |
| Pan slide (R2) | pass 1, float source-rect offset per refresh | libplacebo puts rounding into the source rect, so still one resample; cached-frame reuse is off while the crop moves (`renderer.c:3991-3997`), so EWA reruns each refresh (cost: P4) |
| Strip uncovered by a slide | second `pl_render_image` of the neighbouring frame into the strip, drawn first | stock mpv cannot (only the current frame is visible to a hook) [D] |
| Surround glow | pass 2, own shader with persistent state | libplacebo's `PL_CLEAR_BLUR` is a blurred zoomed copy, not edge-colour extension, has no temporal smoothing and is not dithered (`renderer.c:2645-2708`) |
| Color correction | pass 3, one pass over picture **and** surround | a mismatch between two correction paths would draw a visible edge at the picture boundary; libplacebo's own per-render `lut` only reaches the picture |
| Dither | pass 3 | surround gradients need it as much as the picture; libplacebo only dithers the picture draw |
| Subtitles | pass 4 | dialogue renderer sized to the monitor chosen by the cue (ADR 0013); positioned signs need a second libass layer in picture coordinates cut per monitor [D] |
| Tone mapping, ICC | pass 1 (libplacebo color map to `target.color`) | existing, SOTA |

**Resamples per monitor: one** (pass 1). Passes 2 to 4 are 1:1 pixel operations. Curve mode keeps one only with the custom MAIN hook; the bicubic-after-scale fallback is two. The cost of the FP16 canvas (4K: 66 MB, ~8 GB/s at 60 Hz read+write) is trivial on the dGPU and must be measured on the AMD iGPU (P4).

**Reuse.** The generated shader's cue lookup by `PTS`, geometry and color math move into passes 1 and 3 as hook files or C structs. In C the performer can skip the table texture and set per-shot values directly; the latch becomes one atomic pointer to an immutable cue-sheet version.

**Shared decode, N renderers.** Frames are decoded once and mapped once (shared `pl_frame`, refcounted); each monitor's renderer repeats only source-resolution stages (chroma upsample, YUV→RGB) [D]. Separate adapters (Setup B may put a screen on the iGPU) get one `pl_d3d11` per adapter and a cross-adapter copy; mpv cannot do this (single device).

## 5. Minimal own-performer module list (option C)

| # | Module | Job | Reference to learn from | ~lines full | ~lines M1 |
|---|---|---|---|---|---|
| 1 | app | CLI, config, logging, lifecycle | plplay | 300 | 200 |
| 2 | cue-sheet | load versions, lookup by PTS, per-shot latch, pan-path eval, atomic swap | spike shader + swap rules | 600 | 300 |
| 3 | demux-decode | libavformat/libavcodec, hwaccel, seek/flush, PTS rebase, frame hand-off | plplay decode loop | 1200 | 500 (no seek) |
| 4 | hwdec | D3D11VA slice copy → `pl_d3d11_wrap`; Vulkan via `pl_map_avframe` | mpv `hwdec_d3d11va.c`, ffplay_renderer | 450 | 300 (D3D11 only) |
| 5 | presenter | per-monitor window, swapchain, thread or waitable loop, own Present, frame statistics | mpv `d3d11/context.c` | 1200 | 700 |
| 6 | picture-path | passes 1 to 3 above, hook loader, state | section 4 | 1000 | 300 (pass 1 + trivial finish) |
| 7 | clock-sync | master clock, per-monitor t_m, display-resample port | mpv `video.c:695-939` | 500 | 100 (fixed clock) |
| 8 | audio | decode, swresample, WASAPI shared, audible-position clock | mpv `ao_wasapi.c` | 900 | 0 |
| 9 | subtitles | libass renderers, attachment fonts, overlay atlas, dialogue/sign split | mpv `osd_libass.c`, `vo_gpu_next.c:325-558` | 700 | 0 |
| 10 | control | named-pipe JSON: load cue version, seek, pause, status, diagnostics | spike harness | 500 | 0 (CLI args) |
| 11 | diagnostics | barcode and pacing log compatible with the existing harness | `dda-present-log.py` | 300 | 200 |
| | **Total** | | | **~7.65k** | **~2.6k** |

Estimates are [D] from the reference files' sizes (plplay 768 lines, ffplay_renderer ~590, mpv `hwdec_d3d11va.c` 311, `ao_wasapi*.c` 1.9k, sync core ~240); they exclude build files and shader text.

Display-mode switching and the watchdog (phase 07) are a separate small executable and not counted.

## 6. Spike designs to settle what remains open

All reuse the existing harness (barcode video, Windows Graphics Capture, `dda-present-log.py`, `analyze-span-pacing.py`, swap trial scripts). None changes display settings except where the user approves. Pass thresholds are proposals [D]; the user's pending by-eye seam threshold can tighten or loosen P1.

| # | Build | Measure | Pass | Effort |
|---|---|---|---|---|
| **P0** toolchain and reference playback | libplacebo (7.360.1 and a master pin), FFmpeg 9.0 LGPL-shared, libass; plplay-derived `perf0.exe`; backends D3D11 and Vulkan; hwdec D3D11VA(copy) and Vulkan Video; try MSYS2 UCRT64 and clang-cl routes | build route that works from a clean checkout; 10 min playback of 1080p/4K H.264, HEVC, AV1 anime; GPU/CPU %; render ms p99 for 1080p→1440p EWA on RTX 5070 Ti and AMD 610M; first-frame time cold and with `pl_cache`; libav helper compile against FFmpeg 9; LGPL build `-hwaccels` | one documented build command; 0 decode errors; p99 ≤ 3 ms on dGPU (fits a 240 Hz refresh); first frame < 1 s warm | 1-2 d |
| **P1** two-window presenter (R1) | video-only, 2 borderless windows (laptop + external) on one D3D11 device; modes: each at own vsync; leader/follower (external leader, laptop presents right after, ALLOW_TEARING if VRR works, else 10 per frame at 240 Hz) | refreshes per film frame per monitor; offset external-minus-laptop p5/median/p95 vs the spanning-window baseline (−1.7 / +2.5 / +6.7 ms); PresentMon present mode | each monitor even pace; median offset ≤ 1 ms and p95 ≤ 3 ms; no flicker on `pan-span` by eye. **Fail for engine reasons** (our loop cannot hold the offset) → open B2 gate; fail for OS reasons → hand to the OS researcher | 2-3 d |
| **P2** pan mode per refresh (R2) | presenter draws the real-anime slides with the measured path, crop offset per refresh; and a stock-mpv variant using `frame` + STORAGE | position per refresh against the path (capture); cost per refresh | own presenter ≤ 0.25 px error at 240 Hz; user "pan mode was good" repeats on both screens; stock-mpv variant reports its error so A can be priced | 1-2 d |
| **P3** cue exactness, refine, seek latch (R3, R4) | cue sheet versions, atomic swap, per-shot latch in the presenter | repeat `run-swap-trials.py` matrix incl. adversarial seeks at −50..+150 ms around each swap, scrubbing 5 seeks/s, 25 fps | 0 wrong frames, 0 shots changed on screen over ≥ 100 swaps and 50 seeks | 1 d |
| **P4** picture-path quality and cost (R5) | fold (one EWA resample) vs the current OUTPUT-hook path (second resample) on zone plate, slanted edge, text, a face; curve via custom MAIN hook vs bicubic second pass; passes 2 to 3 with FP16 canvas | ringing/aliasing/MTF; ms per pass at 1440p and 4K on both GPUs; banding on dark gradient with and without pass-3 dither; brightness match across a bezel | fold path ≥ plain single-monitor libplacebo sharpness; pass 2+3 < 1 ms at 1440p on dGPU, < 3 ms on iGPU; no visible banding on 8-bit panel | 2 d |
| **P5** audio and sync (R6) | WASAPI shared + `IAudioClock`, display-resample port, 23.976 on 240.001 Hz | A/V offset over 30 min with a flash/click test (loopback capture vs screen capture); seek ×50 | \|offset\| ≤ 20 ms steady; re-sync ≤ 200 ms after seek; no audible glitch | 2-3 d |
| **P6** subtitles and seek (R6) | libass per monitor, MKV attachment fonts, dialogue vs sign split | stale subs after seek; glyph coverage; timing | no stale or missing subs in 50 seeks; dialogue never crosses a bezel | 2 d |
| **P7** B2 viability (only if P1 fails for engine reasons or P5+P6 > 3 weeks) | build mpv under MSYS2; trivial `vo_panorama` that opens 2 windows and draws a test pattern from `draw_frame` | needs no core change; frame hand-off latency; hwdec import effort | VO opens N windows with core untouched | 3-4 d |

Order: P0 → P1 → P3 → P2 → P4 in parallel with P5/P6. P0 and P1 decide the engine; everything after builds on the answer.

## 7. Sources

Local source (pinned): mpv `d94d095` (2026-10-10) and libplacebo `1dd0226` (2026-10-10) under the session scratchpad; files cited inline as `mpv:` / `plc:`.

Web (all fetched 2026-10-11):
- https://api.github.com/repos/mpv-player/mpv/releases (v0.41.0 2025-12-21)
- https://api.github.com/repos/haasn/libplacebo/releases (v7.360.1 2026-03-13)
- https://api.github.com/repos/libass/libass/releases (0.17.5 2026-06-24)
- https://ffmpeg.org/download.html (9.0.2 2026-09-18; 8.1.3 2026-09-21; 7.1.5 2026-06-20)
- https://api.github.com/repos/FFmpeg/FFmpeg/commits/n9.0 and /n8.1 (committer dates 2026-08-03, 2026-03-16); https://api.github.com/repos/FFmpeg/FFmpeg/tags
- https://ffmpeg.org/index.html (news list showed only 8.1 of 2026-03-16 and no 9.0 item; possibly cached. The download page, the git tags, BtbN's n9.0 builds and `ffmpeg-next` 9.0.0 all agree on 9.0, so the 9.0 date is the tag commit date, not an announcement date)
- https://raw.githubusercontent.com/FFmpeg/FFmpeg/master/Changelog (7.0, 7.1, 8.0, 8.1, 9.0 hwaccel lines)
- https://raw.githubusercontent.com/FFmpeg/FFmpeg/master/fftools/ffplay_renderer.c (via summary)
- https://api.github.com/repos/BtbN/FFmpeg-Builds/releases/tags/latest ; https://raw.githubusercontent.com/BtbN/FFmpeg-Builds/master/README.md ; https://api.github.com/repos/BtbN/FFmpeg-Builds/contents/scripts.d ; zip central directory of `ffmpeg-n9.0-latest-win64-lgpl-shared-9.0.zip` (byte range)
- https://packages.msys2.org/base/mingw-w64-libplacebo (7.360.1-2, updated 2026-10-11)
- https://gstreamer.freedesktop.org/releases/ and /releases/1.28/ (1.28.0 2026-01-27; 1.28.8 2026-10-08)
- https://docs.vulkan.org/features/latest/features/proposals/VK_EXT_present_timing.html
- https://crates.io/api/v1/crates?q=libplacebo ; ?q=ffmpeg ; /crates/rsmpeg ; ?q=wasapi
- https://gitlab.com/sat-mtl/tools/splash/splash/-/raw/develop/README.md
- Search snippets [S]: VLC 4.0 status (osnews/linuxiac, July 2026 article); cubeb WASAPI clock (bugzilla.mozilla.org/show_bug.cgi?id=1136360, asuth.searchfox.org libcubeb README); libmpv render-API search (no issue found).
- Dead ends: vcpkg `libplacebo` port (404 on three URLs), BtbN `50-ffmpeg.sh` (404), trac.ffmpeg.org HWAccelIntro (bot-blocked).

Prior project reports used: the three spikes, `research-261011-1000` (sections 4-5), `researcher-261010-1846` rendering and bezels reports.

## 8. Unresolved questions and limits

Open questions:
1. Which toolchain will the user approve installing: MSYS2 UCRT64 (also gives FFmpeg, libass, shaderc, spirv-cross), or clang-cl plus meson via pip, plus shaderc/SPIRV-Cross built from source? MSVC `cl` for libplacebo is untested.
2. Does libplacebo master's libav helper compile against FFmpeg 9 headers (libavutil 61)? Fallback: BtbN n8.1.
3. D3D11 vs Vulkan backend on these two GPUs: reliability, independent flip, hwdec zero-copy. P0 and P1 decide; default start is D3D11 (mpv's Windows default, one render thread fits the not-thread-safe `pl_gpu`).
4. Does `VK_EXT_present_timing` work in a borderless window on Windows with driver 617.42? Advertised, not exercised. The OS researcher owns the presentation behaviour.
5. Which hwaccels does the BtbN LGPL build enable (d3d11va, vulkan)? Check with `-hwaccels`.
6. Pan mode strip fill: is two renders per refresh (nearest frame + neighbour into the strip) affordable on the iGPU? P2 and P4.
7. Subtitle signs vs dialogue: how the split is detected (style/tag heuristic) is a planner/analysis question; the performer only needs two layers.
8. Does the user want a control UI inside the performer (seek bar, track menu) or only IPC? Affects module 10 size.
9. Per-monitor panel latency calibration method (camera) for A/V and bezel timing: OS researcher.
10. Setup B adapter map (which monitor hangs off which GPU) decides whether cross-adapter copies are needed.

Limits of this research:
- No runtime measurement of libplacebo, FFmpeg or WASAPI on this PC: every performance and quality claim is a spike target, not a result.
- No Windows build was attempted; build claims rest on build files, CI config and package pages.
- miniaudio, SDL3 and cubeb clock/latency behaviour was not read from source; licenses of libass, HarfBuzz, FreeType, shaderc, glslang, SPIRV-Cross, cubeb, miniaudio, SDL3 are [K].
- GStreamer's sink pacing is [K]: its release notes were read only to character 100000 of 220539.
- 33 network calls were used against a budget of about 30; 6 were dead ends.
- Source quality: pinned source, official release APIs, crates.io API, FFmpeg Changelog and the Vulkan proposal page are primary (high). MSYS2 and BtbN pages are secondary (medium). Search snippets (VLC status, cubeb) and all [K] items are low and flagged; none carries a conclusion alone.
- Team skill is unknown. If the user prefers Rust for the performer, the ranking of C over B2 holds but the cost rises by bindgen setup (libclang), `unsafe` call sites and hand-rewritten struct macros.
- Single-source warning: libplacebo behaviour rests on one code base (no independent reimplementation exists); mpv, FFmpeg's ffplay and plplay all consume it, which is also its strongest adoption signal.
