# Code review: phase 4 structure path (AI surround live path)

I only read files: no source file was edited. Commands run: file reads, grep, git status/diff, ls/du/find, and `unzip -p | od` to read arrays from the worker's live cache. There was no decode, encode, build, test suite or Python run, because panorama-d1 is timing the GPU and the screens.

## Scope

- **Python (`spikes/ai-surround/`, untracked, so there is no diff; I reviewed the current files):**
  - New: `live/camera_model.py` (254 lines), `live/step_planner.py` (212), `live/structure.py` (288), `identity-refs.py` (271).
  - Changed: `analyse_v0.py`, `mosaic_t1.py`, `set_atlas.py`, `worker_state.py`, `side_store.py`, `side-store.layout.json`, `side_cache.py`, `live-worker.py`, `true-sides-test.py`, `live-fake-player.py`.
  - Self-tests: store, analysis and sets.
- **C (`spikes/performer-p1/`):**
  - New: `perf1-ai-sides-patches.c` (178 lines).
  - Changed: `perf1-ai-sides-store.{h,c}`, `-internal.h`, `perf1-ai-sides.c`, `-upload.c`, `perf1-finish-surround-ai.c`, `ai-sides-selftest.c`, and `compile-perf1.sh` (13 lines added; the only tracked file in scope).
- **What I checked beyond the files:**
  - File modification times, to see which player files phase 4 touched.
  - Every consumer of the store records.
  - The content-model schemas.
  - The CCIP model folder.
  - **The live worker cache** (`out/live/cache/6bc0…/`). The main session's worker was writing to it during this review (02:29–02:45). Its arrays give the real-data evidence behind H1–H3.

## Overall

- **What holds:**
  - The coordinate chain is consistent end to end.
  - The patch page's counter rules are correct on both ends.
  - The C reader checks every rectangle it reads from the store.
  - Shots without a patch page take the old path.
  - The M1 player files are untouched.
- **Where it breaks:** the data the worker makes, and what scale does to it.
  - The per-side gains are wrong on many shots, and they apply to every tier.
  - Exit strips are packed as bounding boxes, so a page fills after a few drawings.
  - Pages stay in the worker's RAM with no budget.
- **On the player side:**
  - Two monitors showing different frames of one shot will likely force full re-applies of the patches.
  - Two texture pairs, held for 1 s after their shot was last drawn, make patches appear mid-shot.
  - Pages compete with canvases for GPU slots.

## Critical

None.

## High

### H1. The side gains are wrong on many shots, on every tier (a regression from phase 2)

- **Where:**
  - `step_planner.py:26-31`: the gain is a product of per-step light factors since the shot's first frame.
  - `step_planner.py:95-103`: the light factor is counted for every explained step, STILL included.
  - `step_planner.py:178-185`: only explained steps multiply `_light`; a PATCH or GEN step drops its factor.
  - `analyse_v0.py:204-205`: new analyses always have `gains4`, so v0's median rule (`:89-95`) never runs.
  - `mosaic_t1.py:141-146`: the T1 canvas holds the median-bright kept frame per texel, and T2 is painted from it.
- **Three defects:**
  1. **Reference mismatch.** The gains are referenced to the first frame, but the canvas content is median-bright. A fade-in shows full-brightness sides over a black picture, then up to `GAIN_MAX` 2.5x.
  2. **Asymmetric counting.** A darkening explained by GAIN or STILL, followed by a brightening flagged PATCH or GEN, leaves a permanent offset for the rest of the shot.
  3. **Dark bands.** DIFF 16 codes "explains" large relative changes as STILL, and the ^2.4 factor amplifies them.
