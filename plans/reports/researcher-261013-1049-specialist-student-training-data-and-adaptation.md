> Saved by the parent session from the agent's returned text (the agent's harness forbade writing report files), 2026-10-13.

# Specialist anime-sides student: training, data, adaptation (2026-10-13)

Tags: [V] verified this pass (arXiv PDF/HTML, HF or GitHub API/README read 2026-10-13) · [S] snippet/secondhand · [R] recalled · [E] my arithmetic/inference · [L] measured in this project (reports 2026-10-12/13).
Budget: 4 WebSearch/WebFetch + ~95 API/PDF/script pulls (over the 40-60 guide if scripts count). No GPU job, no model download, no repo file touched. Report returned inline (harness rule: no report files); intended path `plans/reports/researcher-261013-1049-specialist-student-training-data-and-adaptation.md` NOT written.
Terms (once): **LoRA** = small add-on that retrains a big model (rank 32 on a 7B ~ 100-200 MB [R]). **Teacher/student** = big slow model / small fast copy. **Step distillation** = student needs 2-4 denoising steps, not 25-50. **DMD** = student trained so its outputs match the teacher's *distribution*; a second "fake" network tracks the student. **Consistency (rCM)** = student learns to jump from any noise level straight to the answer. **Adversarial** = a critic separates real from generated; the student learns to fool it. **Causal/AR** = video made chunk by chunk from past chunks, so it can stream. **Plate/key** = painter's still for a shot. **Memory frame** = another frame of the same shot whose sides are known. **Hidden-sides pair** = real frame with outer parts hidden + the hidden truth. **DPO** = tune from "A beat B" pairs; **reward head** = small scorer trained on such pairs. **MOTIA/TTT** = briefly train on the very video being processed.

## 1 Decision + executive summary

**Ranked decision**
1. **Do now, laptop first: train the truth.** (a) per-show painter LoRA on hidden-sides pairs cut from the user's own episodes, targets = random windows of frames, not whole pictures; (b) plate-keyed animator LoRA at periphery resolution (416x240-class), memory = true sides of another frame of the shot (50%) + painter plates (50%). Only real anime truth teaches ratio, composition, style; Qwen and Wan are zero-shot on anime [E].
2. **Then, only if the eye accepts (1) and speed is still the gap, rent and compress (public clips only):** animator -> 1.3B, causal, 2-4 step student (DMD/rCM family, 14B as score network); painter -> 8-step by on-policy distillation (OPD, DiffSynth). Distillation copies a ceiling and costs $0.3-1.5k per run [E].
3. **Parallel, cheap: preference loop** = free truth-vs-sample pairs (RealAlign style) + the user's forced choices as acceptance gate and reward head.
4. **Skip:** TTT layers (12.8k H100-h), per-shot MOTIA (7B/14B), from-scratch small model, reference-conditioned VAE decoder (960 H200-h) unless line weight stays the top failure.

