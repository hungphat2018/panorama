---
phase: 2
title: "Live skeleton: worker, side store, player hook, tiers T0-T2"
status: completed
priority: P1
effort: "3-4 d"
dependencies: [1]
---

# Phase 2: Live skeleton

## Progress (2026-10-13, night): done

All six steps are built and checked, including the smoke on both screens. Details: [report](../reports/implementation-261013-2012-live-skeleton-phase2.md).
- **Screen runs** (user OK, unattended, with rests):
  - E1's painter cell with the rules got worse: 39.8 / 40.9 wrong frames a minute at 240 / 96 Hz, against 23.0 / 18.4 uncapped. ComfyUI's painter can't share the GPU with the player whatever its cap; phase 3's painter must fit its VRAM budget without streaming weights.
  - Windowed, 2 min, with the painter: T1 and plates on screen, 37 copies, 0 torn.
  - Full screen, 10 min, without the painter, with 3 seeks, a re-watched minute and the worker killed and restarted: no wrong frame or missed refresh comes from the worker or the AI sides.
    - Outside seeks, the player's faults fall on a 2-second system stall that also hits it with no AI and no worker. That is for M1 to find.
- **Fixed after the smoke** (worker): the warm start had left shots cached without this room's mosaic with no T1. The room's widths are now rounded up to 32 px, so windowed and full-screen players share the cache. Shots split at hidden cuts are found in the cache.
  - The review's findings were fixed too: an error in the warm start no longer leaves the run without its analyser, damaged or stale cache files count as misses, and shot rows are reused.
  - Checked with three worker passes against a fake player, and the new `live-cache-selftest.py` (23/23).

Before the screens (steps 1-5):

- **Contracts** (step 1): `spikes/ai-surround/live/side-store.layout.json` (schema `panorama.side-store/1`), described in `live/__init__.py`.
- **Side store** (step 2): `live/side_store.py` writes it; `src/perf1-ai-sides-store.c` reads it.
  - `live-store-selftest.py` checks it (231/231): a round trip over every field, and a stress run in which every torn copy is caught (156 in the last run) and no corrupt one is kept.
- **Worker** (step 3): `live-worker.py` and the `live/` package.
  - Analysis runs at about 85 fps (≈3.5x film speed) on the CPU. It splits shots at cuts the content model missed.
  - T1 mosaic, the T2 plate (X2's recipe: 15-16 s per plate with the room's real side area), the disk cache, the heap, and earliest-deadline-first plates.
  - `live-analysis-selftest.py` passes 10/10 on a synthetic pan.
- **Player** (step 4): `perf1 --ai-sides STORE` in `perf1-p3-ai.exe`.
  - The store reader, chunked copies to the GPU, showings fixed at cuts, and the fill under the picture with gain and the seam pull.
  - The room's side widths are written back for the worker: 818 / 0 / 1500 / 275 source px on `setup-a-default`.
  - Windowed checks:
    - Real side pixels continue the picture's lines across both edges with no step.
    - Copies take 0.12 ms of CPU per chunk (p50) and the seam profile 0.005 ms of GPU.
- **Throttle** (step 5): the worker starts no plate after 10 or more missed refreshes in 1 s, or a late present call.
  - From E1's data: without load the player misses refreshes in bursts at clock changes, and its present lead barely moves under the painter.
- **Decisions taken here:**
  - The player pulls canvases from fixed-size heap slots; the worker keeps the ones near the playhead in the heap.
  - The side canvases are stored at half the film's resolution.
  - The player reserves its own VRAM (`--vram-reserve-mb`). The painter kept `--reserve-vram 4` GB free, which made it worse (above); phase 3 replaces it.
  - Canvases cover the room's side widths rounded up to 32 source px.
- **Known limits:**
  - T1 edges are stepped where the kept frames end.
  - A cross-dissolve inside a shot mixes two places in its mosaic (left for phase 4).
  - Flow at an eighth of the source size drifts about 1.5 % of a pan's travel; at the picture's edge that is a few px.

## Overview

The walking skeleton, end to end on the real player. A separate worker process reads the film ahead of the playhead and plans the sides. It paints plates with today's recipe and writes the results into a shared side store. The player samples them every film frame and never waits. Tiers are T0 (edge glow, exists), T1 (the shot's own real pixels along the camera path) and T2 (painted plate). Phases 3-5 add speed and higher tiers behind the same contracts. This phase holds the hard-to-change choices.

