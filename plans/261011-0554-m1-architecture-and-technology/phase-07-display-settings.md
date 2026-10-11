---
phase: 7
title: "Display settings"
status: in-progress
priority: P2
effort: "0.5d"
dependencies: []
---

# Phase 7: Display settings

## Overview

Changed display settings, such as a refresh rate switched for playback, always come back, even when the player is killed ([ADR 0005](../../docs/adr/0005-app-may-change-display-settings-during-playback.md)).

## Context

- Existing non-persistent switcher: `spikes/mixed-rate-pacing/display-refresh.py`.
- Proposed restore design (unverified): [rendering research](../reports/researcher-261010-1846-windows-rendering-and-smooth-multi-monitor-playback.md) section 3.3.
- New: [room model and display settings](../reports/researcher-261011-1247-room-model-and-display-settings.md) section 4 and the bounded hard-kill test in `spikes/display-restore/`.

## Invariant

Every display setting panorama changes returns to the user's setting when playback ends, however it ends: clean exit, crash, hard kill, sleep, logoff, power loss (at the next start at the latest).

## Levers

Non-persistent changes (the saved mode stays the user's), what Windows itself reverts, an out-of-process watchdog, a dirty snapshot on disk restored at the next start, a logon task.

Hard to change: process topology (a separate watchdog), snapshot format. Cheap: timeouts, UI for the panic path.

## Implementation Steps

1. Done: research and the hard-kill test (`CDS_FULLSCREEN` reverted 0.45 s after the kill; flags 0 and `SetDisplayConfig` without save did not). Decided: [ADR 0023](../../docs/adr/0023-display-changes-held-by-the-player.md).
2. Write the design: the performer (long-lived) makes `CDS_FULLSCREEN` changes; the launcher writes the snapshot and runs the guardian only for changes that outlive the player; restore-first on every start if the snapshot is dirty; list and remove panorama's custom modes.
3. Test: kill the player 3× mid-playback; kill player and watchdog together, then start again; hot-plug during playback.
4. Write the ADR.

## Success Criteria

- [ ] All tests restore the original modes (read back via CCD).
- [ ] ADR written.

## Risk Assessment

- Windows may not revert anything on its own. Response: the watchdog and restore-at-start carry the guarantee.
- Hot-plug or Win+P during playback changes topology. Response: restore only monitors whose identity still exists and whose mode is still ours.
