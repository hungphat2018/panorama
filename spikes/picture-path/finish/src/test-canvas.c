// Synthetic canvases for the finish tests, see test-canvas.h.
#include "test-canvas.h"

#include "finish-internal.h"

// Making the test picture is not part of any pass's cost: no timer.
static bool dispatch_untimed(const struct fp_env *env, pl_shader *sh, pl_tex target)
{
    return pl_dispatch_finish(env->dp, pl_dispatch_params(.shader = sh, .target = target));
}

static const char shot_body[] =
    "vec2 p = gl_FragCoord.xy;                                                                      \n"
    "vec2 e = (p - 0.5 * (tc_rect.xy + tc_rect.zw)) / (0.5 * (tc_rect.zw - tc_rect.xy));             \n"
    "bool inside = tc_ellipse > 0.5 ? dot(e, e) < 1.0                                                \n"
    "    : (p.x >= tc_rect.x && p.x < tc_rect.z && p.y >= tc_rect.y && p.y < tc_rect.w);             \n"
    "vec2 uv = clamp((p - tc_rect.xy) / (tc_rect.zw - tc_rect.xy), 0.0, 1.0);                        \n"
    "vec3 c = mix(tc_left, tc_right, uv.x);                                                          \n"
    "c *= 1.0 + tc_shade * (0.5 - uv.y);                                                             \n"
    "c *= 1.0 + tc_texture * sin(uv.x * 40.0) * sin(uv.y * 17.0);                                    \n"
    "uint hx = uint(p.x) * 1597334677u ^ uint(p.y) * 3812015801u ^ uint(tc_seed) * 2654435761u;       \n"
    "hx ^= hx >> 16; hx *= 0x7feb352du; hx ^= hx >> 15; hx *= 0x846ca68bu; hx ^= hx >> 16;           \n"
    "float n = float(hx) / 4294967295.0 - 0.5;                                                       \n"
    "c *= 1.0 + tc_grain * 2.0 * n;                                                                  \n"
    "c = mix(c, vec3(1.0), tc_flash);                                                                \n"
    "color = inside ? vec4(max(c, vec3(0.0)), 1.0) : vec4(0.0);                                      \n";

bool tc_gen_shot(const struct fp_env *env, pl_tex canvas, const struct tc_shot *shot, int frame_index)
{
    float rect[4], left[3], right[3], shade = shot->shade, grain = shot->grain, flash = shot->flash;
    float texture = shot->texture, ellipse = shot->ellipse ? 1.0f : 0.0f;
    int seed = frame_index;
    memcpy(rect, shot->rect, sizeof(rect));
    memcpy(left, shot->left, sizeof(left));
    memcpy(right, shot->right, sizeof(right));
    struct pl_shader_var vars[] = {
        { .var = pl_var_vec4("tc_rect"), .data = rect, .dynamic = true },
        fp_var_v3("tc_left", left), fp_var_v3("tc_right", right), fp_var_f("tc_shade", &shade),
        fp_var_f("tc_grain", &grain), fp_var_f("tc_flash", &flash), fp_var_i("tc_seed", &seed),
        fp_var_f("tc_texture", &texture), fp_var_f("tc_ellipse", &ellipse),
    };
    pl_shader sh = pl_dispatch_begin(env->dp);
    bool ok = pl_shader_custom(sh, &(struct pl_custom_shader) {
        .description = "test shot", .body = shot_body, .input = PL_SHADER_SIG_NONE, .output = PL_SHADER_SIG_COLOR,
        .variables = vars, .num_variables = 9,
    });
    ok = ok && dispatch_untimed(env, &sh, canvas);
    if (!ok)
        pl_dispatch_abort(env->dp, &sh);
    return ok;
}

static const char ramp_body[] =
    "float x = (gl_FragCoord.x - 0.5) / max(tc_w - 1.0, 1.0);                                  \n"
    "float s = mix(tc_lo, tc_hi, clamp(x, 0.0, 1.0));                                          \n"
    "float lin = s <= 0.04045 ? s / 12.92 : pow((s + 0.055) / 1.055, 2.4);                     \n"
    "color = vec4(vec3(lin), 1.0);                                                             \n";

bool tc_gen_ramp(const struct fp_env *env, pl_tex canvas, float signal_lo, float signal_hi)
{
    float w = (float) canvas->params.w;
    struct pl_shader_var vars[] = { fp_var_f("tc_w", &w), fp_var_f("tc_lo", &signal_lo), fp_var_f("tc_hi", &signal_hi) };
    pl_shader sh = pl_dispatch_begin(env->dp);
    bool ok = pl_shader_custom(sh, &(struct pl_custom_shader) {
        .description = "test ramp", .body = ramp_body, .input = PL_SHADER_SIG_NONE, .output = PL_SHADER_SIG_COLOR,
        .variables = vars, .num_variables = 3,
    });
    ok = ok && dispatch_untimed(env, &sh, canvas);
    if (!ok)
        pl_dispatch_abort(env->dp, &sh);
    return ok;
}

bool tc_gen_flat(const struct fp_env *env, pl_tex canvas, const float rgb[3])
{
    float c[3] = { rgb[0], rgb[1], rgb[2] };
    struct pl_shader_var vars[] = { fp_var_v3("tc_c", c) };
    pl_shader sh = pl_dispatch_begin(env->dp);
    bool ok = pl_shader_custom(sh, &(struct pl_custom_shader) {
        .description = "test flat", .body = "color = vec4(tc_c, 1.0);", .input = PL_SHADER_SIG_NONE,
        .output = PL_SHADER_SIG_COLOR, .variables = vars, .num_variables = 1,
    });
    ok = ok && dispatch_untimed(env, &sh, canvas);
    if (!ok)
        pl_dispatch_abort(env->dp, &sh);
    return ok;
}
