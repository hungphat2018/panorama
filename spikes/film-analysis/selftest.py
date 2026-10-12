"""Unit checks for the pure parts of the worker (no GPU, no video file needed except where a tiny clip is made with ffmpeg).

    tools\\venv-analysis\\Scripts\\python.exe -I spikes\\film-analysis\\selftest.py

Covers: the file key, the frame table and its timeline rule (negative timestamps, discarded packets, repeated timestamps, missing
timestamps), the nominal frame rate / variable-frame-rate test, bar-shaped crop evidence and the vocabulary, segment planning,
shot boundaries from model scores, the window alignment of the shot model against a fake session, colour conversion against
OpenCV, the slide-run finder on a synthetic pan, subtitle alignment parsing, subject tracking and the keep-clear map.
"""
import os
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from film_analysis import bars_crop, file_key, frame_table as ft, segments as sg, shots_transnet as st  # noqa: E402
from film_analysis import slides_motion as sm, subjects_faces as sf, subtitles_events as se  # noqa: E402
from film_analysis.segment_worker import yuv_to_rgb_small  # noqa: E402

RESULTS = []


def check(name):
    def deco(fn):
        try:
            fn()
            RESULTS.append((name, True, ""))
        except Exception:  # noqa: BLE001
            RESULTS.append((name, False, traceback.format_exc(limit=3)))
        return fn
    return deco


@check("file key: samples, size and determinism")
def _():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "a.bin")
        data = np.random.default_rng(1).integers(0, 256, 5 * (1 << 20), dtype=np.uint8).tobytes()
        open(p, "wb").write(data)
        k1 = file_key.sample_key(p)
        assert k1 == file_key.sample_key(p)
        open(p, "r+b").write(b"x")                       # change inside the first sample
        assert file_key.sample_key(p)["sample_sha256"] != k1["sample_sha256"]
        open(p, "wb").write(data[:1000])                 # small file: hashed whole
        assert file_key.sample_key(p)["size_bytes"] == 1000
        q = os.path.join(d, "renamed.bin")
        os.replace(p, q)
        assert file_key.sample_key(q)["sample_sha256"] == file_key.sample_key(q)["sample_sha256"]


@check("frame table: B-frame order, negative pts, discard flags, repeats, rule in doubles")
def _():
    # decode order with B-frames: pts 0, 3, 1, 2, then -2000 (discarded by an edit list), a repeat of pts 3, no-pts packet
    pk = [(0, -1, 1, "K__"), (3, 0, 1, "___"), (1, 1, 1, "___"), (2, 2, 1, "___"), (-2000, -2001, 1, "KD_"), (3, 3, 1, "___"), (None, 4, 1, "___")]
    t = ft.table_from_packets(pk, 1, 24)
    assert t.pts.tolist() == [0, 1, 2, 3], t.pts
    assert t.stats["dropped_discard_flag"] == 1 and t.stats["dropped_repeated_pts"] == 1 and t.stats["dropped_no_pts"] == 1
    neg = ft.table_from_packets([(-5, 0, 2, "K__"), (-3, 1, 2, "___"), (-1, 2, 2, "___")], 1, 90000)
    s = neg.seconds()
    assert s[0] == 0.0 and abs(s[1] - 2 / 90000) < 1e-15
    assert neg.seconds()[2] == (-1 * (1 / 90000)) - (-5 * (1 / 90000))     # the rule, double for double
    assert neg.cue_starts([0, 1, 2]) == [0.0, float((s[0] + s[1]) / 2), float((s[1] + s[2]) / 2)]
    assert neg.frame_at(-1.0) == 0 and neg.frame_at(s[1]) == 1 and neg.frame_at(s[1] - 1e-9) == 0


@check("frame table: raw streams without timestamps get a synthetic table")
def _():
    pk = [(None, None, 48000, "K__")] + [(None, None, 48000, "___")] * 9
    t = ft.table_from_packets(pk, 1, 1200000)
    assert t.source == "synthetic" and t.n == 10 and abs(t.seconds()[9] - 0.36) < 1e-12


