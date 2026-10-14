"""Painter bench (phase 3): the resident painter (painter-service.py) on the hidden-sides scenes, scored like X2
(painter-speed.py): each scene's key frame split at s = 0.5, the visible centre on a ~1 MP grey canvas (1344x768), the
plate scaled back, the real centre pasted, near / far / seam against the hidden truth.

A variant = a DiT x a read (what the encoder reads):
  DiTs   --dits NAME=FILE[+loras],...   +loras bakes X2's LoRAs (Turbo rank-178 1.0, outpaint v2 1.0) in at load; a file
         from painter-quantize.py has them merged. One painter service per DiT, the reads painted one after the other.
  reads  read-full        the canvas itself (X2's recipe)
         read-t<N>        a thumbnail of the canvas of about N x N pixels (TextEncodeQwenImage21's rounding); the DiT still
                          gets the full canvas as reference: only the encoder's view shrinks
         proxy-grey       one all-grey canvas, encoded once for every scene (the encoder leaves the live path)
         proxy-<scene>    that scene's canvas, encoded once, used for every scene
         caption-full     the canvas, with the 25-step plate's own prompt: the instruction + " Scene: " + the scene's
                          caption (out/runs/captions.json, written by comfy-plates.py from the visible picture)
Per plate also the mean change of the sides against the reference variant's plate of the same scene and seed
(--ref DIT/READ, default the first of each): "delta" isolates what the DiT or the read changes.

The encoder (INT8, ~9.4 GB) and a painter never share the GPU: the encoder encodes every read first (cached per run, or
taken from --from-run), then each painter paints. Rests (user rule): --rest before each service and each variant,
--rest-plate between plates; GPU temperature logged at each start.

    python -I -X utf8 painter-bench.py [--dits int8=qwen_image_2.1_int8_convrot.safetensors+loras,...]
        [--reads read-full,read-t256,...] [--ref int8/read-full] [--scenes a,b] [--kv-cache gpu-int8]
        [--vae-tile TILE:OVERLAP] [--vram-cap-gb G] [--te FILE] [--tag T] [--rest 180] [--rest-plate 20]
        [--port 8192] [--encode-only] [--from-run DIR]

Writes out/runs/painter-bench/<stamp>-<tag>/: inputs/, conds/, plates/<dit>/<read>/<scene>.png, sheets/<scene>.png,
plates.jsonl, summary.json, gpu.csv, service-*.log.

    python -I -X utf8 painter-bench.py --compare RUN[:DIT/READ],RUN[:DIT/READ],... [--tag T]
merges finished runs (each RUN a run folder, its first variant unless DIT/READ is given; the first one is the reference):
one table (scores, seconds, change against the reference's plate of the same scene) and per-scene sheets, written to
out/runs/painter-bench/compare-<stamp>-<tag>/.
"""
import argparse
import importlib.util
import json
import math
import os
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import hidden_sides as hs  # noqa: E402


def load_script(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), os.path.join(HERE, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ps = load_script("painter-speed")                                  # make_canvas, GpuMonitor, rest, SIDE, X2's names
plates_mod = ps.plates                                            # comfy-plates.py: INSTRUCTION, GREY
REPO = os.path.dirname(os.path.dirname(HERE))
COMFY_PY = os.path.join(REPO, "tools", "ComfyUI_windows_portable", "python_embeded", "python.exe")
SERVICE = os.path.join(HERE, "painter-service.py")
SCALE = 0.7                                                       # X2's ~1 MP canvas: 1344x768 (--canvas-scale)
X2_LORAS = [f"{ps.TURBO}:1.0", f"{ps.OUTPAINT}:1.0"]
DEFAULT_DITS = "int8=qwen_image_2.1_int8_convrot.safetensors+loras"


# ---------------------------------------------------------------- the service (a child process)

class ServiceProc:
    def __init__(self, port, args, log_path):
        self.port, self.url = port, f"http://127.0.0.1:{port}"
        if self._ping():
            raise SystemExit(f"something already answers on port {port}; stop it or pass --port")
        cmd = [COMFY_PY, "-s", SERVICE, "--port", str(port), "--parent-pid", str(os.getpid()),
               "--log", log_path.replace(".log", "-comfy.log"), *args]
        self.log = open(log_path, "w", encoding="utf-8")
        self.proc = subprocess.Popen(cmd, cwd=REPO, stdout=self.log, stderr=subprocess.STDOUT)
        for _ in range(1200):
            if self._ping():
                return
            if self.proc.poll() is not None:
                raise SystemExit(f"painter service exited with {self.proc.returncode}; see {log_path}")
            time.sleep(0.5)
        self.stop()
        raise SystemExit("painter service did not answer within 10 minutes")

    def _ping(self):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{self.port}/status", timeout=2).read()
            return True
        except (urllib.error.URLError, OSError):
            return False

    def call(self, path, payload=None, timeout=600):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self.url + path, data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"{path}: {e.code} {e.read()[:2000]!r}") from None

    def stop(self):
        if self.proc.poll() is None:
            try:
                self.call("/quit", {}, timeout=10)
                self.proc.wait(timeout=60)
            except (RuntimeError, OSError, subprocess.TimeoutExpired):
                self.proc.terminate()
                self.proc.wait(timeout=30)
        self.log.close()


