"""What the performer knows about each refresh grid: wake-up jitter, the perf1 wake-up fit, the scan-line fit.

Truth (display-timing.py): picture k's top row at top(k) = phase + k * period. The performer never sees that
directly; it sees
  - WaitForVBlank wake-ups, one per refresh, a little after the blank starts, with jitter (perf1-clock.c fits a
    least-squares line through the last 240 of them after every wake-up), or
  - scan-line samples (scanline-clock.h): each gives a top-row time to about a microsecond; the research
    suggests a 20 ms burst every few seconds, fitted with a line.
A Grid answers "when does picture K's top row come?" as the performer would at decision time t_dec, under one
of four time models:
  wake            perf1 as built: the fitted wake-up time IS the picture time (blank and wake delay ignored)
  wake-corrected  fitted wake-up time - median wake delay + blank length (scan-line probe calibration)
  scan            scan-line fit of top-row times
  exact           the true grid (for the exactness self-check)
"""
import json
from pathlib import Path

import numpy as np

TIME_MODELS = ("wake", "wake-corrected", "scan", "exact")
FIT_WINDOW = 240            # perf1-clock.c: least squares over the last 240 wake-ups
FIT_MIN = 32                # perf1-clock.c: first estimate after 32 wake-ups
PROBE_OUTLIER_RATE = 0.005  # ASSUMED: the idle probe saw "rare outliers up to +130 us"; the presenter logs
                            # show 0.2-1.0 % of wake-ups beyond +100 us under load
PROBE_OUTLIER_MAX_S = 130e-6
SCAN_BURST_EVERY_S = 2.0    # research: "a 20 ms burst every few seconds is enough"
SCAN_BURST_S = 0.020
SCAN_POLL_S = 17.5e-6       # one D3DKMTGetScanLine call (presentation research)
SCAN_FIT_BURSTS = 8         # 16 s of bursts per fit
SCAN_FIRST_BURST_S = 1.0    # first burst after the windows open; the first fit needs two bursts


def first_fit_time(model):
    """When the first usable fit exists (perf1's 32 wake-ups take under 0.7 s even at 48 Hz)."""
    return SCAN_FIRST_BURST_S + SCAN_BURST_EVERY_S + SCAN_BURST_S if model == "scan" else 0.7

PRESENTER_LOGS = ("results-2026-10-11-24fps-96hz-aimed", "results-2026-10-11-24fps-96hz",
                  "results-2026-10-11-24fps-72hz-aimed", "results-2026-10-11")


def true_tops(mon, t_end, rng, phase=None):
    """Top-row times of every refresh from time 0 to t_end (plus one), random phase unless given."""
    p = mon.period
    ph = rng.uniform(0.0, p) if phase is None else phase
    n = int((t_end - ph) / p) + 2
    return ph + np.arange(n) * p


def load_wake_pools(spikes_dir):
    """Wake-up residuals (seconds, about their median) from the per-monitor presenter's logs [L]: monitor 0
    is the laptop, monitor 1 the external. Each run's wake-ups are indexed by gap and fitted with one line."""
    pools = {"laptop": [], "external": []}
    base = Path(spikes_dir) / "per-monitor-presenter"
    for run in PRESENTER_LOGS:
        for mon, role, nominal in ((0, "laptop", 1 / 240.0), (1, "external", None)):
            f = base / run / f"monitor-{mon}-nearest.json"
            if not f.exists():
                continue
            vb = np.asarray(json.loads(f.read_text())["vblanks"])
            nom = nominal or float(np.median(np.diff(vb)))
            k = np.concatenate([[0], np.cumsum(np.maximum(1, np.round(np.diff(vb) / nom)))])
            a = np.polyfit(k, vb - vb[0], 1)
            r = (vb - vb[0]) - np.polyval(a, k)
            pools[role].append(r - np.median(r))
    return {role: np.concatenate(v) for role, v in pools.items() if v}


def wake_jitter(n, mon, rng, model, pools=None):
    """Wake-up delay around its median: 'probe' = normal(sd) plus rare outliers up to +130 us,
    'log' = bootstrap of the presenter logs' residuals, 'none' = no jitter."""
    if model == "none":
        return np.zeros(n)
    if model == "log":
        pool = pools[mon.wake_role]
        return pool[rng.integers(0, len(pool), n)]
    j = rng.normal(0.0, mon.wake_sd_s, n)
    out = rng.random(n) < PROBE_OUTLIER_RATE
    j[out] += rng.uniform(0.0, PROBE_OUTLIER_MAX_S, int(out.sum()))
    return j


