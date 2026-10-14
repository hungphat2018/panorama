"""Paint role, GPU side: Qwen-Image-2.1 + the outpaint LoRA run in-process with ComfyUI 0.39's own model code as a library
(no server, no node cache, no dynamic VRAM). Imported only by painter-service.py, which runs in ComfyUI's embedded Python
(torch 2.14 cu130, comfy_kitchen); the worker and the bench talk to that service over HTTP (live/painter.py).

Why in-process (phase 3, plans/261013-0718-ai-surround-live-path/phase-03-painter-at-speed.md): the painter must share
the GPU with the player without moving weights while the film plays. ComfyUI's server swapped the 8B encoder and the DiT
on every plate and, with a VRAM cap, streamed DiT weights at every step: 23-40 wrong frames a minute in the player (E1,
phase 2). Here the models are loaded once, completely, and stay put; memory is reserved by a warm-up plate at the largest
canvas, then reused.

Numerics are ComfyUI's, as in X2 (plans/reports/spike-261013-1855-x2-painter-speed.md): TextEncodeQwenImage21's
conditioning (the 8B Qwen3-VL's last layer, without its final norm, for the prompt's tokens after the image; the image
tokens themselves are dropped and their slot gets the reference latents), the reference latents from the VAE, an empty
latent of the canvas size, euler over the Turbo card's 8 sigmas with CFG 1 (BasicGuider), the text + reference K/V
computed once per plate (the model's prefix cache). One change: the encoder may read another image than the one the DiT
gets as reference (a thumbnail, or a fixed proxy whose conditioning is computed once), so the encoder can leave the live
path. Units: images are uint8 RGB arrays (H, W, 3); sizes are multiples of 32.
"""
from __future__ import annotations

import logging
import os
import sys
import time

import numpy as np
from PIL import Image

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
COMFY_APP = os.path.join(REPO, "tools", "ComfyUI_windows_portable", "ComfyUI")
MODELS = os.path.join(REPO, "tools", "models", "comfy", "qwen-image-2.1")
# Qwen-Image-2.1-Turbo card (2026-10-09): 8 steps, CFG 1, euler; the same list as painter-speed.py (X2)
TURBO_SIGMAS = (1.0, 0.978453, 0.95418, 0.926626, 0.89508, 0.845148, 0.704534, 0.414568, 0.0)
# fewer steps without training: the Turbo schedule sub-sampled (its first steps sit close together at high noise)
SCHEDULES = {"turbo8": TURBO_SIGMAS,
             "turbo6": (1.0, 0.95418, 0.89508, 0.845148, 0.704534, 0.414568, 0.0),
             "turbo5": (1.0, 0.95418, 0.845148, 0.704534, 0.414568, 0.0),
             "turbo4": (1.0, 0.89508, 0.704534, 0.414568, 0.0),           # the reference latents set the layout
             # the card's 8 steps make only 3 moves below 0.85 (the last one 0.41 -> 0): three more low-noise steps where
             # small details and hands are drawn (2026-10-14: the few-step artifacts left with the caption)
             "turbo8lo": (1.0, 0.978453, 0.95418, 0.926626, 0.89508, 0.845148, 0.704534, 0.55, 0.414568, 0.25, 0.12, 0.0)}

torch = comfy = mm = None                                          # bound by init_comfy()


ATTENTION = ("pytorch", "ck-int8")