@check("frame rate: 23.976 with millisecond ticks is constant, mixed 24/30/60 is variable")
def _():
    from fractions import Fraction
    n = 480
    pts = np.round(np.arange(n) * 1001 / 24).astype(np.int64)
    t = ft.FrameTable(1, 1000, pts, np.zeros(n, bool), 42)
    r = ft.frame_rate_facts(t, Fraction(24000, 1001), Fraction(24000, 1001))
    assert r["nominal_rate"] == [24000, 1001] and not r["vfr"], r
    # an MP4 in a 1/16000 clock whose timestamps were rounded to milliseconds: steps of 656 and 672 ticks, still constant
    ms = np.round(np.arange(n) * 1001 / 24).astype(np.int64) * 16
    t3 = ft.FrameTable(1, 16000, ms, np.zeros(n, bool), 667)
    assert not ft.frame_rate_facts(t3, Fraction(24000, 1001), Fraction(24000, 1001))["vfr"]
    gap = np.delete(pts, 200)                                    # one dropped frame: everything after it is off the grid
    assert ft.frame_rate_facts(ft.FrameTable(1, 1000, gap, np.zeros(n - 1, bool), 42), Fraction(24000, 1001), None)["vfr"]
    steps = np.concatenate([np.full(100, 42), np.full(100, 33), np.full(100, 17)])
    t2 = ft.FrameTable(1, 1000, np.concatenate([[0], np.cumsum(steps)]).astype(np.int64), np.zeros(301, bool), 17)
    assert ft.frame_rate_facts(t2, None, None)["vfr"]


@check("crop evidence: bar-shaped rectangles only; vocabulary finds the true modes; small logo rectangle is rejected")
def _():
    W, H = 1920, 1080
    rect = np.array([[0, 1919, 138, 941], [0, 1919, 0, 1079], [240, 1679, 0, 1079], [848, 1209, 522, 604], [0, 1919, 140, 941], [5, 20, 5, 20]], np.int16)
    ok = bars_crop.valid_mask(rect, W, H)
    assert ok.tolist() == [True, True, True, False, True, False], ok
    items = []
    for r, w in zip(rect[[0, 1, 2, 4]], (400, 300, 300, 400)):
        items.append({"union": r.astype(np.int32), "agree": 1.0, "weight": w})
    vocab = bars_crop.build_vocabulary(items)
    assert len(vocab) == 3, vocab                                   # scope (two rectangles 2 px apart merge), none, pillar
    scope = next(m for m in vocab if m["rect"][2] > 100 and m["rect"][0] == 0 and m["rect"][1] == 1919)
    assert scope["rect"][2] == 138 and scope["share"] > 0.5
    assert bars_crop.snap(np.array([0, 1919, 150, 930]), vocab) is not None
    ev = [{"union": np.array([0, 1919, 138, 941]), "agree": 1.0, "n_valid": 50, "n_frames": 50},
          {"union": None, "agree": 0.0, "n_valid": 0, "n_frames": 30},
          {"union": np.array([0, 1919, 0, 1079]), "agree": 1.0, "n_valid": 4, "n_frames": 40}]
    crops = bars_crop.assign_shot_crops(ev, vocab, W, H)
    assert crops[0]["rect"] == [0, 138, 1920, 942] and crops[1]["held_from_previous"] and crops[1]["rect"] == crops[0]["rect"]
    assert crops[2]["evidence"] == "dark" and crops[2]["rect"] == [0, 0, 1920, 1080]


@check("segments: cover everything once, cut on keyframes, follow the playhead, skip done intervals")
def _():
    n, fps = 12000, 24.0
    secs = np.arange(n) / fps
    key = np.zeros(n, bool)
    key[::110] = True
    segs = sg.plan(secs, key, [], 5000, 45.0)
    assert sum(b - a for a, b in segs) == n and segs[0][0] == int(np.nonzero(key[:5001])[0][-1])
    assert all(key[a] for a, _ in segs if a > 0) and all(b == n or key[b] for _, b in segs)
    assert segs[1][0] == segs[0][1] and (segs[0][1] - segs[0][0]) / fps < 20          # the ramp: short first segments
    covered = sorted(segs)
    assert covered[0][0] == 0 and all(covered[i][1] == covered[i + 1][0] for i in range(len(covered) - 1))
    rest = sg.plan(secs, key, [[0, 6050]], 100, 45.0)
    assert min(a for a, _ in rest) == 6050 and sum(b - a for a, b in rest) == n - 6050
    d0, d1 = sg.decode_range(key, 5500, 6050, n)
    assert key[d0] and d0 <= 5500 - 77 and d1 == 6050 + 75


