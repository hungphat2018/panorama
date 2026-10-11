# 0014. Architecture: plan ahead into a cue sheet, perform it live

- **Status:** Accepted — refined by [0016](0016-camera-slides-play-smooth.md), [0021](0021-one-window-per-monitor-one-film-clock.md), [0022](0022-own-performer-on-libplacebo-and-ffmpeg.md), [0024](0024-film-analysis-worker-and-content-model-shape.md)
- **Date:** 2026-10-10

## Context

Of the five illusion breakers, only stutter has to be handled while a frame is on screen. Faces on bezels, jumping crops and split subtitles can be decided per shot, and color once at setup. Local files ([ADR 0004](0004-local-files-only-for-now.md)) put the whole film in reach before it plays.

Crop, placement and subtitle position depend on each other: a crop change rescales the picture and moves faces relative to the bezels. The renderer will also change over time: stock mpv for the concept demo ([ADR 0012](0012-concept-demo-starting-scope.md)), likely our own per-monitor renderer later ([synthesis](../../plans/reports/research-261010-1922-sota-research-synthesis.md)), and a macOS one after that ([ADR 0003](0003-windows-first-macos-later.md)).

## Decision

Decide ahead, perform live:

- **Content model:** what is in the film — shots and cuts, black bars, text in the bars, where faces and attention are, subtitle lines. Made once per film and cached. Knows nothing about the setup.
- **Room model:** the setup — each monitor's pixels, physical size, position and angle, bezels, color and refresh rates, plus the seat. Made once per setup. Knows nothing about the film. It works in physical units, so the bezels are part of the geometry, not a separate step.
- **Preferences:** fold or smooth curve, surround style, crop preset.
- **Planner:** turns those three into a **cue sheet**. Per shot: the crop, the picture's position and size, the subtitle screen, the surround settings. Per monitor: fixed geometry and color. One planner owns crop, placement and subtitle position together, and it may use the whole film, not only the current shot.
- **Performer:** plays the file and applies the cue sheet, each change landing on the exact frame. It makes no decisions.

The cue sheet is plain data: it can be read, compared and written by hand.

## Consequences

- Only the performer runs against the clock, so smooth playback depends on it alone. Analysis must never slow playback down.
- The performer can be replaced (demo, own renderer, macOS) without touching the planner.
- A new setup or new preferences means re-planning from the cached content model, not re-scanning the film.
- Playback can start at once on a cautious cue sheet from the quick pre-scan; the background scan refines only the part not yet shown.
- The shader generator turns the room model into fixed per-monitor geometry and color, and carries the cue sheet as a table the shader reads by each frame's own timestamp.
- The planner, room model and analysis talk to the performer through files and never link mpv code, so the license choice stays open.
- Hand-written cue sheets let the concept demo test placement and size by eye before any face analysis exists.
- Stock mpv applies a change on the exact frame when the shader looks the cue up by the frame's own timestamp. Verified on Setup A, including seeks, 23.976 to 25 fps and a 3000-cue table; a script setting the change while playing lands about one frame late ([spike report](../../plans/reports/spike-261010-2345-exact-frame-cue-switching-in-stock-mpv.md)).
- Refining the cue sheet while playing works. Each refinement is a new shader file with the same code and a new table, and changes only shots that start at least a second ahead, so no shot changes on screen. Verified with no wrong frames, shader compiles or stutter, and mpv reports which table it is drawing ([second spike](../../plans/reports/spike-261011-0950-refining-the-cue-table-during-playback.md)).
- Per-shot changes are applied in the shader's final stage, together with the monitor mapping. Only there do they stay exact when mpv blends frames for smoother motion; earlier in the shader they land one frame late at most cuts.
