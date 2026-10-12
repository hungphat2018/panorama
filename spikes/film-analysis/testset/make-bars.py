"""Constructed clips with exact black-bar truth: bars painted onto real footage, natural scope pictures padded onto a 16:9 canvas.

Usage: python -I make-bars.py VARIANT [--seed N]

  anime-a      first 7,200 frames of the anime episode (cold open, opening, scene A): four windows of 1,800 frames: scope bars (138 px top and
               bottom), no bars, 4:3 pillarbox (240 px left and right), and alternating scope / none at the real cuts (IMAX-like).
  anime-dark   the same four windows over the night scenes (source frames 20,100-27,300).
  live-dark    the same four windows over Meridian (dark noir live action, 59.94 fps source resampled to 23.976), 7:00 of film from 2:30.
  lifted       Meridian again, but the bars are lifted black (luma 22) with grain (sigma 1.2): the black level must be calibrated per file.
  mixed        about 60 excerpts alternating natural scope pictures (Tears of Steel, Sintel: 1920x800 / 1920x818 padded onto the canvas, bars exact)
               with full 16:9 pictures (Meridian, promo, Big Buck Bunny, anime), hard cuts and six dips to black: bars appear and disappear
               between shots, next to dark pictures.

Output: tools/testset/clips/ctor-bars-<variant>.mp4 (anime: .mkv HEVC 10-bit like the source), labels/<clip>/bars.json (status exact):
the rectangle of real picture per frame range, frames whose picture area is black (truth not visible), and the real cuts the bars switch on.
A bar change only ever happens at a cut that the detectors agree on (or at the exact join), never inside a shot.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import testset_common as tc  # noqa: E402
import testset_compose as comp  # noqa: E402
from testset_bank import CANVAS_H, CANVAS_W, canvas_fit, clip_info, pick_excerpts, tc_clip_file  # noqa: E402

_EVAL = str(Path(__file__).resolve().parent.parent / "eval")
if _EVAL not in sys.path:
    sys.path.insert(0, _EVAL)
from fa_eval import cache  # noqa: E402
from fa_eval.methods import shots_policy as sp  # noqa: E402

FPS = "24000/1001"
FPS_F = 24000 / 1001
SCOPE = (0, 138, 1920, 942)
FULL = (0, 0, 1920, 1080)
PILLAR = (240, 0, 1680, 1080)
WIN = 1800

VARIANTS = {
    "anime-a": dict(source="anime-nama-anaru-ep2", start_frame=0, codec="libx265", suffix="mkv", black=(16, 128, 128), noise=0.0),
    "anime-dark": dict(source="anime-nama-anaru-ep2", start_frame=20100, codec="libx265", suffix="mkv", black=(16, 128, 128), noise=0.0),
    "live-dark": dict(source="meridian-4k", t_start=150.0, codec="libx264", suffix="mp4", black=(16, 128, 128), noise=0.0),
    "lifted": dict(source="meridian-4k", t_start=420.0, codec="libx264", suffix="mp4", black=(22, 128, 128), noise=1.2, windows=2, win_len=1200),
}


def proxy_gray(path, t_start: float, n: int, fps: str | None, vf: str) -> np.ndarray:
    """Tiny gray frames (68x120) of the same n output frames the full pass will produce (same seek, same fps filter)."""
    chain = ([f"fps={fps}"] if fps else []) + ([vf] if vf else []) + ["scale=120:68:flags=area", "format=gray"]
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-threads", "4", "-ss", f"{t_start:.4f}", "-i", str(path), "-an", "-sn", "-map", "0:v:0",
           "-frames:v", str(n), "-vf", ",".join(chain), "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    r = tc.run(cmd)
    a = np.frombuffer(r.stdout, np.uint8)
    k = len(a) // (68 * 120)
    return a[: k * 68 * 120].reshape(k, 68, 120)


def exact_cuts(clip_id: str, t_start: float, n: int, fps_out: float, fps_arg: str | None, vf: str) -> list[int]:
    """Output-frame indices (0..n-1) of cuts inside the excerpt: consensus cuts of the source clip, moved to the frame with the biggest
    picture change within 2 frames of where the timeline says (frame-rate conversion can shift them by one)."""
    sig = cache.load_npz("signals", clip_id)
    cuts = sp.consensus_cuts(sig)
    path = tc_clip_file(clip_id)
    times = tc.frame_seconds(path, [b.frame for b in cuts])
    thumbs = proxy_gray(path, t_start, n, fps_arg, vf)
    d = np.zeros(len(thumbs), np.float32)
    d[1:] = np.abs(thumbs[1:].astype(np.int16) - thumbs[:-1].astype(np.int16)).mean(axis=(1, 2))
    out = []
    for t in times:
        o = int(round((t - t_start) * fps_out))
        if o < 3 or o >= len(d) - 3:
            continue
        lo, hi = o - 2, o + 3
        k = lo + int(np.argmax(d[lo:hi]))
        med = float(np.median(d[max(0, o - 12): o + 13])) + 1e-3
        if d[k] >= 6.0 and d[k] >= 3.0 * med:
            out.append(k)
    return sorted(set(out))


def rle(rects: np.ndarray) -> list[dict]:
    segs, start = [], 0
    for i in range(1, len(rects) + 1):
        if i == len(rects) or not np.array_equal(rects[i], rects[start]):
            segs.append({"frames": [start, i], "rect": [int(v) for v in rects[start]]})
            start = i
    return segs


def ambiguous_ranges(black_luma: np.ndarray, level: int) -> list[list[int]]:
    """Frame ranges in which the whole picture area is at most 4 codes above the bar level (so the true rectangle cannot be seen)."""
    dark = black_luma <= level + 4
    out, i = [], 0
    while i < len(dark):
        if dark[i]:
            j = i
            while j < len(dark) and dark[j]:
                j += 1
            out.append([i, j])
            i = j
        else:
            i += 1
    return out


def painted(variant: str, a, rng) -> dict:
    v = VARIANTS[variant]
    src = v["source"]
    path = tc_clip_file(src)
    info = clip_info(src)
    fitvf, _ = canvas_fit(info)
    fitvf = "" if fitvf == "null" else fitvf
    win = v.get("win_len", WIN)
    nwin = v.get("windows", 4)
    n = win * nwin
    src_fps = float(Fraction(info["fps"]))
    same_rate = abs(src_fps - FPS_F) < 0.01
    fps_arg = None if same_rate else FPS
    if "start_frame" in v:
        t0 = float(tc.frame_seconds(path, [v["start_frame"]])[0])
    else:
        t0 = v["t_start"]
    cuts = exact_cuts(src, t0, n, FPS_F, fps_arg, fitvf)
    rects = np.zeros((n, 4), np.int32)
    rects[:] = FULL
    plan = [("scope", SCOPE), ("full", FULL), ("pillar", PILLAR)][: max(nwin - 1, 1)]
    if nwin == 2:
        plan = [("scope", SCOPE)]
    for k, (_, r) in enumerate(plan):
        rects[k * win:(k + 1) * win] = r
    # last window: alternate scope / full at the cuts that fall inside it
    last0 = (nwin - 1) * win
    state = 0
    toggles = [c for c in cuts if last0 < c < n]
    cur = SCOPE
    rects[last0:] = cur
    for c in toggles:
        state ^= 1
        cur = FULL if state else SCOPE
        rects[c:] = cur
        # keep the next window's own rectangle only inside this window (n is the end)
    black = v["black"]
    noise_sigma = v["noise"]
    out = tc.CLIPS / f"ctor-bars-{variant}.{v['suffix']}"
    enc = dict(codec=v["codec"], crf=18 if v["codec"] == "libx265" else 14, preset="fast" if v["codec"] == "libx265" else "medium",
               pix_fmt="yuv420p10le" if v["codec"] == "libx265" else "yuv420p")
    peak = np.zeros(n, np.int32)
    pad_rng = np.random.default_rng(a.seed + 1)
    with tc.VideoWriter(out, CANVAS_W, CANVAS_H, FPS, **enc) as vw:
        for i, buf in enumerate(tc.read_excerpt_yuv420(path, t0, n, CANVAS_W, CANVAS_H, fps=fps_arg, vf=fitvf)):
            f = comp.unpack(buf, CANVAS_W, CANVAS_H)
            x0, y0, x1, y1 = (int(c) for c in rects[i])
            peak[i] = int(f[0][y0:y1, x0:x1].max())
            noise = None
            if noise_sigma > 0:
                noise = np.clip(np.rint(pad_rng.normal(0, noise_sigma, (CANVAS_H, CANVAS_W))), -4, 4).astype(np.int16)
            vw.write(comp.pack(comp.paint_rect_outside(f, (x0, y0, x1, y1), black, noise)))
            last = i
    if last + 1 != n:
        raise RuntimeError(f"wrote {last + 1} of {n} frames")
    return dict(out=out, n=n, rects=rects, cuts=cuts, peak=peak, black=black, noise=noise_sigma, sources=[src], note=(
        f"{n} frames of {src} from {t0:.2f}s; windows: scope, " + ("none, 4:3 pillarbox, " if nwin == 4 else "") + "alternating at real cuts; bars painted over real picture"))


def mixed(a, rng) -> dict:
    scope_bank = ["tos-1080p", "sintel-w1", "sintel-w2", "sintel-w3", "sintel-w4"]
    full_bank = ["meridian-4k", "hotel-aman-promo", "bbb-1080p30", "anime-nama-anaru-ep2"]
    n_exc = 60
    lens = [int(rng.integers(72, 150)) for _ in range(n_exc)]
    # alternate scope / full; the full pictures lean on Meridian (dark) so dark pictures sit next to bars
    exc_a = pick_excerpts(rng, scope_bank, n_exc // 2, lens[0::2], FPS_F)
    full_cycle = ["meridian-4k", "meridian-4k", "hotel-aman-promo", "bbb-1080p30", "anime-nama-anaru-ep2"]
    exc_b = pick_excerpts(rng, full_cycle, n_exc // 2, lens[1::2], FPS_F, gap=24)
    exc = [e for pair in zip(exc_a, exc_b) for e in pair]
    dips = set(int(i) for i in rng.choice(np.arange(1, n_exc), size=6, replace=False))     # joins that dip to black (12 frames)
    out = tc.CLIPS / "ctor-bars-mixed.mp4"
    rect_per_frame: list[tuple[int, int, int, int]] = []
    peak: list[int] = []
    shots: list[dict] = []
    cuts: list[int] = []
    used = []
    pos, tail, tail_rect = 0, None, None
    with tc.VideoWriter(out, CANVAS_W, CANVAS_H, FPS, codec="libx264", crf=14, preset="medium") as vw:
        for i, ex in enumerate(exc):
            info = clip_info(ex.clip)
            vf, rect = canvas_fit(info)
            vf = "" if vf == "null" else vf
            frames = [comp.unpack(b, CANVAS_W, CANVAS_H) for b in tc.read_excerpt_yuv420(tc_clip_file(ex.clip), ex.t_start, ex.frames, CANVAS_W, CANVAS_H, fps=FPS, vf=vf)]
            if len(frames) != ex.frames:
                raise RuntimeError(f"excerpt {ex.clip} gave {len(frames)} of {ex.frames}")
            d = 12 if i in dips else 0
            lead = 0
            if i > 0:
                if d == 0:
                    shots.append({"frame": pos, "kind": "hard", "span": None, "layout_change_point": True})
                    cuts.append(pos)
                else:
                    for j in range(d):
                        t = (j + 1) / (d + 1)
                        mf = comp.transition_frame("fadeblack", tail[j], frames[j], t)
                        rj = tail_rect if t < 0.5 else rect          # the rectangle switches at the darkest frame
                        vw.write(comp.pack(mf))
                        rect_per_frame.append(rj)
                        peak.append(int(mf[0][rj[1]:rj[3], rj[0]:rj[2]].max()))
                    shots.append({"frame": pos, "kind": "fade_black", "span": [pos, pos + d], "layout_change_point": True})
                    pos += d
                    lead = d
            body_end = len(frames) - (12 if (i + 1) in dips else 0)
            for f in frames[lead:body_end]:
                vw.write(comp.pack(f))
                rect_per_frame.append(rect)
                x0, y0, x1, y1 = rect
                peak.append(int(f[0][y0:y1, x0:x1].max()))
                pos += 1
            tail, tail_rect = frames[body_end:], rect
            used.append({"clip": ex.clip, "start_frame": ex.start_frame, "t_start": round(ex.t_start, 3), "frames": ex.frames, "rect": list(rect)})
    n = pos
    rects = np.array(rect_per_frame, np.int32)
    return dict(out=out, n=n, rects=rects, cuts=cuts, peak=np.array(peak, np.int32), black=(16, 128, 128), noise=0.0,
                sources=sorted({u["clip"] for u in used}), shots=shots, used=used,
                note="60 real excerpts: natural scope pictures (padded, exact bars) alternate with full 16:9 pictures; hard cuts and six dips to black")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("variant", choices=list(VARIANTS) + ["mixed"])
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    rng = np.random.default_rng(a.seed)
    r = mixed(a, rng) if a.variant == "mixed" else painted(a.variant, a, rng)
    cid = f"ctor-bars-{a.variant}"
    out, n, rects = r["out"], r["n"], r["rects"]
    facts = tc.describe_clip(out)
    if facts["video"]["frames"] != n:
        raise RuntimeError(f"wrote {n} frames, file has {facts['video']['frames']}")
    body = {"storage": {"w": CANVAS_W, "h": CANVAS_H}, "bar_level": {"y": r["black"][0], "noise_sigma": r["noise"]}, "segments": rle(rects),
            "ambiguous": ambiguous_ranges(r["peak"], r["black"][0]), "cuts": r["cuts"], "cuts_note": "real cuts the rectangle may change on (a subset of all cuts in the clip)",
            "excerpts": r.get("used")}
    basis = "constructed: " + r["note"] + "; rectangles are exact by construction"
    tc.write_labels(cid, "bars", body, status="exact", proposed_by="script", basis=basis, frame_count=n, fps=FPS)
    labels = {"bars": {"file": f"labels/{cid}/bars.json", "status": "exact", "items": len(body["segments"])}}
    tests = ["bars", "prescan"]
    if r.get("shots"):
        tc.write_labels(cid, "shots", {"boundaries": r["shots"], "not_cuts": [], "regions": []}, status="exact", proposed_by="script", basis=basis, frame_count=n, fps=FPS)
        labels["shots"] = {"file": f"labels/{cid}/shots.json", "status": "exact", "items": len(r["shots"])}
        tests.append("shots")
    man = tc.load_manifest()
    tc.upsert_clip(man, {"id": cid, "kind": "constructed", "domain": "anime" if a.variant.startswith("anime") else "live", "path": tc.rel_media(out), "tests": tests,
                         "recipe": {"script": "make-bars.py", "args": {"variant": a.variant, "seed": a.seed}}, "derived_from": r["sources"], "notes": basis,
                         "labels": labels, **facts})
    tc.save_json(tc.MANIFEST, man)
    amb = sum(b - a_ for a_, b in body["ambiguous"])
    print(f"{cid}: {n} frames, {len(body['segments'])} rectangle segments, {len(r['cuts'])} cuts used, {amb} ambiguous (black picture) frames")
    return 0


if __name__ == "__main__":
    sys.exit(main())
