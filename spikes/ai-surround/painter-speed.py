"""Painter speed test (X2): do public few-step weights, low precision and a smaller canvas bring the Qwen-Image-2.1 +
outpaint-LoRA-v2 plate (~170-200 s today) to the live budget of ~2-4 s with the same look (style, seam, colour)?

One resident ComfyUI server paints the middle frame of every scene once per variant. Each plate is made, scored and laid
out exactly like comfy-plates.py (grey sides #808080, 4 grey rows top and bottom, the real centre pasted back, scored on
the hidden sides) and shown next to the 25-step plate (recipe qwen21-outpaint-v2).

Variants, recipe "qwen21-ov2-<name>". Shared: the DiT file qwen_image_2.1_bf16 loaded as fp8_e4m3fn_fast (fp8 weights,
fp8 matmul, activations clamped to +-448 in ComfyUI 0.39; --dit-dtype fp8_e4m3fn keeps fp8 weights with bf16 compute),
text encoder and VAE as today, the LoRA's instruction alone as the prompt (no caption pass), seed 7:
  fast25           today's graph, 25 steps (KSampler euler/simple, CFG 1), outpaint LoRA v2 at 1.0: precision + overhead
  turbo8-s10       + the Comfy-Org Turbo LoRA (extracted, avg rank 178) at 1.0 and the Turbo card's 8 sigmas through
  turbo8-s07         ManualSigmas + SamplerCustomAdvanced (euler, BasicGuider = CFG 1); outpaint LoRA at 1.0 / 0.7
  acc4             PAI Fun-Acc 4-step LoRA: not native in ComfyUI 0.39 (reason in SKIPPED), listed and skipped
  <name>-1mp       the same graph on a 1344x768 canvas (1.03 MP: picture scaled by 0.7, 6 grey rows top and bottom)
                   instead of 1920x1088 (2.09 MP); the plate is scaled back to 1920x1080 before the centre is pasted
  <name>-textonly  the VLM reads the prompt only (TextEncodeQwenImage21 without images: ComfyUI computes it once and keeps
                   it), the canvas reaches the DiT only as a VAE reference latent (ReferenceLatent; with no image slot in
                   the prompt the DiT puts it after the text), sampled from an empty latent of the canvas size
"best" in --variants stands for --best (the V2/V3 winner; default turbo8-s10).

Server: ComfyUI 0.39 started here on --port (default 8190; the script refuses a port something already answers on) with
--disable-dynamic-vram (issue #16551: dynamic VRAM applies LoRAs on the fly, and ComfyUI's fp8 matmul only runs on layers
without such patches) and, instead of comfy_client.LOW_RAM, the classic cache ("--cache-classic", --cache classic): each
prompt keeps the outputs of the previous one that it can reuse and drops the rest, so the loaded DiT, text encoder, VAE
and LoRA chain stay between the plates of a variant and only the LoRA chain is rebuilt between variants. ComfyUI's
default RAM-pressure cache is not used: under pressure it evicts entries still in use, i.e. reloads models mid-variant.
Trade-off: without dynamic VRAM, ComfyUI loads the legacy way (each file copied into RAM, then cast), so the resident set
is the fp8 DiT 7.1 GB + text encoder 17.5 GB (fp16) + VAE + LoRAs, about 26 GB of the 31.9 GB, and loading the encoder
adds its 17.5 GB file copy for a moment (ComfyUI runs the encoder before it loads the DiT, so that peak comes first):
Windows may page during V1-V4 (visible as slow plates and low ram_avail_gb).
A guard stops the server when the commit headroom (Windows commit limit minus charge) falls below --min-commit-gb.
--cache none is LOW_RAM (every plate reloads every model: the overhead V1 is meant to remove); --server-args adds flags
(written with "=" since they start with "--"), e.g. --server-args=--fp8_e4m3fn-text-enc (encoder stored in fp8, 8.8 GB:
no paging, at the cost of encoder precision) or --server-args=--enable-dynamic-vram (disk-backed loading, LoRAs patched
on the fly), best with --tag (recipe names get "-<tag>") so the recipes stay apart.

Rest (user rule 2026-10-13): --rest seconds before each variant (the first too, so a GPU warm from an earlier job cools)
and --rest-plate seconds between plates; GPU temperature, clocks and power are logged at each variant start and per plate.

Measured per plate: exec_s (server-side, from ComfyUI's execution_start/success timestamps), wall_s, sampler_s and s/it
(the sampler's progress bar), peak VRAM (NVML, device-wide, every 0.1 s, idle baseline logged), mean SM clock and power,
lowest free RAM and commit headroom, and from the server log: "lora key not loaded" warnings (must be 0), models loaded
to the GPU and whether the DiT went in completely or partially (partially = fp8 matmul off for the offloaded layers),
model builds from disk, VAE out-of-memory fallbacks. Warm = every plate but the first of a variant (the first carries
model loading and LoRA patching).

    python -I -X utf8 painter-speed.py [--variants fast25,turbo8-s10,turbo8-s07,acc4,best-1mp,best-textonly]
           [--best turbo8-s10] [--scenes a,b] [--rest 180] [--rest-plate 20] [--port 8190] [--cache classic|none]
           [--server-args="--flag ..."] [--min-commit-gb 4] [--dit-dtype fp8_e4m3fn_fast] [--seed 7] [--tag T]
           [--scenes-dir D] [--runs-dir D] [--dry-run]

--dry-run builds every graph and validates it offline without the GPU: writes the input canvases, runs every graph
through ComfyUI's own validator in a CPU-only process with the GPU hidden (comfy-validate-graphs.py: node classes,
inputs, links, model/LoRA/image names), checks the model and LoRA files, predicts from the file headers which LoRA keys
ComfyUI would not load, reads the GPU sensors once (NVML, no CUDA) and prints the plan. No server, no CUDA.

Writes RUNS/<recipe>/<scene>/{plate,raw}-s0.5.png, RUNS/scores-painter-speed.json, RUNS/sheets/<scene>-painter-speed-s0.5.png
(every painter-speed recipe measured so far next to the truth and the 25-step plate) and RUNS/painter-speed/
{timing,summary,comfy-server,gpu}-<stamp>.*; --dry-run writes RUNS/painter-speed/dry-run-{graphs,validation}.json.
"""
import argparse
import ctypes
import importlib.util
import json
import os
import re
import shlex
import shutil
import statistics
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid

