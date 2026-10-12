"""The float64 reference renderer: one monitor's canvas (premultiplied linear RGBA, BT.709 working space, 1.0 = SDR white)
from a scenario file, with the geometry of pp_geometry and the kernel of pp_resample. The harness reads the same scenario.

Scenario (JSON, paths relative to the harness folder):
  room            room file (panorama.room/1)
  surface         {"model": "fold"} or {"model": "curve", "curvature": 1/R in 1/mm}  (curvature 0 = flat plane)
  source          {"file": "patterns/x.ppm", "sar": 1.0}; source_b the same, for the rolling blend
  cue             {"crop": [x0, y0, x1, y1] (default whole), "centre_mm": [u, v], "height_mm": h}
  kernel          ewa_lanczossharp (default) | ewa_lanczos | ewa_lanczos4sharpest | ewa_robidoux | ewa_catmull_rom | ewa_mitchell
  domain          "auto" (sigmoid when the monitor upscales, linear otherwise: libplacebo's rule) | "linear" | "sigmoid"
  slide           {"velocity_mm_s": [vu, vv], "t_ref_ms": 0, "t_top_ms": {"<monitor id>": ms}}: each row is drawn for its
                  own light time t = t_top + row / rows * scan_ms; the content is sampled from where it is at that time
                  (shifted by v * (t - t_ref) on the surface). The picture's rectangle on the surface stays put, so coverage
                  uses the unshifted position; positions beyond the frame clamp (the product fills the strip from the next frame)
  blend           {"<monitor id>": {"wa": a, "wb": b}}: weight of source_b in row py is clamp(a + b * py, 0, 1)
"""
import json
import math
import os

import numpy as np

import pp_geometry as geo
import pp_resample as rs
from pp_image import read_ppm


def load_scenario(path):
    with open(path, "r", encoding="utf-8") as f:
        scn = json.load(f)
    scn["_dir"] = os.path.dirname(os.path.abspath(path))
    return scn


def resolve(scn, rel):
    """Scenario paths are relative to the current directory (the harness folder), as in the C harness."""
    return rel if os.path.isabs(rel) else os.path.abspath(rel)


class Setup:
    """Everything a monitor render needs, derived once from the scenario."""

    def __init__(self, scn):
        self.scn = scn
        self.room = geo.Room(resolve(scn, scn["room"]))
        surf = scn.get("surface", {"model": "fold"})
        self.model = surf["model"]
        self.curvature = float(surf.get("curvature", 0.0))
        self.src_a = read_ppm(resolve(scn, scn["source"]["file"]))
        self.sar = float(scn["source"].get("sar", 1.0))
        self.src_b = read_ppm(resolve(scn, scn["source_b"]["file"])) if "source_b" in scn else None
        h, w, _ = self.src_a.shape
        cue = scn.get("cue", {})
        crop = cue.get("crop")
        centre = cue.get("centre_mm", [0.0, 0.0])
        height = cue.get("height_mm")
        if height is None:
            raise ValueError("cue.height_mm is required in a scenario")
        self.picture = geo.Picture(w, h, self.sar, crop, centre, height)
        self.kernel = rs.make_kernel(scn.get("kernel", "ewa_lanczossharp"))
        self.domain = scn.get("domain", "auto")
        slide = scn.get("slide", {})
        self.slide_v = slide.get("velocity_mm_s", [0.0, 0.0])
        self.slide_t_ref = float(slide.get("t_ref_ms", 0.0))
        self.slide_t_top = slide.get("t_top_ms", {})
        self.blend = scn.get("blend", {})

    def decide_domain(self, k):
        """libplacebo's rule: sigmoidize when the picture is upscaled on this monitor, linear light when it is shrunk.
        Evaluated at the monitor's centre pixel."""
        if self.domain != "auto":
            return self.domain
        m = self.room.monitors[k]
        px, py = np.array([m.W / 2.0]), np.array([m.H / 2.0])
        _, _, j = geo.source_jacobian(self.room, k, self.picture, px, py, self.model, self.curvature)
        ev = np.linalg.eigvalsh(j[0] @ j[0].T)
        s_max, s_min = math.sqrt(max(ev)), math.sqrt(max(min(ev), 0.0))
        if s_max > 1.0 + 1e-6 or (abs(s_max - 1.0) <= 1e-6 and abs(s_min - 1.0) <= 1e-6):
            return "linear"          # shrunk, or exactly 1:1 (libplacebo skips the scaler and sigmoid there)
        return "sigmoid"


def render_monitor(setup, k, rows=None, block=48):
    """Canvas (H, W, 4) float32; rows = (r0, r1) renders only those rows (the rest stay NaN)."""
    room, m = setup.room, setup.room.monitors[k]
    pic = setup.picture
    domain = setup.decide_domain(k)
    to_domain = (lambda x: rs.sigmoidize(x)) if domain == "sigmoid" else (lambda x: x)
    from_domain = (lambda y: rs.unsigmoidize(y)) if domain == "sigmoid" else (lambda y: y)
    lin_a = to_domain(rs.srgb_to_linear(setup.src_a / 255.0))
    lin_b = to_domain(rs.srgb_to_linear(setup.src_b / 255.0)) if setup.src_b is not None else None
    t_top = float(setup.slide_t_top.get(m.id, 0.0))
    vu, vv = setup.slide_v

    def per_row(py):
        t = geo.row_time_ms(m, py, t_top)
        return (vu * (t - setup.slide_t_ref) / 1000.0, vv * (t - setup.slide_t_ref) / 1000.0)

    use_slide = bool(vu or vv)
    bl = setup.blend.get(m.id)
    out = np.full((m.H, m.W, 4), np.nan, dtype=np.float32)
    r0, r1 = rows if rows else (0, m.H)
    for a in range(r0, r1, block):
        b = min(r1, a + block)
        px, py = np.meshgrid(np.arange(m.W) + 0.5, np.arange(a, b) + 0.5)
        sx, sy, j = geo.source_jacobian(room, k, pic, px, py, setup.model, setup.curvature,
                                        per_row=per_row if use_slide else None)
        fx, fy, fj = sx.ravel(), sy.ravel(), j.reshape(-1, 2, 2)
        # coverage belongs to the picture's rectangle on the surface, which does not slide: use the unshifted position
        bx, by = geo.map_to_source(room, k, pic, px, py, setup.model, setup.curvature)
        bx, by = bx.ravel(), by.ravel()
        color = from_domain(rs.ewa(lin_a, fx, fy, fj, setup.kernel))
        if bl is not None:
            w = np.clip(bl["wa"] + bl["wb"] * py.ravel(), 0.0, 1.0)
            if lin_b is None:
                raise ValueError("blend needs source_b")
            other = from_domain(rs.ewa(lin_b, fx, fy, fj, setup.kernel))
            color = (1.0 - w)[:, None] * color + w[:, None] * other
        gx = np.hypot(fj[:, 0, 0], fj[:, 0, 1])               # source pixels per output pixel, along each edge's normal
        gy = np.hypot(fj[:, 1, 0], fj[:, 1, 1])
        x0, y0, x1, y1 = pic.crop
        cov = (np.clip(0.5 + (bx - x0) / gx, 0, 1) * np.clip(0.5 + (x1 - bx) / gx, 0, 1)
               * np.clip(0.5 + (by - y0) / gy, 0, 1) * np.clip(0.5 + (y1 - by) / gy, 0, 1))
        blk = np.empty((b - a, m.W, 4))
        blk[..., :3] = (color * cov[:, None]).reshape(b - a, m.W, 3)
        blk[..., 3] = cov.reshape(b - a, m.W)
        out[a:b] = blk
    return out, domain
