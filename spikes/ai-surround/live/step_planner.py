"""Step planner (role: analyse): per new drawing and side, what the side needs, read from the picture only (never the
hidden truth): HOLD, STILL, SLIDE, GAIN (structure carries it), PATCH or GEN (a generator must redraw part of it), the
rows or columns where the change is (the zone, for phase 5) and the side's brightness. Phase 4 of
plans/261013-0718-ai-surround-live-path.

The picture's band next to each seam (BAND of its width for left/right, BAND of its height for top/bottom) stands in for
the side, which has no pixels. Grey at PLAN_W px wide (the research's 480x270: its thresholds were measured at that
size; the camera's flow runs coarser). A pixel is unexplained when it differs
by more than DIFF codes under every hypothesis (research report 261013-1049 render-not-generate, section 4; X1's harness):
  step tests   against the previous frame: held still, slid by the camera step, slid + one brightness gain;
               U_step = the smallest unexplained share
  drift test   against the side's reference (the frame of its last refresh, or the shot's first): carried there by the
               camera + one gain; U_drift. A per-step test misses change that builds up slowly (X1: the pan got no
               refresh in 2.9 s, the reaching arm 1.4-2.8 a second); this one does not.
  near test    the NEAR share of the picture next to the seam, slid + gain: a heavy change right at the seam
  travel       how far the seam's midpoint has moved through the world since the side's reference, over the picture's
               width: no 2D camera map follows a pan through a 3D set for long (X1's pan: parallax; camera_model), so
               the side is redrawn after tau_travel of travel
States (new drawings; a repeated drawing is HOLD):
  GEN    U_step > tau_gen, or U_drift > tau_drift, or U_near > near_heavy, or travel > tau_travel
  PATCH  U_step > tau_patch (the research rule's "structure plus local patch", a generator call too)
  GAIN / SLIDE / STILL  by the hypothesis that explained the step (GAIN when its gain differs from 1 by > GAIN_MIN)
PATCH and GEN refresh the side: its reference becomes this frame. When the camera has moved so far that the reference
covers under MIN_COVER of the band, the reference moves on without a refresh (what came in is the plate's: there is no
drift to measure). The thresholds are the cadence: X1 refreshed only at U_step > 0.10; X1c tests this planner.

Display gains are not the planner's: analyse_v0 keeps v0's (the picture's luma over its median, soft knee), which phase
2 checked on the screens. A product of per-step lighting factors drifted (review of 2026-10-14: a shot stuck near black
after a darkening step was explained and the brightening one was not); per-side lighting is an open item.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

STATES = {"unknown": 0, "hold": 1, "still": 2, "slide": 3, "gain": 4, "patch": 5, "gen": 6}   # side-store step_state
SIDES = ("left", "top", "right", "bottom")
PLAN_W = 480                                                      # the planner's grey width (the research's 480x270)
DIFF = 16
BAND = 0.25
NEAR = 0.10
GAIN_MIN = 0.02
MIN_COVER = 0.5
GAIN_KNEE = (np.log(1.05), np.log(1.16))
GAIN_MAX = 2.5


@dataclass
class Cadence:
    tau_patch: float = 0.02
    tau_gen: float = 0.10
    tau_drift: float = 0.10
    near_heavy: float = 0.30
    tau_travel: float = 0.05

    def key(self) -> str:
        return (f"patch{self.tau_patch:g}|gen{self.tau_gen:g}|drift{self.tau_drift:g}|near{self.near_heavy:g}"
                f"|travel{self.tau_travel:g}")


@dataclass
class SideStep:
    state: int
    u_step: float = 0.0
    u_drift: float = 0.0
    u_near: float = 0.0
    gain: float = 1.0               # the band's brightness over its reference (carried): since the side's last refresh
    zone: tuple = ()                # (a0, a1): span along the seam (rows for left/right, columns for top/bottom), band px


def band_slices(side: str, w: int, h: int, share: float):
    bw, bh = max(1, int(round(share * w))), max(1, int(round(share * h)))
    return {"left": (slice(None), slice(0, bw)), "right": (slice(None), slice(w - bw, w)),
            "top": (slice(0, bh), slice(None)), "bottom": (slice(h - bh, h), slice(None))}[side]


def _unexplained(cur: np.ndarray, pred: np.ndarray, valid: np.ndarray, gain_fit: bool):
    """(unexplained share, gain, mask) of cur against a prediction over its valid pixels (gain: one factor)."""
    n = int(valid.sum())
    if n < 16:
        return 1.0, 1.0, np.ones(valid.shape, bool)
    a, b = cur[valid], pred[valid]
    g = float(a.mean() / max(1.0, b.mean())) if gain_fit else 1.0
    bad = np.zeros(valid.shape, bool)
    bad[valid] = np.abs(a - g * b) > DIFF
    return float(bad[valid].mean()), g, bad


def soft_knee(ratio: np.ndarray) -> np.ndarray:
    """analyse_v0's gain rule: 1 within +-5 %, the full ratio beyond +-16 %, smoothstep between; [0, GAIN_MAX]."""
    lr = np.log(np.clip(ratio, 1e-3, None))
    x = np.clip((np.abs(lr) - GAIN_KNEE[0]) / (GAIN_KNEE[1] - GAIN_KNEE[0]), 0.0, 1.0)
    return np.clip(np.exp(lr * x * x * (3.0 - 2.0 * x)), 0.0, GAIN_MAX).astype(np.float32)


