"""Film-analysis worker: makes (or continues) the content model of one video file.

    python -I analyze.py FILE (--out DIR | --cache-root ROOT) [--prescan-only] [--from SECONDS] [--workers N]
                         [--segment-seconds 45] [--sample-stride 12] [--decode-path auto|cuda|d3d11va|software]
                         [--no-subtitles] [--full-hash] [--force]

DIR becomes the content-model folder (content.json is its index; schema in schema/). With --cache-root the folder is
ROOT/<first 32 hex digits of the file key>-<size>: one folder per file identity, found again whatever the file is called or
where it lies. Without --prescan-only the quick pre-scan is
followed by the full scan, segment by segment, starting at --from (seconds on the film's own timeline, 0 by default). Run it again
on the same DIR and file and it continues: finished segments are not redone, a table whose analyzer version is out of date is made
again alone (refresh.py), and signals made by other analyzer versions or models are scanned afresh. The process lowers its own priority to
below-normal, puts itself and its children (ffmpeg, pool processes) in one Windows job object (CPU accounting, optional memory
cap, children die with it), decodes on the GPU's video decoder where there is one and keeps every model call short.
Exit code 0 when the requested work finished (failed segments are listed in content.json and give exit code 3).
"""
import argparse
import json
import multiprocessing
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)                      # python -I does not put the script's folder on sys.path
os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")   # OpenCV 5's DNN prints a harmless backend note otherwise

REPO = os.path.abspath(os.path.join(HERE, "..", ".."))


def parse_args(argv):
    ap = argparse.ArgumentParser(description="Film-analysis worker (content model panorama.content/0)")
    ap.add_argument("file", help="the video file")
    where = ap.add_mutually_exclusive_group(required=True)
    where.add_argument("--out", help="content-model folder (created if missing)")
    where.add_argument("--cache-root", help="folder holding one content-model folder per file identity (the folder name is made from the file key)")
    ap.add_argument("--prescan-only", action="store_true", help="stop after the quick pre-scan")
    ap.add_argument("--from", dest="playhead", type=float, default=0.0, metavar="SECONDS", help="playhead: scan from here first")
    ap.add_argument("--workers", type=int, default=0, help="pool processes for the CPU work (default: half the cores, at most 6)")
    ap.add_argument("--segment-seconds", type=float, default=45.0, help="target segment length")
    ap.add_argument("--sample-stride", type=int, default=12, help="faces / heads / people on every Nth frame")
    ap.add_argument("--decode-path", default="auto", choices=["auto", "cuda", "d3d11va", "software"])
    ap.add_argument("--models-dir", default=os.path.join(REPO, "tools", "models"))
    ap.add_argument("--models-on", default="auto", choices=["auto", "cpu"], help="auto: GPU when ONNX Runtime has CUDA, else CPU; cpu: never the GPU")
    ap.add_argument("--no-ramp", action="store_true", help="no short first segments")
    ap.add_argument("--no-subtitles", action="store_true", help="skip the subtitle tracks")
    ap.add_argument("--full-hash", action="store_true", help="after a complete scan, also hash every byte of the file and store it in the key")
    ap.add_argument("--force", action="store_true", help="redo everything: new signals, scan from scratch")
    ap.add_argument("--max-segments", type=int, default=None, help="stop after this many segments (testing)")
    ap.add_argument("--memory-cap-gb", type=float, default=12.0, help="job-wide memory cap, 0 = none")
    ap.add_argument("--verify-models", action="store_true", help="check the model files' sha-256 against models-manifest.json")
    ap.add_argument("--priority", default="below-normal", choices=["below-normal", "normal"])
    return ap.parse_args(argv)


