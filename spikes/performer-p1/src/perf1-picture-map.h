// The map from a monitor's pixel to a source pixel, on the CPU in double precision: pixel -> surface (perf1-room.h) ->
// the shot's picture -> source. The shader of the resample pass (perf1-picture-shader.c) does the same chain in float;
// this one decides things (is the picture magnified here, the fold's constant Jacobian) and is the reference the tests
// and the offline comparison hold the shader to. No GPU.
#pragma once

#include <stdbool.h>

#include "perf1-room.h"

// One shot's picture as the map needs it.
struct pic_view {
    double crop[4];          // source pixels used: x0, y0, x1, y1
    double rect[4];          // where the cropped picture sits on the surface, mm: u0, v0, u1, v1
};

// A camera slide at one refresh: the picture sits displaced by (du0 + ddu * row, dv0 + ddv * row) millimetres at pixel
// row `row` of the output (continuous, 0 = top edge): every row has its own light-up time, so its own position.
struct pic_slide {
    double du0, dv0, ddu, ddv;
};

// Source position of a continuous output pixel position (pixel centres at +0.5), the slide applied for its row.
void pic_map_pixel(const struct room_geom *g, const struct pic_view *v, const struct pic_slide *s, double px, double py,
                   double *sx, double *sy);

// Jacobian by central differences of +-0.5 output pixel: j = {dsx/dpx, dsx/dpy, dsy/dpx, dsy/dpy}.
void pic_jacobian(const struct room_geom *g, const struct pic_view *v, const struct pic_slide *s, double px, double py,
                  double j[4]);

// True when the picture is magnified at the monitor's centre (largest singular value of the Jacobian at most 1, and not
// exactly 1:1, which is a plain copy): libplacebo's rule for resampling in the sigmoid domain.
bool pic_monitor_upscales(const struct room_geom *g, const struct pic_view *v);
