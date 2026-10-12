// Pass 3 tests: the shader against the CPU reference, the near-black ramp with and without dither, colour ownership,
// and the question whether libplacebo's own dither covers our pass.
#include "test-canvas.h"
#include "test-main.h"

// ---- helpers ------------------------------------------------------------------------------------------------------

static double srgb_eotf(double s)
{
    return s <= 0.04045 ? s / 12.92 : pow((s + 0.055) / 1.055, 2.4);
}

// Uploads linear RGBA floats into an rgba16f canvas and returns what the GPU actually stored (half-float rounded).
static float *upload_canvas(const struct fp_env *env, pl_tex canvas, const float *rgba, int w, int h)
{
    if (!pl_tex_upload(env->gpu, pl_tex_transfer_params(.tex = canvas, .ptr = (void *) rgba)))
        return NULL;
    return tu_read_f32(env, canvas, w, h);
}

// ---- C0: how this GPU converts a float to an 8-bit code ----------------------------------------------------------

// The conversion of a shader's float output to an 8-bit render target is only specified to within 0.6 of a code, so
// without dither "round to nearest" is not exact. Measured here: the value (as a fraction of a code) at which the
// output steps from k to k+1.
static void c0_rounding(struct tctx *t)
{
    const struct fp_env *env = &t->dev.env;
    const int W = 2048;
    pl_tex canvas = tu_canvas(env, W, 1, "c0 canvas"), out8 = tu_target8(env, W, 1, false, "c0 out");
    float *src = malloc((size_t) W * 4 * sizeof(float));
    if (!canvas || !out8 || !src) {
        tu_check(false, "c0 textures");
        return;
    }
    for (int i = 0; i < W; i++) {
        double k = i / 128, f = (i % 128) / 128.0; // codes 0..15, 128 steps per code
        float v = (float) ((k + f) / 255.0);
        src[4 * i] = src[4 * i + 1] = src[4 * i + 2] = v;
        src[4 * i + 3] = 1.0f;
    }
    float *stored = upload_canvas(env, canvas, src, W, 1);
    struct fp_color_monitor ident = { .transfer = FP_TRC_POWER, .power = 1.0, .gamut_owner = FP_GAMUT_OS };
    struct fp_color_gpu cg;
    fp_color_make(&ident, &cg);
    struct fp_finish *fin = fp_finish_create(env);
    pl_dispatch_reset_frame(env->dp);
    uint8_t *got = fp_finish_run(fin, canvas, out8, &cg, FP_DITHER_NONE) ? tu_read_u8(env, out8, W, 1) : NULL;
    if (got && stored) {
        double sum = 0, lo = 1, hi = 0;
        int n = 0;
        for (int k = 1; k < 15; k++) {
            for (int i = 0; i < W; i++) {
                double code = stored[4 * i] * 255.0;
                if (code > k - 0.5 && code < k + 1.5 && got[4 * i] == k + 1 && (i == 0 || got[4 * (i - 1)] == k)) {
                    double th = code - k;
                    sum += th;
                    lo = th < lo ? th : lo;
                    hi = th > hi ? th : hi;
                    n++;
                    break;
                }
            }
        }
        printf("  float to 8-bit conversion: the code steps up at %.3f of a code on average (range %.3f to %.3f), exact rounding is 0.500\n",
               n ? sum / n : 0.0, lo, hi);
        tu_check(n > 10 && lo > 0.4 && hi < 0.6, "the GPU's conversion is within 0.1 of a code of round-to-nearest (it is only specified to within 0.6)");
    } else {
        tu_check(false, "c0 ran");
    }
    free(got);
    free(stored);
    free(src);
    fp_finish_destroy(&fin);
    pl_tex_destroy(env->gpu, &canvas);
    pl_tex_destroy(env->gpu, &out8);
}

// ---- C1: the shader against the CPU reference ---------------------------------------------------------------------

