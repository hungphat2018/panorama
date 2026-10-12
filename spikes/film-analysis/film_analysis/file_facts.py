"""File facts: streams, time base, frame rate, colour, HDR and Dolby Vision, chapters. Everything here comes from the
container and the first decoded frame; nothing needs a scan."""
from __future__ import annotations

import re
from fractions import Fraction
from typing import Optional

from . import frame_table as ft
from .media_probe import parse_rational

TEXT_SUBTITLE_CODECS = {"ass", "ssa", "subrip", "srt", "mov_text", "webvtt", "text", "microdvd", "subviewer", "sami",
                        "realtext", "jacosub", "mpl2", "pjs", "stl", "vplayer", "ttml", "eia_608"}
BITMAP_SUBTITLE_CODECS = {"hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle", "xsub", "dvb_teletext"}

TIMELINE_RULE = (
    "t[i] = pts[i] * (tb_num / tb_den) - pts[0] * (tb_num / tb_den) in IEEE double; frame 0 is time 0; a shot that begins "
    "with frame a > 0 starts at (t[a-1] + t[a]) / 2, the first shot at 0; the table holds the frames the performer's "
    "decoder path presents, in presentation order, strictly increasing")


_CHROMA_NAMES = {"420": "4:2:0", "422": "4:2:2", "444": "4:4:4", "440": "4:4:0", "411": "4:1:1", "410": "4:1:0"}


def bit_depth_and_chroma(pix_fmt: str) -> tuple:
    """'yuv420p10le' -> (10, '4:2:0'); 'nv12' -> (8, '4:2:0'); 'p010le' -> (10, '4:2:0'); unknown formats -> (None, None)."""
    pf = pix_fmt or ""
    m = re.match(r"^yuvj?(\d{3})p(\d+)?(?:le|be)?$", pf)
    if m:
        return (int(m.group(2)) if m.group(2) else 8), _CHROMA_NAMES.get(m.group(1))
    m = re.match(r"^p(\d{3})(?:le|be)$", pf)
    if m:
        return int(m.group(1)), "4:2:0"
    if pf in ("nv12", "nv21"):
        return 8, "4:2:0"
    if pf in ("nv16", "nv24"):
        return 8, "4:2:2" if pf == "nv16" else "4:4:4"
    m = re.match(r"^gray(\d+)?(?:le|be)?$", pf)
    if m:
        return (int(m.group(1)) if m.group(1) else 8), "4:0:0"
    return None, None


def _side_data(stream_or_frame: dict, type_name: str) -> Optional[dict]:
    for sd in stream_or_frame.get("side_data_list", []) or []:
        if sd.get("side_data_type") == type_name:
            return sd
    return None


def _frac_text_to_float(text: str) -> Optional[float]:
    try:
        return float(Fraction(text))
    except (ValueError, ZeroDivisionError, TypeError):
        return None


def hdr_facts(vstream: dict, first_frame: Optional[dict]) -> dict:
    """Kind of dynamic range and the static metadata: stream-level side data first, then the first decoded frame's."""
    transfer = vstream.get("color_transfer")
    primaries = vstream.get("color_primaries")
    dovi_conf = _side_data(vstream, "DOVI configuration record")
    frame_sd = (first_frame or {})
    mastering = _side_data(vstream, "Mastering display metadata") or _side_data(frame_sd, "Mastering display metadata")
    light = _side_data(vstream, "Content light level metadata") or _side_data(frame_sd, "Content light level metadata")
    hdr10plus = _side_data(frame_sd, "HDR Dynamic Metadata SMPTE2094-40 (HDR10+)") is not None
    if dovi_conf:
        kind = "dolby-vision"
    elif transfer == "smpte2084":
        kind = "hdr10plus" if hdr10plus else "hdr10"
    elif transfer == "arib-std-b67":
        kind = "hlg"
    else:
        kind = "sdr"
    out = {"kind": kind, "transfer": transfer, "primaries": primaries, "mastering_display": None,
           "max_content_light_level": None, "max_frame_average_light_level": None}
    if mastering:
        out["mastering_display"] = {
            "min_luminance_nits": _frac_text_to_float(mastering.get("min_luminance")),
            "max_luminance_nits": _frac_text_to_float(mastering.get("max_luminance")),
            "red": [_frac_text_to_float(mastering.get("red_x")), _frac_text_to_float(mastering.get("red_y"))],
            "green": [_frac_text_to_float(mastering.get("green_x")), _frac_text_to_float(mastering.get("green_y"))],
            "blue": [_frac_text_to_float(mastering.get("blue_x")), _frac_text_to_float(mastering.get("blue_y"))],
            "white": [_frac_text_to_float(mastering.get("white_point_x")), _frac_text_to_float(mastering.get("white_point_y"))],
        }
    if light:
        out["max_content_light_level"] = light.get("max_content")
        out["max_frame_average_light_level"] = light.get("max_average")
    return out


