// Picture layout per shot, see perf1-layout.h. The default case is the arithmetic perf1 always used for its single
// fixed picture, so a sheet without crops or placements puts every pixel where it was.
#include "perf1-layout.h"

#include <math.h>
#include <string.h>

void layout_defaults(const struct lay_monitor *mon, int n, double picture_height_mm, double picture_center_mm,
                     struct lay_defaults *out)
{
    double band_top = mon[0].top_mm, band_bottom = mon[0].top_mm + mon[0].h_mm;
    double left = mon[0].left_mm, right = mon[0].left_mm + mon[0].w_mm;
    for (int i = 1; i < n; i++) {
        if (mon[i].top_mm > band_top) band_top = mon[i].top_mm;
        if (mon[i].top_mm + mon[i].h_mm < band_bottom) band_bottom = mon[i].top_mm + mon[i].h_mm;
        if (mon[i].left_mm < left) left = mon[i].left_mm;
        if (mon[i].left_mm + mon[i].w_mm > right) right = mon[i].left_mm + mon[i].w_mm;
    }
    out->height_mm = picture_height_mm > 0 ? picture_height_mm : band_bottom - band_top;
    out->top_v_mm = band_top;
    if (!isnan(picture_center_mm)) {
        out->center_u_mm = picture_center_mm;
    } else if (n >= 2) {
        // middle of the gap between the two leftmost monitors
        int a = 0, b = -1;
        for (int i = 1; i < n; i++)
            if (mon[i].left_mm < mon[a].left_mm) a = i;
        for (int i = 0; i < n; i++)
            if (i != a && (b < 0 || mon[i].left_mm < mon[b].left_mm)) b = i;
        out->center_u_mm = 0.5 * (mon[a].left_mm + mon[a].w_mm + mon[b].left_mm);
    } else {
        out->center_u_mm = 0.5 * (left + right);
    }
}

void layout_shot(const struct cue_shot *shot, double src_w, double src_h, double sar, const struct lay_monitor *mon,
                 int n, const struct lay_defaults *def, struct lay_picture *out)
{
    memset(out, 0, sizeof(*out));
    if (shot->has_crop) {
        memcpy(out->crop, shot->crop, sizeof(out->crop));
    } else {
        out->crop[0] = 0.0;
        out->crop[1] = 0.0;
        out->crop[2] = src_w;
        out->crop[3] = src_h;
    }
    const double cw = out->crop[2] - out->crop[0], ch = out->crop[3] - out->crop[1];
    out->aspect = ch > 0 ? cw / ch * (sar > 0 ? sar : 1.0) : 16.0 / 9.0;

    double h, cx, y0;
    if (shot->has_place) {
        h = shot->height_mm;
        cx = shot->center_u_mm;
        y0 = shot->center_v_mm - 0.5 * h;
    } else {
        h = def->height_mm;
        cx = def->center_u_mm;
        y0 = def->top_v_mm;
    }
    const double x0 = cx - 0.5 * h * out->aspect, x1 = cx + 0.5 * h * out->aspect, y1 = y0 + h;
    out->rect_mm[0] = x0;
    out->rect_mm[1] = y0;
    out->rect_mm[2] = x1;
    out->rect_mm[3] = y1;

    for (int i = 0; i < n && i < LAY_MAX_MON; i++) {
        const struct lay_monitor *m = &mon[i];
        struct lay_share *s = &out->share[i];
        double top = m->top_mm;
        double ix0 = fmax(m->left_mm, x0), ix1 = fmin(m->left_mm + m->w_mm, x1);
        double iy0 = fmax(top, y0), iy1 = fmin(top + m->h_mm, y1);
        if (ix1 <= ix0 || iy1 <= iy0)
            continue;
        double sx = m->sc_w / m->w_mm, sy = m->sc_h / m->h_mm; // pixels per millimetre
        s->tx0 = (int) lround((ix0 - m->left_mm) * sx);
        s->tx1 = (int) lround((ix1 - m->left_mm) * sx);
        s->ty0 = (int) lround((iy0 - top) * sy);
        s->ty1 = (int) lround((iy1 - top) * sy);
        if (s->tx1 > m->sc_w) s->tx1 = m->sc_w;
        if (s->ty1 > m->sc_h) s->ty1 = m->sc_h;
        if (s->tx1 <= s->tx0 || s->ty1 <= s->ty0)
            continue;
        // the snapped pixel edges back to the picture's own coordinates (one resample, exact mapping)
        double pw = x1 - x0, ph = y1 - y0;
        s->u0 = (m->left_mm + s->tx0 / sx - x0) / pw;
        s->u1 = (m->left_mm + s->tx1 / sx - x0) / pw;
        s->v0 = (top + s->ty0 / sy - y0) / ph;
        s->v1 = (top + s->ty1 / sy - y0) / ph;
        s->has_picture = true;
    }
}