static void c1_numeric(struct tctx *t, const char *name, const struct fp_color_monitor *mon, enum fp_dither dither)
{
    const struct fp_env *env = &t->dev.env;
    const int W = 512, H = 256;
    pl_tex canvas = tu_canvas(env, W, H, "c1 canvas"), out8 = tu_target8(env, W, H, false, "c1 out");
    float *src = malloc((size_t) W * H * 4 * sizeof(float));
    if (!canvas || !out8 || !src) {
        tu_check(false, "c1 %s: textures", name);
        return;
    }
    // log-spaced intensities from 1e-5 to 1.6 (above 1 = extended range), a colour wheel of channel mixes, some negatives
    unsigned rng = 12345;
    for (int i = 0; i < W * H; i++) {
        rng = rng * 1664525u + 1013904223u;
        double a = pow(10.0, -5.0 + 5.2 * ((rng >> 8) & 0xFFFF) / 65535.0);
        rng = rng * 1664525u + 1013904223u;
        double mixg = ((rng >> 8) & 0xFFFF) / 65535.0;
        rng = rng * 1664525u + 1013904223u;
        double mixb = ((rng >> 8) & 0xFFFF) / 65535.0;
        src[4 * i] = (float) a;
        src[4 * i + 1] = (float) (a * mixg);
        src[4 * i + 2] = (float) (a * mixb);
        src[4 * i + 3] = 1.0f;
        if (i % 97 == 0)
            src[4 * i] = -0.05f; // outside the working gamut
    }
    float *stored = upload_canvas(env, canvas, src, W, H);
    struct fp_color_gpu cg;
    fp_color_make(mon, &cg);
    struct fp_finish *fin = fp_finish_create(env);
    pl_dispatch_reset_frame(env->dp);
    bool ok = stored && fp_finish_run(fin, canvas, out8, &cg, dither);
    uint8_t *got = ok ? tu_read_u8(env, out8, W, H) : NULL;
    tu_check(got != NULL, "c1 %s: pass 3 ran", name);
    if (got) {
        int d1 = 0, d2 = 0, maxd = 0, outside = 0;
        double bias = 0;
        long nb = 0;
        for (int i = 0; i < W * H; i++) {
            double in[3] = { stored[4 * i], stored[4 * i + 1], stored[4 * i + 2] }, enc[3];
            fp_color_reference(&cg, in, enc);
            for (int c = 0; c < 3; c++) {
                double exact = enc[c] * 255.0;
                int want = (int) floor(exact + 0.5);
                int d = abs((int) got[4 * i + c] - want);
                maxd = d > maxd ? d : maxd;
                d1 += d == 1;
                d2 += d >= 2;
                // a dithered code is one of the two neighbours of the exact value (the shader quantises itself)
                int lo = (int) floor(exact + 1e-3);
                outside += got[4 * i + c] != lo && got[4 * i + c] != lo + 1 && got[4 * i + c] != (int) ceil(exact - 1e-3);
                if (exact > 2.0 && exact < 250.0) {
                    bias += got[4 * i + c] - exact;
                    nb++;
                }
            }
        }
        if (dither == FP_DITHER_NONE) {
            // The GPU's own float to 8-bit conversion is loose (section C0), so a code may be one off the exact rounding.
            tu_check(d2 == 0, "c1 %s: no dither: %d values are 1 code from the exactly rounded reference (the GPU's conversion), %d are 2 or more", name, d1, d2);
        } else {
            tu_check(outside == 0 && fabs(bias / nb) < 0.01,
                     "c1 %s: dithered codes are always a neighbour of the exact value (%d are not) and unbiased (mean error %+.4f code)",
                     name, outside, bias / nb);
        }
        free(got);
    }
    free(stored);
    free(src);
    fp_finish_destroy(&fin);
    pl_tex_destroy(env->gpu, &canvas);
    pl_tex_destroy(env->gpu, &out8);
}

// ---- C2: the near-black ramp --------------------------------------------------------------------------------------

struct ramp_stats {
    int distinct;         // distinct 8-bit codes in the ramp area
    double run_median;    // median run of equal codes along a row (pixels)
    int run_max;          // longest run
    int step_max;         // largest jump between neighbouring runs (codes)
    double lp_rms, lp_max, lp_p99; // low-pass error against the ideal ramp, in codes
    double lum_err_max_pct;        // worst luminance error of the low-passed ramp against the ideal (Weber, in %)
};

