---
title: "Spike P3: cue sheet versions, per-shot latch, seek latch and the clock fixes, in the performer"
date: 2026-10-11
type: spike
answers: "M1 phase 02, step 5 (P3): cue versions with an atomic swap and a per-shot latch; seeks around every swap; the three clock fixes from the clock simulation"
harness: spikes/performer-p1/
results: spikes/performer-p1/results-p3/
---

# Spike P3: cue versions, per-shot latch, seek latch and the clock fixes

Run 2026-10-11, 20:10 to about 21:45, Asia/Bangkok, on Setup A. **Nothing windowed or full-screen was run**: a game was running, then the controller's measurement session started and asked for everything to stop. No graphics device was created either: the new test mode (`--headless`) uses virtual displays and libplacebo's dummy GPU (the one display-stack call was a `--probe`, which only lists adapters and outputs). Every result below comes from code, simulation and that mode. The windowed and full-screen checks are pending, with commands in section 8.

**Terms.**
- A *cue sheet* is the planner's list of per-shot settings; a *version* is one immutable copy of it. Example: version 3 says shot 12, starting at 51.230 s, is cropped 5 % in from every edge and placed 12 mm left.
- A *shot* is one piece of footage between two cuts. The *latch* is the performer's memory of which version a shot started with: shot 12 keeps version 3 on both screens even if version 4 arrives while it plays.
- A *break* is a seek, a pause or a resume. An *epoch* is the stretch of playing between two breaks; every epoch restarts the film clock on one master refresh.
- An *alignment class*: at 240 + 96 Hz, 2 master refreshes carry 5 laptop refreshes, so a master frame change falls on one of 2 positions against the laptop's refresh grid. Each position is a class (3 at 72 Hz, 5 at 100 Hz).
- *Hysteresis*: a follower keeps its refresh for a class until another refresh is better by more than 0.05 ms, and moves only at a cut. *Re-aim*: at a cut the master's frame changes may move by one refresh into a better class.
- *Wrong frame*: a refresh that shows another frame than the one due, applies another cue than the table says for that frame's shot, or whose renderer drew another frame than chosen. *Shot changed on screen*: the version or cue applied to a shot changes while the shot plays.

Evidence tags: **[L]** measured here (virtual displays count as measured code, not as measured screens), **[D]** derived or simulated, **[V]** source read, **[K]** prior knowledge.

## 1. Answer