- **Evidence from the live cache (key `ef306639…`):**
  - 39 of 312 shots have a side gain outside [0.75, 1.3] at some frame; 5 go below 0.3.
  - Shot 5287-28:
    - The luma goes 0.016 → 0.0013 → 0.0125, recovering to 78 %.
    - Yet the gains end at L 0.18, **T 0.0113**, R 0.67, B 0.52 from about frame 3 on: the top side is about black for 25 of 28 frames.
    - The states show the darkening steps were GAIN or STILL and the brightening steps PATCH or GEN.
  - Shot 2937-36: the luma goes 0.043 → 0.21 (about 5x), while L, T and R stay at 1.0 and B is stuck at 0.23.
- **Contract mismatch:**
  - The plan (`phase-04:63`) and `side-store.layout.json:99` say "the band's brightness over its median over the shot".
  - `live/__init__.py:15-16` and the code say "since the shot began".
- **Failure:** any shot with a flash, a fade or a dark stretch gets near-black or blown-out sides for the rest of the shot.
- **Why the checks miss it:**
  - X1c cannot see it: the harness uses `SideStep.gain` (`true-sides-test.py:368`).
  - The self-test checks a steady pan only (`live-analysis-selftest.py:124`).
- **Fix:**
  - Use phase 2's rule per side band: the band luma over its median across the kept frames, through the soft knee.
  - Or at least normalise `light` by its median over the kept frames, and count factors the same way across PATCH and GEN steps.
  - Add a fade-in and a flash case to the self-test.

### H2. Exit strips are packed as bounding boxes, so a page fills after a few drawings

- **Where:**
  - `structure.py:109-124`: one patch is the bounding box of every texel that left the picture.
  - `structure.py:189-190`: `pack` stops at the first patch that does not fit.
  - `live-worker.py:179,190`.
- **Failure:**
  - A diagonal pan, roll, zoom or shake makes texels leave on two or more sides, so the box is the whole previous picture: about 959x539 texels, 2 MB.
  - 7–8 such patches fill the 16 MB slot, and every later strip of the shot is dropped.
- **Evidence from the live cache:**
  - 65 of the current key's 312 pages are at the 16 MiB − 64 KiB cap.
  - Shot 2656-24 (a diagonal whip pan):
    - 7 patches of 959x539 texels, frames 2658–2664.
    - Per frame, `i0` steps from −227 to −636 texels and `j0` grows by 18 → 42.
    - The page is full after that, so the last 15 frames get nothing.
  - Shot 4738-65 (a slow roll):
    - 8 patches, frames 4739–4746, most of them 959–960 x 531–546 texels.
    - `H.npy` shows `h01` going from −0.006 to −0.022.
    - About 56 frames get no strips.
- **Fix:**
  - Split the strip mask into per-side rectangles (or row runs, or connected components) before packing.
  - Drop strips thinner than about 1 texel.
  - Cap the area of one patch.

### H3. Pages stay in the worker's RAM with no budget, and the warm start loads them all

- **Where:**
  - `worker_state.py:281-291`: each shot's `PageVersion` holds its `structure.Page`, texture included, and is never released.
  - `worker_state.py:345-358`: pages are written to the heap from RAM.
  - `live-worker.py:274-277`: the warm start loads every cached page; it holds `state.lock` per set (`:268`).
  - `side_cache.py:131-140`.
- **Evidence:** the patches folder held 1.77 GB for 322 pages at about 02:45, about 5.5 MB per shot.
- **Failure:**
  - One JJK episode (679 shots) means about 3.7 GB of page textures in the worker, plus the 768 MB canvas cache.
  - A warm start reads the same amount from disk.
  - The disk cache grows by about 3.7 GB per episode; the previous review measured about 0.5 GB.
- **Fix:**
  - Keep only metadata in `PageVersion`, and load the npz in `_write_page` through an LRU, like `_pixels_of`.
  - Consider `np.savez_compressed`: most texels in the boxes have alpha 0.
  - Fixing H2 makes pages smaller, but the RAM they take would still grow without bound.

### H4. Two monitors on different frames force full re-applies of the patches (likely; size not measured)

