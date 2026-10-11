# 0011. Setup: automatic detection and manual alignment

- **Status:** Accepted
- **Date:** 2026-10-10

## Context

Windows reports pixels, refresh rates, positions and rotation exactly, but physical size only roughly. It cannot report bezels, gaps, physical alignment, angles or the viewer's position. A phone photo of on-screen patterns was considered as an optional helper. Setup must stay simple.

## Decision

- Prioritize automatic detection and manual alignment.
- No phone photo: not needed.

## Consequences

- Everything Windows can't report comes from manual alignment on screen.
- Physical sizes reported by monitors need a sanity check; they can be a few percent off.
- The seat position needed by the smooth-curve model is entered or aligned manually.
