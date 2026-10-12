"""Tests for analyze-cues.py on made-up logs with known answers.

A small independent oracle (this file, not the engine) writes a valid cue log: two monitors (a 240 Hz follower and a
96 Hz master, 5 follower refreshes per 2 master refreshes), 24 fps film at 4 master refreshes per frame, two epochs
(the second after a seek), three versions published at known times, and the latch rule applied the way the design says.
The analyzer must find nothing wrong with it. Then every test breaks one thing and the analyzer must flag the
check that owns that rule, and no other must-be-zero check.

  python -I test-analyze-cues.py        (last line: "N tests, M failed")
"""
import bisect
import copy
import importlib.util
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("analyze_cues", HERE / "analyze-cues.py")
ac = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ac)

FD = 1001 / 24000
STEP = FD / 4
N_FRAMES = 100
AIM_L, AIM_M = 0.0024, 0.0057      # a refresh's wake-up to the light of the aim row, laptop and external


def t_master(k):                    # master refresh 1000 + 960 e starts epoch e at time 100 + 10 e
    return 100.0 + (k - 1000) / 96.0


def t_lap(k):                       # the laptop's matching refresh lights the aim row at the same moment
    return 100.0 + (k - 2500) / 240.0 + (AIM_M - AIM_L)


def shot_of(starts, pts):
    i = 0
    for n, s in enumerate(starts):
        if s <= pts:
            i = n
    return i


