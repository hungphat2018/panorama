// The room model (panorama.room/1, the fields this spike needs): the seat frame, each monitor's pixels, active area and
// pose, and the fold gaps. Frame: millimetres, right-handed, +x right, +y UP, +z away from the eye, origin at the eye.
#pragma once

#include <stdbool.h>
#include <stddef.h>

#define PP_MAX_MON 4

struct pp_vec3 {
    double x, y, z;
};

struct pp_monitor {
    char id[32];
    int w, h;                       // native pixels
    double w_mm, h_mm;              // active area
    struct pp_vec3 centre;          // seat frame
    struct pp_vec3 ex, ey, ez;      // panel axes in the seat frame: right (pixel x), up (against the pixel rows), away
    double refresh_hz, scan_ms, lag_ms;
};

struct pp_room {
    char name[64];
    double seat_distance;           // mm from the eye to the surface straight ahead (the cylinder's tangent point)
    int nmon;
    struct pp_monitor mon[PP_MAX_MON];
    double gap[PP_MAX_MON];         // gap[k]: lateral gap between monitor k and k+1 (the distance the fold prints)
    double u0[PP_MAX_MON];          // fold: u of each monitor's left edge, unrolled from the first monitor's left edge
    double u_anchor;                // fold: u0-based position of the surface origin (the first seam's centre)
};

// Reads a room file. Returns false and fills err on any problem (missing field, bad value, unsupported gaze).
bool pp_room_load(struct pp_room *r, const char *path, char *err, size_t errsz);

// The monitor's local up coordinate and its pixel row: the monitor-side vertical flip, the only one besides the
// picture's (pp_picture_uv_to_src). `row` is continuous (row 0's top edge is 0, pixel centres are at +0.5).
double pp_row_to_local_y(double row, int h_px, double h_mm);
double pp_local_y_to_row(double y_mm, int h_px, double h_mm);

// Seat-frame point of a continuous pixel position, and the panel-local coordinates (mm from the panel centre).
struct pp_vec3 pp_monitor_point(const struct pp_monitor *m, double px, double py, double *lx, double *ly);
