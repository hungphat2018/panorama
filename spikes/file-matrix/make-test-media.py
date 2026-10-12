#!/usr/bin/env python3
"""Regenerate every generated test file of the supported-file matrix (panorama M1, phase 02).

  python make-test-media.py              build everything that is missing
  python make-test-media.py --force      rebuild everything
  python make-test-media.py --only ID..  build the listed cases (and what they derive from)
  python make-test-media.py --list       print the case table

Needs: the MSYS2 UCRT64 ffmpeg/ffprobe (GPL build with x264, x265, SVT-AV1, libvpx, mp3lame: a tool for
making test media, never linked into the player; FFMPEG_DIR overrides the folder), MKVToolNix 102.0 portable
in tools/mkvtoolnix (official 7z from mkvtoolnix.download, SHA-256 checked against the published sums, extracted
there), DejaVu fonts in tools/fonts (from the matplotlib package, Bitstream Vera licence), numpy and Pillow.
The downloaded files (dl-*) come from fetch-samples.py; the two real files are only referenced.

Output: media/<id>.<ext> (git-ignored) and media/cases.json, the table run-matrix.py reads: for every
case id, group, description, file, run settings (frame to dump, screenshot time), what a correct
picture looks like (`expect`) and what the file really contains (`probe`, `structure`).
"""
import argparse
import json
import random
import re
import shutil
import struct
import subprocess
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path

HERE = Path(__file__).resolve().parent
T = SourceFileLoader("matrix_media_tools", str(HERE / "matrix-media-tools.py")).load_module()
MEDIA, WORK = T.MEDIA, T.WORK

JUDAS = r"C:\Users\tamla\Downloads\[Judas] Nama-Anaru - S01E02.mkv"
HOTEL = r"C:\Users\tamla\Downloads\260723_HOTEL_AMAN_43_V3_MAIN_50s_MASTER_16x9_VA_H264.mp4"
FILM = "24000/1001"
SIZE_LIMIT = 50 * 1024 * 1024

CASES = {}      # id -> dict, in registration order
BUILDERS = {}   # id -> function(out_path) -> structure dict or None


def case(cid, group, desc, ext="mkv", kind="generated", dump_frame=50, shot_at=2.1, seconds=8.0,
         expect=None, hw="expected", file=None, mpv=True):
    """Register a case. `hw`: 'expected' (D3D11VA should decode it), 'none' (no GPU decoder exists on this PC for
    the profile: software fallback needed) or 'n/a'. `expect` holds what a correct picture shows. `mpv`: False
    when stock mpv is not asked to play the file (a concat script is only meaningful for libavformat)."""
    def deco(fn):
        CASES[cid] = dict(id=cid, group=group, desc=desc, kind=kind, ext=ext, dump_frame=dump_frame,
                          shot_at=shot_at, seconds=seconds, expect=expect or {}, hw=hw, file=file, mpv=mpv)
        BUILDERS[cid] = fn
        return fn
    return deco


def path_of(cid):
    c = CASES[cid]
    if c.get("file"):
        return Path(c["file"])
    stem = cid[:-len(c["ext"]) - 1] if cid.endswith("-" + c["ext"]) else cid   # "h264-1080p24-mkv" -> h264-1080p24.mkv
    return MEDIA / f"{stem}.{c['ext']}"


def ensure(cid):
    """Build a case if its file is missing (used by cases derived from another one)."""
    p = path_of(cid)
    if not p.exists():
        build(cid)
    return p


# ---------------------------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------------------------

def src(w, h, rate, dur, post=""):
    return ["-f", "lavfi", "-i", f"testsrc2=size={w}x{h}:rate={rate}:duration={dur}{post}"]


def aud(dur):
    return ["-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={dur}"]


def x264(crf=22, preset="medium", pix="yuv420p", profile="high", extra=()):
    return ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", pix, "-profile:v", profile,
            "-bf", "3", "-g", "48", *extra]


# FFmpeg 9's command line ignores -color_primaries/-color_trc on the encoder; frame properties set by a
# setparams filter do reach the encoder (VUI) and the muxer, so every tag below goes through the filter graph.
TAGF = {"709": "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv",
        "709full": "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=pc",
        "601": "setparams=color_primaries=smpte170m:color_trc=smpte170m:colorspace=smpte170m:range=tv",
        "2020pq": "setparams=color_primaries=bt2020:color_trc=smpte2084:colorspace=bt2020nc:range=tv",
        "2020hlg": "setparams=color_primaries=bt2020:color_trc=arib-std-b67:colorspace=bt2020nc:range=tv"}


def vf(*parts, tag=None):
    """-vf argument: the given filters, then (optionally) a setparams that tags the frames."""
    chain = [p for p in parts if p] + ([TAGF[tag]] if tag else [])
    return ["-vf", ",".join(chain)] if chain else []


AAC = ["-c:a", "aac", "-b:a", "128k"]
HDR_X265 = ("colorprim=bt2020:transfer={trc}:colormatrix=bt2020nc:range=limited:repeat-headers=1:log-level=error"
            ":keyint=48")
MASTER = ("master-display=G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(10000000,1):max-cll=1000,400"
          ":hdr10=1:hdr10-opt=1")


def work(name):
    WORK.mkdir(parents=True, exist_ok=True)
    return WORK / name


def h264_sps_flags(path):
    """frame_mbs_only_flag / mb_adaptive_frame_field_flag of the first SPS, via the trace_headers bitstream filter."""
    p = subprocess.run([T.FFMPEG, "-v", "trace", "-i", str(path), "-map", "0:v:0", "-c", "copy", "-bsf:v", "trace_headers",
                        "-frames:v", "1", "-f", "null", "-"], capture_output=True, text=True, errors="replace")
    txt = p.stderr
    out = {}
    for key in ("frame_mbs_only_flag", "mb_adaptive_frame_field_flag"):
        m = re.search(rf"{key}\s+\S+\s+=\s+(\d)", txt) or re.search(rf"{key}\s+(\d)", txt)
        if m:
            out[key] = int(m.group(1))
    return out


