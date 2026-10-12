"""Shared helpers for the file matrix scripts: tool locations, ffmpeg/mkvmerge runners,
test pictures (PQ/HLG cards, colour cards), a PGS subtitle writer and an MP4 edit-list reader.

Imported by make-test-media.py and run-matrix.py through importlib (the file name has a dash).
Nothing here reads downloaded media: the only parsers (MP4 boxes, ffprobe) run on files this
spike generated or on the ffprobe tool's own output.
"""
import json
import os
import struct
import subprocess
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
MEDIA = HERE / "media"
WORK = MEDIA / "_work"          # intermediate files, never played
RESULTS = HERE / "results"
TOOLS = HERE / "tools"

# The MSYS2 UCRT64 FFmpeg is a GPL build (x264, x265, SVT-AV1, libvpx, mp3lame ...): fine as a tool that
# makes test media, never linked into the player. Override with FFMPEG_DIR.
FFMPEG_DIR = Path(os.environ.get("FFMPEG_DIR", r"C:\Users\tamla\msys64\ucrt64\bin"))
FFMPEG = str(FFMPEG_DIR / "ffmpeg.exe")
FFPROBE = str(FFMPEG_DIR / "ffprobe.exe")
MKVMERGE = str(TOOLS / "mkvtoolnix" / "mkvmerge.exe")
FONT_DIR = TOOLS / "fonts"


def run(cmd, what, quiet=True):
    """Run a tool, raise with its stderr when it fails."""
    p = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0 and not (cmd[0] == MKVMERGE and p.returncode == 1):  # mkvmerge 1 = warnings only
        raise RuntimeError(f"{what} failed (rc={p.returncode}):\n{' '.join(map(str, cmd))}\n{p.stderr[-2000:]}{p.stdout[-1000:]}")
    return p


def ff(args, out, what=None):
    """ffmpeg with quiet logging, overwrite, args then the output file."""
    run([FFMPEG, "-v", "error", "-hide_banner", "-y", *args, out], what or f"ffmpeg -> {Path(out).name}")


def probe_json(path):
    """ffprobe as a dict: format, streams (with side data) and chapters."""
    p = run([FFPROBE, "-v", "error", "-show_format", "-show_streams", "-show_chapters",
             "-print_format", "json", str(path)], f"ffprobe {Path(path).name}")
    return json.loads(p.stdout)


# ---------------------------------------------------------------------------------------------
# Test pictures. All are generated with numpy; 16-bit cards go to ffmpeg as binary PPM (P6, maxval 65535).
# ---------------------------------------------------------------------------------------------

def pq_encode(nits):
    """SMPTE ST 2084 inverse EOTF: absolute luminance in cd/m2 -> PQ signal 0..1."""
    y = np.clip(np.asarray(nits, dtype=np.float64) / 10000.0, 0.0, 1.0)
    m1, m2 = 2610 / 16384, 2523 / 4096 * 128
    c1, c2, c3 = 3424 / 4096, 2413 / 4096 * 32, 2392 / 4096 * 32
    p = y ** m1
    return ((c1 + c2 * p) / (1 + c3 * p)) ** m2


def pq_decode(signal):
    """PQ signal 0..1 -> cd/m2 (used to label patches in the manifest)."""
    s = np.clip(np.asarray(signal, dtype=np.float64), 0.0, 1.0)
    m1, m2 = 2610 / 16384, 2523 / 4096 * 128
    c1, c2, c3 = 3424 / 4096, 2413 / 4096 * 32, 2392 / 4096 * 32
    p = s ** (1 / m2)
    return 10000.0 * (np.maximum(p - c1, 0) / (c2 - c3 * p)) ** (1 / m1)


def hlg_oetf(e):
    """ARIB STD-B67 / BT.2100 HLG OETF: normalised scene light 0..1 -> signal 0..1."""
    e = np.clip(np.asarray(e, dtype=np.float64), 0.0, 1.0)
    a, b, c = 0.17883277, 0.28466892, 0.55991073
    return np.where(e <= 1 / 12, np.sqrt(3 * e), a * np.log(np.maximum(12 * e - b, 1e-12)) + c)


# Luminance steps (cd/m2) of the HDR10 card's grey row, left to right; also read by run-matrix.py.
PQ_GREY_NITS = [0.1, 1, 5, 18, 100, 203, 400, 600, 1000, 2000, 4000, 10000]
CARD_GREY_ROW = (120, 420)     # y range of the grey patches
CARD_COLOUR_ROW = (480, 780)   # y range of the colour patches
CARD_RAMP_ROW = (840, 1000)    # y range of the signal ramp


