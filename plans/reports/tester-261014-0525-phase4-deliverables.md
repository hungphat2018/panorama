# Tester report: phase-4 deliverables, AI surround live path (CPU only)

Date: 2026-10-14. Plan: `C:/Users/tamla/Downloads/Telegram Desktop/panorama/plans/261013-0718-ai-surround-live-path/phase-04-structure-path.md`.
Scope: the 7 checks requested. Not a diff-aware run. No GPU, no windows or players, no film decoded, one process at a time, no source edits.
Writes: this report; `C:/Users/tamla/Downloads/Telegram Desktop/panorama/spikes/performer-p1/bin/ai-sides-selftest.exe` (recompiled); scratch scripts in `C:/Users/tamla/AppData/Local/Temp/claude/C--Users-tamla-Downloads-Telegram-Desktop-panorama/bef3aa35-6f92-4096-a131-8c5ebcd7d833/scratchpad/tester/`.

## Overview

| # | Check | Verdict | Evidence |
|---|---|---|---|
| 1 | Self-tests | PASS | analysis 18/18, store 251/251, sets 7/7, cache 23/23 |
| 2 | Ruff `F,B007,E9` | PASS | "All checks passed!", exit 0 |
| 3 | Page packing invariants | FAIL on 2 edge paths; core PASS | 550/552 harness checks (failing: E1, and case A's efficiency expectation); E1 and E2 break the prefix rule; neither hit on JJK |
| 4 | Mosaic rule sanity | PASS | 9/9 |
| 5 | X1c test folder | PASS | 0 failing; 247 MB on disk |
| 6 | Identity clusters | PASS | 0 failing of 13,336; 2 notes |
| 7 | Player compile `ai-sides-test` | PASS | exit 0, 0 warnings |

Overall: DONE_WITH_CONCERNS.

## 1. Self-tests (`python -I -X utf8`, numpy/cv2 from `tools/venv-outpaint`)

- `live-analysis-selftest.py`: PASS 18/18, exit 0, 3 s. Camera error 0.22 px over 96 frames. Mosaic median 0.33 codes. Strips on sides [0, 3]. Fade: 6 of 11 kept frames vote.
- `live-store-selftest.py`: PASS 251/251, exit 0, 8 s (243 round trip + 8 concurrency). Ran the reader rebuilt by check 7. Corrupt copies 0, wrong frame plans 0, player block inconsistent 0. Torn copies caught 113, rejected by the counter as designed.
- `live-sets-selftest.py`: PASS. 7 `ok` lines, 0 FAIL, exit 0. Its summary reads "PASS: 0 failed", not "N/N".
- `live-cache-selftest.py`: PASS 23/23, exit 0.
- Doc drift: plan progress line still says "analysis 15/15". The test now reports 18/18.

## 2. Ruff

`ruff check --no-cache --isolated --select F,B007,E9` (ruff 0.6.9) over `identity-refs.py`, `live/*.py`, `live-worker.py`, `true-sides-test.py`, `live-analysis-selftest.py`, `planner-stats.py`: "All checks passed!", exit 0.

## 3. Page packing (`live/structure.py`): FAIL on 2 edge paths, core PASS

Harness `check-pack-invariants.py`: 552 checks, 550 pass. Synthetic patches: tall (h 100-1200, w 1-8), wide (w 100-1500, h 1-8), random (1-260 per side); frames increasing; 10 % overlays; 30 % alpha-0 texels.

PASS:
- Table in film-frame order (column 0 non-decreasing).
- Texture ≤ max_bytes: at 16 MB, in a repack-forcing sweep (budgets 1.0-2.5x the area lower bound, all 9 force the loop), and in 12 random mixtures.
- Every table row maps to its input patch. Texels at (sx, sy) equal the patch's rgba.
- No texture rect overlap.
- Page grid is the tight box and covers every destination.
- `replay(page, f)` equals painting the kept persistent patches with frame ≤ f from the original arrays (8 probe frames per page; overlays excluded). Non-vacuous: 125,336 covered texels at the last frame.
- Budget below the lower bound, or empty input, gives `None`.
- With only the byte budget binding, the kept set is a prefix. The loop keeps the largest fitting prefix in 9 of 9 sweep budgets (binary search). At 16 MB it keeps 27 where 28 fit.

FAIL on the literal rule "patches that don't fit are the latest ones (a prefix of the input is kept)":
- E1 (asserted, FAIL): a patch wider than the page (w 4104 > PAGE_W 4096) is skipped, not stopped at. `structure.py:212-213`: `if w > page_w or w > 0xFFFF or h > 0xFFFF: continue`. Later patches are kept: kept input indices [0, 2, 3, 4, 5]. Not a prefix.
- E2 (printed, not asserted): grid overflow. `pack([first])` gives a page. `pack([first, far])`, with `far` about 20,000 texels away, gives `None` (`structure.py:243-244`). The earliest patch is dropped together with the latest. `live-worker.py:195-201` then publishes the shot with no page.
- Neither occurs in the JJK cache: max grid 2426 x 1027 texels (limit 16384), max entry 1110 x 690, texture width ≤ 4096.

FAIL, tester expectation (not a spec rule), case A: 150 synthetic patches at 16 MB keep 27. Cause is the shelf layout. Texture width is the widest shelf, so every row is padded: 191,996 patch texels in a 3,814,174-texel texture (fill 5 %).
- The docstring claim is wrong. `structure.py:205-208` says tall and wide strips "share no shelf". Repro: a tall 4 x 1000 and a wide 1000 x 8 both sit on shelf 0 (sy 0 and 0). Texture 1000 x 1004, fill 1.2 %.

Real JJK pages (`out/live/cache/6bc0a6fadf566263123a2052fd36f430-530562886/patches`, 128 files; scripts `page-fill-scan.py`, `real-page-invariants.py`):
- 52 are `none` (shots with no patches). 76 have a texture: 3,108 persistent entries, 0 overlay.
- Fill median 0.09 (p10 0.01, p90 0.88). Median texture 8.3 MB for median 16 entries. Max 16.65 MB against a 16.71 MB budget.
- Only 1 of 76 pages reaches 95 % of budget (159 entries), so drops are rare on JJK. The cost is memory, disk and upload size. Upload size is not verified.
- Structural invariants on all 76 pages: PASS (inside texture, no overlap, inside grid, tight grid, film order).
- Not verified: texel equality on real pages (sources not cached).

## 4. Mosaic rule sanity (`live/mosaic_t1.py`): PASS 9/9 (`mosaic-pan-sanity.py`)

Synthetic world and pan as in the self-test (96 frames, 675 px travel).
- Unsure texels: 0.
- Disagree: 0.0000. Judged texels recounted independently: 214,870 (≥ MIN_JUDGED 2000), so the 0.0 is measured, not the default.
- First picture, 516,804 covered texels: median error 0.33 codes (p90 1.00). 100 % equal (±1 code) to some kept frame's warped value: real frame pixels.
- Outside, 245,652 texels: median 0.33.
- Control: every other kept frame 24 px off gives 53,968 unsure texels, disagree 17.6 %, side share 0.404 → 0.315. The rule fires.
- Second exact pan (diagonal, 60 frames, new drawing each frame): unsure 0, disagree 0.0009.

## 5. X1c folder (`out/demo/20261014-043348-true-sides-c`): PASS (`check-x1c-folder.py`)

- `trials.m3u`: 242 entries (start, 48 x [label-1, clip, label-2, clip, prompt], end). All exist and are non-empty. Layout matches the generator (`true-sides-test.py:549-557`).
- `key.json`: 48 trials. Levels: sham 4, frozen 4, planner-v2 18, near-every 18, pipe 4. `odd` matches the generator's rule. Each (moment, version) maps to one clip id (26 pairs). The 26 free-look files used are hard links to those clips.
- `play-trials.bat`: mpv at `tools/mpv-v0.41.0-x86_64-w64-mingw32/mpv.exe` exists. `spikes/ai-surround/trial-answer-keys.lua` exists. Playlist is `%~dp0trials.m3u`.
- Six `play-free-look-*.bat`: each names 5 existing, non-empty clips.
- `metrics.json` parses: thresholds and 6 moments.
- 62 mp4 files, all non-empty with an ftyp box.
- Size: 129 files, 247 MB on disk (235.5 MiB, hard links counted once); 465 MB if summed per name.
- Notes: `ffmpeg.log` is 0 bytes. `free-look/` names each version and sits in the blind folder, so opening it before answering unblinds the test.

## 6. Identity clusters (`out/live/cache/.../identity/clusters.json`): PASS (`check-identity-clusters.py`)

- Parses. version 1, 176 clusters, 1,090 `cut_by_seam` entries.
- 1,281 crops (801 body, 480 head). All PNG, RGB, both sides multiple of 32, 0.184-0.214 MP (224-1152 x 160-864). Paths are relative to `identity/` and resolve.
- `cut_by_seam`: sides left 210, bottom 393, right 195, top 292. 1,241 cluster ids named, 0 unknown.
- Facts agree with the file: clusters 176; shot_sides_cut 1090; tall_tracks 743 = sum of cluster track counts; clustered_tracks 705 = tracks in clusters of 2+; largest_clusters [44, 43, 19, 16, 15, 15, 13, 13]; min_body_h 160 = smallest body crop height.
- Notes:
  - 41 of 176 clusters have body crops only. A head crop needs a head box in the same frame, inside the body box, at least 64 px tall (`identity-refs.py:279-287`). Plan wording is "body and head crops per cluster".
  - 38 singleton clusters.
  - The folder also holds `clusters-dbscan-chained.json.bad` (128 KB, the discarded run) and `embeddings.npz`.

## 7. Player compile (`compile.ps1 -Program ai-sides-test`): PASS

- Exit 0. Output is one line, "compiled .../bin/ai-sides-selftest.exe", with 0 warning or error lines. Exe rebuilt 05:20:16.
- Not covered: this target compiles only `perf1-ai-sides-store.c`. `perf1-ai-sides-patches.c`, `perf1-ai-sides-upload.c`, `perf1-ai-sides.c` and `perf1-finish-surround-ai.c` were not compiled here. `-Program p3` was not compiled, per instruction.

## Performance

- Self-test wall time: analysis 3 s, store 8 s, sets 2 s, cache under 1 s. Pack harness 0.6 s. Mosaic check 5 s. Identity validation a few seconds.
- Compile time not captured. No coverage tool requested or run, so no coverage percentages.

## Critical issues

None blocking.
1. Page layout wastes memory: JJK median fill 9 %. The docstring's shelf claim is false. Upload size per shot unknown.
2. `pack()` literal-rule gaps (E1, E2): one over-wide or far-away patch removes a shot's earlier patches or its whole page. Not seen on JJK.
3. Blind test: `free-look/` in the blind folder reveals the version of each clip.

## Recommendations (priority order)

1. `structure.py`: stop at the first over-wide patch (no `continue`). On grid overflow, drop the latest until the grid fits, not `None` for a fitting prefix. Add E1 and E2 cases to `live-analysis-selftest.py` or `live-store-selftest.py`.
2. Fix the packing layout so tall and wide strips do not share one shelf family (or store tall strips transposed). Re-measure JJK fill on the 76 pages; target at least 50 %. Correct the docstring until then.
3. Keep `free-look/` out of the blind folder until the test is done. Move `clusters-dbscan-chained.json.bad` out of the identity folder or document it.
4. Decide whether body-only clusters (41 of 176) are acceptable for the painter (phase 3) and refiner (phase 5).
5. Compile the player's patch and upload sources (the p3 build, out of this run's scope) and record warnings.
6. Doc drift: plan line "analysis 15/15" is now 18/18.

