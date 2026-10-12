// Pass 2 tests: the fill in space (continuity at the picture's edge, falloff, hue, odd shapes, black mode, state exactness)
// and in time (the cut rules and the flicker study; the numbers are computed by analyze-flicker.py from the CSVs).
#include "test-canvas.h"
#include "test-main.h"
#include "test-surround.h"

#define W 2560
#define H 1600
#define RECT_TOP 264
#define RECT_BOTTOM 1336

static double phi(double x)
{
    return 0.5 * erfc(-x / sqrt(2.0));
}

static const struct fp_surround_look default_look = { .mode = FP_SURROUND_GLOW, .gain = 0.8f, .falloff_px = 250.0f,
                                                      .saturation = 1.0f, .max_level = 1.0f };
static const struct fp_surround_time steady_time = { .tau_steady_s = 0.5f, .cut_rule = FP_CUT_SNAP };

// Mean luminance of a pixel block of a read-back float image.
static double block_luma(const float *img, int x0, int x1, int y0, int y1)
{
    double sum = 0;
    int n = 0;
    for (int y = y0; y < y1; y++)
        for (int x = x0; x < x1; x++, n++)
            sum += tc_luma(&img[4 * ((size_t) y * W + x)]);
    return sum / n;
}

static void block_rgb(const float *img, int x0, int x1, int y0, int y1, double out[3])
{
    out[0] = out[1] = out[2] = 0;
    int n = 0;
    for (int y = y0; y < y1; y++)
        for (int x = x0; x < x1; x++, n++)
            for (int c = 0; c < 3; c++)
                out[c] += img[4 * ((size_t) y * W + x) + c];
    for (int c = 0; c < 3; c++)
        out[c] /= n;
}

// ---- spatial ------------------------------------------------------------------------------------------------------

static void a_flat(struct tctx *t, pl_tex canvas, pl_tex filled, struct fp_surround *s)
{
    const struct fp_env *env = &t->dev.env;
    printf("-- A1: a flat picture on the canvas, glow mode\n");
    struct tc_shot shot = { .rect = { 0, RECT_TOP, W, RECT_BOTTOM }, .left = { 0.5f, 0.2f, 0.1f }, .right = { 0.5f, 0.2f, 0.1f } };
    pl_dispatch_reset_frame(env->dp);
    tc_gen_shot(env, canvas, &shot, 0);
    struct fp_surround_frame fr = { .dt_s = 1.0 / 240, .reset = true };
    pl_dispatch_reset_frame(env->dp);
    tu_check(fp_surround_apply(s, canvas, filled, &default_look, &steady_time, &fr), "surround applied");
    float *in = tu_read_f32(env, canvas, W, H), *out = tu_read_f32(env, filled, W, H);
    if (!in || !out) {
        tu_check(false, "read back");
        return;
    }

    // the picture is untouched, bit for bit
    long changed = 0, nonfinite = 0;
    for (int y = RECT_TOP; y < RECT_BOTTOM; y++)
        for (int x = 0; x < W; x++)
            for (int c = 0; c < 3; c++)
                changed += in[4 * ((size_t) y * W + x) + c] != out[4 * ((size_t) y * W + x) + c];
    for (size_t i = 0; i < (size_t) W * H * 4; i++)
        nonfinite += !isfinite(out[i]);
    tu_check(changed == 0, "the picture's pixels are bit-identical in the output (%ld of %d values changed)", changed, W * (RECT_BOTTOM - RECT_TOP) * 3);
    tu_check(nonfinite == 0, "no NaN or infinity anywhere in the output");

    // continuity and falloff above the picture, measured over the middle of the row
    double pic_y = block_luma(in, 1200, 1360, RECT_TOP + 4, RECT_TOP + 8);
    const double sigma = default_look.falloff_px, h = RECT_BOTTOM - RECT_TOP;
    double worst = 0;
    printf("  distance above the edge   glow / (gain x picture edge)   expected 2*(Phi((d+h)/s)-Phi(d/s))\n");
    static const int dists[] = { 1, 8, 32, 64, 128, 192, 250 };
    for (size_t i = 0; i < TU_ARRAY_SIZE(dists); i++) {
        int d = dists[i];
        double g = block_luma(out, 1200, 1360, RECT_TOP - d - 1, RECT_TOP - d + 1) / (default_look.gain * pic_y);
        double want = 2.0 * (phi((d + h) / sigma) - phi(d / sigma));
        worst = fmax(worst, fabs(g - want));
        printf("  %6d px                   %.3f                          %.3f\n", d, g, want);
    }
    tu_check(worst < 0.08, "the glow dims with distance as the model says: worst difference %.3f", worst);
    double near_ratio = block_luma(out, 1200, 1360, RECT_TOP - 2, RECT_TOP - 1) / (default_look.gain * pic_y);
    tu_check(near_ratio > 0.93 && near_ratio < 1.01, "next to the picture the glow is gain x edge colour (ratio %.3f)", near_ratio);
    double c_in[3], c_out[3];
    block_rgb(in, 1200, 1360, RECT_TOP + 4, RECT_TOP + 8, c_in);
    block_rgb(out, 1200, 1360, RECT_TOP - 40, RECT_TOP - 38, c_out);
    double n_in = sqrt(c_in[0] * c_in[0] + c_in[1] * c_in[1] + c_in[2] * c_in[2]), n_out = sqrt(c_out[0] * c_out[0] + c_out[1] * c_out[1] + c_out[2] * c_out[2]);
    double hue = 0;
    for (int c = 0; c < 3; c++)
        hue = fmax(hue, fabs(c_out[c] / n_out - c_in[c] / n_in));
    tu_check(hue < 0.01, "the glow keeps the picture's colour (hue error %.4f)", hue);
    char path[512];
    snprintf(path, sizeof(path), "%s/surround-flat.png", t->out);
    tu_png_write_linear(path, out, W, H, 1.0f);
    free(in);
    free(out);
}

