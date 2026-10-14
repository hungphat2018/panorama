# Code review: phase 4 fixes after 02:40 (AI surround live path)

Read-only. No source file edited. Run: file reads, grep, git status, ls/du/df, `unzip -p | od` on the worker cache, `ruff check --no-cache --isolated`, `compile()` syntax checks, the four self-tests (CPU), two scratch probes (cache key hashes; a mosaic-gate probe). No build, no GPU, no worker, no player.

## Scope

- **C** (`spikes/performer-p1/src/`): `perf1-ai-sides-internal.h` (03:44), `perf1-ai-sides.c` (03:44), `perf1-ai-sides-upload.c` (03:44), `perf1-ai-sides-patches.c` (03:28), `perf1-finish-surround-ai.c` (03:27), `perf1-ai-sides-record.c` (02:53). `bin/perf1-p3-ai4.exe` was built 03:45:20, after the last C edit.
- **Python** (`spikes/ai-surround/`): `live/analyse_v0.py` (03:59), `live/structure.py` (03:59), `live/side_cache.py` (04:14), `live/mosaic_t1.py` (04:54), `live-worker.py` (04:33), `identity-refs.py` (05:03), `true-sides-test.py` (02:51), `live-analysis-selftest.py` (04:52). Also checked for the earlier fixes: `step_planner.py` (02:42), `worker_state.py` (02:44), `camera_model.py` (04:17), `side-store.layout.json` and `__init__.py` (02:42).
- **Files that changed during this review.** Findings refer to the versions listed above.
  - `mosaic_t1.py` 04:31 → 04:54. It is now v0.7: a per-region gate (Gaussian σ 12 texels, ≥ 25 % of the multi-seen texels disagree, density ≥ 0.1), and inside the first picture it holds frame 0 itself. The per-shot gate (SHOT_UNSURE 0.10) in the brief no longer exists.
  - The self-test's reliability check changed at 04:52.
  - `identity-refs.py` gained `--same` and SAME 0.14 at 04:58–05:03.
- **Real-data evidence:**
  - Cache `out/live/cache/6bc0…`. A probe confirmed that the current code's keys for a 1920x1080 crop equal those of the 03:59–04:09 run: geometry `bedc3a52578b1209`, analysis `6050480b07b086d8`.
  - Smoke runs `out/live/smoke/20261014-0328…`, `-0345…` and `-0358…`.

## Overall

- **Player side:** solid.
  - The double-binding fix is complete, and the screen run had an empty stderr.
  - H4 (forward-only) is confirmed by data: 14,189 "behind" refreshes, with 0 patch failures.
  - The premultiplied composite is correct.
- **Worker side:** the earlier H2 and H3 are only half fixed.
  - The strips are now thin, but the shelf packer wastes 95 % of every page, so a third of the pages still hit the 16 MiB cap.
  - Pages still sit in RAM until their first heap write.
  - Tonight's key changes orphaned about 6 GB of cache on a disk that is 99 % full. One run already died of ENOSPC.
- **New mosaic gate (v0.7):** disagreement inside the picture bleeds over the seam.

## Earlier findings: status

| Earlier | Status | Evidence |
|---|---|---|
| H1 gains | **Fixed** | `gains4` is no longer produced. The rule is v0's luma over the median of the lit kept frames (`analyse_v0.py:97-110`). The cached analyses have no `gains4`. The fade test passes. *Plan drift:* phase-04:92 asks for per-side gains, but one value is used. |
| H2 page fill | **Partly** | The per-edge split and the 25 % cap work. The shelf packing is the new bottleneck: see **H1** below. |
| H3 RAM/disk | **Partly** | Disk is fixed (398 pages take 52 MB compressed; the old key took 3.76 GB for 617). RAM is not: **H2**. Stale files: **H3**. |
| H4 re-applies | **Fixed** | `patches.c:135-138`. In the 03:28 run: `behind` 14,189, `failed` 0, patch CPU p99 0.025 ms. |
| M1 pairs | **Fixed** | The pair is decided at the first refresh, with 3 pairs and a 0.25 s pin. The log still over-counts: **L3**. |
| M2 canvases vs pages | **Fixed** as suggested (two passes). By design, pages starve when the GPU is full of canvases. |
| M3 bad page | **Fixed** (a ring of 8, and the grid limit is 16384 on both ends). Residual issues: **L4**. |
| M4 divisor | **Fixed**: `camera_model._tame` (`:268-279`) checks the composed map over the side reach. The version string is not marked: **L12**. |
| M5 keys | **Partly**: the cadence is split out, but the strips and gain versions still key mosaics and plates: **M1**. |
| L1 alpha | **Fixed**. `pl_alpha_overlay` (gpu.c:962) leaves the accumulate texture premultiplied; `ai_sample` premultiplies the canvas and un-premultiplies the result. |
| L2 write order | **Fixed** (`live-worker.py:196-197`). |
| L3 identity-refs | **Fixed** (frame size, fps_mode, best box, guards, PIL bilinear; `painter_jobs.for_spans` matches by overlap). Residual issues: **L8**. |
| L4 x1c | **Fixed** (ratio, guard, def). Residual: **L7**, and Q2. |
| L5 heap order | **Addressed**: pages are kept only within PAGE_HORIZON_S (30 s). |
| L6 diagnostics | **Fixed**: `resident_n` counts canvases, failures are counted, NEW_DRAWING_DIFF is gone. The overlay texture is still allocated (accepted). |
| L7 cut-time textures | **Mostly fixed**: 3 pairs of 2048x1024 are made at warm-up. Residual: **L2**. |

