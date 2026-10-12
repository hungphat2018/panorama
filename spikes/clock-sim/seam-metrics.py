"""Measures a played timeline in light on the true grids, and pools runs (seeds).

Seam offset: when the reference side's row changes to the next film frame minus when the follower side's row at
the same height does, in light (blank, scan and panel lag included). On Setup A the reference is the external
(the master), so it reads "external - laptop", as in the spike reports. For a blend follower it is the
reference row's moment minus the follower row's light-weighted moment.
Phase slip: a follower frame that lasts one refresh more or less than the even pace (a 9 or 11 at 240 Hz).
"""
import numpy as np

FLIP_FLOP_FRAMES = 48     # two opposite slips within 48 frames (2 s), not at cuts, count as one flip-flop
BLEND_ROWS = 33           # heights per seam for blend errors (top, ..., aimed, ..., bottom)
MIX_EPS = 0.02            # a row "shows a mix" when the new frame's share is between 2 % and 98 %
MIX_SAMPLE = 2000         # frame changes for the every-row mixed-refresh count


def _hist(a):
    v, c = np.unique(a, return_counts=True)
    return {int(k): int(n) for k, n in zip(v, c)}


def _sum_hist(hs):
    out = {}
    for h in hs:
        for k, v in h.items():
            out[k] = out.get(k, 0) + v
    return dict(sorted(out.items()))


def _slips(n, pos, is_cut, p, q):
    """Follower frames off the even pace (the film clock's master refresh count times p/q, where whole): at cuts
    (including re-aims), mid-shot, and mid-shot flip-flops (a slip undone within FLIP_FLOP_FRAMES)."""
    pace = np.diff(n)
    nominal = np.diff(pos) * p / q
    whole = np.abs(nominal - np.round(nominal)) < 1e-9
    d = np.where(whole, pace - np.round(nominal), 0).astype(np.int64)
    at = np.flatnonzero(d != 0)
    mid = ~is_cut[1:][at]
    sgn = np.sign(d[at])
    ff = int(np.sum((np.diff(at) <= FLIP_FLOP_FRAMES) & (sgn[1:] == -sgn[:-1]) & mid[1:] & mid[:-1]))
    return pace, {"slips_at_cuts": int((~mid).sum()), "slips_mid_shot": int(mid.sum()), "flip_flops": ff,
                  "whole_multiple": bool(whole.all())}


def measure(lib, ses, master, seams, run):
    """One run: master pace and re-aims, and per seam the follower's pace, slips and offsets in light.
    Returns (summary, raw) where raw holds per-change arrays keyed by (seam name, kind) for pooling."""
    J, is_cut = run["J"], run["is_cut"]
    tM = ses.tops[master]
    mp = np.diff(J)
    out = {"hours": float(tM[J[-1]] - tM[J[0]]) / 3600.0, "frames": int(len(J)), "hold": run["hold"],
           "speed": run["speed"], "locked": run["locked"], "classes": run["Q"], "master_pace": _hist(mp),
           "master_reaims": int(np.sum(np.diff(run["shift"]) != 0)),       # always at cuts, by construction
           "av_shift_refreshes": [int(run["shift"].min() - run["s0"]), int(run["shift"].max() - run["s0"])],
           "skipped_frames": int(np.sum(np.diff(run["shown"]) > 1)), "seams": {}}
    raw, chosen = {}, {}
    for s in seams.values():
        inf, mf, tf = run["info"][s.mon], ses.monitors[s.mon], ses.tops[s.mon]
        if s.ref != master and seams[s.ref].kind == "crisp":
            mr, ref_top = ses.monitors[s.ref], ses.tops[s.ref][chosen[s.ref]]
        else:                                   # the master's wave (also what a blend/VRR neighbour shows)
            mr, ref_top = ses.monitors[master], tM[J]
        rows = np.asarray(s.rows_mm, dtype=float)
        ref_light = ref_top[:, None] + mr.light_offset_s(rows)[None, :]
        res = {"kind": s.kind, "reference": mr.name}
        if s.kind == "crisp":
            chosen[s.mon] = inf["n"]
            off = ref_light - (tf[inf["n"]][:, None] + mf.light_offset_s(rows)[None, :])
            pace, slips = _slips(inf["n"], run["pos"], is_cut, *run["ratio"][s.mon])
            res.update(slips, pace=_hist(pace))
        elif s.kind == "vrr":                   # presents at the believed moment; the refresh starts then
            d = mr.light_offset_s(s.aim_mm) - mf.light_offset_s(s.aim_mm)
            off = ref_light - ((inf["ref_top_hat"] + d)[:, None] + mf.light_offset_s(rows)[None, :])
        else:
            off, res["blend"], _ = _blend(lib, ses, s, inf, mr, ref_top, rows)
        raw[(mf.name, "off")] = off
        out["seams"][mf.name] = res
    return out, raw


