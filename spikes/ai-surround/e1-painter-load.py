"""E1's real-painter load: plates back to back until the E1 runner's stop file appears, so the player is measured next to
the live painter's actual kernels. Two painters:
  default    one resident ComfyUI server (painter-speed.py's graphs; X2, E1, phase 2)
  --service  the live worker's resident painter service (live/painter.py + painter-service.py: the merged quantized DiT
             loaded once and frozen, its pool reserved by a warm-up plate, capped at --vram-cap-gb), painting the
             hidden-sides canvases with a conditioning file, called exactly as the worker calls it (phase 3)

The E1 runner (e1-player-under-load.py --painter-cmd) starts this in each painter cell with E1_STOP_FILE and E1_CELL_DIR
set. It prints "LOAD STEADY" once the first plate is done (models loaded, sampling in a loop), then keeps painting with
no rest between plates (the runner rests between cells), and writes one line per plate to E1_CELL_DIR/painter-load.jsonl.

--identity-encoder N (with --service): the worker's CPU identity encoder (live/identity_encoder.py, the 8B encoder at
below-normal priority, ~25 s a crop) also reads N distinct identity crops, queued through its own read-ahead path, into
the cell folder (no cached reads). N = 12 keeps it busy for ~5 min, past a 2-minute cell: its worst case (live, it reads
only while crops wait). "LOAD STEADY" then also waits for its first read.

    python -I e1-painter-load.py --variant turbo8-s10-1mp [--best turbo8-s10] [--scenes jjk-pan,jjk-action,...]
                                 [--port 8190] [--dit-dtype fp8_e4m3fn_fast] [--server-args=...]
    python -I e1-painter-load.py --service [--dit FILE] [--cond FILE] [--vram-cap-gb 7] [--port 8191]
                                 [--identity-encoder 12 [--identity-crops GLOB]]
"""
import argparse
import glob
import importlib.util
import json
import os
import sys
import time

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)                                          # python -I leaves the script's folder off sys.path
spec = importlib.util.spec_from_file_location("painter_speed", os.path.join(HERE, "painter-speed.py"))
ps = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ps)
cc, hs = ps.cc, ps.hs


def service_load(a, stop, cell):
    """The worker's resident painter (live/painter.py), plates back to back on the scenes' ~1 MP canvases."""
    from live.painter import Painter, PainterConfig
    cfg = PainterConfig()
    for field, value in (("dit", a.dit), ("cond", a.cond), ("vram_cap_gb", a.vram_cap_gb), ("port", a.service_port),
                         ("gpu_queue", a.gpu_queue), ("gpu_priority", a.gpu_priority), ("attention", a.attention),
                         ("attn_chunk", a.attn_chunk)):
        if value is not None:
            setattr(cfg, field, value)
    canvases = []
    for sc in a.scenes.split(","):
        frame = cv2.cvtColor(cv2.imread(os.path.join(HERE, "out", "scenes", sc, "key.png")), cv2.COLOR_BGR2RGB)
        truth, vis, side = hs.split(frame, ps.SIDE)
        canvas, _, _ = ps.make_canvas(truth, side, ps.CANVAS_SCALE[True])
        path = os.path.join(cell, f"canvas-{sc}.png")
        cv2.imwrite(path, cv2.cvtColor(canvas, cv2.COLOR_RGB2BGR))
        canvases.append((sc, path))
    encoder = identity_load(a, cfg, cell) if a.identity_encoder else None
    painter = Painter(cfg, os.path.join(cell, "painter-service.log"), lambda m: print(m, flush=True))
    painter.start()
    log_path, out = os.path.join(cell, "painter-load.jsonl"), os.path.join(cell, "painter-load-plate.png")
    steady, n = False, 0
    try:
        while not (stop and os.path.exists(stop)):
            sc, path = canvases[n % len(canvases)]
            t0 = time.time()
            # the live recipe's schedule and reference size, as live/painter.py sends them (left out, the service
            # paints its default 8 steps)
            res = painter._call("/paint", {"canvas": path, "cond": cfg.cond, "seed": a.seed, "out": out,
                                           "schedule": cfg.schedule, "ref_scale": cfg.ref_scale}, timeout=300)
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"plate": n, "scene": sc, "wall_s": round(time.time() - t0, 2), "t": round(t0, 3),
                                    "stages": res["seconds"], "vram": res["vram"], "resident": res["fully_loaded"]}) + "\n")
            n += 1
            if not steady and (encoder is None or encoder.made >= 1):
                print("LOAD STEADY", flush=True)
                steady = True
    finally:
        painter.stop()
        if encoder is not None:
            encoder.stop()
    print(f"painter load done: {n} plates" + (f", {encoder.made} identity reads" if encoder else ""), flush=True)
    return 0


