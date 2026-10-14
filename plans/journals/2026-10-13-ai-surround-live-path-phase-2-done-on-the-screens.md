---
title: "AI surround live path: phase 2 done on the screens"
date: 2026-10-13
summary: Smoke clean of worker faults; painter cap made E1 worse; player misses ride a 2-s system stall; warm-start cache bugs fixed
---

# AI surround live path: phase 2 done on the screens

## What happened
- Phase 2's screen runs ran unattended (user OK), with rests:
  - E1's painter cell again, with the GPU rules;
  - a 2-min windowed smoke with the painter;
  - a 10-min full-screen smoke without it: 3 seeks, a re-watched minute, the worker killed at 330 s and restarted at 420 s.
- E1 re-test: capping ComfyUI's VRAM (`--reserve-vram 4`) made the painter worse.
  - 39.8 / 40.9 wrong frames a minute at 240 / 96 Hz, against 23.0 / 18.4 uncapped.
  - Likely weight streaming: the INT8 DiT no longer fits.
- Full-screen smoke: no wrong frame or missed refresh came from the worker or the AI sides.
  - Outside seeks, every fault sits on one 2-second grid of the system clock.
  - The no-load cells, with no AI and no worker, show the same grid, including E1's first no-load cell (31 of 35 misses, render stalls of 12-18 ms). E1's "misses at clock changes" are mostly this stall.
- The windowed run's one 12.6 ms copy chunk fell on that stall too. The planned "copy one refresh after the prefetch" change was dropped: the longest chunk without the painter took 1.3 ms.
- The smoke found worker cache bugs:
  - analyses cached without a mosaic for the room's current margins were published and never got T1: 287 s of 600 s showed glow;
  - keys held the margins to 0.1 px, so windowed and full-screen runs never shared canvases;
  - spans split at hidden cuts were only looked up whole.

## Decision
- The warm start waits for the room's margins and publishes a shot only with this room's mosaic (and its T1). The analyser makes the rest again.
- Margins round up to 32 source px for both the canvas extent and the key.
- The done test and the warm start follow the parts that tile a span.
- Review fixes:
  - an error in the warm start is logged, and the analyser always starts (it used to stay silent with no analyser);
  - damaged or stale cache files count as misses;
  - shot rows are reused by first frame, with a budget of 4N + 256.
- New `live-cache-selftest.py`: 23/23.
- Phase 3's painter must fit its VRAM budget without weight streaming, measured on E1's painter cell.
- ADR 0028 stays Proposed until the user OKs what the viewer sees (ADR 0017).

## Next steps
- From the user: an OK on ADR 0028 (glow until ready, upgrades only at cuts, half-resolution sides), and the commit.
- M1: find the 2-second stall's owner (one no-load run without nvidia-smi; LatencyMon or a WPR trace).
- Phase 1's open items (X1 blind test, X2 sheets), then phases 3 and 4.

Report: plans/reports/implementation-261013-2012-live-skeleton-phase2.md

> Historical work record — not durable authority. Prefer docs/specs/ADRs for current decisions.
