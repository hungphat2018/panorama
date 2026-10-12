---
title: "Simulation: ADR 0021's film-clock rules, offline"
date: 2026-10-11
type: sim
answers: "M1 phase 01, step 7b: do ADR 0021's clock rules hold with measured refresh jitter and drift, with and without hysteresis, at 25 fps, with real and variable-frame-rate timestamps, across breaks, and on three screens? What does the rolling blend cost?"
harness: spikes/clock-sim/
results: spikes/clock-sim/results/
---

# Film-clock rules, simulated offline

Run 2026-10-11 from 18:20, Asia/Bangkok. Offline only: no window, no screen, no display change. ffprobe read the two content files' video timestamps once (read-only); they are cached in `spikes/clock-sim/data/`.

perf1 changed during this run. At 18:20 it timed refreshes by WaitForVBlank wake-ups. By 19:15 another agent had moved it to row timing from a scan-line grid (`spikes/per-monitor-presenter/row-timing.h`), under the then-new [ADR 0025](../../docs/adr/0025-refresh-times-come-from-the-scan-line.md). The simulator models both versions: "perf1 as built" (18:20) and "perf1 with row timing" (19:15).

**Terms.**
- A *refresh* is one chance for a screen to show a picture.
- *Pace* is how many refreshes each film frame stays up.
- The *seam offset* is how far apart in time the two sides of a bezel change to the next film frame, as seen in the light. Here it is the external minus the laptop, at the top, the aimed middle row and the bottom of the 215 mm picture, with blank and scan included.
- The *master* is the screen whose refresh count is the film clock. A *follower* switches on its own refresh nearest the master's switch.
- *Alignment classes*: the master's refreshes fall at a few fixed positions against the follower's grid: 2 at 240 + 96 Hz, 3 at 240 + 72 Hz, 5 at 240 + 100 Hz.
- A *phase slip* is a follower frame one refresh longer or shorter than the even pace (a 9 or an 11 at 240 Hz). A *flip-flop* is a slip undone within 2 s.
- *Hysteresis*: keep the chosen refresh until another one is better by more than a threshold.
- *Re-aim*: at a cut, move the master's frame changes by one refresh into a better alignment class.

**Tags.** [L] measured on this PC (earlier spikes, other agents' runs today, or this pass). [V] source read. [D] derived or simulated here.

## 1. Answer

1. **The film clock and the follower rule hold, with four details to fix.** Each variant ran 16 simulated hours (8 seeds × 2 h) with the measured jitter and 0.5 ppm drift. On every frame, the master kept its exact pace (4 refreshes at 96 Hz) and the follower kept 10, except where the rules below move them at cuts [D].
2. **Hysteresis is required.**
   - *perf1 as built.* Each time the drift carries the follower's offset across half a refresh (about every 2.3 h), wake-up jitter makes it flip between two refreshes: 57 mid-shot slips per crossing, 53 of them flip-flops. On average that is 22-25 per hour, in bursts, and the same from 0.5 to 5 ppm.
   - *perf1 with row timing.* The scan-line grid is quiet enough that each crossing gives one slip, but mid-shot: 0.5 per hour at 0.5 ppm, 4 per hour at 5 ppm.
   - *The worst case is a parked offset.* It sits on the boundary when the drift is zero (exact pixel clocks) or, for variable-frame-rate files, in the other alignment class (section 4). There, even the scan-line grid slips 40 times in 5 minutes.
   - *The fix.* 0.05 ms of hysteresis per alignment class, moving only at cuts, gives 0 mid-shot slips in every case [D].
3. **Time rows, not the blank. [ADR 0025](../../docs/adr/0025-refresh-times-come-from-the-scan-line.md) decided this, and perf1 now does it.**
   - perf1 as built took the wake-up as the picture time. In light, its "aligned" seam was +0.36 to +4.17 ms off at the aimed row at 96 Hz, and +3.83 to +7.64 ms at 72 Hz (up to 9.87 ms at the bottom) [D].
   - With top-row times, aimed in light at the crossing row and re-aimed at cuts, the aimed row stays within **±1.07 ms (96 Hz) and ±0.72 ms (72 Hz)** for 2 h; top and bottom within ±3.1 / ±2.8 ms (the scan-speed term) [D].
   - Do not use a wake-up fit corrected by a constant delay: the wake-up delay moved by up to 0.37 ms within one run under load [L, measured by the agent who wrote `row-timing.h`].
4. **Re-aim at cuts is still missing from perf1, and it is worth adding.** With re-aim, the aimed row stays within ±1.07 / ±0.72 ms. It fires 0.81 times an hour at 96 Hz (1.25 at 72 Hz); each time, one master frame at a cut lasts one refresh longer or shorter, and the picture stays within one master refresh of the sound. Without it (perf1 with row timing), the aimed row decays to ±2.08 ms at both rates over 2 h [D].
5. **Frames by timestamp: keep half a master refresh of tolerance; drop perf1's shortcut.**
   - "Last frame due, with half a master refresh of tolerance" picked the intended frame on every refresh of three traces: the real anime MKV (timestamps rounded to ms), the real 25 fps MP4, and 2 h of synthetic 24/30/60 fps video.
   - With no tolerance, 11-23 % of refreshes show the wrong frame.
   - perf1's `queue_position` shortcut is still in place (both versions). It is exact for constant frame rates. On the variable trace it shows the wrong frame on 12 % of refreshes (75 % in 60 fps sections), up to 16.7 ms late [D; pl_queue's pick from libplacebo source, V].
