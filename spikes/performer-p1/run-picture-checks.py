"""The picture path against the picture-path harness's float64 reference, windowed (small windows, a few seconds each, no
full screen, no display-mode change): pass 1's canvas of each monitor is read back (--dump-canvas) and compared with the
reference, and the finished frame is checked against pass 3's maths.

  python -I run-picture-checks.py [--exe bin/perf1-pic-lgpl.exe] [--only fold,curve,...] [--out results-pic/checks]

Cases (all on Setup A's default room at --window-scale 0.5, so 1280x800 and 960x540):
  fold     the harness's grid/circle pattern t1 over both screens, fold surface
  curve    the same on the curved surface (seat 700 mm)
  crop     a quarter of the picture magnified over both screens: the laptop upscales (sigmoid domain), the external shrinks
  slide    a camera slide of 300 mm/s across and 60 mm/s down: each row at its own light time
  blend    two alternating pictures with the rolling blend: a follower refresh that mixes both frames
  glow     the surround fills the leftover area (checked on the finished frame only)
  cutblend the rolling blend across a cut: the two mixed frames belong to different shots, each is seen through its own crop and place
  ramp     flat bands at the darkest sRGB codes: pass 1's canvas holds the codes they were made from (the black point is declared)
  colour   a colour block per monitor (gain, white balance, black lift): pass 3's output against the exact maths (finished frame only)
  subs     the film's own text subtitle track, drawn on the monitor the cue sheet names and on no other (finished frame only)

Pass: the largest difference away from edges stays within 1 code value (the harness's tolerance) on every monitor and the
finished frame is within one code of pass 3's exact value. Needs the screens: skip it while something else uses them.
"""
import argparse
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data", "pic-test")
PATTERNS = os.path.join(HERE, "..", "picture-path", "patterns")
ROOM = os.path.join(HERE, "..", "picture-path", "room", "setup-a-default.json")
HALF_ROOM = os.path.join(DATA, "room-half.json")
BIN = os.path.join(HERE, "bin")
ENV = dict(os.environ, PATH=";".join([BIN, r"C:\Users\tamla\msys64\home\tamla\p0\prefix\ffmpeg-lgpl\bin",
                                      r"C:\Users\tamla\msys64\home\tamla\p0\prefix\libplacebo-master\bin",
                                      r"C:\Users\tamla\msys64\ucrt64\bin", os.environ.get("PATH", "")]))

# name: (film, cue sheet, extra program arguments, dump time, compare against the reference, surround for the final-frame check)
CASES = {
    "fold": ("t1.mkv", None, ["--surround", "black"], 0.4, True, "black"),
    "curve": ("t1.mkv", None, ["--surround", "black", "--surface", "curve"], 0.4, True, "black"),
    "crop": ("t1.mkv", "cue-crop.json", ["--surround", "black"], 0.4, True, "black"),
    "slide": ("t1.mkv", "cue-slide.json", ["--surround", "black"], 0.5, True, "black"),
    "blend": ("alt-t1-t7.mkv", None, ["--surround", "black", "--blend", "rolling"], 0.5, True, "black"),
    "glow": ("t1.mkv", "cue-crop.json", ["--surround", "glow"], 0.4, False, "glow"),
    "cutblend": ("alt-t1-t7.mkv", "cue-cut.json", ["--surround", "black", "--blend", "rolling", "--dump-cut"], 0.9, True, "black"),
    "ramp": ("ramp.mkv", None, ["--surround", "black"], 0.4, "ramp", "black"),
    "colour": ("t1.mkv", None, ["--surround", "black", "--room", os.path.join(DATA, "room-colour.json")], 0.4, False, "black"),
    "subs": ("t1-subs.mkv", "cue-subs.json", ["--surround", "black"], 1.0, False, "text:external"),
}


def run(cmd, **kw):
    t0 = time.time()
    p = subprocess.run(cmd, env=ENV, capture_output=True, text=True, **kw)
    return p, time.time() - t0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", default=os.path.join(BIN, "perf1-pic-lgpl.exe"))
    ap.add_argument("--only", default=None)
    ap.add_argument("--out", default=os.path.join(HERE, "results-pic", "checks"))
    args = ap.parse_args()
    names = args.only.split(",") if args.only else list(CASES)
    failed = []
    for name in names:
        film, cue, extra, at, ref_compare, surround = CASES[name]
        out = os.path.join(args.out, name)
        os.makedirs(out, exist_ok=True)
        cmd = [args.exe, "--windowed", "--window-scale", "0.5", "--decode", "sw", "--file", os.path.join(DATA, film), "--room", ROOM,
               "--seconds", "4", "--dump-canvas", os.path.join(out, "dump"), "--dump-at", str(at), "--out", out, *extra]
        if cue:
            cmd += ["--cues", os.path.join(DATA, cue)]
        p, dt = run(cmd, timeout=120)
        with open(os.path.join(out, "console.txt"), "w", encoding="utf-8") as f:
            f.write(p.stdout + p.stderr)
        dump = os.path.join(out, "dump")
        if p.returncode or not os.path.exists(os.path.join(dump, "laptop.json")) or not os.path.exists(os.path.join(dump, "external.json")):
            print("%-6s FAILED to run (exit %d): %s" % (name, p.returncode, (p.stderr or p.stdout).strip()[-300:]))
            failed.append(name)
            continue
        print("%-6s ran %.0f s" % (name, dt))
        if ref_compare == "ramp":
            q, _ = run([sys.executable, "-I", os.path.join(HERE, "check-ramp.py"), "check", dump])
            print("\n".join("   " + l for l in q.stdout.splitlines() if l.strip()))
            if q.returncode:
                failed.append(name + " (black point)")
        elif ref_compare:
            odd = ["--source-odd", os.path.join(PATTERNS, "t7-1920x1080.ppm")] if film.startswith("alt") else []
            q, _ = run([sys.executable, "-I", os.path.join(HERE, "compare-dump.py"), dump, "--room", HALF_ROOM, "--source-even",
                        os.path.join(PATTERNS, "t1-1920x1080.ppm"), *odd])
            print("\n".join("   " + l for l in q.stdout.splitlines() if l.strip()))
            if q.returncode:
                failed.append(name + " (reference)")
                print("   " + q.stderr.strip()[-400:])
        final_args = ["--text-on", surround[5:]] if surround.startswith("text:") else ["--surround", surround]
        if "--room" in extra:
            final_args += ["--room", extra[extra.index("--room") + 1]]
        q, _ = run([sys.executable, "-I", os.path.join(HERE, "compare-final.py"), dump, *final_args])
        print("\n".join("   " + l for l in q.stdout.splitlines() if l.strip()))
        if q.returncode:
            failed.append(name + " (finished frame)")
    print("RESULT:", "PASS" if not failed else "FAIL " + ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
