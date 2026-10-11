---
title: "Film analysis for the content model: best way per field, and the analysis tech stack"
date: 2026-10-11
type: research
sources: 38
---

# Film analysis for the content model (M1, phase 04)

Environment: 2026-10-11, Windows 11 26200, Ryzen 9 8940HX (16C/32T), 32 GB, RTX 5070 Ti Laptop 12 GB (driver 617.42, CUDA UMD 13.4), AMD 610M iGPU, FFmpeg 8.1 (gyan full).

Evidence tags: **[V]** verified this pass from a primary source (repo, release, doc, file I fetched). **[S]** search snippet only. **[K]** prior knowledge, not re-verified. **[L]** measured on this PC (commands in section 4). **[D]** derived by arithmetic from other tags.

Terms (new ones, with an example):
- **Dissolve**: one shot fades into the next while both are visible. **Fade**: the picture goes to or from black or white. **Flash**: 1-3 bright frames inside one shot (an explosion); not a cut.
- **Held drawing**: anime repeats the same drawing for 2-3 frames ("on twos/threes"), so the picture changes less often than 24 times a second.
- **Keyframe**: a frame the video file stores whole; encoders often put one at a cut.
- **OP/ED**: the opening/ending credit sequences of an episode (fast graphics, many cuts).
- **Hard subs**: subtitles burned into the picture. **PlayRes**: the canvas size an ASS subtitle file is written for. **PGS**: bitmap subtitles on Blu-ray.
- **Parallax**: near things slide faster than far things when the camera moves.
- **NVDEC**: the GPU's video-decoder chip (separate from the shader cores). **ECC**: a pixel-matching refinement that gets image alignment to a fraction of a pixel.

## 1. Answer

1. **Stack.** One separate, low-priority **Python analysis worker per film**; FFmpeg 8.1 decodes on NVDEC to tiny frames (2,600 fps, no shader cores used with `hevc_cuvid -resize`); **ONNX Runtime 1.31 CUDA EP** runs the models (TransNetV2, face/head/person); OpenCV does motion. Output is a two-layer cache (raw per-frame signals + derived tables) keyed by file identity, indexed by PTS, in source-pixel coordinates. Contract = files + ONNX models, so a Rust/native rewrite later is possible without touching planner or performer. [L][V]
2. **Speed.** 24-min episode: first usable result about 1 s (keyframe scan 0.8 s), full pass about 1 min wall; 2 h 1080p film about 5 min [D from L]. Not measured: 4K HDR remux (expect 3-5x). CPU-only fallback exists (about 45 min for a film, dominated by CPU face models). [L][D]
3. **Shots/cuts:** **TransNetV2 (ONNX, MIT, 2020, repo stalled since 2023-12 but still the working baseline)**. 19 s per episode; flagged 53 of its 350 events that FFmpeg `scdet` misses (mostly dark scenes) and ignores 113 of `scdet`'s 422 events. By eye, 18 sampled from each group: the first all real changes, the second 17 held-drawing pops or OP/ED graphics. **OmniShotCut v1.5 (MIT, 2026, anime in scope)** adds typed transitions with spans but is PyTorch-only and not more accurate here: optional second opinion. Store raw scores, not only decisions. [L][V]
4. **Black bars:** FFmpeg `cropdetect` + our per-shot union + vocabulary snap cut **0.00%** of frames of real anime with exact synthetic bars (raw per-frame: **9.8%**, running max: 0% cut but bars left in **36.8%**), 0.96% bars left, every change on a cut; needs a calibrated black level (limit 0.075 fine, 0.15 breaks). [L]
5. **Text in bars:** `cropdetect` alone **cuts the bottom 6-13 px of 36-72 px hard subs in nearly every frame** even with per-shot union; a bar-strip max-luma guard finds **99.2%** of text frames with 1.9% false alarms. Build the guard. [L]
6. **Faces/attention:** domain-specific detectors. Anime: deepghs face v1.4_s / head v2.0_s / person v1.3_s ONNX (MIT weights, YOLOv8-class architecture = AGPL question). Live action: YuNet (and MediaPipe). YuNet is nearly blind on anime (2 hits in 24 shots). No verified saliency SOTA and no anime evidence: skip for the demo; add "text on screen" as a third keep-clear class. [L][V][S]
7. **Dialogue:** parse every track; libass's own "sign" test (`\pos \move \clip \iclip \org \pbo \p`) marks **0 of 480** events of the real streaming ASS as signs although 61 are top-aligned (37 overlapping speech, 24 titles/credits), so alignment + overlap + timing heuristics and a labeled set are needed. Rendered lines are wide: median 41% of frame width, **98.7% of centred lines cross a seam at 50%**. [L][V]
8. **Camera slides:** phase correlation + ECC at 480x270 reaches **0.039 px median error** on known shifts after x265 re-encode (6 ms/pair); full resolution adds nothing. Telling pure slides from layered ones needs per-pair gating plus static/other-motion fractions; 6 of 12 inspected anime runs were layered; slow ramp-in/out segments are ambiguous. Nothing off the shelf: build it. [L]
9. **GPU sharing:** NVDEC decoding and CPU inference never disturbed playback; CUDA kernels do when calls are long: **batch <= 8 (<= 20 ms per call) gave zero measurable vsync jitter at 79% GPU load, batch 16 and 64 gave large jitter**; no dropped frame in any trial; the Windows GPU "idle" priority class did nothing; CUDA MPS does not exist on Windows. [L][V]

Honest limits: one anime episode, one 62 s promo, one machine; no labeled anime shot data found anywhere; PGS, Dolby Vision, 4K HDR, real hard-sub films and live-action faces untested.

## 2. Per field

Levers common to all fields: the whole file is available ahead of time (two-pass, look-ahead); the planner tolerates some errors (see each field); container metadata is free; scan order can follow the playhead.

### 2.1 Shots and cuts (including fades, dissolves, flashes, anime cases)

**Invariant.** A list of moments where the picture may change layout unseen, each with kind (hard, dissolve, fade, wipe, flash, unknown), span, and confidence. Not "narrative shots": a flash or a held-drawing jump must not be a layout-change point, a dissolve is.
**Planner tolerance.** Missed cut = a lost chance only (layout held). Extra cut = harmless unless the planner moves there, so the planner should require confidence.

**Levers.** (a) frame differences (`scdet`, histogram detectors); (b) learned detectors; (c) codec side info: keyframes and packet sizes cost no decode [L: 0.3 s for the whole 259 MB file]; (d) metadata: chapters (none in the local episode), Dolby Vision RPU `scene_refresh_flag` and L5 exist in FFmpeg 8.1 side data [V: libavutil/dovi_meta.h n8.1]; (e) whole-file context (windows around the cut, global thresholds); (f) audio and cross-episode OP/ED fingerprints (not tested, outside "file alone").

