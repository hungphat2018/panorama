---
handoff-version: 1
generated: 2026-10-11T08:47:36Z
generator: ak:handoff@2.0.0
focus: "panorama M1 session 2026-10-11: plan 261011-0554-m1-architecture-and-technology; stop before spike P1; write to plans/handoffs/"
workspace: C:/Users/tamla/Downloads/Telegram Desktop/panorama
branch: main
head: 7150fbd
---

# HANDOFF: M1 architecture design, stopped before spike P1

## Mission and current status

Focus: "panorama M1 session 2026-10-11: plan 261011-0554-m1-architecture-and-technology; stop before spike P1; write to plans/handoffs/".

Outcome wanted (roadmap M1): every hard-to-change architecture/technology choice for panorama made on evidence, then proven in a thin prototype on Setup A (laptop 2560x1600 240 Hz + external EGM22F100H 1920x1080 100 Hz over HDMI, RTX 5070 Ti Laptop, Windows 11 25H2). The user asked to "start on M1 architecture design" with freedom to research, download and pull; then asked to stop before P1 and write this handoff.

Done:
- M1 plan scaffolded with the `ak plan` CLI: `plans/261011-0554-m1-architecture-and-technology/` (plan.md + 8 phases, one per roadmap question), pinned with `ak plan use`, `ak plan validate` passes.
- Four research reports (presentation/sync, performer engine/picture path, film analysis, room model/display settings), one red-team review (verdict: go with changes, folded in).
- Spikes: per-monitor presenter (raw DXGI), seam tuner (by eye), custom refresh modes 96/72 Hz (saved in the driver), seam-offset threshold clip (rendered, not yet run by the user), P0 performer toolchain.
- Decisions: ADRs 0020-0024 written; 0005/0009/0010/0012/0014/0016 marked refined; PRD and roadmap updated; architecture design report written.

Remaining (M1):
- P1: two-window presenter on libplacebo (brief ready below, not launched).
- P2-P6 (pan mode per refresh, cues + seek latch, picture-path quality/cost, audio, subtitles), phase 03/05/06/07 work, phase 08 thin prototype.
- By-eye tests: film-step crossings (forced choice), seam tuner v2 (forced choice for gap/height/lag), optional threshold test.
- User decisions and inputs listed under Open risks.

Urgency: normal; the user is engaged and runs by-eye tests when asked.

## Scope and guardrails

Workspace: `C:/Users/tamla/Downloads/Telegram Desktop/panorama` (path contains a space: quote it).

In scope: M1 plan phases 01-08, spikes under `spikes/`, reports under `plans/reports/`, ADRs under `docs/adr/`, plan files under `plans/261011-0554-m1-architecture-and-technology/`.

Out of scope: building the concept demo (M2), commits (the user has not asked; everything is untracked), changes to the user's Claude config.

Constraints (user and project rules):
- Recommend only freshly verified state of the art (ADR 0006); frame research as invariant, then levers, then techniques (ADR 0019); explain film terms plainly with an example.
- ADR 0017: agents may change *how* (write a new ADR, tell the user after); *what the viewer sees* changes only with the user's OK. ADR 0020 (zero delay across a bezel) is the user's own requirement.
- By-eye checks: concrete on-screen anchor, exact pass test, prefer a forced choice between two settings; software adjusts, the user only judges (memory note updated today). "Adjust until the lines look straight" failed today ("unclear what to judge").
- Markdown only in `plans/` or `docs/`.

Safety boundaries:
- Warn the user before any full-screen takeover of both screens; keep runs short; Esc must stop every spike window.
- Display-mode changes: the user approved custom 96/72 Hz tests (done). Make refresh changes from the long-lived process with `CDS_FULLSCREEN` (Windows restores on exit, ADR 0023); never `CDS_UPDATEREGISTRY`.
- A `scout-block` hook blocks Bash commands containing the word "build"; compile with the `compile.bat` files run from the PowerShell tool (they call `VsDevCmd.bat` of VS 2022 Community). Do not edit `.ckignore`.
- Downloaded files are untrusted: separate folders, `python -I` for anything reading them.

## Current state

Branch: main
HEAD: 7150fbd (first commit; only README.md is committed)
Working tree: untracked `docs/`, `plans/`, `spikes/`, `tools/` (observed with `git status --short`)
Changed files: none tracked
Untracked files: `docs/`, `plans/`, `spikes/`, `tools/` (all of this session's and earlier sessions' work)
Intentional local modifications: yes

