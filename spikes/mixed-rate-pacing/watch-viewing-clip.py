"""Play one viewing clip the way the pacing tests need it, looping until you press q.

CLIP is a file, or a short name for the clips in viewing-clips/:
  ab    pan-ab-laptop.mkv    24 fps as filmed vs smooth, laptop panel
  seam  seam-sim-laptop.mkv  fake bezel, right side 0-17 ms late, laptop panel
  pairs seam-pairs-laptop.mkv  fake bezel, 0 ms alternating with 4, 8, 13 ms every 3 s
  span  pan-span.mkv         24 fps pan across the real seam, both screens
  span-ab pan-span-ab.mkv    the same pan, 24 fps as filmed vs smooth (pan mode), both screens
  anime anime-pan-ab-laptop.mkv  real anime pans: as drawn vs smooth, laptop panel
Clips whose name starts with "pan-span" play fullscreen across every screen;
the others play fullscreen on the laptop panel (screen 0, 240 Hz). With
--refresh DEVICE=HZ a display is switched for the session (unsaved) and switched
back afterwards. On exit it prints mpv's dropped and late frame counts, so a
clean comparison can be told from a stuttering one.

Usage: python watch-viewing-clip.py CLIP [--refresh \\\\.\\DISPLAY2=96] [--seconds N] [--mpv MPV_EXE]
"""
import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_MPV = HERE.parents[1] / "tools" / "mpv-v0.41.0-x86_64-w64-mingw32" / "mpv.exe"
SHORT = {"ab": "pan-ab-laptop.mkv", "seam": "seam-sim-laptop.mkv", "pairs": "seam-pairs-laptop.mkv",
         "span": "pan-span.mkv", "span-ab": "pan-span-ab.mkv", "anime": "anime-pan-ab-laptop.mkv"}
COUNTERS = ("frame-drop-count", "vo-delayed-frame-count", "mistimed-frame-count", "display-fps")
LUA = """
local last = {}
local names = {%s}
mp.add_periodic_timer(1, function()
    for _, n in ipairs(names) do last[n] = mp.get_property(n) or last[n] end
end)
mp.register_event("shutdown", function()
    for _, n in ipairs(names) do print("counter " .. n .. "=" .. tostring(last[n])) end
end)
""" % ", ".join(f'"{c}"' for c in COUNTERS)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("clip")
    parser.add_argument("--refresh", action="append", default=[], help="DEVICE=HZ for this session only")
    parser.add_argument("--seconds", type=float, help="stop after N seconds instead of looping")
    parser.add_argument("--mpv", type=Path, default=DEFAULT_MPV)
    args = parser.parse_args()

    clip = HERE / "viewing-clips" / SHORT[args.clip] if args.clip in SHORT else Path(args.clip)
    screens = ["--fs-screen=all"] if clip.name.startswith("pan-span") else ["--screen=0", "--fs-screen=0"]
    timing = [f"--end={args.seconds}"] if args.seconds else ["--loop-file=inf"]
    refresh_tool = [sys.executable, str(HERE / "display-refresh.py")]
    switched = []
    with tempfile.TemporaryDirectory() as tmp:
        script, log = Path(tmp) / "counters.lua", Path(tmp) / "mpv.log"
        script.write_text(LUA)
        try:
            for item in args.refresh:
                device, hz = item.split("=")
                switched.append(device)
                subprocess.run([*refresh_tool, "set", device, hz], check=True)
            subprocess.run([str(args.mpv), "--no-config", "--fs", *screens, *timing, "--vo=gpu-next",
                            "--gpu-api=d3d11", "--hwdec=d3d11va", "--video-sync=display-resample", "--osc=no",
                            "--osd-level=0", "--cursor-autohide=always", f"--script={script}", f"--log-file={log}",
                            str(clip)])
        finally:
            for device in switched:
                subprocess.run([*refresh_tool, "restore", device])
        found = dict(re.findall(r"counter (\S+)=(.*)", log.read_text(errors="replace"))) if log.exists() else {}
    print("dropped {frame-drop-count}, late {vo-delayed-frame-count}, mistimed {mistimed-frame-count}, "
          "display {display-fps} Hz".format(**{c: found.get(c, "?") for c in COUNTERS}))


if __name__ == "__main__":
    main()
