"""Which of the live painter's GPU kernels are long enough to hurt the player? E1 found 2-12 ms kernels harmless (1-3
wrong frames a minute, fewer than no load), yet the resident painter, with no weight moves left, still costs the player
8 wrong frames a minute at 240 Hz (out/e1/20261014-011121). This paints one warm plate under torch.profiler and lists the
kernels by duration: how many run longer than 4 / 8 / 12 / 16 ms, from which stage (VAE encode, sampling, VAE decode),
and the longest ones by name. Runs in ComfyUI's embedded Python; GPU, about a minute.

    tools/ComfyUI_windows_portable/python_embeded/python.exe -s spikes/ai-surround/painter-kernel-profile.py
        [--dit qwen_image_2.1_turbo8-ov2_w4a8.safetensors] [--attention pytorch|ck-int8] [--canvas 1344x768]
        [--schedule turbo8] [--ref-scale 1.0] [--vae-tile TILE:OVERLAP] [--out FILE.json]
"""
import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import painter_core as pc  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dit", default="qwen_image_2.1_turbo8-ov2_w4a8.safetensors")
    ap.add_argument("--cond", default=os.path.join(HERE, "out", "live", "painter", "cond-proxy-grey.pt"))
    ap.add_argument("--attention", default="pytorch", choices=pc.ATTENTION)
    ap.add_argument("--canvas", default="1344x768")
    ap.add_argument("--schedule", default="turbo8")
    ap.add_argument("--ref-scale", type=float, default=1.0)
    ap.add_argument("--vae-tile", default="")
    ap.add_argument("--out", default=os.path.join(HERE, "out", "runs", "painter-bench", "kernel-profile.json"))
    a = ap.parse_args(argv)
    pc.init_comfy(attention=a.attention)
    torch = pc.torch
    from torch.profiler import ProfilerActivity, profile, record_function
    tile = tuple(int(v) for v in a.vae_tile.split(":")) if a.vae_tile else None
    painter = pc.Painter(a.dit, kv_cache={"device": "gpu", "dtype": "int8"}, vae_tile=tile)
    painter.load(int(9 * 2**30))
    context, extra = pc.load_cond(a.cond)
    w, h = (int(v) for v in a.canvas.split("x"))
    rng = np.random.default_rng(1)
    canvas = np.full((h, w, 3), 128, np.uint8)
    canvas[:, w // 4:3 * w // 4] = rng.integers(0, 255, (h, w // 2, 3), dtype=np.uint8)   # a busy picture in the middle
    sig = pc.SCHEDULES[a.schedule]
    painter.paint(canvas, context, extra, 7, sig, a.ref_scale)                     # warm: kernels picked, pool reserved
    pc.sync()
    with profile(activities=[ProfilerActivity.CUDA]) as prof:
        with record_function("plate"):
            _, secs = painter.paint(canvas, context, extra, 7, sig, a.ref_scale)
        pc.sync()
    kernels = []
    for ev in prof.events():
        dt = getattr(ev, "device_type", None)
        if dt is not None and "CUDA" in str(dt) and ev.device_time_total > 0:
            kernels.append((ev.name, ev.device_time_total / 1000.0))             # ms
    kernels.sort(key=lambda k: -k[1])
    total = sum(k[1] for k in kernels)
    bins = {f">{t}ms": sum(1 for k in kernels if k[1] > t) for t in (4, 8, 12, 16)}
    time_long = {f">{t}ms": round(sum(k[1] for k in kernels if k[1] > t), 1) for t in (4, 8, 12, 16)}
    names = {}
    for name, ms in kernels:
        n = names.setdefault(name[:110], [0, 0.0, 0.0])
        n[0] += 1
        n[1] = max(n[1], ms)
        n[2] += ms
    top = sorted(names.items(), key=lambda kv: -kv[1][1])[:20]
    res = {"settings": vars(a), "plate_seconds": secs, "kernels": len(kernels), "gpu_ms_total": round(total, 1),
           "count_longer_than": bins, "ms_in_kernels_longer_than": time_long,
           "longest": [{"name": k, "count": v[0], "max_ms": round(v[1], 2), "total_ms": round(v[2], 1)} for k, v in top]}
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(res, f, indent=1)
    print(json.dumps({k: res[k] for k in ("plate_seconds", "kernels", "gpu_ms_total", "count_longer_than",
                                          "ms_in_kernels_longer_than")}, indent=1))
    for t in res["longest"][:14]:
        print(f"{t['max_ms']:8.2f} ms max  x{t['count']:4d}  {t['total_ms']:8.1f} ms total  {t['name']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
