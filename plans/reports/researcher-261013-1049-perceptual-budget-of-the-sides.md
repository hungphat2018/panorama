---
title: "Perceptual budget of the generated sides: what the eye needs at each distance from the picture"
date: 2026-10-13 (~12:00 Asia/Bangkok), researcher, perception pass
---

> Saved by the parent session from the agent's returned text (the agent's harness forbade writing report files), 2026-10-13.


Tags: **[V]** read in full text/PDF this pass · **[Vd]** abstract or WebFetch digest only, not re-checked · **[S]** search snippet / secondhand · **[R]** recalled, unverified · **[E]** my arithmetic or inference (scripts: scratchpad `pv/scripts/{geometry,budget-integral,band-table}.py`).
Terms once: **eccentricity** = angle between where you look and the thing (a spot 30 cm beside your gaze point on a screen 70 cm away = 23°). **cpd** (cycles per degree) = light-dark stripe pairs per degree; a thumb at arm's length is ~2° wide; 30 cpd = "20/20". **px/deg** = screen pixels per degree (27" 1440p at 70 cm = 52). **foveated rendering** = draw less detail where the viewer is not looking. **temporal aliasing / boil** = shimmer or crawl of under-sampled detail changing frame to frame. **flicker fusion (CFF)** = blink rate above which a flashing light looks steady. **pooling / crowding** = periphery averages detail over a region about half as wide as the eccentricity, so things are seen as texture, not exact shape. **vection** = illusory self-motion from a big moving scene (the train next to yours starts moving). **forced choice** = two versions, you must pick one, chance = 50%. **drawing / on twos** = anime holds each drawing 2-3 film frames. **band** = strip of a side by distance from the picture edge.

## 1 Decision + executive summary

**Decision.** Size the sides by *errors and time*, not by pixels. Pixels are already cheap.