State outside the repo (observed this session):
- MSYS2 UCRT64 installed self-contained at `C:\Users\tamla\msys64` by spike P0 (no PATH/registry change).
- NVIDIA driver custom modes 96 Hz and 72 Hz saved for `\\.\DISPLAY2` (EGM22F100H), fast-scan timings (native pixel clock, longer vertical blank). Windows lists 50/59/60/72/75/96/100 Hz. Remove with `spikes/custom-refresh/bin/custom-refresh-mode.exe delete DISPLAY2 96` (and 72) if the user asks.
- Displays at native rates: DISPLAY1 240 Hz (laptop, main display, 150% scaling), DISPLAY2 100 Hz.
- Laptop backlight 44% (matched by eye, unchanged).
- No spike processes left running (checked with `tasklist`).
- mpv and libplacebo master clones (d94d095, 1dd0226, 2026-10-10) live in the session scratchpad and may be gone in a new session; the P0 report has the build commands and its binaries are in `spikes/performer-p0/bin/`.

## Decisions and rationale

| Decision | Rationale | Alternative rejected | Reference |
|---|---|---|---|
| Zero felt delay when something crosses a bezel, as seen (light, not just signal) | user's requirement after feeling a delay in spike A | a visibility threshold | `docs/adr/0020-motion-crosses-a-bezel-with-no-delay.md` |
| One borderless window + flip swapchain per monitor; film clock = master monitor's refresh count; followers on nearest refresh, lag-corrected, aimed at the crossing row; aimed start picks the best alignment class; epochs at breaks, hysteresis, device per adapter | measured: both windows in hardware overlay, even pace, offset 0.16-0.39 ms with aimed start | one spanning window (composed on the main display's clock, −1.7..+6.7 ms) | `docs/adr/0021-one-window-per-monitor-one-film-clock.md` |
| Own C11 performer on libplacebo + FFmpeg + libass + WASAPI; D3D11 first; stock mpv as baseline/per-file fallback; mpv core + own video output as fallback | mpv VO single-window/single-swapchain; libmpv render API GL-only; P0 built and played | mpv fork, GStreamer, libmpv hybrid, VLC 4 | `docs/adr/0022-own-performer-on-libplacebo-and-ffmpeg.md` |
| Display changes held by the performer with `CDS_FULLSCREEN` + snapshot; guardian only for persistent changes | kill test: `CDS_FULLSCREEN` reverted 0.45 s after kill; flags 0 / `SetDisplayConfig` without save did not | watchdog-only design | `docs/adr/0023-display-changes-held-by-the-player.md` |
| Film analysis = separate Python worker (FFmpeg NVDEC + ONNX Runtime CUDA EP + OpenCV), raw per-frame signals + derived tables, timestamps + source pixels, crops by per-shot union snapped to vocabulary | measured ~1 min/episode, ~5 min/film, no playback drops; 0.00% picture cut on synthetic bars | DirectML, in-process analysis, frame-number keys | `docs/adr/0024-film-analysis-worker-and-content-model-shape.md` |
| Custom modes keep the native pixel clock and lengthen vertical blank | scan stays 9.7 ms (vs 10.1/13.5/20.2 ms with lower clocks) | same-totals recipe (kept as plan B) | red-team review, `spikes/custom-refresh/` |
| Brightness match before tuning lag | perceived timing shifts ~15 ms per tenfold luminance [K] | tune lag first | red-team review |
| No VRR on Setup A | user: no G-SYNC page in NVIDIA Control Panel; driver: not possible | VRR follower (spike B) | phase 01 file |

## Work performed

- Read all prior docs, ADRs 0001-0019, all reports; created the M1 plan via `ak plan create` / `add-phase`; wrote plan.md and 8 phase files.
- Launched 4 researchers, 1 red-team (kongming), 1 developer (P0); all finished.
- `spikes/seam-threshold/`: forced-choice seam-offset test (40 trials, 4.2/8.3 ms, card and pan), rendered `trial-clips/seam-threshold-trials.mkv` (295 MB) + key; runner scores answers. Generator later changed to balance answer positions (current render is unbalanced in the pan conditions, still valid for "visible").
- `spikes/per-monitor-presenter/`: `per-monitor-presenter.cpp` (modes own/nearest/follow, film clock locked to master refresh count, aimed start, `--refresh DISPLAYn=HZ` via `CDS_FULLSCREEN`), `seam-tuner.cpp` (smooth/film-step pattern, row timing, gap/height/lag keys, on-screen help, D2D overlay), shared `per-monitor-common.h`, `compile.bat`, `run-presenter-trials.py`, `analyze-per-monitor-pacing.py`; results folders `results-2026-10-11*`; user's tuner saves `seam-settings-*.json` (all defaults).
- `spikes/custom-refresh/`: `custom-refresh-mode.cpp` (NVAPI list/plan/try/delete), vendored NVAPI headers (MIT), `compile.bat`; tried and saved 96 and 72 Hz with the user confirming.
- `spikes/mixed-rate-pacing/display-refresh.py`: fixed `CDS_TEST` constant (was 0x4 = `CDS_FULLSCREEN`).
- Researchers added `spikes/room-probe/room-probe.py` (read-only room report) and `spikes/display-restore/` (kill test, n=1 per method); P0 added `spikes/performer-p0/` (perf0 C11 player, D3D11VA import, benchmarks, LGPL FFmpeg 9.0.2 build).
- Docs: ADRs 0020-0024 new; PRD "Motion" + "Current approach", roadmap M1 status/questions updated; `plans/reports/design-261011-1247-m1-architecture.md`, `spike-261011-1247-one-window-per-monitor.md` written.
- Memory: updated `give-by-eye-checks-a-concrete-anchor`, added `msvc-builds-avoid-build-in-paths`, index updated.

