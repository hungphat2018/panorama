"""The quick pre-scan: facts, frame table, black level, crop vocabulary and a cut prior from the keyframes alone.

What it does (target: a few seconds for a 24-minute episode; measured numbers are in the report):
  1. file key (three 1 MiB samples), ffprobe of the container, packet list -> frame table (no decoding);
  2. first-frame side data for HDR / Dolby Vision files;
  3. the decode path: the first of cuda / d3d11va / software that decodes the stream;
  4. keyframes only (ffmpeg -skip_frame nokey): 960x540 luma -> black level of the file, bright-edge profiles of the bars;
  5. keyframes only, full size, through cropdetect with the calibrated threshold -> crop rectangles;
  6. the crop vocabulary, the region of interest for slide analysis, provisional shots cut at the keyframes.
Everything is written to the content folder at once, so a planner can already make a cautious cue sheet.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Optional

import cv2
import numpy as np

from . import bars_crop, black_level, derive_tables, file_facts, file_key, frame_table as ft, manifest as mf, media_probe as mp, mkv_chapters
from . import decode_paths as dp
from . import slides_motion as sm
from . import versions
from .proc import find_tool, popen, tool_version
from .segment_worker import SMALL_FRAME_BYTES, _parse_showinfo, _read_exact, _StderrReader
from .signals_store import SignalsStore, column_specs, write_json_atomic

DEPTH_Y, DEPTH_X = int(0.25 * dp.SMALL_H), int(0.25 * dp.SMALL_W)


@dataclass
class Context:
    path: str
    out_dir: str
    key: dict
    info: dict
    vstream: dict
    table: ft.FrameTable
    facts: dict
    ref: dp.StreamRef
    decode: dict
    manifest: dict
    store: SignalsStore
    width: int
    height: int
    has_dovi: bool
    black: dict
    vocab_modes: list = field(default_factory=list)       # inclusive rects with shares
    roi: sm.Roi = field(default_factory=lambda: sm.Roi(0, 0, sm.GRID_W, sm.GRID_H))
    timings: dict = field(default_factory=dict)


def _band_profiles(y: np.ndarray) -> tuple:
    """Second-largest value of every line of the four edge bands (top, bottom rows; left, right columns): a line is 'bright'
    for a threshold when this is at least the threshold, i.e. when two pixels reach it."""
    def second(band: np.ndarray, axis: int) -> np.ndarray:
        k = band.shape[axis] - 2
        return np.partition(band, k, axis=axis).take(k, axis=axis)
    return (second(y[:DEPTH_Y], 1), second(y[-DEPTH_Y:][::-1], 1), second(y[:, :DEPTH_X], 0), second(y[:, -DEPTH_X:][:, ::-1], 0))


def _first_at_least(profile: np.ndarray, thr: int, scale: float, cap: int) -> int:
    hit = np.nonzero(profile >= thr)[0]
    return cap if len(hit) == 0 else min(cap, int(round(int(hit[0]) * scale)))


def _keyframe_luma_pass(ref: dp.StreamRef, decode_path: str, table: ft.FrameTable, tmp: str) -> dict:
    """Decode the keyframes to 960x540 NV12; return per keyframe row: edge medians for the black level and band profiles."""
    cmd = dp.decode_cmd(decode_path, ref, None, None, dp.small_filter(decode_path), ["-f", "rawvideo", "-pix_fmt", "nv12", "pipe:1"],
                        keyframes_only=True)
    p = popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, bufsize=0)
    err = _StderrReader(p.stderr)
    err.start()
    buf = np.empty(SMALL_FRAME_BYTES, np.uint8)
    mv = memoryview(buf)
    ys, profiles = [], []
    try:
        while _read_exact(p.stdout, mv):
            y = buf[:dp.SMALL_W * dp.SMALL_H].reshape(dp.SMALL_H, dp.SMALL_W)
            ys.append(black_level.edge_samples(y))
            profiles.append(_band_profiles(y))
    finally:
        p.stdout.close()
        if p.poll() is None:
            p.kill()
        p.wait(timeout=30)
        err.join(timeout=5)
    pts, _scene = _parse_showinfo(err.lines)
    pts = pts[: len(ys)]
    rows = np.searchsorted(table.pts, pts)
    good = (rows < table.n) & (table.pts[np.minimum(rows, table.n - 1)] == pts)
    return {"rows": rows[good[: len(rows)]], "edge_samples": [ys[i] for i in range(len(ys)) if good[i]],
            "profiles": [profiles[i] for i in range(len(profiles)) if good[i]], "decoded": len(ys), "matched": int(good.sum())}


def _keyframe_crop_pass(ref: dp.StreamRef, decode_path: str, pix_fmt: str, limit: float, table: ft.FrameTable, tmp: str) -> dict:
    name = "crop-keyframes.txt"
    cmd = dp.decode_cmd(decode_path, ref, None, None, dp.full_filter(decode_path, pix_fmt, limit, name), ["-f", "null", "-"],
                        keyframes_only=True, log_level="error")
    r = subprocess.run(cmd, cwd=tmp, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       creationflags=0x08000000 | 0x00004000 if os.name == "nt" else 0)
    meta = os.path.join(tmp, name)
    if r.returncode != 0 or not os.path.isfile(meta):
        raise RuntimeError(f"keyframe cropdetect failed: {r.stderr.strip()[-300:]}")
    with open(meta, encoding="utf-8", errors="replace") as f:
        parsed = bars_crop.parse_cropdetect_metadata(f.read())
    os.remove(meta)
    rows = np.searchsorted(table.pts, parsed["pts"])
    good = (rows < table.n) & (table.pts[np.minimum(rows, table.n - 1)] == parsed["pts"])
    return {"rows": rows[good], "rect": parsed["rect"][good], "decoded": len(parsed["pts"]), "matched": int(good.sum())}


def _roi_from_vocabulary(modes: list, W: int, H: int) -> sm.Roi:
    big = [bars_crop.to_half_open(m["rect"]) for m in modes if m["share"] >= 0.10]
    roi = sm.roi_from_modes(big, W, H)
    area_frac = ((roi.x1 - roi.x0) * (roi.y1 - roi.y0)) / (sm.GRID_W * sm.GRID_H)
    return roi if (roi.usable and area_frac >= 0.40) else sm.Roi(0, 0, sm.GRID_W, sm.GRID_H)


def run(path: str, out_dir: str, model_hashes: dict, sample_stride: int, decode_preference: str = "auto") -> Context:
    t_all = time.perf_counter()
    timings = {}
    os.makedirs(out_dir, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="prescan-", dir=out_dir)
    try:
        return _run(path, out_dir, tmp, decode_preference, model_hashes, sample_stride, t_all, timings)
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


def _run(path, out_dir, tmp, decode_preference, model_hashes, sample_stride, t_all, timings) -> Context:
    def lap(name, t0):
        timings[name] = round(time.perf_counter() - t0, 3)

    t0 = time.perf_counter()
    key = file_key.sample_key(path)
    lap("file_key_s", t0)
    t0 = time.perf_counter()
    info = mp.probe_container(path)
    vstream = mp.pick_video_stream(info)
    lap("probe_s", t0)
    t0 = time.perf_counter()
    num, den = (int(x) for x in vstream["time_base"].split("/"))
    avg = mp.parse_rational(vstream.get("avg_frame_rate")) or mp.parse_rational(vstream.get("r_frame_rate"))
    step = int(round((1 / avg) / Fraction(num, den))) if avg else None
    packets = mp.video_packets(path, int(vstream["index"]))
    table = ft.table_from_packets(packets, num, den, step)
    lap("frame_table_s", t0)
    W, H = int(vstream["width"]), int(vstream["height"])

    # first-frame side data for HDR / Dolby Vision
    t0 = time.perf_counter()
    first_frame = hdr_first_frame(path, vstream)
    sd_types = {sd.get("side_data_type") for sd in vstream.get("side_data_list", []) or []}
    lap("hdr_side_data_s", t0)
    has_dovi = "DOVI configuration record" in sd_types

    cstart = mp.parse_rational(info.get("format", {}).get("start_time"))
    ref = dp.StreamRef(path, int(vstream["index"]), float(cstart) if cstart is not None else 0.0, vstream.get("pix_fmt", ""))
    t0 = time.perf_counter()
    decode = dp.probe_decode_path(ref, decode_preference)
    lap("decode_probe_s", t0)

    # ---- keyframe passes ---------------------------------------------------------------------------------------------------
    t0 = time.perf_counter()
    kf_luma = _keyframe_luma_pass(ref, decode["path"], table, tmp)
    lap("keyframe_luma_pass_s", t0)
    black = black_level.estimate_from_samples(kf_luma["edge_samples"], vstream.get("color_range"))
    t0 = time.perf_counter()
    kf_crop = _keyframe_crop_pass(ref, decode["path"], vstream.get("pix_fmt", ""), black["cropdetect_limit"], table, tmp)
    lap("keyframe_crop_pass_s", t0)

    # ---- the frame table and keyframe rows go into the signals file ----------------------------------------------------------
    t0 = time.perf_counter()
    columns = column_specs(has_dovi)
    groups = versions.signal_group_versions(model_hashes, has_dovi, sample_stride)
    store = SignalsStore.create(out_dir, table.n, columns, groups)
    store.col("pts")[:] = table.pts
    store.col("key")[:] = table.key
    thr = black["bright_threshold"]
    sy_, sx_ = H / dp.SMALL_H, W / dp.SMALL_W
    cap_y, cap_x = int(0.25 * H), int(0.25 * W)
    for row, prof in zip(kf_luma["rows"], kf_luma["profiles"]):
        store.col("edge_top")[row] = _first_at_least(prof[0], thr, sy_, cap_y)
        store.col("edge_bottom")[row] = _first_at_least(prof[1], thr, sy_, cap_y)
        store.col("edge_left")[row] = _first_at_least(prof[2], thr, sx_, cap_x)
        store.col("edge_right")[row] = _first_at_least(prof[3], thr, sx_, cap_x)
    if len(kf_crop["rows"]):
        store.col("crop_rect")[kf_crop["rows"]] = kf_crop["rect"]
    bv = store.col("bars_valid")
    bv[np.intersect1d(kf_luma["rows"], kf_crop["rows"])] = 1
    # keyframe rows that have a rectangle but no luma profile still have a valid rectangle; edges stay zero there
    store.flush()
    lap("signals_write_s", t0)

    # ---- vocabulary from keyframes -------------------------------------------------------------------------------------------
    t0 = time.perf_counter()
    kf_rows = np.nonzero(table.key)[0]
    ok_rect = bars_crop.valid_mask(store.col("crop_rect").astype(np.int16), W, H) & bv.astype(bool)
    items = []
    gop = np.diff(np.concatenate([kf_rows, [table.n]]))
    for r, g in zip(kf_rows, gop):
        if ok_rect[r]:
            items.append({"union": store.col("crop_rect")[r].astype(np.int32), "agree": 1.0, "weight": int(g)})
    modes = bars_crop.build_vocabulary(items)
    roi = _roi_from_vocabulary(modes, W, H) if modes else sm.Roi(0, 0, sm.GRID_W, sm.GRID_H)
    lap("vocabulary_s", t0)

    # ---- facts, manifest, provisional shots ---------------------------------------------------------------------------------
    t0 = time.perf_counter()
    vers = {"worker": versions.table_versions(), "signals": groups}
    facts = file_facts.build_file_facts(os.path.basename(path), key, info, vstream, table, first_frame, decode["path"], vers)
    attach_mkv_chapters(facts, path)
    facts["black_level"] = black
    facts["crop_vocabulary"] = {"source": "keyframes", "keyframes_used": int(len(items)),
                                "modes": [{"id": i, "rect": bars_crop.to_half_open(m["rect"]), "share": m["share"]} for i, m in enumerate(modes)]}
    facts["slide_region_of_interest"] = {"grid_px": [roi.x0, roi.y0, roi.x1, roi.y1], "grid": [sm.GRID_W, sm.GRID_H]}
    write_json_atomic(os.path.join(out_dir, "file-facts.json"), facts)
    man = mf.new_manifest(key, os.path.basename(path), table.n)
    man["tools"] = mf.tools_info(tool_version("ffmpeg"), tool_version("ffprobe"), {"numpy": np.__version__, "opencv": cv2.__version__})
    man["decode_path"] = decode
    mf.set_table(man, "file-facts", "file-facts.json", versions.FILE_FACTS, "complete")
    mf.set_table(man, "signals", "signals.json", json_version(groups), "partial", columns=len(columns))
    ctx = Context(path, out_dir, key, info, vstream, table, facts, ref, decode, man, store, W, H, has_dovi, black, modes, roi, timings)
    write_l1(ctx, raw_subjects=None)
    lap("tables_s", t0)
    timings["prescan_total_s"] = round(time.perf_counter() - t_all, 3)
    man["prescan"] = {"timings": timings, "keyframes": int(table.key.sum()), "keyframes_luma_matched": kf_luma["matched"],
                      "keyframes_crop_matched": kf_crop["matched"]}
    mf.save(out_dir, man)
    return ctx


def hdr_first_frame(path: str, vstream: dict) -> Optional[dict]:
    """Side data of the first frame (HDR10 / Dolby Vision metadata sits there), for streams that can carry it; else None."""
    sd_types = {sd.get("side_data_type") for sd in vstream.get("side_data_list", []) or []}
    if vstream.get("color_transfer") in ("smpte2084", "arib-std-b67") or "DOVI configuration record" in sd_types:
        try:
            frames = mp.frame_side_data(path, int(vstream["index"]), count=1)
            return frames[0] if frames else None
        except mp.ProbeError:
            return None
    return None


def attach_mkv_chapters(facts: dict, path: str) -> None:
    """Ordered chapters of a Matroska file: FFmpeg ignores them and so does the model; the facts say so."""
    if "matroska" not in (facts["file"].get("container") or ""):
        return
    oc = mkv_chapters.read_chapters(path)
    facts["timeline"]["ordered_chapters"] = oc
    if oc and oc["ordered_editions"]:
        facts["timeline"]["notes"].append(
            f"this Matroska file has {oc['ordered_editions']} ordered chapter edition(s) ({oc['atoms']} chapters, "
            f"{oc['atoms_linking_other_segments']} linking other files); FFmpeg ignores them and so does this model, which "
            "describes the file's own linear timeline")


def json_version(groups: dict) -> str:
    return ",".join(f"{k}={v}" for k, v in groups.items())


def write_l1(ctx: Context, raw_subjects: Optional[dict], want: Optional[set] = None) -> dict:
    """Derives the per-shot tables from the signals as they are now and writes them next to the signals. `want` names the tables
    to make (default: shots, slide-paths, subjects); the others are left exactly as they are on disk."""
    done = ctx.manifest["scan"]["done"]
    out = derive_tables.derive(ctx.store, ctx.table, ctx.facts, raw_subjects, done, want)
    if out["shots"] is not None:
        ctx.facts.update(out["facts_update"])
        write_json_atomic(os.path.join(ctx.out_dir, "shots.json"), out["shots"], indent=None)
        mf.set_table(ctx.manifest, "shots", "shots.json", versions.SHOTS_TABLE, out["shots"]["state"], shots=out["shots"]["counts"]["shots"])
        write_json_atomic(os.path.join(ctx.out_dir, "file-facts.json"), ctx.facts)
    if out["slide_paths"] is not None:
        write_json_atomic(os.path.join(ctx.out_dir, "slide-paths.json"), out["slide_paths"], indent=None)
        mf.set_table(ctx.manifest, "slide-paths", "slide-paths.json", versions.SLIDE_PATHS, out["slide_paths"]["state"],
                     runs=len(out["slide_paths"]["runs"]))
    if out["subjects"] is not None:
        write_json_atomic(os.path.join(ctx.out_dir, "subjects.json"), out["subjects"], indent=None)
        mf.set_table(ctx.manifest, "subjects", "subjects.json", versions.SUBJECTS, out["subjects"]["state"],
                     sampled_frames=out["subjects"]["sampled_frames"])
    return out


def load_context(path: str, out_dir: str, model_hashes: dict, sample_stride: int) -> Optional[Context]:
    """The pre-scan of an earlier run, when the folder holds one for this very file (same key) with compatible signals;
    otherwise None and the caller runs the pre-scan afresh."""
    man = mf.load(out_dir)
    facts_path = os.path.join(out_dir, "file-facts.json")
    if man is None or not os.path.isfile(facts_path) or not os.path.isfile(os.path.join(out_dir, "signals.json")):
        return None
    key = file_key.sample_key(path)
    if not file_key.same_file(man["file"]["key"], key):
        return None
    with open(facts_path, encoding="utf-8") as f:
        facts = json.load(f)
    try:
        store = SignalsStore.open(out_dir, writable=True)
    except (OSError, ValueError, KeyError):
        return None
    has_dovi = facts["video"].get("dolby_vision") is not None
    columns = column_specs(has_dovi)
    groups = versions.signal_group_versions(model_hashes, has_dovi, sample_stride)
    if not store.compatible_with(columns, groups, store.n_frames):
        return None
    tl = facts["timeline"]
    table = ft.FrameTable(tl["time_base"][0], tl["time_base"][1], np.array(store.col("pts")), np.array(store.col("key")).astype(bool),
                          tl.get("last_duration_ticks"), tl.get("frame_table_source", "packets"), tl.get("frame_table_stats", {}))
    info = mp.probe_container(path)
    vstream = mp.pick_video_stream(info)
    cstart = mp.parse_rational(info.get("format", {}).get("start_time"))
    ref = dp.StreamRef(path, int(vstream["index"]), float(cstart) if cstart is not None else 0.0, vstream.get("pix_fmt", ""))
    modes = [{"rect": [m["rect"][0], m["rect"][2] - 1, m["rect"][1], m["rect"][3] - 1], "share": m["share"]}
             for m in facts.get("crop_vocabulary", {}).get("modes", [])]
    r = facts.get("slide_region_of_interest", {}).get("grid_px", [0, 0, sm.GRID_W, sm.GRID_H])
    ctx = Context(path, out_dir, key, info, vstream, table, facts, ref, man["decode_path"], man, store,
                  int(vstream["width"]), int(vstream["height"]), has_dovi, facts["black_level"], modes, sm.Roi(*r), {})
    return ctx
