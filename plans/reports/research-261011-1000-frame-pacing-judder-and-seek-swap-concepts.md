---
title: "Research — frame pacing, judder and the seek–swap race, at concept level"
date: 2026-10-11
type: research
answers: "spike-261011-0950 unresolved questions 1 (even pace on Setup A) and 2 (seek during a swap)"
sources: "5 research calls: 1 source clone (mpv d94d095 + libplacebo 1dd0226, both 2026-10-10) + 4 web searches; plus read-only probes of this PC (EDID, CCD, EnumDisplaySettings, nvidia-smi)"
---

# Research: frame pacing, judder and the seek–swap race (concept level)

Started 2026-10-11 10:00, Asia/Bangkok. Nothing was changed on the PC; no spike trials were run.

## Contents

0. Terms
1. Answer
2. Setup A facts measured today
3. Reframing
4. Judder and frame pacing
5. Seek during a swap
6. Proposed rule changes (not applied)
7. Next experiments
8. Evidence
9. Unresolved questions

## 0. Terms

- **Frame**: one still picture of the film. Films carry 24 per second (23.976 for most files).
- **Refresh**: one chance for a monitor to show a picture. A 60 Hz monitor has 60 per second.
- **Pace**: how many refreshes each frame gets. 24 fps on 240 Hz: 10 each, even. On 60 Hz: 3, 2, 3, 2…, uneven ("3:2").
- **Judder**: steady motion that looks jerky because frames stay up for unequal times. Example: a slow pan over a skyline at 60 Hz steps 50 ms, 33 ms, 50 ms, 33 ms.
- **Temporal seam** (new term): the monitors switch to the next frame at different moments, or one paces evenly and the other doesn't. A car crossing the bezel then shows two different film moments on its two halves for a few ms per frame. This is the part specific to multiple monitors.
- **Shot / cut**: a shot is one unbroken piece of footage. A cut is where it ends and the next begins (a close-up of a face cuts to a wide street view).
- **Swap**: replacing the cue table (per-shot settings inside the shader) while the film plays.
- **Variable refresh (VRR)**: the monitor waits for the next picture instead of refreshing on its own fixed clock.

## 1. Answer

1. **The requirement is not one shared refresh rate.** Each monitor must run at a whole multiple of the film's *playback* rate. The monitors don't have to match each other. Setup A: laptop at 240 Hz (×10) plus the external at a custom 96 or 72 Hz (×4 or ×3) gives an even pace on both screens in the one spanning window. That holds only if Windows shows each monitor's latest picture at that monitor's own refresh, which is unknown on 25H2, so a camera test is needed. The external's EDID states 48–100 Hz, so 96 and 72 are inside its range. The "only common rate is 60 Hz" premise counted only built-in modes and required equal rates.
2. **The film's clock can drive the screens.** The laptop panel does variable refresh at 60–240 Hz (EDID, verified today). With variable refresh, a fixed-rate monitor can be the master and a variable-refresh monitor follows it in step: hardware-free sync, like the "genlock" signal video walls use. HDMI's QMS-VRR is the industry version of "screen follows the content rate". It needs one window per monitor, so it belongs to the product renderer, not the stock-mpv demo.
3. **Plan time like space.** How visible judder is depends on motion speed, brightness and frame rate. A published model scores it per shot, and most shots are near-static. The planner already works per shot, so the cue sheet can gain a time column: judder risk, the treatment to use, and which monitor sets the clock. Keeping both sides of a seam identical matters more than perfecting one monitor.
4. **Seek–swap: let the renderer decide "is this shot on screen?"** Today the planner guesses from a playhead read made in another process, while seeks go straight into mpv; that gap is the race. libplacebo shader storage keeps its contents across frames, and mpv keeps each shader file loaded until the video output closes (verified in source). So a new table can, on its first frame, pin the shot on screen to the value the old table was showing, and use its new values only from the next cut or seek. This is how a database lets a reader keep the snapshot it started with. The race closes by construction, and the planner no longer needs the playhead for correctness.
5. **One rule underneath all of it:** presentation changes only at a break: a cut, a seek, a pause or file start. Crops (ADR 0010), placement and size (ADR 0009, 0015), table refinements (spike) and any per-shot time treatment are all cases of it. One per-shot latch in the performer enforces every case.

## 2. Setup A facts measured today [V]

