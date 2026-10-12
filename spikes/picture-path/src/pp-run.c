// Scenario runner, see pp-run.h.
#include "pp-run.h"

#include <stdio.h>
#include <string.h>

#include "pp-util.h"

const char *pp_structure_name(enum pp_structure s)
{
    static const char *const n[] = { "s1", "s1-strips", "s1-blend", "s1-margin", "s1-hook", "s1-twopass", "s2" };
    return n[s];
}

bool pp_structure_from_name(const char *name, enum pp_structure *out)
{
    for (int i = 0; i < 7; i++) {
        if (!strcmp(name, pp_structure_name((enum pp_structure) i))) {
            *out = (enum pp_structure) i;
            return true;
        }
    }
    return false;
}

bool pp_run_init(struct pp_run *r, const struct pp_gpu *g, const char *scenario_path, const struct pp_run_cfg *cfg)
{
    memset(r, 0, sizeof(*r));
    r->g = g;
    r->cfg = *cfg;
    char err[256];
    if (!pp_scenario_load(&r->scn, scenario_path, err, sizeof(err))) {
        fprintf(stderr, "picture-path: %s\n", err);
        return false;
    }
    if (cfg->kernel[0])
        snprintf(r->scn.kernel, sizeof(r->scn.kernel), "%s", cfg->kernel);
    pp_scenario_picture(&r->scn, &r->pic);
    const struct pp_room *room = &r->scn.room;

    if (!pp_source_load(&r->src_a, g, r->scn.source_file, cfg->fmt, r->scn.src_w, r->scn.src_h))
        return false;
    if (r->scn.has_b) {
        if (!pp_source_load(&r->src_b, g, r->scn.source_b_file, cfg->fmt, r->scn.src_w, r->scn.src_h))
            return false;
        r->has_src_b = true;
    }
    for (int k = 0; k < room->nmon; k++) {
        r->canvas[k] = pp_tex_create_canvas_fmt(g, room->mon[k].w, room->mon[k].h, cfg->debug_map ? "rgba32f" : "rgba16f",
                                                "monitor canvas");
        if (!r->canvas[k])
            return false;
        if (!strcmp(r->scn.domain, "sigmoid"))
            r->sigmoid[k] = true;
        else if (!strcmp(r->scn.domain, "linear"))
            r->sigmoid[k] = false;
        else
            r->sigmoid[k] = pp_monitor_upscales(room, k, &r->pic, &r->scn.surface);
        r->any_sigmoid |= r->sigmoid[k];
        if (cfg->st == PP_ST_S1_BLEND) {
            r->tmp_a[k] = pp_tex_create_canvas(g, room->mon[k].w, room->mon[k].h, "blend canvas A");
            r->tmp_b[k] = pp_tex_create_canvas(g, room->mon[k].w, room->mon[k].h, "blend canvas B");
            if (!r->tmp_a[k] || !r->tmp_b[k])
                return false;
        }
    }
    const char *chroma = cfg->chroma[0] ? cfg->chroma : NULL;
    // S2 also provides the custom passes of the S1 variants (the blend), so it is always created.
    // S1 can use any libplacebo filter by name; S2's table knows only the radial kernels of pp_kernel_weight, so an S1-only
    // name leaves S2 (needed for the blend helper) on the default table.
    double unused_support;
    const char *s2_kernel = pp_kernel_weight(r->scn.kernel, 0.0, NULL, &unused_support) || cfg->st == PP_ST_S2
                                ? r->scn.kernel : "ewa_lanczossharp";
    if (!pp_s2_init(&r->s2, g, &r->tm, cfg->shader_dir, s2_kernel, chroma))
        return false;
    if (cfg->st == PP_ST_S1_HOOK && !pp_s1_hook_init(&r->s1h, g, &r->s2, &r->tm, room->nmon))
        return false;
    if (cfg->st != PP_ST_S2 && cfg->st != PP_ST_S1_HOOK) {
        if (!pp_s1_init(&r->s1, g, &r->tm, room->nmon, r->scn.kernel, strcmp(r->scn.domain, "linear") != 0, r->scn.antiring, chroma))
            return false;
    }
    if (cfg->st == PP_ST_S1_TWOPASS) {
        if (!pp_s2_init(&r->s2b, g, &r->tm, cfg->shader_dir, cfg->kernel2[0] ? cfg->kernel2 : "ewa_catmull_rom", chroma))
            return false;
        for (int k = 0; k < room->nmon; k++) {
            const struct pp_monitor *m = &room->mon[k];
            int wi = (int) lround(r->pic.width / (m->w_mm / m->w)), hi = (int) lround(r->pic.height / (m->h_mm / m->h));
            r->inter[k] = pp_tex_create_canvas(g, wi, hi, "intermediate");
            if (!r->inter[k])
                return false;
            r->pic2[k] = r->pic;                      // same place on the surface, the whole intermediate as its crop
            r->pic2[k].crop[0] = r->pic2[k].crop[1] = 0.0;
            r->pic2[k].crop[2] = r->pic2[k].cw = wi;
            r->pic2[k].crop[3] = r->pic2[k].ch = hi;
        }
    }
    return true;
}

void pp_run_destroy(struct pp_run *r)
{
    if (!r->g)
        return;
    pp_s1_destroy(&r->s1);
    pp_s1_hook_destroy(&r->s1h);
    pp_prepared_destroy(&r->s2, &r->prep_a);
    pp_prepared_destroy(&r->s2, &r->prep_b);
    pp_s2_destroy(&r->s2);
    pp_s2_destroy(&r->s2b);
    for (int k = 0; k < PP_MAX_MON; k++) {
        pl_tex_destroy(r->g->gpu, &r->inter[k]);
        pl_tex_destroy(r->g->gpu, &r->canvas[k]);
        pl_tex_destroy(r->g->gpu, &r->tmp_a[k]);
        pl_tex_destroy(r->g->gpu, &r->tmp_b[k]);
    }
    pp_source_destroy(&r->src_a, r->g);
    if (r->has_src_b)
        pp_source_destroy(&r->src_b, r->g);
    memset(r, 0, sizeof(*r));
}

