// State shared by the picture path's own files (perf1-picture-render.c, -dump.c); nothing outside them includes this.
#pragma once

#include "perf1-picture.h"

// Columns of the per-pass cost log, one sample per drawn refresh (GPU milliseconds as libplacebo's timers report them,
// a few refreshes late).
enum pic_cost { COST_PREPARE, COST_RESAMPLE, COST_SURROUND, COST_FINISH, COST_SUBS, COST_COUNT };

struct pic_mon {
    pl_dispatch dp;                 // this monitor's own: see perf1-picture-pass1.h
    pl_tex canvas;                  // pass 1's output: RGBA16F, the swapchain's size
    pl_timer t_resample, t_surround, t_finish;
    struct fp_env env;
    struct fp_surround *surround;
    struct fp_finish *finish;
    struct fp_subs *subs;           // this monitor's libass
    // the surround's inputs from refresh to refresh
    bool primed, last_glow;
    int last_epoch;
    uint32_t last_shot;
    double last_wake;
    bool dumped;
    // log
    struct samples cost[COST_COUNT];
    uint32_t n_drawn, n_slide, n_blend, n_sub_draws, n_sub_overflow, n_upscaling, n_blend_missed;
    double last_gpu_ms;             // GPU time the latest timer results add up to
};

extern struct pic_mon g_pm[MAX_MONITORS];
extern bool g_have_subs;            // a subtitle track was read

// Passes 2 (the fill, state already updated) and 3 into `out`, then the subtitles when this monitor shows them; out_bits
// is the depth the dither works to.
bool pic_finish_into(struct pic_mon *pm, const struct pic_refresh *r, const struct pic_shot *shot, pl_tex out, int out_bits);

// perf1-picture-dump.c: reads pass 1's canvas and an 8-bit rendering of the finished frame back and writes them
// (--dump-canvas), once per monitor.
void picture_dump(struct pic_mon *pm, const struct pic_refresh *r, const struct pic_shot *shot, const struct pic_pass1_job *job,
                  const struct pic_timing *tm);
