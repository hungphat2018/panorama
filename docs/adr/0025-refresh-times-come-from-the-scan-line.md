# 0025. Refresh times come from the scan line

- **Status:** Accepted
- **Date:** 2026-10-11
- **Refines:** [0021](0021-one-window-per-monitor-one-film-clock.md)

## Context

[ADR 0021](0021-one-window-per-monitor-one-film-clock.md) schedules each monitor's frame changes on a fitted grid of its refresh times. The spikes fitted that grid to `IDXGIOutput::WaitForVBlank` wake-ups and read when a present reached the screen from `GetFrameStatistics`. Both mark the start of the vertical blank, just after a refresh's bottom row. The next picture's top row comes one blank later ([zero-delay research](../../plans/reports/research-261011-1523-zero-delay-across-scan-speeds.md), section 3.1).

The blank depends on the mode. On Setup A it is 0.46 ms on the laptop, and on the external 0.31 ms at 100 Hz, 0.72 ms at the 96 Hz custom mode and 4.18 ms at 72 Hz (custom modes keep the scan fast by lengthening the blank). So the [per-monitor spike](../../plans/reports/spike-261011-1247-one-window-per-monitor.md)'s seam offsets of 0.39 ms (96 Hz) and 0.16 ms (72 Hz) were measured between blank starts. Between top rows they are about 0.65 ms and 3.9 ms, and they grow further down the screen, because the two screens scan at different speeds (3.70 ms and 9.69 ms from top to bottom).

Measured on Setup A on 2026-10-11 with `spikes/per-monitor-presenter/scanline-probe.cpp`, at native rates:

- The top row comes 0.393 ms (laptop) and 0.221 ms (external) after the `WaitForVBlank` wake-up. The wake-ups themselves jitter by 21 and 17 µs (standard deviation), with outliers up to 130 µs.
- A line fitted to `D3DKMTGetScanLine` samples over picture rows gives each top row to within 0.6 and 2.3 µs (median residual). One sample per millisecond is enough: within 2 µs of continuous polling.

## Decision

- A refresh's time is when its top row is drawn. It comes from a line fitted to scan-line samples over picture rows; samples inside the blank are dropped. It never comes from the vblank signal or a present's statistics alone.
- Row y of a refresh is drawn at the top-row time plus y line times. Line time, scan time and blank come from the mode's exact timing (`QueryDisplayConfig`).
- Seam offsets, the follower's nearest refresh and the aimed start compare the same physical row on both sides of the seam, each at its row time plus its panel lag.
- Vblank wake-ups and present statistics still say which refresh a present landed on; the scan-line grid says when.

## Consequences

- Each monitor's performer loop keeps a fitted top-row grid, fed by a light background sampler (about 1,000 kernel calls a second per monitor). `spikes/per-monitor-presenter/scanline-clock.h` is the reference code.
- The 96 Hz and 72 Hz seam results are measured again with row times.
- The room model takes each monitor's scan time and blank from its exact timing. Panel lag stays a calibrated value ([ADR 0020](0020-motion-crosses-a-bezel-with-no-delay.md)).
