"""The performer's film-clock rules (ADR 0021), as pure functions over arrays.

- Film clock: film time at master refresh j is (j - j0) * step; step = frame duration / hold when the speed lock
  (at most 1 %) makes a frame last a whole number of master refreshes (perf1 choose_film_start).
- Frames are picked by timestamp: on each master refresh, the last frame due ("pts <= film time + tolerance"),
  or perf1's shortcut through pl_queue (queue_position + libplacebo point(), nearest frame to a shifted time).
- Followers: each master frame change maps to the follower refresh nearest it; switches whose master refreshes are
  a whole number of follower-grid periods apart form an alignment class; each class keeps its chosen refresh
  offset until the nearest one is better by more than a hysteresis, and may move only where allowed (cuts).
"""
from fractions import Fraction

import numpy as np


def speed_lock(frame_dur, master_period, max_nudge=0.01):
    """Hold (master refreshes per frame), playback speed, film seconds per master refresh, and whether locked."""
    hold = max(1, int(round(frame_dur / master_period)))
    speed = frame_dur / (hold * master_period)
    locked = abs(speed - 1.0) <= max_nudge
    return hold, speed, (frame_dur / hold if locked else master_period), locked


def whole_multiple_speeds(rate_hz, film_fps, max_nudge=0.01, n_max=12):
    """Speeds within +-max_nudge at which a monitor at rate_hz shows a whole number of refreshes per frame."""
    out = []
    for n in range(1, n_max + 1):
        speed = rate_hz / (n * film_fps)
        if abs(speed - 1.0) <= max_nudge:
            out.append((n, speed))
    return out


def nearest_whole(rate_hz, film_fps):
    """The two whole refresh counts around rate/fps and the speed change each would need."""
    r = rate_hz / film_fps
    return [(n, rate_hz / (n * film_fps) - 1.0) for n in (int(np.floor(r)), int(np.ceil(r))) if n > 0]


def period_ratio(master_rate_hz, follower_rate_hz, max_den=64):
    """Master period / follower period as p/q in lowest terms (q master refreshes = p follower refreshes)."""
    f = Fraction(follower_rate_hz / master_rate_hz).limit_denominator(max_den)
    return f.numerator, f.denominator


def last_frame_due(pts, film_t, tol):
    """Index of the last frame with pts <= film time + tol (-1 before the first frame)."""
    return np.searchsorted(pts, np.asarray(film_t, dtype=float) + tol, side="right") - 1


def pl_queue_pick(pts, film_t, step, frame_dur):
    """perf1: pl_queue asked for pos = film time + step/2 - frame_dur/2 (queue_position), with interpolation off.
    libplacebo frame_queue.c: advance() keeps the last frame with pts <= pos and the next one; point() returns
    whichever is nearer to pos (strictly nearer, so the earlier frame on a tie); nothing before the first frame."""
    pos = np.maximum(np.asarray(film_t, dtype=float) + 0.5 * step - 0.5 * frame_dur, 0.0)
    i0 = np.searchsorted(pts, pos, side="right") - 1
    i1 = np.minimum(i0 + 1, len(pts) - 1)
    d0 = np.abs(pts[np.maximum(i0, 0)] - pos)
    d1 = np.abs(pts[i1] - pos)
    pick = np.where((i1 > i0) & (d1 < d0), i1, i0)
    return np.where(i0 < 0, -1, pick)


def frame_changes(frames):
    """Positions (master refresh offsets from j0) where the shown frame changes, and the frame shown from there."""
    pos = np.concatenate([[0], np.flatnonzero(np.diff(frames) != 0) + 1])
    return pos, frames[pos]


def make_cuts(n_frames, frame_dur, rng, lo_s=2.0, hi_s=8.0):
    """Shots of uniform random length lo_s..hi_s; True at the first frame of each shot."""
    cut = np.zeros(n_frames, dtype=bool)
    i = 0
    while i < n_frames:
        cut[i] = True
        i += max(1, int(round(rng.uniform(lo_s, hi_s) / frame_dur)))
    return cut


def hysteresis_round(target, allowed, eta):
    """Integer track of a slowly moving target with hysteresis.

    Starts at round(target[0]); keeps its value until a position where a move is allowed and the nearest integer
    is closer to the target than the kept one by more than eta; then takes the nearest. eta = 0 with every move
    allowed is plain rounding (perf1's nearest refresh). Event-skipping search, so few moves cost little.
    """
    n = len(target)
    near = np.round(target)
    if n == 0 or (eta <= 0 and allowed.all()):
        return near.astype(np.int64)
    gap_near = np.abs(near - target)
    out = np.empty(n, dtype=np.int64)
    cur, pos = near[0], 0
    while True:
        start, nxt, chunk = pos + 1, n, 256
        while start < n:
            stop = min(n, start + chunk)
            hit = np.flatnonzero(allowed[start:stop] &
                                 (np.abs(cur - target[start:stop]) - gap_near[start:stop] > eta))
            if hit.size:
                nxt = start + hit[0]
                break
            start, chunk = stop, chunk * 2
        out[pos:nxt] = cur
        if nxt >= n:
            return out
        cur, pos = near[nxt], nxt


def follower_choice(x, j, p, q, allowed, eta, first=None):
    """Follower refresh for every master frame change.

    x: estimated continuous follower refresh index of each change (where its light at the aimed row should be);
    j: the master refresh of each change; p/q: master period / follower period. Changes whose master refreshes
    are a multiple of q apart are one alignment class: between them lies a whole number of follower refreshes,
    so the class keeps one integer offset (with hysteresis eta, in follower refreshes) from that anchor.
    `first`: optional forced offset choice for each class's first change (else nearest).
    """
    n_out = np.empty(len(x), dtype=np.int64)
    cls = j % q
    for c in np.unique(cls):
        idx = np.flatnonzero(cls == c)
        anchor = (j[idx] - j[idx[0]]) // q * p
        base = np.floor(x[idx[0]] + 0.5) if first is None else first
        k = hysteresis_round(x[idx] - (base + anchor), allowed[idx], eta)
        n_out[idx] = (base + anchor + k).astype(np.int64)
    return n_out