def init_comfy(cpu_only: bool = False, attention: str = "pytorch"):
    """Import ComfyUI as a library with its default arguments (classic memory management: no dynamic VRAM, so models
    stay where they are loaded). Call once before anything else in this module. attention: "pytorch" (SDPA, ComfyUI's
    default here) or "ck-int8" (comfy_kitchen's INT8 attention, ComfyUI's --use-ck-attention)."""
    global torch, comfy, mm
    if torch is not None:
        return
    if attention not in ATTENTION:
        raise ValueError(f"attention {attention!r}: one of {ATTENTION}")
    if cpu_only:
        os.environ["CUDA_VISIBLE_DEVICES"] = ""
    sys.path.insert(0, COMFY_APP)
    import torch as _torch
    import comfy.options                                           # binds the module-level name (global above)
    comfy.options.enable_args_parsing(False)
    import comfy.cli_args
    if cpu_only:
        comfy.cli_args.args.cpu = True
    comfy.cli_args.args.use_ck_attention = attention == "ck-int8"   # read when the attention module is first imported
    import comfy.model_management
    import comfy.sample
    import comfy.samplers
    import comfy.sd
    import comfy.utils
    torch, mm = _torch, comfy.model_management
    comfy.utils.PROGRESS_BAR_ENABLED = False


def model_path(kind: str, name: str) -> str:
    """Absolute path of a model file: as given if it exists, else under tools/models/comfy/qwen-image-2.1/<kind>/."""
    return name if os.path.isfile(name) else os.path.join(MODELS, kind, name)


def to_tensor(img: np.ndarray):
    """uint8 (H, W, 3) -> ComfyUI IMAGE (1, H, W, 3) float in 0..1."""
    return torch.from_numpy(np.array(img, copy=True)).float().div_(255.0).unsqueeze(0)


def round32(img: np.ndarray) -> np.ndarray:
    """The image at its own size rounded to multiples of 32 (TextEncodeQwenImage21 with resolution 0)."""
    h, w = img.shape[:2]
    h32, w32 = max(32, round(h / 32) * 32), max(32, round(w / 32) * 32)
    if (h32, w32) == (h, w):
        return img
    return np.asarray(Image.fromarray(img).resize((w32, h32), Image.LANCZOS))


def sync():
    if torch.cuda.is_available():
        torch.cuda.synchronize()


class Encoder:
    """The 8B Qwen3-VL text/vision encoder. Conditioning = (context, extra): extra holds image_slots (where the DiT
    splices the reference latents) and pooled_output; the reference latents are added per plate by Painter."""

    def __init__(self, te_file: str, device: str = "default"):
        self.path = model_path("text_encoders", te_file)
        opts = {}
        if device == "cpu":
            opts["load_device"] = opts["offload_device"] = torch.device("cpu")
        self.clip = comfy.sd.load_clip(ckpt_paths=[self.path], clip_type=comfy.sd.CLIPType.QWEN_IMAGE, model_options=opts)

    def encode(self, prompt: str, image) -> tuple:
        """(context on the CPU, extra, seconds). image: one image, a list (one image slot each: the canvas, then
        identity references), or None (the prompt alone, with no image slot)."""
        sync()
        t0 = time.perf_counter()
        imgs = [] if image is None else (list(image) if isinstance(image, (list, tuple)) else [image])
        with torch.inference_mode():                               # as ComfyUI's executor runs every node
            images = [to_tensor(round32(im)) for im in imgs]
            tokens = self.clip.tokenize(prompt, images=images, keep_vision=False, prevent_empty_text=True)
            cond = self.clip.encode_from_tokens_scheduled(tokens)
        sync()
        context, extra = cond[0]
        return context.cpu(), {k: v for k, v in extra.items() if k in ("image_slots", "pooled_output")}, time.perf_counter() - t0

    def unload(self):
        mm.unload_all_models()
        self.clip = None
        mm.soft_empty_cache()


class _GuiderBasic:
    """ComfyUI's Guider_Basic (comfy_extras/nodes_custom_sampler.py): CFG 1, positive conditioning only."""

    @staticmethod
    def make(patcher, positive):
        g = comfy.samplers.CFGGuider(patcher)
        g.inner_set_conds({"positive": positive})
        g.set_cfg(1.0)
        return g


_frozen = {"cap": None}


