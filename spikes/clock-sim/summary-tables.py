"""Plain-text tables of the numbers the report quotes (results/summary.txt). Offsets in ms: external - laptop
(reference minus follower) at the top / aimed / bottom rows of the seam, in light."""


def _off(seam):
    o = seam["offset_ms"]
    return " | ".join(f"{o[r]['p5']:+.2f}..{o[r]['p95']:+.2f} (max {o[r]['max_abs']:.2f})" for r in ("top", "aim", "bottom"))


def _pace(h):
    return " ".join(f"{k}x{v}" for k, v in h.items())


def variant_table(title, variants, follower="laptop"):
    lines = [title, "-" * len(title),
             "variant | follower pace | slips/h mid-shot, at cuts | flip-flops/h | master re-aims/h | "
             "offset top | aim | bottom (p5..p95, max |x|)"]
    for name, r in variants.items():
        s = r["seams"].get(follower)
        if s is None:
            lines.append(f"{name} | master pace {_pace(r['master_pace'])} | (no seam)")
            continue
        if s["kind"] == "crisp":
            slips = f"{s['slips_mid_shot_per_hour']:.2f}, {s['slips_at_cuts_per_hour']:.2f}"
            lines.append(f"{name} | {_pace(s['pace'])} | {slips} | {s['flip_flops_per_hour']:.2f} | "
                         f"{r['master_reaims_per_hour']:.2f} | {_off(s)}")
        else:
            extra = ""
            if "blend" in s:
                b = s["blend"]
                extra = (f" | blend err us p50 {b['err_us_p50']:.3g} p95 {b['err_us_p95']:.3g} max {b['err_us_max']:.3g}"
                         f"; rows mixed {b['rows_mixed_fraction']:.3f}; mixed refreshes/change "
                         f"{b['mixed_refreshes_per_change_mean']:.2f}; share p5/p50/p95 {b['share_new_p5_p50_p95']}")
            lines.append(f"{name} | ({s['kind']}) | - | - | {r['master_reaims_per_hour']:.2f} | {_off(s)}{extra}")
    return "\n".join(lines) + "\n"


def summary(results, check, meta):
    out = [f"clock-sim summary ({meta['hours_per_seed']} h x {meta['seeds']} seeds per variant unless noted)", ""]
    if 1 in results:
        r = results[1]
        out.append(variant_table("Scenario 1: 23.976 fps on 240 + 96 Hz (speed +0.1 %)", r["variants"]))
        for ppm, v in r["drift_sensitivity"].items():
            out.append(variant_table(f"Scenario 1, drift {ppm}", v))
        out.append("Scenario 1, forced boundary crossings: per crossing (mid-shot slips, at-cut slips, flip-flops)")
        for model, v in r["boundary_crossings"]["per_crossing"].items():
            for name, c in v.items():
                out.append(f"  {model} | {name} | {c['slips_mid_shot']:.2f} | {c['slips_at_cuts']:.2f} | "
                           f"{c['flip_flops']:.2f}")
        out.append("Zero drift, offset parked on the boundary: per 5-min run (mid-shot slips, at-cut slips, flip-flops)")
        for model, v in r["boundary_crossings"]["zero_drift_parked_on_the_boundary_per_run"].items():
            for name, c in v.items():
                out.append(f"  {model} | {name} | {c['slips_mid_shot']:.2f} | {c['slips_at_cuts']:.2f} | "
                           f"{c['flip_flops']:.2f}")
        out.append("")
    if 2 in results:
        out.append(variant_table("Scenario 2: 23.976 fps on 240 + 72 Hz", results[2]["72hz"]["variants"]))
        lad = results[2]["25fps_ladder"]
        out.append(variant_table("Scenario 2: 25 fps ladder on Setup A (master 100 Hz, hold 4)", lad["runs"]))
        out.append(f"speed nudges within 1 %: {lad['speed_nudge_within_1pct']}; nearest whole counts and speed "
                   f"change needed: {lad['speed_needed']}\n")
    if 3 in results:
        out.append("Scenario 3: rolling blend")
        for name, b in results[3]["rows"].items():
            out.append(f"  {name}: err us p50 {b['err_us_p50']:.3g} p95 {b['err_us_p95']:.3g} p99.9 "
                       f"{b['err_us_p999']:.3g} max {b['err_us_max']:.3g}; lw frame duration err max "
                       f"{b['lw_frame_duration_err_us_max']:.3g} us; rows mixed {b['rows_mixed_fraction']:.3f}; "
                       f"mixed refreshes/change {b['mixed_refreshes_per_change_mean']:.2f} "
                       f"{b['mixed_refreshes_per_change_hist']}; share p5/p50/p95 {b['share_new_p5_p50_p95']}")
        out.append("")
    if 4 in results:
        out.append("Scenario 4: frames picked by timestamp (wrong-refresh fraction; start error ms p5/p50/p95/max)")
        for name, t in results[4]["traces"].items():
            out.append(f"  {name}: hold {t['hold']}, speed {t['speed']}, stored-true ms {t['stored_minus_true_ms']}")
            for rule in ("due", "strict", "pl_queue"):
                x = t[rule]
                by = x.get("wrong_fraction_by_section_fps", "")
                out.append(f"    {rule}: wrong {x['wrong_refresh_fraction']:.4f}; start err {x['start_error_ms_p5_p50_p95_maxabs']}; "
                           f"pace {_pace(x['pace_hist'])} {by}")
        out.append(variant_table("Scenario 4: seam with VFR content on 240 + 96", results[4]["seam_with_vfr_on_240_96"]))
    if 5 in results:
        out.append("Scenario 5: breaks (naive restart vs ADR restart)")
        for setup, cases in results[5]["cases"].items():
            for kind, r in cases.items():
                for rule in ("naive", "adr"):
                    out.append(f"  {setup} | {kind} | {rule}: {r[rule]}")
                out.append(f"  {setup} | {kind} | even follower pace {r['even_follower_pace']}; master fit error us "
                           f"p95/max {r['master_fit_error_us_p95_max']}")
        out.append("")
    if 6 in results:
        for name, v in results[6].items():
            if isinstance(v, dict) and v and all(isinstance(x, dict) and "seams" in x for x in v.values()):
                for follower in ("other", "fast-144", "lg-4k"):
                    if any(follower in x["seams"] for x in v.values()):
                        out.append(variant_table(f"Scenario 6: {name} - seam of {follower}", v, follower))
    out.append(f"Self-check: {check}")
    return "\n".join(out) + "\n"