static void a_black_and_empty(struct tctx *t, pl_tex canvas, pl_tex filled, struct fp_surround *s)
{
    const struct fp_env *env = &t->dev.env;
    printf("-- A2: black mode, and a canvas with no picture\n");
    struct tc_shot shot = { .rect = { 200, RECT_TOP, 2300, RECT_BOTTOM }, .left = { 0.5f, 0.2f, 0.1f }, .right = { 0.1f, 0.3f, 0.5f } };
    pl_dispatch_reset_frame(env->dp);
    tc_gen_shot(env, canvas, &shot, 0);
    struct fp_surround_look black = default_look;
    black.mode = FP_SURROUND_BLACK;
    struct fp_surround_frame fr = { .dt_s = 1.0 / 240, .reset = true };
    pl_dispatch_reset_frame(env->dp);
    tu_check(fp_surround_apply(s, canvas, filled, &black, &steady_time, &fr), "black mode applied");
    float *in = tu_read_f32(env, canvas, W, H), *out = tu_read_f32(env, filled, W, H);
    if (in && out) {
        long bad = 0, changed = 0;
        for (int y = 0; y < H; y++)
            for (int x = 0; x < W; x++) {
                bool inside = x >= 200 && x < 2300 && y >= RECT_TOP && y < RECT_BOTTOM;
                const float *o = &out[4 * ((size_t) y * W + x)], *i = &in[4 * ((size_t) y * W + x)];
                if (inside)
                    changed += o[0] != i[0] || o[1] != i[1] || o[2] != i[2];
                else
                    bad += o[0] != 0.0f || o[1] != 0.0f || o[2] != 0.0f;
                bad += o[3] != 1.0f;
            }
        tu_check(bad == 0 && changed == 0, "black mode: the leftover is exactly 0, the picture exact, alpha 1 (%ld wrong, %ld changed)", bad, changed);
    }
    free(in);
    free(out);

    // no picture at all: alpha 0 everywhere
    float zero[3] = { 0, 0, 0 };
    struct tc_shot none = { .rect = { 0, 0, 0, 0 } };
    memcpy(none.left, zero, sizeof(zero));
    pl_dispatch_reset_frame(env->dp);
    tc_gen_shot(env, canvas, &none, 0);
    fr.reset = true;
    pl_dispatch_reset_frame(env->dp);
    tu_check(fp_surround_apply(s, canvas, filled, &default_look, &steady_time, &fr), "glow applied to an empty canvas");
    out = tu_read_f32(env, filled, W, H);
    if (out) {
        long nonzero = 0, nonfinite = 0;
        for (size_t i = 0; i < (size_t) W * H; i++) {
            nonfinite += !isfinite(out[4 * i]) || !isfinite(out[4 * i + 1]) || !isfinite(out[4 * i + 2]);
            nonzero += out[4 * i] != 0.0f || out[4 * i + 1] != 0.0f || out[4 * i + 2] != 0.0f;
        }
        tu_check(nonfinite == 0 && nonzero == 0, "no picture, no glow: the output is black and finite (%ld non-zero, %ld non-finite)", nonzero, nonfinite);
        free(out);
    }
}

