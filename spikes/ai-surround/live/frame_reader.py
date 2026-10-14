"""Decoding for the live worker: ffmpeg on the CPU, from a keyframe before the first wanted frame, scaled to a fixed size
in RGB with the film's own matrix and range (film_timeline._colour_options), every frame named by its row in the frame
table: -copyts keeps the file's timestamps and showinfo prints each output frame's time, matched to the table within a
millisecond. Frames the table does not hold (a repeated timestamp it refused) are skipped.

    reader = FrameReader(film, first_frame, size=(960, 540), threads=4)
    for index, rgb in reader: ...        # rgb: (h, w, 3) uint8, a copy the caller may keep
    reader.close()
"""
from __future__ import annotations

import queue
import re
import subprocess
import threading

import numpy as np

from .film_timeline import Film
from film_analysis.proc import find_tool, popen

SHOWINFO = re.compile(r"\bn:\s*\d+\s+pts:\s*-?\d+\s+pts_time:\s*(-?[\d.]+)")


class FrameReader:
    def __init__(self, film: Film, first: int, size=(960, 540), threads: int = 4):
        self.film, self.w, self.h, self.first = film, size[0], size[1], first
        self.abs_times = film.origin_abs_s + film.times
        self.tol = min(0.001, 0.25 / max(1.0, film.fps))
        t_abs = float(self.abs_times[first])
        # a quarter second early: ffmpeg's accurate seek must never drop the wanted frame; earlier frames are skipped here
        seek = max(0.0, t_abs - film.container_start_s - 0.25)
        vf = (f"scale={self.w}:{self.h}:in_color_matrix={film.matrix}:in_range={film.in_range}:out_range=pc:flags=area,"
              "format=rgb24,showinfo=checksum=0")
        cmd = [find_tool("ffmpeg"), "-hide_banner", "-nostdin", "-loglevel", "info", "-nostats", "-threads", str(threads),
               "-copyts", "-ss", f"{seek:.6f}", "-i", film.path, "-an", "-sn", "-dn", "-map", f"0:{film.stream_index}",
               "-fps_mode", "passthrough", "-vf", vf, "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]
        self.proc = popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, bufsize=0)
        self._times = queue.Queue()
        self._err_tail = []
        self._err = threading.Thread(target=self._read_stderr, daemon=True)
        self._err.start()
        self._buf = bytearray(self.w * self.h * 3)

    def _read_stderr(self):
        for raw in iter(self.proc.stderr.readline, b""):
            line = raw.decode("utf-8", "replace")
            m = SHOWINFO.search(line)
            if m:
                self._times.put(float(m.group(1)))
            elif "showinfo" not in line:
                self._err_tail = (self._err_tail + [line.strip()])[-5:]
        self._times.put(None)

    def _read_frame(self) -> bool:
        mv, got, n = memoryview(self._buf), 0, len(self._buf)
        while got < n:
            k = self.proc.stdout.readinto(mv[got:])
            if not k:
                return False
            got += k
        return True

    def _index_of(self, t: float):
        i = int(np.searchsorted(self.abs_times, t))
        for j in (i - 1, i):
            if 0 <= j < len(self.abs_times) and abs(self.abs_times[j] - t) <= self.tol:
                return j
        return None

    def __iter__(self):
        return self

    def __next__(self):
        while True:
            if not self._read_frame():
                raise StopIteration
            try:
                t = self._times.get(timeout=30)
            except queue.Empty:
                raise RuntimeError("ffmpeg wrote a frame without its showinfo line")
            if t is None:
                raise StopIteration
            idx = self._index_of(t)
            if idx is None or idx < self.first:
                continue
            return idx, np.frombuffer(self._buf, np.uint8).reshape(self.h, self.w, 3).copy()

    def stderr_tail(self) -> str:
        return " | ".join(self._err_tail)

    def close(self):
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait()
        try:
            self.proc.stdout.close()
        except OSError:
            pass
        self._err.join(timeout=5)                                 # ends at the pipe's end, now that ffmpeg is gone