**Evidence in one list**
1. Tiny data teaches the *task*: Qwen-Image-2.1 outpaint LoRA = 68 pictures, rank 32, 2,000 steps, "works from step 500" [V card]; Wan2.1-14B in/outpaint LoRA = 25 clips at 416x240x81, 2,000 steps, ~1 h on one H100 [V 2511.03272]; M3DDM+ repaired a mask mismatch with 10k videos [V]. One episode = 357-679 shots [L]. Small sets do NOT teach far-side world knowledge or identity: supply via keys/references.
2. Mask schedule matters more than volume: same mask on every frame (M3DDM+), no rescale between known and target (ratio), windows as targets (PQDiff) [V]. "Window targets teach continue-not-close" is my hypothesis, untested [E].
3. Memory proxy exists in neighbours, not for outpainting: VideoCanvas (20 random anchor frames, patches 20-100% of frame), StreamingEffect (reference image sampled from the target video) [V]. Novel for anime sides.
4. Compression evidence: VDOT-14B = 4-step VACE released (Apache) [V]; VDOT++ 1.3B generator with 14B score networks 80.51 vs same-scale 79.98 vs 14B teacher 77.70 (T2V VBench) [V]; StreamingEffect teacher->4-step causal student 0.14 -> 14.1 FPS but PSNR 19.4 -> 16.7 [V]. No paper compares a small student with a 14B teacher on outpainting or anime.
5. Per-show beats per-shot: MOTIA r16 LoRA, 1,000 steps = 309 s, 12.7 GB for 16 frames of 512^2 (+0.94 dB PSNR, FVD 312 -> 286 vs direct tune) [V]; at 357 shots/episode on 7B+ models this is impossible, so adapt per show.
6. User's eye: population DPO needs 10k-851k pairs [V]; RealAlign: 512 real-vs-degraded pairs ~ 851k human pairs on its metrics [V PDF]. Hidden-sides gives unlimited free truth pairs. n=42 forced choices detect a 70/30 gap; "equal within 10 points" needs ~164 [E].
7. Laptop = LoRA only: 7B painter ~40 s/step, 1.3B animator ~10 s/step at 416x240x81, 14B ~1.5-2 days per 2k steps [E]. Distillation needs >=4 x 80 GB. H100 $2.20 spot / $2.65 on-demand, live 13 Oct 2026 [V]; H200 ~$4.4-4.6, 5090 ~$0.5-0.7 [S].
8. Film stays local at every rung if rented jobs use public clips + synthetic masks; per-show adaptation is laptop-only. Cost: R1-R3 ~$0 local or ~$3-35 per rented LoRA run; same-size 1.3B DMD ~$280-340 per run; cross-size 14B->1.3B ~$0.5-1.4k per run [E]; all-in with retries $1.5-5k [E].
9. Restructure levers (cheap to test): train on hold-compressed *drawing sequences* (74% of frame steps repeat in the comedy episode [L] -> ~3.8x fewer tokens, matches twos/threes); dual-region loss; key dropout so one model runs with or without a plate; low-noise-expert-first LoRA for A14B (project evidence: AnimeGen in the low-noise expert only gave real content, both experts gave blank sides [L]).

## 2 Invariant + lever map
**Invariant:** sides continue the real picture (style, ratio, colour, composition, identity, motion, across cuts) at a cost the player can afford; film stays local; the user's eye decides; the real picture is never touched.

| Lever | Options | Verdict |
|---|---|---|
| T1 target signal | real hidden truth / teacher outputs / user pairs / verifiable reward (distance to truth) | truth for *knowing*, teacher for *speed*, user eye for *feel* |
| T2 data | user's episodes (local) / public clips / stills / synthetic | local for per-show; public for rented jobs |
| T3 mask + window | kept fraction f 0.25-0.75, symmetric/asymmetric/off-centre, one mask for all frames, window targets | main quality lever per dollar |
| T4 memory | none / true sides of other frame / painter plate / retrieved reference crop | train on mix of true + plate |
| T5 what trains | LoRA / full / adapter / decoder | LoRA first; low-noise expert first |
| T6 compression | step / size / causal / drawing-rate | after quality is accepted |
| T7 adaptation | per-show LoRA / per-shot TTT / scene plate bank | per-show LoRA + bank |
| T8 user signal | gate / reward head / DPO / online RL | gate + head first |
| T9 where | laptop / rented | trigger-based (sec 4) |

## 3 Findings per question

### Q1 Data, mask schedules, the edge gap
| Public set | Size | Licence / access | Note |
|---|---|---|---|
| Sakuga-42M (2024-05) [V README] | full 1.12M clips / 142k videos / 38.1M keyframes / ~441 GB; "aesthetic" 140k clips ~56 GB; small 112k ~45 GB | CC BY-NC-SA 4.0, "academic only", creators own copyright; request form -> parquet -> download.py (>=15 h); arXiv v1 withdrawn (submitter lacked rights) | action-heavy highlights; resolution not stated |
| Sakugabooru2025 HF (2025-03) [V] | 155,238 videos + 8,680 images, ~1.27 TB | tag MIT = uploader's label, DMCA-reduced, treat as unlicensed | open download |
| AnimeShooter (2025-06) [V] | 1K-10K multi-shot items, 6.5 GB, character refs | CC BY-NC 4.0 | identity/reference experiments |
| Anim400K (2024) [V partial] | 100K-1M clips, 239 GB | gated, no licence tag | dubbing-oriented |
| MagicAnime (2025-07) [V abstract] | 400k clips claimed | repo/dataset not found | skip |
| Stills: anime-with-caption-cc0 (15k, CC0), Anime-Background-Finetuning V1.1 (10,143, Apache tag, 2026-09-28) [V] | | tags unreliable for scraped art | painter regularisation |
| User's library [L] | 357 shots (comedy ep), 679 (JJK ep) | local | best in-domain source |
Newer large anime video set after 2025-07: none found (HF datasets createdAt>=2026 + 3 HF-papers queries) [V, limited].