# ---------------------------------------------------------------------------------------------
# A. codecs and containers (the baseline picture is testsrc2: moving bars, a checkerboard, a frame counter)
# ---------------------------------------------------------------------------------------------

@case("h264-1080p24-mkv", "codec", "H.264 High 8-bit 4:2:0, 1920x1080, 23.976 CFR, B-frames, AAC, MKV, BT.709 tagged",
      expect={"dar": 16 / 9})
def _(out):
    T.ff([*src(1920, 1080, FILM, 10), *aud(10), "-map", "0:v", "-map", "1:a", *x264(), *vf(tag="709"), *AAC], out)


@case("h264-1080p24-mp4", "codec", "same H.264 stream in MP4 (moov at the end) with an embedded mov_text subtitle track",
      ext="mp4", expect={"dar": 16 / 9})
def _(out):
    srt = work("baseline.srt")
    srt.write_text("1\n00:00:01,000 --> 00:00:04,000\nmov_text subtitle in MP4\n\n2\n00:00:05,000 --> 00:00:08,000\n"
                   "second <i>cue</i>\n", encoding="utf-8")
    T.ff(["-i", ensure("h264-1080p24-mkv"), "-i", srt, "-map", "0:v", "-map", "0:a", "-map", "1", "-c", "copy",
          "-c:s", "mov_text", "-disposition:s:0", "default"], out)


@case("hevc-main10-1080p24-mkv", "codec", "HEVC Main10, 1920x1080, 23.976, BT.709 tagged (the anime-library staple)",
      expect={"dar": 16 / 9})
def _(out):
    T.ff([*src(1920, 1080, FILM, 10), *aud(10), "-map", "0:v", "-map", "1:a", "-c:v", "libx265", "-preset", "fast",
          "-crf", "24", "-pix_fmt", "yuv420p10le", "-x265-params",
          "log-level=error:keyint=48:colorprim=bt709:transfer=bt709:colormatrix=bt709", *AAC], out)


@case("av1-10bit-1080p24-mkv", "codec", "AV1 Main 10-bit, 1920x1080, 23.976 (SVT-AV1)", expect={"dar": 16 / 9})
def _(out):
    T.ff([*src(1920, 1080, FILM, 10), *aud(10), "-map", "0:v", "-map", "1:a", "-c:v", "libsvtav1", "-preset", "8",
          "-crf", "36", "-pix_fmt", "yuv420p10le", "-g", "48", *vf(tag="709"), *AAC], out)


@case("vp9-1080p24-webm", "codec", "VP9 profile 0, 1920x1080, 23.976, Opus, WebM", ext="webm", expect={"dar": 16 / 9})
def _(out):
    T.ff([*src(1920, 1080, FILM, 10), *aud(10), "-map", "0:v", "-map", "1:a", "-c:v", "libvpx-vp9", "-crf", "34",
          "-b:v", "0", "-deadline", "good", "-cpu-used", "5", "-row-mt", "1", "-pix_fmt", "yuv420p", "-g", "48",
          *vf(tag="709"), "-c:a", "libopus", "-b:a", "96k"], out)


@case("h264-hi10p-1080p24-mkv", "codec", "H.264 High 10 (10-bit 4:2:0): the anime fansub staple, no GPU decoder exists",
      hw="none", expect={"dar": 16 / 9})
def _(out):
    T.ff([*src(1920, 1080, FILM, 10), *aud(10), "-map", "0:v", "-map", "1:a",
          *x264(pix="yuv420p10le", profile="high10"), *vf(tag="709"), *AAC], out)


@case("h264-422-10bit-1080p24-mkv", "codec", "H.264 High 4:2:2 10-bit (intra-frame production style), software only",
      hw="none", expect={"dar": 16 / 9})
def _(out):
    T.ff([*src(1920, 1080, FILM, 8), *aud(8), "-map", "0:v", "-map", "1:a",
          *x264(pix="yuv422p10le", profile="high422", crf=24), *vf(tag="709"), *AAC], out)


@case("h264-444-1080p24-mkv", "codec", "H.264 High 4:4:4 Predictive 8-bit (screen-recording style), software only",
      hw="none", expect={"dar": 16 / 9})
def _(out):
    T.ff([*src(1920, 1080, FILM, 8), *aud(8), "-map", "0:v", "-map", "1:a",
          *x264(pix="yuv444p", profile="high444", crf=24), *vf(tag="709"), *AAC], out)


@case("mpeg4-asp-480p25-avi", "codec", "MPEG-4 ASP (XviD tag) 640x480 25 fps + MP3 in AVI: the old rip", ext="avi",
      expect={"dar": 4 / 3})
def _(out):
    T.ff([*src(640, 480, 25, 10), *aud(10), "-map", "0:v", "-map", "1:a", "-c:v", "mpeg4", "-vtag", "XVID", "-q:v", "4",
          "-bf", "0", "-g", "50", "-c:a", "libmp3lame", "-b:a", "128k"], out)


@case("hevc-main10-2160p60-mkv", "codec", "HEVC Main10 3840x2160 at 60 fps (UHD 60p), BT.709 tagged", expect={"dar": 16 / 9},
      dump_frame=100)
def _(out):
    T.ff([*src(3840, 2160, 60, 10), *aud(10), "-map", "0:v", "-map", "1:a", "-c:v", "libx265", "-preset", "ultrafast",
          "-crf", "28", "-pix_fmt", "yuv420p10le", "-x265-params",
          "log-level=error:keyint=120:colorprim=bt709:transfer=bt709:colormatrix=bt709:frame-threads=8", *AAC], out)


# ---------------------------------------------------------------------------------------------
# B. interlaced and telecined
# ---------------------------------------------------------------------------------------------

@case("h264-1080i-tff-mbaff-ts", "interlace", "H.264 High 1080i25, top field first, MBAFF, MPEG-TS, AC-3 (broadcast style)",
      ext="ts", expect={"dar": 16 / 9, "combing": True})
