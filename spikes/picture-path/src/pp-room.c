// Room model loading, see pp-room.h.
#include "pp-room.h"

#include <math.h>
#include <stdio.h>
#include <string.h>

#include "pp-json.h"
#include "pp-util.h"

typedef struct { double m[3][3]; } mat3;

static mat3 mul(mat3 a, mat3 b)
{
    mat3 r;
    for (int i = 0; i < 3; i++)
        for (int j = 0; j < 3; j++)
            r.m[i][j] = a.m[i][0] * b.m[0][j] + a.m[i][1] * b.m[1][j] + a.m[i][2] * b.m[2][j];
    return r;
}

// Right-hand rotations about +y, +x, +z: R = Ry(yaw) Rx(pitch) Rz(roll). A negative yaw turns a right-hand panel toward the eye.
static mat3 rotation(double yaw, double pitch, double roll)
{
    double c, s;
    c = cos(yaw * M_PI / 180.0), s = sin(yaw * M_PI / 180.0);
    mat3 ry = {{{ c, 0, s }, { 0, 1, 0 }, { -s, 0, c }}};
    c = cos(pitch * M_PI / 180.0), s = sin(pitch * M_PI / 180.0);
    mat3 rx = {{{ 1, 0, 0 }, { 0, c, -s }, { 0, s, c }}};
    c = cos(roll * M_PI / 180.0), s = sin(roll * M_PI / 180.0);
    mat3 rz = {{{ c, -s, 0 }, { s, c, 0 }, { 0, 0, 1 }}};
    return mul(mul(ry, rx), rz);
}

double pp_row_to_local_y(double row, int h_px, double h_mm)
{
    return (0.5 - row / h_px) * h_mm;
}

double pp_local_y_to_row(double y_mm, int h_px, double h_mm)
{
    return (0.5 - y_mm / h_mm) * h_px;
}

struct pp_vec3 pp_monitor_point(const struct pp_monitor *m, double px, double py, double *lx, double *ly)
{
    double x = (px / m->w - 0.5) * m->w_mm;
    double y = pp_row_to_local_y(py, m->h, m->h_mm);
    if (lx)
        *lx = x;
    if (ly)
        *ly = y;
    return (struct pp_vec3) {
        m->centre.x + x * m->ex.x + y * m->ey.x,
        m->centre.y + x * m->ex.y + y * m->ey.y,
        m->centre.z + x * m->ex.z + y * m->ey.z,
    };
}

static bool vec3_from(const struct pp_json *a, struct pp_vec3 *v)
{
    if (!a || a->type != PP_J_ARR || a->n != 3)
        return false;
    v->x = pp_json_num(pp_json_at(a, 0), NAN);
    v->y = pp_json_num(pp_json_at(a, 1), NAN);
    v->z = pp_json_num(pp_json_at(a, 2), NAN);
    return !isnan(v->x) && !isnan(v->y) && !isnan(v->z);
}

#define FAIL(...) do { snprintf(err, errsz, __VA_ARGS__); pp_json_free(root); free(text); return false; } while (0)

