"""Collect the perf0 result files into one table.

    python -I summarize-results.py RESULTS_DIR > results-summary.md

Reads <id>.json (player result), <id>-load.csv.summary.json (GPU/CPU sampler) and the
render-bench/*.json files, prints Markdown tables.
"""
import glob
import json
import os
import sys


def load(path):
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def f(x, nd=2):
    return "-" if x is None else f"{x:.{nd}f}"


def load_summary(res_dir, run_id):
    d = load(os.path.join(res_dir, run_id + "-load.csv.summary.json"))
    if not d:
        return {}
    return {k: v for k, v in d.get("columns", {}).items()}


def playback_rows(res_dir):
    rows = []
    for p in sorted(glob.glob(os.path.join(res_dir, "*.json"))):
        name = os.path.basename(p)
        if name.endswith(".summary.json") or name.startswith("media-"):
            continue
        d = load(p)
        if not d or "timings_ms" not in d:
            continue
        rid = d["run_id"]
        t = d["timings_ms"]
        c = d["counts"]
        pr = d["process"]
        cfg = d["config"]
        ld = load_summary(res_dir, rid)
        rows.append({
            "id": rid,
            "status": d["status"],
            "backend": cfg["backend"],
            "decode": cfg["decode"],
            "preset": cfg["preset"],
            "worst": cfg["worst_case_no_frame_cache"],
            "secs": d["playback"]["measured_s"],
            "first_frame_ms": d["first_frame_ms"]["from_process_creation"],
            "render_p50": t["render_cpu_steady"]["p50"],
            "render_p99": t["render_cpu_steady"]["p99"],
            "render_max": t["render_cpu_steady"]["max"],
            "new_p99": t["render_cpu_steady_new_film_frame"]["p99"],
            "gpu_p50": t["gpu_pass_sum_steady"]["p50"],
            "gpu_p99": t["gpu_pass_sum_steady"]["p99"],
            "iv_p50": t["present_interval_steady"]["p50"],
            "iv_p99": t["present_interval_steady"]["p99"],
            "iv_max": t["present_interval_steady"]["max"],
            "late15": c["late_refreshes_over_1p5x_steady"],
            "late25": c["late_refreshes_over_2p5x_steady"],
            "steady": c["steady_refreshes"],
            "skipped": c["skipped_refreshes_estimate"],
            "hitches": c.get("hitches_over_50ms_steady"),
            "max_pump": c.get("max_message_pump_ms"),
            "dropped": c["dropped_by_queue"],
            "stalls": c["queue_stalls"],
            "decoded": c["decoded"],
            "hist": d["refreshes_per_film_frame"],
            "dxgi": d.get("dxgi_refreshes_between_presents", {}),
            "cpu1": pr["cpu_pct_of_one_core"],
            "render_thread": pr["render_thread_cpu_pct"],
            "dec_cpu_s": pr["decode_thread_cpu_s"],
            "ws": pr["peak_working_set_mb"],
            "gpu3d": (ld.get("proc_3d") or {}).get("mean"),
            "gpu3d_max": (ld.get("proc_3d") or {}).get("max"),
            "gpudec": (ld.get("proc_videodecode") or {}).get("mean"),
            "gpucopy": (ld.get("proc_copy") or {}).get("mean"),
            "vram": (ld.get("proc_vram_mb") or {}).get("max"),
            "cpu_total": (ld.get("cpu_total_pct") or {}).get("mean"),
            "hw_fmt": d["media"]["hw_fmt"],
            "gpu": d["env"]["gpu"],
        })
    return rows


def first_frame_table(rows):
    """Cold = first run with an empty pl_cache file, warm = runs 2..5 reusing it."""
    import statistics
    groups = {}
    for r in rows:
        if r["id"].startswith("firstframe-"):
            cfg = r["id"].split("-run")[0]
            groups.setdefault(cfg, {})[int(r["id"].split("-run")[1])] = r
    if not groups:
        return
    print("\n## First frame (process creation to first presented frame), ms\n")
    print("| config | cold (run 1, no pl_cache file) | warm runs 2-5 (median / min / max) |")
    print("|---|---|---|")
    for cfg, runs in sorted(groups.items()):
        warm = [runs[n]["first_frame_ms"] for n in sorted(runs) if n > 1]
        print(f"| {cfg.replace('firstframe-', '')} | {runs[1]['first_frame_ms']:.0f} | "
              f"{statistics.median(warm):.0f} / {min(warm):.0f} / {max(warm):.0f} |")


