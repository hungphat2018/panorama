"""Scenario 4: frames picked by timestamp, on real and variable-frame-rate timestamp traces.

For every master refresh the film time is (j - j0) * step (speed-locked). The intended frame is the one whose
true timestamp starts on the master refresh nearest to it ("last frame due" with half a master refresh of
tolerance, on exact timestamps). Three rules see the stored (rounded) timestamps:
  due       last frame with pts <= film time + step/2           (ADR 0021: frames picked by timestamp)
  strict    last frame with pts <= film time                    (no tolerance)
  pl_queue  perf1's shortcut: pl_queue's nearest frame to film time + step/2 - frame_dur/2 (nominal duration)
Real traces come from ffprobe (video packets, read-only) and are cached in data/.
"""
import os
import subprocess
from pathlib import Path

import numpy as np

JUDAS = Path(r"C:/Users/tamla/Downloads/[Judas] Nama-Anaru - S01E02.mkv")
HOTEL = Path(r"C:/Users/tamla/Downloads/260723_HOTEL_AMAN_43_V3_MAIN_50s_MASTER_16x9_VA_H264.mp4")
FFPROBES = (Path(r"C:/Users/tamla/msys64/ucrt64/bin/ffprobe.exe"),
            Path(r"C:/Users/tamla/msys64/home/tamla/p0/prefix/ffmpeg-lgpl/bin/ffprobe.exe"))
VFR_RATES = ((24000, 1001, 0.70), (30000, 1001, 0.20), (60000, 1001, 0.10))   # ASSUMED mix, anime-style VFR
RULES = ("due", "strict", "pl_queue")


