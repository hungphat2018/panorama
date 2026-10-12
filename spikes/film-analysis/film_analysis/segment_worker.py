"""One segment of the full scan, run in a pool process (below-normal priority, CPU only).

A segment is a run of frames [out_start, out_end) of the frame table that begins and ends on keyframes. The worker decodes it
with two ffmpeg runs on the chosen decode path, one after the other (so a worker holds one hardware decoder session at a time;
the machine may be running a game):
  full   full-size frames of [out_start, out_end) into cropdetect, results written by the metadata filter to a text file;
  small  960x540 NV12 of every frame from `dec_start` (a keyframe at least 77 frames before out_start, for the shot model's
         100-frame windows and the scdet score's two-frame memory) to `dec_end` (75 frames after out_end), through a pipe.
From the small frames it computes, for the frames of the segment: the slide pair measurement, luma statistics, the scdet-like
score, the bright-edge distances of the four bars, the Dolby Vision scene-refresh flag when present; for every frame of the
decode range the 48x27 RGB picture the shot model reads; and for every `sample_stride`-th frame the letterboxed 640x384 picture
the face / head / person models read, plus YuNet's boxes (CPU).
Both ffmpeg runs print the presentation timestamp of every frame they produce; the worker compares them with the frame table
and refuses the segment on any difference, so a silent offset between files and columns cannot happen.
"""
from __future__ import annotations

import os
import re
import subprocess
import threading
import time
import traceback
from typing import Optional

import cv2
import numpy as np

from . import bars_crop
from . import decode_paths as dp
from . import gpu_inference as gi
from . import slides_motion as sm
from .proc import popen

cv2.setNumThreads(1)       # one process per core of the pool: OpenCV's own thread pool would oversubscribe the machine
SMALL_FRAME_BYTES = dp.SMALL_W * dp.SMALL_H * 3 // 2
_SHOWINFO_RX = re.compile(r"\bn:\s*(\d+)\s+pts:\s*(-?\d+)\s+pts_time")
_SCENE_REFRESH_RX = re.compile(r"scene_refresh_flag=(\d)")


class SegmentError(RuntimeError):
    pass


def yuv_to_rgb_small(y: np.ndarray, u: np.ndarray, v: np.ndarray, color_space: Optional[str], color_range: Optional[str],
                     height: int) -> np.ndarray:
    """Planes of 48x27 images (uint8, [m,27,48]) to RGB uint8 [m,27,48,3] with the matrix the stream is tagged with (BT.709 for
    untagged streams 720 lines or taller, BT.601 below)."""
    cs = (color_space or "").lower()
    if cs in ("bt2020nc", "bt2020c", "bt2020"):
        kr, kb = 0.2627, 0.0593
    elif cs in ("smpte170m", "bt470bg", "bt601", "smpte240m") or (not cs and height < 720):
        kr, kb = 0.299, 0.114
    else:
        kr, kb = 0.2126, 0.0722
    kg = 1.0 - kr - kb
    yf = y.astype(np.float32)
    uf = u.astype(np.float32) - 128.0
    vf = v.astype(np.float32) - 128.0
    if color_range == "pc":
        ys, cs_scale, yo = 1.0, 1.0, 0.0
    else:
        ys, cs_scale, yo = 255.0 / 219.0, 255.0 / 224.0, 16.0
    yy = (yf - yo) * ys
    r = yy + 2.0 * (1.0 - kr) * cs_scale * vf
    b = yy + 2.0 * (1.0 - kb) * cs_scale * uf
    g = yy - (kr * (r - yy) + kb * (b - yy)) / kg          # solves Y = kr R + kg G + kb B for G
    rgb = np.stack([r, g, b], axis=-1)
    return np.clip(np.rint(rgb), 0, 255).astype(np.uint8)


def _read_exact(stream, mv: memoryview) -> bool:
    got, n = 0, len(mv)
    while got < n:
        r = stream.readinto(mv[got:])
        if not r:
            return False
        got += r
    return True


class _StderrReader(threading.Thread):
    """Collects an ffmpeg process's stderr lines (showinfo output, errors)."""

    def __init__(self, stream):
        super().__init__(daemon=True)
        self.stream = stream
        self.lines: list = []

    def run(self):
        for raw in iter(self.stream.readline, b""):
            self.lines.append(raw.decode("utf-8", errors="replace").rstrip())


