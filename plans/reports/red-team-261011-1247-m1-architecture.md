---
title: "Red-team review: M1 architecture design"
date: 2026-10-11
type: review
reviews: "design-261011-1247-m1-architecture.md; ADRs 0020, 0021, 0022, 0023; spike-261011-1247-one-window-per-monitor.md; researcher-261011-1247 reports (presentation and sync, engine and picture path, room model and display settings)"
---

# Red-team review: M1 architecture design

Advisory only; nothing in the repo was changed, no screen was taken over, no setting touched. Tags: **[V]** read this pass in a project report, ADR, spike source or a fetched page (a report's own measurement is cited as that report's); **[D]** my reasoning or arithmetic; **[K]** prior knowledge, not re-verified. Terms as in the design: a *refresh* is one chance for a screen to show a picture, *T* its period; the *seam* is the bezel; the *master* is the monitor whose refresh count is the film clock; a *follower* is any other monitor.

Question map: Q1 zero delay → findings 2, 3, 5, 6, 7, 11. Q2 engine → 8. Q3 clock → 1, 7. Q4 unsettled hard choices → 4, 9, 10, 12. Q5 → "Missing experiments".

## Verdict: go, with changes

- The three load-bearing choices hold on the evidence. One window per monitor: both windows in hardware overlay at once, even pace on both, 0.28 ms apart [V spike]. Film clock counted on the master's refreshes: the free-running clock stumbled 57 of 380 frames, the counted one none [V spike line 46]. Own performer: stock mpv is single-window, single-swapchain, single vsync, and libmpv's render API is OpenGL/software only [V engine research Q1]. Nothing here says rethink.
- Change before building: (1) the clock model is under-specified: no epoch for seeks, pauses and master changes, no hysteresis at the nearest-refresh boundary, no fallback when no monitor is a whole multiple of the file rate (25p, VFR, the external at 100 Hz); (2) "target-time present" is written as a precision lever, but on a fixed-refresh monitor it is only another way to pick a refresh; only VRR is finer than ±T/2, and VRR reads "not possible" on both screens today [V]; (3) one device per *output adapter*, with frames crossing adapters, is a day-one shape for a hybrid laptop and Setup B, not a Setup B footnote.
- Change before the user's custom-mode session: build 96/72/48 Hz timings at the native pixel clock with a longer vertical blank. Scan time, the largest seam term on slow screens, then stays at the native value instead of growing with the lower rate: LG 4K at 48 Hz scans in 16.4 ms, not 20.0 ms [D].
- Change the by-eye order: match brightness first, then tune lag. Perceived timing shifts about 15 ms per tenfold luminance change [K], so a 2× brightness mismatch moves the "lag" the user tunes by ~4 ms; pixel-response asymmetry adds a contrast-dependent term. One phone slow-motion clip tells panel from eye.
- Own performer in C11: right call, but two of the stated reasons are weak and the 6-month regret sits in the file matrix mpv covers and we will not (interlaced, ordered-chapter MKV, PGS, passthrough, hot-plug). Write that matrix now; keep stock mpv as a per-file fallback engine, not only a demo fallback.

## Findings, ranked by severity

### 1. Clock model: no epochs, no hysteresis, no fallback for non-multiple content (high)

