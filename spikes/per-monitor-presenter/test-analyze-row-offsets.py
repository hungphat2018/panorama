"""Tests for the row-light part of analyze-per-monitor-pacing.py, on made-up logs whose answers are known.

The logs are built from Setup A's exact timings (laptop 2560x1600 in 2720x1800 at 240 Hz, external 1920x1080 in
2000x1160 at 96 Hz) with every film frame reaching both screens on blank starts that coincide, so the seam offset in
light is just the blank difference plus the scan-speed difference at each height (research 3.2: external minus laptop
+0.26 / +2.29 / +4.33 ms at the top / middle / bottom row).

Usage: python -I test-analyze-row-offsets.py
"""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("pacing", HERE / "analyze-per-monitor-pacing.py")
pacing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pacing)

T0 = 5000.0          # blank start of the first film frame on both screens (QPC seconds)
FPS = 24.0           # 10 laptop refreshes and 4 external refreshes per film frame
SYNC_DELAY = 0.05e-3  # DXGI SyncQPCTime comes a little after the blank starts
FRAMES = 40
HEIGHTS = (0.0, 107.5, 215.0)


def make_log(index, name, refresh_hz, active_h, total_h, height_mm, top_mm=0.0, lag_ms=0.0, old=False):
    """A presenter log for one monitor: FRAMES film frames, each first shown on a blank start at T0 + n / FPS."""
    line_s = 1.0 / (refresh_hz * total_h)
    scan_s = active_h * line_s
    blank_s = (total_h - active_h) * line_s
    period = 1.0 / refresh_hz
    first_blank = T0
    presents, stats, vblanks = [], [], []
    for k in range(int(refresh_hz * (FRAMES + 3) / FPS)):  # a wake-up per refresh, 90 us after the blank starts
        vblanks.append(first_blank + k * period + 0.09e-3)
    for n in range(FRAMES):
        blank_start = T0 + n / FPS
        present_id = n + 1
        ret = blank_start - 0.002                      # the present returned just before its refresh
        presents.append([present_id, ret - 0.0003, ret, blank_start, n])
        stats.append([present_id, 0, 0, blank_start + SYNC_DELAY, ret + 0.001])
    log = {"monitor": index, "name": name, "rect": [0, 0, 1, 1], "mode": "nearest", "fps": FPS, "t0": T0 - 3.0,
           "master": 1, "error": "", "composition_modes": {"1": 100}, "vblanks": vblanks, "presents": presents,
           "stats": stats}
    if not old:
        log.update({
            "windowed": False,
            "timing": {"refresh_hz": refresh_hz, "line_s": line_s, "scan_s": scan_s, "blank_s": blank_s,
                       "active": [1, active_h], "total": [1, total_h]},
            "scan_grid": {"ok": True, "base": first_blank + blank_s, "period": period, "resid_p50_us": 1.0,
                          "resid_p95_us": 3.0, "samples": 1000, "taken": 1000},
            "scan_grid_at_start": {"ok": True, "base": first_blank + blank_s + 4e-6, "period": period,
                                   "resid_p50_us": 1.0, "resid_p95_us": 3.0, "samples": 500, "taken": 500},
            "wake_to_top_ms": (blank_s - 0.09e-3) * 1e3, "wake_to_top_end_ms": (blank_s - 0.09e-3) * 1e3 + 0.01,
            "live_refits": 14,
            "geometry": {"height_mm": height_mm, "top_mm": top_mm, "lag_ms": lag_ms},
            "aim_mm": 107.5, "shared_mm": [0.0, 215.0]})
    return log


def laptop(**kw):
    return make_log(0, "DISPLAY1", 240.0, 1600, 1800, 215.0, **kw)


def external(**kw):
    return make_log(1, "DISPLAY2", 96.0, 1080, 1160, 268.0, **kw)


def analyze(logs, skip_s=1.0):
    firsts = [pacing.summarize(log, skip_s)["first_shown"] for log in logs]
    return pacing.row_offsets(logs, firsts)


def scan_ms(timing, fraction):
    return timing["scan_s"] * fraction * 1e3


