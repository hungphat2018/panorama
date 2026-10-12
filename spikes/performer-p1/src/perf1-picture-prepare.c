// Prepared-frame cache, see perf1-picture-prepare.h. Ported from the picture-path harness (pp_s2_prepare).
#include "perf1-picture-prepare.h"

#include "perf1-picture-shader.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <libplacebo/filters.h>
#include <libplacebo/shaders/custom.h>

static void info_cb(void *priv, const struct pl_render_info *info)
{
    struct pic_prep *pp = priv;
    pp->gpu_ns += info->pass->last;
}

bool pic_prep_init(struct pic_prep *pp, pl_gpu gpu, pl_dispatch dp, const char *chroma_filter)
{
    memset(pp, 0, sizeof(*pp));
    pp->gpu = gpu;
    pp->dp = dp;
    // a 1:1 conversion: no scaler, no sigmoid, no dither, no peak detection, no deband
    pp->params = *pl_render_params(.info_callback = info_cb, .info_priv = pp);
    if (chroma_filter && *chroma_filter) {
        const struct pl_filter_config *f = pl_find_filter_config(chroma_filter, PL_FILTER_UPSCALING);
        if (!f) {
            fprintf(stderr, "perf1: unknown chroma filter '%s'\n", chroma_filter);
            return false;
        }
        pp->params.plane_upscaler = f;
        pp->params.plane_downscaler = f;
    }
    pp->header = strdup(pic_glsl_common);
    return pp->header != NULL;
}

// Forgets the frame; the textures stay for the next one of the same size (allocating 16 MB per frame would hitch).
static void free_slot(struct pic_prep *pp, struct pic_prepared *s)
{
    (void) pp;
    s->used = false;
    s->has_sig = false;
}

static void destroy_slot(struct pic_prep *pp, struct pic_prepared *s)
{
    pl_tex_destroy(pp->gpu, &s->lin);
    pl_tex_destroy(pp->gpu, &s->sig);
    memset(s, 0, sizeof(*s));
}

void pic_prep_flush(struct pic_prep *pp)
{
    for (int i = 0; i < PIC_PREP_SLOTS; i++)
        free_slot(pp, &pp->slot[i]);
}

void pic_prep_destroy(struct pic_prep *pp)
{
    if (!pp->gpu)
        return;
    for (int i = 0; i < PIC_PREP_SLOTS; i++)
        destroy_slot(pp, &pp->slot[i]);
    pl_timer_destroy(pp->gpu, &pp->sigmoid_timer);
    free(pp->header);
    memset(pp, 0, sizeof(*pp));
}

static pl_tex make_texture(struct pic_prep *pp, int w, int h)
{
    pl_fmt fmt = pl_find_named_fmt(pp->gpu, "rgba16f");
    if (!fmt)
        return NULL;
    return pl_tex_create(pp->gpu, pl_tex_params(.w = w, .h = h, .format = fmt, .sampleable = true, .renderable = true,
                                                .storable = true, .blit_src = true, .blit_dst = true,
                                                .host_readable = true, .debug_tag = PL_DEBUG_TAG));
}

static bool make_sigmoid(struct pic_prep *pp, struct pic_prepared *s)
{
    if (!s->sig)
        s->sig = make_texture(pp, s->w, s->h);
    if (!s->sig)
        return false;
    if (!pp->sigmoid_timer)
        pp->sigmoid_timer = pl_timer_create(pp->gpu);
    struct pl_shader_desc d = {
        .desc = { .name = "u_src_a", .type = PL_DESC_SAMPLED_TEX },
        .binding = { .object = s->lin, .address_mode = PL_TEX_ADDRESS_CLAMP, .sample_mode = PL_TEX_SAMPLE_NEAREST },
    };
    struct pl_custom_shader cs = {
        .header = pp->header,
        .body = "color = vec4(pic_sigmoid(texelFetch(u_src_a, ivec2(gl_FragCoord.xy), 0).rgb), 1.0);",
        .input = PL_SHADER_SIG_NONE, .output = PL_SHADER_SIG_COLOR,
        .descriptors = &d, .num_descriptors = 1,
        .compute = true, .compute_group_size = { 16, 16 },
        .description = "picture sigmoid copy",
    };
    pl_shader sh = pl_dispatch_begin(pp->dp);
    if (!pl_shader_custom(sh, &cs)) {
        pl_dispatch_abort(pp->dp, &sh);
        return false;
    }
    s->has_sig = pl_dispatch_finish(pp->dp, pl_dispatch_params(.shader = &sh, .target = s->sig, .timer = pp->sigmoid_timer));
    return s->has_sig;
}

