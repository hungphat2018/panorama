// Pass 3, the colour maths on the CPU (double precision, independent of libplacebo): the monitor's final 3x3 and the
// reference formula the shader must match. Kept apart from the shader so the unit tests link without a GPU library.
//
// Per pixel the shader (finish-color.c) does, in this order, all in linear light:
//   v = M * rgb                      gamut step (identity when Windows owns the gamut) times the relative 3x3 tweak
//   v = max(v, 0)                    colours outside the panel's gamut are clipped, as Windows' own stage does
//   v = lift + (gain - lift) * v^g   per channel: black lift, white level, gamma on linear light
//   v = clamp(v, 0, 1)
//   e = encode(v)                    piecewise sRGB, or a pure power law
#include <math.h>
#include <string.h>

#include "perf1-finish-passes.h"

#define D65_X 0.3127
#define D65_Y 0.3290

typedef struct { double m[9]; } mat3d; // row-major

static mat3d mat_mul(mat3d a, mat3d b)
{
    mat3d r;
    for (int i = 0; i < 3; i++)
        for (int j = 0; j < 3; j++) {
            double s = 0;
            for (int k = 0; k < 3; k++)
                s += a.m[i * 3 + k] * b.m[k * 3 + j];
            r.m[i * 3 + j] = s;
        }
    return r;
}

static mat3d mat_inv(mat3d a)
{
    const double *m = a.m;
    double c00 = m[4] * m[8] - m[5] * m[7], c01 = m[5] * m[6] - m[3] * m[8], c02 = m[3] * m[7] - m[4] * m[6];
    double det = m[0] * c00 + m[1] * c01 + m[2] * c02;
    double id = det != 0.0 ? 1.0 / det : 0.0;
    mat3d r = {{
        c00 * id, (m[2] * m[7] - m[1] * m[8]) * id, (m[1] * m[5] - m[2] * m[4]) * id,
        c01 * id, (m[0] * m[8] - m[2] * m[6]) * id, (m[2] * m[3] - m[0] * m[5]) * id,
        c02 * id, (m[1] * m[6] - m[0] * m[7]) * id, (m[0] * m[4] - m[1] * m[3]) * id,
    }};
    return r;
}

// RGB -> XYZ for primaries given as xy chromaticities; the white is forced to D65 (so drive (1,1,1) is D65).
static mat3d rgb_to_xyz(struct fp_xy r, struct fp_xy g, struct fp_xy b)
{
    mat3d p = {{ r.x / r.y, g.x / g.y, b.x / b.y,
                 1.0, 1.0, 1.0,
                 (1 - r.x - r.y) / r.y, (1 - g.x - g.y) / g.y, (1 - b.x - b.y) / b.y }};
    double wx = D65_X / D65_Y, wy = 1.0, wz = (1 - D65_X - D65_Y) / D65_Y;
    mat3d pi = mat_inv(p);
    double s[3] = { pi.m[0] * wx + pi.m[1] * wy + pi.m[2] * wz, pi.m[3] * wx + pi.m[4] * wy + pi.m[5] * wz,
                    pi.m[6] * wx + pi.m[7] * wy + pi.m[8] * wz };
    mat3d out;
    for (int i = 0; i < 3; i++)
        for (int j = 0; j < 3; j++)
            out.m[i * 3 + j] = p.m[i * 3 + j] * s[j];
    return out;
}

static const struct fp_primaries bt709 = {
    .r = { 0.640, 0.330 }, .g = { 0.300, 0.600 }, .b = { 0.150, 0.060 }, .w = { D65_X, D65_Y },
};

void fp_color_make(const struct fp_color_monitor *in, struct fp_color_gpu *out)
{
    mat3d gamut = {{ 1, 0, 0, 0, 1, 0, 0, 0, 1 }};
    if (in->gamut_owner == FP_GAMUT_PLAYER) {
        mat3d src = rgb_to_xyz(bt709.r, bt709.g, bt709.b);
        mat3d dst = rgb_to_xyz(in->panel.r, in->panel.g, in->panel.b);
        gamut = mat_mul(mat_inv(dst), src);
    }
    mat3d tweak = {{ 1, 0, 0, 0, 1, 0, 0, 0, 1 }};
    bool any = false;
    for (int i = 0; i < 9; i++)
        any |= in->tweak_matrix[i] != 0.0;
    if (any)
        memcpy(tweak.m, in->tweak_matrix, sizeof(tweak.m));
    mat3d full = mat_mul(tweak, gamut);
    for (int i = 0; i < 9; i++)
        out->m[i] = (float) full.m[i];
    for (int c = 0; c < 3; c++) {
        out->gain[c] = in->gain[c] != 0.0f ? in->gain[c] : 1.0f;
        out->gamma[c] = in->gamma[c] != 0.0f ? in->gamma[c] : 1.0f;
        out->lift[c] = in->lift[c];
    }
    out->transfer = in->transfer;
    out->power = in->power > 0.0 ? (float) in->power : 2.2f;
    out->out_bits = in->out_bits > 0 ? in->out_bits : 8;
}

void fp_color_reference(const struct fp_color_gpu *c, const double rgb_lin[3], double enc_out[3])
{
    for (int i = 0; i < 3; i++) {
        double v = c->m[i * 3] * rgb_lin[0] + c->m[i * 3 + 1] * rgb_lin[1] + c->m[i * 3 + 2] * rgb_lin[2];
        if (v < 0.0)
            v = 0.0;
        v = c->lift[i] + ((double) c->gain[i] - c->lift[i]) * pow(v, c->gamma[i]);
        v = v < 0.0 ? 0.0 : v > 1.0 ? 1.0 : v;
        if (c->transfer == FP_TRC_SRGB)
            v = v >= 0.0031308 ? 1.055 * pow(v, 1.0 / 2.4) - 0.055 : v * 12.92;
        else
            v = pow(v, 1.0 / c->power);
        enc_out[i] = v;
    }
}