@check("shot boundaries: cuts, gradual transitions from both heads, unscanned stretches at keyframes")
def _():
    n = 400
    single = np.zeros(n, np.float16)
    many = np.zeros(n, np.float16)
    single[99] = 0.97                                            # cut between 99 and 100
    single[199:202] = 0.8                                        # 3 frames flagged: gradual
    many[299:306] = 0.9                                          # a dissolve only the many-hot head sees
    scanned = np.ones(n, np.uint8)
    scanned[350:] = 0
    key = np.zeros(n, bool)
    key[[0, 100, 360, 380]] = True
    b = st.tn_boundaries(single, many, scanned, key)
    assert [(x["frame"], x["kind"]) for x in b] == [(100, "hard"), (202, "gradual"), (306, "gradual")], b
    assert b[0]["keyframe_aligned"] and not b[2]["keyframe_aligned"]
    kb = st.keyframe_boundaries(key, scanned, {x["frame"] for x in b})
    assert [x["frame"] for x in kb] == [360, 380]


@check("shot model windows: aligned to the whole file, equal for any segment, edges repeated")
def _():
    from film_analysis.gpu_inference import ModelHub

    class FakeSession:                                            # each frame's "probability" is its own marker value
        def run(self, _out, feed):
            w = feed["frames"][0, :, 0, 0, 0].astype(np.float32)   # frame marker per window position
            logit = np.log(np.clip(w, 1, 250) / (255.0 - np.clip(w, 1, 250)))
            return [logit[None, :, None], logit[None, :, None]]

    hub = ModelHub.__new__(ModelHub)
    hub.stats = type("S", (), {"add": lambda *_: None})()
    hub.session = lambda _id: FakeSession()
    N = 523
    marker = (np.arange(N) % 200 + 1).astype(np.uint8)            # distinct enough per frame
    frames = np.zeros((N, 27, 48, 3), np.uint8)
    frames[:, :, :, 0] = marker[:, None, None]
    whole, _ = hub.tn_windows(frames, 0, N, 0, N)
    for a, b in ((0, 120), (120, 333), (333, 523)):
        lo, hi = max(0, a - 77), min(N, b + 75)
        part, _ = hub.tn_windows(frames[lo:hi], lo, N, a, b)
        assert np.array_equal(part, whole[a:b]), (a, b)
    assert np.allclose(whole, marker / 255.0, atol=1e-3)


@check("colour: BT.709 limited range matches OpenCV on flat patches")
def _():
    rng = np.random.default_rng(3)
    y = rng.integers(16, 236, (20, 1, 1), dtype=np.uint8).repeat(27, 1).repeat(48, 2)
    u = rng.integers(16, 241, (20, 1, 1), dtype=np.uint8).repeat(27, 1).repeat(48, 2)
    v = rng.integers(16, 241, (20, 1, 1), dtype=np.uint8).repeat(27, 1).repeat(48, 2)
    ours = yuv_to_rgb_small(y, u, v, "bt709", "tv", 1080)
    worst = 0
    for i in range(20):
        # OpenCV has no BT.709 limited converter for 3-plane input; compute with its float matrix as the reference
        yy, uu, vv = float(y[i, 0, 0]), float(u[i, 0, 0]) - 128, float(v[i, 0, 0]) - 128
        ref = np.clip([1.16438 * (yy - 16) + 1.79274 * vv, 1.16438 * (yy - 16) - 0.21325 * uu - 0.53291 * vv, 1.16438 * (yy - 16) + 2.11240 * uu], 0, 255)
        worst = max(worst, float(np.abs(ours[i, 0, 0].astype(float) - ref).max()))
    assert worst <= 1.0, worst


@check("slide runs: a synthetic pan is one run, a cut ends it, held frames do not break it")
def _():
    n = 200
    dx = np.zeros(n, np.float32)
    dy = np.zeros(n, np.float32)
    before = np.zeros(n, np.float32)
    peak = np.ones(n, np.float32) * 0.9
    expl = np.ones(n, np.float32)
    dx[20:100] = 6.0                                              # source px per step (4 px per grid px -> 1.5 grid px)
    before[20:100] = 5.0
    dx[40], before[40] = 0.0, 0.1                                 # one held frame inside the pan
    runs = sm.find_runs(dx, dy, peak, before, expl, set(), 24.0, 1.0, 4.0, 4.0)
    assert len(runs) == 1 and runs[0]["first"] == 20 and runs[0]["last"] == 99, runs
    cut = sm.find_runs(dx, dy, peak, before, expl, {60}, 24.0, 1.0, 4.0, 4.0)
    assert [(r["first"], r["last"]) for r in cut] == [(20, 59), (61, 99)], cut      # the pair across the cut is in no run
    assert sm.classify_purity(0.95, 0.005, 0.02) == "pure" and sm.classify_purity(0.6, 0.2, 0.3) == "layered"
    assert sm.classify_purity(0.8, 0.01, 0.1) == "unknown"