Ranked recommendation:
1. **Temporal rule (non-negotiable): a side pixel changes only when the picture changes (new drawing, camera step, cut).** No boil, no mid-shot swaps, cut-synchronous switching, motion that follows the picture. Zero headroom here [V: Strasburger, Guenter, Patney, Novy].
2. **Graded resolution by distance from the picture edge: ~1/2 linear within 5°, 1/4 to 15°, 1/8 beyond** = ~10% of native side pixels on 2 screens, ~6% on 3 = **0.2-0.4 Mpx for both sides together** [E on V models]. Normal viewing needs less (1-13%, table 4.3); the margin covers glances at the picture edge. This is already the working size of the current candidates (klein 960x544 = 0.52 Mpx, VACE-1.3B 848x480 = 0.41 Mpx), so **resolution is not where the compute is; time is** (and the model's own resolution floor).
3. **Level of brightness/colour and the seam step** are the next most visible; chroma detail is not [V Patney; V Novy].
4. **New-content refresh rate: weakest evidence.** Prior [E]: 3-6 Hz refresh + picture-clocked camera warp between; frozen fails on action (your verdict). **E2 decides.**
5. Rejected: uniform full resolution; 24 fps generation; independent 12 Hz sample-and-hold (phase-beats against 2s/3s drawing cadence, [E]); still plate for action.

Executive summary:
- **Detail is the least visible error.** Grating acuity falls to ~1/10 of foveal at 23° and ~1/16 at 40° (E2 = 2.6-3.0) [V Strasburger]. Gaze-tracked user studies tolerated MAR slopes of 1.3-1.65 arcmin per degree, 2-3.5x steeper than classic acuity [V Guenter] (tested only to 23°). Periphery is recognised by texture statistics with pooling regions 0.48 x eccentricity wide: layout errors far out are invisible when fixating [V Freeman & Simoncelli].
- **Time is the most visible error.** Peripheral flicker sensitivity is not worse than foveal and rises for large fields (50 Hz TV looks steady foveally, flickers peripherally) [V Strasburger]. Under-sampled periphery gave temporal aliasing that was "surprisingly prominent" [V Patney] and "crawling... the eye is drawn to it" [V Guenter]; motion/flicker also pull gaze [S Mital]. Your 2026-10-13 verdict (sides 0.00/frame vs picture 13.9) is the same failure Infinity-by-Nine users reported: "distracting when the main screen was moving and the side screens weren't" [V Novy].
- **Periphery-extension systems used far less than centre fidelity and were liked**: IllumiRoom periphery 28 px/deg vs TV 64 [V]; Infinity-by-Nine sides "less than half SD" [V]. Their problems were motion mismatch, cuts, over-bright/uncalibrated sides, "too much happening" [V].
- **Gaze data for anime or multi-monitor viewing: none found.** Film: >50% of the time gaze sits inside <12% of the scene [V Goldstein]. In your test episode a face touches a side edge in 6% of frames [project data]. So glances at the picture edge are rare but exist, and artifacts in the sides recruit gaze (feedback loop).
- Consistent with Kongming's plan (plate + cels, inner quarter 1/2, outer 1/4 = 11% pixels, changes slaved to the picture, upgrades at cuts) and supports it; adds: the far band can go to 1/8, and "upgrade only at cuts" is evidence-backed.
- Test on your monitors with TRUE sides: **E2, E4, E1, E3** (section 5), ~35-60 min.

## 2 Invariant + lever map

**Invariant:** with eyes on the picture, the viewer cannot tell the sides from a real continuation and is never pulled to look at them. Not pixel faithfulness. A "good" side is one the periphery cannot object to.

| Lever | Headroom | Basis |
|---|---|---|
| Spatial detail (resolution, blur) | large, graded with distance | acuity, Guenter, Patney [V] |
| Chroma detail | large | "subjects often unable to tell" chromaticity reduction [V Patney] |
| Layout / identity exactness | large far out; small within ~5° of the seam or if gaze is there | pooling 0.48 x E [V], 6% faces at edge [project] |
| Content refresh rate | medium, unmeasured | Guenter half-rate periphery [V], Flöter [Vd]; E2 |
| Coupling to picture clock (changes only with picture) | none | Novy, Guenter, Patney [V] |
| Brightness / colour level, seam step | small | Novy [V]; E4 |
| Spatial allocation without eye tracker | use distance from picture edge (centre bias) | Goldstein [V]; assumption |
| Content gating | flat low-contrast anime fills tolerate more than textures | Tursun 2019: need depends on local luminance contrast [Vd] |
| Bezels | Setup B: seams sit behind bezels (1.2-2.5° wide) and are partly masked; Setup A: seam is inside a screen, fully exposed | geometry [E] |

## 3 Findings

### 3.1 Q1 Peripheral vision numbers
Table = what the eye can use at eccentricity E (all [E] computed from [V] parameters). Foveal 30 cpd = 20/20; 48 cpd = Guenter's average.

| E | rel. acuity 1/(1+E/2.7) | finest grating cpd (f0=30 / 48) | user-study cutoff cpd (Guenter B / A) | Nyquist px/deg (Guenter B) | pooling diameter 0.48E |
|---|---|---|---|---|---|
| 5° | 0.35 | 10.5 / 16.8 | 7.6 / 6.3 | 15 | 2.4° |
| 10° | 0.21 | 6.4 / 10.2 | 4.2 / 3.4 | 8 | 4.8° |
| 20° | 0.12 | 3.6 / 5.7 | 2.2 / 1.8 | 4.3 | 9.6° |
| 30° | 0.083 | 2.5 / 4.0 | 1.5 / 1.2 | 2.9 | 14° |
| 40° | 0.063 | 1.9 / 3.0 | 1.1 / 0.9 | 2.2 | 19° |
| 50° | 0.051 | 1.5 / 2.5 | 0.9 / 0.7 | 1.8 | 24° |

- **Acuity**: MAR = MAR0 (1 + E/E2). E2: grating 2.6 (Klein&Levi 2001), 2.7 (Virsu 1987), 2.6-3.0 (Levi 1985); Landolt-C 1.0-1.14; letters 2.3-3.3; vernier 0.62-0.8; differential motion 1.77 (McKee&Nakayama 1984) [V Strasburger 2011, Table 4]. Caveat in the paper: E2 is only a normalised slope; curves bend away from linear at large E.
- **Detected vs resolved**: at 30° detection acuity ~30 cpd, resolution acuity ~5 cpd (Thibos 1996) [Vd via Patney]. Fine lines are *seen* (as faint texture/shimmer) though not resolved; blur that deletes them drops apparent contrast ("tunnel vision") [V Patney]. Anime line art lives exactly in this zone. Contrast-restoring downscale is cheap insurance (tested in E1).
- **Form**: periphery loses "the quality of form", it does not simply blur [V Lettvin quoted in Strasburger]. Metamers: texture-statistics images were indistinguishable when pooling diameter/E = 0.48 +- 0.02 (V1-only model 0.26); 4 observers, 200 ms, fixated, static, 26° window; gross scrambling (a face dissolving) went unseen [V Freeman & Simoncelli 2011]. Bouma crowding ~0.5 E [R].
- **Contrast sensitivity**: scaling the foveal curve by (1+E/E2) captures most of it, an unexplained factor 2 remains [V Strasburger]. No eccentricity CSF table fetched.
- **Temporal**: CFF rises to ~30-60° for large fields (Hartmann 1979, Tyler 1987); Granit-Harper: CFF grows with log area; Ferry-Porter slope grows with eccentricity (Tyler&Hamer 1990/93); temporal CSF eccentricity-independent to 30° when M-scaled (Virsu 1982); a 50 Hz TV "appears constantly illuminated in direct view but is perceived as flickering when viewed peripherally" (Welde&Cream 1972) [V Strasburger]. Velocity discrimination ~uniform across the field [Vd Patney citing McKee&Nakayama]. **Large side areas are flicker-sensitive areas.**
- **Colour**: some colour differentiation remains at high E [Vd Patney]; colour identification lost only beyond ~74-83° (USAF, quoted in Novy) [Vd]. Not a limit here (max ~57°).

### 3.2 Q2 Foveated rendering / video thresholds, and gaze
| Study | Set-up | N | Tolerated | Noticed |
|---|---|---|---|---|
| Guenter 2012 (SIGGRAPH Asia) [V] | 3D fly-through, 8 s pairs, 1920x1080 at 59 cm (display = 40% of foveal acuity), gaze-tracked, 3 layers | 15 | MAR = 1/48 + m E; pair test (equal to non-foveated) m = 0.0220 (1.32 arcmin/deg); ramp test (direction unnoticed) m = 0.0275 (1.65); to 23.4°; 5-6x fewer shaded pixels; outer layers refreshed at half rate (60 vs 120 Hz) with reprojection | latency "pop" of the fovea; twinkling/crawling from under-sampling; speed of motion did not change the demand |
| Patney 2016 (TOG) [V] | VR + desktop, 1-up 2-down staircase on blur growth | 4 | contrast-preserving blur tolerated 2x (HMD) / 2.3x (desktop) faster growth than plain blur; shading -70% | blur loses contrast -> "tunnel vision"; temporal aliasing "surprisingly prominent" (~3x lower threshold than contrast-preserving in HMD); colour reduction often unnoticed |
| Tursun 2019 [Vd] | luminance-contrast-aware foveation | - | savings depend on local luminance contrast | - |
| DeepFovea 2019 [S] | GAN rebuilds periphery from sparse samples | - | 10% of pixels, 14x compression, "no significant degradation" | - |
| Flöter 2025 (arXiv 2505.03682) [Vd] | HMD 90 Hz, 5 gaze-centred regions refreshed at 90/45/30/22.5/18 Hz | 15 | distraction/discomfort low; 63.6% fewer pixels/frame at config 11223 | <50% report none/barely perceivable at 11223; tearing at region borders; no reprojection; head motion |

- Note: gaze-contingent studies keep the fovea sharp wherever it looks; you have no tracker, so the picture plays that role. The mapping is distance from the picture edge (E >= d if gaze is inside the picture).
- **Gaze on film**: 20 viewers, 6 clips, 37.5 min: "more than half of the time" gaze fell inside <12% of the scene area [V Goldstein 2007]; up to ~80% of 54 viewers looked at the same region at times, Hollywood trailers most coherent [V Dorr 2010]; synchrony tracks motion, contrast, flicker [S Mital 2011]. **Not found**: anime, ultra-wide or multi-monitor gaze data (NPF-200, 100 cartoon videos, reports no centre-bias number [V]). Proxy [project, one gag episode]: person box touches a side edge in 52% of frames, head 27.5%, face 6%.
- Assumption used below [E]: scenarios C = gaze at picture centre; Eg10 = gaze 10° inside the edge; Eg = gaze on the edge.

### 3.3 Q3 Periphery-extension studies
| Study | Read | Fidelity of periphery | Result / what bothered |
|---|---|---|---|
| IllumiRoom, Jones, Benko, Ofek, Wilson, CHI 2013 | full text [V] | projector 1280x800 over 45° vs TV 720p over 20° = 28 vs 64 px/deg (0.44x); panoramic video may be blurred to de-emphasise | 10 gamers + 15 designers, "very positively"; SSQ mean 14.96 (normal); F+C Full "too much happening at once" in fast game; F+C Edges (high-contrast edges) lowest satisfaction; Radial Wobble off-putting; flow patterns raise sense of motion |
| Infinity-by-Nine, Novy (MIT thesis 2013-06-01; demo 2012) | full text [V] | sides "less than half SD", colour/light/movement patterns, affine camera motion (FAST + Lucas-Kanade) applied to side panels | 50" TV, groups of 2-6, n unreported: >80% more immersion, not distracting; complaints: tracking failures -> "distracting when the main screen was moving and the side screens weren't", cuts ("the thing to avoid"), side screens brighter/uncalibrated, "wish it was dimmer"; 2012 press: "crude blobs and blurry shapes" suffice [S] |
| Aides, Avraham, Schechner, ICCP 2011 | abstract [Vd] | coarse-to-fine foveated extrapolation: finer detail near the input, fewer effective pixels far | less artifact-prone; no study in abstract; full text unreachable |
| Turban, Urban, Guillotel, IEEE TVCG 2016/17 | abstract [Vd] | extends legacy video into extrafoveal vision "without changing creative intent"; new QoE protocol | user tests said to confirm more immersion; full text paywalled |
| ExtVision, Kimura & Rekimoto, CHI 2018 | abstract [Vd] | DNN-generated context images around TV | user study exists; results unreachable |
| Kimura, Kono, Rekimoto 2019 (DNN extension, HMD) | snippet [S] | DNN | extension improved experience, more comfortable (less sickness) in first-person video |
| Ambilight vs classic TV (Philips-related, date n/d) | snippet [S] | LED colour wash | higher self-reported immersion and physiological response |
| SparseLightVR, Xiao & Benko, CHI 2016 | abstract [Vd] | very low-res LED array to 190° | better situational awareness, preferred, may reduce motion sickness |

Read-across: every liked system gave the periphery <= ~half the centre's pixel density; complaints were temporal mismatch, cuts, brightness/calibration, clutter, not softness. 2019-2026 evidence on *generated* periphery is thin (DNN extensions, DeepFovea, Flöter); none on AI-outpainted anime sides.

### 3.4 Q4 Temporal requirements
| Claim | Evidence | Tag |
|---|---|---|
| Periphery is not temporally slow; large fields flicker-sensitive | 3.1 temporal bullet | V |
| Frozen/lagging periphery next to moving centre is noticed and distracting | Novy quotes; your verdict | V |
| Periphery moving with the picture's camera motion increases sense of motion | IllumiRoom Grid/Starfield; Novy | V |
| Periphery drives vection and spatial presence: field loss of the periphery (glaucoma) lowers vection and presence, central loss raises it | Luu et al. IOVS 2021; Brandt 1973 "peripheral dominance" | V |
| A frozen periphery reads as "a moving object in a window", not a scene | no direct desk-scale study; matches Novy + verdict | E |
| Pops at tier changes/cuts are the most visible event | Guenter pop; Novy "avoid cuts" | V |
| Outer layers at half rate are fine *with reprojection* (baseline 60-120 Hz) | Guenter; Flöter (half noticed by ~half of users, low distraction) | V / Vd |
| Coarser steps still read as motion in periphery (apparent-motion limit Dmax rises with eccentricity) | Baker & Braddick 1985 | S |
| Motion/flicker attract gaze | Mital 2011 | S |

**Answers.** (a) *12 Hz or drawing rate while the centre shows 24 fps?* Nothing found says no and nothing measures it on hand-drawn content. Anime itself is on twos (12 drawings/s), comedy ~6.3 new drawings/s, action up to ~15/s [project]. Update **on the picture's own drawing changes** removes the phase problem (an independent 12 Hz grid lands 42 ms off a 3s drawing half the time [E]). (b) *Does warping between updates suffice?* For camera-driven change yes (Novy's affine warp worked until tracking failed) [V]; for character/effect motion reaching the sides, warping a still cannot move parts that were never drawn [E]. E2 measures where this breaks, per clip type. (c) *Flicker/boil* tolerance does not relax with distance (large fields) [V]; photosensitivity: never flash the sides harder than the picture (WCAG limits not verified) [R].

