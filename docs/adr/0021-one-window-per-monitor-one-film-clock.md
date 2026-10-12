# 0021. One window per monitor, one film clock

- **Status:** Accepted — refined by [0025](0025-refresh-times-come-from-the-scan-line.md)
- **Date:** 2026-10-11
- **Refines:** [0014](0014-architecture-plan-ahead-into-a-cue-sheet-perform-live.md), [0016](0016-camera-slides-play-smooth.md)

## Context

[ADR 0020](0020-motion-crosses-a-bezel-with-no-delay.md) asks for zero delay across a bezel. A window spanning both screens is composed by Windows once per refresh of the main display; the other screen picks the picture up at its own refresh, so the two sides switch frames −1.7 to +6.7 ms apart and nothing in the window can change that ([mixed-rate spike](../../plans/reports/spike-261011-1034-one-window-across-mixed-refresh-rates.md)).

With one borderless window and swapchain per monitor in one process, both screens ran in hardware overlay at once, each paced by its own refresh, with an even pace on both and the frame changes 0.28 ms apart; the scheme bounds it at half the faster monitor's refresh, ±2.1 ms on Setup A ([per-monitor spike](../../plans/reports/spike-261011-1247-one-window-per-monitor.md)). The GPU's refresh clocks for the two screens are locked to each other (12:5 to 0.5 ppm), so that offset holds for hours; rows scan at different speeds (3.70 ms vs 9.69 ms per screen), which a schedule can aim at the row where things cross ([presentation research](../../plans/reports/researcher-261011-1247-per-monitor-presentation-and-sync.md)).

## Decision

- The performer opens one borderless window exactly covering each monitor and presents to each on that monitor's own refresh.
- One film clock, derived from the refresh count of a master monitor: the slowest monitor carrying the picture, whose rate is a whole multiple of the playback rate. Never compare the film clock against refresh times; count refreshes.
- Every other monitor switches to the next frame on its refresh nearest the master's switch, corrected by the difference in panel lag and aimed at the row where things cross the seam.
- Each refresh is drawn for the moment its own light appears. In a camera slide (ADR 0016) that gives every refresh its true position, on every monitor.
- Where a follower runs with variable refresh, it presents at the exact moment instead of the nearest refresh. Target-time presents (composition swapchain `SetTargetTime`, Vulkan `VK_EXT_present_timing`, both available on Setup A) still land on a refresh of a fixed-rate monitor; they help queue frames ahead, not land between refreshes. (Corrected the same day after the [red-team review](../../plans/reports/red-team-261011-1247-m1-architecture.md).)
- Every break (seek, pause, resume, a change of master, a display-mode change) restarts the clock on one master refresh chosen far enough ahead for every monitor's render lead; all monitors switch on it.
- A follower keeps its chosen refresh until the nearest one is clearly better (a small hysteresis), and moves only at a cut.
- Without variable refresh, the film starts on the master refresh whose alignment with the follower's grid is closest: one GPU drives the screens from one clock, so their refreshes keep a fixed ratio and fall into a few alignment classes. Measured on Setup A at 24 fps: 0.39 ms at 240 + 96 Hz, 0.16 ms at 240 + 72 Hz ([per-monitor spike](../../plans/reports/spike-261011-1247-one-window-per-monitor.md)). The aim is re-checked at cuts as the clocks drift.
- Film time is a time, not a frame count: frames are picked by their own timestamps, so files with a variable frame rate work.
- One rendering device per graphics adapter that drives a monitor.

## Consequences

- The performer has to own its windows and present calls: stock mpv can't ([0022](0022-own-performer-on-libplacebo-and-ffmpeg.md)).
- Monitors carrying the picture need a rate that is a whole multiple of the playback rate: built-in, a custom mode, or variable refresh. Setup A's external needs 96 or 72 Hz for 24 fps films.
- What remains on fixed-rate monitors: up to half the faster monitor's refresh at the aimed row, plus the scan-speed difference above and below it. Setup reports it.
- The room model records per monitor: exact rate, refresh grid, scan time, panel lag, variable-refresh range.
- Phase slips (one 9- or 11-refresh frame every couple of hours) go at cuts.
- When no picture monitor's rate is a whole multiple of the playback rate (25 fps on Setup A, a 60 Hz screen), a ladder applies: custom mode, variable refresh, a speed nudge of at most 1%, then blending frames on that monitor or keeping the picture off it (ADR 0015). Which rung comes first is a what-you-see choice for the user.
- With three screens, followers chain: each switches on its refresh nearest its neighbour's chosen refresh, aimed at the row where things cross that seam.
- Custom modes keep the monitor's native pixel clock and lengthen the vertical blank, so the scan stays as fast as at the native rate.
- A simulation with the measured jitter and drift confirms these rules ([clock simulation](../../plans/reports/sim-261011-1912-film-clock-rules.md), 2026-10-11). It sets the values: a hysteresis of 0.05 ms per alignment class; a re-aim at cuts that never shortens the frame before a cut below one refresh; frames picked as the last one due, with half a master refresh of tolerance. With a variable frame rate, frame changes land on every alignment class, so the aimed start can't help there.