static void a_shapes(struct tctx *t, pl_tex canvas, pl_tex filled, struct fp_surround *s)
{
    const struct fp_env *env = &t->dev.env;
    printf("-- A3: a gradient edge, an ellipse (what a curved picture looks like) and a 4:3 picture with leftover on the sides\n");
    char path[512];
    float *out, *in;

    // horizontal colour gradient: the glow follows the edge colour along the edge
    struct tc_shot grad = { .rect = { 0, RECT_TOP, W, RECT_BOTTOM }, .left = { 1.0f, 0.0f, 0.0f }, .right = { 0.0f, 0.0f, 1.0f } };
    pl_dispatch_reset_frame(env->dp);
    tc_gen_shot(env, canvas, &grad, 0);
    struct fp_surround_frame fr = { .dt_s = 1.0 / 240, .reset = true };
    pl_dispatch_reset_frame(env->dp);
    fp_surround_apply(s, canvas, filled, &default_look, &steady_time, &fr);
    in = tu_read_f32(env, canvas, W, H);
    out = tu_read_f32(env, filled, W, H);
    if (in && out) {
        double worst = 0;
        for (int x = 160; x < W; x += 480) {
            double pi[3], po[3];
            block_rgb(in, x - 8, x + 8, RECT_TOP + 4, RECT_TOP + 6, pi);
            block_rgb(out, x - 8, x + 8, RECT_TOP - 30, RECT_TOP - 28, po);
            double nr = pi[0] + pi[1] + pi[2], no = po[0] + po[1] + po[2];
            double e = fabs(pi[0] / nr - po[0] / no) + fabs(pi[2] / nr - po[2] / no);
            worst = fmax(worst, e);
            printf("  x=%4d  picture edge (%.2f, %.2f, %.2f)  glow 30 px above (%.2f, %.2f, %.2f)\n", x, pi[0], pi[1], pi[2], po[0], po[1], po[2]);
        }
        tu_check(worst < 0.12, "red-to-blue gradient: the glow's colour follows the edge colour along x (worst chromaticity difference %.3f)", worst);
        snprintf(path, sizeof(path), "%s/surround-gradient.png", t->out);
        tu_png_write_linear(path, out, W, H, 1.0f);
    }
    free(in);
    free(out);

    // an ellipse
    struct tc_shot ell = { .rect = { 480, 200, 2080, 1400 }, .left = { 0.9f, 0.7f, 0.2f }, .right = { 0.2f, 0.6f, 0.9f },
                           .shade = 0.5f, .texture = 0.15f, .ellipse = true };
    pl_dispatch_reset_frame(env->dp);
    tc_gen_shot(env, canvas, &ell, 0);
    fr.reset = true;
    pl_dispatch_reset_frame(env->dp);
    fp_surround_apply(s, canvas, filled, &default_look, &steady_time, &fr);
    in = tu_read_f32(env, canvas, W, H);
    out = tu_read_f32(env, filled, W, H);
    if (in && out) {
        double worst = 0, lo = 9, hi = 0;
        for (int a = 0; a < 16; a++) {
            double ang = a * M_PI / 8.0;
            double ex = 1280 + 800 * cos(ang), ey = 800 + 600 * sin(ang), nx = cos(ang) / 800, ny = sin(ang) / 600, nl = hypot(nx, ny);
            nx /= nl;
            ny /= nl;
            int xi = (int) lround(ex - 4 * nx), yi = (int) lround(ey - 4 * ny), xo = (int) lround(ex + 4 * nx), yo = (int) lround(ey + 4 * ny);
            if (xi < 2 || xo < 2 || xi > W - 3 || xo > W - 3 || yi < 2 || yo < 2 || yi > H - 3 || yo > H - 3)
                continue;
            double pic = block_luma(in, xi - 1, xi + 2, yi - 1, yi + 2), glow = block_luma(out, xo - 1, xo + 2, yo - 1, yo + 2);
            double r = glow / (default_look.gain * pic);
            lo = fmin(lo, r);
            hi = fmax(hi, r);
            worst = fmax(worst, fabs(1.0 - r));
        }
        printf("  ellipse: glow just outside / (gain x picture just inside), 16 angles: %.2f to %.2f\n", lo, hi);
        tu_check(lo > 0.55 && hi < 1.25, "an elliptical picture is continued at every angle (ratio %.2f to %.2f, expected about 1 up to the 4 px step and the grid)", lo, hi);
        snprintf(path, sizeof(path), "%s/surround-ellipse.png", t->out);
        tu_png_write_linear(path, out, W, H, 1.0f);
    }
    free(in);
    free(out);

    // 4:3 picture, leftover left and right
    struct tc_shot four3 = { .rect = { 347, 0, 2213, 1400 }, .left = { 0.3f, 0.5f, 0.2f }, .right = { 0.8f, 0.3f, 0.3f }, .shade = 0.3f, .texture = 0.1f };
    four3.rect[3] = 1400;
    pl_dispatch_reset_frame(env->dp);
    tc_gen_shot(env, canvas, &four3, 0);
    fr.reset = true;
    pl_dispatch_reset_frame(env->dp);
    fp_surround_apply(s, canvas, filled, &default_look, &steady_time, &fr);
    out = tu_read_f32(env, filled, W, H);
    if (out) {
        snprintf(path, sizeof(path), "%s/surround-4x3.png", t->out);
        tu_png_write_linear(path, out, W, H, 1.0f);
        double lft = block_luma(out, 300, 340, 600, 800), rgt = block_luma(out, 2220, 2260, 600, 800);
        tu_check(lft > 0.01 && rgt > 0.01, "4:3 picture: both side bands are lit (%.3f and %.3f)", lft, rgt);
        free(out);
    }
}

