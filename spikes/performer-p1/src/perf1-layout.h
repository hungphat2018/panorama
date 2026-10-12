// Where one shot's picture lands on the screens: the cue's crop and placement turned into each monitor's target
// rectangle (whole pixels in its swapchain) and the matching part of the cropped picture. Pure geometry, no
// graphics API, so it is tested without a window.
//
// Surface coordinates (cue sheet v0): u runs along the screens from the left edge of the leftmost one, the
// monitors sitting gap_mm apart; v runs down from the top of the master screen (the shared vertical axis of
// row-timing.h). Each monitor is a rectangle on that surface; the picture is another one; a monitor shows the
// part of the picture inside its rectangle, snapped to whole pixels, so one resample puts source pixels on the
// panel (image.crop / target.crop in libplacebo).
#pragma once

#include "perf1-cue-sheet.h"

#define LAY_MAX_MON 4

struct lay_monitor {
    double left_mm;          // u of its left edge
    double top_mm;           // v of its top edge
    double w_mm, h_mm;       // active area
    int sc_w, sc_h;          // swapchain size in pixels
};

// Where a shot without "placement" goes: horizontally centred on center_u_mm, its top at top_v_mm, height_mm tall.
struct lay_defaults {
    double center_u_mm, top_v_mm, height_mm;
};

struct lay_share {           // one monitor's part of the picture
    bool has_picture;
    int tx0, ty0, tx1, ty1;  // target rectangle, swapchain pixels
    double u0, v0, u1, v1;   // the part of the cropped picture that lands there, as fractions 0..1
};

struct lay_picture {
    double crop[4];          // source pixels used: x0, y0, x1, y1 (relative to the visible frame)
    double rect_mm[4];       // the picture on the surface: u0, v0, u1, v1
    double aspect;           // displayed width / height of the cropped picture
    struct lay_share share[LAY_MAX_MON];
};

// The defaults for these monitors: as tall as the height they all share (or picture_height_mm when > 0), centred on
// the gap between the two leftmost monitors (or on picture_center_mm unless it is NaN; the middle of the monitors
// when there is only one).
void layout_defaults(const struct lay_monitor *mon, int n, double picture_height_mm, double picture_center_mm,
                     struct lay_defaults *out);

// The picture of `shot` for a source of src_w x src_h visible pixels with pixel aspect ratio sar (<= 0 means 1).
void layout_shot(const struct cue_shot *shot, double src_w, double src_h, double sar, const struct lay_monitor *mon,
                 int n, const struct lay_defaults *def, struct lay_picture *out);
