// The AI sides' shaders (see perf1-ai-sides.h): the fill fragment fused into passes 2 and 3, and the seam profile of a
// film frame.
//
// Fill, per pixel the picture does not fully cover (color is the canvas texel, premultiplied by the picture's coverage a):
//   p      = the pixel's source position: pass 1's own map (pic_glsl_geometry), this refresh's camera slide included, so
//            the sides slide with the picture
//   t      = the canvas texel under p: the frame's projective camera transform to its world, then the canvas's origin and
//            scale (q = M (p, 1), t = q.xy / q.z)
//   c      = the canvas texel (straight alpha), then, when the showing has its shot's patch page (T3), the accumulate
//            texture over it and the overlay texture over that (both premultiplied, sampled at the page grid's coordinate
//            of p, the same projective way): what the camera carried into the sides since the shot began
//   lin    = that colour in linear light (its RGB is the film's own encoding: code^2.4, as the prepare step decodes the
//            picture), times the gain of the side the pixel lies farthest out on
//   pull   = the seam profile of each side the pixel lies outside of, faded with the distance from the picture's edge
//            (exp(-d / (falloff x side width)), hidden_sides.live_seam's rule), averaged where two sides meet
//   color  = picture + (1 - a) * coverage * (lin + pull); color.a grows by what the canvas covers, so the glow that follows
//            fills only the rest
// Seam profile, once per film frame and canvas: for each side, along its edge (source rows for left/right, columns for
// top/bottom), the mean of BAND picture pixels just inside the crop minus the mean of BAND canvas texels just outside
// (the same composite as the fill: patches included; gain applied; only where it covers), then blurred along the edge
// (sigma 24 source px) weighted by that coverage.
// Rows: 0 left, 1 top, 2 right, 3 bottom; texel i = source coordinate i + 0.5.
#include "perf1-ai-sides-internal.h"
#include "perf1-picture-shader.h"

#define SEAM_BAND 4
#define SEAM_SIGMA_PX 24.0f

#define GLSL(...) #__VA_ARGS__

static const char fill_header[] = GLSL(
    vec3 ai_lin(vec3 c)
    {
        return pow(max(c, vec3(0.0)), vec3(2.4));
    }

    float ai_fade(float d, float width)
    {
        return d > 0.0 ? exp(-d / max(1.0, ai_seam.y * width)) : 0.0;
    }

    vec3 ai_profile(float row, float along)
    {
        float n = ai_seam.z;
        return textureLod(ai_seam_tex, vec2(clamp(along, 0.5, n - 0.5) * ai_seam.w, (row + 0.5) * 0.25), 0.0).rgb;
    }
);

// Shared by the fill and the seam profile (their uniforms and textures carry the same names).
static const char sample_glsl[] = GLSL(
    // The sides' colour at source position p: the canvas (straight alpha), the accumulate texture over it, the overlay
    // over that (both premultiplied: the canvas is premultiplied for the composite); straight alpha out.
    vec4 ai_sample(vec2 p)
    {
        vec3 q = vec3(dot(ai_m0.xyz, vec3(p, 1.0)), dot(ai_m1.xyz, vec3(p, 1.0)), dot(ai_m2.xyz, vec3(p, 1.0)));
        vec4 c = vec4(0.0);
        if (q.z > 1e-6) {
            vec2 t = q.xy / q.z;
            if (t.x >= 0.0 && t.y >= 0.0 && t.x < ai_size.x && t.y < ai_size.y)
                c = textureLod(ai_tex, t * ai_size.zw, 0.0);
        }
        if (ai_aon.x > 0.5) {
            vec3 r = vec3(dot(ai_a0.xyz, vec3(p, 1.0)), dot(ai_a1.xyz, vec3(p, 1.0)), dot(ai_a2.xyz, vec3(p, 1.0)));
            if (r.z > 1e-6) {
                vec2 g = r.xy / r.z;
                if (g.x >= 0.0 && g.y >= 0.0 && g.x < ai_asize.x && g.y < ai_asize.y) {
                    vec4 k = textureLod(ai_acc_tex, g * ai_asize.zw, 0.0);
                    c.rgb *= c.a;
                    c = vec4(k.rgb + (1.0 - k.a) * c.rgb, k.a + (1.0 - k.a) * c.a);
                    if (ai_aon.y > 0.5) {
                        vec4 o = textureLod(ai_ovl_tex, g * ai_asize.zw, 0.0);
                        c = vec4(o.rgb + (1.0 - o.a) * c.rgb, o.a + (1.0 - o.a) * c.a);
                    }
                    c.rgb = c.a > 1e-6 ? c.rgb / c.a : vec3(0.0);
                }
            }
        }
        return c;
    }
);

