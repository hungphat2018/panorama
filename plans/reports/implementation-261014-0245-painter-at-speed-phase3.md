---
title: "AI surround live path, phase 3: the painter at speed (resident, encoder-free, quantized; sets; GPU sharing)"
date: 2026-10-14
type: implementation report
plan: plans/261013-0718-ai-surround-live-path/phase-03-painter-at-speed.md
---

## Outcome

The live painter is now a resident service that paints a ~1 MP plate in **5.2 s** (from 14 s in X2 and 15-22 s in phase 2), with the hidden-sides scores of today's 25-step plate. It no longer needs the 8B encoder in its process, and it shares the GPU with the player at the no-load level.

| Before (phase 2) | Now |
|---|---|
| ComfyUI server, dynamic VRAM, weights moving every plate | in-process service, models loaded once, loader frozen |
| INT8 DiT + LoRAs on the fly, INT8 8B encoder reading every canvas | W4A8 DiT with the LoRAs merged, one fixed conditioning file |
| 8 steps, SDPA attention | 5 steps, INT8 attention in kernels of ≤ 2048 queries, one layer in flight |
| 15-16 s a plate (22 s under the 4 GB cap) | 5.2 s a plate |
| E1 painter cell: 39.8 / 294.7 wrong / missed per min at 240 Hz | 2.0 / 1.0 (no load: 1.0 / 2.6); 0 / 0 at 96 Hz |

- **Identity references help.** Over 4 seeds, far error drops 7-9 %, and the cut-off character keeps his short hair. The live path is built and off by default (`--identity-refs`): a background CPU encoder reads each crop once, about 25 s a crop.
- **Set atlas.** Shots of one place can share one world, canvas and plate. But on this action episode, sharing a plate mostly shows the wrong things in the sides. A strict join check (sets-v2) now leaves about 1-2 % of shots sharing, so the painting budget must assume no reuse.

## What was built

