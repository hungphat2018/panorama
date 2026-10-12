// Room file loading and the pixel -> surface map, see perf1-room.h.
#include "perf1-room.h"

#include "perf1-json.h"

#include <math.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define ROOM_MAX_FILE_BYTES (1u << 20)

// ---- helpers -------------------------------------------------------------------------------------------------

static bool fail(char *err, size_t n, const char *source, const char *fmt, ...) __attribute__((format(gnu_printf, 4, 5)));
static bool fail(char *err, size_t n, const char *source, const char *fmt, ...)
{
    if (err && n) {
        int w = snprintf(err, n, "%s: ", source);
        va_list ap;
        va_start(ap, fmt);
        if (w > 0 && (size_t) w < n)
            vsnprintf(err + w, n - (size_t) w, fmt, ap);
        va_end(ap);
    }
    return false;
}

static bool num_of(const struct json_value *v, double *out)
{
    if (!v || v->type != JSON_NUMBER || !isfinite(v->num))
        return false;
    *out = v->num;
    return true;
}

// A member that is a number, or `dflt` when absent. False when present and not a finite number.
static bool opt_num(const struct json_value *obj, const char *key, double dflt, double *out)
{
    const struct json_value *v = json_member(obj, key);
    *out = dflt;
    return !v || v->type == JSON_NULL || num_of(v, out);
}

// n numbers from an array member.
static bool num_array(const struct json_value *v, size_t n, double *out)
{
    if (!v || v->type != JSON_ARRAY || v->n != n)
        return false;
    for (size_t i = 0; i < n; i++)
        if (!num_of(&v->items[i], &out[i]))
            return false;
    return true;
}

// A number (applied to all three channels) or an array of three, absent = dflt.
static bool channels(const struct json_value *obj, const char *key, double dflt, double out[3])
{
    const struct json_value *v = json_member(obj, key);
    for (int i = 0; i < 3; i++)
        out[i] = dflt;
    if (!v || v->type == JSON_NULL)
        return true;
    if (v->type == JSON_NUMBER) {
        double x;
        if (!num_of(v, &x))
            return false;
        out[0] = out[1] = out[2] = x;
        return true;
    }
    return num_array(v, 3, out);
}

// Right-hand rotations about +y (yaw), +x (pitch), +z (roll): R = Ry Rx Rz. A negative yaw turns a right-hand panel
// toward the eye. Columns 0 and 1 of R are the panel's right and up axes.
static void panel_axes(double yaw_deg, double pitch_deg, double roll_deg, struct room_vec3 *ex, struct room_vec3 *ey)
{
    const double k = 3.14159265358979323846 / 180.0;
    double cy = cos(yaw_deg * k), sy = sin(yaw_deg * k), cx = cos(pitch_deg * k), sx = sin(pitch_deg * k);
    double cz = cos(roll_deg * k), sz = sin(roll_deg * k);
    double ry[3][3] = {{ cy, 0, sy }, { 0, 1, 0 }, { -sy, 0, cy }};
    double rx[3][3] = {{ 1, 0, 0 }, { 0, cx, -sx }, { 0, sx, cx }};
    double rz[3][3] = {{ cz, -sz, 0 }, { sz, cz, 0 }, { 0, 0, 1 }};
    double t[3][3], r[3][3];
    for (int i = 0; i < 3; i++)
        for (int j = 0; j < 3; j++)
            t[i][j] = rx[i][0] * rz[0][j] + rx[i][1] * rz[1][j] + rx[i][2] * rz[2][j];
    for (int i = 0; i < 3; i++)
        for (int j = 0; j < 3; j++)
            r[i][j] = ry[i][0] * t[0][j] + ry[i][1] * t[1][j] + ry[i][2] * t[2][j];
    *ex = (struct room_vec3) { r[0][0], r[1][0], r[2][0] };
    *ey = (struct room_vec3) { r[0][1], r[1][1], r[2][1] };
}

// ---- loading ---------------------------------------------------------------------------------------------------

