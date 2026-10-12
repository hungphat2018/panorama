// Pass 3: the monitor's colour correction in linear light over picture and surround together, then encode to the
// monitor's transfer and dither to its bit depth. The maths (and the CPU reference of the shader) are in
// finish-color-math.c; this file is the shader and the passes. See finish-passes.h and the report.
//
// The dither runs on the encoded value, after everything that changes values, and it sees the surround too: this is why
// pass 3 is one pass over picture and surround together.
#include "perf1-finish-internal.h"

struct fp_finish {
    const struct fp_env *env;
    pl_shader_obj dither_state;
};

static const char finish_body[] =
    "vec3 v = max(fin_m * color.rgb, vec3(0.0));                                           \n"
    "v = fin_lift + (fin_gain - fin_lift) * pow(v, fin_gamma);                                \n"
    "v = clamp(v, 0.0, 1.0);                                                              \n"
    "if (fin_trc == 0) {                                                                   \n"
    "    v = mix(v * 12.92, 1.055 * pow(v, vec3(1.0 / 2.4)) - 0.055, lessThanEqual(vec3(0.0031308), v)); \n"
    "} else {                                                                             \n"
    "    v = pow(v, vec3(fin_inv_power));                                                  \n"
    "}                                                                                    \n"
    "color = vec4(v, 1.0);                                                                \n";

struct fp_finish *fp_finish_create(const struct fp_env *env)
{
    struct fp_finish *f = calloc(1, sizeof(*f));
    if (f)
        f->env = env;
    return f;
}

void fp_finish_destroy(struct fp_finish **fp)
{
    struct fp_finish *f = *fp;
    if (!f)
        return;
    pl_shader_obj_destroy(&f->dither_state);
    free(f);
    *fp = NULL;
}

bool fp_finish_append(struct fp_finish *f, pl_shader sh, const struct fp_color_gpu *c, enum fp_dither dither)
{
    float m9[9];
    int trc = c->transfer == FP_TRC_SRGB ? 0 : 1;
    float inv_power = 1.0f / c->power;
    struct pl_shader_var vars[] = {
        fp_var_m3_rowmajor("fin_m", c->m, m9), fp_var_v3("fin_gain", c->gain), fp_var_v3("fin_gamma", c->gamma),
        fp_var_v3("fin_lift", c->lift), fp_var_i("fin_trc", &trc), fp_var_f("fin_inv_power", &inv_power),
    };
    bool ok = pl_shader_custom(sh, &(struct pl_custom_shader) {
        .description = "finish: correct and encode", .body = finish_body,
        .input = PL_SHADER_SIG_COLOR, .output = PL_SHADER_SIG_COLOR,
        .variables = vars, .num_variables = 6,
    });
    if (!ok)
        return false;
    switch (dither) {
    case FP_DITHER_NONE:
        break;
    case FP_DITHER_BLUE_NOISE:
        pl_shader_dither(sh, c->out_bits, &f->dither_state, pl_dither_params(.method = PL_DITHER_BLUE_NOISE));
        break;
    case FP_DITHER_ORDERED:
        pl_shader_dither(sh, c->out_bits, &f->dither_state, pl_dither_params(.method = PL_DITHER_ORDERED_FIXED));
        break;
    case FP_DITHER_WHITE:
        pl_shader_dither(sh, c->out_bits, &f->dither_state, pl_dither_params(.method = PL_DITHER_WHITE_NOISE));
        break;
    }
    return !pl_shader_is_failed(sh);
}

bool fp_finish_run(struct fp_finish *f, pl_tex in, pl_tex out, const struct fp_color_gpu *c, enum fp_dither dither)
{
    const struct fp_env *env = f->env;
    pl_shader sh = pl_dispatch_begin(env->dp);
    // Leftover pixels (alpha < 1) become black, as in the surround's black mode.
    bool ok = fp_append_canvas_fetch(sh, in) &&
              pl_shader_custom(sh, &(struct pl_custom_shader) {
                  .description = "over black", .body = "color = vec4(color.rgb * clamp(color.a, 0.0, 1.0), 1.0);",
                  .input = PL_SHADER_SIG_COLOR, .output = PL_SHADER_SIG_COLOR,
              }) &&
              fp_finish_append(f, sh, c, dither);
    if (!ok || !fp_dispatch(env, &sh, out)) {
        pl_dispatch_abort(env->dp, &sh);
        return false;
    }
    return true;
}

bool fp_finish_run_fused(struct fp_finish *f, struct fp_surround *s, pl_tex canvas, pl_tex out,
                         const struct fp_surround_look *look, const struct fp_color_gpu *c, enum fp_dither dither)
{
    const struct fp_env *env = f->env;
    pl_shader sh = pl_dispatch_begin(env->dp);
    bool ok = fp_append_canvas_fetch(sh, canvas) && fp_surround_append_fill(s, sh, look) &&
              fp_finish_append(f, sh, c, dither);
    if (!ok || !fp_dispatch(env, &sh, out)) {
        pl_dispatch_abort(env->dp, &sh);
        return false;
    }
    return true;
}

bool fp_finish_error_diffusion(struct fp_finish *f, pl_tex in_float, pl_tex out, int bits)
{
    const struct fp_env *env = f->env;
    pl_shader sh = pl_dispatch_begin(env->dp);
    bool ok = pl_shader_error_diffusion(sh, pl_error_diffusion_params(
        .input_tex = in_float, .output_tex = out, .new_depth = bits, .kernel = &pl_error_diffusion_sierra_lite));
    if (!ok) {
        pl_dispatch_abort(env->dp, &sh);
        return false;
    }
    return pl_dispatch_compute(env->dp, pl_dispatch_compute_params(.shader = &sh, .dispatch_size = { 1, 1, 1 },
                                                                    .timer = env->timer));
}
