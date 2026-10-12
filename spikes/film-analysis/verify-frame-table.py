"""Checks the worker's frame table (and the timeline rule) against the performer's own decoder path.

For every file:
  A. the worker's table: ffprobe packet list -> film_analysis.frame_table (the rule in its docstring);
  B. frame-times.exe (native-checks/): the performer's decode loop without a window, software decoder, optionally also
     through D3D11VA (--hw);
  C. optionally the real performer, perf1-p3-lgpl.exe --headless: the frame timestamps it logs in its cue log.
A matches B when both lists have the same frames (ticks equal, same count) and A's seconds equal B's doubles bit for bit.
A against C is compared to 1 ns (the cue log prints 9 decimals).

Run:  tools\\venv-analysis\\Scripts\\python.exe -I spikes\\film-analysis\\verify-frame-table.py --files A.mkv B.mp4 ...
      [--hw d3d11va] [--performer PATH\\perf1-p3-lgpl.exe --performer-seconds 14] [--json OUT.json]
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import numpy as np  # noqa: E402

from film_analysis import frame_table as ft  # noqa: E402
from film_analysis import media_probe as mp  # noqa: E402

PREFIX = Path(r"C:\Users\tamla\msys64\home\tamla\p0\prefix")
RUNTIME_DIRS = [PREFIX / "ffmpeg-lgpl" / "bin", PREFIX / "libplacebo-master" / "bin",
                Path(r"C:\Users\tamla\msys64\ucrt64\bin")]


def lgpl_env() -> dict:
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join([str(p) for p in RUNTIME_DIRS] + [env.get("PATH", "")])
    return env


def worker_table(path: str):
    info = mp.probe_container(path)
    st = mp.pick_video_stream(info)
    num, den = (int(x) for x in st["time_base"].split("/"))
    pk = mp.video_packets(path, int(st["index"]))
    return ft.table_from_packets(pk, num, den), st


def performer_path_times(path: str, hw: str = "") -> dict:
    exe = HERE / "native-checks" / "bin" / "frame-times.exe"
    cmd = [str(exe), path] + (["--hw", hw] if hw else [])
    r = subprocess.run(cmd, capture_output=True, text=True, env=lgpl_env(), encoding="utf-8", errors="replace")
    if r.returncode != 0:
        return {"error": (r.stderr or r.stdout).strip()[:300]}
    ticks, rebased, header = [], [], {}
    for line in r.stdout.splitlines():
        if line.startswith("#"):
            parts = line[2:].split("\t")
            header[parts[0]] = parts[1:]
            continue
        f = line.split("\t")
        if len(f) == 4:
            ticks.append(int(f[1]))
            rebased.append(float(f[3]))
    return {"ticks": np.asarray(ticks, dtype=np.int64), "rebased": np.asarray(rebased, dtype=np.float64), "header": header}


def performer_headless_times(performer: str, path: str, seconds: float) -> dict:
    out = Path(tempfile.mkdtemp(prefix="perf-headless-"))
    cmd = [performer, "--headless", "96", "--file", path, "--seconds", str(seconds), "--no-lock", "--out", str(out)]
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True, env=lgpl_env(), encoding="utf-8", errors="replace")
    log = out / "perf1-cuelog.json"
    if not log.exists():
        return {"error": f"no cue log (exit {r.returncode}): {(r.stderr or r.stdout).strip()[-300:]}"}
    d = json.loads(log.read_text())
    ep = d["epochs"][0] if d.get("epochs") else None
    return {"frame_pts": np.asarray(ep["frame_pts"], dtype=np.float64) if ep else np.zeros(0), "wall_s": time.time() - t0,
            "exit": r.returncode}


def compare(path: str, hw: str, performer: str, perf_seconds: float) -> dict:
    res = {"file": os.path.basename(path)}
    try:
        table, st = worker_table(path)
    except Exception as e:  # noqa: BLE001 - report and go on with the next file
        res["status"] = "worker-error"
        res["error"] = str(e)[:300]
        return res
    res.update({"codec": st.get("codec_name"), "time_base": f"{table.tb_num}/{table.tb_den}", "worker_frames": table.n,
                "worker_stats": table.stats})
    ref = performer_path_times(path)
    if "error" in ref:
        res["status"] = "reference-error"
        res["error"] = ref["error"]
        return res
    res["decoder_frames"] = int(ref["ticks"].shape[0])
    same_count = table.n == ref["ticks"].shape[0]
    ticks_equal = same_count and bool(np.array_equal(table.pts, ref["ticks"]))
    bit_equal = same_count and bool(np.array_equal(table.seconds(), ref["rebased"]))
    res["ticks_equal"] = ticks_equal
    res["seconds_bit_equal"] = bit_equal
    if not ticks_equal:
        m = min(table.n, ref["ticks"].shape[0])
        diff = np.nonzero(table.pts[:m] != ref["ticks"][:m])[0]
        res["first_mismatch_index"] = int(diff[0]) if diff.size else m
        res["worker_first_ticks"] = [int(x) for x in table.pts[:3]]
        res["decoder_first_ticks"] = [int(x) for x in ref["ticks"][:3]]
        # frames only one side has
        a, b = set(table.pts.tolist()), set(ref["ticks"].tolist())
        res["only_in_worker"] = len(a - b)
        res["only_in_decoder"] = len(b - a)
        res["worker_origin_s"] = table.origin_seconds_abs()
    if hw:
        hwr = performer_path_times(path, hw)
        if "error" in hwr:
            res["hw_error"] = hwr["error"]
        else:
            res["hw_frames"] = int(hwr["ticks"].shape[0])
            res["hw_ticks_equal_software"] = bool(np.array_equal(hwr["ticks"], ref["ticks"]))
            res["hw_seconds_bit_equal_software"] = bool(np.array_equal(hwr["rebased"], ref["rebased"]))
    if performer:
        pr = performer_headless_times(performer, path, perf_seconds)
        if "error" in pr:
            res["performer_error"] = pr["error"]
        else:
            fp = pr["frame_pts"]
            res["performer_frames"] = int(fp.shape[0])
            n = min(fp.shape[0], table.n)
            res["performer_prefix_max_abs_diff_s"] = float(np.max(np.abs(fp[:n] - table.seconds()[:n]))) if n else None
            res["performer_complete"] = bool(fp.shape[0] == table.n)
    ok = ticks_equal and bit_equal
    if performer and "performer_prefix_max_abs_diff_s" in res:
        ok = ok and (res["performer_prefix_max_abs_diff_s"] is not None and res["performer_prefix_max_abs_diff_s"] <= 1e-9)
    res["status"] = "match" if ok else "DIFFERENT"
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--files", nargs="+", required=True)
    ap.add_argument("--hw", default="", help="also run the decoder path through this hardware API (d3d11va)")
    ap.add_argument("--performer", default="", help="path to perf1-p3-lgpl.exe: also read its headless cue log")
    ap.add_argument("--performer-seconds", type=float, default=14.0)
    ap.add_argument("--json", default="")
    a = ap.parse_args()
    results = []
    for f in a.files:
        r = compare(f, a.hw, a.performer, a.performer_seconds)
        results.append(r)
        extra = ""
        if r.get("performer_frames") is not None:
            extra = f" performer {r['performer_frames']} fr, max|diff| {r.get('performer_prefix_max_abs_diff_s')}"
        if r.get("hw_frames") is not None:
            extra += f" hw {r['hw_frames']} fr ticks_eq={r.get('hw_ticks_equal_software')}"
        print(f"{r['status']:12s} {r['file']:44s} worker {r.get('worker_frames')} decoder {r.get('decoder_frames')}"
              f" ticks_eq={r.get('ticks_equal')} bit_eq={r.get('seconds_bit_equal')}{extra}"
              + (f" ERROR {r.get('error') or r.get('performer_error') or r.get('hw_error')}" if (
                  r.get('error') or r.get('performer_error') or r.get('hw_error')) else ""), flush=True)
    if a.json:
        Path(a.json).write_text(json.dumps(results, indent=1))
    bad = [r for r in results if r["status"] not in ("match",)]
    print(f"{len(results) - len(bad)} of {len(results)} files match")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
