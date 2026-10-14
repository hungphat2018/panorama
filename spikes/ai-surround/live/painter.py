"""T2 plate (role: paint, generated), worker side: the resident painter service (painter-service.py, a child process in
ComfyUI's embedded Python) paints the shot's mosaic; this module sizes the canvas, asks for the plate, fits its colour to
the real pixels and keeps the real pixels where they were seen.

Recipe (phase 3, plans/261013-0718-ai-surround-live-path/phase-03-painter-at-speed.md): Qwen-Image-2.1 with X2's Turbo
LoRA and the outpaint LoRA v2 merged into one W4A8 DiT (painter-quantize.py), euler over 5 of the Turbo card's sigmas,
CFG 1, INT8 attention, about 1 MP, seed 7, the text + reference K/V cached per plate in VRAM (int8): ~5.2 s a plate,
with the 25-step plate's hidden-sides scores. The 8B encoder is not in the painter's process: the plate's conditioning
is read once, ahead, from a file (cond), and the painter gets the canvas itself as the reference latent. Identity
references (identity_refs): a character crop as a second reference latent, with a conditioning the CPU encoder read
from that crop (identity_encoder.py). The painter sees the mosaic: the picture and every real pixel the camera revealed, grey
where nothing was seen; the plate comes back at the canvas size, its colours fitted to the real pixels
(hidden_sides.colour_fit over the first frame's picture), the real pixels kept where seen (feathered 12 texels into the
painted ones). Every texel is then covered (alpha 255).

GPU sharing (E1, phases 2-3): the service loads the DiT and the VAE once, completely, and never moves them (its loader
is frozen; a reload is an error), reserves its memory pool with a warm-up plate at the largest canvas before the film
plays, and is capped at --vram-cap-gb; it keeps one attention kernel (of at most 2048 queries), MLP or VAE convolution
in flight, so the player's frames never wait behind a deep queue; the scheduler starts no plate while the player
reports pressure. The service runs at
below-normal priority, inside the worker's kill-on-close job, and also exits when the worker's pid is gone.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass

import cv2
import numpy as np

from .mosaic_t1 import FEATHER_TEXELS, Mosaic

import hidden_sides as hs  # noqa: E402  (spikes/ai-surround is on sys.path: the worker's folder)

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))     # spikes/ai-surround
REPO = os.path.dirname(os.path.dirname(HERE))
COMFY_PY = os.path.join(REPO, "tools", "ComfyUI_windows_portable", "python_embeded", "python.exe")
SERVICE = os.path.join(HERE, "painter-service.py")
TARGET_PIXELS = 1.03e6                                             # X2's ~1 MP canvas (1344x768 on a 16:9 picture)
PLATE_TIMEOUT_S = 120.0                                            # a hung service must not stop every plate after it
SEED = 7


def http_call(url: str, path: str, payload=None, timeout: float = 10.0) -> dict:
    """POST (with a payload) or GET a painter service's JSON endpoint."""
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url + path, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"painter {path}: {e.code} {e.read()[:1000]!r}") from None