const struct pic_prepared *pic_prep_get(struct pic_prep *pp, pl_renderer rr, double pts, const struct pl_frame *frame,
                                        int w, int h, bool want_sigmoid)
{
    struct pic_prepared *s = NULL, *victim = &pp->slot[0];
    for (int i = 0; i < PIC_PREP_SLOTS; i++) {
        struct pic_prepared *c = &pp->slot[i];
        if (c->used && c->pts == pts && c->w == w && c->h == h)
            s = c;
        if (!c->used || (victim->used && c->stamp < victim->stamp))
            victim = c;
    }
    if (s) {
        pp->hits++;
    } else {
        pp->misses++;
        s = victim;
        if (s->lin && (s->w != w || s->h != h))
            destroy_slot(pp, s);
        free_slot(pp, s);
        s->w = w;
        s->h = h;
        if (!s->lin)
            s->lin = make_texture(pp, w, h);
        if (!s->lin) {
            fprintf(stderr, "perf1: cannot create the %dx%d prepared-frame texture\n", w, h);
            return NULL;
        }
        struct pl_frame img = *frame, out = {
            .num_planes = 1,
            .planes = {{ .texture = s->lin, .components = 4, .component_mapping = { 0, 1, 2, 3 } }},
            .repr = { .sys = PL_COLOR_SYSTEM_RGB, .levels = PL_COLOR_LEVELS_FULL, .alpha = PL_ALPHA_PREMULTIPLIED },
            // black pinned to "infinite contrast": left unset, libplacebo infers a 1000:1 display and lifts black
            .color = { .primaries = PL_COLOR_PRIM_BT_709, .transfer = PL_COLOR_TRC_LINEAR,
                       .hdr = { .min_luma = PL_COLOR_HDR_BLACK, .max_luma = PL_COLOR_SDR_WHITE } },
            .crop = { 0, 0, (float) w, (float) h },
        };
        if (!pl_render_image(rr, &img, &out, &pp->params)) {
            fprintf(stderr, "perf1: preparing the frame at %.6f s failed\n", pts);
            free_slot(pp, s);
            return NULL;
        }
        s->used = true;
        s->pts = pts;
    }
    s->stamp = ++pp->tick;
    if (want_sigmoid && !s->has_sig && !make_sigmoid(pp, s)) {
        fprintf(stderr, "perf1: the sigmoid copy of the frame at %.6f s failed\n", pts);
        return NULL;
    }
    return s;
}

const struct pic_prepared *pic_prep_find(struct pic_prep *pp, double pts, int w, int h, bool want_sigmoid)
{
    for (int i = 0; i < PIC_PREP_SLOTS; i++) {
        struct pic_prepared *c = &pp->slot[i];
        if (!c->used || c->pts != pts || c->w != w || c->h != h)
            continue;
        if (want_sigmoid && !c->has_sig && !make_sigmoid(pp, c))
            return NULL;
        c->stamp = ++pp->tick;
        return c;
    }
    return NULL;
}

uint64_t pic_prep_take_gpu_ns(struct pic_prep *pp)
{
    uint64_t ns = pp->sigmoid_timer ? pl_timer_query(pp->gpu, pp->sigmoid_timer) : 0;
    ns += pp->gpu_ns;
    pp->gpu_ns = 0;
    return ns;
}
