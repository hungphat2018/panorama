"""Identity conditionings for the painter (role: paint, generated): phase 4's crops of characters cut by the seam
(<film cache>/identity/clusters.json) read by the 8B encoder on the CPU, once per crop, in the background.

The painter then gets the crop as a second reference latent, with a conditioning whose second image slot is the encoder's
read of that crop. painter-identity-test.py, jjk-one-side, 4 seeds: far error 32.6 -> 30.3, and the character keeps his
short hair where the painter alone gives him long hair (the blood stain still turns into a red collar). The canvas slot
reads a tiny grey canvas: what the canvas shows does not matter to the plate (phase 3), only that the slot is there.

The encoder (painter-service.py --te FILE --te-device cpu: ~9 GB of RAM; ~25 s a crop on every core, now confined: see
CPU_THREADS) runs
only while crops wait, and is stopped when none do. A plate whose crop has no conditioning yet is painted without it.

    <film cache>/identity/conds/<crop key>.png    the crop as the painter gets it (painter.fit_ref)
    <film cache>/identity/conds/<crop key>.pt     its conditioning (painter_core.save_cond)
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import threading
import time
import urllib.error
from typing import Optional

import cv2
import numpy as np

from .mosaic_t1 import GREY
from .painter import COMFY_PY, REPO, SERVICE, fit_ref, http_call

# the outpaint LoRA's instruction, as comfy-plates.INSTRUCTION (the live grey-proxy conditioning was read with it too)
INSTRUCTION = ("Outpaint the image: replace the solid gray areas with a seamless continuation of the scene, keeping the "
               "existing picture unchanged.")
READ_TIMEOUT_S = 600.0                                            # a CPU read takes ~25 s; a busy machine, longer
# E1 (2026-10-14): the encoder on every core cost the 240 Hz screen 156 wrong frames a minute next to the painter (no
# load: 0; the painter alone, phase 3: 2). It now reads at idle priority with CPU_THREADS threads on the upper half of
# the logical CPUs (this laptop's second 8-core CCD), leaving the other half to the player.
CPU_THREADS = 8


def crop_key(path: str) -> str:
    """A crop's key: its content (phase 4 writing the same crop again keeps its conditioning)."""
    with open(path, "rb") as f:
        return hashlib.sha1(f.read()).hexdigest()[:16]


