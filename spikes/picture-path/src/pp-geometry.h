// The map from a monitor's pixel to a source pixel, on the CPU in double precision: pixel -> physical point -> picture
// surface (u, v) -> picture -> source. The GLSL in shaders/pp-resample.glsl implements the same chain in float; the
// harness uses this one for decisions (up- or downscaling, the fold's pixel rectangles) and the geometry test compares
// it with the numpy reference.
#pragma once

#include <stdbool.h>

#include "pp-room.h"

enum pp_surface_model { PP_SURFACE_FOLD = 0, PP_SURFACE_CURVE = 1 };

struct pp_surface {
    enum pp_surface_model model;
    double curvature;               // curve: 1/R in 1/mm, the cylinder tangent to the gaze at the seat distance; 0 = flat plane
};

// The picture on the surface: a source crop placed by its centre and height in mm (u right, v UP, origin at the gaze axis).
struct pp_picture {
    double crop[4];                 // source pixels x0, y0, x1, y1
    double cw, ch;                  // crop size
    double width, height;           // mm on the surface
    double uc, vc;                  // centre
    double u_left, v_top;
};

// A camera slide: the picture moves by v * (t - t_ref); each row of a monitor is drawn at its own light time
// t = t_top + row / rows * scan_ms (ADR 0025).
struct pp_slide {
    double vu, vv;                  // mm per second on the surface
    double t_ref_ms;
    double t_top_ms[PP_MAX_MON];    // per monitor: when its top row lights
};

void pp_picture_init(struct pp_picture *p, int src_w, int src_h, double sar, const double *crop_or_null, double uc, double vc,
                     double height_mm);

// The picture-side vertical flip: v is up, source rows go down.
void pp_picture_uv_to_src(const struct pp_picture *p, double u, double v, double *sx, double *sy);

// Monitor k's pixel position (continuous, centres at +0.5) on the surface.
void pp_monitor_uv(const struct pp_room *r, int k, double px, double py, const struct pp_surface *s, double *u, double *v);

// Pixel -> source position, with the slide applied for the pixel's own row.
void pp_map_to_source(const struct pp_room *r, int k, const struct pp_picture *p, const struct pp_surface *s,
                      const struct pp_slide *slide, double px, double py, double *sx, double *sy);

// Position and Jacobian by central differences of +-0.5 output pixel: j = {dsx/dpx, dsx/dpy, dsy/dpx, dsy/dpy}.
void pp_source_jacobian(const struct pp_room *r, int k, const struct pp_picture *p, const struct pp_surface *s,
                        const struct pp_slide *slide, double px, double py, double *sx, double *sy, double j[4]);

// True when the picture is upscaled at the monitor's centre pixel (libplacebo's sigmoid rule): the largest singular
// value of J is at most 1 and the map is not exactly 1:1 (a 1:1 map is a no-op: no scaler, no sigmoid).
bool pp_monitor_upscales(const struct pp_room *r, int k, const struct pp_picture *p, const struct pp_surface *s);

// Fold only: the monitor's pixel axes as affine functions of the surface, px -> u = u_px0 + du_dpx * px and
// py -> v = v_py0 + dv_dpy * py (dv_dpy < 0: v falls as rows go down).
void pp_fold_axes(const struct pp_room *r, int k, double *u_px0, double *du_dpx, double *v_py0, double *dv_dpy);
