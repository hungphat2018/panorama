"""Scenario 5: breaks (seek, pause/resume, display-mode change) restart the film clock.

ADR 0021: every break restarts the clock on one master refresh K chosen far enough ahead for every monitor's
render lead; all monitors switch on it (each follower on its refresh nearest K, aimed at the seam row in light).
Compared with a naive restart: K is the first refresh the master can make; a follower shows the first frame on
its nearest refresh if it still can, else on the next one it can make.

Monte Carlo over many independent breaks at once, one master and one follower:
  - random grid phases and break times; each grid fitted from its last 240 wake-ups (perf1), or after a mode
    change from the first N wake-ups of the master's new mode (new phase);
  - a monitor with a lead of L refreshes prepares picture k right after the wake-up L refreshes earlier
    (perf1: L = 1), so it can show the new frame on picture k only if wake(k - L) + 0.3 ms >= ready + render;
  - the plan uses believed (fitted) times plus a margin; the outcome uses the true times.
"""
import numpy as np

RENDER_S = 1.0e-3            # ASSUMED render + submit time for an epoch's first frame (perf1: ~0.3 ms GPU)
PLAN_MARGIN_S = 0.5e-3       # margin the plan keeps against wake-up jitter
SEEK_READY_S = (0.03, 0.15)  # ASSUMED decode time of a seek target (uniform); resume and mode change: 0
SETTLE_COUNTS = (32, 64, 120, 240)


def _fit(rng, mon, phase, k_first, n, F):
    """Wake-corrected least-squares top-row grid from wake-ups k_first .. k_first + n - 1 of each break."""
    ks = k_first[:, None] + np.arange(n)[None, :]
    j = rng.normal(0.0, mon.wake_sd_s, ks.shape)
    out = rng.random(ks.shape) < F.PROBE_OUTLIER_RATE
    j[out] += rng.uniform(0.0, F.PROBE_OUTLIER_MAX_S, int(out.sum()))
    w = phase[:, None] + ks * mon.period - mon.mode.blank_s + mon.wake_delay_s + j
    kc, tc = ks.mean(axis=1), w.mean(axis=1)
    per = ((ks - kc[:, None]) * (w - tc[:, None])).sum(axis=1) / ((ks - kc[:, None]) ** 2).sum(axis=1)
    return kc, tc + mon.mode.blank_s - mon.wake_delay_s, per, w[:, -1]


