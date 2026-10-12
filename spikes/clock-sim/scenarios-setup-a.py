"""Scenarios 1-3 on Setup A: laptop 240 Hz + external (96, 72 or 100 Hz), 2 simulated hours per seed.

1   23.976 fps on 240 + 96 Hz (speed lock +0.1 %): aimed start on/off, hysteresis, moves anytime / at cuts /
    never, re-aim at cuts; perf1 as built; the rolling blend under each time model; drift sensitivity; forced
    boundary crossings (flip-flops per crossing against the hysteresis).
2   the same rules on 240 + 72 Hz; 25 fps on 240 + 100 Hz (master 100 Hz, hold 4; the laptop needs 9.6
    refreshes per frame) with every rung of ADR 0021's ladder.
3   the rolling blend's exactness and cost across those cases.
"""
ETA_REC_S = 0.05e-3                                   # hysteresis used for the recommended policy
CROSS_ETAS_MS = (0.0, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.3)


def _seams(lib, rows):
    return lambda kind: {} if kind == "none" else {0: lib.tl.Seam(0, 1, rows, kind)}


def rule_variants(lib, C, eta=ETA_REC_S, full=True):
    """Crisp follower policies (perf1 as built, the ADR's parts one by one) and blends under each time model."""
    P, V = lib.tl.Policy, C.Variant
    rec = P(eta_s=eta, moves="cuts", reaim="cuts")
    vs = [V("perf1-as-built", "wake", "crisp", P(aim_rows=False, pick="pl_queue")),
          V("perf1-with-row-timing", "scan", "crisp", P(pick="pl_queue")),    # perf1 after ADR 0025 (19:15)
          V("nearest-no-aim", "wake-corrected", "crisp", P(aimed_start=False)),
          V("nearest-aimed", "wake-corrected", "crisp", P()),
          V("recommended", "wake-corrected", "crisp", rec),
          V("recommended/scan-fit", "scan", "crisp", rec),
          V("blend/wake-corrected", "wake-corrected", "blend", P()),
          V("blend/scan-fit", "scan", "blend", P())]
    if full:
        vs += [V("frozen-aimed", "wake-corrected", "crisp", P(moves="never")),
               V("hyst-anytime", "wake-corrected", "crisp", P(eta_s=eta)),
               V("hyst-cuts", "wake-corrected", "crisp", P(eta_s=eta, moves="cuts")),
               V("recommended/eta-0.3ms", "wake-corrected", "crisp", P(eta_s=0.3e-3, moves="cuts", reaim="cuts")),
               V("nearest-aimed/log-jitter", "wake-corrected", "crisp", P(), "log"),
               V("recommended/log-jitter", "wake-corrected", "crisp", rec, "log"),
               V("blend/perf1-wake-times", "wake", "blend", P()),
               V("blend/exact", "exact", "blend", P()),
               V("blend/wake-corrected/log-jitter", "wake-corrected", "blend", P(), "log")]
    return vs


def scenario_1(lib, cfg):
    C, T = cfg.common, lib.timing
    lap, ext = T.setup_a(T.EXT_96)
    content = C.cfr_content(lib, 24000, 1001, cfg.seconds, True, 1, "23.976 fps, ms timestamps")
    out = {"setup": "laptop 240 Hz + external 96 Hz (custom), 23.976 fps", "drift_ppm": T.MEASURED_DRIFT_PPM,
           "variants": C.run_variants(lib, [lap, ext], 1, _seams(lib, C.SETUP_A_ROWS), content,
                                      rule_variants(lib, C), cfg.seeds, cfg.seconds, cfg.pools)}
    drift = {}
    wanted = ("perf1-with-row-timing", "nearest-no-aim", "nearest-aimed", "recommended")
    for ppm in (2.0, 5.0):
        lap_d, ext_d = T.setup_a(T.EXT_96, drift_ppm=ppm)
        drift[f"{ppm:g} ppm"] = C.run_variants(lib, [lap_d, ext_d], 1, _seams(lib, C.SETUP_A_ROWS), content,
                                               [v for v in rule_variants(lib, C, full=False) if v.name in wanted],
                                               cfg.seeds[:4], cfg.seconds, cfg.pools)
    out["drift_sensitivity"] = drift
    out["boundary_crossings"] = crossings(lib, cfg, lap, ext, content)
    return out


def crossings(lib, cfg, lap, ext, content, n_cross=16, seconds=300.0):
    """Forced crossings of the follower's nearest-refresh boundary (aimed start off, so the class is fixed):
    slips and flip-flops per crossing against the hysteresis, moves anytime or only at cuts. Then the worst case
    for a quiet fit: zero drift (exact pixel clocks), the offset parked on the boundary for the whole run."""
    C, P, T = cfg.common, lib.tl.Policy, lib.timing

    def run(laptop, external, tm, jit, aim_rows, etas):
        variants = [C.Variant(f"eta {e:g} ms/{mv}", tm, "crisp",
                              P(aimed_start=False, eta_s=e * 1e-3, moves=mv, aim_rows=aim_rows), jit)
                    for e in etas for mv in ("anytime", "cuts")]
        phases = lambda seed: C.crossing_phases(lib, laptop, external, seconds, seed, C.SETUP_A_ROWS[1], aim_rows,
                                                4, tm)
        res = C.run_variants(lib, [laptop, external], 1, _seams(lib, C.SETUP_A_ROWS), content, variants,
                             list(range(100, 100 + n_cross)), seconds, cfg.pools, phases)
        return {name: {k: r["seams"]["laptop"][k + "_total"] / n_cross
                       for k in ("slips_mid_shot", "slips_at_cuts", "flip_flops")} for name, r in res.items()}

    out = {f"{tm}/{jit}": run(lap, ext, tm, jit, ar, CROSS_ETAS_MS)
           for tm, jit, ar in (("wake", "probe", False), ("wake-corrected", "probe", True),
                               ("wake-corrected", "log", True), ("scan", "probe", True))}
    lap0, ext0 = T.setup_a(T.EXT_96, drift_ppm=0.0)
    parked = {f"{tm}/probe": run(lap0, ext0, tm, "probe", True, (0.0, 0.05))
              for tm in ("scan", "wake-corrected")}
    return {"crossings": n_cross, "seconds_each": seconds, "per_crossing": out,
            "zero_drift_parked_on_the_boundary_per_run": parked}


