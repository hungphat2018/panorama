---
title: "Spike: seam tuner v2: row timing, the film-step test (X2) and forced-choice calibration"
date: 2026-10-11
type: spike
answers: "M1 phase 01 step 6b and research experiment X2: can the user judge, by forced choice, the gap, height and lag across the bezel, and whether the rolling blend removes a scan-speed difference they can see?"
harness: spikes/per-monitor-presenter/seam-*.cpp, seam-score-x2-trials.py, seam-smoke-tests.py
---

# Seam tuner v2: row timing, film steps, forced-choice calibration

Evidence tags: **[L]** measured here today, **[V]** source read, **[D]** derived. Nothing was run full screen and no display mode was changed (rule). Every run below was windowed, at the native 240 Hz and 100 Hz.

Terms, each with an example:
- **Refresh:** one chance for a screen to show a picture (the laptop has 240 a second).
- **Row, scan:** a screen draws its picture one row at a time, top to bottom. Row 800 of the laptop's 1600 comes 1.85 ms after row 0.
- **Film step:** at 24 fps a moving thing jumps to a new place every 41.7 ms instead of sliding.
- **Aimed row:** today's way. The laptop swaps its whole picture on the one refresh nearest the external's swap, judged at one height (mid-height).
- **Rolling blend:** the laptop row at each height instead mixes the old and new picture during the one refresh that straddles the external row's swap, in proportion to time (example: external row swaps 1 ms into a 4.17 ms laptop refresh, so that refresh shows 76% new, 24% old).
- **Forced choice:** two settings shown in turn, labelled 1 and 2; the viewer says which is better; no "adjust until right".
- **Anchor:** a picture of what "right" looks like, shown first: the same pattern crossing a fake bezel (a black strip) drawn inside the laptop screen. Both sides are one panel, so it is perfect by construction.

## 1. Answer

All five jobs are built and pass their smoke tests, windowed, with auto answers:

1. **Row timing in the tuner.** Every row's light time is the top-row time from the scan-line grid, plus row times the line time, plus that monitor's lag. In smooth motion each row is drawn for the middle of the time its light stays up. `WaitForVBlank` is no longer used.
2. **`--refresh DISPLAYn=HZ`**, held by the process with `CDS_FULLSCREEN` (`CDS_TEST` first) and put back by Windows on exit, same code as the presenter's. Not run here (rule).
3. **X2 film steps** (`--mode x2`): anchor, comparisons a and b, 40 trials, answer log with the hidden truth, scorer with the pass test.
4. **Forced-choice calibration** (`--mode calibrate`): height, gap, lag; 7 rounds each; saves the existing JSON plus new fields.
5. **`--windowed`, `--seconds`, `--auto-answer right|wrong|random`**, plus `--quick` (short clips for smoke tests) and `--self-check` (numeric check of the blend weights).

What the controller still has to do, because I cannot do it from here: run the **full-screen presents check** (section 6, step 0) before the user sessions. The mapping from "now" to "which refresh this present reaches" is the one thing windowed runs cannot test.

## 2. What was built

`seam-tuner.cpp` stayed under 600 lines (434) by splitting by concern; `compile.bat tuner` now lists the new sources (only that line changed). Clean at `/W3` and at `/W4` (syntax pass), no warnings.

| File | Lines | What it does |
|---|---|---|
| `seam-tuner.cpp` | 434 | main, arguments, windows (full or windowed), `--refresh`, session dispatch |
| `seam-row-clock.h` | 176 | one monitor's row clock on `scanline-clock.h`: sampler thread, 5 refits a second over 10 s, stable refresh numbering |
| `seam-blend-model.h` | 173 | the timing maths: aimed switch, rolling blend, aimed start, self-check (plain functions, no GPU) |
| `seam-render.cpp` | 351 | shader, one render thread per monitor, per-refresh plan, text overlay, present check |
| `seam-app.h` | 172 | shared types: monitors, scene, sessions |
| `seam-film-path.h` | 60 | where the white bar is at each film frame |
| `seam-x2-session.*` | 349 + 38 | the film-step test |
| `seam-calibration-session.*` | 277 + 31 | the forced-choice calibration |
| `seam-free-session.*` | 141 + 21 | the original key-driven tuner, on the new clocks |
| `seam-settings.*` | 128 + 35 | settings JSON read and write |
| `seam-self-check.cpp` | 108 | `--self-check` |
| `seam-score-x2-trials.py` | 186 | scorer, with `--self-test` |
| `seam-smoke-tests.py` | 145 | runs every mode windowed and asserts the results (35 checks, 66 s) |

