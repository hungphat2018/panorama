"""Painter service: the paint role's GPU process (Qwen-Image-2.1 + outpaint LoRA, painter_core.py), resident, answering
one request at a time on 127.0.0.1. Runs in ComfyUI's embedded Python; clients (live/painter.py in the worker,
painter-bench.py) run in tools/venv-outpaint and pass images and conditionings as files.

    tools/ComfyUI_windows_portable/python_embeded/python.exe -s spikes/ai-surround/painter-service.py
        [--port 8192] [--log FILE] [--parent-pid PID] [--cpu-priority below|idle] [--cpu-threads N] [--cpu-affinity A-B]
        [--te FILE [--te-device default|cpu]]                     encoder: POST /encode
        [--dit FILE [--lora FILE:STRENGTH ...] [--vae FILE] [--kv-cache gpu-int8|gpu|off|auto]
         [--attention pytorch|ck-int8] [--vae-tile TILE:OVERLAP] [--warmup WxH] [--vram-cap-gb G]
         [--gpu-queue off|layer|block] [--attn-chunk N] [--gpu-priority normal|below|idle]]
                                                                  painter: POST /paint

Endpoints (JSON in, JSON out):
  GET  /status   what is loaded, VRAM (torch allocator and device), plates served
  POST /encode   {"prompt", "image": PNG path or null, or "images": [PNG paths], "out": .pt path} -> conditioning
                 file with one image slot per image; the images are what the encoder reads (the canvas, a thumbnail
                 of it, or a proxy; then identity references)
  POST /paint    {"canvas": PNG path, "cond": .pt path, "seed": 7, "out": PNG path, optional "schedule" (turbo8,
                 turbo6, turbo5) or "sigmas", "ref_scale" (a smaller reference latent), "extra_refs": [PNG paths] (identity
                 references; the conditioning needs as many more image slots), "sides": [[x0, x1], ...] (canvas columns,
                 multiples of 16: only those are painted, the rest is the reference), "sampler" (euler, or another
                 ComfyUI sampler such as res_multistep)} -> the plate at the canvas size,
                 seconds per stage (vae_encode, sample, vae_decode), VRAM, whether anything of the models is off the GPU
  POST /quit

Live rules (E1, phase 2): the painter is loaded once and completely (no weight moves while the film plays); --warmup paints
one plate at the largest canvas at start so the allocator's pool is reserved before playback; --vram-cap-gb makes any
growth beyond the budget an error instead of a silent move; the encoder never shares the GPU with the painter in the live
path (its conditioning is computed once, ahead; see phase 3). The process runs at below-normal CPU priority and exits when
--parent-pid is gone (no orphans).
"""
import argparse
import json
import os
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, HTTPServer

import numpy as np
import psutil
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))     # -s/-I leave the script's folder off sys.path
import painter_core as pc  # noqa: E402

KV_CACHE = {"gpu-int8": {"device": "gpu", "dtype": "int8"}, "gpu": {"device": "gpu", "dtype": "default"},
            "off": {"device": "off", "dtype": "default"}, "auto": None}


def read_rgb(path):
    return np.asarray(Image.open(path).convert("RGB"))