import cv2
import numpy as np
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)                                          # python -I leaves the script's folder off sys.path
import comfy_client as cc  # noqa: E402
import hidden_sides as hs  # noqa: E402


def load_script(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), os.path.join(HERE, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


plates = load_script("comfy-plates")                              # INSTRUCTION, GREY, PAD of the painter recipe

SIDE = 0.5                                                        # 16:9 on 32:9, the share every 25-step plate used
REFERENCE = "qwen21-outpaint-v2"                                  # today's 25-step plates (comfy-plates.py)
DIT = "qwen_image_2.1_bf16.safetensors"
TE = "qwen3vl_8b_bf16.safetensors"
VAE = "qwen_image_2.1_vae_bf16.safetensors"
OUTPAINT = "qwen-image-2.1-outpaint-v2.safetensors"
TURBO = "qwen_image_2.1_turbo_lora_avg_rank_178_bf16.safetensors"  # Comfy-Org/Qwen-Image-2.1 loras/
TURBO_SIGMAS = "1.0, 0.978453, 0.95418, 0.926626, 0.89508, 0.845148, 0.704534, 0.414568, 0.0"   # Qwen-Image-2.1-Turbo card
BASES = {
    "fast25": {"loras": [(OUTPAINT, 1.0)], "steps": 25},
    "turbo8-s10": {"loras": [(TURBO, 1.0), (OUTPAINT, 1.0)], "sigmas": TURBO_SIGMAS},
    "turbo8-s07": {"loras": [(TURBO, 1.0), (OUTPAINT, 0.7)], "sigmas": TURBO_SIGMAS},
}
SKIPPED = {"acc4": (
    "PAI Qwen-Image-2.1-Fun-Acc-4Step is not native in ComfyUI 0.39: its 528 tensors are named '<module>.lora_down' / "
    "'.lora_up' with no '.weight' (none of ComfyUI's LoRA name formats, so every key would be 'lora key not loaded'), "
    "and proj_out.weight is a [4, 64, 4096] bank of per-step parallel-decoding heads that its PDD code "
    "(qwenimage21_pdd.py: PDDParallelHead, a step callback and its own scheduler) selects at each step; ComfyUI 0.39 "
    "has PDD heads only for MiniMax H3. Checked from the file header on Hugging Face 2026-10-13, file not downloaded.")}
MODS = ("1mp", "textonly")
CANVAS_SCALE = {False: 1.0, True: 0.7}                            # 1920x1088 (2.09 MP) or 1344x768 (1.03 MP)
VALIDATOR = os.path.join(HERE, "comfy-validate-graphs.py")
EMBEDDED_PY = os.path.join(cc.COMFY, "python_embeded", "python.exe")


# ---------------------------------------------------------------- variants and graphs

def parse_variant(name, best):
    """(name, base, set of mods) of a variant name such as turbo8-s10-1mp; 'best' stands for the --best base."""
    if name == "best" or name.startswith("best-"):
        name = best + name[len("best"):]
    base, mods = name, set()
    for _ in MODS:
        for m in MODS:
            if base.endswith("-" + m):
                base, mods = base[:-len(m) - 1], mods | {m}
    if base not in BASES and base not in SKIPPED:
        raise SystemExit(f"unknown variant {name!r}: bases {list(BASES) + list(SKIPPED)}, suffixes -1mp, -textonly")
    return name, base, mods


def painter_graph(base, mods, canvas_name, size, seed, prefix, dit_dtype="fp8_e4m3fn_fast"):
    """API graph of one plate; returns (graph, SaveImage node id, sampler steps)."""
    g = {}

    def add(class_type, **inputs):
        nid = str(len(g) + 1)
        g[nid] = {"class_type": class_type, "inputs": inputs}
        return nid

    model = [add("UNETLoader", unet_name=DIT, weight_dtype=dit_dtype), 0]
    for lora, strength in BASES[base]["loras"]:
        model = [add("LoraLoaderModelOnly", model=model, lora_name=lora, strength_model=strength), 0]
    clip = add("CLIPLoader", clip_name=TE, type="qwen_image")
    vae = add("VAELoader", vae_name=VAE)
    img = add("LoadImage", image=canvas_name)
    if "textonly" in mods:
        te = add("TextEncodeQwenImage21", clip=[clip, 0], prompt=plates.INSTRUCTION, negative_prompt="", resolution=0)
        ref = add("VAEEncode", pixels=[img, 0], vae=[vae, 0])
        positive = [add("ReferenceLatent", conditioning=[te, 0], latent=[ref, 0]), 0]
        negative = [te, 1]
        latent = [add("EmptyLatentImage", width=size[0], height=size[1], batch_size=1), 0]
    else:                                                         # the VLM reads the canvas, as in comfy-plates.py
        te = add("TextEncodeQwenImage21", clip=[clip, 0], prompt=plates.INSTRUCTION, negative_prompt="",
                 vae=[vae, 0], resolution=0, **{"images.image_1": [img, 0]})
        positive, negative, latent = [te, 0], [te, 1], [te, 2]
    if "sigmas" in BASES[base]:
        steps = len(BASES[base]["sigmas"].split(",")) - 1
        samples = [add("SamplerCustomAdvanced", noise=[add("RandomNoise", noise_seed=seed), 0],
                       guider=[add("BasicGuider", model=model, conditioning=positive), 0],
                       sampler=[add("KSamplerSelect", sampler_name="euler"), 0],
                       sigmas=[add("ManualSigmas", sigmas=BASES[base]["sigmas"]), 0], latent_image=latent), 0]
    else:
        steps = BASES[base]["steps"]
        samples = [add("KSampler", model=model, positive=positive, negative=negative, latent_image=latent, seed=seed,
                       steps=steps, cfg=1.0, sampler_name="euler", scheduler="simple", denoise=1.0), 0]
    decoded = add("VAEDecode", samples=samples, vae=[vae, 0])
    return g, add("SaveImage", images=[decoded, 0], filename_prefix=prefix), steps


def make_canvas(truth, side, scale):
    """Model canvas for one truth canvas: the visible centre (scaled) between grey sides, grey rows added top and bottom
    to a multiple of 32. Returns (canvas, pad rows, picture rows). scale 1.0 gives comfy-plates.py's canvas exactly."""
    h0, w0 = truth.shape[:2]
    w = int(round(w0 * scale / 32)) * 32
    ph = int(round(h0 * w / w0))
    height = -(-ph // 32) * 32
    pad = (height - ph) // 2
    side_s = int(round(side * w / w0))
    vis = truth[:, side:w0 - side]
    if w != w0:
        vis = cv2.resize(vis, (w - 2 * side_s, ph), interpolation=cv2.INTER_AREA)
    canvas = np.full((height, w, 3), plates.GREY, np.uint8)
    canvas[pad:pad + ph, side_s:w - side_s] = vis
    return canvas, pad, ph


# ---------------------------------------------------------------- measurement

class MemoryStatus(ctypes.Structure):
    _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def memory_status():
    """(physical RAM available, commit headroom = commit limit - commit charge, commit limit) in GB, from Windows."""
    m = MemoryStatus()
    m.dwLength = ctypes.sizeof(m)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
        return None, None, None
    return (round(m.ullAvailPhys / 1024 ** 3, 2), round(m.ullAvailPageFile / 1024 ** 3, 2),
            round(m.ullTotalPageFile / 1024 ** 3, 1))


class GpuMonitor:
    """GPU sensors polled in a thread through NVML (the driver's management library; no CUDA context), nvidia-smi as
    fallback: device-wide memory used, temperature, power, SM clock, utilisation; plus Windows RAM and commit headroom.
    `guard(headroom_gb)` is called once when the commit headroom falls below `min_commit_gb`."""
    FIELDS = ("t", "mem_mb", "temp_c", "power_w", "sm_mhz", "util", "ram_avail_gb", "commit_free_gb")

    def __init__(self, csv_path=None, period=0.1, min_commit_gb=None):
        self.samples, self.period, self.csv_path = [], period, csv_path
        self.read_gpu, self.source = self._nvml(), "nvml"
        if self.read_gpu is None:
            self.read_gpu, self.source = self._smi(), "nvidia-smi"
        self.min_commit_gb, self.guard, self.tripped = min_commit_gb, None, False
        self._stop = threading.Event()
        self._thread = None

    def read(self):
        s = self.read_gpu()
        return None if s is None else s + memory_status()[:2]

    @staticmethod
    def _nvml():
        try:
            nv = ctypes.WinDLL("nvml.dll")
            handle = ctypes.c_void_p()
            if nv.nvmlInit_v2() != 0 or nv.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(handle)) != 0:
                return None
        except (OSError, AttributeError):
            return None

        class Memory(ctypes.Structure):
            _fields_ = [("total", ctypes.c_ulonglong), ("free", ctypes.c_ulonglong), ("used", ctypes.c_ulonglong)]

        class Utilisation(ctypes.Structure):
            _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]

        mem, util, temp, power, clock = Memory(), Utilisation(), ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint()

        def read():
            nv.nvmlDeviceGetMemoryInfo(handle, ctypes.byref(mem))
            nv.nvmlDeviceGetTemperature(handle, 0, ctypes.byref(temp))            # NVML_TEMPERATURE_GPU
            nv.nvmlDeviceGetPowerUsage(handle, ctypes.byref(power))               # mW
            nv.nvmlDeviceGetClockInfo(handle, 1, ctypes.byref(clock))             # NVML_CLOCK_SM
            nv.nvmlDeviceGetUtilizationRates(handle, ctypes.byref(util))
            return (time.time(), mem.used >> 20, temp.value, round(power.value / 1000, 1), clock.value, util.gpu)
        return read

    @staticmethod
    def _smi():
        exe = shutil.which("nvidia-smi")

        def read():
            if exe is None:
                return None
            out = subprocess.run([exe, "--query-gpu=memory.used,temperature.gpu,power.draw,clocks.sm,utilization.gpu",
                                  "--format=csv,noheader,nounits"], capture_output=True, text=True).stdout
            try:
                mem, temp, power, sm, util = (float(x) for x in out.splitlines()[0].split(","))
            except (ValueError, IndexError):
                return None
            return (time.time(), int(mem), int(temp), power, int(sm), int(util))
        return read

    def start(self):
        def loop():
            out = open(self.csv_path, "w", encoding="utf-8") if self.csv_path else None
            if out:
                out.write(",".join(self.FIELDS) + "\n")
            while not self._stop.is_set():
                s = self.read()
                if s is not None:
                    self.samples.append(s)
                    if out:
                        out.write(",".join(str(x) for x in s) + "\n")
                    if (self.min_commit_gb and s[7] is not None and s[7] < self.min_commit_gb and self.guard
                            and not self.tripped):
                        self.tripped = True
                        self.guard(s[7])
                self._stop.wait(self.period)
            if out:
                out.close()
        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()
        time.sleep(0.3)

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def now(self):
        """The latest sample as a dict (the polling thread's, or one read here when it is not running)."""
        s = self.samples[-1] if self._thread is not None and self.samples else self.read()
        return dict(zip(self.FIELDS[1:], s[1:])) if s else {}

    def stats(self, t0, t1):
        w = [s for s in self.samples if t0 - 2 * self.period <= s[0] <= t1 + 2 * self.period]
        if not w:
            return {}
        ram = [s[6] for s in w if s[6] is not None]
        commit = [s[7] for s in w if s[7] is not None]
        return {"peak_vram_mb": max(s[1] for s in w), "temp_c_max": max(s[2] for s in w),
                "power_w_mean": round(float(np.mean([s[3] for s in w])), 1),
                "sm_mhz_mean": int(np.mean([s[4] for s in w])), "util_mean": int(np.mean([s[5] for s in w])),
                "ram_avail_gb_min": min(ram) if ram else None, "commit_free_gb_min": min(commit) if commit else None}


