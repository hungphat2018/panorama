---
phase: 1
title: "Pace and motion"
status: in-progress
priority: P1
effort: "3d"
dependencies: []
---

# Phase 1: Pace and motion

## Overview

Decide how frames reach each screen: what keeps every screen that carries the picture on an even pace, both sides of every bezel switching to the next frame together, and camera slides moving on every refresh ([ADR 0016](../../docs/adr/0016-camera-slides-play-smooth.md)). The answer decides whether the performer needs one window per monitor (phase 02).

## Context

- Roadmap M1 "Pace" and "Motion"; ADRs [0005](../../docs/adr/0005-app-may-change-display-settings-during-playback.md), [0014](../../docs/adr/0014-architecture-plan-ahead-into-a-cue-sheet-perform-live.md), [0016](../../docs/adr/0016-camera-slides-play-smooth.md).
- Measured: [mixed-rate spike](../reports/spike-261011-1034-one-window-across-mixed-refresh-rates.md). One spanning window is composed once per refresh of the main display; each screen shows the newest image at its own refresh; the external got the same frame −1.7 to +6.7 ms after the laptop; 240 + 100 Hz stumbles on the 100 Hz screen; pan mode removed bezel flicker.
- Levers so far: [pacing research](../reports/research-261011-1000-frame-pacing-judder-and-seek-swap-concepts.md) (Paths A–E, VRR follower, takeover latch).
- New: [per-monitor presentation and sync](../reports/researcher-261011-1247-per-monitor-presentation-and-sync.md).

Terms: a *refresh* is one chance for a screen to show a picture (240 Hz = 240 a second). *Pace* = refreshes per film frame. The *temporal seam* = the two sides of a bezel switching frames at different moments, so a moving object shows two film moments for a few ms. *VRR* = the monitor waits for the next picture instead of ticking on its own clock.

## Invariant

On every monitor carrying the picture:

1. **Even pace:** each film frame stays up the same number of refreshes.
2. **In step, zero delay as seen** ([ADR 0020](../../docs/adr/0020-motion-crosses-a-bezel-with-no-delay.md)): something crossing a bezel arrives on the other screen exactly when its motion says, counting each panel's lag, the real gap and the scan speed of each screen. The user felt a delay with white bars crossing in spike A (signal offset 0.3–2.2 ms), so the target is zero, not a threshold; the threshold test (step 2) only says how close is close enough.
3. **Slides:** during a camera slide, every refresh shows the picture where the slide path puts it at that refresh's own time.

Monitors showing only the surround are exempt from 1–2; they carry slowly smoothed light.

## Lever map (which clock bends)

| Lever | Bends | Setup A today |
|---|---|---|
| Each monitor at a multiple of the playback rate (built-in or custom 96/72/48 Hz) | display rate | laptop 240 ✓; external has no 24p multiple built in; custom modes untested |
| VRR follower: a VRR monitor presents at the fixed monitor's refresh | display phase | laptop EDID says VRR 60–240 Hz; whether the driver allows it here is unknown |
| Nearest-refresh scheduling: each monitor switches on its refresh nearest a common beat | which refresh carries the change | bounds the offset by half the faster monitor's period, only with per-monitor control |
| Aim the present time at a chosen row (panels scan top to bottom at different speeds: ≈4 ms at 240 Hz, ≈10 ms at 96 Hz) | row-dependent offset | derivation only |
| Playback speed lock (23.976 → display, `display-resample`) | content clock | in use, follows one display |
| Pan mode: per-refresh slide synthesis | motion sampling | proven by eye on pre-rendered clips; needs each refresh's time in the renderer |
| Planner: picture on one monitor (ADR 0015), or a cost for spanning layouts in shots with motion at the seam | layout | design |
| Same-GPU head sync (Surround/Eyefinity-style) | display phase | unknown for mixed sizes and rates |

Hard to change: one spanning window vs one swapchain per monitor; what the master clock is; whether the renderer knows each refresh's time. Cheap: the chosen rates, the aim row, which monitor leads.

## Requirements

- Functional: the three invariants on Setup A; a path to Setup B (60 Hz LG UltraFine 4K among three screens).
- Non-functional: 0 dropped frames from the presentation itself; no display change without the restore guarantee of phase 07.

## Implementation Steps

