# Code review: warm start and cache key fixes (AI-surround live worker)

Read-only review. No source file was edited, and no worker, player, GPU job or screen run was started.

## Scope

- Changed: `spikes/ai-surround/live-worker.py`: `MARGIN_STEP_PX` and `room_margins` (:47-54), `cached_tiling` and `warm_start` (:183-232), analyser start-up (:234-236), margins set (:270, :278). `spikes/ai-surround/live/worker_state.py`: `is_done` (:121-132).
- Context read: `analyse_v0.py`, `side_cache.py`, `painter_jobs.py`, `mosaic_t1.py`, `side_store.py`, `film_timeline.py`, `paint_plate.py`. From the player: `performer-p1/src/perf1-ai-sides.c` (showing start, fill margins) and `perf1-ai-sides-record.c` (margin computation).
- `spikes/ai-surround/` is untracked, so there is no git diff. I reviewed the current files plus the two fake-player run logs (scratchpad `warm-check/windowed/20261013-225633` and `fullscreen/20261013-225821`).
- Checks run:
  - `live-analysis-selftest.py`: PASS 10/10.
  - `live-store-selftest.py`: PASS 231/231.
  - `ruff check`: clean on both changed files.
  - `py_compile`: ok.
  - A CPU-only scratch probe covering: `room_margins` edge values and key equality; content model against the cache; a copy of `cached_tiling` on edge cases; `is_done` on a fake film; `load_analysis` on a truncated and an empty npz; `Thread._stop` shadowing.

## Overall

The three fixes meet (a), (b) and (c) for the measured room and today's cache:
- Shots without this room's mosaic are left for the analyser.
- The two measured margin sets give one key.
- Split spans tile, and `is_done` walks their parts.

The weak point is the new control flow. The analyser is started only at the end of the warm thread, and there is no error path. Any exception there leaves the run with no analysis and says nothing in worker.log; one bad cache file is enough to cause it. Two cache-consistency holes (a mosaic without its T1, and greedy tiling across keys) can bring back the symptoms of bugs 1 and 3 in later runs.

## Critical

None.

## High

### H1. An exception in warm_start means no analyser for the whole run, with no trace in worker.log; it repeats every run when a cache file is the cause
- Where: `live-worker.py:194-232` (`analyser.start()` is reached only at :232; there is no try/finally) and `side_cache.py:80-88` (`load_analysis` catches only OSError, ValueError and KeyError).
- Evidence (probe):
  - A truncated npz raises `zipfile.BadZipFile`, and a zero-length npz raises `EOFError`. Neither is caught by `load_analysis`.
  - An exception in a daemon thread is printed to stderr only (worker-stdout.txt in the smoke). worker.log and events.jsonl get nothing, and the worker keeps beating "running".
- Other triggers:
  - `state.publish_analysis` → `store.write_shot` raises IndexError once split rows pass `max_shots` (see M4).
  - `int()` in `cached_spans` fails on an odd file name.
  - A JSON file missing a field: `mm["side_share"]`, `meta["tex_w"]`.
- Failure:
  - No shot is analysed this run. Every uncached shot glows, gets no T1, and new shots get no plates.
  - A zero-length or garbled npz left by a power loss (`os.replace` without fsync) repeats this in every later run until someone deletes the file.
  - Per the task description, the warm start used to run in the main thread, where the same exception crashed the worker visibly at start-up.
- Fix:
  - Wrap the warm thread in `try` / `except Exception` (log it to worker.log) / `finally: analyser.start()`.
  - Put a `try` around each part, so one bad file only sends its span to the analyser.
  - Make `load_analysis` use `except Exception: return None` (re-analysis overwrites the bad file).

## Medium

### M1. A mosaic cached without its T1 canvas is warm-loaded as complete, so the shot never gets T1 (bug 1's symptom)
- Where:
  - `live-worker.py:154` (`save_mosaic`) runs before `:164` (`save_canvas`).
  - `warm_start` (:211-227) requires only the mosaic, and adds T1 only `if meta is not None`.
- Failure:
  - The worker can stop between the two writes. The window is `canvas_rgba` plus the T1 PNG write, about 0.1-0.3 s per non-empty shot. Ways it stops there:
    - `live-smoke.py --kill-worker-at` uses `kill_tree`.
    - A normal interpreter exit stops the daemon analyser in the middle of `on_shot`.
  - Every later run with this room then publishes the shot without T1. `is_done` is True, so nothing ever re-makes it, and the shot glows.
  - Short shots (< 0.8 s, never plated) and `--no-paint` runs stay on glow permanently.
  - Probe: today's cache has 0 non-empty mosaics without their T1, so the hole is latent.
- Fix:
  - Write the T1 canvas before the mosaic, so the mosaic JSON acts as the commit marker. Or, in `warm_start`, treat `side_share >= EMPTY_SHARE and canvas_meta(T1) is None` as not cached.
  - A T1 can also be re-made from the cached mosaic without decoding (`load_mosaic` → `canvas_rgba`).