- **Where:**
  - `perf1-ai-sides-patches.c:114-122`: when `f < applied`, the accumulate texture is cleared and the page is applied again from its start.
  - It is called per monitor, with that monitor's own frame (`perf1-ai-sides.c:422, :446`).
  - Showings are per epoch (`:230-236, :438`), so a seek never reaches this branch. Only two monitors showing different frames of one shot do.
- **Mechanism:**
  - Each monitor is rendered when its swapchain is ready (`perf1-render.c:291-322`), about one refresh before its own vsync.
  - So at a film-frame boundary, the 96 Hz screen's refresh for frame f+1 (rendered about 10 ms ahead) often comes before the 240 Hz screen's last refresh of frame f (about 4 ms ahead).
  - My rough estimate: about 40 % of frame boundaries on 240 + 96 Hz.
- **Failure:**
  - Each such inversion costs one draw call per persistent patch applied so far, about 10 times a second.
  - That breaks "the player's patch work per film frame stays well under 1 ms".
  - It gets worse once H2 is fixed (more, smaller patches).
- **Fix:**
  - Never go backward inside a showing: `if (f <= sh->applied) return true;`.
  - A strip drawn one frame early is hidden: it lies at least 1 px inside the lagging monitor's picture.
  - On the first two-screen run, check `patches.replays` and `frame_cpu_ms` in `ai-sides.json`.

## Medium

### M1. Too few texture pairs: patches appear mid-shot, against the cut-only rule

- **Where:**
  - `perf1-ai-sides-internal.h:14,17`: 2 texture pairs; a pair stays pinned until 1 s after its shot was last drawn (`AI_PIN_S`).
  - `patches.c:38-46`: a pair counts as idle only once its showing has not been drawn for 1 s.
  - `patches.c:107-113`: a failed take is retried every refresh, and a later success applies everything up to the current frame.
- **Failure:**
  - Three shots in a row have pages, and the middle one is under 1 s.
  - The third finds both pairs taken, so it shows the canvas alone.
  - A fraction of a second later it gets a pair, and all its strips pop in mid-shot.
- **Side effects:**
  - The tier stays STRUCTURE, and `tier_film_s[3]` and `pages_shown` count it (`perf1-ai-sides.c:253-255, :445-450`).
  - A failed allocation is retried every refresh (`patches.c:51-64`).
- **Fix:**
  - Decide the pair at the showing's first refresh; if none is free, set `sh->page = -1` for the whole showing.
  - Hold a pair only about 0.1 s (enough for a lagging monitor), or raise `AI_ACC_MAX` to 3–4.
  - Remember allocation failures.

### M2. Pages compete with canvases for the 12 GPU slots, and win for nearer shots

- **Where:**
  - `upload.c:273-297`: shot k's page is copied before shot k+1's canvas.
  - `upload.c:90-97`: pages are pinned like canvases.
  - `perf1-ai-sides.c:16`: `gpu_canvases` is 12.
- **Failure:**
  - In dense cutting, the pinned canvas-and-page pairs of the last second plus the pages ahead fill the slots, so the upload walk stops.
  - A 16 MB page (H2) takes 64 chunks.
  - Shot k+1's canvas misses its start, so the whole shot shows glow where phase 2 showed T1 or T2.
- **Fix:** copy the canvases for the whole horizon first, then pages; or give pages a small pool of their own.

### M3. A page the C reader rejects stalls the upload walk

- **Where:**
  - `upload.c:287-294`: the walk returns whether or not `start_copy` succeeded.
  - `upload.c:205-208`: `forget(g)` runs before `ai_store_read_page`, and a failure is counted as torn.
  - Limits differ: the C reader allows a grid up to 16384 texels (`store.c:221-223`), the worker up to 65535 (`structure.py:206`).
- **Failure:**
  - A grid wider than 16384 texels, or any other persistent check failure, is found again every 50 ms.
  - Each attempt empties one GPU slot.
  - No canvas for any later shot is copied until that shot is on screen.
