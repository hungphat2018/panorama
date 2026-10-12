"""Plain data types shared by methods, adapters and metrics. Nothing here touches files or video.

Coordinates are source storage pixels with a half-open rectangle (x0, y0, x1, y1): pixels x0 <= x < x1, y0 <= y < y1.
Frame numbers are presentation-order indices of the clip's frame table; a span is [start, end) in frames.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np


@dataclass(frozen=True)
class Boundary:
    """A place where the picture may change layout unseen. For a transition, `frame` is the first frame of its span."""
    frame: int
    kind: str = "hard"                       # hard | dissolve | fade_black | fade_white | wipe | slide | flash | unknown
    span: Optional[tuple[int, int]] = None   # [start, end) of a transition; None for a hard cut
    score: float = 1.0
    source: str = ""

    @property
    def start(self) -> int:
        return self.span[0] if self.span else self.frame

    @property
    def end(self) -> int:
        """Last frame touched by the boundary, exclusive."""
        return self.span[1] if self.span else self.frame


@dataclass
class ShotPrediction:
    boundaries: list[Boundary]
    n_frames: int
    name: str = ""
    seconds: float = 0.0                     # compute time, when known


@dataclass
class CropPrediction:
    """Per-frame final crop rectangle [N,4] int32 (x0, y0, x1, y1), after any text guard, plus how it was made."""
    rect: np.ndarray
    shot_starts: Optional[np.ndarray] = None          # first frame of every shot the crop was decided on
    vocabulary: list = field(default_factory=list)    # rectangles the crops were snapped to
    name: str = ""
    black_level: Optional[float] = None


@dataclass
class TextGuardPrediction:
    """Per-frame flags: the bar strips hold something bright (text, logo). Rows/columns to keep are folded into the crop."""
    flagged: np.ndarray                      # bool [N]
    name: str = ""
    file_flags: dict = field(default_factory=dict)    # {"top": bool, "bottom": bool, "left": bool, "right": bool}


@dataclass(frozen=True)
class Box:
    cls: str                                 # face | head | person | text
    x: float
    y: float
    w: float
    h: float
    score: float = 1.0
    detector: str = ""

    def rect(self) -> tuple[float, float, float, float]:
        return (self.x, self.y, self.x + self.w, self.y + self.h)


@dataclass
class SubjectPrediction:
    frames: dict[int, list[Box]]             # only the frames that were evaluated
    name: str = ""


@dataclass
class SubtitleEvent:
    start: float                             # seconds on the clip's timeline
    end: float
    cls: str                                 # dialogue | sign | unknown
    conf: float = 1.0
    rect: Optional[tuple[int, int, int, int]] = None   # rendered box (x0, y0, x1, y1) on the PlayRes-scaled canvas, when known
    text: str = ""


@dataclass
class SubtitlePrediction:
    events: list[SubtitleEvent]
    track: str = ""
    name: str = ""


@dataclass
class SlidePrediction:
    """Per frame: (dx, dy) of frame i against frame i-1 in source px (positive = content moves right/down), a confidence,
    and the runs the method calls slides, each pure (smooth it) or not."""
    dxdy: np.ndarray                         # float [N,2], row 0 = 0
    conf: np.ndarray                         # float [N] in 0..1
    runs: list[dict]                         # {"frames": (a, b), "pure": bool, "purity": {...}}
    name: str = ""
