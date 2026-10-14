"""Painter kernels: how fast each of comfy_kitchen's formats runs the painter DiT's own matmuls and attention on this GPU,
at the shapes of a ~1 MP plate (Qwen-Image-2.1: 32 blocks of q, k, v, out 4096x4096, gate_up 24576x4096, out
4096x12288; 4032 target tokens; the first step also runs the ~4070-token text + reference prefix). Decides which
speed lever is worth a quality test (phase 3): FP4 tensor cores (NVFP4) for the linears, INT8 attention.

Linears: bf16, int8 (per-channel ConvRot, the official file's), w4a8, w6a8 (painter-quantize.py's), nvfp4 (W4A4,
dynamic activation scale), each through ComfyUI's own mixed-precision Linear (comfy.ops), so the kernels are the ones the
painter would run. Attention: ComfyUI's SDPA path and comfy_kitchen's INT8 attention, 32 heads x 128, queries 4032,
keys 4032 + prefix. Median of --reps timed runs after warm-up (CUDA events). Runs in ComfyUI's embedded Python; GPU,
a few seconds of load.

    tools/ComfyUI_windows_portable/python_embeded/python.exe -s spikes/ai-surround/painter-kernels-bench.py [--reps 20]
"""
import argparse
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import painter_core as pc  # noqa: E402

SHAPES = {"qkvo": (4096, 4096), "gate_up": (24576, 4096), "mlp_out": (4096, 12288)}
PER_BLOCK = {"qkvo": 4, "gate_up": 1, "mlp_out": 1}
FORMATS = {
    "int8": ("TensorWiseINT8Layout", {"per_channel": True, "convrot": True, "convrot_groupsize": 256},
             {"format": "int8_tensorwise", "convrot": True, "convrot_groupsize": 256}),
    "w4a8": ("AsymW4A8Int8Layout", {"group_size": 16, "convrot_groupsize": 256, "symmetric": True, "codebook": True,
                                    "bits": 4, "scale_search": False},
             {"format": "asym_w4a8_int8", "group_size": 16, "convrot_groupsize": 256}),
    "w6a8": ("AsymW4A8Int8Layout", {"group_size": 16, "convrot_groupsize": 256, "symmetric": True, "codebook": False,
                                    "bits": 6, "scale_search": False},
             {"format": "w6a8_int8", "group_size": 16, "convrot_groupsize": 256}),
    "nvfp4": ("TensorCoreNVFP4Layout", {}, {"format": "nvfp4"}),
}


def timed(fn, reps):
    torch = pc.torch
    for _ in range(3):
        fn()
    torch.cuda.synchronize()
    out = []
    for _ in range(reps):
        a, b = torch.cuda.Event(enable_timing=True), torch.cuda.Event(enable_timing=True)
        a.record()
        fn()
        b.record()
        b.synchronize()
        out.append(a.elapsed_time(b))
    return statistics.median(out)


def linear_layer(fmt, n, k):
    """A ComfyUI mixed-precision Linear holding a random weight in `fmt` (None: bf16), built through its own loader."""
    import json as _json
    import comfy.ops
    from comfy_kitchen.tensor import QuantizedTensor
    torch = pc.torch
    w = torch.randn(n, k, device="cuda", dtype=torch.float32) * 0.02
    sd = {}
    if fmt is None:
        sd["weight"] = w.to(torch.bfloat16)
        ops = comfy.ops.mixed_precision_ops({}, torch.bfloat16)
    else:
        layout, kw, conf = FORMATS[fmt]
        q = QuantizedTensor.from_float(w.to(torch.bfloat16) if fmt == "nvfp4" else w, layout, **kw)
        sd.update(q.state_dict("weight"))
        sd = {k: v.cpu() for k, v in sd.items()}                 # as a checkpoint file gives them
        sd["comfy_quant"] = torch.tensor(list(_json.dumps(conf).encode("utf-8")), dtype=torch.uint8)
        ops = comfy.ops.mixed_precision_ops({}, torch.bfloat16)
    lin = ops.Linear(k, n, bias=False, device="cuda", dtype=torch.bfloat16)
    lin.load_state_dict({key: v if key == "comfy_quant" else v.cuda() for key, v in sd.items()}, strict=False)
    return lin


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--reps", type=int, default=20)
    ap.add_argument("--formats", default="bf16,int8,w4a8,w6a8,nvfp4")
    ap.add_argument("--no-attention", action="store_true")
    ap.add_argument("--tokens", type=int, default=4032)
    ap.add_argument("--prefix", type=int, default=4070)
    a = ap.parse_args(argv)
    pc.init_comfy()
    torch = pc.torch
    import comfy.ldm.modules.attention as attn
    import comfy_kitchen
    res = {"gpu": torch.cuda.get_device_name(0), "tokens": a.tokens, "prefix": a.prefix, "linear_ms": {}, "attention_ms": {}}
    x = torch.randn(a.tokens, 4096, device="cuda", dtype=torch.bfloat16)
    with torch.inference_mode():
        for fmt in [None if f == "bf16" else f for f in a.formats.split(",")]:
            name = fmt or "bf16"
            res["linear_ms"][name] = {}
            for shape, (n, k) in SHAPES.items():
                try:
                    lin = linear_layer(fmt, n, k)
                    xin = x if k == 4096 else torch.randn(a.tokens, k, device="cuda", dtype=torch.bfloat16)
                    ms = timed(lambda lin=lin, xin=xin: lin(xin), a.reps)
                    res["linear_ms"][name][shape] = round(ms, 3)
                    del lin
                except Exception as e:                            # a format this GPU or build cannot run
                    res["linear_ms"][name][shape] = f"failed: {e!r}"[:300]
            per_block = [res["linear_ms"][name].get(s) for s in SHAPES]
            if all(isinstance(v, float) for v in per_block):
                res["linear_ms"][name]["per_step_32_blocks"] = round(
                    32 * sum(PER_BLOCK[s] * res["linear_ms"][name][s] for s in SHAPES), 1)
            print(name, res["linear_ms"][name], flush=True)
            torch.cuda.empty_cache()
        heads, d = 32, 128
        if a.no_attention:
            fns_skip = True
        else:
            fns_skip = False
        q = torch.randn(1, a.tokens, heads * d, device="cuda", dtype=torch.bfloat16)
        kv = torch.randn(1, a.tokens + a.prefix, heads * d, device="cuda", dtype=torch.bfloat16)
        fns = {"pytorch_sdpa": attn.attention_pytorch}
        if comfy_kitchen.int8_attention_is_available():
            fns["ck_int8"] = attn.attention_comfy_kitchen_int8
        for name, fn in ({} if fns_skip else fns).items():
            try:
                ms = timed(lambda: fn(q, kv, kv, heads), a.reps)
                res["attention_ms"][name] = {"one": round(ms, 3), "per_step_32_blocks": round(32 * ms, 1)}
                if name != "pytorch_sdpa":
                    ref = attn.attention_pytorch(q, kv, kv, heads).float()
                    out = fn(q, kv, kv, heads).float()
                    res["attention_ms"][name]["rel_err_vs_sdpa"] = round(float((out - ref).norm() / ref.norm()), 5)
            except Exception as e:
                res["attention_ms"][name] = f"failed: {e!r}"[:300]
            print(name, res["attention_ms"][name], flush=True)
    print(json.dumps(res, indent=1))
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out", "runs", "painter-bench", "kernels.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(res, f, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
