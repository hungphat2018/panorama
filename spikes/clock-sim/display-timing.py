"""Display timing model for the clock simulator: exact signal timing per mode, rows by physical height,
and the measured (or assumed) monitors of Setup A and Setup B.

Time model of one monitor (signal timing, as in spikes/per-monitor-presenter/scanline-clock.h):
  - refresh k draws its top picture row at top(k) = phase + k * period (QPC seconds);
  - row y follows y line times later; the bottom row ends one scan time after the top row;
  - then the blank, until the next top row;
  - IDXGIOutput::WaitForVBlank wakes the waiting thread a little after the blank starts;
  - light leaves a row one panel lag after it is drawn and stays until the row is drawn again.
Wake-up k in this simulator is the wake-up in the blank just before picture k.
"""
from dataclasses import dataclass, replace

import numpy as np


@dataclass(frozen=True)
class Mode:
    """Exact signal timing of one display mode (pixel totals and pixel clock)."""
    total_w: int
    total_h: int
    active_h: int
    pixel_hz: float

    @property
    def line_s(self):
        return self.total_w / self.pixel_hz

    @property
    def rate_hz(self):
        return self.pixel_hz / (self.total_w * self.total_h)

    @property
    def period_s(self):
        return 1.0 / self.rate_hz

    @property
    def scan_s(self):
        return self.active_h * self.line_s

    @property
    def blank_s(self):
        return (self.total_h - self.active_h) * self.line_s


@dataclass(frozen=True)
class Monitor:
    """One monitor: its mode, its true period against QPC, its place in the room, and how it is observed."""
    name: str
    mode: Mode
    height_mm: float
    top_mm: float = 0.0            # top edge in the shared physical frame (0 = tops level with the tallest)
    period_s: float = 0.0          # true refresh period against QPC; 0 = the mode's nominal period
    wake_delay_s: float = 94e-6    # median WaitForVBlank wake-up after the blank starts
    wake_sd_s: float = 17e-6       # wake-up jitter (sd) around that
    wake_role: str = "external"    # which logged wake-up residuals to bootstrap ("laptop" or "external")
    scan_sd_s: float = 3.41e-6     # one scan-line sample's top-row error (sd = residual p50 / 0.6745)
    lag_s: float = 0.0             # panel lag, signal to light; assumed known to the performer (calibrated)

    @property
    def period(self):
        return self.period_s or self.mode.period_s

    @property
    def rate_hz(self):
        return 1.0 / self.period

    def row_s(self, h_mm):
        """Time from the top row to the row at physical height h_mm (clipped to the screen)."""
        frac = np.clip((np.asarray(h_mm, dtype=float) - self.top_mm) / self.height_mm, 0.0, 1.0)
        return frac * self.mode.scan_s

    def light_offset_s(self, h_mm):
        """Time from a refresh's top-row signal to the light of the row at h_mm."""
        return self.row_s(h_mm) + self.lag_s

    def with_period_ratio(self, reference, ratio, drift_ppm):
        """Same monitor with its true period tied to `reference`: one GPU clock drives both heads, so the
        period ratio is fixed, off the nominal ratio by drift_ppm (positive: this monitor runs slow)."""
        return replace(self, period_s=reference.period * ratio * (1.0 + drift_ppm * 1e-6))


# ---- Setup A, measured 2026-10-11 [L] -------------------------------------------------------------------
# Laptop panel 240 Hz: 2720 x 1800 total, 1600 active, 1175.045 MHz (CCD pixel rate, rounded by Windows).
LAPTOP_240 = Mode(2720, 1800, 1600, 1175.045e6)
# External, native 100 Hz and the saved custom modes (native pixel clock, longer vertical blank).
EXT_100 = Mode(2000, 1115, 1080, 223.0e6)
EXT_96 = Mode(2000, 1160, 1080, 222.72e6)
EXT_72 = Mode(2000, 1545, 1080, 222.48e6)
# Laptop custom modes for the 25 fps ladder [D, untested on the panel]: native line rate, longer blank.
LAPTOP_200 = Mode(2720, 2160, 1600, 1175.04e6)
LAPTOP_150 = Mode(2720, 2880, 1600, 1175.04e6)

# Scan-line probe: top-row residual p50 0.6 us (laptop), 2.3 us (external); sd = p50 / 0.6745 for a normal.
SCAN_SD_LAPTOP = 0.6e-6 / 0.6745
SCAN_SD_EXTERNAL = 2.3e-6 / 0.6745

# Period errors fitted against QPC (scan-line probe): laptop -0.75 ppm, external at 100 Hz -4.44 ppm.
LAPTOP_PPM_VS_QPC = -0.75
EXT100_PPM_VS_QPC = -4.44
# Drift between the heads at 240 + 96 Hz measured by the zero-delay research: 0.5 us per second.
MEASURED_DRIFT_PPM = 0.5
# The laptop's 240 Hz pixel clock is exactly 4 x 293.76 MHz = 1175.04 MHz (Windows rounds it to 1175.045), so
# its exact rate, used for nominal ratios between heads, is 240.000 Hz [presentation research].
LAPTOP_EXACT_HZ = 240.0