# ---------------------------------------------------------------- inputs

def thumb_size(w, h, n):
    """TextEncodeQwenImage21's resize: about n x n pixels, multiples of 32, aspect kept."""
    ratio = w / h
    return max(32, round(math.sqrt(n * n * ratio) / 32) * 32), max(32, round(math.sqrt(n * n / ratio) / 32) * 32)


def write_png(path, rgb):
    cv2.imwrite(path, cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))


def read_png(path):
    return cv2.cvtColor(cv2.imread(path), cv2.COLOR_BGR2RGB)


def prepare(scenes, scenes_dir, inputs, scale=SCALE):
    """Per scene: truth, visible centre, side, canvas (1344x768), its pad rows and picture rows; the canvas PNG."""
    out = {}
    for sc in scenes:
        key = os.path.join(scenes_dir, sc, "key.png")
        if os.path.isfile(key):
            frame = read_png(key)
        else:
            frames = hs.read_frames(os.path.join(scenes_dir, sc, "clip.mkv"))
            frame = frames[len(frames) // 2]
        truth, vis, side = hs.split(frame, ps.SIDE)
        canvas, pad, ph = ps.make_canvas(truth, side, scale)
        path = os.path.join(inputs, f"{sc}-canvas.png")
        write_png(path, canvas)
        out[sc] = {"truth": truth, "vis": vis, "side": side, "canvas": canvas, "pad": pad, "ph": ph, "canvas_path": path}
    return out


def sides_of(p, band=0):
    """--sides-only: the canvas columns to paint, multiples of 16: each grey side in full, plus `band` px of picture."""
    w = p["canvas"].shape[1]
    side_s = int(round(p["side"] * w / p["truth"].shape[1]))      # ps.make_canvas's side width on the canvas
    left = -(-(side_s + band) // 16) * 16
    return [[0, left], [w - left, w]]


def read_image(read, sc, prep, inputs):
    """(encoder image path, cond key) of one read for one scene. A proxy has one key for every scene."""
    canvas = prep[sc]["canvas"]
    h, w = canvas.shape[:2]
    if read == "read-full":
        return prep[sc]["canvas_path"], f"full-{sc}"
    if read == "caption-full":
        return prep[sc]["canvas_path"], f"caption-full-{sc}"
    if read.startswith("read-t"):
        n = int(read[len("read-t"):])
        tw, th = thumb_size(w, h, n)
        path = os.path.join(inputs, f"{sc}-thumb{n}-{tw}x{th}.png")
        if not os.path.isfile(path):
            write_png(path, cv2.resize(canvas, (tw, th), interpolation=cv2.INTER_AREA))
        return path, f"t{n}-{sc}"
    if read == "proxy-grey":
        path = os.path.join(inputs, f"proxy-grey-{w}x{h}.png")
        if not os.path.isfile(path):
            write_png(path, np.full((h, w, 3), plates_mod.GREY, np.uint8))
        return path, "proxy-grey"
    if read.startswith("proxy-"):
        other = read[len("proxy-"):]
        if other not in prep:
            raise SystemExit(f"{read}: scene {other} is not in this run's scenes")
        return prep[other]["canvas_path"], f"full-{other}"
    raise SystemExit(f"unknown read {read}")


def read_prompt(key):
    """The encoder's prompt for a cond key: the outpaint instruction; for caption-full, comfy-plates.py's prompt with the
    scene's caption (the 25-step plate's own)."""
    if not key.startswith("caption-full-"):
        return plates_mod.INSTRUCTION
    sc = key[len("caption-full-"):]
    captions = json.load(open(os.path.join(HERE, "out", "runs", "captions.json"), encoding="utf-8"))
    if sc not in captions:
        raise SystemExit(f"caption-full: no caption for {sc} in out/runs/captions.json")
    return plates_mod.INSTRUCTION + " Scene: " + captions[sc]


def parse_dits(spec):
    out = []
    for item in spec.split(","):
        name, _, rest = item.strip().partition("=")
        if not rest:
            raise SystemExit(f"--dits: {item!r} is not NAME=FILE[+loras]")
        loras = rest.endswith("+loras")
        out.append((name, rest[:-len("+loras")] if loras else rest, X2_LORAS if loras else []))
    return out


# ---------------------------------------------------------------- sheets

def write_sheet(sc, prep, combos, plates_dir, rows, path):
    truth, side = prep[sc]["truth"], prep[sc]["side"]
    tiles, labels = [hs.mark_seams(truth, side)], [f"{sc}  TRUTH (hidden sides)"]
    for dit, read in combos:
        p = os.path.join(plates_dir, dit, read, f"{sc}.png")
        r = rows.get((dit, read, sc))
        if r is None or not os.path.isfile(p):
            continue
        tiles.append(hs.mark_seams(read_png(p), side))
        d = f"  delta {r['delta']}" if r.get("delta") is not None else ""
        labels.append(f"{dit}/{read}  {r['seconds']['total']:.1f} s  near {r['err_near']}  far {r['err_far']}  "
                      f"seam x{r['seam_step']}{d}")
    cells = []
    for img, label in zip(tiles, labels):
        im = cv2.resize(img, (960, int(round(img.shape[0] * 960 / img.shape[1]))), interpolation=cv2.INTER_AREA)
        strip = np.zeros((22, 960, 3), np.uint8)
        cv2.putText(strip, label, (6, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1, cv2.LINE_AA)
        cells.append(np.vstack([strip, im]))
    if len(cells) % 2:
        cells.append(np.zeros_like(cells[0]))
    gap = np.zeros((cells[0].shape[0], 4, 3), np.uint8)
    write_png(path, np.vstack([np.hstack([cells[i], gap, cells[i + 1]]) for i in range(0, len(cells), 2)]))


# ---------------------------------------------------------------- main

def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dits", default=DEFAULT_DITS)
    ap.add_argument("--reads", default="read-full")
    ap.add_argument("--ref", default="", help="DIT/READ the deltas are taken against (default: the first of each)")
    ap.add_argument("--scenes", default="")
    ap.add_argument("--te", default="qwen3vl_8b_int8_convrot.safetensors")
    ap.add_argument("--kv-cache", default="gpu-int8")
    ap.add_argument("--attention", default="pytorch", help="pytorch | ck-int8 (painter-service.py --attention)")
    ap.add_argument("--schedule", default="turbo8", help="turbo8 | turbo8lo | turbo6 | turbo5 | turbo4 (painter_core.SCHEDULES)")
    ap.add_argument("--sampler", default="euler", help="ComfyUI sampler: euler (the Turbo card's), res_multistep, ...")
    ap.add_argument("--attn-chunk", type=int, default=0, help="painter-service.py --attn-chunk (0: whole)")
    ap.add_argument("--gpu-queue", default="off", help="painter-service.py --gpu-queue (off | layer | block)")
    ap.add_argument("--canvas-scale", type=float, default=SCALE, help="0.7: 1344x768 (~1 MP); 0.6: 1152x640")
    ap.add_argument("--ref-scale", type=float, default=1.0, help="reference latent size against the canvas")
    ap.add_argument("--sides-only", action="store_true", help="paint only the grey sides (painter_core sides): the "
                    "picture stays the reference, not repainted")
    ap.add_argument("--sides-band", type=int, default=0, help="--sides-only: also paint this many canvas px of the "
                    "picture next to each seam (the composite puts the real picture back)")
    ap.add_argument("--vae-tile", default="")
    ap.add_argument("--warmup", default="", help="painter-service.py --warmup WxH (the live painter: 1856x576)")
    ap.add_argument("--vram-cap-gb", type=float, default=10.0, help="the service's allocator cap: an overrun is an "
                    "out-of-memory error, never Windows' slow spill into shared system memory")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--tag", default="")
    ap.add_argument("--rest", type=float, default=180.0)
    ap.add_argument("--rest-plate", type=float, default=20.0)
    ap.add_argument("--port", type=int, default=8192)
    ap.add_argument("--encode-only", action="store_true")
    ap.add_argument("--from-run", default="", help="reuse this run's conds (its encoder reads)")
    ap.add_argument("--scenes-dir", default=os.path.join(HERE, "out", "scenes"))
    ap.add_argument("--runs-dir", default=os.path.join(HERE, "out", "runs", "painter-bench"))
    ap.add_argument("--compare", default="", help="RUN[:DIT/READ],...: merge finished runs (the first is the reference)")
    a = ap.parse_args(argv)
    if a.compare:
        return compare(a)
    dits = parse_dits(a.dits)
    reads = [v.strip() for v in a.reads.split(",") if v.strip()]
    combos = [(d[0], r) for d in dits for r in reads]
    ref = tuple(a.ref.split("/", 1)) if a.ref else combos[0]
    scenes = [d for d in sorted(os.listdir(a.scenes_dir)) if os.path.isfile(os.path.join(a.scenes_dir, d, "clip.mkv"))]
    if a.scenes:
        wanted = a.scenes.split(",")
        missing = [s for s in wanted if s not in scenes]
        if missing:
            raise SystemExit(f"unknown scenes {missing}")
        scenes = [s for s in scenes if s in wanted]

    stamp = time.strftime("%Y%m%d-%H%M%S") + (f"-{a.tag}" if a.tag else "")
    run = os.path.join(a.runs_dir, stamp)
    inputs, plates_dir = os.path.join(run, "inputs"), os.path.join(run, "plates")
    conds = os.path.join(a.from_run, "conds") if a.from_run else os.path.join(run, "conds")
    for d in (inputs, conds, plates_dir, os.path.join(run, "sheets")):
        os.makedirs(d, exist_ok=True)
    prep = prepare(scenes, a.scenes_dir, inputs, a.canvas_scale)
    jobs = {(r, sc): read_image(r, sc, prep, inputs) for r in reads for sc in scenes}
    gpu = ps.GpuMonitor(os.path.join(run, "gpu.csv"))
    gpu.start()
    log_path = os.path.join(run, "plates.jsonl")

    def log(event):
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(dict(event, time=time.strftime("%H:%M:%S")), ensure_ascii=False) + "\n")
    log({"event": "start", "args": vars(a), "dits": dits, "reads": reads, "scenes": scenes, "gpu": gpu.now()})
    print(f"run {run}\ndits {[d[0] for d in dits]}  reads {reads}\nscenes {scenes}", flush=True)
    rows = {}
    try:
        # ---- encode phase: every distinct read once
        todo = {}
        for (r, sc), (img, key) in jobs.items():
            todo.setdefault(key, img)
        missing = [k for k in todo if not os.path.isfile(os.path.join(conds, f"{k}.pt"))]
        if missing:
            ps.rest(a.rest, "before the encoder", gpu, log)
            print(f"\n== encoder {a.te}: {len(missing)} reads; GPU {gpu.now()}", flush=True)
            svc = ServiceProc(a.port, ["--te", a.te], os.path.join(run, "service-encode.log"))
            try:
                for key in missing:
                    r = svc.call("/encode", {"prompt": read_prompt(key), "image": todo[key],
                                             "out": os.path.join(conds, f"{key}.pt")})
                    print(f"  encode {key:28s} {r['seconds']:.2f} s, {r['context_tokens']} tokens, slots {r['image_slots']}",
                          flush=True)
                    log(dict(r, event="encode", key=key, image=todo[key]))
            finally:
                svc.stop()
        if a.encode_only:
            return 0

        # ---- paint phase: one service per DiT
        for dit, file, loras in dits:
            ps.rest(a.rest, f"before the painter {dit}", gpu, log)
            args = ["--dit", file, "--kv-cache", a.kv_cache, "--vram-cap-gb", str(a.vram_cap_gb), "--attention", a.attention,
                    "--attn-chunk", str(a.attn_chunk), "--gpu-queue", a.gpu_queue]
            if a.vae_tile:
                args += ["--vae-tile", a.vae_tile]
            if a.warmup:
                args += ["--warmup", a.warmup]
            for spec in loras:
                args += ["--lora", spec]
            print(f"\n== painter {dit}: {file} + {loras}; GPU {gpu.now()}", flush=True)
            t0 = time.time()
            svc = ServiceProc(a.port, args, os.path.join(run, f"service-paint-{dit}.log"))
            try:
                status = svc.call("/status")
                print(f"   ready in {time.time() - t0:.0f} s: {status}", flush=True)
                log({"event": "painter_ready", "dit": dit, "seconds": round(time.time() - t0, 1), "status": status})
                for ri, read in enumerate(reads):
                    if ri:
                        ps.rest(a.rest, f"before {dit}/{read}", gpu, log)
                    snap = gpu.now()
                    print(f"\n== {dit}/{read}: GPU {snap.get('temp_c', '?')} C, {snap.get('mem_mb', '?')} MB used", flush=True)
                    log({"event": "variant_start", "dit": dit, "read": read, "gpu": snap})
                    odir = os.path.join(plates_dir, dit, read)
                    os.makedirs(odir, exist_ok=True)
                    for si, sc in enumerate(scenes):
                        if si:
                            ps.rest(a.rest_plate, "between plates", gpu, log)
                        p = prep[sc]
                        raw_path = os.path.join(odir, f"{sc}-raw.png")
                        t1 = time.time()
                        res = svc.call("/paint", {"canvas": p["canvas_path"], "seed": a.seed, "out": raw_path,
                                                  "cond": os.path.join(conds, f"{jobs[(read, sc)][1]}.pt"),
                                                  "schedule": a.schedule, "ref_scale": a.ref_scale, "sampler": a.sampler,
                                                  "sides": sides_of(p, a.sides_band) if a.sides_only else None})
                        t2 = time.time()
                        raw = read_png(raw_path)[p["pad"]:p["pad"] + p["ph"]]
                        big = cv2.resize(raw, (p["truth"].shape[1], p["truth"].shape[0]), interpolation=cv2.INTER_LANCZOS4)
                        big[:, p["side"]:p["side"] + p["vis"].shape[1]] = p["vis"]
                        write_png(os.path.join(odir, f"{sc}.png"), big)
                        row = dict(hs.score([p["truth"]], [big], p["side"]), dit=dit, read=read, scene=sc,
                                   seconds=res["seconds"], wall_s=round(t2 - t1, 2), vram=res["vram"],
                                   fully_loaded=res["fully_loaded"], **gpu.stats(t1, t2))
                        rows[(dit, read, sc)] = row
                        log(dict(row, event="plate"))
                        s = res["seconds"]
                        print(f"  {dit}/{read:12s} {sc:22s} {s['total']:6.2f} s (enc {s['vae_encode']:.2f} sample "
                              f"{s['sample']:.2f} dec {s['vae_decode']:.2f}) peak MB {s.get('peak_mb')}  near {row['err_near']} "
                              f"far {row['err_far']} seam x{row['seam_step']}  dev {row.get('peak_vram_mb', '?')} MB  "
                              f"{row.get('temp_c_max', '?')} C  resident {res['fully_loaded']}", flush=True)
            finally:
                svc.stop()
    finally:
        gpu.stop()
        finish(a, run, prep, scenes, combos, ref, plates_dir, rows)
    return 0


def finish(a, run, prep, scenes, combos, ref, plates_dir, rows):
    """Deltas against the reference variant, the summary and the sheets (also after an interrupted run)."""
    for (dit, read, sc), row in rows.items():
        base = os.path.join(plates_dir, ref[0], ref[1], f"{sc}.png")
        if (dit, read) != ref and os.path.isfile(base):
            side = prep[sc]["side"]
            x = cv2.imread(os.path.join(plates_dir, dit, read, f"{sc}.png")).astype(np.float32)
            y = cv2.imread(base).astype(np.float32)
            cols = np.r_[0:side, x.shape[1] - side:x.shape[1]]
            row["delta"] = round(float(np.abs(x[:, cols] - y[:, cols]).mean()), 2)
    ref25 = {}
    klein = os.path.join(HERE, "out", "runs", "scores-klein.json")
    if os.path.isfile(klein):
        ref25 = {sc: v[f"s{ps.SIDE}"] for sc, v in json.load(open(klein, encoding="utf-8")).get(ps.REFERENCE, {}).items()
                 if f"s{ps.SIDE}" in v}
    summary = {}
    for dit, read in combos:
        rs = [rows[(dit, read, sc)] for sc in scenes if (dit, read, sc) in rows]
        if not rs:
            continue
        warm = rs[1:] or rs

        def mean(k):
            vals = [r[k] for r in rs if r.get(k) is not None]
            return round(float(np.mean(vals)), 2) if vals else None
        summary[f"{dit}/{read}"] = {
            "plates": len(rs), "near": mean("err_near"), "far": mean("err_far"), "seam": mean("seam_step"),
            "delta": mean("delta"), "warm_total_s_median": round(statistics.median(r["seconds"]["total"] for r in warm), 2),
            "warm_sample_s_median": round(statistics.median(r["seconds"]["sample"] for r in warm), 2),
            "warm_vae_s_median": round(statistics.median(r["seconds"]["vae_encode"] + r["seconds"]["vae_decode"] for r in warm), 2),
            "peak_mb_by_stage": {k: max(r["seconds"].get("peak_mb", {}).get(k, 0) for r in rs)
                                 for k in ("vae_encode", "sample", "vae_decode")},
            "allocated_mb": max(r["vram"].get("allocated_mb", 0) for r in rs),
            "max_reserved_mb": max(r["vram"].get("max_reserved_mb", 0) for r in rs),
            "device_peak_mb": max((r.get("peak_vram_mb") or 0) for r in rs),
            "always_resident": all(r["fully_loaded"] for r in rs)}
    refs = [ref25[sc] for sc in scenes if sc in ref25]
    summary["ref25"] = ({k: round(float(np.mean([x[k] for x in refs])), 2) for k in ("err_near", "err_far", "seam_step")}
                        if refs else {})
    hs.save_json({"run": run, "args": vars(a), "ref": "/".join(ref), "summary": summary,
                  "rows": [dict(r) for r in rows.values()]}, os.path.join(run, "summary.json"))
    for sc in scenes:
        if any((d, r, sc) in rows for d, r in combos):
            write_sheet(sc, prep, combos, plates_dir, rows, os.path.join(run, "sheets", f"{sc}.png"))
    print(f"\n{'variant':28s} {'near':>6s} {'far':>6s} {'seam':>5s} {'delta':>6s} {'warm s':>7s} {'sample':>7s} "
          f"{'vae':>5s} {'alloc MB':>8s} {'peaks MB (enc/sample/dec)':>26s} resident")
    for v, s in summary.items():
        if v == "ref25":
            continue
        pk = s["peak_mb_by_stage"]
        print(f"{v:28s} {s['near'] or 0:6.2f} {s['far'] or 0:6.2f} {s['seam'] or 0:5.2f} {s['delta'] or 0:6.2f} "
              f"{s['warm_total_s_median']:7.2f} {s['warm_sample_s_median']:7.2f} {s['warm_vae_s_median']:5.2f} "
              f"{s['allocated_mb']:8d} {pk['vae_encode']:>8d}/{pk['sample']:>6d}/{pk['vae_decode']:>6d}     "
              f"{s['always_resident']}")
    print(f"25-step plate (same scenes): {summary['ref25']}\nsheets {os.path.join(run, 'sheets')}")


def compare(a):
    """One table and per-scene sheets over finished runs; changes against the first entry's plates."""
    entries = []
    for item in a.compare.split(","):
        run, _, combo = item.strip().partition(":")
        summ = json.load(open(os.path.join(run, "summary.json"), encoding="utf-8"))
        rows = summ["rows"]
        if not rows:                                               # a run that painted nothing (it failed): left out
            print(f"compare: {run} has no plates, left out")
            continue
        if combo:
            dit, read = combo.split("/", 1)
        else:
            dit, read = rows[0]["dit"], rows[0]["read"]
        tag = os.path.basename(os.path.normpath(run)).split("-", 2)[-1]
        name = f"{tag}:{dit}/{read}"
        by_scene = {r["scene"]: r for r in rows if r["dit"] == dit and r["read"] == read}
        entries.append((name, os.path.join(run, "plates", dit, read), by_scene, summ.get("args", {})))
    ref_name, ref_dir, ref_rows, _ = entries[0]
    scenes = [sc for sc in ref_rows if all(sc in e[2] for e in entries)]
    out = os.path.join(a.runs_dir, f"compare-{time.strftime('%Y%m%d-%H%M%S')}" + (f"-{a.tag}" if a.tag else ""))
    os.makedirs(os.path.join(out, "sheets"), exist_ok=True)
    table = {}
    for name, pdir, rows, args in entries:
        deltas = []
        for sc in scenes:
            x = cv2.imread(os.path.join(pdir, f"{sc}.png")).astype(np.float32)
            y = cv2.imread(os.path.join(ref_dir, f"{sc}.png")).astype(np.float32)
            side = x.shape[1] // 4                                 # s = 0.5: a quarter of the width on each side
            cols = np.r_[0:side, x.shape[1] - side:x.shape[1]]
            deltas.append(float(np.abs(x[:, cols] - y[:, cols]).mean()))
        rs = [rows[sc] for sc in scenes]
        table[name] = {"near": round(float(np.mean([r["err_near"] for r in rs])), 2),
                       "far": round(float(np.mean([r["err_far"] for r in rs])), 2),
                       "seam": round(float(np.mean([r["seam_step"] for r in rs])), 2),
                       "seam_median": round(float(np.median([r["seam_step"] for r in rs])), 2),
                       "delta_vs_ref": round(float(np.mean(deltas)), 2),
                       "warm_total_s": round(float(np.median([r["seconds"]["total"] for r in rs[1:] or rs])), 2),
                       "warm_sample_s": round(float(np.median([r["seconds"]["sample"] for r in rs[1:] or rs])), 2),
                       "settings": {k: args.get(k) for k in ("attention", "schedule", "sampler", "canvas_scale", "ref_scale", "sides_only", "sides_band", "dits")}}
    for sc in scenes:
        truth = cv2.cvtColor(cv2.imread(os.path.join(a.scenes_dir, sc, "key.png")), cv2.COLOR_BGR2RGB)
        side = truth.shape[1] // 4
        tiles, labels = [hs.mark_seams(truth, side)], [f"{sc}  TRUTH (hidden sides)"]
        for name, pdir, rows, _ in entries:
            r = rows[sc]
            tiles.append(hs.mark_seams(read_png(os.path.join(pdir, f"{sc}.png")), side))
            labels.append(f"{name}  {r['seconds']['total']:.1f} s  near {r['err_near']}  far {r['err_far']}  seam x{r['seam_step']}")
        cells = []
        for img, label in zip(tiles, labels):
            im = cv2.resize(img, (960, int(round(img.shape[0] * 960 / img.shape[1]))), interpolation=cv2.INTER_AREA)
            strip = np.zeros((22, 960, 3), np.uint8)
            cv2.putText(strip, label, (6, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1, cv2.LINE_AA)
            cells.append(np.vstack([strip, im]))
        if len(cells) % 2:
            cells.append(np.zeros_like(cells[0]))
        gap = np.zeros((cells[0].shape[0], 4, 3), np.uint8)
        write_png(os.path.join(out, "sheets", f"{sc}.png"),
                  np.vstack([np.hstack([cells[i], gap, cells[i + 1]]) for i in range(0, len(cells), 2)]))
    hs.save_json({"reference": ref_name, "scenes": scenes, "table": table}, os.path.join(out, "compare.json"))
    print(f"{'variant':48s} {'near':>6s} {'far':>6s} {'seam':>5s} {'seam~':>6s} {'delta':>6s} {'warm s':>7s} {'sample':>7s}")
    for name, t in table.items():
        print(f"{name:48s} {t['near']:6.2f} {t['far']:6.2f} {t['seam']:5.2f} {t['seam_median']:6.2f} {t['delta_vs_ref']:6.2f} "
              f"{t['warm_total_s']:7.2f} {t['warm_sample_s']:7.2f}")
    print(f"sheets {os.path.join(out, 'sheets')}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
