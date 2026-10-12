"""Reference geometry for the picture-path spike: float64, numpy only, written from the written model and not from the
C/GLSL code (the two implementations are compared in the harness runs).

Frame: the room's seat frame in millimetres, right-handed, +x right, +y UP, +z away from the eye toward the screens,
origin at the eye (panorama.room/1). Screen pixels count rows DOWN. The two vertical conventions meet in exactly two
places, both here: a monitor's pixel row <-> its local up coordinate (row_to_local_y / local_y_to_row), and a picture's
source row <-> the surface height v (the picture class). Nothing else flips y.

Surface (u, v), both in mm: v is the height above eye level, u runs to the right and is 0 where the gaze axis meets the
surface; the first seam's centre sits on the gaze axis in every room this spike makes, so fold and curve share the origin.
  fold  : the picture is printed on the panels and unrolled: u = millimetres along the panels, gaps included.
  curve : the picture lies on a vertical cylinder of radius R = 1/curvature tangent to the gaze at the seat distance D;
          a monitor pixel's ray from the eye hits it, u = R * (angle around the cylinder axis), v = height of the hit.
          R = D puts the axis on the eye (u = D * azimuth, v = D * tan(elevation)); curvature 0 is the flat plane at D.
"""
import json
import math

import numpy as np


# ---- rotations (degrees, right-hand rule about +y, +x, +z; a negative yaw turns a right-hand panel toward the eye)

def rot_y(deg):
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])


def rot_x(deg):
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])


def rot_z(deg):
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


def rotation(yaw, pitch, roll):
    return rot_y(yaw) @ rot_x(pitch) @ rot_z(roll)


# ---- the vertical convention, monitor side

def row_to_local_y(row, h_px, h_mm):
    """Continuous pixel row (row 0 edge at the top, pixel centres at +0.5) -> mm above the panel centre."""
    return (0.5 - np.asarray(row, dtype=float) / h_px) * h_mm


def local_y_to_row(y_mm, h_px, h_mm):
    return (0.5 - np.asarray(y_mm, dtype=float) / h_mm) * h_px


class Monitor:
    def __init__(self, d):
        self.id = d["id"]
        self.W, self.H = (int(v) for v in d["panel"]["native_px"])
        self.w_mm, self.h_mm = (float(v) for v in d["panel"]["active_area_mm"])
        pose = d["pose"]
        self.centre = np.array(pose["centre_mm"], dtype=float)
        self.R = rotation(pose.get("yaw_deg", 0.0), pose.get("pitch_deg", 0.0), pose.get("roll_deg", 0.0))
        self.ex, self.ey, self.ez = self.R[:, 0], self.R[:, 1], self.R[:, 2]
        t = d.get("timing", {})
        self.refresh_hz = float(t.get("refresh_hz", 60.0))
        self.scan_ms = float(t.get("scan_ms", 1000.0 / self.refresh_hz))
        self.lag_ms = float(t.get("lag_ms", 0.0))
        self.pitch_mm = self.w_mm / self.W

    def point(self, px, py):
        """Seat-frame point of continuous pixel position (px, py) and the local (lx, ly) in mm."""
        lx = (np.asarray(px, dtype=float) / self.W - 0.5) * self.w_mm
        ly = row_to_local_y(py, self.H, self.h_mm)
        p = self.centre + lx[..., None] * self.ex + ly[..., None] * self.ey
        return p, lx, ly


class Room:
    def __init__(self, path):
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        if d.get("schema") != "panorama.room/1":
            raise ValueError("not a panorama.room/1 file: %s" % path)
        self.name = d.get("name", path)
        self.seat_distance = float(d["seat"]["distance_mm"])
        self.gaze_yaw = float(d["seat"].get("gaze_yaw_deg", 0.0))
        if abs(self.gaze_yaw) > 1e-12 or abs(float(d["seat"].get("gaze_pitch_deg", 0.0))) > 1e-12:
            raise ValueError("this spike only handles a gaze along +z")
        self.monitors = [Monitor(m) for m in d["monitors"]]
        self.gaps = [float(s["gap_mm"]) for s in d["derived"]["seams"]]
        if len(self.gaps) != max(0, len(self.monitors) - 1):
            raise ValueError("derived.seams must list one gap per neighbouring pair")
        # fold: u of each panel's left edge when unrolled from the first panel's left edge
        self.u0 = [0.0]
        for k in range(1, len(self.monitors)):
            self.u0.append(self.u0[-1] + self.monitors[k - 1].w_mm + self.gaps[k - 1])
        if len(self.monitors) >= 2:
            self.u_anchor = self.u0[1] - 0.5 * self.gaps[0]
        else:
            self.u_anchor = 0.5 * self.monitors[0].w_mm


# ---- surface models: seat-frame point (or panel-local coordinates for fold) -> (u, v)

