---
title: "AI surround live path, phase 2: the live skeleton (worker, side store, player fill, tiers T0-T2)"
date: 2026-10-13
type: implementation report
plan: plans/261013-0718-ai-surround-live-path/phase-02-live-skeleton.md
---

## Outcome

The live skeleton runs end to end on the real player.
- A worker process reads JJK ahead of the playhead and plans the sides. It makes T1 mosaics (the shot's own real pixels) and T2 plates (X2's painter recipe), and serves them through a memory-mapped side store.
- The player (`perf1 --ai-sides`) copies the canvases to the GPU a few rows per refresh. It shows each one only from a cut, under the picture, with the glow where nothing is ready, and it never waits.
- Real side pixels continue the picture's lines across both edges with no step.
- The screen runs, E1's painter re-test and the smoke, are in "Screen runs" below.

## What was built

| Part | Files | What it does |
|---|---|---|
| Contract | `spikes/ai-surround/live/side-store.layout.json`, `live/__init__.py` | The binary layout (schema `panorama.side-store/1`) and the three contracts: side plan, canvas, playhead record |
| Store, worker end | `live/side_store.py` | Create, attach or empty in place with a new id; counter-protected writes |
| Store, player end | `spikes/performer-p1/src/perf1-ai-sides-store.{h,c}` | Mapping; counter-protected reads (static asserts pin every offset) |
| Analysis | `live/{film_timeline,frame_reader,analyse_v0}.py` | Frame table from the content model or the packet list; CPU decode at 960 px wide; camera path by chained DIS flow at 1/8 source size; per-frame gain; kept frames; splits at cuts the content model missed |
| T1 | `live/mosaic_t1.py` | The shot's real pixels at their world positions, whole pixels from the median-bright frame, feathered into the glow |
| T2 | `live/paint_plate.py`, `live/painter_jobs.py` | X2's recipe on its own ComfyUI (port 8191, `--reserve-vram 4`); earliest deadline first among shots it can still finish; throttled under player pressure |
| Cache | `live/side_cache.py` | Analyses, mosaics and canvases per film key (ADR 0027); re-watch and later runs reuse them |
| Scheduling | `live/worker_state.py`, `live-worker.py` | Shots, canvas versions, publishing, heap residency near the playhead, seeks |
| Player | `src/perf1-ai-sides{,-record,-upload}.c`, `src/perf1-finish-surround-ai.c` | Store polling, the player record, the room's side widths, GPU copies, showings (cut-only), the fill and seam passes, the log |
| Hooks in shared player files | `perf1-picture-render.c` (3 calls), `perf1-finish-surround.c` + `-passes.h` (fill hook), `perf1-picture-shader.{c,h}` + `perf1-picture-pass1.c` (geometry GLSL split out, same text), `perf1-args.c`, `compile-perf1.sh`, `perf1-gpu-share.{c,h}` (`--vram-reserve-mb`) | Without `--ai-sides` the player does what it did before |
| Tools | `live-cues.py`, `live-smoke.py`, `live-store-selftest.py`, `live-analysis-selftest.py`, `live-cache-selftest.py`, `ai-sides-selftest.c` | Cue sheet stand-in, screen smoke harness, self-tests |

## Decisions taken here

- **The player pulls, the worker keeps the heap.** Canvases live in fixed-size heap slots (24 x 16 MiB by default). The worker keeps the best version for each shot within 60 s of the playhead in the heap; the player copies what the next 8 s need.
  - If the worker crashes, the heap stays valid: sides hold for what is already there, then fall back to glow.
  - If the worker restarts, it empties the store in place with a new id. The player drops what it holds and shows glow until the next cut.
