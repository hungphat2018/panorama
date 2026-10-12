// Pass 2: the surround. See finish-passes.h for the contract and the report for the measurements.
//
// Per refresh (all on the GPU, nothing is read back):
//   update   canvas -> grid state     one fragment per grid cell: alpha-weighted mean colour + coverage of the cell's
//                                     canvas pixels, then the temporal rule (the CPU picks alpha / rate limit)
//   edge     state -> edge            keeps only cells at the picture's edge (a cell with picture next to a cell
//                                     that is not all picture); the picture's inside never colours the surround
//   blur     edge -> level A, B       separable Gaussian at two scales (fine near the edge, coarse farther out)
//            state -> level C         the same blur of the plain coverage: it dims the glow with distance
//   fill     (per canvas pixel)       leftover pixels take the fine level where it has edge cells nearby, else the
//                                     coarse one, times gain and the falloff; the picture pixels pass through
// The fill is a shader fragment (fp_surround_append_fill) so it can be fused into pass 3's single pass over the canvas.
#include "finish-internal.h"

#define FP_DEFAULT_CELL 32
#define FP_SIGMA_FINE_CELLS 2.0f
#define FP_SIGMA_COARSE_CELLS 8.0f

struct fp_surround {
    const struct fp_env *env;
    int w, h, cell, gw, gh;
    pl_tex state[2];
    int cur;
    pl_tex edge, tmp, lvl[3]; // lvl[0] fine colours, lvl[1] coarse colours, lvl[2] coverage
    struct fp_surround_clock clock;
    bool ready;               // update() has run at least once
    char prelude[96];
};

// ---- shader text ----------------------------------------------------------------------------------------------

static const char update_body[] =
    "ivec2 c = ivec2(gl_FragCoord.xy);                                     \n"
    "ivec2 p0 = c * SR_CELL;                                               \n"
    "ivec2 p1 = min(p0 + ivec2(SR_CELL), sr_size);                         \n"
    "vec4 acc = vec4(0.0);                                                 \n"
    "for (int y = p0.y; y < p1.y; ++y) {                                   \n"
    "    for (int x = p0.x; x < p1.x; ++x) {                               \n"
    "        vec4 t = texelFetch(sr_canvas, ivec2(x, y), 0);               \n"
    "        float a = clamp(t.a, 0.0, 1.0);                               \n"
    "        vec3 rgb = sr_premult > 0.5 ? (a > 1.0e-4 ? t.rgb / a : vec3(0.0)) : t.rgb; \n"
    "        rgb = pow(clamp(rgb, 0.0, sr_maxlevel), vec3(sr_gamma));      \n"
    "        acc += vec4(rgb * a, a);                                      \n"
    "    }                                                                 \n"
    "}                                                                     \n"
    "vec4 target = acc / float((p1.x - p0.x) * (p1.y - p0.y));             \n"
    "vec4 prev = texelFetch(sr_prev, c, 0);                                \n"
    "vec4 lim = vec4(sr_maxdelta);                                         \n"
    "vec4 moved = prev + clamp(sr_alpha * (target - prev), -lim, lim);     \n"
    "color = (sr_alpha >= 1.0 && sr_maxdelta >= 1.0e8) ? target : moved;   \n";

static const char edge_body[] =
    "ivec2 c = ivec2(gl_FragCoord.xy);                                      \n"
    "ivec2 gs = textureSize(sr_state, 0);                                   \n"
    "vec4 s = texelFetch(sr_state, c, 0);                                   \n"
    "float wmin = s.w;                                                      \n"
    "for (int dy = -1; dy <= 1; ++dy) {                                     \n"
    "    for (int dx = -1; dx <= 1; ++dx) {                                 \n"
    "        ivec2 n = clamp(c + ivec2(dx, dy), ivec2(0), gs - 1);          \n"
    "        wmin = min(wmin, texelFetch(sr_state, n, 0).w);                \n"
    "    }                                                                  \n"
    "}                                                                      \n"
    "color = (s.w > 0.02 && wmin < 0.98) ? s : vec4(0.0);                   \n";

