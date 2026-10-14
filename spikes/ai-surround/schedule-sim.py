"""Live schedule of the AI sides: with playback and the generator starting together (no head start), which share of the
film gets its plate before its shot starts, and how fast must the painter be for 1x?

Pure arithmetic on set-reuse.py's results (x5-coverage.json: shots, sets, plate owners; x5-gen-frames.json: the new
drawings that need the generator). Jobs, all known at t = 0 (the film is local and analysed ahead):
  plate   one per owner shot; useful for every shot it serves (owner + inheritors), only if done before that shot
          starts (tiers upgrade at cuts only). Policies: one plate per shot; one per set (connected components of the
          registration, at the set's first shot); one per plate-cover owner (a later shot of the set inherits an earlier
          plate only if that plate's canvas covers >= 80% of its sides, set-reuse.py coverage).
          Short shots (< 0.8 s) may skip their own plate: they use a set plate that is already done, else a lower tier.
  gen     one refiner job per GEN drawing (worse 25% band's U above tau), due when the drawing is shown.
The generator owns a share u of the GPU: a job of c GPU-seconds (full GPU) takes c / u. Earliest deadline first, run to
completion; a job that can no longer finish in time is dropped (a plate moves on to the next shot it serves).
Per combination: top tier = share of film time whose plate is ready when its shot starts; GEN zones ready in time;
the time after which nothing is late any more; run-ahead (job deadline minus its start) and GPU-seconds per film-second.
Variants: OP/ED sides cached (identical every episode: no jobs, top tier; the shots set-reuse.py marks "ed"), the plate
policy, short shots skipping.

    python -I schedule-sim.py OUT [--tau 0.1] [--hyp flow] [--rest-before 0]

Reads OUT/x5-coverage.json and OUT/x5-gen-frames.json; writes OUT/x5-schedule.json.
"""
import argparse
import heapq
import json
import os
import sys
import time

import numpy as np

FPS = 24000 / 1001
SHARES = (0.5, 0.6, 0.75, 0.85)
PAINTER_S = (1, 1.5, 2, 3, 5, 9)                                  # seconds per plate on the whole GPU
REFINER_MS = (0, 30, 60, 120)                                     # ms per GEN drawing on the whole GPU (0: plates only)
POLICIES = ("shot", "set", "cover-0.8")
TOP_TARGET = 0.95


def plan(cov, gen_frames, policy, skip_short, oped_cached):
    """Plate jobs {owner: {"due": first deadline, "uses": [(start_s, shot)]}}, GEN deadlines, and per shot its owner
    (None: no plate, "cached": OP/ED sides from the cache)."""
    shots = cov["shots"]
    long_ = {s["shot"]: s["end_s"] - s["start_s"] >= 0.8 for s in shots}
    cached = {s["shot"] for s in shots if oped_cached and s["region"] == "ed"}
    owner = {}
    if policy == "shot":
        for s in shots:
            owner[s["shot"]] = s["shot"] if (long_[s["shot"]] or not skip_short) else None
    elif policy == "set":
        first = {}
        for s in shots:                                           # the set's first shot (that may own a plate)
            if s["shot"] not in cached and (long_[s["shot"]] or not skip_short):
                first.setdefault(s["set"], s["shot"])
        for s in shots:
            owner[s["shot"]] = first.get(s["set"])
    else:
        key = policy.split("-", 1)[1] + ("-skip" if skip_short else "")
        for s in shots:
            owner[s["shot"]] = s["plate_of"][key]
    for x in cached:
        owner[x] = "cached"
    jobs = {}
    for s in shots:
        o = owner[s["shot"]]
        if o is None or o == "cached":
            continue
        j = jobs.setdefault(o, {"uses": [], "due": None})
        j["uses"].append((s["start_s"], s["shot"]))
        if s["shot"] == o:                                        # first deadline: the owner shot's start
            j["due"] = s["start_s"]
    for j in jobs.values():
        j["uses"].sort()
    gen = []
    for s in shots:
        if s["shot"] in cached:
            continue
        gen += [(s["first_frame"] + t) / FPS for t in gen_frames.get(str(s["shot"]), [])]
    return jobs, sorted(gen), owner


