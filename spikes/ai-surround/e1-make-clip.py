"""Cuts the E1 test clip: the stretch of the JJK episode with the most camera pans, plus a cue sheet with its real shot starts.

E1 plays perf1 for 120 s per cell; the 3 s pan scene (out/scenes/jjk-pan) is too short, and no single pan lasts that long. So the
content model picks the stretch instead: of the --seconds long windows that start on a keyframe, the one whose frames lie most
inside slide runs (slide-paths.json: sideways and vertical pans with a measured path); it contains the jjk-pan shot. The video
stream is copied, not re-encoded (HEVC 1080p Main 10, decoded by the GPU's video engine as in the product), with the episode's
own timestamps (-copyts). Matroska seeks to an index point, which can be an earlier keyframe than asked, so the clip's real start
is read back from the copy: its first decoded frame is time 0 for perf1 (perf1-decode.c rebases to the first decoded frame).
No audio, no subtitles: E1 runs perf1 with --no-audio --no-subs.

The cue sheet (panorama.cues/1, version 1) has one shot per shot of the content model (shots.json) inside the clip, at that
rebased time, halfway between the shot's first frame and the frame before it; default placement, no crop, no slide (the per-
refresh GPU cost does not depend on them: every refresh runs every pass). It keeps the engine's behaviour at cuts (re-aim,
follower moves) as in a real film.

  python -I -X utf8 e1-make-clip.py [--film EPISODE.mkv] [--content out/jjk/content] [--seconds 130] [--out out/e1/clip]
Writes OUT/jjk-pans-<S>s.mkv, OUT/cues.json and OUT/clip.json (where it came from, the pans inside, the shots); one clip per
folder, so for another length give another --out (the E1 runner then takes its --clip and --cues). Needs ffmpeg and
ffprobe on PATH (or PANORAMA_FFMPEG / PANORAMA_FFPROBE). Reads the episode's packet index (no decoding) and copies one stretch.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

HERE = Path(__file__).resolve().parent
FILM = Path("C:/Users/tamla/Downloads/Jujutsu.Kaisen.S03E12.FiNAL.MULTi.1080p.WEBRiP.x265-KAF.mkv")


def tool(name, env):
    path = os.environ.get(env) or shutil.which(name)
    if not path:
        raise SystemExit(f"{name} not found (PATH or {env})")
    return path


def csv_rows(ffprobe, args):
    out = subprocess.run([ffprobe, "-v", "error", *args, "-of", "csv=p=0"], capture_output=True, text=True, check=True).stdout
    return [line.split(",") for line in out.splitlines() if line.strip()]


def keyframes(ffprobe, film):
    """Timestamps of the video keyframes, from the packets' flags (demuxing only)."""
    rows = csv_rows(ffprobe, ["-select_streams", "v:0", "-show_entries", "packet=pts_time,flags", str(film)])
    return sorted(float(r[0]) for r in rows if len(r) > 1 and r[0] not in ("", "N/A") and "K" in r[1])


def pan_cover(runs, s, seconds):
    return sum(max(0.0, min(r["end_s"], s + seconds) - max(r["start_s"], s)) for r in runs)


