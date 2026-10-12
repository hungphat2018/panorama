// Structure S2: prepared frames and our own resample pass, see pp-s2.h.
#include "pp-s2.h"

#include <math.h>
#include <stdio.h>
#include <string.h>

#include <libplacebo/filters.h>
#include <libplacebo/shaders/custom.h>

#include "pp-geometry.h"
#include "pp-util.h"

#define LUT_N 1024
#define JINC_ZERO3 3.2383154841662362076499
#define JINC_ZERO4 4.2410628637960698819573
#define JINC_FIRST_ZERO 1.2196698912665045

// ---- kernels: radial weight functions of the kernel distance, tabulated for the shader --------------------

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

// Same definitions as pp_resample.make_kernel (libplacebo's pl_filter_sample: the blur scales the whole kernel, window included).
bool pp_kernel_weight(const char *name, double t, double *w, double *support)
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
    if (!strcmp(name, "ewa_hermite")) {              // libplacebo's downscaling default, as a radial kernel of radius 1
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

// libplacebo's built-in anti-ringing strength of a filter preset (ewa_lanczos4sharpest carries 0.8, the others none).
double pp_kernel_antiring(const char *name)
{
    return !strcmp(name, "ewa_lanczos4sharpest") ? 0.8 : 0.0;
}

static bool make_lut(struct pp_s2 *s2, const char *name)
{
    double support;
    if (!pp_kernel_weight(name, 0.0, NULL, &support)) {
        fprintf(stderr, "picture-path: unknown kernel '%s'\n", name);
        return false;
    }
    float *w = pp_xcalloc(LUT_N, sizeof(float));
    for (int i = 0; i < LUT_N; i++) {
        double v;
        pp_kernel_weight(name, support * i / (LUT_N - 1), &v, &support);
        w[i] = (float) v;
    }
    // The first zero of the kernel, where its main lobe ends and where libplacebo's anti-ringing stops collecting
    // neighbours (it takes the first point at or below its 1e-3 cutoff, then the secant root).
    float first_zero = (float) support;
    for (int i = 1; i < LUT_N; i++) {
        if (w[i] <= 1e-3f * w[0]) {
            float t0 = (float) (support * (i - 1) / (LUT_N - 1)), t1 = (float) (support * i / (LUT_N - 1));
            first_zero = t1 - w[i] * (t1 - t0) / (w[i] - w[i - 1]);
            break;
        }
    }
    pl_fmt fmt = pp_find_fmt(s2->g, "r32f");
    if (!fmt) {
        fprintf(stderr, "picture-path: no r32f format for the kernel table\n");
        free(w);
        return false;
    }
    pl_tex_destroy(s2->g->gpu, &s2->lut);
    s2->lut = pl_tex_create(s2->g->gpu, pl_tex_params(.w = LUT_N, .h = 1, .format = fmt, .sampleable = true,
                                                      .host_writable = true, .debug_tag = PL_DEBUG_TAG));
    bool ok = s2->lut && pl_tex_upload(s2->g->gpu, pl_tex_transfer_params(.tex = s2->lut, .ptr = w));
    free(w);
    if (!ok) {
        fprintf(stderr, "picture-path: uploading the kernel table failed\n");
        return false;
    }
    s2->support = (float) support;
    s2->first_zero = first_zero;
    snprintf(s2->kernel_name, sizeof(s2->kernel_name), "%s", name);
    return true;
}

// ---- init / destroy -----------------------------------------------------------------------------------------

static char *read_shader(const char *dir, const char *file)
{
    char path[512];
    snprintf(path, sizeof(path), "%s/%s", dir, file);
    return pp_read_file(path, NULL);
}

bool pp_s2_init(struct pp_s2 *s2, const struct pp_gpu *g, struct pp_timing *tm, const char *shader_dir,
                const char *kernel_name, const char *chroma_filter)
{
    memset(s2, 0, sizeof(*s2));
    s2->g = g;
    s2->tm = tm;
    s2->dp = pl_dispatch_create(g->log, g->gpu);
    s2->rr = pl_renderer_create(g->log, g->gpu);
    if (!s2->dp || !s2->rr)
        return false;
    s2->glsl_common = read_shader(shader_dir, "pp-common.glsl");
    s2->glsl_resample = read_shader(shader_dir, "pp-resample.glsl");

    // Preparing a frame is a 1:1 conversion: no scaler, no sigmoid, no dither, no peak detection, no deband.
    s2->prep_params = *pl_render_params(.info_callback = pp_timing_render_cb, .info_priv = tm);
    if (chroma_filter && *chroma_filter) {
        const struct pl_filter_config *f = pl_find_filter_config(chroma_filter, PL_FILTER_UPSCALING);
        if (!f) {
            fprintf(stderr, "picture-path: unknown chroma filter '%s'\n", chroma_filter);
            return false;
        }
        s2->prep_params.plane_upscaler = f;
        s2->prep_params.plane_downscaler = f;
    }
    return make_lut(s2, kernel_name);
}

void pp_s2_destroy(struct pp_s2 *s2)
{
    if (!s2->g)
        return;
    pl_tex_destroy(s2->g->gpu, &s2->lut);
    for (int i = 0; i < PP_S2_SLOTS; i++)
        pl_timer_destroy(s2->g->gpu, &s2->timer[i]);
    pl_renderer_destroy(&s2->rr);
    pl_dispatch_destroy(&s2->dp);
    free(s2->glsl_common);
    free(s2->glsl_resample);
    memset(s2, 0, sizeof(*s2));
}

void pp_s2_begin_frame(struct pp_s2 *s2)
{
    pl_dispatch_reset_frame(s2->dp);
}

// ---- preparing a frame --------------------------------------------------------------------------------------

static bool ensure_canvas(struct pp_s2 *s2, pl_tex *t, int w, int h, const char *what)
{
    if (*t && (*t)->params.w == w && (*t)->params.h == h)
        return true;
    pl_tex_destroy(s2->g->gpu, t);
    *t = pp_tex_create_canvas(s2->g, w, h, what);
    return *t != NULL;
}

static bool dispatch_color(struct pp_s2 *s2, const struct pl_custom_shader *cs, pl_tex target, int slot, int mon)
{
    if (!s2->timer[slot])
        s2->timer[slot] = pl_timer_create(s2->g->gpu);
    pl_shader sh = pl_dispatch_begin(s2->dp);
    if (!pl_shader_custom(sh, cs)) {
        fprintf(stderr, "picture-path: pl_shader_custom failed (%s)\n", cs->description);
        pl_dispatch_abort(s2->dp, &sh);
        return false;
    }
    if (!pl_dispatch_finish(s2->dp, pl_dispatch_params(.shader = &sh, .target = target, .timer = s2->timer[slot]))) {
        fprintf(stderr, "picture-path: dispatching '%s' failed\n", cs->description);
        return false;
    }
    s2->timer_pending[slot] = true;
    s2->timer_mon[slot] = mon;
    snprintf(s2->timer_desc[slot], sizeof(s2->timer_desc[slot]), "%s", cs->description);
    return true;
}

void pp_s2_collect_timers(struct pp_s2 *s2)
{
    for (int i = 0; i < PP_S2_SLOTS; i++) {
        if (!s2->timer_pending[i])
            continue;
        uint64_t ns = pl_timer_query(s2->g->gpu, s2->timer[i]);
        if (!ns)
            continue;
        pp_timing_add(s2->tm, s2->timer_desc[i], ns, s2->timer_mon[i]);
        s2->timer_pending[i] = false;
    }
}

static struct pl_shader_desc tex_desc(const char *name, pl_tex tex)
{
    return (struct pl_shader_desc) {
        .desc = { .name = name, .type = PL_DESC_SAMPLED_TEX },
        .binding = { .object = tex, .address_mode = PL_TEX_ADDRESS_CLAMP, .sample_mode = PL_TEX_SAMPLE_NEAREST },
    };
}

bool pp_s2_prepare(struct pp_s2 *s2, const struct pp_source *src, struct pp_prepared *out, bool want_sigmoid, int which)
{
    if (!ensure_canvas(s2, &out->lin, src->w, src->h, "prepared frame"))
        return false;
    out->w = src->w;
    out->h = src->h;
    struct pl_frame tgt, img = src->frame;
    pp_canvas_frame(&tgt, out->lin);
    if (!pl_render_image(s2->rr, &img, &tgt, &s2->prep_params)) {
        fprintf(stderr, "picture-path: preparing the frame failed\n");
        return false;
    }
    out->has_sig = false;
    if (!want_sigmoid)
        return true;
    if (!ensure_canvas(s2, &out->sig, src->w, src->h, "sigmoid copy"))
        return false;
    struct pl_shader_desc d = tex_desc("u_src_a", out->lin);
    struct pl_custom_shader cs = {
        .header = s2->glsl_common,
        .body = "color = vec4(pp_sigmoid(texelFetch(u_src_a, ivec2(gl_FragCoord.xy), 0).rgb), 1.0);",
        .input = PL_SHADER_SIG_NONE, .output = PL_SHADER_SIG_COLOR,
        .descriptors = &d, .num_descriptors = 1,
        .compute = true, .compute_group_size = { 16, 16 },
        .description = "pp-prepare sigmoid copy",
    };
    out->has_sig = dispatch_color(s2, &cs, out->sig, PP_S2_SLOT_SIGMOID + which, 0);
    return out->has_sig;
}

void pp_prepared_destroy(struct pp_s2 *s2, struct pp_prepared *p)
{
    pl_tex_destroy(s2->g->gpu, &p->lin);
    pl_tex_destroy(s2->g->gpu, &p->sig);
    memset(p, 0, sizeof(*p));
}

// ---- the resample pass --------------------------------------------------------------------------------------

bool pp_s2_resample(struct pp_s2 *s2, const struct pp_s2_job *job)
{
    const struct pp_scenario *scn = job->scn;
    const struct pp_room *room = &scn->room;
    const struct pp_monitor *m = &room->mon[job->k];
    const struct pp_picture *pic = job->pic;
    const bool curve = scn->surface.model == PP_SURFACE_CURVE;

    float u_mon[4] = { (float) m->w, (float) m->h, (float) m->w_mm, (float) m->h_mm };
    float u_cen[4] = { (float) m->centre.x, (float) m->centre.y, (float) m->centre.z,
                       (float) (room->u0[job->k] - room->u_anchor) };
    float u_ex[4] = { (float) m->ex.x, (float) m->ex.y, (float) m->ex.z, curve ? 1.0f : 0.0f };
    float u_ey[4] = { (float) m->ey.x, (float) m->ey.y, (float) m->ey.z, (float) scn->surface.curvature };
    float u_pic[4] = { (float) pic->u_left, (float) pic->v_top, (float) pic->width, (float) pic->height };
    float u_crop[4] = { (float) pic->crop[0], (float) pic->crop[1], (float) pic->crop[2], (float) pic->crop[3] };
    float u_slide[4] = { 0, 0, 0, 0 };
    if (scn->has_slide)
        u_slide[0] = (float) scn->slide.vu, u_slide[1] = (float) scn->slide.vv, u_slide[2] = (float) scn->slide.t_ref_ms,
        u_slide[3] = (float) (scn->slide.t_top_ms[job->k] + job->extra_slide_ms);
    float u_misc[4] = { (float) room->seat_distance, (float) m->scan_ms, s2->support, job->debug_map ? 1.0f : 0.0f };
    bool blend = job->b && scn->has_blend[job->k];
    float u_blend[4] = { (float) scn->blend_wa[job->k], (float) scn->blend_wb[job->k], blend ? 1.0f : 0.0f,
                         job->sigmoid ? 1.0f : 0.0f };
    double sx, sy, jj[4] = {0};
    pp_source_jacobian(room, job->k, pic, &scn->surface, scn->has_slide ? &scn->slide : NULL, m->w / 2.0, m->h / 2.0,
                       &sx, &sy, jj);
    float u_jfold[4] = { (float) jj[0], (float) jj[1], (float) jj[2], (float) jj[3] };
    // the scenario's anti-ringing strength; a filter preset that carries its own (ewa_lanczos4sharpest) keeps it, as in libplacebo
    double ar = scn->antiring > 0 ? scn->antiring : pp_kernel_antiring(s2->kernel_name);
    float u_filt[4] = { (float) ar, s2->first_zero, 0, 0 };

    #define VAR(name, arr) { .var = pl_var_vec4(name), .data = arr, .dynamic = true }
    struct pl_shader_var vars[] = {
        VAR("u_mon", u_mon), VAR("u_cen", u_cen), VAR("u_ex", u_ex), VAR("u_ey", u_ey), VAR("u_pic", u_pic),
        VAR("u_crop", u_crop), VAR("u_slide", u_slide), VAR("u_misc", u_misc), VAR("u_blend", u_blend),
        VAR("u_jfold", u_jfold), VAR("u_filt", u_filt),
    };
    #undef VAR
    pl_tex ta = job->sigmoid ? job->a->sig : job->a->lin;
    pl_tex tb = blend ? (job->sigmoid ? job->b->sig : job->b->lin) : NULL;
    if (!ta || (blend && !tb)) {
        fprintf(stderr, "picture-path: the prepared frame lacks the sigmoid copy this monitor needs\n");
        return false;
    }
    // libplacebo refuses one texture bound twice, so u_src_b exists only when the pass blends.
    struct pl_shader_desc descs[3] = { tex_desc("u_src_a", ta), tex_desc("u_lut", s2->lut) };
    int ndesc = 2;
    if (blend)
        descs[ndesc++] = tex_desc("u_src_b", tb);

    size_t n = strlen(s2->glsl_common) + strlen(s2->glsl_resample) + 64;
    char *header = pp_xcalloc(n, 1);
    snprintf(header, n, "%s%s\n%s", blend ? "#define PP_BLEND 1\n" : "", s2->glsl_common, s2->glsl_resample);
    struct pl_custom_shader cs = {
        .header = header,
        .body = "color = pp_main();",
        .input = PL_SHADER_SIG_NONE, .output = PL_SHADER_SIG_COLOR,
        .variables = vars, .num_variables = PP_ARRAY_SIZE(vars),
        .descriptors = descs, .num_descriptors = ndesc,
        .compute = true, .compute_group_size = { 16, 16 },
        .description = "pp-resample",
    };
    bool ok = dispatch_color(s2, &cs, job->canvas, PP_S2_SLOT_RESAMPLE + job->k, job->k);
    free(header);
    return ok;
}

bool pp_s2_blend_canvases(struct pp_s2 *s2, pl_tex a, pl_tex b, pl_tex out, double wa, double wb, int mon)
{
    float u_blend[4] = { (float) wa, (float) wb, 1.0f, 0.0f };
    struct pl_shader_var var = { .var = pl_var_vec4("u_blend"), .data = u_blend, .dynamic = true };
    struct pl_shader_desc descs[2] = { tex_desc("u_src_a", a), tex_desc("u_src_b", b) };
    struct pl_custom_shader cs = {
        .body = "float w = clamp(u_blend.x + u_blend.y * gl_FragCoord.y, 0.0, 1.0);\n"
                "color = mix(texelFetch(u_src_a, ivec2(gl_FragCoord.xy), 0), texelFetch(u_src_b, ivec2(gl_FragCoord.xy), 0), w);",
        .input = PL_SHADER_SIG_NONE, .output = PL_SHADER_SIG_COLOR,
        .variables = &var, .num_variables = 1,
        .descriptors = descs, .num_descriptors = 2,
        .compute = true, .compute_group_size = { 16, 16 },
        .description = "pp-blend rows",
    };
    return dispatch_color(s2, &cs, out, PP_S2_SLOT_BLEND + mon, mon);
}

// The hook variant of S1 sees the picture as gamma-coded RGB inside libplacebo's pipeline; this pass makes the linear (or
// sigmoid) copy that the resample pass wants, once per refresh and monitor.
bool pp_s2_linearize(struct pp_s2 *s2, pl_tex src, pl_tex dst, bool sigmoid, int mon)
{
    float u_dom[4] = { sigmoid ? 1.0f : 0.0f, 0, 0, 0 };
    struct pl_shader_var var = { .var = pl_var_vec4("u_dom"), .data = u_dom, .dynamic = true };
    struct pl_shader_desc d = tex_desc("u_src_a", src);
    struct pl_custom_shader cs = {
        .header = s2->glsl_common,
        .body = "vec3 l = pp_srgb_to_linear(texelFetch(u_src_a, ivec2(gl_FragCoord.xy), 0).rgb);\n"
                "color = vec4(u_dom.x > 0.5 ? pp_sigmoid(l) : l, 1.0);",
        .input = PL_SHADER_SIG_NONE, .output = PL_SHADER_SIG_COLOR,
        .variables = &var, .num_variables = 1,
        .descriptors = &d, .num_descriptors = 1,
        .compute = true, .compute_group_size = { 16, 16 },
        .description = "pp-prepare linearize (hook)",
    };
    return dispatch_color(s2, &cs, dst, PP_S2_SLOT_SIGMOID + (mon & 1), mon);
}
