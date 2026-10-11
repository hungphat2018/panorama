# 0009. Bezels: placement first, picture continues behind the bezel

- **Status:** Accepted — refined by [0015](0015-picture-size-may-change-at-cuts.md), [0020](0020-motion-crosses-a-bezel-with-no-delay.md)
- **Date:** 2026-10-10
- **Amends:** [0001](0001-crop-none-of-the-real-picture-by-default.md)

## Context

Bezels cutting through faces is the top illusion breaker. The research pass found no technique that makes the black stripe itself disappear. Letting the picture continue behind each bezel keeps shapes and motion true; skipping the bezel makes moving things jump. Continuing behind the bezel hides about 1–3% of the image width.

## Decision

- Prioritize placement: keep faces and the picture's centre off the bezels, and move the picture only at cuts, where one shot ends and the next begins. Within a scene, the planner prefers to stay still and moves only when it clearly helps.
- The picture continues behind the bezels. Losing 1–3% of image width there is accepted, as an exception to the no-crop rule.

## Consequences

- A layout planner decides placement per shot and re-plans whenever the crop changes.
- The picture is not always centred on the canvas.
