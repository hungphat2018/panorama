// Structure S1: libplacebo draws each monitor, see pp-s1.h.
#include "pp-s1.h"

#include <math.h>
#include <stdio.h>
#include <string.h>

#include <libplacebo/filters.h>

#include "pp-geometry.h"
#include "pp-util.h"

bool pp_s1_init(struct pp_s1 *s1, const struct pp_gpu *g, struct pp_timing *tm, int nmon, const char *kernel, bool sigmoid,
                double antiring, const char *chroma_filter)
{
    memset(s1, 0, sizeof(*s1));
    s1->g = g;
    s1->tm = tm;
    s1->nmon = nmon;
    s1->filter = pl_find_filter_config(kernel, PL_FILTER_UPSCALING);
    if (!s1->filter) {
        fprintf(stderr, "picture-path: libplacebo has no filter named '%s'\n", kernel);
        return false;
    }
    for (int k = 0; k < nmon; k++) {
        s1->rr[k] = pl_renderer_create(g->log, g->gpu);
        if (!s1->rr[k])
            return false;
    }
    // The leftover area is cleared to fully transparent black: premultiplied alpha is the picture's coverage.
    s1->params = *pl_render_params(
        .upscaler = s1->filter, .downscaler = s1->filter,
        .sigmoid_params = sigmoid ? &pl_sigmoid_default_params : NULL,
        .antiringing_strength = (float) antiring,
        .border = PL_CLEAR_COLOR, .background = PL_CLEAR_COLOR, .background_transparency = 1.0f,
        .info_callback = pp_timing_render_cb, .info_priv = tm,
    );
    if (chroma_filter && *chroma_filter) {
        const struct pl_filter_config *f = pl_find_filter_config(chroma_filter, PL_FILTER_UPSCALING);
        if (!f) {
            fprintf(stderr, "picture-path: unknown chroma filter '%s'\n", chroma_filter);
            return false;
        }
        s1->params.plane_upscaler = f;
        s1->params.plane_downscaler = f;
    }
    return true;
}

void pp_s1_destroy(struct pp_s1 *s1)
{
    for (int k = 0; k < PP_MAX_MON; k++) {
        if (s1->g)
            pl_tex_destroy(s1->g->gpu, &s1->wide[k]);
        pl_renderer_destroy(&s1->rr[k]);
    }
    memset(s1, 0, sizeof(*s1));
}

static double clampd(double v, double lo, double hi)
{
    return v < lo ? lo : (v > hi ? hi : v);
}

bool pp_s1_fold_rects(const struct pp_scenario *scn, int k, const struct pp_picture *pic, int margin, int rect[4],
                      double src_rect[4])
{
    const struct pp_room *room = &scn->room;
    const struct pp_monitor *m = &room->mon[k];
    double u0, du, v0, dv;
    pp_fold_axes(room, k, &u0, &du, &v0, &dv);
    // pixel coordinates relative to the (extended) canvas: the monitor's pixel 0 is at `margin`
    double px_l = (pic->u_left - u0) / du, px_r = (pic->u_left + pic->width - u0) / du;
    double py_t = (pic->v_top - v0) / dv, py_b = (pic->v_top - pic->height - v0) / dv;
    double lo_x = -margin, hi_x = m->w + margin, lo_y = -margin, hi_y = m->h + margin;
    int x0 = (int) lround(clampd(px_l, lo_x, hi_x)), x1 = (int) lround(clampd(px_r, lo_x, hi_x));
    int y0 = (int) lround(clampd(py_t, lo_y, hi_y)), y1 = (int) lround(clampd(py_b, lo_y, hi_y));
    if (x1 <= x0 || y1 <= y0)
        return false;
    // The snapped pixel edges back to the picture: source = crop origin + (surface position - picture edge) / size * crop size.
    pp_picture_uv_to_src(pic, u0 + du * x0, v0 + dv * y0, &src_rect[0], &src_rect[1]);
    pp_picture_uv_to_src(pic, u0 + du * x1, v0 + dv * y1, &src_rect[2], &src_rect[3]);
    rect[0] = x0 + margin;
    rect[1] = y0 + margin;
    rect[2] = x1 + margin;
    rect[3] = y1 + margin;
    return true;
}

static void make_target(struct pl_frame *tgt, pl_tex canvas, const int rect[4])
{
    pp_canvas_frame(tgt, canvas);
    tgt->crop = (pl_rect2df) { (float) rect[0], (float) rect[1], (float) rect[2], (float) rect[3] };
}