1. Read the presentation research; update the lever map with verified facts and dates.
2. **Anchored threshold test (user, ~10 min).** On the 240 Hz laptop, a fake 20 mm bezel; content A: an object crossing at 24 fps over a still background (the case pan mode does not cover); content B: a 24 fps pan for reference. Each trial plays the same 3 s twice, once with 0 ms offset and once with X ms (X = 4.2, 8.3, 12.5: the 240 Hz grid), in random order. The user says which one flickers at the bezel. 10 trials per X. Pass test for "invisible": ≤ 6 of 10 right; "visible": ≥ 9 of 10; in between: 10 more trials. Gives ε (or "below 4.2 ms").
3. **Spike A, per-monitor presenter (screen takeover, ~30 s per trial).** One process, two borderless windows each exactly covering one monitor, flip-model swapchains, a frame-numbered barcode and a moving bar. Measure: present mode per window (PresentMon), each monitor's flip times, refreshes per frame on each, offset at the seam with (a) each monitor on its own refresh, (b) nearest-refresh scheduling.
4. **Spike B, VRR follower: not possible on Setup A** (no G-SYNC page in NVIDIA Control Panel, user 2026-10-11; driver reports variable refresh not possible). Replaced by the aimed start: choosing the master refresh whose alignment class is closest gave 0.39 ms at 240 + 96 Hz and 0.16 ms at 240 + 72 Hz ([spike](../reports/spike-261011-1247-one-window-per-monitor.md)). Retry VRR on Setup B or with a DisplayPort monitor.
5. **Spike C, custom modes (approved 2026-10-11).** External at 96 and 72 Hz with `spikes/custom-refresh/` (native pixel clock, longer vertical blank, so the scan stays 9.7 ms; same-totals recipe as plan B). Rerun the pending 240 + 96 and 240 + 72 trials, in the spanning window and in the per-monitor presenter at 24 fps.
6. **Per-refresh time for pan mode:** take it from the chosen presentation (vblank timestamps per monitor); record the method.
6a. **Brightness first** (`spikes/mixed-rate-pacing/match-brightness.py`): perceived timing shifts with brightness, so a mismatch would hide inside the tuned lag. Prefer brightening the dimmer screen over dimming the laptop (its backlight may flicker when dimmed). Store the backlight levels with the lag.
6b. **Seam tuner (user judges, software adjusts).** On the per-monitor presenter: a diagonal-stripe pattern continuous behind the bezel, moving smoothly (each screen draws the position for the moment its own light appears, row by row). Still: tune the gap and height until the stripes run straight across the bezel. Moving left and right: tune each monitor's lag until they stay straight in both directions (a lag error flips side with direction, a gap error doesn't). Then a bar crossing at film steps, to feel what remains. Values go to the room model.
7. Camera check (phone slow motion ≥ 240 fps) of the tuner at the tuned values, plus a plain grey field: separates panel lag from perception, shows each screen's scan direction and any backlight flicker.
7b. Offline clock simulation before P1: refresh jitter and drift, hysteresis on and off, 25 fps content, a real variable-frame-rate timestamp trace; checks the clock rules in ADR 0021.
8. Decide; write the ADR(s): presentation model and clock model.

## Success Criteria

- [ ] Threshold test records whether 4.2 ms is visible (how close is close enough; the target stays zero).
- [x] Chosen presentation measured on Setup A ([spike](../reports/spike-261011-1247-one-window-per-monitor.md)): even pace on both monitors at 20 fps, seam offset 0.28 ms (bound ±2.1 ms), both in hardware overlay. Still to do at 24 fps on 96 Hz, over 5 min, with the tuned lag applied.
- [ ] By eye: a slow pan across the bezel shows no flicker and no stumble; an object crossing the bezel at 24 fps shows no split and no delay.
- [ ] Seam tuner: gap, height offset and per-monitor lag set by the user's judgment with the moving-stripe pass test; residual per row reported.
- [ ] ADR for the presentation model, naming what the performer engine must provide.

## Risk Assessment

- VRR may be off for windowed/borderless apps on this driver. Signal: the laptop's flip times stay on its 240 Hz grid. Response: nearest-refresh scheduling (≤ ±2.1 ms if the external runs a 24p multiple), then ask the user to enable windowed G-SYNC.
- Custom 96 Hz may fail over HDMI on this monitor. Signal: no picture or "out of range". Response: Windows reverts the test mode; try 72 Hz, then 48 Hz; else keep 100 Hz with pan mode and planner rules.
- Both windows may not reach independent flip together. Signal: PresentMon shows "Composed". Response: test exclusive full screen per monitor and the composition swapchain before concluding.
- Logs measure the signal, not the panel. Signal: the user sees a split the logs don't. Response: camera check (step 7).
- Setup B's 60 Hz screen has no even 24p pace without 48/72 Hz or VRR, and scans 4× slower than a 240 Hz panel. Response: carry into phase 06 facts; planner may keep the picture off mismatched pairs.
