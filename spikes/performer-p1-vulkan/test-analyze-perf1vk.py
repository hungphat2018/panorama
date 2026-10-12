"""Tests for analyze-perf1vk.py on synthetic perf1vk logs with known answers.

Run: python -I test-analyze-perf1vk.py

The synthetic monitor is a 240 Hz panel with a 0.463 ms blank. A well-behaved loop acquires an image right after a
refresh, picks and renders for 0.3 ms, presents, and the present lands on the next refresh. Tests add a stall inside
the swapchain acquire (with a log line about a swapchain re-creation next to it), a stall in the pick phase, a missed
refresh, and present-timing feedback from the own-swapchain backend.
"""
import importlib.util
import sys
import json
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("apv", HERE / "analyze-perf1vk.py")
apv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(apv)

T = 1 / 240.0
BLANK = 0.463e-3
BASE = 5000.0
T0 = BASE + 2.0
QPC_FREQ = 10_000_000.0


def make_log(n=480, stall=None, missed_at=None, own=False, fpo_offset=0.0, name="DISPLAY1", index=0, qpc_unit=None):
    """Loop k is aimed at refresh F + k; stall = (k, phase, ms) adds a delay in one phase of loop k.
    missed_at = k: present k lands one refresh after its aim (its render was late); the loops after it run one refresh
    later, as the acquire only returns once that frame is up.
    own: the own-swapchain backend's feedback block; its time domain is a swapchain-local clock (nanoseconds, 55 s ahead
    of QPC), or the QPC domain counted in qpc_unit "ticks" or "ns"."""
    vblanks = [BASE + (k * T) - BLANK + 0.0002 for k in range(0, 3000)]
    cycles, presents, render_cpu, render_gpu, lead = [], [], [], [], []
    k_ref = 400  # first aimed refresh
    delay_total = 0.0
    for k in range(n):
        ref = k_ref + k + (1 if missed_at is not None and k > missed_at else 0)  # the refresh loop k is aimed at
        acquired = BASE + ref * T - T + 0.0001 + delay_total      # right after the previous refresh's flip
        loop = acquired - 0.0002
        pick_ms, render_ms, acq_extra = 0.01, 0.3, 0.0
        if stall and stall[0] == k:
            _, phase, ms = stall
            if phase == "acquire":
                acq_extra = ms
            elif phase == "pick":
                pick_ms += ms
        acquired += acq_extra * 1e-3
        picked = acquired + pick_ms * 1e-3
        rendered = picked + render_ms * 1e-3
        call = rendered + 0.00001
        ret = call + 0.0001
        done = ret
        cycles.append([loop, acquired, picked, rendered, call, ret, done])
        target = BASE + ref * T - BLANK + 0.0002
        presents.append([k + 1, call, ret, target, k // 10])
        render_cpu.append(render_ms)
        render_gpu.append(0.1)
        lead.append((target - call) * 1e3)
        if stall and stall[0] == k and stall[1] in ("acquire", "pick"):
            delay_total += stall[2] * 1e-3
    log = {
        "monitor": index, "name": name, "t0": T0 - 1.0, "fps": 24.0, "master": 0, "error": "", "composition_modes": {},
        "vblanks": vblanks, "presents": presents, "stats": [], "render_cpu_ms": render_cpu, "render_gpu_ms": render_gpu,
        "lead_ms": lead,
        "perf1": {"queue_more": 0, "empty_mix": 0, "render_failed": 0, "windowed": True, "locked": True},
        "vk": {"backend": "own" if own else "libplacebo", "headless": False, "present_mode": "fifo", "depth": 1, "images": 2,
               "alpha": "opaque", "format": "rgb10a2", "device": "Test GPU", "layers_off": "none", "start_fail": 0,
               "distinct_images": 2, "hitch_ms": 50.0,
               "cycle_fields": ["loop", "acquired", "picked", "rendered", "call", "ret", "done"], "cycles": cycles},
        "timing": {"refresh_hz": 240.0, "line_s": 2.3148e-6, "scan_s": 3.7037e-3, "blank_s": BLANK},
        "scan_grid": {"ok": True, "base": BASE, "period": T, "resid_p50_us": 1.0, "resid_p95_us": 3.0, "samples": 900},
        "geometry": {"height_mm": 215.0, "top_mm": 0.0, "lag_ms": 0.0},
    }
    if own:
        domain, domain_id = (3, 1) if qpc_unit else (1000208001, 3)   # VK_TIME_DOMAIN_QUERY_PERFORMANCE_COUNTER_KHR = 3
        feedback, cals, own_presents = [], [], []
        if not qpc_unit:
            for j in range(6):
                q = BASE + 1 + 0.5 * j
                cals.append([q, int((q + 55.0) * 1e9), 30, 0])
        for k in range(n):
            aim = k_ref + k + (1 if missed_at is not None and k > missed_at else 0)
            ref = aim + (1 if k == missed_at else 0)  # the refresh it really landed on
            top = BASE + ref * T
            fpo = top + fpo_offset
            if qpc_unit:
                unit = QPC_FREQ if qpc_unit == "ticks" else 1e9
                q_raw, f_raw = int((fpo - 0.004) * unit), int(fpo * unit)
            else:
                q_raw, f_raw = int((fpo - 0.004 + 55.0) * 1e9), int((fpo + 55.0) * 1e9)
            feedback.append([k + 1, top + 0.001, q_raw, f_raw, domain, domain_id, 1])
            call = presents[k][1]
            own_presents.append([k + 1, 0.0, 0.0, 0, call - 2e-4, call - 1.5e-4, 0, call, call + 1e-4, 0])
        log["own"] = {"qpc_freq": QPC_FREQ, "images": 3, "wait2": True, "wait_lag": 1, "timing": True,
                      "composite_alpha": "opaque", "domain": domain, "domain_id": domain_id, "refresh_duration_ns": 4166666,
                      "refresh_interval_ns": 4166666, "queue_full": 0, "wait_timeouts": 0, "calibrations": cals,
                      "present_fields": ["id", "wait_call", "wait_ret", "wait_result", "acq_call", "acq_ret", "acq_result",
                                         "call", "ret", "result"],
                      "presents": own_presents,
                      "feedback_fields": ["id", "polled", "queue_end_raw", "first_pixel_out_raw", "domain", "domain_id", "complete"],
                      "feedback": feedback}
    return log


def write_dir(logs, events=()):
    d = Path(tempfile.mkdtemp(prefix="apv-test-"))
    for i, log in enumerate(logs):
        (d / f"monitor-{i}-nearest.json").write_text(json.dumps(log))
    (d / "events.json").write_text(json.dumps({"t0": T0, "events": list(events)}))
    return d


class AnalyzePerf1vk(unittest.TestCase):
    def test_clean_run_has_no_hitches_and_no_missed_refreshes(self):
        r = apv.analyze(write_dir([make_log()]), skip_s=0.5)
        m = r["monitors"][0]
        self.assertEqual(r["hitches"]["DISPLAY1"], [])
        self.assertEqual(m["loop"]["missed_refreshes_inferred"], 0)
        self.assertEqual(m["loop"]["acquire_gap_refreshes"], {"1": m["loop"]["acquire_gap_refreshes"]["1"]})
        self.assertAlmostEqual(m["loop"]["present_interval_ms"]["p50"], 4.167, places=2)

    def test_stall_in_the_acquire_is_found_with_the_nearby_event(self):
        log = make_log(stall=(300, "acquire", 320.0))
        t_stall = log["vk"]["cycles"][300][1]
        events = [[t_stall - 1.0, 4, "unrelated, far away"], [t_stall - 0.001, 4, "(Re)creating swapchain of size 1280x800"]]
        r = apv.analyze(write_dir([log], events), skip_s=0.5)
        hs = r["hitches"]["DISPLAY1"]
        self.assertEqual(len(hs), 1)
        self.assertEqual(hs[0]["dominant_phase"], "acquire")
        self.assertAlmostEqual(hs[0]["dominant_ms"], 320.0, delta=1.0)
        texts = [e[2] for e in hs[0]["events"]]
        self.assertIn("(Re)creating swapchain of size 1280x800", texts)
        self.assertNotIn("unrelated, far away", texts)
        self.assertGreater(r["monitors"][0]["loop"]["missed_refreshes_inferred"], 50)

    def test_stall_in_the_pick_phase_is_blamed_on_the_decoder_side(self):
        r = apv.analyze(write_dir([make_log(stall=(200, "pick", 120.0))]), skip_s=0.5)
        h = r["hitches"]["DISPLAY1"][0]
        self.assertEqual(h["dominant_phase"], "pick")
        self.assertIn("decoder", h["cause"])

    def hitch_cause(self, phase, event_text):
        log = make_log(stall=(300, phase, 200.0))
        t = log["vk"]["cycles"][300][1] if phase == "acquire" else log["vk"]["cycles"][300][2]
        events = [[t - 0.001, 4, event_text]] if event_text else []
        return apv.analyze(write_dir([log], events), skip_s=0.5)["hitches"]["DISPLAY1"][0]["cause"]

    def test_causes(self):
        self.assertIn("shader compilation", self.hitch_cause("acquire", "Spent 180.500 ms compiling shader (slow!)"))
        self.assertIn("window", self.hitch_cause("acquire", "window DISPLAY1: WM_SIZE wp=0x0 lp=0x3200500"))
        self.assertIn("swapchain re-creation", self.hitch_cause("acquire", "swapchain image #3 first seen (1280x800)"))
        self.assertIn("swapchain / presentation engine", self.hitch_cause("acquire", None))
        self.assertIn("decoder", self.hitch_cause("pick", "ffmpeg: [hevc] error while decoding"))
        # a compile line that is far shorter than the hitch does not explain it
        self.assertIn("swapchain / presentation engine", self.hitch_cause("acquire", "Spent 12.000 ms creating pipeline"))

    def test_hitch_threshold_can_be_raised(self):
        d = write_dir([make_log(stall=(300, "acquire", 80.0))])
        self.assertEqual(len(apv.analyze(d, skip_s=0.5)["hitches"]["DISPLAY1"]), 1)
        self.assertEqual(apv.analyze(d, skip_s=0.5, hitch_ms=200.0)["hitches"]["DISPLAY1"], [])

    def test_one_missed_refresh_is_counted(self):
        r = apv.analyze(write_dir([make_log(missed_at=200)]), skip_s=0.5)
        loop = r["monitors"][0]["loop"]
        self.assertEqual(loop["missed_refreshes_inferred"], 1)
        self.assertEqual(loop["acquire_gap_refreshes"].get("2"), 1)

    def test_own_backend_feedback_gives_landing_and_screen_times(self):
        r = apv.analyze(write_dir([make_log(own=True)]), skip_s=0.5)
        m = r["monitors"][0]
        self.assertEqual(m["fallback_times"], 0)  # every screen time came from the report
        ld = m["landing"]
        self.assertAlmostEqual(ld["fpo_minus_top_ms"]["p50"], 0.0, delta=0.01)
        self.assertEqual(ld["landing_vs_target_refreshes"], {"0": ld["results"]})

    def test_own_backend_first_pixel_out_at_the_blank_start_lands_on_the_same_refresh(self):
        r = apv.analyze(write_dir([make_log(own=True, fpo_offset=-BLANK)]), skip_s=0.5)
        ld = r["monitors"][0]["landing"]
        self.assertEqual(ld["landing_vs_target_refreshes"], {"0": ld["results"]})
        self.assertAlmostEqual(ld["fpo_minus_top_ms"]["p50"], -BLANK * 1e3, delta=0.01)

    def test_own_backend_qpc_domain_unit_is_detected_not_assumed(self):
        for unit in ("ticks", "ns"):
            r = apv.analyze(write_dir([make_log(own=True, qpc_unit=unit)]), skip_s=0.5)
            ld = r["monitors"][0]["landing"]
            self.assertEqual(ld["qpc_unit"], unit)
            self.assertAlmostEqual(ld["fpo_minus_top_ms"]["p50"], 0.0, delta=0.01)
            self.assertEqual(ld["landing_vs_target_refreshes"], {"0": ld["results"]})
            self.assertIn(f"qpc unit {unit}", "\n".join(apv.lines(r)))

    def test_own_backend_late_present_shows_as_one_refresh_late(self):
        r = apv.analyze(write_dir([make_log(own=True, missed_at=300)]), skip_s=0.5)
        ld = r["monitors"][0]["landing"]
        self.assertEqual(ld["landing_vs_target_refreshes"].get("1"), 1)
        self.assertEqual(ld["landing_vs_target_refreshes"].get("0"), ld["results"] - 1)

    def test_report_lines_print(self):
        r = apv.analyze(write_dir([make_log(stall=(300, "acquire", 200.0))], [[T0, 4, "x"]]), skip_s=0.5)
        text = "\n".join(apv.lines(r))
        self.assertIn("hitches (> threshold): 1", text)
        self.assertIn("acquire", text)


if __name__ == "__main__":
    unittest.main()