def rolling_fit(t, nominal, window=FIT_WINDOW, min_n=FIT_MIN):
    """Least-squares line through the last `window` samples, after every sample (perf1-clock.c vclock_add).

    Sample i has index i (one wake-up per refresh; perf1 indexes by gap, which gives the same indices when no
    wake-up is missed). Returns (kc, tc, period): centroid index and time and slope of each fit, NaN until
    min_n samples exist. The prediction for index K is tc + period * (K - kc).
    """
    n = len(t)
    k = np.arange(n, dtype=float)
    u = (t - t[0]) - k * nominal               # detrended, so the sums stay small and exact
    kc = np.full(n, np.nan)
    uc = np.full(n, np.nan)
    slope = np.full(n, np.nan)
    m = min(n, window - 1)                     # partial windows [0, i] at the start
    if m >= min_n:
        i = np.arange(m, dtype=float)
        cnt = i + 1.0
        sk, skk = i * (i + 1) / 2.0, i * (i + 1) * (2 * i + 1) / 6.0
        su, sku = np.cumsum(u[:m]), np.cumsum(k[:m] * u[:m])
        ok = i >= min_n - 1
        den = np.where(ok, cnt * skk - sk * sk, 1.0)
        slope[:m][ok] = ((cnt * sku - sk * su) / den)[ok]
        kc[:m][ok] = (sk / cnt)[ok]
        uc[:m][ok] = (su / cnt)[ok]
    if n >= window:                            # full windows, by convolution with fixed kernels
        w = np.arange(window) - (window - 1) / 2.0
        slope[window - 1:] = np.convolve(u, w[::-1], mode="valid") / np.sum(w * w)
        cs = np.concatenate([[0.0], np.cumsum(u)])
        uc[window - 1:] = (cs[window:] - cs[:-window]) / window
        kc[window - 1:] = np.arange(window - 1, n) - (window - 1) / 2.0
    return kc, t[0] + kc * nominal + uc, nominal + slope


def scan_bursts(mon, tops, rng, t_first, every_s=SCAN_BURST_EVERY_S, window=SCAN_FIT_BURSTS):
    """Scan-line top-row fit: a dense burst every `every_s`; each burst averages its picture-row samples into
    one top-row time (error sd = sample sd / sqrt(samples)); a line through the last `window` bursts.
    Returns (obs_t, kc, tc, period) per burst."""
    tb = np.arange(t_first, tops[-1] - every_s, every_s)
    kb = np.searchsorted(tops, tb)                           # the refresh being drawn during the burst
    samples = SCAN_BURST_S / SCAN_POLL_S * mon.mode.scan_s / mon.period
    est = tops[kb] + rng.normal(0.0, mon.scan_sd_s / np.sqrt(samples), len(kb))
    nb = len(kb)
    kc, tc, per = (np.full(nb, np.nan) for _ in range(3))
    for i in range(1, nb):                                   # few thousand bursts: a plain loop is fine
        lo = max(0, i - window + 1)
        kk, tt = kb[lo:i + 1].astype(float), est[lo:i + 1]
        p = np.polyfit(kk - kk.mean(), tt - tt.mean(), 1)[0]
        kc[i], tc[i], per[i] = kk.mean(), tt.mean(), p
    return tb + SCAN_BURST_S, kc, tc, per


class Grid:
    """One monitor's refresh grid as the performer knows it, under one time model."""

    def __init__(self, mon, tops, wake_rng, model, jitter="probe", pools=None, scan_rng=None,
                 t_first=SCAN_FIRST_BURST_S):
        self.mon, self.tops, self.model = mon, tops, model
        self.phase, self.period = tops[0], mon.period
        blank = mon.mode.blank_s
        # wake-up k comes in the blank just before picture k (drawn for every model: decision times use it)
        self.wakes = tops - blank + mon.wake_delay_s + wake_jitter(len(tops), mon, wake_rng, jitter, pools)
        self.wakes[0] = max(self.wakes[0], 0.0)
        if model in ("wake", "wake-corrected"):
            self.obs = self.wakes
            self.kc, self.tc, self.per = rolling_fit(self.wakes, mon.mode.period_s)
            self.shift = 0.0 if model == "wake" else blank - mon.wake_delay_s
        elif model == "scan":
            self.obs, self.kc, self.tc, self.per = scan_bursts(mon, tops, scan_rng or wake_rng, t_first)
            self.shift = 0.0
        elif model != "exact":
            raise ValueError(model)

    def _fit(self, t_dec):
        i = np.searchsorted(self.obs, t_dec, side="right") - 1
        return self.kc[i], self.tc[i] + self.shift, self.per[i]

    def top_hat(self, t_dec, k):
        """Picture k's top-row time as believed at t_dec."""
        if self.model == "exact":
            return self.phase + np.asarray(k, dtype=float) * self.period
        kc, tc, per = self._fit(t_dec)
        return tc + per * (k - kc)

    def index_hat(self, t_dec, t):
        """Continuous refresh index whose top row is believed to come at time t (and the believed period)."""
        if self.model == "exact":
            return (np.asarray(t) - self.phase) / self.period, np.full(np.shape(t), self.period)
        kc, tc, per = self._fit(t_dec)
        return kc + (t - tc) / per, per

    def valid_from(self):
        """Earliest decision time with a usable fit."""
        if self.model == "exact":
            return 0.0
        ok = np.flatnonzero(np.isfinite(self.per))
        return float(self.obs[ok[0]])
