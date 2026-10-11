# Pixel-Accurate Immersive Video Canvas

Working name: **panorama**

A video playback system that turns multiple monitors into one seamless, ultra-immersive viewing canvas for movies and anime, with intelligent black-bar handling and precise image placement.

The expected result is a custom immersive movie/anime player — or a combination of existing tools — that makes a multi-monitor setup feel like a single, carefully calibrated panoramic screen.

This is a long-term project. Quality and practical usability come first.

- **Focus:** the architecture and the algorithm, or model, of the multi-screen concept.
- **Who it's for:** almost anyone with a multi-monitor setup, not just the author's own. It adapts to each setup.
- **Sources:** local files only for now ([ADR 0004](docs/adr/0004-local-files-only-for-now.md)).
- **Published:** for free, non-commercial. The exact license is decided once the concept demo is finished.
- **Platform:** Windows first, macOS later ([ADR 0003](docs/adr/0003-windows-first-macos-later.md)).
- **Approach:** assembling existing tools is fine and encouraged; the real enemy is making the goal harder instead of achievable ([ADR 0002](docs/adr/0002-assemble-existing-tools-first.md)). But this is not a middleman that uses everything as given: existing techniques have their own weaknesses, and the project builds on top of the best available — the state of the art — to break them ([ADR 0006](docs/adr/0006-build-on-top-of-the-state-of-the-art.md)). Architecture and technology are settled first, at concept level; implementation and bug fixes come later ([ADR 0019](docs/adr/0019-settle-architecture-and-technology-first.md)).

## Key design principle

**Maximize immersive screen usage without sacrificing image fidelity.**

This is not about stretching a movie until it fills every monitor. The system intelligently decides what can be cropped, what should remain untouched, and how the resulting image is mapped across the physical displays.

Ideal framing: crop none of the real picture. Only black bars are removed, the video is never distorted, and it keeps its composition ([ADR 0001](docs/adr/0001-crop-none-of-the-real-picture-by-default.md)).

Picture sizing combines three things: the no-crop default, a surround effect in the leftover area, and an optional crop preset ([ADR 0008](docs/adr/0008-picture-sizing-no-crop-surround-optional-crop-preset.md)). The picture's size can change too, with the same timing as its position ([ADR 0015](docs/adr/0015-picture-size-may-change-at-cuts.md)).

## What success feels like

A 2–3 monitor setup that feels just like one screen, perceived as one video. It is hard to describe exactly, but that is the goal.

Each of these breaks the illusion, making it feel like different screens playing different videos. In priority order:

1. Bezels cutting through faces
2. Color or brightness differences between monitors
3. Stutter
4. Crops jumping during dark scenes
5. Subtitles split across screens

Not ranked: setup hassle.

## Multi-screen model

- **Shape:** something around a curved wraparound. It doesn't have to be curved; it depends on how the monitors are placed.
- **Fold or smooth curve:** decided by eye in the concept demo.
  - *Fold:* the picture follows the monitors exactly and bends at the bezels. No seat position needed.
  - *Smooth curve:* an even curve fitted to how the monitors are placed. Needs the seat position.
- **Bezels:** placement first — faces and the picture's centre stay off the bezels, and the picture moves only at cuts, preferring to stay still within a scene. The picture continues behind the bezels; losing 1–3% of image width there is accepted ([ADR 0009](docs/adr/0009-bezels-placement-first-picture-continues-behind-bezel.md)).
- **Subtitles:** dialogue always stays inside one screen, even if that puts it off-centre ([ADR 0013](docs/adr/0013-dialogue-subtitles-stay-inside-one-screen.md)).
- **Viewer:** a single viewer ("just me"), no webcam ([ADR 0007](docs/adr/0007-one-viewer-no-webcam.md)).

## Architecture

Decide ahead, perform live ([ADR 0014](docs/adr/0014-architecture-plan-ahead-into-a-cue-sheet-perform-live.md)).

- **Content model:** what is in the film — shots, black bars, faces, subtitle lines, camera slides. Made once per film; knows nothing about the setup.
- **Room model:** the setup — monitors, sizes, bezels, angles, seat, color, refresh rates. Made once per setup; knows nothing about the film.
- **Planner:** turns both, plus preferences, into a cue sheet: per shot, the crop, the picture's position and size, the subtitle screen and the surround.
- **Performer:** plays the file and applies the cue sheet on the exact frame. It makes no decisions, so nothing slow runs during playback.

## Problems to solve

- **Wasted screen space** — Movies and anime often have black bars around the actual picture. Detect and remove those bars so the picture can use more of the available display area.
- **Monitors behaving like separate screens** — Multiple monitors should function as one continuous viewing surface rather than independent displays.
- **Imprecise image mapping** — Place pixels accurately across monitors, preserving image geometry and avoiding distortion, unintended cropping, and misalignment.
- **Physical monitor bezels** — Ideally, account for the black borders between monitors so the overall viewing experience feels more continuous.