def freeze(cap_bytes: int | None = None):
    """From now on ComfyUI may not move any model (the live rule: no weight moves while the film plays):
    - its loader only checks that what a sampler or the VAE asks for is already completely on the GPU, else it raises
      (a reload would stream weights; a partial unload would stream them at every step, as with E1's capped painter);
    - emptying the CUDA cache is a no-op, so the allocator's pool, reserved once by a warm-up plate, is reused and the
      driver sees no allocations while the film plays;
    - with cap_bytes, the torch allocator is capped there and ComfyUI's free-memory figure counts that cap (its VAE
      batch size and the prefix cache's placement read it)."""
    if _frozen.get("on"):
        return
    orig_free = mm.get_free_memory

    def load_models_gpu(models, *args, **kwargs):
        for m in models:
            lm = next((x for x in mm.current_loaded_models if x.model is m), None)
            if lm is None or lm.model_loaded_memory() < lm.model_memory() - 2**20:
                raise RuntimeError(f"painter frozen: {type(getattr(m, 'model', m)).__name__} is not completely on the "
                                   f"GPU and would be (re)loaded")
            lm.currently_used = True

    def get_free_memory(dev=None, torch_free_too=False):
        out = orig_free(dev, torch_free_too)
        cap = _frozen["cap"]
        if cap is None or (dev is not None and getattr(dev, "type", "cuda") == "cpu"):
            return out
        left = max(0, cap - torch.cuda.memory_allocated())
        if torch_free_too:
            return min(out[0], left), min(out[1], max(0, cap - torch.cuda.memory_reserved()) +
                                          torch.cuda.memory_reserved() - torch.cuda.memory_allocated())
        return min(out, left)

    if cap_bytes:
        total = torch.cuda.get_device_properties(0).total_memory
        torch.cuda.set_per_process_memory_fraction(min(1.0, cap_bytes / total))
        _frozen["cap"] = cap_bytes
    mm.load_models_gpu = load_models_gpu
    mm.get_free_memory = get_free_memory
    mm.soft_empty_cache = lambda *a, **k: None
    _frozen["on"] = True


def throttle_queue(modules) -> int:
    """One piece of work in flight: the CPU waits for the GPU at the end of each of these modules, so the painter never
    has more than one attention, MLP or VAE convolution queued ahead of the player's frames. E1's synthetic loads ran
    one call in flight and were harmless (1-3 wrong frames a minute); the resident painter, queueing hundreds of kernels
    ahead, stalled the player up to ~50 ms (8 wrong frames a minute at 240 Hz, out/e1/20261014-011121). Costs about one
    launch gap per module (~30 ms a plate). Returns how many hooks were set."""
    def hook(mod, inp, out):
        torch.cuda.current_stream().synchronize()
    n = 0
    for m in modules:
        m.register_forward_hook(hook)
        n += 1
    return n


def lower_gpu_priority(class_id: int = 1) -> int:
    """This process's GPU scheduling class (D3DKMT: 0 idle, 1 below normal, 2 normal, ...; the call perf1 --gpu-priority
    makes for HIGH), set before CUDA starts. Returns the NTSTATUS (0: done)."""
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32")
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    fn = ctypes.WinDLL("gdi32").D3DKMTSetProcessSchedulingPriorityClass
    fn.argtypes, fn.restype = [wintypes.HANDLE, ctypes.c_int], ctypes.c_long
    return int(fn(k32.GetCurrentProcess(), class_id)) & 0xFFFFFFFF


class _Peak:
    """Peak torch-allocated memory over a block, in MB (the allocator's high-water mark, reset at entry)."""

    def __enter__(self):
        if torch.cuda.is_available():
            torch.cuda.synchronize()
            torch.cuda.reset_peak_memory_stats()
            self.base = torch.cuda.memory_allocated()
        return self

    def __exit__(self, *exc):
        if torch.cuda.is_available():
            torch.cuda.synchronize()
            self.mb = round((torch.cuda.max_memory_allocated() - self.base) / 2**20)
        else:
            self.mb = 0
        return False