ANSI = re.compile(r"\x1b\[[0-9;]*m")
PROGRESS = re.compile(r"(\d+)/(\d+) \[(?:(\d+):)?(\d+):(\d+)<[^,\]]*,\s*([\d.]+)(s/it|it/s)")


def parse_server_log(text, steps):
    """What one prompt left in the server log: LoRA warnings, models loaded to the GPU (and how), builds from disk,
    VAE out-of-memory fallbacks, and the sampler's progress bar (elapsed s with 1 s resolution, s per step)."""
    text = ANSI.sub("", text)
    requested, loads = [], []
    for line in re.split(r"[\r\n]+", text):
        m = re.search(r"Requested to load (\S+)", line)
        if m:
            requested.append(m.group(1))
        m = re.search(r"(loaded completely|loaded partially|Unloaded partially)[;:]\s*(.*)", line)
        if m:
            what = requested.pop(0) if requested and m.group(1) != "Unloaded partially" else "?"
            loads.append(f"{what}: {m.group(1)}; {m.group(2)[:110]}")
    out = {"lora_warnings": text.count("lora key not loaded"), "gpu_loads": loads,
           "dit_load": next((x.split(": ")[1].split(";")[0] for x in reversed(loads) if x.startswith("QwenImage21:")), None),
           "model_builds": len(re.findall(r"CLIP/text encoder model load device|model weight dtype|VAE load device", text)),
           "vae_oom_fallbacks": text.count("retrying with tiled VAE"), "log_errors": len(re.findall(r"Traceback", text))}
    bars = [m for m in PROGRESS.finditer(text) if m.group(1) == m.group(2) == str(steps)]
    if bars:
        b = bars[-1]
        out["sampler_s"] = int(b.group(3) or 0) * 3600 + int(b.group(4)) * 60 + int(b.group(5))
        rate = float(b.group(6))
        out["s_per_it"] = round(rate if b.group(7) == "s/it" else 1.0 / rate, 3)
    return out


