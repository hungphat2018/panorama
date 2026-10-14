"""Does the picture's edge stay still within a shot? Decides whether one generated plate per shot can meet the live picture.

A plate is generated once per shot from one frame. If the strip of picture next to the seam changes later in the shot (a
character walks to the edge, an arm swings, an effect flashes), the plate no longer continues it and the seam shows. This
measures, per shot, how far the left and right edge bands drift from the shot's middle frame (the plate's likely source) and
how often they change from one frame to the next.

The film is decoded once at 480x270 grey by ffmpeg (hardware decode when available). Bands are the outer 10% and 25% of the
picture's width on each side (25% = the hidden side of the hidden-sides test at s = 0.5). A step "changes" a band when its
mean absolute difference exceeds --held-below (code values, same rule the content model uses for held drawings). Drift is
the mean absolute difference between a frame's band and the middle frame's band. Per shot, the left and right values are
also kept apart (`*_lr`), so a shot whose one side holds while the other moves can be found (routing by motion). Only one
shot's bands are held in memory at a time.

    python -I edge-band-change.py FILM CONTENT_DIR [--out FILE] [--held-below 0.8] [--drift-limit 6]
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

import numpy as np

W, H = 480, 270
BANDS = {"10": 0.10, "25": 0.25}


def frames(film):
    ffmpeg = os.environ.get("PANORAMA_FFMPEG") or shutil.which("ffmpeg")
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-hwaccel", "auto", "-i", film, "-map", "0:v:0",
           "-vf", f"scale={W}:{H}:flags=area,format=gray", "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=W * H * 64)
    size = W * H
    while True:
        buf = p.stdout.read(size)
        if len(buf) < size:
            break
        yield np.frombuffer(buf, np.uint8).reshape(H, W)
    p.wait()


def band_cols(share):
    n = max(1, int(round(W * share)))
    return slice(0, n), slice(W - n, W)


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("film")
    ap.add_argument("content_dir")
    ap.add_argument("--out")
    ap.add_argument("--held-below", type=float, default=0.8)
    ap.add_argument("--drift-limit", type=float, default=6.0, help="mean abs difference (code values) above which a band no longer matches the plate's source frame")
    a = ap.parse_args(argv)

    shots = json.load(open(os.path.join(a.content_dir, "shots.json"), encoding="utf-8"))["shots"]
    last = shots[-1]["last_frame"]
    tally = {k: {"static_shots": 0, "static_frames": 0, "drift_ok_frames": 0, "changed_steps": 0, "within_steps": 0,
                 "per_shot": []} for k in BANDS}

    def close_shot(s, bands, steps):
        """Per-shot stats of one finished shot: bands[k] = its frames' bands (uint8), steps[k] = its in-shot step changes."""
        for k in BANDS:
            seg = np.stack(bands[k]).astype(np.int16)              # (frames, H, 2*band)
            half = seg.shape[2] // 2
            ref = seg[(len(seg) - 1) // 2]
            drift = np.stack([np.abs(seg[:, :, :half] - ref[:, :half]).mean(axis=(1, 2)),
                              np.abs(seg[:, :, half:] - ref[:, half:]).mean(axis=(1, 2))], axis=1)
            ch = np.asarray(steps[k], dtype=np.float64).reshape(-1, 2) > a.held_below   # steps that change a band
            t = tally[k]
            t["within_steps"] += ch.shape[0]
            t["changed_steps"] += int(ch.any(axis=1).sum())
            ok = drift.max(axis=1) <= a.drift_limit
            t["drift_ok_frames"] += int(ok.sum())
            is_static = bool(ok.all())
            t["static_shots"] += is_static
            t["static_frames"] += len(seg) if is_static else 0
            share = lambda c: round(float(c.mean()) if c.size else 0.0, 3)
            t["per_shot"].append({
                "shot": s["id"], "frames": len(seg), "changed_step_share": share(ch.any(axis=1)),
                "drift_p90": round(float(np.percentile(drift.max(axis=1), 90)), 2), "plate_holds": is_static,
                "changed_step_share_lr": [share(ch[:, 0]), share(ch[:, 1])],
                "drift_p90_lr": [round(float(np.percentile(drift[:, i], 90)), 2) for i in (0, 1)],
                "plate_holds_lr": [bool((drift[:, i] <= a.drift_limit).all()) for i in (0, 1)]})

    # Stream: each frame's bands join the current shot; a shot is measured and dropped as soon as its last frame is in.
    order = iter(shots)
    cur = next(order)
    bands = {k: [] for k in BANDS}
    steps = {k: [] for k in BANDS}
    prev = {}
    n = 0
    for f in frames(a.film):
        if n > last:
            break
        for k, share in BANDS.items():
            l, r = band_cols(share)
            band = np.concatenate([f[:, l], f[:, r]], axis=1)
            if bands[k] and k in prev:                             # a step inside the shot (not across its cut)
                d = np.abs(band.astype(np.int16) - prev[k].astype(np.int16))
                half = band.shape[1] // 2
                steps[k].append((d[:, :half].mean(), d[:, half:].mean()))
            prev[k] = band
            bands[k].append(band)
        if n == cur["last_frame"]:
            close_shot(cur, bands, steps)
            bands = {k: [] for k in BANDS}
            steps = {k: [] for k in BANDS}
            cur = next(order, None)
            if cur is None:
                n += 1
                break
        n += 1
    if cur is not None and bands[next(iter(BANDS))]:              # the film ended inside a shot
        close_shot(cur, bands, steps)
    if n != last + 1:
        print(f"warning: decoded {n} frames, content model has {last + 1}", file=sys.stderr)

    result = {"frames": n, "shots": len(shots), "held_below": a.held_below, "drift_limit": a.drift_limit, "bands": {}}
    for k in BANDS:
        t = tally[k]
        result["bands"][k + "%"] = {
            "shots_whose_band_stays_within_drift_limit": round(t["static_shots"] / len(shots), 3),
            "frames_in_such_shots": round(t["static_frames"] / n, 3),
            "frames_within_drift_limit_of_mid_frame": round(t["drift_ok_frames"] / n, 3),
            "steps_that_change_a_band": round(t["changed_steps"] / max(1, t["within_steps"]), 3),
            "per_shot": t["per_shot"],
        }
    text = json.dumps(result, indent=1)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    print(json.dumps({k: {kk: vv for kk, vv in v.items() if kk != "per_shot"} for k, v in result["bands"].items()}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
