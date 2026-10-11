"""Read-only room report: print one JSON document describing the monitors on this Windows desk.

Run it with no arguments; it changes nothing (no display mode, driver, registry or file is written):

    python room-probe.py > room-report.json

Needs Python 3.8+ and the standard library only (ctypes, winreg). One optional section shells out to
Windows PowerShell 5.1 to read two WinRT classes; if that fails the section is skipped and noted.

What it reports, per active monitor (all values are read, nothing is guessed except where a field
says "derived" or carries a sanity flag):

  identity   EDID manufacturer / product / serial / week / year / checksum, SHA-256 of the base block,
             Windows' own stable monitor id (rebuilt from the EDID, checked against WinRT
             DisplayTarget.StableMonitorId), Windows container id, device path, adapter LUID + target id
             (the LUID is only valid until reboot).
  geometry   desktop position, pixel size, rotation, scaling, DPI scale, exact current timing
             (pixel clock, totals, rational refresh) from CCD QueryDisplayConfig.
  modes      every mode DXGI lists for the output with exact rational refresh rates, the GDI mode list,
             and the mode Windows would fall back to (the "registry" mode).
  timing     EDID-declared timings (DTD, CTA VICs, DisplayID type VII) and every variable-refresh signal
             in the EDID: base-block range limits (+ the EDID 1.4 continuous-frequency flag), AMD FreeSync
             vendor block, HDMI Forum VRRmin/VRRmax, DisplayID dynamic range limits.
  size       physical size from every source (DisplayID, detailed timing, base block cm) with sanity flags.
  color      EDID primaries and luminance, advanced-colour / HDR / auto-colour-management state.
  history    every monitor ever recorded in Enum\\DISPLAY (current and past) with the same EDID summary.

Evidence that "variable refresh is on" is NOT readable without a vendor SDK (NVAPI / ADL / IGCL);
the report says what is declared, what Windows exposes, and leaves driver state as null.
"""
import base64
import ctypes
import ctypes.wintypes as wt
import datetime
import hashlib
import json
import os
import subprocess
import sys
import uuid
import winreg
from fractions import Fraction

SCHEMA = "panorama.room-probe/1"
ERRORS = []  # a failing section never stops the report; it lands here instead


def guarded(label, fn, default=None):
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - one broken API must not kill the whole report
        ERRORS.append({"section": label, "error": f"{type(exc).__name__}: {exc}"})
        return default


# --------------------------------------------------------------------------------------------------
# EDID / CTA-861 / DisplayID parsing (layouts checked against edid-decode and the Linux drm_edid.c)
# --------------------------------------------------------------------------------------------------

CTA_VICS = {  # video-relevant subset of CTA-861 VICs: (width, height, hz_nominal, interlaced)
    1: (640, 480, 60, 0), 2: (720, 480, 60, 0), 3: (720, 480, 60, 0), 4: (1280, 720, 60, 0),
    5: (1920, 1080, 60, 1), 16: (1920, 1080, 60, 0), 17: (720, 576, 50, 0), 18: (720, 576, 50, 0),
    19: (1280, 720, 50, 0), 20: (1920, 1080, 50, 1), 31: (1920, 1080, 50, 0), 32: (1920, 1080, 24, 0),
    33: (1920, 1080, 25, 0), 34: (1920, 1080, 30, 0), 41: (1280, 720, 100, 0), 47: (1280, 720, 120, 0),
    60: (1280, 720, 24, 0), 61: (1280, 720, 25, 0), 62: (1280, 720, 30, 0), 63: (1920, 1080, 120, 0),
    64: (1920, 1080, 100, 0), 93: (3840, 2160, 24, 0), 94: (3840, 2160, 25, 0), 95: (3840, 2160, 30, 0),
    96: (3840, 2160, 50, 0), 97: (3840, 2160, 60, 0), 98: (4096, 2160, 24, 0), 99: (4096, 2160, 25, 0),
    100: (4096, 2160, 30, 0), 101: (4096, 2160, 50, 0), 102: (4096, 2160, 60, 0),
    114: (3840, 2160, 48, 0), 115: (4096, 2160, 48, 0), 117: (3840, 2160, 100, 0), 118: (3840, 2160, 120, 0),
}
LUMINANCE_CODE_BASE = 50.0  # CTA-861 HDR static metadata: nits = 50 * 2**(code/32)


def hex_bytes(b):
    return " ".join(f"{x:02x}" for x in b)


def pnp_letters(b8, b9):
    v = (b8 << 8) | b9
    return "".join(chr(64 + ((v >> s) & 31)) for s in (10, 5, 0))


def ascii_field(b):
    text = bytes(b).split(b"\n")[0].decode("ascii", "replace").rstrip()
    return text or None


def parse_dtd(d):
    """18-byte detailed timing descriptor -> dict (None when the pixel clock is 0)."""
    clock = (d[0] | d[1] << 8) * 10_000
    if clock == 0:
        return None
    h_act, h_blk = d[2] | ((d[4] & 0xF0) << 4), d[3] | ((d[4] & 0x0F) << 8)
    v_act, v_blk = d[5] | ((d[7] & 0xF0) << 4), d[6] | ((d[7] & 0x0F) << 8)
    h_mm, v_mm = d[12] | ((d[14] & 0xF0) << 4), d[13] | ((d[14] & 0x0F) << 8)
    total = (h_act + h_blk) * (v_act + v_blk)
    rate = Fraction(clock, total) if total else None
    return {"w": h_act, "h": v_act, "pixel_clock_hz": clock, "h_total": h_act + h_blk, "v_total": v_act + v_blk,
            "refresh": [rate.numerator, rate.denominator] if rate else None,
            "hz": round(float(rate), 4) if rate else None, "interlaced": bool(d[17] & 0x80),
            "image_mm": [h_mm, v_mm] if h_mm and v_mm else None}


def parse_range_limits(d, rev):
    """Display range limits descriptor (tag 0xFD)."""
    off = d[4]
    vmin, vmax, hmin, hmax = d[5], d[6], d[7], d[8]
    if rev >= 4:  # EDID 1.4 offsets: +255 Hz / kHz (bits 1:0 vertical, 3:2 horizontal)
        if off & 0x02:
            vmax += 255
            if off & 0x01:
                vmin += 255
        if off & 0x08:
            hmax += 255
            if off & 0x04:
                hmin += 255
    flag = d[10]
    return {"v_hz": [vmin, vmax], "h_khz": [hmin, hmax],
            "max_pixel_clock_mhz": d[9] * 10 if d[9] != 255 else None,
            "timing_flag": {0: "default_gtf", 1: "range_limits_only", 2: "secondary_gtf", 4: "cvt"}.get(flag, f"0x{flag:02x}")}


def luminance_nits(code):
    return round(LUMINANCE_CODE_BASE * 2 ** (code / 32.0), 4)


def parse_cta_blocks(data, out):
    """Walk CTA-861 data blocks (bytes between the 4-byte header and the first DTD)."""
    i = 0
    while i < len(data):
        tag, length = data[i] >> 5, data[i] & 0x1F
        payload = bytes(data[i + 1:i + 1 + length])
        i += 1 + length
        if tag == 2:  # video data block: short video descriptors
            for v in payload:
                vic = v & 0x7F if (v & 0x7F) <= 64 else v
                out.setdefault("cta_vics", []).append({"vic": vic, "native": bool(v & 0x80) and (v & 0x7F) <= 64})
        elif tag == 3 and len(payload) >= 3:  # vendor specific, OUI little-endian
            oui = payload[0] | payload[1] << 8 | payload[2] << 16
            x = payload[3:]
            if oui == 0x000C03:  # HDMI LLC
                out["hdmi_vsdb"] = {"phys_addr": f"{x[0] >> 4}.{x[0] & 15}.{x[1] >> 4}.{x[1] & 15}" if len(x) >= 2 else None,
                                    "max_tmds_mhz": x[3] * 5 if len(x) >= 4 else None}
            elif oui == 0xC45DD8:  # HDMI Forum VSDB
                out["hdmi_forum"] = parse_hf(x)
            elif oui == 0x00001A and len(x) >= 5:  # AMD FreeSync (layout reverse-engineered by edid-decode)
                out["amd_vsdb"] = {"version": f"{x[0]}.{x[1]}", "freesync_bit": bool(x[1] & 1), "min_hz": x[2],
                                   "max_hz": x[3], "flags_1x": f"0x{x[4]:02x}", "uses_mccs": bool(x[4] & 0xE6)}
            else:
                out.setdefault("other_vsdb", []).append(f"{oui:06x}")
        elif tag == 7 and payload:  # extended tag
            etag, x = payload[0], payload[1:]
            if etag == 0 and x:
                out["video_capability"] = {"rgb_quantization_selectable": bool(x[0] & 0x40),
                                           "ycc_quantization_selectable": bool(x[0] & 0x80)}
            elif etag == 5 and x:
                names = ["xvYCC601", "xvYCC709", "sYCC601", "opYCC601", "opRGB", "BT2020cYCC", "BT2020YCC", "BT2020RGB"]
                out["colorimetry"] = [n for k, n in enumerate(names) if x[0] & (1 << k)]
            elif etag == 6 and len(x) >= 2:
                hdr = {"eotf": [n for k, n in enumerate(["sdr_gamma", "hdr_gamma", "pq_st2084", "hlg"]) if x[0] & (1 << k)]}
                if len(x) >= 3:
                    hdr["max_nits"] = luminance_nits(x[2])
                if len(x) >= 4:
                    hdr["max_avg_nits"] = luminance_nits(x[3])
                if len(x) >= 5:
                    hdr["min_nits"] = round(hdr["max_nits"] * (x[4] / 255.0) ** 2 / 100.0, 4)
                out["hdr_static"] = hdr
            elif etag == 0x79 and len(x) >= 3:  # HDMI Forum SCDB: one reserved byte, then the VSDB layout
                out["hdmi_forum"] = parse_hf(x[1:])
            else:
                out.setdefault("ext_tags", []).append(etag)