def _(out):
    T.ff([*src(1920, 1080, 50, 10), *aud(10), "-map", "0:v", "-map", "1:a", *vf("interlace=scan=tff", tag="709"),
          "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", "-profile:v", "high",
          "-flags", "+ilme+ildct", "-g", "50", "-c:a", "ac3", "-b:a", "192k"], out)


@case("mpeg2-1080i-tff-ts", "interlace", "MPEG-2 1080i25, top field first, MPEG-TS, MP2 (ATSC/DVB HD style)", ext="ts",
      expect={"dar": 16 / 9, "combing": True})
def _(out):
    T.ff([*src(1920, 1080, 50, 10), *aud(10), "-map", "0:v", "-map", "1:a", *vf("interlace=scan=tff", tag="709"),
          "-c:v", "mpeg2video", "-b:v", "16M", "-maxrate", "18M", "-bufsize", "8M", "-flags", "+ilme+ildct",
          "-pix_fmt", "yuv420p", "-g", "12", "-c:a", "mp2", "-b:a", "192k"], out)


@case("mpeg2-576i-bff-16x9-ts", "interlace", "MPEG-2 720x576 25i, BOTTOM field first, 16:9 anamorphic (SAR 64:45), MPEG-TS",
      ext="ts", expect={"dar": 16 / 9, "combing": True})
def _(out):
    T.ff([*src(720, 576, 50, 10), *aud(10), "-map", "0:v", "-map", "1:a", *vf("interlace=scan=bff", tag="601"),
          "-c:v", "mpeg2video", "-b:v", "6M", "-maxrate", "8M", "-bufsize", "2M", "-flags", "+ilme+ildct",
          "-aspect", "16:9", "-pix_fmt", "yuv420p", "-g", "12", "-c:a", "mp2", "-b:a", "192k"], out)


@case("mpeg2-480i-hard-telecine-mpg", "interlace",
      "MPEG-2 720x480 29.97i, 23.976 film hard-telecined (3:2 pulldown baked in), 4:3, MPEG-2 program stream (DVD rip)",
      ext="mpg", expect={"dar": 4 / 3, "combing": True, "telecine": True})
def _(out):
    T.ff([*src(720, 480, FILM, 10), *aud(10), "-map", "0:v", "-map", "1:a", *vf("telecine=pattern=23,setfield=mode=tff", tag="601"),
          "-c:v", "mpeg2video", "-b:v", "5M", "-maxrate", "8M", "-bufsize", "2M", "-flags", "+ilme+ildct", "-aspect", "4:3",
          "-pix_fmt", "yuv420p", "-g", "15", "-c:a", "ac3", "-b:a", "192k", "-f", "vob"], out)


@case("dl-h264-paff", "interlace", "JVT conformance stream with picture-adaptive frame/field (PAFF) H.264, raw, 720x480",
      kind="download", ext="26l", file=str(MEDIA / "dl-h264-paff.26l"), expect={"combing": True}, shot_at=1.0, dump_frame=20)
def _(out):
    raise RuntimeError("downloaded by fetch-samples.py")


# ---------------------------------------------------------------------------------------------
# C. timing
# ---------------------------------------------------------------------------------------------

@case("vfr-24-30-60-mkv", "timing", "variable frame rate MKV: 4 s at 24 fps, 4 s at 30 fps, 4 s at 60 fps (1280x720 H.264)",
      expect={"dar": 16 / 9, "vfr": [24, 30, 60]}, seconds=12.0)
def _(out):
    parts = []
    for fps in (24, 30, 60):
        p = work(f"vfr-part-{fps}.mkv")
        T.ff([*src(1280, 720, fps, 4), "-c:v", "libx264", "-preset", "fast", "-crf", "22", "-pix_fmt", "yuv420p",
              "-bf", "2", "-g", str(fps * 2), *vf(tag="709")], p)
        parts.append(p)
    lst = work("vfr-concat.txt")
    lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8")
    T.ff(["-f", "concat", "-safe", "0", "-i", lst, "-c", "copy"], out)


@case("mp4-empty-edit-1p5s", "timing", "MP4 whose video track starts at 1.5 s behind an empty edit; audio starts at 0",
      ext="mp4", expect={"dar": 16 / 9, "start": 1.5})
def _(out):
    base = ensure("h264-1080p24-mkv")
    T.ff(["-itsoffset", "1.5", "-i", base, "-i", base, "-map", "0:v", "-map", "1:a", "-c", "copy"], out)


@case("mp4-negative-cts", "timing", "MP4 with B-frames, negative composition offsets (ctts v1) and no edit list", ext="mp4",
      expect={"dar": 16 / 9})
def _(out):
    T.ff([*src(1280, 720, FILM, 10), *aud(10), "-map", "0:v", "-map", "1:a", *x264(crf=23, preset="fast"), *vf(tag="709"), *AAC,
          "-movflags", "+negative_cts_offsets"], out)


@case("mp4-trimmed-edit-list", "timing",
      "MP4 whose edit list starts 3.3 s into the media (QuickTime-style trim): the decoder must start at the 2.0 s keyframe "
      "and show nothing before 3.3 s", ext="mp4", expect={"dar": 16 / 9, "trim_s": 3.3}, seconds=6.0)
