"""Can FFmpeg's own filters undo the hard telecine of media/mpeg2-480i-hard-telecine.mpg?  (finding G6)

Decodes frames of the 29.97 fps file plain and through `fieldmatch,yadif=deint=interlaced,decimate`, counts frames, and prints
the comb score (matrix-picture-checks.py) of five consecutive frames of each. Result (results/experiments/ivtc.txt): 300 frames
become 240 at 23.98 fps; the two combed frames of every five (score about 0.8 %) are gone (about 0.15 to 0.18 %, the clean level).
Also checks that perf0 plays a concat script, the way an ordered-chapters timeline would be handed to libavformat.

  python experiments\\ivtc-check.py
"""
import importlib.machinery as m
import re
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
T = m.SourceFileLoader("matrix_media_tools", str(HERE / "matrix-media-tools.py")).load_module()
C = m.SourceFileLoader("matrix_picture_checks", str(HERE / "matrix-picture-checks.py")).load_module()
FILE = HERE / "media" / "mpeg2-480i-hard-telecine.mpg"
OUT = HERE / "scratch" / "ivtc"
OUT.mkdir(parents=True, exist_ok=True)
IVTC = "fieldmatch,yadif=deint=interlaced,decimate"


def count(vf):
    p = subprocess.run([T.FFMPEG, "-v", "info", "-i", str(FILE), "-map", "0:v:0", "-an", "-vf", vf, "-f", "null", "-"],
                       capture_output=True, text=True, errors="replace")
    frames = re.findall(r"frame=\s*(\d+)", p.stderr)
    fps = re.findall(r"Video: .*?, ([\d.]+) fps", p.stderr)
    return int(frames[-1]), fps[-1] if fps else "?"


def comb(vf, n, name):
    png = OUT / f"{name}-{n}.png"
    subprocess.run([T.FFMPEG, "-v", "error", "-y", "-i", str(FILE), "-vf", f"{vf + ',' if vf else ''}select=eq(n\\,{n})", "-frames:v", "1", str(png)],
                   capture_output=True)
    img = C.load(png)
    return round(C.comb_score(img, C.picture_bbox(img)), 3)


if __name__ == "__main__":
    print("plain  : frames, fps =", count("null"))
    print("ivtc   : frames, fps =", count(IVTC), " ->", IVTC)
    print("comb score % of five consecutive frames, plain :", [comb("", n, "plain") for n in range(100, 105)])
    print("comb score % of five consecutive frames, ivtc  :", [comb(IVTC, n, "ivtc") for n in range(80, 85)])
