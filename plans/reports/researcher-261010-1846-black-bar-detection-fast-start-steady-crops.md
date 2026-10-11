---
title: "Black-bar detection with fast start and steady crops"
date: 2026-10-10
topic: "Active-image-area (letterbox/pillarbox) detection: SOTA, failure cases, look-ahead architecture, never-cut safety, concept-demo pipeline"
sources: 29
---

# Research Report: Black-bar detection with fast start and steady crops

Timestamp: 2026-10-10, written ~19:15 Asia/Bangkok (session start 18:46). Panorama SOTA pass, topic 1 of 5.

Evidence tags used everywhere: **V** = verified by me this pass (local FFmpeg 8.1 probe, or page fetched and read). **S** = web-search summary/snippet only, page not opened. **K** = prior knowledge, not re-verified (a lead, not a fact).

## Table of contents

1. Executive Summary
2. Research Methodology
3. Key Findings: 1 Technology Overview, 2 Current State & Trends, 3 Best Practices, 4 Security, 5 Performance
4. Comparative Analysis
5. Implementation Recommendations
6. Recommendation for the concept demo
7. Resources & References
8. Appendix: Glossary
9. Unresolved questions

---

## Executive Summary

**Best-in-class: no benchmark exists (not found).** Real-time reference systems are madVR/Envy and Lumagen (S only). Both are reactive (hysteresis, zoom-rate limits) and both punt on variable-aspect (IMAX) content: Lumagen advises disabling auto-aspect for IMAX titles (S); madVR makes the user pick a crop-vs-bars trade-off (S). Best open building block is FFmpeg 8.1 `cropdetect` (V): cheap (+5-10 % over decode) but stateless. On 4 local clips (V): per-frame detection returned a crop that cuts real picture in 0.3-7.8 % of frames with synthetic 2.39:1 bars (top/bottom edges only; up to full collapse on black frames) and in 14 % / 23 % of frames of clips A/B as-is, no bars at all (all four edges); running-max (default `reset=0`) never cut after convergence but never recovers and took 0-1.3 s on 3 clips, 16.9 s on a dark-edge clip; default `round=16` shaved 8 px off a full-height 4:3 picture; small bright marks in bars are invisible to it (they get cut). No ML letterbox detector found, none needed. No system found that uses whole-file look-ahead: that is the gap to exploit.

**"Both worlds" is an assembly of proven parts, novel as a whole (untested on real films/anime).** Keyframe-only prescan (22 keyframes = 0.2 s for 108 s of 1080p, V) can give a global crop vocabulary + black level, start <= 1 s (design, not yet built). A background pass ahead of the playhead (10x realtime on ONE software thread, 24x NVDEC, 66x all-core; V) records per-frame edges + cut scores. Per-shot decision = union of evidence, snapped to the file's crop vocabulary, hold on dark/ambiguous shots, expand-always/shrink-only-on-proof. Cached timeline, applied exactly at shot cuts (only possible with look-ahead; reactive systems cannot). Shot-detector errors fail safe in this design (reasoning), so SOTA ML shot detectors are not needed for the demo.

