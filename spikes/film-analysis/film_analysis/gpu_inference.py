"""Model runs: ONNX Runtime with the CUDA provider (CPU provider when CUDA is not there), short calls only.

GPU rules from the research (measured with a 240 Hz player running; section 4.4): decode on the video decoder chip, never the
shader cores; CUDA calls of at most 8 items, at most about 20 ms each (batch 8 left 240 Hz playback with no late frame, batch 16
and 64 did); no sleeping between calls (duty cycling was worse); light fields on the CPU. `MAX_BATCH` enforces the 8; every call's
duration is recorded so a run can prove it stayed under 20 ms.
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Optional

import cv2
import numpy as np

MAX_BATCH = 8
TN_W, TN_H = 48, 27
YOLO_W, YOLO_H = 640, 384
NMS_IOU = 0.7


class CallStats:
    """Durations of model calls, per model, in milliseconds."""

    def __init__(self):
        self._lock = threading.Lock()
        self.ms: dict = {}

    def add(self, model: str, ms: float) -> None:
        with self._lock:
            self.ms.setdefault(model, []).append(ms)

    def summary(self) -> dict:
        out = {}
        for model, v in self.ms.items():
            a = np.asarray(v)
            out[model] = {"calls": int(a.size), "mean_ms": round(float(a.mean()), 2), "p99_ms": round(float(np.percentile(a, 99)), 2),
                          "max_ms": round(float(a.max()), 2), "calls_over_20ms": int((a > 20.0).sum())}
        return out


class ModelHub:
    """Lazy ONNX sessions for the models in tools/models, listed in models-manifest.json."""

    def __init__(self, models_dir: str, manifest_path: str, prefer_cuda: bool = True):
        import onnxruntime as ort
        self.ort = ort
        if prefer_cuda:
            try:
                ort.preload_dlls()
            except Exception:  # noqa: BLE001 - no CUDA wheels / not Windows: the CPU provider is used below
                pass
        with open(manifest_path, encoding="utf-8") as f:
            self.manifest = {m["id"]: m for m in json.load(f)["models"]}
        self.dir = models_dir
        self.stats = CallStats()
        self._sessions: dict = {}
        self._locks = {mid: threading.Lock() for mid in self.manifest}
        avail = ort.get_available_providers()
        self.providers = (["CUDAExecutionProvider"] if prefer_cuda and "CUDAExecutionProvider" in avail else []) + ["CPUExecutionProvider"]

    def path(self, model_id: str) -> str:
        m = self.manifest[model_id]
        p = os.path.join(self.dir, m["path"])
        if not os.path.isfile(p):
            raise FileNotFoundError(f"model file missing: {p} (see models-manifest.json for the source)")
        if os.path.getsize(p) != m["size_bytes"]:
            raise ValueError(f"model {model_id}: size {os.path.getsize(p)} differs from the manifest's {m['size_bytes']}")
        return p

    def session(self, model_id: str):
        with self._locks[model_id]:
            if model_id not in self._sessions:
                so = self.ort.SessionOptions()
                so.log_severity_level = 3
                # the GPU does the work: one CPU thread that sleeps instead of spinning (the default pool of one thread per
                # core spins and burns CPU the player may need), and cuDNN's quick heuristic instead of an exhaustive search
                # that re-runs for every new batch size
                on_gpu = self.providers[0] == "CUDAExecutionProvider"
                so.intra_op_num_threads = 1 if on_gpu else 4          # the CPU fallback needs real threads
                so.inter_op_num_threads = 1
                so.add_session_config_entry("session.intra_op.allow_spinning", "0")
                providers = [("CUDAExecutionProvider", {"cudnn_conv_algo_search": "HEURISTIC"}) if p == "CUDAExecutionProvider" else p
                             for p in self.providers]
                self._sessions[model_id] = self.ort.InferenceSession(self.path(model_id), so, providers=providers)
            return self._sessions[model_id]

    def warm_up(self, model_ids: list) -> dict:
        """Creates the sessions and makes one throw-away call each (kernel selection and memory allocation happen on the first
        call), so the first real call is as fast as the rest. Returns seconds per model."""
        took = {}
        for mid in model_ids:
            t0 = time.perf_counter()
            sess = self.session(mid)
            if mid == "transnetv2":
                sess.run(None, {"frames": np.zeros((1, 100, TN_H, TN_W, 3), np.uint8)})
            elif mid in ("anime-face-v1.4s", "anime-head-v2.0s-yv11", "anime-person-v1.3s"):
                sess.run(None, {"images": np.zeros((MAX_BATCH, 3, YOLO_H, YOLO_W), np.float32)})
            took[mid] = round(time.perf_counter() - t0, 2)
        return took

    def active_provider(self, model_id: str) -> str:
        return self.session(model_id).get_providers()[0]

    def threshold(self, model_id: str) -> float:
        return float(self.manifest[model_id]["threshold"])

    def model_sha256(self, model_id: str) -> str:
        return self.manifest[model_id]["sha256"]

    # -- TransNetV2 --------------------------------------------------------------------------------------------------
    def tn_windows(self, frames: np.ndarray, first_frame: int, n_total: int, a: int, b: int) -> tuple:
        """Predictions for frames a..b-1 of the file, with the official windowing aligned to the whole file (100-frame windows
        every 50 frames starting 25 before frame 0; the centre 50 of each window is kept), so the result does not depend on
        where a segment starts. `frames`: uint8 [m, 27, 48, 3] holding file frames first_frame .. first_frame+m-1, which must
        cover every frame the windows need (clamped at the file's ends, where the official recipe repeats the end frames).
        Returns (single, many) float32 arrays of length b-a."""
        sess = self.session("transnetv2")
        singles = np.zeros(b - a, np.float32)
        manys = np.zeros(b - a, np.float32)
        for j in range(a // 50, (b - 1) // 50 + 1):
            idx = np.clip(np.arange(50 * j - 25, 50 * j + 75), 0, n_total - 1)
            local = idx - first_frame
            if local.min() < 0 or local.max() >= frames.shape[0]:
                raise ValueError(f"TransNetV2 window {j} needs frames {idx.min()}..{idx.max()}, have "
                                 f"{first_frame}..{first_frame + frames.shape[0] - 1}")
            win = frames[local][None]
            t0 = time.perf_counter()
            single_l, many_l = sess.run(None, {"frames": win})
            self.stats.add("transnetv2", (time.perf_counter() - t0) * 1000)
            lo, hi = max(a, 50 * j), min(b, 50 * j + 50)
            s = 1.0 / (1.0 + np.exp(-single_l[0, 25 + (lo - 50 * j): 25 + (hi - 50 * j), 0]))
            m = 1.0 / (1.0 + np.exp(-many_l[0, 25 + (lo - 50 * j): 25 + (hi - 50 * j), 0]))
            singles[lo - a: hi - a] = s
            manys[lo - a: hi - a] = m
        return singles, manys

    # -- YOLO-family detectors (deepghs) -------------------------------------------------------------------------------
    def detect(self, model_id: str, letterboxed_rgb: np.ndarray) -> list:
        """letterboxed_rgb: uint8 [n, 384, 640, 3]. Runs in batches of at most MAX_BATCH. Returns, per image, a list of
        (cx, cy, w, h, score) boxes in the letterboxed image's pixels after threshold and NMS."""
        sess = self.session(model_id)
        thr = self.threshold(model_id)
        out = []
        for k in range(0, letterboxed_rgb.shape[0], MAX_BATCH):
            chunk = letterboxed_rgb[k:k + MAX_BATCH]
            x = chunk.astype(np.float32).transpose(0, 3, 1, 2) / 255.0
            t0 = time.perf_counter()
            o = sess.run(None, {"images": np.ascontiguousarray(x)})[0]
            self.stats.add(model_id, (time.perf_counter() - t0) * 1000)
            for bi in range(chunk.shape[0]):
                p = o[bi]
                if p.shape[0] < p.shape[1]:
                    p = p.T                                  # [N, 5]: cx, cy, w, h, score
                sc = p[:, 4]
                keep_m = sc >= thr
                p, sc = p[keep_m], sc[keep_m]
                if len(p) == 0:
                    out.append([])
                    continue
                boxes = np.stack([p[:, 0] - p[:, 2] / 2, p[:, 1] - p[:, 3] / 2, p[:, 2], p[:, 3]], 1)
                keep = cv2.dnn.NMSBoxes(boxes.tolist(), sc.tolist(), thr, NMS_IOU)
                res = []
                for i in np.array(keep).reshape(-1):
                    x0, y0, w, h = boxes[i]
                    res.append((float(x0 + w / 2), float(y0 + h / 2), float(w), float(h), float(sc[i])))
                out.append(res)
        return out


def letterbox(img_bgr: np.ndarray) -> tuple:
    """Fit an image into 640x384 keeping its aspect ratio, grey (114) padding. Returns (rgb uint8, ratio, pad_x, pad_y)."""
    h, w = img_bgr.shape[:2]
    r = min(YOLO_W / w, YOLO_H / h)
    nw, nh = int(round(w * r)), int(round(h * r))
    im = cv2.resize(img_bgr, (nw, nh), interpolation=cv2.INTER_AREA)
    pad = np.full((YOLO_H, YOLO_W, 3), 114, np.uint8)
    dx, dy = (YOLO_W - nw) // 2, (YOLO_H - nh) // 2
    pad[dy:dy + nh, dx:dx + nw] = im
    return cv2.cvtColor(pad, cv2.COLOR_BGR2RGB), r, dx, dy


def unletterbox(box: tuple, r: float, dx: int, dy: int) -> tuple:
    """(cx, cy, w, h, score) in letterboxed pixels -> (x, y, w, h, score) in the image the letterbox was made from."""
    cx, cy, w, h, s = box
    return ((cx - w / 2 - dx) / r, (cy - h / 2 - dy) / r, w / r, h / r, s)
