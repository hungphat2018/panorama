# 0019. Settle architecture and technology first, at concept level

- **Status:** Accepted
- **Date:** 2026-10-11
- **Refines:** [0006](0006-build-on-top-of-the-state-of-the-art.md)

## Context

Implementation and bug fixes are easy to do at the end. Architecture and technology are much harder to change once code is built on them. Traditional approaches each have weaknesses, and their usual framing can hide better options: "all monitors must share one refresh rate" hid per-monitor multiples and variable refresh ([pacing research](../../plans/reports/research-261011-1000-frame-pacing-judder-and-seek-swap-concepts.md)).

## Decision

- Agents research each problem at concept level: first the outcome needed, then every lever that could reach it, and only then named techniques. They are not bound by the traditional approach or its terms.
- They build on top of the best available, checked fresh ([ADR 0006](0006-build-on-top-of-the-state-of-the-art.md)), and push past its weaknesses.
- Architecture and technology are settled on evidence before the build. Polish and bug fixes come later.

## Consequences

- Early work is mostly research and spikes, and its code may be thrown away.
- A "how" decision is cheapest to change before the build, so agents challenge it early ([ADR 0017](0017-agents-may-change-how-not-what-you-see.md)).
- The [roadmap](../project-roadmap.md) settles architecture and technology before building the concept demo.