static bool parse_color(struct room_color *c, const struct json_value *js, const char *source, int k, char *err, size_t n)
{
    *c = (struct room_color) { .acm = true, .gain = { 1, 1, 1 }, .white = { 1, 1, 1 }, .power = 2.2 };
    if (!js || js->type == JSON_NULL)
        return true;
    if (js->type != JSON_OBJECT)
        return fail(err, n, source, "monitors[%d].color must be an object", k);
    const struct json_value *acm = json_member(js, "acm");
    if (acm && acm->type != JSON_BOOL)
        return fail(err, n, source, "monitors[%d].color.acm must be true or false", k);
    if (acm)
        c->acm = acm->num != 0.0;
    if (!channels(js, "gain", 1.0, c->gain) || !channels(js, "white", 1.0, c->white) ||
        !channels(js, "black_lift", 0.0, c->lift))
        return fail(err, n, source, "monitors[%d].color: gain, white and black_lift are a number or [r, g, b]", k);
    for (int i = 0; i < 3; i++)
        if (!(c->gain[i] > 0.0) || !(c->white[i] > 0.0) || c->lift[i] < 0.0 || c->lift[i] >= 1.0)
            return fail(err, n, source, "monitors[%d].color: gain and white must be positive, black_lift in [0, 1)", k);
    const struct json_value *tr = json_member(js, "transfer");
    if (tr && (tr->type != JSON_STRING || (strcmp(tr->str, "srgb") && strcmp(tr->str, "power"))))
        return fail(err, n, source, "monitors[%d].color.transfer must be \"srgb\" or \"power\"", k);
    c->power_trc = tr && !strcmp(tr->str, "power");
    if (!opt_num(js, "power", 2.2, &c->power) || !(c->power > 0.5 && c->power < 5.0))
        return fail(err, n, source, "monitors[%d].color.power must be a number from 0.5 to 5", k);
    const struct json_value *pr = json_member(js, "primaries");
    if (pr && pr->type != JSON_NULL) {
        static const char *const names[4] = { "r", "g", "b", "w" };
        for (int i = 0; i < 4; i++)
            if (!num_array(json_member(pr, names[i]), 2, c->primaries[i]))
                return fail(err, n, source, "monitors[%d].color.primaries.%s must be [x, y]", k, names[i]);
        c->has_primaries = true;
    }
    return true;
}

static bool parse_monitor(struct room *r, int k, const struct json_value *d, const char *source, char *err, size_t n)
{
    struct room_monitor *m = &r->mon[k];
    const struct json_value *id = json_member(d, "id");
    const struct json_value *panel = json_member(d, "panel");
    const struct json_value *pose = json_member(d, "pose");
    double px[2], mm[2], ctr[3], yaw, pitch, roll;
    if (d->type != JSON_OBJECT || !id || id->type != JSON_STRING || !id->str[0])
        return fail(err, n, source, "monitors[%d] needs an \"id\"", k);
    snprintf(m->id, sizeof(m->id), "%s", id->str);
    if (!num_array(json_member(panel, "native_px"), 2, px) || px[0] < 1 || px[1] < 1 || px[0] > 32768 || px[1] > 32768 ||
        px[0] != floor(px[0]) || px[1] != floor(px[1]))
        return fail(err, n, source, "monitors[%d].panel.native_px must be two whole numbers [width, height]", k);
    if (!num_array(json_member(panel, "active_area_mm"), 2, mm) || !(mm[0] > 0.0) || !(mm[1] > 0.0))
        return fail(err, n, source, "monitors[%d].panel.active_area_mm must be two positive numbers", k);
    if (!num_array(json_member(pose, "centre_mm"), 3, ctr) || !opt_num(pose, "yaw_deg", 0, &yaw) ||
        !opt_num(pose, "pitch_deg", 0, &pitch) || !opt_num(pose, "roll_deg", 0, &roll))
        return fail(err, n, source, "monitors[%d].pose needs centre_mm [x, y, z] and numeric angles", k);
    const struct json_value *t = json_member(d, "timing");
    if (!opt_num(t, "refresh_hz", 60.0, &m->refresh_hz) || !opt_num(t, "scan_ms", 0.0, &m->scan_ms) ||
        !opt_num(t, "lag_ms", 0.0, &m->lag_ms))
        return fail(err, n, source, "monitors[%d].timing holds numbers", k);
    m->w = (int) px[0];
    m->h = (int) px[1];
    m->w_mm = mm[0];
    m->h_mm = mm[1];
    m->centre = (struct room_vec3) { ctr[0], ctr[1], ctr[2] };
    panel_axes(yaw, pitch, roll, &m->ex, &m->ey);
    return parse_color(&m->color, json_member(d, "color"), source, k, err, n);
}

