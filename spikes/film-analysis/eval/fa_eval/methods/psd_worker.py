"""PySceneDetect 0.7.1 (Content and Adaptive detectors, default settings) on one video. Runs in tools/testset/venv-extras.

Usage: python -I psd_worker.py VIDEO OUT_NPZ
Writes content/adaptive = scene-start frame numbers after the first scene (frame number = seconds * fps as PySceneDetect counts),
plus the seconds each detector took. Stdout carries one summary line per detector.
"""
import sys
import time

import numpy as np
from scenedetect import AdaptiveDetector, ContentDetector, SceneManager, open_video


def run(path: str, det):
    video = open_video(path)
    sm = SceneManager()
    sm.add_detector(det)
    t0 = time.perf_counter()
    sm.detect_scenes(video, show_progress=False)
    dt = time.perf_counter() - t0
    scenes = sm.get_scene_list()
    starts = np.array([s[0].frame_num for s in scenes][1:], dtype=np.int64)
    n = scenes[-1][1].frame_num if scenes else 0
    return starts, dt, n


def main() -> int:
    path, out = sys.argv[1], sys.argv[2]
    res = {}
    for name, det in (("content", ContentDetector()), ("adaptive", AdaptiveDetector())):
        starts, dt, n = run(path, det)
        res[name] = starts
        res[name + "_seconds"] = np.array([dt])
        print(f"{name}: {len(starts) + 1} scenes, {n} frames in {dt:.1f}s")
    np.savez_compressed(out, **res)
    return 0


if __name__ == "__main__":
    sys.exit(main())
