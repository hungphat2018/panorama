"""Analyzer versions: one per table / signal group. A stored table is reused only when its version equals the one here, so a
change to a detector or a policy bumps exactly the tables it touches (ADR 0024: each table carries its own analyzer version).

L0 groups (raw per-frame signals; a change needs a rescan of the affected group):
  frame-table  pts and keyframe flags from the packet list
  scan         the `scanned` coverage flag
  shots        TransNetV2 scores (model sha-256 and the window alignment are part of the version string)
  subjects-raw boxes of the face / head / person detectors on every Nth frame (their model hashes and N are part of the version)
  motion       slide pair measurement, luma statistics, scdet-like score (480x270 grid, region of interest)
  crop         cropdetect rectangles (round=2, reset=1, black threshold from the file's black level)
  bars-text    bright-edge distances of the four bars
  dolby-vision scene_refresh_flag per frame
L1 tables (derived from L0 in seconds; a policy change re-derives them without decoding):
  shots, slide-paths, subjects, subtitles, file-facts

Cache key of a content model = the file's identity (file_key.py) for the folder, plus these versions for what inside the folder
may be reused: signal groups all equal -> the signals are reused; each L1 table is reused or made again by its own version
(manifest.stale_tables, refresh.py).
"""
import hashlib

FRAME_TABLE = "1"
SCAN = "1"
SHOTS_SIGNAL = "1"        # + model hash, see shots_signal_version()
MOTION = "1"
CROP = "1"
BARS_TEXT = "1"
DOLBY_VISION = "1"

SHOTS_TABLE = "1"
SLIDE_PATHS = "1"
SUBJECTS = "1"
SUBTITLES = "1"
FILE_FACTS = "1"
SUBJECTS_RAW = "1"        # + detector model hashes and the sampling stride, see subjects_raw_version()


def shots_signal_version(transnet_sha256: str) -> str:
    return f"{SHOTS_SIGNAL}+tn:{transnet_sha256[:12]}"


def subjects_raw_version(detector_sha256: dict, sample_stride: int) -> str:
    """Raw detections depend on which models ran and on which frames: both are part of the version."""
    digest = hashlib.sha256("|".join(f"{k}={v}" for k, v in sorted(detector_sha256.items())).encode()).hexdigest()[:12]
    return f"{SUBJECTS_RAW}+stride:{sample_stride}+models:{digest}"


def signal_group_versions(model_sha256: dict, with_dovi: bool, sample_stride: int) -> dict:
    """model_sha256: model id -> sha-256 from models-manifest.json (transnetv2 plus the subject detectors)."""
    detectors = {k: v for k, v in model_sha256.items() if k != "transnetv2"}
    v = {"frame-table": FRAME_TABLE, "scan": SCAN, "shots": shots_signal_version(model_sha256.get("transnetv2", "none")),
         "motion": MOTION, "crop": CROP, "bars-text": BARS_TEXT, "subjects-raw": subjects_raw_version(detectors, sample_stride)}
    if with_dovi:
        v["dolby-vision"] = DOLBY_VISION
    return v


def table_versions() -> dict:
    return {"shots": SHOTS_TABLE, "slide-paths": SLIDE_PATHS, "subjects": SUBJECTS, "subtitles": SUBTITLES, "file-facts": FILE_FACTS}