bool pp_run_prepare(struct pp_run *r)
{
    if (r->cfg.st != PP_ST_S2)
        return true;
    pp_timing_reset(&r->tm);
    pp_s2_begin_frame(&r->s2);
    if (!pp_s2_prepare(&r->s2, &r->src_a, &r->prep_a, r->any_sigmoid, 0))
        return false;
    return !r->has_src_b || pp_s2_prepare(&r->s2, &r->src_b, &r->prep_b, r->any_sigmoid, 1);
}

bool pp_run_refresh(struct pp_run *r, bool vary)
{
    const struct pp_room *room = &r->scn.room;
    int n = r->refresh_count++;
    double jitter = vary ? 0.01 + 0.0001 * (n % 5000) : 0.0;
    double extra_ms = vary ? 0.001 * (n % 5000) : 0.0;
    pp_timing_reset(&r->tm);
    if (r->cfg.st == PP_ST_S2)
        pp_s2_begin_frame(&r->s2);

    for (int k = 0; k < room->nmon; k++) {
        r->tm.cur_mon = k;
        if (r->flush_cache && r->cfg.st != PP_ST_S2 && r->cfg.st != PP_ST_S1_HOOK && r->cfg.st != PP_ST_S1_TWOPASS)
            pl_renderer_flush_cache(r->s1.rr[k]);
        switch (r->cfg.st) {
        case PP_ST_S2: {
            struct pp_s2_job job = { .scn = &r->scn, .k = k, .pic = &r->pic, .a = &r->prep_a,
                                     .b = r->has_src_b ? &r->prep_b : NULL, .canvas = r->canvas[k],
                                     .sigmoid = r->sigmoid[k], .extra_slide_ms = extra_ms, .debug_map = r->cfg.debug_map };
            if (!pp_s2_resample(&r->s2, &job))
                return false;
            break;
        }
        case PP_ST_S1_TWOPASS: {
            // pass 1: libplacebo's one resample, source -> the picture at this monitor's pixel pitch
            struct pl_frame img = r->src_a.frame, tgt;
            pp_canvas_frame(&tgt, r->inter[k]);
            img.crop = (pl_rect2df) { (float) r->pic.crop[0], (float) r->pic.crop[1], (float) r->pic.crop[2], (float) r->pic.crop[3] };
            if (!pl_render_image(r->s1.rr[k], &img, &tgt, &r->s1.params))
                return false;
            // pass 2: the warp onto the monitor, a second resample from the intermediate
            pp_s2_begin_frame(&r->s2b);
            struct pp_prepared inter = { .w = r->inter[k]->params.w, .h = r->inter[k]->params.h, .lin = r->inter[k] };
            struct pp_s2_job job = { .scn = &r->scn, .k = k, .pic = &r->pic2[k], .a = &inter, .b = NULL, .canvas = r->canvas[k],
                                     .sigmoid = false, .extra_slide_ms = extra_ms };
            if (!pp_s2_resample(&r->s2b, &job))
                return false;
            break;
        }
        case PP_ST_S1_HOOK:
            pp_s2_begin_frame(&r->s2);
            if (!pp_s1_hook_render(&r->s1h, &r->scn, k, &r->pic, &r->src_a, r->canvas[k], r->sigmoid[k], extra_ms))
                return false;
            break;
        case PP_ST_S1:
        case PP_ST_S1_MARGIN:
        case PP_ST_S1_STRIPS: {
            struct pp_s1_opts o = { .strips = r->cfg.st == PP_ST_S1_STRIPS ? r->cfg.strips : 0,
                                    .margin = r->cfg.st == PP_ST_S1_MARGIN ? 8 : 0, .jitter_px = jitter,
                                    .extra_slide_ms = extra_ms, .uniform_slide = r->cfg.uniform_slide };
            if (!pp_s1_render_monitor(&r->s1, &r->scn, k, &r->pic, &r->src_a, r->canvas[k], &o))
                return false;
            break;
        }
        case PP_ST_S1_BLEND: {
            struct pp_s1_opts o = { .jitter_px = jitter, .extra_slide_ms = extra_ms, .uniform_slide = r->cfg.uniform_slide };
            if (!r->has_src_b || !r->scn.has_blend[k]) {
                if (!pp_s1_render_monitor(&r->s1, &r->scn, k, &r->pic, &r->src_a, r->canvas[k], &o))
                    return false;
                break;
            }
            if (!pp_s1_render_monitor(&r->s1, &r->scn, k, &r->pic, &r->src_a, r->tmp_a[k], &o) ||
                !pp_s1_render_monitor(&r->s1, &r->scn, k, &r->pic, &r->src_b, r->tmp_b[k], &o))
                return false;
            pp_s2_begin_frame(&r->s2);
            if (!pp_s2_blend_canvases(&r->s2, r->tmp_a[k], r->tmp_b[k], r->canvas[k], r->scn.blend_wa[k], r->scn.blend_wb[k], k))
                return false;
            break;
        }
        }
    }
    return true;
}

void pp_run_collect(struct pp_run *r)
{
    pp_s2_collect_timers(&r->s2);
    if (r->s2b.g)
        pp_s2_collect_timers(&r->s2b);
}
