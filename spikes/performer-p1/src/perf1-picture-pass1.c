// Pass 1 of the picture path, see perf1-picture-pass1.h. Ported from the picture-path harness (pp-s2.c).
#define _USE_MATH_DEFINES
#include "perf1-picture-pass1.h"

#include "perf1-picture-shader.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <libplacebo/shaders/custom.h>

#define LUT_N 1024
#define JINC_ZERO3 3.2383154841662362076499
#define JINC_ZERO4 4.2410628637960698819573
#define JINC_FIRST_ZERO 1.2196698912665045

// ---- kernels: radial weight functions of the kernel distance, tabulated for the shader ---------------------------

static double jinc(double x)
{
    if (x < 1e-8)
        return 1.0;
    x *= M_PI;
    return 2.0 * _j1(x) / x;
}

static double cubic_bc(double x, double b, double c)
{
    x = fabs(x);
    if (x < 1.0)
        return ((12 - 9 * b - 6 * c) * x * x * x + (-18 + 12 * b + 6 * c) * x * x + (6 - 2 * b)) / 6.0;
    if (x < 2.0)
        return ((-b - 6 * c) * x * x * x + (6 * b + 30 * c) * x * x + (-12 * b - 48 * c) * x + (8 * b + 24 * c)) / 6.0;
    return 0.0;
}

// libplacebo's pl_filter_sample: the blur scales the whole kernel, window included.
bool pic_kernel_weight(const char *name, double t, double *w, double *support)
{
    double blur = 1.0, r = JINC_ZERO3;
    if (!strcmp(name, "ewa_lanczossharp") || !strcmp(name, "ewa_lanczos") || !strcmp(name, "ewa_lanczos4sharpest")) {
        if (!strcmp(name, "ewa_lanczossharp"))
            blur = 0.98125058372237073562493;
        if (!strcmp(name, "ewa_lanczos4sharpest")) {
            blur = 0.88451209326050047745788;
            r = JINC_ZERO4;
        }
        *support = r * blur;
        if (w)
            *w = jinc(t / blur) * jinc(t / (blur * r) * JINC_FIRST_ZERO);
        return true;
    }
    if (!strcmp(name, "ewa_hermite")) {
        *support = 1.0;
        if (w)
            *w = t < 1.0 ? 1.0 - 3.0 * t * t + 2.0 * t * t * t : 0.0;
        return true;
    }
    *support = 2.0;
    if (!strcmp(name, "ewa_robidoux")) {
        double b = 12.0 / (19.0 + 9.0 * sqrt(2.0));
        if (w)
            *w = cubic_bc(t, b, (1.0 - b) / 2.0);
        return true;
    }
    if (!strcmp(name, "ewa_catmull_rom")) {
        if (w)
            *w = cubic_bc(t, 0.0, 0.5);
        return true;
    }
    if (!strcmp(name, "ewa_mitchell")) {
        if (w)
            *w = cubic_bc(t, 1.0 / 3.0, 1.0 / 3.0);
        return true;
    }
    return false;
}

static bool make_lut(struct pic_pass1 *p, const char *name)
{
    double support;
    if (!pic_kernel_weight(name, 0.0, NULL, &support)) {
        fprintf(stderr, "perf1: unknown kernel '%s'\n", name);
        return false;
    }
    float *w = calloc(LUT_N, sizeof(float));
    pl_fmt fmt = pl_find_named_fmt(p->gpu, "r32f");
    if (!w || !fmt) {
        fprintf(stderr, "perf1: no r32f format for the kernel table\n");
        free(w);
        return false;
    }
    for (int i = 0; i < LUT_N; i++) {
        double v;
        pic_kernel_weight(name, support * i / (LUT_N - 1), &v, &support);
        w[i] = (float) v;
    }
    // The first zero of the kernel, where its main lobe ends and where the anti-ringing stops collecting neighbours
    // (libplacebo takes the first point at or below its 1e-3 cutoff, then the secant root).
    float first_zero = (float) support;
    for (int i = 1; i < LUT_N; i++) {
        if (w[i] <= 1e-3f * w[0]) {
            float t0 = (float) (support * (i - 1) / (LUT_N - 1)), t1 = (float) (support * i / (LUT_N - 1));
            first_zero = t1 - w[i] * (t1 - t0) / (w[i] - w[i - 1]);
            break;
        }
    }
    p->lut = pl_tex_create(p->gpu, pl_tex_params(.w = LUT_N, .h = 1, .format = fmt, .sampleable = true,
                                                  .host_writable = true, .debug_tag = PL_DEBUG_TAG));
    bool ok = p->lut && pl_tex_upload(p->gpu, pl_tex_transfer_params(.tex = p->lut, .ptr = w));
    free(w);
    if (!ok) {
        fprintf(stderr, "perf1: uploading the kernel table failed\n");
        return false;
    }
    p->support = (float) support;
    p->first_zero = first_zero;
    p->antiring = !strcmp(name, "ewa_lanczos4sharpest") ? 0.8f : 0.0f; // the preset's own strength, as in libplacebo
    snprintf(p->kernel, sizeof(p->kernel), "%s", name);
    return true;
}

// ---- life cycle ------------------------------------------------------------------------------------------------

