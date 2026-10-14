> Saved by the parent session from the agent's returned text (the agent's harness forbade writing report files), 2026-10-13.

# Fast current recipe without training: Qwen-Image-2.1 painter + Wan2.2-VACE-Fun-A14B animator (2026-10-13)


Tags: [V] verified this pass (page, API, source or table read) / [S] secondhand (third-party card claim, Reddit, snippet) / [E] my arithmetic or inference / [L] measured locally (task brief, handoff 2026-10-13 10:40) / [prior] from reports 261012-1407, 261012-1639. WebFetch = small-model digest, numbers digest-level.
Effort: 2 WebSearch + 5 WebFetch (1 x 404) + ~95 API/script reads (HF, GitHub, arXiv, pullpush). Over the 40-60 guide if script reads count. No GPU, download, install, repo edit.

Terms (once): **NFE** = one forward pass of the big network; **CFG** = a second pass per step for the negative prompt (2 NFE per step); **step distillation** = a student trained to match a 40-50-step teacher in 4-8 steps, shipped as a small LoRA add-on or a merged checkpoint; **W8A8 / FP8 / NVFP4** = weights and activations stored in 8 / 4 bits so matmuls run ~2x / ~4x faster on Blackwell; **SVDQuant** = 4-bit with a small 16-bit side branch for outliers; **sparse attention** = skip most token pairs in attention; **KV cache (prefix)** = text + reference-image tokens computed once, reused every step; **RT-ratio** = GPU-seconds needed per film-second (real time needs RT-ratio <= u = 0.5-0.75); "short by Nx" = RT-ratio / u.

## 1 Decision + executive summary

**Decision: NO, not live.** Stacking every public lever lands 8-12x (action) / 4-6x (comedy) short of real time in the best case, and 37-55x / 17-25x short with levers that exist as downloads today [E, section 4]. What the levers do buy: the pre-pass of a 24-min action episode falls from ~356 GPU-hours to ~2.4-11 h (comedy 146 h to 1.2-5 h). Ship "pre-processed episode + cheap live tools"; do not plan a live generative path on this recipe.

1. **Painter:** official Qwen-Image-2.1-Turbo (8 steps, CFG 1, 2026-10-09) and an official extracted Turbo LoRA (rank 178, 0.91 GB) exist [V]. Nobody has shown that the base-trained outpaint LoRA survives Turbo [V negative]. Plate 170 s -> ~25 s (public-today) -> ~9 s (+ NVFP4 + ~4x fewer canvas tokens) [E]; budget 1.1-1.6 s (action) / 2-3 s (comedy).
2. **Animator:** 4-step Wan2.2 T2V LoRAs exist (lightx2v, rank 64) and the Fun-VACE maintainers say to use them, but "don't know how well they combine" [V]; nobody ships a distilled Wan2.2 VACE. **New: VDOT-14B** is a public 4-step VACE student (Wan2.1-VACE-14B, CVPR 2026) that reports outpaint parity with the 100-step teacher [V digest]. No community use, no anime evidence.
3. **Animator speed:** 54 s/frame -> ~2.2 (4 steps) -> ~1.0 (INT8/FP8 + SageAttention) -> ~0.59 (NVFP4 + sparse + LightVAE) -> ~0.11 s/frame at 424x240 [E]; budget 33-50 ms (action, 15 updates/s). NVFP4 + sparse exist only for T2V/I2V, not VACE [V].
4. **VAE floor:** after the transformer is fast, Wan VAE encode + decode (~10-16 s per 69-frame clip here [E]) is 20-30% of the time. LightVAE-Lite decode is 4.2x faster at -2.6 dB PSNR [V].
5. **Streaming 14B: nothing fits 12-16 GB.** Krea Realtime 14B W4A4 needs 22.8 GB and runs 2.81 fps on a 4090; no mask input [V]. No 14B causal model with VACE-style masks found [V negative].
6. **Biggest quality risks (evidence in section 4):** distillation vs far-content hold, colour and motion (animator); outpaint-LoRA transfer and anime line/flat-fill fidelity (painter); aggressive quantisation of an already distilled model gives motion artefacts.
7. **Do first:** T1 (Turbo LoRA + outpaint LoRA, ~1 h) and T2 (4-step LoRAs on the 17-frame smoke clip, ~2-3 h): section 5. They decide whether the "x25 free" claim holds.

