"""Tests for analyze-vk-timing.py on synthetic probe logs with known answers.

Run: python -I test-analyze-vk-timing.py

The synthetic monitor is a 240 Hz panel with a 0.463 ms blank; its top rows fall on base + k * T. A fake presentation
engine reports "first pixel out" at a chosen offset from the top row, in a swapchain-local clock that runs 55 s ahead of
QPC (nanosecond ticks), or directly in QPC ticks.
"""
import importlib.util
import sys
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("avt", HERE / "analyze-vk-timing.py")
avt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(avt)

T = 1 / 240.0
BLANK = 0.463e-3
BASE = 1000.0003
QPC_FREQ = 10_000_000.0
LOCAL_AHEAD_S = 55.0


def make_log(experiments, grid_ok=True):
    return {
        "monitor": {"index": 0, "name": "DISPLAY1", "rect": [0, 0, 2560, 1600], "window": [1280, 800], "windowed": True},
        "qpc_freq": QPC_FREQ, "device": "Test GPU",
        "config": {"images": 3, "alpha": "opaque", "format": "bgra8", "fse_disallowed": True, "domain_pref": "auto",
                   "layers_off": "none", "seconds_each": 2.5},
        "surface": {"extent": [1280, 800], "min_images": 2, "max_images": 8, "alpha_flags": 3, "present_timing_supported": 1,
                    "absolute_supported": 0, "relative_supported": 1, "stage_queries": 5, "present_id2_supported": 1,
                    "present_wait2_supported": 1, "present_modes": [2, 3, 0, 1, 1000361000]},
        "time_domains": [{"domain": 1000208001, "name": "SWAPCHAIN_LOCAL", "id": 7}],
        "timing": {"refresh_hz": 240.0, "line_s": 2.3148e-6, "scan_s": 3.7037e-3, "blank_s": BLANK},
        "scan_grid": {"ok": grid_ok, "base": BASE, "period": T, "resid_p50_us": 1.2, "resid_p95_us": 4.0, "samples": 900},
        "vblanks": [BASE + k * T - BLANK + 0.0002 for k in range(0, 4000)],
        "experiments": experiments,
    }


def experiment(name, n=300, gap=1, fpo_offset_s=0.0, target=0, target_refreshes=0.0, nearest=False, wait_lag=1,
               domain=1000208001, latency_rows=2, fpo_noise=0.0, qpc_ns=False):
    """n presents; present i is shown at top row `first + i * gap`, reported at that top row + fpo_offset_s.
    qpc_ns: a QPC-domain report counts nanoseconds instead of QPC ticks."""
    start = BASE + 10 * T
    first_k = 20
    presents, feedback, cals = [], [], []
    for i in range(n):
        k = first_k + i * gap
        shown = BASE + k * T
        call = shown - latency_rows * T + 0.0004
        presents.append([i + 1, call - 1e-4, call - 5e-5, call - 3e-3 if wait_lag else 0.0, call - 1e-4 if wait_lag else 0.0,
                         0, call, call + 2e-4, 0, 0])
        fpo = shown + fpo_offset_s + ((i % 5) - 2) * fpo_noise
        qend = call + 1e-3
        if domain == avt.DOMAIN_QPC:
            unit = 1e9 if qpc_ns else QPC_FREQ
            f_raw, q_raw = int(fpo * unit), int(qend * unit)
        else:
            f_raw, q_raw = int((fpo + LOCAL_AHEAD_S) * 1e9), int((qend + LOCAL_AHEAD_S) * 1e9)
        feedback.append([i + 1, shown + 0.01, 0, domain, 7, 1, [[1, q_raw], [4, f_raw]]])
    for j in range(5):
        q = BASE + 5 + j
        cals.append([q, int((q + LOCAL_AHEAD_S) * 1e9), 40, 0])
    return {
        "name": name, "api": 2, "wait_lag": wait_lag, "timing": True, "target": target, "target_refreshes": target_refreshes,
        "nearest": nearest, "status": "", "images": 3, "domain": domain, "domain_id": 7, "refresh_duration_ns": int(T * 1e9),
        "refresh_interval_ns": int(T * 1e9), "timing_props_result": 0, "queue_full": 0, "t_start": start,
        "t_end": presents[-1][6], "calibrations": cals if domain != avt.DOMAIN_QPC else [],
        "present_fields": ["id", "acq_call", "acq_ret", "wait_call", "wait_ret", "wait_result", "call", "ret", "result", "target_raw"],
        "presents": presents, "feedback": feedback,
    }


