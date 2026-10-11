# Roadmap

Living document, last reviewed 2026-10-11. Milestones are ordered by what they unblock, not by date. Each names its goal, the questions it must settle, and how we know it is done. How to get there is up to the agents doing the work ([ADR 0017][0017]). What panorama must achieve: [requirements](project-overview-pdr.md).

Architecture and technology come first: they are hard to change once code is built on them, while implementation and bug fixes are easy to do at the end ([ADR 0019][0019]).

## Status

| Milestone | Status |
|---|---|
| M0. Foundations | Done |
| M1. Settle the architecture and technology | **In progress** ([plan](../plans/261011-0554-m1-architecture-and-technology/plan.md)) |
| M2. Build the concept demo on Setup A | Planned |
| M3. Demo verdict on Setup A | Planned |
| M4. Demo verdict on Setup B | Planned |
| After the demo | Unordered |

## How every milestone runs

1. Frame each blocking question as the outcome needed and every lever that could reach it, before naming a technique. Don't stop at the traditional approach ([0019]).
2. Build on the best available: check that a tool or technique is still the state of the art before adopting it ([0006]).
3. Settle each question with the cheapest test that can: a spike, a measurement, or a by-eye comparison.
4. Bring by-eye checks to the user with a reference on screen and an exact pass test. Adjust settings from software; the user only judges.
5. Write results as reports in `plans/reports/`. A changed decision becomes a new ADR ([0017]).
6. Give each milestone its own plan in `plans/` when work starts. If a result shows this order is wrong, change it here.

## M0. Foundations (done)

- Vision and the five illusion breakers: [README](../README.md). Decisions: [`adr/`](adr/).
- State-of-the-art research pass, 2026-10-10 ([synthesis]).
- Spikes on Setup A, all on stock mpv:
  - Per-shot changes land on the exact frame ([exact-frame spike]).
  - The cue sheet can be refined while the film plays, with no visible effect ([refinement spike]).
  - One window across 240 Hz and 100 Hz: the main display paces the whole window, the 100 Hz screen stumbles, and pans flicker at the bezel. Smooth camera slides removed the flicker, and the user preferred them on real anime ([0016]). Still open: trials with custom 96 and 72 Hz modes ([mixed-rate spike]).

## M1. Settle the architecture and technology

**Goal:** every choice that would be hard to change later is made on evidence, building on the best available. Code written here may be thrown away.

**Questions to settle:**

- **Pace:** what keeps every screen carrying the picture on an even pace, and both sides of a bezel in step: one window across the screens, one per screen, or something else? Something crossing a bezel must arrive with no felt delay, counting each panel's lag and the real gap ([0020]). Finish the open mixed-rate trials first; the levers are mapped in the [pacing research]. Answered in part on 2026-10-11: one window per monitor with one film clock ([0021]), so the own per-monitor renderer comes in now ([0022]).
- **Motion:** how the picture moves on every refresh during camera slides ([0016]).
- **Picture path:** drawing the whole canvas, including the leftover area, with bezels, per-monitor color, and room for both the fold and the smooth-curve model.
- **Film analysis:** what finds shots, cuts, black bars, text in the bars, faces, dialogue lines and camera slides reliably, in anime and in live action.
- **Plan:** what the cue sheet holds, and how a refined plan reaches the player without changing a shot on screen, even when the viewer jumps around (seek–swap section of the [pacing research]).
- **Room:** what the setup must capture, and Setup B's facts (sizes, orientation, gaps, refresh rates), so nothing assumes two side-by-side screens.
- **Display settings:** changed settings always come back, even when the player is killed ([0005]).

**Done when:**

- Each question is answered in a report, and every choice it makes is an ADR.
- A thin prototype on Setup A, rough quality allowed, plays a real film from a hand-written cue sheet through the chosen pieces. Measured: changes land on their exact frames, and every frame stays up equally long on the screens carrying the picture. By eye: a slow pan across the bezel shows no flicker and no stumble, and something moving across the bezel shows no delay.

**Needs the user:** approval for display-mode changes, the by-eye checks, and Setup B for its facts.

## M2. Build the concept demo on Setup A

**Goal:** the demo's whole scope runs on Setup A, built on M1's choices: setup with on-screen alignment and color matching, film analysis and planning from the file alone, the surround, subtitles and smooth slides, and a switch to the plain stretched picture for comparison. Build only to the quality the verdict needs.

**Done when:**

- Measured on a full episode and a full film: per-shot changes land on their exact frames; no shot changes its look on screen, also while jumping around; crops change only at cuts; dialogue never crosses a bezel; text in the bars is never cropped; analysis never causes a dropped frame; display settings always come back.
- By eye: a diagonal line crossing the bezel lines up on both sides, as if it ran straight on behind it; on grey bars across the bezel, the user can't say which side is brighter; crops don't jump in dark scenes; faces stay off the bezels.

## M3. Demo verdict on Setup A

**Goal:** pass the [demo pass test][0018] on Setup A.

- Fix the hard-scene list with the user first. Starting point: the by-eye A/B plan in section 6.3 of the [bezels research].
- Make the by-eye choices, each recorded as an ADR: glow or black in the leftover area; fold or smooth curve; whether size changes at cuts feel natural; smooth slides in live action.
- Then compare against the plain stretched picture, then do the full watch.

**Done when:** the pass test holds on Setup A. A lost scene sends its cause back into the work, and back to M1 if the cause is architectural.

## M4. Demo verdict on Setup B

**Goal:** pass the same test on Setup B: three screens, one from a different brand, with mixed orientation or sizes, among them a 60 Hz LG UltraFine 4K.

**Done when:** the pass test holds on Setup B.

## After the demo

Unordered; each starts with its own decision.

- Polish and bug fixes.
- License and first public release.
- Own per-monitor renderer: brought into M1 ([0022]); after the demo, its polish.
- A setup that stays simple on anyone's 2–3 monitor desk ([0011]).
- A smarter crop preset, decided per scene. How much it may cut is open.
- macOS ([0003]).
- Streaming sources ([0004]).

[0003]: adr/0003-windows-first-macos-later.md
[0004]: adr/0004-local-files-only-for-now.md
[0005]: adr/0005-app-may-change-display-settings-during-playback.md
[0006]: adr/0006-build-on-top-of-the-state-of-the-art.md
[0011]: adr/0011-setup-automatic-detection-and-manual-alignment.md
[0014]: adr/0014-architecture-plan-ahead-into-a-cue-sheet-perform-live.md
[0016]: adr/0016-camera-slides-play-smooth.md
[0017]: adr/0017-agents-may-change-how-not-what-you-see.md
[0018]: adr/0018-concept-demo-pass-test.md
[0019]: adr/0019-settle-architecture-and-technology-first.md
[0020]: adr/0020-motion-crosses-a-bezel-with-no-delay.md
[0021]: adr/0021-one-window-per-monitor-one-film-clock.md
[0022]: adr/0022-own-performer-on-libplacebo-and-ffmpeg.md
[synthesis]: ../plans/reports/research-261010-1922-sota-research-synthesis.md
[exact-frame spike]: ../plans/reports/spike-261010-2345-exact-frame-cue-switching-in-stock-mpv.md
[refinement spike]: ../plans/reports/spike-261011-0950-refining-the-cue-table-during-playback.md
[mixed-rate spike]: ../plans/reports/spike-261011-1034-one-window-across-mixed-refresh-rates.md
[pacing research]: ../plans/reports/research-261011-1000-frame-pacing-judder-and-seek-swap-concepts.md
[bezels research]: ../plans/reports/researcher-261010-1846-bezels-perception-and-geometry-model.md
