"""Compares the C geometry (bin/geometry-test.exe) with the numpy reference at a grid of pixels, for scenarios that cover
fold, the curve, a toed-in panel, a depth step and a slide. The two codes are independent; they must agree to 1e-6 source pixels.
  python -I tests/test-c-geometry.py
"""
import json
import os
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import pp_geometry as geo  # noqa: E402
import pp_reference as ref  # noqa: E402

EXE = os.path.join(ROOT, "bin", "geometry-test.exe")
ENV = dict(os.environ, PATH=r"C:\Users\tamla\msys64\ucrt64\bin;" + os.environ.get("PATH", ""))
FAILED = []


def run_scenario(name):
    path = os.path.join("scenarios", name + ".json")
    proc = subprocess.run([EXE, path], cwd=ROOT, env=ENV, capture_output=True, text=True)
    if proc.returncode != 0:
        FAILED.append(name + ": exit %d %s" % (proc.returncode, proc.stderr.strip()))
        return
    setup = ref.Setup(ref.load_scenario(os.path.join(ROOT, path)))
    ids = [m.id for m in setup.room.monitors]
    worst_s, worst_j, n = 0.0, 0.0, 0
    for line in proc.stdout.splitlines():
        d = json.loads(line)
        if "px" not in d:
            if "upscales" in d:
                k = ids.index(d["monitor"])
                want = setup.decide_domain(k) == "sigmoid"
                if bool(d["upscales"]) != want:
                    FAILED.append("%s: %s upscales C=%s reference=%s" % (name, d["monitor"], d["upscales"], want))
            elif d.get("bad"):
                FAILED.append("vertical round trip failed in C")
            continue
        k = ids.index(d["monitor"])
        m = setup.room.monitors[k]
        t_top = float(setup.slide_t_top.get(m.id, 0.0))
        vu, vv = setup.slide_v

        def per_row(py, m=m, t_top=t_top, vu=vu, vv=vv):
            t = geo.row_time_ms(m, py, t_top)
            return (vu * (t - setup.slide_t_ref) / 1000.0, vv * (t - setup.slide_t_ref) / 1000.0)

        sx, sy, j = geo.source_jacobian(setup.room, k, setup.picture, np.array([d["px"]]), np.array([d["py"]]),
                                        setup.model, setup.curvature, per_row=per_row if (vu or vv) else None)
        worst_s = max(worst_s, abs(sx[0] - d["sx"]), abs(sy[0] - d["sy"]))
        cj = np.array(d["j"]).reshape(2, 2)
        worst_j = max(worst_j, float(np.max(np.abs(j[0] - cj))))
        n += 1
    ok = worst_s < 1e-6 and worst_j < 1e-6
    print(("ok   " if ok else "FAIL ") + "%s: %d points, max |dsrc| %.2e px, max |dJ| %.2e" % (name, n, worst_s, worst_j))
    if not ok:
        FAILED.append(name)


if __name__ == "__main__":
    if not os.path.exists(EXE):
        print("bin/geometry-test.exe missing: run compile.ps1 -Target geometry-test")
        sys.exit(2)
    for scn in ("fold-t1", "curve-t1", "curveflat-t1", "curve-toe30-t1", "curve-laptop-nearer-t1", "shear-fold", "identity-laptop"):
        run_scenario(scn)
    print("%d failed" % len(FAILED))
    for f in FAILED:
        print("  " + f)
    sys.exit(1 if FAILED else 0)