def hdr_card(transfer):
    """1920x1080 float RGB (already transfer-encoded, BT.2020 primaries) card for 'pq' or 'hlg'.

    Row 1: twelve grey patches. Row 2: R G B Y C M at two levels. Row 3: a 0..100 % signal ramp.
    Outer 6 px frame at diffuse white (203 cd/m2 in PQ, 75 % in HLG). Background: dark grey.
    """
    w, h = 1920, 1080
    img = np.zeros((h, w, 3))
    if transfer == "pq":
        enc = pq_encode
        grey = [float(enc(n)) for n in PQ_GREY_NITS]
        bg, frame = float(enc(5)), float(enc(203))
        colour_levels = [float(enc(203)), float(enc(1000))]
        def scale(vec, level_nits):  # primary of the given luminance, then encode
            return enc(np.array(vec, dtype=float) * level_nits)
        colour_nits = (203, 1000)
    else:  # HLG: patches are specified as signal levels: 12 steps from 2 % to 100 %
        grey = [0.02, 0.05, 0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.75, 0.85, 0.95, 1.00]
        bg, frame = 0.10, 0.75
        colour_nits = (0.5, 1.0)  # signal fractions
        def scale(vec, level):
            return np.array(vec, dtype=float) * level
    img[:] = bg
    y0, y1 = CARD_GREY_ROW
    for i, g in enumerate(grey):
        x0 = 60 + i * 150
        img[y0:y1, x0:x0 + 130] = g
    prims = [(1, 0, 0), (0, 1, 0), (0, 0, 1), (1, 1, 0), (0, 1, 1), (1, 0, 1)]
    y0, y1 = CARD_COLOUR_ROW
    for i, p in enumerate(prims):
        for j, level in enumerate(colour_nits):
            x0 = 60 + (i * 2 + j) * 150
            img[y0:y1, x0:x0 + 130] = scale(p, level)
    y0, y1 = CARD_RAMP_ROW
    img[y0:y1, 60:w - 60] = np.linspace(0, 1, w - 120)[None, :, None]
    for sl in (np.s_[:6, :], np.s_[-6:, :], np.s_[:, :6], np.s_[:, -6:]):
        img[sl] = frame
    return img


def write_ppm16(path, img01):
    """float RGB 0..1 (h, w, 3) -> binary PPM with 16-bit big-endian samples."""
    h, w, _ = img01.shape
    u16 = np.clip(np.round(img01 * 65535), 0, 65535).astype(">u2")
    with open(path, "wb") as f:
        f.write(b"P6\n%d %d\n65535\n" % (w, h) + u16.tobytes())


# 8 colour bars + grey steps + saturated corners: known sRGB values, for the SDR colour cases.
COLOUR_CARD_BARS = [(235, 235, 235), (235, 235, 16), (16, 235, 235), (16, 235, 16),
                    (235, 16, 235), (235, 16, 16), (16, 16, 235), (16, 16, 16)]