| | Laptop panel | External |
|---|---|---|
| Panel / link | AUO 0x7DB2, eDP, 2560×1600 | EGM22F100H, HDMI, 1920×1080 |
| Built-in modes at native resolution | 60, 240 Hz | 50, 59, 60, 75, 100 Hz |
| Exact current rate (CCD) | 240001/1000 = 240.001 Hz | 100/1 Hz |
| EDID stated range | 60–240 Hz | 48–100 Hz |
| Variable refresh in EDID | DisplayID "seamless timing change" flag set; FreeSync block 60–240 Hz | AMD FreeSync-over-HDMI block 48–100 Hz; no HDMI 2.1 VRR block |
| Driven by | RTX 5070 Ti Laptop, driver 617.42 | same adapter (same LUID) |

- Both screens hang off the NVIDIA GPU, so there is no cross-adapter copy as long as the laptop stays in dGPU mode. This closes part of synthesis spike S6.
- OS: Windows 11 25H2, build 26200.8875.
- NVIDIA's G-SYNC Compatible works over DisplayPort or HDMI 2.1 VRR, not over AMD's FreeSync-over-HDMI. So expect **no** variable refresh on the external from this GPU [K, not verified today; one-minute check in E1].
- The registry also holds an LG UltraFine 4K EDID (60 Hz, range 40–60 Hz), last seen on the same HDMI connector. It may be a Setup B monitor. 48 Hz (2×24) is inside its range.
- mpv paces a spanning window to the monitor it overlaps most (`MonitorFromWindow`, `w32_common.c`), at that monitor's exact CCD rate. Here that is the laptop [V source].

## 3. Reframing

### Inversions

| Assumption | Inverted | Holds where |
|---|---|---|
| All monitors share one refresh rate | Each monitor only needs a multiple of the playback rate | Spanning window if Windows samples per monitor (test E3); always with one window per monitor |
| The monitor clock is fixed; the film adapts | The film clock drives the monitor (VRR) | Laptop panel now; external not on this GPU/link |
| Smoothness is a global player setting | Smoothness is planned per shot and per region | Planner + content model (already per shot) |
| The planner must know the playhead to swap safely | The renderer knows what is on screen and decides when a table takes effect | Stock mpv via shader storage (feasible by source); trivial in own renderer |
| Judder must be removed | Judder must be invisible, or the same on every monitor | Most shots (static, dim); coherence beats per-monitor perfection |
| Every monitor carries the film | Only monitors carrying the picture need film pace; surround monitors carry soft light | ADR 0015 layouts, e.g. film on the external, laptop as surround |

### Borrowed from other fields

- **Orchestra.** Monitors are players with different note grids; the film is the beat. One shared grid (60 Hz) is the conventional answer. Instead, each player can subdivide the same beat (240 = 10 per beat, 96 = 4 per beat), or follow the conductor's baton (VRR).
- **Video walls and broadcast.** Pro walls lock every tile to one sync signal (genlock). The consumer equivalent: one GPU drives both outputs from one reference clock, which is true here. With variable refresh, a present starts a refresh, so lining up the presents lines up the screens [D].
- **Audio sample-rate conversion.** Converting 44.1 to 48 kHz by repeating samples is crude; real converters filter. 3:2 is repeating. Blending (mpv `oversample`, also called SmoothMotion) is a short filter. Motion-compensated synthesis is the ideal filter, with its own artifacts. Picking per shot is normal practice in audio, not a hack.
- **Colour grading → motion grading.** Colour is set per shot and shipped as metadata. Motion can be too. Dolby patents cover judder-visibility metadata and "dynamic persistence" [S titles; assignee K], and a 2021 thesis drives per-pixel frame rates from a judder model. Our cue sheet is that metadata channel.
- **Databases (snapshot isolation).** The cue table is a database, swaps are writes, and the shot on screen is a reader that keeps the version it started with. That is the per-shot latch in section 5.

### One pattern, five places

The same rule appears five times. Crops change only at cuts. Placement and size change only at cuts. Refinements never change a shot on screen. Time treatment is per shot. Display-mode switches happen only at start, seek or pause. Abstract form: *presentation changes only at breaks.* One latch enforces it.

### Extremes

