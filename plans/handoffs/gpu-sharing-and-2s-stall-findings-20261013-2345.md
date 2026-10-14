# Handoff: GPU sharing and the 2-second stall (from AI surround live path, phase 2)

## Mission and current status

Investigate three findings from phase 2's screen runs (2026-10-13 night) and find their causes:

1. **ComfyUI's painter can't share the GPU with the player.** Capping its VRAM, as planned, made it worse: about 40 wrong frames a minute, against 23 uncapped. Probable cause: it now streams model weights at every step. Phase 3 already requires its painter to fit in VRAM without streaming, tested on this same measurement.
2. **The player's own dropped frames come from a 2-second system timer, not from GPU clock changes.** Almost all of them land at the same point of a 2-second cycle of the system clock (QPC), with or without AI. E1's first no-load run shows it too (31 of 35 miss events). The cause sits outside the player: a driver, a laptop service, or possibly the nvidia-smi logging in our own test scripts.
3. **The 12.6 ms copy chunk in the windowed run was that same stall**, not the canvas copy itself (without the painter the longest chunk is 1.3 ms). The planned "start copying one refresh later" change was dropped.

Done: the measurements, the attribution, and the phase-2 report. Remaining: name the stall's owner and remove it or work around it (item 2), and confirm or refute the weight-streaming explanation (item 1). Item 3 needs nothing unless item 2's answer changes.

