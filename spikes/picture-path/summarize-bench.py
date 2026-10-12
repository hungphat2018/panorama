"""Prints the benchmark JSON files of out/bench/<backend>-<adapter>/ as markdown tables: GPU ms per refresh (p50 / p99), per
monitor where the scenario has two, plus the CPU time to record the commands and the wall time of record + finish.

  python -I summarize-bench.py [--dir out/bench/d3d11-nvidia] [--filter yuv]
"""
import argparse
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=os.path.join("out", "bench", "d3d11-nvidia"))
    ap.add_argument("--filter", default=None)
    args = ap.parse_args()
    rows = []
    for path in sorted(glob.glob(os.path.join(HERE, args.dir, "*.json"))):
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        if args.filter and args.filter not in d["label"]:
            continue
        rows.append(d)
    print("| config | structure | mode | phase | GPU p50 ms | GPU p99 ms | per monitor p50 (p99) | CPU p50 ms | wall p50 ms |")
    print("|---|---|---|---|---|---|---|---|---|")
    for d in rows:
        mons = "; ".join("%s %.3f (%.3f)" % (m["id"].replace("m", "") if m["id"].startswith("m") else m["id"], m["gpu_ms"]["p50"], m["gpu_ms"]["p99"])
                         for m in d["monitors"])
        print("| %s | %s | %s | %s | %.3f | %.3f | %s | %.3f | %.3f |" % (
            d["label"], d["structure"], d["mode"], d["phase"], d["gpu_ms"]["p50"], d["gpu_ms"]["p99"], mons, d["cpu_ms"]["p50"], d["wall_ms"]["p50"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