class RowOffsetTests(unittest.TestCase):
    def test_aligned_blank_starts_match_the_research_table(self):
        result = analyze([laptop(), external()])
        offsets = result["offset_ms"]
        self.assertAlmostEqual(offsets["top"]["median"], 0.26, delta=0.01)
        self.assertAlmostEqual(offsets["aim"]["median"], 2.29, delta=0.01)
        self.assertAlmostEqual(offsets["bottom"]["median"], 4.33, delta=0.01)
        self.assertEqual(result["heights_mm"], {"top": 0.0, "aim": 107.5, "bottom": 215.0})
        self.assertEqual(result["first"], "DISPLAY1")
        self.assertEqual(result["second"], "DISPLAY2")

    def test_offset_is_closed_form(self):
        a, b = laptop(), external()
        result = analyze([a, b])
        for label, y in zip(("top", "aim", "bottom"), HEIGHTS):
            expected = (b["timing"]["blank_s"] - a["timing"]["blank_s"]) * 1e3 \
                + scan_ms(b["timing"], y / 268.0) - scan_ms(a["timing"], y / 215.0)
            self.assertAlmostEqual(result["offset_ms"][label]["median"], expected, delta=0.006, msg=label)

    def test_steady_offset_has_no_spread(self):
        stats = analyze([laptop(), external()])["offset_ms"]["aim"]
        self.assertLessEqual(stats["max"] - stats["min"], 0.011)
        # the earliest frame of each monitor is dropped (its first retained present is not its first appearance)
        self.assertEqual(stats["frames"], FRAMES - 1)

    def test_panel_lag_moves_every_row_by_the_lag(self):
        base = analyze([laptop(), external()])["offset_ms"]
        lagged = analyze([laptop(), external(lag_ms=1.5)])["offset_ms"]
        for label in ("top", "aim", "bottom"):
            self.assertAlmostEqual(lagged[label]["median"] - base[label]["median"], 1.5, delta=0.006, msg=label)

    def test_height_offset_shifts_the_follower_rows(self):
        # external 10 mm lower: at height y its row (y - 10) / 268 of the way down; band 10..215, aim 112.5
        a = laptop()
        b = external(top_mm=10.0)
        for log in (a, b):
            log["shared_mm"] = [10.0, 215.0]
            log["aim_mm"] = 112.5
        result = analyze([a, b])
        self.assertEqual(result["heights_mm"], {"top": 10.0, "aim": 112.5, "bottom": 215.0})
        for label, y in zip(("top", "aim", "bottom"), (10.0, 112.5, 215.0)):
            expected = (b["timing"]["blank_s"] - a["timing"]["blank_s"]) * 1e3 \
                + scan_ms(b["timing"], (y - 10.0) / 268.0) - scan_ms(a["timing"], y / 215.0)
            self.assertAlmostEqual(result["offset_ms"][label]["median"], expected, delta=0.006, msg=label)

    def test_heights_fall_back_to_the_geometry_without_shared_and_aim(self):
        a, b = laptop(), external(top_mm=30.0)
        for log in (a, b):
            del log["shared_mm"], log["aim_mm"]
        self.assertEqual(pacing.seam_heights([a, b]), (30.0, 122.5, 215.0))

    def test_predicted_values_are_carried_into_the_result(self):
        a, b = laptop(), external()
        a["predicted_ms"] = {"top": 0.26, "aim": 2.29, "bottom": 4.33}
        result = analyze([a, b])
        self.assertEqual(result["predicted_ms"], a["predicted_ms"])
        text = "\n".join(pacing.row_offset_lines(result))
        self.assertIn("predicted +2.29", text)

    def test_grid_checks_report_the_snapping_margin(self):
        a = laptop()
        result = analyze([a, external()])
        check = result["checks"]["DISPLAY1"]
        blank_ms = (a["timing"]["blank_s"] - SYNC_DELAY) * 1e3
        self.assertAlmostEqual(check["sync_to_top_ms"]["median"], blank_ms, delta=0.002)
        self.assertAlmostEqual(check["wake_median_to_top_ms"], (a["timing"]["blank_s"] - 0.09e-3) * 1e3, delta=0.002)
        self.assertAlmostEqual(check["wake_to_top_ms"], (a["timing"]["blank_s"] - 0.09e-3) * 1e3, delta=0.002)
        self.assertAlmostEqual(check["wake_to_top_end_ms"], (a["timing"]["blank_s"] - 0.09e-3) * 1e3 + 0.01, delta=0.002)
        # the start fit sits 4 us later than the end fit: drift = start - end = +4 us
        self.assertAlmostEqual(check["grid_drift_us"], 4.0, delta=0.2)
        self.assertEqual(check["live_refits"], 14)

    def test_statistics_off_the_blank_are_flagged(self):
        good = analyze([laptop(), external()])
        self.assertEqual(good["checks"]["DISPLAY1"]["sync_in_blank_fraction"], 1.0)
        self.assertFalse(any("WARNING" in line for line in pacing.row_offset_lines(good)))
        b = external()
        b["stats"] = [[s[0], s[1], s[2], s[3] + 3.1e-3, s[4]] for s in b["stats"]]  # a composed window: anywhere in the refresh
        bad = analyze([laptop(), b])
        self.assertLess(bad["checks"]["DISPLAY2"]["sync_in_blank_fraction"], 0.1)
        warnings = [line for line in pacing.row_offset_lines(bad) if "WARNING" in line]
        self.assertEqual(len(warnings), 1)
        self.assertIn("DISPLAY2", warnings[0])

    def test_end_fit_missing_falls_back_to_the_start_fit(self):
        a = laptop()
        a["scan_grid"]["ok"] = False
        self.assertEqual(pacing.scan_grid_of(a), a["scan_grid_at_start"])
        b = laptop()
        b["scan_grid"]["ok"] = False
        b["scan_grid_at_start"]["ok"] = False
        self.assertIsNone(pacing.scan_grid_of(b))
        self.assertFalse(pacing.has_rows(b))

    def test_top_after_and_nearest(self):
        grid = {"base": 10.0, "period": 0.5}
        self.assertAlmostEqual(pacing.top_after(grid, 10.0), 10.0)
        self.assertAlmostEqual(pacing.top_after(grid, 10.0001), 10.5)
        self.assertAlmostEqual(pacing.top_after(grid, 9.7), 10.0)
        self.assertAlmostEqual(pacing.top_after(grid, 12.2), 12.5)
        self.assertAlmostEqual(pacing.top_nearest(grid, 10.2), 10.0)
        self.assertAlmostEqual(pacing.top_nearest(grid, 10.3), 10.5)

    def test_one_monitor_has_no_seam(self):
        a = laptop()
        self.assertIsNone(pacing.row_offsets([a], [pacing.summarize(a, 1.0)["first_shown"]]))

    def test_frames_missing_on_one_monitor_are_left_out(self):
        a, b = laptop(), external()
        b["presents"] = [p for p in b["presents"] if p[4] % 2 == 0]  # the external only shows even frames
        result = analyze([a, b])
        self.assertEqual(result["offset_ms"]["aim"]["frames"], FRAMES // 2 - 1)
        self.assertAlmostEqual(result["offset_ms"]["aim"]["median"], 2.29, delta=0.01)


class OldLogTests(unittest.TestCase):
    def test_old_logs_have_no_row_section_but_still_parse(self):
        logs = [laptop(old=True), external(old=True)]
        self.assertFalse(pacing.has_rows(logs[0]))
        firsts = [pacing.summarize(log, 1.0)["first_shown"] for log in logs]
        self.assertIsNone(pacing.row_offsets(logs, firsts))
        self.assertIn("no row-timing fields", pacing.row_offset_lines(None)[0])

    def test_main_runs_on_a_folder_of_old_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            for log in (laptop(old=True), external(old=True)):
                Path(tmp, f"monitor-{log['monitor']}-nearest.json").write_text(json.dumps(log))
            import sys
            old_argv = sys.argv
            sys.argv = ["analyze-per-monitor-pacing.py", tmp]
            try:
                pacing.main()
            finally:
                sys.argv = old_argv
            summary = json.loads(Path(tmp, "pacing-summary.json").read_text())
            self.assertIn("offset_ms", summary["nearest"])
            self.assertNotIn("row_offset", summary["nearest"])

    def test_main_adds_the_row_section_for_new_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            for log in (laptop(), external()):
                Path(tmp, f"monitor-{log['monitor']}-nearest.json").write_text(json.dumps(log))
            import sys
            old_argv = sys.argv
            sys.argv = ["analyze-per-monitor-pacing.py", tmp]
            try:
                pacing.main()
            finally:
                sys.argv = old_argv
            summary = json.loads(Path(tmp, "pacing-summary.json").read_text())
            self.assertIn("offset_ms", summary["nearest"])
            self.assertAlmostEqual(summary["nearest"]["row_offset"]["offset_ms"]["aim"]["median"], 2.29, delta=0.01)

    def test_describe_matches_the_original_percentiles(self):
        stats = pacing.describe([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0])
        self.assertEqual(stats, {"frames": 10, "min": 1.0, "p5": 1.0, "median": 6.0, "p95": 10.0, "max": 10.0})


if __name__ == "__main__":
    unittest.main(verbosity=2)
