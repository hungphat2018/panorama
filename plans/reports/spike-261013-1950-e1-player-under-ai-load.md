---
title: "E1: does the player keep every frame on time while AI runs on the same GPU?"
date: 2026-10-13
type: spike (GPU + both screens, unattended)
plan: plans/261013-0718-ai-surround-live-path/phase-01-decision-gates.md
---

## Decision

- The live path can share the GPU, but not yet with zero wrong frames.
- **Short GPU kernels are harmless:** 2-12 ms GEMM or attention chunks give 1-3 wrong frames per minute, fewer than no load at all.
- **The real painter is not harmless:** about 23 wrong frames per minute at 240 Hz and 18 at 96 Hz, not tied to clock events. Likely cause: VRAM pressure and weight streaming. ComfyUI's dynamic mode held ~11.5 of 12 GB of VRAM.
- **The player has a separate problem with no load:** 23 wrong frames per minute at 240 Hz, most of them next to GPU clock (P-state) changes.
- **Frame latency 3 is unusable** until the engine plans k + (N−1).

## Setup

- **Player:** perf1 (`bin/perf1-p3-e1.exe`), D3D11, full screen. DISPLAY1 = laptop 2560x1600 at 240 Hz; DISPLAY2 = 1920x1080 switched to 96 Hz.
- **Clip:** `out/e1/clip/jjk-pans-130s.mkv` (11 camera pans), with cues; 120 s per cell.
- **Loads:**
  - `gpu-load.py` (bf16 GEMM or SDPA, calibrated to the kernel length named in the cell)
  - the real painter `e1-painter-load.py`: Turbo 8-step + outpaint LoRA v2, INT8 DiT and encoder, ~1 MP, ComfyUI dynamic VRAM, plates back to back
- **Rests:** 180 s before each cell plus a thermal wait (the user rule); every cell started at 38-42 °C.
- **Run:** `out/e1/20261013-185619/` (`e1-summary.json`, per-cell `presents-*.csv`, `nvsmi.csv`).

## Results

"Wrong" = film frames shown for another number of refreshes than planned (at 240 Hz, one refresh = 4.2 ms). "Miss" = refreshes with no new present. "Near clock" = how many of the misses happened within 0.25 s of a P-state change, a clock step or a throttle reason.

| Cell | 240 Hz wrong/min | 240 Hz miss/min | 96 Hz wrong/min | 96 Hz miss/min | Near clock, 240 / 96 Hz |
|---|---|---|---|---|---|
| none, latency 1 | 23.5 | 85.8 | 11.2 | 19.9 | 49 / 16 |
| GEMM 2 ms, latency 1 | 1.0 | 1.0 | 0 | 0 | 0 |
| GEMM 8 ms (p50 9.5, max 13.6), latency 1 | 3.1 | 7.7 | 0 | 0 | 0 |
| attention 8 ms (p50 12.3, max 15.4), latency 1 | 1.0 | 1.0 | 0 | 0 | 0 |
| **painter, latency 1** | **23.0** | **153.2** | **18.4** | **52.6** | **0 / 0** |
| none, latency 3 | 85.3 | 8639 | 0 | 0 | 0 |
| GEMM 8 ms, latency 3 (stopped at 60 s) | 328 | 8640 | 0 | 0 | 277 / 15 |
| attention 8 ms, latency 3 | 613 | 8640 | 0 | 0 | 0 |
| painter, latency 3 | 650 | 8640 | 0 | 0 | 3862 / 232 |
| painter, latency 3, priority high | 322 | 8640 | 1.0 | 0 | 3877 / 105 |

- The overlay was 99-100% on every cell (independent flip; no composition).
- The 20-s smoke baseline at 18:18 had 0 wrong frames. The 120-s baseline shows the clock-related misses are intermittent.
- Latency 3: every present lands 2 refreshes after its planned refresh (known from prep). The 240 Hz panel then counts ~144 "misses" per second even with no load, so these cells say nothing about stutter.
- Priority high was tested only at latency 3, where it halved the wrong frames. Its effect at latency 1 is untested.

## What it means for the plan (phase 2 GPU-sharing rules)

1. **The player locks GPU clocks during playback.** NVAPI power management "prefer maximum performance" (or consistent), as the GPU-sharing report proposed (E2, lever L11). This is for the M1 track: it affects playback even without AI.
2. **The painter's VRAM is capped,** so the player keeps headroom: ComfyUI `--reserve-vram`, or the native pipeline with fixed allocations, plus `SetVideoMemoryReservation` for the player. The encoder (INT8 9.35 GB) and the DiT (INT8 7.26 GB) don't fit together, so their swap is inherent. Batch the encoder reads (encode many plates' conditioning in one encoder session) so swaps are rare.
3. **Kernel length is not the limit** up to ~12-15 ms; no chunking is needed for that.
4. **Frame latency > 1** needs queue-aware planning in the engine (M1 track) before it can help.
5. **Re-test** the painter cell with 1 and 2, before the phase 2 smoke.

## Unresolved

1. Are ~20 wrong frames a minute (each ~4 ms) visible to the user? M1's standard is zero; not asked.
2. Is the painter's damage from VRAM pressure, PCIe weight streaming or long kernels? Not separated; the re-test with capped VRAM tells.
3. Does priority high help at latency 1?