- **Refresh goes very high (1000 Hz):** any film rate fits and blending becomes invisible. So the enemy is a *low* refresh rate, not a mismatch. Never lower a fast monitor to find a common rate; keep each at its highest multiple.
- **Ten monitors (a wall):** no common rate exists. Only "multiples + one content clock + aligned phase" scales, and that is the product architecture.
- **Constant scrubbing:** any design where the planner reads the playhead has its race window open all the time. A render-loop latch is unaffected.
- **A 10-minute single take:** the latch keeps the cautious value for 10 minutes, which policy accepts. Idea [D]: the planner could mark masked moments inside long shots (a fade to black, a flash, a whip pan) as allowed change points.

## 4. Judder and frame pacing

### Lever map: which clock bends

| Lever | Mechanism | Setup A | Stock-mpv demo | Evidence |
|---|---|---|---|---|
| Display: built-in multiples | switch each monitor to a multiple (ADR 0005) | laptop 240 ✓ for 24p; external has none for 24p | yes | [V] modes |
| Display: custom modes | add 96/72 Hz (90 for 30p) to the external once; store in the room model | inside EDID range; untested | yes | [V] range, [D] |
| Display: VRR follows the film | the monitor refreshes when the frame arrives | laptop ✓ 60–240; external likely ✗ | no, needs one window per monitor | [V] EDID, [K] NVIDIA |
| Film: speed nudge | 23.976 → reference-locked (+0.1%), audio resampled | in use (`display-resample`) | yes | [V] docs |
| Film: blending | `oversample` blends only the refresh that straddles a frame change; mpv's default `tscale` | fallback at 60 Hz | yes; per-shot work stays exact in OUTPUT (spike) | [V] source, docs |
| Film: pan synthesis | for shots that are a pure slide (landscape pans, anime background slides), make in-between positions by sub-pixel shift: exact, no guessing | needs per-refresh timing | no: shaders get `PTS` plus HDR fields, no refresh time | [D] new; [V] param list |
| Film: motion-compensated synthesis | estimate motion, invent in-between frames | GPU can | external tools | named generically, not verified today; changes the film look |
| Plan: per-shot judder risk | score speed × brightness × frame rate | content-model work | n/a | [S] Chapiro 2019 |
| Plan: clock follows the picture | the monitors carrying the picture set the clock; surround is softened | ADR 0015 often puts the film on the external alone | partly | [D] |
| Plan: per-region treatment | picture vs surround, pan vs subject | later | partly | [S] thesis 2021 |
| Presentation: one window per monitor | each monitor its own rate and filter, presents aligned | product | no | synthesis S9 |

### Setup A paths, ranked

24p content locked to the laptop's 240.001 Hz. Each figure is how many refreshes a frame stays up.

