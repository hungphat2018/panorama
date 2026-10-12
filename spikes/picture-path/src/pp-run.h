// Runs one scenario under one pass structure: owns the GPU resources of every monitor (canvas, prepared frames) and does
// one refresh's worth of work for all monitors on demand. The functional run writes the canvases; the bench loops it.
#pragma once

#include <stdbool.h>

#include "pp-gpu.h"
#include "pp-s1.h"
#include "pp-s1-hook.h"
#include "pp-s2.h"
#include "pp-scenario.h"
#include "pp-source.h"
#include "pp-timing.h"

enum pp_structure {
    PP_ST_S1 = 0,                   // libplacebo draws each monitor, one pl_render_image_mix with crops (fold)
    PP_ST_S1_STRIPS,                // S1 with n strips per monitor, each with the slide of its own rows (fold)
    PP_ST_S1_BLEND,                 // S1 twice (frames A and B) and our blend pass over the two canvases (fold)
    PP_ST_S1_MARGIN,                // S1 into a canvas with a margin around the monitor, then a copy of the middle (fold)
    PP_ST_S1_HOOK,                  // S1 with our resample as a native hook inside libplacebo's pipeline (fold and curve)
    PP_ST_S1_TWOPASS,               // libplacebo scales the picture to the monitor's pixel pitch, then a second filtered pass warps it (curve): two resamples
    PP_ST_S2,                       // prepared frame once, our resample per monitor (fold and curve)
};

enum pp_phase {
    PP_PHASE_REFRESH = 0,           // the per-refresh work (S2: the resample pass from a prepared frame)
    PP_PHASE_PREPARE,               // S2 only: the once-per-frame preparation
};

struct pp_run_cfg {
    enum pp_structure st;
    int strips;
    enum pp_src_fmt fmt;
    char kernel[48];                // empty: the scenario's
    char kernel2[48];               // S1-twopass: the second (warp) pass's kernel, default ewa_catmull_rom
    char chroma[48];                // empty: the cheap built-in bilinear
    char shader_dir[256];
    bool uniform_slide;             // S1 without strips: one slide offset per refresh
    bool debug_map;                 // S2: dump the pixel -> source map (float32) instead of the picture
};

struct pp_run {
    const struct pp_gpu *g;
    struct pp_scenario scn;
    struct pp_run_cfg cfg;
    struct pp_picture pic;
    struct pp_source src_a, src_b;
    bool has_src_b;
    struct pp_prepared prep_a, prep_b;
    pl_tex canvas[PP_MAX_MON];
    pl_tex tmp_a[PP_MAX_MON], tmp_b[PP_MAX_MON];
    bool sigmoid[PP_MAX_MON];       // monitor upscales: libplacebo's sigmoid rule
    bool any_sigmoid;
    struct pp_timing tm;
    struct pp_s1 s1;
    struct pp_s1_hook s1h;
    struct pp_s2 s2;
    struct pp_s2 s2b;               // S1-twopass: the warp pass with its own kernel
    pl_tex inter[PP_MAX_MON];       // S1-twopass: the picture at the monitor's pixel pitch, between the two passes
    struct pp_picture pic2[PP_MAX_MON];
    int refresh_count;
    bool flush_cache;               // S1: empty libplacebo's frame cache before each monitor (a refresh that must run every pass)
};

const char *pp_structure_name(enum pp_structure s);
bool pp_structure_from_name(const char *name, enum pp_structure *out);

bool pp_run_init(struct pp_run *r, const struct pp_gpu *g, const char *scenario_path, const struct pp_run_cfg *cfg);
void pp_run_destroy(struct pp_run *r);

// Prepares the source frame(s) (S2 only; S1 has no such step). Call once before the first refresh.
bool pp_run_prepare(struct pp_run *r);
// One refresh for every monitor. vary: change the per-refresh inputs a little (a moved crop, a later row time) so that
// nothing can be reused from the previous refresh, as in pan mode.
bool pp_run_refresh(struct pp_run *r, bool vary);
// After pl_gpu_finish: adds the timer samples of our own passes to r->tm (their GPU times are those of this refresh).
void pp_run_collect(struct pp_run *r);
