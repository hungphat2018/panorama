"""Live worker: the AI surround's sides, made ahead of the player's playhead and served through the side store
(plans/261013-0718-ai-surround-live-path, phase 2; contracts in live/__init__.py and live/side-store.layout.json).

    python -I -X utf8 live-worker.py FILM [--store out/live/side-store.bin] [--content DIR] [--cues FILE]
           [--cache out/live/cache] [--out out/live/runs] [--no-paint] [--painter-port 8191] [--painter-dit FILE]
           [--painter-cond FILE] [--painter-vram-gb 7] [--big-plate-min-s 3] [--identity-refs]
           [--threads 4] [--heap-slots 48] [--slot-mb 16] [--scale 2] [--horizon-s 60] [--lookahead-s 120]
           [--margins 960,0,960,0] [--margin-wait-s 20] [--late-limit 10] [--slack-ms 0] [--exit-after-player-s 10]

Then the player: perf1 --ai-sides <the same store> (plus its usual --cues, --room...). Either may start first.

What runs (threads of this process, at below-normal priority; children die with it through a kill-on-close job):
  warm      once the room's margins are known (rounded up to 32 source px, so the cache serves a windowed player too):
            publishes the shots analysed in earlier runs whose mosaic for this room is cached, with their canvases, then
            starts analyse (a shot cached without this room's mosaic is analysed again)
  analyse   decodes forward from the shot after the playhead's (at most --lookahead-s ahead, then at film speed;
            shots behind the playhead only while it does not play), analyses each shot (projective camera path, step plan,
            gains, exit strips, kept frames) and publishes its side plan; with --patches also its patch page (T3, retired
            since X1c: the strips the picture leaves behind as the camera moves); makes its T1 mosaic canvas as soon as
            the room's margins are known (from the player block: need_margin_px; --margins when no player shows up
            within --margin-wait-s)
  paint     T2 plates from the resident painter service (live/painter.py: the merged, quantized painter loaded once and
            never moved, capped at --painter-vram-gb), earliest deadline first among shots it can still finish, long
            shots (>= --big-plate-min-s) first; none while the player reports pressure (the throttle)
  main      10 times a second: reads the player block (playhead, seeks, pressure), keeps the canvases needed within
            --horizon-s in the heap, counts, beats
--content: the film's content model (spikes/film-analysis): its frame table and shots; without one the frame table comes
from the packet list and the shots from a v0 cut detector. --cues: the same cue sheet the player plays (its crops).
Writes <out>/<stamp>/worker.log and events.jsonl; caches under <cache>/<film key>/ (re-watches and seeks back come from
there, also in later runs).
"""
import argparse
import collections
import json
import os
import sys
import threading
import time
from typing import Optional

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)                                          # python -I leaves the script's folder off sys.path
from live import film_timeline, mosaic_t1, set_atlas, side_cache, side_store, structure  # noqa: E402
from live.analyse_v0 import Analyser, ShotAnalysis  # noqa: E402
from live.painter import PainterConfig  # noqa: E402
from live.painter_jobs import PainterJobs, painter_for  # noqa: E402
from live.worker_sets import SetKeeper  # noqa: E402
from live.worker_state import TIER_PLATE, WorkerState  # noqa: E402
from film_analysis.proc import JobObject, lower_own_priority  # noqa: E402

PLAYER = side_store.ENUMS["player_state"]
OUT = os.path.join(HERE, "out", "live")


class Log:
    def __init__(self, run_dir: str):
        os.makedirs(run_dir, exist_ok=True)
        self.text = open(os.path.join(run_dir, "worker.log"), "a", encoding="utf-8")
        self.events = open(os.path.join(run_dir, "events.jsonl"), "a", encoding="utf-8")
        self.t0 = time.monotonic()
        self.lock = threading.Lock()

    def __call__(self, msg: str, event: dict = None, echo: bool = True):
        stamp = time.monotonic() - self.t0
        with self.lock:
            line = f"[{stamp:8.2f}] {msg}"
            self.text.write(line + "\n")
            self.text.flush()
            if event is not None:
                self.events.write(json.dumps({"t": round(stamp, 3), **event}) + "\n")
                self.events.flush()
        if echo:
            print(line, flush=True)


