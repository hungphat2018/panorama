---
phase: 1
title: "Pace and motion"
status: completed
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

Verified on Setup A, 2026-10-11, unless marked derived.

| Lever | Bends | Setup A |
|---|---|---|
| Each monitor at a multiple of the playback rate (built-in or custom 96/72/48 Hz) | display rate | laptop 240 ✓; external custom 96 and 72 Hz saved and working (native pixel clock, longer blank, scan stays 9.7 ms) |
| VRR follower: a VRR monitor presents at the fixed monitor's refresh | display phase | not possible: no G-SYNC page, driver reports it not possible on both screens; retry on Setup B or a DisplayPort monitor |
| Nearest-refresh scheduling + aimed start (film starts on the best-aligned master refresh) | which refresh carries the change | measured ±2.1 ms bound; aimed start 0.39 ms (96 Hz) and 0.16 ms (72 Hz), but between blank starts: at the top rows about 0.65 and 3.9 ms ([ADR 0025](../../docs/adr/0025-refresh-times-come-from-the-scan-line.md)); re-measure with row times (X1) |
| Row times from the scan line ([ADR 0025](../../docs/adr/0025-refresh-times-come-from-the-scan-line.md)) | how each refresh's time is measured | top row 0.393 / 0.221 ms after the vblank wake-up; fitted to 0.6 / 2.3 µs |
| Aim the change at a chosen row (laptop scans in 3.70 ms, external in 9.69 ms) | row-dependent offset | ±2.04 ms at the top and bottom of the shared 215 mm when aimed at the middle (derived) |
| Rolling blend: the faster screen copies the slower screen's wave row by row, mixing the one refresh that straddles each row's moment by time share | the moment each row changes, in light | exact by derivation (0 µs over 400 rows); costs one mixed laptop refresh per film frame change; whether that shows is X2 (what you see: the user's call) |
| Scan-matched laptop mode (120 Hz, 2720 × 1725 total at 563.04 MHz, scan 7.73 ms) | scan speed, in hardware | rows line up to ±0.02 ms (derived); whether the panel accepts it is X3 (laptop may go dark up to 15 s: needs approval) |
| Draw each row for the middle of the time its light stays up (smooth motion) | hold centre: 3.1 ms between a 240 Hz and a 96 Hz screen | derived; built into seam tuner v2 |
| Playback speed lock (23.976 → display, `display-resample`) | content clock | +0.1 % at 96 or 72 Hz |
| Pan mode: per-refresh slide synthesis | motion sampling | proven by eye on pre-rendered clips; needs each row's time in the renderer (design §5) |
| Planner: picture on one monitor (ADR 0015), or a cost for spanning layouts in shots with motion at the seam | layout | design |
| Head sync / genlock (Surround, Quadro Sync) | display phase | needs Quadro Sync boards or identical timings: out for Setup A |

Hard to change: one spanning window vs one swapchain per monitor; what the master clock is; whether the renderer knows each row's time and can mix per row (the rolling blend needs a per-row weight in libplacebo's frame mixer, a contained patch). Cheap: the chosen rates, the aim row, which monitor leads.

## Requirements

- Functional: the three invariants on Setup A; a path to Setup B (60 Hz LG UltraFine 4K among three screens).
- Non-functional: 0 dropped frames from the presentation itself; no display change without the restore guarantee of phase 07.

## Implementation Steps