LAPTOP_H_MM = 215.0
EXTERNAL_H_MM = 268.0


def setup_a(ext_mode, drift_ppm=MEASURED_DRIFT_PPM, laptop_mode=LAPTOP_240, laptop_drift_ppm=None):
    """Laptop + external, tops level. Returns (laptop, external) with true periods.

    The laptop's true period comes from its CCD rate and the fitted -0.75 ppm. At 100 Hz the external's true
    period comes from its own fitted -4.44 ppm (0.56 ppm slower than the laptop's grid, relatively). In a custom
    mode the external is tied to the laptop by the nominal ratio and `drift_ppm` (measured 0.5 at 96 Hz;
    assumed the same at 72 Hz). A laptop custom mode (25 fps ladder) is tied to the external the same way.
    """
    laptop = Monitor("laptop", LAPTOP_240, LAPTOP_H_MM, wake_delay_s=67e-6, wake_sd_s=21e-6,
                     wake_role="laptop", scan_sd_s=SCAN_SD_LAPTOP,
                     period_s=LAPTOP_240.period_s * (1.0 + LAPTOP_PPM_VS_QPC * 1e-6))
    external = Monitor("external", ext_mode, EXTERNAL_H_MM, wake_delay_s=94e-6, wake_sd_s=17e-6,
                       wake_role="external", scan_sd_s=SCAN_SD_EXTERNAL)
    if ext_mode is EXT_100:
        external = replace(external, period_s=EXT_100.period_s * (1.0 + EXT100_PPM_VS_QPC * 1e-6))
    else:
        external = external.with_period_ratio(laptop, LAPTOP_EXACT_HZ / ext_mode.rate_hz, drift_ppm)
    if laptop_mode is not LAPTOP_240:
        ratio = external.mode.rate_hz / laptop_mode.rate_hz
        drift = drift_ppm if laptop_drift_ppm is None else laptop_drift_ppm
        laptop = replace(laptop, mode=laptop_mode).with_period_ratio(external, ratio, -drift)
    return laptop, external


# ---- Setup B: ASSUMED timings (not measured; only the LG's EDID is on record) ------------------------------
# LG UltraFine 4K, 27-inch class: EDID native 3840x2160 at 60 Hz, 4400 x 2250 at 594 MHz [L EDID, room research];
# 48 Hz "fast scan" custom mode 4400 x 2750 at 580.8 MHz (132 kHz lines, inside its 30-135 kHz range) [D red team].
LG_60 = Mode(4400, 2250, 2160, 594.0e6)
LG_48 = Mode(4400, 2750, 2160, 580.8e6)
# Other 60 Hz screen: ASSUMED 24-inch 1920x1080, CEA timing 2200 x 1125 at 148.5 MHz; 48 Hz at the native
# line rate (2200 x 1400 at 147.84 MHz, exactly 48.000 Hz); 72 Hz with the same totals (178.2 MHz, if accepted).
OTHER_60 = Mode(2200, 1125, 1080, 148.5e6)
OTHER_48 = Mode(2200, 1400, 1080, 147.84e6)
OTHER_72 = Mode(2200, 1125, 1080, 178.2e6)
# 144 Hz screen: ASSUMED 27-inch 2560x1440, reduced-blanking totals 2720 x 1481, exactly 144 Hz.
HZ144 = Mode(2720, 1481, 1440, 2720 * 1481 * 144.0)

H_27_MM = 27 * 25.4 * 9 / np.hypot(16, 9)   # 336.2 mm, 16:9
H_24_MM = 24 * 25.4 * 9 / np.hypot(16, 9)   # 298.9 mm


def setup_b(lg_mode=LG_48, other_mode=OTHER_48, fast_mode=HZ144, drift_ppm=MEASURED_DRIFT_PPM, seed_ppm=None):
    """Three screens side by side, vertical centres level [assumed]: other 60 Hz | LG 4K | 144 Hz.

    One GPU is assumed to drive all three, so periods are tied to the LG by nominal ratios, each off by a drift
    (default the 0.5 ppm measured on Setup A). `seed_ppm` overrides the per-monitor drifts (e.g. screens on
    different graphics adapters with independent crystals). Wake-up jitter: Setup A external's numbers.
    """
    tall = H_27_MM
    lg = Monitor("lg-4k", lg_mode, H_27_MM, top_mm=(tall - H_27_MM) / 2)
    other = Monitor("other", other_mode, H_24_MM, top_mm=(tall - H_24_MM) / 2)
    fast = Monitor("fast-144", fast_mode, H_27_MM, top_mm=(tall - H_27_MM) / 2)
    d_other, d_fast = (drift_ppm, -drift_ppm) if seed_ppm is None else seed_ppm
    other = other.with_period_ratio(lg, lg_mode.rate_hz / other_mode.rate_hz, d_other)
    fast = fast.with_period_ratio(lg, lg_mode.rate_hz / fast_mode.rate_hz, d_fast)
    return other, lg, fast