class Service:
    def __init__(self, a):
        self.a = a
        self.encoder = self.painter = None
        self.plates = 0
        self.started = time.time()
        self.lock = threading.Lock()

    def load(self):
        a = self.a
        if a.te:
            t0 = time.perf_counter()
            self.encoder = pc.Encoder(a.te, a.te_device)
            print(f"encoder {a.te} on {a.te_device} ready in {time.perf_counter() - t0:.1f} s", flush=True)
        if a.dit:
            loras = []
            for spec in a.lora:
                file, _, strength = spec.rpartition(":")
                loras.append((file, float(strength)))
            tile = tuple(int(v) for v in a.vae_tile.split(":")) if a.vae_tile else None
            t0 = time.perf_counter()
            self.painter = pc.Painter(a.dit, a.vae, loras, KV_CACHE[a.kv_cache], vae_tile=tile)
            loaded = self.painter.load(int(a.vram_cap_gb * 2**30) if a.vram_cap_gb else None, a.gpu_queue,
                                       a.attn_chunk)
            print(f"painter {a.dit} + {loras} ready in {time.perf_counter() - t0:.1f} s: {loaded}; "
                  f"fully loaded {self.painter.fully_loaded()}; {pc.vram_state()}", flush=True)
            if a.warmup:
                w, h = (int(v) for v in a.warmup.lower().split("x"))
                ctx = pc.torch.zeros(1, 20, 4096)
                _, secs = self.painter.paint(np.full((h, w, 3), 128, np.uint8), ctx, {"image_slots": [8]}, 0)
                print(f"warm-up {w}x{h}: {secs}; {pc.vram_state()}", flush=True)

    def status(self):
        return {"ok": True, "pid": os.getpid(), "uptime_s": round(time.time() - self.started, 1), "plates": self.plates,
                "encoder": self.a.te and {"file": self.a.te, "device": self.a.te_device},
                "painter": self.a.dit and {"file": self.a.dit, "loras": self.painter.loras if self.painter else None,
                                           "kv_cache": self.a.kv_cache, "attention": self.a.attention,
                                           "fully_loaded": self.painter.fully_loaded() if self.painter else None},
                "vram": pc.vram_state()}

    def encode(self, req):
        if self.encoder is None:
            raise ValueError("no encoder loaded (--te)")
        paths = req.get("images") or ([req["image"]] if req.get("image") else [])
        imgs = [read_rgb(p) for p in paths]
        context, extra, secs = self.encoder.encode(req["prompt"], imgs or None)
        info = {"prompt": req["prompt"], "images": paths, "image_sizes": [im.shape[1::-1] for im in imgs],
                "te": self.a.te, "seconds": round(secs, 3)}
        pc.save_cond(req["out"], context, extra, info)
        return {"seconds": round(secs, 3), "context_tokens": int(context.shape[1]), "image_slots": extra.get("image_slots")}

    def paint(self, req):
        if self.painter is None:
            raise ValueError("no painter loaded (--dit)")
        canvas = read_rgb(req["canvas"])
        context, extra = pc.load_cond(req["cond"])
        sigmas = req.get("sigmas") or pc.SCHEDULES[req.get("schedule", "turbo8")]
        extra_refs = [read_rgb(p) for p in req.get("extra_refs") or []]
        sides = [(int(x0), int(x1)) for x0, x1 in req.get("sides") or []]
        plate, secs = self.painter.paint(canvas, context, extra, int(req.get("seed", 7)), sigmas,
                                         float(req.get("ref_scale", 1.0)), extra_refs, sides, req.get("sampler", "euler"))
        Image.fromarray(plate).save(req["out"])
        self.plates += 1
        return {"seconds": secs, "size": [int(canvas.shape[1]), int(canvas.shape[0])], "vram": pc.vram_state(),
                "fully_loaded": self.painter.fully_loaded()}


