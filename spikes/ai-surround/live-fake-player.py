"""Stand-in player for the live worker (tests without the screens): attaches to the side store, writes the player block
the way perf1 --ai-sides does (beat, playing, the playhead at film speed, seeks with a new epoch, the room's side widths,
no pressure, no slot being copied) and, at every cut the playhead crosses, notes what the real player would show for
the showing that starts there: the shot's canvas id (its set), the best tier of that canvas already in a heap slot and
valid from the showing's start, or the glow. That is what the cut-only rule lets the player take (perf1-ai-sides.c).

    python -I -X utf8 live-fake-player.py [--store out/live/side-store.bin] [--seconds 120] [--start-at 0]
        [--margins 818,0,1500,275] [--seeks AT:TO,AT:TO] [--out FILE.jsonl]

The worker's run then reads like a viewing: film time per tier at the showings' starts and plates arriving.
"""
import argparse
import json
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from live import side_store as ss  # noqa: E402

PLAYING = ss.ENUMS["player_state"]["playing"]
TIERS = {v: k for k, v in ss.ENUMS["tier"].items()}
READY = ss.ENUMS["canvas_state"]["complete"]


def write_player(store, **fields):
    rec = store.player
    rec["seq"] = int(rec["seq"]) | 1                               # odd: readers drop what they copy now
    for k, v in fields.items():
        rec[k] = v
    rec["beat"] = int(rec["beat"]) + 1
    rec["qpc"] = ss.qpc()
    rec["seq"] = int(rec["seq"]) + 1


def frame_at(times: np.ndarray, t: float) -> int:
    return max(0, int(np.searchsorted(times, t, side="right")) - 1)


def heap_best(store, canvas_id: int, start_s: float, shot: int = -1) -> int:
    """The tier the player would show: the best canvas of the set valid from the showing's start, or structure (3) when
    that shot's patch page is in the heap too (schema 2: a page goes over a canvas, never alone)."""
    best, page = 0, False
    for i in range(len(store.canvases)):
        c = ss.read_record(store.canvases[i])
        if c is None or int(c["canvas_id"]) != canvas_id or int(c["state"]) != READY:
            continue
        if int(c.get("kind", 0)) == ss.ENUMS["canvas_kind"]["patch_page"]:
            page |= int(c["shot"]) == shot
        elif float(c["valid_from_s"]) <= start_s + 1e-6:
            best = max(best, int(c["tier"]))
    return ss.ENUMS["tier"]["structure"] if best and page else best


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--store", default=os.path.join(HERE, "out", "live", "side-store.bin"))
    ap.add_argument("--seconds", type=float, default=120.0, help="wall seconds to play")
    ap.add_argument("--start-at", type=float, default=0.0)
    ap.add_argument("--margins", default="818,0,1500,275", help="L,T,R,B source px (setup-a-default's, from phase 2)")
    ap.add_argument("--seeks", default="", help="AT:TO wall seconds -> film seconds, comma-separated")
    ap.add_argument("--out", default="")
    ap.add_argument("--wait-s", type=float, default=60.0, help="how long to wait for the worker's store")
    a = ap.parse_args(argv)
    seeks = sorted((float(x), float(y)) for x, y in (s.split(":") for s in a.seeks.split(",") if s.strip()))
    margins = [float(v) for v in a.margins.split(",")]
    t0 = time.monotonic()
    store = None
    while store is None:
        try:
            store = ss.SideStore.open(a.store)
            if int(store.worker["frames_published"]) == 0:
                store.close()
                store = None
        except (OSError, ValueError):
            store = None
        if store is None:
            if time.monotonic() - t0 > a.wait_s:
                raise SystemExit(f"no published store at {a.store} after {a.wait_s:.0f} s")
            time.sleep(0.5)
    n = int(store.worker["frames_published"])
    times = np.array(store.frames["pts"][:n], np.float64)
    out = open(a.out, "w", encoding="utf-8") if a.out else None
    epoch, playhead, last_shot = 1, a.start_at, None
    showings, tier_time = [], {}
    t_start = time.monotonic()
    t_last = t_start
    write_player(store, pid=os.getpid(), state=PLAYING, epoch=epoch, playhead_s=playhead, speed=1.0, slack_ms=4.0,
                 late_recent=0, need_margin_px=np.float32(margins), need_valid=1, src_w=int(store.worker["src_w"]),
                 src_h=int(store.worker["src_h"]), uploading_slot=-1, showing_shot=-1, showing_tier=0, showing_canvas=-1)
    print(f"fake player: store {a.store}, {n} frames, playing from {playhead:.1f} s for {a.seconds:.0f} s", flush=True)
    current = None
    while True:
        now = time.monotonic()
        wall = now - t_start
        if wall >= a.seconds:
            break
        playhead += now - t_last
        t_last = now
        if seeks and wall >= seeks[0][0]:
            if current is not None:                               # the showing ends at the playhead before the jump
                tier_time[current["tier"]] = tier_time.get(current["tier"], 0.0) + (playhead - current["start"])
                current = None
            _, playhead = seeks.pop(0)
            epoch += 1
            last_shot = None
            print(f"fake player: seek to {playhead:.1f} s (epoch {epoch})", flush=True)
        f = frame_at(times, playhead)
        fr = ss.read_record(store.frames[f])
        shot = int(fr["shot"]) if fr is not None else -1
        if shot != last_shot:                                     # a showing starts: a cut, a seek or the film start
            if current is not None:
                d = playhead - current["start"]
                tier_time[current["tier"]] = tier_time.get(current["tier"], 0.0) + d
            last_shot = shot
            sh = ss.read_record(store.shots[shot]) if shot >= 0 else None
            cid = int(sh["canvas_id"]) if sh is not None else -1
            tier = heap_best(store, cid, float(sh["start_s"]), shot) if sh is not None else 0
            current = {"wall": round(wall, 2), "epoch": epoch, "start": playhead, "shot": shot, "canvas": cid, "tier": TIERS.get(tier, tier),
                       "shot_start_s": round(float(sh["start_s"]), 3) if sh is not None else None}
            showings.append(current)
            if out:
                out.write(json.dumps(current) + "\n")
                out.flush()
            write_player(store, showing_shot=shot, showing_tier=tier, showing_canvas=cid)
        write_player(store, playhead_s=playhead, epoch=epoch, state=PLAYING, speed=1.0)
        time.sleep(1 / 60)
    if current is not None:
        tier_time[current["tier"]] = tier_time.get(current["tier"], 0.0) + (playhead - current["start"])
    write_player(store, state=ss.ENUMS["player_state"]["ended"])
    total = sum(tier_time.values()) or 1.0
    by_tier = {k: f"{v:.1f} s ({100 * v / total:.0f} %)" for k, v in sorted(tier_time.items(), key=lambda kv: str(kv[0]))}
    shown = {}
    for s in showings:
        shown[s["tier"]] = shown.get(s["tier"], 0) + 1
    print(f"fake player: {len(showings)} showings {shown}; film time by tier at the showings' starts: {by_tier}", flush=True)
    if out:
        out.write(json.dumps({"summary": {"showings": shown, "film_s_by_tier": tier_time}}) + "\n")
        out.close()
    store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
