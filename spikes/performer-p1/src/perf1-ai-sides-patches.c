// The AI sides' patches, player end (see perf1-ai-sides.h; phase 4 of plans/261013-0718-ai-surround-live-path): a showing
// whose shot's patch page was on the GPU when it began gets an accumulate texture and an overlay texture, both the page's
// grid (set world px -> grid texel through the page's grid map, the same projective path the canvas takes).
//   accumulate  every persistent patch up to the film frame on screen, drawn in table order with alpha blending: a patch's
//               straight-alpha texels replace what is under them where their alpha is 1 and keep it where it is 0, so
//               the texture holds premultiplied colour (bilinear sampling then needs no fringe fix)
//   overlay     the overlays whose frame range holds the frame on screen, over a cleared texture
// Both are brought forward once per film frame (the monitors share them: a second call for the same frame does nothing,
// and a monitor a frame behind the other sees the newer state for that refresh: a seek starts a new showing, so nothing
// ever goes back). A showing that has no pair at its cut, or loses it, shows its canvas alone for the whole shot: the
// strips never pop in mid-shot. The pairs are made at the warm-up frame (no texture is created while the film plays,
// unless a page's grid is larger than WARM_W x WARM_H). The fill and the seam profile composite overlay over accumulate
// over the canvas.
#include "perf1-ai-sides-internal.h"

#define WARM_W 2048                 // the pairs' size made at warm-up (page grids of JJK's shots are mostly under this)
#define WARM_H 1024

static const char patch_body[] =
    "color = texelFetch(pt_page, ivec2(floor(gl_FragCoord.xy + pt_off)), 0);\n";

static pl_fmt acc_format(void)
{
    static const char *const names[] = { "rgba8" };
    return fp_find_fmt(A.gpu.gpu, names, 1, PL_FMT_CAP_SAMPLEABLE | PL_FMT_CAP_RENDERABLE | PL_FMT_CAP_BLENDABLE |
                                                PL_FMT_CAP_LINEAR);
}

static int bucket(int n)
{
    return (n + AI_TEX_BUCKET - 1) / AI_TEX_BUCKET * AI_TEX_BUCKET;
}

static void clear(pl_tex t)
{
    pl_tex_clear(A.gpu.gpu, t, (float[4]) { 0.0f, 0.0f, 0.0f, 0.0f });
}

// The pair's textures, made (or grown) to hold w x h.
static bool make_pair(struct ai_acc *a, int w, int h)
{
    if (a->acc && a->ovl && a->tex_w >= w && a->tex_h >= h)
        return true;
    pl_tex_destroy(A.gpu.gpu, &a->acc);
    pl_tex_destroy(A.gpu.gpu, &a->ovl);
    struct fp_env env = { .log = A.gpu.log, .gpu = A.gpu.gpu, .dp = G_AI.dp };
    pl_fmt fmt = acc_format();
    int tw = bucket(w) > a->tex_w ? bucket(w) : a->tex_w;          // grow, never shrink the other axis
    int th = bucket(h) > a->tex_h ? bucket(h) : a->tex_h;
    a->acc = fmt ? fp_make_target(&env, tw, th, fmt, false, "ai accumulate") : NULL;
    a->ovl = fmt ? fp_make_target(&env, tw, th, fmt, false, "ai overlay") : NULL;
    if (!a->acc || !a->ovl) {
        pl_tex_destroy(A.gpu.gpu, &a->acc);
        pl_tex_destroy(A.gpu.gpu, &a->ovl);
        a->tex_w = a->tex_h = 0;
        return false;
    }
    a->tex_w = tw;
    a->tex_h = th;
    return true;
}

// A pair for showing `idx`: one no showing drew within AI_ACC_PIN_S, holding w x h, cleared; -1 when none is free.
static int acc_take(int idx, int w, int h)
{
    double now = now_s();
    int pick = -1;
    for (int i = 0; i < AI_ACC_MAX; i++) {
        const struct ai_acc *a = &G_AI.acc[i];
        bool idle = a->owner < 0 || !G_AI.show[a->owner].used || G_AI.show[a->owner].acc != i ||
                    now - G_AI.show[a->owner].last_drawn > AI_ACC_PIN_S;
        if (idle && pick < 0)
            pick = i;
    }
    if (pick < 0)
        return -1;
    struct ai_acc *a = &G_AI.acc[pick];
    if (a->owner >= 0 && G_AI.show[a->owner].acc == pick)
        G_AI.show[a->owner].acc = -1;                       // taken from a showing that is over
    a->owner = -1;
    if (!make_pair(a, w, h))
        return -1;
    clear(a->acc);
    clear(a->ovl);
    a->ovl_dirty = false;
    a->owner = idx;
    return pick;
}

