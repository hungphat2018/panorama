# Code review: phase 4 gate, ladder, patches opt-in (AI surround live path)

Read-only. I edited no source file. What I ran:
- file reads and grep;
- `ast.parse` plus `ruff --select F,E9` on the 5 Python files (all pass);
- `os.stat` inode checks on the X1c folder;
- two tiny pure-Python probes in the scratchpad:
  - the signs of `cv2.phaseCorrelate` and `warpAffine`, on 64x128 arrays;
  - `fit()` against the Lua staircase rules: 1200 simulated sessions, about 15 s on one core.

No decode, encode, compile or GPU.

## Scope

- **New:**
  - `spikes/ai-surround/live/side_gate.py` (11:14), `side-gate.py` (11:01);
  - `side-ladder.py` (11:18), `side-ladder.lua` (10:53);
  - `live/side-gate.json` (11:14), `live/side-model.inputs.json` (11:09).
- **Changed:**
  - `true-sides-test.py` (11:09), `live-worker.py` (11:12);
  - `spikes/performer-p1/src/perf1-ai-sides.c` and `perf1-ai-sides-internal.h` (11:11).
- **About 1.4 k LOC.** Context read:
  - the plan's phase-04 sections "X1c verdict" and "Requirements";
  - `make-wide-demo.compose`, `structure.ShotStructure`, `side_cache` page functions, `worker_state` pages, `perf1-ai-sides-upload.c` / `-patches.c`, `perf1-args.c`.
- **Real data:**
  - `out/demo/20261014-043348-true-sides-c`: hard links, `gate-normal/normal.json`;
  - `out/demo/20261014-105347-side-ladder`: `ladder.json`, m3u, bat; no answers yet.
- **Accepted limits, not re-reported:** truth-free measures can't separate breaks from action (unset bars), and 16 trials per staircase.

## Overall

- **The ladder path that the user is about to take is sound:**
  - Lua and Python indices agree;
  - the answers file is valid JSON;
  - the counterbalance is exact;
  - the x75 maths is verified numerically.
- **The gate's maths and slices are right.** The risks are in what surrounds them:
  - a "pass" verdict on a partial bar set;
  - a duplicate-clip check flooded by hard links;
  - a broken order balance after identical trials are dropped;
  - the gate running after the test is already launchable;
  - a worker cache that silently drops T3 in a later `--patches` run.
- **The C change is back-compatible but not built.** The only player binary predates it (see Build status).

## High

### H1. The gate says "pass" when the bars that catch breaks don't exist. CONFIRMED (logic); the planner-v2 outcome is PLAUSIBLE (not run)

- **Where:** `side-gate.py:245-246`, with `live/side_gate.py:321-338`.
- **The cause:** the verdict is "no bars yet" only when *no* bar exists. Otherwise it is pass/fail over the measures that happen to have bars.
- **Today's `side-gate.json`:**
  - has bars only for `detail_loss`, `boil`, `detail`, `seam_step` and `seam_colour`;
  - has none for `seam`, `line`, `jump` or `drift`. Those come from the ladder, and `bars_from` may still leave any of them `{unset}`.
- **What breaks:** a carried version with a positional break is judged only on boil, detail and the seam brightness offset. An example is X1c's planner-v2 pan, f5-7, seam 11.4.
  - On a pan with no held steps, boil is NaN.
  - detail_loss is about 1: the same single resampling as truth-pipe.
  - seam_step is a brightness offset, not a shift.
  - So the verdict is likely "pass".
- **Why it matters now:** `true-sides-test.py` now runs this gate after every build and prints the table. The plan's rule is "a version reaches the user only after it passes the gate", so the clip the user saw break would be labelled as passed. That repeats the named failure: "the test reached the user unchecked".
- **Fix:** define the required bars per mode, and never print "pass" on a partial set.

  ```python
  REQUIRED = {"truth": ("seam", "line", "jump", "drift", "boil", "detail_loss"), "free": ("seam_step", "detail")}
  missing = [m for m in REQUIRED["truth" if ref is not None else "free"] if m not in bars]
  verdict = "fail" if any_fail else (f"incomplete: no bar for {','.join(missing)}" if missing else "pass")
  ```

### H2. A `--patches` worker run after a run without it publishes no pages for cached shots. CONFIRMED (trace)

- **Where:** `live-worker.py:199-207`, `232-243`, `288-291`. The help text is at `121-123`.
- **The cause:**
  - With `--patches` off, `on_shot` saves the analysis but no page. The comment at :201, "a cached analysis has its page", no longer holds.
  - `cached_shot()` returns any cached analysis whether a page exists or not.
  - `warm_start` publishes a page only when `page_state()` is not None. So those shots are loaded **without T3 and never re-analysed**.
  - The help text says such shots "need them analysed again", but no code does that.