def _blend(lib, ses, s, inf, mr, ref_top, rows):
    """Rolling blend (a share per row) or uniform blend (one share for the whole screen, set at the aimed row,
    as libplacebo's oversample mixer would). Returns (offsets at the reported rows, summary, errors)."""
    mf, tf, g = ses.monitors[s.mon], ses.tops[s.mon], ses.grids[s.mon]

    def rows_eval(heights, sel):
        e_true = ref_top[sel, None] + mr.light_offset_s(heights)[None, :]
        r_f = mf.light_offset_s(heights)
        if s.kind == "uniform-blend":
            h = np.atleast_1d(s.aim_mm)
            e_aim = ref_top[sel, None] + mr.light_offset_s(h)[None, :]
            e_aim_hat = inf["ref_top_hat"][sel, None] + mr.light_offset_s(h)[None, :]
            k, w, _ = lib.blend.blend_rows(e_aim, e_aim_hat, g, tf, mf.period, mf.light_offset_s(h), inf["t_dec"][sel])
            k, w = np.repeat(k, len(heights), axis=1), np.repeat(w, len(heights), axis=1)
            return k, w, tf[k] + r_f[None, :] + (1.0 - w) * mf.period - e_true
        e_hat = inf["ref_top_hat"][sel, None] + mr.light_offset_s(heights)[None, :]
        return lib.blend.blend_rows(e_true, e_hat, g, tf, mf.period, r_f, inf["t_dec"][sel])

    k, w, err = rows_eval(np.linspace(rows[0], rows[-1], BLEND_ROWS), slice(None))
    n_fine = max(2, int(round((rows[-1] - rows[0]) / mf.height_mm * mf.mode.active_h)))
    sample = np.unique(np.linspace(0, len(ref_top) - 1, min(MIX_SAMPLE, len(ref_top))).astype(np.int64))
    kf, wf, _ = rows_eval(np.linspace(rows[0], rows[-1], n_fine), sample)
    summary = lib.blend.summarize_blend(err, w, kf, wf, MIX_EPS)
    return -err[:, [0, BLEND_ROWS // 2, BLEND_ROWS - 1]], summary, err


def offset_stats(off):
    """Per reported row (top, aimed, bottom): signed p5 / p50 / p95 and the largest magnitude, in ms."""
    o = off * 1e3
    return {name: {"p5": round(float(np.percentile(o[:, i], 5)), 4), "p50": round(float(np.percentile(o[:, i], 50)), 4),
                   "p95": round(float(np.percentile(o[:, i], 95)), 4), "max_abs": round(float(np.abs(o[:, i]).max()), 4)}
            for i, name in enumerate(("top", "aim", "bottom"))}


def pool(results):
    """Pools runs (seeds) of one variant: counts summed, rates per hour, offsets pooled over all changes."""
    outs = [r[0] for r in results]
    hours = sum(o["hours"] for o in outs)
    first = outs[0]
    pooled = {"runs": len(outs), "hours": round(hours, 3), "frames": sum(o["frames"] for o in outs),
              "hold": first["hold"], "speed": round(first["speed"], 6), "locked": first["locked"],
              "classes": first["classes"],
              "master_reaims_per_hour": round(sum(o["master_reaims"] for o in outs) / hours, 4),
              "av_shift_refreshes": [min(o["av_shift_refreshes"][0] for o in outs),
                                     max(o["av_shift_refreshes"][1] for o in outs)],
              "skipped_frames": sum(o["skipped_frames"] for o in outs),
              "master_pace": _sum_hist([o["master_pace"] for o in outs]), "seams": {}}
    for name, s0 in first["seams"].items():
        off = np.concatenate([r[1][(name, "off")] for r in results])
        res = {"kind": s0["kind"], "reference": s0["reference"], "offset_ms": offset_stats(off)}
        if s0["kind"] == "crisp":
            for key in ("slips_at_cuts", "slips_mid_shot", "flip_flops"):
                total = sum(o["seams"][name][key] for o in outs)
                res[key + "_per_hour"], res[key + "_total"] = round(total / hours, 4), total
            res["pace"] = _sum_hist([o["seams"][name]["pace"] for o in outs])
            res["whole_multiple"] = s0["whole_multiple"]
        if "blend" in s0:
            b = [o["seams"][name]["blend"] for o in outs]
            res["blend"] = dict(b[0])
            for key in ("err_us_p50", "err_us_p95", "err_us_p999", "rows_mixed_fraction",
                        "mixed_refreshes_per_change_mean"):
                res["blend"][key] = round(float(np.mean([x[key] for x in b])), 6)
            res["blend"]["err_us_max"] = max(x["err_us_max"] for x in b)
            res["blend"]["lw_frame_duration_err_us_max"] = max(x["lw_frame_duration_err_us_max"] for x in b)
            res["blend"]["err_us_max_top_mid_bottom"] = [float(v) for v in
                                                         np.max([x["err_us_max_top_mid_bottom"] for x in b], axis=0)]
            res["blend"]["mixed_refreshes_per_change_hist"] = _sum_hist(
                [x["mixed_refreshes_per_change_hist"] for x in b])
        pooled["seams"][name] = res
    return pooled
