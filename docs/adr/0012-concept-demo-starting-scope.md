# 0012. Concept demo starting scope

- **Status:** Accepted — refined by [0018](0018-concept-demo-pass-test.md), [0022](0022-own-performer-on-libplacebo-and-ffmpeg.md)
- **Date:** 2026-10-10

## Context

The research pass proposed a starting demo ([synthesis](../../plans/reports/research-261010-1922-sota-research-synthesis.md)).

## Decision

Accept the proposed starting demo:

- Edge-color glow versus black in the leftover area.
- Bezel alignment.
- Color calibration.
- Pre-scan of the file.
- Crop changes at cuts.

It starts from the proposed playback base: mpv 0.41 with libplacebo 7.360, plus our own generated shader.

## Consequences

- The playback base and every other component are re-checked against the current state of the art before building ([ADR 0006](0006-build-on-top-of-the-state-of-the-art.md)).
