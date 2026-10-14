"""Merge the painter's LoRAs into Qwen-Image-2.1's DiT and store it quantized in ComfyUI's own checkpoint format, so the
live painter (painter-service.py) loads one file, applies no LoRA at load and fits its VRAM budget next to the player.

Why (phase 3, plans/261013-0718-ai-surround-live-path/phase-03-painter-at-speed.md): the official INT8 DiT is 7.26 GB.
Resident with the VAE it leaves no room for the plate's decode, the text + reference K/V cache and phase 5's refiner
inside the worker's ~8 GB, and ComfyUI then unloads part of it at every plate (weights moving while the film plays:
the E1 failure). Formats, all Comfy-Org's comfy_kitchen layouts on the 192 block linears (q, k, v, out, gate_up, out of
32 blocks; the rest stays bf16 as in the official INT8 file):
  w4a8   ConvRot-rotated 4-bit weights, groups of 16 with fp8 scales and a Lloyd-Max codebook, INT8 activations
         (the layout of Comfy-Org's own W4A8 text encoders): ~4.2 GB
  w6a8   the same with uniform 6-bit codes (about 3x less weight error for 1.5x the bytes): ~5.9 GB
  int8   per-channel INT8 with ConvRot, like the official file but with the LoRAs merged before quantizing: ~7.3 GB
The LoRAs are merged in fp32 with ComfyUI's own LoRA code (comfy.lora.calculate_weight), the same maths as baking them at
load. "lora key not loaded" warnings must be 0. Per layer, the relative weight error of the stored layer against the
merged fp32 weight is logged (the swap test judges the plates).

    tools/ComfyUI_windows_portable/python_embeded/python.exe -s spikes/ai-surround/painter-quantize.py
        --format w4a8|w6a8|int8 [--base qwen_image_2.1_bf16.safetensors] [--lora FILE:STRENGTH ...] [--out NAME]

Default LoRAs: X2's (Turbo rank-178 extraction 1.0, outpaint v2 1.0). Output: tools/models/comfy/qwen-image-2.1/
diffusion_models/<out>.safetensors plus <out>.quant.json (recipe, errors, timings).
"""
import argparse
import json
import logging
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import painter_core as pc  # noqa: E402

DEFAULT_LORAS = ["qwen_image_2.1_turbo_lora_avg_rank_178_bf16.safetensors:1.0",
                 "qwen-image-2.1-outpaint-v2.safetensors:1.0"]
BLOCK_LINEAR = re.compile(r"^transformer_blocks\.\d+\.(attn\.to_q|attn\.to_k|attn\.to_v|attn\.to_out\.0|"
                          r"img_mlp\.gate_up|img_mlp\.out)\.weight$")
FORMATS = {
    "w4a8": ("AsymW4A8Int8Layout", {"group_size": 16, "convrot_groupsize": 256, "symmetric": True, "codebook": True,
                                    "bits": 4, "scale_search": True},
             {"format": "asym_w4a8_int8", "group_size": 16, "convrot_groupsize": 256}),
    "w6a8": ("AsymW4A8Int8Layout", {"group_size": 16, "convrot_groupsize": 256, "symmetric": True, "codebook": False,
                                    "bits": 6, "scale_search": True},
             {"format": "w6a8_int8", "group_size": 16, "convrot_groupsize": 256}),
    "int8": ("TensorWiseINT8Layout", {"per_channel": True, "convrot": True, "convrot_groupsize": 256},
             {"format": "int8_tensorwise", "convrot": True, "convrot_groupsize": 256}),
    # FP4 weights and activations (W4A4) on Blackwell's FP4 tensor cores through cuBLAS: per-tensor scale + an fp8
    # scale per 16 values; the activations are quantized the same way at every call (no calibration). 27 % faster
    # linears than INT8 here (painter-kernels-bench.py), at the precision risk of 4-bit activations.
    "nvfp4": ("TensorCoreNVFP4Layout", {}, {"format": "nvfp4"}),
}
MIXES = {                                                         # per layer kind: attention projections / MLP
    "w4a8-nvfp4mlp": ("w4a8", "nvfp4"),
}


