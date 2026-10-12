"""Scenario 6: three screens like Setup B, 23.976 fps. ALL Setup B timings and sizes are assumptions
(display-timing.py): only the LG UltraFine's EDID (60 Hz, 40-60 Hz range, no VRR) is on record.

Layout A (assumed): other 60 Hz (24") | LG 4K (27") | 144 Hz (27"), vertical centres level.
Layout B (chain):   LG 4K | 144 Hz | other, so the far screen follows its neighbour, not the master.
Seam rows: (top, middle, bottom) of the height both screens of a seam cover.
"""
SEEDS_B = 4


def _rows(a, b):
    top, bottom = max(a.top_mm, b.top_mm), min(a.top_mm + a.height_mm, b.top_mm + b.height_mm)
    return (top, (top + bottom) / 2, bottom)


def _run(lib, cfg, monitors, master, pairs, content, kinds, eta, label_drift=""):
    """pairs: [(follower, reference)] in chain order; kinds: {name: "kindA|kindB"} per variant."""
    C, P = cfg.common, lib.tl.Policy
    rec = P(eta_s=eta, moves="cuts", reaim="cuts")

    def seams_for(kind):
        ks = kind.split("|")
        return {f: lib.tl.Seam(f, r, _rows(monitors[f], monitors[r]), k) for (f, r), k in zip(pairs, ks)}

    variants = [C.Variant(name + label_drift, "wake-corrected", kind, rec) for name, kind in kinds.items()]
    return C.run_variants(lib, monitors, master, seams_for, content, variants, cfg.seeds[:SEEDS_B], cfg.seconds,
                          cfg.pools)


def scenario_6(lib, cfg, eta):
    C, T, R = cfg.common, lib.timing, lib.rules
    content = C.cfr_content(lib, 24000, 1001, cfg.seconds, True, 7, "23.976 fps, ms timestamps")
    fps = 24000 / 1001
    out = {"assumptions": {
        "lg": "27-inch 3840x2160; 60 Hz = 4400x2250 at 594 MHz (EDID); 48 Hz = 4400x2750 at 580.8 MHz (fast scan)",
        "other": "24-inch 1920x1080; 60 Hz = 2200x1125 at 148.5 MHz; 48 Hz = 2200x1400 at 147.84 MHz; "
                 "72 Hz = 2200x1125 at 178.2 MHz (if accepted)",
        "fast": "27-inch 2560x1440 at 144 Hz, 2720x1481 totals; VRR assumed available",
        "clocks": "one GPU drives all three: 0.5 ppm between heads (Setup A's measurement); "
                  "independent clocks case: +-20 ppm", "wake_jitter": "Setup A external's"},
        "whole_multiples_at_23.976": {f"{hz} Hz": {"refreshes_per_frame": round(hz / fps, 3),
                                                   "speed_nudge_within_1pct": R.whole_multiple_speeds(hz, fps)}
                                      for hz in (60, 144, 48, 72)}}
    timings = {}
    for name, mode in (("LG 60", T.LG_60), ("LG 48", T.LG_48), ("other 60", T.OTHER_60), ("other 48", T.OTHER_48),
                       ("other 72", T.OTHER_72), ("144", T.HZ144)):
        timings[name] = {"rate_hz": round(mode.rate_hz, 4), "scan_ms": round(mode.scan_s * 1e3, 3),
                         "blank_ms": round(mode.blank_s * 1e3, 3)}
    out["timings"] = timings

    other, lg, fast = T.setup_b(T.LG_48, T.OTHER_48)
    out["48 Hz custom on both 60 Hz screens (master LG, centre)"] = _run(
        lib, cfg, [other, lg, fast], 1, [(0, 1), (2, 1)], content,
        {"other crisp | 144 crisp": "crisp|crisp", "other crisp | 144 VRR": "crisp|vrr",
         "other crisp | 144 rolling blend": "crisp|blend", "other rolling blend (20.8 ms mixes) | 144 rolling blend":
             "blend|blend"}, eta)
    other_i, lg_i, fast_i = T.setup_b(T.LG_48, T.OTHER_48, seed_ppm=(20.0, -20.0))
    out["48 Hz custom, screens on independent clocks (+-20 ppm, assumed)"] = _run(
        lib, cfg, [other_i, lg_i, fast_i], 1, [(0, 1), (2, 1)], content,
        {"other crisp | 144 crisp": "crisp|crisp", "other rolling blend | 144 rolling blend": "blend|blend"}, eta)
    other72, lg48, fast72 = T.setup_b(T.LG_48, T.OTHER_72)
    out["LG 48 Hz, other 72 Hz (if accepted)"] = _run(
        lib, cfg, [other72, lg48, fast72], 1, [(0, 1), (2, 1)], content,
        {"other crisp | 144 crisp": "crisp|crisp", "other rolling blend (13.9 ms mixes) | 144 rolling blend":
            "blend|blend"}, eta)
    other60, lg60, fast60 = T.setup_b(T.LG_60, T.OTHER_60)
    out["native 60 Hz (master = the 144 Hz screen, the only whole multiple)"] = _run(
        lib, cfg, [other60, lg60, fast60], 2, [(1, 2), (0, 1)], content,
        {"LG crisp | other crisp (chain)": "crisp|crisp",
         "LG rolling blend (16.7 ms mixes) | other rolling blend": "blend|blend"}, eta)
    # chain layout: LG | 144 | other; the far screen follows its neighbour
    lgc, fastc, otherc = (lambda o, l, f: (l, f, o))(*T.setup_b(T.LG_48, T.OTHER_48))
    out["chain layout LG 48 | 144 | other 48"] = _run(
        lib, cfg, [lgc, fastc, otherc], 0, [(1, 0), (2, 1)], content,
        {"144 crisp | other crisp follows the 144's chosen refresh": "crisp|crisp",
         "144 rolling blend | other crisp follows the wave": "blend|crisp"}, eta)
    return out
