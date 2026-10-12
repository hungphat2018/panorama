// Structure S2 ("frames prepared once, our own resample"): libplacebo converts each decoded frame once, 1:1 at source
// resolution, into linear light BT.709 RGBA16F (the prepared frame); our compute shader then does the one resample per
// monitor per refresh, straight from the prepared frame to the monitor's canvas, with the per-pixel map (fold, curve,
// slide shear) and an optional per-row blend of two prepared frames. The same helpers serve the S1 variants that need a
// custom pass (the blend of two libplacebo renders).
#pragma once

#include <stdbool.h>

#include <libplacebo/dispatch.h>
#include <libplacebo/renderer.h>

#include "pp-gpu.h"
#include "pp-scenario.h"
#include "pp-source.h"
#include "pp-timing.h"

// Weight of the named kernel at kernel distance t (w may be NULL) and its support radius; false for an unknown name.
// Names: ewa_lanczossharp, ewa_lanczos, ewa_lanczos4sharpest, ewa_robidoux, ewa_catmull_rom, ewa_mitchell.
bool pp_kernel_weight(const char *name, double t, double *w, double *support);

struct pp_prepared {
    int w, h;
    pl_tex lin;                     // linear light, BT.709 primaries, 1.0 = SDR white
    pl_tex sig;                     // the same in libplacebo's sigmoid domain (for monitors that upscale)
    bool has_sig;
};

// Timer slots of our own passes: one per monitor for the resample and the blend, one per prepared frame for the sigmoid copy.
#define PP_S2_SLOT_RESAMPLE 0
#define PP_S2_SLOT_BLEND 4
#define PP_S2_SLOT_SIGMOID 8
#define PP_S2_SLOTS 10

struct pp_s2 {
    const struct pp_gpu *g;
    struct pp_timing *tm;
    // Our passes carry their own timers: libplacebo keeps one timing history per distinct shader, so two monitors running
    // the same resample shader would otherwise read each other's time.
    pl_timer timer[PP_S2_SLOTS];
    bool timer_pending[PP_S2_SLOTS];
    int timer_mon[PP_S2_SLOTS];
    char timer_desc[PP_S2_SLOTS][48];
    pl_dispatch dp;
    pl_renderer rr;                 // converts source frames to prepared frames
    struct pl_render_params prep_params;
    char *glsl_common, *glsl_resample;
    pl_tex lut;                     // kernel weights over [0, support], 1 x N, R32F
    float support;
    float first_zero;               // end of the kernel's main lobe, in kernel units
    char kernel_name[48];
};

// libplacebo's built-in anti-ringing strength of a filter preset (0.8 for ewa_lanczos4sharpest, otherwise 0).
double pp_kernel_antiring(const char *name);

// shader_dir holds pp-common.glsl and pp-resample.glsl. chroma_filter: libplacebo filter name for the chroma planes
// (NULL = the cheap built-in bilinear). Returns false (with a message) on any failure.
bool pp_s2_init(struct pp_s2 *s2, const struct pp_gpu *g, struct pp_timing *tm, const char *shader_dir,
                const char *kernel_name, const char *chroma_filter);
void pp_s2_destroy(struct pp_s2 *s2);

// Starts a frame for libplacebo's shader cache bookkeeping; call once before the passes of every refresh.
void pp_s2_begin_frame(struct pp_s2 *s2);

// One-per-frame preparation. Reuses out's textures when the size matches. want_sigmoid also fills out->sig. `which` (0 or 1)
// picks the timer slot of the sigmoid pass for frame A or B.
bool pp_s2_prepare(struct pp_s2 *s2, const struct pp_source *src, struct pp_prepared *out, bool want_sigmoid, int which);
void pp_prepared_destroy(struct pp_s2 *s2, struct pp_prepared *p);

struct pp_s2_job {
    const struct pp_scenario *scn;
    int k;                          // monitor index
    const struct pp_picture *pic;
    const struct pp_prepared *a, *b;// b NULL: no blend
    pl_tex canvas;                  // RGBA16F, storable, monitor size
    bool sigmoid;                   // monitor upscales: sample the sigmoid copy, map back to linear
    double extra_slide_ms;          // added to the slide's top-row time (bench: changes every refresh)
    bool debug_map;                 // write (source x, source y, j00, j11) in float32 instead of the picture
};
bool pp_s2_resample(struct pp_s2 *s2, const struct pp_s2_job *job);

// out = mix(a, b, clamp(wa + wb * row, 0, 1)) over whole canvases (RGBA16F, same size): the app-side rolling blend of S1.
bool pp_s2_blend_canvases(struct pp_s2 *s2, pl_tex a, pl_tex b, pl_tex out, double wa, double wb, int mon);

// gamma-coded (sRGB) RGB -> linear light, or the sigmoid domain, same size: the hook variant of S1 needs it every refresh.
bool pp_s2_linearize(struct pp_s2 *s2, pl_tex src, pl_tex dst, bool sigmoid, int mon);

// After the GPU has finished: appends the timer samples of our passes to s2->tm (tagged with their monitor).
void pp_s2_collect_timers(struct pp_s2 *s2);