def build(follower_shift=None):
    """A valid log. follower_shift=(frame, delta) moves the follower's start of that frame of epoch 0 (and the later ones)
    by delta refreshes, records included. Versions: 0 default, 1 published at t=50, 2 at t=103 (so epoch 0's third shot gets it)."""
    shots = {
        0: [dict(start=0.0, crop=None, placement=None)],
        1: [dict(start=0.0, crop=[0, 0, 1920, 1080], placement=None),
            dict(start=2.0, crop=[10, 10, 1900, 1070], placement=None),
            dict(start=4.0, crop=[20, 20, 1880, 1060], placement=None)],
        2: [dict(start=0.0, crop=[0, 0, 1920, 1080], placement={"center_mm": [354.0, 107.5], "height_mm": 215.0}),
            dict(start=2.0, crop=[30, 30, 1890, 1050], placement={"center_mm": [354.0, 107.5], "height_mm": 215.0}),
            dict(start=4.0, crop=[40, 40, 1880, 1040], placement={"center_mm": [350.0, 107.5], "height_mm": 210.0})],
    }
    loads = [(50.0, 1), (103.0, 2)]

    def newest(t):
        v = 0
        for tt, i in loads:
            if tt <= t:
                v = i
        return v

    d = {
        "schema": "panorama.perf1.cuelog/1", "n_monitors": 2, "master": 1,
        "monitors": [
            dict(index=0, name="DISPLAY1", rate_hz=240.0, scan_s=0.0037, blank_s=0.00046, aim_offset_s=AIM_L, p=5, q=2),
            dict(index=1, name="DISPLAY2", rate_hz=96.0, scan_s=0.0097, blank_s=0.00072, aim_offset_s=AIM_M, p=1, q=1)],
        "config": dict(step_s=STEP, frame_dur_s=FD, tol_s=STEP / 2, eta_s=0.00005, hysteresis=True, reaim=True,
                       own_mode=False, aim_mm=107.5, horizon=2),
        "versions": [dict(id=i, source=f"v{i}", shots=s) for i, s in shots.items()],
        "start_fields": ["ep", "ord", "j", "cut", "shift", "s0", "s1"],
        "refresh_fields": ac.FIELDS,
        "stats": dict(swaps=2, seeks=1, restarts=2, truncated=False),
        "epochs": [], "starts": [], "events": [], "refreshes": {"0": [], "1": []},
    }
    for t, i in loads:
        d["events"].append(dict(kind="load", t=t, gen=0, i0=i, i1=0, d0=0.0, d1=0.0, text=""))
    # epoch 0: frame 0 of the film at master refresh 1000; epoch 1 after a seek to film time 3.0
    origins = [0.0, 3.0]
    d["events"].append(dict(kind="seek_cmd", t=108.9, gen=1, i0=1, i1=0, d0=3.0, d1=109.0, text=""))
    recs = []
    for ep, origin in enumerate(origins):
        J0 = 1000 + 960 * ep
        kL = 2500 + 2400 * ep
        pts = [origin + n * FD for n in range(N_FRAMES)]
        d["epochs"].append(dict(ep=ep, gen=ep, origin=origin, step_s=STEP, t_plan=99.0 + 10 * ep, t_ready=99.5 + 10 * ep,
                                cost_s=0.0, J0=J0, start_k=[kL, J0], frame_pts=pts))
        d["events"].append(dict(kind="restart", t=99.0 + 10 * ep, gen=ep, i0=ep, i1=J0, d0=origin, d1=0.0, text=""))
        lap_starts = []
        for n in range(N_FRAMES):
            cut = 1 if n == 0 or shot_of([0.0, 2.0, 4.0], pts[n]) != shot_of([0.0, 2.0, 4.0], pts[n - 1]) else 0
            shift = follower_shift[1] if follower_shift and ep == 0 and n >= follower_shift[0] else 0
            lap_starts.append(kL + 10 * n + shift)
            d["starts"].append([ep, n, J0 + 4 * n, cut, 0, lap_starts[-1], J0 + 4 * n])
        for k in range(J0, J0 + 4 * N_FRAMES):
            recs.append((1, ep, k, t_master(k), (k - J0) // 4, 0.008))
        for k in range(kL, kL + 10 * N_FRAMES):
            # with a follower shift the follower starts 2 ms early and ends 2.17 ms late: both inside half a refresh
            recs.append((0, ep, k, t_lap(k) - (0.002 if follower_shift and ep == 0 else 0.0),
                         bisect.bisect_right(lap_starts, k) - 1, 0.004))
    recs.sort(key=lambda r: r[3] - r[5])          # time order of preparation
    latch = {}                                      # (ep, shot index in the film) -> (lid, vid)
    next_lid = 0
    for m, ep, k, target, o, lead in recs:
        pts = d["epochs"][ep]["frame_pts"][o]
        svc = target - lead
        sidx = shot_of([0.0, 2.0, 4.0], pts)
        flags = 0
        if (ep, sidx) not in latch:
            vid = newest(svc)
            latch[(ep, sidx)] = (next_lid, vid)
            next_lid += 1
            flags |= 1
        lid, vid = latch[(ep, sidx)]
        # the shot index inside version 0 is always 0
        si = 0 if vid == 0 else sidx
        vs = shots[vid][si]
        first = (k == d["epochs"][ep]["start_k"][m])
        if first:
            flags |= 2
        ach = ac.shot_hash(vs["crop"], vs["placement"])
        J0 = d["epochs"][ep]["J0"]
        d["refreshes"][str(m)].append([k, J0 + 4 * o, svc, target, pts, ep, o, vid, si, vs["start"], o, ach, lid, flags, 10 ** 6])
    return d


def counts(d, tol=0.0):
    C, c, seam = ac.analyze(copy.deepcopy(d), tol, 5)
    return {k: v.count for k, v in C.items() if v.count}, C


tests = []


def test(fn):
    tests.append(fn)
    return fn


def must_flag(d, key, also=(), forbid_others=True):
    flagged, C = counts(d)
    must = {k for k, v in C.items() if v.must_be_zero}
    got = {k for k in flagged if k in must}
    want = {key, *also}
    assert key in got, f"{key} not flagged; flagged {flagged}"
    if forbid_others:
        extra = got - want
        assert not extra, f"{key} expected, but also flagged {sorted(extra)}: {flagged}"


def recs_of(d, m):
    return d["refreshes"][str(m)]


@test
def valid_log_is_clean():
    flagged, C = counts(build())
    must = {k: v for k, v in flagged.items() if C[k].must_be_zero}
    assert not must, f"valid log flagged: {must}"


@test
def valid_log_counts():
    d = build()
    C, c, seam = ac.analyze(d, 0.0, 5)
    assert c["swaps"] == 2 and c["seeks"] == 1 and c["restarts"] == 2 and c["epochs"] == 2, c
    assert c["refreshes"]["DISPLAY1"] == 2 * 10 * N_FRAMES and c["refreshes"]["DISPLAY2"] == 2 * 4 * N_FRAMES, c
    assert c["shots_latched"] == 5, c
    assert max(s["max_abs"] for s in seam.values()) < 0.01, seam      # the seam is aligned in light to the microsecond


@test
def versions_reach_only_later_shots():
    d = build()
    # epoch 0: shot 0 and 1 started before v2 was published (t=103), shot 2 after: v1, v1, v2
    first_by_shot = {}
    for r in sorted(recs_of(d, 1) + recs_of(d, 0), key=lambda r: r[ac.SVC]):
        if r[ac.EP] == 0 and r[ac.FLAGS] & 1:
            first_by_shot[r[ac.SIDX]] = r[ac.VID]
    assert first_by_shot == {0: 1, 1: 1, 2: 2}, first_by_shot


@test
def version_changes_inside_a_shot_is_C4():
    d = build()
    rows = recs_of(d, 1)
    v1 = d["versions"][1]["shots"][1]
    for r in rows:   # from the middle of shot 1 of epoch 0 on, apply v2's cue to it
        if r[ac.EP] == 0 and 2.5 <= r[ac.PTS] < 4.0 and not (r[ac.FLAGS] & 1):
            r[ac.VID], r[ac.ACH] = 2, ac.shot_hash(d["versions"][2]["shots"][1]["crop"], d["versions"][2]["shots"][1]["placement"])
    must_flag(d, "C4", also=("C4b",))
    _ = v1


@test
def same_shot_latched_twice_is_C4():
    d = build()
    rows = recs_of(d, 1)
    for r in rows:
        if r[ac.EP] == 0 and r[ac.PTS] >= 1.0 and r[ac.PTS] < 1.5:
            r[ac.LID] = 9999
            r[ac.FLAGS] |= 1
    # a second capture of shot 0 in the middle of the shot
    must_flag(d, "C4", also=("C5", "C4b", "C6b"))


@test
def stale_pts_lookup_is_C2():
    d = build()
    for r in recs_of(d, 0):   # the cue of the next shot applied one frame early on the follower
        if r[ac.EP] == 0 and 1.96 < r[ac.PTS] <= 2.0:
            r[ac.VID], r[ac.SIDX], r[ac.SSTART] = 1, 1, 2.0
            r[ac.ACH] = ac.shot_hash(d["versions"][1]["shots"][1]["crop"], None)
            r[ac.LID] = [x for x in recs_of(d, 0) if x[ac.EP] == 0 and x[ac.PTS] > 2.0][0][ac.LID]
    must_flag(d, "C2", also=("C4", "C4b"))


@test
def applied_cue_differs_from_table_is_C3():
    d = build()
    r = recs_of(d, 1)[50]
    r[ac.ACH] ^= 1
    must_flag(d, "C3", also=("C4", "C4b"))


@test
def wrong_frame_early_is_C1b():
    d = build()
    recs_of(d, 1)[100][ac.ORD] += 1
    recs_of(d, 1)[100][ac.DRAWN] += 1
    recs_of(d, 1)[100][ac.PTS] += FD
    must_flag(d, "C1b", also=("C2", "C4b", "C4", "C3"), forbid_others=False)


@test
def older_frame_while_decided_is_C1b():
    d = build()
    r = recs_of(d, 1)[101]   # the frame shown is the previous one although the new one was decided
    r[ac.ORD] -= 1
    r[ac.DRAWN] -= 1
    must_flag(d, "C1b", forbid_others=False)


@test
def older_frame_not_decided_is_only_late():
    d = build()
    r = recs_of(d, 1)[101]
    r[ac.ORD] -= 1
    r[ac.DRAWN] -= 1
    r[ac.DEC] = r[ac.ORD] + 1   # the engine had not decided the newer frame yet: the decoder was behind
    flagged, C = counts(d)
    assert not C["C1b"].count and C["C1late"].count == 1, flagged


@test
def frame_start_formula_is_C1a():
    d = build()
    for row in d["starts"]:
        if row[0] == 0 and row[1] == 7:
            row[2] += 1
    flagged, C = counts(d)
    assert C["C1a"].count == 1, flagged


@test
def drawn_other_than_chosen_is_C12():
    d = build()
    recs_of(d, 0)[30][ac.DRAWN] = recs_of(d, 0)[30][ac.ORD] - 1
    must_flag(d, "C12")


@test
def monitors_disagree_on_a_frame_is_C4b():
    d = build()
    r = recs_of(d, 0)[400]
    other = [x for x in recs_of(d, 1) if x[ac.EP] == r[ac.EP] and x[ac.ORD] == r[ac.ORD]][0]
    r[ac.VID], r[ac.SIDX], r[ac.SSTART], r[ac.ACH], r[ac.LID] = 1, other[ac.SIDX], other[ac.SSTART], other[ac.ACH] ^ 5, other[ac.LID]
    must_flag(d, "C4b", also=("C3", "C4"))


@test
def capture_takes_stale_version_is_C5():
    d = build()
    # shot 2 of epoch 0 starts after v2 was published but captured v1
    for m in (0, 1):
        for r in recs_of(d, m):
            if r[ac.EP] == 0 and r[ac.SIDX] == 2:
                r[ac.VID] = 1
                r[ac.ACH] = ac.shot_hash(d["versions"][1]["shots"][2]["crop"], None)
    must_flag(d, "C5")


@test
def capture_tolerance_is_honoured():
    d = build()
    # publish v2 2 ms after the capture of shot 2: with a 5 ms tolerance either version is acceptable
    cap = min((r for m in (0, 1) for r in recs_of(d, m) if r[ac.EP] == 0 and r[ac.SIDX] == 2 and r[ac.FLAGS] & 1), key=lambda r: r[ac.SVC])
    for ev in d["events"]:
        if ev["kind"] == "load" and ev["i0"] == 2:
            ev["t"] = cap[ac.SVC] + 0.002
    for m in (0, 1):
        for r in recs_of(d, m):
            if r[ac.EP] == 0 and r[ac.SIDX] == 2:
                r[ac.VID] = 1
                r[ac.ACH] = ac.shot_hash(d["versions"][1]["shots"][2]["crop"], None)
    flagged, C = counts(d, 0.005)
    assert not C["C5"].count, flagged
    flagged, C = counts(d, 0.0005)
    assert not C["C5"].count, "v1 was the newest at the capture instant, 0.5 ms tolerance still sees v2 as later"


@test
def latch_survives_a_break_is_C6():
    d = build()
    # epoch 1 reuses the latch entry of epoch 0 for its first shot instead of capturing afresh
    e0 = [r for r in recs_of(d, 1) if r[ac.EP] == 0 and r[ac.SIDX] == 1][0]
    for m in (0, 1):
        for r in recs_of(d, m):
            if r[ac.EP] == 1 and r[ac.SIDX] == 1:
                r[ac.LID], r[ac.VID], r[ac.ACH] = e0[ac.LID], e0[ac.VID], e0[ac.ACH]
                r[ac.FLAGS] &= ~1
    must_flag(d, "C6", also=("C5", "C4", "C4b", "C3"), forbid_others=False)


@test
def record_without_capture_is_C6b():
    d = build()
    recs_of(d, 1)[10][ac.LID] = 424242
    must_flag(d, "C6b", also=("C4",), forbid_others=False)


@test
def only_the_master_starts_is_C7():
    d = build()
    d["refreshes"]["0"] = [r for r in recs_of(d, 0) if r[ac.EP] != 1]
    must_flag(d, "C7")


@test
def first_frame_off_in_light_is_C7b():
    d = build()
    for r in recs_of(d, 0):
        if r[ac.EP] == 1 and r[ac.ORD] == 0:
            r[ac.TGT] += 0.003
    flagged, C = counts(d)
    assert C["C7b"].count == 1, flagged


@test
def wrong_start_refresh_is_C7():
    d = build()
    for r in recs_of(d, 0):
        if r[ac.EP] == 1 and r[ac.ORD] == 0 and r[ac.FLAGS] & 2:
            r[ac.K] += 1
    must_flag(d, "C7", forbid_others=False)


@test
def old_epoch_after_a_seek_command_is_C8():
    d = build()
    # the seek executed at t=109.0; a refresh of epoch 0 prepared at 109.002
    rows = recs_of(d, 1)
    i = max(i for i, r in enumerate(rows) if r[ac.EP] == 0)
    extra = list(rows[i])                        # one more refresh of epoch 0, prepared after the command executed
    extra[ac.SVC] = 109.002
    extra[ac.TGT] = 109.01
    extra[ac.K] += 1
    rows.insert(i + 1, extra)
    must_flag(d, "C8", forbid_others=False)


@test
def follower_moves_mid_shot_is_C9():
    d = build(follower_shift=(20, 1))        # frame 20 is inside shot 0 (frames 0..47): one refresh later from there on
    flagged, C = counts(d)
    must = {k: v for k, v in flagged.items() if C[k].must_be_zero}
    assert must == {"C9": 1}, flagged


@test
def follower_move_at_a_cut_is_allowed():
    d = build(follower_shift=(48, 1))        # frame 48 is the first of shot 1 (film time 2.0): a cut
    flagged, C = counts(d)
    must = {k: v for k, v in flagged.items() if C[k].must_be_zero}
    assert not must and C["I1"].count == 1, (must, flagged)


@test
def follower_flip_flop_is_reported():
    d = build(follower_shift=(20, 1))
    for row in d["starts"]:
        if row[0] == 0 and row[1] >= 24:     # and back again a few frames later
            row[5] -= 1
    flagged, C = counts(d)
    assert C["I2"].count == 1 and C["C9"].count == 2, flagged


@test
def zero_length_follower_frame_is_C9b():
    d = build()
    for row in d["starts"]:
        if row[0] == 0 and row[1] == 12:
            row[5] = [r for r in d["starts"] if r[0] == 0 and r[1] == 11][0][5]
    flagged, C = counts(d)
    assert C["C9b"].count == 1, flagged


@test
def reaim_not_at_a_cut_is_C10():
    d = build()
    for row in d["starts"]:
        if row[0] == 0 and row[1] >= 30:
            row[4] = 1
            row[2] += 1
            row[6] += 1
            row[5] += 0
    flagged, C = counts(d)
    assert C["C10"].count >= 1, flagged


@test
def reaim_at_a_cut_is_allowed():
    d = build()
    for row in d["starts"]:
        if row[0] == 0 and row[1] >= 48:
            row[4] = 1
            row[2] += 1
            row[6] += 1
            row[5] += 2
    flagged, C = counts(d)
    assert not C["C10"].count and not C["C1a"].count, flagged


@test
def reaim_shortening_below_one_refresh_is_C10():
    d = build()
    # frame 47 lasts one refresh (frame 47 starts at J0+188, frame 48 at J0+189); a shift of -1 at frame 48 would make it 0
    for row in d["starts"]:
        if row[0] == 0 and row[1] == 48:
            row[2] = 1000 + 188 + 1
            row[4] = 0
        if row[0] == 0 and row[1] == 47:
            row[2] = 1000 + 188
    # make frame 48 nominally at 4*48 and shift it to the same refresh as frame 47 -> zero length
    for row in d["starts"]:
        if row[0] == 0 and row[1] == 47:
            row[2] = 1000 + 191
        if row[0] == 0 and row[1] == 48:
            row[2] = 1000 + 191
            row[4] = -1
    flagged, C = counts(d)
    assert C["C10"].count >= 1, flagged


@test
def seam_offset_too_large_is_C11():
    d = build()
    for r in recs_of(d, 0):
        if r[ac.EP] == 0 and r[ac.ORD] == 20:
            r[ac.TGT] += 0.0028
    flagged, C = counts(d)
    assert C["C11"].count == 1, flagged


@test
def frame_timestamps_must_increase_C13():
    d = build()
    d["epochs"][0]["frame_pts"][10] = d["epochs"][0]["frame_pts"][9]
    flagged, C = counts(d)
    assert C["C13"].count >= 1, flagged


@test
def truncated_log_is_C0():
    d = build()
    d["stats"]["truncated"] = True
    must_flag(d, "C0")


@test
def fingerprint_matches_the_c_code():
    # the same constants as cue-selftest.c
    assert ac.shot_hash(None, None) == 1858958381
    assert ac.shot_hash([0, 140, 1920, 940], {"center_mm": [354, 107.5], "height_mm": 215.0}) == 4221016235
    assert ac.shot_hash([0.5, 0, 1920, 1080], None) == 1664246429
    assert ac.shot_hash(None, {"center_mm": [-0.0, 0.0], "height_mm": 215.0}) == 3938428256


@test
def adversarial_seek_count():
    d = build()
    d["events"].append(dict(kind="seek_cmd", t=103.1, gen=2, i0=1, i1=0, d0=1.0, d1=103.1, text=""))     # 100 ms after the swap
    d["events"].append(dict(kind="seek_cmd", t=102.96, gen=3, i0=1, i1=0, d0=1.0, d1=102.96, text=""))   # 40 ms before it
    d["events"].append(dict(kind="seek_cmd", t=103.2, gen=4, i0=1, i1=0, d0=1.0, d1=103.2, text=""))     # 200 ms after: not adversarial
    C, c, seam = ac.analyze(d, 0.0, 5)
    assert c["adversarial_seeks"] == 2, c


def main():
    failed = 0
    for fn in tests:
        try:
            fn()
        except Exception as ex:   # noqa: BLE001 - report every failing test
            failed += 1
            print(f"FAIL {fn.__name__}: {ex}")
    print(f"{len(tests)} tests, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
