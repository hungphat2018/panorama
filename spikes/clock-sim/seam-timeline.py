"""Timeline engine: one film on N monitors for hours, decided from fitted grids, measured on true grids.

play() applies ADR 0021's rules with switchable parts:
  - film clock from the master's refresh count, speed lock <= 1 %, frames picked by timestamp;
  - aimed start: the film starts on the master refresh (one per alignment class) whose followers' nearest
    refreshes are closest, at each seam's aimed row in light (or in raw grid times, as perf1 does);
  - re-aim at cuts: at a cut the master may move its frame changes by one refresh (another alignment class)
    when that is better by more than the hysteresis, staying within one refresh of the start;
  - followers: nearest refresh per alignment class, with hysteresis, moving anytime / only at cuts / never;
    chained followers aim at their neighbour's chosen refresh at their own seam's row.
Blend and VRR followers do not choose refreshes; seam-metrics.py measures them against the master's wave.
"""
import math
from dataclasses import dataclass, field

import numpy as np

DECIDE_AFTER_WAKE_S = 0.3e-3     # a render thread picks a refresh's picture just after the previous wake-up
SETTLE_S, FILM_LEAD_S = 1.5, 0.5  # perf1: grids settle 1.5 s, the film starts at least 0.5 s later


class Session:
    """True grids and the performer's fitted grids of a set of monitors, for one seed and one time model.
    The same seed gives the same truth and the same wake-ups under every time model."""

    def __init__(self, lib, monitors, seconds, seed, time_model, jitter="probe", pools=None, phases=None):
        truth = np.random.default_rng([seed, 0])
        self.monitors, self.seconds, self.time_model = list(monitors), seconds, time_model
        self.tops = [lib.fit.true_tops(m, seconds + 60.0, truth, None if phases is None else phases[i])
                     for i, m in enumerate(self.monitors)]
        self.grids = [lib.fit.Grid(m, t, np.random.default_rng([seed, 1, i]), time_model, jitter, pools,
                                   np.random.default_rng([seed, 2, i]))
                      for i, (m, t) in enumerate(zip(self.monitors, self.tops))]


@dataclass
class Seam:
    mon: int                   # follower monitor
    ref: int                   # the monitor it follows (the master, or a neighbour follower: a chain)
    rows_mm: tuple             # (top, aimed, bottom) heights of the seam, physical frame
    kind: str = "crisp"        # crisp | blend | uniform-blend | vrr

    @property
    def aim_mm(self):
        return self.rows_mm[1]


@dataclass
class Policy:
    aimed_start: bool = True
    reaim: str = "never"       # never | cuts
    moves: str = "anytime"     # anytime | cuts | never
    eta_s: float = 0.0         # hysteresis, seconds
    pick: str = "due"          # due (last frame due, half a master refresh of tolerance) | pl_queue | strict
    aim_rows: bool = True      # aim in light at the seam row; False = compare raw grid times (perf1)


@dataclass
class Content:
    name: str
    pts: np.ndarray            # frame timestamps, film seconds from the first frame
    frame_dur: float           # nominal frame duration (container rate)
    cuts: np.ndarray           # True at the first frame of each shot
    extra: dict = field(default_factory=dict)


def ready_time(lib, time_model):
    """When the film may start: grids settled (perf1 SETTLE_S, or the first scan-line fit) plus the lead."""
    return max(SETTLE_S, lib.fit.first_fit_time(time_model)) + FILM_LEAD_S


def pick_frames(rules, content, film_t, step, how):
    if how == "pl_queue":
        return rules.pl_queue_pick(content.pts, film_t, step, content.frame_dur)
    return rules.last_frame_due(content.pts, film_t, 0.5 * step if how == "due" else 0.0)


def _reference(ses, master, seam, J, chosen, seams):
    """Monitor whose rows define the target, and its true and believed top-row times per frame change. A crisp
    neighbour is followed at its chosen refresh; a blend/VRR neighbour reproduces the master's wave."""
    ref = seam.ref
    if ref != master and seams[ref].kind == "crisp":
        n = chosen[ref]
        g = ses.grids[ref]
        return ref, ses.tops[ref][n], (lambda t: g.top_hat(t, n))
    g = ses.grids[master]
    return master, ses.tops[master][J], (lambda t: g.top_hat(t, J))


def _target(ses, seam, ref_mon, ref_true, ref_hat, aim_rows):
    """Continuous follower refresh index of the reference change at the seam's aimed row: true (u) and
    believed at the follower's decision time (x), with the believed period, the decision time, and the believed
    reference top-row time (blend and VRR followers work from it)."""
    mf, mr = ses.monitors[seam.mon], ses.monitors[ref_mon]
    d = (mr.light_offset_s(seam.aim_mm) - mf.light_offset_s(seam.aim_mm)) if aim_rows else 0.0
    tops = ses.tops[seam.mon]
    u = (ref_true + d - tops[0]) / mf.period
    nc = np.clip(np.floor(u).astype(np.int64) - 1, 0, len(tops) - 1)
    t_dec = ses.grids[seam.mon].wakes[nc] + DECIDE_AFTER_WAKE_S
    ref_top_hat = ref_hat(t_dec)
    x, per = ses.grids[seam.mon].index_hat(t_dec, ref_top_hat + d)
    return u, x, per, t_dec, ref_top_hat