class CountWarnings(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.lora_misses = []

    def emit(self, record):
        msg = record.getMessage()
        if "lora key not loaded" in msg:
            self.lora_misses.append(msg)


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--format", choices=sorted(FORMATS) + sorted(MIXES), required=True,
                    help="one format for every block linear, or a mix (attention projections / MLP)")
    ap.add_argument("--base", default="qwen_image_2.1_bf16.safetensors")
    ap.add_argument("--lora", action="append", default=None, help=f"FILE:STRENGTH (default {DEFAULT_LORAS})")
    ap.add_argument("--out", default="", help="output name without extension")
    a = ap.parse_args(argv)
    loras = a.lora or DEFAULT_LORAS
    out_name = a.out or f"qwen_image_2.1_turbo8-ov2_{a.format}"
    out_path = pc.model_path("diffusion_models", out_name + ".safetensors")
    if os.path.exists(out_path):
        raise SystemExit(f"{out_path} exists; pass another --out")

    pc.init_comfy()
    import comfy.lora
    import comfy.sd
    import comfy.utils
    from comfy_kitchen.tensor import QuantizedTensor
    from safetensors.torch import save_file
    torch = pc.torch
    counter = CountWarnings()
    logging.getLogger().addHandler(counter)
    logging.getLogger().setLevel(logging.INFO)

    t0 = time.perf_counter()
    patcher = comfy.sd.load_diffusion_model(pc.model_path("diffusion_models", a.base))
    for spec in loras:
        file, _, strength = spec.rpartition(":")
        sd = comfy.utils.load_torch_file(pc.model_path("loras", file), safe_load=True)
        patcher, _ = comfy.sd.load_lora_for_models(patcher, None, sd, float(strength), 0)
        del sd
    print(f"base + {len(loras)} LoRAs ready in {time.perf_counter() - t0:.0f} s; {len(patcher.patches)} patched keys; "
          f"lora key misses {len(counter.lora_misses)}", flush=True)
    if counter.lora_misses:
        raise SystemExit("LoRA keys not loaded:\n" + "\n".join(counter.lora_misses[:20]))

    attn_fmt, mlp_fmt = MIXES.get(a.format, (a.format, a.format))
    dm = patcher.model.diffusion_model
    keys = list(dm.state_dict().keys())
    out, errors, bytes_q = {}, {}, 0
    t1 = time.perf_counter()
    for i, key in enumerate(keys):
        pkey = "diffusion_model." + key
        param = comfy.utils.get_attr(dm, key)
        w = param.detach().to(device="cuda", dtype=torch.float32, copy=True)
        if pkey in patcher.patches:
            w = comfy.lora.calculate_weight(patcher.patches[pkey], w, pkey)
        if BLOCK_LINEAR.match(key):
            fmt = mlp_fmt if ".img_mlp." in key else attn_fmt
            layout, qkw, conf = FORMATS[fmt]
            q = QuantizedTensor.from_float(w.to(torch.bfloat16) if fmt == "nvfp4" else w, layout, **qkw)
            prefix = key[:-len("weight")]
            for k, v in q.state_dict(prefix + "weight").items():
                out[k] = v.contiguous().cpu()
                bytes_q += v.numel() * v.element_size()
            out[prefix + "comfy_quant"] = torch.tensor(list(json.dumps(conf).encode("utf-8")), dtype=torch.uint8)
            deq = q.dequantize().float()
            errors[key] = round(float((deq - w).norm() / w.norm().clamp_min(1e-12)), 5)
            del q, deq
        else:
            out[key] = w.to(torch.bfloat16).cpu()
            bytes_q += out[key].numel() * 2
        comfy.utils.set_attr_param(dm, key, torch.empty(0))            # the base copy leaves RAM as we go
        del w
        if i % 24 == 0:
            print(f"  {i + 1}/{len(keys)} {key}  err {errors.get(key, '-')}  {bytes_q / 2**30:.2f} GiB so far", flush=True)
    t2 = time.perf_counter()
    errs = sorted(errors.values())
    stats = {"layers": len(errs), "rel_err_mean": round(sum(errs) / len(errs), 5), "rel_err_median": errs[len(errs) // 2],
             "rel_err_max": errs[-1], "worst": sorted(errors.items(), key=lambda kv: -kv[1])[:8]}
    recipe = {"base": a.base, "loras": loras, "format": a.format, "attention_layers": FORMATS[attn_fmt],
              "mlp_layers": FORMATS[mlp_fmt], "merge": "fp32, comfy.lora.calculate_weight",
              "made": time.strftime("%Y-%m-%d %H:%M")}
    save_file(out, out_path, metadata={"format": "pt", "panorama_recipe": json.dumps(recipe)})
    size = os.path.getsize(out_path)
    report = {"recipe": recipe, "file": out_path, "bytes": size, "seconds": {"load_merge_setup": round(t1 - t0, 1),
              "quantize": round(t2 - t1, 1), "save": round(time.perf_counter() - t2, 1)}, "errors": stats,
              "per_layer": errors}
    with open(out_path.replace(".safetensors", ".quant.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1)
    print(f"wrote {out_path}: {size / 2**30:.2f} GiB; weight error mean {stats['rel_err_mean']} median "
          f"{stats['rel_err_median']} max {stats['rel_err_max']}; quantize {t2 - t1:.0f} s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
