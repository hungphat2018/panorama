---
handoff-version: 1
generated: 2026-10-14T05:12:00Z
generator: ak:handoff@2.0.0
focus: "Phase 3 (painter) of plans/261013-0718-ai-surround-live-path: continue with the user's chosen next step — option 1, a fast anime upscaler on the ~1 MP + scene-caption plates, judged by the agent against the 25-step plate before any user test. Write the handoff in plans/handoffs/."
workspace: C:/Users/tamla/Downloads/Telegram Desktop/panorama
branch: main
head: a175cd0
---

# HANDOFF: AI surround painter, upscaler on the 1 MP + caption plates

## Mission and current status

Focus: "Phase 3 (painter) of plans/261013-0718-ai-surround-live-path: continue with the user's chosen next step — option 1, a fast anime upscaler on the ~1 MP + scene-caption plates, judged by the agent against the 25-step plate before any user test. Write the handoff in plans/handoffs/."

**The quality bar changed** (user, 12:10, verbatim): "the image quality of generated doesn't have to be exactly the same as the original, as good as it make senses, and right context, less artifact possible is fine".
- A side passes when it makes sense, has the right scene context, and has as few artifacts as possible.
- Matching the 25-step plate is no longer required. Use it as a comparison, not as the pass/fail test.
- A visible seam still fails.

Goal: a live-speed painter recipe whose sides pass that bar. The candidate is the live recipe at ~1 MP with the scene caption, 5 steps (5.22 s a plate; the budget is ~4.7 s at T = 3 s), plus a fast anime upscaler on the sides. The upscaler fixes the 1 MP softness, which shows as a sharpness step ("join line") where the side meets the picture.

Done (this session, 2026-10-14 09:30-12:12):
- Scored the user's swap tests:
  - live 1 MP, 5 steps: 14/20 → fail;
  - 1 MP, 4 steps: 16/20 → fail;
  - 2 MP, 5 steps: 17/20 → fail (8/10 with the reference first, 9/10 second).
  - The user's cues: "what was painted" and "the join line".
- Found the main cause: the live painter had no scene caption.
  - The 25-step plate's prompt is the outpaint instruction + " Scene: " + a Qwen3-VL-8B caption of the visible picture. The live painter used one fixed grey-canvas conditioning.
  - With the caption (`painter-bench.py --reads caption-full`), the live painter gets scene context right, at 1 MP and at 2 MP.
- Diagnosis and acceptance, judged by the agent (the user delegated):
  - caption, 2 MP, 8 steps: 8/13 against the 25-step plate, decisive trials 4/7 (≈ chance);
  - caption, 2 MP, 5 steps: 7/13, decisive 6/8;
  - grey proxy, 2 MP, 8 steps: 9/13, decisive 7/8.
  - The user's own impression: "pretty 80% good".
- Levers that did not help:
  - the `res_multistep` sampler: mixed;
  - three extra low-noise steps (`turbo8lo`, 11 steps): no change;
  - sides-only painting without training: garbled sides, rejected.
- 1 MP + caption: 5.22 s at 5 steps, context right, but soft. Side detail 1.8 against the truth's 4.3; side/picture detail at the join 0.53, against ~1.0.
- Character references: the user picked the clusters c2, c16, c28 (`identity/allowed.json`). The CPU encoder fails the E1 timing check even confined, so references are OFF (user: "leave off for now").