| Option | Version / date / license | Maintenance | Speed on this PC | Anime evidence | Verdict |
|---|---|---|---|---|---|
| **TransNetV2** ONNX (3rd-party export, sha256 checked, parity 3e-6 vs TF [V]) | 2020; MIT; F1 77.9 ClipShots / 96.2 BBC / 93.9 RAI per README [V] | repo last push 2023-12-04, 1,053 stars [V] | GPU 5,826 fps (243x realtime); NVDEC decode 2,558 fps; episode 19 s total. CPU 32 threads 368 fps (15x) [L] | 350 cuts, shots median 81 frames; 27 of 27 sampled events looked like real visual changes (2 were OP overlay switches); 53 events `scdet` misses [L] | **Pick** |
| **OmniShotCut v1.5** | arXiv 2604.24762 (2026-04-27, v2 05-21); repo MIT; 52.8M params; weights on HF [V] | pushed 2026-09-09, 322 stars; used by 4 data pipelines [V] | GPU 2,727 fps (114x) at 128x96, 520 MB VRAM [L]. PyTorch only, pins `transformers==4.57.3` [V] | 428 shots; finds 93% of TN cuts; 98 extra (18 sampled: 11 OP/ED graphics changes, 4 plausible real cuts or transitions, 3 false splits in ordinary shots); 7/10 synthetic transitions located with a type, long fades only as boundaries, 2-frame white flash wrongly called a fade [L] | Optional second opinion + transition types |
| **PySceneDetect 0.7.1** (Adaptive/Content) | 2026-07-21; BSD-3 [V] | pushed 2026-10-10 [V] | Adaptive 210 fps, Content 232 fps CPU (162 s / 147 s per episode) [L] | agrees with 87% / 80% of TN cuts [L] | CPU fallback |
| FFmpeg `scdet` t=10 | in 8.1 | n/a | 2,332 fps incl. decode [L] | 84% of TN cuts found, 70% of its 422 events confirmed [L]; fires on held-drawing pops and flashes (86 on a synthetic flash) | cheap extra signal only |
| PERSIST (BMVC 2026) | Apache-2.0 [V] | 2 stars; MMAction2 fork, Linux, PyTorch 2.4, CUDA 11.8 [V] | not run | claims 33-80% fewer flash/overlay false positives (abstract) [V] | Watch; revisit when packaged |
| TransVLM (ECCV 2026) | Apache-2.0; 4B VLM weights [V] | 20 stars [V] | not run (VLM) | none | Skip (too heavy per frame) |
| AutoShot (CVPRW 2023) | MIT [V] | last push 2023-04 [V] | not run | short-video dataset, no anime [S] | Legacy |

**Results that matter.**
- Synthetic known transitions (anime + live clips joined by `xfade`, 10 transitions + 1 flash) [L]: TN single/many-hot heads at 0.5 found 9 of 10 (the 12-frame dissolve scored 0.45); with `max(heads) > 0.4` found **10 of 10** and still ignored the flash. Many-hot head flags the whole span of long dissolves (22 of 24 frames).
- Keyframes are strong free evidence: in this x265 anime encode **74%** of TN cuts coincide with a keyframe (+-1 frame) and 258 of 306 keyframes (**84%**) are TN cuts; in the H.264 promo 55% of cuts. Packet-size jumps are useless (precision 3-10%). [L]
- OP/ED graphics changes are inherently ambiguous: all detectors disagree there; the planner should treat OP/ED as one low-confidence region.
- No anime-specific shot benchmark found by search [S]; every number above is eyeballed on one episode.

