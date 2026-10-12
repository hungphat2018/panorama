#!/usr/bin/env python3
"""Run the supported-file matrix: every case in media/cases.json through perf0 (D3D11VA and software decode,
windowed, never full screen) and through stock mpv, then check the pictures and write results/.

  python run-matrix.py                      all cases, perf0 x2 + mpv x2 (software and d3d11va decode)
  python run-matrix.py --only ID ...        some cases
  python run-matrix.py --group codec ...    some groups (codec interlace timing chapters subtitles hdr colour geometry broken real)
  python run-matrix.py --no-mpv-hw          skip mpv with --hwdec=d3d11va
  python run-matrix.py --variant TAG "ARGS"  perf0 only, with extra switches (e.g. "--deinterlace bwdif --rotation"), stored as perf0-<mode>@TAG
                                            (a single option such as --sd-passthrough needs a leading space: " --sd-passthrough")
  python run-matrix.py --sheets [TAG]       rebuild results/sheets/<group>-N.png from the saved frame grabs (TAG: before/after sheets of that variant)
  python run-matrix.py --table              print the result table (markdown) from results/summary.json

Windowed runs only, each <= 15 s of playback, ended by a time limit; the harness also kills a run that
hangs (polite taskkill first) and checks tasklist afterwards. Needs media/cases.json (make-test-media.py).
Results per case: results/<id>/{perf0-d3d11va,perf0-sw,mpv-sw,mpv-hw}.{json,png,stderr.log|log.gz}.
"""
import argparse
import ctypes
import gzip
import json
import os
import re
import subprocess
import sys
import time
from importlib.machinery import SourceFileLoader
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
RESULTS = HERE / "results"
MEDIA = HERE / "media"
PERF0_DIR = ROOT / "spikes" / "performer-p0" / "bin"
FFMPEG_LGPL_BIN = Path(r"C:\Users\tamla\msys64\home\tamla\p0\prefix\ffmpeg-lgpl\bin")
MSYS2_BIN = Path(r"C:\Users\tamla\msys64\ucrt64\bin")
MPV = ROOT / "tools" / "mpv-v0.41.0-x86_64-w64-mingw32" / "mpv.exe"
LUA = HERE / "mpv-matrix-probe.lua"

CHECKS = SourceFileLoader("matrix_picture_checks", str(HERE / "matrix-picture-checks.py")).load_module()
TOOLS = SourceFileLoader("matrix_media_tools", str(HERE / "matrix-media-tools.py")).load_module()

WINDOW = "1920x1080"        # perf0 window and frame-dump size: 1:1 for 1080p sources
MAX_SECONDS = 12.0          # perf0 playback cap, well inside the 15 s limit per run
GROUP_ORDER = ["codec", "interlace", "timing", "chapters", "subtitles", "hdr", "colour", "geometry", "broken", "real"]


# ---------------------------------------------------------------------------------------------
# process handling
# ---------------------------------------------------------------------------------------------

def perf0_env(flavor):
    dirs = [str(PERF0_DIR)] + ([str(FFMPEG_LGPL_BIN)] if flavor == "lgpl" else []) + [str(MSYS2_BIN)]
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join(dirs + [env.get("PATH", "")])
    return env


def stop(p):
    """Polite first (taskkill without /F closes the window), hard kill if it ignores that."""
    subprocess.run(["taskkill", "/PID", str(p.pid)], capture_output=True)
    try:
        p.wait(timeout=4)
    except subprocess.TimeoutExpired:
        p.kill()


def run_proc(cmd, env=None, timeout=30.0):
    """Run to completion; returns (returncode, stdout, stderr, timed_out, wall_s)."""
    t0 = time.perf_counter()
    p = subprocess.Popen([str(c) for c in cmd], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                         encoding="utf-8", errors="replace")
    timed_out = False
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        stop(p)
        try:
            out, err = p.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()
            out, err = p.communicate()
    return p.returncode, out, err, timed_out, time.perf_counter() - t0


def stray_processes():
    """Names of our player processes still alive (tasklist check after each run)."""
    p = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, errors="replace")
    names = [line.split('","')[0].strip('"').lower() for line in p.stdout.splitlines() if line]
    return [n for n in names if n.startswith("perf0") or n == "mpv.exe"]


def cpu_busy_pct(window_s=1.0):
    """Whole-machine CPU use over a short window (GetSystemTimes): context for the 'late' columns."""
    class FT(ctypes.Structure):
        _fields_ = [("lo", ctypes.c_uint32), ("hi", ctypes.c_uint32)]

    def read():
        idle, kern, user = FT(), FT(), FT()
        ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user))
        v = lambda f: (f.hi << 32) | f.lo
        return v(idle), v(kern) + v(user)
    i0, t0 = read()
    time.sleep(window_s)
    i1, t1 = read()
    return round(100.0 * (1 - (i1 - i0) / max(1, t1 - t0)), 1)


