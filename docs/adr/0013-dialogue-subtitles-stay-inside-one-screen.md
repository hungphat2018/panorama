# 0013. Dialogue subtitles stay inside one screen

- **Status:** Accepted
- **Date:** 2026-10-10

## Context

Subtitles split across screens break the illusion. A subtitle centred on the picture can land on a bezel, especially when the picture is not centred on the canvas ([ADR 0009](0009-bezels-placement-first-picture-continues-behind-bezel.md)).

## Decision

Dialogue subtitles always stay inside one screen, even if that puts them off-centre.

## Consequences

- Dialogue can sit off the picture's centre.
- Applies to dialogue; positioned signs that the subtitle file places on the picture stay where the file puts them.
