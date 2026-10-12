"""Which mpv settings reproduce perf0's HDR10 to SDR look?  (supported-file matrix, finding G4)

Plays media/hdr10-pq-card.mkv in stock mpv with several target options and prints the luma of the twelve grey patches
(0.1 to 10000 cd/m2) and six colour patches of each window screenshot, next to perf0's numbers from results/summary.json.
Result (results/experiments/hdr-target.txt): none of target-prim/trc, gamut mode, target-peak 203 or spline tone mapping
brought mpv down to perf0's levels (203 cd/m2 patch: perf0 135, every mpv variant 160 of 255), so the difference is a
different default target/tone-mapping state, to be set on purpose in the performer.

  python experiments\\hdr-target-experiment.py
"""
import importlib.machinery as m
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
RM = m.SourceFileLoader("run_matrix", str(HERE / "run-matrix.py")).load_module()
C, T = RM.CHECKS, RM.TOOLS
FILE = (HERE / "media" / "hdr10-pq-card.mkv").as_posix()
OUT = HERE / "scratch" / "hdr-exp"
OUT.mkdir(parents=True, exist_ok=True)


def patches(png):
    img = C.load(png)
    b = C.picture_bbox(img)
    grey, colour = C.hdr_card_patches(img, b, T)
    y = [round(0.2126 * p[0] + 0.7152 * p[1] + 0.0722 * p[2], 1) for p in grey]
    return y, [tuple(round(v) for v in colour[i]) for i in (0, 1, 2, 3, 6, 7)]


def mpv(tag, extra=()):
    png = OUT / f"{tag}.png"
    cmd = [RM.MPV, "--no-config", "--length=3", "--geometry=50%", "--ao=null", "--no-osc", "--osd-level=0", "--no-terminal",
           f"--script={RM.LUA}", f"--script-opts=matrix-out={(OUT / tag).as_posix()}.json,matrix-shot={png.as_posix()},matrix-shot_at=1.5",
           *extra, FILE]
    RM.run_proc(cmd, timeout=30)
    return patches(png) if png.exists() else "no screenshot"


if __name__ == "__main__":
    print("grey patch luma (12 steps) and colour patches R/R/G/G/Y/Y at 203 and 1000 cd/m2")
    for i, (name, extra) in enumerate([
            ("mpv default", []),
            ("mpv --target-prim=bt.709 --target-trc=srgb", ["--target-prim=bt.709", "--target-trc=srgb"]),
            ("mpv ... --gamut-mapping-mode=perceptual", ["--target-prim=bt.709", "--target-trc=srgb", "--gamut-mapping-mode=perceptual"]),
            ("mpv ... --target-peak=203 --tone-mapping=spline", ["--target-prim=bt.709", "--target-trc=srgb",
                                                                 "--gamut-mapping-mode=perceptual", "--target-peak=203", "--tone-mapping=spline"])]):
        print(name)
        print("   ", mpv(f"v{i}", extra), flush=True)
    s = json.load(open(HERE / "results" / "summary.json"))
    p = s["hdr10-pq-card-mkv"]["runs"]["perf0-sw"]["picture"]
    print("perf0 software (pass 1):")
    print("   ", p["grey_luma"], [tuple(p["colour_rgb"][i]) for i in (0, 1, 2, 3, 6, 7)])
