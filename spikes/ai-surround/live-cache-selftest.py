"""Side cache self-test: the rules the live worker's warm start relies on (no film, no GPU, a few seconds).

    python -I -X utf8 live-cache-selftest.py

  margins  the room's side widths round up to 32 source px: the windowed and the full-screen player's widths give one
           mosaic key; 1 px or less, or not a number, is no side
  tiling   cached parts tile a span (hidden cuts split it), longer parts first, backing out of a dead end; a zero-length
           part is ignored; no tiling is None
  spans    cached_spans(crop_at) lists only what load_analysis finds (this analysis version and crop; temporary files
           left out); a damaged analysis file loads as None
  plan     WorkerState: a split span is done only when its analysed parts tile it; new_shot_id gives one row per first
           frame (a shot analysed again, or given up at a seek, keeps its row)
"""
import os
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)                                          # python -I leaves the script's folder off sys.path
from live import side_cache, side_store  # noqa: E402
from live.analyse_v0 import ShotAnalysis  # noqa: E402
from live.film_timeline import ShotSpan  # noqa: E402
from live.worker_state import WorkerState  # noqa: E402

CROP = (0.0, 120.0, 1920.0, 960.0)
FAILS = []
CHECKS = [0]


def check(ok: bool, what: str):
    CHECKS[0] += 1
    if not ok:
        FAILS.append(what)
        print(f"FAIL: {what}", flush=True)


class FakeFilm:
    """What WorkerState reads of a film: spans, frame times, cut times and the cue sheet's crops."""

    def __init__(self, spans: list, n_frames: int, fps: float = 24.0):
        self.spans, self.n_frames, self.fps = spans, n_frames, fps
        self.times = np.arange(n_frames) / fps

    def cut_time(self, i: int) -> float:
        return float(i) / self.fps

    def crop_at(self, t: float) -> tuple:
        return CROP


def analysis(shot_id: int, first: int, n: int, crop: tuple) -> ShotAnalysis:
    return ShotAnalysis.from_arrays({
        "shot_id": shot_id, "first": first, "n": n, "crop": np.array(crop), "decode_scale": 2.0,
        "from_content_model": True, "offsets": np.zeros((n, 2)), "agree": np.ones(n, np.float32),
        "luma": np.full(n, 0.2, np.float32), "new_drawing": np.zeros(n, bool), "kept_index": np.array([0], np.int32)})


def check_margins():
    rm = side_cache.room_margins
    windowed, full = rm([817.6, 0.0, 1500.1, 275.0]), rm([817.9, 0.0, 1500.8, 275.7])
    check(windowed == full == (832.0, 0.0, 1504.0, 288.0), f"windowed {windowed} and full screen {full} round to one room")
    key = side_cache.SideCache.mosaic_key
    check(key(CROP, windowed, 2.0) == key(CROP, full, 2.0), "one mosaic key for both players")
    check(rm([960, 0, 960, 0]) == (960.0, 0.0, 960.0, 0.0), "a width on a step stays")
    check(rm([0.4, 1.0, 1.01, 33]) == (0.0, 0.0, 32.0, 64.0), "1 px or less is no side")
    check(rm([float("inf"), float("nan"), -5, 31.9]) == (0.0, 0.0, 0.0, 32.0), "not a number is no side")


def check_tiling():
    t = side_cache.tiling
    check(t({0: {300}}, 0, 300) == [(0, 300)], "a whole span")
    check(t({0: {100}, 100: {150}, 250: {50}}, 0, 300) == [(0, 100), (100, 150), (250, 50)], "a span split twice")
    check(t({0: {50, 100}, 50: {250}}, 0, 300) == [(0, 50), (50, 250)], "a dead end is backed out of")
    check(t({0: {0, 100}, 100: {200}}, 0, 300) == [(0, 100), (100, 200)], "a zero-length part is ignored")
    check(t({0: {100}}, 0, 300) is None and t({}, 0, 300) is None, "no tiling")
    check(t({0: {400}}, 0, 300) is None, "a part past the span's end")


def check_cache_and_plan(tmp: str):
    cache = side_cache.SideCache(os.path.join(tmp, "cache"), "film")
    cache.save_analysis(analysis(0, 0, 40, CROP))
    cache.save_analysis(analysis(5, 40, 20, CROP))
    cache.save_analysis(analysis(7, 60, 30, (0.0, 0.0, 1920.0, 1080.0)))         # another crop than the cue sheet's
    adir = os.path.join(cache.dir, "analysis")
    open(os.path.join(adir, "90-10-0123456789abcdef.npz"), "wb").close()        # another analysis version
    open(os.path.join(adir, f"100-5-{cache.analysis_key(CROP)}.npz.tmp.npz"), "wb").close()   # a write cut short
    with open(os.path.join(adir, f"120-8-{cache.analysis_key(CROP)}.npz"), "wb") as f:
        f.write(b"PK\x03\x04 truncated")                                         # a damaged analysis
    spans = cache.cached_spans(lambda first: CROP)
    check(spans == [(0, 40), (40, 20), (120, 8)], f"cached spans for this crop and version: {spans}")
    check(len(cache.cached_spans()) == 6, "without crop_at every name is listed")
    check(cache.load_analysis(120, 8, CROP) is None, "a damaged analysis loads as None")
    an = cache.load_analysis(40, 20, CROP)
    check(an is not None and (an.first, an.n) == (40, 20), "a cached analysis loads")

    # the plan: a span of 100 frames split at 40 and 70 (hidden cuts), then a span of 50
    film = FakeFilm([ShotSpan(0, 0, 100, True), ShotSpan(1, 100, 50, True)], 150)
    store = side_store.SideStore.create(os.path.join(tmp, "store.bin"), 150, 16, 2, 64 << 10, 1, 1920, 1080, "test.mkv")
    try:
        st = WorkerState(film, store, cache, print)
        check(not st.is_done(0, 100), "nothing analysed: not done")
        st.publish_analysis(analysis(0, 0, 40, CROP))
        check(not st.is_done(0, 100), "the first part only: not done")
        r2 = st.new_shot_id(40)
        check(r2 == 2 and st.new_shot_id(40) == r2, "one row per first frame, also before the shot is published")
        st.publish_analysis(analysis(r2, 40, 30, CROP))
        check(not st.is_done(0, 100), "two parts of three: not done")
        r3 = st.new_shot_id(70)
        st.publish_analysis(analysis(r3, 70, 30, CROP))
        check(st.is_done(0, 100), "all three parts: done")
        check(st.new_shot_id(40) == r2 and st.new_shot_id(70) == r3, "a shot analysed again keeps its row")
        check(st.new_shot_id(75) == r3 + 1, "a new first frame gets a new row")
        check(not st.is_done(100, 50), "the next span: not done")
    finally:
        store.close()


def main() -> int:
    check_margins()
    check_tiling()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        check_cache_and_plan(tmp)
    print(f"{'PASS' if not FAILS else 'FAIL'}: {CHECKS[0] - len(FAILS)}/{CHECKS[0]} checks", flush=True)
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