## 4 Perceptual budget + geometry

### 4.1 Geometry (flat coplanar screens, eye on the canvas axis at mid-height, 1.5 cm gap, picture = 16:9 at screen height centred on the canvas) [E]
| Layout | D cm | picture edge | canvas outer edge mid-height (corner) | side width |
|---|---|---|---|---|
| A: 2x27" (59.8x33.6 cm), picture spans the seam | 60 / 70 / 80 | 26.5 / 23.1 / 20.5° | 45.2 / 40.8 / 37.1° (46.3 / 41.9 / 38.1) | 18.8 / 17.7 / 16.6° |
| A: 2x24" (53.1x29.9 cm) | 60 / 70 / 80 | 23.9 / 20.8 / 18.4° | 41.9 / 37.6 / 34.0° | 18.0 / 16.8 / 15.6° |
| B: 3x27" | 60 / 70 / 80 | 26.5 / 23.1 / 20.5° | 56.6 / 52.5 / 48.7° (57.1 / 52.9 / 49.2) | 30.2 / 29.4 / 28.2° |
| B: 3x24" | 60 / 70 / 80 | 23.9 / 20.8 / 18.4° | 53.5 / 49.2 / 45.4° | 29.7 / 28.5 / 27.1° |
| B: 3x27", outer screens angled in 25° | 60 / 70 / 80 | same | 67.7 / 62.2 / 57.2° | - |
| A2: 16" laptop (34.5x21.5) + 27", picture limited by laptop height = 38 cm wide | 60 / 70 / 80 | +-17.7 / 15.3 / 13.5° | laptop side to 30.6 / 26.9 / 23.9°; 27" side to 45.4 / 41.0 / 37.2° | 12.9 / 11.6 / 10.4° vs 27.7 / 25.7 / 23.7° |