class LogTail:
    def __init__(self, path):
        self.path, self.pos = path, 0

    def read_new(self):
        if not os.path.isfile(self.path):
            return ""
        with open(self.path, encoding="utf-8", errors="replace") as f:
            f.seek(self.pos)
            text = f.read()
            self.pos = f.tell()
        return text


def run_plate(port, graph, timeout_s=None):
    """Queue one graph and wait for it (history polled every 50 ms); returns (outputs, status messages by event). With
    timeout_s, a plate still running after that long is interrupted (POST /interrupt) and TimeoutError raised."""
    deadline = time.monotonic() + timeout_s if timeout_s else None
    body = json.dumps({"prompt": graph, "client_id": uuid.uuid4().hex}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}/prompt", data=body, headers={"Content-Type": "application/json"})
    try:
        pid = json.load(urllib.request.urlopen(req))["prompt_id"]
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"ComfyUI refused the workflow: {e.read().decode(errors='replace')[:3000]}")
    while True:
        hist = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/history/{pid}"))
        if pid in hist:
            status = hist[pid].get("status", {})
            if status.get("status_str") == "error":
                raise RuntimeError(f"workflow failed: {json.dumps(status.get('messages', []))[:3000]}")
            if status.get("completed"):
                return hist[pid]["outputs"], {m[0]: m[1] for m in status.get("messages", [])}
        if deadline is not None and time.monotonic() > deadline:
            urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}/interrupt", data=b"", method="POST")).read()
            raise TimeoutError(f"the plate did not finish within {timeout_s:.0f} s (interrupted)")
        time.sleep(0.05)


def rest(seconds, why, gpu, log):
    if seconds <= 0:
        return
    before = gpu.now()
    print(f"rest {seconds:g} s {why} (GPU {before.get('temp_c', '?')} C)", flush=True)
    time.sleep(seconds)
    log({"event": "rest", "why": why, "seconds": seconds, "gpu_before": before, "gpu_after": gpu.now()})


# ---------------------------------------------------------------- checks used by --dry-run

def model_dirs():
    """Folders per model kind: ComfyUI's own models/<kind> plus the ones extra_model_paths.yaml adds."""
    cfg = yaml.safe_load(open(os.path.join(cc.COMFY_APP, "extra_model_paths.yaml"), encoding="utf-8")) or {}
    dirs = {k: [os.path.join(cc.COMFY_APP, "models", k)] for k in ("diffusion_models", "text_encoders", "vae", "loras")}
    for entry in cfg.values():
        for kind, subs in entry.items():
            if kind != "base_path":
                dirs.setdefault(kind, []).extend(os.path.join(entry.get("base_path", ""), s.strip())
                                                 for s in str(subs).splitlines() if s.strip())
    return dirs


def find_model(dirs, kind, name):
    return next((os.path.join(d, name) for d in dirs.get(kind, []) if os.path.isfile(os.path.join(d, name))), None)


def safetensors_keys(path):
    with open(path, "rb") as f:
        n = struct.unpack("<Q", f.read(8))[0]
        return [k for k in json.loads(f.read(n)) if k != "__metadata__"]


LORA_NAMES = ((".lora_up.weight", ".lora_down.weight", ".lora_mid.weight"), ("_lora.up.weight", "_lora.down.weight"),
              (".lora_B.weight", ".lora_A.weight"), (".lora.up.weight", ".lora.down.weight"), (".lora_B", ".lora_A"),
              (".lora_linear_layer.up.weight", ".lora_linear_layer.down.weight"),
              (".lora_B.default.weight", ".lora_A.default.weight"))