static const char blur_body[] =
    "ivec2 c = ivec2(gl_FragCoord.xy);                                      \n"
    "ivec2 gs = textureSize(sr_src, 0);                                     \n"
    "int r = int(ceil(3.0 * sr_sigma));                                     \n"
    "float k = -0.5 / (sr_sigma * sr_sigma);                                \n"
    "vec4 acc = vec4(0.0);                                                  \n"
    "float ws = 0.0;                                                        \n"
    "for (int i = -r; i <= r; ++i) {                                        \n"
    "    float w = exp(k * float(i * i));                                   \n"
    "    acc += w * texelFetch(sr_src, clamp(c + sr_dir * i, ivec2(0), gs - 1), 0); \n"
    "    ws += w;                                                           \n"
    "}                                                                      \n"
    "color = acc / ws;                                                      \n";

static const char fill_body[] =
    "float a = clamp(color.a, 0.0, 1.0);                                    \n"
    "vec3 pic = color.rgb;                                                  \n"
    "vec3 fill = vec3(0.0);                                                 \n"
    "if (sr_glow > 0.5 && a < 0.9995) {                                     \n"
    "    vec2 gsz = vec2(textureSize(sr_lvl_a, 0));                         \n"
    "    vec2 tc = (gl_FragCoord.xy / float(SR_CELL)) / gsz;                \n"
    "    vec4 va = textureLod(sr_lvl_a, tc, 0.0);                           \n"
    "    vec4 vb = textureLod(sr_lvl_b, tc, 0.0);                           \n"
    "    float cov = textureLod(sr_lvl_c, tc, 0.0).w;                       \n"
    "    vec3 ca = va.rgb / max(va.w, 1.0e-5);                              \n"
    "    vec3 cb = vb.rgb / max(vb.w, 1.0e-5);                              \n"
    "    vec3 g = pow(mix(cb, ca, smoothstep(0.04, 0.2, va.w)), vec3(sr_inv_gamma)); \n"
    "    float luma = dot(g, vec3(0.2126, 0.7152, 0.0722));                 \n"
    "    g = mix(vec3(luma), g, sr_sat);                                    \n"
    "    fill = min(g * (sr_gain * clamp(2.0 * cov, 0.0, 1.0)), vec3(sr_maxlevel)); \n"
    "}                                                                      \n"
    "color.rgb = mix(pic * a, pic, sr_premult) + fill * (1.0 - a);          \n"
    "color.a = 1.0;                                                         \n";

// ---- life cycle -----------------------------------------------------------------------------------------------

struct fp_surround *fp_surround_create(const struct fp_env *env, int canvas_w, int canvas_h, int cell_px)
{
    struct fp_surround *s = calloc(1, sizeof(*s));
    if (!s)
        return NULL;
    s->env = env;
    s->w = canvas_w;
    s->h = canvas_h;
    s->cell = cell_px > 0 ? cell_px : FP_DEFAULT_CELL;
    s->gw = (canvas_w + s->cell - 1) / s->cell;
    s->gh = (canvas_h + s->cell - 1) / s->cell;
    snprintf(s->prelude, sizeof(s->prelude), "#define SR_CELL %d\n", s->cell);

    // The state needs 32-bit floats: a slow follower adds a tiny step each refresh, and half floats would stall it.
    static const char *const f32[] = { "rgba32f" };
    static const char *const f16f32[] = { "rgba16f", "rgba32f" };
    pl_fmt fstate = fp_find_fmt(env->gpu, f32, 1, PL_FMT_CAP_SAMPLEABLE | PL_FMT_CAP_RENDERABLE | PL_FMT_CAP_HOST_READABLE);
    pl_fmt flvl = fp_find_fmt(env->gpu, f16f32, 2, PL_FMT_CAP_LINEAR | PL_FMT_CAP_RENDERABLE);
    if (!fstate || !flvl) {
        FP_ERR(env, "surround: the GPU has no rgba32f render target or no filterable float format");
        free(s);
        return NULL;
    }
    bool ok = true;
    for (int i = 0; i < 2; i++)
        ok &= (s->state[i] = fp_make_target(env, s->gw, s->gh, fstate, false, "surround state")) != NULL;
    ok &= (s->edge = fp_make_target(env, s->gw, s->gh, flvl, false, "surround edge")) != NULL;
    ok &= (s->tmp = fp_make_target(env, s->gw, s->gh, flvl, false, "surround blur scratch")) != NULL;
    for (int i = 0; i < 3; i++)
        ok &= (s->lvl[i] = fp_make_target(env, s->gw, s->gh, flvl, false, "surround level")) != NULL;
    if (!ok) {
        fp_surround_destroy(&s);
        return NULL;
    }
    for (int i = 0; i < 2; i++)
        pl_tex_clear(env->gpu, s->state[i], (float[4]) {0});
    fp_surround_clock_reset(&s->clock);
    return s;
}