Free mode keeps its keys. `S` film steps now uses the aimed row and the 24 fps clock; the HUD gains a line counting presents that reached a different refresh than planned (full screen only).

## 3. The timing model

1. **Row light time** `L(m, k, y) = T_m(k) + y * line_m + lag_m`. `T_m(k)` is the top row of refresh k from a fit to `D3DKMTGetScanLine` samples (about 400 a second per screen). Line time, scan and blank come from `QueryDisplayConfig` [V scanline-clock.h]. A positive lag means that screen's light comes later than its signal, so the tuner draws it for a later moment. Fit residual in today's windowed runs: p50 1.4 to 4.6 us, p95 5 to 11 us, 700 to 1000 samples at start, while other agents were running jobs [L]; the probe's quiet-machine figures were 0.6 and 2.3 us.
2. **Stable numbering.** Each refit re-anchors on the same refresh (a refit that disagrees with the running line by over a quarter refresh is refused and counted), so "reference refresh k0 + 4n" is one film clock for the whole run.
3. **Which refresh a present reaches.** The first top row at or after `now + 0.42 ms + blank`. This reproduces the margins of the old rule (present at least 0.5 ms before the vblank wake-up) to 0.04 ms on all three modes: 0.88 vs 0.89 ms at 240 Hz, 0.73 vs 0.70 at 100 Hz, 1.14 vs 1.13 at 96 Hz [D]. After each present, DXGI frame statistics say which refresh it really reached; mismatches are counted as **late** (a later refresh) or **early** (an earlier one). `--lead-ms` changes the 0.42.
4. **Smooth motion:** row y of a refresh shows the pattern where it is at `L + P/2`, P = that screen's refresh period. That removes the hold-centroid term (research section 1, item 7: 3.1 ms, 0.94 mm at 300 mm/s) [D].
5. **Film clock:** film frame n starts on the external's refresh `k0 + hold * n` (hold 4 at 96 Hz, so 24 fps; the laptop's is 10). `k0` is the aimed start: of 24 consecutive external refreshes, the one where the laptop's nearest refresh lands closest at the aim row, by row light times. The aim row is the middle of the height both screens cover (107.5 mm).
6. **Aimed switch:** the laptop shows film frame n from the refresh whose light at the aim row starts nearest the external's: `e_n(aim) < a_j(aim) + T/2`.
7. **Rolling blend:** for laptop refresh j, row y: `w = clamp((a + T - e) / T, 0, 1)`, `a` = that row's light start in refresh j, `T` the laptop period, `e` the external row's switch at the same physical height (including the lag difference). Both scans are linear in the row, so the renderer sends two numbers per refresh (new share at row 0, its change per row); the shader mixes the old and new bar in linear light and encodes sRGB. Row y's light-weighted switch moment is then `a + T - wT = e`, whatever a, T and e are [D].
8. **Film bar:** 30 mm wide, full height, white (0.95) on black, steps of speed/24 (12.5 mm at 300 mm/s, 25 mm at 600). One step puts its centre on the bezel centre, so that frame shows 5 mm slivers on both sides at once; the frames either side hand it over. Passes repeat to make about 3 s (3.25 / 3.58 s real bezel at 300 / 600; 3.17 / 2.88 s fake) [D from the path code].

## 4. Checks run

**Numeric self-check of the blend** (`seam-tuner.exe --self-check`, no windows) [L]. For 3 film frames and 8 rows, it adds up the light a laptop row gives off over the 7 refreshes around the switch, using the blend numbers cast to float and clamped like the shader, and compares the light-weighted switch moment with the external row's:

| Case | Result |
|---|---|
| 240 + 96 Hz from exact signal timing (saved custom mode 2000 x 1160 at 222.72 MHz), 6 phases between the two clocks | largest error 0.0005 us (limit 1 us) |
| the monitors' real clocks now (240 + 100 Hz), lag 0 / +2.7 ms 3 mm lower / -1.3 ms 2 mm higher | largest error 0.0004 us |

The same run checks the aimed switch against the research on the exact 240 + 96 Hz timing: it leaves **+2.04 ms at the top and -2.04 ms at the bottom, around the aim row** (all six phases), and at most 1.02 ms at the aim row (limit 1.04), so the worst case is 3.06 ms at the top. That is the research's section 3.2 and 1.6 [D, self-check section 1]. Example row from the output: with the external 1.1 ms after the laptop's top row, aimed switch leaves top +3.06, aim +1.02, bottom -1.02 ms.

**Shader and geometry, by capture** [L]. The laptop window was captured at about 236 fps while a trial played:
- bar centres stepped through 316.5, 341.5, 366.5, 391.5 mm, which are the path's centres (k = -3, -1, +1, +3); a bar at 341.5 mm shows 17.5 mm on the laptop and nothing on the external (the rest is behind the bezel gap), at 366.5 mm it shows 17.5 mm on the external, as designed;
- in the **blend** clip 9 of 140 bar frames showed the old and new position at complementary shares (new band at level 124 to 131, about 25% share, old band at about 85%; each band one step wide); in the **plain** clip 0 of 144 did.

**Smoke battery** (`python spikes/per-monitor-presenter/seam-smoke-tests.py`, 66 s, windowed, 35 of 35 checks) [L]:

| Run | Result |
|---|---|
| x2, a at 300 mm/s, always right, 10 trials | 10 of 10; scorer: visible |
| x2, b at 600 mm/s, always wrong | 0 of 10; scorer: not visible, flagged reversed |
| x2, all four conditions, random, 3 each | 12 answers, speeds in blocks (300 then 600), truth logged, target clip is aimed for a and blend for b |
| calibrate, always right, simulated truth height -1.3 mm, gap 23.7 mm, lag 2.6 ms | -1.312, 23.625, 2.562: each within its last step (0.06, 0.125, 0.06) |
| settings JSON | old fields kept, new fields added, brightness note present |

Also exercised by posting key messages to the windows: Enter, A (anchor), R (replay, `replays: 1` logged), 1, 2, Esc (saves) in x2; arrows, Tab, S, R, P, H, Enter in free mode.

**Not tested** (cannot be, here): independent-flip presents full screen (the late/early counters exist for this); `--refresh` (never run, by rule); whether each panel's light follows its signal scan (research question 3, answered only by X2 and a light probe).

## 5. Choices that go beyond or differ from the brief

- **Height rounds show only the horizontal lines; gap rounds only the diagonal stripes** (not both). A "\" stripe moved down by d looks the same as one moved sideways by d, so a choice on stripes alone cannot tell height from gap. Lines are bent only by height; with height set first, stripes are bent only by the gap. To use both patterns in both, set `flags` in the `kInfo` table of `seam-calibration-session.cpp`.
- **Order:** height, gap, lag (lag last, as asked).
- **The film bar is 30 mm wide**, not the 10 to 12 mm of the first session: a 12 mm bar is mostly slivers at a 20 mm bezel gap.
- **Pre-roll card** before each trial shows the question and a one-line gloss ("the upper part of the bar reappears a moment before or after its lower part"; "a second, fainter copy of the bar's edge right beside it"). It shortens from 1.5 to 1 s after trial 4.
- **Scorer flags REVERSED** when 9 or 10 of 10 are wrong. In comparison a the blend clip has its own faint double edge, so a viewer could consistently pick the wrong clip: they see a difference, just not the one asked about.
- **Late/early present counters** and `--lead-ms` (see section 3.3) so the controller can fix the one assumption this machine could not test.
- `--settings FILE` loads gap, height and lag from a saved JSON, so X2 runs with the calibrated values.

## 6. User session script

