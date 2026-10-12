"""Runs the whole worker (pre-scan, segmented scan, subtitles) on a list of small edge-case files and checks that every segment's
decoded frame times matched the frame table, that nothing failed, and that the folder passes validate-content.py.

    tools\\venv-analysis\\Scripts\\python.exe -I spikes\\film-analysis\\scan-edge-cases.py OUT_DIR FILE [FILE ...]
        [--decode-path software|auto|cuda|d3d11va] [--models-on cpu|auto] [--segment-seconds 3] [--sample-stride 48]

A segment whose frames come out at other times than the frame table says is refused by the worker (segment_worker.py), so a
file that scans to completion has, for every segment, decoded exactly the table's frames in the table's order.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
PY = REPO / "tools" / "venv-analysis" / "Scripts" / "python.exe"


def main(argv) -> int:
    opts = {"--decode-path": "software", "--models-on": "cpu", "--segment-seconds": "3", "--sample-stride": "48", "--workers": "2"}
    pos = []
    i = 0
    while i < len(argv):
        if argv[i] in opts and i + 1 < len(argv):
            opts[argv[i]] = argv[i + 1]
            i += 2
        else:
            pos.append(argv[i])
            i += 1
    if len(pos) < 2:
        print(__doc__)
        return 2
    out_root = Path(pos[0])
    out_root.mkdir(parents=True, exist_ok=True)
    rows, bad = [], 0
    for f in pos[1:]:
        name = Path(f).stem
        out = out_root / name
        cmd = [str(PY), "-I", str(HERE / "analyze.py"), f, "--out", str(out), "--force"] + [x for kv in opts.items() for x in kv]
        t0 = time.time()
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        status = {"file": name, "exit": r.returncode, "seconds": round(time.time() - t0, 1)}
        content = out / "content.json"
        if content.exists():
            m = json.loads(content.read_text())
            status.update({"scan_state": m["scan"]["state"], "failed_segments": len(m["scan"]["failed"]),
                           "segments_done": len(m["scan"]["done"]), "frames": m["scan"]["n_frames"]})
            v = subprocess.run([str(PY), "-I", str(HERE / "validate-content.py"), str(out), "--quiet"], capture_output=True, text=True)
            status["validate"] = v.stdout.strip().splitlines()[-1] if v.stdout.strip() else v.stderr.strip()[-200:]
            ok = r.returncode == 0 and m["scan"]["state"] == "complete" and v.returncode == 0
        else:
            ok = False
            status["error"] = (r.stderr or r.stdout).strip()[-300:]
        status["ok"] = ok
        bad += 0 if ok else 1
        rows.append(status)
        print(json.dumps(status), flush=True)
    (out_root / "edge-case-scan.json").write_text(json.dumps(rows, indent=1))
    print(f"{len(rows) - bad} of {len(rows)} files scanned and validated")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