## 2 Invariant + lever map

**Invariant:** real picture untouched; sides continue art style, scale, colour, composition; no seam; motion only where/when the picture moves; local only; this laptop (12 GB, 120 W, sm_120). **Currency:** RT-ratio (above). Budgets: update budget = u / rate: action 15/s -> 33-50 ms; comedy 6/s -> 83-125 ms; plate budget = u / plate rate: action 0.47 plates/s -> 1.1-1.6 s; comedy 0.25/s -> 2.0-3.0 s.

Levers used below (IDs): **P1** Turbo steps; **P2** INT8/FP8 + Sage-class attention + resident model phases; **P3** NVFP4; **P4** fewer canvas tokens. **A1** 4-step distillation; **A2** INT8/FP8 + SageAttention2; **A3** NVFP4 + sparse attention; **A4** LightVAE; **A5** half-resolution (424x240); **A6** fewer frames (drawing rate); **A7** one expert only; **A8** caching. Orthogonal (not counted): plate reuse across shots, skipping very short shots, scroll/routing (other agents).

## 3 Findings

### 3.1 Q1 Painter

| Option | Steps / speed evidence | Outpaint-LoRA transfer | Windows + sm_120 | Source quality |
|---|---|---|---|---|
| **Turbo checkpoint** (Qwen, 2026-10-09; 7.12B; Qwen Research licence) | 8 steps, CFG 1, prefix KV cache; own sigma list `1.0,.978453,.95418,.926626,.89508,.845148,.704534,.414568,0`; Euler/simple alone "didn't give clean results" [S nomadoor]; base uses 40 steps [V] | untested | ComfyUI 0.39: needs ManualSigmas + SamplerCustomAdvanced; diffusers needs PR #14950 [V]; Comfy-Org INT8-ConvRot 7.26 GB, bf16 14.2 GB | official card + LightX2V config (8 steps) [V] |
| **Turbo LoRA** (Comfy-Org, rank 178, 0.91 GB, extracted) | same 8 steps; "almost no difference from the full Turbo" [S nomadoor 2026-10-10]; "not fond ... with extracted lora it isn't that bad" [S] | stack on the same base the outpaint LoRA targets (sum of deltas), not co-trained | native | repo file list [V] |
| **PAI Fun-Acc-4Step** (2026-09-24, 0.35 GB, rank 64) | 4 NFE ("PDD"); card limits: small text degrades, "edits slightly blurrier and darker" [V] | untested | needs `qwenimage21_pdd.py` + `lora_utils_pdd.py` (diffusers/VideoX-Fun); ComfyUI only via community ports [S] | official card [V] |
| Community 4-step / 6-NFE LoRAs (viggle r64, RunningHub, Sutoonq) | not examined | untested | n/c | [S] names only |
| **NVFP4 SVDQuant Turbo** (catplusplus, 2026-10-12; DiT 4.4-4.9 GB; r128) | RTX 5060 Ti 16 GB: DiT 8 steps 1024x576 4.85 s; edit e2e 12.1 s; T2I 1024^2 13.7 s vs 39-42 s BF16-offloaded [S single source, card] | untested; LoRA on SVDQuant unverified | needs a Nunchaku class `transformer_qwenimage21` (not in README news); Nunchaku Windows wheels stop at torch 2.11, last release 2026-03-06, we run torch 2.14 [V] | single community card; tags Apache though base is Qwen Research |
| **FP8 + Sage (LightX2V)** | RTX 5090: I2I 1024^2 40 steps **6.8 s**, 2048^2 **42.8 s**, CFG off, FP8 f16-accum DiT + FP8 TE + SageAttn2 [V README] | n/a | Linux/Docker; Windows one-click exists, open RTX 50 bug #943 [V] | primary |
| ComfyUI `fp8_e4m3fn_fast` | dynamic VRAM (default) costs 1.08-1.16 vs 1.72 it/s on a 5070 Ti; fix `--disable-dynamic-vram`; open on 0.39.0 [V #16551] | n/a | affects us | primary issue |
| GGUF Q8_0 | do not use: broken on 2.1 (#487/#488), maintainer "Stop using GGUF" (2026-10-10) [V] | n/a | | |

Answers:
- **Outpaint LoRA on distilled?** No direct evidence exists (searched HF, Reddit, ausboss card: base-only, 25 steps, "INT8 Qwen Image 2.1") [V negative]. Indirect: klein precedent, base-trained LoRAs "do not produce good results" on distilled [prior V]; a Turbo-trained LoRA used "Turbo-sigma-adapted training" and degrades above strength 0.5 [S ysharma]; Edit-2511 FP8 + Lightning LoRA gave blocky artefacts while the baked checkpoint worked [S Reddit 2026-02-06]; stacked adapters on Turbo give "extra sharpening" [S comfyui-wiki]. Treat as unproven. Test base + Turbo LoRA + outpaint LoRA first; the full Turbo checkpoint second.
- **KV cache is already on:** ComfyUI node `QwenImage21Cache` defaults to `auto` (VRAM, then RAM) [V source]. No extra gain from "prefix caching" in our current ComfyUI path.
- **Text encoder:** it reads the reference image, so no cache across plates [E]. Offload/quantise: official `qwen3vl_8b_w4a8` 6.31 GB, INT8 9.35 GB, bf16 17.5 GB [V]. Our script also runs a 160-token caption pass on the 8B encoder and loads bf16 DiT + bf16 encoder [V comfy-plates.py]: hold the encoder in pinned RAM, run it once per plate, evict, then load the DiT.
- **Resolution vs time:** LightX2V 5090 I2I 1024^2 6.8 s vs 2048^2 42.8 s: 4x pixels = 6.3x time [V]. The ausboss LoRA trained at ~1-2 MP, v2 "everyday extensions at 1-2 MP" [V card]: below that is out of distribution.
- **Local floor:** the plate is ~9.6k tokens (sibling kongming report, ~2.4 MP [E]); 25 steps with KV cache = 3.4-5.8 PFLOP = 64-110 s at 100% of the measured bf16 peak [E]. So the 170 s is DiT-dominated and bf16-bound: the precision levers matter more than the reload overhead.

### 3.2 Q2 Animator

| Lever | Public artefact (date) | Works with VACE-Fun-A14B / Wan2.1-VACE-14B? | Evidence | Speed | Quality risk |
|---|---|---|---|---|---|
| **4-step LoRA** (A1) | lightx2v/Wan2.2-Distill-Loras: T2V-A14B high+low rank 64 "1217", 0.61 GB each (2025-12-17); Wan2.2-Lightning (2025-11-13) | backbone = official T2V (rounded) [prior V]. VideoX-Fun maintainer: weight 1, CFG 1, "VACE base is T2V ... I don't know how well the two combine" [V #329 2025-09-18, translated]. LightX2V runs VACE-Fun (40 steps, CFG 3.5, sage_attn2) but never shipped a distilled VACE [V #844, #73, #382 open] | Reddit 2025-09: "tested 30 steps vs distill loras, pretty similar" [S]; "tried Lightning and LightX2v, got nothing good", used reward LoRAs 4+4 steps [S]; video extend "smooth but almost no prompt adherence" [S]; Phr00t MEGA v12 = bf16 Fun-VACE base + rCM/lightx2v accelerators, 4 steps CFG 1, 1,677 likes, built single-expert [S card]; same card: too much Lightning "hurt motion", I2V zooms, colour shifts/noise at start [S] | x12-25 | **high** |
| **VDOT-14B** (A1 alt) | arXiv 2512.06802 (v3 2025-12-22, CVPR 2026); HF yutongwang1012/VDOT 34.7 GB bf16 (2026-03-13), fp8 17.3 GB (rdp2, 2026-05-14); Apache tag | own 4-step student of **Wan2.1**-VACE-14B (not Fun-A14B), single model, no expert swap | outpaint VBench-norm 76.54 vs 75.49 (teacher 100 steps), user study 4.62 vs 4.57; extension 80.53 vs 77.10, user 4.36 vs 4.52; trained at 832x480, 4 x H200 [V digest]; no wall-clock times, no limitations section; repo 20 stars, 3 issues, no ComfyUI port or Reddit use [V negative]; script defaults 50 steps / guide 5.0 [V] | x25 | **high** (unvalidated; not anime) |
| VDOT++ (2610.03221, 2026-10-02) | weights "before mid-October" [V README 2026-10-05]; none found 2026-10-13 | | | | |
| TurboDiffusion (rCM + SLA) | thu-ml (push 2026-08-27): TurboWan2.1-T2V-14B, TurboWan2.2-I2V-A14B-720P only | **no** Wan2.2 T2V-A14B, no VACE [V] | | | |
| **LightWan2.2-A14B** NVFP4 + sparse QAT (A3) | lightx2v (2026-05-28, mod 07-27), 8.38 GB/expert, T2V + I2V, Blackwell only | **no VACE**; attaching the Fun module untested | 5090: 480p 4 NFE **9.1 s vs 734 s** (80.7x), 720p 22.5 vs 2668 s [V card] | x4 per NFE over bf16 [E from card] | medium-high |
| NVFP4 by own conversion | `convert_to_quant --nvfp4 --wan` (InsecureErasure, 2026-06-05): distilled I2V on an RTX 5060, sampling "<60 s" for 81 frames; "abrupt camera movements or fast subject motion may produce artifacts"; 4 h per expert [S card] | would need our own conversion with `vace_blocks.*.(before|after)_proj` kept unquantised | | x1.7-2 over FP8+Sage [E] | medium-high |
| **INT8-ConvRot Fun-VACE** (A2) | FartingBackwards (2026-07-25), 17.8 GB/expert; proj layers left unquantised because quantisation noise can silence VACE [V card] | yes (is Fun-VACE) | speed unmeasured; native ComfyUI W8A8 | x2.0-2.3 with Sage [E] | low-medium |
| **SageAttention2** (A2) | woct0rdho Windows wheels incl. **sm_120** (post7, 2026-10-09); ComfyUI `--use-ck-attention` (comfy-kitchen) [V]; "5090: 560 T, 2.7x FlashAttention2" [V README] | n/a | earlier: only KJNodes `auto` ran on sm_120 [prior V] | attention x2.5 [E] | low-med |
| SageAttention3 FP4 attn | thu-ml `sageattention3_blackwell` (2025-09-27); Windows unverified | | | | |
| Sparse attention (A3) | Radial (push 2025-11-11; Wan2.1-14B 1.8x at default length, lightx2v LoRA support, no VACE) [V]; SVG/SVG2 training-free (push 2026-07-04); SLA needs fine-tuning = training | VACE unverified | BLADE abstract: training-free sparse + step distillation "yields suboptimal results" [V 2508.10774] | <=1.2-1.3x at 28k tokens [E] | med-high |
| Caching (A8) | MagCache supports VACE 1.3B/14B and Wan2.2 (1.5-2x at 40-50 steps) [V README, push 2025-11-17] | yes | nothing to skip at 4 steps [E] | only for an undistilled fallback | medium (PSNR loss) |
| One expert only (A7) | MEGA merges are single low-noise based [S]; high-noise "not needed for VACE" [S prior] | | no FLOP saving; saves swaps + RAM | x1.05-1.1 | low-med |
| LightVAE (A4) | ModelTC/LightVAE (repo 2026-10-10, HF mod 2026-10-12): Wan2.1 decoder 30.9 FPS -> Lite **129.9 FPS** (5090), PSNR 33.5 -> 30.9 (UCF-101) [V table image] | Wan2.2 14B uses the Wan2.1 VAE [V]; encoder not accelerated | | decode x4.2 | low-med |

Distillation failure modes (generic): "oversaturation, temporal inconsistency, and mode collapse" [V 2603.21864]; DMD students drift toward near-static output [prior V]. For us: oversaturation = colour/haze criterion; collapse = sides that stop following the picture.

### 3.3 Q3 Streaming 14B

| Model | Evidence | Fits 12-16 GB? | Mask input? |
|---|---|---|---|
| Krea Realtime 14B (Wan2.1 14B + Self-Forcing, Apache tag, 2025-10-08) | 11 fps vendor, B200; independent: H100 eager bf16 **5.71 fps, 49.6 GB peak** (window 3, 4 steps); fp8 weights 17.4 GB [V bench 2026-07-24] | no | no (v2v strength) [prior V] |
| Krea 14B W4A4 SVDQuant (2026-07-24, CC-BY-NC-SA tag) | **2.81 fps, 22.78 GB of 24 GB** on a 4090 [V card] | no (needs 24 GB) | no |
| Krea 14B + Adapting-VACE inpaint | 12.5 FPS, 45.1 GB H100 at 320x576 [prior] | no | yes, but H100-only |
| Any other 14B causal + VACE masks | none found (HF "causal vace", "self-forcing vace": 0 hits; arXiv sweep) [V negative] | - | - |

Laptop estimate if it ever fit: ~1 fps bf16 (H100 5.71 / 5) [E]. Answer: **nothing runs on 12-16 GB**; the only mask-capable real-time model remains the 1.3B LongLive + VACE hack [prior].

### 3.4 Q4 Since 2026-10-12 (HF newest + arXiv title sweeps, 2026-10-13; limited)

- Nothing changes the verdict. Qwen-Image-2.1 ecosystem moved (NVFP4 Turbo card 10-12, Comfy-Org README 10-12, Turbo card edit 10-12, lightx2v prompt-enhancer GGUFs 10-12 [V]). LightVAE (10-10/12). VDOT++ weights still "coming" [V]. No new distilled Wan2.2 VACE; no new mask-capable fast video model found.
- Older but new to our notes (not in the 10-12 report): **VDOT-14B** (2026-03), **LightWan2.2 NVFP4+sparse** (2026-05), **Wan2.2 T2V 4-step LoRA 1217** (2025-12), **LightX2V Wan2.2-VACE-Fun runner** (2026-01), **Krea W4A4** (2026-07).

## 4 Laptop speed tables (current -> each lever -> stacked)

**Assumptions [E unless tagged]:** laptop = 5090 / 3.6 (+-30%) [prior]; burst clocks, apply x0.8 for 120 W sustained [prior X4]; two screens (3 screens ~x2). **Animator baseline** 54 s/frame [L] = ~100 NFE x ~37 s (CFG on, 848x480x69f incl. VACE); cross-check: LightX2V Diffusers Wan2.1-14B 480p 81f = 9.77 s/it on an H100, 30.5 s/it on a 4090D, an "it" being 2 NFE [V README], which scales to ~23 s per NFE here; the 37 s is within the ratio's error. If CFG is currently off (50 NFE x 74 s), A1 is x12.5, not x25, and every animator row below doubles. **Painter baseline** 170 s [L], bf16 DiT + bf16 encoder + caption pass, ~9.6k-token canvas, DiT-dominated. **VAE** enc+dec ~10-16 s per clip = 0.18 s/frame [E, unmeasured].

### 4.1 Painter (s per plate; budget 1.1-1.6 action / 2-3 comedy)

| Row | Lever | Factor | s/plate | RT action / short by | RT comedy / short by | Quality risk and evidence |
|---|---|---|---|---|---|---|
| R0 | today | - | 170 [L] | 80 / 107-160x | 42 / 57-85x | - |
| R1 | P1 Turbo 8 steps (LoRA or full) | x1.7-2.5 | 68-100 | 32-47 / 43-95x | 17-25 / 23-50x | **medium-high:** outpaint-LoRA transfer unproven; PDD 4-step admits blur/darker; anime unreported; Reddit split ("small loss" vs "not fond") [S] |
| R2 | + P2 INT8/FP8-fast + Sage-class attention + resident phases, caption cached | x2.2-2.6 more | ~25 (20-28) | 12 / 16-24x | 6 / 8-13x | **low-medium:** LoRA on quantised base can show artefacts [S]; dynamic-VRAM FP8 loss (#16551); no GGUF; `lora key not loaded` warnings must be zero [V #16944] |
| R3 | + P3 NVFP4 SVDQuant (rank 128) | x1.2-1.4 | ~21 (17-23) | 10 / 13-20x | 5 / 7-11x | **medium:** photoreal tests only; faces smear at rank 32 [S card]; no anime; Windows build gap |
| R4 | + P4 canvas ~4x fewer tokens (foveated / half-res) | x2.2-2.5 | ~9 (7-11) | 4.2 / 5.6-8.5x | 2.3 / 3-4.5x | **low-medium:** LoRA trained at 1-2 MP [V]; far detail lost |

### 4.2 Animator (s per generated frame, 848x480 unless stated; budget 33-50 ms action, 83-125 ms comedy)

| Row | Lever (cumulative) | Factor | s/frame | Clip (69f) | Short by: action | comedy | Quality risk and evidence |
|---|---|---|---|---|---|---|---|
| R0 | today (50 steps, switch 32, Q8_0, SDPA) | - | 54 [L] | 62 min | 1,080-1,620x | 430-650x | - |
| - | steps 50 -> 20, no LoRA | x2.5 | 22 | 25 min | - | - | low-medium [S Reddit "sweet spot ~20"] |
| R1 | A1 4 steps, CFG 1, 2+2 (LoRA pair) or VDOT-14B | x25 (x12.5 if CFG off) | 2.2 | 150 s | 43-65x | 17-26x | **high:** colour shift/oversaturation, motion collapse, weak far-content hold; mixed community results [S]; maintainers unsure [V]; VDOT unvalidated |
| R2 | + A2 INT8/FP8 W8A8 + SageAttn2 (no GGUF dequant) | x2.0-2.3 | 1.0 | 72 s | 21-31x | 8-13x | **low-medium:** keep VACE proj layers unquantised [V]; Sage on sm_120 `auto` only [prior]; INT8 vs Q8_0 fidelity unmeasured |
| R3 | + A3 NVFP4 + sparse (needs own VACE conversion) | x1.7-1.9 | 0.66 | 45 s | 13-20x | 5-8x | **medium-high:** distilled + NVFP4 = "abrupt/fast motion artifacts" [V card]; BLADE: training-free sparse + distillation suboptimal [V]; no public VACE build |
| R4 | + A4 LightVAE-Lite decode | x1.1 | 0.59 | 41 s | 12-18x | 5-7x | low-medium: PSNR -2.6 dB [V] |
| R5 | + A5 424x240 (tokens /4, attention /16; VAE /4) | x5 | **0.11** | 8 s | 2.3-3.4x | 0.9-1.4x | **medium-high:** VACE buckets 512-1024 [prior], 14B at 424x240 untested; line weight softens |
| R5' | public-today path at half-res (R2 + A5) | x5 | 0.20 | 14 s | 4-6x | 1.6-2.4x | as R1 + R2 + A5 |
| - | A6 generate at the drawing rate (15 fps not 24) | x1.6 per film-second | in "short by" via update rate | | | | unknown VACE behaviour at non-16 fps |

### 4.3 Whole episode (24 min; GPU-hours; painter + animator; no plate reuse)

Workload [L/user]: action 679 plates + 21,600 side updates (15/s); comedy 357 plates + 8,640 updates (6/s). Budget = 0.4 h x u.

| Scenario | Painter s/plate | Animator s/frame | Action: GPU-h, RT, short by | Comedy: GPU-h, RT, short by |
|---|---|---|---|---|
| Today | 170 | 54 | 356 h, 890, 1,190-1,780x | 146 h, 366, 490-730x |
| A public-today (R2 + R2; plate canvas and 848x480 unchanged) | 25 | 1.04 | 11 h, 27, 37-55x | 5.0 h, 12, 17-25x |
| B + half-res everywhere | 12 | 0.20 | 3.5 h, 8.7, 12-17x | 1.7 h, 4.2, 5.6-8.3x |
| C best (R4 + R5: NVFP4, sparse, LightVAE, half-res) | 9 | 0.11 | 2.4 h, 5.9, **8-12x** | 1.2 h, 2.9, **4-6x** |
| C + plates halved by reuse/skip (orthogonal) | 9 | 0.11 | ~1.9 h, 4.7, 6-9x | ~0.9 h, 2.3, 3-5x |

Reading: total speed-up = 150-300x; needed = 1,200-1,800x (action) / 490-730x (comedy). The painter is now as big a cost as the animator in scenario C. Scenario C is speculative: it needs (a) a VACE model that survives 4 steps, (b) an NVFP4 + sparse VACE build that nobody published, (c) a 14B that behaves at 424x240. Without (b), the best-evidenced path is B: 12-17x / 6-8x short. 3-screen width roughly doubles every row.

Adoption risk and fit (ranked):
1. **Turbo LoRA + resident INT8/FP8 phases in ComfyUI 0.39** (R1-R2): official files, ComfyUI-native, low maintenance; risk = outpaint-LoRA transfer (T1).
2. **4-step LoRA pair on Fun-VACE-A14B INT8-ConvRot** (R1-R2 animator): same runner, no new code; risk = quality (T2).
3. **VDOT-14B** as a second animator: Apache, single model, outpaint-evaluated; risk = a 20-star academic repo, `.pt` pickle, 4-GPU default, Wan2.1 base (T3).
4. **NVFP4 / Nunchaku / LightX2V Docker / sparse attention:** after T1-T3 pass; Windows + torch 2.14 build gaps, conversion 4 h per expert.
Not recommended: Krea or any streaming 14B, TurboDiffusion/SLA (training), MagCache once distilled.

## 5 What to try first (cheap, each changes the decision)

- **T1 (~1 h, 1 GB download):** on the 5 jjk plates run base INT8 + `qwen_image_2.1_turbo_lora_avg_rank_178_bf16` + ausboss v2 (strength 1.0 and 0.7) with ManualSigmas (8 values above), then the full Turbo checkpoint. Compare with the 25-step plate: kept-area PSNR (~33.6 dB card baseline), gray left <= 1%, seam factor, by-eye line weight / flat fill / haze; also time with `--disable-dynamic-vram` on/off and check the log for "lora key not loaded". Pass: seam and colour within the current spread -> P1 is real.
- **T2 (~2-3 h, 1.2 GB):** the 17-frame smoke clip (baseline 549 + 309 s) with the lightx2v T2V 4-step LoRA pair (strength 1.0, CFG 1, 2 + 2 steps), then the INT8-ConvRot Fun-VACE pair. Measure s, far-content hold on held-dialogue, colour drift and the motion number (sides' change per frame vs picture). Pass: hold + colour survive -> animator x25 for free; fail -> only VDOT remains.
- **T3 (~half day, 17-35 GB):** VDOT-14B (fp8 17.3 GB or bf16 34.7 GB) with `--offload_model` in its repo (or converted) on the same clip; steps/CFG as the paper states (script defaults are 50 / 5.0). Pass: >= T2 quality at lower cost -> adopt as the 4-step animator.
- **T4 (~30 min):** time VAE encode + decode of 69 frames at 848x480 in the current ComfyUI, and one sustained 10-min FP8-fast matmul log at 120 W. Decides LightVAE and the x0.8 derate.
- **Only if T1-T3 pass:** NVFP4 conversion of Fun-VACE (`convert_to_quant`, exclude `vace_blocks.*.(before|after)_proj`), `--use-ck-attention`.

## 6 Sources (read 2026-10-13; date = release / last change)

Painter: Qwen-Image-2.1-Turbo card https://huggingface.co/Qwen/Qwen-Image-2.1-Turbo (2026-10-09, mod 10-12) · QwenLM README https://github.com/QwenLM/Qwen-Image-2.1 (2026-10-09) · Comfy-Org repack + README (turbo files, LoRA; 2026-10-12) https://huggingface.co/Comfy-Org/Qwen-Image-2.1 · HF discussions: Comfy-Org #22 (2026-10-11), #11, #20; Turbo #4, #7 · comfyui-wiki https://comfyui-wiki.com/news/2026-10-09-qwen-image-2-1-turbo · PAI Fun-Acc LoRAs https://huggingface.co/alibaba-pai/Qwen-Image-2.1-Fun-Acc-LoRAs (2026-09-24) · ausboss outpaint LoRA (2026-09-26) · LightX2V Qwen-Image-2.1 README + configs https://github.com/ModelTC/LightX2V/tree/main/scripts/qwen_image_21 (push 2026-10-12) · ComfyUI `comfy_extras/nodes_qwen.py` (QwenImage21Cache), issues #16551, #14618, #16944, #16435, v0.39.0 (2026-10-05) · catplusplus NVFP4 Turbo https://huggingface.co/catplusplus/nunchaku-qwen-image-2.1-turbo (2026-10-12) · BlazeMCworld INT4 (2026-09-23) · Nunchaku releases (last 2026-03-06) · Qwen-Image-Flash 2606.03746 · Reddit via pullpush: 1x1sy3d, 1x2e4bl, 1x2csbl, 1x1t7jd, 1x2v049 (2026-10-09..11), 1qx8awl (2026-02-06), 1wqlk4u (2026-09-26).
Animator: lightx2v/Wan2.2-Distill-Loras (2025-12-17), Wan2.2-Lightning, **LightWan2.2-A14B** https://huggingface.co/lightx2v/Wan2.2-NVFP4-Sparse (2026-05-28) · LightX2V README (tables 2025-12-01, push 2026-10-12), configs `wan22_vace`, issues #73, #382, #575, #844, #943 · VideoX-Fun #329 (2025-09-18), #414 · **VDOT** arXiv 2512.06802 https://arxiv.org/abs/2512.06802, HF yutongwang1012/VDOT (2026-03-13), rdp2/vdot_14b_fp8, https://github.com/hhhh1138/VDOT (push 2026-10-05), VDOT++ 2610.03221 · Phr00t/WAN2.2-14B-Rapid-AllInOne (mod 2026-04-06) · InsecureErasure NVFP4 (2026-06-05) · FartingBackwards INT8-ConvRot (2026-07-25) · TurboDiffusion (push 2026-08-27) · Radial attention (2025-11-11) · Sparse-VideoGen (2026-07-04) · SLA (2026-02-24) · BLADE 2508.10774 · Adaptive Video Distillation 2603.21864 (2026-03-23) · MagCache (2025-11-17) · SageAttention (2026-01-17), woct0rdho wheels (2026-10-09), comfy-kitchen (2026-10-12) · LightVAE https://github.com/ModelTC/LightVAE (2026-10-10; table1.png read) · Reddit 1nf05fe, 1nfgyxp, 1npsfs8 (2025-09) · lilting.ch RTX 4060 14B Rapid: 480x480x33f, 4 steps, 111 s (undated) [S].
Streaming: krea/krea-realtime-video (mod 2026-09-08); sztlink/krea-realtime-bench (2026-07-24); felipesztutman/krea-realtime-video-14b-w4a4 (2026-07-24).
Local/prior: plans/handoffs/ai-surround-moving-sides-20261013-1040.md; plans/reports/kongming-261013-1049-real-time-architecture-counsel.md; spikes/ai-surround/comfy-plates.py; reports 261012-1407 (real-time models) and 261012-1639 (painters/runners).

## 7 Unresolved questions and limits

1. Does the ausboss LoRA keep registration on Turbo (8 sigmas) or with the Turbo LoRA? No source tests it (T1).
2. Do 4-step T2V LoRAs preserve far-content hold, colour and picture-following motion on Fun-VACE keyed by a plate? Community is split; maintainers unsure (T2).
3. VDOT-14B on this laptop: offload path, `.pt` to ComfyUI conversion, actual steps/CFG, anime behaviour. Digest-level numbers (not re-checked in the PDF).
4. Is today's animator on CFG (100 NFE)? Decides x25 vs x12.5.
5. Real VAE encode + decode time; FP8-fast and INT8-ConvRot matmul rates on sm_120; sustained clocks at 120 W.
6. Plate canvas size and the non-DiT share of the 170 s (caption, reloads): assumed from the sibling report.
7. NVFP4 for VACE (proj layers, ComfyUI layout), Nunchaku on torch 2.14, Radial/SVG with VACE at 4 steps: unverified.
8. VDOT++ weights (promised mid-October); licences (Qwen Research; Krea W4A4 CC-BY-NC-SA; community NVFP4 tagged Apache over a Qwen Research base).
9. Not covered: TensorRT/torchao for Wan on sm_120 (nothing found), SageAttention3 on Windows, LTX-2.5, 3-screen width, thermal, quality at 424x240. "x" factors are +-1.5x; absolute seconds +-2x.
