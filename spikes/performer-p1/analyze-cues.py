"""Analyze a perf1 cue log (cuelog.json) and check the performer's cue and clock invariants from the outside.

The log is written by perf1 (src/perf1-engine-log.c) and by the headless simulation (latch-sim.c) in the same format.
This script knows nothing about the engine's code: it recomputes what should have happened from the log's own data
(frame timestamps, versions, commands, epoch parameters) and compares.

Log layout (all times in seconds on the host clock, refresh indices are the monitor's own count):
  monitors[]   index, name, rate_hz, scan_s, blank_s, aim_offset_s (a refresh's wake-up to the light of the aim row),
               p, q (q master refreshes carry p of this follower's)
  config       step_s (film seconds per master refresh), tol_s (half of it), eta_s, hysteresis, reaim, own_mode
  versions[]   id, source, shots[] {start, crop|null, placement|null}   every cue sheet version loaded
  epochs[]     ep, origin (timestamp of the first frame), J0 (master refresh the epoch starts on), start_k[] (each
               monitor's first refresh), t_ready, frame_pts[] (timestamps of the frames the decoder queued)
  starts[]     one per decided frame: [ep, ord, j, cut, shift, s0, s1, ...]: the master refresh j the frame starts on,
               whether it is the first frame of a shot, the re-aim shift S in force, and each monitor's start refresh
  events[]     load (version published), seek_cmd (t = request, d0 = film time sought, d1 = executed, i0 = 1 for a
               seek, 0 for the seek a resume makes), pause, resume, parked, ready, restart, reaim
  refreshes{}  per monitor, one row per refresh the performer prepared and presented:
               [k, j, svc, target, pts, ep, ord, vid, sidx, sstart, drawn, ach, lid, flags, dec]
               k refresh index, j the master refresh the frame starts on, svc when it was prepared, target the refresh's
               wake-up time, pts/ord the frame (timestamp, ordinal in the epoch), vid/sidx the latched version and shot,
               drawn the ordinal the renderer drew (-1 = not reported), ach the fingerprint of the cue applied, lid the
               latch entry, flags 1 = shot captured on this refresh, 2 = first refresh of the epoch on this monitor,
               4 = stale latch lookup, dec how many frames of the epoch the performer had decided when it prepared the
               refresh (a frame it had not decided yet may legitimately be missing: the decoder was behind)

Checks (the headline three are the ones the cue-latch design promises):
  WRONG FRAMES        C1 frame start formula and picks, C2 cue looked up by the frame's own timestamp, C3 applied cue
                      equals the version's table, C12 the renderer drew the frame chosen
  SHOTS CHANGED       C4 version or capture changes inside a latched shot, C4b monitors disagreeing on a frame,
                      C5 capture takes the newest version, C6 the latch is reset at every break
  BREAKS              C7 every monitor starts the epoch together (same frame, same master refresh, aligned in light),
                      C8 nothing of the old epoch is presented after a break command
  CLOCK               C9 a follower moves only at cuts, C10 re-aim: only at cuts, within one refresh, never shortening
                      the frame before the cut below one refresh, C11 follower stays within half a refresh of the master
                      in light at the aim row, C13 epoch bookkeeping

What this can and cannot see. It judges the performer's decisions as logged: which frame and cue it prepared for which refresh
of which monitor, and when. The refresh times in the log are the performer's own predictions from its fitted grids, so C7b and
C11 check its choices, not what the panels did; the measured light offsets, pace and overlay state come from the per-monitor
logs (analyze-perf1.py). "drawn" is the frame the monitor's queue returned, not what reached the glass.

Usage: python -I analyze-cues.py LOG_OR_DIR [--clock-tol-ms 0.5] [--json OUT.json] [--max-examples 5]
Exit code 0 when every must-be-zero count is zero.
"""
import argparse
import bisect
import json
import math
import struct
import sys
from collections import defaultdict
from pathlib import Path

K, J, SVC, TGT, PTS, EP, ORD, VID, SIDX, SSTART, DRAWN, ACH, LID, FLAGS, DEC = range(15)
FIELDS = ["k", "j", "svc", "target", "pts", "ep", "ord", "vid", "sidx", "sstart", "drawn", "ach", "lid", "flags", "dec"]