def probe_pts(path, cache, ffprobe=None, reprobe=False):
    """Video packet timestamps (seconds from the first frame), sorted into presentation order; cached."""
    if cache.exists() and not reprobe:
        return np.loadtxt(cache)
    exe = Path(ffprobe) if ffprobe else next((p for p in FFPROBES if p.exists()), None)
    if exe is None or not Path(path).exists():
        return None
    env = dict(os.environ, PATH=str(exe.parent) + os.pathsep + os.environ.get("PATH", ""))
    out = subprocess.run([str(exe), "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts_time",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True, env=env, check=True).stdout
    pts = np.sort(np.array([float(x.strip(",")) for x in out.split() if x.strip(",") not in ("", "N/A")]))
    pts -= pts[0]
    cache.parent.mkdir(parents=True, exist_ok=True)
    np.savetxt(cache, pts, fmt="%.6f")
    return pts


def synthetic_vfr(seconds, seed):
    """Sections at 23.976 / 29.97 / 59.94 fps, 5-60 s each; exact timestamps and MKV-style ms rounding."""
    rng = np.random.default_rng([seed, 4])
    t, true, rate_of = 0.0, [], []
    while t < seconds:
        num, den, _ = VFR_RATES[rng.choice(len(VFR_RATES), p=[w for *_, w in VFR_RATES])]
        n = int(rng.uniform(5.0, 60.0) * num / den)
        true.append(t + np.arange(n) * den / num)
        rate_of.append(np.full(n, num / den))
        t += n * den / num
    true = np.concatenate(true)
    return true, np.round(true * 1000.0) / 1000.0, np.concatenate(rate_of)


def check_picks(lib, true_pts, stored_pts, frame_dur, master_hz, rate_of=None):
    """Compares each rule's frame with the intended one on every master refresh of the trace."""
    R = lib.rules
    hold, speed, step, locked = R.speed_lock(frame_dur, 1.0 / master_hz)
    film_t = np.arange(int(true_pts[-1] / step) - 2) * step
    intended = R.last_frame_due(true_pts, film_t, 0.5 * step)
    out = {"master_hz": master_hz, "hold": hold, "speed": round(speed, 6), "refreshes": int(len(film_t)),
           "frames": int(len(true_pts)), "stored_minus_true_ms": [round(float(x), 3) for x in
                                                                    np.percentile((stored_pts - true_pts) * 1e3,
                                                                                  [0, 50, 100])]}
    for rule in RULES:
        got = (R.pl_queue_pick(stored_pts, film_t, step, frame_dur) if rule == "pl_queue"
               else R.last_frame_due(stored_pts, film_t, 0.5 * step if rule == "due" else 0.0))
        wrong = got != intended
        pos, shown = R.frame_changes(got)
        start_err = film_t[pos] - true_pts[np.maximum(shown, 0)]        # first display of each frame - its time
        res = {"wrong_refresh_fraction": round(float(wrong.mean()), 6), "wrong_refreshes": int(wrong.sum()),
               "frames_never_shown": int(len(true_pts) - len(np.unique(got[got >= 0]))) - int(
                   len(true_pts) - len(np.unique(intended[intended >= 0]))),
               "start_error_ms_p5_p50_p95_maxabs": [round(float(x), 3) for x in
                                                    list(np.percentile(start_err * 1e3, [5, 50, 95]))
                                                    + [np.abs(start_err).max() * 1e3]],
               "pace_hist": {int(k): int(v) for k, v in zip(*np.unique(np.diff(pos), return_counts=True))}}
        if rate_of is not None:
            fr = rate_of[np.clip(intended, 0, len(rate_of) - 1)]
            res["wrong_fraction_by_section_fps"] = {f"{r:.3f}": round(float(wrong[np.isclose(fr, r)].mean()), 4)
                                                    for r in np.unique(rate_of)}
        out[rule] = res
    return out


def scenario_4(lib, cfg):
    C, T, data = cfg.common, lib.timing, cfg.here / "data"
    out = {"traces": {}}
    judas = probe_pts(JUDAS, data / "judas-s01e02-video-pts.txt", cfg.ffprobe, cfg.reprobe)
    if judas is not None:
        exact = np.arange(len(judas)) * 1001 / 24000          # the file is CFR 24000/1001 (checked below)
        out["traces"]["anime mkv (real, ms timestamps) on 96 Hz"] = dict(
            check_picks(lib, exact, judas, 1001 / 24000, 96.0), cfr_deviation_ms=[
                round(float(x), 3) for x in ((judas - exact).min() * 1e3, (judas - exact).max() * 1e3)])
        out["traces"]["anime mkv (real) on 72 Hz"] = check_picks(lib, exact, judas, 1001 / 24000, 72.0)
    hotel = probe_pts(HOTEL, data / "hotel-aman-video-pts.txt", cfg.ffprobe, cfg.reprobe)
    if hotel is not None:
        out["traces"]["25 fps mp4 (real) on 100 Hz"] = check_picks(lib, np.arange(len(hotel)) * 0.04, hotel,
                                                                    0.04, 100.0)
    true, stored, rate_of = synthetic_vfr(cfg.seconds, 1)
    out["traces"]["synthetic VFR 24/30/60 on 96 Hz"] = check_picks(lib, true, stored, 1001 / 24000, 96.0, rate_of)
    out["traces"]["synthetic VFR 24/30/60 on 72 Hz"] = check_picks(lib, true, stored, 1001 / 24000, 72.0, rate_of)

    # The seam with VFR content: frame changes land on every master alignment class, not one.
    lap, ext = T.setup_a(T.EXT_96)
    content = lib.tl.Content("synthetic VFR", stored, 1001 / 24000,
                             lib.rules.make_cuts(len(stored), 1001 / 24000, np.random.default_rng(6)))
    P, V = lib.tl.Policy, C.Variant
    seams = lambda kind: {0: lib.tl.Seam(0, 1, C.SETUP_A_ROWS, kind)}
    out["seam_with_vfr_on_240_96"] = C.run_variants(lib, [lap, ext], 1, seams, content, [
        V("aimed start only (no re-aim)", "wake-corrected", "crisp", P(eta_s=5e-5, moves="cuts")),
        V("recommended (re-aim at cuts)", "wake-corrected", "crisp", P(eta_s=5e-5, moves="cuts", reaim="cuts")),
        V("perf1 as built", "wake", "crisp", P(aim_rows=False, pick="pl_queue")),
        V("perf1 with row timing (scan-line grid, no hysteresis)", "scan", "crisp", P(pick="pl_queue")),
        V("rolling blend", "wake-corrected", "blend", P())], cfg.seeds[:4], cfg.seconds, cfg.pools)
    return out