class Painter:
    """DiT + VAE, loaded once onto the GPU and kept there. loras: [(file, strength)], baked into the weights at load
    (quantized layers are re-quantized by ComfyUI); a merged file (painter-quantize.py) needs none.
    kv_cache: the model's text + reference K/V cache for one plate, {"device": "gpu"|"cpu"|"auto"|"off",
    "dtype": "default"|"int8"|"int4"}; "cpu" moves it over PCIe at every step, so the live path uses "gpu" or "off".
    vae_tile: None decodes the plate in one piece; (tile_px, overlap_px) decodes it in one pass of feathered tiles
    (bounded memory; ComfyUI's own tiled decode runs three tilings and averages them)."""

    def __init__(self, dit_file: str, vae_file: str = "qwen_image_2.1_vae_bf16.safetensors", loras=(),
                 kv_cache=None, dit_dtype: str = "default", vae_tile=None):
        self.dit_path, self.vae_path = model_path("diffusion_models", dit_file), model_path("vae", vae_file)
        opts = {}
        if dit_dtype == "fp8_e4m3fn":
            opts["dtype"] = torch.float8_e4m3fn
        patcher = comfy.sd.load_diffusion_model(self.dit_path, model_options=opts)
        self.loras = []
        for file, strength in loras:
            path = model_path("loras", file)
            sd = comfy.utils.load_torch_file(path, safe_load=True)
            patcher, _ = comfy.sd.load_lora_for_models(patcher, None, sd, strength, 0)
            self.loras.append((os.path.basename(path), strength))
        if kv_cache:
            patcher.model_options.setdefault("transformer_options", {})["qwen_image21_cache"] = dict(kv_cache)
        self.patcher = patcher
        self._target_cols = None                                  # sides-only painting: the target's canvas columns
        self._name_target_columns()
        sd, meta = comfy.utils.load_torch_file(self.vae_path, return_metadata=True)
        self.vae = comfy.sd.VAE(sd=sd, metadata=meta)
        self.vae.throw_exception_if_invalid()
        self.vae_tile = vae_tile
        self.samplers = {}                                        # by name: euler (the Turbo card's), res_multistep, ...
        self.load_log = []

    def load(self, cap_bytes: int | None = None, gpu_queue: str = "off", attn_chunk: int = 0):
        """Both models completely onto the GPU (LoRAs baked here), then freeze(): nothing moves after this. Returns
        what ComfyUI loaded."""
        sync()
        t0 = time.perf_counter()
        mm.load_models_gpu([self.patcher, self.vae.patcher], force_full_load=True)
        sync()
        loaded = {type(m.model.model).__name__: {"loaded_mb": round(m.model_loaded_memory() / 2**20),
                                                  "total_mb": round(m.model_memory() / 2**20)}
                  for m in mm.current_loaded_models}
        self.load_log.append({"seconds": round(time.perf_counter() - t0, 2), "loaded": loaded})
        if not self.fully_loaded():
            raise RuntimeError(f"the painter does not fit on the GPU completely: {loaded}")
        freeze(cap_bytes)
        if gpu_queue != "off":                                 # "layer": attention and MLP; "block": whole blocks
            import torch.nn as nn
            mods = []
            for blk in self.patcher.model.diffusion_model.transformer_blocks:
                mods += [blk.attn, blk.img_mlp] if gpu_queue == "layer" else [blk]
            mods += [m for m in self.vae.first_stage_model.modules() if isinstance(m, (nn.Conv2d, nn.Conv3d))]
            self.load_log.append({"gpu_queue": gpu_queue, "hooks": throttle_queue(mods)})
        if attn_chunk > 0:                                       # attention in kernels of at most attn_chunk queries
            import painter_attention
            painter_attention.install(attn_chunk)
            self.load_log.append({"attn_chunk": attn_chunk})
        return loaded

    def _sampler(self, name: str):
        if name not in self.samplers:
            self.samplers[name] = comfy.samplers.sampler_object(name)
        return self.samplers[name]

    def _name_target_columns(self):
        """Sides-only painting: the target latent holds only some of the canvas's columns, and qwen_image21's
        build_sequence numbers a target's columns from its own centre. The rope ids reach pe_embedder as (1, N, 3) with
        the target last; while self._target_cols is set, the target's column ids become the canvas columns it holds,
        so they line up with the reference's. A post_input patch could do the same, but the model treats any
        post_input patch as a hook and turns the text + reference K/V cache off."""
        emb = self.patcher.model.diffusion_model.pe_embedder
        inner = emb.forward

        def forward(ids):
            cols = self._target_cols
            if cols is not None:
                ids = ids.clone()
                ids[0, -cols.numel():, 2] = cols.to(ids.device, ids.dtype)
            return inner(ids)
        emb.forward = forward

    def _decode(self, samples):
        if self.vae_tile is None:
            img = self.vae.decode(samples)
        else:
            tile, overlap = (max(1, v // self.vae.upscale_ratio) for v in self.vae_tile)
            fm = self.vae.first_stage_model
            img = comfy.utils.tiled_scale(
                samples, lambda a: fm.decode(a.to(self.vae.vae_dtype).to(self.vae.device)).to(self.vae.vae_output_dtype()),
                tile, tile, overlap, upscale_amount=self.vae.upscale_ratio, out_channels=self.vae.output_channels,
                output_device=self.vae.output_device)
            img = self.vae.process_output(img).movedim(1, -1)
        return img.reshape(-1, *img.shape[-3:]) if img.dim() == 5 else img

    def fully_loaded(self) -> bool:
        """True when no part of the DiT or the VAE lives off the GPU (nothing would stream)."""
        for p in (self.patcher, self.vae.patcher):
            lm = next((x for x in mm.current_loaded_models if x.model is p), None)
            if lm is None or lm.model_loaded_memory() < lm.model_memory() - 2**20:
                return False
        return True

    def paint(self, canvas: np.ndarray, context, extra: dict, seed: int = 7, sigmas=TURBO_SIGMAS,
              ref_scale: float = 1.0, extra_refs=(), sides=None, sampler: str = "euler") -> tuple:
        """(plate uint8 at the canvas size, seconds per stage). canvas: (H, W, 3), multiples of 32; the target is its
        size and so is the reference latent (TextEncodeQwenImage21's convention with resolution 0) unless ref_scale < 1
        makes the reference a smaller copy (fewer prefix tokens: a cheaper first step and fewer keys at every step; the
        model places references centred on the target). Runs in inference mode, as ComfyUI's executor runs every node
        (no autograd records, so no activations kept for a backward pass). extra_refs: identity references (uint8
        RGB, sides multiples of 32), spliced after the canvas's latent; the conditioning must have one image slot per
        reference (a proxy encoded with as many images).
        sides: [(x0, x1), ...] canvas columns, multiples of 16: only those are painted (sides-only), at the canvas's
        full density, still seeing the whole canvas as the reference; the rest of the plate is the reference latent
        decoded (the picture there, which callers replace with the real pixels anyway)."""
        slots = len(extra.get("image_slots") or [])
        if slots != 1 + len(extra_refs):
            raise ValueError(f"the conditioning has {slots} image slots for {1 + len(extra_refs)} references")
        with torch.inference_mode():
            return self._paint(canvas, context, extra, seed, sigmas, ref_scale, extra_refs, sides, sampler)

    def _paint(self, canvas, context, extra, seed, sigmas, ref_scale=1.0, extra_refs=(), sides=None, sampler="euler"):
        h, w = canvas.shape[:2]
        if h % 32 or w % 32:
            raise ValueError(f"canvas {w}x{h}: sides must be multiples of 32")
        cols = None
        if sides:
            if ref_scale < 0.999:
                raise ValueError("sides-only painting splices into the full-size reference latent: ref_scale must be 1")
            cols = torch.cat([torch.arange(x0 // 16, x1 // 16) for x0, x1 in sides])
            if (w // 16) % 2 or cols.numel() % 2:                 # odd widths shift the reference's ids by half a token
                raise ValueError(f"sides-only: the canvas ({w // 16}) and the target ({cols.numel()}) need even latent widths")
        secs, peaks = {}, {}
        sync()
        t0 = time.perf_counter()
        ref_img = canvas
        if ref_scale < 0.999:
            rw, rh = max(32, int(round(w * ref_scale / 32)) * 32), max(32, int(round(h * ref_scale / 32)) * 32)
            ref_img = np.asarray(Image.fromarray(canvas).resize((rw, rh), Image.LANCZOS))
        with _Peak() as pk:
            ref = self.vae.encode(to_tensor(ref_img))
            refs = [ref] + [self.vae.encode(to_tensor(round32(im))) for im in extra_refs]
        peaks["vae_encode"] = pk.mb
        t1 = time.perf_counter()
        secs["vae_encode"] = t1 - t0
        info = dict(extra)
        info["reference_latents"] = refs
        positive = [[context, info]]
        latent = torch.zeros([1, 64, h // 16, w // 16 if cols is None else cols.numel()], device=mm.intermediate_device())
        latent = comfy.sample.fix_empty_latent_channels(self.patcher, latent, None, None)
        noise = comfy.sample.prepare_noise(latent, seed, None)
        guider = _GuiderBasic.make(self.patcher, positive)
        if cols is not None:                                      # the target's column ids, row after row
            self._target_cols = (cols.float() - (w // 16) // 2).repeat(h // 16)
        try:
            with _Peak() as pk:
                samples = guider.sample(noise, latent, self._sampler(sampler), torch.FloatTensor(list(sigmas)), denoise_mask=None,
                                        callback=None, disable_pbar=True, seed=seed)
        finally:
            self._target_cols = None
        peaks["sample"] = pk.mb
        t2 = time.perf_counter()
        secs["sample"] = t2 - t1
        samples = samples.to(mm.intermediate_device())
        if cols is not None:                                      # the painted columns into the canvas's own latent
            r = ref[:, :, 0] if ref.dim() == 5 else ref
            if tuple(r.shape[1:]) != (samples.shape[1], h // 16, w // 16):
                raise RuntimeError(f"sides-only: the reference latent {tuple(ref.shape)} is not the target's layout")
            full = r.to(samples.device, samples.dtype).clone()
            full[..., cols.to(samples.device)] = samples
            samples = full
        with _Peak() as pk:
            img = self._decode(samples)
        peaks["vae_decode"] = pk.mb
        secs["vae_decode"] = time.perf_counter() - t2
        secs["total"] = time.perf_counter() - t0
        plate = (img[0].clamp(0, 1).mul(255).round().to(torch.uint8).cpu().numpy())
        out = {k: round(v, 3) for k, v in secs.items()}
        out["peak_mb"] = peaks
        return plate, out


def vram_state() -> dict:
    """The process's CUDA memory (torch allocator) and the device's (driver), in MB; and the allocator's counts since
    start: retries (an allocation that had to free cached blocks first: a device-wide sync each), device allocs / frees
    (cudaMalloc / cudaFree; steady plates should add none)."""
    if not torch.cuda.is_available():
        return {}
    free, total = torch.cuda.mem_get_info()
    st = torch.cuda.memory_stats()
    return {"allocated_mb": round(torch.cuda.memory_allocated() / 2**20), "reserved_mb": round(torch.cuda.memory_reserved() / 2**20),
            "max_reserved_mb": round(torch.cuda.max_memory_reserved() / 2**20),
            "max_allocated_mb": round(torch.cuda.max_memory_allocated() / 2**20),
            "device_used_mb": round((total - free) / 2**20), "device_total_mb": round(total / 2**20),
            "alloc_retries": int(st.get("num_alloc_retries", 0)), "device_allocs": int(st.get("num_device_alloc", 0)),
            "device_frees": int(st.get("num_device_free", 0))}


def save_cond(path: str, context, extra: dict, info: dict):
    torch.save({"context": context, "extra": extra, "info": info}, path)


def load_cond(path: str) -> tuple:
    d = torch.load(path, map_location="cpu", weights_only=False)
    return d["context"], d["extra"]


def setup_logging(path: str):
    logging.basicConfig(filename=path, level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        force=True)
