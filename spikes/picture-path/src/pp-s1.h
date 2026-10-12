// Structure S1 ("libplacebo draws each monitor"): per monitor one pl_render_image_mix (or pl_render_image) with the fold
// geometry expressed as image.crop and target.crop, as the performer prototype does today. The picture's pixel rectangle
// on the monitor is snapped to whole pixels and the snapping moved into the source rectangle (libplacebo does the same
// internally), so the mapping inside the picture is exact and the edge sits on a pixel boundary.
//
// libplacebo samples only inside the crop rectangle (its intermediate textures hold just the crop), so at a monitor's
// border, where the picture continues behind the bezel, the filter footprint is clamped: about 2 to 3 pixels are wrong.
// The margin variant renders into a canvas that is margin pixels larger on every side and blits the middle out.
#pragma once

#include <stdbool.h>

#include <libplacebo/renderer.h>

#include "pp-gpu.h"
#include "pp-scenario.h"
#include "pp-source.h"
#include "pp-timing.h"

struct pp_s1 {
    const struct pp_gpu *g;
    struct pp_timing *tm;
    int nmon;
    pl_renderer rr[PP_MAX_MON];     // one renderer per monitor, as the performer does: each keeps its own frame cache
    pl_tex wide[PP_MAX_MON];        // margin variant: the oversized canvas
    struct pl_render_params params;
    const struct pl_filter_config *filter;
};

// kernel: a libplacebo filter name used for both upscaling and downscaling; sigmoid: libplacebo's default sigmoidization
// on upscaling (its own rule), chroma_filter: for 4:2:0 sources (NULL = built-in bilinear).
bool pp_s1_init(struct pp_s1 *s1, const struct pp_gpu *g, struct pp_timing *tm, int nmon, const char *kernel, bool sigmoid,
                double antiring, const char *chroma_filter);
void pp_s1_destroy(struct pp_s1 *s1);

struct pp_s1_opts {
    int strips;                     // 0: one render per monitor (pl_render_image_mix, frame cache on); n > 0: n strips, each its own
                                    // pl_render_image with the slide of its middle row
    int margin;                     // > 0: render into a canvas this many pixels larger on each side, then blit the monitor out
    double jitter_px;               // added to the source crop: a changed crop defeats libplacebo's frame cache (pan mode)
    double extra_slide_ms;          // added to the slide's top-row time (strips)
    bool uniform_slide;             // one slide offset for the whole refresh, taken at the monitor's middle row
};

// Draws monitor k of the scenario's fold into `canvas` (RGBA16F, premultiplied linear, picture coverage in alpha).
// Fold only: a curve has no crop rectangle.
bool pp_s1_render_monitor(struct pp_s1 *s1, const struct pp_scenario *scn, int k, const struct pp_picture *pic,
                          const struct pp_source *src, pl_tex canvas, const struct pp_s1_opts *opts);

// The picture's snapped pixel rectangle on monitor k (extended by `margin` pixels on every side of the monitor, in the
// extended canvas' coordinates) and the matching source rectangle (fold). Returns false when the picture does not touch
// the extended monitor. Exposed for the tests and for the strip logic.
bool pp_s1_fold_rects(const struct pp_scenario *scn, int k, const struct pp_picture *pic, int margin, int rect[4],
                      double src_rect[4]);
