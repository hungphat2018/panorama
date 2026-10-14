"""E1 load generator: keeps the GPU busy from a separate process with calls of a known length, so the performer's pacing can be
measured against kernel length and duty (plans/reports/researcher-261012-1407-gpu-sharing-and-inference-runtime-on-windows.md,
section 5, E1). Every call is timed with CUDA events and synchronized before the next one starts (one call in flight), so the
GPU sees one kernel of about the asked length at a time.

  gemm --ms D   one bf16 matrix multiply (N x N by N x N; one cuBLAS GEMM, normally a single kernel) per call, N sized at the
                start of the run so a call lasts about D ms
  attn --ms D   one torch.nn.functional.scaled_dot_product_attention call (batch 1, 24 heads of 128, bf16), the sequence length
                sized at the start of the run so a call lasts about D ms; the first backend that runs, in the order flash,
                cuDNN, memory-efficient, math, is used and reported (math is several kernels per call)

  --duty P             busy P % of every --duty-period-ms window (default 100: back to back); busy = summed call time
  --seconds S          stop after S seconds of timed load (calibration not counted)
  --stop-file PATH     stop as soon as PATH exists (how the E1 runner ends a cell); checked every 50 ms
  --out-json PATH      summary: calibration, achieved call times, one window per second on the QPC clock (time.perf_counter
                       is QueryPerformanceCounter on Windows: the clock perf1 stamps its logs with)
  --device cpu         dry runs only: the same code on the CPU, timed with perf_counter

Calibration runs at the start of every run (clocks, power and other processes differ between runs; nothing is cached): a warm-up
of --warmup-s seconds back to back at a size guessed from the GPU's throughput, then up to 10 resizes towards the asked time (GEMM
time grows with N^3, attention with L^2), then 15 timed calls at the chosen size. Prints 'LOAD STEADY ...' (flushed) when the timed loop starts, a
line every 5 s (call time p50 / p90 / max, busy share), and 'LOAD DONE ...' at the end.

  python -I -X utf8 gpu-load.py gemm --ms 2 --seconds 150 --out-json load.json
"""
import argparse
import json
import math
import os
import statistics
import sys
import time

STEADY = "LOAD STEADY"


def pct(v, q):
    v = sorted(v)
    return v[min(len(v) - 1, max(0, int(math.ceil(q * len(v))) - 1))] if v else float("nan")


class CallTimer:
    """Times one call: CUDA events around it on the GPU (then waits for it), perf_counter on the CPU."""

    def __init__(self, torch, device):
        self.torch, self.cuda = torch, device == "cuda"
        if self.cuda:
            self.start = torch.cuda.Event(enable_timing=True)
            self.end = torch.cuda.Event(enable_timing=True)

    def ms(self, call):
        if not self.cuda:
            t = time.perf_counter()
            call()
            return (time.perf_counter() - t) * 1e3
        self.start.record()
        call()
        self.end.record()
        self.end.synchronize()  # one call in flight: the next starts only after this one ended
        return self.start.elapsed_time(self.end)


def gemm_call(torch, device, n):
    dtype = torch.bfloat16 if device == "cuda" else torch.float32
    a = torch.randn(n, n, device=device, dtype=dtype)
    b = torch.randn(n, n, device=device, dtype=dtype)
    out = torch.empty(n, n, device=device, dtype=dtype)
    return lambda: torch.matmul(a, b, out=out)


ATTN_HEADS, ATTN_DIM = 24, 128


def attn_backends(torch):
    from torch.nn.attention import SDPBackend
    return [("flash", SDPBackend.FLASH_ATTENTION), ("cudnn", SDPBackend.CUDNN_ATTENTION),
            ("efficient", SDPBackend.EFFICIENT_ATTENTION), ("math", SDPBackend.MATH)]


def attn_call(torch, device, length, backend):
    from torch.nn.attention import sdpa_kernel
    import torch.nn.functional as F
    dtype = torch.bfloat16 if device == "cuda" else torch.float32
    q, k, v = (torch.randn(1, ATTN_HEADS, length, ATTN_DIM, device=device, dtype=dtype) for _ in range(3))

    def call():
        with sdpa_kernel(backend):
            return F.scaled_dot_product_attention(q, k, v)
    return call