- **Fix:**
  - On failure, remember (slot, gen) as bad and continue the walk.
  - Use the same limit on both ends, ideally a VRAM cap, since the accumulate and overlay textures are allocated at grid size.

### M4. The camera map's projective divisor is never bounded once steps are chained

- **Where:**
  - `camera_model.py:144-147` checks `DENOM` only for one step's leftover map.
  - `camera_model.py:242` composes that onto the anchor's map.
  - `mosaic_t1.py:74-79`, `structure.py:66-67` and `set_atlas.py:176-179` divide by d with no sign check.
- **Failure:**
  - Projective terms build up over anchor hops on a long pan or roll, until d ≤ 0 somewhere over the side area.
  - Then `int(np.ceil(inf))` raises `OverflowError` in `on_shot`. The analyser logs it as a decode failure, skips 1 s, comes back later and fails again.
  - Or the canvas scale balloons (a blurry T1), silently.
  - The plan's risk table claims the divisor is bounded.
- **Fix:**
  - After composing, test d over the corners of the side area.
  - Fall back to the translation or hold step when min d < 1/`DENOM_MAX`.
  - Guard the divides.

### M5. The cache keys tie mosaics and plates to the planner's cadence

- **Where:**
  - `analyse_v0.py:39-40`: `ANALYSIS_VERSION` includes `Cadence().key()` and `strips-s2`.
  - `side_cache.py:98-99, :157-158`: the mosaic key is built from the analysis key, and the plate key from the mosaic key.
- **Failure:**
  - The user's X1c verdict changes τ, so every cached analysis, T1 and painted plate is made again: about 15–20 s of GPU per plate.
  - Mosaics depend only on the camera path.
- **Fix:** key mosaics and plates on the camera version; give the plan and strips (states, gains, pages) a key of their own.

## Low

- **L1. Mixed alpha conventions in the composite.**
  - `finish-surround-ai.c:68,71` treats the straight-alpha canvas as premultiplied.
  - The colour is wrong where the accumulate's alpha is fractional (bilinear sampling at strip edges) over canvas alpha below 1: a 1-texel fringe.
  - Fix: premultiply first, un-premultiply at the end.
- **L2. A crash between two writes loses a shot's patches for good.**
  - `live-worker.py:189-191` saves the analysis before the page.
  - The warm start (`:274-277`) publishes no page when the file is missing, and `is_done` stops a re-analysis.
  - Fix: write the page first, so the analysis file marks the shot complete.
- **L3. `identity-refs.py` (not run yet: the cache has no `identity/` folder):**
  - `:55-58,66,172` hard-code 1920x1080 frames: any other size reads misaligned frames and gives garbage with no error. JJK is 1080p.
  - `:187-192`: `first_of` takes each track's earliest box, not its best (the docstring says best).
  - `:56` uses `-vsync 0`, which is deprecated; the repo uses `-fps_mode passthrough` (`frame_reader.py:37`).
  - `:59` and `:192` crash on empty input (`max(set())`, `np.stack([])`), which also happens when ffmpeg fails.
  - `:75` downscales with cv2 bilinear; imgutils uses PIL's antialiased bilinear, so the embeddings drift from the model card's eps. INTER_AREA is closer.
  - `cut_by_seam` is keyed by content-model spans (`:256`), but `painter_jobs.IdentityRefs.for_spans` (`:61-62`) gets the worker's spans, so shots split at a hidden cut never match.
- **L4. `true-sides-test.py`:**
  - `:633`: `cc[0]` raises IndexError when no shot qualifies.
  - `:604`: a lambda assignment (ruff E731).
  - `boil` (`:416`) is a difference in codes, but the success criterion (`phase-04:142`) is a ratio, so the output cannot answer it.
- **L5. Heap order changed.**
  - `worker_state.py:298-305` puts each shot's page right after its canvas within the same `heap_slots - 2` budget.
  - Each page takes a full 16 MB slot, so busy stretches keep canvases for a shorter horizon than phase 2/3 did.
