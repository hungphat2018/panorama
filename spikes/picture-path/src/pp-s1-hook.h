// Structure S1-hook: libplacebo draws each monitor and our resample runs INSIDE its pipeline as a native hook at the RGB stage
// ("a custom hook sized to the monitor"): libplacebo decodes the source frame to RGB, the hook turns it into the monitor's
// canvas with the per-pixel map (so the main scaler then sees 1:1 and does nothing), libplacebo colour-manages and writes the
// target. Fold and curve both work, one resample. The price: the source conversion and our linearize pass run again for
// every monitor on every refresh; nothing is prepared once and shared.
#pragma once

#include <stdbool.h>

#include <libplacebo/renderer.h>
#include <libplacebo/shaders/custom.h>

#include "pp-gpu.h"
#include "pp-s2.h"
#include "pp-scenario.h"
#include "pp-source.h"
#include "pp-timing.h"

struct pp_hook_ctx;

struct pp_s1_hook {
    const struct pp_gpu *g;
    struct pp_s2 *s2;               // provides the shaders and the kernel table
    struct pp_timing *tm;
    int nmon;
    pl_renderer rr[PP_MAX_MON];
    struct pl_render_params params;
    struct pp_hook_ctx *ctx[PP_MAX_MON];
};

bool pp_s1_hook_init(struct pp_s1_hook *h, const struct pp_gpu *g, struct pp_s2 *s2, struct pp_timing *tm, int nmon);
void pp_s1_hook_destroy(struct pp_s1_hook *h);

// Draws monitor k into canvas (RGBA16F). sigmoid: the monitor upscales (libplacebo's rule, decided by the caller).
bool pp_s1_hook_render(struct pp_s1_hook *h, const struct pp_scenario *scn, int k, const struct pp_picture *pic,
                       const struct pp_source *src, pl_tex canvas, bool sigmoid, double extra_slide_ms);
