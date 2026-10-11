# Product requirements (PRD)

Working name: **panorama**. Living document, last reviewed 2026-10-11.

This says what panorama must achieve and how we know it does. It does not fix how to build it: the current approach is a best bet that agents improve as they work ([ADR 0017][0017]).

- Vision and background: [README](../README.md)
- Decisions: [`adr/`](adr/)
- Order of work: [roadmap](project-roadmap.md)

## Terms

- **Shot:** one unbroken piece of footage. **Cut:** where one shot ends and the next begins, e.g. a close-up of a face cuts to a wide street view.
- **Bezel:** the black border between two monitors.
- **Black bars:** black strips stored in the video above and below, or beside, the real picture.
- **Leftover area:** the part of the monitors the picture does not cover.
- **Camera slide:** the camera pans sideways or tilts up or down, so the whole picture moves together.
- **Refresh:** one chance for a monitor to show a new picture; a 240 Hz monitor has 240 a second.
- **Hard scene:** a short clip picked because it tends to break the illusion, e.g. a face next to a bezel.

## Problem

Monitors on a desk play a film like separate screens. Black bars waste space, bezels cut through faces, each monitor shows slightly different color, and motion stutters or falls out of step across a bezel. Stretching the film across all screens, the usual way, distorts it and leaves the bezels wherever they fall.

## Goal

A 2–3 monitor setup that feels like one screen playing one video: as much screen as possible, without sacrificing the picture.

## Who it is for

- One viewer at a desk with 2–3 monitors, in any mix of sizes, brands, orientations and refresh rates ([0007]).
- Movies and anime from local files ([0004]).
- Windows first, macOS later ([0003]).
- Free and non-commercial. The license is chosen after the concept demo.

## Success

Each of these illusion breakers makes it feel like separate screens playing separate videos. In priority order:

1. Bezels cutting through faces
2. Color or brightness differences between monitors
3. Stutter, including the two sides of a bezel changing picture at different moments
4. Crops jumping during dark scenes
5. Subtitles split across screens

Not ranked: setup hassle.

Panorama succeeds when none of these is noticed in normal viewing, and the result beats a plain picture stretched across the screens.

## Requirements

What must be true, whatever the implementation. Sources in brackets.

**Picture**

- Never distorted; it keeps its composition ([0001]).
- No real picture is cropped by default; only black bars are removed. The strip hidden behind each bezel, about 1–3% of the width, is the one accepted exception ([0001], [0009]).
- The picture continues behind the bezels, true to where the monitors physically sit: a straight line crossing a bezel lines up on both sides ([0009]).
- Position and size may change, only at cuts. A smaller picture with no bezel through it can win over a bigger one ([0015]).
- The leftover area carries a surround effect, chosen by eye ([0008]).
- Crop-to-fill and other framings are opt-in presets, never the default ([0008]).

**Bezels**

- Faces and the picture's centre stay off the bezels. The picture moves only at cuts and prefers to stay still within a scene ([0009]).

**Color**

- Monitors match in brightness and color: on grey bars running across a bezel, the user can't say which side is brighter ([0012]).

**Motion**

- No stutter: on the monitors carrying the picture, every frame stays up equally long ([0005]).
- Camera slides play smooth along their own path; everything else keeps its drawn or filmed timing ([0016]).
- Both sides of a bezel change picture together. Any flicker at the bezel fails ([mixed-rate spike]).
- Something moving across a bezel arrives on the other screen with no felt delay: the target is zero, as seen, counting each panel's lag and the real gap ([0020]).
- Display settings such as refresh rate may change during playback and always come back, even after a crash ([0005]).

**Black bars**

- Bars are detected, including aspect ratios that change during the film. The last confident crop holds; it changes only with reliable evidence and only at cuts ([0010]).
- Subtitles or other text in the bars are never cropped ([0010]).
- Playback starts after a short wait at most, and analysis never slows playback ([0010], [0014]).

**Subtitles**

- Dialogue stays inside one screen, even off-centre. Signs that the subtitle file places on the picture stay where it puts them ([0013]).

**Changes on screen**

- No shot changes its look while it is on screen. Changes land on the exact frame of a cut, or where the viewer jumps to ([0014], [refinement spike]).

**Setup**

- What Windows reports is detected automatically. Bezels, gaps, alignment, angles and the seat are set by alignment on screen; no phone photo ([0011]).
- Setup stays simple and is saved per setup. A new setup or new preferences never require re-scanning a film ([0014]).

**Basics**

- The viewer chooses the framing: original (the default), crop-to-fill, or other modes ([0008]).
- Hardware decoding, audio, and subtitles.

## Concept demo

The first goal is to prove that a setup is perceived as one screen.

- **Runs on** the author's two setups. Setup A: a laptop screen plus one other screen. Setup B: three screens, one from a different brand, with mixed orientation or sizes.
- **Scope:** edge-color glow versus black in the leftover area, bezel alignment, color calibration, a pre-scan of the file, and crop changes at cuts ([0012]). It also shows both the fold and the smooth-curve model, so the choice can be made by eye, plus size changes at cuts ([0015]) and smooth camera slides ([0016]).
- **Pass test** ([0018]): on a fixed list of hard scenes, the user prefers panorama over the plain stretched picture on every one. Then the user watches one full episode and one film without noticing any illusion breaker.
- **Measured alongside:** per-shot changes land on their exact frames, no shot changes its look on screen, display settings always come back, and dialogue never crosses a bezel.