| Recipe | Schedule | Volume / cost |
|---|---|---|
| M3DDM / M3DDM+ [V] | 1-4 borders, ratio U[0.15,0.75]; + same direction/width on all frames, P(4/2/1 dir)=.3/.55/.15 | fine-tune on 10,000 WebVid videos |
| 2511.03272 [V] | border mask keeps central U[0.5,0.8] per dim OR 1-4 interior rects; dual-region loss (best weight 0.9) | Wan2.1-14B LoRA r16, 25 clips, 2k steps, ~1 h on 1 H100 |
| ausboss Qwen-2.1 LoRA [V] | v1: all sides/one/opposite/corner/three sides/small window (12-30% kept); v2: kept 45-93% (median 74%) | 924 pairs (231 pictures) or 68 pictures; r32, LR 1e-4, 1.5-2k steps |
| HL-OutPaint [V prior report] | random position/scale/shape from 4 sides | ~17k clips 768^2x49, GPU-h not stated |
| VideoCanvas [V PDF] | 20 random anchor frames, patches 20-100% of frame | 20k steps, batch 32, 32 GPUs |
| PQDiff, ICLR 2024 [V abstract] | source and target both random crops of one image + relative position | no pretrained backbone |
Our canvases (f = 0.25-0.67, [E] from earlier report): sample f in [0.25,0.75], 70% symmetric / 30% one side bigger, +-0.15 off-centre, sometimes single side; same mask on all frames; known region kept in a small-weight loss term.

**The "redraw a crop" vs "extend beyond the real edge" gap:** no paper measures it [V absence: 9 video-outpainting titles + 4 HF-paper queries]. Evidence is indirect: base models re-frame ("kitchen shrank inside its frame") [V ausboss card]; PQDiff makes the *target* a crop too. My plan [E]: (1) window targets so output edges are arbitrary; (2) test on **pan-truth**: shots where the camera slides and later frames really reveal the outside (project `scroll-plate.py` mosaic), plus 2.39:1 films cropped; (3) no resize between known and target; (4) strip black bars first.

### Q2 Teacher -> student
| Method | Evidence for conditional video | Cost / code |
|---|---|---|
| VDOT / VDOT++ (2025-12 / 2026-10-02) [V] | VACE-Wan2.1-14B -> 4-step incl. masked video editing; DMD + optimal transport + GAN after 900 warm-up; T2V 1.3B+14B scores 80.51 > 79.98 same-scale > 77.70 teacher | 2,100 steps, 4xH200, 250k Artgrid 832x480 videos with auto conditions; VDOT-14B Apache-2.0 34.7 GB on HF; ++ code/weights promised mid-Oct |
| rCM / Causal-rCM [V] | matches DMD2/CausVid on VBench (77.42 vs teacher 77.70, DMD2 78.18); unified teacher-forcing + self-forcing streaming recipe | NVlabs/rcm Apache-2.0, 819 stars |
| Self-Forcing, Causal Forcing(++) [V README] | 1.3B causal, data-free (prompts only); CF++ frame-wise 2-step | 600 its < 2 h on 64 H100 (~128 H100-h) |
| StreamingEffect (2026-05) [V PDF] | in-context video editing, keyframe control, teacher 50 step -> causal 4-step | teacher LoRA 8k steps on 8 H200; student 3k+3k its; Apache code |
| EditStream (2026-08) [V PDF] | 5B unified AR incl. editing propagation + reference-guided v2v; 10.56 FPS H100 | code "soon" |
| LongLive-Plug (2026-09-29) [V] | reusable few-step + CFG LoRAs; verified on 54 models incl. Index-AniSora V2.0; VACE and Wan2.2-A14B NOT in set | base ~80 H100-h once; task-specific distill alone 56-150 H100-h |
| APT / AAPT [V abstract] | 1-step 8B, 24 fps 736x416 on H100, trained against real data | code not checked |
| OPD (DiffSynth docs) [V] | Qwen-Image-2.1 and klein-base-4B -> 8 steps, LoRA; 50->4 direct fails, go 50->8->4 | "usually within 2 GPU-h" |
| FastLightGen (CVPR 2026) [V abstract] | 4 steps + 30% pruning best | n/s |
- **Ranking for our narrow task:** (1) DMD family with GAN on real data and bigger score nets (VDOT++), because VACE-style masked tasks are the only conditional case distilled and released; (2) Causal-rCM / Causal Forcing++ for the streaming step; (3) LongLive-Plug as free first try; (4) OPD for the painter.
- **Cross-size vs same-size:** cross-size proven only for T2V; for conditional/outpaint unproven. Same-size keeps the 14B (still ~2.5 min per 3 s clip even at 4 steps [E]), so real time needs ~1.3B.
- **Real vs teacher targets:** truth trains knowledge (free, exact, anime-native); teacher/score nets compress the sampler; adversarial critic on real wide crops adds realism (DMD2, VDOT++). Student may *beat* the zero-shot teacher on anime because it sees truth [E].
- **Risks:** DMD students drift to near-static output (DynaForcing 2026-08 [V abstract, sibling]); compression costs fidelity (StreamingEffect PSNR -2.7 dB); VAE thins lines (community [S]).

