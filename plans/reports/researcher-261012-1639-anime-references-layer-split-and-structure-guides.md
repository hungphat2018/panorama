---
title: "AI surround (anime): reference gathering, layer split, structure guides"
date: 2026-10-12 ~17:30 (Asia/Bangkok)
type: research report (researcher agent; saved by the parent session: the agent's harness forbade writing report files)
plan: plans/261012-1639-ai-surround-quality-baseline/plan.md
---

# AI surround (anime): reference gathering, layer split, structure guides

No model downloaded, no GPU or CPU experiment, no repo file edited. Prior reports read first (synthesis sections 1-5, outpainting-bases, keep-the-seen).

Tags: **[V]** read on the cited page/API today; **[Vp]** number from a paper-page digest, not re-checked against the PDF; **[E]** estimate/inference; **[L]** measured locally; **[eye]** by eye on the parent's neighbour contact sheets. S-ids = sources at the end (URL + date). Table cells are [V] unless tagged. "d" = working days incl. testing [E].

## 0 Decisions this report settles

1. **Layered recipe in this session's quality baseline: include as ONE pilot arm (L) on 3 scenes, next to a same-references single-pass control arm (R). Not the backbone.** Every part has a local tool; **no published evidence anywhere that the chain works on TV anime** (searched: arXiv, HF papers, GitHub, web). Build ~1-2 d [E] with hand-picked references. Without arm R a win cannot be credited to layering.
2. **Weakest link = the character-layer join**: a cut-off character must continue across the seam with matching silhouette, line weight, colour and pose, then sit on a separately painted background (extra seams: matte edge, outline thickness, contact shadow, haze). A still completion also freezes while the real character moves. Second: BG object scale ("ratio") is untouched by layering. Third: matte quality on hair/effects. (Section 6.)
3. **Automate reference gathering later in this order**: CCIP on the repo's existing head tracks (A1) -> MegaLoc + time window (A2) -> SIFT/ECC for re-used paintings, RoMa v2 for other views (A3). Only A1 has anime-specific evidence, and that is informal.
4. **Layer-decomposition nets do not fit frame-to-layers.** Qwen-Image-Layered = Photoshop design files; See-through = single character illustrations, backgrounds omitted by design; LayerD = graphic design. Skip in this session.
5. **Structure guides are a separate lever**: run a "ratio probe" (tile pitch vs hidden truth) with Z-Image ControlNet-Union inpaint, diffusers-native. No tool extends a guide into the sides; that part is own code.
6. **Free references in this episode [eye, S53]**: the ending (shots ~334-340) shows full-body flat-colour design sheets of 3 main characters (red-haired boy 335, green-haired girl 337, pink-haired girl 340); bathroom shots 102-106 show one room at several angles/scales; the green-haired girl appears fuller in shots 152/154/160/161. Hand-pick these for the pilot.

## 1 Terms in plain words

- **Fingerprint (embedding)**: list of numbers summarising an image; similar images give close lists.
- **Re-ID**: decide whether two crops show the same character. **AUC**: 1.0 = always tells same from different, 0.5 = coin flip.
- **Place recognition**: same idea for "same location".
- **Matcher / registration**: matcher finds which point of B matches each point of A (sparse = a few hundred landmarks, dense = every pixel). Registration = the shift/zoom/warp that lays B on A.
- **Matte (alpha)**: per-pixel transparency of a cut-out character. **Clean plate**: background with characters removed.
- **ControlNet / IC-LoRA**: add-ons that make a generator follow a structure picture (lines, depth, pose).
- **Vanishing point (VP)**: where parallel room/street lines appear to meet; it fixes how object size shrinks along a surface.

## 2 Summary per role

| Role | Top pick | Runner-up | Why | Effort [E] |
|---|---|---|---|---|
| A1 same character, fuller frame | CCIP on head crops of existing head tracks, cluster per episode, same-scene window | SigLIP 2 on person crops (head not visible) | only anime-trained identity model; informal AUC 0.999 vs SigLIP 2 0.949 vs DINOv2 0.678; hobby pipelines already cluster 10k screenshots; same deepghs ONNX stack as the repo | 0.5 d |
| A2 same place, other angle | MegaLoc on character-blanked key frames, candidates within +-20 shots | SigLIP 2 / DINOv3 global fingerprint | best-documented general place/pair retrieval, MIT; zero anime evidence for any option; time window = free precision | 0.5 d |
| A3 registration | SIFT+RANSAC or ECC for same-painting crop/zoom; RoMa v2 where it fails | VGGT-Omega (N-view geometry); LightGlue | exact and free for re-used paintings; RoMa v2 newest dense matcher, photo-trained, anime untested | 0.25 d + 0.5-1 d |
| B1 character matte | ToonOut (compare with SAM 3.1 box-prompted and imgutils ISNet on 20 frames) | SAM 3.1 | only anime matting fine-tune with numbers (PA 99.5%) but trained on SDXL-made images; SAM 3.1 strong generic, no anime eval | 0.5 d |
| B2 image-to-layers nets | skip for frames | See-through, for a clean character crop/sheet only | see decision 4 | - |
| B3 clean plate | no separate tool: BG painter treats character region near seam as unknown; temporal median where characters move | masked fill by klein/Qwen; LTX Clean-Plate (video) | no anime evidence for any inpainter; real centre is never altered, so only BG near the seam needs filling | 0 / 0.5 d |
| B4 anime video layers | LayerAnimate Layer Curation Pipeline (motion layers) | none | only released anime-video layer tool; pans not handled; nothing for the camera-filter layer | 1 d |
| C1 line art | AniLines (detail mode) | Anime2Sketch | built for cel animation + video, MIT | 0.5 d |
| C2 depth/normals | MoGe-2 | Depth Anything 3 (Large-1.1) | metric points + normals + FOV in one pass, MIT; neither tested on anime | 0.25 d |
| C3 lines/VP | LSD or DeepLSD + RANSAC VP fit (own code) | GeoCalib (focal/gravity) | no anime estimator exists; clean line art suits classical detectors [E] | 1 d |
| C4 image generator, guide + mask | Z-Image-Turbo + Fun-ControlNet-Union 2.1 via diffusers `ZImageControlNetInpaintPipeline` | Qwen-Image-2.1 + Fun-ControlNet-Union (NC licence; ComfyUI/VideoX-Fun) | lowest friction (native diffusers, Apache); Qwen-2.1 has Lineart + control+mask but heavy and NC | 0.5 d / 1 d |
| C4 video generator, guide | Wan2.2-VACE-Fun-A14B | LTX-2.3 Union-Control (+ In-Outpainting) | VACE takes control and kept real pixels in one input (paper); LTX combination unverified | per earlier plan |

## 3 Role A: gather references (one episode, ~350 shots)

### A1 same character

| Tool | What | Version / date | Licence | Fit 12 GB | Anime evidence | Effort | Src |
|---|---|---|---|---|---|---|---|
| CCIP (deepghs) | "same anime character?" distance for two single-character crops | model 2023-05-15, edited 2024-09-09; imgutils v0.19.0 2025-09-10 | model OpenRAIL; lib MIT | yes, 0.38 GB ONNX | trained on fan-art thumbnails (v1 4,001 chars/292,800 imgs; v2 faces 37,886/836,217), not TV frames; own F1 0.941; community AUC 0.999; 10k-screenshot run -> 146 clusters, no accuracy stated | pip `dghs-imgutils`; repo already runs deepghs ONNX detectors; 0.5 d | S1-S4,S6,S54 |
| SigLIP 2 so400m | general image-text fingerprint | 2025-02-17 | Apache-2.0 | yes, 1.1B (~2.3 GB fp16) | community AUC 0.949 | HF transformers, 0.5 d | S6,S12 |
| PixAI Tagger v1.0 | anime tagger (30,877 tags, 8,308 named characters), SAM3-based; its inner vector acts as fingerprint | 2026-09-15 | Apache-2.0 | yes, 0.49B at 1008 px | community AUC 0.960; tag names useless for a new show | HF, 0.5 d | S6,S55 |
| DINOv3 ViT-L | self-supervised features, strong for shape/geometry | 2025-08-06; repo pushed 2026-07-15 | custom DINOv3 Licence, gated (no NC clause in grant text read) | ViT-L yes; 7B no | none for identity; DINOv2-small AUC 0.678; trained on web photos | HF, 0.5 d | S6,S11 |
| CLIP + YOLO face "prototypes" (corte-cenas) | hobby Windows app: cuts episode into shots, groups by character; "Discovery mode" clusters the episode's own faces | 2026-07-04, pushed 2026-08-21 | none stated | yes | no accuracy given | reference only | S5 |

Notes A1
- **Repo fit [L S54]**: content model already tracks 517 face / 593 head / 574 person tracks per episode (deepghs YOLO ONNX, every 12th frame). CCIP = one extra ONNX call per head track.
- **Recipe [E]**: crop head + padding per track -> CCIP -> cannot-link tracks in one frame -> agglomerative clusters -> inside a cluster prefer frames where the person box does not touch the edge, is largest, shows most body keypoints (community found DWPose body usable on anime, hand model failed <0.5 confidence [S6]). **Restrict references to the same scene (+-20 shots)**: outfits/hair state change between scenes [E].
- **Limits [V S4]**: in the 10k run 1,774 images had no head, 4,705 of 16,927 heads (28%) failed size (>=64 px), sharpness or confidence filters; authors report public character-tag models gave wrong names. This episode mixes chibi, manga-panel and halftone gag styles [eye S53: shots 104, 106, 156]: expect CCIP misses there.
- **Do not use general re-ID or VLMs**: on manga, TransReID/InstructReID/ReID5o score 37-40 mAP vs 33 chance; three VLMs <=1.1% [Vp S8] (manga, different models).
- **Do not auto-grow galleries**: 62-89% of a model's own additions were wrong; one reference per character matched a big gallery [Vp S8]. Keep a human/by-eye gate.
- **Upgrade path [E]**: CAST refines an embedding per series from tracking triplets [S9]; fine-tune on in-episode tracks only if CCIP fails. No code checked.
- **Evidence gap**: no anime re-ID benchmark on TV frames. OregairuChar = detection only (1,600 frames, 11 characters, YOLOv5 best, no re-ID models compared) [Vp S10]. Community AUC table = 8 characters x 6 images, ~460 pairs, single author [S6]: informal.

### A2 same place, other angle

| Tool | What | Version / date | Licence | Fit | Anime evidence | Effort | Src |
|---|---|---|---|---|---|---|---|
| MegaLoc | one 8,448-number fingerprint per image, nearest = same place (DINOv2-B + SALAD) | v1.0 2025-02-25; pushed 2026-08-06; CVPRW 2025 | MIT | yes, 1.4 GB | none. 2026 pair-retrieval study: global descriptors "increasingly suitable"; ViT ones (SALAD, MegaLoc) better with ambiguity/missing frames; CNN EigenPlaces-512 0.13 s vs 1.8-2.8 s per scene on RTX 3090 [Vp]. No training code/data released | torch.hub, 0.5 d | S14,S15 |
| SigLIP 2 / DINOv3 global | same use, generic | see A1 | see A1 | yes | none | HF | S11,S12 |
| Also checked | SALAD (GPL-3.0), BoQ (MIT, 2024), EigenPlaces, AnyLoc (weak: P@1 31.25 vs 56.25 on Tanks&Temples) | | | | | | S15 |

Notes A2
- **No anime evidence.** Production fact [V S24]: "In traditional animation, every time a painted background has to be changed or the camera cuts, a scene change is required." So another angle of a room is normally another painting [E]; expect hits for same-room runs (bathroom 102-106 [eye S53]) and re-used paintings; pixel overlap should be rare [E].
- **Free levers [E]**: candidates within +-20 shots; blank person boxes (repo has them) or use the matte before embedding; skip graphic-overlay shots (halftone, title cards).
- **Pattern with outside evidence**: global shortlist then local verification; animal re-ID fuses global descriptors with LoFTR/LightGlue [Vp S16]. Here: MegaLoc top-5 -> SIFT/RoMa v2 inlier count.

### A3 registration (overlapping shots, flat colour, zoom)

| Tool | What | Version / date | Licence | Fit | Anime evidence | Effort | Src |
|---|---|---|---|---|---|---|---|
| RoMa v2 | dense matcher: each pixel of A -> where it lands in B, confidence, 2x2 precision | arXiv 2511.15706 (2025-11, ECCV 2026); v2.0.1 2026-04-04; pushed 2026-08-30 | code MIT; backbone DINOv3 licence; weights licence not stated | 4.8 GB at 640x640 batch 8; 30.9 pairs/s on H200 [Vp] | none (see P1). Trained on photos + graphics (Hypersim, TartanAir...); better than RoMa on textureless; weaker under modality change (WxBS 55.4 vs 60.8) [Vp] | uv/pip from git; README "tested on Linux"; Windows unverified; 0.5-1 d | S17 |
| OpenCV SIFT/AKAZE + RANSAC, ECC | classic landmark matching / alignment | OpenCV 5 in venv | Apache-2.0 | CPU | none. [E] clean line art gives corners; tile repeats give false matches, RANSAC filters; zoom beyond ~3x needs an image pyramid | 0.25 d | - |
| LightGlue (+DISK/ALIKED/SIFT) | sparse matcher | pushed 2026-02-18 | Apache-2.0; DISK Apache, ALIKED BSD-3, **SuperPoint restrictive** | yes | none | in HF transformers, 0.25 d | S22 |
| VGGT-Omega | N images -> camera poses + depth for all | CVPR 2026 oral; weights 2026-05; repo pushed 2026-09-22 | "other", gated by access request | 6.0 GB (1 img), 9.7 (50), 13.4 (100) at 624x416 on A100 -> ~75 frames fit [E] | none | repo + approval, ~1 d | S20 |
| MASt3R / DUSt3R | 3D-aware pair matcher | pushed 2025-06-30 | CC BY-NC-SA 4.0 | yes | none; RoMa v2 ties MASt3R on ScanNet-1500 AUC@5 (33.6) [Vp] | repo | S17,S19 |
| Also checked | UFM (BSD-3, 2025-06-10, flow + wide-baseline, claims 6.7x faster and 62% lower error than RoMa), MatchAnything (cross-modality, 2025-01, licence unclear), MapAnything (Apache-2.0, v1.1.3 2026-07-17), pi3 (BSD-2, 2025-07-14) | | | | | | S21 |

Notes A3
- **P1 negative evidence [V]**: GitHub issue search 2026-10-12 for "anime" and "cartoon" returned 0 issues in LightGlue, RoMa, RoMaV2, MASt3R, DUSt3R, VGGT, VGGT-Omega, Depth-Anything-3, MoGe, Marigold, DINOv3, SAM 3, MegaLoc (Depth-Anything-V2: 1, unrelated).
- **Anime matching work solves other problems**: BasicPBC (CVPR 2024) and manga line-art region correspondence (CVPR 2026) match regions between two drawings of one pose; AnimeRun (NeurIPS 2022) and LinkTo-Anime (2025) are flow/segment datasets for consecutive frames [S51]. None registers shots across a cut.
- **Order of use [E]**: SIFT/ECC on pairs flagged "same room"; RoMa v2 only where it fails. If overlap is real (same painting) paste real pixels into the side (zero invention); else hand the other shot to the generator as a reference. Cross-shot overlap rate in this episode is unmeasured (side coverage from slides was 0.1% [L]).

## 4 Role B: layer split

### B1 character matte / segmentation

| Tool | What | Version / date | Licence | Fit | Anime evidence | Effort | Src |
|---|---|---|---|---|---|---|---|
| ToonOut | BiRefNet fine-tuned for anime cut-outs (hair, transparency) | weights 2025-09-09 (edited 2026-06-12); paper 2025-09-08 | MIT code+weights; data CC BY 4.0 | yes, 0.89 GB (.pth), BiRefNet 221M | pixel acc 99.5% vs BiRefNet 95.3%, BRIA RMBG-2 97.8%, Photoroom (closed) 99.2% on own 126-image test; images generated with an SDXL anime checkpoint and hand-annotated, not TV frames; "items" weakest 96.6% [Vp] | repo + weights, demo notebook; 0.5 d | S25 |
| SAM 3 / 3.1 | Meta promptable segmenter + tracker (text, exemplar, box) | SAM 3 2025-11-07 (paper 2025-11-20); SAM 3.1 2026-03-27 | SAM License (custom, gated; no NC clause in grant text read) | yes, 0.86B; 30 ms/img on H200 [Vp] | none found. AnimeIns study: Grounded SAM original mask AP 46.7 vs 93.2 dedicated, 90.1 after fine-tune [Vp]; LayerAnimate builds anime layers with SAM + SAM2 [S33] | HF transformers (library tag) or repo (Py>=3.12, torch>=2.7, CUDA>=12.6); 0.5 d + access wait | S26,S27,S33 |
| imgutils ISNet-IS | anime-character segmenter shipped with the same pip package as CCIP | imgutils 0.19.0 | MIT lib | yes | no TV-frame numbers | same pip | S2 |
| AnimeInsSeg | splits individual characters (RTMDet-Ins + IS-Net) | repo pushed 2025-04-29 | weights MIT | yes | trained on synthetic composites (chroma-key videos + Danbooru art on cartoon BGs); 307-image test, mask AP 93.2 [Vp] | mmdet env, 1 d | S27 |
| Also | BRIA RMBG-2.0 (CC BY-NC); BiRefNet_HR-matting (MIT, soft alpha); MatAnyone2 (video matting from first-frame mask, 2026-03-15, licence not stated); LTX-2.5 Alpha-Gen IC-LoRA (video alpha, <=1920x1088, <=145 frames, "H100/B200-class" for full HD, 2026-09-28, LTX licence, no anime) | | | | | | S36 |

Notes B1: nothing is validated on TV frames with busy backgrounds, motion blur, glows. Test 20 frames by eye with the matte overlaid on a flat colour. The repo's person boxes can be SAM box prompts.

### B2 image-to-layers networks

| Tool | What | Version / date | Licence | Fit | Anime evidence | Effort | Src |
|---|---|---|---|---|---|---|---|
| Qwen-Image-Layered | one image -> 3-10 RGBA layers, recursive | 2025-12-17 | Apache-2.0 | 20.4B: 40 GB DiT, needs Q4 GGUF/FP8 + offload [E] | trained on Photoshop design files, evaluated on Crello (graphics); no anime, VRAM or video numbers; hidden-part inpainting quality not reported [Vp] | diffusers 0.41 has `pipeline_qwenimage_layered` [P2]; 0.5 d | S29 |
| Qwen-Image-2.1 | unified generate + edit; native RGBA; up to 10 reference images; mask/circle edits | released 2026-09-20 (HF repo 2026-09-14); Turbo 8-step 2026-10-09 | **Qwen Research Licence: research/evaluation only** | DiT 14.2 + text encoder 17.5 GB bf16; int8 7.3 + 9.4 GB (Comfy-Org repack); offload | none on anime | diffusers `QwenImage21Pipeline` present in 0.41.0 [P2]; ComfyUI native; 0.5 d | S38 |
| See-through | one character illustration -> up to 23 inpainted body-part layers + draw order (SDXL LayerDiff3D + anime-tuned Marigold + SAM body parsing, 19 parts) | arXiv 2026-02-03; SIGGRAPH 2026; repo pushed 2026-10-05 (4.5k stars) | code Apache-2.0; weights OpenRAIL++ | weights 10.2 + 3.3 GB, offload [E]; ~74 s + ~10 s per image on RTX 4090 (page text garbled) [Vp] | trained on 9,102 Live2D models; **backgrounds omitted by design**; no screenshots; SAM+LaMa baseline FID 81.1 vs 18.4 [Vp] | repo + ComfyUI plugin, 1 d | S30 |
| Also | LayerD (graphic designs, Apache-2.0, v0.1.0 2026-01-05); workflow-aware anime decomposition into line/flat/shadow/highlight (arXiv 2026-03-16, **code link 404 today**); Bunraku (Live2D from illustration, 2026-07-29) | | | | | | S31,S32 |

Use See-through only to split a clean character crop or design sheet into parts. SAM body-part parse (19 parts) might double as a "how much of the body is visible" gauge for A1 [E].

### B3 clean plate (background behind removed characters)

| Approach | What | Date / licence | Fit | Anime evidence | Src |
|---|---|---|---|---|---|
| Temporal median after registration | static camera: median over the shot's frames = BG wherever characters move; pixel-true | none / none | CPU | none [E]; fails on held dialogue where the body never moves | - |
| Outpainter with a hole | mark character region near the seam as unknown, BG painter fills and extends in one pass | existing stack | existing | klein-inpaint pitfall (copies reference) known [L] | - |
| DiffSynth Template-Inpaint (klein-base-4B) | masked fill | 2026-04-22, Apache-2.0 | "40 GB direct"; low-VRAM script linked | none; 0 downloads | S43 |
| LaMa | classic inpainter | n/a | small | See-through: LaMa errors "typically lie in the far-back stratum ... difficult to observe after compositing"; AnimeIns used PatchMatch + latent-diffusion inpainting, qualitative only [Vp] | S27,S30 |
| LTX-2.5 Clean-Plate IC-LoRA | video: removes people/dynamic subjects, rebuilds BG, no mask input | 2026-09-09, LTX-2.x community licence | 22B; validated 1920x1088 | trained on 26 real clips; "out of scope: subjects that dominate/occlude most of the frame"; no anime [V] | S35 |
| Omnimatte family | video layers incl. shadows/reflections: Generative Omnimatte (CVPR 2025), OmnimatteZero (SIGGRAPH Asia 2025, training-free), EasyOmnimatte (2025-12-26), TriLayer/DBL (2026-07-28) | various | heavy | real video, no anime | S34 |

**No anime clean-plate benchmark or paper found** (HF papers, 7 queries). Test with truth [E]: cut a character-shaped hole (matte from another frame) into a character-free frame, inpaint, compare with the original.

### B4 anime-specific video layer / cel separation

LayerAnimate Layer Curation Pipeline (released 2025-08-22): SAM1 ViT-H + SAM 2.1 + UniMatch flow; masklets merged by motion score (static < 0.1, capacity N=4) into layers; "static vs dynamic, not background vs foreground"; **camera pans not addressed; no decomposition metrics** [Vp]; LayerAnimate code Apache-2.0, pipeline repo licence not stated [S33]. **Nothing found for the post-process ("camera filter": haze, glow) layer**; the per-shot colour fit [L] is the practical substitute.

## 5 Role C: structure guides for "ratio"

### C1-C3 estimators

| Tool | What | Version / date | Licence | Fit | Anime evidence | Effort | Src |
|---|---|---|---|---|---|---|---|
| AniLines | line art from anime images and video; `basic` = main structure, `detail` adds backgrounds and cel edges; use 1080p input | created 2025-02-06, pushed 2025-09-18 | MIT | yes; weights on Google Drive | built for cel animation, compared with Anime2Sketch/MangaLine in README (no numbers) | repo, 0.5 d | S46 |
| Anime2Sketch / MangaLineExtraction | illustration/manga line extractors | pushed 2026-09-08 / 2025-01-10 | MIT | yes | illustration/manga trained | repo | S46 |
| MoGe-2 | one image -> metric-scale points, depth, normals, FOV | weights 2025-06-10, edited 2026-09-02 | MIT | yes | none | pip/repo, 0.25 d | S48 |
| Depth Anything 3 | any-view depth + camera pose | repo 2025-11-12; Large-1.1 2025-12-11 | Apache-2.0 for Small/Base/Large-1.1; Giant and original Large CC BY-NC | yes (Comfy-Org repack 2026-05-21) | none | pip/Comfy | S47 |
| Marigold (+ See-through's anime Marigold) | diffusion depth/normals; anime variant = draw-order pseudo-depth for **characters** | Marigold pushed 2026-09-06 | code Apache-2.0, weights OpenRAIL++ | yes | characters only | diffusers | S49,S30 |
| DeepLSD / ScaleLSD / M-LSD / GeoCalib / NeurVPS | line segments, focal+gravity, vanishing points | DeepLSD pushed 2026-09-06; ScaleLSD CVPR 2025; GeoCalib ECCV 2024; NeurVPS 2019 | MIT / MIT / Apache-2.0 / Apache-2.0 / MIT | yes | none, all photo-trained | repo | S50 |

No anime-specific normal or vanishing-point estimator found. See-through itself states that depth/normal baselines are "primarily designed for natural photorealistic imagery" (no fail numbers) [Vp S30].

### C4 generators that take a guide

| Model | Guides | Guide + mask together | Version / date | Licence | Fit | Anime evidence | Effort | Src |
|---|---|---|---|---|---|---|---|---|
| Z-Image-Turbo + Fun-ControlNet-Union 2.1 | canny, depth, pose, MLSD, HED, scribble, gray | yes, "inpainting mode"; use larger scale for continuity | CN 2025-12-11, v2602 2026-02-26 | Apache-2.0 | base 6.15B (24.6 GB fp32 files, ~12 GB bf16), CN 6.7 GB or lite 2.0 GB; fp8/offload [E] | none; CN trained on 1M general/human images | **diffusers 0.41 `ZImageControlNetInpaintPipeline` (control_image + mask_image)** [P2]; 0.5 d | S41 |
| Qwen-Image-2.1 + Fun-ControlNet-Union | 8: canny, depth, gray, HED, **lineart**, MLSD, pose, scribble | yes, control and mask fed together | 2026-09-23 | Qwen Research (NC) | CN 7.55 GB (int8 3.78) + base int8 7.3 + TE int8 9.4 GB; offload | none | ComfyUI model_patches / VideoX-Fun (Apache-2.0); diffusers pipeline for the CN not confirmed; 1 d | S38,S39 |
| Qwen-Image-2512 + Fun-ControlNet-Union | canny, HED, depth, pose, MLSD, scribble, gray | yes | 2026-01-12, v2602 2026-02-13 | Apache-2.0 | base 20B, GGUF + offload | none | diffusers has Qwen ControlNet-inpaint pipeline for InstantX CNs [P2] | S40 |
| FLUX.2-klein-base-4B + DiffSynth Templates | structure control, inpaint, content reference, colour; composable by design | claimed by framework, not shown for this combo | 2026-04-22; paper 2026-04-27 | Apache-2.0 | "40 GB direct"; low-VRAM scripts | one "anime aesthetic" fox demo only; 0 downloads = unproven | DiffSynth-Studio (pushed 2026-10-09), 1 d | S43 |
| klein RefControl depth / canny LoRAs | reference image + depth/canny map side by side | no | 2026-05/06 | Apache-2.0 | small | none | HF | S44 |
| FLUX.2-dev + Fun-ControlNet-Union | canny, HED, depth, pose, MLSD, scribble, gray | yes | 2025-12-01, v2602 | FLUX dev NC | 32B | card: ControlNet "adheres more reliably" than FLUX.2 edit-style conditioning | VideoX-Fun | S42 |
| SDXL anime (NoobAI-XL / Illustrious) + lineart_anime / inpaint CNs | line art, inpaint | separate nets | CNs 2024-11, 2025-03 | OpenRAIL-type | yes | anime-native base, old, 1024 px | diffusers | S52 |
| Anima 2B + community Canny CN | canny | no | Anima 2026-01-29; CN 2026-09-13 (15k images, 3,000 steps) | NC | yes | anime-native; CN ecosystem thin | ComfyUI fork branch | S52 |
| Wan2.2-VACE-Fun-A14B / Wan2.1-VACE | depth, pose, scribble, layout, flow, gray + masks + reference | one input holds control frames and kept real pixels ("reactive" vs "inactive" streams); depth-guided expansion implied, **not demonstrated** [Vp] | 2025-09-10 | Apache-2.0 | GGUF Q8 ~18.7 GB x 2 experts | none | per earlier plan | S45 |
| Wan2.2-Fun-A14B-Control (+Camera) | canny, depth, pose, MLSD, trajectory | no (separate InP model) | 2025-08 | Apache-2.0 | heavy | none | VideoX-Fun | S45 |
| LTX-2.3 Union-Control / In-Outpainting IC-LoRAs | canny+depth+pose; reference + binary mask | combination not stated | 2026-03-05 / 2026-06-17 | LTX-2.x community licence | 22B | none | per earlier plan | S37 |

Notes C
- **Missing piece**: no tool extends the guide into the sides. Options [E, none has evidence]: (1) detect lines on the real picture with characters masked, group into 2-3 families, fit VPs, redraw the families across the side canvas with spacing following perspective (cross-ratio of visible spacing), feed as Lineart/MLSD + mask; (2) fit floor/wall planes from MoGe-2 normals/depth and extend; (3) two-stage: generator draws line art first, colours second; (4) estimate depth on the generated plate, compare with extended geometry, regenerate.
- **Ratio probe [E]**: measure tile pitch (or any repeated-object size) in the generated side vs the hidden truth; baseline klein-green +30% [L].
- **Anime-quality evidence for every generator above: none found.** Model cards use photos; one anime-style demo (DiffSynth fox).

## 6 Verdict: layered recipe

**Can it be built now? Yes** (all parts local). **At quality? Unknown**; nothing published on TV anime.

| Part | Tool now | Risk |
|---|---|---|
| matte | ToonOut / SAM 3.1 / ISNet, pick by eye | medium: unvalidated on TV frames; matters at the seam and for hair |
| BG painter | the current outpainter with a hole mask (+ guide later) | same ratio/composition problems as today |
| character completer | Qwen-Image-2.1 (RGBA output, 10 references) or klein multi-reference | **highest**: identity from references helps, pose/line/colour continuity unproven |
| composite | per-shot colour fit + live seam pull (exists) | medium: extra seams, no camera-filter separation |

**Weakest link**: the character-layer join. A still plate cannot follow a moving real character; held frames (74% of frame steps repeat [L]) soften this for static poses only.
**Why not skip layering**: 52% of frames have a person box at a side edge [L]; wrong hair/colour at the seam is the failure the user named; this is the cheapest direct test of "information from elsewhere in the episode".

**Pilot [E]** (run later):
- Scenes: edge-person-still, edge-head-closeup, held-dialogue (bathroom).
- References by hand from the neighbour sheets and the ending design sheets.
- Arm R: single pass, same references. Arm L: matte -> BG fill with hole -> RGBA character completion -> composite + colour fit.
- Test (forced choice by eye, truth on top, 100% zoom): L stays only if it beats R in >=2 of 3 scenes, hair/colour right in >=4 of 5 completions, and silhouette/line at the seam shows no visible step or halo.
- Matte pre-test: 20 frames on flat colour; if >30% show halos or holes, drop L.

Not in this session: automating A1-A3, layer nets, video-level layered compositing.

## 7 Method and limits

- Searched: HF API (models, datasets, papers, file trees), GitHub API (repos, READMEs, issue counts, diffusers v0.41.0 tree), arXiv API, ~25 WebSearch, ~15 WebFetch. Paper numbers come from page digests (cut at 100k chars: DINOv3 retrieval/matching tables and SAM 3 appendices not reached) [Vp]. Reddit via pullpush gave noise.
- Not covered: Windows installs, real quality on this episode, commercial licensing detail (LTX transfer clause in earlier report), manga-specific Magi models, closed APIs, per-tool VRAM at 1080p.
- Community anime numbers (AUC table, c6-cluster, corte-cenas) are single-author hobby repos: informal.
- Environment today [L]: C: 137 GB free, GPU 11.9/12.2 GB used at 100% (parent's job), diffusers 0.41.0 (2026-10-06) already contains `QwenImage21Pipeline`, `ZImageControlNetInpaintPipeline`, qwenimage_layered, flux2_klein inpaint/kv [P2].

## 8 Unresolved questions

1. Does CCIP hold on this episode's heads (small, chibi, manga-panel, halftone gags)? Needs a P@5 by-eye test on ~10 anchor shots.
2. Do ToonOut / SAM 3.1 / ISNet give usable alpha on TV frames (hair, glows, motion blur)?
3. Does Qwen-Image-2.1 or klein multi-reference complete a cut-off anime character with the right identity and an RGBA layer that joins at the seam?
4. How often do two shots share registrable background pixels in this episode? A CPU SIFT pass over 357 shots (~10 min) would answer.
5. Do structure guides actually fix tile pitch? Needs the ratio probe; unknown whether Z-Image CN-Union inpaint follows a drawn line guide inside a masked side.
6. Licences: RoMa v2 weights unstated; VGGT-Omega "other"; DINOv3/SAM custom; CCIP and See-through weights OpenRAIL(++); Qwen Research = non-commercial; LTX transfer clause. Decide after the quality tests, as the user said.
7. Windows: RoMa v2 README is Linux-tested, SAM 3 needs Py>=3.12 and CUDA>=12.6 (venv has torch 2.14.1+cu130), See-through/VideoX-Fun untried.
8. Moving characters at the seam: how should a completed character follow the real one (video model with reference + mask, or warp)? Out of scope here.
9. Camera-filter (haze/glow) separation: is the per-shot colour fit enough, or is a real layer needed?

## 9 Sources (page date = created/edited/pushed or arXiv v1; all read 2026-10-12)

- S1 https://huggingface.co/deepghs/ccip (2023-05-15 / 2024-09-09; F1 table)
- S2 https://github.com/deepghs/imgutils (v0.19.0 2025-09-10; pushed 2025-10-11); https://dghs-imgutils.deepghs.org/main/api_doc/segment/isnetis.html
- S3 https://huggingface.co/datasets/deepghs/character_similarity
- S4 https://github.com/Neuro987/c6-cluster (2026-08-16)
- S5 https://github.com/leviclementino1-creator/corte-cenas (2026-07-04 / 2026-08-21)
- S6 https://github.com/Tesixki/anime-illust-similarity (2026-09-21; calibration AUC table)
- S8 https://arxiv.org/abs/2609.34032 (2026-09-27); S9 https://arxiv.org/abs/2201.07619 (2022-01-19); S10 https://arxiv.org/abs/2511.05263 (2025-11-07)
- S11 https://huggingface.co/facebook/dinov3-vitl16-pretrain-lvd1689m (2025-08-06); licence https://github.com/facebookresearch/dinov3/blob/main/LICENSE.md (repo pushed 2026-07-15); https://arxiv.org/abs/2508.10104
- S12 https://huggingface.co/google/siglip2-so400m-patch14-384 (2025-02-17); https://arxiv.org/abs/2502.14786
- S14 https://github.com/gmberton/MegaLoc (v1.0 2025-02-25; pushed 2026-08-06); https://arxiv.org/abs/2502.17237
- S15 https://arxiv.org/abs/2603.13917 (2026-03); S16 https://arxiv.org/abs/2408.12934 (2024-08-23)
- S17 https://github.com/Parskatt/RoMaV2 (v2.0.1 2026-04-04); https://arxiv.org/abs/2511.15706 (2025-11)
- S19 https://github.com/naver/mast3r (pushed 2025-06-30)
- S20 https://github.com/facebookresearch/vggt-omega (pushed 2026-09-22); https://huggingface.co/facebook/VGGT-Omega (2026-03-17)
- S21 https://github.com/facebookresearch/map-anything (v1.1.3 2026-07-17); https://huggingface.co/yyfz233/Pi3 (2025-07-14); https://github.com/UniFlowMatch/UFM (BSD-3, pushed 2026-04-04), https://arxiv.org/abs/2506.09278; https://arxiv.org/abs/2501.07556
- S22 https://github.com/cvg/LightGlue (pushed 2026-02-18; licence section)
- S24 https://docs.toonboom.com/help/storyboard-pro-20/storyboard/structure/about-scene.html
- S25 https://arxiv.org/abs/2509.06839 (2025-09-08); https://huggingface.co/joelseytre/toonout (2025-09-09); https://github.com/MatteoKartoon/BiRefNet
- S26 https://github.com/facebookresearch/sam3 (pushed 2026-10-07; SAM 3.1 news 2026-03-27); https://huggingface.co/facebook/sam3.1; https://arxiv.org/abs/2511.16719
- S27 https://arxiv.org/abs/2312.01943 (2023-12-04); https://huggingface.co/dreMaz/AnimeInstanceSegmentation; https://github.com/CartoonSegmentation/CartoonSegmentation (2025-04-29)
- S29 https://huggingface.co/Qwen/Qwen-Image-Layered (2025-12-17); https://arxiv.org/abs/2512.15603
- S30 https://github.com/shitagaki-lab/see-through (pushed 2026-10-05); https://arxiv.org/abs/2602.03749 (2026-02-03); https://huggingface.co/layerdifforg/seethroughv0.0.2_layerdiff3d
- S31 https://github.com/CyberAgentAILab/LayerD; S32 https://arxiv.org/abs/2603.14925 (2026-03-16), https://arxiv.org/abs/2607.27348
- S33 https://arxiv.org/abs/2501.08295; https://github.com/IamCreateAI/LayerAnimate (pushed 2025-08-22); https://github.com/YuxueYang1204/Layer-Curation-Pipeline (2025-08-22)
- S34 https://arxiv.org/abs/2411.16683, 2503.18033, 2512.21865, 2607.25802
- S35 https://huggingface.co/Lightricks/LTX-2.5-22b-IC-LoRA-Clean-Plate (2026-09-09); S36 https://huggingface.co/Lightricks/LTX-2.5-22b-IC-LoRA-Alpha-Gen (2026-09-28)
- S37 https://huggingface.co/Lightricks/LTX-2.3-22b-IC-LoRA-In-Outpainting (2026-06-17); .../LTX-2.3-22b-IC-LoRA-Union-Control (2026-03-05)
- S38 https://huggingface.co/Qwen/Qwen-Image-2.1 (2026-09-14); https://github.com/QwenLM/Qwen-Image-2.1 (pushed 2026-10-12); licence https://huggingface.co/Qwen/Qwen-Image-2.1/blob/main/LICENSE (2026-09-20); https://huggingface.co/Comfy-Org/Qwen-Image-2.1
- S39 https://huggingface.co/alibaba-pai/Qwen-Image-2.1-Fun-Controlnet-Union (2026-09-23)
- S40 https://huggingface.co/alibaba-pai/Qwen-Image-2512-Fun-Controlnet-Union (2026-01-12)
- S41 https://huggingface.co/alibaba-pai/Z-Image-Turbo-Fun-Controlnet-Union-2.1 (2025-12-11; v2602 2026-02-26); https://huggingface.co/Tongyi-MAI/Z-Image-Turbo
- S42 https://huggingface.co/alibaba-pai/FLUX.2-dev-Fun-Controlnet-Union (2025-12-01)
- S43 https://arxiv.org/abs/2604.24351 (2026-04-27); https://huggingface.co/DiffSynth-Studio/Template-KleinBase4B-ControlNet (2026-04-22), .../Template-KleinBase4B-Inpaint
- S44 https://huggingface.co/thedeoxen/refcontrol-FLUX.2-klein-4B-reference-depth-lora (2026-05-28)
- S45 https://arxiv.org/abs/2503.07598; https://huggingface.co/alibaba-pai/Wan2.2-VACE-Fun-A14B (2025-09-10)
- S46 https://github.com/zhenglinpan/AniLines-Anime-Lineart-Extractor; https://github.com/Mukosame/Anime2Sketch
- S47 https://github.com/ByteDance-Seed/Depth-Anything-3; https://huggingface.co/depth-anything/DA3-LARGE-1.1
- S48 https://github.com/microsoft/MoGe; https://huggingface.co/Ruicheng/moge-2-vitl-normal
- S49 https://github.com/prs-eth/Marigold; S50 https://github.com/cvg/DeepLSD, https://github.com/ant-research/scalelsd, https://github.com/cvg/GeoCalib
- S51 https://arxiv.org/abs/2403.18342, 2509.09501, 2211.05709, 2506.02733
- S52 https://huggingface.co/circlestone-labs/Anima (2026-01-29); https://huggingface.co/khanghy1000/Anima-ControlNet-VACE-Canny (2026-09-13); https://huggingface.co/Eugeoter/noob-sdxl-controlnet-lineart_anime (2024-11-11)
- S53 parent's contact sheets (2026-10-12 16:43), session scratchpad `neighbours/*-neighbours.jpg` (temporary)
- S54 repo: `plans/reports/spike-261012-1103-content-model-schema-v0-and-worker.md`, `spikes/film-analysis/film_analysis/gpu_inference.py`
- S55 https://huggingface.co/pixai-labs/pixai-tagger-v1.0 (2026-09-15)
- P1 `gh api -X GET search/issues -f q="repo:<r> anime"` (and "cartoon"), 13 repos, 2026-10-12. P2 https://github.com/huggingface/diffusers/tree/v0.41.0/src/diffusers/pipelines (v0.41.0 published 2026-10-06; Qwen-Image 2.1 PR #14804 merged 2026-09-18).

Status (agent): DONE_WITH_CONCERNS. Concerns: no tool in the three roles has anime-TV-frame validation; anime-specific numbers are informal single-author community measurements; several paper numbers come from page digests, not PDFs.
