"""Writes the room files (panorama.room/1, the fields this spike reads) into room/.

Setup A as measured (room research 3.1, seam-settings-last.json): laptop panel 2560x1600, 344x215 mm, 240 Hz, scan 3.70 ms;
external 1920x1080, 480x270 mm (square-pixel rule), 100 Hz, scan 9.69 ms; gap 20 mm, tops level, seat 700 mm.
The eye sits on the line through the centre of the seam, level with the laptop's centre. Variants move only the external
(toe-in about the seam hinge) or the laptop (nearer to the eye); derived.seams[].gap_mm is the lateral gap the fold model
prints: the distance between the facing edges with any depth step left out.

  python -I make-room-variants.py            (writes room/*.json)
"""
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LAPTOP = dict(id="laptop", px=(2560, 1600), mm=(344.0, 215.0), hz=240.001, scan_ms=3.704)
EXTERNAL = dict(id="external", px=(1920, 1080), mm=(480.0, 270.0), hz=100.0, scan_ms=9.69)
GAP = 20.0
SEAT = 700.0


def monitor(spec, centre, yaw=0.0):
    return {
        "id": spec["id"],
        "panel": {"native_px": list(spec["px"]), "active_area_mm": list(spec["mm"])},
        "pose": {"centre_mm": [round(c, 6) for c in centre], "yaw_deg": yaw, "pitch_deg": 0.0, "roll_deg": 0.0},
        "timing": {"refresh_hz": spec["hz"], "scan_ms": spec["scan_ms"], "lag_ms": 0.0},
    }


def setup_a(name, note, toe_deg=0.0, laptop_nearer_mm=0.0):
    lw, lh = LAPTOP["mm"]
    ew, eh = EXTERNAL["mm"]
    laptop_c = (-0.5 * GAP - 0.5 * lw, 0.0, SEAT - laptop_nearer_mm)
    top = 0.5 * lh                                   # tops level: both top edges at +107.5 mm
    toe = math.radians(toe_deg)
    hinge_x = 0.5 * GAP                              # the external's left edge, at the laptop's plane
    ext_c = (hinge_x + 0.5 * ew * math.cos(toe), top - 0.5 * eh, SEAT + 0.5 * ew * math.sin(toe))
    return {
        "schema": "panorama.room/1",
        "name": name,
        "note": note,
        "units": "mm, deg. Frame = seat: right-handed, +x right, +y up, +z away from the eye toward the screens, origin = eye point",
        "seat": {"gaze_yaw_deg": 0, "gaze_pitch_deg": 0, "distance_mm": SEAT,
                 "source": "default: 700 mm from the seam, eye level with the laptop's centre"},
        "monitors": [monitor(LAPTOP, laptop_c), monitor(EXTERNAL, ext_c, yaw=-toe_deg)],
        "derived": {"seams": [{"a": "laptop", "b": "external", "gap_mm": GAP, "stagger_mm": 0.0, "hinge_deg": toe_deg}],
                    "anchor": "seam 0 (the gaze axis passes through its centre)"},
    }


def single(spec, name, w_mm=None):
    mm = spec["mm"] if w_mm is None else w_mm
    return {
        "schema": "panorama.room/1",
        "name": name,
        "units": "mm, deg. Frame = seat",
        "seat": {"gaze_yaw_deg": 0, "gaze_pitch_deg": 0, "distance_mm": SEAT, "source": "default"},
        "monitors": [monitor(dict(spec, mm=mm), (0.0, 0.0, SEAT))],
        "derived": {"seams": [], "anchor": "the monitor's centre"},
    }


def bench(w, h):
    pitch = 0.1344                                   # the laptop panel's pitch; only the picture's mm size matters here
    spec = dict(id="m%dx%d" % (w, h), px=(w, h), mm=(w * pitch, h * pitch), hz=240.0, scan_ms=3.7)
    return single(spec, "bench-%dx%d" % (w, h), spec["mm"])


def main():
    out_dir = os.path.join(HERE, "room")
    os.makedirs(out_dir, exist_ok=True)
    rooms = {
        "setup-a-default": setup_a("setup-a-default", "coplanar, gap 20 mm, tops level, seat 700 mm"),
        "setup-a-toe15": setup_a("setup-a-toe15", "external toed in 15 degrees about the seam", toe_deg=15.0),
        "setup-a-toe30": setup_a("setup-a-toe30", "external toed in 30 degrees about the seam", toe_deg=30.0),
        "setup-a-laptop-nearer": setup_a("setup-a-laptop-nearer", "laptop 150 mm nearer to the eye", laptop_nearer_mm=150.0),
        "single-laptop": single(LAPTOP, "single-laptop"),
        "single-external": single(EXTERNAL, "single-external"),
        "bench-2560x1600": bench(2560, 1600),
        "bench-1920x1080": bench(1920, 1080),
        "bench-3840x2160": bench(3840, 2160),
    }
    for name, room in rooms.items():
        path = os.path.join(out_dir, name + ".json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(room, f, indent=2)
            f.write("\n")
        print("wrote", path)


if __name__ == "__main__":
    sys.exit(main())
