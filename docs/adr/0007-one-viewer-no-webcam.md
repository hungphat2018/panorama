# 0007. One viewer, no webcam

- **Status:** Accepted
- **Date:** 2026-10-10

## Context

Wraparound and window-style models look exactly right from one eye position only. A webcam tracking the viewer's head could keep the picture correct when the viewer moves, or when several people watch.

## Decision

Design for a single viewer ("just me"). No webcam.

## Consequences

- Where the model needs the viewer's position, it comes from setup instead of tracking.