@dataclass
class PainterConfig:
    dit: str = "qwen_image_2.1_turbo8-ov2_w4a8.safetensors"         # painter-quantize.py's file, LoRAs merged
    cond: str = os.path.join(HERE, "out", "live", "painter", "cond-proxy-grey.pt")
    kv_cache: str = "gpu-int8"
    # phase 3's levers (13 scenes, scores within the baseline's noise): INT8 attention (6.8 s a plate from 8.1) and the
    # Turbo schedule at 5 steps (6 steps: 5.8 s with chunked attention; 5 steps: 5.2 s, scores and plates within 1.3
    # codes of 6 steps)
    attention: str = "ck-int8"                                      # or "pytorch" (painter-service.py --attention)
    schedule: str = "turbo5"                                        # painter_core.SCHEDULES
    ref_scale: float = 1.0                                          # reference latent size against the canvas
    gpu_queue: str = "layer"                                       # one attention / MLP / VAE conv in flight (E1: 5x fewer misses)
    gpu_priority: str = "normal"                                   # painter-service.py --gpu-priority
    # attention in kernels of at most 2048 queries, one in flight (identical plates, +5 % time): with it E1's painter cell
    # matches the no-load cell at 240 Hz (2.0 / 1.0 wrong / missed a minute against 1.0 / 2.6; 3.1 / 6.1 without it)
    attn_chunk: int = 2048                                         # painter-service.py --attn-chunk (0: whole)
    # identity references (phase 4's crops of characters cut by the seam): one crop a plate, with the conditioning the
    # CPU encoder read from it (identity_encoder.py); 4 seeds on jjk-one-side: far error 32.6 -> 30.3, hair kept
    identity_refs: bool = False
    te: str = "qwen3vl_8b_int8_convrot.safetensors"                # the encoder that reads the crops (on the CPU)
    identity_port: int = 8196
    vram_cap_gb: float = 7.0
    warmup: str = "1856x576"                                        # a bit above the largest ~1 MP canvas (reserves the pool)
    port: int = 8191

    @property
    def recipe(self) -> str:
        """What the plates of this painter depend on (the cache key's paint part)."""
        return (f"resident-v1|dit:{os.path.basename(self.dit)}|cond:{os.path.basename(self.cond)}|1mp|{self.schedule}|"
                f"seed{SEED}|kv:{self.kv_cache}|attn:{self.attention}|ref{self.ref_scale:g}")


REF_PIXELS = 0.15e6                                                # an identity crop's size for the painter


def fit_ref(bgr: np.ndarray) -> np.ndarray:
    """An identity crop at most REF_PIXELS, sides multiples of 32 (one reference latent of ~600 prefix tokens)."""
    h, w = bgr.shape[:2]
    f = min(1.0, (REF_PIXELS / float(w * h)) ** 0.5)
    rw, rh = max(32, int(round(w * f / 32)) * 32), max(32, int(round(h * f / 32)) * 32)
    return cv2.resize(bgr, (rw, rh), interpolation=cv2.INTER_AREA)


def canvas_size(w: int, h: int) -> tuple:
    """The painter's canvas for a mosaic of w x h texels: about TARGET_PIXELS, multiples of 32, aspect kept."""
    f = (TARGET_PIXELS / float(w * h)) ** 0.5
    return max(64, int(round(w * f / 32)) * 32), max(64, int(round(h * f / 32)) * 32)