bool room_parse(struct room *r, const char *text, size_t len, const char *source, char *err, size_t err_size)
{
    memset(r, 0, sizeof(*r));
    char jerr[200];
    struct json_value *root = json_parse(text, len, jerr, sizeof(jerr));
    if (!root)
        return fail(err, err_size, source, "not valid JSON: %s", jerr);
    bool ok = false;
    do {
        const struct json_value *schema = json_member(root, "schema");
        if (!schema || schema->type != JSON_STRING || strcmp(schema->str, "panorama.room/1")) {
            fail(err, err_size, source, "\"schema\" must be \"panorama.room/1\"");
            break;
        }
        const struct json_value *name = json_member(root, "name");
        snprintf(r->name, sizeof(r->name), "%s", name && name->type == JSON_STRING ? name->str : source);
        const struct json_value *seat = json_member(root, "seat");
        double yaw, pitch;
        if (!num_of(json_member(seat, "distance_mm"), &r->seat_distance) || !(r->seat_distance > 0.0)) {
            fail(err, err_size, source, "seat.distance_mm must be a positive number");
            break;
        }
        if (!opt_num(seat, "gaze_yaw_deg", 0, &yaw) || !opt_num(seat, "gaze_pitch_deg", 0, &pitch) ||
            fabs(yaw) > 1e-12 || fabs(pitch) > 1e-12) {
            fail(err, err_size, source, "only a gaze along +z is supported (gaze_yaw_deg and gaze_pitch_deg 0)");
            break;
        }
        const struct json_value *mons = json_member(root, "monitors");
        if (!mons || mons->type != JSON_ARRAY || mons->n < 1 || mons->n > ROOM_MAX_MON) {
            fail(err, err_size, source, "monitors must list 1 to %d entries", ROOM_MAX_MON);
            break;
        }
        r->nmon = (int) mons->n;
        bool good = true;
        for (int k = 0; k < r->nmon && good; k++)
            good = parse_monitor(r, k, &mons->items[k], source, err, err_size);
        if (!good)
            break;
        const struct json_value *seams = json_member(json_member(root, "derived"), "seams");
        size_t nseams = seams && seams->type == JSON_ARRAY ? seams->n : 0;
        if (nseams != (size_t) r->nmon - 1) {
            fail(err, err_size, source, "derived.seams must list one gap_mm per neighbouring pair (%d)", r->nmon - 1);
            break;
        }
        for (int k = 0; k + 1 < r->nmon && good; k++) {
            good = num_of(json_member(&seams->items[k], "gap_mm"), &r->gap[k]) && r->gap[k] >= 0.0;
            if (!good)
                fail(err, err_size, source, "derived.seams[%d].gap_mm must be a number from 0", k);
        }
        if (!good)
            break;
        for (int k = 1; k < r->nmon; k++)
            r->mon[k].left_mm = r->mon[k - 1].left_mm + r->mon[k - 1].w_mm + r->gap[k - 1];
        r->u_axis = r->nmon >= 2 ? r->mon[1].left_mm - 0.5 * r->gap[0] : 0.5 * r->mon[0].w_mm;
        const struct json_value *surf = json_member(root, "surface");
        const struct json_value *model = json_member(surf, "model");
        if (model && (model->type != JSON_STRING || (strcmp(model->str, "fold") && strcmp(model->str, "curve")))) {
            fail(err, err_size, source, "surface.model must be \"fold\" or \"curve\"");
            break;
        }
        r->curve = model && !strcmp(model->str, "curve");
        if (!opt_num(surf, "curvature", 1.0 / r->seat_distance, &r->curvature) || fabs(r->curvature) > 0.01) {
            fail(err, err_size, source, "surface.curvature must be a number (1 / radius in 1 / mm) up to 0.01");
            break;
        }
        ok = true;
    } while (0);
    json_free(root);
    return ok;
}