def parse_hf(x):
    """HDMI Forum VSDB/SCDB payload after the OUI (x[0] = version). VRR fields exist only when long enough."""
    info = {"version": x[0] if x else None}
    if len(x) >= 3:
        info["max_tmds_mhz"] = x[1] * 5
        info["scdc"] = bool(x[2] & 0x80)
    if len(x) >= 4:
        info["max_frl_code"] = x[3] >> 4  # 0 = no FRL (HDMI 2.0 or older)
    if len(x) >= 5:
        info.update({"allm": bool(x[4] & 0x02), "fast_vactive": bool(x[4] & 0x04),
                     "cinema_vrr": bool(x[4] & 0x10), "mdelta": bool(x[4] & 0x20)})
    if len(x) >= 7:
        info["vrr_min_hz"] = x[5] & 0x3F
        info["vrr_max_hz"] = ((x[5] & 0xC0) << 2) | x[6]
    return info


def parse_cta_extension(block):
    rev, dtd_off, flags = block[1], block[2], block[3]
    out = {"revision": rev, "native_dtd_count": flags & 0x0F, "basic_audio": bool(flags & 0x40),
           "ycbcr444": bool(flags & 0x20), "ycbcr422": bool(flags & 0x10), "dtds": []}
    if dtd_off >= 4:
        parse_cta_blocks(block[4:dtd_off], out)
        pos = dtd_off
        while pos + 18 <= 127:
            dtd = parse_dtd(block[pos:pos + 18])
            if not dtd:
                break
            out["dtds"].append(dtd)
            pos += 18
    return out


def parse_displayid_extension(block):
    """DisplayID inside an EDID extension block (tag 0x70). Handles 1.x and 2.x data blocks."""
    d = block[1:]
    version, size = d[0], d[1]
    out = {"version": f"{version >> 4}.{version & 15}", "primary_use_case": d[2], "tags": [], "type7_timings": []}
    body = d[4:4 + size]
    v2 = version >= 0x20
    i = 0
    while i + 3 <= len(body):
        tag, brev, length = body[i], body[i + 1], body[i + 2]
        if tag == 0 and length == 0 and not v2:
            break
        payload = bytes(body[i + 3:i + 3 + length])
        i += 3 + length
        out["tags"].append(f"0x{tag:02x}")
        if tag in (0x01, 0x21) and len(payload) >= 8:  # display parameters
            unit = 1.0 if (v2 and brev & 0x80) else 0.1
            out["image_mm"] = [round((payload[0] | payload[1] << 8) * unit, 1), round((payload[2] | payload[3] << 8) * unit, 1)]
            out["native_px"] = [payload[4] | payload[5] << 8, payload[6] | payload[7] << 8]
        elif tag == 0x09 and len(payload) >= 15 and not v2:  # video timing range limits (1.x)
            out["range_limits_v1"] = {"v_hz": [payload[13], payload[14]], "h_khz": [payload[9], payload[10]],
                                      "discrete_frequency_device": bool(payload[17 - 3] & 0x10) if len(payload) > 14 else None}
        elif tag == 0x25 and len(payload) >= 9 and v2:  # dynamic video timing range limits (2.x): the VRR signal
            vmax = payload[7] | ((payload[8] & 3) << 8) if brev & 7 else payload[7]
            out["dynamic_range_limits"] = {"v_hz": [payload[6], vmax], "seamless_timing_change": bool(payload[8] & 0x80),
                                           "pixel_clock_khz": [1 + (payload[0] | payload[1] << 8 | payload[2] << 16),
                                                               1 + (payload[3] | payload[4] << 8 | payload[5] << 16)]}
        elif tag == 0x22 and v2:  # type VII detailed timings, 20 bytes each
            for k in range(0, len(payload) - 19, 20):
                t = payload[k:k + 20]
                clock_khz = 1 + (t[0] | t[1] << 8 | t[2] << 16)
                ha, hb = 1 + (t[4] | t[5] << 8), 1 + (t[6] | t[7] << 8)
                va, vb = 1 + (t[12] | t[13] << 8), 1 + (t[14] | t[15] << 8)
                rate = Fraction(clock_khz * 1000, (ha + hb) * (va + vb))
                out["type7_timings"].append({"w": ha, "h": va, "pixel_clock_hz": clock_khz * 1000,
                                             "refresh": [rate.numerator, rate.denominator], "hz": round(float(rate), 4),
                                             "preferred": bool(t[3] & 0x80)})
        elif tag == 0x29 and len(payload) >= 16 and v2:
            out["container_id"] = str(uuid.UUID(bytes=bytes(payload[:16])))
        elif tag == 0x81 and v2:  # CTA-861 data blocks carried inside DisplayID
            cta = {}
            parse_cta_blocks(payload, cta)
            out["cta_in_displayid"] = cta
    return out