def play(lib, ses, master, seams, content, pol):
    """Plays `content` with `pol`; returns the frame changes and every follower's decisions."""
    R = lib.rules
    gM, tM, mM = ses.grids[master], ses.tops[master], ses.monitors[master]
    t_ready = max(ready_time(lib, ses.time_model), max(g.valid_from() for g in ses.grids) + FILM_LEAD_S)
    base = int(np.searchsorted(tM, t_ready))
    p_hat = float(gM.index_hat(t_ready, t_ready)[1])
    hold, speed, step, locked = R.speed_lock(content.frame_dur, p_hat)
    crisp = [s for s in seams.values() if s.kind == "crisp"]
    ratio = {s.mon: R.period_ratio(mM.mode.rate_hz, ses.monitors[s.mon].mode.rate_hz) for s in seams.values()}
    Q = math.lcm(*[ratio[s.mon][1] for s in crisp]) if crisp else 1
    n_ref = int((ses.seconds - tM[base]) / mM.period) - 2 * Q - 2
    frames = pick_frames(R, content, np.arange(n_ref) * step, step, pol.pick)
    pos, shown = R.frame_changes(frames)
    keep = shown >= 0
    pos, shown = pos[keep], shown[keep]
    is_cut = np.zeros(len(shown), dtype=bool)
    ok = shown < len(content.cuts)
    is_cut[ok] = content.cuts[shown[ok]]
    is_cut[0] = True

    def chain_cost(J):           # worst believed seam offset (s) if every crisp follower took its nearest refresh
        chosen, cost = {}, np.zeros(len(J))
        for s in crisp:
            ref_mon, ref_true, ref_hat = _reference(ses, master, s, J, chosen, seams)
            u, x, per, _, _ = _target(ses, s, ref_mon, ref_true, ref_hat, pol.aim_rows)
            chosen[s.mon] = np.floor(x + 0.5).astype(np.int64)
            cost = np.maximum(cost, np.abs(x - chosen[s.mon]) * per)
        return cost

    cut_idx = np.flatnonzero(is_cut)
    s0 = 0
    if pol.aimed_start and Q > 1:
        first_seg = slice(0, cut_idx[1] if len(cut_idx) > 1 else len(pos))
        s0 = int(np.argmin([chain_cost(base + s + pos[first_seg]).max() for s in range(Q)]))
    shift = np.full(len(pos), s0, dtype=np.int64)
    if pol.reaim == "cuts" and Q > 1:
        # Re-aim within one master refresh of the start, so picture and sound stay within one refresh; never shorten
        # the frame before the cut below one refresh (a 1-refresh frame of a variable-rate file would be lost).
        cands = [s0 - 1, s0, s0 + 1]
        seg = np.maximum.reduceat(np.stack([chain_cost(base + s + pos) for s in cands]), cut_idx, axis=1)
        prev_len = pos[cut_idx[1:]] - pos[cut_idx[1:] - 1]
        cur, order = 1, (1, 0, 2)                                    # ties: the smaller shift
        per_seg = [s0]
        for i in range(1, len(cut_idx)):
            ok = [c for c in order if prev_len[i - 1] + cands[c] - cands[cur] >= 1]
            best = min(ok, key=lambda c: seg[c, i])
            if seg[best, i] < seg[cur, i] - pol.eta_s:
                cur = best
            per_seg.append(cands[cur])
        shift = np.repeat(np.asarray(per_seg, dtype=np.int64), np.diff(np.append(cut_idx, len(pos))))
    J = base + shift + pos

    allowed = {"anytime": np.ones(len(pos), bool), "cuts": is_cut, "never": np.zeros(len(pos), bool)}[pol.moves]
    chosen, info = {}, {}
    runs = np.concatenate([[0], np.flatnonzero(np.diff(shift)) + 1, [len(pos)]])
    for s in seams.values():                 # dict order = chain order
        pieces = []
        for a, b in zip(runs[:-1], runs[1:]):
            ref_mon, ref_true, ref_hat = _reference(ses, master, s, J[a:b], {k: v[a:b] for k, v in chosen.items()},
                                                    seams)
            u, x, per, t_dec, ref_top_hat = _target(ses, s, ref_mon, ref_true, ref_hat, pol.aim_rows)
            n = None
            if s.kind == "crisp":
                p, q = ratio[s.mon]
                eta = pol.eta_s / ses.monitors[s.mon].period
                n = R.follower_choice(x, J[a:b], p, q, allowed[a:b], eta)
            pieces.append((u, x, per, t_dec, ref_top_hat, n))
        cat = [np.concatenate([pc[i] for pc in pieces]) if pieces[0][i] is not None else None for i in range(6)]
        info[s.mon] = dict(zip(("u", "x", "per", "t_dec", "ref_top_hat", "n"), cat))
        if s.kind == "crisp":
            chosen[s.mon] = cat[5]
    return dict(J=J, pos=pos, shift=shift, shown=shown, is_cut=is_cut, hold=hold, speed=speed, step=step,
                locked=locked, Q=Q, ratio=ratio, info=info, base=base, s0=s0)