bool room_load(struct room *r, const char *path, char *err, size_t err_size)
{
    FILE *f = fopen(path, "rb");
    if (!f)
        return fail(err, err_size, path, "cannot open the room file");
    fseek(f, 0, SEEK_END);
    long size = ftell(f);
    fseek(f, 0, SEEK_SET);
    if (size <= 0 || (unsigned long) size > ROOM_MAX_FILE_BYTES) {
        fclose(f);
        return fail(err, err_size, path, "empty or larger than %u bytes", ROOM_MAX_FILE_BYTES);
    }
    char *buf = malloc((size_t) size);
    size_t got = buf ? fread(buf, 1, (size_t) size, f) : 0;
    fclose(f);
    bool ok = got == (size_t) size && room_parse(r, buf, got, path, err, err_size);
    if (got != (size_t) size && buf)
        fail(err, err_size, path, "short read");
    free(buf);
    return ok;
}

// ---- vertical placement ---------------------------------------------------------------------------------------

// Height (up) of a monitor's top edge in the seat frame.
static double top_edge_y(const struct room *r, int k)
{
    return r->mon[k].centre.y + 0.5 * r->mon[k].h_mm * r->mon[k].ey.y;
}

double room_top_mm(const struct room *r, int k, int master)
{
    return top_edge_y(r, master) - top_edge_y(r, k);
}

double room_eye_v(const struct room *r, int master)
{
    return top_edge_y(r, master);
}

// ---- the map -----------------------------------------------------------------------------------------------------

void room_geom_fold(struct room_geom *g, int px_w, int px_h, double w_mm, double h_mm, double left_mm, double top_mm)
{
    memset(g, 0, sizeof(*g));
    g->px_w = px_w;
    g->px_h = px_h;
    g->w_mm = w_mm;
    g->h_mm = h_mm;
    g->left_mm = left_mm;
    g->top_mm = top_mm;
}

void room_geom_curve(struct room_geom *g, const struct room *r, int k, int px_w, int px_h, int master)
{
    const struct room_monitor *m = &r->mon[k];
    room_geom_fold(g, px_w, px_h, m->w_mm, m->h_mm, m->left_mm, room_top_mm(r, k, master));
    g->curve = true;
    g->centre = m->centre;
    g->ex = m->ex;
    g->ey = m->ey;
    g->seat_distance = r->seat_distance;
    g->curvature = r->curvature;
    g->u_axis = r->u_axis;
    g->v_eye = room_eye_v(r, master);
}

void room_map_uv(const struct room_geom *g, double px, double py, double *u, double *v)
{
    if (!g->curve) {
        *u = g->left_mm + px / g->px_w * g->w_mm;
        *v = g->top_mm + py / g->px_h * g->h_mm;
        return;
    }
    double lx = (px / g->px_w - 0.5) * g->w_mm, ly = (0.5 - py / g->px_h) * g->h_mm;
    double x = g->centre.x + lx * g->ex.x + ly * g->ey.x;
    double y = g->centre.y + lx * g->ex.y + ly * g->ey.y;
    double z = g->centre.z + lx * g->ex.z + ly * g->ey.z;
    double len = sqrt(x * x + y * y + z * z);
    double dx = x / len, dy = y / len, dz = z / len;
    double du, dv;
    if (fabs(g->curvature) < 1e-12) {
        double t = g->seat_distance / dz;
        du = t * dx;
        dv = t * dy;
    } else {
        // the ray meets the cylinder (axis parallel to y, at z = distance - radius): u is the arc length from the gaze axis
        double radius = 1.0 / g->curvature, za = g->seat_distance - radius;
        double a = dx * dx + dz * dz, b = dz * za, c = za * za - radius * radius;
        double t = (b + sqrt(b * b - a * c)) / a;
        du = radius * atan2(t * dx, t * dz - za);
        dv = t * dy;
    }
    *u = g->u_axis + du;
    *v = g->v_eye - dv;
}