6. **Variable frame rates defeat aiming.** Frame changes land on every alignment class, so the aimed row stays at ±1.9 to ±2.2 ms however the start or re-aim is chosen. Only the rolling blend (or variable refresh) fixes it [D].
7. **Breaks: the restart rule holds. It matters when a follower needs more lead time than the master.**
   - On Setup A, a naive restart timed for the master alone never missed: the 240 Hz laptop's lead is shorter. But it picks a random class: the first frame is up to 2.09 ms off, against 1.05 ms with the ADR rule.
   - With a slow follower (an LG at 60 Hz behind a 144 Hz master), the naive restart was 1-2 refreshes late in 99.6 % of restarts (p50 19 ms); the ADR rule never was.
   - After a display-mode change, 32 wake-ups are enough to aim [D].
8. **The rolling blend is exact, and only the master needs a whole-multiple rate.**
   - With exact timing, the light-weighted switch moment equals the reference row's to 9.1 × 10⁻⁷ µs (self-check).
   - Fitted grids: max 0.76 µs with the scan-line fit; p99.9 12 µs and max 20-24 µs with a wake-up fit whose delay is assumed constant.
   - A 240 Hz laptop at 25 fps (9.6 refreshes per frame) stays exact in light-weighted time.
   - Cost: at each frame change, 96 % of laptop rows show one mixed refresh (new-frame share between 2 % and 98 %). About 2 of the laptop's 10 refreshes per frame carry the mix, each in a band of rows [D].
9. **25 fps on Setup A:** no speed change within 1 % helps (the laptop would need −4 % or +6.7 %). The rungs that work today are the rolling blend (exact) and keeping the picture off the laptop (section 6).
10. **Setup B (assumed timings).**
    - A 24 fps film needs 48 Hz custom modes on both 60 Hz screens.
    - Two 48 Hz screens lock 1:1 with a random phase: their crisp seam is off by up to ±10.4 ms.
    - The 144 Hz screen follows within ±3.5 ms at the aimed row, plus a ±4.8 ms scan-speed term.
    - The blend makes every seam exact in light-weighted time (section 7) [D].

## 2. What was simulated

The truth is each monitor's exact refresh grid (top row of refresh k at phase + k × period). The heads drift at a fixed ratio, because one GPU clock drives them.

The performer sees one of two things:
- jittered wake-ups, fitted as perf1-clock.c does: a least-squares line through the last 240, refitted after every wake-up;
- scan-line samples: a 20 ms burst every 2 s, with a line through the last 8 bursts. perf1 now samples about every 1-2 ms and refits each second over 10 s; both give sub-microsecond grids.

