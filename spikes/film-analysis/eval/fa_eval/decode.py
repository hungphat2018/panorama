"""Decoding helpers for the analysis methods: small frames out of ffmpeg, hardware decode with a software fallback.

Everything goes through an ffmpeg subprocess writing raw frames to a pipe. Hardware decode (NVDEC through -hwaccel cuda)
is tried first for codecs the GPU decodes and the run falls back to software if ffmpeg fails or returns nothing, so a
clip never silently yields fewer frames than the software path would.
"""
from __future__ import annotations

import subprocess
import os
from typing import Iterator

import numpy as np

import fa_eval  # noqa: F401  (puts testset/ on sys.path)
from testset_common import NVDEC_CODECS, popen, video_info


THREADS = os.environ.get("FA_EVAL_THREADS", "4")


def _vf_small(w: int, h: int, fmt: str, gpu: bool, src_pix_fmt: str, via: tuple[int, int] | None, scaler: str) -> str:
    """Filter chain that ends in raw `fmt` frames of w x h.

    gpu: scale on the GPU (scale_cuda) first, copy back, finish with a CPU area scale when `via` (an intermediate size) is given.
    """
    if gpu:
        vw, vh = via or (w, h)
        chain = f"scale_cuda={vw}:{vh}:format=nv12,hwdownload,format=nv12"
        if via and (vw, vh) != (w, h):
            chain += f",scale={w}:{h}:flags={scaler}"
    else:
        chain = f"scale={w}:{h}:flags={scaler}"
    return chain + f",format={fmt}"


def small_frames(path, w: int, h: int, *, fmt: str = "gray", gpu: bool | None = None, via: tuple[int, int] | None = (480, 270),
                 scaler: str = "area", chunk: int = 64, expect_frames: int | None = None) -> Iterator[np.ndarray]:
    """Yield uint8 arrays [n, h, w] (gray) or [n, h, w, 3] (rgb24) in chunks of up to `chunk` frames.

    gpu=None picks NVDEC when the codec is one the GPU decodes. If the GPU run fails or gives no frames, the software path runs.
    """
    info = video_info(path)
    use_gpu = (info["codec"] in NVDEC_CODECS) if gpu is None else gpu
    channels = 1 if fmt == "gray" else 3
    fsize = w * h * channels
    for attempt_gpu in ([True, False] if use_gpu else [False]):
        hw = ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"] if attempt_gpu else ["-threads", THREADS]
        vf = _vf_small(w, h, fmt, attempt_gpu, info["pix_fmt"] or "", via, scaler)
        cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", *hw, "-i", str(path), "-an", "-sn", "-map", "0:v:0",
               "-fps_mode", "passthrough", "-vf", vf, "-f", "rawvideo", "-pix_fmt", fmt, "-"]
        p = popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        got = 0
        try:
            while True:
                buf = p.stdout.read(fsize * chunk)
                if not buf:
                    break
                n = len(buf) // fsize
                if n == 0:
                    break
                arr = np.frombuffer(buf, np.uint8, n * fsize)
                arr = arr.reshape(n, h, w) if channels == 1 else arr.reshape(n, h, w, 3)
                got += n
                yield arr
        finally:
            try:
                p.stdout.close()
            except Exception:
                pass
            p.kill()
            err = p.stderr.read().decode("utf-8", "replace")
            p.wait()
        if got > 0:
            if expect_frames is not None and got != expect_frames:
                print(f"warning: {path}: decoded {got} frames, the frame table says {expect_frames}", flush=True)
            return
        if not attempt_gpu:
            raise RuntimeError(f"decode of {path} returned no frames: {err[-400:]}")
        # the GPU path produced nothing: the loop goes on with the software path (nothing was yielded, so no duplicates)


def all_small_frames(path, w: int, h: int, **kw) -> np.ndarray:
    parts = list(small_frames(path, w, h, **kw))
    return np.concatenate(parts, 0) if parts else np.zeros((0, h, w), np.uint8)
