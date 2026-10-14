---
phase: 3
title: "Painter at speed: set atlas, sides-only plates"
status: in-progress
priority: P1
effort: "2-3 d + 1-2 overnight local trainings"
dependencies: [1, 2]
---

# Phase 3: Painter at speed

## Progress (2026-10-14, night)

- **Resident painter built** (step 1): `painter-service.py` + `painter_core.py` run ComfyUI 0.39's own model code in-process (ComfyUI's embedded Python), no server. The worker talks to the service through `live/painter.py` (which replaces `paint_plate.py`).
  - Models load once, completely, then ComfyUI's loader is frozen: a reload is an error. Cache emptying is off, the allocator is capped, and a warm-up plate reserves the pool. Everything runs in inference mode.
  - Without the freeze, ComfyUI unloaded part of the DiT at every plate and spilled into shared memory (56 s per plate).
- **Merged, quantized painter** (`painter-quantize.py`): Turbo LoRA and outpaint LoRA v2 merged in fp32 with ComfyUI's LoRA code, then quantized with Comfy-Org's own formats. Weight error per layer: W4A8 7.3 %, W6A8 2.1 %.
- **13 hidden-sides scenes, read-full** (`painter-bench.py`, run `out/runs/painter-bench/20261013-234638-q2-dits`):

  | Painter | Near / far / seam | Warm s/plate (sampling) | Weights + peak VRAM |
  |---|---|---|---|
  | official INT8 + LoRAs | 10.32 / 27.00 / 1.33 | 7.5 (6.65) | 7.6 + 1.5 GB |
  | merged W6A8 | 10.05 / 25.74 / 1.32 | 8.2 (7.37) | 6.4 + 1.7 GB |
  | **merged W4A8 (chosen: VRAM)** | 10.20 / 26.06 / 1.58 | 8.2 (7.35) | 4.7 + 1.7 GB |
  | 25-step plate (today's best) | 10.35 / 27.10 / 1.31 | ~180 | |

  - By eye (agent, 2 sheets) the three look alike and all keep the plate's identity guess (blood stain turned scarf).
  - Peaks per stage (W4A8): VAE encode +0.86, sampling +1.72 (incl. the int8 text + reference K/V cache), decode +1.30 GB. The whole painter stays under ~6.4 GB.
- **Kernels at ~1 MP, per step** (`painter-kernels-bench.py`):
  - Linears: bf16 1058 ms, INT8 390, W4A8 379, NVFP4 282.
  - Attention: SDPA 296 ms, comfy_kitchen INT8 146 (0.8 % error).
  - A 7B DiT at 1 MP is near this GPU's limit. Without training, INT8 attention plus NVFP4 linears could reach about 6 s per plate.
- **The 8B encoder leaves the live path.** On the CPU it would take 18-33 s per read, but the plate does not need a per-canvas read at all. Run `20261014-002308-q1-reads-w4a8`, W4A8, 13 scenes:

  | Encoder read | Near / far / seam | Change vs the full read |
  |---|---|---|
  | full canvas (X2) | 10.20 / 26.06 / 1.58 | |
  | thumbnails 448 / 256 / 128 px | 10.22-10.48 / 26.36-27.36 / 1.58-1.59 | 1.7-4.6 codes |
  | **one fixed all-grey canvas** | 10.44 / 26.43 / 1.53 | 4.4 codes |
  | one fixed JJK canvas | 10.32 / 26.02 / 1.58 | 4.0 codes |

  - By eye (agent, action and close-up sheets) all reads give the same plate.
  - What matters is the image slot in the conditioning (the instruction read after an image). X2's text-only variant had no slot, which is why its sides came back grey.
  - The live painter therefore reads one conditioning file, `out/live/painter/cond-proxy-grey.pt`, encoded once. No encoder weights, no encoder time and no swap remain.
- **E1's painter cell with the resident painter** (`out/e1/20261014-011121`; the user away, screens unattended as in phase 2):

  | Painter cell | 240 Hz wrong / missed per min | 96 Hz wrong / missed per min |
  |---|---|---|
  | none (today) | 0.00 / 0.51 | 0.00 / 0.00 |
  | **resident W4A8** | **8.17 / 40.85** | **3.06 / 5.62** |
  | ComfyUI, capped (phase 2) | 39.8 / 294.7 | 40.9 / 105.2 |
  | ComfyUI, uncapped (E1) | 23.0 / 153.2 | 18.4 / 52.6 |

  - Keeping the painter resident cut the harm 3-13x, but it is not yet at the no-load level.
  - The misses come in runs (one 62 ms gap; the player's own GPU render once took 53 ms, p50 1.06 ms).
  - Kernel profile of one plate (`painter-kernel-profile.py`): attention 288 x ~9.7 ms (max 13), INT8 GEMMs at most 2.5 ms, VAE convolutions at most 6 ms. These kernels are no longer than E1's harmless synthetic ones.
  - The difference: E1's loads kept one call in flight, while the painter queues hundreds ahead.
  - Throttled queue (`--gpu-queue layer`: wait after each attention, MLP and VAE convolution; ~0.05 s a plate), `out/e1/e1-painter-gpuqueue_layer.log`: **5.11 / 8.17 at 240 Hz, 0.00 / 0.00 at 96 Hz.** It is now the live default.
  - The painter at a below-normal GPU class (D3DKMT) was worse (19.9 / 29.1 alone; 4.6 / 17.4 with the throttle). Those two cells overlapped another session's CPU-heavy build, though, so the priority result is not settled.
  - Left at 240 Hz: the ~10-13 ms attention kernels are longer than a refresh. INT8 attention halves them, and `--attn-chunk N` (`painter_attention.py`) splits them into kernels of at most N queries with the same wait.
- **Speed levers without training** (`painter-bench.py --compare`, 13 scenes, W4A8 + grey proxy; run `compare-20261014-021700-levers`):

  | Lever | Near / far / seam (median) | Change vs baseline | s/plate |
  |---|---|---|---|
  | baseline | 10.44 / 26.43 / 1.18 | | 8.11 |
  | INT8 attention (comfy_kitchen) | 10.32 / 26.42 / 1.16 | 1.7 codes | 6.81 |
  | 6 steps (the Turbo schedule sub-sampled) | 10.29 / 26.12 / 1.19 | 2.5 codes | 6.51 |
  | half-size reference latent | 17.72 / 29.99 / 3.92 | 14.5 codes | 6.44: **rejected** (the seam breaks) |
  | 0.74 MP canvas | 10.68 / 25.86 / 1.24 | 14.5 codes (other content, softer) | 5.64: held for the user's eye |
  | NVFP4 MLP | | | not measured: the quantizer crashed while allocating the bf16 model (access violation under memory pressure from concurrent jobs); retry later |

- **Live recipe** (the defaults in `live/painter.py`): W4A8 merged DiT, the grey proxy conditioning, INT8 attention, 6 steps, ~1 MP, throttled queue.
  - Run `compare-20261014-022847-recipe`: near 10.26, far 26.09, seam median 1.16 (the 25-step plate: 10.35 / 27.10 / 1.31).
  - **5.48 s a plate**; 5.78 s with attention split into kernels of ≤ 2048 queries. Chunking gives identical plates.
  - Against the two-speed target at T = 3 s: met at u 0.85 (6.1 s), short at u 0.75 (5.0 s); the 0.74 MP canvas would meet both (~4.4 s).
- **Identity references** (`painter-identity-test.py`, jjk-one-side, the character's crop from shot 500):
  - Grey proxies with the crop as a second reference: no change (the scarf stays).
  - The encoder reading canvas and crop: a bloodied collar instead of the scarf (far error 31.3 → 28.4).
  - A tiny grey canvas plus the crop read: best far error (28.0), scarf still there.
  - One seed per variant: **inconclusive**. The live code is in and off by default (`identity_refs`; crops from phase 4's `identity/clusters.json`).
- **Set atlas** (step 2) and the scheduler (step 3) are built: `live/set_atlas.py`, `live/worker_sets.py`, sets in `live/worker_state.py`, `live/painter_jobs.py` (per set, long sets first), `live-worker.py`.
  - `live-sets-selftest.py` passes: similarity within 0.4 px, pixels agree to 1 code, frame transforms within 0.2 px.
  - Headless end to end on JJK (`live-smoke.py --fake-player`, the stand-in `live-fake-player.py`; `out/live/smoke/20261014-005905-e2e-sets-painter`):
    - 240 s from 300 s with a seek, fresh cache: 316 shots, 24 joined sets (reuse ~1.08; X5 1.10), 24 plates at 8.0-8.7 s.
    - Film time at the showings' starts: plate 32 %, T1 39 %, glow 29 % (cold start, no head start, no small painter yet).
  - Re-watch on that cache: 314 shots in 295 sets and 157 canvases from the cache, none analysed again; plates 58 % of film time.
  - Store, analysis and set self-tests pass.
  - Handed to phase 4 at 01:10, which has since moved the frame record to a homography (store schema 2).
- **E1 with the final recipe** (`out/e1/final-recipe-cells.log`, 02:39-02:56):

  | Cell | 240 Hz wrong / missed per min | 96 Hz |
  |---|---|---|
  | no load | 1.02 / 2.55 | 0 / 0 |
  | live recipe, throttled queue | 3.06 / 6.13 | 0 / 0 |
  | **+ attention in kernels of ≤ 2048 queries (live default now)** | **2.04 / 1.02** | **0 / 0** |

  - At the no-load level: 4 wrong frames in 2 min against 2. Plates are identical and paint about 5 % slower (5.78 s).
  - **u measured:** with the player running at 240 + 96 Hz, the painter keeps 0.75-0.79 of its alone speed in every E1 cell, throttle and chunking included.
  - The live service is slower than the bench on the same canvases, alone (sampling 6.3 against 4.9 s). Suspect: the 7 GB allocator cap. `out/runs/painter-bench/after-window.sh` measures caps 7 / 8 / 10 with the allocator's retry counts.
- **Set atlas by eye on 10 cuts** (`set-atlas-cuts-check.py`, `out/live/set-cuts/20261014-025704-set-cuts`):
  - Geometry holds: walls, floors and scrolls continue at the seams.
  - 2 of 6 sets are good (the gallery's shot and reverse shot).
  - 4 sets show a mismatch caused by sharing: another shot's character in the sides; a 1-frame smoke flash's plate beside a clear shot; a fade's black plate on a lit shot; a dark side where the depths differ.
  - Fix, sets-v2: a join also needs the two shots' mosaics to agree where both saw the place (`set_atlas.agreement`), and no character cut by a side edge in either shot (`worker_sets.edge_person`). Re-check on a fresh run is pending.
  - The fade's black mosaic is a mosaic-vote bug. Phase 4's session is fixing `mosaic_t1` and `analyse_v0`.
  - Plates of 3D or curved camera moves also show staircase fragments from kept frames that do not line up. This is open: seam-cut compositing in the mosaic.
- **Late night (04:10-05:00):**
  - **Live recipe: 5 steps (5.20 s).** Scores near 10.16 / far 25.67 / seam median 1.17; plates within 1.3 codes of 6 steps. The swap test is rebuilt on these plates: `out/demo/20261014-044008-swap-live-w4a8-ckint8-turbo5`.
  - The allocator cap (7 / 8 / 10 GB) changes nothing. E1's slow load plates were 8-step plates: `e1-painter-load.py` sent no schedule. Fixed.
  - **Identity references help across 4 seeds:** far error -9 % with ref-read, -7 % with ref-mixed, short hair kept.
    - A CPU read of a crop takes ~25 s and 9 GB, and paints the same plate as the GPU read.
    - The live path (`live/identity_encoder.py`, `--identity-refs`) is in, off until phase 4's re-clustered crops exist.
  - **sets-v2 adds a plate-band check:** a set's painted plate must agree with the joining shot's own picture along its side edges.
    - On the old run, sets-v2 keeps 2 of 28 joins; in the fresh e2e run, 3 of 16 matches.
    - Reuse is about 1.01, so the budget assumes none: the target is ~4.7 s a plate at T = 3 s and u 0.77.
  - **Fresh e2e** with phase 4's mosaic v0.5 (`out/live/smoke/20261014-044028-e2e-sets-v2`):
    - Film time: glow 60 %, structure 39 %, plate 2 %. T1 mosaics are mostly gated to glow (28 % of shots disagree above 0.10).
    - 28 plates at 5.8 s.
  - **Gallery e2e with v0.7** (340-640 s): structure 74 %, plate 5 %, glow 21 %; 49 plates at 5.4 s. The fade set now gets a lit plate. Phase 4's v0.8 then made the picture region the representative lit frame (a smoke-then-clear shot had kept its smoke).
  - **Identity e2e** (1150-1450 s, phase 4's 176 clusters): crops read at 22-31 s each on the CPU, but only 1 of 23 plates had its crop in time.
    - The encoder now reads ahead the 24 clusters cut most often at the side edges, and a plate's own request jumps the queue.
    - Identity stays off by default: two top clusters still mix characters.

## Progress (2026-10-14, morning)

- **Swap tests (user, 09:16-09:26): both fail.**
  - The 25-step plate was picked 14 of 20 times against 5 steps (p 0.058) and 16 of 20 against 4 steps (p 0.006); the pass is at most 12. Shams: 1 of 2 each.
  - Per scene, over both tests: the 25-step plate every time in action (4/4), jjk-one-side (4/4), edge-head-closeup and jjk-edge-move (3/3); the live plate every time in dark and jjk-still-edge-person (3/3).
  - The user's cues: what was painted, and the join line. Scores: `result.json` in both test folders.
- **The 1 MP canvas is the cause** (fine detail = mean |Laplacian| over the swap test's side regions, 13 scenes; join = side over picture detail in 48 px bands at the seams):

  | Plates | Side detail | Join | s/plate |
  |---|---|---|---|
  | truth | 4.3 | | |
  | 25-step plate (~2 MP) | 4.7 | 1.14 | ~180 |
  | live recipe at ~1 MP: 6, 5 or 4 steps | 1.9 | 0.56 | 5.8 / 5.2 / 4.4 |
  | the 25-step plate through 1344×756 and back | 2.1 | | |
  | **live recipe on the 25-step canvas (1920×1088), 5 steps** | **3.8** | **0.97** | **11.0** |
  | the same, 4 steps | 3.7 | 0.95 | 9.3 |

  - It is more than softness. At 1 MP the painter paints simpler things: the warning signs' "!" breaks at every 1 MP setting (4 steps adds glitch blocks at the outer edges), the robe loses its fold lines, the creature behind turns to haze (`out/demo/20261014-0945-painter-canvas-size-grids/`). Next to the sharp picture this reads as a join line.
  - X2's Turbo 8-step painter at ~2 MP keeps that detail (5 JJK scenes: 4.0 against 1.7 at 1 MP; 25-step 4.7).
  - The hidden-sides scores (near / far / seam) did not see any of it.
- **Full-size check** (`out/runs/painter-bench/full-size-check.sh`; runs `20261014-094457-full-turbo5`, `20261014-095246-full-turbo4`):
  - By eye (agent, all 13 scenes' seams against the 25-step plate): the hand, the scarf's texture, the robe's lines and the hair strands hold, and no seam line shows. Left: one "!" is a thin broken bar (action), and a few sketchy strokes by an eye (tilt-vertical).
  - 4 steps at full size is glitchier: black blobs in a sign, a stray streak. The swap test uses 5 steps.
  - Device peak ~10.2 GB.
- **Swap test, full size, 5 steps** (`out/demo/20261014-100311-swap-live-2mp-w4a8-turbo5`): **fail, 17 of 20** (8/10 with the reference shown first, 9/10 second). So the canvas size is not the gap the user sees.
  - The 25-step plate was picked every time (2/2) in action, dark, held-dialogue, jjk-one-side and pan-horizontal.
  - In the `after/` sheets, the live painter's inventions are murkier wherever the scene leaves room to guess (dark: a smudge for the left girl's head, a muddy boy's hair). It also misses scene context: in action the film and the 25-step plate have a purple background at the outer edges; the live plate carries the sign's stripes to the edge.
  - The 25-step plate's prompt (comfy-plates.py): the outpaint instruction + " Scene: " + a Qwen3-VL-8B caption of the visible picture (place, people, clothes, art style, colours, light), read with the real canvas; action's caption names "a dreamy, gradient purple background". The live painter: one grey-canvas read of the bare instruction, for every plate. No fast recipe (X2's included) ever had the caption.
  - Neither the agent's eye nor the hidden-sides scores predicted any of the three swap tests.
- **Content diagnosis** (`painter-bench.py --reads caption-full`: the 25-step plate's own prompt and caption, read with the canvas; `out/runs/painter-bench/content-diagnosis.sh`): the full-size live recipe with that read at 5 steps, and with the grey proxy at 8 steps. Then one interleaved blind session (`painter-swap-test.py NAME=DIR,NAME=DIR`, 13 trials each, scored per candidate).
  - If the caption passes: captions made per scene ahead of time (on the CPU, or a small captioner), plus sides-only painting for speed.
  - If only 8 steps passes: a better few-step painter, distilled locally from the 25-step one (implementation step 5).
- **Diagnosis, judged by the agent** (the user: "no you do the test"; their impression: "pretty close, the other with a little more artifacts"). Session `out/demo/20261014-110347-swap-diagnosis-description-vs-8steps`; agent-blind set (os.urandom order and A/B, key opened after the answers, ties by coin):

  | Candidate (full size) | 25-step plate picked | Decisive trials | Ties |
  |---|---|---|---|
  | caption read, 5 steps (11.1 s) | 7 of 13 (formally a pass) | 6 of 8 to the 25-step plate | 5 |
  | grey proxy, 8 steps (15.8 s) | 9 of 13 | 7 of 8 | 5 |

  - Leaks, disclosed: the agent printed the test's order strings while checking the build (so the set was rebuilt). Each scene's two trials share one image, the 25-step plate, which could be told apart from trial 6 on.
  - The caption fixes scene context: action gets clean "!" signs, the sign's thick frame and the purple background beyond, as in the film. What remains are small few-step artifacts: a neck smear, murky dark masses, a faint join line (pan-horizontal), sketchy hands, a smudge in the hair.
  - 8 steps without the caption keeps more oddities: extra characters with smudged faces, a cyan blob, stray stripes, broken bar ends.
  - Next: caption + 8 steps (`out/runs/painter-bench/caption-steps8.sh`).
- **Caption + 8 steps** (run `20261014-112128-caption-turbo8`): 15.6 s a plate. Compared with the caption at 5 steps (agent, not blind) on the six scenes where that one kept artifacts:
  - Better in three: edge-person-still loses its floating hair strands; tilt-vertical gets a clean small flower clip; dark's girl gets the 25-step plate's clearly drawn long hair.
  - The same in two. jjk-edge-move's far sides stay murky. pan-horizontal's faint join line is a ~2-level brightness step that every painted version has, the 25-step plate included (+1.6 to +2.2 levels at x = 1440 against the film's -0.3). The product's colour fit and seam pull remove such steps; the swap test leaves both out for every version.
  - Worse in one: jjk-still-edge-person's sleeve ends without a hand, with a stray line.
  - It is the best fast candidate so far. Acceptance swap test, counterbalanced, seed 61: `out/demo/20261014-113141-swap-live-2mp-caption-turbo8`.
  - **The user** (11:33): "you do the test yourself and find room for improvement, I think they're pretty 80% good right now". Character references stay off for now.
  - **Agent-blind check** (each scene once, os.urandom A/B, key read after the answers): the 25-step plate picked 8 of 13 (p 0.29). Decisive trials 4 of 7, 6 ties; the caption at 5 steps was 6 of 8. The agent has seen the 25-step plates many times today, so this is a weak check.
  - **Room for improvement**, the four scenes the 25-step plate still wins, all small details drawn in the last low-noise steps:
    - anatomy: a sleeve with no hand (jjk-still-edge-person), a doubled eyelash stroke (tilt-vertical);
    - smears: on a neck (edge-person-still), at a hairline (tilt-vertical);
    - murky dark masses where the film has crisp ice shards (jjk-edge-move).
  - Levers painted (`out/runs/painter-bench/caption-levers.sh`; `painter-bench.py --sampler`, `painter_core.SCHEDULES["turbo8lo"]`):
    - **`res_multistep`, the same 8 sigmas** (15.6 s; near / far / seam 11.46 / 28.63 / 1.68 against euler's 10.20 / 26.54 / 1.53): mixed. The neck smear goes and jjk-edge-move's shards turn crisp, but the stray line darkens and tilt-vertical gains a scribble.
    - **`turbo8lo`, 3 more low-noise steps** (11 steps, 20.5 s): the same composition, with marginally cleaner lines. No fix.
  - **Conclusion:** the leftover artifacts (a missing hand, a neck smear, murky masses) are decided at high noise, where the composition forms. No sampler or schedule lever removes them; it would take training the few-step painter toward the 25-step one (implementation step 5, distillation), or a seed/best-of-N choice at N times the cost.
- **The caption at the live ~1 MP canvas** (`out/runs/painter-bench/caption-1mp.sh`; runs `20261014-115406-caption-1mp-turbo5`, `-115946-caption-1mp-turbo8`):
  - **5.22 s a plate at 5 steps**, the live recipe's speed; near / far / seam 9.90 / 27.02 / 1.47.
  - The caption's gains hold: action gets the sign's thick frame and the purple background, and jjk-one-side the scarf and robe. The "!" marks are blockier.
  - The softness stays. Side detail is 1.8 at 5 or 8 steps (2 MP + caption, 8 steps: 3.4; 25-step 4.7; truth 4.3), and the side/picture detail ratio at the join is 0.53 (2 MP: 0.94), the likely "join line" of the first two swap tests.
  - Next candidate for the live path: 1 MP + caption + a fast anime upscaler on the sides (the user's first choice this morning, now that the caption fixes what is painted). It is judged by the agent first; the user sees it only if it passes.
- **Sides-only painting without training: rejected** (runs `20261014-110201-sides-turbo5-band0`, `-110811-sides-turbo5-band32`).
  - 7.1-7.4 s a plate, against 11.0 for the whole canvas.
  - Band 0: seam score 8.07 (whole canvas 1.44), with garbled copies of the picture's text and glyphs in the sides, and a line at both joins. 32 px of picture painted next to each seam: seam 1.95, but the content is still garbage (checkerboard glyphs, nonsense letters in the bar), and jjk-one-side's man gets long flowing hair.
  - Without its whole output canvas the model loses coherence. Painting only the sides needs the trained sides-only LoRA (implementation step 4).
  - `painter-swap-test.py` now counterbalances the order: the reference is first in exactly half the trials, and a scene shown twice gets both orders. The user picked the second plate in 29 of 44 answers.
  - The two earlier tests were balanced by chance (11 and 10 of 20), so their verdicts stand.
- **Painting only the sides, without training** (`painter_core` `sides`, `painter-bench.py --sides-only [--sides-band PX]`; `out/runs/painter-bench/sides-only-check.sh`; not yet run):
  - The target latent holds only the side columns at full density, and the whole canvas stays the reference.
  - The target's rope column ids are set to the canvas columns it holds (an override of the model instance's `pe_embedder`). A `post_input` patch would turn the text + reference K/V cache off.
  - The painted columns are spliced into the canvas's latent for the decode.
  - Estimate: ~5.4 s a plate at 5 steps (half the generated tokens, attention ~0.4×).
- Dropped: the 0.74 MP canvas (softer still) and the 6-step swap test (same softness).
- **Identity references:** the user chose clusters checked by eye, after a timing check.
  - `painter_jobs.IdentityRefs` uses only `identity/allowed.json`, and only while it names the current `clusters.json` (by sha1).
  - JJK's list: c2 (the lead in white), c16 (the fur-collared man), c28 (the pink-haired woman).
  - c1 also holds a man in a pink jacket, and its crop is the lead with his blood stain. c8 also holds a fur-collared man. Both stay out until split.
  - Timing check: `e1-painter-load.py --identity-encoder 12` keeps the CPU encoder busy through the painter cell (`out/e1/identity-encoder-cells.sh`, run `out/e1/20261014-103611`).
  - **It fails.** At 240 Hz: 156 wrong / 137 missed per min, 1206 engine replans (no load at 10:31: 0 / 0.5; the painter alone, phase 3: 2.0 / 1.0). 96 Hz stays at 0.5 / 0. The GPU's SM clock held (p5 2182 MHz), so this is CPU time: the encoder on all 32 threads, against the player's 4.2 ms refresh. Reads took 22-30 s; 22 plates were painted.
  - The encoder now runs at idle priority, with 8 threads on the upper half of the logical CPUs (`identity_encoder.CPU_THREADS`; `painter-service.py --cpu-priority/--cpu-threads/--cpu-affinity`). Re-measure: `out/e1/identity-encoder-confined-cell.sh`. References stay off until it is clean.
  - **Re-measure, confined (`out/e1/20261014-111439`): 7.15 wrong / 8.68 missed per min at 240 Hz, 24 replans.** That is 20× better, but still above no load (0 / 0.5) and the painter alone (phase 3: 2.0 / 1.0). 96 Hz: 0 / 0. Reads still take 29-33 s.
  - It still fails criterion 3. Remaining options: read only while the film is paused or stopped, or ahead of playback.

## Overview

The painter is the budget risk ([X5](../reports/spike-261013-1430-x5-set-reuse-and-live-schedule.md)).
- JJK needs 0.33-0.36 new plates per film-second: places rarely repeat (set reuse 1.10), and real pixels cover only ~11% of the sides.
- If one painter makes every plate, it must take ~1.3-1.8 s per plate, against the ~5-11 s that public levers are expected to reach.

**Two-speed painting closes most of that gap** (X5 addendum). The big painter paints only the plates that stay on screen ≥ ~3 s: 170 of 527 plates, covering 56% of film time. A small fast painter paints the rest: short, busy shots, ≤ 0.3-0.5 s per plate. The big painter's target then becomes ~3.5-6 s per plate, depending on u and the refiner's cost. This phase reaches it without visible loss, using levers in order of cost and stopping when the target is met.

## Requirements

- Functional:
  - The painter runs resident in the worker and produces T2 patches for the world canvas.
  - One painting serves a set, and shots of that set crop the atlas.
  - OP/ED sides are painted once per series and cached.
- Non-functional:
  - Big painter: warm s/plate ≤ the target for the chosen threshold T (table below); small painter ≤ 0.3-0.5 s per plate. VRAM within the worker budget next to the refiner.
  - The painter shares the GPU with the player without harm. It fits its VRAM budget with fixed allocations and streams no weights while the film plays.
    - Phase 2 re-ran E1's painter cell with ComfyUI capped at `--reserve-vram 4` ([report](../reports/implementation-261013-2012-live-skeleton-phase2.md)). Wrong frames doubled: 39.8 / 40.9 per minute at 240 / 96 Hz, against 23.0 / 18.4 uncapped and 3.1 / 0.5 with no load.
    - Likely cause (PCIe traffic not measured): the cap leaves less VRAM than the INT8 DiT needs, so its weights stream at every step.
    - Measured on that cell. Target: at or below the no-load cell.
  - Every recipe change passes the swap test: the blind test against the 25-step plate (≤ 12/20) plus hidden-sides scores.
  - Training is local only, with rests.

## Architecture

**Two-speed policy (the scheduler).** Each plate goes to the big or the small painter by how long it stays on screen:
- Threshold T: default 3 s, set by the user's forced choice.
- Optionally by calmness: shots with few GEN zones get the big painter.
- Small-painter plates are judged on short action shots in real use. The test is the user's forced choice against big-painter plates: not reliably picked (≤ 12/20). If they fail, raise T, which raises the big painter's target (X5 addendum table).

| Threshold T | Big plates (film time) | Big painter target, u 0.75 / refiner 30 ms | u 0.85 / 30 ms |
|---|---|---|---|
| 2 s | 290 (73%) | 3.0 s | 3.6 s |
| 3 s | 170 (56%) | 5.0 s | 6.1 s |
| 4 s | 98 (42%) | 8.4 s | 9.7 s |

The small painter is the refiner family from phase 5 in "paint mode": more steps, the whole side at low resolution, trained on the show's hidden-sides truth.

**Big-painter levers, in order.** Stop as soon as the target is met.

1. **X2's measured recipe as the starting point: 14 s per plate** ([X2](../reports/spike-261013-1855-x2-painter-speed.md)).
   - Official INT8-ConvRot DiT and encoder, Turbo 8-step LoRA + outpaint LoRA v2, ~1 MP canvas, no caption pass.
   - Resident in ComfyUI's **dynamic VRAM** mode. Legacy resident mode trips the RAM guard and was slower.
   - Text-only conditioning failed (grey sides), so the 8B VLM keeps reading the canvas; it costs ~7 s of the 14.
2. **Cheaper VLM read:** a smaller input image for the VLM (`resolution` on TextEncodeQwenImage21) or the official W4A8 encoder (6.3 GB). Swap test against the 14 s recipe.
3. **Set atlas:**
   - One painting per set (sets from X5's registration).
   - Real pixels from every shot of the set registered into the atlas first; the painter fills only what no shot saw.
   - Each shot's plate is a crop or scale of the atlas through its registration.
   - A new set is painted at its first shot; a shot whose registration fails gets its own plate.
4. **Sides-only plates at the real strip geometry.** Today the painter redraws the picture area as well (about half its tokens), and that output is then discarded. Retrain the outpaint LoRA locally so its target is only the sides plus a seam band, with the picture given as the condition image:
   - Data: hidden-sides pairs from the user's own episodes (local, exact truth).
   - Trainer: musubi-tuner (Qwen-Image-2.1 merged 2026-10-12, experimental) or DiffSynth.
   - Settings: FP8 base, block swap; 6-22 h overnight runs with rests.
5. **If still short: local self-distillation to 4 steps** (on-policy distillation, OPD) of the merged painter on the laptop. The PAI Fun-Acc 4-step LoRA does not load in ComfyUI 0.39.
6. **Last:** NVFP4 (Nunchaku / ModelOpt on sm_120, unverified) or depth pruning with LoRA healing (unproven on 2.1).

**Identity:** the painter also receives phase 4's identity reference crops of characters cut by the edge, as extra reference images; Qwen-Image-2.1 edits from several images. Check: on jjk-one-side the plate keeps the blood stain, short hair and no ponytail.

Licence flag, not a filter: Qwen-Image-2.1 is under a research licence, and students trained on its outputs inherit it. The user decides later.

## Related code files

- Create:
  - `spikes/ai-surround/live/painter.py` (resident painter, recipe from X2)
  - `spikes/ai-surround/live/set_atlas.py` (registration into the atlas, crop per shot)
  - `spikes/ai-surround/make-sides-only-pairs.py` (training pairs from local episodes)
  - `spikes/ai-surround/train-outpaint-lora.py` (wrapper around the chosen trainer; `--rest`)
- Modify: `spikes/ai-surround/live/paint_plate.py` (replaced by `painter.py`); worker scheduler (plates per set).
- Reuse: `painter-speed.py` (X2), `set-reuse-and-schedule.py` (X5 registration), `comfy-plates.py`, `hidden_sides.py`, `make-blind-test.py` / `true-sides-test.py`.

## Implementation steps

1. Port X2's winning recipe into a resident painter with fixed allocations inside the worker's VRAM budget (no weight streaming during playback). Measure warm s/plate, VRAM and the overhead split (encoder, DiT, VAE), then run E1's painter cell on it.
2. Build the set atlas:
   - Registration from X5, made incremental (each new shot is matched against the open sets).
   - Real pixels first, then the painter for the rest.
   - Store the atlas in the cache.
3. Change the scheduler to paint per set at first appearance; inherit for short shots; OP/ED from the series cache.
4. If the big painter's target is not met: build sides-only training pairs (strip geometry 32:9, the picture as condition, the sides plus seam band as target; random windows; same mask on all frames), then train overnight with rests. Swap test against the old LoRA.
5. If still short: OPD to 4 steps locally. Swap test.
6. Record s/plate, VRAM, the swap-test verdicts and the chosen recipe. Update the synthesis.

## Success criteria

- [ ] Big painter: warm s/plate ≤ the target for the chosen threshold T, on the 5 JJK scenes and on the comedy scenes.
- [ ] Two-speed policy: the user does not reliably pick big-painter plates over small-painter plates on short action shots (≤ 12/20). Threshold T recorded.
- [ ] The swap test passes: the user does not reliably pick the 25-step plate (≤ 12/20 on still shots); seam and colour within the current spread.
- [ ] Set atlas: plates per film-second on JJK ≤ X5's simulated value; shots that inherit a set's plate show no visible mismatch at cuts (by eye on 10 cuts).
- [ ] Worker VRAM (painter + phase 5's refiner reservation) within budget; no pagefile growth.
- [ ] E1's painter cell with this painter: wrong frames at or below the no-load cell.

## Risk assessment

- **The outpaint LoRA doesn't survive few-step weights.** Signal: X2 seams or style break. Response: self-distil locally (step 5) or retrain the LoRA on the Turbo base.
- **Local training is too slow on 12 GB** (> 60 s/step or out of memory). Response: a smaller rank, lower resolution or fewer steps. Never cloud (user rule). If the target is still missed, replan the painter (smaller anime-capable base as an action-shot painter, by-eye gated).
- **Atlas crops look wrong across cuts** (scale or perspective differs between shots). Signal: mismatch seen at cuts. Response: per-shot plate for those shots (registration confidence threshold).
- **Licence.** Flag only; the user decides.