static int cmp_dbl(const void *a, const void *b)
{
    double x = *(const double *) a, y = *(const double *) b;
    return (x > y) - (x < y);
}

// `out`: W x H RGBA8 of the ramp (rows identical in the ideal). `ideal[x]`: the ideal code (float) for column x.
static struct ramp_stats ramp_measure(const uint8_t *out, int W, int H, const double *ideal)
{
    struct ramp_stats s = { 0 };
    bool seen[256] = { false };
    for (int y = 0; y < H; y++)
        for (int x = 0; x < W; x++)
            seen[out[4 * ((size_t) y * W + x)]] = true;
    for (int i = 0; i < 256; i++)
        s.distinct += seen[i];

    // runs along the middle row
    int y0 = H / 2;
    double *runs = malloc(sizeof(double) * (size_t) W);
    int nr = 0, run = 1;
    int prev_code = out[4 * ((size_t) y0 * W)];
    for (int x = 1; x < W; x++) {
        int c = out[4 * ((size_t) y0 * W + x)];
        if (c == prev_code) {
            run++;
        } else {
            runs[nr++] = run;
            if (run > s.run_max)
                s.run_max = run;
            if (abs(c - prev_code) > s.step_max)
                s.step_max = abs(c - prev_code);
            run = 1;
            prev_code = c;
        }
    }
    runs[nr++] = run;
    qsort(runs, (size_t) nr, sizeof(double), cmp_dbl);
    s.run_median = runs[nr / 2];
    free(runs);

    // low-pass: 2D Gaussian, sigma 3 px, on the codes; compared with the ideal ramp over the middle of the image
    const double sigma = 3.0;
    const int R = 9;
    double k[19];
    double ks = 0;
    for (int i = -R; i <= R; i++) {
        k[i + R] = exp(-0.5 * i * i / (sigma * sigma));
        ks += k[i + R];
    }
    for (int i = 0; i < 2 * R + 1; i++)
        k[i] /= ks;
    int rows = 0;
    double sumsq = 0, maxe = 0, maxlum = 0;
    double *errs = malloc(sizeof(double) * (size_t) W * 8);
    int ne = 0;
    for (int y = H / 2 - 4; y < H / 2 + 4; y++) {
        for (int x = 30; x < W - 30; x++) {
            double acc = 0;
            for (int j = -R; j <= R; j++)
                for (int i = -R; i <= R; i++)
                    acc += k[j + R] * k[i + R] * out[4 * ((size_t) (y + j) * W + (x + i))];
            double e = acc - ideal[x];
            sumsq += e * e;
            if (fabs(e) > maxe)
                maxe = fabs(e);
            errs[ne++] = fabs(e);
            if (ideal[x] > 1.0) {
                double lp = srgb_eotf(acc / 255.0), id = srgb_eotf(ideal[x] / 255.0);
                double w = fabs(lp - id) / id * 100.0;
                if (w > maxlum)
                    maxlum = w;
            }
        }
        rows++;
    }
    s.lp_rms = sqrt(sumsq / ne);
    s.lp_max = maxe;
    qsort(errs, (size_t) ne, sizeof(double), cmp_dbl);
    s.lp_p99 = errs[(int) (0.99 * (ne - 1))];
    s.lum_err_max_pct = maxlum;
    free(errs);
    (void) rows;
    return s;
}

static void ramp_print(const char *name, const struct ramp_stats *s)
{
    printf("  %-34s %3d levels  run median %5.1f px, max %4d px  biggest jump %d code  low-pass error rms %.3f max %.3f p99 %.3f  worst luminance error %.1f %%\n",
           name, s->distinct, s->run_median, s->run_max, s->step_max, s->lp_rms, s->lp_max, s->lp_p99, s->lum_err_max_pct);
}