def simulate(cov, plan_, u, painter_s, refiner_ms):
    """Earliest deadline first on one GPU share; returns the metrics of one combination."""
    jobs, gen, owner = plan_
    shots = cov["shots"]
    film_s = cov["film_s"]
    heap = [(j["due"], 0, o) for o, j in jobs.items()]
    if refiner_ms > 0:
        heap += [(d, 1, i) for i, d in enumerate(gen)]
    heapq.heapify(heap)
    t, done, gen_ok, runahead, late = 0.0, {}, 0, [], []
    plate_c, gen_c = painter_s / u, refiner_ms / 1000.0 / u
    while heap:
        d, kind, j = heapq.heappop(heap)
        c = plate_c if kind == 0 else gen_c
        if t + c > d:                                             # cannot finish in time
            if kind == 0:
                nxt = [s for s, _ in jobs[j]["uses"] if s >= t + c]
                if nxt:
                    heapq.heappush(heap, (nxt[0], 0, j))
            else:
                late.append(d)
            continue
        runahead.append((t, d - t))
        t += c
        if kind == 0:
            done[j] = t
        else:
            gen_ok += 1
    top_s = story_s = story_top = no_plate_s = 0.0
    for s in shots:
        dur = s["end_s"] - s["start_s"]
        o = owner[s["shot"]]
        top = o == "cached" or (o is not None and done.get(o, np.inf) <= s["start_s"])
        top_s += dur * top
        if o is None:
            no_plate_s += dur
        elif not top:
            late.append(s["start_s"])
        if s["region"] == "story":
            story_s += dur
            story_top += dur * top
    ra = np.array(runahead) if runahead else np.zeros((0, 2))
    ahead_after = max(late) if late else 0.0
    after = ra[ra[:, 0] >= ahead_after, 1] if len(ra) else ra
    demand = (len(jobs) * painter_s + (len(gen) * refiner_ms / 1000.0 if refiner_ms > 0 else 0.0)) / film_s
    return {"top_share": round(top_s / film_s, 4), "story_top_share": round(story_top / max(1e-9, story_s), 4),
            "no_plate_share": round(no_plate_s / film_s, 4),
            "gen_ready_share": round(gen_ok / len(gen), 4) if refiner_ms > 0 and gen else None,
            "ahead_for_good_after_s": round(ahead_after, 1),
            "runahead_min_s": round(float(ra[:, 1].min()), 2) if len(ra) else None,
            "runahead_min_after_ahead_s": round(float(after.min()), 2) if len(after) else None,
            "runahead_max_s": round(float(ra[:, 1].max()), 1) if len(ra) else None,
            "runahead_p50_s": round(float(np.median(ra[:, 1])), 1) if len(ra) else None,
            "gpu_s_per_film_s": round(demand, 3), "plates": len(jobs), "plates_done": len(done), "gen_jobs": len(gen),
            "generator_busy_until_s": round(t, 1)}


def needed_painter_s(cov, plan_, u, refiner_ms, target=TOP_TARGET):
    """Largest seconds per plate (full GPU) that keeps >= target of film time at the top tier (bisection; None if even
    0.05 s fails)."""
    ok = lambda p: simulate(cov, plan_, u, p, refiner_ms)["top_share"] >= target
    lo, hi = 0.05, 60.0
    if not ok(lo):
        return None
    if ok(hi):
        return hi
    for _ in range(22):
        mid = (lo * hi) ** 0.5
        lo, hi = (mid, hi) if ok(mid) else (lo, mid)
    return round(lo, 3)


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("out")
    ap.add_argument("--tau", default="0.1", help="GEN trigger (a key of x5-gen-frames.json: 0.02, 0.1, 0.3)")
    ap.add_argument("--hyp", default="flow", help="camera step of the GEN test: flow or pc")
    ap.add_argument("--rest-before", type=int, default=0)
    a = ap.parse_args(argv)
    if a.rest_before > 0:
        print(f"resting {a.rest_before} s before this run", flush=True)
        time.sleep(a.rest_before)
    try:
        import psutil
        psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    except Exception:
        pass
    cov = json.load(open(os.path.join(a.out, "x5-coverage.json"), encoding="utf-8"))
    gen_all = json.load(open(os.path.join(a.out, "x5-gen-frames.json"), encoding="utf-8"))
    t0 = time.perf_counter()
    grid, answer = [], []
    for policy in POLICIES:
        for skip in (True, False):
            for oped in (False, True):
                plans = {tau: plan(cov, gen_all[f"{a.hyp}-16-{tau}"], policy, skip, oped) for tau in ("0.02", "0.1", "0.3")}
                pl = plans[a.tau]
                for u in SHARES:
                    for p in PAINTER_S:
                        for r in REFINER_MS:
                            m = simulate(cov, pl, u, p, r)
                            grid.append({"policy": policy, "skip_short": skip, "oped_cached": oped, "u": u,
                                         "painter_s": p, "refiner_ms": r, **m})
                for u in (0.6, 0.75):
                    for r in REFINER_MS:
                        row = {"policy": policy, "skip_short": skip, "oped_cached": oped, "u": u, "refiner_ms": r,
                               "plates": len(pl[0]), "needed_painter_s": needed_painter_s(cov, pl, u, r)}
                        if r == 60:                                # GEN trigger sensitivity at the middle refiner cost
                            for tau in ("0.02", "0.3"):
                                row[f"needed_painter_s_tau{tau}"] = needed_painter_s(cov, plans[tau], u, r)
                        answer.append(row)
    out = {"params": {"shares": SHARES, "painter_s": PAINTER_S, "refiner_ms": REFINER_MS, "policies": POLICIES,
                      "tau": a.tau, "hyp": a.hyp, "top_target": TOP_TARGET, "film_s": cov["film_s"]},
           "answer": answer, "grid": grid}
    with open(os.path.join(a.out, "x5-schedule.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    for row in answer:
        print(json.dumps(row))
    print(f"schedule done in {time.perf_counter() - t0:.0f} s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