## Out of scope for now

- Streaming sources ([0004]).
- More than one viewer, head tracking, webcams ([0007]).
- macOS ([0003]).
- A phone photo during setup ([0011]).
- Generating new picture content for the leftover area with AI: far too slow today ([synthesis]).
- Making the bezel itself disappear: no known technique does it. The picture continues behind it instead ([0009]).

## Constraints

- Build on the best available, checked fresh before anything is adopted. Assembling existing tools is fine ([0002], [0006]).
- Research each problem at concept level, not bound by traditional approaches. Architecture and technology are settled on evidence before the build; polish and bug fixes come later ([0019]).
- Keep the license choice open until after the demo ([0014]).

## Current approach

A best bet, not a contract. Agents may replace any of it when evidence shows something better ([0017]), and the earlier the better, since architecture and technology are hard to change once built on ([0019]).

- **Decide ahead, perform live** ([0014]). A content model of the film and a room model of the setup feed a planner. The planner writes a cue sheet: per shot, the crop, position, size, subtitle screen and surround. A performer plays the file and applies the cue sheet on the exact frame, making no decisions of its own.
- **Performer:** our own program on libplacebo and FFmpeg, with one window per monitor and one film clock locked to a master monitor ([0021], [0022]). One window spanning both screens can't make them switch pictures in step ([mixed-rate spike]); one window per monitor did, within half a refresh of the faster screen (±2.1 ms guaranteed on Setup A, 0.28 ms in the test run) ([per-monitor spike]).
- **Fallback and baseline:** stock mpv with a generated shader ([0012]), proven to land changes on the exact frame and to refine the cue sheet while the film plays ([exact-frame spike], [refinement spike]). It stays the demo fallback until the new performer passes its spikes, and the plain stretched picture in comparisons.

## How decisions change

Per [ADR 0017][0017]:

- **How it works** (architecture, tools, renderer, file formats, algorithms): an agent may replace a decision when a spike, a measurement or a by-eye result shows something better. It writes a new ADR with the evidence and tells the user afterwards.
- **What you see** (such as no crop by default, one viewer, dialogue inside one screen, Windows first, local files) and every by-eye verdict: changes only with the user's OK.
- When unsure which kind a decision is, treat it as "what you see".

## Open questions

Settled by eye during the concept demo, each recorded as an ADR:

- Fold or smooth curve.
- Edge-color glow or black in the leftover area, and whether a fill makes a mismatched monitor stand out.
- Whether size changes at cuts feel natural ([0015]).
- Smooth camera slides in live-action films ([0016]).

After the demo: how much a crop preset may cut.

[0001]: adr/0001-crop-none-of-the-real-picture-by-default.md
[0002]: adr/0002-assemble-existing-tools-first.md
[0003]: adr/0003-windows-first-macos-later.md
[0004]: adr/0004-local-files-only-for-now.md
[0005]: adr/0005-app-may-change-display-settings-during-playback.md
[0006]: adr/0006-build-on-top-of-the-state-of-the-art.md
[0007]: adr/0007-one-viewer-no-webcam.md
[0008]: adr/0008-picture-sizing-no-crop-surround-optional-crop-preset.md
[0009]: adr/0009-bezels-placement-first-picture-continues-behind-bezel.md
[0010]: adr/0010-crop-changes-hold-last-confident-crop.md
[0011]: adr/0011-setup-automatic-detection-and-manual-alignment.md
[0012]: adr/0012-concept-demo-starting-scope.md
[0013]: adr/0013-dialogue-subtitles-stay-inside-one-screen.md
[0014]: adr/0014-architecture-plan-ahead-into-a-cue-sheet-perform-live.md
[0015]: adr/0015-picture-size-may-change-at-cuts.md
[0016]: adr/0016-camera-slides-play-smooth.md
[0017]: adr/0017-agents-may-change-how-not-what-you-see.md
[0018]: adr/0018-concept-demo-pass-test.md
[0019]: adr/0019-settle-architecture-and-technology-first.md
[0020]: adr/0020-motion-crosses-a-bezel-with-no-delay.md
[0021]: adr/0021-one-window-per-monitor-one-film-clock.md
[0022]: adr/0022-own-performer-on-libplacebo-and-ffmpeg.md
[per-monitor spike]: ../plans/reports/spike-261011-1247-one-window-per-monitor.md
[synthesis]: ../plans/reports/research-261010-1922-sota-research-synthesis.md
[exact-frame spike]: ../plans/reports/spike-261010-2345-exact-frame-cue-switching-in-stock-mpv.md
[refinement spike]: ../plans/reports/spike-261011-0950-refining-the-cue-table-during-playback.md
[mixed-rate spike]: ../plans/reports/spike-261011-1034-one-window-across-mixed-refresh-rates.md