- Native px/deg at 70 cm on-axis: 27" 1080p 39, 1440p 52, 4K 79; 24" 1080p 44; 16" 2560x1600 91. A flat panel's pixels shrink off-axis (x1/cos²: 1.2 at 25°, 1.7 at 40°, 2.4 at 50°), so the sides are over-supplied even more. Bezel 1.5-3 cm = 1.2-2.5° at 70 cm. A2 is asymmetric: the real shape of the sides depends on your actual layout; re-run `geometry.py` with it.
- Layout facts: the sides never come closer than ~20° to a picture-centre fixation; the *whole* side is >= 23° eccentric, outer edge 37-45° (A), 49-57° (B flat).

### 4.2 Bands (27", D = 70 cm, flat; E from picture-centre gaze; share of side area A / B)
| Band | d beyond picture edge | E (centre gaze) | share A / B | linear scale needed vs native 1440p: gaze centre / 10° inside / on edge (Guenter B -> grating f0=48) | provisional | refresh (provisional [E]) | visible errors |
|---|---|---|---|---|---|---|---|
| N near | 0-5° | 23-28° | 24% / 12% | 0.08->0.21 / 0.16->0.40 / 0.61->1.00 | **1/2** | picture clock; >= 6 Hz new content (E2) | everything: seam step, line/object continuity, colour level, pops, frozen, flicker |
| M mid | 5-15° | 28-38° | 57% / 29% | 0.04-0.06->0.12-0.17 / 0.07-0.10->0.18-0.27 / 0.11-0.23->0.28-0.54 | **1/4** | 3-6 Hz + warp (E2) | flicker, frozen vs action, colour level, bright "glow"; not layout, not softness |
| F far | > 15° (A: only 15-18°; B: 15-29°) | > 38° | 18% / 59% | 0.03->0.07 / 0.03->0.09 / 0.05->0.13 | **1/8** | 1-3 Hz + warp or plate | flicker, frozen vs action, brightness level; blur and layout invisible |