Each decision uses the fit that exists when the render thread wakes, one refresh ahead. The outcome is measured on the true grids, in light, row by row, by physical height.

There are four time models:
- *wake*: the wake-up is taken as the picture time (perf1 as built).
- *corrected wake-up fit*: median wake delay removed, blank added; the delay is assumed constant.
- *scan-line fit*: perf1 with row timing, and ADR 0025.
- *exact*.

| Input | Value | Tag |
|---|---|---|
| Laptop 240 Hz | 2720 × 1800 total, 1600 rows, 1175.045 MHz; line 2.3148 µs, scan 3.704 ms, blank 0.463 ms; 215 mm | [L] |
| External | 100 Hz: 2000 × 1115 at 223.0 MHz, scan 9.686, blank 0.314 ms. 96 Hz: 2000 × 1160 at 222.72 MHz, scan 9.698, blank 0.718. 72 Hz: 2000 × 1545 at 222.48 MHz, scan 9.709, blank 4.180. 268 mm | [L] |
| Drift between heads | 240 + 100: from −0.75 / −4.44 ppm against QPC, 0.56 ppm. 240 + 96: 0.5 µs/s. 240 + 72: 0.5 assumed. Sensitivity runs at 0, 2 and 5 ppm | [L], assumption |
| Wake-ups, "probe" model | 67 / 94 µs after the blank starts, sd 21 / 17 µs, plus 0.5 % outliers up to +130 µs (rate assumed) | [L] |
| Wake-ups, "presenter-log" model | bootstrap of 29,158 wake-up residuals from the per-monitor presenter's logs: skewed, 5 % beyond +50 µs, max +584 µs | [L] |
| Scan-line samples | top-row residual p50 0.6 / 2.3 µs | [L] |
| Content | 23.976 fps with ms timestamps (the anime file is exactly that: 34,047 frames, −0.50 to +0.46 ms from exact CFR); 25 fps exact (MP4 timebase 1/12800); cuts every 2-8 s | [L] ffprobe |
| Panel lag | equal and known | assumption |

How well the performer knows its grids, at decision time [D]:
- The corrected wake-up fit predicts top rows to sd 2.8 / 2.3 µs (laptop / external), max 11 µs with the "probe" jitter, or p95 10-11 µs and max 24 µs with the "presenter-log" jitter. That is before any drift in the wake-up delay itself, which was measured at up to 0.37 ms.
- The scan-line fit gets max 0.08 / 0.27 µs.

## 3. ADR 0021, rule by rule

All numbers are [D] from `spikes/clock-sim/results/`. "Aimed row" is the seam offset at the middle row, as p5..p95 (max).

