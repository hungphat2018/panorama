"""Subtitle events per track: timing, anchor, rendered box, and a dialogue / sign / unknown class with its evidence.

Every subtitle stream is analysed (the viewer may switch tracks). Text tracks (ASS, SSA, SRT, mov_text, WebVTT ...) are extracted
with ffmpeg and parsed with pysubs2; their boxes come from libass itself: the track is rendered on a black canvas at 5 frames per
second through ffmpeg's `subtitles` filter and `bbox` finds the lit pixels (research section 2.5; 16 s per track for a
24-minute episode at full size, about a quarter of that on a half-size canvas). Bitmap tracks (PGS, DVD) are drawn onto the canvas
by ffmpeg's subtitle-to-video path and boxed the same way.

Times are seconds on the film's rebased timeline (container time minus the first video frame's time). Boxes are pixels of a
canvas of the video's stored size (the half-size render is scaled back up); the canvas size is in the track header.

Classes (v0 heuristics from the research's measurements of a streaming release: libass's own "explicit" test marked 0 of 480 events
although 61 were top-aligned titles or second speakers). Features are stored so the labelled subtitle set can tune the rules:
  sign      explicit placement (\\pos \\move \\clip \\iclip \\org \\pbo \\p, banner / scroll effect), or a standalone line that is top
            aligned or sits bottom-left / bottom-right (titles, credits, notes)
  dialogue  the track's main style at its usual place; a top-aligned line that overlaps a bottom line (second speaker moved up);
            every line of a plain SRT
  unknown   a style that differs from the main one without any placement evidence
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import numpy as np

from . import versions
from .proc import filter_quote_path, find_tool, popen, run_text
from .signals_store import write_json_atomic

RENDER_FPS = 5
_TAG_BLOCK = re.compile(r"\{[^}]*\}")
_EXPLICIT = re.compile(r"\\(pos|move|clip|iclip|org)\(|\\pbo|\\p[1-9]")
_AN = re.compile(r"\\an([1-9])")
_A_LEGACY = re.compile(r"\\a(\d{1,2})(?!\w)")
_SIGNISH_STYLE = re.compile(r"sign|title|op\b|ed\b|credit|staff|song|kara|romaji|note|logo|text", re.I)
TEXT_EXTRACT = {"ass": ("ass", "copy"), "ssa": ("ass", "ass"), "subrip": ("srt", "copy"), "srt": ("srt", "copy")}


def legacy_to_numpad(a: int) -> int:
    """SSA \\a alignment (1-3 bottom, 5-7 top, 9-11 middle) -> numpad \\an (1-9)."""
    base = {1: 1, 2: 2, 3: 3, 5: 7, 6: 8, 7: 9, 9: 4, 10: 5, 11: 6}
    return base.get(a, 2)


def event_alignment(text: str, style_alignment: int) -> int:
    m = _AN.search(text)
    if m:
        return int(m.group(1))
    m = _A_LEGACY.search("".join(_TAG_BLOCK.findall(text)))
    if m:
        return legacy_to_numpad(int(m.group(1)))
    return int(style_alignment) if style_alignment else 2


def plain_text(text: str) -> str:
    t = _TAG_BLOCK.sub("", text).replace("\\N", " ").replace("\\n", " ").replace("\\h", " ")
    return re.sub(r"\s+", " ", t).strip()


# --- extraction and rendering -------------------------------------------------------------------------------------------------

def extract_text_track(path: str, stream_index: int, codec: str, tmp: str) -> Optional[str]:
    """The track as a standalone .ass / .srt file with the container's own (absolute) times."""
    ext, mode = TEXT_EXTRACT.get(codec, ("srt", "srt"))
    out = os.path.join(tmp, f"track-{stream_index}.{ext}")
    codec_args = ["-c:s", "copy"] if mode == "copy" else ["-c:s", ext if ext != "srt" else "srt"]
    r = run_text([find_tool("ffmpeg"), "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-copyts", "-i", path, "-map",
                  f"0:{stream_index}", *codec_args, out], timeout=600)
    return out if r.returncode == 0 and os.path.isfile(out) and os.path.getsize(out) > 0 else None


