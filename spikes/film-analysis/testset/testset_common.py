"""Shared helpers for the film-analysis test set: paths, ffmpeg/ffprobe wrappers, frame tables, raw frame I/O, manifest I/O.

Everything here is plain Python 3.11 + NumPy. Scripts add this folder to sys.path themselves (python -I does not).

Timeline convention (the same rule as the worker's frame table): a clip's frame index is the position in presentation
order of the video stream's packets (sorted by pts, a repeated pts keeps the first, packets the demuxer marks for
discard are dropped). Labels and results use that index. Seconds are only for people to read.
"""
from __future__ import annotations

import hashlib
import json
import os
import struct
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from pathlib import Path
from typing import Iterator, Optional

import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
MEDIA = REPO / "tools" / "testset"                 # git-ignored: downloads, clips, sheets, cached runs
DOWNLOADS = MEDIA / "downloads"
CLIPS = MEDIA / "clips"
SHEETS = MEDIA / "sheets"
RUNS = MEDIA / "runs"
MANIFEST = HERE / "manifest.json"
SOURCES = HERE / "sources.json"
LABELS = HERE / "labels"
TZ = timezone(timedelta(hours=7))
MIB = 1 << 20
BELOW_NORMAL = 0x00004000 if os.name == "nt" else 0   # CREATE flag; keeps our encodes from disturbing timing runs of others

LOCAL_ANIME = Path(r"C:\Users\tamla\Downloads\[Judas] Nama-Anaru - S01E02.mkv")
LOCAL_HOTEL = Path(r"C:\Users\tamla\Downloads\260723_HOTEL_AMAN_43_V3_MAIN_50s_MASTER_16x9_VA_H264.mp4")


def today() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d")


# --------------------------------------------------------------------------------------------- processes
def run(cmd: list, *, check: bool = True, capture: bool = True, input_bytes: bytes | None = None, timeout: float | None = None):
    """subprocess.run at below-normal priority; text off (bytes in/out)."""
    cmd = [str(c) for c in cmd]
    r = subprocess.run(cmd, input=input_bytes, capture_output=capture, timeout=timeout, creationflags=BELOW_NORMAL)
    if check and r.returncode != 0:
        err = (r.stderr or b"").decode("utf-8", "replace")[-1500:]
        raise RuntimeError(f"command failed ({r.returncode}): {' '.join(cmd[:6])} ...\n{err}")
    return r


def popen(cmd: list, **kw) -> subprocess.Popen:
    return subprocess.Popen([str(c) for c in cmd], creationflags=BELOW_NORMAL, **kw)


def ffprobe_json(path, *args) -> dict:
    r = run(["ffprobe", "-v", "error", "-of", "json", *args, str(path)])
    return json.loads(r.stdout.decode("utf-8", "replace"))