| Rule | Verdict | Numbers | Change |
|---|---|---|---|
| Film clock from the master's refresh count; master = slowest picture monitor with a whole-multiple rate | **Holds** | Master pace exactly 4 (96 Hz), 3 (72 Hz), 4 (100 Hz) on every frame of 16 h, apart from re-aims at cuts. Speed lock +0.10 % (23.976 fps on 96/72 Hz), 0.00 % (25 fps on 100 Hz) | Add a tie rule for two screens at the same slowest rate (Setup B, both at 48 Hz): take the coarsest grid in the middle of the chain |
| Follower on its refresh nearest the master's switch, lag-corrected, aimed at the crossing row | **Holds with row times** (ADR 0025, now in perf1) | perf1 as built: aimed row +0.36..+4.17 (4.36) ms at 96 Hz, +3.83..+7.64 (7.83) at 72 Hz. Row times: −1.90..+1.90 (2.08) without re-aim; −0.91..+0.96 (1.07) and −0.60..+0.65 (0.72) with re-aim | Done by ADR 0025 |
| Each refresh drawn for the moment its own light appears | Not simulated | — | — |
| A variable-refresh follower presents at the exact moment | Holds; the scan-speed term remains | Hypothetical laptop VRR: 0 at the aimed row, ±2.04 ms at top and bottom. Setup B 144 Hz VRR: ±4.81 ms | — |
| Every break restarts on one master refresh ahead of every monitor's render lead | **Holds; matters when a follower's lead is longer than the master's** | Setup A: a master-only restart was never late. LG 60 Hz behind a 144 Hz master: late in 99.6 % of restarts (1-2 refreshes, p50 19 ms), ADR rule 0 %. First frame at the aimed row: max 1.05 ms (ADR) vs 2.09 ms (naive) at 96 Hz | Also pick the best alignment class among the reachable refreshes (+5 ms latency at 96 Hz, +14 at 72 Hz, +53 at 144/60). After a mode change, 32 wake-ups are enough |
| Hysteresis; a follower moves only at a cut | **Holds, and it is required** | Without it: perf1 as built 22-25 mid-shot slips per hour (57 per crossing); perf1 with row timing 0.5-4 per hour, mid-shot, and 40 in 5 min on a parked boundary. 0.05 ms, moves at cuts: 0 mid-shot slips in every case | Value: 0.05 ms, kept separately for each alignment class |
| Aimed start on the best alignment class, re-checked at cuts | **Holds; the re-check is not in perf1** | With re-aim: ±1.07 ms (96 Hz) / ±0.72 ms (72 Hz) for 2 h, at 0.81 / 1.25 re-aims per hour. Without: ±2.08 ms (perf1 with row timing), or 4.45 ms if the choice is frozen | Re-aim = move the master's frame changes one refresh at a cut (picture within ±1 master refresh of the sound). Never shorten the frame before the cut below one refresh: a 60 fps frame of a variable-rate file was lost 3 times in 8 h before this guard. With variable frame rates, every class is in use and aiming can't help |
| Film time is a time; frames picked by timestamp, so variable frame rates work | **Holds with tolerance; perf1's shortcut fails on variable frame rates** | "Last frame due" + half a master refresh: 0 wrong refreshes on every trace. No tolerance: 11.5 % / 15 % wrong (anime file at 96 / 72 Hz), up to 23 % (variable trace at 72 Hz). perf1 shortcut: 0 % on constant rates, 12 % on the variable trace (75 % in 60 fps sections, ≤ 16.7 ms late) | Replace `queue_position`'s half-frame shift: pick the frame by its timestamp, then ask pl_queue for that frame's own pts (its nearest-frame pick is then exact) |
| Consequence: monitors carrying the picture need a whole-multiple rate | **Needs a change** once the blend is in | 25 fps on a 240 Hz follower is exact in light-weighted time | Only the master needs a whole multiple when the others blend |
| Consequence: phase slips go at cuts | **Holds with hysteresis and re-aim** | 0 mid-shot slips; 0.81 re-aims per hour at cuts (0.5 ppm), 3.5 (2 ppm), 8.5 (5 ppm) | — |
| Consequence: followers chain on three screens | **Holds for finer followers**; a coarse far follower adds its own error | A 48 Hz far screen chained to the 144 Hz screen: up to 10.45 ms at its aimed row, 15.5 ms at top and bottom | Use the coarsest screen as the reference and have the finer ones copy its wave; two coarse screens: section 7 |

Not simulated: "one rendering device per graphics adapter".

## 4. Recommended hysteresis and re-aim policy

- **Follower:** for each alignment class, keep the chosen refresh until the nearest one is better by more than **0.05 ms**. Move only at a cut, or at a restart after a break.
- **Master re-aim:** at a cut, move the frame changes by one master refresh when that improves the worst seam by more than 0.05 ms.
  - Stay within one refresh of the start: picture within 10.4 ms of the sound at 96 Hz.
  - Never shorten the frame before the cut below one refresh.
- **Times:** row times from the scan-line grid (ADR 0025), aimed in light at the crossing row, lag-corrected.
- **Result at the measured drift (240 + 96 Hz):** 0 mid-shot slips; 0.81 re-aims per hour, all at cuts; aimed row −0.91..+0.96 ms (max 1.07); top −2.94..−1.08 ms (max 3.06); bottom +1.13..+2.99 ms (max 3.11). The "presenter-log" jitter gives the same.