def colour_card(w, h):
    """8-bit RGB card (h, w, 3): row 1 colour bars, row 2 grey steps, bright outer frame so the picture
    bounds can be measured against a black window background."""
    img = np.full((h, w, 3), 90, dtype=np.uint8)
    top, bot = int(h * 0.12), int(h * 0.50)
    for i, c in enumerate(COLOUR_CARD_BARS):
        x0, x1 = int(w * (0.06 + i * 0.11)), int(w * (0.06 + (i + 1) * 0.11))
        img[top:bot, x0:x1] = c
    top, bot = int(h * 0.56), int(h * 0.80)
    for i in range(8):
        g = int(round(16 + (235 - 16) * i / 7))
        x0, x1 = int(w * (0.06 + i * 0.11)), int(w * (0.06 + (i + 1) * 0.11))
        img[top:bot, x0:x1] = g
    t = max(2, h // 120)
    for sl in (np.s_[:t, :], np.s_[-t:, :], np.s_[:, :t], np.s_[:, -t:]):
        img[sl] = (200, 200, 60)
    return img


def write_ppm8(path, img):
    h, w, _ = img.shape
    with open(path, "wb") as f:
        f.write(b"P6\n%d %d\n255\n" % (w, h) + img.astype(np.uint8).tobytes())


# ---------------------------------------------------------------------------------------------
# PGS (Blu-ray image subtitle) writer: enough of the format for one bitmap per display set.
# ---------------------------------------------------------------------------------------------

def _pgs_segment(kind, pts_s, payload):
    pts = int(round(pts_s * 90000)) & 0xFFFFFFFF
    return b"PG" + struct.pack(">IIBH", pts, 0, kind, len(payload)) + payload


def _pgs_rle(indices):
    """RLE of a (h, w) uint8 palette-index bitmap in the PGS run-length scheme."""
    out = bytearray()
    for row in indices:
        x, w = 0, len(row)
        while x < w:
            c, n = int(row[x]), 1
            while x + n < w and row[x + n] == c and n < 16383:
                n += 1
            if c == 0:
                out += bytes([0, n]) if n < 64 else bytes([0, 0x40 | (n >> 8), n & 0xFF])
            elif n == 1:
                out.append(c)
            else:
                out += bytes([0, 0x80 | n, c]) if n < 64 else bytes([0, 0xC0 | (n >> 8), n & 0xFF, c])
            x += n
        out += b"\x00\x00"
    return bytes(out)


def pgs_display_set(pts_s, comp_no, rgba_bitmap, x, y, screen_w, screen_h, palette_id=0):
    """One 'epoch start' display set (PCS, WDS, PDS, ODS, END) showing an RGBA bitmap at (x, y)."""
    from PIL import Image
    h, w = rgba_bitmap.shape[:2]
    im = Image.fromarray(rgba_bitmap, "RGBA")
    # 63 colours + index 0 reserved for 'transparent'
    quant = getattr(Image, "Quantize", Image)       # Pillow >= 9.1 moved the constants into enums
    dither = getattr(Image, "Dither", Image)
    q = im.quantize(colors=63, method=quant.FASTOCTREE, dither=dither.NONE)
    pal = q.getpalette("RGBA")[: 63 * 4]
    idx = np.array(q, dtype=np.uint8) + 1
    alpha = np.array(im)[:, :, 3]
    idx[alpha < 8] = 0
    entries = [(0, 16, 128, 128, 0)]  # entry 0: fully transparent
    for i in range(63):
        r, g, b, a = pal[4 * i: 4 * i + 4]
        # BT.709 limited-range Y'CbCr, as Blu-ray HD graphics use
        yy = 16 + (0.2126 * 219 * r + 0.7152 * 219 * g + 0.0722 * 219 * b) / 255
        cb = 128 + (-0.2126 / 1.8556 * 224 * r - 0.7152 / 1.8556 * 224 * g + 0.9278 / 1.8556 * 224 * b) / 255
        cr = 128 + (0.7874 / 1.5748 * 224 * r - 0.7152 / 1.5748 * 224 * g - 0.0722 / 1.5748 * 224 * b) / 255
        entries.append((i + 1, int(round(yy)), int(round(cr)), int(round(cb)), int(a)))
    rle = _pgs_rle(idx)
    segs = []
    # PCS: size, frame rate code 0x10, composition number, epoch start, no palette-only update, palette id, 1 object
    segs.append(_pgs_segment(0x16, pts_s, struct.pack(">HHBHBBBB", screen_w, screen_h, 0x10, comp_no, 0x80, 0x00,
                                                       palette_id, 1) + struct.pack(">HBBHH", 0, 0, 0, x, y)))
    segs.append(_pgs_segment(0x17, pts_s, struct.pack(">BBHHHH", 1, 0, x, y, w, h)))
    pds = struct.pack(">BB", palette_id, 0) + b"".join(struct.pack(">BBBBB", *e) for e in entries)
    segs.append(_pgs_segment(0x14, pts_s, pds))
    ods = struct.pack(">HBB", 0, 0, 0xC0) + struct.pack(">I", len(rle) + 4)[1:] + struct.pack(">HH", w, h) + rle
    segs.append(_pgs_segment(0x15, pts_s, ods))
    segs.append(_pgs_segment(0x80, pts_s, b""))
    return b"".join(segs)


def pgs_clear_set(pts_s, comp_no, x, y, w, h, screen_w, screen_h):
    """Display set that removes the previous bitmap (PCS with no objects, WDS, END)."""
    segs = [_pgs_segment(0x16, pts_s, struct.pack(">HHBHBBBB", screen_w, screen_h, 0x10, comp_no, 0x00, 0x00, 0, 0)),
            _pgs_segment(0x17, pts_s, struct.pack(">BBHHHH", 1, 0, x, y, w, h)),
            _pgs_segment(0x80, pts_s, b"")]
    return b"".join(segs)


# ---------------------------------------------------------------------------------------------
# MP4 edit-list reader (on generated files only): proves a file really carries the structure it is named for.
# ---------------------------------------------------------------------------------------------

def _boxes(buf, start, end):
    pos = start
    while pos + 8 <= end:
        size, typ = struct.unpack(">I4s", buf[pos:pos + 8])
        hdr = 8
        if size == 1:
            size = struct.unpack(">Q", buf[pos + 8:pos + 16])[0]
            hdr = 16
        elif size == 0:
            size = end - pos
        if size < hdr:
            return
        yield typ.decode("latin1"), pos + hdr, pos + size
        pos += size


def mp4_edit_lists(path):
    """[{track: i, handler, movie_timescale, media_timescale, edits: [(duration_s, media_time_s or None, rate)]}]"""
    data = Path(path).read_bytes()
    out = []
    for typ, s, e in _boxes(data, 0, len(data)):
        if typ != "moov":
            continue
        movie_ts = 1000
        for t2, s2, e2 in _boxes(data, s, e):
            if t2 == "mvhd":
                ver = data[s2]
                movie_ts = struct.unpack(">I", data[s2 + (12 if ver == 0 else 20):s2 + (16 if ver == 0 else 24)])[0]
            if t2 != "trak":
                continue
            track = {"track": len(out), "handler": "", "movie_timescale": movie_ts, "media_timescale": 0, "edits": []}
            for t3, s3, e3 in _boxes(data, s2, e2):
                if t3 == "edts":
                    for t4, s4, e4 in _boxes(data, s3, e3):
                        if t4 != "elst":
                            continue
                        ver = data[s4]
                        n = struct.unpack(">I", data[s4 + 4:s4 + 8])[0]
                        p = s4 + 8
                        for _ in range(n):
                            if ver == 1:
                                dur, mt = struct.unpack(">Qq", data[p:p + 16])
                                rate = struct.unpack(">hh", data[p + 16:p + 20])
                                p += 20
                            else:
                                dur, mt = struct.unpack(">Ii", data[p:p + 8])
                                rate = struct.unpack(">hh", data[p + 8:p + 12])
                                p += 12
                            track["edits"].append((dur / movie_ts, None if mt == -1 else mt, rate[0] + rate[1] / 65536))
                if t3 == "mdia":
                    for t4, s4, e4 in _boxes(data, s3, e3):
                        if t4 == "mdhd":
                            ver = data[s4]
                            track["media_timescale"] = struct.unpack(
                                ">I", data[s4 + (12 if ver == 0 else 20):s4 + (16 if ver == 0 else 24)])[0]
                        if t4 == "hdlr":
                            track["handler"] = data[s4 + 8:s4 + 12].decode("latin1")
            ts = track["media_timescale"] or 1
            track["edits"] = [(round(d, 4), None if m is None else round(m / ts, 4), r) for d, m, r in track["edits"]]
            out.append(track)
    return out


def summarize_probe(pj):
    """Short, stable description of an ffprobe JSON used in the manifest and the report."""
    fmt = pj.get("format", {})
    vs = [s for s in pj.get("streams", []) if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")]
    out = {"container": fmt.get("format_name"), "duration_s": _f(fmt.get("duration")), "start_time_s": _f(fmt.get("start_time")),
           "size_bytes": int(fmt.get("size", 0)), "chapters": len(pj.get("chapters", [])),
           "audio": [s.get("codec_name") for s in pj.get("streams", []) if s.get("codec_type") == "audio"],
           "subtitles": [s.get("codec_name") for s in pj.get("streams", []) if s.get("codec_type") == "subtitle"]}
    if vs:
        v = vs[0]
        side = {sd.get("side_data_type"): sd for sd in v.get("side_data_list", [])}
        rot = side.get("Display Matrix", {}).get("rotation")
        out["video"] = {k: v.get(k) for k in ("codec_name", "profile", "width", "height", "pix_fmt", "field_order",
                                               "avg_frame_rate", "r_frame_rate", "sample_aspect_ratio",
                                               "display_aspect_ratio", "color_range", "color_space",
                                               "color_transfer", "color_primaries", "start_time", "bits_per_raw_sample")}
        if rot is not None:
            out["video"]["rotation_deg"] = rot
        if "DOVI configuration record" in side:
            d = side["DOVI configuration record"]
            out["video"]["dovi_profile"] = d.get("dv_profile")
            out["video"]["dovi_bl_compat_id"] = d.get("dv_bl_signal_compatibility_id")
        if "Mastering display metadata" in side:
            out["video"]["mastering_display"] = "present"
    return out


def _f(x):
    try:
        return round(float(x), 4)
    except (TypeError, ValueError):
        return None