1. **The P3 pass test is met in the headless simulation** [D]. Six cases: 23.976 fps at 96 and 72 Hz, 25 fps at 100 Hz, a variable-rate trace at 96 and 72 Hz, and the real 12 s 24/30/60 clip at 96 Hz. Each has swap-and-seek runs and scrub runs at 5 seeks per second. Total: **1,441 version swaps, 4,904 seeks (1,339 of them between −50 and +150 ms of a swap), 4,859 restarts, 1.18 million refreshes: 0 wrong frames, 0 shots changed on screen, every monitor starting every epoch together, 0 clock-rule violations.** Every case alone has 202 to 275 swaps and 195 to 251 adversarial seeks.
2. **The judge can see errors.** Seven deliberate defects (no latch, cue looked up by the clock, latch kept across a break, the old `queue_position` pick, followers restarting on their own, monitors not holding during a break, a re-aim that shortens the frame before a cut below one refresh) are each flagged by a named check [D]. The judge, `analyze-cues.py`, reads only the log and knows nothing of the engine; it has 33 tests of its own on made-up logs.
3. **The three clock fixes work and match the earlier simulation** [D]. Hysteresis: 0 mid-shot follower moves in about 65 simulated hours per rule; the first P1 rule moves 0.9 to 4.3 times an hour mid-shot in the ordinary cases (1.6 with a variable rate) and 942 times an hour with the offset parked on a boundary. Re-aim: the aimed row stays within ±1.07 ms at 96 Hz and ±0.72 ms at 72 Hz, against ±2.08 ms without. Last-due picking: 0 wrong frames on every trace; the old shortcut is wrong on 21 % of refreshes on the variable-rate trace.
4. **The real program runs the same code** [L]. `perf1-p3-lgpl.exe --headless` runs the real FFmpeg decoder with seek, the real `pl_queue` (reset, peek, update), the real threads, the control script and the named pipe. At normal priority (the user's game was in the task list at 21:11) all seven runs passed, including 600 seeks at 20 per second (no hang) and 98 seeks at 5 per second; the renderer-drew-chosen check was 0 wrong in all of them. The console numbers are in section 5.5; their logs were deleted when the final test run cleaned its folder.
5. **One gap found, not fixed: a late first refresh breaks "switch together"** (section 6). In the final full test run, made at idle priority while the controller's full-screen session was running, 4 of the 6 real-program runs had epochs where a monitor presented frame 0 one refresh after the planned one. Wrong frames and shots changed were still 0 in those runs. The simulation does not model a render thread more than 2.5 ms late, so it did not show this. The fix (re-plan the start when the first service is late) is described and not built, because code runs were paused.
6. **Not done**: every windowed and full-screen check; `control-client.py` has not been run. Commands in section 8.

## 2. What was built

All under `spikes/performer-p1/`. The first P1 binary `bin/perf1-lgpl.exe` is untouched (sha256 `b245f2d3…`, checked at 21:24; modification time 19:14:47, checked again at 21:30); the exact sources that built it are frozen in `src-perf1-frozen/` with a script that rebuilds them into their own folder (a rebuild there gives a different file, not byte-identical to the original).

| Part | Files (lines) | Job |
|---|---|---|
| Shared core, also run by the simulation | `src/perf1-json.c/.h` (575), `perf1-cue-sheet.c/.h` (520), `perf1-shot-latch.c/.h` (107), `perf1-frame-index.c` (84), `perf1-engine.c/.h` (1,133), `perf1-engine-log.c` (123) | strict JSON parser; sheet loader, validation, fingerprint and version store behind one atomic pointer; the per-shot latch; timestamps of queued frames; the engine (film clock, picks, schedule, breaks); the cue log |
| Picture geometry | `src/perf1-layout.c/.h` (141) | a shot's crop and placement into each monitor's `target.crop` and `image.crop`; the default case reproduces P1's arithmetic (checked against the numbers P1 logged) |
| The program | `perf1-main.c` (465, was 1,347), `-args.c` (160), `-display.c` (320), `-render.c` (465), `-headless.c` (187), `-control.c/.h` (484), `-log.c` (118), `-app.h` (126), `-decode.c` (703, was 505) | start-up and tear-down; command line; real monitors and swapchains; one refresh of one monitor; virtual displays; script and pipe; logs; decoder with seek |
| Tests | `cue-selftest.c` (391), `latch-sim.c` + `latch-sim-model.h` (1,027), `analyze-cues.py` (511), `test-analyze-cues.py` (516), `run-latch-test.py` (371), `test-control-pipe.py` (138) | unit checks; the deterministic simulation; the judge; the judge's tests; the whole test in one command; the named pipe against a running program |
| Media and tools | `make-cue-test-clip.py` (145), `make-cue-sheets.py` (122), `control-client.py` (40), `data/cue-test/`, `data/cue-test-25/`, `data/vfr-24-30-60-pts.txt`, `compile-perf1.sh`, `compile.ps1` | frame-numbered clip; a planner stand-in that writes versions and control scripts; a pipe client; compile with an output-name option |

## 3. How it works

**Cue sheet v0** (`panorama.cues/1`, format in the header of `perf1-cue-sheet.h`). Header: file hash, room signature, preferences hash (carried, not checked), version. Per shot: `start`, `crop` (source pixels), `placement` (centre and height in mm), and `subtitle_monitor`, `surround`, `time`, carried unused. Validation at load: schema, version an integer from 1, **starts strictly increasing, first start at or before 0, crop inside the source and non-empty, placement height positive**; unknown keys are counted and warned about. Versions are immutable. The store keeps all of them (cap 4,096), holds one atomic pointer to the newest, and refuses a version that does not exceed the current one.

**Placement coordinates** (my choice for v0, to confirm in phase 05/06): u is millimetres along the screens from the left edge of the leftmost one; v is millimetres **down** from the top of the master screen (the shared vertical axis of the row-timing code). A shot with no placement is as tall as the height all screens share, centred on the gap: exactly P1's picture.

**The latch** (`perf1-shot-latch.c`). One ring of 16 shots shared by all monitors. A refresh asks for the shot of its frame by the frame's own timestamp. If the shot is in the ring, its version is used. If not, the first monitor to *prepare* that frame captures the store's newest version. The ring keeps the previous shots because a monitor can still be drawing the last frame of shot 11 after the other has started shot 12. Spans never overlap: a version that moved a boundary cannot give an earlier frame to the newer shot. A break resets the ring. A lookup older than 16 shots is flagged stale.

**The engine** (`perf1-engine.c`, no OS calls: time and grids come in through a host interface, which is what the simulation supplies).
- *Frame starts.* Frame n starts on the first master refresh whose film time plus half a master refresh reaches its timestamp: the last frame due, from the frames' own timestamps. Starts are decided in order, two master refreshes ahead, once each, and then every monitor reads the same table.
- *Followers.* The start refresh is the follower refresh whose picture lights the aim row nearest to the moment the master lights it (ADR 0025), kept per class and moved only at a cut when another refresh is better by more than 0.05 ms. Classes come from the exact signal rates: 5 per 2 at 96 Hz, 10 per 3 at 72, 12 per 5 at 100.
- *Re-aim.* At a cut: shift S in {−1, 0, +1} master refreshes, scored on the coming shot's known frames (up to 16); taken only if better by more than 0.05 ms and never if it would shorten the frame before the cut below one refresh.
- *Breaks.* A seek stops presenting on every monitor (the last picture stays on screen), resets the latch, and asks the decoder to seek. The decoder parks; the render thread resets every queue and the frame index; the decoder seeks to the keyframe before the time, decodes forward without queueing, queues the frame that contains the time and the next one, and announces them. The engine then picks the master refresh K that every monitor's render lead can make, trying each alignment class (up to 4 tries per class) and taking the class with the smallest seam error; each follower starts on its own refresh for K. A pause holds; a resume is a seek to the held frame, so it resets the queues too.
- *New rule found by the judge:* a command that arrives while an epoch is half started (the master's first frame is up, a follower's is not) waits until every monitor has started, at most one master refresh. Without it, a second seek left the two screens briefly showing different pictures.

**The decoder** (`perf1-decode.c`) follows the five-step handshake in its header. Pushes use 5 ms timeouts so a blocked push notices a request at once; a frame is added to the engine's index only after every queue holds it, so the engine can never pick a frame a queue lacks. At end of file the decoder parks until a seek; the program ends the run when the master has had the last frame up for its time.

**The renderer** (`perf1-render.c`) asks the engine, then asks the monitor's `pl_queue` for the frame's *exact* timestamp (so its nearest-frame pick cannot choose another), checks which frame it returned, and draws the latched shot's crop and placement in one resample. If the engine says HOLD, nothing is presented and the monitor is left out of the wait until its time (its waitable stays signalled, so waiting on it would spin).

**Control** (`perf1-control.c`). Commands: `load`, `seek`, `pause`, `resume`, `status`, `stop`, as JSON. A script line carries `"at"` (film time on the master) or `"after"` (wall seconds since the previous line); `load` lines are read and validated at start-up, so a bad sheet fails the run before it starts and publishing takes microseconds. The named pipe is message mode, one reply per command, remote clients refused.

**Headless** (`perf1-headless.c`). Two virtual monitors with Setup A's signal timing (laptop 240.0013 Hz; external 96, 72 or 100 Hz, locked to each other with optional drift), each with a thread that "wakes" at the start of every blank and feeds the real refresh-grid fit, an exact scan-line grid, libplacebo's dummy GPU, software decoding with 2 threads. From the controller's request on, it runs at idle priority class and without the MMCSS boost.

## 4. How it is verified

`python -I run-latch-test.py` runs, in order: (1) 155 unit checks (JSON, loader, store, latch, layout against P1's logged numbers); (2) 33 tests of the judge on made-up logs; (3) the six cases above, each judged from its log; (4) the master's frame on every refresh against the exact timestamps; (5) 2 simulated hours per seed on the true grids for hysteresis and re-aim; (6) the seven defects; (7) the real program headless. The simulation (`latch-sim`) is deterministic: a rerun gives a byte-identical log [L]. It models the decoder (a park delay, 30 to 150 ms to decode a seek, frames kept 0.5 s ahead), the planner (a new version every 1.5 to 4.5 s that changes every shot a second or more after the playhead and sometimes moves a boundary), noisy fits of both refresh grids (refitted every second), render-thread wake-ups with jitter and an odd 1 to 2.5 ms delay, a seek scheduled −50 to +150 ms around each swap, pauses, and bursts of 50 seeks at 5 per second.

The judge's checks (`analyze-cues.py` header lists them): frame starts against the formula and the frame each monitor showed; cue looked up by the frame's own timestamp; applied cue equal to the table (a fingerprint of crop and placement); latch stable inside a shot and equal on all monitors; each capture takes the newest version; latch reset at each break; every epoch starting on every monitor together on the planned refresh and within half a refresh in light; nothing of the old epoch after a break command; follower moves only at cuts; re-aim only at cuts and within one refresh; seam within half a refresh. What it cannot see: refresh times in the log are the performer's predictions from its fitted grids, and "drawn" is what the queue returned, so it judges decisions, not the glass (header of the file).

## 5. Results

### 5.1 Latch, picks and breaks (simulation) [D]

| Case | Swaps | Seeks (adversarial) | Pauses | Restarts | Refreshes | Wrong / changed / break / clock |
|---|---|---|---|---|---|---|
| 23.976 fps, 240 + 96 Hz | 263 | 886 (231) | 24 | 891 | 224,616 | 0 / 0 / 0 / 0 |
| 23.976 fps, 240 + 72 Hz | 202 | 706 (195) | 14 | 691 | 153,882 | 0 / 0 / 0 / 0 |
| 25 fps, 240 + 100 Hz | 259 | 894 (242) | 17 | 894 | 225,471 | 0 / 0 / 0 / 0 |
| variable rate (synthetic), 96 Hz | 275 | 898 (251) | 20 | 876 | 227,266 | 0 / 0 / 0 / 0 |
| variable rate (synthetic), 72 Hz | 206 | 711 (197) | 17 | 704 | 150,878 | 0 / 0 / 0 / 0 |
| variable rate (real clip), 96 Hz | 236 | 809 (223) | 22 | 803 | 195,786 | 0 / 0 / 0 / 0 |

"Variable rate": `clock-sim/data/` holds only the two constant-rate traces (anime episode, hotel film). The synthetic trace re-creates the clock simulation's recipe (sections of 23.976, 29.97 and 59.94 fps, 70/20/10 %, millisecond timestamps); the real trace is the 12 s MKV in `file-matrix/media/` (456 frames, 24/30/60 fps sections), read with ffprobe.

Picks against the exact timestamps, 300 s of master refreshes each:

| Trace | Engine wrong | Old shortcut wrong |
|---|---|---|
| 23.976 fps, 96 Hz / 72 Hz | 0 of 28,750 / 0 of 21,561 | 0 / 0 |
| 25 fps, 100 Hz | 0 of 6,363 | 0 |
| variable rate, 96 Hz / 72 Hz | 0 of 28,750 / 0 of 21,561 | 6,101 (21.2 %) / 4,597 (21.3 %), up to 33.4 ms late |

The clock simulation found 12 % on its mix; the share depends on how much 30 and 60 fps footage a trace has.

### 5.2 Negative controls [D]

| Defect | Flagged by |
|---|---|
| no latch (version re-read every refresh) | C4 ×44,859, C4b ×176 |
| cue looked up by the film clock | C2 ×215, C4 ×68, C4b ×215 |
| latch kept across a break | C6 ×43,743 |
| old `queue_position` pick (real 24/30/60 clip) | C1b ×8,991 |
| followers restart on their own earliest refresh | C7b ×93, C11 ×184, C9 ×59 |
| monitors keep presenting during a seek | C8 ×198, C4 ×213, C4b ×226, C6 ×1,946, C6b ×15,995, C11 ×1 |
| re-aim may shorten the frame before a cut below one refresh | C10 ×4 |

### 5.3 Hysteresis and re-aim over simulated hours, true grids [D]

Aim row, ms (p5 to p95, max); mid-shot moves per hour; "recommended" is hysteresis plus re-aim at cuts. Drift 0.5 ppm unless stated. Each seed is 2 simulated hours: 8 seeds (16 h per rule) for 23.976 fps at 96 and 72 Hz, 4 seeds (8 h) for the others, 8 seeds of 6 minutes for the parked case.

| Setup | Rule | Mid-shot moves /h | Aim row | Re-aims /h |
|---|---|---|---|---|
| 23.976 fps, 96 Hz | first P1 rule (nearest) | 1.12 | −1.90 to +1.90 (2.08) | 0 |
| | hysteresis | **0** | −1.88 to +1.93 (2.11) | 0 |
| | hysteresis + re-aim | **0** | **−0.90 to +0.94 (1.07)** | 0.75 |
| 23.976 fps, 72 Hz | first P1 rule | 0.88 | −1.90 to +1.90 (2.08) | 0 |
| | hysteresis + re-aim | **0** | **−0.60 to +0.65 (0.72)** | 1.38 |
| 25 fps, 100 Hz | first P1 rule | 4.00 | −1.88 to +1.87 (2.08) | 0 |
| | hysteresis (+ re-aim) | **0** | −1.85 to +1.90 (2.14) | 0 |
| 23.976 fps, 96 Hz, 2 ppm | first P1 rule / recommended | 1.50 / **0** | ±2.08 / **±1.08** | 0 / 3.38 |
| 23.976 fps, 96 Hz, 5 ppm | first P1 rule / recommended | 4.25 / **0** | ±2.08 / **±1.10** | 0 / 8.50 |
| variable rate, 96 Hz | first P1 rule / recommended | 1.62 / **0** | ±2.08 / ±2.17 | 0 / 11.75 |
| variable rate, offset parked on a boundary (0 ppm, 0.8 h) | first P1 rule / hysteresis | 942 / **0** | | |

- **Agreement with the earlier Python simulation** (`sim-261011-1912-film-clock-rules.md`): it gives ±1.07 and ±0.72 ms with re-aim, ±2.08 without, 0.81 re-aims per hour at 0.5 ppm, 3.5 and 8.5 at 2 and 5 ppm, and 1.5 and 4.0 mid-shot slips per hour for the P1 rule at 2 and 5 ppm. The C engine gives 1.07, 0.72, 2.08, 0.75, 3.38, 8.50, 1.50, 4.25. The P1 rule at 0.5 ppm is 1.12 per hour here against 0.50 there: my noise model for the two fits differs, and the count is a handful of events.
- **With a variable frame rate, re-aim does nothing for the seam** (every class is in use) and churns: 11.75 re-aims per hour, aim row unchanged. This is the Python simulation's "aiming can't help", now with a cost I did not expect. See question 6.
- The parked case: 942 per hour is 79 moves in 5 minutes against the Python simulation's 40.5; the order and the 0 with hysteresis agree.
- Frame before a cut: the guard holds (never below one refresh). At 96 Hz the shortest master frame with re-aim is 3 refreshes; at 72 Hz it can be 1: a swing of the shift from +1 to −1 shortens a 3-refresh frame by 2, which the guard allows. That is a 41.7 ms frame shown for 13.9 ms (question 7).

### 5.4 The units [L]

`cue-selftest.exe`: 155 checks, 0 failed (includes the layout against the numbers P1 logged on 2026-10-11: laptop target 606,0,1280,800 and external 0,0,365,433). `test-analyze-cues.py`: 33 tests, 0 failed (a valid made-up log is clean; each of the judge's checks fires on a made-up log with the matching defect, and most tests also assert that no unrelated must-be-zero check fires; the fingerprint matches the C value). `test-control-pipe.py` against a running headless program: 17 of 17 (status, a good load, the same version again refused, a missing file, a crop outside the video, broken JSON, an unknown command, a negative seek, a seek that lands, a pause that stands still, a resume, stop, exit code 0, the log passing the judge).

### 5.5 The real program, headless [L]

At normal priority, 21:09 to 21:20, with the user's game in the task list (checked at 21:11; numbers from the console; those logs were deleted afterwards). "Sheets published" counts the sheet given with `--cues`.

| Run | Sheets published | Seeks | Pauses | Restarts | Verdict |
|---|---|---|---|---|---|
| 23.976 fps clip, 96 Hz, seek −50..+150 ms around each swap, 40 s | 8 | 7 (all adversarial) | 0 | 8 | pass; each seek landed on the frame containing the time, decode 22 to 35 ms |
| same, scrub at 5/s, 50 s | 10 | 98 | 0 | 99 | pass |
| same, pauses, resumes, a seek while paused | 6 | 2, plus 5 made by resumes | 5 | 8 | pass (the film then ended by a seek near its end) |
| same, 20 seeks/s, 40 s | 4 | 600 | 0 | 601 | pass, no hang |
| `--mode own` | 1 | 0 | 0 | 1 | pass |
| 72 Hz, `--no-lock` | 7 | 6 | 0 | 7 | pass |
| 25 fps clip, 100 Hz | 6 | 5 | 0 | 6 | pass |

In all of them the renderer-drew-chosen check was 0 wrong: the real `pl_queue` returned exactly the frame the engine chose, on both monitors, on every refresh, across about 730 queue resets.

The **final full run of the test** (320 s, idle priority, started about 21:25, while the controller's full-screen session was running):

| Run | Seeks | Restarts | Wrong / changed / break failures | Verdict |
|---|---|---|---|---|
| adversarial, 96 Hz | 11 | 12 | 0 / 0 / 12 | fail |
| scrub 5/s, 96 Hz | 98 | 98 | 0 / 0 / 106 | fail |
| pauses and resumes, 96 Hz | 2 (and 5 pauses) | 8 | 0 / 0 / 9 | fail |
| 20 seeks/s, 96 Hz | 668 | 504 | 0 / 0 / 8 | fail |
| 72 Hz, unlocked | 7 | 8 | 0 / 0 / 0 | pass |
| 25 fps, 100 Hz | 6 | 7 | 0 / 0 / 0 | pass |
| named pipe | | | 17 of 17 | pass |

## 6. Finding: a late first refresh breaks "switch together"

**What the judge saw** [L]. Two findings, both in every failing run: C7 "first refresh 516, planned 515" (a monitor's first refresh of the epoch was one later than planned; fired 7, 66, 6 and 5 times in the four runs, counted per monitor and epoch), and C7b "first frame −6.9 ms or −11.0 ms from the master in light" (the two monitors started out of step; 5, 40, 3 and 3 times). Every example is exactly planned + 1. The two monitors were late by one refresh each, but a refresh is 4.17 ms on the laptop and 10.42 ms on the master: both late gives about −6.9 ms (4.17 − 10.42 − 0.6 ms of the normal offset), only the master late gives about −11.0 ms. The same runs have 0 wrong frames and 0 shots changed on screen: the latch holds; only the alignment of the restart fails.

**Why** [D]. The engine plans a restart so that every monitor can prepare its first refresh in time, and each monitor must then be serviced inside a window of about one refresh (3.3 ms for the laptop, 9.5 ms for the master). A render thread that is late by more than the window presents frame 0 on the next refresh and nothing notices. In these runs the program was at idle priority class while the controller's session was running, so a wake-up more than 10 ms late is plausible; **I did not measure the lateness** (the logs were deleted), so this is the explanation that fits, not a measurement. The same scripts at normal priority had no such failure in seven runs, with the user's game in the task list. The simulation's service jitter stops at 2.5 ms, so it never produced this. A real full-screen run with the render thread in the MMCSS "Games" class should be much less exposed, but the engine has no answer when it does happen, and the laptop's 3.3 ms window is small.

**Proposed fix, not built** (code runs were paused): in `engine_service`, when a monitor's first service of an epoch finds its refresh later than planned and no monitor has presented yet, re-plan the restart from the current time (same rule, `t_ready = now`), save the abandoned epoch's frame list so its starts stay checkable, and cap it at 5 attempts per break. Test it by adding rare long render-thread delays (5 to 15 ms) to the simulation: C7 and C7b should then fail without the fix and pass with it. If one monitor has already presented, nothing can be undone; log it.

**Side effect to undo.** The idle priority class is hard-wired into `--headless` (added at the controller's request to keep tests from disturbing a live session), so on a busy machine step 7 of the test is unreliable. An option such as `--headless-priority normal`, passed by the runner when asked, is needed for a clean record. I did not add it for the same reason (no compile was allowed).

## 7. Decisions and differences from the brief

- **Control script** accepts `"after"` (wall seconds since the previous line) as well as `"at"` (film time on the master): a seek 50 ms before or 150 ms after a swap is far easier to write in wall time.
- **Resume is a seek to the held frame** (queues reset), so it takes a decoder seek (22 to 35 ms on the test clip; up to a keyframe interval on a long-GOP film). Pause alone holds. This follows "all queues reset at every break"; the alternative (resume without a reset, when the queue still holds the frames) is lower latency and needs an index that continues across the pause.
- **Commands wait while an epoch start is in flight** (section 3). A new rule the brief did not have.
- **Presentation during a break**: nothing is presented; each screen keeps its last picture.
- **`--seconds`** counts wall time after the film clock starts, pauses included.
- **End of file ends the run**; a seek after the last frame is not possible.
- **The log's frame ordinals restart in every epoch.** `analyze-perf1.py`'s pace statistics are only meaningful for runs without seeks; use `analyze-cues.py` for the rest.
- **The latch captures when the first monitor prepares the shot's first frame**, one refresh before its light.
- **No mpv code**: the pick-by-exact-timestamp trick and the park/reset handshake are mine; libplacebo's `pl_queue` behaviour (reset, blocking push, nearest pick) was taken from its header [V] and a summary of its source read through a fetch tool [V, secondhand], then confirmed by the headless runs [L].
- **Forced mid-shot move for very long shots under drift**: not built (the clock simulation's open question 5).

## 8. For the controller: windowed and full-screen checks (pending)

Run from `C:\Users\tamla\Downloads\Telegram Desktop\panorama\spikes\performer-p1` in PowerShell 7.3 or later (it passes the quotes in a JSON argument intact). **Esc stops every window.** Each run takes over the screens. Nothing here was run.

**Prepare** (CPU only, seconds; the clip exists already; `python -I .\make-cue-test-clip.py .\data\cue-test` remakes it):

```powershell
Set-Location 'C:\Users\tamla\Downloads\Telegram Desktop\panorama\spikes\performer-p1'
$clip = (Resolve-Path '.\data\cue-test').Path
foreach ($k in 'swaps','adversarial','scrub','breaks') {
    python -I .\make-cue-sheets.py $clip "$clip\sheets-$k" --versions 10 --seed 4 --script $k --span 45
}
```

The clip (`data/cue-test/cue-test.mkv`): 1920×1080, 23.976 fps, 60 s, 1,438 frames, 16 shots with a new background colour at each cut. Every frame shows its number in big digits, a barcode of its index (4 sync blocks, 12 index bits, 4 check bits, 40 px blocks at y 880 to 930), a red bar sweeping across in 2 s, and outlines at 5 % (yellow), 10 % (cyan) and 15 % (magenta) inset. `shots.json` has the cut times. The versions crop 0, 5 or 10 % and shift or shrink the picture by up to 25 mm, differently per shot and version, so a version change is visible exactly where it takes effect.

**W1. Windowed smoke, 30 s, only when no game runs** (the external stays at its current mode; the film then plays at 1.0 speed, unlocked):

```powershell
.\bin\run.cmd perf1-p3-lgpl.exe --windowed --file "$clip\cue-test.mkv" --cues "$clip\sheets-adversarial\cues-v1.json" --control-script "$clip\sheets-adversarial\script-adversarial.jsonl" --seconds 30 --out .\results-p3\windowed-adversarial
python -I .\analyze-cues.py .\results-p3\windowed-adversarial\perf1-cuelog.json
```

Should read: `VERDICT: PASS`; the console lines `DISPLAY1:`/`DISPLAY2:` end with `drawn!=chosen 0` (`held` counts the services spent inside breaks, so it grows with the number of seeks); both windows show the clip, the picture split across them; versions change at cuts only. This is the first run of the real draw path (per-shot crop and placement, hardware-decoded seeks, `pl_queue_reset` with hardware frames).

**F1. Full screen at 96 Hz, the four scripts** (about 60 s each; `--refresh` is held by the process and Windows restores it, ADR 0023):

```powershell
foreach ($k in 'swaps','adversarial','scrub','breaks') {
    .\bin\run.cmd perf1-p3-lgpl.exe --file "$clip\cue-test.mkv" --refresh DISPLAY2=96 --cues "$clip\sheets-$k\cues-v1.json" --control-script "$clip\sheets-$k\script-$k.jsonl" --seconds 50 --out ".\results-p3\fs-96hz-$k"
    python -I .\analyze-cues.py ".\results-p3\fs-96hz-$k\perf1-cuelog.json"
}
```

Should read, per run: `VERDICT: PASS`; wrong frames, shots changed, break failures 0. If C7 or C7b fire, that is the section 6 gap on real hardware: look at `lead_ms` in the monitor logs and report the lateness. Do not run another capture program in these runs.

**F2. Clock rules, 300 s soak without seeks, then the same with the first P1 rule:**

```powershell
$f = 'C:/Users/tamla/Downloads/[Judas] Nama-Anaru - S01E02.mkv'
.\bin\run.cmd perf1-p3-lgpl.exe --file $f --refresh DISPLAY2=96 --seconds 300 --out .\results-p3\fs-96hz-soak300
python -I .\analyze-perf1.py .\results-p3\fs-96hz-soak300
python -I .\analyze-cues.py .\results-p3\fs-96hz-soak300\perf1-cuelog.json
.\bin\run.cmd perf1-p3-lgpl.exe --file $f --refresh DISPLAY2=96 --seconds 300 --no-hysteresis --no-reaim --out .\results-p3\fs-96hz-soak300-p1rule
python -I .\analyze-perf1.py .\results-p3\fs-96hz-soak300-p1rule
```

Should read: refreshes per frame laptop {10: all}, external {4: all}; no 9 or 11 mid-shot (at 0.5 ppm one move in several hours is expected, so 300 s mostly shows none either way: the soak checks that nothing new breaks, the simulation carries the statistics); `row-light seam offset` aim-row median within 0.05 ms of the printed prediction (the X1 test), now also after re-aims; overlay on both monitors; `landed_off_target_refresh` and `missed_refreshes` about 0.

**F3. Live control through the pipe** (the program in one window, commands from a second PowerShell):

```powershell
.\bin\run.cmd perf1-p3-lgpl.exe --file "$clip\cue-test.mkv" --refresh DISPLAY2=96 --cues "$clip\sheets-swaps\cues-v1.json" --pipe perf1-live --seconds 120 --out .\results-p3\fs-96hz-pipe
python -I .\control-client.py perf1-live '{"cmd":"status"}'
python -I .\control-client.py perf1-live ('{"cmd":"load","file":"' + ("$clip\sheets-swaps\cues-v2.json" -replace '\\','/') + '"}') '{"cmd":"seek","t":31.5}'
python -I .\control-client.py perf1-live '{"cmd":"pause"}' '{"cmd":"resume"}' '{"cmd":"stop"}'
```

`control-client.py` has not been run (it does what `test-control-pipe.py` does, which passed 17 of 17).

**By eye, for the user** (F1): the big number and the background change on both screens in the same refresh at a cut; after a `load` nothing changes until the next cut, then the zoom or shift appears on both screens at once with no half-updated picture across the bezel; a seek, and a resume, show the new frame on both screens together; during a pause nothing moves; the red bar crosses the bezel smoothly in the quiet parts.

**Present mode.** PresentMon per output, as the sibling spike does it (its tool and summarizer; I did not check them): `..\performer-p1-vulkan\tools\presentmon-2.6.0\PresentMon-2.6.0-x64.exe` with `--process_name perf1-p3-lgpl.exe --v2_metrics --write_display_metadata --terminate_on_proc_exit --output_file .\results-p3\fs-96hz-adversarial-presentmon.csv`, started hidden two seconds before the run, then `python -I ..\performer-p1-vulkan\summarize-presentmon.py <csv>`. Should read `Hardware: Independent Flip` or `Hardware Composed: Independent Flip` on both swapchains for the whole run, including across the cues' crop and placement changes (the swapchain never changes size, so a changing crop should not leave overlay) [D].

### How the screen output itself could be checked

| Method | What it sees | Verdict |
|---|---|---|
| The cue log plus `drawn` | what the performer chose and what each queue returned, per refresh and monitor | built, judged by `analyze-cues.py`; cannot see the glass |
| PresentMon | present mode per swapchain and when each present was displayed, per output | passive (ETW), does not touch the swapchain [K]; use in every full-screen run |
| Windows Graphics Capture of each window, barcode decoded | the picture the compositor holds, per window, at up to 240 Hz | worked on stock mpv's full-screen window and read barcodes at up to 240 Hz [L, exact-frame spike; whether that window was in overlay during the capture is not recorded]; the clip's barcode survives crops up to 10 % (it ends at y 930 of 1080); **a decoder that finds the barcode under any crop and scale is not written**; it sees the compositor, not the panels [L, frame-pacing research] |
| Desktop Duplication (`dda-present-log.py`) | composed desktop images only | **returned pure black for an overlay window** [L, exact-frame spike], so it cannot check the full-screen path; usable for a windowed (composed) run, where it reports composition times rather than scan-out times [L, mixed-rate spike] |
| Phone at 240 fps or more | the panels, including lag and scan | the only check of the glass; needs the user |

**Would the capture knock the windows out of hardware overlay?** Unknown for both Desktop Duplication and Graphics Capture [K, untested]: the earlier research lists them among the suspected causes and says not to use Desktop Duplication as the measuring tool in an overlay test. Desktop Duplication not seeing the overlay plane at all suggests that, with it running, the window either stays in overlay and is invisible, or is composed and visible; which one happens is the thing to measure. A search for Microsoft documentation on this found nothing about either API. Test: two 30 s runs of F1 `adversarial` with PresentMon running, one without and one with Graphics Capture of both windows; compare the present-mode share. Do not run Desktop Duplication in a run whose overlay state matters.

## 9. Reproduce, headless (no window, no graphics hardware)

```powershell
Set-Location 'C:\Users\tamla\Downloads\Telegram Desktop\panorama\spikes\performer-p1'
pwsh -File .\compile.ps1 -Program latch-test        # bin\latch-sim.exe, bin\cue-selftest.exe
pwsh -File .\compile.ps1                              # bin\perf1-p3-lgpl.exe (an output name can follow: -Output NAME.exe)
python -I .\run-latch-test.py --skip-exe              # steps 1 to 6, about 2 minutes, idle priority, summary in results-p3\latch-test
python -I .\run-latch-test.py                          # all seven steps; step 7 runs the program at idle priority, so use it only on a quiet machine (section 6)
python -I .\test-control-pipe.py                      # the named pipe against a headless run
```

`run-latch-test.py` deletes and recreates its output folder (`results-p3\latch-test`, which today holds the 21:25 run: console text in `results-p3\latch-test-console.txt`, per-run `analysis.txt` files and `latch-test-summary.json`), and deletes the large cue logs after judging them unless `--keep-logs` is given. Copy the folder first if you want to keep this run.

A single run by hand (after the Prepare step of section 8): `.\bin\run.cmd perf1-p3-lgpl.exe --headless 96 --file .\data\cue-test\cue-test.mkv --cues .\data\cue-test\sheets-adversarial\cues-v1.json --control-script .\data\cue-test\sheets-adversarial\script-adversarial.jsonl --seconds 40 --out .\results-p3\h1`, then `python -I .\analyze-cues.py .\results-p3\h1\perf1-cuelog.json`. (Compilation here went through MSYS2's bash with `MSYSTEM=UCRT64`, the same thing `compile.ps1` does.)

## 10. For the ADRs

- **0014**: the latch is built as designed (one atomic pointer, immutable versions, per-shot capture, reset at breaks) and holds over the counts in section 5.1; per-shot work is plain C in the render thread, with no shader table. Placement coordinates are a v0 choice (section 3).
- **0021**: hysteresis (0.05 ms per class, moves at cuts), re-aim at cuts with its guard and last-due picking are implemented and match the clock simulation (section 5.3). Added: commands wait while an epoch start is in flight; resume is a seek. Open: a late first refresh (section 6); re-aim on variable-rate footage.

## 11. Unresolved questions

1. Does the real draw path work: per-shot crop and placement on both screens, a hardware-decoded seek, `pl_queue_reset` unmapping D3D11VA frames, overlay staying on across cue changes? (W1 and F1.)
2. How late does the render thread run at a restart on real hardware, and does it ever miss the planned refresh? (F1 `lead_ms`; then decide on the re-plan fix, section 6.)
3. Is a seek that waits up to one master refresh while an epoch starts acceptable (10 ms at 96 Hz, 20.8 ms at 48 Hz)?
4. Resume as a seek: acceptable on a long-GOP film (up to a keyframe interval of decode before the picture returns), or build resume without a reset?
5. Is "v down from the master's top, in mm" the right placement convention for the contract (the design sketch says surface u and v with the seat frame +y up)? Phase 05/06.
6. Re-aim on variable-rate footage moves the picture against the sound by up to a master refresh and gains nothing: raise the threshold, or switch re-aim off when the stream's rate varies?
7. May a re-aim swing the shift by two refreshes (from +1 to −1) at one cut? The guard allows it while the frame stays at least one refresh long, so at 72 Hz a 41.7 ms frame can be shown for 13.9 ms. Limit each re-aim to one refresh?
8. Should a shot that outlasts the drift (60 s at 5 ppm drifts 0.3 ms; 300 ppm is beyond half a refresh) be allowed a mid-shot move? None is built (the clock simulation's question 5).
9. Does Graphics Capture or Desktop Duplication knock a full-screen window out of overlay on Setup A? (Section 8, two PresentMon runs.)
10. Should the first sheet given with `--cues` count as a swap in the log? Today it does.

Status: DONE_WITH_CONCERNS
Summary: Cue sheet v0, the immutable version store, the per-shot latch, the seek and break machinery, scripted and named-pipe control and the three clock fixes are built into `bin/perf1-p3-lgpl.exe`; the headless simulation passes the P3 test (1,441 swaps, 4,904 seeks, 0 wrong frames, 0 shots changed, all restarts in step) and catches seven injected defects, and the real program passes the same judge headless at normal priority.
Concerns: no windowed or full-screen run happened (pending, section 8); the last full test run at idle priority on a loaded machine failed 4 of 6 real-program runs on late first refreshes, a real gap with a described but unbuilt fix; `control-client.py` is unrun; the earlier normal-priority exe logs were deleted, so section 5.5's first table is console numbers only.
