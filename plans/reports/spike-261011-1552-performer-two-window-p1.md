---
title: "Spike P1: two-window presenter on libplacebo (D3D11), windowed smoke test"
date: 2026-10-11
type: spike
harness: spikes/performer-p1/
results: spikes/performer-p1/results-2026-10-11/
---

# Spike P1: two-window presenter on libplacebo (D3D11)

Scope: build `perf1`, windowed smoke test only. No full-screen run, no `--refresh` run was done (the code path exists, untested). Controller runs the full-screen trials.

## What was built

`spikes/performer-p1/` (C11, 4 files, about 1.9k lines; derived from perf0):
- `src/perf1-main.c` windows, swapchains, scheduling, render thread, logs
- `src/perf1-decode.c` D3D11VA import from perf0; one decoder, every frame pushed to one `pl_queue` per monitor as its own AVFrame reference
- `src/perf1-clock.c` C port of VblankClock; `src/perf1-stats.c`
- `compile.ps1` -> `compile-perf1.sh` (MSYS2 UCRT64, libplacebo master 7.375.0, self-built LGPL FFmpeg 9.0.2; clean, no warnings); `bin/run.cmd` (DLL PATH for that process only)
- `analyze-perf1.py` (runs the original `analyze-per-monitor-pacing.py` unchanged, adds render time, lateness, stalls)

Design choices:
- **One D3D11 device + one render thread** for both swapchains (waits on both waitable objects, rotating order). Chosen over a device per monitor: D3D11VA surfaces live on one device (no cross-device copies), `pl_gpu` is not thread-safe, and render cost is 0.3 ms GPU per new frame, so serialising is cheap. Risk: render on one monitor delays the other by up to ~0.5 ms; lead before target stayed 3.8-4.2 ms on the 240 Hz screen.
- Own flip-model swapchain per monitor (rgb10a2, 3 buffers, latency 1) via `CreateSwapChainForHwnd`, wrapped with `pl_d3d11_create_swapchain(.swapchain)`, own `Present1(1,0)`. `--format bgra8` available.
- Scheduling as the raw presenter: grids from `WaitForVBlank`, slowest monitor = master, film time = master refresh count, nearest-refresh follower (largest master refresh whose nearest follower refresh is not later), aimed start over 24 candidates, `--mode own` too. Speed lock to the master refresh count only when within 1 % (else plays at 1.0, `--no-lock`).
- Picking by PTS: `pl_queue` asked for the film time of the refresh being prepared; mixing off (`interpolation_threshold` huge), drift compensation off, non-blocking updates (no waits inside the refresh loop). pl_queue is nearest-neighbour, so the position is shifted by half a frame and half a master refresh to give "last frame due". Limit: half-frame shift assumes constant frame duration (VFR approximate).
- Picture: 16:9 (from source), height = shorter screen (215 mm), centred on the gap, tops level, gap 20 mm; laptop at 0..344 mm, external at 364..840 mm; picture x 162.9..545.1 mm. Each monitor: whole-pixel `target.crop`, matching float `image.crop` (one resample), black elsewhere. Screenshot of the windowed run showed the picture split across both windows, external part smaller in pixels (4.0 vs 7.4 px/mm) as intended; I did not verify continuity at the seam (windows are half-size).
- Flags: `--windowed [--window-scale 0.5]` (borderless, not topmost, no-activate), `--seconds N` (after film start), `--refresh DISPLAYn=HZ` (`CDS_TEST` then `CDS_FULLSCREEN`, restore on exit; refused together with `--windowed`), `--probe`, `--master`, `--preset`, `--decode sw|d3d11va`, `--sync-render`. Esc: window message and global key state, so it works without focus.
- Logs: `monitor-N-<mode>.json` same fields as per-monitor-presenter (vblanks, presents, stats, composition_modes) plus `render_cpu_ms`, `render_gpu_ms`, `lead_ms`, `render_sync_ms`, `perf1{}` block; parse with the original analyzer.

## Windowed results (composed mode: overlay NOT expected, pacing of the 100 Hz window NOT representative)

Both screens, half-size windows, D3D11VA, 15 s film each, preset default, 2 s skipped by analyzer (1 s). Files `results-2026-10-11/windowed-{hotel,judas,esc}/`.

| Run | Screen | Refreshes per frame | skipped frames | queue stalls | render CPU p50/p99 (new frame p50) | GPU pass p50/p99 (new frame p50) |
|---|---|---|---|---|---|---|
| Hotel 25 fps, speed locked (hold 4 at 100 Hz) | DISPLAY1 240 Hz | 9: 142, 10: 208 | 0 | 0 | 0.027 / 0.28 ms (0.24) | 0.076 / 0.34 ms (0.33) |
| | DISPLAY2 100 Hz | 4: 345, 3: 3, 5: 2 | 0 | 0 | 0.024 / 0.13 ms (0.09) | 0.003 / 0.09 ms (0.09) |
| Judas 23.976 fps, not locked (100 Hz needs +4.3 %) | DISPLAY1 | 9: 111, 10: 167, 12: 57 | 0 | 0 | 0.027 / 0.31 ms (0.26) | 0.007 / 0.33 ms (0.10) |
| | DISPLAY2 | 4: 277, 5: 57 | 0 | 0 | 0.024 / 0.12 ms (0.09) | 0.003 / 0.14 ms (0.09) |

