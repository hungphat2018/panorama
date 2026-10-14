"""Side store self-test: the Python worker end (live/side_store.py) against the player's C reader
(spikes/performer-p1/src/perf1-ai-sides-store.c, through bin/ai-sides-selftest.exe). No GPU, no window, a few seconds.

  1. round trip   every field of every block and record type written here with known values, read back by the C reader
                  (dump), compared field by field: the layout file, the Python dtypes and the C structs agree; a patch
                  page (schema 2) made by live/structure.Page and read back through the C page reader.
  2. concurrency  a fake worker rewrites frame plans and canvas slots as fast as it can, writing garbage first and the
                  right values last inside each record's odd-counter window, while the C reader acts as a player for
                  --seconds (watch): copies kept must never hold garbage (corrupt 0, wrong frame plans 0), torn copies
                  must be caught, evictions and slot reuse must be survived; the player block it writes must read back
                  consistent here.

    python -I -X utf8 live-store-selftest.py [--seconds 8] [--exe spikes/performer-p1/bin/ai-sides-selftest.exe]
Exit code 0 = all checks passed. Compile the reader first: pwsh -File spikes/performer-p1/compile.ps1 -Program ai-sides-test
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)                                          # python -I leaves the script's folder off sys.path
from live import side_store as ss  # noqa: E402
from live import structure  # noqa: E402

REPO = os.path.dirname(os.path.dirname(HERE))
DEFAULT_EXE = os.path.join(REPO, "spikes", "performer-p1", "bin", "ai-sides-selftest.exe")
MSYS_BIN = r"C:\Users\tamla\msys64\ucrt64\bin"                    # the reader's C runtime, as bin/run.cmd puts it on PATH

checks, failed = 0, 0


def check(cond, what):
    global checks, failed
    checks += 1
    if not cond:
        failed += 1
        print(f"FAIL {what}", flush=True)


def run_reader(exe, *args, timeout=120):
    env = dict(os.environ, PATH=MSYS_BIN + os.pathsep + os.environ.get("PATH", ""))
    r = subprocess.run([exe, *args], capture_output=True, text=True, env=env, timeout=timeout)
    if r.returncode not in (0, 1):
        raise SystemExit(f"{exe} {args[0]} failed ({r.returncode}): {r.stderr.strip()}")
    return r.returncode, json.loads(r.stdout)


def pattern(canvas_id, version, w, h):
    """The test pattern ai-sides-selftest.c checks: R = (x + 7 id), G = (y + 13 version), B = x ^ y ^ id, A = 255."""
    x = np.arange(w, dtype=np.uint32)[None, :]
    y = np.arange(h, dtype=np.uint32)[:, None]
    img = np.empty((h, w, 4), np.uint8)
    img[..., 0] = (x + 7 * canvas_id) & 255
    img[..., 1] = (y + 13 * version) & 255
    img[..., 2] = (x ^ y ^ canvas_id) & 255
    img[..., 3] = 255
    return img


def fnv1a(data: bytes) -> str:
    h = 1469598103934665603
    for b in data:
        h = ((h ^ b) * 1099511628211) & 0xFFFFFFFFFFFFFFFF
    return f"{h:016x}"


def f32s(values):
    """float32 values as the C reader prints them (%.9g) and as Python reads them back: compare after rounding to float32."""
    return [float(np.float32(v)) for v in values]


# ---------------------------------------------------------------- 1. round trip

def round_trip(exe, tmp):
    path = os.path.join(tmp, "round-trip.side")
    st = ss.SideStore.create(path, n_frames=50, max_shots=8, heap_slots=4, heap_slot_bytes=64 << 10, film_bytes=530562886,
                             src_w=1920, src_h=1080, film_name="Jujutsu.Kaisen.S03E12 test.mkv")
    times = np.arange(50, dtype=np.float64) * (1001 / 24000)
    st.publish_frame_times(times)
    m = np.array([[1.001, 0.002, -float(i) - 0.25, -0.003, 0.999, 2.5 * i, 1.5e-5, -2.25e-6] for i in range(10, 20)],
                 np.float32)
    gain = np.array([[1.0 + i / 100, 0.5, 2.0, 0.25] for i in range(10, 20)], np.float32)
    state = np.tile(np.array([3, 1, 6, 4], np.uint8), (10, 1))
    flags = np.full(10, 1, np.uint32)
    flags[0] |= 2
    st.write_frame_plan(10, 1, m, gain, state, flags)
    st.write_shot(1, flags=1 | 2 | 16, first_frame=10, n_frames=10, canvas_id=1, key_frame=10, crop=[0, 125, 1920, 953],
                  start_s=(times[9] + times[10]) / 2, end_s=(times[19] + times[20]) / 2, best_tier=2)
    st.write_shot(0, flags=1, first_frame=0, n_frames=10, canvas_id=0, key_frame=0, crop=[0, 0, 1920, 1080], start_s=0.0,
                  end_s=(times[9] + times[10]) / 2)
    img = pattern(1, 2, 37, 21)
    st.finish_slot(2, img, canvas_id=1, version=2, tier=2, world_x0=-820.5, world_y0=-3.25, scale=2.0,
                   valid_from_s=0.41666, recipe=0x0123456789ABCDEF, shot_hint=1)
    st.finish_slot(0, pattern(0, 1, 8, 4), canvas_id=0, version=1, tier=1, world_x0=0.0, world_y0=0.0, scale=1.0,
                   valid_from_s=0.0, recipe=7, shot_hint=0)
    st.evict_slot(0)
    # a patch page: two persistent strips and an overlay, through structure.pack (the worker's path)
    rng = np.random.default_rng(1)
    patches = [structure.Patch(frame=12, kind=structure.PERSISTENT, i0=-40, j0=7, rgba=rng.integers(0, 256, (30, 3, 4), np.uint8),
                               side=0),
               structure.Patch(frame=13, kind=structure.PERSISTENT, i0=-43, j0=7, rgba=rng.integers(0, 256, (30, 3, 4), np.uint8),
                               side=0),
               structure.Patch(frame=14, kind=structure.OVERLAY, i0=100, j0=60, rgba=rng.integers(0, 256, (9, 11, 4), np.uint8),
                               side=2, until=16)]
    page = structure.pack(patches, 32 << 10)
    gmap = page.grid_map(np.float64([[0.5, 0.0, 10.0], [0.0, 0.5, -4.0], [0.0, 0.0, 1.0]]))
    st.finish_page(3, {"shot": 1, "grid_w": page.grid_w, "grid_h": page.grid_h, "first_frame": 10, "n_frames": 10,
                       "grid_map": gmap.ravel()}, page.store_table(ss.PATCH_DT), page.texture, canvas_id=1, version=5,
                   tier=3, valid_from_s=0.41666, recipe=9, shot_hint=1)
    page_sum = fnv1a(np.ascontiguousarray(page.texture).tobytes())
    w = st.worker
    w["frames_published"], w["analysed_frames"], w["painter_state"] = 50, 10, 3
    w["plates_done"], w["plates_skipped"], w["cache_hits"], w["throttle_events"] = 4, 5, 6, 7
    w["painter_s_per_plate"], w["epoch_seen"] = 14.25, 3
    st.set_state("running")
    st.beat("round trip")
    p = st.player                                                 # the player's block, written here only for this test
    p["seq"], p["pid"], p["state"], p["epoch"] = 6, 4242, 1, 3
    p["playhead_s"], p["speed"], p["slack_ms"], p["late_recent"], p["late_total"] = 12.345678901234, 1.0, 2.5, 4, 99
    p["need_margin_px"], p["need_valid"], p["src_w"], p["src_h"] = [820.25, 0.0, 1504.5, 277.75], 1, 1920, 1080
    p["uploading_slot"], p["uploading_gen"], p["showing_shot"], p["showing_tier"], p["showing_canvas"] = 2, 10, 1, 2, 1
    p["resident_n"], p["uploads_done"], p["uploads_torn"] = 3, 17, 2
    expected_sum = fnv1a(np.ascontiguousarray(img).tobytes())
    uid = int(st.layout["store_uid"])
    st.close()

    rc, d = run_reader(exe, "dump", path)
    check(rc == 0, f"dump exit code {rc}")
    lay = d["layout"]
    check(lay["schema_major"] == 2 and lay["max_frames"] == 50 and lay["max_shots"] == 8 and lay["heap_slots"] == 4,
          f"layout sizes {lay}")
    check(lay["store_uid"] == uid, "store_uid")
    check(lay["off_heap"] % 4096 == 0 and lay["heap_slot_bytes"] == 64 << 10, "heap alignment")
    wk = d["worker"]
    for k, v in {"frames_published": 50, "analysed_frames": 10, "painter_state": 3, "plates_done": 4, "plates_skipped": 5,
                 "cache_hits": 6, "throttle_events": 7, "epoch_seen": 3, "src_w": 1920, "src_h": 1080, "state": 1,
                 "film_bytes": 530562886, "beat": 1, "film_name": "Jujutsu.Kaisen.S03E12 test.mkv",
                 "status": "round trip"}.items():
        check(wk[k] == v, f"worker.{k} = {wk[k]!r}, expected {v!r}")
    check(wk["painter_s_per_plate"] == 14.25, "worker.painter_s_per_plate")
    check(wk["qpc"] > 0 and wk["qpc_freq"] > 0 and wk["pid"] == os.getpid(), "worker qpc / pid")
    pl = d["player"]
    for k, v in {"seq": 6, "pid": 4242, "state": 1, "epoch": 3, "playhead_s": 12.345678901234, "speed": 1.0, "slack_ms": 2.5,
                 "late_recent": 4, "late_total": 99, "need_margin_px": [820.25, 0.0, 1504.5, 277.75], "need_valid": 1,
                 "uploading_slot": 2, "uploading_gen": 10, "showing_shot": 1, "showing_tier": 2, "showing_canvas": 1,
                 "resident_n": 3, "uploads_done": 17, "uploads_torn": 2}.items():
        check(pl[k] == v, f"player.{k} = {pl[k]!r}, expected {v!r}")
    frames = d["frames"]
    check(len(frames) == 50, f"{len(frames)} frames read")
    for f in frames:
        i = f["i"]
        check(f["pts"] == times[i], f"frame {i} pts {f['pts']!r} != {times[i]!r}")
        check(f["find"] == i, f"find_frame({times[i]}) = {f['find']}, expected {i}")
        if 10 <= i < 20:
            k = i - 10
            check(f["shot"] == 1 and f["seq"] == 2 and f["flags"] == (3 if k == 0 else 1), f"frame {i} shot/seq/flags {f}")
            check(f32s(f["h"]) == f32s(m[k]) and f32s(f["gain"]) == f32s(gain[k]) and f["state"] == [3, 1, 6, 4],
                  f"frame {i} plan {f}")
        else:
            check(f["shot"] == -1 and f["flags"] == 0, f"frame {i} should be unplanned: {f}")
    shots = d["shots"]
    check(len(shots) == 2, f"{len(shots)} shots read")
    s1 = shots[1]
    check(s1["flags"] == 19 and s1["first_frame"] == 10 and s1["n_frames"] == 10 and s1["canvas_id"] == 1 and
          s1["key_frame"] == 10 and s1["crop"] == [0, 125, 1920, 953] and s1["best_tier"] == 2, f"shot 1 {s1}")
    check(s1["start_s"] == (times[9] + times[10]) / 2 and s1["end_s"] == (times[19] + times[20]) / 2, "shot 1 times")
    cv = d["canvases"]
    c2 = cv[2]
    for k, v in {"gen": 2, "state": 1, "canvas_id": 1, "version": 2, "tier": 2, "format": 1, "tex_w": 37, "tex_h": 21,
                 "row_bytes": 148, "shot_hint": 1, "world_x0": -820.5, "world_y0": -3.25, "scale": 2.0, "valid_from_s": 0.41666,
                 "bytes": 37 * 21 * 4, "recipe": 0x0123456789ABCDEF, "pixels_fnv1a": expected_sum}.items():
        check(c2[k] == v, f"canvas slot 2 {k} = {c2[k]!r}, expected {v!r}")
    check(c2["kind"] == 0 and c2["shot"] == -1 and c2["n_patches"] == 0, f"canvas slot 2 kind/shot: {c2}")
    check(cv[0]["state"] == 2 and cv[0]["gen"] == 4, f"slot 0 evicted with an even gen: {cv[0]}")
    check(cv[1]["state"] == 0 and cv[1]["gen"] == 0, f"slot 1 untouched: {cv[1]}")
    c3 = cv[3]
    th, tw = page.texture.shape[:2]
    for k, v in {"gen": 2, "state": 1, "kind": 1, "shot": 1, "canvas_id": 1, "version": 5, "tier": 3, "n_patches": 3,
                 "page_header_bytes": 4096, "tex_w": tw, "tex_h": th, "row_bytes": tw * 4, "bytes": tw * th * 4,
                 "pixels_fnv1a": page_sum}.items():
        check(c3[k] == v, f"page slot 3 {k} = {c3[k]!r}, expected {v!r}")
    pg = c3.get("page")
    check(pg is not None, "the C page reader refused the page")
    if pg is not None:
        check(pg["shot"] == 1 and pg["grid_w"] == page.grid_w and pg["grid_h"] == page.grid_h and pg["first_frame"] == 10
              and pg["n_frames"] == 10, f"page header {pg}")
        check(pg["grid_map"] == [float(v) for v in gmap.ravel()], f"page grid map {pg['grid_map']} != {gmap.ravel()}")
        want = [[int(r[f]) for f in ("frame", "until", "kind", "side", "dst_x", "dst_y", "w", "h", "src_x", "src_y")]
                for r in page.store_table(ss.PATCH_DT)]
        check(pg["patches"] == want, f"page patches {pg['patches']} != {want}")
        check(want[0][4:6] == [3, 0] and want[1][4:6] == [0, 0] and want[2][2] == 2 and want[2][1] == 16,
              f"patch destinations relative to the grid / overlay range: {want}")
    print(f"round trip: {checks - failed}/{checks} checks passed", flush=True)


# ---------------------------------------------------------------- 2. concurrency

class FakeWorker(threading.Thread):
    """Rewrites frame plans and canvas slots as fast as it can, garbage first inside every odd-counter window."""

    def __init__(self, store, n_frames, stop):
        super().__init__(daemon=True)
        self.st, self.n, self.stop = store, n_frames, stop
        self.versions = {}
        self.writes = self.evictions = self.plan_writes = 0
        self.player_reads = self.player_torn = 0
        self.player_bad = 0

    def write_canvas(self, slot, canvas_id, rng):
        st = self.st
        version = self.versions.get(canvas_id, 0) + 1
        self.versions[canvas_id] = version
        w, h = int(rng.integers(64, 400)), int(rng.integers(16, 200))
        st.begin_slot(slot)
        px = np.frombuffer(st.slot_pixels(slot), np.uint8, h * w * 4).reshape(h, w, 4)
        px[...] = 0xAA                                            # garbage a torn copy would show
        time.sleep(0.0002)
        st.finish_slot(slot, pattern(canvas_id, version, w, h), canvas_id=canvas_id, version=version, tier=1 + version % 2,
                       world_x0=0.0, world_y0=0.0, scale=2.0, valid_from_s=0.0, recipe=1, shot_hint=canvas_id)
        self.writes += 1

    def write_plans(self, i0, n):
        rows = self.st.frames[i0:i0 + n]
        rows["seq"] += 1
        rows["h"] = 12345.0                                       # garbage first
        rows["gain"] = -1.0
        idx = np.arange(i0, i0 + n, dtype=np.float32)
        m = np.zeros((n, 8), np.float32)
        m[:, 0], m[:, 2], m[:, 4], m[:, 5] = 1, -idx, 1, -2 * idx
        rows["h"] = m
        rows["gain"] = (1.0 + idx / 1000.0)[:, None]
        rows["shot"] = 0
        rows["flags"] = 1
        rows["seq"] += 1
        self.plan_writes += 1

    def run(self):
        rng = np.random.default_rng(5)
        st = self.st
        k = 0
        while not self.stop.is_set():
            k += 1
            slot = int(rng.integers(0, st.heap_slots))
            if rng.random() < 0.1:
                st.evict_slot(slot)
                self.evictions += 1
            else:
                self.write_canvas(slot, int(rng.integers(0, 40)), rng)
            i0 = int(rng.integers(0, self.n - 64))
            self.write_plans(i0, 64)
            if k % 20 == 0:
                st.beat("stress")
                p = st.read_player()
                self.player_reads += 1
                if p is None:
                    self.player_torn += 1
                elif p["state"] == 1 and not (p["speed"] == 1.0 and p["epoch"] == 1 and p["slack_ms"] == 2.0):
                    self.player_bad += 1


def concurrency(exe, tmp, seconds):
    path = os.path.join(tmp, "stress.side")
    n = 4000
    st = ss.SideStore.create(path, n_frames=n, max_shots=4, heap_slots=8, heap_slot_bytes=512 << 10, film_bytes=1, src_w=1920,
                             src_h=1080, film_name="stress")
    st.publish_frame_times(np.arange(n, dtype=np.float64) / 24.0)
    st.set_state("running")
    stop = threading.Event()
    fw = FakeWorker(st, n, stop)
    fw.start()
    try:
        rc, s = run_reader(exe, "watch", path, str(seconds), timeout=seconds + 60)
    finally:
        stop.set()
        fw.join()
    print(f"concurrency: reader {s}", flush=True)
    print(f"concurrency: worker canvases {fw.writes}, evictions {fw.evictions}, plan writes {fw.plan_writes}, player block "
          f"reads {fw.player_reads} (torn {fw.player_torn}, inconsistent {fw.player_bad})", flush=True)
    check(rc == 0, f"watch exit code {rc}")
    check(s["corrupt_copies"] == 0, "a canvas copy that passed its counter held garbage")
    check(s["frame_plans_wrong"] == 0, "a frame plan that passed its counter held garbage")
    check(s["canvas_copies_kept"] > 50, "too few canvas copies kept to mean anything")
    check(s["frames_checked"] > 50, "too few frame plans read")
    check(s["evictions_seen"] > 0, "no eviction seen")
    check(s["worker_beats_seen"] > 0, "no worker beat seen")
    check(fw.player_bad == 0, "the player block read back inconsistent")
    if s["torn_copies_caught"] == 0:
        print("note: no torn canvas copy happened this run (the race window was never hit); the counter check is "
              "untested by this run", flush=True)
    st.close()


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--seconds", type=float, default=8.0)
    ap.add_argument("--exe", default=DEFAULT_EXE)
    a = ap.parse_args(argv)
    if not os.path.isfile(a.exe):
        raise SystemExit(f"{a.exe} is missing: pwsh -File spikes/performer-p1/compile.ps1 -Program ai-sides-test")
    with tempfile.TemporaryDirectory(prefix="side-store-") as tmp:
        round_trip(a.exe, tmp)
        concurrency(a.exe, tmp, a.seconds)
    print(f"{'PASS' if not failed else 'FAIL'}: {checks - failed}/{checks} checks", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
