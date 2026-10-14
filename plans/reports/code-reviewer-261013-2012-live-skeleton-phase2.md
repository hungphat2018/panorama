# Code review: AI surround live path, phase 2 (live skeleton)

Plan: `plans/261013-0718-ai-surround-live-path/phase-02-live-skeleton.md`, plan.md acceptance criteria (AC) 1, 3, 5, 6, 8.
Advisory only; no source file edited.

## Scope

- Worker (Python, new): `spikes/ai-surround/live/{__init__, side_store, film_timeline, frame_reader, analyse_v0, mosaic_t1, paint_plate, side_cache, worker_state, painter_jobs}.py`, `side-store.layout.json`, `live-worker.py`, `live-cues.py`, `live-smoke.py`, `live-store-selftest.py`, `live-analysis-selftest.py`; `hidden_sides.flow_step` (file is untracked, reviewed as is).
- Player (C, new): `src/perf1-ai-sides{-store.h,-store.c,.h,-internal.h,.c,-upload.c}`, `src/perf1-finish-surround-ai.c`, `src/perf1-gpu-share.{c,h}`, `ai-sides-selftest.c`.
- Player hooks (diff): `perf1-picture-render.c`, `perf1-finish-surround.c`, `perf1-finish-passes.h`, `perf1-picture-shader.{c,h}`, `perf1-picture-pass1.c`, `perf1-args.c`, `perf1-display.c` (gpu-share hooks; not listed in the task but changed), `compile-perf1.sh`.
- LOC: ~5,000 new (C ~2,050, Python ~2,780, layout JSON 161), ~60 changed in shared player files.
- Checks run: `live-store-selftest.py --seconds 4` PASS 231/231 (torn copies caught 57, corrupt 0, **records_busy 225**); `live-analysis-selftest.py` PASS 10/10; `gcc -fsyntax-only -std=gnu11 -Wall -Wextra` (repo flags) on the 10 new/changed player files + selftest: 0 warnings. Two scratch probes (below) against `bin/ai-sides-selftest.exe` (CPU only, temp files removed).

## Overall assessment

The store protocol core is sound: Boehm-style seqlock reader (acquire load, copy, acquire fence, relaxed re-load), parity-robust player-block writer, canvas `gen` re-checked after every chunk, texel conventions consistent end to end (mosaic warp, frame transform, shader `t * size.zw`, seam profile `texel i = coord i + 0.5`). The no-flag path is effectively zero-cost and the GLSL split is token-identical.

The weak points are around the store's life cycle and the worker's scheduling, not the hot path: the documented "either may start first / restart the worker" path crashes (verified), the analyser busy-loops at its lookahead and, without a content model, stops producing shots; and several player-side behaviours break AC3/AC5 in predictable ways (first-use shader compile mid-film, a torn read drops the sides to glow for a refresh, GPU-canvas pin/evict thrash).

## Critical

### C1. Worker cannot start while a player maps the store: `open_store` catches the wrong exception
- Where: `live-worker.py:66-78` (`except PermissionError` at :72), `live/side_store.py:112` (`open(path, "w+b")`).
- Evidence (scratch probe, reader = `ai-sides-selftest.exe watch` mapping the file like perf1): `SideStore.create` raised `OSError: [Errno 22] Invalid argument`, `isinstance(e, PermissionError) == False`. CREATE_ALWAYS on a file with a user-mapped section fails with ERROR_USER_MAPPED_FILE (1224), which the CRT maps to EINVAL.
- Failure: the worker dies at start-up whenever a player already maps the store, so the `reset()` fallback is unreachable:
  - `live-smoke.py --restart-worker-at` (smoke step "kill the worker mid-play" + restart) fails every time;
  - "Either may start first" (`live-worker.py:9`) fails whenever a store from an earlier run exists at the path; `live-smoke.py:178` reuses `out/live/side-store.bin` and `:206` waits only for `store.exists()` (already true) + 1 s, so the player can map the old store before the worker reaches `create`, a start-up race in every smoke after the first.
- Fix: if the file exists, `SideStore.open()`; if `fits()` (also compare `heap_slots`/`heap_slot_bytes`), `reset()` and use it; otherwise `create()` and catch `OSError` (errno 22/13, or `winerror in (32, 1224)`) with a clear exit. In the smoke, wait for `worker.state == running` and a new `store_uid`, not file existence.

## High

