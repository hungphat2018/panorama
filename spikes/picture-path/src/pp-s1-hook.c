// Structure S1-hook, see pp-s1-hook.h.
#include "pp-s1-hook.h"

#include <stdio.h>
#include <string.h>

#include "pp-util.h"

struct pp_hook_ctx {
    struct pp_s1_hook *h;
    const struct pp_scenario *scn;
    int k;
    const struct pp_picture *pic;
    bool sigmoid;
    double extra_slide_ms;
    pl_tex canvas;                  // the monitor's canvas: the target of the render call, the shape of the hook's output
    struct pl_hook hook;
    const struct pl_hook *hooks[1];
};

// Runs at PL_HOOK_RGB: the frame is RGB in the source's own transfer (sRGB here), source resolution, in a texture.
static struct pl_hook_res hook_cb(void *priv, const struct pl_hook_params *p)
{
    struct pp_hook_ctx *c = priv;
    struct pl_hook_res res = { .failed = true };
    const struct pp_monitor *m = &c->scn->room.mon[c->k];
    if (!p->tex || p->color.transfer != PL_COLOR_TRC_SRGB) {
        fprintf(stderr, "picture-path: the hook expects an sRGB texture at the RGB stage\n");
        return res;
    }
    pl_tex lin = p->get_tex(p->priv, p->tex->params.w, p->tex->params.h);
    pl_tex out = p->get_tex(p->priv, m->w, m->h);
    if (!lin || !out || !lin->params.storable || !out->params.storable) {
        fprintf(stderr, "picture-path: the renderer gave no storable temporary texture for the hook\n");
        return res;
    }
    if (!pp_s2_linearize(c->h->s2, p->tex, lin, c->sigmoid, c->k))
        return res;
    struct pp_prepared prep = { .w = lin->params.w, .h = lin->params.h, .lin = lin, .sig = lin, .has_sig = true };
    struct pp_s2_job job = { .scn = c->scn, .k = c->k, .pic = c->pic, .a = &prep, .b = NULL, .canvas = out,
                             .sigmoid = c->sigmoid, .extra_slide_ms = c->extra_slide_ms };
    if (!pp_s2_resample(c->h->s2, &job))
        return res;
    res.failed = false;
    res.output = PL_HOOK_SIG_TEX;
    res.tex = out;
    res.repr = (struct pl_color_repr) { .sys = PL_COLOR_SYSTEM_RGB, .levels = PL_COLOR_LEVELS_FULL, .alpha = PL_ALPHA_PREMULTIPLIED };
    res.color = (struct pl_color_space) { .primaries = PL_COLOR_PRIM_BT_709, .transfer = PL_COLOR_TRC_LINEAR,
                                          .hdr = { .min_luma = PL_COLOR_HDR_BLACK, .max_luma = PL_COLOR_SDR_WHITE } };
    res.components = 4;
    res.rect = (pl_rect2df) { 0, 0, (float) m->w, (float) m->h };
    return res;
}

bool pp_s1_hook_init(struct pp_s1_hook *h, const struct pp_gpu *g, struct pp_s2 *s2, struct pp_timing *tm, int nmon)
{
    memset(h, 0, sizeof(*h));
    h->g = g;
    h->s2 = s2;
    h->tm = tm;
    h->nmon = nmon;
    for (int k = 0; k < nmon; k++) {
        h->rr[k] = pl_renderer_create(g->log, g->gpu);
        if (!h->rr[k])
            return false;
        h->ctx[k] = pp_xcalloc(1, sizeof(*h->ctx[k]));
    }
    // Everything the pipeline does around the hook is the cheap built-in path: no scaler is configured, the hook delivers
    // the monitor's size, so the main scaler has nothing to do.
    h->params = *pl_render_params(
        .border = PL_CLEAR_COLOR, .background = PL_CLEAR_COLOR, .background_transparency = 1.0f,
        .info_callback = pp_timing_render_cb, .info_priv = tm,
    );
    return true;
}

void pp_s1_hook_destroy(struct pp_s1_hook *h)
{
    for (int k = 0; k < PP_MAX_MON; k++) {
        pl_renderer_destroy(&h->rr[k]);
        free(h->ctx[k]);
    }
    memset(h, 0, sizeof(*h));
}

bool pp_s1_hook_render(struct pp_s1_hook *h, const struct pp_scenario *scn, int k, const struct pp_picture *pic,
                       const struct pp_source *src, pl_tex canvas, bool sigmoid, double extra_slide_ms)
{
    struct pp_hook_ctx *c = h->ctx[k];
    c->h = h;
    c->scn = scn;
    c->k = k;
    c->pic = pic;
    c->sigmoid = sigmoid;
    c->extra_slide_ms = extra_slide_ms;
    c->canvas = canvas;
    c->hook = (struct pl_hook) { .stages = PL_HOOK_RGB, .input = PL_HOOK_SIG_TEX, .priv = c, .hook = hook_cb,
                                 .signature = 0x70f0 + (uint64_t) k };
    c->hooks[0] = &c->hook;
    struct pl_render_params params = h->params;
    params.hooks = c->hooks;
    params.num_hooks = 1;
    struct pl_frame img = src->frame, tgt;
    pp_canvas_frame(&tgt, canvas);
    return pl_render_image(h->rr[k], &img, &tgt, &params);
}
