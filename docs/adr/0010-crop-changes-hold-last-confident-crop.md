# 0010. Crop changes: hold the last confident crop

- **Status:** Accepted — refined by [0024](0024-film-analysis-worker-and-content-model-shape.md)
- **Date:** 2026-10-10

## Context

Dark scenes, fades and night shots make black-bar detection unsure. Taken literally, "when in doubt, don't crop" brings the bars back in every dark scene, which is the "crops jumping" illusion breaker.

## Decision

- Keep the last confident crop; change it only with reliable evidence.
- Never crop subtitles or text in the bars.
- Crop changes happen at cuts, not only at scene changes: some films switch framing in the middle of a scene.

## Consequences

- Until a confident crop exists (file start, a jump to a part not yet analysed), no crop is applied.
- Text found in the bars keeps those bars visible.
- The file is pre-scanned, so decisions can use what comes later in the film.