def short_error(text, limit=160):
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    keep = [l for l in lines if re.search(r"error|fail|invalid|cannot|no video|not |unsupported|moov", l, re.I)] or lines
    return " | ".join(dict.fromkeys(keep[:3]))[:limit]


# ---------------------------------------------------------------------------------------------
# picture analysis shared by both players
# ---------------------------------------------------------------------------------------------

def analyze_picture(png, case):
    """Automatic checks on a grab; everything is also judged by eye from the contact sheets."""
    if not png or not Path(png).exists():
        return None
    img = CHECKS.load(png)
    b = CHECKS.picture_bbox(img)
    out = {"size": [img.shape[1], img.shape[0]], "bbox": list(b) if b else None}
    if not b:
        out["blank"] = True
        return out
    asp = CHECKS.aspect_of(b)
    out["aspect"] = round(asp, 4)
    exp = case.get("expect", {})
    if "dar" in exp:
        out["expected_aspect"] = round(exp["dar"], 4)
        out["aspect_ok"] = abs(asp / exp["dar"] - 1) < 0.02
    if exp.get("combing") or case["group"] == "interlace":
        out["comb_pct"] = round(CHECKS.comb_score(img, b), 3)
    if "ordered" in exp or case["group"] == "chapters":
        out["left_bar"] = CHECKS.scene_colour(img, b)
    if case["id"].startswith("hdr10-pq-card") or case["id"].startswith("hlg-card"):
        grey, colour = CHECKS.hdr_card_patches(img, b, TOOLS)
        out["grey_rgb"] = [[round(v) for v in p] for p in grey]
        out["colour_rgb"] = [[round(v) for v in p] for p in colour]
        y = [0.2126 * p[0] + 0.7152 * p[1] + 0.0722 * p[2] for p in grey]
        out["grey_luma"] = [round(v, 1) for v in y]
        out["grey_monotonic_steps"] = sum(1 for i in range(11) if y[i + 1] > y[i] + 1.0)
    if case["id"].startswith("color-"):
        got, want = CHECKS.colour_card_patches(img, b, TOOLS)
        diffs = [max(abs(g - w) for g, w in zip(gp, wp)) for gp, wp in zip(got, want)]
        out["patch_rgb"] = [[round(v) for v in p] for p in got]
        out["patch_expected_rgb"] = want
        out["patch_max_abs_error"] = round(max(diffs), 1)
        out["patch_mean_abs_error"] = round(sum(diffs) / len(diffs), 1)
    return out


def ppm_to_png(ppm):
    from PIL import Image
    png = Path(ppm).with_suffix(".png")
    Image.open(ppm).save(png)
    os.remove(ppm)
    return png


# ---------------------------------------------------------------------------------------------
# perf0
# ---------------------------------------------------------------------------------------------

def window_for(case):
    """perf0 window and frame-dump size. 1080p picture: 1920x1080 (1:1). SD interlaced picture: its displayed size at native
    height, so interlace combing is dumped 1:1 and not smeared by a vertical resample."""
    pv = (case.get("probe") or {}).get("video") or {}
    h, w = pv.get("height"), pv.get("width")
    if case["group"] == "interlace" and h and w and h < 1080:
        dar = (case.get("expect") or {}).get("dar") or w / h
        return f"{round(h * dar)}x{h}"
    return WINDOW