def open_store(path: str, film, max_shots: int, heap_slots: int, slot_bytes: int, log) -> side_store.SideStore:
    """The store for this run: an existing store file that fits is emptied in place with a new store_uid (a player may
    have it mapped, and Windows refuses to re-create a mapped file; the new id tells that player to drop what it took from
    the old contents); otherwise a new file."""
    if os.path.isfile(path):
        try:
            st = side_store.SideStore.open(path)
        except (OSError, ValueError) as e:
            st = None
            log(f"store: {path} is not a usable store ({e}): making a new one")
        if st is not None:
            if st.fits(film.n_frames, max_shots):
                st.reset(film.size, film.src_w, film.src_h, os.path.basename(film.path))
                log(f"store: {path} emptied in place with a new id")
                return st
            st.close()
    try:
        return side_store.SideStore.create(path, film.n_frames, max_shots, heap_slots, slot_bytes, film.size, film.src_w,
                                           film.src_h, os.path.basename(film.path))
    except OSError as e:                                           # mapped by a player and too small for this film
        raise SystemExit(f"{path} cannot be made ({e}): stop the player that has it open, or use another --store")


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("film")
    ap.add_argument("--store", default=os.path.join(OUT, "side-store.bin"))
    ap.add_argument("--content", default=None, help="the film's content model folder")
    ap.add_argument("--cues", default=None, help="the cue sheet the player plays (its crops)")
    ap.add_argument("--cache", default=os.path.join(OUT, "cache"))
    ap.add_argument("--out", default=os.path.join(OUT, "runs"))
    ap.add_argument("--no-paint", action="store_true", help="T1 mosaics only (no GPU)")
    pdef = PainterConfig()
    ap.add_argument("--painter-port", type=int, default=pdef.port)
    ap.add_argument("--painter-dit", default=pdef.dit, help="the painter's DiT (painter-quantize.py's file)")
    ap.add_argument("--painter-cond", default=pdef.cond, help="the painter's conditioning, encoded once ahead")
    ap.add_argument("--painter-vram-gb", type=float, default=pdef.vram_cap_gb,
                    help="the painter's allocator cap (its pool is reserved at start; the worker's budget is ~8 GB)")
    ap.add_argument("--big-plate-min-s", type=float, default=3.0, help="sets on screen this long get the painter first (X5)")
    ap.add_argument("--identity-refs", action="store_true",
                    help="plates get the crop of a character cut by the seam (<cache>/identity/clusters.json), read once "
                         "by the encoder on the CPU (live/identity_encoder.py)")
    ap.add_argument("--no-sets", action="store_true", help="every shot its own set (no registration, no shared plates)")
    ap.add_argument("--patches", action="store_true",
                    help="T3 (retired since X1c): pack, cache and publish each shot's patch page for perf1 --ai-patches. "
                         "A shot cached by a run without it is analysed again (its page is made)")
    ap.add_argument("--threads", type=int, default=4, help="decoder threads")
    ap.add_argument("--heap-slots", type=int, default=48, help="canvases and patch pages (a slot each)")
    ap.add_argument("--slot-mb", type=float, default=16.0)
    ap.add_argument("--scale", type=float, default=2.0, help="source px per canvas texel")
    ap.add_argument("--horizon-s", type=float, default=60.0, help="canvases kept in the heap this far ahead")
    # the analyser's lead cap: past it the analyser follows the playhead at film speed (its CPU share follows the
    # playhead's need; at 600 s it ran at full speed for most of a 10-min screen run, and wrong frames at 240 Hz rose).
    # 120 s covers the painter's choice (the earliest feasible shot), the heap's horizon and the 2-min catch-up.
    ap.add_argument("--lookahead-s", type=float, default=120.0, help="analysis at most this far ahead of the playhead")
    ap.add_argument("--margins", default="960,0,960,0", help="L,T,R,B source px when no player gives its own")
    ap.add_argument("--margin-wait-s", type=float, default=20.0)
    # E1's no-load runs miss refreshes in bursts at GPU clock changes (8 % of seconds with 10 or more at 240 Hz), and the
    # present lead (median ~4 ms) barely moves under the painter: only a heavier burst or a late present call is pressure
    ap.add_argument("--late-limit", type=int, default=10, help="player misses in 1 s that count as pressure")
    ap.add_argument("--slack-ms", type=float, default=0.0, help="present slack below which the player is under pressure")
    ap.add_argument("--exit-after-player-s", type=float, default=0.0, help="exit when a player was seen and has been gone this long (0: never)")
    a = ap.parse_args(argv)
    try:
        fallback_margins = side_cache.room_margins(a.margins.split(","))
    except ValueError:
        fallback_margins = ()
    if len(fallback_margins) != 4:
        ap.error("--margins takes four numbers: L,T,R,B source px")

    pcfg = PainterConfig(dit=a.painter_dit, cond=a.painter_cond, vram_cap_gb=a.painter_vram_gb, port=a.painter_port,
                         identity_refs=a.identity_refs)
    lower_own_priority()
    job = JobObject(kill_on_close=True)                            # ffmpeg and the painter service die with this process
    cv2.setNumThreads(2)
    cv2.ocl.setUseOpenCL(False)                                    # CPU only: the GPU belongs to the player and the painter
    run_dir = os.path.join(a.out, time.strftime("%Y%m%d-%H%M%S"))
    log = Log(run_dir)
    log(f"live worker: {a.film} (run {run_dir})")

    film = film_timeline.open_film(a.film, a.content, a.cues)
    log(f"film: {film.n_frames} frames, {film.duration_s:.1f} s, {film.src_w}x{film.src_h}, matrix {film.matrix} {film.in_range}, "
        f"{'content model: %d shots' % len(film.spans) if film.spans else 'no content model: v0 cut detector'}, "
        f"{'cue sheet crops' if film.cue_starts else 'whole-frame crops'}")
    max_shots = 4 * len(film.spans) + 256 if film.spans else film.n_frames // 4 + 16   # room for hidden-cut splits
    store = open_store(a.store, film, max_shots, a.heap_slots, int(a.slot_mb * (1 << 20)), log)
    cache = side_cache.SideCache(a.cache, film.key_folder)
    state = WorkerState(film, store, cache, log)
    store.publish_frame_times(film.times)
    store.set_state("running")
    store.beat("starting")

    margins = {"value": None}
    margins_ready = threading.Event()
    stats = {"t1": 0, "t1_empty": 0, "shots": 0, "cache_hits": 0, "pages": 0}
    sets = SetKeeper(film, state, cache, log, a.scale, store.heap_slot_bytes, enabled=not a.no_sets)
    recent = collections.OrderedDict()                             # row -> Mosaic: the last few made, in RAM

    def mosaic_of(row: int):
        m = recent.get(row)
        if m is not None:
            return m
        info = state.shots.get(row)
        return cache.load_mosaic(info.first, info.n, info.mosaic_key) if info is not None and info.mosaic_key else None

    def mosaic_meta_of(row: int):
        m = recent.get(row)
        if m is not None:
            return {"side_share": m.side_share}
        info = state.shots.get(row)
        return cache.mosaic_meta(info.first, info.n, info.mosaic_key) if info is not None and info.mosaic_key else None

    page_bytes = store.heap_slot_bytes - (64 << 10)               # room for the page's header and patch table

    def publish_page(an: ShotAnalysis, set_id: int, has_page: bool):
        """The shot's patch page into the state (its grid placed in the set's world), read from the disk cache only when
        the heap writes it: pages of shots far ahead never sit in RAM (the analyser runs up to --lookahead-s ahead)."""
        t = dict(sets.members(set_id)).get(an.shot_id) if sets.enabled else None
        load = (lambda f=an.first, n=an.n, c=an.crop: cache.load_page(f, n, c)[1]) if has_page else None
        state.set_page(an.shot_id, load, None if t is None or np.allclose(t, set_atlas.IDENTITY) else t,
                       cache.page_key(an.crop))

    def on_shot(an: ShotAnalysis):
        t0 = time.perf_counter()
        page = structure.pack(an.patches[:2000], page_bytes) if a.patches else None
        if a.patches:
            cache.save_page(an.first, an.n, an.crop, page)         # before the analysis: a cached analysis has its page
        cache.save_analysis(an)
        margins_ready.wait()                                       # the canvas extent and the set need the room's margins
        set_id, transforms = sets.register(an, margins["value"])
        state.publish_analysis(an, set_id, transforms)
        if a.patches:
            publish_page(an, set_id, page is not None)
        an.patches = []
        stats["shots"] += 1
        stats["pages"] += page is not None
        m = mosaic_t1.build(an, margins["value"], a.scale, store.heap_slot_bytes)
        mkey = cache.mosaic_key(an.crop, margins["value"], a.scale)
        cache.save_mosaic(an.first, an.n, mkey, m)
        state.set_mosaic(an.shot_id, mkey, m.empty)
        recent[an.shot_id] = m
        while len(recent) > 16:
            recent.popitem(last=False)
        an.kept.clear()
        t1 = sets.make_t1(set_id, mosaic_of, mosaic_meta_of)
        stats["t1" if t1 else "t1_empty"] += 1
        if set_id != an.shot_id or stats["shots"] % 20 == 0:
            sets.save()
        log(f"shot {an.shot_id}: {an.n} frames at {state.shots[an.shot_id].start_s:.1f} s, side share {m.side_share:.0%}"
            f"{'' if set_id == an.shot_id else f', set {set_id}'}{'' if t1 else ' (no T1)'}, "
            f"{1e3 * (time.perf_counter() - t0):.0f} ms",
            event={"event": "shot", "shot": an.shot_id, "set": set_id, "first": an.first, "n": an.n,
                   "side_share": round(m.side_share, 4), "disagree": round(m.disagree, 4), "t1": bool(t1),
                   "tex": [m.tex_w, m.tex_h], "scale": m.scale,
                   "ms": round(1e3 * (time.perf_counter() - t0))},
            echo=False)

    def cached_shot(first: int, n: int) -> Optional[tuple]:
        """(analysis, this room's mosaic key, whether that mosaic is empty) of a cached shot, or None: analysed again."""
        try:
            an = cache.load_analysis(first, n, film.crop_at(film.cut_time(first)))
            if an is None:
                return None
            if a.patches and cache.page_state(first, n, an.crop) is None:   # cached by a run without --patches:
                return None                                                # analysed again, so its page exists
            mkey = cache.mosaic_key(an.crop, margins["value"], a.scale)
            mm = cache.mosaic_meta(first, n, mkey)
            return None if mm is None else (an, mkey, mm["side_share"] < mosaic_t1.EMPTY_SHARE)
        except Exception as e:                                     # a damaged file: the analyser makes the shot again
            log(f"cache: the shot at frame {first} is unreadable ({e!r}): analysing it again")
            return None

    def warm_start():
        """Shots analysed in earlier runs whose mosaic for this room is cached too: their sets (cached), side plans and
        canvases (T1, made from the members' mosaics if not cached for this set; the plate from the cache) are published
        at once, then the analyser starts. A shot cached without this room's mosaic (another room, another mosaic
        version) is analysed again, since a mosaic needs the decoded kept frames. Each set's members and canvases are
        published together (one hold of the state lock), so the painter never sees a set without its cached plate."""
        try:
            margins_ready.wait()
            on_disk = {}
            for first, n in cache.cached_spans(lambda f: film.crop_at(film.cut_time(f))):
                on_disk.setdefault(first, set()).add(n)
            groups = ([(s.shot_id, side_cache.tiling(on_disk, s.first, s.first + s.n)) for s in film.spans] if film.spans
                      else [(None, [(f, max(on_disk[f]))]) for f in sorted(on_disk)])
            loaded, again = [], 0
            with state.lock:
                for shot_id, parts in groups:
                    got = [cached_shot(f, n) for f, n in parts] if parts else None
                    if not got or any(g is None for g in got):
                        again += bool(parts)
                        continue
                    for i, (an, mkey, empty) in enumerate(got):
                        an.shot_id = shot_id if i == 0 and shot_id is not None else state.new_shot_id(an.first)
                        info = state.ensure_shot(an.shot_id, an.first, an.n, an.from_content_model)
                        info.mosaic_key, info.mosaic_empty = mkey, empty
                        loaded.append(an)
            n_sets = sets.load()                                   # sets whose members are all among these shots
            by_set = collections.defaultdict(list)
            for an in loaded:
                sid = sets.reg.set_id_of(an.shot_id) if sets.enabled else None
                if sid is None:
                    wx0, wy0, sc, tw, th = mosaic_t1.canvas_geometry(an, margins["value"], a.scale, store.heap_slot_bytes)
                    sets.adopt_alone(an.shot_id, (wx0, wy0, wx0 + tw * sc, wy0 + th * sc))
                    sid = an.shot_id
                by_set[sid].append(an)
                sets.add_cached_features(an.shot_id)
            canvases = pages = 0
            for sid, members in by_set.items():
                with state.lock:                                   # a set and its canvases reach the painter together
                    transforms = dict(sets.members(sid))
                    for an in members:
                        t = transforms.get(an.shot_id)
                        xf = None if t is None or np.allclose(t, set_atlas.IDENTITY) else set_atlas.frame_transforms(an, t)
                        state.publish_analysis(an, sid, xf)
                        state_ = cache.page_state(an.first, an.n, an.crop) if a.patches else None   # read when needed
                        if state_ is not None:
                            publish_page(an, sid, state_)
                            pages += state_
                        info = state.shots[an.shot_id]
                        state.set_mosaic(an.shot_id, info.mosaic_key, info.mosaic_empty)
                    if sets.make_t1(sid, mosaic_of, mosaic_meta_of):
                        canvases += 1
                    rkey = sets.plate_key(sid, pcfg.recipe)
                    key_shot = state.shots[sid]
                    meta = cache.canvas_meta(key_shot.first, key_shot.n, TIER_PLATE, rkey) if rkey else None
                    if meta is not None and meta.get("recipe", pcfg.recipe) != pcfg.recipe:
                        meta = None                                # another painter's plate: painted again
                    if meta is not None:
                        state.add_canvas(sid, TIER_PLATE, rkey, float(meta["world_x0"]), float(meta["world_y0"]),
                                         float(meta["scale"]), None, meta, int(meta["tex_w"]), int(meta["tex_h"]))
                        sets.adopt_plate(sid, meta, rkey)          # later joins are judged against it
                        canvases += 1
            stats["cache_hits"] += canvases
            log(f"cache: {len(loaded)} shots from earlier runs in {len(by_set)} sets ({n_sets} sets as stored), "
                f"{canvases} canvases, {pages} patch pages; {again} analysed for another room or mosaic version are "
                f"analysed again")
        except Exception as e:                                     # whatever the cache holds, the run gets its analyser
            log(f"cache: warm start stopped ({e!r}); the analyser makes the rest")
        finally:
            analyser.start()

    analyser = Analyser(film, state, on_shot, log, a.threads, a.lookahead_s)
    analyser.goto(0)
    threading.Thread(target=warm_start, daemon=True, name="warm").start()   # starts the analyser when it is done
    jobs = None
    if not a.no_paint:
        jobs = PainterJobs(state, sets, mosaic_of, painter_for(run_dir, pcfg, log), log, big_min_s=a.big_plate_min_s,
                           lookahead_s=a.lookahead_s)
        jobs.start()

    t_start = time.monotonic()
    playhead, speed, pressure, epoch, uploading = 0.0, 1.0, False, None, -1
    last_beat, last_beat_t, seen_player, last_status = None, None, False, 0.0
    skipped = set()
    try:
        while True:
            now = time.monotonic()
            p = store.read_player()
            alive = False
            if p is not None:
                if p["beat"] != last_beat:
                    last_beat, last_beat_t = p["beat"], now
                alive = last_beat_t is not None and now - last_beat_t < 2.0 and p["state"] in (
                    PLAYER["playing"], PLAYER["paused"], PLAYER["seeking"])
            if alive:
                if not seen_player:
                    log(f"player: pid {p['pid']} connected at {p['playhead_s']:.2f} s")
                seen_player = True
                playhead, speed = p["playhead_s"], p["speed"]
                pressure = p["late_recent"] >= a.late_limit or (p["state"] == PLAYER["playing"] and p["slack_ms"] < a.slack_ms)
                uploading = p["uploading_slot"]
                if p["epoch"] != epoch:                            # a seek, or the film start: re-plan from the playhead
                    epoch = p["epoch"]
                    analyser.goto(film.frame_at(playhead) + 1)
                    store.worker["epoch_seen"] = epoch
                    log(f"player: epoch {epoch} at {playhead:.2f} s: re-planning from the next shot",
                        event={"event": "epoch", "epoch": epoch, "playhead_s": round(playhead, 3)})
                if not margins_ready.is_set() and p["need_valid"]:
                    margins["value"] = side_cache.room_margins(p["need_margin_px"])
                    log(f"margins from the player: {tuple(round(float(v), 1) for v in p['need_margin_px'])} source px "
                        f"(L, T, R, B); canvases cover {margins['value']}")
                    margins_ready.set()
            elif seen_player and a.exit_after_player_s > 0 and last_beat_t is not None and now - last_beat_t > a.exit_after_player_s:
                log("player gone: exiting")
                break
            if not margins_ready.is_set() and now - t_start > a.margin_wait_s:
                margins["value"] = fallback_margins
                log(f"no player margins after {a.margin_wait_s:.0f} s: using {margins['value']}")
                margins_ready.set()
            analyser.set_playhead(playhead)
            analyser.set_fill_behind(not (alive and p["state"] == PLAYER["playing"]))
            if jobs is not None:
                jobs.set_view(playhead, speed, pressure)
            state.ensure_heap(playhead, a.horizon_s, uploading)
            for info in state.shots_from(playhead - 5.0, 5.0):    # plates the painter could not make in time
                if (info.start_s < playhead and info.row not in skipped and not info.short and info.analysed and
                        not any(v.tier >= TIER_PLATE for v in info.versions) and not info.plate_tried):
                    skipped.add(info.row)
            w = store.worker
            w["plates_skipped"] = len(skipped)
            w["cache_hits"] = stats["cache_hits"]
            if jobs is not None:
                w["throttle_events"] = jobs.throttle_events
            store.beat(f"{'painting' if jobs is not None and jobs.current is not None else 'running'}")
            if now - last_status > 5.0:
                last_status = now
                heap = state.heap_summary()
                ahead = 0.0
                for info in state.shots_from(playhead, a.lookahead_s):
                    if not info.analysed:
                        break
                    ahead = info.end_s - playhead
                log(f"playhead {playhead:7.1f} s{' (player)' if alive else ''} | shots analysed {stats['shots']} this run, "
                    f"{ahead:5.0f} s ahead | T1 {stats['t1']} (empty {stats['t1_empty']}) | pages {stats['pages']} | plates "
                    f"{jobs.plates_made if jobs else 0} (skipped {len(skipped)}, est {jobs.est_s if jobs else 0:.1f} s) | "
                    f"heap {heap['slots_used']}/{store.heap_slots} ({heap['pages']} pages) | "
                    f"{'PRESSURE' if pressure else 'ok'} | decoded {analyser.frames_decoded}")
            time.sleep(0.1)
    except KeyboardInterrupt:
        log("stopping (Ctrl+C)")
    finally:
        analyser.stop()
        if jobs is not None:
            jobs.stop()
            jobs.painter.stop()
        store.set_state("stopped")
        store.beat("stopped")
        log(f"done: {stats['shots']} shots analysed, {stats['t1']} T1 canvases, "
            f"{jobs.plates_made if jobs else 0} plates, {len(skipped)} plates skipped")
        del job
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
