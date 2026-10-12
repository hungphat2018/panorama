"""Canvas comparison for the picture-path spike: difference in 8-bit sRGB code values between a harness canvas and the float64
reference, over the pixels "away from edges".

Away from edges (the phase's wording, pinned down here):
  interior  = reference coverage 1 and at least EDGE_MARGIN output pixels from the nearest pixel that is not fully covered
              (the picture's boundary; the kernel is at most about 4 output pixels wide, so everything the boundary can
              influence through clamping or rounding of the picture rectangle is excluded). The monitor's own border does
              not count as an edge here: the picture continues behind the bezel, so a monitor's border pixels must be right.
  core      = interior, also kept EDGE_MARGIN pixels away from the monitor's border (what a crop-clamping renderer gets right)
  flat      = interior pixels whose reference gradient is under 2 code values per pixel (kernel table and arithmetic error only)
  edge      = interior pixels at or above that gradient (where a position error turns into a value error)
Everything is reported over all three, plus over every covered pixel for transparency.
"""
import numpy as np
from scipy import ndimage

from pp_image import linear_to_code

EDGE_MARGIN = 6


def code_of(canvas):
    """8-bit sRGB code values (float, not rounded) of the RGB of a premultiplied canvas, divided by coverage where it is > 0."""
    a = canvas[..., 3:4]
    straight = np.where(a > 1e-6, canvas[..., :3] / np.maximum(a, 1e-6), 0.0)
    return linear_to_code(straight)


def masks(ref):
    cover = ref[..., 3]
    valid = ~np.isnan(cover)
    full = valid & (cover >= 0.999)
    box = np.ones((2 * EDGE_MARGIN + 1,) * 2)
    interior = ndimage.binary_erosion(full, structure=box, border_value=1)
    core = ndimage.binary_erosion(full, structure=box, border_value=0)
    # gradient measured in code values of the green channel, a good enough proxy for "near a content edge"
    code_g = np.where(valid, code_of(np.nan_to_num(ref))[..., 1], 0.0)
    cy, cx = np.gradient(code_g)
    grad = np.hypot(cx, cy)
    grad = ndimage.maximum_filter(grad, size=5)
    flat = interior & (grad < 2.0)
    edge = interior & (grad >= 2.0)
    return {"interior": interior, "core": core, "flat": flat, "edge": edge, "covered": valid & (cover > 0.0), "valid": valid}


def stats(d):
    if d.size == 0:
        return {"n": 0}
    return {"n": int(d.size), "max": float(d.max()), "p99": float(np.percentile(d, 99)), "p99.9": float(np.percentile(d, 99.9)),
            "mean": float(d.mean()), "over1": int((d > 1.0).sum())}


def compare(gpu, ref):
    """gpu, ref: (H, W, 4) float32 canvases. Returns a dict of statistics."""
    m = masks(ref)
    cg, cr = code_of(gpu), code_of(np.where(np.isnan(ref), 0.0, ref))
    diff = np.max(np.abs(cg - cr), axis=-1)
    out = {}
    for name in ("interior", "core", "flat", "edge", "covered"):
        out[name] = stats(diff[m[name]])
    a_diff = np.abs(gpu[..., 3] - np.where(np.isnan(ref[..., 3]), 0.0, ref[..., 3]))
    far_outside = m["valid"] & ~ndimage.binary_dilation(m["covered"], structure=np.ones((2 * EDGE_MARGIN + 1,) * 2))
    out["alpha_interior_max_diff"] = float(a_diff[m["interior"]].max()) if m["interior"].any() else 0.0
    out["alpha_outside_max"] = float(np.abs(gpu[..., 3][far_outside]).max()) if far_outside.any() else 0.0
    out["gpu_range"] = [float(np.nanmin(gpu[..., :3])), float(np.nanmax(gpu[..., :3]))]
    out["nan_in_gpu"] = int(np.isnan(gpu).sum())
    out["_diff"] = diff
    out["_masks"] = m
    return out