def identity_load(a, cfg, cell):
    """--identity-encoder: the worker's identity encoder reading a.identity_encoder distinct crops into the cell folder,
    queued as its read-ahead is (live/painter_jobs.py), so its service, priority and timeouts are the worker's own."""
    from live.identity_encoder import IdentityEncoder
    crops = sorted(glob.glob(a.identity_crops))[:a.identity_encoder]
    if len(crops) < a.identity_encoder:
        raise SystemExit(f"--identity-encoder {a.identity_encoder}: only {len(crops)} crops match {a.identity_crops}")
    folder = os.path.join(cell, "identity-load")                  # conds/ inside it: nothing read before, nothing cached
    os.makedirs(os.path.join(folder, "conds"), exist_ok=True)
    encoder = IdentityEncoder(folder, cfg.te, cfg.identity_port, lambda m: print(m, flush=True))
    encoder.start()
    encoder.prefetch(crops)
    return encoder


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--service", action="store_true", help="the live worker's resident painter service (phase 3)")
    ap.add_argument("--dit", default=None, help="--service: the DiT file (default: live/painter.py's)")
    ap.add_argument("--cond", default=None, help="--service: the conditioning file (default: live/painter.py's)")
    ap.add_argument("--vram-cap-gb", type=float, default=None, help="--service: the allocator cap")
    ap.add_argument("--service-port", type=int, default=None, help="--service: the service's port")
    ap.add_argument("--gpu-queue", default=None, help="--service: off | layer | block (painter-service.py)")
    ap.add_argument("--gpu-priority", default=None, help="--service: normal | below | idle (painter-service.py)")
    ap.add_argument("--attention", default=None, help="--service: pytorch | ck-int8 (painter-service.py)")
    ap.add_argument("--attn-chunk", type=int, default=None, help="--service: attention kernels of at most N queries")
    ap.add_argument("--identity-encoder", type=int, default=0, metavar="N",
                    help="--service: the CPU identity encoder also reads N distinct crops (12: busy past a 2-min cell)")
    ap.add_argument("--identity-crops", default=os.path.join(HERE, "out", "live", "cache", "*", "identity", "c*", "0.png"),
                    help="--identity-encoder: the crops (a glob; phase 4's first crop of each cluster)")
    ap.add_argument("--variant", default="turbo8-s10-1mp", help="a painter-speed.py variant, e.g. turbo8-s10-1mp")
    ap.add_argument("--best", default="turbo8-s10")
    ap.add_argument("--scenes", default="jjk-pan,jjk-action,jjk-one-side,jjk-edge-move,jjk-still-edge-person")
    ap.add_argument("--port", type=int, default=8190)
    ap.add_argument("--dit-dtype", default="fp8_e4m3fn_fast")
    ap.add_argument("--server-args", default="")
    ap.add_argument("--dit-file", default=ps.DIT)
    ap.add_argument("--te-file", default=ps.TE)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args(argv)
    ps.DIT, ps.TE = a.dit_file, a.te_file                         # the same model files as X2's winning run
    stop = os.environ.get("E1_STOP_FILE")
    cell = os.environ.get("E1_CELL_DIR") or os.path.join(HERE, "out", "e1")
    os.makedirs(cell, exist_ok=True)
    if a.service:
        return service_load(a, stop, cell)
    name, base, mods = ps.parse_variant(a.variant, a.best)
    if base in ps.SKIPPED:
        raise SystemExit(f"{a.variant}: {ps.SKIPPED[base]}")
    scale = ps.CANVAS_SCALE["1mp" in mods]
    plates = []
    for sc in a.scenes.split(","):
        frame = cv2.cvtColor(cv2.imread(os.path.join(HERE, "out", "scenes", sc, "key.png")), cv2.COLOR_BGR2RGB)
        truth, vis, side = hs.split(frame, ps.SIDE)
        canvas, pad, ph = ps.make_canvas(truth, side, scale)
        cname = f"painter-speed-{sc}-{canvas.shape[1]}x{canvas.shape[0]}.png"
        cv2.imwrite(os.path.join(cc.INPUT_DIR, cname), cv2.cvtColor(canvas, cv2.COLOR_RGB2BGR))
        graph, _, steps = ps.painter_graph(base, mods, cname, (canvas.shape[1], canvas.shape[0]), a.seed,
                                           f"painter-speed/e1-load-{sc}", a.dit_dtype)
        plates.append((sc, graph, steps))
    if cc.ping(a.port):
        raise SystemExit(f"port {a.port} already answers; E1's painter needs its own server")
    sargs = ps.server_args(argparse.Namespace(cache="classic", server_args=a.server_args))
    server = cc.start_server(a.port, os.path.join(cell, "painter-comfy-server.log"), sargs)
    log_path = os.path.join(cell, "painter-load.jsonl")
    steady, n = False, 0
    try:
        while not (stop and os.path.exists(stop)):
            sc, graph, steps = plates[n % len(plates)]
            t0 = time.time()
            ps.run_plate(a.port, graph)
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"plate": n, "scene": sc, "steps": steps, "wall_s": round(time.time() - t0, 2),
                                    "t": round(t0, 3)}) + "\n")
            n += 1
            if not steady:
                print("LOAD STEADY", flush=True)
                steady = True
    finally:
        cc.stop_server(server)
    print(f"painter load done: {n} plates", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