## Requirements

- Functional:
  - Press play and playback starts at once, with glow until something better is ready.
  - The worker follows the playhead, works ahead in deadline order, and drops stale work after a seek.
  - Plates appear only at cuts, never mid-shot.
  - A cache makes re-watching and seeking back instant.
- Non-functional:
  - Zero wrong-refresh frames caused by the worker (E1 rules applied).
  - The player stays correct if the worker crashes or hangs.
  - Worker VRAM is within its budget.
  - Rests between test runs.

## Architecture

```
film file ─► WORKER (Python, own process)                      PLAYER (perf1, C, D3D11)
             analyse: shots, camera path, step plan, sets      decode + picture passes (exist)
             schedule: earliest deadline from the playhead      │
             paint (T2) / structure (T3) / refine (T4)          │ every film frame (never waits):
             patches ─► SIDE STORE (memory-mapped file) ◄──────┤  read header + index,
                        header: playhead, speed, seek id,       │  upload new patches for coming frames
                                present slack (from player)     │  into the shot's world canvas,
                        index ring + fixed-size slots           │  pass 2 "ai" fill: pixel → side → world
             disk cache (content-model key + recipe id)         │  coords by the shot's camera transform;
                                                                │  seam pull, per-step gain, glow fallback
```

- **Contracts (written down, versioned):**
  - **Side plan**, per shot: set id; the world-canvas extent covering the camera's travel; the camera transform per film frame (dx, dy, scale, rotation; flow mode from `scroll-plate.py`, similarity added in phase 4). Per drawing and side: a state (HOLD, STILL, SLIDE, GAIN, PATCH, GEN), zone rectangles and a gain.
  - **Patch:** shot/set id, the film frame from which it applies, side, a rectangle in world coordinates, tier, format (RGBA8 or RGBA16F linear), colour already fitted.
  - **Playhead record**, written by the player: film time, speed, seek counter, present slack and late-frame count.
- **Why CPU shared memory, not GPU texture sharing.** The worker runs seconds ahead, so latency doesn't matter. Patch traffic is a few MB per second. It needs no CUDA↔D3D11 interop, and a worker crash only stops new patches (the player keeps the last state, then falls to lower tiers). The GPU-sharing report's GPU ring stays the upgrade path if E1 or upload cost demands it.
- **The player stays simple.** The worker does all the thinking: plates, propagation, trails, refining. The player samples one world canvas per shot with the camera transform and applies the seam pull and the per-step gain, both of which need the current picture. The side state updates only on the film's frame clock, so there is no change between refreshes and no boil.
- **Degrade ladder (never stall):** T0 glow → T1 mosaic → T2 plate → T3 structure patches (phase 4) → T4 refined zones (phase 5). A missing tier falls to the one below. Upgrades happen only at cuts.
- **GPU sharing** ([E1](../reports/spike-261013-1950-e1-player-under-ai-load.md)):
  - Kernels up to ~12-15 ms are harmless, so no chunking.
  - The real painter caused ~20 wrong frames a minute through VRAM pressure and weight streaming. So: cap the worker's VRAM (`--reserve-vram`, or fixed allocations in a native pipeline), reserve the player's own VRAM (`SetVideoMemoryReservation`), and batch the encoder reads so the encoder and DiT swap rarely.
  - The player locks GPU clocks while playing ("prefer maximum performance"; the M1 track): it misses frames at clock changes even without AI.
  - Frame latency stays 1 until the engine plans for queue depth (M1).
  - Throttle: the worker pauses when the player reports late frames or low slack.
  - First step of this phase: re-run E1's painter cell with these rules. Target: wrong frames at or below the no-load baseline.
- **Raising the worker's share u (X5: every +0.1 of u buys ~0.3 s per plate).** The player draws a new canvas only when the film frame changes; it reuses it on the other refreshes of that frame.
  - This is the option ADR 0026 names. It applies on shots without pan mode or the rolling blend.
  - At 240 Hz the player now spends ~27% of the GPU (1.14 ms per refresh).
  - A change to the M1 player, so coordinate with that track.

## Related code files

