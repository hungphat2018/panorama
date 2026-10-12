---
title: "Spike X1: row timing in the per-monitor presenter and in perf1"
date: 2026-10-11
type: spike
answers: "M1 phases 01-02, experiment X1 of the zero-delay research: can both presenters time the seam by when each screen lights a row, not by when its blank starts?"
harness: spikes/per-monitor-presenter/ and spikes/performer-p1/
results: "spikes/per-monitor-presenter/results-2026-10-11/windowed-rowtiming-* and spikes/performer-p1/results-2026-10-11/windowed-rowtiming-*"
---

# Spike X1: row timing in the presenters

Run 2026-10-11, 18:20 to 19:20, Asia/Bangkok. Only windowed runs, at the native 240 + 100 Hz. No full-screen run, no display change.

Evidence tags: **[L]** measured here, **[V]** source read, **[D]** derived, **[K]** prior knowledge.

Terms:

- **Blank:** the pause between a refresh's bottom row and the next top row. **Top row:** the first row of a refresh. The `WaitForVBlank` wake-up and DXGI's `SyncQPCTime` mark the start of the blank, so the picture comes one blank later.
- **Light of a row:** when a screen lights row y of the picture it is showing. Example: the laptop lights its middle row 1.85 ms after its top row; the external lights its middle row 3.89 ms after its top row.
- **Seam offset:** light of a row on one side of the bezel minus light of the same physical height on the other side. Here always **second monitor minus first by output index = DISPLAY2 minus DISPLAY1 = external minus laptop**, the orientation of the research table (the brief's "follower minus master" is the negative of that on Setup A, because the external is the master).
- **Aim row:** the physical height where "nearest" and the aimed start make the two screens agree. Default: the middle of the height both share, 107.5 mm.

## 1. Answer

1. **Both programs now schedule by row light.** The follower's refresh is the one whose aim row lights nearest to when the master lights it. The aimed start picks the start where that is closest. They print the predicted seam offset at the top, aim and bottom rows, and log it. [L] plumbing, windowed.
2. **Windowed runs prove the plumbing, not the pacing.** The laptop window ran mostly in hardware overlay; its DXGI times sit 0.33 to 0.45 ms before the next top row (median 0.37 to 0.41), inside its 0.463 ms blank, as the model needs. The external window was composed on the 240 Hz clock, so its DXGI times are not on its own grid and the offsets of those runs mean nothing; the analyzer now says so (WARNING line). [L]
3. **Predictions held up where they can be checked.** The predicted aim-row offset always fell inside the bound the theory gives (half the alignment-class spacing, ±0.42 ms at 240 + 100 Hz), and top and bottom sat 2.03 ms below and above it, the research number. [L, D]
4. **Two things differ from the brief, both for a reason found by measuring** (section 4): the scan grid is refitted once a second during the film instead of only at start and end, and a row is snapped to the grid line nearest the middle of the blank instead of `top_after`.
5. **Not reached:** the sampler delivers 120 to 290 valid samples a second per monitor, not 1,000 (Windows `Sleep(1)` returns after 2 ms at best). It is enough for the fit (section 6).

Status of the brief's acceptance: compiles clean (MSVC /W3 and gcc -Wall -Wextra, no warnings); windowed smoke done on both programs; logs carry the new fields; analyzers print row offsets; old logs still parse. `wake_to_top_ms` agrees with the probe only to about ±0.05 ms (laptop) and ±0.03 ms (external), not to the 0.39 / 0.22 of the earlier probe (section 6).

## 2. What changed

| File | Change |
|---|---|
| `spikes/per-monitor-presenter/row-timing.h` (new) | One header, valid as C11 and C++17, used by both programs: exact timing, sampler thread, fits, light time, geometry, nearest-light refresh, aimed start, JSON fields. Its first comment is the model's spec. |
| `per-monitor-presenter.cpp` | Uses it. New options `--lag-ms DISPLAYn=X` (repeatable), `--height-offset-mm`, `--aim-mm`, `--windowed [--window-scale 0.5]`. Heights added to the size table (215 mm, 268 mm). Window set-up, geometry and film-start moved into functions. Unknown options now fail instead of being ignored; exit code 1 on errors; logs are written only for a film that started. |
| `per-monitor-common.h` | Additive: `VblankClock::line(&base, &period)` (the fitted line under one lock). Checked: the seam tuner files still pass `cl /Zs`. |
| `analyze-per-monitor-pacing.py` | Today's metric labelled "blank-start" (key `offset_ms` unchanged). New row-light section, sanity checks, composed-window WARNING. Old logs parse (output identical to the stored summary). |
| `run-presenter-trials.py` | Passes options it does not know (e.g. `--windowed`, `--lag-ms`) to the presenter. |
| `spikes/performer-p1/src/perf1-main.c`, `perf1.h`, `perf1-clock.c`, `compile-perf1.sh` | Same model and options and log fields. Render path, decoder and `pl_queue` use untouched. The picture layout now takes each screen's top from the model (identical result with level tops; `--height-offset-mm` moves the picture too). Master is picked from the exact signal rates before the layout. `-I ../per-monitor-presenter` added. |
| `analyze-perf1.py` | Row offsets printed and saved to `perf1-row-offsets.json` (`perf1-summary.json` keeps its shape). |
| `row-timing-selftest.c`, `compile-row-timing-selftest.bat`, `test-analyze-row-offsets.py` (new) | Tests (section 7). |

Not touched: `scanline-clock.h`, `scanline-probe.cpp`, `compile.bat`, the seam files, other spikes. No change to `scanline-clock.h` is needed.

## 3. The light-time formula

```
light(v, y) = top_row(v) + (y - top_mm) / height_mm * scan_s + lag_s
```

- `v`: the `WaitForVBlank` time of the refresh the present lands on (from the existing `VblankClock` fit). It says **which** refresh.
- `top_row(v)`: the line of the monitor's scan grid nearest `v + blank_s / 2`. It says **when**. Equal to `top_after(v)` whenever the grid is right (the unit tests check that), but it cannot slip a whole refresh if `v` and the grid disagree by more than the 0.2 ms between a wake-up and its top row.
- `y`, `top_mm`: millimetres on one vertical axis whose zero is the master's top. The follower's `top_mm` is `--height-offset-mm`.
- `scan_s`, `blank_s`, `line_s`: from `QueryDisplayConfig` (exact, per mode). `height_mm`: 215 (laptop) and 268 (external). `lag_s`: `--lag-ms`.
- Nearest follower and aimed start: for master refresh j, the follower refresh k minimising `|light_F(k, aim) - light_M(j, aim)|`. The follower shows film frame n on the refresh chosen for master refresh `first + hold * n`.
- Prediction: the same expressions at three heights, for the chosen start.

Numbers the brief asked me to state (the program prints `r` = the aim-row value; top = r - 2.04, bottom = r + 2.04):

| Pair | Blank starts aligned, ext - lap at top / mid / bottom | Aimed at the middle | Bound on r |
|---|---|---|---|
| 240 + 96 | +0.26 / +2.29 / +4.33 ms | r - 2.04 / r / r + 2.04 | ±1.04 ms (classes 2.083 ms apart) |
| 240 + 72 | +3.72 / +5.76 / +7.80 | r - 2.04 / r / r + 2.04 | ±0.69 ms (classes 1.389 ms apart) |
| 240 + 100 (smoke) | -0.15 / +1.88 / +3.92 | r - 2.03 / r / r + 2.03 | ±0.42 ms (classes 0.833 ms apart) |

The first two rows reproduce the research table in the unit test (within 0.01 ms). [D, L]

## 4. Where I departed from the brief, and why

1. **The grid is refitted while the film runs.** The brief fits at film start and at the end. I kept those two (logged as `scan_grid_at_start` and `scan_grid`), but a fit over the 1.5 s settle has a period good only to a few ppm: against the whole-run fit the two disagreed by 1 to 28 µs after 8 to 27 s of film, in 13 runs on both monitors, which is 0.1 to 3 ppm [L, D]. Extrapolated over the 300 s soak that is up to 1 ms. The laptop has at most 0.4 ms (at times 0.03 ms) between a wake-up and its top row, so a plain "first top row after the wake-up" would have jumped a whole refresh (4.17 ms) and the follower would have switched refresh by itself, looking like a hardware phase slip; and even with a robust snap it leaves a model error of up to 1 ms against the 0.05 ms goal. The sampler therefore refits the last 10 s of samples once a second and publishes the result under a small lock (`live_refits` in the log counts them). This is what ADR 0025's "each performer loop keeps a fitted top-row grid" means in code.
2. **The wake-up times could not carry the schedule instead.** The gap between a `WaitForVBlank` wake-up and the top row moved by up to 0.37 ms inside one run (perf1 on the laptop: 0.40 ms at film start, 0.03 ms at the end), and between probe runs 40 minutes apart (laptop 0.39, then 0.24 to 0.31) [L]. Anchoring on wake-ups plus a constant would have put that into the light times. The scan grid is hardware; the wake-ups only pick the refresh.
3. **`top_row` instead of `top_after`** in the running programs (section 3). The analyzer keeps `top_after` on the fresh end-of-run fit, as briefed, and prints how far after each DXGI time the next top row comes, so a mis-snap would show.
4. **The analyzer drops the earliest film frame of each monitor** from the row offsets: the first seconds are skipped, so that frame's first retained present is not its first appearance (that frame was the old `max` of 2.5 to 5.5 ms).
5. **Offsets are second minus first by output index**, not follower minus master, so the sign matches the research table and the predictions above.
6. A few safeguards: `--aim-mm` outside the shared height, no shared height, an unknown display in `--lag-ms` and `--windowed` with `--refresh` are refused with a message.

## 5. New log fields (per monitor, JSON, all existing fields kept)

`windowed` (presenter only; perf1 had it in `perf1{}`), `timing` {refresh_hz, line_s, scan_s, blank_s, pixel_rate_hz, active, total, interlaced}, `scan_grid` (end-of-run fit) and `scan_grid_at_start` {ok, base, period, resid_p50_us, resid_p95_us, samples, taken}, `live_refits`, `wake_to_top_ms` (at film start) and `wake_to_top_end_ms`, `geometry` {height_mm, top_mm, lag_ms}, `aim_mm`, `shared_mm` [top, bottom], `predicted_ms` {top, aim, bottom} (nearest mode with two monitors).

`wake_to_top_ms` is the top row minus the wake-up between the two fitted lines. The analyzer also prints the median over the run's wake-ups.

## 6. Windowed smoke results [L]

Both screens at native modes, half-size windows. Every run exited normally and left no process (`tasklist`).

| Run | Result |
|---|---|
| Presenter, nearest, 12 s (`windowed-rowtiming-nearest`) | Sampler: 118 to 294 valid samples/s per monitor across the six runs (118 / 141 in this one). Start fit 236 / 212 samples (laptop / external), residual p50 3.3 µs. End fit 1,652 / 1,977 samples, residual p50 1.9 / 3.0 µs, p95 8.1 / 9.1 µs. Grid refitted 16 times. Predicted DISPLAY2 - DISPLAY1: top -2.43, aim -0.40, bottom +1.64 ms. |
| Presenter, options (`-options`): `--lag-ms DISPLAY2=2.5 --lag-ms \\.\DISPLAY1=0.5 --height-offset-mm 10 --aim-mm 100` | Shared height 10.0..225.0 mm, aim 100.0 printed and logged; geometry in both logs (laptop top 10 mm, lags 0.5 and 2.5 ms); predicted -1.70 / -0.00 / +2.36. |
| Presenter, Esc (`-esc`) | Run set to 60 s, Esc message posted after 7 s: stopped 65 ms later, logs written. |
| perf1 on Judas (`windowed-rowtiming-judas`), Hotel (`-hotel`), Esc (`-esc`) | Same fields and prints; predicted -2.38 / -0.34 / +1.69 (Judas), -2.37 / -0.33 / +1.70 (Hotel, 25 fps locked, hold 4); Esc stopped it in 92 ms with logs. |
| `own` and `follow` modes (presenter, scratch folder) | Run and analyze (windowed artefacts as expected). |

Checks against the brief:

- **Logs carry the new fields; the analyzers print row offsets;** old logs parse. [L]
- **`wake_to_top_ms` vs the probe.** Laptop at film start 0.23 to 0.42 ms, external 0.16 to 0.24 ms over all runs. Bracketing one run with the probe (19:08 to 19:09): probe 0.297 and 0.308 (laptop), 0.216 and 0.214 (external); the log 0.351 and 0.231 at film start, 0.204 and 0.183 at the end. So about ±0.05 ms on the laptop and ±0.03 ms on the external, not ±0.03 against 0.39 / 0.22: the probe itself read 0.394 / 0.200 at 18:28 and 0.237 / 0.210 at 19:07. The number follows the OS wake-up latency, not the model, and the light times do not use it.
- **Prediction vs measurement.** Not testable windowed: the laptop's DXGI times are real but its partner's are not. In the laptop's own data the DXGI time comes 0.33 to 0.45 ms before the top row (median 0.37 to 0.41 over the runs), always inside the 0.463 ms blank. The external's window: 0 to 8 % of DXGI times inside its blank, flagged.
- **Cost.** perf1 render CPU (p50 / p99) on the laptop 0.034 / 0.48 ms against 0.027 / 0.28 ms before, on the external 0.029 / 0.19 against 0.024 / 0.13. Small, and the PC was busy with other work; not attributed to the samplers.

## 7. Tests

- `compile-row-timing-selftest.bat` builds `row-timing-selftest.c` as C++17 with MSVC and runs it; the same file builds as C11 with gcc (`gcc -std=gnu11 -Wall -Wextra ... -luser32`). 1,643 checks, 0 failed on both, no warnings. It covers: the research table for 240 + 96 and 240 + 72; `top_row` against a grid that has drifted past the margin; wake-up-to-top-row between fitted lines; the live grid following a clock whose rate changes 3 ppm (a whole-run fit lags it); reads of the live grid against concurrent publishing (never half of two fits); nearest-light refresh against a brute-force search over 400 random cases with lags and a height offset; the aimed start on 12 phases (always within half a class); the shared height; the fit through the lock; the JSON fragment.
- Ten deliberately broken variants of the header (sign of the lag, missing top, worst candidate, unlocked reads, extrapolating the start fit, ...) were each caught by at least one check.
- `python -I test-analyze-row-offsets.py`: 17 tests on made-up logs with known answers (the table above to 0.01 ms; lag, height offset, missing frames, the composed-window WARNING, old logs, `main()` on a folder of old and of new logs).

## 8. Full-screen runs for the controller

Warn the user: both screens are taken over; Esc stops. PowerShell 7. Presenter, nearest at 24 fps, 20 s each (the runner sets and restores the external's rate; analysis runs at the end):

```powershell
Set-Location 'C:\Users\tamla\Downloads\Telegram Desktop\panorama\spikes\per-monitor-presenter'
python .\run-presenter-trials.py .\results-2026-10-11-rowtiming-24fps-96hz --modes nearest --fps 24 --seconds 20 --refresh DISPLAY2=96
python .\run-presenter-trials.py .\results-2026-10-11-rowtiming-24fps-72hz --modes nearest --fps 24 --seconds 20 --refresh DISPLAY2=72
```

perf1 (Judas, 23.976 fps; hold 4 at 96 Hz, 3 at 72 Hz, speed lock +0.1 %), 20 s each, then the 300 s soak at 96 Hz:

```powershell
Set-Location 'C:\Users\tamla\Downloads\Telegram Desktop\panorama\spikes\performer-p1'
$f = 'C:/Users/tamla/Downloads/[Judas] Nama-Anaru - S01E02.mkv'
.\bin\run.cmd perf1-lgpl.exe --file $f --mode nearest --refresh DISPLAY2=96 --seconds 20 --out .\results-2026-10-11\fs-rowtiming-96hz
python -I .\analyze-perf1.py .\results-2026-10-11\fs-rowtiming-96hz
.\bin\run.cmd perf1-lgpl.exe --file $f --mode nearest --refresh DISPLAY2=72 --seconds 20 --out .\results-2026-10-11\fs-rowtiming-72hz
python -I .\analyze-perf1.py .\results-2026-10-11\fs-rowtiming-72hz
.\bin\run.cmd perf1-lgpl.exe --file $f --mode nearest --refresh DISPLAY2=96 --seconds 300 --out .\results-2026-10-11\fs-rowtiming-96hz-soak300
python -I .\analyze-perf1.py .\results-2026-10-11\fs-rowtiming-96hz-soak300
```

If a run is killed, Windows restores the external's rate (ADR 0023). To re-score a folder: `python -I .\analyze-per-monitor-pacing.py <folder>` (presenter) or `analyze-perf1.py` (perf1).

What each line should read if the model is right (`r` = the printed aim-row value):

| Where | Line | 96 Hz | 72 Hz |
|---|---|---|---|
| console | `monitor 1 ... refresh` | 96.00 Hz (master) | 72.00 Hz (master) |
| console | `signal ... total, line, scan, blank` (external) | 2000x1160, 8.980 µs, 9.698 ms, 0.718 ms | 2000x1545, 8.990 µs, 9.709 ms, 4.180 ms |
| console | same, laptop | 2720x1800, 2.315 µs, 3.704 ms, 0.463 ms | same |
| console | `top row X ms after the vblank wake-up` | laptop 0.1 to 0.4; external 0.5 to 0.7 | laptop 0.1 to 0.4; external 4.0 to 4.1 (blank minus wake latency) |
| console | `scan grid N of M samples, residual p50` | N above 200, p50 under 6 µs | same |
| console | `aim row ...; shared height` | 107.5 mm; 0.0..215.0 mm | same |
| console | `predicted seam offset DISPLAY2 - DISPLAY1` | top r - 2.04, aim r, bottom r + 2.04; r within ±1.04 | same; r within ±0.69 |
| console | `film clock locked ... refreshes per frame` | 4, 24.00 fps | 3, 24.00 fps |
| console, end | `grid at film start vs end ... us`, `end fit ... p50, p95` | a few to 40 µs (the soak: up to 1,000 µs, see below); p50 under 4, p95 under 12 µs | same |
| analyzer | `refreshes per frame` | laptop {10: all}, external {4: all}, overlay on both | {10}, {3} |
| analyzer | `blank-start frame-change offset` (the old metric) | about r - 2.29 ms | about r - 5.76 ms |
| analyzer | `row-light seam offset` top / aim / bottom | median = the `predicted` printed beside it, within 0.05 ms (the X1 pass test); p5 to p95 within about 0.1 ms | same |
| analyzer | `DXGI SyncQPCTime to next top row` per monitor | laptop 0.33 to 0.45; external 0.58 to 0.70; all inside the blank, no WARNING | laptop same; external 4.04 to 4.16 |
| analyzer | `vblank wake-up to top row` start, end | within 0.3 ms of each other (they follow the OS) | same |
| analyzer (perf1) | `landed_off_target_refresh`, `missed_refreshes` | about 0 | about 0 |

The old blank-start line changes meaning on purpose: aiming moved from the blank to mid-height, so it now reads about r - 2.29 ms where the earlier aimed run read +0.39 ms (that run was +0.65 ms at the top row and +2.7 ms at mid-height: research section 1.5, ADR 0025).

For the soak: the aim-row median should drift by at most about 0.15 ms over 300 s (the heads' 0.5 µs/s [K]) and the pace stay {10} and {4}; a 9- or 11-refresh frame would mean the follower crossed its flip edge, half a laptop refresh (2.08 ms) from the aimed remainder; with r within ±1.04 ms that takes a change above 1 ms, far beyond the 0.15 ms expected, so a 9 or 11 in the soak is a finding, not a model artefact. "Start-fit grid vs end-fit grid" may read up to a millisecond there: that is the start fit's extrapolation error, which the film did not follow (it followed the refits, `live_refits` about 299). If the end fit's residual p95 grows well above 12 µs, the display clock wandered and one line over the whole run is too coarse for the analysis; logging the refit history would fix that (not done).

How a wrong model would show: an aim-row median off by one follower refresh (4.17 ms) means the follower landed on the wrong refresh; a constant shift of the same sign on all three rows is panel lag or a DXGI offset; a top-to-bottom spread other than 4.08 ms (2 x 2.04) means the heights or the scan speed are wrong (for example a scaler that buffers a frame).

## 9. Found along the way

- **The start of a short fit is not the truth for long runs** (section 4.1): 0.1 to 3 ppm.
- **`WaitForVBlank` wake-up latency is not a constant** (4.2). Light times that sit on the scan grid are immune; anything computed as "wake-up plus a constant" is not.
- **The sampler cannot reach 1 kHz here.** A standalone test: `Sleep(1)` takes 2.00 ms (median) after `timeBeginPeriod(1)` and 16 ms without it. Inside the programs 118 to 294 valid samples/s per monitor (the scanline probe's "sparse" mode was 437/s). The fit does not need more: residual p50 1 to 5 µs.
- **DXGI statistics of a composed window are not on its monitor's grid** (windowed DISPLAY2: 0 to 8 % inside the blank). The analyzer now checks this for every monitor, so a full-screen run that is secretly composed shows a WARNING.
- The seam tuner agent's `seam-row-clock.h` also refits the grid over the last 10 s (every 200 ms) and numbers refreshes by the scan grid itself; `row-timing.h` could replace it, or the other way round. Names do not clash (`row_*`, `rowmon_*`, `ROWMON_*` against `seam::`).

## 10. Unresolved questions

1. Does the external's DXGI time sit inside its own blank in full screen at 96 and 72 Hz (the check line says so; windowed cannot)?
2. Does the aim-row median match the printed prediction within 0.05 ms, the X1 pass test?
3. Why did the laptop's wake-up latency change between 18:28 and 19:07 with the machine otherwise idle (power state? another tool)? It does not affect the model, but it matters to anything built on `WaitForVBlank`.
4. Is one line over a 300 s run accurate enough to score the soak (end-fit residual p95), or should the refit history be logged?
5. Should the follower keep its refresh with a small hysteresis (ADR 0021) instead of re-deciding every frame? Not added: after an aimed start the flip edge is at least 1.04 ms away, about seven times the expected drift in 300 s. Revisit if a soak shows a 9 or 11.
6. A ~2 ms sampler (120 to 290 samples/s) is what Windows gives with `Sleep(1)`; a high-resolution waitable timer would give more if a later fit ever needs it.