# --------------------------------------------------------------------------------------------- identity
def sample_key(path) -> dict:
    """Same scheme as the worker's file_key.sample_key ("size+3x1MiB/sha256/0"): the cache key of a content model."""
    path = str(path)
    size = os.path.getsize(path)
    h = hashlib.sha256()
    h.update(b"panorama.filekey/0\0")
    h.update(struct.pack("<Q", size))
    with open(path, "rb") as f:
        if size <= 3 * MIB:
            h.update(f.read())
        else:
            for off in (0, size // 2 - MIB // 2, size - MIB):
                f.seek(off)
                h.update(f.read(MIB))
    return {"scheme": "size+3x1MiB/sha256/0", "size_bytes": size, "sample_sha256": h.hexdigest()}


def full_sha256(path, chunk: int = 8 * MIB) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                return h.hexdigest()
            h.update(b)


# --------------------------------------------------------------------------------------------- video facts
def video_info(path) -> dict:
    """Facts about the first video stream: codec, size, rate, pix_fmt, range, packet count, duration."""
    j = ffprobe_json(path, "-select_streams", "v:0", "-show_entries",
                     "stream=index,codec_name,profile,width,height,r_frame_rate,avg_frame_rate,pix_fmt,color_range,color_space,"
                     "color_transfer,time_base,nb_frames,duration,sample_aspect_ratio,field_order:format=duration,size,format_name")
    st = j["streams"][0]
    fps = Fraction(st["r_frame_rate"]) if st.get("r_frame_rate") not in (None, "0/0") else Fraction(st["avg_frame_rate"])
    return {"codec": st["codec_name"], "profile": st.get("profile"), "width": int(st["width"]), "height": int(st["height"]),
            "fps": f"{fps.numerator}/{fps.denominator}", "pix_fmt": st.get("pix_fmt"), "range": st.get("color_range"),
            "color_space": st.get("color_space"), "transfer": st.get("color_transfer"), "sar": st.get("sample_aspect_ratio"),
            "time_base": st["time_base"], "stream_index": int(st["index"]),
            "container": j["format"].get("format_name"), "duration_s": float(j["format"].get("duration", 0) or 0)}


def frame_table(path, stream: int = 0) -> tuple[np.ndarray, Fraction]:
    """(pts ticks int64 in presentation order, time base as Fraction). Packet list only: no decoding."""
    info = ffprobe_json(path, "-select_streams", f"v:{stream}", "-show_entries", "stream=time_base")
    tb = Fraction(info["streams"][0]["time_base"])
    r = run(["ffprobe", "-v", "error", "-select_streams", f"v:{stream}", "-show_entries", "packet=pts,flags", "-of", "csv=p=0", str(path)])
    pts = []
    for line in r.stdout.decode().splitlines():
        parts = line.strip().split(",")
        if len(parts) < 2 or parts[0] in ("N/A", ""):
            continue
        if "D" in parts[1]:
            continue
        pts.append(int(parts[0]))
    arr = np.unique(np.array(pts, dtype=np.int64))      # sorted, repeated pts keeps one
    return arr, tb


def keyframe_indices(path, stream: int = 0) -> np.ndarray:
    """Frame indices (in the table above) of packets flagged as keyframes."""
    r = run(["ffprobe", "-v", "error", "-select_streams", f"v:{stream}", "-show_entries", "packet=pts,flags", "-of", "csv=p=0", str(path)])
    rows = []
    for line in r.stdout.decode().splitlines():
        p = line.strip().split(",")
        if len(p) >= 2 and p[0] not in ("N/A", "") and "D" not in p[1]:
            rows.append((int(p[0]), "K" in p[1]))
    d = {}
    for pts, k in rows:
        d.setdefault(pts, k)
    keys = sorted(d)
    return np.array([i for i, p in enumerate(keys) if d[p]], dtype=np.int64)


NVDEC_CODECS = {"h264", "hevc", "av1", "vp9", "mpeg2video", "vc1", "mpeg4", "vp8"}


def hw_args(codec: str, enable: bool = True) -> list:
    return ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"] if enable and codec in NVDEC_CODECS else []


# --------------------------------------------------------------------------------------------- raw frame I/O
def yuv420_size(w: int, h: int) -> int:
    return w * h * 3 // 2


def split_yuv420(buf: bytes, w: int, h: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    a = np.frombuffer(buf, np.uint8)
    y = a[: w * h].reshape(h, w)
    u = a[w * h: w * h + (w // 2) * (h // 2)].reshape(h // 2, w // 2)
    v = a[w * h + (w // 2) * (h // 2):].reshape(h // 2, w // 2)
    return y, u, v


def read_yuv420_frames(path, w: int, h: int, *, fps: str | None = None, start_frame: int = 0, count: int | None = None,
                       vf: str = "", src_fps: str | None = None) -> Iterator[bytes]:
    """Decode (software) to 8-bit yuv420p at w x h; scale/pad to fit with `vf` if the source differs.

    start_frame/count select source frames by index through the select filter, which is exact (no -ss rounding).
    fps resamples to a constant rate (frames are dropped/duplicated by ffmpeg) and is applied BEFORE selection.
    """
    chain = []
    if fps:
        chain.append(f"fps={fps}")
    if start_frame or count is not None:
        stop = f"*lt(n,{start_frame + count})" if count is not None else ""
        chain.append(f"select='gte(n,{start_frame}){stop}',setpts=N/FRAME_RATE/TB")
    if vf:
        chain.append(vf)
    chain.append("format=yuv420p")
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-threads", os.environ.get("FA_EVAL_THREADS", "4"), "-i", str(path), "-an", "-sn", "-map", "0:v:0",
           "-vf", ",".join(chain), "-f", "rawvideo", "-pix_fmt", "yuv420p", "-"]
    p = popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    fs = yuv420_size(w, h)
    n = 0
    try:
        while True:
            buf = p.stdout.read(fs)
            if len(buf) < fs:
                break
            yield buf
            n += 1
            if count is not None and n >= count:
                break
    finally:
        try:
            p.stdout.close()
        except Exception:
            pass
        p.kill()
        p.wait()


class VideoWriter:
    """Pipe raw yuv420p frames into an ffmpeg encoder. `with VideoWriter(...) as w: w.write(bytes)`."""

    def __init__(self, path, w: int, h: int, fps: str, *, codec: str = "libx264", crf: int = 16, pix_fmt: str = "yuv420p",
                 preset: str = "medium", extra: list | None = None, in_pix_fmt: str = "yuv420p", threads: int = 8, vf: str = ""):
        self.path, self.w, self.h = str(path), w, h
        enc = ["-c:v", codec, "-crf", str(crf), "-preset", preset, "-pix_fmt", pix_fmt]
        if codec == "libx265":
            enc += ["-x265-params", f"log-level=error:pools={threads}:frame-threads=2", "-tag:v", "hev1"] if str(path).endswith(".mp4") else \
                   ["-x265-params", f"log-level=error:pools={threads}:frame-threads=2"]
        if codec == "libx264":
            enc += ["-threads", str(threads)]
        vfa = ["-vf", vf] if vf else []
        cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", in_pix_fmt,
               "-s", f"{w}x{h}", "-r", fps, "-i", "-", "-an", *vfa, *enc, *(extra or []), self.path]
        self.proc = popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        self.n = 0

    def write(self, buf: bytes) -> None:
        self.proc.stdin.write(buf)
        self.n += 1

    def __enter__(self):
        return self

    def __exit__(self, et, ev, tb):
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        err = self.proc.stderr.read().decode("utf-8", "replace")
        rc = self.proc.wait()
        if et is None and rc != 0:
            raise RuntimeError(f"encoder failed for {self.path}: {err[-800:]}")
        return False


def read_excerpt_yuv420(path, t_start: float, n_frames: int, w: int, h: int, *, fps: str | None = None, vf: str = "") -> Iterator[bytes]:
    """n_frames of 8-bit yuv420p at w x h starting at t_start seconds (input seek, then exact count). `fps` resamples to a constant rate.

    The seek lands on the first frame at or after t_start. `vf` runs before the final yuv420p conversion (scale, pad, ...).
    """
    chain = []
    if fps:
        chain.append(f"fps={fps}")
    if vf:
        chain.append(vf)
    chain.append("format=yuv420p")
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-threads", os.environ.get("FA_EVAL_THREADS", "4"),
           "-ss", f"{max(t_start, 0):.4f}", "-i", str(path), "-an", "-sn", "-map", "0:v:0", "-frames:v", str(n_frames),
           "-vf", ",".join(chain), "-f", "rawvideo", "-pix_fmt", "yuv420p", "-"]
    p = popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    fs = yuv420_size(w, h)
    try:
        while True:
            buf = p.stdout.read(fs)
            if len(buf) < fs:
                break
            yield buf
    finally:
        try:
            p.stdout.close()
        except Exception:
            pass
        p.kill()
        p.wait()


def frame_seconds(path, indices) -> np.ndarray:
    """Rebased presentation time (s) of the given frame indices (the worker's rule: t = pts*tb - pts[0]*tb)."""
    pts, tb = frame_table(path)
    t = (pts.astype(np.float64) - float(pts[0])) * float(tb)
    return t[np.asarray(indices, dtype=np.int64)]


# --------------------------------------------------------------------------------------------- manifest and labels
def load_json(path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_json(path, data, indent: int = 1) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=indent, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(p)


def load_manifest() -> dict:
    if MANIFEST.exists():
        return load_json(MANIFEST)
    return {"manifest_format": "panorama-testset-manifest/1", "media_root": "tools/testset", "clips": []}


def clip_by_id(man: dict, clip_id: str) -> dict:
    for c in man["clips"]:
        if c["id"] == clip_id:
            return c
    raise KeyError(f"clip {clip_id!r} is not in the manifest")


def upsert_clip(man: dict, entry: dict) -> None:
    for i, c in enumerate(man["clips"]):
        if c["id"] == entry["id"]:
            merged = dict(c)
            merged.update(entry)
            if "labels" in c and "labels" in entry:
                lab = dict(c["labels"])
                lab.update(entry["labels"])
                merged["labels"] = lab
            man["clips"][i] = merged
            return
    man["clips"].append(entry)


def clip_path(entry: dict) -> Path:
    p = Path(entry["path"])
    return p if p.is_absolute() else MEDIA / p


def label_file(clip_id: str, field: str) -> Path:
    return LABELS / clip_id / f"{field}.json"


def read_labels(clip_id: str, field: str) -> Optional[dict]:
    p = label_file(clip_id, field)
    return load_json(p) if p.exists() else None


def write_labels(clip_id: str, field: str, body: dict, *, status: str, proposed_by: str, basis: str, frame_count: int, fps: str) -> Path:
    """Write labels/<clip>/<field>.json with the common header. `status` is exact | agent-proposed | human-confirmed."""
    assert status in ("exact", "agent-proposed", "human-confirmed"), status
    hdr = {"label_format": "panorama-testset-labels/1", "clip": clip_id, "field": field, "status": status,
           "proposed_by": proposed_by, "basis": basis, "created": today(), "frame_count": frame_count, "fps": fps}
    hdr.update(body)
    p = label_file(clip_id, field)
    save_json(p, hdr)
    return p


def describe_clip(path, extra: dict | None = None) -> dict:
    """Manifest fields that come from the file itself."""
    vi = video_info(path)
    pts, tb = frame_table(path)
    out = {"video": dict(vi, frames=int(len(pts))), "identity": sample_key(path), "sha256": full_sha256(path),
           "size_bytes": os.path.getsize(path)}
    if extra:
        out.update(extra)
    return out


def rel_media(path) -> str:
    try:
        return str(Path(path).resolve().relative_to(MEDIA.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


def log(*a) -> None:
    print(*a, flush=True)


if __name__ == "__main__":
    sys.exit("tsutil is a library; import it from the scripts in this folder")
