# 0023. Display changes are held by the player

- **Status:** Accepted
- **Date:** 2026-10-11
- **Refines:** [0005](0005-app-may-change-display-settings-during-playback.md)

## Context

ADR 0005 lets panorama change display settings during playback, if they always come back, even when the player is killed. A bounded test on Setup A switched the external screen from 100 to 60 Hz in a child process and killed it ([room and display-settings research](../../plans/reports/researcher-261011-1247-room-model-and-display-settings.md)):

- A change made with `ChangeDisplaySettingsEx` and `CDS_FULLSCREEN` came back by itself, 0.45 s after the kill.
- Changes made with flags 0, or with `SetDisplayConfig` without saving, stayed at 60 Hz.
- The saved Windows mode never changed.

MPC-BE and Kodi switch refresh rates the first way.

## Decision

- The performer, the long-lived process that plays the film, makes refresh changes itself, with `CDS_FULLSCREEN`, so Windows undoes them when it exits or dies. A short-lived helper must never make them: Windows would undo them when the helper exits.
- Before the first change it writes a snapshot of every monitor's mode to disk, marked unfinished; the next start restores from an unfinished snapshot first.
- Changes that would outlive the player (exact fractional rates through `SetDisplayConfig`, main display, rotation) are made only with a separate guardian process that restores them.
- Nothing is ever saved as the user's own setting.
- Custom refresh modes, once the user has approved and seen them, are kept in the driver for later use; panorama can list and remove every custom mode it created.

## Consequences

- One test per method so far: lock, sleep, hot-plug, a windowless player and a normal exit are tested in M1 phase 07.
- Spike tools that set a mode in one process and restore it from another must not use `CDS_FULLSCREEN` (the earlier `display-refresh.py` did by mistake, now fixed).
