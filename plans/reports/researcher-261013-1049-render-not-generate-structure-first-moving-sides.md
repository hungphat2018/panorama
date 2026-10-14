> Saved by the parent session from the agent's returned text (the agent's harness forbade writing report files), 2026-10-13.

# Render, not generate: structure-first for the moving sides (JJK S03E12), 2026-10-13


Tags: **[V]** read today on the cited page/API/PDF (digests are not re-checked against PDFs); **[S]** titles or snippets only; **[R]** recalled; **[E]** my arithmetic or inference; **[L]** measured by my CPU scripts on the user's episode; **[eye]** looked at frames.
Method: three CPU-only decode probes at 480x270 grey (BelowNormal priority, no GPU, ~30 min wall). Scripts and outputs are in the session scratchpad. Reproduction recipes are in agent memory.

Terms. **Step**: frame i-1 to i. **Repeat**: step with mean luma change < 0.5 (the same drawing shown again). **Band**: the outer 25% (or 10%) columns of the real picture on each side; it stands in for the sides, which have no pixels. **Unexplained area (U)**: share of band pixels that differ by more than 16/255 from the previous frame under every hypothesis tried (stand still, slide by one shift, slide plus brightness gain). **Plate**: a painted still. **Mosaic**: the shot's own frames stitched along the camera path. **Drift limit**: mean absolute luma difference of 6 levels between the live band and the plate band (the repo's `edge-band-change.py` limit).

## 1 Decision + executive summary

**Decision.** Build the structure path as the base layer and the fallback of the moving sides. Do not expect it to carry an action episode alone. On JJK S03E12 [L]:
- Holds, cuts and steps whose edge bands are exactly "still or one rigid slide" make up **63% of steps**. With small patches (<=10% of band pixels unexplained) this rises to **81%**.
- The other **19% of steps must be generated**: 4.7 new drawings/s, mean 19% of band area. That is 6% of the pixel work of "generate every drawing" (17x less).
- If the sides come only from painted plates (still or camera-slid, never updated), the plate matches the live edge in only **33%** of frames (1 plate/shot) to **53%** (1 plate/s). Plates go stale within 0.25-0.5 s.
- The lift from 33-53% to 63-81% comes from updating the sides every drawing: warp the previous render by the camera step, add seam pull and gain, and patch. The patches need a generator.

Findings, ranked by how much they change the plan:
1. **Camera motion measured on the picture's centre extrapolates to the edge** [L]. A model fitted on the centre columns explains the outer 25% bands in 62.7% of new-picture steps (one translation) or 65.4% (zoom+rotation+shift). The band's own motion gives 65.0%. The sides need no pixels of their own.
2. **Rigid camera moves are a small part of this action episode** [L]: 8.9% of steps pure rigid, 2.6% rigid with a tracked character, 29% camera-moving multi-motion, clear zoom 2.3%, shake-like 7%. Most generator work is effects and fast action, not camera.
3. **The shot's own frames are not a source of real side pixels** [L]. In rigid-slide runs (13.5% of steps) they cover 19.5% of the side area on average (median 7.5%). The painter completes at least 80%.
4. **Canonical-image/atlas methods (CoDeF, NaRCan): skip.** Per-video optimisation takes 5-20 min per 100 frames, and they are documented to fail on large non-rigid motion. For rigid pans the canonical image is the mosaic. I found no feed-forward successor [V].
5. **Example-based propagation (EbSynth, Texler): skip as an engine.** It needs real target frames as the colour guide, and the sides have none.
6. **Warp-and-refine outpainting has no off-the-shelf tool with weights.** Precedents exist (VidPanos, TVCG-2025 cartoon outpainting, Seen-to-Scene), none anime at 32:9 or real time.
7. **Cut-off characters: no verified cheap tool.** Inbetweeners need sketches or poses. Edge-touching characters explain only ~29% of generator-needed steps; ~70% is effects, fast motion and atmosphere [L, eye].
8. **Effects: no cheap transfer beyond global gain.** A brightness gain rescues only 2.4% of steps. Beams, speed lines and lightning must be generated or continued procedurally.