def render_boxes(path: str, sub_ordinal: int, canvas: tuple, scale_back: float, duration_s: float, tmp: str, tag: str,
                 bitmap: bool = False) -> dict:
    # white text (ASS / SRT) is boxed at luma >= 128; bitmap captions may be grey or coloured, so anything clearly above the
    # canvas black (luma 16 after the conversion to gray) counts there
    """Boxes of the lit pixels of the rendered track at RENDER_FPS: {'t': times, 'box': [x1,y1,x2,y2] per sample or None}."""
    name = f"bbox-{tag}.txt"
    path = os.path.abspath(path)             # ffmpeg runs inside the temp folder
    cw, ch = canvas
    src = f"color=c=black:s={cw}x{ch}:r={RENDER_FPS}:d={duration_s:.3f}"
    if bitmap:
        cmd = [find_tool("ffmpeg"), "-hide_banner", "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i", src, "-canvas_size", f"{cw}x{ch}",
               "-i", path, "-filter_complex",
               f"[0:v][1:s:{sub_ordinal}]overlay=format=auto,format=gray,bbox=min_val={40 if bitmap else 128},metadata=print:file={name}", "-f", "null", "-"]
    else:
        vf = f"subtitles=filename={filter_quote_path(path)}:si={sub_ordinal},format=gray,bbox=min_val=128,metadata=print:file={name}"
        cmd = [find_tool("ffmpeg"), "-hide_banner", "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i", src, "-vf", vf, "-f", "null", "-"]
    r = subprocess.run(cmd, cwd=tmp, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       creationflags=(0x08000000 | 0x00004000) if os.name == "nt" else 0)
    meta = os.path.join(tmp, name)
    if r.returncode != 0 or not os.path.isfile(meta):
        raise RuntimeError(f"subtitle render failed: {r.stderr.strip()[-300:]}")
    with open(meta, encoding="utf-8", errors="replace") as f:
        txt = f.read()
    os.remove(meta)
    times, boxes = [], []
    for block in re.split(r"(?m)^frame:", txt)[1:]:
        t = re.search(r"pts_time:([0-9.]+)", block)
        if not t:
            continue
        g = lambda k: re.search(rf"lavfi\.bbox\.{k}=(-?\d+)", block)
        x1, x2, y1, y2 = g("x1"), g("x2"), g("y1"), g("y2")
        times.append(float(t.group(1)))
        boxes.append(None if not (x1 and x2 and y1 and y2) else
                     [int(round(int(x1.group(1)) * scale_back)), int(round(int(y1.group(1)) * scale_back)),
                      int(round((int(x2.group(1)) + 1) * scale_back)), int(round((int(y2.group(1)) + 1) * scale_back))])
    return {"t": np.asarray(times), "box": boxes}


def _union(boxes: list) -> Optional[list]:
    boxes = [b for b in boxes if b]
    if not boxes:
        return None
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


# --- classification ----------------------------------------------------------------------------------------------------------

def classify_events(events: list, main_style: str) -> None:
    """Adds 'class', 'confidence' to every event dict in place (needs 'alignment', 'explicit', 'overlaps', 'overlaps_bottom_dialogue')."""
    for e in events:
        f = e["features"]
        if f["explicit"]:
            e["class"], e["confidence"] = "sign", 0.9
        elif f["effect_banner"]:
            e["class"], e["confidence"] = "sign", 0.8
        elif f["alignment"] in (7, 8, 9) and f["overlaps_center_dialogue"]:
            e["class"], e["confidence"] = "dialogue", 0.6          # second speaker moved to the top
        elif f["alignment"] in (7, 8, 9):
            e["class"], e["confidence"] = "sign", 0.7              # title, credit, note standing alone at the top
        elif f["alignment"] in (1, 3) and not f["overlaps_center_dialogue"]:
            e["class"], e["confidence"] = "sign", 0.6              # credit at bottom left / right
        elif f["alignment"] in (1, 3):
            e["class"], e["confidence"] = "dialogue", 0.5
        elif f["alignment"] in (4, 5, 6):
            e["class"], e["confidence"] = "sign", 0.6
        elif f["style"] != main_style and _SIGNISH_STYLE.search(f["style"] or ""):
            e["class"], e["confidence"] = "sign", 0.6
        elif f["style"] != main_style:
            e["class"], e["confidence"] = "unknown", 0.4
        else:
            e["class"], e["confidence"] = "dialogue", 0.9 if f["has_style_info"] else 0.7