static void c2_ramp(struct tctx *t)
{
    printf("-- C2: near-black ramp, signal 0 to 10 %% of sRGB, 2560 px wide, 8-bit out\n");
    const struct fp_env *env = &t->dev.env;
    const int W = 2560, H = 64;
    pl_tex canvas = tu_canvas(env, W, H, "ramp"), out8 = tu_target8(env, W, H, true, "ramp out");
    pl_fmt f32 = pl_find_named_fmt(env->gpu, "rgba32f");
    pl_tex enc_f32 = f32 ? pl_tex_create(env->gpu, pl_tex_params(.w = W, .h = H, .format = f32, .sampleable = true,
                                                                  .renderable = true, .host_readable = true)) : NULL;
    if (!canvas || !out8) {
        tu_check(false, "c2 textures");
        return;
    }
    pl_dispatch_reset_frame(env->dp);
    tu_check(tc_gen_ramp(env, canvas, 0.0f, 0.10f), "ramp canvas made");

    struct fp_finish *fin = fp_finish_create(env);
    struct fp_color_monitor none = { .transfer = FP_TRC_SRGB, .gamut_owner = FP_GAMUT_OS };
    // gain 0.8 and a gamma tweak of 1.15 on linear light (a typical "match the dimmer panel" setting)
    struct fp_color_monitor tweak = { .transfer = FP_TRC_SRGB, .gamut_owner = FP_GAMUT_OS,
                                      .gain = { 0.8f, 0.8f, 0.8f }, .gamma = { 1.15f, 1.15f, 1.15f } };
    struct fp_color_gpu c_none, c_tweak;
    fp_color_make(&none, &c_none);
    fp_color_make(&tweak, &c_tweak);

    double *ideal_none = malloc(sizeof(double) * W), *ideal_tweak = malloc(sizeof(double) * W);
    for (int x = 0; x < W; x++) {
        double sig = 0.10 * x / (W - 1), lin = srgb_eotf(sig), e[3], in[3] = { lin, lin, lin };
        fp_color_reference(&c_none, in, e);
        ideal_none[x] = e[0] * 255.0;
        fp_color_reference(&c_tweak, in, e);
        ideal_tweak[x] = e[0] * 255.0;
    }

    struct { const char *name; const struct fp_color_gpu *c; enum fp_dither d; const double *ideal; } cases[] = {
        { "uncorrected, no dither", &c_none, FP_DITHER_NONE, ideal_none },
        { "uncorrected, blue noise", &c_none, FP_DITHER_BLUE_NOISE, ideal_none },
        { "gain 0.8 + gamma 1.15, no dither", &c_tweak, FP_DITHER_NONE, ideal_tweak },
        { "gain 0.8 + gamma 1.15, blue noise", &c_tweak, FP_DITHER_BLUE_NOISE, ideal_tweak },
        { "gain 0.8 + gamma 1.15, ordered 16x16", &c_tweak, FP_DITHER_ORDERED, ideal_tweak },
        { "gain 0.8 + gamma 1.15, white noise", &c_tweak, FP_DITHER_WHITE, ideal_tweak },
    };
    struct ramp_stats res[TU_ARRAY_SIZE(cases) + 1];
    for (size_t i = 0; i < TU_ARRAY_SIZE(cases); i++) {
        pl_dispatch_reset_frame(env->dp);
        bool ok = fp_finish_run(fin, canvas, out8, cases[i].c, cases[i].d);
        uint8_t *u = ok ? tu_read_u8(env, out8, W, H) : NULL;
        tu_check(u != NULL, "c2 %s ran", cases[i].name);
        if (!u)
            continue;
        res[i] = ramp_measure(u, W, H, cases[i].ideal);
        ramp_print(cases[i].name, &res[i]);
        if (i == 2 || i == 3 || i == 0) {
            // an enlarged strip for a look: 16 rows tall, 4x magnified vertically is not needed; write the 64 rows as is
            char path[512];
            snprintf(path, sizeof(path), "%s/ramp-%zu.png", t->out, i);
            tu_png_write_rgba8(path, u, W, H);
        }
        free(u);
    }
    // error diffusion (libplacebo's compute shader, one work group), needs a float input and a storable 8-bit output
    if (enc_f32 && out8->params.storable) {
        pl_dispatch_reset_frame(env->dp);
        // pass 3 without quantising: encode into a float target, then let the error diffusion quantise
        bool ok = fp_finish_run(fin, canvas, enc_f32, &c_tweak, FP_DITHER_NONE);
        bool ed = ok && fp_finish_error_diffusion(fin, enc_f32, out8, 8);
        tu_check(ed, "c2 error diffusion ran (sierra lite)");
        uint8_t *u = ed ? tu_read_u8(env, out8, W, H) : NULL;
        if (u) {
            struct ramp_stats s = ramp_measure(u, W, H, ideal_tweak);
            ramp_print("gain 0.8 + gamma 1.15, error diffusion", &s);
            res[TU_ARRAY_SIZE(cases)] = s;
            free(u);
        }
    } else {
        printf("  (error diffusion skipped: needs rgba32f targets and a storable rgba8 on this device)\n");
    }

    // The checks that carry the phase criterion: dither removes the bands that correction creates or keeps.
    tu_check(res[2].run_median > 20.0, "without dither the corrected ramp has real bands (median run %.0f px)", res[2].run_median);
    tu_check(res[3].lp_p99 < 0.15, "with blue noise the low-pass error stays under 0.15 code (p99 %.3f; without dither %.3f)", res[3].lp_p99, res[2].lp_p99);
    tu_check(res[3].lp_p99 < res[2].lp_p99 * 0.5, "blue noise at least halves the low-pass error of the corrected ramp");
    free(ideal_none);
    free(ideal_tweak);
    fp_finish_destroy(&fin);
    pl_tex_destroy(env->gpu, &canvas);
    pl_tex_destroy(env->gpu, &out8);
    pl_tex_destroy(env->gpu, &enc_f32);
}

