// The room as the performer needs it (panorama.room/1): the seat, each monitor's pixels, active area, pose and colour, and
// the gaps between neighbours. Pure data and geometry: no GPU, no window, no OS calls, so it is tested without hardware.
//
// File (JSON, millimetres and degrees; frame = the seat: right-handed, +x right, +y UP, +z away from the eye to the
// screens, origin = the eye):
//   { "schema": "panorama.room/1", "name": "...",
//     "seat": { "distance_mm": 700, "gaze_yaw_deg": 0, "gaze_pitch_deg": 0 },           gaze must be along +z
//     "surface": { "model": "fold" | "curve", "curvature": 0.00143 },                    optional; fold by default
//     "monitors": [ {                                                                    left to right
//         "id": "laptop",
//         "panel": { "native_px": [2560, 1600], "active_area_mm": [344, 215] },
//         "pose":  { "centre_mm": [-182, 0, 700], "yaw_deg": 0, "pitch_deg": 0, "roll_deg": 0 },
//         "timing": { "refresh_hz": 240, "scan_ms": 3.7, "lag_ms": 0 },                  informational (the run measures)
//         "color": { "acm": true, "gain": 1.0, "white": [1, 1, 1], "black_lift": 0.0,    optional, neutral by default
//                    "primaries": { "r": [x, y], "g": [x, y], "b": [x, y], "w": [x, y] }, "transfer": "srgb", "power": 2.2 }
//       }, ... ],
//     "derived": { "seams": [ { "gap_mm": 20 }, ... ] } }                                one gap per neighbouring pair
//
// Surface. The picture is placed on a surface measured in millimetres, u to the right and v DOWN (cue sheet v0):
//   fold   the picture is printed on the panels, the gaps between them included as physical gaps: a monitor's pixel
//          (px, py) sits at u = left + px / width * w_mm, v = top + py / height * h_mm. No seat is needed.
//   curve  a ray from the eye through the pixel meets a cylinder of radius 1 / curvature that touches the gaze axis at the
//          seat distance (curvature 0 = the flat plane at that distance); u = u_axis + arc length, v = v_eye - height.
// The curvature defaults to 1 / seat distance (the eye on the cylinder's axis). u_axis is where the gaze axis crosses the surface (the middle of the first gap), v_eye the surface v of eye level.
#pragma once

#include <stdbool.h>
#include <stddef.h>

#define ROOM_MAX_MON 4

struct room_vec3 {
    double x, y, z;
};

struct room_color {
    bool acm;                       // Windows' automatic colour management owns the gamut (output is plain sRGB)
    double gain[3];                 // relative white level (1 = none), per channel
    double white[3];                // white balance, per channel (1 = none); multiplied with gain
    double lift[3];                 // black lift as a fraction of white, linear light (0 = none)
    bool has_primaries;             // the panel's own primaries, used only when the player owns the gamut (acm false)
    double primaries[4][2];         // r, g, b, white as xy chromaticities
    bool power_trc;                 // encode with a pure power law instead of piecewise sRGB
    double power;
};

struct room_monitor {
    char id[32];
    int w, h;                       // native pixels
    double w_mm, h_mm;              // active area
    struct room_vec3 centre, ex, ey; // seat frame: panel centre, right axis (pixel x), up axis (against the pixel rows)
    double refresh_hz, scan_ms, lag_ms;
    double left_mm;                 // u of the left edge on the fold surface (first monitor 0, gaps included)
    struct room_color color;
};

struct room {
    char name[64];
    double seat_distance;
    int nmon;
    struct room_monitor mon[ROOM_MAX_MON];
    double gap[ROOM_MAX_MON];       // gap[k] between monitor k and k + 1
    double u_axis;                  // u of the gaze axis: the middle of the first gap (the middle of a lone monitor)
    bool curve;                     // surface model
    double curvature;               // curve: 1 / radius in 1 / mm
};

// Reads and validates a room file. False with a message on any problem.
bool room_load(struct room *r, const char *path, char *err, size_t err_size);
bool room_parse(struct room *r, const char *text, size_t len, const char *source, char *err, size_t err_size);

// The surface v (down) of the top edge of monitor k when monitor `master`'s top edge is v = 0.
double room_top_mm(const struct room *r, int k, int master);
// The surface v of eye level when monitor `master`'s top edge is v = 0.
double room_eye_v(const struct room *r, int master);

// One monitor's surface description: all the map needs, CPU and shader.
struct room_geom {
    bool curve;
    int px_w, px_h;                 // pixels of the target (the swapchain; smaller than native in a windowed test)
    double w_mm, h_mm;
    double left_mm, top_mm;         // fold: u of the left edge, v of the top edge
    struct room_vec3 centre, ex, ey; // curve: pose in the seat frame
    double seat_distance, curvature; // curve
    double u_axis, v_eye;           // curve: where the gaze axis and eye level are on the surface
};

void room_geom_fold(struct room_geom *g, int px_w, int px_h, double w_mm, double h_mm, double left_mm, double top_mm);
// master: the monitor whose top edge is v = 0.
void room_geom_curve(struct room_geom *g, const struct room *r, int k, int px_w, int px_h, int master);

// Surface position (mm) of a continuous pixel position of the target (pixel centres at +0.5), in double precision.
void room_map_uv(const struct room_geom *g, double px, double py, double *u, double *v);