def shot_hash(crop, place):
    """The fingerprint of the applied picture (cue_shot_hash in src/perf1-cue-sheet.c): FNV-1a over a flag byte and four
    doubles for the crop, a flag byte and three doubles for the placement; -0.0 counts as 0.0."""
    h = 2166136261

    def mix(h, data):
        for b in data:
            h = ((h ^ b) * 16777619) & 0xFFFFFFFF
        return h

    h = mix(h, bytes([1 if crop is not None else 0]))
    for v in (crop if crop is not None else [0.0] * 4):
        h = mix(h, struct.pack("<d", v + 0.0))
    h = mix(h, bytes([1 if place is not None else 0]))
    pv = [place["center_mm"][0], place["center_mm"][1], place["height_mm"]] if place is not None else [0.0] * 3
    for v in pv:
        h = mix(h, struct.pack("<d", v + 0.0))
    return h


class Check:
    """A named count with a few examples; `must_be_zero` ones decide the exit code."""

    def __init__(self, key, title, must_be_zero=True, group=None):
        self.key, self.title, self.must_be_zero, self.group = key, title, must_be_zero, group
        self.count = 0
        self.examples = []
        self.info = {}

    def hit(self, example=None, limit=5):
        self.count += 1
        if example is not None and len(self.examples) < limit:
            self.examples.append(example)


def load(path):
    p = Path(path)
    if p.is_dir():
        p = p / "cuelog.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    if d.get("schema") != "panorama.perf1.cuelog/1":
        raise SystemExit(f"{p}: not a perf1 cue log (schema {d.get('schema')!r})")
    if d["refresh_fields"] != FIELDS:
        raise SystemExit(f"{p}: refresh fields {d['refresh_fields']} differ from the expected {FIELDS}")
    return p, d


def build_versions(d):
    versions = {}
    for v in d["versions"]:
        shots = v["shots"]
        starts = [s["start"] for s in shots]
        ends = starts[1:] + [math.inf]
        hashes = [shot_hash(s["crop"], s["placement"]) for s in shots]
        versions[v["id"]] = {"shots": shots, "starts": starts, "ends": ends, "hashes": hashes, "source": v.get("source", "")}
    return versions