| Part | Files | What it does |
|---|---|---|
| Painter core | `spikes/ai-surround/painter_core.py` | ComfyUI 0.39 as a library. Load once and freeze: a reload is an error, cache emptying is off, the allocator is capped. Inference mode; stage timings and memory peaks; identity references as extra reference latents |
| Painter service | `painter-service.py` | The GPU process: `/paint` and `/encode` over HTTP on 127.0.0.1, `--gpu-queue`, `--attn-chunk`, `--warmup`, `--vram-cap-gb`; exits with its parent |
| Short attention kernels | `painter_attention.py` | Queries split into kernels of at most N, one in flight |
| Quantizer | `painter-quantize.py` | LoRAs merged in fp32 with ComfyUI's code, then W4A8 / W6A8 / INT8 / NVFP4 in ComfyUI's format, with per-layer error |
| Bench | `painter-bench.py` | DiTs × encoder reads on the 13 hidden-sides scenes, scores, sheets, `--compare` across runs |
| Kernel tools | `painter-kernels-bench.py`, `painter-kernel-profile.py` | Matmul and attention speed per format; one plate's kernels by length |
| Identity test | `painter-identity-test.py` | A character crop from another shot as a second reference |
| Swap test | `painter-swap-test.py` | The live plate against the 25-step plate on both screens, forced choice, with a score |
| Live client | `live/painter.py` (replaces `paint_plate.py`) | Starts the service and sizes the canvas; colour fit; real pixels kept; identity reference (off) |
| Identity encoder | `live/identity_encoder.py`, `live-worker.py --identity-refs` | The 8B encoder on the CPU reads each phase-4 identity crop once (~25 s, 9 GB, only while crops wait) |
| Set cut check | `set-atlas-cuts-check.py` | Joined shots' strips at cuts with the set's plate; today's join check on a cache's joins; the mosaics' disagreement distribution |
| Sets | `live/set_atlas.py`, `live/worker_sets.py` | Registration (X5's rule, incremental), the set's atlas, frame transforms into the set's world, persistence |
| Worker | `live-worker.py`, `live/worker_state.py`, `live/side_cache.py`, `live/painter_jobs.py` | Shots register into sets before publishing. Canvas versions live per set; T1 per set; one plate per set, long sets first; a set-aware warm start |
| Checks | `live-sets-selftest.py`, `live-fake-player.py`, `live-smoke.py --fake-player` | Set geometry against a known world; a headless player for end-to-end runs |
| E1 | `e1-painter-load.py --service` | E1's painter cell runs the worker's own painter |

## Decisions, with the evidence

1. **The encoder leaves the live path.**
   - The plate does not need a per-canvas encoder read (run `20261014-002308-q1-reads-w4a8`, 13 scenes): full read 10.20 / 26.06 / 1.58; one fixed all-grey canvas 10.44 / 26.43 / 1.53; thumbnails in between. By eye the plates are the same.
   - What matters is the image slot in the conditioning; X2's text-only variant had none.
   - Live: `out/live/painter/cond-proxy-grey.pt`. On the CPU the encoder would take 18-33 s a read, which ruled that route out.
2. **W4A8, merged.** All three formats score like the 25-step plate (run `20261013-234638-q2-dits`). W4A8 fits the budget (4.7 GB of weights, about 6.4 GB in use while painting) at INT8's speed.

   | Format | Near / far / seam | s/plate | Weights + peak |
   |---|---|---|---|
   | official INT8 + LoRAs | 10.32 / 27.00 / 1.33 | 7.5 | 7.6 + 1.5 GB |
   | merged W6A8 | 10.05 / 25.74 / 1.32 | 8.2 | 6.4 + 1.7 GB |
   | merged W4A8 | 10.20 / 26.06 / 1.58 | 8.2 | 4.7 + 1.7 GB |
   | 25-step plate | 10.35 / 27.10 / 1.31 | ~180 | |

3. **Speed levers** (run `compare-20261014-021700-levers`):
   - Kept: INT8 attention (8.1 → 6.8 s) and 6 steps (→ 6.5 s), both within the scores' noise.
   - Rejected: a half-size reference (the seam breaks: median seam ×3.9).
   - Held for the user: a 0.74 MP canvas (5.6 s; same scores, other content, softer).
   - The combined recipe: 10.26 / 26.09 / seam median 1.16, **5.48 s** (run `compare-20261014-022847-recipe`). With chunked attention (below): 5.78 s.
   - **5 steps** (run `20261014-043335-lever-turbo5`, now the live default): near 10.16 / far 25.67 / seam median 1.17. Its plates are 1.3 codes from 6 steps' and look the same by eye on two sheets. **5.20 s.**
   - The allocator cap does not matter: 7, 8 and 10 GB all give 5.70 s a plate at 6 steps, with no device allocations per plate (`*-cap7/8/10`).
   - E1's painter load sent no schedule, so its service painted 8 steps. Its per-plate times (7-8 s alone) were 8-step times. E1's sharing results hold: they depend on kernels, not the step count. Fixed in `e1-painter-load.py`.
4. **GPU sharing is about queue depth, not kernel length or weights.**
   - Resident and unthrottled, the painter still cost 8.2 wrong / 40.9 missed per min at 240 Hz (no load: 0 / 0.5). The player's render once waited 53 ms.
   - Its kernels are no longer than E1's harmless synthetic loads (attention ~10 ms, GEMMs ≤ 2.5 ms, VAE ≤ 6 ms), but E1's loads kept one call in flight.
   - Waiting for the GPU after each attention, MLP and VAE convolution (`--gpu-queue layer`, ~0.05 s a plate): **5.1 / 8.2 at 240 Hz and 0 / 0 at 96 Hz.**
   - A below-normal GPU class for the painter was worse, but those cells overlapped another session's CPU build: unsettled.
   - Final cells (recipe; recipe + 2048-query attention kernels): see below.
   - **u, measured.** With the player running at 240 + 96 Hz, the painter keeps 0.75-0.79 of its alone speed in all 6 E1 painter cells, throttled or not.
     - The two-speed target at T = 3 s is therefore about 5.2 s a plate at X5's reuse of 1.10, or about 4.7 s with no reuse (below).
     - 5 steps (5.2 s) meets the first. The second needs one more lever: 4 steps (being benched), the 0.74 MP canvas (the user's eye), NVFP4 MLP (~7 %), or T = 3.5 s.

## E1 with the final recipe

Run `out/e1/final-recipe-cells.sh` (02:39-02:56), perf1 full screen on both monitors, 2 min a cell, 180 s rest before each:

| Cell | 240 Hz wrong / missed per min | 96 Hz wrong / missed per min | Engine replans |
|---|---|---|---|
| no load (today) | 1.02 / 2.55 | 0 / 0 | 4 |
| live recipe, throttled queue | 3.06 / 6.13 | 0 / 0 | 17 |
| **live recipe + attention in kernels of ≤ 2048 queries (now the live default)** | **2.04 / 1.02** | **0 / 0** | **2** |

- With the short attention kernels, the painter cell is at the no-load level: 4 wrong frames in 2 min against 2, and fewer missed refreshes. Another no-load cell (01:11) measured 0.00 / 0.51.
- The cost: 5.78 s a plate instead of 5.48 s, with identical plates. `live/painter.py` defaults to `attn_chunk = 2048`. Chunking is not part of the cache key, so cached plates stay valid.
- E1 criterion ("at or below the no-load cell"): met at 96 Hz, and at 240 Hz within the noise of 2-minute cells.

## Identity references

`painter-identity-test.py --seeds 7,11,23,42`, jjk-one-side, the live recipe (`out/identity-test/20261014-041035-jjk-one-side`):

| Variant | Near / far / seam (mean of 4 seeds) | Far error, per seed |
|---|---|---|
| none (today's plate) | 7.88 / 32.55 / 1.46 | 32.3, 30.5, 35.4, 32.0 |
| ref-read: encoder reads canvas + crop | 7.83 / **29.53** / 1.45 | 27.8, 27.7, 33.0, 29.7 |
| ref-mixed: encoder reads a tiny grey canvas + crop | 7.86 / **30.28** / 1.44 | 28.2, 28.4, 33.9, 30.7 |

- **The reference helps in every seed.** By eye, without it the painter gives the man long, flowing hair and a red scarf; with his crop he keeps his short hair in every seed checked. The blood stain still turns into a red collar.
- **ref-mixed works without reading the canvas.** The encoder needs only the crop. On the CPU that takes 24-28 s a crop and 9 GB of RAM, and it paints the same plate as the GPU read: 0.8-1.2 codes apart, equal scores (`out/identity-test/cpu-vs-gpu-cond`).
- **Live path, off by default.**
  - `live/identity_encoder.py` runs the CPU encoder service only while crops wait. It writes `<cache>/identity/conds/<crop sha1>.pt`.
  - `painter_jobs` gives each plate the set's most frequent cluster crop (phase 4's `identity/clusters.json`, matched by overlapping spans). A crop not yet read is painted without.
  - The worker flag is `live-worker.py --identity-refs`.
- **E2E with phase 4's re-clustered crops** (176 clusters, complete linkage at 0.14; `out/live/smoke/20261014-050823-e2e-identity`, 1150-1450 s):
  - It works end to end: the CPU encoder came up in 8 s and read 10 crops at 22-31 s each.
  - But only 1 of 23 plates had its crop read in time, since read on demand the encoder (~25 s a crop) can't keep up with the painter (~5.5 s a plate). So it now:
    - reads ahead the 24 clusters cut most often at a left or right edge (~10 min of CPU per episode);
    - puts a plate's own request first;
    - counts only left and right seam cuts;
    - prefers a whole-body crop.
  - Phase 4 notes that two of the top clusters (c0, c4) still mix characters. Identity stays off by default until the plates are looked at.
- **Rerun with read-ahead** (`20261014-051919-e2e-identity-prefetch`, the conditionings already read kept): **10 of 21 plates** had a crop.
  - By eye, the jjk-one-side man's plate (29394+77) keeps his short hair, and the blood stain stays a small patch, not a scarf.
  - One cluster's crop is hands with a lighter (28872), a person-detector false track: useless there, but harmless.
  - CCIP's own spread (`mean_diff`) does not flag the mixed clusters, so a whitelist is the safe start. Phase 4's by-eye clean clusters: c1, c2, c8, plus the fur-collared and pink-haired ones.
- **Found on the way (not the painter's):** shot 29263+86 (~1220.5 s) is letterboxed, but the cue sheet gives no crop there. The bars count as picture, so the plate continues them, and the painter fills the room's 275 px bottom margin below them with junk.
  - The cause is upstream: the content model's crop detection gave this shot the full frame at confidence 0.081.
  - In JJK, 28 of 679 shots (3.0 % of frames) have crop confidence under 0.5.
  - The fix belongs in the cue sheet or the content model's crop check (M1), not the worker: the player takes its crop from the cues.

## Set atlas by eye: 10 cuts

`set-atlas-cuts-check.py` on the e2e cache (`out/live/set-cuts/20261014-025704-set-cuts`) shows each joined shot in three strips: the frame before the cut with its own sides, then the joined shot's first and last frames, both with the set's plate.

- **Geometry holds.** Wall curves, floor lines and scrolls continue across the seam at the inherited plate's scale (member scales 0.988-1.000).
- **2 of 6 sets are good.** These are the gallery's shot and reverse shot (4 cuts).
- **4 sets show a mismatch caused by sharing:**
  - Set 8638: a character's flying hair, real pixels plus the painter's continuation, appears in another shot's sides.
  - Set 11888: the key shot is a 1-frame smoke flash, and its plate's dark smoke stays beside the clear 80-frame shot.
  - Set 12203: the key shot fades in from black. Its mosaic is black (median-bright vote over kept frames with luma 0 / 0 / 0.002 / 0.036), so the plate is black, and a lit shot inherits it.
  - Set 15239: the right side is dark where the two shots' depths differ.
- **Fix: sets-v2.** A shot joins only if all three hold; matched candidates are tried by inliers:
  - Its mosaic agrees with the matched member's where both saw the place (`set_atlas.agreement`: cells of 4 texels, at most 15 % differing by more than 40 codes).
  - Neither shot has a character cut by its left or right edge (`worker_sets.edge_person`: a content-model box within 2 % of the edge).
  - If the set already has a plate, the plate agrees with the shot's own picture in bands along its side edges, 12 % of its width (`plate_band_agreement`). A plate is invented wherever its own shots saw nothing, and the mosaic check cannot see that.
- **Result.**
  - On the old run's 28 joins (`--stats-only`), sets-v2 keeps 2. It rejects all 4 bad cut sets, but also the 4 gallery cuts that looked right: 31-39 % of their cells differ, through character poses and torn mosaics, and characters touch their edges.
  - One kept join (15263 into 15239) is the dark-side cut. Its plate agrees with the shot in its bands (0 % differ), so the dark side is real pixels from the other shot. Whether that side is true to the scene can't be told from the frames.
  - In the fresh e2e run, 3 of 16 SIFT matches join (1-3-frame shots), and the rest differ by 29-90 %.
  - **Reuse is about 1.01**, against X5's 1.10. The success criterion's "plates per film-second ≤ X5's" fails. "No visible mismatch at cuts" now holds by construction: almost nothing is shared.
- **Mosaics (the other session's, v0.5).**
  - Fades now vote with their lit frames.
  - A shot whose kept frames disagree as a whole (over 10 % of multi-seen texels outside the first picture) keeps only its first picture as real. T1 then shows glow there, and the painter invents the sides from the picture.
  - That removes the torn fragments and staircases from those plates. The cause is camera drift: the chained maps are 30-45 px off at the picture's corners after 21 frames on the gallery move. A keyframe ECC refinement fixes 20-25 % of it, but costs CPU and is deferred.
  - Later versions:
    - v0.6 leaves out only the disagreeing regions, not whole shots.
    - v0.7 puts frame 0 inside the first picture, the painter's context, instead of a ghosted vote.
    - v0.8 puts there the lit kept frame nearest the vote instead. With frame 0, a smoke-then-clear shot gave a smoky plate beside a clear picture.
- **Gallery e2e with v0.7** (`out/live/smoke/20261014-045905-e2e-v07-gallery`, 340-640 s, fresh cache):
  - Film time: structure 74 %, plate 5 %, glow 21 %; 49 plates at 5.4 s.
  - The fade set (12203 / 12236) now has a lit plate and joins correctly.
  - Cut sheets: `out/live/set-cuts/20261014-050736-set-cuts`.

## Code review of the live changes

Report: [code-reviewer-261014-0525-phase3-live-painter-sets-identity](code-reviewer-261014-0525-phase3-live-painter-sets-identity.md). It found 4 High, 6 Medium and 7 Low; the coordinate conventions and lock order were verified correct.

**Fixed** (self-tests pass: sets, cache 23/23, store 251/251, analysis 18/18; the plate-generation guard was unit-checked):
- **H1.** Every plate, a shot alone too, now goes to the registry, and the warm start adopts cached plates there. Later joins therefore meet the cover rule and the plate-band check. `plate_key` prefers the registry's key, so identity plates are found again on a re-watch. A cached plate of another recipe is not used (M2, at the warm start).
- **H2.** A join into a set being painted is refused, and a plate registered while verify ran invalidates the verdict (`plate_gen`).
- **H3.** The identity encoder stops its service outside its lock, and stops a service whose read failed.
- **H4.** The paint loop survives an exception. A failed identity plate is retried without the reference, and a timed-out painter service is stopped, so the loop restarts it (M4).
- **M3.** A set whose atlas is not ready is retried 2 s later instead of being dropped for the run.
- **L1.** The join check never raises.
- **L7.** The grey canvas is written atomically.
- **Temp folder.** A check run after these fixes painted 0 plates: the painter's temp folder (`%TEMP%\panorama-painter-*`) vanished 21 s after the worker started. Nothing in the repo deletes it, so it was probably an outside cleaner. The painter now recreates the folder before each plate and checks the canvas write.
- **Verified** with a 90 s headless run with identity on (`out/live/smoke/20261014-054949-e2e-after-review2`): 13 plates, 7 of them with an identity crop, no failures.

**Open:**
- M1: a cached plate without identity is not upgraded when its crop becomes ready.
- M5 is measured: 10 identity plates painted with no failure, and the memory pool's peak stayed at the warm-up's 7152 MB.
- M6: the CPU encoder's load next to the player is not measured by E1, and it is unthrottled.
- L2-L6, as listed in the review.

## 4 steps (benched last)

Run `20261014-045811` (`compare-20261014-045811`), 13 scenes, the live recipe with 4 of the Turbo card's sigmas (1.0, 0.895, 0.705, 0.415, 0):

| Steps | Near / far / seam (median) | Change from 6 steps | s/plate |
|---|---|---|---|
| 6 | 10.26 / 26.09 / 1.16 | | 5.78 |
| 5 (live default) | 10.16 / 25.67 / 1.17 | 1.3 codes | 5.20 |
| 4 | 9.99 / 26.04 / 1.23 | 4.4 codes | **4.41** |

- 4 steps keeps the scores, and by eye on two sheets it holds the bathroom's shelves and the fight's fur and lightning. Its plates differ more from 6 steps', though: other inventions, possibly softer detail at full size.
- It meets the no-reuse target (~4.7 s). It stays off until the user's swap test passes.

## Not done, deferred, open

- **Two-speed forced choice.** Big against small painter on short shots; needs phase 5's small painter. T stays at 3 s.
  - At u 0.77 with no reuse, the big painter's target is ~4.7 s. 5 steps (5.2 s) misses it by ~10 %; 4 steps (4.4 s) or the 0.74 MP canvas would meet it.
  - With a small painter for short shots, glow on short shots goes away (the fresh e2e run showed 60 % glow on a cold start).
- **Sides-only retraining and distillation** (steps 4-5) were not started. Removing about half the canvas tokens from the painter would roughly halve the plate time. It is the biggest remaining lever if 4 steps fails the user's eye.
- **NVFP4 MLP** is not measured (~7 % expected). The quantizer crashed under memory pressure, and its ~4 GB output does not fit the disk tonight (6-7 GB free).
- **OP/ED cached per series** is not built. It needs two episodes of one series.
- **Identity references:** the e2e run with `--identity-refs` waits for phase 4's re-clustered `identity/clusters.json`.
- **Set atlas:** sets-v2 makes sharing rare. The geometry is right, so sharing could return where it helps most (dialogue cut-backs) once mosaics are reliable (phase 4's v0.7) and characters are handled at the seams (T3/T4).
- **The worker's VRAM with phase 5's refiner:** the painter's pool is ~7.15 GB under a 7 GiB cap. The refiner's share is tight unless the int8 K/V cache goes off (−1 GB) or the canvas shrinks.
- **E1 with the player's patch path** (phase 4) is not mine: phase 4 measured 6.0 wrong/min at 240 Hz with patches on, and is batching patch dispatches.

## User checks waiting

1. **Swap test, 5 steps (the live default):** `spikes/ai-surround/out/demo/20261014-044008-swap-live-w4a8-ckint8-turbo5/play-trials.bat`. 20 trials and 2 shams, both screens, about 6 min. Score with `painter-swap-test.py --score DIR "1,2,..."`. Pass: the 25-step plate is picked at most 12 of 20 times.
2. **Swap test, 4 steps:** `.../20261014-045845-swap-live-w4a8-ckint8-turbo4/play-trials.bat`, in another trial order. If it passes, 4 steps becomes the default and meets the no-reuse budget.
3. The 6-step test (`20261014-025649-swap-live-w4a8-ckint8-turbo6`) is only a fallback.
4. Optional: is the 0.74 MP canvas's softer side acceptable?
5. Phase 4's X1c, from the other session.

The swap tests' plates come from single frames, so they do not judge mosaic artifacts. The e2e cut sheets do.

## Unresolved questions

1. Do 4 or 5 steps pass the user's swap test? That decides the default and whether training is needed.
2. Is the 0.74 MP canvas acceptable to the user's eye?
3. Is set sharing worth bringing back for dialogue cut-backs once mosaics are reliable? It needs a dialogue episode (Judas S01E02) to measure.
4. Do identity references hold up on phase 4's real clusters across scenes, not only jjk-one-side? Should they go on with a whitelist of clean clusters (c1, c2, c8, the fur-collared and pink-haired ones) until mixed clusters can be detected?
5. Does the CPU identity encoder (all cores, 9 GB of RAM, ~10 min per episode) cost the player wrong frames? It needs an E1 cell with the encoder running, or a thread cap.
6. Letterboxed shots the content model is unsure of (28 of 679 on JJK) get bars treated as picture. This is for M1's crop detection, or a stopgap in live-cues.py.
