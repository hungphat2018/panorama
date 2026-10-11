---
title: "Spike: one window per monitor, switching frames in step (Setup A)"
date: 2026-10-11
type: spike
answers: "M1 phase 01 (pace): can per-monitor presentation keep both sides of a bezel in step with an even pace, where one spanning window can't?"
harness: spikes/per-monitor-presenter/
results: spikes/per-monitor-presenter/results-2026-10-11/
---

# Spike: one window per monitor, switching frames in step

Terms: a *refresh* is one chance for a screen to show a picture (240 a second on the laptop). *Pace* is how many refreshes each film frame stays up. The *seam offset* is how far apart in time the two sides of the bezel switch to the next film frame. *Independent flip* (also "hardware overlay") means the GPU shows the window's picture directly, without Windows composing the desktop first.

## Answer

**Yes, on Setup A.** One process with one borderless window and one flip-model swapchain per monitor:

- Both windows ran in hardware overlay (independent flip) the whole time, at the same time, each paced by its own monitor. A single spanning window is composed on the main display's clock instead ([mixed-rate spike](spike-261011-1034-one-window-across-mixed-refresh-rates.md)).
- With the film clock locked to the slower monitor (the "master"), and the other monitor switching on its own refresh **nearest** each of the master's frame changes, both screens kept an even pace and switched frames **0.28 ms apart** (p5–p95: −0.28 to −0.27 ms), steady over 380 frames.
- The 0.28 ms is luck of how the two refresh clocks happen to line up. The scheme guarantees at most half the faster monitor's refresh period: ±2.1 ms here, against −1.7 to +6.7 ms for the spanning window.
- Variable refresh (VRR) on the laptop panel did not respond for these windows: presents with tearing allowed tore on the fixed 240 Hz grid. G-SYNC is probably off for windowed/borderless apps in the driver settings (not checked yet).

Consequence for M1: the performer needs one swapchain per monitor and a shared film clock that it schedules itself. Stock mpv (one window) can't do that, so this points to the own per-monitor renderer for the concept demo (decided in phase 02, with the engine research).

## How it was measured

- `per-monitor-presenter.cpp` (C++17, Win32 + D3D11 + DXGI 1.6, per-monitor DPI aware): one window exactly covering each monitor, `FLIP_DISCARD`, 2 buffers, frame-latency waitable object, max latency 1, one render thread and one device per monitor. Each frame: bars moving along one physical path across the gap, plus a 16-bit barcode of the film frame number.
- Each monitor's refresh grid is fitted from `IDXGIOutput::WaitForVBlank` times. When each present reached the screen comes from `IDXGISwapChain::GetFrameStatistics` (`SyncQPCTime`); for vsync'd presents it matched the logged refresh times within 0.06 ms and fell 3–4 ms (240 Hz) and ~9.7 ms (100 Hz) after the present call, i.e. on the next refresh, as expected. The composition mode comes from `IDXGISwapChainMedia::GetFrameStatisticsMedia`.
- Film rate 20 fps: 240 Hz = 12 refreshes per frame, 100 Hz = 5, so both native rates have an even pace (stands in for 24 fps on 240 + 96 Hz, which needs a custom mode).
- Modes:
  - **own**: each monitor switches on its first refresh at or after the film frame's time.
  - **nearest**: the master (the 100 Hz external) counts its own refreshes (5 per frame); the laptop switches on its refresh nearest each master switch.
  - **follow**: the laptop presents at the master's refresh times with tearing allowed, so a VRR panel would refresh in step.
- 20 s per mode, laptop as main display, Windows 11 25H2 (26200), RTX 5070 Ti Laptop, driver 617.42.

## Results

