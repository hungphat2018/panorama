"""Phase 2 smoke of the AI surround live path: the live worker and the player together on a real episode, one run per call.

    python -I -X utf8 live-smoke.py [--seconds 600] [--windowed] [--no-paint] [--seeks AT:TO,AT:TO] [--start-at S]
           [--kill-worker-at S] [--restart-worker-at S] [--dump-at S] [--no-seam] [--fresh-cache] [--audio] [--subs]
           [--rest 180] [--tag NAME] [--film F --content D] [--perf1 perf1-p3-ai.exe] [--refresh DISPLAY2=96]
           [--vram-reserve-mb 1024] [--painter-vram-gb 7] [--worker-arg=...] [--perf1-arg=...] [--fake-player]

What a run does (children in a kill-on-close job: nothing outlives this script):
  1. rest --rest seconds (user rule: the PC rests between runs), then waits until the GPU is near the first run's
     temperature; refuses to start while another job keeps the GPU busy
  2. nvidia-smi logs clocks every 100 ms (perf1/nvsmi.csv, with perf1/cell.json's clock pairs: the clock events
     e1-analyse.py sets against missed refreshes)
  3. the live worker (live-worker.py) on the film with its content model and the cue sheet (live-cues.py makes it once),
     painting unless --no-paint, exiting 10 s after the player has gone
  4. the player (perf1 --ai-sides) for --seconds: full screen (or --windowed: half-size windows, no display change), the
     same cue sheet and room; --seeks AT:TO,... becomes a control script (at film time AT on the master, seek to TO);
     --kill-worker-at / --restart-worker-at S: wall seconds after the player starts
  5. scores: e1-analyse.py on the player's logs (wrong and missed refreshes per minute, near clock events), the player's
     ai-sides.json and ai-showings.jsonl, the worker's events; writes smoke-summary.json and prints it
Run folder: out/live/smoke/<stamp>-<tag>/ (player logs in perf1/, worker run in worker/).
--fake-player: the stand-in player (live-fake-player.py) instead of perf1, for checks without the screens (phase 3): the
same worker, seeks (at film time, as for perf1) and kill / restart; what each showing would take at its cut goes to
perf1/fake-showings.jsonl; no display change, no timing scores.
"""
import argparse
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
_spec = importlib.util.spec_from_file_location("e1_runner", HERE / "e1-player-under-load.py")
e1 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(e1)

FILM = "C:/Users/tamla/Downloads/Jujutsu.Kaisen.S03E12.FiNAL.MULTi.1080p.WEBRiP.x265-KAF.mkv"
CONTENT = HERE / "out" / "jjk" / "content"
LIVE = HERE / "out" / "live"
PY = [sys.executable, "-I", "-X", "utf8"]
say = e1.say


def cue_sheet(content: Path) -> Path:
    path = LIVE / f"{content.parent.name}-cues.json"
    if not path.exists():
        subprocess.run(PY + [str(HERE / "live-cues.py"), str(content), str(path)], check=True)
    return path


def control_script(run: Path, seeks: str, start_at: float) -> Path:
    lines = []
    if start_at > 0:
        lines.append({"after": 0.5, "cmd": "seek", "t": start_at})
    for item in [s for s in (seeks or "").split(",") if s.strip()]:
        at, to = (float(v) for v in item.split(":"))
        lines.append({"at": at, "cmd": "seek", "t": to})
    if not lines:
        return None
    path = run / "control.jsonl"
    path.write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")
    return path


def worker_command(a, run: Path, cues: Path, store: Path, cache: Path) -> list:
    cmd = PY + [str(HERE / "live-worker.py"), a.film, "--content", str(a.content), "--cues", str(cues), "--store", str(store),
                "--cache", str(cache), "--out", str(run / "worker"), "--exit-after-player-s", "10",
                "--painter-vram-gb", str(a.painter_vram_gb)]
    if a.no_paint:
        cmd.append("--no-paint")
    return cmd + a.worker_arg


def fake_player_command(a, run: Path, store: Path) -> list:
    """The stand-in player: film-time seeks AT:TO become wall-time ones (it plays at 1x from --start-at)."""
    seeks = []
    for item in [s for s in (a.seeks or "").split(",") if s.strip()]:
        at, to = (float(v) for v in item.split(":"))
        seeks.append(f"{max(0.0, at - a.start_at):g}:{to:g}")
    cmd = PY + [str(HERE / "live-fake-player.py"), "--store", str(store), "--seconds", str(a.seconds), "--start-at",
                str(a.start_at), "--margins", a.fake_margins, "--out", str(run / "perf1" / "fake-showings.jsonl")]
    return cmd + (["--seeks", ",".join(seeks)] if seeks else [])


