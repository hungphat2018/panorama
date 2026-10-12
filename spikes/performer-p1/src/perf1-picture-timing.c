// Row times of one refresh, from the engine's film clock and the monitors' scan-line grids (ADR 0025): the camera-slide
// position of each row and, for the rolling blend, which neighbouring frame a follower's refresh mixes in and how much.
// The arithmetic itself is perf1-picture-plan.c.
#include "perf1-picture.h"

// The master's start record of frame ord, valid while the engine's ring still holds it.
static const struct frame_start *master_start(long ord)
{
    if (ord < 0 || ord >= A.engine.decided || ord <= A.engine.decided - ENGINE_FS_RING)
        return NULL;
    return &A.engine.fs[ord % ENGINE_FS_RING];
}

// New-frame line of the pair whose new frame starts on the master at fs: the master lights this monitor's top and bottom
// edge with it at e0 and e1.
static void new_frame_line(const struct frame_start *fs, const row_mon *master, const row_vgrid *mg, const row_mon *row,
                           double a_top, double period, int rows, double *wa, double *wb)
{
    double y0 = row->top_mm, y1 = row->top_mm + row->height_mm;
    double e0 = rowmon_light_at(master, mg, fs->j, y0), e1 = rowmon_light_at(master, mg, fs->j, y1);
    pic_blend_line(a_top, row->timing.scan_s, period, rows, e0, e1, wa, wb);
}

void picture_timing(const struct pic_refresh *r, struct pic_timing *out)
{
    memset(out, 0, sizeof(*out));
    if (!r->timed)
        return;
    struct monitor *m = r->m; // not const: vclock_line takes the clock's lock
    struct monitor *mas = &A.mon[A.master];
    const row_mon *row = &m->row;
    row_vgrid mg, fg;
    vclock_line(&mas->clock, &mg.base, &mg.period);
    vclock_line(&m->clock, &fg.base, &fg.period);
    const double a_top = rowmon_light(row, r->wake, row->top_mm); // top row of this refresh, panel lag included
    const int rows = m->sc_h;

    struct pic_clock clk = {
        .l0 = rowmon_light_at(&mas->row, &mg, A.engine.J0, A.aim_mm), .master_period = mg.period,
        .origin = A.engine.origin, .step_s = A.engine.cfg.step_s, .shift = A.engine.shift,
    };
    out->slide_on = pic_row_slide(r->cue, &clk, a_top, row->timing.scan_s, fg.period, rows, &out->slide);

    if (!PC.blend_rolling || m == mas || r->ord < 0)
        return;
    const struct frame_start *cur = master_start(r->ord), *next = master_start(r->ord + 1);
    double wa_p = 0, wb_p = 0, wa_n = 0, wb_n = 0;
    if (cur && r->ord >= 1)
        new_frame_line(cur, &mas->row, &mg, row, a_top, fg.period, rows, &wa_p, &wb_p);
    if (next)
        new_frame_line(next, &mas->row, &mg, row, a_top, fg.period, rows, &wa_n, &wb_n);
    out->blend = pic_blend_choose(rows, cur && r->ord >= 1, wa_p, wb_p, next != NULL, wa_n, wb_n);
    if (out->blend.partner) {
        const struct frame_start *p = master_start(r->ord + out->blend.partner);
        if (!p) {
            memset(&out->blend, 0, sizeof(out->blend));
            return;
        }
        out->partner_pts = p->pts;
        // the neighbouring frame may belong to another shot (a cut): it is seen through that shot's own crop, place and slide
        out->partner_shot = picture_shot_of_frame(p->pts);
        if (out->partner_shot)
            out->partner_slide_on = pic_row_slide(out->partner_shot->cue, &clk, a_top, row->timing.scan_s, fg.period, rows,
                                                  &out->partner_slide);
    }
}