**Decision needed from the user.** Proposed rule "when in doubt don't crop", read literally, shows full-frame bars on every dark scene of a scope film, i.e. exactly the "crops jumping in dark scenes" breaker (#4). Recommend: "when in doubt, hold the last confident crop; no-crop only when no confident crop exists (file start, unanalysed seek)". Never-cut rule 2 (text in bars) is implementable with stock FFmpeg (`crop` + `signalstats` YMAX on the bar strip, V on synthetic data).

---

## Research Methodology

- **Web budget: 5 calls used (3 WebSearch extended, 2 WebFetch), none left.**
  1. WebSearch: madVR zoom control / Envy / Lumagen / mpv dynamic crop. Engine collapsed to ONE sub-search (madVR only). S.
  2. WebFetch arXiv API, title-OR query (shot boundary/transition/detection, letterbox, black bars, hard-coded/burned-in subtitle), 40 newest. Noisy ("zero-shot" hits) but yielded 4 shot-detection papers and 0 letterbox/subtitle papers. V (abstract level).
  3. WebSearch: PP-OCRv5 det, burned-in subtitle detection, PySceneDetect, av-scenechange. 4 sub-searches. S.
  4. WebSearch: Lumagen auto-aspect (+ mpv/PotPlayer/Kodi/madVR in query). Collapsed to Lumagen only. S.
  5. WebFetch GitHub API: mpv + cropdetect Lua repos. total_count = 2. V.
- **Free local evidence (not web calls):** FFmpeg 8.1-full_build-www.gyan.dev (c) 2000-2026, Windows 11, NVIDIA GeForce RTX 5070 Ti Laptop GPU. `ffmpeg -h filter=...` plus ~30 probe runs written to the `null` muxer (no files kept). Material: 6 local non-cinematic clips (A,B,C = 1080p H.264 promos; D + two more = 720p15 talking heads; only A-D used for detection stats, all 6 for the freeze test), synthetic 2.39:1 letterbox (crop + pad black) so ground truth is exact; lavfi test patterns for text/noise/grey/rounding probes. **No feature film and no anime available: statements about them are inference.** N is tiny; numbers are illustrations, not statistics.
- **Source credibility (high to low):** own V probes on the shipped binary > arXiv papers (PERSIST BMVC 2026, TransVLM ECCV 2026 per arXiv comment field; OmniShotCut and Fassold-ICISPC not independently checked) > vendor docs with self-reported numbers (PaddleOCR/PaddleX) > project metadata (GitHub API) > forum/dealer posts (AVS, lumagen.expert; anecdotal) > unopened search snippets.
- **Date range:** 2025-2026 preferred; foundational patents granted 2002 and 2013 (titles only; years inferred from patent-number ranges, K).
- **Key search terms:** madVR "automatically detect hard coded black bars", madVR Envy Black Bar Configuration, Lumagen auto aspect IMAX, mpv dynamic crop cropdetect, shot boundary detection, PySceneDetect, av-scenechange, PP-OCRv5 mobile det, burned-in subtitle detection.
- **Not covered (budget):** Kodi, MPC-HC/BE, PotPlayer, VLC auto-crop; mpv first-party `autocrop.lua` source; madVR/Envy/Lumagen manuals (PDFs not opened); TransNetV2/AutoShot numbers.

---

## Key Findings

### 1 Technology Overview

Goal: find the rectangle of real picture ("active image area") = outermost rows/columns that carry picture. Four detector families:

| Family | Example | Status |
|---|---|---|
| Line statistics vs black threshold | FFmpeg `cropdetect` mode=black; TV/STB chips (patents US6366706, granted 2002; US8547481B2, granted 2013; titles only, S; years K) | dominant, classical |
| Edges + motion vectors | `cropdetect` mode=mvedges (exists in 8.1, V; needs decoder-exported MVs; NOT evaluated) | niche |
| Metadata hints | Dolby Vision RPU Level 5 "active area" offsets (K), broadcast AFD (K), container crop fields (K) | free ground truth on some files; verify before use |
| ML | none found (arXiv title search 0/40, web search none) | n/a |

**FFmpeg 8.1 `cropdetect` options (V, `ffmpeg -h filter=cropdetect`):**

| Option | Default | Note |
|---|---|---|
| mode | black | `mvedges` = motion vectors + edges |
| limit | 0.0941 (= 24/255) | absolute code-value threshold, float 0-65535, NOT range-aware |
| round | 16 | floors width/height to a multiple; verified to cut real picture |
| skip | 2 | first N frames not evaluated (also drops first 2 keyframes in keyframe-only scans) |
| reset / reset_count | 0 | 0 = running max (never shrinks); 1 = per-frame |
| max_outliers | 0 | newer option; hazard, see below |
| high / low / mv_threshold | 0.098 / 0.059 / 8 | mvedges only |

**Behaviour measured with probes (V):**
- Line test behaves like a per-line MEAN vs `limit`, not a pixel count: a one-row white run of <= 64 px on a 1920 px bar row is ignored, >= 90 px triggers (mean math: N*(235-16)/1920 > 8). A 2-char "OK" mark is ignored (it would be cut); a 20-char white subtitle line expanded the crop by 72 rows (bar shrank, subtitle kept).
- Mean test is noise-robust: exact crops with added noise up to `alls=60`, `max_outliers=0`.
- `max_outliers`: `=100` made even a 512 px white row ignored; `=200` on noisy input trimmed 48/88/130 px off the picture width (noise 10/30/60); `=2000` produced no output. Treat as picture-cutting hazard.
- `limit` below the black level breaks everything: 0.04 (< TV black 16/255 = 0.063) -> no crop in 100 % of frames. Grey bars (Y ~ 43) need limit >= 0.2 (0.094 and 0.15 fail). Too high cuts picture: limit 0.15 -> 23 % (clip A) / 46 % (clip B) of frames too tight.
- `round=16` floors dims: 802 -> 800 px, 840 -> 832 px, 4:3 pillarbox 1080 -> 1072 px (8 px of picture lost though no vertical bars exist). `round=2` was exact in all tests (asymmetric bars 100/140, pillarbox, windowbox). **Default `round=16` violates ADR 0001; use 2.**
- Machine-readable output: `metadata=print:file=-` emits `lavfi.cropdetect.{x1,x2,y1,y2,w,h,x,y,limit}` and `lavfi.scd.{score,mafd,time}` per frame. No log scraping needed.
- HDR: default limit maps to ~0.03 nit in PQ (computed, SMPTE ST 2084) vs 0.036 nit for SDR code 24 at 100-nit peak: similar absolute black threshold.

### 2 Current State & Trends

#### 2.1 Tool landscape and best-in-class (Q1)

| Tool | What it does | Maintained? | Tag |
|---|---|---|---|
| FFmpeg `cropdetect` 8.1 | stateless per-frame line test; running-max or reset; no shot/hysteresis logic | yes (8.1, 2026 build) | V |
| mpv first-party `TOOLS/lua/autocrop.lua` | cropdetect-driven auto crop script | unknown | K, not opened |
| mpv community `Ashyni/mpv-scripts` (`dynamic-crop.lua`) | "dynamic-crop.lua script for mpv player/lib"; 133 stars, MIT, created 2020, last push 2024-09-14. GitHub search "mpv cropdetect Lua" returned only 2 repos | **stalled-ish** (2 y no push) | V metadata only, internals unread |
| madVR (PC) | zoom-control page: "automatically detect hard coded black bars" + "crop black bars"; for variable-AR films user chooses avoid-cropping vs avoid-bars with in-between options; "zoom small black bars away"; reduce zoom in/out rate if AR changes often | PC release cadence unverified | S |
| madVR Envy | "Black Bar Configuration Menu", "Small Black Bar Reduction" up to 100 % (Scope Screen Setup Guide); guide does not state detection speed | active product | S |
| Lumagen Radiance Pro | auto-aspect with deliberate hysteresis delay against false switches (Lumagen tech tip), ~0.5 s / N frames (one forum user); detects fixed ratio list 1.78-2.4; **Lumagen recommends disabling auto-aspect for IMAX titles**; owners mixed (Asteroid City fine vs wrong ratio on next scene) | active product | S |
| Kodi, MPC-HC/BE, PotPlayer, VLC | not covered | - | not found |
| ML detectors | none | - | V (scoped) |

**Ranking (judgment, no benchmark):** (1) madVR/Envy and Lumagen are the best real-time systems in the field because they are purpose-built, tunable, and ship the only documented handling of aspect-change rate and variable AR that I found, yet both stay reactive and still defer IMAX to the user. (2) FFmpeg `cropdetect` is the best open primitive (fast, exact on clean bars, machine-readable). (3) mpv scripts are thin wrappers around (2). **None found exploits that the whole file is available. A look-ahead timeline layered on FFmpeg should beat all of them on the user's actual goal (fast start AND steady) by construction; untested on real films.**

#### 2.2 Failure cases and how each system copes (Q2)

| Failure | FFmpeg cropdetect (V probes) | madVR / Lumagen (S) | Proposed handling |
|---|---|---|---|
| Dark scene / night | per-frame too tight in 0.3 / 7.8 / 4.6 / 5.3 % of frames (clips A/B/C/D, top/bottom edges only; clips A/B as-is, all edges: 14 % / 23 % non-full-frame crops); running-max safe but 16.9 s to converge on dark-edge clip D (1.1 % frames cut meanwhile) | Lumagen hysteresis (~0.5 s) | hold last confident crop; shrink only on sharp-boundary proof; global prescan over whole file |
| Fade in/out, black frames | per-frame collapse up to 941 px (whole picture) | hysteresis | skip low-energy frames; hold |
| Credits | text-only frames would collapse a per-frame crop (reasoning, untested) | unknown | same as dark: hold |
| Subtitles / logos in bars | tiny marks ignored (cut); long lines kept; `max_outliers` worsens | unknown (madVR dev notes cover subtitle interplay, K) | bar-residual guard: `crop`+`signalstats` YMAX on bar strip, temporal persistence; hardsub-in-bar flag per file |
| Non-pure-black / noisy / grey bars | mean test tolerates noise; grey bars need limit > bar level | unknown | calibrate bar level in prescan, limit = level + margin |
| IMAX / variable AR | running-max cannot return to scope; per-frame jumpy | Lumagen: disable auto; madVR: user trade-off | per-shot modes, snap to vocabulary, switch at cuts |
| Anime: scope movie segments, 4:3 pillarbox, OP/ED | pillarbox + windowbox exact at round=2 (synthetic); **no anime sample tested** | no source found | same machinery both axes; need anime test set; dark-blue night scenes may sit near the threshold (hypothesis) |

#### 2.3 Shot detection and text detection, 2025-2026

- **Shot boundary detection (SBD).** PERSIST (arXiv 2608.29287, 2026-08-29, BMVC 2026, code github.com/linty5/PERSIST): removes 33-80 % of flash/text-overlay/archival false positives on a 2,727-video diagnostic, roughly halves TransNetV2's pseudo-event FPs at matched recall, parity with the strongest public detector (V, abstract). TransVLM (2604.27975, ECCV 2026): VLM + optical flow, detects transition segments not cut points, no numbers in abstract. OmniShotCut (2604.24762, 2026-04-27): relational SBD with Shot-Query Transformer + OmniShotCutBench. Fassold (2502.09202, ICISPC 2024): motion-field + cross-correlation, hard cuts + short dissolves + sampling structure + keyframes, ~4x faster than real time, no F1 given. All research code; TransNetV2 remains the public baseline they compare to. TransNetV2/AutoShot numbers: **not verified this pass**.
- **Practical detectors.** FFmpeg `scdet` (V locally: threshold default 10, `sc_pass`; 42 cuts at t=10 / 47 at t=8 on a 95 s promo; ~+17 % CPU over decode). PySceneDetect v0.7.1 (2026-07-21; AdaptiveDetector = rolling-average threshold; ThresholdDetector handles fades; 0.7 was a breaking timestamp overhaul) (S). av-scenechange (rust-av, rav1e-derived, used by Av1an; speed 0-1; Av1an notes "up to 35 % scenechange speedup"): **version conflict**, lib.rs 0.23.0 (2026-02-23) vs docs.rs "latest" 0.17.3 (S).
- **Lightweight text detection.** PP-OCRv5 mobile detector 4.7 MB, CPU 57.77 ms normal / 28.15 ms high-perf, det Hmean 79.0 (server 83.8) per PaddleX table (vendor numbers, S). PP-OCRv6 (arXiv 2606.13108, 2026-06): 1.5M-34.5M params tiers, tiny tier 3.9x faster than PP-OCRv5-mobile on Xeon CPU (vendor claim, S). Burned-in subtitle detection has no canonical method: per-frame detect + temporal merge, cheap prefilter then OCR, video-level vote (S). EAST (2017) / CRAFT (2019) are legacy (K). **For "is there anything in the bars I must keep" a text detector is overkill: persistence of non-black structure is enough.**

### 3 Best Practices

#### 3.1 "Both worlds" architecture for local files (Q3)

```
file open
  |
  +--> P0 keyframe prescan (<= 1 s; ~22-60 keyframes over the file; -skip_frame nokey)
  |        -> bar black level, crop VOCABULARY (1-3 modes), confidence, hardsub-in-bar flag
  |
  +--> playback starts: crop = dominant mode if confident, else none
  |
  +--> P1 background pass AHEAD of playhead (HW decode or 1-2 SW threads, 10-24x realtime)
           per frame: 4 edges, bar-strip YMAX, mean luma, scdet score
              |
              v
           shots (scdet; PySceneDetect / av-scenechange only if scdet misses)
              |
              v
           per-shot crop = union(frame evidence) -> snap to smallest vocabulary mode that contains it
                           hold previous if shot dark / fade / ambiguous
              |
              v
           timeline JSON  --cache-->  %LOCALAPPDATA%\panorama\crops\<content-hash>.json
              |
              v
           renderer: lookup(t); apply EXACTLY at the cut; optional expand-only reactive guard
```

Why it works (measured, V): keyframe-only decode of 22 frames = 0.20-0.24 s (444-533x realtime); running-max over keyframes converged on the first evaluated keyframe in 3 of 4 clips (21st keyframe, ~21 s, on dark-edge clip D). Sampling non-key frames saves no decode time (`select=not(mod(n,6))`: 11.8 s vs 10.9 s single-thread), so P1 should consume all frames and P0 only keyframes. Keyframe cadence differs hugely per file (A 5.1 s, B ~1.0 s, C ~1.7 s, D ~0.9 s): prescan cost and sample density are file-dependent (x264/x265 also place keyframes at scene cuts, K, so real encodes often give cut-aligned samples).

#### 3.2 Proven vs novel

| Component | Status | Evidence |
|---|---|---|
| Keyframe-only sampled prescan | proven technique | V (0.2 s / 22 keyframes) |
| Wait-then-detect-once, freeze crop | proven, common in scripts | V: freeze at 4 s left 0 px cut on 5 of 6 clips and 8 px on clip D (until it converges, 17-32 s); freeze at 0.5 / 1 / 2 s on D cut 3015 / 1080 / 593 px |
| Reactive detection with hysteresis | proven in hardware processors | S (Lumagen) |
| Second decoder running ahead of playhead (HW) | engineering-proven (plain FFmpeg); new for this use | V throughput; NVDEC has no consumer decode-session cap (K) |
| Shot detection | proven; ML SOTA exists, overkill here | V scdet, S PySceneDetect/av-scenechange, V-abstract PERSIST/TransVLM/OmniShotCut |
| Vocabulary snapping + per-shot union + hold | **novel, not found anywhere** | design only; synthetic reasoning |
| Cached per-file crop timeline | **novel in players** (encoders cache scene lists, K) | none |
| Cut-synchronous application | only possible with look-ahead | reasoning |

"Not found" means not found within a 5-call budget, not proven absent.

#### 3.3 Decision rules (initial guesses, to tune by eye; none literature-derived)

- **Evidence asymmetry.** Bright structure OUTSIDE the current crop is reliable evidence of picture (or of text/logo to keep). Black INSIDE a candidate region is unreliable (dark picture looks like bars: 0.3-7.8 % of frames in V probes). So: expand freely; shrink only with positive proof = sharp boundary (content row directly next to a pure-black row) in >= k frames of a shot with no outside content; otherwise hold.
- **Crop vocabulary.** Cluster prescan/pass results into 1-3 modes (e.g. scope 2.39:1, full frame, scope+subtitle band). A shot's crop = smallest mode containing the union of its evidence. This removes arbitrary jitter crops like 1856x1080 produced by dark edges (V: on clips A and B as-is, no bars at all, 14 % / 23 % of frames returned such non-full-frame crops at limit 0.094, round=2, reset=1).
- **Black level.** Estimate from outer rows/cols in P0 (TV 16, PC 0, or noisy); limit = level + max(8, 4 sigma) in 8-bit codes (FFmpeg default 24 = 16 + 8).
- **Dark/ambiguous shot** (low mean luma, fade slope, no sharp edge): hold previous crop.
- **SBD errors fail safe:** a missed cut leaves a union that is looser (bars stay, no picture cut); an extra cut only adds a boundary with the same mode.
- Starting numbers: expand after >= 3 frames of coherent outside content (>= 1 % of edge length); shrink after >= 48 frames (~2 s) of sharp-edge proof; prescan consensus >= 70 % of keyframes agreeing +-2 px.
- **Stage 1 (demo default, only stock FFmpeg output):** per-shot running-max of `cropdetect` (reset at scdet cuts) + vocabulary snap + dark hold. **Stage 2 (if A/B shows need):** black-level calibration, sharp-edge shrink proof, residual guard.

#### 3.4 "Never cut real picture" (Q4)

- **Burned-in subtitles/text/logos in bars.** `cropdetect` is blind to small marks (V) and `max_outliers` makes it worse. Stock-FFmpeg guard (V on synthetic): `crop=W:barH:0:y,signalstats,metadata=print` -> `lavfi.signalstats.YMAX` jumped 16 -> 235 exactly while a 2-char mark was on screen, while `cropdetect` saw nothing. Real compressed bars will show sporadic YMAX noise: threshold well above the noise floor (e.g. bar level + 40), require temporal persistence (>= 3 frames) and spatial clustering; an `erosion`/`median` pre-filter to kill isolated pixels is an untested option. Policy: persistent structure in a bar region = keep that region (looser crop) for the whole shot; if it recurs across the file -> file-level "hardsub-in-bars" flag: keep bars on that side (flip-flopping per subtitle is the jumping we must avoid).
- **ML text detection** (PP-OCRv5/v6 mobile det, S) is an optional second opinion for sparse signs, not a prerequisite. No evidence found that it beats the persistence test for this narrow question.
- **Confidence thresholds.** No published thresholds found. Industry practice found = fixed hysteresis delays (Lumagen ~0.5 s, S). Use the numbers in 3.3 and the metrics in section 6.
- **Conflict with the proposed rule** (see Executive Summary): hold-last-confident beats literal no-crop. Residual risk: a dark IMAX shot directly after a scope shot is ambiguous and held at scope (real picture cut). Mitigate with within-shot evidence (any bright frame) and A/B by eye; accept otherwise.
- **Free hints, verify before relying:** Dolby Vision L5 active-area offsets (K) could give exact per-frame truth on DV titles; also a regression oracle for the detector.

#### 3.5 Applying aspect-ratio changes

- Reactive systems must wait (Lumagen ~0.5 s hysteresis, S; madVR reduces zoom rate when AR changes often, S) and then jump or ease. With look-ahead the change can be scheduled to land **on the cut** (instantly hidden) and, if no cut exists (fade, subtitle-expansion mid-shot), eased over a short time. Defaults to A/B: (a) instant at cut, (b) instant at cut + 300-500 ms ease when no cut, (c) Lumagen-like reactive with 0.5 s delay.
- Never animate a tighten mid-shot. Expansion mid-shot (late evidence) should be pre-applied at the previous cut thanks to look-ahead.
- Coupling: a crop change rescales the picture across the monitors, moving faces relative to bezels (illusion breaker #1). The layout planner must consume the same timeline and re-plan at the same cut.

### 4 Security Considerations

- Untrusted media files hit complex decoders: run P0/P1 in a separate low-privilege process, keep FFmpeg current (local build 8.1), cap runtime/memory, treat a crashed analyzer as "no timeline -> no crop".
- Cache: key by content hash + analyzer version, parse as data only (JSON), store under the user profile, never execute. Contents leak file-level metadata only (durations, crop rects).
- ML models (only if added later): third-party weights = supply-chain surface; pin hashes.
- Patents on black-bar/aspect detection exist (US6366706, granted 2002; US8547481B2, granted 2013; titles only, S; years inferred from patent numbers, status unchecked). Free/non-commercial does not remove patent exposure; the proposed method is generic statistics + look-ahead, but this is not legal advice.

### 5 Performance Insights

Environment: FFmpeg 8.1, RTX 5070 Ti Laptop GPU, all numbers from clip A/B (1080p H.264 High 8-bit, ~2.3-2.4 Mbps, 95-109 s) unless noted. **Low bitrate: high-bitrate remux and 4K will be slower; not measured.**

| Path (decode + cropdetect, null output) | fps | x realtime | CPU-s per 109 s clip |
|---|---|---|---|
| SW, all cores | 1,584-1,628 | 65-66x | 17.8 |
| SW, 2 threads | 473 | 19x | 10.7 |
| SW, 1 thread (+ scdet: 261 fps) | 249 | 10x | 11.0 |
| D3D11VA (copy-back) | 513 | 20x | 6.9 |
| NVDEC `-hwaccel cuda` (copy-back) | 601 | 24x | 3.0 |
| NVDEC + `scale_cuda` 480 + hwdownload | 318 | 12.7x | n/m |
| SW + scale to 480 first | 1,409 | 56x | decode-bound, no gain |
| Keyframes only (22 frames) | n/a | 444-533x (0.20-0.24 s) | ~0.2 |

- Detection overhead is small next to decode: decode-only 1,763 fps -> +cropdetect 1,584 fps (+5-10 %) -> +scdet ~+17 % CPU.
- Extrapolation (labelled, 1080p low-bitrate): a 2 h film = 172,800 frames -> ~11.6 min on 1 SW thread, ~4.8 min NVDEC, ~1.8 min all-core. Peak RSS ~170 MB (all-core SW, measured).
- HW copy-back is NOT automatically faster in wall time on a many-core laptop for low-bitrate 1080p (601 vs 1,628 fps) but costs ~6x less CPU. At 4K, copy-back of P010 frames is heavy: reduce on GPU (row/col stats via compute shader/CUDA, download a few KB per frame) -- design suggestion, unmeasured.
- NVDEC on this GPU decoded H.264 High10 (Hi10P), HEVC Main10, AV1 10-bit (forced CUDA-frame test passed; verbose log confirms NVDEC for H.264 High10). Other GPUs: not verified; query decoder caps at runtime and fall back to SW.
- Timeline size is negligible (per-shot records; per-frame raw metadata is ~KB-MB per film, estimate).

---

## Comparative Analysis

| Rank | Option | Start | Steady | Never-cut | IMAX/VAR | Effort | Maintained | Verdict |
|---|---|---|---|---|---|---|---|---|
| 1 | FFmpeg 8.1 primitives + own look-ahead timeline (vocabulary snap, per-shot union, hold) | <= 1 s | high by construction | high (expand-always, hold) | per-shot modes, at cuts | M (~few hundred LOC + cache) | FFmpeg active (V) | **Recommended** |
| 2 | `cropdetect` running-max, wait 2-4 s, `round=2`, freeze | 0-1.3 s typical (17 s dark-edge) | very steady | leaves 8 px cut on dark-edge clip; permanent over-expansion on long subtitles | cannot return to scope | XS | V | Stage-0 fallback for constant-bar files |
| 3 | Reactive per-frame + hysteresis (Lumagen-like) | instant | medium | medium (wrong AR until delay) | poor (vendor says disable for IMAX) | S | product active (S) | A/B reference; reuse as expand-only guard |
| 4 | madVR / Envy zoom control | ? | zoom-rate limiting (S) | ? | user trade-offs (S) | closed, not a base | Envy active; PC unverified | A/B target by eye (MPC-HC 64-bit and VLC are installed locally; madVR and mpv are not) |
| 5 | mpv `dynamic-crop.lua` (Ashyni) | ? | ? | ? | designed for dynamic | S | last push 2024-09-14 | read and A/B; not a base |
| 6 | ML letterbox detector | - | - | - | - | - | none exists (scoped) | skip |

---

## Implementation Recommendations

### Quick Start

1. Pin FFmpeg >= 8.1 and drive it as a subprocess in the demo (ADR 0002: assemble first); zero native code.
2. Run P0 and P1 commands below; parse `metadata=print` records into per-frame rows.
3. Implement the Stage-1 timeline builder (per-shot running-max + vocabulary snap + dark hold); write JSON cache.
4. Replay the timeline in the renderer, applying at cuts; log every crop change.
5. Score on the four metrics in section 6 over a labelled set before adding Stage 2.

### Code Examples

Both verified to run on FFmpeg 8.1 and to emit the listed keys:

```bash
# P0: keyframe prescan. skip=0 or the first 2 keyframes are silently dropped.
ffmpeg -v error -skip_frame nokey -i IN -an -sn \
  -vf "cropdetect=limit=0.094:round=2:reset=1:skip=0,metadata=print:file=-" -f null -

# P1: full pass, optional HW decode (frames are copied back automatically)
ffmpeg -v error -hwaccel d3d11va -i IN -an -sn \
  -vf "scdet=t=10,cropdetect=limit=0.094:round=2:reset=1:skip=0,metadata=print:file=-" -f null -
# keys: lavfi.cropdetect.{x1,x2,y1,y2,w,h,x,y,limit}, lavfi.scd.{score,mafd,time}

# Stage 2 bar-strip residual (bottom 138 rows of a 1080p frame), per-frame YMAX
ffmpeg -v error -i IN -an -vf "crop=iw:138:0:ih-138,signalstats,metadata=print:key=lavfi.signalstats.YMAX:file=-" -f null -
```

### Common Pitfalls

1. `round=16` (default) cuts picture; `skip=2` drops the first samples; `limit` is an absolute code value (below TV black 16/255 = 0.063 nothing is ever "black").
2. `reset=0` never shrinks; `reset=1` jitters. Neither is a policy.
3. `max_outliers` trades noise-robustness for picture cuts (V).
4. Thin text/logos are invisible to the mean test; long text is visible: behaviour flips with line length, causing crop flicker with intermittent subtitles.
5. Frame sampling saves nothing on decode; only keyframe-only does.
6. HW copy-back can lose to multi-core SW on low-bitrate 1080p; measure per machine; Hi10P HW decode is GPU-dependent.
7. Dark-edge content converges slowly (16.9 s, 21 keyframes in clip D): do not freeze early, sample the whole file.
8. Literal "don't crop when in doubt" = bars on every dark scene.
9. Shot-detector false positives are cheap here; do not over-invest in SBD accuracy.

---

## Recommendation for the concept demo

**Build on (named, dated, verified):**
- FFmpeg 8.1 (gyan.dev full build, (c) 2000-2026; V): decoders with d3d11va/cuda/d3d12va/amf/qsv/vulkan listed, `cropdetect`, `scdet`, `signalstats`, `metadata=print`. Maintained. Adoption risk low (mature, huge user base) but pin the build: option sets change between versions (`max_outliers` is new to me; K: older docs lack it), so ship/pin one FFmpeg.
- `scdet` for cuts. Escalate to PySceneDetect 0.7.1 (2026-07-21, S, maintained) or av-scenechange (S, maintained, version conflict; 0.x churn, Rust-only) only if scdet misses on anime; PySceneDetect adoption risk = 0.x API with a breaking 0.7 release. Do NOT adopt PERSIST/TransVLM/OmniShotCut for the demo (2026 research code, no packaged runtime verified).

**Renderer hand-off:** the timeline is renderer-agnostic JSON (time ranges + crop rect + confidence). A sibling pass recorded mpv 0.41.0 (2025-12-21, gpu-next default) and libmpv render API = OpenGL/SW only (read from the sibling's notes, not re-verified by me). Whether crop-at-cut can be driven through libmpv properties (`video-crop`, K) or needs a custom renderer belongs to the rendering report.

**Build ourselves:** (1) P0 prescan + crop vocabulary + black-level estimate; (2) per-shot union + vocabulary snap + dark hold; (3) bar-strip residual guard; (4) look-ahead scheduler + content-hash cache; (5) cut-synchronous application (and optional expand-only reactive guard); (6) the four-metric evaluation harness.

**A/B by eye:** (a) AR change: instant-at-cut vs +300-500 ms ease vs Lumagen-like 0.5 s reactive; (b) hold-last-confident vs literal no-crop on dark scenes; (c) hardsub-in-bar: keep bars vs crop; (d) madVR (if installed) and `dynamic-crop.lua` vs ours on the same clips: an IMAX-style switch, a night scene, a fade, a credits roll, a hardsub-in-bar clip.

**Evaluation metrics:** cut-px (must be 0 on every frame), residual-bar-px, crop changes per hour and % landing on cuts, time-to-first-correct-crop. Needs a small labelled set: one scope film with IMAX segments, one night-heavy film, 4:3 anime pillarbox, anime scope movie, hardsub-in-bar clip. Ground truth by shot-level labelling (or DV L5 if present).

**Compute on a laptop (RTX 5070 Ti Laptop measured, 1080p low-bitrate):** P0 ~0.2-0.3 s; P1 10x realtime on one SW thread (~12 min for 2 h, background, low priority) or ~5 min NVDEC at ~3 CPU-s per 109 s of video; ~170 MB RAM. 4K/high-bitrate: expect slower, use HW decode with GPU-side reduction; unmeasured.

---

## Resources & References

Tags: [opened] = fetched and read; [snippet] = surfaced by search, not opened; [local] = run on this machine.

1. FFmpeg 8.1-full_build-www.gyan.dev, `ffmpeg -h filter=cropdetect|scdet|blackdetect`, `-hwaccels`, ~30 probes. [local] 2026-10-10 (build (c) 2000-2026). Official docs for reference: https://ffmpeg.org/ffmpeg-filters.html#cropdetect (not opened).
2. arXiv API feed, shot/letterbox/subtitle title query. [opened] 2026-10-10. https://export.arxiv.org/api/query?search_query=ti:%22shot+boundary%22+OR+ti:%22shot+transition%22+OR+ti:%22shot+detection%22+OR+ti:letterbox+OR+ti:%22black+bars%22+OR+ti:%22hardcoded+subtitle%22+OR+ti:%22burned-in+subtitle%22&sortBy=submittedDate&sortOrder=descending&max_results=40
3. PERSIST, arXiv 2608.29287, 2026-08-29 (abstract via feed). https://arxiv.org/abs/2608.29287
4. TransVLM, arXiv 2604.27975, 2026-04-30. https://arxiv.org/abs/2604.27975
5. OmniShotCut, arXiv 2604.24762, 2026-04-27. https://arxiv.org/abs/2604.24762
6. Fassold, faster-than-real-time shot boundary detection, arXiv 2502.09202, 2025-02-13. https://arxiv.org/abs/2502.09202
7. GitHub API search "mpv cropdetect Lua". [opened] 2026-10-10. https://api.github.com/search/repositories?q=mpv+cropdetect+in:name,description,readme+language:Lua&sort=stars&order=desc&per_page=20
8. Ashyni/mpv-scripts, last push 2024-09-14. https://github.com/Ashyni/mpv-scripts
9. PySceneDetect changelog, v0.7.1 2026-07-21. [snippet] https://www.scenedetect.com/changelog/
10. PySceneDetect detectors 0.7.1. [snippet] https://www.scenedetect.com/docs/latest/api/detectors.html
11. av-scenechange on lib.rs (0.23.0, 2026-02-23). [snippet] https://lib.rs/crates/av-scenechange
12. av-scenechange on docs.rs (shows 0.17.3). [snippet] https://docs.rs/crate/av-scenechange/latest
13. Av1an releases (scenechange speedup note). [snippet] https://github.com/rust-av/Av1an/releases
14. PaddleX OCR pipeline docs (PP-OCRv5 mobile det table). [snippet] https://paddlepaddle.github.io/PaddleX/3.2/en/pipeline_usage/tutorials/ocr_pipelines/OCR.html
15. PaddleOCR 3.0 Technical Report, arXiv 2507.05595, 2025-07. [snippet] https://arxiv.org/html/2507.05595v1
16. PP-OCRv6, arXiv 2606.13108, 2026-06. [snippet] https://arxiv.org/pdf/2606.13108
17. PP-OCRv5 intro (PaddleOCR docs), undated. [snippet] https://paddlepaddle.github.io/PaddleOCR/main/en/version3.x/algorithm/PP-OCRv5/PP-OCRv5.html
18. AVS Forum, "VARs Pandemic - Best Approach to Variable Aspect Ratio with MadVR", undated. [snippet] https://www.avsforum.com/threads/vars-pandemic-best-approach-to-variable-aspect-ratio-with-madvr.3268532/
19. AVS Forum, madVR Player Support Thread p.141, undated. [snippet] https://www.avsforum.com/threads/madvr-player-support-thread.2215490/page-141
20. AVS Forum, madVR Player Support Thread p.44, undated. [snippet] https://www.avsforum.com/threads/madvr-player-support-thread.2215490/page-44
21. anime.my, "Configuration: madVR", undated. [snippet] https://anime.my/tutorials/madvr/
22. madVR Envy Scope Screen Setup Guide (PDF), undated. [snippet] https://madvrenvy.com/wp-content/uploads/Scope-Screen-Setup-Guide.pdf
23. madVR v0.89.5 developer notes (PDF), version-dated only. [snippet] http://madshi.net/89notesForDevs.pdf
24. Lumagen "Fixes Aspect Ratios" tech tip (lumagen.expert), undated. [snippet] http://lumagen.expert/site/fixes-aspect-ratios/
25. AVS Forum, New Lumagen Radiance Pro Series p.358, undated. [snippet] https://www.avsforum.com/threads/new-lumagen-radiance-pro-series.2172017/page-358
26. Lumagen Radiance Pro owner's manual (device.report), undated. [snippet] https://device.report/manual/15361188
27. US6366706, "Method and apparatus for automatic aspect format detection in digital video pictures", grant date not shown (2002 inferred from number, K). [snippet] https://patents.justia.com/patent/6366706
28. US8547481B2, "Apparatus and method for black bar detection in digital TVs and set-top boxes", grant year 2013 (K, inferred from number). [snippet] https://patents.google.com/patent/US8547481
29. videocr (Tesseract-based hardcoded subtitle extraction), undated. [snippet] https://github.com/apm1467/videocr

---

## Appendix: Glossary

- **Active image area:** rectangle of real picture, bars excluded.
- **Letterbox / pillarbox / windowbox:** bars top+bottom / left+right / all four sides.
- **VAR / IMAX switching:** film that alternates aspect ratios (e.g. 2.39:1 and ~1.90:1 or 1.78:1) between scenes.
- **Running-max:** `cropdetect reset=0`: crop only ever grows to the largest area seen.
- **Crop vocabulary:** the 1-3 legitimate crops of a file; per-shot results are snapped to it.
- **Hold:** keep the last confident crop when evidence is ambiguous.
- **Keyframe / GOP:** independently decodable frame / frames to the next one; keyframe-only decode (`-skip_frame nokey`) is the cheap sampler.
- **SBD / scdet:** shot boundary detection / FFmpeg scene-change filter.
- **NVDEC / D3D11VA:** NVIDIA / Windows hardware video decode APIs; copy-back = frames downloaded to system memory.
- **Hi10P:** H.264 High 10 profile (10-bit), common in anime encodes.
- **PQ:** SMPTE ST 2084 HDR transfer function.
- **DV L5:** Dolby Vision metadata block carrying active-area offsets (K).
- **Hysteresis:** delay/threshold gap that suppresses flip-flopping.
- **CIH:** constant image height projection setups (Envy/Lumagen context).

---

## Unresolved questions

1. No ground-truth set or public dataset for active-area detection found. Real-film and anime behaviour (night scenes, IMAX, OP/ED, hardsubs) is untested: all numbers here are from non-cinematic clips with synthetic bars.
2. madVR/Envy and Lumagen algorithms, thresholds, latencies: only forum/secondary summaries seen; vendor manuals (PDFs) not opened. Is PC madVR still developed?
3. mpv first-party `autocrop.lua` and Ashyni `dynamic-crop.lua` internals unverified. Kodi/MPC/PotPlayer/VLC auto-crop not covered.
4. TransNetV2/AutoShot vs PERSIST numbers unverified; av-scenechange latest version conflicts (0.23.0 vs 0.17.3); scdet quality on anime (flat colours, limited animation) unknown.
5. Can FFmpeg 8.1 expose Dolby Vision L5 active-area side data on a real DV file? Not tested (no DV sample).
6. HW decode support for Hi10P/HEVC/AV1 on Intel/AMD/older NVIDIA not verified (only this RTX 5070 Ti Laptop).
7. `cropdetect mode=mvedges` quality not evaluated.
8. User decision: hold-last-confident (recommended) vs literal "don't crop when in doubt" for dark scenes; and hardsub-in-bar policy (keep bars vs crop).
9. Perception: instant-at-cut vs eased vs reactive aspect changes needs the eye test on Setup A/B.
10. Patent status of US6366706 / US8547481B2 unchecked.