def run_perf0(case, mode, flavor, outdir, cap, tag="", extra=()):
    """One perf0 run. `tag`/`extra`: a variant with extra command-line switches (the file-matrix fixes), kept next to the
    plain run as perf0-<mode>@<tag>.*"""
    exe = PERF0_DIR / f"perf0-{flavor}.exe"
    name = f"perf0-{mode}" + (f"@{tag}" if tag else "")
    prefix = outdir / name
    ppm = outdir / f"{name}.ppm"
    secs = min(case["seconds"], cap)
    cmd = [exe, "--file", Path(case["path"]).as_posix(), "--backend", "d3d11", "--decode", mode, "--adapter", "NVIDIA",
           "--size", window_for(case), "--seconds", f"{secs}", "--out", prefix, "--id", f"{case['id']}-{mode}",
           "--dump-ppm", ppm, "--dump-frame", str(case["dump_frame"]), "--log-level", "3", *extra]
    for f in prefix.parent.glob(prefix.name + ".*"):
        f.unlink()
    rc, out, err, timed_out, wall = run_proc(cmd, perf0_env(flavor), timeout=secs + 25)
    (outdir / f"{name}.stderr.log").write_text((err or "")[-60000:], encoding="utf-8")
    res = {"cmd": " ".join(map(str, cmd[1:])), "rc": rc, "timed_out": timed_out, "wall_s": round(wall, 2)}
    jpath = Path(str(prefix) + ".json")
    if jpath.exists():
        j = json.loads(jpath.read_text(encoding="utf-8"))
        c, pb = j.get("counts", {}), j.get("playback", {})
        hist = j.get("refreshes_per_film_frame", {})
        res.update({
            "status": j.get("status"), "error": j.get("error", ""),
            "media": j.get("media"), "first_frame_ms": j.get("first_frame_ms", {}).get("from_process_creation"),
            "decoded": c.get("decoded"), "mapped": c.get("mapped"), "dropped": c.get("dropped_by_queue"),
            "queue_stalls": c.get("queue_stalls"), "late_1p5x": c.get("late_refreshes_over_1p5x_steady"),
            "late_2p5x": c.get("late_refreshes_over_2p5x_steady"), "steady_refreshes": c.get("steady_refreshes"),
            "skipped_est": c.get("skipped_refreshes_estimate"), "hitches_50ms": c.get("hitches_over_50ms_steady"),
            "measured_s": pb.get("measured_s"), "eof": pb.get("eof"), "film_frame_changes": pb.get("film_frames_front_changes"),
            "refreshes_per_film_frame": dict(sorted(hist.items(), key=lambda kv: -kv[1])[:5]),
            "cpu_pct_one_core": j.get("process", {}).get("cpu_pct_of_one_core"),
            "peak_ws_mb": j.get("process", {}).get("peak_working_set_mb"),
        })
        csv = Path(str(prefix) + ".csv")
        if csv.exists():
            csv.unlink()   # per-refresh rows are not kept: the summary numbers are in the JSON
    else:
        res.update({"status": "no-result", "error": short_error(err or out or "")})
    warn = [l for l in (err or "").splitlines() if l.strip()]
    res["stderr_lines"] = len(warn)
    res["stderr_head"] = list(dict.fromkeys(warn))[:6]
    if ppm.exists():
        png = ppm_to_png(ppm)
        res["png"] = png.name
        res["picture"] = analyze_picture(png, case)
    return res


# ---------------------------------------------------------------------------------------------
# stock mpv
# ---------------------------------------------------------------------------------------------

LOG_KEEP = re.compile(r"\[(warn|error|fatal)\]|Using hardware decoding|VO: |Video: |ordered chapter|edition|"
                      r"Failed|cannot|invalid|hwdec|colorspace|interlace|deinterlac", re.I)
LOG_DROP = re.compile(r"auto_profiles|hook command", re.I)


