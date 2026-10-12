// The finish test driver: shared context and the entry points of each test group.
#pragma once

#include "test-util.h"

struct tctx {
    struct fp_device dev;
    char out[256];        // output directory for PNGs and CSVs
    char font_path[512];  // a TrueType font for the subtitle tests
    int cell_px;          // surround grid cell size (0 = default)
    const char *only;     // optional: run only the scenario with this name
    int seconds;          // benchmark length per case
};

int test_smoke(struct tctx *t);
int test_surround(struct tctx *t);
int test_color(struct tctx *t);
int test_subs(struct tctx *t);
int test_bench(struct tctx *t);