def _(out):
    base = ensure("h264-1080p24-mkv")
    tmp = work("trim-source.mp4")
    T.ff(["-i", base, "-map", "0:v", "-map", "0:a", "-c", "copy", "-use_editlist", "1"], tmp)
    data = bytearray(Path(tmp).read_bytes())
    patched = []
    trk = 0
    for typ, s, e in T._boxes(data, 0, len(data)):
        if typ != "moov":
            continue
        for t2, s2, e2 in T._boxes(data, s, e):
            if t2 != "trak":
                continue
            movie_ts = 1000
            media_ts = 1
            for t3, s3, e3 in T._boxes(data, s2, e2):
                if t3 == "mdia":
                    for t4, s4, e4 in T._boxes(data, s3, e3):
                        if t4 == "mdhd":
                            media_ts = struct.unpack(">I", data[s4 + 12:s4 + 16])[0]
            for t3, s3, e3 in T._boxes(data, s2, e2):
                if t3 != "edts":
                    continue
                for t4, s4, e4 in T._boxes(data, s3, e3):
                    if t4 == "elst" and data[s4] == 0:
                        n = struct.unpack(">I", data[s4 + 4:s4 + 8])[0]
                        # last entry = the media segment: skip the first 3.3 s of media, play the remaining 6.7 s
                        p = s4 + 8 + 12 * (n - 1)
                        struct.pack_into(">Ii", data, p, int(6.7 * movie_ts), int(3.3 * media_ts))
                        patched.append(trk)
            trk += 1
    if not patched:
        raise RuntimeError("no version-0 edit list found to patch")
    Path(out).write_bytes(bytes(data))
    return {"patched_tracks": patched, "note": "elst media_time rewritten in place to 3.3 s; keyframes every 2.0 s"}


@case("mkv-start-10s", "timing", "MKV whose timestamps start at 10.0 s (a cut remuxed with -c copy)",
      expect={"dar": 16 / 9, "start": 10.0})
def _(out):
    base = ensure("h264-1080p24-mkv")
    T.ff(["-itsoffset", "10", "-i", base, "-itsoffset", "10", "-i", base, "-map", "0:v", "-map", "1:a", "-c", "copy"], out)


@case("dl-mp4-empty-edit-5s", "timing", "FATE: MP4 4:4:4 H.264 with a 5 s empty edit at the start", kind="download",
      ext="mp4", file=str(MEDIA / "dl-mp4-empty-edit-5s.mp4"), hw="none", shot_at=1.5, dump_frame=20)
def _(out):
    raise RuntimeError("downloaded by fetch-samples.py")


@case("dl-mov-neg-first-pts", "timing", "FATE: MOV with negative first pts whose leading frames the edit list discards",
      kind="download", ext="mov", file=str(MEDIA / "dl-mov-neg-first-pts.mov"), shot_at=0.3, dump_frame=5, seconds=3.0)
def _(out):
    raise RuntimeError("downloaded by fetch-samples.py")


# ---------------------------------------------------------------------------------------------
# D. Matroska ordered chapters
# ---------------------------------------------------------------------------------------------

def ordered_edition_xml(atoms):
    """atoms: [(start_s, end_s, name, segment_uid_hex or None)] -> Matroska chapters XML with an ordered edition."""
    def ts(sec):
        return f"{int(sec // 3600):02d}:{int(sec % 3600 // 60):02d}:{sec % 60:012.9f}"
    xs = ['<?xml version="1.0" encoding="UTF-8"?>', "<Chapters>", "  <EditionEntry>",
          "    <EditionFlagOrdered>1</EditionFlagOrdered>", "    <EditionFlagDefault>1</EditionFlagDefault>"]
    for a, b, name, uid in atoms:
        xs += ["    <ChapterAtom>", f"      <ChapterTimeStart>{ts(a)}</ChapterTimeStart>",
               f"      <ChapterTimeEnd>{ts(b)}</ChapterTimeEnd>"]
        if uid:
            xs.append(f'      <ChapterSegmentUID format="hex">{uid}</ChapterSegmentUID>')
        xs += ["      <ChapterDisplay>", f"        <ChapterString>{name}</ChapterString>",
               "        <ChapterLanguage>eng</ChapterLanguage>", "      </ChapterDisplay>", "    </ChapterAtom>"]
    xs += ["  </EditionEntry>", "</Chapters>", ""]
    return "\n".join(xs)


def scene_clip(out, seconds, hue_expr, w=1280, h=720, fps="24"):
    """testsrc2 whose hue rotates with the expression: the left colour bar tells which scene is on screen
    (0 deg: red, 120 deg: green, 240 deg: blue)."""
    T.ff([*src(w, h, fps, seconds), *vf(f"hue=h='{hue_expr}'", tag="709"), "-c:v", "libx264", "-preset", "fast",
          "-crf", "23", "-pix_fmt", "yuv420p", "-bf", "2", "-g", "48"], out)


@case("mkv-ordered-chapters-internal", "chapters",
      "MKV, 15 s of three 5 s scenes (left bar red, green, blue); ordered edition plays only scene 1 then scene 3 (10 s)",
      expect={"ordered": ["red", "blue"]}, shot_at=6.2, dump_frame=150, seconds=12.0)
def _(out):
    clip = work("ordered-internal-source.mkv")
    scene_clip(clip, 15, "120*floor(t/5)")
    xml = work("ordered-internal-chapters.xml")
    xml.write_text(ordered_edition_xml([(0, 5, "Scene 1", None), (10, 15, "Scene 3", None)]), encoding="utf-8")
    T.run([T.MKVMERGE, "-o", out, "--chapters", xml, clip], "mkvmerge ordered internal")


LINKED_DIR = MEDIA / "mkv-ordered-chapters-linked"
LINKED_UIDS = {"op": "11111111222222223333333344444441", "body": "11111111222222223333333344444442",
               "ed": "11111111222222223333333344444443"}


@case("mkv-ordered-chapters-linked", "chapters",
      "segment linking: main.mkv's ordered edition plays op.mkv (red, 5 s), its own 10 s body (green), then ed.mkv (blue, "
      "5 s); the three files sit in one folder", file=str(LINKED_DIR / "main.mkv"),
      expect={"ordered": ["red", "green", "blue"]}, shot_at=2.0, dump_frame=48, seconds=12.0)