- **World canvas per shot, transform per frame.** Each frame maps its source px to its shot's world (v1: a translation, `w = p - offset`). A canvas is RGBA in world px at a per-canvas scale; v1 uses half the film's resolution.
- **Cut-only tiers.** A showing (a shot from its first drawn frame) picks the best canvas already on the GPU at a cut, a seek or the film start, and keeps it.
- **The room decides the side area.** The player maps its monitors' borders through pass 1's own map and writes the result back for the worker: on `setup-a-default`, 818 / 0 / 1500 / 275 source px (left / top / right / bottom). This is not the even 32:9 the demos assumed.
- **GPU sharing (E1's rules).**
  - Applied here: the painter keeps 4 GB free (`--reserve-vram 4`, near the plan's ~8 GB); the player reserves 1 GB (`--vram-reserve-mb`).
  - The throttle stops new plates on 10 or more missed refreshes in a second, or on a late present call. E1's no-load data shows the player alone misses refreshes in bursts at clock changes.
  - Not applied: locking the clocks (M1 track) and batching the encoder (phase 3).

## Checks without the screens

- Store self-test:
  - a round trip over every field (231/231);
  - a stress run: 91 torn canvas copies caught, 0 corrupt kept, 0 wrong frame plans.
- Analysis self-test on a synthetic 675 px pan: 10/10.
  - The camera path stays within 7.9 px of the truth (chained flow drifts about 1.2-1.5 % of the travel).
  - The mosaic places the world's pixels with a median error of 4 codes.
- Picture self-test 92/0; the player compiles with 0 warnings.
- Worker speed on JJK: about 85 fps of analysis (3.5x film speed). Mosaics take 0.2-0.7 s per shot. The cache is about 0.7 MB per shot.
- Painter in the live path: 15-16 s per plate with the room's side area (first plate 22 s with model loading). The plates continue the scene in style (checked by agent on the tunnel shot).
- Windowed player runs:
  - the shaders compile at the warm-up frame;
  - real T1 pixels line up with the picture on the seats pan, with the seam pull off;
  - copies cost 0.12 ms of CPU per chunk (p50, max 0.5 ms), the seam profile 0.005 ms of GPU;
  - killing and restarting the worker behaves as designed, with 0 torn copies.

## Review (code-reviewer, [report](code-reviewer-261013-2012-live-skeleton-phase2.md))

Fixed:
- C1: the worker could not re-create a store file a player had mapped. It now empties an existing store in place, and the smoke waits for the new id.
- H1: the analyser busy-looped at its lookahead, and without a content model lost the shot at a decode start. It now waits at the edge, and a start right after an analysed shot counts as a cut.
- H2: the AI shader variants compiled mid-film. They now compile at the warm-up frame.
- H3: a read during a write flashed the sides to glow. Each monitor now reuses its last whole record, and the worker no longer rewrites shot rows for a diagnostic field.
- H4: GPU canvases thrashed. Now:
  - only showings drawn in the last second pin a canvas;
  - a canvas is never given up for a farther shot;
  - the walk starts at the next shot;
  - textures come in 256-texel buckets;
  - no copy happens when the GPU already has an equal or better tier.
- M1: decoding now keeps the film's aspect.
- M2: the 60 s splits are gone.
- M3: a write now sets the counter's low bit, so a crashed writer cannot leave a record dead.
- M4: the player checks the film again after a store reset.
- M5: the side-width search has no layout cap and runs at warm-up; heap pages are prefetched before a copy.
- M6: the warm start reads only JSON and runs off the main loop.
- M8: canvases too large for the slots are skipped.
- M9: a plate times out after 300 s.
- M10: a crop mismatch between worker and player shows glow, with one warning.
- L2: slot sizes are checked.
- L3: a comment named the wrong test file.
- L4: the VRAM cap is now 4 GB.
- L8: the player record was split into its own file.

Left, with reasons:
- M7 (the rolling blend with AI sides): untested, noted in the code; the blend is off by default.
- L1: the gpu-share log is E1's existing behaviour.
- L5: no cache size cap yet (about 0.5 GB per episode).
- L6: frame matching needs ffmpeg 7 or newer; 8.1 is installed.

## Screen runs

Both screens, unattended (user OK 2026-10-13), with a rest before every run. Log: `spikes/ai-surround/out/live/checks/phase2-screens.log`.

### E1's painter cell again, with the GPU rules

`out/e1/20261013-221533`. The player reserves 1 GB (`--vram-reserve-mb 1024`); the painter's ComfyUI keeps 4 GB free (`--reserve-vram 4`). 120 s on the pans clip per cell.

| Cell | 240 Hz wrong/min | 240 Hz miss/min | 96 Hz wrong/min | 96 Hz miss/min | Misses near a clock event, 240 / 96 Hz |
|---|---|---|---|---|---|
| none, today | 3.1 | 6.6 | 0.5 | 0 | 0 / 0 |
| **painter with the rules, today** | **39.8** | **294.7** | **40.9** | **105.2** | 132 / 44 |
| E1: none | 23.5 | 85.8 | 11.2 | 19.9 | 49 / 16 |
| E1: painter, no rules | 23.0 | 153.2 | 18.4 | 52.6 | 0 / 0 |

- The rules made the painter worse: about twice E1's wrong frames and misses at both rates.
- The cap leaves the painter about 7 GB, less than the INT8 DiT's 7.26 GB, so it likely streams weights at every step instead of only between the encoder and the DiT. That points to weight streaming, not VRAM pressure, as what hurts the player (E1's open question). PCIe traffic was not measured, and the player's own reservation changed too.
- So ComfyUI's painter can't share the GPU with the player, whatever its cap. The painter has to fit in VRAM with fixed allocations, or be smaller: phase 3, measured against this cell.
- The smoke below runs full screen without the painter: it measures what this phase built.

### Smoke 1: windowed, 120 s, with the painter

`out/live/smoke/20261013-222650-windowed`.
- Player and worker exit 0. No worker losses, record reuses or crop mismatches.
- Film time on screen: glow 17.1 s, T1 29.8 s, T2 73.2 s (plates partly from earlier runs' cache). Showings by tier: 10 glow, 11 T1, 18 T2.
- Copies: 37 canvases (214 MB), 0 torn, 0 aborted.
  - CPU per chunk p50 0.10 ms, p99 0.38 ms.
  - One chunk of 891 took 12.6 ms, enough to miss a 240 Hz refresh. It fell on the system's 2-second stall (smoke 2): the player's render took 12.8 ms at that moment, on the same phase of the clock.
- Seam profile: 0.005 ms of GPU (p50).
- Worker: 229 shots analysed, 82 T1 canvases, 5 plates.
  - Plates took 22.2 s (median) against 15-16 s alone: the 4 GB cap again.
  - 15 plates skipped: their deadline could not be met, or the player reported pressure. Windowed composition misses refreshes, so the throttle held plates often.
- Timing is not comparable windowed: the 96 Hz screen is composed.

### Smoke 2: full screen, 10 min, without the painter

`out/live/smoke/20261013-223232-fullscreen-nopaint`.
- Seeks at film time 150 → 400, 460 → 1200 and 1260 → 400 (the minute 400-460 s is watched twice).
- The worker is killed at 330 s and started again at 420 s (wall time from launch).
- Player and worker exit 0. Film start 5.6 s after launch.

Timing (E1's scoring; attribution in `timing-attribution.json` beside the summary):

| | 240 Hz | 96 Hz |
|---|---|---|
| Wrong frames | 15 (1.5/min) | 9 (0.9/min) |
| … at the 3 seeks | 3 | 3 |
| Missed refreshes | 129 | 23 |
| … at the 3 seeks | 60 | 18 |
| Outside seeks, per min | wrong 1.2, missed 6.9 | wrong 0.6, missed 0.5 |
| No-load cell today, per min (2 min, no AI) | wrong 3.1, missed 6.6 | wrong 0.5, missed 0 |

- **No fault comes from the worker or the AI sides.**
  - Nothing happened at the kill or the restart. The one missed refresh within a second of them came 0.27 s before the kill, on the 2-second grid below.
  - Copies: 104 canvases (705 MB), 0 torn. CPU per chunk p50 0.03 ms, p99 0.21 ms, max 1.3 ms (a 240 Hz refresh is 4.2 ms). Seam profile 0.005 ms of GPU.
- **Outside seeks, every fault falls on one 2-second grid of the system clock.** 40 of the 43 miss events at 240 Hz land at the same phase, as do the player's few long renders (11-15 ms).
  - The same grid holds the misses of the no-load cells, which run without AI and without a worker: today 6 of 7, and E1's first no-load cell 31 of 35, with render stalls of 12-18 ms.
  - In such a stall the player's own driver calls block for 11-19 ms (for example Present1 on both monitors at once, at 425.9 s).
  - So E1's "misses at clock changes" are mostly this periodic stall. Its source is outside the player: a driver or service timer, possibly our own nvidia-smi logging. Finding it is M1's (LatencyMon or a WPR trace; one no-load run without nvidia-smi).
- The seeks' misses come from the player's seek path (20 refreshes each at 240 Hz), the same with or without AI.

Sides:
- Film time: glow 450 s, T1 150 s, plates 0. There was no painter, and no cached plate matched this room's margins (fixed below).
- Why each glow shot had no canvas, in film time:

| Reason | Film time |
|---|---|
| Analysis from an earlier run's cache, but no mosaic for this room's margins: a bug, fixed below | 287 s |
| The worker was dead (the test) | 84 s |
| No real side pixels in the shot (T1 empty; a plate's job) | 59 s |
| First shot after the film start or a seek (cut-only rule) | 14 s |

- No shot showed glow because its T1 arrived late or was not on the GPU in time. Wherever the worker made a T1, it showed from the shot's start.
- Seeks: the worker re-planned from the new playhead each time. After a seek, only the first shot showed glow because of the cut-only rule.
- Kill: playback was undisturbed. Sides held where the heap had a canvas (a T1 shot 7.7 s after the kill), then glow until the restart.
- Restart: the new worker emptied the store in place. It found the first worker's 104 T1 canvases in the disk cache, and the player picked them up from the next cut.
- Re-watch (400-460 s twice): the same shots showed T1 on both visits.

### Fixed after the smoke (worker only)

1. **The warm start left shots without T1.** Analyses from earlier runs were published even when the cache had no mosaic for the room's current margins, and nothing re-made those mosaics. That is 287 s of glow in smoke 2.
   - Now the warm start waits for the room's margins.
   - It publishes only the shots whose mosaic for this room is cached, with their canvases, and the analyser analyses the rest again (a mosaic needs the decoded kept frames).
2. **The cache missed between windowed and full screen.** Cache keys held the room's side widths to 0.1 px, and the windowed and full-screen players measure them 0.3-0.7 px apart.
   - The widths are now rounded up to 32 source px, for both the canvas extent and the key: 832 / 0 / 1504 / 288 on `setup-a-default`.
   - Earlier cached canvases were keyed with the exact widths, so they are not reused.
3. **Split shots and the cache.** A span split at a hidden cut was looked up in the cache only as a whole. The analyser also counted it done once its first part was analysed.
   - The warm start and the done test now follow the parts that tile the span.

Checked without the screens: two worker runs, without the painter, against a stand-in that writes the player's record (playhead at film speed, the room's widths).
- Run 1, with the windowed player's widths: no shot came from the cache (the key changed), so the worker analysed 79 shots and made 31 T1 canvases in 40 s.
- Run 2, with the full-screen player's widths: all 79 shots, split parts included, and the 31 T1 canvases came from the cache within 0.6 s of the widths arriving. The worker analysed only new shots after them, with no overlap.
- Store self-test 231/231 and analysis self-test 10/10, as before.

Review of these fixes ([code-reviewer](code-reviewer-261013-2244-warm-start-cache-fixes.md)). Fixed:
- **H1:** an error during the warm start (a damaged cache file, say) left the run with no analyser and nothing in the log. Now:
  - the error is logged and that shot is analysed again;
  - the analyser always starts;
  - a damaged analysis file loads as a miss.
- **M1:** a non-empty mosaic counts as cached only together with its T1 canvas, in case a worker was stopped between the two writes (the smoke kills it on purpose).
- **M2:** the cache lists only the analyses this version and crop can load, and the tiling backs out of dead ends.
- **M3:** a width of 1 px or less, or one that is not a number, is no side, as in the player's fill.
  - Left: a width within a pixel of a 32 px step can still round either way.
- **M4, L2:**
  - a shot keeps its row by first frame (analysed again, or given up at a seek);
  - rows are budgeted at 4 per content-model shot plus 256;
  - a failed row write leaves the shot not done.
- **L1:** a cached shot and its canvases reach the painter in one step. A failed plate write to the cache no longer stops the painter.
- **L3, L4, L5:**
  - a zero-length part is ignored;
  - `--margins` is checked at start;
  - the stop events no longer shadow the thread's own.

Tests:
- New `live-cache-selftest.py`, 23/23: margins, tiling, cache listing, damaged files, and the done test and rows on split spans. The old code fails most of these checks.
- Store 231/231, analysis 10/10.
- A third worker pass loaded the 162 shots and 65 canvases of the first two passes, then analysed only new shots.

Not changed: the copy path. A copy that starts one refresh after its prefetch is not needed: without the painter the longest chunk took 1.3 ms, and the windowed run's 12.6 ms chunk was the system stall.

## Known limits

- T1 edges are stepped where the kept frames' views end. A cross-dissolve inside a shot mixes two places in its mosaic; that is phase 4's structure path.
- Flow at an eighth of the source size drifts about 1.5 % of a pan's travel. At the picture's edge that is a few px, because sides and picture share the path.
- A worker restart shows glow until the next cut, also on the shot on screen when it happens.
- The plate covers the room's whole side area at about 1 MP, which is coarse on the laptop's left side; phase 3 levers apply.
- The first shot after play or a seek shows the glow unless its canvas is already on the GPU, which is rare after a seek (cut-only).
- Without the painter, shots with no real side pixels stay glow: about 58 % of JJK's shots have an empty T1 (322 of 556 in smoke 2's first worker).
- Changing the room or the mosaic version re-analyses the film (about 3.5x film speed), since mosaics need the decoded frames.

## Unresolved questions

1. Phase 3's painter target now has to cover the room's real side area (about 1.7x the picture's area on Setup A, against X2's 1.0x). Is "sides-only targets" enough to recover that?
2. The player's own misses fall on a 2-second stall, not mainly on clock changes. Follow-up run without nvidia-smi (`out/e1/20261013-233940`):
   - the 2-second lock disappeared, so our own nvidia-smi logging likely makes those stalls;
   - but misses rose and scattered (23 a minute at 240 Hz), maybe from P-state changes once nothing keeps the GPU awake;
   - it was a single 2-min run. Handed to another agent: `plans/handoffs/gpu-sharing-and-2s-stall-findings-20261013-2345.md`.
3. The clock lock ("prefer maximum performance") stays M1's lever; the stall above is the bigger share.
4. ADR 0028 waits for the user's OK on what the viewer sees: glow until ready, upgrades only at cuts, sides stored at half the film's resolution (ADR 0017).