### H1. Analyser busy-loops at the lookahead; without a content model it stops producing shots
- Where: `live/analyse_v0.py:316-317` (`return tracker.first ...` at the lookahead), `:283-292` (`run` re-enters without sleeping), `:341` (`cut = idx == 0 or (g_prev is not None ...)`), `:331`, `:350` (`new_shot_id` per new tracker).
- Failure (both modes): at steady state one shot always straddles `playhead + lookahead`. `_decode_from` abandons it and returns its first frame, and `run` immediately decodes it again (it is within the lookahead), up to the same boundary. That is a tight loop: an ffmpeg spawn plus decode and DIS per pass, continuous, and forever while paused. With the default `--lookahead-s 600` this starts a minute or two into every session. It burns several CPU cores on a laptop that shares its thermal budget with the GPU (E1: clock changes cause misses).
- Failure (incremental, no content model): a decode that starts at a shot's first frame never sees that shot's cut (`g_prev` is None and `idx != 0`), so the abandoned shot is never analysed. `_start_frame(0, False)` keeps returning that gap and re-decodes it; each abandoned tracker leaks a shot row (`max_shots = n_frames // 4 + 16`). In practice, once the analyser reaches the lookahead, later shots get no T1 or T2. In content-model mode, the second part of a hidden-cut span abandoned at the boundary is likewise never analysed: `is_done(span)` is already true.
- Fix: at the boundary, wait inside `_decode_from` (poll `_stop`/`_pending_goto`/playhead every 0.25 s; ffmpeg blocks on the pipe), or finish the current shot regardless (it is bounded by `MAX_SHOT_S`). Incremental mode: treat `idx == start` as a cut when `start > 0` and frame `start - 1` belongs to a finished shot, or start the reader one frame early. Allocate shot rows in `finish()`, not when the tracker is created.

### H2. First canvas showing compiles new shaders mid-film (AC3)
- Where: warm-up `perf1-render.c:363-416` ("so shader compilation ... not counted against the film"), `perf1-finish-surround-ai.c:129-130` (fill appends nothing while `!in->on`), `:186-228` (seam dispatch and its two passes created lazily).
- Failure: at warm-up no canvas can be on the GPU, so only the pass-2/3 variant *without* the AI fragment is compiled. The first showing with a canvas compiles, on the render thread and mid-film: the fused pass with `pic_glsl_geometry` + fill (once per monitor dispatch) and the two seam passes (D3D11: shaderc, SPIRV-Cross, D3DCompile, typically tens of ms). The result is several wrong-refresh frames in every run, at the first T1 showing.
- Fix: warm the AI variants in `render_warm_up`: draw the warm-up frame once per monitor with `fi->on = true` (1x1 `G_AI.dummy` as `ai_tex` and `ai_seam_tex`; uniforms are dynamic, so the text is the one used later) and run `ai_seam_profile` once against the warm-up frame. Optionally persist with `pl_cache`.

### H3. A torn read of the frame or shot record drops the sides to glow for that refresh (AC5)
- Where: `perf1-ai-sides.c:410-424` (any read failure leaves `fi->on = false`), `perf1-ai-sides-store.c:11` (`READ_TRIES 64`, about 64 PAUSEs, a few µs), `live/side_store.py:234-242` (`write_shot`: about 10 numpy field writes between counter bumps), `live/worker_state.py:174-183,192-209` (every new canvas version rewrites the shot row), `painter_jobs.py:129`.
- Failure: the Python write window (tens of µs; ≥ 5 ms if the GIL switches threads mid-write) is far longer than the reader's spin. A read overlapping a write fails, and the refresh shows glow instead of the canvas: a 1-2 refresh flash mid-shot. Triggers:
  - a T2 plate that finishes after its shot started (estimate overrun; the first plate after model load) rewrites the on-screen shot's row;
  - at a cut, a failed read means no showing on that refresh, then the canvas on the next refresh of the same film frame;
  - a suspended or hung worker that leaves a counter odd.

  `records_busy: 225` (of about 2,080 canvas reads) in today's self-test shows how often reads fail under write load.
- Fix: make the fill sticky. Per monitor keep the last good `ai_frame_rec` with its index and reuse it when the read of the same index fails. Look up the showing by `(epoch, fr.shot)` first, and read the shot row only when creating one. Writer: don't rewrite the shot row for the diagnostic `best_tier`, or assign the whole record at once between the two counter bumps.