## Expected outcome

- **One continuous picture** — A movie or anime spans the combined monitor area as a single image, without awkward gaps in the rendered content or independent playback windows.
- **Intelligent black-bar removal** — Detect letterboxing and pillarboxing dynamically. Expand the usable picture without stretching it, while preserving the option to keep the original framing when cropping would remove meaningful content.
- **Pixel-accurate display mapping** — Respect each monitor's native resolution, physical position, orientation, and scaling. Avoid unintended resampling and alignment errors wherever possible.

## What the software should support

| Capability | Expected behavior |
|---|---|
| Multi-monitor canvas | Treat multiple displays as one logical viewing surface |
| Setup detection | Detect the display setup automatically; what can't be detected, such as bezels, is set by manual alignment on screen ([ADR 0011](docs/adr/0011-setup-automatic-detection-and-manual-alignment.md)) |
| Automatic cropping | Detect black bars and adapt to changing aspect ratios; keep the last confident crop and change it only at cuts ([ADR 0010](docs/adr/0010-crop-changes-hold-last-confident-crop.md)) |
| Aspect-ratio preservation | Never stretch or distort the original image |
| Display mapping | Map the picture precisely to each monitor's pixel coordinates |
| Bezel compensation | Optionally account for physical monitor borders |
| Playback quality | Smooth playback, with camera slides played smooth ([ADR 0016](docs/adr/0016-camera-slides-play-smooth.md)), hardware decoding, and audio/subtitle support |
| Display settings | May change Windows display settings, such as refresh rate, during playback and restores them afterwards ([ADR 0005](docs/adr/0005-app-may-change-display-settings-during-playback.md)) |
| User control | Choose between original framing, crop-to-fill, and other scaling modes |

## Known limits

These need explicit handling rather than assuming the displays can become literally seamless:

- Black-bar detection can be ambiguous during dark scenes.
- Pixel-accurate mapping cannot eliminate the physical gaps between monitor bezels.

## Open problems

Traditional approaches may not be enough for these.

- **Bezels** — one of the most important factors in making it perceived as one screen. The traditional approaches (hide or skip the strip behind each bezel) probably won't solve it. Demo strategy: placement first, with the picture continuing behind the bezels ([ADR 0009](docs/adr/0009-bezels-placement-first-picture-continues-behind-bezel.md)); whether that is enough is judged by eye.
- **Setup** — a guided on-screen setup sounds good, but only if it is simple to set up. Approach: automatic detection plus manual alignment, no phone photo ([ADR 0011](docs/adr/0011-setup-automatic-detection-and-manual-alignment.md)). Keeping it simple is the open part.
- **Black-bar detection** — a short wait before playback is acceptable, but the aim is both worlds: fast start and steady crops. Existing techniques each have their own weaknesses; the project builds on top of them to break those. Policy: keep the last confident crop, change it only with reliable evidence and at cuts, and never crop subtitles or text in the bars ([ADR 0010](docs/adr/0010-crop-changes-hold-last-confident-crop.md)).
- **Crop preset** — a "smarter" crop, decided per scene, would be better if possible. How much it may cut: open.
- **Leftover area** — used for a surround effect ([ADR 0008](docs/adr/0008-picture-sizing-no-crop-surround-optional-crop-preset.md)). The demo compares an edge-color glow against black.

## Concept demo

It must prove that the setup is perceived as one screen: an immersive experience.

- Starting scope: edge-color glow versus black, bezel alignment, color calibration, pre-scan, and crop changes at cuts ([ADR 0012](docs/adr/0012-concept-demo-starting-scope.md)).
- Shows both the fold and the smooth-curve model, so the choice can be made by eye.
- Judged two ways: by eye on hard scenes (a face crossing a bezel, a slow pan, a dark scene), and side by side against a plain picture stretched across the screens. Pass test: [ADR 0018](docs/adr/0018-concept-demo-pass-test.md).

The demo runs on the author's two setups:

- **Setup A:** a laptop screen plus one other screen.
- **Setup B:** three screens, one of them from a different brand, with mixed orientation or screen sizes.

## Requirements and roadmap

- [Product requirements](docs/project-overview-pdr.md): what panorama must achieve and how it is judged.
- [Roadmap](docs/project-roadmap.md): milestones, starting with settling the architecture and technology.

## Decisions

Decisions are recorded as numbered ADRs in [`docs/adr/`](docs/adr/). Agents may change how panorama works when evidence shows something better; what the viewer sees changes only with the author's OK ([ADR 0017](docs/adr/0017-agents-may-change-how-not-what-you-see.md)).
# panorama
