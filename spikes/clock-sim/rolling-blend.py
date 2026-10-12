"""The rolling blend (zero-delay research, section 5): a finer screen copies the reference screen's wave row by row.

For one film frame change, the reference (master) row at height h changes in light at e(h). On the follower,
the row at the same height is lit by refresh k from a_k(h) until a_k(h) + T. The refresh straddling e(h)
(a_k <= e < a_k + T) shows the new frame with share w = (a_k + T - e) / T, mixed in light with the old one.
That row's light-weighted switch moment is w * a_k + (1 - w) * (a_k + T) = e, whatever a_k, T and e are.

The performer computes w from what it believes (fitted grids); the light comes from the true grids. The error of
the light-weighted moment is what the blend leaves: the difference between the two screens' grid-fit errors.
"""
import numpy as np


def blend_rows(e_true, e_hat, fol_grid, fol_tops, fol_period, row_off, t_dec):
    """One row-set of a follower against the reference wave, for many frame changes at once.

    e_true, e_hat: (n_changes, n_rows) reference light times, true and believed; row_off: (n_rows,) follower
    light offset of each row from its top-row signal; t_dec: (n_changes,) decision times (which fits are used).
    Returns (k, w, err): straddling refresh index, share of the new frame in it, and the light-weighted moment
    minus the true reference moment (seconds).
    """
    x, _ = fol_grid.index_hat(t_dec[:, None], e_hat - row_off[None, :])
    k = np.floor(x).astype(np.int64)
    frac = x - k                                   # (e_hat - a_hat_k) / T_hat
    moment = fol_tops[k] + row_off[None, :] + frac * fol_period
    return k, 1.0 - frac, moment - e_true


def mixed_refresh_counts(k, w, eps):
    """Per frame change: how many distinct follower refreshes carry a mix (some row with eps < w < 1 - eps),
    and the fraction of rows that show a mix."""
    mixed = (w > eps) & (w < 1.0 - eps)
    kk = np.where(mixed, k, -1)
    srt = np.sort(kk, axis=1)
    distinct = (np.diff(srt, axis=1) != 0) & (srt[:, 1:] >= 0)
    count = distinct.sum(axis=1) + (srt[:, 0] >= 0)
    return count, mixed.mean(axis=1)


def summarize_blend(err, w, k_fine, w_fine, eps):
    """Statistics of the blend: error of the light-weighted moment (all changes, coarse rows), how far each row's
    light-weighted frame duration is from the reference's, the new frame's share in mixed refreshes, and (fine
    rows, a sample of changes) how many follower refreshes per change carry a mix."""
    abs_us = np.abs(err) * 1e6
    per_row_max_us = abs_us.max(axis=0)
    lw_dur_err_us = np.abs(np.diff(err, axis=0)) * 1e6      # light-weighted frame duration minus reference's
    count, frac_rows = mixed_refresh_counts(k_fine, w_fine, eps)
    ws = w[(w > 1e-9) & (w < 1 - 1e-9)]
    return {
        "err_us_p50": float(np.percentile(abs_us, 50)),
        "err_us_p95": float(np.percentile(abs_us, 95)),
        "err_us_p999": float(np.percentile(abs_us, 99.9)),
        "err_us_max": float(abs_us.max()),
        "err_us_max_top_mid_bottom": [float(per_row_max_us[0]), float(per_row_max_us[len(per_row_max_us) // 2]),
                                      float(per_row_max_us[-1])],
        "lw_frame_duration_err_us_max": float(lw_dur_err_us.max()) if len(lw_dur_err_us) else 0.0,
        "share_new_p5_p50_p95": [round(float(x), 4) for x in np.percentile(ws, [5, 50, 95])] if ws.size else [],
        "rows_mixed_fraction": float(frac_rows.mean()),
        "mix_threshold": eps,
        "mixed_refreshes_per_change_mean": float(count.mean()),
        "mixed_refreshes_per_change_hist": {int(c): int(n) for c, n in zip(*np.unique(count, return_counts=True))},
    }


def exactness_check(n_changes=20000, n_rows=400, seed=7):
    """With exact timing the light-weighted moment must equal the reference row's moment. Random grids, random
    periods (multiples or not), random reference waves over 2 hours of times. Returns the max error in us."""
    rng = np.random.default_rng(seed)

    class Exact:                                   # a grid known exactly (time model "exact")
        def __init__(self, phase, period):
            self.phase, self.period = phase, period

        def index_hat(self, t_dec, t):
            return (t - self.phase) / self.period, self.period

    worst = 0.0
    for period in (1 / 240.0, 1 / 200.0, 1 / 144.0, 1 / 60.0):
        phase = rng.uniform(0, period)
        g = Exact(phase, period)
        tops = phase + np.arange(int(7300 / period)) * period
        scan_f = rng.uniform(0.003, 0.016)
        row_off = np.linspace(0, scan_f, n_rows) + rng.uniform(0, 0.02)      # rows plus a panel lag
        start = rng.uniform(1.0, 7190.0, n_changes)
        e = start[:, None] + np.linspace(0, rng.uniform(0.003, 0.02), n_rows)[None, :]
        _, _, err = blend_rows(e, e, g, tops, period, row_off, start)
        worst = max(worst, float(np.abs(err).max()))
    return worst * 1e6