## Critical

None.

## High

### H1. `pack()` leaves 95 % of a page empty, so a third of the pages still fill and drop their later strips

- **Where:**
  - `live/structure.py:200-228`: a shelf packer in time order, where a shelf is as tall as its tallest patch.
  - The left and right strips are tall: up to about 540 texels high and 1–30 wide.
  - The top and bottom strips are wide: about 960 wide and 5–90 high.
  - Mixed on one shelf, every wide strip pays for a 540-row shelf.
- **Evidence** (the 398 current-key pages):
  - **Fill:** the textures hold 931 M texels and the strip boxes 48.6 M, so the fill is **5.2 %**. 303 pages are under 10 % fill.
  - **Pages at the cap:** 130 pages (33 %) are within 5 % of the 16 MiB − 64 KiB cap.
    - They hold 17 patches on average.
    - Their last strip comes 23 of 43 frames before the shot ends; uncapped pages: 13.6 of 40.
  - **Shot 15418-14:** 9 strips, 0.59 MB of boxes, in a 3900x553 texture (8.6 MB).
  - **Shot 4738-65 (the roll):** a full page by frame 4766; its last 36 frames get no strips. Its boxes total 3.5 MB.
  - **With perfect packing:** p50 0.31 MB, p90 1.06 MB; no page would come near the cap.
- **Cost beyond the dropped strips:**
  - Mean page texture: 8.9 MiB uncompressed.
  - Each page takes a 16 MB heap slot, a GPU texture, and 30–64 upload chunks.
  - The 03:28 run copied 3.28 GB in 10 minutes, against 1.31 GB without patches (03:58).
  - The pages also drive **H2**.
- **Fix:**
  1. Pick the patches in time order by box area, up to about 0.8 of the budget.
  2. Place them tallest first (NFDH), or use separate shelf streams for tall and wide strips.
  3. Re-sort `entries` back to time order: both the player (`patches.c:142-153`) and `replay` apply patches in table order.
  4. If the result overflows, drop the latest patch and pack again.
  5. Do **M1** first, so that the strips version bump does not re-make every mosaic and plate.

### H2. Page textures stay in the worker's RAM until their first heap write, and leak when that never comes

- **Where:**
  - `live-worker.py:185-191, :201` passes `page=page`.
  - `worker_state.py:297-298` stores it, and only `_write_page` (`:366`) releases it.
  - `ensure_heap` writes a page only when `start_s <= playhead + PAGE_HORIZON_S` (30 s, `:310`).
  - The analyser runs up to `--lookahead-s` 600 s ahead.
- **Failure:**
  - **Steady state:** the worker holds every analysed page between +30 s and the analyser's lead.
    - That is about 0.46 pages per film second (398 pages over 866 s) × 8.9 MiB.
    - It reaches about **2 GB** once the analyser is a few minutes ahead.
  - **Leak:** pages of shots skipped by a forward seek, or cut by the `heap_slots - 2` budget, are never written, so they are never released.
  - **Never released either:** a page whose write raised `ValueError` (`:368-374`).
  - The docstring at `worker_state.py:55-56` ("a whole episode's pages would not sit in RAM") overstates the fix.
