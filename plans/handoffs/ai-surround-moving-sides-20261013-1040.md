---
handoff-version: 1
generated: 2026-10-13T03:40:00Z
generator: claude (ak:handoff layout)
focus: "AI surround: make the sides move with the picture on action shots (continuity); queue running; AnimeGen removed"
workspace: C:/Users/tamla/Downloads/Telegram Desktop/panorama
branch: main
head: a175cd0
---

# HANDOFF: AI surround, moving sides for action shots

## Mission and current status

**Outcome** (unchanged): the quality baseline for extending anime into the leftover canvas, built from replaceable roles
(plan `plans/261012-1639-ai-surround-quality-baseline/plan.md`, criteria 1-5). A model is a tool in a role, never the goal.

**The problem to solve next** (user, 2026-10-13 ~10:45, real-use demo on the two screens): the still-plate recipe on an
uncut fight moment "didn't maintain the continuity, it's one frame in the action scene". Measured: the sides change 0.00
per frame while the picture changes 13.9. Still plates are now for still shots only. In the action episode the hidden
side moves in 88% of frames, so moving sides decide whether the feature works.

**Done this session** (2026-10-12 22:40 to 2026-10-13 10:40):
- Verified the 21:10 handoff: state held; the PC had been rebooted (22:28), nothing running, C: 51 GB free.
- Action episode added (`Jujutsu.Kaisen.S03E12`, the user's file): content model, per-side edge motion, 5 test scenes
  picked by eye (`out/scenes/jjk-*`). The hidden side holds for the whole shot in 12% of its frames (comedy: 43%).
- 14B (Wan2.2-VACE-Fun-A14B Q8) keyed by the Qwen plate: held-dialogue and edge-person-still. The 14B holds the plate's
  far content; the 1.3B drifts. edge-head-closeup never ran.
- Memory incident overnight (details below) and its fix, verified on the plate run and two 14B smoke runs.
- New tools: `scroll-plate.py`, `route-by-motion.py`, `make-wide-moving-demo.py`, `vlm-pairwise-judge.py`.
- AnimeGen (anime fine-tune of the same 14B, outpainting blocks grafted in): full graft left the sides blank white;
  low-noise expert only drew real content but more invented clutter and a seam. **Rejected by the user ("far away worse
  than Qwen") and removed at 10:50**: run stopped, model files deleted, graft node and `--animegen` option removed.
- Qwen plates on the 5 action scenes: the painter keeps this show's style at the key frame.
- Two-screen demos of still plates on two action moments: the user's continuity verdict above.
- Blind test rebuilt (21 trials, comedy episode only). The user has not taken it yet.

**Remaining**, in order (AnimeGen is out):
1. Watch the two moving versions of the fight (queued; see Current state) with the user, as video.
2. Hidden-sides numbers for moving shots: A14B keyed on the 4 moving jjk scenes (queued), against the plate, the
   scrolling plate and VACE-1.3B.
3. Design sides that move without fighting the motion (see Opinion; next actions 3-5).
4. The user takes the blind test (with the action scenes once their candidates exist); record it in the synthesis.
5. Declare the baseline per scene type (still / camera slide / other motion) in the synthesis.

## Scope and guardrails

**Workspace**: `C:\Users\tamla\Downloads\Telegram Desktop\panorama`, branch `main`, no commits this session.

**In scope**: `spikes/ai-surround/` (code tracked, `out/` ignored), `plans/261012-1639-ai-surround-quality-baseline/`,
`plans/reports/`, `plans/handoffs/`, `tools/` (git-ignored: ComfyUI portable, `models/comfy/`, venvs), HF cache.

**Out of scope**: the pre-existing modifications not from this track (M1 plan files under
`plans/261011-0554-m1-architecture-and-technology/`, `plans/reports/fullstack-developer-261012-1133-performer-audio.md`,
`spikes/performer-p1/*`); performer code; ADRs (none until the user's by-eye verdicts); commits (only when asked).

**Constraints (user)**: quality first at any cost (art style, ratio, colour, composition); speed and hardware are
information only. Big models run locally, Q8/FP8/bf16 + offload. Licences: test all, decide later. By eye decides
(forced choice, truth as anchor). Explain AI and film terms plainly. Verify the state of the art before naming a tool.
Think in roles, levers and replaceable tools. Ask early when blocked or unsure of quality; the user collaborates.

**Safety boundaries**: the real picture is never altered (every output pastes the real centre back, ADR 0001). Film
content stays local. One heavy GPU job at a time. Do not delete files or model caches without asking (C: 27 GB free).
Markdown only under `plans/` or `docs/`.

**Memory rule (new)**: every ComfyUI run with big models starts the server with `cc.LOW_RAM` (`--cache-none`), and the
14B runs one expert per prompt (`comfy-vace-clips.run_two_phase`). Without this, one prompt held both 18.7 GB experts
plus the 11 GB text encoder: 54 GB committed on 32 GB of RAM, the pagefile grew 21 -> 40 GB, C: fell to 21 GB, and
Claude Code reaped the queue runner (its note: restart only on the user's request). With it: 15 GB RAM free on plates.

## Current state (observed 2026-10-13 10:40)

**Git**: HEAD `a175cd0`, 50 entries in `git status --short`. This track's are untracked: the plan folder, 4 handoffs,
6 reports, `spikes/ai-surround/`. Everything else is pre-existing and not ours.

**GPU queue (running as a background job of the closing session)**: `spikes/ai-surround/out/runs/queue/run-queue.sh`
runs `queue.txt` line by line from the repo root and logs to `runner.log` ("start step N" / "exit=RC step N").
Lines starting with `#` are skipped, `END` stops it, and `until [ -f ... ]` lines are gates (`go-animegen` and
`go-animegen2` already exist; steps 1-9 are done or stopped). At 10:50 it started **step 10**. In order (times are
estimates):
- 10 `make-wide-moving-demo.py --at 1314.4 --recipe scroll`: the fight with a scrolling plate (~11:00)
- 11 `make-wide-moving-demo.py --at 1314.4 --recipe a14b-key`: the fight with 14B moving sides, per side (~13:10)
- 12 seam match: whole-clip plate scores incl. jjk
- 13 `scroll-plate.py --paint` on jjk-pan, jjk-one-side, jjk-action, jjk-edge-move, tilt-vertical, pan-horizontal
- 14 A14B keyed on the 4 moving jjk scenes (~4.4 h), 15 wraps, 16 routing, 17 blind test rebuild
- 18 A14B keyed edge-head-closeup, 19 wraps; 20-24 VACE-1.3B plain and keyed on jjk + wraps + routing
- 25-27 A14B plain on the story scenes and on jjk + wraps; 28-31 still demos (318.5 s corridor, 1566.5 s exterior),
  scroll pan demo (520 s), A14B moving demo of shot 503 (1226.1 s); 32 pairwise VLM judge; 33 blind test; END.

**Update 11:40.** Claude Code reaped the runner again (low memory, session idle; restart only on the user's request).
Step 10 finished: `out/demo/20261013-104958-scroll` (fight, scrolling plate): the sides now move (2.38 per frame vs the
picture's 13.79, camera travel 445 px) but by eye they are blurry, ghosted smears of the stitched frames (fist and fur
leftovers), and the lightning stops at the seam: the scrolling plate is for clean pans of still scenery only. Step 11
(`out/demo/20261013-105521-a14b-key`, the fight with A14B moving sides) survived the reap and was still sampling (its
ComfyUI holds ~31 GB committed: with `--cache-none` the text encoder still stays loaded beside one expert); nothing after
it will start. Memory was at 2.3 GB commit free with another session's probe and a C++ link running beside it.

**If the session closed, the runner may have died.** Check: `ps -ef | grep run-queue` (Git Bash) and the last lines of
`runner.log`; `tasklist | findstr python`. To resume: confirm nothing runs, delete the finished lines from `queue.txt`
(keep the header comment), then start `bash spikes/ai-surround/out/runs/queue/run-queue.sh` from the repo root as a
background job. A step killed mid-way must run again (its outputs are incomplete); a 14B scene whose frames ComfyUI
saved can be finished on the CPU (how: `hidden_sides.finish_video_fill` on the PNGs in
`tools/ComfyUI_windows_portable/ComfyUI/output/panorama/`; done once this session for edge-person-still).

**Disk and memory**: C: 50 GB free (after removing AnimeGen). RAM 32 GB; with the memory rule, about 15 GB free during plates and a 14B expert
streamed at 8.5 GB in VRAM + 9.6 GB offloaded.

**Models** (`tools/models/comfy/`): `qwen-image-2.1/` (DiT bf16 14.2, Qwen3-VL-8B TE 17.5, VAE, outpaint LoRAs v1/v2),
`wan/vace-fun-a14b/{HighNoise,LowNoise}` (2 x 18.66 GB Q8_0), `wan/wan21-repack/` (umT5 fp16 11.4, VAE).
HF cache: Wan2.1-VACE-1.3B (19 GB). **Deleted with the user's OK**: FLUX.2-klein-4B + its LoRAs (16.1 GB, `hf cache rm`;
the klein recipes cannot run now) and AnimeGen-T2V Q8 (30.8 GB, downloaded and removed today).

**ComfyUI config** (`tools/ComfyUI_windows_portable/ComfyUI/extra_model_paths.yaml`): back to the 2026-10-12 entries
(the AnimeGen folder and the custom-node path were added and removed today).

**Outputs worth opening** (`spikes/ai-surround/out/`):
- `blind-test/index.html` (21 trials; external monitor, F11)
- `demo/20261013-100301` (fight, still plate), `demo/20261013-100956` (man reaching, still plate): `play-compare-*.bat`
- `runs/sheets/jjk-*-painters-s0.5.png`, `held-dialogue-vace-a14b-key-s0.5.png`, `edge-person-still-vace-a14b-key-s0.5.png`
- `smoke/sheets/held-dialogue-animegen{,-low}-a14b-s0.5.png` (AnimeGen record; the model is gone)
- `jjk/edge-band-change.json`, `jjk/content/`

## Decisions and rationale

| Decision | Rationale | Rejected | Reference |
|---|---|---|---|
| Still plate only for still shots | User, by eye: frozen sides break continuity on action; sides 0.00 change vs picture 13.9 per frame | Plates for all shots | synthesis 8.5 |
| Moving action scenes picked by eye from each rule's top 5 | Shot detector misses smear cuts; top rule picks were flashes and hidden cuts | Rule picks alone | `make-test-scenes.py --set moving --force` |
| Camera step = most common optical-flow vector of the visible picture | Phase correlation locked onto the man the camera follows (60 px vs ~310 px travel) | Phase correlation | `scroll-plate.py` |
| Scroll only for confident slides | Credits "pan" has flow agreement 0.07 (not rigid); tilt 0.97 | Scroll every moving shot | synthesis 8.5 |
| AnimeGen rejected and removed (user) | Full graft: blank white sides; low-noise only: clutter + seam, "far away worse than Qwen" | Keep testing it | synthesis 8.5 |
| One 14B expert per prompt, `--cache-none` | 54 GB committed on 32 GB, runner reaped | Single prompt | `comfy_client.LOW_RAM`, `run_two_phase` |
| Do not restart reaped work without the user | Claude Code's reaper note | Silent restart | runner.log 00:28 |

## Work performed

**New code** (`spikes/ai-surround/`):
- `scroll-plate.py`: camera offsets (flow mode), median mosaic of the shot's visible picture, painter for the unseen
  part (`--paint`, canvas capped at 2.2 MP), slide with the camera, live seam pull. Recipes `scroll-qwen21-outpaint-v2`
  (existing plate) and `scroll-mosaic-qwen21` (painted mosaic).
- `route-by-motion.py`: plate side where the picture's band holds >= 90% of steps, video side elsewhere.
- `make-wide-moving-demo.py`: real-use 32:9 demo for moving shots (`scroll`, `vace13[-key]`, `a14b[-key]`, per side).
- `vlm-pairwise-judge.py`: the blind test taken by Qwen3-VL-8B (A/B, both orders). Never run.
- (removed 10:50) the AnimeGen graft node and `comfy-vace-clips.py --animegen`.

**Changed code**: `edge-band-change.py` (one shot in memory at a time; per-side `*_lr`), `make-test-scenes.py`
(`--set moving`, `--edge-stats`, `--prefix`, `--force`, `--dry-run`), `run-baseline-fills.py` (`--scenes`, merges scores),
`make-blind-test.py` (one-monitor instruction, `--scenes`, group `scroll`),
`comfy_client.py` (`extra_args`, `free()`, `LOW_RAM`), `comfy-vace-clips.py` (`run_two_phase`), `comfy-plates.py`,
`make-wide-demo.py` (`LOW_RAM`).

**Results** (hidden-sides test, s = 0.5, near / far / seam, whole clip unless noted) [L]:

| Scene | Qwen plate fit+live | VACE-1.3B keyed + wraps | A14B keyed + wraps |
|---|---|---|---|
| held-dialogue | 8.8 / 12.1 / x1.0 | 12.7 / 22.3 / x1.68 | 9.1 / 11.7 / x1.50 |
| edge-person-still | 15.2 / 24.4 / x1.62 | 16.7 / 32.3 / x1.87 | 16.7 / 27.0 / x1.91 |

- A14B speed: 54-55 s per frame (62-63 min per 69-frame clip); 9 + 5 min for a 17-frame smoke.
- Qwen plates on jjk, key frame: still 3.1 / 17.3 / x1.35; pan 4.9 / 20.9 / x1.08; one-side 8.7 / 24.8 / x1.15;
  action 13.7 / 41.8 / x0.96; edge-move 18.1 / 52.6 / x1.01. Style holds; the pole the boy grips (all in the hidden
  side) is missing.
- Scrolling plate, existing plate edge-stretched (CPU): tilt seam x4.58 -> x1.56, near 56.5 -> 31.1; credits "pan" no gain.
- AnimeGen smoke, held-dialogue, 17 frames, no key: both experts 40.6 / 67.2 / x2.63 (blank white sides); low only
  13.0 / 18.4 / x2.62 (real content, more clutter, seam line on the right).
- Routing on story scenes: plate on both sides for held-dialogue and edge-head-closeup; edge-person-still plate left /
  1.3B keyed right, worse than the plate alone (far 29.4 vs 24.4).

**Failures and fixes**: queue reaped for low memory (fix above); 00:53 the 14B client's ffmpeg encoder died under memory
pressure after edge-person-still was generated (frames finished on the CPU); first camera estimate locked onto a
character (switched to flow mode); my first look at the action demo judged motion from one frame (the user caught it).

## Verification

| Check | How | Outcome |
|---|---|---|
| Previous handoff's state | git, processes, GPU, disk | held (rebooted, idle, 51 GB) |
| AnimeGen graft (since removed) | GGUF header compare; ComfyUI log | 1095 shared tensors, 0 mismatches; loaded both experts |
| Split-phase 14B runs | two smoke runs | both complete; phase 1 549 s, phase 2 309 s (17 frames) |
| Low-RAM plates | 5 jjk plates | 17 min, RAM 15 GB free |
| Phase graphs | node references resolved offline | complete, nothing unused |
| Scripts | `ast.parse` after each edit | ok |
| Demo motion | sides vs picture change per frame | sides 0.00, picture 13.9 (fight) / 3.7 (reaching) |

Not run: the blind test (user), the moving demos, A14B on moving scenes, VACE-1.3B on jjk, scroll with painter,
routing on moving scenes, the pairwise judge, A14B plain, the 3-monitor width (s = 1.0) check the user asked about.

## Opinion (Claude, for the next agent)

- **Still shots: close to solved for quality.** The Qwen plate continues style, colour and scale on both shows, and by
  eye on uncut frames "the room continues, no visible seam".
- **Action: unsolved, and it is most of an action episode.** The candidates, best guess first:
  - The **14B keyed by the plate** moves with the picture and holds the plate far out on a held scene. But one fully kept
    middle frame pins the sides to one moment: on the comedy's moving credits a still key made things worse (far 22 ->
    34). The 14B obeys its key more strongly than the 1.3B, which may fight real motion.
  - Designs worth testing:
    - the plate given as a VACE **reference image** (appearance without pinning the position);
    - **several keys** per shot from plates at several frames;
    - **scroll-rendered keys** that move with the camera;
    - the **plain 14B** (no key) on action.
  - The **scrolling plate** is the cheap answer for clean camera slides; route by flow agreement.
- **AnimeGen**: dropped (user). An anime-tuned animator would need its outpainting blocks trained for it, a training job
  for later if ever; the general 14B guided by the Qwen plate is the animator to beat.
- **Speed**: 1 h per 3 s clip on the 14B. Real-time on this laptop is far off even with distillation (Wan2.2 Lightning
  LoRAs, 4-8 steps, about 6-10x) and routing. A pre-processed episode is the realistic first product shape. The user
  ranks quality first; say this plainly when asked.

## Open risks and blockers

- **Judging motion from stills** (risk, agent): every video result must be watched as video or checked with a motion
  number (sides' change per frame vs the picture's, and flicker < 0 meaning frozen) before it is shown to the user.
- **No automatic far-content or continuity judge** (risk): the pairwise VLM judge is untested; the user's eye is the
  only judge; the blind test is not taken yet.
- **Single middle key vs motion** (question, agent): see Opinion; needs the queued numbers.
- **Hidden cuts in action shots** (risk): TransNetV2 misses smear cuts; per-shot plates and video windows can span a
  cut. Scenes were checked by eye; the product needs a better cut detector.
- **Effects at the edges** (lightning, beams, flashes): hard for any model; seen in jjk-edge-move and jjk-action.
- **Licence** (question, user): Qwen-Image-2.1 is research-only; the plate winner cannot ship as is. Apache-2.0 options:
  Wan VACE (1.3B, VACE-Fun).
- **Memory and disk** (risk): the fix holds on the runs so far; C: 50 GB free.
- **Invention level** (question, user): completing cut-off subjects (the monster's body, the ponytail girl) not yet
  decided by eye.
- **3 monitors** (question, user): about 2x the generation cost at the same resolution and more invention far out;
  could be held near today's cost by lower resolution far out and plates there. Test at s = 1.0 offered, not run.

## Exact next actions

1. **First safe step**: check whether the runner and ComfyUI still run (`ps -ef | grep run-queue`, `tasklist | findstr
   python`, last lines of `out/runs/queue/runner.log`). If they died, resume as described in Current state.
2. When the two fight demos exist (`out/demo/<stamp>-scroll`, `<stamp>-a14b-key`): check their motion numbers, then ask
   the user to watch `play-compare-matched.bat` / `-fill.bat` and pick: still plate (`20261013-100301`) vs scroll vs 14B.
3. When step 15 is done: sheets `runs/sheets/jjk-*-vace-a14b-key-s0.5.png`, whole-clip scores, motion check; compare
   with the plate (`scores-seam.json` fit+live), the scroll (`scores-scroll.json`) and 1.3B. Write synthesis 8.5.
4. Test a key that does not pin the motion, on jjk-action and jjk-pan: (a) the plate as `--refs`-style reference
   (WanVaceToVideo `reference_image`) instead of a kept frame; (b) keys at several frames; (c) scroll-rendered keys.
   About 1 h per scene with the 14B; start with the cheapest arm.
5. Extend `route-by-motion.py` to three routes: still -> plate, confident camera slide -> scroll, other motion -> video.
6. Rebuild the blind test after the jjk candidates exist; the user takes it on the external monitor (F11).
7. Then the baseline declaration, and the speed phase plan (distillation, routing, resolution by distance).

## Source pointers

- Plan: `plans/261012-1639-ai-surround-quality-baseline/plan.md` (section "Round 2")
- Synthesis: `plans/reports/research-261012-1521-ai-surround-anime-outpainting-synthesis.md` (sections 8.4, 8.5)
- Reports A and B: `plans/reports/researcher-261012-1639-*.md` (A: runners, AnimeGen graft facts)
- Previous handoff: `plans/handoffs/ai-surround-quality-baseline-20261012-2110.md`
- Code: `spikes/ai-surround/` (see Work performed); queue `spikes/ai-surround/out/runs/queue/`
- Film: `C:\Users\tamla\Downloads\Jujutsu.Kaisen.S03E12.FiNAL.MULTi.1080p.WEBRiP.x265-KAF.mkv`
- Memory: `~/.claude/projects/C--Users-tamla-Downloads-Telegram-Desktop-panorama/memory/`
- Models: https://huggingface.co/QuantStack/Wan2.2-VACE-Fun-A14B-GGUF,
  https://huggingface.co/Comfy-Org/Qwen-Image-2.1, https://huggingface.co/ausboss/Qwen-Image-2.1-Outpaint-LoRA