### Q3 Carrying a key through a shot
| Paper | Idea | Training / status |
|---|---|---|
| VideoCanvas 2510.08555 [V] | in-context patches at any place/time, RoPE interpolation; includes outpaint | self-supervised: random anchors from same clip |
| StreamingEffect / EditStream [V] | reference keyframe injected online, propagated | reference sampled from target video; streaming 4-step |
| FFF-VDI 2408.11402; VideoRepainter 2412.08975 [V abstract] | fill first frame (image model) then propagate (I2V fine-tune / flow) | the "plate + animator" shape; no anime |
| AnyV2V, I2VEdit, GenProp [V abstract] | first-frame edit propagated by I2V; GenProp trained on synthetic composites | tuning-free or small; no outpaint |
| VideoPainter 2503.05639 [V] | plug-in context encoder = 6% of backbone params | cheap adapter route |
| Memorize When Needed, MosaicMem (2026) [V abstract] | decoupled lightweight memory branch / patch memory | world models, not outpaint |
| Seen-to-Scene (prior report) | real pixels by flow, MIT code, no weights | not learned memory |
- **Direct answer:** no paper trains "true sides of another frame" as memory for outpainting. Closest = VideoCanvas + StreamingEffect. Proxy is sound but has a gap: at test time the key is a painter plate (wrong scale ~30%, closes composition [L]). Fix: 50% true-side keys, 50% painter plates (made locally, 170 s each; Turbo 8-step is cheaper), plus scale/colour/misregistration jitter on the key, key age up to the shot length, 20% null key.
- Implementation needs no new architecture: VACE already takes video+mask+references; key frame = mask 0 everywhere, other frames mask 1 on sides [E].
- Identity: reference *crops* of the same character from neighbouring shots (CCIP, earlier report); whole-frame references copied the wrong room [L].

### Q4 Test-time / per-show adaptation
| Method | Cost (measured) | Gain (measured) | Verdict |
|---|---|---|---|
| MOTIA (2403.13745) [V] | r16 LoRA (7.49M params), 1,000 steps = 309 s, 12.7 GB, +58 s sampling; 16 frames 512^2 | PSNR 19.42 -> 20.36, LPIPS .178 -> .160, FVD 312 -> 286 vs direct tune; user study 72.6% vs M3DDM | per-shot too slow at 7B+; per-show instead |
| Per-show LoRA | painter ~4-25 h, animator 1.3B ~6 h on laptop [E] | task proven by ausboss/2511.03272; show-specific gain unmeasured | rung 1/3 |
| TTT layers (2504.05298) [V] | 50 h on 256 H100 (12.8k H100-h), ~7 h Tom and Jerry, 5B | +34 Elo coherence | skip |
| Texler 2020 (2004.14489) [V] | 16 s from one keyframe; 640x640 at 17 fps | appearance matching only | per-shot harmoniser (seam, line weight), not invention |
| Internal learning (Zhang 2019) [V abstract]; lifelong stream (2406.04814) [V abstract] | per-video net; needs experience replay | | per-show LoRA files avoid forgetting |
- "Keeps learning the current show": nightly LoRA update on the episode just watched (replay of earlier episodes), one LoRA per show, selected by file identity; plus scene plate bank (reverse angles recur [L]).