@check("pair meter: recovers a known sub-pixel shift (phase correlation + ECC) and calls a held pair held")
def _():
    rng = np.random.default_rng(5)
    base = cv2.GaussianBlur(rng.random((270 + 20, 480 + 20)).astype(np.float32) * 255, (0, 0), 2.0)
    base = base * (255.0 / base.max())

    def view(ox, oy):
        M = np.float32([[1, 0, -ox], [0, 1, -oy]])
        sh = cv2.warpAffine(base, M, (base.shape[1], base.shape[0]), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT)
        return np.ascontiguousarray(sh[10:280, 10:490])
    meter = sm.PairMeter(sm.Roi(0, 0, 480, 270), 1920, 1080)
    a, b = view(0, 0), view(2.30, -1.45)       # the content of b has moved by (-2.30, +1.45) grid px relative to a
    m = meter.measure(a, b)
    assert abs(m["dx"] - (-2.30 * 4)) < 0.5 and abs(m["dy"] - (1.45 * 4)) < 0.5, m        # source px = 4 x grid px
    assert m["explained"] > 0.8
    h = meter.measure(a, a + 0.1)
    assert h["before"] < sm.HELD_BELOW and h["dx"] == 0.0 and h["explained"] == 1.0


@check("subtitles: alignment overrides (\\an, legacy \\a), explicit placement, plain text")
def _():
    assert se.event_alignment(r"{\an8}Hi", 2) == 8 and se.event_alignment(r"{\a6}Hi", 2) == 8 and se.event_alignment(r"{\a1}Hi", 2) == 1
    assert se.event_alignment("Hi", 2) == 2 and se.event_alignment(r"{\a5}Hi", 2) == 7 and se.event_alignment(r"{\a11}Hi", 2) == 6
    assert se._EXPLICIT.search(r"\pos(10,20)") and se._EXPLICIT.search(r"\p1") and se._EXPLICIT.search(r"\pbx") is None
    assert se.plain_text(r"{\i1}Bonjour{\i0}\Nmonde") == "Bonjour monde"
    ev = [{"features": {"explicit": False, "effect_banner": False, "alignment": 8, "overlaps": 0, "overlaps_center_dialogue": False,
                        "overlaps_bottom_dialogue": False, "style": "Default", "has_style_info": True}},
          {"features": {"explicit": False, "effect_banner": False, "alignment": 8, "overlaps": 1, "overlaps_center_dialogue": True,
                        "overlaps_bottom_dialogue": True, "style": "Default", "has_style_info": True}},
          {"features": {"explicit": True, "effect_banner": False, "alignment": 2, "overlaps": 0, "overlaps_center_dialogue": False,
                        "overlaps_bottom_dialogue": False, "style": "Default", "has_style_info": True}},
          {"features": {"explicit": False, "effect_banner": False, "alignment": 2, "overlaps": 0, "overlaps_center_dialogue": False,
                        "overlaps_bottom_dialogue": False, "style": "Default", "has_style_info": True}}]
    se.classify_events(ev, "Default")
    assert [e["class"] for e in ev] == ["sign", "dialogue", "sign", "dialogue"]


@check("subjects: tracks link by overlap, models collapse, keep-clear map marks the right cells")
def _():
    samples = {0: [["face", "anime-face-v1.4s", 100, 100, 200, 200, 0.9], ["face", "yunet-2023mar", 105, 98, 195, 205, 0.8],
                   ["person", "anime-person-v1.3s", 50, 50, 600, 900, 0.7]],
               12: [["face", "anime-face-v1.4s", 110, 100, 200, 200, 0.85]]}
    r = sf.build_shot_subjects(samples, 0, 20, 1920, 1080)
    faces = [t for t in r["tracks"] if t["class"] == "face"]
    assert len(faces) == 1 and len(faces[0]["boxes"]) == 2 and len(faces[0]["models"]) == 2
    keep = sf.decode_keepclear(r["keepclear"])
    assert keep.shape == (36, 64) and keep[5, 5] > 0 and keep[35, 63] == 0 and keep.max() >= 13
    empty = sf.build_shot_subjects(samples, 100, 200, 1920, 1080)
    assert empty["samples"] == 0 and empty["keepclear"] is None


def main() -> int:
    bad = 0
    for name, ok, detail in RESULTS:
        print(("ok   " if ok else "FAIL ") + name)
        if not ok:
            bad += 1
            print(detail)
    print(f"{len(RESULTS) - bad} of {len(RESULTS)} checks passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
