"""Black level of the file: what the bars are made of, so cropdetect's threshold sits just above it.

Research finding (film-analysis research, section 2.2): the black threshold must be calibrated per file, bar level plus 3
codes (limit 0.075 for a bar level of 16); the default 0.094 cuts picture in 9.8% of frames of real anime with exact bars and
0.15 breaks. The research did not say how to find the bar level; this module does it from the keyframes of the pre-scan:

  1. For each keyframe (960x540 luma, 8-bit code values) take the outermost two rows at the top and bottom and the two
     outermost columns at the left and right.
  2. An edge is "bar-like" when its median is within 12 codes of the nominal black (16 for video range, 0 for full range) and
     its 2nd-to-98th percentile spread is at most 6 codes (flat and dark).
  3. The bar level is the median of the medians of all bar-like edges (needs at least 3 samples; otherwise the nominal black).
  4. cropdetect limit = (bar level + 3) / 255, kept between (nominal + 3) / 255 and the FFmpeg default 24/255.
  5. The "bright" threshold for text in the bars is bar level + 40 (the research found the same recall at +24, +40 and +60).
"""
from __future__ import annotations

import numpy as np

DEFAULT_LIMIT = 24 / 255
MARGIN_CODES = 3
BRIGHT_CODES = 40


def nominal_black(color_range: str | None) -> int:
    return 0 if color_range == "pc" else 16


def edge_samples(y: np.ndarray) -> list:
    """(median, spread) of the four outer edges of one luma frame (rows x cols, uint8)."""
    edges = (y[:2, :], y[-2:, :], y[:, :2], y[:, -2:])
    out = []
    for e in edges:
        v = e.ravel()
        p2, p50, p98 = np.percentile(v, [2, 50, 98])
        out.append((float(p50), float(p98 - p2)))
    return out


def estimate_from_samples(edge_samples_per_frame: list, color_range: str | None) -> dict:
    """edge_samples_per_frame: for each keyframe the output of edge_samples()."""
    nominal = nominal_black(color_range)
    levels = []
    n_edges = 0
    for samples in edge_samples_per_frame:
        for med, spread in samples:
            n_edges += 1
            if med <= nominal + 12 and spread <= 6:
                levels.append(med)
    if len(levels) >= 3:
        level = float(np.median(levels))
        source = "bar-like edges of keyframes"
    else:
        level = float(nominal)
        source = "nominal (no flat dark edges found)"
    lo = (nominal + MARGIN_CODES) / 255
    limit = min(max((level + MARGIN_CODES) / 255, lo), DEFAULT_LIMIT)
    return {"nominal": nominal, "bar_level": round(level, 2), "bar_like_edge_samples": len(levels),
            "edge_samples": n_edges, "keyframes_used": len(edge_samples_per_frame), "cropdetect_limit": round(limit, 5),
            "bright_threshold": int(round(level + BRIGHT_CODES)), "source": source, "unit": "8-bit code values of the decoded stream"}


def estimate(frames_y: list, color_range: str | None) -> dict:
    return estimate_from_samples([edge_samples(y) for y in frames_y], color_range)
