---
handoff-version: 1
generated: 2026-10-12T14:10:00Z
generator: ak:handoff
focus: "AI surround quality baseline (role architecture): painter round done (Qwen-Image-2.1 + outpaint LoRA wins), overnight 14B video queue running, blind test + real-use check next"
workspace: C:/Users/tamla/Downloads/Telegram Desktop/panorama
branch: main
head: a175cd0
---

# HANDOFF: AI surround quality baseline, overnight 14B queue, blind test next

## Mission and current status

Focus: "AI surround quality baseline (role architecture): painter round done (Qwen-Image-2.1 + outpaint LoRA wins), overnight 14B video queue running, blind test + real-use check next".

**Outcome.**
- Establish the quality baseline for extending anime video into the leftover canvas: the best sides we can make today, regardless of speed.
- Build it from replaceable roles, not one model. User, this session: "model is just one of our tools ... do not make the model as an goal ... wrap / work with it / break it down ... could replace anything at anytime".
- Plan: `plans/261012-1639-ai-surround-quality-baseline/plan.md` (concept map, roles R1-R6, acceptance criteria 1-5).

**Done:**
- Role architecture and contract written.
- Motion and colour wraps (R5) built and measured.
- Hand-picked references (R2) tested: whole frames hurt close-ups.
- Best-of-8 judge (R6) tested: pixel signals pick seams only; Qwen3-VL-8B rating does not discriminate.
- Painter round (R3) done on 8 scenes: klein distilled, klein-base, Qwen-Image-2.1 + outpaint LoRA v1/v2, Anima + inpaint LLLite. **Qwen v2 wins**: mean near 10.8 / far 24.4 / seam x1.44, against fast klein 21.4 / 33.0 / x5.7.
- VACE-1.3B keyed by Qwen plates on the 3 story scenes, plus wraps.
- Two research reports, synthesis section 8 (VACE-1.3B) and 8.4 (round 1).
- Memory updated.

**Remaining:**
1. Run the 14B runs (stopped at 21:50 by the user to rest the PC): Wan2.2-VACE-Fun-A14B Q8, keyed by Qwen plates, then plain; 3 story scenes each.
2. Read the rebuilt blind test; the user takes it.
3. Real-use check (plan criterion 5): best recipes on uncut 16:9 frames, by eye on the user's monitors.
4. Declare the baseline in the synthesis.
5. AnimeGen + VACE-Fun graft (needs disk) and routing by motion (plate for still bands, video only for moving ones).

**Urgency:** normal. The overnight queue only lives while this Claude session stays open.

## Scope and guardrails

**Workspace:** `C:\Users\tamla\Downloads\Telegram Desktop\panorama` (git, branch `main`).

**In scope:**
- `spikes/ai-surround/` (code tracked, `out/` ignored)
- `plans/261012-1639-ai-surround-quality-baseline/`, `plans/reports/`, `plans/handoffs/`
- `tools/` (git-ignored): `venv-outpaint`, `ComfyUI_windows_portable` (pinned v0.39.0), `models/comfy/`
- HF cache `~/.cache/huggingface/hub`

**Out of scope:** pre-existing modifications not from this track:
- M1 plan files under `plans/261011-0554-m1-architecture-and-technology/`
- `plans/reports/fullstack-developer-261012-1133-performer-audio.md`
- `spikes/performer-p1/*` (audio/latch changes, deleted latch-test results, `results-audio/*`)