0 redactions applied.

## Verification

| Check | Command | Outcome | When |
|---|---|---|---|
| Per-monitor presenter, 20 fps, 240+100 Hz | `python run-presenter-trials.py results-2026-10-11 --modes own,nearest --fps 20` | both overlay; 12/5 refreshes per frame; nearest offset −0.28 ms | 13:26 |
| 24 fps, 240+96 Hz | `... results-2026-10-11-24fps-96hz --modes nearest,own --fps 24 --refresh DISPLAY2=96` | 10/4 per frame; offset −1.1 ms; 100 Hz restored after | 14:31 |
| Aimed start, 24 fps, 96 and 72 Hz | `... results-2026-10-11-24fps-{96,72}hz-aimed --modes nearest --fps 24 --refresh DISPLAY2={96,72}` | 0.38-0.40 ms (96), 0.13-0.19 ms (72), predicted 0.40/0.12 | 14:37 |
| Custom modes | `custom-refresh-mode.exe try DISPLAY2 96` / `72` | 96.0000 / 72.0000 Hz, user: normal picture, saved | 14:28 |
| Seam tuner by eye | `seam-tuner.exe --out spikes/per-monitor-presenter` | moving: "no delay now"; still alignment: "unclear what to judge" | 14:25 |
| Brightness match | `python spikes/mixed-rate-pacing/match-brightness.py` | equal at 44%, no change | 14:2x |
| Display restore kill test | researcher's `display-restore-kill-test.py` | `CDS_FULLSCREEN` reverted 0.45 s after kill; others persisted | 13:24 |
| P0 playback | see P0 report | D3D11 0 late/0 skipped, 1437/1439 frames at 10 refreshes; Vulkan jittery | ~15:30 |
| Plan format | `ak plan validate plans/261011-0554-m1-architecture-and-technology` | valid | 13:0x |
| Links in docs/plan/reports | inline Python link check | 38 files, 0 broken | 15:3x |

Not run:
- Seam threshold test (needs the user, ~8 min; lower priority since the target is zero).
- Film-step crossing test (not yet built).
- PresentMon cross-check of overlay state (needs admin or Performance Log Users).
- 5-10 min soak runs; Setup B anything; supported-file matrix; P1-P6.

## Open risks and blockers

- Type: risk. Owner: next agent. Impact: Vulkan presenting was jittery with unexplained 300-720 ms hitches in P0; P1 starts on D3D11.
- Type: risk. Owner: next agent. Impact: hardware overlay (independent flip) not yet verified with libplacebo's D3D11 renderer at full screen (P1 checks via `GetFrameStatisticsMedia`).
- Type: risk. Owner: unknown. Impact: scan-speed difference (laptop 3.7 ms vs external 9.7 ms per screen) leaves up to ±3 ms above/below the aimed row; no VRR on Setup A. Idea to test later: laptop custom mode with a slower scan.
- Type: question. Owner: user. Impact: do things moving on their own at film steps show a delay at the bezel? (forced-choice test to build)
- Type: question. Owner: user (what you see). Impact: subtitle lines are wide (median 41% of frame) so nearly every centred line crosses a middle seam: re-wrap, shrink, or move as-is?
- Type: question. Owner: user (what you see). Impact: when no screen can pace the film evenly (25 fps here): which fallback rung first (custom mode, speed nudge ≤ 1%, blending, picture off that screen)?
- Type: question. Owner: user. Impact: 96 Hz (0.39 ms) vs 72 Hz (0.16 ms, fewer slide positions) on the external.
- Type: blocker for analysis spikes. Owner: user. Impact: needs 4-6 films/episodes from the user's library (scope/IMAX, night-heavy, PGS, hardsub in bars).
- Type: blocker for Setup B. Owner: user. Impact: run `python spikes/room-probe/room-probe.py` with Setup B connected; sizes, orientation, gaps.
- Type: risk. Owner: next agent. Impact: licences: MSYS2's FFmpeg package is GPL (use the self-built LGPL 9.0.2); some detector architectures AGPL, some face models non-commercial.

