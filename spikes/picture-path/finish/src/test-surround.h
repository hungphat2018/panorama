// The surround tests' shared types: a film sequence, a rule configuration, and the run that writes one CSV.
#pragma once

#include "test-main.h"

struct tl_shot {
    float len_s;
    float left[3], right[3]; // linear colours of the picture at its left and right edge
};

struct tl_scenario {
    const char *name;
    const struct tl_shot *shots;
    int n_shots;
    float grain;        // relative film grain (0.02 = 2 %)
    int flash_frame;    // film frame where a white flash starts (needs flash_frames > 0)
    int flash_frames;   // how many film frames the whole picture is white
    double dissolve_s;  // cross-dissolve into the next shot over the last part of each shot (0 = hard cuts)
    int strobe_frames;  // > 0: the picture's brightness alternates between 1x and 3x every this many film frames
};

struct tl_cfg {
    const char *name;
    struct fp_surround_look look;
    struct fp_surround_time time;
    bool flag_flash_as_cut; // the planner marked the flash's first and last frame as cuts
};

// The recorded series of one run (the same numbers as the CSV), for the checks made inside the test program.
struct tl_series {
    int n;
    double *t, *pic, *top_near, *top_far, *bot_near;
};
void tl_series_free(struct tl_series *s);

// Renders the scenario at 240 Hz refreshes through the real passes and writes one CSV (see analyze-flicker.py).
// `out` may be NULL; otherwise the caller frees it with tl_series_free().
bool tl_run(struct tctx *t, const struct tl_scenario *sc, const struct tl_cfg *cfg, const char *csv_path,
            struct tl_series *out);