Also out of scope: performer code, ADRs (none until the user's by-eye verdict), commits (only when asked).

**Constraints (user):**
- Quality first, at any cost: art style, ratio, colour, composition. Speed and hardware are information only.
- Big models run locally, Q8/FP8/bf16 + offload.
- Licences: test all, decide later.
- By eye decides: forced choice with the truth as anchor.
- Explain AI and film terms plainly.
- Verify the state of the art before naming a tool.
- Think at concept level: roles, levers, replaceable tools.
- The user lifted the old "no download during a GPU job" rule (17:36).
- The user values anime-only models as a later efficiency lever (memory file).

**Safety boundaries:**
- The real picture is never altered: every output pastes the real centre back (ADR 0001).
- Film content stays local: no hosted pages, no external APIs.
- One heavy GPU job at a time.
- Markdown only under `plans/` or `docs/`.
- Do not delete user files or model caches without asking. Disk is tight.

## Current state

Observed 21:10 (git probes, process logs):

**Git:**
- Branch `main`, HEAD `a175cd0f623b8497b1d1eafdaf360e3edecc7121`, 49 entries in `git status --short` (50 with this file).
- Untracked from this track:
  - `plans/261012-1639-ai-surround-quality-baseline/`
  - `plans/handoffs/ai-surround-anime-outpainting-20261012-{1503,1615}.md`
  - `plans/reports/research-261012-1521-...-synthesis.md`
  - `plans/reports/researcher-261012-1407-*.md` (3)
  - `plans/reports/researcher-261012-1639-*.md` (2)
  - `spikes/ai-surround/`
- All other entries are pre-existing and not ours.
- Intentional local modifications: yes for the track files.

**Update 21:50 (supersedes the queue notes below): nothing runs.** The user stopped all GPU work to let the PC rest. No background jobs, no ComfyUI server, GPU idle (checked).
- The 14B runs, their wraps and the blind-test rebuild move to the next session (next actions, step 1). No 14B scores exist yet: the first scene was stopped twice, at step 5 and step 1.
- Two-screen demo (still-shot recipe on an uncut frame, minute 17:58, shot 287): `spikes/ai-surround/out/demo/20261012-213750/`, with `play-fill.bat`, `play-matched.bat` (mpv, paths relative to the .bat), `preview.png` and `wide-32x9.mp4`.
  - By eye: the room continues, no people added, no visible seam.
  - The user has not yet said which layout feels right.
- Earlier attempt `.../20261012-213102`: a whole-frame caption made each side draw the characters it named, so the main characters appeared two and three times. Fixed: each side's caption now describes only its visible half (`make-wide-demo.py`).

**Update 21:35.** The queue below was stopped for the user's two-screen demo (`make-wide-demo.py`, output `spikes/ai-surround/out/demo/<stamp>/`). It was re-queued as one chain, `out/runs/chain-overnight.log`: A14B keyed by Qwen (3 scenes), then A14B plain (3), then the 14B wraps, then the blind-test rebuild. It starts when `out/demo-run2.log` shows `exit=`; the demo was redone with per-side captions, because a whole-frame caption duplicated the main characters. Expected done about 04:00. Read `chain-overnight.log` instead of `chain-video.log` / `chain-final.log`.

**Running (background jobs of this Claude session, as of 21:10):**
1. `chain-video` (`out/runs/chain-video.log`). Done steps: Anima fixed, seam match for Anima, VLM judge, VACE-1.3B keyed by Qwen. Running: `comfy-vace-clips.py --key-plate qwen21-outpaint-v2` on held-dialogue, edge-person-still and edge-head-closeup (ComfyUI server pid 12752, port 8189). Then `comfy-vace-clips.py` plain on the same 3. Speed about 72 s per step, 50 steps (32 high-noise + 18 low-noise), about 1 h per scene. Expected done about 03:00.
2. `chain-final` (`out/runs/chain-final.log`): waits for `chain-video` `exit=`, then wraps the 14B outputs (live+freeze keyed, freeze plain), then rebuilds the blind test (`out/blind-test/index.html`).

**Disk and memory:**
- C: 23 GB free. The Windows pagefile grew to 30 GB allocated (2.5 GB used) under model memory pressure; it shrinks on reboot.
- RAM about 9 GB free while the 14B model runs.

**Models on disk:**
- `tools/models/comfy/`:
  - `qwen-image-2.1/` (bf16 DiT 14.2, Qwen3-VL-8B TE 17.5, VAE 0.7, LoRAs v1 + v2)
  - `anima/` + `anima-lllite/` (5.7 GB)
  - `wan/vace-fun-a14b/{HighNoise,LowNoise}` (2 x 18.66 GB Q8_0 GGUF)
  - `wan/wan21-repack/` (umT5 fp16 11.4, VAE 0.25)
- HF cache: FLUX.2-klein-4B, FLUX.2-klein-base-4B (transformer 7.75), fal LoRA, Wan2.1-VACE-1.3B. Plus 6 stale `*.incomplete` blobs (1.1 GB) from an older interrupted download.

**ComfyUI install:**
- `tools/ComfyUI_windows_portable` (v0.39.0)
- `extra_model_paths.yaml` points at `tools/models/comfy`
- Custom nodes: `ComfyUI-Anima-LLLite` @ b7495bd, `ComfyUI-GGUF` @ 6ea2651
- `gguf` pip-installed in `python_embeded` and in `venv-outpaint`

**Outputs (`spikes/ai-surround/out/runs/`):**
- Fills per recipe: `qwen21-outpaint-v{1,2}`, `klein-base-green`, `anima-lllite`, `vace-1.3b-key-qwen21-outpaint-v2[-fit][-live-freeze...]` and others.
- Scores: `scores-{klein,seam,vace,wraps,best-of-n,vlm-judge}.json`, `captions.json`.
- By-eye material: `sheets/*-painters-s0.5.png`, `*-vace*-s0.5.png`, `*-n8-s0.5.png`.
- References: `out/scenes/<kind>/refs/`.

## Decisions and rationale

| Decision | Rationale | Alternative rejected | Reference |
|---|---|---|---|
| Work as roles R1-R6 behind one output contract (`runs/<recipe>/<scene>/s0.5.mp4` + `raw-*`, same scorer) | User framing: model is a tool; any role swappable | One-model hunt (14B round first) | plan, memory `frame-research-as-invariants-and-levers.md` |
| R3 lay out = Qwen-Image-2.1 + ausboss outpaint LoRA v2 (ComfyUI, 25 steps, CFG 1, Qwen3-VL caption) | Best on numbers (mean near 10.8, far 24.4, seam x1.44) and by eye: keeps the cut-off person's identity, closes the warning sign as the truth does, no colour seam | klein distilled / base (scale, colour, invented people); Anima + LLLite (darkens sides, invents characters: lacks outpaint training) | synthesis 8.4, `sheets/*-painters-s0.5.png` |
| Still shots: plate alone; video only for what moves | Keyed video reproduces the plate only at the key frame and drifts elsewhere (held-dialogue far 12.4 plate vs 22.4 keyed VACE) | Video everywhere | plan "Routing by motion" |
| R5 default wraps for video: live + freeze; tone only where a cast exists | freeze removes boiling (+0.43 -> -0.03); tone hurts when different content meets at the seam | Always tone | synthesis 8.1, 8.4 |
| R2 references as crops or same-framing shots only | Whole wide frames made klein copy the wide room into close-ups (far 24 -> 78) | Whole neighbouring frames | synthesis 8.4 |
| 14B runner = ComfyUI 0.39.0 + ComfyUI-GGUF, Q8_0, official VACE-Fun mask settings (50 steps, CFG 5, shift 12, switch at 0.875 = step 32) | Runs two experts on 12 GB / 32 GB with streaming; diffusers path had several unverified risks | diffusers GGUF + group offload | report A, `comfy-vace-clips.py` |
| AnimeGen + VACE-Fun graft after plain VACE-Fun | Plain = control; graft needs bf16 sources + about 53 GB peak disk (23 GB free) | AnimeGen first (handoff 1615) | report A |
| Hidden-sides test = development gate; real-use check on uncut frames on the user's monitors before the baseline verdict | Test continues what animators drew; real use invents beyond the true edge, seen in peripheral vision behind bezels | Hidden-sides test as the only gate | plan criterion 5 (user, 18:10-18:12) |

## Work performed

**Code** (`spikes/ai-surround/`):
- New:
  - `apply-wraps.py`: R5 tone / live / freeze / hold, boil score.
  - `pick-references.py`: oracle R2.
  - `best-of-n-plates.py`: R6 seeds + no-truth signals.
  - `vlm-judge.py`: Qwen3-VL rating via ComfyUI.
  - `comfy_client.py`: start / run / stop the pinned ComfyUI.
  - `comfy-plates.py`: Qwen-2.1 v1/v2 and Anima plates.
  - `comfy-vace-clips.py`: A14B via ComfyUI, `--key-plate`, `--refs`.
- Changed:
  - `run-klein-plates.py`: `--refs`, `--prompt`, `--base` with CFG.
  - `run-vace-clips.py`: `--refs`; post-processing moved to `hidden_sides.finish_video_fill`.
  - `hidden_sides.py`: adds `live_seam` and `finish_video_fill`.
  - `seam-match.py`: uses `hs.live_seam`; merges `scores-seam.json` instead of overwriting.
  - `make-blind-test.py`: new GROUPS (Qwen plate, plain / keyed VACE-1.3B, 14B keyed / plain).

**Tools:**
- ComfyUI portable v0.39.0 unpacked.
- `extra_model_paths.yaml` written.
- Two custom nodes cloned (pinned by commit).
- About 95 GB of models downloaded (list in Current state).

**Results** (all [L], hidden-sides test at s = 0.5):
- VACE-1.3B plain:
  - The seam is best of all video tools (x1.84).
  - Far out it invents purple masses.
  - Identity breaks (ponytail -> bob; boy's back -> face).
- klein-keyed VACE + tone/live/freeze, held-dialogue: 12.5 / 19.2 / x1.64.
- Painter means over 8 scenes (key frame):

  | Painter | near | far | seam |
  |---|---|---|---|
  | klein distilled | 21.4 | 33.0 | x5.7 |
  | klein-base | 18.8 | 26.7 | x4.2 |
  | Qwen v1 | 12.3 | 24.8 | x1.49 |
  | Qwen v2 | 10.8 | 24.4 | x1.44 |

  Qwen takes about 170 s per plate.
- Whole clips: the Qwen plate holds on still shots (x1.0-1.4); every plate fails on moving shots (x9-35).
- Qwen-keyed VACE-1.3B + live + freeze:

  | Scene | near | far | seam |
  |---|---|---|---|
  | held-dialogue | 12.7 | 22.3 | x1.68 |
  | edge-person-still | 16.7 | 32.3 | x1.87 |
  | edge-head-closeup | 7.7 | 19.3 | x1.62 |

  Boil -0.01 to -0.03. The Qwen plate alone is better on all three still scenes.
- VLM judge: answers "7" for nearly everything. Its Spearman numbers are tie artefacts; ignore them.

**Failures and fixes:**
- Queued `--suffix -full` failed in argparse; re-run as `--suffix=-full`.
- `hf download --exclude a b` treated `b` as a file; repeat `--exclude`.
- Monitor pipelines need line-buffered stages (no `tr -u`, no plain `cut`).
- Anima with black sides gave dark ghost sides; fixed to kohya's own workflow (img2img, denoise 0.8, blur-prefilled).

0 redactions applied.

## Verification

| Check | Command | Outcome | When |
|---|---|---|---|
| Previous handoff's Current state | git probes, process list, logs | held; queue intact; one queued step failed (argparse) | 16:35 |
| Script syntax after each edit | `python -I -c "import ast; ast.parse(...)"` | ok for all changed / new scripts | 16:50-21:00 |
| Painter runs | `comfy-plates.py --recipes qwen21-outpaint-v2,qwen21-outpaint-v1,anima-lllite`; `run-klein-plates.py --base` | exit 0; sheets viewed by eye (action, edge-head-closeup, edge-person-still, held-dialogue) | 18:13-19:36 |
| VACE-1.3B keyed by Qwen | `run-vace-clips.py --key-plate qwen21-outpaint-v2` | exit 0; scores read | 21:00 |
| 14B runner smoke | first `comfy-vace-clips.py` scene | workflow accepted; experts load (3.8 GB in VRAM, 14.3 GB streamed); sampling at about 72 s/step | 21:08 |

Not run:
- 14B results (pending overnight).
- Blind test by the user.
- Real-use check (uncut frames).
- Cut-sequence consistency test.
- AnimeGen graft, AniSora, LTX.
- Layered pilot (parked).

Known failures: the VLM rating judge does not discriminate; Anima + LLLite darkens sides.

## Open risks and blockers

- **14B runs not done.** Type: blocker. Owner: agent. Impact: the 14B rows of the baseline and the blind test wait for a 6-7 h GPU run. The user paused GPU work at 21:50. Run it with the user's OK (next actions, step 1). Background jobs die if the Claude session closes.
- **Disk.** Type: risk. Owner: agent. Impact:
  - 21:25: the user approved and Claude deleted Anima + LLLite, klein-base-4B (its unique transformer) and the 6 stale VACE-1.3B blobs. C: has 37 GB free.
  - The pagefile is 30 GB allocated and shrinks on reboot.
  - The AnimeGen graft needs about 53 GB at peak: reboot after the overnight queue, or graft one expert at a time.
- **Test set.** Type: question. Owner: user. Impact: 4 of 8 anchors are opening/ending/title/eyecatch graphics, and the episode has almost no story shots with motion at the edges. Video recipes and motion routing need an action episode.
- **Licences.** Type: question. Owner: user. Impact: Qwen-Image-2.1 is research-only and Anima non-commercial; decide after the tests (user: "test all, decide later").
- **Far-content judge.** Type: risk. Owner: agent. Impact: no automatic judge of far content yet; the user's eye is the only judge. Next designs: pairwise A/B, failure-specific questions, bigger VLM.
- **Invention level and "ratio".** Type: question. Owner: user. Impact: still to be decided by eye. Qwen does not invent people here; klein-base and Anima do.

## Exact next actions

1. **First safe step**: confirm nothing runs: no python under `spikes/ai-surround` or `ComfyUI_windows_portable`; `nvidia-smi` idle.
   - Ask the user which demo layout felt right (fill or matched).
   - Then run the 14B work as one background chain, about 1 h per scene, roughly 6-7 h in all. Run it overnight only with the user's OK. Each step uses `tools/venv-outpaint/Scripts/python.exe -I -X utf8 spikes/ai-surround/...`, with scenes `held-dialogue,edge-person-still,edge-head-closeup`:
     1. `comfy-vace-clips.py <scenes dir> <runs dir> --scenes <3 scenes> --key-plate qwen21-outpaint-v2`
     2. the same without `--key-plate`
     3. `apply-wraps.py ... --fills vace-fun-a14b-q8-key-qwen21-outpaint-v2-fit --sets "live,freeze"`
     4. `apply-wraps.py ... --fills vace-fun-a14b-q8-fit --sets "freeze"`
     5. `make-blind-test.py <scenes dir> <runs dir> spikes/ai-surround/out/blind-test`
   - Per-step speed seen: about 72 s.
2. View the 14B sheets (`sheets/*-vace-a14b*-s0.5.png`) next to the 1.3B ones, then write synthesis 8.5. Questions: does the 14B model drift less from the key plate, and does it invent less far out?
3. Ask the user to take the blind test and paste the results JSON; record it in the synthesis.
   - Layout: the user's two screens differ (laptop 2560x1600 at 150% scaling = DISPLAY1, primary; external 1920x1080 = DISPLAY2, to the right). Do not stretch the test across them.
   - Instead: the external monitor alone, browser fullscreen (F11), both clips side by side on one panel.
4. Real-use check (plan criterion 5).
   - Render: Qwen v2 plate on uncut 16:9 frames (16:9 -> 32:9), per side, with the adjacent half of the picture as context, so the geometry matches the test.
   - Show it as panorama will (ADR 0008): the picture is sized to the canvas height and centred, so on these two screens it straddles the bezel, with the sides on the far left of the laptop and the far right of the external. Use the performer if it can play a surround video, else mpv (`tools/mpv-*`) spanning both screens; note the size and DPI mismatch.
   - The user does a forced choice between recipes.
   - The user will bring an action-heavy episode next session (promised 21:20) for moving story shots.
5. Declare the baseline in the synthesis: best recipe per scene type (still: Qwen plate; moving: keyed video + live + freeze), weakest role, next lever.
6. Ask about disk deletions, then build the AnimeGen + VACE-Fun graft (report A) and run it on the same 3 scenes. Then the routing-by-motion wrap and an action episode for moving story shots.

## Source pointers

- **Plan:** `plans/261012-1639-ai-surround-quality-baseline/plan.md`
- **Synthesis (read sections 8-8.4):** `plans/reports/research-261012-1521-ai-surround-anime-outpainting-synthesis.md`
- **Reports:**
  - `plans/reports/researcher-261012-1639-reference-conditioned-painters-and-big-model-runners.md` (A: painters, 14B runners, download list)
  - `plans/reports/researcher-261012-1639-anime-references-layer-split-and-structure-guides.md` (B: references, layer split, structure guides)
- **Previous handoff:** `plans/handoffs/ai-surround-anime-outpainting-20261012-1615.md`
- **Code:** `spikes/ai-surround/` (`hidden_sides.py`, `comfy_client.py`, `comfy-plates.py`, `comfy-vace-clips.py`, `run-vace-clips.py`, `run-klein-plates.py`, `apply-wraps.py`, `best-of-n-plates.py`, `vlm-judge.py`, `pick-references.py`, `seam-match.py`, `make-blind-test.py`)
- **Runner:** `tools/ComfyUI_windows_portable/ComfyUI/extra_model_paths.yaml`; server logs `out/runs/comfy-server*.log`
- **Memory:** `~/.claude/projects/C--Users-tamla-Downloads-Telegram-Desktop-panorama/memory/` (`frame-research-as-invariants-and-levers.md`, `ai-surround-quality-first-speed-later.md`)
- **Models:**
  - https://huggingface.co/Comfy-Org/Qwen-Image-2.1
  - https://huggingface.co/ausboss/Qwen-Image-2.1-Outpaint-LoRA
  - https://huggingface.co/QuantStack/Wan2.2-VACE-Fun-A14B-GGUF
  - https://huggingface.co/circlestone-labs/Anima
  - https://huggingface.co/kohya-ss/Anima-LLLite
  - https://huggingface.co/black-forest-labs/FLUX.2-klein-base-4B