// ---- C3: colour ownership -----------------------------------------------------------------------------------------

static void print_matrix(const char *name, const struct fp_color_gpu *c)
{
    printf("  %-44s [%.4f %.4f %.4f | %.4f %.4f %.4f | %.4f %.4f %.4f]\n", name, c->m[0], c->m[1], c->m[2], c->m[3], c->m[4],
           c->m[5], c->m[6], c->m[7], c->m[8]);
}

static void c3_ownership(struct tctx *t)
{
    printf("-- C3: colour ownership per monitor\n");
    struct fp_color_monitor laptop = { .transfer = FP_TRC_SRGB, .gamut_owner = FP_GAMUT_OS };
    struct fp_color_monitor ext_ids = { .transfer = FP_TRC_POWER, .power = 2.2, .gamut_owner = FP_GAMUT_PLAYER,
        .panel = { .r = { 0.640, 0.330 }, .g = { 0.300, 0.600 }, .b = { 0.150, 0.060 }, .w = { 0.3127, 0.3290 } } };
    struct fp_color_monitor ext_off = ext_ids;
    ext_off.panel.r = (struct fp_xy) { 0.652, 0.333 };
    ext_off.panel.g = (struct fp_xy) { 0.307, 0.605 };
    ext_off.panel.b = (struct fp_xy) { 0.152, 0.064 };
    struct fp_color_monitor lap_if_player = { .transfer = FP_TRC_SRGB, .gamut_owner = FP_GAMUT_PLAYER,
        .panel = { .r = { 0.679, 0.316 }, .g = { 0.258, 0.693 }, .b = { 0.147, 0.052 }, .w = { 0.3127, 0.3290 } } };
    struct fp_color_gpu a, b, c, d;
    fp_color_make(&laptop, &a);
    fp_color_make(&ext_ids, &b);
    fp_color_make(&ext_off, &c);
    fp_color_make(&lap_if_player, &d);
    print_matrix("laptop, Windows owns the gamut (ACM on)", &a);
    print_matrix("external, player owns, EDID = sRGB primaries", &b);
    print_matrix("external, player owns, primaries 1-2 % off sRGB", &c);
    print_matrix("laptop if the player owned it (wrong: double)", &d);
    bool ident_a = true, ident_b = true;
    for (int i = 0; i < 9; i++) {
        ident_a &= fabsf(a.m[i] - (i % 4 == 0)) < 1e-6f;
        ident_b &= fabsf(b.m[i] - (i % 4 == 0)) < 2e-6f;
    }
    tu_check(ident_a, "ACM on: the player's gamut step is the identity (only relative tweaks)");
    tu_check(ident_b, "ACM off, panel measured as sRGB: the player's gamut step is the identity too");

    // And on the GPU: with the OS owning the gamut, a colour goes through unchanged except for the encode.
    c1_numeric(t, "ACM on: identity matrix + sRGB", &laptop, FP_DITHER_NONE);
    struct fp_color_monitor wide = lap_if_player;
    wide.gain[0] = 0.9f; wide.gain[1] = 0.85f; wide.gain[2] = 0.95f;
    wide.gamma[0] = 1.05f; wide.gamma[1] = 1.0f; wide.gamma[2] = 0.97f;
    wide.lift[0] = 0.004f; wide.lift[1] = 0.003f; wide.lift[2] = 0.005f;
    c1_numeric(t, "ACM off: EDID matrix + tweaks + sRGB", &wide, FP_DITHER_NONE);
    struct fp_color_monitor pow24 = ext_off;
    pow24.power = 2.4;
    pow24.gain[0] = 0.8f; pow24.gain[1] = 0.8f; pow24.gain[2] = 0.8f;
    c1_numeric(t, "ACM off: matrix + gain 0.8 + power 2.4", &pow24, FP_DITHER_NONE);
    c1_numeric(t, "ACM off, with blue-noise dither", &pow24, FP_DITHER_BLUE_NOISE);
}