def hist_str(h):
    if not h:
        return "-"
    return " ".join(f"{k}x{v}" for k, v in sorted(h.items(), key=lambda kv: int(kv[0].rstrip('+'))))


def main():
    res_dir = sys.argv[1] if len(sys.argv) > 1 else "."
    rows = playback_rows(res_dir)
    print("## Windowed playback runs\n")
    print("Steady = after the first 2 s. Times in ms. CPU % is of one core (the machine has 32 logical CPUs).\n")
    print("| run | status | s | first frame (from process start) | render CPU p50 / p99 / max | GPU passes p50 / p99 | loop interval p50 / p99 / max | late >1.5x / >2.5x of steady | hitches >50 ms | skipped refreshes (est.) | queue dropped / stalls | refreshes per film frame | process CPU | render thread CPU | decode thread CPU s | GPU 3D / video-decode / copy (mean %) | VRAM MB | peak WS MB |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        if r["id"].startswith("firstframe-"):
            continue
        print(f"| {r['id']} | {r['status']} | {r['secs']:.1f} | {f(r['first_frame_ms'], 0)} | "
              f"{f(r['render_p50'], 3)} / {f(r['render_p99'], 3)} / {f(r['render_max'], 2)} | "
              f"{f(r['gpu_p50'], 3)} / {f(r['gpu_p99'], 3)} | "
              f"{f(r['iv_p50'], 3)} / {f(r['iv_p99'], 2)} / {f(r['iv_max'], 1)} | "
              f"{r['late15']} / {r['late25']} of {r['steady']} | "
              f"{'-' if r['hitches'] is None else r['hitches']} | {f(r['skipped'], 1)} | "
              f"{r['dropped']} / {r['stalls']} | {hist_str(r['hist'])} | {f(r['cpu1'], 1)} % | "
              f"{f(r['render_thread'], 1)} % | {f(r['dec_cpu_s'], 2)} | "
              f"{f(r['gpu3d'], 1)} / {f(r['gpudec'], 1)} / {f(r['gpucopy'], 2)} | {f(r['vram'], 0)} | {f(r['ws'], 0)} |")

    first_frame_table(rows)

    print("\n### DXGI refreshes between presents (D3D11 only, sampled; most runs: every present on the next refresh)\n")
    for r in rows:
        if r["dxgi"] and not r["id"].startswith("firstframe-"):
            print(f"- {r['id']}: {hist_str(r['dxgi'])}")

    dec_dir = os.path.join(res_dir, "decode-bench")
    if os.path.isdir(dec_dir):
        print("\n## Decode-only throughput (12 s per run, frames discarded)\n")
        print("| run | decoder | frames/s | x real time | process CPU (% of one core) |")
        print("|---|---|---|---|---|")
        for p in sorted(glob.glob(os.path.join(dec_dir, "*.json"))):
            d = load(p)
            if not d or "bench_decode" not in d:
                continue
            b = d["bench_decode"]
            print(f"| {d['run_id']} | {b['codec']} {b['hw_fmt'] or 'software'} | {b['fps']:.0f} | {b['x_realtime']:.1f} | {b['cpu_pct_of_one_core']:.0f} |")

    bench_files = []
    for sub, flavor in (("render-bench", "master 7.375"), ("render-bench-packaged", "package 7.360")):
        bench_files += [(flavor, p) for p in sorted(glob.glob(os.path.join(res_dir, sub, "*.json")))]
    if bench_files:
        print("\n## Headless render benchmark (1920x1080 frame, caches bypassed, GPU awaited per draw)\n")
        print("| run | libplacebo | GPU | backend | preset | source | target | wall p50 | wall p99 | GPU passes p50 | GPU passes p99 |")
        print("|---|---|---|---|---|---|---|---|---|---|---|")
        for flavor, p in bench_files:
            d = load(p)
            if not d or "bench_render" not in d:
                continue
            b = d["bench_render"]
            w = b["wall_ms_draw_plus_gpu_finish"]
            g = b["gpu_pass_sum_ms"]
            print(f"| {d['run_id']} | {flavor} | {b['gpu']} | {b['backend']} | {b['preset']} | {b['source'][0]}x{b['source'][1]} | "
                  f"{b['target'][0]}x{b['target'][1]} | {w['p50']:.3f} | {w['p99']:.3f} | {g['p50']:.3f} | {g['p99']:.3f} |")


if __name__ == "__main__":
    main()