### H4. GPU canvas residency: stale pins plus nearest/farthest thrash; texture re-created per canvas
- Where: `perf1-ai-sides-upload.c:53-59` (`pinned`: any of the last 4 showings, `AI_SHOWINGS 4` in `-internal.h:11`, never released after their shot ends), `:62-80` (`gpu_place` evicts the farthest-ahead canvas even for a candidate farther ahead), `:181-183` (walk starts at the current shot), `:135-149` (`pl_tex_destroy` + `pl_tex_create` whenever the size differs, which is almost every canvas).
- Failure: with the default 8 GPU canvases, up to 4 are pinned by past shots, leaving about 4 for upcoming ones. On action scenes (1-2 s shots, 8 s horizon) with 5+ ready canvases, the walk uploads #5 by evicting #4, then #4 by evicting #5, forever: continuous 256 KB/refresh `UpdateSubresource` and a texture destroy/create on the render thread every canvas (VRAM-pressure sensitive, per E1). The walk also spends uploads on the current shot's canvas, which this showing can no longer use.
- Fix:
  - pin only showings drawn within the last ~0.5 s (store `last_drawn` per showing);
  - in `gpu_place`, never evict an ahead canvas for a candidate that starts later (return -1);
  - start the walk at the next shot's first frame;
  - allocate textures in size buckets (round w/h up, e.g. to 256), keep `ai_size` = canvas extent for the range check and use the allocated size for uv.

## Medium

### M1. Decode aspect assumed 16:9: wrong vertical geometry for any other stored size
- Where: `frame_reader.py:33` (`scale=960:540`), `analyse_v0.py:41,224` (`ds = src_w / 960` used for both axes), `:136-137,145`, `mosaic_t1.py:108-121`.
- Failure: a 1920x800, 1440x1080 or 4:3 film is stretched to 960x540, but y uses `src_w/960`. That gives a wrong flow crop, a wrong `dy`, and a vertically mis-scaled mosaic, so lines do not continue at the top/bottom seams. JJK/Judas at 1920x1080 pass, which is why the self-test (1920x1080) cannot see it.
- Fix: decode at `960 x round(960*src_h/src_w/2)*2` and carry `ds_x, ds_y` separately. Add a non-16:9 case to `live-analysis-selftest.py`.