Tolerable blur = finest detail still needed (cpd, centre gaze, Guenter -> acuity): N 1.7-4.6, M 1.3-3.6, F 1.0-2.8 (each x2 if gaze 10° inside). Chroma can be coarser than luma [V Patney].

Visible-error ranking per band (H high / M medium / L low):
| Error | N | M | F | Basis |
|---|---|---|---|---|
| Flicker / boil (change not in the picture) | H | H | H | Strasburger, Patney, Guenter [V] |
| Frozen or lagging vs moving picture | H | H | M-H | Novy, verdict [V] |
| Pop at cuts / mid-shot swap | H | H | H | Guenter, Novy [V] |
| Brightness / colour step and level | H | M | M | Novy [V]; Weber ~1-2% for touching fields [R] |
| Slow colour/brightness drift in a shot | M | M | L-M | unknown, E4 |
| Seam structure (lines/objects not continuing) | H if gaze near | L-M | L | pooling [V], gaze [E] |
| Invented layout / identity | M | L | L | Freeman & Simoncelli [V] |
| Blur / softness | L-M (H if gaze on edge) | L | none | 4.1-4.3 [E] |

### 4.3 Pixel share of native side pixels (area-averaged; Guenter-B slope -> grating f0=48; both x1.5 margin) [E]
| Layout | gaze centre | gaze 10° inside edge | gaze on edge |
|---|---|---|---|
| A 2x27" 1440p | 0.4 -> 2.8% | 1.1 -> 7.4% | 5.5 -> 23.5% |
| A 2x27" 1080p | 0.7 -> 5.0% | 2.0 -> 13.2% | 9.2 -> 36.9% |
| B 3x27" 1440p | 0.2 -> 1.8% | 0.7 -> 4.4% | 3.0 -> 13.2% |
| B 3x24" 1080p | 0.5 -> 3.3% | 1.1 -> 7.2% | 4.9 -> 20.5% |
| B 3x27" 4K | 0.1 -> 0.8% | 0.3 -> 2.0% | 1.4 -> 6.4% |