def run_mpv(case, hw, outdir, cap):
    tag = "mpv-hw" if hw else "mpv-sw"
    js, png, log = outdir / f"{tag}.json", outdir / f"{tag}.png", outdir / f"{tag}.log"
    for f in (js, png, log, outdir / f"{tag}.log.gz"):
        if f.exists():
            f.unlink()
    length = min(case["seconds"], cap)
    cmd = [MPV, "--no-config", f"--length={length}", "--geometry=50%", "--ao=null", "--no-osc", "--osd-level=0", "--no-terminal",
           "--msg-level=all=v", f"--log-file={log}", f"--script={LUA}",
           f"--script-opts=matrix-out={js.as_posix()},matrix-shot={png.as_posix()},matrix-shot_at={case['shot_at']},"
           f"matrix-shot_mode={'video' if case['group'] == 'interlace' else 'window'}"]
    if hw:
        cmd.append("--hwdec=d3d11va")
    cmd.append(Path(case["path"]).as_posix())
    rc, out, err, timed_out, wall = run_proc(cmd, timeout=length + 25)
    res = {"cmd": " ".join(map(str, cmd[1:])), "rc": rc, "timed_out": timed_out, "wall_s": round(wall, 2)}
    if js.exists():
        j = json.loads(js.read_text(encoding="utf-8"))
        # mpv's Lua JSON writer turns an empty table into [] : treat every empty or missing table as {}
        vp = j.get("video_params") if isinstance(j.get("video_params"), dict) else {}
        c = j.get("counters") if isinstance(j.get("counters"), dict) else {}
        res.update({"video_params": {k: vp.get(k) for k in ("w", "h", "dw", "dh", "aspect", "par", "rotate", "pixelformat",
                                                          "colormatrix", "colorlevels", "primaries", "gamma", "sig-peak")},
                    "hwdec_current": j.get("hwdec_current"), "current_vo": j.get("current_vo"), "video_format": j.get("video_format"),
                    "first_frame_s": j.get("t_first_frame_s"), "end_reason": j.get("end_reason"), "end_error": j.get("end_error"),
                    "frame_info": j.get("frame_info"), "frame_drop": c.get("frame_drop"), "decoder_drop": c.get("decoder_drop"),
                    "vo_delayed": c.get("vo_delayed"), "mistimed": c.get("mistimed"), "frames": c.get("estimated_frame_number"),
                    "time_pos": c.get("time_pos"), "duration": c.get("duration"),
                    "chapters": len(j.get("chapter_list") or []), "shot_ok": j.get("shot_ok")})
        res["status"] = "ok" if j.get("end_reason") in ("eof", "quit", None) and not j.get("end_error") else "error"
        if j.get("end_error"):
            res["error"] = str(j.get("end_error"))
    else:
        res["status"] = "no-result"
    if log.exists():
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
        res["log_excerpt"] = list(dict.fromkeys(l[:220] for l in lines if LOG_KEEP.search(l) and not LOG_DROP.search(l)))[:14]
        errs = [l for l in lines if re.search(r"\[(error|fatal)\]", l)]
        if errs and res.get("status") != "ok":
            res["error"] = res.get("error") or short_error("\n".join(errs))
        with gzip.open(outdir / f"{tag}.log.gz", "wt", encoding="utf-8") as gz:
            gz.write("\n".join(lines))
        log.unlink()
    if png.exists():
        res["png"] = png.name
        res["picture"] = analyze_picture(png, case)
    return res


# ---------------------------------------------------------------------------------------------
# driver, sheets, table
# ---------------------------------------------------------------------------------------------

def load_cases():
    cases = json.loads((MEDIA / "cases.json").read_text(encoding="utf-8"))
    return [c for c in cases if c.get("exists")]


def load_summary():
    p = RESULTS / "summary.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def save_summary(summary):
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")


def run_case(case, args, summary):
    outdir = RESULTS / case["id"]
    outdir.mkdir(parents=True, exist_ok=True)
    entry = summary.get(case["id"], {})
    entry.update({"group": case["group"], "kind": case["kind"], "desc": case["desc"], "path": case["path"],
                  "machine_cpu_busy_pct_before": cpu_busy_pct(1.0), "probe": case.get("probe"),
                  "structure": case.get("structure"), "expect": case.get("expect")})
    runs = entry.setdefault("runs", {})
    if args.variant:     # second pass: perf0 only, with extra switches, stored beside the plain runs
        tag, extra = args.variant[0], args.variant[1].split()
        for mode in ("d3d11va", "sw"):
            print(f"  perf0 {mode} @{tag} ({' '.join(extra)}) ...", flush=True)
            runs[f"perf0-{mode}@{tag}"] = run_perf0(case, mode, args.flavor, outdir, MAX_SECONDS, tag, extra)
            time.sleep(0.8)
    elif not args.no_perf0:
        for mode in ("d3d11va", "sw"):
            print(f"  perf0 {mode} ...", flush=True)
            runs[f"perf0-{mode}"] = run_perf0(case, mode, args.flavor, outdir, MAX_SECONDS)
            time.sleep(0.8)
    if not args.no_mpv and not args.variant and case.get("mpv", True):
        for hw in (False, True):
            if hw and args.no_mpv_hw:
                continue
            print(f"  mpv {'hw' if hw else 'sw'} ...", flush=True)
            runs["mpv-hw" if hw else "mpv-sw"] = run_mpv(case, hw, outdir, MAX_SECONDS)
            time.sleep(0.8)
    strays = stray_processes()
    entry["strays_after"] = strays
    if strays:
        print(f"  WARNING: still running after the case: {strays}", flush=True)
    summary[case["id"]] = entry
    save_summary(summary)