// ---- C4: does libplacebo's own dither cover our pass? -----------------------------------------------------------

// One pl_render_image of `image_tex` (linear BT.709) into an sRGB 8-bit target. `declare_black`: say that black is black
// on both ends; otherwise libplacebo's defaults apply (a 1000:1 sRGB display, so the black point is compensated).
static bool render_with_libplacebo(struct tctx *t, pl_tex image_tex, pl_tex target_tex, pl_rect2df crop,
                                   enum pl_clear_mode border, bool declare_black)
{
    const struct fp_env *env = &t->dev.env;
    pl_renderer rr = pl_renderer_create(env->log, env->gpu);
    float min_luma = declare_black ? PL_COLOR_HDR_BLACK : 0.0f;
    struct pl_frame image = {
        .num_planes = 1,
        .planes = {{ .texture = image_tex, .components = 4,
                     .component_mapping = { PL_CHANNEL_R, PL_CHANNEL_G, PL_CHANNEL_B, PL_CHANNEL_A } }},
        .repr = { .sys = PL_COLOR_SYSTEM_RGB, .levels = PL_COLOR_LEVELS_FULL, .alpha = PL_ALPHA_NONE },
        .color = { .primaries = PL_COLOR_PRIM_BT_709, .transfer = PL_COLOR_TRC_LINEAR,
                   .hdr = { .min_luma = min_luma, .max_luma = PL_COLOR_SDR_WHITE } },
    };
    int depth = target_tex->params.format->component_depth[0];
    struct pl_frame target = {
        .num_planes = 1,
        .planes = {{ .texture = target_tex, .components = 4,
                     .component_mapping = { PL_CHANNEL_R, PL_CHANNEL_G, PL_CHANNEL_B, PL_CHANNEL_A } }},
        .repr = { .sys = PL_COLOR_SYSTEM_RGB, .levels = PL_COLOR_LEVELS_FULL, .alpha = PL_ALPHA_NONE,
                  .bits = { .sample_depth = depth, .color_depth = depth } },
        .color = { .primaries = PL_COLOR_PRIM_BT_709, .transfer = PL_COLOR_TRC_SRGB,
                   .hdr = { .min_luma = min_luma, .max_luma = PL_COLOR_SDR_WHITE } },
        .crop = crop,
    };
    struct pl_render_params params = pl_render_default_params;
    params.border = border;
    params.dither_params = &pl_dither_default_params;
    bool ok = pl_render_image(rr, &image, &target, &params);
    pl_renderer_destroy(&rr);
    return ok;
}