def first_frames(ffprobe, clip, n=1):
    """Timestamps of the first n decoded frames of the clip (decodes only those)."""
    rows = csv_rows(ffprobe, ["-select_streams", "v:0", "-read_intervals", f"%+#{n}", "-show_entries", "frame=pts_time",
                              str(clip)])
    return [float(r[0]) for r in rows if r and r[0] not in ("", "N/A")]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--film", type=Path, default=FILM)
    ap.add_argument("--content", type=Path, default=HERE / "out" / "jjk" / "content")
    ap.add_argument("--seconds", type=float, default=130.0, help="clip length (perf1 plays --seconds of it; default 130)")
    ap.add_argument("--out", type=Path, default=HERE / "out" / "e1" / "clip")
    a = ap.parse_args()
    ffmpeg, ffprobe = tool("ffmpeg", "PANORAMA_FFMPEG"), tool("ffprobe", "PANORAMA_FFPROBE")

    slides = json.loads((a.content / "slide-paths.json").read_text(encoding="utf-8"))
    shots = json.loads((a.content / "shots.json").read_text(encoding="utf-8"))["shots"]
    facts_path = a.content / "file-facts.json"
    facts = json.loads(facts_path.read_text(encoding="utf-8")) if facts_path.exists() else {}
    fps = float(Fraction(24000, 1001))
    duration = slides["frame_count"] / fps
    keys = [k for k in keyframes(ffprobe, a.film) if k + a.seconds <= duration]
    cover, key = max((pan_cover(slides["runs"], k, a.seconds), -k) for k in keys)
    key = -key  # the earliest of equally good starts

    a.out.mkdir(parents=True, exist_ok=True)
    clip = a.out / f"jjk-pans-{int(round(a.seconds))}s.mkv"
    # ffmpeg backs a seek target off by about 0.13 s for containers that do not seek by presentation time, which would land
    # on the index point before the keyframe: ask a little after it. -t before -i limits what is read from the seek point on
    # (after -i it would count the copied absolute timestamps).
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{key + 0.2:.3f}", "-t", f"{a.seconds:.3f}",
                    "-i", str(a.film), "-map", "0:v:0", "-c", "copy", "-copyts", "-an", "-sn", "-dn", str(clip)], check=True)

    # where the copy really starts and ends (packets are counted, not decoded: one video packet per frame in this file)
    t0 = first_frames(ffprobe, clip)[0]
    pk = csv_rows(ffprobe, ["-select_streams", "v:0", "-show_entries", "packet=pts_time", str(clip)])
    pts = sorted(float(r[0]) for r in pk if r and r[0] not in ("", "N/A"))
    frames, t_last = len(pts), pts[-1]
    if t_last - t0 < a.seconds - 1.0:
        raise SystemExit(f"the copy holds {t_last - t0:.1f} s, asked {a.seconds} s")

    # cue sheet: shot starts on perf1's rebased timeline (the clip's first decoded frame at episode time t0 is 0)
    cue_shots, inside = [], []
    for sh in shots:
        if sh["end_s"] <= t0 or sh["start_s"] > t_last:
            continue
        inside.append(sh["id"])
        t = sh["start_s"] - 0.5 / fps - t0
        cue_shots.append({"start": 0.0 if not cue_shots else round(t, 6)})
    cues = {"schema": "panorama.cues/1", "version": 1, "file_hash": clip.name, "room_signature": "setup-a",
            "preferences_hash": "default", "shots": cue_shots}
    (a.out / "cues.json").write_text(json.dumps(cues, indent=1), encoding="utf-8")

    pans = [{"run": r["id"], "shot": r["shot"], "start_s": round(r["start_s"] - t0, 3), "seconds": r["seconds"],
             "direction": r["direction"], "speed_px_s": r["speed_px_s"]}
            for r in slides["runs"] if r["end_s"] > t0 and r["start_s"] < t_last]
    meta = {"schema": "panorama.e1.clip/1", "film": str(a.film), "film_key": facts.get("file", {}).get("key"),
            "asked_keyframe_s": key, "first_frame_episode_s": t0, "last_frame_episode_s": t_last, "frames": frames,
            "seconds": round(t_last - t0 + 1.0 / fps, 3), "pan_seconds_from_asked_keyframe": round(cover, 3),
            "pans_in_clip": pans, "shots_in_clip": inside, "clip": str(clip), "cues": str(a.out / "cues.json"),
            "why": "of the keyframe-aligned windows, the one with the most frames inside slide runs (camera pans) of the "
                   "content model; contains the jjk-pan shot 133 (out/scenes/jjk-pan)"}
    (a.out / "clip.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    print(f"{clip}: episode {t0:.3f}..{t_last:.3f} s (asked from keyframe {key:.3f} s), {frames} frames, "
          f"{len(cue_shots)} shots, {len(pans)} pans ({cover:.1f} s of pan from the asked start)")
    print(f"{a.out / 'cues.json'}: {len(cue_shots)} shots; {a.out / 'clip.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
