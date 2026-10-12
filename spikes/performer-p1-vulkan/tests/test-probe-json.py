"""Checks the JSON that vk-timing-probe writes: runs bin/test-probe-json.exe (built by `compile.ps1 lgpl jsontest`), parses
its output and feeds it to analyze-vk-timing.py, so a mismatch between the C writer and the analyzer shows up here and
not in the first real run.

Run: python -I tests/test-probe-json.py
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
EXE = ROOT / "bin" / "test-probe-json.exe"
spec = importlib.util.spec_from_file_location("avt", ROOT / "analyze-vk-timing.py")
avt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(avt)


class ProbeJson(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not EXE.exists():
            raise unittest.SkipTest(f"{EXE} not built: run compile.ps1 lgpl jsontest")
        out = Path(tempfile.mkdtemp(prefix="probe-json-")) / "probe.json"
        subprocess.run([str(ROOT / "bin" / "run.cmd"), "test-probe-json.exe", str(out)], check=True, cwd=ROOT / "bin")
        cls.log = json.loads(out.read_text())

    def test_header_blocks(self):
        log = self.log
        self.assertEqual(log["monitor"]["name"], "DISPLAY1")
        self.assertEqual(log["device"], 'Test GPU "quoted"')
        self.assertEqual(log["qpc_freq"], 10000000)
        self.assertEqual(log["surface"]["absolute_supported"], 0)
        self.assertEqual(log["surface"]["relative_supported"], 1)
        self.assertEqual(log["surface"]["stage_queries"], 5)
        self.assertEqual([d["name"] for d in log["time_domains"]], ["SWAPCHAIN_LOCAL", "PRESENT_STAGE_LOCAL"])
        self.assertAlmostEqual(log["scan_grid"]["period"], 1 / 240.0, places=9)
        self.assertTrue(log["scan_grid"]["ok"])
        self.assertEqual(len(log["vblanks"]), 5)

    def test_experiment_rows_match_their_field_lists(self):
        exp = self.log["experiments"][0]
        self.assertEqual(exp["name"], "rel-2")
        self.assertEqual(exp["status"], 'a "status" with a backslash \\ in it')
        self.assertEqual(len(exp["presents"]), 4)
        self.assertTrue(all(len(r) == len(exp["present_fields"]) for r in exp["presents"]))
        self.assertEqual(len(exp["feedback"]), 4)
        pid, polled, target, domain, domain_id, complete, stages = exp["feedback"][0]
        self.assertEqual((pid, domain, domain_id, complete), (1, 1000208001, 7, 1))
        self.assertEqual(stages, [[1, 55000000000], [4, 55004000000]])
        self.assertEqual(len(exp["calibrations"]), 2)

    def test_the_analyzer_reads_it(self):
        res = avt.analyze(self.log, skip_s=0.0)
        e = res["experiments"][0]
        self.assertEqual(e["name"], "rel-2")
        self.assertEqual(e["feedback_results"], 4)
        self.assertEqual(e["presents"], 4)
        # the numbers are made up; the point is that every key the analyzer needs exists and the numbers come out
        self.assertIn("fpo_minus_top_ms", e)
        text = "\n".join(avt.lines(res))
        self.assertIn("DISPLAY1", text)


if __name__ == "__main__":
    unittest.main()