**Pick:** TransNetV2 ONNX, both heads' per-frame probabilities stored; threshold in the planner. **Fallback:** PySceneDetect Adaptive (CPU). **Optional:** OmniShotCut v1.5 behind the same interface for transition spans/types after the shot spike.
**Build ourselves:** boundary typing + "layout-change point" policy, keyframe prior, raw-score cache, labeled eval harness. Re-export TN from upstream ourselves before shipping (the ONNX we used is a stranger's repo; hash matches its card).

### 2.2 Black bars per shot (including changing ratios, dark scenes, hold policy)

**Invariant.** Per shot, the exact rectangle of real picture; real picture is never cut; changes only at cuts; dark/ambiguous shots keep a sensible crop (ADR 0010). **Tolerance.** Leaving bars = cosmetic; cutting picture = violation: asymmetric.

**Levers.** Per-line luma statistics (`cropdetect`); black-level calibration (limit = bar level + margin); sharp-edge proof; per-shot union; vocabulary of the file's 1-3 true modes; look-ahead; keyframe-only pre-scan (**0.8 s for the whole episode**, 306 keyframes; software 5.5 s) [L]: on the test below, 96 keyframes recovered all three true modes (shares 39/35/25%), 95 of 96 keyframe rects within 2 px of a true mode at limit 0.075 (86 of 96 at 0.094) [L]; metadata: Dolby Vision L5 active area (`AVDOVIDmLevel5` left/right/top/bottom offsets, FFmpeg 8.1) [V], container crop and conformance windows (handled by the decoder).

**Test (new, [L]).** First 23,016 frames (16 min) of the real anime episode, HEVC Main10, with exact bars painted in: W1 scope bars (138 px top+bottom), W2 none, W3 4:3 pillarbox (240 px each side), W4 scope/none alternating per TN shot (IMAX-like). 244 shots, 63 black frames. Per-frame `cropdetect` (round=2, reset=1), then three policies:

| Policy (limit 0.094 = default 24/255) | Frames cutting picture | Frames with bars left | Crop changes (on cuts) |
|---|---|---|---|
| S0 raw per frame | **9.81%** (W3 15.5%, W4 20.7%; worst 1,857 px) | 0% | 811 (90) |
| S1 running max (`reset=0`) | 0.00% | **36.77%** (cannot return from none to pillarbox/scope) | 1 |
| **S2 per-shot union + vocabulary snap** | **0.00%** | **0.96%** | 64 (**64**) |
| S2b = S2 + "hold previous when frames disagree" | 1.06% (worst 480 px) | 1.85% | 56 (56) |

- The vocabulary found automatically was exactly the three true modes. At limit 0.075 (about 19/255; true bar level 16): S0 2.55% cut, S2 still 0.00% / 0.96%. At 0.15: S0 26%, S2 4.4% cut. **Black level must be calibrated per file** (bar level + 3 codes), not left at the default.
- **ADR 0010's "hold the last confident crop" should be implemented as "snap to the smallest vocabulary mode containing the shot's evidence"; "hold previous" only for shots with no valid frame.** The hold-on-disagreement variant made things worse (carries scope into dark IMAX shots).
- The real episode has no bars: raw per-frame `cropdetect` (limit 0.094) still returned a non-full-frame crop in **7.2%** of its 34,047 frames (31 stretches, longest 426 frames = 17.8 s) [L]: dark scenes and black frames. That is the "crops jumping in dark scenes" breaker in the raw.
- Live action: the hotel promo (62 s, 16:9, no bars) gave non-full crops in 4.9% of frames, one 76-frame stretch [L]. No real scope film available locally.

| Option | Version/license | Verdict |
|---|---|---|
| FFmpeg 8.1 `cropdetect` (+ own timeline) | LGPL/GPL build; 8.1.x point releases exist (n8.1.3 tag) [V]; 1,072 fps incl. NVDEC copy-back [L] | **Pick** (primitive) |
| Dolby Vision L5 / container metadata | FFmpeg side data [V]; no DV sample to test | Use when present, as ground truth and oracle |
| mpv community dynamic-crop / madVR / Lumagen | see earlier report | reactive; not a base |
| ML letterbox detector | none found (earlier report) | skip |

**Pick:** the Stage-1 pipeline above with black-level calibration. **Fallback:** running-max `cropdetect` for constant-bar files. **Build:** black-level estimate, per-shot union, vocabulary, snapping, dark/no-evidence rule, strip guard (2.3), cache, eval harness. **Still open:** real scope film with IMAX segments; a dark IMAX shot straight after a scope shot stays ambiguous.

### 2.3 Text in the bars (burned-in subtitles, logos)

**Invariant.** No pixel of real text/logo in a bar is ever cropped. (Soft subtitle tracks are drawn by the player on top; only burned-in text is a detection problem.)

**Levers.** Bar-strip max-luma/row profiles with temporal persistence; subtitle-track presence (hints at soft subs); OCR/text detector as second opinion; per-shot union; a per-file "hard subs in bars" flag.

**Test (new, [L]).** First 240 s of the scope window with the real French ASS burned in by libass at font size 72 and 36 (71% of frames carry text; 3,763 bottom, 668 top frames reach into a bar):
- `cropdetect` per-frame crop keeps the whole text in only **27%** (72 px) / **25%** (36 px) of text frames at limit 0.075; the bottom **12-13 px** (72 px font) or 7 px (36 px) of the line are cut in the rest. Per-shot union does not help (cut 12-13 px). The mean-over-line test never "sees" the last glyph rows.
- **Bar-strip guard** (`crop` strip + `signalstats` YMAX >= black + 24..60): recall **99.2%** (4,038 of 4,071 text frames), false alarms 32 of 1,681 text-free frames (**1.9%**); same at thresholds 24, 40, 60.

**Pick:** a **max-based** strip guard (rows/cols of the bars, persistence >= 3 frames, spatial clustering), producing the rows that must stay; crop extends to cover them. **Fallback/second opinion:** RapidOCR (Apache-2.0, PP-OCR detection ONNX, pushed 2026-10-11) [V], not needed for this test. **Policy to judge by eye:** file-level "hard subs in bars" flag (keep those bars always) vs per-shot (crop size flips between shots with and without text, only at cuts). **Build:** the guard, flag, evaluation. Real hard-sub films and logos not tested.

### 2.4 Faces and attention

**Invariant.** Per shot, the places the viewer looks at (faces first, then heads/bodies, on-screen text) in picture coordinates, so the planner keeps them off bezels. **Tolerance.** The picture continues behind bezels (1-3% width, ADR 0009), so a miss costs a sliver, but a face under a bezel is breaker #1.

**Levers.** Detectors per domain; temporal tracking and per-shot aggregation (sampling 2 fps is enough: shots are seconds long); text detectors; saliency; motion/camera slides (subject fixed in frame during a follow shot); subtitle position; planner conservatism (keep centre clear).

| Option | Version/date/license | Speed here | Evidence |
|---|---|---|---|
| **deepghs anime face v1.4_s** (ONNX) | 2023-06 repo, MIT; F1 0.95, 11.1M params; YOLOv8s yaml [V] | 224 fps (b1), 581 fps (b8) CUDA; 23 fps CPU [L] | faces in 17 of 24 sampled shots; the 7 without were credits, black, landscapes, torso; half-profile, small, partial faces and a crowd (5 faces + posters) found [L] |
| **deepghs anime head v2.0_s_yv11** | MIT; F1 0.92, mAP50 0.958 [V] | 166 / 552 fps [L] | live: found a back-of-head and a dark half-profile the face models missed; also fired on tiny arch niches (2 false of 9 live detections) [L] |
| **deepghs anime person v1.3_s** | MIT; F1 0.86 [V] | 191 / 558 fps [L] | live: found people in the shots that have them; at least 1 false hit on an archway [L] |
| **YuNet 2023mar** (+2026may dynamic-shape export) | OpenCV Zoo (Apache-2.0 repo), libfacedetection; WIDER AP 0.884/0.866/0.750 [V] | 87 fps at 960x540, 172 fps at 640x360 (CPU, OpenCV 5.0) [L] | live: 4 detections in 4 of 32 promo shots (3 faces, 1 tiny distant player), missed the dark half-profile; **anime: 2 detections in 24 shots** [L] |
| MediaPipe face detector | Apache-2.0, pushed 2026-10-10 [V] | not run | none |
| SCRFD / InsightFace | code MIT, **models non-commercial research only** [V] | not run | accuracy lead among small models per YOLO5Face paper [S] |
| YOLO11/12-face, YOLOv8 | **Ultralytics AGPL-3.0** [V] | not run | competitive [S] |
| Saliency (ACLNet 2018, SalEMA 2019; PredJSal 2026 claim) | PredJSal only via a social post [S] | not run | trained on natural-video eye tracking, **no anime evidence, no verified 2026 leaderboard** |

- Licensing flag: the deepghs weights are MIT but the architecture is YOLOv8-class [V: model_artifacts.json], whose code is AGPL-3.0. The analysis worker is a separate process talking through files (ADR 0014), so this is isolatable; decide with the licence choice after the demo.
- The local live-action clip has a visible face in only about 5 of 32 shots (brand promo: bodies, architecture, a bird): the main subject is often **not a face**, so persons/heads/text and later saliency matter. Face-detector evidence on live action is thin: test set needed.

**Pick:** run **both families on every sampled frame** (3 anime ONNX models + YuNet; about 600 fps combined at batch 8, so 14,400 frames of a 2 h film = about 75 s) and let the planner use `class` and `domain_score`; no brittle anime-vs-live switch. Add a text detector (PP-OCR det via RapidOCR) as class `text`. **Fallback:** YuNet + a COCO person detector for live action (not benchmarked: RT-DETR/D-FINE/RF-DETR are Apache-2.0 candidates [K]).
**Build:** sampler (shot-aware), IoU tracker, per-shot keep-clear map (64x36 grid, max over time) and raw boxes, planner-level evaluation (bezel-hit rate).

### 2.5 Dialogue lines (SRT, ASS/SSA, PGS)

**Invariant.** Per line: start/end, text-class (dialogue vs sign/title/credits/song), anchor (where the file puts it), natural rendered box (so the planner knows whether it fits one screen). All tracks, because the viewer may switch.

**Levers.** libass's definition of "explicit" (positioned) events: `\pos \move \clip \iclip \org \pbo \p`, or scrolling effects [V: `ass_event_has_hard_overrides`, `ass_render.c`]; style names, layer, alignment (`\an`, `\a`), overlap with other lines, timing windows (OP/ED), `Actor/Effect` fields, other-language tracks as dialogue reference, track flags (forced/SDH), PGS bitmap positions, rendering with libass for width.

**Local ASS facts [L]** (streaming-release French/German/Polish ASS tracks; English is SRT):
- 1 style ("Default"), 0 events with `\pos/\move/\clip/\p`; 80 of 480 use `\a` alignment overrides: `\a6` top-centre 61 (37 overlap a bottom line = second speaker; 24 standalone = titles/credits/"EN PLEIN BOULOT"), `\a1` 9 and `\a3` 6 (all standalone credits, bottom-left/right), `\a5` 3 and `\a7` 1 (overlapping speech, top-left/right). Max 2 simultaneous lines. So style names and libass "explicit" classify nothing here; **overlap + alignment + timing** are the signal.
- Rendering with libass (via FFmpeg `subtitles` + `bbox`, 5 fps sampling) took **16 s per track** (7,100 canvas frames over 1,420 s; one frame per event would be ~1-2 s). French ASS: width median **41%** of frame (p95 55.6%, max 71.4%), centre x 0.50 for 95%; 81.6% of frames bottom, 6.7% top, 11.7% mid. English SRT (default style): median 36%, all bottom. A vertical seam at x=0.50 crosses **98.7%** (ASS) / **100%** (SRT) of centred lines; at 0.40/0.60 about 90%/89%. ADR 0013 will fire on nearly every line when a seam is near the picture centre.
- SRT has no positions: width depends on the **player's** font, so store text + timing and compute width at plan time. PGS (not available locally): FFmpeg's decoder gives bitmap rects (x, y, w, h) [K]; positions are explicit.

| Component | Version/license | Verdict |
|---|---|---|
| libass | 0.17.5 (2026-06-24), ISC [V] | de-facto reference (mpv uses it); use for widths |
| pysubs2 | 1.8.1, MIT [V] | parse ASS/SRT; trivial |
| FFmpeg subtitle demux / decode | 8.1 | all tracks, PGS rects |
| mpv `--sub-scale-signs` | uses libass heuristics, "best effort" [S] | confirms no standard better than libass |

**Pick:** pysubs2 + libass rendering + a feature-based classifier {explicit, alignment, overlap, style differs from main dialogue style, in OP/ED window, caps/colon patterns} with a **three-way output (dialogue / sign / unknown) and confidence**; the planner moves only `dialogue`, never `sign`. **Fallback:** all SRT lines = dialogue. **Build:** classifier, labeled corpus (fansub ASS with typesetting, streaming ASS, SRT, PGS), PGS rect extractor, per-track cache.

### 2.6 Camera slides (pure slide, sub-pixel path, vs parallax and characters)

**Invariant.** Per shot (or per run inside it): is the whole picture sliding as one rigid sheet; if yes, the position of every frame along the path to <= 0.1 px; not parallax, not characters moving on their own. **Tolerance.** Wrongly smoothing a layered shot smears characters: precision over recall; confidence per frame.

**Levers.** Phase correlation; ECC/Lucas-Kanade translation; feature matching + RANSAC homography (live-action rotation/zoom); dense flow + layer clustering; codec motion vectors; residual tests after alignment (static layer share, other-motion share); anime production habits (background layer slides on keyed, eased positions; characters on top; held drawings); cut times (a slide never crosses a cut); eased-path priors; look-ahead for ramp-in/out.

**Accuracy test (new, [L]).** 16 real frames per domain, shifted by known random sub-pixel amounts (+-10 px, half of them vertical-only), re-encoded as a 2-frame x265 CRF 22 10-bit clip, measured:

| Method | Anime median / p90 / max error (px) | Live median / p90 / max | Time per pair |
|---|---|---|---|
| phase correlation at 480x270 | 0.146 / 0.392 / 1.08 | 0.288 / 0.629 / 0.68 | 3.8 ms |
| phase correlation at 1080p | 0.161 / 0.258 / 0.29 | 0.049 / 0.211 / 0.23 | 32 ms |
| **phase 480 + ECC at 480x270** | **0.039 / 0.059 / 0.069** | **0.041 / 0.065 / 0.085** | **6.3 ms** |
| phase 480 + ECC at 960x540 | 0.026 / 0.044 / 0.045 | 0.032 / 0.064 / 0.078 | 18 ms |
| phase 480 + ECC at 1080p | 0.025 / 0.042 / 0.043 | 0.033 / 0.071 / 0.074 | 79 ms |

Phase correlation alone is not sub-0.1 px; ECC at 480x270 is. Full resolution buys 0.01 px for 12x the time: skip. (Synthetic pure translation; real slides carry more error.)

**Real shots [L].** Anime episode: 34,046 pairs in 253 s single Python process (135 pairs/s); 21 sliding runs >= 1 s. Features per run: `explained` = share of textured pixels the single shift explains, `static` = textured pixels that did not move although the slide did, `other` = pixels explained by neither. Visual check of the first 12 runs:
- pure: 0.88-1.00 explained, static <= 0.006, other <= 0.05: the runs at 566.5 s, 584.1 s and 852.3 s (which contains 853.9 s) are exactly the three slides the user preferred smoothed (ADR 0016), plus a dark corridor tilt at 812.4 s.
- layered: end-credit graphics with static label plates or grids (0.53-0.81 explained, other 0.10-0.33), a character acting over a sliding background (0.57 explained, static 0.19), a title plate over a slow push (0.44, static 0.21). **6 of 12 inspected runs were layered.**
- Live-action promo (62 s): 16 runs >= 0.6 s; pure-looking ones 0.93-0.99 explained; a dolly past pillars (parallax) 0.75 explained, static 0.084; fast body close-ups 0.56-0.75.
- **Ambiguous ramps:** in the "pure" run at 852.3 s the first 20 pairs (0.8 s, camera easing in) disagree between phase-480 and ECC-960 (3.1 vs 1.3 px per pair) and *neither* lowers the residual below the unshifted difference (2.1-2.3 vs 1.5). Cumulative path agreement between resolutions on two other pure runs: rms 0.6-0.75 px over 92 and 41 frames (829 and 113 px slides). Gate **per pair** (the spike's rule: after <= max(1.0, 0.35 x before)) and store per-frame confidence; do not take run medians.
- 73.1% of all neighbouring pairs in the episode show the same drawing (held) [L], consistent with the spike's 72%.

| Option | Verdict |
|---|---|
| phase corr + ECC translation (spike baseline) | **Pick**; add gating + purity features + path export |
| feature + RANSAC homography | for live-action rotation/zoom: needs a test; not run |
| dense flow (RAFT family, DIS) | anime flow benchmark is 2022 AnimeRun only [S]; no verified anime numbers; heavy; use only as layer-separation tool if static/other features prove insufficient |
| codec motion vectors | encoder-dependent, not exposed by NVDEC [K]; skip |
| VLM camera-motion classifiers (CameraBench, NeurIPS 2025) | "SfM struggles with content-dependent motion, VLMs with precise trajectories" [S]: confirms geometric estimation is the right tool for the path; a VLM could only label "follow shot" |

**Build ourselves:** all of it (nothing off the shelf): per-pair measurement, gating, purity classification, path with confidence, ramp handling, live-action model test.

### 2.7 Per-shot motion and brightness (judder risk; lower priority)

**Invariant.** Per shot: typical and p90 speed of the main motion, mean and peak luminance, frame rate; the planner converts to degrees per second and nits with the room model (Chapiro, Atkins, Daly, ACM TOG 38(5) 142, 2019: a luminance-aware judder model using mean luminance, frame rate and average clip speed [S]).
**Levers.** Free by-products of 2.6 (global dx/dy per pair, residual `after`, luma mean/std) and of decode (luma percentiles); HDR metadata (MaxCLL, Dolby Vision L1 per-frame min/avg/max if present); local-motion magnitude from a few flow samples if needed.
**Pick:** store raw per-pair values; derive speed (px/s normalised to picture height), p90, luma percentiles per shot; add flow only after the judder model is wired. **Build:** derivation + HDR code-value to nits mapping in planner. IP flag: Dolby holds a patent on judder-visibility control (US 10,368,031, title seen in search) [S]: unchecked, relevant only to a later licence choice.

## 3. Analysis tech stack

```
file -> A0 facts (ffprobe, packet scan, keyframes, hash, chapters, DV side data, tracks)      ~0.5 s
     -> A1 keyframe-only decode -> black level, crop vocabulary, cut prior                    ~0.8 s episode
     -> B1 NVDEC session 1: hevc_cuvid -resize 480x270 (8-bit) -> worker splits:
           48x27 RGB windows  -> TransNetV2 (ORT CUDA)          -> L0 shot scores
           gray 480x270 pairs -> phase corr (+ECC if sliding)    -> L0 slide measures, luma
           every 12th frame, 640x384 -> face/head/person/YuNet/text -> L0 subject boxes
     -> B2 NVDEC session 2: full-res copy-back (1,290 fps) -> cropdetect + bar-strip max profiles -> L0 crops, strips
     -> C  subtitles: all tracks -> pysubs2 + libass boxes / PGS rects -> subtitle events
     L0 (raw, per frame, ~40 B/frame, ~7 MB per 2 h) -> L1 derived tables: shots, crops, text-in-bars,
        slides + paths, subjects + keep-clear maps, motion/brightness  -> planner
```

| Decision | Choice | Evidence / reason |
|---|---|---|
| Language | **Python 3.11-3.13 worker** now; native later if packaging demands | ORT-GPU 1.31.0 (2026-10-09) wheel runs on Blackwell directly [L][V]; Rust `ort` is still 2.0.0-rc.13 (2026-07-28) after 13 release candidates, one maintainer, breaking changes per rc [V][K] |
| Runtime + EP | **ONNX Runtime CUDA EP** (ORT 1.31.0; CUDA plugin EP 0.3.0; MIT) | TN 5.8k fps, YOLOs 224-581 fps; `CUDAExecutionProvider` verified active (not CPU fallback) [L]. TensorRT EP is listed in the wheel, untested. |
| Packaging later | **TensorRT-RTX EP / Windows ML** | RTX 30+; not on PyPI/NuGet yet, plugin ABI since ORT 1.23, JIT engines on device [V]; Windows ML ships EPs via Windows Update, needs 24H2 (this PC 26200) [V]. ORT+CUDA libs here = 1.8 GB vs TRT-RTX < 200 MB [L][K] |
| Avoid | **DirectML** | "sustained engineering", moved to Windows ML [V: learn.microsoft.com, updated 2026-01-09]; pip package stuck at 1.24.4 vs ORT 1.31 [V] |
| PyTorch | only for PyTorch-only models (OmniShotCut) | torch 2.14.1+cu130 on Windows lists sm_120 and runs [L]; cu128 builds exist since 2.7 [V]; 2.9 GB installed |
| Decode path | **FFmpeg 8.1 subprocess, NVDEC** (`hevc_cuvid -resize` or `-hwaccel cuda` + `scale_cuda`); fallback D3D11VA/software | decoder-bound at ~2,600 fps either way; CUDA cores untouched with `cuvid -resize`; TN results with NVDEC-resized frames equal the official CPU-resize path (347 of 350 cuts match) [L] |
| Alternatives kept in reserve | PyNvVideoCodec 2.2.3 (2026-09-15, MIT per PyPI): 2,305 fps to GPU memory, but **MKV needs `needScannedStreamMetadata=True`** (BinaryIO and plain path failed) [L]. torchcodec 0.17.0 (2026-09-30, BSD-3): 2,214 fps to CUDA RGB tensors on Windows via the cu130 index **and needs shared FFmpeg DLLs on PATH** (worked with mpv's) [L][V]. Software torchcodec 74 fps. | Only needed if full-res work moves to GPU (bar strips, reductions) |
| Dead/stalled | VALI archived 2026-02-05; decord last push 2024-07; DALI Linux-centric [V][K] | do not adopt |
| Process boundary | **separate worker process** (never inside the performer); files + small control channel (playhead, seek, priorities); CPU priority BELOW_NORMAL; job object memory cap; crash = cautious cue sheet | ADR 0014; GPU/CUDA libs cannot be unloaded in-process [K, earlier note]; keeps CUDA out of the render path |
| Cache key | `sha256(size + first/middle/last 1 MiB)` instantly, full SHA-256 later (1,475 MB/s here = 0.18 s per 259 MB) + **per-table analyzer version**; segments GOP-aligned (30-60 s) with overlap so scans can start at any seek | sha256 timing [L] |
| Hybrid graphics | pin NVIDIA by vendor id; CUDA enumerates NVIDIA only | AMD 610M present [L]; ORT notes [K, earlier note] |
| Portability | ONNX models + FFmpeg hwaccel (D3D11VA, VideoToolbox later); never assume CUDA | Setup B GPU unknown; macOS later (ADR 0003); ORT 1.31 adds CoreML EP selection [V] |

**GPU sharing rules (measured, section 4.4).**
1. Decode on NVDEC only; it did not disturb playback (decode loop at 1,130 fps, NVDEC 43% busy).
2. Keep each CUDA call short: **batch <= 8 (<= 20 ms)**; no batch >= 16. TransNetV2 windows (8.6 ms per call) are inside the tested range but were not run during playback.
3. No priority trick: D3DKMT idle class changed nothing at batch 16; CUDA MPS is "only supported on the Linux and QNX operating systems" [V]; green contexts partition SMs between CUDA contexts, not against graphics, Windows support unconfirmed [S].
4. Prefer continuous small submissions over sleep-based duty cycling: batch 4 at 25% duty gave 27 delayed frames, worse than batch 4 saturating (2) [L, n=1, anomaly unexplained].
5. Light fields (bars, slides, subtitles) run on CPU workers at below-normal priority: no effect on playback measured.
6. Re-verify with the real performer (spike S1): these are windowed-mpv numbers on one panel.

**Throughput (RTX 5070 Ti Laptop + Ryzen 9 8940HX).** Episode = 34,047 frames, 1080p HEVC 10-bit, about 1.5 Mbps. Film = 172,800 frames (24 fps), assumed 1080p; times scale 5.1x [D].

| Stage | Where | Measured | Episode | 2 h film |
|---|---|---|---|---|
| packet scan + hash | CPU | 0.29 s + 0.18 s | 0.5 s | ~3 s |
| keyframe-only decode (crop vocabulary) | NVDEC | 306 keyframes 0.82 s | 0.8 s | ~4 s |
| small-frame decode | NVDEC | 2,596 fps | 13 s | 67 s |
| TransNetV2 | GPU | 5,826 fps | 6 s | 30 s |
| OmniShotCut (optional) | GPU | 2,727 fps | 12.5 s | 63 s |
| cropdetect + strips (full-res copy-back) | NVDEC+CPU | 1,072 fps | 32 s | 161 s |
| slide pairs (phase + ECC, Python) | CPU | 135 pairs/s per process | 253 s (about 32 s on 8 procs) | 1,280 s (160 s on 8) |
| face + head + person at 2 fps | GPU | 224/166/191 fps b1; 581/552/558 b8 | 15-45 s | 77-226 s |
| libass boxes, all tracks | CPU | 7,100 canvas frames (5 fps) in 16 s | 2-16 s per track | same |
| **Parallel wall clock** | | NVDEC about 40 s + CPU about 40 s overlapped | **about 1 min** | **about 5 min** |
| First usable result (keyframe scan + first 3 min full pipeline, warm worker) | | | **about 5 s** [D] (slides may lag until a slide shot is reached); cold start +3 s (ORT CUDA session 2.95 s) | same |
| CPU-only fallback | CPU | TN 368 fps (15x); software decode 912 fps; YOLOs 23-28 fps (8 threads) | about 9 min (YOLOs 340 s of it) | about 45 min [D]; halve with 1 fps sampling or two models |

4K HDR remux: not measured; expect NVDEC fps roughly /4 and bitrate-bound reads, so 3-5x the film numbers [D, unverified].

## 4. Local measurements (exact commands)

Scratch scripts live in `...\scratchpad\work\` (session folder; copy to `spikes/` if kept). Isolated venv `...\scratchpad\venv-analysis` (ORT-GPU 1.31.0 with CUDA 13 wheels, torch 2.14.1+cu130, torchvision 0.29.1, torchcodec 0.17.0, PyNvVideoCodec 2.2.3, OpenCV 5.0.0.93, NumPy 2.4.6, SciPy 1.17.1, scenedetect 0.7.1, pysubs2 1.8.1); every script run as `python -I`. Downloads each in its own `dl\<name>\` dir; the OmniShotCut checkpoint pickle's globals were listed without executing (OrderedDict, torch tensors, argparse.Namespace only) before loading. One side effect: torchvision downloaded a ResNet-18 weight file into `%USERPROFILE%\.cache\torch`; moved to scratchpad and the folders removed; later runs set `TORCH_HOME`.

**4.1 Decode (anime episode, 180 s excerpt unless noted)**
```
ffmpeg -benchmark -hwaccel cuda -hwaccel_output_format cuda -ss 60 -t 180 -i EP -an -sn -map 0:v:0 -f null -                      # 2,506 fps, 0.9 CPU-s
... -vf "hwdownload,format=p010le"                                                                                              # 1,291 fps, 2.5 CPU-s
... -vf "scale_cuda=480:270:format=nv12,hwdownload,format=nv12"                                                                 # 2,449 fps, 1.7 CPU-s
ffmpeg -benchmark -ss 60 -t 180 -i EP -an -sn -map 0:v:0 -f null -                                                              # software 912 fps, 33 CPU-s
ffmpeg -benchmark -ss 60 -t 180 -c:v hevc_cuvid -resize 480x270 -i EP -an -sn -map 0:v:0 -f null -                              # 2,532 fps, 0.8 CPU-s
ffmpeg -benchmark -skip_frame nokey -hwaccel cuda ... -vf scale_cuda=480:270,hwdownload ...                                    # 306 keyframes, 0.82 s (software 5.5 s)
ffprobe -v error -select_streams v:0 -show_entries packet=pts,size,flags -of csv=p=0 EP                                         # 0.29 s
```
Python: `nvc_bench2.py` (PyNvVideoCodec), `tc_bench.py` (torchcodec, mpv's FFmpeg DLLs on PATH).

**4.2 Shots**: `tn_run.py VIDEO OUT --decode nvdec-cuda-scale|sw|cuvid --ep cuda|cpu` (official windowing: 100 frames, stride 50, centre 50; ONNX at `dl\transnetv2-onnx`); `scdet_scores.py` (`scale_cuda,hwdownload,format=gray,scdet=t=10,metadata=print:file=...`); `psd_run.py` (PySceneDetect Content/Adaptive defaults); `osc_run.py` (OmniShotCut v1.5, frames at 128x96); `sbd_compare.py`, `pick_events.py`, `grab_sheets.py` (before/after contact sheets for eye checks); `make_transition_test.py` (xfade: fade 12/24/4/1, fadeblack 24, fadewhite 12, wipeleft 12, slideleft 6, circleopen 12, fadegrays 36, plus a 2-frame white flash) + `eval_trans.py`, `thr_sweep.py`; `packet_cuts.py`.

**4.3 Bars and text**: `crop_frames.py` (`hwdownload,format=p010le,cropdetect=limit=L:round=2:reset=1:skip=0,metadata=print:file=...`, per frame); `make_bars_test.py` (drawbox on the decoded episode, HEVC Main10 NVENC, exact ground truth); `bars_eval.py` (S0/S1/S2); `make_hardsub.py` + `sub_bbox.py` (libass truth boxes at 24 fps) + `hardsub_eval.py` (`crop=iw:130:0:950,signalstats,metadata=print:key=lavfi.signalstats.YMAX`). Pitfalls hit: `drawbox` needs planar 10-bit (`format=yuv420p10le`); `-filter_script:v` instead of `-filter_complex_script` when the encoder follows; paths in filters need `\:` escaping.

**4.4 GPU sharing**: `mpv_trial.py NAME [--load cuda|nvdec|cpu PRIO] [--load-batch N]` starts project mpv 0.41.0 (`--vo=gpu-next --gpu-api=d3d11 --hwdec=d3d11va --video-sync=display-resample --interpolation --tscale=oversample --scale=ewa_lanczossharp --geometry=2480x1450+20+20 --focus-on=never`, windowed on the 240 Hz laptop panel, project's `span-test-helper.lua` for counters, `nvidia-smi -lms 500`) and `gpu_load.py` (ORT CUDA YOLOv8s 640x384, or NVDEC decode loop, or CPU ORT; `D3DKMTSetProcessSchedulingPriorityClass` for "idle"). 25 s of playback each, one trial per row (CUDA batch 1 and 64 twice, baseline twice; the first baseline, run with a cold shader cache, is noted in the first row):

| Load during playback | Background rate | GPU util | mpv dropped / mistimed | delayed frames | vsync jitter | est. fps |
|---|---|---|---|---|---|---|
| none, second run (first run, cold shader cache: 5 delayed, jitter 0.118, 238.6 fps) | | 7-8% | 0 / 0 | 1 | 0.0001 | 239.9996 |
| NVDEC decode loop | 1,133 fps | 12% (NVDEC 43%) | 0 / 0 | 0 | 0.00014 | 239.999 |
| NVDEC loop, idle class | 1,114 fps | 13% | 0 / 0 | 0 | 0.00014 | 239.999 |
| CPU ONNX 8 threads, below-normal | 28 fps | 7% | 0 / 0 | 1 | 0.0008 | 239.999 |
| CUDA batch 1 (x2) | 149-155 fps | 57-65% | 0 / 0 | 1 | 0.00013 | 239.9998 |
| CUDA batch 1, idle class | 147 fps | 61% | 0 / 0 | 1 | 0.00015 | 239.9998 |
| CUDA batch 4 | 351 fps | 78% | 0 / 0 | 2 | 0.00015 | 240.000 |
| CUDA batch 8 | 432 fps | 79% | 0 / 0 | 6 | 0.0002 | 240.000 |
| CUDA batch 16 | 486 fps | 82% | 0 / 0 | 5 | **0.18** | 238.1 |
| CUDA batch 16, idle class | 473 fps | 83% | 0 / 0 | 2 | **0.16** | 238.8 |
| CUDA batch 64 (x2) | 447-453 fps | 83-84% | 0 / 0 | 10 | **0.66-0.92** | 230.5-235.1 |
| CUDA batch 4 at 25% duty (sleep) | 77 fps | 25% | 0 / 0 | **27** | 0.064 | 239.04 |

mpv's jitter is its own estimate of vsync deviation, not a measured present time; a per-frame present log (the repo's `dda-present-log.py` or PresentMon) is the next step. The idle-class call returned NTSTATUS 0 (accepted).

**4.5 Subtitles**: `ffmpeg -i EP -map 0:3 -c copy sub_3.ass` (also 0:2 SRT, 0:4, 0:8); `ass_stats.py`, `ass_overlap.py` (pysubs2); `sub_bbox.py EP SI OUT --seconds 1420 --fps 5` (`color=black:s=1920x1080` + `subtitles=filename=EP:si=SI,format=gray,bbox=min_val=128,metadata=print`); `sub_bbox_stats.py`.

**4.6 Faces**: `face_eval.py VIDEO NAME N OUT` (one frame per sampled shot; models from `dl\anime-face-v14s`, `anime-head-v20s-yv11`, `anime-person-v13s`, `yunet`; YOLO letterbox 640x384, NMS 0.7, thresholds from each model's `threshold.json`); `det_bench.py` (throughput).

**4.7 Slides**: `reg_bench.py VIDEO NAME 16` (FFT sub-pixel shift, x265 CRF 22 re-encode, five estimators); `slide_pairs.py` (NVDEC 960x540 then exact 2x2 average to 480x270, phase corr, ECC on moving pairs, residual/static/other fractions); `slide_runs.py` (runs + residual sheets); `slide_path_check.py`, `diag4b.py` (resolution agreement).

## 5. Proposed content-model fields

All coordinates in **source storage pixels** (before crop and SAR), plus `storage_w/h` and `sar`. All times are **PTS** (stream time base, rational) with a frame table for display-order index; never frame numbers alone.

```
file_facts      { key{size, sample_sha256, [full_sha256]}, streams, timebase, fps_avg, vfr, pix_fmt, range, transfer,
                  chapters[], dv{has_rpu, l5 offsets, scene_refresh}, black_level, bar_vocabulary[{rect, share}],
                  hardsub_in_bars{top,bottom,left,right}, analyzer_versions{table: version} }
frames (L0)     per frame: pts, tn_single f16, tn_manyhot f16, scdet f16, crop_rect i16x4 (raw), strip_max u8x2,
                  luma_mean u8, luma_p95 u8, dx f32, dy f32, peak u8, resid_ratio u8, explained u8, static u8, other u8, held u8
shots (L1)      { id, start_pts, end_pts, in_boundary{kind: hard|dissolve|fade_black|fade_white|wipe|slide|flash|unknown,
                  span[t0,t1], p_single, p_manyhot, source}, layout_safe, region{op|ed|credits|normal, conf},
                  crop{rect, vocab_id, conf, evidence: normal|dark|no_valid_frames}, bar_content{rect_extensions},
                  motion{speed_px_s_p50, p90, held_share}, luma{mean, p95, peak_nits?},
                  slide{is_pure, explained, static, other, direction, run_ids[]}, subjects_ref }
slide_paths     per run: frame pts[], x[] y[] (f32, cumulative source px), new_drawing[] (bool), conf[] (0-1)
subjects        per shot: tracks[{class: face|head|person|text, domain_score, boxes[{pts, x,y,w,h, score}]}], keepclear_map 64x36 (max over time)
subtitle_events per track: { track, kind: srt|ass|pgs, lang, flags, play_res, events[{start,end, text?, style, layer, an|a, explicit, overlaps,
                  rect_px? (ASS libass box / PGS bitmap), class: dialogue|sign|unknown, conf, features}] }
```

| Hard to change (decide now) | Cheap to change later |
|---|---|
| PTS-master time model + frame table (B-frames, VFR, MKV 1 ms base) | thresholds and policies: hold rules, vocabulary share, snap tolerance, purity limits, boundary confidence |
| Source-pixel coordinate system (crop-independent) | keep-clear grid size, class weights, face sampling rate |
| Two layers: raw L0 per-frame signals (about 40 B/frame) vs derived L1; **per-table analyzer versions**; GOP-aligned resumable segments (scan from any seek) | which detector models (if outputs map to the same classes) |
| File identity key and what changes it (remux = new key) | subtitle classifier rules; width render settings |
| Worker process boundary + file protocol; crash = cautious cue sheet | motion/brightness summaries (derived from L0) |
| Hot-path models = ONNX only; PyTorch-only models optional behind one interface | slide path smoothing (a performer/planner policy, not stored) |
| Decoder abstraction: FFmpeg hwaccel, never assume CUDA, CPU fallback present | precision of stored positions (f32 is enough) |
| Boundary = kind + span + both heads' scores (so "layout-change point" policy can change) | derived heat maps |
| Slide path stored as per-frame positions + confidence, not a fitted curve | |
| All subtitle tracks analysed, per-track tables | |

Raw-layer rule: when a policy changes, re-derive from L0 in seconds; re-decode only when a model changes.

## 6. Test set and metrics

**Clips (labels needed; none exist yet).**
- Anime (>= 6 episodes + 2 films): modern 16:9 TV, older 4:3 pillarbox, scope movie, dark horror (night scenes), OP/ED-heavy, CGI/mecha, streaming release with ASS signs, fansub with heavy typesetting, Blu-ray with PGS, one hard-sub in bars. Include "on threes" pans and credit slides.
- Live action (>= 4 films): scope with IMAX switching, night-heavy, dialogue-heavy, documentary/handheld, one Dolby Vision file, one VFR, one with burned-in subs in the bars, one with a station logo.
- Labels: shots (cuts, transitions with spans, flashes) for >= 1 h per domain; exact crop rect per shot (DV L5 where present); text-in-bar boxes; face/head/person/text boxes at 1 fps for 200 frames per domain; subtitle class for 300 events per corpus; 100 slide runs (pure / layered / parallax / subject-motion); end-to-end faces-on-bezel.

| Field | Metrics (pass criteria in brackets) |
|---|---|
| Shots | F1 at +-1 frame for cuts; span IoU for transitions; false splits per hour (planner-relevant); flash false positives [0 in 100 flashes]; time to first boundary after seek |
| Bars | frames cutting picture [0, hard]; residual-bar px; crop changes per hour and share on cuts [100%]; time to first correct crop [< 1 s]; dark-scene stability |
| Text in bars | recall of text frames [>= 99%]; false alarms; text px cropped [0]; per-file flag accuracy |
| Faces | recall per shot (any visible face found) per domain; box IoU >= 0.5; planner metric: share of shots with a face under a bezel after planning; false-keep-clear area |
| Dialogue | dialogue/sign/unknown accuracy and macro-F1; width error of rendered box vs truth; correct "crosses a seam" prediction; PGS rect error |
| Slides | purity precision [>= 0.95; recall secondary]; position error vs synthetic known path [<= 0.1 px, per frame]; residual after smooth path; false-smooth rate on layered shots [~0] |
| Judder inputs | correlation of speed and luma summaries with the model's inputs on 20 clips; no hard pass |
| System | analysis adds 0 dropped frames and no vsync-jitter increase under the real performer; time-to-first-cue-sheet; resume after seek |

## 7. Spike designs for what remains open

| # | Question | Method | Pass | Cost |
|---|---|---|---|---|
| S1 | GPU sharing under the real performer (flip-model window across both screens) | repeat 4.4 with per-frame present log; sweep batch 1-16, NVDEC loop, TensorRT EP, DirectML; test the duty-cycle anomaly | zero late frames, p99.9 present interval within +-0.5 ms of baseline at the chosen batch | 1 day |
| S2 | Shot detector bake-off with labels (TN vs OmniShotCut vs PySceneDetect vs combos) | label 2 anime + 2 live x 20 min; run all; tune thresholds; type accuracy | pick by F1 and false splits per hour; decide whether OmniShotCut stays | 2-3 days labeling |
| S3 | Bars on real films | the five clips in section 6 with exact labels; Stage-1 + strip guard; A/B at-cut vs eased vs hold by eye | 0 cut frames; changes only on cuts | 2 days |
| S4 | Text-in-bars on real hard subs/logos | strip guard vs RapidOCR detection | recall >= 99%, false alarms < 3% | 1 day |
| S5 | Subtitle corpus | 20 releases (fansub, streaming, SRT, PGS): classifier, libass boxes vs real render, PGS rects | >= 95% sign/dialogue on labeled events; seam-crossing prediction correct | 2 days |
| S6 | Slides: purity, ramps, live action | label 100 runs; per-pair gating; test homography vs translation on live pans; dense-flow layer split only if features fail; synthetic eased paths with known truth | purity precision >= 0.95; path error <= 0.1 px | 3 days |
| S7 | Faces at planner level | 300 labeled shots per domain; bezel-hit rate after planning with each detector set; merge rules; add saliency/COCO person detector for non-face shots | bezel-hit rate vs baseline, per domain | 2 days |
| S8 | 4K HDR / Dolby Vision | UHD remux: NVDEC speed, L5 offsets and `scene_refresh_flag` vs shots, tone-mapping not needed for analysis | numbers + oracle agreement | 1 day |
| S9 | Packaging pilot | TN + YOLO on TensorRT-RTX EP / Windows ML on this PC: footprint, cold start, speed | footprint < 300 MB, speed >= 70% of CUDA EP | 1 day |
| S10 | Pre-scan quality | first 3 min + keyframe scan on 10 files: how often is the cautious cue sheet already right | share of first-3-min shots with correct crop | 1 day |

## 8. Sources

Dates are release/push dates or fetch date 2026-10-11. [V] unless marked.
1. TransNetV2 repo and README table. https://github.com/soCzech/TransNetV2 (MIT, push 2023-12-04)
2. TransNetV2 ONNX re-export (release transnetv2-v1, 2026-06-01). https://github.com/compilons/videosteno-models
3. AutoShot. https://arxiv.org/abs/2304.06116 ; https://github.com/wentaozhu/AutoShot [S for results]
4. OmniShotCut. https://arxiv.org/abs/2604.24762 ; https://github.com/UVA-Computer-Vision-Lab/OmniShotCut ; https://huggingface.co/uva-cv-lab/OmniShotCut_v1.5
5. PERSIST. https://github.com/linty5/PERSIST ; https://arxiv.org/abs/2608.29287
6. TransVLM. https://github.com/heygen-com/TransVLM ; https://arxiv.org/abs/2604.27975
7. PySceneDetect. https://github.com/Breakthrough/PySceneDetect ; PyPI scenedetect 0.7.1
8. av-scenechange 0.24.1 (2026-06-22). https://crates.io/crates/av-scenechange
9. FFmpeg 8.1 `dovi_meta.h` (scene_refresh_flag, L5). https://github.com/FFmpeg/FFmpeg/blob/n8.1/libavutil/dovi_meta.h
10. deepghs models. https://huggingface.co/deepghs/anime_face_detection ; .../anime_head_detection ; .../anime_person_detection
11. YuNet. https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet ; https://github.com/ShiqiYu/libfacedetection
12. InsightFace licence. https://github.com/deepinsight/insightface#license
13. Ultralytics AGPL-3.0. https://github.com/ultralytics/ultralytics
14. MediaPipe (Apache-2.0). https://github.com/google-ai-edge/mediapipe
15. Face detector comparisons (YOLO5Face, SCRFD). https://arxiv.org/pdf/2105.12931 ; https://arxiv.org/pdf/2105.04714 [S]
16. Video saliency (DHF1K/ACLNet; PredJSal claim). https://arxiv.org/abs/1801.07424 [S]
17. libass `ass_parse.c`, `ass_render.c`, release 0.17.5. https://github.com/libass/libass
18. pysubs2 1.8.1. https://pypi.org/project/pysubs2/
19. mpv sub-scale-signs note [S]. https://mpv.io/manual/master/
20. ONNX Runtime 1.31.0 (2026-10-09). https://github.com/microsoft/onnxruntime/releases/tag/v1.31.0 ; https://pypi.org/project/onnxruntime-gpu/1.31.0/
21. TensorRT RTX EP. https://onnxruntime.ai/docs/execution-providers/TensorRTRTX-ExecutionProvider.html
22. Windows ML overview (updated 2026-09-08). https://learn.microsoft.com/en-us/windows/ai/new-windows-ml/overview
23. DirectML intro (updated 2026-01-09). https://learn.microsoft.com/en-us/windows/ai/directml/dml ; onnxruntime-directml 1.24.4 (PyPI)
24. PyTorch cu130 index (torch 2.14.1). https://download.pytorch.org/whl/cu130
25. PyNvVideoCodec 2.2.3. https://pypi.org/project/PyNvVideoCodec/
26. torchcodec 0.17.0. https://github.com/pytorch/torchcodec
27. VALI (archived), DALI, decord. https://github.com/RomanArzumanyan/VALI ; https://github.com/NVIDIA/DALI ; https://github.com/dmlc/decord
28. D3DKMT scheduling priority class. https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/d3dkmthk/ne-d3dkmthk-_d3dkmt_schedulingpriorityclass
29. NVIDIA MPS platforms. https://docs.nvidia.com/deploy/mps/when-to-use-mps.html
30. CUDA green contexts. https://docs.nvidia.com/cuda/cuda-programming-guide/04-special-topics/green-contexts.html [S]
31. Chapiro et al. 2019. https://dblp.uni-trier.de/pid/182/9009.html ; https://unpaywall.org/10.1145%2F3338696 [S]; US 10,368,031. https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/10368031 [S]
32. AnimeRun (NeurIPS 2022). https://arxiv.org/abs/2211.05709 [S]
33. CameraBench (NeurIPS 2025). https://arxiv.org/abs/2504.15376 [S]
34. RapidOCR / PaddleOCR. https://github.com/RapidAI/RapidOCR ; https://github.com/PaddlePaddle/PaddleOCR
35. `ort` crate 2.0.0-rc.13 (2026-07-28). https://crates.io/crates/ort
36. Earlier reports (repo): `plans/reports/researcher-261010-1846-black-bar-detection-fast-start-steady-crops.md`; `plans/reports/spike-261011-1034-one-window-across-mixed-refresh-rates.md`; spikes `find-pan-shots.py`, `make-real-pan-ab.py`.
37. Local: FFmpeg 8.1 gyan build, mpv 0.41.0 (project `tools/`), the two clips (read-only), PC probes above [L].
38. Earlier-pass notes on ORT / `ort` crate status (2026-10-03) and CUDA-library unloading, used as leads and re-checked where quoted [K].

## 9. Unresolved questions

1. Which shot detector is better on anime, and by how much? No labeled anime data found; all numbers are eyeballed on one episode. (S2)
2. Does the per-shot union + vocabulary hold on real scope films with IMAX switching, real dark scenes next to bars, and real logos/hard subs? Only synthetic bars on anime were tested. (S3, S4)
3. Hard-subs policy: keep bars file-wide, or per shot? Needs by-eye A/B.
4. Dialogue vs sign: what accuracy do the heuristics reach on fansub typesetting and PGS? Streaming ASS here has no `\pos` at all. (S5)
5. Slide purity: how to handle ramp-in/out and live-action rotation/zoom; is translation-only enough? Does smoothing look right on live action (ADR 0016 open)? (S6)
6. Faces in live action: only about 5 shots with faces locally; real detector choice and a person/saliency layer for non-face subjects are untested. (S7)
7. GPU sharing under the real two-screen performer and non-windowed flip model; the idle-class and duty-cycle results (n=1) need replication. (S1)
8. 4K HDR remux speed, Dolby Vision L5 and `scene_refresh_flag` as oracles; PGS rect extraction. (S8)
9. Licence: AGPL-class detector architectures (deepghs/YOLO), non-commercial InsightFace models, a third-party TransNetV2 ONNX (re-export from upstream), Dolby judder patent: matter once the licence is chosen.
10. Non-NVIDIA GPUs on other setups: DirectML is in sustained engineering; is the CPU/D3D11VA fallback fast enough for the pre-scan? (S10)
11. OP/ED handling: treat as one low-confidence region, or exploit cross-episode fingerprints (outside "from the file alone")?
12. ORT TensorRT EP exists in the wheel but was not tested; TensorRT-RTX and Windows ML pilots pending. (S9)
