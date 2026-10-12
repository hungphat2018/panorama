// Pixel -> source map in double precision, see perf1-picture-map.h.
#include "perf1-picture-map.h"

#include <math.h>

void pic_map_pixel(const struct room_geom *g, const struct pic_view *v, const struct pic_slide *s, double px, double py,
                   double *sx, double *sy)
{
    double u, w;
    room_map_uv(g, px, py, &u, &w);
    if (s) {
        u -= s->du0 + s->ddu * py;
        w -= s->dv0 + s->ddv * py;
    }
    *sx = v->crop[0] + (u - v->rect[0]) / (v->rect[2] - v->rect[0]) * (v->crop[2] - v->crop[0]);
    *sy = v->crop[1] + (w - v->rect[1]) / (v->rect[3] - v->rect[1]) * (v->crop[3] - v->crop[1]);
}

void pic_jacobian(const struct room_geom *g, const struct pic_view *v, const struct pic_slide *s, double px, double py,
                  double j[4])
{
    double xp, yp, xm, ym, xq, yq, xn, yn;
    pic_map_pixel(g, v, s, px + 0.5, py, &xp, &yp);
    pic_map_pixel(g, v, s, px - 0.5, py, &xm, &ym);
    pic_map_pixel(g, v, s, px, py + 0.5, &xq, &yq);
    pic_map_pixel(g, v, s, px, py - 0.5, &xn, &yn);
    j[0] = xp - xm;
    j[1] = xq - xn;
    j[2] = yp - ym;
    j[3] = yq - yn;
}

bool pic_monitor_upscales(const struct room_geom *g, const struct pic_view *v)
{
    double j[4];
    pic_jacobian(g, v, NULL, g->px_w / 2.0, g->px_h / 2.0, j);
    double a = j[0] * j[0] + j[1] * j[1], b = j[0] * j[2] + j[1] * j[3], c = j[2] * j[2] + j[3] * j[3];
    double dl = sqrt(0.25 * (a - c) * (a - c) + b * b);
    double smax = sqrt(0.5 * (a + c) + dl), smin = sqrt(fmax(0.5 * (a + c) - dl, 0.0));
    if (smax > 1.0 + 1e-6)
        return false; // shrunk: linear light
    return !(fabs(smax - 1.0) <= 1e-6 && fabs(smin - 1.0) <= 1e-6);
}