bool pp_room_load(struct pp_room *r, const char *path, char *err, size_t errsz)
{
    memset(r, 0, sizeof(*r));
    char *text = pp_read_file(path, NULL);
    struct pp_json *root = pp_json_parse(text, err, errsz);
    if (!root) {
        free(text);
        return false;
    }
    if (strcmp(pp_json_str(pp_json_get(root, "schema"), ""), "panorama.room/1"))
        FAIL("%s: not a panorama.room/1 file", path);
    snprintf(r->name, sizeof(r->name), "%s", pp_json_str(pp_json_get(root, "name"), path));
    const struct pp_json *seat = pp_json_get(root, "seat");
    r->seat_distance = pp_json_num(pp_json_get(seat, "distance_mm"), 0.0);
    if (r->seat_distance <= 0)
        FAIL("%s: seat.distance_mm missing", path);
    if (fabs(pp_json_num(pp_json_get(seat, "gaze_yaw_deg"), 0.0)) > 1e-12 ||
        fabs(pp_json_num(pp_json_get(seat, "gaze_pitch_deg"), 0.0)) > 1e-12)
        FAIL("%s: this spike only handles a gaze along +z", path);

    const struct pp_json *mons = pp_json_get(root, "monitors");
    if (!mons || mons->type != PP_J_ARR || mons->n < 1 || mons->n > PP_MAX_MON)
        FAIL("%s: monitors must list 1 to %d entries", path, PP_MAX_MON);
    r->nmon = mons->n;
    for (int k = 0; k < r->nmon; k++) {
        const struct pp_json *d = pp_json_at(mons, k);
        struct pp_monitor *m = &r->mon[k];
        snprintf(m->id, sizeof(m->id), "%s", pp_json_str(pp_json_get(d, "id"), ""));
        const struct pp_json *panel = pp_json_get(d, "panel");
        m->w = (int) pp_json_num(pp_json_at(pp_json_get(panel, "native_px"), 0), 0);
        m->h = (int) pp_json_num(pp_json_at(pp_json_get(panel, "native_px"), 1), 0);
        m->w_mm = pp_json_num(pp_json_at(pp_json_get(panel, "active_area_mm"), 0), 0);
        m->h_mm = pp_json_num(pp_json_at(pp_json_get(panel, "active_area_mm"), 1), 0);
        if (!m->id[0] || m->w <= 0 || m->h <= 0 || m->w_mm <= 0 || m->h_mm <= 0)
            FAIL("%s: monitor %d has no id, native_px or active_area_mm", path, k);
        const struct pp_json *pose = pp_json_get(d, "pose");
        if (!vec3_from(pp_json_get(pose, "centre_mm"), &m->centre))
            FAIL("%s: monitor %s has no pose.centre_mm", path, m->id);
        mat3 rot = rotation(pp_json_num(pp_json_get(pose, "yaw_deg"), 0), pp_json_num(pp_json_get(pose, "pitch_deg"), 0),
                            pp_json_num(pp_json_get(pose, "roll_deg"), 0));
        m->ex = (struct pp_vec3) { rot.m[0][0], rot.m[1][0], rot.m[2][0] };
        m->ey = (struct pp_vec3) { rot.m[0][1], rot.m[1][1], rot.m[2][1] };
        m->ez = (struct pp_vec3) { rot.m[0][2], rot.m[1][2], rot.m[2][2] };
        const struct pp_json *t = pp_json_get(d, "timing");
        m->refresh_hz = pp_json_num(pp_json_get(t, "refresh_hz"), 60.0);
        m->scan_ms = pp_json_num(pp_json_get(t, "scan_ms"), 1000.0 / m->refresh_hz);
        m->lag_ms = pp_json_num(pp_json_get(t, "lag_ms"), 0.0);
    }
    const struct pp_json *seams = pp_json_get(pp_json_get(root, "derived"), "seams");
    int nseams = seams && seams->type == PP_J_ARR ? seams->n : 0;
    if (nseams != r->nmon - 1)
        FAIL("%s: derived.seams must list one gap per neighbouring pair", path);
    for (int k = 0; k < nseams; k++)
        r->gap[k] = pp_json_num(pp_json_get(pp_json_at(seams, k), "gap_mm"), NAN);
    r->u0[0] = 0.0;
    for (int k = 1; k < r->nmon; k++) {
        if (isnan(r->gap[k - 1]))
            FAIL("%s: seam %d has no gap_mm", path, k - 1);
        r->u0[k] = r->u0[k - 1] + r->mon[k - 1].w_mm + r->gap[k - 1];
    }
    r->u_anchor = r->nmon >= 2 ? r->u0[1] - 0.5 * r->gap[0] : 0.5 * r->mon[0].w_mm;
    pp_json_free(root);
    free(text);
    return true;
}
