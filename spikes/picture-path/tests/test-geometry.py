"""Checks of the reference geometry (pp_geometry.py) against closed forms: the vertical-convention round trip, the
coplanar identity of fold and the flat-limit curve, the cylinder formulas, seam continuity, the toe-in hinge.
  python -I tests/test-geometry.py
"""
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import pp_geometry as geo  # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + (("  " + detail) if detail else ""))
    if not cond:
        FAILED.append(name)


def room(name):
    return geo.Room(os.path.join(ROOT, "room", name + ".json"))


def test_vertical_round_trip():
    rng = np.random.default_rng(3)
    rows = rng.uniform(0, 1600, 1000)
    y = geo.row_to_local_y(rows, 1600, 215.0)
    back = geo.local_y_to_row(y, 1600, 215.0)
    check("pixel row -> local up -> pixel row round trip", np.max(np.abs(back - rows)) < 1e-9, "max %.2e" % np.max(np.abs(back - rows)))
    check("row 0 edge is the top (+h/2), row H edge the bottom", abs(geo.row_to_local_y(0, 1600, 215.0) - 107.5) < 1e-12
          and abs(geo.row_to_local_y(1600, 1600, 215.0) + 107.5) < 1e-12)
    pic = geo.Picture(1920, 1080, 1.0, None, (0.0, 0.0), 215.0)
    u, v = np.array([0.0]), np.array([107.5])
    _, sy = pic.uv_to_src(u, v)
    check("picture top (v up) is source row 0", abs(sy[0]) < 1e-12)
    _, sy = pic.uv_to_src(u, np.array([-107.5]))
    check("picture bottom is the last source row", abs(sy[0] - 1080) < 1e-9)


def test_coplanar_identity():
    r = room("setup-a-default")
    worst = 0.0
    for k, m in enumerate(r.monitors):
        px, py = geo.monitor_grid(m)
        u0, v0 = geo.monitor_uv(r, k, px, py, "fold", 0.0)
        u1, v1 = geo.monitor_uv(r, k, px, py, "curve", 0.0)
        worst = max(worst, float(np.max(np.abs(u0 - u1))), float(np.max(np.abs(v0 - v1))))
    check("coplanar: fold == curve with curvature 0 (flat plane at the seat distance)", worst < 1e-9, "max %.2e mm" % worst)
    # a finite cylinder around the eye is NOT the same picture: its u = D * azimuth
    m = r.monitors[0]
    px, py = np.array([0.5]), np.array([800.0])
    u0, _ = geo.monitor_uv(r, 0, px, py, "fold", 0.0)
    u1, _ = geo.monitor_uv(r, 0, px, py, "curve", 1.0 / 700.0)
    check("coplanar: the cylinder around the eye differs from fold at the far edge (expected, reported)",
          abs(u0[0] - u1[0]) > 20.0, "fold u %.1f, cylinder u %.1f mm" % (u0[0], u1[0]))


def test_cylinder_closed_form():
    r = room("setup-a-default")
    d = 700.0
    px, py = geo.monitor_grid(r.monitors[1])
    p, _, _ = r.monitors[1].point(px, py)
    u, v = geo.curve_uv(p, d, 1.0 / d)
    az = np.arctan2(p[..., 0], p[..., 2])
    rho = np.hypot(p[..., 0], p[..., 2])
    check("cylinder around the eye: u = D * azimuth", np.max(np.abs(u - d * az)) < 1e-9)
    check("cylinder around the eye: v = D * y / rho (D * tan elevation)", np.max(np.abs(v - d * p[..., 1] / rho)) < 1e-9)
    # general radius: the hit point is on the cylinder
    for kappa in (1.0 / 700.0, 1.0 / 1000.0, 1.0 / 1400.0):
        big_r = 1.0 / kappa
        za = d - big_r
        uu, vv = geo.curve_uv(p, d, kappa)
        hx = big_r * np.sin(uu / big_r)
        hz = za + big_r * np.cos(uu / big_r)
        dirs = p / np.linalg.norm(p, axis=-1, keepdims=True)
        # the hit point must lie on the ray: (hx, vv, hz) parallel to dirs
        cross = np.abs(hx * dirs[..., 1] - vv * dirs[..., 0]) + np.abs(hz * dirs[..., 1] - vv * dirs[..., 2])
        check("cylinder radius %.0f: the hit lies on the ray" % big_r, np.max(cross) < 1e-9, "max %.2e" % np.max(cross))
    u, v = geo.curve_uv(np.array([[0.0, 0.0, 700.0]]), d, 1.0 / 1000.0)
    check("straight ahead is the surface origin", abs(u[0]) < 1e-12 and abs(v[0]) < 1e-12)