- **Failure scenario:** phase 4 step 5 runs A/B on one `--cache`, the T3-off arm first.
  - The T3-on arm (`--patches` plus `--ai-patches`) then shows almost no patches.
  - Its only trace is "pages N" in the log or `pages_shown` in `ai-sides.json`.
  - The A/B silently compares off against off, so it can't name T3's share of the wrong frames.
- **Fix:**

  ```python
  # in cached_shot(), after load_analysis():
  if a.patches and cache.page_state(first, n, an.crop) is None:
      return None        # analysed without --patches: no page cached, analyse again
  ```

  Alternatively, put the patches mode into the analysis key. Also log how many shots were re-analysed for pages.

## Medium

### M1. `side-gate.py md5` reports every clip as identical to itself. CONFIRMED

- **Where:** `side-gate.py:345-363` (`os.walk` over the whole test folder), against `true-sides-test.py:530-533`, where free-look clips are `os.link`s of `clips/<id>.mp4`.
- **Evidence:** in `20261014-043348-true-sides-c`, all 30 free-look clips share an inode with a `clips/` file (st_nlink 2).
- **What breaks:**
  - 30 false "IDENTICAL" groups bury the real one (edge-move: planner-v2 = near-every = truth-pipe).
  - "no identical clips" can never print for any true-sides test.
- **Fix:** skip a path whose `(st_dev, st_ino)` was already seen before hashing, or walk only `clips/`.
- `x1c()`'s own check, over free-look only, and the build's trial drop are unaffected.

### M2. Dropping identical trials breaks the order balance, and a level can vanish silently. CONFIRMED

- **Where:** `true-sides-test.py:541-557`.
- **The cause:** `firsts` is balanced over `count` *before* the trials are dropped.
- **Example, X1c:** edge-move's planner-v2 and near-every trials (k = 3, 9, 15) are dropped. 15 remain per level, and the reference is first in anywhere from 6 to 9 of them.
  - That breaks the user's rule "each version first in exactly half the trials", which exists because of the measured 29/44 lean to the second clip.
- **A level can disappear:** if every trial of a level is identical (another `--at` set), the level vanishes from `key.json` and from `--score`. Only a "left out" line is printed.
- **Fix:** per level, first build the kept moment list, then assign a balanced and shuffled `firsts` over `len(kept)`. If a level is left with 0 trials (or fewer than 2), warn loudly or fail.

### M3. The gate runs after the test is written and announced. CONFIRMED

- **Where:** `true-sides-test.py:573-595`.
- **The order today:**
  1. `trials.m3u`, `play-trials.bat`, the free-look bats and `key.json` are written;
  2. `done: … (N trials)` is printed;
  3. only then does the gate decode every clip (see M4).
- **What breaks:**
  - The test is launchable, and reported done, before its gate verdict exists.
  - If the agent or user starts it on "done", CPU-heavy decodes run during a timing-sensitive viewing. The user's memory rule is "no CPU-heavy work during screen runs".
- **Fix:** run the gate before writing the launchers and printing "done". Write `play-trials.bat` only when every version passes, or mark it ungated and print the verdict last.

### M4. Each clip is decoded 5-7 times, against the phase's rule "each clip is decoded once". CONFIRMED

- **Where:** `side-gate.py:222-231`, `151-156`, `306-318`.
- **Decodes per version:**
  - grey of the version, grey of the truth, and yuv444p of the version;
  - per side, rgb24 of the version and of the truth (picks).
- **Cost:** the truth is re-decoded for every version. X1c (6 moments x 5 clips at 4480x1600) means about 200 decodes per gate run. That multiplies M3's CPU window, and the ladder score's own load.
- **Fix:**
  - decode each clip once as yuv444p: Y is the grey, and U and V are the chroma;
  - cache the truth per moment;
  - make the crops from the in-memory frames (`cv2.cvtColor` YUV→RGB for the 3 picks).

### M5. The truth-free `detail` check can't fail a strongly softened side. PLAUSIBLE

- **Where:** `live/side_gate.py:179-184`.
- **The cause:** a row tile is skipped when the *side's own* medium detail is below `DETAIL_FLOOR` (0.5). A heavily blurred side drops below the floor in every tile, so it returns NaN and never fails.
  - That is phase 3's failure, "soft sides read as a join line", at its worst.
- **Evidence of how much is unjudged:** even on truth-pipe, only 2873 of 4140 cells are judged (31 % NaN; `gate-normal/normal.json`).
- **Contrast:** `detail_loss` (`:192-197`) already does it right. It gates on the truth's detail and floors the version's at 1e-3.
- **Fix:** gate the tile on `m_pic` only, and use `max(m_side, 1e-3)`.
  - A flat side's noise gives a fine/medium ratio of about 1 or more, so real flat content won't read as soft.
  - Then re-run `normal`.