static void a_state(struct tctx *t, pl_tex canvas, pl_tex filled, struct fp_surround *s)
{
    const struct fp_env *env = &t->dev.env;
    printf("-- A4: the persistent state\n");
    struct tc_shot shot = { .rect = { 0, RECT_TOP, W, RECT_BOTTOM }, .left = { 0.5f, 0.2f, 0.1f }, .right = { 0.5f, 0.2f, 0.1f } };
    pl_dispatch_reset_frame(env->dp);
    tc_gen_shot(env, canvas, &shot, 0);
    struct fp_surround_frame fr = { .dt_s = 1.0 / 240, .reset = true };
    pl_dispatch_reset_frame(env->dp);
    fp_surround_apply(s, canvas, filled, &default_look, &steady_time, &fr);
    int gw = 0, gh = 0;
    float *st = malloc((size_t) 4 * 128 * 128 * sizeof(float));
    if (fp_surround_read_state(s, st, &gw, &gh)) {
        // cell row 8 holds the picture's top edge: pixels 264..287 of its 256..287 are covered (24 of 32 rows)
        int cell = t->cell_px > 0 ? t->cell_px : 32;
        int row = RECT_TOP / cell;
        double cov = (double) ((row + 1) * cell - RECT_TOP) / cell;
        const float *c = &st[4 * ((size_t) row * gw + 10)], *interior = &st[4 * ((size_t) (row + 4) * gw + 10)], *leftover = &st[4 * ((size_t) (row - 4) * gw + 10)];
        tu_check(fabs(c[3] - cov) < 1e-5 && fabs(c[0] - 0.5 * cov) < 1e-4 && fabs(c[1] - 0.2 * cov) < 1e-4,
                 "the edge cell holds the covered share (%.3f, want %.3f) and its alpha-weighted colour (%.4f, want %.4f)", c[3], cov, c[0], 0.5 * cov);
        tu_check(fabs(interior[3] - 1.0) < 1e-6 && fabs(interior[0] - 0.5) < 1e-6, "an interior cell holds the picture colour (%.4f) with coverage 1", interior[0]);
        tu_check(leftover[3] == 0.0f && leftover[0] == 0.0f, "a leftover cell holds nothing");
    } else {
        tu_check(false, "state read back");
    }
    free(st);

    // The follower with a slow time constant must keep moving: a half-float state would stall (its steps get smaller
    // than its resolution). tau 20 s, 10 s of 240 Hz refreshes.
    struct tc_shot a = shot, b = shot;
    b.left[0] = b.right[0] = 0.9f;
    const struct fp_surround_time slow = { .tau_steady_s = 20.0f, .cut_rule = FP_CUT_SNAP };
    pl_dispatch_reset_frame(env->dp);
    tc_gen_shot(env, canvas, &a, 0);
    fr.reset = true;
    pl_dispatch_reset_frame(env->dp);
    fp_surround_update(s, canvas, &default_look, &slow, &fr);
    pl_dispatch_reset_frame(env->dp);
    tc_gen_shot(env, canvas, &b, 0);
    fr.reset = false;
    for (int k = 0; k < 2400; k++) {
        pl_dispatch_reset_frame(env->dp);
        fp_surround_update(s, canvas, &default_look, &slow, &fr);
    }
    st = malloc((size_t) 4 * 128 * 128 * sizeof(float));
    if (fp_surround_read_state(s, st, &gw, &gh)) {
        const float *interior = &st[4 * ((size_t) 20 * gw + 10)];
        double want = 0.9 - (0.9 - 0.5) * exp(-10.0 / 20.0);
        tu_check(fabs(interior[0] - want) < 1e-4, "tau 20 s after 10 s: state %.5f, exp(-t/tau) says %.5f (a half-float state would stall)", interior[0], want);
    }
    free(st);
}