Provisional bands 1/2, 1/4, 1/8: **A 10.0%, B 5.75%** of native = A 0.37 Mpx (1440p) / 0.21 (1080p); B 0.42 / 0.24 Mpx. Kongming's inner-quarter-1/2, outer-1/4 = 10.9%. Covers "gaze 10° inside" everywhere; covers "on the edge" only for the Guenter model. Versus today's half-res-everywhere (25%): 2.5-4.4x fewer pixels, plus update-rate savings (10% pixels x 3-6 Hz / 24 = 1-2.5% of native-24fps work). Caveat [E]: DiT cost is not linear in pixels and has a floor at the model's trained resolution (~0.4-0.5 Mpx), so most of this saving is already taken; the gain left is in time and architecture.

## 5 By-eye experiments on your monitors with TRUE sides (no generator)

**Harness (all four).**
- *True sides*: take a 4K source frame, crop a 32:9 strip (A) or 48:9 (B) from the middle rows; picture = its centre 16:9, untouched; sides = the strip's outer parts, real pixels. 1080p source: strip is 1920x540 shown at 2x, so sides are already half the screen's pixels; in E1 a 1/2 level is then no loss, start at 1/4. B needs a 4K source or the picture is soft by construction.
- *Play the full canvas on the real monitors* (panorama player), seated at normal distance, not a browser page. The existing `make-blind-test.py` shows two clips at once = **scrutiny mode** (gaze on the sides); use it as the strict bar, these tests as the in-use bar.
- *Trial* (~9 s): "1" big in the picture centre 0.5 s, clip A 3 s, "2" 0.5 s, clip B 3 s, prompt "Which has the [softer / choppier / odd] sides? press 1 or 2". One version is the truth; order random; no feedback. **Fixate** a small cross at the picture centre during clips (real-use mode). Show first an exaggerated example (1/16) labelled "this is what degraded looks like".
- *Clips*: 8 x 3 s: 3 action (>= 10 new drawings/s), 2 held dialogue, 1 pan/tilt, 1 dark, 1 bright flash; a cut inside two. Log results per clip type.
- *Controls per experiment*: 6 positive (obviously degraded: must be >= 5/6 correct), 6 sham (identical clips: report how often "2" is chosen; skew > 5/6 = redo).
- *Per level*: 20 trials. **>= 15/20 = visible** (p = 0.021 by guessing); **<= 12/20 = not reliably visible** (a true 75%-detector slips through only 10% of the time); 13-14 = 20 more, pooled >= 28/40 visible, <= 24/40 not. Run levels harsh -> mild, stop at the first pass.
- *Finish*: repeat the winning combination in free-look (scrutiny) mode, 10 trials, to see your margin.
- Time: ~3 min per level + 2 min controls per experiment.