def analyze_text_track(path: str, stream: dict, sub_ordinal: int, origin_abs_s: float, video_wh: tuple, duration_abs_s: float,
                       tmp: str) -> dict:
    import pysubs2
    codec = stream.get("codec") or ""
    track_file = extract_text_track(path, stream["index"], codec, tmp)
    if track_file is None:
        raise RuntimeError("could not extract the track")
    subs = pysubs2.load(track_file, encoding="utf-8")
    raw = [e for e in subs if not e.is_comment and e.end > e.start]
    styles = subs.styles
    style_counts = Counter(e.style for e in raw)
    main_style = style_counts.most_common(1)[0][0] if style_counts else "Default"
    events = []
    for e in raw:
        st = styles.get(e.style)
        style_align = getattr(st, "alignment", 2) if st is not None else 2
        text = e.text or ""
        blocks = "".join(_TAG_BLOCK.findall(text))
        events.append({
            "start_s": e.start / 1000.0 - origin_abs_s, "end_s": e.end / 1000.0 - origin_abs_s, "text": plain_text(text)[:200],
            "features": {"style": e.style, "layer": int(e.layer), "alignment": event_alignment(text, style_align),
                         "style_alignment": int(style_align), "explicit": bool(_EXPLICIT.search(blocks)),
                         "effect_banner": bool(re.match(r"\s*(banner|scroll)", e.effect or "", re.I)), "effect": e.effect or "",
                         "margin_l": int(e.marginl), "margin_r": int(e.marginr), "margin_v": int(e.marginv),
                         "has_style_info": codec in ("ass", "ssa"), "overlaps": 0, "overlaps_bottom_dialogue": False,
                         "overlaps_center_dialogue": False}})
    events = [e for e in events if e["end_s"] > 0]
    events.sort(key=lambda e: (e["start_s"], e["end_s"]))
    # overlaps with other events
    starts = np.array([e["start_s"] for e in events])
    ends = np.array([e["end_s"] for e in events])
    for i, e in enumerate(events):
        ov = np.nonzero((starts < ends[i]) & (ends > starts[i]))[0]
        ov = ov[ov != i]
        e["features"]["overlaps"] = int(len(ov))
        e["features"]["overlaps_bottom_dialogue"] = bool(any(events[j]["features"]["alignment"] in (1, 2, 3) for j in ov))
        # a plain bottom-centre line shown at the same time: the other speaker's words, which a moved or corner line is not
        e["features"]["overlaps_center_dialogue"] = bool(any(events[j]["features"]["alignment"] == 2 and not events[j]["features"]["explicit"]
                                                            for j in ov))
    classify_events(events, main_style)
    # rendered boxes
    W, H = video_wh
    half = W >= 1920
    canvas = (W // 2 // 2 * 2, H // 2 // 2 * 2) if half else (W // 2 * 2, H // 2 * 2)
    scale_back = W / canvas[0]
    rendered = render_boxes(path, sub_ordinal, canvas, scale_back, max(1.0, duration_abs_s), tmp, f"{stream['index']}")
    t_abs = rendered["t"]
    for i, e in enumerate(events):
        a, b = e["start_s"] + origin_abs_s, e["end_s"] + origin_abs_s
        idx = np.nonzero((t_abs >= a) & (t_abs < b))[0]
        own = [k for k in idx if all((o is e) or not (o["start_s"] + origin_abs_s <= t_abs[k] < o["end_s"] + origin_abs_s)
                                     for o in events[max(0, i - 8): i + 9])]
        rect = _union([rendered["box"][k] for k in own])
        kind = "own"
        if rect is None and len(idx):
            rect = _union([rendered["box"][k] for k in idx])
            kind = "shared"
        e["rect_px"] = rect
        e["rect_kind"] = kind if rect else None
        e["rendered_samples"] = int(len(idx))
        if rect:
            e["rect_width_share"] = round((rect[2] - rect[0]) / W, 4)
            e["rect_center_x"] = round((rect[0] + rect[2]) / 2 / W, 4)
            e["rect_center_y"] = round((rect[1] + rect[3]) / 2 / H, 4)
    return _track_doc(stream, "text", codec, main_style, canvas, W, H, events, {"play_res": [subs.info.get("PlayResX"), subs.info.get("PlayResY")],
                                                                                "styles": sorted(style_counts)})


def _track_doc(stream: dict, kind: str, codec: str, main_style: Optional[str], canvas: tuple, W: int, H: int, events: list, extra: dict) -> dict:
    counts = Counter(e["class"] for e in events)
    out_events = []
    for e in events:
        out_events.append({k: v for k, v in e.items()})
    doc = {"schema": "panorama.content.subtitles/0", "version": versions.SUBTITLES, "track": {
        "stream_index": stream["index"], "kind": kind, "codec": codec, "language": stream.get("language"), "title": stream.get("title"),
        "default": stream.get("default"), "forced": stream.get("forced"), "hearing_impaired": stream.get("hearing_impaired")},
        "main_style": main_style, "canvas": {"width": W, "height": H, "render_width": canvas[0], "render_height": canvas[1],
                                             "render_fps": RENDER_FPS},
        "counts": {"events": len(events), **{k: int(v) for k, v in counts.items()}, "with_box": sum(1 for e in events if e.get("rect_px"))},
        **extra, "events": out_events}
    return doc


BITMAP_SHOW_MIN_BYTES = 100       # a PGS display set that draws a caption is kilobytes; the one that clears it is about 30 bytes


def analyze_bitmap_track(path: str, stream: dict, ordinal: int, origin_abs_s: float, video_wh: tuple, duration_abs_s: float,
                         tmp: str, stream_info: dict) -> dict:
    """PGS / DVD bitmap track: event times from the subtitle packets (a large display set shows a caption until the next packet,
    which clears it or replaces it), boxes from ffmpeg drawing the track on a black canvas the size of its own plane."""
    from .media_probe import stream_packets_with_size
    num, den = (int(x) for x in stream_info["time_base"].split("/"))
    tbf = num / den
    pk = [p for p in stream_packets_with_size(path, stream["index"]) if p[0] is not None]
    pk.sort(key=lambda p: p[0])
    W, H = video_wh
    plane_w = int(stream_info.get("width") or W)
    plane_h = int(stream_info.get("height") or H)
    events = []
    for i, (pts, dur, size, _flags) in enumerate(pk):
        if size < BITMAP_SHOW_MIN_BYTES:
            continue
        start_abs = pts * tbf
        if dur and dur > 0:
            end_abs = start_abs + dur * tbf
        elif i + 1 < len(pk):
            end_abs = pk[i + 1][0] * tbf
        else:
            end_abs = start_abs + 3.0
        if end_abs - origin_abs_s <= 0 or end_abs <= start_abs:
            continue
        events.append({"start_s": start_abs - origin_abs_s, "end_s": end_abs - origin_abs_s, "text": "",
                       "features": {"bitmap": True, "packet_bytes": int(size), "alignment": None, "explicit": False,
                                    "style": None, "has_style_info": False}})
    rendered = render_boxes(path, ordinal, (plane_w, plane_h), W / plane_w, max(1.0, duration_abs_s), tmp, f"{stream['index']}", bitmap=True)
    t_abs = rendered["t"]
    for e in events:
        a, b = e["start_s"] + origin_abs_s, e["end_s"] + origin_abs_s
        idx = np.nonzero((t_abs >= a) & (t_abs < b))[0]
        rect = _union([rendered["box"][k] for k in idx])
        e["rect_px"], e["rect_kind"], e["rendered_samples"] = rect, ("own" if rect else None), int(len(idx))
        if rect:
            e["rect_width_share"] = round((rect[2] - rect[0]) / W, 4)
            e["rect_center_x"] = round((rect[0] + rect[2]) / 2 / W, 4)
            e["rect_center_y"] = round((rect[1] + rect[3]) / 2 / H, 4)
            # no style information: a caption in the lower part of the picture is dialogue, one elsewhere is likely a sign / title
            low = e["rect_center_y"] >= 0.6
            e["class"], e["confidence"] = ("dialogue", 0.6) if low else ("sign", 0.4)
        else:
            e["class"], e["confidence"] = "unknown", 0.2
    return _track_doc(stream, "bitmap", stream.get("codec") or "", None, (plane_w, plane_h), W, H, events, {"plane": [plane_w, plane_h]})


def run_all(ctx, folder: str, threads: int = 3, log=None) -> dict:
    """Analyse every subtitle stream; write subtitles/track-<index>.json. Returns {'tracks': [...], 'seconds': float}."""
    import time as _time
    t0 = _time.perf_counter()
    streams = [s for s in ctx.facts["streams"] if s["type"] == "subtitle"]
    stream_info = {s["index"]: s for s in ctx.info.get("streams", [])}
    os.makedirs(os.path.join(folder, "subtitles"), exist_ok=True)
    origin = ctx.table.origin_seconds_abs()
    duration_abs = origin + ctx.table.duration_s() + 1.0
    W, H = ctx.width, ctx.height
    results = []
    lock = threading.Lock()

    def one(args):
        ordinal, st = args
        tmp = tempfile.mkdtemp(prefix=f"sub{st['index']}-", dir=folder)
        try:
            if st.get("subtitle_kind") == "text":
                doc = analyze_text_track(ctx.path, st, ordinal, origin, (W, H), duration_abs, tmp)
            elif st.get("subtitle_kind") == "bitmap":
                doc = analyze_bitmap_track(ctx.path, st, ordinal, origin, (W, H), duration_abs, tmp, stream_info[st["index"]])
            else:
                return {"stream_index": st["index"], "codec": st["codec"], "status": "skipped", "reason": "unknown subtitle kind"}
            name = f"track-{st['index']}.json"
            write_json_atomic(os.path.join(folder, "subtitles", name), doc, indent=None)
            return {"stream_index": st["index"], "codec": st["codec"], "language": st.get("language"), "status": "ok",
                    "file": f"subtitles/{name}", "events": doc["counts"]["events"], "counts": doc["counts"]}
        except Exception as e:  # noqa: BLE001 - one bad track must not stop the others
            return {"stream_index": st["index"], "codec": st.get("codec"), "status": "failed", "error": f"{type(e).__name__}: {e}"[:300]}
        finally:
            for f in os.listdir(tmp):
                try:
                    os.remove(os.path.join(tmp, f))
                except OSError:
                    pass
            try:
                os.rmdir(tmp)
            except OSError:
                pass

    with ThreadPoolExecutor(max_workers=threads) as pool:
        for r in pool.map(one, list(enumerate(streams))):
            with lock:
                results.append(r)
            if log:
                log(f"subtitle track {r['stream_index']} ({r.get('codec')}): {r['status']}" + (f", {r.get('events')} events" if r["status"] == "ok" else f" {r.get('error', r.get('reason', ''))}"))
    return {"tracks": results, "seconds": round(_time.perf_counter() - t0, 2)}