def analyze(d, clock_tol_s=0.0005, max_examples=5):
    mons = d["monitors"]
    n_mon = d["n_monitors"]
    master = d["master"]
    cfg = d["config"]
    step, tol = cfg["step_s"], cfg["tol_s"]
    versions = build_versions(d)
    epochs = {e["ep"]: e for e in d["epochs"]}
    events = d["events"]
    recs = {int(m): rows for m, rows in d["refreshes"].items()}
    sf = d["start_fields"]
    starts_by_ep = defaultdict(list)
    for row in d["starts"]:
        starts_by_ep[row[0]].append(row)

    C = {}

    def chk(key, title, must=True, group=None):
        C[key] = Check(key, title, must, group)
        return C[key]

    c1a = chk("C1a", "frame start refresh differs from the formula (J0 + S + ceil(film time/step - 0.5))", group="wrong frames")
    c1b = chk("C1b", "a monitor showed a frame other than the last one started on it (early or wrong)", group="wrong frames")
    c1late = chk("C1late", "a monitor showed an older frame than the last one started on it (decoder or queue behind)", False, "info")
    c2 = chk("C2", "cue not looked up by the frame's own timestamp (frame outside the latched shot's span)", group="wrong frames")
    c2b = chk("C2b", "latched shot start differs from the version's table", group="wrong frames")
    c3 = chk("C3", "applied cue fingerprint differs from the version's table for that shot", group="wrong frames")
    c12 = chk("C12", "the renderer drew another frame than the one chosen", group="wrong frames")
    c4 = chk("C4", "shot changed on screen: version or latch entry changed inside a latched shot", group="shots changed")
    c4b = chk("C4b", "monitors applied different cues to the same frame", group="shots changed")
    c5 = chk("C5", "shot captured a version other than the newest published at that moment", group="shots changed")
    c6 = chk("C6", "latch entry used outside the epoch it was captured in (latch not reset at a break)", group="shots changed")
    c6b = chk("C6b", "refresh record whose latch entry was never captured", group="shots changed")
    c7 = chk("C7", "an epoch did not start on every monitor together (frame 0, master refresh J0, planned refresh)", group="breaks")
    c7b = chk("C7b", "first frame of an epoch not within half a refresh of the master in light", group="breaks")
    c8 = chk("C8", "refresh of the old epoch presented after a break command", group="breaks")
    c9 = chk("C9", "a follower moved its refresh mid-shot (not at a cut)", group="clock")
    c9b = chk("C9b", "a shown frame lasted less than one refresh on a follower", group="clock")
    c10 = chk("C10", "re-aim not at a cut, outside one refresh of the start, or shortened the frame before the cut below one refresh", group="clock")
    c11 = chk("C11", "follower further than half a refresh (+0.5 ms) from the master in light at the aim row", group="clock")
    c13 = chk("C13", "epoch bookkeeping wrong (timestamps not increasing, origin, ordinals)", group="clock")
    c0 = chk("C0", "log incomplete (refresh records truncated)", group="clock")
    slips = chk("I1", "follower slips at cuts (informational)", False, "info")
    flips = chk("I2", "follower flip-flops: a slip undone within 2 s (informational)", False, "info")

    if d["stats"].get("truncated"):
        c0.hit("refresh records were capped")

    # ---- publication times of the versions --------------------------------------------------------------------
    loads = sorted((ev["t"], ev["i0"]) for ev in events if ev["kind"] == "load")
    load_times = [t for t, _ in loads]
    load_ids = [v for _, v in loads]
    default_id = 0

    def newest_ids_at(t):
        """The versions that may be current at time t, within the clock tolerance."""
        lo = bisect.bisect_right(load_times, t - clock_tol_s)
        hi = bisect.bisect_right(load_times, t + clock_tol_s)
        ids = {load_ids[lo - 1] if lo > 0 else default_id}
        ids.update(load_ids[lo:hi])
        return ids

    # ---- C13 epochs ------------------------------------------------------------------------------------------
    for ep, e in epochs.items():
        pts = e["frame_pts"]
        if any(b <= a for a, b in zip(pts, pts[1:])):
            c13.hit(f"epoch {ep}: frame timestamps not increasing")
        if pts and abs(pts[0] - e["origin"]) > 1e-6:
            c13.hit(f"epoch {ep}: origin {e['origin']} is not the first timestamp {pts[0]}")

    # ---- C1a / C10: the decided frame starts ------------------------------------------------------------------
    start_j = {}      # (ep, ord) -> master start refresh
    start_s = {}      # (ep, ord) -> list of per-monitor start refreshes
    cut_of = {}
    prev_row = {}
    for ep, rows in starts_by_ep.items():
        e = epochs.get(ep)
        if e is None:
            c13.hit(f"starts for unknown epoch {ep}")
            continue
        pts = e["frame_pts"]
        J0, origin = e["J0"], e["origin"]
        shifts_seen = set()
        for row in rows:
            _, n, j, cut, shift = row[:5]
            s = row[5:]
            start_j[(ep, n)] = j
            start_s[(ep, n)] = s
            cut_of[(ep, n)] = bool(cut)
            shifts_seen.add(shift)
            if n >= len(pts):
                c13.hit(f"epoch {ep}: start of frame {n} but only {len(pts)} timestamps")
                continue
            nominal = J0 + shift + math.ceil((pts[n] - origin) / step - 0.5 - 1e-9)
            if n > 0 and j != nominal:
                c1a.hit(f"epoch {ep} frame {n}: j {j} != formula {nominal}", max_examples)
            if n == 0 and j != J0:
                c1a.hit(f"epoch {ep}: frame 0 starts on {j}, not J0 {J0}", max_examples)
            prev = prev_row.get((ep, n - 1))
            if prev is not None:
                j_prev, shift_prev = prev[2], prev[4]
                if shift != shift_prev:
                    # a re-aim at this frame
                    delta = shift - shift_prev
                    ok = bool(cut) and abs(shift) <= 1
                    nominal_old = J0 + shift_prev + math.ceil((pts[n] - origin) / step - 0.5 - 1e-9)
                    len_old = nominal_old - j_prev
                    len_new = j - j_prev
                    if len_old >= 1 and len_new < 1:
                        ok = False
                    if not ok:
                        c10.hit(f"epoch {ep} frame {n}: shift {shift_prev}->{shift}, cut={cut}, "
                                f"previous frame {len_old}->{len_new} refreshes", max_examples)
                    _ = delta
            prev_row[(ep, n)] = row
        if any(abs(sh) > 1 for sh in shifts_seen):
            c10.hit(f"epoch {ep}: shift outside -1..+1: {sorted(shifts_seen)}")

    # ---- first pass: the latch captures of all monitors (a capture may be on any monitor) ---------------------------
    first_rec = {}    # (m, ep, ord) -> record index of the first refresh showing the frame
    capture_of = {}   # lid -> (ep, vid, sidx, ach, svc)
    shown_hash = {}   # (ep, ord) -> (vid, sidx, ach) from the first monitor that showed it
    for m in sorted(recs):
        for r in recs[m]:
            if r[FLAGS] & 1:
                if r[LID] in capture_of:
                    c4.hit(f"latch entry {r[LID]} captured twice", max_examples)
                capture_of[r[LID]] = (r[EP], r[VID], r[SIDX], r[ACH], r[SVC])
                ok_ids = newest_ids_at(r[SVC])
                if r[VID] not in ok_ids:
                    c5.hit(f"monitor {m} refresh {r[K]} at {r[SVC]:.3f}: captured v{r[VID]}, newest was {sorted(ok_ids)}", max_examples)

    # ---- second pass: per-monitor checks over the refresh records ---------------------------------------------------
    for m in sorted(recs):
        rows = recs[m]
        prev = None
        starts_m = defaultdict(list)   # ep -> sorted (s, ord) of decided starts for this monitor
        for (ep, n), s in start_s.items():
            starts_m[ep].append((s[m], n))
        for ep in starts_m:
            starts_m[ep].sort()
        keys_m = {ep: [x[0] for x in v] for ep, v in starts_m.items()}
        for i, r in enumerate(rows):
            ep, ord_, vid, sidx = r[EP], r[ORD], r[VID], r[SIDX]
            # C1b: the frame this monitor should show at refresh k = the last frame whose start on it is <= k
            lst, keys = starts_m.get(ep), keys_m.get(ep)
            if lst:
                pos = bisect.bisect_right(keys, r[K]) - 1
                # frames decided but with equal start refresh: the later ordinal wins
                want = lst[pos][1] if pos >= 0 else -1
                if ord_ > want:
                    c1b.hit(f"monitor {m} refresh {r[K]}: showed frame {ord_}, last started is {want}", max_examples)
                elif ord_ < want:
                    if want < r[DEC]:
                        # the frame that had started was already decided, yet the monitor showed an older one
                        c1b.hit(f"monitor {m} refresh {r[K]}: showed frame {ord_}, frame {want} had started and was decided "
                                f"({r[DEC]} decided)", max_examples)
                    else:
                        c1late.hit(f"monitor {m} refresh {r[K]}: showed frame {ord_}, frame {want} had started but was not "
                                   f"decided yet (the decoder was behind)", max_examples)
            key = (m, ep, ord_)
            if key not in first_rec:
                first_rec[key] = i
            # C2/C3: lookup by the frame's own timestamp, applied cue equals the table
            v = versions.get(vid)
            if v is None or not (0 <= sidx < len(v["shots"])):
                c2.hit(f"monitor {m} refresh {r[K]}: unknown version {vid} / shot {sidx}", max_examples)
            else:
                if not (v["starts"][sidx] <= r[PTS] < v["ends"][sidx]):
                    c2.hit(f"monitor {m} refresh {r[K]}: frame {r[PTS]:.4f} outside shot {sidx} of v{vid} "
                           f"[{v['starts'][sidx]:.4f}, {v['ends'][sidx]:.4f})", max_examples)
                if abs(v["starts"][sidx] - r[SSTART]) > 1e-6:
                    c2b.hit(f"monitor {m} refresh {r[K]}: logged shot start {r[SSTART]} vs table {v['starts'][sidx]}", max_examples)
                if v["hashes"][sidx] != r[ACH]:
                    c3.hit(f"monitor {m} refresh {r[K]}: applied {r[ACH]} vs table {v['hashes'][sidx]}", max_examples)
            if r[DRAWN] >= 0 and r[DRAWN] != ord_:
                c12.hit(f"monitor {m} refresh {r[K]}: drew {r[DRAWN]}, chose {ord_}", max_examples)
            # C4b: the same frame gets the same cue on every monitor
            sk = (ep, ord_)
            if sk in shown_hash:
                if shown_hash[sk] != (vid, sidx, r[ACH]):
                    c4b.hit(f"epoch {ep} frame {ord_}: {shown_hash[sk]} vs {(vid, sidx, r[ACH])} on monitor {m}", max_examples)
            else:
                shown_hash[sk] = (vid, sidx, r[ACH])
            # latch entries: captured once, used only in their epoch
            lid = r[LID]
            if lid not in capture_of:
                c6b.hit(f"monitor {m} refresh {r[K]}: latch entry {lid} was never captured", max_examples)
            elif not (r[FLAGS] & 1):
                cap = capture_of[lid]
                if cap[0] != ep:
                    c6.hit(f"monitor {m} refresh {r[K]}: latch entry {lid} captured in epoch {cap[0]}, used in epoch {ep}", max_examples)
                if (cap[1], cap[2], cap[3]) != (vid, sidx, r[ACH]):
                    c4.hit(f"monitor {m} refresh {r[K]}: latch entry {lid} changed from {cap[1:4]} to {(vid, sidx, r[ACH])}", max_examples)
            # C4: consecutive refreshes of one monitor in one epoch
            if prev is not None and prev[EP] == ep:
                if (prev[VID], prev[SIDX]) != (vid, sidx) or prev[ACH] != r[ACH]:
                    pv = versions.get(prev[VID])
                    end_prev = pv["ends"][prev[SIDX]] if pv and prev[SIDX] < len(pv["ends"]) else math.inf
                    if r[PTS] < end_prev - 1e-9 and r[PTS] >= prev[PTS] - 1e-9:
                        c4.hit(f"monitor {m} refresh {r[K]}: v{prev[VID]}/{prev[SIDX]} -> v{vid}/{sidx} inside the shot "
                               f"(frame {r[PTS]:.4f} < end {end_prev:.4f})", max_examples)
                elif prev[LID] != lid:
                    c4.hit(f"monitor {m} refresh {r[K]}: same shot latched twice (entries {prev[LID]}, {lid})", max_examples)
            prev = r

    # ---- C9 / C9b / I1 / I2: follower moves ------------------------------------------------------------------
    for m in range(n_mon):
        if m == master:
            continue
        p, q = mons[m]["p"], mons[m]["q"]
        for ep, rows in starts_by_ep.items():
            last = {}
            last_slip = {}
            prev_start = None
            for row in rows:
                _, n, j, cut = row[0], row[1], row[2], row[3]
                s = row[5 + m]
                if prev_start is not None:
                    pj, ps = prev_start
                    if j > pj and s <= ps:
                        c9b.hit(f"monitor {m} epoch {ep} frame {n}: start {s} <= previous {ps}", max_examples)
                prev_start = (j, s)
                c = j % q
                if c in last:
                    j0, s0 = last[c]
                    dv = (s - s0) - p * ((j - j0) // q)
                    if dv != 0:
                        if cut:
                            slips.hit()
                        else:
                            c9.hit(f"monitor {m} epoch {ep} frame {n} (class {c}): moved by {dv} refreshes mid-shot", max_examples)
                        t = start_j[(ep, n)] * step
                        direction = 1 if dv > 0 else -1
                        if c in last_slip and last_slip[c][1] == -direction and t - last_slip[c][0] < 2.0:
                            flips.hit()
                        last_slip[c] = (t, direction)
                last[c] = (j, s)

    # ---- C7 / C7b: breaks ------------------------------------------------------------------------------------
    off = {m: mons[m]["aim_offset_s"] for m in range(n_mon)}
    period = {m: 1.0 / mons[m]["rate_hz"] for m in range(n_mon)}
    for ep, e in epochs.items():
        firsts = {}
        started = [m for m in range(n_mon) if (m, ep, 0) in first_rec]
        if started and len(started) < n_mon:
            c7.hit(f"epoch {ep}: only monitors {started} showed frame 0 (the others were pre-empted: the screens differ)", max_examples)
        for m in range(n_mon):
            idx = first_rec.get((m, ep, 0))
            if idx is None:
                continue
            r = recs[m][idx]
            firsts[m] = r
            if not (r[FLAGS] & 2) or r[J] != e["J0"] or r[K] != e["start_k"][m]:
                c7.hit(f"epoch {ep} monitor {m}: first refresh {r[K]} (planned {e['start_k'][m]}), j {r[J]} (J0 {e['J0']}), "
                       f"flags {r[FLAGS]}", max_examples)
            # nothing earlier in the epoch on this monitor
            if idx > 0 and recs[m][idx - 1][EP] == ep:
                c7.hit(f"epoch {ep} monitor {m}: refreshes of the epoch before frame 0", max_examples)
        if master in firsts:
            for m, r in firsts.items():
                if m == master:
                    continue
                dl = (r[TGT] + off[m]) - (firsts[master][TGT] + off[master])
                if abs(dl) > 0.5 * period[m] + 0.2e-3:
                    c7b.hit(f"epoch {ep} monitor {m}: first frame {dl * 1e3:+.2f} ms from the master in light "
                            f"(limit {0.5 * period[m] * 1e3 + 0.2:.2f})", max_examples)
    # C8: after a command that stops presenting (seek, pause) nothing of the old epoch may be presented
    for ev in events:
        if ev["kind"] not in ("seek_cmd", "pause"):
            continue
        t_exec = ev["d1"]
        # the epoch in force: the highest epoch among refreshes prepared up to t_exec on the master
        rows = recs.get(master, [])
        svc_list = [r[SVC] for r in rows]
        i = bisect.bisect_right(svc_list, t_exec) - 1
        if i < 0:
            continue
        e_old = rows[i][EP]
        for m, mrows in recs.items():
            svcs = [r[SVC] for r in mrows]
            j0 = bisect.bisect_right(svcs, t_exec + 1e-9)
            for r in mrows[j0:j0 + 3]:
                if r[EP] <= e_old:
                    c8.hit(f"monitor {m} refresh {r[K]} at {r[SVC]:.4f}: epoch {r[EP]} presented after {ev['kind']} executed at {t_exec:.4f}",
                           max_examples)
                    break
                break

    # ---- C11: seam alignment at the aim row, from the first appearance of every frame ---------------------------
    seam_ms = defaultdict(list)
    if master in recs:
        master_first = {(ep, o): i for (m, ep, o), i in first_rec.items() if m == master}
        for m in range(n_mon):
            if m == master:
                continue
            for (mm, ep, o), i in first_rec.items():
                if mm != m or (ep, o) not in master_first:
                    continue
                if o == 0:
                    continue
                rm = recs[master][master_first[(ep, o)]]
                rf = recs[m][i]
                dl = (rf[TGT] + off[m]) - (rm[TGT] + off[master])
                seam_ms[m].append(dl * 1e3)
                # the master may show a frame later than it started (late service); only use the clean ones
                if abs(dl) > 0.5 * period[m] + 0.5e-3:
                    c11.hit(f"monitor {m} epoch {ep} frame {o}: {dl * 1e3:+.2f} ms from the master in light", max_examples)

    # ---- tallies ---------------------------------------------------------------------------------------------
    counts = {
        "swaps": sum(1 for ev in events if ev["kind"] == "load"),
        "swap_failures": sum(1 for ev in events if ev["kind"] == "load_failed"),
        "seeks": sum(1 for ev in events if ev["kind"] == "seek_cmd" and ev["i0"] == 1),
        "resume_seeks": sum(1 for ev in events if ev["kind"] == "seek_cmd" and ev["i0"] == 0),
        "pauses": sum(1 for ev in events if ev["kind"] == "pause"),
        "resumes": sum(1 for ev in events if ev["kind"] == "resume"),
        "restarts": sum(1 for ev in events if ev["kind"] == "restart"),
        "epochs": len(epochs),
        "versions": len(versions),
        "shots_latched": len(capture_of),
        "refreshes": {mons[m]["name"]: len(recs.get(m, [])) for m in range(n_mon)},
        "decided_frames": len(d["starts"]),
        "reaims": sum(1 for ev in events if ev["kind"] == "reaim"),
    }
    seam = {}
    for m, v in seam_ms.items():
        v.sort()
        seam[mons[m]["name"]] = {"n": len(v), "p5": v[int(0.05 * (len(v) - 1))], "p50": v[len(v) // 2],
                                 "p95": v[int(0.95 * (len(v) - 1))], "max_abs": max(abs(v[0]), abs(v[-1]))}
    # adversarial seeks: requested between 50 ms before and 150 ms after a version was published
    seek_times = sorted(ev["t"] for ev in events if ev["kind"] == "seek_cmd" and ev["i0"] == 1)
    adv = 0
    for ts in seek_times:
        lo = bisect.bisect_left(load_times, ts - 0.150)
        if lo < len(load_times) and load_times[lo] <= ts + 0.050:
            adv += 1
    counts["adversarial_seeks"] = adv
    return C, counts, seam


def report(path, d, C, counts, seam):
    mons = d["monitors"]
    lines = [f"perf1 cue log {path}"]
    lines.append("  monitors: " + ", ".join(
        f"{m['name']} {m['rate_hz']:.3f} Hz{' (master)' if m['index'] == d['master'] else ''}" +
        ("" if m["index"] == d["master"] else f" [{m['p']} per {m['q']} master refreshes]") for m in mons))
    cfg = d["config"]
    lines.append(f"  clock: step {cfg['step_s'] * 1e3:.4f} ms per master refresh, hysteresis {cfg['hysteresis']}, "
                 f"re-aim {cfg['reaim']}, eta {cfg['eta_s'] * 1e3:.3f} ms")
    lines.append("  run: " + ", ".join(f"{k} {v}" for k, v in counts.items() if not isinstance(v, dict)))
    lines.append("  refreshes: " + ", ".join(f"{k} {v}" for k, v in counts["refreshes"].items()))
    groups = defaultdict(list)
    for c in C.values():
        groups[c.group].append(c)
    totals = {}
    for g in ("wrong frames", "shots changed", "breaks", "clock", "info"):
        cs = groups.get(g, [])
        totals[g] = sum(c.count for c in cs if c.must_be_zero)
    lines.append("RESULT")
    lines.append(f"  wrong frames ............ {totals['wrong frames']}")
    lines.append(f"  shots changed on screen . {totals['shots changed']}")
    lines.append(f"  break failures .......... {totals['breaks']}")
    lines.append(f"  clock rule violations ... {totals['clock']}")
    for g in ("wrong frames", "shots changed", "breaks", "clock", "info"):
        for c in groups.get(g, []):
            flag = "FAIL" if c.must_be_zero and c.count else "ok  " if c.must_be_zero else "info"
            lines.append(f"    {flag} {c.key:6s} {c.count:7d}  {c.title}")
            for ex in c.examples:
                lines.append(f"             e.g. {ex}")
    for name, s in seam.items():
        lines.append(f"  seam offset at the aim row, {name} minus master (ms, first appearance of each frame): "
                     f"p5 {s['p5']:+.3f}, median {s['p50']:+.3f}, p95 {s['p95']:+.3f}, max |x| {s['max_abs']:.3f} (n {s['n']})")
    bad = sum(1 for c in C.values() if c.must_be_zero and c.count)
    lines.append("VERDICT: " + ("PASS" if not bad else f"FAIL ({bad} checks)"))
    return "\n".join(lines), totals, bad


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("log")
    ap.add_argument("--clock-tol-ms", type=float, default=0.5,
                    help="how far apart a version's publication and a capture may be and count as simultaneous")
    ap.add_argument("--json", type=Path, default=None, help="write the summary here")
    ap.add_argument("--max-examples", type=int, default=5)
    a = ap.parse_args(argv)
    path, d = load(a.log)
    C, counts, seam = analyze(d, a.clock_tol_ms * 1e-3, a.max_examples)
    text, totals, bad = report(path, d, C, counts, seam)
    print(text)
    if a.json:
        a.json.write_text(json.dumps({
            "log": str(path), "counts": counts, "seam_ms": seam, "totals": totals, "verdict": "PASS" if not bad else "FAIL",
            "checks": {k: {"count": c.count, "must_be_zero": c.must_be_zero, "title": c.title, "examples": c.examples}
                       for k, c in C.items()}}, indent=1), encoding="utf-8")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