- **Claim.** ADR 0021 and design §4 define the steady state only.
- **Why it matters.** Every break (seek, pause, resume, master change at a cut, modeset) re-anchors the film clock. The follower presents one refresh ahead of the master, choosing its frame from a *predicted* master grid (`spikes/per-monitor-presenter/per-monitor-presenter.cpp:196-198`: frame for `next_after(now + 0.5 ms)` via `master_switch(n)`) [V]. A global re-anchor applied while that present is in flight puts the two screens on different frames for one refresh: the seam flicker the user calls unwatchable. Second, the nearest choice flips when the relative phase sits within jitter of ±T/2; the 0.5 ppm drift walks it through that band once per ~2.3 h [V presentation research, addendum 3], and the spike shows what boundary flip-flop looks like (57 stumbles) [V]. Third, "master = slowest monitor whose rate is a whole multiple of the playback rate" is undefined for 25p, 29.97 telecine and VFR anime, and for Setup A until 96 Hz exists; as written the ADR cannot play those files.
- **Evidence.** Spike source as cited; design §6 cue sheet has "per film: display modes and the master" but no per-break rule [V design line 100]; the pacing research lists 25p on 240 Hz as 9.6 per frame [V].
- **Change.** (a) Every break is a frame change keyed to a master refresh index K, chosen at least (follower lead + render time) ahead; followers switch on their refresh nearest `time_of(K)` with the same lag/row correction; all frame queues reset on the same K. (b) Hysteresis on the follower's refresh choice: keep the current side until the other is nearer by ~0.3 ms, release only at a cut; deferral costs 0.3 ms per 10 min at 0.5 µs/s [D]. (c) Film time is a *time*, t = t0 + s·k·T_master (k = master refresh count, s = speed nudge), frames chosen by PTS against it (`pl_queue` does exactly this [V engine Q2]); hold counts fall out, VFR works, and "whole multiple" becomes a per-file *mode choice*, not a clock precondition. (d) Define the ladder when no multiple exists: custom mode → VRR → speed nudge within ±1 % (24→25 is 4 %, out) → the offending monitor mixes frames (oversample) or carries surround only (ADR 0015 allows the picture on one monitor). Which rung is the default is a what-you-see call for the user; recommend "picture on the monitors that have a multiple, else mix".
- **Also.** Audio: fixed delay to the master's light (vblank + master lag), never per shot; the other monitor's lag difference stays inside lip-sync tolerance [K]. Device-clock drift against the GPU is absorbed by the resampler; if audio leaves over the external's HDMI its clock is already derived from that head's pixel clock [K].

### 2. "Exact-time present" is not a precision lever on a fixed-refresh monitor (high)

- **Claim.** ADR 0021 and design §4: where `SetTargetTime` or `VK_EXT_present_timing` exist, "the follower presents at the exact moment instead of the nearest refresh".
- **Why it matters.** On a fixed-refresh display any present lands on a refresh boundary; a target time only selects which one. Both APIs are "not before the target" [K], i.e. the one-sided rule, which the spike measured as an arbitrary steady offset (−1.1 ms in one run, +2.2 ms in another) [V spike line 45], worse than nearest unless the app aims the target itself. Only VRR moves the refresh. NVAPI reports VRR possible = 0 on both screens [V presentation 2.1]; the "follow" mode tore on the fixed 240 Hz grid [V spike].
- **Change.** Reword to "VRR follower where VRR is on; otherwise nearest refresh". Drop present-timing work from P1 until the Control Panel check shows VRR can be enabled. If it cannot, Setup A's floor is ±2.1 ms plus row skew, to be judged by eye (finding 11), not engineered around.

### 3. Custom-mode recipe lowers the pixel clock and slows the scan (high, cheap to fix)