class IdentityEncoder(threading.Thread):
    def __init__(self, folder: str, te_file: str, port: int, log):
        super().__init__(daemon=True, name="identity-encoder")
        self.dir = os.path.join(folder, "conds")
        self.te_file, self.port, self.log = te_file, port, log
        self.url = f"http://127.0.0.1:{port}"
        self.queue: list = []
        self.cv = threading.Condition()
        self.stopping = False
        self.proc = None
        self.made = 0
        self.busy = False                                        # the queue's head is being read

    def ready(self, crop: str) -> Optional[tuple]:
        """(conditioning, the crop as the painter gets it) when made; else None, and the crop waits for the encoder."""
        try:
            k = crop_key(crop)
        except OSError:
            return None
        cond, png = os.path.join(self.dir, f"{k}.pt"), os.path.join(self.dir, f"{k}.png")
        if os.path.isfile(cond) and os.path.isfile(png):
            return cond, png
        with self.cv:                                             # a plate waits for it: next to be read
            if self.busy and self.queue and self.queue[0] == crop:
                return None                                       # being read now
            if crop in self.queue:
                self.queue.remove(crop)
            self.queue.insert(1 if self.busy and self.queue else 0, crop)
            self.cv.notify()
        return None

    def prefetch(self, crops):
        """Crops to read ahead, after every plate's own request (the clusters cut most often first)."""
        with self.cv:
            for crop in crops:
                if crop not in self.queue:
                    self.queue.append(crop)
            self.cv.notify()

    def stop(self):
        with self.cv:
            self.stopping = True
            self.cv.notify()

    def run(self):
        while True:
            with self.cv:
                idle = not self.queue and not self.stopping and self.proc is not None
                while not self.queue and not self.stopping and not idle:
                    self.cv.wait()
                if self.stopping:
                    break
                crop = None if idle else self.queue[0]
                self.busy = crop is not None
            if crop is None:                                      # nothing waits: give the RAM back (outside the lock:
                self._stop_service_safely()                       # stopping takes seconds, plates must not wait)
                continue
            try:
                self._read(crop)
            except Exception as e:                                # a bad crop must not stop the others; a service that
                self.log(f"identity: {os.path.basename(crop)} not read: {e}")   # failed a read is not trusted again
                self._stop_service_safely()
            with self.cv:
                self.busy = False
                if crop in self.queue:
                    self.queue.remove(crop)
        self._stop_service_safely()

    def _stop_service_safely(self):
        try:
            self._stop_service()
        except Exception as e:
            self.log(f"identity: stopping the encoder service failed: {e!r}")
            self.proc = None

    def _read(self, crop: str):
        k = crop_key(crop)
        cond, png = os.path.join(self.dir, f"{k}.pt"), os.path.join(self.dir, f"{k}.png")
        if os.path.isfile(cond) and os.path.isfile(png):
            return
        os.makedirs(self.dir, exist_ok=True)
        img = cv2.imread(crop)
        if img is None:
            raise ValueError("unreadable image")
        tmp = png + ".tmp.png"
        cv2.imwrite(tmp, fit_ref(img))
        os.replace(tmp, png)
        grey = os.path.join(self.dir, "grey-canvas.png")
        if not os.path.isfile(grey):
            cv2.imwrite(grey + ".tmp.png", np.full((64, 128, 3), GREY, np.uint8))
            os.replace(grey + ".tmp.png", grey)
        self._start_service()
        r = http_call(self.url, "/encode", {"prompt": INSTRUCTION, "images": [grey, png], "out": cond + ".tmp"},
                      timeout=READ_TIMEOUT_S)
        os.replace(cond + ".tmp", cond)
        self.made += 1
        self.log(f"identity: {os.path.basename(crop)} read in {r['seconds']:.0f} s (slots {r.get('image_slots')})")

    def _ping(self) -> bool:
        try:
            http_call(self.url, "/status", timeout=2.0)
            return True
        except (urllib.error.URLError, OSError, RuntimeError):
            return False

    def _start_service(self):
        if self.proc is not None and self.proc.poll() is None:
            return
        if self._ping():
            raise RuntimeError(f"port {self.port} already answers: the identity encoder needs its own service")
        log_path = os.path.join(self.dir, "encoder-service.log")
        n = os.cpu_count() or 2
        cmd = [COMFY_PY, "-s", SERVICE, "--port", str(self.port), "--parent-pid", str(os.getpid()),
               "--log", log_path.replace(".log", "-comfy.log"), "--te", self.te_file, "--te-device", "cpu",
               "--cpu-priority", "idle", "--cpu-threads", str(CPU_THREADS), "--cpu-affinity", f"{n // 2}-{n - 1}"]
        self.proc = subprocess.Popen(cmd, cwd=REPO, stdout=open(log_path, "a", encoding="utf-8"), stderr=subprocess.STDOUT,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        t0 = time.monotonic()
        while not self._ping():
            if self.proc.poll() is not None:
                raise RuntimeError(f"encoder service exited with {self.proc.returncode}; see {log_path}")
            if time.monotonic() - t0 > 300:
                self._stop_service()
                raise RuntimeError("encoder service did not answer within 300 s")
            time.sleep(0.5)
        self.log(f"identity: CPU encoder service up in {time.monotonic() - t0:.0f} s")

    def _stop_service(self):
        if self.proc is None:
            return
        if self.proc.poll() is None:
            try:
                http_call(self.url, "/quit", {}, timeout=5.0)
                self.proc.wait(timeout=30)
            except (RuntimeError, OSError, urllib.error.URLError, subprocess.TimeoutExpired):
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
        self.proc = None
