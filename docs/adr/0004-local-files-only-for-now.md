# 0004. Local files only for now

- **Status:** Accepted
- **Date:** 2026-10-10

## Context

Sources considered were local files and streaming URLs. The focus is the architecture and the algorithm, or model, of the multi-screen concept.

## Decision

Support local files only for now. Streaming URLs are dropped for now.

## Consequences

- The whole file is available before and during playback, not just the part already played.
- Bringing streaming back later needs its own decision.
