"""Constructed clips with text burned into the black bars, with exact truth boxes (libass renders the same subtitles on black).

Usage: python -I make-hardsub.py VARIANT

  anime-72, anime-36   first 3,600 frames of the anime episode with scope bars painted (138 px) and the French ASS track of the episode
                       burned in with libass at font size 72 / 36 (the research test, rebuilt; bars exact).
  tos-48               150 s of Tears of Steel (24 fps film frame padded onto a 16:9 canvas: bars 140 px, exact) with the English SRT
                       (shifted to the window) burned in at font size 48; two-line titles can reach into the picture.
  tos-logo             60 s of the same film with a translucent station logo in the top bar, a running timecode in the bottom bar and
                       small English subtitles: three kinds of text in the bars.

Truth: labels/<clip>/text_in_bars.json lists, per frame range, the box (x0, y0, x1, y1, half-open) of every piece of text or logo
(pixels of luma >= 48 when drawn on black), and which bar(s) it touches is worked out by the metric from labels/<clip>/bars.json.
"""
from __future__ import annotations

import argparse
import re
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import testset_common as tc  # noqa: E402
import testset_compose as comp  # noqa: E402
from testset_bank import CANVAS_H, CANVAS_W, canvas_fit, clip_info, tc_clip_file  # noqa: E402

FPS = "24000/1001"
FPS_F = 24000 / 1001
SCOPE = (0, 138, 1920, 942)
WORK = tc.MEDIA / "work"


def esc(p) -> str:
    return str(p).replace("\\", "/").replace(":", "\\:")


def extract_ass(clip_id: str, index: int, out: Path) -> Path:
    tc.run(["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-i", tc_clip_file(clip_id), "-map", f"0:{index}", "-c", "copy", out])
    return out


def shifted_srt(src: Path, shift_s: float, out: Path) -> Path:
    import pysubs2
    subs = pysubs2.load(str(src), encoding="utf-8")
    subs.shift(s=-shift_s)
    subs.events = [e for e in subs.events if e.end > 0]
    for e in subs.events:
        e.start = max(e.start, 0)
    subs.save(str(out), format_="srt")
    return out


def render_boxes(sub_file: Path, style: str, n: int) -> np.ndarray:
    """[n,4] half-open boxes (x0,y0,x1,y1) of what libass draws on black per frame, -1 where nothing is drawn."""
    with tempfile.TemporaryDirectory() as td:
        meta = Path(td) / "bbox.txt"
        fs = f"color=c=black:s={CANVAS_W}x{CANVAS_H}:r={FPS}:d={n / FPS_F + 1:.3f},subtitles=filename='{esc(sub_file)}'" + (f":force_style='{style}'" if style else "")
        fs += f",format=gray,bbox=min_val=48,metadata=print:file='{esc(meta)}'"
        tc.run(["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-filter_complex", fs, "-frames:v", str(n), "-f", "null", "-"])
        txt = meta.read_text()
    out = np.full((n, 4), -1, np.int32)
    for b in re.split(r"(?m)^frame:", txt)[1:]:
        k = int(re.match(r"(\d+)", b).group(1))
        vals = {key: re.search(rf"lavfi\.bbox\.{key}=(-?\d+)", b) for key in ("x1", "y1", "x2", "y2")}
        if k < n and all(vals.values()):
            out[k] = (int(vals["x1"].group(1)), int(vals["y1"].group(1)), int(vals["x2"].group(1)) + 1, int(vals["y2"].group(1)) + 1)
    return out


def boxes_to_events(boxes: np.ndarray, kind: str) -> list[dict]:
    ev, i, n = [], 0, len(boxes)
    while i < n:
        if boxes[i][0] < 0:
            i += 1
            continue
        j = i
        while j + 1 < n and np.array_equal(boxes[j + 1], boxes[i]):
            j += 1
        ev.append({"frames": [i, j + 1], "box": [int(v) for v in boxes[i]], "kind": kind})
        i = j + 1
    return ev


def draw_logo(f, rng_state: dict) -> tuple:
    """Translucent logo in the top bar: rounded plate with 'PANO TV' text (alpha 0.7). Returns (frame, box)."""
    y = f[0].copy()
    plate = np.zeros_like(y)
    cv2.rectangle(plate, (70, 24), (330, 104), 255, -1)
    cv2.putText(plate, "PANO TV", (92, 80), cv2.FONT_HERSHEY_DUPLEX, 1.5, 0, 3, cv2.LINE_AA)
    a = (plate.astype(np.float32) / 255.0) * 0.7
    y2 = np.clip(y.astype(np.float32) * (1 - a) + 235.0 * a + 0.5, 0, 255).astype(np.uint8)
    ys, xs = np.nonzero(plate > 0)
    return (y2, f[1], f[2]), (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)


def draw_timecode(f, frame_no: int) -> tuple:
    """Running timecode HH:MM:SS:FF in the bottom bar, left (white, Y=235). Fixed-width so its box is constant."""
    tcs = f"{frame_no // 86400:02d}:{frame_no // 1440 % 60:02d}:{frame_no // 24 % 60:02d}:{frame_no % 24:02d}"
    y = f[0].copy()
    cv2.putText(y, tcs, (70, 1040), cv2.FONT_HERSHEY_SIMPLEX, 1.2, 235, 2, cv2.LINE_AA)
    return (y, f[1], f[2])