- Frame-change offset DISPLAY2 minus DISPLAY1: Hotel median -3.88 ms (p95 -2.35, range -13.2..+1.8); Judas median -3.89 ms (range -5.7..+12.4). Predicted aimed-start offset 0.07-0.13 ms. The measured value is dominated by an artefact (below), so it is not a result for the scheme.
- `--sync-render` (GPU waited after each render, Hotel): wall render p50 0.26 / 0.18 ms (DISPLAY1/2), p99 5.2 / 0.5 ms. The 5 ms p99 on DISPLAY1 is waiting behind the present queue, same artefact as P0's 4.1 ms timer readings, not render cost.
- Composition: DISPLAY1 overlay in about 90 % of polls even in a half-size window (rest composed); DISPLAY2 composed (6 polls "none"). Overlay with the libplacebo-wrapped rgb10a2 swapchain is possible on DISPLAY1; full-screen overlay on both remains to be verified.
- Exits: `--seconds` ended the run itself (exit 0, logs written); Esc sent as a real key press at 16:06:47.206 ended the process by 16:06:47.246 (30 s run) with logs written. `tasklist` shows no perf1 process after any run.

### Windowed artefacts (why the offsets above mean little)

1. **DISPLAY2's window is presented at 240 Hz, not 100 Hz.** 2.4 `Present1` calls per DISPLAY2 refresh (log: `present_calls_per_refresh`), as the earlier mixed-rate spike found for composed windows: DWM drives composition from the main display. So DXGI statistics for DISPLAY2 are sparse (7..34 matched presents) and its screen times come from the analyzer's fallback (next vblank after the call), so per-frame refresh counts are indicative only.
2. DISPLAY1 statistics report screen time one refresh (4.04 ms) after my target in every run (`screen_minus_target_ms` p50 4.04); presents are made 4.1 ms before the target. In a composed window this is the extra DWM latency; whether it is real or a convention of `SyncQPCTime` is unknown until the full-screen run shows the same field.

## Commands for the controller (full screen; warn the user first, Esc stops every window)

Run from `C:\Users\tamla\Downloads\Telegram Desktop\panorama\spikes\performer-p1` in PowerShell 7. 24 fps content: Judas (23.976 fps, 1080p HEVC). At 96 Hz master: hold 4, speed lock +0.1 %; at 72 Hz: hold 3, +0.1 %. Master = slowest monitor = DISPLAY2.

```powershell
$f = 'C:/Users/tamla/Downloads/[Judas] Nama-Anaru - S01E02.mkv'     # forward slashes, brackets are fine as a plain argument
.\bin\run.cmd perf1-lgpl.exe --file $f --mode nearest --refresh DISPLAY2=96 --seconds 20 --out .\results-2026-10-11\fs-96hz-aimed
python -I .\analyze-perf1.py .\results-2026-10-11\fs-96hz-aimed
.\bin\run.cmd perf1-lgpl.exe --file $f --mode nearest --refresh DISPLAY2=72 --seconds 20 --out .\results-2026-10-11\fs-72hz-aimed
python -I .\analyze-perf1.py .\results-2026-10-11\fs-72hz-aimed
```

Aimed start is automatic in `--mode nearest` (it prints "predicted follower offset"; the raw spike predicted 0.40 ms at 96 Hz and 0.12 ms at 72 Hz). Optional: `--mode own` as comparison, `--preset hq`, `--format bgra8`. Check in the output: `composition` should read overlay on both monitors, refreshes per frame 10/4 (96 Hz) and 10/3 (72 Hz), offset small, `landed_off_target_refresh` and `missed_refreshes` near 0, `screen_minus_target_ms` a constant, `queue_more` 0. `python -I analyze-per-monitor-pacing.py <dir>` in `spikes\per-monitor-presenter\` also works on the same folder. If perf1 is killed the display mode is restored by Windows (CDS_FULLSCREEN, ADR 0023).

## Unresolved questions

1. Does the libplacebo-wrapped swapchain reach hardware overlay (independent flip) full-screen on both monitors? Only windowed DISPLAY1 showed overlay.
2. Is the +1 refresh between my target and DXGI's screen time real latency or a statistics convention? Full-screen will show (compare with the raw presenter's value).
3. Does single-thread serialisation of the two monitors hold at full-screen resolution (laptop 2560x1600 target, `hq` preset)? Window render was 0.3 ms GPU per new frame at half size.
4. Not covered: Vulkan (D3D11 only), second graphics adapter, software decode path (compiled, untested), VFR files, audio, seeks, EOF handling (stops every window together, untested), 5-10 min soak, `--refresh` code path.
5. Picture size/position (height = shorter screen, centred on the gap) is my choice; confirm or change `--picture-height-mm` / `--picture-center-mm`.
