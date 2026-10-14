"""Live worker analysis self-test: a synthetic camera pan with known offsets through the analyser's shot tracker and the T1
mosaic (live/analyse_v0.py, live/camera_model.py, live/step_planner.py, live/structure.py, live/mosaic_t1.py). No film,
no GPU, a few seconds.

The world is a large textured image; frame i is the crop of it at a known camera position (content moves by +d_i, as the
contract says: world px = source px - d_i). Checks:
  camera     the tracker's path stays within 2 % of the travel of the truth over a 675 px pan on twos, and held frames add
             none
  mosaic     every covered texel holds the world's colour at its world position (median error small): the frame transform,
             the canvas origin and the texel scale agree with the contract's conventions
  extent     the canvas covers the crop widened by the margins at every frame's position
  transform  ShotAnalysis.transforms() (the store's projective h) maps a source px of frame i to the world px the truth gives
  plan       held drawings are HOLD, the camera's steps SLIDE (the bands are explained by the slide), gains about 1
  strips     the pan leaves exit strips on the left and bottom (where content leaves the picture); replayed, their texels
             hold the world's colour at their lattice position
  reliable   the exact pan keeps its sides; with every other kept frame misplaced by 24 px the regions they disagree in go
  fade       black kept frames do not vote: the lit ones make the mosaic and the gain reference
    python -I -X utf8 live-analysis-selftest.py
Exit code 0 = all checks passed.
"""
import copy
import os
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from live import analyse_v0, mosaic_t1, step_planner, structure  # noqa: E402

checks, failed = 0, 0


def check(cond, what):
    global checks, failed
    checks += 1
    if not cond:
        failed += 1
        print(f"FAIL {what}", flush=True)


def world_image(w, h, seed=3):
    """A textured world: blurred noise and blocks of colour (no regular lines: a chained tracker drifts on it)."""
    rng = np.random.default_rng(seed)
    img = cv2.GaussianBlur(rng.integers(0, 255, (h, w, 3), np.uint8), (0, 0), 2.0)
    for _ in range(600):
        x, y = int(rng.integers(0, w)), int(rng.integers(0, h))
        cv2.circle(img, (x, y), int(rng.integers(4, 30)), [int(c) for c in rng.integers(0, 255, 3)], -1)
    return img


