---
title: "Research: zero delay across the bezel at every height, despite different scan speeds"
date: 2026-10-11
type: research
answers: "ADR 0020's remaining scan-speed term (the laptop draws top to bottom in 3.7 ms, the external in 9.7 ms): can it reach zero, as the refresh ticks did?"
sources: "libplacebo master src/renderer.c (read 2026-10-11); Blur Busters CRT beam simulator README; Blur Busters QFT / large-vertical-total pages; Cisco patent US11195452B1; Oculus staff forum posts on GPU overdrive; Brompton Tessera vendor and reseller pages; retailer listings for the external; a laptopmedia panel listing (search snippet only; the page returned 403). Local read-only probes: D3DKMTGetScanLine and D3DKMTWaitForVerticalBlankEvent on both screens, NVAPI current timings and saved custom modes. Derivations: a timing model of both screens (script in the session scratchpad)."
---

# Zero delay across the bezel at every height

Run 2026-10-11, from 15:23, Asia/Bangkok. Nothing on the PC was changed: no display mode, no setting, no full-screen window.

Evidence tags: **[V]** primary source read this pass. **[S]** search snippet, vendor page or forum post. **[K]** prior knowledge, not re-checked. **[L]** measured on this PC this pass. **[D]** my derivation.

## 0. Terms

- **Row, scan:** every refresh, each screen draws its picture one row at a time, top to bottom. The laptop draws its 1,600 rows in 3.70 ms; the external draws its 1,080 rows in 9.70 ms (96 Hz custom mode).
- **Scan speed:** how fast that drawing moves down the glass, in mm per ms. Laptop: 215 mm in 3.70 ms = 58.1 mm/ms. External: 268 mm in 9.70 ms = 27.6 mm/ms.
- **Blank:** the pause between the bottom row of one refresh and the top row of the next. Laptop 0.46 ms. External 0.31 ms at 100 Hz, 0.72 ms at the 96 Hz custom mode, 4.18 ms at the 72 Hz custom mode.
- **Wave:** when the film moves to its next frame, each screen shows the new frame above a line and the old frame below it. That line sweeps down each screen at that screen's scan speed. On the external it sweeps 268 mm in 9.7 ms; on the laptop, 215 mm in 3.7 ms.
- **Light-weighted moment:** the average time of a change, counted by how much light changes when. Example: a row that switches fully at 1.0 ms, and a row that goes 76% of the way at 0 ms and the remaining 24% at 4.17 ms, have the same light-weighted moment, 1.0 ms.
- **Blend:** one refresh that shows a mix of two film frames, such as 76% new and 24% old, mixed in light rather than in pixel values.

## 1. Answer