class AnalyzeVkTiming(unittest.TestCase):
    def one(self, exp, **kw):
        res = avt.analyze(make_log([exp]), **kw)
        return res["experiments"][0]

    def test_first_pixel_out_on_the_top_row(self):
        e = self.one(experiment("v2-wait-timing", fpo_offset_s=0.0, fpo_noise=2e-6))
        self.assertAlmostEqual(e["fpo_minus_top_ms"]["p50"], 0.0, delta=0.01)
        self.assertEqual(e["fpo_reading"], "equals the top row")
        self.assertAlmostEqual(e["fpo_minus_vblank_start_ms"]["p50"], BLANK * 1e3, delta=0.01)
        self.assertAlmostEqual(e["domain_clock_vs_qpc"], 1.0, places=6)

    def test_first_pixel_out_at_the_vblank_start(self):
        e = self.one(experiment("v2-wait-timing", fpo_offset_s=-BLANK, fpo_noise=2e-6))
        self.assertAlmostEqual(e["fpo_minus_top_ms"]["p50"], -BLANK * 1e3, delta=0.01)
        self.assertIn("vblank start", e["fpo_reading"])
        self.assertAlmostEqual(e["fpo_minus_vblank_start_ms"]["p50"], 0.0, delta=0.01)

    def test_neither_instant(self):
        e = self.one(experiment("v2-wait-timing", fpo_offset_s=1.2e-3))
        self.assertTrue(e["fpo_reading"].startswith("neither"))

    def test_qpc_domain_needs_no_calibration(self):
        e = self.one(experiment("v2-wait-timing", domain=avt.DOMAIN_QPC, fpo_offset_s=0.0))
        self.assertAlmostEqual(e["fpo_minus_top_ms"]["p50"], 0.0, delta=0.001)
        self.assertEqual(e["fpo_reading"], "equals the top row")
        self.assertEqual(e["qpc_unit"], "ticks")

    def test_qpc_domain_counted_in_nanoseconds_is_recognised(self):
        # same picture times, but the raw values are nanoseconds of the QPC clock: the unit is detected, not assumed
        e = self.one(experiment("v2-wait-timing", domain=avt.DOMAIN_QPC, fpo_offset_s=0.0, qpc_ns=True))
        self.assertEqual(e["qpc_unit"], "ns")
        self.assertAlmostEqual(e["fpo_minus_top_ms"]["p50"], 0.0, delta=0.001)
        self.assertEqual(e["fpo_reading"], "equals the top row")
        text = "\n".join(avt.lines(avt.analyze(make_log([experiment("v2-wait-timing", domain=avt.DOMAIN_QPC, qpc_ns=True)]))))
        self.assertIn("qpc unit ns", text)

    def test_qpc_unit_unknown_without_reports(self):
        scale, unit = avt.qpc_domain_scale([], QPC_FREQ)
        self.assertEqual(unit, "unknown")
        self.assertAlmostEqual(scale, 1 / QPC_FREQ)

    def test_clock_that_runs_fast_is_followed_by_the_interpolation(self):
        exp = experiment("v2-wait-timing", fpo_offset_s=0.0)
        # the local clock gains 100 ppm on QPC: rebuild raw values and calibrations accordingly
        rate = 1.0001
        def local(q):
            return int((q - BASE) * rate * 1e9 + 5e12)
        exp["calibrations"] = [[BASE + 5 + j, local(BASE + 5 + j), 40, 0] for j in range(0, 60, 6)]
        for fb in exp["feedback"]:
            pid = fb[0]
            shown = BASE + (20 + (pid - 1)) * T
            qend = exp["presents"][pid - 1][6] + 1e-3
            fb[6] = [[1, local(qend)], [4, local(shown)]]
        e = self.one(exp)
        self.assertAlmostEqual(e["fpo_minus_top_ms"]["p50"], 0.0, delta=0.01)
        self.assertAlmostEqual(e["domain_clock_vs_qpc"], rate, places=5)

    def test_relative_target_honoured(self):
        e = self.one(experiment("rel-2", gap=2, target=1, target_refreshes=2.0))
        self.assertTrue(e["target_check"]["honoured"])
        self.assertAlmostEqual(e["fpo_gap_refreshes"]["p50"], 2.0, places=3)
        self.assertEqual(e["fpo_gap_counts"], {"2": e["fpo_gap_counts"]["2"]})

    def test_relative_target_ignored(self):
        e = self.one(experiment("rel-3", gap=1, target=1, target_refreshes=3.0))
        self.assertFalse(e["target_check"]["honoured"])

    def test_nearest_refresh_cycle_rounds_down(self):
        e = self.one(experiment("rel-2.4-nearest", gap=2, target=1, target_refreshes=2.4, nearest=True))
        self.assertTrue(e["target_check"]["honoured"])
        e = self.one(experiment("rel-2.4", gap=2, target=1, target_refreshes=2.4, nearest=False))
        self.assertFalse(e["target_check"]["honoured"])  # without the flag 2.4 must round up to 3

    def test_pace_against_the_refresh_rate(self):
        e = self.one(experiment("v2-wait-timing"))
        self.assertAlmostEqual(e["presents_per_refresh"], 1.0, delta=0.01)
        e = self.one(experiment("slow", gap=2))
        self.assertAlmostEqual(e["presents_per_refresh"], 0.5, delta=0.01)

    def test_missing_grid_still_reports_pace(self):
        res = avt.analyze(make_log([experiment("v2-wait-timing")], grid_ok=False))
        e = res["experiments"][0]
        self.assertIn("presents_per_s", e)
        self.assertNotIn("fpo_minus_top_ms", e)

    def test_lines_print_without_error(self):
        res = avt.analyze(make_log([experiment("v2-wait-timing"), experiment("rel-2", gap=2, target=1, target_refreshes=2.0)]))
        text = "\n".join(avt.lines(res))
        self.assertIn("equals the top row", text)
        self.assertIn("honoured", text)

    def test_short_experiment_is_flagged(self):
        e = self.one(experiment("tiny", n=2))
        self.assertEqual(e.get("note"), "too few presents")


if __name__ == "__main__":
    unittest.main()
