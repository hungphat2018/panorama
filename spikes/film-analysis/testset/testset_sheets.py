"""Contact sheets: PNG frame strips around events, for the user's quick confirmation of agent-proposed labels.

grab_frames() reads the clip once, sequentially, and keeps the wanted frames (small, BGR). The sheet builders only draw.
Sheets are written under tools/testset/sheets/<clip>/ (git-ignored: they are pictures from films); the label files
point at them by sheet name and cell number.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Iterable

import cv2
import numpy as np

from testset_common import NVDEC_CODECS, SHEETS, popen, video_info


def grab_frames(path, wanted: Iterable[int], width: int = 480, *, gpu: bool | None = None) -> dict[int, np.ndarray]:
    """{frame index: BGR image width x (width*9/16 rounded to even)} for the wanted frame indices (presentation order).

    The aspect ratio of the clip is kept (height follows). One sequential pass; stops after the last wanted frame.
    """
    want = sorted({int(i) for i in wanted if i >= 0})
    if not want:
        return {}
    info = video_info(path)
    height = int(round(info["height"] * width / info["width"] / 2)) * 2
    use_gpu = (info["codec"] in NVDEC_CODECS) if gpu is None else gpu
    out: dict[int, np.ndarray] = {}
    for g in ([True, False] if use_gpu else [False]):
        if g:
            hw = ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"]
            vf = f"scale_cuda={width}:{height}:format=nv12,hwdownload,format=nv12,format=bgr24"
        else:
            hw = ["-threads", os.environ.get("FA_EVAL_THREADS", "4")]
            vf = f"scale={width}:{height}:flags=area,format=bgr24"
        cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", *hw, "-i", str(path), "-an", "-sn", "-map", "0:v:0",
               "-fps_mode", "passthrough", "-vf", vf, "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
        p = popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        fs = width * height * 3
        i, k, last = 0, 0, want[-1]
        wset = set(want)
        try:
            while i <= last:
                buf = p.stdout.read(fs)
                if len(buf) < fs:
                    break
                if i in wset:
                    out[i] = np.frombuffer(buf, np.uint8).reshape(height, width, 3).copy()
                i += 1
        finally:
            p.kill()
            p.wait()
        if out:
            return out
    return out


def caption(img: np.ndarray, text: str, *, color=(255, 255, 255), scale: float = 0.4) -> np.ndarray:
    cv2.rectangle(img, (0, 0), (img.shape[1], 15), (0, 0, 0), -1)
    cv2.putText(img, text, (3, 11), cv2.FONT_HERSHEY_SIMPLEX, scale, color, 1, cv2.LINE_AA)
    return img


def thumb(img: np.ndarray | None, w: int, h: int) -> np.ndarray:
    if img is None:
        return np.zeros((h, w, 3), np.uint8)
    return cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)


def event_row(frames: dict[int, np.ndarray], idx: list[int], labels: list[str], title: str, tw: int, th: int,
              mark_gap: int | None = None) -> np.ndarray:
    """One row: thumbnails of frames idx with small labels, a title bar on the first thumbnail. mark_gap: draw a red line after that column."""
    cells = []
    for k, (i, lab) in enumerate(zip(idx, labels)):
        t = thumb(frames.get(i), tw, th)
        cv2.putText(t, lab, (3, th - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (0, 255, 255), 1, cv2.LINE_AA)
        if k == 0:
            caption(t, title)
        cells.append(t)
    row = np.hstack(cells)
    if mark_gap is not None and 0 < mark_gap < len(idx):
        x = mark_gap * tw
        cv2.line(row, (x, 16), (x, th), (0, 0, 255), 1)
    return row


def stack_rows(rows: list[np.ndarray], cols: int) -> np.ndarray:
    """Place rows in a grid of `cols` columns."""
    if not rows:
        return np.zeros((10, 10, 3), np.uint8)
    h, w = rows[0].shape[:2]
    while len(rows) % cols:
        rows.append(np.zeros((h, w, 3), np.uint8))
    lines = [np.hstack(rows[r:r + cols]) for r in range(0, len(rows), cols)]
    return np.vstack(lines)


def write_sheet(clip_id: str, name: str, img: np.ndarray) -> Path:
    d = SHEETS / clip_id
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{name}.png"
    cv2.imwrite(str(p), img)
    return p
