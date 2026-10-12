"""Makes the media of run-picture-checks.py in data/pic-test/ (lossless FFV1 RGB films tagged sRGB / BT.709, so the decoded frames
are exactly the picture-path harness's test patterns), with the LGPL FFmpeg of the performer's toolchain.

  python -I make-picture-test-media.py [--force]

  t1.mkv          the harness's grid-and-circles pattern t1, 4 s at 23.976 fps
  alt-t1-t7.mkv   t1 and t7 on alternating frames (96 frames at 23.976 fps): what makes a rolling blend visible
  t1-subs.mkv     t1.mkv with a two-line SRT track (subs.srt)
  ramp.mkv        flat bands at the darkest sRGB codes (check-ramp.py), 3 s
  room-half.json  the Setup A default room with native_px halved (the windowed runs' references), room-colour.json, the cue sheets
                  and scenarios are small files kept in the folder
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data", "pic-test")
PATTERNS = os.path.join(HERE, "..", "picture-path", "patterns")
FFMPEG_BIN = r"C:\Users\tamla\msys64\home\tamla\p0\prefix\ffmpeg-lgpl\bin"
ENV = dict(os.environ, PATH=";".join([FFMPEG_BIN, r"C:\Users\tamla\msys64\ucrt64\bin", os.environ.get("PATH", "")]))
TAGS = "colorspace=gbr:color_primaries=bt709:color_trc=iec61966-2-1:range=pc"   # RGB, sRGB transfer, full range


def ffmpeg(*args):
    subprocess.run([os.path.join(FFMPEG_BIN, "ffmpeg.exe"), "-hide_banner", "-loglevel", "error", "-y", *args], env=ENV, check=True)


def main():
    force = "--force" in sys.argv
    os.makedirs(DATA, exist_ok=True)
    t1, t7 = os.path.join(PATTERNS, "t1-1920x1080.ppm"), os.path.join(PATTERNS, "t7-1920x1080.ppm")
    out = lambda n: os.path.join(DATA, n)
    todo = lambda n: force or not os.path.exists(out(n))
    if todo("t1.mkv"):
        ffmpeg("-loop", "1", "-framerate", "24000/1001", "-i", t1, "-t", "4", "-vf", "format=bgr0,setparams=" + TAGS, "-c:v", "ffv1", out("t1.mkv"))
    if todo("alt-t1-t7.mkv"):
        # frames 1001 ticks of 1/24000 s apart, t1 on even and t7 on odd ones
        ffmpeg("-loop", "1", "-framerate", "12", "-i", t1, "-loop", "1", "-framerate", "12", "-i", t7, "-filter_complex",
               "[0:v]format=bgr0,settb=1/24000,setpts=N*2002[a];[1:v]format=bgr0,settb=1/24000,setpts=N*2002+1001[b];"
               "[a][b]interleave,setparams=" + TAGS + "[v]", "-map", "[v]", "-frames:v", "96", "-c:v", "ffv1", out("alt-t1-t7.mkv"))
    if todo("t1-subs.mkv"):
        with open(out("subs.srt"), "w", encoding="utf-8") as f:
            f.write("1\n00:00:00,500 --> 00:00:02,500\nHello panorama, this is dialogue\n\n2\n00:00:03,000 --> 00:00:04,000\nSecond line\n")
        ffmpeg("-i", out("t1.mkv"), "-i", out("subs.srt"), "-map", "0:v", "-map", "1:s", "-c:v", "copy", "-c:s", "srt", out("t1-subs.mkv"))
    if todo("ramp.mkv"):
        subprocess.run([sys.executable, "-I", os.path.join(HERE, "check-ramp.py"), "make", out("ramp.ppm")], check=True)
        ffmpeg("-loop", "1", "-framerate", "24000/1001", "-i", out("ramp.ppm"), "-t", "3", "-vf", "format=bgr0,setparams=" + TAGS, "-c:v", "ffv1", out("ramp.mkv"))
        os.remove(out("ramp.ppm"))
    if force or not os.path.exists(out("room-half.json")):
        room = json.load(open(os.path.join(HERE, "..", "picture-path", "room", "setup-a-default.json"), encoding="utf-8"))
        for m in room["monitors"]:
            m["panel"]["native_px"] = [v // 2 for v in m["panel"]["native_px"]]
        room["name"] = "setup-a-default-half"
        json.dump(room, open(out("room-half.json"), "w", encoding="utf-8"), indent=1)
    print("media in", DATA)


if __name__ == "__main__":
    main()
