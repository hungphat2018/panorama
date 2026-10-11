# 0017. Agents may change how it works, not what you see

- **Status:** Accepted
- **Date:** 2026-10-11

## Context

Agents build panorama and review its architecture as they work, so the approach that works is expected to come out of that work rather than be fixed up front. The ADRs hold two kinds of decisions: what the viewer sees, and how the system produces it. Changing the first kind without the user changes the product. Waiting for the user on every change of the second kind stalls the work.

## Decision

- **How it works** (architecture, tools, renderer, file formats, algorithms): an agent may replace such a decision when a spike, a measurement or a by-eye result shows something better. It writes a new ADR with the evidence, marks the old one as refined or superseded, and tells the user afterwards.
- **What you see** (such as no crop by default, one viewer, dialogue subtitles inside one screen, Windows first, local files only) and every by-eye verdict: changes only with the user's OK.
- In an ADR that holds both kinds, the parts about what the viewer sees are protected; the parts about the mechanism are not.
- When it is unclear which kind a decision is, it is treated as "what you see".

## Consequences

- The architecture in [ADR 0014](0014-architecture-plan-ahead-into-a-cue-sheet-perform-live.md) and the playback base in [ADR 0012](0012-concept-demo-starting-scope.md) are current best bets, not fixed contracts.
- A replaced "how" decision always comes with its evidence in a report under `plans/reports/`.
- New tools and techniques still get a fresh state-of-the-art check first ([ADR 0006](0006-build-on-top-of-the-state-of-the-art.md)).