def perf1_command(a, run: Path, cues: Path, store: Path, control: Path) -> list:
    out = run / "perf1"
    out.mkdir(parents=True, exist_ok=True)
    cmd = [str(e1.BIN / a.perf1), "--file", a.film, "--seconds", str(a.seconds), "--cues", str(cues), "--room", str(e1.ROOM),
           "--ai-sides", str(store), "--out", str(out), "--vram-reserve-mb", str(a.vram_reserve_mb)]
    if a.windowed:
        cmd.append("--windowed")
    elif a.refresh and a.refresh.lower() != "none":
        cmd += ["--refresh", a.refresh]
    if not a.audio:
        cmd.append("--no-audio")
    if not a.subs:
        cmd.append("--no-subs")
    if control:
        cmd += ["--control-script", str(control)]
    if a.dump_at is not None:
        cmd += ["--dump-canvas", str(out), "--dump-at", str(a.dump_at)]
    if a.no_seam:
        cmd.append("--ai-no-seam")
    return cmd + a.perf1_arg


def _store_field(store: Path, block: str, name: str):
    """One field of an existing store's header, read without mapping the file (None when there is no store)."""
    from live import side_store as ss
    try:
        with open(store, "rb") as f:
            head = f.read(ss.HEADER_BYTES)
    except OSError:
        return None
    if len(head) < ss.HEADER_BYTES or head[:8] != ss.MAGIC:
        return None
    import numpy as np
    return np.frombuffer(head, ss.BLOCK_DT[block], 1, ss.BLOCK_OFFSET[block])[0][name].item()


def store_uid(store: Path):
    return _store_field(store, "layout", "store_uid")


def store_running(store: Path) -> bool:
    from live import side_store as ss
    return _store_field(store, "worker", "state") == ss.ENUMS["worker_state"]["running"]


