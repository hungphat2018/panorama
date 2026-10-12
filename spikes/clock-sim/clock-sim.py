"""Offline simulation of the film-clock rules of ADR 0021 (M1 phase 01, step 7b). No window, no display change.

    python spikes/clock-sim/clock-sim.py --all            # every scenario, 2 simulated hours x 8 seeds
    python spikes/clock-sim/clock-sim.py --scenario 1 3   # some scenarios
    python spikes/clock-sim/clock-sim.py --all --quick    # 15 minutes x 2 seeds, to check the code runs

Writes results/scenario-N.json, results/summary.txt and results/self-check.json; exits 1 if the rolling blend's
self-check fails (light-weighted moment off the reference row's by 0.01 us or more with exact timing).
Deterministic: every random draw comes from a fixed seed.
"""
import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np

HERE = Path(__file__).resolve().parent
EXACT_LIMIT_US = 0.01
sys.dont_write_bytecode = True          # keep the spike folder free of __pycache__


def load(name):
    """Sibling modules have kebab-case names, so they are loaded by path (as analyze-perf1.py does)."""
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def to_json(x):
    if isinstance(x, dict):
        return {str(k): to_json(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [to_json(v) for v in x]
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.floating, float)):
        return None if not np.isfinite(x) else float(f"{x:.6g}")
    if isinstance(x, np.bool_):
        return bool(x)
    return x


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--scenario", type=int, nargs="*", default=[])
    ap.add_argument("--hours", type=float, default=2.0)
    ap.add_argument("--seeds", type=int, default=8)
    ap.add_argument("--quick", action="store_true", help="15 simulated minutes, 2 seeds")
    ap.add_argument("--out", type=Path, default=HERE / "results")
    ap.add_argument("--ffprobe", default=None, help="ffprobe.exe for the real timestamp traces (else cached data/)")
    ap.add_argument("--reprobe", action="store_true", help="read the content files again instead of data/")
    a = ap.parse_args()
    todo = list(range(1, 7)) if a.all or not a.scenario else sorted(set(a.scenario))
    if a.quick:
        a.hours, a.seeds = 0.25, 2

    lib = SimpleNamespace(timing=load("display-timing"), fit=load("refresh-grid-fit"), rules=load("film-clock-rules"),
                          blend=load("rolling-blend"), tl=load("seam-timeline"), metrics=load("seam-metrics"))
    sa, vfr, brk, sb, tables = (load(n) for n in ("scenarios-setup-a", "scenario-vfr", "scenario-breaks",
                                                  "scenario-setup-b", "summary-tables"))
    cfg = SimpleNamespace(seconds=a.hours * 3600.0, seeds=list(range(1, a.seeds + 1)), here=HERE,
                          pools=lib.fit.load_wake_pools(HERE.parent), common=load("scenario-common"),
                          ffprobe=a.ffprobe, reprobe=a.reprobe)
    a.out.mkdir(parents=True, exist_ok=True)
    meta = {"hours_per_seed": a.hours, "seeds": a.seeds, "wake_pool_sizes": {k: len(v) for k, v in cfg.pools.items()}}
    results = {}
    t_all = time.time()
    for n in todo:
        t0 = time.time()
        if n == 1:
            results[1] = sa.scenario_1(lib, cfg)
        elif n == 2:
            results[2] = sa.scenario_2(lib, cfg)
        elif n == 3:
            r1 = results.get(1) or sa.scenario_1(lib, cfg)
            r2 = results.get(2) or sa.scenario_2(lib, cfg)
            results[3] = sa.scenario_3(lib, cfg, r1, r2)
        elif n == 4:
            results[4] = vfr.scenario_4(lib, cfg)
        elif n == 5:
            results[5] = brk.scenario_5(lib, cfg)
        elif n == 6:
            results[6] = sb.scenario_6(lib, cfg, sa.ETA_REC_S)
        results[n] = dict(results[n], meta=dict(meta, seconds_to_run=round(time.time() - t0, 1)))
        (a.out / f"scenario-{n}.json").write_text(json.dumps(to_json(results[n]), indent=1))
        print(f"scenario {n}: {time.time() - t0:.1f} s", flush=True)

    unit = lib.blend.exactness_check()
    timeline = results.get(3, {}).get("timeline_exact_max_us")
    ok = unit < EXACT_LIMIT_US and (timeline is None or timeline < EXACT_LIMIT_US)
    check = {"limit_us": EXACT_LIMIT_US, "unit_check_max_us": unit, "timeline_exact_max_us": timeline,
             "pass": bool(ok)}
    (a.out / "self-check.json").write_text(json.dumps(to_json(check), indent=1))
    (a.out / "summary.txt").write_text(tables.summary(results, check, meta))
    print(f"self-check: rolling blend with exact timing, max error {unit:.2e} us (unit), "
          f"{timeline if timeline is None else f'{timeline:.2e}'} us (timeline): {'PASS' if ok else 'FAIL'}")
    print(f"total {time.time() - t_all:.1f} s; results in {a.out}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