## Files (absolute)

- Report: `C:/Users/tamla/Downloads/Telegram Desktop/panorama/plans/reports/tester-261014-0525-phase4-deliverables.md`
- Pack code: `C:/Users/tamla/Downloads/Telegram Desktop/panorama/spikes/ai-surround/live/structure.py`
- Mosaic code: `C:/Users/tamla/Downloads/Telegram Desktop/panorama/spikes/ai-surround/live/mosaic_t1.py`
- Identity generator: `C:/Users/tamla/Downloads/Telegram Desktop/panorama/spikes/ai-surround/identity-refs.py`
- X1c folder: `C:/Users/tamla/Downloads/Telegram Desktop/panorama/spikes/ai-surround/out/demo/20261014-043348-true-sides-c`
- Identity output: `C:/Users/tamla/Downloads/Telegram Desktop/panorama/spikes/ai-surround/out/live/cache/6bc0a6fadf566263123a2052fd36f430-530562886/identity`
- Page cache: `C:/Users/tamla/Downloads/Telegram Desktop/panorama/spikes/ai-surround/out/live/cache/6bc0a6fadf566263123a2052fd36f430-530562886/patches`
- Compiled reader: `C:/Users/tamla/Downloads/Telegram Desktop/panorama/spikes/performer-p1/bin/ai-sides-selftest.exe`
- Scratch scripts: `C:/Users/tamla/AppData/Local/Temp/claude/C--Users-tamla-Downloads-Telegram-Desktop-panorama/bef3aa35-6f92-4096-a131-8c5ebcd7d833/scratchpad/tester/` (`check-pack-invariants.py`, `pack-overshoot.py`, `page-fill-scan.py`, `real-page-invariants.py`, `mosaic-pan-sanity.py`, `check-x1c-folder.py`, `check-identity-clusters.py`)

## Unresolved questions

1. Does the player upload the whole page texture per shot (median 8.3 MB), or only the used rects? GPU, not checked.
2. Are the 52 `none` shots strip-free, or were strips rejected by `MAX_STRIP_SHARE`? The cache does not record why.
3. Is body-only acceptable for the 41 clusters without a head crop?
4. Was the p3 build's "0 warnings" re-verified? Not in this run, per instruction.
5. Is the non-prefix behaviour of E1 intended? The `pack()` docstring says prefix.

Status: DONE_WITH_CONCERNS
Summary: All 7 checks ran. Checks 1, 2, 4, 5, 6 and 7 pass. Check 3's core invariants pass, but two edge paths break the stated prefix rule (E1, E2), and page layout wastes about 90 % of textures on JJK.
Concerns/Blockers: page packing fill and layout claim; E1/E2 literal-rule gaps; blind folder exposure; 41 body-only clusters; player patch and upload code not compiled in this run.