def main(argv) -> int:
    args = parse_args(argv)
    from film_analysis import proc
    if args.priority == "below-normal":
        proc.lower_own_priority()
    job = proc.JobObject(memory_cap_bytes=int(args.memory_cap_gb * (1 << 30)) if args.memory_cap_gb > 0 else None)
    t0 = time.perf_counter()

    from film_analysis import file_key, manifest as mf, models_check, prescan, refresh, scan, versions
    manifest_path = os.path.join(HERE, "models-manifest.json")
    if args.verify_models:
        problems = models_check.verify(args.models_dir, manifest_path)
        if problems:
            print("model check failed:\n  " + "\n  ".join(problems), file=sys.stderr)
            return 2
    model_hashes = models_check.model_hashes(manifest_path)
    file = os.path.abspath(args.file)
    if not os.path.isfile(file):
        print(f"no such file: {file}", file=sys.stderr)
        return 2
    out = os.path.abspath(args.out) if args.out else os.path.join(os.path.abspath(args.cache_root), file_key.cache_folder_name(file_key.sample_key(file)))

    ctx = None if args.force else prescan.load_context(file, out, model_hashes, args.sample_stride)
    if ctx is None:
        print(f"[prescan] {file}", file=sys.stderr, flush=True)
        ctx = prescan.run(file, out, model_hashes, args.sample_stride, args.decode_path)
        pre = ctx.timings
        print(f"[prescan] done in {pre['prescan_total_s']:.2f} s: {ctx.table.n} frames, {int(ctx.table.key.sum())} keyframes, "
              f"decode path {ctx.decode['path']}, black level {ctx.black['bar_level']}", file=sys.stderr, flush=True)
    else:
        stale = [t for t in mf.stale_tables(ctx.manifest, versions.table_versions(), out) if t != "subtitles"]
        print(f"[prescan] reusing the stored pre-scan" + (f"; out-of-date tables: {', '.join(stale)}" if stale else ""), file=sys.stderr, flush=True)
        if stale:
            refresh.refresh_stale(ctx, stale, log=lambda m: print(f"[refresh] {m}", file=sys.stderr, flush=True))

    summary = {"content": os.path.join(out, mf.NAME), "prescan_s": ctx.manifest.get("prescan", {}).get("timings", {}).get("prescan_total_s")}
    rc = 0
    if not args.prescan_only:
        workers = args.workers or 3
        sub_future = None
        have = ctx.manifest["tables"].get("subtitles")
        if not args.no_subtitles and not (have and have["version"] == versions.SUBTITLES and have["state"] == "complete"):
            from concurrent.futures import ThreadPoolExecutor
            from film_analysis import subtitles_events
            sub_pool = ThreadPoolExecutor(max_workers=1)
            sub_future = sub_pool.submit(subtitles_events.run_all, ctx, out, 2)
        timing = scan.run_scan(ctx, args.models_dir, manifest_path, args.playhead, workers, args.segment_seconds, args.sample_stride,
                               args.force, ramp=not args.no_ramp, max_segments=args.max_segments, models_on=args.models_on)
        summary["scan"] = timing
        if sub_future is not None:
            subs = sub_future.result()
            sub_pool.shutdown()
            mf.set_table(ctx.manifest, "subtitles", None, versions.SUBTITLES,
                         "complete" if all(t["status"] != "failed" for t in subs["tracks"]) else "partial", tracks=subs["tracks"], seconds=subs["seconds"])
            summary["subtitles"] = {"seconds": subs["seconds"], "tracks": len(subs["tracks"])}
        ctx.manifest["tables"]["signals"]["state"] = ctx.manifest["scan"]["state"]
        mf.save(out, ctx.manifest)
        if ctx.manifest["scan"]["failed"]:
            rc = 3
        elif args.full_hash and ctx.manifest["scan"]["state"] == "complete":
            full = file_key.full_sha256(file)          # the slow, later part of the key: every byte
            for holder in (ctx.manifest["file"]["key"], ctx.facts["file"]["key"]):
                holder["full_sha256"] = full
            mf.save(out, ctx.manifest)
            from film_analysis.signals_store import write_json_atomic
            write_json_atomic(os.path.join(out, "file-facts.json"), ctx.facts)
            summary["full_sha256"] = full
    cpu = job.cpu_seconds()
    summary["wall_s"] = round(time.perf_counter() - t0, 2)
    summary["job"] = {"active": job.active, "error": job.error, "cpu": cpu, "peak_memory_bytes": job.peak_memory_bytes()}
    print(json.dumps(summary, default=str))
    return rc


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main(sys.argv[1:]))