def pick_attn_backend(torch, device, length):
    """The first SDPA backend that runs here at this size (flash, cuDNN, memory-efficient, math)."""
    for name, backend in attn_backends(torch):
        try:
            call = attn_call(torch, device, length, backend)
            call()
            if device == "cuda":
                torch.cuda.synchronize()
            return name, backend
        except RuntimeError:
            continue
    raise SystemExit("gpu-load: no scaled_dot_product_attention backend runs on this device")


def calibrate(torch, a, timer):
    """Size so one call lasts about a.ms: returns (size, call, info)."""
    target = a.ms
    cuda = a.device == "cuda"
    # first guess from throughput (bf16 dense GEMM measured at 52.8 TFLOPS on this laptop; attention a little below), so the
    # warm-up already runs near the asked length and the clocks ramp under a load like the one that follows
    if a.mode == "gemm":
        power, step, lo, hi = 1.0 / 3.0, 64, 64, 16384
        size = (target * 1e-3 * (50e12 if cuda else 0.5e12) / 2.0) ** (1.0 / 3.0)
        make = lambda s: gemm_call(torch, a.device, s)
        info = {"what": "bf16 matmul N x N by N x N" if cuda else "fp32 matmul (cpu)"}
    else:
        power, step = 0.5, 64
        size = (target * 1e-3 * (40e12 if cuda else 0.2e12) / (4.0 * ATTN_HEADS * ATTN_DIM)) ** 0.5
        name, backend = pick_attn_backend(torch, a.device, 1024)
        # math builds the whole L x L score matrix per head: keep it under about 2 GB
        lo, hi = 64, (8192 if name == "math" else 65536)
        make = lambda s: attn_call(torch, a.device, s, backend)
        info = {"what": f"sdpa batch 1, {ATTN_HEADS} heads x {ATTN_DIM}, bf16" if cuda else "sdpa fp32 (cpu)", "backend": name}
    size = max(lo, min(hi, int(round(size / step)) * step))
    call = make(size)
    t_end = time.perf_counter() + a.warmup_s
    while time.perf_counter() < t_end:  # clocks up, libraries' heuristics settled
        timer.ms(call)
    tried = []
    best = None
    for _ in range(10):
        med = statistics.median(timer.ms(call) for _ in range(7))
        tried.append([size, round(med, 4)])
        if best is None or abs(med / target - 1) < abs(best[1] / target - 1):
            best = (size, med)
        if abs(med / target - 1) < 0.05:
            break
        new = int(round(size * (target / med) ** power / step)) * step
        new = max(lo, min(hi, new))
        if new == size or new in (s for s, _ in tried):
            break
        size = new
        del call
        call = make(size)
    if best[0] != size:
        size = best[0]
        del call
        call = make(size)
    check = [timer.ms(call) for _ in range(15)]
    info.update({"size": size, "size_is": "N" if a.mode == "gemm" else "sequence length", "tried": tried,
                 "check_ms_p50": round(statistics.median(check), 4), "check_ms_max": round(max(check), 4)})
    return size, call, info


