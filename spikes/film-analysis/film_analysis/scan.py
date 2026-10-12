"""The full scan: segments ordered from the playhead, CPU work in a process pool, model work here.

The coordinator (this process, below-normal priority) plans the segments, hands each to a pool process (segment_worker.py), and
as results arrive it writes the raw signals, runs the shot model and the face / head / person models in short GPU calls
(batches of at most 8), appends the raw detections, marks the interval done in content.json and now and then re-derives the
per-shot tables, so a reader can use the model while the scan is still running.
"""
from __future__ import annotations

import multiprocessing
import os
import sys
import tempfile
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from typing import Optional

import cv2
import numpy as np

from . import gpu_inference as gi
from . import manifest as mf
from . import prescan, proc, segments as sg, subjects_faces as sf, versions
from .segment_worker import scan_segment

DETECTORS = (("anime-face-v1.4s", "face"), ("anime-head-v2.0s-yv11", "head"), ("anime-person-v1.3s", "person"))
DERIVE_EVERY_S = 15.0


def _log(msg: str) -> None:
    print(f"[scan {time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def _make_task(ctx, a: int, b: int, seg_id: int, models_dir: str, tmp_dir: str, stride: int) -> dict:
    t = ctx.table
    secs = t.seconds_abs()
    d0, d1 = sg.decode_range(t.key, a, b, t.n)
    vs = ctx.vstream
    return {
        "seg_id": seg_id, "file": ctx.path, "stream_index": ctx.ref.stream_index, "container_start_s": ctx.ref.container_start_s,
        "pix_fmt": ctx.ref.pix_fmt, "decode_path": ctx.decode["path"], "width": ctx.width, "height": ctx.height,
        "out_start": a, "out_end": b, "dec_start": d0, "dec_end": d1, "tmp_dir": tmp_dir,
        "pts_ticks": t.pts[d0:d1].tolist(),
        "seek_small_abs_s": float(secs[d0]) if d0 > 0 else None, "seek_crop_abs_s": float(secs[a]) if a > 0 else None,
        "roi": (ctx.roi.x0, ctx.roi.y0, ctx.roi.x1, ctx.roi.y1), "cropdetect_limit": ctx.black["cropdetect_limit"],
        "bright_threshold": ctx.black["bright_threshold"], "sample_stride": stride, "do_crop_stream": True,
        "color_space": vs.get("color_space"), "color_range": vs.get("color_range"), "has_dovi": ctx.has_dovi,
        "yunet_path": os.path.join(models_dir, "yunet-2023mar", "yunet_2023mar.onnx"), "yunet_threshold": 0.7,
    }


def _store_result(ctx, res: dict, hub: gi.ModelHub, raw_out: list) -> dict:
    """Writes one finished segment into the signals file and runs the models on its pictures. Returns timings."""
    a, b, d0 = res["out_start"], res["out_end"], res["dec_start"]
    st = ctx.store
    t0 = time.perf_counter()
    st.write(a, b, res["cols"])
    if res["crop_rect"] is not None:
        st.write(a, b, {"crop_rect": res["crop_rect"]})
    if res.get("dv_scene_refresh") is not None and st.has("dv_scene_refresh"):
        st.write(a, b, {"dv_scene_refresh": res["dv_scene_refresh"]})
    single, many = hub.tn_windows(res["tn_rgb"], d0, ctx.table.n, a, b)
    st.write(a, b, {"tn_single": single.astype(np.float16), "tn_many": many.astype(np.float16)})
    t_tn = time.perf_counter() - t0
    # faces / heads / people on the sampled pictures (GPU, batches of at most 8) plus YuNet's boxes from the worker
    t0 = time.perf_counter()
    records = []
    samples = res["samples"]
    if samples is not None:
        per_model = {mid: hub.detect(mid, samples) for mid, _cls in DETECTORS}
        sx, sy = ctx.width / 960.0, ctx.height / 540.0
        for k, (g, r, pdx, pdy) in enumerate(res["sample_meta"]):
            boxes = []
            for mid, cls in DETECTORS:
                for det in per_model[mid][k]:
                    x, y, w, h, s = gi.unletterbox(det, r, pdx, pdy)
                    boxes.append((cls, mid, x * sx, y * sy, w * sx, h * sy, s))
            for (x, y, w, h, s) in res["yunet"].get(g, []):
                boxes.append(("face", "yunet-2023mar", x, y, w, h, s))
            records.append((g, boxes))
        sf.append_raw(ctx.out_dir, records)
        raw_out.extend(records)
    t_det = time.perf_counter() - t0
    st.col("scanned")[a:b] = 1
    st.col("bars_valid")[a:b] = 1
    st.flush()
    return {"write_and_tn_s": round(t_tn, 3), "detectors_s": round(t_det, 3)}


PER_WORKER_COMMIT = 2.0 * (1 << 30)      # python process + its ffmpeg (hardware decoder context included), upper bound
PER_WORKER_VRAM_MIB = 600                # one decoder session with its surfaces
MAIN_COMMIT = 3.0 * (1 << 30)            # this process with four ONNX sessions


def choose_workers(requested: int) -> tuple:
    """Fewer workers when the machine has little room (a game may be running): free commit and free GPU memory both count."""
    note = []
    n = requested
    room = proc.memory_headroom()
    if room:
        by_commit = int((room["free_commit"] - MAIN_COMMIT) // PER_WORKER_COMMIT)
        if by_commit < n:
            note.append(f"free commit {room['free_commit'] / (1 << 30):.1f} GiB allows {max(1, by_commit)}")
        n = min(n, max(1, by_commit))
    vram = proc.free_vram_mib()
    if vram is not None:
        by_vram = int((vram - 1500) // PER_WORKER_VRAM_MIB)
        if by_vram < n:
            note.append(f"free GPU memory {vram} MiB allows {max(1, by_vram)}")
        n = min(n, max(1, by_vram))
    return n, note


def _mark_done(ctx, a: int, b: int) -> None:
    done = [list(x) for x in ctx.manifest["scan"]["done"]] + [[a, b]]
    ctx.manifest["scan"]["done"] = sg.merge_intervals(done)


def run_scan(ctx, models_dir: str, manifest_path: str, playhead_s: float, workers: int, segment_seconds: float,
             stride: int, force: bool, ramp: bool = True, max_segments: Optional[int] = None, models_on: str = "auto") -> dict:
    t_start = time.perf_counter()
    man = ctx.manifest
    scan = man["scan"]
    n = ctx.table.n
    if force:
        scan["done"] = []
        for col in ctx.store.names():
            if col not in ("pts", "key", "crop_rect", "bars_valid") and col not in (
                    "edge_top", "edge_bottom", "edge_left", "edge_right"):
                ctx.store.col(col)[:] = 0
        raw = os.path.join(ctx.out_dir, sf.RAW_NAME)
        if os.path.isfile(raw):
            os.remove(raw)
    secs = ctx.table.seconds()
    playhead_frame = ctx.table.frame_at(playhead_s)
    plan = sg.plan(ctx.table.seconds_abs(), ctx.table.key, scan["done"], playhead_frame, segment_seconds,
                   ramp_s=(10.0, 20.0, 40.0) if ramp else ())
    if max_segments is not None:
        plan = plan[:max_segments]
    scan.update({"state": "running" if plan else "complete", "playhead_s": playhead_s, "started_at": mf.now_iso(),
                 "workers": workers, "segment_seconds": segment_seconds, "sample_stride": stride})
    mf.save(ctx.out_dir, man)
    if not plan:
        _log("nothing to scan: every frame is already covered")
        return {"segments": 0}

    workers, why = choose_workers(workers)
    if why:
        _log("workers reduced to %d: %s" % (workers, "; ".join(why)))
    scan["workers"] = workers
    _log(f"{len(plan)} segments, playhead {playhead_s:.1f} s (frame {playhead_frame}), {workers} workers, decode path {ctx.decode['path']}")
    cv2.setNumThreads(1)
    hub = gi.ModelHub(models_dir, manifest_path, prefer_cuda=(models_on != "cpu"))
    warm = {}

    def warm_models():
        warm.update(hub.warm_up(["transnetv2"] + [m for m, _ in DETECTORS]))

    wt = threading.Thread(target=warm_models, daemon=True)
    wt.start()
    _log(f"models warming up in the background ({time.perf_counter() - t_start:.1f} s since start)")

    raw_subjects = sf.load_raw(ctx.out_dir)
    tmp_dir = tempfile.mkdtemp(prefix="scan-", dir=ctx.out_dir)
    mp_ctx = multiprocessing.get_context("spawn")
    first_done_s: Optional[float] = None
    seg_times = []
    failed = []
    last_derive = float("-inf")          # derive the per-shot tables right after the first segment, then every DERIVE_EVERY_S
    done_frames = 0
    with ProcessPoolExecutor(max_workers=workers, mp_context=mp_ctx) as pool:
        pending = {}
        next_i = 0
        retry: list = []                 # indices to try again after a transient failure (memory, hardware decoder)
        attempts: dict = {}
        window = [workers + 1]           # segments in flight; drops to 1 after a memory failure

        def submit_more():
            nonlocal next_i
            while len(pending) < window[0] and (retry or next_i < len(plan)):
                if retry:
                    idx = retry.pop(0)
                else:
                    idx = next_i
                    next_i += 1
                a, b = plan[idx]
                attempts[idx] = attempts.get(idx, 0) + 1
                task = _make_task(ctx, a, b, idx, models_dir, tmp_dir, stride)
                pending[pool.submit(scan_segment, task)] = (idx, a, b, time.perf_counter())

        submit_more()
        _log(f"{len(pending)} segments handed to the pool ({time.perf_counter() - t_start:.1f} s since start)")
        while pending:
            finished, _ = wait(list(pending), return_when=FIRST_COMPLETED)
            for fut in finished:
                idx, a, b, t_sub = pending.pop(fut)
                try:
                    res = fut.result()
                except Exception as e:  # noqa: BLE001 - a crashed pool process: record, go on
                    res = {"ok": False, "seg_id": idx, "error": f"worker process failed: {e}"}
                if not res["ok"]:
                    msg = res["error"]
                    transient = any(k in msg for k in ("emory", "llocate", "CUDA", "ardware device", "frame times differ",
                                                       "gave 0 frames", "failed (exit"))
                    if transient and attempts.get(idx, 1) < 3:
                        window[0] = 1                # the machine is short of something: one segment at a time, then try again
                        retry.append(idx)
                        _log(f"segment {idx} frames {a}-{b} failed ({msg[:160]}); will retry (attempt {attempts[idx] + 1}), one at a time")
                        time.sleep(2.0)
                        continue
                    failed.append({"first": a, "last": b, "error": msg, "attempts": attempts.get(idx, 1)})
                    _log(f"segment {idx} frames {a}-{b} FAILED: {msg[:300]}")
                    continue
                t_wait = time.perf_counter()
                wt.join()
                if first_done_s is None:
                    _log(f"first result after {t_wait - t_start:.1f} s, waited {time.perf_counter() - t_wait:.1f} s for the models")
                extra = _store_result(ctx, res, hub, [])
                _mark_done(ctx, a, b)
                done_frames += b - a
                if first_done_s is None:
                    first_done_s = time.perf_counter() - t_start
                tm = res["timing"]
                seg_times.append({"seg": idx, "first": a, "last": b, "wall_s": round(tm["wall_s"], 2), "queue_to_done_s": round(time.perf_counter() - t_sub, 2),
                                  "frames_decoded": tm["frames_decoded"], **extra})
                _log(f"segment {idx + 1}/{len(plan)} frames {a}-{b} ({secs[b - 1] - secs[a]:.0f} s of film) "
                     f"worker {tm['wall_s']:.1f} s, models {extra['write_and_tn_s'] + extra['detectors_s']:.1f} s; "
                     f"{done_frames / max(1e-9, time.perf_counter() - t_start):.0f} frames/s overall")
                if time.perf_counter() - last_derive >= DERIVE_EVERY_S:
                    raw_subjects = sf.load_raw(ctx.out_dir)
                    prescan.write_l1(ctx, raw_subjects)
                    mf.save(ctx.out_dir, man)
                    last_derive = time.perf_counter()
            submit_more()
    wt.join()
    scan["failed"] = failed
    scan["state"] = "complete" if not failed and sum(b - a for a, b in scan["done"]) == n else ("partial" if scan["done"] else "failed")
    scan["finished_at"] = mf.now_iso()
    wall = time.perf_counter() - t_start
    scan["timing"] = {"wall_s": round(wall, 2), "first_segment_done_s": round(first_done_s, 2) if first_done_s is not None else None,
                      "frames_scanned": done_frames, "frames_per_s": round(done_frames / wall, 1) if wall > 0 else None,
                      "model_warm_up_s": warm, "segments": seg_times, "gpu_calls": hub.stats.summary(),
                      "execution_provider": {mid: hub.active_provider(mid) for mid in ("transnetv2",)}, "models_on": models_on}
    raw_subjects = sf.load_raw(ctx.out_dir)
    prescan.write_l1(ctx, raw_subjects)
    ctx.store.flush()
    mf.save(ctx.out_dir, man)
    for f in os.listdir(tmp_dir):
        try:
            os.remove(os.path.join(tmp_dir, f))
        except OSError:
            pass
    try:
        os.rmdir(tmp_dir)
    except OSError:
        pass
    return scan["timing"]