def _restart(lib, fol, mas, lead_f, lead_m, frame_dur, rng, B, kind, settle=240, aim=None):
    F, R, decide = lib.fit, lib.rules, lib.tl.DECIDE_AFTER_WAKE_S
    aim = (min(fol.height_mm, mas.height_mm) / 2) if aim is None else aim
    pM, pF = mas.period, fol.period
    p, q = R.period_ratio(mas.mode.rate_hz, fol.mode.rate_hz)
    hold = R.speed_lock(frame_dur, pM)[0]
    even = hold * p / q
    t_b = 10.0 + rng.uniform(0.0, 1.0, B)
    phF = rng.uniform(0.0, pF, B)
    if kind == "mode change":             # new master phase from t_b; its picture 0 comes within one period
        phM, kM0, nM = t_b + rng.uniform(0.0, pM, B), np.zeros(B, dtype=np.int64), settle
    else:
        phM = rng.uniform(0.0, pM, B)
        kM0, nM = np.floor((t_b - phM) / pM).astype(np.int64) - 240, 240
    kcM, tcM, perM, lastM = _fit(rng, mas, phM, kM0, nM, F)
    t_dec = np.maximum(t_b, lastM + decide) if kind == "mode change" else t_b
    kcF, tcF, perF, _ = _fit(rng, fol, phF, np.floor((t_dec - phF) / pF).astype(np.int64) - 240, 240, F)
    ready = t_dec + RENDER_S + (rng.uniform(*SEEK_READY_S, B) if kind == "seek" else 0.0)

    top_hatM, top_hatF = (lambda k: tcM + perM * (k - kcM)), (lambda k: tcF + perF * (k - kcF))
    true_topM, true_topF = (lambda k: phM + k * pM), (lambda k: phF + k * pF)
    wake_of = lambda top, mon: top - mon.mode.blank_s + mon.wake_delay_s
    d = mas.light_offset_s(aim) - fol.light_offset_s(aim)
    # first master refresh K whose render thread (woken lead_m refreshes earlier) can still make it (believed)
    kmin = np.ceil((ready + PLAN_MARGIN_S - decide - wake_of(tcM, mas)) / perM + kcM + lead_m).astype(np.int64)

    def follower_for(K):                 # believed nearest follower refresh to K, its offset, can it make it
        x = (top_hatM(K) + d - tcF) / perF + kcF
        n = np.floor(x + 0.5).astype(np.int64)
        return n, np.abs(x - n) * perF, wake_of(top_hatF(n - lead_f), fol) + decide >= ready + PLAN_MARGIN_S

    res = {}
    for rule in ("naive", "adr"):
        if rule == "naive":
            K = kmin
        else:                            # per alignment class: first restart the follower can make; best class
            K, best = kmin.copy(), np.full(B, np.inf)
            for c in range(q):
                K_c, cost_c, found = kmin + c, np.full(B, np.inf), np.zeros(B, dtype=bool)
                for extra in range(4):
                    K_try = kmin + c + extra * q
                    _, cost, ok = follower_for(K_try)
                    take = ok & ~found
                    K_c, cost_c, found = np.where(take, K_try, K_c), np.where(take, cost, cost_c), found | ok
                better = cost_c < best - 1e-9
                K, best = np.where(better, K_c, K), np.where(better, cost_c, best)
        n_plan = follower_for(K)[0]
        n_act = n_plan.copy()            # outcome: a follower that cannot make its refresh shows it on the next
        for _ in range(lead_f + 4):
            late = wake_of(true_topF(n_act - lead_f), fol) + decide < ready
            n_act = np.where(late, n_act + 1, n_act)
        master_late = wake_of(true_topM(K - lead_m), mas) + decide < ready
        off = (true_topM(K) + mas.light_offset_s(aim)) - (true_topF(n_act) + fol.light_offset_s(aim))
        x_true = lambda k: (true_topM(k) + d - phF) / pF
        cls_off = lambda k: np.abs(x_true(k) - np.floor(x_true(k) + 0.5)) * pF
        worse = (cls_off(K) - np.min([cls_off(K + c) for c in range(q)], axis=0)) * 1e3
        first_pace = np.floor(x_true(K + hold) + 0.5).astype(np.int64) - n_act
        res[rule] = {
            "follower_late_fraction": round(float(np.mean(n_act > n_plan)), 5),
            "follower_late_refreshes_max": int((n_act - n_plan).max()),
            "master_late_fraction": round(float(np.mean(master_late)), 5),
            "first_frame_offset_aim_ms_p50_p95_max": [round(float(v), 4) for v in (
                np.percentile(np.abs(off) * 1e3, 50), np.percentile(np.abs(off) * 1e3, 95), np.abs(off).max() * 1e3)],
            "first_frame_follower_pace": {int(k): int(v) for k, v in zip(*np.unique(first_pace, return_counts=True))},
            "class_worse_than_best_ms_p95_max": [round(float(np.percentile(worse, 95)), 4), round(float(worse.max()), 4)],
            "break_to_first_frame_ms_p50_max": [round(float(np.percentile((true_topM(K) - t_b) * 1e3, 50)), 3),
                                                round(float(((true_topM(K) - t_b) * 1e3).max()), 3)],
        }
    err = np.abs(top_hatM(kmin) - true_topM(kmin)) * 1e6
    res["even_follower_pace"] = even
    res["master_fit_error_us_p95_max"] = [round(float(np.percentile(err, 95)), 3), round(float(err.max()), 3)]
    return res


def scenario_5(lib, cfg, B=20000):
    T = lib.timing
    d24 = 1001 / 24000
    lap96, ext96 = T.setup_a(T.EXT_96)
    lap72, ext72 = T.setup_a(T.EXT_72)
    other, lg60, fast = T.setup_b(T.LG_60, T.OTHER_60)
    cases = {  # name: (follower, master, follower lead, master lead)
        "240+96, one-refresh leads (perf1)": (lap96, ext96, 1, 1),
        "240+96, laptop queued two refreshes ahead": (lap96, ext96, 2, 1),
        "240+72, one-refresh leads": (lap72, ext72, 1, 1),
        "Setup B native: LG 60 Hz following the 144 Hz master (assumed timings)": (lg60, fast, 1, 1),
    }
    out = {"breaks_per_case": B, "render_s": RENDER_S, "plan_margin_s": PLAN_MARGIN_S, "seek_ready_s": SEEK_READY_S,
           "cases": {}}
    for i, (name, (fol, mas, lf, lm)) in enumerate(cases.items()):
        rng = np.random.default_rng([11, i])
        case = {kind: _restart(lib, fol, mas, lf, lm, d24, rng, B, kind) for kind in ("seek", "resume")}
        settles = SETTLE_COUNTS if i == 0 else (32,)
        case.update({f"mode change, settle {n} wake-ups": _restart(lib, fol, mas, lf, lm, d24, rng, B, "mode change",
                                                                   settle=n) for n in settles})
        out["cases"][name] = case
    return out