// ---- temporal -----------------------------------------------------------------------------------------------------

static const struct tl_shot ten_cuts[] = {
    { 1.0f, { 0.85f, 0.80f, 0.70f }, { 0.80f, 0.82f, 0.75f } },   // bright, warm
    { 0.3f, { 0.015f, 0.015f, 0.02f }, { 0.02f, 0.015f, 0.015f } }, // dark
    { 2.0f, { 0.75f, 0.03f, 0.02f }, { 0.65f, 0.05f, 0.03f } },   // saturated red
    { 0.5f, { 0.70f, 0.78f, 0.90f }, { 0.82f, 0.85f, 0.88f } },   // bright, cool
    { 3.0f, { 0.02f, 0.02f, 0.025f }, { 0.015f, 0.02f, 0.02f } }, // dark
    { 0.4f, { 0.03f, 0.06f, 0.80f }, { 0.04f, 0.05f, 0.70f } },   // saturated blue
    { 1.5f, { 0.80f, 0.75f, 0.65f }, { 0.85f, 0.80f, 0.70f } },   // bright
    { 0.3f, { 0.04f, 0.70f, 0.06f }, { 0.05f, 0.65f, 0.08f } },   // saturated green
    { 2.5f, { 0.01f, 0.01f, 0.015f }, { 0.015f, 0.01f, 0.01f } }, // dark
    { 0.6f, { 0.70f, 0.05f, 0.65f }, { 0.65f, 0.06f, 0.60f } },   // saturated magenta
    { 1.2f, { 0.88f, 0.86f, 0.80f }, { 0.84f, 0.85f, 0.82f } },   // bright
};
static const struct tl_shot one_long[] = { { 4.0f, { 0.35f, 0.30f, 0.22f }, { 0.30f, 0.32f, 0.28f } } };
static const struct tl_shot one_mid[] = { { 3.0f, { 0.20f, 0.18f, 0.15f }, { 0.18f, 0.20f, 0.16f } } };
static const struct tl_shot dissolve_pair[] = {
    { 2.0f, { 0.85f, 0.80f, 0.70f }, { 0.80f, 0.82f, 0.75f } },
    { 2.0f, { 0.02f, 0.02f, 0.03f }, { 0.02f, 0.03f, 0.02f } },
};

static const struct tl_scenario scenarios[] = {
    { "ten-cuts", ten_cuts, (int) TU_ARRAY_SIZE(ten_cuts), 0.02f, 0, 0, 0.0 },
    { "flash", one_long, 1, 0.02f, 36, 2, 0.0 },
    { "grain", one_long, 1, 0.05f, 0, 0, 0.0 },
    { "dissolve", dissolve_pair, 2, 0.02f, 0, 0, 1.0, 0 },
    { "strobe12", one_mid, 1, 0.02f, 0, 0, 0.0, 1 }, // the picture's edge flickers 12 times a second (1x / 3x brightness)
    { "strobe4", one_mid, 1, 0.02f, 0, 0, 0.0, 3 },  // ... and 4 times a second
};

