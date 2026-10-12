// Pixel -> source map in double precision, see pp-geometry.h.
#include "pp-geometry.h"

#include <math.h>

void pp_picture_init(struct pp_picture *p, int src_w, int src_h, double sar, const double *crop, double uc, double vc,
                     double height_mm)
{
    if (crop) {
        for (int i = 0; i < 4; i++)
            p->crop[i] = crop[i];
    } else {
        p->crop[0] = 0.0;
        p->crop[1] = 0.0;
        p->crop[2] = src_w;
        p->crop[3] = src_h;
    }
    p->cw = p->crop[2] - p->crop[0];
    p->ch = p->crop[3] - p->crop[1];
    p->height = height_mm;
    p->width = height_mm * p->cw / p->ch * (sar > 0 ? sar : 1.0);
    p->uc = uc;
    p->vc = vc;
    p->u_left = uc - 0.5 * p->width;
    p->v_top = vc + 0.5 * p->height;
}

void pp_picture_uv_to_src(const struct pp_picture *p, double u, double v, double *sx, double *sy)
{
    *sx = p->crop[0] + (u - p->u_left) / p->width * p->cw;
    *sy = p->crop[1] + (p->v_top - v) / p->height * p->ch;      // v is up, source rows go down
}

// Ray from the eye through `pt` onto the cylinder of radius 1/curvature tangent to the gaze at distance D (axis at
// z = D - R); u = R * angle around the axis, v = height of the hit. curvature 0 is the plane z = D. Mirrors pp_geometry.py.
static void curve_uv(struct pp_vec3 pt, double distance, double curvature, double *u, double *v)
{
    double n = sqrt(pt.x * pt.x + pt.y * pt.y + pt.z * pt.z);
    double dx = pt.x / n, dy = pt.y / n, dz = pt.z / n;
    if (fabs(curvature) < 1e-12) {
        double t = distance / dz;
        *u = t * dx;
        *v = t * dy;
        return;
    }
    double big_r = 1.0 / curvature;
    double za = distance - big_r;
    double a = dx * dx + dz * dz, b = dz * za, c = za * za - big_r * big_r;
    double t = (b + sqrt(b * b - a * c)) / a;
    double hx = t * dx, hy = t * dy, hz = t * dz;
    *u = big_r * atan2(hx, hz - za);
    *v = hy;
}

void pp_monitor_uv(const struct pp_room *r, int k, double px, double py, const struct pp_surface *s, double *u, double *v)
{
    const struct pp_monitor *m = &r->mon[k];
    double lx, ly;
    struct pp_vec3 pt = pp_monitor_point(m, px, py, &lx, &ly);
    if (s->model == PP_SURFACE_FOLD) {
        *u = r->u0[k] + lx + 0.5 * m->w_mm - r->u_anchor;
        *v = m->centre.y + ly;
    } else {
        curve_uv(pt, r->seat_distance, s->curvature, u, v);
    }
}

void pp_map_to_source(const struct pp_room *r, int k, const struct pp_picture *p, const struct pp_surface *s,
                      const struct pp_slide *slide, double px, double py, double *sx, double *sy)
{
    double u, v;
    pp_monitor_uv(r, k, px, py, s, &u, &v);
    if (slide) {
        const struct pp_monitor *m = &r->mon[k];
        double t = slide->t_top_ms[k] + py / m->h * m->scan_ms;                // ms: this row's light time
        u -= slide->vu * (t - slide->t_ref_ms) / 1000.0;
        v -= slide->vv * (t - slide->t_ref_ms) / 1000.0;
    }
    pp_picture_uv_to_src(p, u, v, sx, sy);
}

void pp_source_jacobian(const struct pp_room *r, int k, const struct pp_picture *p, const struct pp_surface *s,
                        const struct pp_slide *slide, double px, double py, double *sx, double *sy, double j[4])
{
    double xp, yp, xm, ym, xq, yq, xn, yn;
    pp_map_to_source(r, k, p, s, slide, px, py, sx, sy);
    pp_map_to_source(r, k, p, s, slide, px + 0.5, py, &xp, &yp);
    pp_map_to_source(r, k, p, s, slide, px - 0.5, py, &xm, &ym);
    pp_map_to_source(r, k, p, s, slide, px, py + 0.5, &xq, &yq);
    pp_map_to_source(r, k, p, s, slide, px, py - 0.5, &xn, &yn);
    j[0] = xp - xm;
    j[1] = xq - xn;
    j[2] = yp - ym;
    j[3] = yq - yn;
}

bool pp_monitor_upscales(const struct pp_room *r, int k, const struct pp_picture *p, const struct pp_surface *s)
{
    double sx, sy, j[4];
    pp_source_jacobian(r, k, p, s, NULL, r->mon[k].w / 2.0, r->mon[k].h / 2.0, &sx, &sy, j);
    double a = j[0] * j[0] + j[1] * j[1], b = j[0] * j[2] + j[1] * j[3], c = j[2] * j[2] + j[3] * j[3];
    double dl = sqrt(0.25 * (a - c) * (a - c) + b * b);
    double smax = sqrt(0.5 * (a + c) + dl), smin = sqrt(fmax(0.5 * (a + c) - dl, 0.0));
    if (smax > 1.0 + 1e-6)
        return false;                                       // shrunk: linear light
    return !(fabs(smax - 1.0) <= 1e-6 && fabs(smin - 1.0) <= 1e-6); // exactly 1:1 is a no-op, no sigmoid either
}

void pp_fold_axes(const struct pp_room *r, int k, double *u_px0, double *du_dpx, double *v_py0, double *dv_dpy)
{
    const struct pp_monitor *m = &r->mon[k];
    *du_dpx = m->w_mm / m->w;
    *u_px0 = r->u0[k] - r->u_anchor;                       // u of the left edge (px = 0)
    double ly0 = pp_row_to_local_y(0.0, m->h, m->h_mm);
    double ly1 = pp_row_to_local_y(1.0, m->h, m->h_mm);
    *v_py0 = m->centre.y + ly0;
    *dv_dpy = ly1 - ly0;
}
