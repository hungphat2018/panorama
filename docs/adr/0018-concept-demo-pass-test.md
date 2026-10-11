# 0018. Concept demo pass test

- **Status:** Accepted
- **Date:** 2026-10-11
- **Refines:** [0012](0012-concept-demo-starting-scope.md)

## Context

The concept demo must prove that a setup is perceived as one screen. It is judged by eye on hard scenes, and side by side against a plain picture stretched across the screens, but what counts as a pass was not set. A hard scene is a short clip picked because it tends to break the illusion, such as a face next to a bezel.

## Decision

The demo passes on a setup when both hold:

1. On a fixed list of hard scenes (at least a face next to a bezel, a slow pan across a bezel, a dark scene with black bars, and a scene with a lot of subtitles), the user prefers panorama over the plain stretched picture on every one.
2. The user then watches one full episode and one film and notices none of the five illusion breakers.

## Consequences

- The hard-scene list is fixed with the user before judging, not picked afterwards.
- Losing one scene blocks the pass. Its cause goes back into the work, then it is judged again.
- The test is run on Setup A and on Setup B.
