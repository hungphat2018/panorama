# 0015. Picture size may change at cuts

- **Status:** Accepted
- **Date:** 2026-10-10
- **Refines:** [0008](0008-picture-sizing-no-crop-surround-optional-crop-preset.md), [0009](0009-bezels-placement-first-picture-continues-behind-bezel.md)

## Context

ADR 0008 started from the picture filling the canvas height. On mixed setups that is not always the biggest picture. Rough numbers for Setup A, from the sizes the monitors report and ignoring height alignment and the laptop sitting nearer: a 16:9 picture on the external screen alone is about 21% taller than any picture spanning both screens; for 2.39:1 films, spanning gains only about 7% and adds a seam.

Moving the picture (ADR 0009) cannot always keep faces off the bezels, for example when several people are spread across a wide shot.

## Decision

- Filling the canvas height is one option, not a rule.
- The planner ([ADR 0014](0014-architecture-plan-ahead-into-a-cue-sheet-perform-live.md)) chooses which monitors carry the picture and at what size, from a small set of sizes such as one monitor or the full span.
- Size changes follow the same timing as moves (ADR 0009). Never in the middle of a shot.
- Nothing is cropped or distorted. ADR 0001 still holds.

## Consequences

- A smaller picture with no seam through it can win over a bigger one crossing a bezel.
- On Setup A, many films may play on the external screen alone, with the laptop as surround.
- The surround area changes shape whenever the picture changes size.
- A size change is more noticeable than a move. Whether it feels natural is judged by eye in the concept demo.
