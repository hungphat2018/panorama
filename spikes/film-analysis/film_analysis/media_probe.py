"""ffprobe wrappers: container facts, the video packet list, side data of the first frames."""
from __future__ import annotations

import json
from fractions import Fraction
from typing import Optional

from .proc import find_tool, run_text


class ProbeError(RuntimeError):
    pass


def probe_container(path: str) -> dict:
    """Streams, format and chapters as ffprobe reports them (parsed JSON)."""
    r = run_text([find_tool("ffprobe"), "-v", "error", "-show_format", "-show_streams", "-show_chapters",
                  "-show_entries", "stream_side_data", "-of", "json", path])
    if r.returncode != 0 or not r.stdout.strip():
        raise ProbeError(f"ffprobe could not read {path!r}: {r.stderr.strip()[:300]}")
    return json.loads(r.stdout)


def parse_rational(text: Optional[str]) -> Optional[Fraction]:
    """'24000/1001' -> Fraction; '0/0', 'N/A' and None -> None."""
    if not text or text == "N/A":
        return None
    try:
        f = Fraction(text)
    except (ValueError, ZeroDivisionError):
        return None
    return f if f != 0 else None


def pick_video_stream(info: dict) -> dict:
    """The video stream the performer decodes. av_find_best_stream skips attached pictures and prefers, in order, the stream
    flagged default (not hearing/visual impaired), then the higher bit rate; ties keep the first."""
    best = None
    best_key = None
    for st in info.get("streams", []):
        if st.get("codec_type") != "video":
            continue
        disp = st.get("disposition", {})
        if disp.get("attached_pic"):
            continue
        score = (0 if (disp.get("hearing_impaired") or disp.get("visual_impaired")) else 1) + int(disp.get("default", 0))
        try:
            rate = int(st.get("bit_rate", 0))
        except (TypeError, ValueError):
            rate = 0
        key = (score, rate)
        if best is None or key > best_key:
            best, best_key = st, key
    if best is None:
        raise ProbeError("no video stream")
    return best


def video_packets(path: str, stream_index: int) -> list:
    """Packets of one stream in the order the demuxer returns them: list of (pts, dts, duration, flags).
    pts/dts/duration are ints in the stream's time base, or None where ffprobe prints N/A. The flags string has K for a
    keyframe, D for a packet the demuxer marks to be discarded (edit lists), C for corrupt."""
    r = run_text([find_tool("ffprobe"), "-v", "error", "-select_streams", str(stream_index), "-show_entries",
                  "packet=pts,dts,duration,flags", "-of", "csv=p=0", path])
    if r.returncode != 0:
        raise ProbeError(f"ffprobe packet scan failed for {path!r}: {r.stderr.strip()[:300]}")

    def num(token: str):
        return None if token in ("N/A", "") else int(token)

    out = []
    for line in r.stdout.splitlines():
        parts = line.strip().split(",")
        if len(parts) < 4:
            continue
        out.append((num(parts[0]), num(parts[1]), num(parts[2]), parts[3]))
    return out


def frame_side_data(path: str, stream_index: int, count: int = 1, skip_nonkey: bool = False,
                    start_seconds: Optional[float] = None) -> list:
    """Decoded frames' side data (HDR metadata, Dolby Vision RPU, display matrix) for the first `count` frames, as ffprobe's
    parsed JSON 'frames' list. With skip_nonkey only keyframes are decoded (cheap)."""
    args = [find_tool("ffprobe"), "-v", "error", "-select_streams", str(stream_index)]
    if skip_nonkey:
        args += ["-skip_frame", "nokey"]
    interval = f"{start_seconds:.3f}%+#{count}" if start_seconds is not None else f"%+#{count}"
    args += ["-read_intervals", interval, "-show_frames", "-show_entries",
             "frame=pts,pts_time,key_frame,pict_type,side_data_list", "-of", "json", path]
    r = run_text(args, timeout=300)
    if r.returncode != 0 or not r.stdout.strip():
        raise ProbeError(f"ffprobe frame scan failed for {path!r}: {r.stderr.strip()[:300]}")
    return json.loads(r.stdout).get("frames", [])


def stream_packets_with_size(path: str, stream_index: int) -> list:
    """Packets of any stream: (pts, duration, size, flags), pts and duration in ticks (None for N/A). Used for bitmap subtitles,
    where a display set that draws a caption is large and one that clears it is tiny."""
    r = run_text([find_tool("ffprobe"), "-v", "error", "-select_streams", str(stream_index), "-show_entries",
                  "packet=pts,duration,size,flags", "-of", "csv=p=0", path])
    if r.returncode != 0:
        raise ProbeError(f"ffprobe packet scan failed for {path!r}: {r.stderr.strip()[:300]}")
    out = []
    for line in r.stdout.splitlines():
        parts = line.strip().split(",")
        if len(parts) < 4:
            continue
        pts = None if parts[0] in ("N/A", "") else int(parts[0])
        dur = None if parts[1] in ("N/A", "") else int(parts[1])
        out.append((pts, dur, int(parts[2]) if parts[2].isdigit() else 0, parts[3]))
    return out
