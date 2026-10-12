// Per-refresh CPU arithmetic of the picture path, see perf1-picture-plan.h.
#include "perf1-picture-plan.h"

#include <math.h>
#include <string.h>

#define BLEND_EPS 1e-4

double pic_film_time(const struct pic_clock *c, double host_t)
{
    return c->origin + ((host_t - c->l0) / c->master_period - (double) c->shift) * c->step_s;
}

bool pic_row_slide(const struct cue_shot *shot, const struct pic_clock *c, double a_top, double scan_s, double period,
                   int rows, struct pic_slide *out)
{
    memset(out, 0, sizeof(*out));
    if (shot->slide_n < 2 || rows < 1)
        return false;
    double du0, dv0, du1, dv1;
    cue_slide_eval(shot, pic_film_time(c, a_top + 0.5 * period), &du0, &dv0);
    cue_slide_eval(shot, pic_film_time(c, a_top + scan_s + 0.5 * period), &du1, &dv1);
    out->du0 = du0;
    out->dv0 = dv0;
    out->ddu = (du1 - du0) / (double) rows;
    out->ddv = (dv1 - dv0) / (double) rows;
    return true;
}

void pic_blend_line(double a_top, double scan_s, double period, int rows, double e0, double e1, double *wa, double *wb)
{
    *wa = (a_top + period - e0) / period;
    *wb = (scan_s - (e1 - e0)) / (period * (double) rows);
}

static double clamp01(double x)
{
    return x < 0.0 ? 0.0 : x > 1.0 ? 1.0 : x;
}

// Smallest and largest of clamp(wa + wb * y) over the rows 0..rows (a straight line: the ends decide).
static void line_range(int rows, double wa, double wb, double *lo, double *hi)
{
    double w0 = clamp01(wa), w1 = clamp01(wa + wb * (double) rows);
    *lo = w0 < w1 ? w0 : w1;
    *hi = w0 < w1 ? w1 : w0;
}

struct pic_blend pic_blend_choose(int rows, bool ok_prev, double wa_prev, double wb_prev, bool ok_next, double wa_next,
                                  double wb_next)
{
    struct pic_blend b = {0};
    double lo, hi;
    if (ok_prev) {
        line_range(rows, wa_prev, wb_prev, &lo, &hi);
        if (lo < 1.0 - BLEND_EPS) { // some row of this refresh is still lit before the master's switch to c
            b = (struct pic_blend) { -1, wa_prev, wb_prev };
            return b;
        }
    }
    if (ok_next) {
        line_range(rows, wa_next, wb_next, &lo, &hi);
        if (hi > BLEND_EPS) // some row already follows the master's switch to c+1
            b = (struct pic_blend) { +1, wa_next, wb_next };
    }
    return b;
}
