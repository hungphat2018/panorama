# 0020. Motion crosses a bezel with no delay

- **Status:** Accepted
- **Date:** 2026-10-11
- **Refines:** [0009](0009-bezels-placement-first-picture-continues-behind-bezel.md), [0016](0016-camera-slides-play-smooth.md)

## Context

The user watched white bars move from the laptop screen to the external screen in the per-monitor test ([spike report](../../plans/reports/spike-261011-1247-one-window-per-monitor.md)) and could feel a delay as they crossed the bezel. The user wants the crossing to feel seamless, aiming for zero delay.

In that test the two screens switched to the next picture 0.3–2.2 ms apart, as sent. One window spanning both screens is worse: −1.7 to +6.7 ms ([mixed-rate spike](../../plans/reports/spike-261011-1034-one-window-across-mixed-refresh-rates.md)). A crossing can also feel delayed for reasons the signal timing doesn't show:

- **Panel lag:** each monitor lights up its own time after the signal arrives.
- **Scan speed:** each screen draws top to bottom at its own speed (about 4 ms on the laptop panel, about 10 ms on the external), so lower rows drift apart.
- **Gap value:** the picture continues behind the bezel (ADR 0009). If the gap in the setup differs from the real one, a moving object comes out of the bezel too early or too late. The test used an assumed 20 mm gap.
- **Steps:** at film speed things move in steps, so the crossing happens in jumps.

## Decision

- An object moving across a bezel arrives on the other screen exactly when and where its motion says it should, as if the bezel were a window frame in front of one continuous picture. The target is zero delay.
- Zero is measured as seen, in the light leaving the panels, not only in the signal sent to them. Each monitor's lag and the real gap are part of the setup.
- It holds for all motion: camera slides and things moving on their own.

## Consequences

- The performer controls when each monitor switches pictures, per monitor; monitors with variable refresh follow the others exactly.
- The room model records each monitor's lag and each seam's gap. Setup includes an on-screen seam check with a moving pattern; the user only judges, software does the adjusting.
- Fixed-rate monitors leave a remainder of up to half the faster monitor's refresh (±2.1 ms at 240 Hz), and screens scanning at different speeds leave a remainder that grows down the screen. Setup reports what remains, and which hardware would close it.
- "Any flicker at the bezel fails" still holds; this is stricter.
