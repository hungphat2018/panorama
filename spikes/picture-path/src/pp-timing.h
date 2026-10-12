// GPU pass timings and the pass list of one rendered frame. libplacebo's own passes (pl_render_image and
// pl_render_image_mix) are gathered from pl_render_params.info_callback; they report the latest *finished* sample of the
// pass, so a loop that finishes the GPU every iteration reads iteration i-1's time in iteration i. Our own passes
// (pp-s2.c) carry a pl_timer each and are appended after the GPU has finished, so their times are those of the
// iteration itself and stay apart per monitor even when two monitors run the same shader.
#pragma once

#include <stdbool.h>

#include <libplacebo/dispatch.h>
#include <libplacebo/renderer.h>

#define PP_MAX_PASSES 1024

enum pp_pass_kind {
    PP_PASS_OTHER = 0,       // colour conversion, merge, blend, copy, dither
    PP_PASS_RESAMPLE,        // filtered sampling of picture content at non-integer positions (counted)
    PP_PASS_CHROMA,          // a separate chroma-plane upsampling pass (reported on its own)
};

struct pp_pass_rec {
    char desc[112];
    double ms;               // finished GPU time of this pass
    enum pp_pass_kind kind;
    int mon;                 // the monitor whose refresh it belongs to
};

struct pp_timing {
    struct pp_pass_rec pass[PP_MAX_PASSES];
    int npass;
    int cur_mon;             // set by the runner before it works on a monitor; callbacks tag their records with it
};

void pp_timing_reset(struct pp_timing *t);
// Appends a record (ns = GPU time in nanoseconds).
void pp_timing_add(struct pp_timing *t, const char *desc, unsigned long long ns, int mon);
// Sum of the GPU time / count of the passes of one kind that belong to monitor `mon`.
double pp_timing_mon_ms(const struct pp_timing *t, int mon);
int pp_timing_mon_count(const struct pp_timing *t, int mon, enum pp_pass_kind kind);
// Callbacks to hand to libplacebo (priv = struct pp_timing *).
void pp_timing_render_cb(void *priv, const struct pl_render_info *info);
void pp_timing_dispatch_cb(void *priv, const struct pl_dispatch_info *info);

double pp_timing_total_ms(const struct pp_timing *t);
int pp_timing_count(const struct pp_timing *t, enum pp_pass_kind kind);

// Classifies a libplacebo shader description (the pretty-printed step list) or one of ours.
enum pp_pass_kind pp_classify_pass(const char *desc);

// Sample series with percentiles, for the cost tables.
struct pp_series {
    double *v;
    int n, cap;
};
void pp_series_add(struct pp_series *s, double x);
void pp_series_free(struct pp_series *s);
// p50, p99, min, max, mean of the series (all 0 when empty). Sorts a copy.
void pp_series_stats(const struct pp_series *s, double *p50, double *p99, double *min, double *max, double *mean);