| Path | Laptop | External | Seam | Cost |
|---|---|---|---|---|
| **A.** 240 + custom 96 (or 72), one spanning window | 10 each, even | 4 (or 3) each, even | offset up to ≈ one external refresh (≤ 10.4 ms) + compositor, steady | custom mode once; Windows behaviour unknown |
| **B.** 60 + 60, `oversample` blending | 2.5 average: 2 clean + half of one blended refresh | same | same pattern both sides; offset ≤ 16.7 ms | laptop drops from 240; a 50/50 double image for 16.7 ms on every other frame change, visible on high-contrast pans |
| **C.** 240 + 100 native | 10 each | 5,4,4,4,4,4: one frame in six stays 25% longer (a 4 Hz stumble) | one side smooth, the other slightly uneven: incoherent | none |
| **D.** 60 + 60, no blending | 3,2,3,2: classic judder | same | coherent but juddery | none |
| **E.** Product: one window per monitor; external fixed at 96 is the master, laptop VRR follows (presents at the external's refresh, 96 or 192 Hz) | even | even | aligned to driver jitter (~ms) [D] | own renderer |

- **Match the playback rate, not the file rate.** In a spanning window, `display-resample` locks 23.976 content to the reference monitor (laptop: 24.0001 fps). The external must be a multiple of *that*: 96.000, not 95.904 [D]. If the custom mode lands exactly on 96.000 Hz, the two clocks differ by 4 ppm, giving one 10 ms stumble about every 40 minutes [D, from CCD rates].
- Prefer 96 over 72: closer to the panel's native 100 Hz, and a finer grid means a smaller seam offset.
- With the film on the external alone (ADR 0015), the external's pace is what matters. Its custom 96 Hz mode is the single highest-value change on Setup A.
- 25 fps content: external 50/75/100 ✓; laptop 240 ✗ (9.6 per frame). Use laptop VRR at 200 Hz, or play film-sourced 25p at 24 (undo the European TV speed-up). 30/60 fps content: laptop 240 ✓, external 60 or custom 90 ✓.
- How Windows handles one window across different rates is the open unknown. Community reports conflict, and no Microsoft source for 24H2/25H2 turned up today [S]. Measure it.
- The spikes' screen capture (Windows Graphics Capture) sees the compositor, not the panels. Per-monitor pace and seam offset need a camera.

### The temporal seam

- Sources of offset: refresh timing (up to one refresh of the slower monitor), compositor delay, panel processing, and scan speed. Top-to-bottom scan takes ≈ 4 ms on the laptop at 240 Hz timing vs ≈ 10 ms at 96 Hz, so the offset grows down the screen [D].
- Effect: for the length of the offset in each frame, the two halves of a moving object show different film moments. A slow pan at 960 px/s (2 s across a 1080p screen) moves 40 px per frame. With a 10 ms offset, the halves disagree by 40 px for 10 of every 41.7 ms [D]. Whether the bezel hides this is unknown, and no study was found, so judge it by eye and camera.
- Levers: aligned refresh (VRR follower, product); a steady offset (one GPU already gives that); a planner cost against spanning layouts for fast pans (a cost term, not a rule).

### Planning time per shot

- Inputs:
  - **Content model, per shot:** global motion speed and direction, whether the shot is a pure slide, and mean brightness.
  - **Room model:** viewing distance (to turn px/s into degrees/s), each monitor's reachable multiples and variable-refresh range, and latency.
- Score: Chapiro, Atkins & Daly (2019) fit a second-degree polynomial in mean luminance, frame rate and speed, giving a 0–9 judder score. The authors scored every shot of a short film to find the problem shots [S]. Limits: it was built from constant-speed pans, and anime and side-vision viewing are untested.
- Uses: film-level choice of display modes; a per-shot treatment column in the cue sheet; a placement cost term.
- On Path A every picture monitor is already even. Treatment then matters only for the 60 Hz fallback, 25p content, and very bright fast pans, where the remaining judder is 24p's own "film look".

### The surround gets its own time

The leftover-area glow can be smoothed over time (a running average) in the shader, using a storage image that persists between frames [V feasible]. That gives no glow flicker at cuts, and no judder on a surround monitor whatever its rate.

## 5. Seek during a swap

### The real rule, and why the race exists

- Rule: no shot changes its look while on screen.
- The planner decides "is this shot on screen?" from a playhead read over IPC. The swap lands 0–2 frames after the request. mpv confirms it 8–15 ms after the request in fullscreen display-resample, and 53–77 ms windowed with default sync. A seek in between can move the screen into a shot the new table changes, so that shot changes once.
- The two events are correlated, not random. After a seek the scanner refines the area after the seek target, so swaps cluster right after seeks. Scrubbing makes the race likelier than the 9-swap/3-seek test suggests.

### Options

| Option | Idea | Race closed? | Stock mpv | Cost |
|---|---|---|---|---|
| 0. Accept + log | rare; the consequence is one shot changing once; log it from mpv's seek events | no | yes | none; acceptable for the demo only |
| 1. Seek gate | every seek goes through our controller, which waits while a swap is in flight (≤ 100 ms, invisible) | yes, if every seek path is ours | brittle: mpv has no pre-seek hook, only load/unload hooks [V]; on-screen seekbar, chapters and keys all need rebinding | medium |
| **2. Takeover latch** | on its first frame, the new table pins the shot on screen to the old value; new values apply from the next cut or seek | yes, with one swap in flight at a time | feasible [V source] | small: one extra column + a storage block |
| 3. Fixed latch file + mailbox | a never-swapped shader file remembers the value actually shown; the swapped table passes its lookup through reserved pixels | yes, any number of swaps | possible but fiddly: saved textures are visible only inside their own file [V] | medium |
| 4. Own renderer | one thread handles swaps and seeks; the latch is a few lines | yes | n/a | product |

### Takeover latch (option 2)

Why it can work in stock mpv:

- libplacebo shaders may declare STORAGE buffers or images that "track and evolve state across invocations" [V `docs/custom-shaders.md`].
- mpv parses each shader path once and keeps the result until the video output closes; a swap only rebuilds the list [V `vo_gpu_next.c` `load_hook`, `update_render_options`].
- Renderer flushes and the per-frame hook reset clear cached frames and per-frame textures only, never storage [V `renderer.c` `pl_renderer_flush_cache`, `pass_begin_frame`; `custom_mpv.c` `hook_reset`].
- So each *new* table file starts with fresh storage on its first frame. That is exactly the moment it must anchor.

Logic, per presented image, in the OUTPUT hook (sketch; directive syntax unverified):

```glsl
// Table per version: shot start | new value | previous version's value.
// Storage (fresh in every new file): anchored, anchor_start, inherit.
// Pass 1 (one compute invocation):
//   s = shot_start(PTS);
//   if (anchored == 0) { anchored = 1; anchor_start = s; inherit = 1; }
//   else if (inherit == 1 && s != anchor_start) inherit = 0;
// Pass 2 (monitor mapping, as today):
//   value = (inherit == 1 && s == anchor_start) ? previous(s) : next(s);
```

- Key the shot by its start time, not its index, so a refined list of cuts doesn't confuse it.
- Keep the 1 s horizon. Then in normal swaps "previous" equals "new" for the anchored shot, and the latch changes nothing. It acts only in the race case, where the anchored shot is a changed shot the old table was showing.
- One planner rule remains. If mpv reports a seek between the planner's playhead read and the swap confirmation, the planner holds the next swap until the playhead has left that shot. Otherwise two quick swaps could disagree on what was shown.
- A revert (bad file) needs its own rule. Reactivating an already-loaded file reuses its old storage, so it won't re-anchor. To be settled in the spike.
- With frame blending on, OUTPUT runs once per presented image after blending (spike), so the latch sees every image.
- Cost: table entries grow from 8 to 16 bytes, plus one single-invocation compute pass. Per-swap memory behaves as in the spike.
- The same latch can hold *all* per-shot state: crop, position, size, surround, subtitle screen, time treatment. That puts the break rule in one place.

### Test (when approved)

Use the existing barcode + screen-capture harness and add:

- seeks at random offsets from −50 to +150 ms around each swap request;
- scrubbing bursts (5 seeks/s), seeks into changed shots, and seeks within the same shot;
- runs at 24 and 25 fps, blending on and off, fullscreen and windowed;
- reverts.

Pass: 0 shots changed on screen, 0 wrong frames.

## 6. Proposed rule changes (not applied, yours to decide)

1. Refine ADR 0005: "each monitor runs at a multiple of the film's playback rate; monitors need not match; custom modes allowed, stored in the room model".
2. The room model records, per monitor: built-in modes, verified custom modes, variable-refresh range and link type (eDP/DP/HDMI), and latency.
3. Performer rule: a per-shot latch in the render loop (the break rule).
4. Later: the cue sheet gains a time column (treatment, judder risk).

## 7. Next experiments

| # | What | Decides | Effort | Needs |
|---|---|---|---|---|
| E1 | NVIDIA Control Panel → "Set up G-SYNC": which screens are listed? Does the external have a DisplayPort input? | variable refresh per monitor | 1 min | you |
| E2 | Add custom 96 and 72 Hz modes for the external; check it shows a picture; remove them | Path A feasible | ~5 min | approval (changes display settings) |
| E3 | Camera spike: phone slow-motion ≥ 240 fps on a clip with bars moving across the seam and big per-monitor frame counters; Paths A–D; PresentMon for present mode | per-monitor pace and seam offset on 25H2; picks the demo path (closes synthesis S2/S3) | ~1 h | approval; takes over both screens |
| E4 | Latch spike with adversarial seeks (section 5) | closes question 2 of the last spike | ~1.5 h | approval; fullscreen trials |
| E5 | Product stage: two windows in one process, VRR follower aligned to the external | Path E | later | renderer decision (synthesis S9) |

After E3, compare A vs B vs C by eye on a slow anime background pan and a live-action pan that cross the seam.

## 8. Evidence

Tags: [V] verified today, from source (mpv d94d095, libplacebo 1dd0226, both dated 2026-10-10), mpv docs, or this PC's EDID/CCD probes. [S] search-level. [K] prior knowledge, not re-verified. [D] our derivation.

Local probes (scripts in the session scratchpad, not kept): EDID from the registry, CCD `QueryDisplayConfig`, `EnumDisplaySettingsEx`, `nvidia-smi`.

Source read:

- mpv:
  - `video/out/vo_gpu_next.c`: `load_hook` caches per path; `update_hook_opts_dynamic` runs before each render.
  - `video/out/gpu/utils.c`: `gpu_get_auto_param` fills only `PTS`, chroma offsets and HDR metadata.
  - `video/out/w32_common.c`: refresh taken from `MonitorFromWindow`.
  - `DOCS/man/options.rst`: `video-sync`, `tscale` (default `oversample`), `interpolation-threshold`, `display-fps-override`.
  - `DOCS/man/input.rst`: hooks are load/unload only.
- libplacebo:
  - `docs/custom-shaders.md`: STORAGE images and buffers persist; `frame` counter.
  - `src/renderer.c`: flush, per-frame hook reset; frame mixers none / linear / oversample "(AKA SmoothMotion)" / mitchell_clamp / hermite.
  - `src/shaders/custom_mpv.c`: `hook_reset` clears only per-frame textures; saved textures are per file.

Web:

- [HDMI Forum: Quick Media Switching (QMS)](https://hdmi.org/spec21sub/quickmediaswitching): uses the VRR mechanism to switch to the media rate, down to 24 Hz, without blanking.
- [Chapiro, Atkins, Daly 2019, A Luminance-aware Model of Judder Perception](https://www.researchgate.net/publication/334352032_A_Luminance-aware_Model_of_Judder_Perception) ([Semantic Scholar](https://www.semanticscholar.org/paper/A-Luminance-aware-Model-of-Judder-Perception-Chapiro-Atkins/efdf950d177ec3016c756d6a20f359ead6bba255))
- [Denes et al. 2020, perceptual model of motion quality for adaptive refresh rate and resolution](https://dl.acm.org/doi/10.1145/3386569.3392411)
- [Locally Varying Frame Rates for Judder Reduction in HDR Content (thesis, 2021; uploaded 2025)](https://www.researchgate.net/publication/390722578_Locally_Varying_Frame_Rates_for_Judder_Reduction_in_High-Dynamic-Range_Content)
- Patents: [Dynamic persistence for judder reduction](https://patents.google.com/patent/US20200402435A1/en), [Dual-ended metadata for judder visibility control](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/10944938)
- Community [S]:
  - [mpv issue 10388](https://github.com/mpv-player/mpv/issues/10388)
  - [Blur Busters: VRR for video playback](https://forums.blurbusters.com/viewtopic.php?t=14074)
  - [NVIDIA forum: window across 180 Hz + 60 Hz](https://forums.developer.nvidia.com/t/ue5-editor-low-fps-on-dx12-rtx-3060-with-dual-monitors-while-vulkan-runs-smoothly/349794)
  - [Notebookcheck: Windows 10 2004 mixed refresh](https://www.notebookcheck.net/Windows-10-2004-20H1-finally-gets-multi-monitor-refresh-rates-right.454143.0.html)
  - [TechRadar: Windows 11 per-display refresh preview](https://www.techradar.com/computing/windows/love-your-multi-monitor-setup-windows-11-could-soon-make-it-even-better)

Gaps:

- No primary source on 25H2 compositing across mixed rates.
- NVIDIA vs FreeSync-over-HDMI not verified.
- Commercial motion-grading tools not verified.
- No study found on offset visibility across a bezel.
- Machine-learning frame synthesis tools not surveyed (named generically above).

## 9. Unresolved questions

1. Does Windows 11 25H2 refresh each monitor from its own clock for one window spanning 240 Hz and 96 Hz? (E3) *User 2026-10-11: test it. Being measured in `spikes/mixed-rate-pacing/`.*
2. Does the external accept 96/72 Hz custom modes over HDMI, and does it have a DisplayPort input (for variable refresh on NVIDIA)? (E1, E2)
3. Is G-SYNC on for the laptop panel, and does the laptop stay in dGPU mode for playback?
4. How visible is a 5–10 ms frame-change offset across the bezel during pans? (by eye, E3) *User 2026-10-11: test it by eye.*
5. Is the LG UltraFine 4K (40–60 Hz) part of Setup B? *Answered 2026-10-11: yes, it is a Setup B monitor.*
6. Is a "smooth pan" look (pan synthesis or motion synthesis) acceptable for films and anime, or must 24p's own judder stay? (taste; A/B) *User 2026-10-11: wants to see both. After the real-anime A/B, the user prefers smooth camera slides: [ADR 0016](../../docs/adr/0016-camera-slides-play-smooth.md). Live-action films not yet judged.*
7. On D3D11, does libplacebo order a storage write in one pass before the read in the next pass of the same file? (E4)
8. Which revert rule keeps the latch exact when a bad file forces a swap back? (E4)