**E2 - Update schedule (motion). "Which has the choppier or less-connected sides?"** *Decides the number of generations per second and whether a free camera warp is enough.*
- Sides = truth refreshed only every N film frames; between refreshes either hold or warp (shift/zoom by the measured camera offset of the content model). Levels, harsh -> mild: frozen at shot start (positive control, the failed still plate; expected visible on action) · 1 Hz + warp · 3 Hz + warp · 6 Hz + warp · 6 Hz hold · 12 Hz hold.
- Pass: <= 12/20 per level and per clip type. Warp passes where hold fails -> build the warp; dialogue passes frozen -> static plates for held shots.
- Expect [E]: dialogue passes down to ~1 Hz; action fails below ~6 Hz because characters/effects reach the sides.

**E4 - Colour level and boil. "Which has the odd sides?"** *Decides the colour-fit and anti-boil tolerance (judge thresholds); `tone/live/hold` wraps exist.*
- Sides = truth plus: (a) brightness -10% as a hard step at the seam; (b) tint toward green, ΔE ~4 (ΔE 1 = just visible side by side, ~4 = clearly tinted [R]; the klein case); (c) boil: smooth random warp of 2 px redrawn at 12 Hz; (d) boil 4 px. Pass test per level as above; bracket: if a level passes double it, if it fails halve it.
- Run after matching the panels with your brightness tool. Largest passing value = tolerance. Expect [E]: boil fails early, (a) fails at the seam.

**E1 - Resolution ladder (detail). "Which has the softer sides?"** *Decides the pixel budget per band (x4 per halving).*
- Sides = truth shrunk by f (area filter) and enlarged back (Lanczos), picture untouched. Levels harsh -> mild: 1/8 · 1/4 · 1/2 uniform; **G** graded (1/2 within 5° of the picture, 1/4 to 15°, 1/8 beyond, 2° blends); **C** = G with local contrast restored (Patney). Positive control 1/16.
- Pass: <= 12/20. Expect [E]: 1/4 and G pass fixated; 1/8 fails near the seam only. If 1/2 fails, your gaze visits the sides: raise the N band.

**E3 - Where it matters. "Which has the degraded sides?"** *Decides whether to split into a live near band plus a cheap far plate.*
- Half of each side degraded, the other half truth (boundary 12° beyond the picture edge on A, 15° on B, 2° blend): (a) far half 1/8; (b) near half 1/4; (c) far half 3 Hz + warp; (d) near half 3 Hz + warp. Pass: <= 12/20.
- (a),(c) pass and (b),(d) fail -> live near band + cheap far plate. All pass -> one cheap uniform fill. (a) fails -> far band needs more than 1/8.

Order by decision value: **E2 > E4 > E1 > E3**. Short on time: E2 (frozen, 3 Hz+warp, 6 Hz+warp) and E4 (boil 2 px, brightness -10%) first. If a result varies by clip type, gate by the content model's action/dialogue class.