Run from the PowerShell tool in `C:\Users\tamla\Downloads\Telegram Desktop\panorama`. The tuner takes over both screens full screen; Esc quits and saves. `--refresh DISPLAY2=96` switches the external for the run and Windows puts it back on exit (ADR 0023). Laptop backlight at the matched 44% before starting.

**Step 0, controller, 1 min: presents check.**
```
spikes\per-monitor-presenter\bin\seam-tuner.exe --refresh DISPLAY2=96 --seconds 25 --out spikes\per-monitor-presenter\results-seam-v2\check0
```
Read the HUD's last line after 20 s: `DISPLAY1 0 late, 0 early of N, DISPLAY2 0 late, 0 early of M`. Pass: late and early under 1% of N and M. If every present is late, raise `--lead-ms` (try 0.8); if early, lower it (try 0.2); a few late presents are hiccups, not a setting. Use the same `--lead-ms` below.

**Part 1, calibration, about 5 min.**
```
spikes\per-monitor-presenter\bin\seam-tuner.exe --mode calibrate --refresh DISPLAY2=96 --out spikes\per-monitor-presenter\results-seam-v2
```
- Per parameter (height, gap, lag of the right screen), in this order:
  1. **Anchor card** (about 30 s to read). The pattern runs straight across a black band in the right part of the laptop screen: that is "straight". Enter to start.
  2. **7 rounds, about 8 s each.** Label 1 top left, the real-bezel pattern for 2.5 s; label 2, 2.5 s; then a black card with the question. Press **1** or **2**. R shows both again, A shows the anchor again.
  3. The next anchor card opens with the value just set.
- What to judge: height, "in which did the thin horizontal lines run straighter across the real bezel?"; gap, "in which did the diagonal stripes run on straighter?"; lag, "in which did the stripes stay straighter, sliding right and sliding left?" (stripes slide at 300 mm/s, one slide each way per setting).
- The first rounds compare big differences (gap 12 vs 28 mm); the step halves each round, so the last two rounds will feel like guesses. That is expected: the last step is 0.125 mm, 0.06 mm, 0.06 ms.
- Result: `seam-settings-<stamp>.json` and `seam-settings-last.json`, with a per-round log `seam-calibration-<stamp>.jsonl`. Esc part-way saves what finished (`"status": "partial"`).
- **Pass test:** (1) no value sits at the edge of what the search can reach (gap 4 to 36 mm, height -8 to +8 mm, lag -8 to +8 ms; the first probes are at gap 20 +- 8, height 0 +- 4, lag 0 +- 4 and the pick moves the centre by 8, 4, 2, ... in turn). A value at an edge means the true one is probably beyond it: rerun starting from the edge value (`--gap-mm`, `--height-mm`, `--lag-ms 0,L`, or `--settings`). (2) Confirmation, 1 min, free mode with the saved values:
  ```
  spikes\per-monitor-presenter\bin\seam-tuner.exe --refresh DISPLAY2=96 --settings spikes\per-monitor-presenter\results-seam-v2\seam-settings-last.json --out spikes\per-monitor-presenter\results-seam-v2\check
  ```
  Press 1 (still): lines and stripes straight across the bezel. Press 3 then 4 (300 and 600 mm/s): stripes straight sliding both ways. One keypress of Left or Up (0.5 mm or 0.5 ms) must make a bend you can see; if not, the calibration is no finer than that and the setting is as good as the eye can tell. Optional: run the calibration twice; two runs within 1 mm (gap), 0.5 mm (height), 1 ms (lag) show how repeatable the user's judgment is.

**Part 2, X2 film steps, about 8 min (40 trials; 9 with a few replays).**
```
spikes\per-monitor-presenter\bin\seam-tuner.exe --mode x2 --refresh DISPLAY2=96 --settings spikes\per-monitor-presenter\results-seam-v2\seam-settings-last.json --out spikes\per-monitor-presenter\results-seam-v2
```
- **Anchor first** (about 1 min). A white bar crosses the fake bezel inside the laptop screen in jerky 24 fps steps; the strip is black on black, so the user sees the bar vanish behind it and come out the other side with its top and bottom changing together. Enter to start. A at any question shows it again.
- **Each trial** (about 8 s of cards and clips, about 10 s with the answer): a card names the question for 1.5 s (first 4 trials) or 1 s, then clip 1 (about 3 s), 0.5 s black, clip 2, then the question card until **1** or **2**. R replays both clips (about 8 s more).
  - **a** (real bezel): the bar crosses the whole row of screens. "1 or 2: in which do the bar's top and bottom come out of the bezel at different moments?" The right answer is the aimed-row clip (hidden).
  - **b** (laptop alone, external black): the bar crosses the fake bezel only. "1 or 2: which shows a faint double edge?" The right answer is the blend (hidden).
  - 300 mm/s first (10 of a, 10 of b, mixed in random order), then 600 mm/s.
