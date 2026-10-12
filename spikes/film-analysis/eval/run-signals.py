"""Compute and cache the raw shot signals of clips: TransNetV2 heads, scdet scores, PySceneDetect scene starts, keyframes.

Usage: python -I run-signals.py CLIP_ID [CLIP_ID ...] [--which tn scdet psd kf] [--force] [--cpu]

Cached in tools/testset/runs/signals/<clip>.npz. Run with the worker's venv (tools/venv-analysis); PySceneDetect runs in
tools/testset/venv-extras through a subprocess. Before a GPU run the script checks the GPU timing lock and other GPU load.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np  # noqa: E402

from fa_eval import cache  # noqa: E402
from fa_eval.gpu_guard import want_gpu  # noqa: E402
from fa_eval.methods import shots as shot_methods  # noqa: E402
from testset_common import frame_table, log  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("clips", nargs="+")
    ap.add_argument("--which", nargs="*", default=["tn", "scdet", "kf", "psd"])
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--cpu", action="store_true", help="TransNetV2 on the CPU provider (no GPU use)")
    a = ap.parse_args()
    for cid in a.clips:
        path = cache.clip_file(cid)
        have = {} if a.force else cache.load_npz("signals", cid)
        n = len(frame_table(path)[0])
        have["n_frames"] = np.array([n])
        use_gpu = (not a.cpu) and want_gpu(log)
        if "tn" in a.which and "tn_single" not in have:
            r = shot_methods.run_transnet(path, ep="cuda" if use_gpu else "cpu", gpu_decode=use_gpu, expect_frames=n)
            have["tn_single"], have["tn_many"] = r["single"], r["many"]
            have["tn_seconds"] = np.array([r["decode_s"], r["infer_s"]])
            log(f"[{cid}] TransNetV2 ({'GPU' if use_gpu else 'CPU'}) {r['n']} frames: decode {r['decode_s']:.1f}s infer {r['infer_s']:.1f}s providers {r['providers'][0]}")
        if "scdet" in a.which and "scdet" not in have:
            t0 = time.perf_counter()
            have["scdet"] = shot_methods.run_scdet(path, gpu=use_gpu)
            log(f"[{cid}] scdet {len(have['scdet'])} frames in {time.perf_counter() - t0:.1f}s")
        if "kf" in a.which and "keyframes" not in have:
            have["keyframes"] = shot_methods.keyframes(path)
            log(f"[{cid}] keyframes {len(have['keyframes'])}")
        if "psd" in a.which and "psd_content" not in have:
            out = cache.run_dir("signals") / f"{cid}.psd.npz"
            r = shot_methods.run_pyscenedetect(path, out)
            have["psd_content"], have["psd_adaptive"] = r["content"], r["adaptive"]
            have["psd_seconds"] = np.array([r["content_seconds"][0], r["adaptive_seconds"][0]])
            log(f"[{cid}] PySceneDetect: " + r["stdout"].strip().replace("\n", " | "))
            out.unlink(missing_ok=True)
        cache.save_npz("signals", cid, have)
    return 0


if __name__ == "__main__":
    sys.exit(main())