#define TIME(rule, tau, rate, taub, win, hold) { .tau_steady_s = tau, .max_rate = rate, .cut_rule = rule, .tau_blend_s = taub, .blend_window_s = win, .hold_s = hold }
// look: linear-light state (LIN) or a roughly perceptual one (PER, state exponent 0.45)
#define LIN { FP_SURROUND_GLOW, 0.8f, 250, 1, 1, false, 1.0f }
#define PER { FP_SURROUND_GLOW, 0.8f, 250, 1, 1, false, 0.45f }
static const struct tl_cfg cfgs[] = {
    { "ref", LIN, TIME(FP_CUT_SNAP, 0, 0, 0, 0, 0), false },
    { "snap-t0.1", LIN, TIME(FP_CUT_SNAP, 0.1f, 0, 0, 0, 0), false },
    { "snap-t0.25", LIN, TIME(FP_CUT_SNAP, 0.25f, 0, 0, 0, 0), false },
    { "snap-t0.5", LIN, TIME(FP_CUT_SNAP, 0.5f, 0, 0, 0, 0), false },
    { "snap-t1", LIN, TIME(FP_CUT_SNAP, 1.0f, 0, 0, 0, 0), false },
    { "snap-t2", LIN, TIME(FP_CUT_SNAP, 2.0f, 0, 0, 0, 0), false },
    { "snap-t0.25-rate1", LIN, TIME(FP_CUT_SNAP, 0.25f, 1.0f, 0, 0, 0), false },
    { "snap-t0.5-rate0.5", LIN, TIME(FP_CUT_SNAP, 0.5f, 0.5f, 0, 0, 0), false },
    { "snap-t0.5-per", PER, TIME(FP_CUT_SNAP, 0.5f, 0, 0, 0, 0), false },
    { "snap-t1-per", PER, TIME(FP_CUT_SNAP, 1.0f, 0, 0, 0, 0), false },
    { "snap-t0.5-rate0.5-per", PER, TIME(FP_CUT_SNAP, 0.5f, 0.5f, 0, 0, 0), false },
    { "blend-b0.04", LIN, TIME(FP_CUT_BLEND, 0.5f, 0, 0.04f, 0.2f, 0), false },
    { "blend-b0.08", LIN, TIME(FP_CUT_BLEND, 0.5f, 0, 0.08f, 0.4f, 0), false },
    { "blend-b0.16", LIN, TIME(FP_CUT_BLEND, 0.5f, 0, 0.16f, 0.8f, 0), false },
    { "blend-b0.32", LIN, TIME(FP_CUT_BLEND, 0.5f, 0, 0.32f, 1.6f, 0), false },
    { "blend-b0.32-per", PER, TIME(FP_CUT_BLEND, 0.5f, 0, 0.32f, 1.6f, 0), false },
    { "blend-b1-per", PER, TIME(FP_CUT_BLEND, 1.0f, 0, 1.0f, 4.0f, 0), false },
    { "hold-h0.3", LIN, TIME(FP_CUT_HOLD, 0.5f, 0, 0, 0, 0.3f), false },
    { "hold-h0.6", LIN, TIME(FP_CUT_HOLD, 0.5f, 0, 0, 0, 0.6f), false },
    { "hold-h1.0", LIN, TIME(FP_CUT_HOLD, 0.5f, 0, 0, 0, 1.0f), false },
    { "hold-h2.0", LIN, TIME(FP_CUT_HOLD, 0.5f, 0, 0, 0, 2.0f), false },
    { "hold-h0.6-t1-per", PER, TIME(FP_CUT_HOLD, 1.0f, 0, 0, 0, 0.6f), false },
    { "hold-h1.0-t1-per", PER, TIME(FP_CUT_HOLD, 1.0f, 0, 0, 0, 1.0f), false },
    { "hold-h1.0-t2-per", PER, TIME(FP_CUT_HOLD, 2.0f, 0, 0, 0, 1.0f), false },
    { "black", { FP_SURROUND_BLACK, 0.8f, 250, 1, 1, false, 1.0f }, TIME(FP_CUT_SNAP, 0.5f, 0, 0, 0, 0), false },
};