def scenario_2(lib, cfg):
    C, T = cfg.common, lib.timing
    lap, ext = T.setup_a(T.EXT_72)
    c24 = C.cfr_content(lib, 24000, 1001, cfg.seconds, True, 2, "23.976 fps, ms timestamps")
    out = {"72hz": {"setup": "laptop 240 Hz + external 72 Hz (custom), 23.976 fps, drift ASSUMED 0.5 ppm",
                    "variants": C.run_variants(lib, [lap, ext], 1, _seams(lib, C.SETUP_A_ROWS), c24,
                                               rule_variants(lib, C, full=False), cfg.seeds, cfg.seconds, cfg.pools)}}
    out["25fps_ladder"] = ladder_25(lib, cfg)
    return out


def ladder_25(lib, cfg):
    """25 fps on Setup A: the external at 100 Hz is the master (hold 4, speed 1.000); the laptop at 240 Hz needs
    9.6 refreshes per frame. Every rung of the ladder, with what it does to pace and to the seam."""
    C, T, R, P, V = cfg.common, lib.timing, lib.rules, lib.tl.Policy, cfg.common.Variant
    c25 = C.cfr_content(lib, 25, 1, cfg.seconds, False, 3, "25 fps, exact timestamps")
    lap, ext = T.setup_a(T.EXT_100)
    rec = P(eta_s=ETA_REC_S, moves="cuts", reaim="cuts")
    rungs = {"speed_nudge_within_1pct": {f"{hz} Hz": R.whole_multiple_speeds(hz, 25.0) for hz in (240, 100)},
             "speed_needed": {f"{hz} Hz": R.nearest_whole(hz, 25.0) for hz in (240, 100)}}
    base = [V("0 nearest (perf1 as built)", "wake", "crisp", P(aim_rows=False, pick="pl_queue")),
            V("0 nearest (ADR time model)", "wake-corrected", "crisp", rec),
            V("2 VRR on the laptop (not available on Setup A)", "wake-corrected", "vrr", P()),
            V("4a rolling blend on the laptop", "wake-corrected", "blend", P()),
            V("4a rolling blend, scan-line fit", "scan", "blend", P()),
            V("4a rolling blend, exact timing", "exact", "blend", P()),
            V("4b uniform blend (one share per refresh, aimed row)", "wake-corrected", "uniform-blend", P()),
            V("5 picture off the laptop", "wake-corrected", "none", P())]
    rungs["runs"] = C.run_variants(lib, [lap, ext], 1, _seams(lib, C.SETUP_A_ROWS), c25, base, cfg.seeds,
                                   cfg.seconds, cfg.pools)
    for name, mode in (("1a laptop custom 200 Hz (8 per frame)", T.LAPTOP_200),
                       ("1b laptop custom 150 Hz (6 per frame)", T.LAPTOP_150)):
        lap_c, ext_c = T.setup_a(T.EXT_100, laptop_mode=mode)
        rungs["runs"].update(C.run_variants(lib, [lap_c, ext_c], 1, _seams(lib, C.SETUP_A_ROWS), c25,
                                            [V(name, "wake-corrected", "crisp", rec)], cfg.seeds, cfg.seconds,
                                            cfg.pools))
    return rungs


def scenario_3(lib, cfg, r1, r2):
    """The rolling blend across the scenarios: exactness, cost, and fit error (wake-up fit vs scan-line fit)."""
    pick = cfg.common.pick
    rows = {}
    for label, src in (("24p on 240+96", r1["variants"]), ("24p on 240+72", r2["72hz"]["variants"]),
                       ("25p on 240+100 (laptop not a whole multiple)", r2["25fps_ladder"]["runs"])):
        for name, res in src.items():
            b = pick(res, "seams", "laptop", "blend")
            if b:
                rows[f"{label} | {name}"] = {k: b[k] for k in (
                    "err_us_p50", "err_us_p95", "err_us_p999", "err_us_max", "err_us_max_top_mid_bottom",
                    "lw_frame_duration_err_us_max", "share_new_p5_p50_p95", "rows_mixed_fraction",
                    "mixed_refreshes_per_change_mean", "mixed_refreshes_per_change_hist")}
    exact = [v["err_us_max"] for k, v in rows.items() if "exact" in k]
    return {"rows": rows, "unit_exactness_check_us": lib.blend.exactness_check(),
            "timeline_exact_max_us": max(exact) if exact else None}
