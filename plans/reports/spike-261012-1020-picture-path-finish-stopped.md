---
title: "Picture path passes 2 to 4 (surround, colour finish, subtitles): stopped early, what it verified"
date: 2026-10-12
type: spike
answers: "M1 phase 3, the lasting choices of passes 2 to 4: pass order, where dither runs, who owns gamut with Windows ACM"
harness: spikes/picture-path/finish/
status: "stopped on the user's scope cut (2026-10-12); findings below were verified before the stop"
---

# Picture path passes 2 to 4: stopped early

The user cut phase 3's testing to what decides the pass structure (2026-10-12). This agent was stopped before its report; this record keeps what it had already verified, because the picture-path ADR rests on it. Its tuning work (surround time constants, flicker thresholds, cost) is not finished and moved to the thin prototype (phase 8).

Terms. *Dither*: adding a tiny, fine noise before rounding to 8 bits, so a smooth dark gradient doesn't turn into visible steps (*bands*). *Surround*: the glow drawn in the leftover screen area around the picture. *ACM*: Windows' automatic colour management, on for the laptop panel and off for the external.

Evidence tags: [L] measured on this PC (RTX 5070 Ti through Vulkan, libplacebo 7.375 at commit 1dd0226, libass 0.17.5), [V] source or Microsoft doc read, [S] search snippet, [D] derived.

## 1. Verified

**Pass order: one colour correction over picture and surround together, then our own dither, then subtitles on top.**
- libplacebo dithers only the picture draw, not its blurred-border fill [V `src/renderer.c` `pass_output_target`]; measured on a 0–10 % near-black ramp, 8-bit out: picture area low-pass error p99 0.034 code, border in 100 px bands [L]. So the surround needs our dither, and correction plus dither must be one pass over both.
- Gain 0.8 and gamma 1.15 without dither: 14 levels, 178 px bands, p99 error 0.511 code. With libplacebo's blue-noise dither inside our shader: 0.018 code, 1 px runs. Ordered dither 0.023, error diffusion 0.020, white noise 0.107 [L].
- Subtitles blended after dither: 0 pixels changed outside libass's bitmaps; a solid libass rectangle comes out exact; 2 of 72,615 single-layer values off by one code (GPU blend rounding); on and off on the exact frame at 23.976, 25 and 60 fps [L]. `pl_render_image` with no image draws overlays only, if `border = PL_CLEAR_SKIP` (else it clears the target first) [V].
- Side findings that touch pass 1: libplacebo's black point compensation moves near-black by about 3 codes (p99 3.3) unless the target's black point is declared; un-dithered float-to-8-bit output is one code off in about 3 % of values [L]. Passed to the pass-structure spike.

**Colour ownership.**
- ACM on (laptop): an 8-bit swapchain is "explicitly treated as sRGB" and Windows converts it to the panel; the ICC APIs then report no profile, so a panel profile applied by the player would correct twice. ACM off (external): Windows applies no colour management and the app must do its own [V MS Learn, "ICC profile behavior with Advanced Color", updated 2025-05-12].
- So: laptop gets sRGB-encoded output with relative tweaks only (gain, white, black lift); external gets the player's own gamut matrix and encode. The math checks: with Windows owning gamut the matrix is the identity; with the player owning it, it matches the textbook BT.709 to Display P3 matrix within 4e-6; applying the laptop's EDID primaries on top of ACM would turn sRGB red into (0.801, 0.036, 0.014) linear drive, i.e. double management [L].
- Microsoft recommends an FP16 scRGB swapchain for Advanced Color [V MS Learn, "Use DirectX with Advanced Color", updated 2025-09-19]; not tested. Windows' SDR transfer is said to be piecewise sRGB, not gamma 2.2 [S only].

## 2. Not done

- Not verified: whether Windows or the GPU re-quantizes our 8-bit dither on the ACM laptop. Only the near-black ramp by eye on the laptop panel can tell (phase 8).
- Not run: GPU cost of passes 2 to 4, any D3D11 run, any AMD 610M run, the by-eye checks.
- Two checks need fixing before a re-run: the surround hold check (off by one refresh; fixed in source, not re-run) and the flicker thresholds in `analyze-flicker.py` (provisional, to be set by eye).
- The surround time study was recorded (`out/surround/`, CSV and PNG) but its strobe scenarios came after the last run; no surround rule is chosen.

## 3. What exists (`spikes/picture-path/finish/`, untracked)

- `src/finish-passes.h`: the passes' whole API; `finish-surround*.c`, `finish-color*.c`, `finish-subs*.c`: passes 2 to 4 as C modules on libplacebo and libass.
- Test driver `src/test-*.c` and `finish-device.c`; `analyze-flicker.py`.
- `pwsh -File spikes\picture-path\finish\compile.ps1 [-Target test|unit|all]` compiles cleanly (checked 2026-10-12 10:20) [L]. `bin\finish-unit.exe`: 25 of 25 CPU checks pass. `bin\finish-test.exe <smoke|surround|color|subs|bench|all> [--backend vulkan|d3d11] [--gpu SUBSTR] [--out DIR]`: the Vulkan runs on the RTX 5070 Ti pass except the two checks above; `bench` is compiled but never run.
- The viewer target was removed from the compile script: its source was never written and the by-eye checks moved to phase 8.

## 4. For the ADR

Per monitor, after pass 1: pass 2 fills the leftover area from the picture's edge colours, kept in GPU state across refreshes; pass 3 applies the monitor's colour correction in linear light over picture and surround together, encodes, and dithers with our own blue-noise dither to the panel's bit depth; pass 4 draws libass subtitles at the monitor's own pixels on top, after dither. Gamut: on a monitor with Windows ACM on, Windows owns it and the player sends sRGB with relative tweaks only; with ACM off, the player maps gamut itself. Declare the black point when libplacebo renders the canvas.

## 5. Unresolved questions

1. Does Windows or the GPU re-quantize our dither on the ACM laptop panel (banding by eye, phase 8)?
2. Should the swapchain be FP16 scRGB on ACM monitors (Microsoft's recommendation) instead of 8-bit sRGB? Affects overlay (independent flip) and cost; untested.
3. Which surround rule at cuts, and its time constant: open until the by-eye check in phase 8.