def read_jsonl(path: Path) -> list:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def summarize(run: Path, a, meta: dict) -> dict:
    perf1 = run / "perf1"
    res = {"run": str(run), "args": {k: v for k, v in vars(a).items() if k not in ("worker_arg", "perf1_arg")}, **meta}
    ai = perf1 / "ai-sides.json"
    if ai.exists():
        res["player_ai"] = json.loads(ai.read_text(encoding="utf-8"))
    shows = read_jsonl(perf1 / "ai-showings.jsonl")
    fake = [s for s in read_jsonl(perf1 / "fake-showings.jsonl") if "summary" not in s]
    if fake:
        res["fake_player"] = next((s["summary"] for s in read_jsonl(perf1 / "fake-showings.jsonl") if "summary" in s), {})
    res["showings"] = {"count": len(shows or fake), "by_tier": {}}
    for s in shows or fake:
        res["showings"]["by_tier"][str(s["tier"])] = res["showings"]["by_tier"].get(str(s["tier"]), 0) + 1
    # after each seek (a new epoch): how many showings until the first with a canvas, and how much film time that took
    epochs = {}
    for s in shows:
        epochs.setdefault(s["epoch"], []).append(s)
    res["after_seek"] = []
    for ep, ss in sorted(epochs.items()):
        if ep <= 0:
            continue
        first = next((i for i, s in enumerate(ss) if s["tier"] > 0), None)
        res["after_seek"].append({"epoch": ep, "landed_s": ss[0]["pts"], "showings_without_canvas": first if first is not None else len(ss),
                                  "film_s_to_first_canvas": round(ss[first]["pts"] - ss[0]["pts"], 3) if first is not None else None})
    events = []
    for p in (run / "worker").glob("*/events.jsonl"):
        events += read_jsonl(p)
    plates = [e for e in events if e.get("event") == "plate"]
    shots = [e for e in events if e.get("event") == "shot"]
    res["worker"] = {"shots_analysed": len(shots), "t1_made": sum(1 for e in shots if e.get("t1")), "plates": len(plates),
                     "plate_s_median": sorted(e["seconds"] for e in plates)[len(plates) // 2] if plates else None,
                     "seeks_seen": sum(1 for e in events if e.get("event") == "epoch")}
    if fake:                                                      # the stand-in has no refresh timing to score
        (run / "smoke-summary.json").write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
        return res
    # wrong and missed refreshes (E1's scoring)
    r = subprocess.run(PY + [str(HERE / "e1-analyse.py"), str(perf1), "--no-csv"], capture_output=True, text=True)
    res["e1_analyse_rc"] = r.returncode
    summ = perf1 / "e1-summary.json"
    if summ.exists():
        res["timing"] = json.loads(summ.read_text(encoding="utf-8"))
    (run / "smoke-summary.json").write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    return res


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--film", default=FILM)
    ap.add_argument("--content", type=Path, default=CONTENT)
    ap.add_argument("--seconds", type=float, default=600.0)
    ap.add_argument("--windowed", action="store_true")
    ap.add_argument("--no-paint", action="store_true")
    ap.add_argument("--seeks", default="", help="AT:TO,...: at film time AT (master), seek to TO")
    ap.add_argument("--start-at", type=float, default=0.0)
    ap.add_argument("--kill-worker-at", type=float, default=None, help="wall s after the player starts")
    ap.add_argument("--restart-worker-at", type=float, default=None, help="wall s after the player starts")
    ap.add_argument("--dump-at", type=float, default=None, help="film time of the player's canvas dump")
    ap.add_argument("--no-seam", action="store_true")
    ap.add_argument("--fresh-cache", action="store_true", help="an empty cache for this run (default: the shared one)")
    ap.add_argument("--audio", action="store_true")
    ap.add_argument("--subs", action="store_true")
    ap.add_argument("--rest", type=float, default=180.0)
    ap.add_argument("--thermal-tolerance", type=float, default=5.0)
    ap.add_argument("--tag", default="smoke")
    ap.add_argument("--perf1", default="perf1-p3-ai.exe")
    ap.add_argument("--refresh", default="DISPLAY2=96")
    ap.add_argument("--vram-reserve-mb", type=int, default=1024)
    ap.add_argument("--painter-vram-gb", type=float, default=7.0, help="the resident painter's allocator cap (worker budget ~8 GB)")
    ap.add_argument("--allow-busy-gpu", action="store_true")
    ap.add_argument("--fake-player", action="store_true", help="the stand-in player (no screens, no timing scores)")
    ap.add_argument("--fake-margins", default="818,0,1500,275", help="the stand-in's room side widths (L,T,R,B)")
    ap.add_argument("--worker-arg", action="append", default=[])
    ap.add_argument("--perf1-arg", action="append", default=[])
    a = ap.parse_args(argv)

    run = LIVE / "smoke" / f"{time.strftime('%Y%m%d-%H%M%S')}-{a.tag}"
    run.mkdir(parents=True, exist_ok=True)
    cues = cue_sheet(a.content)
    store = LIVE / "side-store.bin"
    cache = run / "cache" if a.fresh_cache else LIVE / "cache"
    control = control_script(run, a.seeks, a.start_at)
    meta = {"started": time.strftime("%Y-%m-%d %H:%M:%S")}
    if not a.allow_busy_gpu:
        util, mem = e1.gpu_busy()
        if util is not None and util > 30:
            raise SystemExit(f"the GPU is {util:.0f} % busy ({mem:.0f} MiB): another job runs; --allow-busy-gpu to start anyway")
    if a.rest > 0:
        say(f"rest {a.rest:.0f} s before the run (user rule: the PC rests between runs); GPU {e1.gpu_temp()} C")
        e1.sleep_s(a.rest)
    meta["temp_start_c"] = e1.gpu_temp()
    job = e1.Job()
    e1.keep_awake(True)
    files, procs = [], {}
    try:
        (run / "perf1").mkdir(parents=True, exist_ok=True)
        f_nv = open(run / "perf1" / "nvsmi.csv", "wb")
        files.append(f_nv)
        meta["clock_pairs"] = [e1.clock_pair()]
        procs["nvsmi"] = job.popen(["nvidia-smi", f"--query-gpu={e1.NVSMI_FIELDS}", "--format=csv", "-lms", "100"],
                                   stdout=f_nv, stderr=subprocess.STDOUT)
        wcmd = worker_command(a, run, cues, store, cache)
        meta["worker_cmd"] = wcmd
        f_w = open(run / "worker-stdout.txt", "wb")
        files.append(f_w)
        old_uid = store_uid(store)
        procs["worker"] = job.popen(wcmd, stdout=f_w, stderr=subprocess.STDOUT, cwd=str(HERE))
        t_wait = time.monotonic() + 180
        while time.monotonic() < t_wait and procs["worker"].poll() is None:   # the worker has made or emptied the store
            uid, running = store_uid(store), store_running(store)
            if uid is not None and uid != old_uid and running:
                break
            time.sleep(0.2)
        else:
            raise SystemExit(f"the worker did not get the store ready (rc {procs['worker'].poll()}): see worker-stdout.txt")
        pcmd = fake_player_command(a, run, store) if a.fake_player else perf1_command(a, run, cues, store, control)
        meta["perf1_cmd"] = pcmd
        say(f"player for {a.seconds:.0f} s{' windowed' if a.windowed else ' full screen'}; worker "
            f"{'without' if a.no_paint else 'with'} the painter; run {run}")
        with open(run / "perf1" / "perf1-stdout.txt", "wb") as so, open(run / "perf1" / "perf1-stderr.txt", "wb") as se:
            t0 = time.monotonic()
            meta["clock_pairs"].append(e1.clock_pair())
            player = job.popen(pcmd, stdout=so, stderr=se, env=None if a.fake_player else e1.perf1_env(),
                               cwd=str(HERE if a.fake_player else e1.PERF1_DIR))
            procs["perf1"] = player
            killed = restarted = False
            end = t0 + a.seconds + 120
            while player.poll() is None and time.monotonic() < end:
                el = time.monotonic() - t0
                if a.kill_worker_at is not None and not killed and el >= a.kill_worker_at:
                    say(f"killing the worker at {el:.1f} s (the player must not notice)")
                    e1.kill_tree(procs["worker"])
                    meta["worker_killed_at_s"] = round(el, 2)
                    killed = True
                if a.restart_worker_at is not None and killed and not restarted and el >= a.restart_worker_at:
                    say(f"starting the worker again at {el:.1f} s")
                    f_w2 = open(run / "worker2-stdout.txt", "wb")
                    files.append(f_w2)
                    procs["worker"] = job.popen(wcmd, stdout=f_w2, stderr=subprocess.STDOUT, cwd=str(HERE))
                    meta["worker_restarted_at_s"] = round(el, 2)
                    restarted = True
                time.sleep(0.25)
            if player.poll() is None:
                say("the player did not stop in time: killed")
                e1.kill_tree(player)
            meta["perf1_rc"] = player.returncode
            meta["clock_pairs"].append(e1.clock_pair())
        try:
            procs["worker"].wait(40)
        except subprocess.TimeoutExpired:
            e1.kill_tree(procs["worker"])
        meta["worker_rc"] = procs["worker"].returncode
    finally:
        for p in procs.values():
            e1.kill_tree(p)
        for f in files:
            f.close()
        e1.keep_awake(False)
        meta["temp_end_c"] = e1.gpu_temp()
        meta["ended"] = time.strftime("%Y-%m-%d %H:%M:%S")
        (run / "run.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
        cell = {"cell": a.tag, "status": "ok" if meta.get("perf1_rc") == 0 else f"perf1 rc {meta.get('perf1_rc')}",
                "clock_pairs": meta.get("clock_pairs", []), "temp_start_c": meta.get("temp_start_c"),
                "temp_end_c": meta.get("temp_end_c"), "perf1_rc": meta.get("perf1_rc")}
        (run / "perf1" / "cell.json").write_text(json.dumps(cell, indent=1), encoding="utf-8")   # what e1-analyse.py reads
    res = summarize(run, a, meta)
    if a.fake_player:
        say(f"stand-in player rc {meta.get('perf1_rc')}, worker rc {meta.get('worker_rc')}; showings "
            f"{res['showings']}; {res.get('fake_player')}")
        say(f"worker: {res['worker']}")
        say(f"summary: {run / 'smoke-summary.json'}")
        return 0
    ai = res.get("player_ai", {})
    tiers = ai.get("film_s_per_tier", {})
    up = ai.get("uploads", {})
    say(f"player rc {meta.get('perf1_rc')}, worker rc {meta.get('worker_rc')}")
    say(f"film time: glow {tiers.get('glow', 0):.1f} s, mosaic {tiers.get('mosaic', 0):.1f} s, plate {tiers.get('plate', 0):.1f} s; "
        f"canvases copied {up.get('done')}, torn {up.get('torn')}, chunk CPU p99 {up.get('chunk_cpu_ms', {}).get('p99')} ms")
    say(f"worker: {res['worker']}; after seeks: {res['after_seek']}")
    for cell in (res.get("timing") or {}).get("cells", []):
        for name, mon in cell.get("monitors", {}).items():
            say(f"timing {name}: {mon.get('refresh_hz')} Hz, wrong {mon.get('wrong_per_min')}/min, missed "
                f"{mon.get('missed_per_min')}/min, near clock events {mon.get('near_clock_event')}")
        if cell.get("flags"):
            say(f"timing flags: {cell['flags']}")
    say(f"summary: {run / 'smoke-summary.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