static void c4_dither_coverage(struct tctx *t)
{
    printf("-- C4: where libplacebo's own dither applies (pass 1's draw), and where it does not\n");
    const struct fp_env *env = &t->dev.env;
    const int W = 1280, H = 800, PH = 400;
    pl_tex canvas = tu_canvas(env, W, PH, "c4 image"), out8 = tu_target8(env, W, H, false, "c4 out");
    if (!canvas || !out8) {
        tu_check(false, "c4 textures");
        return;
    }
    pl_dispatch_reset_frame(env->dp);
    tc_gen_ramp(env, canvas, 0.0f, 0.10f);
    double *ideal = malloc(sizeof(double) * W);
    for (int x = 0; x < W; x++)
        ideal[x] = 255.0 * 0.10 * x / (W - 1); // the ramp is sRGB-encoded already, so the ideal code is the signal

    // The picture goes into the middle band of an 8-bit target, a blurred copy fills the border above and below, and
    // libplacebo's dither is on. Run twice: with libplacebo's defaults, and with "black is black" declared.
    for (int pass = 0; pass < 2; pass++) {
        bool declare = pass == 1;
        bool ok = render_with_libplacebo(t, canvas, out8, (pl_rect2df) { 0, 200, W, 600 }, PL_CLEAR_BLUR, declare);
        tu_check(ok, "libplacebo draws the ramp with a blurred border into an 8-bit target (%s)",
                 declare ? "black declared" : "defaults");
        uint8_t *u = ok ? tu_read_u8(env, out8, W, H) : NULL;
        if (!u)
            continue;
        // the picture band (rows 268..331) and the border (rows 68..131), measured with the same low-pass
        uint8_t *band = malloc((size_t) W * 64 * 4), *border = malloc((size_t) W * 64 * 4);
        for (int y = 0; y < 64; y++) {
            memcpy(band + (size_t) y * W * 4, u + ((size_t) (268 + y) * W) * 4, (size_t) W * 4);
            memcpy(border + (size_t) y * W * 4, u + ((size_t) (68 + y) * W) * 4, (size_t) W * 4);
        }
        struct ramp_stats sb = ramp_measure(band, W, 64, ideal), sr = ramp_measure(border, W, 64, ideal);
        char what[96];
        snprintf(what, sizeof(what), "picture band, %s", declare ? "black declared" : "libplacebo defaults");
        ramp_print(what, &sb);
        if (declare) {
            ramp_print("blurred border (same call)", &sr);
            tu_check(sb.lp_p99 < 0.1, "the picture area is dithered by libplacebo (low-pass p99 %.3f code)", sb.lp_p99);
            tu_check(sb.run_median < 3.0 && sr.run_median > 20.0,
                     "the blurred border is NOT dithered: its codes sit in bands of %.0f px, the picture's in runs of %.0f px",
                     sr.run_median, sb.run_median);
            char path[512];
            snprintf(path, sizeof(path), "%s/dither-coverage.png", t->out);
            tu_png_write_rgba8(path, u, W, H);
        } else {
            tu_check(sb.lp_p99 > 1.0, "with libplacebo's defaults the near-black ramp is moved by black point compensation (low-pass error p99 %.2f code)", sb.lp_p99);
        }
        free(band);
        free(border);
        free(u);
    }
    free(ideal);
    pl_tex_destroy(env->gpu, &canvas);
    pl_tex_destroy(env->gpu, &out8);
}

// ---- entry --------------------------------------------------------------------------------------------------------

int test_color(struct tctx *t)
{
    printf("== color (pass 3)\n");
    struct fp_color_monitor plain = { .transfer = FP_TRC_SRGB, .gamut_owner = FP_GAMUT_OS };
    printf("-- C0: the GPU's float to 8-bit conversion\n");
    c0_rounding(t);
    printf("-- C1: the shader against the double-precision reference\n");
    c1_numeric(t, "identity + sRGB, no dither", &plain, FP_DITHER_NONE);
    c2_ramp(t);
    c3_ownership(t);
    c4_dither_coverage(t);
    return 0;
}
