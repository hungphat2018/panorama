# 0008. Picture sizing: no-crop default, surround effect, optional crop preset

- **Status:** Accepted — refined by [0015](0015-picture-size-may-change-at-cuts.md)
- **Date:** 2026-10-10
- **Refines:** [0001](0001-crop-none-of-the-real-picture-by-default.md)

## Context

With the no-crop default, the picture is sized to the canvas height. The research pass ([synthesis](../../plans/reports/research-261010-1922-sota-research-synthesis.md)) found that extra landscape screens side by side add no picture size for 16:9 content and at most about a third more height for 2.39:1 films. A third to three quarters of the canvas is often left over.

## Decision

Combine all three:

- No-crop default (ADR 0001 stays the default).
- Surround effect: the leftover area is used for immersion.
- Optional crop preset.

## Consequences

- The leftover area is a main surface, not a thin border, so the surround effect needs real design and testing.
- The crop preset is opt-in, never the default.