def dolby_vision_facts(vstream: dict, first_frame: Optional[dict]) -> Optional[dict]:
    conf = _side_data(vstream, "DOVI configuration record")
    if not conf:
        return None
    out = {"profile": conf.get("dv_profile"), "level": conf.get("dv_level"), "rpu_present": bool(conf.get("rpu_present_flag")),
           "enhancement_layer_present": bool(conf.get("el_present_flag")), "base_layer_present": bool(conf.get("bl_present_flag")),
           "bl_signal_compatibility_id": conf.get("dv_bl_signal_compatibility_id"),
           "first_frame_rpu": None, "level5_active_area": None, "level5_note": (
               "Not sampled in v0: FFmpeg delivers level 5 (active area) and level 1 (min/avg/max PQ) blocks only as frame side "
               "data through the libavutil C API (av_dovi_find_level), and ffprobe and showinfo print scene_refresh_flag but not "
               "the blocks. native-checks/dovi-probe.c reads them (software decode); the worker does not run it, so the crop "
               "comes from the picture itself. scene_refresh_flag is stored per frame (signal dv_scene_refresh).")}
    meta = _side_data(first_frame or {}, "Dolby Vision Metadata")
    if meta:
        out["first_frame_rpu"] = {"scene_refresh_flag": meta.get("scene_refresh_flag"), "dm_metadata_id": meta.get("dm_metadata_id"),
                                  "source_min_pq": meta.get("source_min_pq"), "source_max_pq": meta.get("source_max_pq"),
                                  "signal_eotf": meta.get("signal_eotf"), "vdr_rpu_profile": meta.get("vdr_rpu_profile")}
    return out


def stream_summaries(info: dict) -> list:
    out = []
    for st in info.get("streams", []):
        tags = st.get("tags", {}) or {}
        disp = st.get("disposition", {}) or {}
        item = {"index": st.get("index"), "type": st.get("codec_type"), "codec": st.get("codec_name"),
                "language": tags.get("language"), "title": tags.get("title"),
                "default": bool(disp.get("default")), "forced": bool(disp.get("forced")),
                "hearing_impaired": bool(disp.get("hearing_impaired")), "attached_pic": bool(disp.get("attached_pic"))}
        if st.get("codec_type") == "subtitle":
            codec = st.get("codec_name") or ""
            item["subtitle_kind"] = "text" if codec in TEXT_SUBTITLE_CODECS else (
                "bitmap" if codec in BITMAP_SUBTITLE_CODECS else "unknown")
        if st.get("codec_type") == "audio":
            item.update({"channels": st.get("channels"), "sample_rate": st.get("sample_rate")})
        out.append(item)
    return out


_CHAPTER_KINDS = [("op", re.compile(r"\b(op|opening|intro)\b", re.I)), ("ed", re.compile(r"\b(ed|ending|outro)\b", re.I)),
                  ("credits", re.compile(r"credit", re.I)), ("preview", re.compile(r"preview|next episode|予告", re.I))]