static bool draw_patch(pl_tex page, pl_tex dst, const struct ai_patch_rec *p)
{
    if (!G_AI.dp && !(G_AI.dp = pl_dispatch_create(A.gpu.log, A.gpu.gpu)))
        return false;
    float off[2] = { (float) p->src_x - (float) p->dst_x, (float) p->src_y - (float) p->dst_y };
    struct pl_shader_var v = { .var = pl_var_vec2("pt_off"), .data = off, .dynamic = true };
    struct pl_shader_desc d = fp_desc_tex("pt_page", page, false);
    pl_shader sh = pl_dispatch_begin(G_AI.dp);
    bool ok = pl_shader_custom(sh, &(struct pl_custom_shader) {
        .description = "ai patch", .body = patch_body, .input = PL_SHADER_SIG_NONE, .output = PL_SHADER_SIG_COLOR,
        .variables = &v, .num_variables = 1, .descriptors = &d, .num_descriptors = 1,
    });
    pl_rect2d rc = { p->dst_x, p->dst_y, p->dst_x + p->w, p->dst_y + p->h };
    if (!ok || !pl_dispatch_finish(G_AI.dp, pl_dispatch_params(.shader = &sh, .target = dst, .rect = rc,
                                                               .blend_params = &pl_alpha_overlay))) {
        pl_dispatch_abort(G_AI.dp, &sh);
        G_AI.patch_failures++;                              // its texels keep the canvas under them (logged)
        return false;
    }
    G_AI.patches_applied++;
    return true;
}

bool ai_patches_to_frame(struct ai_showing *sh, int f)
{
    if (sh->page < 0)
        return false;
    const struct ai_gpu_canvas *pg = &G_AI.gpu[sh->page];
    if (!pg->used || !pg->complete || pg->kind != AI_KIND_PATCH_PAGE || pg->shot != sh->shot || !pg->patches) {
        sh->page = -1;                                      // the page went away: the canvas alone for this showing
        return false;
    }
    int idx = (int) (sh - G_AI.show);
    if (sh->acc < 0 || G_AI.acc[sh->acc].owner != idx) {
        if (sh->applied >= 0 || sh->acc_gen) {              // lost its pair mid-shot: the canvas alone from here on
            sh->page = -1;
            sh->acc = -1;
            return false;
        }
        sh->acc = acc_take(idx, (int) pg->page.grid_w, (int) pg->page.grid_h);
        sh->acc_gen++;
        if (sh->acc < 0) {                                  // no pair at its cut: the canvas alone for the whole shot
            sh->page = -1;
            return false;
        }
    }
    if (f <= sh->applied) {
        G_AI.monitor_behind += f < sh->applied;             // a monitor a frame behind the other: what is applied stays
        return true;
    }
    struct ai_acc *a = &G_AI.acc[sh->acc];
    double t0 = now_ms();
    bool ovl = false;
    for (uint32_t i = 0; i < pg->page.n_patches; i++) {
        const struct ai_patch_rec *p = &pg->patches[i];
        if (p->kind == AI_PATCH_PERSISTENT) {
            if ((int64_t) p->frame > sh->applied && (int64_t) p->frame <= f)
                draw_patch(pg->tex, a->acc, p);
        } else if ((int64_t) p->frame <= f && f <= (int64_t) p->until) {
            if (!ovl)
                clear(a->ovl);
            ovl = true;
            draw_patch(pg->tex, a->ovl, p);
        }
    }
    if (!ovl && a->ovl_dirty)
        clear(a->ovl);
    a->ovl_dirty = ovl;
    sh->applied = f;
    sh->acc_gen++;
    samples_push(&G_AI.patch_ms, now_ms() - t0);
    return true;
}

void ai_patches_warm_up(void)
{
    static bool done;
    if (done)
        return;
    done = true;
    for (int i = 0; i < AI_ACC_MAX; i++) {                  // every pair now, at a size most pages fit
        if (!make_pair(&G_AI.acc[i], WARM_W, WARM_H))
            return;
        clear(G_AI.acc[i].acc);
        clear(G_AI.acc[i].ovl);
        G_AI.acc[i].owner = -1;
    }
    const struct ai_patch_rec p = { .frame = 0, .until = 0, .kind = AI_PATCH_PERSISTENT, .w = 1, .h = 1 };
    draw_patch(G_AI.acc[0].ovl, G_AI.acc[0].acc, &p);       // the pass the film's first patch needs, compiled now
    G_AI.patches_applied = 0;
    clear(G_AI.acc[0].acc);
}

void ai_patches_release(int show_index)
{
    for (int i = 0; i < AI_ACC_MAX; i++)
        if (G_AI.acc[i].owner == show_index)
            G_AI.acc[i].owner = -1;
}

void ai_patches_destroy(void)
{
    if (!A.gpu.gpu)
        return;
    for (int i = 0; i < AI_ACC_MAX; i++) {
        pl_tex_destroy(A.gpu.gpu, &G_AI.acc[i].acc);
        pl_tex_destroy(A.gpu.gpu, &G_AI.acc[i].ovl);
        G_AI.acc[i].owner = -1;
    }
}