- **L6. Diagnostics and dead code:**
  - `perf1-ai-sides-record.c:159-163` counts pages in `resident_n`; the layout doc says "canvases".
  - `draw_patch` failures are ignored while `applied` still advances (`patches.c:124-139`).
  - `NEW_DRAWING_DIFF` is unused (`analyse_v0.py:45`).
  - The overlay texture is allocated at grid size (`patches.c:58`), though nothing makes overlays yet: twice the VRAM per pair.
- **L7. Textures created at a cut.**
  - Texture pairs are created or grown on the render thread at a showing's first frame (`patches.c:51-66`).
  - The warm-up makes only 256x256, so the first real page allocates two render targets at a cut, mid-film.

## Verified OK

### (a) Correctness

- **Camera maps reach the shader in the right units:**
  - Flow px map to decoded px with the crop origin (`camera_model.py:178`), and `source_map` (`:191-194`) converts to continuous source px.
  - The shot tracker feeds whole-frame decoded px (`analyse_v0.py:152-154,172-173`).
  - `h8` divides by `m22`.
  - The player adds the 1 (`perf1-ai-sides.c:327`) and maps world px to texel coordinates where texel i spans [i, i+1) (`:332`).
  - The shader samples t / size and guards `q.z > 1e-6` (`finish-surround-ai.c:55-61`).
- **Patch lattice to shader:**
  - `lattice_map` (`structure.py:41-43`) and `grid_map` (`:153-157`) use the edges convention.
  - `store_table` writes destinations relative to the grid (`:165`).
  - `texelFetch(floor(fragcoord + src - dst))` maps each texel to its source (`patches.c:14,79`).
  - The shader samples the grid at g / texture size (`finish-surround-ai.c:63-68`).
  - The strip content is warped through lattice ∘ W_prev ∘ pixel map (`structure.py:115-118`).
- **Sets:**
  - The frame transform is T∘M (`set_atlas.py:182-186`), and the grid map is G∘T⁻¹ (`structure.py:157`, `worker_state.py:289-291`), so grid map ∘ h = G∘W.
  - `publish_page` uses the same set membership as `publish_analysis` (`live-worker.py:181-185, 269-276`).
- **Writes into the mmap land:**
  - Frame rows are written through slice views, and canvas records through `np.void` views.
  - `finish_page` writes through `np.frombuffer` of a writable memoryview (`side_store.py:297-305`).
  - The round-trip self-test covers a page (`live-store-selftest.py:104-199`).
- **Page counter rules:**
  - Worker: the counter goes odd, then header, table and texels are written, then the record, then the counter goes even (`side_store.py:294-316`).
  - Player: the record is copied under the counter; `ai_store_read_page` copies the header and table and checks the counter again (`store.c:219-221`); `copy_rows` reads from `page_header_bytes` and checks after every chunk (`upload.c:172-186`).
- **Bounds:** the C reader checks the magic, n, the shot, the grid size, a finite map, every destination and source rectangle, `until`, the table size against the header, and header plus pixels against the slot (`store.c:208-232`).
- **Order and lifetimes:**
  - Patches apply in table order, which is film-frame order, as `structure.replay` does.
  - `forget()` frees the patch table.
  - A recycled showing releases its texture pair (`perf1-ai-sides.c:248`), and a stolen pair is cleared from the old showing (`patches.c:48-49`).
  - A replaced page is caught by the complete/kind/shot check (`:102-105`), and a page on screen is pinned.
  - The overlay texture is cleared correctly (`:129-138`).

### (b) No regression, apart from H1, M2 and L5

