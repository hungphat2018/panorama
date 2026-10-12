"""Makes the clips that carry sound for the performer's audio tests, from the frame-numbered cue-test clips (video is copied,
not re-encoded; run make-cue-test-clip.py first if data/cue-test or data/cue-test-25 is missing).

  data/audio-test-24/   cue-test.mkv   23.976 fps video, 60 s, AAC stereo at 44.1 kHz (a 440 Hz tone, 3 Hz tremolo)
  data/audio-test-25/   cue-test.mkv   25 fps video, 40 s, FLAC 5.1 at 48 kHz (a different pitch per channel)
  data/audio-test-vfr/  cue-test.mkv   the 12 s variable-rate clip of the file matrix (24, 30 and 60 fps sections), AAC stereo at 48 kHz
  data/audio-test-ramp/ ramp.nut       video copy (20 s) plus a mono 48 kHz PCM track delayed 0.25 s against the video, where
                                       sample k of the track holds the value k / 2^24 (exact in float): a decoder that seeks
                                       to the exact sample can be told by reading the value back. NUT keeps the timestamps
                                       exact; ramp.mkv is the same in Matroska, whose timestamps are rounded to the millisecond

shots.json and pts.txt are copied next to each cue-test.mkv so make-cue-sheets.py can write cue sheets and scripts for it.
Needs ffmpeg on PATH (the self-built LGPL one is enough: native aac, flac and pcm encoders and lavfi sources).

  python -I audio-make-test-media.py [--force]
"""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
FFMPEG_DIRS = [r"C:\Users\tamla\msys64\home\tamla\p0\prefix\ffmpeg-lgpl\bin", r"C:\Users\tamla\msys64\ucrt64\bin"]


def ffmpeg_env():
    import os
    return dict(os.environ, PATH=";".join(FFMPEG_DIRS + [os.environ.get("PATH", "")]))


def ffmpeg(*args):
    exe = str(Path(FFMPEG_DIRS[0]) / "ffmpeg.exe")
    p = subprocess.run([exe, "-hide_banner", "-loglevel", "error", "-y", *map(str, args)], env=ffmpeg_env(), capture_output=True, text=True)
    if p.returncode:
        sys.exit(f"ffmpeg failed: {p.stderr.strip()[-600:]}")


def with_audio(src_dir, out_dir, audio_args, seconds):
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in ("shots.json", "pts.txt"):
        shutil.copyfile(src_dir / name, out_dir / name)
    ffmpeg("-i", src_dir / "cue-test.mkv", *audio_args, "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-t", seconds,
           out_dir / "cue-test.mkv")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true", help="remake clips that exist")
    a = ap.parse_args()
    for d in ("cue-test", "cue-test-25"):
        if not (DATA / d / "cue-test.mkv").exists():
            sys.exit(f"{DATA / d / 'cue-test.mkv'} is missing: run make-cue-test-clip.py first")

    out = DATA / "audio-test-24"
    if a.force or not (out / "cue-test.mkv").exists():
        with_audio(DATA / "cue-test", out,
                   ["-f", "lavfi", "-i", "aevalsrc='0.3*sin(2*PI*440*t)*(0.6+0.4*sin(2*PI*3*t))|0.3*sin(2*PI*660*t)':s=44100:d=61",
                    "-c:a", "aac", "-b:a", "128k"], 60)
        print("made", out / "cue-test.mkv")

    out = DATA / "audio-test-25"
    if a.force or not (out / "cue-test.mkv").exists():
        chans = "|".join(f"0.2*sin(2*PI*{f}*t)" for f in (220, 330, 110, 55, 440, 550))
        with_audio(DATA / "cue-test-25", out,
                   ["-f", "lavfi", "-i", f"aevalsrc='{chans}':s=48000:d=41:c=5.1", "-c:a", "flac"], 40)
        print("made", out / "cue-test.mkv")

    out = DATA / "audio-test-vfr"
    vfr = HERE.parent / "file-matrix" / "media" / "vfr-24-30-60.mkv"
    if vfr.exists() and (a.force or not (out / "cue-test.mkv").exists()):
        out.mkdir(parents=True, exist_ok=True)
        ffmpeg("-i", vfr, "-f", "lavfi", "-i", "aevalsrc='0.3*sin(2*PI*523*t)|0.3*sin(2*PI*392*t)':s=48000:d=13", "-map", "0:v:0", "-map", "1:a:0",
               "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-t", 12, out / "cue-test.mkv")
        print("made", out / "cue-test.mkv")

    out = DATA / "audio-test-ramp"
    out.mkdir(parents=True, exist_ok=True)
    for ext in ("nut", "mkv"):
        if a.force or not (out / f"ramp.{ext}").exists():
            ffmpeg("-i", DATA / "cue-test" / "cue-test.mkv", "-itsoffset", "0.25", "-f", "lavfi", "-i",
                   "aevalsrc='n/16777216':s=48000:d=20:c=mono", "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "pcm_s32le",
                   "-t", 20, out / f"ramp.{ext}")
            print("made", out / f"ramp.{ext}")


if __name__ == "__main__":
    main()