def windows_of(t_calls, ms_calls, t0, width=1.0):
    """One record per second of load: QPC start, calls, call ms p50 / p90 / max, busy share."""
    out, i, n = [], 0, len(t_calls)
    if not n:
        return out
    k = 0
    while i < n:
        w0 = t0 + k * width
        j = i
        while j < n and t_calls[j] < w0 + width:
            j += 1
        sel = ms_calls[i:j]
        if sel:
            out.append({"t_qpc": round(w0, 4), "calls": len(sel), "p50": round(pct(sel, .5), 4), "p90": round(pct(sel, .9), 4),
                        "max": round(max(sel), 4), "busy": round(sum(sel) / (width * 1e3), 4)})
        i = j
        k += 1
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=("gemm", "attn"))
    ap.add_argument("--ms", type=float, required=True, help="target time of one call (ms)")
    ap.add_argument("--duty", type=float, default=100.0, help="busy percent of every window (default 100)")
    ap.add_argument("--duty-period-ms", type=float, default=100.0, help="window of the duty cycle (default 100 ms)")
    ap.add_argument("--seconds", type=float, default=60.0)
    ap.add_argument("--stop-file", type=str, default=None)
    ap.add_argument("--out-json", type=str, default=None)
    ap.add_argument("--warmup-s", type=float, default=2.0)
    ap.add_argument("--device", choices=("cuda", "cpu"), default="cuda", help="cpu: dry runs only")
    a = ap.parse_args()
    if not (a.ms > 0 and 0 < a.duty <= 100 and a.duty_period_ms > 0 and a.seconds > 0):
        ap.error("--ms, --duty-period-ms and --seconds must be positive, --duty in (0, 100]")

    import torch
    if a.device == "cuda" and not torch.cuda.is_available():
        raise SystemExit("gpu-load: CUDA is not available")
    timer = CallTimer(torch, a.device)
    t_cal = time.perf_counter()
    size, call, cal = calibrate(torch, a, timer)
    cal["seconds"] = round(time.perf_counter() - t_cal, 2)
    gpu = torch.cuda.get_device_name(0) if a.device == "cuda" else "cpu"
    print(f"gpu-load: {a.mode} {cal['what']}, {cal['size_is']} {size}: {cal['check_ms_p50']:.3f} ms per call "
          f"(asked {a.ms} ms; calibrated in {cal['seconds']} s, tried {cal['tried']}){', backend ' + cal['backend'] if 'backend' in cal else ''}; "
          f"{gpu}, torch {torch.__version__}", flush=True)

    busy_ms = a.duty_period_ms * a.duty / 100.0
    t0 = time.perf_counter()
    print(f"{STEADY} t_qpc={t0:.6f} mode={a.mode} ms={a.ms} size={size} duty={a.duty}", flush=True)
    t_end = t0 + a.seconds
    t_calls, ms_calls = [], []
    next_print, next_stop_check = t0 + 5.0, t0
    window_start, busy_in_window = t0, 0.0
    why = "seconds"
    try:
        while True:
            now = time.perf_counter()
            if now >= t_end:
                break
            if a.stop_file and now >= next_stop_check:
                next_stop_check = now + 0.05
                if os.path.exists(a.stop_file):
                    why = "stop file"
                    break
            if a.duty < 100.0 and busy_in_window >= busy_ms:
                # idle for the rest of the window (in short sleeps, so the stop file is still seen), then a new window; one
                # that a long call overran starts now instead of in the past
                window_end = window_start + a.duty_period_ms / 1e3
                rest = window_end - time.perf_counter()
                if rest > 0:
                    time.sleep(min(rest, 0.05))
                    continue
                window_start = max(window_end, time.perf_counter())
                busy_in_window = 0.0
                continue
            t_calls.append(now)
            ms = timer.ms(call)
            ms_calls.append(ms)
            busy_in_window += ms
            if now >= next_print:
                next_print += 5.0
                last = [m for t, m in zip(t_calls[-20000:], ms_calls[-20000:]) if t >= now - 5.0]
                print(f"load t={now - t0:6.1f}s calls={len(last)} call_ms p50={pct(last, .5):.3f} p90={pct(last, .9):.3f} "
                      f"max={max(last):.3f} busy={sum(last) / 5e3:.0%}", flush=True)
    except KeyboardInterrupt:
        why = "interrupted"
    t_stop = time.perf_counter()
    span = max(1e-9, t_stop - t0)
    summary = {
        "schema": "panorama.e1.gpu-load/1", "mode": a.mode, "asked_ms": a.ms, "duty": a.duty, "duty_period_ms": a.duty_period_ms,
        "device": a.device, "gpu": gpu, "torch": torch.__version__, "calibration": cal, "t_qpc_start": round(t0, 6),
        "t_qpc_stop": round(t_stop, 6), "stopped_by": why, "calls": len(ms_calls),
        "call_ms": {"p50": round(pct(ms_calls, .5), 4), "p90": round(pct(ms_calls, .9), 4), "p99": round(pct(ms_calls, .99), 4),
                    "max": round(max(ms_calls), 4) if ms_calls else None},
        "busy_share": round(sum(ms_calls) / (span * 1e3), 4),
        "windows": windows_of(t_calls, ms_calls, t0),
    }
    if a.out_json:
        with open(a.out_json, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=1)
    print(f"LOAD DONE ({why}) {len(ms_calls)} calls in {span:.1f} s, call_ms p50 {summary['call_ms']['p50']} "
          f"max {summary['call_ms']['max']}, busy {summary['busy_share']:.0%}", flush=True)
    return 130 if why == "interrupted" else 0


if __name__ == "__main__":
    sys.exit(main())