1. Done 2026-10-11: lever map above, with the [zero-delay research](../reports/research-261011-1523-zero-delay-across-scan-speeds.md) folded in.
2. **Anchored threshold test (user, ~10 min; optional now: the target is zero, and the rolling blend would make the signal timing exact).** On the 240 Hz laptop, a fake 20 mm bezel; content A: an object crossing at 24 fps over a still background (the case pan mode does not cover); content B: a 24 fps pan for reference. Each trial plays the same 3 s twice, once with 0 ms offset and once with X ms (X = 4.2, 8.3, 12.5: the 240 Hz grid), in random order. The user says which one flickers at the bezel. 10 trials per X. Pass test for "invisible": ≤ 6 of 10 right; "visible": ≥ 9 of 10; in between: 10 more trials. Gives ε (or "below 4.2 ms").
3. **Spike A, per-monitor presenter (screen takeover, ~30 s per trial).** One process, two borderless windows each exactly covering one monitor, flip-model swapchains, a frame-numbered barcode and a moving bar. Measure: present mode per window (PresentMon), each monitor's flip times, refreshes per frame on each, offset at the seam with (a) each monitor on its own refresh, (b) nearest-refresh scheduling.
4. **Spike B, VRR follower: not possible on Setup A** (no G-SYNC page in NVIDIA Control Panel, user 2026-10-11; driver reports variable refresh not possible). Replaced by the aimed start: choosing the master refresh whose alignment class is closest gave 0.39 ms at 240 + 96 Hz and 0.16 ms at 240 + 72 Hz ([spike](../reports/spike-261011-1247-one-window-per-monitor.md)). Retry VRR on Setup B or with a DisplayPort monitor.
5. **Spike C, custom modes (approved 2026-10-11).** Done: external at 96 and 72 Hz with `spikes/custom-refresh/` (native pixel clock, longer vertical blank, so the scan stays 9.7 ms; same-totals recipe as plan B); per-monitor presenter trials at 24 fps done. Spanning-window trials at 96/72 Hz dropped: ADR 0021 already rejects the spanning window, and they would not change that.
6. **Per-refresh time for pan mode:** done, [ADR 0025](../../docs/adr/0025-refresh-times-come-from-the-scan-line.md): each refresh's top-row time from a scan-line fit, row y at top + y line times. Smooth motion draws each row for the middle of the time its light stays up (derived; confirmed by eye in seam tuner v2).
6x. **X1, row timing in the harnesses** (2026-10-11, agent): `spikes/per-monitor-presenter/scanline-clock.h` (verified by `scanline-probe.cpp`) wired into the presenter, perf1 and the analyzer ([report](../reports/spike-261011-1915-row-timing-in-presenters.md)). Done full screen, 24 fps, 20 s (`spikes/per-monitor-presenter/results-2026-10-11-rowtiming-*`, `results-2026-10-11-rowtiming-console.txt`): at 96 Hz the aimed row measured +0.41 ms against +0.40 predicted (p5 to p95 +0.40 to +0.42; top −1.63, bottom +2.45), both screens even (4 and 10 refreshes per frame, one short frame at the start), both in hardware overlay. At 72 Hz the aimed row's median was +0.37 against +0.34 predicted, but the laptop alternated 9 and 11 refreshes on 57 of 455 frames (60 in a quiet rerun): the presenter spike re-decides the follower's refresh every frame, with no hysteresis. perf1 carries the 0.05 ms hysteresis from the clock simulation (step 7b), so the 72 Hz pace is checked again on perf1 in [phase 8](./phase-08-thin-prototype-and-exit.md).
6y. **X2, film-step test** (agent building, then the user, ~10 min): a tall bar crossing the bezel in 24 fps steps; the aimed row against the rolling blend ("in which do the bar's top and bottom come out at different moments?"), and the blend's visibility on the laptop alone ("which shows a faint double edge?"). Anchor: the same bar crossing a fake bezel inside the laptop screen.
6z. **X3, scan-matched laptop mode** (needs the user's approval: the laptop may go dark up to 15 s). **X4, backlight flicker:** phone photo of a white screen at 1/2000 s or faster at the matched brightness (user, 2 min).
6a. **Brightness first** (`spikes/mixed-rate-pacing/match-brightness.py`): perceived timing shifts with brightness, so a mismatch would hide inside the tuned lag. Prefer brightening the dimmer screen over dimming the laptop (its backlight may flicker when dimmed). Store the backlight levels with the lag.
6b. **Seam tuner (user judges, software adjusts).** v1 done 2026-10-11: moving "no delay now"; still "unclear what to judge". v2 (agent building): a forced choice between two settings shown in turn, the software halving the step, with an anchor first. On the per-monitor presenter: a diagonal-stripe pattern continuous behind the bezel, moving smoothly (each screen draws the position for the moment its own light appears, row by row). Still: tune the gap and height until the stripes run straight across the bezel. Moving left and right: tune each monitor's lag until they stay straight in both directions (a lag error flips side with direction, a gap error doesn't). Then a bar crossing at film steps, to feel what remains. Values go to the room model.
7. Camera check (phone slow motion ≥ 240 fps) of the tuner at the tuned values, plus a plain grey field: separates panel lag from perception, shows each screen's scan direction and any backlight flicker.
7b. Done 2026-10-11, [clock simulation](../reports/sim-261011-1912-film-clock-rules.md) (`spikes/clock-sim/`, reruns in about 2 min). The ADR 0021 rules hold. Numbers for what is missing in perf1:
   - hysteresis: 0.05 ms per alignment class, moving only at cuts (0 mid-shot slips; without it 22–25 an hour);
   - re-aim at cuts: aimed row within ±1.07 ms (96 Hz) / ±0.72 ms (72 Hz) over 2 h; it must never shorten the frame before a cut below one refresh;
   - frames by timestamp: "last frame due" with half a master refresh of tolerance (perf1's shortcut is wrong on 12 % of refreshes for variable-frame-rate files).
   Variable frame rates defeat aiming (±1.9–2.2 ms whatever the start), and on Setup B two 48 Hz screens sit at a random phase up to ±10.4 ms apart; only the rolling blend (or VRR) makes those seams exact. The blend is exact to 0.76 µs on the scan-line grid, and keeps a non-multiple follower (240 Hz at 25 fps) exact in light-weighted time. 25 fps on Setup A: no speed nudge within 1 % exists; the blend and picture-off rungs work today.
8. Done: presentation model and clock model ([0021](../../docs/adr/0021-one-window-per-monitor-one-film-clock.md), [0025](../../docs/adr/0025-refresh-times-come-from-the-scan-line.md)); what the performer must provide is phase 02's R1 to R10. The rolling blend changes what you see (ADR 0017): it is built into the performer's picture path as a switch, off by default, and its ADR waits for the user's verdict on X2 in phase 8.

**Closed 2026-10-12.** The user ended the spikes and moved verification to the end of the build. What is left here needs the user's eyes or approval, so it moves to [phase 8](./phase-08-thin-prototype-and-exit.md): the threshold test (step 2, optional), X2 (step 6y, decides the rolling blend), X3 and X4 (step 6z), brightness matching (6a), seam-tuner calibration of gap, height and lag (6b), the camera check (7), and the 72 Hz pace on perf1 (6x). The harnesses for all of them are built and smoke-tested ([seam tuner v2 report](../reports/spike-261011-1922-seam-tuner-v2-and-film-step-test.md)).

## Success Criteria

- [ ] Threshold test records whether 4.2 ms is visible (how close is close enough; the target stays zero). Moved to phase 8, optional.
- [x] Chosen presentation measured on Setup A ([spike](../reports/spike-261011-1247-one-window-per-monitor.md)): even pace on both monitors at 20 fps, seam offset 0.28 ms (bound ±2.1 ms), both in hardware overlay. At 24 fps with row times (step 6x): 96 Hz even on both screens, aimed row within 0.01 ms of the prediction; 72 Hz needs perf1's hysteresis. The 5-minute run with the tuned lag applied is phase 8's pace measurement.
- [ ] By eye: a slow pan across the bezel shows no flicker and no stumble; an object crossing the bezel at 24 fps shows no split and no delay. Moved to phase 8 (step 4).
- [ ] Seam tuner: gap, height offset and per-monitor lag set by the user's judgment with the moving-stripe pass test; residual per row reported. Tuner built (forced choice, v2); the session moves to phase 8.
- [x] ADR for the presentation model, naming what the performer engine must provide: [0021](../../docs/adr/0021-one-window-per-monitor-one-film-clock.md), [0025](../../docs/adr/0025-refresh-times-come-from-the-scan-line.md), with phase 02's R1 to R10.

## Risk Assessment

- VRR may be off for windowed/borderless apps on this driver. Signal: the laptop's flip times stay on its 240 Hz grid. Response: nearest-refresh scheduling (≤ ±2.1 ms if the external runs a 24p multiple), then ask the user to enable windowed G-SYNC.
- Custom 96 Hz may fail over HDMI on this monitor. Signal: no picture or "out of range". Response: Windows reverts the test mode; try 72 Hz, then 48 Hz; else keep 100 Hz with pan mode and planner rules.
- Both windows may not reach independent flip together. Signal: PresentMon shows "Composed". Response: test exclusive full screen per monitor and the composition swapchain before concluding.
- Logs measure the signal, not the panel. Signal: the user sees a split the logs don't. Response: camera check (step 7).
- Setup B's 60 Hz screen has no even 24p pace without 48/72 Hz or VRR, and scans 4× slower than a 240 Hz panel. Response: carry into phase 06 facts; planner may keep the picture off mismatched pairs.