**Why 0.05 ms.** A forced-crossing experiment drives the follower's offset slowly across half a refresh: 16 crossings of 5 min each per setting, with the corrected wake-up fit. Each cell gives slips and flip-flops per crossing, first with the "probe" jitter, then with the "presenter-log" jitter.

| Hysteresis | Moves anytime: mid-shot slips, flip-flops | Moves only at cuts: slips at cuts |
|---|---|---|
| 0 | 57.2, 52.5 / 58.8, 53.4 | 2.1 / 2.0 |
| 0.002 ms | 27.1, 23.6 / 32.1, 27.3 | 1.25 / 1.5 |
| 0.005 ms | 14.1, 10.6 / 17.1, 12.9 | 1.0 / 1.1 |
| 0.01 ms | 5.3, 2.9 / 6.9, 4.2 | 1.0 / 1.0 |
| 0.02 ms | 1.19, 0.12 / 1.50, 0.12 | 1.0 / 1.0 |
| 0.05 ms | **1.00, 0 / 1.00, 0** | 1.0 / 1.0 |
| 0.1 ms | 1.00, 0 / 1.00, 0 | 1.0 / 1.0 |
| 0.3 ms | 0, 0 (the one needed move came after the 5 min window) | 0 |

- **What the table shows.** One slip per crossing is the minimum: the drift really does move the best refresh. perf1's raw wake times behave like the corrected fit (57.3 / 53.3 at 0).
- **Scan-line grid.** Its error (about 0.1 µs) is smaller than the drift between two refits, so it gives exactly 1 slip per crossing even without hysteresis.
- **Parked offset.** With zero drift and the offset parked on the boundary, there is no clean crossing. Per 5 min run:
  - scan-line grid: 40.5 mid-shot slips without hysteresis, 0 with 0.05 ms;
  - corrected wake-up fit: 885 without, 0 with.

  This is the realistic worst case for variable-frame-rate files at 240 + 96 Hz. The two alignment classes sit half a refresh apart, so aiming one class at zero parks the other exactly on the boundary.
- **Cost.** 0.05 ms is about twice the wake-up fit's worst error (24 µs). It costs at most 0.025 ms of extra offset, plus the drift between cuts (≤ 4 µs for 8 s shots at 0.5 ppm). 0.3 ms also works, but it widens the aimed row to max 1.19 ms.

**Policies over 16 h each at 240 + 96 Hz.**

| Policy | Mid-shot slips per hour | Slips at cuts per hour | Aimed row p5..p95 (max), ms |
|---|---|---|---|
| perf1 as built (wake times, nearest, no hysteresis) | 24.6 (22.6 flip-flops) | 0.31 | +0.36..+4.17 (4.36) |
| perf1 with row timing (scan-line grid, nearest, no hysteresis, no re-aim) | 0.50 | 0 | −1.90..+1.90 (2.08) |
| Nearest refresh, corrected wake-up fit, aimed start | 24.6 | 0.38 | −1.90..+1.90 (2.10) |
| Aimed start, then never move | 0 | 0 | −0.23..+3.73 (4.45) |
| Hysteresis 0.05 ms, moves anytime | 0.44 | 0.06 | −1.89..+1.92 (2.10) |
| Hysteresis 0.05 ms, moves at cuts | 0 | 0.50 | −1.88..+1.93 (2.11) |
| **Recommended** (+ re-aim at cuts) | **0** | **0.81** | **−0.91..+0.96 (1.07)** |

At 240 + 72 Hz, perf1 as built gets 28.0 mid-shot slips per hour and perf1 with row timing 0.50; the recommended policy gets 0, with 1.25 re-aims per hour. At 2 and 5 ppm, perf1 with row timing gets 1.5 and 4.0 mid-shot slips per hour, nearest refresh on the wake-up fit 21-25, and the recommended policy 0, with 3.5 and 8.5 re-aims per hour.

