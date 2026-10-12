"""Constructed clips with exact shot truth: known transitions, hard cuts, and 100 flashes that are not cuts.

Usage: python -I make-transitions.py transitions|flashes [--seed N] [--bank CLIP_ID ...]

Both modes cut short stretches out of the real clips (stretches in which no detector fired anywhere near, see testset_bank.py),
fit them on a 1920x1080 canvas (a scope picture keeps its bars, as in a letterboxed release), put them on a 24000/1001 fps timeline
and join or decorate them in numpy, so every boundary and every flash has an exact frame number:
  transitions: 60 joins: 22 hard cuts, cross-dissolves of 1-36 frames, dips to black / white / gray, wipes, slides, circles;
  flashes: 100 excerpts of 60 frames, each with one flash (full white 1-3 frames, partial brightness, orange, a 4-frame ramp, a local
           blob, a double strobe); the 99 joins between excerpts are hard cuts.
Writes tools/testset/clips/ctor-<mode>.mp4 (H.264, CRF 14), labels/ctor-<mode>/shots.json (status exact), and the manifest entry.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import testset_common as tc  # noqa: E402
import testset_compose as comp  # noqa: E402
from testset_bank import CANVAS_H, CANVAS_W, canvas_fit, clip_info, pick_excerpts, tc_clip_file  # noqa: E402

FPS = "24000/1001"
FPS_F = 24000 / 1001
PURE_MIN = 16           # unmixed frames kept before and after every transition


def transition_plan(rng: np.random.Generator) -> list[tuple[str, int]]:
    plan = [("hard", 0)] * 22
    plan += [("fade", d) for d in (2, 4, 6, 8, 12, 16, 24, 36) for _ in range(2)]
    plan += [("fadeblack", d) for d in (12, 24, 36) for _ in range(2)]
    plan += [("fadewhite", d) for d in (12, 24) for _ in range(2)]
    plan += [("wipeleft", 12), ("wiperight", 8), ("wipeup", 12), ("wipedown", 8), ("circleopen", 12), ("circleclose", 12), ("slideleft", 8), ("slideright", 12)]
    plan += [("fadegrays", 24), ("fade", 1), ("fade", 1), ("fade", 3)]
    order = rng.permutation(len(plan))
    return [plan[i] for i in order]


def flash_plan(rng: np.random.Generator) -> list[str]:
    kinds = (["white1"] * 18 + ["white2"] * 18 + ["white3"] * 14 + ["bright2"] * 14 + ["warm2"] * 10 + ["ramp4"] * 8 + ["local3"] * 10 + ["strobe"] * 8)
    return [kinds[i] for i in rng.permutation(len(kinds))]


def load_excerpt(ex, vf: str) -> list:
    got = [comp.unpack(b, CANVAS_W, CANVAS_H) for b in tc.read_excerpt_yuv420(tc_clip_file(ex.clip), ex.t_start, ex.frames, CANVAS_W, CANVAS_H, fps=FPS, vf=vf)]
    if len(got) != ex.frames:
        raise RuntimeError(f"excerpt of {ex.clip} at {ex.t_start:.2f}s gave {len(got)} of {ex.frames} frames")
    return got


def fit_filter(clip_id: str) -> str:
    vf, _ = canvas_fit(clip_info(clip_id))
    return "" if vf == "null" else vf


def build_transitions(rng, bank: list[str], out: Path) -> tuple[list[dict], int, list[dict]]:
    plan = transition_plan(rng)
    d_in = [0] + [d for _, d in plan]
    d_out = [d for _, d in plan] + [0]
    lens = [2 * PURE_MIN + a + b + int(rng.integers(0, 17)) for a, b in zip(d_in, d_out)]
    exc = pick_excerpts(rng, bank, len(lens), lens, FPS_F)
    boundaries, used = [], []
    pos, tail = 0, None
    with tc.VideoWriter(out, CANVAS_W, CANVAS_H, FPS, codec="libx264", crf=14, preset="medium") as vw:
        for i, (ex, n) in enumerate(zip(exc, lens)):
            frames = load_excerpt(ex, fit_filter(ex.clip))
            used.append({"clip": ex.clip, "start_frame": ex.start_frame, "t_start": round(ex.t_start, 3), "frames": n})
            if i > 0:
                kind, d = plan[i - 1]
                if d == 0:
                    boundaries.append({"frame": pos, "kind": "hard", "span": None, "layout_change_point": True, "transition": "hard", "duration": 0})
                else:
                    for j in range(d):
                        vw.write(comp.pack(comp.transition_frame(kind, tail[j], frames[j], (j + 1) / (d + 1))))
                    boundaries.append({"frame": pos, "kind": comp.TRANSITION_KIND[kind], "span": [pos, pos + d], "layout_change_point": True,
                                       "transition": kind, "duration": d})
                    pos += d
            for f in frames[d_in[i]: n - d_out[i]]:
                vw.write(comp.pack(f))
                pos += 1
            tail = frames[n - d_out[i]:]
    return boundaries, pos, used


def build_flashes(rng, bank: list[str], out: Path, n_exc: int = 100, length: int = 60) -> tuple[list[dict], list[dict], int, list[dict]]:
    kinds = flash_plan(rng)
    exc = pick_excerpts(rng, bank, n_exc, length, FPS_F)
    boundaries, flashes, used = [], [], []
    pos = 0
    with tc.VideoWriter(out, CANVAS_W, CANVAS_H, FPS, codec="libx264", crf=14, preset="medium") as vw:
        for i, (ex, kind) in enumerate(zip(exc, kinds)):
            frames = load_excerpt(ex, fit_filter(ex.clip))
            if i > 0:
                boundaries.append({"frame": pos, "kind": "hard", "span": None, "layout_change_point": True, "transition": "hard", "duration": 0})
            at = int(rng.integers(14, 38))
            k = comp.FLASH_KINDS[kind]
            frames[at: at + k] = comp.flash_frames(kind, frames[at: at + k], rng)
            flashes.append({"span": [pos + at, pos + at + k], "kind": f"flash-{kind}", "note": "inside one shot; not a cut"})
            used.append({"clip": ex.clip, "start_frame": ex.start_frame, "t_start": round(ex.t_start, 3), "frames": length, "flash": kind, "flash_at": pos + at})
            for f in frames:
                vw.write(comp.pack(f))
            pos += length
    return boundaries, flashes, pos, used


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["transitions", "flashes"])
    ap.add_argument("--seed", type=int, default=12)
    ap.add_argument("--bank", nargs="*", default=["anime-nama-anaru-ep2", "hotel-aman-promo", "tos-1080p", "meridian-4k", "bbb-1080p30",
                                                  "sintel-w1", "sintel-w2", "sintel-w3", "sintel-w4"])
    a = ap.parse_args()
    rng = np.random.default_rng(a.seed)
    cid = f"ctor-{a.mode}"
    out = tc.CLIPS / f"{cid}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    flashes: list[dict] = []
    if a.mode == "transitions":
        boundaries, n, used = build_transitions(rng, a.bank, out)
        basis = "constructed: real excerpts joined with known transitions; every boundary and span is exact by construction"
    else:
        boundaries, flashes, n, used = build_flashes(rng, a.bank, out)
        basis = "constructed: 100 flashes inside real excerpts (not cuts) and the 99 hard cuts between the excerpts; exact by construction"
    facts = tc.describe_clip(out)
    if facts["video"]["frames"] != n:
        raise RuntimeError(f"wrote {n} frames but the file has {facts['video']['frames']}")
    tc.write_labels(cid, "shots", {"boundaries": boundaries, "not_cuts": flashes, "regions": [], "excerpts": used},
                    status="exact", proposed_by="script", basis=basis, frame_count=n, fps=FPS)
    man = tc.load_manifest()
    derived = sorted({u["clip"] for u in used})
    tc.upsert_clip(man, {"id": cid, "kind": "constructed", "domain": "mixed", "path": tc.rel_media(out), "tests": ["shots"],
                         "recipe": {"script": "make-transitions.py", "args": {"mode": a.mode, "seed": a.seed, "bank": a.bank}},
                         "derived_from": derived, "notes": basis,
                         "labels": {"shots": {"file": f"labels/{cid}/shots.json", "status": "exact", "items": len(boundaries) + len(flashes)}},
                         **facts})
    tc.save_json(tc.MANIFEST, man)
    print(f"{cid}: {n} frames, {len(boundaries)} boundaries, {len(flashes)} flashes, from {derived}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
