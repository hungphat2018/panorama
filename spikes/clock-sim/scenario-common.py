"""Helpers shared by the scenarios: content, running policy variants over seeds, forced boundary crossings."""
from dataclasses import dataclass

import numpy as np

SETUP_A_ROWS = (0.0, 107.5, 215.0)     # tops level; the picture is the laptop's 215 mm tall: top, middle, bottom


@dataclass
class Variant:
    name: str
    time_model: str                    # wake | wake-corrected | scan | exact
    kind: str                          # crisp | blend | uniform-blend | vrr
    policy: object                     # seam-timeline Policy
    jitter: str = "probe"              # probe | log


def cfr_content(lib, num, den, seconds, round_ms, seed, name):
    """Constant-rate frames num/den fps (timestamps rounded to whole ms, as in MKV, or exact), cuts every 2-8 s."""
    dur = den / num
    n = int(seconds * 1.02 / dur) + 200
    pts = np.arange(n) * dur
    if round_ms:
        pts = np.round(pts * 1000.0) / 1000.0
    cuts = lib.rules.make_cuts(n, dur, np.random.default_rng([seed, 5]))
    return lib.tl.Content(name, pts, dur, cuts)


def run_variants(lib, monitors, master, seams_for, content, variants, seeds, seconds, pools, phases_for=None):
    """Runs every variant on every seed; sessions are shared by variants with the same time model and jitter.
    seams_for(kind) -> seams dict; phases_for(seed) -> optional true phases. Returns name -> pooled metrics."""
    results = {v.name: [] for v in variants}
    keys = sorted({(v.time_model, v.jitter) for v in variants})
    for seed in seeds:
        phases = phases_for(seed) if phases_for else None
        for tm, jit in keys:
            ses = lib.tl.Session(lib, monitors, seconds, seed, tm, jit, pools, phases)
            for v in variants:
                if (v.time_model, v.jitter) != (tm, jit):
                    continue
                seams = seams_for(v.kind)
                run = lib.tl.play(lib, ses, master, seams, content, v.policy)
                results[v.name].append(lib.metrics.measure(lib, ses, master, seams, run))
    return {name: lib.metrics.pool(r) for name, r in results.items()}


def crossing_phases(lib, follower, master, seconds, seed, aim_mm, aim_rows, hold, time_model):
    """True phases that put the follower's nearest-refresh boundary (offset exactly half a refresh) at the
    master frame change nearest the middle of the run, for an un-aimed start (switches at base + hold * i)."""
    rng = np.random.default_rng([seed, 9])
    pm, pf = master.period, follower.period
    phi_m = rng.uniform(0.0, pm)
    base = int(np.ceil((lib.tl.ready_time(lib, time_model) - phi_m) / pm))
    i_mid = int(round((seconds / 2 - (phi_m + base * pm)) / (hold * pm)))
    t_switch = phi_m + (base + hold * i_mid) * pm
    if aim_rows:
        d = master.light_offset_s(aim_mm) - follower.light_offset_s(aim_mm)
    else:   # perf1 compares wake-up grids: blank start + wake delay on each side
        d = (-master.mode.blank_s + master.wake_delay_s) - (-follower.mode.blank_s + follower.wake_delay_s)
    m = np.floor((t_switch + d) / pf - 0.5)
    return t_switch + d - (m + 0.5) * pf, phi_m


def pick(d, *keys):
    """Small helper for summaries: nested dict lookup returning None when missing."""
    for k in keys:
        if not isinstance(d, dict) or k not in d:
            return None
        d = d[k]
    return d
