---
title: "Speed gates X2 + E1: runbook for the GPU slot (prep done, nothing run on GPU or screens yet)"
date: 2026-10-13
type: runbook (from the two helper preps)
plan: plans/261013-0718-ai-surround-live-path/phase-01-decision-gates.md
---

Condensed from the X2 and E1 helper reports (2026-10-13 ~15:05 and ~15:20). Rests between runs (user rule) are built into both runners.

## When

- The other session's queue pauses at `queue.txt` line 22 (after step 17 plus rests, ~18:05): it waits for `spikes/ai-surround/out/runs/queue/go-after-speed-gates`.
- Start only when no ComfyUI runs on port 8189 and commit headroom is ≥ ~25 GB.
- Laptop on AC power, Windows "Do Not Disturb" on (a toast composes the screens and taints E1).
- Order:
  1. X1, if the user is at the desk (8 min, needs an idle GPU).
  2. X2.
  3. E1 (uses X2's winning recipe as its painter load).
  4. Touch the go file so the queue resumes.

## X2 painter speed (`spikes/ai-surround/painter-speed.py`)

From the repo root; `PY = tools/venv-outpaint/Scripts/python.exe -I -X utf8`.

0. **Pre-check** (no GPU, ~25 s): `PY spikes/ai-surround/painter-speed.py --dry-run`. Needs "dry run OK", port 8190 free and headroom ≥ ~25 GB.
1. **Probe** (~8 min): `PY spikes/ai-surround/painter-speed.py --variants turbo8-s10 --scenes jjk-pan,jjk-action,jjk-one-side --rest 60 --tag probe`.
   - Each plate line must show `lora-warn 0`, `DiT loaded completely`, `builds 0` on warm plates, and commit headroom above ~6 GB.
   - Look at `runs/qwen21-ov2-turbo8-s10-probe/*/raw-s0.5.png`.
   - If the RAM guard trips: add `"--server-args=--fp8_e4m3fn-text-enc" --tag te8` (fp8 text encoder; a precision change) or `--server-args=--enable-dynamic-vram --tag dynvram` (RAM-safe, ~35% slower).
2. **Main run, in two parts so the best variant can be picked by eye first:**
   - 2a (~45 min): `--variants fast25,turbo8-s10,turbo8-s07`
   - 2b (~25 min): `--variants best-1mp,best-textonly,best-1mp-textonly --best <winner>`
   - Add `--scenes jjk-pan,jjk-edge-move,jjk-one-side,jjk-action,jjk-still-edge-person` to keep it to about 35 min in total.
3. **Optional** (CPU, 3-5 min): `PY spikes/ai-surround/seam-match.py spikes/ai-surround/out/scenes spikes/ai-surround/out/runs --fills <recipes>` for colour-corrected seams.

Expected warm s/plate (the helper's estimates, ±50%):

| Variant | Warm s/plate |
|---|---|
| fast25 (V1) | 45-60 |
| turbo8 (V2) | 18-25 |
| -1mp (V4) | 8-11 |
| -textonly (V5) | 13-18 |
| -1mp-textonly | 5-8 |

- **Skipped:** V3, the PAI 4-step LoRA. ComfyUI 0.39 cannot load it natively: its keys lack `.weight`, and it has a per-step head bank driven by PAI's own scheduler code.
- **Risks:**
  - RAM: resident without dynamic VRAM is about 26 GB, with peaks around 35 GB on the first plate.
  - `fp8_e4m3fn_fast` clamps activations at ±448. The fallback `--dit-dtype fp8_e4m3fn` keeps fp8 weights with bf16 compute.
  - Turbo + outpaint LoRA stacking is untested anywhere.
  - V5 is out of distribution.
- **Outputs:**
  - `out/runs/painter-speed/{timing-*.jsonl, summary-*.json, gpu-*.csv, comfy-server-*.log}`
  - `out/runs/scores-painter-speed.json`
  - `out/runs/sheets/<scene>-painter-speed-s0.5.png` (truth, the 25-step plate, then every variant)

## E1 player under load (`spikes/ai-surround/e1-player-under-load.py`, PowerShell)

```
Set-Location 'C:\Users\tamla\Downloads\Telegram Desktop\panorama'; $py = '.\tools\venv-outpaint\Scripts\python.exe'
# 0. smoke (~4 min)
& $py -I -X utf8 .\spikes\ai-surround\e1-player-under-load.py --cells none-lat1-default,gemm2-lat3-high --seconds 20 --rest 60
# 1. decision cells first (trimmed from the 32-cell matrix, which takes ~3.3 h)
& $py -I -X utf8 .\spikes\ai-surround\e1-player-under-load.py --cells none-lat1-default,none-lat3-default,gemm2-lat1-default,gemm8-lat1-default,attn8-lat1-default,gemm8-lat3-default,attn8-lat3-default,painter-lat1-default,painter-lat3-default,painter-lat3-high --painter-cmd '<PY> -u .\spikes\ai-surround\e1-painter-load.py --variant <X2 winner>'
# re-score: & $py -I -X utf8 .\spikes\ai-surround\e1-analyse.py .\spikes\ai-surround\out\e1\<stamp>
```

- **Smoke must show:**
  - Both cells "ok"; the table at 240 / 96 Hz with overlay 100% on both monitors.
  - `gpu-share.json`: latency 3, 4 buffers, class HIGH, thread priority 7.
  - `load.json`: call_ms p50 ≈ 2.0.
  - `perf1-stdout.txt`: "DISPLAY2 switched to 96 Hz".
- **Player binary:** `spikes/performer-p1/bin/perf1-p3-e1.exe`. New flags `--frame-latency N` and `--gpu-priority high` (new file `src/perf1-gpu-share.c/.h` plus small hooks). Defaults behave exactly as before.
- **Clip:** `out/e1/clip/jjk-pans-130s.mkv` (JJK 274.0-404.3 s, 11 camera pans, 46 s of pans), with `cues.json`.
- **Runner:** rests 180 s before each cell, then waits for GPU temperature ≤ the first cell's + 5 °C (at most 600 s).
  - It refuses to start if the GPU is > 30% busy.
  - Children run in a kill-on-close job; Ctrl+C or a hard kill leaves nothing running.
  - Esc anywhere stops perf1.
- **Metrics per cell and monitor:**
  - wrong/min (film frames shown for a different number of refreshes than planned)
  - miss/min
  - present-interval error p99 / p99.9
  - overlay share
  - clock events within 0.25 s of a miss (E2)
  - load kernel time (p50/max) and busy share
- **Painter cell:** `e1-painter-load.py` (new) paints back to back on its own server (port 8190). It prints `LOAD STEADY` after the first plate and logs `painter-load.jsonl` in the cell folder. The runner kills it when the cell ends.
- **Finding to carry into M1:** with `--frame-latency 3` each monitor keeps its pace, but every present lands 2 refreshes after its planned refresh. That shifts the seam between monitors by ~12.5 ms at 240 + 96 Hz, against ADR 0020. The engine must plan k + (N−1) (`engine_service`, `lead_refreshes` in `plan_restart`) before depth 3 can ship.
- **Already validated without screens or GPU:**
  - compile with 0 warnings; picture-selftest 92/0, cue-selftest 183/0; latch test steps 1-6 PASS
  - the headless E1 binary run PASS
  - the real API calls in a hidden-window probe (latency 3 reads 3; HIGH; priority 7)
  - a 32-cell dry run with 64 truth checks PASS
  - the analysis on an earlier real full-screen run: laptop 1 miss and 0 wrong frames in 43 s; external 0 / 0

## Unresolved

1. The external at 96 Hz (product setting, the default) or its native 100 Hz? Default 96.
2. Add a `--duty 50` pass? Only if the decision cells show a soft-failure zone.
3. Who owns queue-aware planning (k + N − 1) in the engine: the M1 track.