## 5. The rolling blend: exactness and cost

The blend's error is the light-weighted switch moment minus the reference row's. It is checked on 33 heights at every frame change and listed as p50 / p99.9 / max, in µs.

| Case | Exact timing | Scan-line fit | Corrected wake-up fit, "probe" (delay held constant) | Same, "presenter-log" | perf1's raw wake times |
|---|---|---|---|---|---|
| 24 fps, 240 + 96 Hz | 9.1e-7 max | 0.05 / 0.26 / 0.67 | 2.45 / 12.0 / 20.3 | 2.95 / 15.0 / 24.4 | 228 / 240 / 249: the blank term, constant |
| 24 fps, 240 + 72 Hz | — | 0.06 / 0.30 / 0.76 | 2.45 / 12.0 / 17.2 | — | 3,690 [D from the same formula] |
| 25 fps, 240 + 100 Hz (laptop not a whole multiple) | 9.1e-7 max | 0.05 / 0.25 / 0.65 | 2.45 / 12.1 / 18.1 | — | — |
| Variable 24/30/60 fps, 240 + 96 Hz | — | — | p99.9 11.9, max 17.1 | — | — |
| Setup B: 144 Hz copies the LG 48 Hz | — | — | max 15.7 | — | — |
| Setup B: 48 Hz copies the LG 48 Hz | — | — | max 55 (frame change 0, from a 96-wake-up fit); 15 after 10 s | — | — |