- This matters most for the seam guard, which has no truth.

## Low

### L1. `seam_drift`'s noise is the event's own tail. CONFIRMED (arithmetic)

- **Where:** `side-ladder.py:293` with `live/side_gate.py:64` and `294-295`. `off` excludes only ±2 frames around the event, but `seam_drift` sums 24 frames.
- **Break event:** the snap stays in the window alone at f56-58, giving s/3, 2s/3 and s. The noise equals the peak.
- **Drift event:** f68 gives 0.96 s.
- **Result:** "moved" is false by construction, so `seam_drift` always comes out `{unset}`, with the wrong reason ("did not move it above its noise").
- **Masked today** by the normal-range rule (extreme 100 px).
- **Fix:** for windowed measures, exclude `t ≤ hit[-1] + DRIFT_WINDOW` from `off` (and `+POP_WINDOW` for `pop`).

### L2. The ladder score can mix code versions. PLAUSIBLE

- **Where:** `side-ladder.py:276-289`.
- **The cause:** `bar_candidates` re-renders the half-size events from a fresh `truth_pipe()` with today's camera model and structure code. It compares them with `clips/clean.mp4` from build time, and caches them by `{name}-{side}-{size}px` only.
- **What breaks:** after any `camera_model.VERSION` or `carry_strips` change between build and score:
  - every frame differs, so the noise rises;
  - the events stop "moving" the measures, so bars come out unset or loose.
- **Fix:** record `cm.VERSION` (or a hash of the frames) in `ladder.json`, then refuse or rebuild on a mismatch.

### L3. `normal` reuses cached truth-pipe clips across code changes. PLAUSIBLE

- **Where:** `side-gate.py:378-388`.
- **The cause:** the clips in `gate-normal/clips/` are keyed by moment name only, so the normal ranges can describe old resampling. The disk-cache rule applies here too.
- **Fix:** put the version in the file name.

### L4. `--patches` off still builds the exit strips. PLAUSIBLE (perf)

- **Where:** `live/analyse_v0.py:196, 229`, discarded at `live-worker.py:208`.
- **The cause:** `ShotStructure.add` still rasterises and warps strips on each new drawing, and holds their RGBA until the shot ends.
- **Effect:** this is intended if strips are future side-model inputs. But the A/B's T3-off arm still carries that CPU and RAM. Say so when the wrong-frames result is attributed.

### L5. The NaN-max idiom is latent

- **Where:** `live/side_gate.py:171, 184, 197, 230, 233`.
- **The cause:** `worst = d if not worst >= d else worst` handles the NaN start correctly. But a NaN *candidate* would reset `worst` to NaN and drop earlier, worse tiles.
- **Reachable today?** No path produces NaN now: `_ncc` returns 0.0, the floors hold, and the peaks are finite. Use `np.fmax`/`np.fmin` if candidates can ever be NaN.

### L6. `md5_check` can report a false "same file". CONFIRMED (edge)

- **Where:** `side-gate.py:357-358`.
- **The cause:** a trial whose clip ids are missing from `sums` compares `None == None`, so it is reported as "same file".

### L7. `ladder-result.json` is misleading for low-is-bad measures. CONFIRMED

- **Where:** `side-ladder.py:295-302`.
- **The cause:** "moved" is computed for `shape`, `detail` and `detail_loss` with `nanmax` and a high-side test. Those entries are meaningless. `bars_from` ignores them, but a reader won't.

### L8. Two sources disagree on the plate sharpness number. CONFIRMED (doc)

- `live/side_gate.py:17-18` says the 1 MP plates sat "near 0.87".
- `side-gate.json`'s `detail_loss` source says "median 0.85", and the bar 0.925 is derived from 0.85.

## Build status (C)

- **The source change is fine:**
  - `--ai-no-patches` still parses (sets off), and `--ai-patches` sets on;
  - both take no value, so no existing command line is broken;
  - no stale `no_patches` field remains;
  - the upload walk (`upload.c:295`) skips pages when off;
  - the warm-up is skipped when off;
  - `ai_patches_release` is a no-op without pairs;
  - no-patch fills bind stand-ins.
- **But the binary is old:**
  - `bin/perf1-p3-ai4.exe` (05:17) predates `perf1-ai-sides.c` (11:11).
  - The binary contains `--ai-no-patches` but no `--ai-patches` string. Today it still defaults T3 **on**, and `--ai-patches` would make perf1 refuse its arguments (`perf1-args.c:159-162`).
- **Before the screen session:** rebuild, and run `live-store-selftest.py`.
- **Flag coupling:** T3 needs both worker `--patches` and player `--ai-patches`. Either alone silently means no T3; worker-only also takes heap slots.

