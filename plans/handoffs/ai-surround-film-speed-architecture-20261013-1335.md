---
handoff-version: 1
generated: 2026-10-13T06:35:00Z
generator: claude (ak:handoff layout)
focus: "AI surround speed phase: real-time architecture research done; small local training only (no cloud); next: the user takes the true-sides blind test"
workspace: C:/Users/tamla/Downloads/Telegram Desktop/panorama
branch: main
head: a175cd0
---

# HANDOFF: AI surround at film speed (architecture settled on paper, true-sides test ready)

## Mission and current status

Focus: "AI surround speed phase: real-time architecture research done; small local training only (no cloud); next: the user takes the true-sides blind test".

**Outcome (user, 2026-10-13):** the **high preset** = today's best sides (Qwen-Image-2.1 + outpaint LoRA v2 plate, Wan2.2-VACE-Fun-A14B keyed by it, wraps). It must keep "minimal loss that seems to be no loss" and run at **1x film speed on this laptop** (RTX 5070 Ti Laptop, 12 GB). Two screens first. Playback starts at once and the AI sides catch up. Presets for weaker hardware are later, separate work.

**Done this session (10:49-13:35):**
- **Research round, all saved in `plans/reports/`:** 5 researchers plus 1 independent counsel (`kongming`, on Fable): fast current recipe, student architecture, student training, render-not-generate, perceptual budget, counsel.
- **Synthesis:** `plans/reports/research-261013-1049-ai-surround-real-time-architecture-synthesis.md`. It covers the verdict, the architecture by cadence, the budget, local-only training and OpenModelDB.
- **User decisions** recorded in memory (`ai-surround-speed-phase-targets.md`).
- **Experiment X1 built:** the true-sides blind test (`spikes/ai-surround/true-sides-test.py`), output `spikes/ai-surround/out/demo/20261013-133033-true-sides/`. It tests no generator, only how often the sides must change and whether the camera can carry them in between.

**Remaining, in order:**
1. The user takes the X1 blind test (44 trials, ~8 min); then score it and read the verdict.
2. X2, painter speed: Turbo 8-step or Fun-Acc 4-step with the outpaint LoRA, FP8, timing and by-eye check. Needs the GPU, which the other session's queue holds now.
3. X3, anime line round-trip through candidate latents (VAEs).
4. X4, local training smoke for the zone refiner (step time and memory on 12 GB).
5. X5, schedule simulation (set reuse, skippable shots, head start).
6. A plan (`ak:plan`) for building the live path once X1 and X2 settle.

## Scope and guardrails

**Workspace:** `C:\Users\tamla\Downloads\Telegram Desktop\panorama`, branch `main`, no commits this session.

**In scope:**
- `spikes/ai-surround/`: code is tracked; `out/` is git-ignored.
- `plans/reports/*261013-1049*`, `plans/handoffs/`, `tools/` (git-ignored).