def make_handler(svc):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def _send(self, code, obj):
            body = json.dumps(obj).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (ConnectionAbortedError, BrokenPipeError):         # a client's readiness ping that timed out
                pass

        def do_GET(self):
            if self.path == "/status":
                return self._send(200, svc.status())
            self._send(404, {"error": f"unknown {self.path}"})

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(n) or b"{}")
            if self.path == "/quit":
                self._send(200, {"ok": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            fn = {"/encode": svc.encode, "/paint": svc.paint}.get(self.path)
            if fn is None:
                return self._send(404, {"error": f"unknown {self.path}"})
            try:
                with svc.lock:
                    out = fn(req)
                self._send(200, out)
            except Exception as e:
                traceback.print_exc()
                self._send(500, {"error": repr(e)[:2000]})
    return Handler


def watch_parent(pid, server):
    while psutil.pid_exists(pid):
        time.sleep(2.0)
    print(f"parent {pid} is gone: exiting", flush=True)
    os._exit(3)


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", type=int, default=8192)
    ap.add_argument("--log", default="", help="ComfyUI's log lines go here (model loads, LoRA warnings)")
    ap.add_argument("--parent-pid", type=int, default=0, help="exit when this process is gone")
    ap.add_argument("--te", default="", help="encoder file, e.g. qwen3vl_8b_int8_convrot.safetensors")
    ap.add_argument("--te-device", choices=("default", "cpu"), default="default")
    ap.add_argument("--dit", default="", help="DiT file, e.g. qwen_image_2.1_int8_convrot.safetensors or a merged one")
    ap.add_argument("--lora", action="append", default=[], help="FILE:STRENGTH, baked in at load (repeatable)")
    ap.add_argument("--vae", default="qwen_image_2.1_vae_bf16.safetensors")
    ap.add_argument("--kv-cache", choices=sorted(KV_CACHE), default="gpu-int8")
    ap.add_argument("--gpu-queue", choices=("off", "layer", "block"), default="off",
                    help="layer: wait for the GPU after each attention, MLP and VAE convolution (one in flight)")
    ap.add_argument("--attn-chunk", type=int, default=0,
                    help="attention in kernels of at most this many queries, one in flight (0: whole)")
    ap.add_argument("--gpu-priority", choices=("normal", "below", "idle"), default="normal",
                    help="this process's GPU scheduling class (D3DKMT), set before CUDA starts")
    ap.add_argument("--attention", choices=pc.ATTENTION, default="pytorch",
                    help="pytorch: SDPA; ck-int8: comfy_kitchen's INT8 attention (ComfyUI's --use-ck-attention)")
    ap.add_argument("--vae-tile", default="", help="TILE:OVERLAP in pixels: decode in one pass of tiles (default: whole)")
    ap.add_argument("--warmup", default="", help="WxH: paint one plate this size at start (reserves the pool)")
    ap.add_argument("--vram-cap-gb", type=float, default=0.0, help="torch allocator cap after the load (0: none)")
    ap.add_argument("--cpu-priority", choices=("below", "idle"), default="below", help="this process's CPU class")
    ap.add_argument("--cpu-threads", type=int, default=0, help="torch's CPU threads (0: torch's default, every core)")
    ap.add_argument("--cpu-affinity", default="", help="logical CPUs FIRST-LAST this process may run on (default: all)")
    a = ap.parse_args(argv)
    if not (a.te or a.dit):
        raise SystemExit("nothing to serve: pass --te and/or --dit")
    try:
        me = psutil.Process()
        me.nice({"below": psutil.BELOW_NORMAL_PRIORITY_CLASS, "idle": psutil.IDLE_PRIORITY_CLASS}[a.cpu_priority])
        if a.cpu_affinity:
            first, last = (int(v) for v in a.cpu_affinity.split("-"))
            me.cpu_affinity(list(range(first, last + 1)))
    except (psutil.Error, AttributeError):
        pass
    if a.cpu_threads:
        import torch                                              # painter_core binds torch in init_comfy; threads first
        torch.set_num_threads(a.cpu_threads)
    if a.gpu_priority != "normal":
        st = pc.lower_gpu_priority({"below": 1, "idle": 0}[a.gpu_priority])
        print(f"GPU scheduling class {a.gpu_priority}: status 0x{st:08x}", flush=True)
    pc.init_comfy(cpu_only=bool(a.te) and a.te_device == "cpu" and not a.dit, attention=a.attention)
    if a.log:
        pc.setup_logging(a.log)
    svc = Service(a)
    server = HTTPServer(("127.0.0.1", a.port), make_handler(svc))   # bind first: a second service fails here, not later
    svc.load()
    if a.parent_pid:
        threading.Thread(target=watch_parent, args=(a.parent_pid, server), daemon=True).start()
    print(f"painter service on 127.0.0.1:{a.port}, pid {os.getpid()}", flush=True)
    server.serve_forever()
    print("painter service stopped", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