def main():
    src_w, src_h = 1920, 1080                                     # source frame size
    ds = 2.0                                                      # decode scale: frames come at 960x540 like the worker's
    n = 96
    # camera path: a pan to the right with a small drift down, held drawings (anime on twos), source px
    truth = []
    d = np.zeros(2)
    for i in range(n):
        if i and i % 2 == 0:                                      # a new drawing every second frame: the camera steps
            d = d + np.array([-14.37, 3.11])                      # content moves left and down: the camera pans right, up
        truth.append(d.copy())
    truth = np.array(truth)
    pad = 800
    world = world_image(int((src_w + pad * 2) / ds), int((src_h + pad * 2) / ds))
    crop = (0.0, 0.0, float(src_w), float(src_h))
    tracker = analyse_v0.ShotTracker(0, 0, crop, ds, True)
    for i in range(n):
        # frame i shows world px w at source px p = w + d_i; the world image's px (u, v) is world px (u ds - pad, v ds - pad)
        dx, dy = truth[i]
        m = np.float32([[1, 0, (pad - dx) / ds], [0, 1, (pad - dy) / ds]])     # frame px -> world image px (decoded size)
        frame = cv2.warpAffine(world, m, (int(src_w / ds), int(src_h / ds)), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP)
        tracker.add(frame)
    an = tracker.finish()
    err = np.abs(an.offsets - truth).max()
    print(f"camera: largest offset error {err:.2f} source px over {n} frames (pan {truth[-1]})", flush=True)
    travel = float(np.abs(truth[-1]).max())
    check(err <= 0.02 * travel, f"camera path off by {err:.2f} px over {travel:.0f} px of travel")
    held = [i for i in range(1, n) if i % 2 == 1]
    check(np.abs(np.diff(an.offsets, axis=0)[[h - 1 for h in held]]).max() < 0.05, "a held drawing moved the camera")
    check(an.kept and an.kept[0][0] == 0 and an.kept[-1][0] == n - 1, f"kept frames {[k for k, _ in an.kept]}")

    margins = (300.0, 40.0, 500.0, 60.0)
    mo = mosaic_t1.build(an, margins, scale=2.0)
    x0, y0, x1, y1 = crop
    maps = an.maps()
    for i in (0, n // 2, n - 1):                                  # extent: the widened crop of every frame fits the canvas
        c = np.float64([[x0 - margins[0], y0 - margins[1], 1], [x1 + margins[2], y1 + margins[3], 1]]) @ maps[i].T
        (wx0, wy0), (wx1, wy1) = c[0, :2] / c[0, 2], c[1, :2] / c[1, 2]
        check(mo.world_x0 <= wx0 + 1e-6 and mo.world_y0 <= wy0 + 1e-6 and
              mo.world_x0 + mo.tex_w * mo.scale >= wx1 - 1e-6 and mo.world_y0 + mo.tex_h * mo.scale >= wy1 - 1e-6,
              f"canvas misses frame {i}'s side area")
    # mosaic content: texel (i, j) covers world px x0 + (i + 0.5) scale at its centre -> world image px
    jj, ii = np.nonzero(mo.coverage)
    wx = mo.world_x0 + (ii + 0.5) * mo.scale
    wy = mo.world_y0 + (jj + 0.5) * mo.scale
    u = ((wx + pad) / ds - 0.5).astype(np.float32)
    v = ((wy + pad) / ds - 0.5).astype(np.float32)
    ref = np.concatenate([cv2.remap(world, u[k:k + 30000].reshape(1, -1), v[k:k + 30000].reshape(1, -1), cv2.INTER_LINEAR)[0]
                          for k in range(0, len(u), 30000)]).astype(np.float32)   # remap maps stay under 32767 columns
    got = mo.rgb[jj, ii].astype(np.float32)
    e = np.abs(got - ref).mean(axis=1)
    print(f"mosaic: {len(e)} covered texels, median error {np.median(e):.2f}, p90 {np.percentile(e, 90):.2f} codes; "
          f"side share {mo.side_share:.2f}", flush=True)
    check(np.median(e) < 6.0, f"mosaic median error {np.median(e):.2f} codes: the world placement is off")
    check(mo.side_share > 0.05, "the pan revealed no side area")
    # the frame transform of the contract (projective: d = h6 x + h7 y + 1)
    m = an.transforms()
    p = np.array([100.0, 200.0])
    for i in (0, n - 1):
        d = m[i, 6] * p[0] + m[i, 7] * p[1] + 1.0
        w = np.array([m[i, 0] * p[0] + m[i, 1] * p[1] + m[i, 2], m[i, 3] * p[0] + m[i, 4] * p[1] + m[i, 5]]) / d
        check(np.abs(w - (p - truth[i])).max() <= 0.02 * travel, f"transform of frame {i}: {w} != {p - truth[i]}")
    # the plan: held drawings HOLD, the camera's steps explained by the slide
    st = an.states
    S = step_planner.STATES
    check(all(st[h, 0] == S["hold"] for h in held), f"held drawings not HOLD: {st[held, 0]}")
    moved = [i for i in range(2, n, 2)]
    refreshed = sum(st[i, 0] not in (S["slide"], S["still"], S["gain"]) for i in moved)
    by_travel = travel / (step_planner.Cadence().tau_travel * src_w) # the travel trigger's refreshes on a flat pan
    g = an.display_gains()
    print(f"plan: left side states over the camera's steps {np.bincount(st[moved, 0], minlength=7).tolist()} "
          f"({refreshed} refreshed, {by_travel:.1f} expected from travel), gains {g.min():.3f}-{g.max():.3f}",
          flush=True)
    check(refreshed <= by_travel + 2, f"{refreshed} of the camera's steps refreshed on a flat pan (travel allows "
                                      f"{by_travel:.1f})")
    check(np.abs(g - 1.0).max() < 0.02, f"gains moved on a steady pan: {g.min():.3f}-{g.max():.3f}")
    # the exit strips: where content leaves the picture (left, bottom), real pixels of the world at their lattice place
    sides = {p.side for p in an.patches}
    print(f"strips: {len(an.patches)} patches on sides {sorted(sides)}", flush=True)
    check(len(an.patches) >= n // 2 - 2 and sides <= {0, 3} and 0 in sides, f"exit strips {len(an.patches)} on {sides}")
    page = structure.pack(an.patches, 16 << 20)
    acc = structure.replay(page, n)
    jj, ii = np.nonzero(acc[..., 3] > 0)
    wx = (page.grid_i0 + ii + 0.5) * structure.SCALE              # lattice texel centre -> world px
    wy = (page.grid_j0 + jj + 0.5) * structure.SCALE
    u = ((wx + pad) / ds - 0.5).astype(np.float32)
    v = ((wy + pad) / ds - 0.5).astype(np.float32)
    ref = np.concatenate([cv2.remap(world, u[k:k + 30000].reshape(1, -1), v[k:k + 30000].reshape(1, -1), cv2.INTER_LINEAR)[0]
                          for k in range(0, len(u), 30000)]).astype(np.float32)
    e = np.abs(acc[jj, ii, :3].astype(np.float32) - ref).mean(axis=1)
    print(f"strips: {len(e)} texels, median error {np.median(e):.2f}, p90 {np.percentile(e, 90):.2f} codes", flush=True)
    check(len(e) > 1000 and np.median(e) < 6.0, f"exit strip texels off the world: median {np.median(e):.2f}")
    # reliability: the exact pan's frames agree, so its whole mosaic stays; with every other kept frame placed 24 px off (a
    # move the camera maps cannot follow), the regions both halves saw are left out
    check(mo.disagree < 0.02 and not mo.unsure.any(), f"the exact pan disagrees at {mo.disagree:.1%}")
    off = copy.copy(an)
    off.H = an.maps().copy()
    for j, (rel, _) in enumerate(an.kept):
        if j % 2:
            off.H[rel, 0, 2] += 24.0
    mb = mosaic_t1.build(off, margins, scale=2.0)
    print(f"reliability: exact pan disagrees at {mo.disagree:.1%}; every other frame 24 px off: {mb.disagree:.0%}, side "
          f"share {mo.side_share:.2f} -> {mb.side_share:.2f}", flush=True)
    check(mb.unsure.any() and mb.side_share < 0.9 * mo.side_share,                   # (this world's blurred noise and flat
                                                                                      # discs agree a lot when shifted)
          f"a 24 px misregistration kept the sides: disagree {mb.disagree:.0%}, side share {mb.side_share:.2f}")
    # a fade: the first half of the kept frames black; they make no mosaic and set no gain reference
    fade = copy.copy(an)
    fade.luma = an.luma.copy()
    ko = an.kept_offsets
    fade.luma[: ko[len(ko) // 2]] = 0.001
    lit = fade.voting_offsets()
    g = fade.display_gains()
    print(f"fade: {len(lit)} of {len(ko)} kept frames vote; gain {g[0, 0]:.3f} in the black, {g[-1, 0]:.3f} lit", flush=True)
    check(min(lit) >= ko[len(ko) // 2] and abs(g[-1, 0] - 1.0) < 0.05 and g[0, 0] < 0.1, f"fade: votes {lit}, gains "
          f"{g[0, 0]:.3f} / {g[-1, 0]:.3f}")
    print(f"{'PASS' if not failed else 'FAIL'}: {checks - failed}/{checks} checks", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