def chapter_summaries(info: dict, origin_abs_s: float) -> list:
    """Chapters on the rebased timeline (container time minus the first frame's time) with a guess at their kind."""
    out = []
    for ch in info.get("chapters", []) or []:
        title = (ch.get("tags") or {}).get("title", "")
        try:
            start, end = float(ch["start_time"]) - origin_abs_s, float(ch["end_time"]) - origin_abs_s
        except (KeyError, ValueError):
            continue
        kind = next((k for k, rx in _CHAPTER_KINDS if rx.search(title)), "normal")
        out.append({"start_s": start, "end_s": end, "title": title, "kind_guess": kind})
    return out


def build_file_facts(path_name: str, key: dict, info: dict, vstream: dict, table: ft.FrameTable, first_frame: Optional[dict],
                     decode_path: str, analyzer_versions: dict) -> dict:
    pix_fmt = vstream.get("pix_fmt", "")
    depth, chroma = bit_depth_and_chroma(pix_fmt)
    sar = parse_rational(vstream.get("sample_aspect_ratio")) or Fraction(1, 1)
    rotation = 0
    dm = _side_data(vstream, "Display Matrix")
    if dm and "rotation" in dm:
        rotation = int(round(float(dm["rotation"])))
    width, height = int(vstream["width"]), int(vstream["height"])
    fmt = info.get("format", {})
    rate = ft.frame_rate_facts(table, parse_rational(vstream.get("avg_frame_rate")), parse_rational(vstream.get("r_frame_rate")))
    origin = table.origin_seconds_abs()
    hdr = hdr_facts(vstream, first_frame)
    return {
        "schema": "panorama.content.file-facts/0",
        "file": {"key": key, "name": path_name, "container": fmt.get("format_name"), "container_duration_s": _to_float(fmt.get("duration")),
                 "bit_rate": _to_int(fmt.get("bit_rate"))},
        "video": {
            "stream_index": int(vstream["index"]), "codec": vstream.get("codec_name"), "profile": vstream.get("profile"),
            "level": vstream.get("level"), "pix_fmt": pix_fmt, "bit_depth": depth, "chroma": chroma,
            "width": width, "height": height, "coded_width": vstream.get("coded_width"), "coded_height": vstream.get("coded_height"),
            "sample_aspect_ratio": [sar.numerator, sar.denominator],
            "display_aspect_ratio": vstream.get("display_aspect_ratio"),
            "rotation_deg": rotation, "field_order": vstream.get("field_order", "unknown"),
            "color_range": vstream.get("color_range"), "color_space": vstream.get("color_space"),
            "chroma_location": vstream.get("chroma_location"), "bit_rate": _to_int(vstream.get("bit_rate")),
            "hdr": hdr, "dolby_vision": dolby_vision_facts(vstream, first_frame),
        },
        "timeline": {
            "kind": "linear", "rule": TIMELINE_RULE,
            "time_base": [table.tb_num, table.tb_den], "first_pts_ticks": int(table.pts[0]), "first_pts_s_abs": origin,
            "frames": table.n, "duration_s": table.duration_s(), "last_frame_duration_s": table.last_frame_duration_s(),
            "last_duration_ticks": table.last_duration_ticks,
            "frame_rate": rate, "frame_table_source": table.source, "frame_table_stats": table.stats,
            "keyframes": int(table.key.sum()),
            "notes": ["MKV ordered chapters and segment links are not followed (FFmpeg ignores them); the model describes the "
                      "file's own linear timeline"],
        },
        "streams": stream_summaries(info),
        "chapters": chapter_summaries(info, origin),
        "decode_path": decode_path,
        "analyzer_versions": analyzer_versions,
        "black_level": None, "crop_vocabulary": None, "text_in_bars": None,
    }


def _to_float(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_int(v) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