- **Claim.** The research builds 96 Hz at 214.08 MHz (scan 10.09 ms) and the LG at 48 Hz at 475.2 MHz (scan 20.0 ms) [V presentation Q3 table].
- **Why it matters.** The scan-skew term S = T_scan(slow) − T_scan(fast) is the largest residual on slow screens (±6–8 ms at the extreme rows on Setup B) [V]. Scan time = active lines ÷ line rate. Keep the line rate at the native mode's and put the extra period into vertical blank.
- **Evidence [D].** LG: 4400×2750 at 580.8 MHz = 48.000 Hz exactly (on NVAPI's 10 kHz grid), 132 kHz lines (inside its 30–135 kHz range [V room 3.2]), scan 2160/132 kHz = 16.4 ms, −3.6 ms against the proposed timing. External: 2000×1160 at 222.72 MHz = 96.000 Hz, scan 9.70 ms (−0.4 ms). Never pick the LG's built-in 4K24 (VIC 93): 54 kHz lines, scan 40 ms.
- **Change.** Build the timings this way in `spikes/custom-refresh/` before the user's approval run; verify with CCD `activeSize/totalSize` and a 10 s present log. Risk: some scalers reject long blanks (CRU users do this routinely [K]); keep the lower-clock variant as plan B.

### 4. Device topology: one device per output adapter from day one (high)

- **Claim.** Design §4: "hardware decode on the GPU that drives the monitors" (singular); Setup B wiring listed as open; the engine research mentions cross-adapter copies as a Setup B case [V engine §4].
- **Why it matters.** The laptop panel is on the NVIDIA LUID *today* [V room 3.1], which means dGPU/MUX mode; the pacing research already notes it holds "as long as the laptop stays in dGPU mode" [V pacing §2]. On most hybrid laptops, and this one in hybrid mode, the panel hangs off the iGPU (AMD 610M). A window gets independent flip only from a device on the adapter that owns the output [K]. D3D11 has no cross-adapter shared textures: frames cross by CPU copy (1080p NV12 at 24 fps = 75 MB/s; 4K = 300 MB/s, trivial) or by decoding again on the second GPU (the 610M decodes H.264/HEVC/AV1 [V engine Q5]) [K/D]. "Almost anyone's desk" includes USB-C screens on the iGPU.
- **Change.** Presenter (module 5) owns one device per adapter; decode (modules 3–4) hands off CPU-visible frames when adapters differ; the room model records the adapter per monitor at launch and the performer re-plans when it changes. Related: libplacebo's D3D11 `pl_gpu` is not thread-safe [V engine Q2], so "one loop per monitor" means either one device per monitor plus shared textures (NT handle, keyed mutex) or one render thread serving every swapchain (`WaitForMultipleObjects` on the waitables); Vulkan's `pl_gpu` is thread-safe. Decide in P1 on both backends (finding 8).

### 5. The lag number is not a constant: pixel response and luminance-dependent perception (medium-high)

- **Claim.** The room model stores one lag per monitor; the seam tuner tunes it on 40 %-grey stripes (`seam-tuner.cpp:69-72`) [V].
- **Why it matters.** Rise ≠ fall, overdrive and VA black smear move the visible edge of a transition by milliseconds depending on contrast and direction [K]. The eye adds its own term: dimmer stimuli are seen later, about 15 ms per tenfold luminance (Pulfrich/Hess) [K], so a 2× brightness mismatch shifts the tuned lag by ~4 ms, and the tune is only valid at the brightness it was done at. ADR 0020 says "as seen", so the tuner absorbing both is correct, but only for its pattern at that brightness.
- **Change.** Session order: brightness match → gap and height → lag. Tune lag on film-like contrast, not white-on-black. Store with the lag the backlight levels it was tuned at; retune when they change. One phone slow-motion clip (≥ 240 fps) of a bar crossing at the tuned values separates panel from eye and settles scan direction (the row model assumes a single top-to-bottom scan and no frame buffer in the external's scaler [V presentation Q3]). Expect a residual on extreme-contrast crossings; report it, do not chase it.

### 6. PWM and persistence differences at the seam (medium)

- **Claim.** The brightness match lowered the laptop's backlight [V mixed-rate spike]; a vendor database lists PWM dimming at 240 Hz for this panel [V laptopmedia listing, unverified]. In pan mode the sides differ in sub-steps per frame (10 vs 4) and in hold time (4.2 vs 10.4 ms).
- **Why it matters.** PWM at reduced brightness strobes a moving bar on one side only (phantom array), confounding every "flicker" judgment. Persistence and sub-step asymmetry are residuals no timing correction removes.
- **Change.** Check PWM with a phone at high shutter speed at the matched brightness; prefer dimming the external (DDC/CI) over the laptop. Accept the persistence asymmetry for now (the user passed pan mode at 240 vs 100 Hz [V mixed-rate spike]) but keep it on the residual list. Also carry the tuner's row-shear into pan mode: design §5 gives one offset per refresh, yet rows light at different times (3 mm skew top to bottom at 300 mm/s on a 10 ms scan) [D]; the tuner's R toggle tells whether the user sees it.

### 7. Three screens, portrait, stacked: per-seam scheduling and scan direction (medium)

- **Claim.** Two followers of one master can sit T_a/2 + T_b/2 apart; the research says choose the master per seam [V presentation addendum 3]. The design's room contract lists scan fraction but not scan direction or rotation [V design line 97].
- **Change.** Schedule each follower against its adjacent neighbour's *chosen* refresh, outward from the master (a chain), not against the nominal master time; pick the aim row y* per seam. A stacked seam has known crossing rows (y* = 1 on the upper screen, y* = 0 on the lower), so it can be aimed exactly. Record scan direction per monitor (portrait = sideways skew). The cue sheet carries, per film, the master and the aim row per seam; "modes and the master" alone is too coarse for three screens.

### 8. Engine: C11 fine, two reasons weak, backend default untested, regret in the file matrix (medium)

- **Claim.** ADR 0022 picks C because "MSVC's C++ rejects its macros" and "no maintained Rust binding exists"; "license stays open" is a stated benefit; the research defaults to D3D11 [V engine Q5, §8 q3].
- **Why it matters.** clang, the build route anyway, accepts compound literals in C++ [K]; a Rust bindgen is a day's work [K]; both are taste calls, not blockers. "License open" is weak for a free, non-commercial project; the strong reasons are per-monitor control and macOS. The real risk of C with agent-written code is memory and thread safety in a five-thread process on untrusted media. The 6-month regret is what mpv has and the 7.6k-line estimate does not: interlaced sources (anime TV/DVD rips), ordered-chapter MKV (FFmpeg trac #3123 and #5698 open as of their last activity [V]; FFmpeg plays the segment linearly [K]), PGS/VobSub, audio passthrough, hot-plug, HDR passthrough. Backend: D3D11 is the measured presentation path, but its `pl_gpu` is single-threaded and the D3D11VA import is ours to write [V]; Vulkan is thread-safe with zero-copy Vulkan Video through libplacebo's helpers and present timing [V], but Vulkan presentation reaching independent flip on Windows depends on the driver's "layered on DXGI swapchain" mode, a control-panel choice outside the app [V community reports] [K].
- **Change.** Keep C11 with guardrails: ASan/UBSan builds under clang, single-owner threads with message queues, `-Werror`, a fuzz pass on demux and cue paths. Write the supported-file matrix now and route anything outside it to stock mpv plus the proven shader path (a per-file fallback engine, not only a demo fallback). Set the B2 gate on that matrix, not on "3 weeks". Run P1 on both backends; libplacebo makes that cheap.

### 9. Data contracts: timeline, surface coordinates, shot identity (medium)

- **Claim.** Cue sheet v0: "start halfway between frames on the rebased timeline"; placement "centre and height in mm on the surface" [V design line 100].
- **Why it matters.** "Rebased" needs a definition that survives edit lists, negative start PTS and ordered chapters (per segment). The curve surface needs a surface coordinate (u = arc length along fold or curve, v = height) so cues do not change when the user flips fold/curve. The room schema is +y up [V room 2.3]; the seam tuner and Windows are y-down [V `seam-tuner.cpp:52`]: one convention plus a round-trip test. Shot identity across versions by start time needs the rule "a new version never moves a boundary before the horizon". Slide paths in source pixels per frame need the performer to convert through room geometry per monitor.
- **Change.** Put these five rules in the phase 05 schema before the prototype reads it.

### 10. Which process calls `ChangeDisplaySettingsEx` (medium-low)

- **Claim.** ADR 0023: "the player itself makes refresh changes"; the design keeps the display manager with setup/launcher and the performer in C.
- **Why it matters.** The revert is tied to the *calling* process's lifetime: a short-lived setter's change reverts when it exits [V room 4.5]. Saved custom modes persist in the driver.
- **Change.** The performer process owns the modeset (a few lines of C); the launcher owns snapshot and guardian. Add a "remove all panorama custom modes" command. Keep the lock, sleep and hot-plug tests the ADR already lists.

### 11. Threshold evidence is thin; sequence the levers and fix one wording (low)

- The only study starts at 8.3 ms and scores it near-imperceptible with a gap wider than 0.5° [V presentation Q4]; the user felt delay at a 0.3–2.2 ms signal offset [V ADR 0020]. So the felt delay is lag, gap or scan, not quantisation. Order: tuner → camera → threshold ladder; spend on sub-millisecond levers only if the ladder asks.
- Design §1 item 3 states "frame changes 0.28 ms apart" as a result; the spike calls it luck of the phase and the guarantee ±2.1 ms [V spike line 20]. Cite the bound.

### 12. Render lead is fixed at one refresh (low)

- Waitable object with maximum latency 1 means each render must finish inside one refresh (4.17 ms at 240 Hz) [V spike source]. A 4K EWA render on the iGPU will not. The film clock is deterministic, so rendering N refreshes ahead costs nothing in correctness; make the lead configurable per monitor.

## Missing experiments (cheap, in the order to run them)

| # | Experiment | Settles | Cost |
|---|---|---|---|
| X1 | Rebuild the 96/72 Hz (and later LG 48 Hz) timings at the native pixel clock with extended vertical blank; verify by CCD totals and a 10 s present log | finding 3; the scan-skew term | 0 extra user time: same approval run |
| X2 | Phone slow-motion (≥ 240 fps) of the seam tuner at the tuned values: bar crossing, grey field at matched brightness | findings 5, 6: light timing, scan direction, PWM | 30 min |
| X3 | By-eye session re-ordered: brightness match (dim the external if DDC/CI works) → gap/height → lag; then change brightness deliberately and retune lag once | finding 5: size of the perceptual term | 10 min extra |
| X4 | `nearest_refresh_sim.py` with measured jitter and 0.5 ppm drift: count flip-flops near ±T/2 with and without hysteresis; feed a 25p trace and a VFR PTS trace from a real anime MKV (`ffprobe -show_frames`) | finding 1 (b), (c) | 30 min, offline |
| X5 | P1 twice: D3D11 swapchain and Vulkan swapchain on NVIDIA, PresentMon present mode per window; plus one deliberate "wrong adapter" run (the external's window on the AMD device) to price the cross-adapter case | findings 4, 8 | half a day |
| X6 | In P3: seek while a follower present is in flight; pass = both screens switch on the same master refresh (barcode per monitor), 50 seeks | finding 1 (a) | adds 1 h to P3 |
| X7 | P0 file-matrix smoke: ordered-chapter MKV, interlaced sample, PGS subs, 10-bit HEVC, VFR MKV, through FFmpeg 9 and 8.1 | finding 8 | 1 h |
| X8 | Setup B, when connected: room probe, adapter per monitor, LG 48 Hz fast-scan acceptance | findings 3, 4, 7 | needs Setup B |
| X9 | 10-minute drift run on 240 + 96 after the custom mode exists (already planned) | finding 1 (b) | keep |

## Unresolved questions

1. Can G-SYNC be enabled for the laptop panel in windowed/borderless mode on driver 617.42? If not, Setup A's precision floor is ±2.1 ms plus row skew for good (finding 2).
2. Does the external accept a 96 Hz timing with a 1160-line total, and the LG a 2750-line total at 48 Hz (finding 3)?
3. Which rung of the non-multiple ladder is the default: mix frames on the offending monitor, or keep the picture off it (finding 1 (d), a what-you-see call)?
4. Does FFmpeg 9's Matroska demuxer still ignore `EditionFlagOrdered` (tickets open as of last activity; verify in source during P0)?
5. Does a Vulkan swapchain reach independent flip on this driver with default control-panel settings, and on the AMD 610M at all (finding 8)?
6. Does the external's scaler buffer a frame or scan top to bottom (camera, finding 5)?
7. Which process is "the player" for ADR 0023: performer or launcher (finding 10)?
8. Setup B: adapter per monitor, orientation, the third screen's timing range (finding 4, 7).

Status: DONE_WITH_CONCERNS
Summary: The architecture holds on the evidence (go), but the clock model needs epochs, hysteresis and a non-multiple fallback, "target-time present" must be demoted to "VRR only", custom modes should keep the native pixel clock, device topology must be per output adapter from day one, and the by-eye session should match brightness before tuning lag; the own-performer regret risk is the unsupported-file matrix, best covered by keeping stock mpv as a per-file fallback.