static const char fill_body[] = GLSL(
    float a = clamp(color.a, 0.0, 1.0);
    if (a < 0.9995) {
        vec2 pix = gl_FragCoord.xy;
        vec2 p = pic_map(pic_surface(pix), pix.y, u_pic, u_crop, u_slide).xy;
        vec4 c = ai_sample(p);
        if (c.a > 0.0) {
            vec4 d = vec4(u_crop.x - p.x, u_crop.y - p.y, p.x - u_crop.z, p.y - u_crop.w);
            float g = ai_gain.x;
            float dm = d.x;
            if (d.y > dm) { g = ai_gain.y; dm = d.y; }
            if (d.z > dm) { g = ai_gain.z; dm = d.z; }
            if (d.w > dm) { g = ai_gain.w; dm = d.w; }
            vec3 lin = ai_lin(c.rgb) * g;
            if (ai_seam.x > 0.5) {
                vec4 f = vec4(ai_fade(d.x, ai_margin.x), ai_fade(d.y, ai_margin.y), ai_fade(d.z, ai_margin.z),
                              ai_fade(d.w, ai_margin.w));
                vec3 pull = f.x * ai_profile(0.0, p.y) + f.y * ai_profile(1.0, p.x) + f.z * ai_profile(2.0, p.y)
                          + f.w * ai_profile(3.0, p.x);
                lin = max(lin + pull / max(1.0, f.x + f.y + f.z + f.w), vec3(0.0));
            }
            float ca = c.a * (1.0 - a);
            color.rgb += ca * lin;
            color.a = a + ca;
        }
    }
);

static const char seam_raw_body[] = GLSL(
    ivec2 c = ivec2(gl_FragCoord.xy);
    float along = float(c.x) + 0.5;
    vec4 cr = sm_crop;
    vec3 pic = vec3(0.0);
    vec3 can = vec3(0.0);
    float wc = 0.0;
    float g = c.y == 0 ? sm_gain.x : c.y == 1 ? sm_gain.y : c.y == 2 ? sm_gain.z : sm_gain.w;
    for (int k = 0; k < SEAM_BAND; ++k) {
        vec2 pin;
        vec2 pout;
        if (c.y == 0 || c.y == 2) {
            float y = clamp(along, cr.y + 0.5, cr.w - 0.5);
            pin = c.y == 0 ? vec2(cr.x + 0.5 + float(k), y) : vec2(cr.z - 0.5 - float(k), y);
            pout = c.y == 0 ? vec2(cr.x - 0.5 - float(k), y) : vec2(cr.z + 0.5 + float(k), y);
        } else {
            float x = clamp(along, cr.x + 0.5, cr.z - 0.5);
            pin = c.y == 1 ? vec2(x, cr.y + 0.5 + float(k)) : vec2(x, cr.w - 0.5 - float(k));
            pout = c.y == 1 ? vec2(x, cr.y - 0.5 - float(k)) : vec2(x, cr.w + 0.5 + float(k));
        }
        pic += texelFetch(sm_src, ivec2(floor(pin)), 0).rgb;
        vec4 v = ai_sample(pout);
        can += v.a * pow(max(v.rgb, vec3(0.0)), vec3(2.4)) * g;
        wc += v.a;
    }
    pic /= float(SEAM_BAND);
    bool ok = wc > 0.5 * float(SEAM_BAND);
    color = ok ? vec4(pic - can / wc, 1.0) : vec4(0.0);
);

static const char seam_blur_body[] = GLSL(
    ivec2 c = ivec2(gl_FragCoord.xy);
    int n = textureSize(sb_raw, 0).x;
    int r = int(ceil(3.0 * sb_sigma));
    float k = -0.5 / (sb_sigma * sb_sigma);
    vec4 acc = vec4(0.0);
    for (int i = -r; i <= r; ++i) {
        vec4 v = texelFetch(sb_raw, ivec2(clamp(c.x + i, 0, n - 1), c.y), 0);
        float w = exp(k * float(i * i)) * v.a;
        acc += vec4(v.rgb * w, w);
    }
    color = acc.a > 1.0e-6 ? vec4(acc.rgb / acc.a, 1.0) : vec4(0.0);
);