### Q5 Learning from the user's eye
| Evidence | n |
|---|---|
| Diffusion-DPO [V] | 851K Pick-a-Pic pairs |
| DenseDPO [V]; VideoDPO [V abstract] | ~10k human pairs from 30k prompts (vanilla); 1/3 labels with segment-level pairs from aligned copies of one video |
| PrefPaint (inpainting) [V]; AnimeReward [V] | 51k images; 30k samples |
| PPD [V abstract] | 4 prefs per new user, 76% win rate, needs population pre-training |
| RealAlign, ICML 2026 [V PDF] | 512 constructed pairs (real = winner, inpainted degraded copy = loser) ~ Diffusion-DPO 851k; 256 -> 512 helps, more diminishing |
| VideoGPA; SPIN-Diffusion [V abstract] | self-generated preference / real data as winner, no human labels |
| Flow-GRPO, DanceGRPO, DiffusionNFT [V abstract] | online RL with rule-based rewards (GenEval 63 -> 95%); NFT up to 25x cheaper than Flow-GRPO |
- No paper trains from one labeller's <=300 pairs on inpainting/outpainting [V absence, limited]. Hidden-sides makes truth-vs-sample pairs free; reward = distance to truth + seam score (verifiable), so RL/DPO needs no human at scale. User pairs calibrate a reward head and gate.
- Project judge results [L]: `seam_self` Spearman 0.61 with true seam; Qwen3-VL-8B rating judge did not discriminate. DPO on the 7B painter is ~3x LoRA cost per pair [E]; do it on the fast student later.
- Forced-choice sizes (exact binomial, one-sided .05, power .8) [E]: detect 80/20 n=18; 70/30 n=42 (>=27 wins); 65/35 n=76; 60/40 n=169. Student not worse than teacher (wins >=40%): n=164 if truly equal. A fixed gate "27/40" passes a true 70/30 student 70% of the time, 60/40 21%.