bool pp_s1_render_monitor(struct pp_s1 *s1, const struct pp_scenario *scn, int k, const struct pp_picture *pic,
                          const struct pp_source *src, pl_tex canvas, const struct pp_s1_opts *opts)
{
    if (scn->surface.model != PP_SURFACE_FOLD) {
        fprintf(stderr, "picture-path: S1 draws the fold with crops; a curve has no crop rectangle\n");
        return false;
    }
    const struct pp_room *room = &scn->room;
    const struct pp_monitor *m = &room->mon[k];
    const int margin = opts->strips > 0 ? 0 : opts->margin;
    int rect[4];
    double sr[4];
    if (!pp_s1_fold_rects(scn, k, pic, margin, rect, sr)) {
        struct pl_frame none;                       // the picture misses this monitor: a fully transparent canvas
        pp_canvas_frame(&none, canvas);
        pl_frame_clear_rgba(s1->g->gpu, &none, (const float[4]) {0});
        return true;
    }

    // The source crop is relative to the visible frame; the source frame's own crop is the whole picture here.
    struct pl_frame img = src->frame;
    struct pl_frame tgt;

    if (opts->strips <= 0) {
        double du = 0.0, dv = 0.0;
        if (scn->has_slide && opts->uniform_slide) {
            double t = scn->slide.t_top_ms[k] + opts->extra_slide_ms + 0.5 * m->scan_ms;
            du = scn->slide.vu * (t - scn->slide.t_ref_ms) / 1000.0;
            dv = scn->slide.vv * (t - scn->slide.t_ref_ms) / 1000.0;
        }
        // Content shifted by (du, dv) on the surface is sampled from source position of (u - du, v - dv).
        double kx = pic->cw / pic->width, ky = pic->ch / pic->height;
        img.crop = (pl_rect2df) { (float) (sr[0] - du * kx + opts->jitter_px), (float) (sr[1] + dv * ky),
                                  (float) (sr[2] - du * kx + opts->jitter_px), (float) (sr[3] + dv * ky) };
        pl_tex target = canvas;
        if (margin > 0) {
            int ww = m->w + 2 * margin, wh = m->h + 2 * margin;
            if (!s1->wide[k] || s1->wide[k]->params.w != ww || s1->wide[k]->params.h != wh) {
                pl_tex_destroy(s1->g->gpu, &s1->wide[k]);
                s1->wide[k] = pp_tex_create_canvas(s1->g, ww, wh, "margin canvas");
                if (!s1->wide[k])
                    return false;
            }
            target = s1->wide[k];
        }
        make_target(&tgt, target, rect);
        struct pl_frame_mix mix = {
            .num_frames = 1,
            .frames = (const struct pl_frame *[]) { &img },
            .signatures = (uint64_t[]) { src->signature },
            .timestamps = (double[]) { 0.0 },
            .vsync_duration = 1.0,
        };
        if (!pl_render_image_mix(s1->rr[k], &mix, &tgt, &s1->params))
            return false;
        if (margin > 0) {
            // the monitor is the middle of the oversized canvas: a copy, not a resample
            pl_tex_blit(s1->g->gpu, pl_tex_blit_params(
                .src = target, .dst = canvas,
                .src_rc = { margin, margin, 0, margin + m->w, margin + m->h, 1 },
                .dst_rc = { 0, 0, 0, m->w, m->h, 1 }));
        }
        return true;
    }

    // Strips: the monitor's picture rows in `strips` bands, each drawn by its own pl_render_image with the slide of its
    // middle row. The canvas is cleared first, the strips skip their own border fill.
    struct pl_frame clear;
    pp_canvas_frame(&clear, canvas);
    pl_frame_clear_rgba(s1->g->gpu, &clear, (const float[4]) {0});
    struct pl_render_params sp = s1->params;
    sp.border = PL_CLEAR_SKIP;
    double u0, du_px, v0, dv_py;
    pp_fold_axes(room, k, &u0, &du_px, &v0, &dv_py);
    int rows = rect[3] - rect[1];
    for (int s = 0; s < opts->strips; s++) {
        int a = rect[1] + (int) ((long long) rows * s / opts->strips);
        int b = rect[1] + (int) ((long long) rows * (s + 1) / opts->strips);
        if (b <= a)
            continue;
        double t = scn->has_slide ? scn->slide.t_top_ms[k] + opts->extra_slide_ms + 0.5 * (a + b) / m->h * m->scan_ms : 0.0;
        double du = scn->has_slide ? scn->slide.vu * (t - scn->slide.t_ref_ms) / 1000.0 : 0.0;
        double dv = scn->has_slide ? scn->slide.vv * (t - scn->slide.t_ref_ms) / 1000.0 : 0.0;
        double sx0, sy0, sx1, sy1;
        pp_picture_uv_to_src(pic, u0 + du_px * rect[0] - du, v0 + dv_py * a - dv, &sx0, &sy0);
        pp_picture_uv_to_src(pic, u0 + du_px * rect[2] - du, v0 + dv_py * b - dv, &sx1, &sy1);
        img.crop = (pl_rect2df) { (float) (sx0 + opts->jitter_px), (float) sy0, (float) (sx1 + opts->jitter_px), (float) sy1 };
        int srect[4] = { rect[0], a, rect[2], b };
        make_target(&tgt, canvas, srect);
        if (!pl_render_image(s1->rr[k], &img, &tgt, &sp))
            return false;
    }
    return true;
}