// ---- the fill ---------------------------------------------------------------------------------------------------

static char *g_fill_header;

#define V4(name, arr) { .var = pl_var_vec4(name), .data = arr, .dynamic = true }
// The canvas and patch uniforms both shaders share (ai_sample).
#define SAMPLE_VARS(in) V4("ai_m0", (in)->m0), V4("ai_m1", (in)->m1), V4("ai_m2", (in)->m2), V4("ai_size", (in)->size), \
    V4("ai_a0", (in)->a0), V4("ai_a1", (in)->a1), V4("ai_a2", (in)->a2), V4("ai_asize", (in)->asize), V4("ai_aon", (in)->aon)
#define SAMPLE_DESCS(in) fp_desc_tex("ai_tex", (in)->tex, true), \
    fp_desc_tex("ai_acc_tex", (in)->acc_tex ? (in)->acc_tex : G_AI.stand_in[AI_STAND_ACC], true), \
    fp_desc_tex("ai_ovl_tex", (in)->ovl_tex ? (in)->ovl_tex : G_AI.stand_in[AI_STAND_OVL], true)

bool ai_fill_append(void *priv, pl_shader sh)
{
    struct ai_fill_in *in = priv;
    if (!in->on)
        return true; // nothing ready for this showing: the glow fills the leftover area alone
    if (!g_fill_header) {
        size_t n = strlen(pic_glsl_geometry) + strlen(sample_glsl) + strlen(fill_header) + 3;
        g_fill_header = malloc(n);
        if (!g_fill_header)
            return false;
        snprintf(g_fill_header, n, "%s\n%s\n%s", pic_glsl_geometry, sample_glsl, fill_header);
    }
    struct pl_shader_var vars[] = {
        V4("u_mon", in->u_mon), V4("u_cen", in->u_cen), V4("u_ex", in->u_ex), V4("u_ey", in->u_ey), V4("u_srf", in->u_srf),
        V4("u_pic", in->u_pic), V4("u_crop", in->u_crop), V4("u_slide", in->u_slide), SAMPLE_VARS(in),
        V4("ai_gain", in->gain), V4("ai_margin", in->margin), V4("ai_seam", in->seam),
    };
    struct pl_shader_desc descs[] = {
        SAMPLE_DESCS(in),
        fp_desc_tex("ai_seam_tex", in->seam_tex ? in->seam_tex : G_AI.stand_in[AI_STAND_SEAM], true), // no profile
    };
    return pl_shader_custom(sh, &(struct pl_custom_shader) {
        .description = "ai sides fill", .header = g_fill_header, .body = fill_body,
        .input = PL_SHADER_SIG_COLOR, .output = PL_SHADER_SIG_COLOR,
        .variables = vars, .num_variables = sizeof(vars) / sizeof(vars[0]),
        .descriptors = descs, .num_descriptors = sizeof(descs) / sizeof(descs[0]),
    });
}

// ---- the seam profile -------------------------------------------------------------------------------------------

static bool seam_targets(void)
{
    if (G_AI.seam_raw)
        return true;
    int n = A.src_w > A.src_h ? A.src_w : A.src_h;
    static const char *const f16[] = { "rgba16f" };
    pl_fmt fmt = fp_find_fmt(A.gpu.gpu, f16, 1, PL_FMT_CAP_SAMPLEABLE | PL_FMT_CAP_RENDERABLE | PL_FMT_CAP_LINEAR);
    struct fp_env env = { .log = A.gpu.log, .gpu = A.gpu.gpu, .dp = G_AI.dp };
    if (!fmt || !(G_AI.seam_raw = fp_make_target(&env, n, 4, fmt, false, "ai seam profile")))
        return false;
    for (int i = 0; i < AI_SEAM_CACHE; i++) {
        if (!(G_AI.seam_tex[i] = fp_make_target(&env, n, 4, fmt, false, "ai seam profile")))
            return false;
        G_AI.seam_pts[i] = -1.0;
        G_AI.seam_gpu[i] = -2; // matches no key: -1 is the warm-up's stand-in
    }
    G_AI.seam_timer = pl_timer_create(A.gpu.gpu);
    return true;
}