static const struct tl_cfg *find_cfg(const char *name)
{
    for (size_t i = 0; i < TU_ARRAY_SIZE(cfgs); i++)
        if (!strcmp(cfgs[i].name, name))
            return &cfgs[i];
    fprintf(stderr, "finish-test: no surround config named %s\n", name);
    abort();
}

// The configs run by default for each scenario; `--only full` runs every config on every scenario.
static const char *const default_ten[] = { "ref", "snap-t0.5", "snap-t0.5-per", "snap-t1", "snap-t0.25-rate1", "blend-b0.08",
                                           "blend-b0.32", "hold-h0.6", "hold-h1.0", "hold-h1.0-t1-per", "hold-h1.0-t2-per", "black" };
static const char *const default_flash[] = { "ref", "snap-t0.5", "snap-t0.5-rate0.5", "snap-t0.5-rate0.5-per", "snap-t1-per", "blend-b0.08" };
static const char *const default_grain[] = { "ref", "snap-t0.25", "snap-t1" };
static const char *const default_dissolve[] = { "ref", "snap-t0.5", "snap-t0.5-per" };
static const char *const default_strobe[] = { "ref", "snap-t0.1", "snap-t0.25", "snap-t0.5", "snap-t0.5-per", "snap-t1", "blend-b0.08", "hold-h0.6" };

static void run_group(struct tctx *t, const struct tl_scenario *sc, const char *const *which, int n, bool full)
{
    char path[512];
    int count = full ? (int) TU_ARRAY_SIZE(cfgs) : n;
    for (int i = 0; i < count; i++) {
        const struct tl_cfg *cfg = full ? &cfgs[i] : find_cfg(which[i]);
        // the flash scenario is also run with the planner marking the flash's first and last frame as cuts
        for (int variant = 0; variant < (sc->flash_frames > 0 ? 2 : 1); variant++) {
            struct tl_cfg run = *cfg;
            run.flag_flash_as_cut = variant == 1;
            char name[96];
            snprintf(name, sizeof(name), "%s%s", cfg->name, variant ? "+flash-flagged" : "");
            run.name = name;
            snprintf(path, sizeof(path), "%s/%s__%s.csv", t->out, sc->name, name);
            double t0 = tu_now_ms(), total = 0;
            for (int j = 0; j < sc->n_shots; j++)
                total += sc->shots[j].len_s;
            bool ok = tl_run(t, sc, &run, path, NULL);
            tu_check(ok, "%s / %s: %d refreshes recorded in %.1f s", sc->name, name, (int) (240 * total), (tu_now_ms() - t0) / 1000.0);
        }
    }
}