**What the generative part must still do:**
- (a) Paint plates per shot (0.40/s) and extension canvases per rigid run. This is per shot or run, not per frame.
- (b) Draw the seam zone on ~4.7 flagged drawings/s: effects, new or fast objects, cut-off characters.
- (c) Patch residuals on ~15.6% of steps (<=10% of the band).
- (d) Re-key periodically to bound blur and drift.

Budget arithmetic [E], at 30% of the GPU and one component alone: plates <=0.75 s each (170 s today, 230x too slow); flagged drawings <=64 ms each (Wan2.2-VACE-Fun-A14B at 54 s is 840x too slow).

## 2 Invariant and lever map

**Invariant.** At every new drawing, the sides must continue the picture at the seam (style, colour, composition, cut-off objects) and move as the picture's world moves, without altering the picture. Observation: no frame contains side pixels, so every method that consumes target frames loses its input. The only motion evidence is the picture itself.

| Lever | Supplies | Cost | Measured reach on JJK |
|---|---|---|---|
| L0 hold | previous render again | 0 | 35.9% of steps are repeats (31.4/39.6/43.4% at thresholds 0.3/0.75/1.0) |
| L1 plate | painted still per shot | 0.40 plates/s | valid in 29.6% of frames |
| L2 camera slide | plate sampled along the centre-fitted path | ms | adds 4-6 points over still plates |
| L3 seen mosaic | real pixels from other frames | free | 19.5% of side area in rigid runs |
| L4 propagate | previous render warped by the step, plus seam pull and gain | ms | exact for 25% of steps (still/slide) |
| L5 local patch | generator on <=10% of the band | small | 15.6% of steps (+2.3% gain-only) |
| L6 seam-zone generation | generator on the flagged zone | large | 19.4% of steps |
| L7 effects | gain, procedural continuation | small | only gain verified (2.4%) |

## 3 Findings per question

### Q1 Anime camera motion
| Item | Facts | Fit |
|---|---|---|
| AnimeRun (NeurIPS 2022 D&B) | 2,819 frames, 30 clips, Blender open movies in 2D style, moving background included; RAFT EPE 5.40 (line 3.75, flat 6.29), GMFlow fine-tuned 3.35 (flat 3.85); flat error is about 1.5x line error [V, digest]; repo has no licence [V] | only camera-bearing anime flow set, synthetic |
| LinkTo-Anime (2025-06-03) | character-only 3D renders on white, 395 sequences, 24,230 train frames; RAFT/GMA fine-tuned EPE 0.20; errors rise above 50 px motion [V] | wrong domain for camera |
| Toon3D (2405.10320) | recovers cameras/geometry from inconsistent cartoon images by deforming them [V] | multi-image, not per-step |
| TVCG 2025 (Wu et al., DOI 10.1109/TVCG.2024.3379125) | only anime outpainting paper: 4:3 to 16:9; region-guided flow for solid colours, stitched guide frame, voting fusion of aligned neighbours [V abstract] | precedent for propagation |
| General flow: SEA-RAFT (ECCV 2024), MEMFOF (ICCV 2025, 2.09 GB at 1080p), NeuFlow v2 | exist [V abstracts]; none anime-evaluated | optional upgrade |
| My probe, OpenCV DIS flow plus mode | works on real TV frames; 1.2/2.5/9.8 ms per 480x270 pair (ULTRAFAST/FAST/MEDIUM, 2 threads, loaded box) [L] | adequate, computed ahead |

No TV-anime camera-motion benchmark exists (HF papers and web searches returned only the rows above) [V]. The pipeline must self-validate: the repo's gate `after <= max(1, 0.35*before)`, plus the agreement share.

Measured camera behaviour [L], shares not exclusive:
| Behaviour | New-picture steps | All steps |
|---|---|---|
| Pure rigid pan/tilt | 14.3% | 8.9% (53% vertical-dominant; shift p50 3.7 px, p90 46 px per new picture at 1080p) |
| Rigid plus screen-locked layer (tracked character) | 4.2% | 2.6% |
| Clear zoom | 3.7% | 2.3% |
| Clear rotation | 1.7% | 1.1% |
| Shake-like windows (path 3x or more the net shift) | 11.1% | 7.0% |
| Neither translation nor zoom explains 60% of the flow field | 45.1% | 28% |