1. **Yes: the scan-speed term can go to zero at every height, in software, on today's screens.** The laptop copies the external's wave row by row. Each laptop row changes to the next film frame at the same moment as the external's row at the same height. A laptop row can only change once per refresh (every 4.17 ms), so the one refresh that straddles that moment shows a blend of the two frames, split by time share. That row's light over those 4.17 ms equals the light of a row that switched exactly on time, so its light-weighted moment is exact [D; checked numerically, error 0.000 µs over 400 rows]. The external is untouched and stays crisp. Call this the **rolling blend**.
2. **The same blend also removes the refresh-tick remainder and the slow clock drift.** It works for any offset, not only the one that grows down the screen. So the ticks go to zero too, not just to 0.16–0.39 ms.
3. **The cost:** at each film frame change, almost every laptop row (96% on 240 + 96 Hz) shows one 4.17 ms refresh that mixes the two frames. Nothing else changes. The parts are proven: libplacebo's default frame mixer already gives each frame a time share of the refresh ("oversample"), and the Blur Busters CRT beam simulator makes a fast screen imitate another screen's scan row by row [V]. Doing it per row across screens is new; no precedent found. Whether the one-refresh mix is visible is a by-eye question. It changes what you see, so it needs your OK (ADR 0017).
4. **A crisp zero, with no blend, needs hardware timing.** A laptop custom mode that scans at the external's speed: 120 Hz, 2720 × 1725 total at 563.04 MHz, scan 7.73 ms (27.8 mm/ms against the external's 27.6). Rows then line up to ±0.02 ms down the whole seam [D]. One even offset remains, at most ±1.04 ms after the aimed start. A small, even blend (at most 12.5%) closes it, or a phase lock would (variable refresh or genlock); Setup A has neither. Unknown: whether the laptop panel accepts this timing. The NVAPI trial tool exists; trying it needs your approval, and the laptop screen may go dark for up to 15 s.
5. **Found along the way: the presenter's logs time the blank, not the picture.** The vblank signal fires just after the bottom row is drawn; the next top row comes one blank later [L]. In the custom modes the external's blank is long. Its picture therefore comes 0.26 ms (96 Hz) or 3.72 ms (72 Hz) later than the logs say, relative to the laptop. So the spike's "0.16 ms at 72 Hz" is about 3.9 ms at the top rows and 5.9 ms at the laptop's mid-height. Its "0.39 ms at 96 Hz" is about 0.65 ms and 2.7 ms [D from the spike's numbers]. Fix: time rows from a scan-line fit (3.1). Software only.
6. **Along the seam the scan term is ±2.0 ms, not ±3 ms.** With the tops level, only the laptop's 215 mm of height has picture on both sides of the bezel. Over that height the two waves drift 4.07 ms apart, so ±2.04 ms when aimed at the middle [D]. With the tick remainder (±1.04 ms) the worst case is about 3.1 ms.
7. **Smooth motion has a separate 3.1 ms term.** Each refresh's light stays up for the whole refresh: 4.17 ms on the laptop, 10.42 ms on the external. If each refresh is drawn for its start time, the external's picture sits on average 3.1 ms behind the laptop's: 0.94 mm at 300 mm/s. Drawing each row for the middle of its own refresh removes it [D].
8. **After these, "zero as seen in the light" depends on the panels.** Three panel terms remain:
   - The external's processing delay: one number; calibrate it.
   - Pixel response: how fast a pixel finishes changing. It differs with the colours involved and is not measured yet.
   - The laptop backlight may flicker at 240 Hz when dimmed. One database lists it; unverified.

   Each has a lever (section 4). A light probe with two sensors would measure all three.

**Recommendation.**

- **Now** (how it works, no visible downside): use row times from the scan-line fit, and draw each row for the middle of its refresh in smooth motion.
- **Next:** build the rolling blend into the seam tuner. Test it by eye against today's switching, with the fake-bezel reference (experiment X2).
- **In parallel:** try the scan-matched laptop mode (X3). If the panel takes it, the blend shrinks to a small, even one, and pans run at 120 positions a second on the laptop instead of 240.
- **You choose by eye between:**
  - 240 Hz with the rolling blend;
  - 120 Hz scan-matched with a small blend;
  - today's single aimed row.
- **The general mechanism** is the rolling blend. It works on any setup; scan matching depends on what each panel accepts (beside Setup B's 60 Hz LG, the laptop would need 96 Hz or less).

## 2. Invariant

At every height along every seam, each film frame change gives off its light at the same light-weighted moment on both sides. In smooth motion, each side shows the picture where the motion puts it at the light-weighted moment of that side's own refresh. This holds for all content, counting cable timing, panel delay, pixel response and backlight.

## 3. Error budget at the seam

Setup A, 24 fps, external at 96 Hz, tops level, gap known. "After" assumes the matching lever.

| # | Term | Size now | Lever | After |
|---|---|---|---|---|
| 1 | Tick remainder: which refresh carries the change | ≤ ±1.04 ms after the aimed start (0.39 ms measured, in blank terms) | rolling blend | 0 |
| 2 | Blank length: logs time the blank, the picture comes one blank later | +0.26 ms at 96 Hz, +3.72 ms at 72 Hz, −0.15 ms at 100 Hz (external relative to laptop) | time rows from the scan-line fit | 0 |
| 3 | Scan speed | ±2.04 ms at top and bottom when aimed at the middle | rolling blend, or scan-matched mode (±0.02 ms) | 0 |
| 4 | Clock drift between the screens | 0.5 µs/s, about 1.8 ms an hour if not re-aimed | rolling blend absorbs it continuously | 0 |
| 5 | Hold centroid (smooth motion only) | 3.13 ms × speed (240 vs 96 Hz) | draw each row for the middle of its refresh | 0 |
| 6 | Panel processing delay | unknown, constant | forced-choice lag tuning, or a light probe | calibration precision |
| 7 | Pixel response | unknown; varies with colours (external listed "5 ms (OD)") | measure; optionally the laptop imitates the external's transitions | unknown until measured |
| 8 | Laptop backlight flicker when dimmed | unknown; with 240 Hz pulses, up to about ±2 ms varying by row | phone check; run the backlight flicker-free and dim in the picture | 0 if no flicker |
| 9 | Gap and height values | 1 mm of gap error = 3.3 ms at 300 mm/s | forced-choice calibration | calibration precision |

Terms 1–5 are timing on the cable, and software can make them exactly zero. Terms 6–9 are in the panels and the room: calibrate or measure them.

### 3.1 Measured here: when does each top row come? [L]

Read-only probe, both screens at native modes (240 and 100 Hz), 1 s of samples each, about 54,000 per screen.

- `D3DKMTGetScanLine` numbers the picture rows from the top: 0–1599 on the laptop, 0–1079 on the external. The blank is numbered separately: its front-porch lines take the top numbers of the range, then the count restarts at 0 from the sync pulse, flagged as blank. So a frame's line 0 appears twice: once in the blank (flagged) and once as the real top row.
- A fit over picture rows only gives each refresh's top-row time to p50 0.6 µs (laptop) and 2.3 µs (external). Including the blank samples ruins it (p95 291 µs on the laptop). The presentation research's 30 s fits (1.2 µs and 0.3 µs) are in the same range.
- `D3DKMTWaitForVerticalBlankEvent` wakes 68 µs (laptop) and 89 µs (external) after the bottom row, sd 11 and 17 µs. The next top row comes 394 µs and 225 µs after that wake-up: the blank (463 and 314 µs) minus the wake delay.
- So `IDXGIOutput::WaitForVBlank` and `GetFrameStatistics` times, which the per-monitor spike and the seam tuner use, mark the start of the blank. The picture comes one blank later. Blank lengths of the saved custom modes [L NVAPI]: 96 Hz, 2000 × 1160 total → 80 lines = 0.718 ms; 72 Hz, 2000 × 1545 → 465 lines = 4.180 ms. The "fast scan" recipe keeps the scan at 9.7 ms by lengthening the blank, and the picture's delay after the vblank signal grows with it.

### 3.2 Derived numbers [D]

From the screens' exact timings, physical heights 215.0 mm (laptop, EDID mm) and 268.1 mm (21.5" 16:9 external), tops level:

| Pair | Laptop scan | External scan | Blank starts aligned: top / middle / bottom (external − laptop) | Aimed at middle: top / bottom | Tick classes apart |
|---|---|---|---|---|---|
| 240 + 96 (today) | 3.704 ms, 58.05 mm/ms | 9.698 ms, 27.64 mm/ms | +0.26 / +2.29 / +4.33 ms | −2.04 / +2.04 ms | 2.083 ms |
| 240 + 72 | 3.704 | 9.709 | +3.72 / +5.76 / +7.80 | −2.04 / +2.04 | 1.389 |
| 240 + 100 (tuner session) | 3.704 | 9.686 | −0.15 / +1.88 / +3.92 | −2.03 / +2.03 | 0.833 |
| 120 scan-matched (2720 × 1725, 563.04 MHz) + 96 | 7.729 ms, 27.82 mm/ms | 9.698 | +0.12 / +0.14 / +0.16 | −0.02 / +0.02 | 2.083 |
| 120 same totals (2720 × 1800, 587.52 MHz) + 96 | 7.407 ms, 29.03 mm/ms | 9.698 | −0.21 / −0.02 / +0.16 | −0.19 / +0.19 | 2.083 |

"Middle" is the middle of the laptop's height (107.5 mm). A height offset between the screens moves every number by the same amount, and the aim absorbs it.

## 4. Lever map

The question for each lever: what does it bend?

| # | Lever | Bends | Setup A | Evidence |
|---|---|---|---|---|
| 1 | **Rolling blend**: finer screens copy the coarsest screen's wave row by row, splitting the straddling refresh by time share | the moment each row switches, in light | works on today's modes; one 4.17 ms mix per film frame change on the laptop | [D]; parts proven [V] |
| 2 | **Picture time from the scan-line fit** (top row = blank start + blank length) | the blank term | ready; probe in 3.1 | [L] |
| 3 | **Draw for the middle of each row's refresh** in smooth motion | hold centroid | ready | [D] |
| 4 | **Scan-matched laptop mode** (lower its line rate) | scan speed, in hardware | 120 Hz, 2720 × 1725 at 563.04 MHz; panel acceptance unknown. The panel already runs two line rates (60 Hz: 108 kHz; 240 Hz: 432 kHz), so it may take a third (207 kHz) | [L] timings, [D] |
| 5 | The tool's existing "same totals" 120 Hz recipe | scan speed | 7.41 ms; leaves ±0.19 ms | [L] plan output |
| 6 | Faster scan on the external (large vertical total, "QFT") | scan speed | the external already scans at its 100 Hz line rate; faster needs a panel rated above 100 Hz and no frame buffer in the scaler. Unlikely on a budget 100 Hz IPS | [S] Blur Busters |
| 7 | Phase lock by variable refresh (follower) | tick remainder, crisply | NVIDIA reports it not possible on either screen. Untested: in hybrid mode the AMD 610M would usually drive the laptop panel [K], and the panel's EDID declares 60–240 Hz Adaptive-Sync. But VRR keeps the 3.7 ms scan, and the two screens would sit on two GPUs | [L] prior research, [K], [D] |
| 8 | Hardware genlock, raster lock, Surround | phase | Quadro Sync boards or identical timings only | [V] prior research |
| 9 | Re-roll the phase by repeating a mode change | phase | about 10 tries to land within ±0.1 ms of 2.08 ms classes, then drift undoes it in minutes unless both pixel clocks come out exact | [D] |
| 10 | Pick pixel clocks the GPU makes exactly | drift | measure after the 120/96 modes exist | [D] |
| 11 | Laptop imitates the external's pixel response (from measured curves) | response | needs a light probe | [D]; GPU-side response control has precedent in the Oculus runtime's overdrive shader [S] |
| 12 | Extra overdrive on the slow panel | response | risky: the monitor overdrives too, so overshoot | [S] |
| 13 | Laptop backlight at a flicker-free level, brightness matched in the picture | backlight timing | costs the laptop's black level (brighter backlight, brighter black) | [D] |
| 14 | Planner keeps fast motion off seams | hides the rest | exists (ADR 0009) | ADR |
| 15 | Linux (open NVIDIA mode-setting code, DRM) | timing control | ADR 0003 says Windows first; raster lock still needs identical timings | [K] |
| 16 | Light probe with two sensors sampled together | measures 6–8 at any height | DIY, about $10–40 | [S] |

## 5. The rolling blend

### 5.1 How it works

Take one film frame change, the next frame being due on the external at its refresh k. The external's row at height h switches at its own scan time: e(h) = top-row time of refresh k + (h / 268.1 mm) × 9.698 ms. Then add the panel-delay difference, external minus laptop, as tuned.

On the laptop, the row at the same height is drawn at times a, a + T, a + 2T, … (T = 4.17 ms): a(h) = a laptop refresh's top-row time + (h / 215 mm) × 3.704 ms. Take the laptop refresh that straddles the target, so a ≤ e(h) < a + T:

- before it: old frame;
- in it: new frame with share w = (a + T − e(h)) / T, old frame with share 1 − w, mixed in light;
- after it: new frame.

Example: the external's row switches 1.0 ms into a laptop refresh. That refresh shows 3.17 / 4.17 = 76% new and 24% old.

### 5.2 Why it's exact

The row's light-weighted switch moment is w × a + (1 − w) × (a + T) = a + T − w × T = e(h), whatever a, T and e(h) are. Over that refresh the row gives off exactly the light a row switching at e(h) would. So every part of the seam's timing on the cable (term 1, the tick remainder; 3, scan speed; 4, drift) goes to zero at once. This includes the 240 vs 96 Hz grids, and Setup B's 60 Hz LG.

What remains is the shape of the change. On the laptop, the change is spread over at most one 4.17 ms refresh; on the external, it is a single step. The external's own pixels take a few ms to finish changing (listed "5 ms (OD)" [S]), so on the glass the two sides' changes take similar time.

### 5.3 What you would see

At 24 fps a film frame lasts 41.7 ms: 10 laptop refreshes. With the rolling blend, at each change the laptop shows one refresh in which the mix share runs smoothly down the screen. There is no hard line anywhere. Then comes the new frame.

A moving edge shows a faint second copy for that one refresh. libplacebo's oversample mixer already makes this kind of one-refresh mix, for the whole screen at once, whenever frames and refreshes don't line up. Whether it shows at 240 Hz on these screens is X2's by-eye test.

The external is unchanged. Moving vertical edges on the laptop lean as much as on the external, because the laptop now changes at the external's 27.6 mm/ms. That is what keeps them straight across the bezel.

### 5.4 Precedents and what is new

- libplacebo master `src/renderer.c`, `pl_render_image_mix` [V]:
  - The default frame mixer is oversample. A frame's weight is `(end - rts) / vsync_duration`, its share of the refresh.
  - Frames are rendered once into cached textures, converted to linear light (`mix_csp.transfer = PL_COLOR_TRC_LINEAR`), then summed.
  - Each frame's weight is one number for the whole screen, uploaded as a dynamic shader constant (`SH_FLOAT_DYN(weight)`).
  - The oversample filter's threshold parameter snaps small weights to zero; set it to 0 for exact timing.
- The Blur Busters CRT beam simulator (Mark Rejhon and Timothy Lottes, MIT licence) [V]:
  - It makes a fast screen imitate a CRT's rolling scan row by row, using several real refreshes per imitated one. The README says "More native:simulated Hz ratio the merrier".
  - It redistributes brightness across refreshes to keep the light total [S].
  - It ships in RetroArch, TestUFO, ShaderGlass and others.
  - This is the same idea as the rolling blend: a finer screen reproduces another screen's light pattern in time.
- Cisco patent US11195452B1 (priority 2020-05-14, granted 2021-12-07, listed active to 2040) [V]:
  - On a stacked video wall of identical screens, each lower row of screens is delayed by whole frames, so the scan carries on from one screen to the next.
  - It is one wave across the canvas, at whole-frame steps. No per-row blending, no mixed rates.
- Not found: per-row mixing between screens with different scan speeds or rates (one extended search).

### 5.5 Changes to the engine

The rolling blend lives in the performer ([ADR 0022](../../docs/adr/0022-own-performer-on-libplacebo-and-ffmpeg.md)), not in mpv.

1. **libplacebo frame mixer: one weight per row instead of one per frame.**
   - Pass each frame's start and end times as uniforms, with a row-time function t(y) = top-row time + y × line time (for a screen turned sideways, t(x)).
   - In the final mix shader, each frame's weight becomes its share of [t(y), t(y) + T].
   - With the oversample kernel this is a clamp: a contained patch in `pl_render_image_mix` plus a field in `pl_frame_mix`. Other kernels would need a filter-table lookup; not needed.
2. **`pl_queue`:** ask for frames covering the whole rolling window, the laptop's scan plus one refresh: about 8 ms on 240 + 96 Hz, never more than 2 film frames at 24 fps.
3. **Pan mode:** add a per-row offset in the horizontal scaling pass, so a camera slide stays one resample. Design §5 already asks for per-row offsets. libplacebo's `distort_params` pass would be a second resample. A stopgap without a patch is horizontal bands, each drawn with its own source crop.
4. **Our per-monitor clock:**
   - top-row times from the scan-line fit (3.1);
   - each screen's line time and blank from its exact timing;
   - lag per screen;
   - the master's wave e(h) mapped through the room model's heights.

   The reference is the coarsest screen, which is already ADR 0021's master.
5. **Optional, later (needs measured curves):** a per-monitor pass that imitates the slower panel's pixel response, holding the previous output in a history texture.

### 5.6 Three screens, turned screens

- The coarsest screen is the reference. Every finer screen copies its wave where they meet, and the wave continues across the canvas.
- A screen in portrait scans sideways, so its wave runs left to right. The weights then depend on x as well as y. The same formula holds per pixel.
- Two coarse screens with different speeds next to each other, such as Setup B's 60 Hz LG beside another 60 Hz screen: either scan-match them with custom modes, or accept a 16.7 ms mix on one of them (likely visible). Otherwise the planner keeps fast motion off that seam.

## 6. Hardware layer: a scan-matched laptop mode

- **Target:** the laptop's rows move at the external's 27.64 mm/ms, so 215 mm takes 7.78 ms.
- **Which rate:**
  - 120 Hz keeps an even 24 fps pace (5 refreshes per frame). It has the same 2.083 ms tick classes as 240 Hz against 96 Hz.
  - 96 Hz would leave the phase entirely to luck, with one class.
- **Timing:** 2720 × 1725 total at 563.04 MHz gives 120.000 Hz exactly, on NVAPI's 10 kHz step. Line time 4.831 µs, scan 7.729 ms, blank 125 lines = 0.604 ms (the native blank is 0.463 ms). 2720 × 1700 at 554.88 MHz also works: scan 7.843 ms, ±0.03 ms.
- **What it fixes:** rows line up to ±0.02 ms down the seam. A 1 mm error in the external's height adds at most 0.03 ms from top to bottom.
- **What it costs:**
  - Pans move 120 times a second on the laptop instead of 240. That is closer to the external's 96, so the two sides look more alike.
  - The laptop's own moving edges lean like the external's.
- **What is left:** one even offset, at most ±1.04 ms after the aimed start. Close it with an even blend of at most 12.5%, or try re-rolls (lever 9).
- **Risk:**
  - eDP panels often accept only their listed timings. A community thread reports custom laptop modes failing behind an iGPU, or above a pixel-clock cap [S, old, Intel].
  - Here the panel is on the NVIDIA GPU and already runs 108 and 432 kHz line rates.
  - `custom-refresh-mode.exe list DISPLAY1` reads no saved modes, and `plan DISPLAY1 120` reads the current timing (2720 × 1800, 1175.05 MHz) [L]. Neither tells whether a trial is accepted.

## 7. Proposed doc changes (not applied)

- **ADR 0021 (how it works):**
  - "A refresh's picture time is when its top row is drawn: the blank start plus the blank length, from a scan-line fit over picture rows. Never the vblank signal."
  - "In smooth motion each row is drawn for the middle of the time its light stays up." This refines "for the moment its own light appears".
- **ADR 0020 / 0021 (what you see; needs your OK):**
  - "Every row changes picture at the light-weighted moment of the reference screen's row at the same height. Finer screens split the straddling refresh by time share, in light."
  - This replaces "aimed at the row where things cross the seam" and the "remainder that grows down the screen".
- **Spike code:** `per-monitor-presenter.cpp` and `seam-tuner.cpp` time refreshes from `WaitForVBlank`, so they miss the blank term. The spike report's 96 and 72 Hz offsets are blank offsets (3.1).

## 8. Experiments, cheapest first

| # | What | Who and cost | Pass test |
|---|---|---|---|
| X1 | Fix the time model in the presenter and the tuner (top-row times from the scan-line fit; blank lengths; middle-of-refresh drawing). Rerun the aimed start at 96 Hz and log top-row and mid-height offsets | agent; the run takes over both screens for about 30 s (your usual approval) | logged mid-height offset matches the prediction within 0.05 ms |
| X2 | Seam tuner "film steps". Anchor shown first: a tall white bar crossing a fake 20 mm bezel inside the laptop screen, in 24 fps steps; both sides are one panel, so this is what zero looks like. (a) Real bezel, today's aimed row against the rolling blend, random order, at 300 then 600 mm/s: "1 or 2: in which do the bar's top and bottom come out of the bezel at different moments?" (b) Laptop alone, the rolling blend against the plain switch: "1 or 2: which shows a faint double edge?" | agent build; you, about 10 min, 10 trials per pair | (a) at least 9 of 10 right: the scan term shows and the blend fixes it; at most 6 of 10: not visible at that speed. (b) at most 6 of 10: the blend can't be seen (as in phase 01, step 2) |
| X3 | Scan-matched laptop mode: add an exact-timing option to `custom-refresh-mode`, put the confirmation dialog on the external (the laptop may be dark), keep the 15 s auto-revert. Then measure the 120/96 head drift for 10 min | your approval for a laptop display-mode trial; up to 15 s of dark laptop screen | normal picture; Windows reports 120.000 Hz; scan-line fit shows 1725 lines at 4.831 µs; drift ≤ 1 µs/s |
| X4 | Backlight flicker: phone photo of a white screen at 1/2000 s or faster, at the brightness used for matching | you, 2 min | no dark bands |
| X5 | Light probe: two photodiodes, one each side of the bezel at the same height, sampled on one clock at 20 kHz or more | parts and assembly (optional) | measures the seam offset in light at any height, each panel's delay and response; the target reads 0 within 0.1 ms |
| X6 | Laptop imitates the external's pixel response | only if X5 shows a response gap above about 0.5 ms | X5 reads 0 on dark and light transitions |

## 9. Sources (fetched 2026-10-11)

- libplacebo `src/renderer.c` (master): https://raw.githubusercontent.com/haasn/libplacebo/master/src/renderer.c
- Blur Busters CRT beam simulator: https://github.com/blurbusters/crt-beam-simulator ; overview page (403 to the fetcher, snippet only): https://blurbusters.com/crt ; news [S]: https://www.timeextension.com/news/2025/01/tech-boffins-are-simulating-the-raster-scanning-of-old-school-crts-on-modern-hardware
- Quick Frame Transport / large vertical totals [S]: https://blurbusters.com/viewsonic-xg2431-analysis-a-blur-busters-fans-deep-dive-of-motion-blur-reduction/5/ ; https://forums.blurbusters.com/viewtopic.php?p=70303
- Scan skew [S]: https://forums.blurbusters.com/viewtopic.php?p=27010 ; https://screenhz.com/scan-out-skew/
- Cisco, "Sub-frame synchronization of multiple displays with independent vertical refresh rates", US11195452B1: https://patents.google.com/patent/US11195452B1/en
- Oculus GPU overdrive (staff forum posts) [S]: https://communityforums.atmeta.com/discussions/dev-pcvr/ghosting-in-oculus-demos/285649 ; https://forums.unrealengine.com/t/black-smearing/14819
- Brompton Tessera genlock phase offset [S]: https://dynamo-led-displays.co.uk/genlock/ ; https://theasc.com/articles/brompton-vp-toolbox-led-processing
- Beam racing, strip rendering in VR [S]: https://blog.imaginationtech.com/reducing-latency-in-vr-by-using-single-buffered-strip-rendering/
- Photodiode lag testing [S]: https://psychtoolbox.discourse.group/t/cheap-method-for-measuring-monitor-input-lag-and-response-time/3492
- External, E-Dra EGM22F100H [S]: https://www.phucanh.vn/man-hinh-e-dra-egm22f100h.html ; https://hoanghamobile.com/man-hinh-may-tinh/man-hinh-edra-egm22f100h
- Laptop panel AUO B160QAN02.3, PWM listing [S, page 403]: https://laptopmedia.com/fr/screen/b160qan02-3-auo7db2/
- Custom laptop modes failing [S, old, Intel]: https://community.intel.com/t5/Graphics/9024844-Custom-resolution-not-working/m-p/306302
- Project: ADRs 0009, 0016, 0017, 0020–0022; [presentation research](researcher-261011-1247-per-monitor-presentation-and-sync.md); [per-monitor spike](spike-261011-1247-one-window-per-monitor.md); [room research](researcher-261011-1247-room-model-and-display-settings.md); [design](design-261011-1247-m1-architecture.md); [red team](red-team-261011-1247-m1-architecture.md); `spikes/per-monitor-presenter/`; `spikes/custom-refresh/`.

Local [L]: Windows 11 Pro 25H2 (26200), NVIDIA driver 617.42, laptop at 240 Hz and external at 100 Hz during the probes. Scripts in the session scratchpad: `scanline_probe.py`, `scanline_shape.py`, `scanline_trace.py` (read-only D3DKMT probes) and `seam_timing_budget.py` (the numbers in 3.2 and the exactness check).

## 10. Unresolved questions

1. Is the rolling blend's one-refresh mix visible at 240 Hz on real film content? (X2)
2. Does the laptop panel accept a 207 kHz line rate (120 Hz scan-matched)? (X3)
3. Does the external's scaler pass rows straight through, so that its light follows the 9.7 ms signal scan? Or does it buffer a frame, changing its delay and maybe its scan? (X5, or a phone at 960 fps)
4. How far apart are the two panels' pixel responses, by colour? (X5)
5. Does the laptop backlight flicker at the matched brightness, and is the flicker tied to the refresh? (X4)
6. After the 120/96 modes exist, are their pixel clocks exact (no drift), and is the phase after a mode change random?
7. In hybrid mode, does the AMD 610M offer variable refresh on the laptop panel for windowed apps (lever 7)? Only worth checking if a crisp tick ever matters after the blend.