def _(out):
    LINKED_DIR.mkdir(parents=True, exist_ok=True)
    for name, secs, hue in (("op", 5, "0"), ("body", 10, "120"), ("ed", 5, "240")):
        clip = work(f"linked-{name}-source.mkv")
        scene_clip(clip, secs, hue)
        target = Path(out) if name == "body" else LINKED_DIR / f"{name}.mkv"
        extra = []
        if name == "body":
            xml = work("linked-chapters.xml")
            xml.write_text(ordered_edition_xml([(0, 5, "Opening", LINKED_UIDS["op"]), (0, 10, "Episode", None),
                                                (0, 5, "Ending", LINKED_UIDS["ed"])]), encoding="utf-8")
            extra = ["--chapters", xml]
            target = Path(out)
        T.run([T.MKVMERGE, "-o", target, "--segment-uid", LINKED_UIDS[name], *extra, clip], f"mkvmerge linked {name}")


@case("ordered-internal-via-ffconcat", "chapters",
      "the ordered edition of mkv-ordered-chapters-internal written as an FFmpeg concat script, the way an own timeline layer "
      "would hand it to libavformat: scene 1 (0-5 s), then scene 3 (10-15 s of the same file)", ext="ffconcat",
      expect={"ordered": ["red", "blue"]}, shot_at=6.2, dump_frame=150, seconds=12.0, mpv=False)
def _(out):
    src_name = ensure("mkv-ordered-chapters-internal").name
    lines = ["ffconcat version 1.0"]
    for a, b in ((0, 5), (10, 15)):
        lines += [f"file '{src_name}'", f"inpoint {a}", f"outpoint {b}"]
    Path(out).write_text("\n".join(lines) + "\n", encoding="utf-8")


@case("ordered-linked-via-ffconcat", "chapters",
      "the linked-segment edition of mkv-ordered-chapters-linked as a concat script: op.mkv, main.mkv, ed.mkv in order (20 s)",
      ext="ffconcat", file=str(LINKED_DIR / "timeline.ffconcat"), expect={"ordered": ["red", "green", "blue"]},
      shot_at=2.0, dump_frame=48, seconds=12.0, mpv=False)
def _(out):
    ensure("mkv-ordered-chapters-linked")
    Path(out).write_text("ffconcat version 1.0\nfile 'op.mkv'\nfile 'main.mkv'\nfile 'ed.mkv'\n", encoding="utf-8")


# ---------------------------------------------------------------------------------------------
# E. subtitles
# ---------------------------------------------------------------------------------------------

ASS_TEXT = r"""[Script Info]
Title: panorama file matrix
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,DejaVu Serif,64,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,3,1,2,60,60,60,1
Style: Sign,DejaVu Sans,72,&H0000FFFF,&H000000FF,&H00101010,&H00000000,-1,0,0,0,100,100,0,0,1,4,0,8,60,60,80,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.50,0:00:04.50,Default,,0,0,0,,Attached DejaVu Serif: sphinx of black quartz, judge my vow.
Dialogue: 1,0:00:01.00,0:00:08.00,Default,,0,0,0,,{\an3\pos(1860,1020)\fs44}{\k40}ka{\k40}ra{\k40}o{\k40}ke
Dialogue: 2,0:00:05.00,0:00:09.00,Sign,,0,0,0,,{\an8\pos(960,140)\fad(400,400)}PANORAMA {\c&H00FF00&}SIGN{\r}
Dialogue: 0,0:00:05.00,0:00:09.00,Default,,0,0,0,,{\i1}Fallback glyphs: \N日本語の字幕 and Latin together{\i0}
"""


@case("ass-attached-fonts-mkv", "subtitles",
      "H.264 1080p MKV with an ASS track (styles, karaoke, \\pos, \\fad, CJK) and two attached DejaVu fonts the OS lacks",
      expect={"dar": 16 / 9, "subtitles": "ass"}, shot_at=2.0, dump_frame=48)
def _(out):
    base = ensure("h264-1080p24-mkv")
    ass = work("matrix-subs.ass")
    ass.write_text(ASS_TEXT, encoding="utf-8")
    fonts = [T.FONT_DIR / "DejaVuSerif.ttf", T.FONT_DIR / "DejaVuSans-Bold.ttf"]
    args = [T.MKVMERGE, "-o", out, base, "--language", "0:eng", "--track-name", "0:Signs and Songs", ass]
    for f in fonts:
        args += ["--attachment-mime-type", "application/x-truetype-font", "--attachment-name", f.name, "--attach-file", f]
    T.run(args, "mkvmerge ass + fonts")


@case("pgs-generated-mkv", "subtitles", "H.264 1080p MKV with an image subtitle track (PGS) written by our own encoder",
      expect={"dar": 16 / 9, "subtitles": "pgs"}, shot_at=2.0, dump_frame=48)
def _(out):
    from PIL import Image, ImageDraw, ImageFont
    import numpy as np
    font = ImageFont.truetype(str(T.FONT_DIR / "DejaVuSans.ttf"), 58)
    events = [(1.0, 4.0, "Generated PGS bitmap subtitle"), (5.0, 8.5, "Second display set, 1920x1080 plane")]
    sup = bytearray()
    for i, (a, b, text) in enumerate(events):
        probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
        l, t, r, bt = probe.textbbox((0, 0), text, font=font, stroke_width=3)
        w, h = r - l + 16, bt - t + 16
        im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ImageDraw.Draw(im).text((8 - l, 8 - t), text, font=font, fill=(255, 255, 255, 255), stroke_width=3,
                                stroke_fill=(0, 0, 0, 255))
        x, y = (1920 - w) // 2, 1080 - 150 - h
        sup += T.pgs_display_set(a, 2 * i, np.array(im), x, y, 1920, 1080)
        sup += T.pgs_clear_set(b, 2 * i + 1, x, y, w, h, 1920, 1080)
    supf = work("generated.sup")
    supf.write_bytes(bytes(sup))
    T.ff(["-i", ensure("h264-1080p24-mkv"), "-f", "sup", "-i", supf, "-map", "0", "-map", "1", "-c", "copy",
          "-disposition:s:0", "default"], out)


@case("dl-pgs-supsample", "subtitles", "FFmpeg samples: real Blu-ray PGS in MKV (720x480 video, 1280x720 PGS plane)",
      kind="download", ext="mkv", file=str(MEDIA / "dl-pgs-supsample.mkv"), shot_at=2.0, dump_frame=48)