### M2. cached_tiling takes the longest entry of any key: an older analysis version that did not split a span hides the current split in every run
- Where: `live-worker.py:200-202` (`on_disk` is built from every `*.npz`) and :187-191 (`max(fits)`). Also `side_cache.py:90-91`: the docstring says "current analysis version", but the glob takes every key.
- Probe:
  - `{0: {300 (old key), 100}, 100: {200}}` → `[(0, 300)]` → `load_analysis` with the current key returns None → the span is counted "again".
  - Re-analysis rewrites (0,100) and (100,200), but the old file stays, so the same miss happens every run.
  - `on_shot` (:168-176) registers only T1, never the cached plate under the same rkey. The painter therefore repaints those parts every run (~20 s GPU each) and overwrites the cached plates.
  - Greedy dead end: `{0: {50, 100}, 50: {250}}` → `[]`, although 50 + 250 tiles the span.
- Trigger: an `ANALYSIS_VERSION` bump that adds or moves hidden cuts. This is likely: 163 of the 347 cached split parts are 2 frames or shorter, which makes the cut detector a candidate for tuning. In today's cache no first frame has two n values, so this is latent.
- Fix:
  - Keep only files whose key equals `analysis_key(film.crop_at(film.cut_time(first)))`; `cached_spans` would return the key as well. Or try candidates longest-first, with backtracking and a load check.
  - Fix the docstring.
  - Optionally, have `on_shot` register a cached plate (one `canvas_meta(TIER_PLATE, plate_rkey(mkey))` lookup), so any re-analysis reuses it.

### M3. Rounding up to 32 px still splits at the step edges: (b) holds for this room, not in general
- Where: `live-worker.py:47-54`.
- Probe: 831.8 → 832, but 832.3 → 864.
- Failure:
  - With the measured spreads (0.3 / 0.7 / 0.7 px), about (0.3 + 0.7 + 0.7) / 32 ≈ 5 % of rooms will have a side that straddles a step edge. Bug 2 then returns for that room: every shot is re-analysed and every plate repainted.
  - This room's right margin sits only 3.2-3.9 px under 1504.
  - 0 is also an edge: a flush side that measures 0 in one mode and a sub-pixel overhang in the other flips from 0 to 32. The player itself treats margins of 1 px or less as none (`perf1-ai-sides.c:302`).
- The comment at :47-48 overclaims ("a sub-pixel change in its measurement then finds the canvases cached").
- Fix options:
  1. Clamp values of 1 px or less to 0 before rounding. Cheap, and removes the edge at 0.
  2. On a miss, also try the next step up for each non-zero side (a bigger canvas covers a smaller room).
  3. Look up by containment: store the margins in the mosaic JSON or name, and use any cached mosaic whose margins are at least the room's.
  4. Accept the residual and document it.

### M4. The shot-row budget is tight for the hidden-cut rate; with the new is_done, a row overflow stalls the analyser on one span
- Where:
  - `live-worker.py:140`: `max_shots = 2N + 64`.
  - `analyse_v0.py:348`: a row is taken when the tracker starts, and an abandoned tracker leaks it.
  - `worker_state.py:173` sets `analysed = True` before :176 `write_shot` raises.
  - `analyse_v0.py:302-305` with :271-272: the retry from `start + fps` lands back on the same span.
- Data:
  - 679 spans, with ids 0..678 contiguous.
  - The cache holds 347 split parts over 519 spans (0.67 per span), so about 455 for the whole film against 743 spare rows. Fine for this film.
  - A film with more than about 1.1 hidden cuts per span runs out of rows in one pass.
- Failure once rows run out:
  - In the warm start: IndexError, which leads to H1.
  - In the analyser, for a span of 1 s or more with 3 or more parts:
    - Part 2's publish raises on every attempt. Each retry takes a fresh row, and part 3 is never reached.
    - `is_done` stays False, so the span is re-decoded and part 1 re-published about once a second for the rest of the run. Nothing after that span is analysed.
  - The old `is_done` (done once part 1 was in) moved on instead.
- Fix:
  - Rows are 64 B each, so size the table generously (e.g. 8N + 256, about 0.35 MB).
  - Allocate rows in `finish()`.
  - Reuse `by_first[first]` in `new_shot_id(first)`, which today ignores `first`.
  - In `run()`, skip a span after 2-3 failures.

## Low

- **L1: painter double-paint race** (`live-worker.py:219-227` vs `painter_jobs.py:64-71`).
  - Between `set_mosaic` and `add_canvas(T2)`, a warm-loaded shot can be picked without its cached plate and gets repainted (~20 s GPU).
  - The window is about 1 ms per part, and only matters when ComfyUI is already up during the warm start (the player connects late, or margins come from the 20 s fallback).
  - `ensure_heap` can likewise load T1 and then T2 for the same shot.
  - Fix: read the metas first, then do publish + `set_mosaic` + `add_canvas` inside one `with state.lock:` (it is an RLock).
  - Related: `save_canvas` at `painter_jobs.py:128` is outside the try. On Windows, `os.replace` can fail while `ensure_heap` has the same PNG open, and that error kills the paint thread.