void fp_surround_destroy(struct fp_surround **sp)
{
    struct fp_surround *s = *sp;
    if (!s)
        return;
    for (int i = 0; i < 2; i++)
        pl_tex_destroy(s->env->gpu, &s->state[i]);
    pl_tex_destroy(s->env->gpu, &s->edge);
    pl_tex_destroy(s->env->gpu, &s->tmp);
    for (int i = 0; i < 3; i++)
        pl_tex_destroy(s->env->gpu, &s->lvl[i]);
    free(s);
    *sp = NULL;
}

bool fp_surround_read_state(struct fp_surround *s, float *rgbw, int *grid_w, int *grid_h)
{
    *grid_w = s->gw;
    *grid_h = s->gh;
    return pl_tex_download(s->env->gpu, pl_tex_transfer_params(.tex = s->state[s->cur], .ptr = rgbw));
}

// ---- the small passes -----------------------------------------------------------------------------------------

static bool pass_update(struct fp_surround *s, pl_tex canvas, const struct fp_surround_look *look,
                        struct fp_surround_step st)
{
    const struct fp_env *env = s->env;
    int size[2] = { s->w, s->h };
    float alpha = st.alpha, maxdelta = st.max_delta, maxlevel = look->max_level > 0 ? look->max_level : 1.0f;
    float premult = look->premultiplied_alpha ? 1.0f : 0.0f;
    float gamma = look->state_gamma > 0 ? look->state_gamma : 1.0f;
    struct pl_shader_var vars[] = {
        fp_var_iv2("sr_size", size), fp_var_f("sr_alpha", &alpha), fp_var_f("sr_maxdelta", &maxdelta),
        fp_var_f("sr_maxlevel", &maxlevel), fp_var_f("sr_premult", &premult), fp_var_f("sr_gamma", &gamma),
    };
    struct pl_shader_desc descs[] = {
        fp_desc_tex("sr_canvas", canvas, false), fp_desc_tex("sr_prev", s->state[s->cur], false),
    };
    pl_shader sh = pl_dispatch_begin(env->dp);
    bool ok = pl_shader_custom(sh, &(struct pl_custom_shader) {
        .description = "surround update", .prelude = s->prelude, .body = update_body,
        .input = PL_SHADER_SIG_NONE, .output = PL_SHADER_SIG_COLOR,
        .variables = vars, .num_variables = 6, .descriptors = descs, .num_descriptors = 2,
    });
    if (!ok || !fp_dispatch(env, &sh, s->state[1 - s->cur])) {
        pl_dispatch_abort(env->dp, &sh);
        return false;
    }
    s->cur = 1 - s->cur;
    return true;
}

static bool pass_edge(struct fp_surround *s)
{
    const struct fp_env *env = s->env;
    struct pl_shader_desc d = fp_desc_tex("sr_state", s->state[s->cur], false);
    pl_shader sh = pl_dispatch_begin(env->dp);
    bool ok = pl_shader_custom(sh, &(struct pl_custom_shader) {
        .description = "surround edge cells", .body = edge_body,
        .input = PL_SHADER_SIG_NONE, .output = PL_SHADER_SIG_COLOR, .descriptors = &d, .num_descriptors = 1,
    });
    if (!ok || !fp_dispatch(env, &sh, s->edge)) {
        pl_dispatch_abort(env->dp, &sh);
        return false;
    }
    return true;
}