def _(out):
    raise RuntimeError("downloaded by fetch-samples.py")


@case("srt-mkv", "subtitles", "H.264 1080p MKV with an embedded SubRip (SRT) track with italic and positioned cues",
      expect={"dar": 16 / 9, "subtitles": "srt"}, shot_at=2.0, dump_frame=48)
def _(out):
    srt = work("matrix-subs.srt")
    srt.write_text("1\n00:00:01,000 --> 00:00:04,000\nPlain SRT line, <i>italic</i> and <b>bold</b>\n\n"
                   "2\n00:00:05,000 --> 00:00:08,000\n{\\an8}Top-aligned cue\n\n", encoding="utf-8")
    T.ff(["-i", ensure("h264-1080p24-mkv"), "-i", srt, "-map", "0", "-map", "1", "-c", "copy", "-c:s", "srt",
          "-disposition:s:0", "default"], out)


# ---------------------------------------------------------------------------------------------
# F. HDR and colour
# ---------------------------------------------------------------------------------------------

def hdr_clip(out, transfer):
    ppm = work(f"hdr-{transfer}.ppm")
    T.write_ppm16(ppm, T.hdr_card(transfer))
    trc = "smpte2084" if transfer == "pq" else "arib-std-b67"
    params = HDR_X265.format(trc=trc) + (":" + MASTER if transfer == "pq" else "")
    raw = work(f"hdr-{transfer}-raw.mkv")
    T.ff(["-loop", "1", "-framerate", FILM, "-i", ppm, "-t", "10", *vf(
        "scale=out_color_matrix=bt2020nc:out_range=tv:flags=accurate_rnd+full_chroma_int,format=yuv420p10le",
        tag="2020pq" if transfer == "pq" else "2020hlg"),
        "-c:v", "libx265", "-preset", "fast", "-crf", "18", "-x265-params", params], raw)
    # Container-level colour description, as MakeMKV/mkvmerge write it for real HDR10 / HLG files (the SEI
    # messages stay in the bitstream too): both routes reach the player, so both are present in the test file.
    cmd = [T.MKVMERGE, "-o", out, "--colour-matrix-coefficients", "0:9", "--colour-range", "0:1",
           "--colour-transfer-characteristics", f"0:{16 if transfer == 'pq' else 18}", "--colour-primaries", "0:9"]
    if transfer == "pq":
        cmd += ["--max-content-light", "0:1000", "--max-frame-light", "0:400",
                "--chromaticity-coordinates", "0:0.680,0.320,0.265,0.690,0.150,0.060",
                "--white-colour-coordinates", "0:0.3127,0.3290", "--max-luminance", "0:1000", "--min-luminance", "0:0.0001"]
    T.run([*cmd, raw], f"mkvmerge colour {transfer}")


@case("hdr10-pq-card-mkv", "hdr", "HEVC Main10 HDR10 (PQ, BT.2020, mastering display + MaxCLL) card: grey steps to 10000 nits, "
      "colour patches at 203 and 1000 nits, a ramp", expect={"dar": 16 / 9, "hdr": "pq"}, dump_frame=10)
def _(out):
    hdr_clip(out, "pq")
    return {"grey_nits": T.PQ_GREY_NITS, "mastering": "P3-D65, 0.0001-1000 cd/m2, MaxCLL 1000, MaxFALL 400"}


@case("hlg-card-mkv", "hdr", "HEVC Main10 HLG (ARIB STD-B67, BT.2020) card: grey steps and colour patches by signal level",
      expect={"dar": 16 / 9, "hdr": "hlg"}, dump_frame=10)
def _(out):
    hdr_clip(out, "hlg")


@case("dl-hdr10-leopard-4k", "hdr", "real HDR10 UHD Blu-ray clip: HEVC Main10 3840x2160, DTS-HD MA, PGS (4.2 s, 47 Mbit/s)",
      kind="download", ext="mkv", file=str(MEDIA / "dl-hdr10-leopard-4k.mkv"), dump_frame=20, shot_at=1.0, seconds=6.0)
def _(out):
    raise RuntimeError("downloaded by fetch-samples.py")


@case("dl-hdr10plus", "hdr", "FATE: HDR10+ (ST 2094-40 dynamic metadata) raw HEVC, one 1280x720 frame", kind="download", ext="hevc",
      file=str(MEDIA / "dl-hdr10plus.hevc"), dump_frame=0, shot_at=0.0, seconds=3.0)
def _(out):
    raise RuntimeError("downloaded by fetch-samples.py")


@case("dl-dovi-p5", "hdr", "FATE: Dolby Vision profile 5 (IPTPQc2, no HDR10 fallback), 10 frames", kind="download",
      ext="mp4", file=str(MEDIA / "dl-dovi-p5.mp4"), dump_frame=4, shot_at=0.15, seconds=3.0)
def _(out):
    raise RuntimeError("downloaded by fetch-samples.py")


@case("dl-dovi-p81", "hdr", "FATE: Dolby Vision profile 8.1 (HDR10-compatible base layer), 10 frames", kind="download",
      ext="mp4", file=str(MEDIA / "dl-dovi-p81.mp4"), dump_frame=4, shot_at=0.15, seconds=3.0)
def _(out):
    raise RuntimeError("downloaded by fetch-samples.py")


@case("dl-dovi-p7", "hdr", "FATE: Dolby Vision profile 7 (UHD Blu-ray dual layer: base + enhancement), one 4K frame",
      kind="download", ext="mkv", file=str(MEDIA / "dl-dovi-p7.mkv"), dump_frame=0, shot_at=0.0, seconds=3.0)
def _(out):
    raise RuntimeError("downloaded by fetch-samples.py")