- **L2: orphan rows on re-analysis** (`worker_state.py:141-145`, `analyse_v0.py:342-349`).
  - A span with 3 or more parts that a seek abandons after part 2 is later re-analysed whole. Part 2 gets a new row, and the old row stays analysed in `state.shots`.
  - `ensure_heap` then keeps the old row's canvas in a slot, and the painter can plate it.
  - Same fix as M4: reuse `by_first[first]`.
- **L3: `cached_tiling` never ends on an n = 0 entry** (probe: infinite loop).
  - The analyser never writes n = 0, so only a stray file can cause this. It would block the warm thread, so the analyser would never start.
  - Fix: add an `n > 0` guard.
- **L4: input checks** (`live-worker.py:54`, :278).
  - From the player, `inf` raises OverflowError in the main loop and the worker exits. NaN becomes 0, so that side gets no canvas.
  - `--margins` with other than 4 values, or with text, fails only at the 20 s fallback: a ValueError in the main loop, or a `canvas_geometry` unpack error inside `on_shot`, which puts the analyser in a retry loop.
  - Fix: validate at parse time, and clamp non-finite player values.
- **L5: `Thread._stop` is shadowed by an Event** in `Analyser` (`analyse_v0.py:235`) and `PainterJobs` (`painter_jobs.py:35`).
  - Probe on the venv's Python 3.12.10: once the thread has ended, `is_alive()` and `join()` raise TypeError.
  - Nothing calls them today, but a future `analyser.join()` at shutdown, or an `is_alive()` guard against a double start, would crash.
  - Fix: rename the attribute to `_stop_ev`.
- **L6: leftovers.**
  - `SideCache.canvas_metas()` (`side_cache.py:162-170`) has had no caller since `warm_canvases` was removed.
  - The warm-start log line counts current-key misses (another crop or analysis version) as "another room or mosaic version".

## Verified OK

- **(a)** A span is published only if every part loads and has this room's mosaic. Otherwise none of it is published and the analyser does it. In the windowed run: 0 shots warm-loaded, 546 spans "analysed again".
- **(b)** (817.6, 0, 1500.1, 275.0) and (817.9, 0, 1500.8, 275.7) both give (832, 0, 1504, 288) and the same mosaic key (probe). Rounding leaves the default `--margins 960,0,960,0` unchanged, so caches made with fallback margins keep their keys.
- **(c)**
  - `is_done`: part 1 only → False; parts 1+2 → True (probe).
  - The first part keeps the span id, and later parts take new rows. `next_row = 679` does not collide, because ids are contiguous.
  - The full-screen run warm-loaded 79 shots and continued from shot 56, with no overlap.
- **Seeks:** `goto()` before the start only sets `_goto`, and `run()` takes the latest value.
- **Shutdown:** a `stop()` before the start makes the later `start()` exit at once. The warm thread is a daemon, so being blocked in `margins_ready.wait()` at exit is harmless.
- **Double start:** impossible; there is one call site.
- **Thread safety:**
  - The warm thread changes state only through methods that take the lock.
  - Margins are handed over through the Event.
  - The analyser is not running during the warm start, so nothing is published twice.
- **No content model:** the same all-or-nothing rule applies per cached shot. Re-analysis boundaries stay right (`at_cut` via `is_done(start - 1, 1)`).
- **Painter:** `mosaic_key`, `first` and `n` are per part, so `load_mosaic` and the plate keys line up.
- **Player contract:**
  - `side-store.layout.json` was last changed at 20:32, before these edits, and the store writer did not change.
  - The fill uses the player's own margins, so a larger canvas is fine.
  - A plan published mid-shot (the warm start now runs after the player is up) gives glow for that shot, with no pop-in (`perf1-ai-sides.c:369`, `at_start` false).
- **Start-up:** the analyser now waits for the margins plus the warm loop (0.4-0.55 s in the logs). Before, `on_shot` blocked on margins after one shot anyway, so the difference is negligible.

## Recommended actions

1. H1: try/finally around the warm thread, and widen `load_analysis`.
2. M1: change the write order, or check for T1 in the warm start.
3. M2: filter by the current key. M4: row budget, row reuse, failure cap.
4. M3: decide; at least clamp margins of 1 px or less to 0.
5. The Low items.

## Metrics

- Tests: no automated test covers `warm_start`, `cached_tiling`, `room_margins` or split `is_done`. The evidence is the 2 fake-player runs and the probe.
- Lint: ruff 0 issues on the 2 changed files.

## Unresolved questions

1. Is a corrupt cache file (power loss) in scope, or is deleting the cache an acceptable recovery? This decides how wide H1's fix goes.
2. Should a canvas made for larger margins serve a smaller room (the containment lookup in M3), or is the residual acceptable for now?
3. Are the 1-2 frame hidden cuts (163 of 347 split parts) intended? They drive row use (M4) and the mid-shot canvas changes from the previous review's M2.
4. Should a re-analysed shot reuse its cached plate (the note in M2), or is a repaint wanted?