## 6 Sources (accessed 2026-10-13)
- Strasburger, Rentschler, Jüttner, "Peripheral vision and pattern recognition: a review", J Vis 11(5):13, 2011-12-28 [V full text, Aston copy]: https://publications.aston.ac.uk/id/eprint/23648/1/jov_11_5_13.pdf (2024 erratum on visual-field extent: PMC11033599, not relevant here)
- Freeman & Simoncelli, "Metamers of the ventral stream", Nat Neurosci, Sept 2011 [V full text]: PMC3164938
- Guenter, Finch, Drucker, Tan, Snyder, "Foveated 3D graphics", SIGGRAPH Asia, Nov 2012 [V]: https://www.microsoft.com/en-us/research/publication/foveated-3d-graphics/
- Patney et al., "Towards foveated rendering for gaze-tracked VR", ACM TOG 35(6):179, Nov 2016 [V]: https://anjulpatney.com/docs/papers/2016_Patney_TFR.pdf
- Tursun et al., "Luminance-contrast-aware foveated rendering", ACM TOG 38(4), 2019 [Vd]: doi 10.1145/3306346.3322985 · Kaplanyan et al., DeepFovea, SIGGRAPH Asia 2019 [S] · Mantiuk et al., FovVideoVDP, ACM TOG 40(4):49, 2021 (record only) [S]
- Flöter, Geringer, Reina, Weiskopf, Ropinski, "Evaluating foveated frame rate reduction in VR for HMDs", arXiv 2505.03682, 2025-05 [Vd digest]
- Jones, Benko, Ofek, Wilson, "IllumiRoom", CHI 2013 [V full text]: https://www.microsoft.com/en-us/research/publication/illumiroom-peripheral-projected-illusions-for-interactive-experiences/
- Novy, "Computational Immersive Displays", MIT S.M. thesis, 2013-06-01 [V full text]: https://dam-prod.media.mit.edu/x/files/thesis/2013/novy-ms.pdf
- Aides, Avraham, Schechner, ICCP 2011 [Vd abstract] · Turban, Urban, Guillotel, IEEE TVCG (doi 10.1109/TVCG.2016.2527649) [Vd abstract] · Kimura & Rekimoto, ExtVision, CHI 2018 [Vd abstract] · Kimura, Kono, Rekimoto 2019 [S] · Xiao & Benko, CHI 2016 [Vd abstract] · Ambilight study (IEEE record via search, date n/d) [S]
- Goldstein, Woods, Peli, Comput Biol Med, 2007 [V abstract via Europe PMC] · Dorr et al., J Vis 10(10):28, 2010 [V abstract] · Mital, Smith, Hill, Henderson, Cogn Comput, 2011 [S] · NPF-200 dataset, arXiv 2308.12163, 2023-08-23 [V: no centre-bias statistic]
- Luu et al., "Vision impairment provides new insight into self-motion perception", IOVS 62(2), 2021-03 [V full text]: PMC7862735 · Baker & Braddick, Vision Res 25:803, 1985 [S]
- Project: `plans/reports/research-261012-1521-ai-surround-anime-outpainting-synthesis.md`, `kongming-261013-1049-real-time-architecture-counsel.md`, `spikes/ai-surround/{make-blind-test,apply-wraps,hidden_sides}.py`

## 7 Unresolved questions and limits
1. No gaze data for anime, ultra-wide or multi-monitor viewing; "gaze 10° inside the edge" is my assumption. No eye tracker: E1-E3 indirectly bound it.
2. Guenter's slopes were validated only to 23° and on 3D scenes with a tracker (15 viewers); Patney n=4; Flöter is VR with head motion and no reprojection. Extrapolation to 40-57° and to flat anime is [E].
3. No study of low-update-rate periphery for 24 fps hand-drawn content, or of AI-outpainted sides. Update rate is the weakest axis; E2 is the first real measurement.
4. Aides 2011, Turban 2017, ExtVision, Kimura 2019, Ambilight primary study: full texts unreachable (paywall/403). Their user-test details are not used.
5. Colour: Weber ~1-2% and ΔE scale are [R]; no quantitative colour-mismatch threshold fetched. Photosensitivity limits not verified.
6. Bezels: effect on seam visibility unmeasured; A vs B differ (seam exposed vs behind bezel).
7. FovVideoVDP could rate candidates automatically (needs install; licence CC BY-NC per earlier notes); not run.
8. Perceptual floor is not the model's floor: a DiT cannot go below its trained resolution; needs the architecture owners.
9. Single viewer, no counterbalancing across sessions; repeat E2 once on another day if it decides the architecture.
10. Geometry assumes flat coplanar screens and a viewer on the canvas axis; give the real screen sizes, tilt and distance for exact bands.