| Mode | Laptop refreshes per frame | External refreshes per frame | Seam offset, external − laptop (ms) p5 / median / p95 | Composition (polls) |
|---|---|---|---|---|
| nearest | 12 × 379, 11 × 1 | 5 × 379, 4 × 1 | −0.28 / −0.28 / −0.27 | overlay 4869 + 2027, none 3 + 6 (start-up) |
| own | 12 × 371, 11 × 5, 13 × 4 | 5 × 379, 4 × 1 | −1.13 / −1.12 / −1.11 | overlay 4725 + 2028 |
| follow (VRR not active) | 2–3 refreshes between flips on the 240 Hz grid | 5 | not meaningful: the laptop tore | overlay, 10 composed |

- The single odd frame in each nearest/own row is the first frame.
- Own mode lands at an arbitrary steady offset (+2.2 ms in an earlier run, −1.1 ms in this one); its few 11/13 frames come from the spike's film clock drifting against the laptop's grid, which a real player re-syncs.
- First nearest run, with the film clock free-running instead of locked to the master: 57 of 380 laptop frames stumbled, because frame starts fell within jitter of a master refresh and the master's switch flipped between two refreshes. Locking the film clock to the master's refresh count removed it. Rule: the film clock is derived from the master's refresh count, never compared against it.
- Max offsets of 5.5 ms in both runs are the first frame.

## What this means

1. **Presentation model:** one swapchain per monitor, all in one process, each paced by its own monitor. Windows gave both independent flip with no extra effort: a borderless top-most window exactly covering the monitor.
2. **Clock model:** one film clock locked to the master monitor's refresh count (the slowest picture monitor, whose rate must be a whole multiple of the playback rate); every other monitor switches on its refresh nearest the master's switch. Offset ≤ half the faster monitor's period; pace even on every monitor whose rate is a multiple of the playback rate.
3. **Better than nearest:** a VRR follower (offset ≈ 0 at any row we aim at) once G-SYNC/VRR is on for borderless windows; or re-setting the display mode at playback start, which re-rolls the clocks' phase (ADR 0005 allows it), until the offset is small. Neither tested.
4. **For 24 fps on Setup A** the external needs 96 or 72 Hz (custom mode), or VRR; at 100 Hz no even 24p pace exists.

## Limits

- Signal timing, not light: when each refresh started, not when the panel lit up. Panel processing delay differences need a camera.
- Rows scan top to bottom at different speeds (≈ 4 ms per screen at 240 Hz, ≈ 10 ms at 100 Hz). Aligning refresh starts aligns the top rows; lower rows drift apart by up to ≈ 6 ms at the bottom. Aiming the switch at mid-height halves that; not measured.
- One 20 s run per mode, 20 fps instead of 24, synthetic content, no video decode or audio in the loop.
- Phase drift between the two clocks (≈ 4 ppm from their exact rates) will move the nearest refresh across a boundary about every 17 min, giving one 11- or 13-refresh frame on the laptop. Not seen in 20 s; derived.

## Film speed: 24 fps on 240 + 96 Hz (2026-10-11 14:31)

The external got custom 96 and 72 Hz modes (`spikes/custom-refresh/`, NVAPI trial then save, native pixel clock with a longer vertical blank: 2000 x 1160 at 222.72 MHz for 96 Hz, scan 9.70 ms). Windows measured 96.0000 and 72.0000 Hz; the user confirmed a normal picture on both. The presenter switched the external to 96 Hz itself with `CDS_FULLSCREEN` for each run, and Windows put 100 Hz back when it exited (ADR 0023).

| Mode | Laptop refreshes per frame | External refreshes per frame | Seam offset, external − laptop (ms) p5 / median / p95 | Composition |
|---|---|---|---|---|
| nearest | 10 × 453, 9 × 2, 11 × 1 | 4 × 455, 3 × 1 | −1.12 / −1.11 / −1.10 | overlay on both, whole run |
| own | 10 × 455, 9 × 1 | 4 × 455, 3 × 1 | −1.10 / −1.09 / −1.08 | overlay on both, whole run |

Even 24 fps pace on both screens, offset steady at 1.1 ms, inside the ±2.1 ms bound. The two modes coincide here because the clocks' phase happened to put the laptop's first refresh after each switch also nearest to it. Results: `spikes/per-monitor-presenter/results-2026-10-11-24fps-96hz/`.