Remaining:
- the upscaler candidate (next actions);
- live per-scene captions (design: the 8B VLM can't sit on the GPU next to the painter, and on the CPU it hurts 240 Hz timing);
- training, later and overnight: distilling the few-step painter toward the 25-step one for the leftover composition artifacts, and/or a sides-only LoRA for speed.

Urgency: normal. The user wants another session to continue ("write handoff … let the other do the rest job").

## Scope and guardrails

Workspace: `C:/Users/tamla/Downloads/Telegram Desktop/panorama` (Windows 11, RTX 5070 Ti Laptop 12 GB, Ryzen 9 8940HX 16C/32T, C: ~32 GB free at 12:09).

In scope:
- Upscaling the 1 MP caption plates' sides and measuring them; the agent's own judgment;
- a final ≤12-trial blind test for the user, only if the agent's checks pass;
- if it works, wiring the upscaler into `spikes/ai-surround/live/painter.py` behind an option (default off until the user accepts it).

Out of scope:
- phase 4 files: `side-gate.py`, `live/side-gate.json`, the structure path, `perf1-*` player C files;
- phase 5 files: `make-side-sequences.py`, `train-side-model.py`, `side-model-bakeoff.py`, `live/side_model.py`;
- live caption generation (design only, ask the user);
- training runs;
- identity references (off).

Constraints (user rules, mostly in memory under `~/.claude/projects/C--Users-tamla-Downloads-Telegram-Desktop-panorama/memory/`):
- **Bar:** sense + right context + fewest artifacts; a visible seam fails.
- The agent judges quality itself, with worst moments first. The user sees only finals that pass: by name, next to the truth, worst moments first, with at most 12 blind trials to confirm.
- Recommend only verified state of the art: check what is current before naming a tool or model.
- Counterbalance every blind test: each version first in exactly half the trials; check the mixing (the user leans to the second plate: 29/44).
- The agent's eye has not predicted the user's verdicts. Use pre-checks to reject, never to promise a pass.
- Rest the PC between heavy runs (rests built into scripts).
- No CPU-heavy work (decodes, encodes, builds) during another session's screen runs.
- Never TaskStop a running screen run.
- Small local training only (no cloud). Quality first.

Safety boundaries:
- **The GPU is shared.** The phase 5 session ("panorama-49") has been training on it since 12:06 for ~2-6 h. The phase 4 session ("…true-sides-c…") needs the screens + an idle GPU for 240 Hz runs. Message them (SendMessage; ListAgents) before any GPU or screen use, and wait for a rest window.
- Downloaded models are untrusted: put each in its own new folder, and run Python with `-I`.
- No commits unless the user asks (the repo has a large uncommitted state that is not this session's alone).

## Current state

Branch: main
HEAD: a175cd0 (feat: implement picture path, audio pipeline, and pacing spikes with ADRs 0025-0027)
Working tree: dirty, 98 `git status --short` entries. `spikes/ai-surround/` is untracked as a whole, so git diff shows none of this session's changes.

Changed files (this session, all under the untracked `spikes/ai-surround/`):
- `painter-swap-test.py`: counterbalanced order (each version first in half the trials; a scene shown twice gets both orders); `NAME=DIR,NAME=DIR` multi-candidate interleaved sessions; per-candidate scoring with the reference's picks split by position. LF line endings, as before.
- `painter-bench.py`:
  - `--reads caption-full` (the 25-step plate's prompt and caption from `out/runs/captions.json`, read with the canvas);
  - `--sampler`;
  - `--sides-only` / `--sides-band` (rejected path, kept as an option);
  - settings recorded in summary.json.
- `painter_core.py`:
  - per-paint `sampler`;
  - `SCHEDULES["turbo8lo"]`;
  - the `sides` option (an override of the target's rope column ids on the model instance's `pe_embedder`; the painted columns spliced into the reference latent).
- `painter-service.py`: `/paint` takes `sides`, `sampler`; new flags `--cpu-priority below|idle`, `--cpu-threads N`, `--cpu-affinity A-B`.
- `e1-painter-load.py`: `--identity-encoder N` / `--identity-crops GLOB` (the worker's CPU identity encoder kept busy through an E1 painter cell).
- `live/painter_jobs.py`: `IdentityRefs` uses only the clusters in `<cache>/identity/allowed.json`, and only while it names the current `clusters.json` (sha1). CRLF kept.
- `live/identity_encoder.py`: the CPU encoder service runs at idle priority, with 8 threads, on the upper half of the logical CPUs (`CPU_THREADS`).
- Run scripts:
  - `out/runs/painter-bench/{full-size-check,sides-only-check,content-diagnosis,caption-steps8,caption-levers,caption-1mp}.sh`
  - `out/e1/{identity-encoder-cells,identity-encoder-confined-cell}.sh`
- Data: `out/live/cache/6bc0a6fadf566263123a2052fd36f430-530562886/identity/allowed.json` (c2, c16, c28).
- Plans: `plans/261013-0718-ai-surround-live-path/plan.md` (sections "Where things stand (2026-10-14, morning)" and acceptance criterion 4's amendment) and `phase-03-painter-at-speed.md` ("Progress (2026-10-14, morning)").

Untracked files: see `git status --short`. All new outputs are under `spikes/ai-surround/out/`.
Intentional local modifications: yes.

## Decisions and rationale

| Decision | Rationale | Alternative rejected | Reference |
|---|---|---|---|
| The scene caption is required in any live recipe | Fixes the scene context: action's frame and purple background, clean "!" signs. The biggest single gain in the agent's blind checks. | Grey proxy conditioning (fails the user's eye 14-17/20) | phase-03 "Progress (2026-10-14, morning)" |
| Quality bar = sense + context + fewest artifacts | The user, 12:10 | Exact match to the 25-step plate (forced choice ≤12/20) | plan.md criterion 4 amendment |
| Next candidate: 1 MP + caption + 5 steps + anime upscaler | 5.22 s ≈ budget. The caption fixes content; the upscaler targets the 0.53 join sharpness step. | 2 MP + caption + 8 steps (15.6 s, 3.3× over budget) | the user's option 1, 12:10 |
| Sides-only painting without training rejected | Garbled glyphs and text copies, seam 8.07 at band 0; long hair invented at band 32 | Train a sides-only LoRA later (implementation step 4) | runs `-sides-turbo5-band0/-band32` |
| Sampler / low-noise steps not adopted | res_multistep mixed; turbo8lo no change. The leftovers are decided at high noise. | Distillation toward the 25-step painter (training) | runs `-caption-turbo8-resms`, `-caption-turbo8lo` |
| Character references off | Even the confined CPU encoder costs 7.15 / 8.68 wrong / missed per min at 240 Hz (no load: 0 / 0.5) | Read only while paused; leave for later (the user chose later) | `out/e1/20261014-111439` |
| Swap tests counterbalanced | The user picked the second plate 29/44; seed 8 had produced a 15/5 order | Random per-trial order | memory `counterbalance-blind-test-order` |

## Work performed

- Scored the user's tests: `painter-swap-test.py --score` on
  - `out/demo/20261014-044008-…-turbo5` (14/20),
  - `…-045845-…-turbo4` (16/20),
  - `…-100311-swap-live-2mp-w4a8-turbo5` (17/20).
- Full-size check, `full-size-check.sh`: live recipe at `--canvas-scale 1.0` (1920×1088). 5 steps 10.97 s, 4 steps 9.30 s; peak ~10.2 GB.
- Diagnosis plates, `content-diagnosis.sh`:
  - caption 2 MP, 5 steps: 11.07 s (run `20261014-104317-caption-turbo5`; its `conds/caption-full-<scene>.pt` are reused by the later runs);
  - grey proxy 2 MP, 8 steps: 15.81 s.
  - The 25-step captions are in `out/runs/captions.json`.
- `caption-steps8.sh`: caption 2 MP, 8 steps, 15.57 s (run `20261014-112128-caption-turbo8`).
- `caption-levers.sh`: res_multistep 15.61 s; turbo8lo 20.53 s.
- `caption-1mp.sh`, with the live settings (W4A8, ck-int8 attention, attn-chunk 2048, gpu-queue layer):
  - caption 1 MP, 5 steps: **5.22 s**, run `20261014-115406-caption-1mp-turbo5`;
  - 8 steps: run `20261014-115946-caption-1mp-turbo8`.
- E1 timing:
  - `identity-encoder-cells.sh`, run `out/e1/20261014-103611`: no load 0 / 0.51; painter + encoder on all cores 156.27 / 136.86 wrong / missed per min at 240 Hz (1206 replans).
  - Confined, `identity-encoder-confined-cell.sh`, run `out/e1/20261014-111439`: 7.15 / 8.68 (24 replans); 96 Hz 0 / 0.
- Agent-blind judgments: os.urandom A/B, key read only after the answers were saved, ties settled by coin. The scripts are in the session scratchpad (temporary, may be gone): `%LOCALAPPDATA%/Temp/claude/C--Users-tamla-Downloads-Telegram-Desktop-panorama/c5ec7da1-40fd-48bc-b1cb-c377471b3d26/scratchpad/{blind-build,blind2-build,seam-sheet,sides-rows,strip-stack,full-size-compare}.py`.
- **Measures**, to re-create if the scratchpad is gone (all on 1920×1080 plates, rows 270-810, grey mean |Laplacian| computed on the strip, then columns selected):
  - side detail = mean over x∈[0,480)∪[1440,1920);
  - join ratio = mean over [432,480)∪[1440,1488) / mean over [480,528)∪[1392,1440).
  - Values: truth 4.3 / 1.02; 25-step 4.7 / 1.14; caption 2 MP 8 steps 3.4 / 0.94; caption 1 MP 5 steps 1.8 / 0.53.
  - Seam brightness step: column means at x = 1436-1443. Every painted version is +1.6 to +2.2 levels at x = 1440; the film is continuous.
- Messages to the other sessions: told phase 5 the GPU is theirs from 12:06; told phase 4 no jobs are queued here.
- 0 redactions applied.

## Verification

| Check | Command | Outcome | When |
|---|---|---|---|
| Allowlist logic (no list, valid list, mixed c0 never chosen, stale list) | scratchpad `idtest-run.py` on a copy of the clusters | 5/5 PASS | ~09:55 |
| Syntax of edited Python | `python -c "import ast; ast.parse(...)"` per file | OK | after each edit |
| Line endings kept (CRLF files stay CRLF) | `file <paths>` | OK (painter_jobs.py restored to CRLF after a rewrite) | 09:51+ |
| Multi-candidate scorer reproduces the old score | `painter-swap-test.py --score …-100311-… "<answers>"` | 17/20, 8/10 and 9/10 split | 11:06 |
| Seam pre-check + md5 (no identical pairs) for each candidate test | scratchpad `seam-sheet.py` | no seam lines, no duplicates (2 MP 5 steps, caption 5 steps, steps8, caption 8 steps) | 10:00-11:31 |
| Confined encoder in the real worker path | `identity-encoder-confined-cell.sh` | ran; 7.15 / 8.68 per min at 240 Hz → fails E1 | 11:14-11:21 |

Not run:
- the upscaler itself (not chosen until 12:10);
- the full `live-sets-selftest.py` / cache / store self-tests after the `painter_jobs.py` allowlist change (only the scratch unit check ran; run `live-smoke.py --fake-player` once before relying on the worker);
- the 4-step 2 MP or 8-step 1 MP plates by eye in detail;
- a code review of this session's changes.

## Open risks and blockers

- Type: risk. Owner: next session. Impact: an upscaler sharpens edges but can't add painted detail, and may over-sharpen or add halos. Judge it against the truth's side detail (~4.3) and the join ratio (~1.0), not "sharper is better".
- Type: blocker (scheduling). Owner: user / sessions. Impact: the GPU is taken by phase 5's training (~12:06 + 2-6 h). The upscaler can run on the existing plates on the CPU or in a short GPU window negotiated with panorama-49. Phase 4 needs the screens and an idle GPU for 240 Hz runs.
- Type: question. Owner: user. Impact: how scene captions are made live. The 8B VLM (INT8 ~9.4 GB) can't share the GPU with the painter, and even confined on the CPU it disturbs the 240 Hz screen. Options to design: a GPU pre-pass at episode start, captioning ahead in batches, a smaller captioner feeding the 8B read, and others. Every candidate now depends on this.
- Type: risk. Owner: next session. Impact: the leftover few-step artifacts (a missing hand, a neck smear, murky dark masses) are compositional. Only training (distillation) or best-of-N fixes them, so judge how much they matter under the relaxed bar.
- Type: risk. Owner: next session. Impact: the swap test's composites leave out the product's colour fit and seam pull. Every painted version, the 25-step one included, carries a ~2-level brightness step at the seam. Consider testing with the product's seam handling applied to all versions alike.

## Exact next actions

1. **First safe step**: read `plans/261013-0718-ai-surround-live-path/phase-03-painter-at-speed.md` → "Progress (2026-10-14, morning)", and `plan.md` → acceptance criterion 4 (amended). Then confirm the plate folders exist: `spikes/ai-surround/out/runs/painter-bench/20261014-115406-caption-1mp-turbo5/plates/w4a8/caption-full/` (`<scene>.png` composites and `<scene>-raw.png` canvas outputs, 1344×768).
2. Research the current best FAST anime upscalers (2026; verify, don't assume): models that run 1 MP → 2× in tens of ms on an RTX 5070 Ti laptop with short kernels (E1-friendly), small VRAM, good on cel-shaded line art. Candidates to verify: compact/real-time anime SR families from OpenModelDB and similar. Download into a new, empty folder; run with `python -I`.
3. Upscale the 1 MP caption plates' raw canvas outputs (sides only, or whole canvas then crop), map them back to the 1920×1080 geometry the bench uses (`painter-bench.py`: crop the pad rows, resize to the truth's size, paste the real centre), and write a new plate folder.
4. Measure side detail / join ratio / seam step against the truth, 25-step, 2 MP-caption and plain 1 MP-caption. Then judge by eye per scene: worst moments first, and seam crops at x = 480 and x = 1440. Check for halos and over-sharpening, and that context is unchanged.
5. If it passes the agent's checks under the new bar, build a ≤12-trial counterbalanced swap test (`painter-swap-test.py`, single candidate; pick a well-mixed seed). Show the user the final by name next to the truth, worst moments first.
6. If accepted, add an `upscale` option to `live/painter.py` (default off), and check its time and E1 impact in a coordinated window. Then turn to live captions (design with the user) and the training plan.

## Source pointers

- `plans/261013-0718-ai-surround-live-path/plan.md`: overview, acceptance criteria (criterion 4 amended 12:10), "Where things stand (2026-10-14, morning)".
- `plans/261013-0718-ai-surround-live-path/phase-03-painter-at-speed.md`: "Progress (2026-10-14, morning)" holds all numbers, runs and decisions from this session.
- `plans/reports/implementation-261014-0245-painter-at-speed-phase3.md`: phase 3 before this session.
- `spikes/ai-surround/painter-bench.py`, `painter-swap-test.py`, `painter_core.py`, `painter-service.py`, `live/painter.py`, `comfy-plates.py` (the 25-step recipe and its caption prompt `CAPTION_ASK`).
- Plates:
  - 25-step: `spikes/ai-surround/out/runs/qwen21-outpaint-v2/<scene>/plate-s0.5.png`;
  - truth: `spikes/ai-surround/out/scenes/<scene>/key.png`;
  - captions: `spikes/ai-surround/out/runs/captions.json`.
- Caption runs, under `spikes/ai-surround/out/runs/painter-bench/`:
  - `20261014-104317-caption-turbo5` (2 MP 5 steps; holds the caption conds);
  - `20261014-112128-caption-turbo8` (2 MP 8 steps);
  - `20261014-115406-caption-1mp-turbo5`, `20261014-115946-caption-1mp-turbo8` (1 MP).
- Swap tests (`spikes/ai-surround/out/demo/`):
  - `20261014-113141-swap-live-2mp-caption-turbo8`: built, unanswered by the user; acceptance judged by the agent instead;
  - `20261014-110347-swap-diagnosis-description-vs-8steps`: interleaved, agent-judged.
- E1 runs: `spikes/ai-surround/out/e1/20261014-103611`, `20261014-111439`.
- Memory: `~/.claude/projects/C--Users-tamla-Downloads-Telegram-Desktop-panorama/memory/`, in particular:
  - `ai-surround-speed-phase-targets.md` (the new bar);
  - `agent-eye-does-not-predict-user-verdict.md`;
  - `counterbalance-blind-test-order.md`;
  - `visible-seam-is-a-fail.md`;
  - `replace-failed-bars-and-prove-claims-by-eye.md`.
