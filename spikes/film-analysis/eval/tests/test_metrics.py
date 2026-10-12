"""Metric tests on made-up data. Run:  python -I -m unittest discover -s spikes/film-analysis/eval/tests -t spikes/film-analysis/eval -v"""
import unittest

import numpy as np

from fa_eval.metrics import bars as mb
from fa_eval.metrics import faces as mf
from fa_eval.metrics import prescan as mp
from fa_eval.metrics import shots as ms
from fa_eval.metrics import slides as msl
from fa_eval.metrics import subtitles as msub
from fa_eval.metrics import text_in_bars as mt
from fa_eval.methods import shots_policy as sp
from fa_eval.types import Boundary

FULL = (0, 0, 1920, 1080)
SCOPE = (0, 138, 1920, 942)


class ShotMetricsTest(unittest.TestCase):
    def truth(self):
        return ms.ShotTruth(hard=[100, 300], transitions=[(200, 212, "dissolve")], not_cuts=[(400, 402, "flash-white2")],
                            transition_info=[{"transition": "fade", "duration": 12}])

    def test_perfect_prediction(self):
        pred = [Boundary(100), Boundary(200, "dissolve", (200, 212)), Boundary(300)]
        r = ms.shot_metrics(self.truth(), pred, 1000, 24.0)
        self.assertEqual(r["tol1"]["hard"]["tp"], 2)
        self.assertEqual(r["tol1"]["hard"]["fp"], 0)
        self.assertEqual(r["tol1"]["transitions_found"], 1)
        self.assertEqual(r["tol1"]["false_splits"], 0)
        self.assertAlmostEqual(r["span_iou"]["mean"], 1.0)
        self.assertEqual(r["flash"]["with_false_boundary"], 0)

    def test_tolerance_and_false_split_and_flash(self):
        pred = [Boundary(101), Boundary(303), Boundary(250), Boundary(401)]      # 101 ok at tol1; 303 misses at tol1 but hits at tol3; 250 false; 401 is on the flash
        r = ms.shot_metrics(self.truth(), pred, 24 * 3600, 24.0, tols=(0, 1, 3), main_tol=1)
        self.assertEqual(r["tol0"]["hard"]["tp"], 0)
        self.assertEqual(r["tol1"]["hard"]["tp"], 1)
        self.assertEqual(r["tol3"]["hard"]["tp"], 2)
        self.assertEqual(r["tol1"]["flash_false_positives"], 1)
        self.assertEqual(r["tol1"]["false_splits_not_flash"], 2)      # 303 (too far) and 250
        self.assertEqual(r["flash"]["with_false_boundary"], 1)
        self.assertAlmostEqual(r["false_splits_per_hour"], 2.0)        # one hour of 24 fps frames

    def test_prediction_inside_dissolve_matches_the_dissolve(self):
        r = ms.shot_metrics(self.truth(), [Boundary(206)], 1000, 24.0)
        self.assertEqual(r["tol1"]["transitions_found"], 1)
        self.assertEqual(r["tol1"]["false_splits"], 0)

    def test_one_to_one_matching(self):
        # two predictions near one cut: one matches, the other is a false split
        r = ms.shot_metrics(ms.ShotTruth([100], [], [], []), [Boundary(100), Boundary(101)], 1000, 24.0)
        self.assertEqual(r["tol1"]["hard"]["tp"], 1)
        self.assertEqual(r["tol1"]["false_splits"], 1)

    def test_truth_from_labels(self):
        lab = {"boundaries": [{"frame": 5, "kind": "hard", "span": None}, {"frame": 50, "kind": "dissolve", "span": [50, 60], "transition": "fade"}],
               "not_cuts": [{"span": [70, 72], "kind": "flash-white2"}]}
        t = ms.truth_from_labels(lab)
        self.assertEqual(t.hard, [5])
        self.assertEqual(t.transitions, [(50, 60, "dissolve")])
        self.assertEqual(t.not_cuts, [(70, 72, "flash-white2")])