**No-nvsmi run done (23:42-23:44, `spikes/ai-surround/out/e1/20261013-233940/none-lat1-default`):** no load, no nvidia-smi logging, 120 s.
- 240 Hz: 4.6 wrong / 23.0 missed per min. 96 Hz: 2.0 / 2.6.
- Compare: 3.1 / 6.6 and 0.5 / 0 in the 22:15 run with nvidia-smi.
- The misses are **no longer on the 2-s grid**: 14 events at 240 Hz, spread over phases (at most 4 at one phase, 0.8 s). There were no long renders.
- Reading:
  - The phase-locked stalls go away without nvidia-smi, so nvidia-smi's polling (or the driver work it triggers) is what made them 2-second periodic.
  - But misses overall did not drop: they rose, scattered.
  - Possible cause: without the 10 Hz queries the GPU idles into lower power states more often, and the player then misses at clock or P-state changes (E1's original theory). This could not be checked, because no clock log ran.
- One 2-min run is noisy: E1's first no-load run had 23.5 wrong a minute with nvidia-smi on. Treat this as a lead, not a verdict.

Priority: item 2 is M1-track work (it affects all playback, with or without AI). Item 1 belongs to phase 3 of `plans/261013-0718-ai-surround-live-path/`.

## Scope and guardrails

- Repo: `C:/Users/tamla/Downloads/Telegram Desktop/panorama` (path has a space; quote it). Windows 11, laptop RTX 5070 Ti Laptop (12 GB), two screens: DISPLAY1 2560x1600 at 240 Hz, DISPLAY2 1920x1080 run at 96 Hz.
- User rules:
  - **Screen runs take over both screens: ask the user before each one.**
  - Let the PC rest between heavy runs (the runners have `--rest`, default 180 s).
  - Never TaskStop a running screen-run driver: it orphans perf1, which then fails with 0xC0000142. Let it finish, or kill the process tree during a rest.
  - A hook blocks any Bash command containing the word "b-u-i-l-d" (without dashes). Compile perf1 with `spikes/performer-p1/compile.ps1` from PowerShell.
  - Ask the user when blocked or unsure; explain plainly.
- Do not change what the viewer sees without the user's OK (ADR 0017).
- Do not commit unless asked. The working tree has many uncommitted changes from other tracks (88 entries); leave them alone.
- Python: `tools/venv-outpaint/Scripts/python.exe -I -X utf8`.

## Current state

- Branch `main`, HEAD `a175cd0`, 88 entries in `git status --short` (phase 2's live-path files are untracked; M1 and other tracks' edits too). All intentional, uncommitted.
- Changed for this investigation: `spikes/ai-surround/e1-player-under-load.py` has a new `--no-nvsmi` switch (no nvidia-smi clock logging while the player plays; the one-shot temperature and busy checks before and after a cell remain).
- Analysis scripts copied to `spikes/ai-surround/out/live/checks/analysis-scripts/`:
  - `miss_phase.py PERF1_DIR...`: missed-refresh events per monitor, histogram of their phase mod 2 s on the QPC clock, and presents whose render CPU took over 2 ms.
  - `attribute_timing.py SMOKE_RUN_DIR`: classifies wrong frames and missed refreshes as seek, worker kill/restart, clock event, long render or other.
  - `presents_around.py SMOKE_RUN_DIR T...`: per-present rows around given times (Present1 duration, counters, GPU and CPU ms).
  - `showings_why_glow.py SMOKE_RUN_DIR`: AI-sides showing analysis (not needed here).
  - The scripts hard-code the repo path to import `spikes/ai-surround/e1-analyse.py`.

## Decisions and rationale

- ComfyUI's painter is out of the live path's GPU sharing: phase 3 must ship a painter with fixed allocations that fit its VRAM budget, measured on E1's painter cell (target: at or below no load). Recorded in `plans/261013-0718-ai-surround-live-path/phase-03-painter-at-speed.md` and ADR 0028.
- The copy path stays as it is (item 3).
- The clock lock ("prefer maximum performance") is still M1's lever, but the 2-second stall is the bigger share of the player's misses.

## Work performed

Runs (all with `perf1-p3-ai.exe`, player `--vram-reserve-mb 1024`, DISPLAY2 at 96 Hz):

| Run | Folder (under `spikes/ai-surround/out/`) | Result |
|---|---|---|
| E1 no load, 120 s | `e1/20261013-221533/none-lat1-default` | 240 Hz: 3.1 wrong / 6.6 missed per min; 96 Hz: 0.5 / 0. 6 of 7 miss events at QPC phase 0.7 s |
| E1 painter (ComfyUI `--enable-dynamic-vram --reserve-vram 4`), 120 s | `e1/20261013-221533/painter-lat1-default` | 39.8 / 294.7 (240 Hz), 40.9 / 105.2 (96 Hz) |
| E1 original painter, no cap (earlier today) | `e1/20261013-185619/painter-lat1-default` | 23.0 / 153.2, 18.4 / 52.6 |
| E1 original no load (earlier today) | `e1/20261013-185619/none-lat1-default` | 23.5 / 85.8; 31 of 35 miss events at QPC phase 0.4 s, render stalls 12-18 ms at that phase |
| Live smoke windowed, with painter, 120 s | `live/smoke/20261013-222650-windowed` | one copy chunk 12.6 ms; render CPU 12.8 ms at 95.27 s, QPC phase 0.667 |
| Live smoke full screen, no painter, 600 s | `live/smoke/20261013-223232-fullscreen-nopaint` | outside seeks 1.2 / 6.9 (240 Hz), 0.6 / 0.5 (96 Hz); 40 of 43 miss events at QPC phase 0.7 s |

Stall evidence (full-screen smoke):
- At 425.88 s from launch, Present1 blocked 19.4 ms on DISPLAY1 and 18.8 ms on DISPLAY2 at the same moment, with render CPU at 0.06-0.09 ms. This was a driver- or system-level block, not the player's work.
- Long renders before Present: 11.3 ms (394 s), 13.4 ms (454 s), 15.1 ms (546 s, DISPLAY2), all at phase 0.66-0.72 s.
- Only about 10-13 % of the 2-s ticks cause a miss, so the stall's length varies.
- No P-state or clock event was within 0.25 s of these misses (nvidia-smi samples every 100 ms).
- The phase stayed fixed across separate player processes 20 min apart, and drifted from 0.4 s (18:56) to 0.7 s (22:15). That points to a long-lived kernel or driver periodic timer, not something started per run (each run starts its own nvidia-smi, which would put the phase at a random point).

Painter evidence:
- With `--reserve-vram 4` on a 12 GB GPU (budget ~11.2 GB, player reserves 1 GB), ComfyUI keeps about 7 GB, below the INT8 DiT's 7.26 GB. Weights therefore likely stream every step, where uncapped they swapped only between the encoder (9.35 GB) and the DiT.
- PCIe traffic was **not** measured, and the player's own reservation changed between the two runs too.
- Plates took 22 s next to the player against 15-16 s alone.

## Verification

- Done: E1 scoring (`e1-analyse.py`) on every run; phase analysis via `miss_phase.py` on the five runs above; per-present inspection at 425.9 s.
- Not done:
  - any run without nvidia-smi (in progress, see Mission);
  - any DPC/ISR latency trace;
  - PCIe or VRAM usage measured during the painter cells;
  - a painter cell with only one of the two rules changed.

## Open risks and blockers

- nvidia-smi might be the stall's cause and also the only clock log. If so, clock data must come from elsewhere (NVML at a low rate, or none) during timing runs.
- The stall may be a laptop vendor service or ACPI battery polling. Fixing that is a system setting, so the user decides.
- Screen runs need the user's OK each time.
- Item 1's confirmation needs a GPU-heavy run with ComfyUI. Ask first, and rest the PC.

## Exact next actions

1. **First safe step:** read the no-nvsmi result above, then plan a paired test (user OK, rests between):
   - same session, no load, 3-5 min each, alternating with and without nvidia-smi;
   - ideally also with the NVIDIA "prefer maximum performance" setting for perf1, which takes the P-state changes out of the picture;
   - log clocks without NVML stalls if possible (e.g. 1 Hz instead of 10 Hz, or `nvidia-smi dmon` at a low rate) to see whether its rate matters.
   - Goal: separate "nvidia-smi causes 2-s stalls" from "P-state changes cause misses when the GPU idles".
2. Ask the user to run LatencyMon, or record a WPR/WPA trace (CPU usage + DPC/ISR, `wpr -start GeneralProfile -start CPU`), for 2 min of no-load playback. Find the driver or ISR that runs every 2 s. Candidates: ACPI battery (`ACPI.sys`), the vendor control centre's EC polling, `nvlddmkm.sys`, Wi-Fi.
3. If a culprit is found, test the fix with one more no-load cell (user OK).
4. For item 1 (optional, phase 3):
   - repeat the painter cell with only the painter cap (no player reservation), then only the player reservation;
   - log VRAM and PCIe from a low-rate monitor that does not itself stall (see step 1);
   - this confirms or refutes weight streaming.
5. Write the result into a report in `plans/reports/` and update `plans/261013-0718-ai-surround-live-path/plan.md` "Where things stand" plus the M1 plan if the stall is M1's.

## Source pointers

- Phase-2 report with all numbers: `plans/reports/implementation-261013-2012-live-skeleton-phase2.md` (section "Screen runs").
- E1 report: `plans/reports/spike-261013-1950-e1-player-under-ai-load.md`.
- Plan: `plans/261013-0718-ai-surround-live-path/plan.md`, `phase-03-painter-at-speed.md`; ADR `docs/adr/0028-ai-sides-worker-feeds-the-player-through-a-side-store.md`.
- Runners: `spikes/ai-surround/e1-player-under-load.py` (cells, `--no-nvsmi`), `spikes/ai-surround/live-smoke.py`, scoring `spikes/ai-surround/e1-analyse.py`.
- Player timing code: `spikes/performer-p1/src/perf1-render.c` (render CPU = time before Present1; Present1 call/return logged per present), `perf1-gpu-share.c`.
- Run logs: `spikes/ai-surround/out/live/checks/phase2-screens.log`, `no-nvsmi-run.log`.
- Analysis scripts: `spikes/ai-surround/out/live/checks/analysis-scripts/`.