pl_tex ai_seam_profile(int key, uint32_t serial, uint32_t acc_gen, double pts, pl_tex front_lin, const struct ai_fill_in *fi)
{
    for (int i = 0; i < AI_SEAM_CACHE; i++) // both monitors draw the same frame: the profile is made once
        if (G_AI.seam_gpu[i] == key && G_AI.seam_pts[i] == pts && G_AI.seam_gen[i] == serial && G_AI.seam_acc[i] == acc_gen)
            return G_AI.seam_tex[i];
    if (!G_AI.dp && !(G_AI.dp = pl_dispatch_create(A.gpu.log, A.gpu.gpu)))
        return NULL;
    if (!seam_targets() || !front_lin)
        return NULL;
    int slot = G_AI.seam_next;
    G_AI.seam_next = (slot + 1) % AI_SEAM_CACHE;
    struct fp_env env = { .log = A.gpu.log, .gpu = A.gpu.gpu, .dp = G_AI.dp, .timer = G_AI.seam_timer };
    char prelude[64];
    snprintf(prelude, sizeof(prelude), "#define SEAM_BAND %d\n", SEAM_BAND);
    struct pl_shader_var vars[] = { V4("sm_crop", fi->u_crop), V4("sm_gain", fi->gain), SAMPLE_VARS(fi) };
    struct pl_shader_desc descs[] = { fp_desc_tex("sm_src", front_lin, false), SAMPLE_DESCS(fi) };
    pl_shader sh = pl_dispatch_begin(G_AI.dp);
    bool ok = pl_shader_custom(sh, &(struct pl_custom_shader) {
        .description = "ai seam profile", .prelude = prelude, .header = sample_glsl, .body = seam_raw_body,
        .input = PL_SHADER_SIG_NONE, .output = PL_SHADER_SIG_COLOR,
        .variables = vars, .num_variables = sizeof(vars) / sizeof(vars[0]),
        .descriptors = descs, .num_descriptors = sizeof(descs) / sizeof(descs[0]),
    });
    if (!ok || !fp_dispatch(&env, &sh, G_AI.seam_raw)) {
        pl_dispatch_abort(G_AI.dp, &sh);
        return NULL;
    }
    float sigma = SEAM_SIGMA_PX;
    struct pl_shader_var bv = { .var = pl_var_float("sb_sigma"), .data = &sigma, .dynamic = true };
    struct pl_shader_desc bd = fp_desc_tex("sb_raw", G_AI.seam_raw, false);
    sh = pl_dispatch_begin(G_AI.dp);
    ok = pl_shader_custom(sh, &(struct pl_custom_shader) {
        .description = "ai seam blur", .body = seam_blur_body, .input = PL_SHADER_SIG_NONE, .output = PL_SHADER_SIG_COLOR,
        .variables = &bv, .num_variables = 1, .descriptors = &bd, .num_descriptors = 1,
    });
    if (!ok || !fp_dispatch(&env, &sh, G_AI.seam_tex[slot])) {
        pl_dispatch_abort(G_AI.dp, &sh);
        return NULL;
    }
    G_AI.seam_pts[slot] = pts;
    G_AI.seam_gpu[slot] = key;
    G_AI.seam_gen[slot] = serial;
    G_AI.seam_acc[slot] = acc_gen;
    G_AI.seam_passes++;
    uint64_t ns;
    while ((ns = pl_timer_query(A.gpu.gpu, G_AI.seam_timer)))
        samples_push(&G_AI.seam_ms, (double) ns / 1e6);
    return G_AI.seam_tex[slot];
}

void ai_seam_destroy(void)
{
    if (!A.gpu.gpu)
        return;
    pl_tex_destroy(A.gpu.gpu, &G_AI.seam_raw);
    for (int i = 0; i < AI_SEAM_CACHE; i++)
        pl_tex_destroy(A.gpu.gpu, &G_AI.seam_tex[i]);
    pl_timer_destroy(A.gpu.gpu, &G_AI.seam_timer);
    for (int i = 0; i < AI_STAND_INS; i++)
        pl_tex_destroy(A.gpu.gpu, &G_AI.stand_in[i]);
    pl_dispatch_destroy(&G_AI.dp);
    free(g_fill_header);
    g_fill_header = NULL;
}