// One separable Gaussian blur of `src` into `dst` (via the scratch texture), sigma in grid cells.
static bool pass_blur(struct fp_surround *s, pl_tex src, pl_tex dst, float sigma_cells)
{
    const struct fp_env *env = s->env;
    for (int pass = 0; pass < 2; pass++) {
        int dir[2] = { pass == 0, pass == 1 };
        float sigma = sigma_cells;
        struct pl_shader_var vars[] = { fp_var_iv2("sr_dir", dir), fp_var_f("sr_sigma", &sigma) };
        struct pl_shader_desc d = fp_desc_tex("sr_src", pass == 0 ? src : s->tmp, false);
        pl_shader sh = pl_dispatch_begin(env->dp);
        bool ok = pl_shader_custom(sh, &(struct pl_custom_shader) {
            .description = "surround blur", .body = blur_body,
            .input = PL_SHADER_SIG_NONE, .output = PL_SHADER_SIG_COLOR,
            .variables = vars, .num_variables = 2, .descriptors = &d, .num_descriptors = 1,
        });
        if (!ok || !fp_dispatch(env, &sh, pass == 0 ? s->tmp : dst)) {
            pl_dispatch_abort(env->dp, &sh);
            return false;
        }
    }
    return true;
}

bool fp_surround_update(struct fp_surround *s, pl_tex canvas, const struct fp_surround_look *look,
                        const struct fp_surround_time *time, const struct fp_surround_frame *frame)
{
    if (look->mode == FP_SURROUND_BLACK)
        return true;
    struct fp_surround_step st = fp_surround_clock_step(&s->clock, time, frame);
    float falloff_cells = look->falloff_px > 0 ? look->falloff_px / (float) s->cell : 8.0f;
    if (falloff_cells < 1.0f)
        falloff_cells = 1.0f;
    bool ok = pass_update(s, canvas, look, st) && pass_edge(s) &&
              pass_blur(s, s->edge, s->lvl[0], FP_SIGMA_FINE_CELLS) &&
              pass_blur(s, s->edge, s->lvl[1], FP_SIGMA_COARSE_CELLS) &&
              pass_blur(s, s->state[s->cur], s->lvl[2], falloff_cells);
    s->ready = ok;
    return ok;
}

// ---- the fill -------------------------------------------------------------------------------------------------

bool fp_surround_append_fill(struct fp_surround *s, pl_shader sh, const struct fp_surround_look *look)
{
    bool glow = look->mode == FP_SURROUND_GLOW && s->ready;
    float f_glow = glow ? 1.0f : 0.0f;
    float gain = look->gain, sat = look->saturation, maxlevel = look->max_level > 0 ? look->max_level : 1.0f;
    float premult = look->premultiplied_alpha ? 1.0f : 0.0f;
    float inv_gamma = 1.0f / (look->state_gamma > 0 ? look->state_gamma : 1.0f);
    struct pl_shader_var vars[] = {
        fp_var_f("sr_glow", &f_glow), fp_var_f("sr_gain", &gain), fp_var_f("sr_sat", &sat),
        fp_var_f("sr_maxlevel", &maxlevel), fp_var_f("sr_premult", &premult), fp_var_f("sr_inv_gamma", &inv_gamma),
    };
    struct pl_shader_desc descs[] = {
        fp_desc_tex("sr_lvl_a", s->lvl[0], true), fp_desc_tex("sr_lvl_b", s->lvl[1], true),
        fp_desc_tex("sr_lvl_c", s->lvl[2], true),
    };
    return pl_shader_custom(sh, &(struct pl_custom_shader) {
        .description = "surround fill", .prelude = s->prelude, .body = fill_body,
        .input = PL_SHADER_SIG_COLOR, .output = PL_SHADER_SIG_COLOR,
        .variables = vars, .num_variables = 6, .descriptors = descs, .num_descriptors = 3,
    });
}

bool fp_surround_apply(struct fp_surround *s, pl_tex canvas, pl_tex out, const struct fp_surround_look *look,
                       const struct fp_surround_time *time, const struct fp_surround_frame *frame)
{
    const struct fp_env *env = s->env;
    if (!fp_surround_update(s, canvas, look, time, frame))
        return false;
    pl_shader sh = pl_dispatch_begin(env->dp);
    if (!fp_append_canvas_fetch(sh, canvas) || !fp_surround_append_fill(s, sh, look) || !fp_dispatch(env, &sh, out)) {
        pl_dispatch_abort(env->dp, &sh);
        return false;
    }
    return true;
}