@dataclass
class _Ref:
    g: np.ndarray                   # grey picture (flow size) at the reference frame
    W: np.ndarray                   # its frame -> world map (flow px)


class StepPlanner:
    """One shot, frame by frame: add(grey picture at the flow size, W frame -> world in flow px, new drawing?)."""

    def __init__(self, w: int, h: int, sides=("left", "right"), cadence: Cadence = Cadence()):
        self.w, self.h, self.sides, self.c = w, h, tuple(sides), cadence
        self.prev = None
        self.ref: dict = {}
        self.steps: list = []                                     # per frame: {side: SideStep}
        self.bands = {s: band_slices(s, w, h, BAND) for s in SIDES}
        self.nears = {s: band_slices(s, w, h, NEAR) for s in SIDES}
        self.seam_mid = {"left": (0.0, h / 2), "right": (w - 1.0, h / 2), "top": (w / 2, 0.0), "bottom": (w / 2, h - 1.0)}

    def _warp(self, g: np.ndarray, M: np.ndarray):
        img = cv2.warpPerspective(g, M, (self.w, self.h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
        ok = cv2.warpPerspective(np.ones(g.shape, np.uint8), M, (self.w, self.h), flags=cv2.INTER_NEAREST,
                                 borderMode=cv2.BORDER_CONSTANT) > 0
        return img, ok

    def add(self, g_u8: np.ndarray, W: np.ndarray, new_drawing: bool) -> dict:
        g = g_u8.astype(np.float32)
        if self.prev is None:
            for s in self.sides:
                self.ref[s] = _Ref(g, W)
            return self._push({s: SideStep(STATES["still"]) for s in self.sides}, g, W)
        if not new_drawing:
            last = self.steps[-1]
            return self._push({s: SideStep(STATES["hold"], u_drift=last[s].u_drift, gain=last[s].gain)
                               for s in self.sides}, g, W)
        gp, Wp = self.prev
        Winv = np.linalg.inv(W)
        S = Winv @ Wp                                             # previous frame px -> this frame px
        moved = np.abs(S - np.eye(3)).max() > 1e-6
        slid, slid_ok = self._warp(gp, S) if moved else (gp, np.ones(g.shape, bool))
        out = {}
        for s in self.sides:
            b = self.bands[s]
            cur = g[b]
            cands = [_unexplained(cur, gp[b], np.ones(cur.shape, bool), False) + ("still",)]
            if moved:
                cands.append(_unexplained(cur, slid[b], slid_ok[b], False) + ("slide",))
            u_g, gg, m_g = _unexplained(cur, slid[b], slid_ok[b], True)
            cands.append((u_g, gg, m_g, "gain" if abs(gg - 1.0) > GAIN_MIN else ("slide" if moved else "still")))
            u_step, _, mask, hyp = min(cands, key=lambda c: c[0])
            r = self.ref[s]
            carried, c_ok = self._warp(r.g, Winv @ r.W)
            if float(c_ok[b].mean()) < MIN_COVER:                  # the camera left the reference behind
                self.ref[s] = r = _Ref(g, W)
                u_drift, g_drift, m_drift = 0.0, 1.0, np.zeros(cur.shape, bool)
            else:
                u_drift, g_drift, m_drift = _unexplained(cur, carried[b], c_ok[b], True)
            n = self.nears[s]
            u_near, _, _ = _unexplained(g[n], slid[n], slid_ok[n], True)
            p = np.float64([[*self.seam_mid[s], 1.0]]).T
            q = np.linalg.inv(r.W) @ W @ p                         # the seam's midpoint in the reference frame
            travel = float(np.hypot(*(q[:2, 0] / q[2, 0] - p[:2, 0]))) / self.w
            zmask = None
            if (u_step > self.c.tau_gen or u_drift > self.c.tau_drift or u_near > self.c.near_heavy
                    or travel > self.c.tau_travel):
                state, zmask = STATES["gen"], mask | m_drift
            elif u_step > self.c.tau_patch:
                state, zmask = STATES["patch"], mask
            else:
                state = STATES[hyp]
            zone = ()
            if zmask is not None:
                along = np.flatnonzero(zmask.any(axis=1) if s in ("left", "right") else zmask.any(axis=0))
                if len(along):
                    zone = (int(along[0]), int(along[-1]) + 1)
                self.ref[s] = _Ref(g, W)                          # refreshed: the side is redrawn from this frame
                g_drift = 1.0
            out[s] = SideStep(state, u_step, u_drift, u_near, float(g_drift), zone)
        return self._push(out, g, W)

    def _push(self, step: dict, g, W) -> dict:
        self.steps.append(step)
        self.prev = (g, W)
        return step

    def states(self) -> np.ndarray:
        """(n, 4) uint8 step states, left/top/right/bottom (sides not planned: unknown)."""
        out = np.zeros((len(self.steps), 4), np.uint8)
        for t, st in enumerate(self.steps):
            for s, v in st.items():
                out[t, SIDES.index(s)] = v.state
        return out