- Shots without a page take the old path: `no_patches`, `aon` 0, and the canvas sample (q.z is 1 for a translation).
- Canvas selection ignores pages (`upload.c:47,73,137`; `live-fake-player.py:51-54`).
- Phase 4 touched only `perf1-ai-sides*`, `perf1-finish-surround-ai.c`, the self-test and the compile script (modified 01:45–01:58). The shared player files were last changed on 2026-10-13 between 21:06 and 21:12.
- The set registry, `canvas_geometry` and `frame_transforms` all go through `an.maps()`.
- The warm start's "none" pages tell "no patches" apart from "missing".

### (c) Contracts

- **Intentional, schema 2:**
  - Frame record of 96 B with `h[8]` and `state[4]`.
  - The canvas record gains `kind`, `shot`, `page_header_bytes` and `n_patches`.
  - Patch page and patch records; the static asserts are updated.
- **Silent changes:**
  1. The meaning of `gain` (H1).
  2. `showing_tier` is 3 when a page shows, and `resident_n` counts pages.
  3. New fields: `page` in `ai-showings.jsonl` and `patches` in `ai-sides.json` (additive).
  4. Every phase-2/3 cached analysis, mosaic, T1 and plate is invalidated, and the new `patches/` folder takes about 5.5 MB per shot.
  5. A page's canvas record has tier 3, so any reader that does not filter on `kind` sees a "structure canvas". Every reader in the repo filters.

### (d) Patterns

Naming, comment density and conventions match the neighbouring code. Only nits: E731 and one unused constant.

### (e) Code not yet exercised

- `analyse_v0` uses no removed constant.
- Every `ShotAnalysis(...)` call uses keywords.
- The worker's set paths are consistent.
- The runtime risks are in L3 (`identity-refs.py`) and L4 (`x1c_metrics`).

## Recommended actions

1. **H1 first, before any screen run:** gains on the median reference with symmetric counting, plus a fade/flash test.
2. **H2 and H3:** split strips into rectangles; stream pages from disk through an LRU; compress the npz.
3. **H4:** apply patches forward only. **M1:** decide the texture pair at the showing's start.
4. **M2:** copy canvases before pages. **M3:** continue past a bad page, and use one limit on both ends.
5. **M5:** split the cache keys before the user's X1c verdict changes τ.
6. **M4:** guard the composed map's divisor.
7. The Low items.

## Metrics

- **Tests:** not run (the caller allowed only reads and grep). Gaps:
  - No test of gains under brightness changes.
  - No concurrency stress for pages: `FakeWorker` writes canvases only (`live-store-selftest.py:216-257`).
  - No negative tests for `ai_store_read_page`.
  - The GPU patch path is checked only by a screen run.
  - `identity-refs.py` and `x1c_metrics` have no tests.
- **Lint and type checks:** not run.

## Plan follow-ups (phase 4)

- **Steps 1–4:** the code exists (camera model, planner, structure, X1c).
- **Step 5:** schema 2 is done on both ends; the round-trip test includes a page.
- **Step 6:** `identity-refs.py` exists but has not run.
  - `live-structure-selftest.py` and `live/identity_refs.py` (listed under Create) do not exist.
  - The structure checks are in `live-analysis-selftest.py`, and the identity lookup is in `painter_jobs.IdentityRefs`.
- **Steps 7–8:** pending.
- **Criteria blocked:** H1 blocks "the sides are never brighter or faster-flashing than the picture"; L4 blocks the boil criterion as written.

## Unresolved questions

1. Gain reference: the median over the shot (plan, `layout.json`) or "since the shot began" (`__init__.py`, the code)? This decides H1's fix.
2. Should what leaves the picture during a roll or zoom (thin slivers around the edge) become patches at all, or be left to the base canvas and GEN?
3. Do the two monitors really render out of order? Check `patches.replays` in the first two-screen `ai-sides.json`.
4. Should a whole-episode warm start hold pages in RAM, or stream them from disk like canvases?
5. Outside phase 4: a set's canvas takes the set's earliest member start as `valid_from_s`, so `gpu_place` (`upload.c:111`) treats a reused set's canvas as "behind" the playhead and evicts it first. Is that known?