def parse_edid(raw):
    """Parse a full EDID (base block + extensions). Never raises on odd extension content."""
    raw = bytes(raw)
    if len(raw) < 128 or raw[:8] != b"\x00\xff\xff\xff\xff\xff\xff\x00":
        return {"valid": False, "length": len(raw)}
    b = raw[:128]
    rev = b[19]
    e = {"valid": True, "length": len(raw), "hex": raw.hex(), "base_checksum_ok": sum(b) % 256 == 0,
         "base_sha256": hashlib.sha256(b).hexdigest(), "base_checksum_byte": b[127],
         "mfg": pnp_letters(b[8], b[9]), "product": f"{b[11] << 8 | b[10]:04X}",
         "serial_u32": int.from_bytes(b[12:16], "little"), "week": b[16], "year": b[17] + 1990 if b[17] else None,
         "version": f"{b[18]}.{rev}", "digital": bool(b[20] & 0x80),
         "size_cm": [b[21], b[22]], "features_byte": b[24], "continuous_frequency_flag": bool(b[24] & 1) if rev >= 4 else None,
         "gamma": round((b[23] + 100) / 100, 2) if b[23] != 255 else None, "extension_count": b[126]}

    def f(hi, lo, shift):
        return round(((hi << 2) | ((lo >> shift) & 3)) / 1024, 4)

    e["chromaticity"] = {"red": [f(b[27], b[25], 6), f(b[28], b[25], 4)], "green": [f(b[29], b[25], 2), f(b[30], b[25], 0)],
                         "blue": [f(b[31], b[26], 6), f(b[32], b[26], 4)], "white": [f(b[33], b[26], 2), f(b[34], b[26], 0)]}
    e["dtds"], e["name"], e["serial_text"], e["other_text"], e["range_limits"] = [], None, None, None, None
    for off in (54, 72, 90, 108):
        d = b[off:off + 18]
        if d[0] or d[1]:
            dtd = parse_dtd(d)
            if dtd:
                e["dtds"].append(dtd)
        elif d[3] == 0xFC:
            e["name"] = ascii_field(d[5:18])
        elif d[3] == 0xFF:
            e["serial_text"] = ascii_field(d[5:18])
        elif d[3] == 0xFE:
            e["other_text"] = ascii_field(d[5:18])
        elif d[3] == 0xFD:
            e["range_limits"] = parse_range_limits(d, rev)
    e["cta"], e["displayid"], e["extension_tags"] = None, None, []
    for k in range(1, min(b[126], len(raw) // 128 - 1) + 1):
        blk = raw[k * 128:(k + 1) * 128]
        e["extension_tags"].append(f"0x{blk[0]:02x}")
        try:
            if blk[0] == 0x02 and e["cta"] is None:
                e["cta"] = parse_cta_extension(blk)
            elif blk[0] == 0x70 and e["displayid"] is None:
                e["displayid"] = parse_displayid_extension(blk)
        except Exception as exc:  # noqa: BLE001 - odd vendor content must not break the report
            ERRORS.append({"section": f"edid-ext-{k}", "error": f"{type(exc).__name__}: {exc}"})
    return e


def windows_stable_id(e):
    """Rebuild Windows' stable monitor id: mfg + product + serial text (or 0) + _week_year_checksum (hex)."""
    return f"{e['mfg']}{e['product']}{e['serial_text'] or '0'}_{e['week']:02X}_{(e['year'] or 0):04X}_{e['base_checksum_byte']:02X}"


def serial_is_placeholder(e):
    text = (e.get("serial_text") or "").strip()
    if text and (set(text) <= set("01") or len(set(text)) == 1):
        return True
    return not text and e["serial_u32"] in (0, 1, 0x01010101, 0xFFFFFFFF)


def vrr_from_edid(e):
    """Every variable-refresh signal in one EDID, plus a verdict on whether the base-block range is a VRR range."""
    rl = e.get("range_limits")
    rev = int(e["version"].split(".")[1])
    valid_rule = bool(rl and rev >= 4 and e.get("continuous_frequency_flag") and rl["timing_flag"] == "range_limits_only")
    cta, did = e.get("cta") or {}, e.get("displayid") or {}
    amd = cta.get("amd_vsdb") or (did.get("cta_in_displayid") or {}).get("amd_vsdb")
    hf = cta.get("hdmi_forum") or (did.get("cta_in_displayid") or {}).get("hdmi_forum")
    hf_vrr = [hf["vrr_min_hz"], hf["vrr_max_hz"]] if hf and hf.get("vrr_max_hz") else None
    dyn = did.get("dynamic_range_limits")
    sources, ranges = [], []
    if dyn:
        sources.append("displayid_dynamic_range_limits")
        ranges.append(dyn["v_hz"])
    if amd and amd["freesync_bit"]:
        sources.append("amd_freesync_vsdb")
        ranges.append([amd["min_hz"], amd["max_hz"]])
    if hf_vrr:
        sources.append("hdmi_forum_vrr")
        ranges.append(hf_vrr)
    if valid_rule:
        sources.append("edid_range_limits_continuous")
        ranges.append(rl["v_hz"])
    return {"declared": bool(sources), "sources": sources, "ranges_hz": ranges,
            "range_hz": ranges[0] if ranges else None,
            "edid_range_limits": rl, "edid_range_is_vrr_by_linux_rule": valid_rule,
            "continuous_frequency_flag": e.get("continuous_frequency_flag"),
            "amd_vsdb": amd, "hdmi_forum": hf, "displayid_dynamic_range": dyn}


COMMON_DIAGONALS = [10.1, 11.6, 13.3, 14, 15.6, 16, 17.3, 18.5, 19, 19.5, 21.5, 22, 23, 23.6, 23.8, 24, 24.5, 25, 27, 28, 29,
                    31.5, 32, 34, 35, 38, 40, 42, 43, 45, 48, 49, 55]


def physical_size(e, native_w, native_h):
    """Physical size from every EDID source with sanity flags. Rule: square pixels, scale from the longer axis."""
    did, dtds = e.get("displayid") or {}, e.get("dtds") or []
    sources = {}
    if did.get("image_mm"):
        sources["displayid_params_mm"] = did["image_mm"]
    if dtds and dtds[0].get("image_mm"):
        sources["first_dtd_mm"] = dtds[0]["image_mm"]
    cm = e["size_cm"]
    if cm[0] and cm[1]:
        sources["base_block_cm_x10_mm"] = [cm[0] * 10, cm[1] * 10]
    flags = []
    if not sources:
        flags.append("no_physical_size_in_edid")
        return {"sources_mm": sources, "flags": flags, "best_source": None}
    order = ["displayid_params_mm", "first_dtd_mm", "base_block_cm_x10_mm"]
    best = next(k for k in order if k in sources)
    bw, bh = sources[best]
    if best == "first_dtd_mm" and bw % 10 == 0 and bh % 10 == 0:
        flags.append("dtd_size_is_whole_cm")  # only cm precision: +-0.5 cm rounding
    if best == "base_block_cm_x10_mm":
        flags.append("cm_precision_only")
    px_w, px_h = (native_w, native_h) if native_w and native_h else (0, 0)
    if native_w and native_h and (bw >= bh) != (px_w >= px_h):
        bw, bh = bh, bw  # EDID size is in the panel's native orientation, pixels may be rotated
    out = {"best_source": best, "sources_mm": sources, "flags": flags}
    if px_w and px_h:
        pitch_x, pitch_y = bw / px_w, bh / px_h
        out["pixel_pitch_mm"] = [round(pitch_x, 4), round(pitch_y, 4)]
        skew = abs(pitch_x - pitch_y) / max(pitch_x, pitch_y) * 100
        out["pixel_skew_pct"] = round(skew, 2)
        if skew > 1.5:
            flags.append("non_square_pixels_gt_1.5pct")
        pitch = (bw / px_w) if bw >= bh else (bh / px_h)  # longer axis wins
        sw, sh = px_w * pitch, px_h * pitch
        out["size_mm_square_pixel_rule"] = [round(sw, 1), round(sh, 1)]
        diag_in = (sw ** 2 + sh ** 2) ** 0.5 / 25.4
        near = min(COMMON_DIAGONALS, key=lambda d: abs(d - diag_in))
        out["diagonal_in"] = round(diag_in, 2)
        out["nearest_common_diagonal_in"] = near
        out["diagonal_off_nominal_pct"] = round((diag_in - near) / near * 100, 2)
    if "first_dtd_mm" in sources and "base_block_cm_x10_mm" in sources:
        a, c = sources["first_dtd_mm"], sources["base_block_cm_x10_mm"]
        if max(abs(a[0] - c[0]) / c[0], abs(a[1] - c[1]) / c[1]) > 0.03:
            flags.append("dtd_vs_base_block_differ_gt_3pct")
    if (bw, bh) in ((160, 90), (160, 100), (1600, 900)):
        flags.append("looks_like_placeholder_size")
    return out


def edid_summary(e):
    """Compact view of a parsed EDID for history lists and the per-monitor block."""
    if not e.get("valid"):
        return {"valid": False}
    first = e["dtds"][0] if e["dtds"] else None
    cta, did = e.get("cta") or {}, e.get("displayid") or {}
    preferred = next((t for t in did.get("type7_timings", []) if t.get("preferred")), first)
    vics = [dict(v, **dict(zip(("w", "h", "hz", "interlaced"), CTA_VICS.get(v["vic"], ())))) for v in cta.get("cta_vics", [])]
    declared = {"edid_dtds": e["dtds"] + cta.get("dtds", []), "displayid_type7": did.get("type7_timings", []), "cta_vics": vics}
    nw, nh = (preferred["w"], preferred["h"]) if preferred else (None, None)
    return {"valid": True, "mfg": e["mfg"], "product": e["product"], "serial_u32": e["serial_u32"],
            "serial_text": e["serial_text"], "serial_is_placeholder": serial_is_placeholder(e), "name": e["name"],
            "other_text": e["other_text"], "week": e["week"], "year": e["year"], "version": e["version"],
            "base_checksum_ok": e["base_checksum_ok"], "base_sha256": e["base_sha256"],
            "windows_stable_id": windows_stable_id(e), "first_dtd": first, "preferred_timing": preferred,
            "extension_tags": e["extension_tags"], "chromaticity": e["chromaticity"], "gamma": e["gamma"],
            "hdr_static": cta.get("hdr_static") or (did.get("cta_in_displayid") or {}).get("hdr_static"),
            "colorimetry": cta.get("colorimetry"), "displayid_container_uuid": did.get("container_id"),
            "vrr": vrr_from_edid(e), "physical_size": physical_size(e, nw, nh), "declared_timings": declared,
            "hdmi_vsdb": cta.get("hdmi_vsdb"), "edid_hex": e["hex"]}


# --------------------------------------------------------------------------------------------------
# Registry: EDIDs (current and past), Windows monitor data store
# --------------------------------------------------------------------------------------------------

ENUM_DISPLAY = r"SYSTEM\CurrentControlSet\Enum\DISPLAY"
GRAPHICS = r"SYSTEM\CurrentControlSet\Control\GraphicsDrivers"


def reg_subkeys(root, path):
    try:
        with winreg.OpenKey(root, path) as k:
            return [winreg.EnumKey(k, i) for i in range(winreg.QueryInfoKey(k)[0])]
    except OSError:
        return []


def reg_value(root, path, name):
    try:
        with winreg.OpenKey(root, path) as k:
            return winreg.QueryValueEx(k, name)[0]
    except OSError:
        return None


def filetime_iso(v):
    return (datetime.datetime(1601, 1, 1) + datetime.timedelta(microseconds=v // 10)).strftime("%Y-%m-%dT%H:%M:%SZ")


def monitor_data_store_ids():
    ids = set()
    for sub in ("MonitorDataStore",):
        ids.update(reg_subkeys(winreg.HKEY_LOCAL_MACHINE, f"{GRAPHICS}\\{sub}"))
    for name in reg_subkeys(winreg.HKEY_LOCAL_MACHINE, f"{GRAPHICS}\\ScaleFactors"):
        ids.add(name.split("^")[0])
    return ids


def registry_history(active_paths):
    """Every monitor Windows has ever enumerated on this PC, with its EDID summary and last-write time."""
    known = monitor_data_store_ids()
    rows = []
    for dev in reg_subkeys(winreg.HKEY_LOCAL_MACHINE, ENUM_DISPLAY):
        for inst in reg_subkeys(winreg.HKEY_LOCAL_MACHINE, f"{ENUM_DISPLAY}\\{dev}"):
            path = f"{ENUM_DISPLAY}\\{dev}\\{inst}"
            edid_raw = reg_value(winreg.HKEY_LOCAL_MACHINE, path + r"\Device Parameters", "EDID")
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as k:
                    last = filetime_iso(winreg.QueryInfoKey(k)[2])
            except OSError:
                last = None
            row = {"pnp_id": dev, "instance": inst, "key_last_write_utc": last,
                   "edid_override_present": "EDID_OVERRIDE" in reg_subkeys(winreg.HKEY_LOCAL_MACHINE, path + r"\Device Parameters"),
                   "container_id": reg_value(winreg.HKEY_LOCAL_MACHINE, path, "ContainerID"),
                   "friendly_name": reg_value(winreg.HKEY_LOCAL_MACHINE, path, "FriendlyName"),
                   "present_now": f"{dev}\\{inst}".lower() in active_paths}
            if edid_raw:
                s = edid_summary(parse_edid(edid_raw))
                row["edid"] = s
                if s.get("valid"):
                    row["known_to_windows_monitor_store"] = s["windows_stable_id"] in known
            rows.append(row)
    return sorted(rows, key=lambda r: r["key_last_write_utc"] or "", reverse=True)


# --------------------------------------------------------------------------------------------------
# CCD (QueryDisplayConfig / DisplayConfigGetDeviceInfo); structs match wingdi.h in SDK 10.0.26100.0
# --------------------------------------------------------------------------------------------------

user32, gdi32, shcore = ctypes.windll.user32, ctypes.windll.gdi32, ctypes.windll.shcore
u32 = ctypes.c_uint32
QDC_ALL_PATHS, QDC_ONLY_ACTIVE_PATHS = 0x1, 0x2
TECH = {-1: "OTHER", 0: "HD15", 1: "SVIDEO", 2: "COMPOSITE", 3: "COMPONENT", 4: "DVI", 5: "HDMI", 6: "LVDS", 8: "D_JPN",
        9: "SDI", 10: "DISPLAYPORT_EXTERNAL", 11: "DISPLAYPORT_EMBEDDED", 12: "UDI_EXTERNAL", 13: "UDI_EMBEDDED",
        14: "SDTVDONGLE", 15: "MIRACAST", 16: "INDIRECT_WIRED", 17: "INDIRECT_VIRTUAL", 18: "DISPLAYPORT_USB_TUNNEL",
        -2147483648: "INTERNAL"}
ROTATION = {1: "identity", 2: "rotate90", 3: "rotate180", 4: "rotate270"}
SCALING = {1: "identity", 2: "centered", 3: "stretched", 4: "aspect_ratio_centered_max", 5: "custom", 128: "preferred"}
SCAN = {0: "unspecified", 1: "progressive", 2: "interlaced", 3: "interlaced_lower_field_first"}
COLOR_ENC = {0: "RGB", 1: "YCbCr444", 2: "YCbCr422", 3: "YCbCr420", 4: "intensity"}
COLOR_MODE = {0: "SDR", 1: "WCG", 2: "HDR"}


class LUID(ctypes.Structure):
    _fields_ = [("Low", wt.DWORD), ("High", wt.LONG)]

    def text(self):
        return f"0x{self.High & 0xFFFFFFFF:x}:0x{self.Low:x}"


class RATIONAL(ctypes.Structure):
    _fields_ = [("Numerator", u32), ("Denominator", u32)]


class REGION2D(ctypes.Structure):
    _fields_ = [("cx", u32), ("cy", u32)]


class VIDEO_SIGNAL_INFO(ctypes.Structure):
    _fields_ = [("pixelRate", ctypes.c_uint64), ("hSyncFreq", RATIONAL), ("vSyncFreq", RATIONAL), ("activeSize", REGION2D),
                ("totalSize", REGION2D), ("videoStandard", u32), ("scanLineOrdering", u32)]


class SOURCE_MODE(ctypes.Structure):
    _fields_ = [("width", u32), ("height", u32), ("pixelFormat", u32), ("x", ctypes.c_long), ("y", ctypes.c_long)]


class DESKTOP_IMAGE_INFO(ctypes.Structure):
    _fields_ = [("PathSourceSize", wt.POINT), ("DesktopImageRegion", wt.RECT), ("DesktopImageClip", wt.RECT)]


class MODE_UNION(ctypes.Union):
    _fields_ = [("targetMode", VIDEO_SIGNAL_INFO), ("sourceMode", SOURCE_MODE), ("desktopImageInfo", DESKTOP_IMAGE_INFO)]


class MODE_INFO(ctypes.Structure):
    _fields_ = [("infoType", u32), ("id", u32), ("adapterId", LUID), ("u", MODE_UNION)]


class PATH_SOURCE_INFO(ctypes.Structure):
    _fields_ = [("adapterId", LUID), ("id", u32), ("modeInfoIdx", u32), ("statusFlags", u32)]


class PATH_TARGET_INFO(ctypes.Structure):
    _fields_ = [("adapterId", LUID), ("id", u32), ("modeInfoIdx", u32), ("outputTechnology", ctypes.c_int32),
                ("rotation", u32), ("scaling", u32), ("refreshRate", RATIONAL), ("scanLineOrdering", u32),
                ("targetAvailable", wt.BOOL), ("statusFlags", u32)]


class PATH_INFO(ctypes.Structure):
    _fields_ = [("sourceInfo", PATH_SOURCE_INFO), ("targetInfo", PATH_TARGET_INFO), ("flags", u32)]


class DEVINFO_HEADER(ctypes.Structure):
    _fields_ = [("type", u32), ("size", u32), ("adapterId", LUID), ("id", u32)]


class TARGET_NAME(ctypes.Structure):
    _fields_ = [("header", DEVINFO_HEADER), ("flags", u32), ("outputTechnology", ctypes.c_int32),
                ("edidManufactureId", ctypes.c_uint16), ("edidProductCodeId", ctypes.c_uint16), ("connectorInstance", u32),
                ("monitorFriendlyDeviceName", wt.WCHAR * 64), ("monitorDevicePath", wt.WCHAR * 128)]


class SOURCE_NAME(ctypes.Structure):
    _fields_ = [("header", DEVINFO_HEADER), ("viewGdiDeviceName", wt.WCHAR * 32)]


class ADAPTER_NAME(ctypes.Structure):
    _fields_ = [("header", DEVINFO_HEADER), ("adapterDevicePath", wt.WCHAR * 128)]


class ADV_COLOR(ctypes.Structure):
    _fields_ = [("header", DEVINFO_HEADER), ("value", u32), ("colorEncoding", u32), ("bitsPerColorChannel", u32)]


class ADV_COLOR2(ctypes.Structure):
    _fields_ = [("header", DEVINFO_HEADER), ("value", u32), ("colorEncoding", u32), ("bitsPerColorChannel", u32),
                ("activeColorMode", u32)]


class SDR_WHITE(ctypes.Structure):
    _fields_ = [("header", DEVINFO_HEADER), ("SDRWhiteLevel", wt.ULONG)]


user32.GetDisplayConfigBufferSizes.argtypes = [u32, ctypes.POINTER(u32), ctypes.POINTER(u32)]
user32.QueryDisplayConfig.argtypes = [u32, ctypes.POINTER(u32), ctypes.POINTER(PATH_INFO), ctypes.POINTER(u32),
                                      ctypes.POINTER(MODE_INFO), ctypes.c_void_p]
user32.DisplayConfigGetDeviceInfo.argtypes = [ctypes.c_void_p]


def query_display_config(flags):
    for _ in range(5):  # the topology may change between the two calls
        n_paths, n_modes = u32(), u32()
        rc = user32.GetDisplayConfigBufferSizes(flags, ctypes.byref(n_paths), ctypes.byref(n_modes))
        if rc:
            raise OSError(f"GetDisplayConfigBufferSizes rc={rc}")
        paths, modes = (PATH_INFO * n_paths.value)(), (MODE_INFO * n_modes.value)()
        rc = user32.QueryDisplayConfig(flags, ctypes.byref(n_paths), paths, ctypes.byref(n_modes), modes, None)
        if rc == 0:
            return list(paths)[:n_paths.value], list(modes)[:n_modes.value]
        if rc != 122:  # ERROR_INSUFFICIENT_BUFFER: retry
            raise OSError(f"QueryDisplayConfig rc={rc}")
    raise OSError("QueryDisplayConfig kept returning ERROR_INSUFFICIENT_BUFFER")


def device_info(cls, kind, luid, ident):
    s = cls()
    s.header.type, s.header.size, s.header.adapterId, s.header.id = kind, ctypes.sizeof(cls), luid, ident
    rc = user32.DisplayConfigGetDeviceInfo(ctypes.byref(s))
    return s if rc == 0 else None


def rat(r):
    return {"num": r.Numerator, "den": r.Denominator, "hz": round(r.Numerator / r.Denominator, 6) if r.Denominator else None}


def color_state(luid, ident):
    out = {}
    v1 = device_info(ADV_COLOR, 9, luid, ident)
    if v1:
        out["v1"] = {"supported": bool(v1.value & 1), "enabled": bool(v1.value & 2), "wide_color_enforced": bool(v1.value & 4),
                     "force_disabled": bool(v1.value & 8), "encoding": COLOR_ENC.get(v1.colorEncoding),
                     "bits_per_channel": v1.bitsPerColorChannel}
    v2 = device_info(ADV_COLOR2, 15, luid, ident)  # Windows 11 SDK structure; absent on older builds
    if v2:
        out["v2"] = {"advanced_color_supported": bool(v2.value & 1), "advanced_color_active": bool(v2.value & 2),
                     "limited_by_policy": bool(v2.value & 8), "hdr_supported": bool(v2.value & 16),
                     "hdr_user_enabled": bool(v2.value & 32), "wide_color_supported": bool(v2.value & 64),
                     "wide_color_user_enabled": bool(v2.value & 128), "active_mode": COLOR_MODE.get(v2.activeColorMode),
                     "encoding": COLOR_ENC.get(v2.colorEncoding), "bits_per_channel": v2.bitsPerColorChannel}
    w = device_info(SDR_WHITE, 11, luid, ident)
    if w:
        out["sdr_white_nits"] = round(w.SDRWhiteLevel / 1000 * 80, 1)
    return out


def edid_registry_path(device_path):
    parts = device_path.split("#")  # \\?\DISPLAY#ZZY2150#5&2bc6f227&1&UID4352#{guid}
    return (parts[1], parts[2]) if len(parts) >= 3 else (None, None)


def read_target(luid, ident):
    tn = device_info(TARGET_NAME, 2, luid, ident)
    if not tn:
        return {}
    dev, inst = edid_registry_path(tn.monitorDevicePath)
    base = f"{ENUM_DISPLAY}\\{dev}\\{inst}" if dev else None
    edid_raw = reg_value(winreg.HKEY_LOCAL_MACHINE, base + r"\Device Parameters", "EDID") if base else None
    override = "EDID_OVERRIDE" in reg_subkeys(winreg.HKEY_LOCAL_MACHINE, base + r"\Device Parameters") if base else False
    return {"friendly_name": tn.monitorFriendlyDeviceName or None, "device_path": tn.monitorDevicePath, "edid_override_present": override,
            "pnp_id": dev, "pnp_instance": inst, "connector_instance": tn.connectorInstance,
            "friendly_name_from_edid": bool(tn.flags & 1), "edid_ids_valid": bool(tn.flags & 4),
            "edid_ids_from_ccd": f"{(tn.edidManufactureId >> 8) | ((tn.edidManufactureId & 0xFF) << 8):04x}/{tn.edidProductCodeId:04x}",
            "container_id": reg_value(winreg.HKEY_LOCAL_MACHINE, base, "ContainerID") if base else None,
            "edid_raw": edid_raw}


# --------------------------------------------------------------------------------------------------
# GDI modes, DPI, DXGI (exact rational modes, output colour desc, tearing support)
# --------------------------------------------------------------------------------------------------

class DISPLAY_DEVICEW(ctypes.Structure):
    _fields_ = [("cb", wt.DWORD), ("DeviceName", wt.WCHAR * 32), ("DeviceString", wt.WCHAR * 128),
                ("StateFlags", wt.DWORD), ("DeviceID", wt.WCHAR * 128), ("DeviceKey", wt.WCHAR * 128)]


class DEVMODEW(ctypes.Structure):
    _fields_ = [("dmDeviceName", wt.WCHAR * 32), ("dmSpecVersion", wt.WORD), ("dmDriverVersion", wt.WORD),
                ("dmSize", wt.WORD), ("dmDriverExtra", wt.WORD), ("dmFields", wt.DWORD), ("dmPositionX", wt.LONG),
                ("dmPositionY", wt.LONG), ("dmDisplayOrientation", wt.DWORD), ("dmDisplayFixedOutput", wt.DWORD),
                ("dmColor", ctypes.c_short), ("dmDuplex", ctypes.c_short), ("dmYResolution", ctypes.c_short),
                ("dmTTOption", ctypes.c_short), ("dmCollate", ctypes.c_short), ("dmFormName", wt.WCHAR * 32),
                ("dmLogPixels", wt.WORD), ("dmBitsPerPel", wt.DWORD), ("dmPelsWidth", wt.DWORD), ("dmPelsHeight", wt.DWORD),
                ("dmDisplayFlags", wt.DWORD), ("dmDisplayFrequency", wt.DWORD), ("dmICMMethod", wt.DWORD),
                ("dmICMIntent", wt.DWORD), ("dmMediaType", wt.DWORD), ("dmDitherType", wt.DWORD), ("dmReserved1", wt.DWORD),
                ("dmReserved2", wt.DWORD), ("dmPanningWidth", wt.DWORD), ("dmPanningHeight", wt.DWORD)]


user32.EnumDisplaySettingsExW.argtypes = [wt.LPCWSTR, wt.DWORD, ctypes.POINTER(DEVMODEW), wt.DWORD]
ENUM_CURRENT, ENUM_REGISTRY, EDS_RAWMODE = 0xFFFFFFFF, 0xFFFFFFFE, 0x2


def devmode(device, index, flags=0):
    dm = DEVMODEW()
    dm.dmSize = ctypes.sizeof(dm)
    return dm if user32.EnumDisplaySettingsExW(device, index, ctypes.byref(dm), flags) else None


def dm_dict(dm):
    return {"w": dm.dmPelsWidth, "h": dm.dmPelsHeight, "hz_int": dm.dmDisplayFrequency, "bpp": dm.dmBitsPerPel,
            "orientation_deg": dm.dmDisplayOrientation * 90, "fixed_output": dm.dmDisplayFixedOutput,
            "flags": dm.dmDisplayFlags, "x": dm.dmPositionX, "y": dm.dmPositionY}


def gdi_modes(device):
    seen, i = set(), 0
    while True:
        dm = devmode(device, i, EDS_RAWMODE)
        if not dm:
            break
        i += 1
        if dm.dmBitsPerPel == 32:
            seen.add((dm.dmPelsWidth, dm.dmPelsHeight, dm.dmDisplayFrequency, dm.dmDisplayFixedOutput))
    return sorted(seen, reverse=True)


def dpi_info(x, y):
    hmon = user32.MonitorFromPoint(wt.POINT(x, y), 0)
    if not hmon:
        return None
    eff, raw = (u32(), u32()), (u32(), u32())
    out = {}
    if shcore.GetDpiForMonitor(ctypes.c_void_p(hmon), 0, ctypes.byref(eff[0]), ctypes.byref(eff[1])) == 0:
        out["effective_dpi"] = eff[0].value
        out["scale_percent"] = round(eff[0].value / 96 * 100)
    if shcore.GetDpiForMonitor(ctypes.c_void_p(hmon), 2, ctypes.byref(raw[0]), ctypes.byref(raw[1])) == 0:
        out["raw_dpi"] = [raw[0].value, raw[1].value]
    return out


def gdi_device_caps(device):
    hdc = gdi32.CreateDCW(device, None, None, None)
    if not hdc:
        return None
    try:
        return {"horzsize_mm": gdi32.GetDeviceCaps(hdc, 4), "vertsize_mm": gdi32.GetDeviceCaps(hdc, 6)}
    finally:
        gdi32.DeleteDC(hdc)


class GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16), ("Data3", ctypes.c_uint16), ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def parse(cls, text):
        u = uuid.UUID(text)
        return cls(u.time_low, u.time_mid, u.time_hi_version, (ctypes.c_ubyte * 8)(*u.bytes[8:]))


class DXGI_MODE_DESC(ctypes.Structure):
    _fields_ = [("Width", u32), ("Height", u32), ("Num", u32), ("Den", u32), ("Format", u32), ("Scanline", u32), ("Scaling", u32)]


class DXGI_OUTPUT_DESC1(ctypes.Structure):
    _fields_ = [("DeviceName", wt.WCHAR * 32), ("DesktopCoordinates", wt.RECT), ("AttachedToDesktop", wt.BOOL),
                ("Rotation", u32), ("Monitor", ctypes.c_void_p), ("BitsPerColor", u32), ("ColorSpace", u32),
                ("Red", ctypes.c_float * 2), ("Green", ctypes.c_float * 2), ("Blue", ctypes.c_float * 2),
                ("White", ctypes.c_float * 2), ("MinLuminance", ctypes.c_float), ("MaxLuminance", ctypes.c_float),
                ("MaxFullFrameLuminance", ctypes.c_float)]


class DXGI_OUTPUT_DESC(ctypes.Structure):
    _fields_ = [("DeviceName", wt.WCHAR * 32), ("DesktopCoordinates", wt.RECT), ("AttachedToDesktop", wt.BOOL),
                ("Rotation", u32), ("Monitor", ctypes.c_void_p)]


class DXGI_ADAPTER_DESC1(ctypes.Structure):
    _fields_ = [("Description", wt.WCHAR * 128), ("VendorId", u32), ("DeviceId", u32), ("SubSysId", u32), ("Revision", u32),
                ("DedicatedVideoMemory", ctypes.c_size_t), ("DedicatedSystemMemory", ctypes.c_size_t),
                ("SharedSystemMemory", ctypes.c_size_t), ("AdapterLuid", LUID), ("Flags", u32)]


PP = ctypes.POINTER(ctypes.c_void_p)
HRESULT = ctypes.c_long
IID_IDXGIFactory1 = GUID.parse("770aae78-f26f-4dba-a829-253c83d1b387")
IID_IDXGIFactory5 = GUID.parse("7632e1f5-ee65-4dca-87fd-84cd75f8838d")
IID_IDXGIOutput6 = GUID.parse("068346e8-aaec-4b84-add7-137f513f77a1")
VENDORS = {0x10DE: "NVIDIA", 0x1002: "AMD", 0x8086: "Intel", 0x1414: "Microsoft"}
DXGI_COLORSPACE = {0: "sRGB full", 1: "linear sRGB", 2: "linear sRGB studio", 12: "BT.2020 full (PQ, G2084)",
                   14: "BT.2020 G22 full", 13: "BT.2020 G22 studio", 20: "BT.2020 studio (PQ)"}
DXGI_ROT = {0: "unspecified", 1: "identity", 2: "rotate90", 3: "rotate180", 4: "rotate270"}


def com(ptr, slot, *argtypes, restype=HRESULT):
    """Callable for vtable slot `slot` of the COM object at `ptr` (slot numbers verified in the SDK headers)."""
    vtable = ctypes.cast(ptr, ctypes.POINTER(PP))[0]
    fn = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtable[slot])
    return lambda *args: fn(ptr, *args)


def release(ptr):
    if ptr and ptr.value:
        com(ptr, 2, restype=ctypes.c_ulong)()


def qi(ptr, iid):
    out = ctypes.c_void_p()
    return out if com(ptr, 0, ctypes.POINTER(GUID), PP)(ctypes.byref(iid), ctypes.byref(out)) == 0 else None


def dxgi_report():
    """Adapters, per-output exact modes and colour description, and the system-wide tearing flag."""
    create = ctypes.windll.dxgi.CreateDXGIFactory1
    create.argtypes, create.restype = [ctypes.POINTER(GUID), PP], HRESULT
    factory = ctypes.c_void_p()
    if create(ctypes.byref(IID_IDXGIFactory1), ctypes.byref(factory)) != 0:
        raise OSError("CreateDXGIFactory1 failed")
    out = {"adapters": [], "allow_tearing_supported": None}
    f5 = qi(factory, IID_IDXGIFactory5)
    if f5:
        flag = wt.BOOL()
        if com(f5, 28, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint)(0, ctypes.byref(flag), 4) == 0:
            out["allow_tearing_supported"] = bool(flag.value)
        release(f5)
    a = 0
    while True:
        adapter = ctypes.c_void_p()
        if com(factory, 12, ctypes.c_uint, PP)(a, ctypes.byref(adapter)) != 0:
            break
        a += 1
        desc = DXGI_ADAPTER_DESC1()
        com(adapter, 10, ctypes.POINTER(DXGI_ADAPTER_DESC1))(ctypes.byref(desc))
        row = {"description": desc.Description, "vendor": VENDORS.get(desc.VendorId, f"0x{desc.VendorId:04x}"),
               "vendor_id": f"0x{desc.VendorId:04x}", "device_id": f"0x{desc.DeviceId:04x}",
               "subsys_id": f"0x{desc.SubSysId:08x}", "revision": desc.Revision, "luid": desc.AdapterLuid.text(),
               "dedicated_vram_mb": desc.DedicatedVideoMemory // (1 << 20), "software": bool(desc.Flags & 2), "outputs": []}
        o = 0
        while True:
            output = ctypes.c_void_p()
            if com(adapter, 7, ctypes.c_uint, PP)(o, ctypes.byref(output)) != 0:
                break
            o += 1
            row["outputs"].append(dxgi_output(output))
            release(output)
        out["adapters"].append(row)
        release(adapter)
    release(factory)
    return out


def dxgi_output(output):
    d = DXGI_OUTPUT_DESC()
    com(output, 7, ctypes.POINTER(DXGI_OUTPUT_DESC))(ctypes.byref(d))
    res = {"device": d.DeviceName, "attached": bool(d.AttachedToDesktop), "rotation": DXGI_ROT.get(d.Rotation),
           "rect": [d.DesktopCoordinates.left, d.DesktopCoordinates.top, d.DesktopCoordinates.right, d.DesktopCoordinates.bottom]}
    o6 = qi(output, IID_IDXGIOutput6)
    if o6:
        d1 = DXGI_OUTPUT_DESC1()
        if com(o6, 27, ctypes.POINTER(DXGI_OUTPUT_DESC1))(ctypes.byref(d1)) == 0:
            res["desc1"] = {"bits_per_color": d1.BitsPerColor, "color_space": DXGI_COLORSPACE.get(d1.ColorSpace, d1.ColorSpace),
                            "red": [round(v, 4) for v in d1.Red], "green": [round(v, 4) for v in d1.Green],
                            "blue": [round(v, 4) for v in d1.Blue], "white": [round(v, 4) for v in d1.White],
                            "min_nits": round(d1.MinLuminance, 4), "max_nits": round(d1.MaxLuminance, 2),
                            "max_full_frame_nits": round(d1.MaxFullFrameLuminance, 2)}
        release(o6)
    if d.AttachedToDesktop:  # modes of an output that is not on the desktop are not meaningful
        count = ctypes.c_uint(0)
        fn = com(output, 8, ctypes.c_uint, ctypes.c_uint, ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(DXGI_MODE_DESC))
        fmt, flags = 87, 1  # B8G8R8A8_UNORM, include interlaced; no scaled modes
        if fn(fmt, flags, ctypes.byref(count), None) == 0 and count.value:
            buf = (DXGI_MODE_DESC * count.value)()
            if fn(fmt, flags, ctypes.byref(count), buf) == 0:
                res["modes"] = sorted({(m.Width, m.Height, m.Num, m.Den, m.Scanline) for m in buf[:count.value]}, reverse=True)
    return res


def winrt_report():
    """Optional: DisplayMonitor + DisplayTarget.StableMonitorId through Windows PowerShell 5.1 (WinRT)."""
    ps = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, $type) { $t = $asTask.MakeGenericMethod($type).Invoke($null, @($op)); $t.Wait(-1) | Out-Null; $t.Result }
[void][Windows.Devices.Enumeration.DeviceInformation, Windows.Devices.Enumeration, ContentType=WindowsRuntime]
[void][Windows.Devices.Display.DisplayMonitor, Windows.Devices.Display, ContentType=WindowsRuntime]
$monitors = @()
$devs = Await ([Windows.Devices.Enumeration.DeviceInformation]::FindAllAsync([Windows.Devices.Display.DisplayMonitor]::GetDeviceSelector())) ([Windows.Devices.Enumeration.DeviceInformationCollection])
foreach ($d in $devs) {
  $m = Await ([Windows.Devices.Display.DisplayMonitor]::FromInterfaceIdAsync($d.Id)) ([Windows.Devices.Display.DisplayMonitor])
  $monitors += [pscustomobject]@{ device_id = $m.DeviceId; display_name = $m.DisplayName; connection_kind = "$($m.ConnectionKind)"
    physical_connector = "$($m.PhysicalConnector)"; usage_kind = "$($m.UsageKind)"
    native_px = @($m.NativeResolutionInRawPixels.Width, $m.NativeResolutionInRawPixels.Height)
    size_in = @($m.PhysicalSizeInInches.Width, $m.PhysicalSizeInInches.Height); raw_dpi = @($m.RawDpiX, $m.RawDpiY)
    min_nits = $m.MinLuminanceInNits; max_nits = $m.MaxLuminanceInNits; max_avg_nits = $m.MaxAverageFullFrameLuminanceInNits
    adapter_luid_low = $m.DisplayAdapterId.LowPart; adapter_target_id = $m.DisplayAdapterTargetId
    dolby_vision_hdr = $m.IsDolbyVisionSupportedInHdrMode }
}
$targets = @()
try {
  [void][Windows.Devices.Display.Core.DisplayManager, Windows.Devices.Display.Core, ContentType=WindowsRuntime]
  $dm = [Windows.Devices.Display.Core.DisplayManager]::Create([Windows.Devices.Display.Core.DisplayManagerOptions]::None)
  foreach ($t in $dm.GetCurrentTargets()) {
    if ($t.IsConnected) { $targets += [pscustomobject]@{ stable_monitor_id = $t.StableMonitorId; target_id = $t.AdapterRelativeId
      adapter_luid_low = $t.Adapter.Id.LowPart; device_path = $t.DeviceInterfacePath; usage_kind = "$($t.UsageKind)" } }
  }
  $dm.Dispose()
} catch { }
[pscustomobject]@{ display_monitors = $monitors; connected_targets = $targets } | ConvertTo-Json -Depth 5 -Compress
"""
    exe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    if not os.path.exists(exe):
        raise FileNotFoundError("Windows PowerShell 5.1 not found")
    encoded = base64.b64encode(ps.encode("utf-16-le")).decode("ascii")
    proc = subprocess.run([exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded],
                          capture_output=True, encoding="utf-8", errors="replace", timeout=60)
    if proc.returncode != 0 or not proc.stdout.strip():
        raise RuntimeError(f"powershell rc={proc.returncode}: {proc.stderr.strip()[:200]}")
    data = json.loads(proc.stdout.strip().splitlines()[-1])
    for key in ("display_monitors", "connected_targets"):
        if isinstance(data.get(key), dict):  # ConvertTo-Json unwraps single-item arrays
            data[key] = [data[key]]
        data[key] = data.get(key) or []
    return data


# --------------------------------------------------------------------------------------------------
# Assemble the report
# --------------------------------------------------------------------------------------------------

def system_info():
    v = sys.getwindowsversion()
    cv = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion"
    return {"windows": f"{v.major}.{v.minor}.{v.build}", "family": "Windows 11" if v.build >= 22000 else "Windows 10", "ubr": reg_value(winreg.HKEY_LOCAL_MACHINE, cv, "UBR"),
            "display_version": reg_value(winreg.HKEY_LOCAL_MACHINE, cv, "DisplayVersion"),
            "python": sys.version.split()[0],
            "elevated": bool(ctypes.windll.shell32.IsUserAnAdmin()),
            "windowed_vrr_optimization_setting": reg_value(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\DirectX\UserGpuPreferences",
                                                           "DirectXUserGlobalSettings")}


def adjacency(monitors):
    """Which desktop rectangles touch (Windows' logical layout, not the physical desk)."""
    pairs = []
    for i, a in enumerate(monitors):
        for b in monitors[i + 1:]:
            ax0, ay0, ax1, ay1 = a["rect_px"]
            bx0, by0, bx1, by1 = b["rect_px"]
            if ax1 == bx0 or bx1 == ax0:
                ov = min(ay1, by1) - max(ay0, by0)
                if ov > 0:
                    pairs.append({"a": a["gdi_name"], "b": b["gdi_name"], "edge": "vertical", "overlap_px": ov,
                                  "a_is": "left" if ax1 == bx0 else "right"})
            if ay1 == by0 or by1 == ay0:
                ov = min(ax1, bx1) - max(ax0, bx0)
                if ov > 0:
                    pairs.append({"a": a["gdi_name"], "b": b["gdi_name"], "edge": "horizontal", "overlap_px": ov,
                                  "a_is": "above" if ay1 == by0 else "below"})
    return pairs


def vrr_link_note(tech_name, vrr):
    """How the link type limits which VRR signal can be used (vendor support is prior knowledge, not read)."""
    if tech_name in ("DISPLAYPORT_EMBEDDED", "DISPLAYPORT_EXTERNAL", "DISPLAYPORT_USB_TUNNEL"):
        return "DP/eDP: VESA Adaptive-Sync path (NVIDIA G-SYNC Compatible, AMD FreeSync, Intel Adaptive Sync)"
    if tech_name == "HDMI":
        bits = []
        if (vrr.get("hdmi_forum") or {}).get("vrr_max_hz"):
            bits.append("HDMI Forum VRR declared (needs an HDMI 2.1 VRR source)")
        if vrr.get("amd_vsdb"):
            bits.append("AMD FreeSync-over-HDMI block (prior knowledge: only AMD drivers read it)")
        return "; ".join(bits) or "HDMI without any declared VRR block"
    return "no VRR path known for this link type"


def dump_key(path, depth=0, max_depth=2, limit=60):
    """Generic read-only dump of a registry subtree (values as ints/strings, binary as short hex)."""
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as k:
        n_sub, n_val, _ = winreg.QueryInfoKey(k)
        values = {}
        for i in range(n_val):
            name, val, _t = winreg.EnumValue(k, i)
            values[name] = val.hex()[:96] if isinstance(val, (bytes, bytearray)) else val
        out = {"values": values}
        if depth < max_depth:
            names = [winreg.EnumKey(k, i) for i in range(min(n_sub, limit))]
            out["subkeys"] = {n: dump_key(path + "\\" + n, depth + 1, max_depth, limit) for n in names}
            out["subkeys_total"] = n_sub
        return out


def graphics_configuration_history():
    """Windows' saved multi-monitor layouts (monitor sets, positions, rotation, rates). Readable only when elevated."""
    result = {}
    for name in ("Configuration", "Connectivity"):
        try:
            result[name] = dump_key(GRAPHICS + "\\" + name)
        except PermissionError:
            result[name] = {"accessible": False, "hint": "run from an elevated shell to include Windows' saved layouts"}
        except OSError as exc:
            result[name] = {"accessible": False, "hint": str(exc)}
    return result



def build_report():
    report = {"schema": SCHEMA, "time_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "read_only": True}
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # per-monitor v2: physical pixels everywhere
    report["system"] = guarded("system", system_info)
    dx = guarded("dxgi", dxgi_report) or {"adapters": []}
    winrt = guarded("winrt", winrt_report)
    paths, modes = guarded("ccd-active", lambda: query_display_config(QDC_ONLY_ACTIVE_PATHS), ([], []))
    all_paths, _ = guarded("ccd-all", lambda: query_display_config(QDC_ALL_PATHS), ([], []))

    dx_outputs = {o["device"]: o for ad in dx["adapters"] for o in ad["outputs"]}
    winrt_targets = {(t["adapter_luid_low"], t["target_id"]): t for t in (winrt or {}).get("connected_targets", [])}
    winrt_monitors = {(m["adapter_luid_low"], m["adapter_target_id"]): m for m in (winrt or {}).get("display_monitors", [])}
    gdi_for_adapter = {}
    monitors, active_paths = [], set()
    for p in paths:
        s, t = p.sourceInfo, p.targetInfo
        sn = device_info(SOURCE_NAME, 1, s.adapterId, s.id)
        gdi_name = sn.viewGdiDeviceName if sn else None
        src = next((m for m in modes if m.infoType == 1 and m.id == s.id and m.adapterId.Low == s.adapterId.Low), None)
        tgt = next((m for m in modes if m.infoType == 2 and m.id == t.id and m.adapterId.Low == t.adapterId.Low), None)
        x, y, w, h = (src.u.sourceMode.x, src.u.sourceMode.y, src.u.sourceMode.width, src.u.sourceMode.height) if src else (0, 0, 0, 0)
        info = read_target(t.adapterId, t.id)
        parsed = parse_edid(info["edid_raw"]) if info.get("edid_raw") else {"valid": False}
        summary = edid_summary(parsed)
        if info.get("pnp_id"):
            active_paths.add(f"{info['pnp_id']}\\{info['pnp_instance']}".lower())
        tech = TECH.get(t.outputTechnology, str(t.outputTechnology))
        adapter_name = device_info(ADAPTER_NAME, 4, s.adapterId, 0)
        sig = tgt.u.targetMode if tgt else None
        preferred = summary.get("preferred_timing") or {}
        stable = windows_stable_id(parsed) if parsed.get("valid") else None
        wr_t = winrt_targets.get((t.adapterId.Low, t.id))
        wr_m = winrt_monitors.get((t.adapterId.Low, t.id))
        reg_dm, cur_dm = devmode(gdi_name, ENUM_REGISTRY) if gdi_name else None, devmode(gdi_name, ENUM_CURRENT) if gdi_name else None
        dxo = dx_outputs.get(gdi_name, {})
        mon = {
            "gdi_name": gdi_name, "is_primary": (x, y) == (0, 0), "rect_px": [x, y, x + w, y + h],
            "position_px": {"x": x, "y": y}, "size_px": {"w": w, "h": h},
            "rotation": ROTATION.get(t.rotation), "gpu_scaling": SCALING.get(t.scaling), "dpi": dpi_info(x + w // 2, y + h // 2),
            "gdi_device_caps_mm": gdi_device_caps(gdi_name) if gdi_name else None,
            "connection": {"output_technology": tech, "connector_instance": info.get("connector_instance"),
                           "vrr_link_note": vrr_link_note(tech, summary.get("vrr") or {})},
            "identity": {
                "friendly_name": info.get("friendly_name") or summary.get("name"),
                "edid_id": f"{summary.get('mfg')}{summary.get('product')}" if summary.get("valid") else None,
                "serial_u32": summary.get("serial_u32"), "serial_text": summary.get("serial_text"),
                "serial_is_placeholder": summary.get("serial_is_placeholder"),
                "week_year": [summary.get("week"), summary.get("year")], "edid_base_sha256": summary.get("base_sha256"),
                "windows_stable_id_rebuilt": stable,
                "windows_stable_id_winrt": wr_t["stable_monitor_id"] if wr_t else None,
                "stable_id_matches": (stable == wr_t["stable_monitor_id"]) if (wr_t and stable) else None,
                "windows_container_id": info.get("container_id"), "displayid_container_uuid": summary.get("displayid_container_uuid"),
                "edid_override_in_registry": info.get("edid_override_present"),
                "device_path": info.get("device_path"), "pnp_instance": info.get("pnp_instance"),
                "target_id": t.id, "source_id": s.id,
                "adapter_luid_session_only": t.adapterId.text(),
                "adapter_device_path": adapter_name.adapterDevicePath if adapter_name else None},
            "timing_now": ({"active": [sig.activeSize.cx, sig.activeSize.cy], "total": [sig.totalSize.cx, sig.totalSize.cy],
                            "pixel_clock_hz": sig.pixelRate, "refresh": rat(sig.vSyncFreq), "h_sync": rat(sig.hSyncFreq),
                            "path_refresh": rat(t.refreshRate), "scan": SCAN.get(sig.scanLineOrdering),
                            "path_flags": p.flags} if sig else None),
            "mode_gdi_now": dm_dict(cur_dm) if cur_dm else None,
            "mode_gdi_registry_fallback": dm_dict(reg_dm) if reg_dm else None,
            "mode_differs_from_registry": bool(cur_dm and reg_dm and (cur_dm.dmPelsWidth, cur_dm.dmPelsHeight, cur_dm.dmDisplayFrequency)
                                              != (reg_dm.dmPelsWidth, reg_dm.dmPelsHeight, reg_dm.dmDisplayFrequency)),
            "modes_dxgi": [{"w": m[0], "h": m[1], "refresh": [m[2], m[3]], "hz": round(m[2] / m[3], 4) if m[3] else None,
                            "interlaced": m[4] in (2, 3)} for m in dxo.get("modes", [])],
            "modes_gdi_raw": [{"w": m[0], "h": m[1], "hz_int": m[2], "fixed_output": m[3]} for m in (gdi_modes(gdi_name) if gdi_name else [])],
            "edid_first_dtd": summary.get("first_dtd"), "edid_preferred_timing": preferred or None,
            "vrr": summary.get("vrr"),
            "vrr_enabled_in_driver": None,  # not readable without NVAPI / ADL / IGCL
            "physical_size": summary.get("physical_size") if summary.get("valid") else None,
            "color": {"edid_chromaticity": summary.get("chromaticity"), "edid_gamma": summary.get("gamma"),
                      "hdr_static": summary.get("hdr_static"), "colorimetry": summary.get("colorimetry"),
                      "dxgi_desc1": dxo.get("desc1"), "windows": color_state(t.adapterId, t.id)},
            "winrt_display_monitor": wr_m,
            "edid": summary if summary.get("valid") else None,
        }
        # exact current rate vs what the EDID declares for the same size: they can differ (see report notes)
        monitors.append(mon)
    monitors.sort(key=lambda m: (m["position_px"]["x"], m["position_px"]["y"]))

    # connected targets that are not on the desktop (disabled or mirrored away)
    active_ids = {(p.targetInfo.adapterId.Low, p.targetInfo.id) for p in paths if p.flags & 1}
    inactive = {}
    for p in all_paths:
        t = p.targetInfo
        key = (t.adapterId.Low, t.id)
        if t.targetAvailable and key not in active_ids and key not in inactive:
            info = read_target(t.adapterId, t.id)
            e = edid_summary(parse_edid(info["edid_raw"])) if info.get("edid_raw") else None
            inactive[key] = {"target_id": t.id, "output_technology": TECH.get(t.outputTechnology), "friendly_name": info.get("friendly_name"),
                             "device_path": info.get("device_path"), "edid": e}
    report.update({
        "adapters_dxgi": [{k: v for k, v in ad.items() if k != "outputs"} for ad in dx["adapters"]],
        "dxgi_allow_tearing_supported_system_wide": dx.get("allow_tearing_supported"),
        "topology": {"active_monitors": len(monitors), "windows_layout_adjacency": adjacency(monitors),
                     "desktop_bounds_px": ([min(m["rect_px"][0] for m in monitors), min(m["rect_px"][1] for m in monitors),
                                            max(m["rect_px"][2] for m in monitors), max(m["rect_px"][3] for m in monitors)]
                                           if monitors else None)},
        "monitors": monitors,
        "connected_but_inactive": list(inactive.values()),
        "registry_history": guarded("registry-history", lambda: registry_history(active_paths), []),
        "graphics_configuration_history": guarded("graphics-config", graphics_configuration_history, {}),
        "notes": [
            "adapter LUIDs are valid until reboot; never store them",
            "refresh_now comes from CCD (exact); EDID-declared rates can differ slightly (eDP panels in particular)",
            "windows_stable_id is rebuilt from the EDID; stable_id_matches compares it with WinRT DisplayTarget.StableMonitorId",
            "vrr_enabled_in_driver is null on purpose: Windows exposes no per-monitor 'VRR is on' flag to plain Win32 callers",
        ],
        "errors": ERRORS,
    })
    return report


def main():
    if os.name != "nt":
        raise SystemExit("room-probe.py reads Windows display APIs and runs only on Windows")
    print(json.dumps(build_report(), indent=1, ensure_ascii=True))


if __name__ == "__main__":
    main()
