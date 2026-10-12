"""Shot-boundary signals: TransNetV2 (ONNX), FFmpeg scdet, PySceneDetect, keyframes. Raw per-frame signals only.

Conventions (checked on exactly built cuts, see the report): frame index = presentation-order index of the clip's frame
table. A boundary `b` is the first frame of the new shot. TransNetV2's single-frame head is high on the LAST frame of the
old shot (frame b-1); scdet's score is high on the FIRST frame of the new shot (frame b). The policies in shots_policy.py
turn these signals into boundaries; here nothing is thresholded.
"""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
import time
from pathlib import Path

import numpy as np

import fa_eval
from fa_eval.decode import THREADS, all_small_frames
from testset_common import NVDEC_CODECS, REPO, keyframe_indices, popen, video_info

TN_MODEL = fa_eval.MODELS_DIR / "transnetv2" / "transnetv2.onnx"
EXTRAS_PY = REPO / "tools" / "testset" / "venv-extras" / "Scripts" / "python.exe"
PSD_SCRIPT = Path(__file__).resolve().parent / "psd_worker.py"


def _tn_windows(frames: np.ndarray):
    """Official TransNetV2 windowing: 100 frames, stride 50, first and last frames repeated as padding; keep the centre 50."""
    n = len(frames)
    pad_end = 25 + 50 - (n % 50 if n % 50 != 0 else 50)
    padded = np.concatenate([frames[:1]] * 25 + [frames] + [frames[-1:]] * pad_end, 0)
    ptr = 0
    while ptr + 100 <= len(padded):
        yield padded[ptr:ptr + 100][None]
        ptr += 50


def run_transnet(path, *, ep: str = "cuda", gpu_decode: bool | None = None, expect_frames: int | None = None) -> dict:
    """Both heads' probabilities per frame: single (cut between this frame and the next) and many (frame is inside a transition)."""
    import onnxruntime as ort
    t0 = time.perf_counter()
    frames = all_small_frames(path, 48, 27, fmt="rgb24", gpu=gpu_decode, via=(480, 270), scaler="area", expect_frames=expect_frames)
    t_dec = time.perf_counter() - t0
    if hasattr(ort, "preload_dlls"):
        ort.preload_dlls()
    prov = ["CUDAExecutionProvider", "CPUExecutionProvider"] if ep == "cuda" else ["CPUExecutionProvider"]
    sess = ort.InferenceSession(str(TN_MODEL), providers=prov)
    name = sess.get_inputs()[0].name
    wins = list(_tn_windows(frames))
    sess.run(None, {name: wins[0]})                                   # warm-up
    t1 = time.perf_counter()
    single, many = [], []
    for w in wins:
        o = sess.run(None, {name: w})
        single.append(1 / (1 + np.exp(-o[0][0, 25:75, 0])))
        many.append(1 / (1 + np.exp(-o[1][0, 25:75, 0])))
    n = len(frames)
    return {"single": np.concatenate(single)[:n].astype(np.float32), "many": np.concatenate(many)[:n].astype(np.float32),
            "n": n, "decode_s": t_dec, "infer_s": time.perf_counter() - t1, "providers": sess.get_providers()}


def run_scdet(path, *, gpu: bool | None = None, threshold: float = 10.0) -> np.ndarray:
    """FFmpeg scdet score per frame, on 480x270 gray (scdet reports mean absolute frame difference scaled to 0..100)."""
    info = video_info(path)
    use_gpu = (info["codec"] in NVDEC_CODECS) if gpu is None else gpu
    for g in ([True, False] if use_gpu else [False]):
        with tempfile.TemporaryDirectory() as td:
            meta = Path(td) / "scd.txt"
            mp = str(meta).replace("\\", "/").replace(":", "\\:")
            if g:
                vf = f"scale_cuda=480:270:format=nv12,hwdownload,format=nv12,format=gray,scdet=t={threshold},metadata=print:key=lavfi.scd.score:file='{mp}'"
                hw = ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"]
            else:
                vf = f"scale=480:270:flags=area,format=gray,scdet=t={threshold},metadata=print:key=lavfi.scd.score:file='{mp}'"
                hw = ["-threads", THREADS]
            cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", *hw, "-i", str(path), "-an", "-sn", "-map", "0:v:0",
                   "-fps_mode", "passthrough", "-vf", vf, "-f", "null", "-"]
            r = subprocess.run(cmd, capture_output=True, creationflags=0x4000)
            if r.returncode != 0 or not meta.exists():
                if g:
                    continue
                raise RuntimeError(f"scdet failed on {path}: {r.stderr.decode('utf-8', 'replace')[-400:]}")
            txt = meta.read_text()
        frames = [int(x) for x in re.findall(r"(?m)^frame:(\d+)", txt)]
        scores = [float(x) for x in re.findall(r"lavfi\.scd\.score=([0-9.]+)", txt)]
        k = min(len(frames), len(scores))
        arr = np.zeros(max(frames) + 1 if frames else 0, np.float32)
        arr[np.array(frames[:k], dtype=np.int64)] = np.array(scores[:k], dtype=np.float32)
        return arr
    raise RuntimeError("unreachable")


def run_pyscenedetect(path, out_npz: Path) -> dict:
    """PySceneDetect Content and Adaptive detectors in the extras venv (it is not in the worker's venv). Returns scene-start frames."""
    cmd = [str(EXTRAS_PY), "-I", str(PSD_SCRIPT), str(path), str(out_npz)]
    r = subprocess.run(cmd, capture_output=True, creationflags=0x4000)
    if r.returncode != 0:
        raise RuntimeError(f"PySceneDetect run failed: {r.stderr.decode('utf-8', 'replace')[-600:]}")
    z = np.load(out_npz)
    return {k: z[k] for k in z.files} | {"stdout": r.stdout.decode()}


def keyframes(path) -> np.ndarray:
    return keyframe_indices(path)