## Verified OK (the focus items)

- **Phase correlation sign:**
  - `phaseCorrelate(a, b)` returned +2.97 px for b = a moved right 3, and +2.08 for down 2. The `_phase` docstring is right.
  - The gate uses only magnitudes, or same-sign differences (side minus picture; `seam_drift` sums), so there is no sign bug.
- **Ladder shift direction:** `shifted_wide` with tx −4 moved a line from col 16 to col 12. The left side slides outward, the right one +x, and +dy is down.
- **Slices (no off-by-one):** `Side.band`, `pic_band`, `outer_half`, `pic` (480:1440), `_seam_cols` (at = 192 / 128 is the seam column), the x-t mark, `REGIONS`, and the `shifted_wide` columns (0:960, 2880:3840).
- **Span filter:** it matches `compose('fill')`: crop 2560x1440+0+80 and 1920x1080+2560+0, area-scaled to 960x540 each. The select escaping (`eq(n\,t)`) and `_select_span`'s split are correct.
- **Event frames:**
  - break: onset, +1 and +2 shifted, snap at +3;
  - drift: +0..+23, snap at +24;
  - `bar_candidates` adds the snap frame;
  - all events fit in the 69 frames.
- **Lua and Python indices:**
  - `start` 7 (0-based) and `idx = start + 1` (1-based) both give 5.7 px;
  - `level_index = idx - 1`, and `clips[side][idx]` pairs with Python's `[idx-1]`;
  - idx is clamped to 1..#levels;
  - 2 down before the first wrong answer, then 1 down and 3 up.
- **Answers JSON:** `%g` sizes, `true`/`false` and integers make valid JSON. Python reads `trials`, `ladder`, `size`, `right` and `event_first`. `right = (choice == 1) == event_first` is correct. "replace" clears the playlist, so trial k+1 starts clean.
- **`fit()`:**
  - shift 0.0278 octave, and `p_right(x75)` = 0.750;
  - simulated with the Lua rules, it is unbiased (true 2.04 gives a median of 2.07; 1.02 gives 1.01);
  - CI coverage is 0.93 at the assumed slope, but 0.81 if the true slope is 1.5/octave. The interval is conditional on the fixed slope.
- **Ladder schedule:** 8 blocks of 4. Each staircase gets 8 trials with the event first and 8 second, and 8 left and 8 right. The longest run is 3.
- **live-worker with `--patches` off:** no variable is used unset. `page` is None, the stats are 0, and `publish_page` and `page_state` are skipped.
- **Division by zero:** `pops`, `badness` and `fit` are guarded. A zero `hp1` mean in `detail_ratio`/`detail_loss` would need hp2 ≥ 0.5 on a locally affine image, which is unreachable.
- **Inputs contract:** its cited symbols exist: `CameraTracker.new_drawing`, `MODELS`, `identity/allowed.json`, `common.schema.json`. The JSON parses.

## Recommended actions (in order)

1. H1: make the verdict honest (required bars per mode) before the next gated build.
2. H2: re-analyse cached shots that have no page when `--patches` is on, or use a fresh `--cache` per A/B arm.
3. M3 and M4: gate before writing the launchers, and decode once.
4. M1 and M2: dedupe the md5 check by inode, and balance the order after drops.
5. Rebuild perf1 before the screen session.
6. M5: change the `detail` tile gate, then re-run `normal`.
7. L1 and L2 at the next touch of `side-ladder.py score`.

## Unresolved questions

1. **`BREAK_DIR` (0.62, 0.78), "x outward":** no script records that measurement.
   - Was the left side's x component outward (−x in the strip)?
   - If the real pan break moved inward, the ladder trains on a different artifact: a duplicated picture strip instead of a side cut off at the seam.
2. **Repeated ladder clips:** each (level, side) clip has one fixed onset, and the staircase replays the same file. Accept that this may teach the timing (x75 optimistic, so stricter bars)? Or add 2-3 onset variants per level?
3. **The verdict before the ladder:** is "pass" on today's partial bar set intended? If so, rename it so no one reads it as a gate pass.
4. **Step 5's A/B:** will the T3-on and T3-off arms share one worker cache? If yes, H2 decides the result.

Status: DONE_WITH_CONCERNS
Summary: The ladder path (Lua/Python indices, answers JSON, counterbalance, x75 fit) and the gate's maths and slices check out. Two high findings: the gate prints "pass" when the break bars don't exist yet, and a `--patches` worker run drops T3 on cached shots, which would invalidate step 5's A/B.
Concerns/Blockers: The perf1 C change isn't built (the binary is from 05:17), so "T3 off by default" isn't in any player yet. The measured planner-v2 outcome under H1 is inferred, not run (no decodes allowed).