def build(variant: str) -> dict:
    WORK.mkdir(parents=True, exist_ok=True)
    if variant.startswith("anime"):
        size = int(variant.split("-")[1])
        src, n = "anime-nama-anaru-ep2", 3600
        path = tc_clip_file(src)
        ass = extract_ass(src, 3, WORK / "judas-fr.ass")
        style, sub = f"FontSize={size}", ass
        t0, rect, fps_arg, vf = 0.0, SCOPE, None, ""
        paint = True
        extras = False
    else:
        src = "tos-1080p"
        path = tc_clip_file(src)
        info = clip_info(src)
        vf, rect = canvas_fit(info)
        paint, fps_arg = False, FPS
        if variant == "tos-48":
            t0, n, size, extras = 100.0, 3600, 48, False
        else:
            t0, n, size, extras = 150.0, 1440, 28, True
        style = f"FontSize={size},Outline=2,Shadow=0"
        sub = shifted_srt(tc.DOWNLOADS / "tears-of-steel-subtitles" / "TOS-en.srt", t0, WORK / f"tos-en-{variant}.srt")
    vf = "" if vf == "null" else vf
    cid = f"ctor-hardsub-{variant}"
    out = tc.CLIPS / f"{cid}.mp4"
    if variant.startswith("anime"):
        out = tc.CLIPS / f"{cid}.mkv"
    enc = dict(codec="libx265", crf=18, preset="fast", pix_fmt="yuv420p10le") if variant.startswith("anime") else dict(codec="libx264", crf=14, preset="medium")
    vsub = f"subtitles=filename='{esc(sub)}':force_style='{style}'"
    logo_box, tc_box = None, None
    with tc.VideoWriter(out, CANVAS_W, CANVAS_H, FPS, vf=vsub, **enc) as vw:
        count = 0
        for i, buf in enumerate(tc.read_excerpt_yuv420(path, t0, n, CANVAS_W, CANVAS_H, fps=fps_arg, vf=vf)):
            f = comp.unpack(buf, CANVAS_W, CANVAS_H)
            if paint:
                f = comp.paint_rect_outside(f, rect)
            if extras:
                f, logo_box = draw_logo(f, {})
                f = draw_timecode(f, i)
            vw.write(comp.pack(f))
            count += 1
    if count != n:
        raise RuntimeError(f"wrote {count} of {n} frames")
    boxes = render_boxes(sub, style, n)
    events = boxes_to_events(boxes, "subtitle")
    if extras:
        events.append({"frames": [0, n], "box": list(logo_box), "kind": "logo"})
        # the timecode box is whatever the digits cover: measure it once on a drawn black frame (all digits 8 give the widest extent)
        probe = comp.solid(CANVAS_W, CANVAS_H)
        y = probe[0].copy()
        cv2.putText(y, "88:88:88:88", (70, 1040), cv2.FONT_HERSHEY_SIMPLEX, 1.2, 235, 2, cv2.LINE_AA)
        ys, xs = np.nonzero(y > 100)
        events.append({"frames": [0, n], "box": [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1], "kind": "timecode",
                       "note": "union box of all digit shapes; the digits themselves change every frame"})
    return dict(cid=cid, out=out, n=n, rect=rect, events=events, src=src, style=style, sub=sub.name,
                text_frames=int((boxes[:, 0] >= 0).sum()), vf=vsub)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("variant", choices=["anime-72", "anime-36", "tos-48", "tos-logo"])
    a = ap.parse_args()
    r = build(a.variant)
    facts = tc.describe_clip(r["out"])
    if facts["video"]["frames"] != r["n"]:
        raise RuntimeError(f"wrote {r['n']} frames, file has {facts['video']['frames']}")
    basis = (f"constructed: {r['src']} with a {'painted' if r['src'].startswith('anime') else 'padded'} bar rectangle {list(r['rect'])}, subtitles burned in with libass "
             f"({r['sub']}, {r['style']}); truth boxes are libass's own rendering on black (luma >= 48)")
    tc.write_labels(r["cid"], "bars", {"storage": {"w": CANVAS_W, "h": CANVAS_H}, "bar_level": {"y": 16, "noise_sigma": 0.0},
                                        "segments": [{"frames": [0, r["n"]], "rect": list(r["rect"])}], "ambiguous": [], "cuts": []},
                    status="exact", proposed_by="script", basis=basis, frame_count=r["n"], fps=FPS)
    tc.write_labels(r["cid"], "text_in_bars", {"storage": {"w": CANVAS_W, "h": CANVAS_H}, "events": r["events"], "text_frames": r["text_frames"]},
                    status="exact", proposed_by="script", basis=basis, frame_count=r["n"], fps=FPS)
    man = tc.load_manifest()
    tc.upsert_clip(man, {"id": r["cid"], "kind": "constructed", "domain": "anime" if r["src"].startswith("anime") else "live", "path": tc.rel_media(r["out"]),
                         "tests": ["bars", "text_in_bars"], "recipe": {"script": "make-hardsub.py", "args": {"variant": a.variant}}, "derived_from": [r["src"]],
                         "notes": basis, "labels": {"bars": {"file": f"labels/{r['cid']}/bars.json", "status": "exact", "items": 1},
                                                    "text_in_bars": {"file": f"labels/{r['cid']}/text_in_bars.json", "status": "exact", "items": len(r["events"])}},
                         **facts})
    tc.save_json(tc.MANIFEST, man)
    print(f"{r['cid']}: {r['n']} frames, {r['text_frames']} frames with subtitle text ({r['text_frames'] / r['n']:.0%}), {len(r['events'])} text events")
    return 0


if __name__ == "__main__":
    sys.exit(main())