- If time is short, run `--conditions a:300,b:300` first (20 trials, about 4 min).
- Answers are written after each trial to `seam-x2-answers-<stamp>.jsonl` with the truth, the order, replays, the aimed switch's offsets at the top, aim and bottom rows, and counts of late and early presents.
- **Score:**
  ```
  python spikes\per-monitor-presenter\seam-score-x2-trials.py spikes\per-monitor-presenter\results-seam-v2\seam-x2-answers-<stamp>.jsonl
  ```
- **Pass test** (research section 8), per comparison and speed, 10 trials each: **9 or 10 right: visible**; **6 or fewer: not visible**; **7 or 8: between, run 10 more** and score both files together:
  ```
  spikes\per-monitor-presenter\bin\seam-tuner.exe --mode x2 --refresh DISPLAY2=96 --conditions a:300 --trials 10 --settings ... --out ...
  python spikes\per-monitor-presenter\seam-score-x2-trials.py first.jsonl second.jsonl
  ```
  With 20 answers the rule is the binomial one from the seam-threshold test: 15 or more of 20 visible, 12 or fewer not visible.
- **Reading it:**
  - a visible and b not visible: the scan term shows and the blend fixes it at no visible cost; the case for building the blend into the performer.
  - a visible and b visible: the blend trades one artefact for another; a what-you-see choice for the user (ADR 0017).
  - a not visible at a speed: the aimed row is good enough for film steps at that speed.
  - REVERSED on a: the viewer tells the clips apart but picks the blend clip; ask what they saw (probably the blend's double edge).
- Scorer notes to act on: film not at 24 fps (the external is not at 96 Hz), trials with late or early presents (rerun them if many; if every trial, fix `--lead-ms`), windowed or auto-answered files.

## 7. Which external refresh to calibrate at: 96 Hz

Calibrate at **96 Hz, the rate used for playback**, in the same run (`--refresh DISPLAY2=96` costs nothing: the process holds it). Reasons:
- The lag is what the row model leaves over: the external's processing delay. A scaler or overdrive stage that works per frame has a delay that moves with the frame time (10.4 ms at 96 Hz, 10.0 at 100, 13.9 at 72), and the row times used (blank 0.72 ms at 96 Hz against 0.31 at 100) differ per mode. A lag set at 100 Hz may be off at 96 Hz by an amount nobody has measured [D].
- Gap and height are geometry, so any rate gives the same values. Calibration needs no even film pace, so 100 Hz would also work for those two.
- Playback at 72 Hz (when 96 does not work) would need its own lag check.

If the user has patience for a cross-check, run lag once at 100 Hz (leave out `--refresh`) and compare: a difference is the first measurement of how much lag depends on the mode.

## 8. Unresolved questions

1. Do presents reach the refresh the plan assumes, on both screens, full screen? Step 0 answers it in 25 s; `--lead-ms` is the lever.
2. Does the external's light follow its 9.7 ms signal scan (scaler passes rows straight through)? If not, the blend's row model for the external is wrong and a at 600 mm/s will show no gain. Only X2, or a camera or light probe, can say.
3. Is a black fake bezel on black (invisible; the bar just vanishes) a good enough anchor for the film-step comparisons, or should the user see the strip? The calibration anchors show it (grey pattern behind it).
4. Is height-with-lines-only then gap-with-stripes-only (section 5) acceptable, or does the user want both patterns in both?
5. X2 and calibration need the laptop on the left and the slower screen on the right (Setup A); three screens or other layouts are not handled (the tuner refuses with a message).