- Create (worker, Python), following the repo's naming: importable modules in snake_case like `comfy_client.py`, entry scripts in kebab-case.
  - Entry script: `spikes/ai-surround/live-worker.py` (main loop, playhead follow, deadline queue, seek handling).
  - In the package `spikes/ai-surround/live/`:
    - `side_plan.py` (schema + writer)
    - `side_store.py` (memory-mapped writer, index ring)
    - `analyse_v0.py` (shots from the content model or an incremental scan; camera path via `scroll-plate.camera_offsets`)
    - `paint_plate.py` (today's recipe, resident; replaced in phase 3)
    - `side_cache.py` (disk cache keyed per ADR 0027)
  - The package docstring describes the contracts (no Markdown outside plans/docs).
- Create (player, C11, additive):
  - `spikes/performer-p1/src/perf1-ai-sides.c/.h` (store reader, uploads, world canvas per shot)
  - `perf1-finish-surround-ai.c` (the fill fragment fused into pass 3)
- Modify (small hooks):
  - `perf1-finish-surround.c` (new mode)
  - `perf1-args.c` (`--ai-sides PATH`)
  - `compile-perf1.sh` (new sources)
  - Not the audio files. `perf1-main.c` only if unavoidable.
- Reuse: `scroll-plate.py`, `hidden_sides.live_seam` (port its maths to the shader), `comfy-plates.py` / `comfy_client.py`, the perf1 control channel (`perf1-control.c`) if the playhead record fits there better.

## Implementation steps

1. Write the three contracts as one versioned schema file plus a short spec in the module docstrings. The cache key follows ADR 0027.
2. Side store, both ends. Python writer and C reader. Self-test: a fake worker writes timed patches, and a headless reader checks order, tears (a sequence number per slot) and overflow.
3. Worker v0:
   - Decode on the CPU, ahead of the playhead.
   - Analyse v0: shots plus camera path; incremental when no content model exists yet.
   - Deadline queue from the playhead; drop stale jobs after a seek.
   - T1 mosaic patches, T2 plates (today's recipe, resident server), disk cache.
4. Player:
   - `--ai-sides` reader; upload in small chunks per film frame through staging textures.
   - A world canvas per shot (capped size, tiled for long pans).
   - Pass 2 "ai" fill with seam pull and gain, glow where nothing is ready, the cut-only upgrade rule.
   - Playhead and slack written back.
5. Throttle: the worker pauses while the player reports late frames or slack below the E1 threshold.
6. Smoke, with rests between runs:
   - JJK windowed for 2 min, then full screen for 10 min.
   - Kill the worker mid-play.
   - Three seeks.
   - Re-watch the same minute (cache).

## Success criteria

- [x] JJK plays on press: T1 within seconds of each shot start, T2 plates arriving as the worker gets ahead, and changes only at cuts. No shot showed glow because its T1 came late; plates arrived in the windowed run (the painter itself is phase 3's).
- [x] Zero wrong-refresh frames caused by the worker over 10 min full screen (player log against E1's baseline). Every fault is at a seek or on the system's 2-second stall, which the no-load cells show too.
- [x] Killing the worker never disturbs playback; sides hold, then fall back.
- [x] After a seek the worker re-plans from the new playhead within one shot. Re-watching is served from the cache (the re-watched minute; the restarted worker's 104 T1 canvases; across window modes after the fix).
- [x] The contracts are documented, and a test proves each one (store self-test, schema round-trip).

## Risk assessment

- **The player belongs to the M1 track (uncommitted audio edits).** Changes are additive, in new files with hooks behind a flag. Tell the user before touching any shared file. Signal: the M1 track changes the same hooks; response: rebase our hook onto theirs.
- **Uploads cause hitches in the player.** Small chunks per frame through staging textures, with the cost measured in the player log. Signal: wrong-refresh frames appear only when patches arrive. Response: move to the GPU ring from the GPU-sharing report.
- **The world canvas gets too big for long pans.** Cap and tile, evicting behind the camera.
- **The content model isn't ready at play.** Incremental analysis ahead of the playhead (the content scan runs ~6× real time on GPU decode; the worker uses the CPU path first).
- **The existing ComfyUI recipe is slow and reloads models** (3-4 min per plate with `--cache-none`). Acceptable for the skeleton: T1 carries until phase 3. Signal: RAM pressure (pagefile growth). Response: keep `LOW_RAM`; phase 3 removes ComfyUI from the live path.