class Painter:
    def __init__(self, cfg: PainterConfig, log_path: str, log):
        self.cfg, self.log_path, self.log = cfg, log_path, log
        self.proc = None
        self.url = f"http://127.0.0.1:{cfg.port}"
        self.tmp = tempfile.mkdtemp(prefix="panorama-painter-")
        self.started_s = None

    def _call(self, path: str, payload=None, timeout: float = 10.0) -> dict:
        return http_call(self.url, path, payload, timeout)

    def _ping(self) -> bool:
        try:
            self._call("/status", timeout=2.0)
            return True
        except (urllib.error.URLError, OSError, RuntimeError):
            return False

    def start(self):
        if self._ping():
            raise RuntimeError(f"port {self.cfg.port} already answers: the live painter needs its own service")
        if not os.path.isfile(self.cfg.cond):
            raise RuntimeError(f"no painter conditioning {self.cfg.cond} (painter-service.py --te ... POST /encode)")
        cmd = [COMFY_PY, "-s", SERVICE, "--port", str(self.cfg.port), "--parent-pid", str(os.getpid()),
               "--log", self.log_path.replace(".log", "-comfy.log"), "--dit", self.cfg.dit, "--kv-cache", self.cfg.kv_cache,
               "--vram-cap-gb", f"{self.cfg.vram_cap_gb:g}", "--warmup", self.cfg.warmup, "--attention", self.cfg.attention,
               "--gpu-queue", self.cfg.gpu_queue, "--gpu-priority", self.cfg.gpu_priority,
               "--attn-chunk", str(self.cfg.attn_chunk)]
        t0 = time.monotonic()
        self.proc = subprocess.Popen(cmd, cwd=REPO, stdout=open(self.log_path, "w", encoding="utf-8"),
                                     stderr=subprocess.STDOUT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        while not self._ping():
            if self.proc.poll() is not None:
                raise RuntimeError(f"painter service exited with {self.proc.returncode}; see {self.log_path}")
            if time.monotonic() - t0 > 300:
                self.stop()
                raise RuntimeError("painter service did not answer within 300 s")
            time.sleep(0.5)
        self.started_s = time.monotonic() - t0
        st = self._call("/status")
        self.log(f"paint: painter service pid {st['pid']} ready in {self.started_s:.0f} s, resident "
                 f"{st['painter']['fully_loaded']}, VRAM {st['vram']}")

    def stop(self):
        if self.proc is not None and self.proc.poll() is None:
            try:
                self._call("/quit", {}, timeout=5.0)
                self.proc.wait(timeout=30)
            except (RuntimeError, OSError, urllib.error.URLError, subprocess.TimeoutExpired):
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
        self.proc = None

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def paint(self, m: Mosaic, crop_texels: tuple, identity=None) -> tuple:
        """(T2 canvas RGBA at the mosaic's size, seconds, the service's stage timings). crop_texels: the first frame's
        picture in texels (the colour fit's region). identity: (conditioning, crop PNG) from identity_encoder, or None:
        the crop becomes the second reference latent and that conditioning replaces cond."""
        h, w = m.tex_h, m.tex_w
        pw, ph = canvas_size(w, h)
        f = pw / float(w)
        canvas = cv2.resize(m.painter_input(), (pw, ph), interpolation=cv2.INTER_AREA if f < 1 else cv2.INTER_LINEAR)
        name = uuid.uuid4().hex[:12]
        os.makedirs(self.tmp, exist_ok=True)                     # a temp cleaner may have taken it since the start
        in_path, out_path = os.path.join(self.tmp, f"{name}-in.png"), os.path.join(self.tmp, f"{name}-out.png")
        if not cv2.imwrite(in_path, cv2.cvtColor(canvas, cv2.COLOR_RGB2BGR)):
            raise OSError(f"cannot write the painter's canvas {in_path}")
        cond, ref_paths = (identity[0], [identity[1]]) if identity else (self.cfg.cond, [])
        t0 = time.perf_counter()
        try:
            res = self._call("/paint", {"canvas": in_path, "cond": cond, "seed": SEED, "out": out_path,
                                        "schedule": self.cfg.schedule, "ref_scale": self.cfg.ref_scale,
                                        "extra_refs": ref_paths}, timeout=PLATE_TIMEOUT_S)
            plate = cv2.cvtColor(cv2.imread(out_path), cv2.COLOR_BGR2RGB)
        finally:
            seconds = time.perf_counter() - t0
            for p in (in_path, out_path):
                try:
                    os.remove(p)
                except OSError:
                    pass
        plate = cv2.resize(plate, (w, h), interpolation=cv2.INTER_LANCZOS4 if f < 1 else cv2.INTER_AREA).astype(np.float32)
        x0, y0, x1, y1 = (int(v) for v in crop_texels)
        x0, y0, x1, y1 = max(0, x0), max(0, y0), min(w, x1), min(h, y1)
        if x1 - x0 >= 16 and y1 - y0 >= 16:
            mfit = hs.colour_fit(plate[y0:y1, x0:x1], m.rgb[y0:y1, x0:x1].astype(np.float32))
            plate = hs.apply_fit(plate, mfit)
        dist = cv2.distanceTransform(m.coverage.astype(np.uint8), cv2.DIST_L2, 5)
        keep = np.clip(dist / FEATHER_TEXELS, 0.0, 1.0)[..., None]
        rgb = keep * m.rgb.astype(np.float32) + (1.0 - keep) * plate
        out = np.empty((h, w, 4), np.uint8)
        out[..., :3] = np.clip(np.round(rgb), 0, 255).astype(np.uint8)
        out[..., 3] = 255
        return out, seconds, res.get("seconds", {})
