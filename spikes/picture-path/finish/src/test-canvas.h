// Synthetic canvases for the finish tests: what pass 1 would hand over (RGBA16F, linear light, alpha = picture coverage),
// made on the GPU so a test can make a new film frame every refresh without touching the CPU.
#pragma once

#include "test-util.h"

// A shot: a picture rectangle on the canvas (pixels, y down) with a horizontal blend between two linear colours,
// vertical shading, a slow texture and film grain. Alpha is 1 inside the rectangle and 0 outside.
struct tc_shot {
    float rect[4];       // x0, y0, x1, y1
    float left[3];       // linear colour at the rectangle's left edge
    float right[3];      // ... and at its right edge
    float shade;         // 0..1: lighter at the top, darker at the bottom (0 = flat)
    float grain;         // relative amplitude of per-pixel, per-frame noise (0.02 = 2 %)
    float flash;         // 0..1: blend toward white over the whole picture (a lightning frame)
    float texture;       // relative amplitude of a slow sinusoidal texture (0 = flat colour)
    bool ellipse;        // the picture is the ellipse inscribed in `rect` instead of the rectangle
};

bool tc_gen_shot(const struct fp_env *env, pl_tex canvas, const struct tc_shot *shot, int frame_index);

// A grey ramp across the canvas, defined in sRGB signal space: signal = lo + (hi - lo) * x / (w - 1); the canvas holds
// the linear-light value (sRGB inverse EOTF). alpha = 1.
bool tc_gen_ramp(const struct fp_env *env, pl_tex canvas, float signal_lo, float signal_hi);

// A flat colour (linear), alpha 1.
bool tc_gen_flat(const struct fp_env *env, pl_tex canvas, const float rgb[3]);

// Linear-light luminance (Rec. 709 weights) of a pixel in a read-back float image.
static inline float tc_luma(const float *px)
{
    return 0.2126f * px[0] + 0.7152f * px[1] + 0.0722f * px[2];
}

// CIE L* of a relative luminance (white = 1).
static inline double tc_lstar(double y)
{
    return y > 216.0 / 24389.0 ? 116.0 * cbrt(y) - 16.0 : y * 24389.0 / 27.0;
}