- **Fix:**
  - Do not keep `page`: `save_page` has already written it, so pass `page=None` in `on_shot`.
  - Or keep it only for shots within PAGE_HORIZON_S of the playhead at publish time, and drop `pv.page` for pages behind the playhead.
  - Ideally load from disk outside `state.lock`.
  - **H1** shrinks all of this by about 15x.

### H3. Tonight's key changes orphaned about 6 GB of cache on a nearly full disk; a run already died of ENOSPC

- **Evidence:**
  - `df`: C: has 6.2 GB free (99 % used).
  - The cache is 6.3 GB. Under the current code, these are unreadable:
    - `patches/*-f10ca9970336d184|744ac9c6a7ec7f76|f48daa5e4d756b23` (3.86 GB, uncompressed pages from before the fix);
    - `mosaic/*-1f53e77a…|03d220f1…` (0.41 GB; v0.7's key for this room is `95964a3a28e505f2`);
    - the T1 canvases keyed on those mosaics (about 1.8 GB in `canvas/`).
  - The 03:45 smoke run's worker logged `OSError(28, 'No space left on device')`, and perf1 could not write `perf1-cuelog.json`. That run has no `ai-sides.json`.
- **Why:**
  - `side_cache.py` never deletes a stale key.
  - Each version bump (GEOMETRY_VERSION 03:59, MOSAIC_VERSION 04:31 and 04:54) re-makes everything under new names.
  - The next bump (**H1**) adds another 0.5–1 GB.
- **Fix:**
  - **Now (user's call):** delete the stale keys listed above.
  - **Keep** the 20 T2 plates (`canvas/*-t2-*`, 40 files) until it is decided whether any of them is still reachable.
  - **Then:** add a cache GC at worker start that drops analysis, patch and mosaic files whose key suffix is not current, plus canvases no current key references.

## Medium

### M1. GEOMETRY_VERSION keys mosaics, set features, T1 canvases and plates, but carries the strips and gain versions

- **Where:**
  - `analyse_v0.py:46-47` (`…|strips-s3-by-edge-cap25|gain-v1-lit-vote`).
  - `side_cache.py:104-105, :174-175, :215-216`.
  - `worker_sets.py:239, :305` (T1 and plate keys over `mosaic_key`).
- **Failure:**
  - Any strip or gain tweak re-registers sets, re-makes every mosaic and T1, and orphans every painted plate (about 15–20 s of GPU each).
  - Mosaics do not depend on the strips. The vote rule is already in MOSAIC_VERSION ("lit-vote").
  - The packing fix (**H1**) needs a page-version bump, which would trigger all of this again.
  - The previous M5 is only half done.
- **Fix:** three keys:
  - `CAMERA_VERSION` (decode, camera, kept frames): features, mosaics, plates;
  - `PAGE_VERSION = CAMERA_VERSION + strips + gain rule`: `patches/`;
  - `ANALYSIS_VERSION = PAGE_VERSION + cadence`.

### M2. The v0.7 region gate counts disagreement inside the picture, so it drops real pixels right outside the seam

- **Where:** `mosaic_t1.py:197-199` (04:54): `split` and `multi` are blurred over the whole canvas, inside texels included, and the result is applied to `~inside`.
- **Probe** (scratch; a synthetic exact pan of 9 lit frames, 40 px per frame; an animated band touching the inside of frame 0's right edge):
  - Outside, `disagree` is 0.000.
  - Yet **33 % of the 24-texel ring just outside the edge becomes `unsure`**, and the side share drops from 0.344 to 0.331.
  - With a 20 px gap between the band and the edge: 0 %.
- **Failure:**
  - A character or effect animating at the picture's edge during a pan removes the real background texels right next to it.
  - T1 then shows a feathered gap of about 8 + 12 texels (about 40 source px), glow or T2, exactly at the seam.
- **Fix:**
  - Use normalized convolution over the outside only: `w = blur(~inside)`, `split_r = blur(split & ~inside) / max(w, ε)`, `multi_r = blur(multi & ~inside) / max(w, ε)`.
  - Add the probe's case (an animated spot at the edge, an exact background) to `live-analysis-selftest.py`.

## Low

- **L1. A NULL stand-in breaks every fused pass.**
  - `perf1-ai-sides.c:91-96` leaves a stand-in NULL if `rgba16f` is missing or `pl_tex_create` fails, and `perf1-finish-surround-ai.c:163-164, :185` binds it anyway.
  - `pl_pass_run` then fails `require(db.object)` (gpu.c:1117) on every pass-2/3 refresh without patches: no picture, and a stack trace each time.
  - Fix: in `ai_fill_append`, if any stand-in is NULL, return true without the fill and log once.
- **L2. Textures shrink on one axis, so they are re-created at cuts.**
  - `patches.c:40-60` (`make_pair`) and `upload.c:233-248` re-create the texture at `bucket(w) x bucket(h)` when either axis is short, which shrinks the other axis.
  - Pages (up to 4096 x ≤ 1024) and canvases (about 2000 x 600–1100) now share GPU slots, so slots flip shape and textures are created on the render thread at cuts.
  - Fix: `max(bucket(n), current)` per axis, accepting more VRAM, or give pages slots of their own.
- **L3. The showing log counts T3 that never showed.**
  - `perf1-ai-sides.c:252-263` logs `tier:3, page:true` and counts `pages_shown` before the first `ai_patches_to_frame`.
  - With no free pair, the showing is T1 or T2, but `ai-showings.jsonl` and `live-smoke` `by_tier` say T3.
  - Fix: log after the first refresh, or log a correction.
- **L4. Residual rejected-page issues** (`upload.c`):
  1. A ring of 8 (`:19-21`). With more than 8 bad pages in the horizon (a systematic reader/worker mismatch), the ring cycles.
  2. `start_copy` calls `forget()` on the victim before `ai_store_read_page` (`:228-232`), so each retry also evicts one GPU canvas.
  3. A texture allocation failure (`:240-245`) also rejects the page for good (`:327-328`).
  - Fix: per heap slot `bad_gen[slot]`; read the header before `forget`; reject only read failures.
- **L5. Stale docs and names:**
  - `perf1-ai-sides-internal.h:165-167` still says a frame before `applied` "starts again from empty".
  - `upload.c:7-10` describes the interleaved order.
  - The counter `page_resets` is now emitted as `"behind"`.
  - The ai-sides.json schema string is still `/1`, although a key was renamed.
  - `side_cache.py:7-8, :190`: the mosaic JSON fields list lacks `disagree`.
  - `side-store.layout.json:99`: "median over the shot's kept frames"; the code uses *lit* kept frames.
  - The `mosaic_t1.py:15-16` docstring says "T1 fills it", but unsure texels get alpha 0 (glow shows).
  - `set-atlas-cuts-check.py:86` (another session's file) describes the v0.5 per-shot gate.
- **L6. `kept_offsets` returns `[]` after `an.kept.clear()`.**
  - On a fresh analysis (`live-worker.py:212`; `kept_index` is None), `voting_offsets()` then falls back to `range(n)`, a different gain reference from the one the strips were normalised with.
  - No caller does this today.
  - Fix: set `kept_index` in `ShotTracker.finish()`.
- **L7. x1c:**
  - X1c still multiplies the carried sides by the planner's per-side drift gain (`true-sides-test.py:370`), which the live path no longer publishes. Its clips and metrics model another lighting rule than T3.
  - `boil` is 1.0 when no step is held (`:423`): it reads as a pass and pulls the summary mean toward 1.0. Use None (nanmean) and report the held count.
- **L8. identity-refs:**
  - The embeddings recipe (`identity-refs.py`, `recipe = …`) has no fingerprint of `subjects.json` and no rank-rule tag. After a content-model regeneration, the cached (track, box) indices point to other tracks: wrong `uncut`, heads and `cut_by_seam`, or an IndexError.
  - `give_up` returns rc 0 even when ffmpeg failed.
  - `shutil.which` returning None raises a TypeError.
  - Fix: add the size and mtime (or a sha1) of `subjects.json` to the recipe; return 2 for the ffmpeg case.
- **L9. The strip cap drops only one edge.** `structure.py:128-132` skips only the edge over MAX_STRIP_SHARE. The same drawing's other strips (from the same wrong map or missed cut) are kept. Fix: skip the drawing.
- **L10. A warm-up failure stays in the counters.** `patches.c:177-178` resets `patches_applied` after the warm-up draw but not `patch_failures`.
- **L11. Lint:**
  - New: F401 `dataclasses.field` is unused (`step_planner.py:33`, since the H1 fix).
  - B007 twice in `identity-refs.py` (`j`, `rank` loop variables).
  - Already there: B008 `step_planner.py:107`; B904 `live-worker.py:97`.
  - Syntax: all 11 files compile.
- **L12. The camera version is not marked for the tame check.**
  - `camera_model.VERSION` (`:72-73`) has no TAME marker.
  - `camera_model.py` changed at 04:17, after the 03:59–04:09 run whose analyses and pages share today's keys.
  - If that edit changed behaviour, 589 stale analyses and pages are served (see Q1).

## Answers to the C checks

- **Double binding: none left.**
  - **Fill** (`perf1-finish-surround-ai.c:183-186`): `ai_tex` is the GPU canvas (warm-up: STAND_CANVAS); `ai_acc_tex` is `a->acc` or STAND_ACC; `ai_ovl_tex` is `a->ovl` or STAND_OVL; `ai_seam_tex` is `seam_tex[i]` or STAND_SEAM.
  - **The pass's own textures** are `cv_canvas` (pass-1 output), `sr_lvl_*` and finish resources. None is an AI texture.
  - **Seam raw** (`:232`): `sm_src` = `front->lin` plus SAMPLE_DESCS, rendered into `seam_raw`, which is not bound. **Seam blur:** `sb_raw` into `seam_tex[slot]`.
  - **draw_patch:** `pg->tex` into `a->acc` or `a->ovl`. At warm-up: `acc[0].ovl` into `acc[0].acc`.
  - **`fi->tex` has no fallback**, but it is non-NULL whenever the fill runs: `on = g->tex != NULL`, and the warm-up checks STAND_CANVAS.
  - **Data:** the 03:28 run (stand-in build) has an empty stderr.
- **Stand-in lifetime:** created once in `init_monitor`, destroyed in `ai_seam_destroy` (from `ai_sides_destroy`), with no re-init path. Their format (rgba16f) gives the same shader variant as rgba8. NULL handling: L1.
- **Pair pin:**
  - `last_drawn` is per showing and set by *any* monitor's refresh (`perf1-ai-sides.c:444`, before the patches call).
  - So a showing that a lagging monitor still draws is never idle.
  - A pair can be lost only if every monitor drawing that showing stops for more than 0.25 s (an occluded window, a stall). The showing then falls back to the canvas alone; there is no wrong frame.
  - Three flash cuts each under 0.25 s leave the fourth shot without a pair: the canvas alone, by design.
- **Rejected list:**
  - Keyed by (heap slot, gen). A torn read rejects an old gen, which is harmless.
  - `drop_all` clears it on open, close, uid change and destroy.
  - Store restarts get a new uid, so a reused gen cannot carry a stale rejection.
  - Residual issues: L4.
- **Two-pass walk:** correct, and skipped under `--ai-no-patches`. When the GPU is full, a page can still displace the farthest canvas, and in dense cutting pages starve. Both follow from the design.

## Answers to the Python checks

- **Voting on all-dark shots:** p75 is about 0, so all frames vote. With ref ≤ 1e-4, the gains are 1.
- **One kept frame:** it votes alone. `k = 1` gives no multi-seen texels, so no gate.
- **Analysis loaded from the cache:** `kept_index` is used, which matches the fresh run, so the gains are reproducible. `build()` would raise on an empty `kept`, but no caller builds from a cached analysis.
- **inf/nan in the agreement:** unseen is inf. `inf - finite` never agrees; `inf - inf` is NaN, which is not ≤ 15, and texels with count 0 are never `split` (count ≥ 2 is required). `errstate` covers the warning.
- **`inside`:** the world is frame 0's source px (map 0 is the identity), so `(x0 - wx0) / scale` places it correctly at any canvas origin. `canvas_geometry` includes frame 0, so `wx0 ≤ x0`.
- **Callers of `Mosaic`** (another session's files; report only): all compatible through the defaults.
  - `set_atlas.atlas` and `agreement`, `worker_sets` (plate and banded mosaics, `atlas`), `painter.paint` and `set-atlas-cuts-check` use `coverage` and `side_share` only.
  - Unsure texels are not covered, so the painter repaints them.
- **Cache files:** old uncompressed pages load with `np.load`, but none is reachable under the new keys. The mosaic alpha 64 round-trips (`> 127` covered); old 255/0 PNGs read the same.
- **Strip cap units:** lattice texels. The mask lives on the lattice (`lattice_map`, SCALE 2), and `most = 0.25 * crop area / SCALE²`.

## Contract changes (c)

- **Side-store schema 2:** no binary change tonight. `store.{c,h}` date from 01:45–01:46; `layout.json` (02:42) changed only the gain text.
- **Cache:**
  - The mosaic PNG alpha is 255 / 64 / 0.
  - The mosaic JSON gains `disagree` (read with a default of 0).
  - Pages are written with `savez_compressed` (both formats read).
  - Keys changed: GEOMETRY_VERSION (strips-s3, gain-v1) and MOSAIC_VERSION v0.7. Every earlier mosaic, T1, plate and page is orphaned (**H3**).
- **`ai-sides.json`:** new `patches.failed`; `patches.replays` is renamed `patches.behind`, and it now counts refreshes, not replays. The schema stays `/1`. Its only reader, `live-smoke.py:150`, copies the whole object.
- **CLI:** perf1 gains `--ai-no-patches`; identity-refs gains `--threads` and `--same` (SAME default 0.14).
- **Worker shot event:** gains `disagree`.
- **`Mosaic`:** new fields `unsure` and `disagree`, both with defaults.

## Patterns (d)

The comment density, naming and helper reuse match the neighbours. The stand-ins follow `perf1-picture-pass1.c`'s 1x1 dummy. Nits are in L5.

## Metrics

- **Self-tests:** all pass.
  - Analysis 18/18, run twice: before and after the 04:52 edit. The 24-px misregistration case gives 18 % disagreement, and the side share drops from 0.40 to 0.32 under v0.7.
  - Store 251/251; sets: all OK; cache 23/23.
- **Gaps:**
  - No test for the gate's bleed (M2), the packing fill (H1), the page RAM release (H2), or the strip cap.
  - No round-trip test for alpha 64 or `disagree` in `live-cache-selftest.py`.
- **Player data:**
  - 03:28 run, the pre-03:44 ai4 build, with patches:
    - 186 pages shown, 2,901 applied, 0 failed;
    - structure (T3) on screen for 363.5 s of the 600 s;
    - 3.28 GB copied.
  - On DISPLAY1:
    - wrong frames: 6.0/min with patches (03:28), 3.1/min without (03:58); 59 of the 60 came with a missed refresh;
    - missed refreshes: 14.2/min with patches, 21.9/min without.
  - One run each: no effect can be attributed to the patches yet.

## Recommended actions

1. Before the next worker run, free the disk (H3 list). Keep the T2 plates until decided.
2. Split the keys (M1) before touching `pack()`.
3. Fix `pack()` (H1): NFDH placement, the table kept in time order, a bump of only the page key.
4. Stop holding pages in RAM (H2).
5. Make the region gate count outside texels only (M2), and add the test.
6. Low items: L1 and L4 first, as cheap safety.

## Plan follow-ups (phase 4)

- **Step 6 has run** (04:57): 744 person tracks, 743 tall, 102 clusters (largest 46), 1,090 cut entries. The criterion "identity clusters and crops exist" looks met, pending the user's check of the format and contact sheets. SAME is now 0.14, not the model's 0.1785.
- **Step 7:** the current binary's T3 path (two-pass walk, rejected list, built 03:45) has not been on screen yet.
  - The 03:28 run used the earlier build.
  - The 03:45 and 03:58 runs used `--ai-no-patches`, and the 03:45 run died of ENOSPC.
- **Plan text:**
  - phase-04:92 still asks for "per-side gain".
  - The Progress section stops at 02:37.
  - The success criterion "boil ≤ 1.5x" cannot fail as measured (Q2).

## Unresolved questions

1. Did the `camera_model.py` edit at 04:17 change behaviour? If yes, bump VERSION: the 589 cached analyses and pages from 03:59–04:09 carry the current keys.
2. On held steps, carried sides do not change, so `boil` is about 0 for every carried version and the 1.5x bar cannot fail. Should boil be measured on small picture changes (for example, picture change < 2 codes) instead?
3. The v0.7 thresholds (σ 12, 25 %, 0.1): which shots were they tuned on? Will they need re-tuning once the gate measures outside texels only (M2)?
4. Is one gain for all sides the accepted answer to phase-04:92's "per-side gain", or still an open item for phase 5?
5. Cache pruning: a worker GC, or a manual step? Is it OK to delete the stale keys listed in H3? Should the 20 old T2 plates be kept?
6. When frame 0 is dark (not a voter), the inside of the mosaic keeps the multi-frame vote, which can ghost on action shots, and the painter continues from it. Is that acceptable?