class PolicyTest(unittest.TestCase):
    def test_find_runs(self):
        self.assertEqual(sp.find_runs(np.array([0, 1, 1, 0, 0, 1, 0, 1, 1, 1], bool)), [(1, 2), (5, 5), (7, 9)])
        self.assertEqual(sp.find_runs(np.zeros(0, bool)), [])

    def test_tn_single_places_cut_after_last_flag_and_long_runs_are_transitions(self):
        s = np.zeros(50, np.float32)
        s[10] = 0.9                    # cut between frame 10 and 11 -> boundary 11
        s[30:36] = 0.7                 # six flagged frames -> transition
        b = sp.tn_single(s, 0.5)
        self.assertEqual((b[0].frame, b[0].kind), (11, "hard"))
        self.assertEqual((b[1].kind, b[1].span), ("dissolve", (30, 36)))

    def test_consensus_needs_a_second_signal(self):
        n = 100
        single = np.zeros(n, np.float32)
        single[19] = 0.95              # cut at 20 backed by scdet
        single[59] = 0.95              # cut at 60 backed by nothing
        scd = np.zeros(n, np.float32)
        scd[20] = 40
        sig = {"n_frames": np.array([n]), "tn_single": single, "tn_many": np.zeros(n, np.float32), "scdet": scd, "keyframes": np.array([0, 33])}
        self.assertEqual([b.frame for b in sp.consensus_cuts(sig)], [20])


class BarsMetricsTest(unittest.TestCase):
    def test_cut_and_bar_pixels(self):
        n = 10
        T = np.tile(np.array(SCOPE), (n, 1))
        D = T.copy()
        D[3, 3] -= 12                  # 12 px of picture cut at the bottom
        D[4, 1] -= 20                  # 20 px of bar left at the top
        r = mb.bars_metrics(T, D, 24.0, cuts=[3, 4, 5])
        self.assertEqual(r["cut_frames"], 1)
        self.assertEqual(r["cut_px_worst"], 12)
        self.assertAlmostEqual(r["bar_frames_share"], 0.1)
        self.assertEqual(r["crop_changes"], 3)                    # D differs from the frame before at 3, 4 and 5
        self.assertEqual(r["crop_changes_on_cuts"], 3)            # frames 3, 4, 5 are all cuts

    def test_ambiguous_frames_are_reported_apart(self):
        n = 6
        T = np.tile(np.array(SCOPE), (n, 1))
        D = T.copy()
        D[2] = FULL
        amb = np.zeros(n, bool)
        amb[2] = True
        r = mb.bars_metrics(T, D, 24.0, ambiguous=amb)
        self.assertEqual(r["cut_frames"], 0)                       # full frame cuts nothing; it leaves bars
        self.assertAlmostEqual(r["bar_frames_share"], 1 / 6)
        D2 = T.copy()
        D2[2] = (0, 200, 1920, 900)
        r2 = mb.bars_metrics(T, D2, 24.0, ambiguous=amb)
        self.assertEqual(r2["cut_frames"], 1)
        self.assertEqual(r2["cut_frames_ambiguous"], 1)
        self.assertEqual(r2["cut_frames_clear"], 0)

    def test_rects_from_segments_and_first_correct(self):
        T = mb.rects_from_segments([{"frames": [0, 30], "rect": list(SCOPE)}, {"frames": [30, 60], "rect": list(FULL)}], 60)
        D = T.copy()
        D[:10] = FULL                   # starts with bars left, right from frame 10
        r = mb.bars_metrics(T, D, 24.0)
        self.assertEqual(r["first_correct_frame"], 10)


class TextMetricsTest(unittest.TestCase):
    def test_bar_part(self):
        self.assertEqual(mt.bar_part((100, 900, 500, 1000), SCOPE), (100, 942, 500, 1000))
        self.assertIsNone(mt.bar_part((100, 200, 500, 300), SCOPE))
        self.assertEqual(mt.bar_part((100, 100, 500, 200), SCOPE), (100, 100, 500, 138))

    def test_cropped_rows_and_guard(self):
        n = 8
        T = np.tile(np.array(SCOPE), (n, 1))
        D = T.copy()
        D[:, 3] = 1000                  # keeps rows up to 1000, text reaches 1043
        events = [{"frames": [2, 6], "box": [100, 1010, 900, 1043]}]
        flagged = np.zeros(n, bool)
        flagged[2:5] = True             # guard sees 3 of 4 text frames; one false alarm at frame 7
        flagged[7] = True
        r = mt.text_metrics(events, T, D, flagged)
        self.assertEqual(r["text_frames"], 4)
        self.assertEqual(r["frames_cropped"], 4)
        self.assertEqual(r["cut_px_max"], 43)
        self.assertAlmostEqual(r["guard_recall"], 0.75)
        self.assertEqual(r["guard_false_alarms"], 1)
        D2 = T.copy()
        D2[:, 3] = 1080
        self.assertEqual(mt.text_metrics(events, T, D2, None)["frames_cropped"], 0)

    def test_text_on_picture_is_not_bar_text(self):
        T = np.tile(np.array(SCOPE), (4, 1))
        r = mt.text_metrics([{"frames": [0, 4], "box": [100, 300, 500, 400]}], T, T.copy(), np.zeros(4, bool))
        self.assertEqual(r["text_frames"], 0)

    def test_file_flags(self):
        T = np.tile(np.array(SCOPE), (100, 1))
        f = mt.file_flags([{"frames": [0, 50], "box": [0, 1000, 100, 1040]}], T)
        self.assertEqual(f, {"top": False, "bottom": True, "left": False, "right": False})