def colour_clip(out, w, h, matrix, full_range, tag=None, untag=False):
    """Known sRGB colour card -> YUV with an explicit matrix and range -> H.264 MP4. `untag` clears every colour
    property on the frames so neither the VUI nor the MP4 colr box says anything."""
    ppm = work(f"colour-{w}x{h}-{matrix}-{'full' if full_range else 'tv'}.ppm")
    T.write_ppm8(ppm, T.colour_card(w, h))
    rng = "pc" if full_range else "tv"
    vf = f"scale=in_range=pc:out_color_matrix={matrix}:out_range={rng}:flags=accurate_rnd+full_chroma_int,format=yuv420p"
    if untag:
        vf += ",setparams=range=unknown:colorspace=unknown:color_primaries=unknown:color_trc=unknown"
    elif tag:
        vf += "," + TAGF[tag]
    T.ff(["-loop", "1", "-framerate", "24", "-i", ppm, "-t", "6", "-vf", vf, "-c:v", "libx264", "-preset", "medium",
          "-crf", "14", "-pix_fmt", "yuv420p", "-profile:v", "high"], out)


@case("color-bt709-limited-mp4", "colour", "known colour card, BT.709 matrix, limited range, tagged (the reference)", ext="mp4",
      expect={"dar": 16 / 9, "colour": "bt709"}, dump_frame=10, seconds=5.0)
def _(out):
    colour_clip(out, 1920, 1080, "bt709", False, tag="709")


@case("color-bt709-fullrange-mp4", "colour", "same card, BT.709 matrix, FULL range (pc), tagged full: phone/screen-recorder style",
      ext="mp4", expect={"dar": 16 / 9, "colour": "bt709"}, dump_frame=10, seconds=5.0)
def _(out):
    colour_clip(out, 1920, 1080, "bt709", True, tag="709full")


@case("color-bt601-480p-tagged-mp4", "colour", "card at 720x480 with BT.601 (smpte170m) matrix, tagged", ext="mp4",
      expect={"dar": 3 / 2, "colour": "bt601"}, dump_frame=10, seconds=5.0)
def _(out):
    colour_clip(out, 720, 480, "smpte170m", False, tag="601")


@case("color-bt601-480p-untagged-mp4", "colour", "same 720x480 BT.601 bits with every colour tag set to 'unspecified': the "
      "player must guess BT.601 from the size", ext="mp4", expect={"dar": 3 / 2, "colour": "bt601"}, dump_frame=10,
      seconds=5.0)
def _(out):
    colour_clip(out, 720, 480, "smpte170m", False, untag=True)


# ---------------------------------------------------------------------------------------------
# G. geometry
# ---------------------------------------------------------------------------------------------

@case("anamorphic-sar-vui-mkv", "geometry", "720x480 H.264 with SAR 32:27 in the stream (Extended_SAR VUI) = 16:9 display",
      expect={"dar": 16 / 9})
def _(out):
    T.ff([*src(720, 480, FILM, 8), *vf("setsar=32/27", tag="601"), *x264(crf=22, preset="fast")], out)


@case("anamorphic-container-dar-mkv", "geometry", "720x480 H.264 with SAR 1:1 in the stream but 853x480 display size in the "
      "MKV container (container-only aspect)", expect={"dar": 853 / 480})
def _(out):
    clip = work("anamorphic-container-source.mkv")
    T.ff([*src(720, 480, FILM, 8), *vf("setsar=1", tag="601"), *x264(crf=22, preset="fast")], clip)
    T.run([T.MKVMERGE, "-o", out, "--display-dimensions", "0:853x480", clip], "mkvmerge display dimensions")


@case("rotate-90-mp4", "geometry", "1920x1080 coded video with a 90 degree display matrix in the MP4 (phone portrait video): "
      "shows as 1080x1920", ext="mp4", expect={"dar": 9 / 16, "rotation": 90})
def _(out):
    plain = work("rotate-source.mp4")
    T.ff([*src(1920, 1080, FILM, 8), *vf(tag="709"), *x264(crf=22, preset="fast")], plain)
    T.ff(["-display_rotation", "90", "-i", plain, "-c", "copy"], out)


@case("odd-size-h264-1918x1078-mkv", "geometry", "H.264 1918x1078 (SPS crops 2 px right and 10 px bottom of the 1920x1088 "
      "coded picture)", expect={"dar": 1918 / 1078})
def _(out):
    T.ff([*src(1918, 1078, FILM, 8), *vf(tag="709"), *x264(crf=22, preset="fast")], out)


@case("odd-size-hevc-1918x1078-mkv", "geometry", "HEVC Main10 1918x1078 (conformance window crop)",
      expect={"dar": 1918 / 1078})
def _(out):
    T.ff([*src(1918, 1078, FILM, 8), "-c:v", "libx265", "-preset", "fast", "-crf", "24", "-pix_fmt", "yuv420p10le",
          "-x265-params", "log-level=error:keyint=48:colorprim=bt709:transfer=bt709:colormatrix=bt709"], out)


# ---------------------------------------------------------------------------------------------
# H. broken files
# ---------------------------------------------------------------------------------------------

@case("truncated-mkv", "broken", "the baseline MKV cut off at 55 % of its bytes (no cues, last cluster incomplete)",
      expect={"dar": 16 / 9}, seconds=10.0)
def _(out):
    data = ensure("h264-1080p24-mkv").read_bytes()
    Path(out).write_bytes(data[:int(len(data) * 0.55)])


@case("truncated-mp4-no-moov", "broken", "the baseline MP4 (moov at the end) cut off at 60 %: no index, cannot be opened",
      ext="mp4")
def _(out):
    data = ensure("h264-1080p24-mp4").read_bytes()
    Path(out).write_bytes(data[:int(len(data) * 0.6)])


@case("corrupt-midstream-mkv", "broken", "the baseline MKV with 24 KB of random bytes written over the middle of its clusters",
      expect={"dar": 16 / 9}, seconds=10.0)
def _(out):
    data = bytearray(ensure("h264-1080p24-mkv").read_bytes())
    rng = random.Random(20261011)
    mid = len(data) // 2
    data[mid:mid + 24576] = bytes(rng.getrandbits(8) for _ in range(24576))
    Path(out).write_bytes(bytes(data))