LORA_EXTRA = (".alpha", ".dora_scale", ".reshape_weight", ".w_norm", ".b_norm", ".diff", ".diff_b", ".set_weight")


def unloaded_lora_keys(lora_keys, dit_keys):
    """LoRA keys ComfyUI 0.39 would log as 'lora key not loaded' on the Qwen-Image-2.1 DiT, predicted from names only:
    the targets of comfy/lora.py model_lora_keys_unet (generic and QwenImage formats; gate_up whole or as its
    gate_layer / proj halves) tried with the names comfy.lora.load_lora and LoRAAdapter.load accept (LoRA, diff, set)."""
    targets = set()
    for k in dit_keys:
        if not k.endswith(".weight"):
            targets.add("diffusion_model." + k)
            continue
        stem = k[:-len(".weight")]
        targets |= {"diffusion_model." + stem, "lora_unet_" + stem.replace(".", "_")}
        halves = [stem.replace(".gate_up", ".gate_layer"), stem.replace(".gate_up", ".proj")]
        for t in (halves if stem.endswith(".img_mlp.gate_up") else [stem]):
            targets |= {t, "transformer." + t, "lycoris_" + t.replace(".", "_")}
    keys, loaded = set(lora_keys), set()
    for x in targets:
        for names in LORA_NAMES:
            if x + names[0] in keys:
                loaded |= {x + n for n in names + (".reshape_weight",)} & keys
                break
        loaded |= {x + s for s in LORA_EXTRA} & keys
    return sorted(keys - loaded)


