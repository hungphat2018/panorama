"""Checks the JSON block the own-swapchain backend writes: runs bin/test-own-swap-json.exe (built by
`compile.ps1 lgpl jsontest`), parses its output, and feeds it to analyze-perf1vk.py's feedback conversion.

Run: python -I tests/test-own-swap-json.py
"""
import importlib.util
import sys
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
EXE = ROOT / "bin" / "test-own-swap-json.exe"
spec = importlib.util.spec_from_file_location("apv", ROOT / "analyze-perf1vk.py")
apv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(apv)


class OwnSwapJson(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not EXE.exists():
            raise unittest.SkipTest(f"{EXE} not built: run compile.ps1 lgpl jsontest")
        out = Path(tempfile.mkdtemp(prefix="own-json-")) / "own.json"
        subprocess.run([str(ROOT / "bin" / "run.cmd"), "test-own-swap-json.exe", str(out)], check=True, cwd=ROOT / "bin")
        lines = out.read_text().strip().splitlines()
        cls.with_backend = json.loads(lines[0])
        cls.without = json.loads(lines[1])

    def test_block_parses_and_keeps_every_field(self):
        own = self.with_backend["own"]
        self.assertEqual(own["images"], 3)
        self.assertTrue(own["id2"] and own["wait2"] and own["waits"] and own["timing"])
        self.assertEqual(own["sharing"], "concurrent")
        self.assertEqual(own["stage_mask"], 5)
        self.assertEqual((own["surface_absolute"], own["surface_relative"]), (False, True))
        self.assertEqual(own["queue_idle_waits"], 4)
        self.assertEqual(own["wait_lag"], 1)
        self.assertEqual(own["composite_alpha"], "opaque")
        self.assertEqual(own["domain"], 1000208001)
        self.assertEqual(own["domain_id"], 7)
        self.assertEqual(own["refresh_duration_ns"], 4166666)
        self.assertEqual(own["queue_full"], 2)
        self.assertEqual(own["wait_timeouts"], 1)
        self.assertGreater(own["qpc_freq"], 1e6)
        self.assertEqual(len(own["calibrations"]), 2)
        self.assertEqual(own["calibrations"][1], [100.5, 56000000000, 24, 0])

    def test_rows_match_their_field_lists(self):
        own = self.with_backend["own"]
        self.assertEqual(len(own["presents"]), 3)
        self.assertTrue(all(len(r) == len(own["present_fields"]) for r in own["presents"]))
        self.assertEqual(len(own["feedback"]), 3)
        self.assertTrue(all(len(r) == len(own["feedback_fields"]) for r in own["feedback"]))
        row = dict(zip(own["feedback_fields"], own["feedback"][0]))
        self.assertEqual(row["id"], 1)
        self.assertEqual(row["first_pixel_out_raw"], 55000100000)
        self.assertEqual(row["domain"], 1000208001)
        self.assertEqual(row["domain_id"], 7)
        self.assertEqual(row["complete"], 1)
        prow = dict(zip(own["present_fields"], own["presents"][0]))
        self.assertEqual((prow["id"], prow["result"]), (1, 0))
        self.assertAlmostEqual(prow["call"], 100.004, places=6)

    def test_no_backend_writes_null(self):
        self.assertIsNone(self.without["own"])

    def test_analyzer_converts_the_written_feedback(self):
        # the calibrations map local ticks to QPC: 1 s of local time per second of QPC, starting at (99.5 s, 55e9)
        own = self.with_backend["own"]
        log = {
            "own": own, "timing": {"blank_s": 0.463e-3}, "scan_grid": {"ok": True, "base": 0.0, "period": 1 / 240.0},
            "presents": [[1, 100.004, 100.0041, 99.0, 0], [2, 101.004, 101.0041, 100.0, 0], [3, 102.004, 102.0041, 101.0, 0]],
        }
        result = apv.apply_own_feedback(log)
        self.assertEqual(result["results"], 3)
        self.assertEqual(len(log["stats"]), 3)
        # first pixel out = local 55.0001e9 = QPC 99.5001 s, 0.1 ms after the top row at 99.5 s (= 23880 / 240): the screen
        # time handed to the pacing analysis is the middle of the blank before that top row
        self.assertAlmostEqual(log["stats"][0][3], 99.5 - 0.5 * 0.463e-3, delta=1e-6)


if __name__ == "__main__":
    unittest.main()
