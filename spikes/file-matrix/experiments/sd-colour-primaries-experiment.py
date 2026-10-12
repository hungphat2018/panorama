"""Why does perf0 render BT.601 SD files differently from stock mpv?  (supported-file matrix, finding G4)

Renders the same SD file with perf0 (libplacebo master 7.375 and the MSYS2 package 7.360), with perf0's --sd-passthrough
switch, and with stock mpv under several colour options, then prints the mean RGB of the six test bars of each frame grab.
Result (results/experiments/sd-colour-primaries.txt): both libplacebo versions agree; mpv's default equals perf0
--sd-passthrough (primaries passed through); mpv with --target-prim=bt.709 --target-trc=srgb equals perf0's default
(primaries converted to BT.709, out-of-gamut colours mapped inwards). The gamut-mapping mode does not change mpv's
output because mpv does not convert SD primaries unless told to.

  python experiments\\sd-colour-primaries-experiment.py [media file name]   (default: anamorphic-sar-vui.mkv)
"""
import importlib.machinery as m
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
RM = m.SourceFileLoader("run_matrix", str(HERE / "run-matrix.py")).load_module()
C = RM.CHECKS
FILE = (HERE / "media" / (sys.argv[1] if len(sys.argv) > 1 else "anamorphic-sar-vui.mkv")).as_posix()
OUT = HERE / "scratch" / "colour-exp"
OUT.mkdir(parents=True, exist_ok=True)


def bars(png):
    img = C.load(png)
    b = C.picture_bbox(img)
    return [tuple(round(v) for v in C.rel_patch(img, b, (i + 0.2) / 6, (i + 0.8) / 6, 0.45, 0.55)) for i in range(6)]


def perf0(flavor, tag, extra=()):
    exe = RM.PERF0_DIR / f"perf0-{flavor}.exe"
    ppm = OUT / f"{tag}.ppm"
    cmd = [exe, "--file", FILE, "--backend", "d3d11", "--decode", "sw", "--adapter", "NVIDIA", "--size", "1920x1080", "--seconds", "3",
           "--out", OUT / tag, "--id", tag, "--dump-ppm", ppm, "--dump-frame", "50", "--log-level", "3", *extra]
    env = RM.perf0_env("master" if flavor == "packaged" else flavor)
    rc, out, err, timed_out, wall = RM.run_proc(cmd, env, timeout=30)
    if not ppm.exists():
        return f"no frame (rc={rc}) {err[-200:]}"
    return bars(RM.ppm_to_png(ppm))


def mpv(tag, extra=()):
    png = OUT / f"{tag}.png"
    cmd = [RM.MPV, "--no-config", "--length=3", "--geometry=50%", "--ao=null", "--no-osc", "--osd-level=0", "--no-terminal",
           f"--script={RM.LUA}", f"--script-opts=matrix-out={(OUT / tag).as_posix()}.json,matrix-shot={png.as_posix()},matrix-shot_at=2.0",
           *extra, FILE]
    RM.run_proc(cmd, timeout=30)
    return bars(png) if png.exists() else "no screenshot"


RUNS = [
    ("perf0 lgpl (libplacebo master 375)", lambda: perf0("lgpl", "perf0-lgpl")),
    ("perf0 packaged (libplacebo 7.360)", lambda: perf0("packaged", "perf0-packaged")),
    ("perf0 --sd-passthrough", lambda: perf0("lgpl", "perf0-pass", ("--sd-passthrough",))),
    ("mpv default", lambda: mpv("mpv-default")),
    ("mpv --gamut-mapping-mode=perceptual", lambda: mpv("mpv-perceptual", ["--gamut-mapping-mode=perceptual"])),
    ("mpv --gamut-mapping-mode=clip", lambda: mpv("mpv-clip", ["--gamut-mapping-mode=clip"])),
    ("mpv --target-trc=srgb", lambda: mpv("mpv-srgb", ["--target-trc=srgb"])),
    ("mpv --target-prim=bt.709 --target-trc=srgb", lambda: mpv("mpv-709-srgb", ["--target-prim=bt.709", "--target-trc=srgb"])),
]

if __name__ == "__main__":
    print("file:", FILE)
    print("mean RGB of the six bars (red, green, yellow, blue, magenta, cyan-ish moving element)")
    for name, fn in RUNS:
        print(f"{name:46s}", fn(), flush=True)
