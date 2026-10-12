"""How frames are decoded for analysis: an ffmpeg subprocess, on the GPU's decoder when it has one, never assuming CUDA.

Three paths, tried in this order for each file (the first that decodes the file's video stream is used; `--decode-path`
forces one):
  cuda      -hwaccel cuda (NVDEC) with frames kept on the GPU, scaled there (scale_cuda), then copied back:
            2,500 fps for 1080p HEVC on the research PC with the shader cores idle.
  d3d11va   -hwaccel d3d11va: any Windows GPU's video decoder; frames come back to system memory and swscale shrinks them.
  software  libavcodec on the CPU; the fallback that always works.

Two kinds of streams are decoded per segment:
  small     960x540 NV12 frames (the picture for motion, shots, bars-edge and faces),
  full      full-size frames into cropdetect (needs exact pixel rows and columns).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .proc import find_tool, popen, run_text

SMALL_W, SMALL_H = 960, 540
PATHS = ("cuda", "d3d11va", "software")


@dataclass(frozen=True)
class StreamRef:
    """Which stream of which file, and how seeking must be asked of ffmpeg."""
    path: str
    stream_index: int
    container_start_s: float      # format start_time: ffmpeg's -ss is relative to it unless -seek_timestamp is on
    pix_fmt: str                  # the file's pixel format, e.g. yuv420p10le


def _hw_input(path_name: str) -> list:
    if path_name == "cuda":
        return ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"]
    if path_name == "d3d11va":
        return ["-hwaccel", "d3d11va"]
    return []


def sw_pixfmt_for_download(pix_fmt: str) -> str:
    """The system-memory format NVDEC hands back for a stream: 8-bit -> nv12, 10-bit -> p010le, 12-bit and more -> p016le."""
    from .file_facts import bit_depth_and_chroma
    depth, _chroma = bit_depth_and_chroma(pix_fmt)
    if depth is None or depth <= 8:
        return "nv12"
    return "p010le" if depth <= 10 else "p016le"


def small_filter(path_name: str, showinfo: bool = True) -> str:
    """960x540 NV12 for every frame (the scaling done on the GPU when the frames are there)."""
    if path_name == "cuda":
        chain = f"scale_cuda={SMALL_W}:{SMALL_H}:format=nv12,hwdownload,format=nv12"
    else:
        chain = f"scale={SMALL_W}:{SMALL_H}:flags=bilinear,format=nv12"
    return chain + (",showinfo=checksum=0" if showinfo else "")


def full_filter(path_name: str, pix_fmt: str, limit: float, metadata_file: str) -> str:
    """Full-size frames into cropdetect (round=2, reset=1: one rectangle per frame, the research setting), results printed
    to a text file by the metadata filter. `limit` is the black threshold as a fraction of full scale."""
    pre = f"hwdownload,format={sw_pixfmt_for_download(pix_fmt)}," if path_name == "cuda" else ""
    return (f"{pre}cropdetect=limit={limit:.5f}:round=2:reset=1:skip=0,"
            f"metadata=print:file={_filter_file_name(metadata_file)}")


def _filter_file_name(name: str) -> str:
    """The metadata filter's file name as a bare relative name (ffmpeg runs in the folder that holds it), so no quoting of
    drive letters or spaces is needed."""
    if any(c in name for c in ":\\/' ,;[]="):
        raise ValueError(f"metadata file name must be a bare file name: {name!r}")
    return name


def seek_args(ref: StreamRef, start_abs_s: Optional[float]) -> list:
    """Input seeking to a moment on the file's own (absolute) timeline, with output timestamps kept as they are: -copyts, and
    -ss relative to the container start. Accurate seek then discards frames before the moment."""
    args = ["-copyts"]
    if start_abs_s is not None:
        args += ["-ss", f"{start_abs_s - ref.container_start_s:.6f}"]
    return args


def decode_cmd(path_name: str, ref: StreamRef, start_abs_s: Optional[float], n_frames: Optional[int], filter_chain: str,
               output: list, keyframes_only: bool = False, log_level: str = "info") -> list:
    """One ffmpeg command line: input (with the path's hwaccel), seek, one video stream, `filter_chain`, then `output`
    (for example ['-f', 'rawvideo', '-pix_fmt', 'nv12', 'pipe:1'])."""
    cmd = [find_tool("ffmpeg"), "-hide_banner", "-nostdin", "-loglevel", log_level, "-nostats"]
    cmd += seek_args(ref, start_abs_s)
    if keyframes_only:
        cmd += ["-skip_frame", "nokey"]
    cmd += _hw_input(path_name)
    cmd += ["-i", ref.path, "-an", "-sn", "-dn", "-map", f"0:{ref.stream_index}", "-fps_mode", "passthrough"]
    if n_frames is not None:
        cmd += ["-frames:v", str(n_frames)]
    cmd += ["-vf", filter_chain] + output
    return cmd


def probe_decode_path(ref: StreamRef, preferred: str = "auto") -> dict:
    """Decodes the first frames through each path and returns {'path': name, 'tried': {name: 'ok' | error text}}."""
    order = list(PATHS) if preferred == "auto" else [preferred]
    tried = {}
    for name in order:
        cmd = decode_cmd(name, ref, None, 3, small_filter(name, showinfo=False), ["-f", "null", "-"], log_level="error")
        r = run_text(cmd, timeout=120)
        if r.returncode == 0:
            tried[name] = "ok"
            return {"path": name, "tried": tried}
        tried[name] = (r.stderr.strip().splitlines() or ["failed"])[-1][:200]
    return {"path": "software", "tried": tried, "note": "no path decoded the stream; software assumed"}
