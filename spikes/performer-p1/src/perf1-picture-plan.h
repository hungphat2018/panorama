// What the picture path works out on the CPU for one refresh of one monitor, from times it is handed: the film time of a
// moment, the camera-slide position of each row, and the share of the next (or previous) film frame in each row for the
// rolling blend. Pure arithmetic, no GPU, no engine: the render code feeds it row times from row-timing.h.
//
// Row light time (ADR 0025). A monitor's refresh k lights row y from a(y) = top_row(k) + y / rows * scan_s (+ the panel's
// lag, already inside a_top) and keeps it lit for one refresh period P. Smooth motion draws each row for the middle of
// that time, a(y) + P / 2 (the seam tuner's rule).
#pragma once

#include <stdbool.h>

#include "perf1-cue-sheet.h"
#include "perf1-picture-map.h"

// Film time of a host moment. Film time at the master's refresh j is origin + (j - J0 - shift) * step_s, where the
// refresh "is" the moment the master lights the aim row; l0 is that moment for refresh J0, so a moment t falls at the
// fractional refresh (t - l0) / master_period.
struct pic_clock {
    double l0;                // host time the master lights the aim row of refresh J0
    double master_period;     // seconds per master refresh
    double origin;            // film time of refresh J0 (before the shift)
    double step_s;            // film seconds per master refresh
    int shift;                // the re-aim shift in force (master refreshes)
};

double pic_film_time(const struct pic_clock *c, double host_t);

// The slide of `shot` for a refresh whose top row lights at a_top (host time, lag included) and whose rows take scan_s to
// light, on a monitor with `rows` pixel rows and refresh period `period`. The offset at the top and bottom edge is
// worked out at the middle of each row's light, and the rows in between follow the straight line (exact unless the path
// bends inside one scan, where the error stays below the change of slope times a quarter of the scan). False, with a
// zero slide, when the shot has none.
bool pic_row_slide(const struct cue_shot *shot, const struct pic_clock *c, double a_top, double scan_s, double period,
                   int rows, struct pic_slide *out);

// Rolling blend. The share of the NEW frame in row y of a refresh is w(y) = clamp((a(y) + P - e(y)) / P, 0, 1): a(y) is
// when this monitor starts to light the row, e(y) when the master lights the same physical height with the new frame
// (both straight lines in y), P this monitor's refresh period. Row y is then lit with the new frame for the share of
// the refresh that comes after e(y), so the light-weighted switching moment is exactly e(y) whatever a, P and e are.
// e0 and e1 are e at the top edge (y = 0) and at the bottom edge (y = rows) of this monitor's output. The result is
// w(y) = wa + wb * y, to be clamped by the shader.
void pic_blend_line(double a_top, double scan_s, double period, int rows, double e0, double e1, double *wa, double *wb);

struct pic_blend {
    int partner;              // -1: blend frame c-1 (a) into c (b); +1: blend c (a) into c+1 (b); 0: no blend
    double wa, wb;            // share of frame b in row y: clamp(wa + wb * y, 0, 1)
};

// Chooses which neighbour of the frame c the engine picked takes part, from the new-frame lines of the pair (c-1, c)
// and the pair (c, c+1) (each as from pic_blend_line, ok_* = the master's start of that frame is known). At most one
// pair can be mixed in a refresh when frames last longer than a refresh.
struct pic_blend pic_blend_choose(int rows, bool ok_prev, double wa_prev, double wb_prev, bool ok_next, double wa_next,
                                  double wb_next);