**Out of scope:**
- The **GPU queue** (`spikes/ai-surround/out/runs/queue/*`, its steps and outputs). Another session owns it (user: "other agent is handle it"). The same goes for disk management.
- Pre-existing modifications not from this track: M1 plan files, `plans/reports/fullstack-developer-261012-1133-performer-audio.md`, `spikes/performer-p1/*`.
- ADRs (none until the user's by-eye verdicts), and commits (only when asked).

**Constraints (user):**
- Quality: "minimal loss that seems to be no loss". The high preset is today's best at 1x.
- Size for two screens first. Play at once; the sides catch up.
- **"small training is fine, not cloud"**: LoRAs and small models on this laptop only; no rented GPUs; the user's own episodes may be used locally.
- Verify the state of the art before naming a tool.
- Explain terms plainly.
- By eye decides, through forced choice against an on-screen anchor.
- Think in roles and levers: a model is a replaceable tool.
- AnimeGen is rejected.
- Downloads and repo investigation are allowed ("free to investigate any optimized repo, pull, download anything").

**Safety boundaries:**
- The real picture is never altered (ADR 0001); film content stays local.
- One heavy GPU job at a time: coordinate with the queue's owner before any GPU run.
- Do not delete files or model caches without asking.
- Markdown only under `plans/` or `docs/`.
- C: free space varies with the queue (23 GB at 13:40).

## Current state

- Branch: `main`. HEAD: `a175cd0`.
- Working tree: 58 entries in `git status --short` (observed 13:40). This track's are untracked:
  - `plans/handoffs/ai-surround-*` (4 earlier + this one)
  - `plans/reports/kongming-261013-1049-real-time-architecture-counsel.md`
  - `plans/reports/research-261013-1049-ai-surround-real-time-architecture-synthesis.md`
  - `plans/reports/researcher-261013-1049-{fast-current-recipe-without-training,specialist-student-architecture-and-compute,specialist-student-training-data-and-adaptation,render-not-generate-structure-first-moving-sides,perceptual-budget-of-the-sides}.md`
  - `spikes/ai-surround/` (whole folder untracked, including the new `true-sides-test.py`)
- Changed tracked files: none by this session. Everything else in `git status` is pre-existing and not ours.
- Intentional local modifications: yes (the untracked files above).
- **GPU (13:40):** the other session's queue is on step 14 (`scroll-plate.py --paint` on jjk scenes); GPU 100%, 10.9 GB in use.
- **X1 build:** first build (13:23) stopped at 17/24 clips because of a harness bug and renamed `out/demo/20261013-132345-true-sides-ABORTED-harness-bug` (do not use). Rebuild started 13:30 in the background (CPU only, 24 clips) into `out/demo/20261013-133033-true-sides/`. See Verification for its outcome.

## Decisions and rationale

| Decision | Rationale | Rejected | Reference |
|---|---|---|---|
| Per-frame video generation is not the live path | All public no-training levers give 150-300x; action needs 1,200-1,800x (best case still 8-12x short) | "make the 14B fast" | fast-current-recipe report §4 |
| Architecture: render most frames, generate only flagged zones, every side change tied to a picture change | JJK measured: 63% of steps exact by hold / still / camera slide, 81% with small patches, 19% need generation (4.7 drawings/s); time errors are the most visible, detail the least | per-frame generation; plate-only (33-53% of frames) | render-not-generate §4; perceptual-budget §1; kongming |
| Big models (A14B, Qwen 25-step) leave the live path: painter / teacher / oracle | the budget allows ~2 s per plate and ≤60 ms per refined drawing | keep A14B live | synthesis §4 |
| Training: small, local only | user, 13:20 | rented distillation (DMD / rCM / VDOT++, $0.3-1.4k per run) | synthesis §8 |
| X1 first, with no generator | settles the premise (update timing, camera carry) cheaply, from true pixels only | building a refiner first | synthesis §6 |
| In X1, never-seen content brought in by the camera comes from the truth | the first version showed grey slabs at the outer edge on the 912-px fight pan, which mixed invention into a timing test | edge-extend | `true-sides-test.py` `carried()` |
| X1 moments = the four moving jjk test scenes (fight 534, reaching 503, pan 133, edge-move 523) | the corridor moment (318.5 s) is fully still: all versions equal the truth | corridor, 520 s | dry run 13:2x |
| X1 harness stores newly revealed strips with a nearest-neighbour mask (+1 px dilation, replicate border) | a linear mask never reached 1.0 for a 1-px strip at a fractional offset, so strips stayed unknown and came back as vertical streaks (pan, reaching) that would have biased the test | linear-mask storage | sheets 13:28 vs 13:31 |
| The planner stays pure camera carry + band-triggered refresh (no periodic re-key) | the pan over a curved wall drifts (planner error 15/20 vs 1 Hz 9/15): the test should show whether that drift is visible; 1/3/6 Hz cover periodic re-keying | travel-based re-key now | dry run 13:31 |
| OpenModelDB: no reverse engineering | open weights and architectures (spandrel); upscalers and restorers cannot invent sides | | synthesis §7 |

## Work performed

- **Agents:** 5 `researcher` and 1 `kongming` launched in parallel at ~11:04, finished 12:00-12:45. The researchers' harness forbade writing files, so the parent extracted their final texts from `~/.claude/projects/C--Users-tamla-Downloads-Telegram-Desktop-panorama/3a334338-e158-4863-84f4-784269a9b7b3/subagents/agent-*.jsonl`, using a scratchpad script (`scratchpad/tools/save_reports.py`). Result: 5 reports of 161-293 lines.
- **New code:** `spikes/ai-surround/true-sides-test.py`.
  - Cuts a 32:9 strip (rows 270-810) from 1080p frames: the centre 960 px is the picture, the outer 480 px each side are the true sides. Shown at 2x in make-wide-demo's 'fill' layout.
  - Versions: truth / frozen / warp-1, 3, 6 Hz (camera-carried between refreshes, via a world canvas with one resampling and trails) / planner (refresh only when the picture's 240-px band next to a side is unexplained by hold, slide or gain: > 10% of pixels by > 16 levels).
  - Writes a blind trial playlist (`play-trials.bat`, `trials.m3u`, `key.json`), free-look bats, `metrics.json` and sheets.
  - `--score DIR "1,2,..."` gives hits per level, a binomial p and a verdict.
- **Synthesis written, then updated** for local-only training and OpenModelDB facts.
- **OpenModelDB:** tarball of `OpenModelDB/open-model-database` (pushed 2026-08-14) read into the scratchpad; 671 models, 241 anime/cartoon (summary in synthesis §7).
- **Memory:** new `ai-surround-speed-phase-targets.md` and its index line.
- **Privacy note:** the perception researcher sent the user's email once as the contact parameter of an Unpaywall API request (no result, not repeated). The user was told.
- 0 redactions applied.

## Verification

| Check | Command | Outcome | When |
|---|---|---|---|
| Reports saved | `save_reports.py` | 5 files, 161-293 lines, headers intact | 13:15 |
| X1 logic, fight (shot 534) | dry run via importlib, `versions()` + `metrics()` | camera travel 912 / 319 px, flow agreement 0.74. Error near/far: frozen 63 / 48, warp-1 Hz 33 / 35, warp-3 Hz 20 / 18, warp-6 Hz 13 / 12, planner 7.3 / 7.7. Side change per frame: 0 / 10.7 / 11.9 / 12.8 / 12.9 (truth 14.2, picture 15.8). Planner refreshes 7.8/s left, 13.5/s right | 13:2x |
| X1 versions across the clip | sheet, frames 0 / 23 / 46 / 68, viewed | frozen visibly stale. Grey slabs at the far edge in 1 Hz / 3 Hz (fixed with the truth as perfect painter). 6 Hz and planner close to the truth | 13:2x |
| X1 still moment | dry run at 318.5 s | all versions ≈ truth (picture change 0.29): moment dropped | 13:2x |
| Syntax | `ast.parse` | ok | 13:2x |
| X1 harness fix | dry run, pan + reaching sheets viewed | streaks gone. Pan: planner never refreshes (0/s) and drifts (error 15.3 / 19.8; side change 5.5 vs truth 5.1). warp-1/3/6 Hz error 8.8/15.2, 4.0/8.4, 2.1/4.6. Reaching: planner 1.4 / 2.8 refreshes/s, error 7.3 / 6.8 | 13:31 |
| X1 build | background run (`scratchpad/true-sides-build.log`) | exit 0. 24 clips, 44 trials, 223-line playlist, ffmpeg log empty, 199 MB. All 4 sheets viewed: no harness artifacts. Planner refreshes per second (left/right): fight 8.1/14.1, reaching 1.4/2.8, pan 0/0 (drift), edge-move 23.3/22.2 (≈ every frame: the structure path saves nothing on effect-heavy edges). The low-rate versions break up as expected on edge-move | ~13:45 |

Not run:
- The blind test itself (needs the user).
- Scoring (needs the answers).
- X2-X5: X2 and X4 need the GPU, which the queue holds; X3 and X5 are not started.

## Open risks and blockers

- **Type:** question. **Owner:** user. **Impact:** X1 needs the user on the two screens (~8 min). Every architecture decision downstream waits on it.
- **Type:** risk. **Owner:** agent. **Impact:** X1 tests timing with perfect content. It bounds the structure path but does not show what a real refiner or painter gives on effects and characters crossing the seam.
- **Type:** risk. **Owner:** agent. **Impact:** on busy action sides the planner refreshes almost every drawing (fight right side 14.1/s; edge-move 22-23/s per side, ≈ every frame). The refiner budget (synthesis §4) assumed the episode average of 4.7/s, so heavy action needs the run-ahead buffer and the degrade ladder.
- **Type:** risk. **Owner:** agent. **Impact:** one global camera shift, measured on the picture, does not extrapolate to far sides on curved or parallax backgrounds (pan scene: planner error 15/20, no refresh triggered). The product may need an affine or parallax camera model or a travel-based re-key; X1 shows whether the drift is visible.
- **Type:** risk. **Owner:** agent. **Impact:** the painter must reach ~2 s per plate with no cloud distillation. Remaining levers: official few-step weights, FP8/NVFP4, fewer tokens, plate reuse. Pruning plus local LoRA healing is unproven.
- **Type:** risk. **Owner:** user. **Impact:** licence. Qwen-Image-2.1 is research-only, and students trained from its outputs inherit "Improved using Qwen".
- **Type:** blocker for X2 and X4. **Owner:** the other session. **Impact:** the GPU is held by its queue. Ask the user, or that session, for a slot.
- **Type:** risk. **Owner:** agent. **Impact:** subagent harnesses here forbid writing report files. The parent must save hand-backs (extractor in the scratchpad; transcripts under the session's `subagents/` folder).

## Exact next actions

1. **First safe step**: confirm the X1 build (done ~13:45; verified, see Verification).
   - `spikes/ai-surround/out/demo/20261013-133033-true-sides/` holds `play-trials.bat`, `trials.m3u`, `key.json`, `metrics.json`, `clips/` (24), `free-look/`, `sheets/`.
   - Read `metrics.json` and look at `sheets/*.png` (frames across each clip).
   - Do not open `key.json` before the user answers.
2. Ask the user to double-click `play-trials.bat` on the two screens. Instructions: watch the middle picture as you normally would; after each pair, write 1 or 2 for "which had the odd sides?"; space pauses; 44 trials.
3. Score: `tools/venv-outpaint/Scripts/python.exe -I -X utf8 spikes/ai-surround/true-sides-test.py --score spikes/ai-surround/out/demo/20261013-133033-true-sides "<answers>"`.
   - frozen must come out visible (control); sham shows answer bias.
   - The slowest warp schedule that is "not reliably visible" sets the refresh rate.
   - If the planner is "not reliably visible", the structure path's premise holds.
   - Afterwards the user can open `play-free-look-<moment>.bat`.
4. Record the result in the synthesis (new section) and in this track's plan.
5. X2 painter speed, when the GPU is free:
   - Qwen-Image-2.1 + outpaint LoRA v2 with the official Turbo LoRA (8 steps, the card's ManualSigmas) and with PAI Fun-Acc 4-step.
   - FP8 with `--disable-dynamic-vram`.
   - Measure s/plate, seam and colour; style by eye against the 25-step plate.
6. X3, X4, X5 as in synthesis §6 (local training only).
7. Then `ak:plan` for the live path: analyse / paint / refine / render roles and the degrade ladder (synthesis §4).

## Source pointers

- **Synthesis:** `plans/reports/research-261013-1049-ai-surround-real-time-architecture-synthesis.md`
- **Reports:**
  - `plans/reports/researcher-261013-1049-fast-current-recipe-without-training.md`
  - `plans/reports/researcher-261013-1049-specialist-student-architecture-and-compute.md`
  - `plans/reports/researcher-261013-1049-specialist-student-training-data-and-adaptation.md`
  - `plans/reports/researcher-261013-1049-render-not-generate-structure-first-moving-sides.md`
  - `plans/reports/researcher-261013-1049-perceptual-budget-of-the-sides.md`
  - `plans/reports/kongming-261013-1049-real-time-architecture-counsel.md`
- **Earlier:**
  - `plans/reports/researcher-261012-1407-real-time-generative-models-and-neural-rendering.md`
  - `plans/reports/research-261012-1521-ai-surround-anime-outpainting-synthesis.md` (8.4, 8.5)
  - `plans/261012-1639-ai-surround-quality-baseline/plan.md`
  - `plans/handoffs/ai-surround-moving-sides-20261013-1040.md`
- **Code:** `spikes/ai-surround/true-sides-test.py`, `make-wide-demo.py` (grab, compose, SCREENS, MPV), `scroll-plate.py` (camera_offsets flow mode), `hidden_sides.py`, `apply-wraps.py`.
- **X1 output:** `spikes/ai-surround/out/demo/20261013-133033-true-sides/`
- **Film:** `C:\Users\tamla\Downloads\Jujutsu.Kaisen.S03E12.FiNAL.MULTi.1080p.WEBRiP.x265-KAF.mkv`; content model in `spikes/ai-surround/out/jjk/content/`
- **OpenModelDB:** https://github.com/OpenModelDB/open-model-database
- **Memory:** `~/.claude/projects/C--Users-tamla-Downloads-Telegram-Desktop-panorama/memory/` (`ai-surround-speed-phase-targets.md`)