static void b_temporal(struct tctx *t)
{
    printf("-- B: the cut rules over film sequences at 240 Hz refreshes (CSV for analyze-flicker.py)\n");
    bool full = t->only && !strcmp(t->only, "full");
    run_group(t, &scenarios[0], default_ten, (int) TU_ARRAY_SIZE(default_ten), full);
    run_group(t, &scenarios[1], default_flash, (int) TU_ARRAY_SIZE(default_flash), full);
    run_group(t, &scenarios[2], default_grain, (int) TU_ARRAY_SIZE(default_grain), full);
    run_group(t, &scenarios[3], default_dissolve, (int) TU_ARRAY_SIZE(default_dissolve), full);
    run_group(t, &scenarios[4], default_strobe, (int) TU_ARRAY_SIZE(default_strobe), full);
    run_group(t, &scenarios[5], default_strobe, (int) TU_ARRAY_SIZE(default_strobe), full);

    // Checks made here: the rules do what they say, measured on the rendered rows.
    struct tl_series ref = { 0 }, snap = { 0 }, hold = { 0 }, blk = { 0 };
    char path[512];
    snprintf(path, sizeof(path), "%s/_check-ref.csv", t->out);
    bool ok = tl_run(t, &scenarios[0], find_cfg("ref"), path, &ref);
    snprintf(path, sizeof(path), "%s/_check-snap.csv", t->out);
    ok = ok && tl_run(t, &scenarios[0], find_cfg("snap-t0.5"), path, &snap);
    snprintf(path, sizeof(path), "%s/_check-hold.csv", t->out);
    ok = ok && tl_run(t, &scenarios[0], find_cfg("hold-h1.0"), path, &hold);
    snprintf(path, sizeof(path), "%s/_check-black.csv", t->out);
    ok = ok && tl_run(t, &scenarios[0], find_cfg("black"), path, &blk);
    tu_check(ok, "check runs recorded");
    if (ok) {
        // shot starts (in refreshes): cumulative lengths at 240 Hz
        int starts[TU_ARRAY_SIZE(ten_cuts)];
        double acc = 0;
        for (size_t i = 0; i < TU_ARRAY_SIZE(ten_cuts); i++) {
            starts[i] = (int) ceil(acc * 240.0 - 1e-6); // the first refresh whose time is at or after the shot's start
            acc += (double) ten_cuts[i].len_s;
        }
        double worst_snap = 0, worst_hold = 0, worst_follow = 0;
        for (size_t i = 1; i < TU_ARRAY_SIZE(ten_cuts); i++) {
            int k = starts[i];
            // snap: on the cut's own refresh the surround equals the exact (follow-every-frame) glow
            worst_snap = fmax(worst_snap, fabs(snap.top_near[k] - ref.top_near[k]) / fmax(ref.top_near[k], 1e-4));
            // hold 1.0 s: during the first 0.2 s after the cut (and while the shot lasts) nothing moves
            int end = k + (int) fmin(ten_cuts[i].len_s * 240.0, 48.0);
            for (int j = k + 1; j < end; j++) {
                double rel = fabs(hold.top_near[j] - hold.top_near[k - 1]) / fmax(hold.top_near[k - 1], 1e-4);
                if (rel > worst_hold && t->only && !strcmp(t->only, "debug-hold"))
                    printf("    cut %zu at refresh %d: refresh %d moved %.5f (base %.6f, now %.6f)\n", i, k, j, rel,
                           hold.top_near[k - 1], hold.top_near[j]);
                worst_hold = fmax(worst_hold, rel);
            }
        }
        double lo = 9, hi = 0;
        for (int k = 0; k < ref.n; k++)
            if (ref.pic[k] > 0.05) {
                double r = ref.top_near[k] / ref.pic[k];
                lo = fmin(lo, r);
                hi = fmax(hi, r);
            }
        worst_follow = hi / lo;
        double blk_max = 0;
        for (int k = 0; k < blk.n; k++)
            blk_max = fmax(blk_max, blk.top_near[k]);
        tu_check(worst_snap < 0.02, "snap: on a cut's own refresh the surround is the exact glow of the new shot (worst difference %.4f)", worst_snap);
        tu_check(worst_hold < 0.01, "hold 1.0 s: the surround does not move for 0.2 s after a cut (worst relative change %.5f)", worst_hold);
        tu_check(worst_follow < 1.4, "follow-every-frame glow is the picture's edge brightness times a constant (ratio spread %.2f)", worst_follow);
        tu_check(blk_max == 0.0, "black mode: the leftover stays at luminance 0 through every cut");
    }
    tl_series_free(&ref);
    tl_series_free(&snap);
    tl_series_free(&hold);
    tl_series_free(&blk);
}

// ---- entry --------------------------------------------------------------------------------------------------------

int test_surround(struct tctx *t)
{
    printf("== surround (pass 2)\n");
    const struct fp_env *env = &t->dev.env;
    pl_tex canvas = tu_canvas(env, W, H, "canvas"), filled = tu_canvas(env, W, H, "filled");
    struct fp_surround *s = fp_surround_create(env, W, H, t->cell_px);
    tu_check(canvas && filled && s, "textures and surround created (cell %d px)", t->cell_px ? t->cell_px : 32);
    if (!canvas || !filled || !s)
        return 1;
    bool only_time = t->only && (!strcmp(t->only, "time") || !strcmp(t->only, "full") || !strcmp(t->only, "debug-hold"));
    if (!only_time) {
        a_flat(t, canvas, filled, s);
        a_black_and_empty(t, canvas, filled, s);
        a_shapes(t, canvas, filled, s);
        a_state(t, canvas, filled, s);
    }
    fp_surround_destroy(&s);
    b_temporal(t);
    pl_tex_destroy(env->gpu, &canvas);
    pl_tex_destroy(env->gpu, &filled);
    return 0;
}