### Q6 Compute ladder (12 GB laptop, bf16 52.8 / FP8 113.2 TFLOPS [L]; model: 2*params*tokens + 4*L^2*d*layers, x4 for LoRA, 17 TFLOPS effective; reproduces the paper's 14B-LoRA ~1 h on an H100 [E checked vs V])
| Job | Tokens | Laptop | Rented |
|---|---|---|---|
| VACE-1.3B LoRA, 416x240x81 | 8.2k | ~10 s/step, 2k steps ~6 h [E] | ~1 s/step on H100 |
| VACE-1.3B LoRA, 480x832x49 | 20k | ~40 s/step, 2k steps ~22 h [E] (matches earlier 31 s/sample) | ~4 s/step |
| Qwen-Image-2.1 7B LoRA, 1 MP + control image | ~8.5k | ~40 s/step + block swap; 500 steps ~6 h, 2k ~22 h [E]; musubi `--fp8_base --fp8_scaled --blocks_to_swap` [V doc], experimental | ~3 s/step H100 |
| VACE-14B / Wan2.2-VACE-Fun LoRA, 416x240x81 | 8.2k | 60-90 s/step incl. swap, 2k ~1.5-2 days [E] | ~2 s/step, 2k ~1 h ~$3 [V paper]; A14B has two experts x2 |
| DMD/rCM 1.3B same-size (Self-Forcing) | | does not fit practically (3 nets + optimiser) | 128 H100-h ~$280-340 [V+E] |
| Cross-size 14B teacher/score -> 1.3B (VDOT++ style) | | no | 4xH200, hours not stated; ~100-300 H200-h ~$0.5-1.4k [E, +-2x] |
| OPD 8-step painter | | untested | "<=2 GPU-h" [V doc] |
- Trainer status [V, 2026-10-12]: musubi-tuner merged Qwen-Image-2.1 (LoRA, full, ordered control images) to `dev` on 2026-10-12, labelled experimental; DiffSynth had Qwen-2.1 training bugs fixed 2026-10-09; ai-toolkit has open "lora_B stays zero" bug (#1075). Pin versions; Windows + sm_120 + 12 GB untested [E].
- **Rent trigger** (user rule): rent only if (a) smoke test shows >60 s/step or OOM for the needed job, or (b) laptop LoRAs improve the eye test but speed remains the gap (distillation), or (c) 14B animator LoRA needed.
- **Local-only shaping:** R1-R3 on 1.3B, R6 reward head are fully local. Rented jobs take weights + public clips + synthetic masks; nothing of the film leaves. Uploading user frames is a user decision and breaks the rule. Weights trained on CC BY-NC-SA clips (Sakuga) may carry NC-SA; weights trained on a show are for personal use (legal status not researched).

## 4 Training ladder (cheapest first)
| Rung | What | Data | Where / cost | Expected effect (evidence) | Risk | User decision |
|---|---|---|---|---|---|---|
| R0 | scene plate bank, best-of-N by seam score, hold/freeze wraps, pan-truth test set | existing runs | CPU/laptop, ~0 | freeze removes boil [L]; best-of-8 beat mean seam 6/8 scenes [L] | low | none |
| R1 | per-show painter LoRA (Qwen-2.1 outpaint), window targets vs whole-picture A/B | 150-500 hidden-sides pairs from the show | laptop 6-22 h [E] | task proven (68 pictures) [V]; show style/composition gain unmeasured | musubi experimental; non-commercial licence | none |
| R2 | per-shot harmoniser (Texler-style) plate->real look | one shot's own frames | laptop, minutes | seam/line weight only [V method] | no invention | none |
| R3 | plate-keyed animator LoRA: 1.3B local, or 14B/A14B (low-noise expert first) on 1xH100 | show crops local; public clips if rented | laptop 6-22 h; rented $3-35/run [E] | 25 clips/1 h gave a working outpainter [V]; ratio/identity gain unmeasured | 14B A14B two experts; overfit small sets | rent? (trigger c) |
| R4 | painter student: OPD 8-step (+ klein-base-4B student on Qwen plates + real crops) | public stills | rented or laptop, <=2 GPU-h [V] | 3x fewer steps | LoRA compat with Turbo unknown | none |
| R5 | animator compression: (a) LongLive-Plug/VDOT-14B 4-step first; (b) 1.3B same-size DMD; (c) cross-size VDOT++ style; (d) causal 2-step (StreamingEffect pattern) | public clips + masks, data-free score | 4xH200 / 8xH100; $0.3-1.4k per run, $1.5-5k all-in [E] | 25x fewer steps [V]; fidelity loss ~2.7 dB PSNR in best published case [V] | static sides; unproven for outpaint | **money; NC data licence** |
| R6 | preference/verifiable reward: reward head on truth + user pairs; RealAlign DPO; NFT/GRPO on fast student | free pairs + ~200-500 user pairs [E] | laptop (head); student (RL) | 512 pairs ~ 851k [V] | single labeller: no evidence | user labelling time |
| R7 | RefDecoder-style reference decoder; TTT layers | | 960 H200-h; 12.8k H100-h | | | skip |
Stop rule: if R1+R3 do not beat the current pipeline by the user's blind test (>=27/40 per comparison), do not distil.

## 5 What to try first (cheap, decision-changing)
1. **X1 pan-truth validity (CPU, <=1 h):** score existing recipe outputs on crop-hidden truth and on pan-revealed truth (jjk-pan, pan-horizontal, tilt shots). Spearman >=0.8 -> crop training/eval valid; lower -> window/pan data required.
2. **X2 painter LoRA A/B (laptop overnight, ~11 h for 2 x 500 steps; klein-4B stand-in if musubi fails):** whole-picture vs window targets from 150 shots of one show vs ausboss v2. Decides "continue vs close" and per-show gain.
3. **X3 VACE-1.3B LoRA smoke (30 min):** 20 steps at 416x240x81: s/step, peak VRAM. Decides local vs rented for R3.
4. **X4 free-pair judge (<=1 h):** logistic head on seam_self + DINO/CLIP differences using truth-vs-sample pairs; check agreement with the user's past picks. >=75% -> best-of-N viable; else collect pairs.
5. **X5 (rent ~1-2 h, ~$5; shared with the speed-up agent):** merge LongLive-Plug Wan2.1-14B few-step LoRA into Wan2.1-VACE-14B and run VDOT-14B on 3 hidden-sides scenes. Decides whether a cheap 4-step teacher exists before any distillation spend.

## 6 Sources (read 2026-10-13)
Datasets: github.com/KytraScript/SakugaDataset README; HF datasets trojblue/sakugabooru2025, qiulu66/AnimeShooter, davidchan/anim400k, alfredplpl/anime-with-caption-cc0, puruchinera/Anime-Background-Finetuning-V1.1; arXiv 2405.07425, 2506.03126, 2507.20368, 2412.10255 (AniSora, 10M clips).
Outpaint training: 2309.02119, 2601.11048 (html), 2511.03272 (pdf), 2403.13745 (html), 2510.08555 (pdf), 2401.15652, 2508.00418; HF ausboss/Qwen-Image-2.1-Outpaint-LoRA card.
Distillation: 2512.06802, 2610.03221 (html), 2609.38154 (html) + github NVlabs/LongLive/LongLive-Plug, 2605.17019 (pdf) + github showlab/StreamingEffect, 2608.21424 (pdf), 2606.25473 + 2510.08431 + github NVlabs/rcm, github guandeh17/Self-Forcing, thu-ml/Causal-Forcing, hhhh1138/VDOT + HF yutongwang1012/VDOT, 2405.14867, 2501.08316, 2506.09350, 2403.12015, 2410.11081, 2603.01685, 2608.13391, 2605.15055, 2606.27377; DiffSynth-Studio docs/en/Training/{Distillation,Offload_Training,FP8_Precision}.md; musubi-tuner PR #1145 + docs (dev); HF Qwen/Qwen-Image-2.1-Turbo card.
Memory/propagation: 2403.14468, 2405.16537, 2412.19761, 2503.05639, 2408.11402, 2412.08975, 2604.18215, 2603.17117.
Adaptation: 2504.05298 (html), 2004.14489 (pdf), 1909.07957, 2406.04814, 2605.15196 (RefDecoder, pdf).
Preference: 2311.12908, 2311.13231, 2506.03517 (pdf), 2412.14167, 2505.05470, 2505.07818, 2509.16117, 2501.06655, 2605.19839 (pdf), 2410.21966, 2504.10044, 2601.23286, 2402.10210.
Prices: Spheron H100 page ("live as of 13 Oct 2026") [V]; WebSearch snippets (Vast.ai, RunPod, StockHub 2026-09-06, Vast comparison 2026-07-20) [S]; getdeploying.com 403.
Project: plans/reports/research-261012-1521-ai-surround-anime-outpainting-synthesis.md; researcher-261012-1407-{outpainting-sota..., real-time-generative...}.

## 7 Unresolved questions
1. Does a 1.3B causal keyed student keep far-side content and identity at A14B+plate quality? Untested anywhere; the factorisation (plate supplies knowledge) is my bet [E].
2. Does crop-hidden scoring predict true beyond-edge quality (X1)?
3. Trainers on Windows + sm_120 + 12 GB: s/step unmeasured; musubi Qwen-2.1 is 1 day old and experimental.
4. LongLive-Plug on Wan2.1-VACE-14B / Wan2.2-VACE-Fun-A14B: not in the verified set. VDOT++ hours/GPU-h not stated (my $ range +-2x).
5. Does the ausboss LoRA work on Qwen-Image-2.1-Turbo, or must it be retrained on Turbo?
6. Licence of weights trained on NC-SA clips vs the free-release intent; legal status of training on the user's episodes (not researched, not legal advice).
7. How many pairs does a single user's reward head need? No paper; X4 measures it.
8. Sakuga resolution/aspect mix and access turnaround not checked; APT/AAPT code availability not checked; EditStream code "soon".
9. DMD static-output risk on action clips: measure sides motion vs truth before accepting a student.
10. Digest-level reads: arXiv HTML greps were exact, but papers were not read in full; EditStream/StreamingEffect GPU-hours not stated.