def dry_run(a, recipes, scenes, graphs, out_dir):
    ok = True
    print(f"\n== plan: {len(recipes)} variants x {len(scenes)} scenes = {len(recipes) * len(scenes)} plates on port {a.port}")
    for recipe, (_, base, mods) in recipes.items():
        print(f"  {recipe:34s} base {base}, mods {sorted(mods) or '-'}")
    print(f"  scenes: {', '.join(scenes)}")
    print(f"  server: main.py --listen 127.0.0.1 --port {a.port} {' '.join(server_args(a))}")
    rest_total = len(recipes) * a.rest + max(0, len(scenes) - 1) * len(recipes) * a.rest_plate
    print(f"  rests: {a.rest:.0f} s before each variant, {a.rest_plate:.0f} s between plates = {rest_total / 60:.1f} min")

    print("\n== model and LoRA files (folders from extra_model_paths.yaml)")
    dirs = model_dirs()
    files = {"diffusion_models": [DIT], "text_encoders": [TE], "vae": [VAE],
             "loras": sorted({lo for _, base, _ in recipes.values() for lo, _ in BASES[base]["loras"]})}
    found = {}
    for kind, names in files.items():
        for name in names:
            found[name] = find_model(dirs, kind, name)
            size = f"{os.path.getsize(found[name]) / 1e9:.2f} GB" if found[name] else "MISSING"
            ok &= found[name] is not None
            print(f"  {kind:17s} {name:58s} {size}")
    if found.get(DIT):
        dit_keys = safetensors_keys(found[DIT])
        for name in files["loras"]:
            if found.get(name):
                miss = unloaded_lora_keys(safetensors_keys(found[name]), dit_keys)
                ok &= not miss
                print(f"  predicted 'lora key not loaded' for {name}: {len(miss)} {miss[:3]}")
    if all(found.values()):
        gb = {n: os.path.getsize(p) / 1e9 for n, p in found.items()}
        dit = gb[DIT] / (2 if a.dit_dtype.startswith("fp8") else 1)
        te = gb[TE] * (0.5 if re.search(r"--fp8_e\w+-text-enc", a.server_args) else 1.0)
        held = dit + te + gb[VAE] + sum(gb[n] for n in files["loras"])
        print(f"  RAM held by a resident server (legacy loading): DiT {dit:.1f} + text encoder {te:.1f} + VAE and LoRAs "
              f"{held - dit - te:.1f} = {held:.1f} GB; loading the encoder briefly adds its {gb[TE]:.1f} GB file copy")

    print("\n== scenes")
    for sc in scenes:
        ref = os.path.join(a.runs_dir, REFERENCE, sc, f"plate-s{SIDE}.png")
        print(f"  {sc:22s} 25-step plate {'found' if os.path.isfile(ref) else 'MISSING (sheet without it)'}")

    print("\n== graphs")
    for name, graph in graphs.items():                            # links name existing nodes (the validator checks types)
        for nid, node in graph.items():
            for key, val in node["inputs"].items():
                if isinstance(val, list) and (len(val) != 2 or str(val[0]) not in graph):
                    ok = False
                    print(f"  {name}: node {nid} input {key} -> {val} does not resolve")
    graphs_path = os.path.join(out_dir, "dry-run-graphs.json")
    report_path = os.path.join(out_dir, "dry-run-validation.json")
    hs.save_json(graphs, graphs_path)
    print(f"  {len(graphs)} graphs written to {graphs_path}; ComfyUI's validator in a CPU-only process, GPU hidden:")
    res = subprocess.run([EMBEDDED_PY, "-s", VALIDATOR, graphs_path, report_path], capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
    print("  " + (res.stdout.strip().splitlines() or ["(no output)"])[-1])
    if res.returncode != 0:
        ok = False
        report = json.load(open(report_path, encoding="utf-8")) if os.path.isfile(report_path) else {}
        for name, r in report.items():
            for e in r["errors"][:3]:
                print(f"    {name}: {e}")
        if not report:
            print(res.stderr[-3000:])
    else:
        node_types = sorted({n["class_type"] for g in graphs.values() for n in g.values()})
        print(f"  node classes used: {', '.join(node_types)}")

    print("\n== GPU sensors, RAM, port, disk")
    gpu = GpuMonitor()
    snap = gpu.now()
    print(f"  {gpu.source} read once (no CUDA): {snap or 'NOT AVAILABLE (no temperature or VRAM logging)'}")
    avail, commit_free, commit_limit = memory_status()
    print(f"  RAM available now {avail} GB; commit headroom {commit_free} of {commit_limit} GB (guard stops the server "
          f"below {a.min_commit_gb} GB)")
    print(f"  port {a.port}: {'BUSY (the run would refuse it)' if cc.ping(a.port) else 'free'}")
    print(f"  C: free {shutil.disk_usage(a.runs_dir).free / 1e9:.1f} GB")
    print(f"\ndry run {'OK' if ok else 'FAILED'}")
    return 0 if ok else 1


# ---------------------------------------------------------------- run

def server_args(a):
    cache = ["--cache-classic"] if a.cache == "classic" else list(cc.LOW_RAM)
    return ["--disable-dynamic-vram", *cache, *shlex.split(a.server_args)]


def summarize(records, ref_scores):
    out = {}
    for recipe in dict.fromkeys(r["recipe"] for r in records):
        rs = [r for r in records if r["recipe"] == recipe]
        warm = [r for r in rs if not r["first_call"]] or rs

        def stat(key, rows=warm, fn=statistics.median):
            vals = [r[key] for r in rows if r.get(key) is not None]
            return round(float(fn(vals)), 2) if vals else None
        out[recipe] = {
            "plates": len(rs), "warm_plates": len([r for r in rs if not r["first_call"]]),
            "warm_exec_s_median": stat("exec_s"), "warm_exec_s_min": stat("exec_s", fn=min),
            "warm_exec_s_max": stat("exec_s", fn=max), "first_exec_s": next((r["exec_s"] for r in rs if r["first_call"]), None),
            "warm_sampler_s_median": stat("sampler_s"), "s_per_it_median": stat("s_per_it"),
            "peak_vram_mb_max": stat("peak_vram_mb", rows=rs, fn=max), "sm_mhz_mean": stat("sm_mhz_mean", fn=statistics.mean),
            "lora_warnings": sum(r.get("lora_warnings", 0) for r in rs),
            "dit_partial_loads": sum(r.get("dit_load") == "loaded partially" for r in rs),
            "warm_model_builds": sum(r.get("model_builds", 0) for r in rs if not r["first_call"]),
            "ram_avail_gb_min": stat("ram_avail_gb_min", rows=rs, fn=min),
            "commit_free_gb_min": stat("commit_free_gb_min", rows=rs, fn=min),
            "near_mean": stat("err_near", rows=rs, fn=statistics.mean), "far_mean": stat("err_far", rows=rs, fn=statistics.mean),
            "seam_mean": stat("seam_step", rows=rs, fn=statistics.mean)}
        refs = [ref_scores[r["scene"]] for r in rs if r["scene"] in ref_scores]
        for k in ("err_near", "err_far", "seam_step"):
            out[recipe][f"ref25_{k}_mean"] = round(float(np.mean([x[k] for x in refs])), 2) if refs else None
    return out


def write_sheets(scenes, prepared, scores, ref_scores, runs_dir):
    """Per scene: truth, the 25-step plate and every painter-speed recipe measured so far, two per row."""
    for sc in scenes:
        truth, _, side = prepared[sc][:3]
        tiles, labels = [hs.mark_seams(truth, side)], [f"{sc}  s={SIDE}  TRUTH (hidden sides)"]
        candidates = [(REFERENCE, ref_scores.get(sc), "25 steps bf16 + caption")] + [
            (recipe, by.get(sc, {}).get(f"s{SIDE}"), None) for recipe, by in scores.items()]
        for recipe, s, note in candidates:
            path = os.path.join(runs_dir, recipe, sc, f"plate-s{SIDE}.png")
            if not s or not os.path.isfile(path):
                continue
            secs = f"{s['seconds']} s" if note else f"{s['exec_s']} s{' (first: load)' if s['first_call'] else ''}"
            tiles.append(hs.mark_seams(cv2.cvtColor(cv2.imread(path), cv2.COLOR_BGR2RGB), side))
            labels.append(f"{recipe}  {note + '  ' if note else ''}{secs}  near {s['err_near']}  far {s['err_far']}  "
                          f"seam x{s['seam_step']}")
        cells = []
        for img, label in zip(tiles, labels):
            im = cv2.resize(img, (960, int(round(img.shape[0] * 960 / img.shape[1]))), interpolation=cv2.INTER_AREA)
            strip = np.zeros((22, 960, 3), np.uint8)
            cv2.putText(strip, label, (6, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1, cv2.LINE_AA)
            cells.append(np.vstack([strip, im]))
        if len(cells) % 2:
            cells.append(np.zeros_like(cells[0]))
        gap = np.zeros((cells[0].shape[0], 4, 3), np.uint8)
        sheet = np.vstack([np.hstack([cells[i], gap, cells[i + 1]]) for i in range(0, len(cells), 2)])
        os.makedirs(os.path.join(runs_dir, "sheets"), exist_ok=True)
        cv2.imwrite(os.path.join(runs_dir, "sheets", f"{sc}-painter-speed-s{SIDE}.png"), cv2.cvtColor(sheet, cv2.COLOR_RGB2BGR))


def main(argv):
    global DIT, TE                                                # set from --dit-file / --te-file below
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--variants", default="fast25,turbo8-s10,turbo8-s07,acc4,best-1mp,best-textonly")
    ap.add_argument("--best", default="turbo8-s10", help="the base 'best' stands for (the V2/V3 winner)")
    ap.add_argument("--scenes", default="", help="comma-separated subset (default: every scene)")
    ap.add_argument("--rest", type=float, default=180.0, help="seconds of rest before each variant")
    ap.add_argument("--rest-plate", type=float, default=20.0, help="seconds of rest between plates of a variant")
    ap.add_argument("--port", type=int, default=8190)
    ap.add_argument("--cache", choices=("classic", "none"), default="classic",
                    help="classic: resident (models stay loaded); none: comfy_client.LOW_RAM, every plate reloads")
    ap.add_argument("--server-args", default="", help="extra ComfyUI flags, with '=': --server-args=--fp8_e4m3fn-text-enc")
    ap.add_argument("--min-commit-gb", type=float, default=4.0,
                    help="stop the server when Windows' commit headroom falls below this (0: no guard)")
    ap.add_argument("--dit-dtype", choices=("fp8_e4m3fn_fast", "fp8_e4m3fn", "default"), default="fp8_e4m3fn_fast",
                    help="UNETLoader weight_dtype: fp8_e4m3fn = fp8 weights with bf16 compute (no activation clamp)")
    ap.add_argument("--dit-file", default=DIT, help="diffusion model file, e.g. the official qwen_image_2.1_int8_convrot "
                    "repack (pass --dit-dtype default with it: the file's own INT8 weights)")
    ap.add_argument("--te-file", default=TE, help="text/vision encoder file, e.g. qwen3vl_8b_int8_convrot.safetensors")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--tag", default="", help="recipe names get '-<tag>' (keeps a rerun with other flags apart)")
    ap.add_argument("--scenes-dir", default=os.path.join(HERE, "out", "scenes"))
    ap.add_argument("--runs-dir", default=os.path.join(HERE, "out", "runs"))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    DIT, TE = a.dit_file, a.te_file

    recipes = {}
    for v in a.variants.split(","):
        name, base, mods = parse_variant(v.strip(), a.best)
        if base in SKIPPED:
            print(f"skip {name}: {SKIPPED[base]}")
            continue
        recipes[f"qwen21-ov2-{name}" + (f"-{a.tag}" if a.tag else "")] = (name, base, mods)
    scenes = [d for d in sorted(os.listdir(a.scenes_dir)) if os.path.isfile(os.path.join(a.scenes_dir, d, "clip.mkv"))]
    if a.scenes:
        wanted = a.scenes.split(",")
        missing = [s for s in wanted if s not in scenes]
        if missing:
            raise SystemExit(f"unknown scenes {missing}")
        scenes = [s for s in scenes if s in wanted]
    out_dir = os.path.join(a.runs_dir, "painter-speed")
    os.makedirs(out_dir, exist_ok=True)

    # Inputs: each scene's middle frame (key.png, the frame comfy-plates.py draws from) split like the hidden-sides
    # test, one canvas per size in use, all written before the server starts so ComfyUI's file list has them.
    scales = sorted({CANVAS_SCALE["1mp" in mods] for _, _, mods in recipes.values()})
    prepared = {}
    for sc in scenes:
        key = os.path.join(a.scenes_dir, sc, "key.png")
        if os.path.isfile(key):
            frame = cv2.cvtColor(cv2.imread(key), cv2.COLOR_BGR2RGB)
        else:
            frames = hs.read_frames(os.path.join(a.scenes_dir, sc, "clip.mkv"))
            frame = frames[len(frames) // 2]
        truth, vis, side = hs.split(frame, SIDE)
        canvases = {}
        for scale in scales:
            canvas, pad, ph = make_canvas(truth, side, scale)
            name = f"painter-speed-{sc}-{canvas.shape[1]}x{canvas.shape[0]}.png"
            cv2.imwrite(os.path.join(cc.INPUT_DIR, name), cv2.cvtColor(canvas, cv2.COLOR_RGB2BGR))
            canvases[scale] = (name, pad, ph, (canvas.shape[1], canvas.shape[0]))
        prepared[sc] = (truth, vis, side, canvases)
    graphs, plan = {}, []
    for recipe, (name, base, mods) in recipes.items():
        for sc in scenes:
            cname, pad, ph, size = prepared[sc][3][CANVAS_SCALE["1mp" in mods]]
            graph, save_node, steps = painter_graph(base, mods, cname, size, a.seed, f"painter-speed/{recipe}-{sc}",
                                                    a.dit_dtype)
            graphs[f"{recipe}/{sc}"] = graph
            plan.append((recipe, sc, graph, save_node, steps, pad, ph, size))
    if a.dry_run:
        return dry_run(a, recipes, scenes, graphs, out_dir)

    if cc.ping(a.port):
        raise SystemExit(f"something already answers on port {a.port}: this test needs its own server (its flags); "
                         f"stop that server or pass another --port (never 8189, the other session's)")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    timing_path = os.path.join(out_dir, f"timing-{stamp}.jsonl")
    scores_path = os.path.join(a.runs_dir, "scores-painter-speed.json")
    scores = json.load(open(scores_path, encoding="utf-8")) if os.path.isfile(scores_path) else {}
    klein = os.path.join(a.runs_dir, "scores-klein.json")
    ref_scores = {sc: v[f"s{SIDE}"] for sc, v in (json.load(open(klein, encoding="utf-8")).get(REFERENCE, {})
                                                  if os.path.isfile(klein) else {}).items() if f"s{SIDE}" in v}

    def log(event):
        with open(timing_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(dict(event, time=time.strftime("%H:%M:%S")), ensure_ascii=False) + "\n")

    gpu = GpuMonitor(os.path.join(out_dir, f"gpu-{stamp}.csv"), min_commit_gb=a.min_commit_gb)
    gpu.start()
    server_log = os.path.join(out_dir, f"comfy-server-{stamp}.log")
    log({"event": "start", "args": vars(a), "server_args": server_args(a), "gpu_idle": gpu.now(), "gpu_source": gpu.source,
         "recipes": list(recipes), "scenes": scenes})
    records = []
    server = cc.start_server(a.port, server_log, server_args(a))
    tail = LogTail(server_log)

    def guard(headroom):
        print(f"\nRAM GUARD: commit headroom {headroom} GB < {a.min_commit_gb} GB, stopping the ComfyUI server", flush=True)
        log({"event": "ram_guard", "commit_free_gb": headroom})
        server.terminate()
    gpu.guard = guard
    try:
        tail.read_new()                                           # start-up lines are not a plate's
        failed = set()
        for recipe, sc, graph, save_node, steps, pad, ph, size in plan:
            if recipe in failed:
                continue
            first = not any(r["recipe"] == recipe for r in records)
            if first:
                rest(a.rest, f"before {recipe}", gpu, log)
                snap = gpu.now()
                print(f"\n== {recipe}: GPU {snap.get('temp_c', '?')} C, {snap.get('sm_mhz', '?')} MHz, "
                      f"{snap.get('mem_mb', '?')} MB used", flush=True)
                log({"event": "variant_start", "recipe": recipe, "gpu": snap})
            else:
                rest(a.rest_plate, "between plates", gpu, log)
            truth, vis, side, _ = prepared[sc]
            gpu_start = gpu.now()
            t0 = time.time()
            try:
                outputs, msgs = run_plate(a.port, graph)
            except RuntimeError as e:                             # one broken variant must not cost the others
                print(f"{recipe} {sc} FAILED: {e}", flush=True)
                log({"event": "error", "recipe": recipe, "scene": sc, "error": str(e)[:3000]})
                failed.add(recipe)
                continue
            except OSError as e:                                  # the server is gone (the RAM guard, or a crash)
                print(f"{recipe} {sc}: lost the ComfyUI server ({e}){' after the RAM guard' if gpu.tripped else ''}; "
                      f"see {server_log}", flush=True)
                log({"event": "server_lost", "recipe": recipe, "scene": sc, "error": str(e), "ram_guard": gpu.tripped})
                break
            t1 = time.time()
            time.sleep(0.3)                                       # the server's last log lines of this prompt
            rec = {"recipe": recipe, "scene": sc, "first_call": first, "steps": steps, "canvas": f"{size[0]}x{size[1]}",
                   "wall_s": round(t1 - t0, 2), "gpu_start": gpu_start}
            rec["exec_s"] = round((msgs["execution_success"]["timestamp"] - msgs["execution_start"]["timestamp"]) / 1000, 2) \
                if "execution_success" in msgs and "execution_start" in msgs else rec["wall_s"]
            rec["cached_nodes"] = sorted(graph[n]["class_type"] for n in msgs.get("execution_cached", {}).get("nodes", []) if n in graph)
            rec.update(parse_server_log(tail.read_new(), steps))
            rec.update(gpu.stats(t0, t1))
            raw = cc.saved_images(outputs, save_node)[0][pad:pad + ph]
            big = cv2.resize(raw, (truth.shape[1], truth.shape[0]), interpolation=cv2.INTER_LANCZOS4)
            big[:, side:side + vis.shape[1]] = vis                # the real picture is never the model's
            plate_scores = hs.score([truth], [big], side)
            rec.update(plate_scores)
            odir = os.path.join(a.runs_dir, recipe, sc)
            os.makedirs(odir, exist_ok=True)
            cv2.imwrite(os.path.join(odir, f"plate-s{SIDE}.png"), cv2.cvtColor(big, cv2.COLOR_RGB2BGR))
            cv2.imwrite(os.path.join(odir, f"raw-s{SIDE}.png"), cv2.cvtColor(raw, cv2.COLOR_RGB2BGR))
            scores.setdefault(recipe, {}).setdefault(sc, {})[f"s{SIDE}"] = dict(
                plate_scores, seconds=rec["exec_s"], exec_s=rec["exec_s"], first_call=first, seed=a.seed,
                canvas=rec["canvas"], peak_vram_mb=rec.get("peak_vram_mb"), lora_warnings=rec["lora_warnings"],
                runner=f"comfyui-0.39.0 {' '.join(server_args(a))}")
            hs.save_json(scores, scores_path)
            records.append(rec)
            log(dict(rec, event="plate"))
            print(f"{recipe:34s} {sc:22s} exec {rec['exec_s']:6.1f} s  sampler {rec.get('sampler_s', '?')} s "
                  f"({rec.get('s_per_it', '?')} s/it)  VRAM {rec.get('peak_vram_mb', '?')} MB  {rec.get('temp_c_max', '?')} C  "
                  f"RAM {rec.get('ram_avail_gb_min', '?')} GB free, commit {rec.get('commit_free_gb_min', '?')} GB  "
                  f"DiT {rec['dit_load'] or 'resident'}  builds {rec['model_builds']}  lora-warn {rec['lora_warnings']}  "
                  f"near {plate_scores['err_near']} far {plate_scores['err_far']} seam x{plate_scores['seam_step']}", flush=True)
    finally:
        cc.stop_server(server)
        gpu.stop()

    summary = summarize(records, ref_scores)
    hs.save_json({"stamp": stamp, "server_args": server_args(a), "summary": summary}, os.path.join(out_dir, f"summary-{stamp}.json"))
    write_sheets(scenes, prepared, scores, ref_scores, a.runs_dir)
    print(f"\n{'recipe':34s} {'warm s':>7s} {'first s':>8s} {'s/it':>6s} {'VRAM MB':>8s} {'lora':>5s} {'part':>5s} "
          f"{'near':>6s} {'far':>6s} {'seam':>5s}   (25-step near/far/seam)")
    for recipe, s in summary.items():
        print(f"{recipe:34s} {s['warm_exec_s_median'] or 0:7.1f} {s['first_exec_s'] or 0:8.1f} {s['s_per_it_median'] or 0:6.2f} "
              f"{s['peak_vram_mb_max'] or 0:8.0f} {s['lora_warnings']:5d} {s['dit_partial_loads']:5d} {s['near_mean'] or 0:6.1f} "
              f"{s['far_mean'] or 0:6.1f} {s['seam_mean'] or 0:5.2f}   ({s['ref25_err_near_mean']} / {s['ref25_err_far_mean']} / "
              f"{s['ref25_seam_step_mean']})")
    print(f"\ntiming log {timing_path}\nsheets {os.path.join(a.runs_dir, 'sheets')}/<scene>-painter-speed-s{SIDE}.png")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
