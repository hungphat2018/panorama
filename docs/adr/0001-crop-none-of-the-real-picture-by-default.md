# 0001. Crop none of the real picture by default

- **Status:** Accepted — refined by [0008](0008-picture-sizing-no-crop-surround-optional-crop-preset.md), amended by [0009](0009-bezels-placement-first-picture-continues-behind-bezel.md)
- **Date:** 2026-10-10

## Context

The key design principle is to maximize immersive screen usage without sacrificing image fidelity. Removing black bars loses nothing; cropping into the actual picture does. The open question was how much real picture may be given up to fill more screen.

## Decision

- Cropping the real picture: none is the ideal, so none is the default. Only black bars are removed.
- The video is never distorted in any way and keeps its composition.
- The same rule applies to movies and anime. Other behavior, such as crop-to-fill, can come later as optional presets.

## Consequences

- When the picture's shape differs from the canvas, part of the canvas stays unused.
- Black-bar detection must not mistake real picture for black bars, or the default is broken.