def fold_uv(room, k, lx, ly):
    m = room.monitors[k]
    return room.u0[k] + lx + 0.5 * m.w_mm - room.u_anchor, m.centre[1] + ly


def curve_uv(points, distance, curvature):
    """Ray from the eye (origin) through `points` (..., 3) onto the tangent cylinder; u = R * angle, v = hit height."""
    p = np.asarray(points, dtype=float)
    d = p / np.linalg.norm(p, axis=-1, keepdims=True)
    dx, dy, dz = d[..., 0], d[..., 1], d[..., 2]
    if abs(curvature) < 1e-12:
        t = distance / dz
        return t * dx, t * dy
    big_r = 1.0 / curvature
    if big_r < 0.5 * distance:
        raise ValueError("cylinder radius below half the seat distance puts the eye outside it")
    za = distance - big_r
    a = dx * dx + dz * dz
    b = dz * za
    c = za * za - big_r * big_r
    t = (b + np.sqrt(b * b - a * c)) / a
    hx, hy, hz = t * dx, t * dy, t * dz
    return big_r * np.arctan2(hx, hz - za), hy


def monitor_uv(room, k, px, py, model, curvature):
    m = room.monitors[k]
    p, lx, ly = m.point(px, py)
    if model == "fold":
        return fold_uv(room, k, lx, ly)
    return curve_uv(p, room.seat_distance, curvature)


# ---- the picture: source crop + placement on the surface

class Picture:
    """crop = (x0, y0, x1, y1) in source pixels; placement = centre (uc, vc) and height in mm on the surface."""

    def __init__(self, src_w, src_h, sar=1.0, crop=None, centre_mm=(0.0, 0.0), height_mm=215.0):
        self.crop = tuple(crop) if crop else (0.0, 0.0, float(src_w), float(src_h))
        self.cw = self.crop[2] - self.crop[0]
        self.ch = self.crop[3] - self.crop[1]
        self.height = float(height_mm)
        self.width = self.height * self.cw / self.ch * sar
        self.uc, self.vc = float(centre_mm[0]), float(centre_mm[1])
        self.u_left = self.uc - 0.5 * self.width
        self.v_top = self.vc + 0.5 * self.height

    def uv_to_src(self, u, v):
        sx = self.crop[0] + (np.asarray(u) - self.u_left) / self.width * self.cw
        sy = self.crop[1] + (self.v_top - np.asarray(v)) / self.height * self.ch   # v is up, source rows go down
        return sx, sy


def map_to_source(room, k, picture, px, py, model, curvature, slide_mm=(0.0, 0.0)):
    """Continuous monitor pixel position -> continuous source position. slide_mm shifts the picture on the surface
    (a camera slide: the content seen at surface (u, v) comes from picture position (u - du, v - dv))."""
    u, v = monitor_uv(room, k, px, py, model, curvature)
    return picture.uv_to_src(u - slide_mm[0], v - slide_mm[1])


def row_time_ms(monitor, py, t_top_ms=0.0):
    """Time the light of continuous row position py appears: top-row time + (row / rows) * scan time (ADR 0025)."""
    return t_top_ms + np.asarray(py, dtype=float) / monitor.H * monitor.scan_ms


def monitor_grid(monitor):
    """Pixel-centre coordinates of the whole monitor, shape (H, W) each."""
    px = np.arange(monitor.W, dtype=float) + 0.5
    py = np.arange(monitor.H, dtype=float) + 0.5
    return np.meshgrid(px, py)


def source_jacobian(room, k, picture, px, py, model, curvature, slide_mm=(0.0, 0.0), per_row=None):
    """Central differences (+-0.5 output pixel) of the pixel -> source map. Returns (sx, sy, J) with J[..., 2, 2] rows
    (sx, sy), columns (d/dpx, d/dpy). per_row(py) -> (du, dv) lets the slide depend on the row, evaluated at the
    sample's own row (the difference in py then also sees the shear, as the GPU code does)."""
    def f(x, y):
        if per_row is not None:
            du, dv = per_row(y)
        else:
            du, dv = slide_mm
        return map_to_source(room, k, picture, x, y, model, curvature, (du, dv))

    sx, sy = f(px, py)
    sx_xp, sy_xp = f(px + 0.5, py)
    sx_xm, sy_xm = f(px - 0.5, py)
    sx_yp, sy_yp = f(px, py + 0.5)
    sx_ym, sy_ym = f(px, py - 0.5)
    j = np.empty(np.shape(sx) + (2, 2))
    j[..., 0, 0] = sx_xp - sx_xm
    j[..., 1, 0] = sy_xp - sy_xm
    j[..., 0, 1] = sx_yp - sx_ym
    j[..., 1, 1] = sy_yp - sy_ym
    return sx, sy, j