- **Exact, for any rate.** The 25 fps row shows that a follower whose rate is not a whole multiple of the film rate stays exact in light-weighted time.
- **Each row keeps the master's frame duration.** Measured in light-weighted time, every row's frame duration matches the master's to within 0.85 µs (scan-line fit) or 8-16 µs (wake-up fit).
- **Accuracy is the time model's.** The blend copies whatever the grids say. With perf1's raw wake times it copies the blank and is off by 0.23 ms at 96 Hz and 3.7 ms at 72 Hz. With a wake-up fit corrected by a fixed delay, it would follow the measured delay drift (up to 0.37 ms). So the blend needs the scan-line grid of ADR 0025.
- **Cost (Setup A, all cases alike):**
  - At each frame change, 96 % of laptop rows show one mixed refresh with a new-frame share between 2 % and 98 %. The shares are spread evenly: p5 / p50 / p95 = 0.05 / 0.50 / 0.95.
  - 1.93 laptop refreshes per frame change carry a mix: 2 in 93 % of changes, 1 in 7 %. Each covers a band of rows.
  - Each row mixes 1 of its 10 refreshes per film frame.
  - A uniform blend (one share for the whole refresh, as libplacebo's oversample mixer gives) is exact only at the aimed row: ±2.04 ms at top and bottom.

## 6. 25 fps on Setup A: the ladder, rung by rung

The master is the external at 100 Hz: 4 refreshes per frame, speed exactly 1.000. The laptop would need 9.6 refreshes per frame. Seam offsets are p5..p95 (max) in ms, over 16 h each, with the recommended policy where it applies.

| Rung | Laptop pace | Aimed row | Top / bottom, max | Notes |
|---|---|---|---|---|
| Nothing (nearest refresh, row times) | 9 on 40 % of frames, 10 on 60 % | −1.84..+1.91 (2.18) | 4.10 / 4.21 | perf1 as built: aimed row −0.01..+3.73 (3.95), bottom up to 5.98 |
| 1a. Laptop custom 200 Hz (native line rate, blank 1.30 ms) | 8, even | −2.09..+2.21 (2.53) | 4.51 / 4.56 | One alignment class: the phase at each mode change decides the offset. Panel acceptance unknown |
| 1b. Laptop custom 150 Hz (blank 2.96 ms) | 6, even | −1.47..+1.55 (1.69) | 3.68 / 3.73 | Two classes, aimed; 0.56 re-aims per hour. Panel acceptance unknown |
| 2. Variable refresh | — | (0) | (2.04 / 2.04) | Not available on Setup A (NVAPI: not possible) [L]. Numbers are what it would give |
| 3. Speed nudge ≤ 1 % | — | — | — | None exists: the laptop needs −4.0 % (10 per frame) or +6.7 % (9 per frame) |
| 4a. Rolling blend on the laptop at 240 Hz | 9.6 in light-weighted time | exact (≤ 0.65 µs, scan-line fit) | exact | Mixes as in section 5. A what-you-see choice: needs the user's OK (ADR 0017) |
| 4b. Uniform blend on the laptop | same | exact | 2.05 / 2.05 | The scan-speed term remains |
| 5. Picture off the laptop | — | no seam | — | External alone, 4 per frame, even |

## 7. Three screens (Setup B, assumed timings)

**Assumptions:**
- LG UltraFine 4K, 27-inch: 60 Hz = 4400 × 2250 at 594 MHz (EDID [L]), scan 16.0 ms. 48 Hz fast-scan mode = 4400 × 2750 at 580.8 MHz, scan 16.36 ms, blank 4.47 ms.
- Other screen: 24-inch 1080p. 60 Hz = CEA timing. 48 Hz = 2200 × 1400 at 147.84 MHz, scan 16.07 ms. 72 Hz = same totals at 178.2 MHz, scan 13.3 ms.
- 144 Hz screen: 27-inch 1440p, scan 6.75 ms, with variable refresh.
- Layout: other | LG | 144, vertical centres level. One GPU (0.5 ppm between heads).
- At 23.976 fps, 60 Hz has no whole multiple within 1 %; 144, 72 and 48 Hz do (6, 3 and 2 per frame).

Seam offsets are p5..p95 (max) in ms, over 8 h each, with the recommended policy.

| Modes | Seam | Option | Pace | Aimed row | Top / bottom, max |
|---|---|---|---|---|---|
| LG 48 (master, centre), other 48, 144 | other–LG | crisp | 2, even | −9.72..+9.67 (10.44): a 1:1 lock, the phase at mode change decides | 11.15 / 11.21 |
| | other–LG | rolling blend (20.8 ms mixes) | — | exact | exact |
| | 144–LG | crisp | 6, even | −3.09..+3.13 (3.50): one class | 8.26 / 8.30 |
| | 144–LG | variable refresh | — | 0 | 4.82 / 4.82 |
| | 144–LG | rolling blend (6.9 ms mixes) | — | exact (≤ 16 µs) | exact |
| Same, screens on independent clocks (±20 ppm, assumed) | both | crisp | slips at cuts: other 3.4 per hour, 144 Hz 10.4 per hour | same ranges | same |
| LG 48, other 72 (if accepted) | other–LG | crisp | 3, even | −3.26..+3.21 (3.50): two classes | 4.10 / 4.06 |
| Native 60 Hz (master = 144 Hz, the only whole multiple) | LG–144 | crisp | 2 and 3 alternate (uneven) | −6.17..+5.81 (7.16) | 11.54 / 11.79 |
| | LG–144 | rolling blend (16.7 ms mixes) | — | exact | exact |
| Chain: LG 48 \| 144 \| other 48 | other–144 | crisp, copies the 144 Hz screen's chosen refresh | 2 | −6.30..+9.31 (10.45) | 15.42 / 15.48 |
| | other–144 | crisp, copies the master's wave (144 Hz blended) | 2 | −3.43..+8.75 (9.47) | 10.23 / 8.70 |

**What remains at each seam:**
- **Two 48 Hz screens side by side** have one alignment class. The crisp offset is whatever phase the mode change gave, anywhere within ±10.4 ms. Options:
  - Re-roll by repeating the mode change. Each try lands within ±1 ms with 9.6 % probability.
  - Blend one of them: 20.8 ms mixes, likely visible.
  - Keep fast motion off that seam.
- **The 144 Hz screen** can go three ways: ±3.5 ms at the aimed row plus ±4.8 ms of scan-speed difference; 0 at the aimed row with variable refresh; or exact with the blend.
- **A chain** adds the far follower's half refresh (±10.4 ms at 48 Hz) on top of its neighbour's offset. So the reference should be the coarsest screen, and the others should copy its wave.

## 8. Limits

- Signal timing only. Panel lags are assumed equal and known. Pixel response and backlight are not modelled.
- Not modelled: dropped presents, missed wake-ups, decoder stalls, DWM composition, audio.
- The head ratio is constant. The wake-up delay is held constant in the "corrected wake-up fit", but under load it was measured to move by up to 0.37 ms. The scan-line results do not depend on it.
- The scan-line model (bursts every 2 s, 16 s of fit) is not perf1's sampler (a sample every 1-2 ms, refit each second over 10 s); both give sub-microsecond grids.
- The 22 s presenter logs, fitted by wake-up, show more head drift (1.7-2.0 ppm) than the scan-line probe (0.56 ppm). The 2 and 5 ppm runs cover that.
- Assumed: the drift at 72 Hz, its sign at 96 Hz, and the probe's outlier rate.
- Setup B is all assumption: sizes, timings, layout, adapters, variable refresh.
- Re-aim scores the coming shot with the fit and the frame timestamps a performer would have. The fits move by about 4 µs per shot, so this look-ahead is negligible.
- Restarts assume 1 ms to render and 30-150 ms to decode a seek target.

## 9. How to rerun

From the repository root (`panorama/`), Python 3.11 with numpy:

```text
python spikes/clock-sim/clock-sim.py --all          # every scenario: 2 h x 8 seeds per variant, about 2 minutes
python spikes/clock-sim/clock-sim.py --all --quick  # 15 min x 2 seeds, about 20 s, to check the code
python spikes/clock-sim/clock-sim.py --scenario 1 3 # some scenarios; --hours H --seeds N to change length
```

- **Outputs** (all in `spikes/clock-sim/results/`):
  - `scenario-N.json`, one per scenario;
  - `summary.txt`: the tables this report quotes;
  - `self-check.json`. The run exits 1 if the blend's exact-timing error reaches 0.01 µs (this run: 9.1 × 10⁻⁷ µs).
- **Real timestamp traces** come from `spikes/clock-sim/data/`. `--reprobe` re-reads the content files with ffprobe; `--ffprobe PATH` picks the binary.
- **Inputs:**
  - `display-timing.py`: Setup A measured, Setup B assumed.
  - `refresh-grid-fit.py`: jitter, the wake-up fit, the scan-line bursts.
  - The per-monitor presenter's logs in `spikes/per-monitor-presenter/results-2026-10-11*/`, for the "presenter-log" jitter.
- **Rules:** `film-clock-rules.py` and `seam-timeline.py`. Measurements: `seam-metrics.py` and `rolling-blend.py`. Scenarios: `scenarios-setup-a.py` (1-3), `scenario-vfr.py` (4), `scenario-breaks.py` (5), `scenario-setup-b.py` (6).
- Every run is seeded, so reruns give the same numbers.

## 10. Unresolved questions

1. Does 0.05 ms hysteresis hold under the real performer's load? The jitter here comes from the probe and the raw presenter. Refit it from perf1's own logs once full-screen runs exist.
2. What is the head drift at 72 Hz, and its sign at 96 Hz? perf1 with row timing now logs the drift of its grid between film start and end, which would settle both.
3. Can the user see one mixed refresh per frame change on the laptop? (Experiment X2 in the zero-delay research.)
4. May re-aim shift picture against sound by one master refresh: 10.4 ms at 96 Hz, 20.8 ms at 48 Hz? It is a what-you-hear choice.
5. Long shots without a cut let the offset drift (0.5 µs/s at 0.5 ppm; 20 µs/s on independent clocks). What threshold should force a mid-shot move? Not simulated.
6. Does the laptop panel accept 150 or 200 Hz custom modes (25 fps rung 1)?
7. Setup B: which screens, what sizes and layout, which graphics adapter drives each, does the 144 Hz screen have variable refresh, and does the 60 Hz screen accept 72 Hz?