def _parse_showinfo(lines: list) -> tuple:
    """Per frame: pts ticks, and the Dolby Vision scene_refresh_flag that follows the frame's line (-1 when none printed)."""
    pts, scene = [], []
    for ln in lines:
        m = _SHOWINFO_RX.search(ln)
        if m:
            pts.append(int(m.group(2)))
            scene.append(-1)
            continue
        if scene:
            s = _SCENE_REFRESH_RX.search(ln)
            if s:
                scene[-1] = int(s.group(1))
    return np.asarray(pts, dtype=np.int64), np.asarray(scene, dtype=np.int8)


def scan_segment(task: dict) -> dict:
    """Entry point for the process pool. Returns {'ok': True, ...arrays...} or {'ok': False, 'error': text}."""
    t_start = time.perf_counter()
    procs = []
    try:
        return _scan_segment(task, procs, t_start)
    except Exception as e:  # noqa: BLE001 - reported to the coordinator, which marks the segment failed
        return {"ok": False, "seg_id": task["seg_id"], "error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-1500:]}
    finally:
        for p in procs:
            if p.poll() is None:
                p.kill()


def _scan_segment(task: dict, procs: list, t_start: float) -> dict:
    seg = task["seg_id"]
    ref = dp.StreamRef(task["file"], task["stream_index"], task["container_start_s"], task["pix_fmt"])
    path = task["decode_path"]
    W, H = task["width"], task["height"]
    a, b = task["out_start"], task["out_end"]
    d0, d1 = task["dec_start"], task["dec_end"]
    n_out, n_dec = b - a, d1 - d0
    tmp = task["tmp_dir"]
    pts_expected = np.asarray(task["pts_ticks"], dtype=np.int64)              # for d0..d1-1
    roi = sm.Roi(*task["roi"])
    meter = sm.PairMeter(roi, W, H)
    bright_thr = int(task["bright_threshold"])
    depth_y, depth_x = int(0.25 * dp.SMALL_H), int(0.25 * dp.SMALL_W)         # scan depth at 960x540: a quarter of each dimension
    scale_x, scale_y = W / dp.SMALL_W, H / dp.SMALL_H
    cap_y, cap_x = int(0.25 * H), int(0.25 * W)
    stride = int(task["sample_stride"])

    # ---- the full-size stream first: cropdetect -------------------------------------------------------------------------------
    crop = None
    if task["do_crop_stream"]:
        crop_file = f"crop-{seg:05d}.txt"
        cmd_crop = dp.decode_cmd(path, ref, task["seek_crop_abs_s"], n_out,
                                 dp.full_filter(path, task["pix_fmt"], task["cropdetect_limit"], crop_file), ["-f", "null", "-"],
                                 log_level="error")
        crop_proc = popen(cmd_crop, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, cwd=tmp)
        procs.append(crop_proc)
        err_crop = _StderrReader(crop_proc.stderr)
        err_crop.start()
        crop_proc.wait(timeout=900)
        err_crop.join(timeout=10)
        meta_path = os.path.join(tmp, crop_file)
        if crop_proc.returncode != 0 or not os.path.isfile(meta_path):
            raise SegmentError(f"cropdetect stream failed (exit {crop_proc.returncode}): {' | '.join(err_crop.lines[-3:])[:300]}")
        with open(meta_path, encoding="utf-8", errors="replace") as f:
            crop = bars_crop.parse_cropdetect_metadata(f.read())
        os.remove(meta_path)
        want = pts_expected[a - d0: b - d0]
        # -frames:v stops the output after n_out frames; the metadata filter may have seen one more already in flight
        crop = {"frame": crop["frame"][:n_out], "pts": crop["pts"][:n_out], "rect": crop["rect"][:n_out]}
        if crop["rect"].shape[0] != n_out or not np.array_equal(crop["pts"], want):
            raise SegmentError(f"cropdetect stream frame times differ from the frame table (got {crop['rect'].shape[0]} frames, "
                               f"expected {n_out})")

    # ---- the small stream ------------------------------------------------------------------------------------------------------
    cmd_small = dp.decode_cmd(path, ref, task["seek_small_abs_s"], n_dec, dp.small_filter(path),
                              ["-f", "rawvideo", "-pix_fmt", "nv12", "pipe:1"])
    small = popen(cmd_small, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, bufsize=0)
    procs.append(small)
    err_small = _StderrReader(small.stderr)
    err_small.start()

    # ---- per-frame work ----------------------------------------------------------------------------------------------
    cols = {k: np.zeros(n_out, dt) for k, dt in (
        ("scd", np.float16), ("luma_mean", np.uint8), ("luma_p95", np.uint8), ("luma_std", np.uint8), ("pc_peak", np.uint8),
        ("dx", np.float32), ("dy", np.float32), ("diff_before", np.float16), ("diff_after", np.float16),
        ("explained", np.uint8), ("static", np.uint8), ("other", np.uint8),
        ("edge_top", np.uint16), ("edge_bottom", np.uint16), ("edge_left", np.uint16), ("edge_right", np.uint16))}
    y48 = np.zeros((n_dec, 27, 48), np.uint8)
    u48 = np.zeros((n_dec, 27, 48), np.uint8)
    v48 = np.zeros((n_dec, 27, 48), np.uint8)
    samples_rgb, sample_meta, sample_frames = [], [], []
    yunet = None
    if task.get("yunet_path") and any(((d0 + k) % stride == 0) and a <= d0 + k < b for k in range(n_dec)):
        yunet = cv2.FaceDetectorYN.create(task["yunet_path"], "", (dp.SMALL_W, dp.SMALL_H), float(task["yunet_threshold"]), 0.3, 5000)
    yunet_boxes = {}

    buf = np.empty(SMALL_FRAME_BYTES, np.uint8)
    mv = memoryview(buf)
    prev_f: Optional[np.ndarray] = None
    prev_g8: Optional[np.ndarray] = None
    prev_mafd = 0.0
    frames_read = 0
    t_cpu = 0.0
    t_wait = 0.0
    t_loop0 = time.perf_counter()
    for k in range(n_dec):
        t_w0 = time.perf_counter()
        ok_read = _read_exact(small.stdout, mv)
        t_wait += time.perf_counter() - t_w0
        if not ok_read:
            break
        frames_read += 1
        g = d0 + k
        y = buf[:dp.SMALL_W * dp.SMALL_H].reshape(dp.SMALL_H, dp.SMALL_W)
        uv = buf[dp.SMALL_W * dp.SMALL_H:].reshape(dp.SMALL_H // 2, dp.SMALL_W)
        cur_f = cv2.resize(y.astype(np.float32), (sm.GRID_W, sm.GRID_H), interpolation=cv2.INTER_AREA)   # exact 2x2 mean
        cur_g8 = cv2.resize(y, (sm.GRID_W, sm.GRID_H), interpolation=cv2.INTER_AREA)
        # scdet-like score chain (needs two frames of memory)
        if prev_g8 is None:
            scd, prev_mafd = 0.0, 0.0
        else:
            mafd = float(cv2.mean(cv2.absdiff(cur_g8, prev_g8))[0]) * 100.0 / 256.0
            scd, prev_mafd = min(max(min(mafd, abs(mafd - prev_mafd)), 0.0), 100.0), mafd
        if a <= g < b:
            i = g - a
            t0 = time.perf_counter()
            cols["scd"][i] = scd
            hist = cv2.calcHist([cur_g8], [0], None, [256], [0, 256]).ravel()
            cdf = np.cumsum(hist)
            mean, std = cv2.meanStdDev(cur_f)
            cols["luma_mean"][i] = int(round(float(mean[0, 0])))
            cols["luma_p95"][i] = int(np.searchsorted(cdf, 0.95 * cdf[-1]))
            cols["luma_std"][i] = min(255, int(round(float(std[0, 0]))))
            if prev_f is not None:
                m = meter.measure(prev_f, cur_f)
                cols["dx"][i], cols["dy"][i] = m["dx"], m["dy"]
                cols["pc_peak"][i] = int(min(255, max(0, round(m["peak"] * 255))))
                cols["diff_before"][i], cols["diff_after"][i] = m["before"], m["after"]
                cols["explained"][i] = int(round(min(1.0, m["explained"]) * 255))
                cols["static"][i] = int(round(min(1.0, m["static"]) * 255))
                cols["other"][i] = int(round(min(1.0, m["other"]) * 255))
            # distance from each frame edge to the first row / column holding at least two bright pixels
            cols["edge_top"][i] = _first_bright(y[:depth_y] >= bright_thr, axis=1, scale=scale_y, cap=cap_y)
            cols["edge_bottom"][i] = _first_bright((y[-depth_y:] >= bright_thr)[::-1], axis=1, scale=scale_y, cap=cap_y)
            cols["edge_left"][i] = _first_bright(y[:, :depth_x] >= bright_thr, axis=0, scale=scale_x, cap=cap_x)
            cols["edge_right"][i] = _first_bright((y[:, -depth_x:] >= bright_thr)[:, ::-1], axis=0, scale=scale_x, cap=cap_x)
            t_cpu += time.perf_counter() - t0
            if g % stride == 0:
                bgr = cv2.cvtColor(buf.reshape(dp.SMALL_H * 3 // 2, dp.SMALL_W), cv2.COLOR_YUV2BGR_NV12)
                rgb, r, pdx, pdy = gi.letterbox(bgr)
                samples_rgb.append(rgb)
                sample_meta.append((g, r, pdx, pdy))
                if yunet is not None:
                    _, faces = yunet.detect(bgr)
                    yunet_boxes[g] = [] if faces is None else [
                        (float(f[0]) * scale_x, float(f[1]) * scale_y, float(f[2]) * scale_x, float(f[3]) * scale_y, float(f[14]))
                        for f in faces]
        # the shot model's 48x27 picture: area averages of the 480x270 luma and the two 480x270 chroma planes
        y48[k] = cv2.resize(cur_g8, (48, 27), interpolation=cv2.INTER_AREA)
        u48[k] = cv2.resize(np.ascontiguousarray(uv[:, 0::2]), (48, 27), interpolation=cv2.INTER_AREA)
        v48[k] = cv2.resize(np.ascontiguousarray(uv[:, 1::2]), (48, 27), interpolation=cv2.INTER_AREA)
        prev_f, prev_g8 = cur_f, cur_g8
    # drain: ffmpeg must have no more frames
    small.stdout.close()
    small.wait(timeout=60)
    err_small.join(timeout=10)
    if frames_read != n_dec:
        raise SegmentError(f"small stream gave {frames_read} frames, the frame table needs {n_dec} "
                           f"(ffmpeg: {' | '.join(err_small.lines[-3:])[:300]})")
    pts_small, scene_small = _parse_showinfo(err_small.lines)
    # -frames:v stops the output after n_dec frames; showinfo may have seen one more already in flight
    pts_small, scene_small = pts_small[:n_dec], scene_small[:n_dec]
    if pts_small.shape[0] != n_dec or not np.array_equal(pts_small, pts_expected):
        bad = int(np.nonzero(pts_small[:min(len(pts_small), n_dec)] != pts_expected[:min(len(pts_small), n_dec)])[0][:1].sum()) \
            if len(pts_small) else -1
        raise SegmentError(f"small stream frame times differ from the frame table (got {pts_small.shape[0]} frames, expected "
                           f"{n_dec}; first difference at row {bad}: {pts_small[bad:bad + 2].tolist()} vs "
                           f"{pts_expected[bad:bad + 2].tolist()})")

    # ---- TN input, subject samples ---------------------------------------------------------------------------------------
    tn_rgb = yuv_to_rgb_small(y48, u48, v48, task.get("color_space"), task.get("color_range"), H)
    samples = np.stack(samples_rgb) if samples_rgb else None
    out_scene = scene_small[a - d0: b - d0]
    return {
        "ok": True, "seg_id": seg, "out_start": a, "out_end": b, "dec_start": d0, "cols": cols,
        "crop_rect": crop["rect"] if crop is not None else None,
        "dv_scene_refresh": np.where(out_scene >= 0, out_scene, 0).astype(np.uint8) if task.get("has_dovi") else None,
        "tn_rgb": tn_rgb, "samples": samples, "sample_meta": sample_meta, "yunet": yunet_boxes,
        "timing": {"wall_s": time.perf_counter() - t_start, "python_measure_s": t_cpu, "wait_pipe_s": t_wait,
                   "loop_s": time.perf_counter() - t_loop0, "frames_decoded": n_dec, "frames_out": n_out},
    }


def _first_bright(bright: np.ndarray, axis: int, scale: float, cap: int) -> int:
    """bright: boolean band, rows (axis=1) or columns (axis=0) running away from the frame edge. Distance in source px to the
    first line holding at least two bright pixels; `cap` (the scan depth) when there is none."""
    hits = np.count_nonzero(bright, axis=axis) >= 2
    if not hits.any():
        return cap
    return min(cap, int(round(int(np.argmax(hits)) * scale)))