def build_sheets(variant=None):
    """Contact sheets per group. With `variant` (a --variant tag): perf0 software plain | perf0 software with the switches |
    stock mpv, only for the cases that have that variant run."""
    summary = load_summary()
    sheets = RESULTS / "sheets"
    sheets.mkdir(parents=True, exist_ok=True)
    if variant:
        cols = ["perf0 software", f"perf0 software with switches ({variant})", "stock mpv (software decode)"]
        runs = ("perf0-sw", f"perf0-sw@{variant}", "mpv-sw")
    else:
        cols = ["perf0 D3D11VA", "perf0 software", "stock mpv (software decode)"]
        runs = ("perf0-d3d11va", "perf0-sw", "mpv-sw")
    for g in GROUP_ORDER:
        rows = []
        for cid, e in summary.items():
            if e["group"] != g or (variant and runs[1] not in e["runs"]):
                continue
            paths = []
            for run in runs:
                r = e["runs"].get(run, {})
                paths.append(str(RESULTS / cid / r["png"]) if r.get("png") else None)
            rows.append((f"{cid}: {e['desc'][:95]}", paths))
        if rows:
            # sheets of 4 rows keep each PNG small enough to view
            for k in range(0, len(rows), 4):
                name = f"{g}-{variant}-{k // 4 + 1}.png" if variant else f"{g}-{k // 4 + 1}.png"
                CHECKS.contact_sheet(rows[k:k + 4], sheets / name, cols)
    print(f"sheets written to {sheets}")


def cell(r):
    """One table cell for a run."""
    if not r:
        return "not run"
    if r["status"] in ("no-result",) or r.get("timed_out"):
        return "FAIL " + (("timeout; " if r.get("timed_out") else "") + (r.get("error") or "no result"))[:80]
    if "decoded" in r:     # perf0
        if r["status"] != "ok":
            return "FAIL " + (r.get("error") or "")[:80]
        ff = r.get("first_frame_ms")
        fps = (r.get("media") or {}).get("fps") or 0
        want = fps * (r.get("measured_s") or 0)
        s = f"plays; {r.get('film_frame_changes')}" + (f"/{want:.0f}" if want else "") + \
            f" shown, {r['dropped']} dropped, {r['late_1p5x']} late; first {ff / 1000:.2f} s"
        if r.get("eof"):
            s += "; EOF"
        return s
    # mpv
    if r["status"] != "ok":
        return "FAIL " + (r.get("error") or "")[:80]
    ff = r.get("first_frame_s")
    return f"plays; {r.get('frames')} fr / {r.get('decoder_drop')}+{r.get('frame_drop')} drop; " + (f"{ff:.2f} s" if ff else "-") + \
        (f"; hwdec={r.get('hwdec_current')}" if r.get("hwdec_current") not in (None, "no") else "")


def print_table():
    summary = load_summary()
    print("| group | case | perf0 D3D11VA | perf0 software | mpv software | mpv d3d11va |")
    print("|---|---|---|---|---|---|")
    for g in GROUP_ORDER:
        for cid, e in summary.items():
            if e["group"] == g:
                r = e["runs"]
                print(f"| {g} | {cid} | {cell(r.get('perf0-d3d11va'))} | {cell(r.get('perf0-sw'))} | {cell(r.get('mpv-sw'))} | {cell(r.get('mpv-hw'))} |")
                for k in sorted(r):          # variant runs (perf0-<mode>@<tag>) as extra lines
                    if "@" in k:
                        print(f"| | {cid} {k} | {cell(r[k])} | | | |")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--group", nargs="*")
    ap.add_argument("--no-perf0", action="store_true")
    ap.add_argument("--no-mpv", action="store_true")
    ap.add_argument("--no-mpv-hw", action="store_true")
    ap.add_argument("--variant", nargs=2, metavar=("TAG", "ARGS"),
                    help="perf0 only, with extra switches, e.g. --variant deint '--deinterlace yadif'")
    ap.add_argument("--flavor", default="lgpl", choices=["lgpl", "master"], help="perf0 build: LGPL FFmpeg (the product one) or MSYS2's")
    ap.add_argument("--sheets", nargs="?", const="", metavar="VARIANT",
                    help="rebuild results/sheets; give a variant tag for the before/after sheets of that variant")
    ap.add_argument("--table", action="store_true")
    args = ap.parse_args()
    if args.sheets is not None:
        build_sheets(args.sheets or None)
        return 0
    if args.table:
        print_table()
        return 0
    cases = load_cases()
    if args.only:
        cases = [c for c in cases if c["id"] in args.only]
    if args.group:
        cases = [c for c in cases if c["group"] in args.group]
    if not cases:
        print("no cases selected", file=sys.stderr)
        return 2
    pre = stray_processes()
    if pre:
        print(f"note: player processes already running before the matrix (not ours, left alone): {pre}")
    summary = load_summary()
    for i, c in enumerate(cases, 1):
        print(f"[{i}/{len(cases)}] {c['id']}", flush=True)
        run_case(c, args, summary)
    build_sheets()
    return 0


if __name__ == "__main__":
    sys.exit(main())