class FaceMetricsTest(unittest.TestCase):
    def test_iou(self):
        self.assertAlmostEqual(mf.iou((0, 0, 10, 10), (0, 0, 10, 10)), 1.0)
        self.assertAlmostEqual(mf.iou((0, 0, 10, 10), (5, 0, 10, 10)), 1 / 3)
        self.assertEqual(mf.iou((0, 0, 10, 10), (20, 20, 5, 5)), 0.0)

    def test_subject_metrics(self):
        frames = {0: mf.FrameBoxes(truth=[{"cls": "face", "x": 100, "y": 100, "w": 80, "h": 80}, {"cls": "text", "x": 500, "y": 900, "w": 200, "h": 40}],
                                   pred=[{"cls": "face", "x": 104, "y": 100, "w": 80, "h": 80, "score": 0.9}, {"cls": "person", "x": 900, "y": 50, "w": 100, "h": 300, "score": 0.5}]),
                  1: mf.FrameBoxes(truth=[{"cls": "face", "x": 700, "y": 100, "w": 80, "h": 80}],
                                   pred=[{"cls": "head", "x": 680, "y": 80, "w": 130, "h": 130, "score": 0.8}])}
        r = mf.subject_metrics(frames, 1920, 1080)
        self.assertEqual(r["truth"]["face"], 2)
        self.assertEqual(r["strict"]["face"], 1)                  # frame 1: a head box has IoU < 0.5 with the face and is the wrong class
        self.assertEqual(r["covered"]["face"], 2)                 # but it covers the face
        self.assertEqual(r["strict"]["text"], 0)
        self.assertEqual(r["false_boxes"], 1)                     # the person box matches nothing

    def test_shot_recall(self):
        frames = {0: mf.FrameBoxes([{"cls": "face", "x": 10, "y": 10, "w": 50, "h": 50}], []),
                  1: mf.FrameBoxes([{"cls": "face", "x": 10, "y": 10, "w": 50, "h": 50}], [{"cls": "face", "x": 10, "y": 10, "w": 50, "h": 50, "score": 1}]),
                  2: mf.FrameBoxes([], [])}
        r = mf.shot_recall(frames, lambda f: 0 if f < 1 else 1 if f < 2 else 2, 1920, 1080)
        self.assertEqual((r["shots_with_truth"], r["shots_found"]), (2, 1))

    def test_planner_moves_strip_off_a_centred_face(self):
        lay = mf.Layout()
        face = {"cls": "face", "x": 900, "y": 300, "w": 120, "h": 160}           # centred on 960 of 1920
        shots = [{"truth": [face], "pred": [face]}]
        naive = mf.bezel_hit_rate(shots, lay, 0, 1920, mode="naive")
        planned = mf.bezel_hit_rate(shots, lay, 0, 1920, mode="detected")
        blind = mf.bezel_hit_rate([{"truth": [face], "pred": []}], lay, 0, 1920, mode="detected")
        self.assertEqual(naive["hits"], 1)
        self.assertEqual(planned["hits"], 0)
        self.assertEqual(blind["hits"], 1)                        # a planner that sees no face only keeps the centre clear: the strip still touches a wide centred face
        self.assertGreater(planned["mean_shift"], 0.0)

    def test_centre_rule_keeps_picture_centre_off_the_strip(self):
        lay = mf.Layout()
        c = mf.plan_bezel([], lay)
        s0, s1 = mf.strip(c, lay)
        self.assertFalse(s0 <= 0.5 <= s1)
        self.assertGreaterEqual(abs(c - 0.5), lay.bezel_w / 2 + lay.centre_margin - 1e-9)

    def test_wide_face_cannot_be_avoided_reports_hit(self):
        lay = mf.Layout(max_shift=0.02)
        wide = {"cls": "face", "x": 0, "y": 0, "w": 1920, "h": 100}
        r = mf.bezel_hit_rate([{"truth": [wide], "pred": [wide]}], lay, 0, 1920, mode="oracle")
        self.assertEqual(r["hits"], 1)


