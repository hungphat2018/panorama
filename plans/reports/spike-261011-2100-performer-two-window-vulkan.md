---
title: "Spike P1-Vulkan: the two-window presenter on libplacebo's Vulkan backend, and what Vulkan's present-timing extensions give"
date: 2026-10-11
type: spike
answers: "ADR 0022 gate, Vulkan half: does the two-window presenter work on libplacebo's Vulkan backend, and what do VK_KHR_present_id / present_wait and VK_EXT_present_timing give on a Win32 surface here?"
harness: spikes/performer-p1-vulkan/
results: spikes/performer-p1-vulkan/results-2026-10-11/
status: built and checked without a window; every windowed run is pending (the coordinator runs them)
---

# Spike P1-Vulkan: two-window presenter on libplacebo's Vulkan backend

Run 2026-10-11, 20:10 to 21:50, Asia/Bangkok. Evidence tags: **[L]** measured on this PC, **[V]** source or spec read, **[D]** derived, **[K]** prior knowledge, not checked here, **[P]** pending: needs the windowed suite, which the coordinator runs once the screens are free. `plc:` = libplacebo `1dd0226` source, `ff:` = FFmpeg 9.0.2 headers; P0 and P1 are the earlier toolchain and two-window reports.

**No window was opened by this work.** (`vulkaninfo`, which the brief allowed, makes its own small surface window; it ran twice.) The user's game (DD2.exe) ran most of the time; the coordinator then asked for the windowed suite to be left to them, and none was started after the game ended. Everything marked [L] below is windowless: device and loader queries, device-creation tests, 4 to 8 s offscreen runs of the real presenter (same threads, decoder, clocks, renderers and logs, with an offscreen target instead of a window), and unit tests of the analyzers. What a visible window adds (the swapchain, the compositor, the present reports) is built, compiled and **not yet run**.

Terms (plain):
- **Swapchain**: the few pictures a window shows from. The app draws into a free one and hands it back ("presents" it). Example: the laptop window has 2; one is on screen while the app draws the other.
- **Refresh**: one chance for a screen to change picture: every 4.17 ms on the laptop (240 Hz), 10 ms on the external (100 Hz).
- **Top-row time / scan-line grid**: when a refresh's first row is drawn, from D3DKMT scan-line reads (ADR 0025). The vblank starts one **blank** earlier (0.46 ms on the laptop).
- **Present id / present wait**: a present carries a number; "wait for present N" returns when N is on screen. For Vulkan it does what DXGI's frame-latency waitable object did in perf1: draw frame N only after N-1 is up, and N lands on the next refresh.
- **Present timing**: Vulkan's report of when each present reached the screen ("first pixel out" = the moment its first pixel leaves the presentation engine for the display), and a way to ask that a present appear at least k refreshes after the previous one.
- **Independent flip**: Windows shows the window's picture straight on the display, without the desktop compositor (DWM) redrawing it. **Composed**: DWM redraws it, one more step of delay, and for a window on a second monitor DWM's own clock rules the pace.
- **Implicit layer**: a DLL the Vulkan loader injects into every Vulkan program by itself (overlays, capture hooks). It sees every present call.
- **Hitch**: a presenter loop longer than 50 ms.
- **Queue full**: the present-timing result queue has no room; the present is refused.

## 1. Answer

**Verdict line for ADR 0022's Vulkan gate: WORKS WITH LIMITS, provisional.** Shown [L]: libplacebo's Vulkan backend carries everything the two-monitor presenter does except the window itself: one device, one render thread per monitor, one `pl_renderer` and one frame queue (`pl_queue`) per monitor, shared decode (software and Vulkan Video), film clock, row-timing sampler and logs, 0 failures; the NVIDIA driver accepts the whole present-timing set on one device, also when libplacebo creates that device (`perf1vk --device-test --backend own`). Not shown yet [P]: that a visible window presents on its monitor's refresh without hitches, what the present reports say, and whether any Vulkan window reaches independent flip. What it means for the design, whatever those runs say:

1. **libplacebo's own Vulkan swapchain cannot be the final presenter.** It presents inside `pl_swapchain_submit_frame` with no present id, wait or timing (`plc:src/vulkan/swapchain.c:1021`, no reference to either extension in the file), and blocks in the image acquire with an unlimited timeout (`:893`). It also prefers a **premultiplied-alpha** composite mode over opaque (`:590-592`, order POST_MULTIPLIED, PRE_MULTIPLIED, OPAQUE), and this surface offers only OPAQUE and PRE_MULTIPLIED [L, vulkaninfo], so libplacebo asks for premultiplied: a translucent window as far as the compositor knows, which normally rules out independent flip [K; to confirm in the run: the log prints the mode, and `own-judas-premultiplied` against `own-judas` tests the effect]. The way out is **our own `VkSwapchainKHR` with the images wrapped by `pl_vulkan_wrap`**: written (704 lines, `perf1vk-own-swap.c`), compiled, device creation checked, swapchain not run [P]. A small libplacebo patch (a `composite_alpha` field) would fix only the alpha, not the ids and timing.
2. **Present id, wait and timing exist on NVIDIA only here.** The AMD Radeon 610M (Setup B's iGPU) lists none of them, no swapchain_maintenance1 and no internally synchronized queues [L]. The D3D11 presenter (waitable swapchain, DXGI statistics) stays the portable path and the one for any monitor on a non-NVIDIA adapter.
3. **The report is thinner than the spec allows.** On the Win32 surface: present timing supported; target times **relative only** (`presentAtAbsoluteTimeSupported = false` on the surface, although the device-level feature is true [L, vulkaninfo and `--selftest`]); only **two stages** can be asked for, queue-operations-end and image-first-pixel-out (no request-dequeued, no first-pixel-visible) [L, vulkaninfo]. Whether first pixel out is the top row, the blank start or something else [P].
4. **Vulkan Video decode works on libplacebo's own device** with FFmpeg adopting it (HEVC Main10 and H.264): `hw_fmt vulkan/p010le` and `vulkan/nv12`, 385 and 302 frames mapped by the two queues, 0 failures [L]. That is the reverse of P0's route (FFmpeg created the device, libplacebo imported it) and it matters: only the application-created device can switch on `VK_EXT_present_timing`, which FFmpeg does not know.
5. **P0's 300-720 ms Vulkan hitches have a prime suspect on this PC**: the loader lists 9 layers, 8 of them implicit (3 from the GPU vendors, 5 third-party); two third-party DLLs really are loaded into a plain Vulkan process: the RivaTuner hook (`RTSSVkLayer64.dll`) and an OBS-style capture hook (`we-graphics-hook64.dll`), and ReShade's layer fails to initialise (DLL error 1114) [L]. They hook the present calls. The windowed A/B is built (section 6.4) [P]. Not loader-controlled and in every process: `windhawk.dll` [L].
6. **The two monitors share one libplacebo command recording.** libplacebo's Vulkan `pl_gpu` is thread safe, but it keeps one open command buffer for all threads under a mutex (`plc:src/vulkan/gpu.c:155-170, 200-234`, `gpu.h:62`), and with the default of one queue per family (`vulkan.h:274`) all work goes through one graphics queue [V]. Either render thread's flush submits both threads' recorded work. The cost here is small (a render is about 0.1 ms of GPU time against a 4.17 ms refresh), but the monitors are not independent at the queue [D]: a stall of one would show up in the other's `present`/`swap` phase in the analyzer. If the windowed runs show such cross-monitor stalls, the fix is one `pl_vulkan` object per monitor on the same GPU [D, not tried].
7. The presenter, analyzers and probes are ready, with 32 unit tests and windowless self-tests [L]. Section 6 lists exactly what to run and what each run should read.

## 2. What was built

`spikes/performer-p1-vulkan/` (C11, gcc `-Wall -Wextra` clean; `compile.ps1` -> `compile-perf1vk.sh`, MSYS2 UCRT64, libplacebo master 7.375.0, LGPL FFmpeg 9.0.2; `bin/run.cmd` puts the DLLs on PATH for that process only).

| File | Lines | What |
|---|---|---|
| `src/perf1vk-main.c` | 1488 | perf1's windows, film clock, aimed start, "nearest" follower, picture layout, row timing and logs, with a render thread per monitor and a loop that blocks in the swapchain acquire; hitch bookkeeping (phases of every loop, event log, window messages); `--device-test` |
| `src/perf1vk-gpu.c` | 442 | libplacebo `pl_vk_inst` / `pl_vulkan` creation (device picked by the DXGI adapter's LUID), a `VkSurfaceKHR` per window, event log, offscreen target for `--no-window`, `swap_*` seam |
| `src/perf1vk-own-swap.c` | 704 | own swapchain: opaque alpha, present ids, `vkWaitForPresent2KHR` pacing, present-timing feedback, images handed to libplacebo with `pl_vulkan_release_ex` / `pl_vulkan_hold_ex` (the protocol libplacebo's own swapchain uses) |
| `src/perf1vk-hwdevice.c` | 127 | FFmpeg adopts libplacebo's `VkDevice` (extra video-decode queue family and codec extensions requested through `pl_vulkan_params`) |
| `src/perf1vk-decode.c`, `-clock.c`, `-stats.c` | 401, 148, 87 | copied from perf1 (decoder now software or Vulkan Video; no D3D11VA) |
| `src/vk-caps.c` | 437 | headless probe: layers, GPUs, LUID to DXGI adapter, queue families, extensions, features, a `vkCreateDevice` test with the present set, calibrated clocks, injected DLLs |
| `src/vk-timing-probe.c` | 1526 | plain-Vulkan windowed probe: 13 experiments on present id/wait v1 and v2, present timing, relative targets, nearest-refresh flag; records vblank wake-ups and the scan-line grid next to the reports; `--selftest` makes its device without a window |
| `src/vk-layers.h` | 75 | `--layers-off` (sets `VK_LOADER_LAYERS_DISABLE`), injected-DLL listing |
| `analyze-perf1vk.py`, `analyze-vk-timing.py`, `summarize-presentmon.py` | 305, 352, 75 | analyzers (hitch phases and cause guess; probe reports against the scan grid; PresentMon modes per swapchain) |
| `test-analyze-perf1vk.py`, `test-analyze-vk-timing.py`, `tests/test-own-swap-json.{c,py}`, `tests/test-probe-json.{c,py}`, `run-tests.ps1` | 209, 176, 74+86, 88+70, 14 | 32 unit tests; the two C programs build the log blocks the presenter and the probe write, the Python side parses them and feeds the analyzers |
| `run-windowed-suite.ps1` | 144 | the windowless self-test and the pending windowed runs (refuses to start while DD2.exe runs) |

Design choices:
- **Same seam as perf1, different pacing.** `swap_create / swap_begin / swap_end` is the only place that knows the API. `swap_begin` blocks until an image is free. With two images that is right after the refresh that showed the previous frame, so a present lands on the monitor's next refresh [D, same job as the D3D11 waitable object]. The own backend does the same with `vkWaitForPresent2KHR(N - lag)` before drawing.
- **One render thread per monitor** (perf1 shared one because its D3D11 `pl_gpu` is not thread safe; Vulkan's is: `thread_safe: 1` in libplacebo's own log [L]; but see section 1, point 6).
- **Logs stay perf1's format**, so `analyze-perf1.py` and `analyze-per-monitor-pacing.py` read them [L, run on the headless logs]; extra blocks: `vk` (phases of every loop: acquire, pick, render, present, wait for GPU), `own` (ids, waits, raw timing reports, calibrations), and `events.json` (libplacebo and FFmpeg log lines, window messages and presenter notes, with QPC times).
- **Hitch bookkeeping**: every loop is timed in six phases. The analyzer lists each loop over the threshold with its dominant phase, the log lines within 4 ms of it, and a labelled guess at the cause (shader compilation, window message, swapchain re-creation, decoder, swapchain or presentation engine, GPU, thread not scheduled). The warm-up draws frame 0 twice (first draw and repeat are different passes in libplacebo) plus two more frames, so shader compilation lands before the film clock.
- Extra safety: a render thread stuck in the swapchain (an acquire that never returns) is detected after 5 s; logs are written and the process ends without tearing the swapchain down (destroying it would wait for the lock that thread holds).

## 3. Windowless results

### 3.1 The GPUs and the present set [L, `results-2026-10-11/caps/vk-caps-default.txt`]

| | NVIDIA GeForce RTX 5070 Ti Laptop (driver 617.42, Vulkan 1.4.351) | AMD Radeon 610M (Vulkan 1.4.315) |
|---|---|---|
| DXGI adapter that drives screens | yes: both outputs (LUID 00015887) | no outputs |
| `VK_EXT_present_timing` (rev 3), `VK_KHR_present_id2`, `present_wait2`, `present_id`, `present_wait`, `swapchain_maintenance1`, `present_mode_fifo_latest_ready` | all present, all features true (timing: absolute and relative) | **none** |
| `VK_KHR_calibrated_timestamps` | yes; domains DEVICE, QUERY_PERFORMANCE_COUNTER, PRESENT_STAGE_LOCAL; one read: max deviation 21 ns, QPC 10 MHz | yes; DEVICE and QPC only |
| `VK_EXT_full_screen_exclusive`, `VK_NV_low_latency2` | yes, yes | yes, no |
| `VK_KHR_internally_synchronized_queues` | yes (libplacebo turns it on) | no |
| Video decode queue family (H.264, H.265, AV1 extensions) | family 3, 1 queue | family 4, 1 queue |
| `vkCreateDevice` with swapchain + id + id2 + wait + wait2 + timing + calibrated + swapchain_maintenance1 | **VK_SUCCESS**, every entry point resolved | the two it has: VK_SUCCESS |
| Win32 presentation | queue families 0 and 2 | 0 and 1 |

### 3.2 The Win32 surface [L, `vulkaninfo`, its own 256x256 window]

Present timing supported; absolute target **false**; relative target true; stage queries QUEUE_OPERATIONS_END and IMAGE_FIRST_PIXEL_OUT; present id2 and wait2 supported; full-screen exclusive supported; present modes FIFO, FIFO_RELAXED, MAILBOX, IMMEDIATE, FIFO_LATEST_READY; 2 to 8 images; composite alpha OPAQUE and PRE_MULTIPLIED; formats B8G8R8A8, R8G8B8A8 (UNORM and SRGB) and A2B10G10R10 (all sRGB non-linear). The extension's own page says target-time semantics apply to FIFO modes only [V]. The probe and the own backend read these flags from their own windows and log them (probe: first output lines; own backend: the `own swapchain:` note and `surface_absolute` / `surface_relative` in the `own` block), so a different answer in a full-screen window would show.

### 3.3 The loader and its layers [L, `vk-caps-*.txt`]

- 9 layers listed, **8 implicit** (injected into every process): GPU vendors `VK_LAYER_NV_optimus`, `VK_LAYER_NV_present` ("NVIDIA Presentation Layer"), `VK_LAYER_AMD_switchable_graphics`; third-party `VK_LAYER_RTSS`, `VK_LAYER_reshade`, `VK_LAYER_WEOBS_HOOK`, `VK_LAYER_VALVE_steam_overlay`, `VK_LAYER_VALVE_steam_fossilize`. The ninth, Rockstar's, is explicit. The Epic overlay layer's manifest is missing (the loader logs the error).
- Modules actually loaded into a plain Vulkan process from outside Windows: `RTSSVkLayer64.dll`, `we-graphics-hook64.dll` (default), and `windhawk.dll` (**also with every layer off**: Windhawk injects itself into processes, no loader switch reaches it). The Steam layers do not load here (their manifests need an enable variable that Steam sets); ReShade's DLL fails to initialise.
- `--layers-off third-party` leaves 3 layers (the vendors') and loads no third-party layer DLL; `all-implicit` leaves none (the device and its features are unchanged) [L].
- **Start-up cost does not depend on them**: instance extensions 143 / 126 / 114 ms, instance 39 / 37 / 36 ms, device 577 / 589 / 537 ms (default / third-party off / all implicit off), first SPIR-V translate about 108 ms. Whether they cost anything at **present** time is the windowed A/B [P].

### 3.4 The presenter without a window [L, `selftest-headless*`, 4 to 8 s, next to a running game, below-normal priority]

Offscreen targets of the monitors' own sizes (2560x1600 and 1920x1080), Judas (HEVC 23.976 fps) and Hotel (H.264 25 fps), default preset, two render threads, "nearest" with the aimed start:
- 0 render failures, 0 queue stalls (`queue_more` 0, `empty_mix` 0), 0 hitches. Render CPU p50 0.18 / p99 1.1 ms (D3D11 in perf1: 0.03 / 0.3 ms: Vulkan's call is about six times dearer on the CPU, still 4% of a 240 Hz refresh); GPU passes p50 0.03 / p99 0.25 ms.
- Shader compilation: about ten pipelines at 10 to 19 ms each plus a first SPIR-V translation of 98 to 118 ms, all **before** the film clock started; none during playback.
- Start-up: instance enumeration 115-143 ms, device creation 460-590 ms (the NVIDIA driver's own cost).
- Not a pacing result: the "present" of this mode is a wait for the GPU, and with the game running on the same GPU that wait shows p99 7 ms, max 8.4 ms (the first run). Not attributable to our code.
- Vulkan Video (`--decode vulkan`): HEVC and H.264 decode on libplacebo's device, frames sampled without a copy, locked to 25 fps / hold 4 on the 100 Hz master for Hotel [L].
- Re-run after the review changes (section 7), game not running: Hotel, 5 s, 1328 and 554 presents on the two monitors, render CPU p50 0.08-0.09 / p99 0.7-0.75 ms, 0 failures, 0 hitches, every acquire gap exactly 1 refresh on both [L, `selftest-headless-rerun/`].

### 3.5 What libplacebo's Vulkan swapchain does, read from source [V, libplacebo `1dd0226`]

`minImageCount = swapchain_depth + 1` (`swapchain.c:409`); acquire with `UINT64_MAX` (`:893`); `vkQueuePresentKHR` inside `submit_frame` (`:1021`) with only a present fence from swapchain_maintenance1 in its chain (`:999`); `swap_buffers` waits for GPU completion only (`:1049`); explicitly disallows full-screen exclusive (`:568`, `:774`); alpha preference order (`:590-595`). P0's pacing finding fits this [D]: depth 1 gives 0 skipped refreshes but jitter, because the loop is paced by an acquire that returns when the presentation engine releases an image.

### 3.6 Windowless self-tests of the windowed code [L, `selftest-device/`]

Added so the first windowed run does not fail on set-up:
- `vk-timing-probe --monitor 0|1 --selftest`: picks the GPU by the monitor's LUID (NVIDIA on both), makes the device with the whole present set (only the extensions that exist, each with its dependencies; features switched on only where the device reports them), finds a graphics queue family that presents to Win32, and asks which clocks the device can read next to QPC. Output on both monitors: `present_id 1, present_wait 1, present_id2 1, present_wait2 1, present_timing 1 (absolute 1, relative 1)`; `device can calibrate (domains: DEVICE QUERY_PERFORMANCE_COUNTER PRESENT_STAGE_LOCAL): QueryPerformanceCounter against other clocks: yes`; `selftest ok ... queue family 0`.
- `perf1vk-lgpl --device-test` for the default, `--backend own`, `--decode vulkan` and both together: all four create libplacebo's device; `present timing set ON` with `--backend own`; FFmpeg adopts the device with `--decode vulkan`. This was the one device-creation path the earlier windowless runs had not exercised (they never asked for the timing set).
- `run-windowed-suite.ps1 -Only selftest` runs all of the above (about 3 s, no window) and is the first step of a full suite; a failure stops the suite before any window opens.

## 4. Present timing: what is known and what the windowed runs decide

| Question | Known now | Decided by [P] |
|---|---|---|
| Are present id / wait (v1 and v2) usable on a Win32 surface in a borderless window? | Extensions, features and surface support are all true [L]. The extension rev 3 needs the v2 ids [V]. | probe `v1-wait`, `v2-wait`: wait results, return times against the scan grid |
| What does timing report? | Two stages; times in a time domain chosen from the swapchain's list; the device offers QPC, per-stage and device clocks for calibration [L] | probe `v2-wait-timing`: domain list, calibrated rate, results per present |
| In the QPC domain, are reports QPC ticks or nanoseconds? | Not stated on the extension pages read [V]; the spec says target times are nanoseconds [V] | the analyzers pick the unit that puts a report next to its present call and print `qpc unit ticks` or `qpc unit ns` (unit-tested with both) |
| Is "first pixel out" our top-row time? | Unknown. The three candidates: the top row; the vblank start, one blank earlier (laptop 0.46 ms, external 0.31 ms at 100 Hz); a compositor time [D] | `fpo_minus_top_ms` and `fpo_reading` in the analyzer: "equals the top row" / "equals the vblank start" / "neither" |
| Which refresh did a present land on, and when? | The report gives the time; the refresh follows from the grid [D] | `landing_vs_target_refreshes` (own backend): 0 = landed where aimed |
| Are target times honoured? | Absolute: the surface says not supported [L]; the probe skips that experiment with a note. Relative: supported; semantics "least time the previous picture stays up", targetTime in ns [V] | probe `rel-1 ... rel-5`, `rel-2.5`, `-nearest`: refreshes between consecutive pictures |
| Does the wait return at the flip? | Unknown | `wait_return_minus_fpo_ms`, `wait_return_vs_top_ms` |
| Does a window reach independent flip? | Unknown; libplacebo's swapchain asks for premultiplied alpha [V] | PresentMon `PresentMode` per swapchain (section 6.5) |

## 5. Design implication

- **Presenter = one seam, two implementations.** Film clock, frame picking, picture layout, row timing and logs are shared (perf1vk-main.c is perf1-main.c with the window, pacing and present calls replaced); D3D11 uses a waitable DXGI swapchain, Vulkan uses an own swapchain. A macOS port through MoltenVK would be a third implementation of the same four calls [D; MoltenVK's support for these extensions not checked].
- **The device is created by libplacebo/the app, everything else adopts it** (FFmpeg's Vulkan decoder already does).
- **One render thread per monitor on Vulkan**, one device-wide thread on D3D11, with the shared-recording caveat of section 1, point 6.
- **Present timing is a bonus on NVIDIA, never the base.** The film clock stays on the scan-line grid (ADR 0025); present reports only confirm which refresh a present landed on.
- **A refused present is not retried.** When the timing queue is full the present is lost and the next frame goes on (section 7); the presenter must treat "results stopped completing" as a fault to log, not a reason to block.
- **Set `VK_LOADER_LAYERS_DISABLE` before the first Vulkan call** (the performer owns its process); at least warn the user about overlay hooks. The suite settles how much that matters here.
- If the windowed runs show hitches even with the layers off, or a first-pixel-out time unrelated to the grid, or no independent flip with an opaque own swapchain, the verdict becomes "doesn't (yet)" and the D3D11 presenter stays the only one.

## 6. Pending: the windowed suite and the full-screen runs [P]

### 6.1 Command

PowerShell 7, from the spike folder. Half-size, not-topmost, not-activated windows on both screens; no full screen, no display change; at most about 30 s per run, about 5 minutes in all. It refuses to start a run while `DD2.exe` is running (`-Games DD2,Other` to extend the list).

```powershell
Set-Location 'C:\Users\tamla\Downloads\Telegram Desktop\panorama\spikes\performer-p1-vulkan'
pwsh -File .\run-windowed-suite.ps1 -PresentMon .\tools\presentmon-2.6.0\PresentMon-2.6.0-x64.exe
# steps: selftest | probe | lp | layers | own | decode (comma separated), e.g.  -Only probe,own
# results: .\results-2026-10-11\windowed\  (one folder per run, <run>-analysis.txt, <run>-presentmon.txt)
```

| Step | Runs | Output |
|---|---|---|
| `selftest` | **no window**: the probe's device on each monitor, and the presenter's device for each backend / decoder combination | `selftest-*.txt` |
| `probe` | `vk-timing-probe` on each monitor, two experiment lists (a: v1-wait, v2-wait, v2-wait-timing, v2-acquire-timing, rel-1, rel-2; b: rel-3, rel-5, rel-2.5, rel-2.5-nearest, rel-2.4-nearest, rel-2-acquire), 2 s each | `probe-a`, `probe-b` + `*-analysis.txt` |
| `lp` | presenter on libplacebo's swapchain: Judas (23.976 fps, event level debug) and Hotel (25 fps), 15 s | `lp-judas`, `lp-hotel` |
| `layers` | the Judas run with third-party layers off and with every implicit layer off | `lp-judas-third-party-off`, `lp-judas-all-implicit-off` |
| `own` | presenter on the own swapchain (3 images, wait lag 1), opaque, and premultiplied | `own-judas`, `own-judas-premultiplied` |
| `decode` | Vulkan Video decode, Judas and Hotel | `lp-judas-vkdecode`, `lp-hotel-vkdecode` |

Analyze one run again: `python -I .\analyze-perf1vk.py <dir> [--hitch-ms 20]`; probes: `python -I .\analyze-vk-timing.py <dir>`; PresentMon: `python -I .\summarize-presentmon.py <csv>`. The logs also feed perf1's `analyze-perf1.py` and `analyze-per-monitor-pacing.py` unchanged. The absolute-target experiment is off unless asked for: `bin\run.cmd vk-timing-probe.exe --monitor 0 --windowed --experiments abs-ahead --try-absolute` (expected: skipped, the surface does not accept absolute targets).

### 6.2 What each run should read (expectations, not results)

| Where | Line | Expected, and what a different reading means |
|---|---|---|
| selftest (already run, section 3.6) | `selftest ok ... queue family 0`, `device test ok ... present timing set ON` | anything else: stop; no window was opened |
| probe, any monitor | `surface ... present timing: supported 1, absolute 0, relative 1, stage queries 0x5` | same as vulkaninfo; anything else: the window's surface differs from vulkaninfo's |
| probe `v1-wait`, `v2-wait` | `results {'0': N}` for present and wait | 0 = VK_SUCCESS everywhere: id and wait usable. `wait ... returns against the nearest top row`: tight (spread under 0.5 ms) if the wait is tied to the display |
| probe `v2-wait-timing` | `timing: N results (N complete)`; `domain clock / QPC rate`; `qpc unit` | N about equal to presents; rate 1.0000 (ticks are ns, same speed as QPC); `qpc unit` only when the swapchain offers the QPC domain |
| same | `first pixel out minus nearest top row` and the `->` reading | one of three readings, see section 4. Spread under 0.3 ms means the report is tied to the grid |
| probe `v2-acquire-timing` | `presents per refresh` | **1.0 on both monitors** if each window flips on its own clock; **about 2.4 on the 100 Hz external** if DWM composes it on the laptop's 240 Hz (the P1 windowed D3D11 finding). Windowed pace is not a full-screen pace |
| probe `rel-k` | `refreshes between consecutive pictures` / `honoured` | median gap = k (rounded up) = honoured; gap 1 = ignored. `rel-2.5` expects 3, `rel-2.5-nearest` 2 or 3, `rel-2.4-nearest` 2 |
| any probe experiment | a bracketed `[status]` after its name | empty = ran to the end. `skipped: ...` = the device or surface lacks the feature. `present timing queue full ...` = results stopped completing (a finding) |
| `lp-judas` | `swapchain rgb10a2 premultiplied (2 distinct images)` | premultiplied confirms the libplacebo reading; `opaque` means I read the preference order wrong; more than 2 images means the swapchain was re-created (events say when) |
| `lp-*` | `acquire gaps in refreshes {1: all}`, `missed refreshes (inferred) 0`, `hitches 0` | gaps of 2 or more are refreshes without a new picture; each hitch prints phases, nearby log lines and a likely cause |
| `lp-judas` / `lp-hotel` | `refreshes per frame` | Judas is not locked on 240 + 100 Hz: DISPLAY1 {9, 10, 12}, DISPLAY2 {4, 5}; Hotel locked: DISPLAY2 {4}, DISPLAY1 {9, 10}. All **inferred** on this backend (`N of N screen times inferred`) |
| `own-judas` | `swapchain rgb10a2 opaque`; `present timing: R of P presents reported`; `refreshes between the refresh aimed at and the one reached {0: ...}` | R about P; all 0 means every present landed where aimed; `{1: n}` = n late presents. `fallback_times 0`. The event log line `own swapchain: ... composite alpha opaque ... this surface takes absolute targets no, relative yes` |
| `own-judas-premultiplied` | same, composite alpha premultiplied | if PresentMon shows Composed for this run and Independent Flip for `own-judas`, the alpha is what blocks independent flip |
| `lp-*-third-party-off`, `-all-implicit-off` | hitches, `phases (ms)` (acquire / present p99 and max), `present interval` p99 | compare with `lp-judas`: fewer hitches or a tighter p99 with the layers off puts the blame on the hooks. `all-implicit-off` also removes `VK_LAYER_NV_present`: if swapchain creation then fails, that is a finding |
| `lp-*-vkdecode` | `hw_fmt vulkan/p010le` or `vulkan/nv12`, `queue_more 0` | decode on the GPU with the same pacing |
| PresentMon txt | `present_mode` per swapchain | `Hardware: Independent Flip` = independent flip. Half-size windows are expected `Composed: Flip` or overlay variants; the question is answered in the full-screen runs |

### 6.3 Full-screen runs (the controller, with the user)

Both screens are taken over; Esc stops; `--refresh` holds the mode with CDS_FULLSCREEN and Windows restores it when the process ends (ADR 0023). 24 fps content (Judas, 23.976): hold 4 at 96 Hz, 3 at 72 Hz, speed lock +0.1 %. Each run about 35 s.

```powershell
Set-Location 'C:\Users\tamla\Downloads\Telegram Desktop\panorama\spikes\performer-p1-vulkan'
$f  = 'C:/Users/tamla/Downloads/[Judas] Nama-Anaru - S01E02.mkv'
$pm = (Resolve-Path '.\tools\presentmon-2.6.0\PresentMon-2.6.0-x64.exe').Path
function Start-PM($name) {   # PresentMon, unelevated: this account is in "Performance Log Users"
    $a = '--process_name','perf1vk-lgpl.exe','--output_file',".\results-2026-10-11\$name-presentmon.csv",'--v2_metrics','--write_display_metadata','--no_console_stats','--terminate_on_proc_exit','--stop_existing_session','--session_name','perf1vk-fs'
    $p = Start-Process $pm -ArgumentList $a -WindowStyle Hidden -WorkingDirectory (Get-Location).Path -PassThru; Start-Sleep 2; $p }
function Stop-PM($p, $name) { if (-not $p.WaitForExit(8000)) { $p.Kill() }; python -I .\summarize-presentmon.py ".\results-2026-10-11\$name-presentmon.csv" }

# 1. libplacebo's swapchain, layers as installed
$p = Start-PM 'fs-lp-96hz'
.\bin\run.cmd perf1vk-lgpl.exe --file $f --mode nearest --refresh DISPLAY2=96 --seconds 20 --event-level debug --out .\results-2026-10-11\fs-lp-96hz
Stop-PM $p 'fs-lp-96hz'; python -I .\analyze-perf1vk.py .\results-2026-10-11\fs-lp-96hz

# 2. the same with third-party layers off (A/B)
.\bin\run.cmd perf1vk-lgpl.exe --file $f --mode nearest --refresh DISPLAY2=96 --seconds 20 --layers-off third-party --out .\results-2026-10-11\fs-lp-96hz-layers-off
python -I .\analyze-perf1vk.py .\results-2026-10-11\fs-lp-96hz-layers-off

# 3. our own swapchain (opaque, present_wait2 pacing, present-timing reports)
$p = Start-PM 'fs-own-96hz'
.\bin\run.cmd perf1vk-lgpl.exe --file $f --mode nearest --refresh DISPLAY2=96 --seconds 20 --backend own --vk-depth 2 --out .\results-2026-10-11\fs-own-96hz
Stop-PM $p 'fs-own-96hz'; python -I .\analyze-perf1vk.py .\results-2026-10-11\fs-own-96hz

# 4. 72 Hz and a 300 s soak on the own swapchain
.\bin\run.cmd perf1vk-lgpl.exe --file $f --mode nearest --refresh DISPLAY2=72 --seconds 20 --backend own --vk-depth 2 --out .\results-2026-10-11\fs-own-72hz
.\bin\run.cmd perf1vk-lgpl.exe --file $f --mode nearest --refresh DISPLAY2=96 --seconds 300 --backend own --vk-depth 2 --out .\results-2026-10-11\fs-own-96hz-soak300
```

| Line | Should read |
|---|---|
| console `film ...` | `master DISPLAY2 96.0000 Hz; speed LOCKED ... hold 4` (72 Hz: hold 3) |
| console `predicted seam offset` / analyzer `row-light seam offset` | as perf1's table in the row-timing report: aim-row median = the printed prediction within 0.05 ms (own backend: measured screen times; libplacebo backend: inferred, so only a plumbing check) |
| analyzer `refreshes per frame` | DISPLAY1 {10}, DISPLAY2 {4} (72 Hz: {10}, {3}) |
| analyzer `hitches` | 0 |
| own backend `present timing` | `refreshes between the refresh aimed at and the one reached {0: all}`; first pixel out minus top row: same reading as the probe; event log note `this surface takes absolute targets no/yes`: does a full-screen window's surface say the same as the half-size one |
| PresentMon `present_mode` | `Hardware: Independent Flip` on both swapchains = independent flip reached; `Composed: Flip` = not. Compare run 1 with run 3: the alpha mode is the only difference between them besides the pacing |

### 6.4 Running every Vulkan program with the implicit layers off

| Program | With its flag | Any Vulkan program, from the shell |
|---|---|---|
| `perf1vk-lgpl.exe`, `vk-timing-probe.exe`, `vk-caps.exe` | `--layers-off third-party` (RivaTuner, ReShade, OBS-style hook, Steam, Epic; the GPU vendors' layers stay) or `--layers-off all-implicit` (also `VK_LAYER_NV_present` and `VK_LAYER_NV_optimus`). The flag sets `VK_LOADER_LAYERS_DISABLE` inside the process before the first Vulkan call; the log records the setting | PowerShell: `$env:VK_LOADER_LAYERS_DISABLE = '~implicit~'`; cmd: `set VK_LOADER_LAYERS_DISABLE=~implicit~`. Also takes comma-separated names or globs, e.g. `VK_LAYER_RTSS,VK_LAYER_reshade,VK_LAYER_WEOBS_HOOK,VK_LAYER_VALVE_*` (loader 1.3.234 or later [K]; this PC has 1.4.341 and the variable works there [L]) |
| the whole suite | `pwsh -File .\run-windowed-suite.ps1 -LayersOff third-party` (or `all-implicit`): every step with the layers off; results in `results-2026-10-11\windowed-layers-<mode>`, next to the first pass | - |

- **Per layer, from the layers' own manifests** (set to `1`): `DISABLE_RTSS_LAYER`, `DISABLE_VK_LAYER_reshade_1`, `DISABLE_VULKAN_OBS_CAPTURE`, `DISABLE_VK_LAYER_VALVE_steam_overlay_1`, `DISABLE_VK_LAYER_VALVE_steam_fossilize_1`, `DISABLE_LAYER_NV_PRESENT_1`, `DISABLE_LAYER_NV_OPTIMUS_1`, `DISABLE_LAYER_AMD_SWITCHABLE_GRAPHICS_1`.
- **Not reachable by any of these**: `windhawk.dll`, injected into every process by the Windhawk engine (Windhawk's own process-exclusion list would be the switch; not touched).
- Nothing was uninstalled or changed in the registry; the loader variable lives in the process only.

**Does the suite already run an A/B with and without the layers? Partly, and it can run fully.** Built in: step `layers` runs the Judas presenter on libplacebo's swapchain with third-party layers off and with every implicit layer off, against `lp-judas` (layers as installed): same file, windows and 15 s, one number to compare (hitches, p99 of the present interval, acquire/present phases). Not built in for the probe, the own swapchain and Vulkan Video decode. A second pass of the whole suite does that (`-LayersOff`, table above). The full-screen list in 6.3 has run 2 as the same A/B in full screen.

### 6.5 Telling whether a Vulkan window reached independent flip

- **PresentMon** (Intel, MIT, Authenticode signature valid; SHA-256 `B2A706BC6AD475749E3B7E3409263AA1E6906D45BDCF993F6DBC0F660188F1AF`; copy in `tools/presentmon-2.6.0/`, git-ignored) reads Windows' present events (ETW) and prints a `PresentMode` per swapchain. **It does not need administrator rights here**: this account is in `BUILTIN\Performance Log Users`, the group PresentMon accepts instead of elevation [L, token groups], and a 3 s trace (filter on a process that was not running, no output file) started and ended cleanly unelevated, leaving no session behind [L]. It printed one warning: without elevation, short-running processes may show as `<unknown>` and cannot be matched by `--process_name`; the presenter lives 25 s or more, so it should match, but that is not yet shown (no window was open to trace). `summarize-presentmon.py` prints the modes per swapchain.
- Without PresentMon: a window on the 100 Hz external that presents at the laptop's 240 Hz (2.4 presents per refresh in the probe's `v2-acquire-timing`) is composed on the main display's clock; one that presents at 100 Hz is not. A report whose first-pixel-out sits on the monitor's own grid points the same way. These separate composed from not composed; only PresentMon separates independent flip from an overlay plane.
- D3D11 had `GetFrameStatisticsMedia`; Vulkan has no such call, and the swapchain behind NVIDIA's Vulkan present path is not reachable from the process.

## 7. Review and checks [L]

### 7.1 Code review

An independent review of the spike raised 21 findings, and a second read of the changes added a few; all are applied. The ones that changed behaviour:
- **Refused presents.** `vkQueuePresentKHR` with `VK_ERROR_PRESENT_TIMING_QUEUE_FULL_EXT` counts as "rejected by the presentation engine": the semaphore wait still executes and the image's acquisition is released, so the same present can never be retried with the same semaphore or id [V, docs.vulkan.org `vkQueuePresentKHR`]. The own backend now counts it, reads the results out so the next present has room, and carries on with the next frame; the probe ends that experiment with a note. The extension's proposal page suggests draining, growing the queue, or presenting without timing [V].
- **Waits only name accepted presents.** Both programs remember the newest present the engine accepted (`last_ok`) and never wait for a later id, which would have stalled until the timeout.
- **Image sharing.** The wrapped images are `VK_SHARING_MODE_CONCURRENT` over the queue families libplacebo draws and copies on (what `pl_vulkan_wrap` expects and libplacebo's own swapchain does); `--sharing exclusive` opts out.
- **Teardown order.** libplacebo finishes its work, the graphics queue is idled under libplacebo's queue lock, then the semaphores and the swapchain go.
- **Acquire-semaphore reuse.** Without a wait that confirms earlier work is done (`--wait-lag 0`, a timed-out wait) the queue is idled first (counted as `queue_idle_waits`): slower, always correct.
- **Queue and usage.** The queue is fetched with `vkGetDeviceQueue2` and libplacebo's queue flags (internally synchronized queues turn libplacebo's lock into a no-op); the image usage follows the surface and the format's features; composite alpha is checked against what the surface offers.
- **Return codes.** `VK_SUBOPTIMAL_KHR` is accepted from waits; `VK_INCOMPLETE` from `vkGetSwapchainImagesKHR` is a failure (more images than the arrays hold).
- **Probe device set-up.** Extensions only with their dependencies; feature structures only for enabled extensions, switched on only where the device reports them (the device is made without a surface, the surface is checked afterwards); the probe's experiments skip with a note when the device or surface lacks the id2 / wait2 pair, the first-pixel-out stage, or the kind of target time (the absolute experiment is skipped on this surface).
- **Time domains.** The domain is chosen among those the device can calibrate against QPC (auto: QPC, then per-stage, then swapchain-local); both stage clocks are calibrated at every re-calibration and zero reads are dropped; the unit of a QPC-domain report is detected, not assumed (analyzers, with tests).
- **Barrier.** The image's first layout change waits at the transfer stage, where the acquire semaphore is waited.
- Seen in the review and recorded, not code: the shared command recording (section 1, point 6).

### 7.2 Checks

- Compile: `compile.ps1 lgpl all` builds the three programs and the two test programs with no warnings.
- `pwsh -File .\run-tests.ps1`: **32 unit tests pass** (11 + 14 + 4 + 3). Analyzers on made-up logs with known answers (hitch phases and causes, missed refreshes, own-backend landing with the report at the top row or at the blank start, clock-rate interpolation, QPC domain in ticks and in nanoseconds, relative-target checks); and the exact JSON the presenter's own-swapchain block and the probe write, produced by the real C writer from a hand-built state, parsed and read by the analyzers.
- Windowless runs: sections 3.3, 3.4 and 3.6; headless logs run through `analyze-perf1.py` unchanged.
- The suite refuses to start a windowed step while the game runs (tested with the game running, no window opened); unknown step names are rejected; the `selftest` step runs without a window.
- Caveats: `vulkaninfo` opened its own small window twice (allowed by the brief). Two git-ignored bytecode caches exist from before the analyzers were told not to write them, one in this folder and one in `spikes/per-monitor-presenter/`; a hook blocks shell commands that name them, so they were left. They are harmless and can be deleted.

## 8. Not done

- Every windowed measurement: present timing against the scan grid, pacing and hitches with a visible window, composed-versus-independent flip, the layers A/B, the own swapchain in a window, Vulkan Video in windows.
- The full-screen runs (the controller's commands are in 6.3), the 300 s soak per backend, the 72 Hz run.
- A PresentMon trace of the real presenter (only the feasibility check was done).
- Setup B's AMD iGPU path (the 610M has no outputs on this PC).
- P0's second suspect, the GPU-engine sampler running next to the player.
- A libplacebo patch, any change outside `spikes/performer-p1-vulkan/` and this report, any commit.

## 9. Unresolved questions

1. Does NVIDIA's Vulkan present put a premultiplied-alpha swapchain on the compositor path while an opaque one can reach independent flip? (`own-judas` against `own-judas-premultiplied`, PresentMon.)
2. What instant is "image first pixel out": top row, blank start, or a compositor time? Does that change between composed and full-screen windows?
3. In the QPC time domain, do reports count ticks or nanoseconds? (The analyzers detect it; the extension pages read do not say.)
4. Are relative targets honoured and rounded as the spec says, also with the queue ahead? What does `VK_PRESENT_TIMING_INFO_PRESENT_AT_NEAREST_REFRESH_CYCLE_BIT_EXT` do exactly?
5. The device feature says absolute targets work, the surface says they do not. Is that the Windows compositor path only, and does a full-screen window's surface say yes? (The own backend now logs it.)
6. Do P0's 300-720 ms hitches come back, and with which layers loaded? (RTSS and the OBS-style hook are loaded here; ReShade fails to initialise.) Is Windhawk's injected engine involved? P0 saw them only in runs with its PDH GPU-engine sampler (`sample-gpu-cpu.ps1`, one query a second) next to the player, in 2 of 12 runs, and in none of three bare 60 s runs: the sampler is a second suspect. The suite does not run it; a 60 s windowed run with and without it, layers on and off, separates the two.
7. Does `vkWaitForPresent2KHR` return at the flip, and with how much jitter, against DXGI's waitable object?
8. Does `VK_LAYER_NV_present` have to stay for a working swapchain on this driver (the `all-implicit-off` run)?
9. Own swapchain or a libplacebo patch (`composite_alpha`, ids): which to carry? The patch alone does not give timing.
10. Do the two render threads' shared command recording show up as cross-monitor stalls in a window, and is one `pl_vulkan` object per monitor then worth it?
11. Setup B's monitor on the AMD iGPU: which presenter path (D3D11 waitable, Vulkan without ids)? Not testable here: the 610M has no outputs.
12. Vulkan Video decode together with the own swapchain in windows, and a 5-10 minute soak per backend.
13. Whether the loader-layer problem will exist on other users' PCs: overlays are common (Steam, Discord, OBS, RTSS); a warning or an automatic `VK_LOADER_LAYERS_DISABLE` at start is a product decision.

## Sources

- Vulkan extension page and reference pages: `https://docs.vulkan.org/refpages/latest/refpages/source/VK_EXT_present_timing.html` (the registry URL redirects there), `https://docs.vulkan.org/features/latest/features/proposals/VK_EXT_present_timing.html`, `https://docs.vulkan.org/refpages/latest/refpages/source/VkPresentTimingInfoEXT.html`, `https://docs.vulkan.org/refpages/latest/refpages/source/vkQueuePresentKHR.html`.
- libplacebo source `1dd0226` (`src/vulkan/swapchain.c`, `gpu.c`, `gpu.h`, `context.c`, `include/libplacebo/vulkan.h`) and FFmpeg 9.0.2 headers, read from the local build tree.
- PresentMon 2.6.0: `https://github.com/GameTechDev/PresentMon/releases/download/v2.6.0/PresentMon-2.6.0-x64.exe`.
- On this PC: `vulkaninfo`, `vk-caps.exe` output (`results-2026-10-11/caps/`), the Vulkan layer manifests, the windowless run logs (`results-2026-10-11/selftest-*`).

Status: DONE_WITH_CONCERNS
Summary: The Vulkan two-window presenter, the own-swapchain backend, Vulkan Video decode on libplacebo's device, a present-timing probe and analyzers are built, compile clean, pass 32 unit tests and windowless self-tests; the extension and device findings are measured. Verdict: works with limits (provisional).
Concerns: every windowed measurement (present timing against the scan grid, pacing, hitches, independent flip, the layers A/B) is pending until the screens are free; the verdict can change to "doesn't" if those runs fail as listed in section 5.