def test_seam_and_hinge():
    r = room("setup-a-default")
    lap, ext = r.monitors
    ua, _ = geo.monitor_uv(r, 0, np.array([lap.W]), np.array([100.0]), "fold", 0.0)
    ub, _ = geo.monitor_uv(r, 1, np.array([0.0]), np.array([100.0]), "fold", 0.0)
    check("fold: u jumps by the gap across the seam", abs((ub - ua)[0] - 20.0) < 1e-9, "%.9f mm" % (ub - ua)[0])
    check("fold: u = 0 at the seam centre", abs(ua[0] + 10.0) < 1e-9 and abs(ub[0] - 10.0) < 1e-9)
    for name, toe in (("setup-a-toe15", 15.0), ("setup-a-toe30", 30.0)):
        rr = room(name)
        e = rr.monitors[1]
        left_mid, _, _ = e.point(np.array([0.0]), np.array([e.H / 2.0]))
        check(name + ": the external's left edge sits on the hinge (10, ., 700)", np.allclose(left_mid[0][[0, 2]], [10.0, 700.0], atol=1e-9),
              str(left_mid[0]))
        check(name + ": toe-in turns the right edge away", e.ex[2] > 0 and abs(math.degrees(math.asin(e.ex[2])) - toe) < 1e-9)
        ua, _ = geo.monitor_uv(rr, 0, np.array([rr.monitors[0].W]), np.array([100.0]), "fold", 0.0)
        ub, _ = geo.monitor_uv(rr, 1, np.array([0.0]), np.array([100.0]), "fold", 0.0)
        check(name + ": fold gap unchanged by toe-in", abs((ub - ua)[0] - 20.0) < 1e-9)
        # the curve keeps the picture continuous across the seam: neighbouring rays give neighbouring u
        uc0, _ = geo.monitor_uv(rr, 0, np.array([rr.monitors[0].W]), np.array([100.0]), "curve", 1.0 / 700.0)
        uc1, _ = geo.monitor_uv(rr, 1, np.array([0.0]), np.array([100.0]), "curve", 1.0 / 700.0)
        check(name + ": curve u increases across the seam", (uc1 - uc0)[0] > 0, "%.3f mm" % (uc1 - uc0)[0])
    rn = room("setup-a-laptop-nearer")
    ua, _ = geo.monitor_uv(rn, 0, np.array([rn.monitors[0].W]), np.array([100.0]), "fold", 0.0)
    ub, _ = geo.monitor_uv(rn, 1, np.array([0.0]), np.array([100.0]), "fold", 0.0)
    check("laptop-nearer: fold prints the same gap (it cannot see depth)", abs((ub - ua)[0] - 20.0) < 1e-9)


def test_monotonic_and_scale():
    r = room("setup-a-toe30")
    for model, kap in (("fold", 0.0), ("curve", 1.0 / 700.0)):
        us = []
        for k, m in enumerate(r.monitors):
            px, py = geo.monitor_grid(m)
            u, _ = geo.monitor_uv(r, k, px[0], np.full(m.W, 800.0), model, kap)
            us.append(u)
        u_all = np.concatenate(us)
        check(model + " (toe 30): u strictly increases left to right", np.all(np.diff(u_all) > 0))


if __name__ == "__main__":
    test_vertical_round_trip()
    test_coplanar_identity()
    test_cylinder_closed_form()
    test_seam_and_hinge()
    test_monotonic_and_scale()
    print("%d failed" % len(FAILED))
    sys.exit(1 if FAILED else 0)