## Exact next actions

1. **First safe step**: read this file, `plans/261011-0554-m1-architecture-and-technology/plan.md` and `plans/reports/design-261011-1247-m1-architecture.md`; check `git status --short` and `python spikes/mixed-rate-pacing/display-refresh.py list` (expect DISPLAY1 240 Hz, DISPLAY2 100 Hz with 72/96 offered).
2. Launch P1 (brief in the next section) to a developer agent: build and windowed smoke test only; no full-screen runs by the agent.
3. When P1 is built: warn the user, then run its full-screen trials yourself at 24 fps with `--refresh DISPLAY2=96` and `=72`, aimed start; analyze with the same metrics as the raw spike (overlay, refreshes per frame, offset, drops, render p99 per monitor). Record in a new spike report and update ADR 0022/phase 02.
4. Build the film-step crossing forced-choice test (real seam, film steps vs smooth, or 0 vs extra offset on the real seam) and the seam tuner v2 (two settings in turn, user picks, software halves the step) for gap, height and lag; run with the user.
5. Ask the user the three what-you-see questions above when convenient, and for the analysis test files.
6. Continue phases 03/05/06/07 per the plan (picture path P4, cue schema v0 + latch P3, room schema v0, display restore triggers), then phase 08.

## P1 brief (ready to delegate)

- Build `perf1` from `spikes/performer-p0/src/perf0-*.c` (C11, MSYS2 UCRT64, libplacebo master, LGPL FFmpeg 9.0.2) in a new `spikes/performer-p1/`.
- One borderless window per monitor (per-monitor DPI aware), our own flip-model waitable DXGI swapchains wrapped with `pl_d3d11_create_swapchain(.swapchain = ...)`, our own `Present1` (P0 verified `--own-present`: 0 late, 0 skipped).
- D3D11 `pl_gpu` is not thread-safe: one device and one render thread servicing both swapchains' waitable objects (or a device per monitor with shared textures); decode once with D3D11VA (P0's import code).
- Scheduling exactly as `spikes/per-monitor-presenter/per-monitor-presenter.cpp`: refresh grids from `WaitForVBlank`, film clock from the master's refresh count, nearest-refresh follower, aimed start (best alignment class); frames picked by PTS (`pl_queue`); `--refresh DISPLAYn=HZ` with `CDS_FULLSCREEN`; Esc quits; `--seconds` auto-exit.
- Picture: a 16:9 picture spanning the seam in fold geometry (gap 20 mm, laptop 344x215 mm, external 476x268 mm, tops level), each monitor rendering its part with `image.crop`/`target.crop` (one resample).
- Logs compatible with `analyze-per-monitor-pacing.py` (vblanks, presents, frame statistics, composition modes) plus render time per monitor.
- Deliver: code, compile script, windowed smoke-test results, report `plans/reports/spike-<date>-performer-two-window-p1.md`.

## Source pointers

- Plan: `plans/261011-0554-m1-architecture-and-technology/plan.md` and `phase-01-*.md` … `phase-08-*.md`
- Design: `plans/reports/design-261011-1247-m1-architecture.md`; review: `plans/reports/red-team-261011-1247-m1-architecture.md`
- Research: `plans/reports/researcher-261011-1247-per-monitor-presentation-and-sync.md`, `...-performer-engine-and-picture-path.md`, `...-film-analysis-content-model.md`, `...-room-model-and-display-settings.md`
- Spikes: `plans/reports/spike-261011-1247-one-window-per-monitor.md`, `plans/reports/spike-261011-1247-performer-toolchain-p0.md`
- ADRs: `docs/adr/0020-*.md` … `docs/adr/0024-*.md`; requirements `docs/project-overview-pdr.md`; roadmap `docs/project-roadmap.md`
- Code: `spikes/per-monitor-presenter/`, `spikes/custom-refresh/`, `spikes/seam-threshold/`, `spikes/performer-p0/`, `spikes/room-probe/`, `spikes/display-restore/`, `spikes/mixed-rate-pacing/`
- Toolchain: `C:\Users\tamla\msys64` (UCRT64); MSVC via `VsDevCmd.bat` (VS 2022 Community)
- Test media (read-only): `C:\Users\tamla\Downloads\[Judas] Nama-Anaru - S01E02.mkv`, `C:\Users\tamla\Downloads\260723_HOTEL_AMAN_43_V3_MAIN_50s_MASTER_16x9_VA_H264.mp4`