class SubtitleMetricsTest(unittest.TestCase):
    def test_class_metrics(self):
        t = ["dialogue"] * 6 + ["sign"] * 2
        p = ["dialogue", "dialogue", "dialogue", "sign", "unknown", "dialogue", "sign", "dialogue"]
        r = msub.class_metrics(t, p)
        self.assertAlmostEqual(r["coverage"], 7 / 8)
        self.assertAlmostEqual(r["accuracy_decided"], 5 / 7)
        self.assertEqual(r["signs_called_dialogue"], 1)
        self.assertEqual(r["dialogue_called_sign"], 1)
        self.assertGreater(r["macro_f1"], 0)

    def test_width_and_seam(self):
        truth = [(500, 900, 1400, 1000), (100, 900, 700, 1000)]
        pred = [(520, 900, 1380, 1000), (100, 900, 900, 1000)]
        w = msub.width_errors(truth, pred, 1920)
        self.assertEqual(w["n"], 2)
        self.assertAlmostEqual(w["median"], (40 / 1920 + 200 / 1920) / 2)
        s = msub.seam_crossing(truth, pred, 1920, seams=(0.5,))
        self.assertEqual(s["0.5"]["truth_crossing_share"], 0.5)       # first box crosses x=960, second does not
        self.assertEqual(s["0.5"]["accuracy"], 1.0)


class SlideMetricsTest(unittest.TestCase):
    def test_purity(self):
        truth = [{"frames": [0, 50], "class": "pure"}, {"frames": [100, 150], "class": "pure"}, {"frames": [200, 260], "class": "layered"}, {"frames": [300, 350], "class": "parallax"}]
        pred = [{"frames": (0, 48), "pure": True}, {"frames": (200, 250), "pure": True}, {"frames": (300, 349), "pure": False}, {"frames": (500, 520), "pure": True}]
        r = msl.purity_metrics(truth, pred, 600)
        self.assertEqual(r["pure_found"], 1)
        self.assertAlmostEqual(r["recall"], 0.5)
        self.assertEqual((r["pred_pure_correct"], r["pred_pure_wrong"], r["pred_pure_unlabelled"]), (1, 1, 1))
        self.assertAlmostEqual(r["precision"], 0.5)
        self.assertEqual(r["false_smooth"], 1)
        self.assertAlmostEqual(r["false_smooth_rate"], 0.5)
        self.assertEqual(r["wrong_by_class"], {"layered": 1})

    def test_path_error(self):
        n = 40
        true = np.zeros((n, 2))
        true[:, 0] = np.linspace(0, 39, n)               # 1 px per frame to the right
        step = np.zeros((n, 2))
        step[1:, 0] = 1.0
        r = msl.path_error(true, step, (0, n))
        self.assertAlmostEqual(r["pos_max"], 0.0, places=6)
        step[1:, 0] = 1.05                                # 5% too fast
        r = msl.path_error(true, step, (0, n))
        self.assertAlmostEqual(r["pos_max"], 0.05 * 39, places=6)
        self.assertAlmostEqual(r["step_median"], 0.05, places=6)

    def test_path_error_ignores_start_offset(self):
        true = np.zeros((10, 2))
        true[:, 1] = 100.0 + np.arange(10) * 2.0
        step = np.zeros((10, 2))
        step[1:, 1] = 2.0
        self.assertAlmostEqual(msl.path_error(true, step, (0, 10))["pos_max"], 0.0, places=6)


class PrescanTest(unittest.TestCase):
    def test_shot_share(self):
        fps = 24.0
        n = 600
        T = np.tile(np.array(SCOPE), (n, 1))
        D = T.copy()
        D[:240] = FULL                                    # right only after 10 s
        r = mp.prescan_metrics(T, D, [0, 120, 240, 400], fps, window_s=25.0)
        self.assertEqual(r["shots"], 4)
        self.assertEqual(r["shots_right"], 2)             # shots starting at 240 and 400
        self.assertEqual(r["shots_safe"], 4)              # never cut anything
        self.assertEqual(r["first_right_frame_that_stays"], 240)
        D[:240] = (0, 300, 1920, 800)
        self.assertEqual(mp.prescan_metrics(T, D, [0, 120, 240, 400], fps, window_s=25.0)["shots_safe"], 2)


if __name__ == "__main__":
    unittest.main()
