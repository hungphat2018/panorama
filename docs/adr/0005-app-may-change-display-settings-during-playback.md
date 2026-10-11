# 0005. The app may change display settings during playback

- **Status:** Accepted — refined by [0023](0023-display-changes-held-by-the-player.md)
- **Date:** 2026-10-10

## Context

Stutter breaks the illusion. Monitors running different refresh rates, or a refresh rate that does not match the video's frame rate, cause stutter. The usual fix is switching the screens to a matching rate while the video plays.

## Decision

The app may change Windows display settings during playback, such as refresh rate, and restores them afterwards.

## Consequences

- The original settings must be restored even if playback ends unexpectedly.
