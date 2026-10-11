# 0016. Camera slides play smooth

- **Status:** Accepted — refined by [0020](0020-motion-crosses-a-bezel-with-no-delay.md), [0021](0021-one-window-per-monitor-one-film-clock.md)
- **Date:** 2026-10-11
- **Refines:** [0014](0014-architecture-plan-ahead-into-a-cue-sheet-perform-live.md)

## Context

A film or an episode shows a new picture 24 times a second. When the camera slides across a scene (a pan sideways, or a tilt up or down), the picture moves in visible steps, and on a monitor whose refresh rate is not a whole multiple of 24 the steps are also uneven. Stutter is illusion breaker #3.

In a camera slide the whole picture moves together. So the in-between positions can be made exactly, by sliding the drawn frames along the measured path, without inventing new content. The user compared both on three slides from a real anime episode on the 240 Hz laptop panel, frames as drawn versus the smooth slide, and preferred the smooth slide. The same held for a pan across the real bezel on both screens of Setup A, where the 24 fps version flickers at the bezel ([spike report](../../plans/reports/spike-261011-1034-one-window-across-mixed-refresh-rates.md)).

## Decision

- **Pan mode:** during a camera slide, the picture moves to a new position on every refresh, following the slide's measured path, including the animators' own speed-up and slow-down. Outside slides, frames play as drawn or filmed.
- Only the camera's slide is smoothed. Characters and anything else that moves on its own keep their drawn timing.
- A slide is a per-shot setting like the others: found during the scan, stored in the cue sheet, switched only at cuts ([ADR 0014](0014-architecture-plan-ahead-into-a-cue-sheet-perform-live.md)).

## Consequences

- The content model gains, per shot: whether it is a slide, and where each frame's picture sits along it.
- The performer draws every refresh and needs to know how far between two frames that refresh falls. Stock mpv redraws on every refresh in display-sync mode, but its shader is only told the frame's own timestamp; counting refreshes in the shader's persistent storage might give the rest. Whether the demo can do this is a spike. The own renderer can do it directly.
- A slide uncovers a thin strip at the picture's edge, at most one frame's step wide. With only the current frame at hand, the strip has to be filled from the edge or covered by the surround.
- During a slide, a monitor whose rate is not a multiple of 24 stops stumbling: every refresh shows the right position.
- Not yet judged: live-action films, and slides where things move across the picture on their own (a character walking while the camera follows).