Phase correlation (the content model's `dx, dy`) is unreliable on large steps: median `pc_peak` is 0.21 for shifts of 20 px or more, which is 20% of new steps. Use flow modes for the camera path.

**Extending camera motion into the sides** [L]. The test: new-picture steps, both 25% bands, at most 10% of pixels unexplained.
| Hypothesis | Steps explained |
|---|---|
| stand still | 53.1% |
| oracle, band's own slide | 65.0% |
| centre translation | 62.7% |
| centre similarity (zoom+rot+shift) | 65.4% |
| centre two translations | 67.0% |

- A homography is unnecessary for flat background art [E]. VidPanos uses a homography for pure rotation and SLAM for parallax [V].
- The band moves more than 1 px differently from the centre in 38% of centre-moving steps, but this does not hurt the extrapolation. The second layer adds +4 points.
- Under zoom, the displacement grows linearly with distance from the zoom centre, so the sides need the scale applied about the same centre [E].
- Shake is the same rigid jitter applied to the sides [E].

### Q2 Canonical content
| Method | Facts | Verdict |
|---|---|---|
| Layered neural atlases (2021) | >10 h per video [V via CoDeF] | no |
| CoDeF (2308.07926, CVPR 2024) | 5 min per 100 frames on an A6000 (1-10 min range). Stated limits: per-scene optimisation, extreme view change, large non-rigid deformation, large occlusion. Single field by default; per-layer fields via SAM-Track [V] | no |
| NaRCan (2406.06523) | 20 min per 100 frames on an RTX 4090; homography plus residual MLP plus diffusion prior; may fail on dramatic or non-rigid motion [V] | no |
| Newer 2025-26 | no feed-forward canonical-image or atlas method found (HF papers, extended web search; a Nov-2025 overview still says per-scene optimisation) [V negative] | no |

Reading [E]:
- For rigid pans the canonical image is the mosaic and the deformation is the camera path, which `scroll-plate.py` already does.
- At 100 frames per 5-20 min, 679 shots would take about 35-90 h of optimisation.
- Anime non-rigid motion, with a new drawing every frame (87% of drawings hold one frame), has no stable canonical image.
- Canonical images also contain only visible content, so the sides would still need the painter.

### Q3 Example-based propagation
| Method | Facts | Fit |
|---|---|---|
| Stylizing Video by Example (Jamriska 2019, ACM TOG 38(4)) | I read the PDF [V]. Guides: Gcol (target colour frames, essential), Gmask, Gpos, Gedge, Gtemp. 9 s per 1-MP frame on a GTX 1070 (CUDA PatchMatch), plus <20 s CPU preprocessing per MP (SIFT flow). New content needs a new keyframe; sensitive to lighting change | needs target frames |
| Texler 2020 (2004.14489) | 17 fps at 640x640 (RTX 2080); 5 min training per 512x512 keyframe; "cannot invent consistent stylization for new features" [V digest] | same |
| FFP-300K (2601.01720), PropFly (2602.20583), FreeViS (2510.01686) | first-frame propagation / training-free stylization on existing source video [S] | same |

Guides that could exist beyond the frame edge [E]:
- A positional guide (coordinates from the camera model) extends trivially.
- An edge guide needs own VP/line extrapolation (see the previous report).
- A temporal guide (previous render, warped) exists.
- The colour guide does not exist, and it is the essential one.

Without it, EbSynth reduces to "warp the previous output and re-synthesise patches from the plate". That is lever L4 with real strokes copied, which fits the style-fidelity goal. It is not a model to adopt.

### Q4 Warp-and-refine, propagation, holes
| Item | Facts | Fit for the sides |
|---|---|---|
| Go-with-the-Flow (2501.08331, CVPR'25 oral) | warped noise, fine-tune needed, inference cost equals the base model; Modified Apache with a credit clause [V] | noise warped by the camera path keeps a per-frame generator coherent; needs fine-tuning |
| StreamV2V (ICLR'25) | 20 FPS on an A100; feature bank; SD1.5/LCM; UT Austin research licence [V] | needs a source video; licence |
| FlashRender (2609.03563, 2026-09-03) | camera-retake in seconds, 25x lower sampling cost [V abstract] | re-renders a source video; watch |
| 4DStreamCtrl (2608.25479, 2026-08-26) | camera plus object control, causal 4-step student, 20 FPS at 480p on one high-end GPU [V abstract] | watch; no mask/outpaint stated |
| ReCamMaster, TrajectoryCrafter, GEN3C | camera-controlled re-rendering [S] | heavy, no anime evidence |
| VidPanos (SIGGRAPH Asia 2024) | homography canvas, then Lumiere/Phenaki completion; Lumiere base 300 TPU-min for 84x128x512; limits: fast motion, unseen regions [V] | precedent, far too slow |
| Seen-to-Scene (CVPR'26 Findings) | flow propagation plus generation; MIT code, no weights [V] | nearest design; unusable now |
| ProPainter | 0.083 s/frame at 432x240, 0.249 at 864x480 on a V100 [V]; NTU S-Lab licence, last push 2025-02 | needs seen pixels |
| DiffuEraser (Apache-2.0) | 250 frames at 640x360 in 92 s on an L20, 12 GB [V README] | same |
| MiniMax-Remover | 6 steps, no CFG; no licence field [V] | same |
| OmnimatteZero (SIGGRAPH Asia 2025) | claims real-time background-layer separation [V abstract] | could give clean background plates |

Hole-filling: the strip a camera step reveals is small. For rigid steps it is p50 3.7 px, p90 46 px per new picture at 1080p [L]. It is cheap to fill from the plate or mosaic ahead of the camera. DLSS-style frame generation re-shades existing pixels from motion vectors and does not invent regions [V, earlier pass]; it is not a source of new side content.

Propagation inpainters need seen pixels. For outpainting at 32:9 they degrade to generation (§4 mosaic coverage). The TVCG cartoon paper works because 4:3 to 16:9 is a narrow extension of 12.5% per side [E].

### Q5 Foreground crossing the seam
| Tool | Needs | Fit |
|---|---|---|
| ToonComposer (2508.10881), AniDoc (2412.14173), LVCD (2409.12960), ToonCrafter | line art or sketches for each target frame, plus a colour reference [V abstracts] | no sketch exists beyond the edge |
| CoNR (2207.05378, IJCAI 2023) | character sheets plus target landmarks [V] | pose of the hidden part is unknown |
| Illustrated-character pose estimation (Chen & Zwicker, WACV 2022) | full-body illustrations [V] | partial bodies untested |

No verified tool continues a cut-off anime character at the drawing rate from the visible part plus references. Measured, from stride-12 detector boxes (score >=0.4) [L]:
- A person, face or head box touches a picture edge in 21.8% of sampled frames.
- Among those new-picture frames, the worse band is static 18.9%, slide 6.8%, "mostly" 33.8%, gain-only 4.9%, other 35.5%. For non-touching frames "other" is 27.3%.
- Edge-touching characters are only ~29% of generator-needed steps. Detector misses in action poses make this a lower bound.

Cheapest handles [E]:
- Warp the previous continuation by the seam-local flow between generator calls.
- Hold the continuation while the picture's drawing holds.
- Generate only when the seam-band U exceeds the threshold.

### Q6 Effects and lighting
| Quantity [L] | Value |
|---|---|
| Steps rescued by a plain brightness gain (U>0.10 without gain, <=0.10 with it) | 3.8% of new, 2.4% of all steps |
| Steps with mean luma jump >=10 levels (not cuts) | 3.5% of all steps |
| Flash-like events (jump >=10, returns within 6 frames) | 832 events, 2.0% of steps |

Sixteen random generator-needed steps, by eye [eye]:
- Beams, energy glow, speed lines and bursts: 9.
- Fast fur/fist smears and close-ups: 3.
- Characters crossing the seam mid-air: 2.
- Drifting clouds or debris: 2.

Options:
- Global flashes: apply a gain/offset from the picture to the plate (the existing colour fit plus a per-step gain). Cheap, verified on 2.4% of steps only.
- Atmosphere: advect a texture along the camera and slow drift [E, untested].
- Beams and lightning crossing the seam: generator or procedural continuation along the exit angle [E, untested].
- Relighting nets (TC-Light 2506.18904, Light-A-Video 2502.08590) target real video and are heavy [V abstracts]; not a fit.

## 4 Measured motion-class shares and design of the structure path

### 4.1 Shares (JJK S03E12, 40,998 steps, 1,710 s) [L]
Assumptions: repeat = mean luma change <0.5; the picture's band is a proxy for the sides; pixel threshold 16/255; 10% area rule.

| Class | Share of all steps | Share of new-picture steps |
|---|---|---|
| Cut | 1.7% | |
| Repeat | 35.9% | |
| New picture (14.97/s) | 62.4% | 100% |
| Whole-picture class: rigid, one shift | 8.9% | 14.3% |
| Whole-picture class: rigid plus screen-locked layer | 2.6% | 4.2% |
| Whole-picture class: camera moves, multi-motion | 29.0% | 46.5% |
| Whole-picture class: camera still, small local change | 14.7% | 23.6% |
| Whole-picture class: camera still, big local change | 6.4% | 10.3% |
| Whole-picture class: shift unreadable | 0.8% | 1.2% |
| Both 25% bands, worse band decides: static (still plate holds) | 15.8% | 25.2% |
| Both 25% bands: rigid slide | 9.4% | 15.0% |
| Both 25% bands: mostly explained (<=10% unexplained) | 15.6% | 25.0% |
| Both 25% bands: explained after a gain | 2.3% | 3.8% |
| Both 25% bands: other | 19.4% | 31.1% |

A relaxed rigid definition (explained 0.70, other 0.15, static 0.10) gives 17.2% of steps instead of 8.9%.
Drawing cadence: 87% of drawings hold one frame (67.6% of frames); holds of 2 are 8.0% of frames, of 3 are 13.7%, of 8 or more are 8.0%. This is an on-ones action episode, so hold savings are smaller than the "2-3 frames" rule.
By shot: 46.7% of shots (41% of frames) are dominated by camera-moving multi-motion; 19% by still-camera small change; 7% by pure rigid.

**Estimate of what the structure path serves [E]:**
| Tier | Steps | What does the work |
|---|---|---|
| Exact: holds + cuts (plates) + static + slide | 63% (35.9+1.7+15.8+9.4) | hold, plate, camera slide |
| Add "mostly" and gain-only with small patches | 81% | plus patch generator on <=10% of the band |
| Generator-needed | 19.4% (4.7 drawings/s) | seam-zone generation |

Generator duty vs trigger (worse 25% band U above tau):
| tau | Drawings/s | % of new | Mean U of triggered band |
|---|---|---|---|
| 0.02 | 8.3 | 55% | 0.12 |
| 0.10 | 4.7 | 31% | 0.19 |
| 0.30 | 1.4 | 9% | 0.34 |

The mean unexplained area per new drawing is 0.071, which is 1.06 band-areas/s against 15.0 for "generate everything".
Generator-needed bursts are short: median 1 step, p90 6; only 28% of those frames lie in segments of 1 s or more. Structure-servable stretches are long: 66% of those frames lie in segments of 1 s or more.

**Plate-only variant (no propagation)** [L]: frames whose two 25% bands match a plate within 6 levels. The "rule" column slides the plate when it moved 1 px or more.
| Plates | Per film s | Still only | With slide rule | New-picture frames only (rule) |
|---|---|---|---|---|
| 1 per shot (679) | 0.40 | 29.6% | 33.3% | 16.9% |
| every 4 s | 0.47 | 34.7% | 39.4% | |
| every 2 s | 0.67 | 39.4% | 44.8% | |
| every 1 s | 1.17 | 47.2% | 52.9% | 32.4% |

Drift 4 or 8 levels moves these by about -5 and +5 points; the 10% band by about +4. Validity by distance from the plate frame (rule): 0.04-0.25 s 52%; 0.25-0.5 s 36%; 0.5-1 s 30%; 1-2 s 25%; floor ~21% (the static shots). With a plate every second, 44.5% of the failing new frames are step-level "other" and 25.9% are "mostly". Only 2.9% of step-level "other" frames still match the plate.

**Mosaic coverage** [L], rigid runs of 0.5 s or more (160 runs, 5,541 frames = 13.5% of steps): real seen pixels are 19.5% of the side area on average (median 7.5%); 18% of frames have at least 50% and 1% have at least 90%. The 46 runs of 1.5 s or more average 15.9%.

**Seam** [L]: 17.5% of steps have more than 10% of the 10% near-seam band unexplained, 5.5% heavily (>30%). Both sides at once need the generator in 9.6% of steps and exactly one side in 9.8%.

### 4.2 Design
| # | Component | Per-frame cost | Notes |
|---|---|---|---|
| C1 | Content scan (exists) | scan 158 fps on GPU decode [L, from content.json] | shots, signals, slide paths |
| C2 | Camera-model fitter: DIS flow on the centre columns, mode plus similarity RANSAC | 1.2-9.8 ms per new drawing, CPU, ahead of the playhead [L] | 15 drawings/s is at most 0.15 CPU-s per film-s |
| C3 | Step planner: U of the picture's bands under hold, slide, gain; states HOLD / STILL / SLIDE / GAIN / PATCH / GEN | about 1-2 ms CPU [E] | computable ahead; uses the real picture only, not the hidden truth |
| C4 | Plate store plus extension canvas: painter per shot (cuts) and per rigid run; real mosaic pixels kept where seen; re-key when drift passes the limit | painter 170 s per plate today [L] | adaptive re-key, not fixed K |
| C5 | GPU renderer: sample the canvas at the camera offset (translation plus scale), colour fit plus per-step gain, seam pull, real picture pasted | about 0.1-0.3 ms [E] | extends existing seam pull and colour fit |
| C6 | Propagator: previous side render warped by the step; hole strip from the canvas | about 0.2 ms [E] | re-key every N steps (N to be tested) to bound blur |
| C7 | Hand-over: mask M = unexplained picture pixels dilated across the seam, plus zone box; generator gets (render, picture, M, previous output, plate, references) and returns the zone only; feathered blend | generator | the only per-drawing generative call |
| C8 | Scheduler by playhead deadline: plates for cuts, then rigid canvases, then GEN zones, then patches | | fallback chain below |

**Fallback chain (never stall):** generated zone, else slid plate, else held previous render, else colour field from the seam. Because the film is local, the planner can flag drawings seconds ahead and the generator works ahead on flagged drawings only.

**Hand-over rule.** Per side and per new drawing:
- U <=0.02: structure only.
- 0.02-0.10: structure plus local patch.
- U >0.10 or near-seam heavy or an edge-touching character with U >0.02: generator.
- Plates are re-keyed at cuts, at hidden-cut flags, and when plate drift exceeds the limit.

**Failure modes:**
| Failure | Evidence | Mitigation |
|---|---|---|
| Plate staleness | 25% valid at 1-2 s | propagate per step; re-key |
| Flat-fill flow failure | credits "pan" agreement 0.07 (parent); phase-correlation peak 0.21 on big shifts | flow mode plus gate; do not slide on low agreement |
| Fast action / smear frames | 20% of new steps move 20 px or more | hand over; do not slide |
| Zoom, shake | 2.3% and 7% of steps | scale about the zoom centre; same jitter on the sides |
| Leading-edge coverage | mosaic 19.5%; painted part risks the seam (parent: x1.56 on one tilt) | colour fit, seam pull, painter sees the mosaic |
| Resample blur in propagation | not measured | Lanczos, re-key every N |
| Screen-locked overlays or tracked character | 'static' share inside slides | exclude from the slide mask |
| Effects at the seam (beams, lightning) | 9 of 16 hard steps | generator or procedural |
| Hidden cuts inside shots | parent: detector misses smear-frame runs | step-level reset on a jump |

## 5 What to try first (cheap, decision-changing only)
1. **Replay the structure path on shot 534 and two other action moments, no new model.** Use the existing scroll plate, the centre-fitted camera path, per-step propagation, gain and seam pull. Colour the flagged zones in a debug video. The user checks by eye: where the zones are not flagged, do the sides follow the picture and is the seam acceptable? This decides whether the 63-81% claim holds perceptually, which my metric cannot say.
2. **Plate-only against propagated, same clip.** The user judges the staleness (the 6-level limit and the 16-level / 10% thresholds) at 4, 6 and 8 levels.
3. **Probe the generator's hardest job.** Run the existing masked painter on 8 of the 16 hard-step zones (beams, speed lines, lightning). If even the 14B/Qwen path fails, effects need a procedural or copy-continue path, and the student should not be asked to learn them.
4. **Spec the hand-over from the content model.** Extend `route-by-motion.py` from "band holds >=90% of steps" to the 6-state step planner. Nothing needs a GPU.

Ranked: (1) adopt structure as base plus fallback with the hand-over rule above; (2) painter per shot and per rigid run with adaptive re-key; (3) generator on flagged zones only; (4) do not adopt canonical atlases, EbSynth/Texler or inbetweeners. Watch Seen-to-Scene weights, 4DStreamCtrl and FlashRender.

## 6 Sources (read 2026-10-13 unless noted)
Papers via arXiv API or abstract pages: CoDeF 2308.07926 (2023-08-15; HTML read); NaRCan 2406.06523 (2024-06-10; HTML); AnimeRun 2211.05709 (2022-11-10; ar5iv); LinkTo-Anime 2506.02733 (2025-06-03; HTML); Toon3D 2405.10320; Texler 2004.14489 (2020-04-29; ar5iv); Go-with-the-Flow 2501.08331 (2025-01-14); StreamV2V 2405.15757 (2024-05-24); FlashRender 2609.03563 (2026-09-03); 4DStreamCtrl 2608.25479 (2026-08-26); VidPanos 2410.13832 (2024-10-17; HTML); ProPainter 2309.03897 (HTML); DiffuEraser 2501.10018; MiniMax-Remover 2505.24873; OmnimatteZero 2503.18033; Seen-to-Scene 2604.14648 (2026-04-16); ToonComposer 2508.10881; AniDoc 2412.14173; LVCD 2409.12960; CoNR 2207.05378; 2108.01819; SEA-RAFT 2405.14793; MEMFOF 2506.23151; NeuFlow v2 2408.10161; TC-Light 2506.18904; Light-A-Video 2502.08590.
Titles only [S]: FFP-300K 2601.01720; PropFly 2602.20583; FreeViS 2510.01686; ReCamMaster 2503.11647; TrajectoryCrafter 2503.05638; GEN3C 2503.03751; INVE 2307.07663; DiffusionAtlas 2312.03772.
TVCG 2025 abstract: Europe PMC REST for PMID 38502621, `10.1109/TVCG.2024.3379125`, published 2025-02-27.
Jamriska et al., Stylizing Video by Example, ACM TOG 38(4) 107, 2019: PDF from dcgi.fel.cvut.cz, read.
GitHub metadata and README facts (`gh api`): ProPainter (pushed 2025-02-19), DiffuEraser (Apache-2.0, 2025-04-17), MiniMax-Remover, VideoPainter, Go-with-the-Flow (2025-10-13), StreamV2V (2025-12-29), AnimeRun (2023-08-02).
Local: `spikes/ai-surround/out/jjk/content/{signals.json,signals.bin,shots.json,slide-paths.json,subjects-raw.jsonl,edge-band-change.json}`, `spikes/ai-surround/scroll-plate.py`, `edge-band-change.py`, and the two named prior reports.
Earlier-pass facts reused: Novy thesis 2013 (affine camera applied to side panels), Adapting VACE (2602.14381), DLSS 5 notes. They were not re-verified today.
Volume: about 16 WebFetch/WebSearch calls and about 45 HF/arXiv/GitHub/Europe PMC API requests. Failed or wasted: AnimeRun PDF returned raw bytes; bvsalud ECONNREFUSED; one arXiv OR query HTTP 400.

## 7 Unresolved questions
1. Do the thresholds (16/255 per pixel, 10% area, 6-level drift) match what the user perceives as continuous? Only step 2 in §5 can say.
2. How much blur or drift builds up when the previous render is warped over many steps? Not measured. N (the re-key interval) is unknown.
3. Are the "other" steps mostly effects? I looked at 16 samples only. No effect detector was run.
4. Only luma at 480x270 was used. Coloured effects of similar luma, and thin lines lost in the downscale, are under-counted.
5. Hidden changes beyond the seam cannot be measured from the picture. No model can see them.
6. One episode (action). A comedy episode will have a different split (repeats 74% on the earlier test episode).
7. Local patch generation: real generators need rectangles with context, so the 6% pixel-work figure is optimistic. The patch tool's cost per zone is open (other agents).
8. Painter completion of extension canvases at the leading edge: measured once by the parent (tilt x1.56); not for pans over several seconds.
9. Whether TVCG-2025's method (code not found) would help; I could not read the paper itself, only the abstract.