bool pic_pass1_init(struct pic_pass1 *p, pl_gpu gpu, const char *kernel)
{
    memset(p, 0, sizeof(*p));
    p->gpu = gpu;
    pl_fmt f16 = pl_find_named_fmt(gpu, "rgba16f");
    if (!f16) {
        fprintf(stderr, "perf1: the GPU has no rgba16f format\n");
        return false;
    }
    p->dummy = pl_tex_create(gpu, pl_tex_params(.w = 1, .h = 1, .format = f16, .sampleable = true, .host_writable = true,
                                                .debug_tag = PL_DEBUG_TAG));
    size_t n = strlen(pic_glsl_common) + strlen(pic_glsl_geometry) + strlen(pic_glsl_resample) + 3;
    p->header = malloc(n);
    if (!p->dummy || !p->header)
        return false;
    snprintf(p->header, n, "%s\n%s\n%s", pic_glsl_common, pic_glsl_geometry, pic_glsl_resample);
    return make_lut(p, kernel);
}

void pic_pass1_destroy(struct pic_pass1 *p)
{
    if (!p->gpu)
        return;
    pl_tex_destroy(p->gpu, &p->lut);
    pl_tex_destroy(p->gpu, &p->dummy);
    free(p->header);
    memset(p, 0, sizeof(*p));
}

// ---- the pass ---------------------------------------------------------------------------------------------------

static struct pl_shader_desc tex_desc(const char *name, pl_tex tex)
{
    return (struct pl_shader_desc) {
        .desc = { .name = name, .type = PL_DESC_SAMPLED_TEX },
        .binding = { .object = tex, .address_mode = PL_TEX_ADDRESS_CLAMP, .sample_mode = PL_TEX_SAMPLE_NEAREST },
    };
}

// The uniforms of one view of the picture.
struct view_vars {
    float pic[4], crop[4], slide[4], jfold[4];
};

static void fill_view(struct view_vars *o, const struct room_geom *g, const struct pic_view *v, const struct pic_slide *sl)
{
    for (int i = 0; i < 4; i++) {
        o->pic[i] = (float) v->rect[i];
        o->crop[i] = (float) v->crop[i];
    }
    o->slide[0] = sl ? (float) sl->du0 : 0.0f;
    o->slide[1] = sl ? (float) sl->dv0 : 0.0f;
    o->slide[2] = sl ? (float) sl->ddu : 0.0f;
    o->slide[3] = sl ? (float) sl->ddv : 0.0f;
    double j[4];
    pic_jacobian(g, v, sl, g->px_w / 2.0, g->px_h / 2.0, j); // constant for the fold; the curve measures its own
    for (int i = 0; i < 4; i++)
        o->jfold[i] = (float) j[i];
}

bool pic_pass1_run(struct pic_pass1 *p, const struct pic_pass1_job *job)
{
    const struct room_geom *g = job->geom;
    float u_mon[4] = { (float) g->px_w, (float) g->px_h, (float) g->w_mm, (float) g->h_mm };
    float u_cen[4] = { (float) g->centre.x, (float) g->centre.y, (float) g->centre.z, (float) g->left_mm };
    float u_ex[4] = { (float) g->ex.x, (float) g->ex.y, (float) g->ex.z, g->curve ? 1.0f : 0.0f };
    float u_ey[4] = { (float) g->ey.x, (float) g->ey.y, (float) g->ey.z, (float) g->curvature };
    float u_srf[4] = { (float) g->top_mm, (float) g->u_axis, (float) g->v_eye, (float) g->seat_distance };
    struct view_vars va, vb;
    fill_view(&va, g, job->view, job->slide);
    if (job->view_b)
        fill_view(&vb, g, job->view_b, job->slide_b);
    else
        vb = va;
    float u_blend[4] = { (float) job->wa, (float) job->wb, job->b ? 1.0f : 0.0f, job->sigmoid ? 1.0f : 0.0f };
    float u_filt[4] = { p->antiring, p->first_zero, p->support, job->sigmoid_b ? 1.0f : 0.0f };

    #define VAR(name, arr) { .var = pl_var_vec4(name), .data = arr, .dynamic = true }
    struct pl_shader_var vars[] = {
        VAR("u_mon", u_mon), VAR("u_cen", u_cen), VAR("u_ex", u_ex), VAR("u_ey", u_ey), VAR("u_srf", u_srf),
        VAR("u_pic", va.pic), VAR("u_crop", va.crop), VAR("u_slide", va.slide), VAR("u_jfold", va.jfold),
        VAR("u_pic_b", vb.pic), VAR("u_crop_b", vb.crop), VAR("u_slide_b", vb.slide), VAR("u_jfold_b", vb.jfold),
        VAR("u_blend", u_blend), VAR("u_filt", u_filt),
    };
    #undef VAR
    struct pl_shader_desc descs[3] = { tex_desc("u_src_a", job->a), tex_desc("u_src_b", job->b ? job->b : p->dummy),
                                       tex_desc("u_lut", p->lut) };
    struct pl_custom_shader cs = {
        .header = p->header,
        .body = "color = pic_main();",
        .input = PL_SHADER_SIG_NONE, .output = PL_SHADER_SIG_COLOR,
        .variables = vars, .num_variables = sizeof(vars) / sizeof(vars[0]),
        .descriptors = descs, .num_descriptors = 3,
        .compute = true, .compute_group_size = { 16, 16 },
        .description = "picture resample",
    };
    pl_shader sh = pl_dispatch_begin(job->dp);
    if (!pl_shader_custom(sh, &cs)) {
        fprintf(stderr, "perf1: pl_shader_custom failed (picture resample)\n");
        pl_dispatch_abort(job->dp, &sh);
        return false;
    }
    if (!pl_dispatch_finish(job->dp, pl_dispatch_params(.shader = &sh, .target = job->out, .timer = job->timer))) {
        fprintf(stderr, "perf1: dispatching the picture resample failed\n");
        return false;
    }
    return true;
}