## Aimed start: picking the alignment class (2026-10-11 14:37)

Variable refresh is not available on Setup A: NVIDIA Control Panel has no G-SYNC page (user, 2026-10-11), and the driver reports it as not possible on both screens. But the GPU drives both screens from one clock, so their refreshes keep a fixed ratio (240:96 = 5:2, 240:72 = 10:3) and the master's refreshes fall into a few alignment classes against the laptop's grid (2 classes 2.08 ms apart at 96 Hz, 3 classes 1.39 ms apart at 72 Hz; the presentation research measured 5 at 100 Hz). The presenter now starts the film on the master refresh whose nearest laptop refresh is closest; every later frame start keeps that class.

| External rate | Predicted offset | Measured, external − laptop (ms) p5 / median / p95 | Laptop / external refreshes per frame | Composition |
|---|---|---|---|---|
| 96 Hz | 0.40 | 0.38 / 0.39 / 0.40 | 10 × 455 / 4 × 455 | overlay on both |
| 72 Hz | 0.12 | 0.13 / 0.16 / 0.19 | 10 × 455 / 3 × 455 | overlay on both |

- Bound after aiming: half the class spacing, ±1.04 ms at 96 Hz and ±0.69 ms at 72 Hz, against ±2.1 ms for nearest-refresh alone. Each display-mode change re-rolls the clocks' phase, so the best class differs run to run.
- The clocks drift about 0.5 µs/s, so the aim is re-checked at cuts, where moving the switch by one refresh can't be seen.
- At 72 Hz camera slides get 72 positions a second on the external instead of 96; whether that is visible is a by-eye question for later.

## Seam tuner, first session (by eye, 2026-10-11 14:25)

After this spike the user felt a delay when the white bars crossed the bezel ([ADR 0020](../../docs/adr/0020-motion-crosses-a-bezel-with-no-delay.md)). The seam tuner (`spikes/per-monitor-presenter/seam-tuner.cpp`) then showed the same kind of crossing in smooth motion: one physical pattern across both screens, each screen drawing every refresh for the moment its own light appears, row by row (laptop scan 88.9% of a 4.17 ms refresh, external 96.9% of 10 ms, read from Windows).

- Brightness matched first (`match-brightness.py`): equal at the laptop's 44% backlight, no change needed.
- **Moving, at the starting values (gap 20 mm, height 0, no lag correction): "no delay now".** The felt delay in the spike most likely came from its film-like steps (20 fps, 7.5 mm per step): a stepped bar can only reappear behind the bezel on its next step, up to 50 ms late, rather than from panel lag.
- Still pattern (gap and height): **"unclear what to judge".** Asking the viewer to make lines "straight" gave no anchor. Next version: a forced choice between two settings shown in turn, the software halving the step after each answer.
- Not yet judged: film-step crossings (things moving on their own at 24 fps), against the same steps across a fake bezel on one screen as the reference.

## Reproduce

- `spikes/per-monitor-presenter/compile.bat` (MSVC, VS 2022), then `python spikes/per-monitor-presenter/run-presenter-trials.py OUT_DIR --modes own,nearest --fps 20 --seconds 20`. Takes over every screen about 25 s per mode; Esc stops.
- `python spikes/per-monitor-presenter/analyze-per-monitor-pacing.py OUT_DIR` re-scores existing logs.

## Unresolved questions

1. Is G-SYNC/VRR enabled for windowed and borderless apps on this laptop (NVIDIA Control Panel, "Set up G-SYNC")? Turning it on lets the follow mode be tested.
2. Does the external accept a custom 96 or 72 Hz mode (needs approval)? Then rerun nearest at 24 fps.
3. Is a 0.3–2.1 ms seam offset invisible? The anchored threshold test (`spikes/seam-threshold/`) answers whether 4.2 ms is.
4. Does independent flip survive the real renderer (video decode, libplacebo, subtitles, an on-screen control)?