### M2. Synthetic splits change the canvas mid-shot (cut-only rule, AC5)
- Where: `worker_state.py:163-164` (CUT flag on every shot's first frame), `analyse_v0.py:343-351` (60 s split, incremental), `:329-332` (hidden cut), `perf1-ai-sides.c:416` (`at_start` from the CUT flag).
- Failure: at a 60 s split, or a false-positive hidden cut, the player starts a new showing with a different canvas and world origin in the middle of a picture shot: a visible jump (or a tier change) without a cut.
- Fix: no CUT flag on splits. Give split parts one canvas id (set) with continuous offsets, or flag "continues" and keep the showing.

### M3. Store counters keep crash-parity forever
- Where: `side_store.py:159-180` (`reset` does `+= 1` twice), `:213-230`, `:234-242`.
- Evidence (scratch probe): seq odd after a simulated mid-write death; after `reset` → `[3,3,3,3,3]`; after the next `write_frame_plan` → `[5,5,...]`.
- Failure: rows are odd forever, so the player reads glow for them. During each later write the counter is even, so a half-written record can be accepted. Death mid-write is plausible: `taskkill /F` in the smoke, and daemon threads frozen at interpreter exit. It only matters through `reset()`, which becomes reachable once C1 is fixed.
- Fix: writers use `seq |= 1` to begin and `seq += 1` to end, as `ai_store_player_begin/end` already do. `reset()` first makes all frame/shot counters even.

### M4. Worker restart drops the on-screen showing mid-shot
- Where: `perf1-ai-sides.c:147-153` → `forget_store_contents()` `:94-102`.
- Failure: a new `store_uid` clears every showing and GPU canvas. The current shot goes to glow at once and stays there until the next cut, even for the same film with the same canvases. This contradicts "sides hold, then fall back" for the restart variant.
- Fix: on a uid change, keep the current showings and their GPU textures until the next cut, holding the last good frame record (see H3). Also re-run `store_matches()` on a uid change: today a reset by a worker for another film is adopted silently (`:105-115` runs only at open).

### M5. Render-thread blocking work in the AI path
- Where: `perf1-ai-sides.c:117-145` (`CreateFileW`/`MapViewOfFile`/`_stat64` every 1 s until open, on the master's refresh), `:172-220` (`compute_margins`: about 1,000 `pic_map_pixel` per unique layout; the dedup array `seen[64]` stops growing after 64, so a planner sheet with hundreds of placements maps everything again, about 50 ms+ at a sheet change), `perf1-ai-sides-upload.c:108-111` (`UpdateSubresource` reads file-backed mapped pages: hard faults under RAM pressure, which the plan expects with ComfyUI).
- Fix: open, map and compute margins on a helper thread, handing results over by pointer. Dedup with an unbounded hash set. Prefetch the next chunk (`PrefetchVirtualMemory`) or copy it to private memory off-thread. Log the open and chunk times (the chunk CPU p99 exists; open time does not).

### M6. Warm start decodes every cached mosaic PNG on the main loop
- Where: `live-worker.py:180-198` → `side_cache.py:114-124` (`load_mosaic` reads the PNG; only `m.empty` is used, which comes from `side_share` in the JSON).
- Failure: re-watch with a full cache: about 680 shots x 20-50 ms gives a 15-30 s stall. No beats (the player logs the worker lost), seeks and pressure are not followed, and the heap is not filled.
- Fix: read only the JSON (`side_share`), or store `empty` in the JSON.

### M7. Rolling blend: the fill mixes frame A's view/slide with the front frame's transform
- Where: `perf1-picture-render.c:219-235` (job built from `shot_a`/`slide_a`), `perf1-ai-sides.c:353-361` vs `:410` (frame looked up by `r->pts`), seam cache keyed by pts only (`perf1-finish-surround-ai.c:183-185`).
- Failure: on blended refreshes where the partner is the previous frame, the sides are offset by one frame's slide. At a cut they use the old shot's crop/rect with the new shot's canvas.
- Fix: use the front frame's own view/slide (`partner_is_a ? job.view_b/slide_b : job.view/slide`), or turn the fill off on blended refreshes (if blend + AI is out of scope, assert it).

### M8. A slot-size change between runs crashes the worker main loop
- Where: `side_cache.py:100-102` (mosaic key has no slot bytes), `mosaic_t1.py:68-80` (scale depends on `max_bytes`), `side_store.py:262-263` (raises), `worker_state.py:245`, `live-worker.py:251` (only `KeyboardInterrupt` is handled).
- Fix: put `max_bytes` (or the final scale) in the key, or check `h*w*4 <= heap_slot_bytes` before `begin_slot` and skip.

### M9. Painter hang blocks plates for the rest of the run
- Where: `painter-speed.py:373-381` (`run_plate` polls forever; `urlopen` without a timeout), `painter_jobs.py:113-121`.
- Fix: a deadline per plate (the shot's start minus lead); on expiry POST `/interrupt` and mark the plate failed. Add a timeout to `urlopen`.

### M10. Worker and player crops are never compared
- Where: `film_timeline.py:79-85` (whole frame when `--cues` is missing), shot record `crop` unused by the player, `perf1-ai-sides.c:355` (player crop).
- Failure: a worker without `--cues`, or a planner whose layout crop differs, puts letterbox bars into the mosaic. Black bands then show next to the picture.
- Fix: in `showing_for`, compare `sr.crop` with `job->view->crop` (1 px tolerance); on mismatch use glow and log once.

## Low

- L1 `perf1-gpu-share.c:221-229`: `gpu_share_after_device` always prints 2-3 lines and writes `OUT/gpu-share.json`, so a run without the options is not "unchanged" (`perf1-gpu-share.h:2-3`). It adds no picture or timing change. Make the report opt-in or update the claim.
- L2 `perf1-ai-sides-upload.c:88-90`: `tex_h * row_bytes <= heap_slot_bytes` is not checked (only `bytes`). A buggy record could make `UpdateSubresource` read past the mapping, which would crash the player. One-line guard.
- L3 `perf1-ai-sides-store.h:4` refers to `spikes/ai-surround/live/test_side_store.py`, which does not exist (the test is `live-store-selftest.py`).
- L4 `live-worker.py:91`: `--reserve-vram-gb 3` on a 12 GB GPU lets ComfyUI reach about 9 GB, above AC8's ~8 GB budget. Default it to ≥ 4.
- L5 `side_cache.py`: no size cap or eviction (PNG level 1, several MB per canvas; about 3 files per shot per margins/scale). `analyse_v0.py:174-176`: `travel_keep` is unbounded until `finish()`, 1.5 MB per pick on long pans (content-model shots are not split at 60 s, contrary to the docstring at `:7`).
- L6 `frame_reader.py:22,64-69`: the 1 ms match relies on FFmpeg ≥ 7 printing `pts_time` with 6 decimals (8.1 on PATH today; 6.x prints 6 significant digits, so frames after about 1000 s would never match). Match the integer `pts:` ticks the regex already sees.
- L7 `side_store.py:12-14`: "stores ... in program order" holds for regular stores and for the CRT's NT-store memcpy (it ends with SFENCE), so publication is safe. NT stores issued after `begin_slot`'s odd `gen` are not architecturally ordered after it; this is benign on Intel and AMD in practice. Document it rather than claim it.
- L8 `perf1-ai-sides.c` is 504 lines covering options, store, margins, pressure, showings and fill. Split margins and pressure out (repo guidance: about 200 lines). `perf1-ai-sides.c:58`: `--ai-upload-kb` allows 64 MB per refresh; cap it near 4 MB.

## Edge cases found by scouting

- Player before worker on a fresh path: safe. A half-written layout passes the bounds check with zero tables, but `store_matches()` rejects it until the worker block is written.
- Worker killed (no restart): sides hold. The heap keeps serving uploads, and "worker lost" only logs.
- Seek storm: one showing per epoch is fine; the worker respawns ffmpeg per `goto` (bounded by the 10 Hz loop).
- Frames without a plan, an empty mosaic, VFR, pts tolerance (+1 µs lookup, 1 ms `valid_from`): handled.
- Canvases over 8k: capped at ≤ 16384 on both ends by raising the scale (coarser, not tiled as the plan says).
- Headless: `!r->sf` returns before any GPU call.
- No `--ai-sides`: one early-return call per refresh; `fp_surround.ai_fill` is NULL (`calloc`); pass-1 header tokens identical (`common \n geometry \n resample`).
- `G_AI.dp` never `pl_dispatch_reset_frame`d: harmless (`pl_dispatch_begin` uses id 0, only 2 passes, verified in libplacebo `dispatch.c`).
- Upload source: the D3D11 path is `UpdateSubresource`, which consumes the pointer before returning, so the gen re-check after each chunk is valid (`d3d11/gpu_tex.c`).

## Positive observations (risk calibration only)

- Reader/writer memory ordering is correct for x86-64 on both ends. `ai_store_player_begin/end` is parity-robust and is the model for M3.
- The self-tests are real (garbage written inside every odd window, the pattern checked). Torn copies are caught and none are corrupt.
- Geometry is consistent across worker, store and shader. The DIS sign convention matches the contract (`w = p - d`).

## Recommended actions (order)

1. C1 (store open/reset path + smoke wait), then M3 (counter parity) in the same change.
2. H1 (lookahead wait; incremental first-frame cut; row allocation at finish).
3. H2 (warm the AI shader variants).
4. H3 (sticky fill; lighter shot-row writes).
5. H4 (pins, eviction rule, walk start, texture buckets).
6. M1, M2, M4, M5, M6; then the rest.

## Plan follow-ups (for the lead; plan files not edited)

- Phase 2 status is still `pending` in `phase-02-live-skeleton.md` and `plan.md` while steps 1-4 have code.
- Deviations to record: no `side_plan.py` (the side plan lives in the layout JSON plus `side_store`/`worker_state`); "tiled for long pans" is implemented as a coarser scale; uploads go through `UpdateSubresource` per master refresh, not staging textures per film frame; `perf1-display.c` (an M1 file) was changed for gpu-share.
- AC6 (local only): met (ComfyUI on 127.0.0.1 only). AC3, AC5 and AC8 are at risk until H1-H4 and M2 are fixed. The steps "kill + restart worker" and re-watch from cache are blocked by C1 and M6.

## Metrics

- Type coverage: n/a (C11; Python partially annotated).
- Tests: 2 self-test suites (store protocol + schema round trip 231 checks; analysis geometry 10 checks). None cover the showing/eviction logic, worker scheduling (lookahead, gaps, splits), `reset()` with a mapped player, non-16:9, or the rolling blend.
- Lint: gcc `-Wall -Wextra` (repo flags) 0 warnings on 11 files.

## Unresolved questions

1. Was the user told before the shared M1 files were touched (`perf1-display.c`, `perf1-args.c`, `perf1-finish-surround.c`, `perf1-picture-*`), as the phase's risk section requires?
2. Will Judas S01E02 play with a content model? If not, H1's incremental failure hits AC1/AC2 on that episode directly.
3. Is the rolling blend ever on together with `--ai-sides` (M7: fix, or gate it)?
4. On a worker restart mid-shot: hold the current showing (needs the H3 cache) or accept glow until the next cut?
5. Should 60 s splits and hidden cuts share one canvas (set id) so the cut-only rule holds (M2)?
6. Is `gpu-share.json` in every default run wanted (E1 tooling) or should it be opt-in (L1)?