@case("garbage-not-media-mkv", "broken", "300 KB of random bytes named .mkv", expect={})
def _(out):
    rng = random.Random(7)
    Path(out).write_bytes(bytes(rng.getrandbits(8) for _ in range(300_000)))


@case("empty-file-mkv", "broken", "a zero-byte .mkv", expect={})
def _(out):
    Path(out).write_bytes(b"")


# ---------------------------------------------------------------------------------------------
# I. the two real files (read-only, referenced where they are)
# ---------------------------------------------------------------------------------------------

@case("real-judas-hevc-main10-mkv", "real", "[Judas] Nama-Anaru S01E02: HEVC Main10 1080p 23.976 anime episode, untagged colour",
      kind="real", file=JUDAS, expect={"dar": 16 / 9}, dump_frame=60)
def _(out):
    raise RuntimeError("real file, not generated")


@case("real-hotel-h264-mp4", "real", "HOTEL AMAN master: H.264 High 1080p 25 fps, 62 s", kind="real", file=HOTEL,
      ext="mp4", expect={"dar": 16 / 9}, dump_frame=60)
def _(out):
    raise RuntimeError("real file, not generated")


# ---------------------------------------------------------------------------------------------
# driver
# ---------------------------------------------------------------------------------------------

def build(cid):
    c = CASES[cid]
    if c["kind"] != "generated":
        return
    out = path_of(cid)
    MEDIA.mkdir(parents=True, exist_ok=True)
    print(f"building {cid} ...", flush=True)
    struct_info = BUILDERS[cid](out)
    if struct_info:
        work(f"structure-{cid}.json").write_text(json.dumps(struct_info, default=str), encoding="utf-8")
    mb = out.stat().st_size / 1e6
    print(f"   -> {out.name}  {mb:.1f} MB", flush=True)
    if out.stat().st_size > SIZE_LIMIT:
        print(f"   WARNING: {mb:.1f} MB exceeds the 50 MB limit", file=sys.stderr)


def idet_stats(path, frames=150):
    """libavfilter idet over the first frames: how many are TFF / BFF / progressive, and which fields repeat
    (a telecine pattern shows up as 'repeated fields')."""
    pr = subprocess.run([T.FFMPEG, "-v", "info", "-i", str(path), "-map", "0:v:0", "-vf", "idet", "-frames:v", str(frames),
                         "-an", "-f", "null", "-"], capture_output=True, text=True, errors="replace")
    out = {}
    # the filter graph is created twice (format probing, then the real run): the last report holds the counts
    ms = re.findall(r"Multi frame detection: TFF:\s*(\d+)\s+BFF:\s*(\d+)\s+Progressive:\s*(\d+)\s+Undetermined:\s*(\d+)", pr.stderr)
    if ms:
        out["idet_multi"] = dict(zip(("tff", "bff", "progressive", "undetermined"), map(int, ms[-1])))
    ms = re.findall(r"Repeated Fields: Neither:\s*(\d+)\s+Top:\s*(\d+)\s+Bottom:\s*(\d+)", pr.stderr)
    if ms:
        out["idet_repeated_fields"] = dict(zip(("neither", "top", "bottom"), map(int, ms[-1])))
    return out


def structure_of(cid, p):
    """What the generated file really contains beyond ffprobe: edit lists (MP4), SPS field flags (H.264 TS), notes."""
    info = {}
    saved = work(f"structure-{cid}.json")
    if saved.exists():
        info.update(json.loads(saved.read_text(encoding="utf-8")))
    if p.suffix == ".mp4" and p.stat().st_size > 0:
        try:
            info["edit_lists"] = T.mp4_edit_lists(p)
        except Exception:  # truncated MP4: no moov box to read
            info["edit_lists"] = "unreadable (no moov box)"
    if cid == "h264-1080i-tff-mbaff-ts":
        info["sps"] = h264_sps_flags(p)
    if CASES[cid]["group"] == "interlace" and p.stat().st_size > 0:
        info.update(idet_stats(p))
    return info


def describe_all():
    """Probe every case file that exists and write media/cases.json."""
    rows = []
    for cid, c in CASES.items():
        p = path_of(cid)
        row = {k: v for k, v in c.items() if k != "ext"}
        row["path"] = str(p)
        row["exists"] = p.exists()
        if cid == "mkv-ordered-chapters-linked":
            row["companions"] = [str(LINKED_DIR / "op.mkv"), str(LINKED_DIR / "ed.mkv")]
        if p.exists() and p.stat().st_size > 0:
            try:
                pj = T.probe_json(p)
                row["probe"] = T.summarize_probe(pj)
                (T.RESULTS / "probe").mkdir(parents=True, exist_ok=True)
                (T.RESULTS / "probe" / f"{cid}.json").write_text(json.dumps(pj, indent=1), encoding="utf-8")
            except RuntimeError as e:  # a broken file is allowed to fail probing: that is the case
                row["probe_error"] = str(e).splitlines()[0][:200]
        elif p.exists():
            row["probe_error"] = "zero bytes"
        row["size_bytes"] = p.stat().st_size if p.exists() else None
        if p.exists() and (c["kind"] == "generated" or c["group"] == "interlace"):
            row["structure"] = structure_of(cid, p)
        rows.append(row)
    (MEDIA / "cases.json").write_text(json.dumps(rows, indent=1, default=str), encoding="utf-8")
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    if a.list:
        for cid, c in CASES.items():
            print(f"{c['group']:10s} {cid:34s} {c['kind']:9s} {c['desc'][:90]}")
        return 0
    ids = a.only or [cid for cid, c in CASES.items() if c["kind"] == "generated"]
    for cid in ids:
        if cid not in CASES:
            print(f"unknown case {cid}", file=sys.stderr)
            return 2
        if a.force or not path_of(cid).exists():
            build(cid)
    rows = describe_all()
    missing = [r["id"] for r in rows if not r["exists"]]
    print(f"{len(rows)} cases, {len(rows) - len(missing)} files present" + (f", missing: {', '.join(missing)}" if missing else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
